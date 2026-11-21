"""Task 164 — bounded retries for a required check whose setup failed.

Delivery used to hold every check that could not reach a verdict. The
agreement's `setupRetries` ceiling (0 by default, so nothing changes until
an operator sets it) lets delivery ask again — at the same head, for the
same check, answering the exact setup failure it read — and holds once
the ceiling is spent, saying what failed, how many attempts were used,
why it is held and what a person can do. Assertion failures, refusals,
timeouts and broken reports are never asked again.

Every side effect is the delivery sandbox's stand-in (no check runner).

    python3 -m unittest tests.test_setup_retries -v
"""

from __future__ import annotations

import unittest

from tests.test_orchestration_delivery import (  # noqa: E402
    A, B, CARD, Sandbox, gate)

import agreements  # noqa: E402
import coordinate  # noqa: E402
import operations  # noqa: E402
import required  # noqa: E402


def setup_gate(evidence="ev-1", reason="prepare-failed"):
    return gate("blocked", checks=[("unit", "unmet", reason, evidence)])


class Retries(Sandbox):
    def finish_batch(self):
        batch = self.batches.get(CARD)
        if batch:
            batch["state"] = "done"

    def retry_calls(self):
        return [c for c in self.retries]

    def setUp(self):
        super().setUp()
        self.retries: list = []
        real = self._start_checks

        def start(filename, stage=None, *, retry=None, **kw):
            self.retries.append(retry)
            return real(filename, stage, **kw)
        self._start_checks = start
        from unittest import mock
        patch = mock.patch.object(required, "start", start)
        patch.start()
        self.addCleanup(patch.stop)


class TheDefaultChangesNothing(Retries):
    def test_no_ceiling_holds_with_the_reason_and_the_way_through(self):
        self.enable()
        self.assertEqual(0, agreements.DEFAULTS["setupRetries"])
        self.gate = setup_gate()
        found = self.item()
        self.assertEqual("verification-setup-failed", found["code"])
        self.assertIn("setupRetries is 0", found["reason"])
        self.assertIn("◫ evidence sheet", found["reason"])
        self.assertEqual({"attempts": 0, "ceiling": 0, "retryable": True,
                          "checks": ["unit"]}, found["setupRetry"])
        self.tick()
        self.assertEqual([], self.calls["verify"])
        self.assertEqual([], self.calls["repair"])

    def test_an_agreement_without_the_key_reads_zero(self):
        doc = agreements.new_draft()
        doc["bench"]["capacity"].pop("setupRetries")
        resolved = agreements.resolve(doc)
        self.assertTrue(resolved["valid"], resolved.get("problems"))
        bench = next(s for s in resolved["scopes"] if s["id"] == "bench")
        self.assertEqual(0, bench["ceilings"]["setupRetries"]["value"])


class BoundedRepeats(Retries):
    def test_identical_failures_are_asked_again_up_to_the_ceiling_then_held(self):
        self.enable(setupRetries=2)
        self.gate = setup_gate("ev-1")
        found = self.item()
        self.assertEqual("retry-setup", found["code"])
        self.assertEqual("verify", found["action"])
        self.assertIn("setup retry 1 of 2", found["reason"])
        self.tick()
        self.assertEqual([{"checks": ["unit"], "of": ["ev-1"]}], self.retries)
        op = self.ops("verify")[0]
        self.assertEqual({"checks": ["unit"], "of": ["ev-1"]}, op["retry"])
        self.assertEqual("none", op["pool"])          # no slot, no start
        # while it runs, a second pass asks for nothing
        self.tick()
        self.assertEqual(1, len(self.calls["verify"]))
        self.finish_batch()
        self.gate = setup_gate("ev-2")                # it failed the same way
        coordinate.observe()                          # observed: ended
        self.assertIn("setup retry 2 of 2", self.item()["reason"])
        self.tick()
        self.assertEqual(2, len(self.calls["verify"]))
        self.assertEqual({"checks": ["unit"], "of": ["ev-2"]}, self.retries[-1])
        self.finish_batch()
        self.gate = setup_gate("ev-3")
        self.tick()
        held = self.item()
        self.assertEqual("verification-setup-failed", held["code"])
        self.assertIn("2 of 2 setup retries", held["reason"])
        self.assertEqual(2, held["setupRetry"]["attempts"])
        self.tick()
        self.tick()
        self.assertEqual(2, len(self.calls["verify"]))
        exhausted = [n for n in self.card_notes("attempt")
                     if n.get("outcome") == "exhausted"]
        self.assertEqual(1, len({n["key"] for n in exhausted}))
        self.assertTrue(any("2 of 2 setup retries" in b["text"]
                            for b in self.card_notes("blocker")))

    def test_a_retry_that_ended_without_a_new_answer_is_not_asked_again(self):
        self.enable(setupRetries=3)
        self.gate = setup_gate("ev-1")
        self.tick()
        self.finish_batch()                   # stopped, say: still ev-1
        self.tick()
        found = self.item()
        self.assertEqual("verification-setup-failed", found["code"])
        self.assertIn("ended without a new answer", found["reason"])
        self.assertEqual(1, len(self.calls["verify"]))

    def test_a_new_head_is_a_new_question(self):
        self.enable(setupRetries=1)
        self.gate = setup_gate("ev-1")
        self.tick()
        self.finish_batch()
        self.gate = setup_gate("ev-2")
        self.tick()
        self.assertEqual("verification-setup-failed", self.item()["code"])
        self.heads[CARD] = B
        self.assertEqual("retry-setup", self.item()["code"])


