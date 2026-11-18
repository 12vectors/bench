"""Bounded orchestration passes: observe, reconcile, plan, execute (task 141).

The scheduler is the half of the orchestration engine that *acts*. It
owns no policy and no second execution path: what may happen is
`orchestrator.authorize`'s answer, read again immediately before every
side effect; how implementation work starts is `agents.start_agent`, the
very path **▸ start work** takes, with its plan, profile, inputs and
template preflights; and a card is moved only through `taskfiles`. It
never runs git itself and never makes a worktree.

One pass, under one lock, in this order:

1. **Observe** — operations whose run has ended are given their receipt
   (the ending the registry saw), and a run launched by a board that has
   since restarted is checked against the process table: still alive, it
   keeps its slot (`unobserved`); gone, its ending is recorded as not seen.
   Nothing is cancelled to make a limit look met.
2. **Reconcile** — an intent with no receipt was left by a pass that did
   not finish. A run found on the card is its receipt. No plan recorded
   since the intent proves, by `agents.start_agent`'s own ordering (the
   plan is recorded before any process starts), that nothing ran:
   `abandoned`, and the attempt counts against transient retries. Anything
   else — a plan recorded, no run in sight — is `unknown` and **held**: the
   card is not launched again until a person says what happened.
3. **Plan** — every card a scope may schedule, in the order the
   agreement's scopes share capacity fairly: dependencies (and cycles),
   priority, phase run order, then card number; a declared `**Touches:**`
   overlap with running or chosen work waits, and an undeclared one is
   labelled *unknown*, never safe (D10). Each scope schedules only its own
   cards: a phase's members are the phase scope's, never the Bench
   scope's, and a phase the agreement does not name is the legacy runner's.
4. **Execute** — for each chosen card: authorize, record the intent
   durably (which reserves the slot and spends the start), then the side
   effect, then the receipt. Re-authorized after the card's move into
   in-progress and immediately before the launch, so a person's move, a
   takeover, an exclusion, a pause or an applied agreement reaches the
   very next action.

**Nothing runs while it is off.** With no agreement, an invalid one, or a
Bench scope not enabled, a pass reads the agreement and returns. Reading
the schedule (`projection`) is a read: it writes, launches, reserves and
acquires nothing. Only the beat, on the designated installation holding
its execution lock, executes.

**Failures are classified, not retried blindly (D9).** A refusal before
anything happened (`refused`) is not retried until the brief changes or a
person releases it. A transient failure before anything ran (`retry`) is
retried up to the scope's `transientRetries` ceiling with exponential
backoff, behind a circuit breaker. Anything unexplained is `unknown`.
A run that ended — done, failed, declined — is never relaunched by this
pass on the same brief: that is a person's, or a later recovery member's,
decision.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import agents
import agreements
import config
import decisions
import execowner
import operations
import orchestrator
import plans
import state
import taskfiles

ACTION = "implement"
STAGES = ("to-do", "in-progress")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
# Words in a refusal that mark a failure as worth trying again: a launch
# that could not start a process, a network wait that ran out. Nothing
# else is presumed transient (D9).
TRANSIENT = ("timed out", "timeout", "could not launch adapter",
             "temporarily unavailable", "connection reset", "try again")
BACKOFF_SECONDS = 60
BREAKER_THRESHOLD = 3
BREAKER_SECONDS = 600
# What authorize answers when the card is fine but there is no room now.
WAITING = ("slots-full", "daily-starts-exhausted")

_PASS_LOCK = threading.Lock()
BREAKER = {"failures": 0, "openUntil": 0.0}
LAST: dict = {"pass": None}


# ── reading the board ─────────────────────────────────────────────────

def _tasks(board: dict) -> list[dict]:
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def basis(task: dict) -> str:
    """What a launch is decided against: the brief's revision. Reports,
    reviews and orchestration records are history, so an agent's own
    report or a receipt never makes the same brief a new question."""
    body = task.get("body")
    if body is None:
        try:
            body = (config.TASKS / task["stage"] / task["file"]).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            body = ""
    return taskfiles.brief_sha(taskfiles.card_parts(body)["brief"])[:16]


def cycles(tasks: list[dict]) -> set[str]:
    """Every card number on a dependency cycle (a self-dependency
    included), by Tarjan's strongly connected components."""
    graph = {}
    for task in tasks:
        number = _number(task)
        if number:
            graph[number] = [d for d in task.get("dependsOn") or []]
    index: dict = {}
    low: dict = {}
    stack: list = []
    on: set = set()
    found: set = set()
    counter = [0]

    def visit(node: str) -> None:
        # iterative to keep deep chains off the interpreter's stack
        work = [(node, iter(graph.get(node, ())))]
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on.add(node)
        while work:
            current, edges = work[-1]
            advanced = False
            for nxt in edges:
                if nxt not in graph:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(graph.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on:
                    low[current] = min(low[current], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[current])
            if low[current] == index[current]:
                component = []
                while True:
                    member = stack.pop()
                    on.discard(member)
                    component.append(member)
                    if member == current:
                        break
                if len(component) > 1 or current in graph.get(current, ()):
                    found.update(component)

    for node in graph:
        if node not in index:
            visit(node)
    return found


def _dependency(task: dict, by_number: dict, looped: set) -> tuple[str, str] | None:
    """Why this card may not start yet because of what it depends on."""
    number = _number(task)
    if number in looped:
        return ("dependency-cycle", f"card {number} is on a dependency cycle; "
                "no card in it can start until the loop is broken")
    for dep in task.get("dependsOn") or []:
        other = by_number.get(dep)
        if other is None:
            return ("dependency-unknown", f"it depends on card {dep}, which is "
                    f"not on this board")
        same_phase = (task.get("phase") and other.get("phase") and
                      task["phase"]["number"] == other["phase"]["number"])
        done = other["stage"] == "done" or (same_phase and
                                            other["stage"] in ("review", "done"))
        if not done:
            return ("waiting-dependency", f"it depends on card {dep}, which is "
                    f"in {other['stage']}/")
    return None


def overlap(a: list[str] | None, b: list[str] | None) -> bool | None:
    """Do two `**Touches:**` declarations overlap? None is *unknown*: one
    side declared nothing, and nothing is inferred from prose."""
    if a is None or b is None:
        return None
    for x in a:
        for y in b:
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return True
    return False


def _priority(task: dict) -> int:
    return PRIORITY_RANK.get((task.get("priority") or "").strip().lower(), 3)


def _running_cards() -> set[str]:
    with state.LOCK:
        return {r.get("task") for r in state.AGENTS.values()
                if r.get("status") == "running"}


# ── the plan ──────────────────────────────────────────────────────────

def _held(task: dict, ops: list[dict], here_basis: str,
          ceiling: int) -> tuple[str, str, int] | None:
    """What the operation ledger says about launching this card on this
    brief: (code, reason, 0) when it holds or blocks, else None. Anything
    open on the card holds it whatever its brief; a settled outcome
    (ended, refused, retries spent) holds only the brief it was about."""
    mine = operations.for_card(task["file"], ACTION, ops)
    for op in mine:
        if op["status"] == "unknown":
            return ("outcome-unknown", f"operation {op['key']} has an unknown "
                    f"outcome; it is held until a person says what happened", 0)
        if op["status"] in ("intent", "launched", "unobserved"):
            return ("in-flight", f"operation {op['key']} is {op['status']}", 0)
    same = [op for op in mine if op.get("basis") == here_basis]
    for op in same:
        if op["status"] == "ended":
            return ("ran", "a run on this brief has ended "
                    f"({op.get('outcome') or 'ended'}); launching again is a "
                    f"person's decision, or follows a changed brief", 0)
        if op["status"] == "refused":
            return ("refused", f"the launch was refused: {op.get('reason')} "
                    f"— change what it names, or release it", 0)
    tries = [op for op in same if op["status"] in ("retry", "abandoned")]
    if len(tries) > ceiling:
        return ("retries-exhausted", f"{len(tries)} attempts ended without a "
                f"launch; the ceiling is {ceiling} retries", 0)
    if tries:
        wait = BACKOFF_SECONDS * (2 ** (len(tries) - 1))
        last = tries[-1].get("updatedEpoch") or tries[-1].get("intentEpoch") or 0
        if time.time() < last + wait:
            return ("backing-off", f"attempt {len(tries) + 1} waits "
                    f"{wait}s after the last transient failure", 0)
    return None


def plan(board: dict | None = None) -> dict:
    """The schedule as it stands: every card a scope could launch work on,
    ready, waiting or blocked, each with its code and reason, and the ones
    this pass would choose. Deterministic, and a read: it reserves,
    launches and writes nothing."""
    board = taskfiles.collect() if board is None else board
    tasks = _tasks(board)
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return {"status": "off" if not reading["configured"] else "invalid",
                "items": [], "chosen": []}
    ops = operations.all_ops()
    by_number = {_number(t): t for t in tasks if _number(t)}
    looped = cycles(tasks)
    running = _running_cards()
    items, ready = [], []
    for task in tasks:
        if task["stage"] not in STAGES:
            continue
        sid, why = orchestrator.scope_of(task, reading)
        item = {"card": task["file"], "number": _number(task),
                "stage": task["stage"], "scope": sid,
                "priority": task.get("priority"),
                "touches": task.get("touches")}
        items.append(item)
        if sid is None:
            item.update(state="unmanaged", code=why,
                        reason="its phase has no phase agreement; the existing "
                               "phase runner schedules it")
            continue
        if task.get("isPhase"):
            item.update(state="unmanaged", code="phase-card",
                        reason="a phase card is run by its phase scope, not "
                               "launched as work")
            continue
        if task["file"] in running:
            item.update(state="working", code="running",
                        reason="a run is working on it")
            continue
        here = agreements.scope(reading, sid)
        ceiling = here["ceilings"]["transientRetries"]["value"] if here else 0
        held = _held(task, ops, basis(task), ceiling)
        if held:
            item.update(state="held" if held[0] in ("outcome-unknown", "in-flight")
                        else "blocked", code=held[0], reason=held[1])
            continue
        dep = _dependency(task, by_number, looped)
        if dep:
            item.update(state="blocked", code=dep[0], reason=dep[1])
            continue
        answer = orchestrator.authorize(ACTION, task["file"], board=board)
        if not answer["allowed"]:
            item.update(state="waiting" if answer["code"] in WAITING else "blocked",
                        code=answer["code"], reason=answer["reason"])
            continue
        item.update(state="ready", code="ready", reason="authorized")
        ready.append((task, item))
    chosen = _choose(ready, reading, tasks, running, by_number)
    return {"status": "on", "items": items,
            "chosen": [item["card"] for _task, item in chosen],
            "_chosen": chosen}


def _choose(ready: list, reading: dict, tasks: list[dict], running: set[str],
            by_number: dict) -> list:
    """Fair, bounded selection: within a scope by priority, phase run
    order and number; across scopes round-robin, the scope with the
    fewest starts today first; within the free slots and starts; and never
    two declared overlaps at once."""
    if not ready:
        return []
    used = orchestrator.usage(reading, tasks)
    pool = orchestrator.LAUNCHES[ACTION]
    per_scope: dict = {}
    for task, item in sorted(ready, key=lambda p: (
            _priority(p[0]), (p[0].get("phase") or {}).get("index", 0),
            int(_number(p[0]) or 10 ** 9), p[0]["file"])):
        per_scope.setdefault(item["scope"], []).append((task, item))
    order = sorted(per_scope, key=lambda sid: (used["startsToday"].get(sid, 0), sid))
    interleaved = []
    while any(per_scope[s] for s in order):
        for sid in order:
            if per_scope[sid]:
                interleaved.append(per_scope[sid].pop(0))
    bench = agreements.scope(reading, agreements.BENCH)
    active = [t.get("touches") for t in tasks if t["file"] in running]
    active_names = [t["file"] for t in tasks if t["file"] in running]
    taken_scope: dict = {}
    taken_total = 0
    starts = dict(used["startsToday"])
    starts_total = used["startsTodayTotal"]
    chosen = []
    for task, item in interleaved:
        sid = item["scope"]
        here = agreements.scope(reading, sid)
        slot = orchestrator.pool_for(pool, here)
        mine = used["running"].get(sid, {}).get(slot, 0) + taken_scope.get((sid, slot), 0)
        if mine >= here["capacity"][slot]["own"] or \
                used["totals"].get(slot, 0) + taken_total >= \
                bench["capacity"][slot]["total"]:
            item.update(state="waiting", code="slots-full",
                        reason=f"no free {slot} slot this pass")
            continue
        if starts.get(sid, 0) >= here["capacity"]["dailyStarts"]["own"] or \
                starts_total >= bench["capacity"]["dailyStarts"]["total"]:
            item.update(state="waiting", code="daily-starts-exhausted",
                        reason="no launch left today")
            continue
        clash = next((name for name, other in zip(active_names, active)
                      if overlap(task.get("touches"), other)), None)
        if clash:
            item.update(state="waiting", code="touches-overlap",
                        reason=f"its declared Touches overlap {clash}, which is "
                               f"running or chosen")
            continue
        unknown = [name for name, other in zip(active_names, active)
                   if overlap(task.get("touches"), other) is None]
        item["overlap"] = ("unknown" if unknown else "none")
        if unknown:
            item["overlapUnknownWith"] = unknown
        taken_scope[(sid, slot)] = taken_scope.get((sid, slot), 0) + 1
        taken_total += 1
        starts[sid] = starts.get(sid, 0) + 1
        starts_total += 1
        active.append(task.get("touches"))
        active_names.append(task["file"])
        item.update(state="chosen", code="chosen", reason="chosen this pass")
        chosen.append((task, item))
    return chosen


def projection(board: dict | None = None) -> dict:
    """What `GET /api/orchestration` shows of the scheduler: the plan, the
    circuit breaker and the last pass. A read — it launches, reserves,
    writes and acquires nothing."""
    found = plan(board)
    found.pop("_chosen", None)
    return {**found, "breaker": breaker(), "lastPass": LAST["pass"],
            "interval": config.ORCHESTRATE_INTERVAL,
            "executes": execowner.authorizes()["allowed"],
            "operations": operations.summary()}


def breaker() -> dict:
    open_ = time.time() < BREAKER["openUntil"]
    return {"open": open_, "failures": BREAKER["failures"],
            "until": BREAKER["openUntil"] if open_ else None}


# ── observe and reconcile ─────────────────────────────────────────────

def _process_alive(pid, agent_id: str) -> bool:
    """Is a process with this pid still the run named `agent_id`? Read
    from /proc, where a run's environment names its agent id; a reused
    pid is someone else's and reads as gone."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    return f"BOARD_AGENT_ID={agent_id}".encode() in environ.split(b"\x00")


def _note(op: dict, phase: str, outcome: str, summary: str) -> None:
    """The public record on the card (D11): the action's intent or its
    receipt, once per operation and phase. A card that cannot be written
    never changes what happened; the ledger holds the operation either way."""
    stage = taskfiles.find_stage_of(op["file"])
    if not stage:
        return
    try:
        decisions.note(op["file"], stage, "action", {
            "action": op["action"], "operation": op["key"], "phase": phase,
            "outcome": outcome, "summary": summary[:400]},
            key=f"{op['key']}-{phase}-{outcome}")
    except (ValueError, OSError):
        pass


def observe(ops: list[dict] | None = None) -> list[dict]:
    """Record what became of launched runs. Writes receipts only."""
    changed = []
    for op in operations.all_ops() if ops is None else ops:
        if op["status"] not in ("launched", "unobserved"):
            continue
        with state.LOCK:
            record = dict(state.AGENTS.get(op.get("agent") or "", {}))
        if record and record.get("status") == "running":
            continue
        if record:
            outcome = record.get("status") or "ended"
            done = operations.update(op["key"], "ended", outcome=outcome,
                                     rc=record.get("rc"),
                                     observed="the run's ending was observed")
            _note(done, "receipt", outcome if outcome.isalpha() else "ended",
                  f"the implementation run ended: {outcome}")
            changed.append(done)
            continue
        alive = _process_alive(op.get("pid"), op.get("agent") or "")
        if alive:
            if op["status"] != "unobserved" or not op.get("alive"):
                changed.append(operations.update(
                    op["key"], "unobserved", alive=True,
                    observed="the board restarted while this run was in flight; "
                             "its process is still running, so it keeps its slot"))
            continue
        done = operations.update(op["key"], "ended", outcome="unobserved",
                                 alive=False,
                                 observed="the board restarted and the run's "
                                          "process is gone; its ending was not seen")
        _note(done, "receipt", "unobserved", "the run ended while the board was "
              "not watching; its ending was not seen")
        changed.append(done)
    return changed


def reconcile(ops: list[dict] | None = None) -> list[dict]:
    """Settle every intent that has no receipt, from authoritative state.
    Never repeats a side effect; never reads absence as proof unless
    bench's own ordering makes it one."""
    changed = []
    for op in operations.all_ops() if ops is None else ops:
        if op["status"] != "intent":
            continue
        with state.LOCK:
            found = [dict(r, id=k) for k, r in state.AGENTS.items()
                     if r.get("task") == op["file"] and r.get("mode") == "work"
                     and (r.get("started") or 0) >= (op.get("intentEpoch") or 0) - 1]
        if found:
            run = found[-1]
            pid = getattr(run.get("proc"), "pid", None)
            done = operations.update(op["key"], "launched", agent=run["id"],
                                     pid=pid, observed="reconciled: a run was "
                                     "found on the card after the intent")
            _note(done, "receipt", "launched", "reconciled: the launch was found")
            changed.append(done)
            continue
        recorded = plans.recorded(op["file"]) or {}
        if (recorded.get("recorded") or 0) >= (op.get("intentEpoch") or 0):
            done = operations.update(
                op["key"], "unknown", code="launch-unconfirmed",
                reason="a plan was recorded after the intent but no run is "
                       "tracked: a process may have started; held for a person",
                observed="reconciled: outcome unknown")
            _note(done, "receipt", "unknown", "the launch's outcome is unknown; "
                  "held until a person says what happened")
            changed.append(done)
            continue
        done = operations.update(
            op["key"], "abandoned", code="no-launch",
            reason="no plan was recorded after the intent, and a launch records "
                   "its plan before any process starts: nothing ran",
            observed="reconciled: nothing ran")
        changed.append(done)
    return changed


# ── execute ───────────────────────────────────────────────────────────

def _transient(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in TRANSIENT)


def _walk_back(filename: str, moved: bool) -> None:
    if moved and taskfiles.find_stage_of(filename) == "in-progress":
        try:
            taskfiles.move_task(filename, "in-progress", "to-do",
                                actor="orchestrator")
        except (ValueError, OSError):
            pass


def execute(task: dict, item: dict, board: dict | None = None,
            start=None) -> dict:
    """One launch, with its intent recorded before and its receipt after.
    `start` is the launch path (`agents.start_agent`); a test passes a
    stand-in to play a crash or a lost reply."""
    start = agents.start_agent if start is None else start
    filename = task["file"]
    answer = orchestrator.authorize(ACTION, filename, board=board)
    if not answer["allowed"]:
        return {"card": filename, "outcome": "not-authorized",
                "code": answer["code"], "reason": answer["reason"]}
    here_basis = basis(task)
    prior = operations.for_card(filename, ACTION)
    # The attempt counts only attempts that provably ran nothing, so the
    # same decision taken again while its first attempt is open, launched
    # or ended has the same key — and `begin` refuses it as a duplicate.
    attempt = len([o for o in prior if o.get("basis") == here_basis
                   and o["status"] in operations.NOTHING_RAN]) + 1
    key = operations.key_for(ACTION, filename, here_basis, attempt)
    try:
        op = operations.begin(key, action=ACTION, card=filename,
                              number=_number(task), scope=answer["scope"],
                              pool=orchestrator.LAUNCHES[ACTION],
                              basis=here_basis, attempt=attempt,
                              note="; ".join(answer.get("provenance") or []))
    except operations.Exists as exists:
        return {"card": filename, "outcome": "duplicate", "key": exists.op["key"]}
    _note(op, "intent", "intended", f"launching implementation work "
          f"(attempt {attempt}) under the {answer['scope']} scope")
    moved = False
    try:
        stage = taskfiles.find_stage_of(filename)
        if stage == "to-do":
            again = orchestrator.authorize(ACTION, filename, operation=key)
            if not again["allowed"]:
                return _refused(op, again["code"], again["reason"], moved)
            taskfiles.move_task(filename, "to-do", "in-progress", actor="orchestrator")
            moved = True
            stage = "in-progress"
        if stage != "in-progress":
            return _refused(op, "stage-moved", f"the card is in {stage}/ now", moved)
        # immediately before the side effect: the agreement, the card's
        # mover, its holder and its scope's pause as they are now
        again = orchestrator.authorize(ACTION, filename, operation=key)
        if not again["allowed"]:
            return _refused(op, again["code"], again["reason"], moved)
        record = start(filename, "in-progress")
    except ValueError as exc:
        message = str(exc)
        _walk_back(filename, moved)
        if _transient(message):
            BREAKER["failures"] += 1
            if BREAKER["failures"] >= BREAKER_THRESHOLD:
                BREAKER["openUntil"] = time.time() + BREAKER_SECONDS
            operations.update(key, "retry", code="transient", reason=message)
            return {"card": filename, "outcome": "retry", "key": key,
                    "reason": message}
        return _refused(op, "launch-refused", message, False)
    except Exception as exc:  # noqa: BLE001 — anything unexplained is unknown
        done = operations.update(key, "unknown", code="launch-error",
                                 reason=f"{type(exc).__name__}: {exc}",
                                 observed="the launch raised; whether a run "
                                          "started is not known")
        _note(done, "receipt", "unknown", "the launch's outcome is unknown; held")
        return {"card": filename, "outcome": "unknown", "key": key}
    agent_id = (record or {}).get("id")
    with state.LOCK:
        proc = (state.AGENTS.get(agent_id or "") or {}).get("proc")
    BREAKER["failures"] = 0
    done = operations.update(key, "launched", agent=agent_id,
                             pid=getattr(proc, "pid", None),
                             observed="the launch returned its run")
    _note(done, "receipt", "launched", f"implementation work started "
          f"({(record or {}).get('name') or agent_id})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"orchestrator launched {filename} under the "
                   f"{answer['scope']} scope ({key})"})
    return {"card": filename, "outcome": "launched", "key": key,
            "agent": agent_id}


