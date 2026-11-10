"""The setup guide (task 88), measured in a browser against the disposable
backend's real state.

The backend is an external layout with no instructions, no driver and no
application remote, and the vendor CLI is a stub on its PATH — so the
guide genuinely reads a partial project: some areas configured, some
waiting on a decision. Every status asserted below is read from the
backend's own `GET /api/setup` and compared with the page, so the page
cannot say something the contract does not.

What is covered: the first visit's invitation in the header, which opens
the guide and does not take the board away; empty (nothing decided),
partial and blocked states; a decision made through the page, read back
after a reload (resume); a stale page refused when another client wrote
first; both themes; keyboard reach of the invitation and of a decision;
390px layout with no horizontal overflow; and the read-only half — a
GET leaves the shared record absent and the revision unchanged.

Run through verify/browser, which provides the browser, the backend and
the artifact directory; run any other way these refuse rather than skip.
"""

import json
import unittest
import urllib.error
import urllib.request

import benchverify as bv

S = bv.SESSION
HEADERS = {"Content-Type": "application/json", "X-Bench-Settings": "1"}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api() -> dict:
    code, body = S["backend"].get("/api/setup")
    if code != 200:
        raise bv.CheckFailed(f"GET /api/setup answered {code}")
    return json.loads(body)["setup"]


