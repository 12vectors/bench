"""Task 154: the board on a phone is for checking where cards stand.

Measured in a browser against the disposable backend at phone widths
(320, 375, 390 and 430 CSS pixels, and a phone held sideways), and at a
desktop width to show none of it reaches a desktop:

- an open activity panel never hides its own toggle or makes the page
  taller than the window — the reported defect: a height chosen on a
  desktop and read back on a phone pushed the bar holding the panel's
  only toggle below the window, so the panel could not be closed;
- a phone starts with the panel closed, and opening and closing it are
  ordinary clicks, from the bar or from inside the panel, that give the
  board its space back with nothing left over it;
- an explicit choice survives live events, repaints, readings,
  navigation, the detail view and a rotation;
- the first screen at 390 × 844 shows a stage heading and a whole card,
  the stage strip reaches every stage by pointer and by keyboard, and
  representative idle, running, needs-attention, Review and Done cards
  say what they are with the board's own readings.

Every assertion is a measurement. The screenshots are for a person to
look at; the ones taken before an assertion are the evidence of what
the page drew, whichever way the assertion went.
"""

import json
import os
import time
import unittest

import benchverify as bv

S = bv.SESSION
PHONES = [(320, 568), (375, 667), (390, 844), (430, 932)]
SIDEWAYS = (844, 390)
KEYS = ("bench-log-open", "bench-log-open-narrow", "bench-log-h")

GEOMETRY = """(() => {
  const box = s => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom, right: r.right}; };
  const hit = s => { const e = document.querySelector(s); if (!e) return false;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) return false;
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
    const at = document.elementFromPoint(x, y); return !!at && (at === e || e.contains(at)); };
  const panel = document.querySelector('#logpanel');
  return {vw: innerWidth, vh: innerHeight,
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
    header: box('header'), board: box('#board'), bar: box('#statusbar'),
    headerItems: [...document.querySelector('header').children]
      .filter(e => e.getClientRects().length)
      .map(e => { const r = e.getBoundingClientRect();
        return (e.id || e.className) + '@' + Math.round(r.x) + ',' + Math.round(r.y) + '+' + Math.round(r.width); }),
    toggle: box('#actbtn'), toggleHit: hit('#actbtn'),
    open: panel.classList.contains('open'), panelH: panel.offsetHeight,
    expanded: document.querySelector('#actbtn').getAttribute('aria-expanded')};
})()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def metric(name, value):
    S["metrics"].setdefault("mobileBoard", {})[name] = value


def load(width, height, storage=None, theme=None):
    """The board at a size, with exactly the remembered choices given —
    as a person's browser would have them — and nothing else."""
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    clear = "".join(f"localStorage.removeItem({json.dumps(k)});" for k in KEYS)
    put = "".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
                  for k, v in (storage or {}).items())
    theme_js = (f"localStorage.setItem('bench-theme', {json.dumps(theme)});" if theme
                else "")
    b.eval(f"(() => {{ {clear}{put}{theme_js} return true; }})()")
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5"
                  " && document.querySelectorAll('#board article.card').length >= 5", 15):
        raise bv.CheckFailed(f"the board never drew at {width}x{height}")
    b.eval("document.activeElement && document.activeElement.blur()")
    return b


def geometry():
    return S["browser"].eval(GEOMETRY)


def fits(test, g, where):
    """No page-wide overflow either way, and the panel's toggle on screen
    and hit-testable — nothing laid over it."""
    test.assertLessEqual(g["scrollW"], g["vw"] + 1, f"page-wide horizontal overflow {where}")
    test.assertLessEqual(g["scrollH"], g["vh"] + 1,
                         f"the page is taller than the window {where}: {g['scrollH']} > {g['vh']}")
    test.assertTrue(g["toggle"] and g["toggle"]["y"] >= 0
                    and g["toggle"]["bottom"] <= g["vh"] + 0.5,
                    f"the activity toggle is outside the window {where}: {g['toggle']}")
    test.assertTrue(g["toggleHit"], f"something covers the activity toggle {where}")


