"""Replica etiquette (task 20): the actor's board acts, everyone else renders.

State syncs; reactions don't. These cases run against real clones of a real
bare upstream, with a stub `gh` standing in for GitHub — the point of the
card is who does what to whom, so nothing is mocked except the network's
far end and the SSE fan-out.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402
import watch  # noqa: E402

FILENAME = "20-a-shared-card.md"
STEM = FILENAME[:-3]
BRANCH = f"task/{STEM}"
PR_URL = "https://github.com/acme/widget/pull/7"

# gh, as far as these tests are concerned: it logs every invocation and
# answers from GH_MODE. Real subprocess, real argv — only GitHub is fake.
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
mode = os.environ.get("GH_MODE", "ok")

if args[:2] == ["pr", "create"]:
    if mode == "exists":
        sys.stderr.write('a pull request for branch "{BRANCH}" into branch '
                         '"main" already exists: {PR_URL}\\n')
        sys.exit(1)
    if mode == "create-fails":
        sys.stderr.write("something else went wrong\\n")
        sys.exit(1)
    print("{PR_URL}")
elif args[:2] == ["pr", "view"] and "--jq" in args:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"reviews": [], "reviewRequests": [],
                      "statusCheckRollup": [], "state": "OPEN",
                      "mergeable": "MERGEABLE"}}))
elif args[:2] == ["pr", "merge"]:
    if mode == "unmergeable":
        sys.stderr.write("Pull request is not mergeable: the base branch "
                         "policy prohibits the merge.\\n")
        sys.exit(1)
    print("Merged pull request #7")
else:
    sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def card(status: str, assignee: str | None = None, pr: str | None = None) -> str:
    header = f"**Status:** {status}\n**Priority:** High\n"
    if assignee:
        header += f"**Assignee:** {assignee}\n"
    if pr:
        header += f"**PR:** {pr}\n"
    return ("# 20 — A card two boards can see\n\n" + header +
            "\nBody text long enough that git sees a rename rather than a\n"
            "delete and an add when the file moves between stage directories.\n")


class Boards(unittest.TestCase):
    """One bare upstream, two clones — 'ada' and 'elena'. config.REPO/TASKS
    point at whichever board is acting."""

    SYNC = True

    def setUp(self):
        # resolve(): macOS tempdirs sit behind the /var → /private/var
        # symlink and git reports the resolved path.
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-actor-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)

        self.ada = self._clone("ada")
        for slug in config.STAGE_DIRS:
            (self.ada / "tasks" / slug).mkdir(parents=True)
        (self.ada / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.ada, "add", "-A")
        git(self.ada, "commit", "-q", "-m", "root")
        git(self.ada, "push", "-q", "origin", "main")
        self.elena = self._clone("elena")

        self.gh_log = self.tmp / "gh.log"
        gh = self.tmp / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)
        self.addCleanup(os.environ.pop, "GH_MODE", None)

        self.patch(SYNC=self.SYNC, COMMIT_MOVES=True, FETCH_TIMEOUT=10.0,
                   SESSIONS_DIR=self.tmp / "sessions", GH_BIN=str(gh),
                   WORKTREES=self.tmp / "worktrees",
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
        github.PR_STATE.pop(FILENAME, None)
        self.addCleanup(github.PR_STATE.pop, FILENAME, None)

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

    def mode(self, value: str) -> None:
        os.environ["GH_MODE"] = value

    # — acting as one board or the other —

    def use(self, board: Path) -> None:
        config.REPO, config.TASKS = board, board / "tasks"

    def place(self, board: Path, stage: str, text: str, *, commit: bool = True) -> None:
        (board / "tasks" / stage / FILENAME).write_text(text, encoding="utf-8")
        if commit:
            git(board, "add", "-A")
            git(board, "commit", "-q", "-m", f"board: 20 → {stage} (setup)")
            git(board, "push", "-q", "origin", "main")

    def work_branch(self, board: Path) -> None:
        """A worktree with a commit on it: what an agent leaves behind."""
        worktree = config.WORKTREES / STEM
        git(board, "worktree", "add", "-q", "-b", BRANCH, str(worktree))
        (worktree / "feature.txt").write_text("the work\n", encoding="utf-8")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", "the work")

    def stage_of(self, board: Path) -> str | None:
        for slug in config.STAGE_DIRS:
            if (board / "tasks" / slug / FILENAME).is_file():
                return slug
        return None

    def text(self, board: Path) -> str:
        return (board / "tasks" / self.stage_of(board) / FILENAME).read_text(
            encoding="utf-8")

    def sig(self, board: Path) -> dict[str, set[str]]:
        return {slug: {p.name for p in (board / "tasks" / slug).glob("*.md")}
                for slug in config.STAGE_DIRS
                if (board / "tasks" / slug).is_dir()}

    def elsewhere(self) -> None:
        """Switch processes, not just directories. Two boards are two
        programs: the expectations one holds in memory (I am about to move
        this file) the other never saw, and only the commit reaches it.
        The registries are module globals here, so say so explicitly."""
        state.EXPECTED_MOVES.clear()
        sync.ARRIVED.clear()

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.is_file():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def gh_verbs(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.gh_calls()]

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]


class RemoteMovesAreInert(Boards):
    """A move that arrived over origin renders and narrates. Nothing else."""

    def setUp(self):
        super().setUp()
        self.place(self.ada, "in-progress", card("In Progress", "ada"))
        self.use(self.elena)
        sync.pull_now()
        self.elsewhere()              # the fixture's own pull is not evidence
        self.use(self.ada)
        self.opened: list[str] = []
        self.addCleanup(setattr, github, "open_pr_async", github.open_pr_async)
        github.open_pr_async = self.opened.append

    def narrate(self, board: Path, action, *, fresh: bool = False) -> list[dict]:
        self.use(board)
        if fresh:
            self.elsewhere()
        before = self.sig(board)
        action()
        after = self.sig(board)
        state.BOARD_EVENTS.clear()
        watch.narrate(before, after)
        return [e for e in state.BOARD_EVENTS if e["kind"] == "move"]

    def test_the_actors_board_opens_the_pr(self):
        moves = self.narrate(self.ada, lambda: taskfiles.move_task(
            FILENAME, "in-progress", "review"))

        self.assertEqual(self.opened, [FILENAME])
        self.assertEqual(moves[0]["actor"], "you")
        self.assertFalse(moves[0]["remote"])

    def test_the_same_move_arriving_at_a_replica_triggers_nothing(self):
        self.use(self.ada)
        taskfiles.move_task(FILENAME, "in-progress", "review")
        self.assertEqual(sync.push_now(), "ok")
        self.opened.clear()

        moves = self.narrate(self.elena, sync.pull_now, fresh=True)

        self.assertEqual(self.stage_of(self.elena), "review",
                         "the replica renders the move")
        self.assertEqual(moves[0]["actor"], "ada")
        self.assertTrue(moves[0]["remote"])
        self.assertEqual(self.opened, [],
                         "the side effect belongs to the board that acted")

    def test_a_plain_hand_move_on_this_disk_still_acts(self):
        """Inert means "arrived from elsewhere", not "unattributed": a mv in
        this checkout is still this board's user doing something."""
        moves = self.narrate(self.ada, lambda: shutil.move(
            str(self.ada / "tasks" / "in-progress" / FILENAME),
            str(self.ada / "tasks" / "review" / FILENAME)))

        self.assertEqual(moves[0]["actor"], "disk")
        self.assertFalse(moves[0]["remote"])
        self.assertEqual(self.opened, [FILENAME])

    def test_an_undone_move_coming_back_is_inert_too(self):
        """The loser of a race has its file reverted by the rebase — which
        the watcher sees as a move. It arrived; it acts on nothing."""
        self.use(self.ada)
        taskfiles.move_task(FILENAME, "in-progress", "review")
        sync.push_now()
        self.use(self.elena)
        self.elsewhere()
        before = self.sig(self.elena)
        taskfiles.move_task(FILENAME, "in-progress", "backlog")
        self.assertEqual(sync.push_now(), "pulled")
        self.opened.clear()

        state.BOARD_EVENTS.clear()
        watch.narrate(before, self.sig(self.elena))

        self.assertEqual(self.stage_of(self.elena), "review")
        moves = [e for e in state.BOARD_EVENTS if e["kind"] == "move"]
        self.assertEqual((moves[0]["actor"], moves[0]["remote"]), ("ada", True))
        self.assertEqual(self.opened, [])


