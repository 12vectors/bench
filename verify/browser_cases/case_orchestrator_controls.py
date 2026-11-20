"""The Orchestrator's navigation and confirmation controls under a real
pointer (task 147), against the disposable backend's own routes.

Three defects were reported on the promoted view, and each is held here
the way a person meets it:

- on the Operating agreement page the status strip offered "Review the
  operating agreement" — a link to the page already shown. The Overview
  still offers it, and one click there reaches the agreement;
- a side-nav click was sometimes lost: every repaint replaced the whole
  shell, so a repaint landing between the button going down and coming
  up (the focus leaving the previous control asks for one; so does a
  reading arriving) took the pressed link out from under the pointer.
  Here the press and the release are separate, a render is injected
  between them, and every destination must still answer to one click,
  with the address, the content and the selection agreeing — and
  keyboard, Back and Forward still right;
- the armed "Start a new draft" button drew white on white in Daylight
  under the pointer. Rest, hover, armed, armed under the pointer,
  focused and busy are measured from computed styles in both themes,
  with captures of each.

Starting a draft keeps its arm-then-confirm: one click arms, one confirms
and sends exactly one request, an expired arm needs arming again, and a
busy button sends nothing more. A refusal (another client drafted first)
writes nothing. Navigation sends nothing at all. Everything the case made
is removed after it; no agreement is ever applied.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
POLICY = {"X-Bench-Settings": "1"}
SECTIONS = [("overview", "/orchestrator", "Overview"), ("work", "/orchestrator/work", "Work"),
            ("decisions", "/orchestrator/decisions", "Decisions"),
            ("phases", "/orchestrator/phases", "Phases"),
            ("recovery", "/orchestrator/recovery", "Recovery"),
            ("activity", "/orchestrator/activity", "Activity"),
            ("agreement", "/orchestrator/agreement", "Operating agreement")]
DRAFT_BTN = "[data-o='start-draft-new']"

# Every request the page makes that is not a read, and a way to hold the
# next draft request until the case lets it go. The page's fetch looks up
# `window.fetch` at call time (lib/api.js), so this sees every write.
RECORD_JS = """(() => {
  if (window.__rec) return true;
  window.__posts = [];
  const real = window.fetch.bind(window);
  window.fetch = (url, opts = {}) => {
    const method = String((opts && opts.method) || 'GET').toUpperCase();
    if (method === 'GET' || method === 'HEAD') return real(url, opts);
    window.__posts.push(String(url));
    if (window.__holdNext && String(url).includes('/agreement/draft')) {
      window.__holdNext = false;
      return new Promise((done) => { window.__release = () => done(real(url, opts)); });
    }
    return real(url, opts);
  };
  window.__rec = true;
  return true;
})()"""

# A repaint whose markup really differs from what is drawn: the strip's
# "read … ago" moves by whole minutes, a different count each time.
INJECT_JS = """(() => {
  S.orch.at = Date.now() - %d * 60000;
  render();
  scheduleRender();
  return true;
})()"""

# Computed colours of one control, composited the way they are seen.
MEASURE_JS = """((sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const nums = (c) => (String(c).match(/[\\d.]+/g) || []).map(Number);
  const rgba = (c) => { const n = nums(c); return [n[0] || 0, n[1] || 0, n[2] || 0, n.length > 3 ? n[3] : 1]; };
  const behind = (node) => {
    for (let p = node; p; p = p.parentElement) {
      const c = rgba(getComputedStyle(p).backgroundColor);
      if (c[3] > 0) return c;
    }
    return [255, 255, 255, 1];
  };
  const mix = (a, b, t) => [0, 1, 2].map(i => a[i] * t + b[i] * (1 - t));
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const cs = getComputedStyle(el);
  const under = behind(el.parentElement);
  let bg = rgba(cs.backgroundColor);
  bg = bg[3] > 0 ? mix(bg, under, bg[3]) : under.slice(0, 3);
  let fg = rgba(cs.color);
  fg = mix(fg, bg, fg[3]);
  const op = parseFloat(cs.opacity);
  const fgSeen = mix(fg, under, op), bgSeen = mix(bg, under, op);
  const after = getComputedStyle(el, '::after');
  const bar = rgba(after.backgroundColor);
  return { text: el.innerText.trim(), cls: el.className, color: cs.color, background: cs.backgroundColor,
           opacity: op, filter: cs.filter, ratio: Math.round(ratio(fgSeen, bgSeen) * 100) / 100,
           hover: el.matches(':hover'), focus: el.matches(':focus'), focusVisible: el.matches(':focus-visible'),
           outline: cs.outlineStyle + ' ' + cs.outlineColor,
           bar: after.content === 'none' ? null
             : { color: after.backgroundColor, animation: after.animationName,
                 ratio: Math.round(ratio(mix(bar, bgSeen, bar[3]), bgSeen) * 100) / 100 } };
})(%s)"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api(path="/api/orchestration") -> dict:
    code, body = S["backend"].get(path)
    if code != 200:
        raise bv.CheckFailed(f"GET {path} answered {code}")
    return json.loads(body)


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def metric(name, value):
    S["metrics"].setdefault("orchestratorControls", {})[name] = value


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")
    if not b.wait(f"document.documentElement.dataset.theme === '{which}'", 5):
        raise bv.CheckFailed(f"the page did not switch to the {which} theme")


