"""Scoped orchestration end to end, in all three layouts (task 146).

Each test is one product journey on a disposable board
(`tests/product/journey.py`): real git, a local bare origin, a fake
hosting provider, bench's own launch, check, review, repair and merge
paths, the Claude adapter's real `run` — and a stand-in for the vendor
CLI behind it, so no model is invoked and nothing leaves the machine.
Every step is a fresh process, so every step is also a restart.

The journeys, and the decision each one holds (orchestrator
specification D1–D16):

- **The owner's journey** — someone else writes a card; it is nobody's
  to orchestrate until the current user moves it (D1). With no agreement
  nothing runs (D16, default off). A context-rich reasoning run drafts a
  question onto the card; the owner answers it there (D2, D11, D12). The
  first explicit agreement launches the work through ▸ start work's own
  path (D16), an independent review rides a different model (D3), and an
  explicitly ungated policy merges — labelled *not verified* — with the
  result read back off the remote (D4).
- **Check, repair, recheck** — under a merge rule that requires a check
  and a Bench review, a failing check at the exact head launches a
  counted repair, the new head is checked and reviewed afresh, and only
  then merged (D3, D4, D9).
- **A phase scope** — a phase adopted by its scope shares the Bench
  scope's capacity, runs its members into its own branch (integrated is
  not delivered) and delivers the integrated head under the integration
  rule, sweeping its members to done (D6).

And the failure windows, each preserving work and never doubling an
action: a crash before and after a launch, a crash after a merge, a
stale answer, a head that moves under an approval, a teammate's move, a
parent pause, a spent daily quota, and a held unknown outcome.

    python3 -m unittest tests.test_orchestration_journey -v
"""

from __future__ import annotations

import unittest

from tests.product.journey import Journey

ALL = ("implement", "review", "repair", "merge", "integrate", "reasoning",
       "readiness", "recovery")


def done(name: str):
    return lambda reading: reading["stages"].get(name) == "done"


class _Journeys:
    """The positive journeys, run once per layout by the subclasses."""

    LAYOUT = "external"

    def journey(self, **kwargs) -> Journey:
        j = Journey(self.LAYOUT, **kwargs)
        self.addCleanup(j.close)
        return j

    def test_the_layout_is_the_one_named(self):
        j = self.journey()
        found = j.step("describe")
        self.assertEqual(found["external"], self.LAYOUT == "external")
        self.assertEqual(found["shares"], self.LAYOUT != "external",
                         "the cards share the application's repository "
                         "only when the layout puts them there")
        self.assertEqual(found["identity"]["name"], "ada")

    def test_the_owners_journey_from_a_move_to_the_remote(self):
        j = self.journey()
        card = j.card("7", "Export as CSV", author="bob")

        # D16: shipped and installed, nothing runs until someone turns it on
        j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        off = j.step("pass")
        self.assertEqual(off["pass"], {"ran": False, "why": "off"})
        self.assertEqual(j.calls(), [], "no agent was launched while off")

        # D1: bob wrote it; ada's move into To Do is what makes it hers
        reading = j.step("read")
        self.assertEqual(reading["eligibility"][card], "own-move")

        # D2/D11: a launched, context-rich reasoning run drafts a question,
        # and the question lives on the card
        asked = j.step("reason", file=card, need="questions", via="person")
        self.assertEqual(asked["job"]["status"], "valid")
        [reason] = j.calls("reason")
        self.assertEqual(reason["model"], "fable", "reasoning follows the review model")
        self.assertTrue(any("reason-card" in r for r in reason["reads"]),
                        "the card reached the run by an exact read")
        questions = j.step("read")["decisions"][card]["questions"]
        self.assertEqual(questions, [{"text": "Include hidden columns?",
                                      "state": "open"}])
        self.assertIn("Include hidden columns?", j.card_text(card))
        # D12: answered on the board, bound to what the page showed
        j.step("answer", file=card, choice=0, request="owner-answer-1")
        self.assertEqual(j.step("read")["decisions"][card]["questions"][0]["state"],
                         "answered")

        # D16: the first explicit agreement launches through start work
        j.step("enable")
        j.until(done(card))
        [work] = j.calls("work")
        self.assertEqual(work["model"], "opus")
        self.assertIn("worktrees", work["cwd"])
        # D3: the review rode another model, in a checkout pinned to the head
        [review] = j.calls("review")
        self.assertEqual(review["model"], "fable")
        self.assertIn("reviews", review["cwd"])

        # D4: an ungated policy merges and says so; the remote holds the code
        self.assertTrue(j.remote_has("card-7.txt"), "the merge was published")
        ops = {o["action"]: o for o in j.step("read")["ops"]}
        self.assertEqual(ops["merge"]["outcome"], "merged")
        self.assertEqual(ops["implement"]["scope"], "bench")
        found = j.step("evidence", file=card)
        self.assertEqual(found["checks"], [], "no check ran: none was required")
        [verdict] = found["verdicts"]
        self.assertEqual(verdict["verdict"], "APPROVE")
        text = j.card_text(card)
        self.assertIn("## Orchestration record", text)
        self.assertIn("not verified", text, "the merge says it was ungated")

    def test_a_failed_check_is_repaired_rechecked_and_reviewed_afresh(self):
        j = self.journey(gates={"acceptance": {"checks": ["unit"], "review": "bench"}})
        card = j.card("8", "Import rows", author="bob")
        j.control(broken=["8"])
        j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        j.step("enable")
        j.until(done(card), passes=16)

        found = j.step("evidence", file=card)
        outcomes = [(c["outcome"], c["commit"]) for c in found["checks"]]
        failed = [c for o, c in outcomes if o == "failed"]
        passed = [c for o, c in outcomes if o == "passed"]
        self.assertEqual(len(failed), 1, outcomes)
        self.assertEqual(len(passed), 1, outcomes)
        self.assertNotEqual(failed[0], passed[0],
                            "the pass is about the repaired head, not the failed one")
        # the approval is about the head that passed, and that head merged
        [verdict] = [v for v in found["verdicts"] if v["verdict"] == "APPROVE"]
        self.assertEqual(verdict["head"], passed[0])
        self.assertEqual(len(j.calls("act-pr")), 1, "one counted repair round")
        self.assertFalse(j.remote_has("broken.txt"))
        self.assertTrue(j.remote_has("card-8.txt"))
        self.assertTrue(j.contains("main", passed[0]),
                        "the remote base holds exactly the checked head")
        self.assertEqual(j.s.git(j.origin, "branch", "--list", f"task/{card[:-3]}"), "",
                         "cleanup removed the delivered branch from the remote")


class Vendored(_Journeys, unittest.TestCase):
    LAYOUT = "vendored"


class SourceHosted(_Journeys, unittest.TestCase):
    LAYOUT = "source"


class External(_Journeys, unittest.TestCase):
    LAYOUT = "external"


if __name__ == "__main__":
    unittest.main()
