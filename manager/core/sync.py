"""Boards converge through origin/main: push what this board commits, pull
what the other boards published.

Gated on `BOARD_SYNC` (which implies `BOARD_COMMIT_MOVES` — a move that
never commits has nothing to publish). Off, nothing here runs: no fetch,
no push, no thread.

The shape of it:

- **push** is event-driven. `taskfiles` fires `state.task_committed` after
  a board-made move commits; the hook installed here publishes it. A
  rejected push means another board got there first, so the whole converge
  runs and pushes again.
- **pull** is a beat: fetch, then integrate. Purely behind → fast-forward.
  Diverged → the board's own commits are rebased on top, never merged
  past; a rebase that conflicts on a task file means the local move lost
  the race, and it is dropped with a toast naming who took the card.
- **the piggyback guard** stands in front of every push: each local-ahead
  commit on main must be `board: `-prefixed. A human's unpushed work is
  never published as a side effect of a card moving.
- **offline** is not an error. The first unreachable fetch says so once,
  the rest are silent, commits queue on local main and the next reachable
  fetch catches up.

Git is the lock server and main the linearizer — that is the whole
concurrency control. Nothing here reacts to synced state beyond narrating
it: replicas render, they do not act.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import config
import state
from taskfiles import NUMBER_RE

REMOTE = "origin"                     # one remote, one branch — by design
BRANCH = "main"
UPSTREAM = f"{REMOTE}/{BRANCH}"
BOARD_COMMIT = "board: "              # the prefix taskfiles messages its own commits with
PUSH_TIMEOUT = 120
REBASE_TIMEOUT = 120
ARRIVED_TTL = 60.0                    # the watcher polls every 2s; this is generous

_LOCK = threading.Lock()              # one git operation on the checkout at a time
_ARRIVED_LOCK = threading.Lock()
ARRIVED: dict[str, tuple[str, float]] = {}   # filename -> (author, ts) from the last pull
_NOTES: dict[str, tuple[str, str]] = {}      # key -> (summary, level) already narrated


def _git(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """Never raises: a timeout or a missing binary is just a failed run."""
    try:
        return subprocess.run(["git", "-C", str(config.REPO), *args],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 128, "", "timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


# ── narration ──────────────────────────────────────────────────────────
# Every condition here repeats on every beat, so each one is narrated once
# and then held: the ticker says it, the header chip keeps saying it.


def status() -> dict:
    """What the header shows: ok while converging, otherwise the reason."""
    if not config.SYNC:
        return {"enabled": False, "state": "off", "detail": ""}
    for level in ("offline", "stalled"):
        for summary, note_level in _NOTES.values():
            if note_level == level:
                return {"enabled": True, "state": level, "detail": summary}
    return {"enabled": True, "state": "ok", "detail": ""}


def _note(key: str, summary: str, level: str = "stalled") -> None:
    if _NOTES.get(key) == (summary, level):
        return          # same condition as last time: said once is enough
    _NOTES[key] = (summary, level)
    state.record_board_event({"kind": "sync", "actor": "sync", "summary": summary})
    state.broadcast({"type": "board"})


def _clear(key: str, recovery: str = "") -> None:
    if _NOTES.pop(key, None) is None:
        return
    if recovery:
        state.record_board_event({"kind": "sync", "actor": "sync", "summary": recovery})
    state.broadcast({"type": "board"})


# ── the checkout ───────────────────────────────────────────────────────


def _origin_present() -> bool:
    return REMOTE in _git("remote").stdout.split()


def _head() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _on_main() -> bool:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == BRANCH


def _clean() -> bool:
    """Tracked files only: an untracked scratch file is nobody's business,
    but a modified one is what a fast-forward would run over."""
    return not _git("status", "--porcelain", "--untracked-files=no").stdout.strip()


def _count(rng: str) -> int:
    out = _git("rev-list", "--count", rng).stdout.strip()
    return int(out) if out.isdigit() else 0


def _tasks_prefix() -> str:
    try:
        return config.TASKS.resolve().relative_to(config.REPO.resolve()).as_posix() + "/"
    except ValueError:
        return "tasks/"


def _fetch() -> bool:
    result = _git("fetch", REMOTE, BRANCH, timeout=config.FETCH_TIMEOUT)
    if result.returncode != 0:
        if "couldn't find remote ref" in (result.stderr or "").lower():
            _note("no-branch", f"sync stalled: {REMOTE} has no {BRANCH} branch — "
                               f"sync rides {UPSTREAM} and nothing else")
            return False
        _note("offline",
              f"sync is behind: {REMOTE} is unreachable — this board keeps "
              f"working locally and catches up when it returns", "offline")
        return False
    _clear("no-branch")
    _clear("offline", f"sync caught up: {REMOTE} is reachable again")
    return True


# ── publishing ─────────────────────────────────────────────────────────


def _ahead() -> list[str]:
    """`<short sha> <subject>` for every commit local main has and
    origin/main does not — newest first."""
    out = _git("log", "--format=%h %s", f"{UPSTREAM}..{BRANCH}").stdout
    return [line for line in out.splitlines() if line.strip()]


def _stray(commits: list[str]) -> str:
    """The piggyback hazard: pushing publishes *every* local-ahead commit,
    so one that the board did not make is a human's private work and stops
    the push. Oldest first — that is the one to deal with."""
    for line in reversed(commits):
        subject = line.split(" ", 1)[1] if " " in line else ""
        if not subject.startswith(BOARD_COMMIT):
            return line
    return ""


def _publish() -> str:
    """Push local main if — and only if — everything on it is the board's.

    ok | nothing | stray | not-on-main | retry | offline | stalled
    """
    if not _on_main():
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "a detached HEAD"
        _note("branch", f"sync paused: this checkout is on '{branch}', not {BRANCH} — "
                        f"board commits are not landing where sync publishes from")
        return "not-on-main"
    _clear("branch")
    if _git("rev-parse", "--verify", "--quiet", UPSTREAM).returncode != 0:
        return "retry"          # never fetched: converge first, then publish
    commits = _ahead()
    stray = _stray(commits)
    if stray:
        _note("stray", f"not pushing: {stray} is not a board commit — sync will not "
                       f"publish it for you. Push main yourself, or move that commit "
                       f"off main, and sync resumes")
        return "stray"
    _clear("stray")     # nothing stray left to refuse, however that happened
    if not commits:
        return "nothing"

    result = _git("push", REMOTE, f"{BRANCH}:{BRANCH}", timeout=PUSH_TIMEOUT)
    if result.returncode == 0:
        _clear("push")
        _clear("offline", f"sync caught up: {REMOTE} is reachable again")
        state.record_board_event({
            "kind": "sync", "actor": "sync",
            "summary": f"pushed {len(commits)} board commit"
                       f"{'s' if len(commits) > 1 else ''} to {UPSTREAM}"})
        return "ok"
    stderr = (result.stderr or result.stdout).strip()
    if _rejected(stderr):
        return "retry"
    if _unreachable(stderr):
        _note("offline",
              f"sync is behind: {REMOTE} is unreachable — this board keeps "
              f"working locally and catches up when it returns", "offline")
        return "offline"
    detail = stderr.splitlines()[-1][:140] if stderr else "git said nothing"
    _note("push", f"sync could not push to {UPSTREAM}: {detail}")
    return "stalled"


def _rejected(stderr: str) -> bool:
    text = stderr.lower()
    return "non-fast-forward" in text or "fetch first" in text or "! [rejected]" in text


def _unreachable(stderr: str) -> bool:
    text = stderr.lower()
    return any(mark in text for mark in (
        "could not read from remote", "could not resolve", "unable to access",
        "does not appear to be a git repository", "connection", "timed out",
        "no such file or directory", "permission denied"))


# ── integrating what arrived ───────────────────────────────────────────


def _conflicted() -> list[str]:
    out = _git("diff", "--name-only", "--diff-filter=U").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _is_task_file(path: str) -> bool:
    return path.startswith(_tasks_prefix()) and path.endswith(".md")


def _author_of(filename: str, rev: str) -> str:
    """Who wrote the newest commit touching this card in `rev` — a range for
    what a pull brought, a ref for what origin already holds."""
    return _git("log", "-1", "--format=%an", rev, "--",
                f"{_tasks_prefix()}*/{filename}").stdout.strip()


def _number(filename: str) -> str:
    match = NUMBER_RE.match(filename)
    return match.group(1) if match else filename[:-3] if filename.endswith(".md") else filename


def _lost(filename: str) -> None:
    """The local move lost the race. Say who took the card — the file itself
    reverts to origin's version when the rebase drops our commit."""
    who = _author_of(filename, UPSTREAM) or "someone else"
    message = f"{_number(filename)} claimed by {who} — your move was undone"
    state.record_board_event({"kind": "sync", "actor": "sync", "file": filename,
                              "summary": message})
    state.broadcast({"type": "toast", "message": message, "error": True})
    state.broadcast({"type": "board"})


