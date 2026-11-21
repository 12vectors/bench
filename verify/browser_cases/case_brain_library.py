"""The Brain's kind-first library, in the real browser (task 183).

A disposable backend of this case's own, reached only through the
navigation case's watching proxy (`Watch`: every request that is not a
read is recorded), carries a `brain/` with entries of four kinds and all
four classifications. The case drives the list as a person would:

- **the default view** opens kind first: a section per kind that has
  entries, in the contract's order, Current and Unclassified each under
  its own heading inside a kind, and Historical and Superseded hidden —
  counted, said to be kept, never drawn as rows;
- **composed controls**: choosing a kind narrows to it; showing Historical
  and Superseded (each its own checkbox in the right-hand sidebar) draws
  them under their own headings within their kind; search composes with
  both; a kind with no entries says so, which is not the same sentence as
  a search that matched nothing; the scoped counts follow; Reset restores
  the default without touching a file;
- **direct links to hidden history**: an Historical entry's address opens
  it while the library hides it, and says how the list shows it;
- **Back and Forward** keep the browsing context: the kind and the
  classifications chosen survive a visit to an entry and back, with focus
  on the entry the reader came from;
- **the keyboard**: kind buttons and classification checkboxes are
  reached by Tab and work with Space, keeping focus where it was;
- **the layout**, measured: at 1280px (and at 1510px, the width of the
  "before" screenshot the card carries) the sidebar's box is to the right
  of the list's, beside it; at 390px the controls fold into one button
  above the list, open on request, and neither the view nor the page runs
  wider than the window — in Night and Daylight;
- **passive browsing**: none of it sends a write (so no model, agent or
  check is launched) or changes a file in the work root.
"""

import hashlib
import json
import shutil
import time
import unittest

import benchverify as bv
from case_navigation import Watch

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


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def entry(entry_id, title, kind, klass, summary, extra=""):
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {entry_id}\n**Kind:** {kind}\n"
            f"**Classification:** {klass}\n**Summary:** {summary}\n{extra}\n"
            f"{title}, written for the library case.\n")


# (id, title, kind, classification, summary, extra header lines)
ENTRIES = (
    ("e-lib-guide-now", "Running the board locally", "Guide", "Current", "Start, stop and the port"),
    ("e-lib-guide-old", "Running the board before start.sh", "Guide", "Superseded",
     "The old way to start it", "**Superseded by:** e-lib-guide-now\n"),
    ("e-lib-guide-draft", "Running two boards side by side", "Guide", "Unclassified",
     "Not yet assessed"),
    ("e-lib-dec-now", "Sync never merges", "Decision", "Current", "Fast-forward or replay only"),
    ("e-lib-dec-old", "Merging on origin, first attempt", "Decision", "Historical",
     "How merges were made before the policy"),
    ("e-lib-inv-old", "Why the port hopped", "Investigation", "Historical",
     "A TIME_WAIT socket read as occupied"),
    ("e-lib-rev-open", "Review of the sync replay", "Review", "Unclassified",
     "Findings not yet assessed"),
)
TITLE = {e[0]: e[1] for e in ENTRIES}
DEFAULT = ["e-lib-guide-now", "e-lib-guide-draft", "e-lib-dec-now", "e-lib-rev-open"]


