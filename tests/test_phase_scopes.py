"""A phase run by its scoped sub-orchestrator (task 143, D6).

Two halves. The first drives a disposable two-member phase through its
phase scope end to end — a real git repository, real worktrees, real
headless launches through a stub adapter, the real scheduler pass — and
the legacy phase runner's beat alongside it, to show one scheduling owner
per member, member integration kept apart from delivery, and the final
integrated head merged only under its own rule. It also holds the legacy
half: adoption refused while the runner works the phase, logs, branches
and integrated commits preserved across adoption, release and a restart.

The second half runs the engine on a board in memory (the scheduler's own
sandbox): authority and capacity only narrow, a strategy decides which
member may start, foreign or held members are visibly not this scope's,
and a parent pause, a child pause and cancelling are three different acts.

    python3 -m unittest tests.test_phase_scopes -v
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agreements  # noqa: E402
import config  # noqa: E402
import coordinate  # noqa: E402
import execowner  # noqa: E402
import movements  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import phases  # noqa: E402
import phasescope  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_orchestration_scheduler import Sandbox, agreement  # noqa: E402
from tests.test_phase_runs import (ONE, PHASE, PHASE_BRANCH, TWO,  # noqa: E402
                                   PhaseCase, card, git)

EARLY = "2026-11-01 09:00"


def phase_agreement(**phase) -> dict:
    """Everything delegated at Bench scope; phase 40 named, enabled, with
    one implementation slot of its own. Review is narrowed away here: the
    integration rule asks for none, and a review needs a PR and a second
    model — `coordinate`'s own tests hold that half."""
    doc = agreements.new_draft()
    doc["bench"].update(enabled=True,
                        authority={w: True for w in agreements.AUTHORITIES})
    doc["phases"] = {"40": {"enabled": True,
                            "authority": {"review": False},
                            "allocation": {"implementation": 1,
                                           "dailyStarts": 10},
                            **phase}}
    # the reserved model that predates task 182, kept until migrated
    doc["bench"]["capacityModel"] = agreements.RESERVED
    doc["bench"].pop("scopeDefaults", None)
    return doc


class ScopedPhaseCase(PhaseCase):
    """PhaseCase with the orchestration state kept under the case's own
    directory, the members in To Do, and this board the execution owner."""

    PHASE_STAGE = "in-progress"

    def setUp(self):
        super().setUp()
        state_dir = self.tmp / "state"
        self.patch(STATE=state_dir, GATES_DIR=state_dir / "gates",
                   EVIDENCE_DIR=state_dir / "evidence",
                   VERIFY_DIR=state_dir / "verify",
                   INPUTS_DIR=state_dir / "inputs",
                   ARTIFACTS_DIR=state_dir / "artifacts",
                   RESULTS_DIR=state_dir / "results",
                   REASONING_DIR=state_dir / "reasoning",
                   REFINE_DIR=state_dir / "refine")
        eligible = mock.patch.object(
            movements, "eligibility", lambda task, tasks=None, snap=None: {
                "eligible": True, "code": "own-move", "via": "own",
                "reason": "yours"})
        eligible.start()
        self.addCleanup(eligible.stop)
        self.addCleanup(execowner.release_lock)

    def write_cards(self, **_) -> None:
        self.write(ONE, card("31 — Stand up site/", status="To Do"), "to-do")
        self.write(TWO, card("32 — Serve it", status="To Do"), "to-do")
        status = config.STAGE_LABELS[self.PHASE_STAGE]
        self.write(PHASE, card("40 — Ship the site", status=status,
                               kind="Phase",
                               cards="- 31 — Stand up site/\n- 32 — Serve it\n"),
                   self.PHASE_STAGE)

    def orchestrate(self, doc: dict | None = None) -> None:
        doc = doc or phase_agreement()
        agreements.save_draft(doc, "none")
        agreements.apply(agreements.effective()["revision"],
                         agreements.revision(doc))
        execowner.designate("none")
        self.assertTrue(execowner.acquire_lock())

    def tick(self) -> dict:
        found = scheduler.run_pass()
        self.settle()
        return found

    def ops(self, action: str | None = None) -> list[dict]:
        return [o for o in operations.all_ops()
                if action is None or o.get("action") == action]

    def on_main(self, ref: str) -> bool:
        return git(self.repo, "merge-base", "--is-ancestor", ref,
                   "main").returncode == 0


# ── 1. a disposable phase, run through its scope ──────────────────────

