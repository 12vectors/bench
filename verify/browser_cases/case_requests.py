"""Requests, announcements and the connection chip, in a real browser (task 125, WP16).

The policies themselves are driven as real modules under Node
(tests/node/requests_and_rendering.test.mjs). What needs a browser is the
page around them:

1. a burst of state reads in the running page is at most one request in
   flight and one queued, and the page is painted from the newest;
2. a state read that fails keeps the last board drawn, says so on the
   connection chip and in the alert region, and the next good read clears
   it;
3. the connection chip is a keyboard stop with a visible focus ring, and
   Enter opens Sessions;
4. an error is announced through the persistent alert region, which sits
   outside every root a redraw demolishes, so a repaint does not remove it.
"""

import time
import unittest

import benchverify as bv

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1280, height=800):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


# Wraps the page's fetch to count /api/state requests and the most that
# were ever out at once. The page's own fetch looks the global up at call
# time, so this is what its reads go through.
COUNT = """(() => {
  const real = window.__realFetch || window.fetch;
  window.__realFetch = real;
  window.__state = { asked: 0, out: 0, most: 0 };
  window.fetch = async (url, opts) => {
    const isState = String(url).startsWith('/api/state');
    if (isState) { __state.asked++; __state.out++; __state.most = Math.max(__state.most, __state.out); }
    try { return await real(url, opts); }
    finally { if (isState) __state.out--; }
  };
  return true;
})()"""

RESTORE = "(() => { if (window.__realFetch) window.fetch = window.__realFetch; return true; })()"


class StateReads(unittest.TestCase):

    def tearDown(self):
        S["browser"].eval(RESTORE)

    def test_a_burst_is_one_request_in_flight_and_one_queued(self):
        b = load()
        b.eval(COUNT)
        b.eval("Promise.all(Array.from({ length: 30 }, () => loadState())).then(() => true)")
        got = b.eval("window.__state")
        self.assertLessEqual(got["most"], 1, f"more than one state read out at once: {got}")
        self.assertLessEqual(got["asked"], 2, f"thirty asks were more than two reads: {got}")
        self.assertGreaterEqual(got["asked"], 1)
        self.assertTrue(b.eval("document.querySelectorAll('.drop[data-stage]').length >= 5"))

    def test_a_failed_read_keeps_the_board_and_says_so(self):
        b = load()
        cards = b.eval("document.querySelectorAll('.card').length")
        b.eval("""(() => {
          window.__realFetch = window.__realFetch || window.fetch;
          window.fetch = async (url, opts) => String(url).startsWith('/api/state')
            ? new Response(JSON.stringify({ error: 'the test refused it' }), { status: 503 })
            : window.__realFetch(url, opts);
          return true;
        })()""")
        b.eval("loadState().then(() => true)")
        self.assertTrue(b.wait("document.querySelector('#livechip').textContent.includes('board not read')", 5),
                        "the connection chip did not say the read failed")
        self.assertEqual(cards, b.eval("document.querySelectorAll('.card').length"),
                         "the last good board was not kept")
        self.assertTrue(b.wait("document.querySelector('#announcealert').textContent"
                               ".includes('could not be read')", 5),
                        "the failure was not announced")
        self.assertIn("stale", b.eval("document.querySelector('#livechip').getAttribute('aria-label')"))
        shot("requests-read-failed-1280")
        b.eval(RESTORE)
        b.eval("loadState().then(() => true)")
        self.assertTrue(b.wait("!document.querySelector('#livechip').textContent.includes('not read')", 5),
                        "a good read did not clear the chip")


class TheConnectionChip(unittest.TestCase):

    def test_it_is_a_keyboard_stop_and_enter_opens_sessions(self):
        b = load()
        self.assertEqual("button", b.eval("document.querySelector('#livechip').getAttribute('role')"))
        self.assertEqual("0", b.eval("document.querySelector('#livechip').getAttribute('tabindex')"))
        b.eval("document.querySelector('#livechip').focus()")
        self.assertTrue(b.eval("document.activeElement.id === 'livechip'"))
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual("none", ring, "no visible focus ring on the connection chip")
        shot("requests-livechip-focused-1280")
        b.key("Enter")
        self.assertTrue(b.wait("S.view === 'flight'", 5), "Enter did not open Sessions")
        b.eval("setView('board')")


class Announcements(unittest.TestCase):

    def test_an_error_is_announced_and_a_redraw_keeps_the_region(self):
        b = load()
        self.assertEqual("alert", b.eval("document.querySelector('#announcealert').getAttribute('role')"))
        self.assertEqual("status", b.eval("document.querySelector('#announce').getAttribute('role')"))
        self.assertEqual("true", b.eval("document.querySelector('#toast').getAttribute('aria-hidden')"))
        b.eval("toast('refused: the test says no', true)")
        self.assertTrue(b.wait("document.querySelector('#announcealert').textContent"
                               " === 'refused: the test says no'", 3))
        b.eval("render()")
        time.sleep(0.2)
        self.assertEqual("refused: the test says no",
                         b.eval("document.querySelector('#announcealert').textContent"),
                         "a redraw removed the announcement")
        # the regions are not inside any root a redraw rebuilds
        self.assertEqual("BODY", b.eval("document.querySelector('#announcealert').parentElement.tagName"))


if __name__ == "__main__":
    unittest.main()
