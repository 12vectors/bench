"""A quiet card face, with every tool one labelled control away (task 76).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants — the ones that, if broken, would put
the card back where this task found it (status and ID repeated, a row of
operational chrome on every face) or, worse, would buy that quiet by
hiding a failure or a missing requirement.

The measurable half — the drawn card, the menu by keyboard, 390px — is in
`verify/browser_cases/case_board.py`.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        cls.card = re.search(r"function cardFor\(task\) \{.*?\n\}\n",
                             cls.html, re.S).group(0)

    def body(self, name: str) -> str:
        start = self.html.index(f"function {name}(")
        return self.html[start:self.html.index("\n}\n", start)]


class OneIdAndNoRoutineStatus(Source):
    def test_the_title_drops_only_its_own_number(self):
        body = self.body("cardTitle")
        self.assertIn("canonicalNumber(m[1]) === canonicalNumber(task.number)", body,
                      "a number that is not this card's is the author's title")
        self.assertIn("esc(cardTitle(task))", self.card)
        self.assertNotIn("esc(task.title)", self.card)

    def test_the_status_pill_is_drawn_only_when_something_promoted_it(self):
        self.assertIn("let promoted = !!agent || working;", self.card)
        # every exception that used to set the pill still promotes it
        for word in ("approved", "changes asked", "run failed", "halted",
                     "not published", "completing", "idle"):
            self.assertIn(f"text: '{word}'", self.card, word)
        self.assertEqual(self.card.count("promoted = true;"), 8,
                         "verdict ×2, phase idle, failure, halt, not published, "
                         "completing, idle")
        self.assertRegex(self.card, r"promoted\s*\?\s*`<span class=\"pill status")

    def test_no_edited_time_chatter_on_the_face(self):
        self.assertNotIn("'edited ' + ago(", self.card)
        # the detail view keeps when the file changed
        self.assertIn("edited ${esc(when)}", self.body("renderDrawer"))

    def test_the_who_row_is_drawn_for_an_owner_only(self):
        self.assertIn("const showWho = !!agent || !!task.assignee || "
                      "task.stage === 'in-progress';", self.card)


class ExceptionsStayOnTheFace(Source):
    def test_anything_bad_or_live_is_on_the_face(self):
        body = self.body("chipOnFace")
        self.assertIn("c.cls === 'bad' || c.cls === 'accent'", body)
        self.assertIn("if (c.phase) return true;", body,
                      "phase navigation is how a member reaches its parent")

    def test_a_resting_drive_is_a_tool_not_a_face_chip(self):
        """Only a running or refused drive changes the next action; the
        idle `drive ✳` chip would otherwise sit on every review card with
        a branch whenever a driver exists."""
        idle = next(l for l in self.card.splitlines()
                    if "label: 'drive', glyph: '✳'" in l)
        self.assertIn("face: false", idle)
        refused = next(l for l in self.card.splitlines()
                       if "label: 'drive', glyph: '✕'" in l)
        self.assertNotIn("face: false", refused)
        self.assertIn("if (c.face !== undefined) return c.face;",
                      self.body("chipOnFace"))

    def test_a_gate_that_is_not_met_is_never_quiet(self):
        """Required missing checks read through the gate chip, which is
        blocked (bad), waiting (accent) or overridden (bad) whenever it is
        not met — and all three are face chips by the rule above."""
        gates = self.html[self.html.index("const GATE_STATES = {"):]
        gates = gates[:gates.index("};")]
        self.assertIn("blocked: ['bad'", gates)
        self.assertIn("pending: ['accent'", gates)
        self.assertIn("overridden: ['bad'", gates)

    def test_failed_or_stale_evidence_is_never_quiet(self):
        verify = self.html[self.html.index("const VERIFY_STATE = {"):]
        verify = verify[:verify.index("};")]
        for outcome in ("failed:", "stale:", "'prepare-failed':", "error:", "denied:"):
            line = next(l for l in verify.splitlines() if l.strip().startswith(outcome))
            self.assertIn("'fail'", line, outcome)
        chip = self.body("verifyChip")
        self.assertIn("fail: 'bad'", chip)


class EveryToolOneControlAway(Source):
    def test_the_tools_menu_holds_every_chip(self):
        self.assertIn("const tools = chips.map(c => chipHtml(", self.card)
        self.assertIn("const face = chips.filter(c => chipOnFace(c, task));", self.card)

    def test_the_menu_control_is_labelled_and_reports_its_state(self):
        self.assertIn("more.setAttribute('aria-label', `Tools for ${ref}`);", self.card)
        self.assertIn("more.setAttribute('aria-expanded', String(menuOpen));", self.card)
        self.assertIn("role=\"group\"", self.card)

    def test_the_hover_actions_are_in_the_tools_too(self):
        self.assertIn("wireAction(twin, `${task.file}::${act.label}`, act);", self.card,
                      "the same key, so an armed window is one window")

    def test_the_detail_view_carries_every_tool(self):
        drawer = self.body("renderDrawer")
        self.assertIn("cardFor(t).querySelector('.cardtools')", drawer,
                      "the card's own builder, so the two cannot disagree")
        self.assertIn("signalsBlock(t)", drawer)

    def test_the_keyboard_reaches_the_card_and_its_actions(self):
        self.assertIn("el.tabIndex = 0;", self.card)
        self.assertIn("e.key === 'Enter' || e.key === ' '", self.card)
        self.assertIn(".card:focus-within .hoveracts", self.html)
        self.assertIn("focusCard(t.file);", self.body("renderDrawer"),
                      "closing the detail view returns focus to its card")

    def test_board_and_phases_draw_the_same_card(self):
        self.assertEqual(len(re.findall(r"appendChild\(cardFor\(", self.html)), 2)


class SignalsAreKeptApart(Source):
    def test_ci_review_bench_and_gate_are_four_rows(self):
        body = self.body("signalsBlock")
        for label in ("'Bench checks'", "'External CI'", "'Review'", "'Merge gate'"):
            self.assertIn(label, body)
        self.assertIn("not a Bench check", body, "green CI is not a named check")
        self.assertIn("that is not a pass", body, "no gate is not a pass")


if __name__ == "__main__":
    unittest.main()
