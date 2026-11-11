"""Card 101 — Run required checks with one explicit card action.

Design #94 §10.1–§10.3 (without automatic scheduling) and §15 step 5:
one armed action requests the current candidate's missing required checks,
in the merge rule's order, through `verify.request` under a person's work
reading. Met requirements are skipped, running ones joined, optional
checks never run, and a failure is never retried behind a person's back.
The candidate, the rule and the checkout are re-read between requests.

Every case runs against a disposable tool install, work root and
application checkout (`GateCase`) with stub check executables that count
their own executions in files outside every identity. Nothing here reads
or writes a live board, a remote or operator policy.

- Acceptance 1 (met / missing / running / optional) — `OnlyMissingRequired`.
- Acceptance 2 (one armed action; refusals; mid-batch revalidation) —
  `RefusedBeforeAnything`, `RevalidatedBetweenRequests`, `TheDoor`.
- Acceptance 3 (progress, preparation failure, cancellation/hold,
  restart, explicit retry) — `InterruptionIsExplicit`.
- Acceptance 4 (no automatic scheduling; reading runs nothing) —
  `ReadingRunsNothing`.
- The delivery action — `TheDeliveryAction`.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests.test_integration_gates import CARD, STEM, GateCase, wait_for  # noqa: E402

import config  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import httpd  # noqa: E402
import required  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

# A check that counts its own executions in a file named for it, and whose
# verdict is a flag file — both outside every identity.
COUNTED = """#!{python}
import sys, time
open("@DIR@/@NAME@.count", "a").write("ran\\n")
flag = open("@DIR@/@NAME@.flag").read().strip()
if flag == "hang":
    print("working", flush=True)
    time.sleep(600)
print("flag:", flag)
sys.exit(1 if flag == "fail" else 0)
"""


class BatchCase(GateCase):

    def setUp(self):
        super().setUp()
        for registry in (required.ACTIVE,):
            registry.clear()
            self.addCleanup(registry.clear)
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)
        self.marks = self.tmp / "marks"
        self.marks.mkdir()
        self.addCleanup(self.stop_hanging)

    def stop_hanging(self):
        for evidence in list(verify.RUNNING):
            try:
                verify.cancel(evidence)
            except ValueError:
                pass
        wait_for(lambda: not verify.RUNNING, 10)

    def check(self, name: str, flag: str = "pass", **extra) -> dict:
        (self.marks / f"{name}.flag").write_text(flag, encoding="utf-8")
        body = COUNTED.replace("@DIR@", str(self.marks)).replace("@NAME@", name)
        return {"name": name, "run": str(self.executable(name, body)),
                "root": "worktree", **extra}

    def flag(self, name: str, flag: str) -> None:
        (self.marks / f"{name}.flag").write_text(flag, encoding="utf-8")

    def runs(self, name: str) -> int:
        try:
            return len((self.marks / f"{name}.count").read_text(
                encoding="utf-8").splitlines())
        except OSError:
            return 0

    def policy(self, checks: list[dict], gate: list[str],
               work: list[str] | None = None, prepare=None) -> dict:
        names = [c["name"] for c in checks]
        document = {"v": 1, "checks": checks,
                    "profiles": {"work": {"verify": work if work is not None
                                          else names},
                                 "review": {"verify": names}},
                    "gates": {"acceptance": {"checks": gate}}}
        if prepare:
            document["prepare"] = prepare
        self.declares(document)
        return document

    def fresh(self):
        verify.forget_cached()
        gates.forget_cached()

    def person(self, name: str) -> dict:
        """A person's ↻ for one check, as the ◫ sheet asks."""
        found = verify.request(check=name, task=CARD, stage="in-progress",
                               actor="you", again=True)
        self.fresh()
        return found

    def run_batch(self, **kwargs) -> dict:
        found = required.start(CARD, "in-progress", wait=True, **kwargs)
        self.fresh()
        return required.public(required.latest(CARD)) or found

    def item(self, batch: dict, name: str) -> dict:
        return next(i for i in batch["items"] if i["check"] == name)

    def hang_in_background(self, name: str) -> threading.Thread:
        thread = threading.Thread(target=self.person, args=(name,),
                                  daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: any(
            r.get("check") == name and r.get("outcome") == "running"
            for r in verify.RUNNING.values()), 20))
        return thread

    def state_files(self) -> dict[str, str]:
        out = {}
        for path in sorted(self.state.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(self.state))] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return out


