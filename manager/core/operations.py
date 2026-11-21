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
          note: str | None = None, retry: dict | None = None) -> dict:
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


PUBLIC = ("key", "action", "file", "card", "scope", "pool", "attempt",
          "status", "intentAt", "updatedAt", "day", "note", "outcome",
          "agent", "reason", "code", "resolvedBy", "observed")


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
