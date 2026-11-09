"""One validated execution plan, carried through a card's life (task 58).

The subject is the arithmetic of branches — which commit a worktree was
cut from, which branch a PR targets, what refuses before any of it exists
— so nothing here mocks git. Each case builds a disposable application
repository with a local bare remote, a prerequisite branch to stack on, a
stub adapter in place of a coding agent and a stub `gh` in place of
GitHub. No token is spent, no PR is opened and no remote but the one in
the temporary directory is ever touched.

The regression this exists for is card 57's: a card that had to be based
on `task/56-external-workspaces` while the operator's default was `main`,
with nowhere to say so. `TaskFiftySevensWrongBase` is that scenario,
twice — once as it went, once as it goes now.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import github  # noqa: E402
import plans  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CARD = "57-document-external-workspaces.md"
STEM = CARD[:-3]
BRANCH = f"task/{STEM}"
PREREQ = "task/56-external-workspaces"
PR_URL = "https://github.com/12vectors/bench/pull/59"

# An agent that does the job: one file, one commit, a clean exit. It never
# touches a branch but its own — the whole point of the card is that the
# lifecycle is bench's.
WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "docs.txt"), "w").write("the documentation\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "document it"], check=True)
print("WORK REPORT: documented")
"""

# gh, as far as these tests are concerned: every invocation is logged, and
# GH_BASE says what base an already-open PR has (empty = there is none).
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
open_base = os.environ.get("GH_BASE", "")
if args[:2] == ["pr", "create"]:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"] and any("baseRefName" in a for a in args):
    if not open_base:
        sys.exit(1)
    print(json.dumps({{"url": "{PR_URL}", "baseRefName": open_base}}))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"reviews": [], "reviewRequests": [],
                      "statusCheckRollup": [], "state": "OPEN",
                      "mergeable": "MERGEABLE",
                      "baseRefName": open_base or "main"}}))
else:
    sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def sha(cwd: Path, ref: str) -> str:
    return git(cwd, "rev-parse", ref).stdout.strip()


def card(title: str, *, status: str = "In Progress", base: str | None = None,
         pr_base: str | None = None, kind: str | None = None,
         depends: str | None = None, cards: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if base is not None:
        text += f"**Base branch:** {base}\n"
    if pr_base is not None:
        text += f"**PR base:** {pr_base}\n"
    if depends:
        text += f"**Depends on:** {depends}\n"
    text += "\nWhat this card is for, at enough length to be a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class PlanCase(unittest.TestCase):
    """A disposable application repo, a bare remote beside it, and a
    prerequisite branch to stack on.

    `LAYOUT` is where the board's own files sit relative to the
    application checkout. Filesystem layout must not change
    branch-selection semantics, so the resolution cases below are run
    under both — a vendored `.task-manager/` inside the project, and the
    flat arrangement a self-hosted source checkout has.
    """

    LAYOUT = "vendored"
    REMOTE = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        # the application's git: a bare remote and a clone of it
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main's line\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        if self.REMOTE:
            git(self.repo, "remote", "add", "origin", str(self.origin))
            git(self.repo, "push", "-q", "-u", "origin", "main")

        # the prerequisite: a branch cut from main with work of its own,
        # published, and then main moves on so the two genuinely differ
        git(self.repo, "checkout", "-q", "-b", PREREQ)
        (self.repo / "prereq.txt").write_text("the implementation\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "external workspaces")
        if self.REMOTE:
            git(self.repo, "push", "-q", "-u", "origin", PREREQ)
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "elsewhere.txt").write_text("landed on main\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "something else entirely")
        if self.REMOTE:
            git(self.repo, "push", "-q", "origin", "main")
        self.prereq_tip = sha(self.repo, PREREQ)
        self.main_tip = sha(self.repo, "main")

        # the board's own files, where this layout puts them
        tm = self.repo / ".task-manager" if self.LAYOUT == "vendored" else self.repo
        self.tasks = tm / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        local = tm / "manager" / "local"
        (local / "adapters" / config.ADAPTER).mkdir(parents=True)
        self.adapter = local / "adapters" / config.ADAPTER / "run"
        self.adapter_is(WORKS)

        gh = self.tmp / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.gh_log = self.tmp / "gh.log"
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)
        self.addCleanup(os.environ.pop, "GH_BASE", None)

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=tm, LOCAL=local,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.tmp / "plans",
                   AGENT_DIR=self.tmp / "agent",
                   SESSIONS_DIR=self.tmp / "sessions",
                   GH_BIN=str(gh), APP_BASE="main",
                   COMMIT_MOVES=False, SYNC=False, FETCH_TIMEOUT=10.0)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE):
            registry.clear()
            self.addCleanup(registry.clear)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

    # — the furniture —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def adapter_is(self, script: str) -> None:
        self.adapter.write_text(script, encoding="utf-8")
        self.adapter.chmod(self.adapter.stat().st_mode | stat.S_IEXEC)

    def write(self, text: str, *, name: str = CARD,
              stage: str = "in-progress") -> None:
        (self.tasks / stage / name).write_text(text, encoding="utf-8")

    def read(self, *, name: str = CARD, stage: str = "in-progress") -> dict:
        return taskfiles.read_task(self.tasks / stage / name, stage)

    def plan(self, *, name: str = CARD, stage: str = "in-progress") -> dict:
        return plans.preflight(name, stage)

    def run_agent(self, *, name: str = CARD) -> dict:
        """Launch, and wait for the whole ending — not just the process.

        The reaper flips the status, then files the report, moves the card
        and narrates. A test that stopped at the status flip would read the
        board mid-ending, so this waits for the line the reaper closes
        with: `start_agent` has already recorded one event, and the reaper
        records exactly one more.
        """
        record = agents.start_agent(name, "in-progress")
        marks = len(state.BOARD_EVENTS)
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"),
            "the stub agent never finished")
        self.assertTrue(wait_for(lambda: len(state.BOARD_EVENTS) > marks),
                        "the run never narrated its ending")
        return state.AGENTS[record["id"]]

    def branches(self) -> list[str]:
        return git(self.repo, "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/").stdout.split()

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines()]

    def summaries(self) -> list[str]:
        return [e.get("summary", "") for e in state.BOARD_EVENTS]

    def assertNothingStarted(self) -> None:
        """A refused launch leaves the card exactly as it was: no branch,
        no worktree, no run, and nothing claiming to be running."""
        self.assertNotIn(BRANCH, self.branches())
        self.assertFalse((config.WORKTREES / STEM).exists())
        self.assertEqual(agents.list_public(), [])
        self.assertIsNone(plans.recorded(CARD))


