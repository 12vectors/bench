"""Task 182: global orchestration capacity, shared first come, first served.

The model this holds:

- the agreement's capacity is global pools (implementation with repairs,
  review, reasoning) and daily starts, used by standalone and phase work
  alike, and only while a run holds a slot; a phase inherits a default
  *ceiling* (`scopeDefaults`) or its own `limits`, neither of which
  reserves anything;
- the legacy reserved model keeps its exact behaviour until a person
  migrates it, through a revision-guarded preview and apply that touches
  nothing but the agreement document;
- eligible launch requests wait in a durable queue and are admitted in
  the order they became ready, never by priority or kind, past requests
  that cannot move (a saturated scope, a hold) without losing their place;
- admission is atomic: a capacity reading and the reserving intent are
  one step, so concurrent callers never oversubscribe a pool or a scope;
- slots are released only by observed endings and settled refusals, and
  daily starts are never refunded.
"""

from __future__ import annotations

import json
import threading
import unittest

from tests.test_orchestration_scheduler import Sandbox, agreement  # noqa: E402

import agreements  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402


def legacy_doc(**phases) -> dict:
    """The live diagnosis (task 182): eight implementation slots, seven of
    them reserved for named phases."""
    doc = agreement(capacity={"implementation": 8, "review": 4,
                              "reasoning": 2, "dailyStarts": 40})
    doc["bench"]["capacityModel"] = agreements.RESERVED
    doc["bench"].pop("scopeDefaults", None)
    doc["phases"] = phases or {
        "156": {"enabled": True, "allocation": {"implementation": 3,
                                                "review": 3, "reasoning": 2}},
        "167": {"enabled": True, "allocation": {"implementation": 2}},
        "170": {"enabled": True, "allocation": {"implementation": 2}},
    }
    return doc


class Case(Sandbox):
    def run_agent(self, card: str, mode: str = "work", agent: str | None = None):
        agent = agent or f"manual-{card}-{mode}"
        state.AGENTS[agent] = {"task": card, "mode": mode, "status": "running",
                               "started": 0}
        return agent

    def apply_doc(self, doc: dict) -> str:
        agreements.save_draft(doc, agreements.revision(agreements.draft_document()[0]))
        return agreements.apply(agreements.effective()["revision"],
                                agreements.revision(doc))["revision"]

    def shared(self, capacity=None, defaults=None, phases=None) -> str:
        doc = agreement(capacity=capacity or {"implementation": 2})
        if defaults:
            doc["bench"]["scopeDefaults"].update(defaults)
        doc["phases"] = phases or {}
        return self.apply_doc(doc)

    def scoped_phase(self, number: str, members=(), stage="in-progress"):
        self.board.add(number, stage=stage, is_phase=True)
        names = []
        for index, member in enumerate(members, start=1):
            names.append(self.board.add(member, stage="to-do", phase=number,
                                        index=index))
        return names


