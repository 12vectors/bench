"""The Brain, read in the real browser (task 157).

A disposable backend of this case's own carries a `brain/` in its work
root: a Current guide that links to a Historical investigation, a
tombstone replaced by the guide, and a file that breaks the contract. The
case drives the page as a person would and takes screenshots to look at:

- **the list** at `/brain`: the four classification sections, each entry
  by title with its kind and summary, and the broken file named as a
  diagnostic — at 1280px and 390px, Night and Daylight, with nothing
  overflowing the narrow window;
- **an entry** at its stable address: its Markdown rendered, the entry's
  hostile markup inert (no script ran, no handler attribute, no
  `javascript:` link), focus on its heading, the permalink shown;
- **links and Back**: a link written in the entry's body reaches the other
  entry in this tab, Back returns to the first, and Back again returns to
  the list with focus on the entry the reader came from;
- **the keyboard**: Tab from the list's heading reaches the first entry,
  with a visible focus ring, and Enter opens it;
- **honest absences**: an unknown id says so, a tombstone says it was
  removed and by what it was replaced, and the shared backend — which has
  no `brain/` — explains what fills the Brain without offering a control
  that does nothing;
- **passive reads**: browsing changes no file in the work root.
"""

import hashlib
import shutil
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


def entry(entry_id, title, kind, klass, extra, body, history=""):
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {entry_id}\n**Kind:** {kind}\n"
            f"**Classification:** {klass}\n{extra}\n{body}\n"
            + (f"\n## Classification history\n\n{history}" if history else ""))


ROUTING = entry(
    "e-report-routing", "How review reports stay on their own board", "Guide", "Current",
    "**Summary:** A report is filed on the card its run was launched for, or not at all.\n"
    "**Source:** #148, plans/148-report-isolation.md, https://example.com/notes\n"
    "**Related:** e-report-isolation\n",
    "A review run's report is filed on the card the run was launched for, on the board it "
    "was launched from — or not at all.\n\n"
    "## What decides\n\n"
    "- the board bound at launch\n- the card's stage when the run ends\n\n"
    "| Case | Result |\n| --- | --- |\n| card moved | filed in its new stage |\n"
    "| board gone | not filed, said in the ticker |\n\n"
    "```\nreports.file(card, text)\n```\n\n"
    "Read [the investigation that led here](e-report-isolation) for the history.\n\n"
    "Hostile markup stays text: <script>window.__brainPwned = 1</script> "
    "<img src=x onerror=\"window.__brainPwned = 2\"> and "
    "[a javascript link](javascript:window.__brainPwned=3).\n",
    "- 2026-11-19 · Unclassified · ronald — written before the fix landed\n"
    "- 2026-11-20 · Current · ronald — explains the shipped routing\n")
ISOLATION = entry(
    "e-report-isolation", "Why reports leaked into the live board", "Investigation",
    "Historical",
    "**Summary:** The investigation behind the routing fix; no longer directs work.\n"
    "**Source:** #148\n**Related:** e-report-routing\n",
    "Reports were appended by file name, so a card that had moved boards received them.\n",
    "- 2026-11-20 · Historical · ronald — the routing fix shipped\n")
