"""Verifier reports, in a browser (task 135).

One disposable review card, its branch cut in the fixture's own
application repository, and one check `totals` that opts into the
verifier report contract. The case asks the disposable backend to run it
once (through the same `/api/verify` door a person's ↻ uses); the stub
verifier writes a failing report whose summary and finding carry markup,
control characters and a fake credential, plus a text artifact, and exits
1. Then the page is only read: the card's ◫ evidence sheet shows the
report under **Verifier reports** with bench's voice and the verifier's
labelled apart, the finding expands to its location and expected and
actual values, the markup is text and runs nothing, the credential is
masked, and the artifact link is served sandboxed. Settings → Checks shows
the contract on the check. Opening any of it starts no verification —
the evidence count is the same before and after. Run through
verify/browser.
"""

import json
import sys
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "135-report-fixture"
FILE = f"{STEM}.md"
FAKE = "ghp_01234567890abcdefghijABCDEFGHIJ"

VERIFIER = r'''#!@PYTHON@
import json, os, sys
out = os.environ["BENCH_VERIFY_REPORT_DIR"]
open(os.path.join(out, "diff.txt"), "w").write("-110.00\n+120.00\n")
json.dump({"v": 1, "outcome": "failed",
           "summary": "<img src=x onerror=window.__pwned=1> 1 total wrong token @FAKE@",
           "findings": [{"message": "<script>window.__pwned=2</script>case c1 total‮",
                         "location": {"path": "rules.json", "line": 3},
                         "expected": "110.00", "actual": "120.00",
                         "evidence": ["diff.txt"]}],
           "artifacts": [{"path": "diff.txt", "label": "the diff"}]},
          open(os.environ["BENCH_VERIFY_REPORT"], "w"))
print("verifier: 1 of 3 totals wrong")
sys.exit(1)
'''


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def evidence() -> list:
    status, body = S["backend"].get(f"/api/evidence?file={FILE}&stage=review")
    if status != 200:
        raise bv.CheckFailed(f"/api/evidence answered {status}: {body[:300]!r}")
    return json.loads(body).get("evidence") or []