class TheSchema(Case):
    def test_a_new_draft_shares_capacity_with_default_ceilings(self):
        draft = agreements.new_draft()
        self.assertEqual("shared", draft["bench"]["capacityModel"])
        self.assertEqual(agreements.SCOPE_DEFAULTS, draft["bench"]["scopeDefaults"])
        self.assertTrue(agreements.validate(draft)["ok"])

    def test_an_allocation_cannot_come_back_under_shared_capacity(self):
        doc = agreement()
        doc["phases"] = {"9": {"enabled": True, "allocation": {"implementation": 1}}}
        codes = [p["code"] for p in agreements.validate(doc)["problems"]]
        self.assertEqual(["allocation-retired"], codes)

    def test_limits_and_defaults_are_refused_in_the_reserved_model(self):
        doc = legacy_doc()
        doc["phases"]["156"]["limits"] = {"implementation": 1}
        doc["bench"]["scopeDefaults"] = {"implementation": 1}
        codes = sorted(p["code"] for p in agreements.validate(doc)["problems"])
        self.assertEqual(["capacity-model-mismatch"] * 2, codes)

    def test_phases_inherit_the_default_ceiling_and_reserve_nothing(self):
        doc = agreement(capacity={"implementation": 8})
        doc["bench"]["scopeDefaults"]["implementation"] = 2
        doc["phases"] = {"9": {"enabled": True},
                         "10": {"enabled": True, "limits": {"implementation": 5}},
                         "11": {"enabled": True, "limits": {"implementation": 20}}}
        found = agreements.resolve(doc)
        self.assertTrue(found["valid"], found["problems"])
        bench = agreements.scope(found, "bench")["capacity"]["implementation"]
        self.assertEqual((8, 8, 0), (bench["total"], bench["own"], bench["allocated"]))
        nine = agreements.scope(found, "phase:9")["capacity"]["implementation"]
        self.assertEqual((8, 2, "default"), (nine["total"], nine["ceiling"], nine["by"]))
        ten = agreements.scope(found, "phase:10")["capacity"]["implementation"]
        self.assertEqual((5, "phase:10"), (ten["ceiling"], ten["by"]))
        eleven = agreements.scope(found, "phase:11")["capacity"]["implementation"]
        self.assertEqual(8, eleven["ceiling"], "the global total always wins")
        self.assertEqual("shared", found["capacityModel"])

    def test_preview_apply_and_the_effective_reading_agree(self):
        doc = agreement(capacity={"implementation": 6})
        doc["bench"]["scopeDefaults"]["implementation"] = 3
        agreements.save_draft(doc, "none")
        preview = agreements.preview()
        self.assertTrue(preview["valid"])
        self.assertIn("capacityModel", [c["what"] for c in preview["changes"]])
        applied = agreements.apply("none", preview["draft"])
        self.assertEqual(preview["draft"], applied["revision"])
        found = agreements.effective()
        self.assertEqual({"implementation": 3, "review": 1, "reasoning": 1,
                          "dailyStarts": 20}, found["scopeDefaults"])
        reading = orchestrator.reading(self.board.collect())
        self.assertEqual("shared", reading["effective"]["capacityModel"])
        self.assertFalse(reading["effective"]["migrationNeeded"])


class EightTotalSevenReserved(Case):
    """The live symptom, reproduced, then migrated away."""

    def setUp(self):
        super().setUp()
        self.board.add("156", stage="done", is_phase=True)      # delivered
        self.board.add("167", stage="backlog", is_phase=True)   # unadopted
        self.board.cards["167-card.md"]["phaseMode"] = None
        self.board.add("170", stage="to-do", is_phase=True)     # idle
        self.board.add("174", stage="review")
        for number in ("176", "177", "178"):
            self.board.add(number)
        self.apply_doc(legacy_doc())
        self.own_execution()
        self.run_agent("174-card.md", mode="act-pr")            # a PR repair

    def test_legacy_reservations_strand_seven_slots(self):
        answer = orchestrator.authorize("implement", "176-card.md")
        self.assertEqual("slots-full", answer["code"])
        self.assertIn("(1 of 1)", answer["reason"])
        reading = orchestrator.reading(self.board.collect())
        self.assertTrue(reading["effective"]["migrationNeeded"])
        self.assertEqual({"implementation": 3, "review": 3, "reasoning": 2},
                         reading["effective"]["legacyAllocations"]["156"])

    def test_migrated_shared_capacity_admits_unrelated_work(self):
        before = agreements.effective()["revision"]
        preview = agreements.migration_preview()
        self.assertTrue(preview["needed"])
        self.assertEqual(["156", "167", "170"], [p["phase"] for p in preview["phases"]])
        self.assertEqual(before, agreements.effective()["revision"],
                         "a preview writes nothing")
        agreements.migrate(before)
        found = agreements.effective()
        self.assertEqual("shared", found["capacityModel"])
        self.assertTrue(found["valid"], found["problems"])
        result = self.tick()
        launched = sorted(r["card"] for r in result["results"]
                          if r.get("outcome") == "launched")
        self.assertEqual(["176-card.md", "177-card.md", "178-card.md"], launched)
        used = orchestrator.usage(found, scheduler._tasks(self.board.collect()))
        for phase in ("phase:156", "phase:167", "phase:170"):
            self.assertEqual({}, used["running"].get(phase, {}),
                             f"{phase} holds no slot")
        self.assertEqual(4, used["totals"]["implementation"],
                         "the Review repair and three new runs")
        holders = {h["card"]: h for h in used["holders"] if h["kind"] == "run"}
        self.assertEqual("review", holders["174-card.md"]["stage"])
        self.assertEqual("implementation", holders["174-card.md"]["pool"])