# ── the headers themselves ─────────────────────────────────────────────


class TheTwoHeaders(PlanCase):
    """`**Base branch:**` and `**PR base:**`, read and validated where the
    file is read — before any git command exists to run."""

    def test_both_headers_are_parsed(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        task = self.read()
        self.assertEqual(task["baseBranch"], PREREQ)
        self.assertEqual(task["prBase"], PREREQ)
        self.assertEqual(task["planDrift"], [])

    def test_a_card_with_neither_header_says_nothing(self):
        self.write(card("57 — Document it"))
        task = self.read()
        self.assertIsNone(task["baseBranch"])
        self.assertIsNone(task["prBase"])
        self.assertIsNone(plans.summary(task),
                          "a card on the default has nothing to show")

    def test_backticks_around_the_value_are_the_author_quoting(self):
        self.write(card("57 — Document it", base=f"`{PREREQ}`"))
        self.assertEqual(self.read()["baseBranch"], PREREQ)

    def test_a_note_after_the_value_is_for_the_reader(self):
        """The template's own shape — `value — note`, as **Priority** and
        **Depends on** take it — must not read as plan drift."""
        self.write(card("57 — Document it",
                        base=f"{PREREQ} — needs the workspace code",
                        pr_base=f"`{PREREQ}` - lands with it"))
        task = self.read()
        self.assertEqual(task["baseBranch"], PREREQ)
        self.assertEqual(task["prBase"], PREREQ)
        self.assertEqual(task["planDrift"], [])

    def test_the_templates_example_parses(self):
        template = (Path(__file__).resolve().parent.parent / "tasks"
                    / "task-template.md").read_text(encoding="utf-8")
        self.write(template)
        task = self.read()
        self.assertEqual(task["planDrift"], [])
        self.assertEqual(task["baseBranch"], "task/56-some-prerequisite")

    def test_a_value_that_is_not_a_branch_name_is_dropped_and_flagged(self):
        for bad in ("main; rm -rf /", "--upload-pack=evil", "a..b",
                    "$(whoami)", "main.lock", "ends/", "has space"):
            with self.subTest(bad=bad):
                self.write(card("57 — Document it", base=bad))
                task = self.read()
                self.assertIsNone(task["baseBranch"],
                                  f"{bad!r} must not become a base")
                self.assertTrue(task["planDrift"], f"{bad!r} was not flagged")
                self.assertIn("Base branch", task["planDrift"][0])

    def test_a_flagged_header_never_falls_back_to_the_default(self):
        """The failure being fixed is a silent default, so a header the
        parser rejected refuses the launch rather than reverting."""
        self.write(card("57 — Document it", base="main; rm -rf /"))
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("Base branch", str(caught.exception))
        self.assertNothingStarted()

    def test_ref_problem_accepts_the_shapes_a_branch_really_has(self):
        for good in ("main", "task/56-external-workspaces", "release/1.x",
                     "v2_fix", "a-b.c"):
            with self.subTest(good=good):
                self.assertIsNone(taskfiles.ref_problem(good))


# ── the default, unchanged ─────────────────────────────────────────────


class ACardWithNoMetadata(PlanCase):
    """Acceptance: a normal task keeps its established behaviour. Every
    assertion here is what the board did before plans existed."""

    def test_it_starts_from_the_newest_origin_base(self):
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startRef"], "origin/main")
        self.assertEqual(plan["startCommit"], self.main_tip)
        self.assertEqual(plan["prBase"], "main")
        self.assertEqual(plan["source"], "the application default")

    def test_its_worktree_is_cut_from_main_and_its_pr_targets_main(self):
        self.write(card("57 — Document it"))
        self.run_agent()
        worktree = config.WORKTREES / STEM
        self.assertTrue(worktree.exists())
        self.assertTrue((worktree / "elsewhere.txt").exists(),
                        "cut from main, so main's own work is in it")
        self.assertFalse((worktree / "prereq.txt").exists())
        self.assertEqual(plans.pr_base(CARD), "main")

    def test_no_remote_still_launches_from_head(self):
        self.patch(REPO=self.repo)
        git(self.repo, "remote", "remove", "origin")
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertIsNone(plan["startRef"])
        self.assertEqual(plan["startCommit"], self.main_tip)


# ── a card stacked on another branch ──────────────────────────────────


class ACardStackedOnAnotherBranch(PlanCase):
    """Acceptance: given default main and a card explicitly based on
    task/56 with PR target task/56, the plan says so, the worktree holds
    task/56's starting commit, the PR's diff is the card's own work, and
    the prerequisite branch is neither merged nor modified."""

    def setUp(self):
        super().setUp()
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))

    def test_the_plan_names_the_prerequisite_and_what_chose_it(self):
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startBranch"], PREREQ)
        self.assertEqual(plan["startCommit"], self.prereq_tip)
        self.assertEqual(plan["prBase"], PREREQ)
        self.assertEqual(plan["source"], "the card's **Base branch:** header")
        self.assertEqual(plan["prSource"], "the card's **PR base:** header")

    def test_the_worktree_holds_the_prerequisites_starting_commit(self):
        self.run_agent()
        worktree = config.WORKTREES / STEM
        self.assertTrue((worktree / "prereq.txt").exists(),
                        "the prerequisite's work is in the checkout")
        self.assertFalse((worktree / "elsewhere.txt").exists(),
                         "and main's divergence is not")
        self.assertEqual(
            git(self.repo, "merge-base", "--is-ancestor",
                self.prereq_tip, BRANCH).returncode, 0,
            "the branch does not contain the commit it was planned from")

    def test_the_diff_against_the_prerequisite_is_only_this_cards_work(self):
        self.run_agent()
        changed = git(self.repo, "diff", "--name-only",
                      f"{PREREQ}...{BRANCH}").stdout.split()
        self.assertEqual(changed, ["docs.txt"])

    def test_the_prerequisite_branch_is_neither_merged_nor_modified(self):
        self.run_agent()
        self.assertEqual(sha(self.repo, PREREQ), self.prereq_tip,
                         "the prerequisite branch was moved")
        self.assertNotEqual(
            git(self.repo, "merge-base", "--is-ancestor",
                BRANCH, PREREQ).returncode, 0,
            "the card's work was merged into its prerequisite")

    def test_the_pr_is_opened_against_the_prerequisite(self):
        self.run_agent()
        self.assertEqual(taskfiles.find_stage_of(CARD), "review")
        github.open_pr_now(CARD)
        created = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][created[0].index("--base") + 1], PREREQ)

    def test_the_ticker_says_where_the_work_started(self):
        self.run_agent()
        line = next(s for s in self.summaries() if "started on" in s)
        self.assertIn(PREREQ, line)
        self.assertIn("the card's **Base branch:** header", line)

    def test_a_pr_base_on_its_own_starts_from_the_default_and_says_so(self):
        """Legal, and the plan says what it costs: the diff will carry
        whatever main has that the target does not."""
        self.write(card("57 — Document it", pr_base=PREREQ))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startBranch"], "main")
        self.assertEqual(plan["prBase"], PREREQ)
        self.assertTrue(any("the PR's diff will include" in n
                            for n in plan["notes"]))


