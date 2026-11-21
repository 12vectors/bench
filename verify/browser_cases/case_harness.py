"""The harness's own pointer and settling, in a real browser (task 176).

These are about the browser suite, not about the board: each test puts a
small fixture of its own on the disposable board's page and drives it
through the same transport every other case rides (local CDP or the
remote Playwright adapter), with real, trusted mouse input.

1. A click's target moves between the hit-test and the press — a repaint
   landing between the harness's two round trips. Reproduced on purpose:
   the fixture swaps the target with a decoy right after the hit-test has
   measured it. The guarded click swallows the stray press (the decoy's
   handlers never run), aims again and clicks the target exactly once,
   and the harness trail names the miss.
2. The page loses a click after the press landed — the page replaces the
   pressed control in its own pointerdown handler. That is a product
   defect, and the harness does not hide it: no re-aim, no second click,
   recorded as `lost-click`.
3. An intentional press, repaint, release (press()/release(), which case
   work_base and the orchestrator cases rely on) is untouched: no guard,
   nothing swallowed.
4. settled() waits out a reading in flight, and says what it was waiting
   on when it does not settle.

The page-level halves are also tested under Node (tests/test_verify_click.py,
tests/node/requests_and_rendering.test.mjs).
"""

import unittest

import benchverify as bv

S = bv.SESSION