def _replay() -> str:
    """Rebase this board's commits onto origin/main. Conflicts on a task
    file are resolved by dropping our commit: origin is the linearizer, and
    a card someone else moved first is theirs. Anything conflicting outside
    tasks/ is a real collision — abort and wait for a human.

    ok | dirty | stalled
    """
    if not _clean():
        _note("dirty", "sync paused: main has uncommitted changes — commit or stash "
                       "them and sync resumes (code work belongs in a worktree)")
        return "dirty"
    _clear("dirty")

    result = _git("rebase", UPSTREAM, timeout=REBASE_TIMEOUT)
    for _ in range(50):                    # bounded: one round per replayed commit
        if result.returncode == 0:
            _clear("replay")
            return "ok"
        conflicted = _conflicted()
        if not conflicted or not all(_is_task_file(p) for p in conflicted):
            _git("rebase", "--abort")
            detail = ", ".join(conflicted[:3]) or (result.stderr or result.stdout).strip()[-140:]
            _note("replay", f"sync stalled: replaying this board's commits onto {UPSTREAM} "
                            f"collides outside tasks/ ({detail}) — a human has to settle it")
            return "stalled"
        for name in dict.fromkeys(Path(p).name for p in conflicted):
            _lost(name)
        result = _git("rebase", "--skip", timeout=REBASE_TIMEOUT)
    _git("rebase", "--abort")
    _note("replay", f"sync stalled: replaying onto {UPSTREAM} did not settle — "
                    f"a human has to settle it")
    return "stalled"


