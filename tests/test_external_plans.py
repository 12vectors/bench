"""An execution plan inside an external workspace (tasks 56 + 58).

Each of the two cards has a suite of its own. What neither can see alone
is the seam between them: a plan resolves *application* branches, records
itself under the *tool's* runtime state, and opens its PR on the
*application's* repository — three different roots, in a layout where
they are genuinely three different places rather than one checkout
answering every question.

The cases here are the integration ones only, and they are the ones a
merge of the two could plausibly break:

- a per-card `**Base branch:**` resolved in the application's git, not
  the work state's, with the worktree under the external worktrees root
- the PR opened against the card's own target, on the application's
  repository, named explicitly to `gh`
- the recorded plan living with the tool, and governing after the
  operator moves `BOARD_APP_BASE`
- cleanup that keeps a dirty worktree, and forgets a plan only when the
  branch it described really went

Disposable repositories, a stub adapter and a stub `gh` throughout, on
the harness task 56's own suite uses: no token is spent, no PR is opened
and no remote outside the temporary directory is touched.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import plans  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_external_workspace import (CARD_FILE, PR_URL,  # noqa: E402
                                           Workspace, git)

PREREQ = "task/55-the-prerequisite"
STEM = CARD_FILE[:-3]
BRANCH = f"task/{STEM}"

# A run that does the job: one file, one commit, a clean exit.
WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "built.txt"), "w").write("the work\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "did it"], check=True)
print("WORK REPORT: built it")
"""

# A run that dies having written a file and committed nothing: the one
# case where forcing the worktree out would destroy work.
DIES_DIRTY = """#!/usr/bin/env python3
import os, sys
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "half-done.txt"), "w").write("mid-edit\\n")
print("API Error: 500")
sys.exit(1)
"""

# A run that dies having touched nothing at all.
DIES_CLEAN = """#!/usr/bin/env python3
import sys
print("API Error: 500")
sys.exit(1)
"""

# gh, as far as these cases are concerned: every invocation is logged, so
# which repository it was told to act on is a fact a test can read.
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
open_base = os.environ.get("GH_BASE", "")
if args[:2] == ["pr", "create"]:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"] and any("baseRefName" in a for a in args):
    if not open_base:
        sys.exit(1)
    print(json.dumps({{"url": "{PR_URL}", "baseRefName": open_base}}))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"reviews": [], "reviewRequests": [],
                      "statusCheckRollup": [], "state": "OPEN",
                      "mergeable": "MERGEABLE",
                      "baseRefName": open_base or "main"}}))
else:
    sys.exit(0)
