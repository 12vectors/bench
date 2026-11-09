"""The readiness doctor: what this board would grant, run and refuse,
asked without paying for a model (task 70).

    python3 -m unittest discover -s tests -v

Five things here are load-bearing, and each is something that would make
the feature worse than its absence if it broke:

- **One evaluator.** Every diagnostic the doctor reports is a code
  `profiles.DIAGNOSTICS` names, read off a resolution `profiles.resolve()`
  made. A doctor that could disagree with a launch is a liar with a
  reassuring interface.
- **Cheap evidence cannot wear expensive clothes.** A static reading, a
  generated settings document and a model that actually ran are three
  different amounts of knowledge. `MODE_EVIDENCE` pins each mode to one
  level and `report()` refuses a finding that claims another.
- **Inspection is inspection.** Opening the inspector resolves, hashes
  and reads approved files. It starts no process, pins nothing, provisions
  no checkout and spends nothing.
- **Deferred is not passed.** The real-CLI row exists whether or not
  anybody authorised it, and reads `deferred` until they do.
- **Only what the doctor made.** A runtime diagnosis builds a git fixture
  under `local/state/doctor/`, runs real checks in it through the real
  runner, and removes exactly that.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import doctor  # noqa: E402
import profiles  # noqa: E402
import verify  # noqa: E402


def _ids(findings, mode=None):
    return [f["id"] for f in findings
            if mode is None or f["mode"] == mode]


def _by_name(findings, name, intent=None):
    for found in findings:
        if found["name"] == name and (intent is None or found["intent"] == intent):
            return found
    return None


# ── one evaluator, read from a distance ────────────────────────────────


class OneEvaluator(unittest.TestCase):
    """The doctor owns no policy logic. It calls the resolver and reports."""

    def test_the_doctor_is_the_seam_profiles_named_for_it(self):
        extend = profiles.seams()["extend"]["a doctor"]
        self.assertIn("doctor.py", extend)
        self.assertIn("resolve()", extend)
        self.assertIn("diagnostics", extend)

    def test_every_code_the_doctor_reports_is_one_profiles_names(self):
        report = doctor.report()
        known = set(profiles.DIAGNOSTICS)
        for found in report["findings"]:
            if found["code"] is not None:
                self.assertIn(found["code"], known,
                              f"{found['id']} reports a code profiles does "
                              f"not name")

    def test_the_report_carries_the_whole_diagnostic_table(self):
        report = doctor.report()
        self.assertEqual(set(report["diagnosticCodes"]),
                         set(profiles.DIAGNOSTICS))
        for code, entry in report["diagnosticCodes"].items():
            self.assertTrue(entry["remedy"], f"{code} has no remedy")
            self.assertTrue(entry["title"], f"{code} has no title")

    def test_the_doctor_has_no_parser_of_its_own(self):
        """The one thing a second evaluator would do: read the settings or
        the document itself. Naming `profiles` is the point; reading
        `BOARD_AGENT_COMMANDS` or splitting a command line is not."""
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        for forbidden in ("shlex.split", "json.loads(raw",
                          'setting("BOARD_AGENT_COMMANDS'):
            self.assertNotIn(forbidden, source,
                             f"doctor.py does its own {forbidden}")

    def test_the_intents_reading_is_the_profile_layers_own_allowlist(self):
        report = doctor.report()
        self.assertEqual([r["intent"] for r in report["intents"]],
                         list(profiles.INTENTS))
        for reading in report["intents"]:
            for key in reading:
                self.assertIn(key, set(profiles.PUBLIC) | {"summary"},
                              f"{key} is not in the profile layer's allowlist")


class EvidenceCannotBeOverclaimed(unittest.TestCase):
    def test_every_mode_is_pinned_to_one_level(self):
        self.assertEqual(set(doctor.MODE_EVIDENCE), set(doctor.MODES))
        for mode, level in doctor.MODE_EVIDENCE.items():
            self.assertIn(level, doctor.EVIDENCE, mode)

    def test_each_level_says_what_it_does_not_prove(self):
        for level, entry in doctor.EVIDENCE.items():
            self.assertTrue(entry["proves"], level)
            self.assertTrue(entry["doesNotProve"], level)
            self.assertTrue(entry["label"], level)

    def test_a_finding_may_not_claim_a_level_its_mode_does_not_reach(self):
        with self.assertRaises(ValueError):
            doctor._finding("inspect", "x", "passed", "about", "detail",
                            evidence="made-up")
        # and the report itself enforces the pairing
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        self.assertIn("claims {found['evidence']} ", source)

    def test_no_cheap_finding_wears_the_real_agent_level(self):
        report = doctor.report(["conformance"])
        for found in report["findings"]:
            if found["evidence"] == "real-agent":
                self.assertEqual(found["mode"], "real-agent", found["id"])

    def test_the_page_is_never_told_a_static_pass_is_a_cli_pass(self):
        report = doctor.report()
        self.assertEqual(report["summary"]["strongestEvidence"], "static")
        self.assertIn("No process ran", report["summary"]["line"])

    def test_the_words_are_four_and_each_is_defined(self):
        self.assertEqual(set(doctor.OUTCOMES),
                         {"passed", "failed", "skipped", "deferred"})
        for word, why in doctor.OUTCOMES.items():
            self.assertTrue(why, word)


# ── inspection is inspection ───────────────────────────────────────────


class InspectionStartsNothing(unittest.TestCase):
    def test_inspect_resolves_every_intent_and_names_the_roots(self):
        found = doctor.inspect()
        self.assertEqual([r["intent"] for r in found["intents"]],
                         list(profiles.INTENTS))
        roots = _by_name(found["findings"], "roots")
        self.assertIsNotNone(roots)
        self.assertIn(roots["outcome"], ("passed", "failed"))
        for key in ("app", "work", "worktrees", "tool"):
            self.assertIn(key, found["layout"])

    def test_inspect_pins_nothing_and_provisions_nothing(self):
        before = sorted(p.name for p in config.CONTEXT_DIR.glob("*")) \
            if config.CONTEXT_DIR.is_dir() else []
        reviews = sorted(p.name for p in config.REVIEWS_DIR.glob("*")) \
            if config.REVIEWS_DIR.is_dir() else []
        doctor.inspect()
        after = sorted(p.name for p in config.CONTEXT_DIR.glob("*")) \
            if config.CONTEXT_DIR.is_dir() else []
        reviews_after = sorted(p.name for p in config.REVIEWS_DIR.glob("*")) \
            if config.REVIEWS_DIR.is_dir() else []
        self.assertEqual(before, after, "inspection pinned a context bundle")
        self.assertEqual(reviews, reviews_after,
                         "inspection provisioned a review checkout")

    def test_inspect_writes_no_evidence(self):
        before = len(verify.all_evidence())
        doctor.inspect()
        self.assertEqual(before, len(verify.all_evidence()),
                         "inspection filed a verification record")

    def test_a_review_reading_asks_with_no_pinned_checkout(self):
        """A PR review's checkout is the launch's side effect. The
        inspector must read the policy without making one."""
        found = doctor.inspect()
        review = next(r for r in found["intents"] if r["intent"] == "review")
        self.assertNotIn("review", review["roots"])

    def test_every_mode_but_inspect_declares_its_side_effects(self):
        self.assertEqual(doctor.MODES["inspect"]["sideEffects"], [])
        self.assertFalse(doctor.MODES["inspect"]["needsApproval"])
        for mode in ("conformance", "runtime", "real-agent"):
            self.assertTrue(doctor.MODES[mode]["sideEffects"], mode)
        self.assertTrue(doctor.MODES["real-agent"]["needsApproval"])


class CoverageIsHonestAboutWhatWasNotAsked(unittest.TestCase):
    def test_a_mode_nobody_asked_for_is_a_row_not_an_absence(self):
        report = doctor.report()
        rows = {row["mode"]: row for row in report["coverage"]}
        self.assertEqual(set(rows), set(doctor.MODES))
        self.assertTrue(rows["inspect"]["asked"])
        for mode in ("conformance", "runtime", "real-agent"):
            self.assertFalse(rows[mode]["asked"], mode)
            self.assertIn("not asked", rows[mode]["why"])

    def test_coverage_counts_every_outcome_word(self):
        report = doctor.report()
        for row in report["coverage"]:
            self.assertEqual(set(row["counts"]), set(doctor.OUTCOMES))
            self.assertEqual(sum(row["counts"].values()), row["checks"])

    def test_an_unknown_mode_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            doctor.report(["make-it-green"])
        self.assertIn("make-it-green", str(caught.exception))


# ── the five diagnostics, from the real resolver ───────────────────────


class DeliberateFaultsProduceDistinctDiagnostics(unittest.TestCase):
    """The headline acceptance: a missing input, a review that cannot
    verify, a check in the live checkout, an absent isolation backend and
    a preparation that cannot be read are five different diagnostics —
    produced by `profiles.resolve()` and not by anything here."""

    @classmethod
    def setUpClass(cls):
        cls.findings = doctor.fixture_findings()

    def test_each_fixture_produces_the_code_it_claims(self):
        for found in self.findings:
            self.assertEqual(found["outcome"], "passed",
                             f"{found['id']}: {found['detail']}")

    def test_the_five_the_card_names_are_all_there_and_all_distinct(self):
        codes = [f["code"] for f in self.findings]
        for code in ("context-unreadable", "check-undeclared",
                     "check-root-unsafe", "requirement-unsupported",
                     "prepare-undeclared"):
            self.assertIn(code, codes)
        self.assertEqual(len(codes), len(set(codes)),
                         "two fixtures prove the same diagnostic")

    def test_every_fixture_finding_carries_a_remedy(self):
        for found in self.findings:
            self.assertTrue(found["remedy"], found["id"])

    def test_the_fixtures_are_benchs_own_constants(self):
        """Nothing an operator or a model writes is resolved here."""
        for fixture in doctor.FIXTURES:
            self.assertIn("code", fixture)
            self.assertIn(fixture["intent"], profiles.INTENTS)
            self.assertTrue("document" in fixture or "raw" in fixture)

    def test_a_missing_required_input_blocks_the_intent_it_is_declared_for(self):
        """And blocks only that intent: readiness is per launch, not per
        board."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "v": 1,
                "profiles": {
                    "work": {"context": [{"name": "brief",
                                          "path": "tool:not-here-at-all.md"}]},
                    "review": {},
                },
            }), encoding="utf-8")
            policy = profiles.document_at(path)
            work = profiles.resolve("work", worktree=Path(tmp) / "wt",
                                    policy=policy)
            review = profiles.resolve("review", policy=policy)
            self.assertEqual([d["code"] for d in work["diagnostics"]],
                             ["context-unreadable"])
            self.assertEqual(review["diagnostics"], [])

    def test_a_check_in_the_live_checkout_is_its_own_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "v": 1,
                "checks": [{"name": "unit", "run": "python3 -m unittest",
                            "root": "app"}],
                "profiles": {"work": {"verify": ["unit"]}},
            }), encoding="utf-8")
            found = profiles.resolve("work", worktree=Path(tmp) / "wt",
                                     policy=profiles.document_at(path))
            codes = [d["code"] for d in found["diagnostics"]]
            self.assertIn("check-root-unsafe", codes)
            self.assertIn("live code", found["blockers"][0])

    def test_a_required_isolation_guarantee_nothing_enforces_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "v": 1,
                "profiles": {"work": {"requires": ["filesystem-sandbox"]}},
            }), encoding="utf-8")
            found = profiles.resolve("work", worktree=Path(tmp) / "wt",
                                     policy=profiles.document_at(path))
            self.assertEqual([d["code"] for d in found["diagnostics"]],
                             ["requirement-unsupported"])

    def test_a_pending_guarantee_is_deferred_rather_than_left_out(self):
        findings = doctor.inspect()["findings"]
        for name in profiles.PENDING_CAPABILITIES:
            row = _by_name(findings, f"pending:{name}")
            self.assertIsNotNone(row, name)
            self.assertEqual(row["outcome"], "deferred", name)

    def test_a_board_whose_review_cannot_verify_says_so_per_intent(self):
        findings = doctor.inspect()["findings"]
        row = _by_name(findings, "verification", intent="review")
        self.assertIsNotNone(row)
        self.assertIn(row["outcome"], ("passed", "failed", "skipped"))
        if row["outcome"] != "passed":
            self.assertTrue(row["remedy"])


