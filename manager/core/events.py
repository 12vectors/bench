"""Ingesting normalized events — the fixed contract every adapter speaks.

Adapters translate their vendor's payloads at the edge (see
adapters/*/emit*) and POST the normalized schema here:

    {"v": 1, "session": str, "kind": str, "summary": str,
     "file"?: str, "cmd"?: str, "detail"?: str, "ok"?: bool,
     "running"?: bool, "agent"?: str, "task"?: str}

Core sanitises, updates the session registry, persists a slim record per
session, and pushes to connected browsers over SSE. It never interprets a
vendor's tool vocabulary — that knowledge lives in the adapter.

Beside each session's event log sits its identity — who the session
belonged to, written whole (see state.persist_identity). Events say what
happened; the identity says whose, and it is the only part a restart
cannot recover from the stream.
"""

from __future__ import annotations

import json
import re
import time

import config
import progress
import state
from taskfiles import NUMBER_RE

KINDS = {"session", "end", "idle", "edit", "read", "search", "command",
         "test", "check", "git", "plan", "subagent", "web", "report", "other"}


# In-memory copy of what each session's identity file already says, so the
# sidecar is rewritten only when what the board knows actually changes.
_WRITTEN: dict[str, dict] = {}


def session_label(meta: dict) -> str:
    """Who a session was — in three registers, because there are three
    different states and only one of them is the person.

    An agent's name comes from the live launch record while this board
    still holds it, and from the identity persisted with the session after
    a restart; `Agent` (or `Review`) is the honest fallback when the id is
    known but the name is not. `You` is said only of a session the board
    positively knows carried no agent — every live one, and every replayed
    one whose identity file records that. A log written before identities
    were recorded is none of those: it is unknown, and says so rather than
    claiming to have been you.
    """
    agent_id = meta.get("agentId") or ""
    if agent_id:
        record = state.AGENTS.get(agent_id) or {}
        task = meta.get("task") or ""
        num = NUMBER_RE.match(task)
        who = (record.get("name") or meta.get("agentName")
               or ("Review" if agent_id.startswith("review-") else "Agent"))
        return f"{who} · #{num.group(1)}" if num else who
    if meta.get("known"):
        return f"You · {meta['id'][:8]}"
    return f"Session · {meta['id'][:8]}"


def _txt(value, cap: int) -> str | None:
    return value[:cap] if isinstance(value, str) and value else None


# ── replayed copies of one raw event ───────────────────────────────────
#
# Why this exists (task 73). A headless claude run in a worktree of a
# repository whose *own* `.claude/settings.json` wires this bridge — bench
# developed with bench is exactly that — gets the hooks twice: once from
# that project file (`$CLAUDE_PROJECT_DIR/…/emit.py`) and once from the
# `--settings` document the adapter generates (`/abs/…/emit.py`). The
# vendor de-duplicates identical hook *commands* only, and those two
# strings differ, so every tool call was posted twice with identical
# content and the timeline showed each edit and command two times.
#
# Two layers deal with it. The bridge stops it at the source: a launch
# names its canonical bridge (BOARD_EMIT) and any other copy stands down.
# And here, an event carrying a stable `id` — the vendor's own tool-use id
# and the hook phase, so a start and its finish stay two events — is
# recorded once however many times it arrives. A bridge too old to send
# an id is recognised by contrast: once a session has delivered ids, an
# id-less event in it can only be a second, older bridge's copy, and is
# dropped. Distinct repeated edits and repeated test runs carry distinct
# ids and are never collapsed for having matching text.
_EVENT_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,160}$")
SEEN_CAP = 2000
_SEEN: dict[str, dict[str, None]] = {}     # sid -> ordered ids already recorded
_BRIDGED: set[str] = set()                 # sids whose bridge sends ids
DROPPED: dict[str, int] = {}               # sid -> replayed copies dropped


def _seen_for(sid: str) -> dict[str, None]:
    """The ids this session has recorded, rebuilt from its log on first
    sight after a restart, so a replay across a restart is still one."""
    seen = _SEEN.get(sid)
    if seen is not None:
        return seen
    seen = {}
    path = config.SESSIONS_DIR / f"{sid}.jsonl"
    if "/" not in sid and ".." not in sid and path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[-SEEN_CAP:]:
                event_id = json.loads(line).get("id")
                if isinstance(event_id, str):
                    seen[event_id] = None
        except (OSError, ValueError):
            pass
    if any(not i.startswith("board:") for i in seen):
        _BRIDGED.add(sid)
    _SEEN[sid] = seen
    return seen


