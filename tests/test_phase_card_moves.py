"""A phase card will not move while its work is running (task 58).

The phase card stands for members the Board no longer draws, so moving it
mid-run — to another stage, or out to `tasks/archive/`, which is a move
like any other — leaves the card in one place and a live agent, a worktree
and a branch in another. `phases.assert_not_working` is the refusal, and
these cases are about its edges as much as its middle: a phase between
members moves, a halted one moves, an ordinary card never reaches the
question, and a run that died but has not been reaped does not lock its
phase card for the life of the board.

The guard is the server's, so the last case here goes through a real
`httpd.Handler` on a real socket: that is the path a stale page takes.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import httpd  # noqa: E402
import phases  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_phase_runs import PhaseCase, wait_for  # noqa: E402

# An agent that is still working when the question is asked.
SLEEPS = """#!/usr/bin/env python3
import time
time.sleep(60)
"""

PHASE = "40-ship-the-site.md"
ONE = "31-stand-up-site.md"
TWO = "32-serve-it.md"
PLAIN = "33-landing.md"

HOLD = "‖ hold"


class FakeProc:
    """A launched process, as the guard asks after it: alive while poll()
    says None, gone the moment it says anything else — which is what the
    reaper is about to notice too."""

    def __init__(self, alive: bool = True):
        self.rc = None if alive else 0

    def poll(self):
        return self.rc

    def died(self) -> None:
        self.rc = 1


def card(title: str, *, status: str = "Backlog", kind: str | None = None,
         cards: str | None = None, log: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    text += "\nWhat this card is for, at enough length to be a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    if log is not None:
        text += f"\n## Phase log\n\n{log}\n"
    return text


RUNNING = "- 2026-08-02 09:00 · run started on phase/40-ship-the-site\n"
HALTED = (RUNNING + "- 2026-08-02 09:05 · 31 started\n"
          "- 2026-08-02 09:20 · halted at 31 — its CI is red\n")
HELD = (RUNNING + "- 2026-08-02 09:05 · 31 started\n"
        "- 2026-08-02 09:20 · stopped — held by tester\n")


class PhaseCardCase(unittest.TestCase):
    """A phase in in-progress/ listing two members, and an agent registry
    this test writes by hand — the guard reads the registry and the disk,
    and both are cheaper to state than to stage."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-phase-move-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)

        self.tasks = self.repo / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.repo,
                   WORKTREES=self.tmp / "worktrees", COMMIT_MOVES=False, SYNC=False)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE, phases.SNAPSHOTS):
            registry.clear()
            self.addCleanup(registry.clear)
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda message: None

        self.write(ONE, card("31 — Stand up site/", status="In Progress"),
                   "in-progress")
        self.write(TWO, card("32 — Serve it"))
        self.write(PLAIN, card("33 — The landing page"))
        self.phase_log(RUNNING)

    # — the world —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def phase_log(self, log: str, stage: str = "in-progress") -> None:
        """Rewrite the phase card with the log a run would have left."""
        for slug in config.STAGE_DIRS:
            (self.tasks / slug / PHASE).unlink(missing_ok=True)
        self.write(PHASE, card("40 — Ship the site", status="In Progress",
                               kind="Phase",
                               cards="- 31 — Stand up site/\n- 32 — Serve it\n",
                               log=log), stage)

    def stage_of(self, filename: str) -> str | None:
        for slug in (*config.STAGE_DIRS, "archive"):
            if (self.tasks / slug / filename).is_file():
                return slug
        return None

    # — the registry —

    def working(self, filename: str, name: str = "Wren",
                alive: bool = True, status: str = "running") -> FakeProc:
        proc = FakeProc(alive)
        agent_id = f"a{len(state.AGENTS) + 1}"
        with state.LOCK:
            state.AGENTS[agent_id] = {
                "id": agent_id, "task": filename, "status": status,
                "name": name, "proc": proc, "mode": "work",
                "started": time.time(), "branch": None, "worktree": None,
                "rc": None, "session": None}
        return proc

    # — the question —

    def refusal(self, filename: str = PHASE, doing: str = "move the card") -> str:
        with self.assertRaises(ValueError) as caught:
            phases.assert_not_working(filename, doing)
        return str(caught.exception)


