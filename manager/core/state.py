"""Shared in-memory state, persistence of event logs, and the SSE fan-out.

All cross-thread registries live here, guarded by LOCK where they are
mutated from several threads. Modules communicate through this state rather
than importing each other's internals.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import config
import records

LOCK = threading.Lock()
CLIENTS: set[queue.Queue] = set()          # one queue per open SSE connection
SESSIONS: dict[str, dict] = {}             # session_id -> meta
EVENTS: dict[str, list[dict]] = {}         # session_id -> slim events
BOARD_EVENTS: list[dict] = []              # moves + agent lifecycle
AGENTS: dict[str, dict] = {}               # agent_id -> launch record
EXPECTED_MOVES: dict[tuple[str, str], tuple[str, float, bool]] = {}  # (file, to) -> (actor, ts, quiet)
COMMIT_HOOKS: list = []                    # run after a board-made task commit
COMPLETING: dict[str, dict] = {}           # filename -> {started, step}: merge & clean up in flight

# The port actually being served; board.py sets it from --port at startup so
# launched agents know where to report events.
serve_port = config.PORT

# The last card archived through this board — the scope of the ⌘Z undo.
LAST_ARCHIVED: dict | None = None

# A session's identity sidecar, beside its <sid>.jsonl event log. Not a
# `.jsonl` itself, so no reader that globs the event logs picks it up.
IDENTITY_SUFFIX = ".who.json"


# How many times this board has said "the board changed" (task 128). Every
# `board` frame carries the value it was sent with, and `/api/state` carries
# the value it read *before* reading the disk — so a reading whose `boardRev`
# is at least a frame's `rev` already holds that frame's change. That is the
# order guard the page reconciles by: a confirmed archive names the revision
# its broadcast took, a state reading older than that cannot resurrect the
# card, and a frame the page has already read past asks for nothing.
# In memory and per process; a restart starts again at zero, which a page
# reads as an older revision and simply reads the board again.
BOARD_REV = 0
# Which process the revisions belong to: a reading or a reply from another
# boot is not comparable with this one's revisions.
BOOT = f"{time.time_ns():x}"


def board_rev() -> int:
    with LOCK:
        return BOARD_REV


def broadcast(payload: dict) -> int | None:
    """Fan a frame out to every open stream. A `board` frame is stamped
    with the next board revision, which is returned — the write that
    caused it tells its caller which reading will contain it."""
    global BOARD_REV
    rev = None
    with LOCK:
        if payload.get("type") == "board":
            BOARD_REV += 1
            rev = BOARD_REV
            payload = {**payload, "rev": rev}
        clients = list(CLIENTS)
    msg = json.dumps(payload)
    for q in clients:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass
    return rev


def persist(name: str, record: dict) -> None:
    try:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        with (config.SESSIONS_DIR / name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _session_file(name: str) -> Path | None:
    if "/" in name or ".." in name:
        return None
    return config.SESSIONS_DIR / name


def persist_identity(sid: str, identity: dict) -> None:
    """Who a session belonged to, written beside its event log.

    A whole small file of its own rather than a key on the events: the logs
    are append-only JSONL whose first line every reader takes for an event,
    and identity is a property of the session, not of anything that happened
    inside it. The agent's *name* and *model* live only in board memory, so
    this file is the only thing a restart can read them back from.

    Rewritten whenever what we know changes — an agent id that arrives on a
    later event, a name that was not registered yet when the first event
    landed. The file is written whole, so the last write is simply the truth.
    """
    path = _session_file(f"{sid}{IDENTITY_SUFFIX}")
    if path is None:
        return
    try:
        records.write_json(path, identity, indent=None, sort_keys=False)
    except OSError:
        pass


def read_identity(sid: str) -> dict | None:
    """The persisted identity, or None when nothing was ever recorded — the
    difference between "this session was the person" and "we do not know",
    which is exactly what the label must not blur."""
    path = _session_file(f"{sid}{IDENTITY_SUFFIX}")
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_board_event(event: dict) -> None:
    event["ts"] = time.time()
    with LOCK:
        BOARD_EVENTS.append(event)
        del BOARD_EVENTS[:-config.BOARD_EVENTS_CAP]
        # a card being completed says which step it is on, and the steps are
        # already narrated here — so the registry reads them rather than
        # asking every caller to report twice
        claimed = COMPLETING.get(event.get("file"))
        stepped = bool(claimed) and bool(event.get("summary"))
        if stepped:
            claimed["step"] = event["summary"]
    persist("board.jsonl", event)
    broadcast({"type": "board_event", "event": event})
    if stepped:
        publish_completing()


def completing_public() -> dict:
    with LOCK:
        return {filename: dict(record) for filename, record in COMPLETING.items()}


def publish_completing() -> None:
    """The whole registry, every time it changes. It is one entry at most in
    practice, and a whole map costs nothing to send and cannot go stale in
    the way a patch can."""
    broadcast({"type": "completing", "completing": completing_public()})


def claim_completing(filename: str, step: str) -> bool:
    """Claim a card for the long, destructive run behind "merge & clean up".

    The claim is the card's busy state — what it renders instead of looking
    idle, and what refuses a second request rather than starting a second
    merge. It lives here, in this board's memory, so it dies with the
    process: a board that is killed mid-completion leaves no card stuck
    busy, and every other replica sees the card unchanged until the move
    arrives (state syncs; reactions don't).

    False when the card is already claimed — the caller refuses and must
    not release what it did not take.
    """
    with LOCK:
        if filename in COMPLETING:
            return False
        COMPLETING[filename] = {"started": time.time(), "step": step}
    publish_completing()
    return True


def release_completing(filename: str) -> None:
    """Give the card back — on success, on conflict, on crash alike. A card
    stuck busy forever is worse than a card that looked idle."""
    with LOCK:
        released = COMPLETING.pop(filename, None) is not None
    if released:
        publish_completing()


def task_committed(filename: str) -> None:
    """A board-made move committed itself. Registered hooks turn that into
    whatever else should follow — sync.py's push, when the gate is on. The
    hook is a registry rather than an import so taskfiles stays to the left
    of everything that reacts to it; a hook that raises must never break a
    move that has already happened on disk."""
    for hook in list(COMMIT_HOOKS):
        try:
            hook(filename)
        except Exception:   # noqa: BLE001 — the move is done; nothing may undo it
            pass


def expect_move(filename: str, target: str, actor: str, quiet: bool = False) -> None:
    """Tell the watcher who is about to move a file so it can attribute it.

    `quiet` adds the other half of that: the mover has already narrated
    this one, so the watcher renders it and says nothing. A phase ending
    moves every card it merged at once and reports it as the one thing it
    is; five identical move lines scrolling behind that would be the same
    fact told badly. It suppresses the ticker line only — the move is
    still a move, and everything the board hangs off one still happens.
    """
    with LOCK:
        EXPECTED_MOVES[(filename, target)] = (actor, time.time(), quiet)


def claim_move(filename: str, target: str) -> tuple[str, bool]:
    """Who moved this, and whether they already said so."""
    with LOCK:
        expected = EXPECTED_MOVES.pop((filename, target), None)
        # forget stale expectations while we're here
        cutoff = time.time() - 30
        for key in [k for k, (_, ts, _) in EXPECTED_MOVES.items() if ts < cutoff]:
            EXPECTED_MOVES.pop(key, None)
    return (expected[0], expected[2]) if expected else ("disk", False)


def claim_expected(filename: str, target: str) -> str:
    return claim_move(filename, target)[0]