class APhaseRunThroughItsScope(ScopedPhaseCase):
    PHASE_STAGE = "to-do"

    def test_one_owner_per_member_integration_apart_from_delivery(self):
        self.orchestrate()
        # task 188: queued by being in To Do; nobody adopts it
        tasks = [t for s in taskfiles.collect()["stages"] for t in s["tasks"]]
        member = next(t for t in tasks if t["file"] == ONE)
        self.assertEqual(("phase:40", None),
                         orchestrator.scope_of(member, agreements.effective()))

        seen_integrated_not_delivered = False
        for _ in range(12):
            self.tick()
            if self.branch_exists(f"task/{ONE[:-3]}") and \
                    git(self.repo, "merge-base", "--is-ancestor",
                        f"task/{ONE[:-3]}", PHASE_BRANCH).returncode == 0 \
                    and self.stage_of(PHASE) != "done":
                # integrated is not delivered: in the phase branch, still in
                # review/, and not in the application base
                self.assertEqual("review", self.stage_of(ONE))
                self.assertFalse(self.on_main(f"task/{ONE[:-3]}"))
                seen_integrated_not_delivered = True
            if self.stage_of(PHASE) == "done":
                break
        self.assertTrue(seen_integrated_not_delivered)
        self.assertEqual("done", self.stage_of(PHASE),
                         f"the phase never finished: {self.log()}")

        # exactly one execution owner per member: one launch each, by the
        # scope, and nothing the runner started
        launches = self.ops("implement")
        self.assertEqual(sorted([ONE, TWO]), sorted(o["file"] for o in launches))
        self.assertTrue(all(o["scope"] == "phase:40" for o in launches))
        self.assertEqual(2, len([r for r in state.AGENTS.values()
                                 if r.get("mode") == "work"]))
        log = self.log()
        self.assertFalse([e for e in log if e.startswith("run started")],
                         "no phase runner ran this phase")
        self.assertFalse([e for e in log if phases.STARTED_RE.match(e)])
        self.assertEqual(1, len([e for e in log if e == phasescope.OWNED_LINE]),
                         "the ownership line, for older boards, written once")
        # integration and delivery are separate records
        integrated = self.ops("integrate")
        self.assertEqual(sorted([ONE, TWO]), sorted(o["file"] for o in integrated))
        self.assertTrue(all(o["outcome"] == "integrated" for o in integrated))
        [merge] = self.ops("merge")
        self.assertEqual(PHASE, merge["file"],
                         "only the phase card is merged into the base")
        self.assertIn("ungated", merge["observed"],
                      "an ungated final merge says it is not verified")
        # and the base now holds the final integrated head, members swept
        for name in ("31.txt", "32.txt"):
            self.assertEqual(0, git(self.repo, "cat-file", "-e",
                                    f"main:{name}").returncode,
                             f"the application base holds {name}")
        self.assertEqual(("done", "done"), (self.stage_of(ONE), self.stage_of(TWO)))

    def test_the_final_head_waits_for_its_own_merge_delegation(self):
        self.orchestrate(phase_agreement(authority={"review": False,
                                                    "merge": False}))
        for _ in range(10):
            self.tick()
            if self.stage_of(PHASE) == "review":
                break
        self.assertEqual("review", self.stage_of(PHASE))
        self.tick()
        found = coordinate.plan()
        item = next(i for i in found["items"] if i["card"] == PHASE)
        self.assertEqual(("merge-not-delegated", "integration"),
                         (item["code"], item["gate"]["scope"]))
        # the final rule reads the integrated head — no member's commit
        self.assertEqual(self.tip(PHASE_BRANCH), item["head"])
        self.assertNotIn(item["head"], (self.tip(f"task/{ONE[:-3]}"),
                                        self.tip(f"task/{TWO[:-3]}")))
        members = [i for i in found["items"] if i["card"] in (ONE, TWO)]
        self.assertEqual({"integrated"}, {i["code"] for i in members},
                         "member passes are not the phase's result")
        self.assertEqual([], self.ops("merge"))
        self.assertFalse(self.on_main(PHASE_BRANCH))
        self.assertEqual(("review", "review"),
                         (self.stage_of(ONE), self.stage_of(TWO)))