# ── adapter conformance ────────────────────────────────────────────────


class AdapterConformance(unittest.TestCase):
    """What the installed adapter generates, read through the interface it
    declares — so core still knows nothing about any vendor."""

    @classmethod
    def setUpClass(cls):
        cls.findings = doctor.conformance()

    def test_the_installed_adapter_is_exercised_per_intent(self):
        intents = {f["intent"] for f in self.findings}
        self.assertEqual(intents, set(profiles.INTENTS))

    def test_grants_arguments_cwd_and_lifecycle_are_each_checked(self):
        for name in ("grants", "arguments", "cwd", "lifecycle", "context",
                     "verification"):
            self.assertIsNotNone(_by_name(self.findings, name),
                                 f"no conformance row for {name}")

    def test_nothing_is_granted_the_lifecycle_bench_owns(self):
        for found in self.findings:
            if found["name"] == "lifecycle":
                self.assertEqual(found["outcome"], "passed", found["detail"])

    def test_the_read_only_intent_is_granted_no_way_to_write(self):
        for name in ("publication", "read-only"):
            row = _by_name(self.findings, name, intent="review")
            self.assertIsNotNone(row, name)
            self.assertEqual(row["outcome"], "passed", row["detail"])

    def test_the_cwd_is_the_one_bench_named(self):
        for found in self.findings:
            if found["name"] == "cwd":
                self.assertEqual(found["outcome"], "passed", found["detail"])

    def test_adjacent_reads_are_deferred_and_never_passed(self):
        rows = [f for f in self.findings if f["name"] == "adjacent-reads"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["outcome"], "deferred")
            self.assertIn("filesystem-sandbox", row["detail"])

    def test_the_adapters_own_run_is_exercised_against_a_stub(self):
        rows = [f for f in self.findings if f["name"] == "invocation"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["evidence"], "stub", row["id"])
            self.assertEqual(row["outcome"], "passed", row["detail"])

    def test_a_stub_row_says_what_a_stub_does_not_prove(self):
        self.assertIn("A stub answers; it does not enforce",
                      doctor.EVIDENCE["stub"]["doesNotProve"])

    def test_both_shipped_adapters_declare_the_conformance_seam(self):
        for name in ("claude", "opencode"):
            path = CORE / "adapters" / name / "capabilities.json"
            seam = json.loads(path.read_text(encoding="utf-8"))["conformance"]
            self.assertIsInstance(seam["describe"], list)
            self.assertTrue(seam["stubEnv"])
            argv, why = doctor._describe_argv(seam)
            self.assertIsNotNone(argv, why)

    def test_an_adapter_that_declares_nothing_is_skipped_not_accused(self):
        argv, why = doctor._describe_argv({})
        self.assertIsNone(argv)
        self.assertIn("declares no conformance.describe", why)

    def test_a_describe_command_climbing_out_is_refused(self):
        argv, why = doctor._describe_argv(
            {"describe": ["python3", "../../elsewhere.py"]})
        self.assertIsNone(argv)
        self.assertIn("climbs out", why)


