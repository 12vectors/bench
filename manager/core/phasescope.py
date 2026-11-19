"""A phase run by its own scoped sub-orchestrator (task 143, D6).

The orchestration engine runs at two scopes. The Bench scope manages
standalone cards; a **phase scope** manages one phase card's members, the
integration of each into the phase branch, and the phase's final
acceptance. This module is what a phase scope adds to the shared engine.
It is not a second scheduler: member launches are `scheduler`'s, through
**▸ start work**'s own path; checks, review, repair and the final merge
are `coordinate`'s; and what may happen at all is `orchestrator.authorize`,
asked immediately before every side effect. What it adds:

- **Adoption.** A phase named by the agreement keeps the phase runner
  until a person adopts it (`adopt`), and adoption is refused while the
  runner is working it — a legacy run in force, a member agent running,
  or an operation on its cards left without a receipt. The adoption is a
  line in the phase card's own log, which is where the runner reads that
  it stands down (`phases.run_state` → `scoped`), so there is exactly one
  scheduling owner and the record travels with the card. Nothing about
  the phase branch, its worktree, a member's branch, the log or what is
  already integrated changes: adoption only moves who decides next.
  `release` hands it back, under the same quiescence.
- **Strategy (`may_start`).** Which member may start next: `sequential`
  is the runner's own rule — one member at a time, in list order, each
  after the one before it is integrated — and `dependencies` lets members
  whose in-phase dependencies are integrated run side by side, within the
  phase's allocation. A member in Backlog is never pulled out of it.
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
import state
import taskfiles

ACTIONS = ("prepare", "integrate", "finish")
# Stages a phase card may be adopted from: before it is delivered, and
# never out of Backlog, which is the owner's.
ADOPTABLE = ("to-do", "in-progress", "review")


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
    """`scoped` or `legacy` for a phase card or a member."""
    if task.get("isPhase"):
        return task.get("phaseMode") or "legacy"
    return (task.get("phase") or {}).get("mode") or "legacy"


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


def may_start(task: dict, tasks: list[dict],
              running: set[str] | None = None) -> tuple[str, str] | None:
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
                        f"before it and is not integrated yet"
                        + (" — it is in Backlog, which the phase scope never "
                           "pulls a card out of" if member["stage"] == "backlog"
                           else ""))
        for member in order:
            number = _number(member)
            if number == mine or number in done:
                continue
            if member["file"] in running or member["stage"] in ("in-progress",
                                                                 "review"):
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
    """What is still working this phase, in words: a legacy run in force,
    an agent on the phase card or a member, an operation on any of them
    with no receipt. Empty is quiescent."""
    found = []
    where = phases.run_state(phases.log_entries(phase.get("body") or ""))
    if where["state"] == "running":
        found.append("the phase runner has a run in force — ‖ hold the phase "
                     "first")
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
    """Hand a phase from the phase runner to its phase scope. Refused,
    with nothing written, unless the agreement names the phase, this
    installation executes, and the runner is quiescent. Changes no branch,
    worktree, member, log entry or integrated commit; writes one line."""
    phase, tasks = _find(filename)
    if mode(phase) == "scoped":
        raise Refused("already-adopted", f"{filename} is already run by its "
                      f"phase scope")
    if phase["stage"] not in ADOPTABLE:
        raise Refused("stage-not-adoptable", f"a phase in {phase['stage']}/ is "
                      f"not adopted; adopt one in " + ", ".join(ADOPTABLE))
    if phase.get("phaseDrift"):
        raise Refused("phase-drift", f"{filename} does not resolve: "
                      f"{phase['phaseDrift'][0]}")
    reading = agreements.effective()
    sid = f"phase:{_number(phase)}"
    if not (reading["configured"] and reading["valid"]) or \
            agreements.scope(reading, sid) is None:
        raise Refused("phase-not-in-agreement", f"the agreement in force names "
                      f"no {sid} scope; add the phase to the agreement first")
    owner = execowner.authorizes()
    if not owner["allowed"]:
        raise Refused(owner["code"], owner["reason"])
    busy = quiescence(phase, tasks)
    if busy:
        raise Refused("not-quiescent", "the phase is still working: "
                      + "; ".join(busy[:6]), working=busy)
    who = actor or taskfiles.actor_name() or "unknown"
    _log(phase, f"scope adopted by {who} — the {sid} scope schedules this "
                f"phase from here; the phase runner stands down")
    _record(phase["file"], "adopt", f"adopted into the {sid} scope by {who}; "
            f"branch, log, members and integrated commits unchanged")
    state.record_board_event({"kind": "phase", "actor": "board",
                              "file": filename,
                              "summary": f"{filename} adopted into its phase "
                                         f"scope by {who}"})
    state.broadcast({"type": "board"})
    return {"card": filename, "scope": sid, "mode": "scoped",
            "integrated": sorted(integrated(phase), key=int)}


def release(filename: str, actor: str | None = None) -> dict:
    """Hand a phase back to the phase runner, quiescent first. The runner
    does not resume by itself: ▸ run phase is a person's decision."""
    phase, tasks = _find(filename)
    if mode(phase) != "scoped":
        raise Refused("not-adopted", f"{filename} is not run by its phase scope")
    owner = execowner.authorizes()
    if not owner["allowed"]:
        raise Refused(owner["code"], owner["reason"])
    busy = quiescence(phase, tasks)
    if busy:
        raise Refused("not-quiescent", "the phase is still working: "
                      + "; ".join(busy[:6]), working=busy)
    who = actor or taskfiles.actor_name() or "unknown"
    _log(phase, f"scope released by {who} — the phase runner may run it again")
    _record(phase["file"], "release", f"released to the phase runner by {who}")
    state.broadcast({"type": "board"})
    return {"card": filename, "mode": "legacy"}


