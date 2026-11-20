"""What a card's Overview leads with (task 149): the brief's outline.

The Overview opens with what the work is and why, quoted from the brief as
it reads now — `taskfiles.brief_outline`, served with the card's text by
`GET /api/card` — and with the last result a run recorded, by the report's
own first paragraph (`acceptance.reports(...)[i]["headline"]`). Neither is
persisted and neither is generated, so these tests hold the contract:

- an excerpt is the brief's own words, labelled with the section it came
  from, clipped at a sentence and never a paraphrase of the title;
- a changed brief is a changed outline (and a new revision) on the next
  read, while a report appended to the history changes nothing about it —
  an old work report never becomes the requirement;
- a brief with nothing to quote reads `none`, and nothing is invented;
- reading writes nothing.

The fixtures under `tests/fixtures/task-information/` are the cards the
walkthrough (`develop/design/task-information/walkthrough.md`) and the
browser case (`verify/browser_cases/case_task_information.py`) use, and
`readings.json` is what the page-side tests in
`tests/node/task_information.test.mjs` are fed: it is checked here to be
exactly what the server computes from those fixtures now.

    python3 -m unittest tests.test_task_information -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "manager" / "core"))

import acceptance  # noqa: E402
import config  # noqa: E402
import taskfiles  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "task-information"
READINGS = FIXTURES / "readings.json"
STAGES = {"Backlog": "backlog", "To Do": "to-do", "In Progress": "in-progress",
          "Review": "review", "Done": "done"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def stage_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("**Status:**"):
            return STAGES[line.split(":**", 1)[1].strip()]
    raise AssertionError("a fixture names its status")


def build_readings() -> dict:
    """What the page is sent for each fixture: the card as `/api/card`
    serves it (less its mtime) and its acceptance reading's reports and
    counts — the inputs the Overview draws from."""
    out = {}
    for path in sorted(FIXTURES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        acc = acceptance.read_text(text, path.name)
        out[path.name] = {
            "file": path.name, "stage": stage_of(text), "rev": taskfiles.revision(text),
            "body": text, "parts": taskfiles.part_offsets(text),
            "outline": taskfiles.brief_outline(text),
            "acc": {**{k: acc[k] for k in ("tracked", "criteria", "untracked", "removed", "counts",
                                           "rounds", "relevance", "findings",
                                           "findingCounts", "states")},
                    "reports": acceptance.reports(text)},
        }
    return out


class Outline(unittest.TestCase):

    def test_a_long_legacy_card_is_explained_by_its_own_words(self):
        text = fixture("164-legacy-long.md")
        o = taskfiles.brief_outline(text)
        self.assertEqual(o["state"], "full")
        self.assertTrue(o["lead"]["text"].startswith("Make first installation layout-aware"))
        self.assertEqual(o["lead"]["from"], "the brief's opening")
        # the summary explains the work rather than repeating the title
        self.assertNotIn("Initialize work directories only at", o["lead"]["text"])
        self.assertEqual(o["problem"]["from"], "Context")
        self.assertTrue(o["problem"]["clipped"], "a long Context says it continues")
        self.assertEqual(o["scope"]["kind"], "headings")
        self.assertEqual(o["scope"]["items"], ["Installation-mode-aware initialization",
                                               "Preservation, reinstall and mode changes",
                                               "Legacy installations"])
        self.assertEqual(o["boundaries"]["from"], "Out of scope")
        # every excerpt is the brief's own words, verbatim but for markup
        brief = " ".join(taskfiles.plain_inline(taskfiles.card_parts(text)["brief"]).split())
        for part in ("lead", "problem", "boundaries"):
            self.assertIn(o[part]["text"].rstrip("…"), brief, part)

    def test_the_history_is_never_read_as_the_requirement(self):
        text = fixture("164-legacy-long.md")
        blob = json.dumps(taskfiles.brief_outline(text))
        for history in ("Committed on task/164", "twelve failures", "approving this change",
                        "Read the bench-backend log"):
            self.assertNotIn(history, blob)

    def test_a_changed_brief_is_a_changed_outline_and_a_new_revision(self):
        text = fixture("202-active-run.md")
        before = taskfiles.brief_outline(text)
        edited = text.replace("Add an \"Export CSV\" action", "Add a \"Download CSV\" action")
        after = taskfiles.brief_outline(edited)
        self.assertNotEqual(before["rev"], after["rev"])
        self.assertTrue(after["lead"]["text"].startswith("Add a \"Download CSV\" action"))
        # a report appended to the history is not an edit of the brief
        reported = text + ("\n---\n\n## Work report — 2026-11-19 09:00 (Wren)\n\n"
                           "The export now includes hidden columns too.\n")
        self.assertEqual(taskfiles.brief_outline(reported), before)

    def test_a_brief_with_nothing_to_quote_says_none_and_invents_nothing(self):
        o = taskfiles.brief_outline(fixture("205-missing-summary.md"))
        self.assertEqual(o["state"], "none")
        self.assertIsNone(o["lead"])
        self.assertIsNone(o["problem"])
        self.assertIsNone(o["scope"])
        self.assertEqual(o["missing"], ["lead", "problem", "scope", "boundaries"])
        empty = taskfiles.brief_outline("# 9 — Title only\n\n**Status:** Backlog\n")
        self.assertEqual(empty["state"], "none")
        self.assertEqual(empty["sections"], [])

    def test_a_rough_backlog_card_is_its_lead_and_says_what_is_absent(self):
        o = taskfiles.brief_outline(fixture("201-backlog-rough.md"))
        self.assertEqual(o["state"], "partial")
        self.assertIn("pin a card", o["lead"]["text"])
        self.assertIn("scope", o["missing"])

    def test_open_questions_are_counted_and_none_is_not_a_question(self):
        o = taskfiles.brief_outline(fixture("203-blocked-questions.md"))
        self.assertEqual(o["questions"], {"count": 2, "from": "Open questions"})
        none = taskfiles.brief_outline("# 9 — x\n\n**Status:** Backlog\n\nDo it.\n\n"
                                       "## Open questions\n\n- None.\n")
        self.assertIsNone(none["questions"])

    def test_bullets_by_their_first_clause_and_the_count_of_the_rest(self):
        items = "\n".join(f"- Item {n}: the rest of item {n}." for n in range(9))
        o = taskfiles.brief_outline(f"# 9 — x\n\n**Status:** Backlog\n\nDo it.\n\n## Scope\n\n{items}\n")
        self.assertEqual(o["scope"]["items"][0], "Item 0")
        self.assertEqual(len(o["scope"]["items"]), taskfiles.OUTLINE_ITEMS)
        self.assertEqual(o["scope"]["more"], 9 - taskfiles.OUTLINE_ITEMS)

    def test_code_comments_and_quotes_are_not_prose_and_excerpts_are_bounded(self):
        long = "This sentence is about the work and it goes on for a while. " * 30
        text = ("# 9 — x\n\n**Status:** Backlog\n\n<!-- bench:entry {\"v\": 1} -->\n\n"
                "> a quoted line\n\n```\n## Not a heading\nprint('x')\n```\n\n"
                f"{long}\n\n## Context\n\n| a | b |\n|---|---|\n\nThe real problem.\n")
        o = taskfiles.brief_outline(text)
        self.assertEqual(o["sections"], ["Context"], "a heading in a fence is not a section")
        self.assertTrue(o["lead"]["text"].startswith("This sentence is about the work"))
        self.assertLessEqual(len(o["lead"]["text"]), taskfiles.OUTLINE_LEAD_CHARS)
        self.assertTrue(o["lead"]["clipped"])
        self.assertTrue(o["lead"]["text"].endswith("."), "clipped at a sentence, not mid-word")
        self.assertEqual(o["problem"]["text"], "The real problem.")
        self.assertNotIn("bench:", json.dumps(o))

    def test_the_seed_templates_inline_out_of_scope_bounds_the_scope(self):
        seed = (ROOT / "manager" / "core" / "seed" / "task-template.md").read_text(encoding="utf-8")
        o = taskfiles.brief_outline(seed)
        self.assertEqual(o["scope"]["items"], ["First piece", "Second piece"])
        self.assertEqual(o["boundaries"]["text"], "Not this, even though it is nearby")
        self.assertNotIn("Copy this file", json.dumps(o), "an HTML comment is not prose")
        self.assertIsNone(o["questions"], "`- None.` is not an open question")
        inline = taskfiles.brief_outline(
            "# 9 — x\n\n**Status:** Backlog\n\nDo it.\n\n## What to build\n\n- A\n\n"
            "**Out of scope** — exports and imports.\n\nA closing note.\n")
        self.assertEqual(inline["scope"]["items"], ["A"])
        self.assertEqual(inline["boundaries"]["text"], "exports and imports.")

    def test_identifiers_in_code_spans_are_quoted_as_written(self):
        # review of 3bc5597: emphasis stripping ran inside code spans, so a
        # summary labelled a quote changed the identifiers it quoted
        p = taskfiles.plain_inline
        self.assertEqual(p("Edit `__init__.py` first."), "Edit __init__.py first.")
        self.assertEqual(p("Read `BOARD_AGENT_*` keys."), "Read BOARD_AGENT_* keys.")
        self.assertEqual(p("Run `tests/**/*.py` now."), "Run tests/**/*.py now.")
        self.assertEqual(p("A ``code with ` tick`` here."), "A code with ` tick here.")
        self.assertEqual(p("[the `__x__` doc](a.md) and **bold**"), "the __x__ doc and bold")
        self.assertEqual(p("an `<!-- bench:x -->` example"), "an <!-- bench:x --> example")
        self.assertEqual(p("**loud** and *soft* <!-- hidden -->"), "loud and soft")
        self.assertEqual(p("an `unclosed span with __x__"), "an unclosed span with x")
        o = taskfiles.brief_outline(
            "# 9 — x\n\n**Status:** Backlog\n\nRename `__init__.py` and read `BOARD_AGENT_*`.\n\n"
            "## What to build\n\n- Cover `tests/**/*.py`\n")
        self.assertEqual(o["lead"]["text"], "Rename __init__.py and read BOARD_AGENT_*.")
        self.assertEqual(o["scope"]["items"], ["Cover tests/**/*.py"])

    def test_a_first_sentence_longer_than_the_cap_says_it_was_cut(self):
        text, clipped = taskfiles.clip_sentences("word " * 200, 50)
        self.assertTrue(clipped)
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(len(text), 51)


class ReportHeadline(unittest.TestCase):

    def test_the_last_result_is_the_reports_own_first_paragraph(self):
        reports = acceptance.reports(fixture("164-legacy-long.md"))
        work = [r for r in reports if r["kind"] == "Work report"][-1]
        self.assertTrue(work["headline"]["text"].startswith("Committed on task/164"))
        review = [r for r in reports if r.get("reviewType") == "pr"][-1]
        # a marker line is not prose
        self.assertTrue(review["headline"]["text"].startswith("I'm approving this change"))

    def test_a_report_with_no_prose_has_no_headline(self):
        text = (fixture("206-unavailable-evidence.md") +
                "\n---\n\n## Work report — 2026-11-19 09:00 (Wren)\n\n```\nlog only\n```\n")
        [report] = acceptance.reports(text)
        self.assertIsNone(report["headline"])


class Served(unittest.TestCase):
    """`GET /api/card` carries the outline of the very text it serves, and
    reading it writes nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = config.TASKS
        config.TASKS = self.tmp / "tasks"
        for stage in config.STAGE_DIRS:
            (config.TASKS / stage).mkdir(parents=True)

    def tearDown(self):
        config.TASKS = self.saved
        shutil.rmtree(self.tmp)

    def test_the_card_text_and_its_outline_are_one_reading(self):
        name = "164-legacy-long.md"
        path = config.TASKS / "done" / name
        path.write_text(fixture(name), encoding="utf-8")
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.tmp.rglob("*") if p.is_file()}
        found = taskfiles.card_detail(name, "done")
        self.assertEqual(found["outline"], taskfiles.brief_outline(found["body"]))
        self.assertEqual(found["outline"]["v"], taskfiles.OUTLINE_VERSION)
        after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.tmp.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "reading a card wrote something")
        # an edit of the brief is in the next reading, under a new revision
        path.write_text(fixture(name).replace("Make first installation layout-aware",
                                              "Make every installation layout-aware"), encoding="utf-8")
        again = taskfiles.card_detail(name, "done")
        self.assertNotEqual(again["outline"]["rev"], found["outline"]["rev"])
        self.assertIn("Make every installation", again["outline"]["lead"]["text"])
        self.assertNotEqual(again["rev"], found["rev"])