def _refused(op: dict, code: str, reason: str, moved: bool) -> dict:
    _walk_back(op["file"], moved)
    done = operations.update(op["key"], "refused", code=code, reason=reason)
    _note(done, "receipt", "refused", f"not launched: {reason}")
    return {"card": op["file"], "outcome": "refused", "key": op["key"],
            "code": code, "reason": reason}


# ── the pass ──────────────────────────────────────────────────────────

def _enabled() -> bool:
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return False
    bench = agreements.scope(reading, agreements.BENCH)
    return bool(bench and bench["enabled"])


def run_pass(board: dict | None = None, start=None) -> dict:
    """One bounded pass. Off, it reads the agreement and returns; not the
    owner, it returns before observing anything. Holds one lock, so two
    passes never interleave in this process; the OS execution lock keeps
    a second process out."""
    if not _enabled():
        return {"ran": False, "why": "off"}
    owner = execowner.reading()
    if owner["role"] == "designated-here" and not owner["lockHeld"]:
        execowner.acquire_lock()
    allowed = execowner.authorizes()
    if not allowed["allowed"]:
        return {"ran": False, "why": allowed["code"]}
    if not _PASS_LOCK.acquire(blocking=False):
        return {"ran": False, "why": "busy"}
    try:
        started = time.time()
        observed = observe()
        reconciled = reconcile()
        board = taskfiles.collect() if board is None else board
        found = plan(board)
        results = []
        if time.time() < BREAKER["openUntil"]:
            results.append({"outcome": "breaker-open",
                            "until": BREAKER["openUntil"]})
        else:
            for task, _item in found.get("_chosen", []):
                results.append(execute(task, _item, None, start))
        summary = {"at": operations.now_iso(), "seconds": round(time.time() - started, 3),
                   "observed": len(observed), "reconciled": len(reconciled),
                   "chosen": found.get("chosen", []),
                   "results": [{k: v for k, v in r.items() if k != "reason"}
                               | ({"reason": str(r["reason"])[:200]}
                                  if r.get("reason") else {}) for r in results]}
        LAST["pass"] = summary
        if results:
            state.broadcast({"type": "board"})
        return {"ran": True, **summary}
    finally:
        _PASS_LOCK.release()


