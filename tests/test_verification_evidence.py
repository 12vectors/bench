"""Bench runs the named check, and the evidence is its own exit status
(task 68).

The subject is a security and honesty boundary, so nothing here mocks the
runner. Every case writes a real profile document, a real executable, a
real git checkout and a real worktree, asks for a real verification, and
reads the record back off disk. The "agents" are stub scripts, so no token
is spent and no live application, secret, setting or service is touched —
every root in every case is a disposable temporary directory.

Four claims are load-bearing and each has a class of its own:

- **Nothing a model writes reaches an argv.** A run names a check; bench
  builds the argument list from the operator's document and runs it with
  no shell. Shell metacharacters, command substitution, compound commands
  and paths with spaces are all regression-tested at the invocation, not
  at the settings generator.
- **The outcome is the runner's, and it is not one word.** Pass, test
  failure, permission denial, preparation failure, not-run, cancellation,
  timeout, board restart and stale source are nine distinguishable
  outcomes with structured reasons. A non-zero wrapper exit is never read
  as a test failure, and an agent exiting zero never makes a check pass.
- **A pass is bound to what it was about.** Modified tracked source, a
  changed check definition or a changed policy makes recorded evidence
  read `stale` rather than reusable.
- **The boundary is the real one, not the settings document.** A check
  cannot be declared against the live application checkout, cannot
  reshape the repository, runs without the publication credentials in its
  environment, and cannot reach another card's writable state.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import httpd  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

CLAUDE = REPO / "manager" / "core" / "adapters" / "claude"
OPENCODE = REPO / "manager" / "core" / "adapters" / "opencode"

CARD = "68-run-controlled-verification.md"
STEM = CARD[:-3]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_settings = _load("hook_settings_68", CLAUDE / "hook_settings.py")
permission_config = _load("permission_config_68", OPENCODE / "permission_config.py")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def card(title: str, *, status: str = "In Progress") -> str:
    return (f"# {title}\n\n**Status:** {status}\n**Priority:** High\n\n"
            f"What this card is for, at enough length to be a brief.\n")


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


# A "check" that records exactly what it was handed and where. Everything
# the invocation claims is asserted from this file rather than from the
# generated permission rules, which is the point: a rule is a claim about
# what may be launched, and this is what actually ran.
RECORDER = """#!{python}
import json, os, sys
out = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": {{k: v for k, v in os.environ.items()}},
}}
with open({record!r}, "a") as fh:
    fh.write(json.dumps(out) + "\\n")