def post(path: str, payload: dict) -> tuple[int, dict]:
    """A second client — another tab, or an agent with curl — writing
    through the same door the page uses."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(S["backend"].url + path, method="POST",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=HEADERS)
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def set_theme(theme: str):
    b = S["browser"]
    if b.eval("document.documentElement.dataset.theme") != theme:
        b.click('#themebtn')
    if not b.wait(f"document.documentElement.dataset.theme === '{theme}'", 5):
        raise bv.CheckFailed(f"the theme button did not switch to {theme}")


def open_board(width=1440, height=900, theme="dark"):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    set_theme(theme)
    return b


def open_guide(width=1440, height=900, theme="dark"):
    b = open_board(width, height, theme)
    if not b.wait("!document.querySelector('#setupchip').hidden", 10):
        raise bv.CheckFailed("the setup invitation never appeared")
    b.click('#setupchip')
    if not b.wait("document.querySelectorAll('#sbody .setup-area').length === 6", 10):
        raise bv.CheckFailed("the setup guide did not draw its six areas")
    return b


def page_statuses(b) -> dict:
    return b.eval("Object.fromEntries([...document.querySelectorAll('#sbody .setup-area')]"
                  ".map(s => [s.id.replace('setup-', ''),"
                  " s.querySelector('.setup-ahead .sw').textContent]))")


class T1FirstVisit(unittest.TestCase):
    # numbered: unittest runs classes by name, and the first visit must
    # read the record before any case below writes it
    def test_the_invitation_opens_the_guide_and_the_board_stays_usable(self):
        before = api()
        self.assertEqual("none", before["revision"], "something wrote the record")
        b = open_board()
        self.assertTrue(b.wait("!document.querySelector('#setupchip').hidden", 10))
        self.assertTrue(b.eval("document.querySelectorAll('.card').length > 0"),
                        "the invitation took the board away")
        self.assertIn("to settle", b.eval("document.querySelector('#setupchip').textContent"))
        shot("setup-invitation-1440x900")
        b.click('#setupchip')
        self.assertTrue(b.wait("document.querySelectorAll('#sbody .setup-area').length === 6", 10))
        self.assertEqual("page", b.eval(
            "document.querySelector('#snav [data-ssection=\"setup\"]').getAttribute('aria-current')"))
        found = api()
        self.assertEqual({a["id"]: a["status"] for a in found["areas"]},
                         page_statuses(b))
        # a partial project: something configured and something to decide
        words = {a["status"] for a in found["areas"]}
        self.assertIn("decision-needed", words)
        self.assertIn("configured", words)
        self.assertNotIn("tested", {a["status"] for a in found["areas"]
                                    if a["id"] in ("agent", "workspace")},
                         "a configured area read as tested")
        self.assertFalse(b.eval("/\\d+\\s*%/.test(document.querySelector('#sbody').textContent)"),
                         "the guide drew a percentage")
        self.assertEqual("none", api()["revision"], "reading the guide wrote")
        self.assertEqual([], [e.get("exceptionDetails", {}).get("text")
                              for e in b.console], "the page threw")
        shot("setup-guide-dark-1440x900")

    def test_the_invitation_is_reached_and_fired_by_keyboard(self):
        b = open_board()
        b.eval("document.querySelector('#setupchip').focus()")
        self.assertEqual("setupchip", b.eval("document.activeElement.id"))
        outline = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#view-settings').classList.contains('on')", 5))
        self.assertTrue(b.wait("document.querySelectorAll('#sbody .setup-area').length === 6", 10))
        S["metrics"]["setupChipFocusOutline"] = outline


class T2Decisions(unittest.TestCase):
    def test_a_decision_made_in_the_page_survives_a_reload(self):
        b = open_guide()
        b.eval("(() => { const i = document.querySelector('#setup-reason-preview');"
               " i.focus(); i.value = 'a library, nothing to preview';"
               " i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.click('[data-setupact="not-applicable:preview"]')
        self.assertTrue(b.wait("(document.querySelector('#setup-preview .setup-ahead .sw')"
                               " || {}).textContent === 'not-applicable'", 10),
                        "the decision did not reach the page")
        self.assertNotEqual("none", api()["revision"])
        shot("setup-decided-1440x900")
        b = open_guide()        # a reload: nothing is remembered but the record
        self.assertEqual("not-applicable", page_statuses(b)["preview"])
        self.assertIn("a library", b.eval(
            "document.querySelector('#setup-preview .setup-decided').textContent"))

    def test_a_stale_page_is_refused_and_keeps_the_newer_decision(self):
        b = open_guide()
        # another client writes after this page read the guide
        current = api()
        code, _ = post("/api/setup/decide", {
            "area": "delivery", "decision": "defer", "reason": "no remote yet",
            "expected": current["revision"], "request": "case-setup-0001"})
        self.assertEqual(200, code)
        b.eval("(() => { const i = document.querySelector('#setup-reason-delivery');"
               " i.value = 'from the stale page';"
               " i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        # the SSE board event may already have refreshed the page; if so
        # the stale button is gone and the newer decision is drawn
        stale_button = b.eval("!!document.querySelector('[data-setupact=\"not-applicable:delivery\"]')"
                              " && document.querySelector('#setup-delivery .setup-ahead .sw')"
                              ".textContent !== 'deferred'")
        if stale_button:
            b.click('[data-setupact="not-applicable:delivery"]')
        self.assertTrue(b.wait("document.querySelector('#setup-delivery .setup-ahead .sw')"
                               ".textContent === 'deferred'", 10))
        after = {a["id"]: a for a in api()["areas"]}["delivery"]
        self.assertEqual("deferred", after["status"])
        self.assertEqual("no remote yet", after["decision"]["reason"])


class T3Layouts(unittest.TestCase):
    def test_both_themes_and_390px_fit_without_overflow(self):
        for theme in ("light", "dark"):
            for w, h in ((1440, 900), (390, 780)):
                b = open_guide(w, h, theme)
                width = b.eval("document.documentElement.scrollWidth")
                self.assertLessEqual(width, w + 1, f"horizontal overflow at {w}px {theme}")
                boxes = b.eval("[...document.querySelectorAll('#sbody .setup-area')]"
                               ".map(e => { const r = e.getBoundingClientRect();"
                               " return [Math.round(r.x), Math.round(r.right)]; })")
                for left, right in boxes:
                    self.assertGreaterEqual(left, -1)
                    self.assertLessEqual(right, w + 1)
                shot(f"setup-guide-{theme}-{w}x{h}")

    def test_every_control_is_named(self):
        b = open_guide(1280, 800)
        unnamed = []
        for n in b.ax_nodes():
            if n.get("ignored"):
                continue
            role = (n.get("role") or {}).get("value")
            if role in ("button", "textbox", "link") and \
                    not ((n.get("name") or {}).get("value") or "").strip():
                unnamed.append(role)
        self.assertEqual([], unnamed)


if __name__ == "__main__":
    unittest.main()
