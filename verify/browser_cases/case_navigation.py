"""Board, Brain, Orchestrator and Settings, in the real browser (task 159).

A disposable backend of this case's own carries a phase (#900) with two
members (#901, #902), an ordinary card (#903), three recorded sessions —
one on #903, one on the member #901 and one with no card — and Brain
entries written through the Brain's own write route (task 158), plus one
file that breaks the contract. The case drives the page as a person would,
with the pointer and the keyboard, and takes screenshots to look at:

- **the primary navigation**: exactly Board / Brain / Orchestrator /
  Settings, the one under which the page is marked current at every
  destination, and the Board's bar drawn only under the Board;
- **one ordinary click is one click**: each destination opens on the
  first click and adds exactly one history entry;
- **Board › Tasks / Phases and a phase drilled into**: the lane alone,
  its breadcrumb, a member card opened over it returning to it, and Back;
- **Sessions and Focus in context**: from a card's Activity and from a
  phase's lane, each saying whose activity it is, listing only theirs,
  and leading back; a direct session link naming its recorded card or
  saying it has none; a context or session that is not here, said;
- **the Brain**: real entries found by title and summary, filtered by
  classification and kind, opened, linked and edited; no-results and the
  malformed file explained;
- **themes and widths**: Night and Daylight at 1280px and 390px, nothing
  overflowing;
- **passive navigation**: every navigation above (but the one edit, made
  in this fixture) sends no write and changes no file in the work root,
  and an armed action still arms before it fires.
"""

import hashlib
import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION

PHASE = """# Navigation phase

**Status:** To Do
**Priority:** Medium
**Type:** Phase

A disposable phase for the navigation case.

## Cards

- 901 — First navigation member
- 902 — Second navigation member
"""


def card(title, status, extra=""):
    return f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n{extra}\nA disposable card.\n"


BROKEN = "# Broken\n\n**Brain:** 1\n**Id:** e-nav-broken\n**Kind:** Wiki\n**Classification:** Current\n\nx\n"

