"""Phase 66 review remediation: R1–R5, each as a real runner → gate case.

The review (`reviews/phase66/REVIEW.md`) reproduced five ways the gate
overclaimed what it proved. Every case here drives the real path: a
profile document on disk, a check executable on disk, a disposable git
checkout, `verify.request` running the process and writing evidence, and
`gates.evaluate` reading it — the same reading the merge and the phase
runner make. Nothing reaches GitHub: the formal review state is fed in as
the REST review objects the poll would read, `gh` is a stub script on a
temporary PATH, and every remote is a local bare repository.

- **R1 — a scope is part of the requirement.** A gate names a check and,
  optionally, a declared scope of it. A legacy bare name requires the
  unscoped full run. A narrow pass never satisfies a broader requirement,
  and the newest relevant answer is the one a gate reads, so a failed or
  running rerun supersedes an earlier pass.
- **R2 — the program is part of the evidence.** The executable's bytes,
  any script an interpreter is handed, every preparation step's program
  and bench's own runner revision are bound into the record. Replacing a
  file at the same path makes a pass stale.
- **R3 — an outstanding change request blocks.** Per reviewer, the latest
  formal state stands; another reviewer's approval does not dismiss it,
  and a comment dismisses nothing.
- **R4 — the merge is of the commit the gate read.** The validated head
  flows into the local merge, the phase merge and `gh pr merge
  --match-head-commit`; a ref advanced at the boundary is neither merged
  nor deleted.
- **R5 — intended write scope is not an OS boundary,** and the summary
  says so where it is read, not only in a fold.

    python3 -m unittest tests.test_gate_remediation -v
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import gates  # noqa: E402
import profiles  # noqa: E402
import verify  # noqa: E402

from tests.test_integration_gates import (CARD, PASSES, GateCase,  # noqa: E402
                                          wait_for)

# A check whose verdict depends on its scope arguments: everything passes
# except the broad `all` run, which is the reviewer's reproduction.
BY_SCOPE = """#!{python}
import sys
sys.exit(1 if "all" in sys.argv else 0)
"""

# A check whose verdict is read from a file outside the checkout, so a
# rerun at the same commit can answer differently without the source (or
# the executable) changing — a flaky or newly failing suite.
FLAG_READER = """#!{python}
import sys, time
from pathlib import Path
word = Path({flag!r}).read_text().strip()
if word == "hang":
    print("working", flush=True)
    time.sleep(600)
