"""The configuration check: one result model for the ⚕ sheet, Settings →
Diagnostics and the terminal (task 79).

    python3 -m unittest tests.test_configuration_check -v

What is held here:

- **A specific result and one scope sentence.** An inspected-only reading
  with no findings says *No configuration issues found* and *Configuration
  inspected. Agent execution has not been tested.* — and the scope
  sentence follows what was actually recorded, so a reading with
  execution results never claims execution was untested.
- **Each row names its purpose or setting and its evidence level.** A
  static pass reads `Configuration valid`; an execution pass reads
  `Execution passed`; `deferred` and `skipped` never read as a pass.
- **Each problem once.** One refusal reported from several angles leads
  the list once, and every row is still in `findings`.
- **Stages are independent.** Inspection cannot make execution read as
  tested, and a failed execution stage does not make the configuration
  read as invalid.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import doctor  # noqa: E402


def row(mode, name, outcome, *, intent=None, code=None, detail="d",
        example=False, evidence=None):
    found = doctor._finding(mode, name, outcome, "about", detail,
                            intent=intent, code=code, remedy="do the thing",
                            evidence=evidence,
                            extra={"example": True} if example else None)
    found["area"] = doctor.finding_area(found)
    found["state"] = doctor.finding_state(found)
    return found


class WithPolicy(unittest.TestCase):
    """A board whose policy document is whatever the test writes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-config-check-"))
        self.saved = config.AGENT_PROFILES

    def tearDown(self):
        config.AGENT_PROFILES = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def policy(self, document=None, raw=None):
        path = self.tmp / "profiles.json"
        path.write_text(raw if raw is not None else json.dumps(document),
                        encoding="utf-8")
        config.AGENT_PROFILES = str(path)


class TheResult(WithPolicy):
    def test_an_inspected_only_clean_reading_says_what_it_found_and_no_more(self):
        self.policy({"v": 1, "profiles": {"work": {}}})
        summary = doctor.report()["summary"]
        if summary["issues"]:
            self.skipTest("this checkout's own layout reports an issue")
        self.assertEqual(summary["headline"], "No configuration issues found")
        self.assertEqual(summary["scope"], "Configuration inspected. Agent "
                         "execution has not been tested.")
        for word in ("passed", "ready", "works", "verified"):
            self.assertNotIn(word, summary["headline"].lower())

    def test_every_stage_but_inspection_reads_not_tested(self):
        self.policy({"v": 1, "profiles": {"work": {}}})
        stages = {s["mode"]: s for s in doctor.report()["summary"]["stages"]}
        self.assertTrue(stages["inspect"]["asked"])
        for mode in ("conformance", "runtime", "real-agent"):
            self.assertFalse(stages[mode]["asked"], mode)
            self.assertEqual(stages[mode]["word"], "Not tested", mode)

    def test_the_scope_follows_recorded_execution_results(self):
        findings = [row("inspect", "roots", "passed"),
                    row("runtime", "check-passes", "passed")]
        summary = doctor._verdict(findings, [], ["inspect", "runtime"])
        self.assertIn("Controlled execution: passed.", summary["scope"])
        self.assertNotIn("Agent execution has not been tested", summary["scope"])
        self.assertIn("installed agent CLI has not been tested", summary["scope"])

    def test_a_failed_execution_is_not_a_configuration_issue(self):
        findings = [row("inspect", "roots", "passed"),
                    row("runtime", "check-fails", "failed")]
        summary = doctor._verdict(findings, [], ["inspect", "runtime"])
        self.assertTrue(summary["headline"].startswith(
            "No configuration issues found"))
        self.assertIn("1 execution diagnostic failed", summary["headline"])
        stages = {s["mode"]: s for s in summary["stages"]}
        self.assertEqual(stages["inspect"]["state"], "valid")
        self.assertEqual(stages["runtime"]["state"], "failed")
        self.assertIn("Controlled execution: failed (1).", summary["scope"])

    def test_an_unreadable_policy_is_a_partial_inspection(self):
        self.policy(raw="{ not json")
        report = doctor.report()
        summary = report["summary"]
        self.assertTrue(summary["partial"])
        self.assertIn("partially inspected", summary["scope"])
        self.assertNotEqual(summary["headline"], "No configuration issues found")
        issues = [f for f in report["findings"] if f["id"] in summary["issues"]]
        policy = [f for f in issues if f["name"] == "policy"]
        self.assertEqual(len(policy), 1)
        self.assertEqual(policy[0]["area"], "Execution permissions")
        self.assertEqual(policy[0]["state"], "invalid")
        self.assertTrue(policy[0]["remedy"])

    def test_a_partial_stage_is_not_a_pass(self):
        findings = [row("conformance", "describe", "passed", intent="work"),
                    row("conformance", "describe", "skipped", intent="review")]
        stages = {s["mode"]: s for s in doctor._verdict(
            findings, [], ["inspect", "conformance"])["stages"]}
        self.assertEqual(stages["conformance"]["word"], "Partial")


