"""The one installation that may execute orchestration (D13, D14).

Task 137 (phase 136). Many boards may read the same work state; exactly
one installation, named explicitly, may *act* on it under an operating
agreement, and on that machine exactly one process. Two facts decide it,
and both must hold:

- **The designation** — a small portable record in the work state,
  `<work root>/orchestration/designation.json`, naming the installation
  that executes. It is a *declaration*, carried by sync like any board
  edit so every replica can read who executes. It is not a lock: git sync
  is not a distributed lock and is never treated as one.
- **The operating-system lock** — an exclusive, non-blocking `flock` on
  `manager/local/state/orchestration/execution.lock`, held for as long as a
  process owns execution. A second process on the same installation is
  refused while the first holds it; the kernel releases it when the
  holder exits, however it exits.

An installation is identified by an id made once, on the first
designation, and kept under its own machine-local state; it names no
host, user or path. A board whose id the designation does not name is a
**replica**: it reads and renders, and nothing here lets it execute.

**No failover.** There is no heartbeat expiry, lease or fencing. The
owner process writes an informational note (`owner.json`: its process id
and when it took the lock) and nothing reads it as authority: a stale
note, an unreachable host or a dead-looking process proves nothing about
what is still running there. The designation moves only by a controlled
handover — the owner establishes quiescence (no run in flight, nothing
unreconciled) and releases — followed by an explicit adoption somewhere
else. After an adoption, cards that were already working need explicit
per-card adoption before automation touches them. If the former owner is
gone for good, the way through is a person's: confirm its processes are
stopped, then remove the designation by hand. Bench will not guess.

Imports `config`, `records` and `taskfiles` (for committing the portable
record under `BOARD_COMMIT_MOVES`, as every board edit does).
"""

from __future__ import annotations

import fcntl
import os
import secrets
import threading
import time
from pathlib import Path

import config
import records
import taskfiles

KIND = "bench-orchestration-designation"
VERSION = 1
STATES = ("designated", "released")
ROLES = ("unconfigured", "designated-here", "replica", "released",
         "unreadable")

_LOCK = records.InterprocessLock(lambda: state_dir() / "designation.lock")
_HELD_LOCK = threading.Lock()
_HELD: dict = {}          # path → fd of the execution lock this process holds


class Refused(Exception):
    """An ownership change bench will not make, with the API status."""

    def __init__(self, message: str, status: int = 409, code: str = "refused",
                 **extra) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra


# ── where things live ─────────────────────────────────────────────────

def state_dir() -> Path:
    return config.STATE / "orchestration"


def designation_path() -> Path:
    return config.work_root() / "orchestration" / "designation.json"


def identity_path() -> Path:
    return state_dir() / "installation.json"


def lock_path() -> Path:
    return state_dir() / "execution.lock"


def owner_note_path() -> Path:
    return state_dir() / "owner.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── identity ──────────────────────────────────────────────────────────

def installation_id() -> str | None:
    """This installation's id, or None before it has ever been given one.
    Reading never makes one."""
    found = records.read_json(identity_path(), expect=dict) or {}
    value = found.get("id")
    return value if isinstance(value, str) and len(value) == 24 else None


def ensure_installation_id() -> str:
    current = installation_id()
    if current:
        return current
    value = secrets.token_hex(12)
    records.write_json(identity_path(), {"id": value, "made": _now(),
                                         "v": VERSION}, durable=True)
    return value


# ── the designation ───────────────────────────────────────────────────

def read_designation():
    """(record or None, problem or None)."""
    path = designation_path()
    if not path.exists():
        return None, None
    found = records.read_json(path)
    if not isinstance(found, dict) or found.get("kind") != KIND \
            or found.get("v") != VERSION or found.get("state") not in STATES \
            or not isinstance(found.get("installation"), str):
        return None, ("the designation record is not one this bench reads; "
                      "nothing executes until a person settles it")
    return found, None


def designation_revision(record) -> str:
    return str(record.get("revision", 0)) if record else "none"


def _write_designation(record: dict, what: str) -> bool:
    path = designation_path()
    real = config.escapes_work(path)
    if real is not None:
        raise Refused(f"orchestration/designation.json is a link to {real}, "
                      f"outside the work root; bench does not write through it")
    record["_doc"] = ("Written by bench. Names the one installation that may "
                      "execute orchestration. A declaration, not a lock: it "
                      "moves only by a controlled handover and adoption.")
    path.parent.mkdir(parents=True, exist_ok=True)
    records.write_json(path, record, durable=True)
    if not config.COMMIT_MOVES:
        return False
    return taskfiles.commit_work_file(
        path, f"board: orchestration {what} ({taskfiles.actor_name() or 'board'})",
        f"orchestration: {what} recorded, but committing it failed")