sys.exit(0 if word == "pass" else 1)
"""


class ScopeIsPartOfTheRequirement(GateCase):
    """R1. The gate's requirement is a check *and* the coverage it needs,
    and evidence is matched against both."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()

    def scoped(self, requirement, *, body: str = BY_SCOPE,
               scopes: dict | None = None) -> dict:
        document = self.gated(body=body, scopes_for_check=scopes or {
            "backend": ["backend"], "all": ["all"]})
        document["gates"]["acceptance"]["checks"] = requirement
        self.declares(document)
        return document

    def flag(self, word: str) -> Path:
        path = self.tmp / "verdict.flag"
        path.write_text(word, encoding="utf-8")
        return path

    def flagged(self, requirement=("unit",)) -> None:
        flag = self.flag("pass")
        body = FLAG_READER.replace("{flag!r}", repr(str(flag)))
        self.scoped(list(requirement), body=body)

    # — the reviewer's reproduction, end to end —

    def test_a_narrow_pass_does_not_satisfy_a_legacy_requirement(self):
        """`unit backend` passed, `unit all` failed: the review saw the
        gate read MET. A bare name requires the unscoped full run, and
        neither scoped record is that."""
        self.scoped(["unit"])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        self.assertEqual(self.ask(scope="all")["outcome"], "failed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked", found["summary"])
        row = found["unmet"][0]
        self.assertEqual(row["check"], "unit")
        self.assertIsNone(row["scope"])
        # the newest relevant answer is the failure, and the reader is told
        # which scope it came from
        self.assertEqual(row["reason"], "failed")
        self.assertIn("all", row["why"])

    def test_a_backend_pass_alone_is_not_run_for_the_full_check(self):
        self.scoped(["unit"])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "not-run")
        self.assertIn("full run", row["why"])
        self.assertIn("backend", row["why"])

    def test_the_unscoped_full_run_meets_a_legacy_requirement(self):
        self.scoped(["unit"])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])
        self.assertIsNone(found["requirements"][0]["scope"])
        self.assertIn("full run", found["requirements"][0]["why"])

    # — the explicit schema —

    def test_a_scoped_requirement_is_met_by_its_own_scope(self):
        self.scoped([{"check": "unit", "scope": "backend"}])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "allowed", found["summary"])
        row = found["requirements"][0]
        self.assertEqual((row["check"], row["scope"]), ("unit", "backend"))
        self.assertEqual(row["name"], "unit:backend")
        self.assertIn("scope backend", row["why"])

    def test_the_full_run_covers_a_scoped_requirement(self):
        self.scoped([{"check": "unit", "scope": "backend"}])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_another_scope_does_not_cover_a_scoped_requirement(self):
        self.scoped([{"check": "unit", "scope": "all"}],
                    body=PASSES)
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "not-run")

    def test_a_narrow_pass_cannot_mask_a_broader_failure(self):
        """Both required: the narrow one passing is one met row among two,
        and the broad failure is the gate's answer."""
        self.scoped([{"check": "unit", "scope": "backend"}, "unit"])
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        self.assertEqual(self.ask(scope="all")["outcome"], "failed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual([r["name"] for r in found["unmet"]], ["unit"])
        self.assertEqual([r["state"] for r in found["requirements"]],
                         ["met", "unmet"])

    def test_a_full_failure_after_a_scoped_pass_supersedes_it(self):
        """A scoped requirement is covered by the full run too, so the full
        run failing afterwards is newer, relevant, and decisive."""
        flag = self.flag("pass")
        body = FLAG_READER.replace("{flag!r}", repr(str(flag)))
        self.scoped([{"check": "unit", "scope": "backend"}], body=body)
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(scope="backend")["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")
        flag.write_text("fail", encoding="utf-8")
        self.assertEqual(self.ask()["outcome"], "failed")
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "failed")

    # — supersession: the newest relevant answer is the gate's —

    def test_a_failed_rerun_at_the_head_supersedes_an_earlier_pass(self):
        self.flagged()
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")
        self.flag("fail")
        # the flag is outside the result identity, so only an explicit
        # rerun runs it again — an unforced request reuses the pass (#100)
        self.assertEqual(self.ask(again=True)["outcome"], "failed")
        verify.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked", found["summary"])
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "failed")
        self.assertIn("newer", row["why"])

    def test_a_pass_after_a_failure_is_the_answer(self):
        self.flagged()
        self.flag("fail")
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "failed")
        self.flag("pass")
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_a_running_rerun_holds_an_earlier_pass_pending(self):
        self.flagged()
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        self.flag("hang")
        asking = threading.Thread(target=lambda: self.ask(again=True),
                                  daemon=True)
        asking.start()
        try:
            # The registry reserves a slot before publishing evidence. Wait
            # for the actual check process and its readable running record,
            # not that reservation: the gate reads persisted evidence.
            self.assertTrue(wait_for(lambda: any(
                record.get("proc") is not None
                and (verify.read(ident) or {}).get("outcome") == "running"
                for ident, record in list(verify.RUNNING.items()))))
            found = self.decide()
            self.assertEqual(found["decision"], "pending", found["summary"])
            with self.assertRaises(ValueError):
                gates.require(found, "merging")
        finally:
            for ident in list(verify.RUNNING):
                verify.cancel(ident)
            asking.join(timeout=20)
            self.assertFalse(asking.is_alive(), "verification must end before teardown")
        # and a cancelled rerun decided nothing — but it is the newest
        # answer, so the earlier pass does not come back by itself
        verify.forget_cached()
        self.assertEqual(self.decide()["decision"], "blocked")

    def test_a_denied_request_ran_nothing_and_supersedes_nothing(self):
        self.flagged()
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        self.assertEqual(self.ask(scope="nonesuch")["outcome"], "denied")
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_ordering_is_deterministic_and_a_tie_is_not_a_pass(self):
        """Two answers recorded at one instant: whichever order the files
        are listed in, the gate does not read the pass."""
        self.flagged()
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        passed = self.ask()
        self.flag("fail")
        failed = self.ask(again=True)
        for record in (passed, failed):
            stored = verify.read(record["id"])
            stored["requested"] = 1_000.0
            stored["started"] = 1_000.0
            stored["ended"] = 1_001.0
            verify.path_of(record["id"]).write_text(json.dumps(stored),
                                                    encoding="utf-8")
        verify.forget_cached()
        records = verify.for_task(CARD)
        for order in (records, list(reversed(records))):
            found = gates.evaluate(CARD, "acceptance", records=order)
            self.assertEqual(found["decision"], "blocked")
            self.assertEqual(found["unmet"][0]["reason"], "failed")

    # — the schema is validated where the policy is read —

    def test_a_scope_the_check_does_not_declare_is_refused(self):
        self.scoped([{"check": "unit", "scope": "frontend"}])
        codes = [d["code"] for d in profiles.gate_policy()["diagnostics"]]
        self.assertIn("gate-scope-undeclared", codes)
        self.assertEqual(self.decide()["decision"], "blocked")

    def test_a_requirement_object_with_an_unknown_key_is_refused(self):
        self.scoped([{"check": "unit", "scopes": ["backend"]}])
        codes = [d["code"] for d in profiles.gate_policy()["diagnostics"]]
        self.assertIn("gate-invalid", codes)

    def test_a_requirement_object_naming_no_check_is_refused(self):
        self.scoped([{"scope": "backend"}])
        codes = [d["code"] for d in profiles.gate_policy()["diagnostics"]]
        self.assertIn("name-invalid", codes)

    def test_every_code_the_schema_can_raise_has_a_remedy(self):
        for code in ("gate-scope-undeclared",):
            self.assertIn(code, profiles.DIAGNOSTICS)

    def test_a_scoped_requirement_changes_the_policy_revision(self):
        self.scoped(["unit"])
        legacy = profiles.gate_policy()["revision"]
        self.scoped([{"check": "unit", "scope": "backend"}])
        self.assertNotEqual(legacy, profiles.gate_policy()["revision"])

    def test_the_public_policy_names_each_requirements_scope(self):
        self.scoped([{"check": "unit", "scope": "backend"}, "unit"])
        public = profiles.gate_policy_public(profiles.gate_policy())
        rows = public["scopes"]["acceptance"]["checks"]
        self.assertEqual([(r["name"], r["scope"]) for r in rows],
                         [("unit", "backend"), ("unit", None)])

    def test_the_example_document_declares_a_scoped_requirement(self):
        text = (REPO / "manager" / "core" / "profiles.example.json").read_text(
            encoding="utf-8")
        self.assertIn('"scope":', text)


