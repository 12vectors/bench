"""Integration and acceptance are gated on current evidence (task 71).

The claim under test is narrow and load-bearing: on a board that declares
a gate, **a merge happens only when bench's own recorded evidence says
`passed` for every required check, against the exact commit about to move,
and when the review policy the operator chose is satisfied by a record
that genuinely says what it is read as saying.**

So nothing here mocks the gate. Every case writes a real profile document,
a real check executable, a real git checkout with real branches, asks for a
real verification through `verify.request`, and then asks the same
`gates.evaluate` the phase runner and the merge path ask. The "agents" are
stub scripts: no token is spent, no live application, service or
credential is touched, and every root in every case is a disposable
temporary directory. Nothing in this file reaches GitHub — the formal
review state is fed in as the review objects GitHub's API returns, which
is exactly what `github.py` feeds it in production.

Seven claims have a class each:

- **The policy is read once, where every other policy value is read.** A
  gate naming a check nothing declares, a scope that is not a scope and a
  review policy bench has no word for are refused in `profiles`, with the
  code each came from; a board with no gate block is *ungated*, in that
  word, and keeps its old behaviour exactly.
- **Evidence satisfies a gate only while it is about the commit.** A pass
  against another commit, a pass with uncommitted work under it, a stale
  pass, a failure, a denial, a preparation failure, a cancellation and a
  check nobody asked for are eight different unmet readings with eight
  different remedies — and an agent exiting zero with commits on the
  branch is none of them.
- **The backend refuses, not the page.** `require()` raises the sentence
  the sheet shows, the phase runner holds a member out of `ready`, and
  merge-and-clean-up refuses before it parks a drive and again immediately
  before the merge.
- **A phase's integration head is its own question.** Every member's
  evidence passing does not satisfy the integration gate, because none of
  it ran against the commit the members were merged into.
- **A Bench verdict and GitHub's formal review state are two records.** A
  COMMENTED review is never read as either, the same-account limitation is
  surfaced as the configuration fact it is, and a policy wanting a
  separate reviewer identity reads unmet rather than switching accounts.
- **An override is explicit, bound and never a pass.** Actor, reason,
  head and the exact unmet gates are recorded; a repeat click, a restart
  and a moved head cannot duplicate or widen it.
- **Nothing is thrown away by a refusal.** A blocked merge leaves the
  card, its branch, its worktree and every record exactly as they were.

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
import gates  # noqa: E402
import github  # noqa: E402
import httpd  # noqa: E402
import phases  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

from tests.test_phase_runs import (ONE, PHASE, PHASE_BRANCH, TWO,  # noqa: E402
                                   PhaseCase)

CARD = "71-gate-integration.md"
STEM = CARD[:-3]

PASSES = """#!{python}
print("ran 3 tests in 0.1s — OK")
"""

FAILS = """#!{python}
import sys
print("1 test failed: the thing does not do the thing")
sys.exit(1)
"""

HANGS = """#!{python}
import time
print("working", flush=True)
time.sleep(600)
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def card(title: str, *, status: str = "In Progress", body: str = "",
         **headers) -> str:
    lines = [f"# {title}", "", f"**Status:** {status}", "**Priority:** High"]
    for key, value in headers.items():
        lines.append(f"**{key.replace('_', ' ').capitalize()}:** {value}")
    lines += ["", body or "What this card is for, at enough length to read "
                          "as a brief."]
    return "\n".join(lines) + "\n"


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