class DescribeMatchesWhatRunActuallyPasses(unittest.TestCase):
    """The one drift this design can suffer: `describe` is written in
    python and `run` in bash, so the two could diverge. They are held
    together here, against the real adapter and a stub vendor binary."""

    def _stub_argv(self, adapter: str, stub_env: str, intent: str,
                   profile: dict, cwd: Path) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "argv.json"
            stub = Path(tmp) / "stub.py"
            stub.write_text(
                "import json, sys\n"
                f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8")
            wrapper = Path(tmp) / "bin"
            wrapper.write_text(
                f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n",
                encoding="utf-8")
            wrapper.chmod(0o755)
            env = dict(os.environ)
            env.pop("AGENT_MODEL", None)
            env.update({stub_env: str(wrapper), "AGENT_MODE": intent,
                        "AGENT_PROMPT": "do the task",
                        "AGENT_COMMANDS": "python3 -m unittest",
                        "AGENT_PROFILE": json.dumps(profile),
                        "AGENT_CWD": str(cwd)})
            result = subprocess.run(
                ["bash", str(CORE / "adapters" / adapter / "run")], env=env,
                cwd=cwd, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(capture.read_text(encoding="utf-8"))

    def _described(self, adapter: str, intent: str, profile: dict,
                   cwd: Path) -> dict:
        env = dict(os.environ)
        env.pop("AGENT_MODEL", None)
        env.update({"AGENT_MODE": intent, "AGENT_PROMPT": "do the task",
                    "AGENT_COMMANDS": "python3 -m unittest",
                    "AGENT_PROFILE": json.dumps(profile),
                    "AGENT_CWD": str(cwd)})
        directory = CORE / "adapters" / adapter
        seam = json.loads(
            (directory / "capabilities.json").read_text(encoding="utf-8"))
        argv, _ = doctor._describe_argv(seam["conformance"])
        result = subprocess.run(argv, env=env, cwd=directory,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_claude_describe_is_the_argv_run_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            for intent in profiles.INTENTS:
                found = profiles.resolve(intent, worktree=cwd)
                policy = profiles.adapter_policy(found)
                described = self._described("claude", intent, policy, cwd)
                passed = self._stub_argv("claude", "BOARD_CLAUDE_BIN", intent,
                                         policy, cwd)
                self.assertEqual(described["argv"], passed,
                                 f"claude describe and run disagree for "
                                 f"{intent}")

    def test_opencode_describe_is_the_argv_run_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            for intent in profiles.INTENTS:
                found = profiles.resolve(intent, worktree=cwd)
                policy = profiles.adapter_policy(found)
                described = self._described("opencode", intent, policy, cwd)
                passed = self._stub_argv("opencode", "BOARD_OPENCODE_BIN",
                                         intent, policy, cwd)
                self.assertEqual(described["argv"], passed,
                                 f"opencode describe and run disagree for "
                                 f"{intent}")

    def test_a_grants_whole_command_line_reaches_the_rule(self):
        """The quoting half: a grant with fixed arguments must become a
        rule over the whole line, not over the executable."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "v": 1,
                "profiles": {"work": {"commands": [
                    {"name": "unit", "run": "python3 -m unittest discover -s tests",
                     "root": "worktree"}]}},
            }), encoding="utf-8")
            found = profiles.resolve("work", worktree=Path(tmp),
                                     policy=profiles.document_at(path))
            self.assertFalse(found["blockers"], found["blockers"])
            policy = profiles.adapter_policy(found)
            for adapter in ("claude", "opencode"):
                described = self._described(adapter, "work", policy, Path(tmp))
                rules = " ".join(described["allow"])
                self.assertIn("python3 -m unittest discover -s tests", rules,
                              f"{adapter} lost a grant's arguments")

    def test_a_review_may_ask_bench_for_a_check_and_still_not_commit(self):
        """The separation `publication` exists for, in both adapters' own
        rule languages."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "v": 1,
                "checks": [{"name": "unit", "run": "python3 -m unittest",
                            "root": "source"}],
                "profiles": {"review": {"verify": ["unit"]}},
            }), encoding="utf-8")
            found = profiles.resolve("review",
                                     policy=profiles.document_at(path))
            self.assertFalse(found["blockers"], found["blockers"])
            policy = profiles.adapter_policy(found)
            for adapter in ("claude", "opencode"):
                described = self._described(adapter, "review", policy,
                                            Path(tmp))
                rules = " ".join(described["allow"])
                self.assertIn(profiles.VERIFY_GRANT, rules,
                              f"{adapter} does not let a review ask")
                for forbidden in ("git commit", "git push", "git add"):
                    self.assertNotIn(forbidden, rules,
                                     f"{adapter} let a review {forbidden}")
                self.assertTrue(described["readOnly"])

    def test_a_describe_never_carries_an_approved_inputs_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            found = profiles.resolve("work", worktree=cwd)
            for adapter in ("claude", "opencode"):
                described = self._described(adapter, "work",
                                            profiles.adapter_policy(found), cwd)
                self.assertNotIn("contextContents", described)
                for item in described["context"]:
                    self.assertNotIn("content", item)
                    self.assertNotIn("contents", item)


