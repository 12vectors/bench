"""A phase run by its own scoped sub-orchestrator (task 143, D6).

The orchestration engine runs at two scopes. The Bench scope manages
standalone cards; a **phase scope** manages one phase card's members, the
integration of each into the phase branch, and the phase's final
acceptance. This module is what a phase scope adds to the shared engine.
It is not a second scheduler: member launches are `scheduler`'s, through
**▸ start work**'s own path; checks, review, repair and the final merge
are `coordinate`'s; and what may happen at all is `orchestrator.authorize`,
asked immediately before every side effect. What it adds:

- **One owner, by queueing (task 188).** Every phase card is its phase
  scope's. Nobody adopts it and the agreement need not name it: an unnamed
  phase inherits the Bench scope (`agreements.scope`), a named one only
  narrows it. Moving the phase card into To Do is the person's queue and
  delegation (D1), and it queues the phase's unfinished members with it,
  Backlog ones included (`taskfiles.queue_state`). The historical phase
  runner and its adoption step are retired; `migrate_model` is the
  one-time upgrade that moved every phase over, and the only thing that
  can still hold one back (`agreements.phase_holds`).
- **Strategy (`may_start`).** Which member may start next: `sequential`
  is one member at a time, in list order, each after the one before it is
  integrated — and `dependencies` lets members whose in-phase dependencies
  are integrated run side by side, within the phase's ceiling. A Backlog
  member of a queued phase is queued; one of a phase still in Backlog is
  not.
- **The phase branch (`run`).** A phase card the owner moved into To Do
  is put in-progress (an automated move: the delegation stands, D1) and
  its branch cut through `phases.ensure_branch` — the one way a phase
  branch is cut — and main is merged into it on the beat as the runner
  does. When every member is integrated the phase is finished through
  `phases.finish`: the branch published, the card into review/ and its
  one PR opened. From there `coordinate` delivers it like any card, under
  the *integration* merge rule: the final integrated head's own checks and
  review, never its members' (acceptance 4).
- **Integration (`integrate`).** A member is merged into the phase branch
  through `phases.merge_member` — the gate read again with the head in
  hand, the validated commit merged, never a rebase or a force-push, and
  the `<n> merged into` log line that is the durable record. Integrated is
  not delivered: the member stays in review/ until the phase itself is
  merged into the application base, which is what moves it to done/.

Every side effect here has an intent before it and a receipt after it in
the operation ledger, and its record on the card it is about (D11). A
reading (`projection`) reads the board and the ledger and writes, merges,
moves and launches nothing.
"""

from __future__ import annotations

import hashlib
import threading
import time

import agents
import agreements
import config
import decisions
import execowner
import gates
import operations
import orchestrator
import phases
import records
import state
import taskfiles

ACTIONS = ("prepare", "integrate", "finish")
# Stages whose phase cards the upgrade moves to the unified model. Backlog
# phases need nothing (they are not queued) and Done ones are history.
ACTIVE = ("to-do", "in-progress", "review")
# The line the phase scope writes once into a phase's log when it first
# owns it. This reader does not need it (every phase is its scope's); an
# older bench sharing the work state reads it and its runner stands down.
OWNED_LINE = ("scope adopted by the Orchestrator — unified phase model: the "
              "phase scope owns this phase; the retired phase runner stands "
              "down")
RETIRED_ADOPTION = ("adopting and releasing a phase are retired: every phase "
                    "is its phase scope's, from the moment its card is moved "
                    "into To Do. Pause it in Orchestrator → Phases instead")


class Refused(Exception):
    """An adoption or a release this module will not make, with its code."""

    def __init__(self, code: str, message: str, status: int = 409, **extra):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


# ── reading the board ─────────────────────────────────────────────────

def _tasks(board: dict) -> list[dict]:
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def mode(task: dict) -> str:
    """`scoped` for a phase card or a member, always (task 188), unless
    the one-time upgrade held its phase back: `migrating`."""
    number = _number(task) if task.get("isPhase") else (
        taskfiles.canonical_number((task.get("phase") or {}).get("number"))
        if (task.get("phase") or {}).get("number") else None)
    if agreements.phase_hold(number) is not None:
        return "migrating"
    return "scoped"


def phase_of(task: dict, tasks: list[dict]) -> dict | None:
    """The phase card a member belongs to, from the board."""
    number = (task.get("phase") or {}).get("number")
    if not number:
        return None
    wanted = taskfiles.canonical_number(number)
    return next((t for t in tasks if t.get("isPhase") and _number(t) == wanted),
                None)


def members(phase: dict, tasks: list[dict]) -> list[dict]:
    """The phase's members in run order, as the board has them."""
    number = _number(phase)
    found = [t for t in tasks if not t.get("isPhase") and t.get("phase")
             and taskfiles.canonical_number(t["phase"]["number"]) == number]
    return sorted(found, key=lambda t: (t["phase"].get("index") or 0, t["file"]))


def integrated(phase: dict) -> set[str]:
    """Members the phase branch holds, by the durable record the merge
    wrote — the `<n> merged into` lines of the phase log, legacy-era
    merges included, so a phase adopted mid-run never integrates (or
    re-runs) a member twice."""
    return set(phases.merged_members(phase))


def _running() -> set[str]:
    with state.LOCK:
        return {r.get("task") for r in state.AGENTS.values()
                if r.get("status") == "running"}


def _strategy(phase: dict) -> str:
    reading = agreements.effective()
    here = agreements.scope(reading, f"phase:{_number(phase)}") or {}
    return here.get("strategy") or agreements.STRATEGIES[0]


def has_work(task: dict) -> bool:
    """Whether a card's own work exists: its `task/<stem>` branch. That is
    the first thing ▸ start work makes, so a card without one has never
    been worked on this repository, whatever stage it sits in."""
    return phases.branch_exists(f"task/{task['file'][:-3]}")


def _open_ops(ops: list[dict] | None) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for op in operations.all_ops() if ops is None else ops:
        if op.get("status") in operations.OPEN and op.get("file"):
            found.setdefault(op["file"], []).append(op)
    return found


def in_flight(member: dict, running: set[str],
              open_ops: dict[str, list[dict]]) -> bool:
    """Is this member genuinely being worked (task 160)? A run on it, an
    operation about it with no settled receipt (an intent, a launch, an
    unobserved run, an unknown outcome — each may hold a real process),
    work in review/ waiting to be integrated, or in-progress/ with its own
    branch. A card that is merely *in* in-progress/ — moved there by hand,
    with no run, no reservation and no branch — is not in flight: reading
    its stage as work is how two members of one phase came to wait on
    each other for ever."""
    if member["file"] in running or open_ops.get(member["file"]):
        return True
    if member["stage"] == "review":
        return True
    return member["stage"] == "in-progress" and has_work(member)


