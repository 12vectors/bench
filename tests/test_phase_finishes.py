"""Finishing a phase finishes its cards, and clears up after them (task 59).

A member stops at `review/` on purpose, and **merge & clean up** on the
phase card is the moment that stops being right: the phase's branch is in
`main`, so every card it carried is too. These run the real thing — a real
phase run over a real git repo, then a real merge — because what the card
is about is which branches ended up inside which, and mocking git would be
mocking the subject.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402
import watch  # noqa: E402

from tests.test_phase_runs import (DIES, ONE, PHASE, PHASE_BRANCH, TWO,  # noqa: E402
                                   PhaseCase, card, git)

THREE = "33-the-landing-page.md"
THREE_LISTED = ("- 31 — Stand up site/\n- 32 — Serve it\n"
                "- 33 — The landing page\n")


class PhaseEnding(PhaseCase):
    """The whole run, then the ending: two members merged into the phase
    branch, the phase card in review/, and a person dragging it to done/."""

    def run_phase(self) -> None:
        self.start()        # cut the branch, run 31
        self.advance()      # merge 31, run 32
        self.advance()      # merge 32, open the phase PR, card → review/
        self.assertEqual(self.stage_of(PHASE), "review", "the run never finished")

    def complete(self, stage: str = "review") -> dict:
        return github.complete_task(PHASE, stage)

    def worktrees(self) -> list[str]:
        return [line.split(" ", 1)[1] for line in
                git(self.repo, "worktree", "list", "--porcelain").stdout.splitlines()
                if line.startswith("worktree ")]

    def branches(self) -> list[str]:
        return git(self.repo, "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/").stdout.split()

    def ending(self) -> str:
        """The one line the ticker gets for the ending."""
        lines = [s for s in self.summaries() if s.startswith("phase ")
                 and "finished" in s]
        self.assertEqual(len(lines), 1, f"one ending, not {len(lines)}: {lines}")
        return lines[0]


class AFinishedPhase(PhaseEnding):
    """Merge & clean up on the phase card, single-player and offline."""

    # — the cards —

    def test_the_phase_card_and_every_merged_member_are_done(self):
        self.run_phase()

        self.complete()

        self.assertEqual(self.stage_of(PHASE), "done")
        self.assertEqual(self.stage_of(ONE), "done")
        self.assertEqual(self.stage_of(TWO), "done")

    def test_the_swept_cards_say_done_in_their_own_headers(self):
        """A move is the file *and* the Status line — a swept card that
        still says Review would wear `status drift` the moment it landed."""
        self.run_phase()

        self.complete()

        self.assertIn("**Status:** Done", self.text(ONE))
        self.assertIn("**Status:** Done", self.text(TWO))

    def test_the_completion_reports_what_went_with_it(self):
        self.run_phase()

        result = self.complete()

        self.assertTrue(result["merged"])
        self.assertEqual(sorted(result["swept"]), sorted([ONE, TWO]),
                         "the browser is told which cards went, for its toast")

    # — the workspaces —

    def test_no_merged_member_leaves_a_worktree_or_a_branch(self):
        self.run_phase()
        for stem in (PHASE[:-3], ONE[:-3], TWO[:-3]):
            self.assertIn(str(config.WORKTREES / stem), self.worktrees(),
                          "the run should have left one to clean up")

        self.complete()

        self.assertEqual(self.worktrees(), [str(self.repo)],
                         "git worktree list names only what was there before")
        self.assertEqual(self.branches(), ["main"],
                         "git branch names only what was there before")
        self.assertFalse((config.WORKTREES / ONE[:-3]).exists())
        self.assertFalse((config.WORKTREES / TWO[:-3]).exists())

    def test_a_members_pr_state_is_dropped_with_its_branch(self):
        self.run_phase()
        github.PR_STATE[ONE] = {"verdict": "green", "ci": "pass", "url": "u"}

        self.complete()

        self.assertNotIn(ONE, github.PR_STATE)

    # — one ending, said once —

    def test_the_ticker_reports_one_ending_naming_the_phase_and_the_count(self):
        self.run_phase()
        state.BOARD_EVENTS.clear()

        self.complete()

        line = self.ending()
        self.assertIn("40 — Ship the site", line)
        self.assertIn("2 cards", line)
        self.assertIn("done/", line)

    def test_the_member_moves_do_not_also_scroll_past(self):
        """The watcher narrates every move it finds on disk. A sweep is one
        thing that happens to move several files, so the moves it makes are
        claimed as already told — and the phase card's own move is not."""
        self.run_phase()
        before = watch._board_sig()
        state.BOARD_EVENTS.clear()

        self.complete()
        watch.narrate(before, watch._board_sig())

        moves = [e for e in state.BOARD_EVENTS if e.get("kind") == "move"]
        self.assertEqual([e["file"] for e in moves], [PHASE],
                         "only the card the person dragged narrates its move")
        self.ending()

    def test_a_card_moved_normally_still_narrates(self):
        """The suppression is scoped to the sweep: an ordinary move made
        through the board is as loud as it ever was."""
        before = watch._board_sig()
        taskfiles.move_task(ONE, "backlog", "to-do")
        watch.narrate(before, watch._board_sig())

        self.assertTrue(any(e.get("kind") == "move" and e["file"] == ONE
                            for e in state.BOARD_EVENTS))

    # — only what the phase merged —

    def test_a_member_the_phase_never_merged_is_left_exactly_as_it_is(self):
        """Walked back out of review/ before the phase could bring it: its
        card, its worktree and its branch are none of the ending's
        business, and there is work in all three."""
        self.write_cards(listed=THREE_LISTED)
        self.write(THREE, card("33 — The landing page"))
        self.start()
        self.advance()          # merge 31, run 32
        self.advance()          # merge 32, run 33
        self.assertEqual(self.stage_of(THREE), "review")
        taskfiles.move_task(THREE, "review", "in-progress")
        state.BOARD_EVENTS.clear()

        self.complete("in-progress")

        self.assertEqual(self.stage_of(THREE), "in-progress", "the card stays")
        self.assertTrue(self.branch_exists(f"task/{THREE[:-3]}"))
        self.assertIn(str(config.WORKTREES / THREE[:-3]), self.worktrees())
        self.assertEqual(self.stage_of(ONE), "done")
        self.assertEqual(self.stage_of(TWO), "done")
        self.assertIn("1 card it never merged stays where it is", self.ending())

    def test_a_member_whose_run_died_is_not_swept(self):
        self.write_cards(listed=THREE_LISTED)
        self.write(THREE, card("33 — The landing page"))
        self.start()
        self.advance()
        self.adapter_is(DIES)
        self.advance()          # 33 dies; the phase halts on it
        self.advance()
        self.assertEqual(self.stage_of(THREE), "in-progress")

        self.complete("in-progress")

        self.assertEqual(self.stage_of(THREE), "in-progress")
        self.assertEqual(self.stage_of(ONE), "done")

    def test_only_what_reached_the_branch_goes_however_far_the_run_got(self):
        """Stopped mid-run: 31 is merged, 32 has reached review/ but the
        phase has not brought it, and 33 has not been started. Exactly one
        of those three is the ending's business."""
        self.write_cards(listed=THREE_LISTED)
        self.write(THREE, card("33 — The landing page"))
        self.start()
        self.advance()          # merge 31, run 32 — and stop there

        self.complete("in-progress")

        self.assertEqual(self.stage_of(ONE), "done", "merged, so finished")
        self.assertEqual(self.stage_of(TWO), "review",
                         "in review/ but never merged into the phase branch")
        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"))
        self.assertEqual(self.stage_of(THREE), "backlog", "never started")

    # — abort together —

    def test_a_merge_conflict_moves_nothing_at_all(self):
        self.run_phase()
        for where, text in ((self.repo, "main's own line\n"),
                            (config.WORKTREES / PHASE[:-3], "the phase's line\n")):
            (where / "contested.txt").write_text(text, encoding="utf-8")
            git(where, "add", "-A")
            git(where, "commit", "-q", "-m", "contested")

        with self.assertRaises(ValueError) as caught:
            self.complete()

        self.assertIn("merge conflict", str(caught.exception))
        self.assertEqual(self.stage_of(PHASE), "review", "the phase card stays")
        self.assertEqual(self.stage_of(ONE), "review", "and so does every member")
        self.assertEqual(self.stage_of(TWO), "review")
        self.assertTrue(self.branch_exists(f"task/{ONE[:-3]}"))
        self.assertIn(str(config.WORKTREES / ONE[:-3]), self.worktrees())

    def test_the_sweep_waits_for_the_merge_rather_than_racing_it(self):
        """Ordering, not luck: the cards move after the merge has landed,
        so a merge that fails half way finds nothing already swept."""
        self.run_phase()
        seen = {}
        real = github._merge_locally
        self.addCleanup(setattr, github, "_merge_locally", real)

        def watched(filename, branch):
            seen["before"] = (self.stage_of(ONE), self.stage_of(TWO))
            real(filename, branch)
        github._merge_locally = watched

        self.complete()

        self.assertEqual(seen["before"], ("review", "review"))

    # — the other endings do not sweep —

    def test_just_moving_the_card_moves_no_member(self):
        self.run_phase()

        taskfiles.move_task(PHASE, "review", "done")

        self.assertEqual(self.stage_of(PHASE), "done")
        self.assertEqual(self.stage_of(ONE), "review")
        self.assertEqual(self.stage_of(TWO), "review")
        self.assertTrue(self.branch_exists(f"task/{ONE[:-3]}"))

    def test_archiving_the_phase_card_marks_nothing_done(self):
        """Archiving releases the members to the board in whatever stage
        they are genuinely in — which is not done/, because nothing was
        merged into main."""
        self.run_phase()
        taskfiles.move_task(PHASE, "review", "to-do")

        taskfiles.archive_task(PHASE, "to-do")

        self.assertTrue((self.tasks / "archive" / PHASE).is_file())
        self.assertEqual(self.stage_of(ONE), "review")
        self.assertEqual(self.stage_of(TWO), "review")
        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"))
        self.assertIn(str(config.WORKTREES / TWO[:-3]), self.worktrees())

    # — the edges —

    def test_a_member_already_moved_to_done_by_hand_is_no_trouble(self):
        self.run_phase()
        taskfiles.move_task(ONE, "review", "done")

        result = self.complete()

        self.assertEqual(self.stage_of(ONE), "done")
        self.assertEqual(self.stage_of(TWO), "done")
        self.assertEqual(result["swept"], [TWO],
                         "a card already there is not moved twice")
        self.assertFalse(self.branch_exists(f"task/{ONE[:-3]}"),
                         "it was still cleaned up: its work is in main too")

    def test_a_dirty_worktree_is_reported_and_kept_not_forced(self):
        self.run_phase()
        (config.WORKTREES / TWO[:-3] / "notes.txt").write_text(
            "something a person was in the middle of\n", encoding="utf-8")
        state.BOARD_EVENTS.clear()

        self.complete()

        self.assertTrue((config.WORKTREES / TWO[:-3] / "notes.txt").is_file(),
                        "uncommitted work is never thrown away")
        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"),
                        "and its branch stays with it — it is still checked out")
        self.assertIn(f"{TWO[:-3]}: worktree kept — 1 uncommitted change in it "
                      f"— nothing uncommitted is thrown away", self.summaries(),
                      "the ticker says which one and why")
        self.assertEqual(self.stage_of(TWO), "done",
                         "the card is still finished: its work is in main")
        self.assertFalse((config.WORKTREES / ONE[:-3]).exists(),
                         "the clean one still went")

    def test_a_phase_that_merged_nothing_says_so_and_moves_nobody(self):
        """The card is still the person's to drag to done/ — what it must
        not do is claim an ending for work that never landed."""
        self.write_cards(listed=THREE_LISTED)
        self.write(THREE, card("33 — The landing page"))
        self.start()            # 31 runs, but nothing is merged yet
        state.BOARD_EVENTS.clear()

        result = self.complete("in-progress")

        self.assertEqual(result["swept"], [])
        self.assertEqual(self.stage_of(ONE), "review")
        self.assertIn("it had merged nothing, so no card went with it",
                      self.ending())
        self.assertIn("3 cards it never merged stay where they are", self.ending())

    def test_a_card_with_no_members_completes_as_it_always_did(self):
        """An ordinary card never reaches any of this."""
        self.write(ONE, card("31 — Stand up site/", status="Review"), "review")
        (self.tasks / "backlog" / ONE).unlink()
        git(self.repo, "worktree", "add", "-q", "-b", f"task/{ONE[:-3]}",
            str(config.WORKTREES / ONE[:-3]))
        (config.WORKTREES / ONE[:-3] / "work.txt").write_text("x\n", encoding="utf-8")
        git(config.WORKTREES / ONE[:-3], "add", "-A")
        git(config.WORKTREES / ONE[:-3], "commit", "-q", "-m", "work")

        result = github.complete_task(ONE, "review")

        self.assertEqual(result, {"merged": True, "swept": []})
        self.assertEqual(self.stage_of(ONE), "done")