# ── controlled execution, at a fixture the doctor owns ─────────────────


class ControlledExecution(unittest.TestCase):
    """The real runner, at a fixture. Three outcomes, because "it failed"
    is three different things to do next."""

    @classmethod
    def setUpClass(cls):
        cls.before = {p.name for p in config.DOCTOR_DIR.glob("*")} \
            if config.DOCTOR_DIR.is_dir() else set()
        cls.findings = doctor.runtime()

    def test_a_pass_a_test_failure_and_a_dependency_failure_are_three_things(self):
        for name, wanted, _ in doctor.RUNTIME_CHECKS:
            row = _by_name(self.findings, name)
            self.assertIsNotNone(row, name)
            self.assertEqual(row["outcome"], "passed", row["detail"])
            self.assertEqual(row["recorded"], wanted, row["detail"])

    def test_the_runner_that_ran_them_is_benchs_own(self):
        for row in self.findings:
            self.assertEqual(row["evidence"], "runtime")
            self.assertTrue(row.get("evidenceId"), row["id"])
            record = verify.read(row["evidenceId"])
            self.assertIsNotNone(record, row["id"])
            self.assertEqual(record["runner"]["module"], "verify")
            self.assertEqual(record["actor"], "doctor")

    def test_the_evidence_is_filed_under_the_doctor_and_never_a_card(self):
        for row in self.findings:
            record = verify.read(row["evidenceId"])
            self.assertEqual(record["task"], verify.FIXTURE_TASK)
            self.assertTrue(record.get("doctor"))
            self.assertIn("doctor", verify.public(record))
        # The name a diagnosis files under is not a card's, so no card's
        # own reading can pick one of these up as a check of its work.
        self.assertFalse(verify.FIXTURE_TASK[0].isdigit())
        self.assertIsNone(
            __import__("taskfiles").find_stage_of(verify.FIXTURE_TASK))

    def test_the_policy_was_resolved_by_the_same_resolver_a_launch_rides(self):
        for row in self.findings:
            record = verify.read(row["evidenceId"])
            self.assertTrue(record["policyId"].startswith("profile:work@"))
            self.assertEqual(record["intent"], "work")

    def test_the_fixture_is_removed_and_nothing_else_is(self):
        after = {p.name for p in config.DOCTOR_DIR.glob("*")} \
            if config.DOCTOR_DIR.is_dir() else set()
        self.assertEqual(after - self.before, set(),
                         "the diagnosis left its fixture behind")

    def test_the_application_is_never_the_execution_root(self):
        for row in self.findings:
            record = verify.read(row["evidenceId"])
            path = Path(record["root"]["path"]).resolve()
            self.assertNotEqual(path, Path(config.app_repo()).resolve())
            self.assertIn(config.DOCTOR_DIR.resolve(), path.parents)
            self.assertFalse(record["root"]["owned"],
                             "the runner thinks it owns the doctor's fixture")

    def test_the_check_environment_still_strips_publication_credentials(self):
        record = verify.read(_by_name(self.findings, "doctor-pass")["evidenceId"])
        self.assertTrue(record["env"]["removed"])
        for name in record["env"]["removed"]:
            self.assertIsInstance(name, str)
        self.assertNotIn("=", "".join(record["env"]["removed"]),
                         "an environment value reached the record")