class Reproduction(unittest.TestCase):
    """The reported defect: an open panel that cannot be closed."""

    def tearDown(self):
        S["browser"].eval("(() => { " + "".join(
            f"localStorage.removeItem({json.dumps(k)});" for k in KEYS) + " return true; })()")

    def test_an_open_activity_panel_never_hides_its_own_toggle(self):
        b = S["browser"]
        found = {}
        # opened on this phone before, with a height a desktop chose
        remembered = {"bench-log-open": "1", "bench-log-open-narrow": "1",
                      "bench-log-h": "420"}
        for w, h in [(375, 667), (390, 844), SIDEWAYS]:
            where = f"at {w}x{h}"
            load(w, h, remembered)
            b.wait("document.querySelector('#logbody').childElementCount > 0", 5)
            g = geometry()
            found[f"{w}x{h}-open"] = g
            shot(f"mobile-activity-open-{w}x{h}")
            self.assertTrue(g["open"], f"the remembered open choice was not honoured {where}")
            fits(self, g, where)
            self.assertGreaterEqual(g["board"]["h"], 100,
                                    f"the open panel leaves the board {g['board']['h']}px {where}")
            board_before = g["board"]["h"]
            b.click("#actbtn")
            self.assertTrue(b.wait("!document.querySelector('#logpanel').classList.contains('open')", 5),
                            f"a click on the toggle did not close the panel {where}")
            g = geometry()
            found[f"{w}x{h}-closed"] = g
            shot(f"mobile-activity-closed-{w}x{h}")
            self.assertEqual(0, g["panelH"], f"the closed panel still takes space {where}")
            self.assertEqual("false", g["expanded"])
            self.assertGreater(g["board"]["h"], board_before,
                               f"closing the panel gave the board nothing back {where}")
            fits(self, g, where)
        metric("reproduction", found)