def moved_early(member: dict, tasks: list[dict], running: set[str],
                open_ops: dict[str, list[dict]]) -> tuple[str, str] | None:
    """A member sitting in in-progress/ with nothing working it whose turn
    has not come (task 160): (code, why) or None. It is not launched —
    dependencies and the strategy still hold it — and it no longer holds
    anyone else up; what it needs is a person's deliberate move back."""
    if member["stage"] != "in-progress" or in_flight(member, running, open_ops):
        return None
    held = may_start(member, tasks, running, open_ops=open_ops)
    if not held or held[0] not in ("waiting-member", "waiting-integration"):
        return None
    return ("moved-early", f"it is in In Progress with nothing working it "
            f"(no run, no reservation, no branch), and its turn has not "
            f"come: {held[1]}. Nothing launches it out of order, and it "
            f"does not hold the phase up — move it back to To Do (queued) or "
            f"Backlog (not queued)")


def may_start(task: dict, tasks: list[dict],
              running: set[str] | None = None, *,
              open_ops: dict[str, list[dict]] | None = None
              ) -> tuple[str, str] | None:
    """Why a member of a scoped phase may not start now, or None. Read
    from the board and the phase log only: it launches nothing."""
    phase = phase_of(task, tasks)
    if phase is None:
        return ("phase-missing", "its phase card is not on this board")
    if phase["stage"] != "in-progress":
        return ("phase-not-started", f"its phase card is in {phase['stage']}/; "
                f"the phase scope starts members once it has put the phase "
                f"in-progress and cut its branch")
    running = _running() if running is None else running
    done = integrated(phase)
    mine = _number(task)
    if mine in done:
        # the phase branch already holds it (the log says so, the retired
        # runner's merges included): never launched again, whatever its
        # stage says (task 188)
        return ("integrated", "it is already integrated into the phase branch")
    order = members(phase, tasks)
    # in-phase dependencies guard whatever the strategy: integrated into
    # the phase branch, not merely reviewed
    numbers = {_number(m) for m in order}
    for dep in task.get("dependsOn") or []:
        if dep in numbers and dep not in done:
            return ("waiting-integration", f"it depends on card {dep}, which is "
                    f"not integrated into the phase branch yet")
    if _strategy(phase) == "sequential":
        for member in order:
            number = _number(member)
            if number == mine:
                break
            if number not in done:
                return ("waiting-member", f"sequential: card {number} comes "
                        f"before it and is not integrated yet")
        # in flight means being worked, never merely sitting in a stage
        # (task 160): a later member moved into in-progress/ by mistake
        # would otherwise hold the earlier one while waiting on it
        opened = _open_ops(None) if open_ops is None else open_ops
        for member in order:
            number = _number(member)
            if number == mine or number in done:
                continue
            if in_flight(member, running, opened):
                return ("phase-member-in-flight", f"sequential: card {number} "
                        f"of this phase is in flight")
    return None


# ── adoption: one scheduling owner ────────────────────────────────────

def _find(filename: str) -> tuple[dict, list[dict]]:
    board = taskfiles.collect()
    tasks = _tasks(board)
    phase = next((t for t in tasks if t["file"] == filename), None)
    if phase is None:
        raise Refused("card-unknown", f"{filename} is not on this board", 404)
    if not phase.get("isPhase"):
        raise Refused("not-a-phase", f"{filename} is not a phase card", 400)
    return phase, tasks


def quiescence(phase: dict, tasks: list[dict]) -> list[str]:
    """What is still working this phase, in words: an agent on the phase
    card or a member, an operation on any of them with no receipt. Empty
    is quiescent. (The phase runner's own run in force no longer counts:
    the runner is retired and acts on nothing, task 188.)"""
    found = []
    cards = {phase["file"]} | {m["file"] for m in members(phase, tasks)}
    for name in sorted(cards & _running()):
        found.append(f"an agent is running on {name}")
    for op in operations.all_ops():
        if op.get("file") in cards and op["status"] in operations.OPEN:
            found.append(f"operation {op['key']} ({op.get('action')}) on "
                         f"{op['file']} is {op['status']} — reconcile it first")
    return found


def _log(phase: dict, line: str) -> None:
    stage = taskfiles.find_stage_of(phase["file"]) or phase["stage"]
    if not phases.write_log(phase | {"stage": stage}, line):
        raise Refused("log-unwritable", f"could not write {phase['file']}'s "
                      f"phase log; nothing changed")


def adopt(filename: str, actor: str | None = None) -> dict:
    """Retired (task 188): every phase is its scope's already. Refused
    with the way forward; nothing is read beyond the name or written."""
    raise Refused("adoption-retired", f"{filename}: {RETIRED_ADOPTION}", 410)


def release(filename: str, actor: str | None = None) -> dict:
    """Retired (task 188): there is no phase runner to hand a phase back
    to, so a release cannot resurrect a second scheduler."""
    raise Refused("adoption-retired", f"{filename}: {RETIRED_ADOPTION}", 410)


def resume(filename: str, actor: str | None = None) -> dict:
    """A person lifts a `held` migration hold (task 188): the phase was
    halted or held under the retired runner, and carrying on was a
    person's decision there (▸ run phase). Refused while anything still
    works it, and for a hold that is not a person's to lift (`busy`, which
    clears by itself once its work ends). Writes the hold's removal, the
    ownership line and a receipt on the card; launches nothing."""
    phase, tasks = _find(filename)
    number = _number(phase)
    hold = agreements.phase_hold(number)
    if hold is None:
        raise Refused("not-held", f"{filename} is not held by the upgrade; "
                      f"nothing to resume")
    if hold.get("kind") == "unreadable":
        # resuming would write a fresh record over the unreadable one and
        # lose every other phase's hold with it
        raise Refused("model-unreadable", f"{filename}: {hold['reason']}")
    if hold.get("kind") != "held":
        raise Refused("hold-not-yours", f"{filename} is held because "
                      f"{hold.get('reason')}; that clears by itself once its "
                      f"work ends")
    owner = execowner.authorizes()
    if not owner["allowed"]:
        raise Refused(owner["code"], owner["reason"])
    busy = quiescence(phase, tasks)
    if busy:
        raise Refused("not-quiescent", "the phase is still working: "
                      + "; ".join(busy[:6]), working=busy)
    who = actor or taskfiles.actor_name() or "unknown"
    with _MODEL_LOCK:
        doc = agreements.phase_model() or _new_model()
        holds = dict(doc.get("holds") or {})
        holds.pop(number, None)
        doc["holds"] = holds
        doc.setdefault("resumed", []).append(
            {"card": number, "by": who, "at": _now()})
        _write_model(doc)
    _ensure_owned(phase)
    _record(phase["file"], "resume", f"resumed under the unified phase model "
            f"by {who}: the phase scope schedules it from here")
    state.broadcast({"type": "board"})
    return {"card": filename, "mode": "scoped", "resumed": True}


