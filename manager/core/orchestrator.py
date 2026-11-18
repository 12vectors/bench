"""The shared scoped orchestration engine: deterministic authority (task 137).

One engine, instantiated at two scopes — the Bench scope and a phase
scope (D6). This module is its deterministic half: given the board, the
operating agreement in force (`agreements`), who executes
(`execowner`) and the merge rules the user configured (`gates`), it
answers **may this action happen now, here, for this card** — and why
not. Semantic reasoning (interpreting a brief, planning, drafting
questions, proposing a recovery) is never done here: it is a launched
coding-agent job (D2), and the launch is an action this module
authorizes like any other. The scheduler (`scheduler`, task 141) asks it
before every side effect; the durable operation ledger (`operations`) is
read here so a reserved launch occupies its slot and spends its start
before anything runs.

**Every action is authorized again when it is about to happen.** Nothing
here caches a grant: the agreement is read from disk on every call, so a
pause, an exclusion or an applied revision reaches the very next action.
That is a different fact from what a run already in flight was granted —
a run keeps the execution profile frozen in its manifest at launch, and
pausing a scope neither changes nor cancels it; its ending is observed.

**One scope per card.** A standalone card belongs to the Bench scope. A
phase card and its members belong to that phase's scope when the
agreement names the phase; otherwise the phase is run by the existing
phase runner and *no* orchestrator scope schedules it — two schedulers
over the same members are refused by construction (`legacy-phase`).

**Merge delegation is the user's (D4).** A scope granted `merge` (or
`integrate`, for a member into its phase branch) completes under the
merge rules already configured, and only under them: a configured
requirement that is unmet refuses (`requirements-unmet`), a running one
holds (`requirements-pending`), and an ungated rule allows the merge with
the label *ungated* — nothing required, nothing verified. Nothing here
activates a gate, grants an override or reads agent prose as evidence.

**Last mover, not last editor (D1).** A card is acted on only when its
latest stage move by a person was this board's identity — asked of
`movements.eligibility`, the same answer `reading()` carries per card,
so a launch and the page cannot disagree (task 138).

**Reading is free.** `reading()` — what `GET /api/orchestration` serves —
reads the agreement, the draft, the designation and the run registry. It
launches nothing, runs no check, asks no remote and writes nothing.
"""

from __future__ import annotations

import json
import time

import agreements
import execowner
import gatepolicy
import gates
import movements
import operations
import records
import state
import taskfiles

KIND = "bench-orchestration"
VERSION = 1

# An action, and the authority word that delegates it.
ACTIONS = {
    "readiness": "readiness",
    "reason": "reasoning",
    "implement": "implement",
    "review": "review",
    # Asking bench for the candidate's required checks (task 142). Not a
    # launch — no slot, no start — and delegated by the same word as the
    # independent review it precedes: both are assessing the exact head.
    "verify": "review",
    "repair": "repair",
    "recover": "recovery",
    "integrate": "integrate",
    "merge": "merge",
}
# An action that launches a coding agent, and the pool its slot comes
# from. Reasoning and recovery launches are real usage (D7): they take a
# reasoning slot where one is allocated, and an implementation slot
# otherwise — never nothing.
LAUNCHES = {"reason": "reasoning", "recover": "reasoning",
            "implement": "implementation", "repair": "implementation",
            "review": "review"}
# Which pool a running agent occupies, by the mode its record carries.
MODE_POOL = {"review": "review", "refine": "reasoning", "reason": "reasoning"}
# The stages an orchestrator acts in. Backlog is the owner's space and
# Done is delivered: neither is ever pulled back silently.
MANAGED_STAGES = ("to-do", "in-progress", "review")
WORKING_STAGES = ("in-progress", "review")


def ledger_path():
    return agreements.state_dir() / "launches.jsonl"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ── the board, as scopes see it ───────────────────────────────────────

def _tasks(board: dict | None) -> list[dict]:
    board = taskfiles.collect() if board is None else board
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def scope_of(task: dict, reading: dict) -> tuple[str | None, str | None]:
    """(scope id, why not) for one card under one effective reading."""
    phase = None
    if task.get("isPhase"):
        phase = _number(task)
    elif task.get("phase"):
        phase = taskfiles.canonical_number(task["phase"]["number"])
    if phase is None:
        return agreements.BENCH, None
    sid = f"phase:{phase}"
    if agreements.scope(reading, sid) is None:
        return None, "legacy-phase"
    return sid, None