class TheSweepReachesGit(PhaseEnding):
    """With `BOARD_COMMIT_MOVES` on: a sweep that never left one working
    tree is not a sweep."""

    def setUp(self):
        super().setUp()
        self.patch(COMMIT_MOVES=True)

    def commits(self) -> list[str]:
        return git(self.repo, "log", "--format=%s", "main").stdout.splitlines()

    def test_the_sweep_is_one_commit_that_names_what_moved(self):
        self.run_phase()

        self.complete()

        sweep = [s for s in self.commits() if s.startswith("board: ") and "→ done" in s]
        self.assertIn("board: 31, 32 → done with phase 40 (tester)", sweep)
        self.assertNotIn("board: 31 → done (tester)", sweep,
                         "five cards are not five lines in the log")
        self.assertIn("board: 40 → done (tester)", sweep,
                      "the card the person dragged still moves as itself")

    def test_the_swept_files_are_committed_where_they_landed(self):
        self.run_phase()

        self.complete()

        self.assertEqual(git(self.repo, "status", "--porcelain").stdout.strip(), "",
                         "nothing is left modified for a human to notice later")
        listed = git(self.repo, "ls-files", "tasks/done").stdout.split()
        for name in (PHASE, ONE, TWO):
            self.assertIn(f"tasks/done/{name}", listed)

    def test_the_commit_carries_the_prefix_sync_publishes_on(self):
        """`board: ` is what the piggyback guard looks for: a commit
        without it stalls every later push."""
        self.run_phase()
        published: list[str] = []
        self.addCleanup(state.COMMIT_HOOKS.clear)
        state.COMMIT_HOOKS.append(published.append)

        self.complete()

        self.assertIn(ONE, published, "the sweep fires the same commit hook")
        for line in self.commits():
            if "→ done" in line:
                self.assertTrue(line.startswith("board: "), line)


