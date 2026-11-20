"""A move whose commit failed is recorded by the card's next commit — its
next move, or its next edit under the failed move's own trailers (task 155).

Release 0.6's candidate failed CI because two of the board's commits met
on one work-state index: one commit of a card's move failed, the card had
moved on disk, and the deletion of its old path was never recorded — the
card's later commits named only the paths *they* moved between. The stale
path stayed a local change for good: sync paused over it and merges that
touched it refused.

Every case here runs against a throwaway git repository, single-threaded.
The failed commit is made deterministic rather than timed: the
work-state index is held — `index.lock` created exclusively, exactly as a
concurrent git creates it — for the whole of that one commit, and let go
when it returns.

    python3 -m unittest tests.test_failed_move_commit -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import movements  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CARD = ("# 21 — Export model\n\n"
        "**Status:** To Do\n"
        "**Priority:** High\n\n"
        "Build the export model.\n")
FILENAME = "21-export-model.md"
MOVER = "ada"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class _FailedMoveCommit:
    """Mixed into one TestCase per layout: where tasks/ sits in the
    work-state repository is all that differs."""

    TASKS_UNDER = ""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-failed-move-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        self.tasks = self.repo / self.TASKS_UNDER / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
            (self.tasks / slug / ".gitkeep").write_text("", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", MOVER)
        git(self.repo, "config", "user.email", "ada@example.com")
        self.patch(TASKS=self.tasks, REPO=self.repo, SESSIONS_DIR=tmp / "sessions",
                   STATE=tmp / "state", COMMIT_MOVES=True)
        state.BOARD_EVENTS.clear()
        (self.repo / "unrelated.txt").write_text("one\n", encoding="utf-8")
        (self.tasks / "to-do" / FILENAME).write_text(CARD, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.assertEqual(config.state_repo(), self.repo)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def rel(self, stage: str, filename: str = FILENAME) -> str:
        return str((self.tasks / stage / filename).relative_to(self.repo))

    def porcelain(self) -> str:
        return git(self.repo, "status", "--porcelain", "--untracked-files=all").stdout

    def head_has(self, stage: str, filename: str = FILENAME) -> bool:
        return bool(git(self.repo, "ls-tree", "--name-only", "HEAD", "--",
                        self.rel(stage, filename)).stdout.strip())

    def failing_move(self, source: str, target: str, filename: str = FILENAME) -> dict:
        """The move's commit meets a held index on every attempt. Returns
        what happened, so a lock that was never held cannot pass for a
        reproduction."""
        lock = self.repo / ".git" / "index.lock"
        seen = {"held": False, "committed": None, "heldAtEnd": None}
        real = taskfiles.commit_paths

        def commit_paths(filename, message, spec, failure):
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            seen["held"] = True
            try:
                seen["committed"] = real(filename, message, spec, failure)
            finally:
                seen["heldAtEnd"] = lock.is_file()
                lock.unlink()
            return seen["committed"]

        taskfiles.commit_paths = commit_paths
        try:
            taskfiles.move_task(filename, source, target, actor="agent")
        finally:
            taskfiles.commit_paths = real
        self.assertEqual(seen, {"held": True, "committed": False, "heldAtEnd": True},
                         "the move's commit must have met the held index and failed")
        return seen

    def staged_then_failing_move(self, source: str, target: str) -> dict:
        """The full-suite failure's shape (task 155): the move's `git add`
        succeeds — the old path's deletion and the new path are *staged* —
        and only then is the index held, so its `git commit` (and every
        retry) fails. The index then no longer lists the old path, though
        HEAD still does. Returns what happened; a lock never placed after
        a staged add is not a reproduction."""
        lock = self.repo / ".git" / "index.lock"
        seen = {"staged": False, "committed": None, "heldAtEnd": None}
        real_git, real_commit = taskfiles._git, taskfiles.commit_paths

        def _git(*args, **kwargs):
            done = real_git(*args, **kwargs)
            if args[:1] == ("add",) and done.returncode == 0 and not seen["staged"]:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                seen["staged"] = True
            return done

        def commit_paths(filename, message, spec, failure):
            taskfiles._git = _git
            try:
                seen["committed"] = real_commit(filename, message, spec, failure)
            finally:
                taskfiles._git = real_git
                seen["heldAtEnd"] = lock.is_file()
                if lock.is_file():
                    lock.unlink()
            return seen["committed"]

        taskfiles.commit_paths = commit_paths
        try:
            taskfiles.move_task(FILENAME, source, target, actor="agent")
        finally:
            taskfiles.commit_paths = real_commit
            taskfiles._git = real_git
        self.assertEqual(seen, {"staged": True, "committed": False, "heldAtEnd": True},
                         "the move must have staged, then met the held index and failed")
        return seen

    # — the reproduction: the state a failed move commit leaves —

    def test_a_staged_then_failed_move_commit_leaves_the_move_uncommitted(self):
        """What the staged-then-failed commit leaves: the new path staged,
        HEAD unchanged. Before the repair the move's `git add` also staged
        the old path's deletion, so the index stopped listing it (the
        red commit before the repair shows that state); now only what is
        on disk is staged and the deletion is left for the commit."""
        self.staged_then_failing_move("to-do", "in-progress")

        self.assertTrue(self.head_has("to-do"))
        self.assertFalse(self.head_has("in-progress"))
        self.assertIn(f"A  {self.rel('in-progress')}\n", self.porcelain())
        self.assertIn(self.rel("to-do"), self.porcelain(), "the deletion is a local change")

    def test_an_edit_records_a_deletion_only_head_still_lists(self):
        """The index no longer lists the old path but HEAD does — the
        state the pre-repair add left, and any staged deletion. The edit
        still finds it, by asking HEAD as well as the index."""
        self.staged_then_failing_move("to-do", "in-progress")
        git(self.repo, "rm", "-q", "--cached", "--", self.rel("to-do"))
        self.assertEqual(git(self.repo, "ls-files", "--", self.rel("to-do")).stdout, "")
        self.assertTrue(self.head_has("to-do"))

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n", "report")

        self.assertEqual(self.porcelain(), "")
        self.assertFalse(self.head_has("to-do"))
        self.assertIn("Bench-Move-Id:", git(self.repo, "log", "-1", "--format=%B").stdout)

    def test_the_cards_next_edit_records_a_staged_then_failed_move(self):
        """The 06:58 full-suite failure (task 155): the standalone card's
        move into review/ staged and then failed to commit. Its next edit
        looked for the stale path in the *index* only, found none, named
        only review/ — so git read the card as created there (eligibility
        `no-movement`), and the staged deletion of in-progress/ was the
        local change the phase's merge refused over."""
        self.staged_then_failing_move("to-do", "in-progress")
        [move_id] = self.journal_ids()

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n\nDone.\n", "report")

        self.assertEqual(self.porcelain(), "", "nothing the board did is left unrecorded")
        self.assertFalse(self.head_has("to-do"))
        self.assertTrue(self.head_has("in-progress"))
        message = git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertIn(f"Bench-Move-Id: {move_id}", message)
        movements._MEMO.clear()
        newest = movements.history(FILENAME)["events"][0]
        self.assertEqual((newest["source"], newest["from"], newest["to"]),
                         ("commit", "to-do", "in-progress"))

    def test_the_cards_next_move_records_a_staged_then_failed_move(self):
        self.staged_then_failing_move("to-do", "in-progress")

        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")

        self.assertEqual(self.porcelain(), "")
        self.assertFalse(self.head_has("to-do"))
        self.assertFalse(self.head_has("in-progress"))
        self.assertTrue(self.head_has("review"))

    def test_unrelated_staged_work_survives_a_staged_then_failed_moves_healing(self):
        self.staged_then_failing_move("to-do", "in-progress")
        (self.repo / "unrelated.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "--", "unrelated.txt")

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n", "report")

        files = git(self.repo, "show", "--name-only", "--no-renames",
                    "--pretty=format:", "HEAD").stdout.split()
        self.assertEqual(sorted(files), sorted([self.rel("to-do"), self.rel("in-progress")]))
        self.assertEqual(self.porcelain(), "M  unrelated.txt\n")

    def test_a_failed_move_commit_leaves_the_old_path_unrecorded(self):
        self.failing_move("to-do", "in-progress")

        self.assertTrue((self.tasks / "in-progress" / FILENAME).is_file(),
                        "the disk is the truth: the card moved")
        self.assertTrue(self.head_has("to-do"))
        self.assertFalse(self.head_has("in-progress"))
        self.assertIn(f" D {self.rel('to-do')}\n", self.porcelain())
        self.assertIn(f"?? {self.rel('in-progress')}\n", self.porcelain())
        self.assertTrue(any("committing it failed" in e.get("summary", "")
                            for e in state.BOARD_EVENTS),
                        "the failure is said, not swallowed")

    # — the repair —

    def test_the_cards_next_move_records_the_deletion_left_behind(self):
        self.failing_move("to-do", "in-progress")

        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")

        self.assertEqual(self.porcelain(), "", "nothing the board did is left unrecorded")
        self.assertFalse(self.head_has("to-do"), "the stale path is gone from HEAD")
        self.assertFalse(self.head_has("in-progress"))
        self.assertTrue(self.head_has("review"))
        message = git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertIn("Bench-Mover-Kind: automated", message,
                      "the healing rides a move's commit, with its trailers")

    def journal_ids(self) -> list[str]:
        path = taskfiles.movement_journal()
        if not path.is_file():
            return []
        import json
        return [json.loads(line)["id"] for line in path.read_text().splitlines()
                if line.strip()]

    def test_the_cards_next_edit_records_the_failed_move_as_that_move(self):
        """The full-suite failure (task 155): the card's next commit after
        a failed move was an edit, not a move. It used to name only the
        new path, so git read the card as created there — no mover — and
        the old path's deletion stayed a local change. Now the edit's
        commit records the move too, under that move's own trailers from
        the journal: the same move id, mover and kind."""
        self.failing_move("to-do", "in-progress")
        [move_id] = self.journal_ids()

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n\nDone.\n", "report")

        self.assertEqual(self.porcelain(), "", "nothing the board did is left unrecorded")
        self.assertFalse(self.head_has("to-do"))
        self.assertTrue(self.head_has("in-progress"))
        self.assertIn("Done.", git(self.repo, "show", f"HEAD:{self.rel('in-progress')}").stdout)
        message = git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertIn(f"Bench-Move-Id: {move_id}", message)
        self.assertIn(f"Bench-Mover: {MOVER}", message)
        self.assertIn("Bench-Mover-Kind: automated", message)
        movements._MEMO.clear()
        history = movements.history(FILENAME)["events"]
        newest = history[0]
        self.assertEqual((newest["source"], newest["kind"], newest["from"], newest["to"]),
                         ("commit", "automated", "to-do", "in-progress"), history)
        self.assertEqual(sum(1 for e in history if e.get("id") == move_id), 1,
                         "the journal line is the commit's, not counted twice")

    def test_an_edit_does_not_attribute_a_move_the_board_did_not_make(self):
        """A card moved by hand has no journal line: the board will not
        put a mover's name on it, so its edit names only its own path,
        exactly as before."""
        (self.tasks / "in-progress").mkdir(exist_ok=True)
        shutil.move(str(self.tasks / "to-do" / FILENAME),
                    str(self.tasks / "in-progress" / FILENAME))

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Note\n", "note")

        self.assertTrue(self.head_has("to-do"), "the hand move is not recorded for it")
        self.assertIn(f" D {self.rel('to-do')}\n", self.porcelain())
        self.assertNotIn("Bench-Move", git(self.repo, "log", "-1", "--format=%B").stdout)

    def test_an_edit_in_another_stage_does_not_claim_the_failed_move(self):
        """The journal's newest line for the card must name the stage the
        edit is in; a failed move to somewhere else is not this edit's to
        record."""
        self.failing_move("to-do", "in-progress")
        # the card is moved on by hand afterwards: no journal line names review/
        shutil.move(str(self.tasks / "in-progress" / FILENAME),
                    str(self.tasks / "review" / FILENAME))

        taskfiles.append_to_task(FILENAME, "review", "\n## Note\n", "note")

        self.assertTrue(self.head_has("to-do"))
        self.assertNotIn("Bench-Move", git(self.repo, "log", "-1", "--format=%B").stdout)

    def test_unrelated_staged_work_survives_an_edits_healing(self):
        self.failing_move("to-do", "in-progress")
        (self.repo / "unrelated.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "--", "unrelated.txt")

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n", "report")

        files = git(self.repo, "show", "--name-only", "--no-renames",
                    "--pretty=format:", "HEAD").stdout.split()
        self.assertEqual(sorted(files), sorted([self.rel("to-do"), self.rel("in-progress")]))
        self.assertEqual(self.porcelain(), "M  unrelated.txt\n")

    def test_team_sync_is_paused_by_the_stale_path_until_the_next_move(self):
        """A consequence on a shared board: sync replays and fast-forwards
        only over a checkout whose tracked files are clean, so the stale
        deletion paused it. Its own predicate, read before and after."""
        import sync
        self.assertTrue(sync._clean())
        self.failing_move("to-do", "in-progress")
        self.assertFalse(sync._clean(), "the unrecorded deletion is a tracked change")

        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")
        self.assertTrue(sync._clean(), "the next move recorded it — nothing forced")

    def test_unrelated_staged_work_survives_the_healing(self):
        self.failing_move("to-do", "in-progress")
        (self.repo / "unrelated.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "--", "unrelated.txt")

        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")

        files = git(self.repo, "show", "--name-only", "--no-renames",
                    "--pretty=format:", "HEAD").stdout.split()
        self.assertEqual(sorted(files), sorted([self.rel("to-do"), self.rel("review")]))
        self.assertEqual(self.porcelain(), "M  unrelated.txt\n",
                         "the developer's staged change is still staged, and only it")

    def test_a_copy_still_on_disk_is_never_swept_in(self):
        """Only a path that is gone from disk is recorded as removed: a
        second copy of the card someone left in another stage stays."""
        twin = self.tasks / "backlog" / FILENAME
        twin.write_text(CARD.replace("To Do", "Backlog"), encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "a twin")

        taskfiles.move_task(FILENAME, "to-do", "in-progress", actor="agent")

        self.assertTrue(self.head_has("backlog"), "the twin is untouched")
        self.assertEqual(self.porcelain(), "")

    def test_another_cards_stale_path_is_not_this_cards_to_record(self):
        other = "22-export-button.md"
        (self.tasks / "to-do" / other).write_text(CARD.replace("21 — Export model",
                                                               "22 — Export button"),
                                                  encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "another")
        (self.tasks / "to-do" / other).unlink()   # someone's own, unrecorded

        taskfiles.move_task(FILENAME, "to-do", "in-progress", actor="agent")

        self.assertEqual(self.porcelain(), f" D {self.rel('to-do', other)}\n")

    def test_the_failed_moves_provenance_still_reads(self):
        """The failed move is in the machine-local journal, and the healing
        move's trailers are the board's own: the card's eligibility is
        read as it was before, not as an unattributed move."""
        def read() -> dict:
            board = taskfiles.collect()
            task = next(t for s in board["stages"] for t in s["tasks"]
                        if t["file"] == FILENAME)
            return movements.eligibility(task)

        before = read()
        self.failing_move("to-do", "in-progress")
        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")
        after = read()
        self.assertNotEqual(after.get("code"), "ambiguous-move", after)
        self.assertEqual(after.get("code"), before.get("code"), (before, after))

    # — names git quotes (review at 00a7cc3c6b2e) —

    QUOTED_NAMES = ("23-café-export.md", '24-the-"export".md')

    def add_card(self, filename: str) -> None:
        (self.tasks / "to-do" / filename).write_text(
            CARD.replace("21 — Export model", filename[:-3]), encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"add {filename}")

    def test_a_card_whose_name_git_quotes_still_commits_its_move(self):
        """Git C-quotes a name with a non-ASCII character or a `"` in a
        listing read line by line. The quoted text is a path that exists
        nowhere, so it read as a stale copy, and naming it failed the
        commit of every ordinary move of such a card."""
        for name in self.QUOTED_NAMES:
            with self.subTest(name=name):
                self.add_card(name)
                self.assertEqual(taskfiles._stale_copies(
                    self.tasks / "in-progress" / name), [])

                taskfiles.move_task(name, "to-do", "in-progress", actor="agent")

                self.assertEqual(self.porcelain(), "")
                self.assertFalse(self.head_has("to-do", name))
                self.assertTrue(self.head_has("in-progress", name))
                self.assertFalse(any("committing it failed" in e.get("summary", "")
                                     for e in state.BOARD_EVENTS), state.BOARD_EVENTS)

    def test_a_quoted_cards_failed_move_is_recorded_by_its_next_edit(self):
        for name in self.QUOTED_NAMES:
            with self.subTest(name=name):
                self.add_card(name)
                self.failing_move("to-do", "in-progress", name)
                self.assertEqual(taskfiles._stale_copies(
                    self.tasks / "in-progress" / name),
                    [str(self.tasks / "to-do" / name)])

                taskfiles.append_to_task(name, "in-progress", "\n## Report\n", "report")

                self.assertEqual(self.porcelain(), "")
                self.assertFalse(self.head_has("to-do", name))
                self.assertIn("Bench-Move-Id:",
                              git(self.repo, "log", "-1", "--format=%B").stdout)

    # — an old journal line is never used twice (review at 00a7cc3c6b2e) —

    def healed_failed_move(self) -> str:
        """A move to-do → in-progress whose commit failed, recorded since
        by the card's next edit. Returns that move's id."""
        self.failing_move("to-do", "in-progress")
        [move_id] = self.journal_ids()
        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Report\n", "report")
        self.assertIn(f"Bench-Move-Id: {move_id}",
                      git(self.repo, "log", "-1", "--format=%B").stdout)
        self.assertEqual(self.porcelain(), "")
        return move_id

    def test_a_hand_move_back_into_the_stage_is_not_given_an_old_moves_trailers(self):
        """The journal line for to-do → in-progress has been recorded. The
        card then moves in-progress → review (committed) and is put back
        into in-progress by hand. The stale path is review/, which the
        old line does not come from: the edit names its own path only."""
        self.healed_failed_move()
        taskfiles.move_task(FILENAME, "in-progress", "review", actor="agent")
        self.assertEqual(self.porcelain(), "")
        shutil.move(str(self.tasks / "review" / FILENAME),
                    str(self.tasks / "in-progress" / FILENAME))

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Note\n", "note")

        self.assertTrue(self.head_has("review"), "the hand move is not recorded for it")
        self.assertNotIn("Bench-Move", git(self.repo, "log", "-1", "--format=%B").stdout)

    def test_a_recorded_moves_journal_line_is_not_used_again(self):
        """The same, where the hand move retraces the old move exactly —
        to-do → in-progress — so only the move id already being in a
        commit tells the old line apart from a new failed move."""
        self.healed_failed_move()
        taskfiles.move_task(FILENAME, "in-progress", "to-do", actor="agent")
        self.assertEqual(self.porcelain(), "")
        shutil.move(str(self.tasks / "to-do" / FILENAME),
                    str(self.tasks / "in-progress" / FILENAME))

        taskfiles.append_to_task(FILENAME, "in-progress", "\n## Note\n", "note")

        self.assertTrue(self.head_has("to-do"), "the hand move is not recorded for it")
        self.assertNotIn("Bench-Move", git(self.repo, "log", "-1", "--format=%B").stdout)


class Vendored(_FailedMoveCommit, unittest.TestCase):
    TASKS_UNDER = ".task-manager"


class SourceHosted(_FailedMoveCommit, unittest.TestCase):
    TASKS_UNDER = ""


class External(_FailedMoveCommit, unittest.TestCase):
    """The work state's own repository, apart from the application's:
    `config.EXTERNAL` on, the application a separate checkout, and the
    state repository found from where tasks/ is. No commit may reach the
    application."""
    TASKS_UNDER = ""

    def setUp(self):
        super().setUp()
        app = self.repo.parent / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(app)],
                       check=True, capture_output=True)
        git(app, "config", "user.name", MOVER)
        git(app, "config", "user.email", "ada@example.com")
        (app / "app.txt").write_text("app\n", encoding="utf-8")
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", "app root")
        self.app_head = git(app, "rev-parse", "HEAD").stdout.strip()
        self.app = app
        self.patch(EXTERNAL=True, REPO=app)
        self.assertEqual(config.state_repo(), self.repo)
        self.assertFalse(config.app_shares_state_repository())
        self.addCleanup(self.application_untouched)

    def application_untouched(self) -> None:
        self.assertEqual(git(self.app, "rev-parse", "HEAD").stdout.strip(),
                         self.app_head)
        self.assertEqual(git(self.app, "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