class Phones(unittest.TestCase):
    def tearDown(self):
        S["browser"].eval("(() => { " + "".join(
            f"localStorage.removeItem({json.dumps(k)});" for k in KEYS) + " return true; })()")

    def test_a_phone_starts_compact_and_the_panel_opens_and_closes_by_click(self):
        b = S["browser"]
        found = {}
        # a desktop's open choice in the same browser is not a phone's
        for w, h in PHONES:
            where = f"at {w}x{h}"
            load(w, h, {"bench-log-open": "1"})
            g = geometry()
            found[f"{w}x{h}-initial"] = g
            shot(f"mobile-board-initial-{w}x{h}")
            self.assertFalse(g["open"], f"the panel is open on a phone's first load {where}")
            self.assertEqual(0, g["panelH"])
            fits(self, g, where)
            # the header is not the page: at most a fifth of the window
            self.assertLessEqual(g["header"]["h"], max(110, g["vh"] * 0.2),
                                 f"the header takes {g['header']['h']}px {where}: {g['headerItems']}")
            # nothing is laid over the cards: the first card answers a hit
            hit = b.eval("(() => { const c = document.querySelector('#board article.card');"
                         " const r = c.getBoundingClientRect();"
                         " const at = document.elementFromPoint(r.x + r.width / 2, r.y + Math.min(20, r.height / 2));"
                         " return !!at && c.contains(at); })()")
            self.assertTrue(hit, f"something lies over the first card {where}")

            b.click("#actbtn")
            self.assertTrue(b.wait("document.querySelector('#logpanel').classList.contains('open')", 5),
                            f"a click did not open the panel {where}")
            g = geometry()
            found[f"{w}x{h}-open"] = g
            shot(f"mobile-activity-opened-{w}x{h}")
            self.assertEqual("true", g["expanded"])
            fits(self, g, where)
            self.assertLessEqual(g["panelH"], g["vh"] * 0.5 + 60,
                                 f"the panel takes {g['panelH']}px of {g['vh']} {where}")
            # closed from inside the panel, by a labelled control
            label = b.eval("document.querySelector('#loghead [data-logclose]').innerText")
            self.assertIn("Hide activity", label)
            size = b.eval("(() => { const r = document.querySelector('#loghead [data-logclose]')"
                          ".getBoundingClientRect(); return [r.width, r.height]; })()")
            self.assertGreaterEqual(min(size), 36, f"the Hide control is {size} {where}")
            b.click("#loghead [data-logclose]")
            self.assertTrue(b.wait("!document.querySelector('#logpanel').classList.contains('open')", 5),
                            f"Hide activity did not close the panel {where}")
            self.assertEqual("actbtn", b.eval("document.activeElement && document.activeElement.id"),
                             "closing the panel did not hand focus back to its toggle")
            g = geometry()
            self.assertEqual(0, g["panelH"])
            fits(self, g, where)
        metric("phones", found)

    def test_the_first_screen_at_390_shows_a_stage_and_a_whole_card(self):
        b = S["browser"]
        for theme in ("dark", "light"):
            load(390, 844, theme=theme)
            m = b.eval("""(() => {
              const col = [...document.querySelectorAll('#board .kcol')][0];
              const h2 = col.querySelector('h2').getBoundingClientRect();
              const card = col.querySelector('article.card').getBoundingClientRect();
              const bar = document.querySelector('#statusbar').getBoundingClientRect();
              const title = col.querySelector('article.card .title');
              return {h2: [h2.x, h2.y, h2.width, h2.bottom], card: [card.x, card.y, card.right, card.bottom],
                      bar: bar.y, vw: innerWidth, font: parseFloat(getComputedStyle(title).fontSize),
                      heading: col.querySelector('h2').innerText};
            })()""")
            metric(f"firstScreen-{theme}", m)
            shot(f"mobile-first-screen-{theme}-390x844")
            self.assertTrue(m["h2"][2] > 0 and m["h2"][1] >= 0 and m["h2"][3] <= m["bar"],
                            f"the first stage heading is not on the first screen: {m}")
            self.assertTrue(m["card"][0] >= 0 and m["card"][2] <= m["vw"] + 0.5
                            and m["card"][1] >= 0 and m["card"][3] <= m["bar"],
                            f"no whole card on the first screen: {m}")
            self.assertGreaterEqual(m["font"], 13, "the card title is not readable size")
            # secondary controls are discoverable: one labelled control away
            more = b.eval("(() => { const m = document.querySelector('#hdrmore');"
                          " const r = m.getBoundingClientRect();"
                          " return {w: r.width, h: r.height, label: m.getAttribute('aria-label'),"
                          " theme: document.querySelector('#themebtn').getClientRects().length}; })()")
            self.assertGreaterEqual(min(more["w"], more["h"]), 36, more)
            self.assertIn("theme", more["label"])
            self.assertEqual(0, more["theme"], "the theme button is not folded on a phone")
            b.click("#hdrmore")
            self.assertTrue(b.wait("document.querySelector('#themebtn').getClientRects().length > 0"
                                   " && document.querySelector('#refresh').getClientRects().length > 0", 5),
                            "More did not show the theme and Refresh controls")
            self.assertEqual("true", b.eval("document.querySelector('#hdrmore').getAttribute('aria-expanded')"))
            shot(f"mobile-header-more-{theme}-390x844")
            self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391)
            b.click("#hdrmore")
            # the configuration check is never folded
            self.assertGreater(b.eval("document.querySelector('#doctorchip').getClientRects().length"), 0)