def _integrate() -> str:
    """Bring local main to origin/main without ever merging past a
    divergence.

    up-to-date | pulled | not-on-main | dirty | diverged | stalled
    """
    if _count(f"{BRANCH}..{UPSTREAM}") == 0:
        return "up-to-date"
    if not _on_main():
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "a detached HEAD"
        _note("branch", f"sync paused: this checkout is on '{branch}', not {BRANCH} — "
                        f"switch back and the board catches up with {UPSTREAM}")
        return "not-on-main"
    _clear("branch")

    # Diverged. The board's own bookkeeping can be replayed on top of what
    # arrived — that is how a lost race resolves. A human's commit cannot,
    # and the guard that refuses to push it refuses to rebase it too.
    commits = _ahead()
    stray = _stray(commits)
    if stray:
        _note("diverged", f"sync stalled: main and {UPSTREAM} have diverged and "
                          f"{stray} is not a board commit — pull or rebase it by "
                          f"hand, and this board starts converging again")
        return "diverged"
    _clear("diverged")
    if commits:
        outcome = _replay()
        return "pulled" if outcome == "ok" else outcome

    if not _clean():
        _note("dirty", "sync paused: main has uncommitted changes — commit or stash "
                       "them and sync resumes (code work belongs in a worktree)")
        return "dirty"
    _clear("dirty")
    result = _git("merge", "--ff-only", UPSTREAM, timeout=REBASE_TIMEOUT)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        _note("merge", f"sync stalled: fast-forwarding to {UPSTREAM} failed "
                       f"({detail[-1][:140] if detail else 'no detail'})")
        return "stalled"
    _clear("merge")
    return "pulled"