def load(width=1280, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


class AReportReadsAsTwoVoices(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        script = backend.base / "report-verifier"
        script.write_text(VERIFIER.replace("@PYTHON@", sys.executable)
                          .replace("@FAKE@", FAKE), encoding="utf-8")
        script.chmod(0o755)
        cls.policy = backend.tool / "manager" / "local" / "profiles.json"
        cls.saved = cls.policy.read_bytes() if cls.policy.exists() else None
        cls.policy.write_text(json.dumps({
            "v": 1,
            "checks": [{"name": "totals", "run": str(script), "root": "source",
                        "report": "v1"}],
            "profiles": {"work": {"verify": ["totals"]},
                         "review": {"verify": ["totals"]}},
        }), encoding="utf-8")
        backend._git(backend.app, "checkout", "-q", "-b", f"task/{STEM}")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                     "fixture: the implementation commit")
        backend._git(backend.app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / FILE
        cls.card.write_text(
            "# Report fixture\n\n**Status:** Review\n**Priority:** Low\n\n"
            "A disposable card for the verifier report case.\n",
            encoding="utf-8")
        status, body = backend.post("/api/verify", {
            "check": "totals", "file": FILE, "stage": "review"}, timeout=120)
        if status != 200:
            raise bv.CheckFailed(f"/api/verify answered {status}: {body[:300]!r}")
        cls.record = json.loads(body)["evidence"]
        if cls.record.get("outcome") != "failed":
            raise bv.CheckFailed(f"the verifier did not fail as set up: "
                                 f"{cls.record!r}"[:400])

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        cls.card.unlink(missing_ok=True)
        try:
            backend._git(backend.app, "branch", "-D", f"task/{STEM}")
        except bv.SetupError:
            pass
        if cls.saved is None:
            cls.policy.unlink(missing_ok=True)
        else:
            cls.policy.write_bytes(cls.saved)

    def test_1_the_backend_recorded_the_rule_and_kept_the_report(self):
        rep = self.record.get("report") or {}
        self.assertEqual(("failed", "report-failed"),
                         (self.record["outcome"], self.record["reason"]))
        self.assertEqual("valid", rep.get("state"))
        self.assertEqual("verifier", rep.get("reportedBy"))
        self.assertNotIn(FAKE, json.dumps(self.record))
        kept = {k["path"]: k["id"] for k in rep.get("kept") or []}
        self.assertEqual({"diff.txt", "report.json"}, set(kept))
        S["metrics"]["verifierReport"] = {"reason": self.record["reason"],
                                          "kept": sorted(kept)}

    def test_2_the_evidence_sheet_shows_both_voices_and_runs_nothing(self):
        before = len(evidence())
        b = load()
        sel = f'#board article.card[data-file="{FILE}"]'
        if not b.wait(f"!!document.querySelector('{sel}')", 15):
            raise bv.CheckFailed(f"{FILE} never reached the board")
        opened = b.eval(f"""(() => {{
            const c = document.querySelector('{sel}');
            let v = c.querySelector('[data-verify]');
            if (!v) {{ const m = c.querySelector('[data-more]'); if (m) m.click();
                       v = c.querySelector('[data-verify]'); }}
            if (!v) return false; v.click(); return true; }})()""")
        if not opened:
            raise bv.CheckFailed("the card offers no ◫ evidence control")
        if not b.wait("!!document.querySelector('#sheetwrap .report-run')", 20):
            raise bv.CheckFailed("the evidence sheet shows no verifier report")
        b.eval("document.querySelector('#sheetwrap .report-run').open = true;"
               "document.querySelectorAll('#sheetwrap .report-finding')"
               ".forEach(d => d.open = true)")
        time.sleep(0.3)
        text = b.eval("document.querySelector('#sheetwrap .report-panel').innerText")
        shot("verifier-report-sheet-1280x900")
        self.assertIn("Bench observed", text)
        self.assertIn("Verifier reported", text)
        self.assertIn("exit status 1", text)
        self.assertIn("rules.json:3", text)
        self.assertIn("110.00", text)
        self.assertIn("120.00", text)
        self.assertIn("<script>", text, "markup is drawn as text")
        self.assertNotIn(FAKE, text)
        self.assertIn("«redacted»", text)
        self.assertIsNone(b.eval("window.__pwned || null"),
                          "nothing the verifier wrote ran")
        self.assertEqual(0, b.eval("document.querySelectorAll("
                                   "'#sheetwrap .report-panel script, "
                                   "#sheetwrap .report-panel img').length"))
        link = b.eval("(() => { const a = [...document.querySelectorAll("
                      "'#sheetwrap .report-panel a')].find(a => "
                      "a.textContent === 'diff.txt'); return a ? "
                      "a.getAttribute('href') : null; })()")
        self.assertTrue(link and "purpose=diagnostic" in link, link)
        status, body = S["backend"].get(link)
        self.assertEqual(200, status)
        self.assertEqual(b"-110.00\n+120.00\n", body)
        b.key("Escape")
        self.assertEqual(before, len(evidence()), "reading started no check")

    def test_3_the_sheet_at_390px(self):
        b = load(390, 844)
        sel = f'article.card[data-file="{FILE}"]'
        if not b.wait(f"!!document.querySelector('{sel}')", 15):
            raise bv.CheckFailed(f"{FILE} never reached the board at 390px")
        b.eval(f"""(() => {{ const c = document.querySelector('{sel}');
            let v = c.querySelector('[data-verify]');
            if (!v) {{ const m = c.querySelector('[data-more]'); if (m) m.click();
                       v = c.querySelector('[data-verify]'); }}
            if (v) v.click(); }})()""")
        if not b.wait("!!document.querySelector('#sheetwrap .report-run')", 20):
            raise bv.CheckFailed("no verifier report at 390px")
        b.eval("document.querySelector('#sheetwrap .report-run').open = true")
        wide = b.eval("document.querySelector('#sheetwrap .report-panel')"
                      ".scrollWidth - document.querySelector('#sheetwrap "
                      ".report-panel').clientWidth")
        shot("verifier-report-sheet-390x844")
        self.assertLessEqual(wide, 2, "the report panel overflows at 390px")
        b.key("Escape")

    def test_4_settings_shows_the_contract_and_writes_nothing(self):
        b = load(1440, 900)
        before = self.policy.read_bytes()
        b.click('#views button[data-view="settings"]')
        if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= 3", 15):
            raise bv.CheckFailed("the settings sections never arrived")
        item = '#snav [data-ssection="verification"]'
        if b.eval(f"document.querySelector('{item}').offsetParent === null"):
            b.click('#snavtoggle')
            b.wait(f"document.querySelector('{item}').offsetParent !== null", 5)
        b.click(item)
        if not b.wait("document.querySelector('#sbody').textContent"
                      ".includes('Verifier report')", 15):
            raise bv.CheckFailed("Settings → Checks does not show the contract")
        body = b.eval("document.querySelector('#sbody').textContent")
        shot("verifier-report-settings-1440x900")
        self.assertIn("contract v1", body)
        self.assertIn("Verifier reports — an opt-in contract", body)
        self.assertEqual(before, self.policy.read_bytes(),
                         "reading Settings wrote nothing")


if __name__ == "__main__":
    unittest.main()
