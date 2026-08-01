"""Adding a card to a phase without opening the file (task 51).

The action writes one line into the phase card and nothing at all into the
card being added — membership lives in one place (48) and joining a phase
moves nothing. So these cases hold three things: what the line looks like
and where it lands, what the board refuses (a running phase, a card
already in one, a phase card), and that the write reaches git the way
every other board-made write to a task file does.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

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
import state  # noqa: E402
import taskfiles  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")


def card(title: str, *, kind: str | None = None, cards: str | None = None,
         status: str = "Backlog") -> str:
    """A task file as a person would write it."""
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** Medium\n"
    if kind:
        text += f"**Type:** {kind}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}"
    return text


class AddingCase(unittest.TestCase):
    """One throwaway tasks/ directory per test, read the way the board reads
    it. Nothing here is a git repo — the commit gate has its own case."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-join-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tasks = tmp / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
        self.patch(TASKS=self.tasks, TM_ROOT=tmp, COMMIT_MOVES=False)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def read(self, filename: str, stage: str = "to-do") -> str:
        return (self.tasks / stage / filename).read_text(encoding="utf-8")

    def board(self) -> dict[str, dict]:
        return {task["file"]: task
                for stage in taskfiles.collect()["stages"]
                for task in stage["tasks"]}

    def phase(self, cards: str | None = None, stage: str = "to-do",
              filename: str = "40-the-site.md") -> str:
        self.write(filename, card("40 — Ship the site", kind="Phase", cards=cards),
                   stage)
        return filename


class TheLineItWrites(AddingCase):
    def test_the_card_lands_at_the_end_of_the_list(self):
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.phase(cards="- 31 — Stand up site/\n")

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        self.assertIn("- 31 — Stand up site/\n- 51 — The afterthought\n",
                      self.read("40-the-site.md"))

    def test_the_line_reads_the_way_a_person_writes_it(self):
        """`- 51 — The afterthought`: the number the list is parsed by, and
        the title without the copy of the number it opens with."""
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        self.phase(cards="")

        result = taskfiles.add_to_phase("51-the-afterthought.md", "backlog",
                                        "40-the-site.md")

        self.assertEqual(result["line"], "- 51 — The afterthought")
        self.assertEqual(result["entry"], "51 — The afterthought",
                         "the same thing without the bullet, for the ticker")

    def test_a_title_that_does_not_repeat_its_number_is_used_whole(self):
        self.write("51-the-afterthought.md", card("The afterthought"))
        self.phase(cards="")

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        self.assertIn("- 51 — The afterthought", self.read("40-the-site.md"))

    def test_the_phase_reads_the_new_card_as_its_last_member(self):
        """The point of the whole action: the phase's list resolves and the
        card wears the member chip 48 gives it."""
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        self.phase(cards="- 31 — Stand up site/\n")

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        cards = self.board()
        self.assertEqual([m["file"] for m in cards["40-the-site.md"]["members"]],
                         ["31-stand-up-site.md", "51-the-afterthought.md"])
        self.assertEqual(cards["51-the-afterthought.md"]["phase"]["index"], 2)
        self.assertEqual(cards["51-the-afterthought.md"]["phase"]["total"], 2)
        self.assertEqual(cards["40-the-site.md"]["phaseDrift"], [])

    def test_a_card_in_to_do_joins_from_where_it_stands(self):
        self.write("51-the-afterthought.md", card("51 — The afterthought",
                                                  status="To Do"), "to-do")
        self.phase(cards="")

        taskfiles.add_to_phase("51-the-afterthought.md", "to-do", "40-the-site.md")

        self.assertEqual(self.board()["51-the-afterthought.md"]["phase"]["index"], 1)

    def test_nothing_else_moves(self):
        """Joining a phase is not a commitment to start it: the card stays in
        its stage and says nothing at all about the phase that now holds it."""
        original = card("51 — The afterthought")
        self.write("51-the-afterthought.md", original)
        self.phase(cards="")

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        self.assertEqual(self.read("51-the-afterthought.md", "backlog"), original)
        self.assertTrue((self.tasks / "backlog" / "51-the-afterthought.md").is_file())

    def test_the_rest_of_the_phase_card_is_untouched(self):
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        text = card("40 — Ship the site", kind="Phase", cards="- 31 — Stand up site/\n") \
            + "\n## Notes\n\nWhy this phase exists.\n"
        self.write("40-the-site.md", text, "to-do")
        self.write("31-stand-up-site.md", card("31 — Stand up site/"))

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        after = self.read("40-the-site.md")
        self.assertIn("# 40 — Ship the site", after)
        self.assertIn("**Type:** Phase", after)
        self.assertIn("## Notes\n\nWhy this phase exists.\n", after)
        self.assertIn("- 31 — Stand up site/\n- 51 — The afterthought\n", after)
        self.assertEqual(self.board()["40-the-site.md"]["phaseDrift"], [])

    def test_a_phase_with_no_cards_section_gains_one(self):
        """Not appended to the end of the file as loose prose: the section
        is what the list is, so it is created."""
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        self.phase()   # written with no ## Cards section at all

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")

        after = self.read("40-the-site.md")
        self.assertIn("## Cards\n\n- 51 — The afterthought", after)
        self.assertEqual([m["number"] for m in self.board()["40-the-site.md"]["members"]],
                         ["51"])

    def test_the_second_add_reads_the_file_as_it_is_on_disk(self):
        """Two boards adding to one phase produce two lines, not a lost one:
        the append re-reads the card rather than trusting a render."""
        self.write("51-the-afterthought.md", card("51 — The afterthought"))
        self.write("52-the-other-one.md", card("52 — The other one"))
        self.phase(cards="")
        stale = self.read("40-the-site.md")

        taskfiles.add_to_phase("51-the-afterthought.md", "backlog", "40-the-site.md")
        # the other board wrote in between; this one never saw it
        self.assertNotIn("51", stale)
        taskfiles.add_to_phase("52-the-other-one.md", "backlog", "40-the-site.md")

        self.assertEqual([m["number"] for m in self.board()["40-the-site.md"]["members"]],
                         ["51", "52"])