FIXTURE = r"""(() => {
  document.querySelector('#hx-fixture')?.remove();
  const box = document.createElement('div');
  box.id = 'hx-fixture';
  box.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:120px;z-index:2147483646;background:#fff';
  const mk = (id, x) => {
    const b = document.createElement('button');
    b.id = id; b.textContent = id;
    b.style.cssText = `position:absolute;left:${x}px;top:40px;width:120px;height:40px`;
    box.appendChild(b);
    return b;
  };
  window.__hx = { clicks: { target: 0, decoy: 0, fresh: 0 }, events: [] };
  const count = (name) => (e) => { if (e.isTrusted) window.__hx.clicks[name]++; };
  const log = (e) => { if (e.isTrusted) window.__hx.events.push(e.type + '@' + e.target.id); };
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
    box.addEventListener(t, log);
  const target = mk('hx-target', 40), decoy = mk('hx-decoy', 300);
  target.addEventListener('click', count('target'));
  decoy.addEventListener('click', count('decoy'));
  // decoy activation also by press, as a control that acts on pointerdown
  decoy.addEventListener('pointerdown', (e) => { if (e.isTrusted) window.__hx.decoyPressed = true; });
  document.body.appendChild(box);
  window.__hx.moveOnce = () => {
    // measured once more, then the next task finds it moved: a repaint
    // between the harness's hit-test and its press
    const real = target.getBoundingClientRect.bind(target);
    let armed = true;
    target.getBoundingClientRect = () => {
      const r = real();
      if (armed) {
        armed = false;
        queueMicrotask(() => { target.style.left = '300px'; decoy.style.left = '40px'; });
      }
      return r;
    };
  };
  window.__hx.replaceOnPress = () => {
    target.addEventListener('pointerdown', (e) => {
      if (!e.isTrusted) return;
      const fresh = target.cloneNode(true);
      fresh.id = 'hx-target';
      fresh.addEventListener('click', count('fresh'));
      target.replaceWith(fresh);
    }, { once: true });
  };
  return true;
})()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


class HarnessPointer(unittest.TestCase):

    def setUp(self):
        b = S["browser"]
        b.viewport(1280, 800)
        b.goto("/")
        if not b.wait("document.querySelectorAll('#board article.card').length > 0", 15):
            raise bv.SetupError("the board never drew a card")
        b.eval(FIXTURE)
        self.b = b
        self.mark = bv.harness_mark()

    def tearDown(self):
        self.b.eval("(() => { document.querySelector('#hx-fixture')?.remove(); "
                    "window.__benchClick && window.__benchClick.off(); return true; })()")

    def hx(self):
        return self.b.eval("window.__hx")

    def notes(self):
        return [n for n in bv.harness_since(self.mark)]

    def test_an_undisturbed_click_reaches_its_target_once(self):
        self.b.click("#hx-target")
        hx = self.hx()
        self.assertEqual({"target": 1, "decoy": 0, "fresh": 0}, hx["clicks"])
        self.assertEqual(["pointerdown@hx-target", "mousedown@hx-target",
                          "pointerup@hx-target", "mouseup@hx-target", "click@hx-target"],
                         hx["events"])
        self.assertEqual([], self.notes())
        self.assertFalse(self.b.eval("!!window.__benchClick"), "the guard was left armed")

    def test_a_target_that_moves_after_the_hit_test_is_clicked_not_its_decoy(self):
        self.b.eval("window.__hx.moveOnce(), true")
        self.b.click("#hx-target")
        hx = self.hx()
        self.assertEqual(0, hx["clicks"]["decoy"], "the stray press activated the decoy")
        self.assertFalse(hx.get("decoyPressed"), "the decoy's pointerdown handler ran")
        self.assertEqual(1, hx["clicks"]["target"], "the target was not clicked exactly once")
        kinds = [n["kind"] for n in self.notes()]
        self.assertEqual(["target-moved", "click-reaimed"], kinds,
                         "the miss was not named in the harness trail")
        self.assertIn("hx-decoy", self.notes()[0]["pressedOn"])

    def test_a_click_the_page_loses_after_the_press_is_reported_not_hidden(self):
        self.b.eval("window.__hx.replaceOnPress(), true")
        self.b.click("#hx-target")
        hx = self.hx()
        kinds = [n["kind"] for n in self.notes()]
        # one gesture, never aimed again and never swallowed: whatever the
        # browser makes of a press whose control was replaced is the page's
        self.assertEqual(1, hx["events"].count("pointerdown@hx-target"), hx["events"])
        self.assertNotIn("target-moved", kinds)
        self.assertLessEqual(hx["clicks"]["target"] + hx["clicks"]["fresh"], 1,
                             "the harness clicked the replacement again")
        if not (hx["clicks"]["target"] or hx["clicks"]["fresh"]):
            self.assertEqual(["lost-click"], kinds, "a lost click went unrecorded")

    def test_an_intentional_press_repaint_release_is_untouched(self):
        b = self.b
        hit = b.press("#hx-target")
        # the page repaints the pressed control between press and release
        b.eval("(() => { const t = document.querySelector('#hx-target');"
               " t.textContent = 'repainted'; return true; })()")
        b.release(hit)
        hx = self.hx()
        self.assertEqual(1, hx["clicks"]["target"],
                         "a press/release around an in-place repaint is one click")
        self.assertFalse(b.eval("!!window.__benchClick"), "press armed a guard")
        self.assertEqual([], self.notes())


class HarnessSettled(unittest.TestCase):

    def test_settled_waits_out_a_reading_and_a_paint(self):
        b = S["browser"]
        b.viewport(1280, 800)
        b.goto("/")
        b.wait("document.querySelectorAll('#board article.card').length > 0", 15)
        b.eval("(() => { loadState(); loadState(); scheduleRender(); return true; })()")
        out = b.settled(15)
        self.assertTrue(out["settled"])
        self.assertTrue(b.eval("pageSettled().settled"))

    def test_settled_names_what_never_settled(self):
        b = S["browser"]
        b.goto("/")
        b.wait("document.querySelectorAll('#board article.card').length > 0", 15)
        mark = bv.harness_mark()
        # a scroller that never comes to rest never settles; say so, bounded
        b.eval("(() => { const s = document.createElement('div'); s.id = 'hx-spin';"
               " s.style.cssText = 'position:fixed;left:0;top:0;width:80px;height:40px;overflow:auto';"
               " s.innerHTML = '<div style=\"height:4000px\"></div>'; document.body.appendChild(s);"
               " window.__spin = setInterval(() => { s.scrollTop = (s.scrollTop + 37) % 3000 + 1; }, 4);"
               " return true; })()")
        try:
            with self.assertRaisesRegex(bv.CheckFailed, "still scrolling or resizing"):
                b.settled(0.6)
        finally:
            b.eval("(() => { clearInterval(window.__spin);"
                   " document.querySelector('#hx-spin')?.remove(); return true; })()")
        self.assertEqual("unsettled", bv.harness_since(mark)[-1]["kind"])


if __name__ == "__main__":
    unittest.main()
