"""Verification readiness without circular setup gates (task 90).

Five acts — discover, register, run, review, activate — and the promise
under test is that no two of them collapse into one:

- **Discovery is inert.** Candidates come from the project's scripts, CI
  and documentation with a confidence and their prerequisites; nothing is
  run, installed or written, and every candidate says it is an inference.
- **Registration is a draft.** It goes through Settings' own validate and
  preview, never touches a merge rule, and treats preparation, network and
  secrets as consents of their own. Settings' Apply is the only writer.
- **Readiness rests on a person's review of bench's own evidence.** A
  failure, a preparation failure, a cancellation, a denial, uncommitted
  source, stale evidence and an earlier definition each refuse the review
  with nothing written; a completed setup card or an agent's narrative
  proves nothing.
- **Activation is a separate, previewed change** offered only for a ready
  check, which adds to an existing rule and never weakens it.
- **End to end, in a disposable repository:** a project with no tests gets
  a verification setup card, a delivered program is registered, run, seen
  to fail and to pass, reviewed, and only after deliberate activation does
  the merge gate block the unverified commit and allow the verified one.

    python3 -m unittest tests.test_verification_readiness -v
"""

from __future__ import annotations

import json
import stat
import sys
import unittest
from pathlib import Path

from tests.test_setup_guide import Project, by_id, snapshot

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import gates  # noqa: E402
import profiles  # noqa: E402
import readiness  # noqa: E402
import settings  # noqa: E402
import setupguide  # noqa: E402
import setuptasks  # noqa: E402
import verify  # noqa: E402

from tests.test_integration_gates import CARD, GateCase, git  # noqa: E402


def call(p: Project, fn: str, payload: dict) -> dict:
    return p.run(
        "import readiness\n"
        f"try:\n    R = {{'ok': readiness.{fn}({payload!r})}}\n"
        "except setupguide.Refused as e:\n"
        "    R = {'refused': str(e), 'status': e.status, **e.extra}")


def read(p: Project) -> dict:
    return p.run("import readiness\nR = readiness.state()")


def node_project(p: Project) -> None:
    (p.app / "package.json").write_text(json.dumps({
        "scripts": {"test": "vitest run", "lint": "eslint .",
                    "dev": "vite"}}))
    (p.app / "package-lock.json").write_text("{}\n")
    flows = p.app / ".github" / "workflows"
    flows.mkdir(parents=True)
    (flows / "ci.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: npm test\n"
        "      - run: |\n          make check\n          npm run lint && npm run build\n")
    (p.app / "Makefile").write_text("check:\n\techo hi\n")
    (p.app / "README.md").write_text("Run tests:\n\n```\n$ npm test\n```\n")
    (p.app / ".env.example").write_text("DATABASE_URL=\n")


