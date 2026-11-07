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
import reviews
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
    "program-changed": ("the program the check or a preparation step ran has "
                        "changed at its path since, or was never identified"),
    "runner-changed": "bench's own runner has changed since it ran",
    "head-changed": "the pull request's head has moved on since, so the "
                    "result is about an earlier revision",
    "pr-ended": "the pull request has been merged or closed since, so the "
                "result is historical rather than current",
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
_SLOTS_FOR = config.VERIFY_PARALLEL


def slots() -> threading.BoundedSemaphore:
    """How many verifications may run at once, read from the setting
    rather than from whatever it said at import. A check is somebody
    else's test suite: two at once is a sensible machine's worth, and a
    board whose operator says one means one."""
    global _SLOTS, _SLOTS_FOR
    with state.LOCK:
        if config.VERIFY_PARALLEL != _SLOTS_FOR:
            _SLOTS = threading.BoundedSemaphore(config.VERIFY_PARALLEL)
            _SLOTS_FOR = config.VERIFY_PARALLEL
        return _SLOTS

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
_BOARD: tuple[tuple, frozenset, dict] | None = None


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


# ── what the program actually was ──────────────────────────────────────
#
# A path and an argument list name a program; they do not pin one. An
# operator-local executable replaced at the same path is a different check
# with the same name, so the bytes are part of what a pass is about.
#
# Three kinds of file are bound, each because the verdict comes from it:
# the **executable** as it resolves (on the child's PATH, symlinks
# followed), the **interpreter** a script's `#!` line names — one level,
# no further — and every **argument** that is an absolute path to a file
# outside the execution root, which is how `python3 /opt/ci/check.py`
# hands an interpreter its real program. A file *inside* the root is the
# source identity's to bind, and is not repeated here.
#
# What it does not bind, said where it is bound: the libraries, modules
# and services those programs load in turn. That closure is unbounded,
# and claiming it would be the sandbox nothing here provides.

PROGRAM_WHY = ("the executable, the interpreter its #! line names and any "
               "absolute file argument outside the root are hashed, so a "
               "file replaced at the same path is a different program. This "
               "pins what was invoked; it is not a sandbox, and it does not "
               "bind the libraries, modules or services those programs load")

# The runner itself is part of what a verdict is about: how an exit status
# became an outcome is this module's code. Hashed once, at import — the
# code actually running, whatever the file on disk has become since.
try:
    RUNNER_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
except OSError:                       # pragma: no cover — we were imported
    RUNNER_REVISION = None

_HASHED: dict[tuple, str] = {}


def _hash_file(path: Path) -> str:
    """One file's sha256, memoised on everything a replacement changes —
    the inode, the size, and both times, including the ctime nobody can
    set — so asking twice costs a stat rather than a read."""
    info = path.stat()
    key = (str(path), info.st_dev, info.st_ino, info.st_size,
           info.st_mtime_ns, info.st_ctime_ns)
    found = _HASHED.get(key)
    if found is None:
        found = hashlib.sha256(path.read_bytes()).hexdigest()
        if len(_HASHED) > 500:
            _HASHED.clear()
        _HASHED[key] = found
    return found


def _shebang(path: Path, search_path: str | None) -> str | None:
    """The interpreter a script's first line names, resolved — `env`
    indirection included, since that is how most scripts name one."""
    try:
        with path.open("rb") as handle:
            first = handle.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode("utf-8", "replace").split()
    if not words:
        return None
    if Path(words[0]).name == "env":
        names = [w for w in words[1:] if not w.startswith("-")]
        return shutil.which(names[0], path=search_path) if names else words[0]
    return words[0]


