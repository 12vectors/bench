"""A redraw keeps where you were looking (task 52).

`renderBoard()` starts with `board.innerHTML = ''` and rebuilds every
column, so each column's scrolling `.drop` was a brand-new node on every
pass — and a new node's scrollTop is 0. `render()` runs on every SSE frame
and a working agent emits an event per tool use, so a column being read
snapped back to the top several times a minute. The same wipe threw away
`#board`'s horizontal position, the session timeline, the sessions rail,
the Focus view and the activity log's place when it was not stuck to the
bottom.

The fix is a mark-and-restore pair around each wipe, keyed by a stable
name rather than a node (`v:col:<slug>`, `v:tl:<sid>`), clamped so a
column that lost cards lands at its new bottom instead of past it.

board.html is a single file with inline JS and no frontend test runner, so
this suite does what tests/test_drawer_markdown.py does: it lifts the
scroll helpers straight out of the page and runs them under node, where
their actual behaviour is what to assert on. Node is not a dependency of
bench itself, so those checks skip when it is absent; the source-level
invariants at the bottom always run and are in the same style as the
board's other `test_*.py` checks on board.html.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "manager" / "core" / "board.html"
NODE = shutil.which("node")

HTML = BOARD.read_text(encoding="utf-8")


def lift(pattern: str, what: str) -> str:
    m = re.search(pattern, HTML, re.M | re.S)
    assert m, f"board.html lost {what}"
    return m.group(0)


HELPERS = (
    lift(r"^const scrollMarks = \{\};\n", "its scroll-mark registry")
    + lift(r"^function markScroll\(key, el\) \{\n.*?\n\}\n", "markScroll()")
    + lift(r"^function restoreScroll\(key, el\) \{\n.*?\n\}\n", "restoreScroll()")
    + lift(r"^function forgetViewScroll\(\) \{\n.*?\n\}\n", "forgetViewScroll()")
)

# A scroller with none of the DOM's own clamping, so what the page writes
# is exactly what the assertions see. Its geometry can be changed between
# the mark and the restore — that is a redraw whose content moved on.
FAKE = """
function scroller({top = 0, left = 0, h = 900, ch = 300, w = 0, cw = 0} = {}) {
  return {scrollTop: top, scrollLeft: left,
          scrollHeight: h, clientHeight: ch, scrollWidth: w, clientWidth: cw};
}
const out = {};
"""

CASES = """
// a column read halfway down, redrawn with the same content
{
  const col = scroller({top: 240});
  markScroll('v:col:done', col);
  col.scrollTop = 0;                       // the wipe: a brand-new node
  restoreScroll('v:col:done', col);
  out.same_content = col.scrollTop;
}

// the column lost cards while it was scrolled to the bottom: land at the
// new bottom, not past it
{
  const col = scroller({top: 600, h: 900, ch: 300});
  markScroll('v:col:review', col);
  col.scrollTop = 0; col.scrollHeight = 400;
  restoreScroll('v:col:review', col);
  out.shorter = col.scrollTop;
}

// everything left the column: it is now shorter than its own viewport
{
  const col = scroller({top: 600, h: 900, ch: 300});
  markScroll('v:col:empty', col);
  col.scrollTop = 0; col.scrollHeight = 40;
  restoreScroll('v:col:empty', col);
  out.emptied = col.scrollTop;
}

// #board scrolled sideways to watch review/
{
  const board = scroller({left: 520, w: 2400, cw: 1200, h: 0, ch: 0});
  markScroll('v:board', board);
  board.scrollLeft = 0;
  restoreScroll('v:board', board);
  out.sideways = board.scrollLeft;
  board.scrollLeft = 0; board.scrollWidth = 1400;
  restoreScroll('v:board', board);
  out.sideways_clamped = board.scrollLeft;
}

// a key with no mark writes nothing at all
{
  const fresh = scroller({top: 77});
  restoreScroll('v:tl:never-seen', fresh);
  out.unmarked = fresh.scrollTop;
}

// two columns keep their own places
{
  const a = scroller({top: 120}), b = scroller({top: 480, h: 1200});
  markScroll('v:col:backlog', a);
  markScroll('v:col:to-do', b);
  a.scrollTop = 0; b.scrollTop = 0;
  restoreScroll('v:col:backlog', a);
  restoreScroll('v:col:to-do', b);
  out.two_columns = [a.scrollTop, b.scrollTop];
}

// a timeline is keyed by session: its own place comes back, and another
// session's does not borrow it
{
  const tl = scroller({top: 310, h: 4000});
  markScroll('v:tl:sess-a', tl);
  tl.scrollTop = 0;
  restoreScroll('v:tl:sess-a', tl);
  out.timeline_same = tl.scrollTop;
  tl.scrollTop = 0;
  restoreScroll('v:tl:sess-b', tl);
  out.timeline_other = tl.scrollTop;
}