def pool_for(word: str, scope_reading: dict) -> str:
    """Reasoning without slots of its own takes an implementation slot."""
    if word == "reasoning" and scope_reading["capacity"]["reasoning"]["own"] == 0:
        return "implementation"
    return word


def usage(reading: dict, tasks: list[dict], exclude: str | None = None) -> dict:
    """What is running and launched, per scope and pool. Every running
    agent counts — a person's own launch, an agent this board did not
    schedule, a run of a card no scope manages — because capacity is an
    observation of what is actually running, never a reason to cancel
    one to make a limit look met."""
    by_file = {t["file"]: t for t in tasks}
    running: dict = {}
    unscoped = {pool: 0 for pool in agreements.POOLED if pool != "dailyStarts"}
    totals = dict(unscoped)
    with state.LOCK:
        records_ = [dict(r) for r in state.AGENTS.values()
                    if r.get("status") == "running"]
    for record in records_:
        pool = MODE_POOL.get(record.get("mode") or "work", "implementation")
        task = by_file.get(record.get("task"))
        sid = scope_of(task, reading)[0] if task else None
        scope_reading = agreements.scope(reading, sid) if sid else None
        if scope_reading is not None:
            pool = pool_for(pool, scope_reading)
        elif pool == "reasoning":
            pool = "implementation"
        totals[pool] = totals.get(pool, 0) + 1
        if sid is None:
            unscoped[pool] = unscoped.get(pool, 0) + 1
        else:
            running.setdefault(sid, {}).setdefault(pool, 0)
            running[sid][pool] += 1
    # Reservations (task 141): an operation recorded as an intent, held
    # as unknown, or launched by a board that has since restarted and
    # whose process is still alive occupies its slot exactly as a running
    # agent does, so capacity is reserved *before* a launch and never
    # freed by a crash, a restart or a lost receipt.
    # `exclude` is the one operation whose own reservation is being
    # re-authorized: it must not count against itself.
    # Only launches occupy a slot or spend a start: asking for checks and
    # merging (task 142) are operations with intents and receipts too, but
    # no coding agent runs for them.
    ops = [op for op in operations.all_ops() if op["key"] != exclude
           and op.get("action", "implement") in LAUNCHES]
    reserved = 0
    for op in operations.reserved(ops):
        if op.get("status") == "unobserved" and not op.get("alive"):
            continue
        sid, pool = op.get("scope"), op.get("pool") or "implementation"
        if pool not in totals:
            pool = "implementation"
        totals[pool] = totals.get(pool, 0) + 1
        running.setdefault(sid, {}).setdefault(pool, 0)
        running[sid][pool] += 1
        reserved += 1
    starts, per_card = {}, {}
    today = _today()
    for line in _ledger():
        per_card[line["card"]] = per_card.get(line["card"], 0) + 1
        if line.get("day") == today:
            starts[line["scope"]] = starts.get(line["scope"], 0) + 1
    # every operation that may have launched spent a start and a launch of
    # its card; one that provably ran nothing (refused, a transient
    # failure, abandoned on reconciliation) spent neither
    for op in operations.consumed(ops):
        if op.get("card"):
            per_card[op["card"]] = per_card.get(op["card"], 0) + 1
        if op.get("day") == today:
            starts[op["scope"]] = starts.get(op["scope"], 0) + 1
    return {"running": running, "unscoped": unscoped, "totals": totals,
            "reserved": reserved,
            "startsToday": starts, "startsTodayTotal": sum(starts.values()),
            "launchesPerCard": per_card}


def _ledger() -> list[dict]:
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        entry = _loads(line)
        if isinstance(entry, dict) and isinstance(entry.get("card"), str) \
                and isinstance(entry.get("scope"), str):
            found.append(entry)
    return found


