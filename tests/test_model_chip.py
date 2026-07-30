"""The model chip beside every agent name (task 24).

Task 12 recorded which model each launch rode; this puts it where eyes
land — one chip, in the session-id hash's register, beside the name that
identifies a run.

Two halves. The chip's behaviour (shortening, escaping, and the silence
that means "this launch never knew") is exercised for real: the two
functions are lifted out of board.html and run in node, skipped where
node is absent. The placement — which four render sites wear it, and
that they all wear the same one — is a source-level invariant, board.html
being a single file with inline JS and no frontend test runner.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"

# The pieces the chip is made of, lifted from the page as written.
PARTS = (
    r"const esc = \(s\) =>.*?\}\[c\]\)\);",
    r"function shortModel\(model\) \{.*?\n\}",
    r"function modelChip\(agent\) \{.*?\n\}",
)


def _harness() -> str:
    html = BOARD.read_text(encoding="utf-8")
    out = []
    for pattern in PARTS:
        m = re.search(pattern, html, re.S)
        if m is None:
            raise AssertionError(f"board.html no longer defines {pattern!r}")
        out.append(m.group(0))
    return "\n".join(out)


class ChipBehaviour(unittest.TestCase):
    """What the chip actually renders, run as the browser would run it."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — chip behaviour unrun")
        cls.src = _harness()

    def chips(self, agents: list) -> list:
        """modelChip(a) for each given agent record."""
        script = (self.src + "\nconsole.log(JSON.stringify("
                  + json.dumps(agents) + ".map(modelChip)));")
        out = subprocess.run([self.node, "-e", script],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_launch_that_never_knew_its_model_shows_nothing(self):
        """No chip is the honest answer for an inherited launch, a session
        with no agent record (yours, or one replayed from disk after a
        restart), and a pre-task-12 record: a placeholder would read as a
        model actually named that."""
        self.assertEqual(
            self.chips([None, {}, {"model": None}, {"model": ""},
                        {"name": "Wren"}]),
            ["", "", "", "", ""])

    def test_the_vendor_prefix_is_dropped_and_kept_on_hover(self):
        """claude-opus-4-8 → opus-4-8 on the face, whole on the title."""
        chip, = self.chips([{"model": "claude-opus-4-8"}])
        self.assertIn(">opus-4-8</span>", chip)
        self.assertIn('title="claude-opus-4-8"', chip)
        self.assertIn('class="mchip"', chip)

    def test_a_provider_path_is_dropped_the_same_way(self):
        """The opencode adapter's ids are provider/model."""
        chip, = self.chips([{"model": "anthropic/claude-opus-4-8"}])
        self.assertIn(">opus-4-8</span>", chip)
        self.assertIn('title="anthropic/claude-opus-4-8"', chip)

    def test_an_unfamiliar_name_is_shown_as_recorded_not_guessed_at(self):
        """Shortening only removes what this board knows is redundant —
        it never eats the first word of a name it does not recognise."""
        for model in ("gpt-4o", "some-model", "opus-4-8"):
            chip, = self.chips([{"model": model}])
            self.assertIn(f">{model}</span>", chip)

    def test_the_model_string_is_escaped_on_both_face_and_title(self):
        chip, = self.chips([{"model": 'a"<b'}])
        self.assertNotIn('"<b', chip)
        self.assertIn("&quot;&lt;b", chip)


class ChipPlacement(unittest.TestCase):
    """Where it goes: beside the name, in all four places, once."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    # anchor → what the name is in that view. The chip must follow the
    # anchor within a few characters: beside the name, not further down.
    SITES = {
        "the sessions list row":
            '<span class="sid">${esc(m.id.slice(0, 8))}</span>',
        "the session-detail header":
            '<span class="sid">${esc(sid.slice(0, 8))}</span>',
        "the Focus header":
            '<span class="acc">${esc((meta.label || \'\').split(\' · \')[0])}</span>',
        "the working card's agent line":
            '<span class="who">${esc(who)}</span>',
    }

    def test_every_name_that_identifies_a_run_wears_the_chip(self):
        for where, anchor in self.SITES.items():
            pos = self.html.find(anchor)
            self.assertNotEqual(pos, -1, f"{where} no longer renders as expected")
            window = self.html[pos + len(anchor):pos + len(anchor) + 40]
            self.assertIn("modelChip(", window,
                          f"{where} lost the model chip beside its name")

    def test_there_is_one_chip_component_not_four(self):
        """One component means one register: a second inline copy is how
        the four drift apart."""
        self.assertEqual(self.html.count('class="mchip"'), 1,
                         "the chip's markup must live only in modelChip()")
        self.assertEqual(self.html.count("function modelChip("), 1)

    def test_the_chip_is_the_id_hashs_register_and_means_no_state(self):
        """Mono because a model name is machine-produced, dim because it
        is a footnote to the name — and no state colour, because a model
        is not a state."""
        rule = re.search(r"\n  \.mchip\{([^}]*)\}", self.html)
        self.assertIsNotNone(rule, "the .mchip rule is gone")
        self.assertIn("font-family:var(--mono)", rule.group(1))
        self.assertIn("color:var(--dim)", rule.group(1))
        for state_colour in ("--accent", "--calm", "--alarm"):
            self.assertNotIn(state_colour, rule.group(1),
                             f"the chip took on {state_colour}: colour "
                             "would start meaning 'model' as well as state")

    def test_the_chip_never_wraps_a_line_it_joins(self):
        """It joins flex rows carrying names and timestamps; a wrapping
        chip would move them."""
        rule = re.search(r"\n  \.mchip\{([^}]*)\}", self.html)
        self.assertIn("white-space:nowrap", rule.group(1))

    def test_the_metadata_lines_no_longer_repeat_a_known_model(self):
        """The chip says which model; the two lines that used to carry it
        keep only what the chip cannot say — that a launch inherited the
        vendor default — and say it nowhere else."""
        for m in re.finditer(r"model inherited", self.html):
            window = self.html[max(0, m.start() - 200):m.start()]
            self.assertIn("!agent.model", window,
                          "'model inherited' must be reached only when the "
                          "model is genuinely unknown")
        self.assertEqual(self.html.count("model inherited"), 2,
                         "the session-detail line and the Focus refline are "
                         "the two places that say it")


if __name__ == "__main__":
    unittest.main()
