"""Card actions are click-solid (task 17).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants — the ones that, if broken, would bring
back the wobble the task fixed: a hit target that moves or reshapes
mid-interaction, an armed window you cannot see, a fired action that gives
no feedback until the SSE redraw, or a stuck busy state with no honest exit.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"


class StableGeometryTests(unittest.TestCase):
    """The target must not travel or reshape while being clicked at."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def rule(self, selector: str) -> str:
        m = re.search(re.escape(selector) + r"\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, f"board.html lost its {selector} rule")
        return m.group(1).replace(" ", "").replace("\n", "")

    def test_slot_enters_with_opacity_only(self):
        """The slot fades in place: any transform in its entry animation
        means the target is moving while the pointer approaches it."""
        slot = self.rule(".hoveracts")
        m = re.search(r"animation:(\w+)", slot)
        self.assertIsNotNone(m, ".hoveracts must announce itself with an animation")
        frames = re.search(r"@keyframes " + m.group(1) + r"\{([^@]*?)\}\n", self.html)
        self.assertIsNotNone(frames, f"@keyframes {m.group(1)} missing")
        self.assertNotIn("transform", frames.group(1),
                         "the slot's entry animation must animate opacity only")

    def test_hit_target_is_at_least_24px_and_reserved(self):
        """Buttons are ≥24px tall and the toprow reserves that height even
        when showing the (shorter) status pill, so hovering never grows the
        card or shifts the cards below it."""
        btn = self.rule(".hoveracts button")
        m = re.search(r"min-height:(\d+)px", btn)
        self.assertIsNotNone(m, ".hoveracts button needs an explicit min-height")
        self.assertGreaterEqual(int(m.group(1)), 24)
        row = self.rule(".card .toprow")
        rm = re.search(r"min-height:(\d+)px", row)
        self.assertIsNotNone(rm, ".card .toprow needs a min-height")
        self.assertGreaterEqual(int(rm.group(1)), int(m.group(1)),
                                "the row must reserve the buttons' height")

    def test_slot_pads_its_hitbox_without_moving_layout(self):
        """Padding widens the slot's catch area; the matching negative
        margin keeps the buttons exactly where they were."""
        slot = self.rule(".hoveracts")
        pad = re.search(r"padding:(\d+)px", slot)
        neg = re.search(r"margin:-(\d+)px", slot)
        self.assertIsNotNone(pad, ".hoveracts must pad its hitbox")
        self.assertIsNotNone(neg, "…and take the padding back out of layout")
        self.assertEqual(pad.group(1), neg.group(1))

    def test_every_state_label_shares_one_grid_cell(self):
        """rest / confirm / busy labels are stacked in one cell, so the
        button is born as wide as its widest state and never reshapes when
        arming swaps the text under the cursor."""
        self.assertIn("grid-area:1/1", self.rule(".actlbl>span"))
        self.assertIn("inline-grid", self.rule(".actlbl"))
        for cls in ("l-rest", "l-arm", "l-busy"):
            self.assertIn(cls, self.html, f"the {cls} label span is gone")


class ArmedWindowTests(unittest.TestCase):
    """Armed is a visible, timed state — the user sees what they click in."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def arm_ms(self) -> int:
        m = re.search(r"const ARM_MS = (\d+)", self.html)
        self.assertIsNotNone(m, "ARM_MS must be a named constant")
        return int(m.group(1))

    def test_window_is_about_five_seconds(self):
        self.assertEqual(self.arm_ms(), 5000,
                         "the task lengthened the 3.5s window to ~5s")

    def test_drain_bar_matches_the_disarm_timer(self):
        """The armed button wears a draining bar whose CSS duration equals
        ARM_MS — two clocks showing different times is worse than one."""
        m = re.search(r"animation:drain (\d+(?:\.\d+)?)s linear", self.html)
        self.assertIsNotNone(m, "button.armed::after must run the drain animation")
        self.assertEqual(float(m.group(1)) * 1000, self.arm_ms())
        self.assertIn("@keyframes drain{from{transform:scaleX(1)}to{transform:scaleX(0)}}",
                      self.html)

    def test_rebuilt_buttons_rejoin_the_drain_mid_window(self):
        """Cards are torn down on every SSE render; a rebuild mid-window
        must resume the bar via a negative delay, not restart it."""
        self.assertIn("--arm-delay", self.html)
        self.assertIn("remaining - ARM_MS", self.html)

    def test_armed_state_survives_a_rerender(self):
        """The truth lives in S.acts, not on the DOM node."""
        self.assertRegex(self.html, r"acts:\s*\{\}", "S needs the acts store")
        self.assertIn("phase: 'armed', until:", self.html)


class BusyStateTests(unittest.TestCase):
    """Firing locks the button instantly; the exit is never silent."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_fire_locks_before_the_request_leaves(self):
        """lockAction (disable + busy class) is the first thing fireAction
        does — the click must read as taken before the network is asked."""
        m = re.search(r"async function fireAction\(btn, key, act\) \{\n(\s*)lockAction\(btn\);",
                      self.html)
        self.assertIsNotNone(m, "fireAction must lock the button first")

    def test_busy_wears_the_working_vocabulary(self):
        """busy = breathe + accent: the design system's 'an agent is
        working', not a new dialect."""
        self.assertRegex(self.html, r"button\.busy \.g,\s*button\.busy \.g2\{animation:breathe")
        self.assertIn("button.chip2.busy{color:var(--accent)", self.html.replace(" ", ""))

    def test_busy_holds_the_slot_open_and_the_pill_hidden(self):
        """A busy button stays visible when the pointer leaves, exactly as
        an armed one does."""
        flat = self.html.replace(" ", "").replace("\n", "")
        self.assertIn(".hoveracts:has(.armed),.hoveracts:has(.busy){display:flex}", flat)
        self.assertIn(":has(.hoveracts.busy).toprow.pill.status", flat)

    def test_timeout_is_an_honest_exit(self):
        """If neither the response nor a redraw arrives, the button comes
        back with a toast naming the action — never a silent revert."""
        self.assertRegex(self.html, r"FIRE_TIMEOUT_MS = \d+")
        self.assertIn("toast(`${act.label} got no answer", self.html)
        self.assertIn("toast(`${act.label} failed", self.html)

    def test_run_functions_report_failure(self):
        """fireAction can only unlock-on-error if the runners tell it the
        truth: each POST helper returns its res.ok."""
        for fn in ("runCommand", "stopAgent", "askCopilot"):
            body = re.search(r"async function " + fn + r"\([^)]*\) \{(.*?)\n\}",
                             self.html, re.S)
            self.assertIsNotNone(body, f"{fn} is gone")
            self.assertIn("return res.ok", body.group(1), f"{fn} must return res.ok")
        agent = re.search(r"async function fireAgent.*?\n\}", self.html, re.S)
        self.assertIn("return false", agent.group(0))
        self.assertIn("return true", agent.group(0))


class OneSlotBuilderTests(unittest.TestCase):
    """Every action walks through the same machine — no per-action forks."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_hover_actions_and_command_chips_share_the_machine(self):
        self.assertEqual(len(re.findall(r"(?<!function )wireAction\(btn,", self.html)), 3,
                         "exactly three call sites: the slot builder, the "
                         "$-command chips and the ⌸ archive chip — all on "
                         "the one machine")
        self.assertNotIn("btn.firstChild.textContent", self.html,
                         "the old per-chip label swap is the fork this kills")
        self.assertNotIn("btn.lastElementChild.textContent", self.html)

    def test_every_action_declares_its_participle(self):
        for word in ("starting…", "holding…", "checking…", "reviewing…",
                     "acting…", "asking…", "moving…", "reopening…", "running…",
                     "archiving…"):
            self.assertIn(word, self.html, f"busy label {word!r} missing")

    def test_misses_die_at_the_slot(self):
        """stopPropagation lives on the slot, so clicks in its padding or
        between buttons neither fire an action nor open the card sheet."""
        m = re.search(r"slot\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\)",
                      self.html)
        self.assertIsNotNone(m, "the slot must swallow clicks itself")
        builder = re.search(r"if \(actions\.length\) \{.*?el\.querySelector\('\.toprow'\)",
                            self.html, re.S).group(0)
        self.assertNotIn("e.stopPropagation", builder.replace(m.group(0), ""),
                         "per-button stopPropagation would let gap clicks "
                         "bubble into the card")


if __name__ == "__main__":
    unittest.main()