'''


def card(title: str, *, status: str = "In Progress", base: str | None = None,
         pr_base: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if base is not None:
        text += f"**Base branch:** {base}\n"
    if pr_base is not None:
        text += f"**PR base:** {pr_base}\n"
    text += "\nWhat this card is for, at enough length to read as a brief.\n"
    return text


class ExternalPlanCase(Workspace):
    """Task 56's workspace, plus the two things a plan needs: somewhere of
    its own to be recorded, and a prerequisite branch to be stacked on.

    The prerequisite is published and then the application's base moves
    on, so the two genuinely differ and "which commit was this cut from"
    has a wrong answer available to be caught.
    """

    def setUp(self):
        super().setUp()
        self.plans_dir = self.local / "state" / "plans"
        self.patch(PLANS_DIR=self.plans_dir)
        self._executable(self.gh, FAKE_GH)
        self._executable(self.adapter, WORKS)
        self.addCleanup(os.environ.pop, "GH_BASE", None)

        git(self.app, "checkout", "-q", "-b", PREREQ)
        (self.app / "prereq.txt").write_text("the groundwork\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "the prerequisite")
        git(self.app, "push", "-q", "-u", "origin", PREREQ)
        git(self.app, "checkout", "-q", "main")
        (self.app / "elsewhere.txt").write_text("landed on main\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "something else entirely")
        git(self.app, "push", "-q", "origin", "main")
        self.prereq_tip = git(self.app, "rev-parse", PREREQ).stdout.strip()
        self.main_tip = git(self.app, "rev-parse", "main").stdout.strip()

    # — the furniture —

    def plan(self, *, name: str = CARD_FILE, stage: str = "in-progress") -> dict:
        return plans.preflight(name, stage)

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines()]

    def run_agent(self, *, name: str = CARD_FILE) -> dict:
        record = agents.start_agent(name, "in-progress")
        self.settle()
        return state.AGENTS[record["id"]]

    def run_and_open_pr(self, *, name: str = CARD_FILE) -> dict:
        """The whole arc a card walks: a run, the move into review/ its
        ending makes, and the PR that move opens. `watch` is what fires
        the last step on a live board (the actor-only rule lives there),
        so it is called here as the move's actor would.
        """
        record = self.run_agent(name=name)
        self.assertEqual(self.stage_of(name), "review")
        github.maybe_open_pr(name)
        return record

    def branches(self) -> list[str]:
        return git(self.app, "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/").stdout.split()


class APerCardBaseResolvesInTheApplication(ExternalPlanCase):
    """A card that names its own base, in a workspace where the
    application's git is not the board's. Every branch in the plan is a
    branch of the application, and the worktree lands under the external
    worktrees root."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces", base=PREREQ),
                   "in-progress")

    def test_the_plan_resolves_the_prerequisite_in_the_application(self):
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startBranch"], PREREQ)
        self.assertEqual(plan["startCommit"], self.prereq_tip)
        self.assertEqual(plan["prBase"], PREREQ,
                         "the PR target defaults to the base the card named")
        self.assertIn("**Base branch:**", plan["source"])

    def test_the_work_is_cut_from_it_into_the_external_worktrees_root(self):
        self.run_agent()
        worktree = self.worktrees / STEM
        self.assertTrue((worktree / "built.txt").is_file())
        self.assertEqual(
            git(self.app, "merge-base", PREREQ, BRANCH).stdout.strip(),
            self.prereq_tip,
            "the branch was not cut from the prerequisite's tip")
        self.assertTrue((worktree / "prereq.txt").is_file(),
                        "the prerequisite's own work is not in the checkout")
        self.assertFalse((worktree / "elsewhere.txt").is_file(),
                         "the branch picked up main instead of its base")

    def test_the_application_gains_no_board_files_for_any_of_it(self):
        self.run_agent()
        tracked = self.app_tracked()
        self.assertIn("code.txt", tracked)
        for path in tracked:
            self.assertFalse(path.startswith(".task-manager/")
                             or path.startswith("manager/")
                             or path.startswith("tasks/"),
                             f"the board put {path} in the application")
        subjects = git(self.app, "log", "--format=%s", BRANCH).stdout.splitlines()
        self.assertFalse([s for s in subjects if s.startswith("board:")],
                         "a bookkeeping commit landed on the application")

    def test_an_unreachable_named_base_refuses_and_never_falls_back(self):
        self.write(CARD_FILE, card("56 — External workspaces",
                                   base="task/99-never-existed"), "in-progress")
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        with self.assertRaises(ValueError):
            agents.start_agent(CARD_FILE, "in-progress")
        self.assertNotIn(BRANCH, self.branches())
        self.assertFalse((self.worktrees / STEM).exists())
        self.assertIsNone(plans.recorded(CARD_FILE))


class ThePRGoesToTheApplicationsRepository(ExternalPlanCase):
    """The card's own target, on the application's GitHub repository —
    named to `gh` rather than inferred from a checkout that has two
    identities in play."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces", base=PREREQ),
                   "in-progress")

    def test_the_pr_is_opened_against_the_cards_own_target(self):
        self.run_and_open_pr()
        creates = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, self.gh_calls())
        self.assertIn("--base", creates[0])
        self.assertEqual(creates[0][creates[0].index("--base") + 1], PREREQ)
        self.assertIn(PR_URL, self.body(CARD_FILE))

    def test_gh_is_told_the_applications_repository_not_the_boards(self):
        """Two remotes, two hosts: the application's and the work state's.
        `gh` must be pointed at the first and never the second."""
        git(self.app, "remote", "set-url", "origin",
            "https://github.com/acme/widget.git")
        git(self.work, "remote", "add", "origin",
            "https://github.com/acme/board.git")
        self.assertEqual(config.gh_repo(), "acme/widget")
        github._gh_pr("view", "some-branch", "--json", "url,baseRefName")
        views = [c for c in self.gh_calls() if c[:2] == ["pr", "view"]]
        self.assertTrue(views)
        self.assertIn("--repo", views[-1])
        self.assertEqual(views[-1][views[-1].index("--repo") + 1], "acme/widget")

    def test_a_pr_github_has_against_another_base_is_reported_not_retargeted(self):
        """The card's `**PR:**` line gone (a hand edit, a lost board) and
        GitHub holding a PR aimed somewhere else: neither duplicated nor
        retargeted, and the application's branch is left alone."""
        self.run_and_open_pr()
        path = self.tasks / "review" / CARD_FILE
        path.write_text("\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("**PR:**")) + "\n", encoding="utf-8")
        os.environ["GH_BASE"] = "main"

        with self.assertRaises(ValueError) as caught:
            github.open_pr_now(CARD_FILE)

        said = str(caught.exception)
        self.assertIn("already has a PR into main", said)
        self.assertIn(PREREQ, said)
        creates = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, "a second PR was opened")


class TheRecordedPlanLivesWithTheTool(ExternalPlanCase):
    """Machine-local, beside the agent logs — in neither repository — and
    it governs the work it describes for the life of that work."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces"), "in-progress")

    def test_it_is_written_under_the_tools_state_and_committed_nowhere(self):
        self.run_agent()
        saved = plans.recorded(CARD_FILE)
        self.assertIsNotNone(saved)
        path = self.plans_dir / f"{STEM}.json"
        self.assertTrue(path.is_file(), "the plan was recorded somewhere else")
        self.assertTrue(str(path).startswith(str(self.tool)),
                        f"{path} is not under the tool install")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout, "",
                         "the plan left something in the application")
        self.assertNotIn("plans", " ".join(self.app_tracked()))
        self.assertNotIn("state", " ".join(git(self.work, "ls-files").stdout.split()))

    def test_it_keeps_this_cards_target_when_the_default_moves(self):
        self.run_and_open_pr()
        git(self.app, "branch", "trunk", "main")
        self.patch(APP_BASE="trunk")
        self.assertEqual(plans.pr_base(CARD_FILE), "main",
                         "changing the setting retargeted work already under way")
        creates = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(creates[0][creates[0].index("--base") + 1], "main")

    def test_the_portable_half_carries_no_path_from_this_machine(self):
        record = self.run_agent()
        portable = record.get("plan") or {}
        self.assertTrue(portable)
        self.assertNotIn("worktree", portable)
        blob = json.dumps(portable)
        self.assertNotIn(str(self.worktrees), blob)
        self.assertNotIn(str(self.tool), blob)


class CleanupKeepsWorkAndTheRecordOfIt(ExternalPlanCase):
    """A dead run's workspace, in the external layout: uncommitted work
    keeps the worktree, the branch and the plan that names it; a run that
    left nothing clears all three."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces", base=PREREQ),
                   "in-progress")

    def test_a_dirty_dead_run_keeps_its_worktree_branch_and_plan(self):
        self._executable(self.adapter, DIES_DIRTY)
        self.run_agent()
        worktree = self.worktrees / STEM
        self.assertTrue((worktree / "half-done.txt").is_file(),
                        "uncommitted work was thrown away")
        self.assertIn(BRANCH, self.branches())
        self.assertIsNotNone(plans.recorded(CARD_FILE),
                             "the plan went while the branch it names stayed")
        self.assertTrue(any("uncommitted change" in s for s in self.summaries()),
                        self.summaries())

    def test_a_dead_run_that_left_nothing_clears_the_workspace_and_the_plan(self):
        self._executable(self.adapter, DIES_CLEAN)
        self.run_agent()
        self.assertFalse((self.worktrees / STEM).exists())
        self.assertNotIn(BRANCH, self.branches())
        self.assertIsNone(plans.recorded(CARD_FILE),
                          "a plan survived the branch it described")

    def test_the_card_stays_put_and_wears_the_failure_either_way(self):
        self._executable(self.adapter, DIES_DIRTY)
        record = self.run_agent()
        self.assertEqual(self.stage_of(CARD_FILE), "in-progress")
        self.assertEqual(record["status"], "failed")
        self.assertIn("500", (record.get("failure") or {}).get("excerpt", ""))


if __name__ == "__main__":
    unittest.main()
