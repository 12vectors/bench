"""A confirmed archive leaves the page without waiting for a reading
(task 128), in the browser.

The page's whole-board readings are held back here — the page's own
`fetch` is wrapped so every `/api/state` reply waits until the case lets
it go — and the card must still leave the moment `/api/archive` answers,
with the tray's count and the ⌘Z promise right. A reading sent *before*
the archive and released *after* it must not put the card back. ⌘Z must
bring it back the same way, and once the held readings are released the
page must agree with the disk.

Every card here is a disposable one written into the fixture's work
state and removed afterwards; the fixture cards other cases read are
never moved.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "09-archive-confirmation-fixture"
FILE = f"{STEM}.md"
CARD = ("# An archive confirmed by its reply\n\n**Status:** Backlog\n"
        "**Priority:** Low\n\nA disposable backlog card.\n")
SEL = f'#board article.card[data-file="{FILE}"]'

HOLD = """(() => {
  if (window.__realFetch) return;
  window.__realFetch = window.fetch;
  window.__held = [];
  window.__hold = true;
  window.fetch = (url, opts) => {
    const p = window.__realFetch(url, opts);
    if (!window.__hold || !String(url).startsWith('/api/state')) return p;
    // the request goes out now; its answer is handed over when released
    return new Promise((resolve, reject) => window.__held.push(() => p.then(resolve, reject)));
  };
})()"""
RELEASE = """(() => { window.__hold = false; const h = window.__held.splice(0);
  h.forEach(f => f()); return h.length; })()"""
UNHOLD = """(() => { if (!window.__realFetch) return; window.__hold = false;
  window.__held.splice(0).forEach(f => f()); window.fetch = window.__realFetch;
  delete window.__realFetch; })()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class ConfirmedArchive(unittest.TestCase):

    def setUp(self):
        work = S["backend"].work / "tasks"
        self.backlog = work / "backlog" / FILE
        self.archived = work / "archive" / FILE
        self.backlog.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.archived.unlink, True)
        self.addCleanup(self.backlog.unlink, True)
        self.addCleanup(lambda: S["browser"].eval(UNHOLD))

    def settled(self):
        """Every reading the page has out, painted: a fresh ask queues
        behind them all, so its answer lands last."""
        b = S["browser"]
        b.eval("window.__settled = false; loadState().then(() => { window.__settled = true; })")
        self.assertTrue(b.wait("window.__settled === true", 30), "the page never settled")

    def server_count(self) -> int:
        return json.loads(S["backend"].get("/api/state")[1]).get("archivedCount", 0)

    def test_the_card_leaves_on_the_reply_and_an_older_reading_cannot_bring_it_back(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        if not b.wait(f"!!document.querySelector('{SEL}')", 15):
            raise bv.CheckFailed("the disposable card never reached the board")
        before = self.server_count()
        b.eval(HOLD)
        # a reading sent before the archive, held: the old board, card and all
        # `void`: the reading is sent, not awaited. The harness awaits a
        # returned promise, and this one settles only on RELEASE — an eval
        # the one-at-a-time Playwright adapter would never get to
        b.eval("void loadState()")
        self.assertTrue(b.wait("window.__held.length === 1", 5),
                        "the older reading was not sent")
        # the drop on the activity bar is the archive gesture; it calls the
        # same archiveCard the ⌸ chip does
        b.eval(f"S.dragging = {{file: {json.dumps(FILE)}, from: 'backlog'}};"
               " document.querySelector('#statusbar').dispatchEvent("
               "   new Event('drop', {bubbles: true, cancelable: true}))")
        self.assertTrue(b.wait(f"!document.querySelector('{SEL}')", 10),
                        "the card did not leave while the readings were held")
        self.assertTrue(self.archived.is_file() and not self.backlog.exists())
        self.assertGreaterEqual(b.eval("window.__held.length"), 1,
                                "a whole-board reading was still held when the card left")
        self.assertIn("⌘Z brings it back", b.eval("document.querySelector('#toastmsg').textContent"))
        self.assertEqual(str(before + 1), b.eval(
            "(document.querySelector('#tray .count') || {}).textContent || ''"),
            "the tray's count is the reply's")
        shot("archive-confirmed-before-reconciliation")

        # let every held reading through, the older one included
        b.eval(RELEASE)
        self.settled()
        self.assertFalse(b.eval(f"!!document.querySelector('{SEL}')"),
                         "a reading older than the archive put the card back")
        self.assertEqual(before + 1, b.eval("S.state.archivedCount"))
        self.assertEqual(before + 1, self.server_count())

    def test_undo_brings_it_back_without_waiting_for_a_reading(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/")
        if not b.wait(f"!!document.querySelector('{SEL}')", 15):
            raise bv.CheckFailed("the disposable card never reached the board")
        before = self.server_count()
        b.eval(f"S.dragging = {{file: {json.dumps(FILE)}, from: 'backlog'}};"
               " document.querySelector('#statusbar').dispatchEvent("
               "   new Event('drop', {bubbles: true, cancelable: true}))")
        self.assertTrue(b.wait(f"!document.querySelector('{SEL}')", 10))
        self.assertTrue(b.wait(f"S.state.archivedCount === {before + 1}", 10))
        b.eval(HOLD)
        b.eval("document.dispatchEvent(new KeyboardEvent('keydown',"
               " {key: 'z', ctrlKey: true, bubbles: true}))")
        self.assertTrue(b.wait(f"!!document.querySelector('{SEL}')", 10),
                        "⌘Z did not bring the card back while the readings were held")
        self.assertTrue(self.backlog.is_file() and not self.archived.exists())
        self.assertIn("brought back to backlog/",
                      b.eval("document.querySelector('#toastmsg').textContent"))
        self.assertEqual(before, b.eval("S.state.archivedCount"))
        b.eval(RELEASE)
        self.settled()
        self.assertTrue(b.eval(f"!!document.querySelector('{SEL}')"),
                        "the reconciled board lost the restored card")
        self.assertEqual(before, b.eval("S.state.archivedCount"))
        self.assertEqual(before, self.server_count())


if __name__ == "__main__":
    unittest.main()
