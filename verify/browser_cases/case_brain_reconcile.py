"""Automatic classification through the real page (task 197).

A disposable backend of this case's own, in the external split-Git layout,
with the stub vendor answering one evaluation round and a one-second beat.
The case drives the page's own controls:

- **off until turned on**: on the Brain's list, **Show automatic
  classification** (pointer) says *Not configured*, both authorities *not
  granted* and that this board does not execute; nothing is written and
  no policy file exists;
- **turn on, armed then fired** (pointer), with this installation
  designated: the policy file is written, the evaluate authority reads
  granted and apply does not; entries are queued (initial) with their
  place in the queue and the cards they cite; **Refresh sources for every
  entry** launches one bounded evaluation, and its proposal waits for a
  person's review — nothing is applied; **Review in Evaluate** opens it;
- **grant and revoke apply by keyboard**, armed then fired, each a
  policy write said in the panel;
- **narrow and both themes**: nothing overflows at 390px; screenshots.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

import benchverify as bv

S = bv.SESSION
POLICY = {"X-Bench-Settings": "1"}


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
                       "Exports are semicolon-delimited.", "**Source:** #12\n"),
    "e-csv-study": entry("e-csv-study", "Investigating CSV exports", "Investigation",
                         "Current", "We should export CSV with a semicolon.",
                         "**Source:** #12\n"),
}
CARD = "# 12 — Export as CSV\n\n**Status:** Done\n**Priority:** Medium\n\nSemicolons.\n"


def reply() -> str:
    doc = {"v": 1, "kind": "bench-brain-evaluation", "draft": "__DRAFT__", "round": 0,
           "outcome": "proposal", "summary": "Evaluated from the code.",
           "evaluations": [
               {"entry": "e-csv-study", "current": "Current", "proposed": "Historical",
                "reason": "the semicolon export is implemented in src/export.py",
                "evidence": ["app:src/export.py"], "explanation": {"entry": "e-exports"}},
               {"entry": "e-exports", "current": "Current", "proposed": "Current",
                "reason": "the export module is where the code says it is",
                "evidence": ["app:src/export.py"]}]}
    return ("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1)
            .replace('"round": 0', '"round": __ROUND__') + "\nBRAIN>>>\n")


class Reconcile(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-reconcile-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        # the fixture holds every beat off for an hour; this case needs the
        # reconciliation tick. No operating agreement is put in force, so the
        # scheduler's pass on the same beat stays off
        cls.backend.env["BOARD_ORCHESTRATE_INTERVAL"] = "1"
        app, work = cls.backend.app, cls.backend.work
        (app / "src").mkdir(exist_ok=True)
        (app / "src" / "export.py").write_text('DELIMITER = ";"\n')
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "brain").mkdir(exist_ok=True)
        for eid, text in ENTRIES.items():
            (work / "brain" / f"{eid}.md").write_text(text)
        (work / "tasks" / "done").mkdir(parents=True, exist_ok=True)
        (work / "tasks" / "done" / "12-export-as-csv.md").write_text(CARD)
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
                                 f"{self.text(b, sel)[-900:]!r}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def press(self, b, sel):
        b.eval(f"(() => {{ const e = document.querySelector({json.dumps(sel)});"
               " e.scrollIntoView({block: 'center'}); e.focus(); return true; })()")

    def files(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.backend.work / "brain").glob("*.md")}

    def policy_file(self):
        return self.backend.tool / "manager" / "local" / "brain-reconcile.json"

    def show(self, b):
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-rcact=\"show\"]')", 15):
            raise bv.CheckFailed("the reconciliation panel never drew: " + self.text(b)[:1200])
        b.click('#bshell [data-rcact="show"]')

    def test_1_off_until_turned_on(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        before = self.files()
        self.show(b)
        self.theme(b, False)
        self.wait_text(b, ["Not configured", "not granted", "Executes here"])
        self.assertFalse(self.policy_file().exists(), "reading wrote a policy")
        self.assertEqual(self.files(), before)
        shot("brain-reconcile-off-day")

    def test_2_turn_on_queue_and_review(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        code, raw = self.backend.get("/api/orchestration")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        orch = data.get("orchestration", data)
        code, raw = self.backend.post("/api/orchestration/installation", {
            "action": "designate", "expected": orch["installation"]["revision"]},
            headers=POLICY)
        self.assertEqual(code, 200, raw[:400])
        before = self.files()
        self.show(b)
        self.theme(b, False)
        self.wait_text(b, ["Turn on: evaluate only"])
        b.click('#bshell [data-rcact="policy"][data-rcarg="enable"]')
        if not b.wait("document.querySelector('#bshell [data-rcact=\"policy\"]')"
                      "?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Turn on did not arm")
        self.assertFalse(self.policy_file().exists(), "arming wrote the policy")
        b.click('#bshell [data-rcact="policy"][data-rcarg="enable"]')
        self.wait_text(b, ["On —", "Grant the apply authority"], timeout=20)
        doc = json.loads(self.policy_file().read_text())
        self.assertTrue(doc["evaluate"])
        self.assertFalse(doc["apply"])
        # the beat takes the execution lock and queues every entry once; the
        # open panel reads again on its own while reconciliation is on
        self.wait_text(b, ["Queued", "in the queue", "#12"], timeout=90)
        shot("brain-reconcile-queued-day")
        b.click('#bshell [data-rcact="refresh-all"]')
        self.wait_text(b, ["Waiting for your review", "Up to date"], timeout=60)
        self.assertEqual(self.files(), before, "the evaluate authority applied something")
        shot("brain-reconcile-review-day")
        self.theme(b, True)
        shot("brain-reconcile-review-night")
        self.theme(b, False)
        b.click('#bshell [data-rcact="open"]')
        self.wait_text(b, ["Proposals ready"], "#bshell #ev-draft", timeout=20)

    def test_3_grant_and_revoke_apply_by_keyboard(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        self.show(b)
        self.wait_text(b, ["Grant the apply authority"], timeout=20)
        sel = '#bshell [data-rcact="policy"][data-rcarg="grant-apply"]'
        self.press(b, sel)
        b.key("Enter")
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Grant did not arm")
        self.press(b, sel)
        b.key("Enter")
        self.wait_text(b, ["Revoke the apply authority"], timeout=20)
        self.assertTrue(json.loads(self.policy_file().read_text())["apply"])
        sel = '#bshell [data-rcact="policy"][data-rcarg="revoke-apply"]'
        self.press(b, sel)
        b.key("Enter")
        b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5)
        self.press(b, sel)
        b.key("Enter")
        self.wait_text(b, ["Grant the apply authority"], timeout=20)
        self.assertFalse(json.loads(self.policy_file().read_text())["apply"])

    def test_4_narrow_fits(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        self.show(b)
        self.wait_text(b, ["Automatic classification", "Evaluate authority"], timeout=20)
        for dark in (False, True):
            self.theme(b, dark)
            wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                          " return [s.scrollWidth, s.clientWidth,"
                          " document.documentElement.scrollWidth, innerWidth]; })()")
            self.assertLessEqual(wide[0], wide[1] + 1, f"the Brain overflows {wide}")
            self.assertLessEqual(wide[2], wide[3] + 1, f"the page overflows {wide}")
            shot(f"brain-reconcile-narrow-{'night' if dark else 'day'}")
