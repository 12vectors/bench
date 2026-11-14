"""An external workspace, end to end (task 56).

The layout the issue asks for: a grouping directory holding the tool, the
application's own repository, the work-state repository and the
application's worktrees, none of them inside another. What these cases
hold the code to is *which repository each operation lands in* — code in
the application, bookkeeping in the work state, and nothing at all added
to the application's tracked files.

Disposable repositories, a stub adapter and a stub `gh` throughout: no
token is spent and no remote is mutated.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import agents  # noqa: E402
import commands as commands_mod  # noqa: E402
import config  # noqa: E402
import drive as drive_mod  # noqa: E402
import github  # noqa: E402
import phases  # noqa: E402
import reports  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")
CARD_FILE = "56-external-workspaces.md"
PR_URL = "https://github.com/acme/widget/pull/56"

WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
stem = os.environ["BOARD_TASK"].split("-")[0]
open(os.path.join(cwd, stem + ".txt"), "w").write("built " + stem + "\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "card " + stem], check=True)
print("WORK REPORT: built the thing in", cwd)
"""

FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("GH_LOG")
if log:
    with open(log, "a") as fh:
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
         cards: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    text += "\nWhat this card is for, at enough length to read as a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Workspace(unittest.TestCase):
    """tool/, app/ (+ a bare origin), work/ (+ a bare state origin) and
    worktrees/ in one grouping directory that is not a repository."""

    STATE_IS_A_REPO = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-external-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.app_origin = self.tmp / "app-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.app_origin)], check=True, capture_output=True)
        self.app = self.tmp / "app"
        subprocess.run(["git", "clone", "-q", str(self.app_origin), str(self.app)],
                       check=True, capture_output=True)
        git(self.app, "config", "user.name", "appdev")
        git(self.app, "config", "user.email", "appdev@example.test")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        (self.app / "AGENTS.md").write_text("# The app's own rules\n",
                                            encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "the application")
        git(self.app, "push", "-q", "origin", "main")

        self.work = self.tmp / "work"
        self.tasks = self.work / "tasks"
        for slug in (*STAGES, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        (self.work / "plans").mkdir()
        (self.work / "reference").mkdir()
        (self.work / "AGENTS.md").write_text(
            "# How this board works\n", encoding="utf-8")
        if self.STATE_IS_A_REPO:
            subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)],
                           check=True, capture_output=True)
            git(self.work, "config", "user.name", "boardkeeper")
            git(self.work, "config", "user.email", "board@example.test")
            git(self.work, "add", "-A")
            git(self.work, "commit", "-q", "-m", "the board")

        self.tool = self.tmp / "tool"
        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        self.adapter = self.local / "adapters" / config.ADAPTER / "run"
        self._executable(self.adapter, WORKS)

        self.gh = self.tmp / "gh"
        self._executable(self.gh, FAKE_GH)
        self.gh_log = self.tmp / "gh.log"
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)

        self.worktrees = self.tmp / "worktrees"

        self.patch(REPO=self.app, WORK=self.work, TASKS=self.tasks,
                   TM_ROOT=self.tool, LOCAL=self.local,
                   WORKTREES=self.worktrees,
                   APP_SETTING=str(self.app), WORK_SETTING=str(self.work),
                   EXTERNAL=True, STATE=self.local / "state",
                   AGENT_DIR=self.local / "state" / "agent",
                   DRIVES_DIR=self.local / "state" / "drives",
                   SESSIONS_DIR=self.local / "state" / "sessions",
                   GH_BIN=str(self.gh), COMMIT_MOVES=False, SYNC=False,
                   APP_BASE="main", STATE_BASE="main",
                   FETCH_TIMEOUT=10.0)
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE, phases.SNAPSHOTS):
            registry.clear()
            self.addCleanup(registry.clear)
        state.COMMIT_HOOKS.clear()
        self.addCleanup(state.COMMIT_HOOKS.clear)

        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

    def _executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — reading the world back —

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def stage_of(self, filename: str) -> str | None:
        for slug in STAGES:
            if (self.tasks / slug / filename).is_file():
                return slug
        return None

    def body(self, filename: str) -> str:
        stage = self.stage_of(filename)
        return (self.tasks / stage / filename).read_text(encoding="utf-8")

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def app_tracked(self) -> list[str]:
        return git(self.app, "ls-files").stdout.split()

    def app_subjects(self) -> list[str]:
        return git(self.app, "log", "--format=%s", "main").stdout.splitlines()

    def work_subjects(self) -> list[str]:
        return git(self.work, "log", "--format=%s", "main").stdout.splitlines()

    def settle(self) -> None:
        """Wait for every agent launched so far to be fully reaped.

        The reaper flips the status before it moves the card, so "nothing
        is running" is not the signal — its last act is the agents
        broadcast, and there is exactly one per ended run. Counting them
        is what makes a second launch in the same test wait for its own
        ending rather than returning on the first one's.
        """
        self.assertTrue(
            wait_for(lambda: not any(r["status"] == "running"
                                     for r in state.AGENTS.values())),
            "an agent never ended")
        ended = len([r for r in state.AGENTS.values()
                     if r["status"] != "running"])
        self.assertTrue(
            wait_for(lambda: self.sent.count({"type": "agents"}) >= ended),
            "the reaper never announced the ending")


