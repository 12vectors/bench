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

- R1 — fixed by card 97 (result identities: an approved input the check
  does not declare is provenance, never an invalidator). Its inverted case
  lives in `tests/test_result_identity.py`, and the SAFER36 shape with a
  new and a legacy record in `tests/test_evidence_to_merge.py`
  (`TheSafer36Shape`).
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

import unittest

from tests.test_integration_gates import (CARD, PASSES,  # noqa: E402
                                          GateCase)

import verify  # noqa: E402  (on sys.path once the case module is imported)


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
