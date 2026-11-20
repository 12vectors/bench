"""verify/unittesttiming.py times the backend suite without changing it
(task 163). Its promise is narrow, and each half has a test here: what
unittest prints, counts and exits with is unittest's own, byte for byte
where it matters; and the timings it writes are readings of this run —
each test, each class and module fixture, discovery and the whole —
bound to the interpreter that took them. verify/backend keeps the file
and its summary in the record, and verify/timing-report refuses to
compare runs that did not run the same tests.

Every case runs the real programs as processes against a disposable
candidate.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import shutil
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "verify"
COLLECTOR = VERIFY / "unittesttiming.py"

SUITE = {
    "test_a.py": """
        import time, unittest
        def setUpModule():
            time.sleep(0.15)
        class Slow(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                time.sleep(0.3)
            @classmethod
            def tearDownClass(cls):
                time.sleep(0.2)
            def test_sleeps(self):
                time.sleep(0.25)
            def test_quick(self):
                pass
        class Quick(unittest.TestCase):
            def test_one(self):
                pass
            @unittest.skip("not here")
            def test_skipped(self):
                pass
    """,
    "test_b.py": """
        import unittest
        class Mixed(unittest.TestCase):
            def test_fails(self):
                self.assertEqual(1, 2)
            def test_errors(self):
                raise RuntimeError("boom")
            def test_sub(self):
                for i in range(2):
                    with self.subTest(i=i):
                        self.assertEqual(i, 0)
    """,
}

PASSING = {
    "test_a.py": """
        import unittest
        class T(unittest.TestCase):
            def test_one(self): pass
            def test_two(self): pass
    """,
}


def candidate(base: Path, files: dict) -> Path:
    root = base / "candidate"
    (root / "manager" / "core").mkdir(parents=True)
    (root / "manager" / "core" / "board.py").write_text("# fixture\n")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    for name, body in files.items():
        (root / "tests" / name).write_text(textwrap.dedent(body))
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True,
                   capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "f"], check=True,
                   env=env, capture_output=True)
    return root


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("BENCH_VERIFY")}


def strip_times(text: str) -> str:
    # unittest prints how long the run took; nothing else may differ
    text = re.sub(r"^(Ran \d+ tests?) in [\d.]+s$", r"\1", text, flags=re.M)
    # tracebacks name the runner frames; the test's own frames must match
    return text


class Fixture(unittest.TestCase):
    files = SUITE

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="timing-test-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = candidate(self.base, self.files)

    def plain(self, *args):
        return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                               "-t", ".", *args], cwd=self.root, capture_output=True,
                              text=True, env=clean_env(), timeout=120)

    def timed(self, *args, out=None):
        out = out or self.base / "timings.json"
        r = subprocess.run([sys.executable, str(COLLECTOR), "--timings", str(out),
                            "discover", "-s", "tests", "-t", ".", *args], cwd=self.root,
                           capture_output=True, text=True, env=clean_env(), timeout=120)
        return r, (json.loads(out.read_text()) if out.is_file() else None)


class SameRunAsUnittest(Fixture):
    """The collector must not change what a run says about the suite."""

    def test_output_counts_and_exit_status_match_plain_unittest(self):
        plain = self.plain("-v")
        timed, doc = self.timed("-v")
        self.assertEqual(1, plain.returncode)
        self.assertEqual(plain.returncode, timed.returncode)
        self.assertEqual(plain.stdout, timed.stdout)
        # the verbose lines, the failures and the summary are identical
        # apart from the duration unittest prints itself
        self.assertEqual(strip_times(plain.stderr), strip_times(timed.stderr))
        self.assertIn("FAILED (failures=2, errors=1, skipped=1)", timed.stderr)
        self.assertEqual(1, doc["exitStatus"])

    def test_a_passing_run_exits_zero_like_unittest(self):
        for name in ("test_b.py",):
            (self.root / "tests" / name).unlink()
        plain, (timed, doc) = self.plain(), self.timed()
        self.assertEqual(0, plain.returncode, plain.stderr)
        self.assertEqual(0, timed.returncode, timed.stderr)
        self.assertEqual(strip_times(plain.stderr), strip_times(timed.stderr))
        self.assertEqual(0, doc["exitStatus"])

    def test_an_unimportable_module_is_reported_as_unittest_reports_it(self):
        (self.root / "tests" / "test_broken.py").write_text("import no_such_module_x\n")
        plain, (timed, doc) = self.plain("-v"), self.timed("-v")
        self.assertEqual(plain.returncode, timed.returncode)
        self.assertIn("Failed to import test module: tests.test_broken", timed.stderr)
        self.assertEqual(strip_times(plain.stderr), strip_times(timed.stderr))
        self.assertIsNotNone(doc, "the timings are written even when the run fails")

    def test_usage_without_a_timings_file_runs_nothing(self):
        r = subprocess.run([sys.executable, str(COLLECTOR), "discover", "-s", "tests"],
                           cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(2, r.returncode)
        self.assertIn("usage", r.stderr)
        self.assertNotIn("Ran ", r.stderr)


class WhatIsMeasured(Fixture):

    def setUp(self):
        super().setUp()
        self.r, self.doc = self.timed("-v")

    def test_every_test_is_timed_with_its_outcome(self):
        by_id = {t["id"]: t for t in self.doc["tests"]}
        self.assertEqual(7, len(by_id))
        self.assertEqual(7, self.doc["counts"]["tests"])
        self.assertEqual("skipped", by_id["tests.test_a.Quick.test_skipped"]["outcome"])
        self.assertEqual("failure", by_id["tests.test_b.Mixed.test_fails"]["outcome"])
        self.assertEqual("error", by_id["tests.test_b.Mixed.test_errors"]["outcome"])
        self.assertEqual("failure", by_id["tests.test_b.Mixed.test_sub"]["outcome"])
        self.assertEqual("passed", by_id["tests.test_a.Slow.test_quick"]["outcome"])
        self.assertGreaterEqual(by_id["tests.test_a.Slow.test_sleeps"]["seconds"], 0.25)
        self.assertLess(by_id["tests.test_a.Slow.test_quick"]["seconds"], 0.1)

    def test_class_and_module_fixtures_are_timed_apart_from_tests(self):
        self.assertEqual("measured", self.doc["fixtureTiming"])
        f = {(x["kind"], x["owner"]): x["seconds"] for x in self.doc["fixtures"]}
        self.assertGreaterEqual(f[("setUpClass", "tests.test_a.Slow")], 0.3)
        self.assertGreaterEqual(f[("tearDownClass", "tests.test_a.Slow")], 0.2)
        self.assertGreaterEqual(f[("setUpModule", "tests.test_a")], 0.15)
        # the module's set-up is not also counted inside a class's
        self.assertLess(f[("setUpModule", "tests.test_a")], 0.3)
        slow = next(c for c in self.doc["classes"] if c["class"] == "tests.test_a.Slow")
        self.assertEqual(2, slow["tests"])
        self.assertGreaterEqual(slow["fixtureSeconds"], 0.5)
        self.assertAlmostEqual(slow["seconds"], slow["testSeconds"] + slow["fixtureSeconds"])
        self.assertEqual("tests.test_a.Slow", self.doc["classes"][0]["class"])

    def test_the_parts_add_up_to_the_run(self):
        s = self.doc["seconds"]
        self.assertGreaterEqual(s["run"], s["tests"] + s["fixtures"] - 1e-6)
        self.assertAlmostEqual(s["unattributed"], s["run"] - s["tests"] - s["fixtures"],
                               places=6)
        self.assertAlmostEqual(s["elapsed"], s["discovery"] + s["run"], places=6)
        # setUpModule 0.15 + setUpClass 0.3 + tearDownClass 0.2
        self.assertGreaterEqual(s["fixtures"], 0.64)

    def test_the_runtime_and_measured_overhead_are_recorded(self):
        rt = self.doc["runtime"]
        self.assertEqual(sys.executable, rt["executable"])
        self.assertEqual(str(self.root), rt["cwd"])
        o = self.doc["overhead"]
        self.assertGreater(o["perTestSeconds"], 0)
        # a reading per test that costs as much as a test would be useless;
        # what it measures is tens of microseconds
        self.assertLess(o["perTestSeconds"], 0.005)
        self.assertAlmostEqual(o["testsTotalSeconds"], o["perTestSeconds"] * 7)


class BackendKeepsTheTimings(Fixture):
    files = PASSING

    def backend(self, *args):
        return subprocess.run([sys.executable, str(VERIFY / "backend"), "--root",
                               str(self.root), "--artifacts", str(self.base / "art"),
                               "--scratch", str(self.base), *args],
                              capture_output=True, text=True, env=clean_env(), timeout=300)

    def record(self, r):
        line = [ln for ln in r.stdout.splitlines() if ln.startswith("record: ")][-1]
        return json.loads(Path(line[len("record: "):]).read_text())

    def test_a_timed_run_keeps_the_file_and_a_summary_bound_to_the_candidate(self):
        r = self.backend()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        data = self.record(r)
        t = data["timing"]
        self.assertTrue(t["collected"])
        self.assertEqual(2, t["counts"]["tests"])
        self.assertEqual(2, data["tests"]["ran"])
        self.assertIn("elapsed", t["seconds"])
        self.assertEqual("tests.test_a.T", t["slowestClasses"][0]["class"])
        self.assertTrue(Path(data["artifacts"]["timings"]).is_file())
        self.assertEqual(64, len(t["collectorSha256"]))
        self.assertIn("-m", data["plainCommand"])
        self.assertIn(str(VERIFY / "unittesttiming.py"), data["command"]["argv"])
        # the candidate the timings are about is named in the same record
        self.assertTrue(data["candidate"]["revision"])
        # and the collector wrote nothing into it
        self.assertEqual("", subprocess.run(["git", "-C", str(self.root), "status",
                                             "--porcelain"], capture_output=True,
                                            text=True).stdout)

    def test_no_timing_runs_the_plain_command(self):
        r = self.backend("--no-timing")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        data = self.record(r)
        self.assertEqual({"collected": False, "why": "--no-timing"}, data["timing"])
        self.assertEqual(data["plainCommand"], data["command"]["argv"])
        self.assertNotIn("timings", data["artifacts"])

    def test_a_skip_still_refuses_and_a_failure_still_fails_under_timing(self):
        (self.root / "tests" / "test_s.py").write_text(textwrap.dedent("""
            import unittest
            class S(unittest.TestCase):
                @unittest.skip("needs node")
                def test_s(self): pass
        """))
        r = self.backend()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("skipped", self.record(r)["reason"])
        (self.root / "tests" / "test_s.py").write_text(textwrap.dedent("""
            import unittest
            class S(unittest.TestCase):
                def test_s(self): self.fail("no")
        """))
        r = self.backend()
        self.assertEqual(1, r.returncode, r.stdout)
        data = self.record(r)
        self.assertIn("tests.test_s.S", data["reason"])
        self.assertTrue(data["timing"]["collected"])


class TimingReport(Fixture):
    files = PASSING

    def report(self, *args):
        return subprocess.run([sys.executable, str(VERIFY / "timing-report"), *args],
                              capture_output=True, text=True, timeout=60)

    def test_ranks_one_run(self):
        r, _ = self.timed()
        rep = self.report(str(self.base / "timings.json"))
        self.assertEqual(0, rep.returncode, rep.stderr)
        self.assertIn("slowest classes", rep.stdout)
        self.assertIn("tests.test_a.T", rep.stdout)

    def test_compares_runs_of_the_same_tests_and_refuses_different_ones(self):
        a, b = self.base / "a.json", self.base / "b.json"
        self.timed(out=a)
        self.timed(out=b)
        rep = self.report("--json", "--baseline", str(a), "--candidate", str(b))
        self.assertEqual(0, rep.returncode, rep.stdout + rep.stderr)
        doc = json.loads(rep.stdout)
        self.assertEqual(2, doc["tests"])
        self.assertEqual([], doc["refused"])
        (self.root / "tests" / "test_c.py").write_text(
            "import unittest\nclass C(unittest.TestCase):\n    def test_c(self): pass\n")
        c = self.base / "c.json"
        self.timed(out=c)
        rep = self.report("--baseline", str(a), "--candidate", str(c))
        self.assertEqual(2, rep.returncode)
        self.assertIn("different set of tests", rep.stderr)

    def test_a_failing_run_is_not_compared(self):
        a, f = self.base / "a.json", self.base / "f.json"
        self.timed(out=a)
        (self.root / "tests" / "test_a.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_one(self): self.fail('x')\n    def test_two(self): pass\n")
        self.timed(out=f)
        rep = self.report("--baseline", str(a), "--candidate", str(f))
        self.assertEqual(2, rep.returncode)
        self.assertIn("not a full pass", rep.stderr)


if __name__ == "__main__":
    unittest.main()
