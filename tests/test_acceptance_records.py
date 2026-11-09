"""Acceptance criteria tracked, decided and rechecked; review findings
opened, fixed and resolved (task 81).

Against a throwaway work-state repository, so every write is a real
commit. The rules under test are the ones that keep a count honest:
viewing writes nothing, nothing ticks itself, a stale page cannot
overwrite a newer decision, and history is appended, never rewritten.

    python3 -m unittest tests.test_acceptance_records -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import acceptance  # noqa: E402
import config  # noqa: E402
import progress  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CARD = """# 90 — Something worth accepting

**Status:** Review
**Priority:** Medium

## Context

Why it matters.

```
- [ ] An example in a fence is not a criterion
```

## Acceptance

- [ ] The page opens on the title
- [x] Keyboard users reach every section,
  including the tools
- [ ] Counts link to their section

---

## PR review at 0123456789ab — 2026-11-08 15:20 (Juno)

The tools row is above the title.

PR REVIEW: REQUEST CHANGES

---

## PR update — 2026-11-08 16:00 (Wren)

Moved the title first.
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class Records(unittest.TestCase):
    FILE = "90-accept.md"

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-acceptance-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", "Reviewer")
        git(self.repo, "config", "user.email", "r@example.com")
        for attr, value in (("TASKS", self.repo / "tasks"), ("REPO", self.repo),
                            ("COMMIT_MOVES", True)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        state.BOARD_EVENTS.clear()
        self.addCleanup(progress.RUNS.clear)
        progress.RUNS.clear()
        self.path = self.repo / "tasks" / "review" / self.FILE
        self.path.write_text(CARD, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

    # helpers ----------------------------------------------------------

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def read(self) -> dict:
        return acceptance.for_card(self.FILE, "review")

    def track(self) -> dict:
        return acceptance.track(self.FILE, "review", brief_sha=self.read()["briefSha"])

    def crit(self, reading: dict, index: int) -> dict:
        return reading["criteria"][index]

    def decide(self, index: int, state: str, note="checked by eye at 390px", **kw):
        c = self.crit(self.read(), index)
        return acceptance.decide(self.FILE, "review", criterion=c["id"], state=state,
                                 rev=c["rev"], basis=c["basis"], note=note, **kw)

    def commits(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout)

    # legacy and tracking ----------------------------------------------

    def test_an_untracked_card_reads_not_recorded_and_viewing_writes_nothing(self):
        reading = self.read()
        self.assertFalse(reading["tracked"])
        self.assertEqual(reading["criteria"], [])
        self.assertEqual(len(reading["untracked"]), 3,
                         "the fenced example is documentation, not a criterion")
        self.assertEqual(reading["legacyTicks"], 1)
        self.assertEqual(reading["counts"]["accepted"], 0)
        self.assertIsNone(acceptance.summary(self.text(), self.FILE),
                          "an untracked card carries no count on its face")
        self.assertEqual(self.text(), CARD)
        self.assertEqual(self.commits(), 1)

    def test_tracking_assigns_ids_keeps_ticks_and_text_and_records_the_tick_as_a_claim(self):
        result = self.track()
        self.assertTrue(result["committed"])
        self.assertEqual(len(result["tracked"]), 3)
        self.assertEqual(result["legacyTicks"], 1)
        text = self.text()
        self.assertIn("- [x] Keyboard users reach every section,", text)
        self.assertIn("  including the tools\n", text)
        self.assertIn("- [ ] An example in a fence is not a criterion\n", text)
        reading = self.read()
        self.assertTrue(reading["tracked"])
        states = [c["state"] for c in reading["criteria"]]
        self.assertEqual(states, ["pending", "reported", "pending"])
        self.assertIn("legacy", reading["criteria"][1]["reason"])
        self.assertEqual(reading["counts"]["accepted"], 0,
                         "a hand-written tick is never an acceptance")
        self.assertEqual(reading["criteria"][1]["text"],
                         "Keyboard users reach every section, including the tools")
        self.assertRegex(git(self.repo, "log", "-1", "--pretty=%s").stdout,
                         r"^board: 90 acceptance tracked")

    def test_tracking_refuses_a_stale_brief_and_a_second_pass(self):
        with self.assertRaises(taskfiles.StaleCard):
            acceptance.track(self.FILE, "review", brief_sha="0" * 64)
        self.assertEqual(self.text(), CARD)
        self.track()
        with self.assertRaisesRegex(ValueError, "already tracked"):
            self.track()

    # decisions ---------------------------------------------------------

    def test_a_manual_acceptance_is_attributed_counted_and_survives_a_reread(self):
        self.track()
        with self.assertRaisesRegex(ValueError, "required"):
            self.decide(0, "accepted", note="")
        result = self.decide(0, "accepted", ref="/files/reference/shot.png")
        record = result["record"]
        self.assertEqual(record["actor"], "Reviewer")
        self.assertEqual(record["source"], "manual")
        self.assertEqual(record["ref"], "/files/reference/shot.png")
        reading = self.read()
        self.assertEqual(reading["criteria"][0]["state"], "accepted")
        self.assertEqual(reading["counts"]["accepted"], 1)
        face = acceptance.summary(self.text(), self.FILE)
        self.assertEqual((face["accepted"], face["total"]), (1, 3))

    def test_a_stale_basis_or_rev_is_refused_and_a_replay_is_one_record(self):
        self.track()
        c = self.crit(self.read(), 0)
        acceptance.decide(self.FILE, "review", criterion=c["id"], state="accepted",
                          rev=c["rev"], basis=c["basis"], note="looked at it",
                          request="req-000001")
        again = acceptance.decide(self.FILE, "review", criterion=c["id"],
                                  state="accepted", rev=c["rev"], basis=c["basis"],
                                  note="looked at it", request="req-000001")
        self.assertTrue(again["duplicate"])
        with self.assertRaises(taskfiles.StaleCard):
            acceptance.decide(self.FILE, "review", criterion=c["id"],
                              state="needs-work", rev=c["rev"], basis=c["basis"],
                              note="an older page")
        with self.assertRaises(taskfiles.StaleCard):
            acceptance.decide(self.FILE, "review", criterion=c["id"],
                              state="needs-work", rev="feedfeedfeed",
                              basis=self.crit(self.read(), 0)["basis"], note="x y z")
        records = [r for r in taskfiles.acceptance_records(self.text())
                   if r.get("criterion") == c["id"]]
        self.assertEqual([r["state"] for r in records], ["pending", "accepted"])

    def test_a_criterion_of_another_card_is_refused(self):
        self.track()
        c = self.crit(self.read(), 0)
        with self.assertRaisesRegex(ValueError, "no tracked criterion"):
            acceptance.decide(self.FILE, "review", criterion="zzz-other", state="accepted",
                              rev=c["rev"], basis=None, note="nope nope")

    def test_rewording_rechecks_reordering_keeps_and_removing_leaves_the_denominator(self):
        self.track()
        self.decide(0, "accepted")
        self.decide(2, "accepted")
        reading = self.read()
        first, last = reading["criteria"][0], reading["criteria"][2]
        text = self.text()
        lines = text.split("\n")
        i0 = next(i for i, l in enumerate(lines) if first["id"] in l)
        i2 = next(i for i, l in enumerate(lines) if last["id"] in l)
        lines[i0], lines[i2] = lines[i2], lines[i0]
        self.path.write_text("\n".join(lines), encoding="utf-8")
        after = self.read()
        self.assertEqual([c["state"] for c in after["criteria"]],
                         ["accepted", "reported", "accepted"],
                         "reordering unchanged criteria changes no result")
        reworded = self.text().replace("The page opens on the title",
                                       "The page opens on the title and state")
        self.path.write_text(reworded, encoding="utf-8")
        moved = {c["id"]: c for c in self.read()["criteria"]}
        self.assertEqual(moved[first["id"]]["state"], "recheck")
        self.assertIn("reworded", moved[first["id"]]["reason"])
        self.assertEqual(moved[first["id"]]["decision"]["state"], "accepted",
                         "the prior result is kept and shown")
        self.assertEqual(moved[last["id"]]["state"], "accepted")
        dropped = "\n".join(
            "- [ ] A brand new criterion" if l.startswith("- [") and last["id"] in l else l
            for l in self.text().split("\n"))
        self.path.write_text(dropped, encoding="utf-8")
        final = self.read()
        self.assertEqual([r["id"] for r in final["removed"]], [last["id"]])
        self.assertEqual(final["counts"]["total"], 3)
        self.assertEqual(final["counts"]["accepted"], 0)
        self.assertEqual(final["untracked"][0]["text"], "A brand new criterion")

    def test_apply_through_refinement_marks_affected_records_for_recheck(self):
        self.track()
        self.decide(0, "accepted")
        before = taskfiles.card_parts(self.text())["brief"]
        new_brief = before.replace("The page opens on the title",
                                   "The page opens on title and state")
        decision = {"v": 1, "id": "d-1", "kind": "decision", "outcome": "applied",
                    "proposal": "p-1", "comment": "c-1", "author": "Reviewer",
                    "at": "2026-11-09T10:00:00Z"}
        taskfiles.replace_brief(self.FILE, "review",
                                expected_sha=taskfiles.brief_sha(before),
                                new_brief=new_brief, decision=decision,
                                title="Applied p-1", body="x",
                                affected=taskfiles.affected_criteria(before, new_brief))
        c = self.read()["criteria"][0]
        self.assertEqual(c["state"], "recheck")
        self.assertIn("p-1", c["reason"])
        # and a fresh confirmation against the new wording settles it
        self.decide(0, "accepted", note="rechecked against the new wording")
        self.assertEqual(self.read()["criteria"][0]["state"], "accepted")

    # agents and evidence ------------------------------------------------

    def test_an_agent_claim_reads_reported_and_never_accepts(self):
        self.track()
        cid = self.read()["criteria"][0]["id"]
        progress.RUNS["agent-1"] = {
            "agent": "agent-1", "task": self.FILE, "intent": "work",
            "criteriaReported": {cid: {"at": 9e9, "report": "p-1", "evidence": []}}}
        reading = self.read()
        self.assertEqual(reading["criteria"][0]["state"], "reported")
        self.assertEqual(reading["criteria"][0]["claim"]["agent"], "agent-1")
        self.assertEqual(reading["counts"]["accepted"], 0)

    def test_evidence_must_pass_now_and_a_later_stale_reading_rechecks(self):
        self.track()
        readings = {"ev-1": {"id": "ev-1", "found": True, "outcome": "failed"}}
        original = acceptance._evidence_now
        self.addCleanup(setattr, acceptance, "_evidence_now", original)
        acceptance._evidence_now = lambda eid, f: dict(readings.get(
            eid, {"id": eid, "found": False, "outcome": "missing"}))
        with self.assertRaisesRegex(ValueError, "reads failed"):
            self.decide(0, "accepted", note="", evidence="ev-1")
        with self.assertRaisesRegex(ValueError, "not a record"):
            self.decide(0, "accepted", note="", evidence="ev-2")
        readings["ev-1"] = {"id": "ev-1", "found": True, "outcome": "passed",
                            "check": "unit", "scope": None, "commit": "abc1234"}
        result = self.decide(0, "accepted", note="", evidence="ev-1")
        self.assertEqual(result["record"]["source"], "evidence")
        self.assertEqual(result["record"]["commit"], "abc1234")
        self.assertEqual(self.read()["criteria"][0]["state"], "accepted")
        readings["ev-1"] = {"id": "ev-1", "found": True, "outcome": "stale",
                            "why": "the head moved"}
        c = self.read()["criteria"][0]
        self.assertEqual(c["state"], "recheck")
        self.assertIn("stale", c["reason"])

    # review findings ----------------------------------------------------

    def test_review_rounds_are_read_off_the_report_headings(self):
        rounds = acceptance.rounds(self.text())
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["commit"], "0123456789ab")
        self.assertEqual(rounds[0]["verdict"], "REQUEST CHANGES")
        self.assertEqual(rounds[0]["reviewer"], "Juno")
        self.assertEqual(rounds[0]["id"], acceptance.rounds(self.text())[0]["id"])
        kinds = [r["kind"] for r in acceptance.reports(self.text())]
        self.assertEqual(kinds, ["PR review at 0123456789ab", "PR update"])

    def test_a_finding_stays_open_through_a_reported_fix_until_resolved(self):
        self.track()
        rnd = acceptance.rounds(self.text())[0]["id"]
        cid = self.read()["criteria"][0]["id"]
        opened = acceptance.finding(self.FILE, "review", event="opened",
                                    text="The tools row sits above the title",
                                    round_id=rnd, criterion=cid)
        fid = opened["record"]["finding"]

        def current():
            return next(f for f in self.read()["findings"] if f["id"] == fid)

        self.assertEqual(current()["state"], "open")
        with self.assertRaisesRegex(ValueError, "cannot be marked"):
            acceptance.finding(self.FILE, "review", event="reopened", finding_id=fid,
                               note="already open", basis=current()["basis"])
        acceptance.finding(self.FILE, "review", event="fix-reported", finding_id=fid,
                           note="moved the title first in 4f2c", basis=current()["basis"])
        self.assertEqual(current()["state"], "fix-reported")
        self.assertEqual(self.read()["findingCounts"]["open"], 0)
        stale = current()["events"][0]["id"]
        with self.assertRaises(taskfiles.StaleCard):
            acceptance.finding(self.FILE, "review", event="resolved", finding_id=fid,
                               note="looks right", basis=stale)
        with self.assertRaisesRegex(ValueError, "required"):
            acceptance.finding(self.FILE, "review", event="resolved", finding_id=fid,
                               note="", basis=current()["basis"])
        acceptance.finding(self.FILE, "review", event="resolved", finding_id=fid,
                           note="rechecked at 390px", basis=current()["basis"])
        done = current()
        self.assertEqual(done["state"], "resolved")
        self.assertEqual([e["event"] for e in done["events"]],
                         ["opened", "fix-reported", "resolved"])
        self.assertEqual(done["round"], rnd)
        self.assertEqual(done["criterion"], cid)
        with self.assertRaisesRegex(ValueError, "no finding"):
            acceptance.finding(self.FILE, "review", event="resolved",
                               finding_id="f-notthere", note="x y z", basis=None)
        with self.assertRaisesRegex(ValueError, "no review round"):
            acceptance.finding(self.FILE, "review", event="opened", text="other",
                               round_id="rv-0000000000")

    def test_writes_keep_the_brief_and_every_report(self):
        self.track()
        brief = taskfiles.card_parts(self.text())["brief"]
        self.decide(0, "needs-work", note="title still second")
        acceptance.finding(self.FILE, "review", event="opened", text="Title second")
        text = self.text()
        self.assertEqual(taskfiles.card_parts(text)["brief"], brief)
        self.assertIn("Moved the title first.", text)
        self.assertIn("PR REVIEW: REQUEST CHANGES", text)
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")


class ProgressCriteria(unittest.TestCase):
    def test_a_report_may_name_criteria_by_id_only(self):
        self.assertEqual(progress._criteria(["a1b2c3", "a1b2c3", "x-y"]),
                         ["a1b2c3", "x-y"])
        with self.assertRaises(progress.Refused):
            progress._criteria(["Not An Id"])
        with self.assertRaises(progress.Refused):
            progress._criteria([f"a{i}" for i in range(progress.MAX_CRITERIA + 1)])


if __name__ == "__main__":
    unittest.main()
