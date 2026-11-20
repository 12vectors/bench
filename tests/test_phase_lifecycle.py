"""A phase's execution owner, lifecycle and recovery (task 160).

The scheduler's own in-memory sandbox (no repository) drives a scoped
phase through the states phase #156's dogfood run went through, and one
that run hit by accident: a later member moved into In Progress with no
work, which used to leave the first member held as `phase-member-in-flight`
while the later one waited on it — a silent mutual wait. Every reading
here is the one `/api/state` (`phaseLifecycle`) and `GET
/api/orchestration` (`schedule.phases`) carry, so the Board's face, the
Phases lane and the Orchestrator say one thing.

    python3 -m unittest tests.test_phase_lifecycle -v
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agreements  # noqa: E402
import operations  # noqa: E402
import phases  # noqa: E402
import phasescope  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402

from tests.test_orchestration_scheduler import Sandbox, agreement  # noqa: E402


class PhaseSandbox(Sandbox):
    """Phase 9 adopted, sequential, members 11 and 12 (12 depends on 11).
    Branches are whatever `self.branches` holds — never git."""

    def setUp(self):
        super().setUp()
        self.branches = {"phase/9-card"}
        patches = (
            mock.patch.object(phases, "branch_exists",
                              lambda branch: branch in self.branches),
            # create=True: the regression is also run against the module
            # from before the repair, which had no such function
            mock.patch.object(phasescope, "has_work",
                              lambda task: f"task/{task['file'][:-3]}"
                              in self.branches, create=True),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def doc(self, **phase) -> dict:
        doc = agreement(capacity={"implementation": 3})
        doc["phases"] = {"9": {"enabled": True,
                               "allocation": {"implementation": 1,
                                              "dailyStarts": 10}, **phase}}
        return doc

    def phase(self, stages=("to-do", "to-do"), phase_stage="in-progress",
              mode="scoped") -> None:
        self.board.add("9", stage=phase_stage, is_phase=True)
        self.board.cards["9-card.md"]["phaseMode"] = mode
        self.board.cards["9-card.md"]["title"] = "9 — Ship it"
        for index, (number, stage) in enumerate(zip(("11", "12"), stages), 1):
            self.board.add(number, stage=stage, phase="9", index=index,
                           depends=("11",) if number == "12" else ())
            self.board.cards[f"{number}-card.md"]["phase"]["mode"] = mode
            self.board.cards[f"{number}-card.md"]["phase"]["file"] = "9-card.md"
            self.board.cards[f"{number}-card.md"]["title"] = f"{number} — member"

    def item(self, name: str) -> dict:
        return next(i for i in scheduler.plan()["items"] if i["card"] == name)

    def life(self) -> dict:
        return phasescope.board_lifecycle(self.board.collect())["9-card.md"]

    def row(self, name: str) -> dict:
        return next(r for r in self.life()["members"] if r["card"] == name)

    def projected(self) -> dict:
        found = scheduler.projection()["phases"]["phases"]
        return next(p for p in found if p["card"] == "9-card.md")


class AnAccidentalMoveIsNotAMutualWait(PhaseSandbox):
    """#157/#158: the later member moved into In Progress with no work."""

    def test_the_first_member_launches_and_the_later_one_waits_its_turn(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("to-do", "in-progress"))
        # before the repair: 11 read `phase-member-in-flight` (12 is in
        # in-progress/) while 12 read `waiting-integration` (on 11) —
        # neither could ever move
        self.assertEqual("chosen", self.item("11-card.md")["code"])
        self.assertEqual("waiting-integration", self.item("12-card.md")["code"])
        self.tick()
        self.assertEqual(["11-card.md"], self.running(),
                         "the first member launches; the later one never "
                         "launches before its dependency integrates")
        self.assertEqual([("11-card.md", "in-progress")], self.launch.calls)

    def test_the_mistake_is_named_with_its_safe_recovery(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("to-do", "in-progress"))
        row = self.row("12-card.md")
        self.assertEqual("moved-early", row["state"])
        self.assertIn("move it back to To Do", row["why"])
        life = self.life()
        self.assertEqual("moved-early", life["word"])
        self.assertEqual("you", life["next"]["who"])
        # the guarded way back is open: nothing works the card
        self.assertIsNone(phasescope.return_refusal("12-card.md", "in-progress",
                                                    "to-do"))
        self.board.move("12-card.md", "in-progress", "to-do")
        self.tick()
        self.assertEqual(["11-card.md"], self.running())
        self.assertEqual("working", self.life()["word"])
        self.assertNotIn("moved-early", [r["state"] for r in self.life()["members"]])

    def test_real_work_on_the_later_member_still_holds_the_first(self):
        """In flight means worked: a branch, a run or an open operation —
        never a stage. With work on it, sequential still waits."""
        # two slots, so the open operation's reservation does not answer
        # for the strategy first
        self.put_in_force(self.doc(allocation={"implementation": 2,
                                               "dailyStarts": 10}))
        self.own_execution()
        self.phase(stages=("to-do", "in-progress"))
        self.branches.add("task/12-card")
        self.assertEqual("phase-member-in-flight", self.item("11-card.md")["code"])
        self.branches.discard("task/12-card")
        op = operations.begin(operations.key_for("implement", "12-card.md", "b"),
                              action="implement", card="12-card.md", number="12",
                              scope="phase:9", pool="implementation", basis="b",
                              attempt=1)
        self.assertEqual("phase-member-in-flight", self.item("11-card.md")["code"])
        # and the move back is refused while that operation has no receipt
        refusal = phasescope.return_refusal("12-card.md", "in-progress", "backlog")
        self.assertIn(op["key"], refusal)
        operations.update(op["key"], "unknown")
        self.assertIn("unknown outcome",
                      phasescope.return_refusal("12-card.md", "in-progress",
                                                "to-do"))
        self.assertEqual("unknown", self.life()["word"])

    def test_a_running_member_cannot_be_moved_back(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("in-progress", "to-do"))
        state.AGENTS["r11"] = {"task": "11-card.md", "mode": "work",
                               "status": "running", "started": time.time()}
        self.assertIn("‖ hold", phasescope.return_refusal(
            "11-card.md", "in-progress", "to-do"))
        # forward moves and other phases' cards are not this guard's
        self.assertIsNone(phasescope.return_refusal("11-card.md", "in-progress",
                                                    "review"))


