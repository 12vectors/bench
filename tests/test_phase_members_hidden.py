"""Phase members leave the main board (task 56).

The Board view stops drawing a phase's members: the phase card stands for
them there, and the columns go back to counting what you can actually see.
Nothing is deleted, moved or marked — membership, which the server already
derives on every read, is the only thing that hides a card, so removing it
is what brings the card back.

Two halves, as elsewhere for board.html. The behaviour is exercised for
real: `taskfiles.collect()` reads a throwaway tasks/ directory exactly as
the board does, and the page's own rules are lifted out of board.html and
run in node over that reading. The wiring — that renderBoard() draws and
counts the same list, and that no other view calls the hiding rule — is a
source-level invariant, board.html being a single file with inline JS and
no frontend test runner.

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
import taskfiles  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")

# The rules the Board view draws by, lifted from the page as written.
PARTS = (
    r"const STAGE_NOTE = \{[^\n]*\};",
    r"function agentOnTask\(file\) \{.*?\n\}",
    r"function runningAgents\(\) \{.*?\n\}",
    r"function allTasks\(\) \{[^\n]*\}",
    r"function findTask\(file\) \{[^\n]*\}",
    r"function phaseProgress\(p\) \{.*?\n\}",
    r"function heldByPhase\(task\) \{.*?\n\}",
    r"function columnCards\(stage\) \{.*?\n\}",
    r"function columnNote\(stage, shown\) \{.*?\n\}",
    r"function phaseSummary\(task\) \{.*?\n\}",
    r"function phaseFlight\(task\) \{.*?\n\}",
)


def harness() -> str:
    html = BOARD.read_text(encoding="utf-8")
    out = []
    for pattern in PARTS:
        match = re.search(pattern, html, re.S)
        if match is None:
            raise AssertionError(f"board.html no longer defines {pattern!r}")
        out.append(match.group(0))
    return "\n".join(out)


def card(title: str, *, kind: str | None = None, cards: str | None = None,
         status: str = "Backlog") -> str:
    """A task file as a person would write it."""
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n"
    if kind:
        text += f"**Type:** {kind}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n{cards}\n"
    return text


@unittest.skipUnless(NODE, "node is needed to run the page's own rules")
class BoardViewCase(unittest.TestCase):
    """One tasks/ directory per test, read as the board reads it, then
    rendered by the page's own functions."""

    @classmethod
    def setUpClass(cls):
        cls.src = harness()

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-hide-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tasks = tmp / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        self.addCleanup(setattr, config, "TASKS", config.TASKS)
        config.TASKS = self.tasks
        self.addCleanup(setattr, config, "TM_ROOT", config.TM_ROOT)
        config.TM_ROOT = tmp

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def relocate(self, filename: str, source: str, target: str) -> None:
        (self.tasks / source / filename).rename(self.tasks / target / filename)

    def state(self, **extra) -> dict:
        return {"board": taskfiles.collect(), "agents": [], "phases": {}, **extra}

    def run_js(self, expression: str, **extra) -> object:
        script = (self.src + "\nvar S = { state: " + json.dumps(self.state(**extra))
                  + " };\nconsole.log(JSON.stringify(" + expression + "));\n")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def columns(self, **extra) -> dict[str, dict]:
        """What each column draws and says — renderBoard()'s own reading."""
        drawn = self.run_js(
            "S.state.board.stages.map(s => { const shown = columnCards(s);"
            " return { slug: s.slug, count: shown.length,"
            " note: columnNote(s, shown), files: shown.map(t => t.file) }; })",
            **extra)
        return {column["slug"]: column for column in drawn}

    # the shape used by most of these: a phase card and the two cards it
    # lists, spread over the stages they are genuinely in
    def a_phase_of_two(self, phase_stage: str = "in-progress",
                       listed: str = "- 31 — Stand up site/\n- 32 — Serve it\n") -> None:
        self.write("31-stand-up-site.md", card("31 — Stand up site/",
                                               status="Review"), "review")
        self.write("32-serve-it.md", card("32 — Serve it", status="To Do"), "to-do")
        self.write("40-the-site.md",
                   card("40 — Ship the site", kind="Phase", cards=listed,
                        status=config.STAGE_LABELS[phase_stage]), phase_stage)


