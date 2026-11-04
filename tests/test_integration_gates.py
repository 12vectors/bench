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
