"""A phase runs itself, on a branch of its own (task 49).

The runner is a beat, not an agent: `phases.advance_all()` is one pass, and
these cases drive it by hand rather than waiting on a thread. Everything
under it is real — a real git repo, real worktrees, real headless launches
through a real (stub) adapter — because what this card is about is the
arithmetic of branches, and mocking git would be mocking the subject.

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
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import phases  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

PHASE = "40-ship-the-site.md"
PHASE_BRANCH = "phase/40-ship-the-site"
ONE = "31-stand-up-site.md"
TWO = "32-serve-it.md"
PR_URL = "https://github.com/acme/widget/pull/7"

# An agent that does the job: one file, one commit, a clean exit.
WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
name = os.environ["BOARD_TASK"].split("-")[0]
open(os.path.join(cwd, name + ".txt"), "w").write("card " + name + "\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "card " + name], check=True)
print("WORK REPORT: card", name, "built")
"""

# The same, but it rewrites a file main also owns — the collision a phase
# branch has to refuse rather than guess at.
COLLIDES = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "code.txt"), "w").write("the member's line\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "collide"], check=True)
print("WORK REPORT: done")
"""

DECLINES = """#!/usr/bin/env python3
print("NOT READY: the card does not say which store to write to")
"""

DIES = """#!/usr/bin/env python3
import sys
print("API Error: 500 overloaded")
sys.exit(1)
"""

COMMITS_NOTHING = """#!/usr/bin/env python3
print("WORK REPORT: I read a lot and wrote nothing")
"""

FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "create"]:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"] and "--jq" in args:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"reviews": [], "reviewRequests": [],
                      "statusCheckRollup": [], "state": "OPEN",
                      "mergeable": "MERGEABLE"}}))
else:
    sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def card(title: str, *, status: str = "Backlog", kind: str | None = None,
         depends: str | None = None, cards: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if depends:
        text += f"**Depends on:** {depends}\n"
    text += "\nWhat this card is for, at enough length to be a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


class PhaseCase(unittest.TestCase):
    """One repo, one phase card, two members — and an adapter whose
    behaviour each test writes."""

    REMOTE = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-phase-run-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"

        if self.REMOTE:
            self.origin = self.tmp / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                            str(self.origin)], check=True, capture_output=True)
            subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)],
                           check=True, capture_output=True)
        else:
            self.repo.mkdir()
            git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("shipped\n", encoding="utf-8")

        self.tasks = self.repo / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        if self.REMOTE:
            git(self.repo, "push", "-q", "origin", "main")

        local = self.tmp / "local"
        (local / "adapters" / config.ADAPTER).mkdir(parents=True)
        self.adapter = local / "adapters" / config.ADAPTER / "run"
        self.adapter_is(WORKS)

        gh = self.tmp / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.gh_log = self.tmp / "gh.log"
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.repo,
                   LOCAL=local, WORKTREES=self.tmp / "worktrees",
                   AGENT_DIR=self.tmp / "agent", SESSIONS_DIR=self.tmp / "sessions",
                   GH_BIN=str(gh), COMMIT_MOVES=False, SYNC=False,
                   FETCH_TIMEOUT=10.0)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE, phases.SNAPSHOTS):
            registry.clear()
            self.addCleanup(registry.clear)

        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        self.write_cards()

    def write_cards(self, *, depends: str | None = None,
                    listed: str = "- 31 — Stand up site/\n- 32 — Serve it\n") -> None:
        self.write(ONE, card("31 — Stand up site/"))
        self.write(TWO, card("32 — Serve it", depends=depends))
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase", cards=listed), "in-progress")

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def adapter_is(self, script: str) -> None:
        self.adapter.write_text(script, encoding="utf-8")
        self.adapter.chmod(self.adapter.stat().st_mode | stat.S_IEXEC)

    # — driving the beat —

    def start(self) -> dict:
        snapshot = phases.start_phase(PHASE, "in-progress")
        self.settle()
        return snapshot

    def advance(self) -> dict:
        snapshots = phases.advance_all()
        self.settle()
        return snapshots.get(PHASE, {})

    def settle(self) -> None:
        """Wait for any agent the pass launched to be fully reaped — the
        reaper's last act is the agents broadcast, so that is the honest
        signal that the card has landed wherever it lands."""
        self.assertTrue(
            wait_for(lambda: not any(r["status"] == "running"
                                     for r in state.AGENTS.values())),
            "an agent the phase launched never ended")
        running = [r for r in state.AGENTS.values() if r.get("mode") == "work"]
        if running:
            self.assertTrue(wait_for(
                lambda: self.sent.count({"type": "agents"}) >= len(running)),
                "the reaper never announced the ending")

    # — reading the world back —

    def stage_of(self, filename: str) -> str | None:
        for slug in config.STAGE_DIRS:
            if (self.tasks / slug / filename).is_file():
                return slug
        return None

    def text(self, filename: str) -> str:
        return (self.tasks / self.stage_of(filename) / filename).read_text(
            encoding="utf-8")

    def log(self) -> list[str]:
        return phases.log_entries(self.text(PHASE))

    def branch_exists(self, branch: str) -> bool:
        return git(self.repo, "rev-parse", "--verify", "--quiet",
                   branch).returncode == 0

    def tip(self, ref: str) -> str:
        return git(self.repo, "rev-parse", ref).stdout.strip()

    def merges_on(self, branch: str) -> list[str]:
        return git(self.repo, "log", "--merges", "--format=%s",
                   branch).stdout.strip().splitlines()

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.is_file():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines() if line.strip()]


class StartingAPhase(PhaseCase):
    def test_the_branch_is_cut_from_main_and_member_one_starts_on_it(self):
        main = self.tip("main")

        self.start()

        self.assertTrue(self.branch_exists(PHASE_BRANCH))
        self.assertEqual(git(self.repo, "merge-base", PHASE_BRANCH,
                             "main").stdout.strip(), main,
                         "the phase branch starts at the newest main")
        self.assertEqual(self.stage_of(ONE), "review",
                         "member one ran and reached review/")
        self.assertEqual(git(self.repo, "merge-base", f"task/{ONE[:-3]}",
                             PHASE_BRANCH).stdout.strip(), main,
                         "member one branched from the phase's tip")

    def test_the_phase_gets_a_worktree_of_its_own(self):
        self.start()
        worktree = config.WORKTREES / PHASE[:-3]
        self.assertTrue(worktree.is_dir())
        self.assertEqual(git(worktree, "branch", "--show-current").stdout.strip(),
                         PHASE_BRANCH)

    def test_the_run_is_recorded_on_the_card(self):
        self.start()
        self.assertIn(f"run started on {PHASE_BRANCH}", self.log())
        self.assertIn("31 started", self.log())
        self.assertIn("## Phase log", self.text(PHASE))

    def test_only_the_first_member_is_launched(self):
        self.start()
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"),
                         "sequential: member two waits for member one")
        self.assertEqual(self.stage_of(TWO), "backlog")

    def test_an_empty_phase_refuses_rather_than_leaving_a_branch(self):
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase", cards=""), "in-progress")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(PHASE, "in-progress")

        self.assertIn("lists no cards", str(caught.exception))
        self.assertFalse(self.branch_exists(PHASE_BRANCH))
        self.assertFalse((config.WORKTREES / PHASE[:-3]).exists())

    def test_a_list_that_does_not_resolve_refuses(self):
        """Drift is an authoring mistake; a runner must not act it out."""
        self.write_cards(listed="- 31 — Stand up site/\n- 99 — nothing here\n")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(PHASE, "in-progress")

        self.assertIn("99", str(caught.exception))
        self.assertFalse(self.branch_exists(PHASE_BRANCH))

    def test_an_ordinary_card_is_not_a_phase(self):
        self.write(PHASE, card("40 — Just a card", status="In Progress"),
                   "in-progress")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(PHASE, "in-progress")

        self.assertIn("not a phase", str(caught.exception))

    def test_a_phase_runs_from_in_progress_only(self):
        self.write(PHASE, card("40 — Ship the site", kind="Phase",
                               cards="- 31 — Stand up site/\n"), "to-do")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(PHASE, "to-do")

        self.assertIn("in-progress", str(caught.exception))


class AdvancingOnGreen(PhaseCase):
    def test_member_twos_worktree_holds_member_ones_work(self):
        """The whole point of the branch: card two can see card one."""
        self.start()

        self.advance()

        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"))
        worktree = config.WORKTREES / TWO[:-3]
        self.assertTrue((worktree / "31.txt").is_file(),
                        "member two started from a tip that already had 31")
        self.assertTrue((worktree / "32.txt").is_file())

    def test_the_merge_lands_on_the_phase_branch_and_is_recorded(self):
        self.start()

        self.advance()

        self.assertEqual(
            git(self.repo, "merge-base", "--is-ancestor",
                f"task/{ONE[:-3]}", PHASE_BRANCH).returncode, 0)
        self.assertEqual(self.merges_on(PHASE_BRANCH),
                         [f"phase: merge task/{ONE[:-3]} into {PHASE_BRANCH}"])
        self.assertIn(f"31 merged into {PHASE_BRANCH}", self.log())
        self.assertTrue(any("merged 31" in s for s in self.summaries()))

    def test_the_phase_never_rebases_or_moves_a_members_branch(self):
        self.start()
        before = self.tip(f"task/{ONE[:-3]}")

        self.advance()

        self.assertEqual(self.tip(f"task/{ONE[:-3]}"), before,
                         "merging is additive: the member's branch is untouched")

    def test_a_finished_phase_opens_one_pr_and_moves_to_review(self):
        self.start()
        self.advance()          # merge 31, launch 32
        self.advance()          # merge 32, finish

        self.assertEqual(self.stage_of(PHASE), "review")
        self.assertEqual(len(self.merges_on(PHASE_BRANCH)), 2)
        self.assertIn("every card merged — opening the phase PR", self.log())

    def test_the_run_is_over_and_the_beat_leaves_it_alone(self):
        self.start()
        self.advance()
        self.advance()
        state.BOARD_EVENTS.clear()

        phases.advance_all()

        self.assertEqual(self.summaries(), [],
                         "a phase card out of in-progress/ is not run any more")


class DependenciesGuard(PhaseCase):
    def test_a_member_waiting_on_an_unfinished_card_is_not_launched(self):
        self.write_cards(depends="99")
        self.write("99-something-else.md", card("99 — Something else"))
        self.start()

        snapshot = self.advance()

        self.assertEqual(snapshot["waitingOn"], ["99"])
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"))
        self.assertEqual(self.stage_of(TWO), "backlog")
        self.assertIsNone(snapshot["halted"], "a guard is a wait, not a halt")

    def test_the_same_member_launches_once_the_dependency_is_done(self):
        self.write_cards(depends="99")
        self.write("99-something-else.md",
                   card("99 — Something else", status="Done"), "done")
        self.start()

        self.advance()

        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"))

    def test_a_padded_number_is_the_same_card_everywhere(self):
        """`07`, `7` and `#007` are one card — in the list, in the log and
        in a dependency alike, or none of them match."""
        self.write("07-early.md", card("07 — An early card"))
        self.write(TWO, card("32 — Serve it", depends="07"))
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase",
                               cards="- 07 — An early card\n- 32 — Serve it\n"),
                   "in-progress")

        self.start()
        self.advance()

        self.assertIn("7 started", self.log())
        self.assertTrue(self.branch_exists("task/07-early"))
        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"),
                        "the dependency on 07 resolved against member 7")

    def test_a_dependency_inside_the_phase_counts_as_finished_when_merged(self):
        self.write_cards(depends="31")
        self.start()

        self.advance()

        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"),
                        "31 is merged into the phase branch — that is finished")


class HaltNeverSkip(PhaseCase):
    def halted(self) -> str:
        return phases._halt_reason(self.log()) or ""

    def test_a_member_that_declines_halts_the_phase(self):
        self.adapter_is(DECLINES)
        self.start()

        self.assertEqual(self.stage_of(ONE), "to-do", "the card walks back")
        snapshot = self.advance()

        self.assertIn("31:", self.halted())
        self.assertIn("declined", self.halted())
        self.assertIsNotNone(snapshot["halted"])
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"),
                         "no further member starts")

    def test_a_run_that_dies_halts_the_phase(self):
        self.adapter_is(DIES)
        self.start()

        self.advance()

        self.assertEqual(self.stage_of(ONE), "in-progress")
        self.assertIn("without reaching review/", self.halted())
        self.assertIn("API Error: 500", self.halted())
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"))

    def test_a_clean_run_that_committed_nothing_halts_the_phase(self):
        self.adapter_is(COMMITS_NOTHING)
        self.start()

        self.advance()

        self.assertEqual(self.stage_of(ONE), "in-progress")
        self.assertIn("without reaching review/", self.halted())
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"))

    def test_red_ci_halts_the_phase(self):
        self.start()
        github.PR_STATE[ONE] = {"verdict": "red", "ci": "fail", "url": PR_URL}

        self.advance()

        self.assertIn("CI is red", self.halted())
        self.assertEqual(self.merges_on(PHASE_BRANCH), [],
                         "nothing is merged on a red member")

    def test_checks_still_running_is_a_wait_not_a_halt(self):
        self.start()
        github.PR_STATE[ONE] = {"verdict": "pending", "ci": "running", "url": PR_URL}

        snapshot = self.advance()

        self.assertIsNone(snapshot["halted"])
        self.assertEqual(self.merges_on(PHASE_BRANCH), [])

    def test_a_halt_is_said_once_and_then_held(self):
        self.adapter_is(DIES)
        self.start()
        self.advance()
        state.BOARD_EVENTS.clear()
        lines = len(self.log())

        self.advance()

        self.assertEqual(self.summaries(), [], "a halted phase is quiet")
        self.assertEqual(len(self.log()), lines, "and writes nothing more")

    def test_running_it_again_clears_the_halt_and_carries_on(self):
        self.adapter_is(DIES)
        self.start()
        self.advance()
        self.assertTrue(self.halted())
        self.adapter_is(WORKS)

        phases.start_phase(PHASE, "in-progress")
        self.settle()

        self.assertEqual(self.halted(), "",
                         "the person's decision to run it again clears the halt")
        self.assertEqual(self.stage_of(ONE), "review")


class ConflictsAreRefused(PhaseCase):
    """The third place in this codebase that merges branches: it aborts
    cleanly, leaves no half-merged branch, and names the collision."""

    def test_a_member_that_collides_with_main_halts_and_leaves_no_mess(self):
        self.adapter_is(COLLIDES)
        self.start()
        (self.repo / "code.txt").write_text("main moved on\n", encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "main moved on")

        self.advance()

        self.assertIn("conflicts", phases._halt_reason(self.log()) or "")
        self.assertIn("code.txt", phases._halt_reason(self.log()) or "")
        worktree = config.WORKTREES / PHASE[:-3]
        merge_head = git(worktree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        self.assertFalse((worktree / merge_head).is_file() or
                         Path(merge_head).is_file(),
                         "no merge is left in progress")
        self.assertEqual(git(worktree, "status", "--porcelain").stdout, "",
                         "the phase worktree is left clean")
        self.assertEqual(
            git(self.repo, "merge-base", "--is-ancestor",
                f"task/{ONE[:-3]}", PHASE_BRANCH).returncode, 1,
            "nothing half-merged: the member's branch did not land")


class TheBranchIsKeptFresh(PhaseCase):
    def test_main_is_merged_into_the_phase_branch_on_the_beat(self):
        self.start()
        (self.repo / "later.txt").write_text("landed on main\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "landed on main")

        self.advance()

        self.assertEqual(
            git(self.repo, "merge-base", "--is-ancestor", "main",
                PHASE_BRANCH).returncode, 0,
            "a phase that runs for hours does not drift")
        self.assertTrue(any("merged main into" in s for s in self.summaries()))

    def test_an_unchanged_main_is_merged_nowhere(self):
        self.start()
        state.BOARD_EVENTS.clear()

        self.advance()

        self.assertEqual([s for s in self.summaries() if "merged main" in s], [])


class ARestartResumesByLooking(PhaseCase):
    """The runner holds no registry: git and the card carry the memory."""

    def restart(self) -> None:
        """What a board restart costs the runner: everything it held in
        memory about running agents and past passes."""
        state.AGENTS.clear()
        phases.SNAPSHOTS.clear()

    def test_a_merged_member_is_not_merged_twice(self):
        self.start()
        self.advance()          # merge 31, launch 32
        self.restart()

        self.advance()

        self.assertEqual(len([m for m in self.merges_on(PHASE_BRANCH)
                              if ONE[:-3] in m]), 1)
        self.assertEqual(len([e for e in self.log() if e.startswith("31 merged")]), 1)

    def test_a_started_member_is_not_started_twice(self):
        """The log is what tells "already run, and it ended badly" from
        "not reached yet" — without it a restart would relaunch a dead run."""
        self.adapter_is(DECLINES)
        self.start()
        self.restart()

        self.advance()

        self.assertEqual(len([e for e in self.log() if e == "31 started"]), 1)
        self.assertIn("31:", phases._halt_reason(self.log()) or "")

    def test_a_run_lost_to_a_restart_halts_rather_than_relaunching(self):
        self.start()
        # put the card back where a run that never finished would leave it
        taskfiles.move_task(ONE, "review", "in-progress", actor="tester")
        self.restart()

        self.advance()

        self.assertIn("without reaching review/", phases._halt_reason(self.log()) or "")


class HandMovesDoNotDoubleLaunch(PhaseCase):
    def test_a_card_moved_by_hand_under_a_running_agent_is_left_alone(self):
        self.start()
        state.AGENTS.clear()
        state.AGENTS["live"] = {"id": "live", "task": TWO, "status": "running",
                                "started": time.time(), "mode": "work"}
        taskfiles.move_task(TWO, "backlog", "review", actor="you")
        state.BOARD_EVENTS.clear()

        snapshot = phases.advance_all()[PHASE]

        self.assertEqual([m["state"] for m in snapshot["members"] if m["file"] == TWO],
                         ["running"], "an agent is on it — the phase waits")
        self.assertEqual(len(state.AGENTS), 1, "no second launch on the same task")
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"))

    def test_a_member_finished_before_the_phase_reached_it_needs_no_run(self):
        self.write(TWO, card("32 — Serve it", status="Done"), "done")
        (self.tasks / "backlog" / TWO).unlink()
        self.start()

        self.advance()

        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"),
                         "a card with no branch that is already done is finished")
        self.assertEqual(self.stage_of(PHASE), "review")


class OneBoardRunsIt(PhaseCase):
    """State syncs; reactions don't — the assignee is where "who runs it"
    is written down, and a replica advances nothing."""

    def setUp(self):
        super().setUp()
        self.patch(COMMIT_MOVES=True)

    def test_the_board_that_holds_the_card_runs_it(self):
        self.start()

        self.assertIn("**Assignee:** tester", self.text(PHASE))
        self.assertEqual(self.stage_of(ONE), "review")

    def test_someone_elses_phase_advances_nowhere(self):
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase",
                               cards="- 31 — Stand up site/\n- 32 — Serve it\n"
                               ).replace("**Priority:** High",
                                         "**Priority:** High\n**Assignee:** elena"),
                   "in-progress")
        git(self.repo, "checkout", "-q", "-b", "phase/40-ship-the-site")
        git(self.repo, "checkout", "-q", "main")
        state.BOARD_EVENTS.clear()

        snapshot = phases.advance_all()[PHASE]

        self.assertEqual(len(state.AGENTS), 0, "a replica launches nothing")
        self.assertEqual(self.summaries(), [])
        self.assertEqual([m["state"] for m in snapshot["members"]],
                         ["pending", "pending"], "but it still renders the phase")

    def test_starting_someone_elses_phase_refuses_and_names_them(self):
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase",
                               cards="- 31 — Stand up site/\n"
                               ).replace("**Priority:** High",
                                         "**Priority:** High\n**Assignee:** elena"),
                   "in-progress")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(PHASE, "in-progress")

        self.assertIn("elena holds", str(caught.exception))
        self.assertFalse(self.branch_exists(PHASE_BRANCH))


class ThePhasePR(PhaseCase):
    """From review/ onwards a phase card is an ordinary card — which means
    its branch has to be the one the apparatus finds."""

    REMOTE = True

    def test_the_pr_goes_from_the_phase_branch_into_main(self):
        self.start()
        self.advance()
        self.advance()

        create = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(create), 1, "one PR at the end, not one per card")
        self.assertIn("--head", create[0])
        self.assertEqual(create[0][create[0].index("--head") + 1], PHASE_BRANCH)
        self.assertEqual(create[0][create[0].index("--base") + 1], "main")
        self.assertIn(f"**PR:** {PR_URL}", self.text(PHASE))
        self.assertEqual(self.stage_of(PHASE), "review")

    def test_the_body_is_the_member_list(self):
        self.start()
        self.advance()
        self.advance()

        create = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]][0]
        body = create[create.index("--body") + 1]
        self.assertIn("31 — 31 — Stand up site/", body)
        self.assertIn("32 — 32 — Serve it", body)

    def test_a_members_own_pr_is_based_on_the_phase_branch(self):
        """A member's branch was cut from the phase's, so main is not a base
        whose diff means anything — and a PR into main would invite exactly
        the merge this whole design refuses to make."""
        self.start()

        github.maybe_open_pr(ONE)

        create = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(create[0][create[0].index("--base") + 1], PHASE_BRANCH)
        self.assertEqual(create[0][create[0].index("--head") + 1], f"task/{ONE[:-3]}")

    def test_the_phase_branch_is_published_so_the_base_exists(self):
        self.start()
        self.assertEqual(
            git(self.repo, "rev-parse", f"origin/{PHASE_BRANCH}").returncode, 0,
            "a member's PR needs its base on the remote")


class TheCardsBranchIsFound(PhaseCase):
    def test_a_phase_card_wears_its_own_branch(self):
        self.start()
        self.assertEqual(github.branch_of(PHASE), PHASE_BRANCH)
        self.assertEqual(github.branch_of(ONE), f"task/{ONE[:-3]}")
        self.assertIn(PHASE[:-3], github.task_branches())
        self.assertIn(ONE[:-3], github.task_branches())

    def test_a_card_with_no_branch_at_all_names_the_task_branch(self):
        self.assertEqual(github.branch_of(PHASE), f"task/{PHASE[:-3]}")


class TheBranchPointOfAMember(PhaseCase):
    def test_a_member_of_a_running_phase_branches_from_the_phase(self):
        self.start()
        point, note = agents.phase_branch_point(TWO)
        self.assertEqual(point, PHASE_BRANCH)
        self.assertIn("the phase's own branch", note)

    def test_a_member_of_a_phase_that_has_not_started_branches_as_usual(self):
        self.assertEqual(agents.phase_branch_point(TWO), (None, None))

    def test_a_card_in_no_phase_branches_as_usual(self):
        self.write("77-alone.md", card("77 — On its own"))
        self.start()
        self.assertEqual(agents.phase_branch_point("77-alone.md"), (None, None))

    def test_the_ticker_names_the_branch_point(self):
        self.start()
        self.advance()
        self.assertTrue(any("the phase's own branch" in s for s in self.summaries()),
                        "an unusual branch point is always narrated")


class ThePhaseLog(unittest.TestCase):
    """The record on the card, read back — the only thing that can tell an
    already-run member from one the phase has not reached."""

    def entries(self, *lines: str) -> list[str]:
        body = "\n".join(f"- 2026-08-01 09:0{i} · {line}"
                         for i, line in enumerate(lines))
        return phases.log_entries(f"# 40\n\n## Phase log\n\n{body}\n")

    def test_entries_are_read_in_order(self):
        self.assertEqual(self.entries("run started on phase/40-x", "31 started"),
                         ["run started on phase/40-x", "31 started"])

    def test_a_card_with_no_log_has_no_entries(self):
        self.assertEqual(phases.log_entries("# 40\n\n**Status:** In Progress\n"), [])

    def test_the_section_ends_where_the_next_heading_begins(self):
        text = ("# 40\n\n## Phase log\n\n- 2026-08-01 09:00 · 31 started\n\n"
                "## Work report\n\n- 2026-08-01 09:01 · 32 started\n")
        self.assertEqual(phases.log_entries(text), ["31 started"])

    def test_started_members_are_the_ones_it_launched(self):
        entries = self.entries("run started on phase/40-x", "31 started",
                               "31 merged into phase/40-x", "32 started")
        self.assertEqual(phases._started(entries), {"31", "32"})

    def test_a_halt_holds_until_a_run_clears_it(self):
        entries = self.entries("31 started", "halted at 31 — its CI is red")
        self.assertEqual(phases._halt_reason(entries), "31: its CI is red")

    def test_a_later_run_clears_the_halt(self):
        entries = self.entries("halted at 31 — its CI is red",
                               "run started on phase/40-x")
        self.assertIsNone(phases._halt_reason(entries))

    def test_a_phase_that_never_halted_is_not_halted(self):
        self.assertIsNone(phases._halt_reason(
            self.entries("run started on phase/40-x", "31 started")))


class AppendingToASection(unittest.TestCase):
    """taskfiles' third door: a running record that stays in one place."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-section-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tasks = tmp / "tasks"
        (self.tasks / "in-progress").mkdir(parents=True)
        self.addCleanup(setattr, config, "TASKS", config.TASKS)
        config.TASKS = self.tasks
        self.addCleanup(setattr, config, "COMMIT_MOVES", config.COMMIT_MOVES)
        config.COMMIT_MOVES = False
        self.path = self.tasks / "in-progress" / PHASE
        self.path.write_text(card("40 — Ship the site", status="In Progress",
                                  kind="Phase", cards="- 31 — one\n"),
                             encoding="utf-8")

    def append(self, line: str) -> None:
        taskfiles.append_to_section(PHASE, "in-progress", "Phase log", line, "log")

    def test_the_first_line_creates_the_section(self):
        self.append("- one")
        self.assertIn("## Phase log\n\n- one\n", self.path.read_text())

    def test_later_lines_join_it(self):
        self.append("- one")
        self.append("- two")
        text = self.path.read_text()
        self.assertIn("## Phase log\n\n- one\n- two\n", text)
        self.assertEqual(text.count("## Phase log"), 1)

    def test_a_section_appended_after_it_does_not_break_the_record(self):
        self.append("- one")
        taskfiles.append_to_task(PHASE, "in-progress",
                                 "\n\n---\n\n## Work report\n\nsomething\n", "report")
        self.append("- two")
        text = self.path.read_text()
        self.assertLess(text.index("- two"), text.index("## Work report"),
                        "the log stays in one place a person can read")
        self.assertEqual(phases.log_entries(text), [])   # not stamped lines

    def test_the_cards_section_is_left_intact(self):
        self.append("- one")
        task = taskfiles.read_task(self.path, "in-progress")
        self.assertEqual(task["cards"], ["31"])
        self.assertEqual(task["phaseDrift"], [])


class TheDocumentedPromise(unittest.TestCase):
    """AGENTS.md quotes "the board never merges" as an absolute. After this
    card it has to say what it always meant."""

    @classmethod
    def setUpClass(cls):
        cls.doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")

    def test_the_promise_is_about_main(self):
        self.assertIn("the board never merges into `main`", self.doc)
        self.assertNotIn("Merging remains yours — the board never merges.", self.doc)

    def test_the_module_map_names_the_runner(self):
        self.assertIn("phases.py", self.doc)

    def test_the_runner_is_described(self):
        self.assertIn("phase/<task-stem>", self.doc)


if __name__ == "__main__":
    unittest.main()
