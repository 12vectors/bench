"""The page boots from its asset snapshot (task 123, WP14).

The stylesheet left `board.html` for `web/board.css`, served from the
snapshot the backend booted with under `/web/<build>/`. This case drives
the real browser against the shared backend and checks what only a
browser can show:

- the page names the build `/api/state` reports, and its one stylesheet
  is that build's `board.css`, loaded and applied (a rule from it is in
  force on the header);
- every request the page made is to the board's own origin, except the
  Google Fonts stylesheet and font files the page has always linked —
  which are listed in the metrics by name rather than hidden, because the
  self-hosted font set is not bundled yet;
- the version chip is hidden while page, board and disk agree;
- a screenshot of the booted board, for the matched-view comparison with
  the build before the stylesheet moved.
"""

import re
import unittest

import benchverify as bv

S = bv.SESSION

GOOGLE_FONTS = re.compile(r"https://fonts\.(googleapis|gstatic)\.com/")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


class ThePageBootsFromItsSnapshot(unittest.TestCase):
    def setUp(self):
        b = S["browser"]
        b.viewport(1280, 800)
        b.goto("/")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its stage columns")
        self.b = b

    def test_1_the_page_and_its_stylesheet_are_one_build(self):
        b = self.b
        build = b.eval("document.querySelector('meta[name=\"bench-build\"]').content")
        state = b.eval("fetch('/api/state').then(r => r.json()).then(s => s.assets)")
        self.assertEqual(state["build"], build)
        self.assertEqual(state["mode"], "snapshot")
        self.assertFalse(state["differs"])
        sheets = b.eval("[...document.styleSheets].map(s => s.href || '(inline)')")
        own = [s for s in sheets if s and "/web/" in s]
        self.assertEqual(own, [b.eval("location.origin") + f"/web/{build}/board.css"],
                         sheets)
        # a rule that lives only in web/board.css is in force
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('header'))"
                                ".paddingLeft"), "18px")
        self.assertTrue(b.eval("document.querySelector('#buildchip').hidden"))
        S["metrics"]["assetBuild"] = {"build": build, "sheets": sheets}

    def test_2_nothing_is_fetched_off_the_board_but_the_linked_fonts(self):
        b = self.b
        urls = b.eval("performance.getEntriesByType('resource').map(e => e.name)")
        foreign = [u for u in urls if not u.startswith(b.eval("location.origin"))]
        unexplained = [u for u in foreign if not GOOGLE_FONTS.match(u)]
        S["metrics"]["assetForeignRequests"] = foreign
        self.assertEqual(unexplained, [],
                         "the page fetched from somewhere other than the board "
                         "and the fonts it has always linked")
        shot = S["shots"] / "assets-booted-board.png"
        b.screenshot(shot)
        S["required"].append(shot)


if __name__ == "__main__":
    unittest.main()
