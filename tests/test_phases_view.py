"""The Phases view: a swimlane each (task 57).

Card 56 took a phase's members off the Board; this is where they went. One
lane per phase, the same five stages left to right, and the last column is
the phase's own — a member merged into the phase branch is finished as far
as the phase is concerned and is nowhere near `main`.

Two halves, as elsewhere for board.html. The rules are exercised for real:
`taskfiles.collect()` reads a throwaway tasks/ directory exactly as the
board does, and the page's own functions are lifted out of board.html and
run in node over that reading. The rest — that the lane draws whole cards,
that ▸ run again lives here and hold means hold, that nothing in this view
ends a phase — is a source-level invariant, board.html being a single file
with inline JS and no frontend test runner.

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

# The rules a lane is drawn by, lifted from the page as written.
PARTS = (
    r"const STAGE_TINT = \{.*?\};",
    r"function allTasks\(\) \{[^\n]*\}",
    r"function findTask\(file\) \{[^\n]*\}",
    r"function laneRank\(task\) \{.*?\n\}",
    r"function lanePhases\(\) \{.*?\n\}",
    r"function canonicalNumber\(number\) \{.*?\n\}",
    r"const PHASE_LOG_LINE = [^\n]*",
    r"function phaseLog\(task\) \{.*?\n\}",
    r"function mergedIn\(task\) \{.*?\n\}",
    r"function laneColumns\(\) \{.*?\n\}",
    r"function laneColumnOf\(task, merged\) \{.*?\n\}",
    r"function laneMembers\(task\) \{.*?\n\}",
    r"function laneProgress\(members\) \{.*?\n\}",
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
         status: str = "Backlog", log: str | None = None) -> str:
    """A task file as a person — or the runner — would write it."""
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n"
    if kind:
        text += f"**Type:** {kind}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n{cards}\n"
    if log is not None:
        text += f"\n## Phase log\n\n{log}\n"
    return text


@unittest.skipUnless(NODE, "node is needed to run the page's own rules")
class LaneCase(unittest.TestCase):
    """One tasks/ directory per test, read as the board reads it, then laid
    out by the page's own functions."""

    @classmethod
    def setUpClass(cls):
        cls.src = harness()

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-lanes-")).resolve()
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

    def state(self, **extra) -> dict:
        return {"board": taskfiles.collect(), "agents": [], "phases": {}, **extra}

    def run_js(self, expression: str, **extra) -> object:
        script = (self.src + "\nvar S = { state: " + json.dumps(self.state(**extra))
                  + " };\nconsole.log(JSON.stringify(" + expression + "));\n")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def lanes(self, **extra) -> list[str]:
        """The phases drawn, in the order they are drawn."""
        return self.run_js("lanePhases().map(t => t.file)", **extra)

    def lane(self, file: str, **extra) -> dict:
        """One lane's reading: its columns, and where each member landed."""
        return self.run_js(
            "(() => { const t = findTask(" + json.dumps(file) + ");"
            " const members = laneMembers(t);"
            " return { columns: laneColumns().map(c => ({ slug: c.slug, label: c.label,"
            "   files: members.filter(m => m.column === c.slug).map(m => m.task.file) })),"
            "   members: members.map(m => ({ pos: m.pos, file: m.task.file,"
            "     stage: m.task.stage, column: m.column })),"
            "   progress: laneProgress(members) }; })()", **extra)

    # the shape most of these use: a phase and the three cards it lists,
    # spread over the stages they are genuinely in
    def a_phase_of_three(self, phase_stage: str = "in-progress",
                         log: str | None = None) -> None:
        self.write("31-stand-up-site.md", card("31 — Stand up site/",
                                               status="Review"), "review")
        self.write("32-serve-it.md", card("32 — Serve it", status="In Progress"),
                   "in-progress")
        self.write("33-landing.md", card("33 — The landing page", status="To Do"),
                   "to-do")
        self.write("40-the-site.md",
                   card("40 — Ship the site", kind="Phase", log=log,
                        cards="- 31 — Stand up site/\n- 32 — Serve it\n"
                              "- 33 — The landing page\n",
                        status=config.STAGE_LABELS[phase_stage]), phase_stage)

    def snapshot(self, **extra) -> dict:
        """What the runner's last pass would have published for the phase."""
        members = [{"number": "31", "file": "31-stand-up-site.md",
                    "title": "31 — Stand up site/", "stage": "review",
                    "state": "merged", "why": ""},
                   {"number": "32", "file": "32-serve-it.md",
                    "title": "32 — Serve it", "stage": "in-progress",
                    "state": "running", "why": ""},
                   {"number": "33", "file": "33-landing.md",
                    "title": "33 — The landing page", "stage": "to-do",
                    "state": "pending", "why": ""}]
        snap = {"file": "40-the-site.md", "branch": "phase/40-the-site",
                "members": members, "running": True, "started": True,
                "halted": "", "haltedAt": None, "haltedWhy": None}
        snap.update(extra)
        return {"40-the-site.md": snap}