class EachRowSaysWhatItIsAbout(WithPolicy):
    def test_an_unsupported_requirement_names_the_purpose_and_blocks(self):
        self.policy({"v": 1, "profiles": {
            "review": {"requires": ["filesystem-sandbox"]}}})
        report = doctor.report()
        issues = [f for f in report["findings"]
                  if f["id"] in report["summary"]["issues"]]
        mine = [f for f in issues if f.get("purpose") == "review"]
        self.assertEqual(len(mine), 1, [f["id"] for f in issues])
        self.assertEqual(mine[0]["area"], "Reviews")
        self.assertEqual(mine[0]["state"], "unsupported")
        self.assertEqual(mine[0]["evidence"], "static")
        self.assertTrue(mine[0]["remedy"])
        # the guarantee's own row says the same thing and is not repeated
        self.assertFalse([f for f in issues if f["name"] == "pending:filesystem-sandbox"])
        self.assertFalse(report["summary"]["intents"]["review"])
        self.assertTrue(report["summary"]["intents"]["work"])

    def test_a_missing_required_input_blocks_implementation_only(self):
        self.policy({"v": 1, "profiles": {"work": {"context": [
            {"name": "brief", "path": "tool:no-such-file-here.md"}]}}})
        report = doctor.report()
        issues = [f for f in report["findings"]
                  if f["id"] in report["summary"]["issues"]]
        work = [f for f in issues if f.get("purpose") == "work"]
        self.assertTrue(work)
        self.assertEqual(work[0]["area"], "Implementation")
        self.assertIn(work[0]["state"], ("blocked", "invalid"))
        self.assertEqual(work[0]["code"], "context-unreadable")
        self.assertIn("1 configuration issue", report["summary"]["headline"])

    def test_a_broken_gate_block_is_one_merge_rules_issue(self):
        self.policy({"v": 1, "gates": {"acceptance": {"checks": ["nope"]},
                                       "member": {"checks": ["nope"]}}})
        report = doctor.report()
        issues = [f for f in report["findings"]
                  if f["id"] in report["summary"]["issues"]]
        gates = [f for f in issues if f["area"] == "Merge rules"]
        self.assertTrue(gates)
        self.assertEqual(len({f["detail"] for f in gates}), len(gates),
                         "the same sentence is listed twice")

    def test_fixtures_are_bench_testing_itself_and_belong_to_no_purpose(self):
        report = doctor.report()
        fixtures = [f for f in report["findings"] if f.get("example")]
        self.assertTrue(fixtures)
        for found in fixtures:
            self.assertEqual(found["area"], "Bench self-test")
            self.assertIsNone(found["purpose"])

    def test_every_row_carries_a_state_word_from_the_table(self):
        report = doctor.report()
        for found in report["findings"]:
            self.assertEqual(found["stateWord"],
                             doctor.STATE_WORDS[found["state"]])


class NothingUntestedReadsAsPassed(unittest.TestCase):
    def test_the_states(self):
        cases = [
            (row("inspect", "roots", "passed"), "valid"),
            (row("inspect", "launch", "failed", intent="work",
                 code="context-unreadable"), "blocked"),
            (row("inspect", "requirement-unsupported", "failed", intent="work",
                 code="requirement-unsupported"), "unsupported"),
            (row("inspect", "check-root-unsafe", "failed", intent="work",
                 code="check-root-unsafe"), "invalid"),
            (row("inspect", "pending:network-policy", "deferred"), "not-enforced"),
            (row("inspect", "verification", "skipped", intent="work"),
             "not-configured"),
            (row("real-agent", "work-probe", "deferred"), "not-tested"),
            (row("runtime", "check-fails", "failed"), "execution-failed"),
            (row("runtime", "check-passes", "passed"), "execution-passed"),
            (row("conformance", "describe", "passed", intent="work"),
             "conformance-passed"),
        ]
        for found, state in cases:
            self.assertEqual(found["state"], state, found["id"])
        for found, state in cases:
            if found["outcome"] in ("deferred", "skipped"):
                self.assertNotIn("passed", doctor.STATE_WORDS[state].lower())
                self.assertNotIn("valid", doctor.STATE_WORDS[state].lower())

    def test_purposes_use_the_agents_settings_names(self):
        self.assertEqual(doctor.PURPOSES, {
            "work": "Implementation", "act-pr": "PR fixes",
            "review": "Reviews", "refine": "Card refinement"})


class EachProblemOnce(unittest.TestCase):
    def test_a_launch_row_and_its_diagnostic_are_one_issue(self):
        findings = [
            row("inspect", "launch", "failed", intent="work",
                code="context-unreadable", detail="the brief is not there"),
            row("inspect", "context-unreadable", "failed", intent="work",
                code="context-unreadable", detail="the brief is not there"),
            row("inspect", "launch", "failed", intent="review",
                code="context-unreadable", detail="the brief is not there"),
        ]
        ids = doctor.issue_ids(findings)
        self.assertEqual(ids, [findings[0]["id"], findings[2]["id"]])

    def test_a_board_wide_echo_of_a_purposes_code_is_dropped(self):
        findings = [
            row("inspect", "launch", "failed", intent="work",
                code="requirement-unsupported", detail="a"),
            row("inspect", "pending:filesystem-sandbox", "failed",
                code="requirement-unsupported", detail="b"),
        ]
        self.assertEqual(doctor.issue_ids(findings), [findings[0]["id"]])

    def test_passes_and_deferrals_are_never_issues(self):
        findings = [row("inspect", "roots", "passed"),
                    row("real-agent", "work-probe", "deferred")]
        self.assertEqual(doctor.issue_ids(findings), [])


class TheTerminalLeadsWithTheSameResult(WithPolicy):
    def test_render_starts_with_the_headline_and_scope(self):
        self.policy({"v": 1, "profiles": {"work": {"requires": ["filesystem-sandbox"]}}})
        report = doctor.report()
        text = doctor.render(report, doctor.parse([]))
        lines = text.splitlines()
        self.assertEqual(lines[0], report["summary"]["headline"])
        self.assertEqual(lines[1], report["summary"]["scope"])
        self.assertIn("Implementation · Unsupported", text)


if __name__ == "__main__":
    unittest.main()