// switching views forgets view-scoped places; the activity log spans
// every view, so it keeps its own
{
  const col = scroller({top: 240}), log = scroller({top: 180});
  markScroll('v:col:done', col);
  markScroll('log', log);
  forgetViewScroll();
  col.scrollTop = 0; log.scrollTop = 0;
  restoreScroll('v:col:done', col);
  restoreScroll('log', log);
  out.after_view_switch = [col.scrollTop, log.scrollTop];
}

// an agent emitting events continuously: one redraw per tool use, and the
// column can still be read from top to bottom
{
  const col = scroller({top: 240});
  for (let i = 0; i < 60; i++) {
    markScroll('v:col:in-progress', col);
    col.scrollTop = 0;                     // the wipe, once per SSE frame
    col.scrollHeight += 40;                // and a card grew a live line
    restoreScroll('v:col:in-progress', col);
  }
  out.sixty_frames = col.scrollTop;
}

process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PageParsesTests(unittest.TestCase):
    """The page is one file of inline JS with no build step, so a stray
    brace anywhere in it is a blank board and no error a test would see.
    This change reached into six renderers; the cheapest guard is to hand
    the whole script to a parser."""

    def test_the_inline_script_parses(self):
        m = re.search(r"<script>\n(.*)\n</script>", HTML, re.S)
        self.assertIsNotNone(m, "board.html lost its inline script")
        script = m.group(1)
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp) / "board.js"
            js.write_text(script, encoding="utf-8")
            run = subprocess.run([NODE, "--check", str(js)],
                                 capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


@unittest.skipUnless(NODE, "node is not installed")
class ScrollHelperTests(unittest.TestCase):
    """The page's own markScroll/restoreScroll, run for real."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        js = Path(cls._dir.name) / "scroll.js"
        js.write_text(HELPERS + FAKE + CASES, encoding="utf-8")
        run = subprocess.run([NODE, str(js)], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        cls.out = json.loads(run.stdout)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_a_column_keeps_its_place(self):
        """The reported bug: halfway down a column, an SSE frame redraws
        the board, and you are still halfway down."""
        self.assertEqual(self.out["same_content"], 240)

    def test_a_shorter_column_lands_at_its_new_bottom(self):
        """Clamp rather than guess: 900/300 gave a floor of 600, 400/300
        gives 100, and that is where a remembered 600 belongs."""
        self.assertEqual(self.out["shorter"], 100)

    def test_a_column_shorter_than_its_viewport_lands_at_the_top(self):
        """scrollHeight - clientHeight goes negative when the content no
        longer fills the box; the restore must not write a negative."""
        self.assertEqual(self.out["emptied"], 0)

    def test_the_board_keeps_its_horizontal_place(self):
        """#board is overflow-x:auto, so a wide board scrolled right to
        watch review/ is the same defect and gets the same clamp."""
        self.assertEqual(self.out["sideways"], 520)
        self.assertEqual(self.out["sideways_clamped"], 200)

    def test_an_unmarked_element_is_left_alone(self):
        """No mark, no write — a first paint must not be dragged anywhere,
        which is also what opens a newly selected card at the top."""
        self.assertEqual(self.out["unmarked"], 77)

    def test_sixty_redraws_do_not_drift(self):
        """The mark is re-taken every frame, so a busy board must not creep
        the reader up or down over a run of them."""
        self.assertEqual(self.out["sixty_frames"], 240)

    def test_places_are_kept_per_key(self):
        self.assertEqual(self.out["two_columns"], [120, 480])

    def test_a_timeline_is_keyed_by_session(self):
        """Reading back through a long run survives the events it is still
        producing; a different session is a different key, so it opens at
        the top rather than borrowing."""
        self.assertEqual(self.out["timeline_same"], 310)
        self.assertEqual(self.out["timeline_other"], 0)

    def test_a_view_switch_drops_view_places_but_not_the_log(self):
        """Coming back to a view is a fresh look, not a stale position
        from the previous visit. The log is not in a view."""
        self.assertEqual(self.out["after_view_switch"], [0, 180])


class RenderersKeepPlaceTests(unittest.TestCase):
    """Source-level invariants: the renderers that wipe a scrolling
    element must mark before the wipe and restore after it."""

    @classmethod
    def setUpClass(cls):
        cls.html = HTML

    def body(self, name: str) -> str:
        return lift(rf"^function {name}\(.*?\n\}}\n", f"{name}()")

    def order(self, body: str, *needles: str) -> None:
        at = -1
        for needle in needles:
            found = body.find(needle, at + 1)
            self.assertNotEqual(found, -1, f"missing {needle!r}")
            self.assertGreater(found, at, f"{needle!r} is out of order")
            at = found

    def test_board_marks_before_the_wipe_and_restores_after(self):
        body = self.body("renderBoard")
        self.order(body,
                   "markScroll('v:board', board)",
                   "markScroll('v:col:'",
                   "board.innerHTML = ''",
                   "restoreScroll('v:board', board)",
                   "restoreScroll('v:col:'")

    def test_columns_are_keyed_by_their_stage_slug(self):
        """The node does not survive the wipe, so the key must be
        something that does — the slug already on the drop element."""
        body = self.body("renderBoard")
        self.assertIn("d.dataset.stage", body)
        self.assertIn("drop.dataset.stage = stage.slug", body)

    def test_the_timeline_marks_and_restores_around_its_wipe(self):
        body = self.body("renderTimeline")
        self.order(body,
                   "markScroll('v:tl:' + sid, tl)",
                   "tl.innerHTML =",
                   "restoreScroll('v:tl:' + sid, tl)")

    def test_the_sessions_rail_marks_and_restores_around_its_wipe(self):
        body = self.body("renderFlight")
        self.order(body,
                   "markScroll('v:rail', rail)",
                   "rail.innerHTML =",
                   "restoreScroll('v:rail', rail)")

    def test_focus_restores_on_every_path_out(self):
        """renderFocus() returns early when there is no session; both exits
        have to put the view back."""
        body = self.body("renderFocus")
        self.assertIn("markScroll('v:focus', view)", body)
        self.assertIn("markScroll('v:cstrip', strip)", body)
        self.assertEqual(body.count("putBack();"), 2,
                         "both of renderFocus()'s exits must restore")

    def test_the_drawer_is_keyed_per_document(self):
        body = self.body("renderDrawer")
        self.order(body,
                   "markScroll(S.drawerKey, body)",
                   "S.drawerKey = drawerKey()")
        self.assertEqual(body.count("restoreScroll(key, body);"), 2,
                         "both of renderDrawer()'s painted paths must restore")

    # ── the two behaviours that were already right ──

    def test_the_log_still_sticks_to_the_bottom(self):
        """S.logStick is the older, deliberate behaviour: follow the newest
        line when the reader was at the bottom. It runs after the restore,
        so it wins over the mark."""
        body = self.body("renderLog")
        self.order(body,
                   "markScroll('log', body)",
                   "body.innerHTML =",
                   "restoreScroll('log', body)",
                   "if (S.logStick) body.scrollTop = body.scrollHeight;")

    def test_the_log_scroll_listener_still_sets_the_stick(self):
        block = lift(r"/\* the activity log:.*?\n\{.*?\n\}", "the log block")
        self.assertIn("S.logStick = body.scrollTop + body.clientHeight", block)

    def test_selecting_another_card_still_returns_the_drawer_to_the_top(self):
        body = self.body("showDetail")
        self.assertIn("if (changed) $('#drawerbody').scrollTop = 0;", body)
        self.assertIn("S.selected.file !== task.file", body)

    def test_opening_a_reference_file_still_returns_the_drawer_to_the_top(self):
        body = lift(r"^async function openExtra\(.*?\n\}\n", "openExtra()")
        self.assertIn("$('#drawerbody').scrollTop = 0;", body)

    # ── the marks do not outlive the visit ──

    def test_switching_views_forgets_view_places(self):
        body = self.body("setView")
        self.assertIn("forgetViewScroll()", body)
        self.assertLess(body.index("forgetViewScroll()"), body.index("render()"),
                        "forget before the redraw, not after it")

    def test_every_view_scoped_key_carries_the_prefix(self):
        """forgetViewScroll() drops exactly the `v:` keys, so a key that
        belongs to a view and forgets the prefix would quietly survive a
        view switch. The log is the one key that should."""
        keys = set(re.findall(r"(?:mark|restore)Scroll\('([^']*)'", self.html))
        unprefixed = {k for k in keys if not k.startswith("v:")}
        self.assertEqual(unprefixed, {"log"}, "unexpected view-less key")
        # the drawer's key is computed rather than written at the call site
        parts = [s for s in re.findall(r"'([^']*)'", self.body("drawerKey"))
                 if ":" in s]
        self.assertTrue(parts, "drawerKey() builds no key at all")
        for literal in parts:
            self.assertTrue(literal.startswith("v:"),
                            f"drawerKey() builds a view-less key: {literal!r}")

    def test_no_renderer_wipes_a_scroller_without_a_mark(self):
        """The scrolling elements the CSS declares, against the ones the
        renderers carry. A new one added here without a mark is the bug
        coming back."""
        carried = {
            "#board": "markScroll('v:board'",        # the columns, sideways
            ".drop": "markScroll('v:col:'",          # one column, down
            ".tl": "markScroll('v:tl:'",             # #ftl, a session timeline
            ".f-rail": "markScroll('v:rail'",        # #frail, the sessions list
            "#view-focus": "markScroll('v:focus'",
            ".c-strip": "markScroll('v:cstrip'",     # #cstrip, the stage strip
            "#logbody": "markScroll('log'",
            "#drawerbody": "markScroll(S.drawerKey",
        }
        declared = set()
        for sel, rule in re.findall(r"^  ([#.][\w-]+)\{([^}]*)\}", self.html, re.M):
            if "overflow-y:auto" in rule or "overflow-x:auto" in rule:
                declared.add(sel)
        self.assertEqual(
            declared - set(carried), set(),
            "a scrolling element no renderer keeps the place of")
        for sel, mark in carried.items():
            if sel in declared:
                self.assertIn(mark, self.html,
                              f"{sel} scrolls but nothing marks it")


if __name__ == "__main__":
    unittest.main()
