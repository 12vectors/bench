"""Explicit remote approval can be replayed without replacing a remote."""
from tests.test_bootstrap import Workspace, git, snapshot


class RemoteReplay(Workspace):
    def test_completed_add_remote_replay(self):
        remote = str(self.ws / "remote.git")
        args = ("--add-remote", remote)
        self.assertOk(self.local_apply(*args))
        before = self.app_state()
        self.assertOk(self.local_apply(*args))
        self.assertEqual(git(self.work, "remote", "get-url", "origin").stdout.strip(), remote)
        self.assertEqual(self.app_state(), before)

    def test_interrupted_after_remote_add_replays(self):
        # Stop the real apply at install, after it created the remote.
        installer = self.tool / "install.py"
        original = installer.read_bytes()
        installer.write_text("raise SystemExit(1)\n")
        remote = str(self.ws / "remote.git")
        self.assertNotEqual(self.local_apply("--add-remote", remote).returncode, 0)
        self.assertEqual(git(self.work, "remote", "get-url", "origin").stdout.strip(), remote)
        installer.write_bytes(original)
        self.assertOk(self.local_apply("--add-remote", remote))
        self.assertTrue(self.handoff_file.exists())
        self.assertEqual(git(self.work, "remote", "get-url", "origin").stdout.strip(), remote)

    def test_different_remote_refuses_without_writes(self):
        self.assertOk(self.local_apply("--add-remote", str(self.ws / "first.git")))
        before = snapshot(self.ws)
        result = self.local_apply("--add-remote", str(self.ws / "different.git"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remote", result.stdout + result.stderr)
        self.assertEqual(snapshot(self.ws), before)
