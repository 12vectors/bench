"""The board shows a phase running, and shows it stopping (task 50).

Card 49 built the runner; this one is the interface it does not inherit
from being a card — an action to start and stop it, a header chip while it
runs, and the same chip in `--alarm` when it halts.

Two halves, tested the way each can be: the runner's own half (a hold that
records itself, a run-state read back off the phase log, the toast a halt
fires) runs for real on a real repo through `tests.test_phase_runs`'
harness; the page's half is source-level invariants over board.html, with
its pure functions lifted out and run under node — board.html has no test
runner, and a chip that quietly shows one of two running phases is exactly
the kind of thing that would otherwise reach a person at 2am.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import phases  # noqa: E402
import state  # noqa: E402

from tests.test_phase_runs import (DIES, ONE, PHASE, PHASE_BRANCH, TWO,  # noqa: E402
                                   PhaseCase, card, wait_for)

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")

# An agent that is still working when the hold arrives.
SLEEPS = """#!/usr/bin/env python3
import time
time.sleep(60)
"""


class HoldingAPhase(PhaseCase):
    """`‖ hold` means here what it means everywhere else: the work stops
    and nothing is lost."""

    def running_agents(self) -> list[dict]:
        return [r for r in state.AGENTS.values() if r["status"] == "running"]

    def test_the_hold_is_recorded_where_the_beat_reads_it(self):
        self.start()

        snapshot = phases.stop_phase(PHASE, "in-progress")

        self.assertTrue(any(e.startswith("stopped — held by") for e in self.log()))
        self.assertFalse(snapshot["running"])
        self.assertTrue(snapshot["stopped"])
        self.assertTrue(any("held by" in s for s in self.summaries()))

    def test_the_beat_stands_down_afterwards(self):
        self.start()
        phases.stop_phase(PHASE, "in-progress")
        state.BOARD_EVENTS.clear()

        snapshot = self.advance()

        self.assertEqual(self.summaries(), [], "a held phase is quiet")
        self.assertEqual(self.merges_on(PHASE_BRANCH), [],
                         "and merges nothing more")
        self.assertFalse(self.branch_exists(f"task/{TWO[:-3]}"),
                         "and launches nothing more")
        self.assertTrue(snapshot["stopped"])

    def test_the_phase_branch_and_what_landed_on_it_are_untouched(self):
        self.start()
        self.advance()                      # 31 merges, 32 runs
        merges, tip = self.merges_on(PHASE_BRANCH), self.tip(PHASE_BRANCH)

        phases.stop_phase(PHASE, "in-progress")

        self.assertEqual(self.merges_on(PHASE_BRANCH), merges)
        self.assertEqual(self.tip(PHASE_BRANCH), tip, "a hold is not an undo")
        self.assertTrue(self.branch_exists(f"task/{ONE[:-3]}"))
        self.assertTrue((config.WORKTREES / PHASE[:-3]).is_dir())

    def test_the_member_in_flight_is_held_with_it(self):
        """A phase that said it had stopped while the agent it launched
        kept working would be lying about the one thing it was asked."""
        self.adapter_is(SLEEPS)
        phases.start_phase(PHASE, "in-progress")
        self.assertTrue(wait_for(lambda: any(r["task"] == ONE for r in
                                             self.running_agents())),
                        "the phase never launched its first member")

        phases.stop_phase(PHASE, "in-progress")

        self.assertTrue(wait_for(lambda: not self.running_agents()),
                        "the member's agent was left running")
        self.assertTrue(any(ONE in s and "stopped with it" in s
                            for s in self.summaries()),
                        "and the ticker says which cards stopped with it")

    def test_running_it_again_after_a_hold_carries_on(self):
        self.start()
        phases.stop_phase(PHASE, "in-progress")

        phases.start_phase(PHASE, "in-progress")
        self.settle()

        self.assertIn(f"31 merged into {PHASE_BRANCH}", self.log())
        self.assertTrue(self.branch_exists(f"task/{TWO[:-3]}"),
                        "the next member started")

    def test_a_halted_phase_can_be_held_rather_than_run_again(self):
        """The halt holds until the phase is resumed *or* stopped: someone
        who has read it and does not want to carry on says so here."""
        self.adapter_is(DIES)
        self.start()
        self.advance()
        self.assertIsNotNone(self.advance()["halted"])

        snapshot = phases.stop_phase(PHASE, "in-progress")

        self.assertIsNone(snapshot["halted"], "the alarm is settled")
        self.assertTrue(snapshot["stopped"])

    def test_holding_a_phase_that_never_ran_refuses(self):
        with self.assertRaises(ValueError) as caught:
            phases.stop_phase(PHASE, "in-progress")

        self.assertIn("not running", str(caught.exception))

    def test_holding_it_twice_refuses(self):
        self.start()
        phases.stop_phase(PHASE, "in-progress")

        with self.assertRaises(ValueError) as caught:
            phases.stop_phase(PHASE, "in-progress")

        self.assertIn("not running", str(caught.exception))

    def test_an_ordinary_card_is_not_held_as_a_phase(self):
        self.write(ONE, card("31 — Stand up site/", status="In Progress"),
                   "in-progress")
        (self.tasks / "backlog" / ONE).unlink()

        with self.assertRaises(ValueError) as caught:
            phases.stop_phase(ONE, "in-progress")

        self.assertIn("not a phase", str(caught.exception))


class WhatTheChipIsToldFrom(PhaseCase):
    """The snapshot the API carries has to answer the chip's questions —
    which phase, how far, on which card, and what stopped it — without the
    page inferring any of them."""

    def test_a_running_phase_names_itself_and_where_it_is(self):
        self.adapter_is(SLEEPS)          # so the member is still in flight
        phases.start_phase(PHASE, "in-progress")
        self.addCleanup(phases.stop_phase, PHASE, "in-progress")

        snapshot = phases.public_state()[PHASE]

        self.assertTrue(snapshot["running"])
        self.assertIsNone(snapshot["halted"])
        self.assertEqual(snapshot["title"], "40 — Ship the site")
        self.assertEqual(snapshot["number"], "40")
        self.assertEqual(snapshot["branch"], PHASE_BRANCH)
        self.assertEqual([m["number"] for m in snapshot["members"]], ["31", "32"])
        self.assertEqual([m["state"] for m in snapshot["members"]],
                         ["running", "pending"])

    def test_a_halt_arrives_in_its_parts(self):
        self.adapter_is(DIES)
        self.start()

        snapshot = self.advance()

        self.assertEqual(snapshot["haltedAt"], "31")
        self.assertIn("without reaching review/", snapshot["haltedWhy"])
        self.assertFalse(snapshot["running"])
        # the one line the log's reader gets, still said the old way
        self.assertTrue(snapshot["halted"].startswith("31: "))

    def test_a_halt_is_a_toast_as_well_as_a_state_and_a_line(self):
        """Rare and actionable: the person who is not watching the ticker
        is told once, the way a dead run tells them."""
        self.adapter_is(DIES)
        self.start()
        self.sent.clear()

        phases.advance_all()             # the pass halts; it launches nothing

        toasts = [m for m in self.sent if m.get("type") == "toast"]
        self.assertEqual(len(toasts), 1, "said once, not once a beat")
        self.assertTrue(toasts[0]["error"])
        self.assertIn(PHASE, toasts[0]["message"])
        self.assertIn("halted at 31", toasts[0]["message"])

    def test_a_second_beat_repeats_neither_the_toast_nor_the_line(self):
        self.adapter_is(DIES)
        self.start()
        self.advance()
        self.sent.clear()

        phases.advance_all()

        self.assertEqual([m for m in self.sent if m.get("type") == "toast"], [])

    def test_two_phases_are_two_snapshots(self):
        """Two phases could in principle run on one board — the API says so
        rather than leaving the header to show one of them."""
        self.write("41-second-phase.md",
                   card("41 — A second phase", status="In Progress", kind="Phase",
                        cards="- 77 — On its own\n"), "in-progress")
        self.write("77-alone.md", card("77 — On its own"))
        self.start()
        phases.start_phase("41-second-phase.md", "in-progress")
        self.settle()

        live = phases.public_state()

        self.assertEqual(sorted(live), [PHASE, "41-second-phase.md"])
        self.assertTrue(all(p["running"] for p in live.values()))

    def test_a_phase_out_of_in_progress_leaves_the_header(self):
        self.start()
        self.advance()
        self.advance()          # every member merged: the card moves to review/
        self.advance()          # the next beat, with nothing in in-progress/

        self.assertEqual(self.stage_of(PHASE), "review")
        self.assertEqual(phases.public_state(), {},
                         "nothing is in flight, so there is nothing to show")


class EveryAdvanceIsNarrated(PhaseCase):
    """A phase that advances silently is a phase nobody can debug
    afterwards: what finished, what merged, what started."""

    def test_the_ticker_names_all_three(self):
        self.start()
        state.BOARD_EVENTS.clear()

        self.advance()

        said = " | ".join(self.summaries())
        self.assertIn("31 — 31 — Stand up site/ is green", said)
        self.assertIn(f"merged 31 into {PHASE_BRANCH}", said)
        self.assertIn("started 32", said)

    def test_the_events_are_phase_events(self):
        self.start()

        mine = [e for e in state.BOARD_EVENTS if e["file"] == PHASE]

        self.assertTrue(mine, "the phase narrated nothing at all")
        self.assertEqual({e["kind"] for e in mine}, {"phase"},
                         "the runner's narration is its own kind of event")


class TheLogSaysWhereAPhaseIs(unittest.TestCase):
    """`run_state` is the whole of a restarted board's memory of a run, and
    now of the header's too. The last line that says something wins."""

    def entries(self, *lines: str) -> list[str]:
        body = "\n".join(f"- 2026-08-01 09:0{i} · {line}"
                         for i, line in enumerate(lines))
        return phases.log_entries(f"# 40\n\n## Phase log\n\n{body}\n")

    def state_of(self, *lines: str) -> str:
        return phases.run_state(self.entries(*lines))["state"]

    def test_a_card_that_has_never_run_is_idle(self):
        self.assertEqual(phases.run_state([])["state"], "idle")

    def test_a_started_run_is_running(self):
        self.assertEqual(self.state_of("run started on phase/40-x", "31 started"),
                         "running")

    def test_a_halt_stops_it(self):
        where = phases.run_state(self.entries("run started on phase/40-x",
                                              "halted at 31 — its CI is red"))
        self.assertEqual(where["state"], "halted")
        self.assertEqual(where["at"], "31")
        self.assertEqual(where["reason"], "its CI is red")

    def test_a_hold_stops_it(self):
        where = phases.run_state(self.entries("run started on phase/40-x",
                                              "stopped — held by ronald"))
        self.assertEqual(where["state"], "stopped")
        self.assertEqual(where["reason"], "held by ronald")

    def test_a_hold_after_a_halt_settles_it(self):
        self.assertEqual(self.state_of("halted at 31 — its CI is red",
                                       "stopped — held by ronald"), "stopped")
        self.assertIsNone(phases._halt_reason(
            self.entries("halted at 31 — its CI is red", "stopped — held by ronald")))

    def test_running_it_again_clears_either(self):
        self.assertEqual(self.state_of("stopped — held by ronald",
                                       "run started on phase/40-x"), "running")
        self.assertEqual(self.state_of("halted at 31 — its CI is red",
                                       "run started on phase/40-x"), "running")


