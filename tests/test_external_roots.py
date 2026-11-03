"""Four roots, named separately (task 56): the tool, the application, the
work state and the worktrees.

Bench derived all of them from where `manager/` sat on disk, so the tool,
the app and the task state were one tree with one git identity. These
cases pin the resolution — including that the default layout is byte for
byte today's — and the validation, which fails closed rather than falling
back to the tool's own repository.

Everything runs against disposable git repositories; nothing spends a
token and nothing touches a remote.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import config  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True)
    git(path, "config", "user.name", "tester")
    git(path, "config", "user.email", "tester@example.test")
    return path


def work_skeleton(path: Path) -> Path:
    for slug in (*STAGES, "archive"):
        (path / "tasks" / slug).mkdir(parents=True, exist_ok=True)
    (path / "plans").mkdir(exist_ok=True)
    (path / "reference").mkdir(exist_ok=True)
    return path


class ConfiguredLayout(unittest.TestCase):
    """Every case reloads config under a chosen environment, because that
    is how a real install configures itself — through manager/local/.env,
    which config folds into the same lookup."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-roots-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def reload(self, **env) -> None:
        saved = {k: v for k, v in os.environ.items() if k.startswith("BOARD_")}

        def restore():
            for key in [k for k in os.environ if k.startswith("BOARD_")]:
                os.environ.pop(key, None)
            os.environ.update(saved)
            importlib.reload(config)

        self.addCleanup(restore)
        for key in [k for k in os.environ if k.startswith("BOARD_")]:
            os.environ.pop(key, None)
        os.environ.update({k: str(v) for k, v in env.items()})
        importlib.reload(config)

    def external(self, **extra) -> tuple[Path, Path]:
        """The layout the issue asks for: a grouping directory holding an
        application repo and a work repo, neither inside the other."""
        app = git_init(self.tmp / "app")
        (app / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", "root")
        work = work_skeleton(git_init(self.tmp / "work"))
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work, **extra)
        return app, work

    # — the default is today —

    def test_the_default_layout_is_unchanged(self):
        self.reload()
        self.assertFalse(config.EXTERNAL)
        self.assertEqual(config.REPO, BENCH)
        self.assertEqual(config.WORK, config.TM_ROOT)
        self.assertEqual(config.TASKS, config.TM_ROOT / "tasks")
        self.assertEqual(config.WORKTREES, config.REPO / ".worktrees")
        self.assertEqual(config.state_repo(), config.REPO,
                         "vendored, the two identities are one repository")
        self.assertEqual(config.problems(), [])

    def test_the_work_root_follows_the_tasks_directory(self):
        """So nothing can hold a work root that disagrees with where the
        task files are actually read from."""
        self.reload()
        self.addCleanup(setattr, config, "TASKS", config.TASKS)
        config.TASKS = self.tmp / "elsewhere" / "tasks"
        self.assertEqual(config.work_root(), self.tmp / "elsewhere")

    # — an explicit application —

    def test_an_explicit_application_is_used_rather_than_derived(self):
        app, work = self.external()
        self.assertTrue(config.EXTERNAL)
        self.assertEqual(config.REPO, app)
        self.assertEqual(config.WORK, work)
        self.assertEqual(config.TASKS, work / "tasks")
        self.assertEqual(config.work_root(), work)
        self.assertNotEqual(config.REPO, BENCH,
                            "the tool's own repository is not the application")
        self.assertEqual(config.problems(), [])

    def test_the_state_repository_is_the_work_repository(self):
        app, work = self.external()
        self.assertEqual(config.state_repo(), work)
        self.assertNotEqual(config.state_repo(), app)

    def test_the_project_name_is_the_applications(self):
        app, _ = self.external()
        self.assertEqual(config.PROJECT, app.name)

    def test_a_title_override_still_wins(self):
        self.external(BOARD_TITLE="Ledger")
        self.assertEqual(config.PROJECT, "Ledger")

    # — refusals: the application root —

    def test_a_relative_application_root_is_refused(self):
        self.reload(BOARD_APP_ROOT="../app")
        self.assertTrue(any("absolute" in p for p in config.problems()),
                        config.problems())

    def test_a_missing_application_root_is_refused(self):
        self.reload(BOARD_APP_ROOT=self.tmp / "nowhere")
        self.assertTrue(any("does not exist" in p for p in config.problems()),
                        config.problems())

    def test_an_application_root_that_is_not_a_repository_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.reload(BOARD_APP_ROOT=plain)
        self.assertTrue(any("git" in p for p in config.problems()),
                        config.problems())

    def test_a_subdirectory_of_a_repository_is_not_its_root(self):
        app = git_init(self.tmp / "app")
        (app / "src").mkdir()
        self.reload(BOARD_APP_ROOT=app / "src")
        self.assertTrue(any("top level" in p for p in config.problems()),
                        config.problems())

    def test_the_tools_own_repository_as_the_application_is_refused(self):
        """Bench must not silently manage itself."""
        work = work_skeleton(git_init(self.tmp / "work"))
        self.reload(BOARD_APP_ROOT=BENCH, BOARD_WORK_ROOT=work)
        self.assertTrue(any("bench" in p.lower() for p in config.problems()),
                        config.problems())

    # — refusals: overlap —

    def test_one_directory_for_both_roots_is_refused(self):
        both = work_skeleton(git_init(self.tmp / "both"))
        self.reload(BOARD_APP_ROOT=both, BOARD_WORK_ROOT=both)
        self.assertTrue(any("same directory" in p for p in config.problems()),
                        config.problems())

    def test_a_work_root_inside_the_application_is_refused(self):
        app = git_init(self.tmp / "app")
        inside = work_skeleton(app / "bench-state")
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=inside)
        problems = config.problems()
        self.assertTrue(any("inside the application" in p for p in problems),
                        problems)

    def test_an_application_inside_the_work_root_is_refused(self):
        work = work_skeleton(self.tmp / "work")
        app = git_init(work / "app")
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work)
        problems = config.problems()
        self.assertTrue(any("inside the work" in p for p in problems), problems)

    def test_a_work_root_that_resolves_into_the_application_is_refused(self):
        """Containment by path is not the whole question: a link whose
        target is in the application's repository is the same mistake, and
        git answers it where paths cannot."""
        app = git_init(self.tmp / "app")
        work_skeleton(app / "state")
        link = self.tmp / "work-link"
        link.symlink_to(app / "state")
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=link)
        problems = config.problems()
        self.assertTrue(problems, "a linked work root inside the app is refused")
        self.assertTrue(any("application" in p for p in problems), problems)

    # — refusals: the wrong-repository combinations (issue requirement 8) —

    def test_committing_moves_without_a_state_repository_is_refused(self):
        app = git_init(self.tmp / "app")
        work = work_skeleton(self.tmp / "work")      # not a git repository
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work,
                    BOARD_COMMIT_MOVES="1")
        problems = config.problems()
        self.assertTrue(any("BOARD_COMMIT_MOVES" in p for p in problems), problems)
        self.assertTrue(any("git init" in p for p in problems), problems)

    def test_syncing_without_a_state_repository_is_refused(self):
        app = git_init(self.tmp / "app")
        work = work_skeleton(self.tmp / "work")
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work, BOARD_SYNC="1")
        problems = config.problems()
        self.assertTrue(any("BOARD_SYNC" in p for p in problems), problems)

    def test_local_only_external_state_is_a_supported_layout(self):
        """The issue's first delivery slice: external app, local task
        state, board commit and sync off. Nothing is refused, and nothing
        pretends there is a repository to commit to."""
        app = git_init(self.tmp / "app")
        work = work_skeleton(self.tmp / "work")
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work)
        self.assertEqual(config.problems(), [])
        self.assertIsNone(config.state_repo())

    def test_a_missing_tasks_directory_is_refused(self):
        app = git_init(self.tmp / "app")
        work = git_init(self.tmp / "work")          # no tasks/ at all
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work)
        self.assertTrue(any("tasks" in p for p in config.problems()),
                        config.problems())

    # — the worktrees —

    def test_worktrees_may_live_outside_the_application(self):
        app, _ = self.external(BOARD_WORKTREES=self.tmp / "worktrees")
        self.assertEqual(config.WORKTREES, self.tmp / "worktrees")
        self.assertEqual(config.problems(), [])

    def test_a_relative_worktrees_setting_resolves_against_the_application(self):
        app, _ = self.external(BOARD_WORKTREES="build/trees")
        self.assertEqual(config.WORKTREES, app / "build" / "trees")

    def test_worktrees_inside_the_work_root_are_refused(self):
        app = git_init(self.tmp / "app")
        work = work_skeleton(git_init(self.tmp / "work"))
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work,
                    BOARD_WORKTREES=work / "trees")
        problems = config.problems()
        self.assertTrue(any("work" in p for p in problems), problems)

    def test_worktrees_at_the_application_root_are_refused(self):
        app, _ = self.external(BOARD_WORKTREES=self.tmp / "app")
        self.assertTrue(any("worktree" in p.lower() for p in config.problems()),
                        config.problems())

    def test_worktrees_inside_the_tasks_directory_are_refused(self):
        app = git_init(self.tmp / "app")
        work = work_skeleton(git_init(self.tmp / "work"))
        self.reload(BOARD_APP_ROOT=app, BOARD_WORK_ROOT=work,
                    BOARD_WORKTREES=work / "tasks" / "trees")
        self.assertTrue(config.problems())

    # — two independent remotes and bases —

    def test_the_two_remotes_resolve_independently(self):
        app, work = self.external()
        git(app, "remote", "add", "upstream", "https://example.test/app.git")
        git(work, "remote", "add", "origin", str(self.tmp / "work-origin.git"))

        self.assertEqual(config.git_remote(), "upstream")
        self.assertEqual(config.state_git_remote(), "origin")

    def test_each_remote_setting_governs_its_own_repository(self):
        app, work = self.external(BOARD_GIT_REMOTE="fork",
                                  BOARD_STATE_GIT_REMOTE="team")
        self.assertEqual(config.git_remote(), "fork")
        self.assertEqual(config.state_git_remote(), "team")

    def test_the_two_bases_default_to_main_and_resolve_independently(self):
        self.reload()
        self.assertEqual(config.APP_BASE, "main")
        self.assertEqual(config.STATE_BASE, "main")
        self.external(BOARD_APP_BASE="trunk", BOARD_STATE_BASE="board")
        self.assertEqual(config.APP_BASE, "trunk")
        self.assertEqual(config.STATE_BASE, "board")

    # — what the rest of the system is told —

    def test_child_processes_are_handed_the_resolved_layout(self):
        app, work = self.external(BOARD_WORKTREES=self.tmp / "worktrees")
        env = config.child_env()
        self.assertEqual(env["BOARD_APP_ROOT"], str(app))
        self.assertEqual(env["BOARD_WORK_ROOT"], str(work))
        self.assertEqual(env["BOARD_TASKS_DIR"], str(work / "tasks"))
        self.assertEqual(env["BOARD_WORKTREES_DIR"], str(self.tmp / "worktrees"))

    def test_the_layout_is_published_for_the_board_to_show(self):
        app, work = self.external()
        layout = config.layout()
        self.assertEqual(layout["app"], str(app))
        self.assertEqual(layout["work"], str(work))
        self.assertEqual(layout["tasks"], str(work / "tasks"))
        self.assertEqual(layout["stateRepo"], str(work))
        self.assertEqual(layout["tool"], str(config.TM_ROOT))
        self.assertTrue(layout["external"])

    def test_tasks_outside_the_application_have_no_application_path(self):
        self.external()
        self.assertIsNone(config.tasks_relative_to_app())

    def test_tasks_inside_the_application_keep_their_path(self):
        self.reload()
        self.assertEqual(config.tasks_relative_to_app(), "tasks")


