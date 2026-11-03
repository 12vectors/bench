"""Split-git convergence (task 56): two boards, one shared work-state
remote, and an application remote that never sees a bookkeeping commit.

This is the issue's second delivery slice — Git-managed tickets outside
the application repository. Everything is real: two clones of a real bare
work-state upstream, two clones of a real bare application upstream, and
nothing mocked but the SSE fan-out.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import config  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402
import watch  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")
FILENAME = "56-external-workspaces.md"
CARD = ("# 56 — External workspaces\n\n"
        "**Status:** Backlog\n"
        "**Priority:** High\n"
        "**Type:** Feature\n\n"
        "Body text long enough that git sees a rename rather than a delete\n"
        "and an add when the file moves between two stage directories, which\n"
        "is what turns a same-card race into a conflict it can report.\n")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class TwoExternalBoards(unittest.TestCase):
    """Two operators, 'ada' and 'elena'. Each has an application checkout
    of one upstream and a work-state checkout of another; the board state
    converges through the second and never touches the first."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-split-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.app_origin = self.tmp / "app-origin.git"
        self.state_origin = self.tmp / "state-origin.git"
        for bare in (self.app_origin, self.state_origin):
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                            str(bare)], check=True, capture_output=True)

        seed = self.tmp / "seed-app"
        self._clone(self.app_origin, seed, "seed")
        (seed / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "the application")
        git(seed, "push", "-q", "origin", "main")

        seed_state = self.tmp / "seed-state"
        self._clone(self.state_origin, seed_state, "seed")
        for slug in (*STAGES, "archive"):
            (seed_state / "tasks" / slug).mkdir(parents=True)
        (seed_state / "tasks" / "backlog" / FILENAME).write_text(
            CARD, encoding="utf-8")
        git(seed_state, "add", "-A")
        git(seed_state, "commit", "-q", "-m", "the board")
        git(seed_state, "push", "-q", "origin", "main")

        self.apps, self.works = {}, {}
        for who in ("ada", "elena"):
            self.apps[who] = self._clone(self.app_origin,
                                         self.tmp / f"{who}-app", who)
            self.works[who] = self._clone(self.state_origin,
                                          self.tmp / f"{who}-work", who)

        self.patch(SYNC=True, COMMIT_MOVES=True, FETCH_TIMEOUT=10.0,
                   APP_BASE="main", STATE_BASE="main",
                   GIT_REMOTE="", STATE_GIT_REMOTE="",
                   SESSIONS_DIR=self.tmp / "sessions",
                   APP_SETTING=str(self.apps["ada"]),
                   WORK_SETTING=str(self.works["ada"]), EXTERNAL=True,
                   REPO=self.apps["ada"], WORK=self.works["ada"],
                   TASKS=self.works["ada"] / "tasks")
        self.use("ada")

        for registry in (state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         sync.ARRIVED, sync._NOTES):
            registry.clear()
            self.addCleanup(registry.clear)
        state.COMMIT_HOOKS.clear()
        self.addCleanup(state.COMMIT_HOOKS.clear)
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

        self.broadcasts: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.broadcasts.append

    def _clone(self, origin: Path, path: Path, who: str) -> Path:
        subprocess.run(["git", "clone", "-q", str(origin), str(path)],
                       check=True, capture_output=True)
        git(path, "config", "user.name", who)
        git(path, "config", "user.email", f"{who}@example.test")
        return path

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — acting as one board or the other —

    def use(self, who: str) -> None:
        config.REPO = self.apps[who]
        config.WORK = self.works[who]
        config.TASKS = self.works[who] / "tasks"
        config.APP_SETTING = str(self.apps[who])
        config.WORK_SETTING = str(self.works[who])

    def move(self, who: str, source: str, target: str) -> None:
        self.use(who)
        taskfiles.move_task(FILENAME, source, target)

    def stage_of(self, who: str) -> str | None:
        for slug in STAGES:
            if (self.works[who] / "tasks" / slug / FILENAME).is_file():
                return slug
        return None

    def card(self, who: str) -> str:
        stage = self.stage_of(who)
        return (self.works[who] / "tasks" / stage / FILENAME).read_text(
            encoding="utf-8")

    def head(self, path: Path) -> str:
        return git(path, "rev-parse", "HEAD").stdout.strip()

    def origin_head(self, bare: Path) -> str:
        return git(bare, "rev-parse", "main").stdout.strip()

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def toasts(self) -> list[str]:
        return [b["message"] for b in self.broadcasts
                if b.get("type") == "toast"]

    def sig(self, who: str) -> dict[str, set[str]]:
        self.use(who)
        root = self.works[who] / "tasks"
        return {slug: {p.name for p in (root / slug).glob("*.md")}
                for slug in STAGES if (root / slug).is_dir()}

    # — the two identities —

    def test_the_state_repository_is_the_work_checkout(self):
        self.assertEqual(config.state_repo(), self.works["ada"])
        self.assertNotEqual(config.state_repo(), self.apps["ada"])

    def test_a_move_converges_through_the_state_remote(self):
        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")

        self.use("elena")
        self.assertEqual(sync.pull_now(), "pulled")

        self.assertEqual(self.stage_of("elena"), "to-do")
        self.assertEqual(self.card("elena"), self.card("ada"),
                         "both boards hold the same bytes")
        self.assertIn("**Assignee:** ada", self.card("elena"))

    def test_the_applications_main_never_gains_a_bookkeeping_commit(self):
        before = self.origin_head(self.app_origin)
        for source, target in (("backlog", "to-do"), ("to-do", "in-progress"),
                               ("in-progress", "review")):
            self.move("ada", source, target)
            sync.push_now()

        self.assertEqual(self.origin_head(self.app_origin), before,
                         "the application's upstream is untouched")
        self.assertEqual(self.head(self.apps["ada"]), before)
        subjects = git(self.apps["ada"], "log", "--format=%s",
                       "main").stdout.splitlines()
        self.assertEqual([s for s in subjects if s.startswith("board: ")], [])
        self.assertEqual(git(self.apps["ada"], "status",
                             "--porcelain").stdout.strip(), "")

    def test_the_state_remote_carries_only_board_commits(self):
        self.move("ada", "backlog", "to-do")
        sync.push_now()
        subjects = git(self.state_origin, "log", "--format=%s",
                       "main").stdout.splitlines()
        self.assertIn("board: 56 → to-do (ada)", subjects)

    def test_an_archive_reaches_the_other_board(self):
        self.use("ada")
        taskfiles.archive_task(FILENAME, "backlog")
        self.assertEqual(sync.push_now(), "ok")

        self.use("elena")
        self.assertEqual(sync.pull_now(), "pulled")
        self.assertIsNone(self.stage_of("elena"))
        self.assertTrue((self.works["elena"] / "tasks" / "archive"
                         / FILENAME).is_file())

    def test_the_pull_attributes_the_move_to_its_author(self):
        self.move("ada", "backlog", "to-do")
        sync.push_now()

        self.use("elena")
        before = self.sig("elena")
        sync.pull_now()
        after = self.sig("elena")

        state.EXPECTED_MOVES.clear()
        state.BOARD_EVENTS.clear()
        watch.narrate(before, after)
        moves = [e for e in state.BOARD_EVENTS if e["kind"] == "move"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["actor"], "ada")

    def test_a_card_arriving_in_review_on_a_replica_opens_no_pr(self):
        """State syncs; reactions don't. The rule is unchanged by the
        split, and it matters more: the replica's application checkout is
        a different checkout of the code."""
        opened: list[str] = []
        self.addCleanup(setattr, github, "open_pr_async", github.open_pr_async)
        github.open_pr_async = opened.append

        self.move("ada", "backlog", "to-do")
        self.move("ada", "to-do", "in-progress")
        self.move("ada", "in-progress", "review")
        sync.push_now()
        self.assertEqual(opened, [], "watch.py is what fires it, not the move")

        self.use("elena")
        before = self.sig("elena")
        sync.pull_now()
        state.EXPECTED_MOVES.clear()
        watch.narrate(before, self.sig("elena"))

        self.assertEqual(opened, [],
                         "the replica rendered the arrival and acted on nothing")

    def test_the_actor_board_does_fire_its_own_reaction(self):
        """The other half: the gate must not have turned the reaction off
        for everybody."""
        opened: list[str] = []
        self.addCleanup(setattr, github, "open_pr_async", github.open_pr_async)
        github.open_pr_async = opened.append

        self.use("ada")
        before = self.sig("ada")
        self.move("ada", "backlog", "to-do")
        self.move("ada", "to-do", "in-progress")
        self.move("ada", "in-progress", "review")
        watch.narrate(before, self.sig("ada"))

        self.assertEqual(opened, [FILENAME])

    def test_losing_a_race_undoes_the_move_in_the_state_repository(self):
        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.move("elena", "backlog", "in-progress")
        self.assertEqual(sync.push_now(), "pulled")

        self.assertEqual(self.stage_of("elena"), "to-do")
        self.assertIn("56 claimed by ada — your move was undone", self.toasts())

    def test_a_human_commit_in_the_work_repository_stops_the_push(self):
        self.use("ada")
        (self.works["ada"] / "notes.md").write_text("mine\n", encoding="utf-8")
        git(self.works["ada"], "add", "-A")
        git(self.works["ada"], "commit", "-qm", "notes: not ready for anyone")
        before = self.origin_head(self.state_origin)

        self.move("ada", "backlog", "to-do")

        self.assertEqual(sync.push_now(), "stray")
        self.assertEqual(self.origin_head(self.state_origin), before)
        self.assertTrue(any("not a board commit" in s for s in self.summaries()))

    def test_an_unrelated_application_commit_does_not_stall_sync(self):
        """The whole point of two identities: work in the application is
        nobody's business to the board's own history."""
        self.use("ada")
        (self.apps["ada"] / "code.txt").write_text("my experiment\n",
                                                   encoding="utf-8")
        git(self.apps["ada"], "commit", "-qam", "wip: app experiment")

        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(sync.status()["state"], "ok")

    def test_the_commit_hook_publishes_to_the_state_remote(self):
        sync.install()
        self.assertIn(sync.on_commit, state.COMMIT_HOOKS)
        before = self.origin_head(self.state_origin)

        self.move("ada", "backlog", "to-do")
        for _ in range(100):
            if self.origin_head(self.state_origin) != before:
                break
            time.sleep(0.05)

        self.assertEqual(self.origin_head(self.state_origin),
                         self.head(self.works["ada"]))

    # — each remote and base is its own —

    def test_the_state_remote_setting_governs_sync(self):
        self.use("ada")
        git(self.works["ada"], "remote", "rename", "origin", "team")
        git(self.works["ada"], "remote", "add", "backup",
            str(self.tmp / "elsewhere.git"))
        self.patch(STATE_GIT_REMOTE="team")

        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(self.origin_head(self.state_origin),
                         self.head(self.works["ada"]))

    def test_the_application_remote_setting_does_not_reach_sync(self):
        """A BOARD_GIT_REMOTE naming a remote only the application has
        must not be what sync looks for in the work repository."""
        self.use("ada")
        git(self.apps["ada"], "remote", "rename", "origin", "appfork")
        self.patch(GIT_REMOTE="appfork")

        self.assertEqual(github.remote(), "appfork")
        self.assertEqual(config.state_git_remote(), "origin")
        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")

    def test_a_state_base_branch_of_its_own(self):
        self.use("ada")
        for who in ("ada", "elena"):
            git(self.works[who], "branch", "-m", "main", "board")
        subprocess.run(["git", "-C", str(self.state_origin), "branch",
                        "-m", "main", "board"], capture_output=True)
        for who in ("ada", "elena"):
            git(self.works[who], "fetch", "-q", "origin")
            git(self.works[who], "branch", "--set-upstream-to=origin/board",
                "board")
        self.patch(STATE_BASE="board")

        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(
            git(self.state_origin, "rev-parse", "board").stdout.strip(),
            self.head(self.works["ada"]))

        self.use("elena")
        self.assertEqual(sync.pull_now(), "pulled")
        self.assertEqual(self.stage_of("elena"), "to-do")

    def test_the_tasks_prefix_is_read_against_the_state_repository(self):
        self.use("ada")
        self.assertEqual(sync._tasks_prefix(), "tasks/")
        self.assertTrue(sync._is_task_file(f"tasks/to-do/{FILENAME}"))
        self.assertFalse(sync._is_task_file("code.txt"))

    # — the application's condition is none of sync's business —

    def test_a_dirty_application_does_not_stall_the_board(self):
        """Sync paused on a dirty checkout because the checkout *was* the
        board's. Half-finished code in the application has nothing to do
        with whether a card can move."""
        self.use("ada")
        (self.apps["ada"] / "code.txt").write_text("half done\n",
                                                   encoding="utf-8")

        self.move("ada", "backlog", "to-do")
        self.assertEqual(sync.push_now(), "ok")

        self.assertEqual(sync.status()["state"], "ok")
        self.assertEqual(
            (self.apps["ada"] / "code.txt").read_text(encoding="utf-8"),
            "half done\n", "and the work is untouched")

    def test_an_application_on_another_branch_does_not_stall_the_board(self):
        self.use("ada")
        git(self.apps["ada"], "checkout", "-q", "-b", "feature/x")

        self.move("ada", "backlog", "to-do")

        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(sync.status()["state"], "ok")

    def test_a_diverged_application_does_not_stall_the_board(self):
        self.use("ada")
        git(self.apps["ada"], "commit", "-q", "--allow-empty",
            "-m", "a commit origin has never seen")

        self.move("ada", "backlog", "to-do")

        self.assertEqual(sync.push_now(), "ok")
        self.assertEqual(sync.status()["state"], "ok")
        self.assertEqual(self.origin_head(self.app_origin),
                         git(self.app_origin, "rev-parse",
                             "main").stdout.strip(),
                         "and nothing of the application was published")

    def test_a_dirty_work_checkout_still_stalls_the_board(self):
        """The guard is kept where it belongs: uncommitted changes in the
        *work* repository are what a fast-forward would run over."""
        self.move("ada", "backlog", "to-do")
        sync.push_now()

        self.use("elena")
        (self.works["elena"] / "notes.md").write_text("half\n", encoding="utf-8")
        git(self.works["elena"], "add", "-A")
        git(self.works["elena"], "commit", "-qm", "board: notes")
        git(self.works["elena"], "reset", "-q", "--soft", "HEAD~1")

        self.assertEqual(sync.pull_now(), "dirty")
        self.assertTrue(any("uncommitted changes" in s
                            for s in self.summaries()))

    def test_a_work_repository_with_no_remote_stalls_rather_than_guessing(self):
        self.use("ada")
        git(self.works["ada"], "remote", "remove", "origin")

        self.assertEqual(sync.push_now(), "no-remote")
        stalled = [s for s in self.summaries() if "no remote" in s]
        self.assertEqual(len(stalled), 1, self.summaries())
        self.assertEqual(sync.status()["state"], "stalled")
        self.assertEqual(self.origin_head(self.app_origin),
                         self.head(self.apps["ada"]),
                         "it did not reach for the application's remote")


if __name__ == "__main__":
    unittest.main()
