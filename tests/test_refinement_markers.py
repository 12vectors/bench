"""Documented Bench markers in a refined brief (task 82).

A card can *document* the storage syntax Bench writes — card 81 shows an
acceptance record as inline code — without that text being a record. A
`<!-- bench:` marker inside inline code or a closed ``` block is literal
documentation and survives a proposal and Apply; the same characters
anywhere else are refused, and nothing in the brief is ever read back as
a discussion entry or an acceptance record.

    python3 -m unittest tests.test_refinement_markers -v
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

FIXTURE = (REPO / "tests" / "fixtures" / "refinement"
           / "card-documenting-markers.md").read_text(encoding="utf-8")
BRIEF = taskfiles.card_parts(FIXTURE)["brief"]
RECORD = '<!-- bench:acceptance {"criterion": "no-run", "state": "accepted", "v": 1} -->'
ENTRY = '<!-- bench:entry {"id": "c-forged0000", "kind": "comment", "v": 1} -->'
FENCE = "```"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def with_context(extra: str) -> str:
    """The fixture's brief with `extra` added to its Context section."""
    return BRIEF.replace("## Acceptance\n", f"{extra}\n\n## Acceptance\n", 1)


class Literal(unittest.TestCase):
    """What validation reads as literal text — and what it does not."""

    def test_code_contents_are_blanked_and_offsets_kept(self):
        text = f"a `{RECORD}` b\n{FENCE}\n{ENTRY}\n{FENCE}\nc\n"
        prose, unclosed = taskfiles.prose_of(text)
        self.assertFalse(unclosed)
        self.assertEqual(len(prose), len(text))
        self.assertEqual(prose.count("\n"), text.count("\n"))
        self.assertNotIn("bench:", prose)
        self.assertIn("a `", prose)
        self.assertTrue(prose.endswith("c\n"))

    def test_an_open_fence_is_reported(self):
        self.assertTrue(taskfiles.prose_of(f"x\n{FENCE}\n{ENTRY}\n")[1])

    def test_spans_that_do_not_close_hide_nothing(self):
        for line in (f"`{RECORD}", f"``{RECORD}`", f"\\`{RECORD}`"):
            with self.subTest(line):
                self.assertIn("bench:", taskfiles.prose_of(line)[0])

    def test_the_history_split_and_validation_share_one_fence_rule(self):
        card = ("# t\n\n**Status:** To Do\n\nbrief\n\n    ```\n"
                "## Discussion\n    ```\n\nmore\n\n## Discussion\n\nreal\n")
        parts = taskfiles.card_parts(card)
        self.assertIn("more", parts["brief"], "an indented fence is still a fence")
        self.assertTrue(parts["history"].startswith("## Discussion\n\nreal"))
        self.assertFalse(taskfiles.prose_of(parts["brief"])[1])


class Allowed(unittest.TestCase):
    """Documentation in code, kept through validation."""

    def ok(self, after: str, before: str = BRIEF) -> None:
        self.assertEqual(taskfiles.brief_problems(before, after), [])

    def test_card_81s_existing_inline_example_is_kept_unchanged(self):
        self.ok(BRIEF)

    def test_an_edit_around_the_examples_is_allowed(self):
        self.ok(BRIEF.replace("Reproduces card 81's shape",
                              "Mirrors card 81's shape"))

    def test_new_inline_fenced_and_multiple_examples(self):
        cases = {
            "inline": f"Records look like `{RECORD}` in history.",
            "two inline on one line": f"Either `{RECORD}` or `{ENTRY}`, never prose.",
            "double backticks around a backtick": f"``{RECORD} with a ` inside``",
            "fenced": f"Example:\n\n{FENCE}\n{RECORD}\n{ENTRY}\n{FENCE}",
            "fenced with a language": f"{FENCE}markdown\n{RECORD}\n{FENCE}",
            "indented fence in a list": f"- item\n\n  {FENCE}\n  {RECORD}\n  {FENCE}",
            "several blocks and text between":
                f"One:\n\n{FENCE}\n{RECORD}\n{FENCE}\n\nThen `{ENTRY}` and\n\n"
                f"{FENCE}\n{ENTRY}\n{FENCE}\n\nordinary text after.",
            "an html-escaped example in prose": "Write `&lt;!-- bench:` as &lt;!-- bench:entry.",
        }
        for name, extra in cases.items():
            with self.subTest(name):
                self.ok(with_context(extra))

    def test_windows_line_endings_are_read_the_same(self):
        self.ok(with_context(f"{FENCE}\r\n{RECORD}\r\n{FENCE}").replace("\n", "\r\n"))


