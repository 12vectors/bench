"""Choosing a work base from main or committed work in Review (task 150).

Disposable application repositories with a bare `origin` beside them
(`tests.test_stacks.StackCase`), cards written into a disposable board,
and the decision taken, recorded on the card and read back by the plan
exactly as a launch reads it. The checkout is made the way ▸ start work
makes it (`gitrepo.worktree_add` from the plan's `startRef`), so "B's
checkout contains A's pinned commit" is a fact about a real worktree.

    python3 -m unittest tests.test_work_base -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))
sys.path.insert(0, str(REPO))

import basechoice  # noqa: E402
import config  # noqa: E402
import decisions  # noqa: E402
import gitrepo  # noqa: E402
import plans  # noqa: E402
import stacks  # noqa: E402
import taskfiles  # noqa: E402
import workbase  # noqa: E402
from tests.test_stacks import StackCase, git, sha  # noqa: E402

A = "61-the-api.md"
B = "62-the-ui.md"
C = "63-independent.md"
D = "64-unrelated-review.md"
A_BRANCH = "task/61-the-api"
B_BRANCH = "task/62-the-ui"


def card(title: str, *, status: str = "To Do", depends: str | None = None,
         base: str | None = None, kind: str | None = None,
         cards: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if depends:
        text += f"**Depends on:** {depends}\n"
    if base:
        text += f"**Base branch:** {base}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


class WorkBaseCase(StackCase):

    def setUp(self):
        super().setUp()
        # the record names who decided it; this board's identity
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "ada"

    def write(self, name: str, text: str, stage: str = "to-do") -> None:
        super().write(name, text, stage)

    def board(self) -> dict:
        return taskfiles.collect()

    def task(self, name: str) -> dict:
        return next(t for g in self.board()["stages"] for t in g["tasks"]
                    if t["file"] == name)

    def api_in_review(self) -> str:
        """#61 in Review, its API committed on its branch; main lacks it."""
        tip = self.branch(A_BRANCH, files=("api",))
        self.write(A, card("61 — The API", status="Review"), "review")
        return tip

    def decide(self, name: str) -> dict:
        return basechoice.decide(self.task(name), self.board())

    def preflight(self, name: str, stage: str = "to-do") -> dict:
        return plans.preflight(name, stage, fetch=False)

    def base_records(self, name: str, stage: str = "to-do") -> list[dict]:
        text = (self.tasks / stage / name).read_text(encoding="utf-8")
        return [r for r in decisions.parse(text)["records"] if r["kind"] == "base"]


class TheStackedDecision(WorkBaseCase):

    def test_dependent_work_is_built_on_the_pinned_prerequisite(self):
        tip = self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual("stacked", found["decision"])
        self.assertTrue(found["written"])
        # on the card, portable: branch, exact commit, parent, PR target
        [record] = self.base_records(B)
        self.assertEqual(("stacked", A_BRANCH, tip, "61", A_BRANCH, "main"),
                         (record["decision"], record["branch"], record["commit"],
                          record["parent"], record["prBase"], record["appBase"]))
        self.assertIn("card:61", record["evidence"])
        self.assertIn("committed change", record["summary"])
        # the executable plan agrees with it
        plan = plans.require(self.preflight(B))
        self.assertEqual((A_BRANCH, tip, tip, A_BRANCH),
                         (plan["startBranch"], plan["startRef"],
                          plan["startCommit"], plan["prBase"]))
        self.assertIn("work-base decision", plan["source"])
        self.assertEqual(record["id"], plan["workBase"]["id"])
        # the checkout ▸ start work would make holds A's committed API
        worktree = Path(plan["worktree"])
        made = gitrepo.worktree_add(worktree, plan["branch"], new=True,
                                    start=plan["startRef"])
        self.assertEqual(0, made.returncode, made.stderr)
        self.assertTrue((worktree / "task_61-the-api-api.txt").exists())
        self.assertEqual(0, git(self.repo, "merge-base", "--is-ancestor", tip,
                                plan["branch"]).returncode)
        # and the stack reading sees B stacked on A, from the decision
        reading = stacks.board(self.board())
        self.assertEqual("61-the-api.md", reading[B]["parent"]["file"])

    def test_the_decision_is_written_once_and_survives_a_reload(self):
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        first = self.decide(B)
        again = self.decide(B)          # a repeated tick, a restart
        self.assertTrue(first["written"])
        self.assertFalse(again["written"])
        self.assertEqual(first["record"], again["record"])
        self.assertEqual(1, len(self.base_records(B)))
        # read back off the card alone, as a replica after a sync would
        text = (self.tasks / "to-do" / B).read_text(encoding="utf-8")
        found = workbase.reading(taskfiles.card_parts(text)["history"])
        self.assertEqual(first["record"], found["accepted"]["id"])

    def test_assessing_is_a_read(self):
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        before = (self.tasks / "to-do" / B).read_text(encoding="utf-8")
        with mock.patch.object(gitrepo, "fetch_branch",
                               side_effect=AssertionError("fetched")):
            found = basechoice.assess(self.task(B), self.board())
            basechoice.reading(self.task(B), self.board())
        self.assertEqual("stacked", found["decision"])
        self.assertEqual(before, (self.tasks / "to-do" / B).read_text(encoding="utf-8"))
        self.assertFalse(config.PLANS_DIR.exists() and any(config.PLANS_DIR.iterdir()))