class ThePRLineTravels(Boards):
    """The `**PR:**` line is the backstop behind the actor-only trigger, so
    it has to reach the other boards — which means committing it."""

    def setUp(self):
        super().setUp()
        self.place(self.ada, "review", card("Review", "ada"))
        self.work_branch(self.ada)
        self.use(self.elena)
        sync.pull_now()
        self.use(self.ada)

    def test_opening_a_pr_writes_and_commits_the_url(self):
        github.maybe_open_pr(FILENAME)

        self.assertIn(f"**PR:** {PR_URL}", self.text(self.ada))
        self.assertEqual(git(self.ada, "status", "--porcelain",
                             "--untracked-files=no").stdout, "",
                         "an uncommitted task file would stall sync outright")
        subject = git(self.ada, "log", "-1", "--pretty=%s").stdout.strip()
        self.assertEqual(subject, "board: 20 PR opened (ada)")
        self.assertTrue(subject.startswith(sync.BOARD_COMMIT),
                        "sync's piggyback guard only publishes board commits")

    def test_the_url_reaches_the_other_board(self):
        github.maybe_open_pr(FILENAME)
        self.assertEqual(sync.push_now(), "ok")

        self.use(self.elena)
        self.assertEqual(sync.pull_now(), "pulled")

        self.assertIn(f"**PR:** {PR_URL}", self.text(self.elena))
        self.assertEqual(
            taskfiles.read_task(self.elena / "tasks" / "review" / FILENAME,
                                "review")["pr"], PR_URL,
            "the replica's poller adopts the PR from the file, read-only")

    def test_a_second_attempt_is_a_no_op_not_a_second_pr(self):
        github.maybe_open_pr(FILENAME)
        state.BOARD_EVENTS.clear()

        github.maybe_open_pr(FILENAME)

        self.assertEqual(self.gh_verbs().count("pr create"), 1)
        self.assertEqual([s for s in self.summaries() if "failed" in s], [])

    def test_a_double_that_crosses_on_github_adopts_the_open_pr(self):
        """The rare double-fire: the file gate lost the race but GitHub
        holds the line — one PR still exists, and the card learns its url."""
        self.mode("exists")

        github.maybe_open_pr(FILENAME)

        self.assertIn(f"**PR:** {PR_URL}", self.text(self.ada))
        self.assertTrue(any("adopted it" in s for s in self.summaries()))
        self.assertEqual([s for s in self.summaries() if "failed" in s], [])

    def test_a_real_failure_is_still_a_failure(self):
        self.mode("create-fails")

        github.maybe_open_pr(FILENAME)

        self.assertNotIn("**PR:**", self.text(self.ada))
        self.assertTrue(any("PR creation failed" in s for s in self.summaries()))

    def test_with_the_gate_off_the_line_is_left_for_a_human(self):
        self.patch(SYNC=False, COMMIT_MOVES=False)
        head = git(self.ada, "rev-parse", "HEAD").stdout

        github.maybe_open_pr(FILENAME)

        self.assertIn(f"**PR:** {PR_URL}", self.text(self.ada))
        self.assertEqual(git(self.ada, "rev-parse", "HEAD").stdout, head,
                         "single-player commits tasks/ by hand, as it always did")


