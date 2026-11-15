"""Explicitly confirmed stack actions (task 119, WP10b).

Each case builds a disposable application repository with a bare remote
(`tests.test_stacks.StackCase`), and — where a host is involved — the
fake host of `tests/product/fakehost.py` in one of its modes. No real
hosting provider is reached and no agent runs.

What this holds:

- **Retarget** changes the host's base only where `retarget` reads
  `available`, and the plan and the card follow what was read back. With
  the capability `unavailable` or `unknown`, no provider mutation is
  called and the exact manual step is named. Pre-existing drift refuses,
  a host already targeting the new branch is adopted without an edit,
  and a change request with no host edit records the plan alone.
- **Every action is bound** to the identities its sheet showed, and is
  refused on a moved head, a running agent, a dirty or mismatched
  worktree, a phase member and a phase card.
- **Update from parent** is an additive merge; a conflict is aborted and
  reported with the branch as it was; a parent that is not published is
  never published by its child.
- **Finish: clean up** happens only once the remote application base is
  seen to hold the child's head, and never for a child with no commits of
  its own.
- **Review context follows the target.** A Bench verdict or a formal
  approval given before a retarget does not carry over at the same head,
  including after a later launch re-records the plan; a new head is
  history under the existing rule.

    python3 -m unittest tests.test_stack_actions -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.product import fakehost
from tests.test_integration_gates import CARD, GateCase, git as ggit
from tests.test_stacks import (C_BRANCH, CHILD, P_BRANCH, PARENT, StackCase,
                               card, git, sha)

import config  # noqa: E402  (tests.test_stacks put manager/core on the path)
import gates  # noqa: E402
import githost  # noqa: E402
import plans  # noqa: E402
import stackactions  # noqa: E402
import stacks  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402


class ActionCase(StackCase):
    """#56 delivered into main; #57 stacked on it with an open change
    request (#2) on the fake host targeting the parent's branch."""

    def setUp(self):
        super().setUp()
        self.host = fakehost.FakeHost(self.tmp / "host", self.origin)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.patch(GH_BIN=str(self.host.install(bin_dir)))
        self.addCleanup(state.AGENTS.clear)

    def landed_pair(self, *, with_pr: bool = True) -> tuple[str, str]:
        ptip, ctip = self.parent_and_child()
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 56", P_BRANCH)
        git(self.repo, "push", "-q", "origin", "main")
        self.delivered(PARENT, ptip)
        if with_pr:
            self.host.create(P_BRANCH, "main", "parent")
            url = self.host.create(C_BRANCH, P_BRANCH, "child")
            self.write(CHILD, card("57 — The child", base=P_BRANCH, pr=url))
            self.snapshot(CHILD, head=ctip, prState="OPEN", base=P_BRANCH)
        return ptip, ctip

    def edits(self) -> list[list[str]]:
        return [c for c in self.host.calls()
                if c[:2] == ["pr", "edit"] and "--help" not in c]

    def card_text(self, name: str = CHILD) -> str:
        return (self.tasks / "review" / name).read_text(encoding="utf-8")

    def confirm(self, action: str, name: str = CHILD) -> dict:
        shown = stackactions.preview(name, action)
        self.assertEqual(shown["blockers"], [], shown)
        return stackactions.run(name, action, shown["identities"])


# ── retarget ───────────────────────────────────────────────────────────


class Retarget(ActionCase):

    def test_the_reading_offers_it_once_the_parent_landed(self):
        self.landed_pair()
        found = stacks.board(taskfiles.collect())[CHILD]
        self.assertEqual(found["edge"]["state"], "parent-landed")
        self.assertIn({"action": "retarget", "target": "main",
                       "label": "Retarget onto main"}, found["actions"])

    def test_on_the_host_where_available_and_recorded_from_the_read_back(self):
        _, ctip = self.landed_pair()
        shown = stackactions.preview(CHILD, "retarget")
        self.assertEqual(shown["mode"], "host")
        self.assertEqual(shown["identities"]["newTarget"], "main")
        self.assertEqual(shown["identities"]["childHead"], ctip)
        self.assertEqual(shown["identities"]["change"], "2")
        result = stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["outcome"], "host")
        self.assertEqual(self.edits(), [["pr", "edit", "2", "--base", "main"]])
        self.assertEqual(self.host.view("2", ["baseRefName"])["baseRefName"], "main")
        plan = plans.recorded(CHILD)
        self.assertEqual(plan["prBase"], "main")
        self.assertEqual(plan["startBranch"], P_BRANCH, "the start is history")
        self.assertEqual(plan["retargets"][-1]["how"], "host")
        self.assertEqual(plan["retargets"][-1]["head"], ctip)
        self.assertIn("**PR base:** main", self.card_text())
        self.assertIn(f"**Base branch:** {P_BRANCH}", self.card_text())
        self.assertEqual(self.edge(), "retargeted")
        self.assertNotIn("retarget", [a["action"] for a in
                                      self.stacked()[CHILD]["actions"]])

    def test_the_next_launch_reads_no_drift(self):
        self.landed_pair()
        self.confirm("retarget")
        fresh = plans.preflight(CHILD, "review", fetch=False)
        self.assertEqual(fresh["blockers"], [])
        self.assertEqual(fresh["prBase"], "main")

    def test_unsupported_makes_no_provider_mutation_and_names_the_step(self):
        self.landed_pair()
        self.host.set_mode("retarget", "unsupported")
        shown = stackactions.preview(CHILD, "retarget")
        self.assertEqual(shown["mode"], "manual")
        self.assertIn("gh pr edit 2 --base main", shown["manual"])
        before = self.card_text()
        with self.assertRaises(ValueError) as caught:
            stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertIn("The manual step", str(caught.exception))
        self.assertEqual(self.edits(), [])
        self.assertEqual(self.host.view("2", ["baseRefName"])["baseRefName"], P_BRANCH)
        self.assertEqual(plans.recorded(CHILD)["prBase"], P_BRANCH)
        self.assertEqual(self.card_text(), before)

    def test_unknown_makes_no_provider_call_at_all(self):
        self.landed_pair()
        unknown = {"state": "unknown", "reason": "gave no answer",
                   "source": "installed-cli"}
        with mock.patch.object(githost, "retarget_state", return_value=unknown):
            shown = stackactions.preview(CHILD, "retarget")
            self.assertEqual(shown["mode"], "manual")
            calls = len(self.host.calls())
            with self.assertRaises(ValueError):
                stackactions.run(CHILD, "retarget", shown["identities"])
            with self.assertRaises(githost.Refused):
                githost.retarget_change("2", "main")
        self.assertEqual(len(self.host.calls()), calls,
                         "an unknown capability reached the provider")
        self.assertEqual(plans.recorded(CHILD)["prBase"], P_BRANCH)

    def test_the_host_already_targeting_it_is_adopted_without_an_edit(self):
        self.landed_pair()
        self.host.edit_base("2", "main")       # a person, on the host
        self.snapshot(CHILD, prState="OPEN", base="main",
                      baseMismatch="main", wantBase=P_BRANCH)
        self.assertEqual(self.edge(), "target-changed-on-host")
        self.host.set_mode("retarget", "unsupported")
        shown = stackactions.preview(CHILD, "retarget")
        self.assertEqual(shown["mode"], "adopt")
        result = stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertEqual(result["outcome"], "adopted")
        self.assertEqual(self.edits(), [])
        self.assertEqual(plans.recorded(CHILD)["prBase"], "main")
        self.assertEqual(self.edge(), "retargeted")

    def test_pre_existing_drift_on_the_host_refuses(self):
        self.landed_pair()
        self.branch("release/x")
        shown = stackactions.preview(CHILD, "retarget")
        self.host.edit_base("2", "release/x")   # somebody else's edit
        with self.assertRaises(ValueError) as caught:
            stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertIn("drift was already there", str(caught.exception))
        self.assertEqual(self.edits(), [])
        self.assertEqual(self.host.view("2", ["baseRefName"])["baseRefName"],
                         "release/x", "bench undid someone else's edit")
        self.assertEqual(plans.recorded(CHILD)["prBase"], P_BRANCH)

    def test_a_host_that_does_not_take_the_edit_records_nothing(self):
        self.landed_pair()
        shown = stackactions.preview(CHILD, "retarget")
        provider = githost.load("github")
        refused = {"ok": False, "detail": "HTTP 422", "returncode": 1}
        with mock.patch.object(provider, "retarget_change", return_value=refused):
            result = stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("recorded nothing", result["sentence"])
        self.assertEqual(plans.recorded(CHILD)["prBase"], P_BRANCH)
        self.assertNotIn("**PR base:**", self.card_text())

    def test_without_a_change_request_only_the_plan_moves(self):
        self.landed_pair(with_pr=False)
        shown = stackactions.preview(CHILD, "retarget")
        self.assertEqual(shown["mode"], "plan-only")
        result = stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertEqual(result["outcome"], "plan-only")
        self.assertEqual(self.host.calls(), [])
        self.assertEqual(plans.recorded(CHILD)["prBase"], "main")

    def test_a_card_edit_that_fails_puts_the_plan_back(self):
        self.landed_pair(with_pr=False)
        before = plans.recorded(CHILD)
        shown = stackactions.preview(CHILD, "retarget")
        with mock.patch.object(taskfiles, "set_pr_base",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertEqual(plans.recorded(CHILD), before,
                         "the plan says main while the card does not")
        self.assertNotIn("**PR base:**", self.card_text())

    def test_a_parent_that_has_not_landed_has_nowhere_to_retarget(self):
        self.parent_and_child()
        shown = stackactions.preview(CHILD, "retarget")
        self.assertTrue(any("has not landed" in b for b in shown["blockers"]))

    def test_a_squash_landed_parent_needs_the_target_brought_in_first(self):
        ptip, ctip = self.parent_and_child()
        self.commit_on("main", "the-squashed-parent")   # not the parent's commits
        self.snapshot(PARENT, head=ptip, prState="MERGED", merged=True)
        self.assertEqual(self.edge(), "parent-landed-differently")
        shown = stackactions.preview(CHILD, "retarget")
        self.assertTrue(any("bench will not rebase" in b
                            for b in shown["blockers"]), shown["blockers"])
        # once the target is brought in (by hand, additively) it may go
        git(self.repo, "checkout", "-q", C_BRANCH)
        git(self.repo, "merge", "-q", "--no-edit", "main")
        git(self.repo, "checkout", "-q", "main")
        shown = stackactions.preview(CHILD, "retarget")
        self.assertEqual(shown["blockers"], [])


# ── bound, and refused ─────────────────────────────────────────────────


class BoundAndRefused(ActionCase):

    def test_a_moved_head_refuses_naming_it(self):
        self.landed_pair()
        shown = stackactions.preview(CHILD, "retarget")
        self.commit_on(C_BRANCH, "after-the-sheet")
        with self.assertRaises(stackactions.Moved) as caught:
            stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertIn("childHead", str(caught.exception))
        self.assertEqual(self.edits(), [])
        self.assertEqual(plans.recorded(CHILD)["prBase"], P_BRANCH)

    def test_no_identities_no_action(self):
        self.landed_pair()
        with self.assertRaises(ValueError):
            stackactions.run(CHILD, "retarget", {})
        self.assertEqual(self.edits(), [])

    def test_a_running_agent_refuses(self):
        self.landed_pair()
        state.AGENTS["a1"] = {"task": CHILD, "status": "running"}
        shown = stackactions.preview(CHILD, "retarget")
        self.assertTrue(any("agent is working" in b for b in shown["blockers"]))
        with self.assertRaises(ValueError):
            stackactions.run(CHILD, "retarget", shown["identities"])
        self.assertEqual(self.edits(), [])

    def worktree(self) -> Path:
        path = config.WORKTREES / CHILD[:-3]
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", str(path), C_BRANCH)
        return path

    def test_a_dirty_worktree_refuses(self):
        self.landed_pair()
        (self.worktree() / "half-done.txt").write_text("wip\n", encoding="utf-8")
        for action in ("retarget", "update", "cleanup"):
            with self.subTest(action=action):
                shown = stackactions.preview(CHILD, action)
                self.assertTrue(any("uncommitted" in b for b in shown["blockers"]),
                                shown["blockers"])

    def test_a_worktree_on_another_branch_refuses(self):
        self.landed_pair()
        path = self.worktree()
        git(path, "checkout", "-q", "-b", "elsewhere")
        shown = stackactions.preview(CHILD, "retarget")
        self.assertTrue(any("not task/57-the-child" in b
                            for b in shown["blockers"]), shown["blockers"])

    def test_a_phase_member_is_the_phase_runners(self):
        self.write("55-the-phase.md", card("55 — The phase", kind="Phase",
                                           cards="- 57 — The child"))
        self.write(CHILD, card("57 — The child"))
        shown = stackactions.preview(CHILD, "retarget")
        self.assertTrue(any("phase member" in b for b in shown["blockers"]),
                        shown["blockers"])

    def test_a_phase_card_is_not_maintained_here(self):
        self.write("55-the-phase.md", card("55 — The phase", kind="Phase",
                                           cards="", base=P_BRANCH))
        self.write(PARENT, card("56 — The parent"))
        shown = stackactions.preview("55-the-phase.md", "update")
        self.assertTrue(any("phase card" in b for b in shown["blockers"]))

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            stackactions.preview(CHILD, "restack")


# ── update from parent ─────────────────────────────────────────────────


class UpdateFromParent(ActionCase):

    def test_an_additive_merge_published_with_a_plain_push(self):
        _, ctip = self.parent_and_child()
        ptip = self.commit_on(P_BRANCH, "more-parent-work")
        self.assertIn("update", [a["action"] for a in
                                 self.stacked()[CHILD]["actions"]])
        result = self.confirm("update")
        self.assertTrue(result["ok"], result)
        head = sha(self.repo, C_BRANCH)
        self.assertNotEqual(head, ctip)
        parents = git(self.repo, "rev-list", "--parents", "-n", "1",
                      head).stdout.split()[1:]
        self.assertEqual(parents, [ctip, ptip], "not an additive merge")
        self.assertTrue(result["pushed"])
        self.assertEqual(git(self.origin, "rev-parse", C_BRANCH).stdout.strip(), head)
        self.assertEqual(self.edge(), "on-parent")

    def test_a_conflict_is_aborted_and_the_branch_is_as_it_was(self):
        _, ctip = self.parent_and_child()
        for branch, text in ((P_BRANCH, "parent\n"), (C_BRANCH, "child\n")):
            git(self.repo, "checkout", "-q", branch)
            (self.repo / "code.txt").write_text(text, encoding="utf-8")
            git(self.repo, "commit", "-q", "-am", f"{branch} edits code")
            git(self.repo, "push", "-q", "origin", branch)
        git(self.repo, "checkout", "-q", "main")
        ctip = sha(self.repo, C_BRANCH)
        result = self.confirm("update")
        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "conflict")
        self.assertIn("code.txt", result["conflicts"])
        self.assertEqual(sha(self.repo, C_BRANCH), ctip)
        worktree = config.WORKTREES / CHILD[:-3]
        self.assertEqual(git(worktree, "status", "--porcelain").stdout, "")
        self.assertFalse((Path(git(worktree, "rev-parse", "--git-dir")
                               .stdout.strip()) / "MERGE_HEAD").exists())

    def test_an_unpublished_parent_is_never_published_by_its_child(self):
        self.parent_and_child()
        published = git(self.origin, "rev-parse", C_BRANCH).stdout.strip()
        self.commit_on(P_BRANCH, "parent-local-only", publish=False)
        result = self.confirm("update")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["pushed"])
        self.assertIn("publish the parent first", result["sentence"])
        self.assertEqual(git(self.origin, "rev-parse", C_BRANCH).stdout.strip(),
                         published)

    def test_a_failure_that_is_not_a_conflict_is_not_called_one(self):
        _, ctip = self.parent_and_child()
        self.commit_on(P_BRANCH, "more-parent-work")
        real = stackactions.gitrepo.git

        def refusing(path, *args, **kwargs):
            if args[:2] == ("merge", "--no-ff"):
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "",
                    "fatal: the pre-merge-commit hook refused it\n")
            return real(path, *args, **kwargs)

        with mock.patch.object(stackactions.gitrepo, "git", side_effect=refusing):
            result = self.confirm("update")
        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["conflicts"], [])
        self.assertNotIn("conflict", result["sentence"])
        self.assertIn("hook refused it", result["sentence"])
        self.assertIn("exactly as it was", result["sentence"])
        self.assertEqual(sha(self.repo, C_BRANCH), ctip)

    def test_nothing_to_bring_in_is_refused(self):
        self.parent_and_child()
        shown = stackactions.preview(CHILD, "update")
        self.assertTrue(any("already contains" in b for b in shown["blockers"]))


