"""Asking the orchestrator: a person's free-text question, answered by a
bounded reasoning run and kept as a public conversation (task 181).

A person asks "Why is card 175 still in To Do?" or "What is consuming
capacity?" from the Orchestrator view or a card's detail view. This
module turns that into what bench already has, and nothing new:

- **One reasoning run, through the shared lifecycle.** A submission is
  `reasoning.request(…, need="ask", via="person")`: the same `reason`
  intent (no worktree, no shell, no edit tool), the same pinned delivery
  by exact reads, the same validation — extended with the answer's shape
  (`now`, `why`, `next`, `unblock`, recorded findings citing evidence,
  `gaps`, `suggestions`) — the same duplicate and retry rules, and the
  same accounting. No second agent stack and no scheduler.
- **Context bench reads, bounded and labelled.** The facts the run is
  given are bench's own readings at one moment (`snapshot`): the
  scheduler's plan and refusal codes, capacity and today's starts, the
  agreement and who executes, the runs in flight and their last
  agent-reported progress, recent operations, delivery steps, and — for
  a card — its eligibility and evidence. Every list is capped and says
  how much it left out; what is not included is said in `exclusions`.
  The question itself is data in that document, never prompt text.
- **Asking is not acting.** The answer proposes no action and opens no
  question (the contract refuses both); a suggestion may point at an
  existing control by name, and the page turns that into a link to where
  the control lives, with its own confirmation and preflight. Nothing
  here moves a card, starts or stops work, runs a check, edits the
  agreement or changes a budget.
- **The conversation is public and synced.** A card's (or a phase
  card's) exchanges are `ask` and `reply` records on that card; a
  board-wide conversation is its own file under the work state's
  `orchestration/conversations/`, committed and synced like a card
  (`decisions`). Runtime job records stay machine-local and are never
  the only copy.

Reading — `conversations`, `thread`, `preflight`, `facts_for` — launches
nothing and writes nothing. Only `submit` and `retry` launch, and only on
a person's explicit request.
"""

from __future__ import annotations

import time

import agreements
import config
import decisions
import operations
import orchestrator
import progress
import reasoning
import scheduler
import state
import taskfiles

MAX_QUESTION = 1000
MAX_LIST = 40
MAX_RUNS = 20
MAX_OPS = 12
NO_DELEGATION = ("Asking needs no orchestration authority: it launches one "
                 "read-only reasoning run on your request. Whether the "
                 "orchestrator may act on a card is a separate grant in the "
                 "operating agreement, and asking changes nothing about it.")


class Refused(ValueError):
    """The question was not asked: nothing was written and nothing ran."""

    def __init__(self, message: str, code: str, remedy: str | None = None):
        super().__init__(message)
        self.code = code
        self.remedy = remedy


def _tasks(board: dict) -> list[dict]:
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _number(task: dict | None) -> str | None:
    if not task or not task.get("number"):
        return None
    return taskfiles.canonical_number(task["number"])


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))


# ── what a question is about ──────────────────────────────────────────


def resolve_target(scope, board: dict) -> dict:
    """`{"kind": "board"}`, or `{"kind": "card"|"phase", "card": <number
    or file stem>}` — resolved against the board as it is. A card that is
    a phase card is the phase's scope; a phase scope on an ordinary card
    is refused rather than guessed."""
    if not isinstance(scope, dict) or scope.get("kind") not in decisions.ASK_SCOPES:
        raise Refused("say what the question is about: the board, a phase or a card",
                      "scope-invalid")
    if scope["kind"] == "board":
        return {"kind": "board", "subject": "board"}
    raw = str(scope.get("card") or "").strip()
    if not raw:
        raise Refused("name the card the question is about", "scope-invalid")
    tasks = _tasks(board)
    wanted = taskfiles.canonical_number(raw) if raw.isdigit() else None
    found = [t for t in tasks if (wanted and _number(t) == wanted)
             or t["file"][:-3] == raw]
    if not found:
        raise Refused(f"no card {raw} on this board", "card-unknown",
                      "choose a card that is on this board")
    if len(found) > 1:
        raise Refused(f"two cards carry {raw}; name it by its file name",
                      "card-ambiguous")
    task = found[0]
    if task["stage"] == "archive" or task["stage"] not in config.STAGE_DIRS:
        raise Refused(f"{task['file']} is archived", "card-unknown")
    kind = "phase" if task.get("isPhase") else "card"
    if scope["kind"] == "phase" and kind != "phase":
        raise Refused(f"card {raw} is not a phase card", "scope-invalid",
                      "ask about the card, or choose its phase")
    return {"kind": kind, "file": task["file"], "stage": task["stage"],
            "subject": _number(task) or task["file"][:-3],
            "title": task.get("title")}