class TheExplicitOpenPR(Boards):
    """Nobody finishes the actor's half-done side effect automatically —
    a person asks for it, and hears why when it cannot happen."""

    def setUp(self):
        super().setUp()
        self.place(self.ada, "review", card("Review", "ada"))
        self.use(self.ada)

    def test_it_opens_the_pr_and_returns_the_url(self):
        self.work_branch(self.ada)

        self.assertEqual(github.open_pr_now(FILENAME), PR_URL)
        self.assertIn(f"**PR:** {PR_URL}", self.text(self.ada))

    def test_it_says_why_when_there_is_nothing_to_open(self):
        with self.assertRaises(ValueError) as caught:
            github.open_pr_now(FILENAME)
        self.assertIn("no task/", str(caught.exception))

    def test_it_says_so_when_the_card_already_has_one(self):
        self.work_branch(self.ada)
        github.open_pr_now(FILENAME)

        with self.assertRaises(ValueError) as caught:
            github.open_pr_now(FILENAME)
        self.assertIn("already has a PR", str(caught.exception))

    def test_startup_reconcile_stands_down_in_team_mode(self):
        """Every replica would race to open the same PR at startup."""
        self.work_branch(self.ada)

        github.reconcile()

        self.assertEqual(self.gh_verbs(), [])
        self.assertNotIn("**PR:**", self.text(self.ada))