class MembersLeaveTheBoard(BoardViewCase):
    def test_the_members_are_absent_and_the_phase_card_is_present(self):
        self.a_phase_of_two()

        columns = self.columns()

        drawn = [f for column in columns.values() for f in column["files"]]
        self.assertEqual(drawn, ["40-the-site.md"],
                         "a phase's members are not drawn on the Board view")

    def test_the_column_that_held_a_member_counts_one_fewer_and_says_where(self):
        self.a_phase_of_two()

        columns = self.columns()

        self.assertEqual(columns["review"]["count"], 0)
        self.assertIn("+1 in phases", columns["review"]["note"])
        self.assertEqual(columns["to-do"]["count"], 0)
        self.assertIn("+1 in phases", columns["to-do"]["note"])

    def test_the_note_is_a_signpost_beside_the_stages_own_word(self):
        """`+1 in phases` says where the work went; it does not correct the
        number or replace what the column has always called itself. "your
        move" is now a claim about the cards you can see — see the card's
        own note on that."""
        self.a_phase_of_two()

        self.assertEqual(self.columns()["review"]["note"], "your move · +1 in phases")

    def test_two_hidden_in_one_column_are_counted_together(self):
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("32-serve-it.md", card("32 — Serve it"))
        self.write("33-landing.md", card("33 — The landing page"))
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n- 32 — Serve it\n"))

        columns = self.columns()

        self.assertEqual(columns["backlog"]["files"],
                         ["33-landing.md", "40-the-site.md"])
        self.assertEqual(columns["backlog"]["count"], 2)
        self.assertEqual(columns["backlog"]["note"], "+2 in phases")

    def test_a_column_with_no_hidden_members_shows_no_such_note(self):
        self.a_phase_of_two()

        columns = self.columns()

        for slug in ("backlog", "in-progress", "done"):
            with self.subTest(stage=slug):
                self.assertNotIn("in phases", columns[slug]["note"])
        self.assertEqual(columns["in-progress"]["note"], "")
        self.assertEqual(columns["done"]["note"], "")

    def test_the_count_is_what_you_can_see_in_every_column(self):
        """The rule that keeps the header honest, stated as a whole-board
        invariant rather than one column's arithmetic."""
        self.a_phase_of_two()

        for column in self.columns().values():
            with self.subTest(stage=column["slug"]):
                self.assertEqual(column["count"], len(column["files"]))


class RemovingMembershipUnhides(BoardViewCase):
    """Membership is the only thing that hides a card, so there is nothing
    to sweep or migrate: each of these simply stops deriving it."""

    def test_a_phase_that_reached_done_hands_its_cards_back(self):
        self.a_phase_of_two(phase_stage="done")

        columns = self.columns()

        self.assertEqual(columns["review"]["files"], ["31-stand-up-site.md"])
        self.assertEqual(columns["to-do"]["files"], ["32-serve-it.md"])
        self.assertEqual(columns["done"]["files"], ["40-the-site.md"])
        for slug in ("review", "to-do"):
            self.assertNotIn("in phases", columns[slug]["note"])

    def test_a_phase_moved_to_done_while_you_watch_hands_them_back(self):
        """The same board, redrawn: nothing else changes and no other rule
        runs — the members reappear in the stages they were always in."""
        self.a_phase_of_two(phase_stage="review")
        before = self.columns()
        self.assertEqual(before["review"]["files"], ["40-the-site.md"])

        self.relocate("40-the-site.md", "review", "done")

        after = self.columns()
        self.assertEqual(after["review"]["files"], ["31-stand-up-site.md"])
        self.assertEqual(after["to-do"]["files"], ["32-serve-it.md"])

    def test_an_archived_phase_hands_its_cards_back(self):
        self.a_phase_of_two(phase_stage="to-do")

        self.relocate("40-the-site.md", "to-do", "archive")

        columns = self.columns()
        self.assertEqual(columns["review"]["files"], ["31-stand-up-site.md"])
        self.assertEqual(columns["to-do"]["files"], ["32-serve-it.md"])
        self.assertNotIn("in phases", columns["to-do"]["note"])

    def test_a_number_removed_from_the_list_brings_that_card_back(self):
        self.a_phase_of_two(listed="- 31 — Stand up site/\n- 32 — Serve it\n")
        self.assertEqual(self.columns()["to-do"]["files"], [])

        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n",
                                          status="In Progress"), "in-progress")

        columns = self.columns()
        self.assertEqual(columns["to-do"]["files"], ["32-serve-it.md"],
                         "the card the list no longer names is on the board again")
        self.assertNotIn("in phases", columns["to-do"]["note"])
        self.assertEqual(columns["review"]["files"], [])
        self.assertIn("+1 in phases", columns["review"]["note"])