def _record(filename: str, action: str, summary: str) -> None:
    stage = taskfiles.find_stage_of(filename)
    if not stage:
        return
    try:
        decisions.note(filename, stage, "action", {
            "action": action, "operation": f"phase-{action}", "phase": "receipt",
            "outcome": {"resume": "resumed", "migrate": "migrated",
                        "hold": "held"}.get(action, action),
            "summary": summary[:400]},
            key=f"phase-{action}-{time.time_ns()}")
    except (ValueError, OSError):
        pass


def _owned(phase: dict) -> bool:
    """Whether the phase's log already carries an adoption line — the one
    an older bench reads to stand its runner down."""
    return any(taskfiles.SCOPE_ADOPTED_RE.match(e)
               for e in phases.log_entries(phase.get("body") or ""))


def _ensure_owned(phase: dict) -> bool:
    """Write the ownership line once (idempotent: a log that has it is
    left alone). Returns whether a line was written."""
    if _owned(phase):
        return False
    _log(phase, OWNED_LINE)
    return True


# ── the one-time move to the unified phase model (task 188) ───────────
#
# What the upgrade finds, per phase card in To Do, In Progress or Review:
#
# | found                                             | what happens                      |
# | ------------------------------------------------- | --------------------------------- |
# | adopted already (a `scope adopted` line)          | nothing: it is its scope's        |
# | the runner never ran it, or ran it to the end     | transferred: the ownership line   |
# | a runner run in force, nothing working now        | transferred; the run is history   |
# | a runner run halted, or held by a person          | held (`held`) until a person      |
# |                                                   | resumes it: ▸ run phase was the   |
# |                                                   | person's decision, and still is   |
# | an agent or an unsettled operation on it          | held (`busy`) until it is quiet;  |
# |                                                   | each pass re-checks and finishes  |
#
# Nothing is launched, merged, moved, rewound or cancelled by it; branches,
# worktrees, logs, members, receipts and frozen run permissions are left as
# they are. The agreement is not rewritten: a phase entry it names keeps
# narrowing exactly as before (pause, enablement, authority, limits), and
# an unnamed phase inherits the Bench scope. The record — installation-
# local, beside the agreement — says `complete` only after every phase's
# transfer is durable on its card; a pre-migration recovery copy is kept
# once and never overwritten.

_MODEL_LOCK = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_model() -> dict:
    return {"kind": agreements.PHASE_MODEL_KIND,
            "v": agreements.PHASE_MODEL_VERSION, "model": "unified",
            "status": "pending", "holds": {}, "transferred": [],
            "summary": [], "started": _now(), "completed": None}


def _write_model(doc: dict) -> None:
    path = agreements.phase_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records.write_json(path, doc, durable=True)


def backup_path():
    return agreements.state_dir() / "phase-model-backup.json"


def _backup(tasks: list[dict]) -> None:
    """The pre-migration recovery copy, written once: the agreement and
    its revision, the designation reading, each phase card's log reading
    and mode, and the operation ledger's open entries. Never overwritten,
    so a repeated or interrupted migration keeps the first one."""
    path = backup_path()
    if path.exists():
        return
    doc, problem = agreements.effective_document()
    phase_cards = []
    for task in tasks:
        if not task.get("isPhase"):
            continue
        entries = phases.log_entries(task.get("body") or "")
        phase_cards.append({
            "card": task["file"], "number": _number(task),
            "stage": task["stage"],
            "runner": phases.run_state(entries)["state"],
            "adopted": _owned(task), "log": entries[-40:],
            "members": [m["file"] for m in members(task, tasks)]})
    copy = {"kind": "bench-phase-model-backup", "v": 1, "taken": _now(),
            "agreement": doc, "agreementProblem": problem,
            "agreementRevision": agreements.revision(doc),
            "designation": execowner.reading(),
            "phases": phase_cards,
            "openOperations": [op for op in operations.all_ops()
                               if op.get("status") in operations.OPEN]}
    path.parent.mkdir(parents=True, exist_ok=True)
    records.write_json(path, copy, durable=True)


def _classify(phase: dict, tasks: list[dict]) -> tuple[str, str | None, str]:
    """(outcome, hold kind or None, words) for one phase card."""
    if _owned(phase):
        return "adopted", None, "already run by its phase scope"
    entries = phases.log_entries(phase.get("body") or "")
    where = phases.run_state(entries)
    if where["state"] == "idle" and not any(phases.RUN_RE.match(e)
                                            for e in entries):
        # the runner never touched it: nothing of the old model is in
        # flight, and whatever runs on a member is a run like any other
        return "transferred", None, "moved to its phase scope (the retired " \
                                    "runner never ran it)"
    busy = quiescence(phase, tasks)
    if busy:
        return "held", "busy", ("work the retired phase runner started is "
                                "still running on it (" + "; ".join(busy[:3])
                                + "); it moves over by itself once that ends")
    if where["state"] == "halted":
        return "held", "held", (f"the retired phase runner had halted it — "
                                f"{(where.get('reason') or '')[:200]}; resume it "
                                f"in Orchestrator → Phases once that is settled")
    if where["state"] == "stopped":
        return "held", "held", ("a person had held it under the retired phase "
                                "runner; resume it in Orchestrator → Phases to "
                                "let its phase scope carry on")
    return "transferred", None, ("moved to its phase scope (the runner had it "
                                 f"{where['state']})")


