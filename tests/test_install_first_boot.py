"""install.py's first-boot cleaning: a vendored clone's very first run
clears the distribution's own cards so a new host starts with a pristine
board, and no later run ever touches the host's own. Run with:
python3 -m unittest discover -s tests

install.py is exercised end-to-end as a subprocess against scratch host
layouts — the same entry point start.sh uses — so what is asserted is
what a real first boot does to disk.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES = ["backlog", "to-do", "in-progress", "review", "done"]
KEEP = {".gitkeep", "task-template.md"}


def make_host(root: Path) -> Path:
    """A host project with a freshly vendored .task-manager: the real
    install.py and claude adapter, plus the distribution's shipped cards."""
    host = root / "host"
    (host / ".claude").mkdir(parents=True)
    tm = host / ".task-manager"
    tm.mkdir()
    shutil.copy(REPO / "install.py", tm / "install.py")
    shutil.copytree(REPO / "manager" / "core" / "adapters" / "claude",
                    tm / "manager" / "core" / "adapters" / "claude")
    for stage in STAGES + ["archive"]:
        d = tm / "tasks" / stage
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
        (d / "00-shipped-card.md").write_text("# Shipped\n", encoding="utf-8")
    (tm / "tasks" / "task-template.md").write_text("# Template\n", encoding="utf-8")
    for extra in ["plans", "reference"]:
        d = tm / extra
        d.mkdir()
        (d / ".gitkeep").touch()
        (d / "shipped.md").write_text("shipped\n", encoding="utf-8")
    (tm / "reference" / "shots").mkdir()
    (tm / "reference" / "shots" / "board.png").write_bytes(b"png")
    return tm


def run_install(tm: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    return subprocess.run(
        [sys.executable, str(tm / "install.py"), *args],
        capture_output=True, text=True, cwd=tm.parent, env=env)


def shipped_files(tm: Path) -> list[Path]:
    """Every file under the cleaned directories that first boot should
    have removed — empty means the board is pristine."""
    return [p
            for top in [tm / "tasks", tm / "plans", tm / "reference"]
            for p in top.rglob("*")
            if p.is_file() and p.name not in KEEP]


class FirstBoot(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.tm = make_host(self.scratch)

    def test_first_run_clears_shipped_content_and_prints_each_removal(self):
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(shipped_files(self.tm), [])
        for stage in STAGES + ["archive"]:
            self.assertTrue((self.tm / "tasks" / stage / ".gitkeep").is_file())
        self.assertTrue((self.tm / "tasks" / "task-template.md").is_file())
        self.assertTrue((self.tm / "plans" / ".gitkeep").is_file())
        self.assertTrue((self.tm / "reference" / ".gitkeep").is_file())
        for line in ["tasks/backlog/00-shipped-card.md",
                     "tasks/archive/00-shipped-card.md",
                     "plans/shipped.md", "reference/shots"]:
            self.assertIn(f"removed  {line}", result.stdout)
        self.assertTrue((self.tm / "manager" / "local" / "state").is_dir())

    def test_second_run_removes_nothing_and_host_cards_survive(self):
        run_install(self.tm)
        card = self.tm / "tasks" / "backlog" / "20-host-card.md"
        card.write_text("# The host's own\n", encoding="utf-8")
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed", result.stdout)
        self.assertTrue(card.is_file())
        self.assertIn("ok", result.stdout)

    def test_dry_run_lists_without_removing(self):
        result = run_install(self.tm, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would remove  tasks/backlog/00-shipped-card.md",
                      result.stdout)
        self.assertNotIn("removed  ", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((self.tm / "manager" / "local" / "state").exists())

    def test_existing_env_file_disarms_the_guard(self):
        local = self.tm / "manager" / "local"
        local.mkdir(parents=True)
        (local / ".env").write_text("BOARD_PORT=26071\n", encoding="utf-8")
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((local / "state").exists())

    def test_symlinked_leftover_is_unlinked_not_followed(self):
        """A symlink among the leftovers is removed as a link — the
        directory it points to survives untouched."""
        outside = self.scratch / "outside"
        outside.mkdir()
        (outside / "precious.md").write_text("keep me\n", encoding="utf-8")
        link = self.tm / "tasks" / "backlog" / "10-linked"
        link.symlink_to(outside)
        result = run_install(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(link.is_symlink())
        self.assertFalse(link.exists())
        self.assertTrue((outside / "precious.md").is_file())

    def test_self_hosted_repo_is_never_cleaned(self):
        """When the manager IS the repo (bench itself, or a dev clone of
        it), tasks/ is that repo's history — even unwired, never touched."""
        subprocess.run(["git", "init", "-q", str(self.tm)], check=True,
                       capture_output=True)
        (self.tm / ".claude").mkdir()
        result = run_install(self.tm)
        self.assertNotIn("removed", result.stdout)
        self.assertNotEqual(shipped_files(self.tm), [])
        self.assertFalse((self.tm / "manager" / "local" / "state").exists())


if __name__ == "__main__":
    unittest.main()