sys.exit(int(os.environ.get("RECORDER_RC", "0")))
"""

# A check that fails the way a test suite fails: it ran, and it says no.
FAILS = """#!{python}
import sys
print("1 test failed: the thing does not do the thing")
sys.exit(1)
"""

# A check that does not end until it is killed, for the timeout and the
# cancellation cases.
HANGS = """#!{python}
import time
print("working", flush=True)
time.sleep(600)
"""

# A check that prints something shaped like a credential, for the scrub.
LEAKS = """#!{python}
print("GITHUB_TOKEN=ghp_01234567890abcdefghijABCDEFGHIJ")
print("api_key: sk-abcdefghijklmnopqrstuvwxyz0123")
print("the tests passed")
"""


class VerifyCase(unittest.TestCase):
    """A disposable tool install, work root and application checkout, a
    card in in-progress with a real worktree on a real branch, and config
    pointed at all of it. Nothing outside the temporary directory is read
    or written by any case below."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.repo = self.tmp / "app"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main's line\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# the brief\n", encoding="utf-8")
        # a file named like a credential, beside the code, so "adjacent
        # secret" is a real file on disk rather than a hypothetical
        (self.repo / ".env").write_text("GH_TOKEN=ghp_notreal\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

        self.tool, self.work = self.tmp / "tool", self.tmp / "work"
        self.tasks = self.work / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        (self.work / "reference").mkdir(parents=True, exist_ok=True)

        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        (self.local / "commands").mkdir(parents=True)
        self.adapter = self.local / "adapters" / config.ADAPTER / "run"
        self.state = self.local / "state"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.record = self.tmp / "invocations.jsonl"

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.tool,
                   LOCAL=self.local, CORE=config.CORE,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.state / "plans",
                   MANIFESTS_DIR=self.state / "manifests",
                   CONTEXT_DIR=self.state / "context",
                   EVIDENCE_DIR=self.state / "evidence",
                   VERIFY_DIR=self.state / "verify",
                   AGENT_DIR=self.state / "agent",
                   SESSIONS_DIR=self.state / "sessions",
                   AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
                   APP_BASE="main", GIT_REMOTE="", APP_SETTING=str(self.repo),
                   WORK_SETTING=str(self.work), EXTERNAL=True,
                   COMMIT_MOVES=False, SYNC=False, FETCH_TIMEOUT=10.0,
                   VERIFY_TIMEOUT=60.0, PREPARE_TIMEOUT=60.0,
                   VERIFY_PARALLEL=2, VERIFY_LOG_CAP=4 * 1024 * 1024)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         verify.RUNNING, verify._KEYS):
            registry.clear()
            self.addCleanup(registry.clear)
        # The memoised freshness readings are per evidence id and keyed on
        # paths this case is about to replace, so they start and end empty
        verify.forget_cached()
        self.addCleanup(verify.forget_cached)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        (self.tasks / "in-progress" / CARD).write_text(
            card("68 — Run controlled verification"), encoding="utf-8")

    # — the furniture —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def declares(self, document: dict | str) -> Path:
        path = self.local / "profiles.json"
        path.write_text(document if isinstance(document, str)
                        else json.dumps(document), encoding="utf-8")
        return path

    def executable(self, name: str, body: str) -> Path:
        """One real executable in a directory this test owns."""
        path = self.bin / name
        path.write_text(body.format(python=sys.executable, record=str(self.record)),
                        encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def worktree(self, *, dirty: bool = False) -> Path:
        """The card's own checkout, made the way the board makes one.
        Idempotent, so a class that wants one in setUp and a case that
        asks for one again get the same checkout."""
        path = config.WORKTREES / STEM
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            git(self.repo, "worktree", "add", "-b", f"task/{STEM}",
                str(path), "main")
        if dirty:
            (path / "code.txt").write_text("the run's edit\n", encoding="utf-8")
        return path

    def manifest(self, intent: str = "work") -> dict:
        """The resolved profile, recorded under an agent id, as a launch
        would leave it — so a request can be made as a run."""
        found = profiles.require(
            profiles.manifest(intent, CARD, "in-progress",
                              pin_inputs=True))
        return profiles.record("run-1", found)

    def ask(self, check: str, *, scope: str | None = None,
            agent: str | None = "run-1", intent: str | None = None) -> dict:
        return verify.request(check=check, scope=scope, agent=agent,
                              task=None if agent else CARD, intent=intent,
                              stage=None if agent else "in-progress",
                              actor="agent" if agent else "you")

    def invocations(self) -> list[dict]:
        if not self.record.is_file():
            return []
        return [json.loads(line) for line in
                self.record.read_text(encoding="utf-8").splitlines() if line]

    def one_check(self, name: str = "unit", *, run: str | None = None,
                  root: str = "worktree", **extra) -> dict:
        """A document with one check, declared for the work intent."""
        check = {"name": name, "run": run or str(self.executable("recorder", RECORDER)),
                 "root": root}
        check.update(extra)
        return {"v": 1, "checks": [check],
                "profiles": {"work": {"verify": [name]},
                             "review": {"verify": [name]}}}


# ── nothing a model writes reaches an argv ─────────────────────────────


class TheInvocationIsBenchs(VerifyCase):
    """What actually ran, read out of the process's own record. A
    permission rule is a claim about what may be launched; this is the
    argument list that was."""

    def test_the_argv_is_the_documents_and_no_shell_is_involved(self):
        self.declares(self.one_check(run=f"{self.executable('recorder', RECORDER)} "
                                         f"--quiet -k smoke"))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed", found["why"])
        ran = self.invocations()
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0]["argv"], ["--quiet", "-k", "smoke"])

    def test_a_path_with_spaces_arrives_as_one_argument(self):
        spaced = self.tmp / "a directory with spaces"
        spaced.mkdir()
        target = spaced / "the file.txt"
        target.write_text("x\n", encoding="utf-8")
        recorder = self.executable("recorder", RECORDER)
        # shlex round-trips it, and the grant is stored in normalised form
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": f"{recorder} '{target}'"}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(self.invocations()[0]["argv"], [str(target)])

    def test_a_scope_is_a_name_and_its_arguments_are_the_operators(self):
        recorder = self.executable("recorder", RECORDER)
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(recorder),
             "scopes": {"fast": ["-k", "fast"], "one": ["tests/test_one.py"]}}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit", scope="fast")
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(self.invocations()[0]["argv"], ["-k", "fast"])
        self.assertEqual(found["scopeArgs"], ["-k", "fast"])

    def test_a_scope_that_is_not_a_declared_name_is_refused(self):
        self.declares(self.one_check(scopes={"fast": ["-k", "fast"]}))
        self.worktree()
        self.manifest()
        for attempt in ("; rm -rf /", "$(whoami)", "`id`", "fast && echo no",
                        "../../etc/passwd", "-k everything"):
            with self.subTest(scope=attempt):
                found = self.ask("unit", scope=attempt)
                self.assertEqual(found["outcome"], "denied")
                self.assertEqual(found["reason"], "unknown-scope")
                self.assertEqual(found["argv"], [],
                                 "a refused request builds no argument list")
        self.assertEqual(self.invocations(), [],
                         "nothing ran for any of them")

    def test_a_check_name_with_a_shell_in_it_is_refused_by_name(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        for attempt in ("unit; rm -rf /", "unit && id", "unit$(id)", "../unit"):
            with self.subTest(check=attempt):
                found = self.ask(attempt)
                self.assertEqual(found["outcome"], "denied")
                self.assertEqual(found["reason"], "unknown-check")
        self.assertEqual(self.invocations(), [])

    def test_a_document_declaring_a_shell_line_is_refused_where_it_is_read(self):
        for line in ("pytest && echo done", "pytest | tee out",
                     "pytest $(cat x)", "pytest; id", "pytest > out",
                     "pytest *.py", "pytest `id`"):
            with self.subTest(run=line):
                self.declares({"v": 1, "checks": [{"name": "unit", "run": line}],
                               "profiles": {"work": {"verify": ["unit"]}}})
                found = profiles.manifest("work", CARD, "in-progress")
                self.assertTrue(found["blockers"], line)
                self.assertIn("'unit'", found["blockers"][0])

    def test_the_only_rule_the_run_gains_is_the_shim_itself(self):
        """No prefix broadening: the rules an adapter renders cover the
        shim and the project's own grants, and nothing wider."""
        self.declares(self.one_check())
        self.worktree()
        found = profiles.manifest("work", CARD, "in-progress")
        policy = profiles.adapter_policy(found)
        rules = hook_settings.allow_rules("work", [], policy)
        shim = str(profiles.verify_shim())
        self.assertIn(f"Bash({shim})", rules)
        self.assertIn(f"Bash({shim}:*)", rules)
        # the check's own executable is nowhere in the rules: the run
        # cannot launch it, only ask bench to
        self.assertNotIn(str(self.bin / "recorder"), " ".join(rules))
        self.assertEqual([r for r in rules if "recorder" in r], [])

    def test_the_shim_grant_is_one_executable_with_no_arguments(self):
        self.declares(self.one_check())
        self.worktree()
        found = profiles.manifest("work", CARD, "in-progress")
        shim = [g for g in found["commands"] if g["name"] == "bench-verify"][0]
        self.assertEqual(shim["argv"], [])
        self.assertEqual(shim["executable"], str(profiles.verify_shim()))
        self.assertEqual(shim["source"], "bench")
        self.assertTrue(os.access(profiles.verify_shim(), os.X_OK),
                        "the shim a grant names has to be runnable")


# ── the outcome is the runner's, and it is not one word ────────────────


class EveryOutcomeIsItsOwn(VerifyCase):
    def test_a_pass_is_an_exit_of_zero_from_a_process_bench_waited_on(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed")
        self.assertEqual(found["reason"], "exit-zero")
        self.assertEqual(found["rc"], 0)
        self.assertEqual(found["runner"], {"module": "verify", "version": 1,
                                           "revision": verify.RUNNER_REVISION})

    def test_an_assertion_failure_is_a_test_failure_and_says_rc(self):
        self.declares(self.one_check(run=str(self.executable("fails", FAILS))))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "failed")
        self.assertEqual(found["reason"], "exit-nonzero")
        self.assertEqual(found["rc"], 1)
        self.assertIn("the thing does not do the thing",
                      verify.log_text(found["id"])["text"])

    def test_an_executable_that_cannot_run_is_not_a_test_failure(self):
        missing = self.bin / "not-here"
        self.declares({"v": 1, "checks": [{"name": "unit", "run": str(missing)}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "error")
        self.assertEqual(found["reason"], "no-executable")
        self.assertIn("not a test failure", found["why"])

    def test_a_refusal_is_recorded_as_a_permission_denial(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("coverage")
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "unknown-check")
        self.assertIsNotNone(verify.read(found["id"]),
                             "a refusal is evidence, not an absence")
        self.assertIn("unit", found["why"], "it names what is on offer")

    def test_a_required_preparation_failure_is_its_own_outcome(self):
        self.declares({
            "v": 1,
            "prepare": [{"name": "deps", "run": str(self.executable("fails", FAILS))}],
            "checks": [{"name": "unit", "run": str(self.executable("recorder", RECORDER)),
                        "prepare": ["deps"]}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "prepare-failed")
        self.assertEqual(found["reason"], "prepare-exit")
        self.assertIn("not a test failure", found["why"])
        self.assertEqual([p["name"] for p in found["prepare"]], ["deps"])
        self.assertEqual(self.invocations(), [],
                         "the check itself never ran")

    def test_an_optional_preparation_failure_lets_the_check_run(self):
        self.declares({
            "v": 1,
            "prepare": [{"name": "warm", "run": str(self.executable("fails", FAILS)),
                         "required": False}],
            "checks": [{"name": "unit", "run": str(self.executable("recorder", RECORDER)),
                        "prepare": ["warm"]}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed")
        self.assertIn("optional", found["prepare"][0]["why"])
        self.assertEqual(len(self.invocations()), 1)

    def test_a_check_nothing_asked_for_is_not_run_not_silence(self):
        self.declares(self.one_check())
        self.worktree()
        manifest = self.manifest()
        rollup = verify.rollup(CARD, manifest)
        self.assertEqual([r["check"] for r in rollup["rows"]], ["unit"])
        self.assertEqual(rollup["rows"][0]["outcome"], "not-run")
        self.assertEqual(rollup["rows"][0]["reason"], "never-asked")
        self.assertFalse(rollup["verified"])

    def test_an_agent_exiting_zero_verifies_nothing(self):
        """The whole point, in one case: a run that finished cleanly and
        asked for nothing leaves a card whose checks are `not-run`."""
        self.declares(self.one_check())
        self.worktree()
        manifest = self.manifest()
        rollup = verify.rollup(CARD, manifest)
        self.assertFalse(rollup["verified"])
        self.assertNotIn("passed", [r["outcome"] for r in rollup["rows"]])

    def test_a_timeout_is_not_a_test_failure(self):
        self.patch(VERIFY_TIMEOUT=1.0)
        self.declares(self.one_check(run=str(self.executable("hangs", HANGS))))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "error")
        self.assertEqual(found["reason"], "timeout")
        self.assertIn("not a test failure", found["why"])

    def test_a_cancellation_is_recorded_as_cancelled(self):
        self.declares(self.one_check(run=str(self.executable("hangs", HANGS))))
        self.worktree()
        self.manifest()
        done: list[dict] = []
        thread = threading.Thread(
            target=lambda: done.append(self.ask("unit")), daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)),
                        "the verification never started")
        [eid] = list(verify.RUNNING)
        verify.cancel(eid)
        thread.join(timeout=30)
        self.assertTrue(done, "the request never returned")
        self.assertEqual(done[0]["outcome"], "cancelled")
        self.assertEqual(done[0]["reason"], "cancelled-by-request")

    def hold_while_running(self, cancel_for_agent) -> None:
        """Ask for a hanging check, then hold its run with `cancel_for_agent`.

        What the run was running is read *before* the hold: a cancellation
        may finish — and its reservation leave RUNNING — before it returns,
        so reading RUNNING afterwards races the very thing being tested.
        """
        self.declares(self.one_check(run=str(self.executable("hangs", HANGS))))
        self.worktree()
        self.manifest()
        done: list[dict] = []
        thread = threading.Thread(
            target=lambda: done.append(self.ask("unit")), daemon=True)
        thread.start()

        def stop_and_join():
            # whatever an assertion below did, no hanging check outlives it
            with state.LOCK:
                left = list(verify.RUNNING)
            for eid in left:
                try:
                    verify.cancel(eid)
                except ValueError:
                    pass
            thread.join(timeout=30)
        self.addCleanup(stop_and_join)

        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        with state.LOCK:
            expected = [eid for eid, record in verify.RUNNING.items()
                        if record.get("agent") == "run-1"]
        self.assertTrue(expected, "the check is reserved for the run")
        self.assertEqual(cancel_for_agent("run-1"), expected)
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(done[0]["outcome"], "cancelled")
        self.assertEqual(done[0]["reason"], "cancelled-with-run")
        self.assertEqual(done[0]["id"], expected[0])

    def test_holding_the_run_cancels_what_bench_was_running_for_it(self):
        self.hold_while_running(verify.cancel_for_agent)

    def test_a_hold_that_finishes_before_it_returns_still_names_the_run(self):
        """The cancellation completes, and RUNNING empties, before the
        answer is compared: the run's checks are still the ones named."""
        def finishes_first(agent_id):
            stopped = verify.cancel_for_agent(agent_id)
            self.assertTrue(wait_for(lambda: not verify.RUNNING),
                            "the cancelled check leaves RUNNING")
            return stopped
        self.hold_while_running(finishes_first)

    def test_a_restart_closes_evidence_this_board_does_not_own(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        # what a board that died mid-check leaves behind on disk
        stored = verify.read(found["id"])
        stored.update({"outcome": "running", "reason": None, "ended": None})
        verify._store(stored)
        closed = verify.recover()
        self.assertEqual([c["id"] for c in closed], [found["id"]])
        again = verify.read(found["id"])
        self.assertEqual(again["outcome"], "error")
        self.assertEqual(again["reason"], "board-restart")
        self.assertIn("nothing about it passed", again["why"])

    def test_a_restart_re_runs_nothing(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        before = len(self.invocations())
        stored = verify.read(found["id"])
        stored["outcome"] = "running"
        verify._store(stored)
        verify.recover()
        self.assertEqual(len(self.invocations()), before)

    def test_a_duplicate_request_joins_rather_than_doubling(self):
        self.declares(self.one_check(run=str(self.executable("hangs", HANGS))))
        self.worktree()
        self.manifest()
        first: list[dict] = []
        thread = threading.Thread(
            target=lambda: first.append(self.ask("unit")), daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        second = self.ask("unit")
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(second["id"], list(verify.RUNNING)[0])
        verify.cancel(second["id"])
        thread.join(timeout=30)
        self.assertEqual(len(self.invocations()), 0,
                         "the hanging check is the only process there was")

    def test_two_checks_do_not_fight_over_the_machine(self):
        """A check is somebody else's test suite, so the board runs at
        most BOARD_VERIFY_PARALLEL of them: the second waits its turn
        rather than being refused, and both get a real answer."""
        self.patch(VERIFY_PARALLEL=1)
        marker = self.tmp / "concurrent"
        slow = self.executable("slow", """#!{python}
import os, time
path = os.environ["BENCH_VERIFY_MARKER"]
with open(path, "a") as fh:
    fh.write("in\\n")
time.sleep(1.0)
with open(path, "a") as fh:
    fh.write("out\\n")
""")
        self.declares({"v": 1, "checks": [
            {"name": "one", "run": str(slow)},
            {"name": "two", "run": str(slow)}],
            "profiles": {"work": {"verify": ["one", "two"]}}})
        self.worktree()
        self.manifest()
        config._ENV["BENCH_VERIFY_MARKER"] = str(marker)
        self.addCleanup(config._ENV.pop, "BENCH_VERIFY_MARKER", None)

        done: list[dict] = []
        threads = [threading.Thread(target=lambda n=name: done.append(self.ask(n)),
                                    daemon=True)
                   for name in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(done), 2)
        self.assertEqual({r["outcome"] for r in done}, {"passed"})
        # one in, one out, one in, one out — never two inside at once
        self.assertEqual(marker.read_text(encoding="utf-8").split(),
                         ["in", "out", "in", "out"])

    def test_every_outcome_word_is_one_the_vocabulary_names(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        for record in (self.ask("unit"), self.ask("nope")):
            self.assertIn(record["outcome"], verify.OUTCOMES)
            if record["reason"]:
                self.assertIn(record["reason"], verify.REASONS)


# ── a pass is bound to what it was about ───────────────────────────────


class EvidenceIsBoundToItsSubject(VerifyCase):
    def test_the_record_names_the_exact_source_it_ran_against(self):
        self.declares(self.one_check())
        tree = self.worktree()
        head = git(tree, "rev-parse", "HEAD").stdout.strip()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["source"]["commit"], head)
        self.assertEqual(found["source"]["branch"], f"task/{STEM}")
        self.assertTrue(found["source"]["bound"])
        self.assertTrue(found["source"]["sourceId"].startswith("src:"))

    def test_uncommitted_work_is_part_of_the_binding(self):
        self.declares(self.one_check())
        tree = self.worktree(dirty=True)
        self.manifest()
        found = self.ask("unit")
        self.assertTrue(found["source"]["dirty"])
        self.assertTrue(verify.freshness(found)["fresh"])
        # the same commit, different content: the pass is no longer about
        # the code that is there
        (tree / "code.txt").write_text("changed again\n", encoding="utf-8")
        reading = verify.outcome_now(verify.read(found["id"]))
        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["recorded"], "passed")
        self.assertIn("changed", " ".join(verify.freshness(
            verify.read(found["id"]))["changed"]))

    def test_an_untracked_file_appearing_makes_a_pass_stale(self):
        self.declares(self.one_check())
        tree = self.worktree()
        self.manifest()
        found = self.ask("unit")
        (tree / "new_test.py").write_text("def test(): assert False\n",
                                          encoding="utf-8")
        self.assertEqual(verify.outcome_now(verify.read(found["id"]))["outcome"],
                         "stale")

    def test_a_new_commit_makes_a_pass_stale_and_names_both_commits(self):
        self.declares(self.one_check())
        tree = self.worktree()
        self.manifest()
        found = self.ask("unit")
        (tree / "more.txt").write_text("more\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "-c", "user.email=a@b", "-c", "user.name=t",
            "commit", "-q", "-m", "more")
        fresh = verify.freshness(verify.read(found["id"]))
        self.assertFalse(fresh["fresh"])
        self.assertIn(found["source"]["commit"][:12], " ".join(fresh["changed"]))

    def test_editing_the_check_makes_a_pass_stale(self):
        recorder = self.executable("recorder", RECORDER)
        self.declares({"v": 1, "checks": [{"name": "unit", "run": str(recorder)}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertTrue(verify.freshness(found)["fresh"])
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": f"{recorder} --different"}],
            "profiles": {"work": {"verify": ["unit"]}}})
        reading = verify.outcome_now(verify.read(found["id"]))
        self.assertEqual(reading["outcome"], "stale")
        # a record with a result identity names the dimension that moved
        # (#97); a legacy one still says `policy-changed`
        self.assertEqual(reading["reason"], "check-changed")

    def test_the_memoised_reading_cannot_hold_a_pass_open(self):
        """Asking whether a pass is fresh costs git, so the answer is
        memoised — and the memo is only ever consulted for a `passed`,
        expires, and can be dropped. What it must never do is keep a card
        green after its code changed, so the board reading carries a time
        bucket as well as the epoch."""
        self.declares(self.one_check())
        tree = self.worktree()
        self.manifest()
        found = self.ask("unit")
        collected = {"stages": [{"slug": "in-progress",
                                 "tasks": [{"file": CARD}]}]}
        self.assertEqual(verify.board(collected)[CARD]["outcome"], "passed")
        (tree / "code.txt").write_text("changed under it\n", encoding="utf-8")
        verify.forget_cached()
        self.assertEqual(verify.board(collected)[CARD]["outcome"], "stale")
        # and a failure is never asked at all — the record is its last word
        again = verify.read(found["id"])
        again["outcome"] = "failed"
        self.assertEqual(verify.outcome_now(again)["outcome"], "failed")

    def test_nothing_ever_becomes_passed_by_being_re_read(self):
        self.declares(self.one_check(run=str(self.executable("fails", FAILS))))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        for _ in range(3):
            self.assertEqual(
                verify.outcome_now(verify.read(found["id"]))["outcome"], "failed")

    def test_the_policy_context_and_request_identities_are_on_the_record(self):
        (self.work / "reference" / "d.md").write_text("design\n", encoding="utf-8")
        document = self.one_check()
        document["context"] = [{"name": "design", "path": "work:reference/d.md"}]
        self.declares(document)
        self.worktree()
        manifest = self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["policyId"], manifest["policyId"])
        self.assertEqual(found["requestId"], manifest["requestId"])
        self.assertEqual(found["contextIds"],
                         [i["contextId"] for i in manifest["context"]])
        self.assertTrue(found["checkId"].startswith("check:unit@"))

    def test_a_run_with_no_recorded_profile_cannot_verify_at_all(self):
        self.declares(self.one_check())
        self.worktree()
        found = self.ask("unit", agent="never-launched")
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "no-profile")

    def test_the_frozen_profile_is_the_policy_a_run_asks_under(self):
        """A document edited after the launch retargets the next run, not
        this one: the check a run may ask for is the one it was granted."""
        recorder = self.executable("recorder", RECORDER)
        self.declares({"v": 1, "checks": [{"name": "unit", "run": str(recorder)}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        self.declares({"v": 1, "checks": [{"name": "other", "run": str(recorder)}],
                       "profiles": {"work": {"verify": ["other"]}}})
        self.assertEqual(self.ask("unit")["outcome"], "passed",
                         "the run keeps the check it was granted")
        self.assertEqual(self.ask("other")["outcome"], "denied",
                         "and gains nothing the document added since")


# ── the boundary is the real one ───────────────────────────────────────


class TheBoundaryIsReal(VerifyCase):
    def test_a_check_may_not_be_declared_against_the_application_checkout(self):
        self.declares(self.one_check(root="app"))
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertTrue(found["blockers"])
        self.assertIn("never runs in the application checkout",
                      found["blockers"][0])

    def test_a_check_runs_in_the_cards_own_worktree_and_nowhere_else(self):
        self.declares(self.one_check())
        tree = self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["root"]["policy"], "worktree")
        self.assertEqual(Path(found["root"]["path"]).resolve(), tree.resolve())
        self.assertEqual(Path(self.invocations()[0]["cwd"]).resolve(),
                         tree.resolve())

    def test_another_cards_worktree_is_unreachable_from_a_request(self):
        """The request names no path. The root is derived from the task,
        and the task from the run that asked — so there is no argument a
        run could pass to reach somebody else's writable state."""
        self.declares(self.one_check())
        self.worktree()
        other = config.WORKTREES / "99-someone-elses-card"
        git(self.repo, "worktree", "add", "-b", "task/99-someone-elses-card",
            str(other), "main")
        (other / "secret-work.txt").write_text("not yours\n", encoding="utf-8")
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(Path(found["root"]["path"]).name, STEM)
        self.assertNotIn("99-someone-elses-card", json.dumps(found))
        self.assertTrue((other / "secret-work.txt").is_file())

    def test_a_foreign_checkout_where_the_worktree_goes_is_refused(self):
        self.declares(self.one_check())
        stranger = config.WORKTREES / STEM
        stranger.mkdir(parents=True)
        git(stranger, "init", "-q", "-b", "main")
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "root-foreign")
        self.assertIn("another repository", found["why"])

    def test_a_check_that_reshapes_the_repository_is_refused(self):
        for line in ("git push origin main", "git reset --hard",
                     "git worktree prune", "git checkout main",
                     "git config user.name x", "git clean -fdx",
                     "git branch -D main", "git commit -am x"):
            with self.subTest(run=line):
                self.declares({"v": 1, "checks": [{"name": "unit", "run": line}],
                               "profiles": {"work": {"verify": ["unit"]}}})
                found = profiles.manifest("work", CARD, "in-progress")
                self.assertTrue(found["blockers"], line)
                self.assertIn("reshapes the repository", found["blockers"][0])

    def test_a_read_only_git_check_is_perfectly_fine(self):
        self.declares({"v": 1, "checks": [
            {"name": "whitespace", "run": "git diff --check"}],
            "profiles": {"work": {"verify": ["whitespace"]}}})
        self.assertEqual(profiles.manifest("work", CARD, "in-progress")["blockers"],
                         [])

    def test_an_argument_naming_a_credential_is_refused(self):
        for line in ("pytest --env-file .env", "pytest secrets.yaml",
                     "pytest /keys/id_rsa", "pytest cert.pem"):
            with self.subTest(run=line):
                self.declares({"v": 1, "checks": [{"name": "unit", "run": line}],
                               "profiles": {"work": {"verify": ["unit"]}}})
                found = profiles.manifest("work", CARD, "in-progress")
                self.assertTrue(found["blockers"], line)
                self.assertIn("credential", found["blockers"][0])

    def test_publication_credentials_are_not_in_the_environment(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        # the board's own environment, as an operator's would be
        for name, value in (("GH_TOKEN", "ghp_notreal"),
                            ("GITHUB_TOKEN", "ghp_alsonotreal"),
                            ("ANTHROPIC_API_KEY", "sk-notreal"),
                            ("AWS_SECRET_ACCESS_KEY", "notreal")):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
            # child_env() is built from the settings config loaded at
            # import, so this is where a board's real environment sits
            config._ENV[name] = value
            self.addCleanup(config._ENV.pop, name, None)
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed", found["why"])
        handed = self.invocations()[0]["env"]
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY",
                     "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(name, handed, f"{name} reached a check")
            self.assertIn(name, found["env"]["removed"])

    def test_the_launch_environment_does_not_reach_a_check(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        self.ask("unit")
        handed = self.invocations()[0]["env"]
        for name in ("AGENT_PROMPT", "AGENT_PROFILE", "AGENT_CONTEXT",
                     "BOARD_AGENT_ID", "BOARD_PORT"):
            self.assertNotIn(name, handed,
                             f"{name} reached a check, which could then ask "
                             f"bench for another verification")

    def test_caches_and_temporary_files_are_pointed_somewhere_bench_owns(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        handed = self.invocations()[0]["env"]
        for name in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "PIP_CACHE_DIR",
                     "NPM_CONFIG_CACHE", "CARGO_HOME"):
            self.assertTrue(handed[name].startswith(str(config.VERIFY_DIR)),
                            f"{name} is {handed[name]}")
        self.assertNotEqual(handed["HOME"], os.path.expanduser("~"))
        self.assertEqual(found["env"]["home"], handed["HOME"])

    def test_the_temporary_directory_is_cleaned_up_afterwards(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertFalse(Path(found["env"]["tmp"]).exists())
        self.assertTrue(Path(found["env"]["home"]).is_dir(),
                        "the cache directory is kept on purpose")

    def test_a_review_verifies_in_a_checkout_bench_makes_and_removes(self):
        """A read-only run has no worktree, and must never run a test
        suite in the live application. Bench checks the card's branch out
        detached, runs there, and takes it away again."""
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER)),
             "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        tree = self.worktree()
        (tree / "work.txt").write_text("the work\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "-c", "user.email=a@b", "-c", "user.name=t",
            "commit", "-q", "-m", "the work")
        head = git(tree, "rev-parse", "HEAD").stdout.strip()
        found = profiles.require(profiles.manifest("review", CARD, "in-progress",
                                                   pin_inputs=True))
        profiles.record("review-1", found)
        got = verify.request(check="unit", agent="review-1")
        self.assertEqual(got["outcome"], "passed", got["why"])
        self.assertEqual(got["root"]["policy"], "source")
        ran_in = Path(self.invocations()[0]["cwd"]).resolve()
        self.assertNotEqual(ran_in, self.repo.resolve(),
                            "a check never runs in the live application")
        self.assertTrue(str(ran_in).startswith(str(config.VERIFY_DIR.resolve())))
        self.assertEqual(got["source"]["commit"], head)
        self.assertTrue(got["root"].get("removed"),
                        "bench takes its own checkout away again")
        self.assertFalse(ran_in.exists())

    def test_a_dirty_checkout_bench_made_is_kept_rather_than_forced_out(self):
        leaves = self.executable("leaves", """#!{python}
import os
open("generated.txt", "w").write("build output\\n")
open("tracked.txt", "a")
""")
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(leaves), "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        tree = self.worktree()
        (tree / "tracked.txt").write_text("x\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "-c", "user.email=a@b", "-c", "user.name=t",
            "commit", "-q", "-m", "tracked")
        found = profiles.require(profiles.manifest("review", CARD, "in-progress",
                                                   pin_inputs=True))
        profiles.record("review-1", found)
        got = verify.request(check="unit", agent="review-1")
        self.assertEqual(got["outcome"], "passed", got["why"])
        self.assertIn("kept", got["root"])
        self.assertTrue(Path(got["root"]["path"]).is_dir())
        self.assertIn("uncommitted", got["root"]["kept"])

    def test_a_source_pass_is_bound_to_the_commit_not_to_the_checkout(self):
        """Bench removes its own checkout by design, so asking after it
        would make every read-only verification stale the moment it
        finished. For a `source` check the commit is the binding, and the
        question is whether the card's branch still points at it."""
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER)),
             "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        tree = self.worktree()
        (tree / "work.txt").write_text("the work\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "-c", "user.email=a@b", "-c", "user.name=t",
            "commit", "-q", "-m", "the work")
        profiles.record("review-1", profiles.require(profiles.manifest(
            "review", CARD, "in-progress", pin_inputs=True)))
        got = verify.request(check="unit", agent="review-1")
        self.assertEqual(got["outcome"], "passed", got["why"])
        self.assertTrue(got["root"].get("removed"))
        verify.forget_cached()
        self.assertEqual(verify.outcome_now(verify.read(got["id"]))["outcome"],
                         "passed", "the checkout going is not the pass going")

        (tree / "more.txt").write_text("more\n", encoding="utf-8")
        git(tree, "add", "-A")
        git(tree, "-c", "user.email=a@b", "-c", "user.name=t",
            "commit", "-q", "-m", "more")
        verify.forget_cached()
        reading = verify.outcome_now(verify.read(got["id"]))
        self.assertEqual(reading["outcome"], "stale")
        self.assertIn("moved on", reading["why"])

    def test_a_source_check_with_no_branch_is_refused_not_guessed(self):
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER)),
             "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        found = profiles.require(profiles.manifest("review", CARD, "in-progress",
                                                   pin_inputs=True))
        profiles.record("review-1", found)
        got = verify.request(check="unit", agent="review-1")
        self.assertEqual(got["outcome"], "denied")
        self.assertEqual(got["reason"], "root-unavailable")
        self.assertIn("nothing has been committed", got["why"])

    def test_a_worktree_check_with_no_worktree_is_refused_with_a_way_out(self):
        self.declares(self.one_check())
        self.manifest()
        got = self.ask("unit")
        self.assertEqual(got["outcome"], "denied")
        self.assertEqual(got["reason"], "root-unavailable")
        self.assertIn('"root": "source"', got["why"])

    def test_a_profile_requiring_a_sandbox_is_still_refused(self):
        """The isolation backend question, answered honestly: no shipped
        adapter confines a process, so a profile that requires it refuses
        rather than reporting a guarantee bench cannot make."""
        document = self.one_check()
        document["requires"] = ["filesystem-sandbox"]
        self.declares(document)
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertTrue(found["blockers"])
        self.assertIn("filesystem-sandbox", found["blockers"][0])
        table = found["capabilities"]["filesystem-sandbox"]
        self.assertEqual(table["enforcement"], "pending")

    def test_what_bench_does_enforce_is_claimed_as_enforced(self):
        self.declares(self.one_check())
        self.worktree()
        found = profiles.manifest("work", CARD, "in-progress")
        for name in ("named-verification", "verification-evidence",
                     "isolated-environment"):
            self.assertEqual(found["capabilities"][name]["enforcement"],
                             "enforced")
            self.assertIn(name, found["guarantees"]["enforced"])
        self.assertIn("not a boundary",
                      found["capabilities"]["isolated-environment"]["why"])

    def test_a_network_preparation_step_says_so_and_is_not_enforced(self):
        self.declares({
            "v": 1,
            "prepare": [{"name": "deps", "network": True,
                         "run": str(self.executable("recorder", RECORDER))}],
            "checks": [{"name": "unit", "run": str(self.executable("recorder", RECORDER)),
                        "prepare": ["deps"]}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertEqual(found["blockers"], [])
        self.assertIn("enforces no network boundary", " ".join(found["notes"]))
        self.assertEqual(found["capabilities"]["network-policy"]["enforcement"],
                         "pending")
        self.manifest()
        got = self.ask("unit")
        self.assertTrue(got["prepare"][0]["network"])

    def test_nothing_outside_the_disposable_roots_was_touched(self):
        """The suite's own guarantee, asserted rather than assumed: a
        verification writes under the tool's state and the card's
        worktree, and nowhere else."""
        self.declares(self.one_check())
        before = sorted(p.name for p in self.repo.iterdir())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed")
        self.assertEqual(sorted(p.name for p in self.repo.iterdir()),
                         sorted([*before, ".git"]) if ".git" not in before
                         else before)
        self.assertEqual((self.repo / ".env").read_text(encoding="utf-8"),
                         "GH_TOKEN=ghp_notreal\n")
        self.assertFalse((self.repo / "generated.txt").exists())


# ── review may verify without gaining anything else ───────────────────


class ReviewVerifiesWithoutPublishing(VerifyCase):
    def test_a_read_only_profile_gets_the_shim_and_no_write_rule(self):
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER)),
             "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        found = profiles.manifest("review", CARD, "in-progress")
        self.assertEqual(found["blockers"], [])
        self.assertEqual([g["name"] for g in found["commands"]], ["bench-verify"])
        self.assertFalse(found["publication"]["commit"])
        self.assertFalse(found["publication"]["push"])
        self.assertEqual(found["writable"], [])
        self.assertIn("commit", [d["capability"] for d in found["denied"]])

    def test_both_adapters_render_the_shim_for_review_and_no_commit_rule(self):
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER)),
             "root": "source"}],
            "profiles": {"review": {"verify": ["unit"]}}})
        policy = profiles.adapter_policy(
            profiles.manifest("review", CARD, "in-progress"))
        shim = str(profiles.verify_shim())

        rules = hook_settings.allow_rules("review", [], policy)
        self.assertIn(f"Bash({shim}:*)", rules)
        self.assertNotIn("Bash(git commit)", rules)
        self.assertNotIn("Bash(git push)", rules)
        denies = hook_settings.deny_rules("review", policy)
        self.assertIn("Bash(git commit)", denies)
        self.assertIn("Bash(git push)", denies)

        bash = permission_config.bash_rules("review", [], policy)
        self.assertEqual(bash[f"{shim} *"], "allow")
        self.assertEqual(bash["git commit"], "deny")
        self.assertEqual(bash["git push"], "deny")

    def test_act_pr_may_verify_too_and_runs_in_the_cards_worktree(self):
        """The third permitted mode, executed rather than resolved: a run
        addressing review feedback asks for the same check by the same
        name, and keeps the push it needs."""
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("recorder", RECORDER))}],
            "profiles": {"act-pr": {"verify": ["unit"]}}})
        tree = self.worktree()
        found = profiles.require(profiles.manifest(
            "act-pr", CARD, "in-progress", pin_inputs=True))
        profiles.record("fix-1", found)
        self.assertTrue(found["publication"]["push"])
        got = verify.request(check="unit", agent="fix-1")
        self.assertEqual(got["outcome"], "passed", got["why"])
        self.assertEqual(got["intent"], "act-pr")
        self.assertEqual(Path(self.invocations()[0]["cwd"]).resolve(),
                         tree.resolve())

    def test_a_review_without_a_profile_gains_nothing_at_all(self):
        """The legacy reading is untouched: AGENT_COMMANDS never reached
        the read-only intent and still does not."""
        commands = ["python3 -m unittest", "npm test"]
        for mode in ("work", "act-pr", "review"):
            with self.subTest(mode=mode):
                self.assertEqual(hook_settings.allow_rules(mode, commands),
                                 hook_settings.allow_rules(mode, commands, None))
        rules = hook_settings.allow_rules("review", commands)
        self.assertNotIn("python3 -m unittest", " ".join(rules))


