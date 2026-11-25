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


def journey_reply() -> str:
    """The evaluation the stub vendor answers once the work has landed: the
    investigation is Historical, explained by the existing guide, and that
    same guide gains a section — no new page."""
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
                    "uncovered": ["the byte-order mark is not described"]}},
               {"entry": "e-exports", "current": "Current", "proposed": "Current",
                "reason": "the export module is where the code says it is",
                "evidence": ["app:src/export.py"]}]}
    return ("Working.\n<<<BRAIN\n" + json.dumps(doc, indent=1)
            .replace('"round": 0', '"round": __ROUND__') + "\nBRAIN>>>\n")


REVIEW = ("\n---\n\n## PR review at 0123456789ab — 2026-11-20 10:00 (Hazel)\n\n"
          "The semicolon export is implemented in src/export.py.\n\nPR REVIEW: APPROVE\n")


class Journey(Health):
    """The combined journey through the page (task 200 review): a baseline
    Brain written before reconciliation → an authorized disposable code
    change and its card's review, read in the Brain → reconciliation under
    the policy reassesses the investigation as Historical and updates the
    *same* explanation → the page shows the stable ids, the immutable review
    and the policy-attributed trail → a reversion reads as affected work.
    The stub vendor answers the evaluation; everything else is the board's
    own path."""

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-health-journey"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        cls.backend.env["BOARD_ORCHESTRATE_INTERVAL"] = "1"
        app, work = cls.backend.app, cls.backend.work
        (app / "src").mkdir(exist_ok=True)
        (app / "src" / "export.py").write_text('DELIMITER = ","\n')
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "app")
        (work / "brain").mkdir(exist_ok=True)
        for eid, text in ENTRIES.items():
            (work / "brain" / f"{eid}.md").write_text(text)
        (work / "tasks" / "done").mkdir(parents=True, exist_ok=True)
        (work / "tasks" / "done" / "12-export-as-csv.md").write_text(CARD)
        git(work, "add", "-A")
        git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "brain")
        cls.backend.brain_reply.write_text(journey_reply(), encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin

    # the inherited tests run on Health's own backend; this class runs only
    # the journey
    test_1_a_passive_read_says_what_is_not_complete = None
    test_2_repair_is_previewed_armed_and_queues = None
    test_3_narrow_fits = None

    def commit_app(self, text: str, message: str) -> None:
        app = self.backend.app
        (app / "src" / "export.py").write_text(text)
        git(app, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit",
            "-qam", message)

    def test_the_combined_journey_through_the_page(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 900)
        names = sorted(self.files())
        # 1. the baseline: entries written before reconciliation are never
        #    reconciled, so the Brain is not complete
        self.open_health(b)
        self.theme(b, False)
        self.wait_text(b, ["Investigating CSV exports", "Never reconciled against its inputs"])
        # 2. the authorized disposable change, and its card's review
        self.commit_app('DELIMITER = ";"\n', "semicolons")
        card = self.backend.work / "tasks" / "done" / "12-export-as-csv.md"
        card.write_text(card.read_text() + REVIEW)
        card_bytes = card.read_bytes()
        # 3. the review, read in the Brain at its own address
        code, raw = self.backend.get("/api/brain/reviews")
        self.assertEqual(code, 200, raw[:300])
        listed = json.loads(raw).get("reviews") or {}
        if isinstance(listed, dict):
            listed = listed.get("reviews") or []
        review = next(r for r in listed if str(r.get("card")) == "12")
        b.goto(review["address"])
        self.wait_text(b, ["The semicolon export is implemented"], timeout=20)
        shot("brain-journey-review-day")
        # 4. reconciliation on, with apply and the explanation update, on this
        #    installation
        code, raw = self.backend.get("/api/orchestration")
        orch = json.loads(raw)
        orch = orch.get("orchestration", orch)
        if orch["installation"].get("role") != "designated-here":
            code, raw = self.backend.post("/api/orchestration/installation", {
                "action": "designate", "expected": orch["installation"]["revision"]},
                headers=POLICY)
            self.assertEqual(code, 200, raw[:400])
        code, raw = self.backend.get("/api/brain/reconcile")
        rev = json.loads(raw)["reconcile"]["policy"]["revision"]
        code, raw = self.backend.post("/api/brain/reconcile/policy", {
            "expected": rev, "enabled": True, "evaluate": True, "apply": True,
            "maintain": True, "dailyLaunches": 8}, headers=POLICY)
        self.assertEqual(code, 200, raw[:400])
        rows = {}
        for _ in range(150):
            rows = {r["entry"]: r for r in json.loads(self.backend.get(
                "/api/brain/reconcile")[1])["reconcile"]["entries"]}
            if (rows.get("e-csv-study") or {}).get("stored") == "applied":
                break
            b.wait("false", 1)
        else:
            raise bv.CheckFailed(f"the policy never applied the evaluation: {rows}")
        # 5. stable ids, the same explanation updated, the immutable review,
        #    the policy-attributed trail — read in the page
        self.assertEqual(sorted(self.files()), names, "a new page was written")
        self.assertEqual(card.read_bytes(), card_bytes, "the review on the card changed")
        self.open_health(b)
        self.wait_text(b, ["Transition history", "(policy)", "explained now by e-exports"],
                       timeout=30)
        self.assertNotIn("Historical with no Current explanation", self.text(b))
        shot("brain-journey-health-day")
        self.theme(b, True)
        shot("brain-journey-health-night")
        self.theme(b, False)
        b.goto("/brain/e/e-exports")
        self.wait_text(b, ["How exports are written", "CSV exports",
                           "CSV files use a semicolon delimiter."], timeout=20)
        b.goto("/brain/e/e-csv-study")
        self.wait_text(b, ["Investigating CSV exports", "Historical"], timeout=20)
        shot("brain-journey-entry-day")
        brain = self.backend.work / "brain"
        exports = (brain / "e-exports.md").read_text()
        self.assertIn("_Maintained from evidence", exports)
        self.assertIn("**Classification:** Current", exports)
        csv = (brain / "e-csv-study.md").read_text()
        self.assertIn("**Id:** e-csv-study", csv)
        self.assertIn("**Classification:** Historical", csv)
        self.assertIn("· Current · ronald — written", csv, "the history was rewritten")
        # 6. a reversion of the code is affected work, said in the page
        self.commit_app('DELIMITER = ","\n', "revert")
        self.open_health(b)
        self.wait_text(b, ["Code changed since it was evaluated", "Repair"], timeout=30)
        b.viewport(390, 844)
        for dark in (False, True):
            self.theme(b, dark)
            wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                          " return [s.scrollWidth, s.clientWidth]; })()")
            self.assertLessEqual(wide[0], wide[1] + 1, f"the Brain overflows {wide}")
            shot(f"brain-journey-reverted-narrow-{'night' if dark else 'day'}")
        self.theme(b, False)