class IntegrationIsBoundToTheCheckedHead(ScopedPhaseCase):
    """PR 147 review, item 3: under the user's ungated member rule, a
    member's integration still merges exactly the head the scope checked —
    a commit that lands on the member's branch afterwards is not carried in
    under a receipt naming the earlier head."""
    PHASE_STAGE = "to-do"

    def member_in_review(self) -> dict:
        self.orchestrate()
        for _ in range(10):
            self.tick()
            if self.stage_of(ONE) == "review":
                break
        self.assertEqual("review", self.stage_of(ONE))
        [item] = [i for i in coordinate.plan()["items"] if i["card"] == ONE]
        self.assertEqual("integrate", item["code"], item.get("reason"))
        self.assertEqual("ungated", item["gate"]["decision"],
                         "the user's member rule here is ungated")
        return item

    def holds(self, commit: str, ref: str) -> bool:
        return git(self.repo, "merge-base", "--is-ancestor", commit,
                   ref).returncode == 0

    def push_onto_member(self) -> str:
        """Someone commits to the member's branch after it was checked."""
        branch = f"task/{ONE[:-3]}"
        listing = git(self.repo, "worktree", "list", "--porcelain").stdout
        path = next(block.split("\n", 1)[0][len("worktree "):]
                    for block in listing.split("\n\n")
                    if f"branch refs/heads/{branch}" in block)
        Path(path, "late.txt").write_text("not checked, not reviewed\n")
        git(Path(path), "add", "late.txt")
        result = git(Path(path), "-c", "user.name=elena", "-c",
                     "user.email=e@example.com", "commit", "-q", "-m", "late")
        self.assertEqual(0, result.returncode, result.stderr)
        return self.tip(branch)

    def test_a_commit_after_the_check_is_never_integrated_under_its_receipt(self):
        item = self.member_in_review()
        checked = item["head"]
        late = self.push_onto_member()
        self.assertNotEqual(checked, late)
        task = next(t for s in taskfiles.collect()["stages"] for t in s["tasks"]
                    if t["file"] == ONE)
        result = coordinate.execute(item, task)
        phase_tip = self.tip(PHASE_BRANCH)
        self.assertFalse(self.holds(late, phase_tip),
                         "an unchecked commit was integrated into the phase")
        self.assertEqual("refused", result["outcome"], result)
        self.assertFalse(self.holds(checked, phase_tip),
                         "nothing is merged when the branch moved")
        [op] = self.ops("integrate")
        self.assertEqual("refused", op["status"])
        self.assertIn(late[:12], op["reason"])

    def test_the_new_head_is_integrated_and_its_receipt_names_it(self):
        self.member_in_review()
        late = self.push_onto_member()
        for _ in range(4):
            self.tick()
            if self.holds(late, self.tip(PHASE_BRANCH)):
                break
        [op] = [o for o in self.ops("integrate") if o.get("outcome") == "integrated"]
        self.assertEqual(late, op["integrated"])
        self.assertEqual(late, op["basis"])
        self.assertIn(late[:12], op["observed"])
        self.assertTrue(self.holds(late, self.tip(PHASE_BRANCH)))


# ── 2. upgrading a board the retired phase runner worked (task 188) ───

