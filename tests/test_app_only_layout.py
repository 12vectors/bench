"""An explicit application with the default work root (task 59).

`BOARD_APP_ROOT` alone is a supported layout: the application is named,
and the work state stays where AGENTS.md says it defaults to — the tool
installation. `state_repo()` used to read "no `BOARD_WORK_ROOT`" as
"vendored" and answer with the *application's* repository, so
`problems()` refused the layout with a false diagnosis ("the work root
resolves into the application's own repository") and, had the refusal
been lifted, the board's bookkeeping would have gone into application git.

These cases pin the corrected reading against a disposable tool install
copied out of this checkout, a disposable application repository, and —
where a case needs one — a tool installation that is its own repository.
Every run is a real subprocess, as a real install configures itself, and
nothing touches a remote or spends a token.

    python3 -m unittest discover -s tests -v
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
from pathlib import Path
from unittest import mock

BENCH = Path(__file__).resolve().parents[1]
STAGES = ("backlog", "to-do", "in-progress", "review", "done")

# Prints what the tool's own config resolves to, under whatever
# environment the subprocess was given.
PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import config
state = config.state_repo()
print(json.dumps({
    "external": config.EXTERNAL,
    "app": str(config.REPO),
    "work": str(config.work_root()),
    "tasks": str(config.TASKS),
    "state": str(state) if state else None,
    "problems": config.problems(),
}))
"""

# A board-made move, committed if the settings say so.
MOVE = """
import sys
sys.path.insert(0, sys.argv[1])
import config, taskfiles
if config.problems():
    raise SystemExit("refused: " + "; ".join(config.problems()))
taskfiles.move_task(sys.argv[2], "backlog", "to-do")
"""

CARD = "01-a-card.md"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def git_init(path: Path, name: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True)
    git(path, "config", "user.name", name)
    git(path, "config", "user.email", f"{name}@example.test")
    return path


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