class APhaseUsesWhatItRuns(Case):
    def test_one_running_member_holds_one_slot_not_its_ceiling(self):
        self.shared(capacity={"implementation": 3},
                    defaults={"implementation": 2},
                    phases={"9": {"enabled": True}})
        self.own_execution()
        first, second, third = self.scoped_phase("9", ("91", "92", "93"))
        self.run_agent(first)
        self.board.cards[first]["stage"] = "in-progress"
        for name in ("1", "2"):
            self.board.add(name)
        # the phase may use one more; the Bench scope may use both free slots
        self.assertTrue(orchestrator.authorize("implement", second)["allowed"])
        self.assertTrue(orchestrator.authorize("implement", "1-card.md")["allowed"])
        self.run_agent(second)
        refused = orchestrator.authorize("implement", third)
        self.assertEqual(("slots-full", "scope"), (refused["code"], refused["limit"]))
        self.assertTrue(orchestrator.authorize("implement", "1-card.md")["allowed"],
                        "a saturated phase reserves nothing it is not running")
        self.run_agent("1-card.md")
        self.assertEqual("global", orchestrator.authorize(
            "implement", "2-card.md")["limit"])

    def test_lowering_a_limit_below_usage_stops_admission_and_kills_nothing(self):
        revision = self.shared(capacity={"implementation": 4},
                               phases={"9": {"enabled": True}})
        self.own_execution()
        first, second, third = self.scoped_phase("9", ("91", "92", "93"))
        self.run_agent(first)
        self.run_agent(second)
        doc = json.loads(agreements.document_path().read_text())
        doc["phases"]["9"]["limits"] = {"implementation": 1}
        agreements.save_draft(doc, "none")
        agreements.apply(revision, agreements.revision(doc))
        self.assertEqual("slots-full",
                         orchestrator.authorize("implement", third)["code"])
        self.assertEqual(2, len(self.running()), "nothing is cancelled")