class ExternalWorkAgent(Workspace):
    """A work agent branches, worktrees and commits in the application —
    and the board's record of it lands in the work state."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces",
                                   status="In Progress"), "in-progress")

    def test_the_branch_and_worktree_are_the_applications(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        branch = f"task/{CARD_FILE[:-3]}"
        self.assertEqual(
            git(self.app, "rev-parse", "--verify", "--quiet", branch).returncode,
            0, "the branch is cut in the application repository")
        self.assertNotEqual(
            git(self.work, "rev-parse", "--verify", "--quiet", branch).returncode,
            0, "and not in the work-state repository")
        worktree = self.worktrees / CARD_FILE[:-3]
        self.assertTrue(worktree.is_dir(), "the worktree sits where configured")
        self.assertEqual(
            git(worktree, "rev-parse", "--show-toplevel").stdout.strip(),
            str(worktree), "it is a checkout of its own")
        self.assertEqual(
            git(worktree, "rev-parse", "--git-common-dir").returncode, 0)

    def test_the_commit_lands_in_the_application_on_its_own_branch(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        branch = f"task/{CARD_FILE[:-3]}"
        subjects = git(self.app, "log", "--format=%s", branch).stdout.splitlines()
        self.assertIn("card 56", subjects)
        self.assertNotIn("card 56", self.app_subjects(),
                         "the application's own main is untouched")

    def test_the_card_moves_to_review_in_the_work_state(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        self.assertEqual(self.stage_of(CARD_FILE), "review")
        self.assertTrue((self.tasks / "review" / CARD_FILE).is_file())

    def test_the_report_is_appended_to_the_externally_stored_card(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        body = self.body(CARD_FILE)
        self.assertIn("## Work report", body)
        self.assertIn("WORK REPORT: built the thing in", body)
        self.assertEqual(
            list((self.app).glob("**/56-external-workspaces.md")), [],
            "no copy of the card appears anywhere in the application")

    def test_the_application_gains_no_bench_files_at_all(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        tracked = self.app_tracked()
        for path in tracked:
            self.assertNotIn("task-manager", path)
            self.assertFalse(path.startswith("tasks/"), path)
            self.assertFalse(path.startswith("manager/"), path)
            self.assertFalse(path.startswith(".claude/"), path)
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(), "",
                         "the application's working tree is left clean")

    def test_the_agent_log_stays_in_the_tool_install(self):
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        logs = list((self.local / "state" / "agent" / "logs").glob("*.log"))
        self.assertTrue(logs, "the run's log is kept under the tool's state")
        self.assertEqual(list(self.work.glob("**/*.log")), [],
                         "runtime logs never reach the shared work state")

    def test_the_launch_tells_the_adapter_where_everything_is(self):
        seen = self.tmp / "env.json"
        self._executable(self.adapter, f"""#!/usr/bin/env python3
import json, os
json.dump(dict(os.environ), open({str(seen)!r}, "w"))
print("WORK REPORT: looked around")
""")
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        env = json.loads(seen.read_text(encoding="utf-8"))
        self.assertEqual(env["BOARD_APP_ROOT"], str(self.app))
        self.assertEqual(env["BOARD_WORK_ROOT"], str(self.work))
        self.assertEqual(env["BOARD_TASKS_DIR"], str(self.tasks))
        self.assertEqual(env["BOARD_WORKTREES_DIR"], str(self.worktrees))
        self.assertEqual(env["AGENT_CWD"], str(self.worktrees / CARD_FILE[:-3]))

    def test_the_prompt_names_the_brief_that_lives_outside_the_checkout(self):
        seen = self.tmp / "prompt.txt"
        self._executable(self.adapter, f"""#!/usr/bin/env python3
