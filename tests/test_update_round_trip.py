"""update.sh's brief rename round-trip: a project installed on the old
layout (a full vendor-named CLAUDE.md, no AGENTS.md) updates to a core
that ships AGENTS.md as the brief plus a pointer CLAUDE.md — the brief
must arrive, the pointer must replace the old full copy, and nothing a
project owns may move. Run with: python3 -m unittest discover -s tests

update.sh is exercised end-to-end as a subprocess against a scratch
install and a scratch distribution repo built from this repo's real
top-level files, so what is asserted is what a real update does to disk.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOP_FILES = ["AGENTS.md", "CLAUDE.md", "README.md", "install.py",
             "start.sh", "stop.sh", "update.sh"]

OLD_BRIEF = "# Task Workflow\n\nThe old full vendor-named brief.\n"
OLD_LOCAL_NOTES = "# Project notes\n\nThe project's own, old-style.\n"


def make_dist(root: Path) -> Path:
    """A distribution repo carrying this repo's real top-level files and a
    minimal manager/core/, committed so update.sh can clone it."""
    dist = root / "dist"
    (dist / "manager" / "core" / "adapters").mkdir(parents=True)
    # A different length than the installed "old" — rsync's quick check
    # (size+mtime) must see a change, as any real version bump would.
    (dist / "manager" / "core" / "VERSION").write_text("new-version\n",
                                                       encoding="utf-8")
    for f in TOP_FILES:
        shutil.copy(REPO / f, dist / f)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-qm", "dist"]):
        subprocess.run(cmd, cwd=dist, check=True, capture_output=True)
    return dist


def make_old_install(root: Path) -> Path:
    """An installed .task-manager on the pre-rename layout: the full brief
    under the vendor name, no AGENTS.md anywhere."""
    tm = root / "host" / ".task-manager"
    (tm / "manager" / "core").mkdir(parents=True)
    (tm / "manager" / "local").mkdir()
    (tm / "tasks" / "backlog").mkdir(parents=True)
    (tm / "manager" / "core" / "VERSION").write_text("old\n", encoding="utf-8")
    (tm / "CLAUDE.md").write_text(OLD_BRIEF, encoding="utf-8")
    (tm / "manager" / "local" / "CLAUDE.md").write_text(
        OLD_LOCAL_NOTES, encoding="utf-8")
    (tm / "tasks" / "backlog" / "01-card.md").write_text(
        "# Card\n\n**Status:** Backlog\n", encoding="utf-8")
    shutil.copy(REPO / "update.sh", tm / "update.sh")
    (tm / "update.sh").chmod(0o755)
    return tm


class UpdateRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dist = make_dist(self.tmp)
        self.tm = make_old_install(self.tmp)
        result = subprocess.run(
            ["bash", str(self.tm / "update.sh")],
            env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                 "BENCH_SOURCE": self.dist.as_uri()},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_brief_arrives_under_the_cross_vendor_name(self):
        agents = (self.tm / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents,
                         (REPO / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn("# Task Workflow", agents)

    def test_pointer_replaces_the_old_full_copy(self):
        pointer = (self.tm / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AGENTS.md", pointer)
        self.assertNotEqual(pointer, OLD_BRIEF)

    def test_core_updated_but_project_halves_untouched(self):
        self.assertEqual(
            (self.tm / "manager" / "core" / "VERSION").read_text(),
            "new-version\n")
        self.assertEqual(
            (self.tm / "manager" / "local" / "CLAUDE.md").read_text(
                encoding="utf-8"), OLD_LOCAL_NOTES)
        self.assertTrue((self.tm / "tasks" / "backlog" / "01-card.md").exists())


if __name__ == "__main__":
    unittest.main()