class NothingElseIsTouched(unittest.TestCase):
    """What a diagnosis leaves exactly as it found it. Each of these is
    something a readiness tool could plausibly disturb, and must not."""

    @classmethod
    def setUpClass(cls):
        cls.worktrees = subprocess.run(
            ["git", "-C", str(config.app_repo()), "worktree", "list",
             "--porcelain"], capture_output=True, text=True, timeout=60).stdout
        cls.report = doctor.report(["conformance", "runtime"])
        cls.after = subprocess.run(
            ["git", "-C", str(config.app_repo()), "worktree", "list",
             "--porcelain"], capture_output=True, text=True, timeout=60).stdout

    def test_no_worktree_is_added_or_removed(self):
        self.assertEqual(self.worktrees, self.after,
                         "a diagnosis changed the application's worktrees")

    def test_the_application_checkout_is_never_an_execution_root(self):
        app = Path(config.app_repo()).resolve()
        for found in self.report["findings"]:
            detail = found["detail"]
            self.assertNotIn(f"root {app} ", detail)
        # and the policy layer refuses `app` as a check root at all
        self.assertNotIn("app", profiles.CHECK_ROOTS)

    def test_no_card_is_moved_and_no_task_file_is_written(self):
        stages = {s["slug"]: [t["file"] for t in s["tasks"]]
                  for s in __import__("taskfiles").collect()["stages"]}
        again = {s["slug"]: [t["file"] for t in s["tasks"]]
                 for s in __import__("taskfiles").collect()["stages"]}
        self.assertEqual(stages, again)

    def test_the_configured_adapter_port_and_commands_are_unchanged(self):
        """A diagnosis reads settings; it never writes one."""
        before = (config.ADAPTER, config.PORT, config.agent_commands(),
                  config.AGENT_PROFILES)
        doctor.inspect()
        self.assertEqual(before, (config.ADAPTER, config.PORT,
                                  config.agent_commands(),
                                  config.AGENT_PROFILES))

    def test_a_diagnosis_never_writes_to_the_profile_document(self):
        path = profiles.document_path()
        before = path.read_bytes() if path and path.is_file() else None
        doctor.report(["conformance"])
        after = path.read_bytes() if path and path.is_file() else None
        self.assertEqual(before, after)

    def test_nothing_the_doctor_writes_lands_outside_its_own_directory(self):
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        # every rmtree in this module is of a path under the doctor's own
        # directory or of a temporary directory it made
        for call in re.findall(r"shutil\.rmtree\((\w+)", source):
            self.assertIn(call, ("root", "tmp", "home"), call)


class EvidenceARestartDoesNotOwn(unittest.TestCase):
    """A diagnosis files real evidence, so it is subject to the same rule
    every other record is: one still marked `running` when a board starts
    is evidence that board does not own."""

    def test_a_doctor_record_left_running_is_closed_rather_than_believed(self):
        record = {
            "v": 1, "id": profiles.evidence_id("verification", "req:x", "doc"),
            "runner": {"module": "verify", "version": 1},
            "task": verify.FIXTURE_TASK, "stage": "", "agent": None,
            "actor": "doctor", "doctor": "runtime-test", "intent": "work",
            "check": "doctor-pass", "scope": None, "requested": time.time(),
            "outcome": "running", "reason": None, "why": "bench is running it",
            "rc": None, "started": time.time(), "ended": None,
            "duration": None, "prepare": [], "location": "local",
            "log": str(verify.log_path_of("ev:doctor-recover-test")),
        }
        verify._store(record)
        try:
            closed = verify.recover()
            mine = [r for r in closed if r["id"] == record["id"]]
            self.assertTrue(mine, "the doctor's own stale record was skipped")
            self.assertEqual(mine[0]["outcome"], "error")
            self.assertNotEqual(mine[0]["outcome"], "passed")
        finally:
            verify.path_of(record["id"]).unlink(missing_ok=True)
            verify.forget_cached()


