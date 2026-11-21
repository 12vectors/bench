"""Agent-assisted recovery and classified transient retry (task 144, D5/D9).

Each class is one acceptance criterion of the card:

- quiet-but-active and tool-active/no-progress runs are neither stopped
  nor duplicated, and a justified incident launches one context-bound
  diagnosis;
- repeated ticks, a restart and both scope levels hold the recovery and
  transient-retry limits, the global budgets, the backoff and the
  circuit breaker;
- an active worktree, an unknown outcome and missing authority refuse
  unsafe recovery, and a conflicting restart waits for an observed stop;
- a diagnosis cannot broaden permission or claim success, and an
  unresolved or exhausted incident stays visibly held;
- every diagnosis, attempt, proposed action and observed result is
  recorded on its card, in the card record's own fields.

The scheduler's sandbox is reused: no adapter, model, branch or worktree
is involved. A diagnosis is a stand-in for `reasoning.request` that is
authorized and counted through the real `orchestrator`, exactly as the
real one is; its settling is played by the test.

    python3 -m unittest tests.test_orchestration_recovery -v
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from tests.test_orchestration_scheduler import Sandbox, agreement, snapshot

import agents  # noqa: E402
import agreements  # noqa: E402
import config  # noqa: E402
import events  # noqa: E402
import decisions  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import reasoning  # noqa: E402
import recovery  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

HOURS = 3600


class FakeReasoning:
    """`reasoning.request` and `reasoning.for_subject`, minus the launch:
    authorized as the real one is, counted in the launch ledger, and its
    jobs kept like the real record (they survive a recovery restart)."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.calls: list[dict] = []
        self.busy = False       # another reasoning run holds the card

    def request(self, filename, need, *, via="orchestrator", action="reason",
                board=None, subject=None, **_):
        self.calls.append({"file": filename, "need": need, "action": action,
                           "subject": subject})
        if self.busy:           # as the real one: one reasoning run per card
            raise reasoning.Busy(f"a reasoning run is already in flight for "
                                 f"{filename} (ask); one at a time")
        answer =orchestrator.authorize(action, filename, board=board)
        if not answer["allowed"]:
            raise ValueError(f"not authorized: {answer['reason']} "
                             f"({answer['code']})")
        job = {"id": f"rs-{len(self.jobs) + 1:04d}", "task": filename,
               "need": need, "status": "running", "subject": subject,
               "action": action, "created": time.time()}
        self.jobs.append(job)
        orchestrator.record_launch(answer["scope"], filename.split("-")[0],
                                   action, job["id"])
        return {"job": dict(job), "duplicate": False}

    def for_subject(self, incident, attempt):
        found = [j for j in self.jobs if (j["subject"] or {}).get("incident")
                 == incident and j["subject"].get("attempt") == attempt]
        return dict(found[-1]) if found else None

    def settle(self, status="invalid", actions=(), summary="what was found"):
        job = self.jobs[-1]
        job["status"] = status
        if status == "valid":
            job["response"] = {"summary": summary, "actions": [
                {"id": f"a{n}", "action": word, "card": card, "why": "because",
                 "executable": False} for n, (word, card) in enumerate(actions)]}
        else:
            job["error"] = "the run's response was refused"
        return job


class RecoveryCase(Sandbox):
    def setUp(self):
        super().setUp()
        self.reasoning = FakeReasoning()
        self.stops: list[str] = []
        saved = dict(state.SESSIONS)
        self.addCleanup(lambda: (state.SESSIONS.clear(),
                                 state.SESSIONS.update(saved)))
        running = dict(verify.RUNNING)
        self.addCleanup(lambda: (verify.RUNNING.clear(),
                                 verify.RUNNING.update(running)))
        verify.RUNNING.clear()
        for patch in (
                mock.patch.object(reasoning, "request", self.reasoning.request),
                mock.patch.object(reasoning, "for_subject",
                                  self.reasoning.for_subject),
                mock.patch.object(agents, "stop_agent", self._stop),
                mock.patch.object(recovery, "BACKOFF_SECONDS", 0)):
            patch.start()
            self.addCleanup(patch.stop)

    def _stop(self, agent_id):
        self.stops.append(agent_id)      # asked; the process is still alive
        return {"id": agent_id}

    def launched_and_aged(self, number="1", hours=3, **bench) -> str:
        """One card launched by the orchestrator, its run `hours` old."""
        self.enable(**bench)
        self.board.add(number)
        self.tick()
        [run] = [k for k, r in state.AGENTS.items()
                 if r["task"] == f"{number}-card.md"]
        state.AGENTS[run]["started"] = time.time() - hours * HOURS
        return run

    def incidents(self) -> list[dict]:
        return recovery.all_incidents()

    def notes_of(self, kind: str) -> list[dict]:
        return [n for n in self.notes if n["kind"] == kind]