def ingest_event(raw: dict) -> dict:
    """Record one normalized event. Returns what the bridge may be told
    back — `{"duplicate": true}` for a replayed copy, and `{"remind": …}`
    when the run behind it is due a progress update."""
    if not isinstance(raw, dict) or "kind" not in raw:
        return {}  # not a normalized event — adapters own translation
    sid = str(raw.get("session") or "unknown")
    kind = raw["kind"] if raw.get("kind") in KINDS else "other"
    event_id = raw.get("id") if isinstance(raw.get("id"), str) \
        and _EVENT_ID.match(raw["id"]) else None

    with state.LOCK:
        seen = _seen_for(sid)
        if event_id is not None:
            if event_id in seen:
                DROPPED[sid] = DROPPED.get(sid, 0) + 1
                return {"duplicate": True}
            seen[event_id] = None
            while len(seen) > SEEN_CAP:
                seen.pop(next(iter(seen)))
            # the board's own events (`board:…`) say nothing about which
            # bridge a session has, so they never make id-less events suspect
            if not event_id.startswith("board:"):
                _BRIDGED.add(sid)
        elif sid in _BRIDGED:
            DROPPED[sid] = DROPPED.get(sid, 0) + 1
            return {"duplicate": True}

    event = {"ts": time.time(), "session": sid, "kind": kind,
             "summary": _txt(raw.get("summary"), 300) or kind}
    if event_id is not None:
        event["id"] = event_id
    for key, cap in (("file", 300), ("cmd", 300), ("detail", 900)):
        value = _txt(raw.get(key), cap)
        if value:
            event[key] = value
    if isinstance(raw.get("ok"), bool):
        event["ok"] = raw["ok"]
    if raw.get("running") is True:
        event["running"] = True

    agent_id = _txt(raw.get("agent"), 120)
    task = _txt(raw.get("task"), 200)

    with state.LOCK:
        meta = state.SESSIONS.setdefault(sid, {
            "id": sid, "started": event["ts"], "count": 0,
            "agentId": None, "agentName": None, "agentModel": None,
            "task": None, "status": "active",
        })
        just_linked = False
        if agent_id:
            meta["agentId"] = agent_id
            record = state.AGENTS.get(agent_id)
            if record is not None:
                # The name and the model exist nowhere but this record, and
                # it may only have been registered after the child's first
                # event — so they are taken every time, not just on linking.
                meta["agentName"] = record.get("name")
                meta["agentModel"] = record.get("model")
                just_linked = record["session"] is None
                record["session"] = sid
                task = task or record["task"]
        if task:
            meta["task"] = task
        # An event reaching here is a session the board is watching live, so
        # it knows what it is looking at: an agent when one identified
        # itself, the person when none did.
        meta["known"] = True
        meta["last"] = event["ts"]
        meta["lastSummary"] = event["summary"]
        meta["lastKind"] = kind
        meta["status"] = {"end": "ended", "idle": "idle"}.get(kind, "active")
        meta["label"] = session_label(meta)
        identity = None
        if not event.get("running"):
            meta["count"] += 1
            state.EVENTS.setdefault(sid, []).append(event)
            del state.EVENTS[sid][:-config.EVENTS_CAP]
            identity = {"agentId": meta["agentId"], "name": meta["agentName"],
                        "model": meta["agentModel"], "task": meta["task"]}
            if identity == _WRITTEN.get(sid):
                identity = None
            else:
                _WRITTEN[sid] = identity
        meta_snapshot = dict(meta)

    if not event.get("running"):
        state.persist(f"{sid}.jsonl", event)
    if identity is not None:
        # written beside the log it belongs to, and only when it changed
        state.persist_identity(sid, identity)
    if just_linked:
        # the agent's card can now show its live line instead of "warming up"
        state.broadcast({"type": "agents"})
    state.broadcast({"type": "event", "event": event, "session": meta_snapshot})
    # A tool boundary of a run that has a progress channel: the one moment a
    # reminder can be handed back without interrupting anything. Never on
    # the session's own lifecycle events, and never more than once per
    # interval (progress.reminder keeps the clock).
    if kind not in ("session", "end", "idle", "report") and not event.get("running"):
        remind = progress.reminder(meta_snapshot.get("agentId"))
        if remind:
            return {"remind": remind}
    return {}


def load_disk_sessions() -> None:
    """Rebuild session metadata from state/sessions/ so past sessions replay."""
    if not config.SESSIONS_DIR.is_dir():
        return
    for path in config.SESSIONS_DIR.glob("*.jsonl"):
        sid = path.stem
        if sid == "board" or sid in state.SESSIONS:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
        except (OSError, json.JSONDecodeError, IndexError):
            continue
        # Who it was, if it was recorded. Absent = a log from before
        # identities were written; the label must not fill that gap in.
        identity = state.read_identity(sid)
        known = identity is not None
        identity = identity or {}
        meta = {
            "id": sid, "started": first.get("ts"), "last": last.get("ts"),
            "count": len(lines),
            "agentId": identity.get("agentId"),
            "agentName": identity.get("name"),
            "agentModel": identity.get("model"),
            "task": identity.get("task"),
            "known": known,
            "status": "ended", "lastSummary": last.get("summary"),
            "lastKind": last.get("kind"),
        }
        meta["label"] = session_label(meta)
        state.SESSIONS[sid] = meta

    board_log = config.SESSIONS_DIR / "board.jsonl"
    if board_log.is_file():
        try:
            lines = board_log.read_text(encoding="utf-8").splitlines()[-100:]
            state.BOARD_EVENTS.extend(json.loads(l) for l in lines)
        except (OSError, json.JSONDecodeError):
            pass


def session_events(sid: str) -> list[dict]:
    with state.LOCK:
        if sid in state.EVENTS:
            return list(state.EVENTS[sid])
    path = config.SESSIONS_DIR / f"{sid}.jsonl"
    if not path.is_file() or "/" in sid or ".." in sid:
        return []
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-config.EVENTS_CAP:]:
            events.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return events