# ── finish: clean up ───────────────────────────────────────────────────


class FinishCleanUp(ActionCase):

    def test_only_once_the_remote_base_holds_the_head(self):
        _, ctip = self.parent_and_child()
        # merged into main here, not published
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 57", C_BRANCH)
        shown = stackactions.preview(CHILD, "cleanup")
        self.assertEqual(shown["blockers"], [])
        with self.assertRaises(ValueError) as caught:
            stackactions.run(CHILD, "cleanup", shown["identities"])
        self.assertIn("delivery is not proven", str(caught.exception))
        self.assertTrue((self.tasks / "review" / CHILD).is_file())
        self.assertEqual(sha(self.repo, C_BRANCH), ctip)

    def test_cleans_up_and_moves_to_done_without_merging(self):
        _, ctip = self.parent_and_child()
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 57", C_BRANCH)
        git(self.repo, "push", "-q", "origin", "main")
        main_before = sha(self.repo, "main")
        self.assertIn("cleanup", [a["action"] for a in
                                  self.stacked()[CHILD]["actions"]])
        result = self.confirm("cleanup")
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.tasks / "done" / CHILD).is_file())
        self.assertEqual(sha(self.repo, "main"), main_before, "something merged")
        self.assertEqual(git(self.repo, "branch", "--list", C_BRANCH).stdout, "")
        record = json.loads((config.GATES_DIR / f"{CHILD[:-3]}.delivered.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual((record["commit"], record["how"]), (ctip, "stack"))

    def test_a_child_with_no_work_of_its_own_is_not_finished(self):
        ptip, _ = self.parent_and_child(child_branch=False)
        git(self.repo, "branch", C_BRANCH, P_BRANCH)     # cut, never worked
        git(self.repo, "push", "-q", "origin", C_BRANCH)
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 56", P_BRANCH)
        git(self.repo, "push", "-q", "origin", "main")
        self.delivered(PARENT, ptip)
        self.assertNotIn("cleanup", [a["action"] for a in
                                     self.stacked()[CHILD]["actions"]])
        shown = stackactions.preview(CHILD, "cleanup")
        self.assertTrue(any("no commits of its own" in b
                            for b in shown["blockers"]), shown["blockers"])
        with self.assertRaises(ValueError):
            stackactions.run(CHILD, "cleanup", shown["identities"])
        self.assertTrue((self.tasks / "review" / CHILD).is_file())
        self.assertFalse((config.GATES_DIR / f"{CHILD[:-3]}.delivered.json")
                         .exists())

    def test_a_child_delivered_through_its_parent_is_finished(self):
        # the stack's own route: the child's work lands in its parent's
        # branch, then the parent lands — the child's head is in the
        # parent's tip and it still has work of its own
        _, ctip = self.parent_and_child()
        git(self.repo, "checkout", "-q", P_BRANCH)
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 57", C_BRANCH)
        git(self.repo, "push", "-q", "origin", P_BRANCH)
        git(self.repo, "checkout", "-q", "main")
        ptip = sha(self.repo, P_BRANCH)
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 56", P_BRANCH)
        git(self.repo, "push", "-q", "origin", "main")
        self.delivered(PARENT, ptip)
        self.assertIn("cleanup", [a["action"] for a in
                                  self.stacked()[CHILD]["actions"]])
        shown = stackactions.preview(CHILD, "cleanup")
        self.assertEqual(shown["blockers"], [])
        result = self.confirm("cleanup")
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.tasks / "done" / CHILD).is_file())
        record = json.loads((config.GATES_DIR / f"{CHILD[:-3]}.delivered.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(record["commit"], ctip)


# ── review context follows the target ──────────────────────────────────


class ReviewFollowsTheTarget(GateCase):
    """A base-only retarget (the head fixed) and a head change are both
    new review contexts; check evidence keeps its own rules."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.commit_in(self.wt)
        self.head = ggit(self.repo, "rev-parse", f"task/{CARD[:-3]}").stdout.strip()

    def row(self) -> dict:
        found = gates.evaluate(CARD, "acceptance")
        return [r for r in found["requirements"] if r["kind"] == "review"][0]

    def retarget(self) -> None:
        plans.retarget(CARD, "main", old="task/70-parent", head=self.head,
                       how="host")
        gates.invalidate()

    def test_a_bench_verdict_does_not_survive_a_base_only_retarget(self):
        self.gated(review="bench")
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        self.assertEqual(self.row()["reason"], "bench-approved")
        time.sleep(0.01)
        self.retarget()
        row = self.row()
        self.assertEqual(row["state"], "unmet")
        self.assertEqual(row["reason"], "verdict-before-retarget")
        self.assertIn("task/70-parent", row["why"])
        self.assertEqual(gates.SEVERITY[row["reason"]], "needed")
        time.sleep(0.01)
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        self.assertEqual(self.row()["reason"], "bench-approved")

    def test_a_github_approval_does_not_survive_a_retarget(self):
        self.gated(review="github")
        self.reviews_say([{"id": 2, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": self.head,
                           "submitted_at": "2026-01-01T00:00:00Z"}],
                         head=self.head)
        self.assertEqual(self.row()["reason"], "approved")
        self.retarget()
        self.assertEqual(self.row()["reason"], "approval-before-retarget")
        self.reviews_say([{"id": 3, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": self.head,
                           "submitted_at": "2999-01-01T00:00:00Z"}],
                         head=self.head)
        self.assertEqual(self.row()["reason"], "approved")

    def relaunch(self) -> None:
        """What ▸ start work on an existing branch and ↻ act on PR do
        (`agents`): record the plan preflight returned, which knows
        nothing of retargets, at an unchanged head."""
        fresh = {"task": CARD, "blockers": [], "branch": f"task/{CARD[:-3]}",
                 "startBranch": "task/70-parent", "prBase": "main"}
        with mock.patch.object(plans, "preflight", return_value=fresh):
            plans.record(plans.require(plans.preflight(CARD, "review")))
        gates.invalidate()

    def test_a_later_launch_does_not_erase_the_retarget(self):
        self.gated(review="bench")
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        time.sleep(0.01)
        self.retarget()
        self.relaunch()
        self.assertEqual(plans.latest_retarget(CARD)["to"], "main")
        self.assertEqual(self.row()["reason"], "verdict-before-retarget")

    def test_a_later_launch_does_not_restore_a_github_approval(self):
        self.gated(review="github")
        self.reviews_say([{"id": 2, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": self.head,
                           "submitted_at": "2026-01-01T00:00:00Z"}],
                         head=self.head)
        self.retarget()
        self.relaunch()
        self.assertEqual(self.row()["reason"], "approval-before-retarget")

    def test_a_new_head_is_history_under_the_existing_rule(self):
        self.gated(review="bench")
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        self.commit_in(self.wt, "an update from the parent\n")
        self.assertEqual(self.row()["reason"], "verdict-other-head")

    def test_a_replan_that_changes_the_target_is_a_retarget(self):
        plans.record({"task": CARD, "branch": f"task/{CARD[:-3]}",
                      "prBase": "task/70-parent", "startBranch": "main"})
        with mock.patch.object(plans, "preflight",
                               return_value={"task": CARD, "blockers": [],
                                             "branch": f"task/{CARD[:-3]}",
                                             "prBase": "main"}):
            plans.replan(CARD, "in-progress")
        found = plans.latest_retarget(CARD)
        self.assertEqual((found["from"], found["to"], found["how"]),
                         ("task/70-parent", "main", "replan"))


# ── the page: the sheet is the server's words, and manual means no button ─


class ThePageSheet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — the sheet is unchecked")

    def render(self, preview: dict, armed: bool = False) -> str:
        from tests.test_agents_settings_ui import lift
        body = ("const esc = s => String(s ?? '').replace(/[&<>\"]/g, c => "
                "({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
                + lift("stackSheet") + "\n"
                + "process.stdout.write(JSON.stringify(stackSheet({file: '57-x.md'}, "
                + json.dumps({"action": preview.get("action"), "armed": armed,
                              "preview": preview}) + ")));")
        out = subprocess.run([self.node, "-e", body], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_manual_names_the_step_and_offers_no_confirm(self):
        html = self.render({"action": "retarget", "mode": "manual",
                            "label": "Retarget onto main", "blockers": [],
                            "manual": "run `gh pr edit 2 --base main` yourself",
                            "identities": {"childHead": "a" * 40, "change": "2",
                                           "target": "task/56-p",
                                           "newTarget": "main"}})
        self.assertIn("Manual step.", html)
        self.assertIn("gh pr edit 2 --base main", html)
        self.assertNotIn("data-stack-confirm", html)
        self.assertIn("aaaaaaaaaaaa", html)
        self.assertNotIn("a" * 13, html)

    def test_a_refusal_offers_no_confirm(self):
        html = self.render({"action": "update", "mode": "merge",
                            "blockers": ["an agent is working on 57-x.md"],
                            "identities": {}})
        self.assertIn("Refused.", html)
        self.assertNotIn("data-stack-confirm", html)

    def test_confirm_arms_before_it_fires(self):
        shown = {"action": "update", "mode": "merge", "label": "Update from p",
                 "blockers": [], "identities": {}}
        self.assertIn(">Update from p</button>", self.render(shown))
        self.assertIn("Click again to confirm", self.render(shown, armed=True))


if __name__ == "__main__":
    unittest.main()
