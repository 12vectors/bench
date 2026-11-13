"""Relevance reviews in Reviews, with comments and refinement (task 84).

A relevance review judges whether a card's brief is still needed and
current; a PR review judges code at a revision. Both are filed into the
card's history by agents.py, and until now only the second was read as a
review at all — a relevance review was reachable only as a long report in
Activity. These tests hold the server half of the fix:

- each relevance review is read off the card with a stable identity, its
  reviewer and time, and no invented revision; legacy headings are read
  as written; and none of it feeds a PR verdict, an acceptance count or a
  merge requirement;
- a comment can be saved against one review, through the existing
  discussion store, and starts nothing;
- "Update card from this review" sends that review and the person's
  instruction to the existing refinement job, whose proposal is applied or
  discarded through the existing contract — attributed to the review;
- a review id from another card, a comment about a different review, a
  repeated request and a changed card are each refused or answered safely.

The refinement runs against the fake adapter of `test_card_refinement`:
no model, no network, never the live board.

    python3 -m unittest tests.test_relevance_reviews -v
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import acceptance  # noqa: E402
import httpd  # noqa: E402
import refine  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

from tests import test_card_detail_ui as detail_ui  # noqa: E402
from tests.test_card_refinement import FILENAME, _Board, git, proposal  # noqa: E402

FIXTURE = (REPO / "tests" / "fixtures" / "relevance" / "card-79.md").read_text(
    encoding="utf-8")

HEAD = "# 90 — Reviews\n\n**Status:** Review\n**Priority:** Medium\n\n## Acceptance\n\n" \
       "- [ ] One <!-- ac:one -->\n"
PR_ROUND = ("## PR review at 0123456789ab — 2026-11-08 15:20 (Juno)\n\n"
            "Looks fine.\n\nPR REVIEW: APPROVE\n")
RELEVANCE = ("## Relevance review — 2026-11-09 09:00 (Hazel)\n\n"
             "RELEVANCE REVIEW: Needs rewrite\n\nThe brief describes a removed API.\n")
RELEVANCE_2 = ("## Relevance review — 2026-11-10 11:30 (Wren)\n\n"
               "RELEVANCE REVIEW: Still relevant\n\nRewritten since; holds now.\n")
LEGACY = ("## Relevance review — 2026-10-01\n\n"
          "Still relevant, as far as I can tell.\n")


def card(*history: str) -> str:
    return HEAD + "".join("\n---\n\n" + h for h in history)


class Reading(unittest.TestCase):
    """What a card's text says about its reviews — pure, writes nothing."""

    def test_card_79_shows_hazels_review_as_a_relevance_review(self):
        found = acceptance.read_text(FIXTURE, "79-x.md")
        self.assertEqual(found["rounds"], [], "no PR review was filed")
        [review] = found["relevance"]
        self.assertEqual(review["reviewer"], "Hazel")
        self.assertEqual(review["at"], "2026-11-10 14:22")
        self.assertEqual(review["verdict"], "Partly done")
        self.assertIsNone(review["briefRevision"], "an unrecorded revision stays unknown")
        self.assertFalse(review["legacy"])
        self.assertTrue(review["id"].startswith("rl-"))
        self.assertIn("Recommendation: update the task", review["report"])
        self.assertIn("<script>", review["report"], "the text is the card's, byte for byte")
        line = "## Relevance review — 2026-11-10 14:22 (Hazel)"
        self.assertEqual(review["id"],
                         "rl-" + hashlib.sha256(line.encode()).hexdigest()[:10],
                         "the identity is the heading line, so every board agrees")

    def test_activity_points_at_the_exact_review(self):
        [report] = [r for r in acceptance.reports(FIXTURE)]
        [review] = acceptance.relevance_reviews(FIXTURE)
        self.assertEqual(report["review"], review["id"])
        self.assertEqual(report["reviewType"], "relevance")

    def test_pr_only(self):
        found = acceptance.read_text(card(PR_ROUND), "90-x.md")
        self.assertEqual(found["relevance"], [])
        self.assertEqual(len(found["rounds"]), 1)
        [report] = acceptance.reports(card(PR_ROUND))
        self.assertEqual((report["review"], report["reviewType"]),
                         (found["rounds"][0]["id"], "pr"))

    def test_mixed_and_multiple_rounds_keep_their_kinds_and_order(self):
        text = card(RELEVANCE, PR_ROUND, RELEVANCE_2,
                    "## Work report — 2026-11-11 10:00 (Basil)\n\nDid it.\n")
        found = acceptance.read_text(text, "90-x.md")
        self.assertEqual([r["reviewer"] for r in found["relevance"]], ["Hazel", "Wren"])
        self.assertEqual([r["verdict"] for r in found["relevance"]],
                         ["Needs rewrite", "Still relevant"])
        self.assertEqual(len(found["rounds"]), 1)
        self.assertEqual(len({r["id"] for r in found["relevance"]}), 2)
        kinds = [r.get("reviewType") for r in acceptance.reports(text)]
        self.assertEqual(kinds, ["relevance", "pr", "relevance", None])

    def test_a_legacy_heading_is_read_as_written(self):
        [review] = acceptance.relevance_reviews(card(LEGACY))
        self.assertTrue(review["legacy"])
        self.assertEqual(review["at"], "2026-10-01", "no time is invented")
        self.assertEqual(review["reviewer"], "", "no reviewer is invented")
        self.assertIsNone(review["verdict"], "no marker line, no verdict word")
        self.assertIsNone(review["verdictLine"])

    def test_reading_mutates_nothing(self):
        text = card(LEGACY, RELEVANCE)
        before = text
        acceptance.read_text(text, "90-x.md")
        acceptance.reports(text)
        self.assertEqual(text, before)

    def test_relevance_verdicts_change_no_pr_verdict_count_or_gate(self):
        plain = card(PR_ROUND)
        mixed = card(RELEVANCE, PR_ROUND, "## Relevance review — 2026-11-12 08:00 "
                     "(Hazel)\n\nRELEVANCE REVIEW: Already done\n")
        self.assertEqual(acceptance.summary(plain, "90-x.md"),
                         acceptance.summary(mixed, "90-x.md"),
                         "the face's counts ignore relevance reviews")
        a, b = acceptance.read_text(plain, "90-x.md"), acceptance.read_text(mixed, "90-x.md")
        self.assertEqual(a["counts"], b["counts"])
        self.assertEqual([r["verdict"] for r in a["rounds"]],
                         [r["verdict"] for r in b["rounds"]])
        self.assertEqual(taskfiles.PR_VERDICT_RE.findall(mixed), ["APPROVE"],
                         "the PR verdict reader sees only PR REVIEW lines")
        only = card("## Relevance review — 2026-11-12 08:00 (Hazel)\n\n"
                    "RELEVANCE REVIEW: Already done\n")
        self.assertEqual(acceptance.summary(only, "90-x.md"),
                         acceptance.summary(HEAD, "90-x.md"),
                         "a relevance review alone adds nothing to the face")
        self.assertEqual(taskfiles.PR_VERDICT_RE.findall(only), [])

    def test_a_relevance_heading_inside_the_brief_code_is_not_a_review(self):
        text = HEAD + "```\n## Relevance review — 2026-01-01 10:00 (x)\n```\n"
        self.assertEqual(acceptance.relevance_reviews(text), [])