class TheProgramIsPartOfTheEvidence(GateCase):
    """R2. A path and an argument list name a program; they do not pin
    one. The bytes that ran are part of what a pass is about."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()

    def passed_once(self, document: dict | None = None) -> dict:
        if document is not None:
            self.declares(document)
        self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        found = self.ask()
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(self.decide()["decision"], "allowed")
        return found

    def reread(self) -> dict:
        verify.forget_cached()
        gates.forget_cached()
        return self.decide()

    def test_the_record_names_the_programs_bytes(self):
        found = self.passed_once(self.gated())
        program = found["program"]
        self.assertTrue(program["bound"])
        roles = {f["role"]: f for f in program["files"]}
        self.assertEqual(roles["executable"]["path"],
                         str((self.bin / "unit").resolve()))
        self.assertEqual(len(roles["executable"]["sha256"]), 64)
        # the script's shebang names an interpreter, and that is bound too
        self.assertIn("interpreter", roles)
        # and it says what it is not: a pinned program, not a sealed runtime
        self.assertIn("not a sandbox", program["why"])

    def test_replacing_the_executable_at_the_same_path_invalidates_it(self):
        """The reviewer's reproduction: same path, failing program, and the
        gate still read allowed."""
        self.passed_once(self.gated())
        from tests.test_integration_gates import FAILS
        self.executable("unit", FAILS)
        found = self.reread()
        self.assertEqual(found["decision"], "blocked", found["summary"])
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "stale")
        self.assertIn("executable", row["why"])
        record = verify.for_task(CARD)[0]
        self.assertEqual(verify.outcome_now(record)["reason"], "program-changed")

    def test_an_executable_that_has_gone_invalidates_it(self):
        self.passed_once(self.gated())
        (self.bin / "unit").unlink()
        found = self.reread()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "stale")

    def test_a_script_handed_to_an_interpreter_is_bound(self):
        """`python3 /abs/check.py`: the interpreter is the executable and
        the script is where the verdict comes from."""
        script = self.tmp / "tools" / "check.py"
        script.parent.mkdir()
        script.write_text("print('ok')\n", encoding="utf-8")
        document = self.gated()
        document["checks"][0]["run"] = f"{sys.executable} {script}"
        found = self.passed_once(document)
        roles = [f["role"] for f in found["program"]["files"]]
        self.assertIn("argument", roles)
        script.write_text("raise SystemExit(1)\n", encoding="utf-8")
        self.assertEqual(self.reread()["unmet"][0]["reason"], "stale")

    def test_a_file_inside_the_checkout_is_the_sources_to_bind(self):
        """A script the checkout holds is bound by the source identity
        already; the program identity names only what lies outside it."""
        found = self.passed_once(self.gated())
        root = found["root"]["path"]
        for entry in found["program"]["files"]:
            self.assertFalse(entry["path"].startswith(root + "/"), entry)

    def test_replacing_a_preparation_program_at_the_same_path_invalidates_it(self):
        self.executable("prep", PASSES)
        document = self.gated()
        document["prepare"] = [{"name": "deps",
                                "run": str(self.bin / "prep")}]
        document["checks"][0]["prepare"] = ["deps"]
        found = self.passed_once(document)
        step = found["prepare"][0]
        self.assertEqual(step["outcome"], "prepared")
        self.assertTrue(step["program"]["bound"])
        self.executable("prep", PASSES.replace("OK", "OK, and different"))
        found = self.reread()
        self.assertEqual(found["decision"], "blocked")
        self.assertIn("preparation", found["unmet"][0]["why"])

    def test_the_runner_revision_is_recorded_and_compared(self):
        found = self.passed_once(self.gated())
        self.assertEqual(found["runner"]["revision"], verify.RUNNER_REVISION)
        stored = verify.read(found["id"])
        stored["runner"]["revision"] = "an-earlier-runner"
        verify.path_of(found["id"]).write_text(json.dumps(stored),
                                               encoding="utf-8")
        result = self.reread()
        self.assertEqual(result["decision"], "blocked")
        self.assertIn("runner", result["unmet"][0]["why"])

    def test_a_record_with_no_program_identity_fails_closed(self):
        """Evidence written before programs were bound names no bytes, so
        it cannot say the program is unchanged — and is not reused."""
        found = self.passed_once(self.gated())
        stored = verify.read(found["id"])
        stored.pop("program")
        verify.path_of(found["id"]).write_text(json.dumps(stored),
                                               encoding="utf-8")
        result = self.reread()
        self.assertEqual(result["decision"], "blocked")
        self.assertEqual(result["unmet"][0]["reason"], "stale")

    def test_an_executable_bench_cannot_resolve_is_unbound(self):
        identity = verify.program_identity(["no-such-program-anywhere"],
                                           self.tmp, "/nonexistent")
        self.assertFalse(identity["bound"])
        self.assertIsNone(identity["programId"])


class AnOutstandingChangeRequestBlocks(GateCase):
    """R3. Per reviewer, the latest formal state stands. Another
    reviewer's approval dismisses nobody, a comment dismisses nothing, and
    a change request on an earlier commit is still outstanding."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()

    def head(self, review: str = "separate-reviewer") -> str:
        self.gated(review=review)
        head = self.commit_in(self.wt)
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask()["outcome"], "passed")
        return head

    @staticmethod
    def review(ident, login, word, commit, at):
        return {"id": ident, "user": {"login": login}, "state": word,
                "commit_id": commit, "submitted_at": f"2026-11-04T{at}:00Z"}

    def review_row(self, found):
        return [r for r in found["requirements"] if r["kind"] == "review"][0]

    def test_another_reviewers_approval_does_not_mask_a_change_request(self):
        """The reviewer's reproduction: alice asks for changes, bob
        approves, one exact head — the gate read allowed."""
        for policy in ("separate-reviewer", "github"):
            with self.subTest(policy=policy):
                head = self.head(policy)
                self.reviews_say([
                    self.review(1, "alice", "CHANGES_REQUESTED", head, "10:00"),
                    self.review(2, "bob", "APPROVED", head, "11:00")],
                    head=head)
                found = self.decide()
                self.assertEqual(found["decision"], "blocked", found["summary"])
                row = self.review_row(found)
                self.assertEqual(row["reason"], "changes-requested")
                self.assertIn("alice", row["why"])
                self.assertEqual([r["login"] for r in row["outstanding"]],
                                 ["alice"])

    def test_the_same_reviewers_later_approval_supersedes_her_request(self):
        head = self.head()
        self.reviews_say([
            self.review(1, "alice", "CHANGES_REQUESTED", head, "10:00"),
            self.review(2, "alice", "APPROVED", head, "11:00")], head=head)
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_supersession_follows_submission_time_not_list_order(self):
        head = self.head()
        self.reviews_say([
            self.review(2, "alice", "APPROVED", head, "11:00"),
            self.review(1, "alice", "CHANGES_REQUESTED", head, "12:00")],
            head=head)
        self.assertEqual(self.review_row(self.decide())["reason"],
                         "changes-requested")

    def test_a_comment_does_not_dismiss_a_change_request(self):
        head = self.head()
        self.reviews_say([
            self.review(1, "alice", "CHANGES_REQUESTED", head, "10:00"),
            self.review(2, "alice", "COMMENTED", head, "11:00"),
            self.review(3, "bob", "APPROVED", head, "12:00")], head=head)
        self.assertEqual(self.review_row(self.decide())["reason"],
                         "changes-requested")

    def test_a_dismissed_change_request_no_longer_blocks(self):
        """GitHub reports a dismissed review with the state DISMISSED, on
        the same review object."""
        head = self.head()
        self.reviews_say([
            self.review(1, "alice", "DISMISSED", head, "10:00"),
            self.review(2, "bob", "APPROVED", head, "11:00")], head=head)
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_a_dismissed_approval_is_not_an_approval(self):
        head = self.head()
        self.reviews_say([
            self.review(2, "bob", "DISMISSED", head, "11:00")], head=head)
        self.assertEqual(self.decide()["decision"], "blocked")

    def test_a_change_request_on_an_earlier_commit_is_still_outstanding(self):
        """Conservative on a stale head: GitHub keeps a change request
        standing across pushes until it is dismissed or re-reviewed, and
        so does the gate. An approval does not travel; a request does."""
        head = self.head()
        self.reviews_say([
            self.review(1, "alice", "CHANGES_REQUESTED", "e" * 40, "10:00"),
            self.review(2, "bob", "APPROVED", head, "11:00")], head=head)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = self.review_row(found)
        self.assertEqual(row["reason"], "changes-requested")
        self.assertIn("e" * 12, row["why"])

    def test_the_decision_says_it_is_not_githubs_branch_protection(self):
        head = self.head()
        self.reviews_say([self.review(2, "bob", "APPROVED", head, "11:00")],
                         head=head)
        row = self.review_row(self.decide())
        self.assertEqual(row["state"], "met")
        self.assertIn("branch protection", row["authority"])
        self.assertIn("branch protection",
                      profiles.REVIEW_POLICIES["separate-reviewer"])


