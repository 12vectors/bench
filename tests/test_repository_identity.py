"""Is application git the same repository as work-state git?

`config.app_shares_state_repository()` answers it, and other modules
depend on the answer: `github` reads it to decide whether the sync beat
or the board's own catch-up fast-forwards the application's base after a
merge, and the completion sheet reads it to say whether tickets live in
a repository of their own. It used to be `config._one_repository()`, a
private name serving a cross-module contract (task 62).

The identity is a *resolved* one: two spellings of one checkout are one
repository, and two checkouts are two however alike their paths look.

Nothing here spends a token or touches a remote.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import config  # noqa: E402


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True)
    return path


class SharedRepositoryIdentity(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-identity-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = git_init(self.tmp / "app")
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def layout(self, *, repo: Path, work: Path, external: bool) -> None:
        self.patch(REPO=repo, WORK=work, TASKS=work / "tasks",
                   EXTERNAL=external)
        (work / "tasks").mkdir(parents=True, exist_ok=True)

    # — the three layouts the card names —

    def test_a_vendored_install_shares_the_application_repository(self):
        self.layout(repo=self.app, work=self.app / ".task-manager",
                    external=False)
        self.assertEqual(config.state_repo(), self.app)
        self.assertTrue(config.app_shares_state_repository())

    def test_a_work_state_with_its_own_repository_is_separate(self):
        work = git_init(self.tmp / "work")
        self.layout(repo=self.app, work=work, external=True)
        self.assertEqual(config.state_repo(), work)
        self.assertFalse(config.app_shares_state_repository())

    def test_a_work_state_in_no_repository_shares_nothing(self):
        work = self.tmp / "work"
        work.mkdir()
        self.layout(repo=self.app, work=work, external=True)
        self.assertIsNone(config.state_repo())
        self.assertFalse(config.app_shares_state_repository())

    # — resolved identity, not directory spelling —

    def test_two_spellings_of_one_checkout_are_one_repository(self):
        # The application named through a link, the work state found by
        # git from inside the real checkout: different strings, one repo.
        alias = self.tmp / "app-link"
        alias.symlink_to(self.app, target_is_directory=True)
        self.layout(repo=alias, work=self.app / "board", external=True)
        self.assertNotEqual(str(config.state_repo()), str(config.REPO))
        self.assertTrue(config.app_shares_state_repository())

    def test_an_unnormalised_application_path_is_the_same_repository(self):
        spelled = self.tmp / "elsewhere" / ".." / "app"
        (self.tmp / "elsewhere").mkdir()
        self.layout(repo=spelled, work=self.app / "board", external=True)
        self.assertTrue(config.app_shares_state_repository())

    def test_a_link_named_like_the_application_is_not_it(self):
        # A link called `app` that leads to the work state's repository,
        # beside a real application of the same name elsewhere: spelling
        # alike must not make them one.
        work = git_init(self.tmp / "work")
        decoy_parent = self.tmp / "decoy"
        decoy_parent.mkdir()
        (decoy_parent / "app").symlink_to(work, target_is_directory=True)
        self.layout(repo=self.app, work=decoy_parent / "app",
                    external=True)
        self.assertEqual(config.state_repo().name, "work")
        self.assertFalse(config.app_shares_state_repository())

    def test_separate_repositories_named_through_links_stay_separate(self):
        work = git_init(self.tmp / "work")
        app_link = self.tmp / "a"
        work_link = self.tmp / "w"
        app_link.symlink_to(self.app, target_is_directory=True)
        work_link.symlink_to(work, target_is_directory=True)
        self.layout(repo=app_link, work=work_link, external=True)
        self.assertFalse(config.app_shares_state_repository())

    def test_an_unreadable_path_answers_separate(self):
        self.layout(repo=self.app, work=self.app / ".task-manager",
                    external=False)
        with mock.patch.object(Path, "resolve", side_effect=OSError):
            self.assertFalse(config.app_shares_state_repository())

    # — a read, nothing more —

    def test_asking_runs_no_further_git_once_the_top_level_is_known(self):
        work = git_init(self.tmp / "work")
        self.layout(repo=self.app, work=work, external=True)
        config.state_repo()                    # warms the cached lookup
        with mock.patch.object(config, "git_run") as git_run, \
                mock.patch.object(subprocess, "run") as run:
            self.assertFalse(config.app_shares_state_repository())
        git_run.assert_not_called()
        run.assert_not_called()

    # — the settings that read it keep their answers —

    def test_merge_mode_and_remote_setting_follow_the_identity(self):
        self.patch(SYNC=True, APP_MERGE="", STATE_GIT_REMOTE="")
        self.layout(repo=self.app, work=self.app / ".task-manager",
                    external=False)
        self.assertTrue(config.app_merge_on_origin())
        self.assertEqual(config.state_remote_setting(), "BOARD_GIT_REMOTE")

        config._GIT_ROOTS.clear()
        work = git_init(self.tmp / "work")
        self.layout(repo=self.app, work=work, external=True)
        self.assertFalse(config.app_merge_on_origin())
        self.assertEqual(config.state_remote_setting(),
                         "BOARD_STATE_GIT_REMOTE")


class NoPrivateCallers(unittest.TestCase):
    """The old private name is gone, so nothing can keep reaching for it."""

    def test_the_private_name_is_not_defined(self):
        self.assertFalse(hasattr(config, "_one_repository"))

    def test_no_module_refers_to_the_private_name(self):
        pattern = re.compile(r"\b_one_repository\b")
        offenders = []
        for root in ("manager", "verify", "tests"):
            for path in (BENCH / root).rglob("*.py"):
                if path == Path(__file__).resolve():
                    continue
                if pattern.search(path.read_text(encoding="utf-8",
                                                 errors="replace")):
                    offenders.append(str(path.relative_to(BENCH)))
        for path in BENCH.glob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_github_asks_the_public_helper(self):
        source = (BENCH / "manager" / "core" / "github.py").read_text(
            encoding="utf-8")
        self.assertEqual(source.count("config.app_shares_state_repository()"),
                         2)


if __name__ == "__main__":
    unittest.main()
