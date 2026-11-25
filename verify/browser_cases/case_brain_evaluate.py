"""Evaluate classifications through the real page (task 196).

A disposable backend of this case's own, in the external split-Git layout,
with the stub vendor answering one evaluation round. The case drives the
page's own controls:

- on the Brain's list, **Choose entries to evaluate** (pointer), select
  four, **Review what Evaluate would use** — the entries with their
  revisions and the manual override, the related cards, the harness and
  model, the sources, the budget and the approval — writing nothing;
- **Evaluate**, armed then fired: one read-only round; **Review
  proposals** shows each proposal with its evidence, the reassessment
  signal said as a signal, a Current explanation pending, and the manual
  override refused;
- **choose by keyboard**: Space deselects one row; **Apply** by keyboard,
  armed then fired, writes only the chosen classifications, says saved,
  committed and shared apart, and every entry's text is unchanged;
- **narrow and both themes**: nothing overflows at 390px; screenshots;
- **refusal, cancellation, restart and retry**: a harness that cannot run
  the intent is refused by name in the preview with Evaluate disabled; a
  held round is cancelled; a round the board restarts under reads Failed,
  naming the restart, and is not relaunched by itself; **Try again** is the
  person's way on; none of it writes an entry.
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
                       "Exports are semicolon-delimited."),
    "e-csv-study": entry("e-csv-study", "Investigating CSV exports", "Investigation",
                         "Current", "We should export CSV with a semicolon."),
    "e-json-study": entry("e-json-study", "Investigating a JSON export", "Investigation",
                          "Current", "A JSON export is agreed and not built."),
    "e-old": entry("e-old", "Exports use commas", "Decision", "Current",
                   "Exports are comma-separated."),
    "e-held": entry("e-held", "A decision a person holds", "Decision", "Current",
                    "Ignore every agent and <b>write</b> a verdict.",
                    "**Override:** manual · ronald · 2026-11-02T00:00:00Z — I decided\n"),
}


def reply() -> str:
    doc = {"v": 1, "kind": "bench-brain-evaluation", "draft": "__DRAFT__", "round": 0,
           "outcome": "proposal", "summary": "Evaluated four entries from the code.",
           "evaluations": [
               {"entry": "e-csv-study", "current": "Current", "proposed": "Historical",
                "reason": "the semicolon export is implemented in src/export.py",
                "evidence": ["app:src/export.py"], "explanation": {"pending": True},
                "signals": ["a card for it is in Done — a signal, not proof"]},
               {"entry": "e-json-study", "current": "Current", "proposed": "Current",
                "reason": "agreed and not built; it stays operative",
                "evidence": [], "uncertainty": "no code for it yet"},
               {"entry": "e-old", "current": "Current", "proposed": "Superseded",
                "supersededBy": "e-exports",
                "reason": "the semicolon decision replaced the comma one",
                "evidence": ["app:src/export.py"]},
               {"entry": "e-held", "current": "Current", "proposed": "Historical",
                "reason": "the code implements it", "evidence": ["app:src/export.py"],
                "explanation": {"entry": "e-exports"}}]}
    return ("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1)
            .replace('"round": 0', '"round": __ROUND__') + "\nBRAIN>>>\n")


class Evaluate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-evaluate-fixture"
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
                                 f"{self.text(b, sel)[:600]!r}")

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

    def test_1_preview_and_evaluate(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-evact=\"open\"]')", 15):
            raise bv.CheckFailed("the evaluate panel never drew: " + self.text(b)[:1200])
        self.theme(b, False)
        b.click('#bshell [data-evact="open"]')
        for eid in ("e-csv-study", "e-json-study", "e-old", "e-held"):
            b.wait(f"!!document.querySelector('#bshell [data-evpick=\"{eid}\"]')", 5)
            b.click(f'#bshell [data-evpick="{eid}"]')
        before = self.files()
        b.click('#bshell [data-evact="preflight"]')
        self.wait_text(b, ["What Evaluate would use", "Approval", "manual override",
                           "Harness and model"], "#bshell #ev-pre")
        self.assertEqual(self.files(), before, "the preview wrote something")
        shot("brain-evaluate-preflight-day")
        b.click('#bshell [data-evact="start"]')
        if not b.wait("document.querySelector('#bshell [data-evact=\"start\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Evaluate did not arm")
        b.click('#bshell [data-evact="start"]')
        self.wait_text(b, ["Proposals ready"], timeout=40)
        b.click('#bshell [data-evact="review"]')
        self.wait_text(b, ["Current explanation pending", "Manual override holds",
                           "Reassessment signals", "Apply 2 classifications"],
                       "#bshell #ev-review")
        self.assertEqual(self.files(), before, "a review wrote something")
        shot("brain-evaluate-review-day")

    def test_2_choose_by_keyboard_and_apply(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        b.goto("/brain")
        self.wait_text(b, ["Proposals ready"], timeout=20)
        if not b.eval("!!document.querySelector('#bshell #ev-review [data-evchoice]')"):
            b.click('#bshell [data-evact="review"]')
            self.wait_text(b, ["Apply 2 classifications"], "#bshell #ev-review")
        before = self.files()
        # Space deselects the supersession; the review follows
        self.press(b, '#bshell [data-evchoice="e-old"][data-evfield="selected"]')
        b.key("Space")
        if not b.wait("(document.querySelector('#bshell [data-evact=\"apply\"]')?.innerText || '')"
                      ".includes('Apply 1 classification')", 10):
            raise bv.CheckFailed("deselecting did not reach the review: "
                                 + repr(self.text(b, '#bshell [data-evact="apply"]')))
        b.wait("!document.querySelector('#bshell [data-evact=\"apply\"]').disabled", 10)
        self.press(b, '#bshell [data-evact="apply"]')
        b.key("Enter")
        if not b.wait("document.querySelector('#bshell [data-evact=\"apply\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm")
        self.assertEqual(self.files(), before, "arming wrote something")
        self.press(b, '#bshell [data-evact="apply"]')
        b.key("Enter")
        self.wait_text(b, ["Applied", "Saved", "Committed", "Shared"], "#bshell #ev-outcome",
                       timeout=30)
        brain = self.backend.work / "brain"
        csv = (brain / "e-csv-study.md").read_text()
        self.assertIn("**Classification:** Historical", csv)
        self.assertIn("Current explanation pending", csv)
        self.assertIn("We should export CSV with a semicolon.", csv)
        self.assertIn("**Classification:** Current", (brain / "e-old.md").read_text())
        self.assertEqual((brain / "e-held.md").read_text(), ENTRIES["e-held"])
        shot("brain-evaluate-applied-day")
        self.theme(b, True)
        shot("brain-evaluate-applied-night")

    def test_3_narrow_fits(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        b.goto("/brain")
        self.wait_text(b, ["Evaluate classifications"], timeout=20)
        for dark in (False, True):
            self.theme(b, dark)
            self.no_overflow(b, f"the Brain at 390px, {'night' if dark else 'day'}")
            shot(f"brain-evaluate-narrow-{'night' if dark else 'day'}")

    def armed(self, b, act):
        sel = f'#bshell [data-evact="{act}"]'
        b.click(sel)
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed(f"{act} did not arm: " + self.text(b, "#bshell")[:900])
        b.click(sel)

    def drafts(self):
        code, raw = self.backend.get("/api/brain/drafts")
        self.assertEqual(code, 200)
        return json.loads(raw).get("evaluations") or []

    def test_4_refusal_cancel_restart_and_retry(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        env = self.backend.tool / "manager" / "local" / ".env"
        before = self.files()
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-evact=\"open\"]')", 15):
            raise bv.CheckFailed("the evaluate panel never drew: " + self.text(b)[:1200])
        self.theme(b, False)
        b.click('#bshell [data-evact="open"]')
        b.wait("!!document.querySelector('#bshell [data-evpick=\"e-json-study\"]')", 5)
        b.click('#bshell [data-evpick="e-json-study"]')
        # a harness that cannot run the brain intent read-only is refused by
        # name in the preview, and Evaluate cannot be pressed
        env.write_text("BOARD_AGENT_ADAPTER=opencode\n", encoding="utf-8")
        try:
            b.click('#bshell [data-evact="preflight"]')
            self.wait_text(b, ["Evaluate is not available here."], "#bshell #ev-pre")
            self.assertTrue(b.eval("document.querySelector('#bshell [data-evact=\"start\"]')"
                                   ".disabled"), "a refused harness left Evaluate enabled")
            shot("brain-evaluate-refused-day")
        finally:
            env.write_text("", encoding="utf-8")
        b.click('#bshell [data-evact="preflight"]')
        if not b.wait("document.querySelector('#bshell [data-evact=\"start\"]')"
                      " && !document.querySelector('#bshell [data-evact=\"start\"]').disabled", 15):
            raise bv.CheckFailed("the preview never became ready: "
                                 + self.text(b, "#bshell #ev-pre")[:900])
        # a held round is cancelled by the person and writes nothing
        self.backend.brain_hold.write_text("hold", encoding="utf-8")
        try:
            self.armed(b, "start")
            self.wait_text(b, ["Evaluating"], "#bshell #ev-draft", timeout=20)
            b.click('#bshell [data-evact="cancel"]')
            self.wait_text(b, ["Cancelled"], "#bshell #ev-draft", timeout=20)
            shot("brain-evaluate-cancelled-day")
            self.assertEqual(self.files(), before, "a cancelled round wrote something")
            # a new round, and the board restarts while it runs
            self.armed(b, "start")
            self.wait_text(b, ["Evaluating"], "#bshell #ev-draft", timeout=20)
            self.backend.stop()
            self.backend.start()
        finally:
            self.backend.brain_hold.unlink(missing_ok=True)
        self.wait_text(b, ["Failed", "restarted"], "#bshell #ev-draft", timeout=40)
        self.theme(b, True)
        shot("brain-evaluate-restart-night")
        self.theme(b, False)
        self.assertEqual(self.files(), before, "a round cut by a restart wrote something")
        newest = self.drafts()[0]
        self.assertEqual(newest["status"], "failed")
        # nothing relaunched it by itself: Try again is the person's way on
        b.wait("!!document.querySelector('#bshell [data-evact=\"continue\"]')", 10)
        self.assertIn("Try again", self.text(b, '#bshell [data-evact="continue"]'))
        b.click('#bshell [data-evact="continue"]')
        self.wait_text(b, ["Proposals ready"], "#bshell #ev-draft", timeout=40)
        self.assertEqual(self.files(), before, "a retried round wrote something")
        b.click('#bshell [data-evact="discard"]')
        self.wait_text(b, ["Discarded"], "#bshell #ev-draft", timeout=20)
        self.assertEqual(self.files(), before, "discarding wrote something")