class DiscoveryIsInert(unittest.TestCase):
    def test_candidates_come_from_scripts_ci_and_docs_with_confidence(self):
        p = Project(self)
        node_project(p)
        before = snapshot(p.tool, p.app, p.work)
        found = read(p)
        self.assertEqual(before, snapshot(p.tool, p.app, p.work),
                         "discovery wrote something")
        cands = {c["command"]: c for c in found["discovery"]["candidates"]}
        self.assertIn("npm test", cands)
        npm_test = cands["npm test"]
        self.assertEqual("high", npm_test["confidence"])
        self.assertEqual({"package.json", ".github/workflows/ci.yml",
                          "README.md"},
                         {s["ref"] for s in npm_test["sources"]})
        self.assertIn("make check", cands, "a run: block step is read too")
        self.assertIn("npm run lint", cands)
        compound = cands["npm run lint && npm run build"]
        self.assertFalse(compound["expressible"])
        self.assertIn("wrapper program", compound["expressibleWhy"])
        for c in cands.values():
            self.assertTrue(c["inferred"])
            self.assertFalse(c["executed"])
            self.assertEqual("unproven", c["coverage"])
            self.assertEqual("candidate", c["readiness"])
        needs = {n["kind"]: n for n in npm_test["prerequisites"]}
        self.assertEqual("npm ci", needs["prepare"]["run"])
        self.assertTrue(needs["prepare"]["network"])
        self.assertIn("never production", needs["secrets"]["why"])
        self.assertIn("ran nothing", found["discovery"]["inert"])
        self.assertIn("unproven", found["discovery"]["inert"])

    def test_a_maintained_entrypoint_is_a_high_confidence_candidate(self):
        p = Project(self)
        (p.app / "verify").mkdir()
        tool = p.app / "verify" / "backend"
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
        (p.app / "verify" / "notes.txt").write_text("not a program\n")
        cands = {c["command"]: c for c in read(p)["discovery"]["candidates"]}
        self.assertEqual(["sh verify/backend"], list(cands))
        self.assertEqual("high", cands["sh verify/backend"]["confidence"])
        self.assertTrue(cands["sh verify/backend"]["expressible"])

    def test_a_project_without_tests_offers_nothing_and_reads_incomplete(self):
        p = Project(self)
        found = read(p)
        self.assertEqual([], found["discovery"]["candidates"])
        boot = found["bootstrap"]
        self.assertEqual("incomplete", boot["condition"])
        self.assertIn("Automated verification is incomplete", boot["headline"])
        self.assertIn("human review", boot["headline"])
        self.assertFalse(boot["changesGates"])
        guide = by_id(p.read())["verification"]
        self.assertIn("Automated verification is incomplete",
                      " ".join(f["value"] for f in guide["facts"]))
        self.assertNotEqual("tested", guide["status"])
        self.assertIn("verificationReadiness", p.read())

    def test_the_cli_reads_the_same_state_and_writes_nothing(self):
        p = Project(self)
        node_project(p)
        before = snapshot(p.tool, p.app, p.work)
        out = p.cli("--verification", "--json")
        self.assertEqual(0, out.returncode, out.stderr)
        found = json.loads(out.stdout)
        self.assertEqual(readiness.KIND, found["kind"])
        self.assertEqual(before, snapshot(p.tool, p.app, p.work))
        text = p.cli("--verification")
        self.assertIn("inferred, never run", text.stdout)


class RegistrationIsADraft(unittest.TestCase):
    def setUp(self):
        self.p = Project(self, policy={
            "v": 1, "checks": [{"name": "existing", "run": "python3 -V",
                                "root": "worktree"}],
            "profiles": {"work": {"verify": ["existing"]}},
            "gates": {"acceptance": {"checks": ["existing"]}}})
        node_project(self.p)
        self.cands = {c["command"]: c for c in
                      read(self.p)["discovery"]["candidates"]}

    def test_a_draft_writes_nothing_and_never_touches_a_merge_rule(self):
        before = snapshot(self.p.tool, self.p.app, self.p.work)
        found = call(self.p, "register",
                     {"candidate": self.cands["npm test"]["id"]})["ok"]
        self.assertEqual(before, snapshot(self.p.tool, self.p.app, self.p.work))
        self.assertTrue(found["wroteNothing"])
        self.assertTrue(found["valid"], found["errors"])
        self.assertEqual({"acceptance": {"checks": ["existing"]}},
                         found["document"]["gates"],
                         "the pre-existing rule is preserved, unchanged")
        self.assertNotIn("prepare", found["check"],
                         "preparation is its own consent")
        kinds = {c["kind"] for c in found["changes"]}
        self.assertIn("check-added", kinds)
        self.assertNotIn("gate-changed", kinds)
        self.assertIn("requires it nowhere", found["afterApply"])

    def test_network_preparation_is_a_separate_consent(self):
        cand = self.cands["npm test"]["id"]
        refused = call(self.p, "register", {"candidate": cand, "prepare": True})
        self.assertEqual(409, refused["status"])
        self.assertEqual("network-consent", refused["code"])
        found = call(self.p, "register", {"candidate": cand, "prepare": True,
                                          "network": True})["ok"]
        self.assertEqual(["node-deps"], found["check"]["prepare"])
        step = found["document"]["prepare"][0]
        self.assertEqual({"name": "node-deps", "run": "npm ci",
                          "network": True}, step)

    def test_compound_unknown_and_duplicate_are_refused(self):
        self.assertEqual("compound", call(self.p, "register", {
            "candidate": self.cands["npm run lint && npm run build"]["id"]})["code"])
        self.assertEqual(404, call(self.p, "register",
                                   {"candidate": "cand-000000000000"})["status"])
        self.assertEqual("duplicate", call(self.p, "register", {
            "command": "python3 -V", "name": "existing"})["code"])
        self.assertIn("never in", call(self.p, "register", {
            "command": "python3 -V", "name": "x", "root": "app"})["refused"])

    def test_applying_the_draft_registers_an_optional_check_only(self):
        self.assertEqual("relative", call(self.p, "register", {
            "command": "./verify/tests", "name": "delivered"})["code"])
        found = call(self.p, "register", {"command": "sh verify/tests",
                                          "name": "delivered"})["ok"]
        applied = self.p.run(
            "import settings\n"
            f"R = settings.apply({{'document': {found['document']!r}, "
            f"'revision': {found['revision']!r}}})")
        self.assertTrue(applied["revision"], applied)
        states = {c["name"]: c for c in read(self.p)["checks"]}
        self.assertEqual("registered", states["delivered"]["readiness"])
        self.assertEqual("not-run", states["delivered"]["run"])
        self.assertEqual([], states["delivered"]["required"])
        self.assertEqual(["acceptance"], states["existing"]["required"])


