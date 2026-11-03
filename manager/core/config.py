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
Externally it is the repository holding the work root — and `None`, a
work root in no repository at all, is a work root bench will not commit
to rather than a reason to reach for the application's history.
"""

from __future__ import annotations

import os
import re
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
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10)
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
    """
    if not WORK_SETTING:
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
ADAPTER = setting("BOARD_AGENT_ADAPTER", "claude")

# Command prefixes headless agents may run in a worktree (the project's
# test/check commands) — neutral, comma-separated; each adapter renders
# them in its own permission-rule syntax. The universal git/gh grants are
# the adapter's own knowledge; this list is the project's half.
AGENT_COMMANDS = setting("BOARD_AGENT_COMMANDS", "python3 -m unittest")


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
}


def agent_model(mode: str) -> str:
    """The model one launch intent rides — '' means inherit."""
    return AGENT_MODELS.get(mode, "") or AGENT_MODEL


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
        result = subprocess.run(["git", "-C", str(path), "remote"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return result.stdout.split() if result.returncode == 0 else []


def _resolve_remote(configured: str, names: list[str]) -> str | None:
    """A configured name is used as named, never quietly swapped for
    another; otherwise the checkout's first remote, else None."""
    if configured:
        return configured
    return names[0] if names else None


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
        result = subprocess.run(
            ["git", "-C", str(REPO), "remote", "get-url", rname],
            capture_output=True, text=True, timeout=10)
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
        return subprocess.run(["git", "-C", str(REPO), *args],
                              capture_output=True, text=True, timeout=timeout)
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
        result = subprocess.run(["git", "-C", str(path), *args],
                                capture_output=True, text=True, timeout=10)
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
        result = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30)
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
    """
    if not path.exists():
        return None
    theirs = _common_git_dir(path)
    if theirs is None:
        return (f"{path} already exists and is not a git checkout — move it "
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
        if app == work:
            found.append("BOARD_APP_ROOT and BOARD_WORK_ROOT name the same "
                         "directory — the work state is kept outside the "
                         "application it describes")
        elif _inside(work, app):
            found.append(f"the work root {work_root()} is inside the "
                         f"application {REPO} — task state kept there lands "
                         f"in the application's own files, which is the one "
                         f"thing an external layout exists to prevent")
        elif _inside(app, work):
            found.append(f"the application {REPO} is inside the work root "
                         f"{work_root()} — the two are separate repositories")
        tool = git_root(TM_ROOT)
        if tool is not None and tool.resolve() == app:
            found.append(f"BOARD_APP_ROOT={REPO} is bench's own repository — "
                         f"bench does not manage itself; name the application "
                         f"you want managed")
        state = state_repo()
        if not found and state is not None and state.resolve() == app:
            found.append(f"the work root resolves into the application's own "
                         f"repository ({REPO}) — the board's state needs a "
                         f"repository of its own")
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
            found.append(
                f"{which} is on but the work root {work_root()} is in no git "
                f"repository, so there is nowhere for the board's own commits "
                f"to go — run `git init` there, or turn {which} off in "
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
                     f"{work_root()} — expected {TASKS}")
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


def adapter_dir() -> Path | None:
    """The configured agent adapter's directory — local overrides core."""
    for base in (LOCAL / "adapters", CORE / "adapters"):
        candidate = base / ADAPTER
        if (candidate / "run").is_file():
            return candidate
    return None


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