# ── the facts a question is answered from ─────────────────────────────


def _cut(items: list, limit: int) -> tuple[list, int]:
    return items[:limit], max(len(items) - limit, 0)


def _runs(files: set[str] | None, tasks: list[dict]) -> tuple[list, int]:
    """Running agents, as bench observes them: process facts apart from
    what each run last *said* (agent-reported)."""
    by_file = {t["file"]: t for t in tasks}
    with state.LOCK:
        running = [dict(r) for r in state.AGENTS.values()
                   if r.get("status") == "running"]
    out = []
    for record in sorted(running, key=lambda r: r.get("started") or 0):
        if files is not None and record.get("task") not in files:
            continue
        task = by_file.get(record.get("task"))
        entry = {"card": _number(task) if task else None,
                 "mode": record.get("mode") or "work",
                 "agent": record.get("name"), "model": record.get("model"),
                 "started": _iso(record.get("started")) if record.get("started") else None,
                 "observed": "a process bench launched and is watching"}
        said = progress.public(record["id"]) if record.get("id") else None
        current = (said or {}).get("current") or {}
        if current:
            entry["lastSaid"] = {k: current.get(k) for k in
                                 ("stage", "doing", "blocked", "at") if current.get(k)}
            entry["lastSaid"]["label"] = "agent-reported, not verified"
        out.append(entry)
    return _cut(out, MAX_RUNS)


PLAN_KEYS = ("number", "stage", "scope", "state", "code", "reason", "priority")


def _plan_items(items, numbers: set[str] | None) -> tuple[list, int]:
    out = [{k: item.get(k) for k in PLAN_KEYS if item.get(k) is not None}
           for item in items or []
           if numbers is None or str(item.get("number")) in numbers]
    return _cut(out, MAX_LIST)


def _operations(numbers: set[str] | None) -> tuple[list, int]:
    try:
        ops = operations.all_ops()
    except (OSError, ValueError):
        return [], 0
    out = []
    for op in reversed(ops):
        if numbers is not None and str(op.get("card")) not in numbers:
            continue
        out.append({k: op.get(k) for k in ("key", "card", "action", "status",
                                            "scope", "day", "outcome")
                    if op.get(k) is not None})
    return _cut(out, MAX_OPS)


def _capacity_facts(tasks: list[dict]) -> dict:
    reading = agreements.effective()
    if not reading["configured"]:
        return {"agreement": "none", "note": "no operating agreement is in "
                "force, so there are no slots or daily starts to count against"}
    if not reading["valid"]:
        return {"agreement": "invalid", "problems": reading["problems"][:5]}
    used = orchestrator.usage(reading, tasks)
    scopes = []
    for scope in reading["scopes"]:
        scopes.append({
            "id": scope["id"], "paused": bool(scope.get("paused")),
            "slots": {pool: {"allowed": scope["capacity"][pool].get("own"),
                             "inUse": used["running"].get(scope["id"], {}).get(pool, 0)}
                      for pool in ("implementation", "review", "reasoning")
                      if pool in scope["capacity"]},
            "dailyStarts": {"allowed": scope["capacity"]["dailyStarts"].get("own"),
                            "usedToday": used["startsToday"].get(scope["id"], 0)}})
    return {"agreement": "in force", "revision": reading["revision"],
            "totals": used["totals"], "reserved": used["reserved"],
            "unscoped": used["unscoped"],
            "startsTodayTotal": used["startsTodayTotal"], "scopes": scopes,
            "note": "every running agent counts, a person's own included; a "
                    "recorded intent reserves its slot before it launches"}