# ── acceptance 1 ───────────────────────────────────────────────────────


class OnlyMissingRequired(BatchCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("met"), self.check("slow", "hang"),
                     self.check("missing"), self.check("forbidden"),
                     self.check("optional")],
                    gate=["met", "slow", "missing", "forbidden"],
                    work=["met", "slow", "missing", "optional"])
        self.person("met")

    def test_requests_only_missing_joins_running_never_optional(self):
        self.hang_in_background("slow")
        plan = required.plan(CARD, "in-progress")
        self.assertIsNone(plan["refusal"], plan["why"])
        states = {i["check"]: i["state"] for i in plan["items"]}
        self.assertEqual(states, {"met": "met", "slow": "joined",
                                  "missing": "request",
                                  "forbidden": "unavailable"})
        self.assertNotIn("optional", states, "an optional check is no item")
        batch = self.run_batch(head=self.head)
        self.assertEqual(self.runs("missing"), 1)
        self.assertEqual(self.runs("met"), 1, "met was skipped")
        self.assertEqual(self.runs("slow"), 1, "running was joined, not doubled")
        self.assertEqual(self.runs("optional"), 0, "optional never ran")
        self.assertEqual(self.runs("forbidden"), 0)
        self.assertEqual(self.item(batch, "missing")["state"], "passed")
        self.assertEqual(self.item(batch, "slow")["state"], "joined")
        self.assertEqual(batch["state"], "finished")
        self.assertEqual(batch["summary"]["total"], 1)
        self.assertEqual(batch["summary"]["done"], 1)
        # the gate reads the new evidence; the slow one is still running
        decision = gates.evaluate(CARD, "acceptance")
        rows = {r["check"]: r for r in decision["requirements"]}
        self.assertEqual(rows["missing"]["state"], "met")
        self.assertEqual(rows["slow"]["state"], "pending")

    def test_unavailable_check_names_the_configuration_remedy(self):
        plan = required.plan(CARD, "in-progress")
        item = next(i for i in plan["items"] if i["check"] == "forbidden")
        self.assertEqual(item["state"], "unavailable")
        self.assertIn('"verify" list', item["remedy"])
        self.assertIn("does not widen", item["remedy"])
        self.run_batch()
        self.assertEqual(self.runs("forbidden"), 0,
                         "nothing widened what a person may ask for")

    def test_requests_follow_the_merge_rule_order(self):
        self.policy([self.check("b"), self.check("a"), self.check("c")],
                    gate=["c", "a", "b"])
        order = []
        real = verify.request

        def spy(**kwargs):
            order.append(kwargs["check"])
            return real(**kwargs)

        with mock.patch.object(verify, "request", side_effect=spy):
            self.run_batch()
        self.assertEqual(order, ["c", "a", "b"])
        for call in order:
            self.assertEqual(self.runs(call), 1)

    def test_requests_are_a_persons_work_reading(self):
        batch = self.run_batch()
        record = verify.read(self.item(batch, "missing")["evidence"])
        self.assertEqual(record["actor"], "you")
        self.assertIsNone(record["agent"])
        self.assertEqual(record["intent"], "work")
        self.assertEqual(record["source"]["commit"], self.head)


# ── acceptance 2 ───────────────────────────────────────────────────────


