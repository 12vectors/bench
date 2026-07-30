"""The adapters' permission generation: each launch intent is granted
exactly the side effects its own prompt demands, in the vendor's native
rule syntax. Run with: python3 -m unittest discover -s tests

The `run` scripts are exercised end-to-end against stub binaries
(BOARD_CLAUDE_BIN / BOARD_OPENCODE_BIN), the same seam a live board uses
— so what is asserted here is what a real launch passes to the vendor.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLAUDE = REPO / "manager" / "core" / "adapters" / "claude"
OPENCODE = REPO / "manager" / "core" / "adapters" / "opencode"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_settings = _load("hook_settings", CLAUDE / "hook_settings.py")
permission_config = _load("permission_config", OPENCODE / "permission_config.py")

COMMANDS = ["python3 -m unittest", "npm test"]


class ClaudeAllowRules(unittest.TestCase):
    def test_work_commits_and_tests_but_never_pushes(self):
        rules = hook_settings.allow_rules("work", COMMANDS)
        for prefix in ["git add", "git commit", "git status", "git diff",
                       "python3 -m unittest", "npm test"]:
            self.assertIn(f"Bash({prefix})", rules)
            self.assertIn(f"Bash({prefix}:*)", rules)
        joined = " ".join(rules)
        self.assertNotIn("git push", joined)
        self.assertNotIn("gh pr review", joined)

    def test_act_pr_is_work_plus_push_and_reading_the_pr(self):
        rules = hook_settings.allow_rules("act-pr", COMMANDS)
        work = hook_settings.allow_rules("work", COMMANDS)
        self.assertTrue(set(work) <= set(rules))
        for prefix in ["git push", "gh pr view", "gh pr diff", "gh api"]:
            self.assertIn(f"Bash({prefix}:*)", rules)

    def test_review_posts_verdicts_but_writes_nothing_locally(self):
        rules = hook_settings.allow_rules("review", COMMANDS)
        for prefix in ["gh pr review", "gh pr comment", "gh pr view",
                       "gh pr diff", "git log", "git diff"]:
            self.assertIn(f"Bash({prefix}:*)", rules)
        joined = " ".join(rules)
        for forbidden in ["git add", "git commit", "git push",
                          "python3 -m unittest", "npm test"]:
            self.assertNotIn(forbidden, joined)

    def test_settings_carry_hooks_and_allowlist_in_one_file(self):
        settings = hook_settings.settings("work", COMMANDS)
        self.assertIn("SessionStart", settings["hooks"])
        self.assertIn("PostToolUse", settings["hooks"])
        self.assertIn("Bash(git commit:*)", settings["permissions"]["allow"])

    def test_no_mode_means_hooks_only(self):
        self.assertNotIn("permissions", hook_settings.settings("", []))


class OpencodeConfig(unittest.TestCase):
    def test_work_allows_edits_commits_and_tests_only(self):
        config = permission_config.build_config("work", COMMANDS)
        self.assertEqual(config["permission"]["edit"], "allow")
        bash = config["permission"]["bash"]
        self.assertEqual(next(iter(bash)), "*")  # last match wins: deny first
        self.assertEqual(bash["*"], "deny")
        for prefix in ["git add", "git commit", "python3 -m unittest", "npm test"]:
            self.assertEqual(bash[prefix], "allow")
            self.assertEqual(bash[f"{prefix} *"], "allow")
        self.assertNotIn("git push *", bash)

    def test_act_pr_adds_push(self):
        bash = permission_config.build_config("act-pr", COMMANDS)["permission"]["bash"]
        self.assertEqual(bash["git push *"], "allow")
        self.assertEqual(bash["gh pr view *"], "allow")

    def test_review_cannot_edit_and_bash_default_denies(self):
        config = permission_config.build_config("review", COMMANDS)
        self.assertEqual(config["permission"]["edit"], "deny")
        bash = config["permission"]["bash"]
        self.assertEqual(bash["*"], "deny")
        self.assertEqual(bash["gh pr review *"], "allow")
        self.assertEqual(bash["gh pr comment *"], "allow")
        for forbidden in ["git commit *", "git push *", "python3 -m unittest *"]:
            self.assertNotIn(forbidden, bash)


def _write_stub(directory: Path, name: str, script: str) -> Path:
    stub = directory / name
    stub.write_text(script, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


class ClaudeRunScript(unittest.TestCase):
    """The generated settings actually reach the claude launch."""

    def _run(self, mode: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "args.json"
            stub = _write_stub(Path(tmp), "claude-stub",
                               "#!/usr/bin/env python3\n"
                               "import json, sys\n"
                               f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            env = dict(os.environ)
            env.update({"BOARD_CLAUDE_BIN": str(wrapper),
                        "AGENT_PROMPT": "do the task", "AGENT_MODE": mode,
                        "AGENT_COMMANDS": "python3 -m unittest"})
            result = subprocess.run(["bash", str(CLAUDE / "run")], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(capture.read_text())

    def _settings(self, args: list[str]) -> dict:
        return json.loads(args[args.index("--settings") + 1])

    def test_work_launch_accepts_edits_and_allows_commits(self):
        args = self._run("work")
        self.assertIn("acceptEdits", args)
        self.assertNotIn("--disallowedTools", args)
        allow = self._settings(args)["permissions"]["allow"]
        self.assertIn("Bash(git commit:*)", allow)
        self.assertIn("Bash(python3 -m unittest:*)", allow)
        self.assertNotIn("Bash(git push:*)", allow)

    def test_act_pr_launch_may_push(self):
        args = self._run("act-pr")
        self.assertIn("acceptEdits", args)
        self.assertIn("Bash(git push:*)", self._settings(args)["permissions"]["allow"])

    def test_review_launch_disallows_edit_tools_and_may_post_verdicts(self):
        args = self._run("review")
        self.assertIn("default", args)
        self.assertIn("--disallowedTools", args)
        # This list tracks a moving vendor surface: a deny rule naming a
        # tool the installed CLI no longer has kills the launch outright
        # (MultiEdit did exactly that — task 10).
        for tool in ["Edit", "Write", "NotebookEdit"]:
            self.assertIn(tool, args)
        self.assertNotIn("MultiEdit", args)
        allow = self._settings(args)["permissions"]["allow"]
        self.assertIn("Bash(gh pr review:*)", allow)
        self.assertNotIn("Bash(git commit:*)", allow)


class OpencodeRunScript(unittest.TestCase):
    """The generated config reaches the opencode launch via OPENCODE_CONFIG,
    and the exit code passes through."""

    def _run(self, mode: str, stub_exit: int = 0):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "capture.json"
            stub = _write_stub(Path(tmp), "opencode-stub",
                               "#!/usr/bin/env python3\n"
                               "import json, os, sys\n"
                               "payload = {'argv': sys.argv[1:],\n"
                               "           'config': json.load(open(os.environ['OPENCODE_CONFIG']))}\n"
                               f"open({str(capture)!r}, 'w').write(json.dumps(payload))\n"
                               f"sys.exit({stub_exit})\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            env = dict(os.environ)
            env.update({"BOARD_OPENCODE_BIN": str(wrapper),
                        "AGENT_PROMPT": "do the task", "AGENT_MODE": mode,
                        "AGENT_COMMANDS": "python3 -m unittest"})
            result = subprocess.run(["bash", str(OPENCODE / "run")], env=env,
                                    capture_output=True, text=True)
            payload = json.loads(capture.read_text()) if capture.is_file() else None
            return result, payload

    def test_work_launch_carries_the_permission_config(self):
        result, payload = self._run("work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["argv"], ["run", "do the task"])
        bash = payload["config"]["permission"]["bash"]
        self.assertEqual(bash["*"], "deny")
        self.assertEqual(bash["git commit *"], "allow")
        self.assertEqual(bash["python3 -m unittest *"], "allow")

    def test_review_launch_cannot_edit(self):
        result, payload = self._run("review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["config"]["permission"]["edit"], "deny")

    def test_exit_code_passes_through(self):
        result, _ = self._run("work", stub_exit=3)
        self.assertEqual(result.returncode, 3)


class OpencodeWire(unittest.TestCase):
    def test_wire_installs_the_plugin_shim_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            first = subprocess.run(
                ["python3", str(OPENCODE / "wire"), str(project)],
                capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            shim = project / ".opencode" / "plugin" / "bench-board.js"
            self.assertTrue(shim.is_file())
            self.assertIn("BenchBoard", shim.read_text())

            again = subprocess.run(
                ["python3", str(OPENCODE / "wire"), str(project)],
                capture_output=True, text=True)
            self.assertEqual(again.returncode, 0)
            self.assertIn("nothing to do", again.stdout)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = subprocess.run(
                ["python3", str(OPENCODE / "wire"), str(project), "--dry-run"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertFalse((project / ".opencode").exists())


if __name__ == "__main__":
    unittest.main()