class OnlySetupFailuresAreAskedAgain(Retries):
    def test_assertion_failures_go_to_repair_never_to_a_retry(self):
        self.enable(setupRetries=5)
        self.gate = gate("blocked", checks=[("unit", "unmet", "failed", "ev-1")])
        self.assertNotEqual("retry-setup", self.item()["code"])
        self.tick()
        self.assertEqual([], [r for r in self.retries if r])

    def test_refusals_timeouts_and_broken_reports_are_held(self):
        self.enable(setupRetries=5)
        for reason in ("denied", "error"):
            with self.subTest(reason):
                self.gate = setup_gate("ev-9", reason=reason)
                found = self.item()
                self.assertEqual("verification-setup-failed", found["code"])
                self.assertIn("not a setup failure bench asks again for",
                              found["reason"])
                self.assertFalse(found["setupRetry"]["retryable"])
        self.tick()
        self.assertEqual([], self.calls["verify"])

    def test_one_unretryable_check_holds_the_whole_head(self):
        self.enable(setupRetries=5)
        self.gate = gate("blocked", checks=[
            ("unit", "unmet", "prepare-failed", "ev-1"),
            ("browser", "unmet", "error", "ev-2")])
        self.assertEqual("verification-setup-failed", self.item()["code"])


class RestartsAndRaces(Retries):
    def test_the_count_lives_in_the_ledger_across_a_restart(self):
        self.enable(setupRetries=1)
        self.gate = setup_gate("ev-1")
        self.tick()
        self.finish_batch()
        self.gate = setup_gate("ev-2")
        self.tick()
        # a new process reads the same ledger: nothing in memory carries it
        spent = coordinate.setup_retries(CARD, A, operations.all_ops())
        self.assertEqual(1, len(spent))
        self.assertEqual("verification-setup-failed", self.item()["code"])

    def test_an_intent_that_provably_ran_nothing_is_not_counted(self):
        self.enable(setupRetries=1)
        self.gate = setup_gate("ev-1")
        item = self.item()
        key = operations.key_for("verify", CARD, A, 1)
        operations.begin(key, action="verify", card=CARD, number="7",
                         scope="bench", pool="none", basis=A, attempt=1,
                         retry=item["retry"])
        # the pass died before the batch was recorded: reconciled as
        # abandoned, which frees the attempt rather than spending it
        self.batches.pop(CARD, None)
        coordinate.reconcile()
        self.assertEqual("abandoned", operations.all_ops()[0]["status"])
        self.assertEqual([], coordinate.setup_retries(CARD, A, operations.all_ops()))
        self.assertEqual("retry-setup", self.item()["code"])

    def test_an_unknown_outcome_holds_before_any_retry(self):
        self.enable(setupRetries=3)
        self.gate = setup_gate("ev-1")
        item = self.item()
        key = operations.key_for("verify", CARD, A, 1)
        operations.begin(key, action="verify", card=CARD, number="7",
                         scope="bench", pool="none", basis=A, attempt=1,
                         retry=item["retry"])
        operations.update(key, "unknown", code="verify-error")
        self.assertEqual("outcome-unknown", self.item()["code"])
        self.tick()
        self.assertEqual([], self.calls["verify"])

    def test_two_callers_with_one_decision_run_it_once(self):
        self.enable(setupRetries=2)
        self.gate = setup_gate("ev-1")
        item = self.item()
        task = self.board.cards[CARD]
        first = coordinate.execute(dict(item), task, self.board.collect())
        # a second caller that read the same plan before the first wrote
        orig = operations.for_card
        from unittest import mock
        with mock.patch.object(operations, "for_card", lambda f: []):
            second = coordinate.execute(dict(item), task, self.board.collect())
        self.assertEqual("launched", first["outcome"])
        self.assertEqual("duplicate", second["outcome"])
        self.assertEqual(1, len(self.calls["verify"]))
        self.assertIs(orig, operations.for_card)

    def test_a_retry_overtaken_by_a_newer_answer_does_not_hold_the_head(self):
        self.enable(setupRetries=2)
        self.gate = setup_gate("ev-1")
        item = self.item()

        def overtaken(filename, stage=None, **kw):
            raise required.Refused("nothing-needed")
        from unittest import mock
        with mock.patch.object(required, "start", overtaken):
            result = coordinate.execute(item, self.board.cards[CARD],
                                        self.board.collect())
        self.assertEqual("refused", result["outcome"])
        self.assertEqual([], coordinate.setup_retries(CARD, A, operations.all_ops()))
        # someone's ↻ passed meanwhile: the gate reads met, nothing is held
        self.gate = gate("allowed", checks=[("unit", "met", "passed", "ev-2")])
        self.assertNotEqual("step-refused", self.item()["code"])

    def test_a_retry_refused_about_the_same_failure_holds_instead_of_looping(self):
        # review finding at 18a6f39: a check only the review profile is
        # offered failed its setup; the work reading may not ask for it, so
        # required.start refuses `nothing-needed` while the gate still reads
        # the same failure. That must be one operation and a hold, not a
        # new ledger entry and card record on every pass.
        self.enable(setupRetries=3)
        self.gate = setup_gate("ev-1")
        refusals = []

        def not_offered(filename, stage=None, **kw):
            refusals.append(kw.get("retry"))
            raise required.Refused("nothing-needed")
        from unittest import mock
        with mock.patch.object(required, "start", not_offered):
            for _ in range(5):
                self.tick()
        self.assertEqual(1, len(refusals))
        self.assertEqual(1, len(self.ops("verify")))
        held = self.item()
        self.assertEqual("verification-setup-failed", held["code"])
        self.assertIn("a retry of this exact failure was refused", held["reason"])
        self.assertIn("work profile's verify", held["reason"])
        self.assertEqual(0, held["setupRetry"]["attempts"])
        # a new setup failure at the same head is a new answer to retry
        self.gate = setup_gate("ev-2")
        self.assertEqual("retry-setup", self.item()["code"])

    def test_more_setup_failures_than_the_ledger_keeps_are_held(self):
        self.enable(setupRetries=2)
        many = operations.RETRY_MAX + 1
        self.gate = gate("blocked", checks=[
            (f"c{i}", "unmet", "prepare-failed", f"ev-{i}") for i in range(many)])
        held = self.item()
        self.assertEqual("verification-setup-failed", held["code"])
        self.assertIn(f"more than the {operations.RETRY_MAX}", held["reason"])
        self.tick()
        self.assertEqual([], self.calls["verify"])

    def test_no_grant_or_budget_moves(self):
        self.enable(setupRetries=2)
        before = agreements.effective()
        self.gate = setup_gate("ev-1")
        self.tick()
        after = agreements.effective()
        self.assertEqual(before["revision"], after["revision"])
        bench = next(s for s in after["scopes"] if s["id"] == "bench")
        self.assertEqual(2, bench["ceilings"]["setupRetries"]["value"])


class TheBatchAsksOnlyWhatTheRetryNames(unittest.TestCase):
    def offered(self):
        return {"unit": {}, "browser": {}}

    def test_only_the_named_setup_failure_is_asked_again(self):
        retry = {"checks": ["unit"], "of": ["ev-1"]}
        item = {"check": "unit", "state": "problem", "reason": "prepare-failed",
                "evidence": "ev-1"}
        self.assertTrue(required._retrying(item, retry, self.offered()))
        for change in ({"evidence": "ev-2"}, {"reason": "failed"},
                       {"check": "browser"}, {"state": "met"}):
            with self.subTest(change):
                self.assertFalse(required._retrying({**item, **change}, retry,
                                                    self.offered()))
        # a check the work reading may not ask for is never asked
        self.assertFalse(required._retrying(item, retry, {"browser": {}}))
        self.assertFalse(required._retrying(item, None, self.offered()))

    def test_only_the_orchestrator_may_name_a_retry(self):
        with self.assertRaises(required.Refused) as caught:
            required.start(CARD, head=A, retry={"checks": ["unit"], "of": ["x"]})
        self.assertEqual("authority", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
