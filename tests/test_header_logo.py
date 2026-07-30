"""The header wears the design's wordmark (task 23).

The design's answer to "what is the bench logo" is that there isn't a drawn
one: the word *is* the logo — "bench", lowercase, set in Zilla Slab SemiBold
and tracked -.015em, with the b lifted out of the same face as the icon. The
board cannot fetch that face (the page adds no network requests for a logo),
so the mark ships as outlines instead, and the b's outline is reused verbatim
as the tab icon.

board.html has no frontend test runner, so this is checked the way task 22's
browser half is: as source-level invariants — the ones that, if broken, would
put the mark back on a font, bake a colour into it, let the two copies of the
b drift apart, or shove the header's neighbours around.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "manager" / "core" / "board.html"

# What the page was already allowed to fetch before the logo landed. The list
# is the point: a logo that needed a font would have to grow it.
KNOWN_EXTERNALS = {
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap",
}

# Colour only ever means state, so none of these may appear in the mark.
STATE_TOKENS = ("var(--accent)", "var(--calm)", "var(--alarm)", "var(--idle)")


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def mark(self) -> str:
        """The inline wordmark, markup and all."""
        found = re.search(r'<svg class="mark".*?</svg>', self.html, re.DOTALL)
        self.assertIsNotNone(found, "board.html lost the inline wordmark")
        return found.group(0)

    def icon_href(self) -> str:
        found = re.search(r'<link rel="icon" href="([^"]*)"', self.html)
        self.assertIsNotNone(found, "board.html lost its tab icon")
        return found.group(1)


class TheWordmark(Fixture):
    def test_the_header_carries_it_as_outlines(self):
        """Outlines, not text: a font-set wordmark on this page would either
        need a fetch or silently fall back to whatever the OS has."""
        brand = re.search(r'<div class="brand">.*?</div>', self.html, re.DOTALL)
        self.assertIsNotNone(brand, "board.html lost the .brand block")
        self.assertIn('<svg class="mark"', brand.group(0))
        self.assertNotIn("<b>Bench</b>", self.html,
                         "the old font-set wordmark is still in the header")

    def test_the_word_is_the_lowercase_one(self):
        """"bench", not "Bench" — the design sets it lowercase everywhere, and
        the accessible name is the only place the word survives as text."""
        self.assertIn('aria-label="bench"', self.mark())

    def test_it_spells_five_letters(self):
        """One path per letter of b-e-n-c-h. A missing path is a missing
        letter, and nothing else in this file would notice."""
        self.assertEqual(len(re.findall(r"<path\b", self.mark())), 5)

    def test_nothing_is_fetched_for_it(self):
        """The whole reason it is outlines. If this fails, the page grew a
        request (or an @font-face) that the acceptance forbids."""
        externals = set(re.findall(r'(?:href|src)="(https?://[^"]+)"', self.html))
        self.assertEqual(externals, KNOWN_EXTERNALS,
                         "board.html gained an external resource")
        for forbidden in ("@font-face", ".woff", ".ttf", "family=Zilla"):
            self.assertNotIn(forbidden, self.html,
                             f"the logo may not bring in {forbidden}")
        # Naming the source face in a comment is documentation; setting text
        # in it would be a font the page hasn't got.
        self.assertIsNone(re.search(r"font-family:[^;}\n]*Zilla", self.html),
                          "the mark is outlines — nothing is set in Zilla Slab")

    def test_it_takes_the_theme_s_own_ink(self):
        """currentColor is the theme mechanism: Night and Daylight both get
        --text without a second copy of the mark."""
        rule = re.search(r"\.brand \.mark\{([^}]*)\}", self.html)
        self.assertIsNotNone(rule, "board.html lost the .brand .mark rule")
        self.assertIn("fill:currentColor", rule.group(1))
        self.assertNotRegex(self.mark(), r"#[0-9a-fA-F]{3,6}\b",
                            "the mark must not bake a colour in")

    def test_it_is_not_coloured_like_a_state(self):
        """--accent means an agent is alive. A logo wearing it would be
        lying about the board twice a second."""
        for token in STATE_TOKENS:
            self.assertNotIn(token, self.mark())

    def test_one_token_sizes_it(self):
        """Scaling is a custom property, per the design — not two hard-coded
        sizes to keep in step."""
        self.assertRegex(self.html, r":root\{[^}]*--logo-h:", )
        rule = re.search(r"\.brand \.mark\{([^}]*)\}", self.html)
        self.assertIn("height:var(--logo-h)", rule.group(1))


class TheTabIcon(Fixture):
    def test_it_never_leaves_the_page(self):
        """Inline data: URI, so the tab is right on first paint and offline."""
        self.assertTrue(self.icon_href().startswith("data:image/svg+xml,"),
                        "the tab icon must stay inline")

    def test_it_is_the_wordmark_s_own_b(self):
        """The design's point about this mark is that one face does both jobs,
        so they can never drift apart. Here that means one outline: the icon's
        path and the wordmark's b are the same string, character for
        character."""
        icon = re.search(r"d='([^']+)'", self.icon_href())
        self.assertIsNotNone(icon, "the tab icon lost its b")
        word = re.search(r'<path id="mark-b" d="([^"]+)"', self.html)
        self.assertIsNotNone(word, "the wordmark lost its b")
        self.assertEqual(icon.group(1), word.group(1),
                         "the icon's b and the wordmark's b have drifted apart")

    def test_the_holes_in_the_b_are_holes(self):
        """The b's bowl is a counter, drawn as a second subpath. Without
        even-odd filling it fills solid and the letter becomes a blob."""
        self.assertIn("fill-rule='evenodd'", self.icon_href())
        self.assertIn('fill-rule="evenodd"', self.mark())


class TheNeighbours(Fixture):
    """The card is the logo only: everything beside it stays put."""

    def test_the_path_line_still_hangs_off_the_baseline(self):
        """Baseline alignment against the tasks-root path is the design's own
        arrangement, and it is what keeps the mono line optically seated."""
        rule = re.search(r"\.brand\{([^}]*)\}", self.html)
        self.assertIsNotNone(rule, "board.html lost the .brand rule")
        self.assertIn("align-items:baseline", rule.group(1))

    def test_the_path_line_is_untouched(self):
        self.assertIn(".brand .path{font-family:var(--mono);font-size:11.5px;"
                      "color:var(--dim)}", self.html)

    def test_the_header_row_is_untouched(self):
        """Same padding, same alignment, same order — the mark replaced a
        word, it did not relayout the header."""
        rule = re.search(r"\n  header\{([^}]*)\}", self.html)
        self.assertIsNotNone(rule, "board.html lost the header rule")
        self.assertIn("align-items:center", rule.group(1))
        self.assertIn("padding:10px 18px", rule.group(1))
        header = re.search(r"<header>.*?</header>", self.html, re.DOTALL)
        self.assertIsNotNone(header, "board.html lost its header")
        order = re.findall(r'id="(views|syncchip|livechip|themebtn|refresh)"',
                           header.group(0))
        self.assertEqual(order, ["views", "syncchip", "livechip",
                                 "themebtn", "refresh"])


if __name__ == "__main__":
    unittest.main()