# ── review and activation over real evidence, in-process ──────────────

FIXTURE_CHECK = """#!{python}
import pathlib, sys
text = pathlib.Path("code.txt").read_text()
if "broken" in text:
    print("1 test failed: code.txt is broken")
    sys.exit(1)
print("ran 2 tests — OK")
"""

PREP_FAILS = """#!{python}
import sys
print("could not install")
sys.exit(1)
"""


class ReadinessCase(GateCase):
    def setUp(self):
        super().setUp()
        self.patch(STATE=self.state)
        self.wt = self.worktree()
        self.program = self.executable("unit", FIXTURE_CHECK)
        self.declares({"v": 1,
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree"}],
                       "profiles": {"work": {"verify": ["unit"]}}})

    def run_at(self, text: str) -> dict:
        self.commit_in(self.wt, text)
        verify.forget_cached()
        self.manifest()
        return self.ask()

    def review(self, **payload) -> dict:
        payload.setdefault("check", "unit")
        payload.setdefault("note", "read the program and both runs")
        payload.setdefault("request", "review-0001")
        payload.setdefault("expected", setupguide.revision())
        return readiness.review(payload)

    def refused(self, **payload) -> setupguide.Refused:
        before = setupguide.revision()
        with self.assertRaises(setupguide.Refused) as caught:
            self.review(**payload)
        self.assertEqual(before, setupguide.revision(), "a refusal wrote")
        return caught.exception

    def state_of(self, name: str = "unit") -> dict:
        return {c["name"]: c for c in readiness.check_states()}[name]