# A stand-in for `gh`, faithful in the one way that matters here: `pr
# merge --match-head-commit` is refused unless the pull request's head —
# the branch on the bare "origin" — is exactly that commit, as GitHub
# refuses it. It can also advance that branch *after* merging, which is the
# push that lands between the merge and the cleanup.
FAKE_GH = """#!{python}
import json, subprocess, sys
from pathlib import Path
state = Path({state!r})
args = sys.argv[1:]
with (state / "calls.log").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "merge"] and "--help" in args:
    print("Usage: gh pr merge [<number> | <url> | <branch>] [flags]")
    if not (state / "old-gh").exists():
        print("      --match-head-commit SHA   Commit SHA that the pull "
              "request head must match to allow merge")
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    print(json.dumps({{"url": "https://github.com/o/r/pull/7",
                      "baseRefName": "main"}}))
    sys.exit(0)
if args[:2] == ["pr", "merge"]:
    ref = "refs/heads/" + {branch!r}
    tip = subprocess.run(["git", "--git-dir", {bare!r}, "rev-parse", ref],
                         capture_output=True, text=True).stdout.strip()
    if "--match-head-commit" in args:
        want = args[args.index("--match-head-commit") + 1]
        if tip != want:
            print("GraphQL: Head branch was modified. Review and try the "
                  "merge again.", file=sys.stderr)
            sys.exit(1)
    (state / "merged").write_text(tip)
    # and the base on the remote now holds it, as GitHub's merge leaves it
    sys.path.insert(0, {tests!r})
    import fake_origin
    fake_origin.merge_pr({bare!r}, {branch!r})
    after = state / "advance-after-merge"
    if after.exists():
        subprocess.run(["git", "--git-dir", {bare!r}, "update-ref", ref,
                        after.read_text().strip()])
    sys.exit(0)
sys.exit(0)
"""


