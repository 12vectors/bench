"""Installing a bench that lives outside the application (task 56).

`install.py --app <repo> --work <dir>` configures the external layout,
writes the work-state skeleton, and wires the agent adapter **without
putting anything into the application**: the hook settings are generated
under the tool's own local state and handed to launches from there. The
opt-in `--app-hooks` writes the designated *local* settings file instead,
and refuses outright if git tracks it.

Every run is a real subprocess against scratch directories, like the
other install suites.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
STAGES = ("backlog", "to-do", "in-progress", "review", "done")
EMIT_SUFFIX = "manager/core/adapters/claude/emit.py"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


class ExternalInstall(unittest.TestCase):
    """A tool install in its own directory, an application repository and
    a work-state directory — the three roots the issue asks for."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-extinst-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.tool = self.tmp / "tool"
        shutil.copytree(BENCH / "manager" / "core",
                        self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local" / "adapters").mkdir(parents=True)
        (self.tool / "manager" / "local" / "prompts").mkdir()
        shutil.copy(BENCH / "install.py", self.tool / "install.py")

        self.app = self.tmp / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.app)],
                       check=True, capture_output=True)
        git(self.app, "config", "user.name", "appdev")
        git(self.app, "config", "user.email", "appdev@example.test")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        (self.app / ".claude").mkdir()
        git(self.app, "add", "code.txt")
        git(self.app, "commit", "-q", "-m", "the application")

        self.work = self.tmp / "work"
        self.env_file = self.tool / "manager" / "local" / ".env"
        self.generated = (self.tool / "manager" / "local" / "state"
                          / "hooks" / "claude" / "settings.json")

    # — running it —

    def install(self, *args: str, **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tool / "install.py"), *args],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=clean_env(**env), stdin=subprocess.DEVNULL)

    def external(self, *args: str) -> subprocess.CompletedProcess:
        return self.install("--app", str(self.app), "--work", str(self.work),
                            *args)

    def env_values(self) -> dict:
        values = {}
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
        return values

    def app_tracked(self) -> list[str]:
        return git(self.app, "ls-files").stdout.split()

    # — what it writes —

    def test_it_records_the_layout_in_the_tools_own_env(self):
        result = self.external()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        values = self.env_values()
        self.assertEqual(values["BOARD_APP_ROOT"], str(self.app))
        self.assertEqual(values["BOARD_WORK_ROOT"], str(self.work))
        self.assertIn("BOARD_PORT", values,
                      "the rest of the example came with it, so the project "
                      "can read what else it can change")

    def test_the_worktrees_default_beside_the_application_not_inside_it(self):
        self.external()
        self.assertEqual(self.env_values()["BOARD_WORKTREES"],
                         str(self.tmp / "worktrees"))
        self.assertFalse((self.app / ".worktrees").exists())

    def test_an_explicit_worktrees_location_is_taken(self):
        self.external("--worktrees", str(self.tmp / "trees"))
        self.assertEqual(self.env_values()["BOARD_WORKTREES"],
                         str(self.tmp / "trees"))

    def test_it_builds_the_work_state_skeleton(self):
        self.external()
        for slug in (*STAGES, "archive"):
            self.assertTrue((self.work / "tasks" / slug).is_dir(), slug)
        self.assertTrue((self.work / "plans").is_dir())
        self.assertTrue((self.work / "reference").is_dir())
        self.assertTrue((self.work / "tasks" / "task-template.md").is_file(),
                        "a new board starts from the template")

    def test_it_leaves_an_existing_work_state_alone(self):
        (self.work / "tasks" / "backlog").mkdir(parents=True)
        mine = self.work / "tasks" / "backlog" / "01-mine.md"
        mine.write_text("# 01 — Mine\n", encoding="utf-8")
        self.external()
        self.assertEqual(mine.read_text(encoding="utf-8"), "# 01 — Mine\n")

    def test_it_reports_the_three_roots_it_resolved(self):
        result = self.external()
        for path in (self.tool, self.app, self.work):
            self.assertIn(str(path), result.stdout)

    def test_running_it_again_changes_nothing(self):
        self.external()
        before = self.env_file.read_text(encoding="utf-8")
        generated_before = self.generated.read_text(encoding="utf-8")

        result = self.external()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before)
        self.assertEqual(self.generated.read_text(encoding="utf-8"),
                         generated_before)

    def test_a_dry_run_writes_nothing(self):
        result = self.external("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.env_file.exists())
        self.assertFalse(self.generated.exists())
        self.assertFalse((self.work / "tasks").exists())

    # — nothing reaches the application —

    def test_the_application_gains_nothing_at_all(self):
        before = self.app_tracked()
        self.external()

        self.assertEqual(self.app_tracked(), before)
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(),
                         "", "not even an untracked file")
        self.assertFalse((self.app / ".claude" / "settings.json").exists())

    def test_the_hook_settings_are_generated_under_the_tool(self):
        self.external()
        self.assertTrue(self.generated.is_file())
        settings = json.loads(self.generated.read_text(encoding="utf-8"))

        self.assertEqual(settings["plansDirectory"], str(self.work / "plans"))
        commands = {hook["command"]
                    for groups in settings["hooks"].values()
                    for group in groups for hook in group["hooks"]}
        self.assertEqual(
            commands,
            {f'python3 "{self.tool / EMIT_SUFFIX}"'},
            "the hooks name emit.py absolutely — the app has no copy")
        self.assertEqual(sorted(settings["hooks"]),
                         ["PostToolUse", "PreToolUse", "SessionEnd",
                          "SessionStart", "Stop"])

    def test_it_says_how_to_use_the_generated_settings(self):
        result = self.external()
        self.assertIn(f"--settings {self.generated}", result.stdout)

    def test_an_application_without_a_claude_directory_is_fine(self):
        shutil.rmtree(self.app / ".claude")
        result = self.external()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.generated.is_file())
        self.assertFalse((self.app / ".claude").exists())

    # — the opt-in local wiring —

    def test_app_hooks_writes_the_local_settings_file(self):
        result = self.external("--app-hooks")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        local = self.app / ".claude" / "settings.local.json"
        self.assertTrue(local.is_file())
        self.assertFalse((self.app / ".claude" / "settings.json").exists(),
                         "the tracked settings file is never written")
        settings = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(settings["plansDirectory"], str(self.work / "plans"))
        self.assertNotIn("settings.local.json", self.app_tracked())

    def test_app_hooks_preserves_unrelated_settings_and_hooks(self):
        local = self.app / ".claude" / "settings.local.json"
        local.write_text(json.dumps({
            "env": {"MY_KEY": "mine"},
            "hooks": {"SessionStart": [{"hooks": [
                {"type": "command", "command": "echo theirs"}]}]},
        }), encoding="utf-8")

        self.external("--app-hooks")

        settings = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(settings["env"], {"MY_KEY": "mine"})
        commands = [hook["command"]
                    for group in settings["hooks"]["SessionStart"]
                    for hook in group["hooks"]]
        self.assertIn("echo theirs", commands)
        self.assertTrue(any("emit.py" in c for c in commands))

    def test_app_hooks_refuses_a_settings_file_git_tracks(self):
        local = self.app / ".claude" / "settings.local.json"
        local.write_text("{}", encoding="utf-8")
        git(self.app, "add", "-f", str(local))
        git(self.app, "commit", "-q", "-m", "tracked local settings")

        result = self.external("--app-hooks")

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("tracked", result.stdout + result.stderr)
        self.assertEqual(local.read_text(encoding="utf-8"), "{}",
                         "it was not rewritten")

    # — refusals —

    def test_an_application_that_is_not_a_repository_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        result = self.install("--app", str(plain), "--work", str(self.work))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("git", result.stdout + result.stderr)
        self.assertFalse(self.generated.exists())

    def test_a_work_root_inside_the_application_is_refused(self):
        result = self.install("--app", str(self.app),
                              "--work", str(self.app / "state"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("inside the application", result.stdout + result.stderr)

    def test_a_relative_root_is_refused_rather_than_guessed_at(self):
        result = self.install("--app", "../app", "--work", str(self.work))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("absolute", result.stdout + result.stderr)

    def test_a_refusal_leaves_the_env_untouched(self):
        self.external()
        before = self.env_file.read_text(encoding="utf-8")

        result = self.install("--app", str(self.tmp / "nowhere"),
                              "--work", str(self.work))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before)

    # — the distribution's own cards —

    def test_the_first_boot_clean_does_not_fire_externally(self):
        """It exists to clear the distribution's shipped cards out of a
        vendored install. An external work root is a board of its own."""
        (self.work / "tasks" / "backlog").mkdir(parents=True)
        card = self.work / "tasks" / "backlog" / "01-ours.md"
        card.write_text("# 01 — Ours\n", encoding="utf-8")
        shipped = self.tool / "tasks" / "backlog"
        shipped.mkdir(parents=True)
        (shipped / "99-distribution.md").write_text("# 99\n", encoding="utf-8")

        result = self.external()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(card.is_file(), "our own card survived")
        self.assertNotIn("first boot", result.stdout)


class OpenCodeExternallyIsRefused(unittest.TestCase):
    """opencode discovers plugins under the project it runs in, so its
    event bridge cannot live outside the application. That is said and
    failed rather than quietly written into the app anyway."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-oc-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wire = (BENCH / "manager" / "core" / "adapters" / "opencode"
                     / "wire")
        self.app = self.tmp / "app"
        self.app.mkdir()

    def run_wire(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.wire), str(self.app), *args],
            capture_output=True, text=True, env=clean_env())

    def test_generated_mode_fails_and_says_why(self):
        result = self.run_wire("--generated",
                               str(self.tmp / "generated" / "settings.json"))

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("unsupported", result.stdout)
        self.assertIn("BOARD_AGENT_ADAPTER=claude", result.stdout)

    def test_it_writes_nothing_into_the_application(self):
        self.run_wire("--generated",
                      str(self.tmp / "generated" / "settings.json"))

        self.assertFalse((self.app / ".opencode").exists(),
                         "no silent fallback to in-app wiring")
        self.assertFalse((self.tmp / "generated").exists())

    def test_the_vendored_path_is_untouched(self):
        result = self.run_wire()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.app / ".opencode" / "plugin"
                         / "bench-board.js").is_file())


class ExternalStateSurvivesAFreshToolInstall(unittest.TestCase):
    """A work root that already holds a board, wired by a tool install
    with no .env and no state stamp of its own: the first-boot clean must
    not touch any of it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-preserve-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = self.tmp / "tool"
        shutil.copytree(BENCH / "manager" / "core",
                        self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local" / "adapters").mkdir(parents=True)
        shutil.copy(BENCH / "install.py", self.tool / "install.py")
        self.app = self.tmp / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.app)],
                       check=True, capture_output=True)

        self.work = self.tmp / "work"
        for slug in (*STAGES, "archive"):
            (self.work / "tasks" / slug).mkdir(parents=True)
        for extra in ("plans", "reference"):
            (self.work / extra).mkdir()
        self.cards = {
            self.work / "tasks" / "backlog" / "01-ours.md": "# 01 — Ours\n",
            self.work / "tasks" / "done" / "02-shipped.md": "# 02 — Shipped\n",
            self.work / "tasks" / "archive" / "03-filed.md": "# 03 — Filed\n",
            self.work / "plans" / "01-plan.md": "the plan\n",
            self.work / "reference" / "spec.md": "the spec\n",
        }
        for path, text in self.cards.items():
            path.write_text(text, encoding="utf-8")

    def test_nothing_of_the_board_is_cleared(self):
        self.assertFalse((self.tool / "manager" / "local" / ".env").exists())
        self.assertFalse((self.tool / "manager" / "local" / "state").exists())

        result = subprocess.run(
            [sys.executable, str(self.tool / "install.py")],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=clean_env(BOARD_APP_ROOT=self.app, BOARD_WORK_ROOT=self.work),
            stdin=subprocess.DEVNULL)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("first boot", result.stdout)
        for path, text in self.cards.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text,
                             f"{path} was altered")

    def test_a_restart_after_it_keeps_the_same_roots(self):
        env = clean_env(BOARD_APP_ROOT=self.app, BOARD_WORK_ROOT=self.work)
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(self.tool / "install.py")],
                capture_output=True, text=True, cwd=str(self.tmp), env=env,
                stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertIn(str(self.app), result.stdout)
            self.assertIn(str(self.work), result.stdout)
        for path, text in self.cards.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text)


