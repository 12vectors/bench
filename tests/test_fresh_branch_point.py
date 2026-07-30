"""Where a fresh work launch branches from: origin/main when a fetch can
reach it, local HEAD when there is no origin or the network fails — and
never a blocked launch either way. Run with:
python3 -m unittest discover -s tests
"""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args], text=True,
        stderr=subprocess.DEVNULL).strip()


def _commit(cwd: Path, msg: str) -> None:
    subprocess.check_call(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", msg])


class FreshBranchPoint(unittest.TestCase):
    """_fresh_branch_point decides the base of a brand-new task branch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.upstream = root / "upstream"
        self.upstream.mkdir()
        _git(self.upstream, "init", "-q", "-b", "main")
        _commit(self.upstream, "root")
        self.local = root / "local"
        subprocess.check_call(
            ["git", "clone", "-q", str(self.upstream), str(self.local)],
            stderr=subprocess.DEVNULL)
        self._saved = config.REPO, config.FETCH_TIMEOUT
        config.REPO = self.local
        config.FETCH_TIMEOUT = 5.0

    def tearDown(self):
        config.REPO, config.FETCH_TIMEOUT = self._saved
        self._tmp.cleanup()

    def _hang_remote(self, name: str) -> None:
        """Point a remote at a transport that never answers."""
        _git(self.local, "config", "protocol.ext.allow", "always")
        # ext:: runs the command as the remote helper; sleep never answers
        # git's handshake, so the fetch blocks until the timeout fires.
        _git(self.local, "remote", "set-url", name, "ext::sleep 30")

    def test_no_remote_branches_from_head_silently(self):
        _git(self.local, "remote", "remove", "origin")
        self.assertEqual(agents._fresh_branch_point(), (None, None))

    def test_only_origin_counts_and_is_never_fetched_when_absent(self):
        # A hanging remote under another name: if anything fetched it, this
        # test would stall; instead the launch path answers HEAD instantly.
        _git(self.local, "remote", "rename", "origin", "upstream")
        self._hang_remote("upstream")
        started = time.monotonic()
        self.assertEqual(agents._fresh_branch_point(), (None, None))
        self.assertLess(time.monotonic() - started, 2)

    def test_origin_ahead_branches_from_its_tip_and_says_so(self):
        _commit(self.upstream, "landed elsewhere 1")
        _commit(self.upstream, "landed elsewhere 2")
        point, note = agents._fresh_branch_point()
        self.assertEqual(point, "origin/main")
        self.assertEqual(note, "branched from origin/main, "
                               "2 ahead of this checkout")
        # The fetch refreshed the ref the branch will start from.
        self.assertEqual(_git(self.local, "rev-parse", "origin/main"),
                         _git(self.upstream, "rev-parse", "main"))

    def test_worktree_from_origin_main_has_its_tip_as_merge_base(self):
        # The same commands start_agent runs for a fresh branch.
        _commit(self.upstream, "landed elsewhere")
        point, _ = agents._fresh_branch_point()
        worktree = Path(self._tmp.name) / "wt"
        _git(self.local, "worktree", "add", "--no-track", "-b", "task/x",
             str(worktree), point)
        self.assertEqual(_git(self.local, "merge-base", "task/x", "origin/main"),
                         _git(self.upstream, "rev-parse", "main"))
        # --no-track: the task branch must not adopt origin/main as upstream.
        upstream_cfg = subprocess.run(
            ["git", "-C", str(self.local), "config", "branch.task/x.merge"],
            capture_output=True)
        self.assertNotEqual(upstream_cfg.returncode, 0)

    def test_origin_in_sync_is_used_without_narration(self):
        self.assertEqual(agents._fresh_branch_point(), ("origin/main", None))

    def test_unreachable_origin_falls_back_to_head_and_says_so(self):
        _git(self.local, "remote", "set-url", "origin",
             str(Path(self._tmp.name) / "gone"))
        point, note = agents._fresh_branch_point()
        self.assertIsNone(point)
        self.assertEqual(note, "fetch of origin/main failed; "
                               "branched from local HEAD")

    def test_hanging_fetch_times_out_within_bound_and_says_so(self):
        self._hang_remote("origin")
        config.FETCH_TIMEOUT = 0.5
        started = time.monotonic()
        point, note = agents._fresh_branch_point()
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(point)
        self.assertEqual(note, "fetch of origin/main timed out; "
                               "branched from local HEAD")


if __name__ == "__main__":
    unittest.main()