class TheNormalBase(WorkBaseCase):

    def test_independent_work_starts_from_the_normal_base(self):
        self.api_in_review()
        self.write(C, card("63 — Independent"))
        found = self.decide(C)
        self.assertEqual("normal", found["decision"])
        plan = plans.require(self.preflight(C))
        self.assertEqual(("main", "main"), (plan["startBranch"], plan["prBase"]))
        self.assertEqual("Starting from main",
                         plans.summary(self.task(C))["workBase"]["headline"])

    def test_a_review_card_that_is_not_a_dependency_is_never_selected(self):
        self.api_in_review()
        self.branch("task/64-unrelated-review")
        # a similar title and the same files are not evidence
        self.write(D, card("64 — The UI API", status="Review"), "review")
        self.write(C, card("63 — The UI for the API"))
        found = self.decide(C)
        self.assertEqual("normal", found["decision"])
        unselected = {c["card"]: c["why"] for c in found["considered"]}
        self.assertIn("not a declared dependency", unselected["64"])
        self.assertIn("not a declared dependency", unselected["61"])

    def test_uncommitted_prerequisite_work_is_not_a_base(self):
        # #61 is in Review with a branch that holds nothing main does not;
        # what its checkout has uncommitted is invisible here
        git(self.repo, "branch", A_BRANCH, "main")
        self.write(A, card("61 — The API", status="Review"), "review")
        (self.repo / "dirty.txt").write_text("not committed\n", encoding="utf-8")
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual(("blocked", "no-committed-work"),
                         (found["decision"], found["code"]))
        # a blocked decision refuses a new start rather than picking main
        plan = self.preflight(B)
        self.assertTrue(plan["blockers"])
        self.assertIn("blocked", plan["blockers"][0])

    def test_an_unavailable_prerequisite_is_reported_not_guessed(self):
        self.write(A, card("61 — The API", status="Review"), "review")
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual(("blocked", "prerequisite-unavailable"),
                         (found["decision"], found["code"]))
        self.assertIn("cannot be read", found["summary"])


class ConstraintsOutrankIt(WorkBaseCase):

    def test_an_explicit_header_governs(self):
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61", base="main"))
        found = self.decide(B)
        self.assertEqual("explicit", found["decision"])
        self.assertIn("not overridden", found["summary"])
        plan = plans.require(self.preflight(B))
        self.assertEqual("the card's **Base branch:** header", plan["source"])
        self.assertEqual("main", plan["startBranch"])

    def test_a_phase_member_does_not_escape_its_phase(self):
        self.api_in_review()
        self.write("70-phase.md", card("70 — Phase", status="In Progress",
                                       kind="Phase", cards="- 62 — The UI"),
                   "in-progress")
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual(("blocked", "phase-conflict"),
                         (found["decision"], found["code"]))
        # and the plan, which the phase governs, never reads the decision
        self.assertEqual(f"phase/70-phase",
                         plans.declared(self.task(B))["startBranch"])

    def test_unrelated_prerequisites_are_not_combined(self):
        self.api_in_review()
        self.branch("task/65-other")
        self.write("65-other.md", card("65 — Other", status="Review"), "review")
        self.write(B, card("62 — The UI", depends="61, 65"))
        found = self.decide(B)
        self.assertEqual(("blocked", "multiple-prerequisites"),
                         (found["decision"], found["code"]))

    def test_a_chain_of_prerequisites_builds_on_its_top(self):
        self.api_in_review()
        top = self.branch("task/65-on-the-api", start=A_BRANCH)
        self.write("65-on-the-api.md", card("65 — On the API", status="Review",
                                            base=A_BRANCH), "review")
        self.write(B, card("62 — The UI", depends="61, 65"))
        found = self.decide(B)
        self.assertEqual(("stacked", "task/65-on-the-api", top),
                         (found["decision"], found["branch"], found["commit"]))

    def test_a_cycle_is_refused(self):
        self.branch(A_BRANCH, files=("api",))
        self.write(A, card("61 — The API", status="Review", base=B_BRANCH), "review")
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual(("blocked", "dependency-cycle"),
                         (found["decision"], found["code"]))