class UpgradingFromThePhaseRunner(ScopedPhaseCase):
    """Real git, real cards: phase logs as the retired runner left them."""

    def legacy_log(self, *entries: str) -> None:
        """Append phase-log lines exactly as the old runner wrote them."""
        path = self.tasks / self.stage_of(PHASE) / PHASE
        text = path.read_text(encoding="utf-8").rstrip("\n")
        lines = "\n".join(f"- {EARLY} · {e}" for e in entries)
        path.write_text(f"{text}\n\n## Phase log\n\n{lines}\n", encoding="utf-8")

    def test_a_half_run_phase_carries_on_and_never_reruns_a_member(self):
        # the runner had cut the branch, merged 31 and was running when the
        # upgrade arrived; nothing is working it now
        phases.ensure_branch({"file": PHASE, "stage": "in-progress",
                              "title": "40 — Ship the site", "number": "40"})
        self.legacy_log(f"run started on {PHASE_BRANCH}", "31 started",
                        f"31 merged into {PHASE_BRANCH}")
        before_tip = self.tip(PHASE_BRANCH)
        before_log = self.log()
        self.orchestrate()
        record = phasescope.migrate_model()
        self.assertEqual("complete", record["status"])
        self.assertEqual({}, record["holds"])
        self.assertIn("40", record["transferred"])
        self.assertTrue(phasescope.backup_path().is_file(),
                        "pre-migration recovery data is kept")
        self.assertEqual(before_log, self.log()[:-1], "the log is kept whole")
        self.assertEqual(phasescope.OWNED_LINE, self.log()[-1])
        self.assertEqual(before_tip, self.tip(PHASE_BRANCH))
        # repeated upgrades and restarts change nothing more
        phasescope.migrate_model()
        self.assertEqual(1, self.log().count(phasescope.OWNED_LINE))
        for _ in range(8):
            self.tick()
            if self.stage_of(PHASE) == "done":
                break
        self.assertEqual([], [o for o in self.ops("implement") if o["file"] == ONE],
                         "a member the runner integrated is not run again")
        self.assertEqual("done", self.stage_of(PHASE))

    def test_a_halted_phase_is_held_until_a_person_resumes_it(self):
        phases.ensure_branch({"file": PHASE, "stage": "in-progress",
                              "title": "40 — Ship the site", "number": "40"})
        self.legacy_log(f"run started on {PHASE_BRANCH}", "31 started",
                        "halted at 31 — it is not ready")
        self.orchestrate()
        record = phasescope.migrate_model()
        self.assertEqual("held", record["holds"]["40"]["kind"])
        self.assertIn("halted", record["holds"]["40"]["reason"])
        self.tick()
        self.assertEqual([], self.ops("implement"),
                         "a halt is not lifted by an upgrade")
        tasks = [t for s in taskfiles.collect()["stages"] for t in s["tasks"]]
        member = next(t for t in tasks if t["file"] == ONE)
        self.assertEqual((None, "phase-migrating"),
                         orchestrator.scope_of(member, agreements.effective()))
        life = phasescope.board_lifecycle(taskfiles.collect())[PHASE]
        self.assertEqual("migrating", life["word"])
        self.assertEqual("you", life["next"]["who"])
        phasescope.resume(PHASE, "tester")
        self.assertEqual({}, phasescope.migrate_model()["holds"])
        self.tick()
        self.assertEqual([ONE], [o["file"] for o in self.ops("implement")])

    def test_work_the_runner_started_defers_only_that_phase(self):
        phases.ensure_branch({"file": PHASE, "stage": "in-progress",
                              "title": "40 — Ship the site", "number": "40"})
        self.legacy_log(f"run started on {PHASE_BRANCH}", "31 started")
        state.AGENTS["legacy-31"] = {"task": ONE, "mode": "work",
                                     "status": "running", "started": time.time()}
        self.orchestrate()
        record = phasescope.migrate_model()
        self.assertEqual("partial", record["status"])
        self.assertEqual("busy", record["holds"]["40"]["kind"])
        self.assertNotIn(phasescope.OWNED_LINE, self.log(),
                         "nothing is written for a transfer not yet made")
        self.assertEqual("running", state.AGENTS["legacy-31"]["status"],
                         "the live worker is kept, never cancelled")
        with self.assertRaises(phasescope.Refused) as refused:
            phasescope.resume(PHASE, "tester")
        self.assertEqual("hold-not-yours", refused.exception.code)
        # the run ends: the next pass finishes the transfer by itself
        state.AGENTS["legacy-31"]["status"] = "finished"
        record = phasescope.migrate_model()
        self.assertEqual(("complete", {}), (record["status"], record["holds"]))
        self.assertIn(phasescope.OWNED_LINE, self.log())

    def test_the_retired_entry_points_refuse_with_the_way_forward(self):
        self.orchestrate()
        for call in (phasescope.adopt, phasescope.release):
            with self.assertRaises(phasescope.Refused) as refused:
                call(PHASE, "tester")
            self.assertEqual(("adoption-retired", 410),
                             (refused.exception.code, refused.exception.status))
        for call in (phases.start_phase, phases.stop_phase):
            with self.assertRaises(phases.Retired) as retired:
                call(PHASE, "in-progress")
            self.assertIn("To Do", str(retired.exception))
        self.assertFalse(self.branch_exists(PHASE_BRANCH), "nothing was cut")
        self.assertFalse(hasattr(phases, "beat"), "no runner beat to start")
        self.assertFalse(hasattr(phases, "advance_all"))

    def test_an_older_board_cannot_hand_the_phase_back(self):
        # an old bench sharing the work state writes a release and a run
        self.orchestrate()
        self.tick()
        self.assertIn(phasescope.OWNED_LINE, self.log())
        self.assertTrue(phases.write_log(
            {"file": PHASE, "stage": self.stage_of(PHASE)},
            "scope released by elena — the phase runner may run it again"))
        tasks = [t for s in taskfiles.collect()["stages"] for t in s["tasks"]]
        phase = next(t for t in tasks if t["file"] == PHASE)
        self.assertEqual("scoped", phasescope.mode(phase),
                         "a release line cannot resurrect a second scheduler")
        self.assertIn("older bench", phase["phaseCompat"])
        life = phasescope.board_lifecycle(taskfiles.collect())[PHASE]
        self.assertIn("older bench", life["compat"])

    def test_a_restart_settles_an_integration_by_containment(self):
        self.orchestrate()
        self.tick()                                   # branch cut, 31 runs
        self.assertEqual("review", self.stage_of(ONE))
        head = self.tip(f"task/{ONE[:-3]}")
        op = operations.begin(operations.key_for("integrate", ONE, head),
                              action="integrate", card=ONE, number="31",
                              scope="phase:40", pool="none", basis=head,
                              attempt=1)
        # the board died before merging: nothing is in the phase branch
        [done] = phasescope.reconcile()
        self.assertEqual(("abandoned", "not-integrated"),
                         (done["status"], done["code"]))
        # and one that died after merging is read as integrated, never
        # merged again
        worktree = config.WORKTREES / PHASE[:-3]
        git(worktree, "merge", "--no-ff", "--no-edit", head)
        second = operations.begin(operations.key_for("integrate", ONE, head, 2),
                                  action="integrate", card=ONE, number="31",
                                  scope="phase:40", pool="none", basis=head,
                                  attempt=2)
        [settled] = phasescope.reconcile()
        self.assertEqual(("ended", "integrated"),
                         (settled["status"], settled["outcome"]))
        self.assertNotEqual(op["key"], second["key"])


