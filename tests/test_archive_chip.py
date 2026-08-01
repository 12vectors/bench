"""An archive button on the card, where the card is (task 47).

Archiving used to be one gesture only: drag the card the length of the
board onto the activity bar. The card now carries the same action itself —
a `⌸` chip at the right-hand end of its footer row, arming on the first
click and firing on the second.

board.html is a single file with inline JS and no frontend test runner, so
most of these are source-level invariants: the ones that, if broken, would
give back the thing the task was written against — a second copy of the
archivable-stages rule in the page, a bespoke confirmation, an archive that
loses the ⌘Z promise, or an armed chip left behind by a card that has gone.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import taskfiles  # noqa: E402

BOARD = CORE / "board.html"
TRAY_GLYPH = "⌸"


def html() -> str:
    return BOARD.read_text(encoding="utf-8")


class OneAuthorityForTheRule(unittest.TestCase):
    """Which cards may be archived is the server's answer. The page asks
    for it rather than keeping a copy that can drift out of step."""

    @classmethod
    def setUpClass(cls):
        cls.html = html()

    def test_the_working_stages_are_not_archivable(self):
        """The rule itself, unchanged: finish or walk a card back, never
        tidy it away mid-flight."""
        self.assertEqual(taskfiles.ARCHIVE_FROM, {"backlog", "to-do", "done"})
        for working in ("in-progress", "review"):
            self.assertIn(working, config.STAGE_DIRS)
            self.assertNotIn(working, taskfiles.ARCHIVE_FROM)
            with self.assertRaises(ValueError):
                taskfiles.archive_task("47-nothing.md", working)

    def test_the_state_payload_carries_the_set(self):
        import httpd
        payload = httpd.state_payload()
        self.assertIn("archiveFrom", payload)
        self.assertEqual(set(payload["archiveFrom"]), taskfiles.ARCHIVE_FROM)
        self.assertEqual(payload["archiveFrom"],
                         [slug for slug, _ in config.STAGES
                          if slug in taskfiles.ARCHIVE_FROM],
                         "sent in board order, so the JSON reads like the board")

    def test_the_page_holds_no_second_copy(self):
        for fossil in ("'backlog', 'to-do', 'done'", '"backlog", "to-do", "done"'):
            self.assertNotIn(fossil, self.html,
                             "the archivable stages are the server's list, "
                             "not a literal in the page")

    def test_both_gestures_ask_the_same_helper(self):
        """canArchive() — defined once, reading S.state.archiveFrom, and
        used by the chip and by the drag-to-the-bar gesture alike."""
        body = re.search(r"function canArchive\(task\) \{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(body, "canArchive is gone")
        self.assertIn("S.state.archiveFrom", body.group(1))
        self.assertEqual(len(re.findall(r"canArchive\(task\)", self.html)), 3,
                         "one definition and exactly two callers: the chip "
                         "and the dragstart handler")


class TheChipOnTheCard(unittest.TestCase):
    """A `⌸` chip, last in the footer row, quiet until you are on it."""

    @classmethod
    def setUpClass(cls):
        cls.html = html()
        cls.push = re.search(r"if \(canArchive\(task\)\) \{(.*?)\n  \}",
                             cls.html, re.S)

    def rule(self, selector: str) -> str:
        m = re.search(re.escape(selector) + r"\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, f"board.html lost its {selector} rule")
        return m.group(1).replace(" ", "").replace("\n", "")

    def test_it_wears_the_trays_glyph(self):
        """The whole point is that the two are visibly one action, so the
        glyph appears exactly where the tray's does and nowhere else."""
        self.assertIsNotNone(self.push, "the archive chip is not pushed")
        self.assertIn(f"pre: '{TRAY_GLYPH}'", self.push.group(1))
        tray = re.search(r"\$\('#tray'\)\.innerHTML =(.*?);", self.html, re.S)
        self.assertIn(TRAY_GLYPH, tray.group(1), "the tray lost its glyph")
        self.assertEqual(self.html.count(TRAY_GLYPH), 2,
                         "one glyph, two places: the tray and the card's chip")

    def test_it_is_the_last_chip_in_the_row(self):
        chips = self.html.index("const chipRow = chips.length")
        commands = self.html.index("for (const cmd of (S.state.commands || []))")
        self.assertLess(commands, self.push.start(),
                        "the archive chip is pushed after every tool chip")
        self.assertLess(self.push.end(), chips,
                        "…and before the row is rendered")

    def test_it_sits_at_the_far_end_and_rests_quiet(self):
        self.assertIn("cls: 'arch'", self.push.group(1))
        arch = self.rule(".chip2.arch")
        self.assertIn("margin-left:auto", arch, "pushed to the right-hand end")
        self.assertIn("border-color:transparent", arch)
        self.assertIn("color:var(--dim)", arch)

    def test_hover_armed_and_busy_all_outrank_the_quiet_rule(self):
        """`.chip2.arch` is a two-class selector on purpose: every live
        state is a class plus an element or a pseudo-class, so none of
        them is dulled back to dim by the resting rule."""
        flat = self.html.replace(" ", "").replace("\n", "")
        for live in ("button.chip2:hover{", "button.chip2.armed{", "button.chip2.busy{"):
            self.assertIn(live, flat, f"{live} must stay more specific than .chip2.arch")

    def test_no_chip_where_the_server_would_refuse(self):
        """Nothing renders it on its own terms: the one condition is
        canArchive, so in-progress and review cards simply have no chip."""
        card = re.search(r"function cardFor\(task\) \{.*?\n\}\n", self.html, re.S).group(0)
        self.assertEqual(len(re.findall(r"data-arch", card)), 2,
                         "the chip is written once and wired once")
        self.assertEqual(len(re.findall(r"canArchive\(task\)", card)), 2)


