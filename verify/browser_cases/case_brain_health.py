"""Brain maintenance health through the real page (task 200).

A disposable backend of this case's own, in the external split-Git layout,
with the stub vendor answering evaluation rounds. The case drives the
page's own controls:

- **a passive read** (pointer): **Read maintenance health** says the Brain
  is not complete — entries written before reconciliation read *never
  reconciled*, a Historical entry with no Current explanation is named,
  the handbook gap is shown as never required — and writes nothing;
- **Repair, previewed, armed then fired by keyboard**, once reconciliation
  is on with this installation designated: the preview names the entries,
  the authority and the budget; the click queues them and launches nothing
  in its own right; the queue's evaluation then runs and its proposal waits
  for a person — nothing is applied;
- **narrow and both themes**: nothing overflows at 390px; screenshots.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

import benchverify as bv

from case_brain_reconcile import CARD, ENTRIES, entry, reply

S = bv.SESSION
POLICY = {"X-Bench-Settings": "1"}

ORPHAN = entry("e-old-study", "A study that shipped long ago", "Investigation",
               "Historical", "We built it, and nobody wrote down how.")


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


class Health(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-health-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        cls.backend.env["BOARD_ORCHESTRATE_INTERVAL"] = "1"
        app, work = cls.backend.app, cls.backend.work
        (app / "src").mkdir(exist_ok=True)
        (app / "src" / "export.py").write_text('DELIMITER = ";"\n')
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "brain").mkdir(exist_ok=True)
        for eid, text in {**ENTRIES, "e-old-study": ORPHAN}.items():
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
                                 f"{self.text(b, sel)[-1200:]!r}")

    def theme(self, b, dark):
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")

    def press(self, b, sel):
        b.eval(f"(() => {{ const e = document.querySelector({json.dumps(sel)});"
               " e.scrollIntoView({block: 'center'}); e.focus(); return true; })()")

    def files(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.backend.work / "brain").glob("*.md")}

    def until_review(self, timeout=90):
        """The queue's rows once nothing is queued or running and a proposal
        waits for a person's review."""
        rows = []
        for _ in range(timeout):
            rows = json.loads(self.backend.get("/api/brain/reconcile")[1])[
                "reconcile"]["entries"]
            if rows and any(r["stored"] == "review" for r in rows) and not any(
                    r["stored"] in ("queued", "running") for r in rows):
                return rows
            S["browser"].wait("false", 1)
        raise bv.CheckFailed(f"no proposal waited for review: {rows}")

    def open_health(self, b):
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-rcact=\"health-show\"]')", 15):
            raise bv.CheckFailed("the health panel never drew: " + self.text(b)[:1200])
        b.click('#bshell [data-rcact="health-show"]')
        self.wait_text(b, ["Assessed scope", "Findings"], timeout=30)

    def test_1_a_passive_read_says_what_is_not_complete(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        before = self.files()
        self.open_health(b)
        self.theme(b, False)
        self.wait_text(b, ["never reconciled", "Never reconciled against its inputs",
                           "Historical with no Current explanation",
                           "never required", "Repair is not available"])
        body = self.text(b)
        self.assertNotIn("healthy", body.lower())
        self.assertEqual(self.files(), before, "reading health wrote an entry")
        policy = self.backend.tool / "manager" / "local" / "brain-reconcile.json"
        self.assertFalse(policy.exists(), "reading health wrote a policy")
        shot("brain-health-off-day")
        self.theme(b, True)
        shot("brain-health-off-night")
        self.theme(b, False)

    def test_2_repair_is_previewed_armed_and_queues(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        code, raw = self.backend.get("/api/orchestration")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        orch = data.get("orchestration", data)
        if orch["installation"].get("role") != "designated-here":
            code, raw = self.backend.post("/api/orchestration/installation", {
                "action": "designate", "expected": orch["installation"]["revision"]},
                headers=POLICY)
            self.assertEqual(code, 200, raw[:400])
        code, raw = self.backend.get("/api/brain/reconcile")
        rev = json.loads(raw)["reconcile"]["policy"]["revision"]
        code, raw = self.backend.post("/api/brain/reconcile/policy", {
            "expected": rev, "enabled": True, "evaluate": True, "apply": False,
            "dailyLaunches": 8}, headers=POLICY)
        self.assertEqual(code, 200, raw[:400])
        before = self.files()
        # the beat queues every entry once (initial) and evaluates them; its
        # proposals wait for a person
        rows = self.until_review()
        # then the code moves outside any card event: affected work to repair
        app = self.backend.app
        (app / "src" / "export.py").write_text('DELIMITER = ","\n')
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit",
            "-qam", "commas")
        self.open_health(b)
        self.theme(b, False)
        self.wait_text(b, ["Code changed since it was evaluated", "Authority now",
                           "evaluate granted", "launches left today"], timeout=30)
        sel = '#bshell [data-rcact="repair"]'
        if not b.wait(f"!!document.querySelector({json.dumps(sel)})", 20):
            raise bv.CheckFailed("Repair was not offered: " + self.text(b)[-1200:])
        shot("brain-health-repair-preview-day")
        self.press(b, sel)
        b.key("Enter")
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Repair did not arm")
        self.wait_text(b, ["Confirm: queue"])
        self.press(b, sel)
        b.key("Enter")
        self.wait_text(b, ["for evaluation; the queue launches them"], timeout=30)
        rows = self.until_review()
        self.assertIn("repair", {r["reason"] for r in rows}, rows)
        self.assertEqual(self.files(), before, "evaluate alone applied something")
        b.click('#bshell [data-rcact="health-read"]')
        self.wait_text(b, ["waiting for a person"], timeout=30)
        shot("brain-health-review-day")
        self.theme(b, True)
        shot("brain-health-review-night")
        self.theme(b, False)

    def test_3_narrow_fits(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(390, 844)
        self.open_health(b)
        for dark in (False, True):
            self.theme(b, dark)
            wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                          " return [s.scrollWidth, s.clientWidth,"
                          " document.documentElement.scrollWidth, innerWidth]; })()")
            self.assertLessEqual(wide[0], wide[1] + 1, f"the Brain overflows {wide}")
            self.assertLessEqual(wide[2], wide[3] + 1, f"the page overflows {wide}")
            shot(f"brain-health-narrow-{'night' if dark else 'day'}")