class OneOwnerOneLifecycle(PhaseSandbox):
    def test_the_observed_scoped_snapshot_reads_working_not_unstarted(self):
        """#156: adopted, branch cut, the runner not running, #157 running."""
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("in-progress", "backlog"))
        state.AGENTS["r11"] = {"task": "11-card.md", "mode": "work",
                               "status": "running", "started": time.time()}
        life = self.life()
        self.assertEqual(("phase scope", "working"), (life["owner"], life["word"]))
        self.assertIn("on #11", life["line"])
        self.assertEqual("11-card.md", life["on"])
        self.assertEqual(life["word"], self.projected()["lifecycle"]["word"],
                         "API state and the Orchestrator agree")

    def test_legacy_and_named_but_not_adopted(self):
        self.put_in_force(agreement())
        self.phase(mode="legacy")
        life = self.life()
        self.assertEqual(("phase runner", "not-configured"),
                         (life["owner"], life["word"]))
        self.put_in_force(self.doc())
        life = self.life()
        self.assertEqual(("phase runner", "not-adopted"), (life["owner"], life["word"]))
        self.assertTrue(life["adoptable"])
        self.assertIn("adopt it in Orchestrator", life["next"]["text"])

    def test_prepared_with_every_member_in_backlog(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("backlog", "backlog"))
        life = self.life()
        self.assertEqual("queue-needed", life["word"])
        self.assertIn("#11 is next and is in Backlog", life["line"])
        self.assertEqual("move #11 into To Do to queue it", life["next"]["text"])
        self.assertEqual(["in-backlog", "in-backlog"],
                         [r["state"] for r in self.projected()["members"]],
                         "a Backlog member is never called ready")
        self.tick()
        self.assertEqual([], self.launch.calls, "nothing pulled out of Backlog")

    def test_before_the_branch_is_cut_it_is_preparing(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.branches.clear()
        self.phase(phase_stage="to-do")
        self.assertEqual("preparing", self.life()["word"])

    def test_dependency_wait_and_ready(self):
        self.put_in_force(self.doc(strategy="dependencies"))
        self.own_execution()
        self.phase()
        rows = {r["card"]: r for r in self.projected()["members"]}
        self.assertEqual("ready", rows["11-card.md"]["state"])
        self.assertEqual(("waiting", "waiting-integration"),
                         (rows["12-card.md"]["state"], rows["12-card.md"]["code"]))
        self.assertEqual("ready", self.projected()["lifecycle"]["word"])

    def test_exhausted_and_one_launch_budgets_are_explained(self):
        self.put_in_force(self.doc(allocation={"implementation": 1,
                                               "dailyStarts": 1}))
        self.own_execution()
        self.phase()
        budget = self.life()["budget"]
        self.assertEqual({"own": 1, "used": 0}, budget["dailyStarts"])
        self.assertTrue(any("not a member's whole lifecycle" in w
                            for w in budget["warnings"]))
        self.assertTrue(any("reserved out of the Bench scope" in line
                            for line in budget["lines"]))
        # today's one launch spent — by a review of another card of the
        # phase, say: a launch is a launch, whichever kind
        op = operations.begin(operations.key_for("review", "12-card.md", "b"),
                              action="review", card="12-card.md", number="12",
                              scope="phase:9", pool="review", basis="b",
                              attempt=1)
        operations.update(op["key"], "ended", outcome="done")
        life = self.life()
        self.assertTrue(life["budget"]["missing"])
        self.assertEqual("waiting", life["word"])
        self.assertIn("used up", life["line"])
        self.assertEqual("daily-starts-exhausted",
                         self.item("11-card.md")["code"])

    def test_zero_allocation_says_what_is_missing(self):
        self.put_in_force(self.doc(allocation={}))
        self.own_execution()
        self.phase()
        missing = self.life()["budget"]["missing"]
        self.assertTrue(any("no implementation slot" in m for m in missing))
        self.assertEqual("waiting", self.life()["word"])

    def test_paused(self):
        revision = self.put_in_force(self.doc())
        self.own_execution()
        self.phase()
        agreements.set_scope("phase:9", "pause", revision)
        life = self.life()
        self.assertEqual("paused", life["word"])
        self.assertIn("resume", life["next"]["text"])

    def test_reading_is_passive(self):
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("to-do", "in-progress"))
        before = (len(operations.all_ops()), list(self.board.moves),
                  len(self.notes))
        for _ in range(3):
            self.life()
            self.projected()
        self.assertEqual(before, (len(operations.all_ops()),
                                  list(self.board.moves), len(self.notes)))
        self.assertEqual([], self.launch.calls)

    def test_a_restart_reads_the_same_from_disk(self):
        """Nothing about the reading lives in memory but the runs: a
        restarted board (an empty registry) reads an ended run's member
        as stopped work, never as unstarted."""
        self.put_in_force(self.doc())
        self.own_execution()
        self.phase(stages=("in-progress", "to-do"))
        self.branches.add("task/11-card")
        state.AGENTS.clear()
        self.assertEqual("stopped", self.row("11-card.md")["state"])
        self.assertEqual("stopped", self.life()["word"])


if __name__ == "__main__":
    unittest.main()