def migrate_model(board: dict | None = None) -> dict:
    """Move this installation to the unified phase model, once, and finish
    any transfer it deferred. Idempotent and restart-safe: a complete
    record with nothing held returns at once; a partial one is resumed
    phase by phase, each step checked against what is on the card. Only
    the execution installation transfers (the ownership line is a write to
    the shared work state); a replica reads the record it has, or none."""
    with _MODEL_LOCK:
        doc = agreements.phase_model()
        if doc is not None and doc.get("status") == "complete" and \
                not any((h or {}).get("kind") == "busy"
                        for h in (doc.get("holds") or {}).values()):
            return doc
        if doc is not None and doc.get("status") == "unreadable":
            return doc        # never written over: a person settles it
        role = execowner.reading()["role"]
        if role not in ("designated-here", "unconfigured"):
            # another installation executes (or none may until a handover
            # is adopted): it migrates the shared cards, and this replica
            # writes nothing to them
            return doc or {"status": "not-this-installation", "holds": {},
                           "role": role}
        board = taskfiles.collect() if board is None else board
        tasks = _tasks(board)
        _backup(tasks)
        doc = doc or _new_model()
        holds = dict(doc.get("holds") or {})
        transferred = list(doc.get("transferred") or [])
        summary = []
        for phase in [t for t in tasks if t.get("isPhase")
                      and t["stage"] in ACTIVE]:
            number = _number(phase)
            if not number:
                continue
            previous = holds.get(number)
            if previous and previous.get("kind") == "held":
                summary.append(f"#{number} held: {previous['reason']}")
                continue
            outcome, kind, words = _classify(phase, tasks)
            if kind is not None:
                holds[number] = {"kind": kind, "reason": words,
                                 "card": phase["file"], "since":
                                 (previous or {}).get("since") or _now()}
                summary.append(f"#{number} held: {words}")
                continue
            if outcome == "transferred":
                try:
                    _ensure_owned(phase)   # durable on the card before
                except (Refused, OSError, ValueError) as exc:
                    # the record never says moved before the card does:
                    # held, and tried again on the next pass
                    holds[number] = {"kind": "busy", "card": phase["file"],
                                     "reason": f"its phase log could not be "
                                               f"written ({exc}); retried on "
                                               f"the next pass",
                                     "since": (previous or {}).get("since")
                                     or _now()}
                    summary.append(f"#{number} held: log not written")
                    continue
            holds.pop(number, None)        # the record says it moved
            if number not in transferred:
                transferred.append(number)
                if outcome == "transferred":
                    _record(phase["file"], "migrate", "moved to the unified "
                            "phase model: its phase scope owns it from here; "
                            "branch, log, members and receipts unchanged")
            summary.append(f"#{number} {words}")
        # A `busy` hold whose phase card has left To Do, In Progress and
        # Review (walked back, delivered, archived, deleted) is re-checked
        # here, or it would never clear and every pass would migrate
        # again: dropped once nothing of the old model works on it. Such a
        # card is not scheduled by its stage anyway, and nothing is
        # transferred for it — a card queued again later is its scope's,
        # like any phase. A `held` hold is a person's and stays.
        visited = {_number(t) for t in tasks
                   if t.get("isPhase") and t["stage"] in ACTIVE}
        by_number = {_number(t): t for t in tasks if t.get("isPhase")}
        for number, hold in list(holds.items()):
            if number in visited or (hold or {}).get("kind") != "busy":
                continue
            phase = by_number.get(number)
            if phase is not None and quiescence(phase, tasks):
                summary.append(f"#{number} held: {hold.get('reason')}")
                continue
            holds.pop(number, None)
            summary.append(f"#{number} hold cleared: its card left the active "
                           f"stages and nothing of the old model works on it")
        reading = agreements.effective()
        if reading.get("configured") and \
                reading.get("capacityModel") == agreements.RESERVED:
            summary.append("this agreement still reserves capacity per phase: "
                           "a phase it does not name has no slot of its own "
                           "until it is migrated to shared capacity "
                           "(Orchestrator → Operating agreement)")
        if not reading.get("configured"):
            summary.append("no operating agreement is in force: queued phases "
                           "wait until one is applied (Orchestrator → "
                           "Operating agreement)")
        named = sorted((s["card"] for s in reading.get("scopes") or []
                        if s["kind"] == "phase"), key=int)
        if named:
            summary.append("phase entries kept as they were, narrowing their "
                           "phases: " + ", ".join(f"#{n}" for n in named))
        doc.update(holds=holds, transferred=transferred,
                   summary=summary[:200],
                   status="complete" if not any(
                       h["kind"] == "busy" for h in holds.values())
                   else "partial", checked=_now())
        if doc["status"] == "complete" and not doc.get("completed"):
            doc["completed"] = _now()
        _write_model(doc)
        return doc


def model_reading() -> dict:
    """The migration record as the page shows it: status, holds and the
    summary. A read."""
    doc = agreements.phase_model()
    if doc is None:
        return {"status": "not-run", "holds": {}, "summary": [],
                "backup": False}
    return {"status": doc.get("status"), "model": doc.get("model"),
            "holds": doc.get("holds") or {}, "summary": doc.get("summary") or [],
            "completed": doc.get("completed"), "problem": doc.get("problem"),
            "transferred": doc.get("transferred") or [],
            "backup": backup_path().exists()}


# ── the side effects, each with an intent and a receipt ───────────────

def _begin(action: str, task: dict, basis: str, answer: dict) -> dict | None:
    prior = [o for o in operations.for_card(task["file"], action)
             if o.get("basis") == basis]
    attempt = len([o for o in prior if o["status"] in operations.NOTHING_RAN]) + 1
    key = operations.key_for(action, task["file"], basis, attempt)
    try:
        op = operations.begin(key, action=action, card=task["file"],
                              number=_number(task), scope=answer["scope"],
                              pool="none", basis=basis, attempt=attempt,
                              note="; ".join(answer.get("provenance") or []))
    except operations.Exists:
        return None
    _note(op, "intent", "intended", f"{action} under the {answer['scope']} scope")
    return op


def _note(op: dict, phase: str, outcome: str, summary: str) -> None:
    stage = taskfiles.find_stage_of(op["file"])
    if not stage:
        return
    try:
        decisions.note(op["file"], stage, "action", {
            "action": op["action"], "operation": op["key"], "phase": phase,
            "outcome": outcome, "summary": summary[:400]},
            key=f"{op['key']}-{phase}-{outcome}"[:80])
    except (ValueError, OSError):
        pass


def _ended(op: dict, outcome: str, words: str) -> dict:
    done = operations.update(op["key"], "ended", outcome=outcome, observed=words)
    _note(done, "receipt", outcome, words)
    return {"card": op["file"], "action": op["action"], "outcome": outcome,
            "key": op["key"]}


def _refused(op: dict, code: str, reason: str) -> dict:
    done = operations.update(op["key"], "refused", code=code, reason=reason)
    _note(done, "receipt", "refused", f"not done: {reason}")
    return {"card": op["file"], "action": op["action"], "outcome": "refused",
            "code": code, "key": op["key"], "reason": reason}


def _phase_record(task: dict) -> dict:
    """The phase card as `phases` expects it: file, stage, title, number,
    body and members."""
    stage = taskfiles.find_stage_of(task["file"]) or task["stage"]
    return phases.cards()[0].get(task["file"]) or {**task, "stage": stage}


