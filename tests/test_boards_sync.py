"""Boards sync through origin/main (task 19): a move pushes, a beat pulls,
a lost race is a toast, and a human's unpushed commit is never published.

Everything runs against real clones of a real bare upstream — the whole
point of the card is what git actually does under a race, so nothing here
is mocked except the SSE fan-out (captured, to read what the board said).

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402
import watch  # noqa: E402

FILENAME = "07-shared-card.md"
CARD = ("# 07 — A card two boards can reach\n\n"
        "**Status:** Backlog\n"
        "**Priority:** High\n"
        "**Type:** Feature\n\n"
        "Body text long enough that git sees a rename rather than a delete\n"
        "and an add when the file moves between two stage directories, which\n"
        "is what turns a same-card race into a conflict it can report.\n")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class TwoBoards(unittest.TestCase):
    """One bare upstream, two clones — 'ada' and 'elena', each with its own
    board. config.REPO/TASKS point at whichever board is acting."""

    def setUp(self):
        # resolve(): macOS tempdirs sit behind the /var → /private/var
        # symlink and git reports the resolved path.
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-sync-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)

        self.ada = self._clone("ada")
        for slug in config.STAGE_DIRS:
            (self.ada / "tasks" / slug).mkdir(parents=True)
        (self.ada / "tasks" / "backlog" / FILENAME).write_text(CARD, encoding="utf-8")
        (self.ada / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.ada, "add", "-A")
        git(self.ada, "commit", "-q", "-m", "root")
        git(self.ada, "push", "-q", "origin", "main")
        self.elena = self._clone("elena")

        # REPO/TASKS are patched here (not just assigned by use()) so the
        # checkout under test is restored for every other test module.
        self.patch(SYNC=True, COMMIT_MOVES=True, FETCH_TIMEOUT=10.0,
                   SESSIONS_DIR=self.tmp / "sessions",
                   REPO=self.ada, TASKS=self.ada / "tasks")
        self.use(self.ada)

        state.BOARD_EVENTS.clear()
        state.EXPECTED_MOVES.clear()
        state.COMMIT_HOOKS.clear()
        self.addCleanup(state.COMMIT_HOOKS.clear)
        sync.ARRIVED.clear()
        sync._NOTES.clear()
        self.addCleanup(sync._NOTES.clear)
        self.addCleanup(sync.ARRIVED.clear)

        self.broadcasts: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.broadcasts.append

    def _clone(self, who: str) -> Path:
        path = self.tmp / who
        subprocess.run(["git", "clone", "-q", str(self.origin), str(path)],
                       check=True, capture_output=True)
        git(path, "config", "user.name", who)
        git(path, "config", "user.email", f"{who}@example.com")
        return path

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — acting as one board or the other —

    def use(self, board: Path) -> None:
        config.REPO, config.TASKS = board, board / "tasks"

    def move(self, board: Path, source: str, target: str) -> None:
        self.use(board)
        taskfiles.move_task(FILENAME, source, target)

    def stage_of(self, board: Path) -> str | None:
        for slug in config.STAGE_DIRS:
            if (board / "tasks" / slug / FILENAME).is_file():
                return slug
        return None

    def card(self, board: Path) -> str:
        stage = self.stage_of(board)
        return (board / "tasks" / stage / FILENAME).read_text(encoding="utf-8")

    def head(self, board: Path) -> str:
        return git(board, "rev-parse", "HEAD").stdout.strip()

    def origin_head(self) -> str:
        return git(self.origin, "rev-parse", "main").stdout.strip()

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def toasts(self) -> list[str]:
        return [b["message"] for b in self.broadcasts if b.get("type") == "toast"]

    def sig(self, board: Path) -> dict[str, set[str]]:
        self.use(board)
        return {slug: {p.name for p in (board / "tasks" / slug).glob("*.md")}
                for slug in config.STAGE_DIRS
                if (board / "tasks" / slug).is_dir()}

    # — a move reaching the other board —

    def test_a_move_pushes_and_the_other_board_pulls_it(self):
        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")

        self.use(self.elena)
        self.assertEqual(sync.pull_now(), "pulled")

        self.assertEqual(self.stage_of(self.elena), "to-do")
        self.assertEqual(self.card(self.elena), self.card(self.ada),
                         "both boards hold the same bytes")
        self.assertIn("**Assignee:** ada", self.card(self.elena))

    def test_the_pull_attributes_the_move_to_its_author(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()

        self.use(self.elena)
        before = self.sig(self.elena)
        sync.pull_now()
        after = self.sig(self.elena)

        # Two boards are two processes: elena's has no memory of ada moving
        # anything, so nothing but the pull can attribute this.
        state.EXPECTED_MOVES.clear()
        state.BOARD_EVENTS.clear()
        watch.narrate(before, after)
        moves = [e for e in state.BOARD_EVENTS if e["kind"] == "move"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["actor"], "ada",
                         "a move that arrived over origin is not 'disk'")
        self.assertEqual((moves[0]["from"], moves[0]["to"]), ("backlog", "to-do"))

    def test_an_archive_publishes_itself_and_empties_the_other_board(self):
        """Task 44: an archive is a board-made move like any other, so it
        commits, pushes and reaches the teammate — where the card is simply
        gone from every column rather than lingering as one it never saw
        leave."""
        self.use(self.ada)
        taskfiles.archive_task(FILENAME, "backlog")
        self.assertEqual(sync.push_now(), "ok")

        self.use(self.elena)
        self.assertEqual(sync.pull_now(), "pulled")

        self.assertIsNone(self.stage_of(self.elena), "out of every column")
        self.assertTrue((self.elena / "tasks" / "archive" / FILENAME).is_file(),
                        "never deleted: a fresh clone still has the card")

    def test_attribution_is_consumed_once_and_expires(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()
        self.use(self.elena)
        sync.pull_now()

        self.assertEqual(sync.arrived_actor(FILENAME), "ada")
        self.assertEqual(sync.arrived_actor(FILENAME), "",
                         "the next hand-move on this disk is not ada's")

    def test_a_pull_that_brings_nothing_says_nothing(self):
        self.use(self.elena)
        state.BOARD_EVENTS.clear()

        self.assertEqual(sync.pull_now(), "up-to-date")
        self.assertEqual(self.summaries(), [])

    def test_the_commit_hook_publishes_without_being_asked(self):
        sync.install()
        self.assertIn(sync.on_commit, state.COMMIT_HOOKS)

        before = self.origin_head()
        self.move(self.ada, "backlog", "to-do")
        for _ in range(100):                       # the push runs off-thread
            if self.origin_head() != before:
                break
            time.sleep(0.05)

        self.assertEqual(self.origin_head(), self.head(self.ada),
                         "the move published itself")

    # — the same-card race —

    def test_losing_a_race_undoes_the_move_and_names_who_took_the_card(self):
        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")

        # elena never saw ada's move: her board still shows the card in
        # backlog/ and she moves it somewhere else entirely.
        self.move(self.elena, "backlog", "in-progress")
        self.assertEqual(sync.push_now(), "pulled")

        self.assertEqual(self.stage_of(self.elena), "to-do",
                         "the loser's board holds the winner's version")
        self.assertEqual(self.card(self.elena), self.card(self.ada))
        self.assertIn("**Assignee:** ada", self.card(self.elena))
        self.assertEqual(self.origin_head(), self.head(self.elena))
        self.assertIn("07 claimed by ada — your move was undone", self.toasts())
        self.assertIn("07 claimed by ada — your move was undone", self.summaries())

    def test_exactly_one_claim_survives_when_both_claim_the_same_card(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()
        self.move(self.elena, "backlog", "to-do")     # same target, other name
        sync.push_now()

        self.use(self.ada)
        sync.pull_now()

        self.assertEqual(self.card(self.ada), self.card(self.elena),
                         "both boards converge on the same file bytes")
        self.assertEqual(self.card(self.elena).count("**Assignee:**"), 1)
        self.assertIn("**Assignee:** ada", self.card(self.elena))
        self.assertEqual(self.origin_head(), self.head(self.ada))
        self.assertEqual(self.origin_head(), self.head(self.elena))

    def test_the_winner_keeps_moving_after_the_loser_gave_way(self):
        """Convergence is not a dead end: the board that lost re-reads the
        card and can move it on, and that move publishes normally."""
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()
        self.move(self.elena, "backlog", "in-progress")
        sync.push_now()

        self.move(self.elena, "to-do", "in-progress")
        self.assertEqual(sync.push_now(), "ok")

        self.use(self.ada)
        sync.pull_now()
        self.assertEqual(self.stage_of(self.ada), "in-progress")

    def test_a_race_on_two_different_cards_keeps_both_moves(self):
        other = "08-another-card.md"
        (self.ada / "tasks" / "backlog" / other).write_text(
            CARD.replace("# 07", "# 08"), encoding="utf-8")
        git(self.ada, "add", "-A")
        git(self.ada, "commit", "-q", "-m", "board: 08 → backlog (ada)")
        git(self.ada, "push", "-q", "origin", "main")
        self.use(self.elena)
        sync.pull_now()

        self.move(self.ada, "backlog", "to-do")
        sync.push_now()
        self.use(self.elena)
        taskfiles.move_task(other, "backlog", "to-do")

        self.assertEqual(sync.push_now(), "pulled")
        self.assertTrue((self.elena / "tasks" / "to-do" / other).is_file(),
                        "elena's own move survives a rebase it does not collide with")
        self.assertTrue((self.elena / "tasks" / "to-do" / FILENAME).is_file())
        self.assertEqual(self.toasts(), [], "nothing was undone")

    def test_a_drag_started_before_the_card_moved_underneath_is_refused(self):
        """The mid-drag race. The browser sends the stage it picked the card
        up from, so a move that arrived meanwhile makes the drop stale — and
        a stale drop must fail, not resurrect the card in two places."""
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()
        self.use(self.elena)
        sync.pull_now()

        with self.assertRaises(ValueError) as caught:
            taskfiles.move_task(FILENAME, "backlog", "in-progress")

        self.assertIn("no longer in backlog/", str(caught.exception))
        self.assertEqual(self.stage_of(self.elena), "to-do")
        self.assertFalse((self.elena / "tasks" / "in-progress" / FILENAME).exists())

    # — the piggyback guard —

    def test_a_human_commit_on_main_stops_the_push(self):
        self.use(self.ada)
        (self.ada / "code.txt").write_text("my unpushed experiment\n", encoding="utf-8")
        git(self.ada, "commit", "-qam", "wip: not ready for anyone else")
        before = self.origin_head()

        self.move(self.ada, "backlog", "to-do")

        self.assertEqual(sync.push_now(), "stray")
        self.assertEqual(self.origin_head(), before, "origin never saw it")
        self.assertNotIn("wip: not ready for anyone else",
                         git(self.origin, "log", "--format=%s", "main").stdout)
        warnings = [s for s in self.summaries() if "not a board commit" in s]
        self.assertEqual(len(warnings), 1)
        self.assertIn("wip: not ready for anyone else", warnings[0])
        self.assertEqual(sync.status()["state"], "stalled")

    def test_the_guard_warns_once_however_often_the_beat_runs(self):
        self.use(self.ada)
        git(self.ada, "commit", "-q", "--allow-empty", "-m", "wip: mine")
        self.move(self.ada, "backlog", "to-do")

        for _ in range(4):
            sync.push_now()
            sync.pull_now()

        self.assertEqual(len([s for s in self.summaries() if "not a board commit" in s]), 1)

    def test_a_human_commit_also_blocks_the_rebase_and_says_so(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()

        self.use(self.elena)
        (self.elena / "code.txt").write_text("elena's experiment\n", encoding="utf-8")
        git(self.elena, "commit", "-qam", "wip: elena's own work")
        before = self.head(self.elena)

        self.assertEqual(sync.pull_now(), "diverged")
        self.assertEqual(self.head(self.elena), before,
                         "nothing was rebased over the human's commit")
        self.assertEqual(self.stage_of(self.elena), "backlog")
        self.assertTrue(any("diverged" in s for s in self.summaries()))
        self.assertEqual(sync.status()["state"], "stalled")

    def test_the_guard_stands_down_once_the_human_commit_is_gone(self):
        self.use(self.ada)
        git(self.ada, "commit", "-q", "--allow-empty", "-m", "wip: mine")
        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "stray")

        git(self.ada, "push", "-q", "origin", "main")   # the human pushes it themselves
        self.assertEqual(sync.push_now(), "nothing")
        self.assertEqual(sync.status()["state"], "ok")

    # — offline —

    def test_an_unreachable_origin_is_quiet_and_catches_up(self):
        self.use(self.ada)
        git(self.ada, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        self.move(self.ada, "backlog", "to-do")

        self.assertEqual(sync.push_now(), "offline")
        for _ in range(3):
            self.assertEqual(sync.pull_now(), "offline")
        self.assertEqual(len([s for s in self.summaries() if "unreachable" in s]), 1,
                         "one quiet note, not one per beat")
        self.assertEqual(sync.status()["state"], "offline")
        self.assertEqual(self.stage_of(self.ada), "to-do",
                         "the board kept working while origin was gone")

        git(self.ada, "remote", "set-url", "origin", str(self.origin))
        self.assertEqual(sync.pull_now(), "up-to-date")

        self.assertEqual(self.origin_head(), self.head(self.ada),
                         "the queued commit went out on the next reachable beat")
        self.assertTrue(any("caught up" in s for s in self.summaries()))
        self.assertEqual(sync.status()["state"], "ok")
        self.use(self.elena)
        sync.pull_now()
        self.assertEqual(self.stage_of(self.elena), "to-do")

    # — which remote this board rides (task 37) —

    def test_a_remote_named_otherwise_syncs_exactly_the_same(self):
        """The bug this card came from: sync hardcoded `origin`, so a
        checkout whose remote is called anything else synced nothing at
        all — silently, with a healthy header."""
        self.use(self.ada)
        git(self.ada, "remote", "rename", "origin", "upstream")

        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(self.origin_head(), self.head(self.ada))
        self.assertEqual(sync.status()["state"], "ok")
        self.assertTrue(any("upstream/main" in s for s in self.summaries()),
                        "the ticker names the remote it actually rode")

        self.use(self.elena)
        self.assertEqual(sync.pull_now(), "pulled")
        self.assertEqual(self.stage_of(self.elena), "to-do")

    def test_the_configured_remote_wins_over_the_first_one_listed(self):
        """BOARD_GIT_REMOTE is what PRs already honoured; sync honours the
        same answer, so the two halves of team mode cannot disagree about
        where this board's work goes."""
        self.use(self.ada)
        git(self.ada, "remote", "rename", "origin", "fork")
        git(self.ada, "remote", "add", "backup", str(self.tmp / "elsewhere.git"))
        self.patch(GIT_REMOTE="fork")

        self.assertEqual(config.git_remotes()[0], "backup",
                         "auto-detection alone would pick the wrong one here")
        self.assertEqual(github.remote(), "fork")

        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(self.origin_head(), self.head(self.ada))

    def test_no_remote_at_all_stalls_the_board_and_names_both_fixes(self):
        self.use(self.ada)
        git(self.ada, "remote", "remove", "origin")
        self.move(self.ada, "backlog", "to-do")

        self.assertEqual(sync.push_now(), "no-remote")
        self.assertEqual(sync.pull_now(), "no-remote")

        self.assertEqual(sync.status()["state"], "stalled")
        stalled = [s for s in self.summaries() if "no remote to sync through" in s]
        self.assertEqual(len(stalled), 1, "narrated once, not once per beat")
        self.assertIn("git remote add", stalled[0])
        self.assertIn("BOARD_GIT_REMOTE", stalled[0])
        self.assertIn(stalled[0], sync.status()["detail"])

    def test_the_stall_clears_when_a_remote_appears(self):
        self.use(self.ada)
        git(self.ada, "remote", "remove", "origin")
        self.move(self.ada, "backlog", "to-do")
        self.assertEqual(sync.push_now(), "no-remote")

        git(self.ada, "remote", "add", "origin", str(self.origin))

        # never fetched from it, so this is the full converge: fetch, then push
        self.assertEqual(sync.push_now(), "up-to-date")
        self.assertEqual(sync.status()["state"], "ok")
        self.assertTrue(any("converging again" in s for s in self.summaries()),
                        "the ticker closes the loop, as the offline path does")
        self.assertEqual(self.origin_head(), self.head(self.ada))

    def test_a_named_remote_that_does_not_exist_stalls_naming_it(self):
        """A typo in BOARD_GIT_REMOTE is likelier than no remote at all, and
        quietly using origin instead would hide it."""
        self.use(self.ada)
        self.patch(GIT_REMOTE="typo")
        before = self.origin_head()
        self.move(self.ada, "backlog", "to-do")

        self.assertEqual(sync.push_now(), "no-remote")
        self.assertEqual(self.origin_head(), before, "origin was not used behind our back")

        self.assertEqual(sync.status()["state"], "stalled")
        stalled = [s for s in self.summaries() if "BOARD_GIT_REMOTE names 'typo'" in s]
        self.assertEqual(len(stalled), 1)
        self.assertIn("origin", stalled[0], "it says which remotes this checkout has")

    def test_the_missing_remote_is_on_the_header_from_startup(self):
        """Not on the second beat: the condition is true before the first
        converge, and a board that never started syncing must not render
        like one that is."""
        self.use(self.ada)
        git(self.ada, "remote", "remove", "origin")

        sync.install()

        self.assertEqual(sync.status()["state"], "stalled")
        self.assertEqual(len([s for s in self.summaries()
                              if "no remote to sync through" in s]), 1)

    def test_the_gate_off_says_nothing_about_a_missing_remote(self):
        self.patch(SYNC=False)
        self.use(self.ada)
        git(self.ada, "remote", "remove", "origin")

        sync.install()
        self.assertEqual(sync.push_now(), "off")
        self.assertEqual(sync.pull_now(), "off")

        self.assertEqual(self.summaries(), [])
        self.assertEqual(sync.status(), {"enabled": False, "state": "off", "detail": ""})

    # — never pulling into a checkout that is not ready —

    def test_uncommitted_changes_stall_the_pull_loudly(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()

        self.use(self.elena)
        (self.elena / "code.txt").write_text("half-finished\n", encoding="utf-8")
        before = self.head(self.elena)

        self.assertEqual(sync.pull_now(), "dirty")
        self.assertEqual(self.head(self.elena), before)
        self.assertEqual((self.elena / "code.txt").read_text(encoding="utf-8"),
                         "half-finished\n")
        self.assertTrue(any("uncommitted changes" in s for s in self.summaries()))
        self.assertEqual(sync.status()["state"], "stalled")

        git(self.elena, "checkout", "--", "code.txt")
        self.assertEqual(sync.pull_now(), "pulled")
        self.assertEqual(sync.status()["state"], "ok")

    def test_untracked_files_do_not_stall_anything(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()

        self.use(self.elena)
        (self.elena / "scratch.txt").write_text("mine\n", encoding="utf-8")

        self.assertEqual(sync.pull_now(), "pulled")

    def test_a_checkout_off_main_pauses_sync(self):
        self.move(self.ada, "backlog", "to-do")
        sync.push_now()

        self.use(self.elena)
        git(self.elena, "checkout", "-q", "-b", "side")
        before = self.head(self.elena)

        self.assertEqual(sync.pull_now(), "not-on-main")
        self.assertEqual(self.head(self.elena), before)
        self.assertTrue(any("not main" in s for s in self.summaries()))

    # — the gate —

    def test_the_gate_off_does_not_touch_the_network(self):
        self.patch(SYNC=False)
        self.use(self.ada)
        # A remote that never answers: anything that fetched or pushed here
        # would hang instead of returning at once.
        git(self.ada, "config", "protocol.ext.allow", "always")
        git(self.ada, "remote", "set-url", "origin", "ext::sleep 30")

        started = time.monotonic()
        self.assertEqual(sync.push_now(), "off")
        self.assertEqual(sync.pull_now(), "off")
        sync.on_commit(FILENAME)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.summaries(), [])
        self.assertEqual(sync.status(), {"enabled": False, "state": "off", "detail": ""})

    def test_the_gate_off_leaves_moves_exactly_as_they_were(self):
        self.patch(SYNC=False, COMMIT_MOVES=False)
        before = self.origin_head()

        self.move(self.ada, "backlog", "to-do")

        self.assertEqual(self.stage_of(self.ada), "to-do")
        self.assertEqual(self.head(self.ada), before, "no commit, no push")
        self.assertEqual(self.origin_head(), before)
        self.assertNotIn("**Assignee:**", self.card(self.ada))

    def test_a_registered_hook_is_inert_with_the_gate_off(self):
        sync.install()
        self.patch(SYNC=False)
        before = self.origin_head()

        self.move(self.ada, "backlog", "to-do")
        time.sleep(0.2)

        self.assertEqual(self.origin_head(), before)

    def test_the_watcher_still_says_disk_for_a_plain_hand_move(self):
        self.use(self.ada)
        before = self.sig(self.ada)
        shutil.move(str(self.ada / "tasks" / "backlog" / FILENAME),
                    str(self.ada / "tasks" / "to-do" / FILENAME))
        state.BOARD_EVENTS.clear()

        watch.narrate(before, self.sig(self.ada))

        self.assertEqual(state.BOARD_EVENTS[0]["actor"], "disk")


class TheGateImpliesCommitMoves(unittest.TestCase):
    """BOARD_SYNC=1 turns BOARD_COMMIT_MOVES on: there is nothing to publish
    until moves commit themselves."""

    def reload(self, **env) -> None:
        saved = {k: os.environ.get(k) for k in ("BOARD_SYNC", "BOARD_COMMIT_MOVES")}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(config)

        self.addCleanup(restore)
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update(env)
        importlib.reload(config)

    def test_sync_on_implies_commit_moves(self):
        self.reload(BOARD_SYNC="1")
        self.assertTrue(config.SYNC)
        self.assertTrue(config.COMMIT_MOVES)

    def test_both_are_off_by_default(self):
        self.reload()
        self.assertFalse(config.SYNC)
        self.assertFalse(config.COMMIT_MOVES)
        self.assertEqual(config.SYNC_INTERVAL, 30.0)

    def test_commit_moves_alone_stays_alone(self):
        self.reload(BOARD_COMMIT_MOVES="1")
        self.assertTrue(config.COMMIT_MOVES)
        self.assertFalse(config.SYNC)


class TheStrayCommitTest(unittest.TestCase):
    """The piggyback guard reads commit subjects — the one thing standing
    between a human's private work and origin."""

    def test_board_commits_pass(self):
        self.assertEqual(sync._stray(["abc board: 07 → to-do (ada)",
                                      "def board: 08 → done (elena)"]), "")

    def test_the_oldest_stray_is_the_one_named(self):
        self.assertEqual(
            sync._stray(["abc board: 07 → to-do (ada)", "def wip: older", "aaa wip: oldest"]),
            "aaa wip: oldest")

    def test_a_commit_merely_mentioning_the_board_is_still_stray(self):
        self.assertEqual(sync._stray(["abc fix the board: really"]),
                         "abc fix the board: really")


class TheRemoteResolver(unittest.TestCase):
    """One answer to "which remote is this board's", in config — the module
    that already owns the setting. Resolved on demand: config is imported
    everywhere and must not shell out at import."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-remote-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)],
                       check=True, capture_output=True)
        for attr in ("REPO", "GIT_REMOTE"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
        config.REPO, config.GIT_REMOTE = self.tmp, ""

    def test_a_checkout_with_no_remotes_resolves_to_nothing(self):
        self.assertEqual(config.git_remotes(), [])
        self.assertIsNone(config.git_remote())

    def test_the_first_remote_when_the_setting_is_empty(self):
        git(self.tmp, "remote", "add", "upstream", "https://example.invalid/x.git")
        self.assertEqual(config.git_remotes(), ["upstream"])
        self.assertEqual(config.git_remote(), "upstream")

    def test_the_setting_wins_and_is_taken_exactly_as_named(self):
        git(self.tmp, "remote", "add", "origin", "https://example.invalid/x.git")
        config.GIT_REMOTE = "fork"
        self.assertEqual(config.git_remote(), "fork",
                         "a configured name is never swapped for another")

    def test_somewhere_that_is_not_a_repo_answers_without_raising(self):
        config.REPO = self.tmp / "not-a-checkout"
        self.assertEqual(config.git_remotes(), [])
        self.assertIsNone(config.git_remote())


class TheSyncChip(unittest.TestCase):
    """board.html is a single file with no frontend runner — these are the
    source-level invariants of the surface this card adds."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")

    def test_a_server_toast_reaches_the_person(self):
        self.assertIn("msg.type === 'toast'", self.html)
        self.assertIn("toast(msg.message, !!msg.error)", self.html)

    def test_the_chip_hides_itself_while_sync_is_healthy(self):
        self.assertIn("if (!s || !s.enabled || s.state === 'ok') { el.hidden = true;", self.html)
        self.assertIn(".livechip[hidden]{display:none}", self.html,
                      "the chip's own display:flex would beat the UA's [hidden]")

    def test_a_refused_move_re_reads_the_board(self):
        """What makes the mid-drag race safe in the browser: the drop sends
        the stage it started from, and a rejected move reloads disk state
        rather than leaving the stale card on screen."""
        self.assertIn("const { file, from } = JSON.parse(e.dataTransfer.getData("
                      "'application/json'));", self.html)
        self.assertIn("if (!res.ok) { toast(data.error || 'move failed', true); "
                      "await loadState(); return false; }", self.html)

    def test_sync_events_have_a_glyph_and_a_filter(self):
        self.assertIn("sync: '⇅'", self.html)
        # the board-level kinds share the Moves filter; what matters here is
        # that sync is one of them, not which others have joined it since
        moves = re.search(r"\['moves', 'Moves', new Set\((\[[^\]]*\])\)\]", self.html)
        self.assertIsNotNone(moves, "board.html lost its Moves filter")
        self.assertIn("'sync'", moves.group(1))


if __name__ == "__main__":
    unittest.main()
