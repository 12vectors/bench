"""Archive all, from the Done column's header (task 187), in the browser.

Every card written, archived or refused here is a disposable one in the
fixture's work state. The fixture's own done card is archived along with
them by a confirmed Archive all — that is the action's scope, every
eligible card in done/ — so each case snapshots done/ first and puts every
byte back afterwards: the other cases read the fixture as it was.

What is held: the control is in the column header and reachable by
keyboard; opening it is a read that names the exact set and the cards it
leaves (a phase card) with why; Cancel and Escape change nothing; a card
completed after the sheet opened is not taken; a refused card is a failed
row while the rest are archived, and its retry sends only the outstanding
card; the progress and summary are drawn; ⌘Z brings the set back; the
per-card archive chip is still on a done card's face; and it all draws at
1440 and 390 pixels in both themes.
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
CARDS = {
    "09-archive-all-first.md": "First finished card for Archive all",
    "10-archive-all-second.md": "Second finished card for Archive all",
    "11-archive-all-third.md": "Third finished card for Archive all",
}
PHASE = "12-archive-all-phase.md"
LATE = "13-archive-all-late.md"
BTN = '#col-done > h2 [data-archive-all]'
SHEET = '#archallsheet'


def card(title: str, status: str = "Done", extra: str = "") -> str:
    return f"# {title}\n\n**Status:** {status}\n**Priority:** Low\n{extra}\nA disposable card.\n"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class ArchiveAllFromTheDoneColumn(unittest.TestCase):

    def setUp(self):
        tasks = S["backend"].work / "tasks"
        self.done, self.archive = tasks / "done", tasks / "archive"
        self.archive.mkdir(exist_ok=True)
        # everything done/ held before this case, byte for byte
        self.before = {p.name: p.read_bytes() for p in self.done.glob("*.md")}
        for name, title in CARDS.items():
            (self.done / name).write_text(card(title), encoding="utf-8")
        (self.done / PHASE).write_text(card("A finished phase", extra="**Type:** Phase\n")
                                       + "\n## Cards\n\n- None.\n", encoding="utf-8")
        self.addCleanup(self.restore)
        self.addCleanup(lambda: S["browser"].eval("localStorage.removeItem('bench-theme')"))

    def restore(self):
        mine = set(CARDS) | {PHASE, LATE}
        for directory in (self.done, self.archive):
            for name in mine:
                (directory / name).unlink(missing_ok=True)
        for name, data in self.before.items():
            (self.archive / name).unlink(missing_ok=True)
            (self.done / name).write_bytes(data)

    def load(self, width=1440, height=900, dark=True):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        first = next(iter(CARDS))
        if not b.wait(f"!!document.querySelector('#board article.card[data-file=\"{first}\"]')", 15):
            raise bv.CheckFailed("the disposable done cards never reached the board")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval(f"document.querySelector('{BTN}').scrollIntoView({{block: 'center', inline: 'center'}})")
        return b

    def open_sheet(self):
        b = S["browser"]
        b.click(BTN)
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} #aa-go, {SHEET} #aa-close')"
                               f" && !document.querySelector('{SHEET} [role=status]')", 10),
                        "the Archive all sheet did not finish reading")
        return b

    def where(self, name):
        return ("done" if (self.done / name).exists() else
                "archive" if (self.archive / name).exists() else None)

    def eligible(self):
        return json.loads(S["backend"].get("/api/archive/all")[1])

    # — the control, and the confirmation that changes nothing —

    def test_header_control_names_the_exact_set_and_cancel_changes_nothing(self):
        b = self.load()
        reading = self.eligible()
        files = [r["file"] for r in reading["eligible"]]
        for name in CARDS:
            self.assertIn(name, files)
        self.assertNotIn(PHASE, files)
        # reachable without opening a card: it is in the column header
        self.assertTrue(b.eval(f"!!document.querySelector('{BTN}') && "
                               f"!document.querySelector('{BTN}').disabled"))
        # the single archive chip is still on a done card's face
        self.assertTrue(b.eval("!!document.querySelector('#board .drop[data-stage=done] "
                               "article.card > .chiprow [data-arch]')"))
        # keyboard: focus it and press Enter
        b.eval(f"document.querySelector('{BTN}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} #aa-go')", 10),
                        "Enter on the header control did not open the confirmation")
        text = b.eval(f"document.querySelector('{SHEET}').innerText")
        n = len(files)
        self.assertIn(f"Archive {n} card", text)
        self.assertIn("including any the Board does not draw", text)
        # the fold is closed while there is something to archive; its rows
        # are in it all the same
        self.assertIn("phase card", b.eval(f"document.querySelector('{SHEET} .aa-left').textContent"))
        listed = b.eval(f"[...document.querySelectorAll('{SHEET} .aa-list:not(.aa-items) > li')]"
                        ".map(l => l.dataset.file)")
        self.assertEqual(sorted(files), sorted(f for f in listed if f in files))
        shot("archive-all-confirm-1440-night")
        b.click(f"{SHEET} #aa-close")
        self.assertTrue(b.wait(f"!document.querySelector('{SHEET}')", 5))
        b.click(BTN)
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} #aa-go')", 10))
        b.key("Escape")
        self.assertTrue(b.wait(f"!document.querySelector('{SHEET}')", 5))
        for name in CARDS:
            self.assertEqual("done", self.where(name), "cancelling archived something")

    # — confirm: progress, summary, the frozen set, undo —

    def test_confirm_archives_the_frozen_set_and_undo_brings_it_back(self):
        b = self.load(1440, 900, dark=False)
        self.open_sheet()
        # completed after the person saw the set: not taken
        (self.done / LATE).write_text(card("Completed after confirmation"), encoding="utf-8")
        # hold the batch's replies a moment so the progress can be read
        b.eval("{ const f = window.fetch; window.fetch = async (u, o) => {"
               " if (String(u).startsWith('/api/archive/all?request='))"
               "   await new Promise(r => setTimeout(r, 400));"
               " return f(u, o); }; }")
        b.click(f"{SHEET} #aa-go")
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} .aa-summary')", 20),
                        "no summary after confirming")
        summary = b.eval(f"document.querySelector('{SHEET} .aa-summary').innerText")
        self.assertIn("archived", summary)
        shot("archive-all-summary-1440-day")
        for name in CARDS:
            self.assertEqual("archive", self.where(name))
            self.assertIn("**Status:** Archived", (self.archive / name).read_text("utf-8"))
        self.assertEqual("done", self.where(LATE), "a card completed after confirmation was taken")
        self.assertEqual("done", self.where(PHASE), "the phase card was archived")
        self.assertIn("this board only",
                      b.eval(f"document.querySelector('{SHEET} .aa-sync').innerText"))
        b.click(f"{SHEET} #aa-close")
        # discoverable: the archive tray counts them
        state = json.loads(S["backend"].get("/api/state")[1])
        self.assertGreaterEqual(state.get("archivedCount", 0), len(CARDS))
        # ⌘Z brings the whole set back
        b.eval("document.body.dispatchEvent(new KeyboardEvent('keydown',"
               " {key: 'z', ctrlKey: true, bubbles: true}))")
        self.assertTrue(b.wait("document.querySelector('#toastmsg').textContent"
                               ".includes('brought back to done/')", 10),
                        "⌘Z did not bring the set back: " +
                        str(b.eval("document.querySelector('#toastmsg').textContent")))
        for name in CARDS:
            self.assertEqual("done", self.where(name))
        self.assertTrue(b.wait(f"!!document.querySelector('#board article.card"
                               f"[data-file=\"{next(iter(CARDS))}\"]')", 10))

    # — partial failure and a retry of only what is outstanding —

    def test_a_refused_card_is_reported_and_retried_alone(self):
        clash = "10-archive-all-second.md"
        (self.archive / clash).write_text(card("Older copy", "Archived"), encoding="utf-8")
        b = self.load(1440, 900, dark=True)
        self.open_sheet()
        b.eval("window.__bulk = []; { const f = window.fetch; window.fetch = async (u, o) => {"
               " if (String(u) === '/api/archive/all') window.__bulk.push(JSON.parse(o.body));"
               " return f(u, o); }; }")
        b.click(f"{SHEET} #aa-go")
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} #aa-retry')", 20),
                        "a partial outcome offered no retry")
        row = b.eval(f"document.querySelector('{SHEET} .aa-items li[data-file=\"{clash}\"]').innerText")
        self.assertIn("failed", row)
        self.assertIn("already exists in archive", row)
        self.assertIn("outstanding", b.eval(f"document.querySelector('{SHEET}').innerText"))
        shot("archive-all-partial-1440-night")
        self.assertEqual("archive", self.where("09-archive-all-first.md"))
        self.assertEqual("done", self.where(clash))
        (self.archive / clash).unlink()
        b.click(f"{SHEET} #aa-retry")
        self.assertTrue(b.wait(f"!!document.querySelector('{SHEET} .aa-summary') && "
                               f"!document.querySelector('{SHEET} #aa-retry')", 20))
        sent = b.eval("window.__bulk")
        self.assertEqual(2, len(sent))
        self.assertEqual([clash], sent[1]["files"], "the retry sent more than the outstanding card")
        self.assertEqual(sent[0]["request"], sent[1]["retryOf"])
        self.assertEqual("archive", self.where(clash))

    # — empty, narrow and both themes —

    def test_it_draws_at_1440_and_390_in_both_themes_and_empty_is_disabled(self):
        sizes = {}
        for dark in (True, False):
            theme = "night" if dark else "day"
            for w, h in [(1440, 900), (390, 780)]:
                b = self.load(w, h, dark)
                g = b.eval(f"(() => {{ const r = document.querySelector('{BTN}').getBoundingClientRect(),"
                           " c = document.querySelector('#col-done').getBoundingClientRect();"
                           " return {w: r.width, h: r.height, l: r.left, r: r.right,"
                           " cl: c.left, cr: c.right, doc: document.documentElement.scrollWidth,"
                           " vw: innerWidth}; })()")
                sizes[f"{w} {theme}"] = g
                self.assertGreaterEqual(g["h"], 24, f"under a 24px target at {w} {theme}")
                self.assertGreaterEqual(g["l"], g["cl"] - 0.5, f"leaves its column at {w} {theme}")
                self.assertLessEqual(g["r"], g["cr"] + 0.5, f"clipped at {w} {theme}")
                shot(f"archive-all-header-{w}-{theme}")
                self.open_sheet()
                shot(f"archive-all-sheet-{w}-{theme}")
                b.key("Escape")
                self.assertTrue(b.wait(f"!document.querySelector('{SHEET}')", 5))
        S["metrics"]["archiveAllButton"] = sizes
        # with nothing eligible in done/ the control is disabled and says so
        for p in list(self.done.glob("*.md")):
            if p.name != PHASE:
                p.unlink()
        b = S["browser"]
        b.goto("/")
        self.assertTrue(b.wait(f"!!document.querySelector('{BTN}') && "
                               f"document.querySelector('{BTN}').disabled", 15),
                        "an empty Done column still offered Archive all")
        self.assertIn("nothing to archive",
                      b.eval(f"document.querySelector('{BTN}').getAttribute('aria-label')"))
        time.sleep(0.2)


if __name__ == "__main__":
    unittest.main()
