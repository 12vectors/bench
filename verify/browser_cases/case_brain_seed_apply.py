"""Seeding the Brain, step three, through the real page (task 194).

A disposable backend of this case's own, in the external split-Git layout,
with the stub vendor answering one seed round (as in
`case_brain_seed_draft`). The case drives the page's own controls:

- inspect and **Generate draft**, then **Review sections** (pointer): each
  section's target entry, evidence strength, citations and before/after,
  the handbook before and after, coverage and what publishing means —
  writing nothing;
- **choose by keyboard**: Space deselects Ways of working; the Overview's
  title is edited; the review follows;
- **Apply** by keyboard, armed then fired: only the selected sections and
  the handbook's links are written, the outcome says saved, committed and
  shared apart, and offers Open handbook and Return to board;
- **narrow and both themes**: nothing overflows at 390px; screenshots;
- the application and the cards are unchanged, and nothing was launched
  beyond the one drafting round.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

import benchverify as bv
from case_brain_seed_draft import README, reply

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
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
            and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class SeedApply(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-seed-apply-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        app, work = cls.backend.app, cls.backend.work
        (app / "README.md").write_text(README)
        (app / "CONTRIBUTING.md").write_text("# Contributing\n\nEvery change needs a review.\n")
        (app / "server.py").write_text("print('orchard')\n")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "plans").mkdir(exist_ok=True)
        (work / "plans" / "roadmap.md").write_text("# Roadmap\n\nNext: exports.\n")
        git(work, "add", "-A")
        git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "plan")
        cls.backend.brain_reply.write_text(reply(), encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = (tree(app), git(app, "rev-parse", "HEAD").stdout)
        cls.cards_before = tree(work / "tasks") if (work / "tasks").exists() else {}

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

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def wait_text(self, b, words, sel="#bshell", timeout=20):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, sel)[:1800]}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def choose(self, b, key):
        sel = json.dumps(f'#bshell [data-spick="{key}"]')
        if not b.eval(f"(() => {{ const c = document.querySelector({sel}); if (!c) return false;"
                      " const d = c.closest('details'); if (d && !d.open) d.open = true;"
                      " c.scrollIntoView({block: 'center'}); return true; })()"):
            raise bv.CheckFailed(f"no source {key}: {self.text(b)[:800]}")
        if not b.eval(f"document.querySelector({sel}).checked"):
            b.click(f'#bshell [data-spick="{key}"]')

    def press(self, b, sel):
        b.eval(f"document.querySelector({json.dumps(sel)}).focus(); true")

    def test_1_generate_then_review(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain/seed")
        if not b.wait("!!document.querySelector('#bshell #sd-h-choose')", 15):
            raise bv.CheckFailed("the seed page never drew: " + self.text(b)[:1200])
        self.theme(b, False)
        b.click('#bshell [data-sact="clear"]')
        for key in ("app:README.md", "app:CONTRIBUTING.md", "app:server.py",
                    "work:plans/roadmap.md"):
            self.choose(b, key)
        b.click('#bshell [data-sact="inspect"]')
        if not b.wait("!!document.querySelector('#bshell #sd-result')", 15):
            raise bv.CheckFailed("no inspection result: " + self.text(b)[:1500])
        b.click('#bshell [data-sact="gen-preflight"]')
        self.wait_text(b, ["Approval"], "#bshell #sd-gen")
        b.click('#bshell [data-sact="generate"]')
        b.click('#bshell [data-sact="generate"]')
        self.wait_text(b, ["Private draft ready", "6 · Review and apply"], timeout=40)
        b.click('#bshell [data-sact="review"]')
        self.wait_text(b, ["s-overview", "New entry", "every claim sourced", "Handbook",
                           "Left missing", "operations", "no application file"],
                       "#bshell #sd-review")
        self.assertFalse((self.backend.work / "brain").exists(), "the review wrote the Brain")
        shot("brain-seed-review-day")

    def test_2_choose_by_keyboard_and_apply(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain/seed")
        self.wait_text(b, ["Private draft ready"], timeout=20)
        if not b.eval("!!document.querySelector('#bshell #sd-review [data-schoice]')"):
            b.click('#bshell [data-sact="review"]')
            self.wait_text(b, ["s-overview"], "#bshell #sd-review")
        # Space deselects Ways of working; the review follows
        self.press(b, '#bshell [data-schoice="ways-of-working"][data-sfield="selected"]')
        b.key(" ")
        if not b.wait("!document.querySelector('#bshell [data-schoice=\"ways-of-working\"]"
                      "[data-sfield=\"selected\"]').checked && "
                      "(document.querySelector('#bshell [data-sact=\"apply\"]')?.innerText || '')"
                      ".includes('Apply 3 sections')", 10):
            raise bv.CheckFailed("deselecting did not reach the review: "
                                 + self.text(b, "#bshell #sd-review")[:1500])
        b.eval("(() => { const f = document.querySelector('#bshell [data-schoice=\"overview\"]"
               "[data-sfield=\"title\"]'); f.focus(); f.value = 'Orchard, briefly';"
               " f.dispatchEvent(new Event('input', {bubbles: true}));"
               " f.dispatchEvent(new Event('change', {bubbles: true})); return true; })()")
        self.wait_text(b, ["Orchard, briefly"], "#bshell #sd-review")
        b.wait("!document.querySelector('#bshell [data-sact=\"apply\"]').disabled", 10)
        self.press(b, '#bshell [data-sact="apply"]')
        b.key("Enter")
        if not b.wait("document.querySelector('#bshell [data-sact=\"apply\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm: " + self.text(b, "#bshell #sd-review")[:900])
        self.assertFalse((self.backend.work / "brain").exists(), "arming wrote the Brain")
        shot("brain-seed-apply-armed-day")
        self.press(b, '#bshell [data-sact="apply"]')
        b.key("Enter")
        self.wait_text(b, ["Applied", "Saved", "Committed", "Shared", "Open handbook",
                           "Return to board"], "#bshell #sd-outcome", timeout=30)
        brain = self.backend.work / "brain"
        names = sorted(p.name for p in brain.glob("*.md"))
        self.assertEqual(names, ["s-direction.md", "s-how-it-works.md", "s-overview.md"])
        self.assertIn("Orchard, briefly", (brain / "s-overview.md").read_text())
        self.assertIn("- s-overview", (brain / "handbook" / "handbook.md").read_text())
        self.assertEqual((tree(self.backend.app), git(self.backend.app, "rev-parse",
                                                       "HEAD").stdout), self.app_before)
        work = self.backend.work
        self.assertEqual(tree(work / "tasks") if (work / "tasks").exists() else {},
                         self.cards_before)
        shot("brain-seed-applied-day")
        self.theme(b, True)
        shot("brain-seed-applied-night")

    def test_3_narrow_fits_and_the_handbook_opens(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        b.goto("/brain/seed")
        self.wait_text(b, ["Applied to the Brain", "Open handbook"], timeout=20)
        for dark in (False, True):
            self.theme(b, dark)
            self.no_overflow(b, f"the outcome at 390px, {'night' if dark else 'day'}")
            shot(f"brain-seed-applied-narrow-{'night' if dark else 'day'}")
        b.click('#bshell #sd-outcome a[href="/brain"]')
        self.wait_text(b, ["Project handbook", "Orchard, briefly"], timeout=20)