class AuthoringMistakesHideNothing(BoardViewCase):
    """A card that vanishes is worse than a card that is flagged."""

    def test_a_card_two_phases_claim_is_still_drawn(self):
        self.write("32-serve-it.md", card("32 — Serve it", status="To Do"), "to-do")
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 32 — Serve it\n"))
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 32 — Serve it\n"))

        columns = self.columns()

        self.assertEqual(columns["to-do"]["files"], ["32-serve-it.md"],
                         "a card wearing phase drift must not disappear")
        self.assertEqual(columns["to-do"]["note"], "next up")

    def test_a_number_no_card_has_hides_nothing(self):
        self.write("32-serve-it.md", card("32 — Serve it", status="To Do"), "to-do")
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 99 — a card that is not here\n"))

        columns = self.columns()

        self.assertEqual(columns["to-do"]["files"], ["32-serve-it.md"])
        self.assertEqual(columns["backlog"]["files"], ["40-the-site.md"])

    def test_a_phase_listing_a_phase_hides_neither(self):
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 33 — The landing page\n"))
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 41 — Ship the docs\n"))

        columns = self.columns()

        self.assertEqual(columns["backlog"]["files"],
                         ["40-the-site.md", "41-the-docs.md"])


class HidingCardsDoesNotHideActivity(BoardViewCase):
    def test_the_live_agents_chip_still_counts_an_agent_on_a_hidden_member(self):
        self.a_phase_of_two()
        agents = [{"task": "32-serve-it.md", "status": "running", "started": 1}]

        self.assertEqual(self.run_js("runningAgents().length", agents=agents), 1,
                         "the header counts agents, not cards this view draws")
        # …and the card is still hidden while its agent works
        self.assertEqual(self.columns(agents=agents)["to-do"]["files"], [])

    def test_the_column_note_counts_the_agents_you_can_see(self):
        """The count and its note describe the same visible cards, so a
        column drawing no agent never claims one; the header chip above is
        what says an agent is alive somewhere."""
        self.a_phase_of_two()
        agents = [{"task": "32-serve-it.md", "status": "running", "started": 1}]

        note = self.columns(agents=agents)["to-do"]["note"]
        self.assertNotIn("agent", note)
        self.assertIn("+1 in phases", note)

    def test_a_visible_agent_still_reads_as_it_did(self):
        self.write("32-serve-it.md", card("32 — Serve it", status="In Progress"),
                   "in-progress")
        agents = [{"task": "32-serve-it.md", "status": "running", "started": 1}]

        self.assertEqual(self.columns(agents=agents)["in-progress"]["note"],
                         "1 agent")