class GateCase(unittest.TestCase):
    """A disposable tool install, work root and application checkout, with
    config pointed at all of it. Nothing outside the temporary directory is
    read or written by any case in this file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.repo = self.tmp / "app"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main's line\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# the brief\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

        self.tool, self.work = self.tmp / "tool", self.tmp / "work"
        self.tasks = self.work / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        (self.work / "reference").mkdir(parents=True, exist_ok=True)

        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        (self.local / "commands").mkdir(parents=True)
        self.state = self.local / "state"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.tool,
                   LOCAL=self.local, CORE=config.CORE,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.state / "plans",
                   MANIFESTS_DIR=self.state / "manifests",
                   CONTEXT_DIR=self.state / "context",
                   EVIDENCE_DIR=self.state / "evidence",
                   VERIFY_DIR=self.state / "verify",
                   REVIEWS_DIR=self.state / "reviews",
                   GATES_DIR=self.state / "gates",
                   AGENT_DIR=self.state / "agent",
                   SESSIONS_DIR=self.state / "sessions",
                   AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
                   APP_BASE="main", GIT_REMOTE="", APP_SETTING=str(self.repo),
                   WORK_SETTING=str(self.work), EXTERNAL=True,
                   COMMIT_MOVES=False, SYNC=False, FETCH_TIMEOUT=10.0,
                   VERIFY_TIMEOUT=60.0, PREPARE_TIMEOUT=60.0,
                   VERIFY_PARALLEL=2, VERIFY_LOG_CAP=4 * 1024 * 1024)

        for registry in (state.AGENTS, state.BOARD_EVENTS,
                         state.EXPECTED_MOVES, verify.RUNNING, verify._KEYS,
                         github.PR_STATE, gates.REVIEW_STATE,
                         phases.SNAPSHOTS):
            registry.clear()
            self.addCleanup(registry.clear)
        verify.forget_cached()
        gates.forget_cached()
        self.addCleanup(verify.forget_cached)
        self.addCleanup(gates.forget_cached)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        (self.tasks / "in-progress" / CARD).write_text(
            card("71 — Gate integration"), encoding="utf-8")

    # — the furniture —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def declares(self, document: dict | str) -> Path:
        path = self.local / "profiles.json"
        path.write_text(document if isinstance(document, str)
                        else json.dumps(document), encoding="utf-8")
        gates.forget_cached()
        return path

    def executable(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(body.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def gated(self, *, scope: str = "acceptance", check: str = "unit",
              body: str = PASSES, review: str = "none",
              overrides: dict | None = None, root: str = "worktree",
              intents=("work", "review")) -> dict:
        """A document with one declared check and one gate requiring it."""
        gate: dict = {"checks": [check]} if check else {}
        if review != "none":
            gate["review"] = review
        block: dict = {scope: gate}
        if overrides is not None:
            block["overrides"] = overrides
        document = {
            "v": 1,
            "checks": [{"name": check, "run": str(self.executable(check, body)),
                        "root": root}] if check else [],
            "profiles": {intent: {"verify": [check] if check else []}
                         for intent in intents},
            "gates": block,
        }
        self.declares(document)
        return document

    def worktree(self, stem: str = STEM, *, base: str = "main",
                 branch: str | None = None) -> Path:
        path = config.WORKTREES / stem
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            git(self.repo, "worktree", "add", "-b", branch or f"task/{stem}",
                str(path), base)
        return path

    def commit_in(self, path: Path, text: str = "the run's work\n") -> str:
        (path / "code.txt").write_text(text, encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "work")
        return git(path, "rev-parse", "HEAD").stdout.strip()

    def manifest(self, intent: str = "work", *, filename: str = CARD,
                 stage: str = "in-progress", agent: str = "run-1",
                 worktree: Path | None = None, review: Path | None = None):
        found = profiles.require(profiles.manifest(
            intent, filename, stage, worktree=worktree, review=review,
            pin_inputs=True))
        return profiles.record(agent, found)

    def ask(self, check: str = "unit", *, agent: str = "run-1",
            filename: str = CARD, stage: str = "in-progress",
            scope: str | None = None, intent: str | None = None) -> dict:
        return verify.request(check=check, scope=scope, agent=agent,
                              task=None if agent else filename,
                              intent=intent,
                              stage=None if agent else stage,
                              actor="agent" if agent else "you")

    def decide(self, scope: str = "acceptance", *, filename: str = CARD):
        return gates.evaluate(filename, scope)

    def stage_of(self, filename: str = CARD) -> str | None:
        return taskfiles.find_stage_of(filename)

    def reviews_say(self, entries, *, filename: str = CARD,
                    author: str = "istos", head: str | None = None) -> dict:
        """GitHub's own review objects, in the REST shape the poll reads —
        each with the commit it was given for, which is the field that
        makes a formal approval bindable at all."""
        return gates.observe_reviews(
            filename, reviews=entries, author=author, head=head)


# ── the policy is read once, where every other policy value is ─────────


class ThePolicyIsOneReader(GateCase):
    """`profiles.gate_policy` is the only thing that parses a gate block,
    and a gate that cannot be satisfied is refused there rather than
    blocking every merge later."""

    def test_a_board_with_no_document_is_ungated_in_that_word(self):
        found = gates.evaluate(CARD, "acceptance")
        self.assertEqual(found["decision"], "ungated")
        self.assertFalse(found["gated"])
        self.assertIn("nothing is required", found["summary"])
        self.assertIn("nothing is proved", found["summary"])
        self.assertTrue(gates.passable(found),
                        "a board that gates nothing behaves as it always did")

    def test_a_document_with_no_gates_block_is_ungated(self):
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("unit", PASSES))}],
            "profiles": {"work": {"verify": ["unit"]}}})
        for scope in profiles.GATE_SCOPES:
            with self.subTest(scope=scope):
                self.assertEqual(gates.evaluate(CARD, scope)["decision"],
                                 "ungated")

    def test_a_gate_requiring_an_undeclared_check_is_refused_by_code(self):
        self.declares({"v": 1, "gates": {"acceptance": {"checks": ["unit"]}}})
        policy = profiles.gate_policy()
        self.assertEqual([d["code"] for d in policy["diagnostics"]],
                         ["gate-check-undeclared"])
        self.assertIn("declares nowhere", policy["blockers"][0])
        self.assertIn("top-level", policy["diagnostics"][0]["remedy"])

    def test_a_scope_that_is_not_a_scope_is_refused_by_name(self):
        self.declares({"v": 1, "gates": {"akceptance": {}}})
        policy = profiles.gate_policy()
        self.assertEqual([d["code"] for d in policy["diagnostics"]],
                         ["gate-scope-unknown"])
        self.assertIn("'akceptance'", policy["blockers"][0])

    def test_a_review_policy_bench_has_no_word_for_is_refused(self):
        self.declares({"v": 1, "gates": {"acceptance": {"review": "lgtm"}}})
        policy = profiles.gate_policy()
        self.assertEqual([d["code"] for d in policy["diagnostics"]],
                         ["gate-review-unknown"])
        self.assertIn("'lgtm'", policy["blockers"][0])

    def test_a_policy_that_does_not_resolve_blocks_rather_than_ungates(self):
        self.declares("{ not json")
        found = gates.evaluate(CARD, "acceptance")
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "policy-refused")
        self.assertFalse(gates.passable(found),
                         "an operator mid-edit is not handed an open gate")

    def test_the_four_review_policies_are_named_and_distinct(self):
        self.assertEqual(set(profiles.REVIEW_POLICIES),
                         {"none", "bench", "github", "separate-reviewer"})
        for word, why in profiles.REVIEW_POLICIES.items():
            with self.subTest(word=word):
                self.assertTrue(why.strip(), "each says what it means")

    def test_verified_outputs_is_a_guarantee_only_where_a_gate_exists(self):
        self.declares({"v": 1, "checks": [
            {"name": "unit", "run": str(self.executable("unit", PASSES))}],
            "profiles": {"work": {"verify": ["unit"]}}})
        self.worktree()
        plain = profiles.resolve("work", worktree=config.WORKTREES / STEM)
        self.assertNotIn("verified-outputs", plain["guarantees"]["configured"])
        self.gated()
        gated = profiles.resolve("work", worktree=config.WORKTREES / STEM)
        self.assertIn("verified-outputs", gated["guarantees"]["configured"])
        self.assertIn("verified-outputs", gated["guarantees"]["enforced"])
        self.assertNotIn("verified-outputs", profiles.PENDING_CAPABILITIES)

    def test_the_gate_has_no_second_parser(self):
        seams = gates.seams()
        self.assertIn("profiles.gate_policy()", seams["evaluates"])
        self.assertIn("parses no policy document itself", seams["evaluates"])
        self.assertIn("reads COMMENTED as an approval", seams["never"])


# ── evidence satisfies a gate only while it is about the commit ────────


class EvidenceIsBoundToTheCommit(GateCase):
    """The one rule, and the ways it is not met. Every case here produces
    real evidence through `verify.request` and then reads the gate."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()

    def test_a_pass_against_the_head_meets_the_gate(self):
        self.gated()
        self.commit_in(self.wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])
        row = found["requirements"][0]
        self.assertEqual(row["state"], "met")
        self.assertEqual(row["commit"], found["head"]["commit"])
        gates.require(found)              # does not raise

    def test_a_check_nothing_asked_for_is_not_run_and_not_a_pass(self):
        self.gated()
        self.commit_in(self.wt)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "not-run")
        self.assertIn("an absent record is not a pass", row["why"])
        self.assertIn("evidence sheet", row["remedy"])

    def test_an_agent_exiting_zero_with_commits_does_not_satisfy_it(self):
        """The whole point, as its own case: all three of the things that
        used to answer this question are present here."""
        self.gated()
        head = self.commit_in(self.wt)
        state.AGENTS["run-1"] = {
            "id": "run-1", "task": CARD, "branch": f"task/{STEM}",
            "worktree": str(self.wt), "base": head, "status": "done",
            "rc": 0, "started": time.time(), "mode": "work",
            "proc": None, "log": str(self.tmp / "log"),
            "origin": "in-progress",
        }
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "not-run")

    def test_a_pass_against_another_commit_does_not_travel(self):
        self.gated()
        self.commit_in(self.wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")
        moved = self.commit_in(self.wt, "a later edit\n")
        verify.forget_cached()
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertIn(found["unmet"][0]["reason"], ("other-commit", "stale"))
        self.assertEqual(found["head"]["commit"], moved)

    def test_a_pass_with_uncommitted_work_under_it_is_not_the_commit(self):
        self.gated()
        self.commit_in(self.wt)
        (self.wt / "scratch.txt").write_text("not committed\n",
                                             encoding="utf-8")
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "source-dirty")
        self.assertIn("Commit the work", row["remedy"])

    def test_a_failing_check_blocks_and_reads_as_a_test_failure(self):
        self.gated(body=FAILS)
        self.commit_in(self.wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "failed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "failed")

    def test_a_check_declared_against_the_application_is_refused(self):
        self.gated(root="app")
        self.commit_in(self.wt)
        policy = profiles.gate_policy()
        # Two codes, and both are true: the check is refused where checks
        # are read, and the gate then requires a name nothing declares.
        self.assertEqual([d["code"] for d in policy["diagnostics"]],
                         ["check-root-unsafe", "gate-check-undeclared"])
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "policy-refused")

    def test_a_check_that_is_running_is_pending_and_decides_nothing(self):
        import threading
        self.gated(body=HANGS)
        self.commit_in(self.wt)
        self.manifest()
        asking = threading.Thread(target=self.ask, daemon=True)
        asking.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        try:
            found = self.decide()
            self.assertEqual(found["decision"], "pending")
            self.assertEqual(found["requirements"][0]["state"], "pending")
            with self.assertRaises(ValueError) as caught:
                gates.require(found, "merging")
            self.assertIn("nothing is decided", str(caught.exception))
        finally:
            for ident in list(verify.RUNNING):
                verify.cancel(ident)
            asking.join(timeout=20)

    def test_a_cancelled_verification_is_unmet_with_its_own_reason(self):
        import threading
        self.gated(body=HANGS)
        self.commit_in(self.wt)
        self.manifest()
        asking = threading.Thread(target=self.ask, daemon=True)
        asking.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        for ident in list(verify.RUNNING):
            verify.cancel(ident)
        asking.join(timeout=20)
        verify.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "cancelled")

    def test_every_unmet_reason_carries_a_remedy_of_its_own(self):
        for reason in gates.REASONS:
            with self.subTest(reason=reason):
                self.assertIn(reason, gates.REMEDIES)
                self.assertTrue(gates.REMEDIES[reason].strip())

    def test_reading_the_gate_runs_nothing_and_writes_nothing(self):
        self.gated()
        self.commit_in(self.wt)
        directory = self.state / "evidence"
        before = sorted(p.name for p in directory.glob("*")) \
            if directory.is_dir() else []
        head = git(self.repo, "rev-parse", f"task/{STEM}").stdout.strip()
        for _ in range(3):
            gates.evaluate(CARD, "acceptance")
        after = sorted(p.name for p in directory.glob("*")) \
            if directory.is_dir() else []
        self.assertEqual(before, after, "reading produced no evidence")
        self.assertEqual(
            head, git(self.repo, "rev-parse", f"task/{STEM}").stdout.strip())


