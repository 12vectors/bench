"""Reviews filed on cards, read in the Brain (task 168).

The report stays on its card. These cases hold the Brain's projection of
it to the card's own readers, the provenance bench records when it files
a review to what the run was launched with, and the review classification
record to the Brain's one writer:

- **The same report, never a copy.** A PR review round and a relevance
  review read through `brainreviews` carry the body, identity and digest
  `acceptance` reads off the card, with the clipping notice where the
  record was clipped. No body is written under brain/, and a write that
  names a report field is refused.
- **Provenance from the launch.** A relevance review whose card is edited
  after launch and before filing names the brief revision it was launched
  with; a legacy review names none, and reads unknown. A PR review keeps
  the full head and base and who said so.
- **Stable addresses.** A rename, a move and archiving keep the address;
  two cards sharing a number never resolve to the wrong report; a review
  or card that is not here says which.
- **Unclassified without a write.** A new review reads Unclassified and
  nothing is written to say so; a person's classification and override
  are one record under brain/reviews/, held to its revision, surviving
  every rebuild, and untouched by maintained-entry writes.
- **Passive reads.** Listing and reading run no process, write no file and
  reach no network.

    python3 -m unittest tests.test_brainreviews -v
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import acceptance  # noqa: E402
import agents  # noqa: E402
import brain  # noqa: E402
import brainreviews  # noqa: E402
import brainwrite  # noqa: E402
import config  # noqa: E402
import records  # noqa: E402
import reports  # noqa: E402
import reviews  # noqa: E402
import routes  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402

from tests.test_execution_profiles import CARD, ProfileCase, card  # noqa: E402
from tests.test_settings_security import Board  # noqa: E402

HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "fedcba9876543210fedcba9876543210fedcba98"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check)


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def filed(heading: str, report: str, prov: dict | None = None,
          at: str = "2026-11-20 10:00", who: str = "Juno") -> str:
    """One report as agents._file_report appends it."""
    mark = acceptance.provenance_line(prov) + "\n\n" if prov else ""
    return f"\n\n---\n\n## {heading} — {at} ({who})\n\n{mark}{report}\n"


def brief(title: str, words: str = "What this card is for.") -> str:
    return f"# {title}\n\n**Status:** Review\n**Priority:** Low\n\n{words}\n"


PR_PROV = {"kind": "pr", "head": {"ref": "task/7-a-card", "commit": HEAD, "authority": "github"},
           "base": {"ref": "main", "commit": BASE, "authority": "github"},
           "pr": "https://github.com/o/r/pull/3", "prNumber": "3", "prState": "OPEN",
           "label": "current", "agent": "review-pr-7-1", "model": "opus-x",
           "reviewer": "Juno", "filed": "2026-11-20T10:00:00Z"}


class _Layout(unittest.TestCase):
    """An application repository and a separate work-state repository with
    a few cards holding filed reviews."""

    commit_moves = True

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-brainreviews-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app, self.work = self.dir / "app", self.dir / "work"
        for repo in (self.app, self.work):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "ronald")
            git(repo, "config", "user.email", "r@example.com")
            git(repo, "config", "commit.gpgsign", "false")
        (self.app / "src.py").write_text("print('app')\n")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-qm", "app")
        self.tasks = self.work / "tasks"
        for stage, _ in config.STAGES:
            (self.tasks / stage).mkdir(parents=True)
            (self.tasks / stage / ".gitkeep").write_text("")
        (self.tasks / "archive").mkdir()
        (self.tasks / "archive" / ".gitkeep").write_text("")
        r1 = "PR REVIEW: REQUEST CHANGES\n\nThe first round found a bug."
        r2 = "PR REVIEW: APPROVE\n\nThe second round is happy."
        rel = "RELEVANCE REVIEW: Still relevant\n\nThe card still holds."
        self.card7 = self.tasks / "review" / "07-a-card.md"
        self.card7.write_text(
            brief("07 — A card")
            + filed("PR review at 0123456789ab", r1, {**PR_PROV, "label": "current"},
                    at="2026-11-20 10:00")
            + filed("Relevance review", rel, at="2026-11-19 09:00", who="Hazel")
            + filed("PR review at 0123456789ab", r2, PR_PROV, at="2026-11-21 11:00"))
        self.card9 = self.tasks / "archive" / "09-old-card.md"
        self.card9.write_text(brief("09 — Old card")
                              + filed("Relevance review", "RELEVANCE REVIEW: Already done",
                                      at="2026-11-01 08:00", who="Wren"))
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "work")
        patcher = mock.patch.multiple(
            config, TASKS=self.tasks, REPO=self.app, EXTERNAL=True,
            STATE=self.dir / "state", COMMIT_MOVES=self.commit_moves,
            SYNC=False, STATE_GIT_REMOTE="", STATE_BASE="main")
        patcher.start()
        self.addCleanup(patcher.stop)
        sync._NOTES.clear()
        self.addCleanup(sync._NOTES.clear)
        brainreviews._CACHE.clear()
        self.n = 0

    def req(self) -> str:
        self.n += 1
        return f"test-{self._testMethodName[-30:]}-{self.n}"

    def ids(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        return {"pr": [r["id"] for r in acceptance.rounds(text)],
                "rel": [r["id"] for r in acceptance.relevance_reviews(text)]}


class TheSameReport(_Layout):

    def test_both_kinds_and_every_round_are_listed_unclassified_without_a_write(self):
        before = tree(self.work)
        found = brainreviews.index()
        self.assertEqual(tree(self.work), before, "listing wrote something")
        self.assertFalse((self.work / "brain").exists(), "reading made brain/")
        keys = {(r["cardId"], r["type"], r["round"]) for r in found["reviews"]}
        self.assertEqual(keys, {("7", "pr", 1), ("7", "pr", 2), ("7", "relevance", 1),
                                ("9", "relevance", 1)})
        self.assertTrue(all(r["classification"] == "Unclassified" and not r["classified"]
                            for r in found["reviews"]))
        self.assertEqual(found["counts"]["Unclassified"], 4)
        archived = [r for r in found["reviews"] if r["cardId"] == "9"][0]
        self.assertTrue(archived["archived"])
        self.assertIn("#9", archived["title"])
        # newest first, and every address names the card and the review
        self.assertEqual(found["reviews"][0]["at"], "2026-11-21 11:00")
        for r in found["reviews"]:
            self.assertEqual(r["address"], f"/brain/r/{r['cardId']}/{r['review']}")

    def test_the_brain_reads_the_cards_own_report_and_digest(self):
        text = self.card7.read_text(encoding="utf-8")
        for r in acceptance.rounds(text) + acceptance.relevance_reviews(text):
            got = brainreviews.review("7", r["id"])
            self.assertEqual(got["state"], "found")
            self.assertEqual(got["report"]["body"], r["report"])
            self.assertEqual(got["report"]["reportSha"], r["reportSha"])
            self.assertEqual(got["report"]["reportSha"], acceptance.report_sha(r["report"]))
            self.assertNotIn("bench:review", got["report"]["body"])
            self.assertEqual(got["source"]["reviewsAddress"],
                             f"/cards/7?tab=reviews&review={r['id']}")

    def test_pr_provenance_keeps_the_full_head_base_and_authority(self):
        rv = self.ids(self.card7)["pr"][1]
        got = brainreviews.review("7", rv)
        prov = got["provenance"]
        self.assertTrue(prov["recorded"])
        self.assertEqual(prov["head"], {"ref": "task/7-a-card", "commit": HEAD,
                                        "authority": "github"})
        self.assertEqual(prov["base"]["commit"], BASE)
        self.assertEqual(prov["prNumber"], "3")
        self.assertEqual(got["report"]["round"], 2)
        self.assertEqual(got["report"]["rounds"], 2)
        self.assertEqual(len(got["others"]), 1)
        # bench has not observed this PR's head: unknown, not current
        self.assertEqual(got["assessment"]["state"], "unknown")
        reviews.observe("07-a-card.md", head=HEAD, source="test")
        self.addCleanup(reviews.forget_observed, "07-a-card.md")
        self.assertEqual(brainreviews.review("7", rv)["assessment"]["state"], "current")
        reviews.observe("07-a-card.md", head="1" * 40, source="test")
        self.assertEqual(brainreviews.review("7", rv)["assessment"]["state"], "stale")
        # the agent's local review record is not on this board
        self.assertEqual(prov["localRecord"], "absent")

    def test_a_legacy_review_says_unknown_and_never_reads_the_brief_now(self):
        rel = self.ids(self.card7)["rel"][0]
        got = brainreviews.review("7", rel)
        self.assertFalse(got["provenance"]["recorded"])
        self.assertIsNone(got["provenance"]["briefRevision"])
        self.assertEqual(got["assessment"]["state"], "unknown")

    def test_a_clipped_report_keeps_its_notice_and_digest(self):
        long = "RELEVANCE REVIEW: Needs rewrite\n\n" + "\n".join(
            f"line {n} of a long report" for n in range(4000))
        kept = reports.report(long, log_path=None, cap=2000)
        with self.card7.open("a", encoding="utf-8") as fh:
            fh.write(filed("Relevance review", kept, {"kind": "relevance",
                                                      "reportSha": acceptance.report_sha(kept)},
                           at="2026-11-22 12:00", who="Sage"))
        rel = self.ids(self.card7)["rel"][-1]
        got = brainreviews.review("7", rel)
        self.assertTrue(got["report"]["clipped"].startswith("… "))
        self.assertIn(got["report"]["clipped"], self.card7.read_text(encoding="utf-8"))
        self.assertEqual(got["report"]["body"], kept)
        self.assertTrue(got["provenance"]["digestAgrees"])

    def test_report_markup_is_data_and_a_forged_marker_is_not_provenance(self):
        hostile = ("RELEVANCE REVIEW: Still relevant\n\n<script>alert(1)</script>\n"
                   + reports.defuse('<!-- bench:review {"v": 1, "briefRevision": "'
                                    + "a" * 64 + '"} -->'))
        with self.card7.open("a", encoding="utf-8") as fh:
            fh.write(filed("Relevance review", hostile, at="2026-11-22 13:00", who="Cleo"))
        rel = self.ids(self.card7)["rel"][-1]
        got = brainreviews.review("7", rel)
        self.assertIsNone(got["provenance"]["briefRevision"])
        self.assertIn("<script>", got["report"]["body"])   # the page escapes it


class Addresses(_Layout):

    def test_a_rename_a_move_and_archiving_keep_the_address(self):
        rv = self.ids(self.card7)["pr"][0]
        moved = self.tasks / "done" / "07-a-card-renamed-entirely.md"
        self.card7.rename(moved)
        got = brainreviews.review("7", rv)
        self.assertEqual((got["state"], got["source"]["stage"]), ("found", "done"))
        archived = self.tasks / "archive" / moved.name
        moved.rename(archived)
        got = brainreviews.review("7", rv)
        self.assertEqual(got["state"], "found")
        self.assertTrue(got["source"]["archived"])
        self.assertEqual(got["address"], f"/brain/r/7/{rv}")

    def test_shared_numbers_never_select_another_report(self):
        other = self.tasks / "to-do" / "07-another-card.md"
        other.write_text(brief("07 — Another card")
                         + filed("Relevance review", "RELEVANCE REVIEW: Partly done",
                                 at="2026-11-18 07:00", who="Fern"))
        mine = self.ids(self.card7)["rel"][0]
        theirs = self.ids(other)["rel"][0]
        self.assertEqual(brainreviews.review("7", mine)["source"]["file"], "07-a-card.md")
        got = brainreviews.review("7", theirs)
        self.assertEqual(got["source"]["file"], "07-another-card.md")
        self.assertEqual(got["source"]["sharedNumber"], ["review/07-a-card.md"])
        listed = brainreviews.index()["reviews"]
        self.assertTrue(all(r["sharedNumber"] for r in listed if r["cardId"] == "7"))
        # the very same review on two cards sharing the number: both named
        copied = self.card7.read_text(encoding="utf-8")
        other.write_text(copied.replace("07 — A card", "07 — Copied card"))
        got = brainreviews.review("7", mine)
        self.assertEqual(got["state"], "ambiguous")
        self.assertEqual(sorted(got["files"]), ["review/07-a-card.md",
                                                "to-do/07-another-card.md"])

    def test_missing_review_missing_card_and_bad_addresses(self):
        self.assertEqual(brainreviews.review("7", "rv-0000000000")["state"], "no-review")
        self.assertEqual(brainreviews.review("404", "rv-0000000000")["state"], "no-card")
        for card_id, rid in (("7", "x"), ("../7", "rv-0000000000"), ("7", "rv-00000000000")):
            with self.assertRaises(brainreviews.NotAnAddress):
                brainreviews.review(card_id, rid)

    def test_ui_addresses_on_the_server(self):
        self.assertTrue(routes.ui_path("/brain/r/7/rv-0123456789"))
        self.assertTrue(routes.ui_path("/brain/r/07-a-card/rl-0123456789"))
        for path in ("/brain/r/7", "/brain/r/7/rv-01234", "/brain/r/7/rv-0123456789/x",
                     "/brain/r/%2e%2e/rv-0123456789", "/brain/x/7/rv-0123456789"):
            self.assertFalse(routes.ui_path(path), path)


class Classification(_Layout):

    def classify(self, rid: str, **over) -> dict:
        payload = {"request": self.req(), "card": "7", "review": rid,
                   "classification": "Current", "reason": "explains the shipped routing"}
        payload.update(over)
        return brainwrite.write("review-classify", payload)

    def refused(self, kind: str, payload: dict, status: int) -> brainwrite.Refused:
        before = tree(self.work)
        with self.assertRaises(brainwrite.Refused) as caught:
            brainwrite.write(kind, {"request": self.req(), **payload})
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        self.assertEqual(tree(self.work), before, "a refused write changed the work root")
        return caught.exception

    def test_a_classification_is_one_record_beside_the_card(self):
        cards = tree(self.tasks)
        rv = self.ids(self.card7)["pr"][1]
        op = self.classify(rv)
        self.assertTrue(op["saved"])
        self.assertEqual(op["commit"]["state"], "committed")
        record = self.work / "brain" / "reviews" / f"c7-{rv}.md"
        self.assertTrue(record.is_file())
        text = record.read_text(encoding="utf-8")
        self.assertIn(f"**Review of:** #7/{rv}", text)
        self.assertNotIn("second round", text, "the report was copied")
        self.assertEqual(tree(self.tasks), cards, "a card changed")
        got = brainreviews.review("7", rv)
        self.assertEqual(got["classification"]["classification"], "Current")
        self.assertEqual(got["classification"]["history"][-1]["actor"], "ronald")
        # the maintained-entry reader does not count brain/reviews/
        self.assertEqual(brain.index()["skipped"], 0)
        self.assertEqual(brain.index()["entries"], [])
        # held to the record it read: a second write naming no revision is stale
        self.refused("review-classify", {"card": "7", "review": rv,
                                         "classification": "Historical", "reason": "older"}, 409)
        revision = got["classification"]["revision"]
        self.classify(rv, classification="Historical", reason="no longer directs work",
                      expected=revision)
        history = brainreviews.review("7", rv)["classification"]["history"]
        self.assertEqual([h["classification"] for h in history], ["Current", "Historical"])

    def test_a_reason_is_optional_and_the_override_reason_is_not(self):
        """Task 202: a review is classified, and corrected, with no reason,
        an empty one or whitespace; a short comment is kept as given; the
        history says who and when either way and invents no reason. An
        override still needs its reason, and the record stays guarded."""
        rv = self.ids(self.card7)["pr"][0]
        expected = None
        for klass, reason in (("Current", None), ("Historical", ""),
                              ("Unclassified", "   "), ("Current", "ok")):
            if reason is None:   # the field left out altogether
                brainwrite.write("review-classify", {
                    "request": self.req(), "card": "7", "review": rv,
                    "classification": klass, "expected": expected})
            else:
                self.classify(rv, classification=klass, reason=reason, expected=expected)
            expected = brainreviews.review("7", rv)["classification"]["revision"]
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual([h["classification"] for h in got["history"]],
                         ["Current", "Historical", "Unclassified", "Current"])
        self.assertEqual([h["reason"] for h in got["history"]], ["", "", "", "ok"])
        self.assertEqual({h["actor"] for h in got["history"]}, {"ronald"})
        text = (self.work / "brain" / "reviews" / f"c7-{rv}.md").read_text(encoding="utf-8")
        self.assertNotIn("—", text.split("## Classification history")[1].splitlines()[2])
        # Superseded still needs its structure, not a reason
        e = brainwrite.write("create", {"request": self.req(), "title": "Replacement",
                                        "kind": "Guide"})
        self.classify(rv, classification="Superseded", supersededBy=e["id"], reason="",
                      expected=expected)
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual(got["supersededBy"], e["id"])
        self.assertEqual(got["history"][-1]["reason"], f"superseded by {e['id']}")
        self.refused("review-classify", {"card": "7", "review": rv, "expected": got["revision"],
                                         "classification": "Superseded", "reason": ""}, 400)
        self.refused("review-classify", {"card": "7", "review": rv, "expected": got["revision"],
                                         "classification": "Current", "reason": 5}, 400)
        self.refused("review-classify", {"card": "7", "review": rv, "expected": got["revision"],
                                         "classification": "Current", "reason": "x" * 501}, 400)
        for reason in ("", "  ", None):
            self.refused("review-override", {"card": "7", "review": rv, "action": "set",
                                             "expected": got["revision"], "reason": reason}, 400)

    def test_override_set_and_clear(self):
        rel = self.ids(self.card7)["rel"][0]
        brainwrite.write("review-override", {"request": self.req(), "card": 7, "review": rel,
                                             "action": "set", "reason": "a person decided"})
        got = brainreviews.review("7", rel)["classification"]
        self.assertIn("manual · ronald", got["override"])
        self.assertTrue([r for r in brainreviews.index()["reviews"]
                         if r["review"] == rel][0]["override"])
        brainwrite.write("review-override", {"request": self.req(), "card": "7", "review": rel,
                                             "action": "clear", "reason": "undone",
                                             "expected": got["revision"]})
        self.assertIsNone(brainreviews.review("7", rel)["classification"]["override"])

    def test_report_fields_markup_and_unknown_reviews_are_refused(self):
        rv = self.ids(self.card7)["pr"][0]
        for key in ("body", "report", "title", "file"):
            self.refused("review-classify", {"card": "7", "review": rv, key: "x",
                                             "classification": "Current",
                                             "reason": "a reason"}, 400)
        self.refused("review-classify", {"card": "7", "review": rv, "classification": "Current",
                                         "reason": "<script>alert(1)</script>"}, 400)
        self.refused("review-classify", {"card": "7", "review": "rv-0000000000",
                                         "classification": "Current", "reason": "x y z"}, 404)
        self.refused("review-classify", {"card": "../7", "review": rv,
                                         "classification": "Current", "reason": "x y z"}, 400)

    def test_a_link_for_brain_reviews_is_never_written_through(self):
        (self.work / "brain").mkdir()
        outside = self.dir / "outside"
        outside.mkdir()
        (self.work / "brain" / "reviews").symlink_to(outside, target_is_directory=True)
        rv = self.ids(self.card7)["pr"][0]
        self.refused("review-classify", {"card": "7", "review": rv,
                                         "classification": "Current", "reason": "x y z"}, 409)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(brainreviews.index()["directory"]["state"], "refused")

    def test_classifications_survive_a_rebuild_and_new_reviews_read_unclassified(self):
        rv = self.ids(self.card7)["pr"][0]
        self.classify(rv)
        record = self.work / "brain" / "reviews" / f"c7-{rv}.md"
        kept = record.read_bytes()
        with self.card7.open("a", encoding="utf-8") as fh:
            fh.write(filed("PR review at 0123456789ab", "PR REVIEW: APPROVE\n\nThird.",
                           PR_PROV, at="2026-11-23 09:00"))
        brainreviews._CACHE.clear()
        listed = {r["review"]: r for r in brainreviews.index()["reviews"]}
        self.assertEqual(listed[rv]["classification"], "Current")
        newest = self.ids(self.card7)["pr"][-1]
        self.assertEqual(listed[newest]["classification"], "Unclassified")
        self.assertEqual(record.read_bytes(), kept)

    def test_maintained_entry_writes_leave_reviews_and_cards_alone(self):
        rv = self.ids(self.card7)["pr"][0]
        self.classify(rv)
        record = self.work / "brain" / "reviews" / f"c7-{rv}.md"
        kept, cards = record.read_bytes(), tree(self.tasks)
        op = brainwrite.write("create", {"request": self.req(), "title": "Routing",
                                         "kind": "Guide", "sources": [f"#7/{rv}", "#7"]})
        entry = brain.entry(op["id"])
        self.assertEqual(entry["sources"][0]["kind"], "review")
        self.assertEqual(entry["sources"][0]["address"], f"/brain/r/7/{rv}")
        brainwrite.write("retire", {"request": self.req(), "id": op["id"],
                                    "expected": entry["revision"], "reason": "gone now"})
        self.assertEqual(record.read_bytes(), kept)
        self.assertEqual(tree(self.tasks), cards)
        # a maintained entry citing the review is a backlink on its page
        linked = brainreviews.review("7", rv)["linkedFrom"]
        self.assertEqual([x["via"] for x in linked], ["review"])

    def test_an_orphaned_record_says_its_source_is_missing(self):
        rv = self.ids(self.card7)["pr"][0]
        self.classify(rv)
        self.card7.unlink()
        found = brainreviews.index()
        self.assertEqual(found["orphans"][0]["why"], "card-missing")
        got = brainreviews.review("7", rv)
        self.assertEqual(got["state"], "no-card")
        self.assertEqual(got["classification"]["classification"], "Current")


class PassiveReads(_Layout):

    def test_reading_runs_nothing_writes_nothing_and_reaches_nothing(self):
        rv = self.ids(self.card7)["pr"][0]
        brainwrite.write("review-classify", {"request": self.req(), "card": "7", "review": rv,
                                             "classification": "Current", "reason": "x y z"})
        brainreviews._CACHE.clear()
        before = tree(self.dir)

        def forbidden(*a, **k):
            raise AssertionError("a passive read did something")

        with mock.patch.object(subprocess, "run", forbidden), \
                mock.patch.object(subprocess, "Popen", forbidden), \
                mock.patch.object(records, "write_json", forbidden), \
                mock.patch.object(records, "write_bytes", forbidden), \
                mock.patch.object(urllib.request, "urlopen", forbidden), \
                mock.patch.object(agents, "launch", forbidden):
            listed = brainreviews.index()
            for r in listed["reviews"]:
                brainreviews.review(r["cardId"], r["review"])
            brainreviews.review("7", "rv-0000000000")
        self.assertEqual(tree(self.dir), before)


class ClosedTicketDefault(_Layout):
    """Task 201: a closed card's PR reviews read Historical by a derived
    lifecycle default, never written; records win; reopening undoes it."""

    def close(self, where: str = "done") -> Path:
        moved = self.tasks / where / self.card7.name
        self.card7.rename(moved)
        self.card7 = moved
        return moved

    def readings(self) -> dict:
        listed = {r["review"]: r for r in brainreviews.index()["reviews"] if r["cardId"] == "7"}
        detail = {rid: brainreviews.review("7", rid)["classification"] for rid in listed}
        return {"listed": listed, "detail": detail}

    def forbidden(self):
        def boom(*a, **k):
            raise AssertionError("a passive read did something")
        return [mock.patch.object(subprocess, "run", boom),
                mock.patch.object(subprocess, "Popen", boom),
                mock.patch.object(records, "write_json", boom),
                mock.patch.object(records, "write_bytes", boom),
                mock.patch.object(urllib.request, "urlopen", boom),
                mock.patch.object(agents, "launch", boom)]

    def test_done_and_archive_make_pr_reviews_historical_without_a_write(self):
        ids = self.ids(self.card7)
        text_before = self.card7.read_bytes()
        for where in ("done", "archive"):
            if where == "archive":
                self.close("archive")
            else:
                self.close("done")
            before = tree(self.dir)
            patches = self.forbidden()
            for p in patches:
                p.start()
            try:
                got = self.readings()
                found = brainreviews.index()
            finally:
                for p in patches:
                    p.stop()
            self.assertEqual(tree(self.dir), before, "a reading wrote something")
            self.assertFalse((self.work / "brain").exists())
            for rv in ids["pr"]:
                row, det = got["listed"][rv], got["detail"][rv]
                self.assertEqual((row["classification"], row["classified"],
                                  row["classificationSource"]),
                                 ("Historical", False, "closed-ticket"), where)
                self.assertEqual((det["classification"], det["recorded"], det["source"]),
                                 ("Historical", False, "closed-ticket"))
                self.assertIn("lifecycle default", det["why"])
                self.assertEqual(det["history"], [], "a decision was invented")
                self.assertIsNone(det["override"])
            # relevance reviews are not touched by the rule
            rel = got["listed"][ids["rel"][0]]
            self.assertEqual((rel["classification"], rel["classificationSource"]),
                             ("Unclassified", "none"))
            # counts agree with the rows they count
            self.assertEqual(found["counts"]["Historical"], 2)
            self.assertEqual(found["counts"]["Unclassified"], 2)
        # report bytes, verdicts, identities, digests and links are as they were
        self.assertEqual(self.card7.read_bytes(), text_before)
        self.assertEqual(self.ids(self.card7), ids)
        for rv, verdict in zip(ids["pr"], ("REQUEST CHANGES", "APPROVE")):
            got = brainreviews.review("7", rv)
            self.assertEqual(got["report"]["verdict"], verdict)
            self.assertEqual(got["address"], f"/brain/r/7/{rv}")
            self.assertEqual(got["source"]["reviewsAddress"], f"/cards/7?tab=reviews&review={rv}")
            self.assertEqual(got["provenance"]["head"]["commit"], HEAD)
        self.assertEqual(brain.index()["entries"], [])

    def test_reopening_returns_unrecorded_reviews_to_unclassified_never_current(self):
        rv = self.ids(self.card7)["pr"][0]
        self.close("done")
        self.assertEqual(brainreviews.review("7", rv)["classification"]["classification"],
                         "Historical")
        rev_closed = brainreviews.index()["revision"]
        reopened = self.tasks / "in-progress" / self.card7.name
        self.card7.rename(reopened)
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual((got["classification"], got["source"]), ("Unclassified", "none"))
        # the page's reading revision moves with the card's stage
        self.assertNotEqual(brainreviews.index()["revision"], rev_closed)

    def test_a_persons_record_wins_on_a_closed_card_and_survives_reopening(self):
        rv = self.ids(self.card7)["pr"][1]
        brainwrite.write("review-classify", {"request": self.req(), "card": "7", "review": rv,
                                             "classification": "Current",
                                             "reason": "still explains the routing"})
        self.close("done")
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual((got["classification"], got["source"], got["recorded"]),
                         ("Current", "record", True))
        row = [r for r in brainreviews.index()["reviews"] if r["review"] == rv][0]
        self.assertEqual(row["classification"], "Current")
        self.card7.rename(self.tasks / "review" / self.card7.name)
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual(got["classification"], "Current")
        self.assertEqual([h["classification"] for h in got["history"]], ["Current"])

    def test_an_override_on_a_closed_card_fixes_what_it_reads_as(self):
        rv = self.ids(self.card7)["pr"][0]
        self.close("archive")
        brainwrite.write("review-override", {"request": self.req(), "card": "7", "review": rv,
                                             "action": "set", "reason": "a person decided"})
        got = brainreviews.review("7", rv)["classification"]
        self.assertEqual((got["classification"], got["source"]), ("Historical", "record"))
        self.assertIn("manual · ronald", got["override"])
        # reopened, the person's record still stands
        self.card7.rename(self.tasks / "to-do" / self.card7.name)
        self.assertEqual(brainreviews.review("7", rv)["classification"]["classification"],
                         "Historical")

    def test_a_late_filed_review_on_a_closed_card_reads_historical_once(self):
        self.close("done")
        with self.card7.open("a", encoding="utf-8") as fh:
            fh.write(filed("PR review at 0123456789ab", "PR REVIEW: APPROVE\n\nLate.",
                           PR_PROV, at="2026-11-23 09:00", who="Sage"))
        late = self.ids(self.card7)["pr"][-1]
        first = brainreviews.index()
        brainreviews._CACHE.clear()      # a restart: rebuilt from the cards
        second = brainreviews.index()
        self.assertEqual(first["revision"], second["revision"])
        rows = [r for r in second["reviews"] if r["review"] == late]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["classification"], "Historical")
        self.assertFalse((self.work / "brain").exists())


class RelevanceBindingFromTheLaunch(ProfileCase):
    """A real relevance review, through the adapter's own `run` against a
    stub vendor: the brief is edited while the run works, and the filed
    review names the brief it was launched with."""

    def test_the_binding_is_the_launch_input_not_the_brief_at_filing(self):
        self.adapter_is('#!/usr/bin/env python3\nprint("RELEVANCE REVIEW: Still relevant")\n')
        launched = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        want = taskfiles.brief_sha(taskfiles.card_parts(launched)["brief"])
        gate = threading.Event()
        real = agents.finish

        def finish(*args):
            gate.wait(30)
            return real(*args)

        self.addCleanup(setattr, agents, "finish", real)
        agents.finish = finish
        agents.start_review(CARD, "in-progress")
        path = self.tasks / "in-progress" / CARD
        path.write_text(launched.replace("at enough length to be a brief",
                                         "rewritten while the review ran"), encoding="utf-8")
        gate.set()
        self.assertEqual(agents.join_reapers(30), [])
        text = path.read_text(encoding="utf-8")
        found = acceptance.relevance_reviews(text)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["briefRevision"], want)
        now = taskfiles.brief_sha(taskfiles.card_parts(text)["brief"])
        self.assertNotEqual(want, now)
        self.assertEqual(found[0]["provenance"]["briefSource"], "launch")
        self.assertEqual(found[0]["provenance"]["reportSha"], found[0]["reportSha"])
        brainreviews._CACHE.clear()
        got = brainreviews.review("67", found[0]["id"])
        self.assertEqual(got["provenance"]["briefRevision"], want)
        self.assertEqual(got["assessment"]["state"], "stale")
        self.assertTrue(got["provenance"]["digestAgrees"])


class PrProvenanceAtFiling(ProfileCase):

    def test_a_filed_pr_review_carries_the_pinned_head_and_base(self):
        pinned = {"head": {"ref": "task/67", "commit": HEAD, "authority": "github"},
                  "base": {"ref": "main", "commit": BASE, "authority": "local"},
                  "pr": "https://github.com/o/r/pull/9", "number": "9", "state": "OPEN"}
        prov = agents._pr_provenance("review-pr-x", pinned, {"stale": True,
                                                              "changed": ["head moved"]}, "")
        record = {"task": CARD, "tasks": str(self.tasks), "name": "Juno",
                  "id": "review-pr-x", "model": "opus-x"}
        agents._file_report(record, "PR review at 0123456789ab", "PR REVIEW: APPROVE\n\nok",
                            prov)
        text = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        r = acceptance.rounds(text)[0]
        self.assertEqual(r["report"], "PR REVIEW: APPROVE\n\nok")
        self.assertEqual(r["provenance"]["head"]["commit"], HEAD)
        self.assertEqual(r["provenance"]["base"]["authority"], "local")
        self.assertEqual(r["provenance"]["label"], "superseded")
        self.assertEqual(r["provenance"]["model"], "opus-x")
        self.assertEqual(r["provenance"]["reportSha"], r["reportSha"])
        # the Activity reader shows the report without bench's line, too
        shown = [x for x in acceptance.reports(text) if x.get("review") == r["id"]][0]
        self.assertEqual(shown["text"], r["report"])


class OverHttp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        class _Cleanup:
            def __init__(self):
                self.fns = []

            def addCleanup(self, fn, *a):
                self.fns.append((fn, a))

        def prepare(board):
            (board.work / "tasks" / "review" / "01-a-card.md").write_text(
                brief("01 — A card") + filed("Relevance review", "RELEVANCE REVIEW: Still relevant",
                                             at="2026-11-20 10:00", who="Hazel"),
                encoding="utf-8")
        cls.cleanup = _Cleanup()
        cls.board = Board(cls.cleanup, prepare=prepare)

    @classmethod
    def tearDownClass(cls):
        for fn, a in reversed(cls.cleanup.fns):
            fn(*a)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.board.url + path, timeout=20) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_reads_addresses_and_the_write_door(self):
        status, raw = self.get("/api/brain/reviews")
        self.assertEqual(status, 200)
        listed = json.loads(raw)["reviews"]["reviews"]
        self.assertEqual(len(listed), 1)
        rid = listed[0]["review"]
        status, raw = self.get(f"/api/brain/review?card=1&id={rid}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["review"]["report"]["verdict"], "Still relevant")
        self.assertEqual(self.get("/api/brain/review?card=1&id=rv-0000000000")[0], 404)
        self.assertEqual(self.get("/api/brain/review?card=1&id=nope")[0], 400)
        status, raw = self.get(f"/brain/r/1/{rid}")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", raw.lower())
        self.assertEqual(self.get("/brain/r/1/nope")[0], 404)
        data = json.dumps({"request": "http-review-0001", "card": "1", "review": rid,
                           "classification": "Current", "reason": "x y z"}).encode()
        request = urllib.request.Request(
            self.board.url + "/api/brain/review/classify", data=data, method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example",
                     "Sec-Fetch-Site": "cross-site"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=20)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        self.assertFalse((self.board.work / "brain").exists())


if __name__ == "__main__":
    unittest.main()
