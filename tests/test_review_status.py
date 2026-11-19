"""The review status a Review card wears on its face (task 152).

`delivery.review_status` answers one question — has the work as it stands
now been approved by Bench's own PR review? — from the records the review
rows and the merge gate already read: Bench's durable verdict bound to the
exact commit, the review runs in the registry, and the candidate commit.
Every case runs on a disposable install (`DeliveryCase`): real git, real
verdict records, nothing reaching GitHub, no live card touched.

What is held here:

- the seven words, each from a fixture of the real state it names;
- an approval about an older commit, or given before a retarget, is
  *outdated*, and a fresh approval at the new head restores it;
- contradictory legacy fields — a verdict parsed out of the card's text,
  GitHub's formal approval, a review checkout's label — never read as
  Bench's approval;
- an approval sits beside a failed check without implying merge
  readiness, and the delivery status still names the blocker;
- reading it writes nothing.
"""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest import mock

from tests import test_delivery_surfaces as surfaces  # noqa: E402
from tests.test_delivery_status import PR_URL, DeliveryCase  # noqa: E402
from tests.test_integration_gates import CARD, FAILS, STEM  # noqa: E402

import config  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

ROUND = """

---

## PR review at {commit} — 2026-11-01 10:00 (Wren)

Looks good.

PR REVIEW: APPROVE
"""


