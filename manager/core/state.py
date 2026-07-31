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

LOCK = threading.Lock()
CLIENTS: set[queue.Queue] = set()          # one queue per open SSE connection
SESSIONS: dict[str, dict] = {}             # session_id -> meta
EVENTS: dict[str, list[dict]] = {}         # session_id -> slim events
BOARD_EVENTS: list[dict] = []              # moves + agent lifecycle
AGENTS: dict[str, dict] = {}               # agent_id -> launch record
EXPECTED_MOVES: dict[tuple[str, str], tuple[str, float]] = {}  # (file, to) -> (actor, ts)
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


def broadcast(payload: dict) -> None:
    msg = json.dumps(payload)
    with LOCK:
        clients = list(CLIENTS)
    for q in clients:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


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
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(identity), encoding="utf-8")
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


def expect_move(filename: str, target: str, actor: str) -> None:
    """Tell the watcher who is about to move a file so it can attribute it."""
    with LOCK:
        EXPECTED_MOVES[(filename, target)] = (actor, time.time())


def claim_expected(filename: str, target: str) -> str:
    with LOCK:
        actor_ts = EXPECTED_MOVES.pop((filename, target), None)
        # forget stale expectations while we're here
        cutoff = time.time() - 30
        for key in [k for k, (_, ts) in EXPECTED_MOVES.items() if ts < cutoff]:
            EXPECTED_MOVES.pop(key, None)
    return actor_ts[0] if actor_ts else "disk"