class Persistence(unittest.TestCase):
    """An explicit choice is the person's: nothing else changes it."""

    def tearDown(self):
        S["browser"].eval("(() => { " + "".join(
            f"localStorage.removeItem({json.dumps(k)});" for k in KEYS) + " return true; })()")

    def churn(self, n):
        """Live events, a repaint, a reading and navigation: what a board
        does to a page while a person reads it."""
        b, backend = S["browser"], S["backend"]
        for i in range(3):
            code, _ = backend.post("/api/events", {"session": "mobile-154", "kind": "read",
                                                   "summary": f"a live event {n}.{i}"})
            if code != 200:
                raise bv.SetupError(f"the backend did not take an event: {code}")
        if not b.wait(f"document.querySelector('#bartail').innerText.includes('a live event {n}.2')", 10):
            raise bv.CheckFailed("the live events never reached the page")
        b.eval("(() => { render(); return true; })()")
        b.eval("loadState().then(() => true)")
        b.click('#boardnav a[data-view="flight"]')
        b.wait("location.pathname.startsWith('/sessions')", 5)
        b.click('#views a[data-view="board"]')
        if not b.wait("location.pathname === '/board' && document.querySelectorAll('#board article.card').length >= 5", 5):
            raise bv.CheckFailed("navigating back to the board failed")
        # the detail view, opened and closed
        b.click('#board article.card .title')
        if not b.wait("document.querySelector('#drawer').classList.contains('open')", 5):
            raise bv.CheckFailed("a card's detail view did not open")
        b.key("Escape")
        b.wait("!document.querySelector('#drawer').classList.contains('open')", 5)
        time.sleep(0.5)

    def test_an_explicit_choice_survives_events_repaints_navigation_and_rotation(self):
        b = S["browser"]
        found = {}
        load(390, 844)
        b.click("#actbtn")
        b.click("#actbtn")
        self.assertFalse(geometry()["open"])
        self.churn(1)
        g = geometry()
        found["closed-after-churn"] = g
        self.assertFalse(g["open"], "live updates or navigation opened a closed panel")
        b.viewport(*SIDEWAYS)
        time.sleep(0.4)
        g = geometry()
        found["closed-sideways"] = g
        shot("mobile-rotated-closed-844x390")
        self.assertFalse(g["open"], "a rotation opened a closed panel")
        fits(self, g, "closed, sideways")
        b.viewport(390, 844)
        time.sleep(0.4)
        self.assertFalse(geometry()["open"], "rotating back opened a closed panel")

        b.click("#actbtn")
        self.assertTrue(geometry()["open"])
        self.churn(2)
        g = geometry()
        found["open-after-churn"] = g
        self.assertTrue(g["open"], "live updates or navigation closed an open panel")
        fits(self, g, "open, after updates")
        b.viewport(*SIDEWAYS)
        time.sleep(0.4)
        g = geometry()
        found["open-sideways"] = g
        shot("mobile-rotated-open-844x390")
        self.assertTrue(g["open"], "a rotation closed an open panel")
        fits(self, g, "open, sideways")
        self.assertGreaterEqual(g["board"]["h"], 100, f"sideways the board has {g['board']['h']}px")
        b.viewport(390, 844)
        time.sleep(0.4)
        g = geometry()
        self.assertTrue(g["open"])
        fits(self, g, "open, back upright")
        # and the choice made on the phone is the phone's next time too
        b.goto("/")
        b.wait("document.querySelectorAll('#board article.card').length >= 5", 10)
        self.assertTrue(geometry()["open"], "the phone's explicit open was not remembered")
        metric("persistence", found)


CARDS = {
    "1541-a-card-nobody-has-touched": ("in-progress", "1541 — A card nobody has touched", "In Progress", ""),
    "1542-a-card-whose-header-disagrees": ("to-do", "1542 — A card whose header disagrees", "Done", ""),
    "1543-a-card-being-refined": ("in-progress", "1543 — A card being refined", "In Progress", ""),
}