class TheRefusal(PhaseCardCase):

    def test_a_move_is_refused_while_a_member_has_an_agent(self):
        self.working(ONE)
        reason = self.refusal()
        self.assertIn("31 — Stand up site/", reason)
        self.assertIn(HOLD, reason)
        self.assertIn("Hold it first, then move the card.", reason)

    def test_it_names_who_is_working_and_what_hold_leaves_alone(self):
        """A refusal that only says no makes the person guess, and the
        guess is usually to force it."""
        self.working(ONE, name="Juno")
        reason = self.refusal()
        self.assertIn("Juno is on 31 — Stand up site/", reason)
        self.assertIn("Ship the site", reason)          # which phase
        self.assertIn("worktree", reason)               # what is left as it was
        self.assertIn("phase branch", reason)

    def test_the_whole_refusal_verbatim(self):
        """The board's first real "no" on a drag, so the wording is the
        feature: an instruction, not a wall. Pinned in full here — if this
        has to change, it should be because someone meant it to."""
        self.working(ONE, name="Juno")
        self.assertEqual(
            self.refusal(),
            "Ship the site is still working — Juno is on 31 — Stand up site/. "
            "‖ hold stops the phase and the agent it has in flight, and leaves "
            "the phase branch, everything merged into it and every worktree "
            "exactly as they are. Hold it first, then move the card.")

    def test_every_working_member_is_named(self):
        """Members run one at a time, but a card started by hand under a
        running phase is exactly the state that needs saying in full."""
        self.working(ONE, name="Juno")
        self.working(TWO, name="Basil")
        reason = self.refusal()
        self.assertIn("Juno is on 31 — Stand up site/", reason)
        self.assertIn("Basil is on 32 — Serve it", reason)

    def test_an_unnamed_run_still_reads_as_a_sentence(self):
        """A board restarted mid-run forgets the names it handed out."""
        self.working(ONE, name=None)
        self.assertIn("an agent is on 31 — Stand up site/", self.refusal())

    def test_archiving_takes_the_same_guard(self):
        """A phase you have given up on is precisely the one you would
        archive, and doing it mid-run leaves a branch, a worktree and a
        running agent belonging to a card that is no longer on the board."""
        self.working(ONE)
        reason = self.refusal(doing="archive it")
        self.assertIn("31 — Stand up site/", reason)
        self.assertIn(HOLD, reason)
        self.assertIn("Hold it first, then archive it.", reason)


class WhatStillMoves(PhaseCardCase):
    """The rule is narrow on purpose: refusing a phase that has nothing in
    flight is a rule people learn to resent."""

    def test_a_phase_between_members_moves(self):
        """Running, nothing launched — there is nothing to lose."""
        self.assertIsNone(phases.assert_not_working(PHASE))
        taskfiles.move_task(PHASE, "in-progress", "to-do")
        self.assertEqual(self.stage_of(PHASE), "to-do")

    def test_a_held_phase_moves_and_archives(self):
        """`‖ hold` is what the refusal asks for, so the card has to move
        the moment it has been used — the run stopped, the agent with it."""
        proc = self.working(ONE)
        self.refusal()                       # refused while it runs
        with state.LOCK:                     # what stop_agent leaves behind
            for record in state.AGENTS.values():
                record["status"] = "stopped"
        proc.died()
        self.phase_log(HELD)
        self.assertIsNone(phases.assert_not_working(PHASE))
        taskfiles.move_task(PHASE, "in-progress", "to-do")
        taskfiles.archive_task(PHASE, "to-do")
        self.assertEqual(self.stage_of(PHASE), "archive")

    def test_a_halted_phase_moves(self):
        """A halt has nothing running by construction — and walking the
        card back is exactly what you would want to do then."""
        self.phase_log(HALTED)
        self.assertIsNone(phases.assert_not_working(PHASE))
        taskfiles.move_task(PHASE, "in-progress", "to-do")
        self.assertEqual(self.stage_of(PHASE), "to-do")

    def test_a_run_that_died_unreaped_does_not_lock_the_card(self):
        """The registry's status is flipped by the reaper, a moment after
        the process ends. The guard reads what is actually running."""
        proc = self.working(ONE)
        self.refusal()
        proc.died()                          # the process is gone…
        with state.LOCK:                     # …and the record still says running
            self.assertEqual([r["status"] for r in state.AGENTS.values()],
                             ["running"])
        self.assertIsNone(phases.assert_not_working(PHASE))

    def test_a_record_with_no_process_is_taken_at_its_word(self):
        """Nothing to ask means nothing to second-guess."""
        with state.LOCK:
            state.AGENTS["x"] = {"id": "x", "task": ONE, "status": "running",
                                 "name": "Wren", "mode": "work",
                                 "started": time.time()}
        self.assertIn("Wren is on 31", self.refusal())


