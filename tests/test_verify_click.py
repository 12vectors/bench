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
    where the mouse went. `seen` is what the click guard reports after
    each dispatch, in turn (task 176); unset, it reports nothing."""

    def __init__(self, answer, seen=()):
        self.answer = answer
        self.seen = list(seen)
        self.clicks = []
        self.expressions = []

    def eval(self, expression):
        self.expressions.append(expression)
        if expression == benchverify.CLICK_SEEN_JS:
            return self.seen.pop(0) if self.seen else None
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


# ── task 176: a repaint between the hit-test and the click ────────────

LANDED = {"landed": True, "clicked": True, "pressedOn": "button#x", "swallowed": 0,
          "events": ["pointerdown@button#x", "click@button#x"]}
MISSED = {"landed": False, "clicked": False, "pressedOn": "button#decoy", "swallowed": 5,
          "events": ["pointerdown@button#decoy (off target)"]}
LOST = {"landed": True, "clicked": False, "pressedOn": "button#x", "swallowed": 0,
        "events": ["pointerdown@button#x", "pointerup@button#x2 (off target)"]}


class TheClickIsGuarded(unittest.TestCase):
    """The Python half: what the click does with what the guard saw."""

    def setUp(self):
        self.mark = benchverify.harness_mark()

    def notes(self):
        return benchverify.harness_since(self.mark)

    def test_the_hit_test_arms_the_guard_in_the_same_round_trip(self):
        page = Canned({"x": 1, "y": 2}, [LANDED])
        page.click("#x")
        armed = page.expressions[0]
        self.assertIn("scrollIntoView", armed)
        self.assertIn("window.__benchClick", armed)
        self.assertIn('"#x"', armed)
        self.assertEqual(benchverify.CLICK_SEEN_JS, page.expressions[1])

    def test_a_press_on_the_target_is_one_click_and_no_note(self):
        page = Canned({"x": 1, "y": 2}, [LANDED])
        page.click("#x")
        self.assertEqual([(1, 2)], page.clicks)
        self.assertEqual([], self.notes())

    def test_a_swallowed_miss_is_aimed_again_and_named(self):
        page = Canned({"x": 1, "y": 2}, [MISSED, LANDED])
        page.click("#x")
        self.assertEqual(2, len(page.clicks), "one stray, one aimed again")
        kinds = [n["kind"] for n in self.notes()]
        self.assertEqual(["target-moved", "click-reaimed"], kinds)
        self.assertEqual("button#decoy", self.notes()[0]["pressedOn"])
        self.assertEqual("#x", self.notes()[0]["target"])

    def test_a_target_that_never_holds_still_is_refused_not_retried_forever(self):
        page = Canned({"x": 1, "y": 2}, [MISSED] * 10)
        with self.assertRaisesRegex(benchverify.CheckFailed,
                                    "target moved between hit-test and click on all 3"):
            page.click("#x")
        self.assertEqual(benchverify.CLICK_AIMS, len(page.clicks))

    def test_a_click_the_page_lost_after_the_press_landed_is_not_aimed_again(self):
        # the press reached the target and the page replaced it before the
        # release: that is the page's lost click, for the case to see —
        # the harness neither retries it nor hides it
        page = Canned({"x": 1, "y": 2}, [LOST, LANDED])
        page.click("#x")
        self.assertEqual(1, len(page.clicks))
        self.assertEqual(["lost-click"], [n["kind"] for n in self.notes()])

    def test_a_guard_that_cannot_answer_is_not_a_miss(self):
        page = Canned({"x": 1, "y": 2}, [None])
        page.click("#x")
        self.assertEqual(1, len(page.clicks))

    def test_a_dispatch_that_fails_disarms_the_guard(self):
        class Broken(Canned):
            def _click_at(self, x, y):
                raise benchverify.SetupError("the adapter timed out click")
        page = Broken({"x": 1, "y": 2})
        with self.assertRaises(benchverify.SetupError):
            page.click("#x")
        self.assertEqual(benchverify.CLICK_SEEN_JS, page.expressions[-1])

    def test_press_and_release_stay_unguarded(self):
        # an intentional press, repaint, release: the case drives the race,
        # so no guard may judge or swallow any part of it
        page = Canned({"x": 1, "y": 2})
        moves = []
        page._mouse = lambda phase, x, y: moves.append(phase)
        hit = page.press("#x")
        page.release(hit)
        self.assertEqual(["down", "up"], moves)
        self.assertTrue(all("__benchClick" not in e for e in page.expressions))


class Settled(unittest.TestCase):

    def test_a_settled_page_answers(self):
        page = Canned({"settled": True, "unsettled": []})
        self.assertTrue(page.settled(1)["settled"])
        self.assertIn("whenSettled", page.expression)

    def test_an_unsettled_page_names_what_was_still_going(self):
        mark = benchverify.harness_mark()
        page = Canned({"settled": False, "unsettled": ["a board reading is in flight"]})
        with self.assertRaisesRegex(benchverify.CheckFailed,
                                    "did not settle within 2s: a board reading is in flight"):
            page.settled(2)
        self.assertEqual("unsettled", benchverify.harness_since(mark)[-1]["kind"])

    def test_a_wait_that_times_out_names_its_condition(self):
        mark = benchverify.harness_mark()
        page = Canned(False)
        self.assertFalse(page.wait("window.never", 0.01))
        self.assertEqual("window.never", benchverify.harness_since(mark)[-1]["condition"])

    def test_settled_is_case_api_on_both_transports(self):
        self.assertIn("settled", benchverify.CASE_API)
        for transport in (benchverify.Browser, benchverify.RemoteBrowser):
            self.assertTrue(callable(getattr(transport, "settled", None)))


# A stand-in page in which the trusted gesture is dispatched the way a
# browser does it: capture listeners on the window first, then the page's
# own handler on the element under the point. `C.move` swaps the target
# and a decoy after the target is measured — a repaint landing between the
# hit-test and the press. `C.replace` has the page replace the target in
# its own pointerdown handler — the product losing its click.
GESTURE = r"""
globalThis.innerWidth = 1440; globalThis.innerHeight = 900;
globalThis.window = globalThis;
const C = %s;
const listeners = {};
window.addEventListener = (t, f) => { (listeners[t] = listeners[t] || []).push(f); };
window.removeEventListener = (t, f) => { listeners[t] = (listeners[t] || []).filter(x => x !== f); };
const hits = [];
const mk = (id, x) => ({ nodeType: 1, tagName: 'BUTTON', id, className: '', live: true,
  rect: { x, y: 100, width: 80, height: 20 }, scrollIntoView() {},
  getBoundingClientRect() { return { ...this.rect }; }, contains(t) { return t === this; } });
