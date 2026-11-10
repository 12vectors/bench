"""Comment → Update card with agent → proposal → Apply or Discard (task 65).

The whole lifecycle against throwaway repositories and a fake adapter: a
real refinement job is launched through `agents._launch`, the fake prints
what a model might, and bench validates, keeps, applies or discards it.
No model, no network, and never the live operator board.

    python3 -m unittest tests.test_card_refinement -v
"""

from __future__ import annotations

import json
import os
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

import config  # noqa: E402
import refine  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

FILENAME = "65-refine-me.md"
CARD = """# 65 — Refine me

**Status:** To Do
**Priority:** Medium
**Base branch:** main

## Context

Bench can work an application from outside it.

## Acceptance

- [ ] The roots are validated <!-- ac:roots -->
- [ ] Nothing is committed to the application

---

## Relevance review — 2026-11-08 08:11 (Juno)

RELEVANCE REVIEW: Still relevant

Kept as history.
"""

NEW_BRIEF = """## Context

Bench can work an application from outside it.

## Acceptance

- [ ] The roots are validated <!-- ac:roots -->
- [ ] Nothing is committed to the application
- [ ] Switching external root A to B preserves both roots' data
"""


def proposal(brief: str = NEW_BRIEF,
             explanation: str = "Added a criterion for switching roots.") -> str:
    return (f"I read the card.\n\nREFINEMENT PROPOSAL\n<<<EXPLANATION\n"
            f"{explanation}\nEXPLANATION>>>\n<<<BRIEF\n{brief}BRIEF>>>\n")


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


FAKE = """#!/usr/bin/env python3
import json, os, sys, time
here = {here!r}
json.dump({{k: v for k, v in os.environ.items() if k.startswith(("AGENT_", "BOARD_TASK"))}},
          open(os.path.join(here, "seen.json"), "w"))
reply = open(os.path.join(here, "reply.txt")).read()
if reply.startswith("SLEEP"):
    time.sleep(30)
code = 0
if reply.startswith("EXIT "):
    code = int(reply.split()[1]); reply = "API Error: 500 upstream"
sys.stdout.write(reply)
sys.exit(code)
"""

CAPABLE = {"v": 1, "adapter": "fake", "version": "1",
           "intents": ["work", "act-pr", "review", "refine"],
           "capabilities": {"read-only-tools": "enforced",
                            "shell-withheld": "enforced"}}