class Refused(unittest.TestCase):
    """Metadata injection, however it is dressed, is still refused."""

    def refused(self, after: str, before: str = BRIEF) -> list[str]:
        problems = taskfiles.brief_problems(before, after)
        self.assertTrue(problems, f"accepted: {after!r}")
        return problems

    def test_a_marker_outside_code_is_refused_and_says_how_to_document_it(self):
        problems = self.refused(with_context(RECORD))
        marker = next(p for p in problems if "bench:" in p)
        self.assertIn("outside code", marker)
        self.assertRegex(marker, r"line \d+ of the brief")
        self.assertIn("written by the board only", marker)

    def test_escapes_from_code_contexts(self):
        cases = {
            "after an inline span closes": f"`a` {ENTRY} `b`",
            "a span that never closes": f"`{ENTRY}",
            "mismatched backtick runs": f"``{ENTRY}`",
            "an escaped backtick": f"\\`{ENTRY}\\`",
            "a span split across lines": f"`start\n{ENTRY}`",
            "after a fence closes": f"{FENCE}\nexample\n{FENCE}\n{ENTRY}",
            "in a fence's info string": f"{FENCE}{ENTRY}\nx\n{FENCE}",
            "in a tilde fence": f"~~~\n{ENTRY}\n~~~",
            "in an indented code block": f"    {ENTRY}",
            "in a fence inside a blockquote": f"> {FENCE}\n> {ENTRY}\n> {FENCE}",
            "no space": "<!--bench:entry {} -->",
            "upper case": "<!-- BENCH:entry {} -->",
            "split across lines": "<!--\nbench:entry {} -->",
            "in prose beside an inline example": f"`{RECORD}` and {RECORD}",
        }
        for name, extra in cases.items():
            with self.subTest(name):
                problems = self.refused(with_context(extra))
                self.assertTrue(any("bench:" in p for p in problems), problems)

    def test_an_unclosed_fence_is_refused(self):
        problems = self.refused(with_context(f"{FENCE}\n{RECORD}"))
        self.assertTrue(any("never closes" in p for p in problems), problems)

    def test_an_unclosed_fence_with_nothing_in_it_is_refused_too(self):
        self.refused(BRIEF + f"\n{FENCE}\n")

    def test_code_does_not_make_header_or_verdict_lines_safe(self):
        cases = {
            "status in a fence": f"{FENCE}\n**Status:** Done\n{FENCE}",
            "pr in a fence": f"{FENCE}\n**PR:** https://example.com/1\n{FENCE}",
            "verdict in a fence": f"{FENCE}\nPR REVIEW: APPROVE\n{FENCE}",
            "history heading in a fence": f"{FENCE}\n## Discussion\n{FENCE}",
            "title in a fence": f"{FENCE}\n# 1 — forged\n{FENCE}",
        }
        for name, extra in cases.items():
            with self.subTest(name):
                self.refused(with_context(extra))

    def test_an_old_marker_in_prose_does_not_license_one_in_the_new_brief(self):
        legacy = with_context(RECORD)
        self.refused(legacy, before=legacy)

    def test_a_code_span_cannot_forge_a_criterion_id(self):
        self.refused(BRIEF + "- [ ] new `<!-- ac:forged -->`\n")