# ── the compatibility path, and a board that configured nothing ────────


class LegacyBoardsAreUntouched(VerifyCase):
    def test_a_board_with_no_document_has_no_checks_and_no_shim(self):
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertEqual(found["blockers"], [])
        self.assertEqual(found["verify"], [])
        self.assertEqual([g["name"] for g in found["commands"]], ["command-1"])
        self.assertEqual(verify.available(found), [])
        self.assertEqual(profiles.verification_block(found), "",
                         "the prompt is byte for byte what it was")

    def test_a_legacy_board_answers_a_request_by_saying_it_has_none(self):
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "unknown-check")
        self.assertIn("none", found["why"])

    def test_a_grant_named_in_verify_is_promoted_and_really_runs(self):
        recorder = self.executable("recorder", RECORDER)
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit", "run": str(recorder)}],
            "verify": ["unit"]}}})
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(len(self.invocations()), 1)

    def test_a_promoted_grant_that_cannot_be_a_check_refuses_by_name(self):
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "deploy", "run": "git push origin main",
                          "root": "worktree"}],
            "verify": ["deploy"]}}})
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertTrue(found["blockers"])
        self.assertIn("deploy", found["blockers"][0])
        self.assertIn("reshapes the repository", found["blockers"][0])

    def test_a_grant_the_run_still_has_is_unaffected_by_the_promotion(self):
        recorder = self.executable("recorder", RECORDER)
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit", "run": str(recorder)}],
            "verify": ["unit"]}}})
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertIn(str(recorder), [g["prefix"] for g in found["commands"]],
                      "the run keeps the grant it had; the check is in "
                      "addition to it")

    def test_the_prompt_says_nothing_on_a_board_with_no_checks(self):
        self.assertEqual(profiles.verification_block(None), "")
        self.assertEqual(profiles.verification_block({"verify": []}), "")


