"""A card being merged and cleaned up looks like it, and holds still (task 38).

"Merge & clean up" parks the drive, merges, deletes a worktree and a
branch and moves the card — seconds to a minute of destructive work the
card used to render as "waiting on you", fully draggable, every action
armed. The state is server-held (`state.COMPLETING`), so these run the
real thing against a real git repo: a real merge, a real conflict, and a
real second request arriving mid-run.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import drive as drive_mod  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402

BOARD_HTML = REPO / "manager" / "core" / "board.html"
FILENAME = "38-a-card-being-completed.md"
STEM = FILENAME[:-3]
BRANCH = f"task/{STEM}"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


CARD = ("# 38 — A card with work on it\n\n"
        "**Status:** Review\n**Priority:** Medium\n\n"
        "Body text long enough that git sees a rename rather than a delete\n"
        "and an add when the file moves between stage directories.\n")


class TheRegistry(unittest.TestCase):
    """state.COMPLETING: one claim per card, the latest narrated step, and
    a release that always publishes."""

    def setUp(self):
        state.COMPLETING.clear()
        self.addCleanup(state.COMPLETING.clear)
        state.BOARD_EVENTS.clear()
        self.addCleanup(state.BOARD_EVENTS.clear)
        self.sent: list[dict] = []
        original = state.broadcast
        self.addCleanup(setattr, state, "broadcast", original)
        state.broadcast = self.sent.append
        # persist() writes the event log; keep the test off the real one
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-registry-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, config, "SESSIONS_DIR", config.SESSIONS_DIR)
        config.SESSIONS_DIR = self.tmp

    def published(self) -> list[dict]:
        return [m["completing"] for m in self.sent if m["type"] == "completing"]

    def test_a_second_claim_is_refused_rather_than_queued(self):
        self.assertTrue(state.claim_completing(FILENAME, "merging…"))
        self.assertFalse(state.claim_completing(FILENAME, "merging again…"),
                         "one completion per card, ever")
        self.assertEqual(state.COMPLETING[FILENAME]["step"], "merging…",
                         "the refused claim must not overwrite the live one")

    def test_the_claim_reaches_the_browser_immediately(self):
        state.claim_completing(FILENAME, "merging and cleaning up…")
        self.assertEqual(self.published()[-1][FILENAME]["step"],
                         "merging and cleaning up…")

    def test_narrated_steps_become_the_card_s_line(self):
        state.claim_completing(FILENAME, "merging and cleaning up…")
        state.record_board_event({"kind": "agent", "actor": "board",
                                  "file": FILENAME, "summary": "parking the drive"})
        self.assertEqual(state.COMPLETING[FILENAME]["step"], "parking the drive")
        self.assertEqual(self.published()[-1][FILENAME]["step"], "parking the drive")

    def test_another_card_s_events_are_not_this_card_s_step(self):
        state.claim_completing(FILENAME, "merging and cleaning up…")
        before = len(self.published())
        state.record_board_event({"kind": "agent", "actor": "board",
                                  "file": "07-something-else.md",
                                  "summary": "merged task/07 into main"})
        self.assertEqual(state.COMPLETING[FILENAME]["step"], "merging and cleaning up…")
        self.assertEqual(len(self.published()), before,
                         "an unrelated event must not republish the registry")

    def test_release_empties_it_and_says_so(self):
        state.claim_completing(FILENAME, "merging…")
        state.release_completing(FILENAME)
        self.assertEqual(state.COMPLETING, {})
        self.assertEqual(self.published()[-1], {})

    def test_releasing_what_was_never_claimed_is_silent(self):
        state.release_completing(FILENAME)
        self.assertEqual(self.published(), [])

    def test_the_public_view_is_a_copy(self):
        state.claim_completing(FILENAME, "merging…")
        snapshot = state.completing_public()
        snapshot[FILENAME]["step"] = "tampered"
        self.assertEqual(state.COMPLETING[FILENAME]["step"], "merging…")


class ACompletionInFlight(unittest.TestCase):
    """One board, one card in review/ with a branch and a commit on it.
    Sync off, so the merge is the local one."""

    def setUp(self):
        # resolve(): macOS tempdirs sit behind the /var → /private/var
        # symlink and git reports the resolved path.
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-completing-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.board = self.tmp / "board"
        self.board.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.board)],
                       check=True, capture_output=True)
        git(self.board, "config", "user.name", "ada")
        git(self.board, "config", "user.email", "ada@example.com")
        for slug in config.STAGE_DIRS:
            (self.board / "tasks" / slug).mkdir(parents=True)
        (self.board / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.board, "add", "-A")
        git(self.board, "commit", "-q", "-m", "root")
        (self.board / "tasks" / "review" / FILENAME).write_text(CARD, encoding="utf-8")

        self.patch(REPO=self.board, TASKS=self.board / "tasks",
                   WORKTREES=self.tmp / "worktrees", SESSIONS_DIR=self.tmp / "sessions",
                   SYNC=False, COMMIT_MOVES=False)
        self.worktree = config.WORKTREES / STEM
        git(self.board, "worktree", "add", "-q", "-b", BRANCH, str(self.worktree))
        (self.worktree / "feature.txt").write_text("the work\n", encoding="utf-8")
        git(self.worktree, "add", "-A")
        git(self.worktree, "commit", "-q", "-m", "the work")

        state.COMPLETING.clear()
        self.addCleanup(state.COMPLETING.clear)
        state.BOARD_EVENTS.clear()
        self.addCleanup(state.BOARD_EVENTS.clear)
        state.EXPECTED_MOVES.clear()
        self.addCleanup(state.EXPECTED_MOVES.clear)
        self.addCleanup(setattr, drive_mod, "DRIVE", drive_mod.DRIVE)
        drive_mod.DRIVE = None
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def stage_of(self) -> str | None:
        for slug in config.STAGE_DIRS:
            if (self.board / "tasks" / slug / FILENAME).is_file():
                return slug
        return None

    def steps(self) -> list[str]:
        return [m["completing"][FILENAME]["step"] for m in self.sent
                if m["type"] == "completing" and FILENAME in m["completing"]]

    def narrated(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS if e.get("file") == FILENAME]

    def conflict(self) -> None:
        """The same line, two ways: main and the branch cannot be merged."""
        for where, text in ((self.board, "main's version\n"),
                            (self.worktree, "the branch's version\n")):
            (where / "contested.txt").write_text(text, encoding="utf-8")
            git(where, "add", "-A")
            git(where, "commit", "-q", "-m", f"contested in {where.name}")

    # — the card is busy from the first step to the last —

    def test_the_claim_is_held_before_the_drive_is_parked(self):
        seen = {}
        drive_mod.DRIVE = {"task": FILENAME, "status": "up", "proc": None,
                           "pgid": None, "started": 0, "log": str(self.tmp / "d.log")}
        self.addCleanup(setattr, drive_mod, "stop", drive_mod.stop)

        def fake_stop():
            seen["claimed"] = FILENAME in state.COMPLETING
            drive_mod.DRIVE["status"] = "parked"
        drive_mod.stop = fake_stop

        github.complete_task(FILENAME, "review")

        self.assertTrue(seen.get("claimed"),
                        "the card must already be busy when the drive is parked")

    def test_the_card_tracks_the_steps_the_ticker_narrates(self):
        github.complete_task(FILENAME, "review")

        steps = self.steps()
        self.assertEqual(steps[0], "merging and cleaning up…")
        self.assertTrue(any("merged" in s and "into main" in s for s in steps),
                        f"the merge never reached the card: {steps}")
        self.assertTrue(any(s.startswith("cleaned up:") for s in steps),
                        f"the cleanup never reached the card: {steps}")
        for summary in self.narrated():
            self.assertIn(summary, steps,
                          "every step narrated to the ticker lands on the card too")

    def test_a_finished_completion_gives_the_card_back(self):
        github.complete_task(FILENAME, "review")

        self.assertEqual(self.stage_of(), "done")
        self.assertEqual(state.COMPLETING, {})
        self.assertEqual([m["completing"] for m in self.sent
                          if m["type"] == "completing"][-1], {},
                         "the last word to the browser is 'nothing is busy'")

    # — every failure gives it back too —

    def test_a_merge_conflict_releases_the_card(self):
        self.conflict()

        with self.assertRaises(ValueError) as caught:
            github.complete_task(FILENAME, "review")

        self.assertIn("merge conflict", str(caught.exception))
        self.assertEqual(self.stage_of(), "review", "the card stays put")
        self.assertEqual(state.COMPLETING, {}, "…and comes back to life")
        self.assertEqual(git(self.board, "rev-parse", "--verify", "--quiet",
                             BRANCH).returncode, 0, "the branch survives a conflict")

    def test_the_wrong_branch_refusal_releases_the_card(self):
        git(self.board, "checkout", "-q", "-b", "side")

        with self.assertRaises(ValueError) as caught:
            github.complete_task(FILENAME, "review")

        self.assertIn("not main", str(caught.exception))
        self.assertEqual(state.COMPLETING, {})

    def test_a_crash_mid_completion_releases_the_card(self):
        self.addCleanup(setattr, github, "_merge_locally", github._merge_locally)

        def boom(filename, branch):
            raise OSError("git went away")
        github._merge_locally = boom

        with self.assertRaises(OSError):
            github.complete_task(FILENAME, "review")

        self.assertEqual(state.COMPLETING, {}, "a finally, not a release per exit")

    # — and the second request is refused, not run —

    def test_a_second_request_mid_completion_starts_no_second_merge(self):
        real = github._merge_locally
        self.addCleanup(setattr, github, "_merge_locally", real)
        calls, refused = [], []

        def merge_then_race(filename, branch):
            calls.append(branch)
            try:
                github.complete_task(FILENAME, "review")
            except ValueError as exc:
                refused.append(str(exc))
            real(filename, branch)
        github._merge_locally = merge_then_race

        github.complete_task(FILENAME, "review")

        self.assertEqual(len(refused), 1, "the second request must be refused")
        self.assertIn("already being completed", refused[0])
        self.assertEqual(len(calls), 1, "and no second merge attempted")
        self.assertEqual(self.stage_of(), "done", "the first run finished normally")

    def test_the_refusal_does_not_release_the_run_it_lost_to(self):
        state.claim_completing(FILENAME, "merging and cleaning up…")

        with self.assertRaises(ValueError):
            github.complete_task(FILENAME, "review")

        self.assertIn(FILENAME, state.COMPLETING,
                      "a refused caller must never release what it did not take")
        self.assertEqual(self.stage_of(), "review")


class ServedToTheBrowser(unittest.TestCase):
    def test_state_payload_carries_the_registry(self):
        import httpd
        self.assertIn("completing", httpd.state_payload())

    def test_a_restart_leaves_nothing_stuck(self):
        """The registry is memory, never disk: a board killed mid-merge
        comes back with every card renderable from the files alone."""
        source = (REPO / "manager" / "core" / "state.py").read_text(encoding="utf-8")
        body = re.search(r"def claim_completing.*?\n(?=\ndef )", source, re.S)
        self.assertIsNotNone(body, "claim_completing is gone")
        for persistent in ("persist(", "write_text", "open("):
            self.assertNotIn(persistent, body.group(0),
                             "the claim must not outlive the process")


class TheCardFace(unittest.TestCase):
    """board.html is a single file with no frontend runner — these are the
    source-level invariants of the surface this card adds."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD_HTML.read_text(encoding="utf-8")
        cls.card = re.search(r"function cardFor\(task\) \{.*?\n\}", cls.html, re.S).group(0)

    def test_the_card_renders_from_the_server_s_truth(self):
        self.assertIn("(S.state.completing || {})[task.file]", self.card,
                      "the busy state comes from /api/state, not from a click")
        self.assertIn("msg.type === 'completing'", self.html,
                      "…and follows the SSE stream between full loads")

    def test_it_wears_the_working_vocabulary_and_no_new_colour(self):
        completing = re.search(r"if \(completing\) \{\n\s*//.*?\n\s*//.*?\n(.*?)\n  \}",
                               self.card, re.S).group(1)
        self.assertIn("text: 'completing'", completing)
        self.assertIn("var(--accent)", completing)
        for colour in ("--alarm", "--calm", "--idle"):
            self.assertNotIn(colour, completing, "busy is not an alarm or a verdict")
        flat = self.html.replace(" ", "").replace("\n", "")
        self.assertIn(".card.completing{border-color:color-mix(inoklab,var(--accent)55%,var(--border))", flat)
        self.assertIn(".card.pill.status.breathing{animation:breathe", flat)
        self.assertIn("""class="pill status${completing ? ' breathing' : ''}\"""", self.card)

    def test_it_says_which_step(self):
        self.assertIn("esc(completing.step", self.card,
                      "the card carries the latest narrated step")

    def test_it_holds_still(self):
        self.assertIn("el.draggable = !completing;", self.card)

    def test_its_actions_are_suppressed_not_merely_ignored(self):
        self.assertRegex(self.card, r"if \(completing\) \{\n(\s*//[^\n]*\n)+\s*\} else if \(agent\)",
                         "the action builder must fall through to nothing")
        self.assertIn("if (task.stage === 'review' && !completing) {", self.card,
                      "the drive chips go with the worktree being removed")
        self.assertIn("if (hasBranch && !completing &&", self.card,
                      "…and so do the project's command chips")

    def test_the_sheet_still_only_opens_for_a_card_with_work(self):
        """Untouched path: no branch and no PR means no sheet, so no
        completion and no flash of busy."""
        mover = re.search(r"async function move\(file, from, to\) \{.*?\n\}",
                          self.html, re.S).group(0)
        self.assertIn("task && (task.pr || (S.state.branches || []).includes(stem))", mover)
        self.assertIn("completeSheet(task, from)", mover)


if __name__ == "__main__":
    unittest.main()
