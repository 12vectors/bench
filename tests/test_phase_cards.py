"""A phase is a card that lists its cards (task 48).

`**Type:** Phase` plus a `## Cards` section is the whole model: the phase
card is the only authority on membership, a member's phase and position
are derived from it, and what the list does not resolve is flagged rather
than skipped. These tests run `taskfiles.collect()` over a throwaway
tasks/ directory — the same reading the board does on every request — and
then hold board.html to rendering what that reading produces.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
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
import taskfiles  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")


def card(title: str, *, kind: str | None = None, depends: str | None = None,
         cards: str | None = None, status: str = "Backlog") -> str:
    """A task file as a person would write it."""
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if depends:
        text += f"**Depends on:** {depends}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n{cards}\n"
    return text


class PhaseReadingCase(unittest.TestCase):
    """One tasks/ directory per test, read exactly as the board reads it."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-phase-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tasks = tmp / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
        self.addCleanup(setattr, config, "TASKS", config.TASKS)
        config.TASKS = self.tasks
        self.addCleanup(setattr, config, "TM_ROOT", config.TM_ROOT)
        config.TM_ROOT = tmp

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def board(self) -> dict[str, dict]:
        """Every card the board would show, keyed by filename."""
        return {task["file"]: task
                for stage in taskfiles.collect()["stages"]
                for task in stage["tasks"]}

    def members(self) -> None:
        """Three ordinary cards, out of the order a phase will run them."""
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("32-serve-it.md", card("32 — Serve it from a Worker"))
        self.write("33-landing.md", card("33 — The landing page"))


class ReadingAPhase(PhaseReadingCase):
    def test_a_phase_lists_its_members_in_the_order_it_names_them(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards=(
            "- 33 — The landing page\n"
            "- 31 — Stand up site/\n"
            "- 32 — Serve it from a Worker\n")))

        phase = self.board()["40-the-site.md"]

        self.assertTrue(phase["isPhase"])
        self.assertEqual([m["file"] for m in phase["members"]],
                         ["33-landing.md", "31-stand-up-site.md", "32-serve-it.md"],
                         "document order is run order, not the board's own order")
        self.assertEqual(phase["phaseDrift"], [])
        self.assertIsNone(phase["phase"], "a phase card is not a member of anything")

    def test_a_member_learns_its_phase_and_its_position(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards=(
            "- 31 — Stand up site/\n- 32 — Serve it from a Worker\n- 33 — The landing page\n")))

        cards = self.board()

        self.assertEqual(cards["32-serve-it.md"]["phase"],
                         {"file": "40-the-site.md", "number": "40",
                          "title": "40 — Ship the site", "index": 2, "total": 3})
        self.assertEqual(cards["31-stand-up-site.md"]["phase"]["index"], 1)
        self.assertEqual(cards["33-landing.md"]["phase"]["index"], 3)

    def test_membership_survives_the_cards_being_in_different_stages(self):
        self.write("31-stand-up-site.md", card("31 — Stand up site/", status="Done"), "done")
        self.write("32-serve-it.md", card("32 — Serve it", status="Review"), "review")
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 32 — Serve it\n"))

        cards = self.board()

        self.assertEqual([m["stage"] for m in cards["40-the-site.md"]["members"]],
                         ["done", "review"])
        self.assertEqual(cards["32-serve-it.md"]["phase"]["index"], 2)

    def test_a_card_in_no_phase_is_unchanged(self):
        self.members()

        loose = self.board()["31-stand-up-site.md"]

        self.assertIsNone(loose["phase"])
        self.assertEqual(loose["phaseDrift"], [])
        self.assertFalse(loose["isPhase"])
        self.assertEqual(loose["members"], [])
        self.assertEqual(loose["cards"], [])

    def test_every_way_of_writing_a_number_finds_the_same_card(self):
        self.write("07-early.md", card("07 — An early card"))
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("32-serve-it.md", card("32 — Serve it"))
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards=(
            "- #31 — hash and dash\n"
            "- 32 — em dash only\n"
            "- 7\n")))

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["file"] for m in phase["members"]],
                         ["31-stand-up-site.md", "32-serve-it.md", "07-early.md"])
        self.assertEqual(phase["phaseDrift"], [])

    def test_a_bare_list_without_bullets_reads_the_same(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="31\n32\n33\n"))

        self.assertEqual([m["number"] for m in self.board()["40-the-site.md"]["members"]],
                         ["31", "32", "33"])

    def test_an_indented_continuation_is_neither_member_nor_mistake(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards=(
            "- 31 — Stand up site/\n"
            "  the build has to land before anything is served\n"
            "- 32 — Serve it\n")))

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["number"] for m in phase["members"]], ["31", "32"])
        self.assertEqual(phase["phaseDrift"], [])

    def test_a_cards_section_on_an_ordinary_card_means_nothing(self):
        self.members()
        self.write("40-not-a-phase.md", card("40 — Just a card", kind="Feature",
                                             cards="- 31 — Stand up site/\n"))

        cards = self.board()

        self.assertFalse(cards["40-not-a-phase.md"]["isPhase"])
        self.assertEqual(cards["40-not-a-phase.md"]["cards"], [])
        self.assertIsNone(cards["31-stand-up-site.md"]["phase"])


