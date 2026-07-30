"""The activity log's resize grip actually resizes (task 04).

The grip's drag handlers wrote #logbody's inline height while the CSS said
flex:1 — so the flex algorithm sized the element and the write was dead.
Worse, mouseup persisted a re-read offsetHeight (the flex-computed value),
silently overwriting the remembered size with the status quo.

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants over the rule and the handler block: the
ones that, if broken, would decouple the written height from the laid-out
height again, or bring back the offsetHeight re-read that corrupted the
stored size.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"


class LogResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

        m = re.search(r"#logbody\{([^}]*)\}", cls.html)
        assert m, "board.html lost its #logbody rule"
        cls.logbody = m.group(1).replace(" ", "")

        # the whole activity-log drag block, from its banner comment to the
        # drawer of the next top-level comment
        m = re.search(
            r"/\* the activity log:.*?\n\{(.*?)\n\}", cls.html, re.S)
        assert m, "board.html lost the activity-log resize block"
        cls.logjs = m.group(1)

        m = re.search(r"/\* the drawer:.*?\n\{(.*?)\n\}", cls.html, re.S)
        assert m, "board.html lost the drawer resize block"
        cls.drawerjs = m.group(1)

    # ── the height the handler writes is the height the layout uses ──

    def test_logbody_height_is_authoritative(self):
        """flex:none (0 0 auto): the inline height the drag handlers write
        sizes the element. flex:1 (basis 0% + grow) is what made the write
        dead — the flex algorithm never consulted the height property."""
        self.assertIn("flex:none", self.logbody,
                      "#logbody must not be flex-sized; the grip writes "
                      "style.height and the layout must honour it")
        self.assertNotIn("flex:1", self.logbody)

    def test_logbody_growth_is_guarded(self):
        """With height authoritative again, a huge saved value or a shrunk
        window must not overflow: CSS max-height mirrors the drag clamp's
        60vh ceiling."""
        self.assertIn("max-height:60vh", self.logbody)

    def test_drag_clamp_bounds_survive(self):
        """The advertised 80px–60vh drag bounds live in the handler block."""
        self.assertRegex(self.logjs, r"Math\.max\(px,\s*80\)")
        self.assertRegex(self.logjs, r"innerHeight\s*\*\s*0\.6\b")

    # ── the saved size round-trips instead of being overwritten ──

    def test_mouseup_persists_the_computed_height(self):
        """mouseup must save the value the drag computed. Re-reading
        offsetHeight is the old bug: while the write was dead it persisted
        the flex-computed height, corrupting the stored size (856 → 156)."""
        save = re.search(r"setItem\('bench-log-h',\s*([^)]*)\)", self.logjs)
        self.assertIsNotNone(save, "the log block must persist bench-log-h")
        self.assertNotIn("offsetHeight", save.group(1),
                         "persist the drag-computed value, not a re-read")

    def test_restore_uses_the_drag_channel_and_clamp(self):
        """The load-time restore writes the same property the drag writes,
        through the same clamp — a saved value from a taller window must
        not restore beyond today's 60vh."""
        self.assertRegex(
            self.logjs, r"clamp\([^)]*getItem\('bench-log-h'",
            "the restore must clamp the saved height")
        writes = re.findall(r"body\.style\.(\w+)\s*=", self.logjs)
        self.assertTrue(writes, "the block must size #logbody")
        self.assertEqual(set(writes), {"height"},
                         "restore and drag must write the same property")

    # ── the drawer grip: same interaction family, must not regress ──

    def test_drawer_width_is_still_authoritative(self):
        """The drawer handlers write #drawer's own width; #drawer is
        position:fixed, so no flex sizing competes with the write."""
        drawer = re.search(r"#drawer\{([^}]*)\}", self.html).group(1)
        self.assertIn("position:fixed", drawer.replace(" ", ""))
        self.assertIn("panel.style.width", self.drawerjs)
        self.assertIn("setItem('bench-drawer-w'", self.drawerjs)


if __name__ == "__main__":
    unittest.main()
