"""A board reading holds still while a card moves under it (task 155).

`taskfiles.collect()` lists each stage directory and then reads every card
it listed. A card moved in between — a run's reaper moving its card into
review/, a move on the page, a pull — used to raise FileNotFoundError out
of the reading, and every caller with it: a scheduler pass recorded the
launch it was authorizing as an unknown outcome. These cases move a card
at exactly that point and hold the reading to the board as it is after
the move: every card once, in the stage it is in.

The journey-level reproduction, with the real scheduler, launch path and
reaper, is `tests.test_orchestration_journey`'s
`test_a_card_moved_mid_reading_does_not_cost_the_member_its_launch`.

    python3 -m unittest tests.test_collect_consistency -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import taskfiles  # noqa: E402


def card(title: str, status: str) -> str:
    return f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n\nA card.\n"


class MovedMidReading(unittest.TestCase):

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-collect-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tasks = tmp / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
        self.addCleanup(setattr, config, "TASKS", config.TASKS)
        config.TASKS = self.tasks
        self.write("in-progress", "30-fix-the-footer.md", "30 — Fix the footer", "In Progress")
        self.write("to-do", "21-export-model.md", "21 — Export model", "To Do")
        self.write("backlog", "05-old-idea.md", "05 — Old idea", "Backlog")

    def write(self, stage: str, name: str, title: str, status: str) -> None:
        (self.tasks / stage / name).write_text(card(title, status), encoding="utf-8")

    def move_when_read(self, name: str, source: str, target: str, *, trigger: str,
                       times: int = 1) -> list:
        """Move `name` from `source` to `target` the first `times` times
        the reading reaches `trigger` (a card name in the reading's own
        listing) — before that card is read. Returns the moves made."""
        real = taskfiles.read_task
        made = []

        def read_task(path, slug):
            if Path(path).name == trigger and len(made) < times:
                here = self.tasks / source / name
                there = self.tasks / target / name
                if here.is_file():
                    here.rename(there)
                    made.append((source, target))
            return real(path, slug)
        self.addCleanup(setattr, taskfiles, "read_task", real)
        taskfiles.read_task = read_task
        return made

    def where(self, board: dict) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for stage in board["stages"]:
            for task in stage["tasks"]:
                found.setdefault(task["file"], []).append(stage["slug"])
        return found

    def test_a_card_that_leaves_between_listing_and_reading_is_read_where_it_went(self):
        # listed in in-progress/, moved to review/ just before it is read
        made = self.move_when_read("30-fix-the-footer.md", "in-progress", "review",
                                   trigger="30-fix-the-footer.md")
        board = taskfiles.collect()
        self.assertEqual(made, [("in-progress", "review")], "the move happened mid-reading")
        self.assertEqual(self.where(board), {"30-fix-the-footer.md": ["review"],
                                             "21-export-model.md": ["to-do"],
                                             "05-old-idea.md": ["backlog"]})

    def test_a_card_moved_back_after_it_was_read_is_read_where_it_is_now(self):
        # 30 is read in in-progress/, then walked back into to-do/ (already
        # read) while review/ is being read: without the second listing
        # the reading would still show it in in-progress/
        self.write("review", "40-docs.md", "40 — Docs", "Review")
        made = self.move_when_read("30-fix-the-footer.md", "in-progress", "to-do",
                                   trigger="40-docs.md")
        board = taskfiles.collect()
        self.assertEqual(made, [("in-progress", "to-do")])
        self.assertEqual(self.where(board)["30-fix-the-footer.md"], ["to-do"])

    def test_a_card_moved_forward_after_its_stage_was_read_is_read_where_it_is_now(self):
        # 21 is read in to-do/, then moved on to review/ while in-progress/
        # is being read: the listing taken first has no 21 in review/
        made = self.move_when_read("21-export-model.md", "to-do", "review",
                                   trigger="30-fix-the-footer.md")
        board = taskfiles.collect()
        self.assertEqual(made, [("to-do", "review")])
        self.assertEqual(self.where(board)["21-export-model.md"], ["review"])

    def test_a_board_that_never_holds_still_is_still_read_without_raising(self):
        # every attempt loses the card: the last one keeps what it could read
        real = taskfiles.read_task
        moves = []

        def read_task(path, slug):
            if Path(path).name == "30-fix-the-footer.md" and Path(path).is_file():
                target = "review" if slug == "in-progress" else "in-progress"
                Path(path).rename(self.tasks / target / Path(path).name)
                moves.append(target)
            return real(path, slug)
        self.addCleanup(setattr, taskfiles, "read_task", real)
        taskfiles.read_task = read_task
        board = taskfiles.collect()
        self.assertEqual(len(moves), taskfiles.COLLECT_ATTEMPTS)
        found = self.where(board)
        self.assertEqual(found["21-export-model.md"], ["to-do"])
        self.assertLessEqual(len(found.get("30-fix-the-footer.md", [])), 1)

    def test_an_undisturbed_reading_is_one_listing_and_one_read_per_card(self):
        real = taskfiles.read_task
        reads = []

        def read_task(path, slug):
            reads.append(Path(path).name)
            return real(path, slug)
        self.addCleanup(setattr, taskfiles, "read_task", real)
        taskfiles.read_task = read_task
        taskfiles.collect()
        self.assertEqual(sorted(reads), ["05-old-idea.md", "21-export-model.md",
                                         "30-fix-the-footer.md"])


if __name__ == "__main__":
    unittest.main()