class TheGeneratedSettingsAreTheLaunchSettings(unittest.TestCase):
    """The document written for a person's own sessions and the one each
    headless launch builds must carry the same hooks, or the live view
    tells different stories depending on who started the work."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-hooks-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.adapter = BENCH / "manager" / "core" / "adapters" / "claude"
        self.app = self.tmp / "app"
        self.app.mkdir()

    def generated(self, out: Path, plans: Path | None = None) -> dict:
        args = [sys.executable, str(self.adapter / "wire"), str(self.app),
                "--generated", str(out)]
        if plans:
            args += ["--plans", str(plans)]
        result = subprocess.run(args, capture_output=True, text=True,
                                env=clean_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(out.read_text(encoding="utf-8"))

    def launch_settings(self, mode: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(self.adapter / "hook_settings.py"), mode],
            capture_output=True, text=True, env=clean_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_launch_intent_carries_the_same_hooks(self):
        wired = self.generated(self.tmp / "generated" / "settings.json")
        for mode in ("work", "act-pr", "review"):
            with self.subTest(mode=mode):
                self.assertEqual(self.launch_settings(mode)["hooks"],
                                 wired["hooks"],
                                 "a launch would report differently from a "
                                 "person's own session")

    def test_both_name_emit_py_absolutely(self):
        """Neither can go through $CLAUDE_PROJECT_DIR: the application has
        no copy of emit.py, and a launch's cwd is a worktree."""
        emit = self.adapter / "emit.py"
        wired = self.generated(self.tmp / "generated" / "settings.json")
        commands = {hook["command"]
                    for groups in wired["hooks"].values()
                    for group in groups for hook in group["hooks"]}
        self.assertEqual(commands, {f'python3 "{emit}"'})
        for mode in ("work", "review"):
            launch = {hook["command"]
                      for groups in self.launch_settings(mode)["hooks"].values()
                      for group in groups for hook in group["hooks"]}
            self.assertEqual(launch, {f'python3 "{emit}"'})

    def test_a_path_with_spaces_survives_both(self):
        plans = self.tmp / "my work" / "plans"
        out = self.tmp / "a tool" / "state" / "settings.json"
        wired = self.generated(out, plans)

        self.assertEqual(wired["plansDirectory"], str(plans))
        command = next(iter({hook["command"]
                             for groups in wired["hooks"].values()
                             for group in groups
                             for hook in group["hooks"]}))
        self.assertTrue(command.startswith('python3 "'),
                        "the path is quoted, so a space cannot split it")
        self.assertTrue(command.endswith('emit.py"'))

    def test_the_bridge_reads_a_non_default_port_from_the_env_file(self):
        """emit.py is standalone — it has to find the board the same way,
        with no BOARD_PORT in the environment at all."""
        local = self.tmp / "a tool" / "manager" / "local"
        local.mkdir(parents=True)
        (local / ".env").write_text("BOARD_PORT = '26081'\n", encoding="utf-8")
        shutil.copytree(self.adapter, self.tmp / "a tool" / "manager" / "core"
                        / "adapters" / "claude")

        probe = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util, sys;"
             "spec = importlib.util.spec_from_file_location('emit', sys.argv[1]);"
             "m = importlib.util.module_from_spec(spec);"
             "spec.loader.exec_module(m);"
             "print(m.board_port())",
             str(self.tmp / "a tool" / "manager" / "core" / "adapters"
                 / "claude" / "emit.py")],
            capture_output=True, text=True, env=clean_env())

        self.assertEqual(probe.stdout.strip(), "26081", probe.stderr)

    def test_the_bridge_strips_the_configured_worktrees_directory(self):
        """A file path is reported relative to its checkout. The worktrees
        directory is configurable and may sit anywhere, so it is read
        rather than assumed to be `.worktrees`."""
        emit = self.adapter / "emit.py"
        probe = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util, sys;"
             "spec = importlib.util.spec_from_file_location('emit', sys.argv[1]);"
             "m = importlib.util.module_from_spec(spec);"
             "spec.loader.exec_module(m);"
             "print(m._relpath('/ws/trees/56-card/src/a.py'));"
             "print(m._relpath('/ws/app/src/b.py'))",
             str(emit)],
            capture_output=True, text=True,
            env=clean_env(BOARD_WORKTREES_DIR="/ws/trees",
                          BOARD_APP_ROOT="/ws/app"))

        self.assertEqual(probe.stdout.split(), ["src/a.py", "src/b.py"],
                         probe.stderr)