class PromptGuidance(unittest.TestCase):
    """What a headless run is told to read. Externally the application
    may carry no workflow brief at all, and the board's own lives outside
    the checkout — so the prompt has to name it by absolute path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-guide-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr in ("REPO", "TASKS", "LOCAL"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))

    def point_at(self, app: Path, work: Path, local: Path) -> None:
        config.REPO, config.TASKS, config.LOCAL = app, work / "tasks", local

    def test_an_application_with_its_own_brief_reads_exactly_as_before(self):
        app = self.tmp / "app"
        (app / "nested").mkdir(parents=True)
        (app / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        self.point_at(app, app, app / "nested")
        self.assertEqual(
            config.prompt_guidance(),
            "Read AGENTS.md at the repo root first and follow it, including "
            "its definition of done — run whatever checks it names until "
            "they pass.")

    def test_a_brief_outside_the_checkout_is_named_absolutely(self):
        app = self.tmp / "app"
        app.mkdir()
        (app / "AGENTS.md").write_text("# app rules\n", encoding="utf-8")
        work = self.tmp / "work"
        (work / "tasks").mkdir(parents=True)
        (work / "AGENTS.md").write_text("# board rules\n", encoding="utf-8")
        local = self.tmp / "tool" / "local"
        local.mkdir(parents=True)
        self.point_at(app, work, local)

        text = config.prompt_guidance()
        self.assertIn("AGENTS.md at the repo root", text)
        self.assertIn(str(work / "AGENTS.md"), text)

    def test_an_application_with_no_brief_is_told_so_and_pointed_outside(self):
        app = self.tmp / "app"
        app.mkdir()
        work = self.tmp / "work"
        (work / "tasks").mkdir(parents=True)
        (work / "AGENTS.md").write_text("# board rules\n", encoding="utf-8")
        local = self.tmp / "tool" / "local"
        local.mkdir(parents=True)
        self.point_at(app, work, local)

        text = config.prompt_guidance()
        self.assertIn("no AGENTS.md", text)
        self.assertIn(str(work / "AGENTS.md"), text)

    def test_nothing_anywhere_is_said_plainly_rather_than_invented(self):
        app = self.tmp / "app"
        app.mkdir()
        work = self.tmp / "work"
        (work / "tasks").mkdir(parents=True)
        local = self.tmp / "tool" / "local"
        local.mkdir(parents=True)
        self.point_at(app, work, local)

        text = config.prompt_guidance()
        self.assertIn("no AGENTS.md", text)
        self.assertNotIn(str(work), text)

    def test_the_read_only_flavour_asks_for_no_checks(self):
        app = self.tmp / "app"
        app.mkdir()
        (app / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        self.point_at(app, app, app / "nested")
        self.assertNotIn("until they pass", config.prompt_guidance(checks=False))
        self.assertIn("AGENTS.md", config.prompt_guidance(checks=False))


if __name__ == "__main__":
    unittest.main()