class WhatItRefuses(AddingCase):
    """Each refusal is a state the action is not offered in either — the
    server is the backstop behind a card face that went stale."""

    def setUp(self):
        super().setUp()
        self.write("51-the-afterthought.md", card("51 — The afterthought"))

    def add(self, phase_file: str = "40-the-site.md", stage: str = "backlog") -> str:
        with self.assertRaises(ValueError) as caught:
            taskfiles.add_to_phase("51-the-afterthought.md", stage, phase_file)
        return str(caught.exception)

    def test_a_phase_that_is_running_takes_nothing(self):
        """in-progress/ means the branch exists and the members are being
        worked in the order the list had when it started."""
        self.phase(cards="", stage="in-progress")

        self.assertIn("to-do", self.add())

    def test_a_phase_in_review_or_done_takes_nothing(self):
        for stage in ("review", "done"):
            with self.subTest(stage=stage):
                self.phase(cards="", stage=stage, filename=f"4{stage[0]}-late.md")
                self.assertIn("to-do", self.add(f"4{stage[0]}-late.md"))

    def test_a_card_that_is_not_a_phase_takes_nothing(self):
        self.write("40-ordinary.md", card("40 — An ordinary card", kind="Feature",
                                          cards=""), "to-do")

        self.assertIn("not a phase", self.add("40-ordinary.md"))

    def test_a_card_already_in_this_phase_is_not_listed_twice(self):
        self.phase(cards="- 51 — The afterthought\n")

        self.assertIn("already", self.add())

    def test_a_card_another_phase_holds_stays_where_it_is(self):
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards="- 51 — The afterthought\n"), "to-do")
        self.phase(cards="")

        message = self.add()

        self.assertIn("41", message)
        self.assertNotIn("51", self.read("40-the-site.md"))

    def test_a_phase_does_not_join_a_phase(self):
        self.write("41-the-docs.md", card("41 — Ship the docs", kind="Phase",
                                          cards=""), "backlog")
        self.phase(cards="")

        with self.assertRaises(ValueError) as caught:
            taskfiles.add_to_phase("41-the-docs.md", "backlog", "40-the-site.md")

        self.assertIn("nest", str(caught.exception))

    def test_a_card_at_work_does_not_join(self):
        """The action lives on the two unstarted stages; the stages past
        them are refused rather than quietly accepted."""
        self.phase(cards="")
        for stage in ("in-progress", "review", "done"):
            with self.subTest(stage=stage):
                self.write("51-the-afterthought.md", card("51 — The afterthought"),
                           stage)
                self.assertIn("backlog", self.add(stage=stage))

    def test_a_card_with_no_number_says_so(self):
        self.write("a-nameless-card.md", card("A nameless card"))
        self.phase(cards="")

        with self.assertRaises(ValueError) as caught:
            taskfiles.add_to_phase("a-nameless-card.md", "backlog", "40-the-site.md")

        self.assertIn("number", str(caught.exception))

    def test_a_card_that_has_moved_since_the_page_rendered(self):
        self.phase(cards="")

        with self.assertRaises(ValueError) as caught:
            taskfiles.add_to_phase("51-the-afterthought.md", "to-do", "40-the-site.md")

        self.assertIn("refresh the board", str(caught.exception))

    def test_a_path_is_not_a_filename(self):
        self.phase(cards="")

        for bad in ("../../etc/passwd.md", "to-do/40-the-site.md"):
            with self.subTest(name=bad):
                self.assertIn("bad filename", self.add(bad))


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class TheCommitGate(unittest.TestCase):
    """An addition that never leaves one working tree is not an addition the
    phase will run — so it commits like every other board-made write, and
    with the gate off it does not commit at all."""

    NAME = "Mover One"

    def setUp(self):
        # resolve(): macOS tempdirs sit behind /var → /private/var and git
        # reports the resolved path, so absolute pathspecs must resolve too.
        tmp = Path(tempfile.mkdtemp(prefix="bench-join-git-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", self.NAME)
        git(self.repo, "config", "user.email", "mover@example.com")

        for attr, value in {"TASKS": self.repo / "tasks", "TM_ROOT": self.repo,
                            "REPO": self.repo, "SESSIONS_DIR": tmp / "sessions",
                            "COMMIT_MOVES": True}.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        state.BOARD_EVENTS.clear()

        (self.repo / "tasks" / "backlog" / "51-the-afterthought.md").write_text(
            card("51 — The afterthought"), encoding="utf-8")
        (self.repo / "tasks" / "to-do" / "40-the-site.md").write_text(
            card("40 — Ship the site", kind="Phase", status="To Do",
                 cards="- 31 — Stand up site/\n"), encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.baseline = self.commits()

    def commits(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def head_message(self) -> str:
        return git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()

    def head_files(self) -> list[str]:
        out = git(self.repo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
        return sorted(line for line in out.splitlines() if line.strip())

    def add(self) -> dict:
        return taskfiles.add_to_phase("51-the-afterthought.md", "backlog",
                                      "40-the-site.md")

    def test_the_append_commits_itself_messaged_like_the_bookkeeping_it_is(self):
        self.add()

        self.assertEqual(self.commits(), self.baseline + 1)
        self.assertEqual(self.head_message(), f"board: 40 gained 51 ({self.NAME})")
        self.assertEqual(self.head_files(), ["tasks/to-do/40-the-site.md"],
                         "the phase card and nothing else")
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "",
                         "nothing is left behind for a human to find later")

    def test_the_commit_is_published_like_every_other_one(self):
        published: list[str] = []
        state.COMMIT_HOOKS.append(published.append)
        self.addCleanup(state.COMMIT_HOOKS.remove, published.append)

        self.add()

        self.assertEqual(published, ["40-the-site.md"],
                         "sync publishes the card that changed")

    def test_with_the_gate_off_the_file_is_simply_edited(self):
        config.COMMIT_MOVES = False

        self.add()

        self.assertEqual(self.commits(), self.baseline)
        self.assertIn("- 51 — The afterthought",
                      (self.repo / "tasks" / "to-do" / "40-the-site.md")
                      .read_text(encoding="utf-8"))
        self.assertIn("40-the-site.md",
                      git(self.repo, "status", "--porcelain").stdout)


class TheActionOnTheCard(unittest.TestCase):
    """board.html has no test runner, so these are source-level invariants:
    the action is offered exactly where it can do something, and picking a
    phase goes through the sheet the board already uses for a choice."""

    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def offer(self) -> str:
        match = re.search(r"function joinablePhases\(task\) \{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "board.html lost joinablePhases()")
        return match.group(0)

    def sheet(self) -> str:
        match = re.search(r"function phaseSheet\(task\) \{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "board.html lost phaseSheet()")
        return match.group(0)

    def test_only_phases_waiting_in_to_do_are_offered(self):
        offer = self.offer()
        self.assertIn("'to-do'", offer)
        self.assertIn("t.isPhase", offer)
        self.assertNotIn("in-progress", offer,
                         "a running phase is not on the list")

    def test_a_card_already_in_a_phase_offers_nothing(self):
        self.assertIn("task.phase ||", self.offer())

    def test_a_phase_card_does_not_offer_to_join_one(self):
        self.assertIn("task.isPhase", self.offer())

    def test_the_action_is_absent_when_there_is_no_phase_to_join(self):
        """Present and empty is the thing this must not be."""
        self.assertIn("} else if (joinablePhases(task).length) {", self.html)

    def test_the_action_wears_the_phase_glyph_and_opens_the_sheet(self):
        block = re.search(r"joinablePhases\(task\)\.length\) \{.*?\n    \}",
                          self.html, re.S)
        self.assertIsNotNone(block, "the action's own block is gone")
        self.assertIn("⟶", block.group(0))
        self.assertIn("phaseSheet(task)", block.group(0))

    def test_the_sheet_names_each_phase_and_what_it_holds(self):
        sheet = self.sheet()
        self.assertIn("p.cards", sheet, "each option says how many cards it holds")
        self.assertIn("esc(p.title)", sheet)
        self.assertIn("sheet", sheet)

    def test_the_sheet_can_be_left_without_writing_anything(self):
        self.assertIn("sh-nophase", self.sheet())
        self.assertIn("closeSheet", self.sheet())

    def test_picking_a_phase_posts_it_and_reloads_the_board(self):
        match = re.search(r"async function addToPhase\(task, phase\) \{.*?\n\}",
                          self.html, re.S)
        self.assertIsNotNone(match, "board.html lost addToPhase()")
        post = match.group(0)
        self.assertIn("'/api/phase/add'", post)
        self.assertIn("phase: phase.file", post)
        self.assertIn("stage: task.stage", post)
        self.assertIn("loadState()", post)
        self.assertIn("data.error", post, "a refusal has to reach the toast")

    @unittest.skipUnless(NODE, "node is needed to parse the page")
    def test_the_page_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, script in enumerate(
                    re.findall(r"<script[^>]*>(.*?)</script>", self.html, re.S)):
                source = Path(tmp) / f"page-{index}.js"
                source.write_text(script, encoding="utf-8")
                out = subprocess.run([NODE, "--check", str(source)],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)


class TheRouteBehindIt(unittest.TestCase):
    """The one place the action reaches the write path."""

    def test_the_route_narrates_what_it_wrote(self):
        source = (REPO / "manager" / "core" / "httpd.py").read_text(encoding="utf-8")
        match = re.search(r'elif path == "/api/phase/add":.*?self\._json\(200, result\)',
                          source, re.S)
        self.assertIsNotNone(match, "httpd.py has no /api/phase/add route")
        route = match.group(0)
        self.assertIn("taskfiles.add_to_phase", route)
        self.assertIn("record_board_event", route)
        self.assertIn('"kind": "phase"', route)
        self.assertIn("result['entry']", route,
                      "the ticker says what landed, in the words the file got")
        self.assertIn('state.broadcast({"type": "board"})', route,
                      "every open tab has to see the phase's new member")
