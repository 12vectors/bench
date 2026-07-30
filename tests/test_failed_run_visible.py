"""A failed agent run leaves a visible trace (task 11).

Three agents died in an API outage and the board's whole answer was one
ticker line that scrolled away. So: a dead run is recorded on its launch
record (exit code, ended-at, the log's cleaned tail), the person is told
once by toast, the card wears it until the next launch or the next stage,
and the untouched worktree a dead run left behind is cleared so ▸ start
work is one click again.

Real launches through a real (stub) adapter — the adapter contract is what
a dying agent actually comes through, so nothing is mocked but the SSE
fan-out and the adapter's own binary.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
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
import state  # noqa: E402
import watch  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"

FILENAME = "11-a-run-that-dies.md"
STEM = FILENAME[:-3]
BRANCH = f"task/{STEM}"

CARD = """# 11 — A run that dies

**Status:** In Progress
**Priority:** High

Body text, so the prompt has something to carry.
"""

# What the outage looked like from the board's side: a line of output on
# stdout and a non-zero exit.
DIES = """#!/usr/bin/env python3
import sys
print("thinking…")
print("API Error: 500 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"api_error\\"}}")
sys.exit(1)
"""

# Same death, but it committed first: there is work to keep.
DIES_WITH_WORK = """#!/usr/bin/env python3
import os, subprocess, sys
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "half.txt"), "w").write("half a feature\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "half"], check=True)
print("API Error: 529 overloaded")
sys.exit(1)
"""

SILENT_DEATH = """#!/usr/bin/env python3
import sys
sys.exit(1)
"""

LIVES = """#!/usr/bin/env python3
print("all good")
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def wait_for(pred, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class Launches(unittest.TestCase):
    """One repo, one card, one adapter whose behaviour each test writes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-failed-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

        tasks = self.repo / "tasks"
        for slug in config.STAGE_DIRS:
            (tasks / slug).mkdir(parents=True)
        (tasks / "in-progress" / FILENAME).write_text(CARD, encoding="utf-8")

        local = self.tmp / "local"
        (local / "adapters" / config.ADAPTER).mkdir(parents=True)
        self.adapter = local / "adapters" / config.ADAPTER / "run"

        self.patch(REPO=self.repo, TASKS=tasks, LOCAL=local,
                   WORKTREES=self.tmp / "worktrees",
                   AGENT_DIR=self.tmp / "agent",
                   SESSIONS_DIR=self.tmp / "sessions",
                   COMMIT_MOVES=False, SYNC=False)

        state.AGENTS.clear()
        state.BOARD_EVENTS.clear()
        state.EXPECTED_MOVES.clear()
        self.addCleanup(state.AGENTS.clear)
        self.addCleanup(state.BOARD_EVENTS.clear)

        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def adapter_is(self, script: str) -> None:
        self.adapter.write_text(script, encoding="utf-8")
        self.adapter.chmod(self.adapter.stat().st_mode | stat.S_IEXEC)

    def run_agent(self, script: str, start=None) -> dict:
        """Launch, wait for the reaper to be done with it, return the record.

        Every reaper's last act is the agents broadcast, so counting those
        is the honest "it has finished" — the assertions then see a settled
        record rather than one mid-reap."""
        self.adapter_is(script)
        ended = self.sent.count({"type": "agents"})
        public = (start or (lambda: agents.start_agent(FILENAME, "in-progress")))()
        record = state.AGENTS[public["id"]]
        self.assertTrue(
            wait_for(lambda: self.sent.count({"type": "agents"}) > ended),
            f"the reaper never announced the ending (status {record['status']})")
        return record

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def toasts(self) -> list[dict]:
        return [m for m in self.sent if m.get("type") == "toast"]

    def stage_of(self, filename: str = FILENAME) -> str | None:
        for slug in config.STAGE_DIRS:
            if (config.TASKS / slug / filename).is_file():
                return slug
        return None


class TheOutcomeIsRecorded(Launches):
    def test_a_dead_work_run_lands_on_its_record(self):
        record = self.run_agent(DIES)
        self.assertEqual(record["status"], "failed")
        failure = record["failure"]
        self.assertEqual(failure["rc"], 1)
        self.assertIn("API Error: 500", failure["excerpt"])
        self.assertEqual(failure["stage"], "in-progress")
        self.assertGreaterEqual(failure["ended"], record["started"])
        self.assertTrue(Path(failure["log"]).is_file(),
                        "the failure must name a log that exists")

    def test_the_card_never_advances(self):
        self.run_agent(DIES)
        self.assertEqual(self.stage_of(), "in-progress")

    def test_the_person_is_toasted_once(self):
        self.run_agent(DIES)
        toasts = self.toasts()
        self.assertEqual(len(toasts), 1, "a failure is one toast, not none or two")
        self.assertTrue(toasts[0]["error"], "a failure toast is an alarm")
        self.assertIn("API Error: 500", toasts[0]["message"])
        self.assertIn(FILENAME, toasts[0]["message"])

    def test_the_ticker_line_still_records_it(self):
        """This card adds surfaces; it does not move the permanent record."""
        self.run_agent(DIES)
        line = [s for s in self.summaries() if "rc=1" in s]
        self.assertTrue(line, "the event log lost the exit line")
        self.assertIn(FILENAME, line[0])
        self.assertIn("API Error: 500", line[0])

    def test_the_public_payload_carries_it(self):
        """The card reads the API, not the board's memory."""
        self.run_agent(DIES)
        public = agents.list_public()[0]
        self.assertEqual(public["status"], "failed")
        self.assertIn("API Error: 500", public["failure"]["excerpt"])
        self.assertIsNotNone(public["ended"])

    def test_a_live_run_carries_no_failure(self):
        record = self.run_agent(LIVES)
        self.assertEqual(record["status"], "done")
        self.assertIsNone(agents.list_public()[0]["failure"])
        self.assertEqual(self.toasts(), [])


class TheWayIsClearedForRelaunch(Launches):
    def test_an_untouched_worktree_goes(self):
        """Nothing of value is lost — the run committed nothing — and
        ▸ start work refuses while the worktree exists."""
        record = self.run_agent(DIES)
        self.assertFalse(Path(record["worktree"]).exists(),
                         "a dead run with no commits must not block the relaunch")
        self.assertEqual(
            git(self.repo, "rev-parse", "--verify", "--quiet", BRANCH).returncode, 1,
            "the empty branch goes with the worktree")
        self.assertTrue(any("worktree cleared" in s for s in self.summaries()))

    def test_the_relaunch_actually_works(self):
        self.run_agent(DIES)
        state.BOARD_EVENTS.clear()
        second = self.run_agent(LIVES)
        self.assertEqual(second["status"], "done")

    def test_a_run_with_commits_keeps_its_worktree(self):
        record = self.run_agent(DIES_WITH_WORK)
        self.assertTrue(Path(record["worktree"]).exists(),
                        "work that was committed is never thrown away")
        self.assertIn("kept", " ".join(self.summaries()))


class EveryHeadlessKind(Launches):
    def test_a_dead_relevance_check_surfaces_the_same_way(self):
        """No worktree, any stage — same state on the card."""
        record = self.run_agent(
            DIES, start=lambda: agents.start_review(FILENAME, "in-progress"))
        self.assertEqual(record["status"], "failed")
        self.assertIn("API Error: 500", record["failure"]["excerpt"])
        self.assertEqual(record["failure"]["stage"], "in-progress")
        self.assertEqual(len(self.toasts()), 1)

    def test_a_death_before_the_agent_spoke_still_says_something(self):
        record = self.run_agent(SILENT_DEATH)
        self.assertIn("no output", record["failure"]["excerpt"])
        self.assertIn("no output", self.toasts()[0]["message"])


class TheStateClears(Launches):
    def test_a_relaunch_replaces_it(self):
        """Two records for one card, and the card reads the newest: the
        failure is superseded rather than cleared."""
        first = self.run_agent(DIES)
        time.sleep(1.1)          # agent ids are stamped to the second
        second = self.run_agent(LIVES)
        self.assertNotEqual(first["id"], second["id"])
        latest = max(agents.list_public(), key=lambda a: a["started"])
        self.assertEqual(latest["id"], second["id"])
        self.assertIsNone(latest["failure"])
        self.assertIsNotNone(first["failure"],
                             "the older run keeps its own history")

    def test_a_stage_move_drops_it(self):
        record = self.run_agent(DIES)
        self.assertIsNotNone(record["failure"])
        watch.narrate({"in-progress": {FILENAME}, "to-do": set()},
                      {"in-progress": set(), "to-do": {FILENAME}})
        self.assertIsNone(record.get("failure"),
                          "a card arriving in a new stage wears no old alarm")

    def test_forgetting_is_per_card(self):
        record = self.run_agent(DIES)
        self.assertFalse(agents.forget_failure("99-someone-else.md"))
        self.assertIsNotNone(record["failure"], "another card's move cleared this one")
        self.assertTrue(agents.forget_failure(FILENAME))
        self.assertFalse(agents.forget_failure(FILENAME), "clearing twice is a no-op")


class ExcerptTests(unittest.TestCase):
    """What the card shows, from the log alone."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-excerpt-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def log(self, text: str) -> str:
        path = self.tmp / "run.log"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_the_tail_is_what_you_get(self):
        excerpt = agents._failure_excerpt(
            self.log("\n".join(f"line {i}" for i in range(40))), lines=6)
        self.assertIn("line 39", excerpt)
        self.assertNotIn("line 20", excerpt)
        self.assertEqual(len(excerpt.splitlines()), 6)

    def test_a_tiny_log_survives_whole(self):
        """The MultiEdit flag error was 91 bytes; the excerpt handles it."""
        tiny = "error: unknown option '--allowedTools MultiEdit'\n"
        self.assertIn("MultiEdit", agents._failure_excerpt(self.log(tiny)))

    def test_hook_noise_is_stripped(self):
        excerpt = agents._failure_excerpt(
            self.log("PostToolUse hook failed with status 1\nAPI Error: 500\n"))
        self.assertEqual(excerpt, "API Error: 500")

    def test_nothing_at_all_says_so(self):
        for empty in (self.log(""), self.log("   \n\n"), str(self.tmp / "gone.log"), None):
            self.assertIn("no output", agents._failure_excerpt(empty))

    def test_the_headline_is_the_last_line(self):
        """A dying process says why last."""
        self.assertEqual(agents._headline("thinking…\nAPI Error: 500"), "API Error: 500")
        self.assertEqual(agents._headline(""), "no output")
        self.assertLessEqual(len(agents._headline("x" * 400)), 120)


class TheCardWearsIt(unittest.TestCase):
    """board.html is one file with inline JS and no frontend test runner, so
    these are source-level invariants — the ones that, if broken, put the
    failure back out of sight."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def rule(self, selector: str) -> str:
        m = re.search(re.escape(selector) + r"\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, f"board.html lost its {selector} rule")
        return m.group(1)

    def test_the_border_is_the_alarm_colour(self):
        self.assertIn("--alarm", self.rule(".card.run-failed"),
                      "failed is terracotta — the design system's one word for it")

    def test_the_pill_says_run_failed(self):
        m = re.search(r"\{ text: 'run failed'[^}]*\}", self.html)
        self.assertIsNotNone(m, "the status slot lost its `run failed` pill")
        self.assertIn("--alarm", m.group(0))

    def test_the_failure_is_scoped_to_its_card_and_stage(self):
        """The state belongs to the most recent run on THIS card, in the
        stage it died in: no leaking sideways, none into review/."""
        m = re.search(r"function failedRun\(task\) \{(.*?)\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(m, "failedRun went missing")
        body = m.group(1)
        self.assertIn("lastRunOn(task.file)", body)
        self.assertIn("'failed'", body)
        self.assertIn("failure.stage === task.stage", body)

    def test_the_latest_run_is_a_max_not_a_find(self):
        """Records outlive their processes; picking the first match would
        pin a card to whichever run happens to be first in the list."""
        m = re.search(r"function lastRunOn\(file\) \{(.*?)\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(m, "lastRunOn went missing")
        self.assertIn("started >", m.group(1))

    def test_the_excerpt_is_one_hover_away(self):
        """On the card: the alarm well, the line it died on, the whole
        excerpt in the tooltip."""
        m = re.search(r'<div class="well bad" title="\$\{esc\(failure\.excerpt\)\}"',
                      self.html)
        self.assertIsNotNone(m, "the card's failure well lost its excerpt tooltip")
        self.assertIn("whyFailed(failure)", self.html)

    def test_the_card_sheet_shows_the_whole_excerpt(self):
        self.assertRegex(self.html, r"<pre>\$\{esc\(failure\.excerpt\)\}</pre>",
                         "the drawer must show the excerpt without opening files")
        self.assertRegex(self.html, r"#drawer \.well\.bad pre\{[^}]*max-height",
                         "a long excerpt needs a scroll bound in the sheet")

    def test_the_server_can_toast(self):
        """The failure toast rides the generic server-toast channel."""
        self.assertIn("msg.type === 'toast'", self.html)


if __name__ == "__main__":
    unittest.main()