class ThePrerequisiteEvolves(WorkBaseCase):

    def test_a_parent_that_moves_before_launch_stales_the_decision(self):
        tip = self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        moved = self.commit_on(A_BRANCH, "review-fix")
        # detected before any worktree or run
        plan = self.preflight(B)
        self.assertTrue(plan["blockers"])
        self.assertIn("stale", plan["blockers"][0])
        self.assertFalse(Path(plan["worktree"]).exists())
        self.assertIn("moved", basechoice.recheck(B, fetch=False))
        # recorded stale, visibly, once: the recheck wrote it, and
        # observing the same change writes nothing more
        self.assertEqual([], basechoice.observe(self.board()))
        self.assertEqual([], basechoice.observe(self.board()))
        latest = self.base_records(B)[-1]
        self.assertEqual(("stale", "parent-moved"), (latest["decision"], latest["code"]))
        self.assertIn("Building on #61", plans.summary(self.task(B))["workBase"]["headline"])
        # decided again on what is there now, and then the plan holds again
        again = self.decide(B)
        self.assertEqual(("stacked", moved), (again["decision"], again["commit"]))
        self.assertNotEqual(tip, again["commit"])
        plan = plans.require(self.preflight(B))
        self.assertEqual(moved, plan["startRef"])

    def test_work_under_way_keeps_its_recorded_plan(self):
        tip = self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        plan = plans.require(self.preflight(B))
        gitrepo.worktree_add(Path(plan["worktree"]), plan["branch"], new=True,
                             start=plan["startRef"])
        plans.record(plan)
        self.commit_on(A_BRANCH, "parent-moves-on")
        basechoice.observe(self.board())
        resumed = plans.require(self.preflight(B, "to-do"))
        self.assertTrue(resumed["continuing"])
        self.assertEqual((tip, A_BRANCH), (resumed["startCommit"], resumed["prBase"]))
        # the source under the worker is untouched
        self.assertEqual(tip, sha(self.repo, f"{plan['branch']}~0"))
        fresh = basechoice.freshness(self.task(B), self.board())
        self.assertEqual(("stale", "parent-moved", True),
                         (fresh["state"], fresh["code"], fresh["started"]))
        self.assertIn("Update from the parent", fresh["remedy"])

    def test_rejection_and_landing_are_reassessed(self):
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        plan = plans.require(self.preflight(B))
        gitrepo.worktree_add(Path(plan["worktree"]), plan["branch"], new=True,
                             start=plan["startRef"])
        # rejected: back to in-progress
        (self.tasks / "review" / A).rename(self.tasks / "in-progress" / A)
        fresh = basechoice.freshness(self.task(B), self.board())
        self.assertEqual("parent-returned", fresh["code"])
        # landed: delivered by a squash, the branch gone
        (self.tasks / "in-progress" / A).rename(self.tasks / "done" / A)
        git(self.repo, "merge", "-q", "--squash", A_BRANCH)
        git(self.repo, "commit", "-q", "-m", "squash #61")
        git(self.repo, "branch", "-D", A_BRANCH)
        fresh = basechoice.freshness(self.task(B), self.board())
        self.assertEqual("parent-landed", fresh["code"])
        self.assertIn("Retarget onto the normal base", fresh["remedy"])
        # observing touches no branch: B's branch is where it was
        before = sha(self.repo, plan["branch"])
        basechoice.observe(self.board())
        self.assertEqual(before, sha(self.repo, plan["branch"]))
        # the stack's own reading carries the confirmed next action
        reading = stacks.board(self.board())[B]
        self.assertIn((reading.get("edge") or {}).get("state"),
                      stacks.LANDED + ("parent-gone",))