class OrdinaryCards(PhaseCardCase):
    """An ordinary card is unaffected in every case."""

    def test_a_plain_card_moves_with_its_own_agent_running(self):
        self.working(PLAIN)
        self.assertIsNone(phases.assert_not_working(PLAIN))
        taskfiles.move_task(PLAIN, "backlog", "to-do")
        self.assertEqual(self.stage_of(PLAIN), "to-do")

    def test_a_member_card_still_moves(self):
        """Moving a member is a person overriding the runner deliberately,
        and the phase halting on it afterwards is the honest outcome."""
        self.working(ONE)
        self.assertIsNone(phases.assert_not_working(ONE))
        taskfiles.move_task(ONE, "in-progress", "to-do")
        self.assertEqual(self.stage_of(ONE), "to-do")

    def test_an_agent_on_the_phase_card_itself_is_not_the_rule(self):
        """This card is about the members' work. A run on the phase card —
        a relevance check, a PR fix — is an ordinary card's business, and
        ordinary cards move."""
        self.working(PHASE)
        self.assertIsNone(phases.assert_not_working(PHASE))

    def test_a_card_that_has_left_the_board_asks_nothing(self):
        self.assertIsNone(phases.assert_not_working("99-never-existed.md"))

    def test_a_phase_that_lists_nobody_asks_nothing(self):
        self.write("41-empty.md", card("41 — An empty phase", status="To Do",
                                       kind="Phase", cards=""), "to-do")
        self.working(ONE)
        self.assertIsNone(phases.assert_not_working("41-empty.md"))


class ThroughTheServer(PhaseCardCase):
    """The board can be stale and `/api/move` is reachable regardless, so
    the guard lives behind the API rather than in the drag."""

    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_post_move_is_refused_with_the_same_reason(self):
        self.working(ONE, name="Wren")
        status, body = self.post("/api/move", {"file": PHASE,
                                               "from": "in-progress",
                                               "to": "to-do"})
        self.assertEqual(status, 409)
        self.assertIn("Wren is on 31 — Stand up site/", body["error"])
        self.assertIn(HOLD, body["error"])
        # the card is exactly where it was: refused, not half-moved
        self.assertEqual(self.stage_of(PHASE), "in-progress")

    def test_post_archive_is_refused_with_the_same_reason(self):
        self.phase_log(RUNNING, stage="to-do")
        self.working(ONE)
        status, body = self.post("/api/archive", {"file": PHASE, "from": "to-do"})
        self.assertEqual(status, 409)
        self.assertIn(HOLD, body["error"])
        self.assertIn("then archive it.", body["error"])
        self.assertEqual(self.stage_of(PHASE), "to-do")

    def test_the_move_goes_through_once_nothing_is_running(self):
        status, body = self.post("/api/move", {"file": PHASE,
                                               "from": "in-progress",
                                               "to": "to-do"})
        self.assertEqual(status, 200)
        self.assertEqual(body["task"]["stage"], "to-do")
        self.assertEqual(self.stage_of(PHASE), "to-do")

    def test_an_ordinary_card_moves_through_the_api_regardless(self):
        self.working(ONE)
        status, _ = self.post("/api/move", {"file": PLAIN, "from": "backlog",
                                            "to": "to-do"})
        self.assertEqual(status, 200)
        self.assertEqual(self.stage_of(PLAIN), "to-do")


