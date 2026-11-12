"""`verify/gate-compare`: a read-only before/after gate comparison (card 106).

Design #94 §15.2 asks for a dry comparison of the gate readings on the
operator's own board, before and after the resolver changes, with every
changed reading explained and an unexplained relaxation refused. This
module holds that program to its contract on **fixture** boards only —
a disposable tool install, work root and application repository built by
`GateCase`. A run against an operator's real inventory is a separate act,
made by whoever holds that data, with the same program.

- **Snapshot and read change nothing.** The fixture's live roots and the
  snapshot hash the same before and after; the copy has no remote and
  carries no `.env`, log or HMAC key unless asked.
- **The reading is the resolver's.** On the SAFER36 shape the candidate
  resolver reads `allowed`, with the facts the explanation rests on (a
  result identity, a policy that changed since).
- **Classification.** E1, E4 and E7 relaxations are accounted for; any
  other relaxation, and a requirement that disappears, are not, and the
  program exits 1. Stricter readings are explained, never refused.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_integration_gates import CARD, GateCase, card, git  # noqa: E402

import gates  # noqa: E402
import verify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "verify" / "gate-compare"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("gate_compare", str(TOOL))
    spec = importlib.util.spec_from_loader("gate_compare", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gc = load_tool()


def tree(root: Path) -> str:
    return gc.digest_tree(root)


def reading(cards: dict) -> dict:
    return {"kind": "bench-gate-reading", "v": 1, "cards": cards}


def check(state, reason=None, **kw) -> dict:
    return {"kind": "check", "key": "unit", "name": "unit", "scope": None,
            "state": state, "reason": reason, **kw}


class ClassifyingChanges(unittest.TestCase):

    def one(self, before_row, after_row, before="blocked", after="allowed",
            head="h" * 40):
        b = reading({"x.md": {"decision": before, "head": head,
                              "requirements": [before_row] if before_row else []}})
        a = reading({"x.md": {"decision": after, "head": head,
                              "requirements": [after_row] if after_row else []}})
        return gc.compare(b, a)

    def test_nothing_changed_is_nothing_to_explain(self):
        row = check("met", "passed")
        found = self.one(row, row, before="allowed")
        self.assertEqual((found["changed"], found["unaccounted"]), (0, 0))

    def test_e1_unrelated_policy_is_provenance(self):
        found = self.one(check("unmet", "stale", staleReason="policy-changed"),
                         check("met", "passed", identity=True, policyChanged=True,
                               recordCommit="h" * 40))
        self.assertEqual(found["unaccounted"], 0)
        self.assertIn("E1", found["changes"][0]["explanations"][0])

    def test_e1_needs_a_result_identity(self):
        found = self.one(check("unmet", "stale"),
                         check("met", "passed", identity=False, policyChanged=True))
        self.assertEqual(found["unaccounted"], 1)

    def test_e4_an_older_commits_outcome_is_history(self):
        found = self.one(check("unmet", "cancelled", commit="g" * 40),
                         check("met", "passed", recordCommit="h" * 40))
        self.assertEqual(found["unaccounted"], 0)
        self.assertIn("E4", found["changes"][0]["explanations"][0])

    def test_a_failure_at_this_commit_turning_met_is_unaccounted(self):
        found = self.one(check("unmet", "failed", commit="h" * 40),
                         check("met", "passed", recordCommit="h" * 40))
        self.assertEqual(found["unaccounted"], 1)

    def test_e7_a_declared_equivalent(self):
        found = self.one(check("unmet", "not-run"),
                         check("met", "passed", acceptedFrom="ev:x",
                               recordCommit="h" * 40))
        self.assertEqual(found["unaccounted"], 0)
        self.assertIn("E7", found["changes"][0]["explanations"][0])

    def test_a_pass_at_another_commit_never_explains_a_relaxation(self):
        found = self.one(check("unmet", "stale"),
                         check("met", "passed", identity=True, policyChanged=True,
                               recordCommit="g" * 40))
        self.assertEqual(found["unaccounted"], 1)

    def test_a_requirement_that_disappears_is_unaccounted(self):
        found = self.one(check("unmet", "not-run"), None)
        self.assertEqual(found["unaccounted"], 1)

    def test_a_review_that_became_met_is_unaccounted(self):
        review = {"kind": "review", "key": "bench", "name": "bench", "scope": None}
        found = self.one({**review, "state": "unmet", "reason": "no-verdict"},
                         {**review, "state": "met", "reason": "approved"})
        self.assertEqual(found["unaccounted"], 1)

    def test_stricter_is_explained_and_accounted(self):
        found = self.one(check("met", "passed"), check("unmet", "not-run"),
                         before="allowed", after="blocked")
        self.assertEqual(found["unaccounted"], 0)
        self.assertIn("stricter", found["changes"][0]["explanations"][0])

    def test_a_reason_that_moves_while_unmet_is_explained(self):
        found = self.one(check("unmet", "cancelled", commit="g" * 40),
                         check("unmet", "other-commit"), after="blocked")
        self.assertEqual(found["unaccounted"], 0)
        self.assertIn("still unmet", found["changes"][0]["explanations"][0])

    def test_a_card_only_one_resolver_could_read(self):
        b = reading({"x.md": {"decision": "blocked", "head": None, "requirements": []}})
        a = reading({"x.md": {"error": "boom"}})
        self.assertEqual(gc.compare(b, a)["unaccounted"], 1)

    def test_the_report_renders(self):
        found = self.one(check("unmet", "stale"),
                         check("met", "passed", identity=True, policyChanged=True,
                               recordCommit="h" * 40))
        found["before"]["core"] = "before"
        self.assertIn("x.md — blocked → allowed", gc.markdown(found))


class AgainstAFixtureBoard(GateCase):
    """The SAFER36 shape, on a disposable board: a pass with a result
    identity at H, then unrelated approved context in the policy."""

    def setUp(self):
        super().setUp()
        # the documented defaults, which the snapshot's reader also reads:
        # a check's default time limit is part of its result identity
        self.patch(VERIFY_TIMEOUT=1800.0, PREPARE_TIMEOUT=900.0)
        self.wt = self.worktree()
        self.document = self.gated()
        self.head = self.commit_in(self.wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        (self.work / "reference" / "card37-mockup.md").write_text("mock\n", encoding="utf-8")
        self.document["context"] = [{"name": "card37-mockup",
                                     "path": "work:reference/card37-mockup.md",
                                     "required": False}]
        self.declares(self.document)
        # only what must never be carried: the fixture's checks ran with
        # no .env, so a carried name would be a name they were not given
        (self.local / ".env").write_text(
            "BOARD_GH_TOKEN=s3cr3t-value\nSECRET_TOKEN=x\nBOARD_PORT=1\n"
            "BOARD_APP_ROOT=/somewhere/live\n", encoding="utf-8")
        (self.state / "verify").mkdir(parents=True, exist_ok=True)
        (self.state / "verify" / "env.key").write_bytes(b"k" * 32)
        verify.forget_cached()
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")

    def run_tool(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                              capture_output=True, text=True, timeout=600)

    def live(self) -> dict:
        return {name: tree(path) for name, path in
                (("tool", self.tool), ("work", self.work), ("app", self.repo / ".git"),
                 ("worktree", self.wt))}

    def snapshot(self) -> Path:
        snap = self.tmp / "snap"
        done = self.run_tool("snapshot", "--tool", self.tool, "--work", self.work,
                             "--app", self.repo, "--worktrees", self.tmp / "worktrees",
                             "--out", snap)
        self.assertEqual(done.returncode, 0, done.stderr)
        return snap

    def test_snapshot_and_read_change_nothing_live_and_nothing_copied(self):
        before = self.live()
        snap = self.snapshot()
        digest = tree(snap)
        done = self.run_tool("read", "--snapshot", snap, "--out", self.tmp / "r.json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(tree(snap), digest, "reading wrote nothing to the snapshot")
        self.assertEqual(self.live(), before, "nothing live was written")

    def test_the_snapshot_holds_no_secret_log_or_remote(self):
        snap = self.snapshot()
        self.assertFalse((snap / "tool/manager/local/.env").exists())
        self.assertFalse((snap / "tool/manager/local/state/verify/env.key").exists())
        self.assertEqual(list(snap.rglob("*.log")), [])
        self.assertEqual(git(snap / "app", "remote").stdout.strip(), "")
        meta = json.loads((snap / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["settings"], {})
        for leaked in ("SECRET_TOKEN", "BOARD_GH_TOKEN", "s3cr3t-value"):
            self.assertNotIn(leaked, (snap / "snapshot.json").read_text())
        self.assertEqual(meta["worktrees"], [self.wt.name])

    def test_the_candidate_resolver_reads_the_board_as_it_does_live(self):
        snap = self.snapshot()
        out = self.tmp / "r.json"
        self.assertEqual(self.run_tool("read", "--snapshot", snap, "--out", out).returncode, 0)
        found = json.loads(out.read_text(encoding="utf-8"))["cards"][CARD]
        self.assertEqual(found["decision"], "allowed", json.dumps(found, indent=1))
        self.assertEqual(found["head"], self.head)
        row = found["requirements"][0]
        self.assertEqual(row["state"], "met")
        self.assertTrue(row["identity"])
        self.assertTrue(row["policyChanged"])
        self.assertEqual(row["recordCommit"], self.head)

    def test_the_same_resolver_twice_changes_nothing_and_exits_zero(self):
        snap = self.snapshot()
        done = self.run_tool("run", "--snapshot", snap, "--before-core",
                             ROOT / "manager" / "core", "--out", self.tmp / "cmp")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads((self.tmp / "cmp" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["changed"], report["unaccounted"]), (0, 0))
        self.assertTrue((self.tmp / "cmp" / "report.md").is_file())

    def test_an_unaccounted_relaxation_exits_one(self):
        snap = self.snapshot()
        after = self.tmp / "a.json"
        self.run_tool("read", "--snapshot", snap, "--out", after)
        before = json.loads(after.read_text(encoding="utf-8"))
        row = before["cards"][CARD]["requirements"][0]
        row.update(state="unmet", reason="failed", commit=self.head)
        before["cards"][CARD]["decision"] = "blocked"
        (self.tmp / "b.json").write_text(json.dumps(before), encoding="utf-8")
        done = self.run_tool("compare", "--before", self.tmp / "b.json", "--after", after)
        self.assertEqual(done.returncode, 1)
        self.assertIn("UNACCOUNTED", done.stdout)

    def test_a_snapshot_inside_a_live_root_is_refused(self):
        done = self.run_tool("snapshot", "--tool", self.tool, "--work", self.work,
                             "--app", self.repo, "--out", self.work / "snap")
        self.assertEqual(done.returncode, 2)
        self.assertFalse((self.work / "snap").exists())

    def test_a_directory_that_is_not_a_core_is_refused(self):
        snap = self.snapshot()
        done = self.run_tool("read", "--snapshot", snap, "--core", self.tmp,
                             "--out", self.tmp / "r.json")
        self.assertEqual(done.returncode, 2)


class WhichSettingsTravel(unittest.TestCase):

    def test_settings_that_shape_a_check_travel_and_nothing_else(self):
        found = gc.carried({
            "BOARD_VERIFY_TIMEOUT": "60", "BOARD_APP_BASE": "trunk",
            "BOARD_AGENT_COMMANDS": "npm test",
            "BOARD_GH_TOKEN": "x", "BOARD_API_KEY": "x", "BOARD_SESSION": "x",
            "BOARD_APP_ROOT": "/live", "BOARD_PORT": "1", "BOARD_SYNC": "1",
            "BOARD_GH_BIN": "/usr/bin/gh", "OTHER": "x"})
        self.assertEqual(found, {"BOARD_VERIFY_TIMEOUT": "60",
                                 "BOARD_APP_BASE": "trunk",
                                 "BOARD_AGENT_COMMANDS": "npm test"})

    def test_the_reader_reproduces_the_boards_names_and_switches_off_the_rest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            (evidence / "ev.json").write_text(json.dumps({
                "requested": "2026-11-12T00:00:00Z",
                "env": {"passed": ["PATH", "HOME", "LANG", "BOARD_SYNC",
                                   "BOARD_GH_BIN", "BENCH_VERIFY"]}}),
                encoding="utf-8")
            env = gc.reader_env({"settings": {"BOARD_APP_BASE": "main"}}, evidence,
                                Path(tmp) / "home", Path(tmp) / "snap")
        self.assertEqual(env["BOARD_SYNC"], "0")
        self.assertEqual(env["BOARD_GH_BIN"], "false")
        self.assertIn("LANG", env)
        self.assertNotIn("BENCH_VERIFY", env)
        self.assertEqual(env["BOARD_APP_BASE"], "main")
        self.assertEqual(env["BOARD_APP_ROOT"], str(Path(tmp) / "snap" / "app"))


class TheDigestSeesEveryWrite(unittest.TestCase):

    def test_a_byte_a_name_and_a_link_each_move_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").write_text("1", encoding="utf-8")
            first = tree(root)
            (root / "a").write_text("2", encoding="utf-8")
            second = tree(root)
            (root / "b").symlink_to("a")
            third = tree(root)
        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual(hashlib.sha256().name, "sha256")


if __name__ == "__main__":
    unittest.main()