class OneLanePerPhase(LaneCase):
    def test_every_phase_card_draws_a_lane(self):
        self.a_phase_of_three()
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 31 — Stand up site/\n",
                                          status="To Do"), "to-do")

        self.assertEqual(self.lanes(), ["40-the-site.md", "41-the-docs.md"])

    def test_a_board_with_no_phases_draws_no_lanes(self):
        """…and the view says so plainly rather than rendering an empty grid
        — the sentence itself is asserted in the wiring half below."""
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))

        self.assertEqual(self.lanes(), [])

    def test_a_halted_phase_is_never_below_the_fold(self):
        """It is the one thing on this page waiting on a person."""
        self.a_phase_of_three()
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 33 — The landing page\n",
                                          status="In Progress"), "in-progress")
        phases = self.snapshot()
        phases["41-the-docs.md"] = {"file": "41-the-docs.md", "members": [],
                                    "running": False, "started": True,
                                    "halted": "halted at 33 — it is not ready",
                                    "haltedAt": "33", "haltedWhy": "it is not ready"}

        self.assertEqual(self.lanes(phases=phases),
                         ["41-the-docs.md", "40-the-site.md"],
                         "the halt sorts above the run in flight")

    def test_a_run_in_flight_sorts_above_a_phase_nobody_has_started(self):
        self.a_phase_of_three()
        self.write("39-earlier.md", card("39 — An earlier phase", kind="Phase",
                                         cards="- 33 — The landing page\n",
                                         status="To Do"), "to-do")

        self.assertEqual(self.lanes(phases=self.snapshot()),
                         ["40-the-site.md", "39-earlier.md"])

    def test_a_finished_phase_sorts_last(self):
        self.a_phase_of_three(phase_stage="done")
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 33 — The landing page\n",
                                          status="To Do"), "to-do")

        self.assertEqual(self.lanes(), ["41-the-docs.md", "40-the-site.md"])


class ALaneHoldsThePhasesCards(LaneCase):
    def test_every_listed_card_is_drawn_in_the_stage_it_is_in(self):
        self.a_phase_of_three()

        lane = self.lane("40-the-site.md")

        self.assertEqual([m["file"] for m in lane["members"]],
                         ["31-stand-up-site.md", "32-serve-it.md", "33-landing.md"])
        columns = {c["slug"]: c["files"] for c in lane["columns"]}
        self.assertEqual(columns["review"], ["31-stand-up-site.md"])
        self.assertEqual(columns["in-progress"], ["32-serve-it.md"])
        self.assertEqual(columns["to-do"], ["33-landing.md"])

    def test_a_member_carries_its_position_in_the_run(self):
        self.a_phase_of_three()

        self.assertEqual([(m["pos"], m["file"]) for m in
                          self.lane("40-the-site.md")["members"]],
                         [(1, "31-stand-up-site.md"), (2, "32-serve-it.md"),
                          (3, "33-landing.md")])

    def test_a_listed_number_that_names_no_card_draws_nothing_and_breaks_nothing(self):
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 99 — a card that is not here\n",
                                          status="In Progress"), "in-progress")

        lane = self.lane("40-the-site.md")

        self.assertEqual(lane["members"], [])
        self.assertEqual(lane["progress"], {"done": 0, "total": 0})


