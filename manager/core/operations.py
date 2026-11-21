"""The durable operation ledger: intent before a side effect, receipt after.

Task 141 (phase 136). Every side effect an orchestrator makes — today a
launch of implementation work — is one **operation**, recorded here as a
small versioned JSON file under the tool's state *before* anything is
done, and updated with what was observed afterwards. So a board that
dies between the two leaves a record saying exactly that: an intent with
no receipt, which the scheduler reconciles against the authoritative
state (the run registry, the recorded plan, the process table) on its
next pass rather than guessing.

**A missing receipt is not proof that nothing happened.** Reconciliation
settles an intent as `abandoned` only where bench's own ordering proves
no process could have started; anything it cannot prove either way is
`unknown`, which **holds** the card and occupies a slot until a person
says what happened. Nothing here promises exactly-once remote execution:
the ledger is local, and the scheduler never repeats a side effect whose
outcome it cannot establish.

**Budgets are conserved.** Daily starts and launches per card are counted
from these records, which survive a restart, a pause, a resume and an
agreement edit — none of them resets a count.

The module is the low half: the record, its statuses, its key, its lock.
It decides nothing about a card and imports `config` and `records` only;
`orchestrator` reads it for capacity and `scheduler` writes it.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import config
import records

VERSION = 1
KIND = "bench-orchestration-operation"

# The life of an operation.
#
#   intent      recorded before the side effect; reserves its slot and
#               counts against daily starts and the card's launches
#   launched    the receipt: the run was observed starting
#   ended       the run's ending was observed (its outcome is recorded)
#   unobserved  launched, then the board restarted; the run's process is
#               still alive, so its slot stays occupied
#   unknown     the outcome could not be established — held for a person
#   refused     refused before anything happened (a preflight); nothing ran
#   retry       a transient failure before anything ran; counts against
#               the transient-retry ceiling, never against starts
#   abandoned   reconciled: provably never launched (nothing ran)
STATUSES = ("intent", "launched", "ended", "unobserved", "unknown",
            "refused", "retry", "abandoned")
# What occupies a slot without being in the run registry.
RESERVING = frozenset({"intent", "unknown", "unobserved"})
# What spent a start: anything that may have launched.
CONSUMING = frozenset({"intent", "launched", "ended", "unobserved", "unknown"})
# What has not settled: the scheduler looks at these again every pass.
OPEN = frozenset({"intent", "launched", "unobserved", "unknown"})
# What stands in for a launch nothing ran — another attempt may follow.
NOTHING_RAN = frozenset({"refused", "retry", "abandoned"})
# the most checks one bounded setup retry (task 164) names; delivery holds
# rather than ask about more than the ledger would keep
RETRY_MAX = 32

KEY_RE = re.compile(r"^op-[0-9a-f]{20}$")
TEXT_CAP = 400

_LOCK = records.InterprocessLock(lambda: config.STATE / "orchestration" /
                                 "operations.lock")


class Exists(ValueError):
    """An operation with this key is already recorded."""

    def __init__(self, op: dict) -> None:
        super().__init__(f"operation {op.get('key')} is already recorded "
                         f"({op.get('status')})")
        self.op = op


def ops_dir() -> Path:
    return config.STATE / "orchestration" / "operations"


def _path(key: str) -> Path:
    if not KEY_RE.match(key or ""):
        raise ValueError(f"{key!r} is not an operation key")
    return ops_dir() / f"{key}.json"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def key_for(action: str, card: str, basis: str, attempt: int = 1) -> str:
    """The stable identity of one side effect: the action, the card, what
    it was decided against (the brief's revision) and which attempt. The
    same decision asked twice — a second pass, a restart, two threads —
    is the same key, so it is recorded once."""
    raw = "\x00".join((action, card, basis, str(attempt)))
    return "op-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _clip(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return text[:TEXT_CAP]


def read(key: str) -> dict | None:
    return records.read_json(_path(key), expect=dict)


def all_ops() -> list[dict]:
    """Every operation on record, oldest intent first. A file that is not
    a record of this version is skipped, never guessed at."""
    found = []
    try:
        paths = sorted(ops_dir().glob("op-*.json"))
    except OSError:
        return []
    for path in paths:
        op = records.read_json(path, expect=dict)
        if op and op.get("v") == VERSION and op.get("status") in STATUSES \
                and KEY_RE.match(str(op.get("key"))):
            found.append(op)
    found.sort(key=lambda o: (o.get("intentEpoch") or 0, o["key"]))
    return found


def for_card(card: str, action: str | None = None,
             ops: list[dict] | None = None) -> list[dict]:
    ops = all_ops() if ops is None else ops
    return [o for o in ops if o.get("file") == card
            and (action is None or o.get("action") == action)]


def begin(key: str, *, action: str, card: str, number: str | None,
          scope: str, pool: str, basis: str, attempt: int,
          note: str | None = None, retry: dict | None = None,
          request: str | None = None) -> dict:
    """Record the intent, durably, before the side effect. Refused with
    `Exists` when the key is already on record — the receiver's dedupe:
    a second caller learns what the first did instead of doing it again.
    `retry` marks a bounded setup retry (task 164): which checks it asks
    again and the evidence each answers, so the ceiling is counted from
    the ledger and a restart resets nothing."""
    with _LOCK:
        path = _path(key)
        current = records.read_json(path, expect=dict)
        if current is not None:
            raise Exists(current)
        op = {"v": VERSION, "kind": KIND, "key": key, "action": action,
              "file": card, "card": number, "scope": scope, "pool": pool,
              "basis": basis, "attempt": attempt, "status": "intent",
              "intentAt": now_iso(), "intentEpoch": time.time(),
              "day": today(), "note": _clip(note), "history": []}
        if request and REQUEST_RE.match(request):
            op["request"] = request
        if retry:
            op["retry"] = {"checks": [_clip(c) for c in retry.get("checks") or []][:RETRY_MAX],
                           "of": [_clip(e) for e in retry.get("of") or []][:RETRY_MAX]}
        records.write_json(path, op, durable=True)
    return op


def update(key: str, status: str, **fields) -> dict:
    """Move an operation on, durably, keeping each earlier status in its
    bounded history. Unknown keys become fields of the record."""
    if status not in STATUSES:
        raise ValueError(f"{status!r} is not an operation status")
    with _LOCK:
        path = _path(key)
        op = records.read_json(path, expect=dict)
        if op is None:
            raise ValueError(f"no operation {key} on record")
        history = list(op.get("history") or [])[-19:]
        history.append({"status": op.get("status"), "at": op.get("updatedAt")
                        or op.get("intentAt")})
        op.update({k: (_clip(v) if isinstance(v, str) else v)
                   for k, v in fields.items()})
        op.update({"status": status, "updatedAt": now_iso(),
                   "updatedEpoch": time.time(), "history": history})
        records.write_json(path, op, durable=True)
    return op


def reserved(ops: list[dict]) -> list[dict]:
    return [o for o in ops if o.get("status") in RESERVING]


def consumed(ops: list[dict]) -> list[dict]:
    return [o for o in ops if o.get("status") in CONSUMING]


# ── the admission queue (task 182) ────────────────────────────────────
#
# A launch request — implementation work on a brief, a review or a repair
# of one head — waits here, durably, from the first pass that finds it
# eligible but for capacity, until it is admitted (its operation begun) or
# stops being wanted. Admission is first come, first served per pool:
# `seq`, the order this ledger assigned (ties within one pass broken by
# card number, file and action), then the id — never the wall clock. A restart,
# a poll or the order scopes are read in never moves a request forward.
#
# A request on an explicit **hold** (its scope paused, a dependency that
# went back, a declared overlap, a backoff, the circuit breaker, its work
# base) keeps its place and blocks nobody; one that is no longer wanted
# (admitted, its brief or head changed, its card left the stage or the
# scope, its authority withdrawn, its ledger settled) leaves, and comes
# back, if ever, at the back.

QUEUE_VERSION = 1
QUEUE_KIND = "bench-orchestration-queue"
QUEUE_CAP = 2000
REQUEST_RE = re.compile(r"^rq-[0-9a-f]{20}$")


def queue_path() -> Path:
    return config.STATE / "orchestration" / "queue.json"


def request_id(action: str, card: str, basis: str) -> str:
    """One launch request's identity: the action, the card and what it is
    decided against (a brief's revision, or a head). Not the attempt — a
    retry of the same request keeps its place."""
    raw = "\x00".join(("request", action, card, basis or ""))
    return "rq-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _queue_doc() -> dict:
    doc = records.read_json(queue_path(), expect=dict)
    if not doc or doc.get("v") != QUEUE_VERSION or \
            not isinstance(doc.get("entries"), dict):
        return {"v": QUEUE_VERSION, "kind": QUEUE_KIND, "seq": 0, "entries": {}}
    doc["entries"] = {k: v for k, v in doc["entries"].items()
                      if REQUEST_RE.match(k) and isinstance(v, dict)}
    return doc


def queue_entries() -> list[dict]:
    """Every waiting request, in admission order. A read."""
    return queue_order(list(_queue_doc()["entries"].values()))


def queue_key(entry: dict) -> tuple:
    # `seq` alone orders: it is this ledger's own monotonic count, while
    # `readyEpoch` is a wall clock that can step backwards (shown, not sorted)
    return (entry.get("seq") or 0, entry.get("id") or "")


def queue_order(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=queue_key)


def _tiebreak(request: dict) -> tuple:
    number = str(request.get("card") or "")
    return (int(number) if number.isdigit() else 10 ** 9,
            request.get("file") or "", request.get("action") or "")


def queue_sync(source: str, wanted: list[dict],
               held: dict[str, str] | None = None) -> list[dict]:
    """Bring one planner's requests up to date, durably: each `wanted`
    request not yet waiting joins at the back (all joining in one sync
    share its time, ordered by card number, file and action); a waiting
    request named in `held` keeps its place with that hold; every other
    request of this `source` leaves. Returns the queue in order."""
    held = dict(held or {})
    with _LOCK:
        doc = _queue_doc()
        entries = doc["entries"]
        before = records.dumps(doc, indent=None, sort_keys=True)
        now, stamp = time.time(), now_iso()
        keep = set()
        for request in sorted(wanted, key=_tiebreak):
            rid = request["id"]
            keep.add(rid)
            entry = entries.get(rid)
            if entry is None:
                if len(entries) >= QUEUE_CAP:
                    continue
                doc["seq"] = int(doc.get("seq") or 0) + 1
                entry = {"id": rid, "source": source, "readyAt": stamp,
                         "readyEpoch": now, "seq": doc["seq"]}
                entries[rid] = entry
            entry.update({k: request.get(k) for k in
                          ("action", "file", "card", "scope", "pool", "basis")})
            entry["hold"] = None
        for rid, code in held.items():
            if rid in entries:
                keep.add(rid)
                entries[rid]["hold"] = _clip(code)
        for rid in [r for r, e in entries.items()
                    if e.get("source") == source and r not in keep]:
            del entries[rid]
        if records.dumps(doc, indent=None, sort_keys=True) != before:
            records.write_json(queue_path(), doc, durable=True)
    return queue_order(list(entries.values()))


def queue_remove(rid: str | None) -> bool:
    """A request was admitted (its operation begun): it leaves the queue."""
    if not rid or not REQUEST_RE.match(rid):
        return False
    with _LOCK:
        doc = _queue_doc()
        if rid not in doc["entries"]:
            return False
        del doc["entries"][rid]
        records.write_json(queue_path(), doc, durable=True)
    return True


PUBLIC = ("key", "action", "file", "card", "scope", "pool", "attempt",
          "status", "intentAt", "updatedAt", "day", "note", "outcome",
          "agent", "reason", "code", "resolvedBy", "observed", "request")


def public(op: dict) -> dict:
    """What a reader is given: no path, no process id, no environment."""
    return {k: op.get(k) for k in PUBLIC if op.get(k) is not None}


def summary(ops: list[dict] | None = None, limit: int = 40) -> dict:
    """Counts by status, the operations held for a person, and the newest
    few — a read of files on disk; it changes nothing."""
    ops = all_ops() if ops is None else ops
    counts = {s: 0 for s in STATUSES}
    for op in ops:
        counts[op["status"]] += 1
    return {"counts": counts,
            "held": [public(o) for o in ops if o["status"] == "unknown"],
            "open": [public(o) for o in ops if o["status"] in OPEN],
            "recent": [public(o) for o in ops[-limit:]][::-1]}