# ── 1. quiet is not stalled; a justified incident, one diagnosis ───────

class QuietButActiveIsLeftAlone(RecoveryCase):
    def assert_left_alone(self, run: str, expected: str) -> None:
        for _ in range(3):
            self.tick()
        self.assertEqual([], self.incidents())
        self.assertEqual([], self.reasoning.calls)
        self.assertEqual([], self.stops)
        self.assertEqual(1, len(self.launch.calls))      # nothing duplicated
        self.assertEqual("running", state.AGENTS[run]["status"])
        [seen] = recovery.projection()["runs"]
        self.assertEqual(expected, seen["state"])
        self.assertTrue(seen["warning"])                  # past the threshold

    def test_a_long_test_in_flight_is_quiet_active(self):
        run = self.launched_and_aged()
        state.AGENTS[run]["session"] = "s-1"
        state.SESSIONS["s-1"] = {"id": "s-1", "last": time.time() - 2 * HOURS,
                                 "inFlight": {"since": time.time() - 2 * HOURS,
                                              "summary": "running: npm test"}}
        self.assert_left_alone(run, "quiet-active")

    def test_a_check_bench_runs_is_quiet_active(self):
        run = self.launched_and_aged()
        verify.RUNNING["ev-1"] = {"task": "1-card.md", "check": "unit",
                                  "outcome": "running"}
        self.assert_left_alone(run, "quiet-active")

    def test_tool_activity_without_progress_updates_is_active(self):
        run = self.launched_and_aged()
        state.AGENTS[run]["session"] = "s-1"
        state.SESSIONS["s-1"] = {"id": "s-1", "last": time.time() - 60}
        self.assert_left_alone(run, "active")

    def test_age_alone_under_the_threshold_is_young(self):
        self.launched_and_aged(hours=1)
        self.tick()
        [seen] = recovery.projection()["runs"]
        self.assertEqual(("young", False), (seen["state"], seen["warning"]))
        self.assertEqual([], self.incidents())

    def test_a_stalled_run_opens_one_incident_and_one_diagnosis(self):
        run = self.launched_and_aged()
        for _ in range(4):
            self.tick()
        [item] = self.incidents()
        self.assertEqual(("stalled", run, "diagnosing"),
                         (item["type"], item["run"], item["status"]))
        self.assertEqual(1, len(self.reasoning.calls))
        call = self.reasoning.calls[0]
        self.assertEqual(("recovery", "recover"), (call["need"], call["action"]))
        # context-bound: the incident, its signals and its attempt travel
        self.assertEqual((item["id"], 1, 2), (call["subject"]["incident"],
                                              call["subject"]["attempt"],
                                              call["subject"]["of"]))
        self.assertEqual("alive", call["subject"]["signals"]["process"])
        self.assertEqual([], self.stops)                  # silence kills nothing
        self.assertEqual("running", state.AGENTS[run]["status"])

    def test_a_question_in_flight_is_a_wait_not_a_hold(self):
        # task 181: a person asking about the card while its incident
        # waits for a diagnosis must not turn it into a held blocker
        self.launched_and_aged()
        self.reasoning.busy = True
        for _ in range(3):
            self.tick()
        [item] = self.incidents()
        self.assertEqual("reasoning-in-flight", item["code"])
        self.assertNotIn(item["status"], ("held", "exhausted"))
        self.assertEqual([], item.get("attempts") or [])
        self.assertEqual([], self.reasoning.jobs)
        self.assertFalse(any("diagnosis-refused" in (n.get("text") or "")
                             for n in self.notes_of("blocker")))
        self.reasoning.busy = False               # the question settled
        self.tick()
        [item] = self.incidents()
        self.assertEqual("diagnosing", item["status"])
        self.assertEqual(1, len(self.reasoning.jobs))
        self.assertEqual(1, item["attempts"][0]["n"])

    def test_a_persons_own_run_is_observed_not_diagnosed(self):
        self.enable()
        self.board.add("1", stage="in-progress")
        state.AGENTS["a-person"] = {"task": "1-card.md", "mode": "work",
                                    "status": "running",
                                    "started": time.time() - 5 * HOURS}
        self.tick()
        self.tick()
        self.assertEqual([], self.incidents())
        self.assertEqual([], self.reasoning.calls)
        [seen] = recovery.projection()["runs"]
        self.assertEqual(("stalled", False), (seen["state"], seen["orchestrated"]))

    def test_a_stalled_run_that_comes_back_resolves_by_observation(self):
        run = self.launched_and_aged()
        self.tick()
        state.AGENTS[run]["session"] = "s-1"
        state.SESSIONS["s-1"] = {"id": "s-1", "last": time.time()}
        self.tick()
        [item] = self.incidents()
        self.assertEqual("resolved", item["status"])
        self.assertIn("active again", item["reason"])


