"""What a verdict is about: the result identity, and the runner's own.

A pass holds while everything it depends on holds — the exact source, the
check's substance, its coverage, its programs, its preparation, its
declared inputs, its root class, its declared environment and **this
runner**. This module computes each dimension, and nothing else: it runs
no check, stores no record and reads no record's freshness (`verify`,
`evidence` and `staleness` do those).

## The runner revision, and its one owner

How an exit status became a recorded outcome, how a record reached the
disk, and how a stored record is read as still holding are all code. A
recorded verdict is only worth what that code was, so the code is a
dimension of every result identity (`runner`) and is compared whenever a
record's freshness is read.

Before task 121 that dimension was the sha256 of `verify.py`'s own bytes.
Splitting `verify.py` would have moved authority out of what the hash
covered, so the revision is now a digest over a declared set of files —
`RUNNER_FILES` — and this module is its only owner. Every reader looks up
`resultidentity.RUNNER_REVISION` when it is asked; nothing copies it at
import, and `verify` no longer has the attribute at all, so a test that
still patches the old name fails rather than passing while inert.

**The boundary is an audit, not a hand-picked list.** Every core module
a runner file imports is either itself in `RUNNER_FILES`, or named in
`BOUND_ELSEWHERE` with the reason its effect on a verdict is bound some
other way — re-derived and compared when freshness is read, decided at
request time and recorded as provenance, or display only. A test walks
the imports and refuses a dependency in neither list, and refuses a
stale entry in either (`tests/test_runner_identity.py`).

Changing the definition changes the value once: on promotion every
recorded pass reads `runner-changed`, and each required check on an open
card needs one fresh run. A rollback costs the same again. Nothing reruns
by itself and no record is rewritten; `verify/gate-compare` previews the
changed readings before a promotion.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import config
import gitproc
import profiles


# ── the runner revision ────────────────────────────────────────────────
#
# The files whose code produces or interprets a recorded verdict. Order is
# part of the definition: the digest is over (name, sha256) pairs in this
# order, so adding a file changes the revision exactly once.

RUNNER_FILES = (
    "verify.py",          # the runner: argv, environment, exit -> outcome,
                          # reservation, reuse selection, cancellation
    "resultidentity.py",  # every identity dimension, and this revision
    "evidence.py",        # how a record is stored and read back, and the
                          # outcome vocabulary a verdict is written in
    "staleness.py",       # whether a stored pass still holds, equivalence
    "records.py",         # the atomic, durable write a record attests by
    "gitproc.py",         # the hardened git a source identity and a root
                          # class are read through (task 121 routed the
                          # runner's git here directly, not via config)
    "verifierreport.py",  # the verifier report contract: how a report is
                          # read and combined with an exit status (task 135)
)

# Every other core module the runner files import, and why its effect on
# a verdict is bound without being inside the revision. Held equal to the
# real imports by `tests/test_runner_identity.py`.
BOUND_ELSEWHERE = {
    "artifacts": ("provenance only: what a check declaring \"outputs\" "
                  "wrote is kept after the outcome is already decided, and "
                  "the record only names what was kept; keeping it changes "
                  "no outcome, no identity dimension and no freshness "
                  "answer (task 133)"),
    "config": ("re-derived: the paths, the child environment and the "
               "branch a source check follows are read again when freshness "
               "is asked, and every recorded fact they shaped (the variable "
               "names a check was given, the source identity, the branch "
               "tip) is compared rather than trusted"),
    "profiles": ("re-derived: the check's substance, its scopes, its "
                 "preparation, its inputs' context ids and the stripped "
                 "environment names are resolved from the policy in force "
                 "and compared with the identity on record — a parser change "
                 "that builds a different argv reads `check-changed`. "
                 "Authorisation is asked afresh at every request and is "
                 "never part of a verdict"),
    "reviews": ("re-derived: whether a review check's head is still the "
                "pull request's is read from the PR poll's observation at "
                "every freshness question; provisioning the pinned checkout "
                "is request-time and recorded as provenance"),
    "taskfiles": ("provenance only: the stage a card was in when it asked, "
                  "recorded and never compared"),
    "state": ("coordination only: the lock the registries are read and "
              "reserved under, and the ticker; it decides no recorded field. "
              "The reservation race is held by real workers in "
              "tests/test_runner_identity.py"),
    "redact": ("display only: what a scrubbed log or diagnostic excerpt "
               "shows a reader. It is reached from `evidence.scrub` and from "
               "the verifier report contract's cleaning of the verifier's "
               "own words (`verifierreport.clean`, task 135), which runs "
               "after the report's outcome word is read and changes no "
               "outcome; no function on the verdict path calls either — "
               "held by tests/test_runner_identity.py"),
}


def runner_revision(directory: Path | None = None) -> str | None:
    """The digest over `RUNNER_FILES` as they are in `directory` (this
    module's own, by default). None when any of them cannot be read —
    an unknown runner, which no record can match."""
    where = Path(directory) if directory else Path(__file__).resolve().parent
    parts = ["runner/2"]
    for name in RUNNER_FILES:
        try:
            data = (where / name).read_bytes()
        except OSError:
            return None
        parts += [name, hashlib.sha256(data).hexdigest()]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


# Taken once, at import: the code actually running, whatever the files on
# disk have become since. Read through this module at call time —
# `resultidentity.RUNNER_REVISION` — by every reader.
RUNNER_REVISION = runner_revision()


def digest(*parts: str) -> str:
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


def git(root: Path | str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """`git -C root <args>` through `gitproc` — the same argv and hardened
    read environment `config.git_run` builds, asked of the layer itself so
    the code that reads a source identity is inside the runner revision.
    Never raises: 128 and the exception's text."""
    try:
        return gitproc.git_run(root, *args, timeout=timeout)
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
    head = git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        found["why"] = (f"{root} is not a git checkout bench can read, so the "
                        f"source cannot be identified")
        found["sourceId"] = f"src:unbound@{digest(str(root), str(time.time()))[:12]}"
        return found
    found["commit"] = head.stdout.strip()
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    found["branch"] = branch.stdout.strip() if branch.returncode == 0 else None
    found["dirty"] = config.dirty_files(root)

    parts = [found["commit"]]
    diff = git(root, "diff", "HEAD")
    if diff.returncode != 0:
        found["why"] = "the tracked diff could not be read"
        found["sourceId"] = f"src:unbound@{digest(found['commit'], str(time.time()))[:12]}"
        return found
    parts.append(digest(diff.stdout))
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    names = [n for n in untracked.stdout.split("\0") if n]
    if len(names) > _MAX_BIND_FILES:
        found["why"] = (f"{len(names)} untracked files in {root}, over the "
                        f"{_MAX_BIND_FILES} bench will hash — the source "
                        f"cannot be bound, so this evidence is never reusable")
        found["sourceId"] = f"src:unbound@{digest(*parts, str(time.time()))[:12]}"
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
            found["sourceId"] = f"src:unbound@{digest(*parts, str(time.time()))[:12]}"
            return found
        parts += [name, hashlib.sha256(data).hexdigest()]
    found["bound"] = True
    found["sourceId"] = f"src:{digest(*parts)[:16]}"
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
            sha = _hash_file(real)
        except (OSError, RuntimeError) as exc:
            found["why"] = (f"the {role} {path} could not be read "
                            f"({getattr(exc, 'strerror', None) or exc}), so the "
                            f"program cannot be identified")
            found["files"] = []
            return found
        found["files"].append({"role": role, "path": str(real),
                               "sha256": sha})
        parts += [role, str(real), sha]
    found["bound"] = True
    found["programId"] = f"prog:{digest(*parts)[:16]}"
    return found


def search_path() -> str | None:
    """The PATH a check's executable is resolved on — the child's, which
    `child_env` copies from the board's own environment."""
    return config.child_env().get("PATH")


# ── the environment a check is given ───────────────────────────────────


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
        if name in denied \
                or name.startswith(profiles.VERIFY_ENV_STRIPPED_PREFIXES) \
                or name in profiles.VERIFY_ENV_STRIPPED:
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


# ── declared environment values, bound by a keyed hash ─────────────────
#
# A check that declares `"env": ["DATABASE_URL"]` has its verdict bound to
# that variable's value. The value is never stored: what is recorded is an
# HMAC-SHA256 of it under a key that exists only on this machine
# (`state/verify/env.key`, 0600). So the record proves "the same value as
# when it passed" to this board and to nobody else, and a stolen record
# cannot be dictionary-attacked without the key.
#
# The key is made by the runner, at the moment a check that declares
# `env` is about to run — never by reading. A freshness question that
# finds no key does not make one: it answers that the binding cannot be
# shown to hold, which is the conservative direction. A lost or replaced
# key therefore costs exactly one fresh run of each check that declared
# environment variables, and nothing else.

ENV_KEY_BYTES = 32
_UNSET = "\x00unset"


def env_key_path() -> Path:
    return config.VERIFY_DIR / "env.key"


def _read_env_key() -> bytes | None:
    """The key, if one is here and fit to use. Unreadable, the wrong size,
    or readable by anyone but this user, and it is not used."""
    path = env_key_path()
    try:
        info = path.stat()
        if info.st_mode & 0o077:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if len(data) == ENV_KEY_BYTES else None


def env_key(create: bool) -> bytes | None:
    """The key, made here only when `create` — which only the runner
    passes. Raises when one exists that bench will not use, so the runner
    refuses rather than quietly replacing a key an operator may care
    about."""
    found = _read_env_key()
    if found is not None or not create:
        return found
    path = env_key_path()
    if path.exists():
        raise ValueError(
            f"{path} exists and bench will not use it — it must be exactly "
            f"{ENV_KEY_BYTES} bytes and readable by this user only. Remove "
            f"it to have bench make a new one; every check that declares "
            f"environment variables then needs one fresh run")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_env_key()
    with os.fdopen(fd, "wb") as handle:
        handle.write(os.urandom(ENV_KEY_BYTES))
    return _read_env_key()


def key_id(key: bytes) -> str:
    """Which key bound a record, without saying anything about it."""
    return "key:" + hmac.new(key, b"bench:verify:env-key-id",
                             hashlib.sha256).hexdigest()[:12]


def _env_value_hash(key: bytes, name: str, value: str | None) -> str:
    message = f"{name}\x00{_UNSET if value is None else value}".encode(
        "utf-8", "surrogateescape")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def env_identity(env: dict, declared: list[str], key: bytes | None) -> dict:
    """What the environment a check was given *was*, as far as bench binds
    it: the names of every variable passed (never their values), and a
    keyed hash of each declared variable's value — or of its absence.

    That is all. Bench does not claim the whole runtime environment is
    equal: an undeclared value, a library on disk and a service on the
    network are outside it, and the documentation says so."""
    names = sorted(env)
    bound = []
    for name in declared:
        value = env.get(name)
        bound.append({"name": name, "set": value is not None,
                      "hmac": _env_value_hash(key, name, value) if key else None})
    found = {"passed": names, "declared": bound,
             "keyId": key_id(key) if (key and declared) else None}
    found["envId"] = "env:" + digest(
        "env/1", json.dumps(names),
        json.dumps([[b["name"], b["hmac"]] for b in bound]))[:16]
    return found


# ── the result identity (design §4.1) ──────────────────────────────────
#
# Everything a verdict *depends on*, kept apart from everything that only
# says how the run came about. A pass holds while every dimension below
# holds; it is never invalidated by provenance — which policy revision was
# in force, which unrelated context was approved for some other card, who
# asked, under which model, from which temporary path.

IDENTITY_VERSION = 1
IDENTITY_DIMENSIONS = ("source", "check", "coverage", "program", "prepare",
                       "inputs", "rootClass", "env", "runner")


def coverage_id(scope: str | None, scope_args: list[str]) -> str:
    return "cov:" + digest("coverage/1", scope or "", json.dumps(scope_args))[:16]


def prepare_definition(steps: list[dict]) -> str:
    """The preparation a check needs, as substance: each step's argument
    list, whether it needs the network and whether it is required. Not its
    name — renaming a step changes nothing it does."""
    return "prep:" + digest("prepare/1", json.dumps(
        [[s.get("executable"), list(s.get("argv") or []), bool(s.get("network")),
          bool(s.get("required", True))] for s in steps]))[:16]


def inputs_id(inputs: list[dict]) -> str:
    return "inputs:" + digest("inputs/1", json.dumps(
        [[i.get("name"), i.get("contextId")] for i in inputs]))[:16]


def result_id(identity: dict) -> str:
    """One digest over every dimension — equal only when all of them are."""
    return "result:" + digest("result/1", *(
        json.dumps(identity.get(d), sort_keys=True)
        for d in IDENTITY_DIMENSIONS))[:16]


def identity(record: dict, source: dict, program: dict, steps: list[dict],
             programs: list[dict], inputs: list[dict], klass: str,
             env_id: str) -> dict:
    """The result identity, from its parts. One function, so the identity
    a run records and the identity a request *would* have
    (`verify._prospective`, which decides reuse) can never be computed two
    different ways. The runner dimension is this module's revision as it
    reads at the moment of the call."""
    return {
        "v": IDENTITY_VERSION,
        "source": source.get("sourceId") if source.get("bound") else None,
        "check": record.get("checkSubstance"),
        "coverage": coverage_id(record.get("scope"), record.get("scopeArgs") or []),
        "program": program.get("programId") if program.get("bound") else None,
        "prepare": {"definition": prepare_definition(steps),
                    "programs": [p.get("programId") if p.get("bound") else None
                                 for p in programs]},
        "inputs": inputs_id(inputs),
        "rootClass": klass,
        "env": env_id,
        "runner": RUNNER_REVISION,
    }


def comparable(identity: dict) -> dict:
    """A result identity without its `check` dimension — the substance
    digest, which carries the check's declared root by name. Two records
    of different names are compared on everything else here, and on their
    definitions by `equivalence`, which compares the root by its class."""
    return {k: v for k, v in (identity or {}).items() if k != "check"}


def root_class(policy: str, root: Path) -> str:
    """`worktree`, `fresh` or `reused` — what the checkout could have held
    that the source identity does not see.

    A card's worktree can hold ignored state (caches, `node_modules`, a
    virtualenv) the source identity deliberately does not hash. A `source`
    or `review` checkout is only called `fresh` when the runner genuinely
    found it so as the check began — no ignored, untracked or modified
    file in it. A `source` checkout is made for each run (task 131), so
    it is fresh by construction; a review's pinned checkout outlives one
    check and may not be. One that already held such state is `reused`,
    which is said rather than papered over."""
    if policy not in ("source", "review"):
        return "worktree"
    status = git(root, "status", "--porcelain", "--ignored",
                 "--untracked-files=all")
    if status.returncode != 0:
        return "reused"
    return "fresh" if not status.stdout.strip() else "reused"


# ── memo ───────────────────────────────────────────────────────────────


def forget() -> None:
    """Drop this module's memo — the program-file hashes. `verify`'s
    `forget_cached` calls it with every other owner's."""
    _HASHED.clear()
