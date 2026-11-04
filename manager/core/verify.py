"""Bench runs the check. The run asks for it by name, and the evidence is
the runner's own exit status.

`plans` answers which checkout a run works in. `profiles` answers what it
may do there. This module answers the question those two leave open:
**did the project's own checks actually pass, and against what?**

## Why a name, and not a command line

Before this, "the tests passed" was a sentence in an agent's report. The
board had given the run a *prefix* — `python3 -m unittest` — and a shell,
and then believed what came back. Three things were wrong with that, and
they are the three this module fixes:

- **A prefix is composed into a command line by a model.** Whatever a
  prefix rule matches, the argv that runs is the model's. Here the run
  names a check and nothing else; bench reads the operator's document,
  builds the argument list itself, and runs it with `shell=False`. There
  is no string for anything to be appended to.
- **An exit code read by the thing being judged is not evidence.** The
  outcome on record is the one bench's own `wait()` returned, written to
  disk before the asking run is told anything. An agent exiting zero
  cannot make a check passing, and a check that never ran is `not-run`
  rather than silence.
- **"It failed" is not one fact.** A test that failed, a dependency that
  would not install, a permission that was refused, a run somebody
  cancelled and a timeout are five different things to do next, so they
  are five outcomes with structured reasons — never one "non-zero".

## What is enforced, and what is merely directed

Said plainly, because the honest answer is narrower than the obvious
phrasing. Bench **enforces**: the argument list (it is built here, from
the document, and never parsed from anything a model wrote); the absence
of publication credentials (they are removed from the child's environment
by name, and a process cannot read what is not there); which directory
the check starts in; and that a check never runs in the live application
checkout. Bench **directs**: where a check's caches, temporary files and
build output land, by pointing `HOME`, `TMPDIR` and the cache variables
into a directory made for it. That is not a jail — a check with a path can
still write elsewhere — and `filesystem-sandbox` is the capability that
would make it one. Nothing on either shipped adapter enforces that, so a
profile *requiring* it is refused rather than reported as guaranteed
(`profiles.PENDING_CAPABILITIES`).

## Where the evidence lives

One JSON file per verification under `local/state/evidence/`, written when
the request is accepted and rewritten when it ends, plus the check's whole
output beside it. Machine-local for the manifest's reason — it holds this
machine's paths — so the board says `local to this board` rather than
implying a teammate can see it.

It outlives the run, the worktree and the board: a restarted board sweeps
any evidence still marked `running` into `error` rather than leaving it
looking live, because the one thing that must never happen is evidence
drifting towards "passed" on its own.

## Staleness is a property of the evidence, not of its age

Every record is bound to what it was about: the policy revision, the
check's own definition, the pinned context identities, and the exact
source — the commit plus a digest of everything uncommitted in the
checkout. `freshness()` re-reads that and says what moved. A `passed`
whose binding no longer holds reads as `stale`, which is the whole reason
the binding exists: modified tracked source, an edited check or a changed
profile must not be able to reuse a pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import config
import profiles
import state
import taskfiles

VERSION = 1

# ── the outcome vocabulary ─────────────────────────────────────────────
#
# Closed, and every word means one thing a reader would do differently
# about. `failed` is reserved for a check that really ran and really said
# no: a wrapper that could not start, a timeout and a cancellation are not
# test failures and are never recorded as any.

OUTCOMES = {
    "running": "bench is running it now",
    "preparing": "bench is running this check's preparation steps",
    "passed": "the check ran and exited zero",
    "failed": "the check ran and exited non-zero — the project's own verdict",
    "denied": "it was refused before anything ran",
    "prepare-failed": "a required preparation step failed, so the check did "
                      "not run",
    "not-run": "declared for this run and never asked for",
    "cancelled": "stopped on purpose, by a person or by the run ending",
    "stale": "it passed, and what it was about has changed since",
    "error": "bench could not get an answer — the executable, the timeout or "
             "this board stopping",
}

# Outcomes that are still moving. Everything else is what the record says
# for good, subject only to `freshness()`.
LIVE = ("running", "preparing")

# Why, in one slug, so a consumer branches on data rather than on prose.
REASONS = {
    "exit-zero": "the check exited 0",
    "exit-nonzero": "the check exited non-zero",
    "signal": "the check was killed by a signal",
    "timeout": "the check was still running when its timeout expired",
    "no-executable": "the check's executable could not be run",
    "runner-error": "bench itself failed while running the check",
    "unknown-check": "no check of that name is available to this run",
    "unknown-scope": "no scope of that name is declared on that check",
    "root-unavailable": "the execution root this check needs does not exist",
    "root-foreign": "the directory the root names is a checkout of something "
                    "else",
    "profile-blocked": "the run's own execution profile does not resolve",
    "no-profile": "there is no recorded profile for the run that asked",
    "prepare-exit": "a required preparation step exited non-zero",
    "prepare-error": "a required preparation step could not be run",
    "cancelled-by-request": "a person cancelled it",
    "cancelled-with-run": "the run that asked for it was stopped",
    "board-restart": "this board stopped while it was running",
    "never-asked": "nothing asked for it",
    "source-changed": "the source it ran against has changed since",
    "policy-changed": "the policy or the check's definition has changed since",
}

# Which outcome a check's own exit status means. Separate from everything
# above on purpose: this is the *only* place an exit code becomes a
# verdict, and it only ever sees a process bench itself launched and
# waited on.
def _from_exit(rc: int) -> tuple[str, str, str]:
    if rc == 0:
        return "passed", "exit-zero", "the check exited 0"
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = f"signal {-rc}"
        return ("error", "signal",
                f"the check was killed by {name} — it did not reach a verdict")
    return "failed", "exit-nonzero", f"the check exited {rc}"


# ── the registry of what is in flight ──────────────────────────────────
#
# In memory, like every other registry on this board, and that is exactly
# why `recover()` exists: a board that dies owns nothing afterwards, and
# evidence on disk still marked `running` has to be told so.

RUNNING: dict[str, dict] = {}        # evidence id -> record, with its proc
_KEYS: dict[str, str] = {}           # (task, check, scope) -> evidence id
_SLOTS = threading.BoundedSemaphore(config.VERIFY_PARALLEL)

# ── and the cost of answering about it ─────────────────────────────────
#
# Asking whether a pass is still fresh means reading the checkout it ran
# in: a handful of git commands and a hash per untracked file. That is the
# right price for a question somebody asked, and the wrong price to pay
# per card on every state payload — and the board's own reading rides
# every SSE nudge.
#
# So the answer is memoised, and `_EPOCH` is what makes the memo safe: a
# terminal record never changes, so the only things that can move an
# answer are the source and the policy, which is exactly what the TTL is
# for; and any *new* record bumps the epoch, so a cached board reading can
# never outlive a verification that has since ended.
_EPOCH = 0
_FRESH_TTL = 10.0
_FRESH: dict[str, tuple[float, dict]] = {}
_BOARD: tuple[int, frozenset, dict] | None = None


def _key(task: str, check: str, scope: str | None) -> str:
    return f"{task}\x00{check}\x00{scope or ''}"


# ── identities and paths ───────────────────────────────────────────────

_ID_OK = re.compile(r"^[A-Za-z0-9._:@-]{1,160}$")


def _slug(evidence_id: str) -> str:
    """One evidence id as one filename. The ids carry `:` and `@`, which
    are legal in a POSIX name and awkward everywhere else."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", evidence_id)