class _Board(unittest.TestCase):
    """One application repository holding its own tasks/ (the vendored
    layout), one card, and a fake adapter each test scripts."""

    external = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-refine-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.tmp / "app"
        self.app.mkdir()
        git(self.app, "init", "-q", "-b", "main")
        git(self.app, "config", "user.email", "t@t")
        git(self.app, "config", "user.name", "Refiner")
        (self.app / "code.py").write_text("print('shipped')\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "root")

        if self.external:
            self.work = self.tmp / "work"
            self.work.mkdir()
            git(self.work, "init", "-q", "-b", "main")
            git(self.work, "config", "user.email", "t@t")
            git(self.work, "config", "user.name", "Refiner")
        else:
            self.work = self.app
        tasks = self.work / "tasks"
        for slug in config.STAGE_DIRS:
            (tasks / slug).mkdir(parents=True)
            (tasks / slug / ".gitkeep").write_text("", encoding="utf-8")
        self.card = tasks / "to-do" / FILENAME
        self.card.write_text(CARD, encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "the card")

        local = self.tmp / "local"
        self.fake = local / "adapters" / "fake"
        self.fake.mkdir(parents=True)
        run = self.fake / "run"
        run.write_text(FAKE.format(here=str(self.tmp)), encoding="utf-8")
        run.chmod(run.stat().st_mode | stat.S_IEXEC)
        (self.fake / "capabilities.json").write_text(json.dumps(CAPABLE),
                                                     encoding="utf-8")
        self.patch(REPO=self.app, TASKS=tasks, LOCAL=local, ADAPTER="fake",
                   WORK_SETTING=str(self.work) if self.external else "",
                   EXTERNAL=self.external,
                   WORKTREES=self.tmp / "worktrees",
                   AGENT_DIR=self.tmp / "agent", SESSIONS_DIR=self.tmp / "sessions",
                   PLANS_DIR=self.tmp / "plans", MANIFESTS_DIR=self.tmp / "manifests",
                   CONTEXT_DIR=self.tmp / "context", REFINE_DIR=self.tmp / "refine",
                   AGENT_PROFILES="", COMMIT_MOVES=True, SYNC=False)
        config._GIT_ROOTS.clear()
        state.AGENTS.clear()
        state.BOARD_EVENTS.clear()
        state.COMPLETING.clear()
        refine.reset()
        self.addCleanup(refine.reset)
        self.addCleanup(state.AGENTS.clear)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append
        self.app_head = self.head(self.app)
        self.app_branches = git(self.app, "branch", "--list").stdout

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — helpers —

    def head(self, repo: Path) -> str:
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    def text(self) -> str:
        return self.card.read_text(encoding="utf-8")

    def brief(self) -> str:
        return taskfiles.card_parts(self.text())["brief"]

    def reply(self, text: str) -> None:
        (self.tmp / "reply.txt").write_text(text, encoding="utf-8")

    def seen(self) -> dict:
        return json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))

    def run_job(self, reply: str, **kwargs) -> dict:
        """Ask, and wait for the reaper to finish with it."""
        self.reply(reply)
        ended = self.sent.count({"type": "agents"})
        kwargs.setdefault("text", "Also cover switching external root A to B "
                                  "while preserving both roots' data")
        result = refine.request(FILENAME, "to-do", **kwargs)
        if not result["duplicate"]:
            self.assertTrue(wait_for(
                lambda: self.sent.count({"type": "agents"}) > ended),
                "the refinement never finished")
        return refine.JOBS[result["job"]["id"]]

    def assert_application_untouched(self) -> None:
        self.assertEqual(self.head(self.app), self.app_head
                         if self.external else self.head(self.app))
        self.assertEqual(git(self.app, "branch", "--list").stdout, self.app_branches,
                         "no branch is created by refining a card")
        self.assertFalse((self.tmp / "worktrees").exists(),
                         "no worktree is created by refining a card")
        self.assertEqual(git(self.app, "status", "--porcelain",
                             "--", "code.py").stdout, "")
        self.assertEqual((self.app / "code.py").read_text(encoding="utf-8"),
                         "print('shipped')\n")