# the page records every request it sends that is not a read
SPY = """(() => {
  if (!window.__navSpy) {
    window.__navSpy = [];
    const real = window.fetch;
    window.fetch = function (input, init) {
      const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD')
        window.__navSpy.push(method + ' ' + String(input && input.url || input));
      return real.apply(this, arguments);
    };
  }
  return true;
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


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class Navigation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "navigation-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        tasks = cls.backend.work / "tasks"
        (tasks / "to-do" / "900-navigation-phase.md").write_text(PHASE, encoding="utf-8")
        (tasks / "review" / "901-first-navigation-member.md").write_text(
            card("First navigation member", "Review"), encoding="utf-8")
        (tasks / "to-do" / "902-second-navigation-member.md").write_text(
            card("Second navigation member", "To Do"), encoding="utf-8")
        # a second phase, so drilling into one is seen to leave the other
        (tasks / "backlog" / "910-another-navigation-phase.md").write_text(
            PHASE.replace("# Navigation phase", "# Another navigation phase")
                 .replace("**Status:** To Do", "**Status:** Backlog")
                 .split("## Cards")[0] + "## Cards\n\n- 904 — Not on this board\n", encoding="utf-8")
        (tasks / "in-progress" / "903-plain-navigation-card.md").write_text(
            card("Plain navigation card", "In Progress"), encoding="utf-8")
        (cls.backend.work / "brain").mkdir()
        (cls.backend.work / "brain" / "broken.md").write_text(BROKEN, encoding="utf-8")
        cls.backend.start()
        post = cls.backend.post
        for sid, task in (("nav-plain-session", "903-plain-navigation-card.md"),
                          ("nav-member-session", "901-first-navigation-member.md"),
                          ("nav-orphan-session", None)):
            payload = {"session": sid, "kind": "read", "summary": f"read for {sid}"}
            if task:
                payload["task"] = task
            code, body = post("/api/events", payload)
            if code != 200:
                raise bv.SetupError(f"the backend did not take an event: {code} {body[:200]!r}")
        # real entries, written through the Brain's own write route (#158)
        for request, title, kind, klass, summary in (
                ("nav-req-1", "Deploying the preview", "Guide", "Current",
                 "How a preview starts on a loopback port"),
                ("nav-req-2", "Why sync stalled in March", "Investigation", "Historical",
                 "A diverged main held every push"),
                ("nav-req-3", "Choosing the work base", "Decision", "Unclassified",
                 "Stacked work starts from committed review work")):
            code, body = post("/api/brain/create", {
                "request": request, "title": title, "kind": kind, "summary": summary,
                "sources": ["#903"], "body": f"{title}, written for the navigation case.\n",
                "classification": klass, "reason": "written for the navigation case"})
            if code != 200:
                raise bv.SetupError(f"the Brain refused an entry: {code} {body[:300]!r}")
        cls.ids = {e["title"]: e["id"] for e in json.loads(cls.backend.get("/api/brain")[1])
                   ["brain"]["entries"]}
        cls.origin = S["browser"].origin
        cls.before = tree(cls.backend.work)

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    # ── helpers ──

    def open(self, path, ready, width=1280, height=860, dark=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait(f"typeof S === 'object' && !!S.state && ({ready})", 15):
            raise bv.CheckFailed(f"{path}: never ready ({ready})")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        b.eval(SPY)
        # the page writes addresses only once its first one is applied
        time.sleep(0.3)
        return b

    def here(self):
        return S["browser"].eval("location.pathname + location.search")

    def crumbs(self, where="#bnwhere"):
        return S["browser"].eval(f"[...document.querySelectorAll('{where} .crumbs li')]"
                                 ".map(li => li.innerText.trim())")

    def passive(self, b, what):
        self.assertEqual([], b.eval("window.__navSpy || []"), f"{what}: navigation sent a write")
        self.assertEqual(self.before, tree(self.backend.work), f"{what}: the work root changed")

    def one_click(self, b, selector, path, what):
        """One ordinary click opens the destination and adds one entry.
        Counted by the page's own address writes (`routeWrites`), since a
        browser caps `history.length` at 50."""
        writes = b.eval("routeWrites()")
        b.click(selector)
        if not b.wait(f"location.pathname + location.search === {json.dumps(path)}", 5):
            raise bv.CheckFailed(f"{what}: one click did not reach {path} (at {self.here()})")
        time.sleep(0.2)
        self.assertEqual(writes + 1, b.eval("routeWrites()"), f"{what}: one click, one entry")

    def no_overflow(self, b, where):
        wide = b.eval("[document.documentElement.scrollWidth, innerWidth]")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the page overflows {wide}")

    # ── 1. the primary navigation ──

    def test_1_primary_order_and_selection_at_each_destination(self):
        b = self.open("/board", "document.querySelectorAll('#board article.card').length > 0")
        self.assertEqual(["Board", "Brain", "Orchestrator", "Settings"],
                         b.eval("[...document.querySelectorAll('#views a')].map(a => a.textContent.trim())"))
        self.assertEqual("Primary", b.eval("document.querySelector('#views').getAttribute('aria-label')"))
        for primary, path, view in (("brain", "/brain", "brain"),
                                    ("orchestrator", "/orchestrator", "orchestrator"),
                                    ("settings", "/settings", "settings"),
                                    ("board", "/board", "board")):
            self.one_click(b, f'#views [data-view="{primary}"]', path, f"primary {primary}")
            self.assertTrue(b.wait(f"document.querySelector('#view-{view}').classList.contains('on')", 5))
            current = b.eval("[...document.querySelectorAll('#views a')].filter(a => a.getAttribute('aria-current'))"
                             ".map(a => a.dataset.view + ':' + a.getAttribute('aria-current'))")
            self.assertEqual([f"{primary}:page"], current, f"at {path}")
            self.assertEqual(primary != "board", b.eval("document.querySelector('#boardnav').hidden"),
                             f"the Board's bar at {path}")
        # under the Board, every Board view keeps Board selected
        for path, ready in (("/phases", "#view-phases.on"), ("/sessions", "#view-flight.on"),
                            ("/focus", "#view-focus.on")):
            b.goto(path)
            b.wait(f"!!document.querySelector('{ready}')", 10)
            self.assertEqual("true", b.eval("document.querySelector('#views [data-view=\"board\"]')"
                                            ".getAttribute('aria-current')"), path)
        # the keyboard: a primary link takes focus visibly and Enter follows it
        b = self.open("/board", "!!document.querySelector('#view-board.on')")
        b.eval("document.querySelector('#views [data-view=\"board\"]').focus(); true")
        b.key("Tab")
        self.assertEqual("brain", b.eval("document.activeElement && document.activeElement.dataset.view"),
                         "Tab from Board reaches Brain next")
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        b.key("Enter")
        self.assertTrue(b.wait("location.pathname === '/brain'", 5), "Enter on the Brain link")
        self.assertNotEqual("none", ring, "the focused primary link draws no focus ring")
        b.eval(SPY)
        self.passive(b, "the primary navigation")

    # ── 2. Tasks / Phases and a phase drilled into ──

    def test_2_tasks_phases_and_the_phase_drilldown(self):
        b = self.open("/board", "document.querySelectorAll('#board article.card').length > 0")
        self.one_click(b, '#boardnav [data-view="phases"]', "/phases", "the Phases tab")
        self.assertTrue(b.wait("document.querySelectorAll('#lanes .lane').length >= 2", 10),
                        "every phase draws a lane")
        self.assertEqual("page", b.eval("document.querySelector('#boardnav [data-view=\"phases\"]')"
                                        ".getAttribute('aria-current')"))
        shot("nav-phases-all-1280-night")
        self.one_click(b, '#lanes .lane[data-phase="900-navigation-phase.md"] a.lref', "/phases/900",
                       "drilling into a phase")
        self.assertTrue(b.wait("document.querySelectorAll('#lanes .lane').length === 1", 5),
                        "a phase drilled into is drawn alone")
        self.assertEqual(["Board", "Phases", "#900 Navigation phase"], self.crumbs())
        self.assertEqual("true", b.eval("document.querySelector('#boardnav [data-view=\"phases\"]')"
                                        ".getAttribute('aria-current')"))
        self.assertFalse(b.eval("!!document.querySelector('#lanes a.lref')"),
                         "the phase you are on is not a link to itself")
        shot("nav-phase-drilldown-1280-night")
        # a member opened over the drilled phase keeps the phase as its parent
        b.click('#lanes article.card[data-file="901-first-navigation-member.md"] .title')
        self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')", 5))
        self.assertEqual(["Board", "Phases", "#900", "#901"], self.crumbs("#drawer .dhead"))
        shot("nav-member-over-phase-1280-night")
        b.key("Escape")
        self.assertTrue(b.wait("location.pathname === '/phases/900'", 5),
                        f"closing the member returns to its phase, not {self.here()}")
        # the Phases crumb is the way back to every lane, and Back returns
        self.one_click(b, '#bnwhere .crumbs a[href="/phases"]', "/phases", "the Phases crumb")
        self.assertTrue(b.wait("document.querySelectorAll('#lanes .lane').length >= 2", 5))
        b.eval("history.back()")
        self.assertTrue(b.wait("location.pathname === '/phases/900' && "
                               "document.querySelectorAll('#lanes .lane').length === 1", 5))
        # the Orchestrator can be reached with this phase, and leads back
        b.click('#lanes a[data-phase-orch]')
        self.assertTrue(b.wait("location.pathname === '/orchestrator/phases' && "
                               "location.search === '?card=900'", 5))
        self.assertEqual("page", b.eval("document.querySelector('#views [data-view=\"orchestrator\"]')"
                                        ".getAttribute('aria-current')"))
        if b.wait("!!document.querySelector('#ophase-900 a[data-o=\"board-phase\"]')", 10):
            b.click('#ophase-900 a[data-o="board-phase"]')
            self.assertTrue(b.wait("location.pathname === '/phases/900'", 5),
                            "the Orchestrator's way to the phase on the Board")
        # a card opened from Tasks returns to Tasks
        b.goto("/cards/903")
        b.wait("document.querySelector('#drawer').classList.contains('open')", 10)
        self.assertEqual(["Board", "Tasks", "#903"], self.crumbs("#drawer .dhead"))
        b.click('#drawer .dhead .crumbs li:nth-child(2) a')
        self.assertTrue(b.wait("location.pathname === '/board' && "
                               "!document.querySelector('#drawer').classList.contains('open')", 5),
                        "the Tasks crumb closes the card back to Tasks")
        b.eval(SPY)
        self.passive(b, "Tasks, Phases and the drilldown")

    # ── 3. Sessions and Focus in context ──

    def test_3_sessions_and_focus_from_a_card_and_from_a_phase(self):
        b = self.open("/cards/903?tab=activity",
                      "!!document.querySelector('#drawer [data-sessions-for]')")
        self.one_click(b, '#drawer [data-sessions-for]', "/sessions?card=903", "Sessions for the card")
        self.assertTrue(b.wait("document.querySelectorAll('#frail .sess-row').length === 1", 5))
        self.assertEqual("nav-plain-session", b.eval("document.querySelector('#frail .sess-row').dataset.sid"))
        crumbs = self.crumbs()
        self.assertEqual(["Board", "Tasks", "#903 Plain navigation card", "Sessions for #903"], crumbs[:4])
        self.assertEqual("Sessions for #903", b.eval("document.querySelector('#frail .label').textContent"))
        shot("nav-sessions-card-1280-night")
        # a row keeps the context, Focus keeps it, and the crumbs lead back
        self.one_click(b, '#frail .sess-row', "/sessions/nav-plain-session?card=903", "a session row")
        self.one_click(b, '#fsession a[data-focuslink]', "/focus/nav-plain-session?card=903", "Focus")
        self.assertTrue(b.wait("document.querySelector('#view-focus').classList.contains('on')", 5))
        self.assertEqual("Focus", self.crumbs()[-1])
        shot("nav-focus-card-1280-night")
        self.one_click(b, '#bnwhere .crumbs a[href="/sessions/nav-plain-session?card=903"]',
                       "/sessions/nav-plain-session?card=903", "back to the session")
        self.one_click(b, '#bnwhere .crumbs a[href="/cards/903"]', "/cards/903", "back to the card")
        self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')", 5))
        # from a phase's lane: the phase and its members' sessions
        b.goto("/phases/900")
        b.wait("!!document.querySelector('#lanes a[data-phase-sessions]')", 10)
        b.eval(SPY)
        self.one_click(b, '#lanes a[data-phase-sessions]', "/sessions?card=900", "Sessions for the phase")
        self.assertTrue(b.wait("document.querySelectorAll('#frail .sess-row').length === 1", 5))
        self.assertEqual("nav-member-session", b.eval("document.querySelector('#frail .sess-row').dataset.sid"))
        self.assertEqual(["Board", "Phases", "#900 Navigation phase", "Sessions for #900"], self.crumbs()[:4])
        self.assertEqual("true", b.eval("document.querySelector('#boardnav [data-view=\"phases\"]')"
                                        ".getAttribute('aria-current')"))
        shot("nav-sessions-phase-1280-night")
        # Back walks the visited context
        b.eval("history.back()")
        self.assertTrue(b.wait("location.pathname === '/phases/900'", 5))
        b.eval(SPY)
        self.passive(b, "Sessions and Focus in context")

    def test_4_direct_and_missing_session_links_say_what_they_know(self):
        # a session link with no context names the card it recorded
        b = self.open("/sessions/nav-member-session", "!!document.querySelector('#fsession .s-ctx')")
        line = b.eval("document.querySelector('#fsession .s-ctx').innerText")
        self.assertIn("#901 First navigation member", line)
        self.assertIn("Sessions for #901", line)
        self.assertEqual(["Board", "All sessions"], self.crumbs()[:2])
        # one with no card says so, and invents no parent
        b.goto("/sessions/nav-orphan-session")
        b.wait("!!document.querySelector('#fsession .s-ctx')", 10)
        self.assertIn("No card is recorded", b.eval("document.querySelector('#fsession .s-ctx').innerText"))
        self.assertEqual(["Board", "All sessions"], self.crumbs()[:2])
        # a context that is not on this board shows no sessions in its place
        b.goto("/sessions?card=4040")
        b.wait("!!document.querySelector('#frail .f-empty')", 10)
        self.assertIn("No card #4040 on this board", b.eval("document.querySelector('#frail').innerText"))
        self.assertEqual(0, b.eval("document.querySelectorAll('#frail .sess-row').length"))
        # a session that is not on this board is said, under a card context too
        b.goto("/focus/no-such-session?card=903")
        self.assertTrue(b.wait("document.querySelector('#view-focus').classList.contains('on') && "
                               "document.querySelector('#cgrid').innerText.includes('not on this board')", 10),
                        b.eval("document.querySelector('#cgrid').innerText"))
        # reload keeps the address and its context
        b.goto("/sessions/nav-plain-session?card=903")
        b.wait("!!document.querySelector('#frail .sess-row.sel')", 10)
        self.assertEqual("/sessions/nav-plain-session?card=903", self.here())
        b.eval(SPY)
        self.passive(b, "direct and missing session links")

    # ── 5. the Brain ──

    def search(self, b, text):
        b.eval("(() => { const f = document.querySelector('#bshell [data-bfind=\"q\"]'); f.focus(); "
               f"f.value = {json.dumps(text)}; f.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

    def choose(self, b, which, value):
        b.eval(f"(() => {{ const f = document.querySelector('#bshell [data-bfind=\"{which}\"]'); "
               f"f.value = {json.dumps(value)}; f.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")

    def listed(self, b):
        return b.eval("[...document.querySelectorAll('#bshell .b-list a[data-brain-id]')].map(a => a.textContent)")

    def test_5_brain_search_filter_open_link_and_edit(self):
        b = self.open("/brain", "!!document.querySelector('#bshell [data-bfind=\"q\"]')")
        self.assertEqual(3, len(self.listed(b)))
        self.assertIn("broken.md", b.eval("document.querySelector('#bshell').innerText"))
        self.assertIn("duplicate-id", b.eval("document.querySelector('.b-diag').innerText"),
                      "the diagnostics say how each problem is fixed")
        # by title, then by summary, keeping focus and the caret in the box
        self.search(b, "sync stalled")
        self.assertTrue(b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 1", 5))
        self.assertEqual(["Why sync stalled in March"], self.listed(b))
        self.assertEqual("q", b.eval("document.activeElement && document.activeElement.dataset.bfind"))
        self.search(b, "loopback port")
        b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 1", 5)
        self.assertEqual(["Deploying the preview"], self.listed(b))
        # filters, independent of the search and of each other
        self.search(b, "")
        self.choose(b, "cls", "Unclassified")
        b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 1", 5)
        self.assertEqual(["Choosing the work base"], self.listed(b))
        self.choose(b, "cls", "all")
        self.choose(b, "kind", "Investigation")
        b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 1", 5)
        self.assertEqual(["Why sync stalled in March"], self.listed(b))
        # no results says why and offers the way out
        self.search(b, "nothing like this")
        self.assertTrue(b.wait("document.querySelector('#bshell').innerText.includes('No entry matches')", 5))
        shot("nav-brain-no-results-1280-night")
        b.click('#bshell .b-empty [data-bact="clear-find"]')
        self.assertTrue(b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 3", 5))
        # a search survives going to an entry and back
        self.search(b, "work base")
        guide = self.ids["Choosing the work base"]
        self.one_click(b, f'#bshell .b-list a[data-brain-id="{guide}"]', f"/brain/e/{guide}", "an entry")
        self.assertTrue(b.wait("!!document.querySelector('#btitle')", 5))
        self.assertIn(f"/brain/e/{guide}", b.eval("document.querySelector('#bshell').innerText"))
        b.eval("history.back()")
        self.assertTrue(b.wait("location.pathname === '/brain' && "
                               "document.querySelector('#bshell [data-bfind=\"q\"]').value === 'work base'", 5))
        # its source card crosses to the Board, and Back returns to the Brain
        b.goto(f"/brain/e/{guide}")
        b.wait("!!document.querySelector('#bshell a[href=\"/cards/903\"]')", 10)
        b.click('#bshell a[href="/cards/903"]')
        self.assertTrue(b.wait("location.pathname === '/cards/903' && "
                               "document.querySelector('#views [data-view=\"board\"]').classList.contains('on')", 5))
        b.eval("history.back()")
        self.assertTrue(b.wait(f"location.pathname === '/brain/e/{guide}'", 5))
        b.eval(SPY)
        self.passive(b, "browsing the Brain")
        # an armed action still arms first: Retire writes nothing on one click
        b.wait("!!document.querySelector('#bshell [data-bact=\"retire\"]')", 5)
        b.click('#bshell [data-bact="retire"]')
        b.wait("!!document.querySelector('#bshell .b-form')", 5)
        self.assertEqual([], b.eval("window.__navSpy"), "opening Retire sent a write")
        b.click('#bshell [data-bact="cancel"]')
        # and an edit is a real write, in this fixture only
        b.wait("!!document.querySelector('#bshell [data-bact=\"edit\"]')", 5)
        b.click('#bshell [data-bact="edit"]')
        b.wait("!!document.querySelector('#bshell [data-bf=\"summary\"]')", 5)
        b.eval("(() => { const f = document.querySelector('#bshell [data-bf=\"summary\"]'); f.focus(); "
               "f.value = 'Stacked work starts from pinned review commits'; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.click('#bshell [data-bact="save"]')
        self.assertTrue(b.wait("document.querySelector('#bshell').innerText.includes('Saved')", 10),
                        "the edit did not save")
        b.goto("/brain")
        b.wait("!!document.querySelector('#bshell [data-bfind=\"q\"]')", 10)
        self.search(b, "pinned review commits")
        self.assertTrue(b.wait("document.querySelectorAll('#bshell .b-list a[data-brain-id]').length === 1", 5),
                        "the edited summary is found")
        self.search(b, "")
        # the one write this case makes is the baseline from here on
        type(self).before = tree(self.backend.work)

    # ── 6. themes and widths ──

    def test_6_themes_and_widths(self):
        places = (("/board", "#view-board.on"), ("/phases/900", "#view-phases.on .lane"),
                  ("/sessions?card=903", "#frail .sess-row"), ("/brain", "#bshell .b-find"),
                  ("/cards/901", "#drawer.open .crumbs"))
        for width, height, label in ((1280, 860, "1280"), (390, 844, "390")):
            for dark in (True, False):
                for path, ready in places:
                    b = self.open(path, f"!!document.querySelector('{ready}')", width, height, dark)
                    time.sleep(0.3)
                    self.no_overflow(b, f"{path} {label}")
                    four = b.eval("[...document.querySelectorAll('#views a')].every(a => "
                                  "{ const r = a.getBoundingClientRect(); return r.width > 0 && "
                                  "r.right <= innerWidth + 1 && r.height >= (innerWidth < 760 ? 36 : 20); })")
                    self.assertTrue(four, f"{path} {label}: a primary link is cut off or too small")
                    name = path.strip("/").replace("/", "-").replace("?card=", "-ctx-") or "board"
                    shot(f"nav-{name}-{label}-{'night' if dark else 'day'}")
        S["browser"].eval(SPY)
        self.passive(S["browser"], "themes and widths")


if __name__ == "__main__":
    unittest.main()
