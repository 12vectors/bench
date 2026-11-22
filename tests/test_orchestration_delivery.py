"""Coordinating evidence, independent review, repair and the user's merge
policy for a standalone card in review/ (task 142).

Each class is one acceptance criterion of the card:

- a candidate head that moves invalidates the checks and the review
  chosen for the old one; only exact-head evidence is used;
- a review counts only from a different resolved model, and a same-model
  alias, an unresolved model, an undeclared name and an unavailable
  reviewer are refused with a remedy rather than weakened;
- an explicitly ungated policy can delegate merging without any new
  gate, unmet configured requirements still block, and nothing reads
  "verified";
- the repair ceiling, a check the work reading may not ask for, a check
  that could not reach a verdict and authority withdrawn before the act
  end apart, each written on the card;
- a lost merge reply and an already-merged card reconcile against the
  delivered record, the recovery record and the remote, never by a second
  merge.

Every side effect is a stand-in: no adapter, model, check runner, git
remote or host is involved.

    python3 -m unittest tests.test_orchestration_delivery -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import agents  # noqa: E402
import agreements  # noqa: E402
import changes  # noqa: E402
import completion  # noqa: E402
import config  # noqa: E402
import coordinate  # noqa: E402
import decisions  # noqa: E402
import execowner  # noqa: E402
import gates  # noqa: E402
import integration  # noqa: E402
import movements  # noqa: E402
import operations  # noqa: E402
import orchestrator  # noqa: E402
import phasescope  # noqa: E402
import plans  # noqa: E402
import profiles  # noqa: E402
import required  # noqa: E402
import scheduler  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

A = "a" * 40
B = "b" * 40
CARD = "7-card.md"
MODELS = {"canonical": ["model-one", "model-two", "model-three"],
          "aliases": {"one": "model-one", "two": "model-two"}}


def gate(decision="ungated", checks=(), reviews=(), summary="read"):
    rows = [{"kind": "check", "name": name, "check": name, "state": st,
             "reason": reason, "evidence": evid}
            for name, st, reason, evid in checks]
    unmet = [r for r in rows if r["state"] == "unmet"]
    if decision == "blocked" and not unmet:
        unmet = [{"kind": "review", "name": "github review", "state": "unmet",
                  "reason": "review-needed"}]
    return {"decision": decision, "requirements": rows, "unmet": unmet,
            "pending": [r for r in rows if r["state"] == "pending"],
            "reviews": list(reviews), "summary": summary}


class Board:
    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.moves: list[tuple] = []

    def add(self, name=CARD, stage="review", pr="https://example.test/pr/7"):
        number = name.split("-")[0]
        self.cards[name] = {"file": name, "number": number, "stage": stage,
                            "priority": "Medium", "dependsOn": [],
                            "touches": None, "isPhase": False, "phase": None,
                            "pr": pr,
                            "body": f"# {number} — card\n\nBuild it.\n"}
        return name

    def collect(self):
        stages = {}
        for card in self.cards.values():
            stages.setdefault(card["stage"], []).append(dict(card))
        return {"stages": [{"slug": s, "tasks": t} for s, t in stages.items()]}

    def stage_of(self, name):
        card = self.cards.get(name)
        return card["stage"] if card else None

    def move(self, name, source, target, actor="you", **_):
        self.cards[name]["stage"] = target
        self.moves.append((name, source, target, actor))


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-deliver-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.local, self.work = self.dir / "local", self.dir / "work"
        self.local.mkdir()
        (self.work / "tasks").mkdir(parents=True)
        self.board = Board()
        self.board.add()
        self.notes: list[dict] = []
        self.heads = {CARD: A}
        self.gate = gate()
        self.verdicts: dict = {}
        self.choice = {"work": ("claude", "one"), "act-pr": ("claude", "one"),
                       "review": ("claude", "model-two")}
        self.installed = {"claude": True}
        self.manifests: dict[str, dict] = {}
        self.plan_reading = {"head": A, "items": [], "requestable": 0,
                             "refusal": "nothing-needed", "why": None}
        self.batches: dict = {}
        self.calls: dict[str, list] = {k: [] for k in (
            "verify", "review", "repair", "merge", "delivered")}
        self.remote = {"held": False, "why": ""}
        self.delivered = None
        self.pending = None
        self.merge_result = {"merged": True, "published": True, "moved": True}
        self.merge_raises: BaseException | None = None
        patches = (
            mock.patch.object(config, "LOCAL", self.local),
            mock.patch.object(config, "STATE", self.local / "state"),
            mock.patch.object(config, "MANIFESTS_DIR", self.local / "state" / "manifests"),
            mock.patch.object(config, "COMMIT_MOVES", False),
            mock.patch.object(config, "work_root", lambda: self.work),
            mock.patch.object(config, "app_merge_on_origin", lambda: False),
            mock.patch.object(config, "agent_choice", self._choice),
            mock.patch.object(movements, "eligibility",
                              lambda task, tasks=None, snap=None: {
                                  "eligible": True, "code": "own-move",
                                  "via": "own", "reason": "yours"}),
            mock.patch.object(movements, "snapshot", lambda: {
                "identity": {"name": "tester", "source": "test"}}),
            mock.patch.object(taskfiles, "collect", self.board.collect),
            mock.patch.object(taskfiles, "find_stage_of", self.board.stage_of),
            mock.patch.object(taskfiles, "move_task", self.board.move),
            mock.patch.object(decisions, "note", self._note),
            mock.patch.object(state, "record_board_event", lambda event: None),
            mock.patch.object(state, "broadcast", lambda frame: None),
            mock.patch.object(gates, "candidate", lambda f, s, **_: {
                "state": "present" if self.heads.get(f) else "none-yet",
                "commit": self.heads.get(f)}),
            mock.patch.object(gates, "evaluate", lambda f, s, **_: self.gate),
            mock.patch.object(gates, "verdict_at",
                              lambda f, h: self.verdicts.get((f, h))),
            mock.patch.object(gates, "contained", lambda c, b: False),
            mock.patch.object(profiles, "adapter_identity", self._adapter),
            mock.patch.object(profiles, "recorded",
                              lambda agent: self.manifests.get(agent)),
            mock.patch.object(required, "plan", lambda f, s=None: dict(self.plan_reading)),
            mock.patch.object(required, "start", self._start_checks),
            mock.patch.object(required, "latest", lambda f: self.batches.get(f)),
            mock.patch.object(agents, "start_pr_review", self._review),
            mock.patch.object(agents, "start_pr_fix", self._repair),
            mock.patch.object(changes, "remote", lambda: "origin"),
            mock.patch.object(changes, "woven", lambda f, s: dict(self.board.cards[f])),
            mock.patch.object(plans, "pr_base", lambda f, t=None: "trunk"),
            mock.patch.object(completion, "remote_holds", self._holds),
            mock.patch.object(completion, "complete_task", self._complete),
            mock.patch.object(completion, "complete_delivered", self._delivered_now),
            mock.patch.object(completion, "delivered_record", lambda f: self.delivered),
            mock.patch.object(completion, "recovery", lambda f: self.pending),
            # task 189: the candidate holds its target unless a test says
            # otherwise (tests/test_admission.py covers the rest)
            mock.patch.object(integration, "reading", lambda task, **kw: {
                "state": "up-to-date", "target": "trunk", "fresh": True,
                "required": False, "head": kw.get("head")}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(execowner.release_lock)
        saved = dict(state.AGENTS)
        state.AGENTS.clear()
        self.addCleanup(lambda: (state.AGENTS.clear(), state.AGENTS.update(saved)))

    # ── stand-ins ────────────────────────────────────────────────────
    def _choice(self, intent, values=None):
        harness, model = self.choice.get(intent, ("claude", ""))
        return {"intent": intent,
                "harness": {"value": harness, "setting": "H", "source": "default",
                            "inherited": True},
                "model": {"value": model, "setting": "M", "source": "default",
                          "inherited": True, "harnessDefault": not model}}

    def _adapter(self, name=None):
        if not self.installed.get(name):
            return {"name": name, "path": None, "intents": None}
        return {"name": name, "path": f"/adapters/{name}", "intents": None,
                "models": MODELS}

    def _note(self, filename, stage, kind, fields, *, key, **_):
        self.notes.append({"file": filename, "kind": kind, "key": key, **fields})
        return {"duplicate": False, "id": key}

    def _start_checks(self, filename, stage=None, *, head=None, actor="you",
                      trigger="click", **_):
        self.calls["verify"].append((filename, head, actor, trigger))
        batch = {"id": f"rq-{len(self.calls['verify'])}", "task": filename,
                 "head": head, "state": "running", "requested": time.time()}
        self.batches[filename] = batch
        return dict(batch)

    def _run(self, filename, kind):
        agent = f"{kind}-{filename[:-3]}-{len(self.calls[kind])}"
        state.AGENTS[agent] = {"task": filename, "status": "running",
                               "mode": "review" if kind == "review" else "work",
                               "started": time.time()}
        return {"id": agent, "name": "Juno"}

    def _review(self, filename, stage):
        self.calls["review"].append((filename, stage))
        return self._run(filename, "review")

    def _repair(self, filename, stage, *, operation=None, integration=None):
        # task 174: the delegated repair names its own ledger entry
        self.calls["repair"].append((filename, stage))
        self.operations_named = getattr(self, "operations_named", []) + [operation]
        return self._run(filename, "repair")

    def _holds(self, rname, target, commit):
        return self.remote["held"], "", self.remote["why"]

    def _complete(self, filename, stage, *, expect_head=None, actor="you",
                  source="api", verified_actor=None):
        self.calls["merge"].append((filename, stage, expect_head, actor))
        # task 166: the orchestrator's completion says where it came from
        self.calls.setdefault("sources", []).append(source)
        if self.merge_raises is not None:
            raise self.merge_raises
        return dict(self.merge_result)

    def _delivered_now(self, filename, stage, head, *, actor="orchestrator",
                       source="api", verified_actor=None):
        self.calls["delivered"].append((filename, head))
        self.calls.setdefault("sources", []).append(source)
        return {"merged": False, "reconciled": True, "moved": True}

    # ── helpers ──────────────────────────────────────────────────────
    def enable(self, authority=None, **ceilings):
        doc = agreements.new_draft()
        doc["bench"]["enabled"] = True
        words = authority if authority is not None else agreements.AUTHORITIES
        doc["bench"]["authority"] = {w: w in words for w in agreements.AUTHORITIES}
        doc["bench"].setdefault("capacity", {}).update(ceilings)
        agreements.save_draft(doc, agreements.revision(agreements.draft_document()[0]))
        agreements.apply(agreements.effective()["revision"], agreements.revision(doc))
        execowner.designate("none")
        self.assertTrue(execowner.acquire_lock())

    def item(self, card=CARD) -> dict:
        found = coordinate.plan(self.board.collect())
        return next(i for i in found["items"] if i["card"] == card)

    def tick(self) -> dict:
        return scheduler.run_pass(board=self.board.collect(),
                                  start=lambda *a, **k: None)

    def end_runs(self, status="done"):
        for record in state.AGENTS.values():
            if record.get("status") == "running":
                record["status"] = status

    def approve(self, head=A, agent="review-run", model="model-two",
                verdict="APPROVE"):
        self.verdicts[(CARD, head)] = {"verdict": verdict, "head": head,
                                       "agent": agent}
        self.manifests[agent] = {"intent": "review", "task": CARD,
                                 "selection": {"harness": {"name": "claude"},
                                               "model": {"value": model}}}

    def write_manifest(self, agent, intent, model):
        folder = config.MANIFESTS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{agent}.json").write_text(json.dumps({
            "agent": agent, "intent": intent, "task": CARD,
            "recorded": time.time(),
            "selection": {"harness": {"name": "claude"},
                          "model": {"value": model}}}))

    def ops(self, action=None, status=None):
        return [o for o in operations.all_ops()
                if (action is None or o["action"] == action)
                and (status is None or o["status"] == status)]

    def card_notes(self, kind):
        return [n for n in self.notes if n["kind"] == kind]


# ── 1. the candidate head decides ─────────────────────────────────────

class HeadMovementInvalidatesSelection(Sandbox):
    def test_an_approval_of_the_old_head_is_not_this_heads_review(self):
        self.enable()
        self.approve(head=A)
        self.assertEqual("merge", self.item()["code"])
        self.heads[CARD] = B
        found = self.item()
        self.assertEqual("review", found["code"])
        self.assertEqual(B, found["head"])

    def test_checks_asked_at_the_old_head_do_not_answer_the_new_one(self):
        self.enable()
        self.gate = gate("blocked", checks=[("unit", "unmet", "not-run", None)])
        self.plan_reading.update(refusal=None, requestable=1,
                                 items=[{"check": "unit", "state": "request"}])
        self.tick()
        self.assertEqual([(CARD, A, "orchestrator", "orchestrator")],
                         self.calls["verify"])
        self.batches[CARD]["state"] = "done"
        self.tick()                       # the batch ended: a receipt
        self.assertEqual("ended", self.ops("verify")[0]["status"])
        self.assertEqual("checks-unanswered", self.item()["code"])
        self.heads[CARD] = B              # a new commit is a new question
        self.plan_reading["head"] = B
        self.assertEqual("verify", self.item()["code"])
        self.tick()
        self.assertEqual(B, self.calls["verify"][-1][1])

    def test_the_merge_takes_exactly_the_head_it_was_decided_about(self):
        self.enable()
        self.approve()
        self.tick()
        self.assertEqual([(CARD, "review", A, "orchestrator")], self.calls["merge"])
        self.assertEqual(["orchestrator"], self.calls["sources"])


# ── 2. independent review: a different resolved model ────────────────

class IndependentReviewIsADifferentModel(Sandbox):
    def test_a_different_declared_model_launches_one_review(self):
        self.enable()
        self.tick()
        self.assertEqual([(CARD, "review")], self.calls["review"])
        self.tick()                        # running: never a second review
        self.assertEqual(1, len(self.calls["review"]))
        self.assertEqual("launched", self.ops("review")[0]["status"])

    def test_the_same_model_under_an_alias_is_refused(self):
        self.enable()
        self.choice["review"] = ("claude", "model-one")   # work rides "one"
        found = self.item()
        self.assertEqual("review-not-independent", found["code"])
        self.assertEqual("same-model", found["independence"])
        self.assertIn("BOARD_AGENT_MODEL_REVIEW", found["reason"])
        self.tick()
        self.assertEqual([], self.calls["review"])
        blockers = self.card_notes("blocker")
        self.assertEqual("open", blockers[0]["state"])
        self.assertIn("review-not-independent", blockers[0]["text"])

    def test_an_unresolved_or_undeclared_model_is_not_assumed_different(self):
        self.enable()
        self.choice["review"] = ("claude", "")
        self.assertEqual("reviewer-model-unresolved", self.item()["independence"])
        self.choice["review"] = ("claude", "someone-elses-model")
        self.assertEqual("model-identity-unverified", self.item()["independence"])
        self.choice["review"] = ("claude", "model-two")
        self.write_manifest("work-1", "work", "")      # rode the harness default
        self.assertEqual("implementer-model-unknown", self.item()["independence"])
        self.tick()
        self.assertEqual([], self.calls["review"])

    def test_a_reviewer_harness_that_is_not_installed_is_unavailable(self):
        self.enable()
        self.choice["review"] = ("elsewhere", "model-two")
        found = self.item()
        self.assertEqual("review-unavailable", found["independence"])
        self.assertIn("Settings → Agents", found["reason"])

    def test_a_repair_model_counts_as_an_implementer(self):
        self.enable()
        self.choice["act-pr"] = ("claude", "two")       # the reviewer's model
        self.assertEqual("same-model", self.item()["independence"])

    def test_a_verdict_from_a_same_model_run_is_not_counted(self):
        self.enable()
        self.approve(model="one")          # an earlier review rode the work model
        found = self.item()
        self.assertEqual("review", found["code"])
        self.assertEqual("same-model", found["review"]["counted"]["code"])
        self.assertFalse(found["review"]["counted"]["independent"])
        self.assertTrue(found["review"]["independence"]["independent"])

    def test_a_settings_change_before_the_launch_refuses_it(self):
        self.enable()
        found = self.item()
        self.assertEqual("review", found["code"])
        self.choice["review"] = ("claude", "one")
        result = coordinate.execute(found, self.board.cards[CARD],
                                    self.board.collect())
        self.assertEqual("refused", result["outcome"])
        self.assertEqual("review-not-independent", result["code"])
        self.assertEqual([], self.calls["review"])


# ── 3. the user's merge policy, ungated included ─────────────────────

class UserChosenMergePolicy(Sandbox):
    def test_an_ungated_delegated_merge_runs_no_checks_and_says_so(self):
        self.enable(authority=("merge",))
        found = self.item()
        self.assertEqual("merge", found["code"])
        self.assertEqual(coordinate.NO_CHECKS, found["checks"]["words"])
        self.assertIn("not verified", found["label"])
        self.tick()
        self.assertEqual([], self.calls["verify"])
        self.assertEqual(1, len(self.calls["merge"]))
        receipt = [n for n in self.card_notes("action") if n["phase"] == "receipt"]
        self.assertIn("not verified", receipt[-1]["summary"])
        self.assertNotIn("passed", json.dumps(found))

    def test_unmet_configured_requirements_still_block(self):
        self.enable()
        self.approve()
        self.gate = gate("blocked", checks=[("unit", "met", "passed", "ev-1")],
                         reviews=["github"], summary="github review is unmet")
        found = self.item()
        self.assertEqual("requirements-unmet", found["code"])
        self.tick()
        self.assertEqual([], self.calls["merge"])
        self.assertNotIn("verified", json.dumps(found).replace("not verified", ""))

    def test_a_pending_requirement_holds_and_a_met_one_merges(self):
        self.enable()
        self.approve()
        self.gate = gate("pending", checks=[("unit", "pending", "running", None)])
        self.assertEqual("checks-running", self.item()["code"])
        self.gate = gate("allowed", checks=[("unit", "met", "passed", "ev-1")])
        found = self.item()
        self.assertEqual("merge", found["code"])
        self.assertEqual("every requirement you configured is met", found["label"])

    def test_merge_not_delegated_is_the_users(self):
        self.enable(authority=("review",))
        self.approve()
        found = self.item()
        self.assertEqual("merge-not-delegated", found["code"])
        self.assertEqual("awaiting", found["state"])
        self.tick()
        self.assertEqual([], self.calls["merge"])

    def test_checks_and_merges_spend_no_slot_or_start(self):
        self.enable()
        self.approve()
        self.tick()
        used = orchestrator.usage(agreements.effective(),
                                  [dict(self.board.cards[CARD])])
        self.assertEqual(0, used["startsTodayTotal"])
        self.assertEqual(0, used["reserved"])


# ── 4. distinct, safe endings on the card ─────────────────────────────

class DistinctEndings(Sandbox):
    def failing(self):
        self.gate = gate("blocked", checks=[("unit", "unmet", "failed", "ev-9")])

    def test_a_failed_check_launches_a_counted_repair(self):
        self.enable()
        self.failing()
        self.tick()
        self.assertEqual([(CARD, "review")], self.calls["repair"])
        attempt = self.card_notes("attempt")[0]
        self.assertEqual((1, 2, "started"),
                         (attempt["attempt"], attempt["of"], attempt["outcome"]))
        self.end_runs()
        self.tick()                         # same head: the repair changed nothing
        self.assertEqual("repair-no-change", self.item()["code"])
        self.assertEqual(1, len(self.calls["repair"]))

    def test_the_repair_ceiling_stops_and_says_so(self):
        self.enable(repairRounds=1)
        self.failing()
        self.tick()
        self.end_runs()
        self.heads[CARD] = B                # the repair committed; still failing
        self.tick()
        found = self.item()
        self.assertEqual("repair-exhausted", found["code"])
        self.assertEqual(1, len(self.calls["repair"]))
        exhausted = [n for n in self.card_notes("attempt")
                     if n["outcome"] == "exhausted"]
        self.assertEqual(1, len(exhausted))
        self.assertTrue(any("repair-exhausted" in n["text"]
                            for n in self.card_notes("blocker")))

    def test_a_check_the_work_reading_may_not_ask_for_is_a_permission_refusal(self):
        self.enable()
        self.gate = gate("blocked", checks=[("e2e", "unmet", "not-run", None)])
        self.plan_reading.update(refusal="nothing-needed", requestable=0, items=[
            {"check": "e2e", "state": "unavailable",
             "remedy": "add it to the work profile's verify list"}])
        found = self.item()
        self.assertEqual("check-not-granted", found["code"])
        self.assertIn("work profile", found["reason"])
        self.tick()
        self.assertEqual([], self.calls["verify"])

    def test_a_check_that_could_not_reach_a_verdict_is_not_repaired(self):
        self.enable()
        self.gate = gate("blocked", checks=[("unit", "unmet", "prepare-failed",
                                             "ev-3")])
        found = self.item()
        self.assertEqual("verification-setup-failed", found["code"])
        self.tick()
        self.assertEqual([], self.calls["repair"])
        blocker = self.card_notes("blocker")[0]
        self.assertEqual(["evidence:ev-3"], blocker["evidence"])

    def test_authority_withdrawn_before_the_act_is_its_own_refusal(self):
        self.enable()
        self.approve()
        found = self.item()
        real = orchestrator.authorize
        seen = []

        def flip(action, filename, **kw):
            seen.append(action)
            if kw.get("operation"):
                return {"allowed": False, "code": "paused",
                        "reason": "the bench scope is paused"}
            return real(action, filename, **kw)

        with mock.patch.object(orchestrator, "authorize", flip):
            result = coordinate.execute(found, self.board.cards[CARD],
                                        self.board.collect())
        self.assertEqual("authority-revoked", result["code"])
        self.assertEqual([], self.calls["merge"])
        self.assertEqual("refused", self.ops("merge")[0]["status"])
        receipts = [n for n in self.card_notes("action") if n["phase"] == "receipt"]
        self.assertEqual("authority-revoked", receipts[-1]["outcome"])
        # the next pass asks the agreement afresh rather than holding the head
        self.assertEqual("merge", self.item()["code"])

    def test_a_refused_launch_is_not_asked_again_at_the_same_head(self):
        self.enable()

        def refuse(filename, stage):
            self.calls["review"].append((filename, stage))
            raise ValueError("the PR's head could not be resolved")

        with mock.patch.object(agents, "start_pr_review", refuse):
            self.tick()
            self.tick()
        self.assertEqual(1, len(self.calls["review"]))
        found = self.item()
        self.assertEqual("step-refused", found["code"])
        self.heads[CARD] = B              # a new head is a new question
        self.assertEqual("review", self.item()["code"])

    def test_a_merge_that_left_the_card_in_review_is_not_merged_twice(self):
        self.enable()
        self.approve()
        self.merge_result = {"merged": True, "published": True, "moved": False,
                             "partial": True}
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.calls["merge"]))
        self.assertEqual("partial", self.item()["code"])

    def test_a_card_that_is_not_ours_is_neither_read_into_nor_written(self):
        self.enable()
        with mock.patch.object(movements, "eligibility", lambda *a, **k: {
                "eligible": False, "code": "foreign-move", "via": "x",
                "reason": "someone else moved it"}):
            self.assertEqual("not-ours", self.item()["code"])
            self.tick()
        self.assertEqual([], self.notes)


# ── 5. uncertain merges reconcile against the exact remote state ─────

class MergesReconcile(Sandbox):
    def intent(self) -> dict:
        key = operations.key_for("merge", CARD, A, 1)
        return operations.begin(key, action="merge", card=CARD, number="7",
                                scope="bench", pool="none", basis=A, attempt=1)

    def test_a_lost_reply_whose_merge_landed_is_cleaned_up_not_merged_again(self):
        self.enable()
        self.approve()
        self.intent()
        self.remote["held"] = True
        self.tick()
        self.assertEqual("landed", self.ops("merge")[0]["outcome"])
        self.assertEqual([], self.calls["merge"])
        self.assertEqual([(CARD, A)], self.calls["delivered"])

    def test_an_already_delivered_head_needs_nothing(self):
        self.enable()
        self.approve()
        self.intent()
        self.delivered = {"commit": A}
        coordinate.reconcile()
        self.assertEqual(("ended", "merged"),
                         (self.ops("merge")[0]["status"], self.ops("merge")[0]["outcome"]))
        self.assertEqual([], self.calls["merge"])

    def test_a_merge_awaiting_publication_is_not_merged_again(self):
        self.enable()
        self.approve()
        self.intent()
        self.pending = {"commit": A, "target": "trunk"}
        self.tick()
        self.assertEqual("not-published", self.ops("merge")[0]["outcome"])
        self.assertEqual("not-published", self.item()["code"])
        self.assertEqual([], self.calls["merge"])

    def test_a_remote_that_cannot_be_read_holds_the_outcome(self):
        self.enable()
        self.approve()
        self.intent()
        self.remote.update(held=None, why="origin could not be read")
        self.tick()
        self.assertEqual("unknown", self.ops("merge")[0]["status"])
        self.assertEqual("outcome-unknown", self.item()["code"])
        self.assertEqual([], self.calls["merge"])
        scheduler.resolve(self.ops("merge")[0]["key"], "not-launched", "tester")
        self.tick()                    # a person settled it: one merge now
        self.assertEqual(1, len(self.calls["merge"]))

    def test_a_local_merge_that_provably_never_happened_may_be_retried(self):
        self.enable()
        self.approve()
        self.intent()
        coordinate.reconcile()
        self.assertEqual("abandoned", self.ops("merge")[0]["status"])

    def test_a_merge_that_raises_is_held_unknown(self):
        self.enable()
        self.approve()
        self.merge_raises = RuntimeError("connection dropped mid-reply")
        self.tick()
        self.assertEqual("unknown", self.ops("merge")[0]["status"])
        self.tick()
        self.assertEqual(1, len(self.calls["merge"]))

    def test_a_merge_the_remote_already_holds_is_reconciled_first(self):
        self.enable()
        self.approve()
        self.remote["held"] = True
        self.tick()
        self.assertEqual([], self.calls["merge"])
        self.assertEqual("reconciled", self.ops("merge")[0]["outcome"])


# ── completion: the exact head, and the move's actor ──────────────────

# ── 6. the current target, under orchestration (task 189) ─────────────

TWO = "8-other.md"


class AgainstTheCurrentTarget(Sandbox):
    """Step 3a of `coordinate.assess` and the steps it takes — the refresh,
    the integration repair, the admission lock and the target guard —
    run through the scheduler's own pass. The integration reading is the
    test's (`self.fit`); `tests/test_admission.py` reads it from real git."""

    def setUp(self):
        super().setUp()
        self.fit = {"state": "up-to-date", "target": "trunk", "fresh": True,
                    "required": True, "targetTip": "c" * 40}
        reading = mock.patch.object(
            integration, "reading",
            lambda task, **kw: {**self.fit, "head": kw.get("head")})
        reading.start()
        self.addCleanup(reading.stop)
        self.updates: list[dict] = []
        self.update_result = {"ok": True, "outcome": "merged", "head": B,
                              "sentence": "merged trunk into the branch"}
        self.update_raises: BaseException | None = None
        update = mock.patch.object(coordinate, "update_against_target",
                                   self._update)
        update.start()
        self.addCleanup(update.stop)
        self.repairs: list[dict | None] = []
        fetches = mock.patch.object(coordinate, "_fetch_target",
                                    lambda target: self.fetched.append(target))
        self.fetched: list = []
        fetches.start()
        self.addCleanup(fetches.stop)
        self.gate = gate("allowed", checks=[("unit", "met", "passed", "ev-1")])
        self.approve()

    def _update(self, filename, expected=None, *, actor="person",
                operation=None, launch_repair=True):
        self.updates.append({"file": filename, "head": (expected or {}).get(
            "head"), "actor": actor, "operation": operation,
            "launch_repair": launch_repair})
        if self.update_raises is not None:
            raise self.update_raises
        return dict(self.update_result)

    def _repair(self, filename, stage, *, operation=None, integration=None):
        self.repairs.append(integration)
        return super()._repair(filename, stage, operation=operation,
                               integration=integration)

    def stale(self, state="target-changed", **extra):
        self.fit = {"state": state, "target": "trunk", "fresh": False,
                    "required": True, "targetTip": "c" * 40,
                    "sentence": f"trunk moved ({state})", **extra}

    def test_a_moved_target_is_refreshed_once_without_an_agent(self):
        self.enable()
        self.stale()
        found = self.item()
        self.assertEqual("refresh", found["code"])
        self.tick()
        self.assertEqual(1, len(self.updates))
        self.assertEqual({"file": CARD, "head": A, "actor": "orchestrator",
                          "launch_repair": False},
                         {k: v for k, v in self.updates[0].items()
                          if k != "operation"})
        self.assertEqual([], self.calls["repair"], "a clean update runs no agent")
        self.assertEqual([], self.calls["merge"], "the stale head is not merged")
        op = self.ops("refresh")[0]
        self.assertEqual(("ended", "merged"), (op["status"], op["outcome"]))
        self.assertEqual(op["key"], self.updates[0]["operation"])
        used = orchestrator.usage(agreements.effective(),
                                  [dict(self.board.cards[CARD])])
        self.assertEqual(0, used["startsTodayTotal"], "a refresh spends no start")
        # the update's new head holds the target: its own checks and review
        # first, and only then the merge of that combined head
        self.heads[CARD] = B
        self.fit = {"state": "up-to-date", "target": "trunk", "fresh": True,
                    "required": True}
        self.assertEqual("review", self.item()["code"],
                         "the old head's approval is not this head's")
        self.approve(head=B)
        self.assertEqual("merge", self.item()["code"])
        self.tick()
        self.assertEqual([(CARD, "review", B, "orchestrator")],
                         self.calls["merge"])

    def test_a_stale_head_is_refreshed_before_its_checks_are_asked(self):
        """Review round 2, finding 1: checks asked at a head that does not
        hold the target would be spent on uncombined work and asked again
        after the update, so the update comes first."""
        self.enable()
        self.gate = gate("blocked", checks=[("unit", "unmet", "not-run", None)])
        self.stale()
        self.assertEqual("refresh", self.item()["code"])
        self.tick()
        self.assertEqual(1, len(self.updates))
        self.assertEqual([], self.ops("verify"), "no check asked at the old head")

    def test_a_stale_head_is_refreshed_before_its_review(self):
        self.enable()
        self.verdicts.clear()
        self.gate = gate("blocked", checks=[("unit", "met", "passed", "ev-1")])
        self.stale()
        self.assertEqual("refresh", self.item()["code"])
        self.tick()
        self.assertEqual(1, len(self.updates))
        self.assertEqual([], self.ops("review"), "no review of the old head")

    def test_a_stale_head_whose_check_failed_is_repaired_first(self):
        # a failure at the head is the repair's to answer, as before
        self.enable()
        self.gate = gate("blocked", checks=[("unit", "unmet", "failed", "ev-1")])
        self.stale()
        self.assertEqual("repair", self.item()["action"])

    def test_refreshing_is_the_users_without_the_repair_word(self):
        self.enable(authority=("review", "merge"))
        self.stale()
        self.assertEqual("refresh-not-delegated", self.item()["code"])
        self.tick()
        self.assertEqual([], self.updates)
        self.assertEqual([], self.calls["merge"])

    def test_a_conflict_is_repaired_and_the_run_is_told_what_conflicted(self):
        self.enable()
        self.stale("conflicts", attempt={"outcome": "conflict",
                                         "conflicts": ["shared.txt"]})
        found = self.item()
        self.assertEqual("repair", found["action"])
        self.assertTrue(found["repairIntegration"])
        self.tick()
        self.assertEqual(1, len(self.calls["repair"]))
        brief = self.repairs[0]
        self.assertEqual("trunk", brief["target"])
        self.assertEqual(["shared.txt"], brief["conflicts"])
        self.assertEqual(A, brief["head"])
        self.assertEqual([], self.updates, "the conflict is not updated blind")

    def test_a_lost_admission_lock_is_asked_again_not_held(self):
        self.enable()
        self.merge_raises = integration.Busy("another admission into trunk is "
                                             "in progress — nothing was merged")
        self.tick()
        op = self.ops("merge")[0]
        self.assertEqual(("refused", "admission-busy"),
                         (op["status"], op["code"]))
        self.merge_raises = None
        self.assertEqual("merge", self.item()["code"],
                         "a transient wait does not hold the card for a person")
        self.tick()
        self.assertEqual(2, len(self.calls["merge"]))

    def test_a_target_that_moved_on_the_remote_is_fetched_then_updated(self):
        self.enable()
        self.merge_raises = integration.Stale(
            "target-moved", "origin/trunk is at cccc, which this checkout has "
            "not fetched — nothing was merged", target="trunk",
            targetTip="d" * 40)
        self.tick()
        self.assertEqual(["trunk"], self.fetched)
        self.assertEqual("target-moved", self.ops("merge")[0]["code"])
        self.merge_raises = None
        self.stale()                     # what the fetch makes the next read say
        found = self.item()
        self.assertEqual("refresh", found["code"],
                         "not merge-refused, not step-refused")

    def test_a_guard_refused_integration_or_refresh_does_not_hold(self):
        self.enable()
        self.stale()
        self.update_raises = coordinate.UpdateRefused("busy", "an update is "
                                                      "already in progress")
        self.tick()
        self.assertEqual("busy", self.ops("refresh")[0]["code"])
        self.update_raises = None
        self.assertEqual("refresh", self.item()["code"])
        # and a target-changed refusal of any final step is answered afresh
        for code in ("admission-busy", "target-changed", "target-moved"):
            self.assertIn(code, coordinate.ANSWERED_AFRESH)

    def test_a_refused_push_is_pushed_once_then_held_for_a_person(self):
        self.enable()
        self.fit["unpushed"] = True
        self.assertEqual("refresh", self.item()["code"])
        self.update_result = {"ok": False, "outcome": "push-refused",
                              "head": A, "sentence": "origin refused it"}
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.updates))
        found = self.item()
        self.assertEqual("update-not-pushed", found["code"])
        self.assertEqual("blocked", found["state"])
        self.assertEqual([], self.calls["merge"])

    def test_one_card_holds_a_targets_final_sequence(self):
        self.enable()
        self.board.add(TWO)
        self.heads[TWO] = B
        self.verdicts[(TWO, B)] = dict(self.verdicts[(CARD, A)], head=B)
        self.stale()
        found = {i["card"]: i for i in
                 coordinate.plan(self.board.collect())["items"]}
        self.assertEqual("refresh", found[CARD]["code"])
        self.assertEqual("admission-queued", found[TWO]["code"])
        self.assertEqual(CARD, found[TWO]["waitingFor"])
        self.tick()
        self.assertEqual([CARD], [u["file"] for u in self.updates])

    def test_an_update_left_without_a_receipt_is_settled_not_repeated(self):
        self.enable()
        self.stale()
        op = operations.begin(operations.key_for("refresh", CARD, A, 1),
                              action="refresh", card=CARD, number="7",
                              scope="bench", pool="none", basis=A, attempt=1)
        with mock.patch.object(gates, "branch_tips",
                               lambda: {changes.branch_of(CARD): "9" * 40}):
            self.tick()                  # the restart: the branch moved
        self.assertEqual("unknown", operations.read(op["key"])["status"])
        self.assertEqual([], self.updates, "never updated again blind")
        self.assertEqual("outcome-unknown", self.item()["code"])