class Lifecycle(_Board):

    # — saving a comment —

    def test_a_comment_is_saved_and_starts_nothing(self):
        entry = refine.comment(FILENAME, "to-do", "Please consider external roots")
        self.assertEqual(state.AGENTS, {}, "no agent is launched by a comment")
        self.assertEqual(refine.JOBS, {})
        self.assertTrue(entry["committed"])
        self.assertEqual(entry["author"], "Refiner")
        view = refine.for_card(FILENAME, "to-do")
        self.assertEqual([e["id"] for e in view["entries"]], [entry["id"]])
        self.assertEqual(view["jobs"], [])
        self.assertEqual(taskfiles.card_parts(self.text())["brief"],
                         taskfiles.card_parts(CARD)["brief"])

    # — a refinement, end to end —

    def test_update_with_agent_proposes_and_changes_nothing(self):
        job = self.run_job(proposal())
        self.assertEqual(job["status"], "proposed", job.get("error"))
        self.assertEqual(self.brief(), taskfiles.card_parts(CARD)["brief"],
                         "generation never touches the brief")
        seen = self.seen()
        self.assertEqual(seen["AGENT_MODE"], "refine")
        self.assertEqual(seen["AGENT_CWD"], str(self.app),
                         "it reads the application; it is given no worktree")
        self.assertIn("Also cover switching external root A to B",
                      seen["AGENT_PROMPT"])
        self.assertIn("--- BRIEF ---", seen["AGENT_PROMPT"])
        policy = json.loads(seen["AGENT_PROFILE"])
        self.assertEqual(policy["commands"], [], "no command grant at all")
        self.assertIn("shell", policy["deny"])
        self.assertIn("edit", policy["deny"])
        self.assertFalse(any(policy["publication"].values()))
        view = refine.for_card(FILENAME, "to-do")
        comment = view["entries"][0]
        self.assertEqual(comment["kind"], "comment")
        self.assertIn("root A to B", comment["body"], "the instruction stays visible")
        shown = view["jobs"][0]
        self.assertTrue(shown["fresh"]["fresh"])
        self.assertIn({"op": "+", "text": "- [ ] Switching external root A to B "
                                          "preserves both roots' data"}, shown["diff"])
        self.assertEqual(shown["sections"], ["Acceptance"])
        self.assertEqual(shown["explanation"], "Added a criterion for switching roots.")
        self.assert_application_untouched()

    def test_apply_changes_the_brief_and_preserves_everything_else(self):
        job = self.run_job(proposal())
        before = taskfiles.card_parts(self.text())
        work_head = self.head(self.work)
        result = refine.apply(job["id"])
        self.assertFalse(result["already"])
        after = taskfiles.card_parts(self.text())
        self.assertEqual(after["head"], before["head"],
                         "id, stage, base branch and every header survive")
        self.assertIn("Switching external root A to B", after["brief"])
        self.assertTrue(after["history"].startswith(before["history"].rstrip()),
                        "the review and the comment are kept verbatim")
        decision = taskfiles.decision_for(self.text(), job["id"])
        self.assertEqual(decision["outcome"], "applied")
        self.assertEqual(decision["comment"], job["comment"])
        self.assertEqual(self.card.parent.name, "to-do", "the card did not move")
        self.assertEqual(git(self.work, "rev-list", "--count",
                             f"{work_head}..HEAD").stdout.strip(), "1")
        changed = git(self.work, "show", "--name-only", "--pretty=format:",
                      "HEAD").stdout.split()
        self.assertEqual(changed, [str(self.card.relative_to(self.work))],
                         "one commit, touching only the card")
        self.assert_application_untouched()

    def test_apply_twice_and_discard_after_apply(self):
        job = self.run_job(proposal())
        refine.apply(job["id"])
        text = self.text()
        again = refine.apply(job["id"])
        self.assertTrue(again["already"])
        self.assertEqual(self.text(), text, "a second Apply writes nothing")
        with self.assertRaises(refine.Conflict):
            refine.discard(job["id"])

    def test_discard_leaves_the_brief_and_records_the_outcome(self):
        job = self.run_job(proposal())
        brief = self.brief()
        refine.discard(job["id"])
        self.assertEqual(self.brief(), brief)
        entries = taskfiles.discussion(self.text())
        self.assertEqual([e["kind"] for e in entries], ["comment", "decision"])
        self.assertEqual(entries[1]["outcome"], "discarded")
        self.assertTrue(refine.discard(job["id"])["already"])
        with self.assertRaises(refine.Conflict):
            refine.apply(job["id"])

    def test_a_selected_saved_comment_can_be_refined(self):
        entry = refine.comment(FILENAME, "to-do", "Tighten the context")
        job = self.run_job(proposal(), text=None, comment_id=entry["id"])
        self.assertEqual(job["comment"], entry["id"])
        self.assertEqual(len(taskfiles.discussion(self.text())), 1,
                         "selecting a comment saves no second one")

    # — races, staleness and intervening writes —

    def test_an_edited_brief_makes_the_proposal_stale(self):
        job = self.run_job(proposal())
        self.card.write_text(self.text().replace("from outside it",
                                                 "from outside it, by hand"),
                             encoding="utf-8")
        text = self.text()
        self.assertFalse(refine.public(job)["fresh"]["fresh"])
        with self.assertRaises(taskfiles.StaleCard):
            refine.apply(job["id"])
        self.assertEqual(self.text(), text, "nothing was written")
        self.assertEqual(refine.JOBS[job["id"]]["status"], "proposed")
        again = self.run_job(proposal(), text=None, comment_id=job["comment"],
                             retry=True)
        self.assertEqual(again["attempt"], 2)
        self.assertEqual(refine.JOBS[job["id"]]["status"], "superseded")

    def test_a_move_makes_the_proposal_stale(self):
        job = self.run_job(proposal())
        taskfiles.move_task(FILENAME, "to-do", "backlog")
        with self.assertRaises(taskfiles.StaleCard):
            refine.apply(job["id"])
        self.assertNotIn("Switching external root",
                         (self.card.parent.parent / "backlog" / FILENAME)
                         .read_text(encoding="utf-8"))

    def test_comments_and_reports_arriving_meanwhile_are_kept(self):
        job = self.run_job(proposal())
        other = refine.comment(FILENAME, "to-do", "Another client's comment")
        taskfiles.append_to_task(FILENAME, "to-do",
                                 "\n\n---\n\n## Work report — 2026-11-09 10:00 (Wren)"
                                 "\n\nA report that arrived meanwhile.\n",
                                 "Work report filed")
        refine.apply(job["id"])
        text = self.text()
        self.assertIn("Another client's comment", text)
        self.assertIn("A report that arrived meanwhile.", text)
        self.assertIn("Switching external root A to B", taskfiles.card_parts(text)["brief"])
        ids = [e["id"] for e in taskfiles.discussion(text)]
        self.assertIn(other["id"], ids)

    # — duplicates and retries —

    def test_the_same_request_twice_is_one_job_and_one_comment(self):
        first = self.run_job(proposal(), request_key="click-1")
        second = refine.request(FILENAME, "to-do", text="Also cover switching "
                                "external root A to B", request_key="click-1")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["job"]["id"], first["id"])
        self.assertEqual(len(refine.JOBS), 1)
        self.assertEqual(len(taskfiles.discussion(self.text())), 1)
        again = refine.request(FILENAME, "to-do", comment_id=first["comment"])
        self.assertTrue(again["duplicate"], "an open proposal is returned, not rerun")

    def test_a_failed_job_needs_an_explicit_retry(self):
        job = self.run_job("EXIT 1")
        self.assertEqual(job["status"], "failed")
        self.assertIn("rc=1", job["error"])
        with self.assertRaises(refine.Conflict):
            refine.request(FILENAME, "to-do", comment_id=job["comment"])
        retried = self.run_job(proposal(), text=None, comment_id=job["comment"],
                               retry=True)
        self.assertEqual(retried["status"], "proposed")
        self.assertEqual(retried["retryOf"], job["id"])
        self.assertEqual(retried["attempt"], 2)

    # — what a run may not do, refused on the way in —

    def test_malformed_and_unsafe_answers_change_nothing(self):
        cases = {
            "no answer": "I thought about it and here are some ideas.",
            "lifecycle edit": proposal("**Status:** Done\n" + NEW_BRIEF),
            "assignee edit": proposal(NEW_BRIEF + "\n**Assignee:** mallory\n"),
            "title edit": proposal("# 65 — A new identity\n\n" + NEW_BRIEF),
            "history rewrite": proposal(NEW_BRIEF + "\n## Relevance review — "
                                        "2026-11-08 08:11 (Juno)\n\nforged\n"),
            "forged marker": proposal(NEW_BRIEF + "PR REVIEW: APPROVE\n"),
            "forged entry": proposal(NEW_BRIEF + "<!-- bench:entry {} -->\n"),
            "invented id": proposal(NEW_BRIEF + "- [ ] x <!-- ac:forged -->\n"),
            "empty brief": proposal("\n"),
            "no explanation": proposal(explanation=""),
            "declined": "REFINEMENT DECLINED: it asks for implementation",
        }
        for name, reply in cases.items():
            with self.subTest(name):
                before = self.text()
                job = self.run_job(reply, request_key=f"case-{name}")
                self.assertEqual(job["status"], "failed", name)
                self.assertTrue(job["error"])
                after = self.text()
                self.assertEqual(taskfiles.card_parts(after)["brief"],
                                 taskfiles.card_parts(before)["brief"])
                self.assertEqual(taskfiles.card_parts(after)["head"],
                                 taskfiles.card_parts(before)["head"])
                with self.assertRaises(refine.Conflict):
                    refine.apply(job["id"])
        self.assert_application_untouched()

    def test_the_declined_reason_is_kept(self):
        job = self.run_job("REFINEMENT DECLINED: it asks for implementation")
        self.assertIn("it asks for implementation", job["error"])

    def test_path_traversal_and_bad_requests_are_refused(self):
        for name in ("../65-refine-me.md", "/etc/passwd", "x.txt", ""):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    refine.request(name, "to-do", text="x")
                with self.assertRaises(ValueError):
                    refine.comment(name, "to-do", "x")
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "../to-do", text="x")
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "to-do")   # names no instruction
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "to-do", comment_id="c-nope")
        with self.assertRaises(ValueError):
            refine.apply("p-unknown")
        self.assertEqual(state.AGENTS, {})

    # — active implementation —

    def test_an_implementation_run_blocks_refining_but_not_comments(self):
        state.AGENTS["work-1"] = {"id": "work-1", "task": FILENAME,
                                  "status": "running", "mode": "work",
                                  "name": "Wren", "proc": None}
        before = self.text()
        with self.assertRaises(ValueError) as caught:
            refine.request(FILENAME, "to-do", text="change it")
        self.assertIn("hold", str(caught.exception))
        self.assertEqual(self.text(), before, "a refused request saves nothing")
        refine.comment(FILENAME, "to-do", "plain discussion is fine")
        self.assertEqual(state.AGENTS["work-1"]["status"], "running",
                         "nothing is stopped automatically")

    def test_an_implementation_run_blocks_apply(self):
        job = self.run_job(proposal())
        state.AGENTS["work-1"] = {"id": "work-1", "task": FILENAME,
                                  "status": "running", "mode": "work",
                                  "name": "Wren", "proc": None}
        with self.assertRaises(ValueError):
            refine.apply(job["id"])
        self.assertEqual(refine.JOBS[job["id"]]["status"], "proposed")

    # — restarts and replicas —

    def test_a_restart_fails_a_running_job_and_keeps_a_proposal(self):
        proposed = self.run_job(proposal(), request_key="a")
        running = dict(proposed, id="p-running", status="running",
                       request="b", comment="c-other")
        refine._save(running)
        refine.reset()
        self.assertEqual(refine.recover(), 1)
        self.assertEqual(refine.JOBS["p-running"]["status"], "failed")
        self.assertIn("restarted", refine.JOBS["p-running"]["error"])
        self.assertEqual(refine.JOBS[proposed["id"]]["status"], "proposed")
        refine.reset()
        refine.apply(proposed["id"])
        self.assertIn("Switching external root", self.brief())

    def test_a_decision_a_replica_made_is_not_made_twice(self):
        job = self.run_job(proposal())
        refine.discard(job["id"])
        decided = self.text()
        # this board forgets it decided — as a replica that never did would
        refine.reset()
        stored = json.loads(refine._path(job["id"]).read_text(encoding="utf-8"))
        stored["status"] = "proposed"
        refine._save(stored)
        refine.reset()
        result = refine.apply(job["id"])
        self.assertTrue(result["already"])
        self.assertEqual(result["job"]["status"], "discarded")
        self.assertEqual(self.text(), decided)

    # — cancellation —

    def test_a_cancelled_job_proposes_nothing(self):
        self.reply("SLEEP")
        ended = self.sent.count({"type": "agents"})
        job = refine.request(FILENAME, "to-do", text="slow one")["job"]
        self.assertEqual(job["status"], "running")
        refine.cancel(job["id"])
        self.assertTrue(wait_for(lambda: self.sent.count({"type": "agents"}) > ended))
        self.assertEqual(refine.JOBS[job["id"]]["status"], "cancelled")
        self.assertEqual(self.brief(), taskfiles.card_parts(CARD)["brief"])

    # — sync off, commits off —

    def test_without_commit_moves_everything_is_written_and_says_so(self):
        config.COMMIT_MOVES = False
        head = self.head(self.work)
        job = self.run_job(proposal())
        result = refine.apply(job["id"])
        self.assertFalse(result["job"]["committed"])
        self.assertIn("Switching external root", self.brief())
        self.assertEqual(self.head(self.work), head)
        self.assertFalse(refine.for_card(FILENAME, "to-do")["persistence"]["commits"])


