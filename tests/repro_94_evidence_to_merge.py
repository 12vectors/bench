"""Card 94 — the evidence-to-merge defects, reproduced on disposable fixtures.

This module is **not** part of the suite (the name does not match
`test*.py`, so `unittest discover` never collects it). It is the executable
half of `reference/94-evidence-to-merge-workflow.md`: each case pins one
behaviour of `main` at `ba8338d` that the design changes, so the
implementation card can flip each assertion and keep it as a regression.

    python3 -m unittest tests.repro_94_evidence_to_merge -v

Every case runs in a temporary directory with stub check executables, through
the same `verify.request` and `gates.evaluate` the board uses. Nothing here
reads or writes SAFER, a live board, a remote or any operator configuration.

What each case shows TODAY (and what the design says instead):

- R1 `test_unrelated_context_marks_unchanged_source_stale` — adding an
  approved input that the check never reads makes a same-commit pass read
  `stale / policy-changed`. (Design §4: provenance, not invalidation.)
- R2 — fixed by card 96 (relevance before precedence: the stale pass at
  the current commit is the reason, never the older head's cancellation).
  Its inverted case lives in `tests/test_evidence_to_merge.py`
  (`TheSafer36Shape`, and `Precedence` for every §6.2 row).
- R3 `test_a_differently_named_check_does_not_count` — the same program,
  passing at the same commit under another check name, does not satisfy the
  requirement. This one is CORRECT today and stays: equivalence must be
  proved, never inferred from a name (Design §5).
- R4 `test_headline_count_includes_optional_checks` — the compact Bench
  checks count (`N of M passed`) counts optional checks next to required
  ones. (Design §7: required obligations only.)
- R5 — fixed by card 95 (an override with no implementation commit is
  refused, nothing written). Its inverted case lives in
  `tests/test_evidence_to_merge.py` (`AnOverrideNeedsATarget`).
"""

from __future__ import annotations

import threading
import unittest

from tests.test_integration_gates import (CARD, HANGS, PASSES,  # noqa: E402
                                          GateCase, wait_for)

import gates  # noqa: E402  (on sys.path once the case module is imported)
import verify  # noqa: E402


class Safer36Shape(GateCase):
    """Commit A: `unit` asked for, then cancelled (the run that asked was
    stopped). Commit B: `unit` passes. Then project policy gains an approved
    input meant for the *next* card. The application source never moves."""

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
        # commit B, with a check that now passes
        self.document["checks"][0]["run"] = str(self.executable("unit", PASSES))
        self.declares(self.document)
        self.commit_b = self.commit_in(self.wt, "second attempt\n")
        self.manifest(agent="run-2")
        self.assertEqual(self.ask(agent="run-2")["outcome"], "passed")
        verify.forget_cached()
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")

    def add_unrelated_context(self) -> None:
        mock = self.work / "reference" / "card37-mockup.md"
        mock.write_text("a mockup for the next card\n", encoding="utf-8")
        self.document["context"] = [{"name": "card37-mockup",
                                     "path": "work:reference/card37-mockup.md",
                                     "required": False}]
        self.declares(self.document)
        verify.forget_cached()
        gates.forget_cached()

    def test_unrelated_context_marks_unchanged_source_stale(self):
        self.add_unrelated_context()
        latest = verify.for_task(CARD)[0]
        self.assertEqual(latest["source"]["commit"], self.commit_b)
        reading = verify.outcome_now(latest)
        # TODAY: stale, because the whole profile revision is compared.
        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "policy-changed")
        self.assertNotIn("source-changed", reading["freshness"]["reasons"])


class NamesAreNotEquivalence(GateCase):

    def test_a_differently_named_check_does_not_count(self):
        wt = self.worktree()
        document = self.gated()
        twin = dict(document["checks"][0], name="unit-review")
        document["checks"].append(twin)
        for intent in document["profiles"].values():
            intent["verify"] = ["unit", "unit-review"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask("unit-review")["outcome"], "passed")
        found = self.decide()
        # Correct today and kept: same argv, same commit, different identity.
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["reason"], "not-run")


class HeadlineCounts(GateCase):

    def test_headline_count_includes_optional_checks(self):
        wt = self.worktree()
        document = self.gated()
        optional = {"name": "docs", "run": str(self.executable("docs", PASSES)),
                    "root": "worktree"}
        document["checks"].append(optional)
        for intent in document["profiles"].values():
            intent["verify"] = ["unit", "docs"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask("unit")["outcome"], "passed")
        verify.forget_cached()
        compact = verify.board({"stages": [{"tasks": [{"file": CARD}]}]})[CARD]
        # TODAY: the optional, never-asked `docs` is in the denominator and
        # the card's worst outcome is `not-run`, although the only required
        # check passed at the head and the gate allows the merge.
        self.assertEqual(compact["checks"], 2)
        self.assertEqual(compact["passed"], 1)
        self.assertEqual(compact["outcome"], "not-run")
        self.assertEqual(self.decide()["decision"], "allowed")


if __name__ == "__main__":
    unittest.main()