class InFlightCallsThroughTheEventBridge(RecoveryCase):
    """PR 147 review, item 2: in-flight tool calls are tracked per call
    through the real event path, so a quick parallel call finishing does
    not hide a long test still running — and a live worker is not read
    as stalled, diagnosed and stopped for it."""

    def setUp(self):
        super().setUp()
        for name in ("_CALLS", "_ANON"):
            registry = getattr(events, name, None)
            if registry is not None:
                saved = dict(registry)
                registry.clear()
                self.addCleanup(lambda r=registry, s=saved: (r.clear(), r.update(s)))
        for registry in (events._SEEN, events._WRITTEN, state.EVENTS):
            saved = dict(registry)
            registry.clear()
            self.addCleanup(lambda r=registry, s=saved: (r.clear(), r.update(s)))
        saved_bridged = set(events._BRIDGED)
        self.addCleanup(lambda: (events._BRIDGED.clear(),
                                 events._BRIDGED.update(saved_bridged)))
        patch = mock.patch.object(config, "SESSIONS_DIR", self.dir / "sessions")
        patch.start()
        self.addCleanup(patch.stop)

    def hook(self, sid, phase, call, *, running=False, kind="command",
             summary=None, **extra):
        raw = {"v": 1, "session": sid, "kind": kind, "running": running,
               "summary": summary or f"{phase} {call}", **extra}
        if call is not None:
            raw["id"] = f"{phase}:{call}"
        return events.ingest_event(raw)

    def quiet_for_hours(self, sid, hours=2):
        """The session's last event, and the long call's start, long ago."""
        with state.LOCK:
            meta = state.SESSIONS[sid]
            meta["last"] = time.time() - hours * HOURS
            if meta.get("inFlight"):
                meta["inFlight"] = {**meta["inFlight"],
                                    "since": time.time() - hours * HOURS}

    def stop_if_diagnosed(self):
        """Play the rest of a recovery: if a diagnosis was asked for, it
        proposes the restart, and the next pass carries it out."""
        for _ in range(3):
            self.tick()
            if self.reasoning.jobs and self.reasoning.jobs[-1]["status"] == "running":
                self.reasoning.settle("valid", actions=[("recover", "1")])

    def test_a_quick_parallel_call_finishing_keeps_the_long_one_in_flight(self):
        run = self.launched_and_aged()
        state.AGENTS[run]["session"] = "s-1"
        self.hook("s-1", "PreToolUse", "toolu_long", running=True,
                  summary="running: python3 -m unittest", agent=run)
        self.hook("s-1", "PreToolUse", "toolu_quick", running=True,
                  summary="running: git status", agent=run)
        self.hook("s-1", "PostToolUse", "toolu_quick", summary="ran: git status",
                  agent=run)
        flight = state.SESSIONS["s-1"].get("inFlight")
        self.quiet_for_hours("s-1")
        self.stop_if_diagnosed()
        self.assertEqual([], self.stops, "a live worker was stopped")
        self.assertIsNotNone(flight, "the long test is still running")
        self.assertEqual("running: python3 -m unittest", flight["summary"])
        self.assertEqual([], self.incidents())
        self.assertEqual([], self.reasoning.calls)
        self.assertEqual("running", state.AGENTS[run]["status"])
        [seen] = recovery.projection()["runs"]
        self.assertEqual("quiet-active", seen["state"])

    def test_other_events_do_not_clear_a_call_in_flight(self):
        self.hook("s-2", "PreToolUse", "toolu_long", running=True)
        self.hook("s-2", "PostToolUse", "toolu_read", kind="read")
        self.hook("s-2", "PreToolUse", "toolu_todo", kind="plan")
        self.assertIsNotNone(state.SESSIONS["s-2"].get("inFlight"))
        self.hook("s-2", "PostToolUse", "toolu_long")
        self.assertIsNone(state.SESSIONS["s-2"].get("inFlight"))

    def test_a_failed_finish_and_a_turn_boundary_close_calls(self):
        self.hook("s-3", "PreToolUse", "toolu_a", running=True)
        self.hook("s-3", "PostToolUseFailure", "toolu_a")
        self.assertIsNone(state.SESSIONS["s-3"].get("inFlight"))
        self.hook("s-3", "PreToolUse", "toolu_b", running=True)
        self.hook("s-3", "Stop", "x", kind="idle")
        self.assertIsNone(state.SESSIONS["s-3"].get("inFlight"),
                          "no tool runs once the turn is over")

    def test_a_bridge_without_ids_brackets_each_call(self):
        # opencode's plugin sends no id: a start per tool and a finish per tool
        self.hook("s-4", None, None, running=True, summary="running: bash")
        self.hook("s-4", None, None, running=True, summary="running: read")
        self.hook("s-4", None, None, kind="read")
        self.assertIsNotNone(state.SESSIONS["s-4"].get("inFlight"))
        self.hook("s-4", None, None, kind="command")
        self.assertIsNone(state.SESSIONS["s-4"].get("inFlight"))

    def test_a_long_call_finishing_lets_a_real_stall_be_seen(self):
        run = self.launched_and_aged()
        state.AGENTS[run]["session"] = "s-5"
        self.hook("s-5", "PreToolUse", "toolu_long", running=True, agent=run)
        self.hook("s-5", "PostToolUse", "toolu_long", agent=run)
        self.quiet_for_hours("s-5")
        self.tick()
        [item] = self.incidents()
        self.assertEqual(("stalled", run), (item["type"], item["run"]))