def _record(filename: str, action: str, summary: str) -> None:
    stage = taskfiles.find_stage_of(filename)
    if not stage:
        return
    try:
        decisions.note(filename, stage, "action", {
            "action": action, "operation": f"phase-{action}", "phase": "receipt",
            "outcome": {"adopt": "adopted", "release": "released"}[action],
            "summary": summary[:400]},
            key=f"phase-{action}-{time.time_ns()}")
    except (ValueError, OSError):
        pass


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
    answer = orchestrator.authorize("prepare", phase["file"])
    if not answer["allowed"]:
        return {"card": phase["file"], "action": "prepare",
                "outcome": "not-authorized", "code": answer["code"],
                "reason": answer["reason"]}
    op = _begin("prepare", phase, f"{phase['stage']}:{branch}", answer)
    if op is None:
        return None
    try:
        if phase["stage"] == "to-do":
            again = orchestrator.authorize("prepare", phase["file"])
            if not again["allowed"]:
                return _refused(op, again["code"], again["reason"])
            taskfiles.move_task(phase["file"], "to-do", "in-progress",
                                actor="orchestrator")
        note = phases.ensure_branch(_phase_record(phase))
    except (ValueError, OSError) as exc:
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
    record = _phase_record(phase)
    listed = {**member, "number": _number(member)}
    try:
        whole = phases.merge_member(record, listed)
    except phases.Halt as stop:
        raise ValueError(str(stop)) from None
    return {"whole": whole, "branch": agents.phase_branch(phase["file"])}


def run(board: dict) -> list[dict]:
    """A phase scope's own steps in one pass, for every adopted phase:
    prepare, freshen, finish. Called by `scheduler.run_pass` under its
    lock, before member launches are planned."""
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return []
    tasks = _tasks(board)
    running = _running()
    results = []
    for phase in [t for t in tasks if t.get("isPhase") and mode(t) == "scoped"]:
        here = agreements.scope(reading, f"phase:{_number(phase)}")
        if here is None or not here["active"]:
            continue
        try:
            done = prepare(phase)
            if done:
                results.append(done)
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

def projection(board: dict | None = None) -> dict:
    """Every phase with its scheduling owner and, for adopted ones, each
    member's state as the phase scope reads it. A read."""
    board = taskfiles.collect() if board is None else board
    tasks = _tasks(board)
    reading = agreements.effective()
    running = _running()
    out = []
    for phase in [t for t in tasks if t.get("isPhase")]:
        sid = f"phase:{_number(phase)}"
        named = bool(reading["configured"] and reading["valid"]
                     and agreements.scope(reading, sid))
        here = agreements.scope(reading, sid) if named else None
        entry = {"card": phase["file"], "number": _number(phase),
                 "stage": phase["stage"], "mode": mode(phase),
                 "named": named, "scope": sid if named else None,
                 "owner": ("phase scope" if named and mode(phase) == "scoped"
                           else "phase runner"),
                 "strategy": (here or {}).get("strategy"),
                 "integrated": sorted(integrated(phase), key=int)}
        if named and mode(phase) != "scoped":
            busy = quiescence(phase, tasks)
            entry["adoptable"] = not busy and phase["stage"] in ADOPTABLE
            entry["working"] = busy
        if mode(phase) == "scoped":
            rows = []
            done = set(entry["integrated"])
            for member in members(phase, tasks):
                number = _number(member)
                if number in done:
                    word, why = "integrated", "in the phase branch, not yet " \
                        "delivered to the application base"
                elif member["file"] in running:
                    word, why = "working", "a run is working on it"
                elif member["stage"] == "review":
                    word, why = "integrating", "its checks, review and " \
                        "integration are the phase scope's next steps"
                else:
                    blocked = may_start(member, tasks, running)
                    word, why = (("waiting", blocked[1]) if blocked else
                                 ("ready", "may start when a slot is free"))
                rows.append({"card": member["file"], "number": number,
                             "stage": member["stage"], "state": word,
                             "why": why})
            entry["members"] = rows
            entry["delivered"] = phase["stage"] == "done"
        out.append(entry)
    return {"phases": out, "branchPrefix": config.PHASE_BRANCH_PREFIX}