class ExternalLifecycle(Lifecycle):
    """The same, with the tickets in a repository of their own: every
    board write lands in the work state's repository and the application's
    history does not move at all."""

    external = True

    def test_the_application_history_does_not_move(self):
        job = self.run_job(proposal())
        refine.apply(job["id"])
        self.assertEqual(self.head(self.app), self.app_head)
        self.assertIn("board: 65 brief refined",
                      git(self.work, "log", "-1", "--pretty=%s").stdout)


class DocumentedMarkers(_Board):
    """Task 82: a card that documents Bench's storage syntax — card 81's
    shape, from tests/fixtures/refinement — refined end to end. Examples in
    code are proposed, diffed and applied as text and stay text after a
    reload; a record-shaped marker in prose is refused and changes nothing."""

    FIXTURE = (REPO / "tests" / "fixtures" / "refinement"
               / "card-documenting-markers.md").read_text(encoding="utf-8")
    EXAMPLE = ('```\n<!-- bench:acceptance {"criterion": "examples", '
               '"state": "accepted", "v": 1} -->\n```')

    def setUp(self):
        super().setUp()
        self.card.write_text(self.FIXTURE, encoding="utf-8")
        git(self.work, "commit", "-qam", "a card documenting markers")

    def revised(self, extra: str) -> str:
        brief = taskfiles.card_parts(self.FIXTURE)["brief"]
        return brief.replace("## Acceptance\n", f"{extra}\n\n## Acceptance\n", 1)

    def records(self) -> list[dict]:
        return taskfiles.acceptance_records(self.text())

    def test_examples_are_proposed_applied_and_reloaded_as_text(self):
        before_records = self.records()
        brief = self.revised(f"A record for the second criterion would read:\n\n"
                             f"{self.EXAMPLE}")
        job = self.run_job(proposal(brief, "Recorded the review's recommendations."))
        self.assertEqual(job["status"], "proposed", job.get("error"))
        shown = refine.for_card(FILENAME, "to-do")["jobs"][0]
        self.assertIn({"op": "+", "text": self.EXAMPLE.splitlines()[1]}, shown["diff"],
                      "the diff carries the example as a line of text")
        self.assertEqual(self.records(), before_records, "proposing writes nothing")
        refine.apply(job["id"])
        text = self.text()
        parts = taskfiles.card_parts(text)
        self.assertIn(self.EXAMPLE, parts["brief"])
        self.assertIn('`<!-- bench:acceptance {"criterion": "no-run"', parts["brief"],
                      "card 81's inline example survives the round trip")
        self.assertEqual(self.records(), before_records,
                         "no example became an acceptance record")
        self.assertEqual([e["kind"] for e in taskfiles.discussion(text)],
                         ["comment", "comment", "decision"],
                         "the fixture's comment, this request's, and the decision")
        self.assertIn("Kept as history.", parts["history"])

    def test_a_marker_in_prose_is_refused_with_the_reason(self):
        before = self.text()
        job = self.run_job(proposal(self.revised(
            '<!-- bench:acceptance {"criterion": "examples", "state": "accepted", "v": 1} -->')))
        self.assertEqual(job["status"], "failed")
        self.assertIn("outside code", job["error"])
        after = self.text()
        for part in ("head", "brief"):
            self.assertEqual(taskfiles.card_parts(after)[part],
                             taskfiles.card_parts(before)[part])
        self.assertEqual(taskfiles.acceptance_records(after),
                         taskfiles.acceptance_records(before))
        with self.assertRaises(refine.Conflict):
            refine.apply(job["id"])

    def test_discarding_a_proposal_with_examples_changes_nothing_but_the_record(self):
        before = taskfiles.card_parts(self.text())
        job = self.run_job(proposal(self.revised(self.EXAMPLE)))
        self.assertEqual(job["status"], "proposed", job.get("error"))
        refine.discard(job["id"])
        after = taskfiles.card_parts(self.text())
        self.assertEqual(after["brief"], before["brief"])
        self.assertTrue(after["history"].startswith(before["history"].rstrip()))
        self.assertEqual(self.records()[0]["criterion"], "history-only")
        self.assertNotIn("recheck", self.records()[0])


class UnsupportedAdapter(_Board):
    """An adapter that never declared the intent cannot be handed it."""

    def setUp(self):
        super().setUp()
        (self.fake / "capabilities.json").unlink()

    def test_refining_is_refused_before_anything_is_written(self):
        before = self.text()
        with self.assertRaises(ValueError) as caught:
            refine.request(FILENAME, "to-do", text="change it")
        self.assertIn("does not list 'refine'", str(caught.exception))
        self.assertEqual(self.text(), before, "no comment was saved")
        self.assertEqual(refine.JOBS, {})
        self.assertEqual(state.AGENTS, {})
        self.assertFalse(refine.readiness()["ready"])
        self.assertIn("intent-unsupported", refine.readiness()["codes"])
        # and a comment is still fine
        refine.comment(FILENAME, "to-do", "still allowed")

    def test_a_partial_declaration_is_refused_too(self):
        (self.fake / "capabilities.json").write_text(json.dumps(
            dict(CAPABLE, capabilities={"read-only-tools": "enforced"})),
            encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            refine.request(FILENAME, "to-do", text="change it")
        self.assertIn("shell-withheld", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