# ── 2. limits across ticks, restarts and scopes ────────────────────────

class LimitsHold(RecoveryCase):
    def test_attempts_are_bounded_across_ticks_and_a_restart(self):
        self.launched_and_aged()
        self.tick()
        self.reasoning.settle("invalid")
        self.tick()                                   # attempt 2
        self.assertEqual(2, len(self.reasoning.calls))
        state.AGENTS.update({})                       # a restart: disk only
        self.reasoning.settle("failed")
        for _ in range(4):
            self.tick()
        self.assertEqual(2, len(self.reasoning.calls))
        [item] = self.incidents()
        self.assertEqual(("exhausted", "recovery-exhausted"),
                         (item["status"], item["code"]))
        self.assertEqual([1, 2], [a["n"] for a in item["attempts"]])
        held = recovery.projection()["held"]
        self.assertEqual(item["id"], held[0]["id"])
        self.assertTrue(held[0]["choices"])
        self.assertIn("exhausted", [n["outcome"] for n in self.notes_of("attempt")])

    def test_an_intent_without_a_job_is_reconciled_never_counted(self):
        self.launched_and_aged()
        self.tick()
        [item] = self.incidents()
        # a pass that died between the intent and the request
        item["attempts"].append({"n": 2, "status": "intent", "epoch": time.time()})
        self.reasoning.settle("invalid")
        recovery._save(item)
        self.tick()
        [item] = self.incidents()
        self.assertEqual(["settled", "not-launched", "launched"],
                         [a["status"] for a in item["attempts"]])
        self.assertEqual(2, recovery.public(item)["launched"])
        self.assertEqual(2, len(self.reasoning.calls))

    def test_a_configured_cap_is_the_cap(self):
        self.launched_and_aged(capacity={"recoveryAttempts": 1})
        self.tick()
        self.reasoning.settle("invalid")
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.reasoning.calls))
        self.assertEqual("exhausted", self.incidents()[0]["status"])

    def test_a_phase_scope_lowers_the_cap_and_spends_its_own_starts(self):
        doc = agreement(capacity={"implementation": 4, "dailyStarts": 10})
        doc["phases"] = {"9": {"enabled": True,
                               "ceilings": {"recoveryAttempts": 1},
                               "allocation": {"implementation": 2,
                                              "dailyStarts": 5}}}
        self.put_in_force(doc)
        self.own_execution()
        self.board.add("9", stage="in-progress", is_phase=True)
        name = self.board.add("5", stage="in-progress", phase="9", index=1)
        op = operations.begin(operations.key_for("implement", name, "b", 1),
                              action="implement", card=name, number="5",
                              scope="phase:9", pool="implementation",
                              basis="b", attempt=1)
        operations.update(op["key"], "launched", agent="member-run")
        state.AGENTS["member-run"] = {"task": name, "mode": "work",
                                      "status": "running",
                                      "started": time.time() - 4 * HOURS}
        recovery.run(self.board.collect())
        [item] = self.incidents()
        self.assertEqual(("phase:9", 1), (item["scope"], item["cap"]))
        self.reasoning.settle("invalid")
        recovery.run(self.board.collect())
        self.assertEqual(1, len(self.reasoning.calls))
        self.assertEqual("exhausted", self.incidents()[0]["status"])
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(2, used["startsToday"]["phase:9"])   # the run + one diagnosis

    def test_a_diagnosis_is_a_launch_and_waits_for_a_start(self):
        self.launched_and_aged(capacity={"dailyStarts": 1})
        self.tick()
        self.tick()
        self.assertEqual([], self.reasoning.calls)
        [item] = self.incidents()
        self.assertEqual(("open", "daily-starts-exhausted"),
                         (item["status"], item["code"]))

    def test_the_breaker_is_durable_and_holds_diagnoses(self):
        self.launched_and_aged()
        for card in ("1", "2", "3"):
            recovery.breaker_failure(card, "run-unknown")
        self.assertTrue(scheduler.breaker()["open"])
        state.AGENTS.update({})          # a restart forgets memory, not the file
        self.tick()
        self.assertEqual([], self.reasoning.calls)
        self.assertEqual(["1", "2", "3"], recovery.breaker()["cards"])
        self.assertTrue(recovery.breaker()["choices"])
        recovery.breaker_reset("tester")
        self.tick()
        self.assertEqual(1, len(self.reasoning.calls))

    def test_failures_outside_the_window_never_trip_it(self):
        now = time.time()
        recovery.breaker_failure("1", "x", now - 2 * recovery.BREAKER_WINDOW)
        recovery.breaker_failure("2", "x", now - recovery.BREAKER_WINDOW - 1)
        recovery.breaker_failure("3", "x", now)
        self.assertFalse(recovery.breaker()["open"])

    def end_failed(self, run: str, excerpt: str) -> None:
        state.AGENTS[run].update(status="failed", rc=1,
                                 failure={"excerpt": excerpt, "rc": 1})

    def test_a_transient_run_failure_is_retried_bounded(self):
        self.enable(capacity={"transientRetries": 1})
        self.board.add("1")
        self.tick()
        self.end_failed("run-1", "API Error: 529 overloaded_error")
        self.tick()                                    # observed, then retried
        self.assertEqual(2, len(self.launch.calls))
        [first, second] = self.ops()
        self.assertEqual("transient", first["failure"]["class"])
        self.assertEqual([], self.reasoning.calls)     # a retry, not an incident
        self.end_failed("run-2", "API Error: 529 overloaded_error")
        for _ in range(3):
            self.tick()
        self.assertEqual(2, len(self.launch.calls))    # one try, one retry
        item = next(i for i in scheduler.plan()["items"])
        self.assertEqual("retries-exhausted", item["code"])
        # spent retries are an incident: diagnosed, not retried again
        [incident] = [i for i in self.incidents() if i["type"] == "failed"]
        self.assertEqual("transient", incident["failure"]["class"])

    def test_a_deterministic_failure_is_diagnosed_not_retried(self):
        self.enable()
        self.board.add("1")
        self.tick()
        self.end_failed("run-1", "AssertionError: 2 != 3")
        for _ in range(3):
            self.tick()
        self.assertEqual(1, len(self.launch.calls))
        [item] = self.incidents()
        self.assertEqual(("failed", "deterministic"),
                         (item["type"], item["failure"]["class"]))
        self.assertEqual(1, len(self.reasoning.calls))

    def test_a_failure_the_person_moved_on_from_closes(self):
        self.enable(capacity={"recoveryAttempts": 3})
        self.board.add("1")
        self.tick()
        self.end_failed("run-1", "AssertionError: 2 != 3")
        self.tick()
        self.board.cards["1-card.md"]["stage"] = "review"   # a person's move
        self.reasoning.settle("invalid")
        self.tick()
        [item] = self.incidents()
        self.assertEqual("resolved", item["status"])
        self.assertEqual(1, len(self.reasoning.calls))

    def test_classification_never_presumes_transient(self):
        self.assertEqual("unknown", recovery.classify("it stopped"))
        self.assertEqual("unknown", recovery.classify("card 502 went quiet"))
        self.assertEqual("transient", recovery.classify("HTTP 503 from upstream"))
        self.assertEqual("denied", recovery.classify("Permission denied: push"))
        self.assertEqual("deterministic", recovery.classify("Traceback (most…"))