class PhaseTargets(unittest.TestCase):
    """A phase member lands in its phase branch and a phase card in the
    application base; a member's integration passes the same admission
    lock and guard a merge does (`coordinate._integrate`)."""

    MEMBER = {"file": "81-member.md", "isPhase": False,
              "phase": {"file": "80-ship.md", "number": "80"}}
    PHASE = {"file": "80-ship.md", "isPhase": True, "phase": None,
             "baseBranch": None, "prBase": None, "body": "# 80 — ship\n"}

    def setUp(self):
        for patch in (mock.patch.object(plans, "recorded", lambda f: None),
                      mock.patch.object(gates, "evaluate", lambda *a, **k: gate(
                          "allowed", checks=[("unit", "met", "passed", "e")])),
                      mock.patch.object(coordinate, "_merged",
                                        lambda op, result, outcome, words, **x:
                                        {"outcome": outcome})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_where_each_kind_lands(self):
        self.assertEqual("phase/80-ship", integration.target_for(self.MEMBER))
        self.assertEqual(config.APP_BASE, integration.target_for(self.PHASE))

    def test_a_member_that_does_not_hold_its_phase_branch_is_not_integrated(self):
        def stale(*a, **k):
            raise integration.Stale("target-changed", "phase/80-ship moved — "
                                    "nothing was merged", target="phase/80-ship")

        with mock.patch.object(integration, "require_current", stale), \
                mock.patch.object(phasescope, "integrate") as merged:
            with self.assertRaises(integration.Stale):
                coordinate._integrate({"key": "op-x", "file": "81-member.md"},
                                      self.MEMBER, A, {})
        merged.assert_not_called()

    def test_a_second_integration_into_the_phase_branch_waits(self):
        with mock.patch.object(integration, "require_current",
                               lambda *a, **k: {"applies": True}), \
                mock.patch.object(phasescope, "integrate") as merged:
            with integration.admission("phase/80-ship", "82-other.md"):
                with self.assertRaises(integration.Busy) as said:
                    coordinate._integrate({"key": "op-x",
                                           "file": "81-member.md"},
                                          self.MEMBER, A, {})
            self.assertEqual("admission-busy", said.exception.code)
            merged.assert_not_called()
            merged.return_value = {"whole": True, "integrated": A,
                                   "branch": "phase/80-ship"}
            done = coordinate._integrate({"key": "op-x",
                                          "file": "81-member.md"},
                                         self.MEMBER, A, {})
        self.assertEqual("integrated", done["outcome"])
        merged.assert_called_once()


class CompletionTakesTheExactHead(unittest.TestCase):
    def run_complete(self, tip: str, expect: str):
        merged = []
        moved = []
        patches = [
            mock.patch.object(completion, "recovery", lambda f: None),
            mock.patch.object(completion, "_merged_elsewhere", lambda *a, **k: None),
            mock.patch.object(changes, "branch_of", lambda f: "task/7-card"),
            mock.patch.object(changes, "woven", lambda f, s: {"isPhase": False}),
            mock.patch.object(changes, "remote", lambda: "origin"),
            mock.patch.object(changes, "forget_pr", lambda f: None),
            mock.patch.object(gates, "evaluate", lambda *a, **k: {
                "decision": "ungated", "head": {}}),
            mock.patch.object(completion, "_park_drive", lambda f: None),
            mock.patch.object(completion, "_branch_exists", lambda b: True),
            mock.patch.object(plans, "pr_base", lambda f, t=None: "trunk"),
            mock.patch.object(plans, "target_problem", lambda f, t: None),
            mock.patch.object(completion, "_assert_lands_in_the_base", lambda f, t: None),
            mock.patch.object(completion, "_tip", lambda ref: tip),
            mock.patch.object(config, "app_merge_on_origin", lambda: False),
            mock.patch.object(completion, "_merge_locally",
                              lambda f, b, *c: merged.append(c)),
            mock.patch.object(completion, "_remember", lambda *a, **k: None),
            mock.patch.object(completion, "_publish", lambda *a, **k: {
                "state": "published", "commit": expect, "target": "trunk",
                "remote": "origin"}),
            mock.patch.object(completion, "_clean_up", lambda *a, **k: ({}, None)),
            mock.patch.object(completion, "_sweep", lambda *a: ([], [])),
            mock.patch.object(completion, "move_task",
                              lambda f, s, t, actor="you": moved.append(actor)),
            mock.patch.object(completion, "_record_delivered", lambda *a, **k: None),
            mock.patch.object(completion, "forget_recovery", lambda f: None),
            mock.patch.object(state, "broadcast", lambda frame: None),
            mock.patch.object(state, "record_board_event", lambda e: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return completion._complete(CARD, "review", expect, "orchestrator"), \
            merged, moved

    def test_an_ungated_merge_is_pinned_to_the_expected_head(self):
        result, merged, moved = self.run_complete(A, A)
        self.assertEqual([(A,)], merged)
        self.assertEqual(["orchestrator"], moved)
        self.assertTrue(result["moved"])

    def test_a_branch_that_moved_refuses_with_nothing_merged(self):
        with self.assertRaisesRegex(ValueError, "nothing was merged"):
            self.run_complete(B, A)


if __name__ == "__main__":
    unittest.main()
