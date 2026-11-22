"""The curated project handbook through the real page (task 171).

A disposable backend of this case's own, in the external split-Git layout,
with `BOARD_COMMIT_MOVES` on and five existing entries in its `brain/`:
a sourced overview bound to an application commit, an architecture guide,
a Historical note, an accepted-but-unbuilt direction and an inferred
contribution habit. The case drives the page's own controls:

- **the landing**: five sections, each empty and saying what it does not
  yet explain, above the ordinary library, which is still there;
- **assemble by pointer**: add an entry to Overview, give a reason, Save —
  covered, committed, the entry's classification untouched;
- **reorder by keyboard and replace**: How it works gets two links, the
  second moved up with Enter on its Up button, then one replaced in place;
  the Historical link reads as a source, not the operative explanation;
- **labels without synthetic certainty**: the overview reads Implemented,
  deployment unknown, sources unchanged; Direction reads agreed future
  work; an inferred habit in Ways of working is a convention, not policy;
  Operations stays empty and says so;
- **a concurrent change**: the record changes on disk while a section is
  being organized; Save is refused with both orders shown, the person's
  order kept, and saving on the current version lands;
- **edit a linked entry**: its title changes on the landing, its id and
  classification do not;
- **narrow, both themes**: nothing overflows at 390px; screenshots;
- and nothing launched: no agent ran, the application and cards unchanged.
"""

import hashlib
import json
import shutil
import subprocess
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


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def entry(entry_id, title, klass="Current", kind="Guide", extra=""):
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {entry_id}\n**Kind:** {kind}\n"
            f"**Classification:** {klass}\n**Summary:** {title}.\n{extra}\nAbout {title}.\n")