# ── 3. unsafe recovery is refused ──────────────────────────────────────

class UnsafeRecoveryIsRefused(RecoveryCase):
    def diagnosed(self, *actions, **bench) -> str:
        run = self.launched_and_aged(**bench)
        self.tick()
        self.reasoning.settle("valid", actions or [("recover", "1")])
        return run

    def test_an_unknown_outcome_on_the_card_holds_everything(self):
        self.launched_and_aged()
        op = operations.begin(operations.key_for("implement", "1-card.md", "z", 9),
                              action="implement", card="1-card.md", number="1",
                              scope="bench", pool="implementation", basis="z",
                              attempt=9)
        operations.update(op["key"], "unknown")
        self.tick()
        [item] = self.incidents()
        self.assertEqual(("held", "outcome-unknown"), (item["status"], item["code"]))
        self.assertEqual([], self.reasoning.calls)

    def test_missing_authority_refuses_the_diagnosis(self):
        doc = agreement()
        doc["bench"]["authority"]["recovery"] = False
        self.put_in_force(doc)
        self.own_execution()
        self.board.add("1")
        self.tick()
        state.AGENTS["run-1"]["started"] = time.time() - 3 * HOURS
        self.tick()
        [item] = self.incidents()
        self.assertEqual(("held", "not-authorized"), (item["status"], item["code"]))
        self.assertEqual([], self.reasoning.calls)
        self.assertTrue(any(n["state"] == "open" for n in self.notes_of("blocker")))

    def test_another_run_in_the_worktree_refuses_the_restart(self):
        run = self.diagnosed()
        state.AGENTS["a-person"] = {"task": "1-card.md", "mode": "work",
                                    "status": "running", "started": time.time()}
        self.tick()
        [item] = self.incidents()
        self.assertEqual(("held", "worktree-active"), (item["status"], item["code"]))
        self.assertEqual([], self.stops)
        self.assertEqual(1, len(self.launch.calls))
        self.assertEqual("running", state.AGENTS[run]["status"])

    def test_a_question_in_flight_makes_the_restart_wait_not_hold(self):
        # task 181, review of 10062880: a person's question about the card
        # between the diagnosis and the restart is a reasoning run on the
        # card — no worktree, no worker — so the restart waits for it
        run = self.diagnosed()
        state.AGENTS["question"] = {"task": "1-card.md", "mode": "reason",
                                    "status": "running", "started": time.time()}
        self.tick()
        self.tick()
        [item] = self.incidents()
        self.assertEqual("reasoning-in-flight", item["code"])
        self.assertNotIn(item["status"], ("held", "exhausted"))
        self.assertEqual([], self.stops)
        self.assertEqual(1, len(self.launch.calls))
        self.assertFalse(any("worktree-active" in (n.get("text") or "")
                             for n in self.notes_of("blocker")))
        # the question settles: the restart carries on, stop first
        state.AGENTS["question"]["status"] = "done"
        self.tick()
        self.assertEqual([run], self.stops)
        state.AGENTS[run]["status"] = "stopped"
        # and one asked while the stop is under way waits too
        state.AGENTS["question-2"] = {"task": "1-card.md", "mode": "reason",
                                      "status": "running", "started": time.time()}
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        self.assertEqual("reasoning-in-flight", self.incidents()[0]["code"])
        state.AGENTS["question-2"]["status"] = "done"
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        self.assertEqual("remediating", self.incidents()[0]["status"])

    def test_a_worker_active_again_behind_a_question_is_not_stopped(self):
        # task 181, review of 40e51b1: the restart waits behind a question
        # before any stop is asked; a worker that comes back meanwhile is
        # read as before a diagnosis — resolved, never stopped
        run = self.diagnosed()
        state.AGENTS["question"] = {"task": "1-card.md", "mode": "reason",
                                    "status": "running", "started": time.time()}
        self.tick()
        self.assertEqual("reasoning-in-flight", self.incidents()[0]["code"])
        state.AGENTS[run]["session"] = "s-1"
        state.SESSIONS["s-1"] = {"id": "s-1", "last": time.time()}
        self.tick()
        [item] = self.incidents()
        self.assertEqual("resolved", item["status"])
        self.assertIn("active again", item["reason"])
        state.AGENTS["question"]["status"] = "done"
        self.tick()
        self.assertEqual([], self.stops)
        self.assertEqual(1, len(self.launch.calls))

    def test_a_restart_waits_for_an_observed_stop(self):
        run = self.diagnosed()
        self.tick()
        self.assertEqual([run], self.stops)
        self.assertEqual("stopping", self.incidents()[0]["status"])
        self.tick()                                    # still alive: no restart
        self.assertEqual(1, len(self.launch.calls))
        self.assertEqual([run], self.stops)            # asked once
        state.AGENTS[run]["status"] = "stopped"        # observed gone
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        [item] = self.incidents()
        self.assertEqual("remediating", item["status"])
        restart = operations.read(item["remediation"]["operation"])
        self.assertEqual(("implement", "launched"), (restart["action"], restart["status"]))
        # the restart spent a start like any launch
        used = orchestrator.usage(agreements.effective(),
                                  scheduler._tasks(self.board.collect()))
        self.assertEqual(3, used["launchesPerCard"]["1"])  # run, diagnosis, restart

    def test_a_full_pool_does_not_refuse_the_stop_that_frees_it(self):
        run = self.diagnosed(capacity={"implementation": 1, "reasoning": 1})
        # every slot taken: the worker holds the implementation slot and
        # someone's reasoning run the reasoning one
        state.AGENTS["thinking"] = {"task": "1-card.md", "mode": "reason",
                                    "status": "running", "started": time.time()}
        self.assertEqual("slots-full", orchestrator.authorize(
            "recover", "1-card.md")["code"])
        with mock.patch.object(recovery, "_others", lambda item, *a: []):
            self.tick()
        self.assertEqual([run], self.stops)
        state.AGENTS.pop("thinking")
        self.tick()
        self.assertEqual([run], self.stops)
        state.AGENTS[run]["status"] = "stopped"
        self.tick()
        self.assertEqual(2, len(self.launch.calls))
        self.assertEqual("remediating", self.incidents()[0]["status"])

    def test_a_pause_makes_an_incident_wait_not_hold(self):
        self.launched_and_aged()
        revision = agreements.effective()["revision"]
        agreements.set_scope("bench", "pause", revision)
        self.tick()
        [item] = self.incidents()
        self.assertEqual(("open", "paused"), (item["status"], item["code"]))
        revision = agreements.effective()["revision"]
        agreements.set_scope("bench", "resume", revision)
        self.tick()
        self.assertEqual(1, len(self.reasoning.calls))

    def test_a_stop_never_observed_is_held(self):
        self.diagnosed()
        with mock.patch.object(recovery, "STOP_GRACE", -1):
            self.tick()
            self.tick()
        [item] = self.incidents()
        self.assertEqual(("held", "stop-unconfirmed"), (item["status"], item["code"]))
        self.assertEqual(1, len(self.launch.calls))

    def test_the_restart_is_reauthorized_before_it_starts(self):
        run = self.diagnosed()
        self.tick()
        state.AGENTS[run]["status"] = "stopped"
        revision = agreements.effective()["revision"]
        agreements.set_scope("bench", "pause", revision)
        self.tick()
        self.assertEqual(1, len(self.launch.calls))
        self.assertEqual("paused", self.incidents()[0]["code"])