class ClaimsGateLaunches(Boards):
    """Ownership means something: work does not start on someone else's
    card by accident."""

    def setUp(self):
        super().setUp()
        self.place(self.ada, "in-progress", card("In Progress", "ada"))
        self.use(self.elena)
        sync.pull_now()
        self.use(self.elena)          # elena's board is the one clicking

    def test_someone_elses_card_refuses_and_names_them(self):
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(FILENAME, "in-progress")

        self.assertIn("ada holds", str(caught.exception))
        self.assertFalse((config.WORKTREES / STEM).exists(),
                         "the refusal comes before any worktree is made")
        self.assertEqual(git(self.elena, "rev-parse", "--verify", "--quiet",
                             BRANCH).returncode, 1)

    def test_the_deliberate_takeover_reassigns_the_card(self):
        agents.claim_for_launch(FILENAME, "in-progress", True)

        self.assertIn("**Assignee:** elena", self.text(self.elena))
        self.assertEqual(self.text(self.elena).count("**Assignee:**"), 1)
        self.assertEqual(git(self.elena, "log", "-1", "--pretty=%s").stdout.strip(),
                         "board: 20 claimed by elena (elena)")
        self.assertTrue(any("took" in s and "over from ada" in s
                            for s in self.summaries()))

    def test_the_takeover_reaches_the_other_board(self):
        agents.claim_for_launch(FILENAME, "in-progress", True)
        self.assertEqual(sync.push_now(), "ok")

        self.use(self.ada)
        self.assertEqual(sync.pull_now(), "pulled")
        self.assertIn("**Assignee:** elena", self.text(self.ada))

    def test_an_unclaimed_card_claims_on_launch(self):
        self.place(self.elena, "in-progress", card("In Progress"), commit=False)

        agents.claim_for_launch(FILENAME, "in-progress", False)

        self.assertIn("**Assignee:** elena", self.text(self.elena))
        self.assertTrue(any("claimed" in s for s in self.summaries()))

    def test_your_own_card_launches_untouched(self):
        self.place(self.elena, "in-progress", card("In Progress", "elena"),
                   commit=False)
        head = git(self.elena, "rev-parse", "HEAD").stdout

        agents.claim_for_launch(FILENAME, "in-progress", False)

        self.assertEqual(git(self.elena, "rev-parse", "HEAD").stdout, head,
                         "nothing to record: it was already yours")

    def test_the_gate_off_refuses_nobody(self):
        """Single-player never writes an assignee, so it never reads one as
        a lock — a hand-written line stays decoration."""
        self.patch(SYNC=False, COMMIT_MOVES=False)

        agents.claim_for_launch(FILENAME, "in-progress", False)

        self.assertIn("**Assignee:** ada", self.text(self.elena))


class MergesGoThroughOrigin(Boards):
    """With replicas, local main advances only by fast-forward — so the
    merge commit is made on origin, not here."""

    def setUp(self):
        super().setUp()
        self.place(self.ada, "review", card("Review", "ada", PR_URL))
        self.work_branch(self.ada)
        self.use(self.ada)
        git(self.ada, "push", "-q", "-u", "origin", BRANCH)

    def merges_on_main(self, board: Path) -> int:
        out = git(board, "rev-list", "--count", "--merges", "main").stdout.strip()
        return int(out) if out.isdigit() else 0

    def test_the_merge_is_made_on_origin(self):
        head = git(self.ada, "rev-parse", "main").stdout.strip()

        result = github.complete_task(FILENAME, "review")

        self.assertTrue(result["merged"])
        self.assertIn("pr merge", self.gh_verbs())
        self.assertEqual(self.merges_on_main(self.ada), 0,
                         "the board never makes a merge commit of its own")
        self.assertEqual(
            git(self.ada, "rev-list", "--count", f"{head}..main").stdout.strip(),
            "1", "only the card's own move commit — main is otherwise untouched")
        self.assertEqual(self.stage_of(self.ada), "done")
        self.assertTrue(any("merged" in s and "on origin" in s
                            for s in self.summaries()))

    def test_the_worktree_and_local_branch_go(self):
        github.complete_task(FILENAME, "review")

        self.assertFalse((config.WORKTREES / STEM).exists())
        self.assertEqual(git(self.ada, "rev-parse", "--verify", "--quiet",
                             BRANCH).returncode, 1,
                         "the branch is deleted even though local main never "
                         "merged it — origin did")

    def test_a_pr_origin_will_not_merge_leaves_the_card_alone(self):
        self.mode("unmergeable")

        with self.assertRaises(ValueError) as caught:
            github.complete_task(FILENAME, "review")

        self.assertIn("origin would not merge", str(caught.exception))
        self.assertEqual(self.stage_of(self.ada), "review")
        self.assertEqual(git(self.ada, "rev-parse", "--verify", "--quiet",
                             BRANCH).returncode, 0)

    def test_no_pr_is_a_refusal_that_names_the_way_out(self):
        self.place(self.ada, "review", card("Review", "ada"), commit=False)

        with self.assertRaises(ValueError) as caught:
            github.complete_task(FILENAME, "review")

        self.assertIn("open PR", str(caught.exception))
        self.assertEqual(self.stage_of(self.ada), "review")


