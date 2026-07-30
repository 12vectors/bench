"""update.sh consumes published releases: replace manager/core/ wholesale
plus the manifest's top-level files, touch nothing else, and refuse loudly
— changing nothing — when there is no release or the asset lies about its
version. Hermetic: gh and curl are PATH stubs, the "release" is a real
artifact built by release.sh, and the installed project is a real unpack
of it.

    python3 -m unittest discover -s tests
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

GH_STUB = """\
#!/usr/bin/env bash
# gh stand-in: serves $STUB_TARBALL as the one release, tagged $STUB_TAG.
[ "${STUB_FAIL:-}" = "1" ] && exit 1
case "${1:-} ${2:-}" in
  "release view")
    printf '%s\\n' "${STUB_TAG:?}"
    ;;
  "release download")
    out=""
    prev=""
    for arg in "$@"; do
      [ "$prev" = "--output" ] && out="$arg"
      prev="$arg"
    done
    cp "${STUB_TARBALL:?}" "${out:?}"
    ;;
  *) exit 1 ;;
esac
"""

CURL_STUB = """\
#!/usr/bin/env bash
exit 22
"""


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


class UpdateFromRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-update-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        cls.version = (REPO / "manager" / "core" / "VERSION").read_text().strip()

        cls.stubs = cls.scratch / "bin"
        cls.stubs.mkdir()
        for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
            stub = cls.stubs / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def make_install(self) -> Path:
        """A host project installed from the artifact, then lived in:
        its own card, settings, checks, notes and state."""
        host = Path(tempfile.mkdtemp(prefix="host-", dir=self.scratch))
        tm = host / ".task-manager"
        tm.mkdir()
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)
        (tm / "tasks" / "backlog" / "20-host-card.md").write_text(
            "# The host's own\n", encoding="utf-8")
        local = tm / "manager" / "local"
        (local / ".env").write_text("BOARD_PORT=26071\n", encoding="utf-8")
        (local / "checks").write_text("mine: \\bmine\\b\n", encoding="utf-8")
        (local / "CLAUDE.md").write_text("# Host notes\n", encoding="utf-8")
        (local / "state" / "sessions").mkdir(parents=True)
        (local / "state" / "sessions" / "s1.jsonl").write_text(
            '{"event":"kept"}\n', encoding="utf-8")
        (tm / "tasks" / "task-template.md").write_text(
            "# My own template\n", encoding="utf-8")
        return tm

    def run_update(self, tm: Path, **extra: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_", "STUB_"))}
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("STUB_TAG", f"v{self.version}")
        env.setdefault("STUB_TARBALL", str(self.tarball))
        env.update(extra)
        return subprocess.run(["bash", str(tm / "update.sh")],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=tm.parent, env=env)

    def test_update_replaces_core_and_top_level_and_nothing_else(self):
        tm = self.make_install()
        # An "older install": stale core content and doctored top-level
        # files that the release must put back.
        (tm / "manager" / "core" / "VERSION").write_text("0\n")
        (tm / "manager" / "core" / "stale.py").write_text("gone = True\n")
        (tm / "start.sh").write_text("#!/bin/sh\necho old\n")
        survivors = {
            path: (tm / path).read_bytes()
            for path in ["tasks/backlog/20-host-card.md",
                         "manager/local/.env", "manager/local/checks",
                         "manager/local/CLAUDE.md",
                         "manager/local/state/sessions/s1.jsonl",
                         "tasks/task-template.md"]}

        result = self.run_update(tm)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"version 0 → {self.version}", result.stdout)
        self.assertFalse((tm / "manager" / "core" / "stale.py").exists(),
                         "core must be replaced wholesale")
        with tarfile.open(self.tarball) as tar:
            for name in ("manager/core/VERSION", "manager/core/board.py",
                         "start.sh", "update.sh", "CLAUDE.md"):
                shipped = tar.extractfile(f"./{name}").read()
                self.assertEqual((tm / name).read_bytes(), shipped,
                                 f"{name} must match the release")
        for path, content in survivors.items():
            self.assertEqual((tm / path).read_bytes(), content,
                             f"{path} must survive byte-identical")

    def test_no_published_release_changes_nothing(self):
        tm = self.make_install()
        before = snapshot(tm)

        result = self.run_update(tm, STUB_FAIL="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No published release found for example/bench",
                      result.stderr)
        self.assertIn("Nothing was changed", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_version_tag_disagreement_is_refused(self):
        tm = self.make_install()
        before = snapshot(tm)

        result = self.run_update(tm, BENCH_REF="v999")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"contains core VERSION {self.version}", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_exact_tag_via_bench_ref(self):
        tm = self.make_install()
        result = self.run_update(tm, BENCH_REF=f"v{self.version}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Updated core from v{self.version}", result.stdout,
                      result.stdout + result.stderr)

    def test_env_beats_stamp_and_dev_checkout_has_no_source(self):
        # The repo's own update.sh is unstamped: with no BENCH_SOURCE
        # anywhere it must refuse and say how to configure one.
        tm = self.scratch / "dev-checkout"
        tm.mkdir()
        shutil.copy(REPO / "update.sh", tm / "update.sh")
        result = self.run_update(tm)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BENCH_SOURCE", result.stderr)

        # BENCH_SOURCE (any GitHub spelling) resurrects it — the stub
        # then serves the release as usual.
        (tm / "manager" / "core").mkdir(parents=True)
        result = self.run_update(
            tm, BENCH_SOURCE="git@github.com:example/bench.git")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((tm / "manager" / "core" / "board.py").is_file())


if __name__ == "__main__":
    unittest.main()