class FirstComeFirstServed(Case):
    def test_an_older_request_goes_before_a_later_high_priority_one(self):
        self.shared(capacity={"implementation": 1})
        self.own_execution()
        # the slot holder: a run the scheduler will not relaunch when it ends
        self.board.add("1", stage="done")
        self.run_agent("1-card.md")
        self.board.add("5", priority="Low")
        self.tick()                                   # 5 waits, queued first
        self.board.add("2", priority="High")
        self.tick()                                   # 2 queues behind it
        order = [e["file"] for e in operations.queue_entries()]
        self.assertEqual(["5-card.md", "2-card.md"], order)
        self.end_runs()
        result = self.tick()
        launched = [r["card"] for r in result["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(["5-card.md"], launched)
        self.assertEqual(["2-card.md"], [e["file"] for e in operations.queue_entries()])

    def test_the_queue_survives_a_restart_and_a_reread(self):
        self.shared(capacity={"implementation": 1})
        self.own_execution()
        self.board.add("1", stage="in-progress")
        self.run_agent("1-card.md")
        self.board.add("9")
        self.tick()
        self.board.add("3")
        self.tick()
        before = operations.queue_entries()
        # a restart: nothing in memory but the files
        scheduler.LAST["pass"] = None
        for _ in range(3):
            self.tick()
            scheduler.projection(self.board.collect())
        after = operations.queue_entries()
        self.assertEqual([(e["id"], e["seq"], e["readyEpoch"]) for e in before],
                         [(e["id"], e["seq"], e["readyEpoch"]) for e in after])
        self.assertEqual(["9-card.md", "3-card.md"], [e["file"] for e in after])

    def test_a_repair_does_not_overtake_an_older_implementation(self):
        self.shared(capacity={"implementation": 1})
        self.own_execution()
        self.board.add("1", stage="in-progress")
        self.run_agent("1-card.md")
        self.board.add("4")
        self.tick()                                   # 4 waits first
        self.board.add("2", stage="review")
        repair = {"card": "2-card.md", "number": "2", "stage": "review",
                  "scope": "bench", "state": "ready", "action": "repair",
                  "code": "repair", "head": "a" * 40}
        self.end_runs()
        found = scheduler.plan(self.board.collect(), {"items": [dict(repair)]},
                               sync=True)
        self.assertEqual([{"card": "4-card.md", "action": "implement"}],
                         found["admitted"])
        queued = {e["file"]: e for e in operations.queue_entries()}
        self.assertEqual("repair", queued["2-card.md"]["action"])
        self.assertEqual("implementation", queued["2-card.md"]["pool"])
        # admitted out of turn it would be refused, naming who goes first
        answer = orchestrator.authorize(
            "repair", "2-card.md",
            request=operations.request_id("repair", "2-card.md", "a" * 40))
        self.assertEqual("queued-behind", answer["code"])

    def test_a_saturated_scope_does_not_block_unrelated_work(self):
        self.shared(capacity={"implementation": 3},
                    defaults={"implementation": 1},
                    phases={"9": {"enabled": True, "strategy": "dependencies"}})
        self.own_execution()
        first, second = self.scoped_phase("9", ("91", "92"))
        self.board.cards[first]["stage"] = "in-progress"
        self.run_agent(first)
        self.tick()                      # 92 is eligible but its scope is full
        queued = [e["file"] for e in operations.queue_entries()]
        self.assertEqual([second], queued)
        self.board.add("7")
        result = self.tick()
        launched = [r["card"] for r in result["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(["7-card.md"], launched, "no head-of-line blocking")
        self.assertEqual([second], [e["file"] for e in operations.queue_entries()])

    def test_a_hold_keeps_its_place_and_blocks_nobody(self):
        revision = self.shared(capacity={"implementation": 1},
                               phases={"9": {"enabled": True,
                                             "strategy": "dependencies"}})
        self.own_execution()
        (member,) = self.scoped_phase("9", ("91",))
        self.board.add("1", stage="in-progress")
        self.run_agent("1-card.md")
        self.tick()                                    # 91 queued first
        revision = agreements.set_scope("phase:9", "pause", revision)["revision"]
        self.board.add("5")
        self.tick()                                    # 5 queued behind
        entries = {e["file"]: e for e in operations.queue_entries()}
        self.assertEqual("paused", entries[member]["hold"])
        self.end_runs()
        result = self.tick()
        launched = [r["card"] for r in result["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual(["5-card.md"], launched, "a hold blocks nobody")
        self.assertIn(member, [e["file"] for e in operations.queue_entries()])
        agreements.set_scope("phase:9", "resume", revision)
        self.board.add("6")
        self.tick()
        self.end_runs()
        result = self.tick()
        launched = [r["card"] for r in result["results"]
                    if r.get("outcome") == "launched"]
        self.assertEqual([member], launched, "resumed, it keeps its older place")

    def test_a_request_that_leaves_comes_back_at_the_back(self):
        self.shared(capacity={"implementation": 1})
        self.own_execution()
        self.board.add("1", stage="in-progress")
        self.run_agent("1-card.md")
        self.board.add("3")
        self.tick()
        self.board.add("4")
        self.tick()
        self.board.cards["3-card.md"]["stage"] = "backlog"   # not wanted now
        self.tick()
        self.assertEqual(["4-card.md"], [e["file"] for e in operations.queue_entries()])
        self.board.cards["3-card.md"]["stage"] = "to-do"
        self.tick()
        self.assertEqual(["4-card.md", "3-card.md"],
                         [e["file"] for e in operations.queue_entries()])

    def test_positions_are_shown_on_the_schedule(self):
        self.shared(capacity={"implementation": 1})
        self.own_execution()
        self.board.add("1", stage="in-progress")
        self.run_agent("1-card.md")
        self.board.add("3")
        self.board.add("4")
        self.tick()
        found = scheduler.projection(self.board.collect())
        places = {i["card"]: i.get("queue") for i in found["items"]}
        self.assertEqual(1, places["3-card.md"]["position"])
        self.assertEqual(2, places["4-card.md"]["position"])
        self.assertEqual([1, 2], [q["position"] for q in found["queue"]])


class AtomicAdmission(Case):
    def test_concurrent_admissions_never_exceed_the_global_total(self):
        self.shared(capacity={"implementation": 2})
        self.own_execution()
        names = [self.board.add(str(n)) for n in range(10, 18)]
        results, barrier = [], threading.Barrier(len(names))

        def go(name):
            barrier.wait()
            key = operations.key_for("implement", name, "b", 1)
            answer, op = orchestrator.admit("implement", name, key,
                                            number=name.split("-")[0],
                                            pool="implementation", basis="b",
                                            attempt=1)
            results.append(op is not None)

        threads = [threading.Thread(target=go, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(2, sum(results))
        self.assertEqual(2, len(self.ops("intent")))

    def test_a_phase_ceiling_holds_under_concurrency_too(self):
        self.shared(capacity={"implementation": 6},
                    defaults={"implementation": 1},
                    phases={"9": {"enabled": True}})
        self.own_execution()
        members = self.scoped_phase("9", ("91", "92", "93", "94"))
        results, barrier = [], threading.Barrier(len(members))

        def go(name):
            barrier.wait()
            _answer, op = orchestrator.admit(
                "implement", name, operations.key_for("implement", name, "b", 1),
                number=name.split("-")[0], pool="implementation", basis="b",
                attempt=1)
            results.append(op is not None)

        threads = [threading.Thread(target=go, args=(n,)) for n in members]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(1, sum(results))

    def test_one_request_is_never_admitted_twice(self):
        self.shared()
        self.own_execution()
        name = self.board.add("1")
        key = operations.key_for("implement", name, "b", 1)
        orchestrator.admit("implement", name, key, number="1",
                           pool="implementation", basis="b", attempt=1)
        with self.assertRaises(operations.Exists):
            orchestrator.admit("implement", name, key, number="1",
                               pool="implementation", basis="b", attempt=1)

    def test_manual_and_legacy_runner_runs_are_counted(self):
        self.shared(capacity={"implementation": 2})
        self.own_execution()
        self.board.add("1")
        self.board.add("2", stage="in-progress")
        self.board.add("3", stage="in-progress", phase="40", index=1)
        self.board.cards["3-card.md"]["phase"]["mode"] = None   # runner's
        self.run_agent("2-card.md")          # a person's ▸ start work
        self.run_agent("3-card.md")          # the legacy phase runner's member
        answer = orchestrator.authorize("implement", "1-card.md")
        self.assertEqual(("slots-full", "global"), (answer["code"], answer["limit"]))

    def test_review_and_reasoning_have_their_own_pools(self):
        self.shared(capacity={"implementation": 1, "review": 1, "reasoning": 0})
        self.own_execution()
        self.board.add("1", stage="review")
        self.board.add("2")
        self.run_agent("1-card.md", mode="review")
        self.assertTrue(orchestrator.authorize("implement", "2-card.md")["allowed"])
        self.assertEqual("slots-full",
                         orchestrator.authorize("review", "1-card.md")["code"])
        # no reasoning pool: a diagnosis takes an implementation slot
        here = agreements.scope(agreements.effective(), "bench")
        self.assertEqual("implementation", orchestrator.pool_for("reasoning", here))
        self.run_agent("2-card.md")
        self.assertEqual("slots-full",
                         orchestrator.authorize("recover", "2-card.md")["code"])


class ReleaseAndAccounting(Case):
    def admitted(self, name: str) -> dict:
        _answer, op = orchestrator.admit(
            "implement", name, operations.key_for("implement", name, "b", 1),
            number=name.split("-")[0], pool="implementation", basis="b",
            attempt=1)
        self.assertIsNotNone(op)
        return op

    def test_slots_follow_outcomes_and_starts_are_never_refunded(self):
        self.shared(capacity={"implementation": 1, "dailyStarts": 3})
        self.own_execution()
        one, two = self.board.add("1"), self.board.add("2")
        op = self.admitted(one)
        self.assertEqual("slots-full", orchestrator.authorize("implement", two)["code"])
        operations.update(op["key"], "unknown")           # held: keeps it
        self.assertEqual("slots-full", orchestrator.authorize("implement", two)["code"])
        operations.update(op["key"], "unobserved", alive=True)
        self.assertEqual("slots-full", orchestrator.authorize("implement", two)["code"])
        operations.update(op["key"], "ended", outcome="done")   # observed ending
        self.assertTrue(orchestrator.authorize("implement", two)["allowed"])
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(1, used["startsTodayTotal"], "a finished run spent its start")
        refused = self.admitted(two)
        operations.update(refused["key"], "refused", code="launch-refused")
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(0, used["totals"]["implementation"])
        self.assertEqual(1, used["startsTodayTotal"], "a refusal ran nothing")


class Migration(Case):
    def setUp(self):
        super().setUp()
        self.board.add("1")
        self.revision = self.apply_doc(legacy_doc(**{
            "9": {"enabled": True, "paused": True, "strategy": "dependencies",
                  "exclusions": ["12"], "handback": ["13"],
                  "allocation": {"implementation": 2, "dailyStarts": 0}}}))
        self.own_execution()

    def test_passive_reads_migrate_nothing(self):
        raw = agreements.document_path().read_bytes()
        agreements.effective()
        orchestrator.reading(self.board.collect())
        scheduler.projection(self.board.collect())
        agreements.migration_preview()
        self.assertEqual(raw, agreements.document_path().read_bytes())

    def test_migration_keeps_everything_but_the_reservation(self):
        key = operations.key_for("implement", "1-card.md", "b", 1)
        orchestrator.admit("implement", "1-card.md", key, number="1",
                           pool="implementation", basis="b", attempt=1)
        orchestrator.record_launch("bench", "1", "implement", key)
        used_before = orchestrator.usage(agreements.effective(),
                                         scheduler._tasks(self.board.collect()))
        history = agreements.history_path().read_text().splitlines()
        result = agreements.migrate(self.revision, keep_limits=True)
        doc = json.loads(agreements.document_path().read_text())
        phase = doc["phases"]["9"]
        self.assertNotIn("allocation", phase)
        self.assertEqual({"implementation": 2, "dailyStarts": 0}, phase["limits"])
        for key_ in ("paused", "strategy", "exclusions", "handback"):
            self.assertIn(key_, phase)
        self.assertTrue(all(doc["bench"]["authority"].values()))
        self.assertEqual("open", "open" if operations.read(key)["status"] == "intent"
                         else "settled")
        used_after = orchestrator.usage(agreements.effective(),
                                        scheduler._tasks(self.board.collect()))
        self.assertEqual(used_before["startsTodayTotal"], used_after["startsTodayTotal"])
        self.assertEqual(used_before["launchesPerCard"], used_after["launchesPerCard"])
        self.assertEqual(used_before["totals"], used_after["totals"])
        lines = agreements.history_path().read_text().splitlines()
        self.assertEqual(history, lines[:-1], "history is appended, never reset")
        self.assertEqual("migrate-shared-capacity", json.loads(lines[-1])["action"])
        self.assertEqual(["9"], [p["phase"] for p in result["phases"]])

    def test_relying_on_defaults_drops_the_old_values(self):
        agreements.migrate(self.revision)
        doc = json.loads(agreements.document_path().read_text())
        self.assertNotIn("limits", doc["phases"]["9"])
        self.assertEqual(agreements.SCOPE_DEFAULTS, doc["bench"]["scopeDefaults"])

    def test_a_stale_or_repeated_migration_writes_nothing(self):
        raw = agreements.document_path().read_bytes()
        with self.assertRaises(agreements.Refused) as stale:
            agreements.migrate("0" * 16)
        self.assertEqual(409, stale.exception.status)
        self.assertEqual(raw, agreements.document_path().read_bytes())
        agreements.migrate(self.revision)
        with self.assertRaises(agreements.Refused) as again:
            agreements.migrate(agreements.effective()["revision"])
        self.assertEqual("already-shared", again.exception.extra.get("code"))
        self.assertFalse(agreements.migration_preview()["needed"])

    def test_an_old_reserved_draft_cannot_bring_reservations_back(self):
        old = json.loads(agreements.document_path().read_text())
        agreements.migrate(self.revision)
        raw = agreements.document_path().read_bytes()
        agreements.save_draft(old, "none")
        with self.assertRaises(agreements.Refused) as refused:
            agreements.apply(agreements.effective()["revision"],
                             agreements.revision(old))
        self.assertEqual("capacity-model-downgrade",
                         refused.exception.extra.get("code"))
        self.assertEqual(raw, agreements.document_path().read_bytes())


if __name__ == "__main__":
    unittest.main()