def prepare(phase: dict) -> dict | None:
    """Put an adopted phase in-progress and its branch in place. Returns
    the result, or None when there is nothing to do."""
    branch = agents.phase_branch(phase["file"])
    if phase["stage"] == "in-progress" and phases.branch_exists(branch):
        return None
    if phase["stage"] not in ("to-do", "in-progress"):
        return None
    if phase.get("phaseDrift"):
        # a malformed or ambiguous list: nothing is cut or moved until a
        # person fixes it, and the card says what (task 188)
        return {"card": phase["file"], "action": "prepare",
                "outcome": "blocked", "code": "phase-drift",
                "reason": f"its ## Cards list does not resolve: "
                          f"{phase['phaseDrift'][0]}; fix it and the next "
                          f"pass prepares the phase"}
    if not phase.get("members"):
        return {"card": phase["file"], "action": "prepare",
                "outcome": "blocked", "code": "phase-empty",
                "reason": "it lists no cards, so there is nothing to queue"}
    answer = orchestrator.authorize("prepare", phase["file"])
    if not answer["allowed"]:
        return {"card": phase["file"], "action": "prepare",
                "outcome": "not-authorized", "code": answer["code"],
                "reason": answer["reason"]}
    op = _begin("prepare", phase, f"{phase['stage']}:{branch}", answer)
    if op is None:
        return None
    try:
        # the ownership line first, on the card, so an older bench sharing
        # this work state stands its runner down before anything is cut.
        # Best effort: this board needs no line to own the phase
        try:
            _ensure_owned(phase)
        except Refused:
            pass
        if phase["stage"] == "to-do":
            again = orchestrator.authorize("prepare", phase["file"])
            if not again["allowed"]:
                return _refused(op, again["code"], again["reason"])
            taskfiles.move_task(phase["file"], "to-do", "in-progress",
                                actor="orchestrator")
        note = phases.ensure_branch(_phase_record(phase))
    except (ValueError, OSError, Refused) as exc:
        return _refused(op, "prepare-refused", str(exc))
    return _ended(op, "prepared", f"{branch} is in place"
                  + (f" ({note})" if note else ""))


def freshen(phase: dict) -> dict | None:
    """Merge main into the phase branch, as the runner does on its beat,
    while no member is in flight. A conflict is a blocker on the phase
    card, never a guess."""
    try:
        phases.freshen(_phase_record(phase))
    except phases.Halt as stop:
        return {"card": phase["file"], "action": "freshen",
                "outcome": "blocked", "reason": str(stop)}
    return None


def finish(phase: dict, tasks: list[dict]) -> dict | None:
    """Every member integrated: publish the branch, move the phase card
    to review/ and open its PR — the runner's own ending."""
    order = members(phase, tasks)
    done = integrated(phase)
    if not order or any(_number(m) not in done for m in order):
        return None
    answer = orchestrator.authorize("finish", phase["file"])
    if not answer["allowed"]:
        return {"card": phase["file"], "action": "finish",
                "outcome": "not-authorized", "code": answer["code"],
                "reason": answer["reason"]}
    tip = phases.branch_tip(agents.phase_branch(phase["file"]))
    op = _begin("finish", phase, tip or "no-branch", answer)
    if op is None:
        return None
    try:
        phases.finish(_phase_record(phase))
    except (ValueError, OSError, phases.Halt) as exc:
        return _refused(op, "finish-refused", str(exc))
    return _ended(op, "finished", "every member is integrated: the phase "
                  "branch is published and the phase card is in review/ — "
                  "integrated is not delivered; its merge follows its own rule")


def integrate(member: dict, head: str) -> dict:
    """Merge one member's validated head into its phase branch. Raises
    ValueError when it will not: a conflict, a gate unmet when read again,
    a phase that is not this scope's."""
    tasks = _tasks(taskfiles.collect())
    phase = phase_of(member, tasks)
    if phase is None or mode(phase) != "scoped":
        raise ValueError("its phase is not run by a phase scope")
    if not isinstance(head, str) or not head:
        raise ValueError("no checked head to integrate — nothing was merged")
    record = _phase_record(phase)
    listed = {**member, "number": _number(member)}
    branch = agents.phase_branch(phase["file"])
    try:
        # `head` is the commit this scope checked and reviewed: exactly it
        # is merged, under a gated and an ungated rule alike (PR 147 review)
        whole = phases.merge_member(record, listed, expect=head)
    except phases.Halt as stop:
        raise ValueError(str(stop)) from None
    if not phases.contains(head, branch):
        raise ValueError(f"{branch} does not hold {head[:12]} after the merge")
    return {"whole": whole, "branch": branch, "integrated": head}


def run(board: dict) -> list[dict]:
    """A phase scope's own steps in one pass, for every queued phase:
    prepare, freshen, finish. Called by `scheduler.run_pass` under its
    lock, before member launches are planned. A phase held by the upgrade
    (`migrating`) is skipped until its hold clears."""
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return []
    tasks = _tasks(board)
    running = _running()
    results = []
    for phase in [t for t in tasks if t.get("isPhase") and mode(t) == "scoped"
                  and t["stage"] in ("to-do", "in-progress")]:
        here = agreements.scope(reading, f"phase:{_number(phase)}")
        if here is None or not here["active"]:
            continue
        try:
            done = prepare(phase)
            if done:
                results.append(done)
                if done.get("outcome") == "blocked":
                    _block(phase, done["reason"])
                continue
            if phase["stage"] != "in-progress":
                continue
            order = members(phase, tasks)
            if not any(m["file"] in running for m in order):
                blocked = freshen(phase)
                if blocked:
                    results.append(blocked)
                    _block(phase, blocked["reason"])
                    continue
            finished = finish(phase, tasks)
            if finished:
                results.append(finished)
        except (ValueError, OSError) as exc:
            results.append({"card": phase["file"], "action": "phase",
                            "outcome": "error", "reason": str(exc)[:200]})
    return results


def _block(phase: dict, reason: str) -> None:
    stage = taskfiles.find_stage_of(phase["file"])
    if not stage:
        return
    try:
        decisions.note(phase["file"], stage, "blocker", {
            "blocker": "b-" + hashlib.sha256(
                ("phase-freshen\x00" + phase["file"]).encode("utf-8")
            ).hexdigest()[:10], "state": "open",
            "operation": "phase-scope", "text": reason[:1800], "evidence": []},
            key="phase-freshen-"
                + hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12])
    except (ValueError, OSError):
        pass


# ── observe and reconcile ─────────────────────────────────────────────

def reconcile(ops: list[dict] | None = None) -> list[dict]:
    """Settle a phase step's intent left without a receipt from git and
    the board — never by repeating it blindly. Preparing and finishing are
    idempotent (a branch that exists is not cut again; a PR line is
    checked before a PR is opened), so one that cannot be shown done is
    abandoned and asked again; an integration is settled by containment."""
    changed = []
    for op in operations.all_ops() if ops is None else ops:
        if op.get("action") not in ACTIONS or op["status"] != "intent":
            continue
        filename = op["file"]
        if op["action"] == "integrate":
            stage = taskfiles.find_stage_of(filename)
            task = next((t for t in _tasks(taskfiles.collect())
                         if t["file"] == filename), None) if stage else None
            phase_file = ((task or {}).get("phase") or {}).get("file")
            if phase_file and gates.contained(op.get("basis") or "",
                                              agents.phase_branch(phase_file)):
                words = "reconciled: the phase branch holds the member's head"
                done = operations.update(op["key"], "ended", outcome="integrated",
                                         observed=words)
                _note(done, "receipt", "integrated", words)
                changed.append(done)
                continue
            changed.append(operations.update(
                op["key"], "abandoned", code="not-integrated",
                reason="the phase branch does not hold the head: the local "
                       "merge did not happen", observed="reconciled: nothing "
                       "merged"))
            continue
        changed.append(operations.update(
            op["key"], "abandoned", code="unconfirmed",
            reason=f"the {op['action']} step is idempotent and is asked again",
            observed="reconciled: asked again"))
    return changed


