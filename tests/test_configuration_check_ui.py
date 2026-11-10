"""The configuration check and the launch check in the page (task 79).

    python3 -m unittest tests.test_configuration_check_ui -v

The page's renderer is lifted out of board.html and run in node against
reports the real doctor produced, so what is asserted is what a person
would read: a specific result and one scope sentence, problems first with
their purpose, evidence level and fix, nothing untested drawn as passed,
and paths one per row with a copy control. The rest are source-level
invariants for the parts that need a browser: which resolvers the launch
check asks, that its launch is the card's own action, and that opening
either sheet starts nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
HTML = (CORE / "board.html").read_text(encoding="utf-8")
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import doctor  # noqa: E402


def lift(name: str) -> str:
    """One top-level function's source, by brace matching."""
    marker = f"\nasync function {name}(" if f"\nasync function {name}(" in HTML \
        else f"\nfunction {name}("
    start = HTML.index(marker) + 1
    depth, i = 0, HTML.index("{", start)
    while True:
        ch = HTML[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:i + 1]
        i += 1


def const(name: str) -> str:
    """One top-level `const` statement, up to its terminating semicolon."""
    start = HTML.index(f"\nconst {name} =") + 1
    end = HTML.index(";\n", start)
    return HTML[start:end + 1]


RENDERER = "\n".join([
    const("esc"), const("DOCTOR_OUTCOME"), const("INTENT_LABEL"), const("CC_BAD"),
    lift("doctorOutcome"), lift("fmtShort"), lift("configStale"), lift("ccBadge"),
    lift("ccFor"), lift("ccIssue"), lift("ccShort"), lift("ccRows"),
    lift("lrow"), lift("copyButton"), lift("ccLocations"), lift("doctorDetail"),
    lift("ccWhatWasChecked"), lift("configCheckBody"), lift("readinessPanel"),
])