def snapshot(target: dict, board: dict) -> dict:
    """Bench's readings for one question, at one moment: what the run is
    given as `ask.readings` in its facts, and what `facts_for` shows a
    person. Bounded; omitted counts and exclusions are said. A read — it
    asks the scheduler for its plan, which reserves and launches nothing."""
    tasks = _tasks(board)
    projection = scheduler.projection(board)
    summary = orchestrator.board_summary(board)
    numbers: set[str] | None = None
    files: set[str] | None = None
    if target["kind"] != "board":
        task = next(t for t in tasks if t["file"] == target["file"])
        members = [m["file"] for m in task.get("members") or []] \
            if task.get("isPhase") else []
        files = {task["file"], *members}
        numbers = {_number(t) for t in tasks if t["file"] in files and _number(t)}
    plan, plan_left = _plan_items(projection.get("items"), numbers)
    delivery, delivery_left = _plan_items(
        (projection.get("delivery") or {}).get("items"), numbers)
    runs, runs_left = _runs(files, tasks)
    ops, ops_left = _operations(numbers)
    stages = {}
    for task in tasks:
        stages[task["stage"]] = stages.get(task["stage"], 0) + 1
    cards = [{"number": _number(t), "title": t.get("title"), "stage": t["stage"],
              "assignee": t.get("assignee"),
              "dependsOn": t.get("dependsOn") or [],
              "phase": (t.get("phase") or {}).get("number")}
             for t in tasks if t["stage"] in orchestrator.MANAGED_STAGES
             and (files is None or t["file"] in files)]
    cards, cards_left = _cut(cards, MAX_LIST)
    readings = {
        "taken": _iso(),
        "agreement": {"status": summary["status"], "revision": summary["revision"]},
        "executes": projection.get("executes"),
        "breaker": {k: (projection.get("breaker") or {}).get(k)
                    for k in ("open", "until", "reason", "cards")},
        "lastPass": projection.get("lastPass"),
        "stages": stages,
        "cards": cards,
        "plan": plan, "delivery": delivery,
        "running": runs, "operations": ops,
        "capacity": _capacity_facts(tasks),
        "omitted": {"cards": cards_left, "plan": plan_left,
                    "delivery": delivery_left, "running": runs_left,
                    "operations": ops_left},
        "exclusions": [
            "Backlog, Done and Archive cards are counted by stage, not listed",
            "a past scheduler pass keeps no reason for a choice it did not "
            "make; only the plan as it stands now is here",
            "agent-reported progress is labelled; it is not verified",
            "check evidence is listed for a card question only; ask about "
            "the card for it"],
    }
    if target["kind"] != "board":
        readings["subject"] = {"card": target["subject"], "kind": target["kind"],
                               "stage": target["stage"]}
    readings["cardNumbers"] = sorted({c["number"] for c in cards if c["number"]}
                                     | {str(p.get("number")) for p in plan
                                        if p.get("number")})
    return readings


# ── before asking: what it would cost and whether it can run ──────────


def _person_capacity(board: dict) -> dict | None:
    """A person's question takes a reasoning slot like any reasoning run
    (an implementation slot where the agreement allocates none). It spends
    no orchestrator daily start. With every slot of its pool in use, it is
    refused rather than run over the board's capacity."""
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return None
    bench = agreements.scope(reading, agreements.BENCH)
    if bench is None:
        return None
    pool = orchestrator.pool_for("reasoning", bench)
    used = orchestrator.usage(reading, _tasks(board))
    total = bench["capacity"][pool]["total"]
    if used["totals"].get(pool, 0) >= total:
        return {"code": "slots-full", "pool": pool,
                "reason": f"all {total} {pool} slots on this board are in use "
                          f"({used['totals'].get(pool, 0)} running or reserved)",
                "remedy": "wait for a run to finish, or ‖ hold one from its "
                          "card; raising the agreement's capacity is a "
                          "separate decision in Orchestrator → Operating agreement"}
    return None