def beat(interval: float | None = None) -> None:
    """The scheduler's thread: one pass every interval, forever. A pass
    that raises is reported and the next one runs as usual."""
    while True:
        time.sleep(interval or config.ORCHESTRATE_INTERVAL)
        try:
            run_pass()
        except Exception as exc:  # noqa: BLE001 — the beat must survive
            state.record_board_event({
                "kind": "error", "actor": "board", "file": None,
                "summary": f"orchestration pass failed: {type(exc).__name__}: "
                           f"{str(exc)[:200]}"})


# ── a person's settlement of a held operation ─────────────────────────

RESOLUTIONS = ("launched", "not-launched", "release")


def resolve(key: str, decision: str, actor: str | None = None) -> dict:
    """A person says what happened to an operation the scheduler could
    not settle: an `unknown` outcome was in fact `launched` (its run is
    recorded as ended, its start spent) or `not-launched` (nothing ran; a
    new attempt may follow), or a `refused` launch is `release`d to be
    tried again on the same brief. Nothing is launched by resolving."""
    if decision not in RESOLUTIONS:
        raise ValueError(f"{decision!r} is not one of " + ", ".join(RESOLUTIONS))
    op = operations.read(key)
    if op is None:
        raise LookupError(f"no operation {key} on record")
    who = actor or "unknown"
    if decision == "release":
        if op["status"] != "refused":
            raise ValueError(f"only a refused operation is released; this one "
                             f"is {op['status']}")
        return operations.public(operations.update(
            key, "abandoned", resolvedBy=who,
            observed="released by a person: it may be tried again"))
    if op["status"] != "unknown":
        raise ValueError(f"only an unknown outcome is resolved; this one is "
                         f"{op['status']}")
    if decision == "launched":
        done = operations.update(key, "ended", outcome="resolved", resolvedBy=who,
                                 observed="a person said a run was launched")
    else:
        done = operations.update(key, "abandoned", resolvedBy=who,
                                 observed="a person said nothing was launched")
    _note(done, "receipt", "resolved", f"{who} settled the unknown outcome: "
          f"{decision}")
    return operations.public(done)
