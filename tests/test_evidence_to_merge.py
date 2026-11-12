"""Card 94's evidence-to-merge regressions, kept once each is fixed.

`tests/repro_94_evidence_to_merge.py` pins the behaviour the #94 design
changes; as each implementation card lands, its case moves here with the
assertion inverted (design §15.3). Every case runs against a disposable
tool install, work root and application checkout (`GateCase`), never a
live board.

Card 95 (design §15.1 step 1) — an override needs a real target:

- E11 / R5 — no implementation commit, or one application git cannot
  read: the override is refused, nothing is persisted, and a direct HTTP
  call cannot get past it.
- `overrides.eligible` is the backend's answer, and the sheet draws it.
- E12 — an override at H ceases to apply at a new head, or when what the
  scope requires changes; a stale page cannot record one for another head.
- E13 — the merge still revalidates against the exact commit under an
  override; `--match-head-commit` stays covered in
  `tests/test_gate_remediation.py`.

Card 96 (design §15.1 step 2) — relevance before precedence:

- E4 / R2 — `TheSafer36Shape`: a stale pass at H and an older cancellation
  at G cite H and the pass's own stale reason, never G.
- E5 — `Precedence`: every §6.2 row against `gates._check_requirement`,
  plus legacy stale handling, deterministic order and unknown-head
  refusal; `TheEquivalentBranchIsHeldToTheSameFilters` holds the (not yet
  enabled, #104) declared-equivalent branch to the same filters.
- §6.2's last row — `FindingsAreNotTheChecksGate`.
- E13 — `TheMergeRevalidatesTheEvidence`: a push between the reading and
  the merge is refused at the mutation.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.test_integration_gates import (CARD, FAILS, HANGS, PASSES,  # noqa: E402
                                          STEM, GateCase, card, git,
                                          wait_for)

import gates  # noqa: E402  (on sys.path once the case module is imported)
import github  # noqa: E402
import httpd  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

REASON = "the check runner is down, demo at 5"
NO_TARGET = ("there is no implementation commit to override at, so nothing "
             "was recorded")


class OverrideCase(GateCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "istos"

    def nothing_written(self) -> None:
        self.assertEqual(gates.overrides_for(CARD), [])
        self.assertEqual(list((self.state / "gates").glob("*.overrides.json"))
                         if (self.state / "gates").is_dir() else [], [])


# ── E11 / R5: no target, no record ─────────────────────────────────────


class AnOverrideNeedsATarget(OverrideCase):

    def test_no_branch_is_refused_and_nothing_is_written(self):
        """R5 inverted: the card has no implementation commit at all."""
        self.gated(overrides={"allow": True})
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertIsNone(found["head"]["commit"])
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_an_unreadable_target_is_refused_too(self):
        """A branch application git cannot answer for is not a target: the
        tips read comes back empty, as it does for a broken repository."""
        self.worktree()
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        self.addCleanup(setattr, gates, "_tips", gates._tips)
        gates._tips = lambda: {}
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_a_stale_head_against_no_target_is_still_no_target(self):
        """A page that saw a commit, then the branch went away: the refusal
        is the specific one, not "the head moved to nothing"."""
        self.gated(overrides={"allow": True})
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON,
                           head="0" * 40)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_the_reading_says_it_is_not_eligible_and_why(self):
        self.gated(overrides={"allow": True})
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "no-target")
        self.assertIn("no implementation commit", over["ineligible"]["why"])

    def test_the_sheet_payload_carries_the_backends_answer(self):
        self.gated(overrides={"allow": True})
        scope = httpd.gates_payload(CARD, "in-progress")["scopes"][0]
        self.assertFalse(scope["overrides"]["eligible"])
        self.assertEqual(scope["overrides"]["ineligible"]["code"], "no-target")

    def test_a_valid_target_is_eligible_and_records(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        payload = httpd.gates_payload(CARD, "in-progress")
        self.assertTrue(payload["scopes"][0]["overrides"]["eligible"])
        self.assertIsNone(payload["scopes"][0]["overrides"]["ineligible"])
        record = gates.override(CARD, "acceptance", actor="istos",
                                reason=REASON)["override"]
        self.assertTrue(record["head"])
        self.assertTrue(record["scopePolicy"])

    def test_eligibility_names_the_actor_the_policy_refuses(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True, "actors": ["elena"]})
        # actor-agnostic on the board's reading …
        self.assertTrue(self.decide()["overrides"]["eligible"])
        # … and specific to this board's identity on the sheet
        scope = httpd.gates_payload(CARD, "in-progress")["scopes"][0]
        self.assertFalse(scope["overrides"]["eligible"])
        self.assertEqual(scope["overrides"]["ineligible"]["code"],
                         "actor-not-named")

    def test_a_board_without_overrides_says_so(self):
        self.commit_in(self.worktree())
        self.gated()
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "not-allowed")

    def test_an_override_in_force_is_not_offered_again(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "in-force")


class TheRouteCannotBeBypassed(OverrideCase):
    """`/api/gates/override` on a real socket: the path a stale page, or a
    script, takes."""

    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.url + "/api/gates/override",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_no_target_is_refused_over_http(self):
        self.gated(overrides={"allow": True})
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": "f" * 40})
        self.assertEqual(status, 409)
        self.assertIn(NO_TARGET, body["error"])
        self.nothing_written()

    def test_a_request_naming_no_head_is_refused(self):
        """Without a head a direct call would authorise whatever the branch
        holds when it lands — the stale-page case with the check removed."""
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        for head in (None, "", "   "):
            with self.subTest(head=head):
                payload = {"file": CARD, "scope": "acceptance",
                           "reason": REASON}
                if head is not None:
                    payload["head"] = head
                status, body = self.post(payload)
                self.assertEqual(status, 400)
                self.assertIn("nothing was recorded", body["error"])
        self.nothing_written()

    def test_a_stale_page_cannot_record_for_another_head(self):
        wt = self.worktree()
        seen = self.commit_in(wt)
        self.gated(overrides={"allow": True})
        self.commit_in(wt, "pushed while the sheet was open\n")
        gates.forget_cached()
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": seen})
        self.assertEqual(status, 409)
        self.assertIn("the head moved while you were looking", body["error"])
        self.nothing_written()

    def test_an_explicit_valid_override_is_recorded(self):
        head = self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": head})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["override"]["head"], head)
        self.assertEqual(body["override"]["actor"], "istos")
        self.assertEqual(body["gate"]["decision"], "overridden")


# ── E12: an override authorises one commit, under one rule ─────────────


class AnOverrideIsBoundToItsHeadAndRule(OverrideCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)

    def test_a_new_head_is_not_covered(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        self.commit_in(self.wt, "a new head\n")
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertTrue(found["overrides"]["eligible"],
                        "the new head is a new question, and may be asked")

    def test_a_change_to_what_the_scope_requires_withdraws_it(self):
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        # the required check is redefined: the rule the person proceeded
        # over is not the rule in force now
        document["checks"][0]["run"] = str(self.executable("unit-v2", FAILS))
        self.declares(document)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertIsNone(found["overrides"]["applied"])

    def test_restricting_who_may_override_withdraws_it(self):
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        document["gates"]["overrides"] = {"allow": True, "actors": ["elena"]}
        self.declares(document)
        self.assertEqual(self.decide()["decision"], "blocked")

    def test_an_unrelated_policy_edit_does_not(self):
        """Only what this scope requires is bound: another scope's rule or
        an approved context file is not a decision about this gate."""
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        (self.work / "reference" / "mock.md").write_text("x\n", encoding="utf-8")
        document["context"] = [{"name": "mock", "path": "work:reference/mock.md",
                                "required": False}]
        document["gates"]["member"] = {"checks": ["unit"]}
        self.declares(document)
        self.assertEqual(self.decide()["decision"], "overridden")

    def test_an_existing_record_keeps_its_meaning(self):
        """A record written before the scope binding existed has no
        `scopePolicy`, and is read exactly as it was: bound to its head and
        its gates."""
        self.gated(overrides={"allow": True})
        legacy = {"v": 1, "id": "ovr:legacy00001", "task": CARD,
                  "scope": "acceptance", "actor": "istos", "reason": REASON,
                  "head": self.head, "headRef": f"task/{STEM}",
                  "gates": ["unit"], "unmet": [], "policyId": "gates:old",
                  "policyRevision": "old", "recorded": 1.0}
        path = gates._path(CARD, "overrides")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([legacy]), encoding="utf-8")
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "overridden")
        self.assertEqual(found["overrides"]["applied"]["id"], "ovr:legacy00001")

    def test_an_override_forges_nothing(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        found = self.decide()
        self.assertEqual(found["requirements"][0]["state"], "overridden")
        self.assertEqual(found["requirements"][0]["reason"], "not-run")
        self.assertEqual(verify.for_task(CARD), [])
        self.assertNotEqual(found["decision"], "allowed")


# ── E13: the merge revalidates against the exact commit ────────────────


class TheMergeRevalidatesUnderAnOverride(OverrideCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)
        (self.tasks / "review" / CARD).write_text(
            card("71 — Gate integration", status="Review"), encoding="utf-8")
        (self.tasks / "in-progress" / CARD).unlink()

    def test_an_override_at_h_lets_h_merge(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", self.head,
                   "main").returncode)

    def test_a_push_after_the_override_is_refused_at_the_merge(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        landed: list[str] = []
        original = github._assert_lands_in_the_base

        def push_lands(filename, target):
            original(filename, target)
            if not landed:
                landed.append(self.commit_in(self.wt, "a late push\n"))
                verify.forget_cached()
                gates.forget_cached()

        self.addCleanup(setattr, github, "_assert_lands_in_the_base", original)
        github._assert_lands_in_the_base = push_lands
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertNotEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", landed[0],
                   "main").returncode,
            "the commit nobody overrode must not reach the base")
        self.assertEqual(self.stage_of(CARD), "review")


# ── card 96 (design §15.1 step 2): relevance before precedence ─────────


H = "a" * 40          # the commit a gate would merge
G = "b" * 40          # an older head of the same card


def record(ident: str, outcome: str, *, at: str | None = H, when: float,
           check: str = "unit", scope: str | None = None, dirty=(),
           bound: bool = True, stale: str | None = None,
           ended: float | None = None, **extra) -> dict:
    """One evidence record in the shape `verify` writes. `stale` names the
    freshness reason `outcome_now` reads off it, as a legacy record whose
    binding has gone would."""
    found = {"id": f"ev:{ident}", "task": CARD, "check": check,
             "scope": scope, "outcome": outcome, "requested": when,
             "started": when, "ended": ended if ended is not None else when + 1,
             "source": ({"commit": at, "bound": bound, "dirty": list(dirty)}
                        if at else None),
             "root": {"policy": "worktree"}, "_stale": stale}
    found.update(extra)
    return found


class Precedence(unittest.TestCase):
    """Every row of the design's §6.2 table (E5) against `_check_requirement`
    itself, with `outcome_now` reading the record's own `_stale` marker —
    so each row is the precedence and nothing but the precedence."""

    def setUp(self):
        self.read: list[str] = []

        def outcome_now(rec):
            self.read.append(rec.get("id"))
            if rec.get("_stale") and rec.get("outcome") == "passed":
                return {"outcome": "stale", "reason": rec["_stale"],
                        "why": f"it went stale: {rec['_stale']}",
                        "freshness": {"reasons": [rec["_stale"]]},
                        "recorded": "passed"}
            return {"outcome": rec.get("outcome"), "reason": None,
                    "why": None, "freshness": {"reasons": []},
                    "recorded": rec.get("outcome")}

        self.addCleanup(setattr, verify, "outcome_now", verify.outcome_now)
        verify.outcome_now = outcome_now

    def decide(self, records, *, head: str | None = H, scope=None,
               check: str = "unit") -> dict:
        return gates._check_requirement(
            CARD, {"name": check, "gateScope": scope, "run": "x"},
            {"commit": head}, records)

    # R2 / E4, in miniature (the real fixture is `TheSafer36Shape` below)
    def test_r2_a_stale_pass_at_h_is_the_reason_not_gs_cancellation(self):
        found = self.decide([
            record("old", "cancelled", at=G, when=1),
            record("new", "passed", at=H, when=2, stale="policy-changed")])
        self.assertEqual(found["reason"], "stale")
        self.assertEqual(found["staleReason"], "policy-changed")
        self.assertEqual(found["commit"], H)
        self.assertEqual(found["evidence"], "ev:new")
        self.assertIn(H[:12], found["why"])
        self.assertNotIn(G[:12], found["why"])
        self.assertEqual(found["otherCommits"], 1)

    def test_a_newer_failure_at_h_beats_its_earlier_pass(self):
        found = self.decide([record("p", "passed", when=1),
                             record("f", "failed", when=2)])
        self.assertEqual((found["state"], found["reason"]), ("unmet", "failed"))
        self.assertEqual(found["evidence"], "ev:f")
        self.assertIn("newer than the pass on the same commit", found["why"])

    def test_a_running_rerun_at_h_is_never_hidden(self):
        for live in ("running", "preparing"):
            with self.subTest(live=live):
                found = self.decide([record("p", "passed", when=1),
                                     record("r", live, when=2)])
                self.assertEqual(found["state"], "pending")
                self.assertEqual(found["evidence"], "ev:r")

    def test_a_request_not_yet_bound_to_a_source_still_holds_it(self):
        found = self.decide([record("p", "passed", when=1),
                             record("q", "preparing", at=None, when=2)])
        self.assertEqual(found["state"], "pending")

    def test_a_rerun_running_on_another_commit_is_history(self):
        found = self.decide([record("r", "running", at=G, when=2)])
        self.assertEqual(found["reason"], "other-commit")

    def test_a_cancellation_after_a_pass_at_h_does_not_resurrect_it(self):
        found = self.decide([record("p", "passed", when=1),
                             record("c", "cancelled", when=2)])
        self.assertEqual(found["reason"], "cancelled")
        self.assertEqual(found["commit"], H)
        self.assertIn(f"cancelled before a result on {H[:12]}", found["why"])

    def test_a_failure_on_another_head_is_history(self):
        found = self.decide([record("f", "failed", at=G, when=1)])
        self.assertEqual(found["reason"], "other-commit")
        self.assertIsNone(found["evidence"])
        self.assertIsNone(found["commit"])
        self.assertEqual(found["elsewhere"]["commit"], G)
        self.assertIn(f"has not run on {H[:12]}", found["why"])
        self.assertEqual(found["history"][0]["evidence"], "ev:f")

    def test_a_pass_on_another_head_is_named_and_not_cherry_picked(self):
        found = self.decide([record("p", "passed", at=G, when=1),
                             record("f", "failed", at=G, when=2)])
        self.assertEqual(found["state"], "unmet")
        self.assertEqual(found["reason"], "other-commit")
        self.assertIn(f"It passed on {G[:12]}", found["why"])
        self.assertIn("reads failed", found["why"])
        self.assertEqual(found["elsewhere"]["passedOn"], G)

    def test_a_changed_definition_gives_its_own_reason(self):
        found = self.decide([record("p", "passed", when=1,
                                    stale="policy-changed")])
        self.assertEqual(found["reason"], "stale")
        self.assertEqual(found["staleReason"], "policy-changed")

    def test_a_stale_pass_is_not_skipped_for_an_older_failure(self):
        """Legacy stale evidence is an answer, not a gap: the failure it
        superseded at the same head does not become the reason."""
        found = self.decide([record("f", "failed", when=1),
                             record("p", "passed", when=2,
                                    stale="context-changed")])
        self.assertEqual(found["reason"], "stale")
        self.assertEqual(found["evidence"], "ev:p")

    def test_a_narrow_pass_never_covers_the_full_run(self):
        found = self.decide([record("n", "passed", when=1, scope="fast")])
        self.assertEqual(found["reason"], "not-run")
        self.assertIn("narrower scopes ran (fast)", found["why"])

    def test_a_narrow_stale_pass_leaves_an_older_covering_pass_standing(self):
        found = self.decide([record("p", "passed", when=1),
                             record("n", "passed", when=2, scope="fast",
                                    stale="source-changed")])
        self.assertEqual(found["state"], "met")
        self.assertEqual(found["evidence"], "ev:p")

    def test_a_full_pass_covers_a_scoped_requirement(self):
        found = self.decide([record("p", "passed", when=1)], scope="fast")
        self.assertEqual(found["state"], "met")

    def test_a_denial_ran_nothing_and_supersedes_nothing(self):
        found = self.decide([record("p", "passed", when=1),
                             record("d", "denied", at=None, when=2)])
        self.assertEqual(found["state"], "met")
        alone = self.decide([record("d", "denied", at=None, when=2)])
        self.assertEqual(alone["reason"], "denied")

    def test_a_dirty_pass_does_not_hide_a_genuine_failure_at_h(self):
        found = self.decide([record("f", "failed", when=1),
                             record("p", "passed", when=2, dirty=["x.txt"])])
        self.assertEqual(found["reason"], "failed")
        alone = self.decide([record("p", "passed", when=2, dirty=["x.txt"])])
        self.assertEqual(alone["reason"], "source-dirty")
        unbound = self.decide([record("u", "passed", when=2, bound=False)])
        self.assertEqual(unbound["reason"], "source-unbound")

    def test_an_unknown_head_is_refused_before_any_record_is_read(self):
        found = self.decide([record("p", "passed", when=1),
                             record("c", "cancelled", at=G, when=2)],
                            head=None)
        self.assertEqual(found["reason"], "head-unknown")
        self.assertIsNone(found["evidence"])
        self.assertEqual(self.read, [], "no reason was computed and discarded")

    def test_the_order_is_deterministic(self):
        """Request time, then end time, then a non-pass on a tie — never the
        order the files were listed in."""
        tie = [record("p", "passed", when=1, ended=5),
               record("f", "failed", when=1, ended=5)]
        for records in (tie, list(reversed(tie))):
            self.assertEqual(self.decide(records)["evidence"], "ev:f")
        later_end = [record("f", "failed", when=1, ended=4),
                     record("p", "passed", when=1, ended=5)]
        for records in (later_end, list(reversed(later_end))):
            self.assertEqual(self.decide(records)["state"], "met")

    def test_nothing_on_record_is_not_run(self):
        found = self.decide([])
        self.assertEqual(found["reason"], "not-run")
        self.assertIn("an absent record is not a pass", found["why"])


class TheEquivalentBranchIsHeldToTheSameFilters(Precedence):
    """A declared equivalent (#104) goes through every filter the direct
    branch has. The record's own identity is read as current here, so each
    case is the selection and the precedence and nothing else; the real
    fixtures are in `test_declared_equivalence`."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, verify, "identity_drift",
                        verify.identity_drift)
        verify.identity_drift = lambda rec: (
            {"reasons": [], "changed": []} if rec.get("resultId") else None)

    def decide(self, records, *, head: str | None = H, scope=None,
               check: str = "unit", twin_run: str = "x") -> dict:
        return gates._check_requirement(
            CARD, {"name": check, "gateScope": scope, "run": "x",
                   "root": "worktree", "accepts": ["unit-review"],
                   "acceptedChecks": {"unit-review": {
                       "name": "unit-review", "run": twin_run,
                       "root": "worktree"}}},
            {"commit": head}, records)

    def twin(self, ident, outcome, **kw):
        return record(ident, outcome, check="unit-review", resultId="result:t",
                      identity={"rootClass": "worktree"}, **kw)

    def test_an_equal_twin_at_h_answers(self):
        found = self.decide([self.twin("t", "passed", when=1)])
        self.assertEqual(found["state"], "met")
        self.assertEqual(found["acceptedFrom"]["check"], "unit-review")

    def test_a_declaration_without_equal_substance_does_not(self):
        found = self.decide([self.twin("t", "passed", when=1)],
                            twin_run="y")
        self.assertEqual(found["reason"], "not-run")
        self.assertEqual(found["equivalence"]["dimension"], "command")

    def test_a_twin_on_another_head_is_history(self):
        found = self.decide([self.twin("t", "cancelled", at=G, when=1)])
        self.assertEqual(found["reason"], "other-commit")

    def test_a_twins_narrow_pass_does_not_cover(self):
        found = self.decide([self.twin("t", "passed", when=1, scope="fast")])
        self.assertEqual(found["reason"], "not-run")

    def test_a_twins_denial_supersedes_nothing(self):
        found = self.decide([self.twin("t", "passed", when=1),
                             self.twin("d", "denied", at=None, when=2)])
        self.assertEqual(found["state"], "met")