OLD = entry(
    "e-old-routing-notes", "Old routing notes", "Guide", "Superseded",
    "**Summary:** Early notes.\n**Superseded by:** e-report-routing\n"
    "**Removed:** folded into the routing guide\n**Replaced by:** e-report-routing\n",
    "Kept only so its address explains what happened.\n")


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class BrainReading(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        brain = cls.backend.work / "brain"
        brain.mkdir()
        (brain / "report-routing.md").write_text(ROUTING, encoding="utf-8")
        (brain / "report-isolation.md").write_text(ISOLATION, encoding="utf-8")
        (brain / "old-notes.md").write_text(OLD, encoding="utf-8")
        (brain / "broken.md").write_text(entry("e-broken", "Broken", "Wiki", "Current", "", "x"),
                                         encoding="utf-8")
        (cls.backend.work / "plans").mkdir(exist_ok=True)
        (cls.backend.work / "plans" / "148-report-isolation.md").write_text("# Plan\n")
        cls.backend.start()
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

    def open(self, path, width=1280, height=860, dark=True, origin=None):
        b = S["browser"]
        b.origin = origin or self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
            raise bv.CheckFailed(f"{path}: the Brain view drew no heading: "
                                 + str(b.eval("document.querySelector('#bshell').innerText")))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def test_1_the_list_in_both_themes_and_widths(self):
        for width, height, label in ((1280, 860, "desktop"), (390, 844, "narrow")):
            for dark in (True, False):
                b = self.open("/brain", width, height, dark)
                text = b.eval("document.querySelector('#bshell').innerText")
                for word in ("Current", "Historical", "Superseded", "Unclassified",
                             "How review reports stay on their own board",
                             "Why reports leaked into the live board", "Old routing notes",
                             "could not be shown", "broken.md"):
                    self.assertIn(word, text, f"{label}: {word}")
                self.assertEqual(b.eval("document.querySelector('#views [data-view=\"brain\"]')"
                                        ".getAttribute('aria-current')"), "page")
                self.no_overflow(b, f"list {label}")
                shot(f"brain-list-{label}-{'night' if dark else 'day'}")

    def test_2_an_entry_renders_safely_with_focus_on_its_heading(self):
        b = self.open("/brain")
        b.click('.b-list a[data-brain-id="e-report-routing"]')
        if not b.wait("location.pathname === '/brain/e/e-report-routing' && "
                      "!!document.querySelector('#btitle')", 10):
            raise bv.CheckFailed("the entry never opened at its address")
        self.assertEqual(b.eval("document.activeElement && document.activeElement.id"), "btitle")
        prose = "document.querySelector('.b-prose')"
        self.assertTrue(b.eval(f"!!{prose}.querySelector('table') && !!{prose}.querySelector('pre') "
                               f"&& !!{prose}.querySelector('h3, h2') && !!{prose}.querySelector('li')"))
        self.assertEqual(b.eval("typeof window.__brainPwned"), "undefined")
        self.assertEqual(b.eval(f"{prose}.querySelectorAll('script, img, [onerror]').length"), 0)
        self.assertEqual(b.eval(f"[...{prose}.querySelectorAll('a')].filter(a => "
                                "/^\\s*javascript:/i.test(a.getAttribute('href'))).length"), 0)
        text = b.eval("document.querySelector('#bshell').innerText")
        for word in ("/brain/e/e-report-routing", "Copy link", "Card #148", "not on this board",
                     "plans/148-report-isolation.md", "not fetched or checked",
                     "explains the shipped routing", "written before the fix landed"):
            self.assertIn(word, text, word)
        shot("brain-entry-desktop-night")
        b.eval("S.dark = false; applyTheme(); true")
        shot("brain-entry-desktop-day")
        b.viewport(390, 844)
        time.sleep(0.3)
        self.no_overflow(b, "entry narrow")
        shot("brain-entry-narrow-day")
        b.eval("S.dark = true; applyTheme(); true")
        shot("brain-entry-narrow-night")

    def test_3_body_links_back_and_the_return_focus(self):
        b = self.open("/brain")
        b.click('.b-list a[data-brain-id="e-report-routing"]')
        b.wait("!!document.querySelector('#btitle')", 10)
        b.click('.b-prose a[href="/brain/e/e-report-isolation"]')
        if not b.wait("location.pathname === '/brain/e/e-report-isolation' && "
                      "(document.querySelector('#btitle')?.textContent || '').includes('leaked')", 10):
            raise bv.CheckFailed("a link in the entry's body did not reach the other entry")
        self.assertIn("How review reports stay on their own board",
                      b.eval("document.querySelector('.b-side').innerText"),
                      "the investigation lists the guide that links to it")
        b.eval("history.back()")
        if not b.wait("location.pathname === '/brain/e/e-report-routing' && "
                      "(document.querySelector('#btitle')?.textContent || '').includes('stay on')", 10):
            raise bv.CheckFailed("Back did not return to the first entry")
        b.eval("history.back()")
        if not b.wait("location.pathname === '/brain' && !!document.querySelector('.b-list')", 10):
            raise bv.CheckFailed("Back did not return to the list")
        b.wait("document.activeElement?.dataset?.brainId === 'e-report-routing'", 3)
        self.assertEqual(b.eval("document.activeElement?.dataset?.brainId || null"),
                         "e-report-routing", "focus returns to the entry the reader came from")

    def test_4_the_keyboard_reaches_and_opens_an_entry(self):
        b = self.open("/brain")
        b.eval("document.querySelector('#bshell .b-title').focus(); true")
        # the list's own action comes first (task 158), then its search and
        # its two filters (task 159), then its entries
        b.key("Tab")
        self.assertEqual(b.eval("document.activeElement?.dataset?.bact || null"), "new")
        for control in ("q", "cls", "kind"):
            b.key("Tab")
            self.assertEqual(b.eval("document.activeElement?.dataset?.bfind || null"), control)
        b.key("Tab")
        first = b.eval("document.activeElement?.dataset?.brainId || null")
        self.assertEqual(first, "e-report-routing")
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual(ring, "none", "the focused entry link shows no focus ring")
        shot("brain-list-keyboard-focus")
        b.key("Enter")
        if not b.wait("location.pathname === '/brain/e/e-report-routing'", 10):
            raise bv.CheckFailed("Enter on a focused entry did not open it")

    def test_5_unknown_tombstone_and_the_empty_brain(self):
        b = self.open("/brain/e/e-never-written")
        self.assertIn("No entry e-never-written on this board",
                      b.eval("document.querySelector('#bshell').innerText"))
        b = self.open("/brain/e/e-old-routing-notes")
        text = b.eval("document.querySelector('#bshell').innerText")
        self.assertIn("This entry was removed", text)
        self.assertIn("folded into the routing guide", text)
        self.assertIn("Replaced by How review reports stay on their own board", text)
        shot("brain-tombstone")
        b = self.open("/brain", origin=self.origin)
        text = b.eval("document.querySelector('#bshell').innerText")
        self.assertIn("There is no brain/ directory", text)
        self.assertIn("the first entry you save does", text)
        # the one control is the one that fills it (task 158); reading made nothing
        self.assertEqual(b.eval("[...document.querySelectorAll('#bshell button')]"
                                ".map(x => x.dataset.bact)"), ["new"],
                         "the empty Brain offers exactly one control: New entry")
        shot("brain-empty")

    def test_6_browsing_changed_nothing_in_the_work_root(self):
        self.assertEqual(tree(self.backend.work), self.before)
