"""Archive all: clear the Done column in one confirmed request (task 187).

Archiving one card is `taskfiles.archive_task`, behind `POST /api/archive`.
This is the same act over a set, and it adds nothing to what archiving
*is*: every card goes out through `archive_task` itself — the same
refusals, the same `**Status:** Archived` rewrite, the same `board: <n> →
archived` commit and the same event-driven push. What this module owns is
only what a set needs on top:

- **Which cards.** `reading()` is the server's answer to "what would
  Archive all take": every card in `done/` that is not a phase card, not a
  member of a phase still open (its phase card stands for it on the
  Board), not worked by an agent and not being merged and cleaned up —
  with every excluded card named and why. The page shows that answer, its
  exact count, before anything is written.
- **The set is frozen.** A request names the files the person confirmed.
  A card completed after that is not in the request and is not touched; a
  card in it is read again immediately before it is archived, and one
  that has since moved, become protected or gone is skipped with its
  reason rather than archived anyway.
- **One request, once.** A request carries an identity. The same identity
  again answers with the batch it already started (a double click, a
  resend after a lost reply), and a different set under the same identity
  is refused. One batch runs at a time. Each card's own state makes the
  rest idempotent: a card already in `archive/` is reported as already
  archived, never moved twice and never given a second history line.
- **Partial is said as partial.** There is no transaction across cards;
  each card is archived or not on its own, and the batch reports
  archived, skipped and failed apart. `outstanding` is what a retry may
  ask for: the cards whose archive failed and the archived cards whose
  commit failed (a retry commits those through `taskfiles.commit_edit`,
  which records the move under its own journal line — it does not move
  them again). Skipped cards are not outstanding: they changed, and the
  person decides again from a fresh reading.
- **Local is not shared.** Archiving is local to this board's work state;
  with `BOARD_SYNC` on, the batch asks sync to publish once at the end and
  records what sync said, so "archived here" and "published" are two
  facts.
- **Interrupted is recorded.** Each batch's record is written under
  `manager/local/state/archive/bulk/` as it goes. A batch a restart cut
  short reads `interrupted`, with the cards it had not reached as
  outstanding; asking again archives only those still in `done/`.

Nothing here merges, cleans up a branch or worktree, launches an agent,
or touches a card outside `done/`.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import agents
import config
import phases
import records
import state
import sync
import taskfiles

STAGE = "done"
MAX_FILES = 500
KEEP_BATCHES = 20
REQUEST_RE = re.compile(r"[A-Za-z0-9._:-]{1,80}")

_LOCK = threading.Lock()          # guards _BATCHES and the one-at-a-time rule
_BATCHES: dict[str, dict] = {}    # request -> batch record (this process)
_RUNNING: str | None = None       # the request of the batch in flight


def _dir() -> Path:
    return config.STATE / "archive" / "bulk"


def _path(request: str) -> Path:
    return _dir() / f"{request}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── which cards ───────────────────────────────────────────────────────────

def _excluded(task: dict, by_file: dict[str, dict]) -> tuple[str, str] | None:
    """Why a card in done/ is not Archive all's to take, or None.

    Each reason is a guard the single archive has, or one the Board's own
    drawing implies: a phase card is archived on its own, deliberately; a
    member of a phase still open is the phase's (the Board does not even
    draw it); a card an agent works on, or one being merged and cleaned
    up, is in the middle of something."""
    if task.get("isPhase"):
        return ("phase-card", "a phase card — archive it on its own")
    phase = task.get("phase")
    if phase and not task.get("phaseDrift"):
        owner = by_file.get(phase.get("file"))
        if owner is not None and owner.get("stage") != STAGE:
            number = owner.get("number") or owner["file"]
            return ("in-phase", f"part of phase #{number}, which is still open")
    if agents.working_on({task["file"]}):
        return ("agent-working", "an agent is working on it")
    with state.LOCK:
        completing = task["file"] in state.COMPLETING
    if completing:
        return ("completing", "it is being merged and cleaned up")
    return None


def _board() -> tuple[list[dict], dict[str, dict]]:
    stages = taskfiles.collect()["stages"]
    tasks = [task for stage in stages for task in stage["tasks"]]
    return tasks, {task["file"]: task for task in tasks}


def _row(task: dict) -> dict:
    return {"file": task["file"], "number": task.get("number") or "",
            "title": task.get("title") or task["file"]}


def reading() -> dict:
    """What Archive all would take now, and what it would leave. A read:
    nothing is written, moved or launched."""
    tasks, by_file = _board()
    eligible, excluded = [], []
    for task in tasks:
        if task.get("stage") != STAGE:
            continue
        why = _excluded(task, by_file)
        if why is None:
            eligible.append(_row(task))
        else:
            excluded.append({**_row(task), "code": why[0], "reason": why[1]})
    with _LOCK:
        running = _RUNNING
    return {"stage": STAGE, "eligible": eligible, "excluded": excluded,
            "count": len(eligible), "running": running,
            "sync": sync.status()}


# ── one batch ─────────────────────────────────────────────────────────────

def _check_request(request, files, retry_of) -> tuple[str, list[str], str | None]:
    if not isinstance(request, str) or not REQUEST_RE.fullmatch(request):
        raise ValueError("an Archive all request needs a request identity")
    if not isinstance(files, list) or not files:
        raise ValueError("an Archive all request names the cards it confirmed")
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} cards in one Archive all")
    clean: list[str] = []
    for name in files:
        if (not isinstance(name, str) or Path(name).name != name
                or not name.endswith(".md")):
            raise ValueError(f"{name!r} is not a card's file name")
        if name not in clean:
            clean.append(name)
    if retry_of is not None and (not isinstance(retry_of, str)
                                 or not REQUEST_RE.fullmatch(retry_of)):
        raise ValueError("retryOf must name an earlier Archive all request")
    return request, clean, retry_of


def _persist(batch: dict) -> None:
    try:
        records.write_json(_path(batch["request"]), batch)
    except OSError:
        pass        # the batch still answers from memory; a restart loses it
    else:
        _prune()


def _prune() -> None:
    try:
        found = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for old in found[:-KEEP_BATCHES]:
        try:
            old.unlink()
        except OSError:
            pass


def _load(request: str) -> dict | None:
    batch = records.read_json(_path(request), expect=dict)
    if not batch or batch.get("request") != request:
        return None
    if batch.get("status") == "running":
        # this process did not start it, so whatever it had not reached
        # was never done: a restart interrupted it
        batch["status"] = "interrupted"
        reached = {item["file"] for item in batch.get("items", [])}
        batch["notReached"] = [f for f in batch.get("files", []) if f not in reached]
        _summarise(batch)
    return batch


def get(request: str) -> dict | None:
    with _LOCK:
        batch = _BATCHES.get(request)
        if batch is not None:
            return _public(batch)
    batch = _load(request) if isinstance(request, str) and REQUEST_RE.fullmatch(request) else None
    return _public(batch) if batch else None


def _public(batch: dict) -> dict:
    return {key: (list(value) if isinstance(value, list) else
                  dict(value) if isinstance(value, dict) else value)
            for key, value in batch.items()}


def _summarise(batch: dict) -> None:
    items = batch.get("items", [])
    batch["archived"] = sum(1 for i in items if i["outcome"] in ("archived", "committed"))
    batch["already"] = sum(1 for i in items if i["outcome"] == "already-archived")
    batch["skipped"] = sum(1 for i in items if i["outcome"] == "skipped")
    batch["failed"] = sum(1 for i in items if i["outcome"] == "failed")
    batch["uncommitted"] = sum(1 for i in items if i.get("committed") is False)
    outstanding = [i["file"] for i in items
                   if i["outcome"] == "failed" or i.get("committed") is False]
    outstanding += [f for f in batch.get("notReached", []) if f not in outstanding]
    batch["outstanding"] = outstanding


def start(request, files, retry_of=None, *, background: bool = True) -> tuple[dict, bool]:
    """Begin (or find) the batch `request` names. Returns the batch and
    whether it was already there. Raises ValueError for a request that is
    malformed, reuses an identity for another set, or arrives while
    another batch runs."""
    global _RUNNING
    request, files, retry_of = _check_request(request, files, retry_of)
    with _LOCK:
        known = _BATCHES.get(request) or _load(request)
        if known is not None:
            if known.get("files") != files:
                raise ValueError("that request identity was used for another set of "
                                 "cards — nothing was archived; ask again")
            _BATCHES[request] = known
            return _public(known), True
        if _RUNNING is not None:
            raise ValueError("an Archive all is already running on this board — wait "
                             "for its summary")
        retry_from = None
        if retry_of:
            retry_from = _BATCHES.get(retry_of) or _load(retry_of)
            if retry_from is None:
                raise ValueError(f"no Archive all {retry_of} on this board to retry")
        uncommitted = {i["file"] for i in (retry_from or {}).get("items", [])
                       if i.get("committed") is False}
        batch = {"v": 1, "request": request, "files": files, "retryOf": retry_of,
                 "status": "running", "started": _now(), "ended": None,
                 "items": [], "current": None, "total": len(files),
                 "sync": None, "commitRetry": sorted(uncommitted & set(files))}
        _summarise(batch)
        _BATCHES[request] = batch
        while len(_BATCHES) > KEEP_BATCHES:
            oldest = next(k for k in _BATCHES if k != request)
            _BATCHES.pop(oldest)
        _RUNNING = request
    _persist(batch)
    if background:
        threading.Thread(target=_execute, args=(batch,), name="archive-all",
                         daemon=True).start()
    else:
        _execute(batch)
    return get(request), False


def _one(filename: str, commit_retry: bool) -> dict:
    """Read the card again, then archive it through the single door — or
    say why not."""
    stage = taskfiles.find_stage_of(filename)
    in_archive = (config.TASKS / "archive" / filename).is_file()
    if stage is None and in_archive:
        if commit_retry and config.COMMIT_MOVES:
            committed = taskfiles.commit_edit(filename, "archive", "archived")
            if committed:
                return {"outcome": "committed", "committed": True,
                        "reason": "its archive is now committed"}
            return {"outcome": "archived", "committed": False,
                    "reason": "archived, and committing it failed again — the "
                              "activity log says why"}
        return {"outcome": "already-archived",
                "reason": "already in the archive — nothing to do"}
    if stage is None:
        return {"outcome": "skipped", "code": "gone",
                "reason": "no longer on the board"}
    if stage != STAGE:
        return {"outcome": "skipped", "code": "moved",
                "reason": f"moved to {stage}/ since you confirmed — not archived"}
    _tasks, by_file = _board()
    task = by_file.get(filename)
    if task is None or task.get("stage") != STAGE:
        return {"outcome": "skipped", "code": "moved",
                "reason": "moved while it was being read — not archived"}
    why = _excluded(task, by_file)
    if why is not None:
        return {"outcome": "skipped", "code": why[0],
                "reason": f"{why[1]} — not archived"}
    try:
        phases.assert_not_working(filename, "archive it")
        result = taskfiles.archive_task(filename, STAGE)
    except (ValueError, OSError) as exc:
        return {"outcome": "failed", "reason": str(exc) or exc.__class__.__name__}
    committed = result.get("committed")
    item = {"outcome": "archived", "committed": committed}
    if committed is False:
        item["reason"] = ("archived, but committing it failed — the card is in "
                          "archive/ uncommitted; retry commits it")
    return item


def _execute(batch: dict) -> None:
    global _RUNNING
    archived: list[dict] = []
    finished = False
    try:
        for filename in batch["files"]:
            with _LOCK:
                batch["current"] = filename
            try:
                item = _one(filename, filename in batch["commitRetry"])
            except Exception as exc:  # noqa: BLE001 — one card never stops the rest
                item = {"outcome": "failed",
                        "reason": f"{exc.__class__.__name__}: {exc}"}
            item = {"file": filename, **item}
            if item["outcome"] == "archived":
                archived.append({"file": filename, "from": STAGE})
            with _LOCK:
                batch["items"].append(item)
                _summarise(batch)
            _persist(batch)
            state.broadcast({"type": "board"})
        sync_said = None
        if config.SYNC and (archived or batch.get("commitRetry")):
            try:
                sync_said = sync.push_now()
            except Exception as exc:  # noqa: BLE001
                sync_said = f"error: {exc}"
        with _LOCK:
            batch["sync"] = ({"enabled": True, "outcome": sync_said,
                              "status": sync.status()} if config.SYNC
                             else {"enabled": False})
        finished = True
    finally:
        if archived:
            # ⌘Z covers the last archive this board made: here, the whole
            # set — in place before the batch reads as ended, so an undo
            # pressed on the summary finds it
            with state.LOCK:
                state.LAST_ARCHIVED = {"batch": archived, "request": batch["request"]}
        with _LOCK:
            if finished:
                batch["status"] = "finished"
            if batch["status"] == "running":
                batch["status"] = "interrupted"
                reached = {i["file"] for i in batch["items"]}
                batch["notReached"] = [f for f in batch["files"] if f not in reached]
            batch["current"] = None
            batch["ended"] = _now()
            _summarise(batch)
            if _RUNNING == batch["request"]:
                _RUNNING = None
        _persist(batch)
    state.record_board_event({
        "kind": "move", "actor": "you", "to": "archive",
        "summary": _sentence(batch)})
    state.broadcast({"type": "board"})


def _sentence(batch: dict) -> str:
    parts = [f"Archive all: {batch['archived']} archived from done/"]
    if batch["already"]:
        parts.append(f"{batch['already']} already archived")
    if batch["skipped"]:
        parts.append(f"{batch['skipped']} skipped")
    if batch["failed"]:
        parts.append(f"{batch['failed']} failed")
    if batch["uncommitted"]:
        parts.append(f"{batch['uncommitted']} not committed")
    tail = " — ⌘Z brings them back" if batch["archived"] else ""
    return ", ".join(parts) + tail


def unarchive_batch(entries: list[dict]) -> dict:
    """⌘Z after Archive all: bring every card of that set back to done/,
    each through the single restore door. A card that is no longer in the
    archive (moved by hand since) is reported, not guessed at."""
    restored, refused = [], []
    for entry in entries:
        try:
            result = taskfiles.unarchive_task(entry["file"], entry["from"])
        except (ValueError, OSError) as exc:
            refused.append({"file": entry["file"], "reason": str(exc)})
            continue
        restored.append(result)
    return {"batch": restored, "refused": refused, "to": STAGE,
            "committed": (None if not restored else
                          all(r.get("committed") is not False for r in restored))}
