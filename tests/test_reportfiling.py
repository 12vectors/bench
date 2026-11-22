"""Filing a run's report, recovering one that was not filed, and the
Brain's review index (task 169).

- **One report, one heading, whatever is interrupted.** The filing is
  recorded before the first append. Interrupted before the append, after
  it and before its receipt, after the receipt and before the index, or
  around the commit — then a restart, repeated retries, a double click or
  a lost reply — leaves exactly one copy on the card, under the heading
  allocated first, with the body recorded first, and a status that says
  what happened.
- **Only the original card on the original board.** A heading holding
  other text is a conflict, never overwritten; a card that moved stage or
  was archived is followed; a card that is gone is not recreated; a file
  in two places is not guessed at; another board is never written.
- **Distinct states.** Unfiled, filed-but-unindexed, a missing source and
  a failed commit or push each read differently; a board with no record
  of an operation says so rather than claiming a retry.
- **The index is rebuilt only on request**, from the cards, keeping every
  classification record and marking a lost source rather than dropping
  it. Reading writes nothing and runs nothing.

    python3 -m unittest tests.test_reportfiling -v
"""

from __future__ import annotations

import subprocess
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_brainreviews import _Layout, git, tree  # noqa: E402  (sets sys.path)

import acceptance  # noqa: E402
import agents  # noqa: E402
import brainreviews  # noqa: E402
import brainwrite  # noqa: E402
import config  # noqa: E402
import records  # noqa: E402
import reportfiling  # noqa: E402
import taskfiles  # noqa: E402

REPORT = "RELEVANCE REVIEW: Still relevant\n\nThe card still holds, and here is why."


class Stop(Exception):
    pass


def stop_at(point: str):
    def fault(where):
        if where == point:
            raise Stop(where)
    return fault


class _Filing(_Layout):

    def setUp(self):
        super().setUp()
        self.card = self.tasks / "in-progress" / "12-filing-card.md"
        self.card.write_text("# 12 — Filing card\n\n**Status:** In Progress\n"
                             "**Priority:** Low\n\nWhat this card is for.\n",
                             encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "card 12")
        self.addCleanup(setattr, reportfiling, "FAULT", None)
        self.record = {"task": "12-filing-card.md", "tasks": str(self.tasks),
                       "name": "Hazel", "id": "review-12-101010", "model": "opus-x"}

    def text(self, path: Path | None = None) -> str:
        return (path or self.card).read_text(encoding="utf-8")

    def file(self, fault: str | None = None) -> dict:
        reportfiling.FAULT = stop_at(fault) if fault else None
        try:
            return reportfiling.file(self.record, "Relevance review", REPORT,
                                     {"kind": "relevance", "briefRevision": None})
        finally:
            reportfiling.FAULT = None

    def op(self, op_id: str) -> dict:
        return reportfiling._read(op_id)

    def retry(self, op_id: str, action: str = "file", **extra) -> dict:
        return reportfiling.retry({"operation": op_id, "action": action, **extra})

    def copies(self, op: dict, path: Path | None = None) -> int:
        line = "## " + op["heading"]
        return sum(1 for raw in self.text(path).split("\n") if raw.rstrip() == line)

    def dirty(self) -> str:
        return git(self.work, "status", "--porcelain").stdout