class CardStates(unittest.TestCase):
    """Representative states, read with the board's own readings: idle,
    a run in flight (a held refinement through the stub vendor), a card
    that needs attention (status drift), Review and Done."""

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.made = []
        for stem, (stage, title, status, extra) in CARDS.items():
            path = backend.work / "tasks" / stage / f"{stem}.md"
            path.write_text(f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n{extra}\n"
                            "A disposable card made by bench-verify for task 154.\n", encoding="utf-8")
            cls.made.append(path)
        old = time.time() - 3 * 86400
        os.utime(cls.made[0], (old, old))
        backend.refine_hold.write_text("hold", encoding="utf-8")
        code, body = backend.post("/api/card/refine", {
            "file": "1543-a-card-being-refined.md", "stage": "in-progress",
            "text": "Say it more plainly", "request": f"case-154-{time.time()}"})
        if code != 200:
            backend.refine_hold.unlink(missing_ok=True)
            raise bv.SetupError(f"the fixture refinement did not start: {code} {body[:200]!r}")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        backend.refine_hold.unlink(missing_ok=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            code, body = backend.get("/api/state")
            agents = json.loads(body).get("agents") or [] if code == 200 else []
            if not any(a.get("status") == "running" for a in agents):
                break
            time.sleep(0.2)
        for path in cls.made:
            path.unlink(missing_ok=True)

    def card(self, stem):
        return f'#board article.card[data-file="{stem}.md"]'

    def test_states_read_truthfully_and_every_stage_is_reachable(self):
        b = load(390, 844)
        if not b.wait(" && ".join(f"!!document.querySelector('{self.card(s)}')" for s in CARDS)
                      + f" && document.querySelector('{self.card('1543-a-card-being-refined')}')"
                        ".classList.contains('running')", 20):
            raise bv.CheckFailed("the fixture cards, or the run in flight, never reached the board")
        found = {}
        # the strip names every stage, and says where attention is needed
        strip = b.eval("[...document.querySelectorAll('#stagenav [data-stage]')].map(e =>"
                       " ({stage: e.dataset.stage, label: e.getAttribute('aria-label'),"
                       "   h: e.getBoundingClientRect().height}))")
        found["strip"] = strip
        self.assertEqual(["backlog", "to-do", "in-progress", "review", "done"],
                         [s["stage"] for s in strip])
        todo = [s for s in strip if s["stage"] == "to-do"][0]
        self.assertIn("needs attention", todo["label"], "the drifted card's stage is not marked")
        self.assertTrue(all(s["h"] >= 36 for s in strip), f"stage strip targets too small: {strip}")
        for s in strip:
            if s["stage"] != "to-do":
                self.assertNotIn("attention", s["label"], s)

        expect = {
            "1541-a-card-nobody-has-touched": ("in-progress", "idle"),
            "1542-a-card-whose-header-disagrees": ("to-do", "drift"),
            "1543-a-card-being-refined": ("in-progress", None),
            "04-check-keyboard-focus": ("review", None),
            "05-ship-the-first-release": ("done", None),
        }
        for stem, (stage, pill) in expect.items():
            b.click(f'#stagenav [data-stage="{stage}"]')
            time.sleep(0.6)
            sel = self.card(stem)
            b.eval(f"document.querySelector('{sel}').scrollIntoView({{block: 'nearest'}})")
            time.sleep(0.2)
            m = b.eval(f"""(() => {{
              const c = document.querySelector('{sel}'), r = c.getBoundingClientRect();
              const col = c.closest('.kcol'), h = col.querySelector('h2').getBoundingClientRect();
              const pills = [...c.querySelectorAll('.toprow .pill')].map(p => p.innerText.trim());
              return {{x: r.x, right: r.right, y: r.y, bottom: r.bottom, vw: innerWidth,
                       bar: document.querySelector('#statusbar').getBoundingClientRect().y,
                       heading: col.querySelector('h2').innerText, headingX: h.x,
                       pills, cls: c.className,
                       current: document.querySelector('#stagenav [aria-current="true"]')?.dataset.stage}};
            }})()""")
            found[stem] = m
            shot(f"mobile-state-{stem[:2]}-{stage}-390x844")
            self.assertEqual(stage, m["current"], f"the strip did not take the board to {stage}")
            self.assertTrue(0 <= m["x"] and m["right"] <= m["vw"] + 0.5,
                            f"{stem} is not on screen after choosing {stage}: {m}")
            self.assertTrue(0 <= m["headingX"] < m["vw"], f"{stage}'s heading is off screen: {m}")
            if pill:
                self.assertIn(pill, " ".join(m["pills"]), f"{stem} does not say {pill}: {m}")
        self.assertIn("running", found["1543-a-card-being-refined"]["cls"],
                      "the card with a run in flight does not read as running")
        self.assertIn("done-dim", found["05-ship-the-first-release"]["cls"])
        self.assertIn("Review", found["04-check-keyboard-focus"]["heading"].title())
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391)
        metric("cardStates", found)

    def test_keyboard_reaches_a_stage_and_the_detail_view_returns_to_the_board(self):
        b = load(390, 844, theme="light")
        b.eval("document.querySelector('#stagenav [data-stage=\"review\"]').focus()")
        ring = b.eval("(() => { const s = getComputedStyle(document.activeElement);"
                      " return [s.outlineStyle, s.outlineWidth]; })()")
        self.assertNotEqual("none", ring[0], f"a focused stage button shows no focus ring: {ring}")
        b.key("Enter")
        time.sleep(0.6)
        self.assertEqual("review", b.eval("document.querySelector('#stagenav [aria-current=\"true\"]')?.dataset.stage"))
        left = b.eval("document.querySelector('#board').scrollLeft")
        self.assertGreater(left, 0, "Enter on the Review stage did not move the board")
        sel = '#board article.card[data-file="04-check-keyboard-focus.md"]'
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')", 5),
                        "Enter on a card did not open its detail view")
        d = b.eval("(() => { const r = document.querySelector('#drawer').getBoundingClientRect();"
                   " return {x: r.x, w: r.width, vw: innerWidth,"
                   " scrollW: document.documentElement.scrollWidth}; })()")
        metric("detail", d)
        shot("mobile-detail-light-390x844")
        self.assertLessEqual(abs(d["w"] - d["vw"]), 1, f"the detail view is not the screen: {d}")
        self.assertLessEqual(d["scrollW"], d["vw"] + 1)
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#drawer').classList.contains('open')", 5))
        self.assertTrue(b.wait("document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.file === '04-check-keyboard-focus.md'", 5),
                        "closing the detail view did not return focus to the card")
        self.assertLessEqual(abs(b.eval("document.querySelector('#board').scrollLeft") - left), 2,
                             "closing the detail view lost the board's place")