# ── the public reading ────────────────────────────────────────────────

def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def budget(here: dict | None, reading: dict, used: dict) -> dict | None:
    """A scope's allocations in words (task 160): how many implementation
    slots it has and how many are in use, its launches today, and — for a
    phase — that both are reserved out of the Bench scope's total. A daily
    launch is one agent run (an implementation, an independent review, a
    repair, a diagnosis), never one card's whole lifecycle, so a one-launch
    budget is named as the limit it is. Read only: it explains, and never
    raises an allocation."""
    if here is None:
        return None
    if here["capacity"]["implementation"].get("shared"):
        return _shared_budget(here, reading, used)
    sid = here["id"]
    bench = agreements.scope(reading, agreements.BENCH) or {}
    bench_cap = bench.get("capacity") or {}
    cap = here["capacity"]
    slots = cap["implementation"]["own"]
    in_use = used["running"].get(sid, {}).get("implementation", 0)
    starts = cap["dailyStarts"]["own"]
    spent = used["startsToday"].get(sid, 0)
    review_slots = cap["review"]["own"]
    lines = [
        f"{in_use} of {_plural(slots, 'implementation slot')} in use "
        f"(slots are runs at the same time)",
        f"{spent} of {_plural(starts, 'launch', 'launches')} used today "
        f"(every agent launch counts: implementation, independent review, "
        f"repair, diagnosis)",
        f"{_plural(review_slots, 'review slot')}",
    ]
    if sid != agreements.BENCH:
        total_slots = (bench_cap.get("implementation") or {}).get("total", 0)
        total_starts = (bench_cap.get("dailyStarts") or {}).get("total", 0)
        lines.append(f"reserved out of the Bench scope's total of "
                     f"{_plural(total_slots, 'implementation slot')} and "
                     f"{_plural(total_starts, 'launch', 'launches')} a day; the "
                     f"Bench scope does not use what this phase holds")
    missing, warnings = [], []
    if slots == 0:
        missing.append("no implementation slot is allocated to this scope, so "
                       "no member can start; allocate one in Orchestrator → "
                       "Operating agreement")
    if starts == 0:
        missing.append("no daily launches are allocated to this scope, so "
                       "nothing can start; allocate some in Orchestrator → "
                       "Operating agreement")
    elif spent >= starts:
        missing.append(f"today's {_plural(starts, 'launch', 'launches')} "
                       f"{'is' if starts == 1 else 'are'} used up, so the next "
                       f"launch waits for tomorrow; allocate more in "
                       f"Orchestrator → Operating agreement to go on today")
    if starts == 1:
        warnings.append("one launch a day is one agent run: a member's "
                        "implementation, or its independent review, or one "
                        "repair, not a member's whole lifecycle. Its review or "
                        "repair waits for the next day")
    if review_slots == 0 and here["authority"]["review"]["allowed"]:
        warnings.append("no review slot is allocated, so an independent review "
                        "of a member cannot launch")
    return {"slots": {"own": slots, "inUse": in_use},
            "dailyStarts": {"own": starts, "used": spent},
            "reviewSlots": review_slots, "lines": lines,
            "missing": missing, "warnings": warnings}


def _shared_budget(here: dict, reading: dict, used: dict) -> dict:
    """The same answer under shared capacity (task 182): what this scope
    uses now against its ceiling, what the board uses against the global
    total, and what is free. Nothing is set aside, so an idle phase uses
    nothing and a ceiling is never named as something the phase holds."""
    sid = here["id"]
    cap = here["capacity"]
    ceiling = cap["implementation"]["own"]
    total = cap["implementation"]["total"]
    in_use = used["running"].get(sid, {}).get("implementation", 0)
    board_use = used["totals"].get("implementation", 0)
    free = max(0, total - board_use)
    starts = cap["dailyStarts"]["own"]
    spent = used["startsToday"].get(sid, 0)
    starts_total = cap["dailyStarts"]["total"]
    spent_total = used.get("startsTodayTotal", 0)
    review_slots = cap["review"]["own"]
    by = {"default": "the default phase ceiling",
          "global": "the global total"}.get(cap["implementation"].get("by"),
                                            "this phase's own limit")
    lines = [
        f"{in_use} running here, at most {ceiling} at once ({by}); "
        f"{board_use} of {_plural(total, 'implementation slot')} in use on the "
        f"board, {free} free — shared, first come first served",
        f"{spent} of {_plural(starts, 'launch', 'launches')} today for this "
        f"phase; {spent_total} of {starts_total} on the board (every agent "
        f"launch counts: implementation, independent review, repair, "
        f"diagnosis, and none is refunded when a run ends)",
        f"at most {_plural(review_slots, 'review')} at once",
    ]
    missing, warnings = [], []
    if ceiling == 0:
        missing.append("this phase's implementation ceiling is 0, so no member "
                       "can start; raise its limit or the default in "
                       "Orchestrator → Operating agreement")
    elif free == 0 and in_use < ceiling:
        warnings.append("every implementation slot on the board is in use; the "
                        "next member is admitted in its turn when one frees")
    if starts == 0:
        missing.append("this phase may make no launches today (its daily "
                       "ceiling is 0); raise it in Orchestrator → Operating "
                       "agreement")
    elif spent >= starts or spent_total >= starts_total:
        missing.append("today's launches are used up, so the next launch waits "
                       "for tomorrow")
    if review_slots == 0 and here["authority"]["review"]["allowed"]:
        warnings.append("this phase's review ceiling is 0, so an independent "
                        "review of a member cannot launch")
    return {"slots": {"own": ceiling, "inUse": in_use, "ceiling": ceiling,
                      "total": total, "boardInUse": board_use, "free": free},
            "dailyStarts": {"own": starts, "used": spent},
            "reviewSlots": review_slots, "lines": lines, "shared": True,
            "missing": missing, "warnings": warnings}