class CancellationAndFailure(unittest.TestCase):
    def test_a_fixture_check_can_be_cancelled_and_is_not_a_failure(self):
        """Holding a run cancels the verifications bench was running for
        it. The doctor's own are no different, and a cancellation is
        recorded as one rather than as a test failure."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "slow.py").write_text(
                "import time\ntime.sleep(30)\n", encoding="utf-8")
            document = root / "profiles.json"
            document.write_text(json.dumps({
                "v": 1,
                "checks": [{"name": "slow", "run": "python3 slow.py",
                            "root": "worktree", "timeout": 120}],
                "profiles": {"work": {"verify": ["slow"]}},
            }), encoding="utf-8")
            found = profiles.resolve("work", worktree=root,
                                     policy=profiles.document_at(document))
            self.assertFalse(found["blockers"], found["blockers"])
            found["task"] = verify.FIXTURE_TASK
            found["stage"] = ""
            found["requestId"] = profiles.request_id("work",
                                                     verify.FIXTURE_TASK)
            out = {}

            def ask():
                out["record"] = verify.request(
                    check="slow", actor="doctor",
                    fixture={"manifest": found, "root": root,
                             "label": "cancellation-test"})

            thread = threading.Thread(target=ask)
            thread.start()
            deadline = time.time() + 30
            evidence = None
            while time.time() < deadline and evidence is None:
                time.sleep(0.2)
                with verify.state.LOCK:
                    running = [r for r in verify.RUNNING.values()
                               if r.get("check") == "slow"]
                if running:
                    evidence = running[0]["id"]
            self.assertIsNotNone(evidence, "the fixture check never started")
            verify.cancel(evidence, "cancelled-by-request")
            thread.join(timeout=60)
            record = out.get("record") or {}
            self.assertEqual(record.get("outcome"), "cancelled",
                             record.get("why"))
            self.assertNotEqual(record.get("outcome"), "failed")

    def test_a_missing_fixture_root_is_refused_rather_than_run_elsewhere(self):
        found = profiles.resolve("work", worktree=Path("/nonexistent/wt"))
        found["task"] = verify.FIXTURE_TASK
        found["stage"] = ""
        found["verify"] = [{
            "name": "x", "run": "python3 x.py", "executable": "python3",
            "argv": ["x.py"], "root": "worktree", "scopes": {}, "prepare": [],
            "timeout": 10, "required": True, "help": None, "source": "test",
            "status": "available", "revision": "deadbeef",
            "checkId": "check:x@deadbeef", "enforcement": "enforced"}]
        record = verify.request(
            check="x", actor="doctor",
            fixture={"manifest": found, "root": Path("/nonexistent/fixture"),
                     "label": "missing-root"})
        self.assertEqual(record["outcome"], "denied")
        self.assertEqual(record["reason"], "root-unavailable")


# ── the real CLI, only when separately authorised ──────────────────────


class TheRealAgentStaysDeferred(unittest.TestCase):
    def test_without_approval_every_row_is_deferred_and_none_is_absent(self):
        report = doctor.report(["conformance", "runtime"])
        rows = [f for f in report["findings"] if f["mode"] == "real-agent"]
        self.assertTrue(rows, "the real-CLI rows vanished")
        for row in rows:
            self.assertEqual(row["outcome"], "deferred", row["id"])
            self.assertEqual(row["evidence"], "real-agent", row["id"])
        self.assertFalse(report["approvedRealAgent"])

    def test_both_intents_are_probed_not_just_the_worker(self):
        intents = {p["intent"] for p in doctor.REAL_AGENT_PROBES}
        self.assertEqual(intents, {"work", "review"})
        rows = doctor.real_agent(approved=False)
        self.assertEqual({r["intent"] for r in rows if r["intent"]},
                         {"work", "review"})

    def test_asking_for_the_mode_is_not_approving_it(self):
        report = doctor.report(["real-agent"])
        for row in report["findings"]:
            if row["mode"] == "real-agent":
                self.assertEqual(row["outcome"], "deferred")
        row = _by_name(report["findings"], "smoke:work")
        self.assertIn("Ask for it explicitly", row["remedy"])

    def test_the_deferred_row_says_what_it_would_cost_and_bound_it_by(self):
        row = doctor.real_agent(approved=False)[0]
        self.assertIn("spend tokens", row["detail"])
        self.assertIn(str(doctor.REAL_AGENT_TIMEOUT), row["detail"])
        self.assertIn("disposable fixture", row["detail"])

    def test_adjacent_secret_reads_stay_deferred_however_much_is_approved(self):
        """No amount of launching makes an unenforced guarantee true, so
        this row never becomes `passed` — approved or not."""
        for approved in (False, True):
            rows = doctor.real_agent(approved=approved) if not approved \
                else doctor.real_agent(approved=False)
            row = _by_name(rows, "adjacent-secret-reads")
            self.assertIsNotNone(row)
            self.assertEqual(row["outcome"], "deferred")
            self.assertIn("filesystem-sandbox", row["detail"])

    def test_the_configured_model_and_account_are_preserved(self):
        """Bench passes no model of its own: absent means the vendor's own
        resolution, which is the operator's account default."""
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        self.assertIn('env.pop("AGENT_MODEL", None)', source)
        # the model comes off the same resolution the launch takes (task 77)
        self.assertIn('found["selection"]["model"]["value"]', source)

    def _stub_cli(self, tmp: Path, body: str) -> Path:
        stub = tmp / "stub.py"
        stub.write_text(body, encoding="utf-8")
        wrapper = tmp / "bin"
        wrapper.write_text(f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n",
                           encoding="utf-8")
        wrapper.chmod(0o755)
        return wrapper

    def test_an_approved_smoke_reads_its_verdict_off_disk(self):
        """Exercised against the stub the adapter declares, so the wiring
        is proved without a token being spent — `stub` evidence about a
        `real-agent` path, and this test says so rather than pretending the
        CLI was tested.

        The point being proved is the discipline: a worker that writes the
        file passes, a reviewer that writes one fails, and in both cases
        bench looked rather than believing what the run printed."""
        adapter = profiles.adapter_identity()
        seam = doctor._conformance_seam(adapter)
        self.assertTrue(seam.get("stubEnv"))
        with tempfile.TemporaryDirectory() as tmp:
            # A stub that writes the file it is "asked" for in both
            # intents: correct for `work`, forbidden for `review`.
            wrapper = self._stub_cli(Path(tmp),
                "import os, sys\n"
                "name = 'doctor-wrote-this.txt' if '--permission-mode' in sys.argv "
                "and 'acceptEdits' in sys.argv "
                "else 'review-should-not-have-written.txt'\n"
                "open(name, 'w').write('x')\n"
                f"print({doctor.REAL_AGENT_MARKER!r})\n")
            os.environ[seam["stubEnv"]] = str(wrapper)
            try:
                rows = doctor.real_agent(approved=True)
            finally:
                os.environ.pop(seam["stubEnv"], None)
        work = _by_name(rows, "smoke:work")
        review = _by_name(rows, "smoke:review")
        self.assertEqual(work["outcome"], "passed", work["detail"])
        self.assertTrue(work["observed"]["wrote"])
        self.assertEqual(review["outcome"], "failed",
                         "a reviewer that wrote a file must not pass")
        self.assertTrue(review["observed"]["wrote"])
        self.assertIn("read off disk", work["detail"].lower())
        for row in (work, review):
            self.assertEqual(row["evidence"], "real-agent")
            self.assertEqual(row["bounded"]["launches"], 1)

    def test_a_reviewer_that_writes_nothing_passes(self):
        adapter = profiles.adapter_identity()
        seam = doctor._conformance_seam(adapter)
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = self._stub_cli(Path(tmp),
                f"print({doctor.REAL_AGENT_MARKER!r})\n")
            os.environ[seam["stubEnv"]] = str(wrapper)
            try:
                rows = doctor.real_agent(approved=True)
            finally:
                os.environ.pop(seam["stubEnv"], None)
        review = _by_name(rows, "smoke:review")
        self.assertEqual(review["outcome"], "passed", review["detail"])
        self.assertFalse(review["observed"]["wrote"])
        self.assertFalse(review["observed"]["committed"])
        # a worker that wrote nothing is the other half, and it fails
        work = _by_name(rows, "smoke:work")
        self.assertEqual(work["outcome"], "failed", work["detail"])

    def test_the_smoke_runs_only_in_its_own_fixture_and_leaves_nothing(self):
        before = {p.name for p in config.DOCTOR_DIR.glob("*")} \
            if config.DOCTOR_DIR.is_dir() else set()
        adapter = profiles.adapter_identity()
        seam = doctor._conformance_seam(adapter)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "where.txt"
            wrapper = self._stub_cli(Path(tmp),
                "import os\n"
                f"open({str(marker)!r}, 'a').write(os.getcwd() + chr(10))\n"
                f"print({doctor.REAL_AGENT_MARKER!r})\n")
            os.environ[seam["stubEnv"]] = str(wrapper)
            try:
                doctor.real_agent(approved=True)
            finally:
                os.environ.pop(seam["stubEnv"], None)
            seen = [Path(line).resolve()
                    for line in marker.read_text(encoding="utf-8").split("\n")
                    if line.strip()]
        self.assertEqual(len(seen), 2, "both intents should have launched")
        for where in seen:
            self.assertIn(config.DOCTOR_DIR.resolve(), where.parents,
                          "the smoke ran outside its own fixture")
            self.assertNotEqual(where, Path(config.app_repo()).resolve())
            self.assertFalse(where.is_dir(), "the smoke's fixture was kept")
        after = {p.name for p in config.DOCTOR_DIR.glob("*")} \
            if config.DOCTOR_DIR.is_dir() else set()
        self.assertEqual(after - before, set())

    def test_the_launch_identity_is_not_a_cards(self):
        """A diagnosis is nobody's card, so nothing it launches reports
        against one: the hook bridge is given no run and no task."""
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        self.assertIn('for name in ("BOARD_AGENT_ID", "BOARD_TASK")', source)