class ALegacySourceLayout(ACardStackedOnAnotherBranch):
    """Acceptance: the same answers in the other installation layout. The
    board's own files move; the branch arithmetic does not."""

    LAYOUT = "flat"


# ── what refuses, and when ────────────────────────────────────────────


class WhatPreflightRefuses(PlanCase):
    """Acceptance: a refusal lands before a branch, a worktree or a
    coding-agent launch, with an actionable explanation and no false
    running state."""

    def test_a_base_no_branch_has_is_refused_by_name(self):
        self.write(card("57 — Document it", base="task/99-never-existed"))
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertIn("task/99-never-existed", plan["blockers"][0])
        self.assertIn("does not exist", plan["blockers"][0])
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertNothingStarted()

    def test_a_pr_target_no_branch_has_is_refused_before_the_work(self):
        self.write(card("57 — Document it", pr_base="task/99-never-existed"))
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("PR target", str(caught.exception))
        self.assertNothingStarted()

    def test_an_unreachable_remote_never_substitutes_an_explicit_base(self):
        """The whole point: an explicit base that cannot be resolved is a
        refusal, never the application default."""
        git(self.repo, "branch", "-D", PREREQ)
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        self.write(card("57 — Document it", base=PREREQ))
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertNotEqual(plan["startCommit"], self.main_tip)
        self.assertNotEqual(plan["startBranch"], "main")
        self.assertNothingStarted()

    def test_unpublished_commits_on_the_base_are_named_not_silently_used(self):
        """The published tip is what a reviewer's diff is against, so that
        is what the work starts from — and the note says which way the two
        copies differ rather than leaving it to be discovered."""
        git(self.repo, "worktree", "add", "-q", str(self.tmp / "pre"), PREREQ)
        (self.tmp / "pre" / "unpushed.txt").write_text("local only\n", encoding="utf-8")
        git(self.tmp / "pre", "add", "-A")
        git(self.tmp / "pre", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", "not published")
        self.write(card("57 — Document it", base=PREREQ))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startCommit"], self.prereq_tip)
        self.assertTrue(any("not published" in n for n in plan["notes"]))

    def test_a_base_the_remote_has_moved_on_is_fetched_and_said_so(self):
        git(self.repo, "push", "-q", "origin", f"{self.main_tip}:{PREREQ}", "--force")
        self.write(card("57 — Document it", base=PREREQ))
        plan = self.plan()
        self.assertEqual(plan["startCommit"], self.main_tip)
        self.assertTrue(any("ahead of this checkout" in n for n in plan["notes"]))

    def test_a_base_only_the_remote_has_is_fetched_rather_than_refused(self):
        git(self.repo, "branch", "-D", PREREQ)
        self.write(card("57 — Document it", base=PREREQ))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startRef"], f"origin/{PREREQ}")
        self.assertEqual(plan["startCommit"], self.prereq_tip)

    def test_the_refusal_leaves_no_claim_on_the_card(self):
        self.patch(COMMIT_MOVES=True)
        self.write(card("57 — Document it", base="task/99-never-existed"))
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertIsNone(self.read()["assignee"],
                          "a blocked preflight claimed the card anyway")

    def test_a_refusal_after_the_claim_releases_it(self):
        """The second preflight can refuse what the first allowed — a fetch
        that failed in between. The launch did not happen, so the claim it
        made is given back."""
        self.patch(COMMIT_MOVES=True)
        self.write(card("57 — Document it", base=PREREQ))
        calls = []
        real = plans.preflight

        def flaky(*args, **kwargs):
            plan = real(*args, **kwargs)
            calls.append(plan)
            if len(calls) == 2:
                plan["blockers"].append("the fetch went away in between")
            return plan

        plans.preflight = flaky
        self.addCleanup(setattr, plans, "preflight", real)
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("went away", str(caught.exception))
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.read()["assignee"],
                          "a launch that never happened kept its claim")
        self.assertNothingStarted()

    def test_a_foreign_directory_at_the_worktree_path_is_refused(self):
        self.write(card("57 — Document it"))
        squatter = config.WORKTREES / STEM
        squatter.mkdir(parents=True)
        (squatter / "someone-elses.txt").write_text("mine\n", encoding="utf-8")
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertIn("not a git worktree", plan["blockers"][0])
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertTrue((squatter / "someone-elses.txt").exists(),
                        "bench cleared a directory that was not its own")


# ── GitHub, when it is not there ──────────────────────────────────────


