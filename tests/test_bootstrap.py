"""Bootstrapping an external workspace and handing off to the board (task 87).

`bootstrap.py` discovers, previews and then — only with approval — makes
the operations that set a workspace up, running `install.py` for every
part that installs. Each test builds a disposable workspace (an
application repository, a tool install copied from this checkout, and
where it matters a work repository and a bare "remote"), runs the real
script as a subprocess with no terminal on stdin, and reads the disk
afterwards. External providers are stubs: `gh` is a script on PATH that
records its arguments, remotes are bare repositories on disk, and no
agent CLI or model is ever invoked.

    python3 -m unittest tests.test_bootstrap -v
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
STAGES = ("backlog", "to-do", "in-progress", "review", "done")
TOKEN = "ghp_" + "Z" * 36          # shaped like a GitHub token; never real


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def snapshot(*roots: Path) -> dict:
    """Every file under the roots with its bytes and mode — the whole
    observable state "writes nothing" is a claim about."""
    seen = {}
    for root in roots:
        if not root.exists():
            seen[str(root)] = None
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                seen[str(path)] = (path.read_bytes(), path.stat().st_mode)
            elif path.is_dir():
                seen[str(path)] = "dir"
    return seen


def install_tool(target: Path) -> None:
    shutil.copytree(BENCH / "manager" / "core", target / "manager" / "core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for keep in ("adapters", "prompts", "commands", "driver"):
        (target / "manager" / "local" / keep).mkdir(parents=True)
    for name in ("install.py", "bootstrap.py", "start.sh"):
        shutil.copy2(BENCH / name, target / name)


class Workspace(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-boot-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = self.tmp / "ws"
        self.tool = self.ws / "bench"
        install_tool(self.tool)
        self.app = self.ws / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.app)],
                       check=True, capture_output=True)
        git(self.app, "config", "user.name", "appdev")
        git(self.app, "config", "user.email", "appdev@example.test")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.app, "add", "code.txt")
        git(self.app, "commit", "-q", "-m", "the application")
        self.work = self.ws / "work"
        self.trees = self.ws / "worktrees"
        self.env_file = self.tool / "manager" / "local" / ".env"
        self.handoff_file = (self.tool / "manager" / "local" / "state"
                             / "bootstrap" / "handoff.json")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.port = free_port()

    # — running it —

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "GIT_", "GH_"))}
        env.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "BOARD_PORT": str(self.port),
            # The CLIs are looked up by these names; none of them exists,
            # so no test can reach a real agent or GitHub by accident.
            "BOARD_GH_BIN": "bench-test-gh",
            "BOARD_CLAUDE_BIN": "bench-test-claude",
            "BOARD_OPENCODE_BIN": "bench-test-opencode",
            "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "tester",
            "GIT_COMMITTER_EMAIL": "t@example.test",
        })
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def boot(self, *args: str, env: dict | None = None
             ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tool / "bootstrap.py"), *args],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=env or self.env(), stdin=subprocess.DEVNULL, timeout=120)

    def local_apply(self, *extra: str, **kw) -> subprocess.CompletedProcess:
        return self.boot("apply", "--app", str(self.app), "--work-repo",
                         "new", "--agent", "claude", "--behaviour", "solo",
                         "--yes", *extra, **kw)

    def handoff(self) -> dict:
        return json.loads(self.handoff_file.read_text(encoding="utf-8"))

    def env_values(self) -> dict:
        values = {}
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        return values

    def app_state(self) -> tuple:
        return (git(self.app, "rev-parse", "HEAD").stdout,
                git(self.app, "status", "--porcelain",
                    "--ignored").stdout,
                git(self.app, "ls-files").stdout,
                sorted(p.name for p in self.app.iterdir()))

    def gh_stub(self, script: str) -> None:
        stub = self.bin / "bench-test-gh"
        stub.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        stub.chmod(0o755)

    def assertOk(self, result, code=0):
        self.assertEqual(result.returncode, code,
                         result.stdout + result.stderr)


class InspectionWritesNothing(Workspace):

    def test_inspect_and_plan_write_nothing_and_run_no_project_code(self):
        trap = self.tmp / "trap-ran"
        (self.app / "package.json").write_text(json.dumps({"scripts": {
            "test": f"touch {trap}", "start": f"touch {trap}"}}),
            encoding="utf-8")
        (self.app / "Makefile").write_text(f"test:\n\ttouch {trap}\n",
                                           encoding="utf-8")
        hook = self.tmp / "fsmonitor"
        hook.write_text(f"#!/bin/sh\ntouch {trap}\n", encoding="utf-8")
        hook.chmod(0o755)
        git(self.app, "config", "core.fsmonitor", str(hook))
        before = snapshot(self.ws)

        inspected = self.boot("inspect", "--app", str(self.app), "--json")
        self.assertOk(inspected)
        planned = self.boot("plan", "--app", str(self.app), "--work-repo",
                            "new", "--agent", "claude", "--behaviour",
                            "solo")
        self.assertOk(planned)
        self.assertIn("preview — nothing written", planned.stdout)

        self.assertEqual(snapshot(self.ws), before,
                         "inspection and preview left the disk as it was")
        self.assertFalse(trap.exists(), "no project script, Makefile target "
                                        "or repository-configured program "
                                        "was run")
        doc = json.loads(inspected.stdout)
        self.assertEqual((doc["kind"], doc["v"]),
                         ("bench-bootstrap-handoff", 1))
        names = {(d["file"], d["name"])
                 for d in doc["discovery"]["declarations"]["scripts"]}
        self.assertIn(("package.json", "test"), names)
        self.assertIn(("Makefile", "test"), names)
        self.assertEqual(doc["discovery"]["app"]["repo"]["branch"], "main")

    def test_missing_choices_are_reported_without_hanging(self):
        result = self.boot("apply", "--app", str(self.app), "--json")
        self.assertOk(result, 2)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "needs-choices")
        self.assertEqual({r["choice"] for r in doc["required"]},
                         {"workRepo", "agent", "behaviour"})
        self.assertFalse(self.env_file.exists())
        self.assertFalse(self.work.exists())

    def test_no_application_is_a_required_choice(self):
        result = self.boot("plan", "--json")
        self.assertOk(result, 2)
        self.assertIn("app", {r["choice"]
                              for r in json.loads(result.stdout)["required"]})

    def test_apply_without_approval_writes_nothing(self):
        before = snapshot(self.ws)
        result = self.boot("apply", "--app", str(self.app), "--work-repo",
                           "new", "--agent", "claude", "--behaviour", "solo")
        self.assertOk(result, 2)
        self.assertIn("--yes", result.stdout)
        self.assertEqual(snapshot(self.ws), before)


class FreshExternalLayout(Workspace):

    def test_a_fresh_local_only_layout_reaches_a_working_board(self):
        before = self.app_state()
        result = self.local_apply()
        self.assertOk(result)

        self.assertEqual(self.app_state(), before,
                         "the application is exactly as it was")
        for stage in STAGES:
            self.assertTrue((self.work / "tasks" / stage).is_dir())
        self.assertTrue((self.work / "tasks" / "task-template.md").is_file())
        self.assertEqual(git(self.work, "rev-parse", "--show-toplevel"
                             ).stdout.strip(), str(self.work))
        self.assertEqual(git(self.work, "remote").stdout.strip(), "",
                         "no remote is created by default")
        self.assertIn("bench: initialize work state",
                      git(self.work, "log", "--format=%s").stdout)
        for top in ("tasks", "plans", "reference"):
            self.assertFalse((self.tool / top).exists())

        values = self.env_values()
        self.assertEqual(values["BOARD_APP_ROOT"], str(self.app))
        self.assertEqual(values["BOARD_WORK_ROOT"], str(self.work))
        self.assertEqual(values["BOARD_WORKTREES"], str(self.trees))
        self.assertEqual(values["BOARD_AGENT_ADAPTER"], "claude")
        self.assertEqual(values["BOARD_AGENT_COMMANDS"], "",
                         "no agent command is inferred from a file name")
        self.assertEqual(values["BOARD_SYNC"], "")

        doc = self.handoff()
        self.assertEqual(doc["status"], "complete")
        self.assertEqual(doc["board"]["url"],
                         f"http://127.0.0.1:{self.port}/")
        self.assertFalse(doc["board"]["listening"])
        self.assertIn("http://127.0.0.1:", result.stdout)
        guide = {g["id"] for g in doc["guide"]}
        self.assertIn("agent-cli-missing", guide,
                      "a missing CLI is a guide item, not a failure")
        self.assertIn("agent-commands", guide)
        self.assertIn("remote-none", guide)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", self.port)),
                                0, "setup starts no listener by default")

        # And the board it set up actually serves.
        board = subprocess.Popen(
            [sys.executable, str(self.tool / "manager" / "core" / "board.py"),
             "--no-open", "--port", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=self.env())
        self.addCleanup(board.wait, 10)
        self.addCleanup(board.terminate)
        state = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/api/state",
                        timeout=2) as reply:
                    state = json.loads(reply.read())
                break
            except OSError:
                time.sleep(0.2)
        self.assertIsNotNone(state, "the board answered on loopback")

    def test_repeating_setup_is_idempotent(self):
        self.assertOk(self.local_apply())
        env_before = self.env_file.read_bytes()
        head = git(self.work, "rev-parse", "HEAD").stdout
        app_before = self.app_state()

        again = self.local_apply()
        self.assertOk(again)
        self.assertEqual(self.env_file.read_bytes(), env_before)
        self.assertEqual(git(self.work, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(self.app_state(), app_before)
        outcomes = {o["id"]: o["outcome"]
                    for o in self.handoff()["operations"]}
        self.assertEqual(outcomes.get("install"), "done")
        self.assertNotIn("work-git-init", outcomes,
                         "an existing repository is never reinitialized")

    def test_a_plain_folder_work_root_is_supported(self):
        result = self.boot("apply", "--app", str(self.app), "--work-repo",
                           "none", "--agent", "claude", "--behaviour",
                           "solo", "--yes")
        self.assertOk(result)
        self.assertFalse((self.work / ".git").exists())
        self.assertIn("work-unversioned",
                      {g["id"] for g in self.handoff()["guide"]})

    def test_team_without_a_repository_is_refused(self):
        result = self.boot("plan", "--app", str(self.app), "--work-repo",
                           "none", "--agent", "claude", "--behaviour",
                           "team")
        self.assertOk(result, 1)
        self.assertIn("team behaviour commits every move", result.stdout)


class ExistingWorkState(Workspace):

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "tickets.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)],
                       check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)],
                       check=True)
        git(self.work, "remote", "add", "origin", str(self.remote))
        (self.work / "tasks" / "backlog").mkdir(parents=True)
        self.card = self.work / "tasks" / "backlog" / "01-mine.md"
        self.card.write_text("# 01 — mine\n\n**Status:** Backlog\n",
                             encoding="utf-8")
        self.template = self.work / "tasks" / "task-template.md"
        self.template.write_text("my own template\n", encoding="utf-8")
        git(self.work, "add", "-A")
        subprocess.run(["git", "-C", str(self.work), "commit", "-q", "-m",
                        "my board"], check=True, env=self.env())
        self.env_file.write_text("BOARD_TITLE=mine\nBOARD_FOO=kept\n",
                                 encoding="utf-8")

    def apply(self, *extra):
        return self.boot("apply", "--app", str(self.app), "--work",
                         str(self.work), "--agent", "claude", "--behaviour",
                         "team", "--yes", *extra)

    def test_an_existing_repository_and_its_remote_are_used_as_they_are(self):
        work_head = git(self.work, "rev-parse", "HEAD").stdout
        result = self.apply()
        self.assertOk(result)
        self.assertEqual(git(self.work, "remote", "get-url", "origin"
                             ).stdout.strip(), str(self.remote))
        self.assertEqual(git(self.work, "rev-parse", "HEAD").stdout,
                         work_head, "no commit was made in it")
        self.assertEqual(self.card.read_text(encoding="utf-8"),
                         "# 01 — mine\n\n**Status:** Backlog\n")
        self.assertEqual(self.template.read_text(encoding="utf-8"),
                         "my own template\n")
        values = self.env_values()
        self.assertEqual(values["BOARD_TITLE"], "mine")
        self.assertEqual(values["BOARD_FOO"], "kept")
        self.assertEqual(values["BOARD_COMMIT_MOVES"], "1")
        self.assertEqual(values["BOARD_STATE_GIT_REMOTE"], "origin")
        self.assertNotIn("BOARD_AGENT_COMMANDS", values,
                         "an existing settings file gains only what was "
                         "chosen")
        self.assertEqual(git(self.remote, "for-each-ref").stdout, "",
                         "nothing is pushed unless --push says so")
        self.assertEqual(self.handoff()["choices"]["workRepo"], "existing")

    def test_the_first_push_is_its_own_choice(self):
        self.assertOk(self.apply("--push"))
        self.assertIn("refs/heads/main",
                      git(self.remote, "for-each-ref").stdout)

    def test_asking_to_reinitialize_or_replace_a_remote_is_refused(self):
        before = snapshot(self.ws)
        again = self.boot("plan", "--app", str(self.app), "--work",
                          str(self.work), "--work-repo", "new", "--agent",
                          "claude", "--behaviour", "team")
        self.assertOk(again, 1)
        self.assertIn("never reinitializes", again.stdout)
        replace = self.boot("plan", "--app", str(self.app), "--work",
                            str(self.work), "--agent", "claude",
                            "--behaviour", "team", "--add-remote",
                            "https://example.test/other.git")
        self.assertOk(replace, 1)
        self.assertIn("never adds or replaces", replace.stdout)
        self.assertEqual(snapshot(self.ws), before)


class Remotes(Workspace):

    def base(self, *extra: str) -> list[str]:
        return ["apply", "--app", str(self.app), "--work-repo", "new",
                "--agent", "claude", "--behaviour", "team", "--yes", *extra]

    def test_remote_creation_is_private_and_runs_the_stub(self):
        bare = self.tmp / "created.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                       check=True)
        record = self.tmp / "gh-args"
        # Records its arguments, then does what `gh repo create --source
        # --remote` does locally: adds the remote. No network.
        self.gh_stub(f'printf "%s\\n" "$@" > {record}\n'
                     f'git -C "$6" remote add origin {bare}\n')
        result = self.boot(*self.base("--create-remote", "me/tickets",
                                      "--push"))
        self.assertOk(result)
        args = record.read_text(encoding="utf-8").split()
        self.assertEqual(args[:4], ["repo", "create", "me/tickets",
                                    "--private"])
        self.assertIn("refs/heads/main", git(bare, "for-each-ref").stdout)
        self.assertEqual(self.env_values()["BOARD_STATE_GIT_REMOTE"],
                         "origin")

    def test_refused_remote_creation_leaves_a_working_local_board(self):
        self.gh_stub(f'echo "HTTP 403: token {TOKEN} lacks scope" >&2\n'
                     f"exit 1\n")
        result = self.boot(*self.base("--create-remote", "me/tickets"),
                           env=self.env(GH_TOKEN=TOKEN))
        self.assertOk(result)
        doc = self.handoff()
        self.assertEqual(doc["status"], "complete")
        outcome = {o["id"]: o for o in doc["operations"]}
        self.assertEqual(outcome["work-remote-create"]["outcome"], "failed")
        self.assertIn("remote-not-created", {g["id"] for g in doc["guide"]})
        self.assertTrue((self.work / "tasks" / "backlog").is_dir())
        self.assertEqual(git(self.work, "remote").stdout.strip(), "")
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertNotIn(TOKEN, self.handoff_file.read_text())

    def test_an_unavailable_cli_is_a_recoverable_result(self):
        result = self.boot(*self.base("--create-remote", "me/tickets"))
        self.assertOk(result)
        outcome = {o["id"]: o["outcome"]
                   for o in self.handoff()["operations"]}
        self.assertEqual(outcome["work-remote-create"], "unavailable")
        self.assertIn("gh auth login", result.stdout)

    def test_a_url_carrying_credentials_is_refused_and_never_echoed(self):
        url = f"https://x-access-token:{TOKEN}@github.com/me/t.git"
        before = snapshot(self.ws)
        result = self.boot(*self.base("--add-remote", url))
        self.assertOk(result, 1)
        self.assertIn("carrying credentials", result.stdout)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual(snapshot(self.ws), before)

    def test_secrets_in_the_applications_remote_never_reach_output(self):
        git(self.app, "remote", "add", "origin",
            f"https://x-access-token:{TOKEN}@github.com/me/app.git")
        inspected = self.boot("inspect", "--app", str(self.app), "--json")
        self.assertOk(inspected)
        self.assertNotIn(TOKEN, inspected.stdout)
        self.assertIn("https://***@github.com/me/app.git", inspected.stdout)
        result = self.boot("apply", "--app", str(self.app), "--work-repo",
                           "new", "--agent", "claude", "--behaviour",
                           "solo", "--yes", env=self.env(GH_TOKEN=TOKEN))
        self.assertOk(result)
        self.assertNotIn(TOKEN, result.stdout + self.handoff_file.read_text())


class UnsafeLayouts(Workspace):

    def refused(self, *args: str) -> subprocess.CompletedProcess:
        before = snapshot(self.ws)
        result = self.boot("apply", "--app", str(self.app), "--agent",
                           "claude", "--behaviour", "solo", "--work-repo",
                           "new", "--yes", *args)
        self.assertOk(result, 1)
        self.assertIn("Refused", result.stdout)
        self.assertEqual(snapshot(self.ws), before)
        return result

    def test_a_work_root_inside_the_application_is_refused(self):
        result = self.refused("--work", str(self.app / "tickets"))
        self.assertIn("inside the application", result.stdout)

    def test_worktrees_inside_the_work_root_are_refused(self):
        result = self.refused("--worktrees", str(self.work / "trees"))
        self.assertIn("inside the work root", result.stdout)

    def test_a_work_root_inside_a_foreign_repository_is_refused(self):
        other = self.ws / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        (other / "work").mkdir()
        result = self.refused("--work", str(other / "work"))
        self.assertIn("which it does not own", result.stdout)

    def test_an_application_that_is_not_a_repository_is_refused(self):
        plain = self.ws / "plain"
        plain.mkdir()
        before = snapshot(self.ws)
        result = self.boot("plan", "--app", str(plain), "--agent", "claude",
                           "--behaviour", "solo", "--work-repo", "new")
        self.assertOk(result, 1)
        self.assertIn("not a git repository", result.stdout)
        self.assertEqual(snapshot(self.ws), before)

    def test_an_install_configured_for_another_application_is_refused(self):
        self.assertOk(self.local_apply())
        other = self.ws / "app2"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        result = self.boot("plan", "--app", str(other), "--agent", "claude",
                           "--behaviour", "solo", "--work-repo", "new")
        self.assertOk(result, 1)
        self.assertIn("already manages", result.stdout)


class Recovery(Workspace):

    def test_a_failed_step_is_recorded_and_a_rerun_completes(self):
        real_install = self.tool / "install.py"
        saved = real_install.read_bytes()
        real_install.write_text("import sys\nprint('boom')\nsys.exit(1)\n",
                                encoding="utf-8")
        failed = self.local_apply()
        self.assertOk(failed, 1)
        doc = self.handoff()
        self.assertEqual(doc["status"], "incomplete")
        outcomes = {o["id"]: o["outcome"] for o in doc["operations"]}
        self.assertEqual(outcomes["install"], "failed")
        self.assertEqual(outcomes["settings"], "not-run")

        real_install.write_bytes(saved)
        progress = self.handoff_file.with_name("progress.json")
        state = json.loads(progress.read_text())
        state["status"] = "interrupted"
        progress.write_text(json.dumps(state))
        again = self.local_apply()
        self.assertOk(again)
        self.assertIn("resuming a setup interrupted", again.stdout)
        self.assertEqual(self.handoff()["status"], "complete")


class Vendored(Workspace):

    def setUp(self):
        super().setUp()
        self.vendored = self.app / ".task-manager"
        install_tool(self.vendored)
        (self.app / ".claude").mkdir()   # the claude adapter's vendored wire

    def test_vendored_setup_remains_supported(self):
        result = subprocess.run(
            [sys.executable, str(self.vendored / "bootstrap.py"), "apply",
             "--mode", "vendored", "--agent", "claude", "--behaviour",
             "solo", "--yes"],
            capture_output=True, text=True, env=self.env(),
            stdin=subprocess.DEVNULL, timeout=120)
        self.assertOk(result)
        self.assertTrue((self.vendored / "tasks" / "backlog").is_dir())
        values = {}
        for line in (self.vendored / "manager" / "local" / ".env"
                     ).read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key] = value
        self.assertEqual(values["BOARD_AGENT_ADAPTER"], "claude")
        self.assertNotIn("BOARD_APP_ROOT", {k for k, v in values.items()
                                            if v})

    def test_the_external_default_refuses_a_bench_inside_the_application(self):
        result = subprocess.run(
            [sys.executable, str(self.vendored / "bootstrap.py"), "plan",
             "--app", str(self.app), "--agent", "claude", "--behaviour",
             "solo", "--work-repo", "new"],
            capture_output=True, text=True, env=self.env(),
            stdin=subprocess.DEVNULL, timeout=120)
        self.assertOk(result, 1)
        self.assertIn("--mode vendored", result.stdout)


if __name__ == "__main__":
    unittest.main()
