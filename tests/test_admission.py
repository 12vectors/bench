"""Review is not admission: coordinating Review cards against the current
target (task 189).

Every case runs on a disposable application repository, work root and
tool install (the `GateCase` fixture): real git, real worktrees, bench's
own check runner and real merges. No agent, model or hosting provider is
involved — a conflict's repair launch is a stand-in, and so is the PR's
URL.

- A and B both pass checks and review against an earlier main. A is
  admitted; B then reads *Target changed*, its merge is refused with
  nothing merged, and it is updated on its own branch against the main
  that holds A — never against a third card's unaccepted branch.
- A clean textual update whose combined behaviour breaks is caught by the
  check on the combined head; one that works still needs a fresh review
  of the changed head before it can be admitted.
- A textual conflict is aborted, recorded and offered as a repair; the
  branch is left exactly as it was.
- One admission per target at a time, different targets independent, the
  remote target read immediately before the merge, duplicate updates
  refused, an update left without a receipt reconciled without repeating.

    python3 -m unittest tests.test_admission -v
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_delivery_status import DeliveryCase, PR_URL  # noqa: E402
from test_integration_gates import git  # noqa: E402

import agents  # noqa: E402
import completion  # noqa: E402
import config  # noqa: E402
import coordinate  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import integration  # noqa: E402
import operations  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

A = "81-card-a.md"
B = "82-card-b.md"
C = "83-card-c.md"

# The project's check: each card alone passes; together they pass only if
# B calls the name A's change provides.
COMBINED = """#!{python}
import pathlib, sys
here = pathlib.Path.cwd()
a, b = here / "a.txt", here / "b.txt"
if a.exists() and b.exists() and "renamed" in a.read_text() \\
        and "old_name" in b.read_text():
    print("combined behaviour broken: b calls old_name, which a renamed")
    sys.exit(1)
print("ran 2 tests — OK")
"""


class AdmissionCase(DeliveryCase):

    def setUp(self):
        super().setUp()
        # the operation ledger lives under STATE: keep it in the sandbox
        self.patch(STATE=self.state)
        self.full_gate(body=COMBINED)

    # — furniture —

    def write(self, path: Path, name: str, text: str) -> str:
        (path / name).write_text(text, encoding="utf-8")
        git(path, "add", name)
        git(path, "commit", "-q", "-m", f"work: {name}")
        return git(path, "rev-parse", "HEAD").stdout.strip()

    def card_with(self, filename: str, name: str, text: str) -> str:
        wt = self.worktree(filename[:-3])
        return self.write(wt, name, text)

    def checked(self, filename: str, agent: str) -> dict:
        """The card's required check, run by bench at its head now."""
        wt = config.WORKTREES / filename[:-3]
        self.manifest(filename=filename, agent=agent, worktree=wt)
        found = self.ask(agent=agent, filename=filename)
        verify.forget_cached()
        gates.forget_cached()
        delivery.forget_cached()
        return found

    def reviewed(self, filename: str) -> str:
        head = git(config.WORKTREES / filename[:-3], "rev-parse",
                   "HEAD").stdout.strip()
        gates.record_verdict(filename, verdict="APPROVE", head=head)
        delivery.forget_cached()
        return head

    def review_stage(self, filename: str, title: str) -> None:
        self.place("review", filename=filename, title=title, pr=PR_URL)

    def word(self, filename: str) -> str:  # noqa: D401 — the card's word
        delivery.forget_cached()
        return delivery.for_card(filename)["status"]["word"]

    def main_tip(self) -> str:
        return git(self.repo, "rev-parse", "main").stdout.strip()

    def both_reviewed(self, b_text: str) -> tuple[str, str]:
        self.card_with(A, "a.txt", "renamed: new_name\n")
        self.card_with(B, "b.txt", b_text)
        self.review_stage(A, "81 — Card A")
        self.review_stage(B, "82 — Card B")
        for name, agent in ((A, "run-a"), (B, "run-b")):
            self.assertEqual(self.checked(name, agent)["outcome"], "passed")
        return self.reviewed(A), self.reviewed(B)