class TheRemoteIsClearedToo(PhaseEnding):
    """The third workspace a member leaves behind is on the remote."""

    REMOTE = True

    def remote_branches(self) -> list[str]:
        out = git(self.repo, "ls-remote", "--heads", "origin").stdout
        return [line.split("refs/heads/")[-1] for line in out.splitlines() if line]

    def test_no_merged_member_leaves_a_branch_on_the_remote(self):
        self.run_phase()
        git(self.repo, "push", "-q", "origin",
            f"task/{ONE[:-3]}", f"task/{TWO[:-3]}")
        self.assertIn(f"task/{ONE[:-3]}", self.remote_branches())

        self.complete()

        self.assertEqual(self.remote_branches(), ["main"])

    def test_a_member_the_phase_never_merged_keeps_its_remote_branch(self):
        self.write_cards(listed=THREE_LISTED)
        self.write(THREE, card("33 — The landing page"))
        self.start()
        self.advance()
        self.advance()
        taskfiles.move_task(THREE, "review", "in-progress")
        git(self.repo, "push", "-q", "origin", f"task/{THREE[:-3]}")

        self.complete("in-progress")

        self.assertIn(f"task/{THREE[:-3]}", self.remote_branches())


class TeamModeEndsItOnOrigin(PhaseEnding):
    """With `BOARD_SYNC` on the merge is made by GitHub, so local main has
    not seen it when the sweep runs. Nothing in the sweep may depend on
    that: what the phase carried is answered by the phase branch, which is
    still here, and the members' branches are deleted with `-D` for
    exactly the reason the phase card's own is."""

    REMOTE = True

    def test_the_cards_and_their_workspaces_still_go(self):
        self.run_phase()
        git(self.repo, "push", "-q", "origin",
            f"task/{ONE[:-3]}", f"task/{TWO[:-3]}")
        self.patch(SYNC=True, COMMIT_MOVES=True)

        result = self.complete()

        self.assertTrue(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()),
                        "the merge is origin's to make in team mode")
        self.assertEqual(sorted(result["swept"]), sorted([ONE, TWO]))
        self.assertEqual(self.stage_of(ONE), "done")
        self.assertEqual(self.stage_of(TWO), "done")
        self.assertFalse(self.branch_exists(f"task/{ONE[:-3]}"),
                         "a branch main has not caught up with is still gone")
        remote = git(self.repo, "ls-remote", "--heads", "origin").stdout
        self.assertNotIn(f"task/{ONE[:-3]}", remote)
        self.assertIn("board: 31, 32 → done with phase 40 (tester)",
                      git(self.repo, "log", "--format=%s", "main").stdout)


