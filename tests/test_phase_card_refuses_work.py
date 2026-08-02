"""A work agent must refuse a phase card (task 54).

`▸ run phase` guards its own door — a card that is not a phase is refused
there. This is the other half of that gate, on the neighbour's door: a
phase card's body is a list of other cards, so a work agent handed one
implements the table of contents, which is exactly what happened the first
time a phase reached the board. The refusal is a server rule because the
page that offers one action or the other can be stale or bypassed.

Which headless kinds a phase card may host is *decided* here rather than
left to omission, so each of the four has a case saying which it is.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import phases  # noqa: E402
import state  # noqa: E402

from tests.test_phase_runs import (ONE, PHASE, PHASE_BRANCH, PhaseCase,  # noqa: E402
                                   card, git, wait_for)

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")

ALONE = "77-alone.md"
PR_URL = "https://github.com/acme/widget/pull/7"

# Reads, says something, writes nothing — what a read-only kind does.
REPORTS = """#!/usr/bin/env python3
print("RELEVANCE REVIEW: Still relevant")
"""

# Commits, then waits for a file beside the worktrees directory before
# exiting — a run held open for as long as a test needs it in flight.
BLOCKS = """#!/usr/bin/env python3
import os, subprocess, time
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "work.txt"), "w").write("work\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "work"], check=True)
go = os.path.join(os.path.dirname(os.path.dirname(cwd)), "worktrees", "go")
for _ in range(1200):
    if os.path.exists(go):
        break
    time.sleep(0.05)
print("WORK REPORT: the work is committed")
"""


class LaunchCase(PhaseCase):
    """PhaseCase's world — a real repo, a real adapter, a real phase card in
    in-progress/ — with the launches aimed at the card rather than the run."""

    def only_in(self, filename: str, text: str, stage: str) -> None:
        """One card, one stage — a rewrite that moves it rather than
        leaving the board reading the same number twice."""
        for slug in config.STAGE_DIRS:
            path = self.tasks / slug / filename
            if path.is_file():
                path.unlink()
        self.write(filename, text, stage)

    def ordinary(self, stage: str = "in-progress", **kw) -> None:
        """A card that is not a phase, in the stage a launch wants it in."""
        self.only_in(ALONE, card("77 — On its own",
                                 status=config.STAGE_LABELS[stage], **kw), stage)

    def as_phase(self, filename: str, stage: str, listed: str | None = None) -> None:
        """The same card, now typed Phase — what a person writes when they
        decide a card coordinates rather than builds."""
        self.only_in(filename, card("77 — On its own", kind="Phase",
                                    status=config.STAGE_LABELS[stage],
                                    cards=listed), stage)

    def with_pr(self, filename: str, title: str, stage: str, **kw) -> None:
        text = card(title, status=config.STAGE_LABELS[stage], **kw)
        self.only_in(filename, text.replace(
            "**Priority:** High\n",
            f"**Priority:** High\n**PR:** {PR_URL}\n"), stage)

    def running(self) -> list[dict]:
        return [r for r in state.AGENTS.values() if r["status"] == "running"]

    def worktrees(self) -> list[str]:
        return [line.split(" ", 1)[1] for line in
                git(self.repo, "worktree", "list", "--porcelain").stdout.splitlines()
                if line.startswith("worktree ")]


class TheWorkAgentRefusesAPhaseCard(LaunchCase):
    def test_a_phase_card_is_refused_and_told_what_to_run_instead(self):
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(PHASE, "in-progress")

        self.assertIn("▸ run phase", str(caught.exception))
        self.assertIn("phase card", str(caught.exception))

    def test_the_refusal_leaves_nothing_behind(self):
        """Refused with `_validate`, so it costs nothing: no branch, no
        worktree, no process, nothing for anyone to clean up."""
        with self.assertRaises(ValueError):
            agents.start_agent(PHASE, "in-progress")

        self.assertFalse(self.branch_exists(f"task/{PHASE[:-3]}"))
        self.assertFalse((config.WORKTREES / PHASE[:-3]).exists())
        self.assertEqual(self.worktrees(), [str(self.repo)])
        self.assertEqual(state.AGENTS, {}, "no run was ever recorded")

    def test_it_refuses_ahead_of_the_claim(self):
        """The order the shape depends on: a refusal must not write the card
        it refused. In team mode a launch claims an unheld card, so an
        assignee appearing here would mean the guard ran too late."""
        self.patch(COMMIT_MOVES=True)

        with self.assertRaises(ValueError):
            agents.start_agent(PHASE, "in-progress")

        self.assertNotIn("**Assignee:**", self.text(PHASE))

    def test_a_takeover_is_refused_just_the_same(self):
        """The deliberate second click reassigns a card; it does not make a
        table of contents into a brief."""
        self.patch(COMMIT_MOVES=True)

        with self.assertRaises(ValueError) as caught:
            agents.start_agent(PHASE, "in-progress", takeover=True)

        self.assertIn("▸ run phase", str(caught.exception))
        self.assertNotIn("**Assignee:**", self.text(PHASE))

    def test_a_phase_card_with_no_list_yet_is_still_a_phase_card(self):
        """`**Type:** Phase` is the whole of the reading, the same one
        `phases._phase_card` refuses a non-phase by. A list not written yet
        is an authoring mistake to fix, not an invitation to build it."""
        self.as_phase(ALONE, "in-progress")

        with self.assertRaises(ValueError) as caught:
            agents.start_agent(ALONE, "in-progress")

        self.assertIn("▸ run phase", str(caught.exception))

    def test_an_ordinary_card_starts_work_exactly_as_it_did(self):
        self.ordinary()

        agent = agents.start_agent(ALONE, "in-progress")
        self.settle()

        self.assertEqual(agent["branch"], f"task/{ALONE[:-3]}")
        self.assertTrue(self.branch_exists(f"task/{ALONE[:-3]}"))
        self.assertEqual(self.stage_of(ALONE), "review",
                         "the ordinary path is untouched: commits, then review/")

    def test_the_two_gates_are_mirror_images(self):
        """The bug was an asymmetry, so the symmetry is the test: each door
        refuses the card the other one is for, and says so."""
        self.ordinary()

        with self.assertRaises(ValueError) as work:
            agents.start_agent(PHASE, "in-progress")
        with self.assertRaises(ValueError) as run:
            phases.start_phase(ALONE, "in-progress")

        self.assertIn("phase card", str(work.exception))
        self.assertIn("not a phase", str(run.exception))
        self.assertEqual(state.AGENTS, {})
        self.assertFalse(self.branch_exists(f"phase/{ALONE[:-3]}"))


class ARunInFlightIsLeftAlone(LaunchCase):
    """The guard is about starting. A card retyped under a running agent is
    a person's edit, not a reason to break the run underneath it."""

    def test_a_card_that_becomes_a_phase_mid_run_still_lands(self):
        self.ordinary()
        self.adapter_is(BLOCKS)
        go = config.WORKTREES / "go"

        agents.start_agent(ALONE, "in-progress")
        self.assertTrue(
            wait_for(lambda: (config.WORKTREES / ALONE[:-3] / "work.txt").is_file()),
            "the agent never got as far as its commit")
        # the edit, while the process is alive and the reaper has not run
        self.as_phase(ALONE, "in-progress")
        self.assertTrue(self.running(), "the run ended before the edit landed")
        go.write_text("done\n", encoding="utf-8")
        self.settle()

        record = next(iter(state.AGENTS.values()))
        self.assertEqual(record["rc"], 0)
        self.assertEqual(record["status"], "done", "the run ended as it would have")
        self.assertEqual(self.stage_of(ALONE), "review",
                         "and the card moved on, phase line or not")

    def tearDown(self):
        # never leave a blocked adapter behind if an assertion jumped the wire
        (config.WORKTREES / "go").parent.mkdir(parents=True, exist_ok=True)
        (config.WORKTREES / "go").write_text("done\n", encoding="utf-8")
        wait_for(lambda: not self.running(), timeout=10)


class WhichKindsAPhaseCardMayHost(LaunchCase):
    """Four kinds, four deliberate answers — the two that would work on the
    card refuse it, the two that only read it are allowed."""

    def test_still_true_is_allowed_on_a_phase_card(self):
        self.adapter_is(REPORTS)

        agent = agents.start_review(PHASE, "in-progress")
        self.assertTrue(wait_for(lambda: not self.running()))

        self.assertEqual(agent["mode"], "review")
        self.assertIsNone(agent["worktree"], "read-only: nothing is cut for it")
        self.assertIn("Relevance review", self.text(PHASE))

    def test_reviewing_the_phase_pr_is_allowed_and_names_the_phase_branch(self):
        """The prompt asks GitHub for the diff by branch, and a phase's PR
        is from its own branch — `task/<stem>` was never cut."""
        self.adapter_is(REPORTS)
        self.with_pr(PHASE, "40 — Ship the site", "review", kind="Phase",
                     cards="- 31 — Stand up site/\n")

        agent = agents.start_pr_review(PHASE, "review")
        self.assertTrue(wait_for(lambda: not self.running()))

        self.assertEqual(agent["branch"], PHASE_BRANCH)

    def test_an_ordinary_cards_pr_review_still_names_its_task_branch(self):
        self.adapter_is(REPORTS)
        self.with_pr(ONE, "31 — Stand up site/", "review")

        agent = agents.start_pr_review(ONE, "review")
        self.assertTrue(wait_for(lambda: not self.running()))

        self.assertEqual(agent["branch"], f"task/{ONE[:-3]}")

    def test_acting_on_a_phase_pr_is_refused_and_says_where_it_belongs(self):
        """↻ act on PR is the same work agent with a push, and a phase's PR
        carries its members' commits."""
        self.with_pr(PHASE, "40 — Ship the site", "review", kind="Phase",
                     cards="- 31 — Stand up site/\n")

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(PHASE, "review")

        self.assertIn("phase card", str(caught.exception))
        self.assertIn("member", str(caught.exception))
        self.assertFalse((config.WORKTREES / PHASE[:-3]).exists())
        self.assertEqual(state.AGENTS, {})


@unittest.skipUnless(NODE, "node is needed to run the page's own rules")
class TheCardSaysWhichStateItIsIn(unittest.TestCase):
    """A phase card in in-progress/ that nobody has started used to look
    exactly like one mid-run. These run the page's own function over the
    snapshots the server sends."""

    PARTS = (r"function phaseProgress\(p\) \{.*?\n\}",
             r"function phaseFlight\(task\) \{.*?\n\}")

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        parts = []
        for pattern in cls.PARTS:
            match = re.search(pattern, cls.html, re.S)
            if match is None:
                raise AssertionError(f"board.html no longer defines {pattern!r}")
            parts.append(match.group(0))
        cls.src = "\n".join(parts)
        cls.card_fn = re.search(r"function cardFor\(task\) \{.*?\n\}",
                                cls.html, re.S).group(0)

    def flight(self, snapshot: dict | None, stage: str = "in-progress") -> object:
        task = {"file": PHASE, "stage": stage, "isPhase": True}
        script = (self.src + "\nvar S = { state: { phases: "
                  + json.dumps({PHASE: snapshot} if snapshot else {})
                  + " } };\nconsole.log(JSON.stringify(phaseFlight("
                  + json.dumps(task) + ")));\n")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def snapshot(self, **extra) -> dict:
        return {"file": PHASE, "members": [], "halted": None, "haltedAt": None,
                "haltedWhy": None, "running": False, "stopped": False,
                "started": False, **extra}

    def test_a_phase_nobody_has_started_says_so(self):
        flight = self.flight(self.snapshot())

        self.assertTrue(flight["idle"], "nothing is happening, so nothing breathes")
        self.assertEqual(flight["pill"], "not started")
        self.assertIn("▸ run phase", flight["line"])

    def test_a_held_phase_is_its_own_state(self):
        flight = self.flight(self.snapshot(started=True, stopped=True))

        self.assertTrue(flight["idle"])
        self.assertEqual(flight["pill"], "held")

    def test_a_running_phase_is_unchanged(self):
        flight = self.flight(self.snapshot(
            started=True, running=True,
            members=[{"number": "31", "file": ONE, "title": "Stand up site/",
                      "state": "running"}]))

        self.assertFalse(flight.get("idle"), "a run is work: it keeps the accent")
        self.assertIn("on #31", flight["line"])

    def test_a_halted_phase_is_unchanged(self):
        flight = self.flight(self.snapshot(started=True, halted="31: its CI is red",
                                           haltedAt="31", haltedWhy="its CI is red"))

        self.assertTrue(flight["bad"])
        self.assertFalse(flight.get("idle"))

    def test_a_phase_the_runner_has_not_read_says_nothing(self):
        """Before the first beat there is no snapshot, and a card that
        guessed would be worse than one that waits."""
        self.assertIsNone(self.flight(None))

    def test_a_settled_phase_card_is_not_told_it_never_started(self):
        """review/ and done/ are past the question — the card there is
        waiting on a person, which its stage already says."""
        self.assertIsNone(self.flight(self.snapshot(started=True), stage="review"))
        self.assertIsNone(self.flight(self.snapshot(started=True), stage="done"))

    def test_idle_wears_the_settled_register_and_never_the_accent(self):
        block = re.search(r"if \(flight && flight\.idle\) \{.*?\n  \}",
                          self.card_fn, re.S).group(0)
        self.assertIn("var(--idle)", block)
        for colour in ("--accent", "--alarm", "--calm"):
            self.assertNotIn(colour, block,
                             "not started is neither work, an alarm nor a verdict")
        self.assertIn("!flight.bad && !flight.idle", self.card_fn,
                      "an unstarted phase must not read as an agent working")
        self.assertIn("flight.bad || flight.idle ? '' :", self.card_fn,
                      "…and nothing is still arriving, so there is no caret")

    def test_the_halt_and_the_failure_still_outrank_it(self):
        idle = self.card_fn.index("if (flight && flight.idle)")
        self.assertLess(idle, self.card_fn.index("if (failure)"))
        self.assertLess(idle, self.card_fn.index("if (flight && flight.bad)"))


class TheDocumentedGuard(unittest.TestCase):
    """The doctrine this card restores: the file-carried gates exist because
    a UI layer can be stale, so AGENTS.md has to say this one is there."""

    @classmethod
    def setUpClass(cls):
        cls.doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        # the doc is hard-wrapped, so read it as the sentence it is
        cls.flat = re.sub(r"\s+", " ", cls.doc.replace("**", ""))

    def test_the_refusal_is_written_down_with_the_action_it_names(self):
        self.assertIn("It also refuses a phase card", self.flat)
        self.assertIn("the refusal names ▸ run phase", self.flat)

    def test_the_kinds_a_phase_card_may_host_are_named(self):
        self.assertIn("▸ start work and ↻ act on PR refuse it", self.flat)
        self.assertIn("◔ still true? and ◔ review PR", self.flat)

    def test_the_unstarted_card_is_described(self):
        self.assertIn("`not started`", self.doc)


if __name__ == "__main__":
    unittest.main()