class AnUpdatePreservesTheExternalLayout(unittest.TestCase):
    """A refreshed tool keeps its roots, its generated hooks and every
    ticket: updates replace manager/core/ and the top-level scripts, and
    the work state is not even inside the tool to be reached."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-extupd-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(BENCH / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"release.sh failed:\n{result.stdout}{result.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-extupd-run-",
                                         dir=self.scratch)).resolve()
        self.tool = self.tmp / "tool"
        self.tool.mkdir()
        subprocess.run(["tar", "-xzf", str(self.tarball), "-C", str(self.tool)],
                       check=True, capture_output=True)
        self.app = self.tmp / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.app)],
                       check=True, capture_output=True)
        self.work = self.tmp / "work"
        self.env_file = self.tool / "manager" / "local" / ".env"
        self.generated = (self.tool / "manager" / "local" / "state" / "hooks"
                          / "claude" / "settings.json")

        result = subprocess.run(
            [sys.executable, str(self.tool / "install.py"),
             "--app", str(self.app), "--work", str(self.work)],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=clean_env(), stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.card = self.work / "tasks" / "backlog" / "01-ours.md"
        self.card.write_text("# 01 — Ours\n", encoding="utf-8")

    def refresh_core(self) -> None:
        """What `scripts/build-tool.sh` and `update.sh` both do: replace
        manager/core/ wholesale and the manifest's top-level files."""
        dist = self.tmp / "dist"
        dist.mkdir(exist_ok=True)
        subprocess.run(["tar", "-xzf", str(self.tarball), "-C", str(dist)],
                       check=True, capture_output=True)
        subprocess.run(["rsync", "-a", "--delete",
                        f"{dist}/manager/core/",
                        f"{self.tool}/manager/core/"], check=True,
                       capture_output=True)
        manifest = (dist / "manager" / "core" / "release-manifest")
        for line in manifest.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "copy" and (dist / parts[1]).is_file():
                shutil.copy(dist / parts[1], self.tool / parts[1])

    def test_the_roots_the_tickets_and_the_hooks_all_survive(self):
        before_env = self.env_file.read_text(encoding="utf-8")
        before_hooks = self.generated.read_text(encoding="utf-8")
        (self.tool / "manager" / "core" / "VERSION").write_text("0\n")

        self.refresh_core()

        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before_env)
        self.assertEqual(self.generated.read_text(encoding="utf-8"),
                         before_hooks)
        self.assertEqual(self.card.read_text(encoding="utf-8"), "# 01 — Ours\n")
        self.assertNotEqual(
            (self.tool / "manager" / "core" / "VERSION").read_text().strip(),
            "0", "core really was replaced")

    def test_the_board_still_resolves_the_same_layout_afterwards(self):
        self.refresh_core()

        probe = subprocess.run(
            [sys.executable, "-c",
             "import json, sys; sys.path.insert(0, sys.argv[1]);"
             "import config; print(json.dumps(config.layout()));"
             "print(json.dumps(config.problems()))",
             str(self.tool / "manager" / "core")],
            capture_output=True, text=True, env=clean_env())
        self.assertEqual(probe.returncode, 0, probe.stderr)
        layout, problems = [json.loads(l) for l in probe.stdout.splitlines()]

        self.assertEqual(layout["app"], str(self.app))
        self.assertEqual(layout["work"], str(self.work))
        self.assertTrue(layout["external"])
        self.assertEqual(problems, [])

    def test_re_running_the_installer_after_it_is_a_no_op(self):
        self.refresh_core()
        before = self.env_file.read_text(encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(self.tool / "install.py")],
            capture_output=True, text=True, cwd=str(self.tmp),
            env=clean_env(), stdin=subprocess.DEVNULL)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before)
        self.assertIn("nothing to do", result.stdout)
        self.assertIn(str(self.app), result.stdout)