class TheDocumentedEnding(unittest.TestCase):
    """AGENTS.md is the design record — the ending has to be in it."""

    @classmethod
    def setUpClass(cls):
        cls.doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")

    def test_the_sweep_is_described(self):
        self.assertIn("### Finishing a phase finishes its cards", self.doc)

    def test_it_says_what_is_not_swept(self):
        section = self.doc.split("### Finishing a phase finishes its cards")[1]
        section = section.split("\n### ")[0]
        for promise in ("halted", "walked back", "uncommitted", "single commit",
                        "Just move the card", "Archiving"):
            self.assertIn(promise, section, f"the record must say: {promise}")


class TheSheetSaysSo(unittest.TestCase):
    """board.html has no frontend runner — these are the source-level
    invariants of what the person is told before they click."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")

    def test_the_sheet_counts_the_cards_that_would_go_with_it(self):
        self.assertIn("const swept = task.isPhase ? mergedIn(task).size : 0;",
                      self.html)
        self.assertIn("card${swept === 1 ? '' : 's'} this phase merged go to done/",
                      self.html)

    def test_just_moving_the_card_says_the_members_stay(self):
        self.assertIn('and so does every card in the phase', self.html)

    def test_the_toast_names_how_many_went(self):
        self.assertIn("const went = (data.swept || []).length;", self.html)


if __name__ == "__main__":
    unittest.main()