# ── what a person sees, from fixtures of every outcome ─────────────────


class TheBoardShowsWhatRan(VerifyCase):
    """Every structured outcome, through the API the page actually reads.
    The claim being tested is that each is distinguishable *as data* —
    the page renders a word per outcome, and a word needs a value."""

    def setUp(self):
        super().setUp()
        # A real checkout behind the fixtures, so a `passed` one is bound
        # to something that still holds — binding is what tells a pass
        # from a stale pass, and a fixture that could not be bound would
        # make every one of them stale.
        self.tree = self.worktree()
        self.bound = verify.source_identity(self.tree)

    def _fixture(self, name: str, outcome: str, **extra) -> dict:
        """One stored evidence record, written the way the runner writes
        them, so the reading paths see a real file."""
        record = {
            "v": 1, "id": f"ev:verification@{name}", "task": CARD,
            "stage": "in-progress", "check": name, "scope": None,
            "agent": "run-1", "actor": "agent", "intent": "work",
            "outcome": outcome, "reason": None, "why": f"{outcome} fixture",
            "requested": time.time(), "started": time.time() - 2,
            "ended": time.time(), "duration": 2.0, "rc": None,
            "command": shlex.join([sys.executable]),
            "argv": [sys.executable],
            "root": {"policy": "worktree", "path": str(self.tree)},
            "source": dict(self.bound),
            # the program and runner identities the runner records, so a
            # fixture's pass is bound the way a real one is (review R2)
            "program": verify.program_identity(
                [sys.executable], self.tree, verify._search_path()),
            "prepare": [], "location": "local",
            "runner": {"module": "verify", "version": 1,
                       "revision": verify.RUNNER_REVISION},
            "log": str(verify.log_path_of(f"ev:verification@{name}")),
        }
        record.update(extra)
        return verify._store(record)

    def test_every_outcome_reaches_the_rollup_as_its_own_word(self):
        words = ["passed", "failed", "denied", "prepare-failed", "error",
                 "cancelled"]
        for i, outcome in enumerate(words):
            self._fixture(f"check{i}", outcome)
        rollup = verify.rollup(CARD)
        got = {r["check"]: r["outcome"] for r in rollup["rows"]}
        self.assertEqual(sorted(got.values()), sorted(words))
        for row in rollup["rows"]:
            self.assertIn(row["outcome"], verify.OUTCOMES)

    def test_a_pass_that_could_not_be_bound_reads_stale_not_passed(self):
        self._fixture("unit", "passed",
                      source={"commit": "a" * 40, "bound": False,
                              "sourceId": "src:unbound@x", "dirty": [],
                              "why": "too much untracked work to bind"})
        row = verify.rollup(CARD)["rows"][0]
        self.assertEqual(row["outcome"], "stale")
        self.assertEqual(row["recorded"], "passed")
        self.assertIn("too much untracked work",
                      " ".join(row["freshness"]["changed"]))

    def test_a_preparation_in_progress_is_its_own_state(self):
        self._fixture("unit", "preparing",
                      prepare=[{"name": "deps", "outcome": "prepared",
                                "required": True, "network": False}])
        rollup = verify.rollup(CARD)
        self.assertEqual(rollup["rows"][0]["outcome"], "preparing")
        self.assertTrue(rollup["rows"][0]["live"])
        self.assertEqual(rollup["rows"][0]["prepare"][0]["name"], "deps")

    def test_the_failing_rows_come_first(self):
        self._fixture("a", "passed")
        self._fixture("b", "failed")
        self._fixture("c", "cancelled")
        self.assertEqual([r["outcome"] for r in verify.rollup(CARD)["rows"]],
                         ["failed", "cancelled", "passed"])

    def test_agent_finished_but_unverified_is_visible_as_not_run(self):
        self.declares(self.one_check())
        self.worktree()
        manifest = self.manifest()
        rollup = verify.rollup(CARD, manifest)
        self.assertEqual(rollup["rows"][0]["outcome"], "not-run")
        self.assertFalse(rollup["verified"])
        self.assertIn("nothing has asked for it", rollup["rows"][0]["why"])

    def test_the_card_reading_names_the_worst_thing_and_where_it_lives(self):
        self._fixture("a", "passed")
        self._fixture("b", "failed")
        collected = {"stages": [{"slug": "in-progress",
                                 "tasks": [{"file": CARD}]}]}
        board = verify.board(collected)
        self.assertEqual(board[CARD]["outcome"], "failed")
        self.assertEqual(board[CARD]["checks"], 2)
        self.assertEqual(board[CARD]["passed"], 1)
        self.assertEqual(board[CARD]["location"], "local")

    def test_a_card_with_nothing_recorded_has_no_reading_at_all(self):
        collected = {"stages": [{"slug": "in-progress",
                                 "tasks": [{"file": CARD}]}]}
        self.assertEqual(verify.board(collected), {})

    def test_the_public_record_carries_no_environment_values(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        config._ENV["GH_TOKEN"] = "ghp_leakcanary"
        self.addCleanup(config._ENV.pop, "GH_TOKEN", None)
        found = verify.public(self.ask("unit"))
        text = json.dumps(found)
        self.assertNotIn("ghp_leakcanary", text)
        self.assertNotIn("PATH=", text)
        self.assertIn("removed", found["env"])
        self.assertTrue(all(isinstance(n, str) for n in found["env"]["removed"]))

    def test_a_log_is_scrubbed_of_anything_credential_shaped(self):
        self.declares(self.one_check(run=str(self.executable("leaks", LEAKS))))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        shown = verify.log_text(found["id"])
        self.assertTrue(shown["scrubbed"])
        self.assertNotIn("ghp_01234567890abcdefghijABCDEFGHIJ", shown["text"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz0123", shown["text"])
        self.assertIn("«redacted»", shown["text"])
        self.assertIn("the tests passed", shown["text"],
                      "the rest of the output still reaches the reader")

    def test_the_evidence_outlives_the_worktree_it_ran_in(self):
        self.declares(self.one_check())
        tree = self.worktree()
        self.manifest()
        found = self.ask("unit")
        git(self.repo, "worktree", "remove", "--force", str(tree))
        self.assertFalse(tree.exists())
        again = verify.read(found["id"])
        self.assertEqual(again["outcome"], "passed")
        self.assertEqual(verify.outcome_now(again)["outcome"], "stale",
                         "and a pass whose checkout is gone is not reusable")

    def test_the_evidence_survives_a_board_restart(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        verify.RUNNING.clear()
        verify._KEYS.clear()
        self.assertEqual(verify.read(found["id"])["outcome"], "passed")
        self.assertEqual([r["id"] for r in verify.for_task(CARD)], [found["id"]])
        self.assertEqual([r["id"] for r in verify.for_agent("run-1")],
                         [found["id"]])

    def test_the_ticker_line_names_the_check_the_outcome_and_the_commit(self):
        self.declares(self.one_check(run=str(self.executable("fails", FAILS))))
        self.worktree()
        self.manifest()
        found = self.ask("unit")
        line = verify.line(found)
        self.assertIn("unit FAILED", line)
        self.assertIn(found["source"]["commit"][:12], line)
        summaries = " ".join(e.get("summary", "") for e in state.BOARD_EVENTS)
        self.assertIn("unit FAILED", summaries)
        self.assertTrue(any(s.get("type") == "verify" for s in self.sent))


# ── the api the page and the shim both use ─────────────────────────────


class TheChipAppearsBeforeTheFirstRun(VerifyCase):
    """The card's ◫ reading on a board whose policy offers a check that
    nothing has asked for yet. The sheet behind the chip lists it as
    `not-run` and arms its rerun; without the chip there is no way to
    reach that sheet, so the first run could never be asked for."""

    COLLECTED = {"stages": [{"slug": "in-progress", "tasks": [{"file": CARD}]}]}

    _fixture = TheBoardShowsWhatRan._fixture

    def setUp(self):
        super().setUp()
        self.tree = self.worktree()
        self.bound = verify.source_identity(self.tree)

    def chip(self) -> dict | None:
        return verify.board(self.COLLECTED).get(CARD)

    def two_checks(self) -> dict:
        recorder = str(self.executable("recorder", RECORDER))
        return {"v": 1,
                "checks": [{"name": "unit", "run": recorder, "root": "worktree"},
                           {"name": "lint", "run": recorder, "root": "worktree",
                            "required": False}],
                "profiles": {"work": {"verify": ["unit", "lint"]}}}

    def test_a_registered_unused_check_gives_the_card_a_not_run_chip(self):
        self.declares(self.one_check())
        found = self.chip()
        self.assertIsNotNone(found, "a check the card may ask for, and no chip "
                                    "to open the sheet that asks for it")
        self.assertEqual(found["outcome"], "not-run")
        self.assertEqual((found["passed"], found["checks"]), (0, 1))
        self.assertEqual(found["notRun"], 1)
        self.assertFalse(found["live"])
        self.assertEqual(self.invocations(), [], "reading the board ran a check")

    def test_optional_checks_count_and_a_pass_beside_them_is_not_a_pass(self):
        self.declares(self.two_checks())
        self.assertEqual((self.chip()["checks"], self.chip()["notRun"]), (2, 2))
        self._fixture("unit", "passed")
        verify.forget_cached()
        found = self.chip()
        self.assertEqual(found["outcome"], "not-run",
                         "one check never ran, so the card is not verified")
        self.assertEqual((found["passed"], found["checks"]), (1, 2))

    def test_names_are_counted_once_however_often_they_ran(self):
        self.declares(self.one_check())
        self._fixture("unit-old", "passed", check="unit", id="ev:verification@u1",
                      requested=time.time() - 50)
        self._fixture("unit-new", "passed", check="unit", id="ev:verification@u2")
        found = self.chip()
        self.assertEqual((found["passed"], found["checks"]), (1, 1))
        self.assertEqual(found["notRun"], 0)

    def test_a_failure_stale_or_running_record_still_outranks_not_run(self):
        self.declares(self.two_checks())
        for outcome in ("failed", "running"):
            self._fixture("unit", outcome)
            verify.forget_cached()
            self.assertEqual(self.chip()["outcome"], outcome)
        self._fixture("unit", "passed",
                      source={"commit": "a" * 40, "bound": False,
                              "sourceId": "src:unbound@x", "dirty": [],
                              "why": "unbound"})
        verify.forget_cached()
        self.assertEqual(self.chip()["outcome"], "stale")

    def test_current_policy_counts_even_after_an_old_run_froze_none(self):
        # a completed run launched under a policy with no checks at all
        self.manifest()
        self.declares(self.one_check())
        found = self.chip()
        self.assertIsNotNone(found)
        self.assertEqual(found["outcome"], "not-run")
        # ...and that run's frozen policy still decides what *it* may ask
        refused = self.ask("unit")
        self.assertEqual(refused["outcome"], "denied")
        self.assertEqual(refused["reason"], "unknown-check")
        self.assertEqual(self.invocations(), [])

    def test_a_config_change_is_read_without_waiting_for_the_cache(self):
        self.assertIsNone(self.chip())
        self.declares(self.one_check())
        self.assertEqual(self.chip()["checks"], 1)
        self.declares(self.two_checks())
        self.assertEqual(self.chip()["checks"], 2)
        (self.local / "profiles.json").unlink()
        self.assertIsNone(self.chip())

    def test_legacy_unconfigured_and_invalid_boards_offer_nothing(self):
        self.assertIsNone(self.chip(), "legacy: no document, no chip")
        self.declares({"v": 1, "profiles": {"work": {}}})
        self.assertIsNone(self.chip(), "a document declaring no check")
        self.declares("{ not json")
        self.assertIsNone(self.chip(), "a broken document offers nothing a "
                                       "manual request would not refuse")
        self.declares({"v": 1, "checks": [{"name": "unit", "run": "a | b"}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.assertIsNone(self.chip(), "an invalid check is refused, not offered")

    def test_a_source_check_is_offered_whatever_stage_the_card_is_in(self):
        # a `source` check needs no card worktree, and a done or to-do card
        # may keep one — so no stage hides what the current policy offers
        self.declares(self.one_check(root="source"))
        for slug in ("backlog", "to-do", "in-progress", "review", "done"):
            collected = {"stages": [{"slug": slug, "tasks": [{"file": CARD}]}]}
            found = verify.board(collected).get(CARD)
            self.assertIsNotNone(found, slug)
            self.assertEqual((found["outcome"], found["passed"],
                              found["checks"]), ("not-run", 0, 1), slug)
        self.assertEqual(self.invocations(), [], "reading the board ran a check")
        self.assertEqual(verify.all_evidence(), [])

    def test_a_legacy_board_leaves_every_stage_uncluttered(self):
        for slug in ("backlog", "done"):
            collected = {"stages": [{"slug": slug, "tasks": [{"file": CARD}]}]}
            self.assertEqual(verify.board(collected), {}, slug)

    def test_reading_the_board_creates_no_checkout_and_no_record(self):
        doc = self.one_check(root="source")
        self.declares(doc)
        before = sorted(p.name for p in config.WORKTREES.iterdir())
        self.assertEqual(self.chip()["outcome"], "not-run")
        self.assertEqual(sorted(p.name for p in config.WORKTREES.iterdir()), before)
        self.assertEqual(verify.all_evidence(), [])
        self.assertFalse(config.CONTEXT_DIR.exists(), "nothing was pinned")


class TheApiIsOneDoor(VerifyCase):
    def test_the_state_payload_carries_the_per_card_reading(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        self.ask("unit")
        payload = httpd.state_payload()
        self.assertIn(CARD, payload["verify"])
        self.assertEqual(payload["verify"][CARD]["outcome"], "passed")

    def test_the_evidence_endpoint_lists_checks_that_never_ran(self):
        self.declares(self.one_check())
        self.worktree()
        self.manifest()
        rollup = verify.rollup(CARD, profiles.manifest("work", CARD, "in-progress"))
        self.assertEqual([r["outcome"] for r in rollup["rows"]], ["not-run"])

    def test_the_checks_endpoint_answers_from_the_frozen_manifest(self):
        self.declares(self.one_check(scopes={"fast": ["-k", "fast"]}))
        self.worktree()
        frozen = self.manifest()
        offered = verify.available(frozen)
        self.assertEqual([c["name"] for c in offered], ["unit"])
        self.assertEqual(sorted(offered[0]["scopes"]), ["fast"])

    def _serve(self):
        """A real board on a real port, so the shim can be run the way a
        headless agent runs it — no interactive prompt anywhere in it."""
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        self.addCleanup(setattr, state, "serve_port", state.serve_port)
        state.serve_port = port
        return port

    def _shim(self, port: int, *args: str, agent: str = "run-1"):
        env = dict(os.environ, BOARD_PORT=str(port), BOARD_AGENT_ID=agent)
        return subprocess.run([str(profiles.verify_shim()), *args],
                              capture_output=True, text=True, env=env,
                              timeout=120)

    def test_the_shim_runs_a_check_over_the_loopback_and_exits_zero(self):
        self.declares(self.one_check(scopes={"fast": ["-k", "fast"]}))
        self.worktree()
        self.manifest()
        port = self._serve()

        listing = self._shim(port)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("unit", listing.stdout)
        self.assertIn("fast", listing.stdout)

        ran = self._shim(port, "unit", "fast")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertIn("PASSED: unit", ran.stdout)
        self.assertIn("ev:verification@", ran.stdout)
        self.assertEqual(self.invocations()[0]["argv"], ["-k", "fast"])
        # and the board recorded it before the shim was told anything
        [record] = verify.for_task(CARD)
        self.assertEqual(record["outcome"], "passed")
        self.assertIn(record["id"], ran.stdout)

    def test_the_shim_exits_one_only_for_a_check_that_said_no(self):
        self.declares(self.one_check(run=str(self.executable("fails", FAILS))))
        self.worktree()
        self.manifest()
        port = self._serve()
        failed = self._shim(port, "unit")
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("FAILED: unit", failed.stdout)

        refused = self._shim(port, "coverage")
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertIn("DENIED: coverage", refused.stdout)

    def test_the_shim_says_so_rather_than_guessing_when_asked_by_nobody(self):
        port = self._serve()
        stray = self._shim(port, "unit", agent="")
        self.assertEqual(stray.returncode, 5)
        self.assertIn("BOARD_AGENT_ID", stray.stderr)
        unreachable = subprocess.run(
            [str(profiles.verify_shim()), "unit"], capture_output=True,
            text=True, timeout=60,
            env=dict(os.environ, BOARD_PORT="1", BOARD_AGENT_ID="run-1"))
        self.assertEqual(unreachable.returncode, 5)
        self.assertIn("nothing was verified", unreachable.stderr)

    def test_the_shim_is_shipped_runnable_and_stdlib_only(self):
        shim = profiles.verify_shim()
        self.assertTrue(shim.is_file())
        self.assertTrue(os.access(shim, os.X_OK))
        text = shim.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env python3"))
        for forbidden in ("import requests", "import yaml", "pip install"):
            self.assertNotIn(forbidden, text)

    def test_the_shim_exit_codes_name_each_kind_of_answer(self):
        text = profiles.verify_shim().read_text(encoding="utf-8")
        for outcome in ("passed", "failed", "denied", "prepare-failed",
                        "error", "cancelled"):
            self.assertIn(f'"{outcome}"', text)
        self.assertIn("never, by itself, a test", text)

    def test_the_shipped_example_document_parses_and_resolves(self):
        """The documentation is a real document: it is copied, edited and
        run by every operator who configures this, so a `_doc` key or a
        check that does not validate is a bug in the feature."""
        example = (REPO / "manager" / "core" / "profiles.example.json")
        data = json.loads(example.read_text(encoding="utf-8"))
        self.assertEqual(data["v"], profiles.PROFILE_VERSION)
        # pointed at this test's own roots, with the example's own paths
        # replaced by files that exist here — the shape is what is on test
        data["context"] = []
        for profile in data["profiles"].values():
            profile.pop("context", None)
        recorder = str(self.executable("recorder", RECORDER))
        for check in data["checks"]:
            if check["run"].startswith("python3 -m unittest"):
                check["run"] = recorder
        for step in data["prepare"]:
            step["run"] = recorder
        data["requires"] = [r for r in data["requires"]
                            if r != "pinned-context"]
        self.declares(data)
        self.worktree()
        for intent in profiles.INTENTS:
            with self.subTest(intent=intent):
                found = profiles.manifest(intent, CARD, "in-progress")
                self.assertEqual(found["blockers"], [])
                if intent == "refine":
                    # a refinement asks for no check, by construction
                    self.assertEqual(found["verify"], [])
                    continue
                self.assertTrue(found["verify"],
                                "the example declares a check for every intent")

    def test_the_seams_name_the_runner_that_now_exists(self):
        seams = profiles.seams()
        self.assertIn("verify.py", seams["extend"]["a runner"])
        self.assertIn("verify.for_task", seams["extend"]["a gate"])
        # The gate this card left as a later member is card 71's
        # `gates.py`, so `verified-outputs` is no longer pending — it is a
        # capability a board that declares a gate genuinely has, and the
        # seam names the module that provides it.
        self.assertIn("gates.py", seams["extend"]["a gate"])
        self.assertNotIn("verified-outputs", profiles.PENDING_CAPABILITIES)
        self.assertIn("verified-outputs", profiles.BENCH_CAPABILITIES)
        self.assertNotIn("prepared-inputs", profiles.PENDING_CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
