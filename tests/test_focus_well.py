"""The Focus "Right now" well and the wells' visual grammar (task 08).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants: the ones that, if broken, would bring
back the decorative chevron or lose the fold's keyed state across SSE
re-renders.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"


class FocusWellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_chevron_only_where_clicking_does_something(self):
        """One glyph, one meaning: every › worn by a well's lead span must
        sit inside the wellfold disclosure — no static well may wear it."""
        chevrons = [m.start() for m in
                    re.finditer(r'<span class="lead">›', self.html)]
        self.assertTrue(chevrons, "the disclosure well lost its chevron")
        for pos in chevrons:
            window = self.html[max(0, pos - 300):pos]
            self.assertIn('class="fold wellfold"', window,
                          "a well outside the wellfold disclosure wears ›, "
                          "but clicking it does nothing")
            self.assertIn("<summary>", window,
                          "the › must be inside the disclosure's summary")

    def test_static_wells_wear_a_neutral_lead(self):
        """The board cards' live line and the empty-focus well are plain
        divs; after the audit they lead with · (or the drive's ✳)."""
        for m in re.finditer(r'<div class="well[^>]*"><span class="lead">(.)',
                             self.html):
            if self.html[:m.start()].rstrip("` +\n").endswith("<summary>"):
                continue  # the wellfold's own summary — the earned ›
            self.assertIn(m.group(1), "·✳",
                          f"static well leads with {m.group(1)!r}; "
                          "only the wellfold summary may use ›")

    def test_report_detail_renders_preformatted(self):
        """The full report opens as machine-adjacent prose: a <pre> right
        after the wellfold's summary, scrollable when long."""
        self.assertRegex(self.html,
                         r"</summary>` \+\s*`<pre>\$\{esc\(last\.detail\)\}</pre></details>",
                         "the wellfold must render last.detail in a <pre>")
        self.assertRegex(self.html, r"\.fold\.wellfold pre\{[^}]*max-height",
                         "long reports need a scroll bound on the fold's pre")

    def test_open_state_is_keyed_not_dom_only(self):
        """Focus re-renders every few seconds over SSE; the fold must be
        re-created from S.openFolds and write back on toggle, exactly like
        the Sessions timeline's folds."""
        m = re.search(r"if \(last && last\.detail\) \{(.*?)\}\s*else",
                      self.html, re.S)
        self.assertIsNotNone(m, "the Focus well no longer branches on detail")
        for needle in ("data-key", "S.openFolds.has"):
            self.assertIn(needle, m.group(1),
                          f"the wellfold lost {needle}: open state would "
                          "snap shut on the next re-render")
        self.assertIn("#cgrid details.fold", self.html,
                      "renderFocus must re-attach toggle listeners so "
                      "toggles land back in S.openFolds")

    def test_sessions_timeline_fold_unchanged(self):
        """The timeline's own fold mechanism is the pattern being reused,
        not replaced."""
        self.assertIn("function fold(key, label, body)", self.html)
        self.assertIn("ev.kind === 'report' ? 'the report'", self.html)
        self.assertIn("#ftl details", self.html)


if __name__ == "__main__":
    unittest.main()