def calls(state: Path) -> list[list[str]]:
    path = state / "calls.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class TheMergeIsOfTheCommitTheGateRead(GateCase):
    """R4. Reading the gate twice narrows a window; it does not close it.
    The commit the gate validated is what each merge takes, and what each
    cleanup is allowed to delete."""

    def setUp(self):
        super().setUp()
        import github
        self.github = github
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)
        self.review_card()

    def review_card(self, pr: str = "") -> None:
        from tests.test_integration_gates import card
        (self.tasks / "in-progress" / CARD).unlink(missing_ok=True)
        text = card("71 — Gate integration", status="Review")
        if pr:
            text = text.replace("**Priority:** High",
                                f"**Priority:** High\n**PR:** {pr}")
        (self.tasks / "review" / CARD).write_text(text, encoding="utf-8")

    def verified(self) -> None:
        self.gated()
        self.manifest(worktree=self.wt)
        self.assertEqual(self.ask(filename=CARD, stage="review")["outcome"],
                         "passed")
        self.assertEqual(self.decide()["decision"], "allowed")

    def at_the_boundary(self, action) -> None:
        """Run `action` once, after the last gate read and before the merge
        — the seam is the merge-mode question asked between the two."""
        import config
        original = config.app_merge_on_origin
        fired: list = []

        def boundary():
            if not fired:
                fired.append(action())
            return original()

        self.addCleanup(setattr, config, "app_merge_on_origin", original)
        config.app_merge_on_origin = boundary

    def tip(self, ref: str, repo: Path | None = None) -> str:
        from tests.test_integration_gates import git
        return git(repo or self.repo, "rev-parse", ref).stdout.strip()

    def contains(self, commit: str, ref: str, repo: Path | None = None) -> bool:
        from tests.test_integration_gates import git
        return git(repo or self.repo, "merge-base", "--is-ancestor",
                   commit, ref).returncode == 0

    # — the local merge —

    def test_the_local_merge_takes_the_commit_the_gate_read(self):
        self.verified()
        late: list[str] = []
        self.at_the_boundary(
            lambda: late.append(self.commit_in(self.wt, "a late commit\n")))
        result = self.github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertTrue(late, "the boundary fired")
        self.assertTrue(self.contains(self.head, "main"))
        self.assertFalse(self.contains(late[0], "main"),
                         "a commit nobody verified reached the base")
        # and the cleanup did not take the unmerged commit with it
        self.assertEqual(self.tip(f"task/{CARD[:-3]}"), late[0])
        self.assertTrue(self.wt.is_dir())
        said = " ".join(e["summary"] for e in __import__("state").BOARD_EVENTS)
        self.assertIn("moved on", said)

    def test_the_local_merge_lands_exactly_the_gated_commit(self):
        """Nothing moved, so the base fast-forwards to precisely the commit
        the gate read, and the workspace is cleaned up as before."""
        self.verified()
        result = self.github.complete_task(CARD, "review")
        self.assertEqual(self.tip("main"), self.head)
        self.assertFalse(self.wt.exists())
        self.assertEqual({k: result[k] for k in ("merged", "swept", "moved")},
                         {"merged": True, "swept": [], "moved": True},
                         "a whole merge reports what it always did")
        self.assertNotIn("partial", result)
        # no remote here: a local-only completion, said in those words
        # rather than as a publication
        self.assertEqual(result["publication"]["state"], "local-only")
        self.assertFalse(result["published"])
        self.assertEqual(self.stage_of(CARD), "done")

    def test_a_head_that_moved_past_the_gate_is_a_partial_completion(self):
        """The validated commit merged; the newer one did not. The card is
        not Done — its newest work is not in the base — so it stays where
        it was, and the result and the ticker say the completion is
        partial rather than leaving `merged: true` to read as accepted."""
        self.verified()
        late: list[str] = []
        self.at_the_boundary(
            lambda: late.append(self.commit_in(self.wt, "a late commit\n")))
        result = self.github.complete_task(CARD, "review")
        self.assertTrue(result["merged"], "the gated commit did merge")
        self.assertTrue(result.get("partial"), result)
        self.assertEqual(result.get("stage"), "review")
        self.assertEqual(result["unmerged"]["merged"], self.head)
        self.assertEqual(result["unmerged"]["tip"], late[0])
        self.assertEqual(self.stage_of(CARD), "review",
                         "newer unmerged work was marked Done")
        text = (self.tasks / "review" / CARD).read_text(encoding="utf-8")
        self.assertIn("**Status:** Review", text)
        said = " ".join(e["summary"] for e in __import__("state").BOARD_EVENTS)
        self.assertIn("partly completed", said)
        self.assertIn("stays in review/", said)

    def test_a_merge_commit_still_names_the_branch(self):
        from tests.test_integration_gates import git
        (self.repo / "other.txt").write_text("main moved on\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "main moved on")
        self.verified()
        self.github.complete_task(CARD, "review")
        subject = git(self.repo, "log", "-1", "--format=%s", "main").stdout
        self.assertIn(f"task/{CARD[:-3]}", subject)

    def test_an_ungated_local_merge_is_unchanged(self):
        result = self.github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertTrue(self.contains(self.head, "main"))
        self.assertFalse(self.wt.exists())

    # — the merge on origin —

    def origin(self, *, old_gh: bool = False) -> Path:
        from tests.test_integration_gates import git
        bare = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        git(self.repo, "remote", "add", "origin", str(bare))
        git(self.repo, "push", "-q", "origin", "main", f"task/{CARD[:-3]}")
        self.gh_state = self.tmp / "gh-state"
        self.gh_state.mkdir()
        if old_gh:
            (self.gh_state / "old-gh").write_text("")
        import stat
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable,
                                     state=str(self.gh_state),
                                     branch=f"task/{CARD[:-3]}",
                                     bare=str(bare),
                                     tests=str(Path(__file__).resolve().parent)),
                     encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.patch(GIT_REMOTE="origin", APP_MERGE="origin", GH_BIN=str(gh))
        self.review_card(pr="https://github.com/o/r/pull/7")
        return bare

    def merges(self) -> list[list[str]]:
        return [c for c in calls(self.gh_state)
                if c[:2] == ["pr", "merge"] and "--help" not in c]

    def test_the_origin_merge_is_conditional_on_the_gated_head(self):
        self.origin()
        self.verified()
        self.github.complete_task(CARD, "review")
        (merge,) = self.merges()
        self.assertIn("--match-head-commit", merge)
        self.assertEqual(merge[merge.index("--match-head-commit") + 1],
                         self.head)
        self.assertEqual(self.stage_of(CARD), "done")

    def test_a_head_that_advanced_on_origin_is_refused_by_the_merge(self):
        bare = self.origin()
        self.verified()
        from tests.test_integration_gates import git

        def push_lands():
            late = self.commit_in(self.wt, "pushed at the boundary\n")
            git(self.repo, "push", "-q", "origin", f"task/{CARD[:-3]}")
            return late

        self.at_the_boundary(push_lands)
        with self.assertRaises(ValueError) as caught:
            self.github.complete_task(CARD, "review")
        self.assertIn("origin would not merge", str(caught.exception))
        self.assertFalse((self.gh_state / "merged").exists())
        self.assertEqual(self.stage_of(CARD), "review")
        self.assertNotEqual(self.tip(f"task/{CARD[:-3]}", bare), "")

    def test_a_gh_without_the_flag_is_refused_not_worked_around(self):
        self.origin(old_gh=True)
        self.verified()
        with self.assertRaises(ValueError) as caught:
            self.github.complete_task(CARD, "review")
        self.assertIn("--match-head-commit", str(caught.exception))
        self.assertEqual(self.merges(), [], "no unconditional merge was tried")
        self.assertEqual(self.stage_of(CARD), "review")

    def test_an_ungated_origin_merge_is_unchanged(self):
        self.origin(old_gh=True)
        self.github.complete_task(CARD, "review")
        (merge,) = self.merges()
        self.assertNotIn("--match-head-commit", merge)

    def test_a_push_after_the_merge_is_not_deleted_on_origin(self):
        """The remote branch is deleted only while it still holds nothing
        but what was merged — atomically, with a lease on what was read."""
        bare = self.origin()
        self.verified()
        from tests.test_integration_gates import git
        # a commit somebody else pushes, made without moving this branch
        late = git(self.repo, "commit-tree", f"{self.head}^{{tree}}", "-p",
                   self.head, "-m", "pushed after the merge").stdout.strip()
        git(self.repo, "push", "-q", "origin", f"{late}:refs/heads/late")
        (self.gh_state / "advance-after-merge").write_text(late)
        self.github.complete_task(CARD, "review")
        self.assertEqual(self.tip(f"refs/heads/task/{CARD[:-3]}", bare), late,
                         "an unmerged push was deleted from origin")

    def test_the_remote_delete_is_leased_on_the_value_it_read(self):
        """A push landing between the read and the delete: git's lease
        refuses, rather than bench deleting what it never read."""
        bare = self.origin()
        from tests.test_integration_gates import git
        branch = f"task/{CARD[:-3]}"
        late = git(self.repo, "commit-tree", f"{self.head}^{{tree}}", "-p",
                   self.head, "-m", "raced").stdout.strip()
        git(self.repo, "push", "-q", "origin", f"{late}:refs/heads/late")
        original = self.github._run
        raced: list = []

        def race(cmd, *args, **kwargs):
            if any(str(part).startswith("--force-with-lease") for part in cmd):
                raced.append(git(bare, "update-ref", f"refs/heads/{branch}",
                                 late))
            return original(cmd, *args, **kwargs)

        self.addCleanup(setattr, self.github, "_run", original)
        self.github._run = race
        deleted = self.github._delete_remote_branch("origin", branch,
                                                    self.head, CARD)
        self.assertTrue(raced, "the race was injected at the delete")
        self.assertFalse(deleted)
        self.assertEqual(self.tip(f"refs/heads/{branch}", bare), late)

    def test_a_local_commit_after_the_gate_keeps_the_local_branch(self):
        self.origin()
        self.verified()
        late: list[str] = []
        self.at_the_boundary(
            lambda: late.append(self.commit_in(self.wt, "local only\n")))
        result = self.github.complete_task(CARD, "review")
        self.assertEqual(self.tip(f"task/{CARD[:-3]}"), late[0],
                         "-D removed a commit nobody merged")
        self.assertTrue(self.wt.is_dir())
        self.assertTrue(result.get("partial"), result)
        self.assertEqual(self.stage_of(CARD), "review",
                         "origin merged the gated head, not the local commit")

    # — a phase member's workspace —

    def test_a_member_branch_that_moved_after_the_phase_merged_is_kept(self):
        from tests.test_integration_gates import git
        member = "72-a-member.md"
        path = self.worktree(member[:-3])
        merged = self.commit_in(path, "the member's work\n")
        late = self.commit_in(path, "after the phase merged it\n")
        note = self.github._clear_member({"file": member}, merged)
        self.assertIn("moved on", note)
        self.assertEqual(self.tip(f"task/{member[:-3]}"), late)
        self.assertTrue(path.is_dir())
        # and one that did not move is cleared as before
        git(path, "reset", "-q", "--hard", merged)
        self.assertEqual(self.github._clear_member({"file": member}, merged), "")
        self.assertFalse(path.exists())