import os
open({str(seen)!r}, "w").write(os.environ["AGENT_PROMPT"])
print("WORK REPORT: read the brief")
""")
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        prompt = seen.read_text(encoding="utf-8")
        self.assertIn(str(self.work / "AGENTS.md"), prompt)
        self.assertIn(str(self.tasks / "in-progress" / CARD_FILE), prompt,
                      "the ticket is named by the path it actually has")

    def test_a_clean_exit_that_committed_nothing_keeps_the_card_and_says_so(self):
        self._executable(self.adapter, """#!/usr/bin/env python3
print("WORK REPORT: read a lot and wrote nothing")
""")
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        self.assertEqual(self.stage_of(CARD_FILE), "in-progress",
                         "an empty branch never reaches review/")
        self.assertTrue(any("committed NOTHING" in s for s in self.summaries()))

    def test_a_dead_run_clears_only_the_applications_branch(self):
        self._executable(self.adapter, """#!/usr/bin/env python3
import sys
print("API Error: 500 overloaded")
sys.exit(1)
""")
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()

        branch = f"task/{CARD_FILE[:-3]}"
        self.assertNotEqual(
            git(self.app, "rev-parse", "--verify", "--quiet", branch).returncode, 0,
            "the empty branch is cleared from the application")
        self.assertFalse((self.worktrees / CARD_FILE[:-3]).exists())
        self.assertEqual(self.stage_of(CARD_FILE), "in-progress")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(), "")

    def test_a_relevance_review_runs_in_the_application_checkout(self):
        seen = self.tmp / "cwd.txt"
        self._executable(self.adapter, f"""#!/usr/bin/env python3
import os
open({str(seen)!r}, "w").write(os.getcwd())
print("RELEVANCE REVIEW: Still relevant")
""")
        agents.start_review(CARD_FILE, "in-progress")
        self.assertTrue(wait_for(
            lambda: not any(r["status"] == "running"
                            for r in state.AGENTS.values())))

        self.assertEqual(Path(seen.read_text(encoding="utf-8")).resolve(),
                         self.app)
        self.assertTrue(wait_for(
            lambda: "## Relevance review" in self.body(CARD_FILE)))


class ExternalBookkeeping(Workspace):
    """Every board-made write to a task file commits — to the work-state
    repository, and to nothing else."""

    def setUp(self):
        super().setUp()
        self.patch(COMMIT_MOVES=True)
        self.write(CARD_FILE, card("56 — External workspaces"))
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "the card")
        self.app_before = self.app_subjects()

    def test_a_move_commits_to_the_work_repository_only(self):
        taskfiles.move_task(CARD_FILE, "backlog", "to-do")

        self.assertIn("board: 56 → to-do (boardkeeper)", self.work_subjects())
        self.assertEqual(self.app_subjects(), self.app_before,
                         "a card moving columns never commits on the app's main")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(), "")

    def test_the_claim_is_the_work_repositorys_identity(self):
        taskfiles.move_task(CARD_FILE, "backlog", "to-do")
        self.assertIn("**Assignee:** boardkeeper", self.body(CARD_FILE))
        self.assertEqual(taskfiles.actor_name(), "boardkeeper")

    def test_archiving_and_undoing_it_both_land_in_the_work_repository(self):
        taskfiles.archive_task(CARD_FILE, "backlog")
        self.assertTrue((self.tasks / "archive" / CARD_FILE).is_file())
        self.assertTrue(any("archived" in s for s in self.work_subjects()))

        taskfiles.unarchive_task(CARD_FILE, "backlog")
        self.assertEqual(self.stage_of(CARD_FILE), "backlog")
        self.assertIn("**Status:** Backlog", self.body(CARD_FILE))
        self.assertEqual(git(self.work, "status", "--porcelain").stdout.strip(),
                         "", "the way back is committed like the way out")
        self.assertEqual(self.app_subjects(), self.app_before)

    def test_an_appended_report_commits_to_the_work_repository(self):
        taskfiles.append_to_task(CARD_FILE, "backlog", "\n## Note\n\nseen\n",
                                 "note filed")
        self.assertTrue(any("note filed" in s for s in self.work_subjects()))
        self.assertEqual(self.app_subjects(), self.app_before)

    def test_the_pr_line_commits_to_the_work_repository(self):
        taskfiles.move_task(CARD_FILE, "backlog", "to-do")
        taskfiles.move_task(CARD_FILE, "to-do", "in-progress")
        taskfiles.move_task(CARD_FILE, "in-progress", "review")
        github._write_pr_line(CARD_FILE, PR_URL)

        self.assertIn(f"**PR:** {PR_URL}", self.body(CARD_FILE))
        self.assertTrue(any("PR opened" in s for s in self.work_subjects()))
        self.assertEqual(self.app_subjects(), self.app_before)


class ExternalStateWithoutARepository(Workspace):
    """The issue's first slice: external application, plain-directory work
    state, bookkeeping off. Everything works; nothing is committed, and
    nothing reaches for the application's history instead."""

    STATE_IS_A_REPO = False

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces"))

    def test_there_is_no_state_repository_and_none_is_invented(self):
        self.assertIsNone(config.state_repo())

    def test_moves_still_work_and_commit_nothing_anywhere(self):
        before = self.app_subjects()
        taskfiles.move_task(CARD_FILE, "backlog", "to-do")

        self.assertEqual(self.stage_of(CARD_FILE), "to-do")
        self.assertIn("**Status:** To Do", self.body(CARD_FILE))
        self.assertEqual(self.app_subjects(), before)
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(), "")

    def test_the_gate_on_without_a_repository_says_so_rather_than_guessing(self):
        self.patch(COMMIT_MOVES=True)
        before = self.app_subjects()

        taskfiles.move_task(CARD_FILE, "backlog", "to-do")

        self.assertEqual(self.stage_of(CARD_FILE), "to-do",
                         "the move itself still happened on disk")
        self.assertEqual(self.app_subjects(), before,
                         "and emphatically not on the application's main")
        self.assertTrue(any("work-state repository" in s
                            for s in self.summaries()), self.summaries())


