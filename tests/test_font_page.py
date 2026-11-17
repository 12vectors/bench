"""The page links the board's own fonts and nothing off the board (task 123).

The page-reading half of tests/test_font_bundle.py, read through
`tests.pagesource` and with no regular-expression lift of its own (the
ratchet in tests/test_pagesource.py only lets lifts shrink): the shell
links `fonts/plex.css` from its own build, no asset names a url it would
fetch, and `tests/fixtures/page-symbols.json` is the page's own
inventory of non-ASCII characters, classified against the ranges the
font stylesheet declares.

    python3 -m unittest tests.test_font_page -v
"""

from __future__ import annotations

import json
import unittest

from tests import pagesource
from tests.test_font_bundle import FONTS, INVENTORY, covered, faces

# What a fetched url looks like in a stylesheet or the shell. Text naming
# an upstream url (a licence, a provenance record, a comment) is not here.
FETCHES = ("url(http", "url('http", 'url("http', "url( http", "@import",
           'href="http', "href='http", "href=http", 'src="http', "src='http",
           "src=http")


class ThePageLinksTheBundle(unittest.TestCase):
    def test_the_shell_links_plex_css_from_its_own_build_before_board_css(self):
        shell = pagesource.shell()
        fonts = shell.find('href="/web/__BUILD__/fonts/plex.css"')
        own = shell.find('href="/web/__BUILD__/board.css"')
        self.assertGreater(fonts, 0)
        self.assertGreater(own, fonts)
        self.assertNotIn("fonts.googleapis.com", shell)
        self.assertNotIn("fonts.gstatic.com", shell)
        self.assertNotIn('rel="preconnect"', shell)

    def test_the_stylesheet_asks_for_the_families_plex_css_declares(self):
        page = pagesource.page()
        self.assertIn("'IBM Plex Sans'", page)
        self.assertIn("'IBM Plex Mono'", page)

    def test_no_asset_references_a_url_off_the_board(self):
        """The page fetches nothing from anyone else: neither the shell nor
        any stylesheet or script under web/ — the font stylesheet included
        — names an http(s) url it would load."""
        for fetch in FETCHES:
            self.assertNotIn(fetch, pagesource.page())
        web = pagesource.WEB
        for path in sorted(web.rglob("*")):
            if path.is_file() and path.suffix in (".css", ".js", ".mjs"):
                text = path.read_text(encoding="utf-8")
                for fetch in FETCHES:
                    self.assertNotIn(fetch, text, f"{path.relative_to(web)}")


class TheInventoryOfThePageSymbols(unittest.TestCase):
    """Every non-ASCII codepoint the page's source holds is listed, and the
    list says which a Plex range declares and which a system font has
    always drawn. A symbol added to the page has to be added there, which
    is the point: verify/browser_cases/case_fonts.py then probes it."""

    def test_the_inventory_is_the_page_s_own(self):
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        declared = faces((FONTS / "plex.css").read_text(encoding="utf-8"))
        excluded = []
        for span in inventory["excluded"]:
            low, _, high = span.partition("-")
            excluded.append((int(low, 16), int(high or low, 16)))
        page = {ord(c) for c in pagesource.page() if ord(c) > 0x7F}
        page = {cp for cp in page if not covered(cp, excluded)}
        sans = [r for f in declared if f["family"] == "IBM Plex Sans" for r in f["ranges"]]
        mono = [r for f in declared if f["family"] == "IBM Plex Mono" for r in f["ranges"]]
        want = {"declared": [], "monoOnly": [], "system": []}
        for cp in sorted(page):
            if covered(cp, sans) and covered(cp, mono):
                want["declared"].append(f"{cp:04X}")
            elif covered(cp, mono):
                want["monoOnly"].append(f"{cp:04X}")
            elif covered(cp, sans):
                self.fail(f"U+{cp:04X} is declared by Sans alone; add a class for it")
            else:
                want["system"].append(f"{cp:04X}")
        for key, codes in want.items():
            self.assertEqual(inventory[key], codes,
                             f"tests/fixtures/page-symbols.json {key!r} is not the page's")

    def test_the_excluded_codepoints_are_only_the_markdown_sentinels(self):
        """U+E000–U+E003 are placeholders md() swaps out; nothing draws
        them, and no other private-use character is in the page."""
        page = pagesource.page()
        start = page.index("\nfunction md(")
        ends = [i for i in (page.find(f"\n{w}", start + 1)
                            for w in ("async function ", "function ", "const ", "let "))
                if i > 0]
        outside = page[:start] + page[min(ends):]
        for cp in range(0xE000, 0xF900):
            self.assertNotIn(chr(cp), outside,
                             f"U+{cp:04X} is used outside md(); classify it in "
                             f"tests/fixtures/page-symbols.json")
        for cp in range(0xE004, 0xF900):
            self.assertNotIn(chr(cp), page)


if __name__ == "__main__":
    unittest.main()
