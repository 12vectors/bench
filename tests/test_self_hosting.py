"""Layout resolution: the manager must start cleanly both vendored one
level inside a host repo (.task-manager/) and self-hosted, where this repo
is simultaneously the distribution and the project.

Stdlib only, like everything else here:

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
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADAPTER_FILES = ("wire", "emit.py")

EMIT_SUFFIX = "manager/core/adapters/claude/emit.py"
OLD_EMIT_CMD = ('python3 "$CLAUDE_PROJECT_DIR/.task-manager/'
                'manager/core/adapters/claude/emit.py"')


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        # resolve(): macOS tempdirs live behind the /var → /private/var
        # symlink, and both wire and git report resolved paths.
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


def make_manager(tm_root: Path) -> None:
    """A minimal manager tree at tm_root: install.py plus the claude
    adapter's wire and emit.py, copied from this repo."""
    adapter = tm_root / "manager" / "core" / "adapters" / "claude"
    adapter.mkdir(parents=True)
    for name in ADAPTER_FILES:
        shutil.copy(REPO / "manager" / "core" / "adapters" / "claude" / name,
                    adapter / name)
    shutil.copy(REPO / "install.py", tm_root / "install.py")


def run(cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    return subprocess.run([str(a) for a in cmd], cwd=str(cwd), env=env,
                          capture_output=True, text=True)


def wire(tm_root: Path, project: Path) -> subprocess.CompletedProcess:
    script = tm_root / "manager" / "core" / "adapters" / "claude" / "wire"
    return run([sys.executable, script, project], cwd=project)


def settings(project: Path) -> dict:
    return json.loads((project / ".claude" / "settings.json").read_text())


def our_hook_commands(cfg: dict) -> set:
    return {
        hook["command"]
        for groups in cfg.get("hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
        if "emit.py" in hook.get("command", "")
    }


class WireLayouts(TempDirTestCase):
    def test_self_hosted_paths_have_no_task_manager_segment(self):
        project = self.tmp / "bench"
        make_manager(project)
        (project / ".claude").mkdir()

        result = wire(project, project)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cfg = settings(project)
        self.assertEqual(cfg["plansDirectory"], "./plans")
        self.assertEqual(
            our_hook_commands(cfg),
            {f'python3 "$CLAUDE_PROJECT_DIR/{EMIT_SUFFIX}"'})

    def test_vendored_paths_keep_task_manager_segment(self):
        host = self.tmp / "host"
        make_manager(host / ".task-manager")
        (host / ".claude").mkdir()

        result = wire(host / ".task-manager", host)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cfg = settings(host)
        self.assertEqual(cfg["plansDirectory"], "./.task-manager/plans")
        self.assertEqual(
            our_hook_commands(cfg),
            {f'python3 "$CLAUDE_PROJECT_DIR/.task-manager/{EMIT_SUFFIX}"'})

    def test_stale_hardcoded_path_is_repaired_in_place(self):
        project = self.tmp / "bench"
        make_manager(project)
        (project / ".claude").mkdir()
        stale_hook = {"type": "command", "command": OLD_EMIT_CMD, "timeout": 5}
        (project / ".claude" / "settings.json").write_text(json.dumps({
            "plansDirectory": "./.task-manager/plans",
            "hooks": {"SessionStart": [{"hooks": [stale_hook]}],
                      "PreToolUse": [{"matcher": "Bash", "hooks": [stale_hook]}]},
        }))

        result = wire(project, project)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cfg = settings(project)
        self.assertEqual(cfg["plansDirectory"], "./plans")
        self.assertEqual(
            our_hook_commands(cfg),
            {f'python3 "$CLAUDE_PROJECT_DIR/{EMIT_SUFFIX}"'})
        hooks = [hook for group in cfg["hooks"]["SessionStart"]
                 for hook in group["hooks"]]
        self.assertEqual(len(hooks), 1, "stale hook must be replaced, not kept")

    def test_second_run_is_a_no_op(self):
        for project, tm_root in (
            (self.tmp / "bench", self.tmp / "bench"),
            (self.tmp / "host", self.tmp / "host" / ".task-manager"),
        ):
            with self.subTest(project=project.name):
                make_manager(tm_root)
                (project / ".claude").mkdir()
                wire(tm_root, project)
                before = (project / ".claude" / "settings.json").read_text()

                result = wire(tm_root, project)

                self.assertEqual(result.returncode, 0)
                self.assertIn("nothing to do", result.stdout)
                self.assertEqual(
                    (project / ".claude" / "settings.json").read_text(), before)

    def test_refuses_project_without_claude_dir(self):
        project = self.tmp / "bench"
        make_manager(project)

        result = wire(project, project)

        self.assertEqual(result.returncode, 1)
        self.assertIn("not a .claude-initialised project", result.stdout)


class InstallRootResolution(TempDirTestCase):
    def _git_init(self, path: Path) -> None:
        subprocess.run(["git", "init", "--quiet", str(path)], check=True,
                       capture_output=True)

    def test_vendored_resolves_host_repo_root(self):
        host = self.tmp / "host"
        make_manager(host / ".task-manager")
        self._git_init(host)
        (host / ".claude").mkdir()

        result = run([sys.executable, host / ".task-manager" / "install.py"],
                     cwd=host)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(settings(host)["plansDirectory"],
                         "./.task-manager/plans")

    def test_self_hosted_resolves_own_repo_not_parent(self):
        project = self.tmp / "bench"
        make_manager(project)
        self._git_init(project)
        (project / ".claude").mkdir()

        result = run([sys.executable, project / "install.py"], cwd=project)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(settings(project)["plansDirectory"], "./plans")
        self.assertFalse((self.tmp / ".claude").exists(),
                         "must not wire the parent of the repo")

    def test_no_git_falls_back_to_parent(self):
        host = self.tmp / "host"
        make_manager(host / ".task-manager")
        (host / ".claude").mkdir()

        result = run([sys.executable, host / ".task-manager" / "install.py"],
                     cwd=host)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(settings(host)["plansDirectory"],
                         "./.task-manager/plans")

    def test_refusal_names_the_resolved_root(self):
        project = self.tmp / "bench"
        make_manager(project)
        self._git_init(project)

        result = run([sys.executable, project / "install.py"], cwd=project)

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{project} is not a .claude-initialised project",
                      result.stdout)


class VirginBoot(TempDirTestCase):
    """board.py must boot and serve from a checkout with no local/state/
    and no wiring at all — start.sh tolerates a failed wire on purpose."""

    def test_board_boots_without_state_dirs_or_wiring(self):
        tm_root = self.tmp / "bench"
        shutil.copytree(REPO / "manager" / "core",
                        tm_root / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (tm_root / "manager" / "local").mkdir()
        for stage in ("backlog", "to-do", "in-progress", "review", "done"):
            (tm_root / "tasks" / stage).mkdir(parents=True)

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        proc = subprocess.Popen(
            [sys.executable, str(tm_root / "manager" / "core" / "board.py"),
             "--port", str(port), "--no-open"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            state = None
            for _ in range(50):
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/state",
                            timeout=1) as response:
                        state = json.load(response)
                    break
                except OSError:
                    time.sleep(0.2)

            if proc.poll() is not None:
                out, err = proc.communicate()
                self.fail(f"board died (rc={proc.returncode}):\n{out}\n{err}")
            self.assertIsNotNone(state, "board never answered /api/state")
            local_state = tm_root / "manager" / "local" / "state"
            self.assertTrue((local_state / "sessions").is_dir())
            self.assertTrue((local_state / "agent").is_dir())
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