class ExternalPhase(Workspace):
    """A phase's integration branch is the application's; its log is the
    externally stored card's."""

    PHASE_FILE = "60-ship-it.md"
    ONE = "61-first.md"
    TWO = "62-second.md"

    def setUp(self):
        super().setUp()
        self.write(self.ONE, card("61 — First"))
        self.write(self.TWO, card("62 — Second"))
        self.write(self.PHASE_FILE,
                   card("60 — Ship it", status="In Progress", kind="Phase",
                        cards="- 61 — First\n- 62 — Second\n"), "in-progress")

    def run_pass(self) -> dict:
        snapshots = phases.advance_all()
        self.settle()
        return snapshots.get(self.PHASE_FILE, {})

    def test_the_phase_branch_is_cut_in_the_application(self):
        phases.start_phase(self.PHASE_FILE, "in-progress")
        self.settle()

        branch = f"phase/{self.PHASE_FILE[:-3]}"
        self.assertEqual(
            git(self.app, "rev-parse", "--verify", "--quiet", branch).returncode, 0)
        self.assertNotEqual(
            git(self.work, "rev-parse", "--verify", "--quiet", branch).returncode, 0)
        self.assertTrue((self.worktrees / self.PHASE_FILE[:-3]).is_dir())

    def test_the_phase_log_is_written_into_the_external_card(self):
        phases.start_phase(self.PHASE_FILE, "in-progress")
        self.settle()

        body = self.body(self.PHASE_FILE)
        self.assertIn("## Phase log", body)
        self.assertIn("run started on phase/60-ship-it", body)
        self.assertEqual(list(self.app.glob("**/60-ship-it.md")), [])

    def test_a_member_branches_from_the_phase_tip_in_the_application(self):
        phases.start_phase(self.PHASE_FILE, "in-progress")
        self.settle()

        self.assertEqual(self.stage_of(self.ONE), "review")
        merge_base = git(self.app, "merge-base", "--is-ancestor",
                         f"phase/{self.PHASE_FILE[:-3]}", f"task/{self.ONE[:-3]}")
        self.assertEqual(merge_base.returncode, 0,
                         "the member was cut from the phase's own branch")

    def test_the_run_works_the_whole_list_and_opens_one_pr(self):
        phases.start_phase(self.PHASE_FILE, "in-progress")
        self.settle()
        for _ in range(5):
            snapshot = self.run_pass()
            if self.stage_of(self.PHASE_FILE) == "review":
                break

        self.assertEqual(self.stage_of(self.PHASE_FILE), "review")
        self.assertIn(f"**PR:** {PR_URL}", self.body(self.PHASE_FILE))
        for member in (self.ONE, self.TWO):
            self.assertEqual(
                git(self.app, "merge-base", "--is-ancestor",
                    f"task/{member[:-3]}",
                    f"phase/{self.PHASE_FILE[:-3]}").returncode, 0,
                f"{member} is in the phase branch")

    def test_finishing_the_phase_merges_in_the_application_and_sweeps_cards(self):
        phases.start_phase(self.PHASE_FILE, "in-progress")
        self.settle()
        for _ in range(5):
            self.run_pass()
            if self.stage_of(self.PHASE_FILE) == "review":
                break
        self.assertEqual(self.stage_of(self.PHASE_FILE), "review")

        github.complete_task(self.PHASE_FILE, "review")

        self.assertEqual(self.stage_of(self.PHASE_FILE), "done")
        self.assertEqual(self.stage_of(self.ONE), "done")
        self.assertEqual(self.stage_of(self.TWO), "done")
        self.assertTrue(any("phase: merge" in s for s in self.app_subjects()),
                        "the phase landed on the application's main")
        for member in (self.ONE, self.TWO):
            self.assertFalse((self.worktrees / member[:-3]).exists(),
                             f"{member}'s worktree was cleared")


