"""A board whose agents cannot run anything says so (task 46).

`BOARD_AGENT_COMMANDS` is the one setting a headless agent cannot work
around: a test runner missing from it is a test the work agent cannot run.
Since the install stopped asking for it, a project the detector does not
recognise starts with it empty — and until this card nothing mentioned that
until a run had already ended with an agent explaining it could not verify
its work.

Three places have to agree on *empty*: the splitter (core's, and the
standalone copies each adapter carries), the state payload the page reads,
and the launch that says it in the ticker. So the suite is one class each,
plus the page's own function run under node.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import agents  # noqa: E402
import config  # noqa: E402

from tests.test_phase_runs import PhaseCase, card, git  # noqa: E402

BOARD = CORE / "board.html"
NODE = shutil.which("node")
ALONE = "77-alone.md"
PR_URL = "https://github.com/acme/widget/pull/7"

# The forms of "nothing configured" a person can actually produce: never
# set, cleared, left as whitespace, or reduced to the separator.
NOTHING = ("", "   ", "\t", ",", " , ", ",,", " ,\t,")
SOMETHING = {"npm test": ["npm test"],
             " npm test ": ["npm test"],
             "npm test, make check": ["npm test", "make check"],
             "python3 -m unittest,": ["python3 -m unittest"]}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WhatCountsAsNothing(unittest.TestCase):
    """`config.agent_commands()` is the board's half of the split the
    adapters already do. If the two disagreed, a board could show a chip a
    launch contradicts, or stay silent about a run with nothing to run."""

    def commands(self, raw: str) -> list[str]:
        saved = config.AGENT_COMMANDS
        self.addCleanup(setattr, config, "AGENT_COMMANDS", saved)
        config.AGENT_COMMANDS = raw
        return config.agent_commands()

    def test_every_shape_of_empty_is_empty(self):
        for raw in NOTHING:
            self.assertEqual(self.commands(raw), [],
                             f"{raw!r} configures nothing")

    def test_a_value_survives_stripped(self):
        for raw, expected in SOMETHING.items():
            self.assertEqual(self.commands(raw), expected)

    def test_it_agrees_with_the_adapters(self):
        """Both shipped adapters carry a standalone `split_commands()` (the
        hooks run outside the board's imports). Same answers, or the board
        is guessing about somebody else's rules."""
        splitters = [
            _load("claude_hook_settings",
                  CORE / "adapters" / "claude" / "hook_settings.py").split_commands,
            _load("opencode_permission_config",
                  CORE / "adapters" / "opencode" / "permission_config.py").split_commands,
        ]
        for raw in (*NOTHING, *SOMETHING):
            for split in splitters:
                self.assertEqual(self.commands(raw), split(raw),
                                 f"core and an adapter disagree about {raw!r}")


class TheStatePayloadSaysWhichItIs(unittest.TestCase):
    """One boolean, beside `hasDriver` — the same shape of fact, answered by
    the server so the page never re-parses the setting itself."""

    def flag(self, raw: str) -> bool:
        import httpd
        saved = config.AGENT_COMMANDS
        self.addCleanup(setattr, config, "AGENT_COMMANDS", saved)
        config.AGENT_COMMANDS = raw
        return httpd.state_payload()["hasAgentCommands"]

    def test_empty_is_reported_as_empty(self):
        for raw in NOTHING:
            self.assertFalse(self.flag(raw), f"{raw!r} is nothing configured")

    def test_a_configured_board_reports_true(self):
        self.assertTrue(self.flag("npm test"))
        self.assertTrue(self.flag("python3 -m unittest"))

    def test_the_page_does_not_read_the_raw_setting(self):
        html = BOARD.read_text(encoding="utf-8")
        self.assertNotIn("state.agentCommands", html)
        self.assertIn("hasAgentCommands", html)


@unittest.skipUnless(NODE, "node is needed to run the page's own rules")
class TheHeaderChip(unittest.TestCase):
    """The indicator itself, run as the page runs it: a stub element in
    place of the DOM, a state payload in place of the server."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        match = re.search(r"function renderAgentCommands\(\) \{.*?\n\}",
                          cls.html, re.S)
        if match is None:
            raise AssertionError("board.html no longer defines renderAgentCommands")
        cls.src = match.group(0)

    def chip(self, state: dict | None) -> dict:
        script = (
            "const el = {hidden: null, title: '', innerHTML: ''};\n"
            "function $(sel) { if (sel !== '#cmdchip') "
            "throw new Error('unexpected ' + sel); return el; }\n"
            "var S = " + json.dumps({"state": state}) + ";\n"
            + self.src + "\nrenderAgentCommands();\n"
            "console.log(JSON.stringify(el));\n")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_an_empty_setting_is_said_once_naming_setting_and_file(self):
        chip = self.chip({"hasAgentCommands": False})

        self.assertFalse(chip["hidden"])
        self.assertIn("BOARD_AGENT_COMMANDS", chip["innerHTML"] + chip["title"])
        self.assertIn("manager/local/.env", chip["title"],
                      "a reader who has never opened that file has to be told "
                      "which file it is")

    def test_a_configured_board_shows_nothing_at_all(self):
        chip = self.chip({"hasAgentCommands": True})

        self.assertTrue(chip["hidden"])
        self.assertEqual(chip["innerHTML"], "",
                         "nothing is drawn, not even hidden")

    def test_a_payload_that_does_not_say_is_not_guessed_at(self):
        """An older server, or the first frame before any state: the board
        says nothing rather than accusing a project of being unconfigured."""
        self.assertTrue(self.chip(None)["hidden"])
        self.assertTrue(self.chip({})["hidden"])

    def test_it_is_not_an_alarm(self):
        """Nothing is failing — something is unconfigured. `--alarm` is
        reserved for blocked, failed or HIGH."""
        self.assertNotIn("--alarm", self.src)
        self.assertNotIn("--accent", self.src, "and nothing here is working")
        self.assertIn("var(--idle)", self.src)

    def test_the_chip_exists_and_is_drawn_every_frame(self):
        self.assertIn('id="cmdchip"', self.html)
        render = re.search(r"function render\(\) \{.*?\n\}", self.html, re.S).group(0)
        self.assertIn("renderAgentCommands();", render)


class ALaunchWithNothingToRun(PhaseCase):
    """The sharper half: the moment it matters is the launch. It is a note
    in the ticker beside the run's own line — never a refusal, because an
    agent that only edits files is still useful."""

    SETTING = "BOARD_AGENT_COMMANDS in manager/local/.env"

    def setUp(self):
        super().setUp()
        self.write(ALONE, card("77 — On its own", status="In Progress"),
                   "in-progress")

    def said(self) -> list[str]:
        return [s for s in self.summaries() if self.SETTING in s]

    def test_a_work_launch_says_it_and_still_runs(self):
        self.patch(AGENT_COMMANDS="")

        agents.start_agent(ALONE, "in-progress")
        self.settle()

        self.assertEqual(len(self.said()), 1, self.summaries())
        self.assertIn("cannot run this project's tests", self.said()[0])
        self.assertEqual(self.stage_of(ALONE), "review",
                         "the launch was not blocked: it ran, committed and landed")

    def test_a_configured_board_never_mentions_it(self):
        self.patch(AGENT_COMMANDS="npm test")

        agents.start_agent(ALONE, "in-progress")
        self.settle()

        self.assertEqual(self.said(), [])
        self.assertFalse([s for s in self.summaries()
                          if "BOARD_AGENT_COMMANDS" in s])

    def test_whitespace_and_a_lone_comma_count_as_nothing_here_too(self):
        for raw in (" ", ","):
            with self.subTest(raw=raw):
                self.patch(AGENT_COMMANDS=raw)
                self.assertIsNotNone(agents._no_commands_note())

    def test_the_note_rides_beside_the_branch_point_note(self):
        """Two things worth saying about one launch, one line: the note is
        appended, it does not replace what the launch already said."""
        self.patch(AGENT_COMMANDS="")

        agents.start_agent(ALONE, "in-progress")
        self.settle()

        line = self.said()[0]
        self.assertIn(f"started on {ALONE}", line)
        self.assertIn(f"task/{ALONE[:-3]}", line)

    def test_acting_on_a_pr_says_it_too(self):
        """↻ act on PR is a work agent with a push — same intent, same
        commands, same silence to break."""
        self.patch(AGENT_COMMANDS="")
        text = card("77 — On its own", status="Review").replace(
            "**Priority:** High\n", f"**Priority:** High\n**PR:** {PR_URL}\n")
        (self.tasks / "in-progress" / ALONE).unlink()
        self.write(ALONE, text, "review")
        git(self.repo, "branch", f"task/{ALONE[:-3]}")

        agents.start_pr_fix(ALONE, "review")
        self.settle()

        self.assertEqual(len(self.said()), 1, self.summaries())
        self.assertIn("acting on the review", self.said()[0])

    def test_a_read_only_kind_says_nothing(self):
        """`◔ still true?` never had the project's commands: telling it
        about them would be noise on every card in every stage."""
        self.patch(AGENT_COMMANDS="")
        self.adapter_is("#!/usr/bin/env python3\n"
                        "print('RELEVANCE REVIEW: Still relevant')\n")

        agents.start_review(ALONE, "in-progress")
        self.settle()

        self.assertEqual(self.said(), [])


class TheDocumentedTrade(unittest.TestCase):
    """The install stopped asking on purpose; AGENTS.md carries the reason,
    so it has to carry what the board now does about it."""

    @classmethod
    def setUpClass(cls):
        doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        cls.flat = re.sub(r"\s+", " ", doc.replace("**", "").replace("`", ""))

    def test_the_indicator_is_written_down(self):
        self.assertIn("no agent commands", self.flat)
        self.assertIn("BOARD_AGENT_COMMANDS", self.flat)

    def test_it_says_the_launch_is_not_blocked(self):
        self.assertIn("never refuses a launch", self.flat)


if __name__ == "__main__":
    unittest.main()