class TheABJourney(AdmissionCase):

    def test_b_is_not_ready_once_a_is_admitted_and_is_checked_combined(self):
        a_head, b_head = self.both_reviewed("calls old_name\n")
        # reviewed in parallel, each against the brief and the main it saw
        self.assertEqual(self.word(A), "ready-to-merge")
        self.assertEqual(self.word(B), "ready-to-merge")
        # a third card in review whose work is never accepted
        c_head = self.card_with(C, "c.txt", "unaccepted\n")
        self.review_stage(C, "83 — Card C")

        done = completion.complete_task(A, "review")
        self.assertTrue(done["moved"])
        admitted = self.main_tip()
        self.assertTrue(gates.contained(a_head, "main"))

        # B: requirements met at its old head, and not Ready
        found = delivery.for_card(B)
        self.assertEqual(found["status"]["word"], "target-changed")
        self.assertEqual(found["primary"]["action"], "update-target")
        self.assertEqual(found["integration"]["targetTip"], admitted)

        # its merge is refused with nothing merged
        with self.assertRaises(integration.Stale) as said:
            completion.complete_task(B, "review")
        self.assertIn("nothing was merged", str(said.exception).lower())
        self.assertEqual(said.exception.code, "target-changed")
        self.assertEqual(self.main_tip(), admitted)
        self.assertEqual(self.stage_of(B), "review")

        # update on its own branch: no agent, no third card's work
        with mock.patch.object(agents, "start_pr_fix") as launched:
            result = coordinate.update_against_target(B, {"head": b_head})
        launched.assert_not_called()
        self.assertEqual(result["outcome"], "merged")
        new = result["head"]
        self.assertTrue(gates.contained(admitted, new))
        self.assertFalse(gates.contained(c_head, new),
                         "an unaccepted card's work is never brought in")
        self.assertEqual(git(self.repo, "rev-parse",
                             f"task/{B[:-3]}").stdout.strip(), new)
        self.assertEqual(self.stage_of(B), "review", "the card stays in Review")

        # the old evidence is history; the combined head needs its own
        found = delivery.for_card(B)
        self.assertEqual(found["status"]["word"], "checks-needed")
        self.assertEqual(found["integration"]["state"],
                         "integration-check-needed")
        # textually clean, behaviourally broken: the combined check fails
        self.assertEqual(self.checked(B, "run-b2")["outcome"], "failed")
        found = delivery.for_card(B)
        self.assertEqual(found["status"]["word"], "check-failed")
        self.assertEqual(found["integration"]["state"],
                         "integration-check-failed")
        self.assertIn(admitted[:12], found["integration"]["sentence"])

    def test_a_working_combination_still_needs_a_fresh_review(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        result = coordinate.update_against_target(B, {"head": b_head})
        self.assertEqual(result["outcome"], "merged")
        self.assertEqual(self.checked(B, "run-b2")["outcome"], "passed")
        # the verdict was about the old head: exact-head rules ask again
        self.assertEqual(self.word(B), "review-needed")
        self.reviewed(B)
        found = delivery.for_card(B)
        self.assertEqual(found["status"]["word"], "ready-to-merge")
        self.assertEqual(found["integration"]["state"], "ready-to-admit")
        before = self.main_tip()
        self.assertTrue(completion.complete_task(B, "review")["moved"])
        self.assertTrue(gates.contained(before, "main"))
        self.assertTrue(gates.contained(result["head"], "main"))


class ATextualConflict(AdmissionCase):

    def test_a_conflict_is_aborted_recorded_and_handed_to_one_repair(self):
        self.card_with(A, "shared.txt", "A's version\n")
        b_head = self.card_with(B, "shared.txt", "B's version\n")
        self.review_stage(A, "81 — Card A")
        self.review_stage(B, "82 — Card B")
        for name, agent in ((A, "run-a"), (B, "run-b")):
            self.checked(name, agent)
            self.reviewed(name)
        completion.complete_task(A, "review")
        calls = []

        def repair(filename, stage, *, operation=None, integration=None):
            calls.append((filename, stage, integration))
            return {"id": "fix-pr-stand-in"}

        with mock.patch.object(agents, "start_pr_fix", repair):
            result = coordinate.update_against_target(B, {"head": b_head})
        self.assertEqual(result["outcome"], "conflict")
        self.assertEqual(result["conflicts"], ["shared.txt"])
        self.assertEqual(result["repair"], "fix-pr-stand-in")
        self.assertEqual(len(calls), 1)
        brief = calls[0][2]
        self.assertEqual(brief["target"], "main")
        self.assertEqual(brief["conflicts"], ["shared.txt"])
        self.assertTrue(brief["incoming"], "the accepted commits are named")
        text = agents.integration_brief(brief)
        self.assertIn("never rebase", text)
        self.assertIn("shared.txt", text)
        # the branch is exactly as it was, and the card says what is next
        self.assertEqual(git(self.repo, "rev-parse",
                             f"task/{B[:-3]}").stdout.strip(), b_head)
        found = delivery.for_card(B)
        self.assertEqual(found["status"]["word"], "target-conflicts")
        self.assertEqual(found["integration"]["attempt"]["outcome"], "conflict")

    def test_a_card_without_a_pr_is_told_to_open_one_first(self):
        self.card_with(A, "shared.txt", "A's version\n")
        b_head = self.card_with(B, "shared.txt", "B's version\n")
        self.review_stage(A, "81 — Card A")
        self.place("review", filename=B, title="82 — Card B")
        self.checked(A, "run-a")
        self.reviewed(A)
        completion.complete_task(A, "review")
        with mock.patch.object(agents, "start_pr_fix") as launched:
            result = coordinate.update_against_target(B, {"head": b_head})
        launched.assert_not_called()
        self.assertIn("↑ open PR", result["sentence"])


class GuardsAndDuplicates(AdmissionCase):

    def test_the_preview_reads_and_changes_nothing(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        refs = git(self.repo, "for-each-ref").stdout
        shown = coordinate.update_preview(B)
        self.assertEqual(shown["head"], b_head)
        self.assertEqual(shown["target"], "main")
        self.assertEqual(shown["targetTip"], self.main_tip())
        self.assertIsNone(shown["problem"])
        self.assertEqual(shown["integration"]["state"], "target-changed")
        self.assertTrue(shown["integration"]["incoming"])
        self.assertEqual(refs, git(self.repo, "for-each-ref").stdout)
        self.assertFalse(integration.attempts(B), "reading recorded nothing")

    def test_a_moved_head_is_refused_with_nothing_changed(self):
        self.card_with(B, "b.txt", "x\n")
        self.review_stage(B, "82 — Card B")
        with self.assertRaises(coordinate.UpdateRefused) as said:
            coordinate.update_against_target(B, {"head": "f" * 40})
        self.assertEqual(said.exception.code, "moved")

    def test_a_second_update_while_one_runs_is_refused(self):
        b_head = self.card_with(B, "b.txt", "x\n")
        self.review_stage(B, "82 — Card B")
        integration.UPDATING.add(B)
        self.addCleanup(integration.UPDATING.discard, B)
        with self.assertRaises(coordinate.UpdateRefused) as said:
            coordinate.update_against_target(B, {"head": b_head})
        self.assertEqual(said.exception.code, "busy")
        self.assertFalse(integration.attempts(B), "nothing was attempted")

    def test_an_up_to_date_card_is_not_touched(self):
        b_head = self.card_with(B, "b.txt", "x\n")
        self.review_stage(B, "82 — Card B")
        result = coordinate.update_against_target(B, {"head": b_head})
        self.assertEqual(result["outcome"], "current")
        self.assertEqual(git(self.repo, "rev-parse",
                             f"task/{B[:-3]}").stdout.strip(), b_head)

    def test_one_admission_per_target_and_targets_are_independent(self):
        with integration.admission("main", A):
            with self.assertRaises(integration.Busy):
                with integration.admission("main", B):
                    pass
            # another target goes ahead meanwhile
            with integration.admission("phase/80-ship", B) as held:
                self.assertEqual(held["target"], "phase/80-ship")
            self.assertEqual(integration.holder("main")["card"], A)
        self.assertIsNone(integration.holder("main"))
        with integration.admission("main", B):
            pass

    def test_a_concurrent_merge_into_the_same_target_merges_nothing(self):
        a_head, _ = self.both_reviewed("calls new_name\n")
        before = self.main_tip()
        inside = threading.Event()
        release = threading.Event()

        def hold():
            with integration.admission("main", "someone-else.md"):
                inside.set()
                release.wait(10)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(inside.wait(10))
        try:
            with self.assertRaises(integration.Busy):
                completion.complete_task(A, "review")
        finally:
            release.set()
            holder.join(10)
        self.assertEqual(self.main_tip(), before)
        self.assertEqual(self.stage_of(A), "review")
        self.assertFalse(gates.contained(a_head, "main"))

    def test_the_remote_target_is_read_immediately_before_the_merge(self):
        bare = self.origin()
        a_head, _ = self.both_reviewed("calls new_name\n")
        decision = gates.evaluate(A, "acceptance")
        found = integration.require_current(A, decision, a_head, "main")
        self.assertTrue(found["applies"])
        self.assertEqual(found["targetTip"], self.main_tip())
        # a replica (or the host) advances the remote's main
        other = self.tmp / "replica"
        git(self.tmp, "clone", "-q", str(bare), str(other))
        git(other, "config", "user.email", "r@r")
        git(other, "config", "user.name", "replica")
        self.write(other, "replica.txt", "landed elsewhere\n")
        git(other, "push", "-q", "origin", "main")
        with self.assertRaises(integration.Stale) as said:
            integration.require_current(A, decision, a_head, "main")
        self.assertEqual(said.exception.code, "target-moved")
        # and a remote that cannot be read is not read as current
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "gone"))
        with self.assertRaises(integration.Stale) as said:
            integration.require_current(A, decision, a_head, "main")
        self.assertEqual(said.exception.code, "target-unreadable")

    def test_the_boards_own_commits_on_a_shared_main_are_not_a_change(self):
        """Vendored and source-hosted: card moves are `board:` commits on
        the application's main. They are not work a candidate must be
        combined with, so they neither stale it nor refuse its merge; an
        application change beside them still does."""
        _, b_head = self.both_reviewed("calls new_name\n")
        with mock.patch.object(config, "app_shares_state_repository",
                               lambda: True), \
                mock.patch.object(config, "work_root", lambda: self.repo):
            (self.repo / "tasks").mkdir(exist_ok=True)
            self.write(self.repo, "tasks/9-moved.md", "# moved\n")
            found = delivery.for_card(B)
            self.assertEqual(found["status"]["word"], "ready-to-merge")
            self.assertTrue(found["integration"]["fresh"])
            decision = gates.evaluate(B, "acceptance")
            self.assertTrue(integration.require_current(
                B, decision, b_head, "main")["applies"])
            self.write(self.repo, "app.txt", "an application change\n")
            delivery.forget_cached()
            self.assertEqual(delivery.for_card(B)["status"]["word"],
                             "target-changed")

    def test_an_ungated_rule_requires_no_freshness(self):
        found = integration.require_current(
            A, {"decision": "ungated", "requirements": []}, "0" * 40, "main")
        self.assertFalse(found["applies"])