class OnlyTheResolvedDeclarationCounts(unittest.TestCase):

    def test_a_raw_accepts_list_is_not_a_declaration(self):
        """Equivalents come from the policy's resolved `acceptedChecks` —
        definitions read now — never from a bare list of names."""
        self.assertEqual(gates._accepted_equivalents({"name": "unit",
                                                      "accepts": ["x"]}), {})

    def test_a_record_is_partitioned_by_its_own_identity(self):
        """#97: no identity is legacy; an identity whose definition still
        holds is current; one that moved — or cannot be read — is
        outdated, never current."""
        self.assertEqual(gates._partition({}, None), "legacy")
        self.assertEqual(gates._partition({}, {"reasons": ["x"]}), "legacy")
        self.assertEqual(gates._partition({"resultId": "r"},
                                          {"reasons": []}), "current")
        self.assertEqual(gates._partition({"resultId": "r"},
                                          {"reasons": ["check-changed"]}),
                         "outdated")
        self.assertEqual(gates._partition({"resultId": "r"}, None),
                         "outdated")


class TheSafer36Shape(GateCase):
    """R2 inverted (E4), through the real `verify.request`: commit A's run
    was cancelled; commit B passed; then the policy gains an approved
    input meant for the next card, which (until #97) makes B's pass read
    `stale / policy-changed`. The gate must cite B and that reason."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.document = self.gated(body=HANGS)
        self.commit_a = self.commit_in(self.wt, "first attempt\n")
        self.manifest(agent="run-1")
        asking = threading.Thread(target=lambda: self.ask(agent="run-1"),
                                  daemon=True)
        asking.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        for ident in list(verify.RUNNING):
            verify.cancel(ident, "cancelled-with-run")
        asking.join(timeout=20)
        self.document["checks"][0]["run"] = str(self.executable("unit", PASSES))
        self.declares(self.document)
        self.commit_b = self.commit_in(self.wt, "second attempt\n")
        self.manifest(agent="run-2")
        self.assertEqual(self.ask(agent="run-2")["outcome"], "passed")
        verify.forget_cached()
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")

    def add_the_next_cards_context(self) -> None:
        mock = self.work / "reference" / "card37-mockup.md"
        mock.write_text("a mockup for the next card\n", encoding="utf-8")
        self.document["context"] = [{"name": "card37-mockup",
                                     "path": "work:reference/card37-mockup.md",
                                     "required": False}]
        self.declares(self.document)
        verify.forget_cached()
        gates.forget_cached()

    def make_legacy(self) -> None:
        """B's pass as a record written before result identities: the
        same file, without the fields #97 added."""
        for record in verify.for_task(CARD):
            if record.get("outcome") != "passed":
                continue
            path = verify.path_of(record["id"])
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("resultId", "identity", "provenance", "inputs",
                        "prepareSteps", "checkSubstance"):
                data.pop(key, None)
            path.write_text(json.dumps(data, indent=2, sort_keys=True),
                            encoding="utf-8")
        verify.forget_cached()
        gates.forget_cached()

    def test_new_evidence_survives_unrelated_context(self):
        """E1 / R1 inverted at the gate: B's pass carries a result identity,
        and nothing it depends on moved — the gate is met with zero
        reruns, and the record still names the policy it ran under."""
        before = next(r for r in verify.for_task(CARD)
                      if r.get("outcome") == "passed")
        self.add_the_next_cards_context()
        found = self.decide()
        self.assertEqual(found["decision"], "allowed")
        record = verify.read(before["id"])
        self.assertEqual(record["provenance"]["policyRevision"],
                         before["policyRevision"])
        reading = verify.outcome_now(record)
        self.assertEqual(reading["outcome"], "passed")
        self.assertTrue(reading["freshness"]["provenance"]["policyChanged"])

    def test_the_reason_cites_h_and_its_real_stale_reason(self):
        """The LEGACY shape (E14): B's pass written before #97 keeps
        today's rules — `policy-changed` — cites H, never A, and needs
        exactly one fresh run."""
        self.make_legacy()
        self.add_the_next_cards_context()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        row = found["unmet"][0]
        self.assertEqual(found["head"]["commit"], self.commit_b)
        self.assertEqual(row["reason"], "stale")
        self.assertEqual(row["staleReason"], "policy-changed")
        self.assertEqual(row["commit"], self.commit_b)
        self.assertNotIn(self.commit_a[:12], row["why"])
        self.assertEqual(row["otherCommits"], 1)
        self.assertIn(self.commit_a,
                      [h["commit"] for h in row["history"]],
                      "the older cancellation is kept, as history")

    def test_a_legacy_record_needs_one_fresh_run_and_is_never_rewritten(self):
        self.make_legacy()
        legacy = {p: p.read_bytes()
                  for p in (self.state / "evidence").glob("*.json")}
        self.add_the_next_cards_context()
        self.assertEqual(self.decide()["decision"], "blocked")
        self.manifest(agent="run-3")
        fresh = self.ask(agent="run-3")
        self.assertEqual(fresh["outcome"], "passed")
        self.assertTrue(fresh["resultId"])
        verify.forget_cached()
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")
        for path, data in legacy.items():
            self.assertEqual(path.read_bytes(), data,
                             f"{path.name} is a legacy record and stays "
                             f"byte for byte")

    def test_a_new_commit_leaves_both_as_history(self):
        head = self.commit_in(self.wt, "third attempt\n")
        verify.forget_cached()
        gates.forget_cached()
        found = self.decide()
        row = found["unmet"][0]
        self.assertEqual(row["reason"], "other-commit")
        self.assertIn(f"has not run on {head[:12]}", row["why"])
        self.assertEqual(row["otherCommits"], 2)
        self.assertIsNone(row["evidence"])