def _member_rows(phase: dict, tasks: list[dict], running: set[str],
                 opened: dict, items: dict | None) -> list[dict]:
    """Each member's state as the phase scope reads it, in run order.
    Stage is one input, never the whole answer: a Backlog member is not
    ready however eligible its turn, and a member merely sitting in
    in-progress/ is not working."""
    done = integrated(phase)
    rows = []
    for member in members(phase, tasks):
        number = _number(member)
        code = None
        mine = opened.get(member["file"], [])
        if number in done or member["stage"] == "done":
            word, why = "integrated", ("in the phase branch, not yet delivered "
                                       "to the application base")
        elif member["file"] in running:
            word, why = "working", "a run is working on it"
        elif any(o["status"] == "unknown" for o in mine):
            word, why = "unknown", ("an operation on it has an unknown outcome; "
                                    "settle it in Orchestrator → Recovery")
        elif mine:
            word, why = "launching", (f"operation {mine[0]['key']} is "
                                      f"{mine[0]['status']}; its receipt "
                                      f"settles it")
        elif member["stage"] == "review":
            word, why = "integrating", ("its checks, review and integration "
                                        "are the phase scope's next steps")
        elif member["stage"] == "backlog" and \
                not (member.get("queue") or {}).get("inherited"):
            queue = member.get("queue") or {}
            word, why = ("blocked" if queue.get("state") == "blocked"
                         else "not-queued"), (
                queue.get("reason") or "in Backlog, and its phase is not queued")
            code = "membership" if queue.get("state") == "blocked" else None
        else:
            early = moved_early(member, tasks, running, opened)
            blocked = None if early else may_start(member, tasks, running,
                                                   open_ops=opened)
            item = (items or {}).get(member["file"])
            if early:
                word, why, code = "moved-early", early[1], early[0]
            elif member["stage"] == "in-progress" and has_work(member):
                word, why = "stopped", ("its work is on its branch and nothing "
                                        "is running on it; see Orchestrator → "
                                        "Recovery, or ▸ start work to resume")
            elif blocked:
                word, why, code = "waiting", blocked[1], blocked[0]
            elif item is not None and item.get("state") in ("chosen", "ready"):
                word, why, code = "ready", "chosen to start on the next pass", \
                    item.get("code")
            elif item is not None:
                word = item.get("state") or "waiting"
                why, code = item.get("reason") or "", item.get("code")
            else:
                word, why = "queued", ("queued: the phase scope starts it on its "
                                       "next pass if it is authorized and a slot "
                                       "and a launch are free")
        queue = member.get("queue") or {}
        if queue.get("inherited") and word in ("queued", "ready", "waiting"):
            why = f"{why} — queued with its phase; stored in Backlog until it " \
                  f"starts"
        rows.append({"card": member["file"], "number": number,
                     "title": member.get("title"),
                     "stage": member["stage"], "state": word, "why": why,
                     "code": code,
                     # stored stage against effective queue (task 188)
                     "effective": queue.get("effective") or member["stage"],
                     "inherited": bool(queue.get("inherited"))})
    return rows


LABELS = {
    "not-queued": "Not queued",
    "orchestration-off": "Orchestration off",
    "migrating": "Held by the upgrade",
    "blocked": "Blocked",
    "paused": "Paused",
    "unknown": "Outcome unknown",
    "preparing": "Preparing",
    "working": "Working",
    "moved-early": "Member moved early",
    "waiting": "Waiting",
    "ready": "Ready to start",
    "stopped": "Stopped",
    "integrating": "Integrating",
    "finishing": "Finishing",
    "integration-review": "Integration review",
    "delivered": "Delivered",
}


def _names(rows: list[dict]) -> str:
    return ", ".join(f"#{r['number']}" for r in rows)


def lifecycle(phase: dict, tasks: list[dict], reading: dict,
              running: set[str], opened: dict, used: dict | None = None,
              items: dict | None = None) -> dict:
    """One phase's execution owner and lifecycle, in words, with the one
    concrete next step and who holds it (task 160). The Board's phase
    face, the Phases lane and the Orchestrator all draw this, so they
    cannot disagree about whether a phase has started or who runs it. A
    read: it adopts, queues, launches and moves nothing. `items` is the
    scheduler's plan by card, when the caller has it; without it a member
    that may start reads `queued`."""
    sid = f"phase:{_number(phase)}"
    valid = bool(reading.get("configured") and reading.get("valid"))
    here = agreements.scope(reading, sid) if valid else None
    out = {"card": phase["file"], "scope": sid,
           "named": bool(here) and not here.get("implicit"),
           # every phase is its scope's (task 188): nobody adopts it
           "adopted": True, "owner": "phase scope",
           "queued": phase["stage"] in ("to-do", "in-progress"),
           "compat": phase.get("phaseCompat")}

    def done(word, line, who=None, step=None, **extra):
        out.update(word=word, label=LABELS[word], line=line,
                   next=({"who": who, "text": step} if step else None), **extra)
        return out

    rows = _member_rows(phase, tasks, running, opened, items)
    out["members"] = rows
    by_state: dict[str, list[dict]] = {}
    for row in rows:
        by_state.setdefault(row["state"], []).append(row)
    out["backlog"] = [r["card"] for r in rows if r["stage"] == "backlog"]
    out["inherited"] = [r["card"] for r in rows if r.get("inherited")]
    if phase["stage"] == "done":
        return done("delivered", "delivered: the integrated phase branch is in "
                    "the application base")
    if phase["stage"] == "review":
        return done("integration-review", "every member is integrated; the "
                    "integrated head is checked, reviewed and merged under the "
                    "integration merge rule", "board",
                    "see the phase card's merge requirements")
    if phase.get("phaseDrift"):
        return done("blocked", f"its ## Cards list does not resolve: "
                    f"{phase['phaseDrift'][0]}; nothing is queued or started "
                    f"until it does", "you", "fix the phase's ## Cards list "
                    "(or the other phase that lists the same card)")
    if phase["stage"] == "backlog":
        return done("not-queued", "in Backlog: neither the phase nor its "
                    "members are queued", "you", "move the phase card into To "
                    "Do — that queues the phase and every unfinished member, "
                    "Backlog ones included")
    hold = agreements.phase_hold(_number(phase))
    if hold:
        kind = hold.get("kind")
        return done("migrating", f"held by the upgrade to the unified phase "
                    f"model: {hold.get('reason')}",
                    "board" if kind == "busy" else "you",
                    {"held": "resume it here (Orchestrator → Phases)",
                     "unreadable": "restore phase-model.json from "
                                   "phase-model-backup.json's holds, or a "
                                   "copy of your own, under manager/local/"
                                   "state/orchestration/"}.get(
                        kind, "it moves over by itself once that work ends"),
                    hold=kind)
    if not valid or here is None:
        return done("orchestration-off", "queued, but orchestration is "
                    + ("off on this board: no operating agreement is in force"
                       if not reading.get("configured") else
                       "off: the agreement in force has problems")
                    + "; nothing starts until it is on", "you",
                    "apply an operating agreement in Orchestrator → Operating "
                    "agreement")
    if used is None:
        used = orchestrator.usage(reading, tasks)
    out["budget"] = budget(here, reading, used)
    if not here["active"]:
        why = (f"paused by the {here['pausedBy']} scope" if here.get("pausedBy")
               else f"its scope is off ({here.get('off')})")
        return done("paused", f"{why}: nothing new starts; work already running "
                    f"is observed, not cancelled", "you",
                    "resume it in Orchestrator → Overview")
    if by_state.get("unknown"):
        return done("unknown", f"an operation on {_names(by_state['unknown'])} "
                    f"has an unknown outcome; nothing about it is repeated",
                    "you", "settle it in Orchestrator → Recovery")
    early = by_state.get("moved-early") or []
    early_step = (f"move {_names(early)} back to To Do or Backlog: ↩ on its "
                  f"card in the Phases lane, or drag it there")
    working = (by_state.get("working") or []) + (by_state.get("launching") or [])
    if working:
        w = working[0]
        line = f"on #{w['number']} — {w.get('title') or w['card']}"
        if early:
            return done("working", line + f"; {_names(early)} was moved into "
                        f"In Progress ahead of its turn", "you", early_step,
                        on=w["card"])
        return done("working", line, "agent", "the phase scope checks and "
                    "integrates it once it reaches review", on=w["card"])
    if phase["stage"] != "in-progress" or not phases.branch_exists(
            agents.phase_branch(phase["file"])):
        return done("preparing", f"queued; the Orchestrator moves it into In "
                    f"Progress and cuts {agents.phase_branch(phase['file'])} on "
                    f"its next pass, then starts its members in their turn",
                    "board", "wait for the next pass")
    if early:
        return done("moved-early", f"{_names(early)} sits in In Progress with "
                    f"nothing working it, ahead of its turn; nothing launches "
                    f"it out of order and it does not hold the phase up", "you",
                    early_step)
    if by_state.get("integrating"):
        return done("integrating", f"{_names(by_state['integrating'])} is in "
                    f"review: its checks, review and integration into the phase "
                    f"branch come next", "board",
                    "see the member's merge requirements")
    if rows and all(r["state"] == "integrated" for r in rows):
        return done("finishing", "every member is integrated; the phase scope "
                    "publishes the branch and opens the phase's PR on its next "
                    "pass", "board", "wait for the next pass")
    if by_state.get("stopped"):
        return done("stopped", f"{_names(by_state['stopped'])} has work on its "
                    f"branch and nothing running on it", "you",
                    "see Orchestrator → Recovery, or ▸ start work on it")
    blocked = by_state.get("blocked") or []
    if blocked:
        b = blocked[0]
        return done("blocked", f"#{b['number']} cannot be queued: {b['why']}",
                    "you", "fix the phase lists so every member belongs to "
                    "one phase")
    missing = (out["budget"] or {}).get("missing") or []
    candidates = (by_state.get("ready") or []) + (by_state.get("queued") or [])
    if missing and candidates:
        return done("waiting", f"#{candidates[0]['number']} is queued but cannot "
                    f"start: {missing[0]}", "you", missing[0].split("; ")[-1])
    if candidates:
        r = candidates[0]
        return done("ready", f"#{r['number']} is queued and may start: "
                    f"{r['why']}", "board", "the Orchestrator starts it on its "
                    f"next pass", on=r["card"])
    waiting = [r for r in rows if r["state"] in ("waiting", "held")]
    if waiting:
        w = waiting[0]
        return done("waiting", f"#{w['number']} waits: {w['why']}", "board",
                    "nothing to do unless the reason names you")
    return done("waiting", "nothing to start right now")