# ── 3. the engine, on a board in memory ───────────────────────────────

class ScopedBoard(Sandbox):
    def phase_doc(self, **phase) -> dict:
        doc = agreement(capacity={"implementation": 3})
        doc["phases"] = {"9": {"enabled": True,
                               "allocation": {"implementation": 2,
                                              "dailyStarts": 10}, **phase}}
        # the reserved model that predates task 182, kept until migrated
        doc["bench"]["capacityModel"] = agreements.RESERVED
        doc["bench"].pop("scopeDefaults", None)
        return doc

    def members(self, *numbers, depends=None) -> None:
        self.board.add("9", stage="in-progress", is_phase=True)
        for index, number in enumerate(numbers, start=1):
            self.board.add(number, phase="9", index=index,
                           depends=(depends or {}).get(number, ()))

    def item(self, name: str) -> dict:
        return next(i for i in scheduler.plan()["items"] if i["card"] == name)


class StrategyDecidesWhichMemberStarts(ScopedBoard):
    def test_sequential_starts_one_member_in_list_order(self):
        self.put_in_force(self.phase_doc())
        self.own_execution()
        self.members("11", "12")
        self.tick()
        self.assertEqual(["11-card.md"], self.running())
        self.assertEqual("waiting-member", self.item("12-card.md")["code"])
        # reviewed is not integrated: the next member still waits
        self.end_runs()
        self.board.cards["11-card.md"]["stage"] = "review"
        self.tick()
        self.assertEqual("waiting-member", self.item("12-card.md")["code"])
        # the phase log's integration record releases it
        self.board.cards["9-card.md"]["body"] += (
            f"\n## Phase log\n\n- {EARLY} · 11 merged into phase/9-card\n")
        self.tick()
        self.assertIn("12-card.md", self.running())

    def test_dependencies_run_independent_members_side_by_side(self):
        self.put_in_force(self.phase_doc(strategy="dependencies"))
        self.own_execution()
        self.members("11", "12", "13", depends={"13": ("11",)})
        self.assertEqual("waiting-integration", self.item("13-card.md")["code"])
        self.tick()
        self.assertEqual(["11-card.md", "12-card.md"], self.running(),
                         "two of the phase's own two slots")

    def test_an_unknown_strategy_is_refused(self):
        doc = self.phase_doc(strategy="parallel-everything")
        codes = [p["code"] for p in agreements.validate(doc)["problems"]]
        self.assertEqual(["strategy-unknown"], codes)

    def test_an_unnamed_phase_is_its_scopes_without_adoption(self):
        # task 188: no agreement entry, no adoption, no phase runner
        self.put_in_force(agreement(capacity={"implementation": 3}))
        self.own_execution()
        self.members("11")
        self.board.cards["9-card.md"]["phaseMode"] = "legacy"
        self.tick()
        self.assertEqual([("11-card.md", "in-progress")], self.launch.calls)

    def test_an_unnamed_phase_can_be_paused_by_a_person(self):
        revision = self.put_in_force(agreement(capacity={"implementation": 3}))
        self.own_execution()
        self.members("11")
        self.board.add("21")
        agreements.set_scope("phase:9", "pause", revision)
        self.tick()
        self.assertEqual(["21-card.md"], self.running())
        self.assertEqual("paused", self.item("11-card.md")["code"])
        entry = agreements.effective_document()[0]["phases"]["9"]
        self.assertEqual({"enabled": True, "paused": True}, entry,
                         "the entry written only narrows")

    def test_a_held_phase_is_scheduled_by_nobody(self):
        self.put_in_force(agreement(capacity={"implementation": 3}))
        self.own_execution()
        self.members("11")
        doc = phasescope._new_model()
        doc.update(status="complete", holds={"9": {
            "kind": "held", "reason": "halted under the runner",
            "card": "9-card.md"}})
        phasescope._write_model(doc)
        self.tick()
        self.assertEqual([], self.launch.calls)
        self.assertEqual("phase-migrating", self.item("11-card.md")["code"])


