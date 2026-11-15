"""The derived stack reading and the two safety fixes (task 117, WP09).

A card whose `**Base branch:**` names another card's work branch is
stacked on it. These build disposable application repositories with a
bare remote beside them, write the cards, records and snapshots each
case needs, and read the stack exactly as `/api/state` does. No hosting
provider is reached: `gh` is a stub that logs what it was asked, and the
read-side cases refuse any process that is not a local git read.

    python3 -m unittest tests.test_stacks -v
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import changes  # noqa: E402
import completion  # noqa: E402
import config  # noqa: E402
import delivery  # noqa: E402
import githost  # noqa: E402
import plans  # noqa: E402
import stacks  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "stacks"

PARENT = "56-the-parent.md"
CHILD = "57-the-child.md"
GRANDCHILD = "58-the-grandchild.md"
SIBLING = "59-a-sibling.md"
P_BRANCH = "task/56-the-parent"
C_BRANCH = "task/57-the-child"
G_BRANCH = "task/58-the-grandchild"

# gh, as far as these cases go: every call logged; `pr list` answers what
# GH_TARGETING says (a JSON list), or fails when it is "fail".
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "create"]:
    print("https://github.com/acme/widget/pull/7")
elif args[:2] == ["pr", "view"]:
    sys.exit(1)
elif args[:2] == ["pr", "list"]:
    said = os.environ.get("GH_TARGETING", "[]")
    if said == "fail":
        sys.stderr.write("HTTP 502\\n")
        sys.exit(1)
    print(said)
else:
    sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def sha(cwd: Path, ref: str) -> str:
    return git(cwd, "rev-parse", ref).stdout.strip()


def card(title: str, *, status: str = "Review", base: str | None = None,
         kind: str | None = None, cards: str | None = None,
         pr: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if pr:
        text += f"**PR:** {pr}\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if base:
        text += f"**Base branch:** {base}\n"
    text += "\nWhat this card is for.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


STATUS = {"backlog": "Backlog", "to-do": "To Do", "in-progress": "In Progress",
          "review": "Review", "done": "Done"}


class StackCase(unittest.TestCase):
    """An application repository with a bare `origin`, a board, and the
    state directories a reading consults, all disposable."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "-u", "origin", "main")
        self.tasks = self.tmp / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        gh = self.tmp / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.gh_log = self.tmp / "gh.log"
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)
        self.addCleanup(os.environ.pop, "GH_TARGETING", None)
        self.state_dir = self.tmp / "state"
        self.patch(REPO=self.repo, TASKS=self.tasks,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.state_dir / "plans",
                   GATES_DIR=self.state_dir / "gates",
                   COMPLETIONS_DIR=self.state_dir / "completions",
                   SESSIONS_DIR=self.state_dir / "sessions",
                   GH_BIN=str(gh), APP_BASE="main", GIT_REMOTE="",
                   GIT_PROVIDER="github", COMMIT_MOVES=False, SYNC=False,
                   FETCH_TIMEOUT=10.0)
        (self.state_dir / "gates").mkdir(parents=True)
        for registry in (state.BOARD_EVENTS, state.COMPLETING,
                         state.EXPECTED_MOVES, changes.PR_STATE):
            registry.clear()
            self.addCleanup(registry.clear)
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda *_: None

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — furniture —

    def write(self, name: str, text: str, stage: str = "review") -> None:
        (self.tasks / stage / name).write_text(text, encoding="utf-8")

    def branch(self, name: str, *, start: str = "main", publish: bool = True,
               files: tuple[str, ...] = ("work",)) -> str:
        git(self.repo, "checkout", "-q", "-b", name, start)
        for f in files:
            path = self.repo / f"{name.replace('/', '_')}-{f}.txt"
            path.write_text(f"{f}\n", encoding="utf-8")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", f"{name}: {f}")
        if publish:
            git(self.repo, "push", "-q", "-u", "origin", name)
        git(self.repo, "checkout", "-q", "main")
        return sha(self.repo, name)

    def commit_on(self, branch: str, what: str, publish: bool = True) -> str:
        git(self.repo, "checkout", "-q", branch)
        (self.repo / f"{what}.txt").write_text(what, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", what)
        if publish:
            git(self.repo, "push", "-q", "origin", branch)
        git(self.repo, "checkout", "-q", "main")
        return sha(self.repo, branch)

    def record_plan(self, name: str, **fields) -> None:
        plan = {"task": name, "branch": f"task/{name[:-3]}",
                "appBase": "main", "prSource": "the card's **Base branch:** header",
                "source": "the card's **Base branch:** header"}
        plan.update(fields)
        config.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        plans.plan_path(name).write_text(json.dumps(plan), encoding="utf-8")

    def snapshot(self, name: str, **fields) -> None:
        data = {"v": 1, "task": name, "observed": time.time()}
        data.update(fields)
        (config.GATES_DIR / f"{name[:-3]}.pr.json").write_text(
            json.dumps(data), encoding="utf-8")

    def delivered(self, name: str, commit: str, target: str = "main") -> None:
        (config.GATES_DIR / f"{name[:-3]}.delivered.json").write_text(
            json.dumps({"v": 1, "task": name, "commit": commit,
                        "target": target, "how": "local", "at": time.time()}),
            encoding="utf-8")

    def stacked(self) -> dict:
        return stacks.board(taskfiles.collect())

    def edge(self, name: str = CHILD) -> str:
        return (self.stacked()[name].get("edge") or {}).get("state")

    def parent_and_child(self, *, child_branch: bool = True) -> tuple[str, str]:
        """#56 in review with published work; #57 stacked on it."""
        ptip = self.branch(P_BRANCH)
        self.write(PARENT, card("56 — The parent"))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        ctip = self.branch(C_BRANCH, start=P_BRANCH) if child_branch else ""
        self.record_plan(CHILD, startBranch=P_BRANCH, startCommit=ptip,
                         prBase=P_BRANCH)
        return ptip, ctip


# ── the graph, derived once ────────────────────────────────────────────


class TheGraph(StackCase):

    def woven(self) -> dict:
        return {t["file"]: t for g in taskfiles.collect()["stages"]
                for t in g["tasks"]}

    def test_parent_and_children_are_read_off_the_base_header(self):
        self.write(PARENT, card("56 — The parent"))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.write(SIBLING, card("59 — A sibling", base=P_BRANCH))
        cards = self.woven()
        self.assertEqual(cards[CHILD]["stack"]["mapping"], "card")
        self.assertEqual(cards[CHILD]["stack"]["parent"], PARENT)
        self.assertEqual(cards[PARENT]["stack"]["children"], [CHILD, SIBLING])
        self.assertEqual(cards[PARENT]["stack"]["mapping"], "default")

    def test_a_branch_no_card_owns_is_external(self):
        self.write(CHILD, card("57 — The child", base="release/2.0"))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual((here["mapping"], here["parent"]), ("external", None))

    def test_a_card_shaped_branch_with_no_card_is_missing(self):
        self.write(CHILD, card("57 — The child", base="task/55-gone"))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual(here["mapping"], "missing")
        self.assertIn("55-gone.md", here["why"])

    def test_two_readings_are_ambiguous_and_none_is_chosen(self):
        self.write(PARENT, card("56 — The parent"))
        self.write(PARENT, card("56 — The parent", status="To Do"), "to-do")
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual((here["mapping"], here["parent"]), ("ambiguous", None))
        self.assertIn("more than one stage", here["why"])

    def test_a_task_branch_naming_a_phase_card_is_ambiguous(self):
        self.write(PARENT, card("56 — The parent", kind="Phase", cards=""))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual(here["mapping"], "ambiguous")
        self.assertIn("phase/56-the-parent", here["why"])

    def test_a_loop_is_plan_drift_on_every_card_in_it(self):
        self.write(PARENT, card("56 — The parent", base=C_BRANCH))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        cards = self.woven()
        for name in (PARENT, CHILD):
            self.assertEqual(sorted(cards[name]["stack"]["cycle"]),
                             sorted([PARENT, CHILD]))
            self.assertTrue(any("loop" in d for d in cards[name]["planDrift"]))
        # and the launch refuses on it, as on any drift
        plan = plans.preflight(CHILD, "review", fetch=False)
        self.assertTrue(any("loop" in b for b in plan["blockers"]), plan["blockers"])

    def test_a_card_stacked_on_itself_is_a_loop_of_one(self):
        self.write(CHILD, card("57 — The child", base=C_BRANCH))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual(here["cycle"], [CHILD])

    def test_a_phase_member_keeps_integration_semantics(self):
        self.write("40-a-phase.md", card("40 — A phase", kind="Phase",
                                         cards="- 57 — The child\n",
                                         status="In Progress"), "in-progress")
        self.write(CHILD, card("57 — The child"))
        here = self.woven()[CHILD]["stack"]
        self.assertEqual(here["mapping"], "integration")
        self.assertEqual(here["base"], "phase/40-a-phase")
        found = self.stacked()[CHILD]
        self.assertEqual(found["edge"]["state"], "integration")
        self.assertEqual(found["targets"]["reviewTarget"]["branch"], "phase/40-a-phase")
        self.assertEqual(found["targets"]["landingTarget"]["branch"], "main")

    def test_a_board_with_nothing_stacked_sends_nothing(self):
        self.write(PARENT, card("56 — The parent"))
        self.assertEqual(self.stacked(), {})


# ── each edge state, from a fixture ────────────────────────────────────


class EveryEdgeState(StackCase):

    def test_on_parent(self):
        self.parent_and_child()
        self.assertEqual(self.edge(), "on-parent")

    def test_parent_moved(self):
        self.parent_and_child()
        self.commit_on(P_BRANCH, "more-parent-work")
        found = self.stacked()[CHILD]
        self.assertEqual(found["edge"]["state"], "parent-moved")
        self.assertEqual(found["edge"]["who"], "you")
        self.assertIn("bench will not rebase", found["edge"]["next"])

    def test_parent_rewritten(self):
        self.parent_and_child()
        # the parent is cut again from main: it no longer holds the start
        git(self.repo, "branch", "-f", P_BRANCH, "main")
        git(self.repo, "push", "-q", "-f", "origin", P_BRANCH)
        self.assertEqual(self.edge(), "parent-rewritten")

    def test_parent_landed(self):
        ptip, _ = self.parent_and_child()
        self.delivered(PARENT, ptip)
        found = self.stacked()[CHILD]
        self.assertEqual(found["edge"]["state"], "parent-landed")
        self.assertIn("still targets task/56-the-parent", found["edge"]["sentence"])

    def test_parent_landed_differently(self):
        ptip, _ = self.parent_and_child()
        # the host squash-merged it: says merged, main lacks the tip
        self.snapshot(PARENT, head=ptip, prState="MERGED", merged=True)
        self.assertEqual(self.edge(), "parent-landed-differently")

    def test_parent_merged_with_its_tip_in_the_target_is_landed(self):
        ptip, _ = self.parent_and_child()
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge 56", P_BRANCH)
        self.snapshot(PARENT, head=ptip, prState="MERGED", merged=True)
        self.assertEqual(self.edge(), "parent-landed")

    def test_parent_closed(self):
        ptip, _ = self.parent_and_child()
        self.snapshot(PARENT, head=ptip, prState="CLOSED", merged=False)
        self.assertEqual(self.edge(), "parent-closed")

    def test_parent_gone(self):
        self.parent_and_child()
        self.record_plan(PARENT, startBranch="main", prBase="main")
        git(self.repo, "branch", "-D", P_BRANCH)
        git(self.repo, "push", "-q", "origin", "--delete", P_BRANCH)
        git(self.repo, "fetch", "-q", "--prune", "origin")
        self.assertEqual(self.edge(), "parent-gone")

    def test_parent_not_started(self):
        self.write(PARENT, card("56 — The parent", status="To Do"), "to-do")
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.assertEqual(self.edge(), "parent-not-started")

    def test_parent_retargeted_on_the_host(self):
        ptip, _ = self.parent_and_child()
        self.snapshot(PARENT, head=ptip, prState="OPEN", base="release",
                      wantBase="main", baseMismatch="release")
        self.assertEqual(self.edge(), "parent-retargeted")

    def test_target_changed_on_the_host(self):
        _, ctip = self.parent_and_child()
        self.snapshot(CHILD, head=ctip, prState="OPEN", base="main",
                      wantBase=P_BRANCH, baseMismatch="main")
        found = self.stacked()[CHILD]
        self.assertEqual(found["edge"]["state"], "target-changed-on-host")
        self.assertIn("⌖ replan", found["edge"]["next"])

    def test_not_started(self):
        self.parent_and_child(child_branch=False)
        self.assertEqual(self.edge(), "not-started")

    def test_external_missing_ambiguous_and_loop(self):
        self.write(CHILD, card("57 — The child", base="release/2.0"))
        self.assertEqual(self.edge(), "external-base")
        self.write(CHILD, card("57 — The child", base="task/55-gone"))
        self.assertEqual(self.edge(), "parent-missing")
        self.write(PARENT, card("56 — The parent"))
        self.write(PARENT, card("56 — The parent", status="To Do"), "to-do")
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.assertEqual(self.edge(), "parent-ambiguous")
        (self.tasks / "to-do" / PARENT).unlink()
        self.write(PARENT, card("56 — The parent", base=C_BRANCH))
        self.assertEqual(self.edge(), "cycle")

    def test_every_edge_word_has_its_words(self):
        for word, (label, tone, nxt, who) in stacks.EDGES.items():
            with self.subTest(word=word):
                self.assertTrue(label)
                self.assertIn(tone, delivery.TONES)
                self.assertEqual(nxt is None, who is None)

    def test_the_parent_lists_its_children_with_their_states(self):
        self.parent_and_child()
        found = self.stacked()[PARENT]
        self.assertIsNone(found["parent"])
        self.assertEqual([(c["file"], c["state"]) for c in found["children"]],
                         [(CHILD, "on-parent")])
        self.assertEqual(found["chip"], "under #57 · 1/2")


# ── positions, targets ─────────────────────────────────────────────────


class PositionsAndTargets(StackCase):

    def chain(self) -> None:
        ptip, ctip = self.parent_and_child()
        self.write(GRANDCHILD, card("58 — The grandchild", base=C_BRANCH))
        self.branch(G_BRANCH, start=C_BRANCH)

    def test_a_chain_counts_along_itself(self):
        self.chain()
        found = self.stacked()
        self.assertEqual(found[PARENT]["position"]["label"], "1/3")
        self.assertEqual(found[CHILD]["position"]["label"], "2/3")
        self.assertEqual(found[GRANDCHILD]["position"]["label"], "3/3")
        self.assertEqual(found[GRANDCHILD]["chip"], "on #57 · 3/3")
        self.assertEqual([c["file"] for c in found[GRANDCHILD]["chain"]],
                         [PARENT, CHILD, GRANDCHILD])

    def test_a_branching_stack_invents_no_global_position(self):
        self.chain()
        self.write(SIBLING, card("59 — A sibling", base=P_BRANCH))
        found = self.stacked()
        for name in (CHILD, GRANDCHILD, SIBLING):
            with self.subTest(name=name):
                self.assertEqual(found[name]["position"]["kind"], "path")
                self.assertIsNone(found[name]["position"]["of"])
                self.assertNotIn("/", found[name]["position"]["label"])
        self.assertEqual(found[GRANDCHILD]["position"]["label"], "step 3")
        self.assertEqual(found[SIBLING]["chip"], "on #56 · step 2")
        self.assertEqual(found[PARENT]["chip"], "under 2 cards · step 1")

    def test_three_targets_are_named_apart(self):
        self.chain()
        targets = self.stacked()[GRANDCHILD]["targets"]
        self.assertEqual(targets["selectedBase"]["branch"], C_BRANCH)
        self.assertEqual(targets["reviewTarget"]["branch"], C_BRANCH)
        self.assertEqual(targets["landingTarget"]["branch"], "main")
        self.assertEqual(targets["landingTarget"]["via"], [C_BRANCH, P_BRANCH])
        self.assertIn("Lands in main once", targets["landingTarget"]["sentence"])

    def test_a_review_target_apart_from_the_base_is_said(self):
        self.parent_and_child()
        self.record_plan(CHILD, startBranch=P_BRANCH, prBase="main",
                         prSource="the card's **PR base:** header")
        targets = self.stacked()[CHILD]["targets"]
        self.assertEqual(targets["selectedBase"]["branch"], P_BRANCH)
        self.assertEqual(targets["reviewTarget"]["branch"], "main")


# ── reading is free ────────────────────────────────────────────────────


class ReadingWritesAndFetchesNothing(StackCase):

    def digest(self) -> str:
        h = hashlib.sha256()
        for root in (self.state_dir, self.tasks, self.repo / ".git" / "refs",
                     self.origin / "refs"):
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                h.update(str(path).encode())
                h.update(path.read_bytes())
        for name in ("FETCH_HEAD", "packed-refs"):
            path = self.repo / ".git" / name
            if path.exists():
                h.update(path.read_bytes())
        return h.hexdigest()

    def test_the_reading_runs_only_local_git_reads(self):
        ptip, ctip = self.parent_and_child()
        self.write(GRANDCHILD, card("58 — The grandchild", base=C_BRANCH))
        self.snapshot(PARENT, head=ptip, prState="OPEN")
        ran: list[list[str]] = []
        real = subprocess.run

        def watched(argv, *a, **k):
            ran.append([str(w) for w in argv])
            return real(argv, *a, **k)

        def refused(*a, **k):
            raise AssertionError("the stack reading asked a hosting provider")

        before = self.digest()
        with mock.patch.object(subprocess, "run", watched), \
                mock.patch.object(githost, "run", refused), \
                mock.patch.object(changes, "_run", refused):
            found = stacks.board(taskfiles.collect())
            self.assertIn(CHILD, found)
            delivery.board(taskfiles.collect(), {}, found)
        self.assertEqual(self.digest(), before, "reading wrote something")
        self.assertTrue(ran, "the reading asked git nothing at all")
        for argv in ran:
            self.assertTrue(Path(argv[0]).name == "git", argv)
            words = iter(argv[1:])
            verb = "?"
            for word in words:
                if word in ("-C", "-c"):
                    next(words, None)
                elif not word.startswith("-"):
                    verb = word
                    break
            self.assertIn(verb, ("for-each-ref", "merge-base", "rev-parse",
                                 "rev-list", "status", "log", "show",
                                 "cat-file", "remote", "config", "ls-files",
                                 "worktree-list", "branch", "symbolic-ref"),
                          argv)
            self.assertNotIn("--delete", argv)
            if verb == "remote":     # listing or reading a url only
                for change in ("add", "remove", "rename", "set-url", "prune",
                               "update", "set-head"):
                    self.assertNotIn(change, argv)
        self.assertFalse(self.gh_log.exists(), "gh was run")


# ── plan record v2 ─────────────────────────────────────────────────────


class PlanRecordVersionTwo(StackCase):

    def test_a_stacked_launch_records_its_parent_and_landing(self):
        ptip, _ = self.parent_and_child(child_branch=False)
        (self.tasks / "review" / CHILD).rename(self.tasks / "in-progress" / CHILD)
        plans.forget(CHILD)
        plan = plans.require(plans.preflight(CHILD, "in-progress"))
        self.assertEqual(plan["stack"], {"card": PARENT, "branch": P_BRANCH,
                                         "tip": ptip})
        self.assertEqual(plan["landing"], "main")
        stored = plans.record(plan)
        self.assertEqual(stored["v"], 2)

    def test_an_unstacked_launch_records_no_parent(self):
        self.write(PARENT, card("56 — The parent", status="In Progress"),
                   "in-progress")
        plan = plans.require(plans.preflight(PARENT, "in-progress"))
        self.assertIsNone(plan["stack"])
        self.assertEqual(plan["landing"], "main")

    def test_a_version_one_record_reads_as_before(self):
        """A record written before task 117 — no `stack`, no `landing` —
        is read unchanged, and its stack reading is derived from the card."""
        ptip = self.branch(P_BRANCH)
        self.write(PARENT, card("56 — The parent"))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.branch(C_BRANCH, start=P_BRANCH)
        v1 = json.loads((FIXTURES / "plan-v1.json").read_text(encoding="utf-8"))
        v1["startCommit"] = ptip
        config.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        plans.plan_path(CHILD).write_text(json.dumps(v1), encoding="utf-8")
        self.assertEqual(plans.recorded(CHILD), v1)
        self.assertEqual(plans.pr_base(CHILD), P_BRANCH)
        summary = plans.summary(taskfiles.read_task(self.tasks / "review" / CHILD,
                                                    "review"))
        self.assertEqual((summary["startBranch"], summary["prBase"],
                          summary["startCommit"]), (P_BRANCH, P_BRANCH, ptip))
        found = self.stacked()[CHILD]
        self.assertEqual(found["planVersion"], 1)
        self.assertEqual(found["parent"]["file"], PARENT)
        self.assertEqual(found["edge"]["state"], "on-parent")
        self.assertEqual(found["targets"]["landingTarget"]["branch"], "main")

    def test_an_old_reader_ignores_the_version_two_keys(self):
        """The old-reader fixture: every reader that predates version 2
        answers the same about a version-2 record as about the same record
        with the new keys taken away — they are additions, nothing else."""
        v2 = json.loads((FIXTURES / "plan-v2.json").read_text(encoding="utf-8"))
        v1 = {k: v for k, v in v2.items() if k not in ("stack", "landing")}
        v1["v"] = 1
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        task = taskfiles.read_task(self.tasks / "review" / CHILD, "review")
        answers = []
        for record in (v1, v2):
            config.PLANS_DIR.mkdir(parents=True, exist_ok=True)
            plans.plan_path(CHILD).write_text(json.dumps(record), encoding="utf-8")
            answers.append((plans.pr_base(CHILD, task), plans.summary(task),
                            plans.portable(plans.recorded(CHILD)),
                            plans.line(plans.recorded(CHILD)),
                            plans.brief(dict(plans.recorded(CHILD),
                                             branch=C_BRANCH))))
        self.assertEqual(answers[0], answers[1])


# ── safety fix 1: opening a child never publishes the parent ───────────


class OpeningAChildPublishesNoParent(StackCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, changes, "_poll_pr", changes._poll_pr)
        changes._poll_pr = lambda *a, **k: None

    def pushes(self) -> list[str]:
        return [line for line in self.gh_log.read_text().splitlines()
                if '"create"' in line] if self.gh_log.exists() else []

    def remote_has(self, branch: str) -> str:
        return git(self.origin, "rev-parse", "--verify", "--quiet",
                   f"refs/heads/{branch}").stdout.strip()

    def test_a_parent_that_is_not_published_refuses_the_open(self):
        self.branch(P_BRANCH, publish=False)
        self.write(PARENT, card("56 — The parent", status="In Progress"),
                   "in-progress")
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.branch(C_BRANCH, start=P_BRANCH, publish=False)
        self.record_plan(CHILD, startBranch=P_BRANCH, prBase=P_BRANCH)
        with self.assertRaises(ValueError) as caught:
            changes.open_pr_now(CHILD)
        self.assertIn("Publish 56-the-parent.md first", str(caught.exception))
        self.assertEqual(self.remote_has(P_BRANCH), "", "the parent was pushed")
        self.assertEqual(self.remote_has(C_BRANCH), "", "the child was pushed")
        self.assertEqual(self.pushes(), [])

    def test_a_child_carrying_unpublished_parent_commits_refuses(self):
        published = self.branch(P_BRANCH)
        self.commit_on(P_BRANCH, "parent-local-only", publish=False)
        self.write(PARENT, card("56 — The parent", status="In Progress"),
                   "in-progress")
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        self.branch(C_BRANCH, start=P_BRANCH, publish=False)
        self.record_plan(CHILD, startBranch=P_BRANCH, prBase=P_BRANCH)
        with self.assertRaises(ValueError) as caught:
            changes.open_pr_now(CHILD)
        self.assertIn("1 commit of 56-the-parent.md", str(caught.exception))
        self.assertEqual(self.remote_has(P_BRANCH), published)
        self.assertEqual(self.remote_has(C_BRANCH), "")
        self.assertEqual(self.pushes(), [])

    def test_a_published_parent_is_targeted_and_never_pushed(self):
        published = self.branch(P_BRANCH)
        self.write(PARENT, card("56 — The parent"))
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        ctip = self.branch(C_BRANCH, start=P_BRANCH, publish=False)
        self.record_plan(CHILD, startBranch=P_BRANCH, prBase=P_BRANCH)
        # this checkout has parent commits the child does not carry
        self.commit_on(P_BRANCH, "parent-after-the-cut", publish=False)
        url = changes.open_pr_now(CHILD)
        self.assertTrue(url.endswith("/pull/7"))
        self.assertEqual(self.remote_has(P_BRANCH), published,
                         "opening the child moved the parent on the remote")
        self.assertEqual(self.remote_has(C_BRANCH), ctip)
        create = json.loads(self.pushes()[0])
        self.assertEqual(create[create.index("--base") + 1], P_BRANCH)

    def test_an_external_base_keeps_todays_behaviour(self):
        self.branch("release/2.0", publish=False)
        self.write(CHILD, card("57 — The child", base="release/2.0"))
        self.branch(C_BRANCH, start="release/2.0", publish=False)
        self.record_plan(CHILD, startBranch="release/2.0", prBase="release/2.0")
        changes.open_pr_now(CHILD)
        self.assertNotEqual(self.remote_has("release/2.0"), "")


# ── safety fix 2: cleanup keeps a branch something targets ─────────────


class CleanupKeepsATargetedBranch(StackCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, changes, "_poll_pr", changes._poll_pr)
        changes._poll_pr = lambda *a, **k: None
        self.patch(APP_MERGE="local")
        self.ptip = self.branch(P_BRANCH)
        self.write(PARENT, card("56 — The parent"))

    def remote_has(self, branch: str) -> str:
        return git(self.origin, "rev-parse", "--verify", "--quiet",
                   f"refs/heads/{branch}").stdout.strip()

    def said(self) -> str:
        return "\n".join(e.get("summary") or "" for e in state.BOARD_EVENTS)

    def complete(self) -> dict:
        return completion.complete_task(PARENT, "review")

    def test_nothing_targets_it_so_it_goes(self):
        result = self.complete()
        self.assertEqual(result["cleanup"]["remoteBranch"], "gone", result)
        self.assertEqual(self.remote_has(P_BRANCH), "")

    def test_a_card_on_the_board_still_targets_it(self):
        self.write(CHILD, card("57 — The child", base=P_BRANCH))
        result = self.complete()
        self.assertEqual(result["cleanup"]["remoteBranch"], "kept", result)
        self.assertEqual(self.remote_has(P_BRANCH), self.ptip)
        self.assertIn("57-the-child.md still targets it", self.said())

    def test_an_open_change_request_on_the_host_targets_it(self):
        os.environ["GH_TARGETING"] = json.dumps([
            {"url": "https://github.com/acme/widget/pull/9",
             "headRefName": "someone/else"}])
        result = self.complete()
        self.assertEqual(result["cleanup"]["remoteBranch"], "kept", result)
        self.assertEqual(self.remote_has(P_BRANCH), self.ptip)
        self.assertIn("an open PR targets it (https://github.com/acme/widget/pull/9)",
                      self.said())

    def test_a_host_that_cannot_answer_keeps_it(self):
        os.environ["GH_TARGETING"] = "fail"
        result = self.complete()
        self.assertEqual(result["cleanup"]["remoteBranch"], "kept", result)
        self.assertEqual(self.remote_has(P_BRANCH), self.ptip)
        self.assertIn("could not be established", self.said())

    def test_the_card_is_still_delivered_when_its_branch_is_kept(self):
        os.environ["GH_TARGETING"] = "fail"
        result = self.complete()
        self.assertTrue(result["moved"], result)
        self.assertTrue((self.tasks / "done" / PARENT).is_file())


# ── waiting-for-parent ─────────────────────────────────────────────────


class WaitingForParent(StackCase):

    def reading(self, name: str = CHILD) -> dict:
        found = stacks.board(taskfiles.collect())
        return delivery.compute(
            next(t for g in taskfiles.collect()["stages"] for t in g["tasks"]
                 if t["file"] == name),
            dict(delivery._context(), stacks=found), full=True)

    def test_an_ungated_child_waits_for_its_parent(self):
        self.parent_and_child()
        found = self.reading()
        self.assertEqual(found["status"]["word"], "waiting-for-parent")
        self.assertIn("Waiting for #56 to land", found["status"]["sentence"])
        self.assertEqual(found["status"]["tone"], "neutral")

    def test_a_child_whose_parent_landed_stops_waiting(self):
        ptip, _ = self.parent_and_child()
        self.delivered(PARENT, ptip)
        self.assertNotEqual(self.reading()["status"]["word"], "waiting-for-parent")

    def test_an_unstacked_card_never_waits(self):
        self.parent_and_child()
        self.assertNotEqual(self.reading(PARENT)["status"]["word"],
                            "waiting-for-parent")


if __name__ == "__main__":
    unittest.main()