class EmptyPhases(PhaseReadingCase):
    """A phase with nothing in it is a phase, not a failure."""

    def test_a_phase_with_no_cards_section_reads_as_empty(self):
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase"))

        phase = self.board()["40-the-site.md"]

        self.assertTrue(phase["isPhase"])
        self.assertEqual(phase["members"], [])
        self.assertEqual(phase["phaseDrift"], [])

    def test_an_empty_cards_section_reads_as_empty(self):
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards="\n"))

        phase = self.board()["40-the-site.md"]

        self.assertTrue(phase["isPhase"])
        self.assertEqual(phase["members"], [])
        self.assertEqual(phase["phaseDrift"], [])

    def test_the_section_ends_where_the_next_heading_begins(self):
        self.members()
        text = card("40 — Ship the site", kind="Phase",
                    cards="- 31 — Stand up site/\n") + "\n## Notes\n\n- 32 — not a member\n"
        self.write("40-the-site.md", text)

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["number"] for m in phase["members"]], ["31"])
        self.assertEqual(phase["phaseDrift"], [])


class DriftIsFlagged(PhaseReadingCase):
    """Every unresolvable line is an authoring mistake with a name."""

    def test_a_number_no_card_has_is_flagged(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 99 — nothing here\n"))

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["number"] for m in phase["members"]], ["31"])
        self.assertEqual(len(phase["phaseDrift"]), 1)
        self.assertIn("99", phase["phaseDrift"][0])

    def test_the_same_card_listed_twice_by_one_phase_is_flagged_once(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase", cards=(
            "- 31 — Stand up site/\n- 32 — Serve it\n- 31 — again\n")))

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["number"] for m in phase["members"]], ["31", "32"])
        self.assertEqual(len(phase["phaseDrift"]), 1)
        self.assertIn("twice", phase["phaseDrift"][0])

    def test_a_card_two_phases_both_claim_flags_both(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 32 — Serve it\n"))
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 32 — Serve it\n- 33 — The landing page\n"))

        cards = self.board()

        for filename in ("40-the-site.md", "41-the-docs.md"):
            with self.subTest(phase=filename):
                self.assertEqual(len(cards[filename]["phaseDrift"]), 1)
                self.assertIn("two phases", cards[filename]["phaseDrift"][0])
        # the first phase to list it keeps it, so the member's chip is not
        # a coin toss — and the member wears the collision too
        self.assertEqual(cards["32-serve-it.md"]["phase"]["file"], "40-the-site.md")
        self.assertEqual(len(cards["32-serve-it.md"]["phaseDrift"]), 1)
        self.assertEqual([m["number"] for m in cards["41-the-docs.md"]["members"]], ["33"])

    def test_a_line_naming_no_number_is_flagged(self):
        self.members()
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- and then the rest\n"))

        phase = self.board()["40-the-site.md"]

        self.assertEqual([m["number"] for m in phase["members"]], ["31"])
        self.assertEqual(len(phase["phaseDrift"]), 1)
        self.assertIn("and then the rest", phase["phaseDrift"][0])

    def test_a_phase_listing_a_phase_is_flagged_rather_than_nested(self):
        self.members()
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 33 — The landing page\n"))
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 41 — Ship the docs\n"))

        cards = self.board()

        self.assertEqual([m["number"] for m in cards["40-the-site.md"]["members"]], ["31"])
        self.assertIn("nest", cards["40-the-site.md"]["phaseDrift"][0])
        self.assertIsNone(cards["41-the-docs.md"]["phase"])


class DependsOnIsParsed(PhaseReadingCase):
    """The guard, read at last: what may run, not what runs next."""

    def test_the_line_becomes_a_list_of_numbers(self):
        self.write("32-serve-it.md", card("32 — Serve it", depends="03, 05"))

        self.assertEqual(self.board()["32-serve-it.md"]["dependsOn"], ["3", "5"])

    def test_a_hash_and_a_single_dependency_read_the_same(self):
        self.write("32-serve-it.md", card("32 — Serve it", depends="#31"))

        self.assertEqual(self.board()["32-serve-it.md"]["dependsOn"], ["31"])

    def test_prose_preconditions_are_left_for_the_reader(self):
        self.write("32-serve-it.md",
                   card("32 — Serve it", depends="31, a Cloudflare account"))

        self.assertEqual(self.board()["32-serve-it.md"]["dependsOn"], ["31"])

    def test_no_line_is_an_empty_list(self):
        self.write("32-serve-it.md", card("32 — Serve it"))

        self.assertEqual(self.board()["32-serve-it.md"]["dependsOn"], [])

    def test_nothing_acts_on_it_yet(self):
        """A member whose dependency is unfinished is still a member in the
        position its phase gives it — card 49 decides what may start."""
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("32-serve-it.md", card("32 — Serve it", depends="31"))
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 32 — Serve it\n"))

        cards = self.board()

        self.assertEqual(cards["32-serve-it.md"]["phase"]["index"], 2)
        self.assertEqual(cards["40-the-site.md"]["phaseDrift"], [])


class TheChipOnAMemberCard(unittest.TestCase):
    """board.html has no test runner, so these are source-level invariants:
    the chip is built from the derived membership, sits in the footer row
    with the other destinations, and opens the phase card."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def chip_block(self) -> str:
        start = self.html.index("if (task.phase) {")
        return self.html[start:start + 600]

    def chip_row(self) -> str:
        row = re.search(r"const chipRow = chips\.length.*?\.join\(''\) \+ '</div>'",
                        self.html, re.S)
        self.assertIsNotNone(row, "board.html lost its chip row")
        return row.group(0)

    def test_the_chip_is_pushed_into_the_footer_chip_row(self):
        block = self.chip_block()
        self.assertIn("chips.push", block, "the phase chip belongs in the chip row")
        self.assertIn("⟶", block)
        self.assertIn("task.phase.index", block)
        self.assertIn("task.phase.total", block)

    def test_the_chip_is_only_built_for_a_card_that_has_a_phase(self):
        """A card in no phase shows no chip: the push is guarded."""
        self.assertIn("if (task.phase) {", self.html)

    def test_the_chip_opens_the_phase_card(self):
        self.assertIn("phase: task.phase.file", self.chip_block(),
                      "the chip has to name the card it opens")
        self.assertIn("data-phase", self.chip_row())
        handler = re.search(r"querySelectorAll\('\[data-phase\]'\).*?\}\)\);",
                            self.html, re.S)
        self.assertIsNotNone(handler, "nothing wires the phase chip's click")
        self.assertIn("findTask(btn.dataset.phase)", handler.group(0))
        self.assertIn("showDetail(", handler.group(0))
        self.assertIn("stopPropagation", handler.group(0),
                      "clicking the chip must not also open its own card")

    def test_a_chips_leading_glyph_is_rendered(self):
        """The chip reads ⟶ phase n/total, so the row renders a glyph
        before the label as well as after it."""
        row = self.chip_row()
        self.assertIn("c.pre", row)
        self.assertIn("${p}", row, "the leading glyph has to reach the markup")

    def test_phase_drift_is_flagged_beside_status_drift(self):
        top = re.search(r"const top = \[.*?\];", self.html, re.S)
        self.assertIsNotNone(top, "board.html lost the card's top row")
        self.assertIn("phaseDrift", top.group(0))
        self.assertIn("phase drift", top.group(0))
        self.assertIn('class="pill drift"', top.group(0),
                      "phase drift wears the same flag status drift does")


@unittest.skipUnless(NODE, "node is needed to run the page's own phaseLabel()")
class TheNameOnTheChip(unittest.TestCase):
    """`phaseLabel()` is a pure function of the phase, so — as with the
    drawer's `md()` — it is lifted out of the page and run for real."""

    @classmethod
    def setUpClass(cls):
        html = BOARD.read_text(encoding="utf-8")
        match = re.search(r"^function phaseLabel\(phase\) \{\n.*?\n\}\n", html,
                          re.M | re.S)
        assert match, "board.html lost its phaseLabel()"
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "label.js"
        cls.js.write_text(match.group(0) + "process.stdout.write(phaseLabel("
                          "JSON.parse(require('fs').readFileSync(0, 'utf8'))));\n",
                          encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def label(self, **phase) -> str:
        out = subprocess.run([NODE, str(self.js)], input=json.dumps(phase),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_the_number_the_title_opens_with_is_left_to_the_tooltip(self):
        self.assertEqual(self.label(title="40 — Ship the site", file="40-site.md"),
                         "Ship the site")

    def test_a_title_without_a_number_is_used_whole(self):
        self.assertEqual(self.label(title="Ship the site", file="40-site.md"),
                         "Ship the site")

    def test_a_long_name_is_clipped_to_a_chips_width(self):
        label = self.label(title="40 — Ship the site and everything around it",
                           file="40-site.md")
        self.assertTrue(label.endswith("…"), label)
        self.assertLessEqual(len(label), 22)
        self.assertTrue("Ship the site".startswith(label[:13]))

    def test_a_phase_with_no_title_falls_back_to_its_filename(self):
        self.assertEqual(self.label(title="", file="40-site.md"), "40-site.md")

    def test_the_page_still_parses(self):
        """board.html has no runner, so a stray brace in its inline script
        would reach the browser silently. Parsing costs nothing here."""
        html = BOARD.read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
        self.assertTrue(scripts, "board.html has no inline script")
        for index, script in enumerate(scripts):
            source = Path(self._dir.name) / f"page-{index}.js"
            source.write_text(script, encoding="utf-8")
            out = subprocess.run([NODE, "--check", str(source)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