class FindingsAreNotTheChecksGate(GateCase):
    """§6.2's last row: a passing check at H with an open review finding.
    The check is met — findings never become a check result — and the
    finding stays open where the acceptance record keeps it."""

    def test_an_open_finding_does_not_unmeet_a_passing_check(self):
        import acceptance  # noqa: E402
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "istos"
        wt = self.worktree()
        self.gated()
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        acceptance.finding(CARD, "in-progress", event="opened",
                           text="the empty state is unreadable")
        found = self.decide()
        self.assertEqual(found["decision"], "allowed")
        self.assertEqual(found["requirements"][0]["state"], "met")
        read = acceptance.for_card(CARD, "in-progress")
        self.assertEqual(read["findingCounts"]["open"], 1)


class TheMergeRevalidatesTheEvidence(GateCase):
    """E13 without an override: allowed at H, a push lands before the merge,
    and the mutation refuses rather than merging the commit nobody checked."""

    def test_a_push_between_the_reading_and_the_merge_is_refused(self):
        wt = self.worktree()
        self.gated()
        head = self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        (self.tasks / "review" / CARD).write_text(
            card("71 — Gate integration", status="Review"), encoding="utf-8")
        (self.tasks / "in-progress" / CARD).unlink()
        self.assertEqual(self.decide()["decision"], "allowed")
        landed: list[str] = []
        original = github._assert_lands_in_the_base

        def push_lands(filename, target):
            original(filename, target)
            if not landed:
                landed.append(self.commit_in(wt, "a late push\n"))
                verify.forget_cached()
                gates.forget_cached()

        self.addCleanup(setattr, github, "_assert_lands_in_the_base", original)
        github._assert_lands_in_the_base = push_lands
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertNotEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", landed[0],
                   "main").returncode)
        self.assertNotEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", head,
                   "main").returncode,
            "nothing merged: the gate refused at the mutation")
        self.assertEqual(self.stage_of(CARD), "review")


if __name__ == "__main__":
    unittest.main()