def preflight(scope=None, board: dict | None = None) -> dict:
    """What asking would launch and count, before anything is asked: the
    harness and model the `reason` intent resolves to, whether it can run
    here, the capacity it takes, and that orchestration authority is not
    needed. A read: nothing is launched, pinned or written."""
    board = taskfiles.collect() if board is None else board
    ready = reasoning.readiness()
    selection = ready["selection"]
    out = {
        "ready": True, "code": None, "reason": None, "remedy": None,
        "harness": selection["harness"]["name"],
        "model": selection["model"]["value"] or None,
        "modelSource": selection["model"]["source"],
        "modelSetting": selection["model"]["setting"],
        "accounting": ("One read-only reasoning run per question: it takes a "
                       "reasoning slot while it runs (an implementation slot "
                       "where none is allocated) and spends none of the "
                       "orchestrator's daily starts. Token use and cost are not "
                       "reported by the harness."),
        "delegation": NO_DELEGATION,
        "orchestration": orchestrator.board_summary(board)["status"],
        "readOnly": ("the run reads bench's facts and the cards; it has no "
                     "shell, no edit tool and no worktree, and its answer "
                     "executes nothing"),
    }
    if not ready["ready"]:
        out.update(ready=False, code=(ready["codes"] or ["unsupported"])[0],
                   reason="; ".join(ready["blockers"])[:600],
                   remedy="choose a harness that supports the reason intent in "
                          "Settings → Agents; bench does not fall back to "
                          "another harness or widen a grant")
        return out
    full = _person_capacity(board)
    if full:
        out.update(ready=False, code=full["code"], reason=full["reason"],
                   remedy=full["remedy"])
        return out
    if scope is not None:
        try:
            resolve_target(scope, board)
        except Refused as exc:
            out.update(ready=False, code=exc.code, reason=str(exc), remedy=exc.remedy)
    return out


# ── reading conversations ─────────────────────────────────────────────


def _status(conv: dict) -> str | None:
    if not conv["exchanges"]:
        return None
    last = conv["exchanges"][-1]
    if last["replies"]:
        return last["replies"][-1].get("status")
    return "asked"


def _summary(conv: dict, where: dict) -> dict:
    return {"id": conv["id"], "scope": conv.get("scope") or where.get("kind"),
            "subject": conv.get("subject"), "title": conv.get("title"),
            "count": conv.get("count"), "started": conv.get("started"),
            "updated": conv.get("updated"), "status": _status(conv),
            "where": where}


def conversations(card: str | None = None, board: dict | None = None) -> list[dict]:
    """Every conversation this board can read, newest first — on cards and
    in the board's own files. `card` narrows to one card's. A read."""
    board = taskfiles.collect() if board is None else board
    out = []
    if card is None:
        for conv in decisions.board_conversations():
            out.append(_summary(conv, {"kind": "board"}))
    for task in _tasks(board):
        body = task.get("body") or ""
        if '"kind": "ask"' not in body:
            continue
        if card is not None and _number(task) != taskfiles.canonical_number(str(card)) \
                and task["file"][:-3] != card:
            continue
        where = {"kind": "phase" if task.get("isPhase") else "card",
                 "file": task["file"], "stage": task["stage"],
                 "card": _number(task), "title": task.get("title")}
        for conv in decisions.read_text(body, task["file"])["conversations"]:
            out.append(_summary(conv, where))
    out.sort(key=lambda c: str(c.get("updated") or ""), reverse=True)
    return out


def _locate(conversation: str, board: dict) -> dict | None:
    """Where a conversation lives: its board file, or the card holding it."""
    try:
        if decisions.conversation_path(conversation).is_file():
            return {"kind": "board", "conversation": conversation, "subject": "board"}
    except ValueError:
        return None
    for task in _tasks(board):
        body = task.get("body") or ""
        if conversation in body and '"kind": "ask"' in body:
            return {"kind": "phase" if task.get("isPhase") else "card",
                    "file": task["file"], "stage": task["stage"],
                    "subject": _number(task) or task["file"][:-3],
                    "title": task.get("title"), "conversation": conversation}
    return None