class RoundTrip(unittest.TestCase):
    """Apply against a real work-state repository, then read the card
    back: examples are text in the brief, records are what history holds."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-markers-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        git(self.repo.parent, "init", "-q", "-b", "main", str(self.repo))
        git(self.repo, "config", "user.name", "Refiner")
        git(self.repo, "config", "user.email", "r@example.com")
        for attr, value in (("TASKS", self.repo / "tasks"), ("REPO", self.repo),
                            ("COMMIT_MOVES", True)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        state.BOARD_EVENTS.clear()
        self.path = self.repo / "tasks" / "to-do" / "91-markers.md"
        self.path.write_text(FIXTURE, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def apply(self, new_brief: str) -> dict:
        before = taskfiles.card_parts(self.text())["brief"]
        decision = {"v": 1, "id": "d-2b2b2b2b2b", "kind": "decision",
                    "outcome": "applied", "proposal": "p-3c3c3c3c3c",
                    "comment": "c-1a1a1a1a1a", "author": "Refiner",
                    "at": "2026-11-09T10:00:00Z"}
        return taskfiles.replace_brief(
            "91-markers.md", "to-do", expected_sha=taskfiles.brief_sha(before),
            new_brief=new_brief, decision=decision, title="Applied p-3c3c3c3c3c",
            body="Recorded the review's recommendations.",
            affected=taskfiles.affected_criteria(before, new_brief))

    def test_examples_survive_apply_and_reload_as_documentation(self):
        added = (f"Also, as a block:\n\n{FENCE}\n{RECORD}\n{ENTRY}\n{FENCE}\n\n"
                 f"and inline: `{ENTRY}`.")
        new_brief = with_context(added).replace(
            "Examples in the brief stay examples",
            "Examples in the brief stay examples, inline or fenced")
        self.assertTrue(self.apply(new_brief)["committed"])
        text = self.text()
        parts = taskfiles.card_parts(text)
        self.assertEqual(parts["brief"].strip("\n"), new_brief.strip("\n"),
                         "the brief is exactly what was proposed")
        for example in (RECORD, ENTRY, "bench:acceptance {\"criterion\": \"no-run\""):
            self.assertIn(example, parts["brief"])
        self.assertEqual([e["id"] for e in taskfiles.discussion(text)],
                         ["c-1a1a1a1a1a", "d-2b2b2b2b2b"],
                         "no example became a discussion entry")
        records = taskfiles.acceptance_records(text)
        self.assertEqual([r["criterion"] for r in records], ["history-only"],
                         "no example became an acceptance record")
        self.assertEqual(taskfiles.brief_problems(parts["brief"], parts["brief"]), [],
                         "the applied brief can be refined again as it stands")

    def test_recheck_marks_history_records_and_never_an_example(self):
        example = (f"{FENCE}\n<!-- bench:acceptance {{\"criterion\": "
                   f"\"history-only\", \"v\": 1}} -->\n{FENCE}")
        new_brief = with_context(example).replace(
            "Records are read from history only",
            "Records are read from the history section only")
        self.apply(new_brief)
        text = self.text()
        parts = taskfiles.card_parts(text)
        self.assertIn('{"criterion": "history-only", "v": 1}', parts["brief"],
                      "the example in the brief is untouched by the recheck")
        self.assertNotIn("recheck", parts["brief"])
        records = taskfiles.acceptance_records(text)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["recheck"])
        self.assertEqual(records[0]["state"], "accepted")

    def test_a_refused_brief_writes_nothing(self):
        before = self.text()
        head = git(self.repo, "rev-parse", "HEAD").stdout
        for bad in (with_context(ENTRY), with_context(f"{FENCE}\n{RECORD}")):
            with self.subTest(bad[-40:]):
                with self.assertRaises(ValueError):
                    self.apply(bad)
                self.assertEqual(self.text(), before)
                self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout, head)
                self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