# ── 4. a diagnosis proposes; bench observes ────────────────────────────

class AgentOutputIsAProposal(RecoveryCase):
    def test_a_proposal_bench_does_not_execute_is_held(self):
        self.launched_and_aged()
        self.tick()
        self.reasoning.settle("valid", [("merge", "1"), ("implement", "1")],
                              summary="Fixed it; everything passes now.")
        for _ in range(3):
            self.tick()
        [item] = self.incidents()
        self.assertEqual(("held", "diagnosed"), (item["status"], item["code"]))
        self.assertEqual([], self.stops)
        self.assertEqual(1, len(self.launch.calls))
        self.assertNotEqual("resolved", item["status"])   # a claim is not a remedy

    def test_a_restart_proposed_for_another_card_is_ignored(self):
        self.launched_and_aged()
        self.tick()
        self.reasoning.settle("valid", [("recover", "77")])
        self.tick()
        self.assertEqual("held", self.incidents()[0]["status"])
        self.assertEqual([], self.stops)

    def test_a_restart_resolves_only_when_its_run_is_seen_working(self):
        run = self.launched_and_aged()
        self.tick()
        self.reasoning.settle("valid", [("recover", "1")])
        self.tick()
        state.AGENTS[run]["status"] = "stopped"
        self.tick()
        self.tick()
        self.assertEqual("remediating", self.incidents()[0]["status"])
        state.AGENTS["run-2"]["session"] = "s-2"
        state.SESSIONS["s-2"] = {"id": "s-2", "last": time.time() + 1}
        self.tick()
        [item] = self.incidents()
        self.assertEqual("resolved", item["status"])
        self.assertIn("working", item["reason"])

    def test_a_person_resolves_a_held_incident_and_nothing_runs(self):
        self.launched_and_aged(capacity={"recoveryAttempts": 1})
        self.tick()
        self.reasoning.settle("invalid")
        self.tick()
        [item] = self.incidents()
        before = (len(self.launch.calls), len(self.reasoning.calls))
        recovery.resolve(item["id"], "tester")
        self.tick()
        self.assertEqual(before, (len(self.launch.calls), len(self.reasoning.calls)))
        self.assertEqual("resolved-by-person", self.incidents()[0]["code"])
        with self.assertRaises(ValueError):
            recovery.resolve(item["id"], "tester")