CARD_84 = """# 65 — Refine me

**Status:** To Do
**Priority:** Medium
**Base branch:** main

## Context

Bench can work an application from outside it.

## Acceptance

- [ ] The roots are validated <!-- ac:roots -->
- [ ] Nothing is committed to the application

## Acceptance record

<!-- bench:acceptance-record v1 — written by the board. One decision per line; the newest about a criterion stands. -->

<!-- bench:acceptance {"actor": "ron", "actorSource": "git", "at": "2026-11-09T10:00:00Z", "criterion": "roots", "id": "r-0000000001", "rev": "aaaaaaaaaaaa", "source": "manual", "state": "accepted", "text": "The roots are validated", "note": "checked", "v": 1} -->

---

## Relevance review — 2026-11-08 08:11 (Juno)

RELEVANCE REVIEW: Needs rewrite

Reword the roots criterion: say "validated before any write".

---

## Relevance review — 2026-11-09 09:00 (Hazel)

RELEVANCE REVIEW: Still relevant

Nothing to change.
"""

REWORDED = """## Context

Bench can work an application from outside it.

## Acceptance

- [ ] The roots are validated before any write <!-- ac:roots -->
- [ ] Nothing is committed to the application
"""


class FromAReview(_Board):
    """Comments on a review, and a refinement asked from one, end to end."""

    def setUp(self):
        super().setUp()
        self.card.write_text(CARD_84, encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "reviews")
        self.juno, self.hazel = acceptance.relevance_reviews(CARD_84)

    # — comments —

    def test_a_comment_on_a_review_is_linked_and_starts_nothing(self):
        brief = self.brief()
        entry = refine.comment(FILENAME, "to-do", "Agree with the rewording",
                               review=self.juno["id"])
        self.assertEqual(entry["review"], self.juno["id"])
        self.assertTrue(entry["committed"])
        self.assertEqual(state.AGENTS, {}, "no agent is launched by a comment")
        self.assertEqual(refine.JOBS, {})
        self.assertEqual(self.brief(), brief, "no requirement changes")
        # survives a restart: the link is in the card, not in memory
        refine.reset()
        [saved] = refine.for_card(FILENAME, "to-do")["entries"]
        self.assertEqual((saved["id"], saved["review"]), (entry["id"], self.juno["id"]))
        self.assertIn(f'"review": "{self.juno["id"]}"', self.text())
        self.assert_application_untouched()

    def test_a_repeated_comment_request_is_one_comment(self):
        first = refine.comment(FILENAME, "to-do", "Once", review=self.juno["id"],
                               request_key="click-0000001")
        text = self.text()
        again = refine.comment(FILENAME, "to-do", "Once", review=self.juno["id"],
                               request_key="click-0000001")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(self.text(), text, "the replay writes nothing")

    def test_a_review_from_another_card_or_nobody_is_refused(self):
        foreign = acceptance.relevance_reviews(FIXTURE)[0]["id"]
        text = self.text()
        for bad in (foreign, "rl-0000000000", "rv-0123456789", "../x", ""):
            with self.subTest(review=bad), self.assertRaises(ValueError):
                refine.comment(FILENAME, "to-do", "spoof", review=bad)
        self.assertEqual(self.text(), text, "nothing was written")

    # — refinement from a review —

    def test_update_from_this_review_sends_the_review_and_proposes(self):
        job = self.run_job(proposal(REWORDED, "Reworded the roots criterion."),
                           text="Take the rewording", review_id=self.juno["id"])
        self.assertEqual(job["status"], "proposed", job.get("error"))
        self.assertEqual(job["review"], self.juno["id"])
        prompt = self.seen()["AGENT_PROMPT"]
        self.assertIn("Take the rewording", prompt)
        self.assertIn("--- SELECTED REVIEW ---", prompt)
        self.assertIn('say "validated before any write"', prompt.split(
            "--- SELECTED REVIEW ---")[1], "the selected review's report travels")
        self.assertNotIn("Nothing to change.", prompt.split("--- SELECTED REVIEW ---")[1]
                         .split("--- END SELECTED REVIEW ---")[0],
                         "only the selected review is the selected review")
        self.assertEqual(self.seen()["AGENT_MODE"], "refine",
                         "a refinement, not another review or an implementation")
        [comment] = taskfiles.discussion(self.text())
        self.assertEqual(comment["review"], self.juno["id"])
        shown = refine.public(job)
        self.assertEqual(shown["review"], self.juno["id"])
        self.assertIn("Relevance review — 2026-11-08 08:11 (Juno)", shown["reviewHeading"])
        self.assertTrue(shown["fresh"]["fresh"])
        self.assertEqual(self.card.parent.name, "to-do", "no stage move")
        self.assert_application_untouched()

    def test_apply_keeps_history_marks_recheck_and_attributes_the_review(self):
        job = self.run_job(proposal(REWORDED, "Reworded the roots criterion."),
                           text="Take the rewording", review_id=self.juno["id"])
        history = taskfiles.card_parts(self.text())["history"]
        refine.apply(job["id"])
        after = self.text()
        self.assertIn("validated before any write", self.brief())
        new_history = taskfiles.card_parts(after)["history"]
        for kept in (self.juno["report"], self.hazel["report"]):
            self.assertIn(kept, new_history, "every review stays in the history")
        self.assertEqual([r["id"] for r in acceptance.relevance_reviews(after)],
                         [self.juno["id"], self.hazel["id"]], "review ids survive Apply")
        decision = taskfiles.decision_for(after, job["id"])
        self.assertEqual(decision["review"], self.juno["id"])
        self.assertEqual(decision["affected"], ["roots"])
        self.assertIn("does not say every recommendation", decision["body"])
        records = [r for r in taskfiles.acceptance_records(after)
                   if r.get("criterion") == "roots"]
        self.assertTrue(records[-1].get("recheck"), "the reworded criterion needs recheck")
        self.assertTrue(len(new_history) > len(history))
        found = acceptance.read_text(after, FILENAME)
        self.assertEqual(found["findings"], [], "no finding was resolved or made")

    def test_discard_and_refusal_change_neither_brief_nor_acceptance(self):
        job = self.run_job(proposal(REWORDED), text="x", review_id=self.juno["id"])
        brief, records = self.brief(), taskfiles.acceptance_records(self.text())
        refine.discard(job["id"])
        self.assertEqual(self.brief(), brief)
        self.assertEqual(taskfiles.acceptance_records(self.text()), records)
        self.assertEqual(taskfiles.decision_for(self.text(), job["id"])["review"],
                         self.juno["id"])
        refused = self.run_job(proposal("# A title\n" + REWORDED), comment_id=None,
                               text="y", review_id=self.juno["id"])
        self.assertEqual(refused["status"], "failed")
        self.assertEqual(self.brief(), brief)
        self.assertEqual(taskfiles.acceptance_records(self.text()), records)

    def test_an_empty_instruction_asks_for_the_reviews_recommendations(self):
        job = self.run_job(proposal(REWORDED), text="  ", review_id=self.juno["id"])
        comment = taskfiles.find_entry(self.text(), job["comment"])
        self.assertEqual(comment["body"], refine.REVIEW_INSTRUCTION)

    def test_a_linked_comment_carries_its_review_and_a_mismatch_is_refused(self):
        entry = refine.comment(FILENAME, "to-do", "Do the rewording",
                               review=self.juno["id"])
        job = self.run_job(proposal(REWORDED), text=None, comment_id=entry["id"])
        self.assertEqual(job["review"], self.juno["id"], "the link travels")
        other = refine.comment(FILENAME, "to-do", "Hmm", review=self.hazel["id"])
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "to-do", comment_id=other["id"],
                           review_id=self.juno["id"], request_key="spoof-000001")
        plain = refine.comment(FILENAME, "to-do", "unlinked")
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "to-do", comment_id=plain["id"],
                           review_id=self.juno["id"], request_key="spoof-000002")

    def test_a_foreign_review_is_refused_before_anything_is_written(self):
        text = self.text()
        foreign = acceptance.relevance_reviews(FIXTURE)[0]["id"]
        with self.assertRaises(ValueError):
            refine.request(FILENAME, "to-do", text="spoof", review_id=foreign)
        self.assertEqual(self.text(), text)
        self.assertEqual(refine.JOBS, {})
        self.assertEqual(state.AGENTS, {})

    def test_the_same_request_twice_is_one_job(self):
        job = self.run_job(proposal(REWORDED), text="once", review_id=self.juno["id"],
                           request_key="same-click-01")
        again = refine.request(FILENAME, "to-do", text="once", review_id=self.juno["id"],
                               request_key="same-click-01")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["job"]["id"], job["id"])
        self.assertEqual(len(taskfiles.discussion(self.text())), 1)

    def test_a_removed_review_makes_the_proposal_stale(self):
        job = self.run_job(proposal(REWORDED), text="x", review_id=self.juno["id"])
        self.card.write_text(self.text().replace(
            "## Relevance review — 2026-11-08 08:11 (Juno)",
            "## Relevance review — 2026-11-08 08:12 (Juno)"), encoding="utf-8")
        self.assertFalse(refine.public(job)["fresh"]["fresh"])
        text = self.text()
        with self.assertRaises(taskfiles.StaleCard):
            refine.apply(job["id"])
        self.assertEqual(self.text(), text, "a stale proposal writes nothing")

    def test_comments_after_the_newest_review_are_not_part_of_it(self):
        """The discussion is written after the last review; it is the
        board's section, never the review's text — so a comment saved later
        neither changes the report nor stales a proposal made from it."""
        job = self.run_job(proposal(REWORDED), text="from the newest",
                           review_id=self.hazel["id"])
        refine.comment(FILENAME, "to-do", "A later note", review=self.hazel["id"])
        [_, hazel] = acceptance.relevance_reviews(self.text())
        self.assertEqual(hazel["report"], self.hazel["report"])
        self.assertNotIn("Discussion", hazel["report"])
        self.assertTrue(refine.public(job)["fresh"]["fresh"], refine.public(job)["fresh"])
        last = acceptance.reports(self.text())[-1]
        self.assertNotIn("bench:entry", last["text"], "Activity's copy stops there too")

    def test_retry_of_a_failed_review_refinement_keeps_its_review(self):
        failed = self.run_job("EXIT 1", text="x", review_id=self.juno["id"])
        self.assertEqual(failed["status"], "failed")
        retried = self.run_job(proposal(REWORDED), text=None,
                               comment_id=failed["comment"], retry=True)
        self.assertEqual(retried["status"], "proposed", retried.get("error"))
        self.assertEqual(retried["review"], self.juno["id"])
        self.assertEqual(retried["retryOf"], failed["id"])