class TheHeaderChip(unittest.TestCase):
    """board.html has no test runner, so these are source-level invariants:
    the chip exists, it is absent when there is nothing to say, it wears
    the design system's colours and it is one chip per phase."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def func(self, name: str) -> str:
        match = re.search(rf"^function {name}\(.*?\n\}}\n", self.html,
                          re.M | re.S)
        self.assertIsNotNone(match, f"board.html lost its {name}()")
        return match.group(0)

    def test_the_chip_has_a_home_beside_the_agents_chip(self):
        header = re.search(r"<header>.*?</header>", self.html, re.S).group(0)
        self.assertIn('id="phasechips"', header)
        self.assertLess(header.index('id="phasechips"'), header.index('id="livechip"'),
                        "the phase chip sits with the header's other live chips")

    def test_it_is_rendered_on_every_frame(self):
        self.assertIn("renderPhases();", self.func("render"))

    def test_nothing_running_is_no_chip_at_all(self):
        body = self.func("renderPhases")
        self.assertIn("el.hidden = !live.length", body,
                      "an empty chip or a placeholder is not the answer")

    def test_only_a_run_or_a_halt_is_in_flight(self):
        self.assertIn("p.running || p.halted", self.func("phasesInFlight"))

    def test_a_halt_leads(self):
        self.assertIn("sort(", self.func("phasesInFlight"))
        self.assertIn("b.halted", self.func("phasesInFlight"))

    def test_every_phase_in_flight_gets_its_own_chip(self):
        body = self.func("renderPhases")
        self.assertIn("live.map(", body,
                      "one chip per phase — never one of two, silently")

    def test_the_colours_mean_what_they_mean(self):
        body = self.func("renderPhases")
        self.assertIn("dot live", body, "a run alive breathes in --accent")
        self.assertIn("var(--alarm)", body, "a halt holds in --alarm")

    def test_the_chip_opens_the_phase_card(self):
        body = self.func("renderPhases")
        self.assertIn("data-phasechip", body)
        self.assertIn("showDetail(task)", body)

    def test_the_page_still_parses(self):
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", self.html, re.S)
        self.assertTrue(scripts, "board.html has no inline script")
        if not NODE:
            self.skipTest("node is needed to parse the page")
        with tempfile.TemporaryDirectory() as tmp:
            for index, script in enumerate(scripts):
                source = Path(tmp) / f"page-{index}.js"
                source.write_text(script, encoding="utf-8")
                out = subprocess.run([NODE, "--check", str(source)],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)


@unittest.skipUnless(NODE, "node is needed to run the page's own functions")
class WhatTheChipSays(unittest.TestCase):
    """`phaseChipDetail` is a pure function of the snapshot, so — as with
    `md()` and `phaseLabel()` — it is lifted out of the page and run."""

    @classmethod
    def setUpClass(cls):
        html = BOARD.read_text(encoding="utf-8")
        source = ""
        for name in ("phaseProgress", "clip", "phaseChipDetail", "phasesInFlight"):
            match = re.search(rf"^function {name}\(.*?\n\}}\n", html, re.M | re.S)
            assert match, f"board.html lost its {name}()"
            source += match.group(0)
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "chip.js"
        cls.js.write_text(
            "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
            "const S = { state: { phases: input.phases || {} } };\n" + source +
            "process.stdout.write(JSON.stringify(input.phase "
            "? phaseChipDetail(input.phase) "
            ": phasesInFlight().map(p => p.file)));\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def run_js(self, payload: dict):
        out = subprocess.run([NODE, str(self.js)], input=json.dumps(payload),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def detail(self, **phase) -> str:
        return self.run_js({"phase": phase})

    def members(self, *states) -> list[dict]:
        return [{"number": str(30 + i), "file": f"{30 + i}-c.md", "state": s}
                for i, s in enumerate(states, start=1)]

    def test_a_run_says_how_far_and_which_card(self):
        """Progress is what has landed on the phase branch, not where the
        pointer is — `2/5 · on #33` says two different things, which is the
        whole reason both are on the chip."""
        self.assertEqual(
            self.detail(running=True,
                        members=self.members("merged", "merged", "running",
                                             "pending", "pending")),
            "2/5 · on #33")

    def test_a_member_waiting_on_its_checks_is_still_the_card_in_flight(self):
        self.assertEqual(
            self.detail(running=True, members=self.members("merged", "waiting")),
            "1/2 · on #32")

    def test_a_dependency_holding_the_phase_is_named(self):
        self.assertEqual(
            self.detail(running=True, waitingOn=["99"],
                        members=self.members("merged", "pending")),
            "1/2 · waiting on #99")

    def test_between_passes_the_progress_stands_alone(self):
        self.assertEqual(
            self.detail(running=True, members=self.members("merged", "pending")),
            "1/2")

    def test_a_halt_names_the_card_and_the_reason(self):
        self.assertEqual(
            self.detail(halted="35: it is not ready", haltedAt="35",
                        haltedWhy="it is not ready", members=self.members("merged")),
            "halted at #35 — it is not ready")

    def test_a_long_reason_is_clipped_for_the_header(self):
        detail = self.detail(halted="x", haltedAt="35", members=self.members(),
                             haltedWhy="its run ended without reaching review/ — "
                                       "API Error: 500 overloaded, the model said")
        self.assertTrue(detail.endswith("…"), detail)
        self.assertLessEqual(len(detail), 64, "a header chip is not the place "
                                              "a reason gets to run long")

    def test_a_halt_with_no_member_still_says_what_stopped(self):
        self.assertEqual(
            self.detail(halted="the run could not continue: no", members=[],
                        haltedWhy="the run could not continue: no"),
            "halted — the run could not continue: no")

    def test_a_quiet_board_has_nothing_in_flight(self):
        self.assertEqual(self.run_js({"phases": {
            "40-a.md": {"file": "40-a.md", "running": False, "halted": None},
        }}), [])

    def test_a_halted_phase_leads_the_running_one(self):
        self.assertEqual(self.run_js({"phases": {
            "40-a.md": {"file": "40-a.md", "running": True, "halted": None},
            "41-b.md": {"file": "41-b.md", "running": False, "halted": "31: red"},
        }}), ["41-b.md", "40-a.md"])


class TheCardsActions(unittest.TestCase):
    """▸ run phase sits in the slot ▸ start work has on every other card,
    and ‖ hold replaces it while the phase runs — both on the one action
    machine, so both arm before they fire."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        block = re.search(r"if \(task\.stage === 'in-progress' && task\.isPhase\)"
                          r" \{.*?\n    \} else if \(task\.stage === 'in-progress'\)",
                          cls.html, re.S)
        assert block, "board.html lost the phase card's actions"
        cls.block = block.group(0)

    def test_the_action_only_exists_on_an_in_progress_phase_card(self):
        self.assertIn("task.stage === 'in-progress' && task.isPhase", self.html)

    def test_running_a_phase_arms_like_every_other_launch(self):
        self.assertIn("label: 'run phase'", self.block)
        self.assertIn("confirm: 'run it?'", self.block)
        self.assertIn("busy: 'starting…'", self.block)

    def test_a_phase_someone_else_holds_is_taken_over_deliberately(self):
        self.assertIn("label: 'take over'", self.block)
        self.assertIn("takeover: true", self.block)

    def test_hold_is_the_word_while_it_runs(self):
        self.assertIn("ph.running) actions.push(hold)", self.block)
        self.assertIn("label: 'hold'", self.block)
        self.assertIn("confirm: 'hold it?'", self.block)

    def test_a_halt_offers_the_way_out_and_the_way_to_the_reason(self):
        """A halt still holds until the phase is run again or held, and
        holding is here. Running it again moved to the Phases view with
        card 57: it sits under the reason the phase stopped, because
        clearing a halt should mean having read what caused it. So this
        card carries ⟶ phases instead — the way to that reading."""
        self.assertIn("ph.halted) actions.push(room, hold)", self.block)
        self.assertIn("setView('phases')", self.block)
        self.assertNotIn("ph.halted) actions.push(start", self.block)

    def test_the_slot_never_holds_three(self):
        self.assertIn("else if (actions.length < 2) {", self.html,
                      "the relevance check gives way rather than stacking")

    def test_both_go_through_the_api(self):
        run = re.search(r"async function runPhase.*?\n\}", self.html, re.S).group(0)
        hold = re.search(r"async function holdPhase.*?\n\}", self.html, re.S).group(0)
        self.assertIn("'/api/phase/run'", run)
        self.assertIn("'/api/phase/stop'", hold)
        for body in (run, hold):
            self.assertIn("return res.ok", body,
                          "fireAction can only unlock on error if it is told")
            self.assertIn("toast(", body)