class ReviewRestsOnCurrentEvidence(ReadinessCase):
    def test_a_registered_check_is_not_ready_and_a_pass_is_only_exercised(self):
        self.assertEqual("registered", self.state_of()["readiness"])
        passed = self.run_at("fine\n")
        self.assertEqual("passed", passed["outcome"])
        found = self.state_of()
        self.assertEqual("exercised", found["readiness"])
        self.assertEqual("passed", found["run"])
        self.assertEqual("incomplete", readiness.bootstrap()["condition"])

    def test_a_failure_with_no_pass_cannot_be_reviewed(self):
        failed = self.run_at("broken\n")
        self.assertEqual("failed", failed["outcome"])
        self.assertEqual("attention", self.state_of()["readiness"])
        err = self.refused(passEvidence=failed["id"], failEvidence=failed["id"])
        self.assertEqual("not-passed", err.extra["code"])

    def test_a_pass_and_a_controlled_failure_make_it_ready(self):
        failed = self.run_at("broken\n")
        passed = self.run_at("fine\n")
        result = self.review(passEvidence=passed["id"],
                             failEvidence=failed["id"])
        self.assertEqual("ready", result["readiness"])
        self.assertFalse(result["changesGates"])
        found = self.state_of()
        self.assertEqual("ready", found["readiness"])
        self.assertEqual(readiness.bootstrap()["ready"], ["unit"])
        # binding: the check's and program's identities, the commits
        entry = setupguide.read_decisions()["doc"]["verification"]["checks"]["unit"]
        self.assertEqual(profiles.document_checks(
            profiles.document(), {})[0]["unit"]["checkId"], entry["checkId"])
        self.assertTrue(entry["programId"].startswith("prog:"))
        self.assertEqual(passed["source"]["commit"][:12], entry["passCommit"])
        self.assertNotIn(str(self.tmp), json.dumps(entry),
                         "the portable record carries no path")
        # readiness is not merge permission: nothing is gated
        self.assertEqual("ungated", gates.evaluate(CARD, "acceptance")["decision"])
        # a replay answers, it does not act twice
        again = self.review(passEvidence=passed["id"], failEvidence=failed["id"],
                            expected="anything")
        self.assertTrue(again["duplicate"])

    def test_a_preparation_failure_is_not_a_controlled_failure(self):
        prep = self.executable("deps", PREP_FAILS)
        self.declares({"v": 1, "prepare": [{"name": "deps", "run": str(prep)}],
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree", "prepare": ["deps"]}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        broken = self.run_at("fine\n")
        self.assertEqual("prepare-failed", broken["outcome"])
        self.assertEqual("attention", self.state_of()["readiness"])
        # restore a working definition, pass, and try to cite the prep failure
        self.declares({"v": 1,
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree"}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        passed = self.run_at("fine again\n")
        err = self.refused(passEvidence=passed["id"], failEvidence=broken["id"])
        self.assertEqual("definition-changed", err.extra["code"],
                         "the prep failure ran an earlier definition")

    def test_a_cancelled_run_is_not_a_controlled_failure(self):
        passed = self.run_at("fine\n")
        record = dict(passed, outcome="cancelled", id=None)
        record["id"] = passed["id"].replace(passed["id"][-4:], "beef")
        verify._store(record)
        err = self.refused(passEvidence=passed["id"], failEvidence=record["id"])
        self.assertEqual("not-failed", err.extra["code"])
        self.assertIn("reached no verdict", str(err))

    def test_stale_evidence_cannot_support_a_review(self):
        failed = self.run_at("broken\n")
        passed = self.run_at("fine\n")
        # source drift: the checkout moves on, the pass goes stale
        self.commit_in(self.wt, "fine, later\n")
        verify.forget_cached()
        err = self.refused(passEvidence=passed["id"], failEvidence=failed["id"])
        self.assertEqual("not-passed", err.extra["code"])
        self.assertEqual("stale", err.extra["outcome"])

    def test_a_pass_over_uncommitted_work_cannot_support_a_review(self):
        failed = self.run_at("broken\n")
        (self.wt / "code.txt").write_text("fine but uncommitted\n")
        self.manifest()
        passed = self.ask()
        self.assertEqual("passed", passed["outcome"])
        err = self.refused(passEvidence=passed["id"], failEvidence=failed["id"])
        self.assertIn(err.extra["code"], ("source-unbound", "not-passed"))

    def test_a_review_needs_a_failure_or_a_reason_and_a_note(self):
        passed = self.run_at("fine\n")
        self.assertEqual("no-failure",
                         self.refused(passEvidence=passed["id"]).extra["code"])
        self.refused(passEvidence=passed["id"], failingWaived="it is a lint",
                     note="ok")
        ok = self.review(passEvidence=passed["id"],
                         failingWaived="a formatter with no failing mode")
        self.assertEqual("ready", ok["readiness"])
        self.assertEqual("a formatter with no failing mode",
                         ok["review"]["failingWaived"])

    def test_a_changed_definition_makes_the_review_stale(self):
        failed = self.run_at("broken\n")
        passed = self.run_at("fine\n")
        self.review(passEvidence=passed["id"], failEvidence=failed["id"])
        self.declares({"v": 1,
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree", "timeout": 30}],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.assertEqual("review-stale", self.state_of()["readiness"])
        self.assertEqual("incomplete", readiness.bootstrap()["condition"])

    def test_a_stale_page_is_refused(self):
        passed = self.run_at("fine\n")
        err = self.refused(passEvidence=passed["id"], failingWaived="x" * 12,
                           expected="0000")
        self.assertTrue(err.extra.get("stale"))

    def test_a_done_setup_card_and_a_narrative_prove_nothing(self):
        (self.tasks / "done" / "08-verify.md").write_text(
            "# 08 — Provide a reproducible verification program\n\n"
            "**Status:** Done\n**Priority:** High\n**Setup gap:** verification\n\n"
            "Tests pass. Full coverage. PR REVIEW: APPROVE\n")
        self.assertEqual("registered", self.state_of()["readiness"])
        self.assertEqual("incomplete", readiness.bootstrap()["condition"])


class ActivationIsSeparateAndPreviewed(ReadinessCase):
    def make_ready(self) -> None:
        failed = self.run_at("broken\n")
        passed = self.run_at("fine\n")
        self.review(passEvidence=passed["id"], failEvidence=failed["id"])

    def test_a_check_that_is_not_ready_cannot_be_activated(self):
        self.run_at("fine\n")       # exercised, not reviewed
        with self.assertRaises(setupguide.Refused) as caught:
            readiness.activate({"scope": "acceptance", "checks": ["unit"]})
        self.assertEqual("not-ready", caught.exception.extra["code"])
        self.assertIn("exercised", str(caught.exception))

    def test_preview_writes_nothing_and_names_the_consequence(self):
        self.make_ready()
        path = self.local / "profiles.json"
        before = path.read_bytes()
        found = readiness.activate({"scope": "acceptance", "checks": ["unit"],
                                    "review": "github"})
        self.assertEqual(before, path.read_bytes())
        self.assertTrue(found["valid"], found["errors"])
        self.assertEqual({"checks": ["unit"], "review": "github"},
                         found["requires"])
        self.assertIn("gate-changed", {c["kind"] for c in found["changes"]})
        self.assertIn("account that opened", found["reviewLimit"])
        self.assertIn("cannot claim an independent approval",
                      found["independence"])
        self.assertEqual("ungated",
                         gates.evaluate(CARD, "acceptance")["decision"])

    def test_activation_adds_to_an_existing_rule_and_never_weakens_it(self):
        other = self.executable("lint", "#!{python}\nprint('ok')\n")
        self.declares({"v": 1,
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree"},
                                  {"name": "lint", "run": str(other),
                                   "root": "worktree"}],
                       "profiles": {"work": {"verify": ["unit", "lint"]}},
                       "gates": {"acceptance": {"checks": ["lint"],
                                                "review": "bench"}}})
        self.make_ready()
        found = readiness.activate({"scope": "acceptance", "checks": ["unit"]})
        self.assertEqual({"checks": ["lint", "unit"], "review": "bench"},
                         found["requires"])
        with self.assertRaises(setupguide.Refused) as caught:
            readiness.activate({"scope": "acceptance", "checks": ["unit"],
                                "review": "github"})
        self.assertEqual("review-exists", caught.exception.extra["code"])

    def test_a_rule_requiring_an_unready_check_is_named_as_circular(self):
        self.declares({"v": 1,
                       "checks": [{"name": "unit", "run": str(self.program),
                                   "root": "worktree"}],
                       "profiles": {"work": {"verify": ["unit"]}},
                       "gates": {"acceptance": {"checks": ["unit"]}}})
        boot = readiness.bootstrap()
        self.assertEqual("incomplete", boot["condition"])
        self.assertEqual([{"scope": "acceptance", "checks": ["unit"],
                           "review": "none"}], boot["preserved"])
        self.assertEqual("unit", boot["circular"][0]["check"])
        self.assertIn("enables no override",
                      " ".join(boot["circular"][0]["waysThrough"]))
        self.assertEqual("blocked",
                         gates.evaluate(CARD, "acceptance")["decision"],
                         "the bootstrap condition relaxes nothing")


class EndToEndInADisposableRepository(ReadinessCase):
    """A project with no tests, a verification setup card, a delivered
    program, and a merge rule activated only after the program was
    reviewed — every root a temporary directory."""

    def setUp(self):
        super().setUp()
        self.declares({"v": 1})        # start with nothing registered

    def test_no_tests_to_a_required_check_that_blocks_then_allows(self):
        # 1. nothing to discover; a verification gap is proposed
        self.assertEqual([], readiness.discover()["candidates"])
        self.assertIn("verification", setuptasks.gaps()["gaps"])
        self.assertEqual("incomplete", readiness.bootstrap()["condition"])

        # 2. the setup card's branch delivers a program
        (self.wt / "check.py").write_text(FIXTURE_CHECK.format(
            python=sys.executable))
        (self.wt / "check.py").chmod(0o755)
        git(self.wt, "add", "check.py")
        git(self.wt, "commit", "-q", "-m", "a verification program")

        # 3. registration is a draft until Settings applies it
        draft = readiness.register({"command": str(self.program),
                                    "name": "unit"})
        self.assertTrue(draft["valid"], draft["errors"])
        self.assertNotIn("gates", draft["document"])
        applied = settings.apply({"document": draft["document"],
                                  "revision": draft["revision"]})
        self.assertTrue(applied, applied)
        gates.forget_cached()
        self.assertEqual("registered", self.state_of()["readiness"])
        self.assertEqual("ungated", gates.evaluate(CARD, "acceptance")["decision"])

        # 4. run it — a controlled failure, then a pass — and review it
        failed = self.run_at("broken\n")
        passed = self.run_at("fine\n")
        self.assertEqual(("failed", "passed"),
                         (failed["outcome"], passed["outcome"]))
        self.assertEqual("ungated", gates.evaluate(CARD, "acceptance")["decision"],
                         "a passing run activates nothing")
        self.review(passEvidence=passed["id"], failEvidence=failed["id"])
        self.assertEqual("ungated", gates.evaluate(CARD, "acceptance")["decision"],
                         "a review activates nothing")

        # 5. deliberate activation, previewed then applied
        plan = readiness.activate({"scope": "acceptance", "checks": ["unit"]})
        settings.apply({"document": plan["document"],
                        "revision": plan["revision"]})
        gates.forget_cached()
        verify.forget_cached()
        # the policy changed, so the earlier pass no longer speaks for it
        found = gates.evaluate(CARD, "acceptance")
        self.assertEqual("blocked", found["decision"])
        # a stale pass supersedes nothing, so the reading falls to the
        # earlier controlled failure — unmet either way, never a pass
        self.assertIn(found["unmet"][0]["reason"], ("stale", "failed"),
                      found["summary"])
        self.manifest()
        self.assertEqual("passed", self.ask()["outcome"])
        gates.forget_cached()
        self.assertEqual("allowed", gates.evaluate(CARD, "acceptance")["decision"],
                         "the verified head may merge")

        # 6. an unverified commit is blocked; verifying it allows it again
        self.commit_in(self.wt, "fine, but nobody checked\n")
        verify.forget_cached()
        gates.forget_cached()
        found = gates.evaluate(CARD, "acceptance")
        self.assertEqual("blocked", found["decision"])
        with self.assertRaises(Exception):
            gates.require(found)
        self.manifest()
        self.assertEqual("passed", self.ask()["outcome"])
        gates.forget_cached()
        self.assertEqual("allowed", gates.evaluate(CARD, "acceptance")["decision"])

        # 7. a failing head is blocked too
        self.commit_in(self.wt, "broken again\n")
        self.manifest()
        self.assertEqual("failed", self.ask()["outcome"])
        gates.forget_cached()
        self.assertEqual("blocked", gates.evaluate(CARD, "acceptance")["decision"])


if __name__ == "__main__":
    unittest.main()