class AppOnlyLayout(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-apponly-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # "A tool in no repository" has to mean that wherever the temp
        # directory is. Under bench's own runner TMPDIR sits inside the
        # operator's install, itself a git checkout: without a ceiling git
        # finds that repository, these cases read the wrong layout, and a
        # committed move lands in the operator's history.
        self.enterContext(mock.patch.dict(
            os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)}))

        self.tool = self.tmp / "bench"
        shutil.copytree(BENCH / "manager" / "core",
                        self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local" / "adapters").mkdir(parents=True)
        (self.tool / "manager" / "local" / "prompts").mkdir()
        shutil.copy(BENCH / "install.py", self.tool / "install.py")
        (self.tool / "tasks").mkdir()
        shutil.copy(BENCH / "tasks" / "task-template.md",
                    self.tool / "tasks" / "task-template.md")

        self.app = git_init(self.tmp / "app", "appdev")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        (self.app / ".claude").mkdir()
        git(self.app, "add", "code.txt")
        git(self.app, "commit", "-q", "-m", "the application")
        self.app_head = git(self.app, "rev-parse", "HEAD").stdout.strip()

    # — helpers —

    def core(self, tool: Path | None = None) -> str:
        return str((tool or self.tool) / "manager" / "core")

    def install(self, *args: str, tool: Path | None = None,
                **env) -> subprocess.CompletedProcess:
        tool = tool or self.tool
        return subprocess.run(
            [sys.executable, str(tool / "install.py"), *args],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=clean_env(**env), stdin=subprocess.DEVNULL)

    def resolve(self, tool: Path | None = None, **env) -> dict:
        env.setdefault("BOARD_APP_ROOT", self.app)
        result = subprocess.run(
            [sys.executable, "-c", PROBE, self.core(tool)],
            capture_output=True, text=True, env=clean_env(**env))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def skeleton(self, root: Path) -> None:
        for slug in (*STAGES, "archive"):
            (root / "tasks" / slug).mkdir(parents=True, exist_ok=True)
        (root / "plans").mkdir(exist_ok=True)

    def card(self, root: Path) -> Path:
        path = root / "tasks" / "backlog" / CARD
        path.write_text("# A card\n\n**Status:** Backlog\n**Priority:** Low\n",
                        encoding="utf-8")
        return path

    def assert_app_untouched(self) -> None:
        self.assertEqual(git(self.app, "rev-parse", "HEAD").stdout.strip(),
                         self.app_head, "application git gained a commit")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout, "",
                         "the application's working tree changed")
        self.assertFalse((self.app / "tasks").exists())

    # — where the tickets are —

    def test_tickets_resolve_under_the_tool_not_the_application(self):
        self.skeleton(self.tool)
        layout = self.resolve()
        self.assertTrue(layout["external"])
        self.assertEqual(layout["app"], str(self.app))
        self.assertEqual(layout["work"], str(self.tool))
        self.assertEqual(layout["tasks"], str(self.tool / "tasks"))

    def test_a_tool_in_no_repository_has_no_state_repository(self):
        """The case the old code answered with the application."""
        self.skeleton(self.tool)
        layout = self.resolve()
        self.assertIsNone(layout["state"])
        self.assertNotEqual(layout["state"], str(self.app))

    # — local-only: a supported layout —

    def test_local_only_is_not_refused(self):
        self.skeleton(self.tool)
        layout = self.resolve()
        self.assertEqual(layout["problems"], [])
        self.assertFalse(any("resolves into the application" in p
                             for p in layout["problems"]))

    def test_install_with_only_an_application_succeeds(self):
        result = self.install("--app", str(self.app))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for slug in STAGES:
            self.assertTrue((self.tool / "tasks" / slug).is_dir(), slug)
        env = (self.tool / "manager" / "local" / ".env").read_text("utf-8")
        self.assertIn(f"BOARD_APP_ROOT={self.app}", env)
        self.assertNotIn(f"BOARD_WORK_ROOT={self.tool}", env)
        self.assert_app_untouched()

    def test_the_board_starts_for_that_layout(self):
        self.skeleton(self.tool)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        board = subprocess.Popen(
            [sys.executable, str(self.tool / "manager" / "core" / "board.py"),
             "--no-open", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=clean_env(BOARD_APP_ROOT=self.app, PYTHONUNBUFFERED="1"),
            stdin=subprocess.DEVNULL)
        self.addCleanup(board.stdout.close)
        self.addCleanup(board.wait, 10)
        self.addCleanup(board.kill)
        seen: list[str] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = board.stdout.readline()
            if not line:
                break
            seen.append(line)
            if "Ctrl-C to stop" in line:
                break
        output = "".join(seen)
        self.assertIn(f"Task board for {self.tool / 'tasks'}", output)
        self.assertIn("no git — the board commits nothing", output)
        self.assertNotIn("cannot serve", output)
        self.assert_app_untouched()

    # — commit and sync: the actual work-root repository, or a refusal —

    def test_committing_moves_with_no_state_repository_is_refused_accurately(self):
        self.skeleton(self.tool)
        problems = self.resolve(BOARD_COMMIT_MOVES="1")["problems"]
        self.assertEqual(len(problems), 1, problems)
        problem = problems[0]
        self.assertIn("BOARD_COMMIT_MOVES", problem)
        self.assertIn("tool installation", problem)
        self.assertIn(str(self.tool), problem)
        self.assertIn("BOARD_WORK_ROOT", problem, "the setting that fixes it")
        self.assertNotIn("resolves into the application", problem,
                         "the old, false diagnosis")

    def test_syncing_with_no_state_repository_is_refused_accurately(self):
        self.skeleton(self.tool)
        problems = self.resolve(BOARD_SYNC="1")["problems"]
        self.assertTrue(any("BOARD_SYNC" in p and "BOARD_WORK_ROOT" in p
                            for p in problems), problems)
        self.assertFalse(any("resolves into the application" in p
                             for p in problems), problems)

    def test_a_refused_move_leaves_the_application_alone(self):
        self.skeleton(self.tool)
        card = self.card(self.tool)
        result = subprocess.run(
            [sys.executable, "-c", MOVE, self.core(), CARD],
            capture_output=True, text=True,
            env=clean_env(BOARD_APP_ROOT=self.app, BOARD_COMMIT_MOVES="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BOARD_COMMIT_MOVES", result.stderr)
        self.assertTrue(card.is_file(), "nothing moved")
        self.assert_app_untouched()

    def test_a_tool_that_is_a_repository_is_the_state_repository(self):
        git_init(self.tool, "boarder")
        self.skeleton(self.tool)
        layout = self.resolve(BOARD_COMMIT_MOVES="1")
        self.assertEqual(layout["state"], str(self.tool))
        self.assertEqual(layout["problems"], [])

    def test_a_committed_move_lands_in_the_tools_repository_only(self):
        git_init(self.tool, "boarder")
        self.skeleton(self.tool)
        self.card(self.tool)
        git(self.tool, "add", "tasks")
        git(self.tool, "commit", "-q", "-m", "tickets")
        before = git(self.tool, "rev-parse", "HEAD").stdout.strip()

        result = subprocess.run(
            [sys.executable, "-c", MOVE, self.core(), CARD],
            capture_output=True, text=True,
            env=clean_env(BOARD_APP_ROOT=self.app, BOARD_COMMIT_MOVES="1"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assertTrue((self.tool / "tasks" / "to-do" / CARD).is_file())
        self.assertNotEqual(git(self.tool, "rev-parse", "HEAD").stdout.strip(),
                            before)
        subject = git(self.tool, "log", "-1", "--format=%s").stdout.strip()
        self.assertTrue(subject.startswith("board: "), subject)
        self.assert_app_untouched()

    def test_a_tool_nested_in_another_repository_commits_there(self):
        """The work root's repository is whatever holds it — the same
        derivation the vendored layout has always used — and still never
        the application's."""
        host = git_init(self.tmp / "host", "hoster")
        nested = host / ".task-manager"
        shutil.move(str(self.tool), str(nested))
        self.skeleton(nested)
        layout = self.resolve(tool=nested, BOARD_COMMIT_MOVES="1")
        self.assertEqual(layout["work"], str(nested))
        self.assertEqual(layout["state"], str(host))
        self.assertEqual(layout["problems"], [])

    # — overlaps stay fail-closed, with the reason that is actually true —

    def test_a_tool_inside_the_application_is_refused_for_the_right_reason(self):
        nested = self.app / ".task-manager"
        shutil.move(str(self.tool), str(nested))
        self.skeleton(nested)
        problems = self.resolve(tool=nested)["problems"]
        self.assertTrue(problems, "a work root inside the application")
        self.assertTrue(any("inside the application" in p
                            and "BOARD_WORK_ROOT" in p
                            and "tool installation" in p for p in problems),
                        problems)
        self.assertFalse(any("BOARD_APP_ROOT and BOARD_WORK_ROOT" in p
                             for p in problems),
                         "BOARD_WORK_ROOT was never set")

    def test_the_tool_as_the_application_is_refused_for_the_right_reason(self):
        git_init(self.tool, "boarder")
        self.skeleton(self.tool)
        problems = self.resolve(BOARD_APP_ROOT=self.tool)["problems"]
        self.assertTrue(any("tool installation itself" in p for p in problems),
                        problems)
        self.assertTrue(any("bench itself sits in" in p for p in problems),
                        problems)
        self.assertFalse(any("BOARD_APP_ROOT and BOARD_WORK_ROOT" in p
                             for p in problems), problems)

    def test_an_application_inside_the_tool_is_refused_for_the_right_reason(self):
        inner = git_init(self.tool / "app", "appdev")
        self.skeleton(self.tool)
        problems = self.resolve(BOARD_APP_ROOT=inner)["problems"]
        self.assertTrue(any("inside the work root" in p
                            and "BOARD_WORK_ROOT" in p for p in problems),
                        problems)

    # — the other layouts keep what they had —

    def test_an_explicit_work_root_is_still_its_own_repository(self):
        work = git_init(self.tmp / "work", "worker")
        self.skeleton(work)
        layout = self.resolve(BOARD_WORK_ROOT=work, BOARD_COMMIT_MOVES="1")
        self.assertEqual(layout["work"], str(work))
        self.assertEqual(layout["state"], str(work))
        self.assertEqual(layout["problems"], [])

    def test_an_explicit_work_root_in_no_repository_keeps_its_wording(self):
        work = self.tmp / "work"
        self.skeleton(work)
        problems = self.resolve(BOARD_WORK_ROOT=work,
                                BOARD_COMMIT_MOVES="1")["problems"]
        self.assertTrue(any("git init" in p and str(work) in p
                            and "tool installation" not in p
                            for p in problems), problems)

    def test_a_vendored_install_still_commits_to_its_host(self):
        """No settings at all: the tool sits in the application and the
        two identities are one repository, exactly as before."""
        nested = self.app / ".task-manager"
        shutil.move(str(self.tool), str(nested))
        self.skeleton(nested)
        layout = self.resolve(tool=nested, BOARD_APP_ROOT="",
                              BOARD_COMMIT_MOVES="1")
        self.assertFalse(layout["external"])
        self.assertEqual(layout["app"], str(self.app))
        self.assertEqual(layout["state"], str(self.app))
        self.assertEqual(layout["problems"], [])


if __name__ == "__main__":
    unittest.main()