class Handbook(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-handbook-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        cls.backend.env["BOARD_COMMIT_MOVES"] = "1"
        work, app = cls.backend.work, cls.backend.app
        git(work, "config", "user.name", "handbook-tester")
        git(work, "config", "user.email", "t@example.com")
        head = git(app, "rev-parse", "HEAD").stdout.strip()
        tracked = (git(app, "ls-files").stdout.split("\n") or [""])[0]
        bound = (f"**Standing:** implemented\n**Bindings:** app:{tracked}@{head}\n"
                 "**Assessed:** 2026-11-22\n") if head and tracked else "**Standing:** implemented\n"
        brain = work / "brain"
        brain.mkdir()
        files = {
            "e-hb-overview": entry("e-hb-overview", "What this project is", extra=bound),
            "e-hb-arch": entry("e-hb-arch", "How the parts fit"),
            "e-hb-old": entry("e-hb-old", "The first design", klass="Historical"),
            "e-hb-future": entry("e-hb-future", "Where it goes next", kind="Investigation",
                                 extra="**Standing:** agreed-future\n"),
            "e-hb-habits": entry("e-hb-habits", "Small commits", extra="**Policy:** inferred — seen "
                                 "in history\n"),
        }
        for name, text in files.items():
            (brain / f"{name}.md").write_text(text, encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "handbook fixture")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(app)
        cls.cards_before = tree(work / "tasks")
        cls.entries_before = {n: (brain / f"{n}.md").read_bytes() for n in files}

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

    def open(self, path="/brain", width=1280, height=900, dark=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
            raise bv.CheckFailed(f"{path}: the Brain drew no heading")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        if path == "/brain" and not b.wait("!!document.querySelector('#bshell [data-hbsec]')", 10):
            raise bv.CheckFailed("the handbook never drew: " + self.text(b)[:800])
        return b

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def section(self, b, sid):
        return self.text(b, f'#bshell [data-hbsec="{sid}"]')

    def wait_text(self, b, words, sel="#bshell", timeout=12):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, sel)[:1500]}")

    def organize(self, b, sid):
        b.click(f'#bshell [data-bact="hb-organize"][data-barg="{sid}"]')
        if not b.wait("!!document.querySelector('#bshell .b-hbedit #hb-add')", 5):
            raise bv.CheckFailed(f"Organize {sid} opened no editor")

    def add(self, b, entry_id):
        b.eval("(() => { const f = document.querySelector('#bshell #hb-add'); "
               f"f.value = {json.dumps(entry_id)}; "
               "f.dispatchEvent(new Event('change', {bubbles: true})); return true; })()")
        b.click('#bshell [data-bact="hb-add"]')

    def reason(self, b, text):
        b.eval("(() => { const f = document.querySelector('#bshell #hb-reason'); f.focus(); "
               f"f.value = {json.dumps(text)}; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")

    def order(self, b, sid):
        return b.eval(f"[...document.querySelectorAll('#bshell [data-hbsec=\"{sid}\"] .b-hbitem "
                      "[data-brain-id], #bshell [data-hbsec=\"" + sid + "\"] .b-hborg .b-hbname .mono')]"
                      ".map(x => x.dataset.brainId || x.textContent)")

    def record(self):
        path = self.backend.work / "brain" / "handbook" / "handbook.md"
        return path.read_text() if path.exists() else ""

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_the_landing_shows_five_empty_sections_above_the_library(self):
        b = self.open()
        titles = b.eval("[...document.querySelectorAll('#bshell [data-hbsec] h3')].map(h => h.textContent)")
        self.assertEqual(titles, ["Overview", "How it works", "Ways of working",
                                  "Direction and open questions", "Operations"])
        for sid in ("overview", "operations"):
            self.assertIn("Not covered yet", self.section(b, sid))
            self.assertIn("does not yet say", self.section(b, sid))
        self.assertTrue(b.eval("!!document.querySelector('#bshell .b-lib .b-list a[data-brain-id=\"e-hb-arch\"]')"),
                        "the ordinary library is still there")
        self.assertEqual(self.record(), "", "reading wrote a handbook record")
        shot("brain-handbook-empty-night")

    def test_2_assemble_overview_by_pointer(self):
        b = self.open()
        self.organize(b, "overview")
        self.add(b, "e-hb-overview")
        self.reason(b, "the project's one-page overview")
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["Covered", "What this project is"], '#bshell [data-hbsec="overview"]')
        self.wait_text(b, ["Saved", "Committed"])
        self.assertIn("- e-hb-overview", self.record())
        self.assertIn("board: brain handbook overview updated",
                      git(self.backend.work, "log", "-1", "--format=%s").stdout)
        ov = self.section(b, "overview")
        self.assertIn("Implemented", ov)
        self.assertIn("deployment unknown", ov)
        self.assertIn("assessed 2026-11-22", ov)
        shot("brain-handbook-overview-night")

    def test_3_reorder_by_keyboard_and_replace(self):
        b = self.open()
        self.organize(b, "how-it-works")
        self.add(b, "e-hb-arch")
        self.add(b, "e-hb-old")
        self.assertEqual(self.order(b, "how-it-works"), ["e-hb-arch", "e-hb-old"])
        # the second link's Up button, by keyboard
        b.eval("document.querySelector('#bshell [data-bact=\"hb-up\"][data-barg=\"1\"]').focus(); true")
        b.key("Enter")
        b.wait("document.querySelector('#bshell .b-hborg .b-hbname .mono')?.textContent === 'e-hb-old'", 5)
        self.assertEqual(self.order(b, "how-it-works"), ["e-hb-old", "e-hb-arch"])
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual(ring, "none", "the focused control shows a ring")
        self.reason(b, "architecture first pass")
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["The first design", "not the operative Current explanation"],
                       '#bshell [data-hbsec="how-it-works"]')
        self.assertIn("Partly covered", self.section(b, "how-it-works"))
        # replace the Historical link in place
        self.organize(b, "how-it-works")
        b.eval("(() => { const f = document.querySelector('#bshell [data-hbrep=\"0\"]'); "
               "f.value = 'e-hb-overview'; f.dispatchEvent(new Event('change', {bubbles: true})); "
               "return true; })()")
        b.wait("document.querySelector('#bshell .b-hborg .b-hbname .mono')?.textContent === 'e-hb-overview'", 5)
        self.reason(b, "the overview explains the parts better")
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["Covered"], '#bshell [data-hbsec="how-it-works"]')
        self.assertEqual(self.order(b, "how-it-works"), ["e-hb-overview", "e-hb-arch"])
        self.assertIn("Historical", (self.backend.work / "brain" / "e-hb-old.md").read_text(),
                      "linking promoted nothing")
        shot("brain-handbook-how-it-works-night")

    def test_4_direction_and_ways_of_working_are_labelled_honestly(self):
        b = self.open()
        self.organize(b, "direction")
        self.add(b, "e-hb-future")
        self.reason(b, "accepted, not built")
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["Agreed future work", "not built, so not deployed"],
                       '#bshell [data-hbsec="direction"]')
        self.organize(b, "ways-of-working")
        self.add(b, "e-hb-habits")
        self.reason(b, "observed habit")
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["Convention — not approved policy", "no approved working policy"],
                       '#bshell [data-hbsec="ways-of-working"]')
        self.assertIn("Not covered yet", self.section(b, "operations"))
        shot("brain-handbook-labelled-night")
        b.eval("S.dark = false; applyTheme(); true")
        shot("brain-handbook-labelled-day")

    def test_5_a_concurrent_change_is_refused_with_both_orders(self):
        b = self.open()
        self.organize(b, "overview")
        self.add(b, "e-hb-arch")
        self.reason(b, "adding the architecture")
        path = self.backend.work / "brain" / "handbook" / "handbook.md"
        path.write_text(path.read_text().replace("- e-hb-overview\n", "- e-hb-overview\n- e-hb-future\n"))
        b.click('#bshell [data-bact="hb-save"]')
        self.wait_text(b, ["Not saved: the handbook changed", "What you tried to save", "on disk now"])
        self.assertIn("e-hb-future", path.read_text(), "nothing was overwritten")
        self.assertEqual(self.order(b, "overview"), ["e-hb-overview", "e-hb-arch"], "the order is kept")
        shot("brain-handbook-conflict-night")
        b.click('#bshell [data-bact="hb-rebase"]')
        b.wait("!document.querySelector('#bshell .b-hbedit .b-conflict')", 5)
        b.click('#bshell [data-bact="hb-save"]')
        b.wait("!document.querySelector('#bshell .b-hbedit')", 10)
        text = self.record()
        self.assertIn("- e-hb-arch", text)
        self.assertNotIn("- e-hb-future", text.split("## How it works")[0],
                         "saving on the current version replaced the section with the person's order")

    def test_6_editing_a_linked_entry_keeps_its_identity_and_place(self):
        b = self.open("/brain/e/e-hb-arch")
        if not b.wait("!!document.querySelector('#bshell .b-authority')", 10):
            raise bv.CheckFailed("the entry drew no Source authority panel: " + self.text(b)[-1500:])
        self.wait_text(b, ["Source authority", "Handbook", "How it works", "2 of 2"],
                       "#bshell .b-authority")
        b.click('#bshell [data-bact="edit"]')
        b.eval("(() => { const f = document.querySelector('#bshell [data-bf=\"title\"]'); f.focus(); "
               "f.value = 'How the pieces fit'; f.dispatchEvent(new Event('input', {bubbles: true})); "
               "return true; })()")
        b.click('#bshell [data-bact="save"]')
        if not b.wait("!document.querySelector('#bshell .b-form')", 12):
            raise bv.CheckFailed("the edit was not saved: " + self.text(b, "#bshell .b-form")[-1500:])
        self.wait_text(b, ["How the pieces fit"], "#bshell #btitle")
        self.assertEqual(b.eval("location.pathname"), "/brain/e/e-hb-arch")
        b = self.open()
        self.wait_text(b, ["How the pieces fit"], '#bshell [data-hbsec="how-it-works"]')
        self.assertIn("**Classification:** Current",
                      (self.backend.work / "brain" / "e-hb-arch.md").read_text())

    def test_7_narrow_and_both_themes(self):
        for dark in (True, False):
            theme = "night" if dark else "day"
            b = self.open("/brain", 390, 844, dark)
            self.no_overflow(b, f"handbook narrow {theme}")
            shot(f"brain-handbook-narrow-{theme}")
            self.organize(b, "how-it-works")
            self.no_overflow(b, f"organize narrow {theme}")
            shot(f"brain-handbook-organize-narrow-{theme}")
            b.click('#bshell [data-bact="hb-cancel"]')
            b.goto("/brain/e/e-hb-overview")
            b.wait("!!document.querySelector('#bshell .b-authority')", 10)
            self.no_overflow(b, f"entry narrow {theme}")
            shot(f"brain-handbook-entry-narrow-{theme}")

    def test_8_nothing_else_moved_and_nothing_launched(self):
        self.assertEqual(tree(self.backend.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before, "a card changed")
        for name in ("e-hb-overview", "e-hb-old", "e-hb-future", "e-hb-habits"):
            self.assertEqual((self.backend.work / "brain" / f"{name}.md").read_bytes(),
                             self.entries_before[name], f"{name} was written by a handbook change")
        code, raw = self.backend.get("/api/state")
        self.assertEqual(json.loads(raw).get("agents") or [], [], "an agent ran")


if __name__ == "__main__":
    unittest.main()
