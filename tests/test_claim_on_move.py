"""Claiming is moving (task 18): a board-made move writes the assignee and
commits itself — and with the gate off, changes nothing about today.

Every case runs against a throwaway git repo standing in for the project, so
the commit behaviour is checked against real git rather than a mock.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CARD = ("# 18 — A card that gets claimed\n\n"
        "**Status:** Backlog\n"
        "**Priority:** High\n"
        "**Type:** Feature\n\n"
        "Body text nobody should touch.\n")
FILENAME = "18-a-card.md"
MOVER = "Mover One"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class ClaimOnMove(unittest.TestCase):
    def setUp(self):
        # resolve(): macOS tempdirs sit behind the /var → /private/var
        # symlink and git reports the resolved path, so absolute pathspecs
        # only match if we resolve too.
        tmp = Path(tempfile.mkdtemp(prefix="bench-claim-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", MOVER)
        git(self.repo, "config", "user.email", "mover@example.com")

        self.patch(TASKS=self.repo / "tasks", REPO=self.repo,
                   SESSIONS_DIR=tmp / "sessions", COMMIT_MOVES=True)
        state.BOARD_EVENTS.clear()

        (self.repo / "unrelated.txt").write_text("one\n", encoding="utf-8")
        self.write("backlog", CARD)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.baseline = self.commit_count()

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — the repo under test —

    def path(self, stage: str) -> Path:
        return self.repo / "tasks" / stage / FILENAME

    def write(self, stage: str, text: str) -> None:
        self.path(stage).write_text(text, encoding="utf-8")

    def read(self, stage: str) -> str:
        return self.path(stage).read_text(encoding="utf-8")

    def commit_count(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def head_message(self) -> str:
        return git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()

    def head_files(self) -> list[str]:
        # --no-renames: the point is which paths the commit touched, not how
        # git chooses to describe the pair.
        out = git(self.repo, "show", "--name-only", "--no-renames",
                  "--pretty=format:", "HEAD").stdout
        return sorted(line for line in out.splitlines() if line.strip())

    def hand_move(self, source: str, target: str, text: str) -> None:
        """A teammate's plain mv, committed — the starting point for cases
        that need the card somewhere other than backlog/."""
        shutil.move(str(self.path(source)), str(self.path(target)))
        self.write(target, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"hand move to {target}")
        self.baseline = self.commit_count()

    def porcelain(self) -> str:
        return git(self.repo, "status", "--porcelain").stdout

    # — claiming —

    def test_a_forward_move_claims_the_card_and_commits_once(self):
        task = taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertEqual(task["assignee"], MOVER)
        self.assertIn(f"**Assignee:** {MOVER}", self.read("to-do"))
        self.assertEqual(self.commit_count(), self.baseline + 1)
        self.assertEqual(self.head_message(), f"board: 18 → to-do ({MOVER})")
        self.assertEqual(self.head_files(),
                         ["tasks/backlog/" + FILENAME, "tasks/to-do/" + FILENAME])
        self.assertEqual(self.porcelain(), "",
                         "the move and the claim leave nothing behind uncommitted")

    def test_the_claim_joins_the_header_and_leaves_the_rest_alone(self):
        taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertEqual(self.read("to-do"), CARD
                         .replace("**Status:** Backlog", "**Status:** To Do")
                         .replace("**Status:** To Do\n",
                                  f"**Status:** To Do\n**Assignee:** {MOVER}\n"))

    def test_to_do_to_in_progress_claims_too(self):
        self.hand_move("backlog", "to-do", CARD.replace("Backlog", "To Do"))

        task = taskfiles.move_task(FILENAME, "to-do", "in-progress")

        self.assertEqual(task["assignee"], MOVER)
        self.assertEqual(self.head_message(), f"board: 18 → in-progress ({MOVER})")

    def test_first_claim_sticks_when_someone_else_moves_it_on(self):
        self.hand_move("backlog", "to-do",
                       CARD.replace("**Status:** Backlog",
                                    "**Status:** To Do\n**Assignee:** ada"))
        git(self.repo, "config", "user.name", "Mover Two")

        task = taskfiles.move_task(FILENAME, "to-do", "in-progress")

        self.assertEqual(task["assignee"], "ada", "the first claim owns the card")
        self.assertEqual(self.read("in-progress").count("**Assignee:**"), 1)
        self.assertEqual(self.head_message(), "board: 18 → in-progress (Mover Two)",
                         "the commit names who acted, not who holds it")

    def test_a_move_that_is_not_a_claim_writes_no_assignee(self):
        self.hand_move("backlog", "in-progress",
                       CARD.replace("Backlog", "In Progress"))

        task = taskfiles.move_task(FILENAME, "in-progress", "review")

        self.assertIsNone(task["assignee"])
        self.assertNotIn("**Assignee:**", self.read("review"))
        self.assertEqual(self.commit_count(), self.baseline + 1,
                         "the move itself still commits")

    def test_walking_back_to_backlog_clears_the_claim(self):
        self.hand_move("backlog", "in-progress",
                       CARD.replace("**Status:** Backlog",
                                    "**Status:** In Progress\n**Assignee:** ada"))

        task = taskfiles.move_task(FILENAME, "in-progress", "backlog")

        self.assertIsNone(task["assignee"])
        self.assertEqual(self.read("backlog"), CARD, "back to the unclaimed card")

    def test_no_git_identity_claims_nothing(self):
        """A checkout git cannot name claims nothing — there is no identity
        to write. (git itself then refuses the commit, which is narrated.)"""
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: ""

        task = taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertIsNone(task["assignee"])
        self.assertNotIn("**Assignee:**", self.read("to-do"))

    # — the gate —

    def test_gate_off_moves_exactly_as_before(self):
        self.patch(COMMIT_MOVES=False)

        task = taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertIsNone(task["assignee"])
        self.assertEqual(self.read("to-do"),
                         CARD.replace("**Status:** Backlog", "**Status:** To Do"))
        self.assertEqual(self.commit_count(), self.baseline)
        self.assertEqual(self.porcelain(),
                         " D tasks/backlog/18-a-card.md\n?? tasks/to-do/\n",
                         "the move stays for a human to commit, index untouched")

    def test_gate_off_leaves_a_claimed_card_claimed(self):
        self.patch(COMMIT_MOVES=False)
        self.hand_move("backlog", "in-progress",
                       CARD.replace("**Status:** Backlog",
                                    "**Status:** In Progress\n**Assignee:** ada"))

        task = taskfiles.move_task(FILENAME, "in-progress", "backlog")

        self.assertEqual(task["assignee"], "ada",
                         "with the gate off the board rewrites Status and nothing else")

    # — the commit —

    def test_unrelated_staged_work_is_neither_committed_nor_unstaged(self):
        (self.repo / "unrelated.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "--", "unrelated.txt")

        taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertEqual(self.head_files(),
                         ["tasks/backlog/" + FILENAME, "tasks/to-do/" + FILENAME])
        self.assertEqual(self.porcelain(), "M  unrelated.txt\n",
                         "the developer's staged change is still staged")
        self.assertIn("+two", git(self.repo, "diff", "--cached", "HEAD",
                                  "--", "unrelated.txt").stdout)

    def test_unrelated_unstaged_work_is_left_alone(self):
        (self.repo / "unrelated.txt").write_text("two\n", encoding="utf-8")

        taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertEqual(self.porcelain(), " M unrelated.txt\n")

    def test_a_failing_commit_still_moves_the_card_and_says_so(self):
        self.patch(REPO=self.repo.parent / "not-a-repo")
        (self.repo.parent / "not-a-repo").mkdir()

        task = taskfiles.move_task(FILENAME, "backlog", "to-do")

        self.assertEqual(task["stage"], "to-do")
        self.assertTrue(self.path("to-do").is_file())
        event = state.BOARD_EVENTS[-1]
        self.assertIn("committing it failed", event["summary"])
        self.assertNotEqual(event["kind"], "move",
                            "a move-kind event is rendered from its from/to "
                            "fields, which this one has none of")


class ClaimPredicate(unittest.TestCase):
    """Which transitions claim: leaving an unstarted stage, forwards only."""

    def test_claiming_transitions(self):
        for source, target in (("backlog", "to-do"), ("backlog", "in-progress"),
                               ("to-do", "in-progress"), ("to-do", "review")):
            self.assertTrue(taskfiles.claims(source, target), f"{source} → {target}")

    def test_non_claiming_transitions(self):
        for source, target in (("to-do", "backlog"), ("in-progress", "review"),
                               ("review", "done"), ("done", "to-do"),
                               ("in-progress", "backlog")):
            self.assertFalse(taskfiles.claims(source, target), f"{source} → {target}")


class AssigneeParsing(unittest.TestCase):
    def test_read_task_exposes_the_assignee(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-claim-read-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / FILENAME
        path.write_text(CARD.replace("**Status:** Backlog",
                                     "**Status:** Backlog\n**Assignee:** ada lovelace"),
                        encoding="utf-8")

        self.assertEqual(taskfiles.read_task(path, "backlog")["assignee"], "ada lovelace")

    def test_an_unclaimed_card_has_no_assignee(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-claim-read-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / FILENAME
        path.write_text(CARD, encoding="utf-8")

        self.assertIsNone(taskfiles.read_task(path, "backlog")["assignee"])


class CardFace(unittest.TestCase):
    """board.html is a single file with no frontend runner — these are the
    source-level invariants of the face showing an owner."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")

    def test_the_assignee_replaces_nobody_yet(self):
        self.assertIn("if (task.assignee) { who = task.assignee;", self.html)
        index = self.html.index("if (task.assignee) { who = task.assignee;")
        self.assertLess(index, self.html.index("who = 'nobody yet'"),
                        "the claim must be checked before the stage fallbacks")

    def test_the_who_row_escapes_what_the_file_said(self):
        self.assertIn('<span class="who">${esc(who)}</span>', self.html)


if __name__ == "__main__":
    unittest.main()