def path_of(evidence_id: str) -> Path:
    return config.EVIDENCE_DIR / f"{_slug(evidence_id)}.json"


def log_path_of(evidence_id: str) -> Path:
    return config.EVIDENCE_DIR / "logs" / f"{_slug(evidence_id)}.log"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


# ── what the source actually was ───────────────────────────────────────
#
# A commit is not enough. The normal case for a work run is a checkout
# with uncommitted edits in it — that is what the run has been doing — so
# a binding that named only HEAD would call a pass fresh after the agent
# changed every file under it. Everything uncommitted goes into the
# identity: the tracked diff, and the content of the untracked files.

# Beyond this, there is too much uncommitted work to bind honestly, and
# the identity says so rather than pretending. An evidence record whose
# source could not be bound is never reusable as a pass.
_MAX_BIND_FILES = 200
_MAX_BIND_BYTES = 4 * 1024 * 1024


def _git(root: Path | str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def source_identity(root: Path) -> dict:
    """What the code in `root` was, at the moment it was asked.

    `commit` and `branch` are the portable half — the thing a PR body or a
    teammate can act on. `sourceId` is the whole truth: a digest over the
    commit, the tracked diff against it, and every untracked file's
    content. Two verifications with the same `sourceId` ran against the
    same code; a different one is a different question.
    """
    found: dict = {"commit": None, "branch": None, "dirty": [], "bound": False,
                   "sourceId": None, "why": None}
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        found["why"] = (f"{root} is not a git checkout bench can read, so the "
                        f"source cannot be identified")
        found["sourceId"] = f"src:unbound@{_digest(str(root), str(time.time()))[:12]}"
        return found
    found["commit"] = head.stdout.strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    found["branch"] = branch.stdout.strip() if branch.returncode == 0 else None
    found["dirty"] = config.dirty_files(root)

    parts = [found["commit"]]
    diff = _git(root, "diff", "HEAD")
    if diff.returncode != 0:
        found["why"] = "the tracked diff could not be read"
        found["sourceId"] = f"src:unbound@{_digest(found['commit'], str(time.time()))[:12]}"
        return found
    parts.append(_digest(diff.stdout))
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    names = [n for n in untracked.stdout.split("\0") if n]
    if len(names) > _MAX_BIND_FILES:
        found["why"] = (f"{len(names)} untracked files in {root}, over the "
                        f"{_MAX_BIND_FILES} bench will hash — the source "
                        f"cannot be bound, so this evidence is never reusable")
        found["sourceId"] = f"src:unbound@{_digest(*parts, str(time.time()))[:12]}"
        return found
    total = 0
    for name in sorted(names):
        try:
            data = (Path(root) / name).read_bytes()
        except OSError:
            parts += [name, "unreadable"]
            continue
        total += len(data)
        if total > _MAX_BIND_BYTES:
            found["why"] = (f"more than {_MAX_BIND_BYTES} bytes of untracked "
                            f"work in {root} — the source cannot be bound, so "
                            f"this evidence is never reusable")
            found["sourceId"] = f"src:unbound@{_digest(*parts, str(time.time()))[:12]}"
            return found
        parts += [name, hashlib.sha256(data).hexdigest()]
    found["bound"] = True
    found["sourceId"] = f"src:{_digest(*parts)[:16]}"
    return found


# ── the environment a check is given ───────────────────────────────────


def _isolated_home(root: Path) -> Path:
    """Where a check's caches, virtualenvs and build output are pointed.

    One directory per execution root rather than one per verification, so
    an expensive `pip install` is not repeated on every check — and never
    the person's own `HOME`, and never another card's. A card has one run
    at a time, so one card's home is one run's.
    """
    return config.VERIFY_DIR / "home" / _digest(str(Path(root).resolve()))[:16]


def child_env(root: Path, home: Path, tmp: Path, evidence_id: str,
              check: str) -> tuple[dict, list[str]]:
    """The environment the check runs in, and the names taken out of it.

    Built rather than inherited. Three things leave: **publication
    credentials**, by name, because a test suite has no business being
    able to push or publish and the only reliable way to say so is for the
    token not to be there; the **launch environment** (`AGENT_*`), because
    a check has no business reading the prompt or the resolved policy; and
    the board's **own address** (`BOARD_PORT`, `BOARD_AGENT_ID`), so a
    check cannot turn round and ask bench for another verification.

    Three things arrive: `HOME`, `TMPDIR` and the cache variables, pointed
    into directories made for this, and `BENCH_VERIFY*`, so a check that
    wants to know it is being verified can ask.
    """
    removed: list[str] = []
    env: dict[str, str] = {}
    denied = set(config.verify_env_deny())
    for name, value in config.child_env().items():
        if name in denied or name.startswith("AGENT_") \
                or name in ("BOARD_PORT", "BOARD_AGENT_ID", "BOARD_TASK",
                            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            removed.append(name)
            continue
        env[name] = value
    cache = home / ".cache"
    env.update({
        "HOME": str(home),
        "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "NPM_CONFIG_CACHE": str(cache / "npm"),
        "YARN_CACHE_FOLDER": str(cache / "yarn"),
        "CARGO_HOME": str(home / ".cargo"),
        "GOCACHE": str(cache / "go-build"),
        "GOMODCACHE": str(home / "go" / "pkg" / "mod"),
        "BENCH_VERIFY": evidence_id,
        "BENCH_VERIFY_CHECK": check,
        "BENCH_VERIFY_ROOT": str(root),
        "PWD": str(root),
    })
    return env, sorted(set(removed))


# ── running one process ────────────────────────────────────────────────


def _run_process(argv: list[str], root: Path, env: dict, log,
                 timeout: float, record: dict, phase: str) -> dict:
    """One child, run to its end, killed as a group if it overruns.

    The group matters: a test runner that forks is not stopped by
    terminating the one process bench can see, and a cancellation that
    leaves children behind is a cancellation that did not happen.
    """
    started = time.time()
    # Cancelled before this child existed — while a slot was being waited
    # for, or between two steps. There is nothing to kill, so the check is
    # simply never started: a cancellation that arrived a moment early
    # must not become a 600-second run nobody is waiting for.
    with state.LOCK:
        early = record.get("cancelling")
    if early:
        log.write(f"\nbench: cancelled before `{shlex.join(argv)}` was "
                  f"started\n".encode("utf-8"))
        log.flush()
        return {"rc": None, "started": started, "ended": time.time(),
                "outcome": "cancelled", "reason": early,
                "why": REASONS[early]}
    log.write(f"\n$ {shlex.join(argv)}\n".encode("utf-8"))
    log.flush()
    try:
        proc = subprocess.Popen(
            argv, cwd=str(root), env=env, stdout=log,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
    except (OSError, ValueError) as exc:
        log.write(f"bench: could not start it — {exc}\n".encode("utf-8"))
        log.flush()
        return {"rc": None, "started": started, "ended": time.time(),
                "outcome": "error", "reason": "no-executable",
                "why": (f"{argv[0]} could not be run ({exc}) — nothing was "
                        f"verified, and this is not a test failure")}
    with state.LOCK:
        record["proc"] = proc
        record["phase"] = phase
        # The other half of the early check above: a cancellation that
        # landed between it and the launch read a `proc` of None and so
        # killed nothing. Asked again now the process is on the record,
        # which closes the window in the only direction that matters.
        late = record.get("cancelling")
    if late:
        _kill(proc)
    try:
        rc = proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill(proc)
        rc = proc.wait()
        timed_out = True
    with state.LOCK:
        record["proc"] = None
        cancelled = record.get("cancelling")
    ended = time.time()
    if cancelled:
        return {"rc": rc, "started": started, "ended": ended,
                "outcome": "cancelled", "reason": cancelled,
                "why": REASONS[cancelled]}
    if timed_out:
        log.write(f"bench: killed after {timeout:.0f}s\n".encode("utf-8"))
        log.flush()
        return {"rc": rc, "started": started, "ended": ended,
                "outcome": "error", "reason": "timeout",
                "why": (f"the check was still running after {timeout:.0f}s and "
                        f"was killed — a timeout is not a test failure")}
    outcome, reason, why = _from_exit(rc)
    return {"rc": rc, "started": started, "ended": ended,
            "outcome": outcome, "reason": reason, "why": why}


def _kill(proc: subprocess.Popen) -> None:
    """Terminate the whole process group, then insist."""
    for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


# ── the evidence record ────────────────────────────────────────────────


def _store(record: dict) -> dict:
    """Write the record, every time it changes. Twice at least — once when
    the request is accepted and once when it ends — because an evidence
    file that only appeared at the end would be missing in exactly the
    case a reader most needs it: while it is running, and after a crash."""
    global _EPOCH
    with state.LOCK:
        _EPOCH += 1
    config.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in record.items() if k not in ("proc", "cancelling")}
    path_of(record["id"]).write_text(
        json.dumps(stored, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    return stored


def read(evidence_id: str) -> dict | None:
    """One stored record. Reading is reading: nothing is re-run, nothing is
    re-resolved, and opening a detail panel costs one file read."""
    if not _ID_OK.match(evidence_id or ""):
        return None
    try:
        data = json.loads(path_of(evidence_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def all_evidence() -> list[dict]:
    """Every record this board holds, newest first."""
    found = []
    if not config.EVIDENCE_DIR.is_dir():
        return found
    for path in config.EVIDENCE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            found.append(data)
    found.sort(key=lambda r: r.get("requested") or 0, reverse=True)
    return found


def for_task(filename: str) -> list[dict]:
    return [r for r in all_evidence() if r.get("task") == filename]


def for_agent(agent_id: str) -> list[dict]:
    return [r for r in all_evidence() if r.get("agent") == agent_id]


# ── secrets never reach a log a reader opens ───────────────────────────
#
# A check's own output is the project's, and a project's test suite prints
# what it likes. The record names paths, revisions and hashes and never
# content; the log is the one place bytes bench did not write get through,
# so it is scrubbed on the way out rather than trusted on the way in.

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{10,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{12,})"),
    re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/-]{16,}=*)"),
    re.compile(r"(?i)\b([a-z_]*(?:secret|password|passwd|token|api[_-]?key|"
               r"credential)[a-z_]*)\s*[=:]\s*(\S{6,})"),
)


def scrub(text: str) -> str:
    """The log as a reader may see it, with anything shaped like a
    credential replaced by a marker. Not a proof — no pattern set is —
    but the difference between a token printed by an unlucky test and a
    token in a panel somebody screenshots."""
    out = text
    for pattern in _SECRET_PATTERNS:
        def _mask(match: re.Match) -> str:
            groups = match.groups()
            if len(groups) == 1:
                return "«redacted»"
            return f"{groups[0]}«redacted»"
        out = pattern.sub(_mask, out)
    return out


def log_text(evidence_id: str, cap: int = 200_000) -> dict:
    """One verification's output, scrubbed and clipped to its tail — which
    is where a failing check says what failed."""
    record = read(evidence_id)
    if record is None:
        raise ValueError(f"no evidence {evidence_id}")
    path = Path(record.get("log") or log_path_of(evidence_id))
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"id": evidence_id, "text": "", "bytes": 0,
                "why": f"the log could not be read: {exc}"}
    clipped = len(raw.encode("utf-8", "replace")) > cap
    return {"id": evidence_id, "text": scrub(raw[-cap:]),
            "bytes": len(raw), "clipped": clipped,
            "scrubbed": True, "why": None}


# ── what a run may ask for ─────────────────────────────────────────────


def available(manifest: dict | None) -> list[dict]:
    """The checks one run may ask for, read off its own profile. A legacy
    board has none, which is not a failure: it resolves exactly as it did
    before this module existed, and its agents run the project's commands
    themselves through the grants they have always had."""
    return list((manifest or {}).get("verify") or [])


def _refuse(request: dict, reason: str, why: str) -> dict:
    """A refusal is evidence too.

    It has to be: "the check was never allowed to run" and "the check
    passed" must not look alike in a panel, and the only way to be sure of
    that is for the refusal to be a record with an outcome of its own. It
    is written before anything is started, so there is nothing to clean up
    and nothing that half-ran.
    """
    record = dict(request)
    record.update({
        "outcome": "denied", "reason": reason, "why": why,
        "rc": None, "started": None, "ended": time.time(),
        "duration": 0.0, "prepare": record.get("prepare") or [],
    })
    stored = _store(record)
    state.record_board_event({
        "kind": "verify", "actor": "board", "file": record.get("task") or "",
        "summary": (f"verification of {record.get('check')} on "
                    f"{record.get('task')} refused — {why}")})
    state.broadcast({"type": "verify"})
    return stored


# ── a request, from its first refusal to its evidence ──────────────────


def request(*, check: str, task: str | None = None, agent: str | None = None,
            scope: str | None = None, intent: str | None = None,
            stage: str | None = None, actor: str = "agent") -> dict:
    """Run one named check, and record what happened.

    Synchronous: it returns when the check has ended, because the thing
    asking is usually a run that has to know. The refusals all land before
    a process exists, and each one is a stored record with `outcome:
    "denied"` rather than an exception the caller could paraphrase.

    An `agent` names the run asking, and then **the frozen manifest is the
    policy** — what that run was granted, not what the document says now.
    A request with no agent is a person's, and resolves the profile fresh.
    Neither can name a check the profile does not hold, and neither can
    name another card: the execution root is derived from the task, and
    the task from the run.
    """
    frozen = profiles.recorded(agent) if agent else None
    if agent and frozen is None:
        return _refuse(
            _request_shell(check, task or "", scope, intent or "work", agent,
                           actor, stage or "", None),
            "no-profile",
            f"there is no recorded execution profile for the run '{agent}', so "
            f"there is nothing that says which checks it may ask for")
    if frozen is not None:
        task = frozen.get("task") or task
        intent = frozen.get("intent") or intent or "work"
        stage = frozen.get("stage") or stage
    task = task or ""
    intent = intent or "work"
    if Path(task).name != task or not task.endswith(".md"):
        return _refuse(_request_shell(check, task, scope, intent, agent, actor,
                                      stage or "", frozen),
                       "root-unavailable",
                       f"{task!r} is not a task file name")
    stage = stage or taskfiles.find_stage_of(task) or ""

    manifest = frozen
    if manifest is None:
        try:
            manifest = profiles.manifest(intent, task, stage)
        except (ValueError, OSError) as exc:
            return _refuse(_request_shell(check, task, scope, intent, agent,
                                          actor, stage, None),
                           "profile-blocked", str(exc))
        if manifest["blockers"]:
            return _refuse(_request_shell(check, task, scope, intent, agent,
                                          actor, stage, manifest),
                           "profile-blocked", manifest["blockers"][0])

    shell = _request_shell(check, task, scope, intent, agent, actor, stage,
                           manifest)
    found = next((c for c in available(manifest) if c["name"] == check), None)
    if found is None:
        offered = ", ".join(c["name"] for c in available(manifest)) or "none"
        return _refuse(shell, "unknown-check",
                       f"'{intent}' may not ask for the check {check!r} — this "
                       f"run's profile offers: {offered}. A check is granted by "
                       f"name in the profile document, and bench runs nothing "
                       f"it was not told to run")
    scope_args: list[str] = []
    if scope:
        if scope not in found["scopes"]:
            offered = ", ".join(sorted(found["scopes"])) or "none"
            return _refuse(shell, "unknown-scope",
                           f"the check '{check}' declares no scope {scope!r} — "
                           f"it offers: {offered}. A scope is selected by name; "
                           f"arguments are the operator's and never the run's")
        scope_args = list(found["scopes"][scope])
    shell.update({
        "checkId": found["checkId"], "checkRevision": found["revision"],
        "argv": [found["executable"], *found["argv"], *scope_args],
        "command": shlex.join([found["executable"], *found["argv"], *scope_args]),
        "scopeArgs": scope_args,
        "rootPolicy": found["root"],
        "timeout": found["timeout"],
        "required": found["required"],
        "help": found["help"],
        # the intent's own preparation, which runs before every check it
        # declares — as against the check's, which runs only before it
        "profilePrepare": list(manifest.get("prepare") or []),
    })

    # — the root, and nothing else's —
    try:
        root, prepared, root_note = _execution_root(found, task, manifest)
    except ValueError as exc:
        reason = "root-foreign" if "checkout of another" in str(exc) \
            else "root-unavailable"
        return _refuse(shell, reason, str(exc))
    shell["root"] = {"policy": found["root"], "path": str(root),
                     "owned": prepared, "note": root_note}

    # — one at a time per check, and a duplicate joins rather than doubles —
    key = _key(task, check, scope)
    with state.LOCK:
        running = _KEYS.get(key)
        if running and running in RUNNING:
            existing = {k: v for k, v in RUNNING[running].items()
                        if k not in ("proc", "cancelling")}
            existing["duplicate"] = True
            return existing
        _KEYS[key] = shell["id"]
        RUNNING[shell["id"]] = shell
    shell["outcome"] = "running"
    shell["reason"] = None
    shell["why"] = "bench is running it"
    _store(shell)
    state.record_board_event({
        "kind": "verify", "actor": "board", "file": task,
        "summary": f"verifying {check} on {task} — {shell['command']}"})
    state.broadcast({"type": "verify"})

    try:
        return _execute(shell, found, root, prepared)
    except Exception as exc:                 # noqa: BLE001 — see below
        # Anything unexpected still has to close the record. An exception
        # out of here would otherwise leave `running` on disk until the
        # next board start swept it, and "a verification that looks live
        # for ever" is the one failure this module exists to prevent. The
        # caller still gets the exception; what it does not get is a lie
        # on disk.
        _finish(shell, {"rc": None, "started": shell.get("started"),
                        "ended": time.time(), "outcome": "error",
                        "reason": "runner-error",
                        "why": (f"bench could not run the check: {exc} — "
                                f"nothing was verified, and this is not a "
                                f"test failure")}, prepared, root)
        raise
    finally:
        with state.LOCK:
            RUNNING.pop(shell["id"], None)
            if _KEYS.get(key) == shell["id"]:
                _KEYS.pop(key, None)


def _request_shell(check: str, task: str, scope: str | None, intent: str,
                   agent: str | None, actor: str, stage: str,
                   manifest: dict | None) -> dict:
    """Everything a record carries before a process exists — including the
    identities it is bound to, so a refusal is as traceable as a pass."""
    when = time.time()
    request_id = (manifest or {}).get("requestId") \
        or profiles.request_id(intent, task or "unknown.md", when)
    evidence = profiles.evidence_id(
        "verification", request_id, f"{check}:{scope or ''}:{when:.6f}")
    context = [i.get("contextId") for i in (manifest or {}).get("context") or []
               if i.get("contextId")]
    return {
        "v": VERSION,
        "id": evidence,
        "runner": {"module": "verify", "version": VERSION},
        "task": task, "stage": stage, "agent": agent, "actor": actor,
        "intent": intent, "check": check, "scope": scope,
        "requested": when,
        "requestId": request_id,
        "policyId": (manifest or {}).get("policyId"),
        "policyRevision": (manifest or {}).get("revision"),
        "policySource": ((manifest or {}).get("source") or {}).get("kind"),
        "contextIds": context,
        "adapter": ((manifest or {}).get("adapter") or {}).get("name"),
        # evidence is machine-local, like the manifest and the plan: it
        # names this disk's paths, and nothing here is ever committed
        "location": "local",
        "log": str(log_path_of(evidence)),
        "prepare": [],
        "argv": [], "command": None, "scopeArgs": [],
        "root": None, "source": None, "env": None,
        "outcome": "denied", "reason": None, "why": None,
        "rc": None, "started": None, "ended": None, "duration": None,
        "logBytes": 0, "logTruncated": False,
    }


def _execution_root(check: dict, task: str,
                    manifest: dict) -> tuple[Path, bool, str | None]:
    """Where this check runs, and whether bench made it for the occasion.

    Two answers, and the application checkout is not one of them. A
    `worktree` check runs in the card's own checkout — which bench
    created, for this card, and which a request for another card can never
    name because the path is derived from the task. A `source` check runs
    in a detached checkout of one exact commit that bench adds here and
    removes afterwards, which is the only root a read-only run has: a
    review must never run a test suite in the live application.
    """
    stem = task[:-3]
    if check["root"] == "worktree":
        root = config.WORKTREES / stem
        mismatch = config.worktree_mismatch(root)
        if mismatch:
            raise ValueError(mismatch)
        if not root.is_dir():
            raise ValueError(
                f"the check '{check['name']}' runs in this card's worktree and "
                f"{root} does not exist — start work on the card first, or "
                f"declare the check with \"root\": \"source\" so bench checks "
                f"the branch out for it")
        return root, False, None

    # source: a checkout of one commit, made here and owned here.
    branch = config.branch_for(stem)
    commit = _git(config.app_repo(), "rev-parse", "--verify", f"{branch}^{{commit}}")
    if commit.returncode != 0:
        raise ValueError(
            f"the check '{check['name']}' runs against this card's source and "
            f"there is no branch {branch} to check out — nothing has been "
            f"committed for this card yet")
    sha = commit.stdout.strip()
    root = config.VERIFY_DIR / "src" / f"{stem}-{sha[:12]}"
    if root.is_dir():
        mismatch = config.worktree_mismatch(root)
        if mismatch:
            raise ValueError(mismatch)
        return root, True, f"reusing bench's checkout of {branch} at {sha[:12]}"
    root.parent.mkdir(parents=True, exist_ok=True)
    added = _git(config.app_repo(), "worktree", "add", "--detach",
                 str(root), sha, timeout=120)
    if added.returncode != 0:
        raise ValueError(
            f"bench could not check {branch} out for the check "
            f"'{check['name']}': {(added.stderr or added.stdout).strip()[:200]}")
    return root, True, f"bench checked {branch} out at {sha[:12]} for this"


def _execute(record: dict, check: dict, root: Path, owned: bool) -> dict:
    """Preparation, then the check, then the evidence. One slot at a time
    so two verifications do not fight over the machine."""
    home = _isolated_home(root)
    tmp = config.VERIFY_DIR / "tmp" / _slug(record["id"])
    for directory in (home, tmp, log_path_of(record["id"]).parent):
        directory.mkdir(parents=True, exist_ok=True)
    env, removed = child_env(root, home, tmp, record["id"], check["name"])
    record["env"] = {
        "removed": removed, "home": str(home), "tmp": str(tmp),
        "why": ("publication credentials, the launch environment and this "
                "board's own address are removed by name; HOME, TMPDIR and "
                "the cache variables are pointed into a directory made for "
                "this verification. Where writes are directed, not a boundary "
                "they are held inside"),
    }
    record["source"] = source_identity(root)
    _store(record)

    waited = time.time()
    acquired = _SLOTS.acquire(timeout=max(check["timeout"], 60))
    if not acquired:
        return _finish(record, {
            "rc": None, "started": None, "ended": time.time(),
            "outcome": "error", "reason": "timeout",
            "why": (f"this board was already running "
                    f"{config.VERIFY_PARALLEL} verifications and a slot never "
                    f"came free — nothing ran")}, owned, root)
    if time.time() - waited > 1:
        record["queued"] = time.time() - waited
    try:
        with log_path_of(record["id"]).open("wb") as log:
            log.write(_log_header(record, check, root).encode("utf-8"))
            outcome = _prepare(record, check, root, env, log)
            if outcome is None:
                with state.LOCK:
                    record["outcome"] = "running"
                _store(record)
                outcome = _run_process(record["argv"], root, env, log,
                                       check["timeout"], record, "check")
        return _finish(record, outcome, owned, root)
    finally:
        _SLOTS.release()


def _log_header(record: dict, check: dict, root: Path) -> str:
    source = record.get("source") or {}
    return (
        f"bench verification {record['id']}\n"
        f"check     {check['name']}"
        f"{' · scope ' + record['scope'] if record.get('scope') else ''}\n"
        f"command   {record['command']}\n"
        f"root      {root} ({check['root']})\n"
        f"source    {source.get('commit') or 'unknown'}"
        f"{' · ' + str(len(source.get('dirty') or [])) + ' uncommitted' if source.get('dirty') else ''}\n"
        f"policy    {record.get('policyId')}\n"
        f"asked by  {record.get('agent') or record.get('actor')}\n"
        f"environment: {len(record['env']['removed'])} names removed; "
        f"HOME={record['env']['home']}\n")


def _prepare(record: dict, check: dict, root: Path, env: dict,
             log) -> dict | None:
    """Every declared preparation step, in order, before the check.

    Returns None when the check may go ahead. A **required** step that
    fails ends the verification as `prepare-failed` — which is the whole
    reason preparation is separate: a dependency that would not install is
    not a test that failed, and recording it as one sends a reader to the
    wrong file.
    """
    steps = [*(record.get("profilePrepare") or []), *check["prepare"]]
    if not steps:
        return None
    with state.LOCK:
        record["outcome"] = "preparing"
    _store(record)
    for step in steps:
        argv = [step["executable"], *step["argv"]]
        log.write(f"\nbench: preparing — {step['name']}".encode("utf-8"))
        if step["network"]:
            log.write(b" (declared as needing the network; bench enforces no "
                      b"network boundary)")
        log.write(b"\n")
        result = _run_process(argv, root, env, log,
                              config.PREPARE_TIMEOUT, record,
                              f"prepare:{step['name']}")
        entry = {
            "name": step["name"], "command": shlex.join(argv),
            "required": step["required"], "network": step["network"],
            "rc": result["rc"], "started": result["started"],
            "ended": result["ended"],
            "duration": (result["ended"] - result["started"])
                        if result["started"] else None,
            "outcome": "prepared" if result["outcome"] == "passed"
                       else result["outcome"],
            "reason": result["reason"], "why": result["why"],
        }
        record["prepare"].append(entry)
        _store(record)
        if result["outcome"] == "passed":
            continue
        if result["outcome"] == "cancelled":
            return result
        if not step["required"]:
            entry["why"] = (f"{result['why']} — the step is optional, so the "
                            f"check ran anyway")
            continue
        reason = "prepare-exit" if result["outcome"] == "failed" else "prepare-error"
        return {"rc": result["rc"], "started": result["started"],
                "ended": result["ended"], "outcome": "prepare-failed",
                "reason": reason,
                "why": (f"the required preparation step '{step['name']}' "
                        f"{result['why']} — the check did not run, and this is "
                        f"not a test failure")}
    return None


def _finish(record: dict, outcome: dict, owned: bool, root: Path) -> dict:
    """The record, closed. Written before the asking run is told anything,
    which is the order that makes it evidence rather than a claim."""
    log = log_path_of(record["id"])
    try:
        size = log.stat().st_size
    except OSError:
        size = 0
    truncated = False
    if size > config.VERIFY_LOG_CAP:
        truncated = _clip_log(log)
        size = log.stat().st_size if log.exists() else 0
    with state.LOCK:
        record.pop("proc", None)
        record.pop("cancelling", None)
        record.update({
            "outcome": outcome["outcome"], "reason": outcome["reason"],
            "why": outcome["why"], "rc": outcome["rc"],
            "started": outcome["started"], "ended": outcome["ended"],
            "duration": ((outcome["ended"] - outcome["started"])
                         if outcome["started"] else 0.0),
            "logBytes": size, "logTruncated": truncated,
        })
    _cleanup(record, owned, root)
    stored = _store(record)
    state.record_board_event({
        "kind": "verify", "actor": "board", "file": record["task"],
        "summary": line(stored)})
    state.broadcast({"type": "verify"})
    return stored


def _clip_log(path: Path) -> bool:
    """Keep both ends of an over-long log and say what went. The head names
    what ran; the tail is where a failing check says why."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    half = config.VERIFY_LOG_CAP // 2
    if len(raw) <= config.VERIFY_LOG_CAP:
        return False
    dropped = len(raw) - 2 * half
    kept = (raw[:half] +
            f"\n\n… bench removed {dropped} characters from the middle of this "
            f"log, which is over the {config.VERIFY_LOG_CAP}-byte cap "
            f"(BOARD_VERIFY_LOG_CAP) …\n\n" + raw[-half:])
    try:
        path.write_text(kept, encoding="utf-8")
    except OSError:
        return False
    return True


def _cleanup(record: dict, owned: bool, root: Path) -> None:
    """What bench made, bench removes — and nothing it did not.

    The temporary directory goes. A `source` checkout bench added goes
    too, *unless there is uncommitted work in it*: removing a worktree
    with work in it is the one unrecoverable step anywhere near this, so a
    dirty one is kept and the record says where it is. The isolated home
    is deliberately left: it holds the caches the next verification of
    this card would otherwise rebuild.
    """
    tmp = Path((record.get("env") or {}).get("tmp") or "")
    if tmp.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)
    if not owned:
        return
    dirty = config.dirty_files(root)
    if dirty:
        record["root"]["kept"] = (
            f"{len(dirty)} uncommitted change"
            f"{'' if len(dirty) == 1 else 's'} in bench's own checkout at "
            f"{root}, so it was kept rather than removed")
        return
    removed = _git(config.app_repo(), "worktree", "remove", str(root), timeout=60)
    if removed.returncode != 0:
        record["root"]["kept"] = (
            f"git would not remove bench's checkout at {root}: "
            f"{(removed.stderr or removed.stdout).strip().splitlines()[-1][:120]}"
            if (removed.stderr or removed.stdout).strip() else
            f"git would not remove bench's checkout at {root}")
    else:
        record["root"]["removed"] = True


# ── cancelling, and what a stopped board owes ──────────────────────────


def cancel(evidence_id: str, reason: str = "cancelled-by-request") -> dict:
    """Stop one verification. The process group goes; the record says who
    stopped it and why, and never becomes a failure."""
    with state.LOCK:
        record = RUNNING.get(evidence_id)
        if record is None:
            stored = read(evidence_id)
            if stored is None:
                raise ValueError(f"no verification {evidence_id}")
            if stored.get("outcome") in LIVE:
                raise ValueError(
                    f"{evidence_id} is recorded as running but this board does "
                    f"not own it — it was left behind by an earlier board")
            raise ValueError(f"{evidence_id} has already ended "
                             f"({stored.get('outcome')})")
        record["cancelling"] = reason
        proc = record.get("proc")
    if proc is not None:
        _kill(proc)
    return {"id": evidence_id, "cancelling": reason}


def cancel_for_agent(agent_id: str, reason: str = "cancelled-with-run") -> list[str]:
    """Every verification the given run asked for, stopped with it.

    Ownership, not tidiness: a check running on a card whose agent has
    been held is work nobody is waiting for, and leaving it to finish
    would write evidence attributed to a run that had already stopped.
    """
    with state.LOCK:
        mine = [eid for eid, record in RUNNING.items()
                if record.get("agent") == agent_id]
    stopped = []
    for eid in mine:
        try:
            cancel(eid, reason)
            stopped.append(eid)
        except ValueError:
            continue
    return stopped


def recover() -> list[dict]:
    """What a restarted board owes the evidence it no longer owns.

    A record marked `running` on disk, with no process in this board's
    registry, is from a board that died. Left alone it would read as live
    for ever, and the whole value of this module is that evidence never
    drifts towards a pass on its own — so it is closed as `error`, naming
    the restart. Nothing is re-run: whether to ask again is a person's
    decision, and an automatic retry at boot is how an outage becomes a
    thundering herd.
    """
    closed = []
    for record in all_evidence():
        if record.get("outcome") not in LIVE:
            continue
        with state.LOCK:
            if record["id"] in RUNNING:
                continue
        record.update({
            "outcome": "error", "reason": "board-restart",
            "why": ("this board stopped while the check was running, so what it "
                    "would have found is unknown — nothing about it passed"),
            "ended": record.get("ended") or time.time(),
            "duration": record.get("duration") or 0.0,
        })
        closed.append(_store(record))
    if closed:
        state.record_board_event({
            "kind": "verify", "actor": "board", "file": "",
            "summary": (f"{len(closed)} verification"
                        f"{'' if len(closed) == 1 else 's'} left running by an "
                        f"earlier board recorded as unknown — nothing was "
                        f"re-run")})
    return closed


# ── is this evidence still about anything? ─────────────────────────────


def freshness(record: dict) -> dict:
    """What has changed since this verification ran.

    The question a pass only answers while its binding holds. Three
    bindings, and each is a different way for a pass to stop meaning
    anything: the **source** (the commit plus everything uncommitted), the
    **policy** (the resolved profile, which carries the check's own
    definition), and the **context** (the inputs the run was pinned).
    Compared, never resolved: this function moves nothing and re-runs
    nothing.
    """
    changed: list[str] = []
    if not record:
        return {"fresh": True, "changed": [], "stale": False}
    source = record.get("source") or {}
    where = record.get("root") or {}
    root = Path(where.get("path") or "")
    if not source.get("bound"):
        changed.append(source.get("why") or
                       "the source this ran against was never bound, so the "
                       "result cannot be reused")
    elif where.get("policy") == "source":
        # A `source` check ran in a *detached checkout of one commit* that
        # bench made and then removed on purpose, so the checkout being
        # gone says nothing — asking after it would make every read-only
        # verification stale the moment it finished. The commit is the
        # binding, and the question is whether the card's branch still
        # points at it.
        task = record.get("task") or ""
        branch = config.branch_for(task[:-3]) if task.endswith(".md") else ""
        now = _git(config.app_repo(), "rev-parse", "--verify",
                   f"{branch}^{{commit}}") if branch else None
        if now is None or now.returncode != 0:
            changed.append(f"the branch this ran against ({branch or 'unknown'}) "
                           f"no longer exists, so what it covered cannot be "
                           f"checked")
        elif now.stdout.strip() != source.get("commit"):
            changed.append(
                f"the branch has moved on — it ran against "
                f"{str(source.get('commit') or '')[:12]} and {branch} is now "
                f"at {now.stdout.strip()[:12]}")
    elif not root.is_dir():
        changed.append(f"the checkout it ran in ({root}) is gone, so what it "
                       f"ran against can no longer be checked")
    else:
        now = source_identity(root)
        if now.get("sourceId") != source.get("sourceId"):
            if now.get("commit") != source.get("commit"):
                changed.append(
                    f"the source has moved on — it ran against "
                    f"{str(source.get('commit') or '')[:12]} and the checkout "
                    f"is now at {str(now.get('commit') or '')[:12]}")
            else:
                changed.append("tracked or untracked files in the checkout "
                               "have changed since it ran, so it did not run "
                               "against this code")
    frozen_policy = record.get("policyRevision")
    if frozen_policy:
        try:
            current = profiles.resolve(
                record.get("intent") or "work",
                worktree=(config.WORKTREES / record["task"][:-3])
                if record.get("task", "").endswith(".md") else None)
        except ValueError:
            current = None
        if current is not None and current["revision"] != frozen_policy:
            changed.append("the execution policy — which carries the check's "
                           "own definition — has changed since it ran")
    return {"fresh": not changed, "changed": changed,
            "stale": bool(changed) and record.get("outcome") == "passed"}


def outcome_now(record: dict) -> dict:
    """The outcome a reader should be shown: what was recorded, unless its
    binding has gone, in which case `stale`.

    This is the one function that may disagree with the stored `outcome`,
    and it may only ever disagree in the safe direction: a `passed` whose
    source or policy moved becomes `stale`, and nothing ever becomes
    `passed` here.

    Memoised for `_FRESH_TTL`, because the question costs git. Only a
    `passed` can change reading at all, so only a `passed` is asked —
    everything else is already the record's own last word, and no amount
    of re-reading turns a failure into anything else.
    """
    if record.get("outcome") != "passed":
        return {"outcome": record.get("outcome"), "reason": record.get("reason"),
                "why": record.get("why"),
                "freshness": {"fresh": True, "changed": []},
                "recorded": record.get("outcome")}
    key = record.get("id") or ""
    cached = _FRESH.get(key)
    if cached and time.time() - cached[0] < _FRESH_TTL:
        return cached[1]
    found = _outcome_now(record)
    if key:
        if len(_FRESH) > 500:        # a memo, not a store
            _FRESH.clear()
        _FRESH[key] = (time.time(), found)
    return found


def _outcome_now(record: dict) -> dict:
    fresh = freshness(record)
    if fresh["stale"]:
        return {"outcome": "stale", "reason": "source-changed"
                if any("source" in c or "files" in c for c in fresh["changed"])
                else "policy-changed",
                "why": fresh["changed"][0], "freshness": fresh,
                "recorded": record.get("outcome")}
    return {"outcome": record.get("outcome"), "reason": record.get("reason"),
            "why": record.get("why"), "freshness": fresh,
            "recorded": record.get("outcome")}


# ── what the board shows ───────────────────────────────────────────────

PUBLIC = ("v", "id", "runner", "task", "stage", "agent", "actor", "intent",
          "check", "scope", "requested", "requestId", "policyId",
          "policyRevision", "policySource", "contextIds", "adapter",
          "location", "log", "logBytes", "logTruncated", "prepare", "argv",
          "command", "scopeArgs", "root", "source", "env", "outcome",
          "reason", "why", "rc", "started", "ended", "duration", "timeout",
          "checkId", "checkRevision", "required", "help", "queued",
          "duplicate")


def public(record: dict | None) -> dict | None:
    """One record as the board may show it. An allowlist, for the reason
    `profiles.public` keeps one: the failure being prevented is a new key
    carrying something that should not travel. No environment *value* and
    no file content has ever been in this structure."""
    if not record:
        return None
    out = {key: record[key] for key in PUBLIC if key in record}
    out["live"] = record.get("outcome") in LIVE
    return out


def rollup(filename: str, manifest: dict | None = None) -> dict:
    """Every check this card's work was supposed to verify, and what
    actually happened to each.

    The row that matters most is the one with no evidence at all: a check
    declared for the run and never asked for is `not-run`, said in those
    words. An agent that finished without asking is exactly the case a
    board must not render as a green card, and the absence of a record is
    the only thing that can tell you.
    """
    records = for_task(filename)
    rows = []
    seen: set[str] = set()
    for record in records:
        name = record.get("check")
        reading = outcome_now(record)
        rows.append({
            "check": name, "scope": record.get("scope"),
            "evidence": record["id"], "outcome": reading["outcome"],
            "reason": reading["reason"], "why": reading["why"],
            "recorded": reading["recorded"], "freshness": reading["freshness"],
            "started": record.get("started"), "ended": record.get("ended"),
            "duration": record.get("duration"), "rc": record.get("rc"),
            "command": record.get("command"),
            "commit": (record.get("source") or {}).get("commit"),
            "root": (record.get("root") or {}).get("path"),
            "rootPolicy": (record.get("root") or {}).get("policy"),
            "agent": record.get("agent"), "actor": record.get("actor"),
            "intent": record.get("intent"),
            "prepare": record.get("prepare") or [],
            "live": record.get("outcome") in LIVE,
            "location": record.get("location") or "local",
            "logBytes": record.get("logBytes"),
        })
        if name:
            seen.add(name)
    for check in available(manifest):
        if check["name"] in seen:
            continue
        rows.append({
            "check": check["name"], "scope": None, "evidence": None,
            "outcome": "not-run", "reason": "never-asked",
            "why": (f"the check '{check['name']}' is available to this run and "
                    f"nothing has asked for it"),
            "recorded": None, "freshness": {"fresh": True, "changed": []},
            "command": check["run"], "rootPolicy": check["root"],
            "required": check["required"], "live": False,
            "scopes": sorted(check["scopes"]), "help": check["help"],
            "location": "local",
        })
    order = {"failed": 0, "prepare-failed": 1, "error": 2, "denied": 3,
             "stale": 4, "running": 5, "preparing": 5, "cancelled": 6,
             "not-run": 7, "passed": 8}
    rows.sort(key=lambda r: (order.get(r["outcome"], 9),
                             -(r.get("ended") or r.get("started") or 0)))
    return {"task": filename, "rows": rows,
            "verified": any(r["outcome"] == "passed" for r in rows),
            "location": "local"}


def board(collected: dict) -> dict:
    """The compact per-card reading the board view needs: how many checks
    a card has evidence for and the worst thing any of them says.

    Only cards with something to say appear, which is the law every other
    chip on this board follows — a board nobody configured checks on gets
    an empty object and renders exactly what it always did.
    """
    global _BOARD
    files = frozenset(task["file"] for stage in collected.get("stages", [])
                      for task in stage["tasks"])
    with state.LOCK:
        epoch = _EPOCH
    # The key carries a time bucket as well as the epoch, because a pass
    # can go stale without anything being stored — the source moved under
    # it. Epoch alone would hold a `passed` chip on a card whose code has
    # changed for as long as nothing else was verified.
    stamp = (epoch, int(time.time() / _FRESH_TTL))
    if _BOARD is not None and _BOARD[0] == stamp and _BOARD[1] == files:
        return _BOARD[2]

    worst = ("failed", "prepare-failed", "error", "denied", "stale",
             "cancelled", "running", "preparing", "passed")
    found: dict = {}
    by_task: dict[str, list[dict]] = {}
    for record in all_evidence():
        task = record.get("task") or ""
        if task not in files:
            continue
        by_task.setdefault(task, []).append(record)
    for filename, records in by_task.items():
        # One row per check: the chip summarises a card, and an older run
        # of the same check is history the sheet shows rather than a
        # second thing to weigh here.
        latest: dict[str, dict] = {}
        for record in records:              # all_evidence() is newest first
            latest.setdefault(record.get("check") or "", record)
        records = list(latest.values())
        readings = [outcome_now(r) for r in records]
        outcomes = [r["outcome"] for r in readings]
        pick = next((w for w in worst if w in outcomes), outcomes[0])
        found[filename] = {
            "outcome": pick,
            "checks": len({r.get("check") for r in records}),
            "passed": outcomes.count("passed"),
            "failed": outcomes.count("failed"),
            "live": any(r.get("outcome") in LIVE for r in records),
            "latest": max((r.get("ended") or r.get("requested") or 0)
                          for r in records),
            "location": "local",
        }
    _BOARD = (stamp, files, found)
    return found


def forget_cached() -> None:
    """Drop the memoised readings. Nothing on the serving path calls it —
    the epoch and the TTL do that work — but a test, and anything that
    changes a root under the board's feet, needs the door."""
    global _BOARD
    _FRESH.clear()
    _BOARD = None


def line(record: dict) -> str:
    """One verification, as the ticker tells it. The outcome in words, the
    check by name, and what a reader would go and look at next."""
    word = {
        "passed": "passed", "failed": "FAILED", "denied": "was refused",
        "prepare-failed": "could not be prepared", "cancelled": "was cancelled",
        "error": "did not reach a verdict", "running": "is running",
        "preparing": "is being prepared", "stale": "is stale",
    }.get(record.get("outcome") or "", record.get("outcome") or "ended")
    bits = [f"{record.get('check')} {word} on {record.get('task')}"]
    if record.get("scope"):
        bits[0] += f" (scope {record['scope']})"
    if record.get("duration"):
        bits.append(f"{record['duration']:.0f}s")
    if record.get("rc") is not None and record.get("outcome") == "failed":
        bits.append(f"rc={record['rc']}")
    source = record.get("source") or {}
    if source.get("commit"):
        bits.append(f"at {source['commit'][:12]}"
                    + (f" +{len(source['dirty'])} uncommitted"
                       if source.get("dirty") else ""))
    if record.get("outcome") not in ("passed", "running", "preparing"):
        bits.append(record.get("why") or "")
    return " · ".join(b for b in bits if b)