class AuthorityAndCapacityOnlyNarrow(ScopedBoard):
    def test_a_phase_cannot_widen_authority_or_double_a_budget(self):
        doc = self.phase_doc(authority={"merge": True})
        doc["bench"]["authority"]["merge"] = False
        codes = [p["code"] for p in agreements.validate(doc)["problems"]]
        self.assertIn("authority-expansion", codes)
        doc = self.phase_doc(allocation={"implementation": 4})
        codes = [p["code"] for p in agreements.validate(doc)["problems"]]
        self.assertIn("capacity-overcommitted", codes)

    def test_the_phase_spends_its_allocation_not_the_benchs(self):
        self.put_in_force(self.phase_doc(strategy="dependencies",
                                         allocation={"implementation": 1,
                                                     "dailyStarts": 10}))
        self.own_execution()
        self.members("11", "12")
        self.board.add("21")
        self.tick()
        running = self.running()
        self.assertIn("21-card.md", running)
        self.assertEqual(1, len([r for r in running if r in ("11-card.md",
                                                             "12-card.md")]),
                         "the Bench scope's free slots never run a member")
        self.assertEqual("slots-full", self.item("12-card.md")["code"])

    def test_a_held_or_foreign_member_is_visibly_not_the_scopes(self):
        self.put_in_force(self.phase_doc(strategy="dependencies"))
        self.own_execution()
        self.members("11", "12")
        self.eligible = lambda task: (
            {"eligible": False, "code": "member-held", "via": "phase:9",
             "reason": "ada holds card 12; take it over or move it yourself"}
            if task["file"] == "12-card.md" else
            {"eligible": True, "code": "phase-delegated", "via": "phase:9",
             "reason": "delegated by the phase"})
        self.tick()
        self.assertEqual(["11-card.md"], self.running())
        item = self.item("12-card.md")
        self.assertEqual(("blocked", "not-eligible"), (item["state"], item["code"]))
        self.assertIn("take it over", item["reason"])


class PauseIsNotCancel(ScopedBoard):
    def test_a_parent_pause_stops_child_starts_and_cancels_nothing(self):
        revision = self.put_in_force(self.phase_doc(strategy="dependencies"))
        self.own_execution()
        self.members("11", "12")
        state.AGENTS["running-11"] = {"task": "11-card.md", "mode": "work",
                                      "status": "running", "started": time.time()}
        agreements.set_scope("bench", "pause", revision)
        self.board.add("21")
        self.tick()
        self.assertEqual([], self.launch.calls)
        self.assertEqual("paused", self.item("12-card.md")["code"])
        self.assertEqual("running", state.AGENTS["running-11"]["status"],
                         "a pause observes work in flight; it cancels nothing")

    def test_a_child_pause_leaves_unrelated_work_available(self):
        revision = self.put_in_force(self.phase_doc())
        self.own_execution()
        self.members("11")
        self.board.add("21")
        agreements.set_scope("phase:9", "pause", revision)
        self.tick()
        self.assertEqual(["21-card.md"], self.running())
        self.assertEqual("paused", self.item("11-card.md")["code"])
        self.assertEqual("phase:9",
                         agreements.scope(agreements.effective(),
                                          "phase:9")["pausedBy"])


if __name__ == "__main__":
    unittest.main()
