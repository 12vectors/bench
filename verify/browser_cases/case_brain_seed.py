"""Seeding the Brain, step one, through the real page (task 192).

A disposable backend of this case's own, in the external split-Git layout:
an application with a README, contribution policy, code, a `.env` and a
dependency tree, and a work repository with a plan. The case drives the
page's own controls by pointer and keyboard:

- **both entry points**: the empty Brain's **Seed Brain** and Settings →
  Setup guide's, each with its own way back;
- **the inventory**: both repositories named with their commits, the
  destination with its revision, suggested sources chosen, the `.env` and
  `node_modules/` excluded with why and not choosable;
- **planned reads, then Inspect**: the list of what would be read, a slice,
  a treatment, and the manifest — read, absent and gaps said apart;
- **passive**: opening, choosing and inspecting change no file, commit or
  card in either repository, and launch nothing;
- **narrow and both themes**: nothing overflows at 390px; screenshots.
"""

import hashlib
import json
import shutil
import subprocess
import unittest
import urllib.request

import benchverify as bv

S = bv.SESSION
README = "# Orchard\n\nOrchard tracks fruit trees.\n\n## Running\n\nmake run\n"


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
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
            and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class Seed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-seed-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        app, work = cls.backend.app, cls.backend.work
        (app / "README.md").write_text(README)
        (app / "CONTRIBUTING.md").write_text("# Contributing\n\nEvery change needs a review.\n")
        (app / "server.py").write_text("print('orchard')\n")
        # over the size read whole: choosable only as a slice of its lines
        (app / "design-notes.md").write_text("A long line of design notes.\n" * 20000)
        (app / ".env").write_text("API_KEY=hunter2\n")
        (app / "node_modules" / "pad").mkdir(parents=True)
        (app / "node_modules" / "pad" / "index.js").write_text("module.exports = 1\n")
        git(app, "add", "-f", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "plans").mkdir(exist_ok=True)
        (work / "plans" / "roadmap.md").write_text("# Roadmap\n\nNext: exports.\n")
        git(work, "add", "-A")
        git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "plan")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.before = (tree(app), tree(work), git(app, "rev-parse", "HEAD").stdout,
                      git(work, "rev-parse", "HEAD").stdout)

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

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def wait_text(self, b, words, sel="#bshell", timeout=15):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, sel)[:1500]}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def seed_page(self, b):
        if not b.wait("!!document.querySelector('#bshell #sd-h-choose, #bshell #sd-h-refused')", 15):
            raise bv.CheckFailed("the seed page never drew: " + self.text(b)[:1200])

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def set_lines(self, b, key, value):
        """Type a slice into a planned source's lines field and leave it."""
        sel = json.dumps(f'#bshell [data-slines="{key}"]')
        if not b.eval(f"(() => {{ const f = document.querySelector({sel}); if (!f) return false;"
                      f" f.focus(); f.value = {json.dumps(value)};"
                      " f.dispatchEvent(new Event('input', {bubbles: true}));"
                      " f.dispatchEvent(new Event('change', {bubbles: true})); return true; })()"):
            raise bv.CheckFailed(f"no lines field for {key}: {self.text(b)[:800]}")

    def table_fits(self, b, where):
        wide = b.eval("(() => { const t = document.querySelector('#bshell .sd-table');"
                      " return t ? [t.scrollWidth, t.clientWidth] : null; })()")
        if wide is None:
            raise bv.CheckFailed(f"{where}: no result table")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the result table overflows {wide}")

    def pick(self, key):
        return f'#bshell [data-spick="{key}"]'

    def reveal(self, b, key):
        """Open the category fold a source sits in, as a person would."""
        sel = json.dumps(self.pick(key))
        if not b.eval(f"(() => {{ const c = document.querySelector({sel}); if (!c) return false;"
                      " const d = c.closest('details'); if (d && !d.open) d.open = true;"
                      " c.scrollIntoView({block: 'center'}); return true; })()"):
            raise bv.CheckFailed(f"no source {key} to choose: {self.text(b)[:800]}")

    # ── the journey, in order ──

    def test_1_empty_brain_offers_seeding_and_returns_to_the_brain(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell a[href=\"/brain/seed\"]')", 15):
            raise bv.CheckFailed("the empty Brain offered no seeding: " + self.text(b)[:800])
        self.theme(b, True)
        b.click('#bshell a[href="/brain/seed"]')
        self.seed_page(b)
        self.assertEqual(b.eval("location.pathname + location.search"), "/brain/seed")
        self.assertEqual(b.eval("document.querySelector('#views [data-view=\"brain\"]')"
                                "?.getAttribute('aria-current') || ''") in ("page", "true"), True,
                         "Brain stays the selected destination")
        page = self.text(b)
        for words in ("Back to Brain", "Application", "Work", "independent", "brain/",
                      "README.md", "CONTRIBUTING.md"):
            self.assertIn(words, page)
        # a category with nothing chosen starts folded, and is listed all the same
        self.assertTrue(b.eval(f"!!document.querySelector({json.dumps(self.pick('work:plans/roadmap.md'))})"))
        self.assertIn(git(self.backend.app, "rev-parse", "HEAD").stdout.strip()[:12], page)
        self.assertNotIn("hunter2", page)
        # suggested sources are chosen; excluded ones cannot be
        self.assertTrue(b.eval(f"document.querySelector({json.dumps(self.pick('app:README.md'))})?.checked"))
        self.assertFalse(b.eval("!!document.querySelector('#bshell [data-spick=\"app:.env\"]')"))
        self.assertFalse(b.eval("!!document.querySelector('#bshell [data-spick^=\"app:node_modules\"]')"))
        shot("brain-seed-inventory-night")
        b.click('#bshell a[href="/brain"]')
        if not b.wait("location.pathname === '/brain' && !document.querySelector('#bshell #sd-h-choose')", 10):
            raise bv.CheckFailed("Back to Brain did not return: " + self.text(b)[:600])

    def test_2_setup_guide_entry_returns_to_setup(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/settings/setup")
        if not b.wait("!!document.querySelector('a[href=\"/brain/seed?from=setup\"]')", 20):
            raise bv.CheckFailed("the Setup guide offered no seeding")
        b.click('a[href="/brain/seed?from=setup"]')
        self.seed_page(b)
        self.assertEqual(b.eval("location.pathname + location.search"), "/brain/seed?from=setup")
        self.wait_text(b, ["Back to Settings › Setup guide"])
        b.click('#bshell a[href="/settings/setup"]')
        if not b.wait("location.pathname === '/settings/setup'", 10):
            raise bv.CheckFailed("the way back did not lead to the Setup guide")

    def test_3_choose_slice_and_inspect(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain/seed")
        self.seed_page(b)
        self.theme(b, False)
        # a pointer adds a source; the planned reads follow
        self.reveal(b, "work:plans/roadmap.md")
        b.click(self.pick("work:plans/roadmap.md"))
        self.wait_text(b, ["Work repository · plans/roadmap.md"],
                       '#bshell section[aria-labelledby="sd-h-plan"]')
        # a keyboard adds another: Space on a focused checkbox
        self.reveal(b, "app:server.py")
        b.eval(f"document.querySelector({json.dumps(self.pick('app:server.py'))}).focus(); true")
        b.key(" ")
        if not b.wait(f"document.querySelector({json.dumps(self.pick('app:server.py'))})?.checked", 5):
            raise bv.CheckFailed("Space did not choose the source")
        self.assertEqual(b.eval("document.activeElement?.dataset?.spick"), "app:server.py",
                         "focus stays on the checkbox after the repaint")
        # a file too large to read whole asks for a slice before Inspect
        self.reveal(b, "app:design-notes.md")
        self.assertIn("a slice of lines only", self.text(b))
        b.click(self.pick("app:design-notes.md"))
        self.wait_text(b, ["needs a slice"], '#bshell section[aria-labelledby="sd-h-plan"]')
        self.assertTrue(b.eval("document.querySelector('#bshell [data-sact=\"inspect\"]').disabled"),
                        "Inspect is offered before the large file has a slice")
        self.assertEqual(b.eval("document.querySelector('#bshell [data-slines=\"app:design-notes.md\"]')"
                                ".getAttribute('aria-invalid')"), "true")
        self.set_lines(b, "app:design-notes.md", "1-200")
        if not b.wait("!document.querySelector('#bshell [data-sact=\"inspect\"]').disabled", 5):
            raise bv.CheckFailed("Inspect stayed disabled after the slice was given")
        # a slice the server refuses: the refusal is drawn, nothing is read
        self.set_lines(b, "app:README.md", "0-3")
        b.click('#bshell [data-sact="inspect"]')
        self.wait_text(b, ["Inspection refused", "a slice is"])
        self.assertTrue(b.eval("document.querySelector('#bshell .b-bad[role=\"alert\"]') !== null"))
        self.assertFalse(b.eval("!!document.querySelector('#bshell #sd-result')"))
        shot("brain-seed-inspection-refused-day")
        # a slice of the README
        self.set_lines(b, "app:README.md", "1-3")
        shot("brain-seed-planned-day")
        b.click('#bshell [data-sact="inspect"]')
        if not b.wait("!!document.querySelector('#bshell #sd-result')", 15):
            raise bv.CheckFailed("no inspection result: " + self.text(b)[:1500])
        result = self.text(b, "#bshell #sd-result")
        for words in ("Inspection complete is not a draft", "lines 1–3", "plans/roadmap.md",
                      "lines 1–200", "Coverage gaps", "Operations", "authorizes nothing"):
            self.assertIn(words, result)
        self.assertEqual(b.eval("document.activeElement?.id"), "sd-result",
                         "focus moves to the result")
        shot("brain-seed-result-day")

    def test_4_narrow_and_night(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        b.goto("/brain/seed")
        self.seed_page(b)
        self.theme(b, True)
        self.no_overflow(b, "the seed page at 390px, night")
        shot("brain-seed-narrow-night")
        self.theme(b, False)
        self.no_overflow(b, "the seed page at 390px, day")
        shot("brain-seed-narrow-day")
        # the result, at 390px: the suggested sources inspected
        b.click('#bshell [data-sact="inspect"]')
        if not b.wait("!!document.querySelector('#bshell #sd-result')", 15):
            raise bv.CheckFailed("no inspection result at 390px: " + self.text(b)[:1500])
        for dark in (False, True):
            self.theme(b, dark)
            self.no_overflow(b, f"the result at 390px, {'night' if dark else 'day'}")
            self.table_fits(b, f"the result at 390px, {'night' if dark else 'day'}")
            shot(f"brain-seed-result-narrow-{'night' if dark else 'day'}")

    def test_4a_a_refused_mapping_is_said_at_both_widths(self):
        b = S["browser"]
        b.origin = self.backend.url
        work = self.backend.work
        outside = self.backend.base / "brain-elsewhere"
        outside.mkdir(exist_ok=True)
        (work / "brain").symlink_to(outside)
        try:
            for width, height in ((1280, 900), (390, 844)):
                b.viewport(width, height)
                b.goto("/brain/seed")
                self.seed_page(b)
                self.wait_text(b, ["Seeding cannot use these repositories", "destination-refused",
                                   "Remedy"])
                self.assertFalse(b.eval("!!document.querySelector('#bshell [data-spick]')"),
                                 "a refused mapping still offered sources")
                self.assertFalse(b.eval("!!document.querySelector('#bshell [data-sact=\"inspect\"]')"))
                self.assertTrue(b.eval("!!document.querySelector('#bshell [role=\"alert\"]')"))
                for dark in (True, False):
                    self.theme(b, dark)
                    self.no_overflow(b, f"the refused mapping at {width}px")
                    shot(f"brain-seed-refused-{width}-{'night' if dark else 'day'}")
        finally:
            (work / "brain").unlink()
            shutil.rmtree(outside, ignore_errors=True)

    def test_5_nothing_was_written_or_launched(self):
        app, work = self.backend.app, self.backend.work
        self.assertEqual((tree(app), tree(work), git(app, "rev-parse", "HEAD").stdout,
                          git(work, "rev-parse", "HEAD").stdout), self.before)
        self.assertFalse((work / "brain").exists(), "seeding wrote the Brain")
        # no card moved or appeared, and no run was started on this board
        with urllib.request.urlopen(self.backend.url + "/api/state", timeout=10) as res:
            state = json.loads(res.read())
        self.assertFalse(state.get("agents"), "an agent was launched")
