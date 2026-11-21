"""↻ act on PR on a phase card in Review (task 174).

A phase whose members are all merged into its integration branch, whose
member PRs are merged, and whose own integration PR is open and was asked
for changes. Before this card the only ways forward were a corrective card
or a hand edit: `agents.start_pr_fix` refused every phase card, delivery
offered "Move back to in-progress", and `coordinate` said
`repair-unavailable`. Now one resolver (`agents.repair_target`) names the
target — the existing `phase/<stem>` branch, its Bench-managed worktree and
the open integration PR — and refuses, with nothing created, every case
where repairing it would be unsafe.

Everything here is real: a git repository with a bare origin, the phase
branch and its worktree, the members merged into it, the adapter's own
process. The hosting provider is a stub that answers `gh pr view` from a
JSON file the test writes.

    python3 -m unittest tests.test_phase_repair -v
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import changes  # noqa: E402
import config  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import operations  # noqa: E402
import state  # noqa: E402

from tests.test_phase_runs import (ONE, PHASE, PHASE_BRANCH, TWO,  # noqa: E402
                                   PhaseCase, card, git, wait_for)

PHASE_PR = "https://github.com/acme/widget/pull/40"
ONE_PR = "https://github.com/acme/widget/pull/31"
TWO_PR = "https://github.com/acme/widget/pull/32"

# The provider stub: `gh pr view <n> --json …` answers from $GH_PRS, keyed
# by number; everything else is logged and succeeds quietly.
STUB_GH = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
prs = json.load(open(os.environ["GH_PRS"])) if os.path.exists(os.environ["GH_PRS"]) else {}
if args[:2] == ["pr", "view"]:
    number = args[2] if len(args) > 2 else ""
    found = prs.get(number)
    if found is None:
        sys.stderr.write("no pull requests found\\n")
        sys.exit(1)
    if "--jq" in args:
        print(found.get("url", ""))
    else:
        print(json.dumps(found))
elif args[:2] == ["pr", "list"]:
    print("[]")
else:
    sys.exit(0)
'''

# An agent that addresses the review: one commit on whatever it was given,
# then a push of that branch, with what it saw written down for the test.
REPAIRS = '''#!/usr/bin/env python3
import json, os, subprocess, sys, time
cwd = os.environ["AGENT_CWD"]
branch = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                        capture_output=True, text=True).stdout.strip()
seen = os.path.join(os.path.dirname(cwd), "seen.jsonl")
with open(seen, "a") as fh:
    fh.write(json.dumps({"mode": os.environ.get("AGENT_MODE"), "cwd": cwd,
                         "branch": branch, "task": os.environ.get("BOARD_TASK"),
                         "integration": "integration PR" in os.environ.get("AGENT_PROMPT", ""),
                         "feedback": "the totals are wrong" in os.environ.get("AGENT_PROMPT", "")}) + "\\n")
go = os.path.join(os.path.dirname(cwd), "go")
if os.environ.get("REPAIR_WAIT") or os.path.exists(go + ".wait"):
    for _ in range(1200):
        if os.path.exists(go):
            break
        if os.path.exists(go + ".cancel"):
            # a vendor that stops when its run is held
            sys.exit(143)
        time.sleep(0.05)
open(os.path.join(cwd, "fix.txt"), "w").write("the repair\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "address the integration review"], check=True)
pushed = subprocess.run(["git", "-C", cwd, "push", "-q", "origin", "HEAD:" + branch],
                        capture_output=True, text=True)
if pushed.returncode != 0:
    print("could not push:", pushed.stderr.strip())
    sys.exit(1)
print("ADDRESSED: fixed the integration review")
print("- the totals are wrong: fixed in fix.txt")
'''

REVIEW_SECTION = ("\n## PR review at {head}\n\nPR REVIEW: REQUEST CHANGES\n\n"
                  "- the totals are wrong when both members' work is in\n")


class PhaseRepairCase(PhaseCase):
    """A phase in review/: two members merged into `phase/40-…` (their own
    PRs merged), the phase branch published and its integration PR open."""

    REMOTE = True

    def setUp(self):
        super().setUp()
        state_dir = self.tmp / "state"
        self.patch(STATE=state_dir, GATES_DIR=state_dir / "gates",
                   EVIDENCE_DIR=state_dir / "evidence",
                   VERIFY_DIR=state_dir / "verify",
                   INPUTS_DIR=state_dir / "inputs",
                   ARTIFACTS_DIR=state_dir / "artifacts",
                   RESULTS_DIR=state_dir / "results")
        gh = self.tmp / "gh-stub"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.prs_file = self.tmp / "prs.json"
        os.environ["GH_PRS"] = str(self.prs_file)
        self.addCleanup(os.environ.pop, "GH_PRS", None)
        os.environ.pop("REPAIR_WAIT", None)
        self.addCleanup(os.environ.pop, "REPAIR_WAIT", None)
        self.patch(GH_BIN=str(gh))
        self.adapter_is(REPAIRS)
        self.build()

    # — the world —

    def write_cards(self, **_) -> None:
        pass                               # build() writes them, in review/

    def worktree(self) -> Path:
        return config.WORKTREES / PHASE[:-3]

    def build(self) -> None:
        repo = self.repo
        git(repo, "branch", PHASE_BRANCH, "main")
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        assert git(repo, "worktree", "add", str(self.worktree()),
                   PHASE_BRANCH).returncode == 0
        for member, name in ((ONE, "31.txt"), (TWO, "32.txt")):
            branch = f"task/{member[:-3]}"
            scratch = self.tmp / f"scratch-{member[:2]}"
            git(repo, "worktree", "add", "-q", "-b", branch, str(scratch), "main")
            (scratch / name).write_text(f"{member}\n", encoding="utf-8")
            git(scratch, "add", "-A")
            git(scratch, "-c", "user.email=a@b", "-c", "user.name=m",
                "commit", "-q", "-m", f"member {member[:2]}")
            git(repo, "worktree", "remove", str(scratch))
            merged = git(self.worktree(), "-c", "user.email=a@b", "-c",
                         "user.name=m", "merge", "--no-ff", "-q", "-m",
                         f"merge {branch}", branch)
            assert merged.returncode == 0, merged.stderr
            git(repo, "push", "-q", "origin", branch)
        git(repo, "push", "-q", "origin", PHASE_BRANCH)
        self.integrated = self.tip(PHASE_BRANCH)
        self.write(ONE, self.with_line(card("31 — Stand up site/", status="Review"),
                                       ONE_PR), "review")
        self.write(TWO, self.with_line(card("32 — Serve it", status="Review"),
                                       TWO_PR), "review")
        phase = self.with_line(card("40 — Ship the site", status="Review",
                                    kind="Phase",
                                    cards="- 31 — Stand up site/\n- 32 — Serve it\n"),
                               PHASE_PR)
        self.write(PHASE, phase + REVIEW_SECTION.format(head=self.integrated[:12]),
                   "review")
        self.prs(phase_state="OPEN")

    @staticmethod
    def with_line(text: str, url: str) -> str:
        return text.replace("**Priority:** High\n",
                            f"**Priority:** High\n**PR:** {url}\n")

    def prs(self, *, phase_state="OPEN", head_ref=PHASE_BRANCH,
            head_oid=None, base="main") -> None:
        member = {"state": "MERGED", "baseRefName": PHASE_BRANCH,
                  "mergedAt": "2026-11-20T10:00:00Z"}
        self.prs_file.write_text(json.dumps({
            "40": {"number": 40, "url": PHASE_PR, "state": phase_state,
                   "headRefName": head_ref,
                   "headRefOid": head_oid or self.tip(PHASE_BRANCH),
                   "baseRefName": base,
                   "mergedAt": ("2026-11-21T00:00:00Z"
                                if phase_state == "MERGED" else None)},
            "31": {**member, "number": 31, "url": ONE_PR,
                   "headRefName": f"task/{ONE[:-3]}",
                   "headRefOid": self.tip(f"task/{ONE[:-3]}")},
            "32": {**member, "number": 32, "url": TWO_PR,
                   "headRefName": f"task/{TWO[:-3]}",
                   "headRefOid": self.tip(f"task/{TWO[:-3]}")},
        }), encoding="utf-8")

    def origin_tip(self, branch: str) -> str:
        return git(self.origin, "rev-parse", branch).stdout.strip()

    def seen(self) -> list[dict]:
        path = config.WORKTREES / "seen.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines()]

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines()]

    def refused(self, code: str) -> ValueError:
        before = (self.tip(PHASE_BRANCH), self.origin_tip(PHASE_BRANCH))
        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(PHASE, "review")
        self.assertEqual(code, getattr(caught.exception, "code", None),
                         str(caught.exception))
        self.assertEqual(before, (self.tip(PHASE_BRANCH),
                                  self.origin_tip(PHASE_BRANCH)),
                         "a refusal moves no branch, here or on the remote")
        self.assertEqual([], self.seen(), "no agent was launched")
        self.assertEqual([], [r for r in state.AGENTS.values()
                              if r.get("status") == "running"
                              and str(r.get("id", "")).startswith("fix-pr-")])
        self.assertFalse(self.branch_exists(f"task/{PHASE[:-3]}"))
        return caught.exception


# ── the supported repair ─────────────────────────────────────────────

class APhaseRepairsItsIntegrationPR(PhaseRepairCase):
    def test_one_confirmed_act_on_pr_repairs_the_same_branch_and_pr(self):
        member_tips = (self.tip(f"task/{ONE[:-3]}"), self.tip(f"task/{TWO[:-3]}"))
        agent = agents.start_pr_fix(PHASE, "review")
        self.settle()

        self.assertEqual(PHASE_BRANCH, agent["branch"])
        self.assertEqual(str(self.worktree()), agent["worktree"])
        self.assertEqual({"kind": "phase", "branch": PHASE_BRANCH, "pr": PHASE_PR},
                         {k: agent["repair"][k] for k in ("kind", "branch", "pr")})
        [run] = self.seen()
        self.assertEqual(("act-pr", PHASE_BRANCH, PHASE), (run["mode"], run["branch"],
                                                           run["task"]))
        self.assertTrue(run["integration"], "the run is told it repairs the "
                        "integration PR")
        self.assertTrue(run["feedback"], "the integration review reaches the run")

        # the same integration PR updated: the phase branch moved, on the
        # remote too, building on what was integrated
        new = self.tip(PHASE_BRANCH)
        self.assertNotEqual(self.integrated, new)
        self.assertEqual(new, self.origin_tip(PHASE_BRANCH))
        self.assertEqual(0, git(self.repo, "merge-base", "--is-ancestor",
                                self.integrated, new).returncode)
        # nothing else was created or reopened
        self.assertFalse(self.branch_exists(f"task/{PHASE[:-3]}"))
        self.assertEqual(member_tips, (self.tip(f"task/{ONE[:-3]}"),
                                       self.tip(f"task/{TWO[:-3]}")))
        self.assertFalse([c for c in self.gh_calls() if c[:2] == ["pr", "create"]],
                         "no replacement PR")
        # Review is kept, for the phase and its members, and the card says
        # what the run did
        self.assertEqual(("review", "review", "review"),
                         (self.stage_of(PHASE), self.stage_of(ONE),
                          self.stage_of(TWO)))
        self.assertIn("## PR update", self.text(PHASE))
        self.assertIn("- 31 — Stand up site/\n- 32 — Serve it", self.text(PHASE))
        tasks = sorted(p.name for p in (self.tasks / "backlog").iterdir())
        self.assertEqual([], tasks, "no corrective card was written")

    def test_the_old_approval_does_not_speak_for_the_repaired_head(self):
        gates.record_verdict(PHASE, verdict="APPROVE", head=self.integrated,
                             agent="review-x", actor="agent")
        agents.start_pr_fix(PHASE, "review")
        self.settle()
        self.assertIsNotNone(gates.verdict_at(PHASE, self.integrated))
        self.assertIsNone(gates.verdict_at(PHASE, self.tip(PHASE_BRANCH)),
                          "a fresh review is needed for the updated head")

    def test_a_cleaned_up_worktree_is_recreated_from_the_phase_branch(self):
        git(self.repo, "worktree", "remove", str(self.worktree()))
        target = agents.repair_target(PHASE, "review")
        self.assertIsNone(target["problem"], target["problem"])
        self.assertTrue(target["recreate"])
        agents.start_pr_fix(PHASE, "review")
        self.settle()
        self.assertEqual(PHASE_BRANCH, self.seen()[0]["branch"])
        self.assertEqual(self.tip(PHASE_BRANCH), self.origin_tip(PHASE_BRANCH))

    def test_the_launch_check_reads_the_same_target(self):
        self.assertEqual([], agents.launch_refusals(PHASE, "review", "act-pr"))
        (self.worktree() / "stray.txt").write_text("x\n", encoding="utf-8")
        found = agents.launch_refusals(PHASE, "review", "act-pr")
        self.assertTrue(any("uncommitted" in f for f in found), found)

    def test_a_failed_publication_keeps_the_commit_and_resets_nothing(self):
        """The push is the run's; when the remote refuses it the run fails,
        the card stays in review, and the local commit is kept as it is —
        nothing is reset or forced."""
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        agents.start_pr_fix(PHASE, "review")
        self.settle()
        self.assertNotEqual(self.integrated, self.tip(PHASE_BRANCH),
                            "the repair commit is kept")
        git(self.repo, "remote", "set-url", "origin", str(self.origin))
        self.assertEqual(self.integrated, self.origin_tip(PHASE_BRANCH))
        self.assertEqual("review", self.stage_of(PHASE))
        failed = [r for r in state.AGENTS.values() if r.get("failure")]
        self.assertTrue(failed, "the card wears the failed run")


# ── refused, with nothing created ────────────────────────────────────

class UnsafeTargetsAreRefused(PhaseRepairCase):
    def test_a_merged_integration_pr(self):
        self.prs(phase_state="MERGED")
        self.assertIn("merged", str(self.refused("pr-merged")))

    def test_a_closed_integration_pr(self):
        self.prs(phase_state="CLOSED")
        self.refused("pr-closed")

    def test_a_pr_that_proposes_another_branch(self):
        self.prs(head_ref="task/40-ship-the-site")
        self.assertIn(PHASE_BRANCH, str(self.refused("head-drift")))

    def test_a_pr_head_this_checkout_does_not_hold(self):
        other = self.tmp / "teammate"
        git(self.tmp, "clone", "-q", "-b", PHASE_BRANCH, str(self.origin), str(other))
        (other / "theirs.txt").write_text("pushed elsewhere\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "x")
        git(other, "push", "-q", "origin", PHASE_BRANCH)
        theirs = git(other, "rev-parse", "HEAD").stdout.strip()
        git(self.repo, "fetch", "-q", "origin")
        self.prs(head_oid=theirs)
        self.assertIn("fast-forward", str(self.refused("head-drift")))

    def test_a_pr_into_another_base(self):
        self.prs(base="develop")
        self.refused("base-drift")

    def test_a_dirty_phase_worktree(self):
        (self.worktree() / "stray.txt").write_text("x\n", encoding="utf-8")
        self.refused("dirty")
        self.assertTrue((self.worktree() / "stray.txt").exists(),
                        "nothing in another run's checkout is touched")

    def test_a_worktree_on_another_branch_is_not_switched(self):
        git(self.repo, "worktree", "remove", str(self.worktree()))
        git(self.repo, "worktree", "add", "-q", "-b", "elsewhere",
            str(self.worktree()), "main")
        self.refused("wrong-branch")
        self.assertEqual("elsewhere", git(self.worktree(), "branch",
                                          "--show-current").stdout.strip())

    def test_a_worktree_mid_merge(self):
        gitdir = git(self.worktree(), "rev-parse", "--absolute-git-dir").stdout.strip()
        Path(gitdir, "MERGE_HEAD").write_text(self.integrated + "\n")
        self.refused("conflicting")

    def test_a_foreign_checkout_where_the_worktree_goes(self):
        git(self.repo, "worktree", "remove", str(self.worktree()))
        self.worktree().mkdir()
        (self.worktree() / "notes.txt").write_text("mine\n", encoding="utf-8")
        self.refused("foreign-checkout")
        self.assertEqual("mine\n", (self.worktree() / "notes.txt").read_text())

    def test_a_missing_phase_branch(self):
        git(self.repo, "worktree", "remove", str(self.worktree()))
        git(self.repo, "branch", "-D", PHASE_BRANCH)
        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(PHASE, "review")
        self.assertEqual("no-branch", caught.exception.code)

    def test_a_member_still_working(self):
        state.AGENTS["m-1"] = {"id": "m-1", "task": ONE, "status": "running",
                               "name": "Wren", "proc": None}
        self.assertIn("Wren", str(self.refused("busy")))

    def test_a_pending_or_unknown_operation(self):
        op = operations.begin(operations.key_for("integrate", TWO, "x"),
                              action="integrate", card=TWO, number="32",
                              scope="phase:40", pool="none", basis="x",
                              attempt=1)
        self.refused("operation-pending")
        operations.update(op["key"], "unknown")
        self.refused("operation-unknown")

    def test_its_own_delegated_operation_is_not_a_competitor(self):
        op = operations.begin(operations.key_for("repair", PHASE, "h"),
                              action="repair", card=PHASE, number="40",
                              scope="phase:40", pool="implementation",
                              basis="h", attempt=1)
        target = agents.repair_target(PHASE, "review", operation=op["key"])
        self.assertIsNone(target["problem"], target["problem"])

    def test_concurrent_clicks_launch_one_agent(self):
        os.environ["REPAIR_WAIT"] = "1"
        results: list = []

        def click():
            try:
                results.append(agents.start_pr_fix(PHASE, "review"))
            except ValueError as exc:
                results.append(exc)

        threads = [threading.Thread(target=click) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        launched = [r for r in results if isinstance(r, dict)]
        self.assertEqual(1, len(launched), results)
        self.assertEqual(1, len([r for r in state.AGENTS.values()
                                 if r.get("status") == "running"]))
        (config.WORKTREES / "go").write_text("")
        self.settle()
        self.assertEqual(1, len(self.seen()))


class WhenTheHostOrALaterRunIsInTheWay(PhaseRepairCase):
    def test_an_unavailable_host_repairs_the_local_branch_and_says_so(self):
        """`gh` cannot answer: the target is the phase's own branch here, as
        it is for an ordinary card, and the unchecked head and base are said
        in its notes rather than assumed."""
        self.prs_file.write_text(json.dumps({}), encoding="utf-8")
        target = agents.repair_target(PHASE, "review")
        self.assertIsNone(target["problem"], target["problem"])
        self.assertEqual(PHASE_BRANCH, target["branch"])
        self.assertTrue(any("could not be read" in n for n in target["notes"]),
                        target["notes"])

    def test_an_unavailable_host_and_a_moved_pr_branch_publishes_nothing(self):
        """What keeps that safe: the remote's branch moved on without this
        checkout, so the repair's plain push is refused and nothing is
        forced over the teammate's commit."""
        other = self.tmp / "teammate"
        git(self.tmp, "clone", "-q", "-b", PHASE_BRANCH, str(self.origin), str(other))
        (other / "theirs.txt").write_text("pushed elsewhere\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "x")
        git(other, "push", "-q", "origin", PHASE_BRANCH)
        theirs = git(other, "rev-parse", "HEAD").stdout.strip()
        self.prs_file.write_text(json.dumps({}), encoding="utf-8")
        agents.start_pr_fix(PHASE, "review")
        self.settle()
        self.assertEqual(theirs, self.origin_tip(PHASE_BRANCH),
                         "the teammate's commit is still the PR's head")
        self.assertEqual("review", self.stage_of(PHASE))

    def test_a_held_repair_can_be_asked_again(self):
        """Cancellation: ‖ hold stops the run, the branch and worktree are
        left as they are, and a second deliberate click starts one new run."""
        (config.WORKTREES / "go.wait").write_text("")
        first = agents.start_pr_fix(PHASE, "review")
        wait_for(lambda: self.seen(), timeout=20)
        agents.stop_agent(first["id"])
        # ‖ hold terminates the adapter's process; the stub vendor stops
        # with it here (a real CLI is the adapter's child to stop)
        (config.WORKTREES / "go.cancel").write_text("")
        wait_for(lambda: state.AGENTS[first["id"]].get("status") != "running",
                 timeout=20)
        self.settle()
        self.assertEqual(self.integrated, self.tip(PHASE_BRANCH),
                         "a held repair moved nothing")
        self.assertEqual(self.integrated, self.origin_tip(PHASE_BRANCH))
        self.assertTrue(self.worktree().exists(), "the worktree is kept")
        (config.WORKTREES / "go.wait").unlink()
        time.sleep(1.1)                    # a run's id carries its second
        second = agents.start_pr_fix(PHASE, "review")
        self.settle()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(2, len(self.seen()))
        self.assertEqual(self.tip(PHASE_BRANCH), self.origin_tip(PHASE_BRANCH))

    def test_an_unobserved_repair_from_before_a_restart_blocks_a_second(self):
        """Restart: a delegated repair the previous board launched and never
        saw end is `unobserved` — it keeps its slot, and a click does not
        start a competing run on the same branch."""
        op = operations.begin(operations.key_for("repair", PHASE, "h"),
                              action="repair", card=PHASE, number="40",
                              scope="phase:40", pool="implementation",
                              basis="h", attempt=1)
        operations.update(op["key"], "launched")
        operations.update(op["key"], "unobserved")
        self.assertIn("unobserved", str(self.refused("operation-pending")))


class OrdinaryCardsAreUnchanged(PhaseRepairCase):
    def test_a_merged_member_pr_is_still_refused(self):
        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(ONE, "review")
        self.assertIn("is merged", str(caught.exception))
        self.assertEqual("pr-merged", caught.exception.code)

    def test_an_ordinary_target_is_its_task_branch(self):
        self.prs()
        found = json.loads(self.prs_file.read_text())
        found["31"].update(state="OPEN", mergedAt=None)
        self.prs_file.write_text(json.dumps(found))
        target = agents.repair_target(ONE, "review")
        self.assertEqual(("task", f"task/{ONE[:-3]}", None),
                         (target["kind"], target["branch"], target["problem"]))


# ── what the card offers ─────────────────────────────────────────────

class TheDeliveryStatusOffersTheRepair(PhaseRepairCase):
    def test_changes_requested_offers_act_on_pr_naming_the_target(self):
        gates.record_verdict(PHASE, verdict="REQUEST CHANGES",
                             head=self.integrated, agent="review-x",
                             actor="agent")
        found = delivery.for_card(PHASE, "review")
        self.assertEqual("changes-requested", found["status"]["word"])
        primary = found["primary"]
        self.assertEqual(("act-pr", True, PHASE_BRANCH),
                         (primary["action"], primary["enabled"],
                          primary.get("target")))
        everything = [primary, *(found.get("others") or [])]
        self.assertFalse([a for a in everything if a["action"] == "move"],
                         "no move-back on a phase card")
        self.assertEqual([], self.seen(), "reading starts nothing")

    def test_a_merged_integration_pr_says_why_it_cannot_repair(self):
        gates.record_verdict(PHASE, verdict="REQUEST CHANGES",
                             head=self.integrated, agent="review-x",
                             actor="agent")
        changes.PR_STATE[PHASE] = {"prState": "CLOSED", "merged": False,
                                   "ts": time.time()}
        found = delivery.for_card(PHASE, "review")
        primary = found["primary"]
        self.assertEqual(("act-pr", False), (primary["action"], primary["enabled"]))
        self.assertIn("closed", primary["why"])


# ── a scoped phase selects the same repair ───────────────────────────

from tests.test_phase_scopes import ScopedPhaseCase, phase_agreement  # noqa: E402

import coordinate  # noqa: E402
import phasescope  # noqa: E402


class AScopedPhaseRepairsThroughTheSameTarget(ScopedPhaseCase):
    """The phase scope carries the phase to review/; its integration review
    asks for changes; the next pass selects ↻ act on PR through the same
    `repair_target` a person's click rides, within the repair budget."""

    PHASE_STAGE = "to-do"

    def to_review(self, **authority) -> str:
        self.orchestrate(phase_agreement(authority={"review": False,
                                                    "merge": False,
                                                    **authority}))
        phasescope.adopt(PHASE, "tester")
        for _ in range(12):
            self.tick()
            if self.stage_of(PHASE) == "review":
                break
        self.assertEqual("review", self.stage_of(PHASE))
        if "**PR:**" not in self.text(PHASE):
            path = self.tasks / "review" / PHASE
            path.write_text(path.read_text(encoding="utf-8").replace(
                "**Priority:** High\n", f"**Priority:** High\n**PR:** {PHASE_PR}\n"),
                encoding="utf-8")
        head = self.tip(PHASE_BRANCH)
        gates.record_verdict(PHASE, verdict="REQUEST CHANGES", head=head,
                             agent="review-x", actor="agent")
        return head

    def item(self) -> dict:
        return next(i for i in coordinate.plan()["items"] if i["card"] == PHASE)

    def test_the_delegated_repair_runs_and_the_new_head_is_asked_afresh(self):
        head = self.to_review()
        found = self.item()
        self.assertEqual("repair", found["code"], found.get("reason"))
        self.assertEqual(PHASE_BRANCH, found["repairTarget"]["branch"])

        self.tick()
        [repair] = self.ops("repair")
        self.assertEqual(PHASE, repair["file"])
        self.assertIn(repair["status"], ("launched", "ended"))
        new = self.tip(PHASE_BRANCH)
        self.assertNotEqual(head, new, "the repair committed on the phase branch")
        self.assertEqual(("review", "review", "review"),
                         (self.stage_of(PHASE), self.stage_of(ONE),
                          self.stage_of(TWO)))
        after = self.item()
        self.assertEqual(new, after["head"])
        self.assertNotIn(after["code"], ("repair", "repair-unavailable"),
                         "the old request for changes is history at the new head")
        self.assertFalse(self.branch_exists(f"task/{PHASE[:-3]}"))

    def test_a_merged_or_closed_integration_pr_is_repair_unavailable(self):
        self.to_review()
        changes.PR_STATE[PHASE] = {"prState": "CLOSED", "merged": False,
                                   "ts": time.time()}
        found = self.item()
        self.assertEqual("repair-unavailable", found["code"])
        self.assertIn("closed", found["reason"])
        self.tick()
        self.assertEqual([], self.ops("repair"))

    def test_a_member_still_working_is_a_wait_not_a_refusal_each_pass(self):
        """The plan reads what the launch would refuse for: while a member
        has a run, the phase waits, and no repair operation is opened, and
        none is refused, on pass after pass."""
        self.to_review()
        state.AGENTS["m-1"] = {"id": "m-1", "task": ONE, "status": "running",
                               "name": "Wren", "proc": None}
        self.addCleanup(state.AGENTS.pop, "m-1", None)
        found = self.item()
        self.assertEqual(("repair-waiting", "waiting"),
                         (found["code"], found["state"]))
        self.assertIn("Wren", found["reason"])
        state.AGENTS.pop("m-1")

        operations.begin(operations.key_for("integrate", TWO, "x"),
                         action="integrate", card=TWO, number="32",
                         scope="phase:40", pool="none", basis="x", attempt=1)
        [op] = [o for o in self.ops("integrate") if o["file"] == TWO
                and o["basis"] == "x"]
        operations.update(op["key"], "unknown")
        before = self.text(PHASE).count("<!-- bench:orchestration")
        for _ in range(3):
            self.tick()
        self.assertEqual("repair-held", self.item()["code"])
        self.assertEqual([], self.ops("repair"), "no repair was opened")
        after = self.text(PHASE).count("<!-- bench:orchestration")
        self.assertLessEqual(after - before, 1,
                             "the card is not written to on every pass")

    def test_repair_not_delegated_is_the_persons(self):
        self.to_review(repair=False)
        found = self.item()
        self.assertEqual("repair-not-delegated", found["code"])
        self.tick()
        self.assertEqual([], self.ops("repair"))


if __name__ == "__main__":
    unittest.main()