class ExternalDriverAndCommands(Workspace):
    """Both run against the task's worktree in the application, and are
    told the application's root."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces",
                                   status="In Progress"), "in-progress")
        agents.start_agent(CARD_FILE, "in-progress")
        self.settle()
        self.seen = self.tmp / "seen.json"

    def _recorder(self, path: Path) -> None:
        self._executable(path, f"""#!/usr/bin/env python3
import json, os
json.dump({{"cwd": os.getcwd(), "env": dict(os.environ)}},
          open({str(self.seen)!r}, "w"))
print("DRIVE URL: http://127.0.0.1:9999/")
""")

    def test_the_driver_is_handed_the_applications_worktree(self):
        (self.local / "driver").mkdir(parents=True, exist_ok=True)
        self._recorder(self.local / "driver" / "start")
        try:
            drive_mod.start(CARD_FILE)
            self.assertTrue(wait_for(lambda: self.seen.is_file()))
            seen = json.loads(self.seen.read_text(encoding="utf-8"))
        finally:
            try:
                drive_mod.stop()
            except ValueError:
                pass
            drive_mod.DRIVE = None

        self.assertEqual(seen["env"]["DRIVE_REPO"], str(self.app))
        self.assertEqual(seen["env"]["DRIVE_WORKTREE"],
                         str(self.worktrees / CARD_FILE[:-3]))
        self.assertEqual(Path(seen["cwd"]).resolve(), self.app)

    def test_a_local_command_runs_inside_the_applications_worktree(self):
        (self.local / "commands").mkdir(parents=True, exist_ok=True)
        self._recorder(self.local / "commands" / "reseed")
        commands_mod.run("reseed", CARD_FILE)
        self.assertTrue(wait_for(lambda: self.seen.is_file()))
        seen = json.loads(self.seen.read_text(encoding="utf-8"))

        self.assertEqual(seen["env"]["CMD_REPO"], str(self.app))
        self.assertEqual(Path(seen["cwd"]).resolve(),
                         self.worktrees / CARD_FILE[:-3])
        logs = list((self.local / "state" / "commands").glob("*.log"))
        self.assertTrue(logs, "the command's log stays in the tool install")


class ExternalReferences(Workspace):
    """What the record says about where things are. No fictional
    application-relative task paths (issue requirement 6)."""

    def setUp(self):
        super().setUp()
        self.write(CARD_FILE, card("56 — External workspaces",
                                   status="Review"), "review")

    def test_the_pr_body_does_not_invent_an_application_path(self):
        task = taskfiles.read_task(self.tasks / "review" / CARD_FILE, "review")
        body = github._pr_body(CARD_FILE, task)

        self.assertNotIn(".task-manager/tasks/review/", body)
        self.assertIn(CARD_FILE, body)
        self.assertIn("outside this repository", body)

    def test_a_log_reference_points_into_the_tool_install(self):
        log = self.local / "state" / "agent" / "logs" / "56-x.log"
        text = reports.report("x" * 20000, log_path=str(log))
        self.assertIn("manager/local/state/agent/logs/56-x.log", text)
        self.assertNotIn(str(self.app), text)

    def test_plans_and_reference_are_read_from_the_work_root(self):
        (self.work / "plans" / "56-plan.md").write_text("plan\n",
                                                        encoding="utf-8")
        (self.work / "reference" / "spec.md").write_text("spec\n",
                                                         encoding="utf-8")
        extras = taskfiles.collect()["extras"]
        self.assertEqual(extras["plans"], ["56-plan.md"])
        self.assertEqual(extras["reference"], ["spec.md"])


class VendoredReferencesAreUnchanged(unittest.TestCase):
    """The legacy layout keeps saying exactly what it said."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-vendored-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "host"
        tm = self.repo / ".task-manager"
        for slug in (*STAGES, "archive"):
            (tm / "tasks" / slug).mkdir(parents=True)
        for attr, value in (("REPO", self.repo), ("TM_ROOT", tm),
                            ("WORK", tm), ("TASKS", tm / "tasks"),
                            ("APP_SETTING", ""), ("WORK_SETTING", ""),
                            ("EXTERNAL", False)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        (tm / "tasks" / "review" / CARD_FILE).write_text(
            card("56 — A card", status="Review"), encoding="utf-8")

    def test_the_pr_body_still_names_the_tracked_task_path(self):
        task = taskfiles.read_task(
            config.TASKS / "review" / CARD_FILE, "review")
        body = github._pr_body(CARD_FILE, task)
        self.assertIn(".task-manager/tasks/review/", body)
        self.assertNotIn("outside this repository", body)


class BoardIdentity(unittest.TestCase):
    """Startup with external paths: the board serves the external tasks
    root and says so, and it refuses a layout it cannot serve."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-boot-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = self.tmp / "tool"
        shutil.copytree(BENCH / "manager" / "core",
                        self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local").mkdir(parents=True)
        shutil.copy(BENCH / "start.sh", self.tool / "start.sh")
        shutil.copy(BENCH / "install.py", self.tool / "install.py")

        self.app = self.tmp / "app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.app)],
                       check=True, capture_output=True)
        self.work = self.tmp / "work"
        for slug in (*STAGES, "archive"):
            (self.work / "tasks" / slug).mkdir(parents=True)

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        env.update({"BOARD_APP_ROOT": str(self.app),
                    "BOARD_WORK_ROOT": str(self.work)})
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def board(self, port: int, **extra) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, str(self.tool / "manager" / "core" / "board.py"),
             "--port", str(port), "--no-open"],
            env=self.env(**extra), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self._stop, proc)
        return proc

    def _stop(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc.stdout:
            proc.stdout.close()

    def test_the_board_serves_the_external_tasks_root(self):
        port = free_port()
        proc = self.board(port)
        payload = None
        for _ in range(60):
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/state", timeout=1) as r:
                    payload = json.load(r)
                break
            except OSError:
                time.sleep(0.2)
        if proc.poll() is not None:
            self.fail(f"board died:\n{proc.stdout.read() if proc.stdout else ''}")
        self.assertIsNotNone(payload, "the board never answered")
        self.assertEqual(payload["board"]["root"], str(self.work / "tasks"))
        self.assertEqual(payload["layout"]["app"], str(self.app))
        self.assertEqual(payload["layout"]["work"], str(self.work))
        self.assertEqual(payload["project"], "app")

    def test_the_board_binds_loopback_only(self):
        source = (BENCH / "manager" / "core" / "board.py").read_text(
            encoding="utf-8")
        self.assertIn('ThreadingHTTPServer(("127.0.0.1", args.port)', source)
        self.assertNotIn('"0.0.0.0"', source)

    def _answers(self, port: int) -> bool:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/state", timeout=1):
                return True
        except OSError:
            return False

    def test_a_broken_layout_refuses_to_serve(self):
        port = free_port()
        proc = self.board(port, BOARD_WORK_ROOT=str(self.tmp / "nowhere"))
        proc.wait(timeout=30)
        out = proc.stdout.read() if proc.stdout else ""
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("does not exist", out)
        self.assertFalse(self._answers(port), "nothing was served")

    def test_committing_moves_without_a_state_repository_refuses_to_serve(self):
        port = free_port()
        proc = self.board(port, BOARD_COMMIT_MOVES="1")
        proc.wait(timeout=30)
        out = proc.stdout.read() if proc.stdout else ""
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("BOARD_COMMIT_MOVES", out)

    def test_start_sh_refuses_a_broken_layout_instead_of_serving(self):
        """install.py is run with `|| true` on purpose — a project without
        .claude/ still gets a board. That must not swallow a layout which
        cannot be served, or start.sh walks off its port and launches a
        board that refuses anyway."""
        port = free_port()
        result = subprocess.run(
            ["bash", str(self.tool / "start.sh"), "--no-open"],
            env=self.env(BOARD_PORT=port,
                         BOARD_WORK_ROOT=str(self.tmp / "nowhere")),
            capture_output=True, text=True, timeout=120)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot serve", result.stderr)
        self.assertIn("does not exist", result.stderr)
        self.assertFalse(self._answers(port), "nothing was served")

    def test_start_sh_prints_each_real_layout_problem_as_its_own_bullet(self):
        """Two roots that do not exist are two reasons from the real
        config.problems(), and each is a bullet of its own (task 60)."""
        port = free_port()
        result = subprocess.run(
            ["bash", str(self.tool / "start.sh"), "--no-open"],
            env=self.env(BOARD_PORT=port,
                         BOARD_APP_ROOT=str(self.tmp / "no-app"),
                         BOARD_WORK_ROOT=str(self.tmp / "no-work")),
            capture_output=True, text=True, timeout=120)

        self.assertEqual(result.returncode, 2, result.stderr)
        bullets = [line for line in result.stderr.splitlines()
                   if line.startswith("  - ")]
        self.assertGreaterEqual(len(bullets), 2, result.stderr)
        self.assertTrue(any("no-app" in b for b in bullets), result.stderr)
        self.assertTrue(any("no-work" in b for b in bullets), result.stderr)
        self.assertFalse(self._answers(port), "nothing was served")

    def test_a_board_on_the_same_state_but_another_application_is_foreign(self):
        """Two operators can share one work state and manage different
        applications. Neither may reopen — or, through stop.sh, kill —
        the other's board."""
        other = self.tmp / "other-app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(other)],
                       check=True, capture_output=True)
        port = free_port()
        proc = self.board(port)
        self.assertTrue(wait_for(lambda: self._answers(port), 20))

        # start.sh ends in `exec board.py`, so it is read rather than
        # waited on: what matters is the decision it prints before that.
        starting = subprocess.Popen(
            ["bash", str(self.tool / "start.sh"), "--no-open"],
            env=self.env(BOARD_PORT=port, BOARD_APP_ROOT=str(other),
                         BOARD_PORT_WAIT=1),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self._stop, starting)
        said = self._read_until(starting, ("already running", "using "), 60)

        self.assertNotIn("already running", said,
                         "the other operator's board was not adopted as ours")
        self.assertIn("using ", said, "it took a port of its own instead")

    def _read_until(self, proc: subprocess.Popen, marks: tuple[str, ...],
                    timeout: float) -> str:
        """Everything the process has said up to the first line carrying
        one of `marks` — for a script whose last act is to exec a server."""
        collected, deadline = [], time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                continue
            collected.append(line)
            if any(mark in line for mark in marks):
                break
        return "".join(collected)

    def test_stop_sh_leaves_a_board_on_another_application_alone(self):
        if shutil.which("lsof") is None:
            self.skipTest("stop.sh identifies the process with lsof")
        other = self.tmp / "other-app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(other)],
                       check=True, capture_output=True)
        shutil.copy(BENCH / "stop.sh", self.tool / "stop.sh")
        port = free_port()
        proc = self.board(port)
        self.assertTrue(wait_for(lambda: self._answers(port), 20))

        result = subprocess.run(
            ["bash", str(self.tool / "stop.sh")],
            env=self.env(BOARD_PORT=port, BOARD_APP_ROOT=str(other)),
            capture_output=True, text=True, timeout=60)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not this project's board", result.stderr)
        self.assertTrue(self._answers(port), "it is still running")

    def test_stop_sh_stops_the_board_that_is_actually_ours(self):
        if shutil.which("lsof") is None:
            self.skipTest("stop.sh identifies the process with lsof")
        shutil.copy(BENCH / "stop.sh", self.tool / "stop.sh")
        port = free_port()
        proc = self.board(port)
        self.assertTrue(wait_for(lambda: self._answers(port), 20))

        result = subprocess.run(
            ["bash", str(self.tool / "stop.sh")],
            env=self.env(BOARD_PORT=port),
            capture_output=True, text=True, timeout=60)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Board stopped", result.stdout)

    def test_start_sh_finds_the_external_board_it_already_started(self):
        port = free_port()
        proc = self.board(port)
        self.assertTrue(wait_for(lambda: self._answers(port), 20),
                        "the board never came up")

        result = subprocess.run(["bash", str(self.tool / "start.sh"), "--no-open"],
                                env=self.env(BOARD_PORT=port),
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already running", result.stdout)


STUB_CONFIG = """\
import json, os
def problems():
    with open(os.environ["STUB_PROBLEMS"], encoding="utf-8") as f:
        return json.load(f)
"""

STUB_BOARD = """\
import os, sys
with open(os.environ["STUB_SERVED"], "w", encoding="utf-8") as f:
    f.write("\\n".join(sys.argv[1:]))
"""


class StartupProblemsReport(unittest.TestCase):
    """start.sh's refusal, with config.problems() stubbed so the reasons are
    exactly the ones a test chose (task 60). A disposable tool tree: start.sh
    copied in beside a stub installer, a stub config and a stub board that
    records being started instead of serving anything."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-start-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = self.tmp / "tool"
        core = self.tool / "manager" / "core"
        core.mkdir(parents=True)
        (self.tool / "manager" / "local").mkdir()
        shutil.copy(BENCH / "start.sh", self.tool / "start.sh")
        (self.tool / "install.py").write_text("", encoding="utf-8")
        (core / "config.py").write_text(STUB_CONFIG, encoding="utf-8")
        (core / "board.py").write_text(STUB_BOARD, encoding="utf-8")
        self.env_file = self.tool / "manager" / "local" / ".env"
        self.problems = self.tmp / "problems.json"
        self.served = self.tmp / "served"

    def start(self, problems: list[str], port: int) -> subprocess.CompletedProcess:
        self.problems.write_text(json.dumps(problems), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        env.update({"STUB_PROBLEMS": str(self.problems),
                    "STUB_SERVED": str(self.served),
                    "BOARD_PORT_WAIT": "0"})
        self.env_file.write_text(f"BOARD_PORT={port}\n", encoding="utf-8")
        return subprocess.run(["bash", str(self.tool / "start.sh"), "--no-open"],
                              env=env, cwd=self.tmp, capture_output=True,
                              text=True, timeout=60)

    def bullets(self, stderr: str) -> list[str]:
        return [line[4:] for line in stderr.splitlines() if line.startswith("  - ")]

    def test_two_reasons_print_two_bullets_in_order(self):
        reasons = ["BOARD_WORK_ROOT is not a directory: /x/work",
                   "BOARD_APP_ROOT is not a git checkout: /x/app"]
        result = self.start(reasons, free_port())

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.bullets(result.stderr), reasons)
        self.assertFalse(self.served.exists(), "no board was started")

    def test_reasons_with_shell_characters_are_printed_not_run(self):
        canary = self.tmp / "ran"
        reasons = [f"root has spaces  and  $(touch {canary}) inside",
                   f"quotes ' \" and `touch {canary}`; touch {canary} && echo x",
                   "printf directives %s %d %%, a backslash \\n, a glob * ?",
                   "-leading dash | pipe > redirect < in & background"]
        result = self.start(reasons, free_port())

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.bullets(result.stderr), reasons)
        self.assertFalse(canary.exists(), "nothing in a reason was executed")

    def test_a_reason_spanning_lines_stays_one_bullet(self):
        result = self.start(["first line\nsecond line", "next"], free_port())

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("  - first line\n    second line\n  - next\n",
                      result.stderr)

    def test_one_reason_is_one_bullet(self):
        result = self.start(["the only problem"], free_port())

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.bullets(result.stderr), ["the only problem"])
        self.assertIn("cannot serve", result.stderr)

    def test_no_reasons_starts_the_board_without_error_bullets(self):
        port = free_port()
        result = self.start([], port)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.bullets(result.stderr), [])
        self.assertNotIn("cannot serve", result.stderr)
        self.assertEqual(self.served.read_text(encoding="utf-8").split("\n"),
                         ["--port", str(port), "--no-open"])

    def test_refuses_before_walking_off_a_held_port(self):
        """With the port held, a start that got past the refusal would hop
        to another port and rewrite BOARD_PORT. A refused one does neither."""
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]

        result = self.start(["one", "two"], port)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.bullets(result.stderr), ["one", "two"])
        self.assertNotIn("using ", result.stdout)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         f"BOARD_PORT={port}\n")
        self.assertFalse(self.served.exists(), "no board was started")


if __name__ == "__main__":
    unittest.main()