class WhatThisBoardCanActuallyDeliver(PlanCase):
    """Acceptance: a disabled GitHub integration cannot appear ready for
    requested automatic PR delivery, and explicitly local-only work
    remains supported."""

    def test_no_gh_is_local_only_and_names_what_is_missing(self):
        self.patch(GH_BIN=str(self.tmp / "no-such-gh"))
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["delivery"], "local-only")
        self.assertFalse(plan["capabilities"]["pr"])
        self.assertTrue(any("not installed" in n for n in plan["notes"]))

    def test_no_remote_is_local_only_too(self):
        git(self.repo, "remote", "remove", "origin")
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["delivery"], "local-only")
        self.assertTrue(any("no git remote" in n for n in plan["notes"]))

    def test_local_only_work_still_runs_and_still_commits(self):
        self.patch(GH_BIN=str(self.tmp / "no-such-gh"))
        self.write(card("57 — Document it"))
        record = self.run_agent()
        self.assertEqual(record["status"], "done")
        self.assertIn(BRANCH, self.branches())
        self.assertEqual(taskfiles.find_stage_of(CARD), "review")

    def test_a_card_that_asked_for_a_pr_is_told_it_cannot_have_one(self):
        self.patch(GH_BIN=str(self.tmp / "no-such-gh"))
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        plan = self.plan()
        self.assertEqual(plan["delivery"], "local-only")
        note = next(n for n in plan["notes"] if "local-only" in n)
        self.assertIn(PREREQ, note)
        self.assertIn("this card asks for", note)


# ── one plan, read by everything downstream ───────────────────────────


class TheRecordedPlanGoverns(PlanCase):
    """Acceptance: a global-base change between launch and review does not
    retarget existing work, and automatic and explicit PR opening pick the
    same base."""

    def setUp(self):
        super().setUp()
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))

    def test_the_plan_is_recorded_before_the_work_begins(self):
        self.run_agent()
        saved = plans.recorded(CARD)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["prBase"], PREREQ)
        self.assertEqual(saved["startCommit"], self.prereq_tip)
        self.assertEqual(saved["provenance"], "planned")

    def test_the_record_keeps_no_path_from_this_machine(self):
        self.run_agent()
        travelling = json.dumps(plans.portable(plans.recorded(CARD)))
        self.assertNotIn(str(self.tmp), travelling)
        self.assertNotIn("worktree", travelling)
        self.assertIn(self.prereq_tip, travelling)

    def test_a_later_change_to_the_global_default_retargets_nothing(self):
        self.run_agent()
        self.patch(APP_BASE="trunk")
        self.assertEqual(plans.pr_base(CARD), PREREQ)
        self.assertEqual(github._pr_base(self.read(stage="review")), PREREQ)

    def test_the_automatic_open_and_the_explicit_one_agree(self):
        self.run_agent()
        github.maybe_open_pr(CARD)
        automatic = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(automatic), 1)
        self.assertEqual(automatic[0][automatic[0].index("--base") + 1], PREREQ)
        # the second ask is refused by the **PR:** line, not by a second base
        with self.assertRaises(ValueError):
            github.open_pr_now(CARD)
        self.assertEqual(
            len([c for c in self.gh_calls() if c[:2] == ["pr", "create"]]), 1)

    def test_plan_identity_survives_a_restart(self):
        self.run_agent()
        before = plans.recorded(CARD)
        # a restart forgets every registry; the plan is on disk, so it does not
        state.AGENTS.clear()
        self.assertEqual(plans.recorded(CARD), before)
        self.assertEqual(plans.pr_base(CARD), PREREQ)

    def test_the_agents_brief_states_the_plan_without_a_local_path(self):
        brief = plans.brief(plans.require(self.plan()))
        self.assertIn(PREREQ, brief)
        self.assertIn(BRANCH, brief)
        self.assertNotIn(str(self.tmp), brief)


