"""Verification contention provenance (task 179).

`checkload` watches the runner's registry beside it and keeps a diagnostic
sidecar per verification. These tests hold its promises:

- queue (waiting), preparation and execution are told apart by phase, and
  the peers it lists are the checks it actually saw, with the seconds
  observed together and executing together;
- it is bounded and sanitized: at most `MAX_PEERS` peers listed (the rest
  counted), names clipped, no command, path or environment value;
- it is diagnostic only: it touches no evidence record, it is outside the
  runner revision and the result identity, and an older record with no
  sidecar reads as unobserved;
- a run whose ending was not seen says so.

`verify/contention-report` reads evidence records (old ones included) and
the sidecars, and keeps queue delay, execution and overlap apart.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
VERIFY_DIR = REPO / "verify"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import checkload  # noqa: E402
import evidence  # noqa: E402
import resultidentity  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

# verify/contention.py is a standalone helper, loaded by path so the verify/
# directory never goes on the import path beside the core modules
_spec = importlib.util.spec_from_file_location("contention", VERIFY_DIR / "contention.py")
contention = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contention)

HOST = {"load": [3.5, 2.0, 1.0], "cpus": 8, "memAvailableMiB": 4096}


def runs(**phases):
    return {"runs": {eid: {"check": check, "phase": phase, "doctor": False}
                     for eid, (check, phase) in phases.items()},
            "agents": 2}


A = "ev:verification@aaaaaaaaaaaa"
B = "ev:verification@bbbbbbbbbbbb"


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, config, "EVIDENCE_DIR", config.EVIDENCE_DIR)
        config.EVIDENCE_DIR = self.tmp / "evidence"
        checkload.reset()
        self.addCleanup(checkload.reset)

    def side(self, eid):
        return json.loads(checkload.path_of(eid).read_text(encoding="utf-8"))


class TheSidecar(Base):

    def test_phases_peers_and_end_are_recorded(self):
        checkload.sample(100.0, {"runs": {A: {"check": "bench-backend",
                                               "phase": "waiting"}}, "agents": 1}, HOST)
        doc = self.side(A)
        self.assertEqual(doc["kind"], "bench-check-load")
        self.assertEqual(doc["first"]["phase"], "waiting")
        self.assertFalse(doc["complete"])
        self.assertIsNone(doc["end"])
        # B arrives while A waits: together, not yet executing together
        checkload.sample(105.0, {"runs": {
            A: {"check": "bench-backend", "phase": "waiting"},
            B: {"check": "bench-browser", "phase": "running"}}, "agents": 1}, HOST)
        checkload.sample(110.0, {"runs": {
            A: {"check": "bench-backend", "phase": "preparing"},
            B: {"check": "bench-browser", "phase": "running"}}, "agents": 1}, HOST)
        checkload.sample(115.0, {"runs": {
            A: {"check": "bench-backend", "phase": "running"},
            B: {"check": "bench-browser", "phase": "running"}}, "agents": 3}, HOST)
        written = checkload.sample(120.0, {"runs": {}, "agents": 0}, HOST)
        self.assertEqual(set(written), {A, B})
        doc = self.side(A)
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["end"]["at"], 120.0)
        self.assertEqual(doc["phases"], {"waiting": 100.0, "preparing": 110.0,
                                         "running": 115.0})
        self.assertEqual(doc["first"]["slots"], config.VERIFY_PARALLEL)
        peer, = doc["peers"]
        self.assertEqual((peer["evidence"], peer["check"]), (B, "bench-browser"))
        # B was seen at 105, 110, 115: 10 s together after its first reading,
        # executing together only once A held a slot (110→115)
        self.assertEqual(peer["together"], 10.0)
        self.assertEqual(peer["executing"], 10.0)
        self.assertEqual(doc["most"], {"executing": 2, "live": 2, "agents": 3})
        self.assertEqual(doc["load1"], {"samples": 4, "mean": 3.5, "max": 3.5})
        self.assertEqual(doc["last"]["others"], {"waiting": 0, "preparing": 0,
                                                 "running": 1})

    def test_waiting_peer_is_together_but_not_executing(self):
        checkload.sample(0.0, runs(**{A: ("x", "running"), B: ("y", "waiting")}), HOST)
        checkload.sample(5.0, runs(**{A: ("x", "running"), B: ("y", "waiting")}), HOST)
        checkload.sample(10.0, runs(), HOST)
        peer, = self.side(A)["peers"]
        self.assertEqual((peer["together"], peer["executing"]), (5.0, 0.0))

    def test_a_gap_the_beat_did_not_see_is_not_counted(self):
        checkload.sample(0.0, runs(**{A: ("x", "running"), B: ("y", "running")}), HOST)
        checkload.sample(0.0 + checkload.MAX_STEP + 1,
                         runs(**{A: ("x", "running"), B: ("y", "running")}), HOST)
        checkload.sample(100.0, runs(), HOST)
        self.assertEqual(self.side(A)["peers"][0]["together"], 0.0)

    def test_peers_are_bounded_and_the_rest_counted(self):
        many = {f"ev:verification@{i:012x}": ("c", "running")
                for i in range(checkload.MAX_PEERS + 5)}
        many[A] = ("me", "running")
        checkload.sample(0.0, runs(**many), HOST)
        checkload.sample(1.0, runs(), HOST)
        doc = self.side(A)
        self.assertEqual(len(doc["peers"]), checkload.MAX_PEERS)
        self.assertEqual(doc["peersOmitted"], 5)

    def test_names_are_clipped_and_nothing_but_numbers_ids_and_names_travel(self):
        record = {"id": A, "check": "n" * 500 + "\x1b[2J", "outcome": "running",
                  "argv": ["/secret/path", "--token=abc"], "command": "rm -rf /",
                  "env": {"home": "/home/someone"}}
        self.addCleanup(verify.RUNNING.pop, A, None)
        with state.LOCK:
            verify.RUNNING[A] = record
        seen = checkload.snapshot()
        self.assertEqual(seen["runs"][A]["check"], "n" * checkload.NAME_CAP)
        self.assertEqual(seen["runs"][A]["phase"], "waiting")   # no log yet
        checkload.sample(0.0, seen, HOST)
        text = checkload.path_of(A).read_text(encoding="utf-8")
        for leak in ("/secret/path", "--token", "rm -rf", "/home/someone", "\x1b"):
            self.assertNotIn(leak, text)

    def test_the_log_tells_a_run_from_a_wait_and_ended_runs_are_not_live(self):
        log = evidence.log_path_of(A)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("x", encoding="utf-8")
        self.assertEqual(checkload.phase_of({"id": A, "outcome": "running"}), "running")
        self.assertEqual(checkload.phase_of({"id": B, "outcome": "running"}), "waiting")
        self.assertEqual(checkload.phase_of({"id": B, "outcome": "preparing"}), "preparing")
        self.assertIsNone(checkload.phase_of({"id": A, "outcome": "passed"}))

    def test_an_ending_not_observed_stays_incomplete(self):
        checkload.sample(0.0, runs(**{A: ("x", "running")}), HOST)
        checkload.reset()        # the board stopped
        doc = checkload.read(A)
        self.assertFalse(doc["complete"])
        self.assertIsNone(doc["end"])

    def test_a_record_with_no_sidecar_reads_as_unobserved(self):
        self.assertIsNone(checkload.read(A))
        self.assertIsNone(checkload.read("../../etc/passwd"))

    def test_an_unwritable_directory_changes_nothing(self):
        config.EVIDENCE_DIR = self.tmp / "file"
        config.EVIDENCE_DIR.write_text("not a directory", encoding="utf-8")
        checkload.sample(0.0, runs(**{A: ("x", "running")}), HOST)   # no raise
        self.assertIsNone(checkload.read(A))

    def test_it_never_touches_the_evidence_record(self):
        record = {"id": A, "check": "x", "outcome": "passed", "requested": 1.0}
        evidence.store(record)
        before = evidence.path_of(A).read_bytes()
        checkload.sample(0.0, runs(**{A: ("x", "running")}), HOST)
        checkload.sample(5.0, runs(), HOST)
        self.assertEqual(evidence.path_of(A).read_bytes(), before)
        # and the sidecar is not read as evidence
        self.assertEqual([r["id"] for r in evidence.all_evidence()], [A])

    def test_host_reading_is_numbers_or_none(self):
        info = checkload.host()
        self.assertEqual(set(info), {"load", "cpus", "memAvailableMiB"})
        if info["load"] is not None:
            self.assertEqual(len(info["load"]), 3)


class OutsideTheIdentity(unittest.TestCase):

    def test_no_runner_file_imports_checkload(self):
        for name in resultidentity.RUNNER_FILES:
            tree = ast.parse((CORE / name).read_text(encoding="utf-8"))
            imported = {a.name for n in ast.walk(tree)
                        if isinstance(n, ast.Import) for a in n.names}
            imported |= {n.module for n in ast.walk(tree)
                         if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("checkload", imported, name)
        self.assertNotIn("checkload.py", resultidentity.RUNNER_FILES)
        self.assertNotIn("checkload", resultidentity.BOUND_ELSEWHERE)

    def test_the_runner_revision_ignores_the_sidecar_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in resultidentity.RUNNER_FILES:
                shutil.copy(CORE / name, Path(tmp) / name)
            before = resultidentity.runner_revision(tmp)
            (Path(tmp) / "checkload.py").write_text("# changed\n", encoding="utf-8")
            self.assertEqual(resultidentity.runner_revision(tmp), before)
            self.assertEqual(before, resultidentity.runner_revision())


# ── verify/contention-report ───────────────────────────────────────────

def record(eid, check, requested, start, end, *, queued=None, prep=None,
           outcome="passed", scope=None, doctor=False):
    rec = {"id": eid, "check": check, "scope": scope, "outcome": outcome,
           "requested": requested, "started": start, "ended": end,
           "duration": end - start, "prepare": [],
           "source": {"commit": "c" * 40}}
    if prep:
        rec["prepare"] = [{"name": "deps", "started": prep[0], "ended": prep[1],
                           "duration": prep[1] - prep[0]}]
    if queued is not None:
        rec["queued"] = queued
    if doctor:
        rec["doctor"] = {"fixture": "x"}
    return rec


class TheReport(unittest.TestCase):

    def test_queue_preparation_and_execution_are_apart(self):
        row = contention.phases(record("ev:v@1", "b", 100.0, 160.0, 400.0,
                                       queued=50.0, prep=(130.0, 160.0)))
        self.assertEqual(row["beforeStart"], 30.0)     # request → first process
        self.assertEqual(row["slotWait"], 50.0)
        self.assertEqual(row["prepare"], 30.0)
        self.assertEqual(row["checkProcess"], 240.0)
        self.assertEqual(row["execution"], 270.0)      # prep start → end
        self.assertEqual(row["unknown"], [])

    def test_an_older_record_keeps_its_unknowns(self):
        legacy = {"id": "ev:v@2", "check": "b", "outcome": "passed",
                  "requested": 10.0, "started": 12.0, "ended": 20.0, "duration": 8.0}
        row = contention.phases(legacy)
        self.assertIsNone(row["slotWait"])
        self.assertTrue(any(u.startswith("slot-wait") for u in row["unknown"]))
        self.assertEqual(row["execution"], 8.0)
        denied = contention.phases({"id": "ev:v@3", "check": "b", "outcome": "denied",
                                    "requested": 1.0})
        self.assertIsNone(denied["execution"])
        self.assertIsNone(denied["beforeStart"])

    def test_overlap_is_computed_from_the_records(self):
        recs = [record("ev:v@1", "backend", 0, 0, 100),
                record("ev:v@2", "browser", 50, 50, 150),
                record("ev:v@3", "browser", 200, 200, 300)]
        report = contention.analyse(recs)
        by = {r["id"]: r for r in report["runs"]}
        self.assertEqual(by["ev:v@1"]["overlapSeconds"], 50.0)
        self.assertEqual(by["ev:v@2"]["overlapped"][0]["evidence"], "ev:v@1")
        self.assertEqual(by["ev:v@3"]["overlapped"], [])

    def test_scoped_and_doctor_runs_stay_out_unless_asked(self):
        recs = [record("ev:v@1", "b", 0, 0, 10, scope="fast"),
                record("ev:v@2", "b", 0, 0, 10, doctor=True),
                record("ev:v@3", "b", 0, 0, 10)]
        self.assertEqual([r["id"] for r in contention.analyse(recs)["runs"]], ["ev:v@3"])
        self.assertEqual(len(contention.analyse(recs, include_scoped=True)["runs"]), 2)

    def test_a_small_sample_is_said_to_be_too_small(self):
        recs = [record("ev:v@1", "browser", 0, 0, 400),
                record("ev:v@2", "backend", 0, 0, 1500),
                record("ev:v@3", "browser", 2000, 2000, 2380)]
        browser = next(c for c in contention.analyse(recs)["comparison"]
                       if c["check"] == "browser")
        self.assertEqual((browser["quiet"]["n"], browser["overlapped"]["n"]), (1, 1))
        self.assertIn("too small", browser["note"])
        self.assertNotIn("ratio", browser)

    def test_a_readable_sample_gives_a_ratio_and_calls_it_an_association(self):
        recs, t = [], 0
        for i in range(3):          # quiet browser runs
            recs.append(record(f"ev:q@{i}", "browser", t, t, t + 400)); t += 1000
        for i in range(3):          # each beside a backend run
            recs.append(record(f"ev:b@{i}", "backend", t, t, t + 1500))
            recs.append(record(f"ev:o@{i}", "browser", t + 10, t + 10, t + 610)); t += 2000
        browser = next(c for c in contention.analyse(recs)["comparison"]
                       if c["check"] == "browser")
        self.assertEqual(browser["ratio"], 1.5)
        self.assertIn("not a cause", browser["note"])

    def test_the_program_reads_a_directory_and_its_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "load").mkdir()
            (root / "reuse").mkdir()
            rec = record("ev:verification@aaaaaaaaaaaa", "bench-backend", 0, 5, 50)
            (root / "ev-verification-aaaaaaaaaaaa.json").write_text(json.dumps(rec))
            (root / "broken.json").write_text("{")
            (root / "load" / "ev-verification-aaaaaaaaaaaa.json").write_text(json.dumps({
                "kind": "bench-check-load", "v": 1,
                "evidence": "ev:verification@aaaaaaaaaaaa",
                "observer": {"cpus": 8}, "first": {"slots": 2},
                "load1": {"samples": 3, "mean": 1.5, "max": 2.0},
                "most": {"executing": 2, "agents": 1}, "complete": True}))
            out = subprocess.run(
                [sys.executable, str(VERIFY_DIR / "contention-report"), tmp, "--json"],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            report = json.loads(out.stdout)
            run, = report["runs"]
            self.assertEqual(run["observed"]["load1Max"], 2.0)
            self.assertEqual(run["observed"]["slots"], 2)
            text = subprocess.run(
                [sys.executable, str(VERIFY_DIR / "contention-report"), tmp],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("not cause", text.stdout)
            self.assertIn("unknown", text.stdout)
            missing = subprocess.run(
                [sys.executable, str(VERIFY_DIR / "contention-report"),
                 str(root / "nope")], capture_output=True, text=True, timeout=60)
            self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