class RefusedBeforeAnything(BatchCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("unit")], gate=["unit"])

    def refused(self, code: str, **kwargs):
        with self.assertRaises(required.Refused) as caught:
            required.start(CARD, "in-progress", wait=True, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.runs("unit"), 0)
        self.assertIsNone(required.latest(CARD))

    def test_an_absent_candidate(self):
        name = "72-no-branch.md"
        (self.tasks / "in-progress" / name).write_text(
            "# 72 — none\n\n**Status:** In Progress\n**Priority:** Low\n\nx\n",
            encoding="utf-8")
        with self.assertRaises(required.Refused) as caught:
            required.start(name, "in-progress", wait=True)
        self.assertEqual(caught.exception.code, "no-candidate")

    def test_a_dirty_checkout(self):
        (self.wt / "code.txt").write_text("uncommitted\n", encoding="utf-8")
        self.refused("dirty")

    def test_an_untracked_file_is_dirty_too(self):
        (self.wt / "new.txt").write_text("untracked\n", encoding="utf-8")
        self.refused("dirty")

    def test_a_run_may_not_ask(self):
        self.refused("authority", agent="run-1")

    def test_a_stale_confirmation(self):
        self.refused("head-moved", head="0" * 40)

    def test_an_ungated_board(self):
        self.declares({"v": 1, "checks": [self.check("unit")],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.refused("ungated")

    def test_nothing_needed(self):
        self.person("unit")
        self.assertEqual(self.runs("unit"), 1)
        with self.assertRaises(required.Refused) as caught:
            required.start(CARD, "in-progress", wait=True)
        self.assertEqual(caught.exception.code, "nothing-needed")
        self.assertEqual(self.runs("unit"), 1)

    def test_an_unknown_card(self):
        with self.assertRaises(required.Refused) as caught:
            required.plan("99-nope.md")
        self.assertEqual(caught.exception.code, "unknown-card")


class RevalidatedBetweenRequests(BatchCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("one"), self.check("two")],
                    gate=["one", "two"])

    def after_first(self, then):
        real = verify.request
        calls = []

        def spy(**kwargs):
            found = real(**kwargs)
            calls.append(kwargs["check"])
            if len(calls) == 1:
                then()
            return found

        return mock.patch.object(verify, "request", side_effect=spy)

    def test_a_commit_landing_stops_the_rest(self):
        with self.after_first(lambda: self.commit_in(self.wt, "newer\n")):
            batch = self.run_batch()
        self.assertEqual(self.item(batch, "one")["state"], "passed")
        self.assertEqual(self.item(batch, "two")["state"], "not-requested")
        self.assertEqual(batch["stop"], "source-drift")
        self.assertEqual(batch["state"], "stopped")
        self.assertEqual(self.runs("two"), 0)

    def test_a_policy_change_stops_the_rest(self):
        def edit():
            self.policy([self.check("one"), self.check("two"),
                         self.check("three")], gate=["one", "two", "three"])
        with self.after_first(edit):
            batch = self.run_batch()
        self.assertEqual(batch["stop"], "policy-changed")
        self.assertEqual(self.item(batch, "two")["state"], "not-requested")
        self.assertEqual(self.runs("two"), 0)

    def test_uncommitted_work_appearing_stops_the_rest(self):
        def scribble():
            (self.wt / "code.txt").write_text("half-done\n", encoding="utf-8")
        with self.after_first(scribble):
            batch = self.run_batch()
        self.assertEqual(batch["stop"], "dirty")
        self.assertEqual(self.runs("two"), 0)

    def test_a_result_from_another_commit_is_not_this_candidates(self):
        other = {"id": "verification-elsewhere", "outcome": "passed",
                 "reason": "exit-zero", "why": "the check exited 0",
                 "source": {"commit": "f" * 40, "bound": True}}
        with mock.patch.object(verify, "request", return_value=other):
            batch = self.run_batch()
        self.assertEqual(self.item(batch, "one")["state"], "other-candidate")
        self.assertEqual(batch["stop"], "source-drift")
        self.assertEqual(self.item(batch, "two")["state"], "not-requested")

    def test_a_requirement_answered_meanwhile_is_skipped(self):
        def answer():
            self.person("two")
        with self.after_first(answer):
            batch = self.run_batch()
        self.assertEqual(self.item(batch, "two")["state"], "met")
        self.assertEqual(self.runs("two"), 1, "only the person's own run")

    def test_a_second_click_joins_the_batch_in_flight(self):
        self.flag("one", "hang")
        first = required.start(CARD, "in-progress")
        self.assertTrue(wait_for(lambda: any(
            r.get("outcome") == "running" for r in verify.RUNNING.values()), 20))
        second = required.start(CARD, "in-progress")
        self.assertTrue(second.get("joinedBatch"))
        self.assertEqual(second["id"], first["id"])
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        self.assertEqual(len([r for r in verify.for_task(CARD)
                              if r.get("check") == "one"]), 1,
                         "one request, not two")


class TheDoor(BatchCase):
    """The route is a person's click, behind the policy door."""

    def setUp(self):
        super().setUp()
        self.policy([self.check("unit")], gate=["unit"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.patch(PORT=self.port)

    def post(self, path: str, body: dict, headers: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="POST",
            data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request) as found:
                return found.status, json.loads(found.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_a_form_shaped_request_is_refused(self):
        code, _ = self.post("/api/verify/required", {"file": CARD},
                            {"Content-Type": "application/json"})
        self.assertEqual(code, 403)
        self.assertEqual(self.runs("unit"), 0)

    def test_a_run_naming_itself_is_refused(self):
        code, body = self.post(
            "/api/verify/required", {"file": CARD, "agent": "run-1"},
            {"Content-Type": "application/json", "X-Bench-Settings": "1"})
        self.assertEqual(code, 403)
        self.assertEqual(body["code"], "authority")

    def test_a_persons_click_starts_it_and_the_reading_is_a_get(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/verify/required?file={CARD}"
        ) as found:
            plan = json.loads(found.read())["plan"]
        self.assertEqual(plan["requestable"], 1)
        code, body = self.post(
            "/api/verify/required", {"file": CARD, "head": plan["head"]},
            {"Content-Type": "application/json", "X-Bench-Settings": "1"})
        self.assertEqual(code, 200, body)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 30))
        self.assertEqual(self.runs("unit"), 1)


# ── acceptance 3 ───────────────────────────────────────────────────────


class InterruptionIsExplicit(BatchCase):

    def test_progress_says_preparing_apart_from_running(self):
        self.policy([self.check("unit", prepare=["deps"])], gate=["unit"],
                    prepare=[{"name": "deps", "run": str(self.executable(
                        "deps", "#!{python}\nimport time\ntime.sleep(600)\n"))}])
        required.start(CARD, "in-progress")
        self.assertTrue(wait_for(lambda: (
            (required.public(required.latest(CARD)) or {}).get("items")
            or [{}])[0].get("live") == "preparing", 20))
        shown = required.public(required.latest(CARD))
        self.assertEqual(shown["summary"]["current"], "unit")
        self.assertEqual(shown["summary"]["done"], 0)
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))

    def test_a_preparation_failure_is_not_missing_and_not_retried(self):
        self.policy([self.check("unit", prepare=["deps"])], gate=["unit"],
                    prepare=[{"name": "deps", "run": str(self.executable(
                        "deps", "#!{python}\nimport sys\nsys.exit(3)\n"))}])
        batch = self.run_batch()
        self.assertEqual(self.item(batch, "unit")["state"], "prepare-failed")
        self.assertEqual(self.runs("unit"), 0, "the check itself never ran")
        row = gates.evaluate(CARD, "acceptance")["requirements"][0]
        self.assertEqual(row["reason"], "prepare-failed")
        self.assertEqual(gates.severity(row["reason"]), "problem")
        plan = required.plan(CARD, "in-progress")
        self.assertEqual(plan["items"][0]["state"], "problem",
                         "a failure is read, not silently re-requested")
        self.assertEqual(plan["refusal"], "nothing-needed")

    def test_a_failed_check_is_reported_failed_not_passed(self):
        self.policy([self.check("unit", "fail")], gate=["unit"])
        batch = self.run_batch()
        self.assertEqual(self.item(batch, "unit")["state"], "failed")
        self.assertEqual(batch["state"], "finished")
        self.assertIn("unit failed", required.line(required.latest(CARD)))

    def test_cancel_stops_the_rest_and_a_click_recovers(self):
        self.policy([self.check("one", "hang"), self.check("two")],
                    gate=["one", "two"])
        required.start(CARD, "in-progress")
        self.assertTrue(wait_for(lambda: any(
            r.get("outcome") == "running" for r in verify.RUNNING.values()), 20))
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        self.fresh()
        batch = required.public(required.latest(CARD))
        self.assertEqual(self.item(batch, "one")["state"], "cancelled")
        self.assertEqual(self.item(batch, "two")["state"], "not-requested")
        self.assertEqual(batch["stop"], "cancelled")
        self.assertEqual(self.runs("two"), 0, "nothing retried by itself")
        row = gates.evaluate(CARD, "acceptance")["requirements"][0]
        self.assertEqual(row["reason"], "cancelled")
        # the explicit retry: one more click, and both are requested
        self.flag("one", "pass")
        plan = required.plan(CARD, "in-progress")
        self.assertEqual([i["state"] for i in plan["items"]],
                         ["request", "request"])
        again = self.run_batch()
        self.assertEqual([i["state"] for i in again["items"]],
                         ["passed", "passed"])

    def test_a_held_runs_cancelled_check_is_recovered_by_a_click(self):
        self.policy([self.check("unit", "hang")], gate=["unit"])
        self.manifest(agent="run-1", worktree=self.wt)
        thread = threading.Thread(target=self.ask, kwargs={"agent": "run-1"},
                                  daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: any(
            r.get("agent") == "run-1" for r in verify.RUNNING.values()), 20))
        verify.cancel_for_agent("run-1")      # what ‖ hold does to its checks
        thread.join(20)
        self.fresh()
        plan = required.plan(CARD, "in-progress")
        self.assertEqual(plan["items"][0]["reason"], "cancelled")
        self.assertEqual(plan["items"][0]["state"], "request")
        self.flag("unit", "pass")
        batch = self.run_batch()
        self.assertEqual(self.item(batch, "unit")["state"], "passed")

    def test_a_restart_closes_the_batch_and_reads_no_result(self):
        self.policy([self.check("one", "hang"), self.check("two")],
                    gate=["one", "two"])
        required.start(CARD, "in-progress")
        self.assertTrue(wait_for(lambda: any(
            r.get("outcome") == "running" for r in verify.RUNNING.values()), 20))
        # what a dead board leaves on disk: the batch and the evidence both
        # read running, and this board's memory holds neither
        snapshot = json.loads(required._path(CARD).read_text(encoding="utf-8"))
        running = list(verify.RUNNING)
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        for evidence in running:
            record = verify.read(evidence)
            verify._store(dict(record, outcome="running", reason=None,
                               ended=None))
        required._path(CARD).write_text(json.dumps(snapshot), encoding="utf-8")
        verify.recover()
        closed = required.recover()
        self.assertEqual(len(closed), 1)
        self.fresh()
        batch = required.public(required.latest(CARD))
        self.assertEqual(batch["state"], "stopped")
        self.assertEqual(batch["stop"], "board-restart")
        self.assertEqual(self.item(batch, "one")["state"], "error")
        self.assertIn("board restarted", self.item(batch, "one")["why"])
        self.assertEqual(self.item(batch, "two")["state"], "not-requested")
        self.assertEqual(self.runs("two"), 0, "nothing was asked again")
        row = gates.evaluate(CARD, "acceptance")["requirements"][0]
        self.assertEqual(row["reason"], "restarted")
        self.assertEqual(gates.severity("restarted"), "needed")
        self.assertIn("the board restarted", gates.plain("restarted", self.head))
        (self.tasks / "in-progress" / CARD).rename(
            self.tasks / "review" / CARD)
        found = delivery.for_card(CARD, "review")
        self.assertEqual(found["status"]["word"], "checks-needed")
        self.assertEqual(found["primary"]["action"], "run-required-checks")
        self.assertIn("board restarted", found["status"]["sentence"])
        # recover twice changes nothing more
        self.assertEqual(required.recover(), [])