class ReviewStatusCase(DeliveryCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(self._forget_agents)

    def _forget_agents(self):
        with state.LOCK:
            for key in [k for k in state.AGENTS if k.startswith("rv-")]:
                state.AGENTS.pop(key, None)

    def status(self) -> dict:
        delivery.forget_cached()
        return self.read()["review"]

    def word(self) -> str:  # noqa: D102 — the review word, not delivery's
        return self.status()["word"]

    def review_run(self, ident: str, *, status: str, head: str | None,
                   started: float | None = None, failure: dict | None = None):
        with state.LOCK:
            state.AGENTS[ident] = {
                "id": ident, "task": CARD, "status": status, "name": "Wren",
                "mode": "review", "started": started or time.time(),
                "review": {"head": head, "label": "current"},
                "failure": failure}

    def head(self) -> str:
        wt = self.worktree()
        return self.commit_in(wt)


class EachWordFromItsOwnState(ReviewStatusCase):

    def test_not_yet_reviewed_is_pending(self):
        self.head()
        self.place("review", pr=PR_URL)
        found = self.status()
        self.assertEqual(found["word"], "review-pending")
        self.assertEqual(found["label"], "Review pending")

    def test_current_approval_says_review_approved(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        found = self.status()
        self.assertEqual(found["word"], "review-approved")
        self.assertEqual(found["label"], "Review approved")
        self.assertEqual(found["head"], head)
        self.assertIn("not GitHub approval", found["sentence"])
        self.assertIn("permission to merge", found["sentence"])

    def test_current_request_for_changes(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=head)
        self.place("review", pr=PR_URL)
        found = self.status()
        self.assertEqual(found["word"], "review-changes")
        self.assertEqual(found["label"], "Changes requested")

    def test_a_running_review_is_in_progress_not_approved(self):
        head = self.head()
        self.place("review", pr=PR_URL)
        self.review_run("rv-live", status="running", head=head)
        found = self.status()
        self.assertEqual(found["word"], "review-running")
        self.assertEqual(found["label"], "Review in progress")
        self.assertIn("Nothing is approved", found["sentence"])

    def test_a_relevance_check_is_not_a_pr_review(self):
        self.head()
        self.place("review", pr=PR_URL)
        with state.LOCK:
            state.AGENTS["rv-relevance"] = {
                "id": "rv-relevance", "task": CARD, "status": "running",
                "mode": "review", "started": time.time(), "name": "Juno"}
        self.assertEqual(self.word(), "review-pending")

    def test_a_review_that_ended_without_a_verdict_is_incomplete(self):
        head = self.head()
        self.place("review", pr=PR_URL)
        self.review_run("rv-dead", status="failed", head=head,
                        failure={"rc": 1, "excerpt": "API Error: 500"})
        found = self.status()
        self.assertEqual(found["word"], "review-incomplete")
        self.assertEqual(found["label"], "Review incomplete")

    def test_an_incomplete_rerun_hides_an_older_approval_too(self):
        older = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=older)
        newer = self.commit_in(config.WORKTREES / STEM, "a later push\n")
        self.place("review", pr=PR_URL)
        self.review_run("rv-dead", status="done", head=newer,
                        started=time.time() + 1)
        self.assertEqual(self.word(), "review-incomplete")

    def test_no_commit_is_unavailable_never_approved(self):
        gates.record_verdict(CARD, verdict="APPROVE", head="a" * 40)
        self.place("review", pr=PR_URL)
        found = self.status()
        self.assertEqual(found["word"], "review-unavailable")
        self.assertEqual(found["label"], "Review status unavailable")
        self.assertIsNone(found["commit"])


class ApplicabilityFollowsTheHead(ReviewStatusCase):

    def test_a_new_commit_outdates_the_approval_and_a_fresh_one_restores_it(self):
        older = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=older)
        self.place("review", pr=PR_URL)
        self.assertEqual(self.word(), "review-approved")

        newer = self.commit_in(config.WORKTREES / STEM, "a later push\n")
        found = self.status()
        self.assertEqual(found["word"], "review-outdated")
        self.assertEqual(found["label"], "Review outdated")
        self.assertEqual(found["head"], older)
        self.assertIn(older[:12], found["sentence"])
        self.assertIn("history", found["sentence"])

        gates.record_verdict(CARD, verdict="APPROVE", head=newer)
        self.assertEqual(self.word(), "review-approved")

    def test_an_older_approve_retained_in_history_is_not_current(self):
        older = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=older)
        middle = self.commit_in(config.WORKTREES / STEM, "second\n")
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=middle)
        self.commit_in(config.WORKTREES / STEM, "third\n")
        self.place("review", pr=PR_URL)
        # the record still holds the APPROVE, and it is not today's answer
        self.assertIn("APPROVE", [v["verdict"] for v in gates.verdicts_for(CARD)])
        self.assertEqual(self.word(), "review-outdated")

    def test_a_retarget_outdates_an_approval_at_the_same_head(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        self.assertEqual(self.word(), "review-approved")
        later = {"at": time.time() + 5, "from": "task/70-parent", "to": "main"}
        with mock.patch.object(gates, "retargeted", return_value=later):
            found = self.status()
        self.assertEqual(found["word"], "review-outdated")
        self.assertIn("target changed", found["sentence"])


class LegacyFieldsNeverReadAsApproval(ReviewStatusCase):

    def test_a_verdict_in_the_card_text_alone_is_unavailable(self):
        head = self.head()
        self.place("review", pr=PR_URL)
        path = self.tasks / "review" / CARD
        path.write_text(path.read_text(encoding="utf-8")
                        + ROUND.format(commit=head[:12]), encoding="utf-8")
        found = self.status()
        self.assertEqual(found["word"], "review-unavailable")
        self.assertIn("no recorded verdict", found["sentence"])
        # the detail's own records say the same thing apart
        self.assertIsNone(self.read()["reviews"]["bench"])

    def test_a_text_verdict_about_an_older_commit_is_outdated(self):
        older = self.head()
        self.place("review", pr=PR_URL)
        path = self.tasks / "review" / CARD
        path.write_text(path.read_text(encoding="utf-8")
                        + ROUND.format(commit=older[:12]), encoding="utf-8")
        self.commit_in(config.WORKTREES / STEM, "a later push\n")
        self.assertEqual(self.word(), "review-outdated")

    def test_github_approval_is_not_bench_approval(self):
        head = self.head()
        self.place("review", pr=PR_URL)
        self.snapshot(head=head, prState="OPEN", review="approved")
        self.assertEqual(self.word(), "review-pending")

    def test_text_approve_beside_a_bench_request_for_changes(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=head)
        self.place("review", pr=PR_URL)
        path = self.tasks / "review" / CARD
        path.write_text(path.read_text(encoding="utf-8")
                        + ROUND.format(commit=head[:12]), encoding="utf-8")
        self.assertEqual(self.word(), "review-changes")


class ApprovalIsNotMergeReadiness(ReviewStatusCase):

    def test_an_approval_beside_a_failed_check(self):
        head = self.passed_at_head(body=FAILS)
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        found = self.read()
        self.assertEqual(found["review"]["word"], "review-approved")
        self.assertEqual(found["status"]["word"], "check-failed")
        self.assertLess(found["counts"]["met"], found["counts"]["required"])

    def test_the_compact_board_reading_carries_it(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        delivery.forget_cached()
        board = delivery.board(taskfiles.collect())
        self.assertEqual(board[CARD]["review"]["word"], "review-approved")
        self.assertEqual(board[CARD]["review"]["label"], "Review approved")

    def test_reading_writes_nothing(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        before = {p: p.read_bytes() for p in self.local.rglob("*") if p.is_file()}
        tasks = {p: p.read_bytes() for p in self.tasks.rglob("*") if p.is_file()}
        self.status()
        delivery.board(taskfiles.collect())
        self.assertEqual(before, {p: p.read_bytes()
                                  for p in self.local.rglob("*") if p.is_file()})
        self.assertEqual(tasks, {p: p.read_bytes()
                                 for p in self.tasks.rglob("*") if p.is_file()})


FACE = "\n".join([
    "const S = { state: {} };", surfaces.const("esc"),
    surfaces.const("REVIEW_GLYPH"), surfaces.lift("reviewLine"),
    "const deliveryOf = (t) => (S.state.delivery || {})[t.file] || null;",
    surfaces.lift("reviewNowHtml"),
])


@unittest.skipUnless(surfaces.NODE, "node not available")
class TheFaceAndTheReviewsTabDrawTheServersAnswer(ReviewStatusCase):
    """Every surface draws the one reading: the face's line and the Reviews
    tab's head, from the page's own functions, over the compact reading
    `/api/state` carries on disposable fixtures."""

    def draw(self, stage: str = "review") -> dict:
        delivery.forget_cached()
        compact = delivery.board(taskfiles.collect())[CARD]
        task = {"file": CARD, "stage": stage}
        script = (FACE + f"\nS.state.delivery = {{{json.dumps(CARD)}: "
                  f"{json.dumps(compact)}}};"
                  f"\nconst t = {json.dumps(task)};"
                  f"\nconsole.log(JSON.stringify({{face: reviewLine(t, "
                  f"deliveryOf(t)), tab: reviewNowHtml(t)}}));")
        out = subprocess.run([surfaces.NODE, "-e", script],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_approved_says_review_approved_on_the_face_and_in_reviews(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        drawn = self.draw()
        self.assertIn('<span class="rvl">Review approved</span>', drawn["face"])
        self.assertIn('data-section="reviews"', drawn["face"])
        self.assertIn("<button", drawn["face"], "a keyboard stop, not a span")
        self.assertIn(head[:7], drawn["face"])
        self.assertIn("<b>Review approved</b>", drawn["tab"])

    def test_every_word_reaches_the_face_in_words(self):
        head = self.head()
        self.place("review", pr=PR_URL)
        self.assertIn("Review pending", self.draw()["face"])
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=head)
        self.assertIn("Changes requested", self.draw()["face"])
        self.commit_in(config.WORKTREES / STEM, "a later push\n")
        drawn = self.draw()
        self.assertIn("Review outdated", drawn["face"])
        self.assertNotIn("Review approved", drawn["face"])

    def test_only_review_cards_wear_it(self):
        head = self.head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("in-progress")
        self.assertEqual(self.draw("in-progress")["face"], "")

    def test_the_text_is_escaped(self):
        script = (FACE + "\nconsole.log(reviewLine({stage: 'review'}, {review: "
                  "{word: 'review-pending', label: '<b>x</b>', tone: 'neutral', "
                  "sentence: '<script>'}}));")
        out = subprocess.run([surfaces.NODE, "-e", script],
                             capture_output=True, text=True)
        self.assertNotIn("<script>", out.stdout)
        self.assertNotIn("<b>x</b>", out.stdout)


class TheVocabularyIsClosed(unittest.TestCase):

    def test_every_word_has_a_label_and_a_known_tone(self):
        tones = {"calm", "alarm", "working", "neutral"}
        for word, (label, tone) in delivery.REVIEW_WORDS.items():
            self.assertTrue(word.startswith("review-"))
            self.assertTrue(label)
            self.assertIn(tone, tones)
        self.assertEqual(delivery.REVIEW_WORDS["review-approved"][0],
                         "Review approved")
        # approval is the only calm word
        self.assertEqual([w for w, (_, t) in delivery.REVIEW_WORDS.items()
                          if t == "calm"], ["review-approved"])


if __name__ == "__main__":
    unittest.main()
