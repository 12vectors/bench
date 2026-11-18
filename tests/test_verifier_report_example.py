"""Task 135 — the disposable, Cicero-shaped example under
`verify/examples/report-contract/`, run through bench's own runner in the
vendored, self-hosted and external layouts.

The wrapper keeps its own safety checks (an unsafe target is refused, a
missing fixture is a setup failure) and adds only a small report emitter.
Each case asks for the named check the way a run does, reads the
findings and the cited artifact through the run's own diagnostics, fixes
the disposable candidate, commits, and asks for the same check again.
Nothing here touches a real project, a remote or operator policy.

    python3 -m unittest tests.test_verifier_report_example -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "manager" / "core"))

import artifacts  # noqa: E402
import config  # noqa: E402
import evidence  # noqa: E402
import gates  # noqa: E402
import verify  # noqa: E402

import test_verification_evidence as base  # noqa: E402

CARD, STEM, git = base.CARD, base.STEM, base.git
EXAMPLE = (Path(__file__).resolve().parents[1] / "verify" / "examples"
           / "report-contract")

CASES = [{"id": "c1", "amount": 100, "expected": 110.0},
         {"id": "c2", "amount": 50, "expected": 55.0},
         {"id": "c3", "amount": 20, "expected": 22.0}]


class _Example(base.VerifyCase):
    KIND = "external"

    def setUp(self):
        super().setUp()
        if self.KIND != "external":
            tool = (self.repo / ".task-manager" if self.KIND == "vendored"
                    else self.tmp / "bench")
            tasks = tool / "tasks"
            for slug in (*config.STAGE_DIRS, "archive"):
                (tasks / slug).mkdir(parents=True)
            (tool / "reference").mkdir()
            (self.tasks / "in-progress" / CARD).rename(tasks / "in-progress" / CARD)
            local = tool / "manager" / "local"
            (local / "adapters" / config.ADAPTER).mkdir(parents=True)
            (local / "commands").mkdir(parents=True)
            self.tool, self.work, self.tasks, self.local = tool, tool, tasks, local
            self.state = local / "state"
            self.patch(TASKS=tasks, TM_ROOT=tool, LOCAL=local,
                       PLANS_DIR=self.state / "plans",
                       MANIFESTS_DIR=self.state / "manifests",
                       CONTEXT_DIR=self.state / "context",
                       EVIDENCE_DIR=self.state / "evidence",
                       VERIFY_DIR=self.state / "verify",
                       AGENT_DIR=self.state / "agent",
                       SESSIONS_DIR=self.state / "sessions", WORK_SETTING="",
                       APP_SETTING="" if self.KIND == "vendored" else str(self.repo),
                       EXTERNAL=self.KIND != "vendored")
        self.patch(ARTIFACTS_DIR=self.state / "artifacts",
                   GATES_DIR=self.state / "gates",
                   ARTIFACT_MAX_BYTES=64 * 1024,
                   ARTIFACT_CARD_BYTES=1024 * 1024, ARTIFACT_RUN_FILES=24,
                   ARTIFACT_RETENTION_DAYS=90.0)
        gates.forget_cached()
        self.addCleanup(gates.forget_cached)

    def candidate(self, *, rate: float, target: str = "fixture",
                  fixtures: bool = True, message: str = "candidate") -> Path:
        tree = self.worktree()
        (tree / "rules.json").write_text(
            json.dumps({"target": target, "rate": rate}, indent=1) + "\n",
            encoding="utf-8")
        cases = tree / "fixtures" / "cases.json"
        if fixtures:
            cases.parent.mkdir(exist_ok=True)
            cases.write_text(json.dumps(CASES), encoding="utf-8")
        elif cases.exists():
            cases.unlink()
        git(tree, "add", "-A")
        git(tree, "commit", "-qm", message)
        return tree

    def register(self) -> None:
        self.declares({"v": 1, "checks": [{
            "name": "totals", "root": "worktree", "report": "v1",
            "run": f"{sys.executable} {EXAMPLE / 'cicero_shaped_verify'}"}],
            "profiles": {"work": {"verify": ["totals"]}},
            "gates": {"acceptance": {"checks": ["totals"]}}})

    def ask_totals(self) -> dict:
        verify.forget_cached()
        gates.forget_cached()
        found = self.ask("totals")
        verify.forget_cached()
        gates.forget_cached()
        return found

    def gate(self) -> dict:
        return gates.evaluate(CARD, "acceptance")["requirements"][0]

    def test_the_loop_refuse_setup_fail_fix_pass(self):
        self.register()
        # the wrapper's own safety check, kept: an unsafe target is refused
        self.candidate(rate=1.2, target="production", message="unsafe")
        self.manifest()
        refused = self.ask_totals()
        self.assertEqual((refused["outcome"], refused["reason"]),
                         ("error", "verifier-refused"), refused.get("why"))
        self.assertEqual(refused["rc"], 2)
        self.assertEqual(refused["report"]["findings"][0]["location"]["path"],
                         "rules.json")
        self.assertEqual(self.gate()["state"], "unmet")

        # no fixtures: the wrapper's setup failure, never a test failure
        self.candidate(rate=1.2, fixtures=False, message="no fixtures")
        setup = self.ask_totals()
        self.assertEqual((setup["outcome"], setup["reason"]),
                         ("prepare-failed", "verifier-setup"), setup.get("why"))

        # a wrong rate: actionable findings and a cited diff
        self.candidate(rate=1.2, message="wrong rate")
        failed = self.ask_totals()
        self.assertEqual((failed["outcome"], failed["reason"]),
                         ("failed", "report-failed"), failed.get("why"))
        got = verify.diagnostic(failed["id"], agent="run-1")
        findings = got["report"]["findings"]
        self.assertEqual(len(findings), 3)
        self.assertEqual(findings[0]["location"], {"path": "rules.json", "line": 3})
        self.assertEqual((findings[0]["expected"], findings[0]["actual"]),
                         ("110.00", "120.00"))
        diff_id = findings[0]["evidence"][0]["id"]
        diff = verify.diagnostic(failed["id"], agent="run-1", artifact=diff_id)
        self.assertIn("│ c1: -110.00 +120.00", diff["excerpt"]["lines"])
        self.assertEqual(self.gate()["state"], "unmet")
        # kept outside the scratch, with this run, in this layout's state
        kept = artifacts.for_evidence(CARD, failed["id"])
        self.assertTrue(kept)
        self.assertTrue(str(artifacts.card_dir(CARD, artifacts.DIAGNOSTIC))
                        .startswith(str(self.state)))
        self.assertFalse(Path(failed["env"]["tmp"]).exists())

        # the fix, and the same check again
        head = git(self.candidate(rate=1.1, message="fix the rate"),
                   "rev-parse", "HEAD").stdout.strip()
        passed = self.ask_totals()
        self.assertEqual((passed["outcome"], passed["reason"]),
                         ("passed", "report-passed"), passed.get("why"))
        self.assertEqual(passed["source"]["commit"], head)
        row = self.gate()
        self.assertEqual(row["state"], "met", row["why"])
        self.assertEqual(row["evidence"], passed["id"])
        self.assertTrue(evidence.log_path_of(passed["id"]).is_file())

    def test_the_wrapper_needs_no_bench_to_run(self):
        """Outside bench nothing is written: the same wrapper, no report
        variables, prints its verdict and exits with it."""
        import subprocess
        tree = self.candidate(rate=1.2, message="wrong rate")
        env = {k: v for k, v in __import__("os").environ.items()
               if not k.startswith("BENCH_VERIFY")}
        ran = subprocess.run([sys.executable,
                              str(EXAMPLE / "cicero_shaped_verify")],
                             cwd=tree, env=env, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(ran.returncode, 1, ran.stdout + ran.stderr)
        self.assertIn("failed: 3 of 3", ran.stdout)
        self.assertEqual(sorted(p.name for p in tree.iterdir()
                                if p.name.endswith(".json")), ["rules.json"])


class InTheVendoredLayout(_Example):
    KIND = "vendored"


class InTheSelfHostedLayout(_Example):
    """The tool installed beside the application it manages
    (`BOARD_APP_ROOT` alone) — how an operator install runs a bench
    candidate."""
    KIND = "self-hosted"


class InTheExternalLayout(_Example):
    KIND = "external"


del _Example


if __name__ == "__main__":
    unittest.main()
