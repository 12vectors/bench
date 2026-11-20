#!/usr/bin/env python3
"""One step of an orchestration journey, in a fresh process.

    python3 journey_driver.py <core> <step-json>

The harness (`tests/product/journey.py`) runs every step through this
file in a process of its own, with the layout's environment, so the
copied core resolves its roots exactly as an installed board would — and
so every step is also a restart: nothing a previous step held in memory
(the agent registry, reasoning jobs, the execution lock) survives into
the next one. Durable records are all that carry over, which is what the
scheduler's reconciliation claims to need.

A step prints one JSON line on stdout, its result, and nothing else.
Nothing here is a board: no HTTP server runs, no beat ticks. A pass is
`scheduler.run_pass()` called once, and a step waits for every thread it
started (agent reapers, check batches) before it reports.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

CORE = Path(sys.argv[1]).resolve()
STEP = json.loads(sys.argv[2])
sys.path.insert(0, str(CORE))

import agreements  # noqa: E402
import changes  # noqa: E402
import config  # noqa: E402
import coordinate  # noqa: E402
import decisions  # noqa: E402
import execowner  # noqa: E402
import gates  # noqa: E402
import movements  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import phasescope  # noqa: E402
import reasoning  # noqa: E402
import recovery  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402

# as board.py does at startup: with BOARD_SYNC on, every commit the board
# makes publishes itself (a no-op with it off)
sync.install()

# a step must not wait for ever on a thread a bug left running
SETTLE_SECONDS = 120


def settle() -> None:
    """Wait for every thread this step started: reapers, check batches,
    PR openings. A step that returns while one is still running would
    report a world that is about to change."""
    deadline = time.time() + SETTLE_SECONDS
    while time.time() < deadline:
        others = [t for t in threading.enumerate()
                  if t is not threading.main_thread() and t.is_alive()]
        if not others:
            return
        time.sleep(0.05)
    raise SystemExit("journey step: a thread never finished: "
                     + ", ".join(t.name for t in threading.enumerate()))


def stages() -> dict:
    found = {}
    for stage in taskfiles.collect()["stages"]:
        for task in stage["tasks"]:
            found[task["file"]] = stage["slug"]
    return found


def card(filename: str) -> dict:
    stage = taskfiles.find_stage_of(filename)
    task = taskfiles.read_task(config.TASKS / stage / filename, stage)
    return {"stage": stage, "pr": task.get("pr"), "body": task["body"]}


def open_prs() -> list[str]:
    """What the board's own watcher does as the actor when a card it moved
    enters review: open its PR. No beat runs here, so the step does it."""
    opened = []
    for name, stage in stages().items():
        if stage != "review":
            continue
        task = taskfiles.read_task(config.TASKS / stage / name, stage)
        if task.get("pr") or task.get("isPhase"):
            continue
        branch = f"task/{name[:-3]}"
        if config.git_run(config.REPO, "rev-parse", "--verify", "--quiet",
                          branch).returncode != 0:
            continue
        opened.append(changes.open_pr_now(name))
    return opened


CRASHED = 70


def crash(where: str | None) -> None:
    """Kill this process at one of the windows a real board can die in:

    - `launch-before`: after the launch's intent is durable, before the
      launch path is entered;
    - `launch-after`: after the launch path returned a running run, before
      its receipt is written (the run's own process lives on, orphaned,
      and nobody reaps it);
    - `merge-after`: after the merge and its publication, before the
      merge's receipt is written.
    """
    if where is None:
        return
    import agents
    import completion
    if where == "launch-before":
        agents.start_agent = lambda *a, **k: os._exit(CRASHED)
    elif where == "launch-after":
        real = agents.start_agent

        def after(*args, **kwargs):
            real(*args, **kwargs)
            os._exit(CRASHED)
        agents.start_agent = after
    elif where == "merge-after":
        real_merge = completion.complete_task

        def merged(*args, **kwargs):
            real_merge(*args, **kwargs)
            os._exit(CRASHED)
        completion.complete_task = merged
    else:
        raise SystemExit(f"journey step: no crash window {where!r}")


RACE_SECONDS = 60
RACE: dict = {}


def race(spec: dict | None) -> None:
    """Force one interleaving a live board can hit by timing alone (task
    155): the run launched on `hold` finishes — its reaper files the
    report and moves the card into review/ — exactly while `during` is
    being launched, between `taskfiles.collect()` listing the in-progress
    directory and reading `hold`'s card from it.

    `at` says which read: `authorize`, the first one inside the
    scheduler's `execute` for `during` (the authorization before its
    intent), or `launch`, the first one inside `agents.start_agent` for
    it (the launch's own preflight). The reaper is held until then; the
    read waits for it to finish. Both waits are bounded, and what
    happened is recorded in `RACE` for the step's result — a barrier that
    timed out says so, and is never read as the product's own failure.
    The held reaper is always let go when `during`'s launch ends, so the
    step's `settle()` sees every thread finish."""
    if not spec:
        return
    import agents
    hold, during, at = spec["hold"], spec["during"], spec["at"]
    if at not in ("authorize", "launch"):
        raise SystemExit(f"journey step: no race point {at!r}")
    go, done = threading.Event(), threading.Event()
    RACE.update(hold=hold, during=during, at=at, released=False,
                reaped=False, timeout=None, cardThere=None, reaperError=None)
    # `authorize`: anywhere in the scheduler's execute of `during`, whose
    # first board reading is the authorization before its intent
    inside = {"authorize": False, "launch": False}
    real_reap, real_read = agents._reap_agent, taskfiles.read_task
    real_execute, real_start = scheduler.execute, agents.start_agent

    def reap(agent_id, proc, log_file):
        if (state.AGENTS.get(agent_id) or {}).get("task") != hold:
            return real_reap(agent_id, proc, log_file)
        if not go.wait(RACE_SECONDS):
            RACE["timeout"] = "the held reaper was never released"
        try:
            real_reap(agent_id, proc, log_file)
            RACE["reaped"] = True
        except BaseException as exc:  # noqa: BLE001 — recorded, then re-raised
            RACE["reaperError"] = type(exc).__name__
            raise
        finally:
            done.set()

    def read_task(path, slug, *args, **kwargs):
        if (inside[at] and not RACE["released"] and slug == "in-progress"
                and Path(path).name == hold):
            RACE["released"] = True
            go.set()
            if not done.wait(RACE_SECONDS):
                RACE["timeout"] = "the released reaper never finished"
            RACE["cardThere"] = Path(path).is_file()
        return real_read(path, slug, *args, **kwargs)

    def execute(task, item, board=None, start=None):
        mine = task["file"] == during
        inside["authorize"] = inside["authorize"] or mine
        try:
            return real_execute(task, item, board, start)
        finally:
            if mine:
                inside["authorize"] = False
                go.set()

    def start_agent(filename, *args, **kwargs):
        mine = filename == during
        inside["launch"] = inside["launch"] or mine
        try:
            return real_start(filename, *args, **kwargs)
        finally:
            if mine:
                inside["launch"] = False

    agents._reap_agent = reap
    taskfiles.read_task = read_task
    scheduler.execute = execute
    agents.start_agent = start_agent


JAM: dict = {}


def jam(spec: dict | None) -> None:
    """Make one board commit fail the way a concurrent git makes it fail
    (task 155): the work-state index is held — `index.lock` exists, as
    git itself creates it — for the whole of the commit of `file`'s move
    into `to`, and let go when that commit returns. No sleep, no thread
    and no timing: the commit meets a held index on every attempt, so it
    fails exactly as the observed one did, and the card has moved on disk
    with the move unrecorded. What happened is in `JAM`."""
    if not spec:
        return
    target, stage = spec["file"], spec["to"]
    JAM.update(file=target, to=stage, jammed=False, committed=None, lockHeld=None)
    real = taskfiles.commit_paths

    def commit_paths(filename, message, spec_, failure):
        if filename != target or f"→ {stage} " not in message or JAM["jammed"]:
            return real(filename, message, spec_, failure)
        root = config.state_repo()
        found = config.git_run(root, "rev-parse", "--git-path", "index.lock")
        lock = Path(found.stdout.strip())
        if not lock.is_absolute():
            lock = root / lock
        try:   # exclusive, as git itself creates it
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            import traceback
            JAM["contended"] = [
                "".join(traceback.format_stack(frame)[-6:])
                for ident, frame in sys._current_frames().items()
                if ident != threading.get_ident()]
            raise
        JAM["jammed"] = True
        try:
            JAM["committed"] = real(filename, message, spec_, failure)
        finally:
            JAM["lockHeld"] = lock.is_file()
            lock.unlink()
        return JAM["committed"]

    taskfiles.commit_paths = commit_paths


def put_in_force(doc: dict) -> dict:
    current, _ = agreements.draft_document()
    agreements.save_draft(doc, agreements.revision(current))
    return agreements.apply(agreements.effective()["revision"],
                            agreements.revision(doc), actor="ada")


def agreement(spec: dict) -> dict:
    doc = agreements.new_draft()
    doc["bench"]["enabled"] = spec.get("enabled", True)
    words = spec.get("authority", list(agreements.AUTHORITIES))
    doc["bench"]["authority"] = {w: w in words for w in agreements.AUTHORITIES}
    for key in ("capacity", "ceilings"):
        if key in spec:
            doc["bench"].setdefault(key, {}).update(spec[key])
    if "phases" in spec:
        doc["phases"] = spec["phases"]
    return doc


def public_ops() -> list[dict]:
    keep = ("key", "action", "file", "status", "scope", "code", "attempt",
            "agent", "head", "outcome")
    # the reason too, bounded, so a journey that fails on an operation's
    # outcome can say why the operation ended as it did (task 155)
    return [{k: o.get(k) for k in keep if k in o}
            | ({"reason": str(o["reason"])[:300]} if o.get("reason") else {})
            for o in operations.all_ops()]


def calls() -> list[dict]:
    path = Path(STEP["calls"])
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run() -> dict:
    kind = STEP["step"]
    if kind in ("read", "adopt", "resolve"):
        # a designated installation's board process holds its own lock, as
        # a pass takes it; taking it changes no record and ends with the step
        owner = execowner.reading()
        if owner["role"] == "designated-here" and not owner["lockHeld"]:
            execowner.acquire_lock()
    if kind == "describe":
        return {"repo": str(config.REPO), "work": str(config.work_root()),
                "tasks": str(config.TASKS), "external": config.EXTERNAL,
                "shares": config.app_shares_state_repository(),
                "identity": movements.identity()}
    if kind == "enable":
        applied = put_in_force(agreement(STEP.get("agreement", {})))
        if STEP.get("designate", True):
            execowner.designate(execowner.designation_revision(
                execowner.read_designation()[0]), actor="ada")
        return {"revision": applied["revision"], "owner": execowner.reading()["role"]}
    if kind == "scope":
        current = agreements.effective()["revision"]
        return agreements.set_scope(STEP["scope"], STEP["action"], current,
                                    card=STEP.get("card"), actor="ada")
    if kind == "move":
        taskfiles.move_task(STEP["file"], STEP["from"], STEP["to"],
                            actor=STEP.get("actor", "you"))
        return {"stage": taskfiles.find_stage_of(STEP["file"])}
    if kind == "adopt":
        return phasescope.adopt(STEP["file"], actor="ada")
    if kind == "pass":
        crash(STEP.get("crash"))
        race(STEP.get("race"))
        jam(STEP.get("jam"))
        if "backoff" in STEP:
            # the one clock a journey shortens: a retry's backoff (60s and
            # doubling on a board). Whether a retry waits is still decided
            # by the scheduler; only how long is the test's.
            scheduler.BACKOFF_SECONDS = recovery.BACKOFF_SECONDS = STEP["backoff"]
        result = scheduler.run_pass()
        settle()
        if STEP.get("observe", True):
            # a live board's next beat sees the endings of the runs it
            # launched while it still holds them; this process is about to
            # exit, so it looks now (observing launches nothing)
            scheduler.observe()
            coordinate.observe()
        opened = open_prs() if STEP.get("open_prs", True) else []
        settle()
        return {"pass": result, "opened": opened, "stages": stages(),
                "ops": public_ops(), **({"race": dict(RACE)} if RACE else {}),
                **({"jam": dict(JAM)} if JAM else {})}
    if kind == "reason":
        found = reasoning.request(STEP["file"], STEP["need"], via=STEP.get("via", "orchestrator"))
        settle()
        job = reasoning.JOBS.get(found["job"]["id"]) or found["job"]
        return {"duplicate": found["duplicate"], "job": reasoning.public(job)}
    if kind == "answer":
        stage = taskfiles.find_stage_of(STEP["file"])
        reading = decisions.for_card(STEP["file"], stage)
        # the open question, or — to replay an answer — the first one
        question = next((q for q in reading["questions"] if q["state"] == "open"),
                        reading["questions"][0])
        text = (config.TASKS / stage / STEP["file"]).read_text(encoding="utf-8")
        brief = taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])
        return decisions.answer(STEP["file"], stage, question=question["id"],
                                basis=STEP.get("basis", question["basis"]),
                                brief_sha=STEP.get("brief", brief),
                                actor_seen=STEP.get("actor_seen", "ada"),
                                choice=STEP.get("choice", 0),
                                request=STEP.get("request", "journey-answer"))
    if kind == "read":
        found = {"stages": stages(), "ops": public_ops(), "calls": calls(),
                 "eligibility": {}, "decisions": {}, "delivery": {}}
        tasks = [t for s in taskfiles.collect()["stages"] for t in s["tasks"]]
        for task in tasks:
            name = task["file"]
            found["eligibility"][name] = movements.eligibility(task, tasks)["code"]
            reading = decisions.for_card(name, found["stages"][name])
            found["decisions"][name] = {
                "questions": [{"text": q["text"], "state": q["state"]}
                              for q in reading["questions"]],
                "responses": len(reading["responses"]),
                "actions": [a.get("action") for a in reading.get("actions", [])]}
        for item in coordinate.plan()["items"]:
            found["delivery"][item["card"]] = {k: item.get(k) for k in
                                               ("state", "code", "head", "reason",
                                                "authority", "checks", "review",
                                                "label")}
        found["schedule"] = [{k: i.get(k) for k in ("card", "state", "code")}
                             for i in scheduler.plan()["items"]]
        found["phases"] = phasescope.projection()
        found["breaker"] = recovery.breaker()["open"]
        found["incidents"] = [recovery.public(i) for i in recovery.all_incidents()]
        found["reading"] = {"state": orchestrator.reading().get("state"),
                            "role": execowner.reading()["role"]}
        return found
    if kind == "card":
        return card(STEP["file"])
    if kind == "evidence":
        # bench's own records, each with the commit it is bound to — the
        # thing a review reads, rather than any run's account of itself
        import evidence
        checks = [{"check": r.get("check"), "outcome": r.get("outcome"),
                   "commit": (r.get("source") or {}).get("commit"),
                   "requester": (r.get("requester") or {}).get("intent")}
                  for r in evidence.for_task(STEP["file"])]
        verdicts = [{"verdict": v.get("verdict"), "head": v.get("head"),
                     "agent": v.get("agent")} for v in gates.verdicts_for(STEP["file"])]
        return {"checks": checks, "verdicts": verdicts}
    if kind == "resolve":
        return scheduler.resolve(STEP["key"], STEP["decision"], actor="ada")
    if kind == "reset-breaker":
        return recovery.breaker_reset(actor="ada")
    if kind == "handover":
        return orchestrator.handover(execowner.designation_revision(
            execowner.read_designation()[0]), actor="ada")
    if kind == "gate":
        scope = gates.scope_for(taskfiles.read_task(
            config.TASKS / "review" / STEP["file"], "review"))
        return {"decision": gates.evaluate(STEP["file"], scope)["decision"]}
    raise SystemExit(f"journey step: unknown step {kind!r}")


REFUSALS = (decisions.Refused, execowner.Refused, agreements.Refused,
             phasescope.Refused, ValueError, LookupError)


if __name__ == "__main__":
    try:
        out = run()
    except REFUSALS as exc:
        # a refusal is an answer, reported as one: the step changed nothing
        # the code it names did not say it changed
        out = {"refused": True, "kind": type(exc).__name__,
               "code": getattr(exc, "code", None), "message": str(exc)[:400]}
    settle()
    # an unended run in a process about to exit is a run nobody will reap
    assert not any(r.get("status") == "running" for r in state.AGENTS.values())
    print(json.dumps(out, default=str))
