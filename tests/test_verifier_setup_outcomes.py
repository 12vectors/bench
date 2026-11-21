"""Task 164 — bench's own verify/ wrappers speak the verifier report
contract (task 135), so a setup failure they detect is recorded as one.

In #158 the browser verifier printed `setup-error` after Playwright
timeouts and page crashes, and the card read `failed`: bench only saw a
non-zero exit. The contract was already there; the wrappers did not write
it. Now `benchverify.Run` writes a v1 report whenever bench hands it
`$BENCH_VERIFY_REPORT`, and bench's one rule (`verifierreport.combine`)
does the rest. Each case runs a real benchverify-based wrapper through
`verify.request` against a disposable install, and reads the evidence,
the gate row and the run's own diagnostics back.

    python3 -m unittest tests.test_verifier_setup_outcomes -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_verifier_report import ReportCase  # noqa: E402

import verifierreport  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "verify"))

import benchverify  # noqa: E402

# A wrapper built on the harness every verify/ entrypoint uses. The spec
# file says how this run ends.
WRAPPER = r'''#!@PYTHON@
import json, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, "@VERIFY@")
import benchverify as bv
spec = json.load(open("@SPEC@"))
open("@COUNT@", "a").write("ran\n")

def body(run):
    kind = spec["kind"]
    run.record["cases"] = {"results": spec.get("cases", [])}
    if kind == "setup":
        raise bv.SetupError("page crashed: Target closed while navigating")
    if kind == "fail":
        raise bv.CheckFailed("1 of 2 browser cases failed: test_layout")
    if kind == "sleep":
        time.sleep(spec.get("seconds", 30))
    return "2 browser cases held"

run = bv.Run("fixture", "a fixture wrapper", [])
sys.exit(run.main(body))
'''


class WrapperCase(ReportCase):
    def setUp(self):
        super().setUp()
        self.wrapper_spec = self.tmp / "wrapper.json"
        self.wrapper = self.bin / "wrapper"
        self.wrapper.write_text(
            WRAPPER.replace("@PYTHON@", sys.executable)
                   .replace("@VERIFY@", str(REPO / "verify"))
                   .replace("@SPEC@", str(self.wrapper_spec))
                   .replace("@COUNT@", str(self.count)), encoding="utf-8")
        self.wrapper.chmod(0o755)

    def ends(self, kind: str, **extra) -> None:
        self.wrapper_spec.write_text(json.dumps({"kind": kind, **extra}),
                                     encoding="utf-8")

    def document(self, *, report_v: str | None = "v1",
                 timeout: int | None = None) -> dict:
        document = self.contract(report_v=report_v, timeout=timeout)
        document["checks"][0]["run"] = str(self.wrapper)
        return document


class EachEndingReadsApart(WrapperCase):
    """Preparation failure, reported setup error, assertion failure,
    timeout, malformed and missing reports and a pass each land in their
    own words, in the evidence and in the gate row."""

    def test_a_reported_setup_error_is_preparation_failed_not_failed(self):
        self.ends("setup", cases=[{"case": "case_board.Board.test_open",
                                   "outcome": "setup-error",
                                   "detail": "TimeoutError: page.goto"}])
        found = self.go(self.document())
        self.assertEqual((found["outcome"], found["reason"]),
                         ("prepare-failed", "verifier-setup"))
        block = found["report"]
        self.assertEqual(block["outcome"], "setup-failed")
        self.assertEqual(block["reportedBy"], "verifier")
        self.assertIn("page crashed", block["summary"])
        self.assertEqual(len(block["findings"]), 1)
        self.assertIn("setup error", block["findings"][0]["message"])
        row = self.gate_row()
        self.assertEqual((row["state"], row["reason"]), ("unmet", "prepare-failed"))

    def test_an_assertion_failure_is_still_failed(self):
        self.ends("fail", cases=[{"case": "c.T.test_layout", "outcome": "failed",
                                  "detail": "AssertionError: 3 != 4"}])
        found = self.go(self.document())
        self.assertEqual((found["outcome"], found["reason"]),
                         ("failed", "report-failed"))
        self.assertIn("assertion failed",
                      found["report"]["findings"][0]["message"])

    def test_a_pass_is_a_pass_with_its_report(self):
        self.ends("pass")
        found = self.go(self.document())
        self.assertEqual((found["outcome"], found["reason"]),
                         ("passed", "report-passed"))

    def test_a_timeout_is_benchs_whatever_the_wrapper_would_say(self):
        self.ends("sleep", seconds=30)
        found = self.go(self.document(timeout=2))
        self.assertEqual((found["outcome"], found["reason"]), ("error", "timeout"))
        self.assertEqual(found["report"]["decided"], "bench")

    def test_without_the_opt_in_exit_semantics_are_unchanged(self):
        # no "report": exit 2 is an ordinary non-zero, as it always was
        self.ends("setup")
        found = self.go(self.document(report_v=None))
        self.assertEqual(found["outcome"], "failed")
        self.assertNotIn("report", found)

    def test_a_missing_or_malformed_report_fails_closed(self):
        # the stub verifier (task 135's) writes no report, or a broken one
        self.says(None, rc=0)
        document = self.contract()
        missing = self.go(document)
        self.assertEqual((missing["outcome"], missing["reason"]),
                         ("error", "report-missing"))
        self.says(None, raw="{not json", rc=1)
        broken = self.go(document, agent="run-2", again=True)
        self.assertEqual((broken["outcome"], broken["reason"]),
                         ("error", "report-invalid"))

    def test_a_required_preparation_step_that_fails_is_benchs(self):
        self.ends("pass")
        document = self.document()
        failing = self.bin / "prep-fails"
        failing.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(2)\n")
        failing.chmod(0o755)
        document["prepare"] = [{"name": "browser-start", "run": str(failing)}]
        document["checks"][0]["prepare"] = ["browser-start"]
        found = self.go(document)
        self.assertEqual(found["outcome"], "prepare-failed")
        self.assertNotEqual(found["reason"], "verifier-setup")
        self.assertEqual(found["report"]["decided"], "bench")


class OlderPassesDoNotHideANewSetupFailure(WrapperCase):
    def test_a_new_setup_failure_supersedes_and_stops_reuse(self):
        self.ends("pass")
        document = self.document()
        passed = self.go(document)
        self.assertEqual(passed["outcome"], "passed")
        self.assertEqual(self.gate_row()["state"], "met")
        self.ends("setup")
        newer = self.go(document, agent="run-2", again=True)
        self.assertEqual(newer["outcome"], "prepare-failed")
        row = self.gate_row()
        self.assertEqual((row["state"], row["evidence"]), ("unmet", newer["id"]))
        # a plain ask runs again: the old pass is not today's answer
        before = self.runs()
        self.ends("pass")
        third = self.go(document, agent="run-3")
        self.assertNotEqual(third.get("reused"), passed["id"])
        self.assertEqual(self.runs(), before + 1)


class TheAgentReadsWhatTheVerifierSaid(WrapperCase):
    def test_details_carry_the_setup_report_to_the_run(self):
        self.ends("setup")
        found = self.go(self.document())
        got = verify.diagnostic(found["id"], agent="run-1")
        self.assertEqual(got["report"]["outcome"], "setup-failed")
        printed = "\n".join(verify.diagnostic_text(got))
        self.assertIn("setup", printed)
        self.assertIn("page crashed", printed)
        # what bench observed is said apart from what the verifier said
        self.assertEqual(got["outcome"], "prepare-failed")


class TheReportItself(unittest.TestCase):
    """The pure half: what each ending writes, and that bench parses it."""

    def parse(self, status, reason="why", **record):
        out = benchverify.contract_report({"check": "browser", **record},
                                          status, reason)
        return verifierreport.parse(json.dumps(out).encode())

    def test_each_status_maps_to_one_contract_outcome(self):
        self.assertEqual(self.parse(benchverify.PASSED)["outcome"], "passed")
        self.assertEqual(self.parse(benchverify.FAILED)["outcome"], "failed")
        self.assertEqual(self.parse(benchverify.SETUP_ERROR)["outcome"],
                         "setup-failed")
        self.assertEqual(self.parse(benchverify.SETUP_ERROR,
                                    cancelled="SIGTERM")["outcome"], "incomplete")

    def test_long_and_many_findings_stay_inside_the_envelope(self):
        cases = [{"case": f"c{i}", "outcome": "failed", "detail": "x" * 9000}
                 for i in range(80)]
        parsed = self.parse(benchverify.FAILED, "y" * 5000,
                            cases={"results": cases})
        self.assertEqual(len(parsed["findings"]), benchverify.CONTRACT_FINDINGS)
        self.assertLessEqual(len(parsed["summary"]), 600)

    def test_nothing_is_written_unless_bench_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertIsNone(benchverify.write_contract_report(
                {}, benchverify.PASSED, None, env={}))
            # bench's "not declared" sentinel names no directory
            self.assertIsNone(benchverify.write_contract_report(
                {}, benchverify.PASSED, None,
                env={"BENCH_VERIFY_REPORT": "/nonexistent",
                     "BENCH_VERIFY_REPORT_DIR": "/nonexistent"}))
            # a report path that is not report.json in the named directory
            self.assertIsNone(benchverify.write_contract_report(
                {}, benchverify.PASSED, None,
                env={"BENCH_VERIFY_REPORT": str(folder / "elsewhere.json"),
                     "BENCH_VERIFY_REPORT_DIR": str(folder)}))
            target = folder / "report.json"
            os.symlink(folder / "other", target)
            self.assertIsNone(benchverify.write_contract_report(
                {}, benchverify.PASSED, None,
                env={"BENCH_VERIFY_REPORT": str(target),
                     "BENCH_VERIFY_REPORT_DIR": str(folder)}))
            self.assertFalse((folder / "other").exists())

    def test_the_wrapper_writes_one_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            written = benchverify.write_contract_report(
                {"check": "browser"}, benchverify.SETUP_ERROR, "crashed",
                env={"BENCH_VERIFY_REPORT": str(folder / "report.json"),
                     "BENCH_VERIFY_REPORT_DIR": str(folder)})
            self.assertEqual(written, str(folder / "report.json"))
            found = verifierreport.read(folder)
            self.assertEqual(found["state"], "valid")
            self.assertEqual(found["report"]["outcome"], "setup-failed")
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             ["report.json"])


class FakePage:
    """A page that answers the probe's questions from a script: which
    step breaks, and how. Records what was done to it."""

    def __init__(self, breaks: str | None = None, how: type = None):
        self.breaks, self.how = breaks, how
        self.done: list = []
        self.view = "board"

    def viewport(self, w, h):
        self.done.append(("viewport", w, h))

    def goto(self, path):
        self.done.append(("goto", path))
        if self.breaks == "navigation" and self.how:
            raise self.how("Target closed")

    def wait(self, expression, timeout=10.0):
        if ".drop[data-stage]" in expression:
            return self.breaks != "navigation"
        return self.breaks != "interaction" and f"#view-{self.view}" in expression

    def click(self, selector):
        self.done.append(("click", selector))
        if self.breaks == "interaction" and self.how:
            raise self.how(f"{selector}: a click at its centre would land on div")
        self.view = selector.split('"')[1]


class TheHealthProbe(unittest.TestCase):
    def test_a_pass_reaches_every_step_and_claims_no_feature(self):
        page = FakePage()
        found = benchverify.interaction_probe(page, timeout=0.1)
        self.assertEqual({k: found[k] for k in benchverify.PROBE_STEPS},
                         {"connection": "passed", "navigation": "passed",
                          "interaction": "passed"})
        self.assertIs(found["featureCoverage"], False)
        self.assertIn("not feature coverage", found["limits"])
        # actual interactions: a navigation and two clicks on the switcher
        self.assertIn(("goto", "/"), page.done)
        self.assertEqual([d for d in page.done if d[0] == "click"],
                         [("click", '#views [data-view="phases"]'),
                          ("click", '#views [data-view="board"]')])

    def test_a_page_that_never_draws_is_a_setup_error_at_navigation(self):
        for how in (None, benchverify.SetupError, RuntimeError):
            with self.subTest(how):
                with self.assertRaises(benchverify.SetupError) as caught:
                    benchverify.interaction_probe(FakePage("navigation", how),
                                                  timeout=0.1)
                coverage = caught.exception.coverage
                self.assertEqual(("passed", "failed", "not reached"),
                                 (coverage["connection"], coverage["navigation"],
                                  coverage["interaction"]))
                self.assertIn("navigation failed", str(caught.exception))

    def test_a_click_that_does_not_land_is_setup_not_an_assertion(self):
        for how in (None, benchverify.CheckFailed):
            with self.subTest(how):
                with self.assertRaises(benchverify.SetupError) as caught:
                    benchverify.interaction_probe(FakePage("interaction", how),
                                                  timeout=0.1)
                coverage = caught.exception.coverage
                self.assertEqual(("passed", "failed"),
                                 (coverage["navigation"], coverage["interaction"]))

    def test_the_probe_keeps_the_sandbox(self):
        text = (REPO / "verify" / "preflight").read_text(encoding="utf-8")
        probe = text[text.index("def probe("):text.index('if __name__')]
        # it launches through the same constructors the cases use, which
        # never weaken the sandbox, and names no flag that would
        self.assertIn("bv.browser_argv(exe, profile)", probe)
        self.assertIn("bv.RemoteBrowser(", probe)
        for flag in ("--no-sandbox", "chromiumSandbox", "--disable-setuid-sandbox"):
            self.assertNotIn(flag, probe)
        argv = benchverify.browser_argv("/usr/bin/chromium", Path("/tmp/p"))
        self.assertFalse(any("no-sandbox" in a for a in argv))

    def test_the_cli_probe_without_a_browser_is_a_setup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("BENCH_VERIFY") and k != "PW_WS"}
            env["BENCH_VERIFY_BROWSER"] = str(Path(tmp) / "no-such-chromium")
            r = subprocess.run(
                [sys.executable, str(REPO / "verify" / "preflight"), "browser",
                 "--probe", "--root", str(REPO), "--artifacts",
                 str(Path(tmp) / "art"), "--scratch", tmp],
                env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(benchverify.SETUP_ERROR, r.returncode, r.stdout + r.stderr)
            self.assertIn("SETUP-ERROR", r.stdout)
            record = next(Path(tmp, "art").rglob("result.json"))
            data = json.loads(record.read_text())
            self.assertEqual(data["probe"]["coverage"]["connection"], "not reached")
            self.assertIs(data["probe"]["coverage"]["featureCoverage"], False)

    def test_a_coverage_extension_rides_the_report(self):
        out = benchverify.contract_report(
            {"check": "preflight", "probe": {"coverage": {"connection": "passed"}}},
            benchverify.SETUP_ERROR, "probe failed")
        parsed = verifierreport.parse(json.dumps(out).encode())
        self.assertEqual(parsed["extensions"]["bench.verify"]["probe"],
                         {"connection": "passed"})


if __name__ == "__main__":
    unittest.main()
