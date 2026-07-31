"""The tab title carries how many agents are working (task 39).

A running agent was visible on the board and nowhere else, so the moment
you switched tabs — the normal thing to do while an agent works for
minutes — bench said the same string whether three agents ran or none.
The count now leads the title, ahead of the project, because a truncated
tab must still show it.

Two halves, as elsewhere for board.html. The behaviour — what the string
is for 0, 1 and N agents, which set is counted, and that document.title
is written only when it changed — is exercised for real: the pieces are
lifted out of the page and run in node, skipped where node is absent.
The wiring (both readers of "running" going through one function) is a
source-level invariant, board.html being a single file with inline JS and
no frontend test runner.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"

# The pieces the title is made of, lifted from the page as written.
PARTS = (
    r"function runningAgents\(\) \{.*?\n\}",
    r"const VIEW_TITLES = \{[^\n]*\};",
    r"const WORKING_MARK = '[^']+';",
    r"function tabTitle\(project, view, working\) \{.*?\n\}",
    r"let shownTitle = null;",
    r"function renderTitle\(\) \{.*?\n\}",
)


def _harness() -> str:
    html = BOARD.read_text(encoding="utf-8")
    out = []
    for pattern in PARTS:
        m = re.search(pattern, html, re.S)
        if m is None:
            raise AssertionError(f"board.html no longer defines {pattern!r}")
        out.append(m.group(0))
    return "\n".join(out)


def _running(n: int) -> list:
    """n agent records the board would call running, plus noise that is not:
    a finished run and a failed one."""
    agents = [{"status": "running", "started": 1000 + i} for i in range(n)]
    return agents + [{"status": "done"}, {"status": "failed"}]


class TitleBehaviour(unittest.TestCase):
    """What the tab actually says, run as the browser would run it."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — tab title unrun")
        cls.src = _harness()

    def titles(self, frames: list) -> list:
        """document.title after each frame — a frame being the state and
        view the page would render — with every write recorded, so a title
        assigned twice with the same string shows up as two entries."""
        script = (self.src + """
var writes = [];
var document = { set title(v) { writes.push(v); }, get title() {
  return writes.length ? writes[writes.length - 1] : null; } };
var S = {};
for (const frame of """ + json.dumps(frames) + """) {
  S = { state: frame.state, view: frame.view || 'board' };
  renderTitle();
}
console.log(JSON.stringify(writes));
""")
        out = subprocess.run([self.node, "-e", script],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def title(self, state: dict, view: str = "board") -> str:
        """The one title a single frame produces."""
        writes = self.titles([{"state": state, "view": view}])
        self.assertEqual(len(writes), 1, f"expected one write, got {writes}")
        return writes[0]

    def test_a_quiet_board_is_the_plain_title(self):
        """Byte for byte what it was before this existed — no prefix, no
        separator, no space. A board with nothing running looks untouched."""
        self.assertEqual(self.title({"project": "bench", "agents": []}),
                         "bench · bench")
        self.assertEqual(
            self.title({"project": "bench", "agents": _running(0)}),
            "bench · bench")
        self.assertEqual(self.title({"project": "bench"}), "bench · bench")

    def test_the_count_leads_the_title(self):
        """Ahead of the project, because a tab truncated to a few characters
        must still answer the question this card exists for."""
        title = self.title({"project": "bench", "agents": _running(3)})
        self.assertTrue(title.startswith("3"),
                        f"the count must lead the title, got {title!r}")
        self.assertEqual(title, "3◌ · bench · bench")

    def test_one_agent_reads_as_one(self):
        """A count and a mark rather than a word, so "1 agents" is not a
        string this code can produce."""
        title = self.title({"project": "bench", "agents": _running(1)})
        self.assertEqual(title, "1◌ · bench · bench")
        self.assertNotIn("agents", title)

    def test_only_running_agents_are_counted(self):
        """The same filter the header chip applies — see the wiring test
        below for why they cannot drift apart."""
        state = {"project": "bench",
                 "agents": [{"status": "done"}, {"status": "failed"},
                            {"status": "running"}]}
        self.assertEqual(self.title(state), "1◌ · bench · bench")

    def test_the_project_still_names_which_bench_this_is(self):
        """The thing the tab already did well survives the prefix."""
        a = self.title({"project": "projectA", "agents": _running(2)})
        b = self.title({"project": "projectB", "agents": _running(2)})
        self.assertEqual(a, "2◌ · projectA · bench")
        self.assertNotEqual(a, b)

    def test_every_view_carries_the_count(self):
        """It describes the board, not the view, so switching views changes
        only the view word."""
        state = {"project": "bench", "agents": _running(2)}
        self.assertEqual(
            [self.title(state, v) for v in ("board", "flight", "focus")],
            ["2◌ · bench · bench", "2◌ · bench · sessions",
             "2◌ · bench · focus"])

    def test_a_finished_run_returns_the_tab_to_the_plain_title(self):
        """No reload: the frame after the last agent stops is a plain
        title again."""
        busy = {"project": "bench", "agents": _running(1)}
        quiet = {"project": "bench", "agents": _running(0)}
        self.assertEqual(
            self.titles([{"state": busy}, {"state": quiet}]),
            ["1◌ · bench · bench", "bench · bench"])

    def test_a_stateless_page_keeps_the_served_title(self):
        """Before state arrives there is nothing better to say than what the
        server rendered, so nothing is written at all."""
        self.assertEqual(self.titles([{"state": None}, {"state": {}}]), [])

    def test_the_title_is_written_only_when_it_changed(self):
        """render() runs on every SSE frame; a burst with nothing moving
        must not touch document.title once."""
        busy = {"project": "bench", "agents": _running(2)}
        frames = [{"state": busy} for _ in range(20)]
        self.assertEqual(self.titles(frames), ["2◌ · bench · bench"])

    def test_a_changed_count_is_written_again(self):
        """The cache is a cache, not a latch: every distinct title lands,
        including a return to one seen before."""
        def frame(n, view="board"):
            return {"state": {"project": "bench", "agents": _running(n)},
                    "view": view}
        self.assertEqual(
            self.titles([frame(1), frame(1), frame(2), frame(2),
                         frame(2, "focus"), frame(0), frame(1)]),
            ["1◌ · bench · bench", "2◌ · bench · bench",
             "2◌ · bench · focus", "bench · bench", "1◌ · bench · bench"])


class Wiring(unittest.TestCase):
    """board.html's own half: what keeps the tab and the header chip from
    counting two different things, and the mark from becoming an emoji."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_one_filter_answers_how_many_are_running(self):
        """Both counters go through runningAgents(); a second inline filter
        is how the tab and the chip would start disagreeing. (Asking which
        agent holds one card is a different question and stays where it
        is.)"""
        filters = re.findall(r"\.filter\(a => a\.status === 'running'\)",
                             self.html)
        self.assertEqual(len(filters), 1,
                         "only runningAgents() may filter for running agents, "
                         f"found {len(filters)} such filters")
        body = re.search(r"function runningAgents\(\) \{(.*?)\n\}",
                         self.html, re.DOTALL)
        self.assertIsNotNone(body, "board.html lost runningAgents()")
        self.assertIn("a.status === 'running'", body.group(1))

    def test_the_header_chip_counts_that_same_list(self):
        chip = re.search(r"function renderChip\(\) \{(.*?)\n\}",
                         self.html, re.DOTALL)
        self.assertIsNotNone(chip, "board.html lost renderChip()")
        self.assertIn("runningAgents()", chip.group(1))

    def test_the_tab_counts_that_same_list(self):
        title = re.search(r"function renderTitle\(\) \{(.*?)\n\}",
                          self.html, re.DOTALL)
        self.assertIsNotNone(title, "board.html lost renderTitle()")
        self.assertIn("runningAgents()", title.group(1))

    def test_the_mark_is_the_boards_own_in_flight_glyph(self):
        """Not an emoji: the same ◌ the CI and copilot chips wear while
        something is in flight."""
        mark = re.search(r"const WORKING_MARK = '([^']+)';", self.html)
        self.assertIsNotNone(mark, "board.html lost WORKING_MARK")
        self.assertEqual(mark.group(1), "◌")
        self.assertGreater(len(re.findall("◌", self.html)), 1,
                           "the mark should already be in use on the chips")

    def test_the_prefix_is_the_only_thing_ahead_of_the_project(self):
        """One writer, and everything it can put before the project name is
        the count and the mark — no view name, no generic string."""
        writes = re.findall(r"document\.title\s*=\s*([^\n;]+)", self.html)
        self.assertEqual(len(writes), 1,
                         f"expected one document.title assignment, got {writes}")
        built = re.search(r"function tabTitle\(project, view, working\) \{"
                          r"(.*?)\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(built, "board.html lost tabTitle()")
        before, _, after = built.group(1).partition("project +")
        self.assertTrue(after, "tabTitle must put the project in the title")
        self.assertNotIn("VIEW_TITLES", before,
                         "the view name may never precede the project")
        self.assertIn("working", before,
                      "only the running count precedes the project")


if __name__ == "__main__":
    unittest.main()
