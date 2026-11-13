"""Paths, stages and launch configuration for the task manager.

Core knows about tasks, worktrees, PRs and events. It knows nothing about
any particular app (drivers do), agent vendor (adapters do), or project
(local/ does). Everything the other modules need to know about *where
things are* lives here. No state, no behaviour.

Four roots, named separately, because they used to be one directory tree
with one git identity (task 56):

    TM_ROOT    the tool — manager/core, manager/local, prompts, adapters,
               runtime state. Found from __file__, as it always was.
    REPO       **the application**: code, branches, worktrees, PRs. Every
               git subprocess outside taskfiles/sync runs against this.
    WORK       **the work state**: tasks/, plans/, reference/.
    WORKTREES  application worktrees for task branches.

With neither `BOARD_APP_ROOT` nor `BOARD_WORK_ROOT` set, every one of
them resolves exactly as it did before — the vendored layout is the
default, not a special case. Set either and `EXTERNAL` is true, which is
what lets `problems()` refuse a layout that does not hold together
instead of guessing.

A fifth root is derived rather than configured: `state_repo()`, the
repository the board's own bookkeeping is committed to. Vendored, that is
the application repository, because tasks/ genuinely lives in it.
Externally — either root set, `BOARD_APP_ROOT` alone included, where the
work root is still the tool — it is the repository holding the work root,
never the application's — and `None`, a
work root in no repository at all, is a work root bench will not commit
to rather than a reason to reach for the application's history.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

CORE = Path(__file__).resolve().parent             # manager/core — replaceable
MANAGER = CORE.parent                              # manager
LOCAL = MANAGER / "local"                          # project-owned, never replaced
TM_ROOT = MANAGER.parent                           # the tool's own root

STATE = LOCAL / "state"                            # runtime data (gitignored)
SESSIONS_DIR = STATE / "sessions"                  # per-session event logs, JSONL
AGENT_DIR = STATE / "agent"                        # headless-agent stdout logs
DRIVES_DIR = STATE / "drives"                      # driver stdout logs
HOOKS_DIR = STATE / "hooks"                        # generated agent hook config
PLANS_DIR = STATE / "plans"                        # recorded execution plans
MANIFESTS_DIR = STATE / "manifests"                # per-run execution manifests
CONTEXT_DIR = STATE / "context"                    # pinned read-only input bundles
EVIDENCE_DIR = STATE / "evidence"                  # what a verification actually found
VERIFY_DIR = STATE / "verify"                      # isolated roots a check ran in
REVIEWS_DIR = STATE / "reviews"                    # run-owned pinned PR-review checkouts
DOCTOR_DIR = STATE / "doctor"                      # fixtures a diagnosis makes and removes
GATES_DIR = STATE / "gates"                        # Bench review verdicts and gate overrides
REFINE_DIR = STATE / "refine"                      # card-refinement jobs and their proposals
# Agent-reported progress, one record per run. None = beside the agent
# logs (`AGENT_DIR / "progress"`), so whatever relocates a run's log
# relocates what it reported with it — see `progress._dir`.
PROGRESS_DIR: Path | None = None
# Merges whose publication is unfinished. None means "beside the recorded
# plans" (`STATE / "completions"`): a record that finishes a plan's work
# lives wherever that plan does — see `github._recovery_dir`.
COMPLETIONS_DIR: Path | None = None

# Ordered — this is the column order on the board.
STAGES = [
    ("backlog", "Backlog"),
    ("to-do", "To Do"),
    ("in-progress", "In Progress"),
    ("review", "Review"),
    ("done", "Done"),
]
STAGE_DIRS = {slug for slug, _ in STAGES}
STAGE_LABELS = dict(STAGES)


def _load_env() -> dict[str, str]:
    """local/.env, overridden by the process environment. Stdlib-only
    parser: KEY=VALUE lines, # comments, optional quotes around the value."""
    values: dict[str, str] = {}
    path = LOCAL / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    values.update(os.environ)
    return values


_ENV = _load_env()


def setting(key: str, default: str) -> str:
    return _ENV.get(key, default)


def flag(key: str, default: str = "") -> bool:
    """A boolean setting. Anything but empty/0/false/no/off is on."""
    return setting(key, default).strip().lower() not in ("", "0", "false", "no", "off")


# ── reading git without running anything else ──────────────────────────
#
# Reading the board asks git about refs, ancestry and uncommitted work,
# and that has to stay a read. A repository's own configuration can make
# an ordinary `git diff` or `git status` run a program: a textconv or an
# external diff driver named by an attribute, a clean filter, an
# fsmonitor hook, `log.showSignature` reaching for `gpg.program`. And a
# `status` may refresh the index, which is a write. So every git a read
# asks for goes through `git_argv` and `git_env`, which turn all of that
# off for the read verbs below and touch nothing else: a merge, a push,
# a worktree add or a fetch is the authorised side effect it always was,
# run exactly as before.
#
# The settings ride `GIT_CONFIG_COUNT` rather than `-c`, because a filter
# driver's name is whatever the repository says it is — `=` included —
# and an environment pair carries any key verbatim.

_GIT_READS = frozenset({
    "rev-parse", "for-each-ref", "merge-base", "status", "diff", "diff-index",
    "diff-files", "diff-tree", "ls-files", "ls-tree", "cat-file", "rev-list",
    "show", "show-ref", "log", "remote", "symbolic-ref", "config", "describe",
    "name-rev",
})
# Verbs that draw a diff, and so would ask a textconv or an external
# diff driver. `--no-textconv --no-ext-diff` goes straight after the verb.
_GIT_DIFFS = frozenset({"diff", "diff-index", "diff-files", "diff-tree",
                        "show", "log"})
# Verbs that compare the working tree's content with git's, and so would
# run a clean filter or a long-running filter process.
_GIT_FILTERS = frozenset({"diff", "diff-index", "diff-files", "status"})
# Always off for a read: the hooks directory, the fsmonitor hook, the
# global external diff command and signature verification.
_GIT_READ_CONFIG = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
                    ("diff.external", ""), ("log.showSignature", "false"))


def _git_verb(args) -> str:
    return str(args[0]) if args else ""


def git_argv(root: Path | str | None, *args: str) -> list[str]:
    """The argv for `git [-C root] <args>`: a read verb gains
    `--no-textconv --no-ext-diff` where it would draw a diff; anything
    else is returned exactly as written."""
    argv = list(args)
    if _git_verb(argv) in _GIT_DIFFS:
        argv[1:1] = ["--no-textconv", "--no-ext-diff"]
    return ["git", *(["-C", str(root)] if root is not None else []), *argv]