const body = { nodeType: 1, tagName: 'BODY', id: '', className: '', contains: () => true };
let target = mk('x', 100);
const decoy = mk('decoy', 300);
let pending = [];
let measured = 0;
target.getBoundingClientRect = function () {
  measured++;
  if (C.move === 'always' || (C.move && measured === 1)) {
    pending.push(() => { const a = target.rect.x; target.rect.x = decoy.rect.x; decoy.rect.x = a; });
  }
  return { ...this.rect };
};
const nodes = () => [decoy, target].filter(n => n.live);
globalThis.document = {
  querySelector: (sel) => (sel === '#x' ? target : null),
  elementFromPoint: (x, y) => nodes().find(n => x >= n.rect.x && x < n.rect.x + n.rect.width
                                          && y >= n.rect.y && y < n.rect.y + n.rect.height) || body,
};
function fire(type, at) {
  const ev = { type, target: at, isTrusted: true, stopped: false, prevented: false,
               preventDefault() { this.prevented = true; },
               stopImmediatePropagation() { this.stopped = true; } };
  for (const f of [...(listeners[type] || [])]) { f(ev); if (ev.stopped) return; }
  hits.push(`${type}@${at.id || at.tagName}`);
  if (type === 'pointerdown' && C.replace && at === target) {
    // the page repaints on the press: the control is a new node now
    const fresh = mk('x', target.rect.x); target.live = false; target = fresh;
  }
}
function gesture(x, y) {
  const down = document.elementFromPoint(x, y);
  fire('pointerdown', down); fire('mousedown', down);
  const up = document.elementFromPoint(x, y);
  fire('pointerup', up); fire('mouseup', up);
  fire('click', down === up ? up : body);
}
const out = [];
for (let aim = 0; aim < (C.aims || 1); aim++) {
  const hit = eval(C.guarded ? %s : %s);
  for (const p of pending.splice(0)) p();   // the repaint lands now
  gesture(hit.x, hit.y);
  out.push({ hit, seen: C.guarded ? eval(%s) : null });
  if (!C.guarded || !out[out.length - 1].seen || out[out.length - 1].seen.landed !== false) break;
}
console.log(JSON.stringify({ out, hits, armed: !!window.__benchClick,
                             left: Object.values(listeners).reduce((n, l) => n + l.length, 0) }));