def thread(conversation: str, board: dict | None = None) -> dict:
    """One conversation, each reply beside the live state of the run that
    gave it (fresh, stale, still running) — and the run still answering a
    question that has no reply yet. A read."""
    board = taskfiles.collect() if board is None else board
    where = _locate(conversation, board)
    if where is None:
        raise Refused(f"no conversation {conversation} on this board",
                      "conversation-unknown")
    conv = decisions.conversation(where, conversation)
    if conv is None:
        raise Refused(f"no conversation {conversation} on this board",
                      "conversation-unknown")
    for exchange in conv["exchanges"]:
        jobs = reasoning.for_ask(conversation, exchange["ask"]["id"])
        exchange["jobs"] = [{k: j.get(k) for k in
                             ("id", "status", "created", "started", "ended",
                              "name", "harness", "model", "usage", "error",
                              "fresh", "attempt", "retryOf")} for j in jobs]
        live = jobs[-1] if jobs else None
        exchange["active"] = live if live and live.get("status") in reasoning.ACTIVE \
            else None
        for reply in exchange["replies"]:
            job = next((j for j in jobs if j["id"] == reply.get("operation")), None)
            reply["fresh"] = (job or {}).get("fresh") if reply.get("status") == "valid" \
                else None
            reply["jobKnown"] = job is not None
        exchange["canRetry"] = bool(not exchange["active"] and (
            not exchange["replies"] or exchange["replies"][-1].get("status")
            in ("failed", "cancelled", "refused", "invalid", "stale")))
    where = {k: v for k, v in where.items() if k != "conversation"}
    return {"conversation": conv, "where": where,
            "persistence": {"commits": bool(config.COMMIT_MOVES),
                            "syncs": bool(getattr(config, "SYNC", False))}}


def facts_for(operation: str) -> dict:
    """The facts document one question's run was given — bench's public
    readings, scrubbed when written, holding no path — so a person can
    open the evidence an answer cites. A read."""
    job = reasoning.for_operation(operation)
    if job is None or job.get("need") != "ask":
        raise Refused("no question's run by that id on this board",
                      "operation-unknown")
    found = reasoning.facts_document(operation)
    if found is None:
        raise Refused("the facts this run was given are no longer on this "
                      "board", "facts-unavailable")
    return found


# ── asking ────────────────────────────────────────────────────────────


def _history(conv: dict | None) -> list[dict]:
    out = []
    for exchange in (conv or {}).get("exchanges") or []:
        reply = exchange["replies"][-1] if exchange["replies"] else {}
        out.append({"asked": exchange["ask"].get("at"),
                    "question": exchange["ask"].get("text"),
                    "reply": {"status": reply.get("status"),
                              "summary": reply.get("summary"),
                              "answer": reply.get("answer")} if reply else None,
                    "note": "a prior public exchange; its facts may have "
                            "changed since — use this run's readings"})
    return out


def _launch(target: dict, conversation: str, ask_record: str, question: str,
            board: dict, retry: bool = False) -> dict:
    """The run itself, or the reason there is none — recorded as a reply
    so the conversation never shows a question that silently went nowhere."""
    conv = decisions.conversation(target, conversation)
    prior = dict(conv or {})
    prior["exchanges"] = [x for x in (conv or {}).get("exchanges") or []
                          if x["ask"]["id"] != ask_record]
    readings = snapshot(target, board)
    payload = {"target": {k: target[k] for k in ("kind", "file", "subject")
                          if k in target},
               "conversation": conversation, "record": ask_record,
               "question": question, "history": _history(prior),
               "snapshot": readings["taken"], "facts": readings,
               "cards": readings.get("cardNumbers") or []}
    try:
        found = reasoning.request(target.get("file"), "ask", via="person",
                                  retry=retry, board=board, ask=payload)
    except (ValueError, OSError) as exc:
        reason = str(exc)[:600]
        try:
            decisions.record_reply(
                target, conversation=conversation, ask=ask_record,
                status="refused", source="observed",
                fields={"summary": f"no run was launched: {reason}",
                        "diagnostics": ["not-launched"],
                        "snapshot": readings["taken"]},
                key=f"refused-{ask_record}-{int(time.time())}")
        except (ValueError, OSError):
            pass
        raise Refused(f"the question was recorded, but no run was launched: "
                      f"{reason}", "not-launched",
                      "read the reason; Retry asks again once it is fixed") from exc
    return found