def _record_arrivals(before: str) -> None:
    """Attribute what the pull brought: each task file it touched is filed
    under the name of whoever committed it, for the watcher to use instead
    of "disk" when the move surfaces on the next poll."""
    head = _head()
    if not before or not head or head == before:
        return
    rng = f"{before}..{head}"
    changed = _git("diff", "--name-only", rng, "--", _tasks_prefix()).stdout.splitlines()
    names = sorted({Path(p).name for p in changed if p.strip().endswith(".md")})
    if not names:
        return
    now = time.time()
    authors = set()
    with _ARRIVED_LOCK:
        for name in names:
            who = _author_of(name, rng)
            if who:
                ARRIVED[name] = (who, now)
                authors.add(who)
        for name in [n for n, (_, ts) in ARRIVED.items() if now - ts > ARRIVED_TTL]:
            ARRIVED.pop(name, None)
    count = _count(rng)
    state.record_board_event({
        "kind": "sync", "actor": "sync",
        "summary": f"pulled {count} commit{'s' if count != 1 else ''} from {UPSTREAM}"
                   + (f" ({', '.join(sorted(authors))})" if authors else "")})
    state.broadcast({"type": "board"})


def arrived_actor(filename: str) -> str:
    """Who moved this card, if a pull just brought it. Consumed once — the
    watcher asks exactly when it notices the move."""
    with _ARRIVED_LOCK:
        entry = ARRIVED.pop(filename, None)
    if not entry:
        return ""
    who, ts = entry
    return who if time.time() - ts <= ARRIVED_TTL else ""


# ── the two entry points ───────────────────────────────────────────────


def _converge() -> str:
    """One full beat: fetch, integrate what arrived, publish what is ours."""
    if not _origin_present():
        return "no-origin"
    if not _fetch():
        return "offline"
    before = _head()
    outcome = _integrate()
    _record_arrivals(before)
    if outcome in ("up-to-date", "pulled"):
        published = _publish()
        if published in ("stray", "offline", "stalled", "not-on-main"):
            return published
    return outcome


def push_now() -> str:
    """A board commit just landed — publish it. The fast path skips the
    fetch; a rejection means another board pushed first, and then the full
    converge (fetch, replay, push) runs."""
    if not config.SYNC:
        return "off"
    with _LOCK:
        if not _origin_present():
            return "no-origin"
        outcome = _publish()
        if outcome != "retry":
            return outcome
        return _converge()


def pull_now() -> str:
    """The beat. Also the offline catch-up: a fetch that works again is
    followed by the push that could not happen while origin was gone."""
    if not config.SYNC:
        return "off"
    with _LOCK:
        return _converge()


def on_commit(filename: str) -> None:
    """The `state.task_committed` hook: publish off the caller's thread, so
    a card move never waits on the network."""
    if not config.SYNC:
        return
    threading.Thread(target=push_now, name="sync-push", daemon=True).start()


def install() -> None:
    """Wire the push hook. Called once at startup, only with the gate on."""
    if on_commit not in state.COMMIT_HOOKS:
        state.COMMIT_HOOKS.append(on_commit)


def beat(interval: float | None = None) -> None:
    interval = config.SYNC_INTERVAL if interval is None else interval
    while True:
        try:
            pull_now()
        except Exception as exc:              # noqa: BLE001 — the beat outlives a bad cycle
            state.record_board_event({"kind": "sync", "actor": "sync",
                                      "summary": f"sync cycle failed: {str(exc)[:140]}"})
        time.sleep(interval)
