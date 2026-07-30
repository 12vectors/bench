"""release.sh builds the distribution artifact from the manifest at
manager/core/release-manifest — and from nothing else. The tarball must
contain exactly what the manifest names (correct by construction: bench's
own cards, local/ content, state, tests and .claude/ were never in it),
sit at the tarball root, and unpack into a working, pristine install.

    python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "manager" / "core" / "release-manifest"
STAMP_SOURCE = "example/bench"


def manifest_entries() -> list:
    entries = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, path = line.split(None, 1)
        entries.append((kind, path))
    return entries


def expected_files() -> set:
    """The artifact's exact file list, derived from the manifest the same
    way release.sh builds it — the tripwire for manifest drift."""
    files = set()
    for kind, path in manifest_entries():
        if kind in ("copy", "once", "seed"):
            files.add(path)
        elif kind == "keep":
            files.add(f"{path}/.gitkeep")
        elif kind == "tree":
            for p in (REPO / path).rglob("*"):
                if (p.is_file() and "__pycache__" not in p.parts
                        and p.name != ".DS_Store"):
                    files.add(p.relative_to(REPO).as_posix())
        else:
            raise AssertionError(f"unknown manifest class {kind}")
    return files


def shebang_members(tarball: Path) -> dict:
    """{member name: tar-header mode} for every file in the artifact whose
    content starts `#!`.

    The tar header is the truth here, not the repo: git records only the
    exec bit, and release.sh stages through a copy where a umask could
    still lose it (task 21's risk)."""
    modes = {}
    with tarfile.open(tarball) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            stream = tar.extractfile(member)
            if stream is None or stream.read(2) != b"#!":
                continue
            modes[member.name.removeprefix("./")] = member.mode
    return modes


def shebang_files_missing_exec(tarball: Path) -> list:
    """The invariant, in one place: a shipped file that starts `#!` and
    cannot be run. Anything this names is a bug."""
    return sorted(name for name, mode in shebang_members(tarball).items()
                  if not mode & 0o100)


def build_artifact(out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REPO / "release.sh"), "--tarball", str(out),
         "--source", STAMP_SOURCE],
        capture_output=True, text=True)