class ArmThenFire(unittest.TestCase):
    """Nothing about archiving invents its own confirmation."""

    @classmethod
    def setUpClass(cls):
        cls.html = html()

    def test_it_walks_the_one_state_machine(self):
        wiring = re.search(r"el\.querySelectorAll\('\[data-arch\]'\).*?\n  \}\);",
                           self.html, re.S)
        self.assertIsNotNone(wiring, "the archive chip is not wired")
        self.assertIn("wireAction(btn, `${task.file}::archive`", wiring.group(0))
        self.assertIn("confirm: 'archive it?'", wiring.group(0))
        self.assertIn("busy: 'archiving…'", wiring.group(0))
        self.assertIn("archiveCard(task.file, task.stage)", wiring.group(0))

    def test_the_label_swaps_in_place_like_every_other_action(self):
        push_to_row = self.html[self.html.index("if (c.arch) return"):]
        line = push_to_row.split("\n", 1)[0]
        self.assertIn("actLabel(c.label, 'archive it?', 'archiving…')", line,
                      "rest / confirm / busy share one grid cell")
        self.assertIn("<button", line, "an action chip is a button")
        push = re.search(r"if \(canArchive\(task\)\) \{(.*?)\n  \}",
                         self.html, re.S).group(1)
        self.assertIn("label: 'archive'", push, "and the resting label says what it does")

    def test_no_bespoke_confirmation_anywhere_near_it(self):
        for fossil in ("window.confirm", "confirm('", "confirm(`"):
            self.assertNotIn(fossil, self.html,
                             "arm-then-fire is the confirmation")

    def test_a_refused_archive_reports_itself(self):
        """fireAction can only unlock on failure if the runner tells it
        the truth — the same contract runCommand and stopAgent keep."""
        body = re.search(r"async function archiveCard\(file, from\) \{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body, "archiveCard is gone")
        self.assertIn("return res.ok", body.group(1))

    def test_the_click_does_not_open_the_card(self):
        wiring = re.search(r"el\.querySelectorAll\('\[data-arch\]'\).*?\n  \}\);",
                           self.html, re.S).group(0)
        self.assertIn("e.stopPropagation()", wiring)


class TheUndoStaysThePromise(unittest.TestCase):
    """One path to the API, so the sentence that makes a one-click archive
    safe is told whichever gesture asked for it."""

    @classmethod
    def setUpClass(cls):
        cls.html = html()

    def test_one_place_calls_the_api(self):
        self.assertEqual(self.html.count("'/api/archive'"), 1,
                         "the chip and the drop share archiveCard")

    def test_the_toast_names_the_undo(self):
        body = re.search(r"async function archiveCard\(file, from\) \{(.*?)\n\}",
                         self.html, re.S).group(1)
        self.assertIn("⌘Z brings it back", body)
        self.assertIn("await loadState()", body,
                      "the tray's count is read from the state, so it "
                      "follows only if the archive reloads it")

    def test_the_chips_tooltip_says_it_too(self):
        push = re.search(r"if \(canArchive\(task\)\) \{(.*?)\n  \}",
                         self.html, re.S).group(1)
        self.assertIn("⌘Z brings it back", push)
        self.assertIn("never deleted", push)

    def test_the_bar_still_archives_what_is_dropped_on_it(self):
        self.assertIn("if (d) archiveCard(d.file, d.from);", self.html,
                      "the drag gesture keeps working exactly as it did")
        self.assertIn("unarchiveLast();", self.html, "⌘Z is still bound")


class NoStaleArmedChip(unittest.TestCase):
    """A card archived by someone else, or moved by hand on disk, takes
    its armed windows with it."""

    @classmethod
    def setUpClass(cls):
        cls.html = html()

    def test_render_forgets_the_windows_of_cards_that_left(self):
        body = re.search(r"function forgetActsOfVanishedCards\(\) \{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body, "the sweep is gone")
        self.assertIn("allTasks()", body.group(1))
        self.assertIn("key.split('::')[0]", body.group(1),
                      "act keys are <file>::<action>")
        self.assertIn("delete S.acts[key]", body.group(1))

    def test_the_sweep_runs_before_anything_is_drawn(self):
        head = re.search(r"function render\(\) \{\n(.*?)\n  renderTitle", self.html, re.S)
        self.assertIn("forgetActsOfVanishedCards();", head.group(1))

    def test_every_act_key_is_a_task_file(self):
        """The sweep reads the key's first segment as a filename, so every
        key wireAction is given must start with one."""
        keys = re.findall(r"wireAction\(btn, `([^`]+)`", self.html)
        self.assertTrue(keys, "no wired actions found")
        for key in keys:
            self.assertTrue(key.startswith("${task.file}::"), f"stray act key {key!r}")


if __name__ == "__main__":
    unittest.main()