class Desktop(unittest.TestCase):
    """None of the phone layout reaches a desktop."""

    def tearDown(self):
        S["browser"].eval("(() => { " + "".join(
            f"localStorage.removeItem({json.dumps(k)});" for k in KEYS) + " return true; })()")

    def test_a_desktop_keeps_its_layout_and_its_panel(self):
        b = S["browser"]
        found = {}
        for w, h in [(1440, 900), (1024, 768)]:
            load(w, h, {"bench-log-open": "1"})
            m = b.eval("""(() => {
              const vis = s => document.querySelector(s).getClientRects().length > 0;
              const cols = [...document.querySelectorAll('#board .kcol')].map(c => c.getBoundingClientRect());
              return {more: vis('#hdrmore'), strip: vis('#stagenav'), theme: vis('#themebtn'),
                      refresh: vis('#refresh'), path: vis('.brand .path'),
                      open: document.querySelector('#logpanel').classList.contains('open'),
                      columnsInView: cols.filter(r => r.x >= 0 && r.right <= innerWidth + 1).length,
                      oneRow: cols.every(r => Math.abs(r.y - cols[0].y) < 1)
                        && cols.every((r, i) => !i || r.x > cols[i - 1].x),
                      widest: Math.max(...cols.map(r => r.width)),
                      filters: document.querySelectorAll('#loghead [data-lf]').length};
            })()""")
            found[f"{w}x{h}"] = m
            shot(f"desktop-activity-open-{w}x{h}")
            self.assertFalse(m["more"], f"More is drawn on a desktop at {w}")
            self.assertFalse(m["strip"], f"the stage strip is drawn on a desktop at {w}")
            self.assertTrue(m["theme"] and m["refresh"], f"header controls folded at {w}: {m}")
            self.assertTrue(m["open"], "the desktop's remembered open choice was not honoured")
            self.assertTrue(m["oneRow"], f"the five columns are not side by side at {w}: {m}")
            self.assertLessEqual(m["widest"], 297, f"a column is wider than a desktop's at {w}")
            if w >= 1440:
                self.assertEqual(5, m["columnsInView"], f"the five columns do not all fit at {w}")
            self.assertGreaterEqual(m["filters"], 5)
            g = geometry()
            fits(self, g, f"at {w}x{h}")
            b.click("#loghead [data-logclose]")
            self.assertTrue(b.wait("!document.querySelector('#logpanel').classList.contains('open')", 5))
            b.click("#actbtn")
            self.assertTrue(b.wait("document.querySelector('#logpanel').classList.contains('open')", 5))
            self.assertLessEqual(geometry()["panelH"], h * 0.6 + 120)
        metric("desktop", found)