def program_identity(argv: list[str], root: Path | str,
                     search_path: str | None) -> dict:
    """What `argv` would execute, as content. `programId` digests every
    bound file; an identity that could not be established is `bound:
    False` and is never reusable as proof that a program is unchanged."""
    found: dict = {"programId": None, "bound": False, "files": [],
                   "why": PROGRAM_WHY}
    if not argv:
        found["why"] = "there is no program to identify"
        return found
    exe = argv[0]
    resolved = exe if "/" in exe else shutil.which(exe, path=search_path)
    if not resolved:
        found["why"] = (f"{exe} does not resolve on the check's PATH, so the "
                        f"program it would run cannot be identified")
        return found
    wanted = [("executable", Path(resolved))]
    interpreter = _shebang(Path(resolved), search_path)
    if interpreter:
        wanted.append(("interpreter", Path(interpreter)))
    inside = Path(str(root)).resolve()
    for token in argv[1:]:
        if not token.startswith("/"):
            continue
        candidate = Path(token)
        try:
            real = candidate.resolve()
        except OSError:
            continue
        if real.is_file() and not real.is_relative_to(inside):
            wanted.append(("argument", candidate))
    parts: list[str] = []
    for role, path in wanted:
        try:
            real = path.resolve(strict=True)
            digest = _hash_file(real)
        except (OSError, RuntimeError) as exc:
            found["why"] = (f"the {role} {path} could not be read "
                            f"({getattr(exc, 'strerror', None) or exc}), so the "
                            f"program cannot be identified")
            found["files"] = []
            return found
        found["files"].append({"role": role, "path": str(real),
                               "sha256": digest})
        parts += [role, str(real), digest]
    found["bound"] = True
    found["programId"] = f"prog:{_digest(*parts)[:16]}"
    return found


def _search_path() -> str | None:
    """The PATH a check's executable is resolved on — the child's, which
    `child_env` copies from the board's own environment."""
    return config.child_env().get("PATH")


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

    Built rather than inherited. Four things leave: **publication
    credentials**, by name, because a test suite has no business being
    able to push or publish and the only reliable way to say so is for the
    token not to be there; the **launch environment** (`AGENT_*`), because
    a check has no business reading the prompt or the resolved policy; the
    board's **own address** (`BOARD_PORT`, `BOARD_AGENT_ID`), so a check
    cannot turn round and ask bench for another verification; and the
    **resolved layout** (`BOARD_APP_ROOT` and the rest), which is the same
    rule applied to the same kind of fact — which application this board
    manages and where its tickets live is bookkeeping, not configuration
    for the suite under test.

    That last one is not hypothetical. A check runs *in* the root bench
    gave it and is told so (`BENCH_VERIFY_ROOT`), so it needs none of
    them; a project whose own code reads the board's roots at import —
    bench itself, as it happens — would otherwise resolve the operator's
    application while being verified against a checkout of its own.

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
                            "BOARD_APP_ROOT", "BOARD_WORK_ROOT",
                            "BOARD_TASKS_DIR", "BOARD_WORKTREES_DIR",
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
        # `cancel()` takes the reason from its caller, so a word this
        # table does not hold is possible — and a KeyError here would
        # leave the record `running` for ever, which is the one thing a
        # cancellation must not do. The word itself is the fallback.
        return {"rc": rc, "started": started, "ended": ended,
                "outcome": "cancelled", "reason": cancelled,
                "why": REASONS.get(cancelled, cancelled)}
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


# The card name a diagnosis files its evidence under. Not a card — no
# stage directory holds it and `for_task` of any real card never returns
# it — which is the point: a doctor's run must be visible as a doctor's
# run and must never be read as one of a card's checks having passed.
FIXTURE_TASK = "bench-doctor.fixture.md"