class APhaseMergesTheGatedCommit(unittest.TestCase):
    """R4, in the phase runner: the member's merge into the integration
    branch takes the commit its member gate read."""

    def setUp(self):
        from tests.test_phase_runs import PhaseCase
        import config
        import stat
        self.case = type("Case", (PhaseCase,), {"runTest": lambda s: None})()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        c = self.case
        c.patch(EVIDENCE_DIR=c.tmp / "evidence", VERIFY_DIR=c.tmp / "verify",
                GATES_DIR=c.tmp / "gates", AGENT_PROFILES="",
                VERIFY_TIMEOUT=60.0, PREPARE_TIMEOUT=60.0, VERIFY_PARALLEL=2,
                APP_MERGE="")
        for registry in (verify.RUNNING, verify._KEYS, gates.REVIEW_STATE):
            registry.clear()
            self.addCleanup(registry.clear)
        verify.forget_cached()
        gates.forget_cached()
        self.addCleanup(verify.forget_cached)
        self.addCleanup(gates.forget_cached)
        unit = c.tmp / "bin" / "unit"
        unit.parent.mkdir(exist_ok=True)
        unit.write_text(PASSES.format(python=sys.executable), encoding="utf-8")
        unit.chmod(unit.stat().st_mode | stat.S_IEXEC)
        (config.LOCAL / "profiles.json").write_text(json.dumps({
            "v": 1,
            "checks": [{"name": "unit", "run": str(unit), "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]}},
            "gates": {"member": {"checks": ["unit"]}}}), encoding="utf-8")

    def test_a_commit_landing_after_the_member_gate_is_not_merged(self):
        import config
        import phases
        from tests.test_integration_gates import git
        from tests.test_phase_runs import ONE, PHASE, PHASE_BRANCH
        c = self.case
        c.start()
        found = verify.request(check="unit", task=ONE,
                               stage=c.stage_of(ONE), actor="you")
        self.assertEqual(found["outcome"], "passed", found["why"])
        verified = found["source"]["commit"]
        verify.forget_cached()
        gates.forget_cached()
        cards = phases._cards()[0]
        member = next(m for m in phases._snapshot(cards[PHASE], cards)["members"]
                      if m["file"] == ONE)
        worktree = config.WORKTREES / ONE[:-3]
        original = phases._say
        late: list[str] = []

        def lands(*args, **kwargs):
            if not late:
                (worktree / "late.txt").write_text("late\n", encoding="utf-8")
                git(worktree, "add", "-A")
                git(worktree, "-c", "user.email=a@b", "-c", "user.name=late",
                    "commit", "-q", "-m", "late")
                late.append(git(worktree, "rev-parse", "HEAD").stdout.strip())
            return original(*args, **kwargs)

        self.addCleanup(setattr, phases, "_say", original)
        phases._say = lands
        whole = phases._merge_member(cards[PHASE], member)
        self.assertTrue(late)
        self.assertTrue(phases._contains(verified, PHASE_BRANCH))
        self.assertFalse(phases._contains(late[0], PHASE_BRANCH),
                         "a commit the member gate never read was merged")
        # and nothing says the member is in when only part of it is: not
        # the log (which the lane reads as "merged in"), not the ticker,
        # and not the pass, which must not go on to the next member
        self.assertFalse(whole, "a partial merge read as the whole member")
        number = member["number"]
        log = phases.log_entries(c.text(PHASE))
        self.assertFalse([e for e in log if e.startswith(f"{number} merged into ")],
                         log)
        self.assertTrue([e for e in log if e.startswith(f"{number} partly merged")],
                        log)
        said = c.summaries()
        self.assertFalse([s for s in said if f"merged {number} into" in s], said)
        self.assertTrue([s for s in said if "moved on" in s and late[0][:12] in s],
                        said)

    def test_a_whole_member_merge_still_says_merged(self):
        import phases
        from tests.test_phase_runs import ONE, PHASE
        c = self.case
        c.start()
        found = verify.request(check="unit", task=ONE,
                               stage=c.stage_of(ONE), actor="you")
        self.assertEqual(found["outcome"], "passed", found["why"])
        verify.forget_cached()
        gates.forget_cached()
        cards = phases._cards()[0]
        member = next(m for m in phases._snapshot(cards[PHASE], cards)["members"]
                      if m["file"] == ONE)
        self.assertTrue(phases._merge_member(cards[PHASE], member))
        log = phases.log_entries(c.text(PHASE))
        self.assertTrue([e for e in log
                         if e.startswith(f"{member['number']} merged into ")], log)


class IntendedWritesAreNotAnOsBoundary(GateCase):
    """R5. The plain-language summary says where a run is meant to write
    and, beside it, that nothing at the OS level holds it there."""

    @classmethod
    def setUpClass(cls):
        cls.page = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")

    def between(self, start: str, end: str) -> str:
        at = self.page.index(start)
        return self.page[at:self.page.index(end, at)]

    def test_no_baseline_says_nowhere_else(self):
        for intent, baseline in profiles.BASELINE.items():
            for _, why in baseline["writable"]:
                with self.subTest(intent=intent):
                    self.assertNotIn("nowhere else", why)
                    self.assertIn("not confined", why)

    def test_the_resolved_profile_says_isolation_is_not_enforced(self):
        wt = self.worktree()
        found = profiles.resolve("work", worktree=wt)
        self.assertEqual(found["writable"][0]["enforcement"], "intended")
        isolation = found["isolation"]
        self.assertFalse(isolation["enforced"])
        self.assertIn("filesystem-sandbox", isolation["why"])
        self.assertIn("isolation", profiles.public(found))

    def test_requiring_a_sandbox_still_refuses(self):
        self.declares({"v": 1, "profiles": {
            "work": {"requires": ["filesystem-sandbox"]}}})
        found = profiles.resolve("work", worktree=self.worktree())
        self.assertTrue(found["blockers"])
        self.assertIn("requirement-unsupported",
                      [d["code"] for d in found["diagnostics"]])

    def test_the_sheet_labels_intended_writes_and_os_isolation_apart(self):
        body = self.between("async function profileSheet", "function profileDetail")
        self.assertIn("'Intended writes'", body)
        self.assertIn("'OS isolation'", body)
        self.assertIn("isolationLine(p)", body)
        self.assertNotIn("['Writable'", body)

    def test_the_focus_panel_carries_the_same_qualification(self):
        body = self.page[self.page.index("function permissionsPanel"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("isolationLine(found)", body)

    def test_isolation_reads_the_capability_when_a_record_predates_it(self):
        body = self.between("function isolationLine", "\n}\n")
        self.assertIn("filesystem-sandbox", body)
        self.assertIn("not enforced", body)


from tests.test_review_checkouts import (CARD as REVIEW_CARD,  # noqa: E402
                                         ReviewCase)


class AReviewRootedPassSurvivesItsCheckout(ReviewCase):
    """The seam the review probed by hand (`probe_regressions.py`, which
    imported an archived candidate): a check bench ran in the checkout it
    pinned for a PR review satisfies the gate before *and after* that
    checkout is given back by the ownership-aware release — the binding is
    the reviewed head, not the directory — and stops satisfying it once the
    pull request's head moves. Imported from this checkout."""

    def setUp(self):
        super().setUp()
        import config
        old = config.GATES_DIR
        config.GATES_DIR = self.local / "state" / "gates"
        self.addCleanup(setattr, config, "GATES_DIR", old)
        gates.REVIEW_STATE.clear()
        self.addCleanup(gates.REVIEW_STATE.clear)
        gates.forget_cached()
        self.addCleanup(gates.forget_cached)

    def gated_review_check(self) -> None:
        self.proposes()
        document = json.loads(self.review_check().read_text(encoding="utf-8"))
        document["gates"] = {"acceptance": {"checks": ["unit"]}}
        self.declares(document)
        gates.forget_cached()

    def test_the_gate_reads_the_same_before_and_after_release(self):
        import reviews
        self.gated_review_check()
        record, _ = self.pinned()
        checkout = Path(record["path"])
        found = verify.request(check="unit", agent="run-1", actor="agent")
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(found["root"]["policy"], "review")
        self.assertTrue(found["program"]["bound"])
        before = gates.evaluate(REVIEW_CARD, "acceptance")
        self.assertEqual(before["decision"], "allowed", before["summary"])

        reviews.release("run-1", "the review ended")
        self.assertFalse(checkout.exists(),
                         "the ownership-aware release removed the checkout")
        self.assertTrue((reviews.owned("run-1") or {}).get("released"))
        verify.forget_cached()
        gates.forget_cached()
        after = gates.evaluate(REVIEW_CARD, "acceptance")
        self.assertEqual(after["decision"], "allowed", after["summary"])
        self.assertEqual(after["requirements"][0]["evidence"], found["id"])

    def test_a_head_that_moved_after_release_no_longer_satisfies_it(self):
        import reviews
        self.gated_review_check()
        self.pinned()
        found = verify.request(check="unit", agent="run-1", actor="agent")
        self.assertEqual(found["outcome"], "passed", found["why"])
        reviews.release("run-1", "the review ended")
        reviews.observe(REVIEW_CARD, head="d" * 40, status="OPEN")
        verify.forget_cached()
        gates.forget_cached()
        after = gates.evaluate(REVIEW_CARD, "acceptance")
        self.assertEqual(after["decision"], "blocked")


class TheSheetSaysWhichScope(unittest.TestCase):
    """R1, on the page: the gate row names the coverage it required, so a
    verification chip reading 0/1 beside a gate reading 1/1 is never left
    unexplained."""

    @classmethod
    def setUpClass(cls):
        cls.page = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")

    def between(self, start: str, end: str) -> str:
        at = self.page.index(start)
        return self.page[at:self.page.index(end, at)]

    def test_a_requirement_row_says_full_run_or_its_scope(self):
        body = self.between("function requirementLabel(r)", "function gateRow")
        self.assertIn("r.scope ? 'scope ' + r.scope : 'full run'", body)

    def test_the_gate_row_draws_the_requirement_label(self):
        body = self.between("function gateRow(r)", "function gatePanel")
        self.assertIn("requirementLabel(r)", body)


if __name__ == "__main__":
    unittest.main()