class TheLastColumnIsThePhasesOwn(LaneCase):
    """A member merged into the phase branch is finished as far as the phase
    is concerned, and is not in main yet — so the column is not done/."""

    def columns(self, **extra) -> dict[str, list[str]]:
        return {c["slug"]: c["files"]
                for c in self.lane("40-the-site.md", **extra)["columns"]}

    def test_the_five_columns_end_in_the_phases_own(self):
        self.a_phase_of_three()

        columns = self.lane("40-the-site.md")["columns"]

        self.assertEqual([c["slug"] for c in columns],
                         ["backlog", "to-do", "in-progress", "review", "merged"])
        self.assertEqual(columns[-1]["label"], "Merged in")
        self.assertNotIn("done", [c["slug"] for c in columns])

    def test_the_runners_reading_puts_a_merged_member_there(self):
        """Its card is in review/; the phase branch already holds it."""
        self.a_phase_of_three()

        columns = self.columns(phases=self.snapshot())

        self.assertEqual(columns["merged"], ["31-stand-up-site.md"])
        self.assertEqual(columns["review"], [])

    def test_the_log_remembers_it_once_the_runner_has_stopped_passing(self):
        """The edge case: every member merged, the phase waiting on its own
        PR in review/, so no snapshot exists any more. The lane still draws
        the work where it landed, off the log the runner wrote."""
        self.a_phase_of_three(
            phase_stage="review",
            log="- 2026-08-01 10:00 · run started\n"
                "- 2026-08-01 10:20 · 31 merged into phase/40-the-site\n"
                "- 2026-08-01 11:05 · 32 merged into phase/40-the-site\n"
                "- 2026-08-01 12:00 · 33 merged into phase/40-the-site\n"
                "- 2026-08-01 12:01 · every card merged — opening the phase PR\n")

        lane = self.lane("40-the-site.md")

        self.assertEqual([c["files"] for c in lane["columns"]][-1],
                         ["31-stand-up-site.md", "32-serve-it.md", "33-landing.md"])
        self.assertEqual(lane["progress"], {"done": 3, "total": 3})

    def test_a_card_in_done_is_finished_by_any_reading(self):
        self.write("31-stand-up-site.md", card("31 — Stand up site/",
                                               status="Done"), "done")
        self.write("40-the-site.md", card("40 — Ship the site", kind="Phase",
                                          cards="- 31 — Stand up site/\n",
                                          status="In Progress"), "in-progress")

        self.assertEqual(self.lane("40-the-site.md")["columns"][-1]["files"],
                         ["31-stand-up-site.md"])

    def test_progress_is_what_the_last_column_holds(self):
        """The head and the column it counts can never disagree, because
        they are one placement read twice."""
        self.a_phase_of_three()

        self.assertEqual(self.lane("40-the-site.md",
                                   phases=self.snapshot())["progress"],
                         {"done": 1, "total": 3})


class ThePhaseLog(LaneCase):
    """The runner's decisions, read off the card it wrote them on."""

    def log(self, **extra) -> list[dict]:
        return self.run_js("phaseLog(findTask('40-the-site.md'))", **extra)

    def test_the_entries_are_in_the_order_the_runner_wrote_them(self):
        self.a_phase_of_three(
            log="- 2026-08-01 10:00 · run started\n"
                "- 2026-08-01 10:02 · 31 started\n"
                "- 2026-08-01 10:20 · 31 merged into phase/40-the-site\n")

        self.assertEqual([e["text"] for e in self.log()],
                         ["run started", "31 started",
                          "31 merged into phase/40-the-site"])
        self.assertEqual(self.log()[0]["at"], "2026-08-01 10:00")

    def test_a_halt_is_an_entry_like_any_other(self):
        self.a_phase_of_three(
            log="- 2026-08-01 10:00 · run started\n"
                "- 2026-08-01 10:40 · halted at 32 — its CI is red\n")

        self.assertEqual(self.log()[-1]["text"], "halted at 32 — its CI is red")

    def test_a_phase_nobody_has_run_has_no_log(self):
        self.a_phase_of_three()

        self.assertEqual(self.log(), [])

    def test_the_log_stops_at_the_next_heading(self):
        self.write("40-the-site.md",
                   card("40 — Ship the site", kind="Phase", status="In Progress",
                        cards="- 31 — Stand up site/\n",
                        log="- 2026-08-01 10:00 · run started\n\n"
                            "## PR review\n\n- 2026-08-01 10:30 · not a log line\n"),
                   "in-progress")

        self.assertEqual([e["text"] for e in self.log()], ["run started"])

    def test_what_the_log_says_is_merged_is_read_as_merged(self):
        self.a_phase_of_three(
            log="- 2026-08-01 10:20 · 31 merged into phase/40-the-site\n")

        self.assertEqual(self.run_js(
            "[...mergedIn(findTask('40-the-site.md'))]"), ["31"])

    def test_a_started_card_is_not_a_merged_one(self):
        """The distinction the log exists to make: this card has run, and
        that is not the same as its work having landed."""
        self.a_phase_of_three(
            log="- 2026-08-01 10:00 · run started\n"
                "- 2026-08-01 10:02 · 32 started\n")

        self.assertEqual(self.run_js(
            "[...mergedIn(findTask('40-the-site.md'))]"), [])