class ATargetOtherThanMain(AdmissionCase):
    """A card whose **Base branch:** names another branch is read against
    that branch, and that branch moving changes its reading."""

    def test_a_named_base_moving_reads_target_changed(self):
        git(self.repo, "branch", "side", "main")
        wt = self.worktree(B[:-3], base="side")
        head = self.write(wt, "b.txt", "calls new_name\n")
        self.place("review", filename=B, title="82 — Card B", pr=PR_URL,
                   base_branch="side")
        self.assertEqual(self.checked(B, "run-b")["outcome"], "passed")
        self.reviewed(B)
        found = delivery.for_card(B)
        self.assertEqual(found["integration"]["target"], "side")
        self.assertEqual(found["status"]["word"], "ready-to-merge")
        git(self.repo, "checkout", "-q", "side")
        self.write(self.repo, "side.txt", "accepted on side\n")
        git(self.repo, "checkout", "-q", "main")
        delivery.forget_cached()
        found = delivery.for_card(B)
        self.assertEqual(found["integration"]["state"], "target-changed",
                         found["integration"])
        self.assertEqual(found["status"]["word"], "target-changed")
        self.assertTrue(head)


class WhatTheCardSaysWhileSomethingRuns(AdmissionCase):
    """Review round 1, finding 1: a review or an ordinary repair running on
    the card is not an update against its target; only the update itself,
    or the repair run it handed a conflict to, reads *Updating*."""

    def running(self, filename: str, **record) -> None:
        agent = f"run-{len(state.AGENTS)}-{filename}"
        state.AGENTS[agent] = {"task": filename, "status": "running",
                               "mode": "review", "started": 0, **record}
        self.addCleanup(state.AGENTS.pop, agent, None)
        delivery.forget_cached()

    def test_a_review_run_on_a_stale_card_does_not_read_as_updating(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        self.running(B)
        found = delivery.for_card(B)
        self.assertEqual(found["integration"]["state"], "target-changed")
        shown = coordinate.update_preview(B)
        self.assertEqual(shown["integration"]["state"], "target-changed")
        self.assertIn("already working", shown["problem"],
                      "a run still refuses the update itself")

    def test_the_repair_an_update_handed_a_conflict_to_reads_updating(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        self.running(B, mode="work", integration={"target": "main"})
        self.assertEqual(delivery.for_card(B)["integration"]["state"],
                         "updating")
        self.assertEqual(coordinate.update_preview(B)["integration"]["state"],
                         "updating")


class APushTheRemoteRefused(AdmissionCase):
    """Review round 1, finding 6: a clean update whose push is refused is
    said (ok false), the card offers the update again, and that pushes the
    same head — plainly — rather than reading 'current'."""

    def test_the_refused_push_is_offered_again_and_then_published(self):
        bare = self.origin()
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        git(self.repo, "fetch", "-q", "origin")
        # the remote goes away between the read and the push
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "gone"))
        first = coordinate.update_against_target(B, {"head": b_head})
        self.assertEqual(first["outcome"], "merged")
        self.assertFalse(first["ok"])
        self.assertIs(first["pushed"], False)
        new = first["head"]
        self.assertTrue(integration.unpushed(B, new))
        self.checked(B, "run-b2")
        self.reviewed(B)
        found = delivery.for_card(B)
        self.assertTrue(found["integration"]["unpushed"])
        self.assertEqual(found["primary"]["action"], "update-target",
                         "pushing comes before the merge")
        self.assertIsNone(coordinate.update_preview(B)["problem"])
        # the remote is back: the same action publishes the same head
        git(self.repo, "remote", "set-url", "origin", str(bare))
        again = coordinate.update_against_target(B, {"head": new})
        self.assertTrue(again["ok"])
        self.assertEqual(again["outcome"], "pushed")
        self.assertEqual(git(bare, "rev-parse", f"task/{B[:-3]}").stdout.strip(),
                         new)
        self.assertFalse(integration.unpushed(B, new))
        delivery.forget_cached()
        self.assertEqual(delivery.for_card(B)["primary"]["action"], "complete")

    def test_with_no_remote_nothing_is_owed(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        done = coordinate.update_against_target(B, {"head": b_head})
        self.assertTrue(done["ok"])
        self.assertIsNone(done["pushed"])
        self.assertFalse(integration.unpushed(B, done["head"]))


class TheUpdateRoute(AdmissionCase):
    """Review round 1, finding 5: a person's update names the head it was
    shown; without one nothing is updated."""

    def call(self, body):
        replies = []

        class Req:
            def __init__(self):
                self.body = body

            def reply(self, code, payload):
                replies.append((code, payload))

        routes.post_card_integration_update(Req())
        return replies[0]

    def test_a_missing_or_blank_head_is_refused_with_nothing_changed(self):
        _a, b_head = self.both_reviewed("calls new_name\n")
        completion.complete_task(A, "review")
        for body in ({"file": B}, {"file": B, "head": ""},
                     {"file": B, "head": None}, {"file": B, "head": 7}):
            code, payload = self.call(body)
            self.assertEqual(code, 400)
            self.assertEqual(payload["code"], "head-required")
        self.assertFalse(integration.attempts(B), "nothing was attempted")
        self.assertEqual(git(self.repo, "rev-parse",
                             f"task/{B[:-3]}").stdout.strip(), b_head)
        code, payload = self.call({"file": B, "head": b_head})
        self.assertEqual(code, 200)
        self.assertEqual(payload["result"]["outcome"], "merged")


class OneSequencePerTarget(unittest.TestCase):
    """`coordinate._owners`: the final update → check → admission sequence
    is serialized per target; review and repair of others go on."""

    def item(self, card, number, code, target="main", **extra):
        return {"card": card, "number": number, "code": code,
                "state": coordinate.CODES[code],
                "integration": {"target": target}, **extra}

    def test_the_lowest_number_goes_first_and_others_wait_naming_it(self):
        items = [self.item("9-b.md", "9", "merge"),
                 self.item("4-a.md", "4", "refresh"),
                 self.item("5-c.md", "5", "review"),
                 self.item("6-d.md", "6", "merge", target="phase/80-x")]
        coordinate._owners(items, [], {})
        by = {i["card"]: i for i in items}
        self.assertEqual(by["4-a.md"]["code"], "refresh")
        self.assertEqual(by["9-b.md"]["code"], "admission-queued")
        self.assertEqual(by["9-b.md"]["waitingFor"], "4-a.md")
        self.assertNotIn("action", by["9-b.md"])
        self.assertEqual(by["5-c.md"]["code"], "review", "review is parallel")
        self.assertEqual(by["6-d.md"]["code"], "merge",
                         "another target is independent")

    def test_an_integrating_card_holds_its_target_while_it_verifies(self):
        items = [self.item("2-a.md", "2", "refresh"),
                 self.item("7-b.md", "7", "verify", integrating=True)]
        coordinate._owners(items, [], {})
        self.assertEqual(items[0]["code"], "admission-queued")
        self.assertEqual(items[0]["waitingFor"], "7-b.md")
        self.assertEqual(items[1]["code"], "verify")

    def test_a_blocked_integrating_card_releases_its_target(self):
        items = [self.item("2-a.md", "2", "refresh"),
                 self.item("7-b.md", "7", "repair-exhausted", integrating=True)]
        coordinate._owners(items, [], {})
        self.assertEqual(items[0]["code"], "refresh")

    def test_an_operation_in_flight_holds_the_target_across_a_restart(self):
        items = [self.item("2-a.md", "2", "merge")]
        ops = [{"key": "op-" + "1" * 20, "action": "merge", "file": "8-x.md",
                "status": "unknown"}]
        tasks = {"8-x.md": {"file": "8-x.md", "phase": None,
                            "isPhase": False}}
        with mock.patch.object(integration, "target_for",
                               lambda task, plan=None: "main"):
            coordinate._owners(items, ops, tasks)
        self.assertEqual(items[0]["code"], "admission-queued")
        self.assertIn("unknown", items[0]["reason"])


class ReconcilingAnUpdate(AdmissionCase):

    def test_an_intent_without_a_receipt_or_a_move_ran_nothing(self):
        b_head = self.card_with(B, "b.txt", "x\n")
        op = operations.begin(operations.key_for("refresh", B, b_head),
                              action="refresh", card=B, number="82",
                              scope="bench", pool="none", basis=b_head,
                              attempt=1)
        done = coordinate._reconcile_refresh(op, 0)
        self.assertEqual(done["status"], "abandoned")

    def test_an_intent_whose_attempt_was_recorded_takes_its_outcome(self):
        b_head = self.card_with(B, "b.txt", "x\n")
        op = operations.begin(operations.key_for("refresh", B, b_head),
                              action="refresh", card=B, number="82",
                              scope="bench", pool="none", basis=b_head,
                              attempt=1)
        integration.record_attempt(B, {"head": b_head, "target": "main",
                                       "operation": op["key"],
                                       "outcome": "merged",
                                       "newHead": "e" * 40})
        with mock.patch.object(coordinate, "_note"):
            coordinate._reconcile_refresh(op, 0)
        settled = operations.read(op["key"])
        self.assertEqual(settled["status"], "ended")
        self.assertEqual(settled["outcome"], "merged")


if __name__ == "__main__":
    unittest.main()