class ReviewDefects(WorkBaseCase):
    """The three lifecycle defects the review of 0bf63da found."""

    def test_the_decision_pins_what_the_launch_will_resolve(self):
        # defect 1: this checkout's copy of the prerequisite has a commit
        # the remote does not (and the other way round): the decision, the
        # recheck and the fetching preflight read one answer
        tip = self.api_in_review()
        self.commit_on(A_BRANCH, "unpushed", publish=False)
        self.write(B, card("62 — The UI", depends="61"))
        found = self.decide(B)
        self.assertEqual(tip, found["commit"])       # the remote's copy
        self.assertIsNone(basechoice.recheck(B))     # fetching, as the launch does
        plan = plans.require(plans.preflight(B, "to-do"))
        self.assertEqual(tip, plan["startRef"])

    def test_a_push_from_elsewhere_is_reassessed_not_stuck(self):
        tip = self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        # someone else pushes a fix to the prerequisite: this checkout has
        # not fetched it, so local reads still say nothing changed
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", str(self.origin), str(other))
        git(other, "config", "user.email", "o@o")
        git(other, "config", "user.name", "other")
        git(other, "checkout", "-q", A_BRANCH)
        (other / "fix.txt").write_text("fix\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "fix")
        git(other, "push", "-q", "origin", A_BRANCH)
        pushed = sha(other, A_BRANCH)
        # the recheck fetches as the launch would, sees it, records it stale
        self.assertIn("moved", basechoice.recheck(B))
        self.assertEqual(("stale", "parent-moved"),
                         (self.base_records(B)[-1]["decision"],
                          self.base_records(B)[-1]["code"]))
        # and the next decision is made on the commit the launch will find
        again = self.decide(B)
        self.assertEqual(("stacked", pushed), (again["decision"], again["commit"]))
        self.assertTrue(again["written"])
        self.assertIsNone(basechoice.recheck(B))
        self.assertEqual(pushed, plans.require(plans.preflight(B, "to-do"))["startRef"])
        self.assertNotEqual(tip, pushed)

    def _start(self, name: str) -> dict:
        plan = plans.require(self.preflight(name))
        gitrepo.worktree_add(Path(plan["worktree"]), plan["branch"], new=True,
                             start=plan["startRef"])
        plans.record(plan)
        return plan

    def test_a_retry_does_not_remake_the_decision_when_the_parent_moved(self):
        # defect 2, first half
        tip = self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        first = self.decide(B)
        self._start(B)
        self.commit_on(A_BRANCH, "parent-moves-on")
        again = self.decide(B)
        self.assertEqual(("under-way", False), (again["decision"], again["written"]))
        self.assertEqual(first["record"], again["record"])
        self.assertEqual([first["record"]], [r["id"] for r in self.base_records(B)])
        self.assertIsNone(basechoice.recheck(B))
        resumed = plans.require(self.preflight(B))
        self.assertTrue(resumed["continuing"])
        self.assertEqual(tip, resumed["startCommit"])
        # the observer records it stale, and the card does not claim that
        # anything is reassessing work that has started
        basechoice.observe(self.board())
        said = basechoice.reading(self.task(B), self.board())["headline"]
        self.assertIn("work under way keeps its recorded plan", said)
        self.assertNotIn("decided again", said)

    def test_a_stale_decision_says_who_decides_it_again(self):
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        self.commit_on(A_BRANCH, "parent-moves-on")
        basechoice.observe(self.board())
        said = basechoice.reading(self.task(B), self.board())["headline"]
        self.assertIn("stale; decided again before it starts", said)
        refused = self.preflight(B)
        self.assertTrue(any("on a card worked by hand nothing will" in b
                            for b in refused["blockers"]), refused["blockers"])

    def test_a_retry_does_not_remake_the_decision_when_the_parent_landed(self):
        # defect 2, second half: the card stays stacked, its Stack block
        # and Retarget stay, and the relaunch is not refused as a changed plan
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        self.decide(B)
        self._start(B)
        (self.tasks / "review" / A).rename(self.tasks / "done" / A)
        basechoice.observe(self.board())
        again = self.decide(B)
        self.assertEqual("under-way", again["decision"])
        self.assertNotIn("normal", [r["decision"] for r in self.base_records(B)])
        self.assertEqual(A_BRANCH, plans.declared(self.task(B))["prBase"])
        resumed = plans.require(self.preflight(B))
        self.assertTrue(resumed["continuing"])
        parent = stacks.board(self.board())[B]["parent"]
        self.assertEqual((A, A_BRANCH), (parent["file"], parent["branch"]))

    def test_a_blocked_decision_reaches_the_card_through_a_pass(self):
        # defect 3: the scheduler's plan holds the card; the pass writes
        # the blocked decision and an open blocker with its remedy, once
        import scheduler
        self.api_in_review()
        self.branch("task/65-other-api", files=("other",))
        self.write("65-other-api.md", card("65 — Other API", status="Review"),
                   "review")
        self.write(B, card("62 — The UI", depends="61, 65"))
        found = {"items": [{"card": B, "state": "blocked",
                            "code": "base-multiple-prerequisites"}]}
        allowed = {"allowed": True, "code": "ok", "reason": ""}
        with mock.patch.object(scheduler.orchestrator, "authorize",
                               return_value=allowed):
            first = scheduler.record_base_blocks(found, self.board())
            second = scheduler.record_base_blocks(found, self.board())
        self.assertEqual(["base-blocked-recorded"], [r["outcome"] for r in first])
        self.assertEqual([], second)
        [record] = self.base_records(B)
        self.assertEqual(("blocked", "multiple-prerequisites"),
                         (record["decision"], record["code"]))
        text = (self.tasks / "to-do" / B).read_text(encoding="utf-8")
        blockers = decisions.read_text(text, B)["blockers"]
        self.assertEqual(["open"], [b["state"] for b in blockers])
        self.assertIn("Base branch", blockers[0]["text"])
        # a card this scope may not act on is not written on
        refused = {"allowed": False, "code": "not-eligible", "reason": "x"}
        self.write(C, card("63 — Independent", depends="61, 65"))
        with mock.patch.object(scheduler.orchestrator, "authorize",
                               return_value=refused):
            scheduler.record_base_blocks(
                {"items": [{"card": C, "state": "blocked",
                            "code": "base-multiple-prerequisites"}]}, self.board())
        self.assertEqual([], self.base_records(C))
        # once resolved by a header-free sequencing, the blocker clears
        (self.tasks / "review" / "65-other-api.md").rename(
            self.tasks / "done" / "65-other-api.md")
        settled = self.decide(B)
        self.assertEqual("stacked", settled["decision"])
        text = (self.tasks / "to-do" / B).read_text(encoding="utf-8")
        self.assertEqual("cleared",
                         decisions.read_text(text, B)["blockers"][0]["state"])


class AnUnwrittenDecision(WorkBaseCase):
    """A decision that never reached the card launches nothing: the plan
    could not read it, so the work would start from the application base."""

    def test_a_refused_write_launches_nothing(self):
        import operations
        import scheduler
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        allowed = {"allowed": True, "code": "ok", "reason": "", "scope": "bench"}
        started = []
        with mock.patch.object(scheduler.orchestrator, "authorize",
                               return_value=allowed), \
                mock.patch.object(basechoice.decisions, "note",
                                  side_effect=ValueError("the record section is full")):
            result = scheduler.execute(self.task(B), {}, self.board(),
                                       start=lambda *a: started.append(a))
        self.assertEqual("base-unrecorded", result["outcome"])
        self.assertIn("stacked", result["reason"])
        self.assertIn("the record section is full", result["reason"])
        self.assertEqual([], started, "nothing was launched")
        self.assertEqual([], operations.for_card(B, scheduler.ACTION),
                         "no slot was reserved and no start spent")
        self.assertEqual([], self.base_records(B))
        self.assertTrue((self.tasks / "to-do" / B).is_file(), "the card did not move")


class OnTheSchedule(WorkBaseCase):
    """The scheduler's plan reads a Review dependency through the decision
    instead of waiting for it to be done."""

    def test_a_review_dependency_is_assessed_not_waited_on(self):
        import scheduler
        self.api_in_review()
        self.write(B, card("62 — The UI", depends="61"))
        by_number = {"61": self.task(A)}
        self.assertIsNone(scheduler._dependency(self.task(B), by_number, set()))
        self.assertTrue(scheduler._has_review_dependency(self.task(B), by_number))


if __name__ == "__main__":
    unittest.main()
