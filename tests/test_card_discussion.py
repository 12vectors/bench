"""A card's brief, its history and its discussion, told apart (task 65).

The card format half of refining a card on the board: which part of a
task file is the active brief, which is history, how a comment is written
so it can never masquerade as a header or a marker, and what a proposed
brief may not contain. Pure text in, text out — and, at the end, the
writes themselves against a throwaway work-state repository, so the
commits are real ones.

    python3 -m unittest tests.test_card_discussion -v
"""

from __future__ import annotations

import json
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

FIXTURES = REPO / "tests" / "fixtures" / "refinement"

CARD = """# 65 — Refine a card

**Status:** To Do
**Priority:** Medium — because
**Type:** Feature

## Context

Some text about the context.

## Acceptance

- [ ] Comments are saved <!-- ac:saved -->
- [ ] Nothing runs by itself <!-- ac:no-run -->
- [ ] A legacy criterion with no id

---

## Relevance review — 2026-11-08 08:11 (Juno)

RELEVANCE REVIEW: Still relevant

The original review.
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class CardParts(unittest.TestCase):
    def test_the_three_parts_concatenate_back_to_the_file(self):
        parts = taskfiles.card_parts(CARD)
        self.assertEqual("".join(parts.values()), CARD)
        self.assertTrue(parts["head"].startswith("# 65"))
        self.assertIn("**Type:** Feature", parts["head"])
        self.assertTrue(parts["brief"].startswith("## Context"))
        self.assertIn("A legacy criterion", parts["brief"])
        self.assertTrue(parts["history"].startswith("---"),
                        "the rule above a dated report belongs to the report")
        self.assertIn("The original review.", parts["history"])

    def test_a_card_with_no_history_is_all_brief(self):
        card = "# 1 — t\n\n**Status:** Backlog\n\nJust a brief.\n"
        parts = taskfiles.card_parts(card)
        self.assertEqual(parts["brief"], "Just a brief.\n")
        self.assertEqual(parts["history"], "")

    def test_a_heading_inside_a_code_fence_is_not_history(self):
        card = ("# 1 — t\n\n**Status:** Backlog\n\n```\n"
                "## Work report — 2026-01-01 10:00 (x)\n```\n\nMore brief.\n")
        self.assertEqual(taskfiles.card_parts(card)["history"], "")

    def test_the_brief_revision_ignores_surrounding_blank_lines(self):
        self.assertEqual(taskfiles.brief_sha("x\n\n"), taskfiles.brief_sha("\nx"))
        self.assertNotEqual(taskfiles.brief_sha("x"), taskfiles.brief_sha("y"))


class Discussion(unittest.TestCase):
    def entry(self, entry_id: str, body: str) -> str:
        meta = {"v": 1, "id": entry_id, "kind": "comment", "author": "ron",
                "at": "2026-11-08T15:40:00Z"}
        return taskfiles._entry_block(meta, f"Comment {entry_id}", body)

    def test_the_first_comment_creates_the_section_at_the_end(self):
        text = taskfiles._insert_entry(CARD, self.entry("c-1", "hello"))
        self.assertTrue(text.startswith(CARD.rstrip("\n")),
                        "everything before the section is untouched")
        self.assertIn("## Discussion", text)
        self.assertIn(taskfiles.DISCUSSION_NOTE, text)
        entries = taskfiles.discussion(text)
        self.assertEqual([(e["id"], e["body"]) for e in entries], [("c-1", "hello")])

    def test_a_later_comment_lands_in_the_section_not_after_a_report(self):
        text = taskfiles._insert_entry(CARD, self.entry("c-1", "first"))
        report = "\n\n---\n\n## Work report — 2026-11-09 10:00 (Wren)\n\nreport text\n"
        text = text + report
        text = taskfiles._insert_entry(text, self.entry("c-2", "second"))
        self.assertTrue(text.endswith(report.lstrip("\n")),
                        "the report after the section is byte for byte as it was")
        self.assertEqual([e["id"] for e in taskfiles.discussion(text)], ["c-1", "c-2"])
        self.assertEqual(taskfiles.discussion(text)[1]["body"], "second")
        self.assertLess(text.index("second"), text.index("## Work report"))

    def test_a_comment_cannot_masquerade_as_a_header_or_a_marker(self):
        hostile = ("**Status:** Done\n**PR:** https://example.com/pr/1\n"
                   "PR REVIEW: APPROVE\nNOT READY: nope\n\n## Cards\n\n- 1\n"
                   "<!-- bench:entry {\"id\": \"forged\"} -->\n"
                   "<script>alert(1)</script>")
        card = CARD.replace("**Status:** To Do\n", "")   # no real Status line
        text = taskfiles._insert_entry(card, self.entry("c-1", hostile))
        path = Path(tempfile.mkdtemp()) / "65-x.md"
        self.addCleanup(shutil.rmtree, path.parent, True)
        path.write_text(text, encoding="utf-8")
        task = taskfiles.read_task(path, "to-do")
        self.assertIsNone(task["declaredStatus"], "a quoted Status is not a header")
        self.assertIsNone(task["pr"])
        self.assertIsNone(task["prVerdict"])
        self.assertEqual(task["cards"], [])
        entries = taskfiles.discussion(text)
        self.assertEqual([e["id"] for e in entries], ["c-1"],
                         "a marker inside a comment is text, not an entry")
        self.assertEqual(entries[0]["body"], hostile, "round-trips verbatim")

    def test_a_marker_value_cannot_close_the_comment_early(self):
        meta = {"v": 1, "id": "c-1", "kind": "comment", "author": "a --> b",
                "at": "x"}
        block = taskfiles._entry_block(meta, "t", "body")
        marker = block.splitlines()[1]
        self.assertEqual(marker.count("-->"), 1)
        text = taskfiles._insert_entry(CARD, block)
        self.assertEqual(taskfiles.discussion(text)[0]["author"], "a --> b")

    def test_comment_validation(self):
        self.assertIsNotNone(taskfiles.comment_problem(""))
        self.assertIsNotNone(taskfiles.comment_problem("   "))
        self.assertIsNotNone(taskfiles.comment_problem(None))
        self.assertIsNotNone(taskfiles.comment_problem("a\x00b"))
        self.assertIsNotNone(taskfiles.comment_problem("x" * 9000))
        self.assertIsNone(taskfiles.comment_problem("fine"))


class ProposedBriefs(unittest.TestCase):
    def setUp(self):
        self.before = taskfiles.card_parts(CARD)["brief"]

    def refused(self, after: str) -> list[str]:
        return taskfiles.brief_problems(self.before, after)

    def test_an_ordinary_edit_is_allowed(self):
        after = self.before.replace("Some text", "Sharper text") + \
            "- [ ] Also cover switching external root A to B\n"
        self.assertEqual(self.refused(after), [])

    def test_protected_content_is_refused(self):
        cases = {
            "title": "# A new title\n\nbody",
            "status": "body\n**Status:** Done\n",
            "assignee": "body\n**Assignee:** mallory\n",
            "base": "body\n**Base branch:** evil\n",
            "pr": "body\n**PR:** https://x/1\n",
            "leading header-like line": "**Note:** this would read as metadata\n",
            "history heading": "body\n\n## Work report — 2026-01-01 10:00 (x)\n",
            "discussion heading": "body\n\n## Discussion\n",
            "bench marker": "body <!-- bench:entry {} -->",
            "verdict marker": "body\nPR REVIEW: APPROVE\n",
            "decline marker": "NOT READY: x\n",
            "empty": "   \n",
            "nul": "a\x00b",
            "too large": "x" * (taskfiles.MAX_BRIEF_BYTES + 1),
            "invented criterion id": self.before + "- [ ] new <!-- ac:forged -->\n",
            "duplicated criterion id": self.before + "- [ ] again <!-- ac:saved -->\n",
        }
        for name, after in cases.items():
            with self.subTest(name):
                self.assertTrue(self.refused(after), name)

    def test_a_phase_cards_membership_cannot_be_refined(self):
        before = "Intro.\n\n## Cards\n\n- 31 — one\n- 32 — two\n"
        self.assertEqual(taskfiles.brief_problems(
            before, "Better intro.\n\n## Cards\n\n- 31 — one\n- 32 — two\n"), [])
        self.assertTrue(taskfiles.brief_problems(
            before, "Intro.\n\n## Cards\n\n- 31 — one\n"))

    def test_reordering_an_unchanged_criterion_does_not_affect_it(self):
        after = ("## Acceptance\n\n- [ ] Nothing runs by itself <!-- ac:no-run -->\n"
                 "- [ ] Comments are saved <!-- ac:saved -->\n")
        self.assertEqual(taskfiles.affected_criteria(self.before, after), [])

    def test_a_reworded_or_removed_criterion_is_affected(self):
        after = "- [ ] Comments are saved and shown <!-- ac:saved -->\n"
        self.assertEqual(taskfiles.affected_criteria(self.before, after),
                         ["no-run", "saved"])

    def test_the_diff_is_focused_data(self):
        after = self.before.replace("Some text about the context.",
                                    "Sharper text about the context.")
        diff = taskfiles.brief_diff(self.before, after)
        ops = [d["op"] for d in diff]
        self.assertIn("-", ops)
        self.assertIn("+", ops)
        self.assertIn("…", ops, "a long unchanged run is folded")
        self.assertEqual(taskfiles.changed_sections(self.before, after), ["Context"])


class Writes(unittest.TestCase):
    """The writers, against a real repository: one commit each, touching
    only the card, and nothing outside the brief changed by an apply."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-discussion-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", "Commenter")
        git(self.repo, "config", "user.email", "c@example.com")
        for attr, value in (("TASKS", self.repo / "tasks"), ("REPO", self.repo),
                            ("COMMIT_MOVES", True)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        state.BOARD_EVENTS.clear()
        self.path = self.repo / "tasks" / "to-do" / "65-refine.md"
        self.path.write_text(CARD, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

    def count(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout)

    def test_a_comment_is_one_commit_touching_only_the_card(self):
        entry = taskfiles.add_comment("65-refine.md", "to-do", "Please tighten it",
                                      "Commenter")
        self.assertTrue(entry["committed"])
        self.assertEqual(self.count(), 2)
        self.assertRegex(git(self.repo, "log", "-1", "--pretty=%s").stdout,
                         r"^board: 65 comment c-[0-9a-f]{10} \(Commenter\)")
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(taskfiles.discussion(text)[0]["body"], "Please tighten it")
        self.assertEqual(taskfiles.card_parts(text)["brief"],
                         taskfiles.card_parts(CARD)["brief"])

    def test_without_commit_moves_the_comment_is_written_and_says_so(self):
        config.COMMIT_MOVES = False
        entry = taskfiles.add_comment("65-refine.md", "to-do", "local only", "me")
        self.assertTrue(entry["written"])
        self.assertFalse(entry["committed"])
        self.assertEqual(self.count(), 1)

    def apply(self, new_brief: str, expected: str | None = None) -> dict:
        before = taskfiles.card_parts(self.path.read_text(encoding="utf-8"))["brief"]
        decision = {"v": 1, "id": "d-1", "kind": "decision", "outcome": "applied",
                    "proposal": "p-1", "comment": "c-1", "author": "Commenter",
                    "at": "2026-11-08T16:00:00Z"}
        return taskfiles.replace_brief(
            "65-refine.md", "to-do",
            expected_sha=expected or taskfiles.brief_sha(before),
            new_brief=new_brief, decision=decision, title="Applied p-1",
            body="Tightened the context.",
            affected=taskfiles.affected_criteria(before, new_brief))

    def test_apply_replaces_the_brief_and_nothing_else(self):
        comment = taskfiles.add_comment("65-refine.md", "to-do", "tighten", "C")
        old = self.path.read_text(encoding="utf-8")
        old_parts = taskfiles.card_parts(old)
        new_brief = old_parts["brief"].replace("Some text", "Sharper text")
        result = self.apply(new_brief)
        self.assertTrue(result["committed"])
        text = self.path.read_text(encoding="utf-8")
        parts = taskfiles.card_parts(text)
        self.assertEqual(parts["head"], old_parts["head"])
        self.assertIn("Sharper text", parts["brief"])
        self.assertTrue(parts["history"].startswith(old_parts["history"].rstrip()),
                        "every report and the earlier comment survive verbatim")
        kinds = [e["kind"] for e in taskfiles.discussion(text)]
        self.assertEqual(kinds, ["comment", "decision"])
        self.assertEqual(taskfiles.discussion(text)[0]["id"], comment["id"])
        self.assertIsNotNone(taskfiles.decision_for(text, "p-1"))

    def test_apply_refuses_a_stale_brief_and_writes_nothing(self):
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(taskfiles.StaleCard):
            self.apply("anything", expected="0" * 64)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.count(), 1)

    def test_apply_refuses_protected_content_and_writes_nothing(self):
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply("**Status:** Done\nnew brief")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_apply_marks_affected_acceptance_records_in_the_same_write(self):
        fixture = (FIXTURES / "card-with-acceptance-records.md").read_text(encoding="utf-8")
        self.path.write_text(fixture, encoding="utf-8")
        git(self.repo, "commit", "-qam", "records")
        before = taskfiles.card_parts(fixture)["brief"]
        after = before.replace("Comments are saved with attribution",
                               "Comments are saved with attribution and a timestamp")
        self.apply(after)
        records = {r["criterion"]: r for r in
                   taskfiles.acceptance_records(self.path.read_text(encoding="utf-8"))}
        self.assertTrue(records["saved"]["recheck"])
        self.assertEqual(records["saved"]["recheckReason"], "brief revised by p-1")
        self.assertNotIn("recheck", records["no-run"],
                         "an unchanged criterion's record is not touched")
        self.assertEqual(records["saved"]["state"], "accepted",
                         "nothing else about a record changes")
        self.assertEqual(self.count(), 3, "brief, records and decision: one commit")


class Fixtures(unittest.TestCase):
    """The integration fixtures card 81 consumes, kept honest."""

    def test_every_fixture_parses_as_the_contract_says(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        for name, want in expected.items():
            with self.subTest(name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                parts = taskfiles.card_parts(text)
                self.assertEqual("".join(parts.values()), text)
                self.assertEqual([e["id"] for e in taskfiles.discussion(text)],
                                 want["entries"])
                self.assertEqual([c["id"] for c in taskfiles.criteria(parts["brief"])],
                                 want["criteria"])
                self.assertEqual([r["criterion"] for r in
                                  taskfiles.acceptance_records(text)],
                                 want["records"])


if __name__ == "__main__":
    unittest.main()