"""


def gesture(case: dict):
    target = benchverify.CLICK_TARGET_JS % json.dumps("#x")
    armed = benchverify.ARMED_CLICK_JS % (target, json.dumps("#x"))
    src_text = GESTURE % (json.dumps(case), json.dumps(armed), json.dumps(target),
                          json.dumps(benchverify.CLICK_SEEN_JS))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "gesture.js"
        src.write_text(src_text, encoding="utf-8")
        res = subprocess.run([NODE, str(src)], capture_output=True, text=True)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


@unittest.skipUnless(NODE, "node is needed to run the click guard's page half")
class TheGuardOnTheTrustedPress(unittest.TestCase):
    """The race reproduced deterministically: the target moves after the
    hit-test and before the press. The unguarded click (the harness before
    task 176) activates whatever moved under the point; the guarded one
    activates nothing and says the target moved."""

    def test_reproduced_unguarded_the_stale_point_clicks_the_decoy(self):
        r = gesture({"move": True})
        self.assertIn("click@decoy", r["hits"], "the race reproduces without the guard")
        self.assertNotIn("click@x", r["hits"])

    def test_guarded_the_stray_press_is_swallowed_and_the_next_aim_lands(self):
        r = gesture({"move": True, "guarded": True, "aims": 3})
        self.assertEqual(2, len(r["out"]))
        first, second = r["out"][0]["seen"], r["out"][1]["seen"]
        self.assertIs(False, first["landed"])
        self.assertEqual("button#decoy", first["pressedOn"])
        self.assertGreater(first["swallowed"], 0)
        self.assertTrue(second["landed"] and second["clicked"])
        self.assertEqual(["pointerdown@x", "mousedown@x", "pointerup@x", "mouseup@x", "click@x"],
                         r["hits"], "the decoy's handlers never ran; the target was clicked once")
        self.assertFalse(r["armed"])
        self.assertEqual(0, r["left"], "every guard listener was removed")

    def test_guarded_a_target_that_always_moves_activates_nothing(self):
        r = gesture({"move": "always", "guarded": True, "aims": 3})
        self.assertEqual(3, len(r["out"]))
        self.assertTrue(all(o["seen"]["landed"] is False for o in r["out"]))
        self.assertEqual([], r["hits"])

    def test_guarded_a_click_the_page_loses_after_the_press_is_not_masked(self):
        r = gesture({"replace": True, "guarded": True, "aims": 3})
        self.assertEqual(1, len(r["out"]), "a landed press is never aimed again")
        seen = r["out"][0]["seen"]
        self.assertTrue(seen["landed"])
        self.assertFalse(seen["clicked"], "the page's lost click is reported as lost")
        self.assertEqual(0, seen["swallowed"], "nothing of the page's own gesture was swallowed")
        self.assertIn("pointerup@x", r["hits"], "the release reached the new node untouched")
        self.assertIn("click@BODY", r["hits"])

    def test_guarded_an_undisturbed_click_passes_through_untouched(self):
        r = gesture({"guarded": True, "aims": 3})
        self.assertEqual(1, len(r["out"]))
        self.assertTrue(r["out"][0]["seen"]["clicked"])
        self.assertEqual(0, r["out"][0]["seen"]["swallowed"])
        self.assertIn("click@x", r["hits"])


if __name__ == "__main__":
    unittest.main()
