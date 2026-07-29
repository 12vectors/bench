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

import config

LOCK = threading.Lock()
CLIENTS: set[queue.Queue] = set()          # one queue per open SSE connection
SESSIONS: dict[str, dict] = {}             # session_id -> meta
EVENTS: dict[str, list[dict]] = {}         # session_id -> slim events
BOARD_EVENTS: list[dict] = []              # moves + agent lifecycle
AGENTS: dict[str, dict] = {}               # agent_id -> launch record
EXPECTED_MOVES: dict[tuple[str, str], tuple[str, float]] = {}  # (file, to) -> (actor, ts)

# The port actually being served; board.py sets it from --port at startup so
# launched agents know where to report events.
serve_port = config.PORT

# The last card archived through this board — the scope of the ⌘Z undo.
LAST_ARCHIVED: dict | None = None


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
        config.SESSIONS_DIR.mkdir(exist_ok=True)
        with (config.SESSIONS_DIR / name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def record_board_event(event: dict) -> None:
    event["ts"] = time.time()
    with LOCK:
        BOARD_EVENTS.append(event)
        del BOARD_EVENTS[:-config.BOARD_EVENTS_CAP]
    persist("board.jsonl", event)
    broadcast({"type": "board_event", "event": event})


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