# ── acceptance 4 ───────────────────────────────────────────────────────


class ReadingRunsNothing(BatchCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("unit", root="source")], gate=["unit"])

    def test_the_plan_writes_nothing_and_makes_no_checkout(self):
        before = self.state_files()
        worktrees = sorted(p.name for p in config.WORKTREES.iterdir())
        for _ in range(2):
            plan = required.plan(CARD, "in-progress")
            delivery.for_card(CARD, "in-progress")
        self.assertEqual(plan["requestable"], 1)
        self.assertEqual(self.state_files(), before)
        self.assertEqual(sorted(p.name for p in config.WORKTREES.iterdir()),
                         worktrees)
        self.assertEqual(self.runs("unit"), 0)
        self.assertFalse(verify.RUNNING)

    def test_nothing_schedules_a_request_by_itself(self):
        # moving the card to review, and every reading, request nothing
        (self.tasks / "in-progress" / CARD).rename(
            self.tasks / "review" / CARD)
        delivery.for_card(CARD, "review")
        required.running()
        time.sleep(0.3)
        self.assertEqual(self.runs("unit"), 0)
        self.assertIsNone(required.latest(CARD))
        self.assertNotIn("VERIFY_ON_REVIEW", dir(config))

    def test_the_plan_states_its_side_effects(self):
        plan = required.plan(CARD, "in-progress")
        self.assertIn("moves no card", plan["sideEffects"])
        self.assertIn("Nothing is retried by itself", plan["sideEffects"])
        self.assertIn("never ran", plan["preparation"])
        definition = plan["items"][0]["definition"]
        self.assertEqual(definition["root"], "source")
        self.assertTrue(definition["command"].endswith("unit"))