class TheLocalMergeIsUntouched(Boards):
    """With sync off, merge & clean up is the path it always was."""

    SYNC = False

    def setUp(self):
        super().setUp()
        self.patch(COMMIT_MOVES=False)
        self.place(self.ada, "review", card("Review", None, PR_URL))
        self.work_branch(self.ada)
        self.use(self.ada)

    def test_it_merges_locally_and_pushes_main(self):
        github.complete_task(FILENAME, "review")

        self.assertIn("the work", git(self.ada, "log", "--format=%s", "main").stdout)
        self.assertEqual(self.gh_verbs(), [], "gh is not part of this path")
        self.assertEqual(git(self.origin, "rev-parse", "main").stdout,
                         git(self.ada, "rev-parse", "main").stdout)
        self.assertEqual(self.stage_of(self.ada), "done")
        self.assertTrue(any("merged" in s and "into main" in s
                            for s in self.summaries()))

    def test_startup_reconcile_still_catches_up(self):
        """The single-player behaviour the team-mode stand-down replaces."""
        self.place(self.ada, "review", card("Review"), commit=False)

        github.reconcile()

        self.assertIn(f"**PR:** {PR_URL}", self.text(self.ada))


class TheCardFace(unittest.TestCase):
    """board.html is a single file with no frontend runner — these are the
    source-level invariants of the surface this card adds."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")
        cls.httpd = (REPO / "manager" / "core" / "httpd.py").read_text(encoding="utf-8")

    def test_the_board_knows_who_it_is(self):
        self.assertIn('"me": taskfiles.actor_name() if config.COMMIT_MOVES else ""',
                      self.httpd)

    def test_someone_elses_card_offers_takeover_not_start_work(self):
        self.assertIn("const held = task.assignee && S.state.me && task.assignee !== S.state.me",
                      self.html)
        self.assertIn("label: 'take over', confirm: `take from ${held}?`", self.html)
        self.assertIn("fireAgent(task, '/api/agent/start', { takeover: true })", self.html)

    def test_the_takeover_is_armed_like_every_costly_action(self):
        """confirm: … is what makes wireAction demand a second click."""
        index = self.html.index("label: 'take over'")
        self.assertIn("confirm:", self.html[index:index + 120])

    def test_the_server_refuses_a_takeover_it_was_not_asked_for(self):
        self.assertIn("bool(payload.get(\"takeover\"))", self.httpd)

    def test_a_review_card_without_a_pr_can_ask_for_one(self):
        self.assertIn("label: 'open PR', confirm: 'open it?', busy: 'opening…'", self.html)
        self.assertIn("run: () => openPR(task)", self.html)
        self.assertIn('/api/pr/open', self.html)
        self.assertIn('elif path == "/api/pr/open":', self.httpd)

    def test_the_open_pr_action_needs_a_branch_and_no_pr(self):
        index = self.html.index("label: 'open PR'")
        guard = self.html[self.html.index("task.stage === 'review' && !task.pr"):index]
        self.assertIn("S.state.branches", guard)

    def test_the_completion_sheet_tells_the_truth_in_team_mode(self):
        self.assertIn("(S.state.sync || {}).enabled", self.html)
        self.assertIn("the PR on GitHub", self.html)
        self.assertIn("local main fast-forwards on the next sync beat", self.html)


if __name__ == "__main__":
    unittest.main()
