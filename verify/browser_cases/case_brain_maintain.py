"""Maintaining the existing explanation through the real page (task 199).

A disposable backend of this case's own, in the external split-Git layout,
with the stub vendor answering one evaluation round in which an
implemented investigation goes Historical and its existing explanation
gains a section. The case drives the page's own controls:

- **Evaluate** by pointer, then **Review proposals**: the update of the
  existing explanation is drawn under its classification — the section
  after Apply, its claims and what is not supported yet, factual, a new
  section, and the two halves applying together — writing nothing;
- **edit and deselect by keyboard**: the person's edit of the section
  reaches the review; deselecting the update says its explanation update
  is pending; selecting it again applies both;
- **Apply** by keyboard, armed then fired: one batch — the investigation
  Historical, the explanation keeping its own text and gaining the edited
  section with bench's provenance line, no new entry — and the receipt;
- **narrow and both themes**: nothing overflows at 390px; screenshots.
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


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def entry(eid, title, kind, klass, body, extra=""):
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {eid}\n**Kind:** {kind}\n"
            f"**Classification:** {klass}\n**Summary:** {title}.\n{extra}\n{body}\n\n"
            f"## Classification history\n\n- 2026-11-01 · {klass} · ronald — written\n")


ENTRIES = {
    "e-exports": entry("e-exports", "How exports are written", "Guide", "Current",
                       "Exports are written by the export module."),
    "e-csv-study": entry("e-csv-study", "Investigating CSV exports", "Investigation",
                         "Current", "We should export CSV with a semicolon."),
}


def reply() -> str:
    doc = {"v": 1, "kind": "bench-brain-evaluation", "draft": "__DRAFT__", "round": 0,
           "outcome": "proposal", "summary": "The CSV investigation was implemented.",
           "evaluations": [
               {"entry": "e-csv-study", "current": "Current", "proposed": "Historical",
                "reason": "the semicolon export is implemented in src/export.py",
                "evidence": ["app:src/export.py"], "explanation": {"entry": "e-exports"},
                "maintain": {
                    "entry": "e-exports", "operation": "append", "section": "CSV exports",
                    "scope": "factual",
                    "body": "CSV files use a semicolon delimiter.",
                    "claims": [{"claim": "the export writes semicolons",
                                "status": "implemented", "evidence": ["app:src/export.py"]},
                               {"claim": "whether customers have it", "status": "unknown"}],
                    "uncovered": ["the byte-order mark is not in the code"]}}]}
    return ("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1)
            .replace('"round": 0', '"round": __ROUND__') + "\nBRAIN>>>\n")


class Maintain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-maintain-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        app, work = cls.backend.app, cls.backend.work
        (app / "src").mkdir(exist_ok=True)
        (app / "src" / "export.py").write_text('DELIMITER = ";"\n')
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "brain").mkdir(exist_ok=True)
        for eid, text in ENTRIES.items():
            (work / "brain" / f"{eid}.md").write_text(text)
        git(work, "add", "-A")
        git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "brain")
        cls.backend.brain_reply.write_text(reply(), encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin

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
            raise bv.CheckFailed(f"never showed {words} in {sel}: "
                                 f"{self.text(b, sel)[:900]!r}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def press(self, b, sel):
        b.eval(f"(() => {{ const e = document.querySelector({json.dumps(sel)});"
               " e.scrollIntoView({block: 'center'}); e.focus(); return true; })()")

    def files(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.backend.work / "brain").glob("*.md")}

    def apply_label(self, b, words, timeout=10):
        cond = ("(document.querySelector('#bshell [data-evact=\"apply\"]')?.innerText || '')"
                f".includes({json.dumps(words)})")
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"the Apply button never read {words!r}: "
                                 + repr(self.text(b, '#bshell [data-evact="apply"]')))

    def test_1_review_shows_the_update(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-evact=\"open\"]')", 15):
            raise bv.CheckFailed("the evaluate panel never drew: " + self.text(b)[:1200])
        self.theme(b, False)
        b.click('#bshell [data-evact="open"]')
        b.wait("!!document.querySelector('#bshell [data-evpick=\"e-csv-study\"]')", 5)
        b.click('#bshell [data-evpick="e-csv-study"]')
        before = self.files()
        b.click('#bshell [data-evact="preflight"]')
        self.wait_text(b, ["What Evaluate would use"], "#bshell #ev-pre")
        b.click('#bshell [data-evact="start"]')
        b.wait("document.querySelector('#bshell [data-evact=\"start\"]')"
               "?.classList.contains('armed')", 5)
        b.click('#bshell [data-evact="start"]')
        self.wait_text(b, ["Proposals ready"], timeout=40)
        b.click('#bshell [data-evact="review"]')
        self.wait_text(b, ["Update of its explanation", "The section after Apply",
                           "Factual documentation", "Implemented in the source",
                           "Not supported yet", "apply together"], "#bshell #ev-review")
        self.apply_label(b, "Apply 1 classification and 1 explanation update")
        self.assertEqual(self.files(), before, "a review wrote something")
        shot("brain-maintain-review-day")

    def test_2_edit_deselect_and_apply_by_keyboard(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain")
        self.wait_text(b, ["Proposals ready"], timeout=20)
        if not b.eval("!!document.querySelector('#bshell #ev-review [data-evmaint]')"):
            b.click('#bshell [data-evact="review"]')
            self.wait_text(b, ["Update of its explanation"], "#bshell #ev-review")
        before = self.files()
        # Space deselects the update: the classification applies alone, said so
        sel = '#bshell [data-evmaint="e-csv-study"][data-evmfield="selected"]'
        self.press(b, sel)
        b.key("Space")
        self.wait_text(b, ["Explanation update pending"], "#bshell #ev-review")
        self.apply_label(b, "Apply 1 classification")
        # Space selects it again
        self.press(b, sel)
        b.key("Space")
        self.apply_label(b, "and 1 explanation update")
        # the person's edit of the section reaches the review
        b.eval("(() => { const t = document.querySelector('#bshell [data-evmaint=\"e-csv-study\"]"
               "[data-evmfield=\"body\"]'); t.focus(); t.value = 'Semicolons, as finance asked.';"
               " t.dispatchEvent(new Event('input', {bubbles: true}));"
               " t.dispatchEvent(new Event('change', {bubbles: true})); return true; })()")
        self.wait_text(b, ["(your edit)", "Semicolons, as finance asked."], "#bshell #ev-review")
        b.wait("!document.querySelector('#bshell [data-evact=\"apply\"]').disabled", 10)
        self.assertEqual(self.files(), before, "editing wrote something")
        self.press(b, '#bshell [data-evact="apply"]')
        b.key("Enter")
        if not b.wait("document.querySelector('#bshell [data-evact=\"apply\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm")
        self.assertEqual(self.files(), before, "arming wrote something")
        self.press(b, '#bshell [data-evact="apply"]')
        b.key("Enter")
        self.wait_text(b, ["Applied", "§ CSV exports updated", "Saved", "Committed"],
                       "#bshell #ev-outcome", timeout=30)
        brain = self.backend.work / "brain"
        self.assertEqual(sorted(p.name for p in brain.glob("*.md")), sorted(before),
                         "a new entry was created")
        exports = (brain / "e-exports.md").read_text()
        self.assertIn("Exports are written by the export module.", exports)
        self.assertIn("## CSV exports", exports)
        self.assertIn("Semicolons, as finance asked.", exports)
        self.assertIn("_Maintained from evidence", exports)
        self.assertIn("**Classification:** Current", exports)
        csv = (brain / "e-csv-study.md").read_text()
        self.assertIn("**Classification:** Historical", csv)
        self.assertIn("its explanation was updated in the same batch", csv)
        shot("brain-maintain-applied-day")
        self.theme(b, True)
        shot("brain-maintain-applied-night")
        self.theme(b, False)

    def test_3_narrow_fits(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        b.goto("/brain/e/e-exports")
        self.wait_text(b, ["CSV exports"], timeout=20)
        for dark in (False, True):
            self.theme(b, dark)
            self.no_overflow(b, f"the maintained entry at 390px, {'night' if dark else 'day'}")
            shot(f"brain-maintain-entry-narrow-{'night' if dark else 'day'}")