class OneReportWhateverIsInterrupted(_Filing):

    def test_the_ordinary_path_files_commits_and_indexes_once(self):
        got = self.file()
        self.assertEqual(got["status"], "filed")
        self.assertEqual(got["commit"]["state"], "committed")
        self.assertTrue(got["indexed"])
        self.assertFalse(got["retained"], "the block is dropped once the card holds it")
        self.assertEqual(self.copies(got), 1)
        self.assertEqual(self.dirty(), "")
        rel = acceptance.relevance_reviews(self.text())[0]
        self.assertEqual(rel["id"], got["review"])
        self.assertEqual(rel["report"], REPORT)
        self.assertEqual(rel["reportSha"], got["reportSha"])
        reading = reportfiling.reading()
        mine = [r for r in reading["reviews"] if r["review"] == got["review"]][0]
        self.assertTrue(mine["indexed"])
        self.assertEqual(reading["filing"]["attention"], [])

    def test_a_lost_reply_asking_again_files_nothing_new(self):
        first = self.file()
        again = self.file()
        self.assertEqual(again["operation"], first["operation"])
        self.assertEqual(self.copies(first), 1)

    def test_before_the_append_a_restart_then_retry_files_the_original(self):
        got = self.file("before-append")
        self.assertEqual(got["status"], "pending")
        self.assertTrue(got["retained"])
        self.assertNotIn("Still relevant", self.text())
        found = reportfiling.reconcile()
        self.assertEqual(found, {"filed": 0, "unfiled": 1})
        op = reportfiling.operation(got["operation"])
        self.assertEqual((op["status"], op["code"]), ("unfiled", "interrupted"))
        self.assertIn("Retry filing", op["next"])
        reading = reportfiling.reading()
        self.assertEqual([a["operation"] for a in reading["filing"]["attention"]],
                         [got["operation"]])
        done = self.retry(got["operation"])
        self.assertEqual(done["status"], "filed")
        self.assertEqual(done["heading"], got["heading"], "the heading is never reallocated")
        self.assertEqual(done["review"], got["review"])
        again = self.retry(got["operation"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.copies(done), 1)
        self.assertEqual(acceptance.relevance_reviews(self.text())[0]["report"], REPORT)
        self.assertEqual(self.dirty(), "")

    def test_after_the_append_before_the_receipt_reconcile_records_it_filed(self):
        got = self.file("after-append")
        self.assertEqual(self.op(got["operation"])["status"], "pending")
        self.assertEqual(self.copies(got), 1)
        self.assertEqual(reportfiling.reconcile(), {"filed": 1, "unfiled": 0})
        op = reportfiling.operation(got["operation"])
        self.assertEqual(op["status"], "filed")
        self.assertEqual(op["commit"]["state"], "unknown")
        self.assertEqual(op["next"], "Retry commit")
        self.assertTrue(self.retry(got["operation"])["duplicate"])
        committed = self.retry(got["operation"], "commit")
        self.assertEqual(committed["commit"]["state"], "committed")
        self.assertEqual(self.dirty(), "")
        self.assertEqual(self.copies(got), 1)
        # a second Retry commit has nothing to do and says so
        self.assertTrue(self.retry(got["operation"], "commit")["duplicate"])

    def test_after_the_append_a_retry_without_restart_does_not_append_again(self):
        got = self.file("after-append")
        done = self.retry(got["operation"])
        self.assertEqual(done["status"], "filed")
        self.assertEqual(done["lastAttempt"]["result"], "already-filed")
        self.assertEqual(done["commit"]["state"], "committed")
        self.assertEqual(self.copies(got), 1)
        self.assertEqual(self.dirty(), "")

    def test_after_the_receipt_before_the_index_the_review_is_still_listed(self):
        got = self.file("after-receipt")
        op = reportfiling.operation(got["operation"])
        self.assertEqual(op["status"], "filed")
        self.assertFalse(op["indexed"])
        reading = reportfiling.reading()
        mine = [r for r in reading["reviews"] if r["review"] == got["review"]]
        self.assertEqual(len(mine), 1, "a missing index entry hides nothing")
        self.assertFalse(mine[0]["indexed"])
        self.assertGreaterEqual(reading["filing"]["unindexed"], 1)
        result = reportfiling.rebuild({"expected": reading["filing"]["index"]["revision"]})
        self.assertEqual(result["reconciled"], 1)
        after = reportfiling.reading()
        self.assertTrue(all(r["indexed"] for r in after["reviews"]))
        self.assertTrue(reportfiling.operation(got["operation"])["indexed"])

    def test_a_failed_commit_is_recorded_and_retried(self):
        with mock.patch.object(taskfiles, "commit_edit", return_value=False):
            got = self.file()
        self.assertEqual(got["status"], "filed")
        self.assertEqual(got["commit"]["state"], "failed")
        self.assertEqual(got["next"], "Retry commit")
        self.assertIn(got["operation"],
                      [a["operation"] for a in reportfiling.reading()["filing"]["attention"]])
        done = self.retry(got["operation"], "commit")
        self.assertEqual(done["commit"]["state"], "committed")
        self.assertEqual(self.dirty(), "")
        self.assertEqual(self.copies(got), 1)

    def test_a_push_without_a_remote_reads_failed_and_retry_push_says_why(self):
        got = self.file()
        with mock.patch.object(config, "SYNC", True):
            op = reportfiling.operation(got["operation"])
            self.assertEqual(op["push"]["state"], "failed")
            self.assertEqual(op["next"], "Retry push")
            out = self.retry(got["operation"], "push")
        self.assertIn("pushOutcome", out)

    def test_concurrent_retries_append_once(self):
        got = self.file("before-append")
        errors, results = [], []

        def go():
            try:
                results.append(self.retry(got["operation"]))
            except Exception as exc:   # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(self.copies(got), 1)
        self.assertEqual(sum(1 for r in results if not r.get("duplicate")), 1)


class OnlyTheOriginalDestination(_Filing):

    def test_a_different_body_under_the_heading_is_a_conflict_never_overwritten(self):
        got = self.file("before-append")
        heading = "## " + got["heading"]
        self.card.write_text(self.text() + f"\n\n---\n\n{heading}\n\nSomething else.\n",
                             encoding="utf-8")
        before = self.text()
        out = self.retry(got["operation"])
        self.assertEqual(out["status"], "conflict")
        self.assertEqual(self.text(), before)
        with self.assertRaises(reportfiling.Refused) as refused:
            self.retry(got["operation"])
        self.assertEqual(refused.exception.status, 409)
        closed = self.retry(got["operation"], "resolve", reason="the card's text stands")
        self.assertEqual(closed["status"], "resolved")
        self.assertFalse(closed["retained"])
        self.assertEqual(self.text(), before)

    def test_a_card_moved_or_archived_since_is_followed(self):
        got = self.file("before-append")
        archived = self.tasks / "archive" / self.card.name
        self.card.rename(archived)
        out = self.retry(got["operation"])
        self.assertEqual((out["status"], out["stage"]), ("filed", "archive"))
        self.assertEqual(self.copies(got, archived), 1)
        self.assertFalse(self.card.exists(), "nothing was recreated where it was")

    def test_a_card_that_is_gone_is_not_recreated(self):
        got = self.file("before-append")
        self.card.unlink()
        out = self.retry(got["operation"])
        self.assertEqual((out["status"], out["code"]), ("unfiled", "card-missing"))
        self.assertFalse(self.card.exists())
        self.assertTrue(out["retained"], "the report is kept for a person")
        self.assertIn("Restore the card", out["next"])

    def test_a_card_held_in_two_places_is_not_guessed_at(self):
        got = self.file("before-append")
        twin = self.tasks / "review" / self.card.name
        twin.write_text(self.text(), encoding="utf-8")
        out = self.retry(got["operation"])
        self.assertEqual(out["code"], "ambiguous")
        self.assertNotIn("Still relevant", self.text())
        self.assertNotIn("Still relevant", self.text(twin))

    def test_a_renamed_card_with_the_same_number_is_not_guessed_at(self):
        got = self.file("before-append")
        renamed = self.card.with_name("12-renamed.md")
        self.card.rename(renamed)
        out = self.retry(got["operation"])
        self.assertEqual(out["code"], "card-missing")
        self.assertNotIn("Still relevant", self.text(renamed))

    def test_another_board_never_receives_the_report(self):
        got = self.file("before-append")
        other = self.dir / "other" / "tasks"
        (other / "in-progress").mkdir(parents=True)
        (other / "in-progress" / self.card.name).write_text("# 12 — elsewhere\n",
                                                            encoding="utf-8")
        with mock.patch.object(config, "TASKS", other):
            out = self.retry(got["operation"])
        self.assertEqual(out["code"], "board-changed")
        self.assertEqual((other / "in-progress" / self.card.name).read_text(),
                         "# 12 — elsewhere\n")
        self.assertNotIn("Still relevant", self.text())

    def test_a_board_with_no_record_says_so(self):
        got = self.file()
        with mock.patch.object(config, "STATE", self.dir / "replica-state"):
            self.assertIsNone(reportfiling.operation(got["operation"]))
            with self.assertRaises(reportfiling.Refused) as refused:
                self.retry(got["operation"])
            self.assertEqual(refused.exception.status, 404)
            reading = reportfiling.reading()
        self.assertEqual(reading["filing"]["attention"], [])
        self.assertEqual(reading["filing"]["index"]["state"], "absent")
        self.assertTrue(all(not r["indexed"] for r in reading["reviews"]),
                        "a replica's reviews are listed, and said not indexed here")

    def test_retries_are_bounded(self):
        got = self.file("before-append")
        self.card.unlink()
        for _ in range(reportfiling.MAX_ATTEMPTS):
            self.retry(got["operation"])
        with self.assertRaises(reportfiling.Refused):
            self.retry(got["operation"])

    def test_the_agents_door_files_through_the_journal(self):
        agents._file_report(self.record, "Relevance review", REPORT,
                            {"kind": "relevance"})
        ops = reportfiling.operations("12-filing-card.md")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["status"], "filed")
        self.assertNotIn("board", ops[0], "the board's path is not public")


