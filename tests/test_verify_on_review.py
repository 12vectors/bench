"""Card 105 — opt-in verification when a card enters Review.

Design #94 §10.3: `BOARD_VERIFY_ON_REVIEW=1` makes a card a person moves
into review/ request what one **Run required checks** click would, on the
board whose user made the move. Off by default; off, a move into review
runs nothing.

Every case runs against the disposable tool install, work root and
application checkout `GateCase` builds, with stub checks that count their
own executions (`BatchCase`). No live card, board, remote or operator
policy is read or written; the option is switched by patching `config`
in-process, never by writing an operator's `.env`.

- Acceptance 1 (default/off runs nothing; configuration unchanged on
  update) — `OffByDefault`.
- Acceptance 2 (a local person's move requests the missing checks once;
  replicas, agents, hand moves, startup, restarts, replays and racing
  moves do not duplicate) — `ALocalPersonsMove`, `NeverTwice`.
- Acceptance 3 (Settings: provenance, what runs, how to turn it off; a
  policy apply leaves it and every other grant alone) — `InSettings`.
- Acceptance 4 (failed / cancelled / forbidden never retry, launch or
  merge anything; manual recovery stays) — `NoRetryNoFollowOn`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

from tests.test_integration_gates import CARD, wait_for  # noqa: E402
from tests.test_required_checks import BatchCase  # noqa: E402

import agents  # noqa: E402
import config  # noqa: E402
import gates  # noqa: E402
import github  # noqa: E402
import phases  # noqa: E402
import required  # noqa: E402
import settings  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import verify  # noqa: E402
import watch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class OnReviewCase(BatchCase):

    def setUp(self):
        super().setUp()
        required._ON_REVIEW_CLAIMED.clear()
        self.addCleanup(required._ON_REVIEW_CLAIMED.clear)
        # opening a PR is the other reaction to entering review; it is not
        # what these cases are about, and there is no remote to open it on
        opened = mock.patch.object(github, "open_pr_async")
        self.opened = opened.start()
        self.addCleanup(opened.stop)
        self.patch(VERIFY_ON_REVIEW=False)

    def on(self) -> None:
        self.patch(VERIFY_ON_REVIEW=True)

    def to_review(self) -> None:
        src = self.tasks / "in-progress" / CARD
        text = src.read_text(encoding="utf-8").replace(
            "**Status:** In Progress", "**Status:** Review")
        (self.tasks / "review" / CARD).write_text(text, encoding="utf-8")
        src.unlink()

    def back_to_progress(self) -> None:
        src = self.tasks / "review" / CARD
        (self.tasks / "in-progress" / CARD).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")
        src.unlink()

    def move(self, actor: str | None = "you", *, pulled_by: str | None = None):
        """A move into review as the watcher sees it: the signature before
        and after, attributed the way `watch._actor` attributes it."""
        before = watch._board_sig()
        self.to_review()
        after = watch._board_sig()
        if actor is not None:
            state.expect_move(CARD, "review", actor)
        with mock.patch.object(sync, "arrived_actor",
                               return_value=pulled_by):
            watch.narrate(before, after)
        required.join(60)
        self.fresh()

    def events(self) -> list[str]:
        return [e.get("summary", "") for e in state.BOARD_EVENTS
                if e.get("kind") == "verify"]


# ── acceptance 1 ───────────────────────────────────────────────────────


class OffByDefault(OnReviewCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("unit")], gate=["unit"])

    def test_the_documented_default_is_off(self):
        self.assertEqual(settings._default_for("BOARD_VERIFY_ON_REVIEW"), "0")
        with mock.patch.dict(config._ENV, {"BOARD_VERIFY_ON_REVIEW": "0"}):
            self.assertFalse(config.flag("BOARD_VERIFY_ON_REVIEW"))
        with mock.patch.dict(config._ENV, clear=True):
            self.assertFalse(config.flag("BOARD_VERIFY_ON_REVIEW"),
                             "an .env written before the key existed is off")

    def test_a_persons_move_into_review_runs_nothing(self):
        def evidence() -> dict:
            # the move's own narration (sessions/board.jsonl) is not ours
            return {k: v for k, v in self.state_files().items()
                    if k.startswith(("evidence/", "verify/", "gates/"))}

        before = evidence()
        with mock.patch.object(threading, "Thread",
                               side_effect=AssertionError("no thread")):
            self.move("you")
        time.sleep(0.2)
        self.assertEqual(self.runs("unit"), 0)
        self.assertIsNone(required.latest(CARD))
        self.assertEqual(self.events(), [])
        self.assertFalse(verify.RUNNING)
        self.assertEqual(evidence(), before)
        self.opened.assert_called_once_with(CARD)   # the PR, as always

    def test_off_says_so_and_requests_nothing(self):
        self.to_review()
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["outcome"], "off")
        self.assertEqual(self.runs("unit"), 0)

    def test_an_update_does_not_touch_the_operator_setting(self):
        # update.sh replaces manager/core/ and the manifest's top-level
        # files; manager/local/.env is in neither, so a value an operator
        # chose survives and a board that never set it stays off.
        manifest = (ROOT / "manager" / "core" / "release-manifest").read_text(
            encoding="utf-8")
        shipped = [line.split()[-1] for line in manifest.splitlines()
                   if line.strip() and not line.startswith("#")]
        self.assertFalse([p for p in shipped if p.endswith(".env")
                          or p == "manager/local"], shipped)
        update = (ROOT / "update.sh").read_text(encoding="utf-8")
        self.assertNotIn("VERIFY_ON_REVIEW", update)


# ── acceptance 2 ───────────────────────────────────────────────────────


class ALocalPersonsMove(OnReviewCase):

    def setUp(self):
        super().setUp()
        self.on()
        self.policy([self.check("met"), self.check("missing"),
                     self.check("other"), self.check("optional")],
                    gate=["met", "missing", "other"])
        self.person("met")

    def test_requests_exactly_the_missing_required_checks_once(self):
        self.move("you")
        self.assertEqual(self.runs("missing"), 1)
        self.assertEqual(self.runs("other"), 1)
        self.assertEqual(self.runs("met"), 1, "met was skipped")
        self.assertEqual(self.runs("optional"), 0, "optional never runs")
        batch = required.public(required.latest(CARD))
        self.assertEqual(batch["trigger"], "review-entry")
        self.assertEqual(batch["actor"], "you")
        self.assertEqual(batch["intent"], "work")
        self.assertEqual(batch["head"], self.head)
        self.assertEqual(batch["state"], "finished")
        record = verify.read(self.item(batch, "missing")["evidence"])
        self.assertIsNone(record["agent"])
        self.assertEqual(record["source"]["commit"], self.head)
        self.assertTrue(any("BOARD_VERIFY_ON_REVIEW" in s
                            for s in self.events()))
        self.assertEqual(required.entered(CARD), [self.head])

    def test_a_move_a_pull_brought_runs_nothing(self):
        self.move(None, pulled_by="elena")
        self.assertEqual(self.runs("missing"), 0)
        self.assertIsNone(required.latest(CARD))

    def test_an_agents_or_a_phases_move_runs_nothing(self):
        for actor in ("agent", "phase"):
            with self.subTest(actor=actor):
                self.move(actor)
                self.assertEqual(self.runs("missing"), 0)
                self.assertIsNone(required.latest(CARD))
                self.back_to_progress()

    def test_a_hand_move_on_disk_runs_nothing(self):
        self.move(None)                     # unclaimed: "disk"
        self.assertEqual(self.runs("missing"), 0)
        self.assertIsNone(required.latest(CARD))

    def test_a_board_starting_up_runs_nothing(self):
        # a card already in review is the watcher's baseline, never a move
        self.to_review()
        first = watch._board_sig()
        watch.narrate(first, first)
        required.join(10)
        self.assertEqual(self.runs("missing"), 0)
        self.assertIsNone(required.latest(CARD))

    def test_a_new_commit_is_a_new_question(self):
        self.move("you")
        self.assertEqual(self.runs("missing"), 1)
        self.back_to_progress()
        self.head = self.commit_in(self.wt, "a later line\n")
        self.move("you")
        self.assertEqual(self.runs("missing"), 2)
        self.assertEqual(self.runs("met"), 2, "met at the old commit only")
        self.assertEqual(required.entered(CARD)[-1], self.head)


class NeverTwice(OnReviewCase):

    def setUp(self):
        super().setUp()
        self.on()
        self.policy([self.check("unit")], gate=["unit"])
        self.to_review()

    def test_a_replayed_notification_runs_once(self):
        first = required.on_review(CARD, actor="you", wait=True)
        self.fresh()
        again = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(first["outcome"], "started")
        self.assertEqual(again["outcome"], "refused")
        self.assertEqual(again["code"], "nothing-needed")
        self.assertEqual(self.runs("unit"), 1)

    def test_a_restart_remembers_the_commit(self):
        # cancelled reads `needed` again, so only the record of the entry
        # stops a restarted board from asking a second time
        self.flag("unit", "hang")
        required.on_review(CARD, actor="you")
        self.assertTrue(wait_for(lambda: self.runs("unit") == 1, 20))
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        self.fresh()
        self.assertEqual(required.plan(CARD, "review")["requestable"], 1)
        required._ON_REVIEW_CLAIMED.clear()      # a new process
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["outcome"], "already")
        self.assertEqual(self.runs("unit"), 1)

    def test_the_watchers_path_records_the_commit_before_anything_runs(self):
        # The watcher calls on_review with wait=True, which returns only
        # once the checks end. A board stopped mid-batch must already have
        # the commit on record, or its restart asks a second time.
        self.back_to_progress()
        self.flag("unit", "hang")
        before = watch._board_sig()
        self.to_review()
        state.expect_move(CARD, "review", "you")
        with mock.patch.object(sync, "arrived_actor", return_value=None):
            watch.narrate(before, watch._board_sig())
        self.assertTrue(wait_for(lambda: self.runs("unit") == 1, 20))
        self.assertEqual(required.entered(CARD), [self.head],
                         "on record while the first check is still running")
        required._ON_REVIEW_CLAIMED.clear()      # a new process, mid-batch
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertNotIn(found["outcome"], ("started", "joined"), found)
        # the batch ends without a result: cancelled reads `needed` again,
        # so only the record written before it ran stops a second ask
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        required.join(30)
        self.fresh()
        required._ON_REVIEW_CLAIMED.clear()      # and again, after it ended
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["outcome"], "already")
        self.assertEqual(self.runs("unit"), 1)

    def test_an_interrupted_entry_asks_less_never_twice(self):
        # Whether anything started is unknown, so the claim stands.
        with mock.patch.object(required, "start",
                               side_effect=RuntimeError("board stopped")):
            with self.assertRaises(RuntimeError):
                required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(required.entered(CARD), [self.head])
        required._ON_REVIEW_CLAIMED.clear()
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["outcome"], "already")
        self.assertEqual(self.runs("unit"), 0)
        # the manual recovery is untouched
        self.assertEqual(required.plan(CARD, "review")["requestable"], 1)

    def test_a_record_that_cannot_be_written_requests_nothing(self):
        with mock.patch.object(required, "_mark_entered",
                               side_effect=OSError("read-only")):
            found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["code"], "unrecorded")
        self.assertEqual(self.runs("unit"), 0)
        self.assertIsNone(required.latest(CARD))
        self.assertFalse(required._ON_REVIEW_CLAIMED)

    def test_a_refusal_gives_the_claim_back(self):
        with mock.patch.object(required, "start", side_effect=required.Refused(
                "head-moved")):
            found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["outcome"], "refused")
        self.assertEqual(required.entered(CARD), [])
        self.assertFalse(required._ON_REVIEW_CLAIMED)

    def test_a_batch_at_another_commit_is_not_this_commits_answer(self):
        self.flag("unit", "hang")
        old = self.head
        self.start("review")                     # a person's click at `old`
        self.assertTrue(wait_for(lambda: self.runs("unit") == 1, 20))
        self.head = self.commit_in(self.wt, "a later line\n")
        self.fresh()
        found = required.on_review(CARD, actor="you", wait=True)
        self.assertEqual(found["code"], "other-batch", found)
        self.assertIn(old[:12], found["why"])
        self.assertEqual(required.entered(CARD), [],
                         "the new commit was not asked for")
        self.assertFalse(required._ON_REVIEW_CLAIMED)
        self.assertEqual(self.runs("unit"), 1)
        required.cancel(CARD)

    def test_racing_moves_start_one_batch(self):
        self.flag("unit", "hang")
        results = []
        go = threading.Barrier(4)

        def enter():
            go.wait()
            results.append(required.on_review(CARD, actor="you"))

        threads = [threading.Thread(target=enter) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertTrue(wait_for(lambda: self.runs("unit") >= 1, 20))
        time.sleep(0.3)
        self.assertEqual(self.runs("unit"), 1)
        outcomes = sorted(r["outcome"] for r in results)
        self.assertEqual(outcomes.count("started"), 1, outcomes)
        required.cancel(CARD)


# ── acceptance 3 ───────────────────────────────────────────────────────


class InSettings(OnReviewCase):

    def setUp(self):
        super().setUp()
        self.policy([self.check("unit")], gate=["unit"])

    def test_off_with_its_provenance_what_it_runs_and_how_to_disable(self):
        found = settings.acceptance()["onReview"]
        self.assertFalse(found["enabled"])
        self.assertEqual(found["key"], "BOARD_VERIFY_ON_REVIEW")
        self.assertEqual(found["provenance"]["source"], "default")
        self.assertEqual([c["name"] for c in found["checks"]], ["unit"])
        self.assertTrue(found["checks"][0]["run"].endswith("unit"))
        self.assertIn("Off (the default)", found["what"])
        self.assertIn("restart", found["disable"])
        self.assertIn("=0", found["disable"])
        self.assertIn("moves no card", found["cost"])
        self.assertIn("launches no agent", found["never"])

    def test_each_check_names_the_rules_that_require_it(self):
        self.declares({"v": 1,
                       "checks": [self.check("unit"), self.check("phase")],
                       "profiles": {"work": {"verify": ["unit", "phase"]}},
                       "gates": {"acceptance": {"checks": ["unit"]},
                                 "integration": {"checks": ["unit", "phase"]}}})
        found = {c["name"]: c["scopes"]
                 for c in settings.acceptance()["onReview"]["checks"]}
        self.assertEqual(sorted(found["unit"]), ["acceptance", "integration"])
        self.assertEqual(found["phase"], ["integration"])
        self.assertIn("card's own merge rule",
                      settings.acceptance()["onReview"]["runs"])

    def test_a_local_file_value_is_reported_from_where_it_came(self):
        (self.local / ".env").write_text("BOARD_VERIFY_ON_REVIEW=1\n",
                                         encoding="utf-8")
        row = settings.acceptance()["onReview"]["provenance"]
        self.assertEqual(row["source"], "local-file")
        self.assertEqual(row["appliesAt"], "restart")
        self.assertFalse(row["editableHere"])
        # the running board was started off: the file has moved since
        self.assertTrue(row["movedSinceStart"])
        self.assertFalse(settings.acceptance()["onReview"]["enabled"])

    def test_catalogued_in_installation(self):
        keys = [row["key"] for row in settings.catalog()]
        self.assertIn("BOARD_VERIFY_ON_REVIEW", keys)

    def test_a_policy_apply_leaves_the_setting_and_other_grants_alone(self):
        env = "BOARD_VERIFY_ON_REVIEW=1\nBOARD_AGENT_COMMANDS=make test\n"
        (self.local / ".env").write_text(env, encoding="utf-8")
        draft = settings.draft()
        before = {i: settings.access()["intents"][i].get("grants")
                  for i in ("work", "act-pr", "review")}
        draft["document"]["checks"][0]["timeout"] = 60
        checked = settings.validate({"document": draft["document"]})
        self.assertTrue(checked["ok"], checked.get("errors"))
        settings.preview({"document": draft["document"]})
        settings.apply({"document": draft["document"],
                        "revision": draft["revision"]})
        self.assertEqual((self.local / ".env").read_text(encoding="utf-8"),
                         env)
        after = {i: settings.access()["intents"][i].get("grants")
                 for i in ("work", "act-pr", "review")}
        self.assertEqual(before, after)

    def test_the_page_draws_the_row(self):
        page = (ROOT / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")
        self.assertIn("onReviewLine(a.onReview, states)", page)
        self.assertIn("Turn it off:", page)


# ── acceptance 4 ───────────────────────────────────────────────────────


class NoRetryNoFollowOn(OnReviewCase):

    def setUp(self):
        super().setUp()
        self.on()

    def guarded(self):
        """Nothing an entry does may launch an agent, review or merge."""
        targets = [(agents, name) for name in (
            "_launch", "start_agent", "start_review", "start_pr_review",
            "start_pr_fix")]
        targets += [(phases, "_merge_member"), (github, "_merge_locally")]
        for module, name in targets:
            patcher = mock.patch.object(module, name, side_effect=AssertionError(
                f"{module.__name__}.{name} must not be called"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_failure_is_not_asked_again_by_a_second_entry(self):
        self.policy([self.check("unit", "fail")], gate=["unit"])
        self.guarded()
        self.move("you")
        batch = required.public(required.latest(CARD))
        self.assertEqual(self.item(batch, "unit")["state"], "failed")
        self.back_to_progress()
        self.move("you")
        self.assertEqual(self.runs("unit"), 1)
        # a failure is a result to read, not a requirement to ask for
        self.assertTrue(any("nothing was requested" in s
                            for s in self.events()), self.events())
        self.assertFalse(state.AGENTS, "no agent was launched")
        decision = gates.evaluate(CARD, "acceptance")
        self.assertEqual(decision["decision"], "blocked",
                         "the gate is not weakened")
        # the explicit recovery is still a person's click
        self.flag("unit", "pass")
        plan = required.plan(CARD, "review")
        self.assertEqual([i["state"] for i in plan["items"]], ["problem"])
        self.person("unit")
        self.assertEqual(self.runs("unit"), 2)

    def test_a_cancelled_batch_is_not_asked_again(self):
        self.policy([self.check("unit", "hang")], gate=["unit"])
        self.guarded()
        self.to_review()
        found = required.on_review(CARD, actor="you")
        self.assertEqual(found["outcome"], "started")
        self.assertTrue(wait_for(lambda: self.runs("unit") == 1, 20))
        required.cancel(CARD)
        self.assertTrue(wait_for(lambda: CARD not in required.ACTIVE, 20))
        self.fresh()
        self.assertEqual(required.latest(CARD)["stop"], "cancelled")
        self.back_to_progress()
        self.move("you")
        self.assertEqual(self.runs("unit"), 1)
        self.assertEqual(required.plan(CARD, "review")["items"][0]["state"],
                         "request", "Run required checks is the recovery")

    def test_a_forbidden_check_is_named_with_its_remedy_and_never_run(self):
        self.policy([self.check("unit"), self.check("forbidden")],
                    gate=["unit", "forbidden"], work=["unit"])
        self.guarded()
        self.move("you")
        self.assertEqual(self.runs("unit"), 1)
        self.assertEqual(self.runs("forbidden"), 0)
        said = [s for s in self.events() if "'forbidden'" in s]
        self.assertTrue(said and '"verify" list' in said[0], self.events())
        self.assertTrue(any("does not widen" in s for s in said))

    def test_a_dirty_checkout_says_why_and_runs_nothing(self):
        self.policy([self.check("unit")], gate=["unit"])
        (self.wt / "code.txt").write_text("uncommitted\n", encoding="utf-8")
        self.move("you")
        self.assertEqual(self.runs("unit"), 0)
        self.assertTrue(any("Commit, then run checks" in s
                            for s in self.events()))
        self.assertEqual(required.entered(CARD), [],
                         "nothing was asked, so nothing is remembered")

    def test_an_ungated_board_is_quiet(self):
        self.declares({"v": 1, "checks": [self.check("unit")],
                       "profiles": {"work": {"verify": ["unit"]}}})
        self.move("you")
        self.assertEqual(self.runs("unit"), 0)
        self.assertEqual(self.events(), [])