class WithNoPhasesNothingChanged(BoardViewCase):
    """The edge case that keeps this card from being a redesign: a board
    with no phase card on it renders exactly what it rendered before."""

    def board_as_before(self) -> dict[str, dict]:
        """The rule board.html applied until this card: every task drawn,
        the count is the directory's length, the note is the agents here or
        the stage's own word."""
        notes = {"to-do": "next up", "review": "your move"}
        columns = {}
        for stage in taskfiles.collect()["stages"]:
            columns[stage["slug"]] = {
                "slug": stage["slug"], "count": len(stage["tasks"]),
                "note": notes.get(stage["slug"], ""),
                "files": [task["file"] for task in stage["tasks"]]}
        return columns

    def test_every_column_reads_as_it_did(self):
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("32-serve-it.md", card("32 — Serve it", status="To Do"), "to-do")
        self.write("33-landing.md", card("33 — The landing page", status="Review"),
                   "review")
        self.write("34-done.md", card("34 — Something finished", status="Done"), "done")

        self.assertEqual(self.columns(), self.board_as_before())

    def test_an_empty_board_reads_as_it_did(self):
        self.assertEqual(self.columns(), self.board_as_before())


class ThePhaseCardsSummary(BoardViewCase):
    """It is the only thing standing for that work on this view, so it says
    how far the run has got and what it is doing."""

    def snapshot(self, **extra) -> dict:
        """What the runner's last pass would have published for the phase."""
        members = [{"number": "31", "file": "31-stand-up-site.md",
                    "title": "31 — Stand up site/", "stage": "review",
                    "state": "merged", "why": ""},
                   {"number": "32", "file": "32-serve-it.md",
                    "title": "32 — Serve it", "stage": "in-progress",
                    "state": "running", "why": ""}]
        return {"40-the-site.md": {"file": "40-the-site.md", "members": members,
                                   "running": True, "halted": "", "haltedAt": None,
                                   "haltedWhy": None, **extra}}

    def summary(self, **extra) -> str:
        return self.run_js("phaseSummary(findTask('40-the-site.md'))", **extra)

    def flight(self, **extra):
        return self.run_js("phaseFlight(findTask('40-the-site.md'))", **extra)

    def test_progress_is_what_has_landed_on_the_phase_branch(self):
        self.a_phase_of_two()

        self.assertEqual(self.summary(phases=self.snapshot()), "1 of 2 merged")

    def test_a_phase_nobody_has_started_says_what_it_holds(self):
        """No run, no progress to report — so it says how many cards it has
        rather than reporting a merge count that means nothing yet."""
        self.a_phase_of_two(phase_stage="to-do")

        self.assertEqual(self.summary(), "2 cards")

    def test_one_card_is_not_pluralised(self):
        self.a_phase_of_two(listed="- 31 — Stand up site/\n", phase_stage="to-do")

        self.assertEqual(self.summary(), "1 card")

    def test_the_member_in_flight_is_named(self):
        self.a_phase_of_two()

        self.assertEqual(self.flight(phases=self.snapshot()),
                         {"bad": False, "line": "on #32 — 32 — Serve it"})

    def test_a_dependency_holding_the_phase_is_named(self):
        snapshot = self.snapshot()
        snapshot["40-the-site.md"]["members"][1]["state"] = "pending"
        snapshot["40-the-site.md"]["waitingOn"] = ["7"]
        self.a_phase_of_two()

        self.assertEqual(self.flight(phases=snapshot),
                         {"bad": False, "line": "waiting on #7"})

    def test_a_halt_reaches_the_card_that_stands_for_the_member(self):
        self.a_phase_of_two()
        halted = self.snapshot(running=False, halted="halted at 32 — it is not ready",
                               haltedAt="32", haltedWhy="it is not ready")

        self.assertEqual(self.flight(phases=halted),
                         {"bad": True, "line": "halted at #32 — it is not ready"})

    def test_a_phase_with_no_run_has_nothing_to_say(self):
        self.a_phase_of_two(phase_stage="to-do")

        self.assertIsNone(self.flight())

    def test_a_phase_whose_run_ended_has_nothing_to_say(self):
        self.a_phase_of_two()

        self.assertIsNone(self.flight(phases=self.snapshot(running=False)))