class AStackedCardIsNotMergedIntoTheDefault(PlanCase):
    """Acceptance: do not merge a stacked card into the application
    default by accident."""

    def test_merge_and_clean_up_refuses_and_names_the_target(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        self.assertIn(PREREQ, str(caught.exception))
        self.assertEqual(taskfiles.find_stage_of(CARD), "review",
                         "the card moved on a merge that never happened")
        self.assertIn(BRANCH, self.branches())

    def test_team_mode_refuses_it_for_the_same_reason(self):
        """`done/` means merged into the application base in both modes, so
        the refusal is about what finishing a card means rather than about
        what the local merge can do."""
        self.patch(SYNC=True, COMMIT_MOVES=True)
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        github.open_pr_now(CARD)
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        self.assertIn("without its work reaching main", str(caught.exception))
        self.assertEqual([c for c in self.gh_calls() if c[:2] == ["pr", "merge"]],
                         [], "origin was asked to merge it anyway")

    def test_once_the_prerequisite_lands_a_replan_makes_it_finishable(self):
        """The documented way through: the stack lands by its own PR, the
        card's target goes with it, and a replan onto the base is the
        person's decision that lets this action finish the card."""
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--no-edit", PREREQ)
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "branch", "-D", PREREQ)
        git(self.repo, "push", "-q", "origin", "--delete", PREREQ)
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        path = self.tasks / "review" / CARD
        path.write_text(path.read_text(encoding="utf-8").replace(PREREQ, "main"),
                        encoding="utf-8")
        plans.replan(CARD, "review")
        self.assertTrue(github.complete_task(CARD, "review")["merged"])
        self.assertEqual(taskfiles.find_stage_of(CARD), "done")

    def test_an_ordinary_card_still_merges_as_it_always_did(self):
        self.write(card("57 — Document it"))
        self.run_agent()
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(taskfiles.find_stage_of(CARD), "done")
        self.assertNotIn(BRANCH, self.branches())
        self.assertIsNone(plans.recorded(CARD),
                          "the plan outlived the branch it described")

    def test_a_target_that_vanished_stops_the_merge_and_says_why(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        git(self.repo, "branch", "-D", PREREQ)
        git(self.repo, "push", "-q", "origin", "--delete", PREREQ)
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        self.assertIn("no longer exists", str(caught.exception))
        self.assertIn("replan", str(caught.exception))


class ACardOnTheDefaultWhenTheDefaultMoves(PlanCase):
    """Acceptance: a global-base change between launch and review does not
    retarget existing work — for an ordinary card too, whose plan the
    application default chose. The card's requirements did not change, so
    nothing refuses it and nothing asks for a replan."""

    def setUp(self):
        super().setUp()
        git(self.repo, "branch", "trunk", "main")
        git(self.repo, "push", "-q", "origin", "trunk")
        self.write(card("57 — Document it"))
        self.run_agent()
        github.open_pr_now(CARD)
        self.patch(APP_BASE="trunk")

    def test_resuming_keeps_the_recorded_plan(self):
        plan = plans.preflight(CARD, "review")
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startBranch"], "main")
        self.assertEqual(plan["prBase"], "main")
        self.assertTrue(any("next launch" in note for note in plan["notes"]))

    def test_acting_on_the_pr_still_resolves_against_the_recorded_base(self):
        self.adapter_is(ECHOES_PROMPT)
        record = agents.start_pr_fix(CARD, "review")
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("git merge origin/main", brief)
        self.assertNotIn("origin/trunk", brief)
        self.assertEqual(plans.recorded(CARD)["prBase"], "main")

    def test_merge_and_clean_up_finishes_it_into_what_it_was_planned_on(self):
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(taskfiles.find_stage_of(CARD), "done")

    def test_acting_on_the_pr_rides_its_purpose_model_and_template(self):
        """Task 77: the PR-fixes purpose's model and a local act-pr.md
        override reach the launch, and the record names both."""
        import templates
        self.addCleanup(setattr, config, "_ENV_LOCKED", config._ENV_LOCKED)
        config._ENV_LOCKED = {}
        self.addCleanup((config.LOCAL / ".env").unlink, missing_ok=True)
        (config.LOCAL / ".env").write_text("BOARD_AGENT_MODEL_ACT_PR=fix-model\n",
                                           encoding="utf-8")
        (config.LOCAL / "prompts").mkdir(exist_ok=True)
        custom = templates.builtin("act-pr.md") + "\nLOCAL FIX NOTE\n"
        (config.LOCAL / "prompts" / "act-pr.md").write_text(custom, encoding="utf-8")
        self.addCleanup((config.LOCAL / "prompts" / "act-pr.md").unlink)
        self.adapter_is(ECHOES_PROMPT)
        record = agents.start_pr_fix(CARD, "review")
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("LOCAL FIX NOTE", brief)
        self.assertEqual(state.AGENTS[record["id"]]["model"], "fix-model")
        frozen = profiles.recorded(record["id"])
        self.assertEqual(frozen["template"]["source"], "local")
        self.assertEqual(frozen["template"]["name"], "act-pr.md")


class ActingOnTheReviewValidatesTheSameTarget(PlanCase):
    """Acceptance: the target is validated through review and fix too — a
    run that pushes to a PR must not resolve conflicts against a base
    nobody chose."""

    def setUp(self):
        super().setUp()
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        github.open_pr_now(CARD)

    def fix_and_wait(self) -> None:
        record = agents.start_pr_fix(CARD, "review")
        marks = len(state.BOARD_EVENTS)
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        self.assertTrue(wait_for(lambda: len(state.BOARD_EVENTS) > marks))

    def test_the_run_is_told_the_recorded_base(self):
        self.adapter_is(ECHOES_PROMPT)
        self.fix_and_wait()
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn(f"git merge origin/{PREREQ}", brief)
        self.assertNotIn("git merge origin/main", brief)

    def test_a_base_that_vanished_refuses_the_run(self):
        """The prerequisite merged and was deleted while this card sat in
        review. The run that would push to the PR does not start, and the
        refusal names the branch rather than quietly resolving against
        whatever the default happens to be."""
        git(self.repo, "branch", "-D", PREREQ)
        git(self.repo, "push", "-q", "origin", "--delete", PREREQ)
        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(CARD, "review")
        message = str(caught.exception)
        self.assertIn(PREREQ, message)
        self.assertIn("does not exist", message)
        self.assertEqual(agents.working_on({CARD}), [],
                         "a refused act-pr left a run behind")

    def test_a_changed_requirement_refuses_the_run(self):
        # the headers are edited in place, so the **PR:** line the board
        # wrote survives — this is the card changing under its own PR
        path = self.tasks / "review" / CARD
        path.write_text(path.read_text(encoding="utf-8").replace(PREREQ, "main"),
                        encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(CARD, "review")
        self.assertIn("replan", str(caught.exception))
        self.assertEqual(agents.working_on({CARD}), [])


class NothingAboutAPlanReachesSharedHistory(PlanCase):
    """Acceptance: machine-local paths and runtime details stay out of
    shared ticket history — which is also what stops a replica repeating
    a side effect it can see no record of."""

    def test_the_record_lives_under_local_state_and_not_in_tasks(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        self.assertTrue(plans.plan_path(CARD).is_file())
        self.assertEqual(plans.plan_path(CARD).parent, config.PLANS_DIR)
        self.assertNotIn(str(self.tasks), str(config.PLANS_DIR))

    def test_the_task_file_gains_no_plan_of_its_own(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        text = (self.tasks / "review" / CARD).read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn("**Plan:**", text)
        # the headers the author wrote are untouched
        self.assertIn(f"**Base branch:** {PREREQ}", text)


# ── resuming, and the one way to retarget ─────────────────────────────


class ResumingAgainstTheRecordedPlan(PlanCase):
    """Acceptance: resume/replan checks preserve untracked files, edits
    and commits, and reject mismatched plans without destructive repair."""

    def setUp(self):
        super().setUp()
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        self.worktree = config.WORKTREES / STEM
        taskfiles.move_task(CARD, "review", "in-progress")

    def dirty_it(self) -> None:
        (self.worktree / "scratch.txt").write_text("untracked\n", encoding="utf-8")
        (self.worktree / "docs.txt").write_text("edited, uncommitted\n",
                                                encoding="utf-8")

    def test_a_relaunch_continues_the_branch_and_keeps_what_is_in_it(self):
        self.dirty_it()
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertTrue(plan["continuing"])
        self.assertTrue(any("uncommitted" in n for n in plan["notes"]))
        self.adapter_is("#!/usr/bin/env python3\nprint('WORK REPORT: more')\n")
        self.run_agent()
        self.assertTrue((self.worktree / "scratch.txt").exists(),
                        "an untracked file was thrown away")
        self.assertEqual((self.worktree / "docs.txt").read_text(),
                         "edited, uncommitted\n")

    def test_a_changed_requirement_demands_an_explicit_replan(self):
        self.write(card("57 — Document it"))        # the header came off
        self.dirty_it()
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertIn("has changed since the work started", plan["blockers"][0])
        self.assertIn("replan", plan["blockers"][0])
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertTrue((self.worktree / "scratch.txt").exists())
        self.assertIn(BRANCH, self.branches())
        self.assertEqual(plans.recorded(CARD)["prBase"], PREREQ,
                         "the refused launch rewrote the record")

    def test_replan_records_the_new_plan_when_the_branch_allows_it(self):
        """The branch was cut from task/56, which was cut from main, so it
        does contain main — retargeting to main is honest."""
        self.write(card("57 — Document it"))
        result = plans.replan(CARD, "in-progress")
        self.assertEqual(result["plan"]["prBase"], "main")
        self.assertEqual(result["previous"]["prBase"], PREREQ)
        self.assertEqual(plans.pr_base(CARD), "main")
        self.adapter_is("#!/usr/bin/env python3\nprint('WORK REPORT: more')\n")
        self.run_agent()                              # and it starts again

    def test_replan_refuses_a_base_the_branch_does_not_contain(self):
        # main moved on after the prerequisite was cut, so a branch cut
        # from the prerequisite does not contain main's tip — which is
        # exactly a base this branch was not built on.
        git(self.repo, "branch", "task/90-unrelated", "main")
        self.write(card("57 — Document it", base="task/90-unrelated"))
        with self.assertRaises(ValueError) as caught:
            plans.replan(CARD, "in-progress")
        self.assertIn("will not guess", str(caught.exception))
        self.assertEqual(plans.recorded(CARD)["prBase"], PREREQ,
                         "a refused replan changed the record")
        self.assertIn(BRANCH, self.branches())

    def test_a_branch_rewritten_under_its_plan_is_refused_not_repaired(self):
        git(self.worktree, "reset", "-q", "--hard", "main")
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertIn("replan", plan["blockers"][0])


class ARunFromBeforePlansExisted(PlanCase):
    """Acceptance: existing cards and worktrees without a recorded plan
    keep a documented compatible path, or get an actionable refusal for
    the ambiguous cases — and nothing invents a provenance."""

    def setUp(self):
        super().setUp()
        # a branch and a worktree made the old way: cut from main, with
        # commits, and no plan anywhere
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        self.worktree = config.WORKTREES / STEM
        git(self.repo, "worktree", "add", "-q", "--no-track", "-b", BRANCH,
            str(self.worktree), "main")
        (self.worktree / "legacy.txt").write_text("done by hand\n", encoding="utf-8")
        git(self.worktree, "add", "-A")
        git(self.worktree, "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", "legacy work")

    def test_an_ordinary_legacy_run_is_adopted_and_keeps_working(self):
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertTrue(plan["continuing"])
        self.adapter_is("#!/usr/bin/env python3\nprint('WORK REPORT: carried on')\n")
        self.run_agent()
        saved = plans.recorded(CARD)
        self.assertEqual(saved["prBase"], "main")
        self.assertTrue((self.worktree / "legacy.txt").exists())

    def test_adoption_says_it_was_adopted_rather_than_planned(self):
        self.write(card("57 — Document it"))
        # main has moved on since the branch was cut, so the branch is not
        # built on the commit the default now resolves to
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "later.txt").write_text("after\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "later")
        git(self.repo, "push", "-q", "origin", "main")
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["provenance"], "adopted")
        self.assertIn("adopted", plan["source"])
        self.assertTrue(any("read back out of git" in n for n in plan["notes"]))

    def test_an_ambiguous_legacy_run_gets_the_refusal_not_a_guess(self):
        self.write(card("57 — Document it", base=PREREQ))
        plan = self.plan()
        self.assertTrue(plan["blockers"])
        self.assertIn("no recorded plan", plan["blockers"][0])
        self.assertIn("will not rebase", plan["blockers"][0])
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertTrue((self.worktree / "legacy.txt").exists())

    def test_being_behind_the_default_base_is_not_ambiguous(self):
        """Every branch falls behind its base the moment the base moves,
        so that arithmetic must never read as "cut from somewhere else" —
        a legacy run on the default keeps working either way."""
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "later.txt").write_text("after\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "later")
        git(self.repo, "push", "-q", "origin", "main")
        self.write(card("57 — Document it", base="main"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["provenance"], "adopted")

    def test_opening_a_pr_on_legacy_work_pins_its_target(self):
        self.write(card("57 — Document it"))
        taskfiles.move_task(CARD, "in-progress", "review")
        github.open_pr_now(CARD)
        saved = plans.recorded(CARD)
        self.assertEqual(saved["prBase"], "main")
        self.assertEqual(saved["provenance"], "adopted",
                         "nothing may claim this branch was planned")


# ── a PR already aimed somewhere else ─────────────────────────────────


class AnExistingPRWithAnotherTarget(PlanCase):
    """Acceptance: mismatched existing PR targets are reported rather than
    duplicated or silently retargeted."""

    def test_opening_is_refused_and_names_both_bases(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        os.environ["GH_BASE"] = "main"          # GitHub already has one into main
        with self.assertRaises(ValueError) as caught:
            github.open_pr_now(CARD)
        message = str(caught.exception)
        self.assertIn("main", message)
        self.assertIn(PREREQ, message)
        self.assertEqual([c for c in self.gh_calls() if c[:2] == ["pr", "create"]],
                         [], "a PR was created over a contradiction")

    def test_the_poll_folds_the_mismatch_into_the_cards_state(self):
        entry = github._fold({"reviews": [{"state": "APPROVED"}],
                              "reviewRequests": [], "statusCheckRollup": [],
                              "baseRefName": "main"}, {}, PREREQ)
        self.assertEqual(entry["baseMismatch"], "main")
        self.assertEqual(entry["verdict"], "red",
                         "an approval cannot green a PR aimed elsewhere")
        self.assertIn(f"open against main, not {PREREQ}", entry["detail"])

    def test_an_agreeing_pr_folds_to_nothing(self):
        entry = github._fold({"reviews": [], "reviewRequests": [],
                              "statusCheckRollup": [],
                              "baseRefName": PREREQ}, {}, PREREQ)
        self.assertIsNone(entry["baseMismatch"])
        self.assertEqual(entry["verdict"], "pending")


# ── phases keep their own answer ──────────────────────────────────────


class APhaseMemberInheritsItsPhase(PlanCase):
    """Acceptance: phase-member inheritance remains correct, and a member
    that names its own base is rejected rather than silently resolved."""

    PHASE = "40-ship-the-site.md"
    PHASE_BRANCH = "phase/40-ship-the-site"

    def setUp(self):
        super().setUp()
        self.write(card("40 — Ship the site", kind="Phase",
                        cards="- 57 — Document it\n"),
                   name=self.PHASE, stage="in-progress")

    def start_the_phase(self) -> str:
        git(self.repo, "branch", self.PHASE_BRANCH, PREREQ)
        return sha(self.repo, self.PHASE_BRANCH)

    def test_a_member_starts_from_the_phase_branch_and_targets_it(self):
        tip = self.start_the_phase()
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["startRef"], self.PHASE_BRANCH)
        self.assertEqual(plan["startCommit"], tip)
        self.assertEqual(plan["prBase"], self.PHASE_BRANCH)
        self.assertIn("membership of phase 40", plan["source"])

    def test_acting_on_a_members_pr_keeps_the_phase_branch(self):
        """↻ act on PR reads the card woven: a member read on its own looks
        like an ordinary card on `main`, which would refuse every member the
        runner launched as "the plan has changed", or tell the run to merge
        `main` into a branch whose PR targets the phase."""
        self.start_the_phase()
        self.write(card("57 — Document it"))
        self.run_agent()
        self.assertEqual(plans.recorded(CARD)["prBase"], self.PHASE_BRANCH)
        github.open_pr_now(CARD)
        self.adapter_is(ECHOES_PROMPT)
        record = agents.start_pr_fix(CARD, "review")
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn(self.PHASE_BRANCH, brief)
        self.assertNotIn("git merge origin/main", brief)
        self.assertEqual(plans.recorded(CARD)["prBase"], self.PHASE_BRANCH)

    def test_a_member_of_an_unstarted_phase_falls_back_to_the_default(self):
        """Unchanged behaviour: a phase with no branch has nothing to
        stack on, so the card starts where it always did."""
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startRef"], "origin/main")
        self.assertEqual(plan["prBase"], "main")

    def test_a_member_that_names_its_own_base_is_flagged_on_both_cards(self):
        self.start_the_phase()
        self.write(card("57 — Document it", base=PREREQ))
        board = {task["file"]: task for group in taskfiles.collect()["stages"]
                 for task in group["tasks"]}
        for name in (CARD, self.PHASE):
            with self.subTest(card=name):
                drift = board[name]["planDrift"]
                self.assertTrue(drift)
                self.assertIn("conflicts with membership of phase", drift[0])

    def test_the_conflict_refuses_the_launch_before_anything_exists(self):
        self.start_the_phase()
        self.write(card("57 — Document it", pr_base="main"))
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("conflicts with membership", str(caught.exception))
        self.assertNothingStarted()


# ── the two gits stay apart ───────────────────────────────────────────


class ApplicationGitAndWorkStateGit(PlanCase):
    """Acceptance: application git and work-state git remain independent.
    One layout answers both with the same checkout; the question is asked
    through separate doors, so a layout that separates them later changes
    no branch-selection semantics."""

    def test_branch_selection_asks_the_application_repo(self):
        self.write(card("57 — Document it", base=PREREQ))
        asked: list[Path] = []
        original = config.app_repo
        self.addCleanup(setattr, config, "app_repo", original)

        def watched():
            asked.append(original())
            return original()

        config.app_repo = watched
        self.assertEqual(self.plan()["startCommit"], self.prereq_tip)
        self.assertTrue(asked, "the plan resolved a branch without asking "
                               "which repository holds the application")
        self.assertEqual(set(asked), {self.repo})

    def test_the_application_default_is_the_operators_setting(self):
        git(self.repo, "branch", "-m", "main", "trunk")
        git(self.repo, "push", "-q", "origin", "HEAD:trunk")
        self.patch(APP_BASE="trunk")
        self.write(card("57 — Document it"))
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["startBranch"], "trunk")
        self.assertEqual(plan["prBase"], "trunk")


# ── the regression this card exists for ───────────────────────────────


class TaskFiftySevensWrongBase(PlanCase):
    """Card 57 needed to be stacked on `task/56-external-workspaces` while
    the operator's default was `main`, and the requirement existed only in
    prose: the board selected the default, created the worktree, and the
    agent it then launched could do nothing but decline.

    This is that card, twice. The live ticket is not touched by any of it
    — only a disposable repository with the same shape.
    """

    def test_prose_alone_still_selects_the_default_and_says_so(self):
        """A `**Depends on:**` list is not an instruction to stack. The
        board must not read one as one, and the plan has to say plainly
        which structured input chose the base so the gap is visible."""
        self.write(card("57 — Document it", depends="56")
                   + "\nThis documents the work on `task/56-external-workspaces`.\n")
        plan = self.plan()
        self.assertEqual(plan["startBranch"], "main")
        self.assertEqual(plan["source"], "the application default")
        self.assertEqual(self.read()["dependsOn"], ["56"])

    def test_the_header_is_how_the_requirement_becomes_machinery(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ,
                        depends="56"))
        self.run_agent()
        worktree = config.WORKTREES / STEM
        self.assertTrue((worktree / "prereq.txt").exists(),
                        "the run started on the wrong code again")
        self.assertEqual(plans.pr_base(CARD), PREREQ)
        self.assertEqual(sha(self.repo, PREREQ), self.prereq_tip,
                         "the prerequisite was modified to make the card work")

    def test_the_operators_global_default_was_never_touched(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        self.assertEqual(config.APP_BASE, "main",
                         "one card changed the board's default for every card")


# ── what the agent is told, and what the board shows ─────────────────


# A stub that writes down the brief it was handed instead of doing work,
# so a test can read exactly what a real launch passes the vendor.
ECHOES_PROMPT = """#!/usr/bin/env python3
import os
open(os.path.join(os.environ["AGENT_CWD"], "prompt.txt"), "w").write(
    os.environ["AGENT_PROMPT"])
print("WORK REPORT: wrote the brief down")
"""


class TheAgentIsToldThePlan(PlanCase):
    """Acceptance: the resolved context reaches the coding agent so it can
    verify the checkout rather than choose a lifecycle of its own."""

    def test_the_brief_states_the_branch_the_base_and_the_pr_target(self):
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.adapter_is(ECHOES_PROMPT)
        self.run_agent()
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn(BRANCH, brief)
        self.assertIn(PREREQ, brief)
        self.assertIn(self.prereq_tip[:12], brief)
        self.assertIn("the card's **Base branch:** header", brief)

    def test_the_brief_forbids_repairing_the_checkout(self):
        self.write(card("57 — Document it"))
        self.adapter_is(ECHOES_PROMPT)
        self.run_agent()
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("The board resolved and validated this branch's whole git "
                      "lifecycle", brief)
        for forbidden in ("switch branches", "rebase", "add a worktree"):
            self.assertIn(forbidden, brief)

    def test_the_brief_carries_no_path_from_this_machine(self):
        self.write(card("57 — Document it", base=PREREQ))
        self.adapter_is(ECHOES_PROMPT)
        self.run_agent()
        brief = (config.WORKTREES / STEM / "prompt.txt").read_text(encoding="utf-8")
        plan_block = brief.split("- Branch:", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn(str(self.tmp), plan_block)


class TheBoardShowsThePlanBeforeTheLaunch(PlanCase):
    """Acceptance: the resolved plan or the blocking reason is on the board
    before anything starts — including the starting branch and PR target."""

    def test_the_state_payload_carries_a_plan_for_a_stacked_card(self):
        import httpd                                  # noqa: PLC0415
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        payload = httpd.state_payload()
        self.assertEqual(payload["appBase"], "main")
        shape = payload["plans"][CARD]
        self.assertEqual(shape["startBranch"], PREREQ)
        self.assertEqual(shape["prBase"], PREREQ)
        self.assertFalse(shape["recorded"])

    def test_an_ordinary_card_puts_nothing_in_the_payload(self):
        import httpd                                  # noqa: PLC0415
        self.write(card("57 — Document it"))
        self.assertEqual(httpd.state_payload()["plans"], {},
                         "a board of ordinary cards must render as it always did")

    def test_a_drifting_header_reaches_the_payload_as_drift(self):
        import httpd                                  # noqa: PLC0415
        self.write(card("57 — Document it", base="not a branch"))
        shape = httpd.state_payload()["plans"][CARD]
        self.assertTrue(shape["drift"])
        self.assertIn("Base branch", shape["drift"][0])

    def test_the_recorded_plan_is_what_the_payload_shows_afterwards(self):
        import httpd                                  # noqa: PLC0415
        self.write(card("57 — Document it", base=PREREQ, pr_base=PREREQ))
        self.run_agent()
        shape = httpd.state_payload()["plans"][CARD]
        self.assertTrue(shape["recorded"])
        self.assertEqual(shape["startCommit"], self.prereq_tip)
        self.assertEqual(shape["provenance"], "planned")


class ThePageReadsTheSameAnswer(unittest.TestCase):
    """board.html has no frontend runner, so these are source-level
    invariants: the chip, the drift pill, the read-only preflight and the
    one action that retargets work all have to exist and read from the
    server's answer rather than inventing their own."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")

    def test_the_chip_reads_the_servers_per_card_plan(self):
        self.assertIn("(S.state.plans || {})[task.file]", self.html)
        self.assertIn("planChipLabel", self.html)
        self.assertIn("data-plan", self.html)

    def test_a_card_with_no_plan_gets_no_chip(self):
        """The guard, literally: `if (planned)`. Without it every card
        would grow a chip saying `main`."""
        self.assertIn("if (planned) {", self.html)

    def test_plan_drift_is_flagged_beside_status_and_phase_drift(self):
        self.assertIn("task.planDrift", self.html)
        self.assertIn(">plan drift</span>", self.html)

    def test_the_preflight_is_a_get_and_the_replan_a_post(self):
        self.assertIn("fetch('/api/plan?file='", self.html)
        self.assertIn("'/api/plan/replan'", self.html)
        replan = self.html.split("async function replan(task)", 1)[1][:400]
        self.assertIn("method: 'POST'", replan)

    def test_a_mismatched_pr_target_is_worn_rather_than_fixed(self):
        self.assertIn("prState.baseMismatch", self.html)
        self.assertIn("Nothing was retargeted", self.html)

    def test_the_conflicts_chip_names_the_base_the_card_targets(self):
        self.assertIn("prState.wantBase", self.html)


class ThePromptsCarryThePlan(unittest.TestCase):
    """The placeholder has to be in the template, or the board would fill
    a brief that says nothing about the lifecycle it just resolved."""

    PROMPTS = REPO / "manager" / "core" / "prompts"

    def test_work_and_act_pr_both_take_the_plan(self):
        for name in ("work.md", "act-pr.md"):
            with self.subTest(name=name):
                self.assertIn("{plan}", (self.PROMPTS / name).read_text(
                    encoding="utf-8"))

    def test_act_pr_resolves_against_the_recorded_base_not_main(self):
        text = (self.PROMPTS / "act-pr.md").read_text(encoding="utf-8")
        self.assertIn("git merge {remote}/{base}", text)
        self.assertIn("Merge only `{base}`", text)

    def test_act_pr_fetches_from_the_remote_the_plan_resolved(self):
        """`BOARD_GIT_REMOTE` picks the remote plans resolve bases through,
        so the run merges from that one rather than a literal `origin`."""
        text = (self.PROMPTS / "act-pr.md").read_text(encoding="utf-8")
        self.assertNotIn("git fetch origin", text)
        self.assertIn("git fetch {remote} {base}", text)


if __name__ == "__main__":
    unittest.main()
