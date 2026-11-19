"""A run's closing report is filed on the card it was launched for, on the
board it was launched from, or not at all (task 148).

The incident: a test launched a relevance review on a disposable card in
to-do/, waited for the agent's status to leave `running` and returned. The
reaper thread was still between looking up the card's stage and opening
the file, so the test's cleanups restored `config.TASKS` — in a process
that had inherited the operator board's `BOARD_WORK_ROOT` and
`BOARD_COMMIT_MOVES` — and the reaper then opened
`<live>/tasks/to-do/<card>` in append mode, creating a report-only
"card" and committing it as `board: 67 Relevance review filed`.

These cases force that interleaving with barriers, not sleeps: the reaper
is held immediately after it resolves the card's stage, the test swaps the
board's configuration for a **sentinel** board (a work-state repository
standing in for the live one, with commits on), and only then lets the
reaper go. The sentinel must come out byte for byte and commit for commit
what it went in as.
"""

import subprocess
import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_execution_profiles import (CARD, ProfileCase, card, git,  # noqa: E402
                                           wait_for)

REVIEWS = """#!/usr/bin/env python3
print("RELEVANCE REVIEW: Still relevant")
"""

WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
if os.path.exists(os.path.join(cwd, ".git")):
    open(os.path.join(cwd, "done.txt"), "w").write("the work\\n")
    subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
    subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                    "commit", "-q", "-m", "did it"], check=True)