class BrainLibrary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-library-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.watch = Watch()
        env = dict(shared.env)
        env["BOARD_ALLOWED_ORIGINS"] = ",".join(
            o for o in (env.get("BOARD_ALLOWED_ORIGINS", "").strip(), cls.watch.url) if o)
        cls.backend = bv.Backend(shared.tool, base, env, shared.python)
        brain = cls.backend.work / "brain"
        brain.mkdir()
        for eid, title, kind, klass, summary, *extra in ENTRIES:
            (brain / f"{eid}.md").write_text(entry(eid, title, kind, klass, summary, *extra),
                                             encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.watch.start(cls.backend.port)

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.watch.stop()
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def setUp(self):
        self.mark = self.watch.mark()
        self.reads = self.watch.reads
        self.before = tree(self.backend.work)

    def passive(self, what):
        self.assertGreater(self.watch.reads, self.reads, f"{what}: nothing went through the watch")
        self.assertEqual([], self.watch.since(self.mark), f"{what}: browsing sent a write")
        self.assertEqual(self.before, tree(self.backend.work), f"{what}: the work root changed")

    # ── helpers ──

    def open(self, path="/brain", width=1280, height=860, dark=True):
        b = S["browser"]
        b.origin = self.watch.url
        b.viewport(width, height)
        b.goto(path)
        ready = "!!document.querySelector('#bshell .b-lib')" if path == "/brain" \
            else "!!document.querySelector('#btitle')"
        if not b.wait(ready, 15):
            raise bv.CheckFailed(f"{path}: never ready: "
                                 + str(b.eval("document.querySelector('#bshell')?.innerText")))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        time.sleep(0.2)
        return b

    def reset_view(self, b):
        """The library's choices are kept across navigation on purpose;
        each test starts from the default by the page's own Reset."""
        if b.eval("!!document.querySelector('#bshell .b-navreset [data-bact=\"clear-find\"]')"):
            b.eval("document.querySelector('#bshell .b-navreset [data-bact=\"clear-find\"]').click(); true")
            b.wait("!document.querySelector('#bshell .b-navreset [data-bact=\"clear-find\"]')", 5)

    def listed(self, b):
        return b.eval("[...document.querySelectorAll('#bshell .b-results .b-list a[data-brain-id]')]"
                      ".map(a => a.dataset.brainId)")

    def grouped(self, b):
        """{kind: {classification: [ids]}} as drawn."""
        return b.eval("""(() => { const out = {};
          document.querySelectorAll('#bshell .b-kind').forEach(s => {
            const g = out[s.dataset.bkindsec] = {};
            s.querySelectorAll('.b-group').forEach(x => {
              g[x.dataset.bgroup] = [...x.querySelectorAll('a[data-brain-id]')].map(a => a.dataset.brainId);
            });
          }); return out; })()""")

    def kind(self, b, k):
        b.click(f'#bshell [data-bkind="{k}"]')
        if not b.wait(f"document.querySelector('#bshell [data-bkind=\"{k}\"]')"
                      ".getAttribute('aria-pressed') === 'true'", 5):
            raise bv.CheckFailed(f"choosing the kind {k} did not select it")

    def show(self, b, klass, on=True):
        b.click(f'#bshell [data-bshow="{klass}"]')
        if not b.wait(f"document.querySelector('#bshell [data-bshow=\"{klass}\"]').checked === {json.dumps(on)}", 5):
            raise bv.CheckFailed(f"the {klass} control did not turn {'on' if on else 'off'}")

    def search(self, b, text):
        b.eval("(() => { const f = document.querySelector('#bshell [data-bfind=\"q\"]'); f.focus(); "
               f"f.value = {json.dumps(text)}; f.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def rect(self, b, sel):
        return b.eval(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect();"
                      " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width}; })()")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    # ── the tests ──

    def test_1_default_view_is_kind_first_with_history_hidden(self):
        b = self.open()
        self.reset_view(b)
        kinds = b.eval("[...document.querySelectorAll('#bshell .b-kind')].map(s => s.dataset.bkindsec)")
        self.assertEqual(["Guide", "Investigation", "Decision", "Review"], kinds,
                         "a section per kind with entries, in the contract's order")
        self.assertEqual({"Guide": {"Current": ["e-lib-guide-now"], "Unclassified": ["e-lib-guide-draft"]},
                          "Investigation": {},
                          "Decision": {"Current": ["e-lib-dec-now"]},
                          "Review": {"Unclassified": ["e-lib-rev-open"]}}, self.grouped(b))
        self.assertEqual(sorted(DEFAULT), sorted(self.listed(b)))
        for hidden in ("e-lib-guide-old", "e-lib-dec-old", "e-lib-inv-old"):
            self.assertNotIn(TITLE[hidden], self.text(b, "#bshell .b-results"))
        # hidden is counted and said to be kept, per kind and in total
        self.assertIn("1 Historical entry is hidden here, kept and not deleted",
                      self.text(b, '#bshell [data-bkindsec="Investigation"]'))
        self.assertIn("1 Superseded entry is hidden here",
                      self.text(b, '#bshell [data-bkindsec="Guide"]'))
        status = self.text(b, "#bfindcount")
        self.assertIn("Showing 4 of 7 entries", status)
        self.assertIn("2 Historical, 1 Superseded hidden", status)
        self.assertIn("not deleted", status)
        # the controls' defaults, and the scoped counts beside them
        self.assertEqual({"Current": True, "Unclassified": True, "Historical": False, "Superseded": False},
                         b.eval("Object.fromEntries([...document.querySelectorAll('#bshell [data-bshow]')]"
                                ".map(x => [x.dataset.bshow, x.checked]))"))
        self.assertEqual("All kinds: 4 shown of 7 entries",
                         b.eval("document.querySelector('#bshell [data-bkind=\"all\"]').getAttribute('aria-label')"))
        self.assertEqual("Guide: 2 shown of 3 entries",
                         b.eval("document.querySelector('#bshell [data-bkind=\"Guide\"]').getAttribute('aria-label')"))
        self.assertEqual("Prototype: 0 shown of 0 entries",
                         b.eval("document.querySelector('#bshell [data-bkind=\"Prototype\"]').getAttribute('aria-label')"))
        # the old classification-first sections and horizontal filter strip are gone
        self.assertEqual(0, b.eval("document.querySelectorAll('#bshell [id^=\"bsec-\"], #bshell .b-findrow, "
                                   "#bshell select[data-bfind]').length"))
        self.passive("the default library")

    def test_2_kind_visibility_search_and_reset_compose(self):
        b = self.open()
        self.reset_view(b)
        self.kind(b, "Decision")
        self.assertEqual({"Decision": {"Current": ["e-lib-dec-now"]}}, self.grouped(b))
        self.show(b, "Historical")
        self.assertEqual({"Decision": {"Current": ["e-lib-dec-now"], "Historical": ["e-lib-dec-old"]}},
                         self.grouped(b), "Historical drawn under its own heading within its kind")
        # Historical rows keep their classification on the row as well
        self.assertIn("Historical", b.eval("document.querySelector('#bshell .b-row a[data-brain-id=\"e-lib-dec-old\"]')"
                                           ".closest('.b-row').innerText"))
        self.assertEqual("Historical 1", " ".join(self.text(
            b, '#bshell label:has([data-bshow="Historical"])').split()[:2]),
            "the Historical count is scoped to the chosen kind")
        shot("brain-library-decision-with-history-1280-night")
        # every kind, both kinds of history shown
        self.kind(b, "all")
        self.show(b, "Superseded")
        g = self.grouped(b)
        self.assertEqual(["e-lib-guide-old"], g["Guide"]["Superseded"])
        self.assertEqual(["e-lib-inv-old"], g["Investigation"]["Historical"])
        self.assertEqual(list(g["Guide"]), ["Current", "Unclassified", "Superseded"],
                         "Current, Unclassified, then the history, each apart")
        self.assertEqual(7, len(self.listed(b)))
        # search composes with both
        self.search(b, "running")
        b.wait("document.querySelectorAll('#bshell .b-results .b-list a[data-brain-id]').length === 3", 5)
        self.assertEqual(sorted(["e-lib-guide-now", "e-lib-guide-old", "e-lib-guide-draft"]),
                         sorted(self.listed(b)))
        self.show(b, "Superseded", False)
        self.assertEqual(sorted(["e-lib-guide-now", "e-lib-guide-draft"]), sorted(self.listed(b)))
        self.assertIn("1 Superseded", self.text(b, "#bfindcount"))
        # no match, said with what matched but is hidden
        self.search(b, "before start.sh")
        b.wait("document.querySelector('#bshell .b-empty')", 5)
        empty = self.text(b, "#bshell .b-empty")
        self.assertIn("No entry matches", empty)
        self.assertIn("1 Superseded entry matches but the classification controls hide it", empty)
        # a kind with no entries is a different sentence from no match
        self.search(b, "")
        self.kind(b, "Prototype")
        self.assertIn("No Prototype entries in this Brain yet",
                      self.text(b, '#bshell [data-bkindsec="Prototype"]'))
        self.assertEqual(0, b.eval("document.querySelectorAll('#bshell .b-empty').length"))
        # Reset restores the default, and nothing was written
        b.click('#bshell .b-navreset [data-bact="clear-find"]')
        b.wait("document.querySelector('#bshell [data-bkind=\"all\"]').getAttribute('aria-pressed') === 'true'", 5)
        self.assertEqual(sorted(DEFAULT), sorted(self.listed(b)))
        self.assertEqual("all", b.eval("document.activeElement?.dataset?.bkind || null"),
                         "Reset puts focus on All kinds")
        self.passive("choosing kinds, showing history, searching and resetting")

    def test_3_direct_links_to_hidden_history_and_back(self):
        b = self.open("/brain/e/e-lib-dec-old")
        self.assertIn("Merging on origin, first attempt", self.text(b, "#btitle"))
        self.assertIn("Hidden from the Brain's default list", self.text(b))
        shot("brain-library-hidden-entry-direct-1280-night")
        # browse with history shown, open it from the list, and come back
        b.click('#bshell .b-back a[href="/brain"]')
        b.wait("!!document.querySelector('#bshell .b-lib')", 10)
        self.reset_view(b)
        self.kind(b, "Decision")
        self.show(b, "Historical")
        b.click('#bshell .b-list a[data-brain-id="e-lib-dec-old"]')
        if not b.wait("location.pathname === '/brain/e/e-lib-dec-old' && !!document.querySelector('#btitle')", 10):
            raise bv.CheckFailed("the Historical entry did not open from the list")
        b.eval("history.back()")
        if not b.wait("location.pathname === '/brain' && !!document.querySelector('#bshell .b-lib')", 10):
            raise bv.CheckFailed("Back did not return to the library")
        self.assertEqual("true", b.eval("document.querySelector('#bshell [data-bkind=\"Decision\"]')"
                                        ".getAttribute('aria-pressed')"), "the chosen kind survives Back")
        self.assertTrue(b.eval("document.querySelector('#bshell [data-bshow=\"Historical\"]').checked"),
                        "the history shown survives Back")
        b.wait("document.activeElement?.dataset?.brainId === 'e-lib-dec-old'", 3)
        self.assertEqual("e-lib-dec-old", b.eval("document.activeElement?.dataset?.brainId || null"),
                         "focus returns to the entry the reader came from")
        b.eval("history.forward()")
        self.assertTrue(b.wait("location.pathname === '/brain/e/e-lib-dec-old'", 10), "Forward")
        b.eval("history.back()")
        b.wait("!!document.querySelector('#bshell .b-lib')", 10)
        self.reset_view(b)
        self.passive("direct links, Back and Forward")

    def test_4_the_keyboard_works_the_sidebar(self):
        b = self.open()
        self.reset_view(b)
        b.eval("document.querySelector('#bshell .b-title').focus(); true")
        b.key("Tab")
        self.assertEqual("new", b.eval("document.activeElement?.dataset?.bact || null"))
        b.key("Tab")
        self.assertEqual("q", b.eval("document.activeElement?.dataset?.bfind || null"))
        order = []
        for _ in range(7):
            b.key("Tab")
            order.append(b.eval("document.activeElement?.dataset?.bkind || null"))
        self.assertEqual(["all", "Guide", "Investigation", "Decision", "Review", "Prototype", "Attachment"], order)
        # Space on a focused kind chooses it and keeps focus there
        for _ in range(4):
            b.key("Tab", shift=True)
        self.assertEqual("Investigation", b.eval("document.activeElement?.dataset?.bkind || null"))
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual("none", ring, "a focused kind shows a focus ring")
        b.key("Space")
        self.assertTrue(b.wait("document.querySelector('#bshell [data-bkind=\"Investigation\"]')"
                               ".getAttribute('aria-pressed') === 'true'", 5), "Space chose the kind")
        self.assertEqual("Investigation", b.eval("document.activeElement?.dataset?.bkind || null"))
        shot("brain-library-keyboard-kind-1280-night")
        # on to the classification checkboxes; Space shows Historical
        for _ in range(5):
            b.key("Tab")
        self.assertEqual("Current", b.eval("document.activeElement?.dataset?.bshow || null"))
        b.key("Tab")
        b.key("Tab")
        self.assertEqual("Historical", b.eval("document.activeElement?.dataset?.bshow || null"))
        b.key("Space")
        self.assertTrue(b.wait("document.querySelector('#bshell [data-bshow=\"Historical\"]').checked", 5))
        self.assertEqual("Historical", b.eval("document.activeElement?.dataset?.bshow || null"),
                         "focus stays on the checkbox through the repaint")
        self.assertEqual(["e-lib-inv-old"], self.listed(b))
        self.reset_view(b)
        self.passive("the keyboard")

    def test_5_layout_measured_in_both_themes(self):
        for width, height, label in ((1280, 860, "1280"), (1510, 857, "1510")):
            for dark in (True, False):
                b = self.open("/brain", width, height, dark)
                self.reset_view(b)
                nav, res = self.rect(b, "#bshell .b-nav"), self.rect(b, "#bshell .b-results")
                self.assertGreaterEqual(nav["left"], res["right"], f"{label}: the sidebar is right of the list")
                self.assertLess(nav["top"], res["bottom"], f"{label}: beside it, not below")
                self.assertGreaterEqual(res["width"], 600, f"{label}: the list stays readable {res}")
                self.assertTrue(200 <= nav["width"] <= 320, f"{label}: sidebar width {nav}")
                self.assertFalse(b.eval("document.querySelector('#bshell .b-navtoggle').offsetParent !== null"),
                                 f"{label}: no fold button on a wide window")
                self.no_overflow(b, f"library {label}")
                shot(f"brain-library-after-{label}-{'night' if dark else 'day'}")
        for dark in (True, False):
            b = self.open("/brain", 390, 844, dark)
            self.reset_view(b)
            toggle = "#bshell .b-navtoggle"
            self.assertEqual("false", b.eval(f"document.querySelector('{toggle}').getAttribute('aria-expanded')"))
            self.assertFalse(b.eval("document.querySelector('#bshell .b-navbody').offsetParent !== null"),
                             "the controls are folded on a narrow window")
            self.assertIn("All kinds · Current, Unclassified", self.text(b, toggle),
                          "the folded button says what is shown")
            nav, res = self.rect(b, "#bshell .b-nav"), self.rect(b, "#bshell .b-results")
            self.assertLessEqual(nav["bottom"], res["top"] + 1, "one column: the controls above the list")
            self.assertGreaterEqual(res["width"], 330, f"the list keeps the width {res}")
            self.no_overflow(b, "library 390 folded")
            shot(f"brain-library-after-390-folded-{'night' if dark else 'day'}")
            b.click(toggle)
            b.wait(f"document.querySelector('{toggle}').getAttribute('aria-expanded') === 'true'", 5)
            self.assertEqual("toggle-nav", b.eval("document.activeElement?.dataset?.bact || null"))
            self.assertTrue(b.eval("document.querySelector('#bshell .b-navbody').offsetParent !== null"))
            self.kind(b, "Guide")
            self.show(b, "Superseded")
            self.assertEqual(["e-lib-guide-now", "e-lib-guide-draft", "e-lib-guide-old"], self.listed(b))
            self.no_overflow(b, "library 390 open")
            shot(f"brain-library-after-390-open-{'night' if dark else 'day'}")
            self.reset_view(b)
            b.click(toggle)
            b.wait(f"document.querySelector('{toggle}').getAttribute('aria-expanded') === 'false'", 5)
        self.passive("the layout at three widths")
