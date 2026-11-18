"""Shareable addresses for the board's views and cards (task 134).

In a real browser, against the disposable backend:

1. an address opened fresh — a card and one of its sections, Settings
   sections, Phases with one phase, Sessions and Focus with one session —
   shows that destination once the board has been read, and a refresh
   shows it again; the address and what is shown agree;
2. opening a card, switching its sections, closing it and changing view
   make one history entry each, and Back and Forward walk them without
   writing any entry of their own;
3. a card's address survives a move to another stage and a title edit;
4. an archived card's address shows the actual card, read-only, with none
   of its actions; a number no card has, a number two cards share and a
   malformed identifier each say so, and nothing else is shown in its
   place; an ended session is linkable and said to be ended, and a
   session, a phase or a Settings section the board does not have is said
   to be not there;
5. readings of the board, stream frames and repaints neither move the
   address nor add history, a pending address included;
6. the destinations are real links — a modifier-click is left to the
   browser — and Copy link copies the canonical address on this origin;
7. `#setup` still opens the setup guide, now at `/settings/setup`;
8. none of it moved a card, wrote to the work state or launched anything.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION

CARD = "310-a-linkable-card.md"
TITLE = "310 — A linkable card"
ARCHIVED = "311-an-archived-card.md"
PHASE = "312-a-linkable-phase.md"
TWINS = ("313-one-of-two.md", "313-the-other.md")
LIVE_SID = "url-session-live"
ENDED_SID = "url-session-ended"

# What no address may cause: a request to any route that changes something.
MUTATIONS = ("/api/move", "/api/agent/", "/api/archive", "/api/unarchive", "/api/phase/",
             "/api/task/complete", "/api/settings/apply", "/api/pr/", "/api/drive/",
             "/api/command/", "/api/gates/override", "/api/plan/replan")
REQUESTS = """(() => performance.getEntriesByType('resource').map(e => e.name))()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def card_text(title: str, status: str, extra: str = "") -> str:
    return (f"# {title}\n\n**Status:** {status}\n**Priority:** Low\n\n"
            f"## Context\n\nLINKABLE-BRIEF of {title}.\n{extra}\n## Acceptance\n\n- [ ] It opens\n")