# ── a Bench verdict and GitHub's review state are two records ──────────


class TwoRecordsOfReview(GateCase):
    """The separation this card exists to keep: what bench's own reviewer
    found, and what GitHub actually holds. A COMMENTED review is neither,
    and the account that opened a pull request cannot approve it."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()

    def satisfied_checks(self) -> str:
        """A head with the gate's check already passed against it, so the
        cases below are about the review half and nothing else."""
        self.commit_in(self.wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        return git(self.repo, "rev-parse", f"task/{STEM}").stdout.strip()

    def test_a_bench_verdict_is_recorded_against_the_head_it_was_of(self):
        self.gated(review="bench")
        head = self.satisfied_checks()
        gates.record_verdict(CARD, verdict="APPROVE", head=head,
                             agent="rev-1", actor="agent")
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])
        row = [r for r in found["requirements"] if r["kind"] == "review"][0]
        self.assertEqual(row["state"], "met")
        self.assertEqual(row["reason"], "bench-approved")

    def test_a_bench_verdict_about_another_head_does_not_travel(self):
        self.gated(review="bench")
        self.satisfied_checks()
        gates.record_verdict(CARD, verdict="APPROVE", head="0" * 40,
                             agent="rev-1", actor="agent")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "verdict-other-head")
        self.assertIn("is never retracted", row["remedy"])

    def test_a_request_changes_verdict_blocks_with_its_own_reason(self):
        self.gated(review="bench")
        head = self.satisfied_checks()
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=head,
                             agent="rev-1", actor="agent")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "verdict-changes")

    def test_a_verdict_is_only_one_of_the_two_words(self):
        for attempt in ("looks good", "approve-ish", "", "APPROVED"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(ValueError):
                    gates.record_verdict(CARD, verdict=attempt, head="a" * 40)

    def test_a_commented_review_is_never_read_as_an_approval(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 1, "user": {"login": "istos"},
                           "state": "COMMENTED", "commit_id": head,
                           "body": "PR REVIEW: APPROVE — looks good to me"}],
                         head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "same-account")
        self.assertIn("GitHub does not accept an approval", row["why"])
        self.assertIn("\"review\": \"bench\"", row["remedy"])

    def test_the_same_account_limitation_is_surfaced_as_configuration(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        observed = self.reviews_say(
            [{"id": 1, "user": {"login": "istos"}, "state": "COMMENTED",
              "commit_id": head}], author="istos", head=head)
        self.assertEqual(observed["limitation"]["kind"], "same-account")
        self.assertEqual(observed["limitation"]["login"], "istos")
        self.assertEqual(observed["formal"], [],
                         "a comment is not a formal review state")

    def test_a_formal_approval_at_the_head_meets_a_github_gate(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 2, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": head}],
                         head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])

    def test_a_formal_approval_of_an_earlier_commit_does_not_travel(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 2, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": "f" * 40}],
                         head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "approval-other-head")

    def test_changes_requested_beats_nothing_and_blocks(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 3, "user": {"login": "elena"},
                           "state": "CHANGES_REQUESTED", "commit_id": head}],
                         head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "changes-requested")

    def test_a_separate_reviewer_policy_refuses_the_authors_own_approval(self):
        self.gated(review="separate-reviewer")
        head = self.satisfied_checks()
        # GitHub would not accept this, but a board must not depend on
        # that: if an approval authored by the proposer ever arrives, the
        # policy that asked for a second identity still says no.
        self.reviews_say([{"id": 4, "user": {"login": "istos"},
                           "state": "APPROVED", "commit_id": head}],
                         author="istos", head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "no-separate-reviewer")

    def test_a_separate_reviewer_policy_is_met_by_another_identity(self):
        self.gated(review="separate-reviewer")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 5, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": head}],
                         author="istos", head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])
        row = [r for r in found["requirements"] if r["kind"] == "review"][0]
        self.assertEqual(row["reason"], "approved-by-another")

    def test_the_formal_state_survives_a_restart_bound_to_its_commit(self):
        self.gated(review="github")
        head = self.satisfied_checks()
        self.reviews_say([{"id": 2, "user": {"login": "elena"},
                           "state": "APPROVED", "commit_id": head}],
                         head=head)
        gates.REVIEW_STATE.clear()        # the board restarted
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")
        # and it is still bound to the commit it was given for, so a
        # restart cannot re-bind it to whatever the head has become
        moved = self.commit_in(self.wt, "pushed over the approval\n")
        gates.REVIEW_STATE.clear()
        verify.forget_cached()
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertNotEqual(found["head"]["commit"], head)
        self.assertEqual(found["head"]["commit"], moved)

    def test_a_verdict_records_what_happened_when_it_was_published(self):
        note = agents._publication_note(
            "gh: Can not approve your own pull request (HTTP 422)")
        self.assertIsNotNone(note)
        self.assertIn("refused-same-account", note)
        self.assertIsNone(agents._publication_note("all good"))


# ── an override is explicit, bound, and never a pass ───────────────────


class AnOverrideIsNeverAPass(GateCase):
    """What makes a proceed-anyway safe to have at all: who, why, which
    commit and which gates — and a decision that still reads
    `overridden`."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)

    def test_an_override_is_refused_where_the_policy_does_not_enable_one(self):
        self.gated()
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos",
                           reason="shipping the demo in ten minutes")
        self.assertIn("does not allow overriding", str(caught.exception))
        self.assertIn("\"allow\": true", str(caught.exception))
        self.assertEqual(gates.overrides_for(CARD), [])

    def test_an_actor_the_policy_does_not_name_is_refused(self):
        self.gated(overrides={"allow": True, "actors": ["elena"]})
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos",
                           reason="shipping the demo in ten minutes")
        self.assertIn("not one of the identities", str(caught.exception))
        self.assertEqual(gates.overrides_for(CARD), [])

    def test_an_override_records_actor_reason_head_and_the_unmet_gates(self):
        self.gated(overrides={"allow": True})
        result = gates.override(CARD, "acceptance", actor="istos",
                                reason="the check runner is down, demo at 5")
        record = result["override"]
        self.assertEqual(record["actor"], "istos")
        self.assertEqual(record["head"], self.head)
        self.assertEqual(record["gates"], ["unit"])
        self.assertEqual(record["unmet"][0]["reason"], "not-run")
        self.assertIn("demo at 5", record["reason"])
        self.assertTrue(record["policyId"])

    def test_an_override_leaves_the_decision_reading_overridden(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos",
                       reason="the check runner is down, demo at 5")
        found = self.decide()
        self.assertEqual(found["decision"], "overridden")
        self.assertTrue(gates.passable(found), "the merge may now run")
        self.assertEqual(found["requirements"][0]["state"], "overridden")
        self.assertIn("still unmet", found["summary"])
        self.assertEqual(found["overriddenGates"], ["unit"])
        # and it forged nothing: the evidence is exactly as absent as it
        # was before anybody clicked
        self.assertEqual(verify.for_task(CARD), [])
        self.assertEqual(found["requirements"][0]["reason"], "not-run")

    def test_a_second_click_does_not_record_a_second_override(self):
        self.gated(overrides={"allow": True})
        first = gates.override(CARD, "acceptance", actor="istos",
                               reason="the check runner is down, demo at 5")
        second = gates.override(CARD, "acceptance", actor="istos",
                                reason="the check runner is down, demo at 5")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(gates.overrides_for(CARD)), 1)

    def test_an_override_does_not_authorise_the_next_commit(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos",
                       reason="the check runner is down, demo at 5")
        self.assertEqual(self.decide()["decision"], "overridden")
        moved = self.commit_in(self.wt, "something else entirely\n")
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked",
                         "an override authorises one commit")
        self.assertEqual(found["head"]["commit"], moved)

    def test_a_confirmation_about_a_head_that_has_moved_is_refused(self):
        self.gated(overrides={"allow": True})
        stale = self.head
        self.commit_in(self.wt, "pushed while the sheet was open\n")
        gates.forget_cached()
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos",
                           reason="the check runner is down, demo at 5",
                           head=stale)
        self.assertIn("the head moved while you were looking",
                      str(caught.exception))
        self.assertEqual(gates.overrides_for(CARD), [])

    def test_an_override_survives_a_restart_and_is_not_applied_twice(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos",
                       reason="the check runner is down, demo at 5")
        gates.forget_cached()              # the board restarted
        self.assertEqual(self.decide()["decision"], "overridden")
        self.assertEqual(len(gates.overrides_for(CARD)), 1)

    def test_a_gate_that_becomes_unmet_afterwards_was_never_authorised(self):
        self.gated(review="bench", overrides={"allow": True})
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        # only the review half is unmet, so only the review half is in
        # the record
        record = gates.override(CARD, "acceptance", actor="istos",
                                reason="no reviewer until Monday")["override"]
        self.assertEqual(record["gates"], ["bench"])
        self.assertEqual(self.decide()["decision"], "overridden")
        # Now the check stops being met as well. The override named one
        # gate, so it does not stretch over the second — and it lapses
        # rather than covering the half it did name, because a decision
        # that let half an unmet gate through would be the widening this
        # record exists to prevent.
        (self.wt / "code.txt").write_text("edited after the fact\n",
                                          encoding="utf-8")
        verify.forget_cached()
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual([r["name"] for r in found["unmet"]],
                         ["unit", "bench"])

    def test_an_override_needs_a_reason_and_a_person(self):
        self.gated(overrides={"allow": True})
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason="meh")
        self.assertIn("a reason somebody can read later",
                      str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="",
                           reason="a perfectly good reason here")
        self.assertIn("recorded against a person", str(caught.exception))

    def test_there_is_nothing_to_override_on_a_met_gate(self):
        self.gated(overrides={"allow": True})
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos",
                           reason="just in case, for later")
        self.assertIn("nothing to override", str(caught.exception))

    def test_overrides_are_refused_while_the_policy_does_not_resolve(self):
        self.declares({"v": 1, "gates": {"acceptance": {"checks": ["nope"]},
                                         "overrides": {"allow": True}}})
        self.assertFalse(
            profiles.gate_policy()["overrides"]["allowed"],
            "a document with a refusal in it offers no way around itself")
        with self.assertRaises(ValueError):
            gates.override(CARD, "acceptance", actor="istos",
                           reason="the policy is broken, let me through")