# ── 5. the card keeps everything ───────────────────────────────────────

class TheCardKeepsEverything(RecoveryCase):
    def test_every_step_is_a_card_record_of_the_contract(self):
        run = self.launched_and_aged()
        self.tick()
        self.reasoning.settle("valid", [("recover", "1")])
        self.tick()
        state.AGENTS[run]["status"] = "stopped"
        self.tick()
        state.AGENTS["run-2"]["session"] = "s-2"
        state.SESSIONS["s-2"] = {"id": "s-2", "last": time.time() + 1}
        self.tick()
        kinds = {n["kind"] for n in self.notes}
        self.assertLessEqual({"blocker", "attempt", "action"}, kinds)
        outcomes = [n.get("outcome") for n in self.notes_of("attempt")]
        self.assertEqual(["launched", "valid", "resolved"], outcomes)
        phases = [(n.get("outcome"), n.get("phase")) for n in self.notes_of("action")
                  if n.get("action") in ("recover", "implement")
                  and str(n.get("operation", "")).startswith(("in-", "op-"))]
        self.assertIn(("stop", "intent"), phases)
        self.assertIn(("stopped", "receipt"), phases)
        self.assertIn(("launched", "receipt"), phases)
        states = [n["state"] for n in self.notes_of("blocker")
                  if n.get("operation", "").startswith("in-")]
        self.assertEqual(["open", "cleared"], states)
        for note in self.notes:
            # exactly what the real `decisions.note` accepts: its kinds,
            # its fields, its key shape — nothing private, nothing raw
            fields = set(note) - {"file", "kind", "key"}
            self.assertLessEqual(fields, set(decisions.FIELDS[note["kind"]]), note)
            self.assertRegex(note["key"], r"^[A-Za-z0-9._:-]{8,80}$")

    def test_reading_writes_launches_and_stops_nothing(self):
        self.launched_and_aged()
        self.tick()
        before = snapshot(self.local)
        calls = len(self.reasoning.calls)
        for _ in range(3):
            recovery.projection()
            scheduler.projection()
        self.assertEqual(before, snapshot(self.local))
        self.assertEqual(calls, len(self.reasoning.calls))
        self.assertEqual([], self.stops)


class RecoveryFingerprint(unittest.TestCase):
    def test_an_attempt_is_part_of_the_question(self):
        bound = {"card": "1", "cardRev": "x"}
        one = reasoning.fingerprint("recovery", bound, {"incident": "in-1", "attempt": 1})
        two = reasoning.fingerprint("recovery", bound, {"incident": "in-1", "attempt": 2})
        self.assertNotEqual(one, two)
        self.assertEqual(one, reasoning.fingerprint("recovery", bound,
                                                    {"incident": "in-1", "attempt": 1}))
        # a reasoning request without a subject keeps its old identity
        self.assertEqual(reasoning.fingerprint("readiness", bound),
                         reasoning.fingerprint("readiness", bound, None))


if __name__ == "__main__":
    unittest.main()