class ArtifactContents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-artifact-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = build_artifact(cls.tarball)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"release.sh failed:\n{result.stdout}{result.stderr}")
        with tarfile.open(cls.tarball) as tar:
            cls.members = {m.name.removeprefix("./"): m
                           for m in tar.getmembers()}
        cls.files = {name for name, m in cls.members.items() if m.isfile()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def read_member(self, name: str) -> str:
        with tarfile.open(self.tarball) as tar:
            member = tar.extractfile(f"./{name}") or tar.extractfile(name)
            return member.read().decode("utf-8")

    def test_tarball_is_exactly_the_manifest(self):
        self.assertEqual(self.files, expected_files())

    def test_contents_sit_at_the_tarball_root(self):
        """The README one-liner pipes into `tar -xz -C .task-manager` —
        a version-named wrapper directory would land it one level deep."""
        self.assertIn("manager/core/VERSION", self.files)
        self.assertIn("start.sh", self.files)

    def test_none_of_benchs_own_state_ships(self):
        stages = ["backlog", "to-do", "in-progress", "review", "done",
                  "archive"]
        for name in self.files:
            for stage in stages:
                if name.startswith(f"tasks/{stage}/"):
                    self.assertEqual(name, f"tasks/{stage}/.gitkeep",
                                     f"a task card shipped: {name}")
            for top in ("plans/", "reference/"):
                if name.startswith(top):
                    self.assertEqual(name, f"{top}.gitkeep",
                                     f"content shipped under {top}: {name}")
            for forbidden in ("tests/", ".claude/", ".git/", ".worktrees/",
                              "manager/local/state"):
                self.assertFalse(name.startswith(forbidden),
                                 f"{forbidden} leaked into the artifact: {name}")
        self.assertNotIn("release.sh", self.files)
        self.assertNotIn("manager/local/checks", self.files)
        self.assertNotIn("manager/local/.env", self.files)

    def test_local_is_the_generated_starter_not_benchs_own(self):
        # The starter mirrors the root pair (task 13): AGENTS.md holds the
        # notes, CLAUDE.md is the compatibility pointer.
        seeded = self.read_member("manager/local/AGENTS.md")
        self.assertIn("This file is yours", seeded)
        self.assertNotEqual(
            seeded,
            (REPO / "manager" / "local" / "AGENTS.md").read_text("utf-8"),
            "bench's own local notes must never ship")
        pointer = self.read_member("manager/local/CLAUDE.md")
        self.assertIn("@AGENTS.md", pointer)
        for sub in ("adapters", "commands", "driver", "prompts"):
            self.assertIn(f"manager/local/{sub}/.gitkeep", self.files)

    def test_shipped_update_sh_is_stamped_with_the_source(self):
        self.assertIn(f'BENCH_SOURCE_DEFAULT="{STAMP_SOURCE}"',
                      self.read_member("update.sh"))
        # The repo's own copy stays unstamped — dev clones must not
        # silently update from anywhere.
        self.assertIn('BENCH_SOURCE_DEFAULT=""',
                      (REPO / "update.sh").read_text("utf-8"))

    def test_every_shipped_shebang_file_is_executable(self):
        """A shebang is a promise the file can be run. v0.1-alpha shipped
        install.py mode 644, so the README one-liner's `./install.py` was
        permission-denied on every install. The invariant is absolute — no
        exception list: a file that may not be run must not claim it can,
        and adding an exception here means editing this test with a reason.
        """
        self.assertEqual([], shebang_files_missing_exec(self.tarball))
        # The sweep must actually have reached the scripts — an artifact
        # whose members read as empty would pass vacuously.
        seen = shebang_members(self.tarball)
        for name in ("install.py", "start.sh", "stop.sh", "update.sh",
                     "manager/core/board.py",
                     "manager/core/adapters/claude/run",
                     "manager/core/adapters/claude/wire"):
            self.assertIn(name, seen,
                          f"{name} was not seen as a shebang file")

    def test_the_executable_invariant_catches_a_stripped_mode(self):
        """The guard itself, proven to bite: repack the real artifact with
        install.py's mode stripped — exactly the v0.1-alpha shape — and the
        check must name it. Without this, a sweep that silently stopped
        finding shebangs would read as a clean tarball forever."""
        stripped = self.scratch / "mode-stripped.tar.gz"
        with tarfile.open(self.tarball) as src, \
                tarfile.open(stripped, "w:gz") as out:
            for member in src.getmembers():
                if member.name.removeprefix("./") == "install.py":
                    member.mode = 0o644
                out.addfile(member, src.extractfile(member)
                            if member.isfile() else None)

        self.assertEqual(["install.py"], shebang_files_missing_exec(stripped))


class ReleaseRefusals(unittest.TestCase):
    """Publishing must be reproducible from its tag: a dirty tree or an
    already-existing tag stops release.sh before anything is built."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="bench-release-")).resolve()
        self.addCleanup(shutil.rmtree, self.repo, True)
        shutil.copy(REPO / "release.sh", self.repo / "release.sh")
        core = self.repo / "manager" / "core"
        core.mkdir(parents=True)
        (core / "VERSION").write_text("7\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.repo)],
                       check=True, capture_output=True)
        self.git = ["git", "-C", str(self.repo),
                    "-c", "user.name=t", "-c", "user.email=t@t"]

    def release(self) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_"))}
        return subprocess.run(["bash", str(self.repo / "release.sh")],
                              capture_output=True, text=True,
                              cwd=self.repo, env=env)

    def test_refuses_a_dirty_tree(self):
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty", result.stderr)

    def test_refuses_an_existing_tag(self):
        subprocess.run([*self.git, "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run([*self.git, "commit", "-q", "-m", "x"], check=True,
                       capture_output=True)
        subprocess.run([*self.git, "tag", "v7"], check=True,
                       capture_output=True)
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v7 already exists", result.stderr)


class ArtifactInstalls(unittest.TestCase):
    """Unpacking a release as .task-manager/ is the install: first boot
    has nothing to scrub, and the board serves from it."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-install-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = build_artifact(cls.tarball)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"release.sh failed:\n{result.stdout}{result.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def make_install(self, name: str) -> Path:
        host = self.scratch / name
        (host / ".claude").mkdir(parents=True)
        tm = host / ".task-manager"
        tm.mkdir()
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)
        return tm

    def test_first_boot_finds_nothing_to_clean(self):
        tm = self.make_install("pristine")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_"))}
        result = subprocess.run(
            [sys.executable, str(tm / "install.py")],
            capture_output=True, text=True, cwd=tm.parent, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed", result.stdout)
        self.assertTrue((tm / "tasks" / "task-template.md").is_file())
        self.assertTrue((tm / "manager" / "local" / "state").is_dir())

    def test_install_py_runs_directly_from_an_unpacked_release(self):
        """The README's next step after unpacking is `./install.py`.
        v0.1-alpha shipped it mode 644, so that step was permission-denied
        on every install. Run as a program — no interpreter in front of it
        — so the exec bit is what is under test, unpacked and in place."""
        tm = self.make_install("runnable")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_"))}
        result = subprocess.run([str(tm / "install.py")],
                                capture_output=True, text=True,
                                cwd=tm.parent, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_board_serves_from_an_unpacked_artifact(self):
        tm = self.make_install("serving")

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_"))}
        proc = subprocess.Popen(
            [sys.executable, str(tm / "manager" / "core" / "board.py"),
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
            # Whatever the payload shape, the response must not mention
            # bench's own shipped cards.
            self.assertNotIn("install-ships-pristine-board",
                             json.dumps(state))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
