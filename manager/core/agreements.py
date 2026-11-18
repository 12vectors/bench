"""Operating agreements: what an orchestrator scope may do, and how much.

Task 137 (phase 136). One vendor-neutral model for both scopes an
orchestrator runs at — the **Bench** scope, which manages standalone cards
and phase scopes, and a **phase** scope, which manages one phase card's
members. Two levels and no more: a phase entry naming phases of its own is
refused rather than read as a hierarchy.

This module is the low half: the document, its validation, its revision,
and the *effective* reading that the scheduler and the page both read. It
decides nothing about a card, launches nothing and asks no other part of
bench anything; `orchestrator` composes it with ownership, the board and
the merge rules. It imports `config`, `records` and `redact` only.

**Absent means off.** No document is a board on which orchestration does
not exist: no scope, no authority, nothing scheduled, no card or run
touched. A fresh install is off until a person writes a draft, previews
it and applies it. There is no shadow mode and none is required: an
applied agreement whose Bench scope is enabled can authorize launches.

**Narrowing only.** A phase's authority is the intersection of its own and
its parent's, and a phase asking for something its parent does not grant
is *refused* (`authority-expansion`), not quietly intersected away, so the
person who wrote it sees that it would not have happened. Per-card and
per-incident ceilings may only be lowered (`ceiling-expansion`). Pooled
capacity — slots and daily starts — is **allocated** from the parent's
total: the phases' allocations may not sum past it
(`capacity-overcommitted`), and the Bench scope's own share is what is
left, so a slot is never counted twice.

**Fail closed.** A document that does not parse, declares another version,
or carries any refusal resolves every scope off, with the problems listed.
An operator mid-edit is never handed half of what they were writing.

**Draft, preview, apply.** The effective document is machine-local, like
the execution policy (`manager/local/orchestration.json`). A draft lives under the tool's state until
it is applied; preview compares the two resolutions and writes nothing;
apply is bound to the draft revision that was previewed and to the
effective revision the page read, and is refused rather than merged when
either moved. Pause, resume, exclusion and hand-back are operational
toggles a person makes directly on the effective document, under the same
revision guard. None of these writes touches a card, a branch, a worktree
or a run.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import config
import records
import redact

KIND = "bench-orchestration-agreement"
VERSION = 1

BENCH = "bench"
SCOPE_KINDS = ("bench", "phase")

# What a scope may be delegated to do. Each is a yes/no, and the Bench
# scope grants nothing it does not name: an applied agreement that leaves
# a word out has not delegated it.
#
#   readiness   check a card's context and inputs before work
#   reasoning   launch a bounded coding-agent job to interpret, plan or
#               draft questions (D2) — never a hidden model call
#   implement   launch implementation work
#   review      launch an independent review (D3)
#   repair      launch a repair round after review or a failing check
#   recovery    launch a bounded diagnostic/recovery agent (D5)
#   integrate   merge a phase member into its phase branch
#   merge       complete a card or a phase into its target under the
#               merge rules the user already configured (D4)
AUTHORITIES = ("readiness", "reasoning", "implement", "review", "repair",
               "recovery", "integrate", "merge")

# Pooled capacity: one total, allocated between scopes, never duplicated.
POOLED = ("implementation", "review", "reasoning", "dailyStarts")
# Ceilings: limits per card or per incident, which a child may only lower.
CEILINGS = ("launchesPerCard", "repairRounds", "transientRetries",
            "runAgeMinutes")

# D7, accepted as the initial ceilings. Written into every new draft
# explicitly rather than implied, so the page shows them as policy a
# person can edit. `reasoning` 0 means reasoning and recovery launches
# have no slots of their own and take implementation slots — said so on
# the page, never hidden.
DEFAULTS = {
    "implementation": 2,
    "review": 1,
    "reasoning": 0,
    "dailyStarts": 20,
    "launchesPerCard": 6,
    "repairRounds": 2,
    "transientRetries": 2,
    "runAgeMinutes": 120,
}
LIMITS = {key: (0, 1440 if key == "runAgeMinutes" else 1000) for key in DEFAULTS}

SCOPE_KEYS = {"enabled", "paused", "authority", "capacity", "exclusions",
              "handback", "note"}
PHASE_KEYS = {"enabled", "paused", "authority", "allocation", "ceilings",
              "exclusions", "handback", "note"}
TOP_KEYS = {"v", "kind", "bench", "phases", "_doc"}
NOTE_CAP = 200
LIST_CAP = 500
CARD_RE = re.compile(r"^\d{1,6}$")

PROBLEMS = {
    "agreement-unreadable": "the document is not JSON, or not an object",
    "agreement-version": "the document declares a version this bench does not read",
    "agreement-invalid": "a value is not of the kind its key takes",
    "agreement-unknown-key": "a key this version gives no meaning",
    "nesting-refused": "a phase scope may not hold scopes of its own — two levels only",
    "authority-expansion": "a phase asks for authority its parent does not grant",
    "ceiling-expansion": "a phase raises a ceiling above its parent's",
    "capacity-overcommitted": "the phases' allocations exceed the parent's total",
}

_LOCK = records.InterprocessLock(lambda: state_dir() / "agreement.lock")


class Refused(Exception):
    """A change this module will not make, with the status an API answers."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── where things live ─────────────────────────────────────────────────

