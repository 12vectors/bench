"""The verify/ entrypoints are how a project check asks "does this
candidate checkout work?" without the asking run being the one that
judges. So what is tested here is the harness, not the product: that the
candidate is the root it was pointed at (never wherever the script is
installed), that inherited board and agent settings never reach the
child, that a pass is a pass of something real, and that the three ways
a check can go wrong — a test failed, nothing could be run, nothing was
found — come back as different exit statuses.

Every case runs an entrypoint as a real process against a disposable
fixture candidate, so each positive has its deliberate negative beside it.

    python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "verify"
sys.path.insert(0, str(VERIFY))

import benchverify  # noqa: E402

PASS, FAIL, SETUP = 0, 1, 2


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"})


class Fixture(unittest.TestCase):
    """A disposable candidate: a git checkout shaped like bench (it has
    manager/core/board.py), with a tests/ directory each case writes."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="bv-test-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "candidate"
        (self.root / "manager" / "core").mkdir(parents=True)
        (self.root / "manager" / "core" / "board.py").write_text("# fixture\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("")
        self.artifacts = self.base / "artifacts"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)],
                       check=True, capture_output=True)

    def commit(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "fixture")

    def write_test(self, name, body):
        (self.root / "tests" / name).write_text(textwrap.dedent(body))

    def run_entry(self, entry, *args, cwd=None, env_extra=None, root_arg=True,
                  dirs=True):
        # this suite may itself be run by bench, which sets BENCH_VERIFY
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BENCH_VERIFY")}
        env.update(env_extra or {})
        argv = [sys.executable, str(VERIFY / entry)]
        if root_arg:
            argv += ["--root", str(self.root)]
        if dirs:
            argv += ["--artifacts", str(self.artifacts), "--scratch", str(self.base)]
        argv += list(args)
        r = subprocess.run(argv, cwd=str(cwd or self.base), env=env,
                           capture_output=True, text=True, timeout=300)
        # every run gets a fresh directory under --artifacts, and says which
        lines = [ln[len("record: "):] for ln in r.stdout.splitlines()
                 if ln.startswith("record: ")]
        result = Path(lines[-1]) if lines else None
        data = json.loads(result.read_text()) if result and result.is_file() else None
        return r, data


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is dead for this purpose; it only waits to be reaped
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