# ── redaction, and what travels ────────────────────────────────────────


class CredentialsAndContentsDoNotTravel(unittest.TestCase):
    def test_the_report_says_what_it_withholds(self):
        redaction = doctor.redaction()
        for key in ("paths", "values", "logs", "location"):
            self.assertTrue(redaction[key], key)
        self.assertIn("local", redaction["location"])

    def test_anything_credential_shaped_is_scrubbed_out_of_a_detail(self):
        leaked = "token: ghp_0123456789abcdefghijklmnopqrstuvwxyz"
        found = doctor._finding("inspect", "x", "failed", "about", leaked)
        self.assertNotIn("ghp_0123456789abcdefghijklmnopqrstuvwxyz",
                         found["detail"])

    def test_the_doctor_scrubs_with_the_same_scrubber_the_evidence_uses(self):
        source = (CORE / "doctor.py").read_text(encoding="utf-8")
        self.assertIn("verify.scrub", source)

    def test_no_approved_documents_contents_are_anywhere_in_the_report(self):
        report = doctor.report(["conformance"])
        blob = json.dumps(report)
        self.assertNotIn("contextContents", blob)
        for reading in report["intents"]:
            for item in reading["context"]:
                for key in item:
                    self.assertIn(key, profiles.PUBLIC_INPUT, key)

    def test_no_environment_value_reaches_the_report(self):
        os.environ["BENCH_DOCTOR_TEST_SECRET"] = "a-value-nobody-should-see"
        try:
            blob = json.dumps(doctor.report(["conformance"]))
        finally:
            os.environ.pop("BENCH_DOCTOR_TEST_SECRET", None)
        self.assertNotIn("a-value-nobody-should-see", blob)


# ── the CLI ────────────────────────────────────────────────────────────