def document_path() -> Path:
    return config.LOCAL / "orchestration.json"


def state_dir() -> Path:
    return config.STATE / "orchestration"


def draft_path() -> Path:
    return state_dir() / "draft.json"


def history_path() -> Path:
    return state_dir() / "agreement-history.jsonl"


# ── reading ───────────────────────────────────────────────────────────

def revision(doc) -> str:
    """A short digest of the document's canonical form; `none` for none."""
    if doc is None:
        return "none"
    text = records.dumps(doc, indent=None, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read(path: Path):
    """(document or None, problem or None). Missing is `(None, None)`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"the agreement could not be read: {type(exc).__name__}"
    doc = records.read_json(path)
    if not isinstance(doc, dict):
        return {}, "the agreement is not a JSON object" if raw.strip() else \
            "the agreement file is empty"
    return doc, None


def effective_document():
    return _read(document_path())


def draft_document():
    return _read(draft_path())


def new_draft() -> dict:
    """What a person starts from: the Bench scope off, nothing delegated,
    the D7 ceilings written out. Saving it changes nothing that runs."""
    return {
        "v": VERSION, "kind": KIND,
        "bench": {"enabled": False, "paused": False,
                  "authority": {word: False for word in AUTHORITIES},
                  "capacity": dict(DEFAULTS),
                  "exclusions": [], "handback": []},
        "phases": {},
    }


# ── validation ────────────────────────────────────────────────────────

def _problem(found: list, code: str, where: str, message: str) -> None:
    found.append({"code": code, "path": where, "message": message})


def _cards(value, where: str, found: list) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > LIST_CAP:
        _problem(found, "agreement-invalid", where,
                 f"{where} is a list of card numbers")
        return []
    cards = []
    for item in value:
        text = str(item).lstrip("#") if isinstance(item, (str, int)) and \
            not isinstance(item, bool) else ""
        if not CARD_RE.match(text):
            _problem(found, "agreement-invalid", where,
                     f"{where} holds {redact.scrub(str(item)[:40], '••••')!r}, "
                     f"which is not a card number")
            continue
        number = str(int(text))
        if number not in cards:
            cards.append(number)
    return cards


def _bools(value, where: str, found: list, keys) -> dict[str, bool]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _problem(found, "agreement-invalid", where, f"{where} is an object")
        return {}
    out = {}
    for key, flag in value.items():
        if key not in keys:
            _problem(found, "agreement-unknown-key", f"{where}.{key}",
                     f"{where} has no word {key!r}; the words are "
                     + ", ".join(keys))
        elif not isinstance(flag, bool):
            _problem(found, "agreement-invalid", f"{where}.{key}",
                     f"{where}.{key} is true or false")
        else:
            out[key] = flag
    return out


def _counts(value, where: str, found: list, keys) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _problem(found, "agreement-invalid", where, f"{where} is an object")
        return {}
    out = {}
    for key, number in value.items():
        if key not in keys:
            _problem(found, "agreement-unknown-key", f"{where}.{key}",
                     f"{where} has no key {key!r}; the keys are " + ", ".join(keys))
            continue
        low, high = LIMITS[key]
        if isinstance(number, bool) or not isinstance(number, int) \
                or not low <= number <= high:
            _problem(found, "agreement-invalid", f"{where}.{key}",
                     f"{where}.{key} is a whole number from {low} to {high}")
            continue
        out[key] = number
    return out


def _flag(scope: dict, key: str, where: str, found: list) -> bool:
    value = scope.get(key, False)
    if not isinstance(value, bool):
        _problem(found, "agreement-invalid", f"{where}.{key}",
                 f"{where}.{key} is true or false")
        return False
    return value


def _note(scope: dict, where: str, found: list) -> str | None:
    note = scope.get("note")
    if note is None:
        return None
    if not isinstance(note, str) or len(note) > NOTE_CAP:
        _problem(found, "agreement-invalid", f"{where}.note",
                 f"{where}.note is text of at most {NOTE_CAP} characters")
        return None
    return redact.scrub(note, "••••")


def _unknown(scope: dict, allowed: set, where: str, found: list) -> None:
    for key in scope:
        if key == "phases" and where != "bench":
            _problem(found, "nesting-refused", f"{where}.phases",
                     f"{where} names scopes of its own; a phase scope manages "
                     f"its members and nothing nests under it")
        elif key not in allowed:
            _problem(found, "agreement-unknown-key", f"{where}.{key}",
                     f"{where} has no key {key!r}")


def parse(doc) -> dict:
    """The document read whole: every value normalised, every refusal
    listed with its code and the key it is about. Changes nothing."""
    found: list[dict] = []
    if not isinstance(doc, dict):
        _problem(found, "agreement-unreadable", "", PROBLEMS["agreement-unreadable"])
        return {"problems": found, "bench": None, "phases": {}}
    if doc.get("v") != VERSION:
        _problem(found, "agreement-version", "v",
                 f"the agreement declares version {doc.get('v')!r}; this bench "
                 f"reads version {VERSION}")
    for key in doc:
        if key not in TOP_KEYS:
            _problem(found, "agreement-unknown-key", key,
                     f"the agreement has no top-level key {key!r}")

    raw = doc.get("bench")
    if not isinstance(raw, dict):
        _problem(found, "agreement-invalid", "bench",
                 "the agreement names its Bench scope under \"bench\"")
        raw = {}
    _unknown(raw, SCOPE_KEYS, "bench", found)
    capacity = dict(DEFAULTS)
    capacity.update(_counts(raw.get("capacity"), "bench.capacity", found,
                            tuple(DEFAULTS)))
    bench = {
        "id": BENCH, "kind": "bench", "parent": None,
        "enabled": _flag(raw, "enabled", "bench", found),
        "paused": _flag(raw, "paused", "bench", found),
        "authority": {w: False for w in AUTHORITIES}
        | _bools(raw.get("authority"), "bench.authority", found, AUTHORITIES),
        "capacity": capacity,
        "exclusions": _cards(raw.get("exclusions"), "bench.exclusions", found),
        "handback": _cards(raw.get("handback"), "bench.handback", found),
        "note": _note(raw, "bench", found),
    }

    phases = {}
    rawphases = doc.get("phases", {})
    if not isinstance(rawphases, dict):
        _problem(found, "agreement-invalid", "phases",
                 "\"phases\" maps a phase card's number to its agreement")
        rawphases = {}
    for key, entry in rawphases.items():
        number = str(key).lstrip("#")
        where = f"phases.{key}"
        if not CARD_RE.match(number):
            _problem(found, "agreement-invalid", where,
                     f"{key!r} is not a phase card's number")
            continue
        number = str(int(number))
        if not isinstance(entry, dict):
            _problem(found, "agreement-invalid", where, f"{where} is an object")
            continue
        _unknown(entry, PHASE_KEYS, where, found)
        authority = _bools(entry.get("authority"), f"{where}.authority",
                           found, AUTHORITIES)
        for word, wanted in authority.items():
            if wanted and not bench["authority"][word]:
                _problem(found, "authority-expansion", f"{where}.authority.{word}",
                         f"phase {number} asks for {word!r}, which the Bench "
                         f"scope does not grant; a phase can only narrow its "
                         f"parent's authority")
        ceilings = _counts(entry.get("ceilings"), f"{where}.ceilings", found,
                           CEILINGS)
        for name, value in ceilings.items():
            if value > capacity[name]:
                _problem(found, "ceiling-expansion", f"{where}.ceilings.{name}",
                         f"phase {number} sets {name} to {value}, above the "
                         f"Bench scope's {capacity[name]}")
        phases[number] = {
            "id": f"phase:{number}", "kind": "phase", "parent": BENCH,
            "card": number,
            "enabled": _flag(entry, "enabled", where, found),
            "paused": _flag(entry, "paused", where, found),
            "authority": authority,
            "allocation": _counts(entry.get("allocation"), f"{where}.allocation",
                                  found, POOLED),
            "ceilings": ceilings,
            "exclusions": _cards(entry.get("exclusions"), f"{where}.exclusions", found),
            "handback": _cards(entry.get("handback"), f"{where}.handback", found),
            "note": _note(entry, where, found),
        }
    for pool in POOLED:
        allocated = sum(p["allocation"].get(pool, 0) for p in phases.values())
        if allocated > capacity[pool]:
            _problem(found, "capacity-overcommitted", f"phases.*.allocation.{pool}",
                     f"the phases are allocated {allocated} {pool} between them, "
                     f"more than the Bench scope's total of {capacity[pool]}; "
                     f"allocations share the parent's total rather than adding "
                     f"to it")
    return {"problems": found, "bench": bench, "phases": phases}


def validate(doc) -> dict:
    parsed = parse(doc)
    return {"ok": not parsed["problems"], "problems": parsed["problems"],
            "revision": revision(doc)}


# ── the effective reading ─────────────────────────────────────────────

def resolve(doc, problem: str | None = None) -> dict:
    """Every scope as it is in force: authority with the scope that
    decided each word, pooled capacity as allocated, ceilings, pause and
    enablement as inherited. A missing document is `configured: False`
    and every scope is off; one with any refusal is `valid: False` and
    every scope is off too — the problems say why."""
    if doc is None and problem is None:
        return {"configured": False, "valid": True, "problems": [],
                "revision": "none", "scopes": []}
    parsed = parse(doc) if problem is None else {
        "problems": [{"code": "agreement-unreadable", "path": "",
                      "message": problem}], "bench": None, "phases": {}}
    valid = not parsed["problems"]
    bench = parsed["bench"]
    scopes = []
    if bench is not None:
        bench_on = valid and bench["enabled"]
        allocated = {pool: sum(p["allocation"].get(pool, 0)
                               for p in parsed["phases"].values())
                     for pool in POOLED}
        scopes.append({
            "id": BENCH, "kind": "bench", "parent": None, "card": None,
            "enabled": bench["enabled"], "active": bench_on and not bench["paused"],
            "paused": bench["paused"], "pausedBy": BENCH if bench["paused"] else None,
            "off": None if bench_on else ("invalid" if not valid else "disabled"),
            "authority": {w: {"allowed": valid and bench["authority"][w], "by": BENCH}
                          for w in AUTHORITIES},
            "capacity": {pool: {"total": bench["capacity"][pool],
                                "allocated": allocated[pool],
                                "own": max(0, bench["capacity"][pool] - allocated[pool])}
                         for pool in POOLED},
            "ceilings": {c: {"value": bench["capacity"][c], "by": BENCH}
                         for c in CEILINGS},
            "exclusions": bench["exclusions"], "handback": bench["handback"],
            "note": bench["note"],
        })
        for number, phase in sorted(parsed["phases"].items(),
                                    key=lambda kv: int(kv[0])):
            authority = {}
            for word in AUTHORITIES:
                parent = bench["authority"][word]
                own = phase["authority"].get(word, True)
                authority[word] = {
                    "allowed": valid and parent and own,
                    "by": BENCH if not parent else
                          (phase["id"] if word in phase["authority"] else BENCH)}
            on = bench_on and phase["enabled"]
            paused_by = BENCH if bench["paused"] else (
                phase["id"] if phase["paused"] else None)
            scopes.append({
                "id": phase["id"], "kind": "phase", "parent": BENCH,
                "card": number, "enabled": phase["enabled"],
                "active": on and paused_by is None,
                "paused": phase["paused"], "pausedBy": paused_by,
                "off": None if on else ("invalid" if not valid else
                                        "parent-disabled" if not bench_on
                                        else "disabled"),
                "authority": authority,
                "capacity": {pool: {"total": phase["allocation"].get(pool, 0),
                                    "allocated": 0,
                                    "own": phase["allocation"].get(pool, 0),
                                    "from": BENCH}
                             for pool in POOLED},
                "ceilings": {c: ({"value": phase["ceilings"][c], "by": phase["id"]}
                                 if c in phase["ceilings"] else
                                 {"value": bench["capacity"][c], "by": BENCH})
                             for c in CEILINGS},
                "exclusions": phase["exclusions"], "handback": phase["handback"],
                "note": phase["note"],
            })
    return {"configured": True, "valid": valid, "problems": parsed["problems"],
            "revision": revision(doc), "scopes": scopes}


def effective() -> dict:
    doc, problem = effective_document()
    return resolve(doc, problem)


def scope(reading: dict, scope_id: str) -> dict | None:
    return next((s for s in reading["scopes"] if s["id"] == scope_id), None)


def compare(before: dict, after: dict) -> list[dict]:
    """What applying `after` over `before` would change, scope by scope,
    in words a person reads. Two resolutions, never a prediction."""
    changes = []
    if before["valid"] != after["valid"]:
        changes.append({"scope": None, "what": "validity",
                        "before": before["valid"], "after": after["valid"]})
    old = {s["id"]: s for s in before["scopes"]}
    new = {s["id"]: s for s in after["scopes"]}
    for sid in sorted(set(old) | set(new)):
        a, b = old.get(sid), new.get(sid)
        if a is None or b is None:
            changes.append({"scope": sid, "what": "scope",
                            "before": a is not None, "after": b is not None})
            continue
        for key in ("active", "paused", "pausedBy", "off", "exclusions",
                    "handback"):
            if a[key] != b[key]:
                changes.append({"scope": sid, "what": key,
                                "before": a[key], "after": b[key]})
        for word in AUTHORITIES:
            if a["authority"][word]["allowed"] != b["authority"][word]["allowed"]:
                changes.append({"scope": sid, "what": f"authority.{word}",
                                "before": a["authority"][word]["allowed"],
                                "after": b["authority"][word]["allowed"]})
        for pool in POOLED:
            if a["capacity"][pool]["own"] != b["capacity"][pool]["own"] or \
                    a["capacity"][pool]["total"] != b["capacity"][pool]["total"]:
                changes.append({"scope": sid, "what": f"capacity.{pool}",
                                "before": a["capacity"][pool],
                                "after": b["capacity"][pool]})
        for name in CEILINGS:
            if a["ceilings"][name]["value"] != b["ceilings"][name]["value"]:
                changes.append({"scope": sid, "what": f"ceilings.{name}",
                                "before": a["ceilings"][name]["value"],
                                "after": b["ceilings"][name]["value"]})
    return changes


# ── writing ───────────────────────────────────────────────────────────

def _guard(current, expected: str | None, what: str) -> None:
    now = revision(current)
    if expected is None:
        raise Refused(f"{what} names no revision; read the agreement first",
                      400, current=now)
    if expected != now:
        raise Refused(f"the {what} changed since it was read ({expected} → {now}); "
                      f"read it again", 409, stale=True, current=now)


def _history(entry: dict) -> None:
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(records.dumps(entry, indent=None) + "\n")


def save_draft(doc, expected: str | None) -> dict:
    """Keep a draft. An invalid draft is kept too — saving is not
    applying — and its problems come back with it."""
    if not isinstance(doc, dict):
        raise Refused("a draft is a JSON object", 400)
    with _LOCK:
        current, _ = draft_document()
        _guard(current, expected, "draft")
        records.write_json(draft_path(), doc)
    return {"draft": revision(doc), **validate(doc)}


def discard_draft(expected: str | None) -> dict:
    with _LOCK:
        current, _ = draft_document()
        if current is None:
            return {"draft": "none", "discarded": False}
        _guard(current, expected, "draft")
        draft_path().unlink()
    return {"draft": "none", "discarded": True}


def preview(doc=None) -> dict:
    """The draft (or `doc`) beside what is in force. Writes nothing."""
    if doc is None:
        doc, problem = draft_document()
        if doc is None and problem is None:
            raise Refused("there is no draft to preview", 404)
        after = resolve(doc, problem)
    else:
        after = resolve(doc)
    before = effective()
    return {"effective": before["revision"], "draft": after["revision"],
            "valid": after["valid"], "problems": after["problems"],
            "changes": compare(before, after)}


def apply(expected_effective: str | None, expected_draft: str | None,
          actor: str | None = None) -> dict:
    """Put the previewed draft in force. Refused, with nothing written,
    when the draft is invalid or either revision moved since the page
    read it. Writes one file; starts, moves and cancels nothing."""
    with _LOCK:
        draft, problem = draft_document()
        if draft is None:
            raise Refused("there is no draft to apply", 404)
        if problem:
            raise Refused(problem, 409)
        _guard(draft, expected_draft, "draft")
        current, _ = effective_document()
        _guard(current, expected_effective, "agreement")
        checked = validate(draft)
        if not checked["ok"]:
            raise Refused("the draft has problems, so it is not applied", 409,
                          problems=checked["problems"])
        before = resolve(current) if current is not None else resolve(None)
        path = document_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        records.write_json(path, draft, durable=True)
        draft_path().unlink()
        after = resolve(draft)
        changes = compare(before, after)
        _history({"action": "apply", "before": before["revision"],
                  "after": after["revision"], "actor": actor or "unknown",
                  "changed": sorted({c["what"] for c in changes})})
    return {"revision": after["revision"], "changes": changes}


SCOPE_ACTIONS = ("pause", "resume", "exclude", "include", "handback", "reclaim")


def set_scope(scope_id: str, action: str, expected: str | None,
              card: str | None = None, actor: str | None = None) -> dict:
    """A person's operational change to the effective agreement: pause or
    resume a scope, exclude a card from it (or include it again), or hand
    a card back to manual work (or reclaim it). Hand-back changes nothing
    on the card — no branch, worktree or run is reset or stopped; it only
    stops this scope from acting on it."""
    if action not in SCOPE_ACTIONS:
        raise Refused(f"{action!r} is not one of " + ", ".join(SCOPE_ACTIONS), 400)
    with _LOCK:
        current, problem = effective_document()
        if current is None or problem:
            raise Refused("no agreement is in force; apply one first", 409)
        _guard(current, expected, "agreement")
        doc = records.read_json(document_path())
        if scope_id == BENCH:
            entry = doc.setdefault("bench", {})
        elif scope_id.startswith("phase:") and \
                isinstance(doc.get("phases"), dict) and \
                scope_id[6:] in doc["phases"]:
            entry = doc["phases"][scope_id[6:]]
        else:
            raise Refused(f"no scope {scope_id!r} in the agreement", 404)
        if not isinstance(entry, dict):
            raise Refused(f"scope {scope_id!r} is not an object", 409)
        if action in ("pause", "resume"):
            entry["paused"] = action == "pause"
        else:
            if card is None or not CARD_RE.match(str(card).lstrip("#")):
                raise Refused(f"{action} names a card number", 400)
            number = str(int(str(card).lstrip("#")))
            key = "exclusions" if action in ("exclude", "include") else "handback"
            listed = [str(int(str(c).lstrip("#"))) for c in entry.get(key) or []
                      if CARD_RE.match(str(c).lstrip("#"))]
            if action in ("exclude", "handback"):
                if number not in listed:
                    listed.append(number)
            else:
                listed = [c for c in listed if c != number]
            entry[key] = listed
        checked = validate(doc)
        if not checked["ok"]:
            raise Refused("the change would leave the agreement invalid", 409,
                          problems=checked["problems"])
        records.write_json(document_path(), doc, durable=True)
        _history({"action": action, "scope": scope_id, "card": card,
                  "before": revision(current), "after": revision(doc),
                  "actor": actor or "unknown"})
    return {"revision": revision(doc), "scope": scope_id, "action": action,
            "card": card}
