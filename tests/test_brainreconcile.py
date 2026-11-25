"""Brain classification reconciliation (task 197): #196's Evaluate, queued
by work events and initial/rebuild reconciliation, bounded, durable and
fenced by an explicit policy.

Held on #175's disposable split layout through the Claude adapter's real
`run`, with a stub vendor that answers, for the entries each prompt names,
the evaluations a test scripts:

- a fresh board stays unconfigured: a tick reads one file and launches
  nothing; turning it on is a person's write bound to the revision read;
  the evaluate and apply authorities are separate and both shown;
- enabling queues every live entry once (initial); a card an entry cites
  changing queues that entry and no other, repeated changes coalesce, and
  unchanged inputs never call the model again;
- code that moved outside a card event reads `codeDrift`, never fresh,
  until a person's Refresh sources queues it;
- a result whose inputs moved while it ran is stale and never applied; a
  manual override is never re-evaluated; disabling or revoking apply
  while a round runs leaves its proposals for a person;
- the daily budget, bounded retries, cancellation, a crash between the
  intent and the launch, a lost apply reply and a replica each end with
  no duplicate launch and no duplicate write.

    python3 -m unittest tests.test_brainreconcile -v
"""

from __future__ import annotations

import json
import stat
import time
import unittest
from unittest import mock

from tests.test_braindraft import git, wait_for
from tests.test_brainevaluate import (CSV_HISTORICAL, JSON_CURRENT, MUDDLE_UNCLASSIFIED,
                                      OLD_SUPERSEDED, _Evaluating)

import brain  # noqa: E402
import braindraft  # noqa: E402
import brainreconcile  # noqa: E402
import brainwrite  # noqa: E402
import execowner  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import json, re, sys, time
from pathlib import Path
here = Path(__HERE__)
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
with open(here / "calls.txt", "a") as calls:
    calls.write("call\n")
draft = re.search(r'"draft": "(bd-[0-9a-f]+)"', prompt).group(1)
rnd = int(re.search(r'"round": (\d+)', prompt).group(1))
script = json.loads((here / "evaluations.json").read_text())
if script.get("sleep"):
    time.sleep(script["sleep"])
if script.get("fail"):
    print("the vendor fell over")
    sys.exit(1)
doc = {"v": 1, "kind": "bench-brain-evaluation", "draft": draft, "round": rnd,
       "outcome": "proposal", "summary": "Evaluated from the code and the cards."}
doc["evaluations"] = [e for e in script["evaluations"]
                      if ("`" + e["entry"] + "` — ") in prompt]
