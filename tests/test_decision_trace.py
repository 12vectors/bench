"""The orchestrator's durable decision trace (task 184).

Each class holds one acceptance criterion of the card:

- successive passes selecting implementation, review and repair keep a
  timestamped, scoped reason with the facts read, and link the decision
  to its intent, receipt and outcome — a selection is never called a
  launch merely because it was chosen;
- no eligible work, dependency waits, exhausted capacity, daily starts
  and card launches, an authority refusal, a pause, an off agreement and
  a replica each record their actual reason, without changing what the
  scheduler does;
- a candidate selected at planning that loses authority before execution
  keeps both its selection and the refusal; an uncertain launch stays
  unknown and is never launched twice;
- every evaluated pass is represented across identical idle repetitions
  and a restart, with exact counts and ids; recording failures, retention
  boundaries, malformed lines and seq gaps are named, and nothing older
  than the first record is invented;
- a later change of the card or the agreement cannot change what an
  earlier record says, and paging by seq is stable under appends;
- reading the trace is passive, payloads are bounded and scrubbed, and
  idle passes add no card history.

Every launch is a stand-in for `agents.start_agent` (see
tests/test_orchestration_scheduler.py, whose sandbox this reuses).

    python3 -m unittest tests.test_decision_trace -v
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from tests.test_orchestration_scheduler import (  # noqa: E402
    Sandbox, agreement, snapshot)

import agreements  # noqa: E402
import coordinate  # noqa: E402
import decisiontrace  # noqa: E402
import execowner  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402


class TraceSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        decisiontrace._CACHE.clear()
        decisiontrace.HEALTH.update({"failures": 0, "since": None,
                                     "lastError": None, "lastOk": None})

    def entries(self, **filters) -> list[dict]:
        return decisiontrace.page(limit=100, **filters)["entries"]

    def newest(self) -> dict:
        return self.entries()[0]

    def decision(self, entry: dict, card: str, action: str = "implement") -> dict:
        return next(d for d in entry["decisions"]
                    if d.get("card") == card and d.get("action") == action)


# ── 1. selection → intent → receipt → outcome ────────────────────────

class SelectionsAreLinkedToWhatHappened(TraceSandbox):
    def test_an_implementation_launch_is_traced_and_linked(self):
        self.enable()
        self.board.add("1", priority="High")
        self.tick()
        entry = self.newest()
        self.assertEqual("evaluated", entry["status"])
        self.assertEqual("action", entry["outcome"])
        d = self.decision(entry, "1-card.md")
        self.assertTrue(d["selected"])
        self.assertEqual("chosen", d["state"])
        self.assertEqual("launched", d["outcome"])
        self.assertEqual("bench", d["scope"])
        self.assertEqual("High", d["facts"]["priority"])
        self.assertIn("basis", d["facts"])        # the brief it was decided on
        [op] = self.ops()
        self.assertEqual(op["key"], d["result"]["key"])
        # the ledger holds intent then receipt; the card holds both notes
        self.assertEqual("launched", op["status"])
        phases = [(n["phase"], n["outcome"]) for n in self.notes
                  if n.get("operation") == op["key"]]
        self.assertEqual([("intent", "intended"), ("receipt", "launched")], phases)
        self.assertTrue(entry["at"].endswith("Z"))
        self.assertEqual(execowner.installation_id(), entry["owner"]["installation"])
        self.assertEqual(agreements.effective()["revision"],
                         entry["agreement"]["revision"])
        # the run ends: a later pass observes it; the decision is not rewritten
        self.end_runs()
        self.tick()
        again = decisiontrace.entry(entry["id"])
        self.assertEqual("launched", self.decision(again, "1-card.md")["outcome"])
        observed = self.newest()["observed"]
        self.assertEqual([(op["key"], "ended", "done")],
                         [(o["key"], o["status"], o["outcome"]) for o in observed])

    def test_review_and_repair_selections_are_traced(self):
        self.enable(capacity={"review": 1})
        self.board.add("5", stage="review")
        steps = iter(["review", "repair"])
        executed = []

        def plan(board=None):
            action = next(steps, None)
            if action is None:
                return {"status": "on", "items": []}
            return {"status": "on", "items": [{
                "card": "5-card.md", "number": "5", "stage": "review",
                "scope": "bench", "action": action, "state": "ready",
                "code": "ready", "head": "a" * 40,
                "reason": f"the next delivery step is {action}"}]}

        def execute(item, task, board=None):
            executed.append(item["action"])
            return {"card": item["card"], "action": item["action"],
                    "outcome": "launched", "key": "op-" + "1" * 20}

        with mock.patch.object(coordinate, "plan", plan), \
                mock.patch.object(coordinate, "execute", execute), \
                mock.patch.object(coordinate, "run", lambda *a, **k: []):
            self.tick()
            self.tick()
        self.assertEqual(["review", "repair"], executed)
        repair, review = self.entries()[:2]
        for entry, action in ((review, "review"), (repair, "repair")):
            d = self.decision(entry, "5-card.md", action)
            self.assertEqual("delivery", d["kind"])
            self.assertTrue(d["selected"])
            self.assertEqual("launched", d["outcome"])
            self.assertEqual("a" * 40, d["facts"]["head"])
            self.assertIn(action, d["reason"])

    def test_a_chosen_card_whose_launch_is_refused_is_not_called_launched(self):
        self.enable()
        self.board.add("1")
        self.launch.fail = ValueError("the plan's preflight refused it")
        self.tick()
        d = self.decision(self.newest(), "1-card.md")
        self.assertTrue(d["selected"])
        self.assertEqual("chosen", d["state"])
        self.assertEqual("refused", d["outcome"])
        self.assertIn("preflight", d["result"]["reason"])


# ── 2. why nothing happened ───────────────────────────────────────────

class NoActionReasons(TraceSandbox):
    def code(self, card: str) -> tuple:
        d = self.decision(self.newest(), card)
        return d["code"], d["outcome"]

    def test_no_eligible_work(self):
        self.enable()
        self.tick()
        entry = self.newest()
        self.assertEqual("no-action", entry["outcome"])
        self.assertEqual([], entry["decisions"])
        self.assertIn("Wait / No action", entry["headline"])

    def test_dependency_wait(self):
        self.enable()
        self.board.add("7", stage="backlog")
        self.board.add("6", depends=["7"])
        self.tick()
        self.assertEqual(("waiting-dependency", "blocked"), self.code("6-card.md"))
        self.assertIn("card 7", self.decision(self.newest(), "6-card.md")["reason"])

    def test_capacity_daily_starts_and_card_launches(self):
        self.enable(capacity={"implementation": 1})
        self.board.add("1")
        self.board.add("2")
        self.tick()
        self.assertEqual(("slots-full", "waiting"), self.code("2-card.md"))
        # capacity as read at decision time, before this pass's launch
        capacity = self.newest()["capacity"]
        self.assertEqual(1, capacity["limits"]["implementation"])
        self.assertEqual(0, capacity["inUse"]["implementation"])

    def test_daily_starts_exhausted(self):
        self.enable(capacity={"dailyStarts": 1, "implementation": 3})
        self.board.add("1")
        self.board.add("2")
        self.tick()
        self.assertEqual(("daily-starts-exhausted", "waiting"),
                         self.code("2-card.md"))

    def test_card_launches_exhausted(self):
        self.enable(capacity={"launchesPerCard": 1})
        self.board.add("1")
        self.tick()
        self.end_runs()
        # a changed brief is a new question, but the card's one launch is spent
        self.board.cards["1-card.md"]["stage"] = "to-do"
        self.board.cards["1-card.md"]["body"] = "# 1\n\nA new brief.\n"
        self.tick()
        self.assertEqual(("card-launches-exhausted", "blocked"),
                         self.code("1-card.md"))
        self.assertEqual(1, len(self.launch.calls))

    def test_authority_refusal(self):
        self.enable(authority={"implement": False})
        self.board.add("1")
        self.tick()
        code, outcome = self.code("1-card.md")
        self.assertEqual("blocked", outcome)
        self.assertIn("implement", self.decision(self.newest(), "1-card.md")["reason"]
                      + code)

    def test_pause_is_evaluated_and_recorded(self):
        self.enable(paused=True)
        self.board.add("1")
        self.tick()
        self.assertEqual("paused", self.code("1-card.md")[0])
        self.assertEqual([], self.launch.calls)

    def test_off_and_replica_are_recorded_as_not_evaluated(self):
        doc = agreement()
        doc["bench"]["enabled"] = False
        self.put_in_force(doc)
        self.own_execution()
        self.tick()
        off = self.newest()
        self.assertEqual(("skipped", "bench-disabled"), (off["status"], off["why"]))
        self.assertEqual("skipped", off["outcome"])
        self.put_in_force(agreement())
        with mock.patch.object(execowner, "authorizes", lambda: {
                "allowed": False, "code": "replica",
                "reason": "another installation executes"}):
            self.tick()
        replica = self.newest()
        self.assertEqual(("skipped", "replica"), (replica["status"], replica["why"]))
        self.assertIn("another installation", replica["reason"])

    def test_no_agreement_at_all_records_nothing(self):
        self.tick()
        self.assertEqual([], self.entries())
        self.assertFalse(decisiontrace.trace_dir().exists())

    def test_tracing_changes_no_scheduling_decision(self):
        def run(record: bool) -> tuple:
            self.launch.calls.clear()
            state.AGENTS.clear()
            for op in operations.all_ops():
                (operations.ops_dir() / f"{op['key']}.json").unlink()
            if operations.queue_path().exists():
                operations.queue_path().unlink()
            patch = mock.patch.object(decisiontrace, "append",
                                      decisiontrace.append if record
                                      else (lambda *a, **k: None))
            with patch:
                self.tick()
            return (list(self.launch.calls),
                    sorted((o["card"], o["status"]) for o in operations.all_ops()))

        self.enable(capacity={"implementation": 2})
        for n in ("1", "2", "3"):
            self.board.add(n)
        quiet = run(False)
        for card in self.board.cards.values():
            card["stage"] = "to-do"
        traced = run(True)
        self.assertEqual(quiet, traced)
        self.assertEqual(2, len(traced[0]))


# ── 3. revalidation and uncertain launches ────────────────────────────

class RevalidationKeepsBothHalves(TraceSandbox):
    def test_authority_lost_between_plan_and_execute(self):
        self.enable()
        self.board.add("1")
        real_plan = scheduler.plan

        def plan(*a, **k):
            found = real_plan(*a, **k)
            if k.get("sync"):
                self.eligible = lambda task: {"eligible": False,
                                              "code": "foreign-move",
                                              "via": "other",
                                              "reason": "ada moved it last"}
            return found

        with mock.patch.object(scheduler, "plan", plan):
            self.tick()
        d = self.decision(self.newest(), "1-card.md")
        self.assertEqual("chosen", d["state"])        # what planning said
        self.assertTrue(d["selected"])
        self.assertEqual("refused", d["outcome"])     # what execution found
        self.assertEqual([], self.launch.calls)

    def test_an_uncertain_launch_stays_unknown_and_is_not_repeated(self):
        self.enable()
        self.board.add("1")
        self.launch.fail = RuntimeError("the reply was lost")
        self.tick()
        first = self.decision(self.newest(), "1-card.md")
        self.assertEqual("unknown", first["outcome"])
        self.launch.fail = None
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        held = self.decision(self.newest(), "1-card.md")
        self.assertEqual(("outcome-unknown", "held"), (held["code"], held["outcome"]))
        self.assertEqual(1, len(self.ops()))


# ── 4. every pass, compacted losslessly; gaps named ───────────────────

class EveryPassIsRepresented(TraceSandbox):
    def idle_board(self):
        self.enable()
        self.board.add("7", stage="backlog")
        self.board.add("6", depends=["7"])

    def test_identical_idle_passes_fold_with_exact_counts_and_ids(self):
        self.idle_board()
        for _ in range(5):
            self.tick()
        entries = self.entries()
        self.assertEqual(1, len(entries))
        occ = entries[0]["occurrences"]
        self.assertEqual(5, occ["count"])
        self.assertEqual(occ["lastSeq"] - occ["firstSeq"] + 1, 5)
        full = decisiontrace.entry(entries[0]["id"])
        ids = [p["id"] for p in full["occurrences"]["passes"]]
        self.assertEqual(5, len(set(ids)))
        # any one pass's id opens the record it was folded into
        self.assertEqual(entries[0]["id"], decisiontrace.entry(ids[-1])["id"])

    def test_a_restart_starts_a_new_record(self):
        self.idle_board()
        self.tick()
        self.tick()
        with mock.patch.object(state, "BOOT", "restarted"):
            self.tick()
        newest, older = self.entries()[:2]
        self.assertEqual(2, older["occurrences"]["count"])
        self.assertEqual(1, newest["occurrences"]["count"])
        self.assertTrue(newest.get("restartBefore"))

    def test_a_change_ends_the_fold(self):
        self.idle_board()
        self.tick()
        self.board.cards["7-card.md"]["stage"] = "to-do"
        self.tick()
        self.assertEqual(2, len(self.entries()))

    def test_a_recording_failure_is_named_and_changes_nothing(self):
        self.idle_board()
        self.tick()
        with mock.patch.object(decisiontrace, "_append",
                               side_effect=OSError("disk full")):
            out = self.tick()
        self.assertTrue(out["ran"])
        self.assertEqual(1, decisiontrace.meta()["recording"]["failures"])
        self.tick()
        newest = self.newest()
        self.assertEqual(1, newest["gapBefore"]["failures"])
        self.assertIn("disk full", newest["gapBefore"]["error"])
        self.assertEqual(0, decisiontrace.meta()["recording"]["failures"])

    def test_an_error_pass_is_recorded_as_partial(self):
        self.idle_board()
        with mock.patch.object(scheduler, "record_base_blocks",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.tick()
        entry = self.newest()
        self.assertEqual(("error", "error"), (entry["status"], entry["outcome"]))
        self.assertIn("boom", entry["error"])
        self.assertTrue(entry["decisions"])     # what it read before failing

    def test_malformed_lines_and_seq_gaps_are_identified(self):
        self.idle_board()
        self.tick()
        [seg] = sorted(decisiontrace.trace_dir().glob("seg-*.jsonl"))
        with seg.open("a") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps({"v": 1, "kind": "pass", "seq": 9, "id": "x-9",
                                 "epoch": "x", "decisions": [], "status":
                                 "evaluated", "at": "2026-01-01T00:00:00Z"}) + "\n")
        decisiontrace._CACHE.clear()
        meta = decisiontrace.page()["meta"]
        self.assertEqual(1, meta["integrity"]["malformedCount"])
        self.assertEqual(seg.name, meta["integrity"]["malformed"][0]["segment"])
        self.assertEqual(7, meta["integrity"]["gaps"][0]["missing"])

    def test_retention_names_its_boundary_and_invents_nothing(self):
        self.enable()
        with mock.patch.object(decisiontrace, "SEGMENT_BYTES", 1), \
                mock.patch.object(decisiontrace, "KEEP_SEGMENTS", 2):
            first = None
            for n in range(4):
                self.board.add(str(10 + n))
                self.tick()
                first = first or self.newest()["id"]
        meta = decisiontrace.meta()
        self.assertEqual(2, len(list(decisiontrace.trace_dir().glob("seg-*"))))
        self.assertEqual(2, meta["boundary"]["throughSeq"])
        self.assertIsNone(decisiontrace.entry(first))
        self.assertTrue(meta["started"])
        self.assertEqual(2, decisiontrace.page()["total"])


# ── 5. snapshots and stable paging ────────────────────────────────────

class HistoryDoesNotMove(TraceSandbox):
    def test_a_later_edit_does_not_change_an_earlier_record(self):
        self.enable()
        self.board.add("7", stage="backlog")
        self.board.add("6", depends=["7"], priority="Low", brief="First brief.")
        self.tick()
        before = self.newest()
        self.board.cards["6-card.md"]["priority"] = "High"
        self.board.cards["6-card.md"]["dependsOn"] = []
        self.board.cards["6-card.md"]["body"] = "# 6\n\nA rewritten brief.\n"
        self.put_in_force(agreement(capacity={"implementation": 4}))
        self.tick()
        again = decisiontrace.entry(before["id"])
        d = self.decision(again, "6-card.md")
        self.assertEqual("Low", d["facts"]["priority"])
        self.assertEqual(["7"], d["facts"]["dependsOn"])
        self.assertEqual("waiting-dependency", d["code"])
        self.assertEqual(before["agreement"]["revision"],
                         again["agreement"]["revision"])
        self.assertNotEqual(before["agreement"]["revision"],
                            self.newest()["agreement"]["revision"])

    def test_paging_by_seq_is_stable_under_appends(self):
        for n in range(30):
            decisiontrace.append({"status": "evaluated", "boot": f"b{n}",
                                  "decisions": [], "headline": f"pass {n}"})
        first = decisiontrace.page(limit=10)
        second = decisiontrace.page(limit=10, before=first["next"])
        for n in range(5):
            decisiontrace.append({"status": "evaluated", "boot": f"c{n}",
                                  "decisions": []})
        again = decisiontrace.page(limit=10, before=first["next"])
        self.assertEqual([e["id"] for e in second["entries"]],
                         [e["id"] for e in again["entries"]])
        seqs = [e["seq"] for e in first["entries"] + second["entries"]]
        self.assertEqual(sorted(seqs, reverse=True), seqs)
        self.assertEqual(20, len(set(seqs)))

    def test_filters(self):
        self.enable(capacity={"implementation": 1})
        self.board.add("1")
        self.board.add("2")
        self.tick()
        self.assertEqual(1, len(self.entries(card="2")))
        self.assertEqual(1, len(self.entries(outcome="waiting")))
        self.assertEqual(0, len(self.entries(card="99")))
        self.assertEqual(1, len(self.entries(scope="bench")))
        self.assertEqual(0, len(self.entries(scope="phase:9")))
        self.assertEqual(1, len(self.entries(since="2000-01-01")))
        self.assertEqual(0, len(self.entries(until="2000-01-01")))
        with self.assertRaises(ValueError):
            decisiontrace.page(since="yesterday")


# ── 6. passive reads, bounds, redaction, no card churn ────────────────

class ReadingIsPassiveAndBounded(TraceSandbox):
    def test_the_read_routes_write_and_launch_nothing(self):
        import routes
        self.enable()
        self.board.add("1")
        self.tick()
        entry = self.newest()
        calls = list(self.launch.calls)
        ops = [dict(o) for o in self.ops()]
        before = snapshot(self.local, self.work)
        replies = []

        class Req:
            def __init__(self, **query):
                self.query = {k: [v] for k, v in query.items()}

            def reply(self, code, payload):
                replies.append((code, payload))

        for _ in range(3):
            routes.get_orchestration_trace(Req())
            routes.get_orchestration_trace(Req(card="1", outcome="action"))
            routes.get_orchestration_trace_entry(Req(id=entry["id"]))
        routes.get_orchestration_trace_entry(Req(id="../etc"))
        routes.get_orchestration_trace_entry(Req(id="deadbeef-999"))
        routes.get_orchestration_trace(Req(since="nonsense"))
        self.assertEqual(snapshot(self.local, self.work), before)
        self.assertEqual(calls, self.launch.calls)
        self.assertEqual(ops, self.ops())
        codes = [c for c, _ in replies]
        self.assertEqual([200] * 9 + [400, 404, 400], codes)
        links = replies[2][1]["links"]
        [key] = links["operations"]
        self.assertEqual("launched", links["operations"][key]["status"])
        self.assertTrue(replies[0][1]["trace"]["meta"]["local"])

    def test_records_are_scrubbed_and_bounded(self):
        decisions = [{"card": f"{n}-c.md", "reason": "x" * 5000,
                      "facts": {"note": "token=abcdef1234567890"}}
                     for n in range(200)]
        decisiontrace.append({"status": "evaluated", "decisions": decisions,
                              "headline": "password: hunter2hunter2"})
        entry = self.newest()
        self.assertEqual(decisiontrace.MAX_DECISIONS, len(entry["decisions"]))
        self.assertEqual(200 - decisiontrace.MAX_DECISIONS,
                         entry["truncated"]["decisions"])
        text = json.dumps(entry)
        self.assertNotIn("abcdef1234567890", text)
        self.assertNotIn("hunter2hunter2", text)
        self.assertLessEqual(len(entry["decisions"][0]["reason"]),
                             decisiontrace.TEXT_CAP)
        raw = max(p.stat().st_size for p in decisiontrace.trace_dir().glob("seg-*"))
        self.assertLess(raw, decisiontrace.RECORD_CAP * 2)

    def test_idle_passes_add_no_card_history(self):
        self.enable()
        self.board.add("7", stage="backlog")
        self.board.add("6", depends=["7"])
        self.tick()
        notes = len(self.notes)
        for _ in range(6):
            self.tick()
        self.assertEqual(notes, len(self.notes))
        self.assertEqual(1, len(self.entries()))
        self.assertTrue(str(decisiontrace.trace_dir()).startswith(
            str(self.local / "state")))


if __name__ == "__main__":
    unittest.main()