# ── the backend refuses, not the page ──────────────────────────────────


class TheBackendIsTheAuthority(GateCase):
    """Every refusal a disabled button would explain is raised by the code
    that would have done the thing, in the same sentence, from the same
    reading."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)
        (self.tasks / "review" / CARD).write_text(
            card("71 — Gate integration", status="Review"), encoding="utf-8")
        (self.tasks / "in-progress" / CARD).unlink()

    def test_merge_and_clean_up_refuses_an_unmet_gate(self):
        self.gated()
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        said = str(caught.exception)
        self.assertIn("blocked by this board's acceptance gate", said)
        self.assertIn("nothing has asked bench for 'unit'", said)
        self.assertIn("evidence sheet", said, "the remedy travels with it")

    def test_a_refusal_leaves_the_card_the_branch_and_the_worktree(self):
        self.gated()
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertTrue((self.tasks / "review" / CARD).is_file())
        self.assertEqual(
            git(self.repo, "rev-parse", f"task/{STEM}").stdout.strip(),
            self.head)
        self.assertTrue(self.wt.is_dir())
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.repo, "rev-parse", "main").stdout.strip())
        self.assertNotIn(STEM, git(self.repo, "log", "--oneline",
                                   "main").stdout)

    def test_the_refusal_is_not_a_claim_left_on_the_card(self):
        self.gated()
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertEqual(state.completing_public(), {},
                         "the completing claim is given back on every exit")

    def test_a_met_gate_lets_the_merge_through(self):
        self.gated()
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(filename=CARD, stage="review")["outcome"],
                         "passed")
        self.assertEqual(gates.evaluate(CARD, "acceptance")["decision"],
                         "allowed")
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", self.head,
                   "main").returncode,
            "the verified commit is in the application base")
        self.assertEqual(self.stage_of(CARD), "done")

    def test_the_api_and_the_mutation_give_one_reason(self):
        self.gated()
        payload = httpd.gates_payload(CARD, "review")
        shown = payload["scopes"][0]
        self.assertEqual(shown["decision"], "blocked")
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        self.assertIn(shown["unmet"][0]["why"], str(caught.exception))

    def test_an_ungated_board_merges_exactly_as_it_always_did(self):
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])


# ── what the API says, and what it withholds ───────────────────────────


class TheApiSaysOneThing(GateCase):
    """The page reads the decision; it never computes one."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)

    def test_an_ungated_board_sends_no_gate_anywhere(self):
        payload = httpd.state_payload()
        self.assertEqual(payload["gates"], {},
                         "a board that declared no gate has no chip")
        self.assertFalse(payload["gatePolicy"]["configured"])

    def test_a_gated_card_carries_its_decision_and_its_reason(self):
        self.gated()
        payload = httpd.state_payload()
        found = payload["gates"][CARD]
        self.assertEqual(found["scope"], "acceptance")
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["name"], "unit")
        self.assertEqual(found["head"], self.head)
        self.assertEqual(found["headRef"], f"task/{STEM}")
        self.assertEqual(found["location"], "local")

    def test_the_policy_travels_with_the_state_and_holds_no_secrets(self):
        self.gated(review="bench")
        shown = httpd.state_payload()["gatePolicy"]
        self.assertTrue(shown["configured"])
        self.assertEqual(shown["scopes"]["acceptance"]["review"], "bench")
        self.assertEqual(
            [c["name"] for c in shown["scopes"]["acceptance"]["checks"]],
            ["unit"])
        self.assertEqual(json.loads(json.dumps(shown)), shown,
                         "it is JSON the page can read")

    def test_the_gate_sheet_reads_every_record_for_one_card(self):
        self.gated(review="bench", overrides={"allow": True})
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head,
                             agent="rev-1")
        self.reviews_say([{"id": 9, "user": {"login": "istos"},
                           "state": "COMMENTED", "commit_id": self.head}],
                         head=self.head)
        payload = httpd.gates_payload(CARD, "in-progress")
        self.assertEqual(len(payload["scopes"]), 1)
        self.assertEqual(payload["verdicts"][0]["verdict"], "APPROVE")
        self.assertEqual(payload["github"]["limitation"]["kind"],
                         "same-account")
        self.assertEqual(payload["overrides"], [])
        self.assertIn("ungated", payload["decisions"])
        self.assertIn("not-run", payload["reasons"])
        self.assertEqual(payload["location"], "local")

    def test_a_broken_policy_is_shown_with_its_remedy_not_as_a_pass(self):
        self.declares({"v": 1, "gates": {"acceptance": {"checks": ["nope"]}}})
        payload = httpd.state_payload()
        self.assertEqual(payload["gates"][CARD]["decision"], "blocked")
        shown = payload["gatePolicy"]
        self.assertTrue(shown["blockers"])
        self.assertEqual(shown["diagnostics"][0]["code"],
                         "gate-check-undeclared")
        self.assertTrue(shown["diagnostics"][0]["remedy"])

    def test_the_public_reading_is_an_allowlist(self):
        self.gated()
        shown = gates.public(gates.evaluate(CARD, "acceptance"))
        self.assertLessEqual(set(shown), set(gates.PUBLIC))
        self.assertEqual(json.loads(json.dumps(shown)), shown)


