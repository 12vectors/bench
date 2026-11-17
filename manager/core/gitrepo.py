"""Local git on the two repositories (task 114, WP06).

Two git identities live in bench, and this module is where an operation
names which one it means rather than reaching for a path:

- **application git** — `app()`: where a card's branch is cut, a worktree
  is put on it, and a merge lands. Always `config.app_repo()`.
- **work-state git** — `state()`: where the board's own bookkeeping
  commits and syncs. `config.state_repo()`, which may be None.

Whether the two are one repository is answered by
`config.app_shares_state_repository()` (task 62) and by nothing else;
`shares_state()` asks it rather than comparing paths a second way.

Every git call here goes through `config.git_run`, so a read stays a read
(`gitproc`'s hardening) and a test patching `config.git_run` still sees
it. A mutation — fetch, worktree add or remove, branch delete — is the
authorised side effect it always was, run exactly as written.

What this module does **not** own: which branch a card uses (`plans`),
what a merge or a publication means (`github`, later `completion`), or
anything about a hosting provider. It sits on L2 and imports `config`
only.

The branch-point question lives here because two modules above it ask
it — `plans` (resolving a launch) and `phases` (cutting and freshening a
phase branch) — and neither may reach into `agents` for it.

**What `verify.py` still does itself.** Until WP12 the verify train keeps
its own `_git` and calls `config.branch_for`, `config.dirty_files` and
`config.worktree_mismatch`; those bodies therefore stay in `config` (a
delegation from `config` up to this module would be an upward import),
and this module offers them under the same names as its door for every
other caller.

The full inventory of git wrappers — each one's root, timeout, exception
and message contract — is in `ARCHITECTURE.md`, "Git: who runs it".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import config


# ── the two identities ─────────────────────────────────────────────────


def app() -> Path:
    """The application checkout: code, task branches, worktrees, merges."""
    return config.app_repo()


def state() -> Path | None:
    """The work state's repository, or None when the work root is in no
    repository — then nothing is committed, rather than committed to the
    application."""
    return config.state_repo()


def shares_state() -> bool:
    """Is application git the same repository as work-state git? Card 62's
    one answer, asked rather than reimplemented."""
    return config.app_shares_state_repository()


# ── running git ────────────────────────────────────────────────────────


def git(repo: Path | str, *args: str, timeout: float | None = 30,
        timed_out: str | None = None) -> subprocess.CompletedProcess:
    """`git -C <repo> <args>`, never raising.

    A failure to run at all is returncode 128 with the exception's text on
    stderr — which is the contract `config._app_git` had and the gates,
    delivery and required-check readers depend on. `timed_out`, when
    given, replaces the text for a timeout: `plans` and `phases` have
    always said exactly "timed out", and keep saying it. `timeout=None`
    waits as long as git takes, which is what the launch path's worktree
    operations always did."""
    try:
        return config.git_run(repo, *args, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args, 128, "", timed_out if timed_out is not None else str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def app_git(*args: str, timeout: float | None = 30) -> subprocess.CompletedProcess:
    """`git` in the application checkout, never raising (128 and the
    exception's text). The public door for what was `config._app_git`."""
    return git(app(), *args, timeout=timeout)


# ── reads ──────────────────────────────────────────────────────────────


def branch_exists(branch: str) -> bool:
    """Does `branch` resolve in the application checkout? Asked by name,
    as the drive, the local commands and acting on a PR always asked."""
    return git(app(), "rev-parse", "--verify", "--quiet", branch,
               timeout=None).returncode == 0


def branch_for(stem: str) -> str:
    """The branch a card's work lives on (`config.branch_for`)."""
    return config.branch_for(stem)


def dirty(worktree: Path | str) -> list[str]:
    """Uncommitted work in a checkout (`config.dirty_files`): an empty list
    is the only answer that licenses a removal."""
    return config.dirty_files(worktree)


def worktree_mismatch(path: Path) -> str | None:
    """Why `path` is not a worktree of the application, or None
    (`config.worktree_mismatch`)."""
    return config.worktree_mismatch(path)


# ── mutations, each named for what it does ─────────────────────────────


def fetch_branch(remote: str, branch: str, *, timeout: float) -> bool:
    """Bring `remote`'s `branch` into `refs/remotes/<remote>/<branch>` of
    the application, bounded. True when git said it worked. Touches no
    local branch and no checkout."""
    done = git(app(), "fetch", remote,
               f"refs/heads/{branch}:refs/remotes/{remote}/{branch}",
               timeout=timeout)
    return done.returncode == 0


def worktree_add(path: Path | str, branch: str, *, new: bool = False,
                 start: str | None = None, timeout: float | None = None,
                 timed_out: str | None = None) -> subprocess.CompletedProcess:
    """Put a worktree of the application at `path`.

    `new=False` attaches it to the existing `branch`; `new=True` cuts
    `branch` there — from `start` (without tracking it) when given, else
    from the application's HEAD. The argument lists are exactly the ones
    the launch, the phase runner, the drive and the local commands always
    ran. `timed_out` is `git`'s: the fixed text a timeout reads as, so the
    phase runner keeps its "timed out" rather than an argument list naming
    this machine's paths."""
    if not new:
        return git(app(), "worktree", "add", str(path), branch,
                   timeout=timeout, timed_out=timed_out)
    if start:
        return git(app(), "worktree", "add", "--no-track", "-b", branch,
                   str(path), start, timeout=timeout, timed_out=timed_out)
    return git(app(), "worktree", "add", "-b", branch, str(path),
               timeout=timeout, timed_out=timed_out)


def worktree_remove(path: Path | str, *,
                    timeout: float | None = None) -> subprocess.CompletedProcess:
    """Remove a worktree **without** `--force`: git refuses one holding
    work, and that refusal is the point."""
    return git(app(), "worktree", "remove", str(path), timeout=timeout)


def branch_delete(branch: str, *, force: bool = True,
                  timeout: float | None = None) -> subprocess.CompletedProcess:
    """Delete a local application branch (`-D` when `force`, else `-d`)."""
    return git(app(), "branch", "-D" if force else "-d", branch, timeout=timeout)


def ensure_worktree(stem: str, branch: str, *, check_branch: bool = True,
                    timeout: float | None = None,
                    timed_out: str | None = None) -> dict:
    """The card's worktree, recreated from its branch when it was cleaned up.

    One body for what the drive, the local commands, acting on a PR and
    the phase runner each wrote out for themselves. It answers rather than
    raising, so every caller keeps its own words and its own exception:

    - `problem: None` — `path` is a worktree of this application
      (`created` says whether this call made it);
    - `"mismatch"` — something else sits there; `detail` is
      `worktree_mismatch`'s sentence, shown as it stands;
    - `"no-branch"` — no worktree and no `branch` to make one from (asked
      only when `check_branch`; the phase runner lets git refuse instead);
    - `"add-failed"` — git would not add it; `detail` is its stderr
      (`timed_out`, when given, if the add timed out — as for `git`).
    """
    path = config.WORKTREES / stem
    found = {"path": path, "created": False, "problem": None, "detail": ""}
    mismatch = worktree_mismatch(path)
    if mismatch:
        return {**found, "problem": "mismatch", "detail": mismatch}
    if path.exists():
        return found
    if check_branch and not branch_exists(branch):
        return {**found, "problem": "no-branch"}
    config.WORKTREES.mkdir(parents=True, exist_ok=True)
    result = worktree_add(path, branch, timeout=timeout, timed_out=timed_out)
    if result.returncode != 0:
        return {**found, "problem": "add-failed", "detail": result.stderr.strip()}
    return {**found, "created": True}


# ── where a brand-new branch starts ────────────────────────────────────


def fresh_branch_point() -> tuple[str | None, str | None]:
    """Where a brand-new task branch starts when nothing on the card says
    otherwise: the newest base branch that exists, in the **application**
    repository. Moved here from `agents` (task 114) so `plans` and
    `phases` can ask it without reaching up.

    Which remote and which branch are `config`'s answers, resolved
    independently of the work state's (task 56) — and the remote one is
    `app_branch_remote()`, not `git_remote()`: with nothing configured
    only `origin` counts, because branching is on the launch path and a
    board must not start fetching a remote nobody named. An explicit
    setting or an external layout widens it.

    With a remote, fetch its base (bounded by FETCH_TIMEOUT) and branch
    from `<remote>/<base>` — never touching the checkout itself, the
    fetched ref is only the branch point. No remote, a failed fetch or a
    timeout all mean branching from HEAD, because launching must never be
    blocked by network weather.

    Returns (start point, ticker note); (None, …) means HEAD. The note is
    non-None whenever the branch point deserves a mention — the remote
    base ahead of this checkout, or a fetch that had to be skipped.

    The fetch is the only side effect, and it writes one remote-tracking
    ref in this checkout. Nothing is pushed, nothing is checked out.

    This is the *default* path: a card that names its own base is
    resolved by `plans`, which refuses rather than falling back.
    """
    repo = app()

    def _git(*args: str) -> subprocess.CompletedProcess:
        return config.git_run(repo, *args, timeout=None)

    rname = config.app_branch_remote()
    if not rname or rname not in _git("remote").stdout.split():
        return None, None
    base = config.APP_BASE
    upstream = f"{rname}/{base}"
    try:
        fetched = config.git_run(repo, "fetch", rname, base,
                                 timeout=config.FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"fetch of {upstream} timed out; branched from local HEAD"
    if fetched.returncode != 0 or \
            _git("rev-parse", "--verify", "--quiet", upstream).returncode != 0:
        return None, f"fetch of {upstream} failed; branched from local HEAD"
    # Counted against HEAD, not the base: HEAD is the fallback base, so this
    # is exactly what launching would have missed — accurate even when the
    # board checkout sits on another branch.
    ahead = _git("rev-list", "--count", f"HEAD..{upstream}").stdout.strip()
    if ahead.isdigit() and int(ahead) > 0:
        return upstream, (f"branched from {upstream}, "
                          f"{ahead} ahead of this checkout")
    return upstream, None
