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

    def test_a_phase_scope_integrates_its_members_and_delivers_the_whole(self):
        j = self.journey()
        phase = j.card("20", "Ship the export", author="bob",
                       extra="**Type:** Phase\n",
                       body="The export, end to end.\n\n## Cards\n\n"
                            "- 21 — Export model\n- 22 — Export button\n")
        one = j.card("21", "Export model", stage="to-do", author="bob")
        two = j.card("22", "Export button", stage="to-do", author="bob")
        solo = j.card("30", "Fix the footer", author="bob")
        for card in (phase, solo):
            j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        j.step("enable", agreement={
            # one shared total; the phase is allocated part of it and the
            # Bench scope keeps the rest (D6, D7)
            "capacity": {"implementation": 2, "review": 2},
            "phases": {"20": {"enabled": True, "strategy": "sequential",
                              "authority": {w: True for w in ALL},
                              "allocation": {"implementation": 1, "review": 1,
                                             "dailyStarts": 6}}}})
        adopted = j.step("adopt", file=phase)
        self.assertEqual(adopted["scope"], "phase:20")

        # the first pass: the phase is prepared, then its first member and
        # the standalone card start — two launches, within the shared total
        first = j.step("pass")
        launched = [r["card"] for r in first["pass"]["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(sorted(launched), sorted([one, solo]))
        scopes = {o["file"]: o["scope"] for o in first["ops"] if o["action"] == "implement"}
        self.assertEqual(scopes, {one: "phase:20", solo: "bench"},
                         "one scope per card: the Bench scope never launched a member")

        j.until(lambda r: r["stages"].get(phase) == "done", passes=20)
        reading = j.step("read")
        for card in (one, two, solo):
            self.assertEqual(reading["stages"][card], "done", card)
        # sequential: the second member started only after the first was
        # integrated, so the two work runs never overlapped in the phase
        works = [c["task"] for c in j.calls("work")]
        self.assertEqual(works.count(one), 1)
        self.assertEqual(works.count(two), 1)
        # the phase's integrated head is what was reviewed and merged —
        # never a member's head on its own
        for card in ("card-21.txt", "card-22.txt", "card-30.txt"):
            self.assertTrue(j.remote_has(card), card)
        actions = [(o["file"], o["action"], o.get("outcome") or o["status"])
                   for o in reading["ops"]]
        self.assertIn((one, "integrate", "integrated"), actions)
        self.assertIn((two, "integrate", "integrated"), actions)
        self.assertIn((phase, "merge", "merged"), actions)
        self.assertNotIn((one, "merge", "merged"), actions,
                         "a member is integrated, never merged on its own")
        reviewed = {c["task"] for c in j.calls("review")}
        self.assertIn(phase, reviewed, "the integrated head had its own review")


class Vendored(_Journeys, unittest.TestCase):
    LAYOUT = "vendored"


class SourceHosted(_Journeys, unittest.TestCase):
    LAYOUT = "source"


class External(_Journeys, unittest.TestCase):
    LAYOUT = "external"


class FailureWindows(unittest.TestCase):
    """Crashes, stale answers, moved heads, other people's moves, pauses
    and spent quotas: each preserves the work and never doubles an
    action. Spread over the layouts; the positive journeys above already
    run in all three."""

    def journey(self, layout: str, **kwargs) -> Journey:
        j = Journey(layout, **kwargs)
        self.addCleanup(j.close)
        return j

    def owned(self, j: Journey, number: str, title: str) -> str:
        card = j.card(number, title, author="bob")
        j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        return card

    def ops(self, j: Journey, card: str, action: str) -> list[dict]:
        return [o for o in j.step("read")["ops"]
                if o["file"] == card and o["action"] == action]

    def test_a_crash_before_the_launch_is_reconciled_and_launched_once(self):
        j = self.journey("vendored")
        card = self.owned(j, "7", "Export as CSV")
        j.step("enable")
        self.assertEqual(j.step("pass", crash="launch-before"), {"crashed": True})
        [intent] = self.ops(j, card, "implement")
        self.assertEqual(intent["status"], "intent", "the intent was durable first")
        self.assertEqual(j.calls(), [])
        # a launch that provably ran nothing is retried, after its backoff
        # (shortened here; see journey_driver)
        j.until(lambda r: r["stages"][card] in ("review", "done"), backoff=0)
        first, second = sorted(self.ops(j, card, "implement"), key=lambda o: o["attempt"])
        self.assertEqual(first["status"], "abandoned",
                         "the first attempt provably ran nothing")
        self.assertIn(second["status"], ("launched", "ended"), "the second ran")
        self.assertEqual(len(j.calls("work")), 1, "launched exactly once")

    def test_a_crash_after_the_launch_holds_an_unknown_outcome(self):
        j = self.journey("external")
        card = self.owned(j, "7", "Export as CSV")
        j.step("enable")
        self.assertEqual(j.step("pass", crash="launch-after"), {"crashed": True})
        for _ in range(3):
            j.step("pass")
        [op] = self.ops(j, card, "implement")
        self.assertEqual(op["status"], "unknown",
                         "a plan was recorded and no run is tracked: nobody can "
                         "say whether it ran, so it is held")
        self.assertEqual(len(j.calls("work")), 1, "never launched blindly again")
        # D14: execution is not handed over while an outcome is unknown
        refused = j.step("handover")
        self.assertTrue(refused.get("refused"), refused)
        # a person settles it; settling launches nothing
        j.step("resolve", key=op["key"], decision="launched")
        j.step("pass")
        self.assertEqual(self.ops(j, card, "implement")[0]["status"], "ended")
        self.assertEqual(len(j.calls("work")), 1)
        self.assertFalse(j.step("handover").get("refused"),
                         "settled and quiet, the handover is allowed")

    def test_a_crash_after_the_merge_is_reconciled_not_merged_again(self):
        j = self.journey("source")
        card = self.owned(j, "7", "Export as CSV")
        j.step("enable", agreement={"authority": [w for w in ALL if w != "merge"]})
        j.until(lambda r: r["delivery"].get(card, {}).get("code") == "merge-not-delegated")
        # the owner delegates the merge, and the pass that makes it dies
        # after the merge landed and before its receipt
        j.step("enable")
        self.assertEqual(j.step("pass", crash="merge-after"), {"crashed": True})
        [merge] = self.ops(j, card, "merge")
        self.assertEqual(merge["status"], "intent", "no receipt was written")
        self.assertTrue(j.remote_has("card-7.txt"), "the merge itself had landed")
        for _ in range(2):
            j.step("pass")
        [merge] = self.ops(j, card, "merge")
        self.assertEqual(merge["status"], "ended", "reconciled from what happened")
        self.assertEqual(j.step("read")["stages"][card], "done")
        # the work reached the remote base once: one commit introduced it
        # (the board's own bookkeeping commits publish too, in this layout)
        added = j.s.git(j.origin, "log", "--format=%H", "main", "--", "card-7.txt")
        self.assertEqual(len(added.split()), 1, "nothing was merged a second time")

    def test_a_stale_or_foreign_answer_is_refused_and_writes_nothing(self):
        j = self.journey("external")
        card = self.owned(j, "7", "Export as CSV")
        j.step("reason", file=card, need="questions", via="person")
        before = j.card_text(card)
        foreign = j.step("answer", file=card, actor_seen="bob", request="answer-r-1")
        self.assertTrue(foreign.get("refused"), foreign)
        self.assertIn("identity", foreign["message"])
        stale = j.step("answer", file=card, brief="0" * 16, request="answer-r-2")
        self.assertTrue(stale.get("refused"), stale)
        self.assertIn("brief", stale["message"])
        self.assertEqual(j.card_text(card), before, "a refusal writes nothing")
        first = j.step("answer", file=card, request="answer-r-3")
        self.assertFalse(first.get("refused"), first)
        # the same request again is the first answer, not a second one
        replay = j.step("answer", file=card, request="answer-r-3")
        self.assertTrue(replay.get("duplicate"), replay)
        self.assertEqual(j.card_text(card).count('"kind": "answer"'), 1)

    def test_a_head_that_moves_under_an_approval_is_asked_about_afresh(self):
        j = self.journey("external", gates={"acceptance": {"review": "bench"}})
        card = self.owned(j, "7", "Export as CSV")
        # merge is not delegated: the approval stands until a person merges
        j.step("enable", agreement={"authority": [w for w in ALL if w != "merge"]})
        j.until(lambda r: r["delivery"].get(card, {}).get("code") == "merge-not-delegated")
        [first] = j.step("evidence", file=card)["verdicts"]
        moved = j.person_commits(card)
        self.assertNotEqual(moved, first["head"])
        reading = j.step("read")["delivery"][card]
        self.assertEqual(reading["head"], moved)
        self.assertEqual(reading["code"], "review",
                         "the approval was of another commit; this one needs its own")
        j.until(lambda r: r["delivery"].get(card, {}).get("code") == "merge-not-delegated")
        heads = [v["head"] for v in j.step("evidence", file=card)["verdicts"]]
        self.assertEqual(sorted(heads), sorted([first["head"], moved]))
        self.assertEqual(j.step("read")["stages"][card], "review", "nothing merged")

    def test_another_persons_move_is_never_acquired(self):
        j = self.journey("vendored")
        theirs = j.card("5", "Their card", author="bob")
        j.hand_move(theirs, "backlog", "to-do", author="bob")
        mine = self.owned(j, "7", "Export as CSV")
        j.step("enable")
        j.until(lambda r: r["stages"][mine] == "done")
        reading = j.step("read")
        self.assertEqual(reading["eligibility"][theirs], "foreign-move")
        self.assertEqual(reading["stages"][theirs], "to-do")
        self.assertNotIn(theirs, [c["task"] for c in j.calls()],
                         "nothing was launched on a card someone else moved")

    def test_a_later_move_by_someone_else_stops_the_next_action(self):
        j = self.journey("external")
        card = self.owned(j, "7", "Export as CSV")
        j.step("enable", agreement={"authority": [w for w in ALL if w != "review"]})
        j.until(lambda r: r["stages"][card] == "review")
        # bob walks it back; his move, not ada's, is now the latest
        j.hand_move(card, "review", "in-progress", author="bob")
        for _ in range(3):
            j.step("pass")
        reading = j.step("read")
        self.assertEqual(reading["eligibility"][card], "foreign-move")
        self.assertEqual(reading["stages"][card], "in-progress", "user moves win")
        self.assertEqual(len(j.calls("work")), 1, "no second launch on his card")

    def test_a_paused_bench_scope_starts_nothing_until_resumed(self):
        j = self.journey("external")
        card = self.owned(j, "7", "Export as CSV")
        j.step("enable")
        j.step("scope", scope="bench", action="pause")
        for _ in range(2):
            j.step("pass")
        self.assertEqual(j.calls(), [])
        self.assertEqual(j.step("read")["stages"][card], "to-do")
        j.step("scope", scope="bench", action="resume")
        j.until(lambda r: r["stages"][card] in ("review", "done"))
        self.assertEqual(len(j.calls("work")), 1)

    def test_a_spent_daily_quota_survives_restarts(self):
        j = self.journey("external")
        first = self.owned(j, "7", "Export as CSV")
        second = self.owned(j, "8", "Import rows")
        j.step("enable", agreement={"capacity": {"dailyStarts": 1},
                                    "authority": ["implement"]})
        for _ in range(4):            # every pass is a fresh process
            j.step("pass")
        self.assertEqual(len(j.calls("work")), 1)
        codes = {i["card"]: i["code"] for i in j.step("read")["schedule"]}
        waiting = second if j.calls("work")[0]["task"] == first else first
        self.assertEqual(codes.get(waiting), "daily-starts-exhausted")

    def test_a_run_that_keeps_dying_is_retried_boundedly_then_held(self):
        j = self.journey("external")
        card = self.owned(j, "7", "Export as CSV")
        j.control(work="die")             # "API Error: 500 overloaded", exit 1
        j.step("enable")
        for _ in range(10):
            j.step("pass", backoff=0)
        reading = j.step("read")
        # D7/D9: the first launch and exactly two transient retries; the
        # third failure opens the breaker, which starts nothing — not even
        # a diagnosis — while it is open
        self.assertEqual(len(j.calls("work")), 3)
        self.assertTrue(reading["breaker"], "repeated failures open the breaker")
        self.assertEqual(len(reading["incidents"]), 1, "the retries spent: an incident")
        self.assertEqual(j.calls("reason"), [])
        self.assertEqual(reading["stages"][card], "in-progress",
                         "the card stays where the work is; nothing moves it on")
        # D5: a person closes the breaker; the incident gets a bounded,
        # read-only diagnosis, and its proposal is held for the owner
        j.step("reset-breaker")
        for _ in range(6):
            j.step("pass", backoff=0)
        reading = j.step("read")
        diagnoses = j.calls("reason")
        self.assertGreaterEqual(len(diagnoses), 1)
        self.assertLessEqual(len(diagnoses), 2, "at most recoveryAttempts (2)")
        self.assertTrue(all(c["allow"] == len(c["reads"]) for c in diagnoses),
                        "a diagnosis is granted exact reads and nothing else")
        self.assertEqual(len(j.calls("work")), 3, "no blind relaunch")
        [incident] = reading["incidents"]
        self.assertNotEqual(incident["status"], "resolved",
                            "what a diagnosis says resolves nothing")

    def test_default_off_launches_none_of_the_existing_work(self):
        j = self.journey("vendored")
        card = self.owned(j, "7", "Export as CSV")
        working = self.owned(j, "8", "Import rows")
        j.step("move", file=working, **{"from": "to-do", "to": "in-progress"})
        for _ in range(2):
            self.assertEqual(j.step("pass")["pass"], {"ran": False, "why": "off"})
        # an agreement that is in force but not enabled is still off
        j.step("enable", agreement={"enabled": False})
        self.assertEqual(j.step("pass")["pass"], {"ran": False, "why": "off"})
        reading = j.step("read")
        self.assertEqual((reading["stages"][card], reading["stages"][working]),
                         ("to-do", "in-progress"))
        self.assertEqual(j.calls(), [])
        self.assertEqual(reading["ops"], [])


if __name__ == "__main__":
    unittest.main()