class Readings(unittest.TestCase):

    def test_the_page_tests_are_fed_what_the_server_computes_now(self):
        if os.environ.get("BENCH_WRITE_READINGS") == "1":
            READINGS.write_text(json.dumps(build_readings(), indent=1, sort_keys=True,
                                           ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(json.loads(READINGS.read_text(encoding="utf-8")), build_readings(),
                         "tests/fixtures/task-information/readings.json is out of date: "
                         "BENCH_WRITE_READINGS=1 python3 -m unittest tests.test_task_information")

    def test_the_wrapped_head_cards_carry_the_servers_own_split(self):
        # the page's wrapped-field test (tests/node/task_information.test.mjs)
        # is fed these offsets, so they must be the split the server sends:
        # a value wrapped onto a second line ends the server's head there
        cards = json.loads((FIXTURES / "wrapped-head.json").read_text(encoding="utf-8"))
        for name, card in cards.items():
            with self.subTest(name):
                self.assertEqual(card["parts"], taskfiles.part_offsets(card["body"]))
        wrapped = taskfiles.card_parts(cards["wrapped"]["body"])
        self.assertTrue(wrapped["brief"].startswith("onto a second line\n**Type:**"),
                        "the continuation and the fields after it start the brief")


if __name__ == "__main__":
    unittest.main()