def snapshot(root) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class ShareableUrls(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.tasks = backend.work / "tasks"
        cls.made = [
            cls.tasks / "backlog" / CARD,
            cls.tasks / "archive" / ARCHIVED,
            cls.tasks / "to-do" / PHASE,
            cls.tasks / "backlog" / TWINS[0],
            cls.tasks / "done" / TWINS[1],
        ]
        cls.made[0].write_text(card_text(TITLE, "Backlog"), encoding="utf-8")
        cls.made[1].write_text(card_text("311 — An archived card", "Archived",
                                         "ARCHIVED-ONLY-TEXT\n"), encoding="utf-8")
        cls.made[2].write_text(
            "# 312 — A linkable phase\n\n**Status:** To Do\n**Priority:** Low\n"
            "**Type:** Phase\n\n## Cards\n\n- 310 — A linkable card\n", encoding="utf-8")
        cls.made[3].write_text(card_text("313 — One of two", "Backlog"), encoding="utf-8")
        cls.made[4].write_text(card_text("313 — The other", "Done"), encoding="utf-8")
        # two sessions: one still reporting, one that ended
        for sid, kinds in ((LIVE_SID, ("session", "read")), (ENDED_SID, ("session", "read", "end"))):
            for n, kind in enumerate(kinds):
                code, _ = backend.post("/api/events", {"session": sid, "kind": kind,
                                                       "summary": f"{kind} {n} of {sid}"})
                if code != 200:
                    raise bv.SetupError(f"the fixture session {sid} could not be recorded: {code}")
        cls.before = snapshot(backend.work)

    @classmethod
    def tearDownClass(cls):
        for path in cls.made:
            path.unlink(missing_ok=True)
        (cls.tasks / "review" / CARD).unlink(missing_ok=True)

    def open(self, path: str, ready: str, timeout: float = 15):
        b = S["browser"]
        b.goto(path)
        self.assertTrue(b.wait("!!S.state", 15), f"{path}: the board was never read")
        self.assertTrue(b.wait(ready, timeout), f"{path}: the destination never appeared")
        return b

    def here(self) -> str:
        return S["browser"].eval("location.pathname + location.search")

    def assertNoMutation(self):
        names = S["browser"].eval(REQUESTS) or []
        hit = [n for n in names if any(m in n for m in MUTATIONS)]
        self.assertEqual([], hit, "opening an address asked for a change")

    # ── 1. fresh entry and refresh ──────────────────────────────────────

    def test_1_fresh_entry_and_refresh_show_the_destination(self):
        S["browser"].viewport(1280, 900)
        drawer = (f"document.querySelector('#drawer.open #dtitle') && "
                  f"document.querySelector('#dtitle').textContent.includes('A linkable card')")
        for _ in range(2):   # the second is a refresh of the same address
            b = self.open("/cards/310?tab=brief", drawer + " && document.querySelector("
                          "'#dtab-brief').getAttribute('aria-selected') === 'true'")
            self.assertEqual("/cards/310?tab=brief", self.here())
            self.assertTrue(b.eval("document.querySelector('#view-board').classList.contains('on')"),
                            "a card opened by its address is drawn over the Board")
            self.assertTrue(b.wait("(document.querySelector('#dsec-brief') || {}).innerText"
                                   ".includes('LINKABLE-BRIEF')", 10), "the brief was not read")
            self.assertNoMutation()
        shot("urls-card-direct-entry-1280x900")
        for path, ready in (
                ("/settings/agents", "document.querySelector('#snav [data-ssection=\"agents\"]')"
                 "?.getAttribute('aria-current') === 'page'"),
                ("/settings", "document.querySelector('#snav [data-ssection=\"overview\"]')"
                 "?.getAttribute('aria-current') === 'page'"),
                ("/phases/312", "document.querySelector('#view-phases.on .lane.sel')"
                 f"?.dataset.phase === {json.dumps(PHASE)}"),
                (f"/sessions/{ENDED_SID}", f"document.querySelector('#frail .sess-row.sel')"
                 f"?.dataset.sid === {json.dumps(ENDED_SID)}"),
                (f"/focus/{LIVE_SID}", f"document.querySelector('#focussel').value === {json.dumps(LIVE_SID)}"
                 " && document.querySelector('#view-focus').classList.contains('on')"),
                ("/board", "document.querySelector('#view-board.on') && !document.querySelector('#drawer.open')")):
            for _ in range(2):
                self.open(path, ready)
                self.assertEqual(path, self.here(), f"{path} did not keep its address")
        b = S["browser"]
        self.open(f"/sessions/{ENDED_SID}", "!!document.querySelector('#fsession [data-sstatus]')")
        self.assertEqual("ended", b.eval("document.querySelector('#fsession [data-sstatus]').textContent"))
        shot("urls-ended-session-1280x900")
        self.assertNoMutation()

    # ── 2. history ──────────────────────────────────────────────────────

    def test_2_back_and_forward_walk_the_destinations(self):
        b = self.open("/board", f"!!document.querySelector('#board article.card[data-file=\"{CARD}\"]')")
        start = b.eval("history.length")
        b.eval(f"document.querySelector('#board article.card[data-file=\"{CARD}\"]').click()")
        self.assertTrue(b.wait("location.pathname === '/cards/310'", 5), self.here())
        self.assertTrue(b.wait("!!document.querySelector('#dtab-reviews')", 5))
        b.click("#dtab-reviews")
        self.assertTrue(b.wait("location.search === '?tab=reviews'", 5), self.here())
        b.click("#closeDrawer")
        self.assertTrue(b.wait("location.pathname === '/board' && !document.querySelector('#drawer.open')", 5),
                        self.here())
        b.click('#views [data-view="settings"]')
        self.assertTrue(b.wait("location.pathname === '/settings'", 5), self.here())
        self.assertTrue(b.wait("document.querySelectorAll('#snav [data-ssection]').length > 3", 15))
        b.click('#snav [data-ssection="installation"]')
        self.assertTrue(b.wait("location.pathname === '/settings/installation'", 5), self.here())
        self.assertEqual(start + 5, b.eval("history.length"), "one entry per destination")
        writes = b.eval("routeWrites()")
        walk = [("/settings", "document.querySelector('#view-settings.on') && "
                 "document.querySelector('#snav [data-ssection=\"overview\"]').getAttribute('aria-current') === 'page'"),
                ("/board", "document.querySelector('#view-board.on') && !document.querySelector('#drawer.open')"),
                ("/cards/310?tab=reviews", "document.querySelector('#drawer.open') && "
                 "document.querySelector('#dtab-reviews').getAttribute('aria-selected') === 'true'"),
                ("/cards/310", "document.querySelector('#drawer.open') && "
                 "document.querySelector('#dtab-overview').getAttribute('aria-selected') === 'true'"),
                ("/board", "!document.querySelector('#drawer.open')")]
        for want, shown in walk:
            b.eval("history.back()")
            self.assertTrue(b.wait(f"location.pathname + location.search === {json.dumps(want)}", 5),
                            f"Back did not reach {want}: {self.here()}")
            self.assertTrue(b.wait(shown, 5), f"{want} is the address but not what is shown")
        for want, shown in reversed(walk[:-1]):
            b.eval("history.forward()")
            self.assertTrue(b.wait(f"location.pathname + location.search === {json.dumps(want)}", 5),
                            f"Forward did not reach {want}: {self.here()}")
            self.assertTrue(b.wait(shown, 5), f"{want} is the address but not what is shown")
        self.assertEqual(writes, b.eval("routeWrites()"), "Back or Forward wrote an entry")
        self.assertEqual(start + 5, b.eval("history.length"), "Back and Forward changed the history")

    # ── 3. a card's address follows the card ────────────────────────────

    def test_3_a_card_address_survives_a_move_and_a_title_edit(self):
        src, dst = self.tasks / "backlog" / CARD, self.tasks / "review" / CARD
        dst.write_text(card_text("310 — A renamed card", "Review"), encoding="utf-8")
        src.unlink()
        try:
            b = self.open("/cards/310", "document.querySelector('#drawer.open #dtitle')"
                          "?.textContent.includes('A renamed card')")
            self.assertEqual("review", b.eval("S.selected.stage"))
            self.assertEqual("/cards/310", self.here())
        finally:
            src.write_text(card_text(TITLE, "Backlog"), encoding="utf-8")
            dst.unlink(missing_ok=True)

    # ── 4. archived, missing, ambiguous, unavailable ─────────────────────

    def test_4_archived_and_missing_destinations_say_what_they_are(self):
        b = self.open("/cards/311", "!!document.querySelector('#drawer.open #darchived')")
        self.assertIn("ARCHIVED-ONLY-TEXT", b.eval("document.querySelector('#drawerbody').innerText"))
        buttons = b.eval("[...document.querySelectorAll('#drawerbody button')].map(x => x.id)")
        self.assertEqual(["dcopylink", "closeDrawer"], buttons, "an archived card offers an action")
        self.assertFalse(b.eval("!!document.querySelector('#drawerbody .cardtools, #drawerbody [data-act]')"))
        self.assertEqual("/cards/311", self.here())
        shot("urls-archived-card-1280x900")
        b = self.open("/cards/9999", "!!document.querySelector('#drawer.open .dmissing[role=\"alert\"]')")
        self.assertIn("No card #9999", b.eval("document.querySelector('#drawerbody').innerText"))
        self.assertEqual(None, b.eval("S.selected"), "another card was chosen in its place")
        shot("urls-missing-card-1280x900")
        b = self.open("/cards/313", "!!document.querySelector('#drawer.open .dmissing ul')")
        links = b.eval("[...document.querySelectorAll('#drawerbody .dmissing a[data-route]')].map(a => a.getAttribute('href'))")
        self.assertIn("/cards/313-one-of-two", links)
        self.assertIn("/cards/313-the-other", links)
        # a section a card does not have is said, and the card opens on its
        # Overview at its canonical address — replaced, not added
        self.open("/cards/310?tab=nope", "document.querySelector('#drawer.open #dtab-overview')"
                  "?.getAttribute('aria-selected') === 'true'")
        self.assertEqual("/cards/310", self.here())
        self.assertTrue(b.wait("document.querySelector('#toastmsg').textContent.includes('no section')", 5))
        self.open("/sessions/no-such-session", "!!document.querySelector('#fsession [role=\"alert\"]')")
        self.assertEqual(0, b.eval("document.querySelectorAll('#frail .sess-row.sel').length"),
                         "another session was selected in its place")
        self.open("/focus/no-such-session", "!!document.querySelector('#cgrid [role=\"alert\"]')")
        self.open("/phases/999", "!document.querySelector('#phasenote').hidden")
        self.assertIn("No phase #999", b.eval("document.querySelector('#phasenote').innerText"))
        self.open("/phases/310", "!document.querySelector('#phasenote').hidden")
        self.assertIn("is a card, not a phase", b.eval("document.querySelector('#phasenote').innerText"))
        self.open("/settings/no-such-section", "(document.querySelector('#shead h1') || {}).textContent === 'No such section'")
        self.assertEqual(0, b.eval("document.querySelectorAll('#snav [aria-current=\"page\"]').length"))
        shot("urls-unknown-settings-section-1280x900")
        self.assertNoMutation()

    # ── 5. live readings neither move the address nor add history ───────

    def test_5_live_refresh_holds_the_address_and_the_history(self):
        backend = S["backend"]
        for path, ready in (("/cards/310?tab=activity", "document.querySelector('#dtab-activity')"
                             "?.getAttribute('aria-selected') === 'true'"),
                            ("/cards/311", "!!document.querySelector('#darchived')"),
                            (f"/sessions/{ENDED_SID}", "!!document.querySelector('#fsession [data-sstatus]')")):
            b = self.open(path, ready)
            length, writes = b.eval("history.length"), b.eval("routeWrites()")
            for n in range(4):
                b.eval("loadState().then(() => true)")
                backend.post("/api/events", {"session": LIVE_SID, "kind": "read",
                                             "summary": f"a live frame {n}"})
                b.eval("(() => { render(); return true; })()")
            time.sleep(1.0)
            self.assertEqual(path, self.here(), f"a live update moved the address off {path}")
            self.assertTrue(b.eval(ready), f"a live update changed what {path} shows")
            self.assertEqual(length, b.eval("history.length"), "a live update added history")
            self.assertEqual(writes, b.eval("routeWrites()"))

    # ── 6. real links, and Copy link ────────────────────────────────────

    def test_6_destinations_are_links_and_copy_link_copies_the_address(self):
        b = self.open("/board", f"!!document.querySelector('#board article.card[data-file=\"{CARD}\"] a.ref')")
        self.assertEqual("/cards/310", b.eval(
            f"document.querySelector('#board article.card[data-file=\"{CARD}\"] a.ref').getAttribute('href')"))
        self.assertEqual(["/board", "/phases", "/sessions", "/focus", "/settings"],
                         b.eval("[...document.querySelectorAll('#views a[data-view]')].map(a => a.getAttribute('href'))"))
        # a modifier-click is the browser's: the page neither handles nor cancels it
        left = b.eval(f"""(() => {{
          const a = document.querySelector('#board article.card[data-file="{CARD}"] a.ref');
          let seen = null;
          const last = (e) => {{ seen = e.defaultPrevented; e.preventDefault(); }};
          window.addEventListener('click', last, {{ once: true }});
          a.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, ctrlKey: true }}));
          return {{ seen, selected: !!S.selected, at: location.pathname }};
        }})()""")
        self.assertEqual({"seen": False, "selected": False, "at": "/board"}, left)
        # a session row is a link to that session
        self.open("/sessions", "document.querySelectorAll('#frail a.sess-row').length >= 2")
        self.assertIn(f"/sessions/{LIVE_SID}", b.eval(
            "[...document.querySelectorAll('#frail a.sess-row')].map(a => a.getAttribute('href'))"))
        # Copy link: captured rather than written to the person's clipboard
        capture = """(() => { window.__copied = [];
          Object.defineProperty(navigator, 'clipboard', { configurable: true,
            value: { writeText: async (t) => { __copied.push(t); } } }); return true; })()"""
        self.open("/cards/310?tab=reviews", "!!document.querySelector('#drawer.open #dcopylink')")
        b.eval(capture)
        b.click("#dcopylink")
        self.assertTrue(b.wait("__copied.length === 1", 5), "Copy link copied nothing")
        origin = b.eval("location.origin")
        self.assertEqual(f"{origin}/cards/310?tab=reviews", b.eval("__copied[0]"))
        self.assertTrue(b.wait("document.querySelector('#toastmsg').textContent.includes('link copied')", 5))
        b.click("#copylink")
        self.assertTrue(b.wait("__copied.length === 2", 5))
        self.assertEqual(f"{origin}/cards/310?tab=reviews", b.eval("__copied[1]"))
        self.open(f"/focus/{LIVE_SID}", "!!document.querySelector('#cgrid [data-copy-link]')")
        b.eval(capture)
        b.click("#cgrid [data-copy-link]")
        self.assertTrue(b.wait("__copied.length === 1", 5))
        self.assertEqual(f"{origin}/focus/{LIVE_SID}", b.eval("__copied[0]"))
        # a refused clipboard is said, with the link to copy by hand
        b.eval("""(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true,
          value: { writeText: async () => { throw new Error('no'); } } });
          document.execCommand = () => false; return true; })()""")
        b.click("#copylink")
        self.assertTrue(b.wait("document.querySelector('#toastmsg').textContent.includes('refused the clipboard')", 5))
        # a link carries no token, draft or secret: only the path and a tab
        for link in b.eval("[...document.querySelectorAll('[data-copy-link]')].map(x => x.dataset.copyLink)"):
            self.assertRegex(link, r"^/[a-z]+(/[A-Za-z0-9._~%-]+)?(\?tab=[a-z]+)?$")

    # ── 7. the terminal handoff's #setup ─────────────────────────────────

    def test_7_the_setup_alias_still_opens_the_guide(self):
        b = S["browser"]
        length = None
        b.goto("/#setup")
        self.assertTrue(b.wait("location.pathname === '/settings/setup' && !location.hash", 10), self.here())
        self.assertTrue(b.wait("document.querySelector('#snav [data-ssection=\"setup\"]')"
                               "?.getAttribute('aria-current') === 'page'", 15))
        length = b.eval("history.length")
        b.goto("/")
        self.assertTrue(b.wait("location.pathname === '/board'", 10), "/ is not the Board's address")
        self.assertEqual(length + 1, b.eval("history.length"), "the alias was corrected with an extra entry")

    # ── 8. the server's boundary, seen from the browser ──────────────────

    def test_8_an_unknown_path_is_still_not_found_and_nothing_changed(self):
        b = S["browser"]
        b.goto("/not-a-destination")
        self.assertEqual("not found", b.eval("document.body.innerText.trim()"))
        b.goto("/cards/310/extra")
        self.assertEqual("not found", b.eval("document.body.innerText.trim()"))
        self.assertEqual(self.before, snapshot(S["backend"].work),
                         "opening addresses wrote to the work state")
        code, raw = S["backend"].get("/api/state")
        self.assertEqual(200, code)
        self.assertFalse(json.loads(raw).get("agents"), "opening addresses launched an agent")


if __name__ == "__main__":
    unittest.main()
