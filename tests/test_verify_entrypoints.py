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

    def run_entry(self, entry, *args, cwd=None, env_extra=None, root_arg=True):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BENCH_VERIFY_")}
        env.update(env_extra or {})
        argv = [sys.executable, str(VERIFY / entry)]
        if root_arg:
            argv += ["--root", str(self.root)]
        argv += ["--artifacts", str(self.artifacts), "--scratch", str(self.base), *args]
        r = subprocess.run(argv, cwd=str(cwd or self.base), env=env,
                           capture_output=True, text=True, timeout=300)
        result = self.artifacts / "result.json"
        data = json.loads(result.read_text()) if result.is_file() else None
        return r, data


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

    def test_zero_discovered_browser_tests_is_a_setup_error(self):
        empty = self.base / "empty-cases"
        empty.mkdir()
        r, data = self.run_entry("browser", "--cases", str(empty), env_extra={
            "BENCH_VERIFY_BROWSER": sys.executable})
        self.assertEqual(SETUP, r.returncode, r.stdout + r.stderr)
        self.assertIn("zero", data["reason"])


if __name__ == "__main__":
    unittest.main()
