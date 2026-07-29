"""The zero-commit detection behind the board's empty-branch guard: a
work agent that exits cleanly without committing must not advance its
card, so _no_new_commits has to tell an untouched branch from a worked
one."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args], text=True,
        stderr=subprocess.DEVNULL).strip()


class NoNewCommits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "root")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        self.worktree = Path(self._tmp.name) / "wt"
        _git(self.repo, "worktree", "add", "-q", "-b", "task/x",
             str(self.worktree))

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, **overrides):
        record = {"worktree": str(self.worktree), "base": self.base}
        record.update(overrides)
        return record

    def test_untouched_branch_is_flagged(self):
        self.assertTrue(agents._no_new_commits(self.record()))

    def test_a_commit_clears_the_flag(self):
        (self.worktree / "f").write_text("x")
        _git(self.worktree, "add", "f")
        _git(self.worktree, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "work")
        self.assertFalse(agents._no_new_commits(self.record()))

    def test_unknown_base_or_worktree_never_flags(self):
        # Better to advance a card than to hold it on bad bookkeeping.
        self.assertFalse(agents._no_new_commits(self.record(base=None)))
        self.assertFalse(agents._no_new_commits(self.record(worktree=None)))
        self.assertFalse(agents._no_new_commits(
            self.record(worktree=str(self.worktree / "gone"))))


if __name__ == "__main__":
    unittest.main()