class TheIndex(_Filing):

    def test_rebuild_keeps_classifications_and_marks_a_lost_source(self):
        rid = acceptance.relevance_reviews(self.card7.read_text(encoding="utf-8"))[0]["id"]
        brainwrite.write("review-classify", {"request": self.req(), "card": "7",
                                             "review": rid, "classification": "Current",
                                             "reason": "it still holds"})
        records_dir = self.work / "brain" / "reviews"
        sidecars = tree(records_dir)
        brainreviews._CACHE.clear()
        reading = reportfiling.reading()
        self.assertEqual(reading["filing"]["index"]["state"], "absent")
        self.assertTrue(reading["reviews"], "no index hides no review")
        with self.assertRaises(reportfiling.Refused):
            reportfiling.rebuild({"expected": "0123456789abcdef"})
        built = reportfiling.rebuild({"expected": "none"})
        self.assertEqual(built["reviews"], len(reading["reviews"]))
        self.assertEqual(tree(records_dir), sidecars, "classification records untouched")
        again = reportfiling.reading()
        self.assertTrue(all(r["indexed"] for r in again["reviews"]))
        mine = [r for r in again["reviews"] if r["review"] == rid][0]
        self.assertEqual(mine["classification"], "Current")
        # the archived card's review loses its source
        self.card9.unlink()
        brainreviews._CACHE.clear()
        lost = reportfiling.reading()
        self.assertEqual(lost["filing"]["missingCount"], 1)
        self.assertEqual(lost["filing"]["missingSources"][0]["card"], "9")
        rebuilt = reportfiling.rebuild({"expected": lost["filing"]["index"]["revision"]})
        self.assertEqual(rebuilt["missing"], 1)
        self.assertEqual(reportfiling.reading()["filing"]["missingCount"], 1,
                         "a rebuild keeps the lost source, marked")
        # rebuilding twice adds nothing and duplicates nothing
        final = reportfiling.reading()
        keys = [r["key"] for r in final["reviews"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse((self.work / "brain").joinpath("index.json").exists())

    def test_reading_writes_nothing_runs_nothing_and_reaches_nothing(self):
        self.file("before-append")
        reportfiling.rebuild({"expected": "none"})
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
            reading = reportfiling.reading()
            reportfiling.operations()
            for item in reading["filing"]["attention"]:
                reportfiling.operation(item["operation"])
        self.assertEqual(tree(self.dir), before)
        self.assertEqual(len(reading["filing"]["attention"]), 1)


if __name__ == "__main__":
    unittest.main()