# ── the delivery action ────────────────────────────────────────────────


class TheDeliveryAction(BatchCase):
    """In review, where `checks-needed` is the face's word."""

    def setUp(self):
        super().setUp()
        (self.tasks / "in-progress" / CARD).rename(
            self.tasks / "review" / CARD)

    def test_checks_needed_offers_the_real_handler(self):
        self.policy([self.check("unit")], gate=["unit"])
        found = delivery.for_card(CARD, "review")
        self.assertEqual(found["status"]["word"], "checks-needed")
        primary = found["primary"]
        self.assertEqual(primary["action"], "run-required-checks")
        self.assertTrue(primary["enabled"])
        self.assertEqual(primary["handler"], "/api/verify/required")

    def test_a_check_only_the_policy_could_grant_is_the_remedy(self):
        self.policy([self.check("unit"), self.check("other")], gate=["unit"],
                    work=["other"])
        primary = delivery.for_card(CARD, "review")["primary"]
        self.assertEqual(primary["action"], "run-required-checks")
        self.assertFalse(primary["enabled"])
        self.assertIn('"verify" list', primary["why"])

    def test_a_batch_in_flight_shows_its_progress(self):
        self.policy([self.check("one", "hang"), self.check("two")],
                    gate=["one", "two"])
        required.start(CARD, "review")
        self.assertTrue(wait_for(lambda: any(
            r.get("outcome") == "running" for r in verify.RUNNING.values()), 20))
        self.fresh()
        found = delivery.for_card(CARD, "review")
        self.assertEqual(found["status"]["word"], "checks-running")
        progress = [a for a in found["others"]
                    if a["action"] == "run-required-checks"]
        self.assertEqual(len(progress), 1)
        self.assertIn("0 of 2 answered", progress[0]["label"])
        self.assertIn(CARD, required.running())
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))


if __name__ == "__main__":
    unittest.main()