class TheCommandLine(unittest.TestCase):
    """`board.py --doctor`, which is argparse and wiring and nothing else:
    every option and every printed word belongs to doctor.py."""

    def _run(self, *args):
        return subprocess.run([sys.executable, str(CORE / "board.py"),
                               "--doctor", *args], capture_output=True,
                              text=True, timeout=900, env=dict(os.environ))

    def test_with_no_arguments_it_inspects_and_starts_nothing(self):
        result = self._run()
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertIn("inspect", result.stdout)
        self.assertIn("not asked", result.stdout)
        self.assertIn("what this report withholds", result.stdout)

    def test_json_is_the_whole_report(self):
        result = self._run("--json")
        self.assertIn(result.returncode, (0, 1), result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["v"], doctor.VERSION)
        self.assertIn("coverage", report)
        self.assertIn("findings", report)

    def test_the_real_agent_mode_refuses_without_its_own_approval(self):
        result = self._run("--real-agent")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--approve-real-agent", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_an_unknown_flag_is_refused_by_name(self):
        result = self._run("--make-it-green")
        self.assertEqual(result.returncode, 2)
        self.assertIn("make-it-green", result.stderr)

    def test_the_board_does_not_serve_when_it_is_asked_to_diagnose(self):
        source = (CORE / "board.py").read_text(encoding="utf-8")
        self.assertIn("raise SystemExit(doctor.cli(", source)
        self.assertLess(source.index("doctor.cli("),
                        source.index("ThreadingHTTPServer(("))

    def test_help_lists_every_mode_and_what_it_costs(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for phrase in ("--conformance", "--active", "--approve-real-agent",
                       "spends tokens", "Starts no"):
            self.assertIn(phrase, result.stdout, phrase)

    def test_the_exit_status_says_which_kind_of_answer_came_back(self):
        for line in ("0  ready", "1  something", "2  the request"):
            self.assertIn(line, doctor.HELP)

    def test_an_unknown_intent_is_refused_rather_than_silently_ignored(self):
        with self.assertRaises(ValueError) as caught:
            doctor.parse(["--intent", "whatever"])
        self.assertIn("whatever", str(caught.exception))

    def test_quiet_drops_only_the_passing_rows(self):
        report = doctor.report()
        full = doctor.render(report, doctor.parse([]))
        quiet = doctor.render(report, doctor.parse(["--quiet"]))
        self.assertLess(len(quiet), len(full))
        self.assertIn("deferred", quiet)


# ── the HTTP surface ───────────────────────────────────────────────────


class TheApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = importlib.import_module("httpd")

    def test_a_get_is_inspection_and_only_inspection(self):
        report = self.httpd.doctor_payload([])
        self.assertEqual(report["ran"], ["inspect"])
        rows = {row["mode"]: row for row in report["coverage"]}
        for mode in ("conformance", "runtime", "real-agent"):
            self.assertFalse(rows[mode]["asked"], mode)

    def test_the_route_exists_for_reading_and_for_asking_separately(self):
        source = (CORE / "httpd.py").read_text(encoding="utf-8")
        self.assertIn('elif path == "/api/doctor":', source)
        self.assertIn('elif path == "/api/doctor/run":', source)

    def test_the_post_refuses_the_real_agent_without_approval(self):
        with self.assertRaises(ValueError) as caught:
            self.httpd.doctor_payload(["real-agent"], approve=False,
                                      asked=True)
        self.assertIn("approve", str(caught.exception).lower())

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self.httpd.doctor_payload(["make-it-green"], asked=True)


# ── the page ───────────────────────────────────────────────────────────


class ThePage(unittest.TestCase):
    """board.html is one file with inline JS and no frontend runner, so
    these are source-level invariants — the ones that, if broken, would
    let a static pass be read as a real-CLI pass, or let opening the
    inspector start something."""

    @classmethod
    def setUpClass(cls):
        cls.html = (CORE / "board.html").read_text(encoding="utf-8")

    def test_the_inspector_is_discoverable_from_the_header(self):
        self.assertIn('id="doctorchip"', self.html)
        self.assertIn("doctorSheet(", self.html)

    def test_opening_it_is_a_get_and_starts_nothing(self):
        self.assertIn("'/api/doctor'", self.html)
        start = self.html.index("async function doctorSheet(")
        body = self.html[start:start + 9000]
        self.assertNotIn("'/api/doctor/run'", body.split("doctorRun(")[0],
                         "the sheet asks for an active mode on open")

    def test_every_active_mode_arms_before_it_fires(self):
        self.assertIn("data-doctormode", self.html)
        self.assertIn("armed", self.html)
        self.assertIn("doctorRun(", self.html)

    def test_the_buttons_say_their_side_effects(self):
        """In the server's own words, not the page's — so a mode that
        gains a side effect cannot keep a button that does not mention
        it."""
        self.assertIn("m.sideEffects", self.html)
        self.assertIn("m.needsApproval", self.html)
        self.assertIn("side effects", self.html)
        for mode in ("conformance", "runtime", "real-agent"):
            self.assertTrue(doctor.MODES[mode]["sideEffects"], mode)

    def test_the_real_cli_stage_asks_a_second_time_before_spending(self):
        start = self.html.index("async function doctorRun(")
        body = self.html[start:start + 2000]
        self.assertIn("needsApproval", body)
        self.assertIn("spends tokens", body)
        self.assertIn("approve: !!needsApproval", body)

    def test_the_page_renders_the_servers_own_words(self):
        """Four outcomes, four evidence levels and the remedy, all read
        from the payload — the page never invents a verdict."""
        for expr in ("DOCTOR_OUTCOME[", "report.evidence[", "f.remedy",
                     "f.code", "row.doesNotProve"):
            self.assertIn(expr, self.html, expr)

    def test_the_page_never_calls_a_static_reading_a_proof(self):
        shown = self.html.lower()
        for claim in ("fully verified", "guaranteed secure", "sandboxed"):
            self.assertNotIn(claim, shown, claim)

    def test_deferred_is_drawn_apart_from_passed(self):
        self.assertIn("deferred", self.html)
        self.assertRegex(self.html, r"deferred:\s*\[")

    def test_the_focus_view_carries_the_same_readiness_panel(self):
        self.assertIn("readinessPanel(", self.html)
        self.assertGreaterEqual(len(self.html.split("readinessPanel(")) - 1, 2)

    def test_the_sheet_is_usable_narrow_and_labelled(self):
        start = self.html.index("async function doctorSheet(")
        body = self.html[start:start + 12000]
        self.assertIn("aria-label", body)
        self.assertIn("<details", body)


if __name__ == "__main__":
    unittest.main()