class ThePhaseCardsSheet(unittest.TestCase):
    """Opening a phase card answers "where is this up to" without a hunt
    across five columns."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        cls.block = re.search(r"function phaseMembers\(t\) \{.*?\n\}\n",
                              cls.html, re.S).group(0)

    def test_the_sheet_lists_the_members(self):
        self.assertIn("phaseMembers(t) +", self.html,
                      "the list belongs in the drawer, above the card's text")

    def test_only_a_phase_card_gets_one(self):
        self.assertIn("if (!t.isPhase) return '';", self.block)

    def test_run_order_is_the_order_it_lists(self):
        self.assertIn("t.members.map((m, i)", self.block)
        self.assertIn("${i + 1}", self.block)

    def test_each_member_carries_its_stage(self):
        self.assertIn("m.stage", self.block)

    def test_the_runners_reading_is_shown_when_there_is_one(self):
        self.assertIn("S.state.phases || {}", self.block)
        self.assertIn("MEMBER_STATE", self.block)
        for word in ("merged in", "working", "stopped here"):
            self.assertIn(word, self.html)

    def test_a_row_opens_that_card(self):
        self.assertIn("data-member", self.block)
        self.assertIn("findTask(row.dataset.member)", self.html)


class TheTickerReadsPhaseEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_a_phase_event_has_a_glyph_of_its_own(self):
        glyphs = re.search(r"const GLYPHS = \{.*?\};", self.html, re.S).group(0)
        self.assertIn("phase: '⟶'", glyphs)

    def test_phase_events_are_in_the_moves_filter(self):
        moves = re.search(r"\['moves', 'Moves', new Set\((\[[^\]]*\])\)\]", self.html)
        self.assertIsNotNone(moves, "board.html lost its Moves filter")
        self.assertIn("'phase'", moves.group(1),
                      "a phase's narration is board-level, like a move or a sync")


class TheApiHasAStop(unittest.TestCase):
    def test_the_route_exists(self):
        source = (REPO / "manager" / "core" / "httpd.py").read_text(encoding="utf-8")
        self.assertIn('"/api/phase/stop"', source)
        self.assertIn("phases.stop_phase", source)


class TheDocumentedPromise(unittest.TestCase):
    """What the board does is documented in AGENTS.md; an interface that is
    not written down is one the next reader has to find by clicking."""

    @classmethod
    def setUpClass(cls):
        cls.doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")

    def test_the_actions_are_named(self):
        self.assertIn("▸ run phase", self.doc)

    def test_the_chip_is_described(self):
        self.assertIn("halted", self.doc)
        self.assertIn("header", self.doc)


if __name__ == "__main__":
    unittest.main()