print("Reading.\n<<<BRAIN\n" + json.dumps(doc, indent=1) + "\nBRAIN>>>")
'''

EXPORTS_CURRENT = {"entry": "e-exports", "current": "Current", "proposed": "Current",
                   "reason": "the export module is where the code says it is",
                   "evidence": ["app:src/export.py"]}
EVERYTHING = [CSV_HISTORICAL, JSON_CURRENT, OLD_SUPERSEDED, MUDDLE_UNCLASSIFIED,
              EXPORTS_CURRENT]
LIVE = {"e-csv-study", "e-json-study", "e-old-delimiter", "e-muddle", "e-exports"}
OWNER = {"allowed": True, "code": "owner", "reason": "designated here, lock held"}
REPLICA = {"allowed": False, "code": "replica",
           "reason": "another installation is designated to execute"}


class _Reconciling(_Evaluating):

    def setUp(self):
        super().setUp()
        stub = self.tmp / "vendor"
        stub.write_text(STUB.replace("__HERE__", repr(str(self.tmp))), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.script(EVERYTHING)
        self.owner = dict(OWNER)
        patcher = mock.patch.object(execowner, "authorizes", lambda: dict(self.owner))
        patcher.start()
        self.addCleanup(patcher.stop)
        for attr, value in (("SETTLE", 0.0), ("BACKOFF", 0.0)):
            self.addCleanup(setattr, brainreconcile, attr, getattr(brainreconcile, attr))
            setattr(brainreconcile, attr, value)

    def policy(self, **changes) -> dict:
        now = brainreconcile.read_policy()
        return brainreconcile.set_policy({"expected": now["revision"], **changes})

    def enable(self, apply: bool = False, **limits) -> dict:
        return self.policy(enabled=True, evaluate=True, apply=apply,
                           **{"batch": 8, **limits})

    def drain(self, rounds: int = 12) -> None:
        """Tick until nothing is running and nothing more is launched."""
        for _ in range(rounds):
            brainreconcile.tick()
            self.assertTrue(wait_for(lambda: all(
                d["status"] != "running" for d in braindraft.DRAFTS.values())),
                "a round never settled")
            brainreconcile.tick()
            if not any(r["stored"] in ("queued", "running")
                       for r in self.rows().values()):
                return

    def rows(self) -> dict:
        return {r["entry"]: r for r in brainreconcile.reading()["entries"]}

    def card(self, text: str) -> None:
        path = self.work / "tasks" / "done" / "12-export-as-csv.md"
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


class OffUntilTurnedOn(_Reconciling):

    def test_a_fresh_board_reads_one_file_and_does_nothing(self):
        before = self.files()
        self.assertEqual(brainreconcile.tick(), {"ran": False, "code": "unconfigured"})
        self.assertFalse(brainreconcile.state_path().exists())
        read = brainreconcile.reading()
        self.assertEqual(read["policy"]["state"], "unconfigured")
        self.assertFalse(read["effective"]["evaluate"])
        self.assertFalse(read["effective"]["apply"])
        self.assertEqual(read["entries"], [])
        self.assertFalse(brainreconcile.state_path().exists(), "reading wrote state")
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.files(), before)

    def test_turning_it_on_is_bound_to_the_revision_read(self):
        with self.assertRaises(brainreconcile.Refused) as caught:
            brainreconcile.set_policy({"expected": "elsewhere", "enabled": True})
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(brainreconcile.policy_path().exists())
        with self.assertRaises(brainreconcile.Refused):
            self.policy(enabled=True, evaluate=False, apply=True)
        out = self.enable()
        self.assertTrue(out["effective"]["evaluate"])
        self.assertFalse(out["effective"]["apply"], "apply was granted with evaluate")
        hist = out["policy"]["history"]
        self.assertEqual(len(hist), 1)
        self.assertTrue(hist[0]["change"]["enabled"])
        with self.assertRaises(brainreconcile.Refused):
            self.enable()   # nothing changes, nothing is written

    def test_an_invalid_policy_grants_nothing(self):
        brainreconcile.policy_path().write_text(json.dumps(
            {"v": 1, "kind": brainreconcile.POLICY_KIND, "enabled": True,
             "evaluate": True, "apply": True, "extra": 1}))
        read = brainreconcile.read_policy()
        self.assertEqual(read["state"], "invalid")
        self.assertFalse(brainreconcile.effective(read)["evaluate"])
        brainreconcile.tick()
        self.assertEqual(self.calls(), 0)

    def test_a_replica_never_launches(self):
        self.enable()
        self.owner = dict(REPLICA)
        self.assertEqual(brainreconcile.tick()["code"], "replica")
        self.assertEqual(self.calls(), 0)
        self.assertFalse(brainreconcile.reading()["effective"]["runs"])
        with self.assertRaises(brainreconcile.Refused) as caught:
            brainreconcile.act({"action": "rebuild", "request": self.key()})
        self.assertEqual(caught.exception.extra.get("code"), "not-owner")


class EvaluateAuthority(_Reconciling):

    def test_initial_reconciliation_proposes_and_waits_for_review(self):
        before = self.files()
        self.enable()
        self.drain()
        rows = self.rows()
        self.assertEqual(self.calls(), 1, "one batch, one launch")
        self.assertEqual(rows["e-csv-study"]["state"], "review")
        self.assertEqual(rows["e-old-delimiter"]["state"], "review")
        self.assertIn("apply authority is not granted", rows["e-csv-study"]["why"])
        self.assertEqual(rows["e-json-study"]["state"], "current")
        self.assertEqual(rows["e-exports"]["state"], "current")
        self.assertEqual(rows["e-held"]["state"], "held")
        self.assertEqual(self.files(), before, "evaluate authority wrote the Brain")
        drafts = [d for d in braindraft.DRAFTS.values()]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["origin"], "reconcile")
        self.assertNotIn("e-held", drafts[0]["selected"])
        # unchanged inputs: no more model calls, however often it ticks
        for _ in range(4):
            brainreconcile.tick()
        self.assertEqual(self.calls(), 1)

    def test_a_card_event_queues_only_what_cites_it_and_coalesces(self):
        self.enable()
        self.drain()
        self.assertEqual(self.calls(), 1)
        self.card("\nA report: the semicolon export shipped.\n")
        self.card("\nAnd a second edit before anything ran.\n")
        brainreconcile.SETTLE = 3600.0   # hold it in the queue to look at it
        brainreconcile.tick()
        rows = self.rows()
        self.assertEqual(rows["e-csv-study"]["stored"], "queued")
        self.assertEqual(rows["e-exports"]["stored"], "queued")
        self.assertEqual(rows["e-csv-study"]["reason"], "card-event")
        self.assertEqual(rows["e-csv-study"]["triggers"], ["#12"])
        self.assertEqual(rows["e-json-study"]["stored"], "current", "an uncited entry moved")
        place = self.rows()["e-csv-study"]["position"]
        self.card("\nA third edit while queued.\n")
        brainreconcile.tick()
        self.assertEqual(self.rows()["e-csv-study"]["position"], place, "lost its place")
        brainreconcile.SETTLE = 0.0
        self.drain()
        self.assertEqual(self.calls(), 2, "three edits, one more launch")
        last = sorted(braindraft.DRAFTS.values(), key=lambda d: d["created"])[-1]
        self.assertEqual(sorted(last["selected"]), ["e-csv-study", "e-exports"])

    def test_code_drift_is_said_and_only_a_refresh_queues_it(self):
        self.enable()
        self.drain()
        (self.app / "src" / "export.py").write_text("DELIMITER = ','\n")
        git(self.app, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qam", "comma")
        brainreconcile.tick()
        rows = self.rows()
        self.assertTrue(rows["e-exports"]["codeDrift"])
        self.assertEqual(rows["e-exports"]["stored"], "current")
        self.assertEqual(self.calls(), 1, "code drift launched without a refresh")
        out = brainreconcile.act({"action": "refresh", "request": self.key(),
                                  "entries": ["e-exports"]})
        self.assertEqual(out["entries"], ["e-exports"])
        self.assertEqual(self.rows()["e-exports"]["reason"], "source-refresh")
        self.drain()
        self.assertEqual(self.calls(), 2)
        self.assertFalse(self.rows()["e-exports"]["codeDrift"])

    def test_rebuild_queues_every_eligible_entry_again(self):
        self.enable()
        self.drain()
        key = self.key()
        out = brainreconcile.act({"action": "rebuild", "request": key})
        self.assertEqual(set(out["entries"]), LIVE)
        again = brainreconcile.act({"action": "rebuild", "request": key})
        self.assertTrue(again["duplicate"])
        self.drain()
        self.assertEqual(self.calls(), 2)


class ApplyAuthority(_Reconciling):

    def test_default_selected_proposals_are_applied_under_the_policy(self):
        held = (self.work / "brain" / "e-held.md").read_text()
        self.enable(apply=True)
        self.drain()
        rows = self.rows()
        self.assertEqual(self.klass("e-csv-study"), "Historical")
        self.assertEqual(self.klass("e-old-delimiter"), "Superseded")
        self.assertEqual(self.klass("e-muddle"), "Current", "Unclassified is not default")
        # the automatic Apply closed the evaluation; what it left out says so
        self.assertEqual(rows["e-muddle"]["state"], "blocked")
        self.assertEqual(rows["e-muddle"]["code"], "not-default")
        self.assertIn("Evaluate it by hand", rows["e-muddle"]["why"])
        self.assertEqual(rows["e-csv-study"]["state"], "applied")
        self.assertEqual(rows["e-csv-study"]["explanation"]["entry"], "e-exports")
        self.assertEqual((self.work / "brain" / "e-held.md").read_text(), held)
        text = (self.work / "brain" / "e-csv-study.md").read_text()
        self.assertIn("applied under the Brain reconciliation policy", text)
        self.assertIn("We should export CSV with a semicolon.", text, "text rewritten")
        # a lost reply: the same apply request again is answered, not re-written
        op = next(o for o in brainreconcile._load()["ops"].values()
                  if o.get("applyRequest"))
        again = braindraft.evaluate_auto_apply(op["draft"], op["applyRequest"],
                                               ["e-csv-study"])
        self.assertTrue(again["already"])
        self.assertEqual(text, (self.work / "brain" / "e-csv-study.md").read_text())

    def test_a_result_whose_inputs_moved_is_stale_and_not_applied(self):
        self.script(EVERYTHING, sleep=1.5)
        self.enable(apply=True)
        brainreconcile.tick()
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        self.card("\nChanged while the evaluation ran.\n")
        self.assertTrue(wait_for(lambda: all(
            d["status"] != "running" for d in braindraft.DRAFTS.values())))
        brainreconcile.SETTLE = 3600.0
        brainreconcile.tick()
        rows = self.rows()
        self.assertEqual(rows["e-csv-study"]["stored"], "queued")
        self.assertEqual(rows["e-csv-study"]["code"], "stale-result")
        self.assertEqual(self.klass("e-csv-study"), "Current", "a stale result won")
        # an entry whose own inputs held but whose cited evidence moved is not
        # applied either: the writer's source-drift check, said on its row
        self.assertEqual(self.klass("e-old-delimiter"), "Current")
        row = rows["e-old-delimiter"]
        self.assertEqual(row["state"], "review")
        self.assertIn("a source this proposal cites changed", row["why"])

    def test_revoking_apply_while_a_round_runs_fences_its_write(self):
        self.script(EVERYTHING, sleep=1.0)
        self.enable(apply=True)
        brainreconcile.tick()
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        self.policy(apply=False)
        self.drain()
        self.assertEqual(self.klass("e-csv-study"), "Current")
        self.assertEqual(self.rows()["e-csv-study"]["state"], "review")

    def test_disabling_while_a_round_runs_fences_its_write_too(self):
        self.script(EVERYTHING, sleep=1.0)
        self.enable(apply=True)
        brainreconcile.tick()
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        self.policy(enabled=False)
        self.assertTrue(wait_for(lambda: all(
            d["status"] != "running" for d in braindraft.DRAFTS.values())))
        brainreconcile.tick()
        self.assertEqual(self.klass("e-csv-study"), "Current")
        self.assertIn("turned off or revoked", self.rows()["e-csv-study"]["why"])

    def test_an_override_cleared_is_evaluated_and_one_set_is_held(self):
        self.enable(apply=True)
        self.drain()
        self.assertEqual(self.rows()["e-held"]["state"], "held")
        self.assertEqual(self.klass("e-held"), "Current")

    def test_sync_pending_is_said_apart_from_applied(self):
        self.enable(apply=True)
        self.drain()
        with mock.patch.object(brainwrite, "operation", lambda r: {
                "commit": {"state": "committed"}, "push": {"state": "conflict"}}):
            row = self.rows()["e-csv-study"]
        self.assertEqual(row["state"], "sync-pending")
        self.assertEqual(row["sync"]["push"], "conflict")


class Bounds(_Reconciling):

    def test_the_daily_budget_holds_the_rest_in_the_queue(self):
        self.enable(dailyLaunches=1, batch=2)
        self.drain()
        self.assertEqual(self.calls(), 1)
        read = brainreconcile.reading()
        self.assertEqual(read["budget"]["left"], 0)
        waiting = [r for r in read["entries"] if r["stored"] == "queued"]
        self.assertTrue(waiting)
        self.assertTrue(all(r["code"] == "budget-exhausted" for r in waiting))

    def test_failures_retry_a_bounded_number_of_times_then_wait(self):
        self.script(EVERYTHING, fail=True)
        self.enable(retries=1)
        self.drain()
        self.assertEqual(self.calls(), 2, "one try and one retry")
        row = self.rows()["e-csv-study"]
        self.assertEqual(row["state"], "failed")
        for _ in range(3):
            brainreconcile.tick()
        self.assertEqual(self.calls(), 2, "an exhausted retry launched again")
        # a person's Retry is a new decision, and is launched
        self.script(EVERYTHING)
        brainreconcile.act({"action": "retry", "request": self.key()})
        self.drain()
        self.assertEqual(self.calls(), 3)
        self.assertEqual(self.rows()["e-csv-study"]["state"], "review")

    def test_cancelling_a_running_round_writes_nothing_late(self):
        self.script(EVERYTHING, sleep=1.5)
        self.enable(apply=True)
        brainreconcile.tick()
        self.assertTrue(wait_for(lambda: self.calls() == 1))
        brainreconcile.act({"action": "cancel", "request": self.key(),
                            "entries": ["e-csv-study"]})
        self.assertTrue(wait_for(lambda: all(
            d["status"] != "running" for d in braindraft.DRAFTS.values())))
        time.sleep(2.0)
        for _ in range(3):
            brainreconcile.tick()
        self.assertEqual(self.klass("e-csv-study"), "Current")
        self.assertEqual(self.rows()["e-csv-study"]["state"], "cancelled")
        self.assertEqual(self.calls(), 1)

    def test_an_intent_without_a_receipt_is_reconciled_before_any_rerun(self):
        self.enable()
        # play a crash after the intent, before start_evaluate answered
        with mock.patch.object(braindraft, "start_evaluate",
                               side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                brainreconcile.tick()
        st = brainreconcile._load()
        intents = [k for k, o in st["ops"].items() if o["status"] == "intent"]
        self.assertEqual(len(intents), 1)
        self.assertEqual(self.calls(), 0)
        brainreconcile.tick()
        st = brainreconcile._load()
        self.assertEqual(st["ops"][intents[0]]["status"], "not-launched")
        self.drain()
        self.assertEqual(self.calls(), 1, "the interrupted batch launched twice")

    def test_an_intent_whose_draft_exists_is_adopted_not_relaunched(self):
        self.enable()
        real = braindraft.start_evaluate

        def launch_then_crash(payload, **kw):
            real(payload, **kw)
            raise SystemExit("crash after the launch, before the receipt")

        with mock.patch.object(braindraft, "start_evaluate", side_effect=launch_then_crash):
            with self.assertRaises(SystemExit):
                brainreconcile.tick()
        self.drain()
        self.assertEqual(self.calls(), 1, "a launched batch was launched again")
        ops = brainreconcile._load()["ops"]
        self.assertTrue(any(o.get("recovered") and o.get("draft") for o in ops.values()))


if __name__ == "__main__":
    unittest.main()
