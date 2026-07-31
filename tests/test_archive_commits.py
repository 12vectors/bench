"""Archiving a card reaches git (task 44), and so does every other write the
board makes to a task file.

A move commits itself; an archive used to rename the file on disk and stop
there, leaving an uncommitted deletion of a tracked file — precisely what
sync refuses to run over. Appended agent reports had the same gap. These
cases run against a throwaway git repo, so the commits are real ones.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402
import watch  # noqa: E402

CARD = ("# 44 — A card that gets archived\n\n"
        "**Status:** Backlog\n"
        "**Priority:** High\n\n"
        "Body text nobody should touch.\n")
FILENAME = "44-a-card.md"
MOVER = "Mover One"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class ArchiveReachesGit(unittest.TestCase):
    def setUp(self):
        # resolve(): macOS tempdirs sit behind the /var → /private/var
        # symlink and git reports the resolved path, so absolute pathspecs
        # only match if we resolve too.
        tmp = Path(tempfile.mkdtemp(prefix="bench-archive-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", MOVER)
        git(self.repo, "config", "user.email", "mover@example.com")

        self.patch(TASKS=self.repo / "tasks", REPO=self.repo,
                   SESSIONS_DIR=tmp / "sessions", COMMIT_MOVES=True)
        state.BOARD_EVENTS.clear()
        state.EXPECTED_MOVES.clear()

        self.write("backlog", CARD)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.baseline = self.commit_count()

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — the repo under test —

    def path(self, stage: str) -> Path:
        return self.repo / "tasks" / stage / FILENAME

    def write(self, stage: str, text: str) -> None:
        self.path(stage).write_text(text, encoding="utf-8")

    def read(self, stage: str) -> str:
        return self.path(stage).read_text(encoding="utf-8")

    def commit_count(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def head_message(self) -> str:
        return git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()

    def head_files(self) -> list[str]:
        # --no-renames: the point is which paths the commit touched, not how
        # git chooses to describe the pair.
        out = git(self.repo, "show", "--name-only", "--no-renames",
                  "--pretty=format:", "HEAD").stdout
        return sorted(line for line in out.splitlines() if line.strip())

    def porcelain(self) -> str:
        return git(self.repo, "status", "--porcelain").stdout

    def dirty(self) -> str:
        """What sync._clean() looks at: tracked files only."""
        return git(self.repo, "status", "--porcelain",
                   "--untracked-files=no").stdout

    # — archiving —

    def test_archiving_commits_the_move_out_of_the_stage(self):
        taskfiles.archive_task(FILENAME, "backlog")

        self.assertEqual(self.commit_count(), self.baseline + 1)
        self.assertEqual(self.head_message(), f"board: 44 → archived ({MOVER})")
        self.assertEqual(self.head_files(),
                         ["tasks/archive/" + FILENAME, "tasks/backlog/" + FILENAME],
                         "both paths, so git records a rename not a delete and an add")
        self.assertEqual(self.porcelain(), "",
                         "nothing is left behind for a human to find later")
        self.assertIn("**Status:** Archived", self.read("archive"))

    def test_the_archive_no_longer_stalls_sync(self):
        """The bug, stated the way it bit: `sync._clean()` reads
        `git status --porcelain --untracked-files=no`, and an uncommitted
        deletion of a tracked file makes it false for every later move."""
        taskfiles.archive_task(FILENAME, "backlog")

        self.assertEqual(self.dirty(), "")

    def test_the_archive_commit_is_pushed_like_a_move(self):
        """Event-driven push hangs off state.task_committed(), so routing
        through the same helper publishes the archive without sync having
        to know it happened."""
        published: list[str] = []
        state.COMMIT_HOOKS.append(published.append)
        self.addCleanup(state.COMMIT_HOOKS.remove, published.append)

        taskfiles.archive_task(FILENAME, "backlog")

        self.assertEqual(published, [FILENAME])

    def test_a_card_git_has_never_seen_is_archived_as_an_addition(self):
        """A brand-new backlog file has no path in HEAD, so naming the
        source in the pathspec would fail the commit outright."""
        fresh = "45-brand-new.md"
        (self.repo / "tasks" / "backlog" / fresh).write_text(
            CARD.replace("44", "45"), encoding="utf-8")

        taskfiles.archive_task(fresh, "backlog")

        self.assertEqual(self.commit_count(), self.baseline + 1)
        self.assertEqual(self.head_message(), f"board: 45 → archived ({MOVER})")
        self.assertEqual(self.head_files(), ["tasks/archive/" + fresh])
        self.assertEqual(self.porcelain(), "")

    def test_the_archive_names_who_did_it_rather_than_the_disk(self):
        taskfiles.archive_task(FILENAME, "backlog")

        self.assertEqual(state.claim_expected(FILENAME, "archived"), "you",
                         "the mover registers itself, as every other move does")

    # — the way back —

    def test_the_undo_commits_its_own_restore(self):
        taskfiles.archive_task(FILENAME, "backlog")

        taskfiles.unarchive_task(FILENAME, "backlog")

        self.assertEqual(self.commit_count(), self.baseline + 2,
                         "one commit out, one commit back")
        self.assertEqual(self.head_message(), f"board: 44 → backlog ({MOVER})")
        self.assertEqual(self.head_files(),
                         ["tasks/archive/" + FILENAME, "tasks/backlog/" + FILENAME])
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(self.read("backlog"), CARD,
                         "the card comes back exactly as it went")

    def test_the_undo_restores_the_status_of_the_stage_it_returns_to(self):
        shutil.move(str(self.path("backlog")), str(self.path("to-do")))
        self.write("to-do", CARD.replace("Backlog", "To Do"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "hand move to to-do")
        self.baseline = self.commit_count()
        taskfiles.archive_task(FILENAME, "to-do")

        taskfiles.unarchive_task(FILENAME, "to-do")

        self.assertIn("**Status:** To Do", self.read("to-do"))
        self.assertEqual(self.head_message(), f"board: 44 → to-do ({MOVER})")

    def test_the_restored_card_is_attributed_to_the_person_on_the_ticker(self):
        """The watcher sees the card reappear in a stage directory; without
        an expectation registered it would call that appearance `disk`."""
        taskfiles.archive_task(FILENAME, "backlog")
        before = {slug: set() for slug in config.STAGE_DIRS}

        taskfiles.unarchive_task(FILENAME, "backlog")
        state.BOARD_EVENTS.clear()
        watch.narrate(before, {slug: ({FILENAME} if slug == "backlog" else set())
                               for slug in config.STAGE_DIRS})

        events = [e for e in state.BOARD_EVENTS if e.get("file") == FILENAME]
        self.assertEqual([e["actor"] for e in events], ["you"])

    # — appended reports —

    def test_an_appended_report_commits_too(self):
        record = {"task": FILENAME, "name": "Wren", "log": "/dev/null"}

        agents._file_report(record, "Work report", "It works.")

        self.assertIn("## Work report", self.read("backlog"))
        self.assertIn("It works.", self.read("backlog"))
        self.assertEqual(self.commit_count(), self.baseline + 1)
        self.assertEqual(self.head_message(), f"board: 44 Work report filed ({MOVER})")
        self.assertEqual(self.head_files(), ["tasks/backlog/" + FILENAME])
        self.assertEqual(self.porcelain(), "",
                         "no run leaves a modified task file behind")

    def test_an_empty_report_writes_nothing_and_commits_nothing(self):
        agents._file_report({"task": FILENAME, "name": "Wren"}, "Work report", "")

        self.assertEqual(self.read("backlog"), CARD)
        self.assertEqual(self.commit_count(), self.baseline)

    # — the gate —

    def test_gate_off_archives_exactly_as_before(self):
        self.patch(COMMIT_MOVES=False)

        taskfiles.archive_task(FILENAME, "backlog")

        self.assertEqual(self.commit_count(), self.baseline)
        self.assertIn("**Status:** Archived", self.read("archive"))
        self.assertEqual(self.porcelain(),
                         " D tasks/backlog/44-a-card.md\n?? tasks/archive/\n",
                         "the archive stays for a human to commit, index untouched")

    def test_gate_off_files_a_report_without_committing_it(self):
        self.patch(COMMIT_MOVES=False)

        agents._file_report({"task": FILENAME, "name": "Wren"}, "Work report", "Done.")

        self.assertIn("Done.", self.read("backlog"))
        self.assertEqual(self.commit_count(), self.baseline)


if __name__ == "__main__":
    unittest.main()