class Freshness(Fixture):
    """A record is about this run. Nothing a previous run left behind, and
    nothing the run itself changed in the candidate, may stand in a pass."""

    def test_each_run_gets_its_own_artifacts_directory(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        r1, one = self.run_entry("backend")
        r2, two = self.run_entry("backend")
        self.assertEqual(PASS, r2.returncode, r2.stdout + r2.stderr)
        self.assertNotEqual(one["artifacts"]["dir"], two["artifacts"]["dir"])
        for d in (one, two):
            self.assertTrue(Path(d["artifacts"]["dir"]).is_relative_to(self.artifacts.resolve()))

    def test_a_docs_build_cannot_pass_on_a_previous_runs_output(self):
        (self.root / "site").mkdir()
        (self.root / "site" / "pages.json").write_text(json.dumps({"pages": [{"path": "/"}]}))
        build = self.root / "site" / "build.py"
        build.write_text("import sys\nfrom pathlib import Path\n"
                         "o = Path(sys.argv[sys.argv.index('--out') + 1])\n"
                         "o.mkdir(parents=True, exist_ok=True)\n"
                         "(o / 'index.html').write_text('old build')\n")
        self.commit()
        r, data = self.run_entry("docs", "--require-module", "json")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        build.write_text("# deliberately writes nothing and exits zero\n")
        self.commit()
        r, data = self.run_entry("docs", "--require-module", "json")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("wrote no output", data["reason"])

    def test_a_suite_that_changes_the_candidate_is_not_a_pass(self):
        (self.root / "tracked.txt").write_text("before\n")
        self.write_test("test_a.py", """
            import unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_rewrites(self):
                    Path("tracked.txt").write_text("after\\n")
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("changed during the run", data["reason"])
        self.assertNotEqual(data["candidate"]["sourceDigest"],
                            data["candidate"]["after"]["sourceDigest"])

    def test_a_clean_run_reads_the_same_source_afterwards(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(data["candidate"]["sourceDigest"],
                         data["candidate"]["after"]["sourceDigest"])
        self.assertFalse(data["candidate"]["after"]["dirty"])

    def test_artifacts_inside_the_candidate_are_refused(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        self.artifacts = self.root / "out"
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertFalse((self.root / "out").exists())

    def test_an_unusable_scratch_writes_no_record_into_the_working_directory(self):
        blocker = self.base / "a-file"
        blocker.write_text("not a directory\n")
        self.commit()
        env = {k: v for k, v in os.environ.items() if not k.startswith("BENCH_VERIFY_")}
        r = subprocess.run([sys.executable, str(VERIFY / "backend"),
                            "--scratch", str(blocker / "sub")],
                           cwd=str(self.root), env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertFalse((self.root / "result.json").exists())
        self.assertIn("no record was written", r.stderr)

    def test_stale_or_empty_artifacts_are_refused(self):
        since = benchverify.time.time()
        old = self.base / "old.png"
        old.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
        os.utime(old, (since - 60, since - 60))
        empty = self.base / "empty.json"
        empty.write_text("")
        fresh = self.base / "fresh.json"
        fresh.write_text("{}")
        with self.assertRaises(benchverify.SetupError) as c:
            benchverify.require_fresh([old], since)
        self.assertIn("before this run", str(c.exception))
        with self.assertRaises(benchverify.SetupError):
            benchverify.require_fresh([empty], since)
        hashes = benchverify.require_fresh([fresh], since)
        self.assertEqual(benchverify.sha256_file(fresh), hashes[str(fresh)]["sha256"])


class CandidateRoot(Fixture):
    def test_root_defaults_to_the_working_directory_not_the_script(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend", cwd=self.root, root_arg=False)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(str(self.root.resolve()), data["candidate"]["root"])
        self.assertEqual("cwd", data["candidate"]["selectedBy"])
        self.assertFalse(data["runner"]["insideCandidate"])

    def test_bench_verify_root_beats_the_working_directory(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend", cwd=self.base, root_arg=False,
                                 env_extra={"BENCH_VERIFY_ROOT": str(self.root)})
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("BENCH_VERIFY_ROOT", data["candidate"]["selectedBy"])

    def test_a_root_that_is_not_bench_is_a_setup_error(self):
        (self.root / "manager" / "core" / "board.py").unlink()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual("setup-error", data["outcome"])
        self.assertIn("manager/core/board.py", data["reason"])

    def test_revision_and_uncommitted_state_are_recorded(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        r, clean = self.run_entry("backend")
        self.assertEqual(head, clean["candidate"]["revision"])
        self.assertFalse(clean["candidate"]["dirty"])
        (self.root / "tests" / "new_untracked.txt").write_text("x\n")
        r, dirty = self.run_entry("backend")
        self.assertTrue(dirty["candidate"]["dirty"])
        self.assertNotEqual(clean["candidate"]["sourceDigest"],
                            dirty["candidate"]["sourceDigest"])

    def test_a_root_outside_git_is_run_and_says_it_has_no_revision(self):
        # a release artifact or a vendored copy: no checkout of its own
        shutil.rmtree(self.root / ".git")
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        r, data = self.run_entry("backend")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertIsNone(data["candidate"]["revision"])
        self.assertIn("not a git", data["candidate"]["revisionNote"])


    def test_a_vendored_root_does_not_borrow_the_host_revision(self):
        # .task-manager/ inside a host project: the host's HEAD is not the
        # candidate's revision, and the record must not claim it is
        shutil.rmtree(self.root / ".git")
        host = self.base / "host"
        host.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(host)],
                       check=True, capture_output=True)
        vendored = host / ".task-manager"
        shutil.move(str(self.root), str(vendored))
        self.root = vendored
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        r, data = self.run_entry("backend")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertIsNone(data["candidate"]["revision"])
        self.assertIn("vendored", data["candidate"]["revisionNote"])
        self.assertTrue(data["candidate"]["sourceDigest"].startswith("tree:"))


class Backend(Fixture):
    def test_a_passing_suite_passes_and_counts_its_tests(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("passed", data["outcome"])
        self.assertEqual(2, data["tests"]["ran"])
        log = Path(data["artifacts"]["log"])
        self.assertIn("test_two", log.read_text())
        self.assertEqual(str(self.root.resolve()), data["command"]["cwd"])
        self.assertEqual(["-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
                         data["command"]["argv"][1:])

    def test_a_failing_test_is_an_assertion_failure_not_a_setup_error(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_bad(self): self.assertEqual(1, 2)
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual("failed", data["outcome"])
        self.assertEqual(1, data["tests"]["failures"])

    def test_an_error_inside_a_test_is_still_a_failure(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_raises(self): raise RuntimeError("product bug")
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, data["tests"]["errors"])

    def test_a_test_module_that_cannot_import_is_a_setup_error(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.write_test("test_b.py", """
            import bench_verify_no_such_dependency
            import unittest
            class T(unittest.TestCase):
                def test_never(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("tests.test_b", data["reason"])
        self.assertIn("bench_verify_no_such_dependency", data["reason"])
        self.assertEqual(["tests.test_b"], data["tests"]["importFailures"])

    def test_a_suite_whose_every_test_skipped_is_a_setup_error(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                @unittest.skip("needs a service")
                def test_one(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("skipped", data["reason"])

    def test_one_skip_among_passes_is_a_setup_error_naming_why(self):
        # what an installed service PATH without node produced: "OK
        # (skipped=121)", read as a pass of the whole suite
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ran(self): pass
                def test_needs_node(self):
                    raise unittest.SkipTest("node not available — chip behaviour unrun")
        """)
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("1 of 2 tests skipped", data["reason"])
        self.assertIn("node not available", data["reason"])
        self.assertEqual(1, data["tests"]["skipped"])
        self.assertIn("node not available — chip behaviour unrun",
                      data["tests"]["skipReasons"][0])
        log = Path(data["artifacts"]["log"]).read_text()
        self.assertIn("skipped 'node not available", log)
        # the plain command a project already runs is unchanged by this
        plain = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                                "tests", "-t", "."], cwd=self.root,
                               capture_output=True, text=True)
        self.assertEqual(0, plain.returncode, plain.stderr)
        self.assertIn("skipped=1", plain.stderr)

    def test_a_timeout_takes_the_suites_descendants_down_too(self):
        pidfile = self.base / "grandchild.pid"
        self.write_test("test_a.py", f"""
            import subprocess, time, unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_hangs(self):
                    p = subprocess.Popen(["sleep", "300"])
                    Path({str(pidfile)!r}).write_text(str(p.pid))
                    time.sleep(300)
        """)
        self.commit()
        r, data = self.run_entry("backend", "--timeout", "3")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("did not finish within 3", data["reason"])
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        deadline = benchverify.time.time() + 5
        while alive(pid) and benchverify.time.time() < deadline:
            benchverify.time.sleep(0.1)
        self.assertFalse(alive(pid), f"grandchild {pid} outlived the timeout")

    def test_zero_discovered_tests_is_a_setup_error(self):
        self.commit()
        r, data = self.run_entry("backend")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual("setup-error", data["outcome"])
        self.assertIn("zero tests", data["reason"])

    def test_inherited_board_and_agent_settings_never_reach_the_suite(self):
        self.write_test("test_env.py", """
            import os, unittest
            class T(unittest.TestCase):
                def test_env(self):
                    leaked = sorted(k for k in os.environ
                                    if k.startswith(("BOARD_", "AGENT_", "BENCH_")))
                    self.assertEqual([], leaked)
                    self.assertNotEqual(os.environ.get("HOME"), os.environ["ORIG_HOME_SEEN"])
                    self.assertTrue(os.environ["TMPDIR"].startswith(os.environ["HOME"].rsplit("/", 1)[0]))
                    import subprocess
                    name = subprocess.run(["git", "config", "user.name"],
                                          capture_output=True, text=True).stdout.strip()
                    self.assertEqual("bench-verify", name)
        """)
        # the fixture test needs to know the parent's HOME to prove it changed
        self.commit()
        r, data = self.run_entry("backend", env_extra={
            "BOARD_PORT": "1", "AGENT_PROFILE": "{}", "BOARD_AGENT_ID": "x",
            "BENCH_VERIFY_ROOT": "/nowhere", "ORIG_HOME_SEEN": os.environ.get("HOME", "")})
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr +
                         Path(data["artifacts"]["log"]).read_text())
        stripped = data["environment"]["stripped"]
        for name in ("BOARD_PORT", "AGENT_PROFILE", "BOARD_AGENT_ID"):
            self.assertIn(name, stripped)
        self.assertIn("not an OS sandbox", data["environment"]["isolation"])


class Docs(Fixture):
    def site(self, build_body):
        (self.root / "site").mkdir()
        (self.root / "site" / "pages.json").write_text(json.dumps(
            {"pages": [{"path": "/", "source": None}, {"path": "/guide/", "from": "x"}]}))
        (self.root / "site" / "build.py").write_text(textwrap.dedent(build_body))

    WRITES_BOTH = """
        import sys
        from pathlib import Path
        out = Path(sys.argv[sys.argv.index("--out") + 1])
        (out / "guide").mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html>home</html>")
        (out / "guide" / "index.html").write_text("<html>guide</html>")
    """

    def test_a_build_that_writes_every_route_passes(self):
        self.site(self.WRITES_BOTH)
        self.commit()
        r, data = self.run_entry("docs", "--require-module", "json")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(["/", "/guide/"], data["routes"]["written"])
        self.assertTrue(Path(data["artifacts"]["site"], "guide", "index.html").is_file())

    def test_a_route_the_build_did_not_write_is_a_setup_error(self):
        self.site("""
            import sys
            from pathlib import Path
            out = Path(sys.argv[sys.argv.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_text("<html>home</html>")
        """)
        self.commit()
        r, data = self.run_entry("docs", "--require-module", "json")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("/guide/", data["reason"])

    def test_a_build_that_exits_nonzero_is_a_failure(self):
        self.site("import sys\nprint('heading not found', file=sys.stderr)\nsys.exit(1)\n")
        self.commit()
        r, data = self.run_entry("docs", "--require-module", "json")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertIn("heading not found", Path(data["artifacts"]["log"]).read_text())

    def test_a_missing_markdown_parser_is_a_setup_error(self):
        self.site(self.WRITES_BOTH)
        self.commit()
        r, data = self.run_entry("docs", "--require-module",
                                 "bench_verify_no_such_module")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("site/requirements.txt", data["reason"])


class Classification(unittest.TestCase):
    """The pieces of the browser runner that do not need a browser: what
    a measurement means, and what an unusable browser is called."""

    def test_horizontal_overflow_is_reported(self):
        problems = benchverify.layout_problems({
            "viewport": {"width": 1024, "height": 700},
            "scrollWidth": 1100, "boxes": {"#views": [0, 0, 300, 40]}})
        self.assertTrue(any("overflow" in p for p in problems), problems)

    def test_a_box_outside_the_viewport_is_reported(self):
        problems = benchverify.layout_problems({
            "viewport": {"width": 1024, "height": 700},
            "scrollWidth": 1024, "boxes": {"#doctorchip": [1000, 4, 80, 24]}})
        self.assertTrue(any("#doctorchip" in p for p in problems), problems)

    def test_a_layout_inside_the_viewport_has_no_problems(self):
        self.assertEqual([], benchverify.layout_problems({
            "viewport": {"width": 1024, "height": 700},
            "scrollWidth": 1024, "boxes": {"#views": [10, 4, 300, 30]}}))

    def test_a_missing_or_empty_box_is_a_problem_not_a_pass(self):
        problems = benchverify.layout_problems({
            "viewport": {"width": 1024, "height": 700},
            "scrollWidth": 1024, "boxes": {"#views": None, "#board": [0, 0, 0, 0]}})
        self.assertEqual(2, len(problems), problems)

    def test_a_sandbox_failure_names_the_sandbox_and_refuses(self):
        why = benchverify.browser_launch_blocker(
            "[123:123:FATAL:zygote_host_impl_linux.cc] No usable sandbox! ...")
        self.assertIn("sandbox", why)
        self.assertIn("will not disable", why)

    def test_the_sandbox_is_never_turned_off(self):
        argv = benchverify.browser_argv("/x/chrome", Path("/p"))
        self.assertFalse([a for a in argv if "sandbox" in a], argv)

    def test_a_disabling_environment_is_stripped(self):
        work = Path(tempfile.mkdtemp(prefix="bv-env-"))
        self.addCleanup(shutil.rmtree, work, True)
        env = benchverify.child_env(work, {"PATH": "/bin", "CHROME_DEVEL_SANDBOX": "",
                                           "PLAYWRIGHT_CHROMIUM_NO_SANDBOX": "1"})[0]
        self.assertNotIn("CHROME_DEVEL_SANDBOX", env)
        self.assertNotIn("PLAYWRIGHT_CHROMIUM_NO_SANDBOX", env)


class BrowserSetup(Fixture):
    def test_no_browser_is_a_setup_error_not_a_skip(self):
        r, data = self.run_entry("browser", "--cases", str(VERIFY / "browser_cases"),
                                 env_extra={"BENCH_VERIFY_BROWSER":
                                            str(self.base / "no-such-browser")})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual("setup-error", data["outcome"])
        self.assertIn("no-such-browser", data["reason"])

    def fake_browser(self, message):
        exe = self.base / "fake-chrome"
        exe.write_text(f"#!/bin/sh\necho {message!r} >&2\nexit 1\n")
        exe.chmod(0o755)
        return exe

    def test_a_browser_that_will_not_start_still_records_its_exact_argv(self):
        exe = self.fake_browser("FATAL: No usable sandbox!")
        self.root = REPO       # a real core, so the disposable backend serves
        r, data = self.run_entry("browser", "--cases", str(VERIFY / "browser_cases"),
                                 env_extra={"BENCH_VERIFY_BROWSER": str(exe)})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("sandbox", data["reason"])
        argv = data["browser"]["argv"]
        self.assertEqual(str(exe), argv[0])
        self.assertIn("--remote-debugging-pipe", argv)
        self.assertFalse([a for a in argv if "sandbox" in a], argv)

    def test_zero_discovered_browser_tests_is_a_setup_error(self):
        empty = self.base / "empty-cases"
        empty.mkdir()
        r, data = self.run_entry("browser", "--cases", str(empty), env_extra={
            "BENCH_VERIFY_BROWSER": sys.executable})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("zero", data["reason"])


class Preflight(Fixture):
    """The preparation step a profile declares before a check, so bench
    itself records `prepare-failed` — not a test failure — when the
    machine cannot run the check at all."""

    def test_an_importable_dependency_prepares(self):
        self.commit()
        r, data = self.run_entry("preflight", "docs", "--require-module", "json")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertTrue(data["dependency"]["importable"])

    def test_a_missing_dependency_does_not_prepare(self):
        self.commit()
        r, data = self.run_entry("preflight", "docs", "--require-module",
                                 "bench_verify_no_such_module")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("bench_verify_no_such_module", data["reason"])

    def test_a_browser_that_cannot_start_its_sandbox_does_not_prepare(self):
        exe = self.base / "fake-chrome"
        exe.write_text("#!/bin/sh\necho 'No usable sandbox!' >&2\nexit 1\n")
        exe.chmod(0o755)
        self.commit()
        r, data = self.run_entry("preflight", "browser",
                                 env_extra={"BENCH_VERIFY_BROWSER": str(exe)})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("sandbox", data["reason"])
        self.assertEqual(str(exe), data["browser"]["argv"][0])

    def test_no_browser_does_not_prepare(self):
        self.commit()
        r, data = self.run_entry("preflight", "browser", env_extra={
            "BENCH_VERIFY_BROWSER": str(self.base / "absent")})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)

    def fake_node(self):
        node = self.base / "fake-node"
        node.write_text("#!/bin/sh\necho v22.0.0-fixture\n")
        node.chmod(0o755)
        return node

    def test_backend_prepares_when_node_runs_and_the_module_imports(self):
        self.commit()
        r, data = self.run_entry("preflight", "backend", "--node", str(self.fake_node()),
                                 "--require-module", "json")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("v22.0.0-fixture", data["node"]["version"])
        self.assertTrue(data["dependency"]["importable"])

    def test_backend_without_node_does_not_prepare(self):
        self.commit()
        r, data = self.run_entry("preflight", "backend", "--node",
                                 "bench-verify-no-such-node", "--require-module", "json")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("node", data["reason"])
        self.assertIn("skip", data["reason"])

    def test_backend_without_markdown_it_does_not_prepare(self):
        self.commit()
        r, data = self.run_entry("preflight", "backend", "--node", str(self.fake_node()),
                                 "--require-module", "bench_verify_no_such_module")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("bench_verify_no_such_module", data["reason"])


class UnderBench(Fixture):
    """Run by bench's own verify.request: BENCH_VERIFY names the evidence,
    HOME is the per-root home bench keeps, TMPDIR is a directory bench
    removes the moment the check ends. A record that lived under TMPDIR
    was a path printed about a file that no longer existed."""

    def bench_env(self, evidence="ev-verification-0123456789ab"):
        home, tmp = self.base / "bench-home", self.base / "bench-tmp"
        home.mkdir()
        tmp.mkdir()
        return home, tmp, {"BENCH_VERIFY": evidence, "HOME": str(home),
                           "TMPDIR": str(tmp)}

    def passing(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
        """)
        self.commit()

    def test_the_record_and_log_outlive_bench_removing_tmpdir(self):
        self.passing()
        home, tmp, env = self.bench_env()
        r, data = self.run_entry("backend", env_extra=env, dirs=False)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        shutil.rmtree(tmp)  # what verify._cleanup does after the check
        record = Path(r.stdout.split("record: ")[-1].strip())
        self.assertTrue(record.is_file(), record)
        self.assertTrue(record.is_relative_to(
            home / "bench-verification-artifacts" / "ev-verification-0123456789ab"))
        data = json.loads(record.read_text())
        self.assertTrue(Path(data["artifacts"]["log"]).is_file())
        self.assertEqual("bench-home", data["artifacts"]["kept"])
        self.assertEqual("ev-verification-0123456789ab", data["artifacts"]["evidence"])

    def test_explicit_artifacts_beat_the_bench_home(self):
        self.passing()
        home, tmp, env = self.bench_env()
        r, data = self.run_entry("backend", "--artifacts", str(self.artifacts),
                                 env_extra=env, dirs=False)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertTrue(Path(data["artifacts"]["dir"]).is_relative_to(self.artifacts.resolve()))
        self.assertFalse((home / "bench-verification-artifacts").exists())

    def test_an_evidence_id_cannot_climb_out_of_the_home(self):
        self.passing()
        home, tmp, env = self.bench_env(evidence="../../escape")
        r, data = self.run_entry("backend", env_extra=env, dirs=False)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertTrue(Path(data["artifacts"]["dir"]).resolve().is_relative_to(
            (home / "bench-verification-artifacts").resolve()))

    def test_outside_bench_the_default_is_unchanged(self):
        self.passing()
        r, data = self.run_entry("backend", "--scratch", str(self.base), dirs=False)
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual(Path(data["work"]) / "artifacts", Path(data["artifacts"]["dir"]))
        self.assertEqual("work-directory", data["artifacts"]["kept"])


if __name__ == "__main__":
    unittest.main()