def _loads(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


def record_launch(scope_id: str, card: str, action: str, operation: str) -> dict:
    """Count one launch an orchestrator made, against its card and its
    scope's daily starts. Called once the launch is authorized and made;
    reasoning and recovery launches are counted the same as any other."""
    entry = {"v": 1, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "day": _today(), "scope": scope_id, "card": card,
             "action": action, "operation": operation}
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(records.dumps(entry, indent=None) + "\n")
    return entry


# ── authorization ─────────────────────────────────────────────────────

def _refuse(code: str, reason: str, **extra) -> dict:
    return {"allowed": False, "code": code, "reason": reason, **extra}


def authorize(action: str, filename: str, *, board: dict | None = None,
              gate_reading: dict | None = None,
              operation: str | None = None) -> dict:
    """May the orchestrator take `action` on `filename` now?

    The answer, never cached, with the code and the scope that decided:
    ownership first (designated here, lock held), then the agreement in
    force, the card's one scope, enablement and pause (a parent's pause
    reaching its phases), exclusion and hand-back, the stage, adoption
    after a handover, the authority word (an intersection, with who
    decided), capacity (slots, daily starts, launches per card), and for a
    merge the merge rules the user configured. `gate_reading` is a test's
    seam for `gates.evaluate`; the mutation path passes nothing.
    `operation` names an operation already reserved for this very action
    (task 141): re-authorizing it immediately before its side effect must
    not count its own reservation against it."""
    if action not in ACTIONS:
        return _refuse("unknown-action", f"{action!r} is not an orchestrator action")
    owner = execowner.authorizes()
    if not owner["allowed"]:
        return _refuse(owner["code"], owner["reason"], action=action, card=filename)
    reading = agreements.effective()
    if not reading["configured"]:
        return _refuse("off", "no operating agreement is in force; "
                       "orchestration is off on this board", action=action,
                       card=filename)
    if not reading["valid"]:
        return _refuse("agreement-invalid", "the agreement in force has "
                       "problems, so every scope is off", action=action,
                       card=filename, problems=reading["problems"])
    tasks = _tasks(board)
    task = next((t for t in tasks if t["file"] == filename), None)
    if task is None:
        return _refuse("card-unknown", f"{filename} is not on this board",
                       action=action, card=filename)
    sid, why = scope_of(task, reading)
    if sid is None:
        return _refuse(why, "this card's phase has no phase agreement, so the "
                       "existing phase runner schedules it and no orchestrator "
                       "scope may", action=action, card=filename)
    here = agreements.scope(reading, sid)
    base = {"action": action, "card": filename, "scope": sid}
    if here["off"]:
        return _refuse(here["off"], {
            "disabled": f"the {sid} scope is not enabled",
            "parent-disabled": "the Bench scope is not enabled, so no phase "
                               "scope runs",
        }.get(here["off"], f"the {sid} scope is off"), **base)
    if here["pausedBy"]:
        return _refuse("paused", f"the {here['pausedBy']} scope is paused; new "
                       f"work is not scheduled, and work in flight is observed, "
                       f"not cancelled", pausedBy=here["pausedBy"], **base)
    number = _number(task)
    chain = [here] + ([agreements.scope(reading, agreements.BENCH)]
                      if here["kind"] == "phase" else [])
    for owner_scope in chain:
        if number in owner_scope["exclusions"]:
            return _refuse("excluded", f"card {number} is excluded from the "
                           f"{owner_scope['id']} scope", by=owner_scope["id"], **base)
        if number in owner_scope["handback"]:
            return _refuse("handed-back", f"card {number} was handed back to "
                           f"manual work in the {owner_scope['id']} scope; its "
                           f"branch and worktree are the user's",
                           by=owner_scope["id"], **base)
    if task["stage"] not in MANAGED_STAGES:
        return _refuse("stage-not-managed", f"{task['stage']}/ is not an "
                       f"orchestrated stage; Backlog is the owner's and Done is "
                       f"delivered", **base)
    # D1: the card is this board's only when its latest stage move by a
    # person was this board's identity (task 138) — the one resolver the
    # page reads too, so the two cannot disagree
    eligible = movements.eligibility(task, tasks)
    if not eligible["eligible"]:
        return _refuse("not-eligible", f"card {number} is not this board's to "
                       f"orchestrate: {eligible['reason']}",
                       eligibility=eligible, **base)
    designation = execowner.reading()
    if designation["adoptionRequired"] and task["stage"] in WORKING_STAGES \
            and number not in designation["adoptedCards"]:
        return _refuse("adoption-required", f"execution was adopted from "
                       f"another installation and card {number} was already "
                       f"working; reconcile and adopt it explicitly first", **base)
    word = ACTIONS[action]
    grant = here["authority"][word]
    if not grant["allowed"]:
        return _refuse("not-authorized", f"the agreement does not delegate "
                       f"{word!r} here (decided by {grant['by']})",
                       by=grant["by"], **base)
    provenance = [f"{word}: granted by {grant['by']}"]
    if action in LAUNCHES:
        refusal = _capacity(action, here, reading, tasks, number, operation)
        if refusal:
            return {**refusal, **base}
    label = None
    if action in ("merge", "integrate"):
        gate_scope = "member" if action == "integrate" else gates.scope_for(task)
        decision = gate_reading if gate_reading is not None else \
            gates.evaluate(filename, gate_scope, task=task)
        word_ = decision.get("decision")
        if word_ == "blocked":
            return _refuse("requirements-unmet", "a merge requirement you "
                           "configured is unmet; delegation never bypasses it",
                           gate=gate_scope, **base)
        if word_ == "pending":
            return _refuse("requirements-pending", "a required check is "
                           "running; nothing is decided from its absence",
                           gate=gate_scope, **base)
        if word_ not in ("ungated", "allowed", "overridden"):
            return _refuse("requirements-unknown", "the merge rules could not "
                           "be read", gate=gate_scope, **base)
        label = {"ungated": "ungated — nothing is required by your policy, so "
                            "this merge is not verified",
                 "allowed": "every requirement you configured is met",
                 "overridden": "you overrode unmet requirements; not a pass",
                 }[word_]
        provenance.append(f"merge rules ({gate_scope}): {word_}")
    provenance.append(f"eligibility: {eligible['code']} ({eligible['via']})")
    return {"allowed": True, "code": "authorized",
            "reason": f"{word} is delegated to the {sid} scope", "label": label,
            "provenance": provenance, "eligibility": eligible, **base}


def _capacity(action: str, here: dict, reading: dict, tasks: list[dict],
              number: str | None, operation: str | None = None) -> dict | None:
    used = usage(reading, tasks, exclude=operation)
    pool = pool_for(LAUNCHES[action], here)
    bench = agreements.scope(reading, agreements.BENCH)
    own = here["capacity"][pool]["own"]
    mine = used["running"].get(here["id"], {}).get(pool, 0)
    if mine >= own:
        return _refuse("slots-full", f"the {here['id']} scope's {pool} slots "
                       f"are in use ({mine} of {own})", pool=pool)
    total = bench["capacity"][pool]["total"]
    if used["totals"].get(pool, 0) >= total:
        return _refuse("slots-full", f"all {total} {pool} slots on this board "
                       f"are in use, counting runs no scope manages",
                       pool=pool)
    starts = here["capacity"]["dailyStarts"]["own"]
    if used["startsToday"].get(here["id"], 0) >= starts:
        return _refuse("daily-starts-exhausted", f"the {here['id']} scope has "
                       f"made its {starts} launches today")
    if used["startsTodayTotal"] >= bench["capacity"]["dailyStarts"]["total"]:
        return _refuse("daily-starts-exhausted", "this board's orchestrators "
                       "have made every launch allowed today")
    ceiling = here["ceilings"]["launchesPerCard"]["value"]
    if number and used["launchesPerCard"].get(number, 0) >= ceiling:
        return _refuse("card-launches-exhausted", f"card {number} has had "
                       f"{ceiling} orchestrated launches, the ceiling")
    return None


# ── the public reading ────────────────────────────────────────────────

def merge_rules() -> dict:
    """What each guarded merge requires, in the words a delegation is
    shown with. Ungated is said as ungated, never as passed."""
    try:
        found = gatepolicy.gate_policy()
    except (OSError, ValueError) as exc:
        return {"readable": False, "why": type(exc).__name__, "scopes": {}}
    out = {}
    for name, rule in (found.get("scopes") or {}).items():
        checks = [c.get("name") or c.get("check") for c in rule.get("checks") or []]
        out[name] = {
            "gated": bool(rule.get("gated")),
            "checks": [c for c in checks if c],
            "review": rule.get("review"),
            "signoff": rule.get("signoff"),
            "label": ("ungated — no checks required by your policy; a delegated "
                      "merge is not verified" if not rule.get("gated") else
                      "a delegated merge waits for every requirement you "
                      "configured"),
        }
    return {"readable": not found.get("blockers"),
            "problems": list(found.get("blockers") or []), "scopes": out}


def _public_scopes(reading: dict) -> list[dict]:
    return [{k: v for k, v in s.items()} for s in reading["scopes"]]


def reading(board: dict | None = None) -> dict:
    """The public effective state: what is in force against what is only
    drafted, every scope's authority with its provenance, the capacity
    allocations and what is using them, who executes, and what a
    delegated merge would wait for. Names no path and carries no value
    from a credential; free text is scrubbed where it is read."""
    effective = agreements.effective()
    draft_doc, draft_problem = agreements.draft_document()
    draft = None
    if draft_doc is not None or draft_problem is not None:
        draft = agreements.resolve(draft_doc, draft_problem)
        draft["changes"] = agreements.compare(effective, draft)
    tasks = _tasks(board)
    owner = execowner.reading()
    if not effective["configured"]:
        status = "off"
    elif not effective["valid"]:
        status = "invalid"
    else:
        bench = agreements.scope(effective, agreements.BENCH)
        status = ("off" if not bench["enabled"] else
                  "paused" if bench["paused"] else "enabled")
    managed = []
    snap = movements.snapshot()   # one identity, one history, every card
    for task in tasks:
        sid, why = scope_of(task, effective) if effective["configured"] else (None, "off")
        if task["stage"] in MANAGED_STAGES:
            managed.append({"card": task["file"], "number": _number(task),
                            "stage": task["stage"], "scope": sid,
                            "why": why,
                            # the same answer authorize() reads (task 138)
                            "eligibility": movements.eligibility(task, tasks,
                                                                 snap)})
    return {
        "kind": KIND, "v": VERSION, "status": status,
        "effective": {"configured": effective["configured"],
                      "valid": effective["valid"],
                      "revision": effective["revision"],
                      "problems": effective["problems"],
                      "scopes": _public_scopes(effective)},
        "draft": None if draft is None else {
            "revision": draft["revision"], "valid": draft["valid"],
            "problems": draft["problems"], "scopes": _public_scopes(draft),
            "changes": draft["changes"],
            "differs": draft["revision"] != effective["revision"]},
        "defaults": dict(agreements.DEFAULTS),
        "authorities": list(agreements.AUTHORITIES),
        "installation": {k: owner[k] for k in (
            "role", "problem", "revision", "designated", "state", "since",
            "adoptedFrom", "adoptionRequired", "adoptedCards", "lockHeld",
            "lockFree", "ownerNote")}
        | {"thisInstallation": owner["installation"],
           "executes": execowner.authorizes()["allowed"]},
        "usage": usage(effective, tasks) if effective["configured"] else None,
        # the durable operation ledger (task 141), as files on disk say it
        "operations": operations.summary(),
        "cards": managed,
        "mergeRules": merge_rules(),
        "grants": ("A run keeps the grants frozen in its manifest at launch. "
                   "The agreement authorizes new actions only, and is read "
                   "again before each one."),
        "notes": _notes(effective),
        "identity": {**snap["identity"],
                     "attribution": movements.NOT_AUTHENTICATION},
    }


def _notes(effective: dict) -> list[str]:
    notes = []
    if not effective["configured"]:
        notes.append("No operating agreement: orchestration is off and no card "
                     "or run is touched.")
    for s in effective["scopes"]:
        if s["kind"] == "bench" and s["capacity"]["reasoning"]["total"] == 0:
            notes.append("Reasoning and recovery launches have no slots of "
                         "their own: they use implementation slots and count "
                         "against daily starts.")
    return notes


# ── a person's changes, through their owners ──────────────────────────

def handover(expected, actor: str | None = None) -> dict:
    """Hand execution over, with quiescence established from what this
    board can see: every run it is tracking must have ended, and every
    operation in the durable ledger must be settled — an intent with no
    receipt, an unknown outcome or a run whose ending was not observed is
    outstanding, and the handover is refused until it is reconciled
    (task 141)."""
    with state.LOCK:
        flight = [r.get("task") for r in state.AGENTS.values()
                  if r.get("status") == "running"]
    open_ = [op["key"] for op in operations.all_ops()
             if op["status"] in operations.OPEN]
    return execowner.handover(expected, {"inFlight": flight,
                                         "unreconciled": open_}, actor)
