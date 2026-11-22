"""Bounded orchestration passes over a durable operation ledger (task 141).

Each class is one acceptance criterion of the card:

- five ready cards under two slots never exceed the allocated or the
  global capacity, and declared conflicts and dependency cycles block
  while independent eligible work proceeds;
- the first explicitly enabled, eligible commitment launches through
  bench's own start-work path — no shadow stage, no hidden setting;
- forced crash windows before and after the action and its receipt end
  in deduplication, reconciliation or a visible unknown-outcome hold,
  never in a duplicate launch;
- restart, pause and resume and phase allocations conserve the launch
  and retry budgets, and manual work is observed, never cancelled;
- the latest human ownership, agreement changes and a parent's pause are
  re-read before every side effect, and the read endpoints create no
  execution resources.

Every launch here is a stand-in for `agents.start_agent`: no adapter,
model, branch or worktree is involved.

    python3 -m unittest tests.test_orchestration_scheduler -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import agreements  # noqa: E402
import config  # noqa: E402
import decisions  # noqa: E402
import execowner  # noqa: E402
import movements  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import phasescope  # noqa: E402
import plans  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402


def snapshot(*roots: Path) -> dict:
    found = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


class FakeBoard:
    """Cards in stages, as `taskfiles.collect` would read them, with the
    moves the scheduler makes applied to it."""

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.moves: list[tuple] = []

    def add(self, number: str, stage: str = "to-do", *, priority="Medium",
            depends=(), touches=None, phase=None, index=0, is_phase=False,
            brief="Build the thing.") -> str:
        name = f"{number}-card.md"
        self.cards[name] = {
            "file": name, "number": number, "stage": stage,
            "priority": priority, "dependsOn": list(depends),
            "touches": touches, "isPhase": is_phase,
            # adopted into its phase scope (task 143); `phasescope`'s own
            # tests hold adoption, these hold the scheduler
            "phaseMode": "scoped" if is_phase else None,
            "phase": ({"number": phase, "file": f"{phase}-phase.md",
                       "index": index, "mode": "scoped"} if phase else None),
            "body": f"# {number} — card\n\n**Status:** x\n\n{brief}\n"}
        return name

    def collect(self) -> dict:
        stages = {}
        for card in self.cards.values():
            stages.setdefault(card["stage"], []).append(dict(card))
        out = [{"slug": s, "tasks": t} for s, t in stages.items()]
        # the one queue resolver the real board weaves (task 188)
        taskfiles.weave_queue(out)
        return {"stages": out}

    def stage_of(self, name: str):
        card = self.cards.get(name)
        return card["stage"] if card else None

    def move(self, name, source, target, actor="you", **_):
        card = self.cards[name]
        if card["stage"] != source:
            raise ValueError(f"{name} is no longer in {source}/")
        card["stage"] = target
        self.moves.append((name, source, target, actor))
        return dict(card)


class Launcher:
    """A stand-in for `agents.start_agent`: registers a running run the
    way the real one does, and counts every call."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.fail: BaseException | None = None

    def __call__(self, filename: str, stage: str) -> dict:
        self.calls.append((filename, stage))
        if self.fail is not None:
            raise self.fail
        agent = f"run-{len(self.calls)}"
        state.AGENTS[agent] = {"task": filename, "mode": "work",
                               "status": "running", "started": time.time()}
        return {"id": agent, "name": "Wren"}


