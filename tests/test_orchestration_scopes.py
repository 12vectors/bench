"""Scoped orchestration: agreements, authority, capacity and ownership (task 137).

Each class is one acceptance criterion of the card:

- an absent agreement changes nothing and leaves orchestration off, and
  explicit activation authorizes a launch with no shadow stage;
- a phase scope only narrows: authority intersects, capacity is
  allocated from one total and never duplicated, an expansion is
  refused, a moved revision is refused, a parent's pause reaches its
  phases, and a hand-back stops automation without touching the card;
- a user-selected ungated merge delegation validates and is labelled
  ungated, while configured requirements still refuse an unmet merge;
- two processes cannot own one installation, and neither a replica nor a
  stale former owner's heartbeat authorizes execution or a handover;
- the public reading separates effective from draft, carries no secret
  or path, writes nothing, and the route serves it.

    python3 -m unittest tests.test_orchestration_scopes -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import agreements  # noqa: E402
import config  # noqa: E402
import execowner  # noqa: E402
import orchestrator  # noqa: E402
import state  # noqa: E402


def snapshot(*roots: Path) -> dict:
    found = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            found[str(path)] = path.read_bytes() if path.is_file() else None
    return found


def card(number: str, stage: str = "to-do", *, phase: str | None = None,
         is_phase: bool = False) -> dict:
    return {"file": f"{number}-card.md", "number": number, "stage": stage,
            "isPhase": is_phase,
            "phase": {"number": phase, "file": f"{phase}-phase.md"} if phase else None}


def board(*tasks: dict) -> dict:
    return {"stages": [{"slug": "all", "tasks": list(tasks)}]}


def agreement(**bench) -> dict:
    doc = agreements.new_draft()
    doc["bench"].update(enabled=True)
    doc["bench"]["authority"] = {w: True for w in agreements.AUTHORITIES}
    for key, value in bench.items():
        if isinstance(value, dict) and isinstance(doc["bench"].get(key), dict):
            doc["bench"][key].update(value)
        else:
            doc["bench"][key] = value
    return doc


class Sandbox(unittest.TestCase):
    """A tool state, a local directory and a work root of their own."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-orch-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.local, self.work = self.dir / "local", self.dir / "work"
        self.local.mkdir()
        self.work.mkdir()
        (self.work / "tasks").mkdir()
        for patch in (mock.patch.object(config, "LOCAL", self.local),
                      mock.patch.object(config, "STATE", self.local / "state"),
                      mock.patch.object(config, "COMMIT_MOVES", False),
                      mock.patch.object(config, "work_root", lambda: self.work)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(execowner.release_lock)
        saved = dict(state.AGENTS)
        state.AGENTS.clear()
        self.addCleanup(lambda: (state.AGENTS.clear(), state.AGENTS.update(saved)))

    def put_in_force(self, doc: dict) -> str:
        agreements.save_draft(doc, agreements.revision(agreements.draft_document()[0]))
        draft = agreements.revision(doc)
        return agreements.apply(agreements.effective()["revision"], draft)["revision"]

    def own_execution(self) -> None:
        execowner.designate("none")
        self.assertTrue(execowner.acquire_lock())

    def running(self, task: str, mode: str = "work") -> None:
        state.AGENTS[f"a-{len(state.AGENTS)}"] = {"task": task, "mode": mode,
                                                  "status": "running"}


class AbsentAgreementIsOff(Sandbox):
    def test_no_agreement_reads_off_and_writes_nothing(self):
        tasks = board(card("7"))
        before = snapshot(self.local, self.work)
        found = orchestrator.reading(tasks)
        self.assertEqual(found["status"], "off")
        self.assertFalse(found["effective"]["configured"])
        self.assertEqual(found["effective"]["scopes"], [])
        self.assertIsNone(found["draft"])
        self.assertFalse(found["installation"]["executes"])
        # no installation id, no lock file, no draft: reading made nothing
        self.assertEqual(snapshot(self.local, self.work), before)

    def test_no_agreement_authorizes_nothing_even_for_the_owner(self):
        self.own_execution()
        answer = orchestrator.authorize("implement", "7-card.md",
                                        board=board(card("7")))
        self.assertFalse(answer["allowed"])
        self.assertEqual(answer["code"], "off")

    def test_a_new_draft_is_off_and_carries_the_d7_defaults(self):
        draft = agreements.new_draft()
        self.assertFalse(draft["bench"]["enabled"])
        self.assertEqual(draft["bench"]["capacity"], {
            "implementation": 2, "review": 1, "reasoning": 0, "dailyStarts": 20,
            "launchesPerCard": 6, "repairRounds": 2, "transientRetries": 2,
            "runAgeMinutes": 120})
        self.assertFalse(any(draft["bench"]["authority"].values()))
        resolved = agreements.resolve(draft)
        self.assertTrue(resolved["valid"])
        self.assertFalse(resolved["scopes"][0]["active"])

    def test_explicit_activation_authorizes_a_launch_without_a_shadow_stage(self):
        self.own_execution()
        self.put_in_force(agreement())
        answer = orchestrator.authorize("implement", "7-card.md",
                                        board=board(card("7")))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual(answer["scope"], "bench")
        self.assertNotIn("shadow", json.dumps(agreements.new_draft()))

    def test_backlog_and_done_are_never_managed(self):
        self.own_execution()
        self.put_in_force(agreement())
        for stage in ("backlog", "done"):
            answer = orchestrator.authorize("implement", "7-card.md",
                                            board=board(card("7", stage)))
            self.assertEqual(answer["code"], "stage-not-managed")


class PhaseScopesOnlyNarrow(Sandbox):
    def with_phase(self, **phase) -> dict:
        doc = agreement()
        doc["phases"] = {"136": {"enabled": True, **phase}}
        return doc

    def test_authority_is_the_intersection_with_provenance(self):
        doc = self.with_phase(authority={"merge": False})
        doc["bench"]["authority"]["recovery"] = False
        resolved = agreements.resolve(doc)
        self.assertTrue(resolved["valid"], resolved["problems"])
        phase = agreements.scope(resolved, "phase:136")
        self.assertEqual(phase["authority"]["merge"],
                         {"allowed": False, "by": "phase:136"})
        self.assertEqual(phase["authority"]["recovery"],
                         {"allowed": False, "by": "bench"})
        self.assertEqual(phase["authority"]["implement"],
                         {"allowed": True, "by": "bench"})

    def test_privilege_expansion_is_refused_and_everything_is_off(self):
        doc = self.with_phase(authority={"merge": True})
        doc["bench"]["authority"]["merge"] = False
        resolved = agreements.resolve(doc)
        self.assertFalse(resolved["valid"])
        self.assertIn("authority-expansion",
                      [p["code"] for p in resolved["problems"]])
        self.assertFalse(any(s["active"] for s in resolved["scopes"]))
        self.assertFalse(any(a["allowed"] for s in resolved["scopes"]
                             for a in s["authority"].values()))
        agreements.save_draft(doc, "none")
        with self.assertRaises(agreements.Refused) as refused:
            agreements.apply("none", agreements.revision(doc))
        self.assertEqual(refused.exception.status, 409)
        self.assertFalse(agreements.document_path().exists())

    def test_a_ceiling_may_only_be_lowered(self):
        resolved = agreements.resolve(self.with_phase(
            ceilings={"launchesPerCard": 9}))
        self.assertIn("ceiling-expansion", [p["code"] for p in resolved["problems"]])
        lowered = agreements.resolve(self.with_phase(
            ceilings={"launchesPerCard": 3}))
        phase = agreements.scope(lowered, "phase:136")
        self.assertEqual(phase["ceilings"]["launchesPerCard"],
                         {"value": 3, "by": "phase:136"})
        self.assertEqual(phase["ceilings"]["repairRounds"],
                         {"value": 2, "by": "bench"})

    def test_capacity_is_allocated_from_the_parent_never_duplicated(self):
        resolved = agreements.resolve(self.with_phase(
            allocation={"implementation": 1, "dailyStarts": 8}))
        bench = agreements.scope(resolved, "bench")
        phase = agreements.scope(resolved, "phase:136")
        self.assertEqual(bench["capacity"]["implementation"],
                         {"total": 2, "allocated": 1, "own": 1})
        self.assertEqual(phase["capacity"]["implementation"]["own"], 1)
        self.assertEqual(bench["capacity"]["dailyStarts"]["own"], 12)
        self.assertEqual(sum(s["capacity"]["implementation"]["own"]
                             for s in resolved["scopes"]), 2)

        doc = self.with_phase(allocation={"implementation": 2})
        doc["phases"]["140"] = {"enabled": True, "allocation": {"implementation": 1}}
        over = agreements.resolve(doc)
        self.assertIn("capacity-overcommitted", [p["code"] for p in over["problems"]])

    def test_running_work_fills_the_scope_that_holds_it(self):
        self.own_execution()
        self.put_in_force(self.with_phase(allocation={"implementation": 1}))
        tasks = board(card("7"), card("8"), card("136", is_phase=True),
                      card("137", "in-progress", phase="136"),
                      card("138", phase="136"))
        self.running("137-card.md")                 # the phase's one slot
        self.assertEqual(orchestrator.authorize("implement", "138-card.md",
                                                board=tasks)["code"], "slots-full")
        self.assertTrue(orchestrator.authorize("implement", "7-card.md",
                                               board=tasks)["allowed"])
        self.running("8-card.md")                   # the Bench scope's own slot
        self.assertEqual(orchestrator.authorize("implement", "7-card.md",
                                                board=tasks)["code"], "slots-full")

    def test_a_manual_run_no_scope_manages_still_counts(self):
        self.own_execution()
        self.put_in_force(agreement())
        tasks = board(card("7"), card("50", "in-progress", phase="49"),
                      card("49", is_phase=True))
        self.running("50-card.md")
        self.running("50-card.md")
        found = orchestrator.usage(agreements.effective(), _tasks(tasks))
        self.assertEqual(found["unscoped"]["implementation"], 2)
        self.assertEqual(orchestrator.authorize("implement", "7-card.md",
                                                board=tasks)["code"], "slots-full")
        self.assertEqual(len(state.AGENTS), 2, "nothing was cancelled")

    def test_reasoning_launches_count_as_real_usage(self):
        self.own_execution()
        self.put_in_force(agreement(capacity={"implementation": 1}))
        tasks = board(card("7"), card("8"))
        self.running("8-card.md", mode="refine")
        self.assertEqual(orchestrator.authorize("reason", "7-card.md",
                                                board=tasks)["code"], "slots-full")

    def test_daily_starts_and_launches_per_card_are_counted(self):
        self.own_execution()
        self.put_in_force(agreement(capacity={"dailyStarts": 3,
                                              "launchesPerCard": 2}))
        tasks = board(card("7"), card("8"))
        for n in range(2):
            orchestrator.record_launch("bench", "7", "implement", f"op-{n}")
        self.assertEqual(orchestrator.authorize("implement", "7-card.md",
                                                board=tasks)["code"],
                         "card-launches-exhausted")
        orchestrator.record_launch("bench", "8", "reason", "op-3")
        self.assertEqual(orchestrator.authorize("implement", "8-card.md",
                                                board=tasks)["code"],
                         "daily-starts-exhausted")

    def test_a_moved_revision_is_refused_and_nothing_is_written(self):
        first = agreement()
        agreements.save_draft(first, "none")
        previewed = agreements.revision(first)
        second = agreement(paused=True)
        agreements.save_draft(second, previewed)     # someone edits the draft
        with self.assertRaises(agreements.Refused) as stale:
            agreements.apply("none", previewed)
        self.assertTrue(stale.exception.extra.get("stale"))
        self.assertFalse(agreements.document_path().exists())
        with self.assertRaises(agreements.Refused) as stale_draft:
            agreements.save_draft(first, previewed)
        self.assertEqual(stale_draft.exception.status, 409)
        applied = agreements.apply("none", agreements.revision(second))["revision"]
        with self.assertRaises(agreements.Refused):
            agreements.set_scope("bench", "resume", "not-" + applied)
        self.assertEqual(agreements.effective()["revision"], applied)

    def test_preview_compares_and_writes_nothing(self):
        self.put_in_force(agreement())
        agreements.save_draft(agreement(paused=True),
                              agreements.revision(None))
        before = snapshot(self.local)
        found = agreements.preview()
        self.assertIn({"scope": "bench", "what": "paused", "before": False,
                       "after": True}, found["changes"])
        self.assertEqual(snapshot(self.local), before)

    def test_a_parent_pause_reaches_its_phases_and_observes_work_in_flight(self):
        self.own_execution()
        revision = self.put_in_force(self.with_phase(
            allocation={"implementation": 1}))
        tasks = board(card("7"), card("136", is_phase=True),
                      card("138", phase="136"), card("139", "in-progress", phase="136"))
        self.running("139-card.md")
        agreements.set_scope("bench", "pause", revision)
        phase = agreements.scope(agreements.effective(), "phase:136")
        self.assertEqual(phase["pausedBy"], "bench")
        self.assertFalse(phase["active"])
        answer = orchestrator.authorize("implement", "138-card.md", board=tasks)
        self.assertEqual((answer["code"], answer["pausedBy"]), ("paused", "bench"))
        self.assertEqual([r["status"] for r in state.AGENTS.values()], ["running"],
                         "a pause cancels nothing in flight")

    def test_a_phase_pause_affects_that_phase_only(self):
        self.own_execution()
        revision = self.put_in_force(self.with_phase(
            allocation={"implementation": 1}))
        agreements.set_scope("phase:136", "pause", revision)
        tasks = board(card("7"), card("136", is_phase=True), card("138", phase="136"))
        self.assertEqual(orchestrator.authorize("implement", "138-card.md",
                                                board=tasks)["code"], "paused")
        self.assertTrue(orchestrator.authorize("implement", "7-card.md",
                                               board=tasks)["allowed"])

    def test_hand_back_is_a_boundary_and_touches_no_card(self):
        self.own_execution()
        revision = self.put_in_force(agreement())
        card_file = self.work / "tasks" / "7-card.md"
        card_file.write_text("# 7 — kept\n")
        before = snapshot(self.work)
        revision = agreements.set_scope("bench", "handback", revision, "7")["revision"]
        tasks = board(card("7", "in-progress"))
        self.assertEqual(orchestrator.authorize("implement", "7-card.md",
                                                board=tasks)["code"], "handed-back")
        self.assertEqual(snapshot(self.work), before)
        agreements.set_scope("bench", "reclaim", revision, "7")
        self.assertTrue(orchestrator.authorize("implement", "7-card.md",
                                               board=tasks)["allowed"])

    def test_a_phase_with_no_agreement_keeps_the_legacy_runner(self):
        self.own_execution()
        self.put_in_force(agreement())
        tasks = board(card("49", is_phase=True), card("50", phase="49"))
        self.assertEqual(orchestrator.authorize("implement", "50-card.md",
                                                board=tasks)["code"], "legacy-phase")

    def test_two_levels_only(self):
        resolved = agreements.resolve(self.with_phase(phases={"140": {}}))
        self.assertIn("nesting-refused", [p["code"] for p in resolved["problems"]])


def _tasks(tasks: dict) -> list[dict]:
    return [t for stage in tasks["stages"] for t in stage["tasks"]]


class MergeDelegationIsTheUsersChoice(Sandbox):
    def test_ungated_delegation_validates_and_is_labelled_honestly(self):
        doc = agreement()
        self.assertTrue(agreements.validate(doc)["ok"])
        self.own_execution()
        self.put_in_force(doc)
        answer = orchestrator.authorize("merge", "7-card.md",
                                        board=board(card("7", "review")),
                                        gate_reading={"decision": "ungated"})
        self.assertTrue(answer["allowed"])
        self.assertIn("ungated", answer["label"])
        self.assertIn("not verified", answer["label"])
        self.assertNotIn("passed", answer["label"])

    def test_configured_requirements_still_refuse_an_unmet_merge(self):
        self.own_execution()
        self.put_in_force(agreement())
        tasks = board(card("7", "review"))
        self.assertEqual(orchestrator.authorize(
            "merge", "7-card.md", board=tasks,
            gate_reading={"decision": "blocked"})["code"], "requirements-unmet")
        self.assertEqual(orchestrator.authorize(
            "merge", "7-card.md", board=tasks,
            gate_reading={"decision": "pending"})["code"], "requirements-pending")

    def test_merge_needs_the_delegation_itself(self):
        self.own_execution()
        self.put_in_force(agreement(authority={"merge": False}))
        self.assertEqual(orchestrator.authorize(
            "merge", "7-card.md", board=board(card("7", "review")),
            gate_reading={"decision": "allowed"})["code"], "not-authorized")

    def test_a_board_with_no_gates_reads_ungated_not_passed(self):
        rules = orchestrator.merge_rules()
        for scope in rules["scopes"].values():
            if not scope["gated"]:
                self.assertIn("ungated", scope["label"])
                self.assertNotIn("passed", scope["label"])


HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import config, execowner
config.STATE = Path(sys.argv[2])
print("held" if execowner.acquire_lock() else "refused", flush=True)
sys.stdin.readline()
"""


class OneExecutionOwner(Sandbox):
    def test_two_local_processes_cannot_hold_one_installation(self):
        execowner.designate("none")
        other = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(CORE), str(config.STATE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(other.stdout.readline().strip(), "held")
            self.assertFalse(execowner.acquire_lock())
            self.assertEqual(execowner.authorizes()["code"], "lock-not-held")
            self.assertFalse(execowner.lock_free())
            with self.assertRaises(execowner.Refused) as refused:
                execowner.handover("1", {"inFlight": [], "unreconciled": []})
            self.assertEqual(refused.exception.code, "lock-in-use")
        finally:
            other.communicate("\n", timeout=10)
        self.assertTrue(execowner.acquire_lock(), "the kernel released it on exit")
        self.assertTrue(execowner.authorizes()["allowed"])

    def write_foreign_designation(self, **extra) -> None:
        path = execowner.designation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": execowner.KIND, "v": 1, "state": "designated",
            "installation": "f" * 24, "at": "2020-01-01T00:00:00Z",
            "revision": 4, **extra}))

    def test_a_replica_cannot_execute_or_take_over(self):
        self.write_foreign_designation()
        self.assertTrue(execowner.acquire_lock())     # the lock alone is not enough
        self.assertEqual(execowner.authorizes()["code"], "replica")
        self.assertEqual(execowner.reading()["role"], "replica")
        with self.assertRaises(execowner.Refused) as refused:
            execowner.designate("4")
        self.assertEqual(refused.exception.code, "owned-elsewhere")
        with self.assertRaises(execowner.Refused) as handover:
            execowner.handover("4", {"inFlight": [], "unreconciled": []})
        self.assertEqual(handover.exception.code, "not-owner")

    def test_a_stale_former_owner_heartbeat_authorizes_nothing(self):
        self.write_foreign_designation()
        note = execowner.owner_note_path()
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(json.dumps({"pid": 999999999, "since": "2020-01-01T00:00:00Z",
                                    "installation": "f" * 24}))
        self.assertEqual(execowner.authorizes()["code"], "replica")
        self.assertFalse(execowner.reading()["ownerNote"]["authority"])
        with self.assertRaises(execowner.Refused):
            execowner.designate("4")

    def test_handover_needs_quiescence_then_adoption_needs_reconciliation(self):
        self.own_execution()
        self.put_in_force(agreement())
        with self.assertRaises(execowner.Refused) as busy:
            execowner.handover("1", {"inFlight": ["7-card.md"], "unreconciled": []})
        self.assertEqual(busy.exception.code, "not-quiescent")
        self.assertTrue(execowner.holds_lock(), "a refused handover keeps ownership")
        self.running("7-card.md")
        with self.assertRaises(execowner.Refused):
            orchestrator.handover("1")
        state.AGENTS.clear()
        released = orchestrator.handover("1")["designation"]
        self.assertEqual(released["role"], "released")
        self.assertFalse(execowner.holds_lock())

        # another installation adopts: a new identity on the same work state
        execowner.identity_path().unlink()
        adopted = execowner.designate(released["revision"])["designation"]
        self.assertEqual(adopted["role"], "designated-here")
        self.assertTrue(adopted["adoptionRequired"])
        self.assertTrue(execowner.acquire_lock())
        tasks = board(card("7", "in-progress"), card("8"))
        self.assertEqual(orchestrator.authorize("implement", "7-card.md",
                                                board=tasks)["code"],
                         "adoption-required")
        self.assertTrue(orchestrator.authorize("implement", "8-card.md",
                                               board=tasks)["allowed"])
        execowner.adopt_card(adopted["revision"], "7")
        self.assertTrue(orchestrator.authorize("implement", "7-card.md",
                                               board=tasks)["allowed"])

    def test_an_unreadable_designation_executes_nothing(self):
        path = execowner.designation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        self.assertEqual(execowner.authorizes()["code"], "designation-unreadable")
        with self.assertRaises(execowner.Refused):
            execowner.designate("none")
        self.assertEqual(path.read_text(), "{broken")


class PublicReading(Sandbox):
    def test_effective_and_draft_are_told_apart_without_secrets_or_paths(self):
        self.put_in_force(agreement(note="ok"))
        secret = "ghp_" + "A" * 36
        draft = agreement(paused=True, note=f"token {secret}")
        agreements.save_draft(draft, "none")
        found = orchestrator.reading(board(card("7")))
        self.assertEqual(found["status"], "enabled")
        self.assertTrue(found["draft"]["differs"])
        self.assertNotEqual(found["draft"]["revision"],
                            found["effective"]["revision"])
        self.assertIn({"scope": "bench", "what": "paused", "before": False,
                       "after": True}, found["draft"]["changes"])
        text = json.dumps(found)
        self.assertNotIn(secret, text)
        self.assertNotIn(str(self.dir), text)
        self.assertIn("frozen", found["grants"])

    def test_the_route_serves_the_reading(self):
        import routes
        replies = []

        class Req:
            def reply(self, code, payload):
                replies.append((code, payload))

        with mock.patch.object(orchestrator, "reading",
                               lambda: {"status": "off"}):
            routes.get_orchestration(Req())
        self.assertEqual(replies, [(200, {"orchestration": {"status": "off"}})])
        rows = {(r.method, r.path): r.klass for r in routes.ROUTES
                if r.path.startswith("/api/orchestration")}
        self.assertEqual(rows[("GET", "/api/orchestration")], "read")
        self.assertTrue(all(cls == "policy-mutation" for (method, _), cls
                            in rows.items() if method == "POST"))


if __name__ == "__main__":
    unittest.main()