def _filter_drivers(root: Path | str | None) -> list[str]:
    """Every filter driver the repository's configuration names, so a read
    can switch each one off. Read with the read environment itself: a
    `git config` read runs nothing."""
    try:
        result = subprocess.run(
            ["git", *(["-C", str(root)] if root is not None else []), "config",
             "--null", "--name-only", "--get-regexp", r"^filter\."],
            capture_output=True, text=True, timeout=10, env=git_env(None, "config"))
    except (OSError, subprocess.SubprocessError):
        return []
    names = set()
    for key in result.stdout.split("\0"):
        if key.startswith("filter.") and key.count(".") >= 2:
            names.add(key[len("filter."):key.rindex(".")])
    return sorted(names)


def git_env(root: Path | str | None, *args: str) -> dict | None:
    """The environment for a git invocation: None (inherit, unchanged) for
    anything that is not a read; for a read, no optional locks, no
    `GIT_EXTERNAL_DIFF`, and `_GIT_READ_CONFIG` plus every filter driver
    blanked for the verbs that would run one."""
    verb = _git_verb(args)
    if verb not in _GIT_READS:
        return None
    env = {k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    pairs = list(_GIT_READ_CONFIG)
    if verb in _GIT_FILTERS:
        for name in _filter_drivers(root):
            pairs += [(f"filter.{name}.clean", ""), (f"filter.{name}.smudge", ""),
                      (f"filter.{name}.process", ""), (f"filter.{name}.required", "false")]
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        count = 0
    for key, value in pairs:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def git_run(root: Path | str | None, *args: str, timeout: float = 30,
            cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    """`git [-C root] <args>` with `git_argv` and `git_env`, captured as
    text. Raises what `subprocess.run` raises; callers keep their own
    error handling."""
    return subprocess.run(git_argv(root, *args), capture_output=True, text=True,
                          timeout=timeout, env=git_env(root if root is not None else cwd, *args),
                          cwd=None if cwd is None else str(cwd))


# ── the roots ──────────────────────────────────────────────────────────

_GIT_ROOTS: dict[str, Path | None] = {}


def git_root(path: Path) -> Path | None:
    """The top level of the git work tree holding `path`, or None.

    Cached, because `state_repo()` and `problems()` both ask it and the
    answer only changes when somebody runs `git init`."""
    key = str(path)
    if key in _GIT_ROOTS:
        return _GIT_ROOTS[key]
    found: Path | None = None
    try:
        result = git_run(path, "rev-parse", "--show-toplevel", timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            found = Path(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        found = None
    _GIT_ROOTS[key] = found
    return found


def _derived_app_root() -> Path:
    """Where the application is when nobody said: the git toplevel seen
    from the manager's own directory. Vendored installs drop their .git at
    clone time, so this finds the host repo; self-hosted it finds this
    one. No git at all falls back to the vendored-layout assumption."""
    found = git_root(MANAGER)
    return found if found is not None else TM_ROOT.parent


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _real(path: Path) -> Path:
    """`path` with every link in it followed, as far as it exists.

    Non-strict on purpose: a file bench is about to *create* is not there
    yet, and the links in its parents are what decide where creating it
    would land."""
    try:
        return path.resolve()
    except OSError:
        return path


def escapes(path: Path, root: Path) -> Path | None:
    """Where `path` really is, when that is not inside `root`; else None.

    `_inside` compares the paths as written, which a symlink turns into a
    lie: `<work>/tasks`, one stage directory inside it, or a single card
    can point anywhere — at the application, most usefully — and a write
    through the link lands there rather than where every containment
    check above swore it would.

    Both ends are resolved, so a root reached *through* a link (a
    symlinked workspace, /tmp on a mac) is still inside itself. The rule
    is containment, not a ban on links.
    """
    real, base = _real(path), _real(root)
    return None if real == base or _inside(real, base) else real


# The two settings that make a layout external. Read as given — a path
# bench cannot use is refused by `problems()`, never silently replaced.
APP_SETTING = setting("BOARD_APP_ROOT", "").strip()
WORK_SETTING = setting("BOARD_WORK_ROOT", "").strip()
EXTERNAL = bool(APP_SETTING or WORK_SETTING)

# The application repository. Code, branches, worktrees and PRs — and
# nothing of the board's own bookkeeping.
REPO = (Path(APP_SETTING).expanduser() if APP_SETTING else _derived_app_root())

# The work state: tasks/, plans/, reference/.
WORK = Path(WORK_SETTING).expanduser() if WORK_SETTING else TM_ROOT
TASKS = WORK / "tasks"                             # stage directories only


def work_root() -> Path:
    """The directory holding tasks/, plans/ and reference/ — defined as
    the parent of the stage directories, so nothing can hold a work root
    that disagrees with where the task files are actually read from."""
    return TASKS.parent


def escapes_work(path: Path) -> Path | None:
    """Where `path` really is, when a write to it would land outside the
    work root — the board's own state, and the only place it writes."""
    return escapes(path, work_root())


def escapes_tasks(path: Path) -> Path | None:
    """The same question for one card: inside tasks/, and tasks/ inside
    the work root. Both halves, because a tasks/ that was itself a link
    out would make every card under it look safely 'contained' in
    somewhere bench promised not to write."""
    return escapes(path, TASKS) or escapes_work(path)


def work_writables() -> list[tuple[Path, str]]:
    """Every directory under the work root that gets written into, with
    the name a refusal calls it.

    tasks/, its stages and archive/ are the board's own writes (and the
    skeleton and task template the installer lays down). plans/ is where
    the install points an agent's plan files, so a link out of it writes
    by the board's own hand just as surely. reference/ is only ever read
    and served, so a link out of *it* is a reading decision its owner is
    entitled to make.
    """
    found = [(TASKS, "tasks/")]
    found += [(TASKS / slug, f"tasks/{slug}/") for slug, _ in STAGES]
    found.append((TASKS / "archive", "tasks/archive/"))
    found.append((work_root() / "plans", "plans/"))
    found.append((TASKS / "task-template.md", "tasks/task-template.md"))
    return found


_WORKTREES_SETTING = setting("BOARD_WORKTREES", "").strip()


def _worktrees() -> Path:
    """One isolated checkout per running work agent. An absolute setting
    is taken as given; a relative one resolves against the application.

    Unset, the default follows the layout: `.worktrees` inside a vendored
    application (which bench's own .gitignore covers), and `worktrees`
    beside an external one — because an external install must not leave
    directories in a checkout nobody asked it to touch."""
    if _WORKTREES_SETTING:
        candidate = Path(_WORKTREES_SETTING).expanduser()
        return candidate if candidate.is_absolute() else REPO / candidate
    return REPO.parent / "worktrees" if EXTERNAL else REPO / ".worktrees"


WORKTREES = _worktrees()


def state_repo() -> Path | None:
    """The repository the board's own commits go to — moves, claims, the
    `**PR:**` line, appended reports, everything `taskfiles` writes.

    Vendored, tasks/ lives inside the application checkout and the two
    identities really are one repository: returning it is today's
    behaviour, unchanged. Externally the state has a repository of its
    own, found from where the tasks actually live. `None` means the work
    root is in no repository — bench commits nothing rather than
    committing to the application.

    "Externally" is `EXTERNAL`, not "a work root was named": with only
    `BOARD_APP_ROOT` set the work root is still the tool installation, and
    its repository — whatever the tool sits in, or none — is the one the
    tickets live in. Reading an unset `BOARD_WORK_ROOT` as "vendored" there
    handed the board's bookkeeping to the application (task 59).
    """
    if not EXTERNAL:
        return REPO
    return git_root(work_root())


# The project this board serves. Every board looks alike in a tab bar, so
# the title leads with this name — the application directory's, unless the
# setting says otherwise (checkouts all called "app" need the override).
PROJECT = setting("BOARD_TITLE", "").strip() or REPO.name

# Pinned so the board is always at the same bookmarkable URL. Sits in the
# ephemeral-safe 10000–30000 range, clear of the other local dev servers.
PORT = int(setting("BOARD_PORT", "26071"))

# Which agent adapter runs headless jobs. Resolution ladder: local wins.
# The *startup* reading; a launch asks `agent_choice()`, which reads the
# agent settings fresh (task 77).
ADAPTER = setting("BOARD_AGENT_ADAPTER", "claude")

# Command prefixes headless agents may run in a worktree (the project's
# test/check commands) — neutral, comma-separated; each adapter renders
# them in its own permission-rule syntax. The universal git/gh grants are
# the adapter's own knowledge; this list is the project's half. Empty by
# default (task 78): bench's own runner is not another project's, so a
# board nobody configured grants none and says so.
AGENT_COMMANDS = setting("BOARD_AGENT_COMMANDS", "")


def agent_commands() -> list[str]:
    """The prefixes as an adapter reads them: comma-separated, blanks
    dropped — so whitespace, or a lone comma, is exactly nothing
    configured. The adapters split the same string in their own standalone
    copies of `split_commands()`; this is core's, and it is what the board
    asks so the page and the launch can never disagree about *empty*."""
    return [part.strip() for part in AGENT_COMMANDS.split(",") if part.strip()]


# Model per launch intent — an opaque vendor-native name core passes to the
# adapter untranslated (what names mean anything is vendor knowledge). Empty
# = inherit the vendor's own default, exactly today's behaviour. A per-intent
# setting beats the general one; review covers PR reviews and relevance
# checks (they share the review intent).
AGENT_MODEL = setting("BOARD_AGENT_MODEL", "")
AGENT_MODELS = {
    "work": setting("BOARD_AGENT_MODEL_WORK", ""),
    "act-pr": setting("BOARD_AGENT_MODEL_ACT_PR", ""),
    "review": setting("BOARD_AGENT_MODEL_REVIEW", ""),
    # a card refinement (task 65): reads and proposes a brief, nothing else
    "refine": setting("BOARD_AGENT_MODEL_REFINE", ""),
}


def agent_model(mode: str) -> str:
    """The model one launch intent rides — '' means inherit."""
    return agent_choice(mode)["model"]["value"]


# ── which harness and model a launch rides (task 77) ───────────────────
#
# The agent settings are the one group of `.env` keys that is *not* frozen
# at import. Settings → Agents writes them, and a change has to reach the
# next launch without a restart — so the launch, the preflight, the
# doctor and the page all ask `agent_choice()`, which reads them fresh.
# Everything else in this module is still the startup snapshot, on
# purpose: this behaviour change is limited to agent configuration.
#
# Precedence is the one `setting()` has always applied, per key:
#
#   1. an in-process assignment to `ADAPTER`, `AGENT_MODEL` or
#      `AGENT_MODELS` (an embedding or a test patching this module) —
#      the module attributes are what callers have always set, and code
#      that sets one means it;
#   2. the process environment the board was started with — **locked**:
#      it beats the file, and changing it means changing where it is
#      exported and restarting;
#   3. `manager/local/.env` as it is on disk now;
#   4. the documented default.
#
# A per-purpose value that is empty inherits the default for its kind,
# and an empty default model means the harness's own default — the
# variable is then simply absent from the launch.

# The three purposes Settings → Agents names, and the setting suffix each
# reads. `refine` keeps the default harness (it has no harness override
# of its own) but does have a model setting, as it did before.
AGENT_PURPOSES = ("work", "act-pr", "review")
_SUFFIX = {"work": "_WORK", "act-pr": "_ACT_PR", "review": "_REVIEW",
           "refine": "_REFINE"}
HARNESS_KEY = "BOARD_AGENT_ADAPTER"
MODEL_KEY = "BOARD_AGENT_MODEL"
HARNESS_KEYS = (HARNESS_KEY,) + tuple(HARNESS_KEY + _SUFFIX[p]
                                      for p in AGENT_PURPOSES)
MODEL_KEYS = (MODEL_KEY,) + tuple(MODEL_KEY + _SUFFIX[p]
                                  for p in (*AGENT_PURPOSES, "refine"))
AGENT_KEYS = HARNESS_KEYS + MODEL_KEYS
DEFAULT_HARNESS = "claude"

# Which agent keys the process environment set when this board started.
# Snapshotted, not re-read: an export made after the start is not in this
# process's environment anyway, and "locked" must mean the same thing for
# the whole life of the board.
_ENV_LOCKED = {key: os.environ[key] for key in AGENT_KEYS if key in os.environ}
_BOOT = {"ADAPTER": ADAPTER, "AGENT_MODEL": AGENT_MODEL,
         "AGENT_MODELS": dict(AGENT_MODELS)}


def env_file_now() -> dict[str, str]:
    """`manager/local/.env` as it is on disk now, parsed exactly the way
    `_load_env` parses it at import."""
    values: dict[str, str] = {}
    try:
        raw = (LOCAL / ".env").read_text(encoding="utf-8")
    except OSError:
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _patched(key: str) -> str | None:
    """An in-process assignment to the module attribute behind `key`, or
    None when the attribute still holds what the module started with."""
    if key == HARNESS_KEY and ADAPTER != _BOOT["ADAPTER"]:
        return ADAPTER
    if key == MODEL_KEY and AGENT_MODEL != _BOOT["AGENT_MODEL"]:
        return AGENT_MODEL
    for purpose, suffix in _SUFFIX.items():
        if key == MODEL_KEY + suffix:
            now = (AGENT_MODELS or {}).get(purpose, "")
            if now != _BOOT["AGENT_MODELS"].get(purpose, ""):
                return now
    return None


def agent_setting(key: str, file_values: dict[str, str] | None = None) -> dict:
    """One agent key's effective value and where it came from.

    `source` is one of `environment` (locked), `process`, `local-file` or
    `default`; `value` is stripped, and '' is "nothing set here"."""
    if key not in AGENT_KEYS:
        raise KeyError(key)
    patched = _patched(key)
    if patched is not None:
        return {"key": key, "value": str(patched).strip(),
                "source": "process", "locked": True}
    if key in _ENV_LOCKED:
        return {"key": key, "value": _ENV_LOCKED[key].strip(),
                "source": "environment", "locked": True}
    values = env_file_now() if file_values is None else file_values
    if key in values:
        return {"key": key, "value": values[key].strip(),
                "source": "local-file", "locked": False}
    default = DEFAULT_HARNESS if key == HARNESS_KEY else ""
    return {"key": key, "value": default, "source": "default",
            "locked": False}


def agent_choice(intent: str, file_values: dict[str, str] | None = None) -> dict:
    """The harness and model one launch intent rides, with provenance.

    The one resolver: `profiles.resolve` reads it for the manifest, so the
    preflight, the doctor, the Settings page and `_launch` agree by
    construction. `file_values` lets a caller resolve a *draft* `.env`
    (Settings' preview) through exactly the same rules.
    """
    values = env_file_now() if file_values is None else file_values
    suffix = _SUFFIX.get(intent)
    default_h = agent_setting(HARNESS_KEY, values)
    harness = dict(default_h, inherited=True, setting=HARNESS_KEY)
    if intent in AGENT_PURPOSES:
        own = agent_setting(HARNESS_KEY + suffix, values)
        if own["value"]:
            harness = dict(own, inherited=False, setting=own["key"])
    if not harness["value"]:
        harness = dict(harness, value=DEFAULT_HARNESS)
    default_m = agent_setting(MODEL_KEY, values)
    model = dict(default_m, inherited=True, setting=MODEL_KEY)
    if suffix:
        own = agent_setting(MODEL_KEY + suffix, values)
        if own["value"]:
            model = dict(own, inherited=False, setting=own["key"])
    model["harnessDefault"] = not model["value"]
    return {"intent": intent, "harness": harness, "model": model}


def adapters_installed() -> list[dict]:
    """Every adapter directory with a `run`, local before core, one entry
    per name — the local one when both exist, which is the ladder
    `adapter_dir` climbs."""
    found: dict[str, dict] = {}
    for base, where in ((LOCAL / "adapters", "local"), (CORE / "adapters", "core")):
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in found:
                continue
            if (entry / "run").is_file():
                found[entry.name] = {"name": entry.name, "where": where,
                                     "path": str(entry),
                                     "shadows": (where == "local" and
                                                 (CORE / "adapters" / entry.name
                                                  / "run").is_file())}
    return list(found.values())


# GitHub plumbing: the gh CLI (stub-able for tests) and the git remote PRs
# go to. Empty remote = auto-detect the first remote; no remote = no PRs.
GH_BIN = setting("BOARD_GH_BIN", "gh")
GIT_REMOTE = setting("BOARD_GIT_REMOTE", "")
PR_POLL_INTERVAL = float(setting("BOARD_PR_POLL_INTERVAL", "60"))

# The application's base branch — what a fresh task branch is cut from,
# what a PR is opened against and what a merge lands on.
APP_BASE = setting("BOARD_APP_BASE", "main").strip() or "main"

# The work state's own remote and branch, resolved independently of the
# application's: a code PR must never target the work-state remote, and a
# card moving columns must never commit on the application's base.
STATE_GIT_REMOTE = setting("BOARD_STATE_GIT_REMOTE", "")
STATE_BASE = setting("BOARD_STATE_BASE", "main").strip() or "main"


def _remotes_of(path: Path | None) -> list[str]:
    """What `git remote` prints for a checkout, in its order. Asked on
    demand and never at import — config is imported by everything, hooks
    included, and none of them should pay for a subprocess to load it."""
    if path is None:
        return []
    try:
        result = git_run(path, "remote", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return result.stdout.split() if result.returncode == 0 else []


def _resolve_remote(configured: str, names: list[str]) -> str | None:
    """A configured name is used as named, never quietly swapped for
    another; otherwise the checkout's first remote, else None."""
    if configured:
        return configured
    return names[0] if names else None


# ── the application's git, and the board's own ─────────────────────────
#
# Two git identities live in this design, and keeping them apart is what
# lets a card name a branch without the board guessing which repository it
# meant. **Work-state git** is `state_repo()`: the checkout holding
# `tasks/`, where a move commits itself and where sync fast-forwards the
# state's base. **Application git** is where the code a card is implemented
# against lives — the repository a task branch is cut in, a worktree is put
# on, and a PR is opened from, which is `REPO`.
#
# Vendored, the two are one checkout; externally they are not, and the
# functions below are the named door rather than a path each module
# reinvents: a branch-selection question asks `app_repo()` and
# `app_remote()`, a task-state question asks `state_repo()` and
# `state_git_remote()`. Filesystem layout must not change branch-selection
# semantics, and an application question must never be answered by the
# state's git.


def app_repo() -> Path:
    """The checkout a task's code is written in. One answer, asked by every
    branch/worktree/PR decision so none of them can pick a different one —
    and never the work state's repository, even where they coincide."""
    return REPO


def app_remote() -> str | None:
    """The remote the application's branches are published to and fetched
    from: `BOARD_GIT_REMOTE` when set — as named, never quietly swapped —
    else the application checkout's first remote, else None.

    The board's own bookkeeping asks `state_git_remote()` instead, so the
    two halves can never publish to each other's remote."""
    return git_remote()


def git_remotes() -> list[str]:
    """The application checkout's remotes."""
    return _remotes_of(REPO)


def git_remote() -> str | None:
    """The remote the application's work goes to: `BOARD_GIT_REMOTE` when
    set — as named, never quietly swapped for another — else the first
    remote the application checkout has, else None.

    PRs ask here, so code never goes to two different places. Whether the
    name resolves to a remote that actually exists is the caller's
    business: `git_remotes()` answers that."""
    return _resolve_remote(GIT_REMOTE, git_remotes())


def app_branch_remote() -> str | None:
    """Which remote a brand-new task branch is cut from.

    Deliberately narrower than `git_remote()`. With nothing configured,
    **only `origin` counts** — the behaviour bench has always had, and
    the reason it is right: branching is on the launch path, so a board
    must never start fetching a remote nobody named (a `backup` or a
    `fork` listed first would be fetched on every launch, and an
    unreachable one would cost every launch its timeout). A checkout
    whose remotes are called something else simply branches from local
    HEAD, as before.

    An explicit `BOARD_GIT_REMOTE`, or an external layout — where the
    application is named rather than inferred, so its remote is a thing
    the operator has actually thought about — selects the resolved remote
    instead.
    """
    if GIT_REMOTE:
        return GIT_REMOTE
    if EXTERNAL:
        return git_remote()
    return "origin"


def state_git_remotes() -> list[str]:
    """The work-state checkout's remotes — a different list entirely."""
    return _remotes_of(state_repo())


def _one_repository() -> bool:
    """Are the two identities the same repository? Vendored they are, and
    then one remote setting governs both — a project that configured
    `BOARD_GIT_REMOTE` before any of this existed must not find its board
    syncing somewhere else."""
    state = state_repo()
    try:
        return state is not None and state.resolve() == REPO.resolve()
    except OSError:
        return False


def state_git_remote() -> str | None:
    """The remote the board's state syncs through: `BOARD_STATE_GIT_REMOTE`
    when set, `BOARD_GIT_REMOTE` when the two repositories are one, else
    the state checkout's first remote, else None. Sync asks here and
    nowhere else, so bookkeeping can never be published to the
    application's remote."""
    configured = STATE_GIT_REMOTE
    if not configured and _one_repository():
        configured = GIT_REMOTE
    return _resolve_remote(configured, state_git_remotes())


def state_remote_setting() -> str:
    """Which setting a reader should go and fix when sync cannot resolve
    its remote — named rather than guessed, since two settings can supply
    the answer and only one of them applies to any given layout."""
    if STATE_GIT_REMOTE or (EXTERNAL and not _one_repository()):
        return "BOARD_STATE_GIT_REMOTE"
    return "BOARD_GIT_REMOTE"


# Where a card's branch is merged into the application's base. Unset keeps
# today's answer exactly (see `app_merge_on_origin`).
APP_MERGE = setting("BOARD_APP_MERGE", "").strip().lower()

# `owner/name` is only a thing for a real git host; a local path remote
# (every test's bare upstream) is not one, and must not be turned into a
# bogus `--repo` argument.
_SLUG_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.\-]*://[^/]+/|[^/\s]+@[^/:\s]+:)"
    r"([^/\s]+/[^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)


def app_merge_on_origin() -> bool:
    """Whether a card's branch is merged into the application's base *on
    origin* (`gh pr merge`) rather than in this checkout.

    Unset, this is today's answer and nothing more: with the board's own
    state in the application's repository, `BOARD_SYNC` means local base
    advances only by fast-forward, so the merge has to be made on origin
    for replicas to converge. With the state in a repository of its own,
    `BOARD_SYNC` says nothing whatsoever about the application — reading
    it as though it did is exactly the conflation this split exists to
    end — so the default there is the local merge, and
    `BOARD_APP_MERGE=origin` is how an operator asks for the other.
    """
    if APP_MERGE in ("origin", "remote", "gh"):
        return True
    if APP_MERGE in ("local", "checkout"):
        return False
    return SYNC and _one_repository()


def gh_available() -> bool:
    """Is the GitHub CLI on the path? Asked by `github` before it reaches
    for it and by `plans` when it answers whether a run can actually
    deliver a PR — one reading, so the preflight and the attempt can never
    disagree about what this machine can do."""
    return shutil.which(GH_BIN) is not None


def gh_repo() -> str | None:
    """`owner/name` for the application's configured remote, so every gh
    command can be told which repository it acts on.

    gh infers that from the checkout's remotes, and with more than one the
    inference can land on a different repository than the one this board
    rides — a `fork`, a `backup`, an upstream somebody added. Naming it
    removes the guess. None when the url is not a recognisable
    owner/name on a host (a local bare remote, as every test uses), and
    then gh is left to infer exactly as it did before.
    """
    rname = git_remote()
    if not rname:
        return None
    try:
        result = git_run(REPO, "remote", "get-url", rname, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = _SLUG_RE.match(result.stdout.strip())
    return match.group(1) if match else None


# ── branches and worktrees of the application ──────────────────────────

TASK_BRANCH_PREFIX = "task/"
PHASE_BRANCH_PREFIX = "phase/"


def _app_git(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return git_run(REPO, *args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def branch_for(stem: str) -> str:
    """The branch a card's work lives on: a phase's own `phase/<stem>`
    when that branch exists, `task/<stem>` otherwise.

    One answer in one place. A driver or a local command that assumed
    `task/<stem>` attached to a branch a phase card never had."""
    phase = PHASE_BRANCH_PREFIX + stem
    if _app_git("rev-parse", "--verify", "--quiet", phase).returncode == 0:
        return phase
    return TASK_BRANCH_PREFIX + stem


def _common_git_dir(path: Path) -> Path | None:
    """The git directory a checkout shares with its main repository —
    the same value for a repository and all of its worktrees, which is
    what makes it the identity question."""
    result = _git_at(path, "rev-parse", "--git-common-dir")
    if result is None:
        return None
    found = Path(result)
    if not found.is_absolute():
        found = path / found
    try:
        return found.resolve()
    except OSError:
        return None


def _git_at(path: Path, *args: str) -> str | None:
    try:
        result = git_run(path, *args, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def dirty_files(worktree: Path | str) -> list[str]:
    """Uncommitted work in a checkout — modified *and* untracked, because
    both are things a person can only lose once, and `git status
    --porcelain` lists both.

    An empty list is the only safe answer for a removal. A status that
    cannot be read answers "something", which keeps the worktree: the
    cost of a false positive is a directory left on disk, and the cost
    of a false negative is deleted work.
    """
    try:
        result = git_run(worktree, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"<could not read the worktree's status: {exc}>"]
    if result.returncode != 0:
        return ["<could not read the worktree's status>"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def worktree_mismatch(path: Path) -> str | None:
    """Why an existing directory is not this application's worktree, or
    None when it is one (or is not there at all).

    A matching branch name is not enough, and that was the hole: a
    worktree path is `<worktrees>/<task stem>`, and the same stem can
    already be occupied by a checkout of an entirely different repository
    — a hand-made clone, a symlink, a collision between two boards
    sharing a worktrees directory. Running an agent, a driver or a local
    command in it would act on the wrong code and commit to the wrong
    history. git answers the question properly: two checkouts of one
    repository share a git directory.

    The question asked is always "is this a worktree of this
    application", and each answer says which way it failed — a directory
    that is no checkout at all, or a checkout of something else — because
    the two need different things done about them. Every caller shows the
    sentence as it stands: a preflight blocker, an agent's refusal, a
    driver's and a local command's all read the same.
    """
    if not path.exists():
        return None
    theirs = _common_git_dir(path)
    if theirs is None:
        return (f"{path} already exists and is not a git worktree of this "
                f"repository — it is not a git checkout at all. Move it "
                f"aside, or point BOARD_WORKTREES at a directory bench owns")
    ours = _common_git_dir(REPO)
    if ours is not None and theirs != ours:
        return (f"{path} is a checkout of another repository (its git "
                f"directory is {theirs}), not of the application at {REPO} "
                f"— move it aside")
    return None


def tasks_relative_to_app() -> str | None:
    """Where the tasks sit inside the application, when they do — the one
    honest way to name them in a PR body. None when they live outside it,
    which is when naming an application-relative path would be fiction."""
    try:
        return TASKS.resolve().relative_to(REPO.resolve()).as_posix()
    except (ValueError, OSError):
        return None


# How long a work-agent launch waits for `git fetch <remote> <base>` before
# branching from local HEAD instead. Launching must never be blocked by
# network weather; this bounds the whole delay.
FETCH_TIMEOUT = float(setting("BOARD_FETCH_TIMEOUT", "10"))

# How long bench waits to bring the commit a pull request's head names into
# this checkout, before a PR review is refused for want of the revision it
# is supposed to be reviewing (`reviews.py`). Longer than FETCH_TIMEOUT on
# purpose: that one bounds a *fallback* — launching branches from HEAD
# instead — and this one bounds the only thing that will do, so a slow
# remote is waited for rather than silently reviewed around.
REVIEW_FETCH_TIMEOUT = float(setting("BOARD_REVIEW_FETCH_TIMEOUT", "120"))

# Team mode's second half: the work state's base branch is the shared
# truth and every board a converging replica — board commits push as they
# are made, a beat pulls what other boards published. Off by default; on,
# it implies COMMIT_MOVES below, because a move that never commits has
# nothing to publish.
SYNC = flag("BOARD_SYNC")
SYNC_INTERVAL = float(setting("BOARD_SYNC_INTERVAL", "30"))

# Team mode's first half: a board-made move claims the card (writing
# **Assignee:** from git's own user.name) and commits itself, so ownership
# and stage travel with the file to every clone. Off by default — a
# single-player board moves cards exactly as it always did, and committing
# tasks/ stays a hand job.
COMMIT_MOVES = flag("BOARD_COMMIT_MOVES") or SYNC

# How often the phase runner takes a pass: recompute what each running
# phase needs and do the one next thing. A beat, not an agent — it costs a
# handful of git commands and nothing at all when no phase is running.
PHASE_INTERVAL = float(setting("BOARD_PHASE_INTERVAL", "30"))

WATCH_INTERVAL = float(setting("BOARD_WATCH_INTERVAL", "2"))
EVENTS_CAP = int(setting("BOARD_EVENTS_CAP", "800"))
BOARD_EVENTS_CAP = int(setting("BOARD_HISTORY_CAP", "300"))

# Agent-reported progress (progress.py). A run that has said nothing about
# its work for PROGRESS_REMIND seconds is reminded once, at its next tool
# boundary, by the event bridge's reply — never by an interruption and
# never by a model call of the board's own. 0 turns the reminder off.
# Reports closer together than PROGRESS_MIN_GAP seconds are coalesced into
# the previous history entry rather than each becoming a line of their own.
PROGRESS_REMIND = max(0.0, float(setting("BOARD_PROGRESS_REMIND", "600")))
PROGRESS_MIN_GAP = max(0.0, float(setting("BOARD_PROGRESS_MIN_GAP", "20")))


def child_env() -> dict[str, str]:
    """Environment for adapter/driver/command child processes: the real
    environment with local/.env settings folded in (process env still
    wins), plus the **resolved** layout — so a driver, a local command or
    an adapter reads where things are instead of re-deriving it from a
    path it cannot see."""
    env = dict(_ENV)
    env.update({
        "BOARD_APP_ROOT": str(REPO),
        "BOARD_WORK_ROOT": str(work_root()),
        "BOARD_TASKS_DIR": str(TASKS),
        "BOARD_WORKTREES_DIR": str(WORKTREES),
    })
    return env


def layout() -> dict:
    """The resolved roots, for the board to show and for a report to
    print. Cheap on purpose — no remotes, no git probes beyond the one
    `state_repo()` caches."""
    state = state_repo()
    return {
        "tool": str(TM_ROOT),
        "app": str(REPO),
        "work": str(work_root()),
        "tasks": str(TASKS),
        "worktrees": str(WORKTREES),
        "stateRepo": str(state) if state else None,
        "external": EXTERNAL,
        "appBase": APP_BASE,
        "stateBase": STATE_BASE,
    }


# ── validation ─────────────────────────────────────────────────────────


def _joined(paths: list[Path]) -> str:
    names = [str(p) for p in paths]
    if len(names) > 1:
        return " and ".join([", ".join(names[:-1]), names[-1]])
    return names[0] if names else ""


_WORK_REMEDY = ("set BOARD_WORK_ROOT to a directory of its own, outside the "
                "application (`install.py --work <dir>`)")


def _work_root_named() -> str:
    """The work root as a refusal should name it: as configured, or — with
    `BOARD_WORK_ROOT` unset — as the default it actually is, so nobody is
    sent looking for a setting they never made."""
    if WORK_SETTING or not EXTERNAL:
        return f"the work root {work_root()}"
    return (f"the work root {work_root()} (the tool installation, the "
            f"default while BOARD_WORK_ROOT is unset)")


def problems(require_tasks: bool = True,
             require_work: bool = True) -> list[str]:
    """Every reason the configured layout cannot be served, in plain
    words. Empty means it can.

    The two switches are install.py's, and both exist for the same
    reason: it asks this *before* creating the work root and its
    skeleton, because neither may be created under a layout the rest of
    these checks would refuse. Everything that does not need the
    directory to be there yet — the shape of the paths, the application's
    repository, the overlaps, and the links out of the work root — is
    asked either way.

    This fails closed. Each automatic fallback available here leads to the
    wrong repository — the tool's own, or the application's — so a
    configured-but-broken external layout is a refusal rather than a
    warning. `board.py` prints these and exits; `install.py` prints them
    and declines to wire.
    """
    found: list[str] = []

    for key, raw in (("BOARD_APP_ROOT", APP_SETTING),
                     ("BOARD_WORK_ROOT", WORK_SETTING)):
        if not raw:
            continue
        path = Path(raw).expanduser()
        optional = key == "BOARD_WORK_ROOT" and not require_work
        if not path.is_absolute():
            found.append(f"{key}={raw} is not an absolute path — bench "
                         f"resolves nothing against a working directory it "
                         f"cannot see, so give the whole path")
        elif not path.exists():
            if not optional:
                found.append(f"{key}={raw} does not exist")
        elif not path.is_dir():
            found.append(f"{key}={raw} is not a directory")
    if found:
        return found

    if APP_SETTING:
        root = git_root(REPO)
        if root is None:
            found.append(f"BOARD_APP_ROOT={REPO} is not a git repository — "
                         f"bench manages an application by its git checkout")
        elif root.resolve() != REPO.resolve():
            found.append(f"BOARD_APP_ROOT={REPO} is not the top level of its "
                         f"git repository ({root} is) — name the root")
        if found:
            return found

    if EXTERNAL:
        app, work = REPO.resolve(), work_root().resolve()
        # With BOARD_WORK_ROOT unset the work root is the tool installation
        # — the documented default — so a refusal names *that*, and says
        # which setting moves it, rather than blaming a setting nobody made.
        where = _work_root_named()
        if app == work:
            if WORK_SETTING:
                found.append("BOARD_APP_ROOT and BOARD_WORK_ROOT name the same "
                             "directory — the work state is kept outside the "
                             "application it describes")
            else:
                found.append(f"BOARD_APP_ROOT={REPO} is the tool installation "
                             f"itself, which is also {where} — the work state "
                             f"is kept outside the application it describes; "
                             f"{_WORK_REMEDY}")
        elif _inside(work, app):
            found.append(f"{where} is inside the application {REPO} — task "
                         f"state kept there lands in the application's own "
                         f"files, which is the one thing an external layout "
                         f"exists to prevent"
                         + ("" if WORK_SETTING else f"; {_WORK_REMEDY}, or "
                            f"unset BOARD_APP_ROOT for a vendored install"))
        elif _inside(app, work):
            found.append(f"the application {REPO} is inside {where} — the two "
                         f"are separate repositories"
                         + ("" if WORK_SETTING else f"; {_WORK_REMEDY}"))
        tool = git_root(TM_ROOT)
        if tool is not None and tool.resolve() == app:
            found.append(f"BOARD_APP_ROOT={REPO} is the repository bench "
                         f"itself sits in ({TM_ROOT}) — bench does not manage "
                         f"the repository serving the board; name the "
                         f"application you want managed")
        state = state_repo()
        if not found and state is not None and state.resolve() == app:
            found.append(f"{where} resolves into the application's own "
                         f"repository ({REPO}) — the board's state needs a "
                         f"repository of its own"
                         + ("" if WORK_SETTING else f"; {_WORK_REMEDY}"))
        if found:
            return found

    # Where the paths stop telling the truth. Everything above compares
    # them as written, and `<work>/tasks` — or one stage inside it, or
    # archive/, or plans/ — can be a link into the application, at which
    # point every board-made write lands in the checkout this layout
    # exists to leave alone while every check above still passes.
    #
    # Asked of whatever is actually on disk, under every layout, and
    # whether or not the stage directories are required yet: install.py
    # asks with `require_tasks=False` precisely because it is about to
    # create the skeleton and copy the task template, and both would go
    # through the link before anything had refused.
    #
    # A link whose target does not exist yet counts. `exists()` follows
    # links, so a dangling one reads as nothing being there — which is
    # exactly the destination the installer's copy judges as free, and
    # then writes through into the application. Presence is the link's,
    # not its target's.
    for path, name in work_writables():
        if not (path.exists() or path.is_symlink()):
            continue
        real = escapes_work(path)
        if real is not None:
            found.append(f"{name} under the work root {work_root()} is a link "
                         f"to {real}, outside it — the board's state is "
                         f"written nowhere else, least of all into the "
                         f"application")
    if found:
        return found

    if COMMIT_MOVES or SYNC:
        if state_repo() is None:
            which = "BOARD_SYNC" if SYNC else "BOARD_COMMIT_MOVES"
            # Externally with no BOARD_WORK_ROOT, `git init` in the tool
            # would make the installation the ticket repository; naming a
            # work state of its own is the likelier intent, so it comes first.
            remedy = ("run `git init` there" if WORK_SETTING or not EXTERNAL
                      else f"{_WORK_REMEDY} and run `git init` in it, or run "
                           f"`git init` in the tool installation")
            found.append(
                f"{which} is on but {_work_root_named()} is in no git "
                f"repository, so there is nowhere for the board's own commits "
                f"to go — {remedy}, or turn {which} off in "
                f"manager/local/.env. Bench will not commit the board's "
                f"bookkeeping to the application instead")

    app = REPO.resolve()
    work = work_root().resolve()
    try:
        trees = WORKTREES.resolve()
    except OSError:
        trees = WORKTREES
    if trees == app:
        found.append(f"BOARD_WORKTREES={WORKTREES} is the application root "
                     f"itself — a worktree is a checkout beside it, not it")
    elif EXTERNAL and _inside(trees, work):
        # Only external: vendored, the work root sits inside the
        # application (or is it, self-hosted) and `.worktrees` has always
        # lived there, gitignored. The hazard this catches is a work-state
        # repository that would start tracking application checkouts.
        found.append(f"BOARD_WORKTREES={WORKTREES} is inside the work root "
                     f"{work_root()} — application checkouts do not belong "
                     f"in the board's own state")

    if not require_tasks:
        return found
    if not TASKS.is_dir():
        found.append(f"there is no tasks/ directory under the work root "
                     f"{work_root()} — expected {TASKS}; "
                     f"`python3 {TM_ROOT / 'install.py'}` creates the work "
                     f"state there")
    else:
        missing = sorted(slug for slug in STAGE_DIRS
                         if not (TASKS / slug).is_dir())
        if missing:
            found.append(f"{TASKS} is missing its stage directories "
                         f"({', '.join(missing)})")
    return found


# ── what the agents are told ───────────────────────────────────────────


def guidance_outside_app() -> list[Path]:
    """Workflow briefs a headless run cannot find by reading its own
    checkout: the work root's and the tool project's, when they exist and
    are not inside the application already."""
    seen: set[Path] = set()
    found: list[Path] = []
    for candidate in (work_root() / "AGENTS.md", LOCAL / "AGENTS.md"):
        try:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or _inside(resolved, REPO.resolve()):
            continue
        seen.add(resolved)
        found.append(candidate)
    return found


def prompt_guidance(checks: bool = True) -> str:
    """What a prompt tells a run to read. Vendored — the application's own
    AGENTS.md is the brief — this is the sentence the prompts always
    carried, byte for byte. Externally the brief may live outside the
    checkout entirely, and then it is named by absolute path, because an
    external install adds no bench file to the application for the agent
    to stumble on instead.

    `checks=False` is the read-only flavour: a review is told to read the
    brief, not to run anything until it passes.
    """
    tail = " — run whatever checks it names until they pass." if checks else "."
    outside = guidance_outside_app()
    if (REPO / "AGENTS.md").is_file():
        text = ("Read AGENTS.md at the repo root first and follow it, "
                "including its definition of done" + tail)
        if outside:
            text += (f" The board's own workflow brief lives outside this "
                     f"checkout — read {_joined(outside)}, and follow it too.")
        return text
    if outside:
        return (f"This checkout has no AGENTS.md. The board's workflow brief "
                f"lives outside it — read {_joined(outside)} first and follow "
                f"it, including its definition of done{tail}")
    return ("There is no AGENTS.md in this checkout and the board has no "
            "workflow brief configured, so follow the task above and the "
            "conventions of the code you are changing.")


def prompt_fields(filename: str, stage: str) -> dict[str, str]:
    """The layout-dependent placeholders every prompt is filled with, so
    one answer reaches all four templates: where the ticket actually is,
    and what workflow brief to read."""
    return {"task_path": str(TASKS / stage / filename),
            "guidance": prompt_guidance(),
            "guidance_read_only": prompt_guidance(checks=False)}


def prompt(name: str) -> str:
    """Prompt templates: core ships defaults, local/prompts/ overrides win.
    Read fresh on every launch so edits apply without a restart."""
    override = LOCAL / "prompts" / name
    if override.is_file():
        return override.read_text(encoding="utf-8")
    return (CORE / "prompts" / name).read_text(encoding="utf-8")


def checks() -> list[dict]:
    """Definition-of-done checks for the Focus panel: core ships a default
    (core/checks); a local/checks replaces it wholesale, like prompts. Each
    line is `<label>: <command regex>`; invalid regexes are skipped. The
    agent adapter reads the same file to classify commands (the claude
    adapter's emit.py is standalone, so the parser is mirrored there), and
    the browser matches with the served patterns — keep them in the regex
    dialect Python and JavaScript share. Read fresh on every request."""
    for base in (LOCAL, CORE):
        path = base / "checks"
        if not path.is_file():
            continue
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            label, sep, pattern = line.partition(":")
            label, pattern = label.strip(), pattern.strip()
            if not sep or not label or not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                continue
            entries.append({"label": label, "pattern": pattern})
        return entries
    return []


ADAPTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def adapter_dir(name: str | None = None) -> Path | None:
    """An agent adapter's directory — local overrides core. No name means
    the default harness as it resolves now. A name that is not a plain
    directory name resolves to nothing rather than to a path."""
    if name is None:
        name = agent_choice("default")["harness"]["value"]
    if not ADAPTER_NAME.match(name or ""):
        return None
    for base in (LOCAL / "adapters", CORE / "adapters"):
        candidate = base / name
        if (candidate / "run").is_file():
            return candidate
    return None


# Where the execution-profile document is, when the project wrote one: a
# machine-local JSON file resolved per launch intent (`profiles.py`).
# Empty falls back to manager/local/profiles.json, and no document at all
# resolves on BOARD_AGENT_COMMANDS plus the adapter's built-in grants —
# exactly today's behaviour. An absolute value is used as given; a
# relative one resolves against the tool install, never against a working
# directory bench cannot see.
AGENT_PROFILES = setting("BOARD_AGENT_PROFILES", "").strip()

# ── verification (`verify.py`) ─────────────────────────────────────────
#
# How long bench lets one named check — and one preparation step — run
# before it kills it and records a timeout. A timeout is not a test
# failure and is never recorded as one; these only bound the wait.
VERIFY_TIMEOUT = float(setting("BOARD_VERIFY_TIMEOUT", "1800"))
PREPARE_TIMEOUT = float(setting("BOARD_VERIFY_PREPARE_TIMEOUT", "900"))

# How many verifications this board runs at once. A check is somebody
# else's test suite: two in parallel is a sensible machine's worth, and a
# request over the cap waits its turn rather than being refused.
VERIFY_PARALLEL = max(1, int(setting("BOARD_VERIFY_PARALLEL", "2")))

# Opt-in: a card a person moves into review/ through this board asks for
# its missing required checks, exactly as one **Run required checks**
# click would (`required.on_review`). Off by default, and off means a
# move into review requests nothing at all. Read once at start-up, like
# its neighbours, so turning it on or off is an edit to
# manager/local/.env and a restart — never something a page or a pull
# changes under a running board. It grants nothing: the request rides a
# person's work reading and the policy's own checks.
VERIFY_ON_REVIEW = flag("BOARD_VERIFY_ON_REVIEW")

# A cap on one verification's log, so a check that prints forever cannot
# fill the disk. The evidence names the cap when it bites.
VERIFY_LOG_CAP = int(setting("BOARD_VERIFY_LOG_CAP", str(4 * 1024 * 1024)))

# Environment variables a check never inherits, by name. Publication
# credentials are the point: a test suite has no business being able to
# push, open a PR or publish a package, and the one reliable way to
# enforce that is for the token not to be in its environment. Extend with
# BOARD_VERIFY_ENV_DENY (comma-separated names, added to these).
CREDENTIAL_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_API_TOKEN",
    "GH_CONFIG_DIR", "GIT_ASKPASS", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
    "NPM_TOKEN", "NODE_AUTH_TOKEN", "TWINE_PASSWORD", "TWINE_USERNAME",
    "PYPI_TOKEN", "CARGO_REGISTRY_TOKEN", "DOCKER_AUTH_CONFIG",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
)


def verify_env_deny() -> list[str]:
    """Every environment variable name a check runs without: the built-in
    credential list plus whatever the project added."""
    extra = [part.strip() for part in
             setting("BOARD_VERIFY_ENV_DENY", "").split(",") if part.strip()]
    return sorted({*CREDENTIAL_ENV, *extra})


def driver_path() -> Path | None:
    """The project's app driver, if it has one."""
    candidate = LOCAL / "driver" / "start"
    return candidate if candidate.is_file() else None


def commands() -> list[dict]:
    """Project-owned commands: executables in local/commands/, surfaced as
    chips on cards and run against the task's worktree. A `# help:` line
    near the top becomes the chip's tooltip."""
    directory = LOCAL / "commands"
    found = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith(".") or not os.access(path, os.X_OK):
                continue
            help_text = ""
            try:
                for line in path.read_text(encoding="utf-8").splitlines()[:8]:
                    if line.startswith("# help:"):
                        help_text = line[len("# help:"):].strip()
                        break
            except OSError:
                pass
            found.append({"name": path.name, "help": help_text})
    return found