def visit(path, ready, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto(path)
    if not b.wait(ready, 20):
        seen = b.eval("(document.querySelector('#oshell') || document.body).innerText.slice(0, 400)")
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads {seen!r}")
    b.eval(RECORD_JS)
    return b


def discard_draft():
    """Remove any draft through the route that owns it, as another client."""
    draft = api()["orchestration"].get("draft")
    if draft:
        code, body = S["backend"].post("/api/orchestration/agreement/discard",
                                       {"expected": draft["revision"]}, headers=POLICY)
        if code != 200:
            raise bv.SetupError(f"the case's draft could not be discarded: {code} {body[:200]!r}")


class OrchestratorControls(unittest.TestCase):

    injected = 1

    @classmethod
    def setUpClass(cls):
        cls.local = S["backend"].tool / "manager" / "local"
        (cls.local / "orchestration.json").unlink(missing_ok=True)
        shutil.rmtree(cls.local / "state" / "orchestration", ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        (cls.local / "orchestration.json").unlink(missing_ok=True)
        shutil.rmtree(cls.local / "state" / "orchestration", ignore_errors=True)

    def posts(self, b):
        return b.eval("window.__posts.slice()")

    def click_through_repaint(self, b, selector):
        """One click whose press and release straddle a real repaint."""
        hit = b.press(selector)
        OrchestratorControls.injected += 1
        b.eval(INJECT_JS % OrchestratorControls.injected)
        time.sleep(0.15)     # a frame or more: the scheduled paint has run
        b.release(hit)

    def assert_section(self, b, section, path, title):
        want = (f"location.pathname === '{path}'"
                f" && document.querySelector('#oshell .o-nav a.on')?.getAttribute('href') === '{path}'"
                f" && document.querySelector('#oshell .o-nav a[aria-current=page]')?.getAttribute('href') === '{path}'"
                f" && document.querySelector('#oshell .o-title')?.textContent === {json.dumps(title)}"
                f" && document.querySelectorAll('#oshell .o-nav a[aria-current]').length === 1")
        if not b.wait(want, 6):
            seen = b.eval("({path: location.pathname, on: document.querySelector('#oshell .o-nav a.on')?.getAttribute('href'),"
                          " title: document.querySelector('#oshell .o-title')?.textContent})")
            raise bv.CheckFailed(f"{section}: one click did not land — the page reads {seen}")

    # ── 1. the agreement page does not link to itself ────────────────────

    def test_1_agreement_page_has_no_self_link(self):
        b = visit("/orchestrator", "/Nothing starts by itself/.test(document.querySelector('#oshell').innerText)")
        theme(b, "light")
        links = b.eval("[...document.querySelectorAll('#oshell .o-main a')]"
                       ".filter(a => /Review the operating agreement/.test(a.textContent)).length")
        self.assertGreaterEqual(links, 1, "the Overview no longer offers the agreement")
        b.eval("document.querySelector('#oshell .o-nav a.on').focus()")
        self.click_through_repaint(b, "#oshell [data-o='go-agreement']")
        self.assert_section(b, "agreement", "/orchestrator/agreement", "Operating agreement")
        here = b.eval("[...document.querySelectorAll('#oshell .o-main a')]"
                      ".filter(a => a.getAttribute('href') === '/orchestrator/agreement'"
                      " || /Review the operating agreement/.test(a.textContent)).map(a => a.outerHTML)")
        self.assertEqual([], here, "the agreement page links to itself")
        # the status itself stays: its word and sentence are still said
        self.assertTrue(b.eval("!!document.querySelector('#oshell .o-status .o-badge')"
                               " && /Nothing starts by itself/.test(document.querySelector('#oshell .o-status').innerText)"))
        self.assertEqual([], self.posts(b), "following a link sent a write")
        shot("orchestrator-agreement-no-self-link-1440-light")

    # ── 2. one click per destination, through a repaint ──────────────────

    def test_2_every_nav_destination_answers_one_click(self):
        b = visit("/orchestrator", "document.querySelector('#oshell .o-nav a.on')")
        theme(b, "dark")
        order = SECTIONS[1:] + SECTIONS[:1]
        clicks = []
        for section, path, title in order:
            # the previous destination keeps focus, as it does after a click
            b.eval("document.querySelector('#oshell .o-nav a.on').focus()")
            self.click_through_repaint(b, f"#oshell .o-nav a[href='{path}']")
            self.assert_section(b, section, path, title)
            clicks.append(section)
        # one entry per click (history.length is capped at 50, so walk it):
        # Back from Overview is Agreement, and again Activity
        for section, path, title in (SECTIONS[6], SECTIONS[5]):
            b.eval("history.back()")
            self.assert_section(b, section, path, title)
        for section, path, title in (SECTIONS[6], SECTIONS[0]):
            b.eval("history.forward()")
            self.assert_section(b, section, path, title)
        metric("navOneClick", clicks)
        # a reading landing while the button is down: the same, again
        b.eval("document.querySelector('#oshell .o-nav a.on').focus()")
        hit = b.press("#oshell .o-nav a[href='/orchestrator/recovery']")
        b.eval("(() => { S.orch.at = 0; render(); return true; })()")     # stale: asks the board again
        time.sleep(0.6)
        b.release(hit)
        self.assert_section(b, "recovery", "/orchestrator/recovery", "Recovery")
        # keyboard: Enter on a focused link
        b.eval("document.querySelector(\"#oshell .o-nav a[href='/orchestrator/activity']\").focus()")
        b.key("Enter")
        self.assert_section(b, "activity", "/orchestrator/activity", "Activity")
        # Back and Forward restore the section and its selection together
        b.eval("history.back()")
        self.assert_section(b, "recovery", "/orchestrator/recovery", "Recovery")
        b.eval("history.forward()")
        self.assert_section(b, "activity", "/orchestrator/activity", "Activity")
        # direct entry
        visit("/orchestrator/phases", "document.querySelector('#oshell .o-nav a.on')")
        self.assert_section(b, "phases", "/orchestrator/phases", "Phases")
        self.assertEqual([], self.posts(b), "navigation sent a write")
        self.assertFalse(api()["orchestration"]["effective"]["configured"])
        self.assertIsNone(api()["orchestration"].get("draft"), "navigation drafted something")

    # ── 3. start a draft: arm, confirm, exactly one request ─────────────

    def test_3_start_draft_arms_once_and_sends_once(self):
        discard_draft()
        b = visit("/orchestrator/agreement", f"document.querySelector({json.dumps(DRAFT_BTN)})")
        theme(b, "light")
        armed = f"document.querySelector({json.dumps(DRAFT_BTN)})?.classList.contains('armed')"
        b.eval("document.querySelector('#oshell .o-nav a.on').focus()")
        self.click_through_repaint(b, DRAFT_BTN)
        if not b.wait(armed, 3):
            raise bv.CheckFailed("one click through a repaint did not arm Start a new draft")
        self.assertIn("Confirm", b.eval(f"document.querySelector({json.dumps(DRAFT_BTN)}).innerText"))
        self.assertEqual([], self.posts(b), "arming sent a request")
        # an arm that runs out is a first click again
        if not b.wait(f"!({armed})", 8):
            raise bv.CheckFailed("the armed window never ran out")
        self.click_through_repaint(b, DRAFT_BTN)
        if not b.wait(armed, 3):
            raise bv.CheckFailed("a click after the window ran out did not arm again")
        self.assertEqual([], self.posts(b), "a click after expiry fired instead of arming")
        # a repaint that changes no authority leaves the arm as it was
        b.eval(INJECT_JS % 97)
        time.sleep(0.2)
        self.assertTrue(b.eval(armed), "a repaint took the arm away")
        self.click_through_repaint(b, DRAFT_BTN)
        if not b.wait("document.querySelector('#ag-bench-enabled')", 10):
            raise bv.CheckFailed("confirming drew no draft editor")
        posts = [p for p in self.posts(b) if "/api/" in p]
        self.assertEqual(["/api/orchestration/agreement/draft"], posts, "not exactly one draft request")
        found = api()["orchestration"]
        self.assertFalse(found["effective"]["configured"], "starting a draft put something in force")
        self.assertFalse(found["draft"]["document"]["bench"]["enabled"], "the default draft has the Bench scope on")
        metric("draftRequests", len(posts))
        discard_draft()

    # ── 4. readable in both themes, rest to busy; a refusal writes nothing ─

    def test_4_draft_button_legible_in_every_state(self):
        readings = {}
        for which in ("light", "dark"):
            discard_draft()
            b = visit("/orchestrator/agreement", f"document.querySelector({json.dumps(DRAFT_BTN)})")
            theme(b, which)
            sel = json.dumps(DRAFT_BTN)

            def measure(state):
                # colours fade (.14s): read them where they come to rest
                time.sleep(0.05)
                if not b.wait("!document.getAnimations().some(a => a instanceof CSSTransition"
                              " && a.playState === 'running')", 3):
                    raise bv.CheckFailed(f"{which} {state}: transitions never settled")
                m = b.eval(MEASURE_JS % sel)
                readings[f"{which}:{state}"] = m
                self.assertTrue(m and m["text"], f"{which} {state}: no visible label ({m})")
                self.assertGreaterEqual(m["ratio"], 4.5, f"{which} {state}: label contrast {m}")
                return m

            b.hover("#oshell .o-title")
            measure("rest")
            b.hover(DRAFT_BTN)
            measure("hover")
            b.click(DRAFT_BTN)
            if not b.wait(f"document.querySelector({sel}).classList.contains('armed')", 3):
                raise bv.CheckFailed(f"{which}: the draft button did not arm")
            b.hover(DRAFT_BTN)
            time.sleep(0.15)
            m = measure("armed-hover")
            self.assertTrue(m["hover"], f"{which}: the pointer is not over the armed button")
            self.assertIn("Confirm", m["text"])
            self.assertEqual("none", m["filter"], f"{which}: armed under the pointer is filtered")
            self.assertTrue(m["bar"] and m["bar"]["animation"] == "drain",
                            f"{which}: the armed countdown is not drawn ({m['bar']})")
            self.assertGreaterEqual(m["bar"]["ratio"], 3, f"{which}: countdown contrast {m['bar']}")
            shot(f"orchestrator-draft-armed-hover-1440-{which}")
            b.hover("#oshell .o-title")
            b.eval(f"document.querySelector({sel}).focus()")
            m = measure("armed-focus")
            self.assertTrue(m["focus"], f"{which}: the armed button is not focused")
            # busy: the confirmation is out and held; repeated clicks send nothing more
            b.eval("window.__holdNext = true; window.__posts.length = 0; true")
            if not b.eval(f"document.querySelector({sel}).classList.contains('armed')"):
                # the measuring outlasted the window: arm again, deliberately
                b.click(DRAFT_BTN)
                if not b.wait(f"document.querySelector({sel}).classList.contains('armed')", 3):
                    raise bv.CheckFailed(f"{which}: the draft button did not arm again")
            b.click(DRAFT_BTN)
            if not b.wait(f"document.querySelector({sel})?.classList.contains('busy')", 3):
                raise bv.CheckFailed(f"{which}: confirming did not show busy")
            b.hover(DRAFT_BTN)
            m = measure("busy")
            self.assertEqual(1, m["opacity"], f"{which}: busy is faded ({m})")
            shot(f"orchestrator-draft-busy-1440-{which}")
            b.click(DRAFT_BTN)
            b.click(DRAFT_BTN)
            # another client drafts first: the held request is refused
            code, body = S["backend"].post("/api/orchestration/agreement/draft",
                                           {"document": "new", "expected": "none"}, headers=POLICY)
            self.assertEqual(200, code, body[:200])
            theirs = api()["orchestration"]["draft"]["revision"]
            b.eval("window.__release(); true")
            if not b.wait("document.querySelector('#oshell .o-notice.alarm')", 10):
                raise bv.CheckFailed(f"{which}: the refusal was not said")
            self.assertEqual(["/api/orchestration/agreement/draft"],
                             [p for p in self.posts(b) if "/api/" in p],
                             f"{which}: busy clicks sent more than one request")
            found = api()["orchestration"]
            self.assertEqual(theirs, found["draft"]["revision"], "the refused request wrote")
            self.assertFalse(found["effective"]["configured"])
        metric("contrast", {k: {"ratio": v["ratio"], "color": v["color"], "background": v["background"],
                                "opacity": v["opacity"], "filter": v["filter"],
                                "bar": v["bar"] and v["bar"]["ratio"]}
                            for k, v in readings.items()})
        discard_draft()