print("WORK REPORT: did it")
"""


class Held:
    """A barrier on the reaper's stage lookup for one card: the reaper
    stops right after it has resolved where the card is, and waits for the
    test to say go. Calls from any other thread, or for another card, pass
    straight through."""

    def __init__(self, case: unittest.TestCase, filename: str):
        self.filename = filename
        self.reached = threading.Event()
        self.go = threading.Event()
        self.found: list = []
        real = agents.find_stage_of
        main = threading.current_thread()

        def held(name, *args, **kwargs):
            stage = real(name, *args, **kwargs)
            if name == filename and threading.current_thread() is not main \
                    and not self.reached.is_set():
                self.found.append(stage)
                self.reached.set()
                if not self.go.wait(30):
                    raise AssertionError("the test never released the reaper")
            return stage

        case.addCleanup(setattr, agents, "find_stage_of", real)
        agents.find_stage_of = held

    def wait(self, case: unittest.TestCase) -> None:
        case.assertTrue(self.reached.wait(30), "the reaper never looked the card up")


class Sentinel:
    """A board standing in for the operator's live one: its own work-state
    repository with every stage, commits on, one committed card. Nothing
    any test here does is allowed to change it."""

    def __init__(self, root: Path):
        self.work = root / "sentinel-work"
        self.tasks = self.work / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
            (self.tasks / slug / ".gitkeep").write_text("", encoding="utf-8")
        (self.tasks / "done" / "67-the-real-one.md").write_text(
            card("67 — The real one", status="Done"), encoding="utf-8")
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "config", "user.email", "s@s")
        git(self.work, "config", "user.name", "sentinel")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "the live board")
        self.before = self.snapshot()

    def snapshot(self) -> dict:
        files = {str(p.relative_to(self.work)): p.read_bytes()
                 for p in sorted(self.tasks.rglob("*")) if p.is_file()}
        head = git(self.work, "rev-parse", "HEAD").stdout.strip()
        count = git(self.work, "rev-list", "--count", "HEAD").stdout.strip()
        status = git(self.work, "status", "--porcelain").stdout
        return {"files": files, "head": head, "commits": count, "status": status}

    def restore_onto(self, case: unittest.TestCase) -> None:
        """What a test's cleanups do to `config` in a process that
        inherited the operator's settings: point every root back at the
        live board, with commits on. The previous values are restored
        afterwards by the case's own cleanups, so the order is the real
        one: this happens first, then the reaper runs."""
        case.addCleanup(setattr, config, "TASKS", config.TASKS)
        case.addCleanup(setattr, config, "EXTERNAL", config.EXTERNAL)
        case.addCleanup(setattr, config, "WORK_SETTING", config.WORK_SETTING)
        case.addCleanup(setattr, config, "COMMIT_MOVES", config.COMMIT_MOVES)
        case.addCleanup(setattr, config, "SYNC", config.SYNC)
        config.TASKS = self.tasks
        config.EXTERNAL = True
        config.WORK_SETTING = str(self.work)
        config.COMMIT_MOVES = True
        config.SYNC = False

    def assert_untouched(self, case: unittest.TestCase) -> None:
        after = self.snapshot()
        escaped = {name: body for name, body in after["files"].items()
                   if self.before["files"].get(name) != body}
        newest = git(self.work, "log", "-1", "--format=%s").stdout.strip()
        case.assertEqual(sorted(after["files"]), sorted(self.before["files"]),
                         f"a file appeared in (or left) the sentinel board: "
                         f"{escaped!r}; newest sentinel commit: {newest!r}")
        case.assertEqual(after, self.before,
                         "the sentinel board changed: a late report escaped "
                         "into a board the run was never launched from")
        log = git(self.work, "log", "--format=%s").stdout
        case.assertNotIn("filed", log)


class LateReportsCannotEscape(ProfileCase):
    """The incident, reproduced: the reaper resolved the card's stage in
    the fixture board, and the board's configuration then changed under
    it."""

    def setUp(self):
        super().setUp()
        self.sentinel = Sentinel(self.tmp)

    def _review_then_restore(self, stage: str = "to-do") -> dict:
        self.adapter_is(REVIEWS)
        if stage != "in-progress":       # the card is in one stage, as in the incident
            (self.tasks / "in-progress" / CARD).unlink()
            self.write(card("67 — Resolve execution profiles", status="To Do"),
                       stage=stage)
        held = Held(self, CARD)
        record = agents.start_review(CARD, stage)
        held.wait(self)
        self.assertEqual(held.found, [stage], "the reaper found the card here")
        # the test returns and its cleanups restore the inherited config
        self.sentinel.restore_onto(self)
        held.go.set()
        self.assertEqual(agents.join_reapers(30), [],
                         "the reaper did not finish")
        return state.AGENTS[record["id"]]

    def test_a_report_held_past_teardown_never_reaches_the_restored_board(self):
        self._review_then_restore()
        self.sentinel.assert_untouched(self)
        self.assertFalse((self.sentinel.tasks / "to-do" / CARD).exists())

    def test_the_refusal_is_narrated_and_the_fixture_card_is_kept_whole(self):
        before = card("67 — Resolve execution profiles", status="To Do")
        self._review_then_restore()
        self.assertEqual((self.tasks / "to-do" / CARD).read_text(encoding="utf-8"),
                         before, "nothing is filed anywhere once the board moved")
        said = " ".join(e.get("summary", "") for e in state.BOARD_EVENTS)
        self.assertIn("was not filed", said)
        self.assertIn(str(self.tasks), said, "the refusal names the run's board")

    def test_a_work_run_s_report_cannot_escape_either(self):
        self.adapter_is(WORKS)
        held = Held(self, CARD)
        agents.start_agent(CARD, "in-progress")
        held.wait(self)
        self.sentinel.restore_onto(self)
        held.go.set()
        self.assertEqual(agents.join_reapers(30), [])
        self.sentinel.assert_untouched(self)


class LateReportsCannotEscapeVendored(LateReportsCannotEscape):
    LAYOUT = "vendored"


class LateReportsCannotEscapeSelfHosted(LateReportsCannotEscape):
    LAYOUT = "flat"


class AMissingCardIsNeverRecreated(ProfileCase):
    """The second half of the escape: append mode creates. A card that
    left its stage between the lookup and the write is not written back
    into existence as a report-only file."""

    def test_a_card_removed_mid_report_is_not_recreated(self):
        self.adapter_is(REVIEWS)
        (self.tasks / "in-progress" / CARD).unlink()
        self.write(card("67 — Resolve execution profiles", status="To Do"),
                   stage="to-do")
        held = Held(self, CARD)
        agents.start_review(CARD, "to-do")
        held.wait(self)
        (self.tasks / "to-do" / CARD).unlink()
        held.go.set()
        self.assertEqual(agents.join_reapers(30), [])
        self.assertFalse((self.tasks / "to-do" / CARD).exists(),
                         "the report recreated a card that had gone")
        said = " ".join(e.get("summary", "") for e in state.BOARD_EVENTS)
        self.assertIn("no longer", said)

    def test_append_refuses_a_missing_target_rather_than_creating_it(self):
        self.assertFalse(taskfiles.append_to_task(
            "99-not-here.md", "to-do", "\n\nreport\n", "Relevance review filed"))
        self.assertFalse((self.tasks / "to-do" / "99-not-here.md").exists())

    def test_append_refuses_a_board_other_than_the_one_named(self):
        other = self.tmp / "elsewhere" / "tasks"
        self.assertFalse(taskfiles.append_to_task(
            CARD, "in-progress", "\n\nreport\n", "Relevance review filed",
            root=other))
        self.assertNotIn("report", (self.tasks / "in-progress" / CARD)
                         .read_text(encoding="utf-8"))


class ValidReportsStillLand(ProfileCase):
    """The ordinary path is unchanged: a report reaches the card where it
    stands now, including after a supported move during the run."""

    def test_a_relevance_review_is_filed_on_the_card(self):
        self.adapter_is(REVIEWS)
        agents.start_review(CARD, "in-progress")
        self.assertEqual(agents.join_reapers(30), [])
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertIn("## Relevance review", text)
        self.assertIn("RELEVANCE REVIEW: Still relevant", text)
        self.assertTrue(text.startswith("# 67 — Resolve execution profiles"))

    def test_a_card_moved_during_the_run_gets_its_report_where_it_now_is(self):
        self.adapter_is(REVIEWS)
        self.write(card("67 — Resolve execution profiles", status="To Do"),
                   stage="to-do")
        (self.tasks / "in-progress" / CARD).unlink()
        gate = threading.Event()
        real = agents.finish

        def finish(*args):
            gate.wait(30)
            return real(*args)

        self.addCleanup(setattr, agents, "finish", real)
        agents.finish = finish
        agents.start_review(CARD, "to-do")
        taskfiles.move_task(CARD, "to-do", "in-progress")
        gate.set()
        self.assertEqual(agents.join_reapers(30), [])
        self.assertFalse((self.tasks / "to-do" / CARD).exists(),
                         "the report recreated the card where it used to be")
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertIn("RELEVANCE REVIEW: Still relevant", text)

    def test_the_fixture_joins_its_reapers_before_config_is_restored(self):
        """ProfileCase.tearDown runs before any cleanup restores config,
        and it joins the reapers this test started: a report a test does
        not wait for still lands in the test's own board, never later."""
        self.adapter_is(REVIEWS)
        record = agents.start_review(CARD, "in-progress")
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        self.tearDown()
        self.assertEqual(agents.join_reapers(0), [],
                         "a reaper outlived the test's tearDown")
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertIn("RELEVANCE REVIEW: Still relevant", text)


class TheSuiteDoesNotInheritABoard(unittest.TestCase):
    """The precondition of the incident: the suite ran in an agent's shell,
    whose environment carries the operator board's roots and settings
    (`config.child_env`). The test package drops them, as `bench-backend`
    does, so a restored config is the checkout's own default."""

    def test_the_layout_and_write_settings_are_not_inherited(self):
        env = {"BOARD_WORK_ROOT": "/live/work", "BOARD_APP_ROOT": "/live/app",
               "BOARD_COMMIT_MOVES": "1", "BOARD_SYNC": "1",
               "AGENT_MODE": "work", "PATH": "/usr/bin", "HOME": "/tmp"}
        code = ("import os, tests; print(sorted(k for k in os.environ "
                "if k.startswith(('BOARD_', 'AGENT_'))))")
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