def submit(scope, text, *, request, conversation: str | None = None,
           board: dict | None = None) -> dict:
    """Ask one question: check it can run, record it in its conversation,
    then launch one read-only reasoning run for it. A follow-up names the
    conversation and needs no restating of the subject. The same `request`
    again returns the question and run it already made."""
    board = taskfiles.collect() if board is None else board
    if not isinstance(text, str) or len(text.strip()) < 3:
        raise Refused("write a question", "question-empty")
    if len(text) > MAX_QUESTION:
        raise Refused(f"the question is {len(text)} characters; the limit is "
                      f"{MAX_QUESTION}", "question-too-long")
    if conversation:
        target = _locate(conversation, board)
        if target is None:
            raise Refused(f"no conversation {conversation} on this board",
                          "conversation-unknown")
        target = dict(target)
        target.pop("conversation", None)
    else:
        target = resolve_target(scope, board)
    pre = preflight(None, board)
    if not pre["ready"]:
        raise Refused(pre["reason"] or "a question cannot run here",
                      pre["code"] or "unavailable", pre["remedy"])
    busy = reasoning.active_for(target.get("file"),
                                conversation if target["kind"] == "board" else None)
    if busy:
        raise Refused(f"a reasoning run ({busy['need']}, {busy['id']}) is already "
                      f"in flight for this {target['kind']}; one at a time",
                      "run-in-flight", "wait for it to finish, or cancel it")
    subject = target.get("subject") or "board"
    if not conversation:
        conversation = decisions.conversation_id(target["kind"], subject, request)
    if target["kind"] == "board":
        target["conversation"] = conversation
    written = decisions.record_ask(target, conversation=conversation,
                                   text=text, request=request)
    ask_record = written["id"]
    if written.get("duplicate"):
        jobs = reasoning.for_ask(conversation, ask_record)
        if jobs:
            return {"conversation": conversation, "ask": ask_record,
                    "job": jobs[-1], "duplicate": True, "where": _public_where(target)}
    found = _launch(target, conversation, ask_record, text.strip(), board)
    return {"conversation": conversation, "ask": ask_record, "job": found["job"],
            "duplicate": found["duplicate"], "where": _public_where(target)}


def _public_where(target: dict) -> dict:
    return {k: target[k] for k in ("kind", "file", "stage", "subject") if k in target}


def retry(conversation: str, ask_record: str, board: dict | None = None) -> dict:
    """Ask one question again, deliberately: only when its newest run
    failed, was cancelled, refused or went stale — never over a run still
    in flight or an answer that stands."""
    board = taskfiles.collect() if board is None else board
    target = _locate(conversation, board)
    if target is None:
        raise Refused(f"no conversation {conversation} on this board",
                      "conversation-unknown")
    found = thread(conversation, board)
    exchange = next((x for x in found["conversation"]["exchanges"]
                     if x["ask"]["id"] == ask_record), None)
    if exchange is None:
        raise Refused("no such question in this conversation", "ask-unknown")
    if not exchange["canRetry"]:
        raise Refused("this question has a run in flight or an answer that "
                      "stands — ask a follow-up instead", "retry-not-needed")
    pre = preflight(None, board)
    if not pre["ready"]:
        raise Refused(pre["reason"] or "a question cannot run here",
                      pre["code"] or "unavailable", pre["remedy"])
    target = dict(target)
    if target["kind"] != "board":
        target.pop("conversation", None)
    result = _launch(target, conversation, ask_record,
                     exchange["ask"].get("text") or "", board, retry=True)
    return {"conversation": conversation, "ask": ask_record, "job": result["job"],
            "duplicate": result["duplicate"]}


def cancel(operation: str) -> dict:
    """Stop one question's run. Its reply records that it was cancelled;
    nothing else is touched."""
    job = reasoning.for_operation(operation)
    if job is None or job.get("need") != "ask":
        raise Refused("no question's run by that id on this board",
                      "operation-unknown")
    return reasoning.cancel(operation)