# ── a whole phase, gated, in a disposable repository ───────────────────


class AGatedPhase(PhaseCase):
    """The end-to-end half: a real phase run over a real repository, with
    a gate configured, driven one pass at a time.

    The fixture is `test_phase_runs`' own, so the thing under test is the
    runner that ships rather than a re-implementation of it — with the
    state directories this card adds patched into the case's temporary
    tree, because a test must never read or write the install's own."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.patch(EVIDENCE_DIR=self.tmp / "evidence",
                   VERIFY_DIR=self.tmp / "verify",
                   GATES_DIR=self.tmp / "gates",
                   AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
                   VERIFY_TIMEOUT=60.0, PREPARE_TIMEOUT=60.0,
                   VERIFY_PARALLEL=2, VERIFY_LOG_CAP=4 * 1024 * 1024,
                   APP_MERGE="")
        for registry in (verify.RUNNING, verify._KEYS, gates.REVIEW_STATE):
            registry.clear()
            self.addCleanup(registry.clear)
        verify.forget_cached()
        gates.forget_cached()
        self.addCleanup(verify.forget_cached)
        self.addCleanup(gates.forget_cached)

    def executable(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(body.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def declares(self, document: dict) -> None:
        (config.LOCAL / "profiles.json").write_text(json.dumps(document),
                                                    encoding="utf-8")
        gates.forget_cached()

    def gate(self, *, scopes=("member",), body: str = PASSES) -> None:
        self.declares({
            "v": 1,
            "checks": [{"name": "unit",
                        "run": str(self.executable("unit", body)),
                        "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]},
                         "review": {"verify": ["unit"]}},
            "gates": {scope: {"checks": ["unit"]} for scope in scopes},
        })

    def verify_card(self, filename: str) -> dict:
        """Ask bench for the check on one card, as a person would from the
        evidence sheet's armed ↻."""
        stage = self.stage_of(filename)
        return verify.request(check="unit", task=filename, stage=stage,
                              actor="you")

    def test_a_member_with_no_check_halts_the_phase_instead_of_merging(self):
        self.gate()
        self.start()
        found = self.advance()
        self.assertIsNotNone(found["halted"], "an unmet gate halts")
        self.assertIn("member gate is unmet", found["halted"])
        self.assertIn("nothing has asked bench for 'unit'", found["halted"])
        self.assertFalse(
            phases._contains(f"task/{ONE[:-3]}", PHASE_BRANCH),
            "nothing was merged into the phase branch")
        self.assertEqual(self.stage_of(ONE), "review",
                         "the member's own work is left exactly as it is")

    def test_no_ci_no_longer_means_advance_on_a_gated_board(self):
        """The case the SAFER report is about: a committed branch, a clean
        agent exit, and no CI result at all."""
        self.gate()
        self.start()
        self.assertEqual(github.PR_STATE.get(ONE, {}).get("ci"), None)
        self.advance()
        self.assertIn("halted", " ".join(self.log()))

    def test_a_verified_member_merges_and_the_phase_carries_on(self):
        self.gate()
        self.start()
        found = self.verify_card(ONE)
        self.assertEqual(found["outcome"], "passed", found["why"])
        snapshot = self.advance()
        self.assertIsNone(snapshot["halted"], snapshot.get("halted"))
        self.assertTrue(phases._contains(f"task/{ONE[:-3]}", PHASE_BRANCH))
        self.assertIn(f"{ONE[:-3].split('-')[0]} merged", " ".join(self.log()))

    def test_running_the_phase_again_after_verifying_clears_the_halt(self):
        self.gate()
        self.start()
        self.advance()
        self.assertIn("halted", " ".join(self.log()))
        self.verify_card(ONE)
        verify.forget_cached()
        gates.forget_cached()
        phases.start_phase(PHASE, "in-progress")
        self.settle()
        self.assertTrue(phases._contains(f"task/{ONE[:-3]}", PHASE_BRANCH))

    def test_a_members_gate_is_re_read_immediately_before_the_merge(self):
        """The window this closes: the snapshot said ready, and the branch
        moved before the merge ran."""
        self.gate()
        self.start()
        self.verify_card(ONE)
        verify.forget_cached()
        gates.forget_cached()
        snapshot = phases._snapshot(
            phases._cards()[0][PHASE], phases._cards()[0])
        member = next(m for m in snapshot["members"] if m["file"] == ONE)
        self.assertEqual(member["state"], "ready")
        # a push lands between the reading and the merge
        worktree = config.WORKTREES / ONE[:-3]
        (worktree / "late.txt").write_text("pushed after the reading\n",
                                           encoding="utf-8")
        git(worktree, "add", "-A")
        git(worktree, "-c", "user.email=a@b", "-c", "user.name=late",
            "commit", "-q", "-m", "late")
        verify.forget_cached()
        gates.forget_cached()
        with self.assertRaises(phases._Halt) as caught:
            phases._merge_member(phases._cards()[0][PHASE], member)
        self.assertIn("member gate is unmet", str(caught.exception))
        self.assertFalse(phases._contains(f"task/{ONE[:-3]}", PHASE_BRANCH))

    def test_the_integration_head_is_its_own_question(self):
        """Every member verified, every member merged — and the commit
        they were merged into has no evidence of its own."""
        self.gate(scopes=("member", "integration"))
        self.start()
        self.verify_card(ONE)
        self.advance()                       # merge 31, start 32
        self.verify_card(TWO)
        self.advance()                       # merge 32, open the phase PR
        self.assertEqual(self.stage_of(PHASE), "review")
        for member in (ONE, TWO):
            rows = [r for r in verify.for_task(member)
                    if r["outcome"] == "passed"]
            self.assertTrue(rows, f"{member} really did pass its check")
        found = gates.evaluate(PHASE, "integration")
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "not-run")
        self.assertEqual(found["head"]["commit"], self.tip(PHASE_BRANCH))
        with self.assertRaises(ValueError) as caught:
            github.complete_task(PHASE, "review")
        self.assertIn("integration gate", str(caught.exception))
        self.assertEqual(self.stage_of(PHASE), "review",
                         "a refused acceptance moves nothing")

    def test_verifying_the_integration_head_itself_lets_it_through(self):
        self.gate(scopes=("member", "integration"))
        self.start()
        self.verify_card(ONE)
        self.advance()
        self.verify_card(TWO)
        self.advance()
        found = self.verify_card(PHASE)
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual((found["source"] or {}).get("commit"),
                         self.tip(PHASE_BRANCH),
                         "the phase's check ran in the integration head")
        self.assertEqual(gates.evaluate(PHASE, "integration")["decision"],
                         "allowed")
        result = github.complete_task(PHASE, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(sorted(result["swept"]), sorted([ONE, TWO]))

    def test_a_replica_evaluates_and_advances_nothing(self):
        self.gate()
        self.start()
        self.verify_card(ONE)
        verify.forget_cached()
        gates.forget_cached()
        # team mode, and the card is held by somebody else
        self.patch(COMMIT_MOVES=True)
        text = self.text(PHASE).replace("**Priority:** High",
                                        "**Priority:** High\n"
                                        "**Assignee:** elena")
        (self.tasks / "in-progress" / PHASE).write_text(text,
                                                        encoding="utf-8")
        before = self.tip(PHASE_BRANCH)
        snapshot = self.advance()
        self.assertEqual(self.tip(PHASE_BRANCH), before,
                         "a replica merges nothing")
        member = next(m for m in snapshot["members"] if m["file"] == ONE)
        self.assertEqual(member["state"], "ready",
                         "it still renders the same reading")
        self.assertEqual(member["gate"]["decision"], "allowed")

    def test_the_snapshot_carries_the_gate_the_lane_draws(self):
        self.gate(scopes=("member", "integration"))
        self.start()
        snapshot = self.advance()
        member = next(m for m in snapshot["members"] if m["file"] == ONE)
        self.assertEqual(member["gate"]["decision"], "blocked")
        self.assertEqual(member["gate"]["scope"], "member")
        self.assertEqual(snapshot["gate"]["scope"], "integration")
        self.assertEqual(snapshot["gate"]["task"], PHASE)

    def test_an_ungated_phase_runs_exactly_as_it_always_did(self):
        self.start()
        self.advance()
        self.advance()
        self.assertEqual(self.stage_of(PHASE), "review")
        self.assertTrue(phases._contains(f"task/{ONE[:-3]}", PHASE_BRANCH))
        self.assertTrue(phases._contains(f"task/{TWO[:-3]}", PHASE_BRANCH))


# ── what the page says, and what it must never say ────────────────────


class ThePageReadsTheDecision(unittest.TestCase):
    """board.html is one file of inline JS with no frontend runner, so
    these are source-level invariants — the ones that, if broken, would
    put the page back where this card found it: a page that cannot say why
    a card may not advance, or one that draws an absence as a pass."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")
        # The gate's own code, so an invariant about it is asserted
        # against it rather than against the whole page — where a
        # perfectly good line of the verification panel would satisfy or
        # break it by accident.
        start = cls.html.index("may this merge happen?")
        end = cls.html.index("where a PR review ran", start)
        cls.region = cls.html[start:end]

    def test_the_page_asks_the_server_for_the_decision(self):
        self.assertIn("'/api/gates?file='", self.html)
        self.assertIn("'/api/gates/override'", self.html)

    def test_the_page_computes_no_decision_of_its_own(self):
        """The one thing a second evaluator would do: decide. The gate's
        own code never reads an evidence record, never compares a commit
        and never derives a verdict — those answers arrive resolved, and
        the page's whole job is to draw them."""
        self.longMessage = False
        for forbidden in ("S.evidence", "rollup", "outcome === ",
                          ".commit === ", "freshness", ".bound"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden, self.region,
                    f"the gate's own code in board.html contains "
                    f"{forbidden!r}, which is a decision the server makes")
        # the decision words it renders are exactly the server's, mapped
        # to a colour and a glyph and nothing else
        for word in gates.DECISIONS:
            with self.subTest(word=word):
                self.assertIn(f"{word}:", self.region)

    def test_ungated_is_drawn_in_the_settled_register_not_as_a_pass(self):
        self.assertRegex(self.html, r"ungated: \['dim'")
        self.assertRegex(self.html, r"allowed: \['ok'")
        self.assertIn("nothing is required", self.html)

    def test_an_override_is_never_drawn_as_a_gate_that_was_met(self):
        self.assertRegex(self.html, r"overridden: \['bad'")
        self.assertIn("the gate is still unmet", self.html)
        self.assertIn("forges no evidence", self.html)
        self.assertIn("authorises this commit", self.html)

    def test_the_confirmation_names_the_gates_and_the_commit(self):
        self.assertIn("proceeded over", self.html)
        self.assertIn("first.unmet", self.html)
        self.assertIn("(first.head || {}).commit", self.html)

    def test_the_focus_view_separates_produced_from_verified(self):
        self.assertIn("function focusGatePanel", self.html)
        self.assertIn("code produced", self.html)
        self.assertIn("A finished agent is not a verified card", self.html)

    def test_the_lane_says_what_accepting_the_phase_requires(self):
        self.assertIn("integration gate", self.html)
        self.assertIn("snap.gate", self.html)

    def test_the_two_review_records_are_drawn_apart(self):
        self.assertIn("Review — two records, kept apart", self.html)
        self.assertIn("a comment is not a review", self.html)
        self.assertIn("no formal GitHub review state", self.html)
        self.assertIn("same account", self.html)

    def test_the_chip_appears_only_where_a_gate_is_configured(self):
        self.assertIn("const gate = gateChip(task);", self.html)
        self.assertRegex(self.html,
                         r"function gateChip\(task\) \{\n.*\n  if \(!g\) return null;")

    def test_reading_it_is_not_an_action(self):
        self.assertIn("Opening this runs nothing", self.html)
        self.assertIn("Reading it here runs nothing", self.html)