def _guard(record, expected) -> None:
    now = designation_revision(record)
    if expected is None or str(expected) != now:
        raise Refused(f"the designation changed since it was read "
                      f"({expected} → {now}); read it again", 409,
                      code="stale", stale=True, current=now)


# ── the operating-system lock ─────────────────────────────────────────

def holds_lock() -> bool:
    with _HELD_LOCK:
        return str(lock_path()) in _HELD


def acquire_lock() -> bool:
    """Take the execution lock without waiting. True when this process
    holds it (now or already); False when another process does."""
    path = lock_path()
    key = str(path)
    with _HELD_LOCK:
        if key in _HELD:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        _HELD[key] = fd
    records.write_json(owner_note_path(), {
        "pid": os.getpid(), "since": _now(), "installation": installation_id(),
        "_doc": "informational only: never read as authority"})
    return True


def release_lock() -> None:
    key = str(lock_path())
    with _HELD_LOCK:
        fd = _HELD.pop(key, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        try:
            owner_note_path().unlink()
        except OSError:
            pass


def lock_free() -> bool | None:
    """Whether no process holds the lock now — a probe that takes and
    drops it at once. None when the lock file does not exist (nothing has
    ever executed here); asking never creates it."""
    path = lock_path()
    if holds_lock():
        return False
    if not path.exists():
        return None
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


# ── reading ───────────────────────────────────────────────────────────

def reading() -> dict:
    """Who executes, as this board can tell. Writes nothing, makes no id,
    creates no lock file, and reads the owner note as information only."""
    record, problem = read_designation()
    mine = installation_id()
    if problem:
        role = "unreadable"
    elif record is None:
        role = "unconfigured"
    elif record["state"] == "released":
        role = "released"
    elif mine and record["installation"] == mine:
        role = "designated-here"
    else:
        role = "replica"
    note = records.read_json(owner_note_path(), expect=dict) or None
    adoption = (record or {}).get("adoption") or {}
    adopted_cards = [str(c) for c in adoption.get("cards") or []]
    # `pending` names the cards that were working when execution was
    # adopted; a record written before it did not say, and keeps holding
    # every working card until each is adopted (the conservative reading)
    pending = adoption.get("pending")
    pending = ([str(c) for c in pending if str(c) not in adopted_cards]
               if isinstance(pending, list) else None)
    required = bool(adoption.get("required")) and pending != []
    return {
        "role": role, "problem": problem,
        "revision": designation_revision(record),
        "installation": mine,
        "designated": (record or {}).get("installation"),
        "state": (record or {}).get("state"),
        "since": (record or {}).get("at"),
        "adoptedFrom": (record or {}).get("adoptedFrom"),
        "adoptionRequired": required,
        "adoptedCards": adopted_cards,
        "adoptionPending": pending if required else [],
        "releasedWith": (record or {}).get("quiescence"),
        "lockHeld": holds_lock(),
        "lockFree": lock_free(),
        "ownerNote": ({"pid": note.get("pid"), "since": note.get("since"),
                       "authority": False} if note else None),
    }


def authorizes() -> dict:
    """May this process execute now? Both facts, and nothing else: the
    designation names this installation, and this process holds the lock.
    A heartbeat, an owner note or an absent peer is never consulted."""
    read = reading()
    if read["role"] != "designated-here":
        return {"allowed": False, "code": {
            "unconfigured": "not-designated", "replica": "replica",
            "released": "released", "unreadable": "designation-unreadable",
        }[read["role"]], "reason": {
            "unconfigured": "no installation is designated to execute "
                            "orchestration; this board only reads",
            "replica": "another installation is designated to execute; this "
                       "board is a read-only replica",
            "released": "execution was handed over and nothing has adopted it "
                        "yet",
            "unreadable": read["problem"] or "the designation cannot be read",
        }[read["role"]]}
    if not read["lockHeld"]:
        return {"allowed": False, "code": "lock-not-held",
                "reason": "this installation is designated, but this process "
                          "does not hold the execution lock"}
    return {"allowed": True, "code": "owner",
            "reason": "this installation is designated and this process holds "
                      "the execution lock"}


# ── changing ownership ────────────────────────────────────────────────

WORKING_STAGES = ("in-progress", "review")


def _working_cards() -> list[str]:
    """The numbers of the cards in a working stage now — what an adoption
    must reconcile, because it was under way before this installation
    executed anything."""
    found = set()
    for stage in taskfiles.collect()["stages"]:
        if stage["slug"] not in WORKING_STAGES:
            continue
        for task in stage["tasks"]:
            if task.get("number"):
                found.add(taskfiles.canonical_number(task["number"]))
    return sorted(found, key=int)

def designate(expected, actor: str | None = None) -> dict:
    """Name this installation as the one that executes. Only where nothing
    is designated, or execution was released by a controlled handover —
    never over another installation's designation, however stale it
    looks."""
    with _LOCK:
        record, problem = read_designation()
        if problem:
            raise Refused(problem, code="designation-unreadable")
        _guard(record, expected)
        if record is not None and record["state"] == "designated":
            mine = installation_id()
            if record["installation"] == mine:
                raise Refused("this installation is already designated",
                              code="already-designated")
            raise Refused(
                "another installation is designated to execute. It must hand "
                "over first (establish quiescence and release); a stale "
                "heartbeat or an unreachable host is not evidence that its "
                "processes stopped. If it is gone for good, confirm that, then "
                "remove orchestration/designation.json by hand",
                code="owned-elsewhere")
        mine = ensure_installation_id()
        adopted = record is not None and record["state"] == "released"
        # Only the work that was already under way when execution moved
        # needs reconciling (PR 147 review, item 4): the cards in a working
        # stage at this moment. Work that starts here afterwards is this
        # installation's own, and is not held behind the adoption.
        try:
            pending = _working_cards() if adopted else []
        except (OSError, ValueError, KeyError):
            # the board could not be read: hold every working card, the
            # reading a record without `pending` gets
            pending = None
        new = {"kind": KIND, "v": VERSION, "state": "designated",
               "installation": mine, "at": _now(), "by": actor or "unknown",
               "revision": int((record or {}).get("revision", 0)) + 1,
               "adoptedFrom": record["releasedFrom"] if adopted else None,
               "adoption": ({"required": adopted, "cards": []} if pending is None
                            else {"required": bool(pending), "cards": [],
                                  "pending": pending})}
        committed = _write_designation(new, "adopted" if adopted else "designated")
    return {"designation": reading(), "committed": committed}


def handover(expected, quiescence: dict, actor: str | None = None) -> dict:
    """Release execution so another installation can adopt it. Refused
    unless this installation is designated, no other process here holds
    the lock, and the quiescence the caller established is empty: no run
    in flight and nothing unreconciled. Unknown outcomes stay held — they
    are unreconciled until a person or a receipt settles them."""
    in_flight = list(quiescence.get("inFlight") or [])
    unsettled = list(quiescence.get("unreconciled") or [])
    with _LOCK:
        record, problem = read_designation()
        if problem:
            raise Refused(problem, code="designation-unreadable")
        _guard(record, expected)
        mine = installation_id()
        if record is None or record["state"] != "designated" or \
                record["installation"] != mine:
            raise Refused("only the designated installation can hand over",
                          code="not-owner")
        held_before = holds_lock()
        if not acquire_lock():
            raise Refused("another process on this installation holds the "
                          "execution lock; stop it before handing over",
                          code="lock-in-use")
        if in_flight or unsettled:
            if not held_before:
                release_lock()
            raise Refused("execution is not quiescent: "
                          f"{len(in_flight)} run(s) in flight and "
                          f"{len(unsettled)} unreconciled operation(s)",
                          code="not-quiescent", inFlight=in_flight,
                          unreconciled=unsettled)
        new = {"kind": KIND, "v": VERSION, "state": "released",
               "installation": mine, "releasedFrom": mine, "at": _now(),
               "by": actor or "unknown",
               "revision": int(record.get("revision", 0)) + 1,
               "quiescence": {"inFlight": 0, "unreconciled": 0,
                              "establishedAt": _now()}}
        committed = _write_designation(new, "handed over")
        release_lock()
    return {"designation": reading(), "committed": committed}


def adopt_card(expected, card: str, actor: str | None = None) -> dict:
    """After an adoption, record that a person reconciled a working card
    and it may now be touched by automation here."""
    number = str(card).lstrip("#")
    if not number.isdigit():
        raise Refused("adoption names a card number", 400, code="invalid")
    number = str(int(number))
    with _LOCK:
        record, problem = read_designation()
        if problem:
            raise Refused(problem, code="designation-unreadable")
        _guard(record, expected)
        if record is None or record["state"] != "designated" or \
                record["installation"] != installation_id():
            raise Refused("only the designated installation adopts cards",
                          code="not-owner")
        adoption = record.setdefault("adoption", {"required": False, "cards": []})
        cards = [str(c) for c in adoption.get("cards") or []]
        if number not in cards:
            cards.append(number)
        adoption["cards"] = cards
        pending = adoption.get("pending")
        if isinstance(pending, list) and not set(map(str, pending)) - set(cards):
            # every card that was working at the handover is reconciled:
            # the adoption is complete and holds nothing back any more
            adoption["required"] = False
        record["revision"] = int(record.get("revision", 0)) + 1
        record["by"] = actor or "unknown"
        committed = _write_designation(record, f"card {number} adopted")
    return {"designation": reading(), "committed": committed}
