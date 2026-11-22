"""Queue whole phases from To Do; the historical phase management retired
(task 188).

Three halves:

- **The queue resolver** (`taskfiles.queue_state`, woven as every card's
  `queue`): stored stage against effective queue, on a real board read
  from disk — inherited queueing for Backlog members of a queued phase,
  every working or settled stage left as it is, malformed and ambiguous
  membership blocked with its reason, dequeue and requeue by moving the
  phase card, membership edits read on the next read.
- **Scheduling it** on the scheduler's in-memory sandbox: one queue move
  and no per-phase setting starts the right member; repeated passes,
  pause, dequeue and a disabled orchestrator; nothing launched twice.
- **The one-time upgrade** (`phasescope.migrate_model`) on fixtures for a
  legacy-only board, an adopted one, a mixed one, paused and disabled
  phase entries, live workers and uncertain operations, a replica, an
  interrupted run and repeated upgrades.

    python3 -m unittest tests.test_phase_queue -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agreements  # noqa: E402
import config  # noqa: E402
import execowner  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import phases  # noqa: E402
import phasescope  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_orchestration_scheduler import Sandbox, agreement  # noqa: E402

EARLY = "2026-11-01 09:00"


def card(title: str, status: str, *, phase_cards: str | None = None,
         depends: str | None = None, log: tuple[str, ...] = ()) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if phase_cards is not None:
        text += "**Type:** Phase\n"
    if depends:
        text += f"**Depends on:** {depends}\n"
    text += "\nA brief long enough to be one.\n"
    if phase_cards is not None:
        text += f"\n## Cards\n\n{phase_cards}\n"
    if log:
        text += "\n## Phase log\n\n" + "\n".join(f"- {EARLY} · {e}" for e in log) + "\n"
    return text


STATUS = {"backlog": "Backlog", "to-do": "To Do", "in-progress": "In Progress",
          "review": "Review", "done": "Done"}


class DiskBoard(unittest.TestCase):
    """A work root on disk, read by the real `taskfiles.collect`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-queue-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tasks = self.tmp / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
        for attr, value in (("TASKS", self.tasks), ("COMMIT_MOVES", False)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def write(self, name: str, stage: str, **kw) -> None:
        title = kw.pop("title", name[:-3].replace("-", " "))
        (self.tasks / stage / name).write_text(card(title, STATUS[stage], **kw),
                                               encoding="utf-8")

    def move(self, name: str, source: str, target: str) -> None:
        taskfiles.move_task(name, source, target, actor="you")

    def queue(self, name: str) -> dict:
        board = taskfiles.collect()
        task = next(t for s in board["stages"] for t in s["tasks"]
                    if t["file"] == name)
        return task["queue"]

    def phase(self, stage: str = "to-do",
              members: str = "- 11 — one\n- 12 — two\n") -> None:
        self.write("9-ship.md", stage, title="9 — Ship", phase_cards=members)


class TheQueueResolver(DiskBoard):
    def test_backlog_members_of_a_queued_phase_inherit_its_queue(self):
        self.phase("to-do")
        self.write("11-one.md", "backlog", title="11 — one")
        self.write("12-two.md", "backlog", title="12 — two")
        q = self.queue("11-one.md")
        self.assertEqual(("queued", "to-do", "backlog", True),
                         (q["state"], q["effective"], q["stage"], q["inherited"]))
        self.assertIn("queued with its phase", q["reason"])
        self.assertEqual("9-ship.md", q["phase"])
        self.assertTrue((self.tasks / "backlog" / "11-one.md").is_file(),
                        "the stored stage is never moved by reading")

    def test_in_progress_phase_queues_too(self):
        self.phase("in-progress")
        self.write("11-one.md", "backlog", title="11 — one")
        self.write("12-two.md", "backlog", title="12 — two")
        self.assertTrue(self.queue("11-one.md")["inherited"])

    def test_working_and_settled_members_are_never_requeued(self):
        self.phase("to-do", "- 11 — one\n- 12 — two\n- 13 — three\n- 14 — four\n")
        self.write("11-one.md", "in-progress", title="11 — one")
        self.write("12-two.md", "review", title="12 — two")
        self.write("13-three.md", "done", title="13 — three")
        self.write("14-four.md", "to-do", title="14 — four")
        for name, state_ in (("11-one.md", "working"), ("12-two.md", "review"),
                             ("13-three.md", "delivered"), ("14-four.md", "queued")):
            q = self.queue(name)
            self.assertEqual(state_, q["state"], name)
            self.assertFalse(q["inherited"], name)
            self.assertEqual(q["stage"], q["effective"], "never rewound")

    def test_a_phase_in_backlog_queues_nothing_and_requeue_is_a_move(self):
        self.phase("backlog")
        self.write("11-one.md", "backlog", title="11 — one")
        self.write("12-two.md", "backlog", title="12 — two")
        self.assertEqual("not-queued", self.queue("11-one.md")["state"])
        self.assertIn("move the phase card into To Do", self.queue("11-one.md")["reason"])
        self.move("9-ship.md", "backlog", "to-do")           # queue
        self.assertTrue(self.queue("11-one.md")["inherited"])
        self.move("9-ship.md", "to-do", "backlog")           # dequeue
        self.assertEqual("not-queued", self.queue("11-one.md")["state"])
        self.move("9-ship.md", "backlog", "to-do")           # requeue
        self.assertTrue(self.queue("12-two.md")["inherited"])

    def test_a_phase_past_in_progress_queues_no_new_member(self):
        self.phase("review")
        self.write("11-one.md", "backlog", title="11 — one")
        self.write("12-two.md", "backlog", title="12 — two")
        q = self.queue("11-one.md")
        self.assertEqual("not-queued", q["state"])
        self.assertIn("queues no new member", q["reason"])

    def test_membership_edits_are_read_on_the_next_read(self):
        self.phase("to-do", "- 11 — one\n")
        self.write("11-one.md", "backlog", title="11 — one")
        self.write("12-two.md", "backlog", title="12 — two")
        self.assertEqual("not-queued", self.queue("12-two.md")["state"],
                         "not a member: Backlog is the owner's")
        self.phase("to-do", "- 11 — one\n- 12 — two\n")      # added
        self.assertTrue(self.queue("12-two.md")["inherited"])
        self.phase("to-do", "- 11 — one\n")                  # removed again
        self.assertEqual("not-queued", self.queue("12-two.md")["state"])

    def test_a_card_two_phases_list_is_blocked_not_queued(self):
        self.phase("to-do", "- 11 — one\n")
        self.write("8-other.md", "to-do", title="8 — Other", phase_cards="- 11 — one\n")
        self.write("11-one.md", "backlog", title="11 — one")
        q = self.queue("11-one.md")
        self.assertEqual("blocked", q["state"])
        self.assertIn("listed by two phases", q["reason"])
        self.assertFalse(q["inherited"])

    def test_a_list_naming_a_missing_card_blocks_its_members(self):
        self.phase("to-do", "- 11 — one\n- 99 — nobody\n")
        self.write("11-one.md", "backlog", title="11 — one")
        q = self.queue("11-one.md")
        self.assertEqual("blocked", q["state"])
        self.assertIn("99", q["reason"])

    def test_a_standalone_backlog_card_is_not_queued(self):
        self.write("5-alone.md", "backlog", title="5 — alone")
        self.assertEqual(("not-queued", False),
                         (self.queue("5-alone.md")["state"],
                          self.queue("5-alone.md")["inherited"]))


class SchedulingAQueuedPhase(Sandbox):
    """The scheduler's own sandbox: one move, no per-phase setting."""

    def setUp(self):
        super().setUp()
        self.branches = {"phase/9-card"}
        for patch in (mock.patch.object(phases, "branch_exists",
                                        lambda b: b in self.branches),
                      mock.patch.object(phasescope, "has_work",
                                        lambda t: f"task/{t['file'][:-3]}"
                                        in self.branches)):
            patch.start()
            self.addCleanup(patch.stop)

    def phase(self, phase_stage="in-progress", stages=("backlog", "backlog"),
              depends=None):
        self.board.add("9", stage=phase_stage, is_phase=True)
        self.board.cards["9-card.md"]["title"] = "9 — Ship"
        for i, (number, stage) in enumerate(zip(("11", "12"), stages), 1):
            self.board.add(number, stage=stage, phase="9", index=i,
                           depends=(depends or {}).get(number, ()))
            self.board.cards[f"{number}-card.md"]["phase"]["file"] = "9-card.md"

    def item(self, name):
        return next(i for i in scheduler.plan()["items"] if i["card"] == name)

    def test_no_agreement_entry_and_no_adoption_is_needed(self):
        self.enable()                          # the Bench scope only
        self.phase()
        self.assertEqual({}, agreements.effective_document()[0]["phases"],
                         "no per-phase setting exists")
        self.tick()
        self.assertEqual([("11-card.md", "in-progress")], self.launch.calls)
        self.assertEqual("backlog", self.board.cards["12-card.md"]["stage"])
        self.assertEqual("waiting-member", self.item("12-card.md")["code"])

    def test_dependencies_and_order_hold_a_queued_member(self):
        self.enable()
        self.phase(depends={"11": ("50",)})
        self.board.add("50", stage="to-do")
        self.board.cards["50-card.md"]["stage"] = "in-progress"
        self.board.cards["50-card.md"]["file"] = "50-card.md"
        found = self.item("11-card.md")
        self.assertEqual(("blocked", "waiting-dependency"),
                         (found["state"], found["code"]))
        self.assertIn("card 50", found["reason"])

    def test_repeated_passes_launch_once(self):
        self.enable()
        self.phase()
        for _ in range(4):
            self.tick()
        self.assertEqual(1, len(self.launch.calls))
        ops = [o for o in operations.all_ops() if o["file"] == "11-card.md"]
        self.assertEqual(1, len(ops), "one operation for one decision")

    def test_a_paused_or_off_orchestrator_starts_nothing_and_says_so(self):
        revision = self.put_in_force(agreement())
        self.own_execution()
        self.phase()
        agreements.set_scope("bench", "pause", revision)
        self.tick()
        self.assertEqual([], self.launch.calls)
        self.assertEqual("paused", self.item("11-card.md")["code"])
        life = phasescope.board_lifecycle(self.board.collect())["9-card.md"]
        self.assertEqual("paused", life["word"])

    def test_a_dequeued_phase_starts_nothing(self):
        self.enable()
        self.phase(phase_stage="backlog")
        self.tick()
        self.assertEqual([], self.launch.calls)
        self.assertNotIn("11-card.md", [i["card"] for i in scheduler.plan()["items"]])

    def test_a_queued_member_is_authorized_only_while_inherited(self):
        self.enable()
        self.phase()
        self.assertTrue(orchestrator.authorize("implement", "11-card.md")["allowed"])
        self.board.cards["9-card.md"]["stage"] = "backlog"      # dequeued
        answer = orchestrator.authorize("implement", "11-card.md")
        self.assertEqual("stage-not-managed", answer["code"])

    def test_a_refused_launch_walks_the_card_back_to_backlog(self):
        self.enable()
        self.phase()
        def refuse(*_a, **_k):
            raise ValueError("preflight said no")
        self.launch = refuse
        scheduler.run_pass(start=refuse)
        self.assertEqual("backlog", self.board.cards["11-card.md"]["stage"],
                         "a launch that ran nothing leaves it where it was stored")


# ── the one-time upgrade ──────────────────────────────────────────────

class UpgradeFixtures(Sandbox):
    """Upgrade fixtures on the sandbox: phase logs as older boards left
    them, the real migration record, the real ledger."""

    def setUp(self):
        super().setUp()
        # an upgraded board, not a fresh one: no record yet
        agreements.phase_model_path().unlink()
        self.logs: list[tuple[str, str]] = []
        real_write = phases.write_log

        def write_log(phase, entry):
            name = phase["file"]
            self.logs.append((name, entry))
            stamp = f"- {EARLY} · {entry}\n"
            body = self.board.cards[name]["body"]
            if "## Phase log" not in body:
                body += "\n## Phase log\n\n"
            self.board.cards[name]["body"] = body + stamp
            return True
        patch = mock.patch.object(phases, "write_log", write_log)
        patch.start()
        self.addCleanup(patch.stop)
        self.assertIsNotNone(real_write)

    def legacy(self, number: str, stage: str, *log: str, members=("11",)):
        self.board.add(number, stage=stage, is_phase=True)
        name = f"{number}-card.md"
        if log:
            self.board.cards[name]["body"] += "\n## Phase log\n\n" + "".join(
                f"- {EARLY} · {e}\n" for e in log)
        for i, m in enumerate(members, 1):
            self.board.add(m, stage="in-progress", phase=number, index=i)
            self.board.cards[f"{m}-card.md"]["phase"]["file"] = name
        return name

    def owned(self, name: str) -> int:
        return sum(1 for n, e in self.logs if n == name and e == phasescope.OWNED_LINE)

    def test_legacy_only_board_moves_over_with_recovery_data(self):
        self.enable()
        name = self.legacy("9", "in-progress", "run started on phase/9-card")
        record = phasescope.migrate_model()
        self.assertEqual(("complete", {}), (record["status"], record["holds"]))
        self.assertEqual(["9"], record["transferred"])
        self.assertEqual(1, self.owned(name))
        backup = phasescope.backup_path()
        self.assertTrue(backup.is_file())
        copy = backup.read_text(encoding="utf-8")
        self.assertIn('"runner": "running"', copy)
        self.assertIn("agreementRevision", copy)

    def test_repeated_and_interrupted_upgrades_change_nothing_more(self):
        self.enable()
        name = self.legacy("9", "in-progress", "run started on phase/9-card")
        first = phasescope.backup_path()
        phasescope.migrate_model()
        before = first.read_text(encoding="utf-8")
        # a crash after the line but before the record: run it again
        agreements.phase_model_path().unlink()
        phasescope.migrate_model()
        phasescope.migrate_model()
        self.assertEqual(1, self.owned(name), "one ownership line, ever")
        self.assertEqual(before, first.read_text(encoding="utf-8"),
                         "the first recovery copy is never overwritten")
        self.assertEqual("complete", agreements.phase_model()["status"])

    def test_an_adopted_phase_is_left_as_it_was(self):
        self.enable()
        name = self.legacy("9", "in-progress", "scope adopted by ada — the "
                                               "phase:9 scope schedules this phase")
        record = phasescope.migrate_model()
        self.assertEqual(0, self.owned(name))
        self.assertEqual("complete", record["status"])

    def test_a_mixed_board_holds_only_what_must_wait(self):
        self.enable()
        adopted = self.legacy("8", "in-progress", "scope adopted by ada — x",
                              members=("21",))
        halted = self.legacy("9", "in-progress", "run started on phase/9-card",
                             "halted at 11 — its CI is red", members=("11",))
        busy = self.legacy("7", "in-progress", "run started on phase/7-card",
                           members=("31",))
        state.AGENTS["live"] = {"task": "31-card.md", "mode": "work",
                                "status": "running", "started": time.time()}
        fresh = self.legacy("6", "to-do", members=("41",))
        record = phasescope.migrate_model()
        self.assertEqual("partial", record["status"])
        self.assertEqual({"9": "held", "7": "busy"},
                         {k: v["kind"] for k, v in record["holds"].items()})
        self.assertEqual((0, 0, 0, 1), (self.owned(adopted), self.owned(halted),
                                        self.owned(busy), self.owned(fresh)))
        self.assertEqual("running", state.AGENTS["live"]["status"],
                         "a live worker is never cancelled")
        # the held phases are scheduled by nobody; the others are
        self.assertEqual("phase-migrating", self.item("11-card.md")["code"])
        self.assertEqual("phase-migrating", self.item("31-card.md")["code"])
        # the live worker ends: the next pass finishes that transfer
        state.AGENTS["live"]["status"] = "finished"
        self.tick()
        record = agreements.phase_model()
        self.assertEqual({"9": "held"},
                         {k: v["kind"] for k, v in record["holds"].items()})
        self.assertEqual("complete", record["status"],
                         "a person's hold does not keep it partial")
        self.assertEqual(1, self.owned(busy))

    def test_an_uncertain_operation_defers_the_transfer(self):
        self.enable()
        name = self.legacy("9", "in-progress", "run started on phase/9-card")
        operations.begin(operations.key_for("implement", "11-card.md", "b", 1),
                         action="implement", card="11-card.md", number="11",
                         scope="phase:9", pool="implementation", basis="b",
                         attempt=1)
        record = phasescope.migrate_model()
        self.assertEqual("busy", record["holds"]["9"]["kind"])
        self.assertIn("operation", record["holds"]["9"]["reason"])
        self.assertEqual(0, self.owned(name))

    def test_paused_and_disabled_phase_entries_stay_as_they_were(self):
        doc = agreement()
        doc["phases"] = {"9": {"enabled": True, "paused": True},
                         "8": {"enabled": False}}
        before = None
        self.put_in_force(doc)
        self.own_execution()
        before = agreements.effective_document()[0]
        self.legacy("9", "in-progress", members=("11",))
        self.legacy("8", "in-progress", members=("21",))
        record = phasescope.migrate_model()
        self.assertEqual(before, agreements.effective_document()[0],
                         "the agreement is not rewritten")
        self.assertIn("#8", " ".join(record["summary"]))
        self.assertEqual("paused", self.item("11-card.md")["code"])
        self.assertEqual("disabled", self.item("21-card.md")["code"])

    def test_a_replica_writes_nothing_to_the_shared_cards(self):
        self.put_in_force(agreement())
        execowner.designate("none")             # designated here …
        doc = execowner.read_designation()[0]
        # … then read as another installation's
        with mock.patch.object(execowner, "installation_id",
                               lambda: "someone-else"):
            name = self.legacy("9", "in-progress", "run started on phase/9-card")
            record = phasescope.migrate_model()
        self.assertEqual("not-this-installation", record["status"])
        self.assertEqual(0, self.owned(name))
        self.assertIsNone(agreements.phase_model())
        self.assertIsNotNone(doc)

    def test_a_reserved_agreement_says_what_an_unnamed_phase_lacks(self):
        doc = agreement()
        doc["bench"]["capacityModel"] = agreements.RESERVED
        doc["bench"].pop("scopeDefaults", None)
        self.put_in_force(doc)
        self.own_execution()
        self.legacy("9", "in-progress")
        record = phasescope.migrate_model()
        self.assertIn("shared capacity", " ".join(record["summary"]))

    def test_resuming_a_held_phase_is_a_persons_and_writes_once(self):
        self.enable()
        name = self.legacy("9", "in-progress", "run started on phase/9-card",
                           "stopped — held by ada")
        record = phasescope.migrate_model()
        self.assertEqual("held", record["holds"]["9"]["kind"])
        with mock.patch.object(phasescope, "_find", lambda f: (
                next(t for s in self.board.collect()["stages"] for t in s["tasks"]
                     if t["file"] == f),
                [t for s in self.board.collect()["stages"] for t in s["tasks"]])):
            phasescope.resume(name, "ada")
            with self.assertRaises(phasescope.Refused) as again:
                phasescope.resume(name, "ada")
        self.assertEqual("not-held", again.exception.code)
        self.assertEqual({}, agreements.phase_holds())
        self.assertEqual(1, self.owned(name))
        self.tick()
        self.assertIn(("11-card.md", "in-progress"), self.launch.calls)

    def test_an_unreadable_record_holds_every_phase(self):
        """PR #190 review: a damaged or other-version migration record
        fails closed. A phase the retired runner had halted must not be
        scheduled — or have its ownership line written, which would lose
        the hold for good — just because the record cannot be read."""
        self.enable()
        halted = self.legacy("9", "in-progress", "run started on phase/9-card",
                             "halted at 11 — its CI is red", members=("11",))
        fresh = self.legacy("6", "to-do", members=("41",))
        self.assertEqual("held", phasescope.migrate_model()["holds"]["9"]["kind"])
        written = self.owned(fresh)
        path = agreements.phase_model_path()
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual({}, agreements.phase_holds())
        for number in ("9", "6"):
            self.assertEqual("unreadable",
                             agreements.phase_hold(number)["kind"])
        self.assertIsNone(agreements.phase_hold(None))
        # every phase, held or not before, is scheduled by nobody
        self.assertEqual("phase-migrating", self.item("11-card.md")["code"])
        self.assertEqual("phase-migrating", self.item("41-card.md")["code"])
        refused = orchestrator.authorize("implement", "11-card.md")
        self.assertFalse(refused["allowed"])
        self.assertIn("cannot be read", refused["reason"])
        # the migration never writes over it, and nothing launches
        self.assertEqual("unreadable", phasescope.migrate_model()["status"])
        self.tick()
        self.assertEqual([], [c for c in self.launch.calls
                              if c[0] in ("11-card.md", "41-card.md")])
        self.assertEqual("{ not json", path.read_text(encoding="utf-8"))
        self.assertEqual((0, written), (self.owned(halted), self.owned(fresh)))
        # nor can a resume write a fresh record over it
        with mock.patch.object(phasescope, "_find", lambda f: (
                next(t for s in self.board.collect()["stages"] for t in s["tasks"]
                     if t["file"] == f),
                [t for s in self.board.collect()["stages"] for t in s["tasks"]])):
            with self.assertRaises(phasescope.Refused) as resumed:
                phasescope.resume(halted, "ada")
        self.assertEqual("model-unreadable", resumed.exception.code)
        self.assertEqual("{ not json", path.read_text(encoding="utf-8"))
        # another version is unreadable too, never complete
        path.write_text('{"kind": "bench-phase-model", "v": 2, '
                        '"status": "complete", "holds": {}}', encoding="utf-8")
        self.assertEqual("phase-migrating", self.item("11-card.md")["code"])

    def test_a_busy_hold_clears_once_its_card_leaves_the_active_stages(self):
        """PR #190 review: a `busy` hold on a phase card walked back out of
        To Do, In Progress and Review used to stay for ever, keeping the
        record partial and every pass migrating again."""
        self.enable()
        busy = self.legacy("7", "in-progress", "run started on phase/7-card",
                           members=("31",))
        state.AGENTS["live"] = {"task": "31-card.md", "mode": "work",
                                "status": "running", "started": time.time()}
        self.assertEqual("partial", phasescope.migrate_model()["status"])
        self.board.cards[busy]["stage"] = "backlog"
        # still working: the hold stays
        record = phasescope.migrate_model()
        self.assertEqual(("partial", "busy"),
                         (record["status"], record["holds"]["7"]["kind"]))
        state.AGENTS["live"]["status"] = "finished"
        record = phasescope.migrate_model()
        self.assertEqual(("complete", {}), (record["status"], record["holds"]))
        self.assertIn("hold cleared", " ".join(record["summary"]))
        self.assertEqual(0, self.owned(busy), "nothing is transferred for it")

    def item(self, name):
        return next(i for i in scheduler.plan()["items"] if i["card"] == name)


class TheRetiredRoutes(unittest.TestCase):
    def test_the_old_routes_answer_gone_with_the_way_forward(self):
        import routes
        replies = []

        class Req:
            body = {"file": "9-ship.md", "stage": "in-progress"}

            def reply(self, status, payload):
                replies.append((status, payload))

        routes.post_phase_run(Req())
        routes.post_phase_stop(Req())
        self.assertEqual([410, 410], [s for s, _ in replies])
        self.assertTrue(all(p["code"] == "phase-runner-retired" for _, p in replies))
        self.assertIn("To Do", replies[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