class VendoredInstallIsUnchanged(unittest.TestCase):
    """With no external flags and no external settings, install.py does
    exactly what it did: wires the host project's tracked settings file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-vendored-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.host = self.tmp / "host"
        self.tm = self.host / ".task-manager"
        (self.host / ".claude").mkdir(parents=True)
        shutil.copytree(BENCH / "manager" / "core",
                        self.tm / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tm / "manager" / "local").mkdir(parents=True)
        shutil.copy(BENCH / "install.py", self.tm / "install.py")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.host)],
                       check=True, capture_output=True)

    def test_it_still_writes_the_projects_settings_json(self):
        result = subprocess.run(
            [sys.executable, str(self.tm / "install.py")],
            capture_output=True, text=True, cwd=str(self.host),
            env=clean_env(), stdin=subprocess.DEVNULL)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        settings = json.loads(
            (self.host / ".claude" / "settings.json").read_text(
                encoding="utf-8"))
        self.assertEqual(settings["plansDirectory"], "./.task-manager/plans")
        commands = {hook["command"]
                    for groups in settings["hooks"].values()
                    for group in groups for hook in group["hooks"]}
        self.assertEqual(
            commands,
            {f'python3 "$CLAUDE_PROJECT_DIR/.task-manager/{EMIT_SUFFIX}"'})


class GeneratedWiringDirectly(unittest.TestCase):
    """The adapter contract's new half, exercised through `wire` itself:
    `--generated <file>` writes there and touches nothing else."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-wire-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wire = (BENCH / "manager" / "core" / "adapters" / "claude"
                     / "wire")
        self.app = self.tmp / "app"
        (self.app / ".claude").mkdir(parents=True)
        self.out = self.tmp / "generated" / "settings.json"

    def run_wire(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.wire), str(self.app), *args],
            capture_output=True, text=True, env=clean_env())

    def test_it_writes_the_generated_file_and_not_the_project(self):
        result = self.run_wire("--generated", str(self.out),
                               "--plans", str(self.tmp / "work" / "plans"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.out.is_file())
        self.assertFalse((self.app / ".claude" / "settings.json").exists())
        settings = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(settings["plansDirectory"],
                         str(self.tmp / "work" / "plans"))

    def test_a_second_run_reports_ok_and_rewrites_nothing(self):
        self.run_wire("--generated", str(self.out))
        before = self.out.read_text(encoding="utf-8")

        result = self.run_wire("--generated", str(self.out))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nothing to do", result.stdout)
        self.assertEqual(self.out.read_text(encoding="utf-8"), before)

    def test_a_dry_run_writes_nothing(self):
        result = self.run_wire("--generated", str(self.out), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.out.exists())

    def test_it_does_not_need_a_claude_directory_in_the_project(self):
        shutil.rmtree(self.app / ".claude")
        result = self.run_wire("--generated", str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.out.is_file())


if __name__ == "__main__":
    unittest.main()
