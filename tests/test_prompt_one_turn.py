"""A headless run is one non-interactive turn, and the prompts say so.

Every core prompt template carries the identical "this run is a single
non-interactive turn" block, before the task body, naming the trap that
killed card 47: backgrounding a long command and ending the turn to wait
for it. Each template then says what its own run loses when the turn ends
early — a commit, a push, a posted verdict, the report itself — and none
of it disturbs the marker lines the board parses out of the same output.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROMPTS = REPO / "manager" / "core" / "prompts"
TEMPLATES = ("work.md", "review.md", "review-pr.md", "act-pr.md")

# The shared block runs from the first line to the last; both ends are
# fixed so a template that paraphrases either one fails here.
START = "**This run is a single non-interactive turn.**"
END = "or say plainly in your report that it is not done."

BODY = "--- TASK ---"


def _text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _block(name: str) -> str:
    text = _text(name)
    start = text.find(START)
    if start < 0:
        raise AssertionError(f"{name} never says the run is a single turn")
    end = text.find(END, start)
    if end < 0:
        raise AssertionError(f"{name} lost the end of the one-turn block")
    return text[start:end + len(END)]


class SharedBlock(unittest.TestCase):
    def test_identical_across_all_templates(self):
        reference = _block(TEMPLATES[0])
        for name in TEMPLATES[1:]:
            self.assertEqual(_block(name), reference,
                             f"{name} drifted from work.md's one-turn block")

    def test_it_states_the_shape_of_the_run(self):
        block = " ".join(_block("work.md").split())
        self.assertIn("no second turn", block)
        self.assertIn("the process exits", block)

    def test_it_names_the_trap(self):
        block = " ".join(_block("work.md").split())
        self.assertIn("Do not start something in the background and end "
                      "your turn to wait for it", block)
        self.assertIn("no monitor", block)
        self.assertIn("run it in the foreground", block)
        self.assertIn("Do not promise to come back to something", block)

    def test_it_comes_before_the_task_body(self):
        """A brief read after the task is the one the agent skims."""
        for name in TEMPLATES:
            text = _text(name)
            self.assertLess(text.find(START), text.find(BODY),
                            f"{name} says it after the task body")

    def test_block_survives_str_format(self):
        """Prompts are filled via str.format, so a literal brace here
        would break every launch."""
        block = _block("work.md")
        self.assertNotIn("{", block)
        self.assertNotIn("}", block)


class WhatEachRunLoses(unittest.TestCase):
    """The block is the reason; each template gives the instruction that
    follows from it for the run it drives."""

    def test_work_commits_around_long_commands(self):
        text = " ".join(_text("work.md").split())
        self.assertIn("commit early and commit often", text)
        self.assertIn("Commit before you start anything long-running", text)
        self.assertIn("commit again after it", text)

    def test_act_pr_commits_and_pushes(self):
        text = " ".join(_text("act-pr.md").split())
        self.assertIn("commit early and push often", text)
        self.assertIn("Commit before you start anything long-running", text)
        self.assertIn("never pushed never reaches the PR", text)

    def test_review_pr_posts_its_verdict_inside_the_turn(self):
        text = " ".join(_text("review-pr.md").split())
        self.assertIn("post your verdict to GitHub during the turn", text)

    def test_review_writes_its_report_inside_the_turn(self):
        text = " ".join(_text("review.md").split())
        self.assertIn("Your report is the only thing this run leaves behind",
                      text)


class MarkersUndisturbed(unittest.TestCase):
    """agents.py parses the marker lines out of the same output; the new
    prose must not compete for 'the first line'."""

    MARKERS = {
        "work.md": "NOT READY: <one-line reason>",
        "act-pr.md": "ADDRESSED: <one line on what changed>",
        "review-pr.md": "PR REVIEW: <APPROVE | REQUEST CHANGES>",
        "review.md": "RELEVANCE REVIEW: <Still relevant | Partly done | "
                     "Already done | Needs rewrite>",
    }

    def test_markers_still_follow_the_new_block(self):
        for name, marker in self.MARKERS.items():
            text = _text(name)
            self.assertIn(marker, text, f"{name} lost its marker line")
            self.assertLess(text.find(START), text.find(marker),
                            f"{name} now states its marker before the block")

    def test_not_ready_keeps_its_place_in_work(self):
        """The NOT READY instruction is still the only thing in work.md
        claiming a reply's first line, and it still sits after the task."""
        text = _text("work.md")
        self.assertEqual(text.count("FIRST line"), 1)
        self.assertLess(text.find("--- END TASK ---"), text.find("FIRST line"))
        self.assertNotIn("FIRST line", _block("work.md"))
        self.assertNotIn("NOT READY", _block("work.md"))


if __name__ == "__main__":
    unittest.main()