NODE = shutil.which("node")
HARNESS = "\n".join([
    detail_ui.HARNESS.split("const input = JSON.parse")[0],
    detail_ui.function("activityHtml"),
    "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
    "const S = { state: {}, accOpen: {}, accDraft: {}, commentDraft: {},",
    "  evidence: { [input.file]: { evidence: [] } },",
    "  acc: { [input.file]: { data: input.acc } },",
    "  disc: input.disc ? { [input.file]: { data: input.disc } } : {} };",
    "const t = { file: input.file, stage: 'to-do', number: '79', body: input.body };",
    "process.stdout.write(JSON.stringify({",
    "  reviews: reviewsHtml(t, input.acc), activity: activityHtml(t, input.acc) }));",
])


@unittest.skipUnless(NODE, "node is needed to run the page's own renderers")
class Drawn(unittest.TestCase):
    """The page's own renderers, fed the server's own reading."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "reviews.js"
        cls.js.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def render(self, text: str, disc: dict | None = None) -> dict:
        acc = acceptance.read_text(text, "79-x.md")
        acc["reports"] = acceptance.reports(text)
        out = subprocess.run([NODE, str(self.js)], text=True, capture_output=True,
                             input=json.dumps({"file": "79-x.md", "acc": acc,
                                               "body": text, "disc": disc}))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    @staticmethod
    def groups(html: str) -> tuple[str, str]:
        rel, pr = html.split('aria-labelledby="rvg-pr-', 1)
        return rel, pr

    def test_card_79_review_is_under_relevance_beside_a_pr_empty_state(self):
        out = self.render(FIXTURE)
        rel, pr = self.groups(out["reviews"])
        [review] = acceptance.relevance_reviews(FIXTURE)
        self.assertIn("Relevance reviews", rel)
        self.assertIn(f'id="rvw-{review["id"]}"', rel)
        self.assertIn("Partly done", rel)
        self.assertIn("Hazel · 2026-11-10 14:22", rel)
        self.assertIn("Brief revision</dt><dd>not recorded", rel)
        self.assertIn("<strong>not</strong>", rel, "the report is drawn as Markdown")
        self.assertIn("No PR review has been filed on this card.", pr)
        self.assertNotIn("No relevance review", rel)
        self.assertIn("✎ Update card from this review", rel)
        self.assertIn(">Comment</button>", rel)
        # untrusted text stays inert
        self.assertNotIn("<script>", out["reviews"])
        self.assertNotIn('href="javascript:', out["reviews"])
        # Activity dates it and links to that exact copy, without a second one
        self.assertIn(f'data-goreview="{review["id"]}"', out["activity"])
        self.assertNotIn("Recommendation: update the task", out["activity"])

    def test_type_specific_empty_states(self):
        pr_only = self.render(card(PR_ROUND))["reviews"]
        rel, pr = self.groups(pr_only)
        self.assertIn("No relevance review has been filed on this card.", rel)
        self.assertNotIn("No PR review has been filed", pr)
        self.assertIn("Juno approved at 0123456789ab", pr)
        rel_only = self.render(card(RELEVANCE))["reviews"]
        rel, pr = self.groups(rel_only)
        self.assertIn("Needs rewrite", rel)
        self.assertIn("No PR review has been filed on this card.", pr)

    def test_mixed_multiple_and_legacy_rounds_are_labelled(self):
        out = self.render(card(LEGACY, RELEVANCE, PR_ROUND, RELEVANCE_2))
        rel, pr = self.groups(out["reviews"])
        self.assertIn("3 relevance reviews, newest first", rel)
        self.assertEqual(rel.count('class="rpt rvrel"'), 3, "every round is kept")
        self.assertLess(rel.index("Wren · 2026-11-10 11:30"), rel.index("Hazel · 2026-11-09"),
                        "newest first")
        self.assertIn("Wren · 2026-11-10 11:30", rel)
        self.assertIn("filed in an older format", rel)
        self.assertIn("reviewer not recorded", rel)
        self.assertNotIn("Still relevant", pr, "a relevance verdict is not a PR verdict")
        self.assertIn("Juno approved", pr)
        self.assertNotIn("approved", rel.lower().replace("approves nothing", ""),
                         "no relevance verdict is drawn as approval")

    def test_a_linked_comment_and_its_job_sit_in_the_reviews_thread(self):
        [review] = acceptance.relevance_reviews(FIXTURE)
        disc = {"entries": [
            {"id": "c-aaaaaaaaaa", "kind": "comment", "author": "ron", "at": "2026-11-11",
             "body": "<img src=x onerror=alert(1)> take it", "review": review["id"]},
            {"id": "c-bbbbbbbbbb", "kind": "comment", "author": "ron", "at": "2026-11-11",
             "body": "unrelated"}],
            "jobs": [{"id": "p-1", "comment": "c-aaaaaaaaaa", "status": "proposed",
                      "attempt": 1, "name": "Juno", "explanation": "Dropped a bullet.",
                      "review": review["id"], "reviewHeading": review["heading"],
                      "sections": ["What to build"], "affected": [],
                      "diff": [{"op": "-", "text": "- Lead with one result."}],
                      "fresh": {"fresh": True}}],
            "blocked": None, "refine": {"ready": True, "blockers": []}}
        out = self.render(FIXTURE, disc)
        rel, _ = self.groups(out["reviews"])
        thread = rel.split('class="rvthread"')[1]
        self.assertIn("c-aaaaaaaaaa", thread)
        self.assertNotIn("c-bbbbbbbbbb", thread, "only this review's comments")
        self.assertNotIn("<img", thread)
        self.assertIn("Asked from relevance review", thread)
        self.assertIn("not another review and not an implementation run", thread)
        self.assertIn('data-act="apply"', thread)
        self.assertIn('data-act="discard"', thread)
        self.assertIn('aria-label="proposed change to the brief"', thread)
        self.assertIn("about a relevance review", out["activity"])

    def test_a_blocked_board_disables_update_and_says_why(self):
        disc = {"entries": [], "jobs": [], "blocked": "Wren is implementing 79-x.md (work)",
                "refine": {"ready": True, "blockers": []}}
        rel, _ = self.groups(self.render(FIXTURE, disc)["reviews"])
        self.assertRegex(rel, r'data-rvact="refine" disabled')
        self.assertIn("Wren is implementing", rel)
        self.assertNotRegex(rel, r'data-rvact="comment" disabled',
                            "a comment can always be saved")


class SourceInvariants(unittest.TestCase):
    def test_review_actions_arm_and_carry_request_keys(self):
        body = detail_ui.function("wireReviews")
        self.assertIn("label: 'Update card from this review', confirm: true", body)
        self.assertIn("review: id, request", body)
        self.assertIn("'/api/card/comment'", body)
        self.assertNotIn("/api/review", body, "no review is asked for")
        self.assertNotIn("/api/agents", body, "no implementation is started")

    def test_activity_links_rather_than_copies_reviews(self):
        body = detail_ui.function("activityHtml")
        self.assertIn("if (r.review)", body)
        self.assertIn("data-goreview", body)


class Headers(dict):
    """Enough of an `email.message.Message` for the door's reading."""


class Door(unittest.TestCase):
    """Card writes are held to a JSON body from this board's own page."""

    def test_a_form_or_a_foreign_origin_is_refused(self):
        ok = Headers({"Content-Type": "application/json"})
        self.assertIsNone(httpd.card_write_problem(ok, 26071))
        self.assertIsNone(httpd.card_write_problem(
            Headers({"Content-Type": "application/json",
                     "Origin": "http://127.0.0.1:26071",
                     "Sec-Fetch-Site": "same-origin"}), 26071))
        for bad in ({"Content-Type": "text/plain"},
                    {"Content-Type": "application/x-www-form-urlencoded"},
                    {"Content-Type": "application/json", "Origin": "https://evil.example"},
                    {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
                    {"Content-Type": "application/json",
                     "Origin": "http://127.0.0.1:9999"}):
            with self.subTest(headers=bad):
                problem = httpd.card_write_problem(Headers(bad), 26071)
                self.assertIsNotNone(problem)
                self.assertIn("Nothing was changed", problem)


if __name__ == "__main__":
    unittest.main()