class Policy:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-cc-ui-"))
        self.saved = config.AGENT_PROFILES

    def write(self, document=None, raw=None):
        path = self.tmp / "profiles.json"
        path.write_text(raw if raw is not None else json.dumps(document),
                        encoding="utf-8")
        config.AGENT_PROFILES = str(path)

    def close(self):
        config.AGENT_PROFILES = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class TheRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — the renderer is unrun")

    def setUp(self):
        self.policy = Policy()

    def tearDown(self):
        self.policy.close()

    def draw(self, report, purpose=None, state_policy=None, fn="configCheckBody"):
        state = {"policy": state_policy or {"kind": report["policy"]["kind"],
                                            "revision": report["policy"]["revision"]}}
        script = (f"const S = {{ state: {json.dumps(state)} }};\n" + RENDERER +
                  f"\nconst r = {json.dumps(report)};\n"
                  + (f"process.stdout.write({fn}(r, {{ purpose: {json.dumps(purpose)} }}));"
                     if fn == "configCheckBody" else
                     f"process.stdout.write({fn}(r, {{ intent: {json.dumps(purpose)} }}));"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draw.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run([self.node, str(path)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    @staticmethod
    def lead(html):
        """Everything above the first fold — what a person reads first."""
        return html.split("<details", 1)[0]

    def test_a_clean_inspected_only_reading(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        report = doctor.report()
        if report["summary"]["issues"]:
            self.skipTest("this checkout's own layout reports an issue")
        html = self.draw(report)
        lead = self.lead(html)
        self.assertIn("No configuration issues found", lead)
        self.assertIn("Configuration inspected. Agent execution has not been "
                      "tested.", lead)
        # one scope statement, and no repeated introductory caveat
        self.assertEqual(lead.count("has not been tested"), 1)
        for caveat in ("a reading is not a proof", "starts no process",
                       "pins nothing", "spends nothing", "nothing failing"):
            self.assertNotIn(caveat, lead)
        # no overall execution-success signal
        self.assertNotIn("✓", lead)
        self.assertNotIn("Execution passed", lead)
        # successful detail is collapsed and still there
        self.assertRegex(html, r"<details class=\"fold cfold\"><summary>\d+ checks? with no issues")
        self.assertIn("Configuration valid", html)
        self.assertIn("What was checked?", html)

    def test_an_unsupported_requirement_names_its_purpose_level_and_fix(self):
        self.policy.write({"v": 1, "profiles": {
            "review": {"requires": ["filesystem-sandbox"]}}})
        html = self.draw(doctor.report())
        lead = self.lead(html)
        self.assertIn('class="cheadline bad"', lead)
        issue = lead[lead.index('class="cissue"'):]
        self.assertIn("<b>Reviews</b>", issue)
        self.assertIn(">Unsupported<", issue)
        self.assertIn(">inspected<", issue)
        self.assertIn("<b>Fix:</b>", issue)
        self.assertIn("requirement-unsupported", issue)

    def test_a_missing_required_input_is_blocked_for_implementation(self):
        self.policy.write({"v": 1, "profiles": {"work": {"context": [
            {"name": "brief", "path": "tool:no-such-file-here.md"}]}}})
        report = doctor.report()
        lead = self.lead(self.draw(report))
        self.assertIn("<b>Implementation</b>", lead)
        self.assertIn("context-unreadable", lead)
        # filtered to another purpose, the Implementation issue is not shown
        other = self.lead(self.draw(report, purpose="review"))
        self.assertNotIn("<b>Implementation</b>", other)
        self.assertIn("No issues for Review", other)

    def test_an_invalid_policy_is_a_partial_inspection_with_one_issue(self):
        self.policy.write(raw="{ nope")
        report = doctor.report()
        lead = self.lead(self.draw(report))
        self.assertIn("partially inspected", lead)
        # the document itself leads; the one consequence that is a
        # different setting (no gate can be evaluated) follows, once —
        # not one echo per purpose
        issues = re.findall(r'class="cissue".*?<b>([^<]+)</b>', lead)
        self.assertEqual(issues[0], "Execution permissions", issues)
        self.assertEqual(len(issues), len(set(issues)), issues)
        self.assertLessEqual(len(issues), 2, issues)
        self.assertIn(">Invalid<", lead)

    def test_a_reading_of_a_policy_since_changed_reads_stale(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        report = doctor.report()
        html = self.draw(report, state_policy={"kind": "file", "revision": "moved"})
        lead = self.lead(html)
        self.assertIn(">Stale<", lead)
        self.assertIn("this result is stale", lead)

    def test_an_execution_failure_is_its_own_result(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        report = doctor.report()
        failed = doctor._finding("runtime", "check-fails", "failed",
                                 "a check bench ran", "it exited 1",
                                 remedy="read the log")
        failed.update(area="Controlled execution", state="execution-failed",
                      stateWord="Execution failed", purpose=None)
        report["findings"].append(failed)
        report["ran"] = ["inspect", "runtime"]
        report["summary"] = doctor._verdict(report["findings"], report["intents"],
                                            ["inspect", "runtime"])
        lead = self.lead(self.draw(report))
        self.assertIn("1 execution diagnostic failed", lead)
        self.assertIn("<b>Controlled execution</b>", lead)
        self.assertIn(">Execution failed<", lead)
        self.assertIn("Controlled execution: failed (1).", lead)
        self.assertNotIn("Agent execution has not been tested", lead)

    def test_untested_and_deferred_rows_never_read_as_passed(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        html = self.draw(doctor.report())
        fold = html[html.index("not tested, not enforced or not configured"):]
        fold = fold[:fold.index("</details></div></details>") if
                    "</details></div></details>" in fold else len(fold)]
        for word in (">passed<", ">Configuration valid<", ">Execution passed<"):
            self.assertNotIn(word, fold)
        self.assertIn(">Not tested<", html)
        self.assertIn(">Not enforced<", html)

    def test_paths_are_one_per_row_with_a_labelled_copy(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        report = doctor.report()
        html = self.draw(report)
        for label in ("Application", "Task storage", "Worktrees"):
            row = re.search(r'<div class="lrow"><span class="k">' + label +
                            r'</span><span class="v">([^<]*)</span>'
                            r'<button class="chip2 ccopy" data-copy="([^"]*)" '
                            r'aria-label="Copy ' + label + r'">Copy</button></div>', html)
            self.assertIsNotNone(row, label)
            self.assertEqual(row.group(1), row.group(2))
        self.assertIn("<summary>Workspace locations</summary>", html)
        self.assertIn("<summary>Execution permissions</summary>", html)

    def test_the_focus_panel_has_no_overall_tick(self):
        self.policy.write({"v": 1, "profiles": {"work": {}}})
        html = self.draw(doctor.report(), purpose="work", fn="readinessPanel")
        self.assertIn("Configuration check", html)
        self.assertIn("Implementation", html)
        self.assertNotIn("✓", html)
        self.assertNotIn("<summary>Workspace locations</summary>", html)


class TheSheets(unittest.TestCase):
    def body(self, name):
        return lift(name)

    def test_the_global_sheet_is_named_configuration_check(self):
        for name in ("doctorSheet", "paintDoctorSheet"):
            self.assertIn("Configuration check", self.body(name))
        self.assertNotIn("nothing failing", HTML)
        self.assertNotIn("What would happen", HTML)
        self.assertIn("'All purposes'", self.body("paintDoctorSheet"))
        self.assertNotIn("'everything'", HTML)

    def test_both_entry_points_draw_the_same_result(self):
        self.assertIn("configCheckBody(report", self.body("paintDoctorSheet"))
        self.assertIn("configCheckBody(report", self.body("settingsDiagnostics"))
        self.assertIn("configCheckBody(report", self.body("readinessPanel"))

    def test_a_check_that_did_not_complete_says_so(self):
        body = self.body("doctorSheet")
        self.assertIn("did not complete", body)
        self.assertIn("did not complete", self.body("settingsDiagnostics"))

    def test_opening_or_expanding_runs_nothing(self):
        for name in ("doctorSheet", "paintDoctorSheet", "configCheckBody",
                     "profileSheet", "settingsDiagnostics"):
            body = self.body(name)
            self.assertNotIn("'/api/doctor/run'", body, name)
            self.assertNotIn("/api/agent/", body, name)
        # the only GETs the launch check makes are the two resolvers
        body = self.body("profileSheet")
        self.assertEqual(sorted(set(re.findall(r"'(/api/[a-z/-]+)\?", body))),
                         ["/api/plan", "/api/profile"])

    def test_execution_stages_keep_their_own_armed_controls(self):
        body = self.body("paintDoctorSheet")
        self.assertIn("data-doctormode", body)
        self.assertIn("m.sideEffects", body)
        self.assertIn("wireAction(btn, `${BOARD_ACT}doctor::${mode}`", body)

    def test_the_header_chip_never_wears_calm_for_a_static_reading(self):
        body = self.body("renderDoctor")
        self.assertNotIn("'calm'", body)
        self.assertIn("configStale(read)", body)


class TheLaunchCheck(unittest.TestCase):
    def test_purposes_use_the_agents_names_and_refine_is_global_only(self):
        self.assertIn("const LAUNCH_INTENTS = ['work', 'act-pr', 'review'];", HTML)
        labels = const("INTENT_LABEL")
        for word in ("'Implementation'", "'PR fixes'", "'Review'", "'Card refinement'"):
            self.assertIn(word, labels)
        self.assertNotIn("act on PR'", labels)

    def test_the_default_purpose_follows_the_cards_own_action(self):
        body = lift("profileIntentFor")
        self.assertIn("reviewIsIn(task)", body)
        self.assertIn("'review'", body)
        # the card's action slot asks the same question
        self.assertIn("const reviewIn = reviewIsIn(task);", HTML)

    def test_it_reads_the_launch_resolvers_and_shows_their_answers(self):
        body = lift("profileSheet")
        for field in ("pl.startRef", "pl.startBranch", "pl.branch", "pl.prBase",
                      "selectionRows(p)", "p.diagnostics", "pl.blockers",
                      "'Intended writes'", "'OS isolation'", "isolationLine(p)"):
            self.assertIn(field, body, field)

    def test_the_model_is_never_invented(self):
        body = lift("selectionRows")
        self.assertIn("m.value || 'harness default'", body)
        self.assertIn("bench passes no model", body)

    def test_the_launch_is_the_cards_own_action_and_revalidates(self):
        """Same key, same confirmation, same endpoint, and nothing the sheet
        resolved is sent: the server runs its own preflight again."""
        body = lift("profileSheet")
        self.assertIn("wireAction(launch, `${task.file}::${act.label}`", body)
        actions = lift("launchActionFor")
        for endpoint in ("'/api/agent/start'", "'/api/agent/act-pr'",
                         "'/api/agent/review-pr'", "'/api/agent/review'"):
            self.assertIn(endpoint, actions)
        self.assertNotIn("plan", re.sub(r"//.*", "", actions).lower().replace(
            "explain", ""))
        fire = lift("fireAgent")
        self.assertIn("JSON.stringify({ file: task.file, stage: task.stage, ...(extra || {}) })",
                      fire)

    def test_a_blocked_launch_offers_no_launch_button(self):
        body = lift("profileSheet")
        blocked = body[body.index("const next = stops.length"):]
        blocked = blocked[:blocked.index(": action.act")]
        self.assertNotIn("lc-launch", blocked)
        self.assertIn("Fix:", blocked)

    def test_launch_readiness_is_kept_apart_from_merge_and_evidence(self):
        body = lift("profileSheet")
        self.assertIn("Merge rules, Bench checks, CI and review", body)
        for other in ("gateSheet", "verifySheet", "/api/gate", "/api/evidence"):
            self.assertNotIn(other, body)

    def test_a_board_without_a_policy_document_still_has_the_entry(self):
        self.assertRegex(HTML, r"label: 'launch check', pre: '▸', cls: '', "
                               r"profile: true, face: false")


if __name__ == "__main__":
    unittest.main()
