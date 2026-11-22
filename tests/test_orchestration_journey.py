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

    def phase_board(self) -> tuple[Journey, str, str, str, str]:
        """A phase (20: members 21, 22, both stored in Backlog) and a
        standalone card (19), both delegated by the owner's move. Task
        188: the phase card's one move queues it and its Backlog members —
        nobody adopts it and no member is moved by hand."""
        j = self.journey()
        phase = j.card("20", "Ship the export", author="bob",
                       extra="**Type:** Phase\n",
                       body="The export, end to end.\n\n## Cards\n\n"
                            "- 21 — Export model\n- 22 — Export button\n")
        one = j.card("21", "Export model", author="bob")
        two = j.card("22", "Export button", author="bob")
        # numbered below the members: requests ready in one pass are
        # admitted first come, first served with ties by card number (task
        # 182), and the race probes below expect the standalone card's
        # launch, and its run's ending, first — as the old Bench-first
        # round robin gave them
        solo = j.card("19", "Fix the footer", author="bob")
        for card in (phase, solo):
            j.step("move", file=card, **{"from": "backlog", "to": "to-do"})
        j.step("enable", agreement={
            # one shared total, first come first served (task 182); the
            # phase's limits cap what it may use at once and reserve
            # nothing — the Bench scope may use every free slot (D6, D7)
            "capacity": {"implementation": 2, "review": 2},
            "phases": {"20": {"enabled": True, "strategy": "sequential",
                              "authority": {w: True for w in ALL},
                              "limits": {"implementation": 1, "review": 1,
                                         "dailyStarts": 6}}}})
        return j, phase, one, two, solo

    def assert_first_pass(self, j: Journey, first: dict, one: str, solo: str) -> None:
        """The first pass started the phase's first member and the
        standalone card — two launches, within the shared total — each
        under its own scope. On a mismatch the message carries what the
        pass chose, each result and each launch operation's status, code
        and reason (task 155: the CI failure that first missed a member
        printed none of them)."""
        def why() -> str:
            scratch = str(j.s.root)
            lines = [f"chosen: {first['pass'].get('chosen')}"]
            lines += [f"result: {r}" for r in first["pass"].get("results", [])]
            lines += [f"operation: {o}" for o in first["ops"]
                      if o["action"] == "implement"]
            if first.get("race"):
                lines.append(f"race: {first['race']}")
            return "\n".join(lines).replace(scratch, "<scratch>")[-4000:]
        launched = [r["card"] for r in first["pass"]["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(sorted(launched), sorted([one, solo]), why())
        scopes = {o["file"]: o["scope"] for o in first["ops"] if o["action"] == "implement"}
        self.assertEqual(scopes, {one: "phase:20", solo: "bench"},
                         "one scope per card: the Bench scope never launched a "
                         "member\n" + why())

    def test_a_card_moved_mid_reading_does_not_cost_the_member_its_launch(self):
        """Task 155. The standalone card's run ends — its reaper files the
        report and moves the card into review/ — while the phase's first
        member is being launched, between `taskfiles.collect()` listing
        in-progress/ and reading that card. Before the repair the reading
        raised FileNotFoundError: at the authorization before the intent
        the whole pass died, inside the launch the member's operation was
        recorded `unknown` and it never started. Forced here at both
        points, in this layout; the first-pass launches and their scopes
        must hold either way."""
        for at in ("authorize", "launch"):
            with self.subTest(at=at):
                j, _phase, one, _two, solo = self.phase_board()
                first = j.step("pass", race={"hold": solo, "during": one, "at": at})
                self.assertIn("race", first, first)
                race = first["race"]
                # the interleaving happened as intended, or the test says so:
                # a barrier that timed out is the probe's failure, not bench's
                self.assertIsNone(race["timeout"], race)
                self.assertTrue(race["released"], race)
                self.assertTrue(race["reaped"], race)
                self.assertIsNone(race["reaperError"], race)
                self.assertFalse(race["cardThere"],
                                 f"the card had left in-progress/ when it was read: {race}")
                self.assertEqual(first["stages"][solo], "review", race)
                self.assert_first_pass(j, first, one, solo)

    def test_a_move_whose_commit_failed_does_not_stop_the_merges(self):
        """Task 155. The commit of the member's move into in-progress/
        meets a held work-state index — as it did when a run's ending was
        committing another card at the same moment — and fails: the card
        has moved on disk, its old path's deletion unrecorded. Before the
        repair nothing ever recorded that deletion — the card's later
        commits named only the paths it moved between — so the stale
        to-do/ path stayed a local change, and the phase's and the
        standalone card's merges refused over it. The journey must still
        deliver everything, and leave the work state with nothing
        uncommitted."""
        j, phase, one, two, solo = self.phase_board()
        first = j.step("pass", jam={"file": one, "to": "in-progress"})
        jam = first.get("jam")
        self.assertIsNotNone(jam, first)
        self.assertIsNone(jam["timeout"], f"a probe timeout is not a reproduction: {jam}")
        self.assertTrue(jam["jammed"], f"the jam never applied: {jam}")
        self.assertIs(jam["committed"], False,
                      f"the jammed commit must fail, or nothing was reproduced: {jam}")
        self.assertTrue(jam["lockHeld"], jam)
        self.assert_first_pass(j, first, one, solo)

        j.until(lambda r: r["stages"].get(phase) == "done", passes=20)
        reading = j.step("read")
        for card in (one, two, solo):
            self.assertEqual(reading["stages"][card], "done", card)
        self.assertEqual(j.state_status(), "",
                         "every move and report the board made is committed")

    def test_a_move_whose_commit_failed_is_recorded_by_the_cards_next_edit(self):
        """Task 155, the full-suite failure of the test above. The
        standalone card's run ends while the member's move is committing,
        and the reaper's commit of its *own* move into review/ meets the
        held index and fails. The card's next commits are edits — an
        orchestration record, its PR line — not moves. Before the repair
        each named only review/: git read the card as created there (its
        eligibility `no-movement`, so it was never merged), and the
        unrecorded deletion of in-progress/ stayed a local change the
        phase's merge refused over. Forced here deterministically: the
        reaper's move commit waits for the jam, and the jam waits for it."""
        j, phase, one, two, solo = self.phase_board()
        first = j.step("pass", jam={"file": one, "to": "in-progress",
                                    "collide": {"file": solo, "message": "→ review"}})
        jam = first.get("jam")
        self.assertIsNotNone(jam, first)
        self.assertIsNone(jam["timeout"], f"a probe timeout is not a reproduction: {jam}")
        self.assertTrue(jam["jammed"], f"the jam never applied: {jam}")
        self.assertIs(jam["collided"], False,
                      f"the standalone card's move commit must fail: {jam}")
        self.assertIs(jam["committed"], False, jam)
        self.assert_first_pass(j, first, one, solo)
        # right after the pass: the failed move is already recorded by the
        # card's next edit, attributed as the move it was
        self.assertEqual(j.state_status(), "", "the failed move was recorded")
        reading = j.step("read")
        self.assertEqual(reading["eligibility"][solo], "own-move", reading["eligibility"])

        j.until(lambda r: r["stages"].get(phase) == "done"
                and r["stages"].get(solo) == "done", passes=20)
        reading = j.step("read")
        for card in (one, two, solo):
            self.assertEqual(reading["stages"][card], "done", card)
        self.assertEqual(j.state_status(), "",
                         "every move and report the board made is committed")

    def test_a_move_staged_before_its_commit_failed_is_recorded_by_the_next_edit(self):
        """Task 155, the 06:58 full-suite failure of
        `test_a_move_whose_commit_failed_does_not_stop_the_merges`: the
        standalone card's move into review/ had *staged* — its `git add`
        succeeded — when the member's jam took the index, so only its
        commit failed. The staged deletion took in-progress/ out of the
        index, the card's next edit looked for stale paths in the index
        alone and named only review/, so the card read `no-movement` and
        the phase's merge refused over the staged in-progress/ deletion.
        Forced here deterministically: the jam waits for that add, and the
        add's commit waits for the jam."""
        j, phase, one, two, solo = self.phase_board()
        first = j.step("pass", jam={"file": one, "to": "in-progress",
                                    "collide": {"file": solo, "message": "→ review",
                                                "staged": True}})
        jam = first.get("jam")
        self.assertIsNotNone(jam, first)
        self.assertIsNone(jam["timeout"], f"a probe timeout is not a reproduction: {jam}")
        self.assertTrue(jam["stagedBeforeJam"], f"the add never staged first: {jam}")
        self.assertTrue(jam["jammed"], f"the jam never applied: {jam}")
        self.assertIs(jam["collided"], False,
                      f"the standalone card's move commit must fail: {jam}")
        self.assertIs(jam["committed"], False, jam)
        self.assert_first_pass(j, first, one, solo)
        self.assertEqual(j.state_status(), "", "the failed move was recorded")
        reading = j.step("read")
        self.assertEqual(reading["eligibility"][solo], "own-move", reading["eligibility"])

        j.until(lambda r: r["stages"].get(phase) == "done"
                and r["stages"].get(solo) == "done", passes=20)
        reading = j.step("read")
        for card in (one, two, solo):
            self.assertEqual(reading["stages"][card], "done", card)
        self.assertEqual(j.state_status(), "",
                         "every move and report the board made is committed")

    def test_a_phase_scope_integrates_its_members_and_delivers_the_whole(self):
        j, phase, one, two, solo = self.phase_board()

        # the first pass: the phase is prepared, then its first member and
        # the standalone card start — two launches, within the shared total
        first = j.step("pass")
        self.assert_first_pass(j, first, one, solo)

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
        for card in ("card-21.txt", "card-22.txt", "card-19.txt"):
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


    def test_one_move_queues_the_phase_and_its_backlog_members(self):
        """Task 188, in every layout: the phase card's move into To Do is
        the only move a person makes. Its members, stored in Backlog, are
        queued with it; the first member's own launch is what moves it,
        and the second stays where it was stored until its turn."""
        j, phase, one, two, solo = self.phase_board()
        before = j.step("read")["stages"]
        self.assertEqual(("backlog", "backlog"), (before[one], before[two]))
        j.step("pass")
        self.assertIn(one, [c["task"] for c in j.calls("work")])
        self.assertNotIn(two, [c["task"] for c in j.calls("work")])
        after = j.step("read")
        self.assertEqual("backlog", after["stages"][two],
                         "a member whose turn has not come is not moved")
        entry = next(p for p in after["phases"]["phases"] if p["card"] == phase)
        rows = {m["card"]: m for m in entry["members"]}
        self.assertTrue(rows[two]["inherited"], rows[two])
        self.assertEqual("phase scope", entry["owner"])

    def test_a_later_member_moved_early_is_recovered_not_a_mutual_wait(self):
        """Task 160 (#157/#158): the second member moved into In Progress
        by mistake, with no run and no branch. The first member still
        launches, the second never launches before the first is
        integrated, both readings name the mistake, and the person's
        move back to To Do is allowed and launches nothing."""
        j, phase, one, two, solo = self.phase_board()
        j.step("move", file=two, **{"from": "backlog", "to": "in-progress"})
        first = j.step("pass")
        self.assertIn(one, [c["task"] for c in j.calls("work")],
                      [(r.get("card"), r.get("outcome"), r.get("code"))
                       for r in first["pass"].get("results", [])])
        self.assertNotIn(two, [c["task"] for c in j.calls("work")])
        reading = j.step("read")
        entry = next(p for p in reading["phases"]["phases"] if p["card"] == phase)
        rows = {m["card"]: m["state"] for m in entry["members"]}
        self.assertEqual("moved-early", rows[two], rows)
        self.assertEqual("you", entry["lifecycle"]["next"]["who"])
        self.assertIn(two[:2], entry["lifecycle"]["next"]["text"])
        j.step("move", file=two, **{"from": "in-progress", "to": "to-do"})
        after = j.step("read")
        self.assertEqual("to-do", after["stages"][two])
        self.assertEqual(1, [c["task"] for c in j.calls("work")].count(one),
                         "no duplicate launch of the first member")
        self.assertNotIn(two, [c["task"] for c in j.calls("work")],
                         "moving it back launched nothing")

    def test_dependent_work_builds_on_a_prerequisite_in_review(self):
        # task 150: A is in Review and not merged (merge is not delegated);
        # B declares it and is picked up — its decision is recorded before
        # anything is provisioned, its branch holds A's committed work, its
        # PR targets A's branch, and further passes decide nothing twice
        j = self.journey()
        api = j.card("40", "The API", author="bob")
        j.step("move", file=api, **{"from": "backlog", "to": "to-do"})
        j.step("enable", agreement={"authority": [w for w in ALL if w != "merge"]})
        j.until(lambda r: r["stages"].get(api) == "review")
        ui = j.card("41", "The UI", author="bob", extra="**Depends on:** 40\n")
        j.step("move", file=ui, **{"from": "backlog", "to": "to-do"})
        j.until(lambda r: r["stages"].get(ui) == "review")

        a_branch, b_branch = f"task/{api[:-3]}", f"task/{ui[:-3]}"
        text = j.card_text(ui)
        self.assertEqual(text.count('"kind": "base"'), 1, text)
        self.assertIn('"decision": "stacked"', text)
        self.assertIn(a_branch, text)
        # B's published branch holds A's committed work; main does not
        self.assertTrue(j.remote_has("card-40.txt", ref=b_branch))
        self.assertTrue(j.remote_has("card-41.txt", ref=b_branch))
        self.assertFalse(j.remote_has("card-40.txt"), "A was not merged to start B")
        # B's pull request targets A's branch, so its review is B's change
        prs = j.host._load()["prs"].values()
        self.assertEqual([p["base"] for p in prs if p["head"] == b_branch], [a_branch])
        # more passes: no second decision, no second run of B
        j.step("pass")
        j.step("pass")
        self.assertEqual(j.card_text(ui).count('"kind": "base"'), 1)
        self.assertEqual([c["task"] for c in j.calls("work")].count(ui), 1)


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
