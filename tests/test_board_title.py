"""The tab names its project (task 22): the title is "<project> · bench",
project first, so two benches side by side are told apart at tab-bar width.

Two halves are tested here. The server half — config resolving the project
name and httpd rendering it into the served page — runs in fresh
interpreters, because config reads its settings at import and BOARD_TITLE
is the thing under test. The browser half lives in board.html's inline JS
with no frontend test runner, so it is checked as source-level invariants:
the ones that, if broken, would let the tab drift back to a generic string
or put the view name before the project.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
BOARD = CORE / "board.html"

# BOARD_TITLE unset in the process environment is "nothing configured":
# process env beats local/.env, so this also neutralizes a developer's own
# override leaking into the defaults test.
UNSET = {"BOARD_TITLE": ""}


def _probe(expression: str, settings: dict) -> object:
    """Evaluate an expression against config/httpd in a fresh interpreter,
    with the given settings in the environment config reads at import."""
    env = dict(os.environ)
    env.update(UNSET)
    env.update(settings)
    out = subprocess.check_output(
        [sys.executable, "-c",
         "import sys, json; sys.path.insert(0, sys.argv[1]); "
         "import config, httpd; print(json.dumps(eval(sys.argv[2])))",
         str(CORE), expression],
        env=env, text=True)
    return json.loads(out)


def _title_of(settings: dict) -> str:
    """The <title> text of the page the board would serve."""
    page = _probe("httpd.page_bytes().decode('utf-8')", settings)
    match = re.search(r"<title>(.*?)</title>", page, re.DOTALL)
    assert match, "the served page lost its <title>"
    return match.group(1)


class ProjectName(unittest.TestCase):
    def test_defaults_to_the_repo_directory_name(self):
        """The name nobody has to configure: what the checkout is called."""
        self.assertEqual(_probe("config.PROJECT", {}), REPO.name)

    def test_board_title_overrides_it(self):
        """For people whose checkout directories are all called "app"."""
        self.assertEqual(_probe("config.PROJECT", {"BOARD_TITLE": "payments"}),
                         "payments")

    def test_a_blank_setting_is_not_a_blank_title(self):
        """An empty or whitespace-only value means "not configured", not
        "call this board nothing"."""
        self.assertEqual(_probe("config.PROJECT", {"BOARD_TITLE": "   "}),
                         REPO.name)

    def test_the_state_payload_carries_it(self):
        """The browser needs it too — the view switcher rewrites the title
        without refetching the page."""
        self.assertEqual(_probe("httpd.state_payload()['project']",
                                {"BOARD_TITLE": "payments"}), "payments")


class ServedTitle(unittest.TestCase):
    def test_the_title_is_rendered_into_the_page(self):
        """Server-rendered, so the tab is right on first paint rather than
        flickering from generic to named on every refresh."""
        self.assertEqual(_title_of({"BOARD_TITLE": "payments"}),
                         "payments · bench")

    def test_two_projects_get_two_titles(self):
        """The whole point: distinguishable in the tab bar, in cmd-tab and
        in history — and distinguishable by their *first* word."""
        a = _title_of({"BOARD_TITLE": "projectA"})
        b = _title_of({"BOARD_TITLE": "projectB"})
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("projectA") and b.startswith("projectB"),
                        f"the project must lead the title, got {a!r} / {b!r}")

    def test_the_default_title_names_this_checkout(self):
        self.assertEqual(_title_of({}), f"{REPO.name} · bench")

    def test_a_project_name_cannot_inject_markup(self):
        """The name comes from a directory or a settings file, both of which
        can hold anything — it is escaped, not spliced."""
        title = _title_of({"BOARD_TITLE": "<script>x</script>"})
        self.assertNotIn("<script>", title)
        self.assertIn("&lt;script&gt;", title)

    def test_nothing_else_in_the_page_is_disturbed(self):
        """Only the title element is rewritten; the rest is the file."""
        page = _probe("httpd.page_bytes().decode('utf-8')",
                      {"BOARD_TITLE": "payments"})
        source = BOARD.read_text(encoding="utf-8")
        strip = lambda s: re.sub(r"<title>.*?</title>", "", s, count=1,
                                 flags=re.DOTALL)
        self.assertEqual(strip(page), strip(source))


class PageInvariants(unittest.TestCase):
    """board.html's own half: the fallback title and the code that keeps it
    in step with the view switcher."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_shipped_title_is_not_the_old_generic_string(self):
        """"Bench — task board" in two tabs was the bug."""
        self.assertNotIn("task board", self.html.lower())

    def test_the_title_is_only_ever_written_from_the_project(self):
        """One writer, and the string it writes is built by tabTitle() from
        the project — so no code path can revert to the generic string. That
        the project leads everything but the running count (task 39) is
        tested in test_tab_agents.py."""
        writes = re.findall(r"document\.title\s*=\s*([^\n;]+)", self.html)
        self.assertEqual(len(writes), 1,
                         f"expected one document.title assignment, got {writes}")
        body = re.search(r"function renderTitle\(\)\s*\{(.*?)\n\}",
                         self.html, re.DOTALL)
        self.assertIsNotNone(body, "board.html lost renderTitle()")
        self.assertIn("tabTitle(S.state.project", body.group(1),
                      "the title must be built from the project")

    def test_a_stateless_page_keeps_the_served_title(self):
        """Before the first state load there is nothing better to say than
        what the server already rendered."""
        body = re.search(r"function renderTitle\(\)\s*\{(.*?)\n\}",
                         self.html, re.DOTALL)
        self.assertIsNotNone(body, "board.html lost renderTitle()")
        self.assertIn("if (!S.state?.project) return;", body.group(1),
                      "renderTitle must bail out rather than write a "
                      "project-less title")

    def test_every_view_has_a_tail(self):
        """The switcher may suffix, but the board view keeps the name people
        bookmarked: "<project> · bench"."""
        table = re.search(r"const VIEW_TITLES = \{(.*?)\};", self.html, re.DOTALL)
        self.assertIsNotNone(table, "board.html lost VIEW_TITLES")
        tails = dict(re.findall(r"(\w+): '([^']+)'", table.group(1)))
        views = set(re.findall(r'data-view="(\w+)"', self.html))
        self.assertEqual(set(tails), views,
                         "every view in the switcher needs a title tail")
        self.assertEqual(tails["board"], "bench")

    def test_the_title_follows_every_render(self):
        """render() runs on state loads and on view switches alike, so
        hanging renderTitle off it covers both."""
        body = re.search(r"function render\(\)\s*\{(.*?)\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(body, "board.html lost render()")
        self.assertIn("renderTitle();", body.group(1))


if __name__ == "__main__":
    unittest.main()