def request(*, check: str, task: str | None = None, agent: str | None = None,
            scope: str | None = None, intent: str | None = None,
            stage: str | None = None, actor: str = "agent",
            fixture: dict | None = None) -> dict:
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

    `fixture` is the one caller that supplies both halves itself —
    `doctor.py`, with a policy it resolved from a fixture document and a
    root it made for the diagnosis. Everything below it is the runner this
    function has always been: the argument list bench builds, the isolated
    environment, the preparation, the evidence written before the caller
    is told anything. What it is *not* is a second runner, which is the
    whole reason the parameter exists rather than a copy of this body. The
    root is the caller's, so `_cleanup` removes nothing of it.
    """
    frozen = None
    if fixture is not None:
        task = FIXTURE_TASK
        intent = (fixture["manifest"].get("intent") or "work")
        stage = ""
    else:
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

    manifest = fixture["manifest"] if fixture is not None else frozen
    if manifest is None:
        # A person's request, so the profile resolves fresh — with the
        # review checkout this card is still holding, if it is one. Without
        # that the reading would offer a review's checks and then refuse
        # them by name, which is the one thing a rerun affordance must not
        # do. Nothing is provisioned to make it available.
        held = reviews.live_for_task(task) if intent == "review" else None
        try:
            manifest = profiles.manifest(
                intent, task, stage,
                review=(held or {}).get("path") or None,
                review_record=held)
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
    if fixture is not None:
        # Set before any refusal, so a diagnosis's *refused* record is as
        # recognisably the doctor's as a successful one. A reader must
        # never have to wonder whether a row under this name was a card's.
        shell["doctor"] = str(fixture.get("label") or "doctor")
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
    if fixture is not None:
        root, prepared = Path(str(fixture["root"])), False
        root_note = (f"a fixture {fixture.get('label') or 'the doctor'} made "
                     f"and removes itself — bench owns nothing here")
        if not root.is_dir():
            return _refuse(shell, "root-unavailable",
                           f"the fixture root {root} is not there, so nothing "
                           f"was run")
    else:
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
        "runner": {"module": "verify", "version": VERSION,
                   "revision": RUNNER_REVISION},
        "task": task, "stage": stage, "agent": agent, "actor": actor,
        "intent": intent, "check": check, "scope": scope,
        "requested": when,
        "requestId": request_id,
        "policyId": (manifest or {}).get("policyId"),
        "policyRevision": (manifest or {}).get("revision"),
        "policySource": ((manifest or {}).get("source") or {}).get("kind"),
        "contextIds": context,
        # which revision a PR review was pinned to, when the run asking is
        # one. It closes the chain: policy, context, request, review and
        # evidence are all named in one record, so "what did this check
        # actually run against" is answerable from the evidence alone.
        "reviewId": ((manifest or {}).get("review") or {}).get("reviewId"),
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
    """Where this check runs, and whether *this verification* made it.

    Three answers, and the application checkout is not one of them. A
    `worktree` check runs in the card's own checkout — which bench
    created, for this card, and which a request for another card can never
    name because the path is derived from the task. A `source` check runs
    in a detached checkout of one exact commit that bench adds here and
    removes afterwards. A `review` check runs in the checkout bench pinned
    for the PR review that asked, which bench made and owns for that run's
    whole life rather than for this one verification — so `owned` is False
    for it, and `_cleanup` leaves it to `reviews.release`.

    A review must never run a test suite in the live application, and the
    `review` root is how it runs one against the *proposed* code instead.
    """
    stem = task[:-3]
    if check["root"] == "review":
        return _review_root(check, task, manifest)
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


def _review_root(check: dict, task: str,
                 manifest: dict) -> tuple[Path, bool, str | None]:
    """The pinned PR-review checkout the asking run holds.

    Read off the frozen manifest, which is where the launch recorded it —
    so a check runs against the revision that run was pinned to, and not
    against whatever the branch has since become. A person's rerun has no
    manifest of its own, so it falls to the record still held for the
    card; with none, the check is refused rather than run somewhere else.

    Nothing is provisioned here. Checking a PR out is a review launch's
    side effect, with the ownership record that goes with it, and a
    verification that quietly made a second one would own a checkout
    nobody could account for.
    """
    record = (manifest or {}).get("review") or reviews.live_for_task(task)
    if not record or not record.get("path"):
        raise ValueError(
            f"the check '{check['name']}' runs in the checkout bench pins for "
            f"a PR review, and the run asking holds none — ◔ review PR pins "
            f"one for the reviewed head, and a relevance check is pinned "
            f"nothing. Declare the check with \"root\": \"source\" to have "
            f"bench check this card's branch out instead")
    root = Path(str(record["path"]))
    mismatch = config.worktree_mismatch(root)
    if mismatch:
        raise ValueError(mismatch)
    if not root.is_dir():
        raise ValueError(
            f"the review checkout {root} this run was pinned is gone, so the "
            f"check '{check['name']}' has nothing to run against — nothing "
            f"was re-checked-out in its place")
    pinned = str((record.get("head") or {}).get("commit") or "")
    head = _git(root, "rev-parse", "HEAD")
    at = head.stdout.strip() if head.returncode == 0 else ""
    if pinned and at != pinned:
        raise ValueError(
            f"the review checkout {root} is at {at[:12] or 'an unknown commit'} "
            f"and this run was pinned to {pinned[:12]} — bench will not verify "
            f"a revision the review is not of, and resets nothing to make it "
            f"match")
    return root, False, (f"bench's own checkout of PR "
                         f"#{record.get('number')} at {pinned[:12]}, pinned "
                         f"for this review")


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
    # The bytes behind the argument list, read before the process starts:
    # a file replaced at the same path afterwards is a different program,
    # and `freshness` compares this to say so.
    record["program"] = program_identity(record["argv"], root, env.get("PATH"))
    _store(record)

    waited = time.time()
    slot = slots()
    acquired = slot.acquire(timeout=max(check["timeout"], 60))
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
        slot.release()


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
        program = program_identity(argv, root, env.get("PATH"))
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
            "program": program,
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
    # The same findings as slugs, in the same order, so a consumer branches
    # on data rather than on the prose beside it — the law `REASONS` was
    # written under, applied to the one place that had grown a string
    # match instead.
    reasons: list[str] = []

    def moved(slug: str, text: str) -> None:
        reasons.append(slug)
        changed.append(text)

    if not record:
        return {"fresh": True, "changed": [], "reasons": [], "stale": False}
    source = record.get("source") or {}
    where = record.get("root") or {}
    root = Path(where.get("path") or "")
    if not source.get("bound"):
        moved("source-changed",
              source.get("why") or
              "the source this ran against was never bound, so the "
              "result cannot be reused")
    elif where.get("policy") == "review":
        # A `review` check ran in a checkout bench pinned to one commit —
        # the head a pull request proposed — and then removed when the
        # review ended, on purpose. So the checkout being gone says
        # nothing, exactly as it says nothing for a `source` check. The
        # binding is the reviewed head, and the question is whether the
        # pull request still proposes it: a push that lands after the
        # check makes this evidence history rather than the new head's
        # pass. Read from what the PR poll last saw, so asking costs no
        # call to GitHub (`reviews.observe`).
        now = reviews.observed(record.get("task") or "") or {}
        current = str(now.get("commit") or "")
        if current and current != source.get("commit"):
            moved("head-changed",
                  f"the pull request's head has moved on — this ran against "
                  f"{str(source.get('commit') or '')[:12]} and the PR is now "
                  f"at {current[:12]}, so it says nothing about the code "
                  f"there now")
        elif str(now.get("state") or "").upper() in ("MERGED", "CLOSED"):
            moved("pr-ended",
                  f"the pull request has been "
                  f"{str(now.get('state')).lower()} since this ran, so it is "
                  f"a historical record of what was proposed rather than a "
                  f"current result")
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
            moved("source-changed",
                  f"the branch this ran against ({branch or 'unknown'}) no "
                  f"longer exists, so what it covered cannot be checked")
        elif now.stdout.strip() != source.get("commit"):
            moved("source-changed",
                  f"the branch has moved on — it ran against "
                  f"{str(source.get('commit') or '')[:12]} and {branch} is "
                  f"now at {now.stdout.strip()[:12]}")
    elif not root.is_dir():
        moved("source-changed",
              f"the checkout it ran in ({root}) is gone, so what it ran "
              f"against can no longer be checked")
    else:
        now = source_identity(root)
        if now.get("sourceId") != source.get("sourceId"):
            if now.get("commit") != source.get("commit"):
                moved("source-changed",
                      f"the source has moved on — it ran against "
                      f"{str(source.get('commit') or '')[:12]} and the "
                      f"checkout is now at "
                      f"{str(now.get('commit') or '')[:12]}")
            else:
                moved("source-changed",
                      "tracked or untracked files in the checkout have "
                      "changed since it ran, so it did not run against this "
                      "code")
    # The program and the runner: what was invoked, and the code that read
    # its exit status. Both fail closed — a record that names no identity
    # cannot say the program is unchanged, so it is not reused as a pass.
    runner = (record.get("runner") or {}).get("revision")
    if not runner or runner != RUNNER_REVISION:
        moved("runner-changed",
              "bench's own runner has changed since this ran"
              if runner else
              "this was recorded by a runner that named no revision of "
              "itself, so it cannot be shown to be the runner here now")
    program = record.get("program")
    search = _search_path()
    if not program or not program.get("bound"):
        moved("program-changed",
              (program or {}).get("why") if program and not program.get("bound")
              else "the record names no program identity, so the executable "
                   "it ran cannot be shown to be unchanged")
    elif program_identity(list(record.get("argv") or []), root,
                          search).get("programId") != program.get("programId"):
        moved("program-changed",
              "the check's executable, its interpreter or a file it was "
              "handed has changed since it ran — a program replaced at the "
              "same path is a different check")
    for step in record.get("prepare") or []:
        if step.get("outcome") != "prepared":
            continue
        frozen = step.get("program")
        try:
            argv = shlex.split(str(step.get("command") or ""))
        except ValueError:
            argv = []
        if not frozen or not frozen.get("bound") or \
                program_identity(argv, root, search).get("programId") \
                != frozen.get("programId"):
            moved("program-changed",
                  f"the preparation step '{step.get('name')}' runs a program "
                  f"that has changed, or was never identified, since it ran")
            break
    frozen_policy = record.get("policyRevision")
    frozen_context = record.get("contextIds")
    if frozen_policy or frozen_context:
        intent = record.get("intent") or "work"
        task = record.get("task") or ""
        try:
            # Asked the way the launch asked it: a review's policy was
            # resolved with the checkout it was pinned and no worktree, so
            # re-resolving it with the other roots would report bench's own
            # question as "the policy changed".
            current = profiles.resolve(
                intent,
                worktree=(config.WORKTREES / task[:-3])
                if task.endswith(".md") and intent != "review" else None,
                review=(where.get("path") if where.get("policy") == "review"
                        else None))
        except ValueError:
            current = None
        if (current is not None and frozen_policy
                and current["revision"] != frozen_policy):
            moved("policy-changed",
                  "the execution policy — which carries the check's own "
                  "definition — has changed since it ran")
        # The third binding, and the one that was named here before it was
        # compared: a context identity is `name@<content hash>`, so an
        # edited approved input is a different identity rather than the
        # same one with new bytes. A pass recorded under inputs the run
        # was *given* is not a pass under different ones — and the policy
        # revision cannot stand in for it, because that digests which
        # files were selected and not what is in them.
        if current is not None and frozen_context:
            now = {i.get("contextId") for i in current.get("context") or []
                   if i.get("contextId")}
            gone = [i for i in frozen_context if i not in now]
            if gone:
                moved("context-changed",
                      f"{len(gone)} approved input"
                      f"{'' if len(gone) == 1 else 's'} this ran with "
                      f"{'has' if len(gone) == 1 else 'have'} changed or gone "
                      f"since — it did not run with the inputs in force now")
    return {"fresh": not changed, "changed": changed, "reasons": reasons,
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
                "freshness": {"fresh": True, "changed": [], "reasons": []},
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
        # The first finding's own slug, rather than a guess from its
        # wording: "the head moved" and "the policy moved" are different
        # things to do next, and a reader branching on the reason must
        # not have them collapse.
        return {"outcome": "stale",
                "reason": (fresh.get("reasons") or ["source-changed"])[0],
                "why": fresh["changed"][0], "freshness": fresh,
                "recorded": record.get("outcome")}
    return {"outcome": record.get("outcome"), "reason": record.get("reason"),
            "why": record.get("why"), "freshness": fresh,
            "recorded": record.get("outcome")}


# ── what the board shows ───────────────────────────────────────────────

PUBLIC = ("v", "id", "runner", "task", "stage", "agent", "actor", "intent",
          "check", "scope", "requested", "requestId", "policyId",
          "policyRevision", "policySource", "contextIds", "reviewId", "adapter",
          "location", "log", "logBytes", "logTruncated", "prepare", "argv",
          "command", "scopeArgs", "root", "source", "env", "outcome",
          "reason", "why", "rc", "started", "ended", "duration", "timeout",
          "checkId", "checkRevision", "required", "help", "queued",
          "duplicate", "program",
          # present only on a diagnosis's own record: which doctor fixture
          # it ran at. A reader must be able to tell a doctor's run from a
          # card's, and an absent key is a card's.
          "doctor")


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
            "recorded": None,
            "freshness": {"fresh": True, "changed": [], "reasons": []},
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
    a card has evidence for or may ask for, and the worst thing any of
    them says. A check the current policy offers and nothing has asked for
    counts as `not-run` on every card — whatever its stage, since a
    `source` check needs no worktree and a done card may keep one.

    Only cards with something to say appear, which is the law every other
    chip on this board follows — a board nobody configured checks on gets
    an empty object and renders exactly what it always did.
    """
    global _BOARD
    files = frozenset(task["file"] for stage in collected.get("stages", [])
                      for task in stage["tasks"])
    # One file read, so an edited policy is seen on the next reading
    # rather than after the bucket turns over.
    doc = profiles.document()
    with state.LOCK:
        epoch = _EPOCH
    # The key carries a time bucket as well as the epoch, because a pass
    # can go stale without anything being stored — the source moved under
    # it. Epoch alone would hold a `passed` chip on a card whose code has
    # changed for as long as nothing else was verified.
    stamp = (epoch, int(time.time() / _FRESH_TTL), doc["revision"])
    if _BOARD is not None and _BOARD[0] == stamp and _BOARD[1] == files:
        return _BOARD[2]

    worst = ("failed", "prepare-failed", "error", "denied", "stale",
             "cancelled", "running", "preparing", "not-run", "passed")
    offered = _offered(doc) if files else ()
    found: dict = {}
    by_task: dict[str, list[dict]] = {}
    for record in all_evidence():
        task = record.get("task") or ""
        if task not in files:
            continue
        by_task.setdefault(task, []).append(record)
    if offered:
        for filename in files:
            by_task.setdefault(filename, [])
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
        # A check the card may ask for and nothing has: the sheet's
        # `not-run` row, counted by name so a check run ten times is one.
        unasked = [n for n in offered
                   if n not in latest]
        outcomes += ["not-run"] * len(unasked)
        pick = next((w for w in worst if w in outcomes), outcomes[0])
        found[filename] = {
            "outcome": pick,
            "checks": len(set(latest) | set(unasked)),
            "passed": outcomes.count("passed"),
            "failed": outcomes.count("failed"),
            "notRun": len(unasked),
            "live": any(r.get("outcome") in LIVE for r in records),
            "latest": max([(r.get("ended") or r.get("requested") or 0)
                           for r in records], default=None),
            "location": "local",
        }
    _BOARD = (stamp, files, found)
    return found