def _context(board: dict) -> tuple:
    return _tasks(board), agreements.effective(), _running(), _open_ops(None)


def board_lifecycle(board: dict) -> dict:
    """Every phase card's lifecycle, for `/api/state` (task 160): the
    Board's face and the Phases lane read it rather than guessing from the
    phase runner's snapshot. Without the scheduler's plan, so a member
    that may start reads `queued`; the Orchestrator's reading names the
    rest."""
    found = [t for t in _tasks(board) if t.get("isPhase")]
    if not found:
        return {}       # a board of ordinary cards reads nothing more
    tasks, reading, running, opened = _context(board)
    used = (orchestrator.usage(reading, tasks)
            if any(mode(t) == "scoped" for t in found) else None)
    out = {}
    for phase in found:
        try:
            out[phase["file"]] = lifecycle(phase, tasks, reading, running,
                                           opened, used)
        except (ValueError, OSError, KeyError):
            continue
    return out


def return_refusal(filename: str, source: str, target: str) -> str | None:
    """Whether a person may move a scoped phase's member back out of
    in-progress/ or review/ to To Do or Backlog now (task 160). Refused,
    with what to do instead, while anything may be working it: a run on
    it, or an operation on it without a settled receipt. Its stage is no
    evidence either way, and nothing here cancels, settles or rewinds."""
    if source not in orchestrator.WORKING_STAGES or \
            target not in ("to-do", "backlog"):
        return None
    tasks = _tasks(taskfiles.collect())
    task = next((t for t in tasks if t["file"] == filename), None)
    if task is None or task.get("isPhase") or mode(task) != "scoped":
        return None
    if filename in _running():
        return (f"{filename} has a run working on it; ‖ hold that run first "
                f"(its branch and worktree are kept), then move it back")
    for op in _open_ops(None).get(filename, []):
        if op["status"] == "unknown":
            return (f"operation {op['key']} ({op.get('action')}) on {filename} "
                    f"has an unknown outcome; settle it in Orchestrator → "
                    f"Recovery before moving the card back")
        return (f"operation {op['key']} ({op.get('action')}) on {filename} is "
                f"{op['status']}, so something may be running; wait for its "
                f"receipt, or settle it in Orchestrator → Recovery, first")
    return None


def projection(board: dict | None = None,
               items: list[dict] | None = None) -> dict:
    """Every phase with its scheduling owner and its lifecycle (task 160),
    each member's state read through the scheduler's own plan when the
    caller passes it. A read."""
    board = taskfiles.collect() if board is None else board
    tasks, reading, running, opened = _context(board)
    by_card = {i["card"]: i for i in items or []}
    used = orchestrator.usage(reading, tasks)
    out = []
    for phase in [t for t in tasks if t.get("isPhase")]:
        sid = f"phase:{_number(phase)}"
        valid = bool(reading["configured"] and reading["valid"])
        here = agreements.scope(reading, sid) if valid else None
        life = lifecycle(phase, tasks, reading, running, opened, used, by_card)
        entry = {"card": phase["file"], "number": _number(phase),
                 "stage": phase["stage"], "mode": mode(phase),
                 # named: the agreement has an entry narrowing this phase;
                 # unnamed phases inherit the Bench scope (task 188)
                 "named": bool(here) and not here.get("implicit"),
                 "scope": sid, "owner": life["owner"],
                 "strategy": (here or {}).get("strategy")
                 or agreements.STRATEGIES[0],
                 "integrated": sorted(integrated(phase), key=int),
                 "members": life.get("members") or [],
                 "delivered": phase["stage"] == "done",
                 "lifecycle": {k: v for k, v in life.items() if k != "members"}}
        hold = agreements.phase_hold(_number(phase))
        if hold:
            entry["hold"] = hold
            entry["resumable"] = hold.get("kind") == "held"
        out.append(entry)
    return {"phases": out, "branchPrefix": config.PHASE_BRANCH_PREFIX,
            "model": model_reading()}