class Wiring(unittest.TestCase):
    """board.html's own half: the view, the crossing from the Board, and
    what this room deliberately cannot do."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def body(self, pattern: str) -> str:
        match = re.search(pattern, self.html, re.S)
        self.assertIsNotNone(match, f"board.html no longer defines {pattern!r}")
        return match.group(0)

    def test_the_switcher_offers_a_fourth_view(self):
        self.assertIn('<button data-view="phases">Phases</button>', self.html)
        self.assertIn('id="view-phases"', self.html)
        self.assertIn("phases: 'phases'", self.body(r"const VIEW_TITLES = [^\n]*"),
                      "the tab title names the view like the other three")

    def test_the_view_renders_where_the_others_do(self):
        render = self.body(r"function render\(\) \{.*?\n\}")
        self.assertIn("renderViews();", render)
        self.assertIn("renderCards();", render)
        self.assertIn("if (S.view === 'phases') renderLanes();",
                      self.body(r"function renderCards\(\) \{.*?\n\}"))

    def test_the_switcher_carries_the_count_and_the_halt(self):
        """The crossing: a phase halts while you are on the Board and you
        learn it there — the toast and the ticker are the server's, and this
        is the one that keeps saying it after they have scrolled away."""
        views = self.body(r"function renderViews\(\) \{.*?\n\}")
        self.assertIn("p.running", views)
        self.assertIn("p.halted", views)
        self.assertIn("vcount", views)
        self.assertIn("vhalt", views)
        self.assertIn(".views .vhalt{", self.html)
        self.assertIn("var(--alarm)", self.body(r"\.views \.vhalt\{[^}]*\}"),
                      "a halt is alarm-coloured wherever it is worn")

    def test_the_cards_are_the_cards(self):
        """Full fidelity is the advantage a whole view buys: the lane builds
        the same nodes the Board does, not a shrunken token."""
        lane = self.body(r"function laneFor\(task\) \{.*?\n\}")
        self.assertIn("cardFor(m.task)", lane)
        self.assertEqual(len(re.findall(r"appendChild\(cardFor\(", self.html)), 2,
                         "cardFor() builds the cards for the two views that draw "
                         "them — the Board's columns and a lane's")

    def test_the_lane_head_holds_the_phase_and_the_log_sits_under_it(self):
        lane = self.body(r"function laneFor\(task\) \{.*?\n\}")
        self.assertIn("holdPhase(task)", lane)
        self.assertIn("data-open", lane)
        self.assertIn("branch", lane)
        self.assertLess(lane.index('<div class="lstages"'), lane.index("laneLog(task)"),
                        "the log is read under the lane, after its columns")

    def test_run_again_lives_here_and_not_on_the_board(self):
        """Clearing a halt should mean having read what caused it, so the
        action sits beneath the reason — and the Board's phase card points
        at the room rather than offering the launch itself."""
        lane = self.body(r"function laneFor\(task\) \{.*?\n\}")
        self.assertIn("'run again'", lane)
        self.assertIn("runPhase(task)", lane)
        self.assertIn(".lhalt .lacts", lane,
                      "▸ run again is inside the halt, under its reason")
        card_fn = self.body(r"function cardFor\(task\) \{.*?\n\}")
        self.assertIn("else if (ph && ph.halted) actions.push(room, hold);", card_fn)
        self.assertIn("setView('phases')", card_fn)

    def test_holding_stops_the_run_and_unwinds_nothing(self):
        hold = self.body(r"async function holdPhase\(task\) \{.*?\n\}")
        self.assertIn("/api/phase/stop", hold)
        lane = self.body(r"function laneFor\(task\) \{.*?\n\}")
        self.assertIn("stay exactly as they are", lane)

    def test_nothing_here_ends_the_phase(self):
        """Merging is a board move on the phase card: one place where work
        leaves the board, and this is not it."""
        for pattern in (r"function laneFor\(task\) \{.*?\n\}",
                        r"function renderLanes\(\) \{.*?\n\}"):
            body = self.body(pattern)
            for forbidden in ("completeSheet", "/api/task/complete", "rawMove(",
                              "addToPhase", "'/api/move'"):
                self.assertNotIn(forbidden, body,
                                 f"{forbidden} does not belong in the Phases view")

    def test_the_empty_view_says_so_plainly(self):
        render = self.body(r"function renderLanes\(\) \{.*?\n\}")
        self.assertIn("No phases yet.", render)
        self.assertIn("lanePhases()", render)

    def test_the_lane_keeps_your_place_across_a_redraw(self):
        render = self.body(r"function renderLanes\(\) \{.*?\n\}")
        self.assertIn("markScroll('v:phases'", render)
        self.assertIn("restoreScroll('v:phases'", render)
        self.assertIn("v:phaselog:", render)


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
