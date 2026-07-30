"""The kanban's columns yield on small laptops (task 09).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants: the ones that, if broken, would bring
back the rigid 296px columns that clipped the Done column on a 13" MacBook,
or let the floor drift above what a 1280px viewport can hold.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"

COLUMNS = 5  # backlog → to-do → in-progress → review → done


class ColumnFlexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        m = re.search(r"\.kcol\{([^}]*)\}", cls.html)
        assert m, "board.html lost its .kcol rule"
        cls.kcol = m.group(1)

    def token(self, name: str) -> int:
        m = re.search(rf"{name}:\s*(\d+)px", self.html)
        self.assertIsNotNone(m, f"{name} must be a px token at the top of board.html")
        return int(m.group(1))

    def test_floor_is_a_design_token(self):
        """The floor lives beside --col with the other layout tokens, not as
        a magic number buried in the rule."""
        self.token("--col-min")
        self.assertIn("min-width:var(--col-min)", self.kcol.replace(" ", ""),
                      ".kcol must take its floor from the --col-min token")

    def test_columns_yield_between_floor_and_cap(self):
        """flex:1 1 0 with max-width:var(--col): wide screens cap at today's
        296px (pixel-identical), narrower viewports shrink all five evenly."""
        flat = self.kcol.replace(" ", "")
        self.assertIn("flex:110", flat,
                      ".kcol must grow and shrink from a zero basis")
        self.assertIn("max-width:var(--col)", flat,
                      "wide screens must still cap columns at --col")
        self.assertNotIn("width:var(--col);", flat.replace("max-width", ""),
                         "a fixed width would undo the flex")
        self.assertEqual(self.token("--col"), 296,
                         "the cap is today's column width; changing it is a "
                         "redesign, not this fix")

    def test_five_columns_fit_a_1280px_viewport(self):
        """At the floor, 5 columns + 4 gaps + the board's own padding must
        not exceed 1280 CSS px — the smallest laptop this board honours."""
        floor = self.token("--col-min")
        gap = self.token("--gap")
        board = re.search(r"#board\{([^}]*)\}", self.html).group(1)
        pad = re.search(r"padding:\s*\d+px\s+(\d+)px", board)
        self.assertIsNotNone(pad, "#board lost its horizontal padding")
        footprint = COLUMNS * floor + (COLUMNS - 1) * gap + 2 * int(pad.group(1))
        self.assertLessEqual(footprint, 1280,
                             f"five columns at the floor need {footprint}px; "
                             "a 1280px viewport would scroll")

    def test_cards_stay_legible_at_the_floor(self):
        """Shrinking must stop somewhere: a floor under ~240px would start
        crushing card internals instead of ellipsising them."""
        self.assertGreaterEqual(self.token("--col-min"), 240)

    def test_overflow_fallback_survives(self):
        """Below the floor the old behaviour is the fallback: #board still
        scrolls horizontally rather than clipping."""
        board = re.search(r"#board\{([^}]*)\}", self.html).group(1)
        self.assertIn("overflow-x:auto", board.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