class TheGuardIsOnEveryDoorOut(unittest.TestCase):
    """Source-level: the three routes that take a card out of the stage it
    is in all ask, and they ask before anything is written."""

    def setUp(self):
        self.source = (REPO / "manager" / "core" / "httpd.py").read_text(
            encoding="utf-8")

    def test_move_archive_and_complete_all_ask(self):
        for route, call in (("/api/move", "phases.assert_not_working(payload[\"file\"])"),
                            ("/api/archive", "phases.assert_not_working(payload[\"file\"], \"archive it\")"),
                            ("/api/task/complete", "phases.assert_not_working(payload[\"file\"], \"merge it\")")):
            with self.subTest(route=route):
                self.assertIn(call, self.source)

    def test_the_guard_runs_before_the_write(self):
        for guard, write in (("phases.assert_not_working(payload[\"file\"])",
                              "taskfiles.move_task("),
                             ("phases.assert_not_working(payload[\"file\"], \"archive it\")",
                              "taskfiles.archive_task("),
                             ("phases.assert_not_working(payload[\"file\"], \"merge it\")",
                              "github.complete_task(")):
            with self.subTest(write=write):
                self.assertLess(self.source.index(guard), self.source.index(write))


class TheToastCanBeRead(unittest.TestCase):
    """A refusal names what to do instead, which makes it the longest thing
    the toast ever says. A message that runs off the screen, or leaves
    before it can be read, is the wall this card was written against."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")

    def test_the_toast_is_bounded_and_wraps(self):
        self.assertIn("max-width:min(620px, calc(100vw - 48px))", self.html)
        self.assertIn("#toast.wrapped{", self.html)

    def test_it_stays_up_for_as_long_as_it_takes_to_read(self):
        self.assertIn("Math.min(9000, 3200 + message.length * 28)", self.html)

    def test_a_refused_move_puts_the_reason_in_the_toast(self):
        """rawMove's failure path: the server's own words, and a reload so
        the card is drawn where it actually is."""
        raw = self.html[self.html.index("async function rawMove"):]
        raw = raw[:raw.index("\n}")]
        self.assertIn("toast(data.error || 'move failed', true)", raw)
        self.assertIn("await loadState()", raw)


class AgainstARealRun(PhaseCase):
    """The registry is cheaper to state by hand than to stage, so one case
    stages it: a phase started for real, its first member launched through
    the adapter and still working, and `‖ hold` as the way out."""

    def test_the_card_is_held_still_until_the_phase_is(self):
        self.adapter_is(SLEEPS)
        phases.start_phase(PHASE, "in-progress")
        self.assertTrue(wait_for(lambda: any(
            record["task"] == ONE and record["status"] == "running"
            for record in state.AGENTS.values())),
            "the phase never launched its first member")

        with self.assertRaises(ValueError) as caught:
            phases.assert_not_working(PHASE)
        self.assertIn("31 — Stand up site/", str(caught.exception))
        self.assertIn(HOLD, str(caught.exception))
        self.assertEqual(self.stage_of(PHASE), "in-progress")

        phases.stop_phase(PHASE, "in-progress")

        self.assertIsNone(phases.assert_not_working(PHASE),
                          "a held phase moves — that is what the refusal asked for")
        taskfiles.move_task(PHASE, "in-progress", "to-do")
        self.assertEqual(self.stage_of(PHASE), "to-do")


if __name__ == "__main__":
    unittest.main()