class Wiring(unittest.TestCase):
    """board.html's own half: what keeps the count and the cards from
    drifting apart, and the hiding from leaking into the other views."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def body(self, pattern: str) -> str:
        match = re.search(pattern, self.html, re.S)
        self.assertIsNotNone(match, f"board.html no longer defines {pattern!r}")
        return match.group(0)

    def test_the_column_draws_and_counts_one_list(self):
        """The count is the number of cards drawn because it is the length
        of the list drawn — not a second reading that could disagree."""
        render = self.body(r"function renderBoard\(\) \{.*?\n\}")
        self.assertIn("const shown = columnCards(stage);", render)
        self.assertIn('<span class="count">${shown.length}</span>', render)
        self.assertIn("for (const task of shown)", render)
        self.assertNotIn("stage.tasks.length}</span>", render,
                         "the header must not count what the column does not draw")
        self.assertNotIn("for (const task of stage.tasks)", render)

    def test_the_note_is_built_where_the_column_is(self):
        render = self.body(r"function renderBoard\(\) \{.*?\n\}")
        self.assertIn("columnNote(stage, shown)", render)
        note = self.body(r"function columnNote\(stage, shown\) \{.*?\n\}")
        self.assertIn("in phases", note)
        self.assertIn("agentOnTask", note)

    def test_only_the_board_view_hides(self):
        """Sessions and Focus are about runs and sessions, not stages, and a
        phase member's agent is an agent like any other."""
        self.assertEqual(len(re.findall(r"heldByPhase\(", self.html)), 2,
                         "heldByPhase() is defined once and asked once, by columnCards()")
        self.assertEqual(len(re.findall(r"columnCards\(", self.html)), 2,
                         "columnCards() is defined once and used once, by renderBoard()")
        for view in (r"function renderFlight\(\) \{.*?\n\}",
                     r"function renderFocus\(\) \{.*?\n\}"):
            body = self.body(view)
            self.assertNotIn("heldByPhase", body)
            self.assertNotIn("columnCards", body)

    def test_drift_is_the_guard_inside_the_hiding_rule(self):
        held = self.body(r"function heldByPhase\(task\) \{.*?\n\}")
        self.assertIn("phaseDrift", held,
                      "an unresolved membership must not hide a card")
        self.assertIn("'done'", held,
                      "a phase that has reached done/ holds nothing")

    def test_the_summary_chip_sits_in_the_footer_row_and_opens_the_card(self):
        block = self.html[self.html.index("if (task.isPhase) {"):][:400]
        self.assertIn("chips.push", block)
        self.assertIn("phaseSummary(task)", block)
        self.assertIn("phase: task.file", block,
                      "the chip has to name the card it opens — its own")
        self.assertIn("⟶", block)

    def test_the_phase_card_wears_a_running_phase_and_a_halted_one(self):
        card_fn = self.body(r"function cardFor\(task\) \{.*?\n\}")
        self.assertIn("const flight = task.isPhase ? phaseFlight(task) : null;", card_fn)
        self.assertIn("flight && !flight.bad", card_fn,
                      "a phase in flight breathes like any other live work")
        self.assertIn("' halted'", card_fn)
        self.assertIn(".card.halted{", self.html,
                      "the halted card needs the alarm border it wears")

    def test_an_emptied_column_says_why_it_is_empty(self):
        render = self.body(r"function renderBoard\(\) \{.*?\n\}")
        self.assertIn("Everything here is in a phase.", render)
        self.assertIn("Nothing here. Good.", render,
                      "a genuinely empty column keeps the line it always had")


@unittest.skipUnless(NODE, "node is needed to parse the page")
class ThePageStillParses(unittest.TestCase):
    def test_the_inline_script_parses(self):
        html = BOARD.read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
        self.assertTrue(scripts, "board.html has no inline script")
        with tempfile.TemporaryDirectory() as tmp:
            for index, script in enumerate(scripts):
                source = Path(tmp) / f"page-{index}.js"
                source.write_text(script, encoding="utf-8")
                out = subprocess.run([NODE, "--check", str(source)],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
