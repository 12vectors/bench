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
               if not k.startswith("BENCH_VERIFY") and k != "PW_WS"}
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
        # the plain command it stands for, and the same discovery under the
        # timing collector (task 163), which runs unittest's own runner
        self.assertEqual(["-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
                         data["plainCommand"][1:])
        argv = data["command"]["argv"]
        self.assertEqual(str(VERIFY / "unittesttiming.py"), argv[1])
        self.assertEqual(["discover", "-s", "tests", "-t", ".", "-v"], argv[4:])
        r, data = self.run_entry("backend", "--no-timing")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
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


class BytecodeCache(unittest.TestCase):
    """Task 178: a child compiles once into a cache the run owns, and the
    checkout it judges still gets no `__pycache__`."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="bv-pyc-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.tree = self.work / "checkout"
        (self.tree / "pkg").mkdir(parents=True)
        (self.tree / "pkg" / "__init__.py").write_text("")
        (self.tree / "pkg" / "mod.py").write_text("VALUE = 7\n")

    def env(self, parent=None):
        return benchverify.child_env(self.work / "run", parent or {"PATH": os.environ["PATH"]})

    def test_the_cache_is_the_runs_own_and_bytecode_is_written(self):
        env, stripped = self.env({"PATH": os.environ["PATH"],
                                  "PYTHONDONTWRITEBYTECODE": "1",
                                  "PYTHONPYCACHEPREFIX": str(self.tree)})
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", env)
        prefix = Path(env["PYTHONPYCACHEPREFIX"])
        self.assertEqual(prefix, self.work / "run" / "cache" / "pycache")
        self.assertIn("PYTHONDONTWRITEBYTECODE", stripped)
        self.assertIn("PYTHONPYCACHEPREFIX", stripped)
        self.assertIn("PYTHONPYCACHEPREFIX", benchverify.DIRECTED)

    def test_a_child_import_compiles_into_the_prefix_never_into_the_checkout(self):
        env, _ = self.env()
        for _ in range(2):
            done = subprocess.run([sys.executable, "-c", "import pkg.mod as m; print(m.VALUE)"],
                                  cwd=self.tree, env=env, capture_output=True, text=True)
            self.assertEqual((done.returncode, done.stdout.strip()), (0, "7"), done.stderr)
        self.assertEqual([p for p in self.tree.rglob("*") if "pycache" in p.name
                          or p.suffix == ".pyc"], [])
        cached = list(Path(env["PYTHONPYCACHEPREFIX"]).rglob("mod.*.pyc"))
        self.assertEqual(len(cached), 1, cached)


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
        # `sh`, not the default lsof, so this holds on a machine without lsof
        r, data = self.run_entry("preflight", "backend", "--node", str(self.fake_node()),
                                 "--require-module", "json", "--tool", "sh")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertEqual("v22.0.0-fixture", data["node"]["version"])
        self.assertTrue(data["dependency"]["importable"])
        self.assertEqual(["sh"], list(data["tools"]))

    def test_backend_without_a_needed_tool_does_not_prepare(self):
        # task 180: the stop.sh tests skip without lsof, and verify/backend
        # refuses a skip — so the preparation says which tool, first
        self.commit()
        r, data = self.run_entry("preflight", "backend", "--node", str(self.fake_node()),
                                 "--require-module", "json",
                                 "--tool", "sh", "--tool", "bench-verify-no-such-tool")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("bench-verify-no-such-tool", data["reason"])
        self.assertIn("skip", data["reason"])
        self.assertIsNone(data["tools"]["bench-verify-no-such-tool"])

    def test_backend_needs_lsof_by_default(self):
        self.commit()
        only_git = self.base / "only-git"
        only_git.mkdir()
        os.symlink(shutil.which("git"), only_git / "git")
        r, data = self.run_entry("preflight", "backend", "--node", str(self.fake_node()),
                                 "--require-module", "json",
                                 env_extra={"PATH": str(only_git)})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual({"lsof": None}, data["tools"])
        self.assertIn("lsof", data["reason"])

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
                                 "--require-module", "bench_verify_no_such_module",
                                 "--tool", "sh")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("bench_verify_no_such_module", data["reason"])


def load_backend():
    """verify/backend as a module (it has no .py suffix); its main is
    guarded, so loading it runs nothing."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("verify_backend_under_test",
                                                  str(VERIFY / "backend"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class RequiredTiming(Fixture):
    """Task 180: the CI leg's contract. A pass of the whole suite there
    rests on timing evidence this run wrote about exactly the tests that
    ran; anything less is never read as a complete pass."""

    def test_a_timed_pass_records_its_exact_test_set(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend", "--require-timing")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        self.assertTrue(data["timing"]["collected"])
        self.assertEqual(2, data["testSet"]["count"])
        ids = sorted(t["id"] for t in json.loads(
            Path(data["artifacts"]["timings"]).read_text())["tests"])
        self.assertEqual(["tests.test_a.T.test_one", "tests.test_a.T.test_two"], ids)
        import hashlib
        self.assertEqual(hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                         data["testSet"]["sha256"])

    def test_it_cannot_be_combined_with_a_focused_or_untimed_run(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self): pass
        """)
        self.commit()
        for extra in (["--select", "tests.test_a"], ["--no-timing"]):
            r, data = self.run_entry("backend", "--require-timing", *extra)
            self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
            self.assertIn("--require-timing", data["reason"])

    def test_skips_and_zero_discovery_still_refuse_first(self):
        self.commit()
        r, data = self.run_entry("backend", "--require-timing")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("zero tests", data["reason"])
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ran(self): pass
                @unittest.skip("needs node")
                def test_skipped(self): pass
        """)
        self.commit()
        r, data = self.run_entry("backend", "--require-timing")
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("skipped", data["reason"])

    def test_a_failing_run_still_keeps_fresh_timing_and_log(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_bad(self): self.assertEqual(1, 2)
        """)
        self.commit()
        r, data = self.run_entry("backend", "--require-timing")
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertIn("tests.test_a.T.test_bad", Path(data["artifacts"]["log"]).read_text())
        timings = json.loads(Path(data["artifacts"]["timings"]).read_text())
        self.assertEqual(["failure"], [t["outcome"] for t in timings["tests"]])
        self.assertEqual(1, data["testSet"]["count"])


class TestSet(unittest.TestCase):
    """verify/backend's test_set, on timings files made by hand: what a
    missing, stale, unreadable or miscounted file reads as."""

    def setUp(self):
        self.backend = load_backend()
        self.dir = Path(tempfile.mkdtemp(prefix="bv-testset-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "timings.json"

    def write(self, ids):
        self.path.write_text(json.dumps({"tests": [{"id": i} for i in ids]}))

    def test_a_fresh_file_naming_the_tests_that_ran_has_no_problems(self):
        self.write(["t.b", "t.a"])
        got = self.backend.test_set(self.path, benchverify.time.time() - 5, 2)
        self.assertEqual([], got["problems"])
        self.assertEqual(2, got["count"])

    def test_a_missing_file_is_a_problem(self):
        got = self.backend.test_set(self.path, benchverify.time.time(), 2)
        self.assertTrue(any("missing" in p for p in got["problems"]), got)
        self.assertIsNone(got["count"])

    def test_a_file_older_than_the_run_is_a_problem(self):
        self.write(["t.a"])
        old = benchverify.time.time() - 3600
        os.utime(self.path, (old, old))
        got = self.backend.test_set(self.path, benchverify.time.time(), 1)
        self.assertTrue(any("before this run" in p for p in got["problems"]), got)

    def test_an_unreadable_file_is_a_problem(self):
        self.path.write_text("{not json")
        got = self.backend.test_set(self.path, None, 1)
        self.assertTrue(any("not readable" in p for p in got["problems"]), got)

    def test_a_count_that_disagrees_or_repeats_is_a_problem(self):
        self.write(["t.a"])
        got = self.backend.test_set(self.path, None, 3)
        self.assertTrue(any("names 1 tests but unittest says 3" in p
                            for p in got["problems"]), got)
        self.write(["t.a", "t.a"])
        got = self.backend.test_set(self.path, None, 2)
        self.assertTrue(any("more than once" in p for p in got["problems"]), got)

    def test_the_digest_does_not_depend_on_order(self):
        self.write(["t.b", "t.a"])
        one = self.backend.test_set(self.path, None, 2)["sha256"]
        self.write(["t.a", "t.b"])
        self.assertEqual(one, self.backend.test_set(self.path, None, 2)["sha256"])


class CiSummary(Fixture):
    """verify/ci-summary: the strict CI leg's attestation of one record,
    run here against records real verify/backend runs wrote."""

    def summarise(self, *args, env_extra=None):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GITHUB_", "BENCH_VERIFY"))}
        env.update(env_extra or {})
        out = self.base / "summary.md"
        r = subprocess.run([sys.executable, str(VERIFY / "ci-summary"),
                            str(self.artifacts), "--summary", str(out), *args],
                           env=env, capture_output=True, text=True, timeout=60)
        return r, (out.read_text() if out.exists() else "")

    def head(self):
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def python(self):
        return "{}.{}".format(*sys.version_info[:2])

    def passing(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self): pass
        """)
        self.commit()
        r, _ = self.run_entry("backend", "--require-timing")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)

    def test_a_complete_pass_of_the_expected_commit_and_python(self):
        self.passing()
        r, md = self.summarise("--expect-revision", self.head(),
                               "--expect-python", self.python())
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("complete pass", md)
        self.assertIn(self.head(), md)
        self.assertIn("1 ids, sha256", md)

    def test_another_commit_or_python_is_not_this_ones_pass(self):
        self.passing()
        r, md = self.summarise("--expect-revision", "0" * 40)
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("not the expected", md)
        r, md = self.summarise("--expect-python", "2.7")
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("not 2.7", md)

    def test_a_failed_suite_reads_failed_and_is_still_summarised(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_bad(self): self.assertEqual(1, 2)
        """)
        self.commit()
        self.run_entry("backend", "--require-timing")
        r, md = self.summarise("--expect-revision", self.head())
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("FAILED", md)
        self.assertIn("the suite failed", md)

    def test_skips_and_zero_discovery_are_not_a_complete_pass(self):
        self.commit()
        self.run_entry("backend", "--require-timing")
        r, md = self.summarise()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("NOT a complete pass", md)
        self.assertIn("zero tests", md)

    def test_a_plain_untimed_pass_is_not_a_complete_pass(self):
        self.write_test("test_a.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self): pass
        """)
        self.commit()
        r, _ = self.run_entry("backend", "--no-timing")
        self.assertEqual(PASS, r.returncode, r.stdout + r.stderr)
        r, md = self.summarise()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("timing not collected", md)

    def test_no_record_or_two_records_is_not_a_pass(self):
        r, md = self.summarise()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("does not exist", md)
        self.artifacts.mkdir()
        r, md = self.summarise()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("no run record", md)
        self.passing()
        self.run_entry("backend", "--require-timing")
        r, md = self.summarise()
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertIn("2 run records", md)

    def test_the_context_file_holds_only_allowlisted_identifiers(self):
        self.passing()
        r, _ = self.summarise("--write-context", env_extra={
            "GITHUB_SHA": "f" * 40, "CANDIDATE_SHA": self.head(),
            "PR_HEAD_SHA": self.head(), "PR_BASE_SHA": "e" * 40,
            "GITHUB_RUN_ID": "42",
            "GITHUB_TOKEN": "ghs_secretvalue", "SOME_PASSWORD": "hunter2"})
        self.assertEqual(0, r.returncode, r.stdout)
        ctx = json.loads((self.artifacts / "ci-context.json").read_text())
        # GitHub's merge commit and the PR head the run is about, side by
        # side, so the artifact says which commit it attests
        self.assertEqual("f" * 40, ctx["GITHUB_SHA"])
        self.assertEqual(self.head(), ctx["CANDIDATE_SHA"])
        self.assertEqual(self.head(), ctx["PR_HEAD_SHA"])
        self.assertEqual("e" * 40, ctx["PR_BASE_SHA"])
        self.assertEqual("42", ctx["GITHUB_RUN_ID"])
        text = json.dumps(ctx)
        self.assertNotIn("secretvalue", text)
        self.assertNotIn("hunter2", text)


class Workflow(unittest.TestCase):
    """.github/workflows/tests.yml, read as text (no YAML parser in the
    stdlib): one strict, timed leg whose outputs are kept on every path,
    and the plain compatibility leg beside it."""

    def setUp(self):
        self.text = (REPO / ".github" / "workflows" / "tests.yml").read_text()

    def test_both_supported_pythons_remain(self):
        self.assertIn('python-version: ["3.11", "3.13"]', self.text)
        self.assertIn('STRICT_PYTHON: "3.13"', self.text)
        self.assertIn("python3 -m unittest discover -s tests -v", self.text)
        self.assertIn("if: matrix.python-version != env.STRICT_PYTHON", self.text)

    def test_the_strict_leg_prepares_then_runs_the_strict_wrapper(self):
        order = ["actions/setup-node", "pip install -r site/requirements.txt",
                 "verify/preflight backend", "verify/backend --require-timing",
                 "verify/ci-summary", "actions/upload-artifact"]
        at = [self.text.index(step) for step in order]
        self.assertEqual(sorted(at), at)
        self.assertIn('--expect-revision "$CANDIDATE_SHA"', self.text)
        self.assertIn("--expect-python", self.text)

    def test_a_pull_request_run_is_about_the_prs_head_commit(self):
        # On a pull_request event $GITHUB_SHA is GitHub's merge commit,
        # which nothing else names: the candidate is the PR head, checked
        # out, expected, and naming the artifact.
        self.assertIn("CANDIDATE_SHA: ${{ github.event.pull_request.head.sha "
                      "|| github.sha }}", self.text)
        checkout = self.text[self.text.index("actions/checkout"):]
        self.assertIn("ref: ${{ env.CANDIDATE_SHA }}",
                      checkout[:checkout.index("- uses:")])
        self.assertNotIn('--expect-revision "$GITHUB_SHA"', self.text)
        upload = self.text[self.text.index("- name: Keep timing"):]
        self.assertIn("${{ env.CANDIDATE_SHA }}", upload)
        self.assertNotIn("${{ github.sha }}", upload)
        self.assertIn("PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
                      self.text)
        self.assertIn("PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
                      self.text)

    def test_the_wrapper_ends_a_hung_suite_inside_the_job_limit(self):
        # a run the runner kills writes no record; the wrapper's own
        # timeout must come first so a hung suite still keeps one
        job = int(self.text.split("timeout-minutes:")[1].split()[0])
        step = self.text[self.text.index("verify/backend --require-timing"):]
        wrapper = float(step.split("--timeout")[1].split()[0])
        self.assertLess(wrapper, job * 60)

    def test_outputs_are_kept_on_every_path_under_a_unique_name(self):
        upload = self.text[self.text.index("- name: Keep timing"):]
        self.assertIn("if: always()", upload)
        for part in ("${{ matrix.python-version }}", "${{ env.CANDIDATE_SHA }}",
                     "${{ github.run_id }}", "${{ github.run_attempt }}"):
            self.assertIn(part, upload)
        attest = self.text[self.text.index("- name: Attest"):]
        self.assertIn("if: always()", attest[:attest.index("run:")])
        # outputs go to the runner's temp, never into the checkout judged
        self.assertIn('--artifacts "$RUNNER_TEMP/bench-ci/backend"', self.text)

    def test_no_credential_reaches_the_checkout(self):
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("secrets.", self.text)


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


PLAYWRIGHT = VERIFY / "playwright"

# A stand-in for playwright-core with the one surface the adapter uses.
# It records what connect() was handed, so the test reads the adapter's
# real forwarding rather than a description of it.
FAKE_PLAYWRIGHT = """
const fs = require('fs'), path = require('path');
const out = path.join(__dirname, 'connect.json');
const page = {
  on() {}, async goto(url) { this.url = url; },
  async evaluate(expr) { if (expr === 'boom()') throw new Error('boom is not defined');
                         return {expr}; },
  async setViewportSize() {}, keyboard: {async press() {}},
  mouse: {async click() {}}, async screenshot() { return Buffer.from('png'); },
};
const context = {async newPage() { return page; },
                 async newCDPSession() { return {async send(m) { return {nodes: [], m}; }}; }};
const browser = {version() { return '999.0-fake'; }, async newContext(o) {
                   fs.writeFileSync(path.join(__dirname, 'context.json'), JSON.stringify(o));
                   return context; },
                 async close() {}};
exports.chromium = {async connect(ws, opts) {
  fs.writeFileSync(out, JSON.stringify({ws, opts})); return browser; }};
"""


class RemotePlaywright(Fixture):
    """The devbox's browser: Chromium in the official Playwright image,
    reached over the Playwright protocol (not raw CDP) at an explicit,
    loopback-only PW_WS. Without PW_WS nothing here changes."""

    def test_only_a_loopback_ws_endpoint_is_accepted(self):
        for ok in ("ws://127.0.0.1:39123/", "ws://localhost:39123/",
                   "ws://[::1]:39123/", "ws://127.0.0.1:39123"):
            self.assertEqual(ok, benchverify.validate_ws_endpoint(ok))
        for bad in ("ws://100.69.10.127:3000/", "ws://0.0.0.0:3000/",
                    "wss://example.com:443/", "http://127.0.0.1:3000/",
                    "ws://127.0.0.1/", "ws://u:p@127.0.0.1:3000/",
                    "ws://127.0.0.1:3000/?x=1", "ws://127.0.0.1:3000/#f",
                    "ws://127.0.0.1.evil.example:3000/", "", "ws://127.0.0.1:99999/"):
            with self.assertRaises(benchverify.SetupError, msg=bad):
                benchverify.validate_ws_endpoint(bad)

    SECRET = "s3cretTOKEN"
    LEAKY = ("ws://user:s3cretTOKEN@127.0.0.1:3000/", "ws://127.0.0.1:3000/?token=s3cretTOKEN",
             "ws://127.0.0.1:3000/#s3cretTOKEN", "ws://s3cretTOKEN.example:3000/",
             "ws://127.0.0.1:3000/s3cretTOKEN%00", "s3cretTOKEN://127.0.0.1:3000/",
             "ws://127.0.0.1:s3cretTOKEN/", "ws://[s3cretTOKEN/")

    def test_an_invalid_endpoint_is_never_echoed(self):
        for bad in self.LEAKY:
            with self.assertRaises(benchverify.SetupError, msg=bad) as cm:
                benchverify.validate_ws_endpoint(bad)
            self.assertNotIn(self.SECRET, str(cm.exception), bad)
            self.assertIn("PW_WS", str(cm.exception))

    def test_an_invalid_endpoint_reaches_no_record_or_output(self):
        self.root = REPO
        bad = "ws://user:s3cretTOKEN@127.0.0.1:3000/?token=s3cretTOKEN"
        r, data = self.run_entry("browser", "--cases", str(VERIFY / "browser_cases"),
                                 env_extra={"PW_WS": bad})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertNotIn(self.SECRET, r.stdout + r.stderr)
        self.assertNotIn(self.SECRET, json.dumps(data))
        self.assertNotIn("endpoint", data["browser"])

    def test_pw_ws_never_reaches_the_child(self):
        work = self.base / "w"
        env = benchverify.child_env(work, {"PATH": "/bin", "PW_WS": "ws://127.0.0.1:1/"})[0]
        self.assertNotIn("PW_WS", env)

    def test_an_endpoint_off_loopback_is_a_setup_error_before_any_browser(self):
        self.root = REPO
        r, data = self.run_entry("browser", "--cases", str(VERIFY / "browser_cases"),
                                 env_extra={"PW_WS": "ws://100.69.10.127:3000/"})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("loopback", data["reason"])
        self.assertEqual("remote-playwright", data["browser"]["mode"])

    def test_without_pw_ws_the_local_path_is_selected(self):
        r, data = self.run_entry("browser", "--cases", str(VERIFY / "browser_cases"),
                                 env_extra={"BENCH_VERIFY_BROWSER": str(self.base / "absent")})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertEqual("local-cdp", data["browser"]["mode"])

    def test_the_client_is_pinned_exactly(self):
        pkg = json.loads((PLAYWRIGHT / "package.json").read_text())
        self.assertEqual({"playwright-core": "1.62.0"}, pkg["dependencies"])
        lock = json.loads((PLAYWRIGHT / "package-lock.json").read_text())
        self.assertEqual("1.62.0", lock["packages"]["node_modules/playwright-core"]["version"])
        self.assertEqual("1.62.0", benchverify.playwright_pin())

    def test_the_adapter_argv_carries_the_endpoint_and_nothing_weakens_the_sandbox(self):
        argv = benchverify.remote_argv("/usr/bin/node", Path("/m"), "ws://127.0.0.1:1/")
        self.assertEqual("/usr/bin/node", argv[0])
        self.assertIn("ws://127.0.0.1:1/", argv)
        self.assertFalse([a for a in argv if "no-sandbox" in a], argv)

    def fake_modules(self, version="1.62.0"):
        mods = self.base / "node_modules"
        pc = mods / "playwright-core"
        pc.mkdir(parents=True)
        (pc / "package.json").write_text(json.dumps(
            {"name": "playwright-core", "version": version, "main": "index.js"}))
        (pc / "index.js").write_text(FAKE_PLAYWRIGHT)
        return mods, pc

    def remote(self, mods):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        return benchverify.RemoteBrowser(
            benchverify.remote_argv(node, mods, "ws://127.0.0.1:39123/"),
            {"PATH": os.environ.get("PATH", "/bin"), "HOME": str(self.base)},
            "http://127.0.0.1:8", self.base / "adapter.log")

    def test_the_adapter_forwards_the_sandbox_and_loopback_exposure(self):
        mods, pc = self.fake_modules()
        b = self.remote(mods)
        try:
            self.assertEqual("999.0-fake", b.version)
            self.assertEqual({"expr": "!!(1)"}, b.eval("!!(1)"))
            with self.assertRaises(benchverify.CheckFailed):
                b.eval("boom()")
        finally:
            b.close()
        seen = json.loads((pc / "connect.json").read_text())
        self.assertEqual("ws://127.0.0.1:39123/", seen["ws"])
        self.assertEqual("<loopback>", seen["opts"]["exposeNetwork"])
        launch = json.loads(seen["opts"]["headers"]["x-playwright-launch-options"])
        self.assertIs(True, launch["chromiumSandbox"])
        self.assertFalse([a for a in launch.get("args", []) if "sandbox" in a])
        ctx = json.loads((pc / "context.json").read_text())
        self.assertEqual(1, ctx["deviceScaleFactor"])
        self.assertEqual({"chromiumSandbox": True}, b.launch_options)

    def test_a_client_other_than_the_pin_is_refused(self):
        mods, _ = self.fake_modules("1.61.0")
        with self.assertRaises(benchverify.SetupError) as cm:
            self.remote(mods)
        self.assertIn("1.61.0", str(cm.exception))

    def test_missing_modules_are_a_setup_error_naming_the_install(self):
        with self.assertRaises(benchverify.SetupError) as cm:
            self.remote(self.base / "nowhere")
        self.assertIn("npm ci", str(cm.exception))

    def test_an_adapter_that_dies_is_a_setup_error_not_a_hang(self):
        fake = self.base / "dies.py"
        fake.write_text("import sys; print('{\"event\": \"ready\", \"version\": \"x\"}', "
                        "flush=True); sys.stdin.readline(); sys.exit(3)\n")
        b = benchverify.RemoteBrowser([sys.executable, str(fake)], dict(os.environ),
                                      "http://127.0.0.1:8", self.base / "a.log")
        with self.assertRaises(benchverify.SetupError):
            b.eval("1")
        b.close()

    HUNG = textwrap.dedent("""
        import json, subprocess, sys, time
        child = subprocess.Popen(["sleep", "300"])
        open(sys.argv[1], "w").write(str(child.pid))
        if sys.argv[2] == "ready":
            print(json.dumps({"event": "ready", "version": "x"}), flush=True)
        time.sleep(300)   # never answers, never reads its stdin
    """)

    def hung(self, mode):
        fake, pidfile = self.base / "hung.py", self.base / "child.pid"
        fake.write_text(self.HUNG)
        return [sys.executable, str(fake), str(pidfile), mode], pidfile

    def wait_for(self, path):
        for _ in range(100):
            if path.is_file() and path.read_text():
                return int(path.read_text())
            import time
            time.sleep(0.05)
        self.fail(f"{path} never written")

    def test_a_hung_operation_times_out_and_takes_its_children_with_it(self):
        import time
        argv, pidfile = self.hung("ready")
        b = benchverify.RemoteBrowser(argv, dict(os.environ), "http://127.0.0.1:8",
                                      self.base / "a.log")
        child = self.wait_for(pidfile)
        t = time.time()
        with self.assertRaises(benchverify.SetupError) as cm:
            b.request("eval", timeout=1, expression="1")
        self.assertIn("timed out", str(cm.exception))
        self.assertLess(time.time() - t, 5)
        t = time.time()
        b.close()
        self.assertLess(time.time() - t, 15)
        self.assertFalse(alive(b.proc.pid), "the adapter outlived close()")
        self.assertFalse(alive(child), "the adapter's child outlived close()")

    def test_an_adapter_that_never_connects_times_out_and_is_cleaned_up(self):
        argv, pidfile = self.hung("silent")
        with self.assertRaises(benchverify.SetupError) as cm:
            benchverify.RemoteBrowser(argv, dict(os.environ), "http://127.0.0.1:8",
                                      self.base / "a.log", ready_timeout=1)
        self.assertIn("timed out", str(cm.exception))
        self.assertFalse(alive(self.wait_for(pidfile)), "the adapter's child outlived it")

    FAILING_CASES = textwrap.dedent("""
        import unittest
        import benchverify as bv
        S = bv.SESSION
        def png(name):
            p = S["shots"] / f"{name}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"\\0" * 2000)
            S["required"].append(p)
        class T(unittest.TestCase):
            def test_a_holds(self):
                S["metrics"]["a"] = 1
                png("a")
            def test_b_fails_after_its_screenshot(self):
                png("b")
                self.assertEqual(1, 2, "deliberate")
    """)

    def test_a_failing_case_keeps_its_screenshots_hashes_and_backend_log(self):
        if not shutil.which("node"):
            self.skipTest("node not available")
        mods, _ = self.fake_modules()
        cases = self.base / "cases"
        cases.mkdir()
        (cases / "case_failing.py").write_text(self.FAILING_CASES)
        self.root = REPO
        r, data = self.run_entry("browser", "--cases", str(cases), env_extra={
            "PW_WS": "ws://127.0.0.1:39123/",
            "BENCH_VERIFY_PLAYWRIGHT_MODULES": str(mods)})
        self.assertEqual(FAIL, r.returncode, r.stdout + r.stderr)
        self.assertEqual("failed", data["outcome"])
        shutil.rmtree(data["work"], ignore_errors=True)   # what a runner's cleanup does
        art = Path(data["artifacts"]["dir"])
        shots = data["artifacts"]["screenshots"]
        self.assertEqual(["a.png", "b.png"], sorted(Path(p).name for p in shots))
        hashes = data["artifacts"]["screenshotHashes"]
        self.assertEqual(sorted(shots), sorted(hashes))
        for p, h in hashes.items():
            self.assertTrue(Path(p).is_file(), p)
            self.assertEqual(benchverify.sha256_file(p), h["sha256"])
        log = Path(data["artifacts"]["backendLog"])
        self.assertTrue(log.is_file(), log)
        self.assertTrue(log.is_relative_to(art), log)

    SLEEPING_CASE = textwrap.dedent("""
        import json, os, time, unittest
        import benchverify as bv
        S = bv.SESSION
        class T(unittest.TestCase):
            def test_waits_to_be_cancelled(self):
                tmp = os.environ["BV_MARKER"] + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump({"backend": S["backend"].proc.pid,
                               "adapter": S["browser"].proc.pid}, fh)
                os.rename(tmp, os.environ["BV_MARKER"])
                time.sleep(120)
    """)

    def cancel_run(self, sig):
        """The installed runner's way: the wrapper leads a session of its
        own and is signalled as a group (verify.py: SIGTERM, 5s, SIGKILL)."""
        import signal
        import time
        if not shutil.which("node"):
            self.skipTest("node not available")
        mods, _ = self.fake_modules()
        cases = self.base / "cases"
        cases.mkdir()
        (cases / "case_sleep.py").write_text(self.SLEEPING_CASE)
        marker = self.base / "marker.json"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BENCH_VERIFY") and k != "PW_WS"}
        # BV_MARKER reaches the case because the case runs in the wrapper
        env.update({"PW_WS": "ws://127.0.0.1:39123/", "BV_MARKER": str(marker),
                    "BENCH_VERIFY_PLAYWRIGHT_MODULES": str(mods)})
        proc = subprocess.Popen(
            [sys.executable, str(VERIFY / "browser"), "--root", str(REPO), "--cases",
             str(cases), "--artifacts", str(self.artifacts), "--scratch", str(self.base)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))
        deadline = time.time() + 60
        while not marker.exists():
            self.assertIsNone(proc.poll(), proc.stdout.read() if proc.poll() is not None else "")
            self.assertLess(time.time(), deadline, "the case never started")
            time.sleep(0.1)
        pids = json.loads(marker.read_text())
        for pid in pids.values():
            self.addCleanup(lambda p=pid: alive(p) and os.kill(p, signal.SIGKILL))
        t = time.time()
        os.killpg(proc.pid, sig)
        out, _ = proc.communicate(timeout=30)
        elapsed = time.time() - t
        for _ in range(40):          # a death signal is delivered, not awaited
            if not any(alive(p) for p in pids.values()):
                break
            time.sleep(0.05)
        for name, pid in pids.items():
            self.assertFalse(alive(pid), f"the {name} (pid {pid}) outlived the cancellation")
        return proc.returncode, out, elapsed

    def test_sigterm_unwinds_and_takes_backend_and_browser_with_it(self):
        import signal
        rc, out, elapsed = self.cancel_run(signal.SIGTERM)
        self.assertEqual(SETUP, rc, out)
        # well inside the runner's 5s grace, so its SIGKILL is never needed
        self.assertLess(elapsed, 5, out)
        record = Path(out.split("record: ")[-1].strip())
        data = json.loads(record.read_text())
        self.assertEqual("setup-error", data["outcome"])
        self.assertIn("cancelled", data["reason"])
        self.assertEqual("SIGTERM", data["cancelled"])

    def test_sigkill_of_the_wrapper_still_leaves_no_backend_or_browser(self):
        import signal
        rc, out, _ = self.cancel_run(signal.SIGKILL)
        self.assertEqual(-signal.SIGKILL, rc)

    def test_no_two_screenshots_share_a_path(self):
        # a second shot to the same path overwrites the first one's evidence
        import re
        from collections import Counter
        src = (VERIFY / "browser_cases" / "case_board.py").read_text()
        widths = re.search(r"WIDTHS = (\[.*\])", src).group(1)
        views = re.search(r'for view in (\[[^\]]*\])', src).group(1)
        names = []
        for name in re.findall(r'shot\(f?"([^"]+)"\)', src):
            if "{w}x{h}" in name:
                names += [name.replace("{w}x{h}", f"{w}x{h}") for w, h in eval(widths)]
            elif "{view}" in name:
                names += [name.replace("{view}", v) for v in eval(views)]
            else:
                names.append(name)
        self.assertGreater(len(names), 5)
        self.assertEqual([], [n for n, c in Counter(names).items() if c > 1])

    def test_the_readiness_dialog_is_photographed_before_it_is_measured(self):
        src = (VERIFY / "browser_cases" / "case_board.py").read_text()
        body = src[src.index("def test_the_readiness_inspector"):]
        body = body[:body.index("\nclass ")]
        self.assertLess(body.index('shot(f"readiness-open-{w}x{h}")'),
                        body.index('self.assertEqual([], bv.layout_problems(m), f"at {size}")'))
        # the assertion itself is still there, unweakened, at every size
        # including the zoomed one
        self.assertIn('self.assertEqual([], bv.layout_problems(m), f"at {size}")', body)
        self.assertIn("for w, h in WIDTHS + [ZOOMED]:", body)


if __name__ == "__main__":
    unittest.main()
