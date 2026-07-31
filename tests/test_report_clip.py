"""What the record keeps of a long closing report (task 42).

The cap used to keep the *last* 3,000 characters, so a report longer than
that lost exactly the sentence the prompt contract puts first — the one
saying what happened — and the task file began mid-word. The clip now
keeps the head, keeps the tail, cuts the middle on line boundaries, and
says so in words while naming the log that still holds the whole thing.

The regression fixture is card 32's report, `tests/fixtures/
32-work-report.log` (3,619 bytes, the size the live one was). Its run's
log is gitignored state and did not survive into this worktree, so the
fixture is that report reassembled: the 3,000 characters the old clip
kept are verbatim from `tasks/done/32-serve-bench-12vectors-com-from-a-
worker.md`, and the 619 the old clip discarded are rebuilt from the
quotation in card 42, which is where they were preserved. Its first
sentence and its first two action items are the bytes this card exists
to stop losing.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import github  # noqa: E402
import reports  # noqa: E402
import state  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "32-work-report.log"
OLD_CAP = 3000   # what the bug shipped with, and what card 32 met

# A report that is unmistakably longer than any cap under test, with a
# contract-shaped head and tail: the headline first, the pointer last.
HEADLINE = "Work is committed on `task/x` and the suite is green, but nothing is deployed."
CLOSER = "**Review first:** `manager/core/reports.py`, then its tests."


def long_report(paragraphs: int = 400) -> str:
    middle = "\n\n".join(f"Paragraph {i} of the narrative, which is the part "
                         f"a clip may drop without costing the reader a decision."
                         for i in range(paragraphs))
    return f"{HEADLINE}\n\n{middle}\n\n{CLOSER}"


class ShortReportsAreUntouched(unittest.TestCase):
    def test_passed_through_byte_for_byte(self):
        text = "ADDRESSED: fixed the two comments.\n\nBoth were naming.\n"
        self.assertEqual(reports.report(text), text.strip())

    def test_no_elision_line(self):
        self.assertNotIn("were cut here", reports.report("A one-line report."))

    def test_exactly_the_cap_is_whole(self):
        text = "x" * 40 + "\n" + "y" * 59   # 100 characters
        self.assertEqual(reports.report(text, cap=100), text)

    def test_hook_noise_still_stripped(self):
        text = "PostToolUse hook failed with status 1\nThe actual report.\n"
        self.assertEqual(reports.report(text), "The actual report.")


class ClippedReportsKeepTheirHead(unittest.TestCase):
    def setUp(self):
        self.clipped = reports.report(long_report(), log_path="/tmp/logs/09-x-1203.log",
                                      cap=OLD_CAP)

    def test_begins_with_the_reports_own_first_line(self):
        self.assertEqual(self.clipped.splitlines()[0], HEADLINE)

    def test_keeps_the_tail_too(self):
        self.assertTrue(self.clipped.rstrip().endswith(CLOSER), self.clipped[-200:])

    def test_says_in_words_that_it_was_clipped(self):
        self.assertIn("were cut here", self.clipped)
        self.assertRegex(self.clipped, r"… \d+ characters of this report were cut here")

    def test_names_the_log_holding_the_whole_thing(self):
        self.assertIn("09-x-1203.log", self.clipped)

    def test_the_elision_is_on_its_own_line(self):
        line = next(l for l in self.clipped.splitlines() if "were cut here" in l)
        self.assertTrue(line.startswith("… "), line)
        self.assertTrue(line.endswith("."), line)

    def test_stays_within_the_cap(self):
        self.assertLessEqual(len(self.clipped), OLD_CAP)

    def test_cuts_only_on_line_boundaries(self):
        source = set(long_report().splitlines())
        for line in self.clipped.splitlines():
            if "were cut here" in line:
                continue
            self.assertIn(line, source, f"line was cut mid-line: {line!r}")

    def test_nothing_is_kept_twice(self):
        for line in self.clipped.splitlines():
            if line.strip() and "were cut here" not in line:
                self.assertEqual(self.clipped.count(line), 1,
                                 f"duplicated into the record: {line!r}")

    def test_without_a_log_path_it_still_points_at_the_log_directory(self):
        clipped = reports.report(long_report(), cap=OLD_CAP)
        self.assertIn("manager/local/state/agent/logs/", clipped)


class Card32Regression(unittest.TestCase):
    """The live case: 3,619 bytes met a 3,000-character cap and the record
    lost the sentence saying nothing had been deployed."""

    def setUp(self):
        self.text = FIXTURE.read_text(encoding="utf-8")

    def test_the_fixture_is_the_size_the_report_was(self):
        self.assertEqual(len(self.text.encode("utf-8")), 3619)

    def test_it_opens_with_the_state_of_the_work(self):
        clipped = reports.report(self.text, log_path="/tmp/logs/32-…-113412.log",
                                 cap=OLD_CAP)
        self.assertTrue(clipped.startswith("Work is committed on"), clipped[:120])
        self.assertIn("nothing has been deployed", clipped)

    def test_it_retains_action_items_one_and_two(self):
        clipped = reports.report(self.text, cap=OLD_CAP)
        self.assertIn("**Deploy it**", clipped)
        self.assertIn("acceptance boxes I could not reach", clipped)

    def test_it_keeps_the_review_first_pointer(self):
        clipped = reports.report(self.text, cap=OLD_CAP)
        self.assertIn("**Review first:**", clipped)

    def test_the_old_clip_is_what_lost_it(self):
        """The bug, stated as a test: the tail slice this replaces drops
        the headline and starts mid-word."""
        old = self.text.strip()[-OLD_CAP:]
        self.assertNotIn("nothing has been deployed", old)
        self.assertTrue(old.startswith('four"'), old[:40])

    def test_the_shipped_cap_keeps_this_report_whole(self):
        self.assertEqual(reports.report(self.text), self.text.strip())


class MarkersSurviveTheClip(unittest.TestCase):
    """The board parses these out of the report it kept, so keeping the
    head is what keeps them — all three sit on the first line."""

    def clipped(self, marker: str) -> str:
        return reports.report(f"{marker}\n\n{long_report()}", cap=OLD_CAP)

    def test_not_ready(self):
        clipped = self.clipped("NOT READY: the deploy target is still undecided")
        match = re.search(r"^NOT READY:\s*(.*)$", clipped, re.MULTILINE)
        self.assertEqual(match.group(1), "the deploy target is still undecided")

    def test_pr_review(self):
        clipped = self.clipped("PR REVIEW: REQUEST CHANGES")
        self.assertRegex(clipped, r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)")

    def test_addressed(self):
        self.assertTrue(self.clipped("ADDRESSED: renamed the helper")
                        .startswith("ADDRESSED: renamed the helper"))

    def test_relevance_verdict_is_still_the_first_line(self):
        clipped = self.clipped("RELEVANCE REVIEW: Still relevant")
        self.assertEqual(clipped.splitlines()[0], "RELEVANCE REVIEW: Still relevant")


class OneEnormousLine(unittest.TestCase):
    """A run whose whole output is a single line has no boundary to cut
    on. It still has to say something — and still must not end mid-word."""

    def setUp(self):
        self.words = " ".join(f"word{i}" for i in range(4000))
        self.clipped = reports.report(self.words, cap=OLD_CAP)

    def test_produces_something_rather_than_nothing(self):
        self.assertTrue(self.clipped.startswith("word0 word1 "))
        self.assertLessEqual(len(self.clipped), OLD_CAP)

    def test_the_elision_still_explains_itself(self):
        self.assertIn("were cut here", self.clipped)

    def test_both_ends_end_on_a_whole_word(self):
        head, end = self.clipped.split("\n\n")[0], self.clipped.split("\n\n")[-1]
        self.assertIn(f" {head.split()[-1]} ", self.words)
        self.assertIn(f" {end.split()[0]} ", self.words)
        self.assertTrue(self.words.endswith(end))

    def test_a_single_token_with_no_spaces_is_still_reported(self):
        blob = "z" * 9000
        clipped = reports.report(blob, cap=500)
        self.assertTrue(clipped.startswith("zzz"))
        self.assertIn("were cut here", clipped)
        self.assertLessEqual(len(clipped), 500)


class TheWindowsMeet(unittest.TestCase):
    """Where an off-by-one would duplicate a paragraph into the permanent
    record: one character over the cap, and caps small enough that the
    head, the notice and the tail have to share almost nothing."""

    def test_one_character_over_the_cap(self):
        lines = [f"line {i} of the report" for i in range(60)]
        text = "\n".join(lines)
        clipped = reports.report(text, cap=len(text) - 1)
        kept = [l for l in clipped.splitlines() if l.strip() and "were cut here" not in l]
        self.assertEqual(len(kept), len(set(kept)), "a line landed twice")
        self.assertTrue(set(kept) <= set(lines), "a line was cut mid-line")
        self.assertIn("were cut here", clipped)

    def test_a_range_of_caps_never_duplicates_or_overflows(self):
        text = "\n".join(f"line {i} of the report" for i in range(200))
        for cap in range(200, 1200, 37):
            clipped = reports.report(text, cap=cap)
            self.assertLessEqual(len(clipped), cap, f"cap={cap}")
            body = [l for l in clipped.splitlines()
                    if l.strip() and "were cut here" not in l]
            self.assertEqual(len(body), len(set(body)), f"cap={cap} duplicated a line")

    def test_a_cap_too_small_for_the_notice_still_returns_the_head(self):
        clipped = reports.report(long_report(), cap=40)
        self.assertTrue(HEADLINE.startswith(clipped.split("\n")[0][:20]))
        self.assertLessEqual(len(clipped), 40)


class FailedRunsStillKeepTheirTail(unittest.TestCase):
    """The one deliberate exception: for a run that died, the end is the
    story. This card does not invert that."""

    def test_excerpt_is_the_end_of_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "run.log"
            log.write_text("\n".join([f"chatter {i}" for i in range(500)]
                                     + ["API Error: 500 {\"type\":\"error\"}"]),
                           encoding="utf-8")
            excerpt = agents._failure_excerpt(str(log))
        self.assertTrue(excerpt.endswith("API Error: 500 {\"type\":\"error\"}"))
        self.assertNotIn("chatter 0\n", excerpt)
        self.assertNotIn("were cut here", excerpt)


class OneClipForBothConsumers(unittest.TestCase):
    """The PR body and the task file must never tell different stories
    about the same run — same helper, same cap, same text."""

    def test_pr_body_matches_the_task_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "42-x-1203.log"
            log.write_text(long_report(), encoding="utf-8")
            record = {"id": "work-42-x-1203", "task": "42-x.md", "mode": "work",
                      "started": time.time(), "log": str(log)}
            with state.LOCK:
                state.AGENTS[record["id"]] = record
            try:
                self.assertEqual(github._agent_report("42-x.md"),
                                 agents._report_of(record))
            finally:
                with state.LOCK:
                    state.AGENTS.pop(record["id"], None)

    def test_one_documented_cap(self):
        self.assertIsInstance(reports.CAP, int)
        self.assertGreater(reports.CAP, OLD_CAP)


if __name__ == "__main__":
    unittest.main()
