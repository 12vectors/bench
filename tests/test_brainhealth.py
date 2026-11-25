"""Brain maintenance health and whole-project reconciliation (task 200).

Held on #175's disposable split layout, through #197's queue and #199's
explanation update, with the same stub vendor standing in for the model:

- the health reading is passive: it writes, queues and launches nothing,
  on a fresh board as on a running one, and names what it did not inspect;
- entries that predate the policy read *never reconciled*, so a Brain
  nobody has reconciled is incomplete, never healthy;
- it finds affected explanations across source and entry changes — a card
  event, code outside card events, a moved binding — and broken transition
  links: a Historical entry with no Current explanation, a recorded
  explanation that was retired, a supersession chain that does not end at
  a live Current entry, a link to an entry that is not here, an
  Unclassified outcome;
- Repair is preview-bound: it queues exactly what the preview named, on
  the effective authority and budget it showed, refuses a stale preview,
  answers a replay once, and is refused while the policy does not run;
- the combined journey — a baseline, a disposable change of code and its
  card, the immutable review on the card, evidence in the code, a
  reassessed classification and the same explanation updated — keeps the
  stable ids, writes no new page and leaves a traceable Historical →
  Current trail with its attribution.

    python3 -m unittest tests.test_brainhealth -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_braindraft import git
from tests.test_brainevaluate import entry
from tests.test_brainmaintain import with_update
from tests.test_brainreconcile import (EVERYTHING, EXPORTS_CURRENT, JSON_CURRENT,
                                       MUDDLE_UNCLASSIFIED, OLD_SUPERSEDED, _Reconciling)

import brain  # noqa: E402
import braindraft  # noqa: E402
import brainreconcile  # noqa: E402
import handbook  # noqa: E402

LIVE = {"e-csv-study", "e-json-study", "e-old-delimiter", "e-muddle", "e-exports"}


class _Health(_Reconciling):

    def health(self) -> dict:
        return brainreconcile.health()

    def codes(self, read: dict, eid: str | None) -> set[str]:
        return {f["code"] for f in read["findings"] if f["entry"] == eid}

    def put(self, eid: str, text: str) -> None:
        (self.work / "brain" / f"{eid}.md").write_text(text, encoding="utf-8")

    def commit_app(self, text: str, message: str = "change") -> str:
        (self.app / "src" / "export.py").write_text(text, encoding="utf-8")
        git(self.app, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qam", message)
        return git(self.app, "rev-parse", "HEAD")


class Passive(_Health):

    def test_reading_writes_queues_and_launches_nothing(self):
        before = self.files()
        read = self.health()
        self.assertEqual(self.calls(), 0)
        self.assertFalse(brainreconcile.state_path().exists(), "health wrote the queue")
        self.assertEqual(self.files(), before)
        self.assertEqual(read["kind"], "bench-brain-health")
        self.assertTrue(read["scope"]["notInspected"])

    def test_entries_that_predate_the_policy_are_never_reconciled_so_not_complete(self):
        read = self.health()
        self.assertEqual(read["scope"]["entries"], len(LIVE) + 1)   # e-held too
        self.assertEqual(read["scope"]["neverAssessed"], len(LIVE))
        self.assertEqual(read["scope"]["held"], 1)
        self.assertIn({"entry": "e-held", "why": "held by a manual override"},
                      read["scope"]["excluded"])
        for eid in LIVE:
            self.assertIn("never-assessed", self.codes(read, eid))
        self.assertNotIn("never-assessed", self.codes(read, "e-held"))
        self.assertNotEqual(read["state"], "assessed")
        self.assertIn(read["state"], ("incomplete", "attention"))
        nxt = next(f["next"] for f in read["findings"] if f["code"] == "never-assessed")
        self.assertEqual(nxt["action"], "turn-on")
        # unconfigured: a repair is previewed and refused, naming why
        self.assertFalse(read["repair"]["available"])
        self.assertIn("not configured", read["repair"]["refused"])
        self.assertEqual(set(read["repair"]["entries"]), LIVE)

    def test_the_handbook_gap_is_coverage_never_a_gate(self):
        read = self.health()
        gaps = [f for f in read["findings"] if f["code"] == "coverage-gap"]
        self.assertEqual(len(gaps), len(handbook.SECTIONS))
        self.assertTrue(all(f["severity"] == "coverage" for f in gaps))
        self.assertEqual(gaps[0]["next"]["action"], "organize")

    def test_a_file_that_is_not_an_entry_is_an_uninspected_gap(self):
        (self.work / "brain" / "broken.md").write_text(
            "# Broken\n\n**Brain:** 1\n**Id:** e-broken\n", encoding="utf-8")
        read = self.health()
        bad = [f for f in read["findings"] if f["code"] == "file-unreadable"]
        self.assertEqual(bad[0]["file"], "broken.md")
        self.assertEqual(read["scope"]["files"]["unreadable"], 1)


class Links(_Health):

    def test_broken_transition_links_are_each_found(self):
        self.put("e-gone", entry("e-gone", "A retired explanation", "Guide", "Current",
                                 "Old.", "**Removed:** replaced by nothing\n"))
        self.put("e-old-study", entry(
            "e-old-study", "A study that shipped", "Investigation", "Historical",
            "We built it.") + "- 2026-11-03 · Historical · ronald — shipped; "
            "explained now by e-gone\n")
        self.put("e-orphan", entry("e-orphan", "A study with no explanation",
                                   "Investigation", "Historical", "It happened."))
        self.put("e-chain", entry("e-chain", "Replaced twice", "Decision", "Superseded",
                                  "Old.", "**Superseded by:** e-orphan\n"))
        self.put("e-lonely", entry("e-lonely", "Superseded by nobody", "Decision",
                                   "Superseded", "Old."))
        self.put("e-linky", entry("e-linky", "Links elsewhere", "Guide", "Current",
                                  "See e-nowhere.", "**Related:** e-nowhere\n"
                                  "**Source:** #99\n"))
        self.put("e-open", entry("e-open", "Not yet decided", "Guide", "Unclassified",
                                 "Unknown."))
        read = self.health()
        self.assertIn("explanation-broken", self.codes(read, "e-old-study"))
        broken = next(f for f in read["findings"] if f["code"] == "explanation-broken")
        self.assertIn("retired", broken["message"])
        self.assertIn("historical-no-current", self.codes(read, "e-orphan"))
        self.assertIn("superseded-no-successor", self.codes(read, "e-chain"))
        self.assertIn("superseded-no-successor", self.codes(read, "e-lonely"))
        self.assertIn("link-missing", self.codes(read, "e-linky"))
        self.assertIn("cites-missing-card", self.codes(read, "e-linky"))
        self.assertIn("unclassified", self.codes(read, "e-open"))
        self.assertEqual(read["state"], "attention")
        self.assertIn({"entry": "e-gone", "why": "retired"}, read["scope"]["excluded"])

    def test_a_historical_entry_linked_to_a_live_current_one_is_not_a_finding(self):
        self.put("e-done-study", entry(
            "e-done-study", "A study that shipped", "Investigation", "Historical",
            "We built it.", "**Related:** e-exports\n"))
        self.assertNotIn("historical-no-current",
                         self.codes(self.health(), "e-done-study"))


class Bindings(_Health):

    def bind(self) -> None:
        self.put("e-bound", entry("e-bound", "Where the delimiter lives", "Guide", "Current",
                                  "In the export module.",
                                  f"**Bindings:** app:src/export.py@{self.commit}\n"
                                  "**Standing:** implemented\n**Assessed:** 2026-11-01\n"))

    def test_code_that_moved_outside_a_card_event_marks_the_bound_entry(self):
        self.bind()
        self.assertNotIn("binding-moved", self.codes(self.health(), "e-bound"))
        self.commit_app("DELIMITER = ','\n")
        read = self.health()
        moved = [f for f in read["findings"]
                 if f["entry"] == "e-bound" and f["code"] == "binding-moved"]
        self.assertEqual(moved[0]["binding"]["state"], "stale")
        self.assertIn("e-bound", read["repair"]["entries"])
        self.assertEqual(self.calls(), 0, "a moved binding launched something")

    def test_a_spent_git_budget_is_said_as_not_inspected(self):
        self.bind()
        with mock.patch.object(handbook, "budget", lambda: handbook._Budget(0)):
            read = self.health()
        self.assertIn("binding-not-checked", self.codes(read, "e-bound"))
        self.assertEqual(read["scope"]["bindings"]["notChecked"], 1)
        self.assertNotEqual(read["state"], "assessed")


class Repair(_Health):

    def test_repair_is_refused_while_the_policy_does_not_run(self):
        read = self.health()
        with self.assertRaises(brainreconcile.Refused) as caught:
            brainreconcile.act({"action": "repair", "request": self.key(),
                                "entries": read["repair"]["entries"],
                                "preview": read["repair"]["digest"]})
        self.assertEqual(caught.exception.extra.get("code"), "off")
        self.assertEqual(self.calls(), 0)

    def test_repair_queues_exactly_the_preview_once_on_its_authority_and_budget(self):
        self.enable(dailyLaunches=1, batch=2)
        self.drain()
        calls = self.calls()
        self.commit_app("DELIMITER = '|'\n")
        read = self.health()
        drifted = {f["entry"] for f in read["findings"] if f["code"] == "code-changed"}
        self.assertTrue(drifted)
        pre = read["repair"]
        self.assertTrue(pre["available"], pre["refused"])
        self.assertEqual(set(pre["entries"]), drifted)
        self.assertEqual(pre["authority"]["apply"], False)
        self.assertIn("waits for your review", pre["would"])
        self.assertIsNotNone(pre["access"])
        # a stale preview is refused with the current one, nothing queued
        with self.assertRaises(brainreconcile.Refused) as caught:
            brainreconcile.act({"action": "repair", "request": self.key(),
                                "entries": pre["entries"], "preview": "stale"})
        self.assertEqual(caught.exception.extra["code"], "preview-stale")
        self.assertEqual(caught.exception.extra["repair"]["digest"], pre["digest"])
        # an entry the preview does not name is refused
        with self.assertRaises(brainreconcile.Refused):
            brainreconcile.act({"action": "repair", "request": self.key(),
                                "entries": pre["entries"] + ["e-held"],
                                "preview": pre["digest"]})
        key = self.key()
        out = brainreconcile.act({"action": "repair", "request": key,
                                  "entries": pre["entries"], "preview": pre["digest"]})
        self.assertEqual(set(out["entries"]), drifted)
        again = brainreconcile.act({"action": "repair", "request": key,
                                    "entries": pre["entries"], "preview": pre["digest"]})
        self.assertTrue(again["duplicate"])
        rows = self.rows()
        self.assertTrue(all(rows[e]["reason"] == "repair" for e in drifted))
        self.assertEqual(self.calls(), calls, "the click launched something")


class Endings(_Health):
    """A failed, refused or cancelled round wrote an ending, not an
    assessment: the scope and the last-assessed time never count it (the
    review of task 200 found they did)."""

    def scope_adds_up(self, read: dict) -> None:
        s = read["scope"]
        self.assertEqual(s["assessed"] + s["neverAssessed"] + s["inputsChanged"]
                         + s["inProgress"] + s["unresolved"] + s["held"], s["entries"])

    def test_a_failed_round_is_not_an_assessment(self):
        self.script(EVERYTHING, fail=True)
        self.enable(retries=0)
        self.drain()
        read = self.health()
        self.assertEqual(read["scope"]["assessed"], 0)
        self.assertEqual(read["scope"]["unresolved"], len(LIVE))
        self.assertIsNone(read["scope"]["lastAssessed"])
        self.assertNotEqual(read["state"], "assessed")
        self.assertIn("outcome-unresolved", self.codes(read, "e-csv-study"))
        self.scope_adds_up(read)

    def test_a_refused_launch_is_not_an_assessment(self):
        def refuse(payload, **kw):
            raise braindraft.Refused("the harness is not installed here")
        with mock.patch.object(braindraft, "start_evaluate", refuse):
            self.enable()
            brainreconcile.tick()
        rows = self.rows()
        self.assertTrue(all(rows[e]["stored"] == "blocked" for e in LIVE))
        read = self.health()
        self.assertEqual(read["scope"]["assessed"], 0)
        self.assertIsNone(read["scope"]["lastAssessed"])
        self.scope_adds_up(read)

    def test_a_cancelled_entry_is_not_an_assessment_and_keeps_its_real_time(self):
        self.enable(dailyLaunches=1, batch=1)
        self.drain()
        rows = self.rows()
        done = [e for e in LIVE if rows[e]["stored"] == "review"]
        waiting = [e for e in LIVE if rows[e]["stored"] == "queued"]
        self.assertEqual(len(done), 1)
        before = self.health()
        self.assertEqual(before["scope"]["assessed"], 1)
        self.assertEqual(before["scope"]["inProgress"], len(waiting))
        self.scope_adds_up(before)
        brainreconcile.act({"action": "cancel", "request": self.key(),
                            "entries": waiting})
        read = self.health()
        self.assertEqual(read["scope"]["assessed"], 1)
        self.assertEqual(read["scope"]["unresolved"], len(waiting))
        self.assertEqual(read["scope"]["lastAssessed"], before["scope"]["lastAssessed"])
        self.scope_adds_up(read)

    def test_a_later_failure_keeps_when_it_was_really_assessed(self):
        self.enable()
        self.drain()
        first = self.health()["scope"]["lastAssessed"]
        self.assertIsNotNone(first)
        self.script(EVERYTHING, fail=True)
        self.policy(retries=0)
        brainreconcile.act({"action": "rebuild", "request": self.key()})
        self.drain()
        read = self.health()
        self.assertEqual(read["scope"]["assessed"], 0)
        self.assertEqual(read["scope"]["lastAssessed"], first)
        self.scope_adds_up(read)


class RepairBounds(_Health):

    def test_a_repair_without_the_evaluate_authority_is_refused_as_the_preview_says(self):
        self.policy(enabled=True, evaluate=False)
        read = self.health()
        self.assertFalse(read["repair"]["available"])
        with self.assertRaises(brainreconcile.Refused) as caught:
            brainreconcile.act({"action": "repair", "request": self.key(),
                                "entries": read["repair"]["entries"],
                                "preview": read["repair"]["digest"]})
        self.assertEqual(caught.exception.extra.get("code"), "not-available")
        self.assertFalse(any(r["stored"] == "queued" for r in self.rows().values()))

    def test_a_repair_larger_than_one_action_is_offered_in_parts(self):
        with mock.patch.object(brainreconcile, "MAX_REPAIR", 2):
            read = self.health()
        pre = read["repair"]
        self.assertEqual(pre["count"], 2)
        self.assertEqual(pre["more"], len(LIVE) - 2)
        self.assertIn("more need repair", pre["would"])


class Journey(_Health):
    """seed → work → review → implementation → classification → maintained
    explanation, through the queue, the shared evaluation and the writer."""

    def test_the_combined_journey_keeps_ids_and_a_traceable_trail(self):
        # the baseline: maintained entries written before reconciliation
        files_before = set(self.files())
        ids_before = {e["id"] for e in brain.index()["entries"]}
        self.assertEqual(self.health()["scope"]["neverAssessed"], len(LIVE))
        # an authorized disposable change: code, and its card's immutable review
        self.commit_app("DELIMITER = ';'\nBOM = True\n", "semicolons")
        card = self.work / "tasks" / "done" / "12-export-as-csv.md"
        card.write_text(card.read_text(encoding="utf-8") +
                        "\n## PR review at abc123 — 2026-11-20 10:00 (Hazel)\n\n"
                        "The semicolon export is implemented.\n\nPR REVIEW: APPROVE\n",
                        encoding="utf-8")
        self.script([with_update(), JSON_CURRENT, OLD_SUPERSEDED, MUDDLE_UNCLASSIFIED,
                     EXPORTS_CURRENT])
        self.enable(apply=True)
        self.policy(maintain=True)
        self.drain()
        # reassessed: the investigation is Historical, explained by the same page
        self.assertEqual(brain.entry("e-csv-study")["classification"], "Historical")
        exports = brain.entry("e-exports")
        self.assertEqual(exports["classification"], "Current")
        self.assertIn("## CSV exports", exports["body"])
        self.assertEqual(set(self.files()), files_before, "a new page was written")
        self.assertEqual({e["id"] for e in brain.index()["entries"]}, ids_before)
        read = self.health()
        self.assertNotIn("historical-no-current", self.codes(read, "e-csv-study"))
        self.assertNotIn("explanation-broken", self.codes(read, "e-csv-study"))
        self.assertNotIn("superseded-no-successor", self.codes(read, "e-old-delimiter"))
        self.assertNotIn("never-assessed", self.codes(read, "e-csv-study"))
        trail = [t for t in read["transitions"] if t["entry"] == "e-csv-study"]
        self.assertEqual(trail[0]["classification"], "Historical")
        self.assertEqual(trail[0]["by"], "policy")
        self.assertIn("explained now by e-exports", trail[0]["reason"])
        self.assertEqual(trail[-1]["classification"], "Current")   # the history kept
        self.assertTrue(read["applied"])
        # e-muddle was not applied by the policy: an open outcome, said as such
        self.assertIn("outcome-unresolved", self.codes(read, "e-muddle"))
        self.assertEqual(read["state"], "attention")
        # a reversion of the code is affected work; nothing launches by itself
        calls = self.calls()
        self.commit_app("DELIMITER = ','\n", "revert")
        brainreconcile.tick()
        self.assertEqual(self.calls(), calls)
        read = self.health()
        self.assertIn("code-changed", self.codes(read, "e-csv-study"))
        self.assertIn("e-csv-study", read["repair"]["entries"])

    def test_duplicate_card_events_coalesce_into_one_evaluation(self):
        self.enable()
        self.drain()
        calls = self.calls()
        files = set(self.files())
        brainreconcile.SETTLE = 3600.0
        for n in range(3):
            self.card(f"\nEvent {n}.\n")
            brainreconcile.tick()
            brainreconcile.tick()           # a replayed notification
        self.assertEqual(self.calls(), calls)
        brainreconcile.SETTLE = 0.0
        self.drain()
        self.assertEqual(self.calls(), calls + 1)
        self.assertEqual(set(self.files()), files, "an event wrote a page")


if __name__ == "__main__":
    unittest.main()