def agreement(**bench) -> dict:
    doc = agreements.new_draft()
    doc["bench"].update(enabled=True)
    doc["bench"]["authority"] = {w: True for w in agreements.AUTHORITIES}
    for key, value in bench.items():
        if isinstance(value, dict) and isinstance(doc["bench"].get(key), dict):
            doc["bench"][key].update(value)
        else:
            doc["bench"][key] = value
    return doc


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-sched-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.local, self.work = self.dir / "local", self.dir / "work"
        self.local.mkdir()
        self.work.mkdir()
        (self.work / "tasks").mkdir()
        self.board = FakeBoard()
        self.notes: list[dict] = []
        self.plans: dict[str, dict] = {}
        self.eligible = lambda task: {"eligible": True, "code": "own-move",
                                      "via": "own", "reason": "yours"}
        patches = (
            mock.patch.object(config, "LOCAL", self.local),
            mock.patch.object(config, "STATE", self.local / "state"),
            mock.patch.object(config, "COMMIT_MOVES", False),
            mock.patch.object(config, "work_root", lambda: self.work),
            mock.patch.object(movements, "eligibility",
                              lambda task, tasks=None, snap=None:
                              self.eligible(task)),
            mock.patch.object(movements, "snapshot", lambda: {
                "identity": {"name": "tester", "source": "test"}}),
            mock.patch.object(taskfiles, "collect", self.board.collect),
            mock.patch.object(taskfiles, "find_stage_of", self.board.stage_of),
            mock.patch.object(taskfiles, "move_task", self.board.move),
            mock.patch.object(decisions, "note", self._note),
            mock.patch.object(plans, "recorded", lambda f: self.plans.get(f)),
            mock.patch.object(state, "record_board_event", lambda event: None),
            mock.patch.object(state, "broadcast", lambda frame: None),
            mock.patch.object(scheduler, "BACKOFF_SECONDS", 0),
            # a phase scope's branch work is git's, held in
            # tests/test_phase_scopes.py; no repository here
            mock.patch.object(phasescope, "run", lambda board: []),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(execowner.release_lock)
        saved = dict(state.AGENTS)
        state.AGENTS.clear()
        self.addCleanup(lambda: (state.AGENTS.clear(), state.AGENTS.update(saved)))
        scheduler.LAST["pass"] = None
        self.launch = Launcher()
        # a fresh installation: on the unified phase model from the start
        # (task 188); the upgrade itself is tests/test_phase_queue.py's
        phasescope.mark_fresh()

    def _note(self, filename, stage, kind, fields, *, key, **_):
        self.notes.append({"file": filename, "kind": kind, "key": key, **fields})
        return {"duplicate": False, "id": key}

    def put_in_force(self, doc: dict) -> str:
        # a document whose phases carry an `allocation` is the reserved
        # model that predates task 182, held to the behaviour it keeps
        if any(isinstance(p, dict) and "allocation" in p
               for p in (doc.get("phases") or {}).values()):
            doc["bench"]["capacityModel"] = agreements.RESERVED
            doc["bench"].pop("scopeDefaults", None)
            if agreements.effective().get("capacityModel") == agreements.SHARED:
                # setting up a legacy board, not migrating one back: a
                # shared agreement refuses a reserved draft (task 182)
                agreements.document_path().unlink()
        agreements.save_draft(doc, agreements.revision(agreements.draft_document()[0]))
        return agreements.apply(agreements.effective()["revision"],
                                agreements.revision(doc))["revision"]

    def own_execution(self) -> None:
        execowner.designate("none")
        self.assertTrue(execowner.acquire_lock())

    def enable(self, **bench) -> None:
        self.put_in_force(agreement(**bench))
        self.own_execution()

    def tick(self) -> dict:
        return scheduler.run_pass(start=self.launch)

    def running(self) -> list[str]:
        return sorted(r["task"] for r in state.AGENTS.values()
                      if r.get("status") == "running")

    def end_runs(self, status: str = "done") -> None:
        for record in state.AGENTS.values():
            if record.get("status") == "running":
                record["status"] = status
                record["rc"] = 0 if status == "done" else 1

    def ops(self, status: str | None = None) -> list[dict]:
        return [o for o in operations.all_ops()
                if status is None or o["status"] == status]


class LaunchesAfterAnAdoptedHandover(Sandbox):
    """PR 147 review, item 4: after execution is handed over and adopted,
    new work the scheduler itself moves into in-progress launches; only
    the work already under way at the handover waits for adopt-card."""

    def adopt_after_handover(self) -> dict:
        self.enable()
        released = orchestrator.handover(execowner.reading()["revision"])
        self.assertEqual("released", released["designation"]["role"])
        adopted = execowner.designate(released["designation"]["revision"])
        self.assertTrue(execowner.acquire_lock())
        return adopted["designation"]

    def test_new_work_launches_and_preexisting_work_waits(self):
        self.board.add("1", stage="in-progress")      # under way at the handover
        adopted = self.adopt_after_handover()
        self.assertTrue(adopted["adoptionRequired"])
        self.board.add("2")                           # committed to afterwards
        found = self.tick()
        launched = [r["card"] for r in found["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(["2-card.md"], launched, found["results"])
        self.assertEqual("in-progress", self.board.stage_of("2-card.md"))
        self.assertEqual([("2-card.md", "in-progress")], self.launch.calls)
        held = orchestrator.authorize("implement", "1-card.md")
        self.assertEqual("adoption-required", held["code"])
        execowner.adopt_card(execowner.reading()["revision"], "1")
        self.assertFalse(execowner.reading()["adoptionRequired"])
        self.tick()
        self.assertEqual(["1-card.md", "2-card.md"], self.running())


# ── 1. capacity, conflicts and cycles ─────────────────────────────────

class FiveReadyUnderTwoSlots(Sandbox):
    def test_five_ready_cards_never_exceed_two_slots(self):
        self.enable()
        for number in "12345":
            self.board.add(number)
        found = self.tick()
        self.assertEqual(2, len([r for r in found["results"]
                                 if r["outcome"] == "launched"]))
        self.assertEqual(2, len(self.running()))
        for _ in range(3):           # further passes find no free slot
            self.tick()
        self.assertEqual(2, len(self.running()))
        self.assertEqual(2, len(self.launch.calls))
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(2, used["totals"]["implementation"])
        waiting = [i for i in scheduler.plan()["items"]
                   if i["code"] == "slots-full"]
        self.assertEqual(3, len(waiting))

    def test_requests_ready_together_go_by_card_number_not_priority(self):
        # task 182: first come, first served. Four requests that became
        # ready in one pass share their time, and the tie is broken by card
        # number — never by priority
        self.enable()
        self.board.add("1", priority="Low")
        self.board.add("2")
        self.board.add("3", priority="High")
        self.board.add("4")
        self.tick()
        self.assertEqual(["1-card.md", "2-card.md"], self.running())

    def test_declared_conflicts_and_cycles_block_while_independent_work_runs(self):
        self.enable(capacity={"implementation": 4})
        self.board.add("1", priority="High", touches=["src/api"])
        self.board.add("2", priority="High", touches=["src/api/users.py"])
        self.board.add("3", depends=["4"])
        self.board.add("4", depends=["3"])
        self.board.add("5", touches=["docs"])
        self.board.add("6", depends=["7"])
        self.board.add("7", stage="backlog")
        self.board.add("8")       # declares nothing: unknown overlap, not safe
        found = scheduler.plan()
        codes = {i["card"]: i["code"] for i in found["items"]}
        self.assertEqual("dependency-cycle", codes["3-card.md"])
        self.assertEqual("dependency-cycle", codes["4-card.md"])
        self.assertEqual("waiting-dependency", codes["6-card.md"])
        self.assertEqual("touches-overlap", codes["2-card.md"])
        self.assertEqual(["1-card.md", "5-card.md", "8-card.md"],
                         sorted(found["chosen"]))
        eight = next(i for i in found["items"] if i["card"] == "8-card.md")
        self.assertEqual("unknown", eight["overlap"])
        self.tick()
        self.assertEqual(["1-card.md", "5-card.md", "8-card.md"], self.running())
        # the conflict waits on the running card, then proceeds once it ends
        self.end_runs()
        self.board.cards["1-card.md"]["stage"] = "review"
        self.tick()
        self.assertIn("2-card.md", self.running())

    def test_a_phase_allocation_bounds_its_scope_and_the_total(self):
        doc = agreement(capacity={"implementation": 2})
        doc["phases"] = {"9": {"enabled": True,
                               "allocation": {"implementation": 1,
                                              "dailyStarts": 5}}}
        self.put_in_force(doc)
        self.own_execution()
        self.board.add("9", stage="in-progress", is_phase=True)
        for number, index in (("11", 1), ("12", 2), ("13", 3)):
            self.board.add(number, phase="9", index=index)
        for number in ("21", "22"):
            self.board.add(number)
        self.tick()
        running = self.running()
        self.assertEqual(2, len(running))
        # one from the phase's own slot (its first member in run order),
        # one from the Bench scope's remaining one — never more
        self.assertIn("11-card.md", running)
        self.assertEqual(1, len([r for r in running if r.startswith("2")]))
        # the Bench scope never launched a member, and the phase card is
        # not launched as work
        scopes = {o["file"]: o["scope"] for o in self.ops()}
        self.assertEqual("phase:9", scopes["11-card.md"])
        self.assertNotIn("9-card.md", scopes)


# ── 2. the first enabled commitment launches ─────────────────────────

class FirstEnabledCommitmentLaunches(Sandbox):
    def test_nothing_runs_while_off(self):
        self.board.add("1")
        self.assertEqual({"ran": False, "why": "off"}, self.tick())
        draft = agreement()
        draft["bench"]["enabled"] = False
        self.put_in_force(draft)
        self.own_execution()
        self.assertEqual({"ran": False, "why": "off"}, self.tick())
        self.assertEqual([], self.launch.calls)
        self.assertEqual([], self.ops())

    def test_the_first_explicit_eligible_commitment_launches_through_bench(self):
        self.board.add("1")
        self.enable()
        found = self.tick()
        self.assertTrue(found["ran"])
        self.assertEqual([("1-card.md", "in-progress")], self.launch.calls)
        # moved into in-progress by the board, as an automated move
        self.assertEqual([("1-card.md", "to-do", "in-progress", "orchestrator")],
                         self.board.moves)
        [op] = self.ops()
        self.assertEqual("launched", op["status"])
        self.assertEqual("run-1", op["agent"])
        self.assertEqual("bench", op["scope"])
        # the card keeps the public record: intent before, receipt after
        phases = [(n["phase"], n["outcome"]) for n in self.notes
                  if n["kind"] == "action"]
        self.assertEqual([("intent", "intended"), ("receipt", "launched")], phases)
        # and, before the intent, the work-base decision (task 150): an
        # independent card starts from the normal base
        self.assertEqual("base", self.notes[0]["kind"])
        self.assertEqual("normal", self.notes[0]["decision"])

    def test_a_designated_board_takes_its_own_lock_and_no_one_elses(self):
        self.board.add("1")
        self.put_in_force(agreement())
        self.assertEqual({"ran": False, "why": "not-designated"}, self.tick())
        execowner.designate("none")
        execowner.release_lock()          # a restart: designated, no lock
        found = self.tick()
        self.assertTrue(found["ran"])
        self.assertTrue(execowner.holds_lock())
        self.assertEqual(1, len(self.launch.calls))

    def test_a_card_already_in_progress_launches_without_a_move(self):
        self.board.add("1", stage="in-progress")
        self.enable()
        self.tick()
        self.assertEqual([], self.board.moves)
        self.assertEqual([("1-card.md", "in-progress")], self.launch.calls)

    def test_a_refused_launch_walks_its_own_move_back_and_is_not_retried(self):
        self.board.add("1")
        self.enable()
        self.launch.fail = ValueError("required input 'api' is not readable")
        self.tick()
        self.assertEqual("to-do", self.board.stage_of("1-card.md"))
        [op] = self.ops()
        self.assertEqual("refused", op["status"])
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        item = next(i for i in scheduler.plan()["items"] if i["card"] == "1-card.md")
        self.assertEqual("refused", item["code"])
        # a changed brief is a new question
        self.board.cards["1-card.md"]["body"] += "\nNow with the input.\n"
        self.launch.fail = None
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        # and a person may release the refusal of the same brief instead
        scheduler.resolve(op["key"], "release", "tester")
        self.assertEqual("abandoned", operations.read(op["key"])["status"])


# ── 3. crash windows ─────────────────────────────────────────────────

class CrashWindows(Sandbox):
    def reserve(self, name: str) -> dict:
        task = self.board.cards[name]
        key = operations.key_for("implement", name, scheduler.basis(task), 1)
        return operations.begin(key, action="implement", card=name,
                                number=task["number"], scope="bench",
                                pool="implementation",
                                basis=scheduler.basis(task), attempt=1)

    def test_a_crash_before_the_action_is_reconciled_and_launched_once(self):
        self.board.add("1")
        self.enable()
        op = self.reserve("1-card.md")      # the intent, then the crash
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(1, used["reserved"])   # the slot is held meanwhile
        self.tick()
        self.assertEqual("abandoned", operations.read(op["key"])["status"])
        self.assertEqual(1, len(self.launch.calls))
        statuses = sorted(o["status"] for o in self.ops())
        self.assertEqual(["abandoned", "launched"], statuses)
        for _ in range(3):
            self.tick()
        self.assertEqual(1, len(self.launch.calls))

    def test_a_crash_after_the_action_before_the_receipt_finds_the_run(self):
        self.board.add("1", stage="in-progress")
        self.enable()
        op = self.reserve("1-card.md")
        state.AGENTS["lost-reply"] = {"task": "1-card.md", "mode": "work",
                                      "status": "running", "started": time.time()}
        self.tick()
        found = operations.read(op["key"])
        self.assertEqual("launched", found["status"])
        self.assertEqual("lost-reply", found["agent"])
        self.assertEqual([], self.launch.calls)

    def test_a_restart_after_the_action_holds_an_unknown_outcome(self):
        self.board.add("1", stage="in-progress")
        self.enable()
        op = self.reserve("1-card.md")
        # a plan was recorded after the intent, and no run is tracked: a
        # process may have started with the board that died
        self.plans["1-card.md"] = {"recorded": time.time() + 1}
        for _ in range(3):
            self.tick()
        held = operations.read(op["key"])
        self.assertEqual("unknown", held["status"])
        self.assertEqual([], self.launch.calls)
        item = next(i for i in scheduler.plan()["items"] if i["card"] == "1-card.md")
        self.assertEqual(("held", "outcome-unknown"), (item["state"], item["code"]))
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(1, used["reserved"])
        # a handover is refused while it is unreconciled
        with self.assertRaises(execowner.Refused) as refused:
            orchestrator.handover(execowner.reading()["revision"])
        self.assertEqual("not-quiescent", refused.exception.code)
        # a person settles it; only then may a new attempt follow
        scheduler.resolve(op["key"], "not-launched", "tester")
        self.tick()
        self.assertEqual(1, len(self.launch.calls))

    def test_an_unexplained_launch_error_is_unknown_and_never_retried(self):
        self.board.add("1")
        self.enable()
        self.launch.fail = RuntimeError("the reply was lost")
        self.tick()
        self.launch.fail = None
        for _ in range(3):
            self.tick()
        self.assertEqual(1, len(self.launch.calls))
        [op] = self.ops()
        self.assertEqual("unknown", op["status"])
        self.assertEqual("in-progress", self.board.stage_of("1-card.md"))

    def test_the_same_decision_is_recorded_once(self):
        self.board.add("1")
        self.enable()
        self.reserve("1-card.md")
        with self.assertRaises(operations.Exists):
            self.reserve("1-card.md")
        task = self.board.cards["1-card.md"]
        found = scheduler.execute(task, {}, None, self.launch)
        self.assertEqual("duplicate", found["outcome"])
        self.assertEqual([], self.launch.calls)

    def test_a_receipt_lost_to_a_restart_with_the_process_gone_is_not_relaunched(self):
        self.board.add("1")
        self.enable()
        self.tick()
        [op] = self.ops()
        state.AGENTS.clear()                  # the board restarted
        self.tick()
        ended = operations.read(op["key"])
        self.assertEqual(("ended", "unobserved"), (ended["status"], ended["outcome"]))
        self.tick()
        self.assertEqual(1, len(self.launch.calls))

    def test_a_run_still_alive_after_a_restart_keeps_its_slot(self):
        self.board.add("1")
        self.board.add("2")
        self.enable(capacity={"implementation": 1})
        self.tick()
        [op] = self.ops()
        env = dict(os.environ, BOARD_AGENT_ID=op["agent"])
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                env=env)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        operations.update(op["key"], "launched", pid=proc.pid)
        state.AGENTS.clear()                  # restarted; the run lives on
        self.tick()
        self.assertEqual("unobserved", operations.read(op["key"])["status"])
        self.assertEqual(1, len(self.launch.calls))   # no slot for card 2
        proc.kill()
        proc.wait()
        self.tick()
        self.assertEqual("ended", operations.read(op["key"])["status"])
        self.assertEqual(2, len(self.launch.calls))


# ── 4. budgets ───────────────────────────────────────────────────────

class BudgetsAreConserved(Sandbox):
    def test_daily_starts_survive_a_restart_and_a_pause(self):
        self.enable(capacity={"implementation": 5, "dailyStarts": 2})
        for number in "1234":
            self.board.add(number)
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        self.end_runs()
        for name in list(self.board.cards):
            if self.board.cards[name]["stage"] == "in-progress":
                self.board.cards[name]["stage"] = "review"
        # a restart forgets every registry and memory; the ledger stays
        state.AGENTS.clear()
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        revision = agreements.effective()["revision"]
        revision = agreements.set_scope("bench", "pause", revision)["revision"]
        agreements.set_scope("bench", "resume", revision)
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        codes = {i["code"] for i in scheduler.plan()["items"]
                 if i["stage"] == "to-do"}
        self.assertEqual({"daily-starts-exhausted"}, codes)

    def test_launches_per_card_are_counted_from_the_ledger(self):
        self.enable(capacity={"launchesPerCard": 1})
        self.board.add("1")
        self.tick()
        self.end_runs("failed")
        self.board.cards["1-card.md"]["body"] += "\nRewritten.\n"
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        item = next(i for i in scheduler.plan()["items"])
        self.assertEqual("card-launches-exhausted", item["code"])

    def test_transient_retries_are_bounded_across_restarts(self):
        self.enable(capacity={"transientRetries": 2})
        self.board.add("1")
        self.launch.fail = ValueError("could not launch adapter run: busy")
        for _ in range(6):
            self.tick()
            state.AGENTS.clear()   # a restart: the ledger and the breaker stay
        self.assertEqual(3, len(self.launch.calls))   # one try, two retries
        self.assertEqual(3, len(self.ops("retry")))
        item = next(i for i in scheduler.plan()["items"])
        self.assertEqual("retries-exhausted", item["code"])
        # a transient failure spent no start
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(0, used["startsTodayTotal"])

    def test_the_breaker_opens_after_repeated_transient_failures(self):
        self.enable(capacity={"implementation": 5, "transientRetries": 9})
        for number in "123":
            self.board.add(number)
        self.launch.fail = ValueError("timed out waiting for the adapter")
        self.tick()
        self.assertTrue(scheduler.breaker()["open"])
        calls = len(self.launch.calls)
        found = self.tick()
        self.assertEqual(calls, len(self.launch.calls))
        self.assertEqual("breaker-open", found["results"][0]["outcome"])

    def test_manual_work_counts_and_is_never_cancelled(self):
        self.enable()
        self.board.add("1")
        self.board.add("2")
        self.board.add("3", stage="backlog")
        state.AGENTS["a-person"] = {"task": "3-card.md", "mode": "work",
                                    "status": "running", "started": time.time()}
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        self.assertEqual("running", state.AGENTS["a-person"]["status"])

    def test_a_phase_scope_spends_its_own_starts_and_the_total(self):
        doc = agreement(capacity={"implementation": 4, "dailyStarts": 3})
        doc["phases"] = {"9": {"enabled": True,
                               "allocation": {"implementation": 2,
                                              "dailyStarts": 1}}}
        self.put_in_force(doc)
        self.own_execution()
        self.board.add("9", stage="in-progress", is_phase=True)
        self.board.add("11", phase="9", index=1)
        self.board.add("12", phase="9", index=2)
        self.board.add("21")
        self.board.add("22")
        self.board.add("23")
        self.tick()
        scopes = sorted(o["scope"] for o in self.ops())
        # the phase made its one start; the Bench scope its own two
        self.assertEqual(["bench", "bench", "phase:9"], scopes)


# ── 5. re-checked before every side effect; reads are reads ──────────

class RecheckedBeforeSideEffects(Sandbox):
    def test_a_person_moving_the_card_last_wins_before_the_launch(self):
        self.board.add("1")
        self.enable()
        # someone else's move lands between the scheduler's move and launch
        self.eligible = lambda task: (
            {"eligible": False, "code": "foreign-move", "via": "other",
             "reason": "ada moved it last"} if task["stage"] == "in-progress"
            else {"eligible": True, "code": "own-move", "via": "own",
                  "reason": "yours"})
        found = self.tick()
        self.assertEqual([], self.launch.calls)
        [result] = found["results"]
        self.assertEqual(("refused", "not-eligible"),
                         (result["outcome"], result["code"]))
        self.assertEqual("to-do", self.board.stage_of("1-card.md"))

    def test_an_agreement_change_after_planning_is_honoured(self):
        self.board.add("1")
        self.enable()
        planned = scheduler.plan()
        [(task, item)] = planned["_chosen"]
        agreements.set_scope("bench", "exclude", agreements.effective()["revision"],
                             card="1")
        found = scheduler.execute(task, item, None, self.launch)
        self.assertEqual(("not-authorized", "excluded"),
                         (found["outcome"], found["code"]))
        self.assertEqual([], self.launch.calls)
        self.assertEqual([], self.ops())

    def test_a_parent_pause_starts_no_successor_but_observes_endings(self):
        doc = agreement()
        doc["phases"] = {"9": {"enabled": True,
                               "allocation": {"implementation": 1,
                                              "dailyStarts": 5}}}
        self.put_in_force(doc)
        self.own_execution()
        self.board.add("9", stage="in-progress", is_phase=True)
        self.board.add("11", phase="9", index=1)
        self.board.add("12", phase="9", index=2)
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        agreements.set_scope("bench", "pause", agreements.effective()["revision"])
        self.end_runs()
        self.board.cards["11-card.md"]["stage"] = "review"
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        [op] = self.ops()
        self.assertEqual(("ended", "done"), (op["status"], op["outcome"]))
        item = next(i for i in scheduler.plan()["items"]
                    if i["card"] == "12-card.md")
        self.assertEqual("paused", item["code"])

    def test_the_read_endpoints_create_no_execution_resources(self):
        import routes
        self.put_in_force(agreement())
        execowner.designate("none")
        execowner.release_lock()
        self.board.add("1")
        before = snapshot(self.local, self.work)
        replies = []

        class Req:
            def reply(self, code, payload):
                replies.append((code, payload))

        for _ in range(3):
            routes.get_orchestration(Req())
            scheduler.projection()
        self.assertEqual(snapshot(self.local, self.work), before)
        self.assertFalse(execowner.holds_lock())
        self.assertEqual([], self.launch.calls)
        self.assertEqual([], self.ops())
        self.assertEqual(200, replies[0][0])
        schedule = replies[0][1]["schedule"]
        self.assertFalse(schedule["executes"])
        self.assertNotIn("_chosen", schedule)


class TouchesHeader(unittest.TestCase):
    def test_the_header_is_read_and_normalised(self):
        text = ("# 1 — x\n\n**Status:** To Do\n"
                "**Touches:** `src/api/`, ./docs, src/api, tests\n")
        self.assertEqual(["src/api", "docs", "tests"], taskfiles.touches(text))
        self.assertIsNone(taskfiles.touches("# 1 — x\n\nNo header.\n"))

    def test_overlap_is_by_path_segment_and_unknown_without_a_declaration(self):
        self.assertTrue(scheduler.overlap(["src/api"], ["src/api/users.py"]))
        self.assertFalse(scheduler.overlap(["src/api"], ["src/apis"]))
        self.assertIsNone(scheduler.overlap(None, ["docs"]))


if __name__ == "__main__":
    unittest.main()
