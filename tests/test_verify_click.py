"""The browser driver's click (verify/benchverify.py, `PageOps.click`).

Mouse input reaches the page as viewport coordinates. The click used to
take an element's centre as it stood, so a control below the fold was
"clicked" at a point outside the window: nothing was hit, nothing was
raised, and the case failed seconds later with a timeout about whatever
the click should have done — "the draft was not taken from the JSON
field", "Edit opened no editor". Seven Settings cases failed that way.

So the click brings its target into view first, and refuses by name when
the point it would land on is not the target: hidden, inside a closed
fold, or covered by something else.

The Python half runs everywhere; the page half needs `node`, as the
other page-script tests here do.

    python3 -m unittest discover -s tests
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "verify"))

import benchverify  # noqa: E402

NODE = shutil.which("node")


class Canned(benchverify.PageOps):
    """A page whose answer to the click's probe is given, recording
    where the mouse went."""

    def __init__(self, answer):
        self.answer = answer
        self.clicks = []

    def eval(self, expression):
        self.expression = expression
        return self.answer

    def _click_at(self, x, y):
        self.clicks.append((x, y))


class TheClickRefusesRatherThanMisses(unittest.TestCase):
    def test_a_reachable_target_is_clicked_where_the_probe_says(self):
        page = Canned({"x": 10.5, "y": 20})
        page.click("#s-useraw")
        self.assertEqual([(10.5, 20)], page.clicks)
        self.assertIn('"#s-useraw"', page.expression)
        self.assertIn("scrollIntoView", page.expression)

    def test_a_missing_target_is_refused(self):
        page = Canned(None)
        with self.assertRaisesRegex(benchverify.CheckFailed, "not found"):
            page.click("#nothing")
        self.assertEqual([], page.clicks)

    def test_a_target_the_click_would_not_land_on_is_refused_by_name(self):
        page = Canned({"problem": "a click at its centre would land on header"})
        with self.assertRaisesRegex(benchverify.CheckFailed,
                                    "#s-useraw: a click .* header"):
            page.click("#s-useraw")
        self.assertEqual([], page.clicks)


def probe(case: str):
    """Run the click's page probe against a minimal stand-in DOM."""
    harness = f"""
globalThis.innerWidth = 1440; globalThis.innerHeight = 900;
const C = {case};
let rect = C.rect;
const other = {{tagName: 'HEADER', id: 'top', className: 'bar'}};
const el = {{
  scrolled: false,
  scrollIntoView() {{ this.scrolled = true; if (C.after) rect = C.after; }},
  getBoundingClientRect() {{ return rect; }},
  contains(x) {{ return x === el; }},
}};
globalThis.document = {{
  querySelector() {{ return C.absent ? null : el; }},
  elementFromPoint() {{ return C.covered ? other : el; }},
}};
const out = eval({json.dumps(benchverify.CLICK_TARGET_JS % json.dumps("#x"))});
console.log(JSON.stringify({{out, scrolled: el.scrolled}}));
"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.js"
        src.write_text(harness, encoding="utf-8")
        res = subprocess.run([NODE, str(src)], capture_output=True, text=True)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


@unittest.skipUnless(NODE, "node is needed to run the click's page probe")
class ThePageProbe(unittest.TestCase):
    def test_a_target_below_the_fold_is_scrolled_into_view_then_clicked(self):
        r = probe(json.dumps({"rect": {"x": 100, "y": 1800, "width": 80, "height": 20},
                              "after": {"x": 100, "y": 440, "width": 80, "height": 20}}))
        self.assertTrue(r["scrolled"])
        self.assertEqual({"x": 140, "y": 450}, r["out"])

    def test_a_target_still_outside_the_window_is_refused(self):
        r = probe(json.dumps({"rect": {"x": 100, "y": 1800, "width": 80, "height": 20}}))
        self.assertIn("outside the 1440x900 viewport", r["out"]["problem"])

    def test_a_hidden_target_is_refused(self):
        r = probe(json.dumps({"rect": {"x": 0, "y": 0, "width": 0, "height": 0}}))
        self.assertIn("no box", r["out"]["problem"])

    def test_a_covered_target_names_what_covers_it(self):
        r = probe(json.dumps({"rect": {"x": 10, "y": 10, "width": 80, "height": 20},
                              "covered": True}))
        self.assertIn("header#top.bar", r["out"]["problem"])

    def test_an_absent_target_is_none(self):
        r = probe(json.dumps({"absent": True}))
        self.assertIsNone(r["out"])


if __name__ == "__main__":
    unittest.main()