_OFFERED: tuple | None = None


def _offered(doc: dict) -> tuple[str, ...]:
    """The names a person's request from the evidence sheet may ask for
    right now — the `work` reading `/api/evidence` resolves fresh, through
    the same resolver, once per policy revision and time bucket rather than
    once per card. Only the worktree path differs between cards, and a
    `work` check never depends on it. A profile with blockers offers
    nothing, because `request()` would refuse every name. Resolving writes
    nothing: no pin, no checkout, no fetch."""
    global _OFFERED
    key = (doc["revision"], int(time.time() / _FRESH_TTL))
    if _OFFERED is not None and _OFFERED[0] == key:
        return _OFFERED[1]
    names: tuple[str, ...] = ()
    if doc["kind"] == "file":
        try:
            found = profiles.resolve("work", worktree=config.WORKTREES / "_",
                                     policy=doc)
        except (ValueError, OSError):
            found = {"blockers": ["unresolvable"]}
        if not found["blockers"]:
            names = tuple(dict.fromkeys(c["name"] for c in available(found)))
    _OFFERED = (key, names)
    return names


def forget_cached() -> None:
    """Drop the memoised readings. Nothing on the serving path calls it —
    the epoch and the TTL do that work — but a test, and anything that
    changes a root under the board's feet, needs the door."""
    global _BOARD, _OFFERED
    _FRESH.clear()
    _BOARD = None
    _OFFERED = None


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
