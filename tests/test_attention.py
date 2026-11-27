"""Agent activity and its attention inbox (task 207).

What these hold, in the order the card's acceptance names them:

- every source is projected, not re-implemented: a run's state is its
  progress record's ending (an `unobserved` ending after a restart reads
  *interrupted*, never success), a Brain draft's proposal is not *applied*,
  an Orchestrator answer says nothing was executed, a review says it is
  not an acceptance;
- unread is a transition nobody here acknowledged: the same transition
  seen twice (a replayed frame, a second observation, a restart) is one,
  an acknowledgement of an older key never hides a newer one, two
  acknowledgements of different items both stand, and a fresh inbox
  starts from a baseline rather than raising every old result;
- the record is bounded — a cap and a retention, both counted — and a
  record this version cannot read is set aside, never written over;
- a source that no longer holds an item says so (`gone`), and a question
  someone answered elsewhere stops asking;
- reading writes nothing, and the record holds no draft text, prompt or
  credential, and no notification preference.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import attention  # noqa: E402
import braindraft  # noqa: E402
import config  # noqa: E402
import decisions  # noqa: E402
import progress  # noqa: E402
import reasoning  # noqa: E402
import refine  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402

BOARD = {"stages": [{"name": "in-progress", "tasks": [
    {"file": "7-export.md", "stage": "in-progress", "number": "7", "title": "Export the table",
     "body": "# 7 — Export\n"},
]}]}


def item(iid="run:a1", st="finished", key=None, **extra):
    base = {"id": iid, "kind": "run", "purpose": "implementation", "state": st,
            "label": attention.WORDS[st], "key": key or f"{st}:1", "title": "Wren · run",
            "card": None, "owner": "board", "open": {"agent": "run", "target": {"agent": "a1"}},
            "href": "/cards/7", "note": None, "at": None}
    base.update(extra)
    return base


class Isolated(unittest.TestCase):
    """Each test has its own state directory, an empty inbox in memory,
    and no frame leaves the process."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(config, "ATTENTION_DIR", root / "attention"),
            mock.patch.object(config, "PROGRESS_DIR", root / "progress"),
            mock.patch.object(state, "broadcast", side_effect=self.sent),
        ]
        self.frames = []
        for p in self.patches:
            p.start()
        attention.reset()
        self.saved_runs = dict(progress.RUNS)
        progress.RUNS.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        progress.RUNS.clear()
        progress.RUNS.update(self.saved_runs)
        attention.reset()
        self.tmp.cleanup()

    def sent(self, payload):
        self.frames.append(payload)

    def observe_items(self, items, now=None, skipped=()):
        with mock.patch.object(attention, "_project", return_value=(items, set(skipped))):
            return attention.observe(board=BOARD, now=now)

    def items(self):
        return {r["id"]: r for r in attention.reading()["items"]}


class UnreadIsATransitionNobodyAcknowledged(Isolated):
    def test_a_fresh_inbox_is_a_baseline_not_a_flood(self):
        self.observe_items([item("run:old", "finished")])
        self.assertFalse(self.items()["run:old"]["unread"],
                         "what was over before the inbox existed is not news")
        self.assertEqual(attention.reading()["unread"], 0)

    def test_the_baseline_leaves_what_still_waits_on_a_person_unread(self):
        self.observe_items([item("question:7:q1", "needs-answer", kind="question"),
                            item("brain:bd-1", "proposal-ready", kind="brain-draft"),
                            item("run:old", "failed")])
        found = self.items()
        self.assertTrue(found["question:7:q1"]["unread"], "an open question is not over")
        self.assertTrue(found["brain:bd-1"]["unread"], "a proposal waits on your review")
        self.assertFalse(found["run:old"]["unread"], "an old failure is over")

    def test_a_new_result_is_unread_once_however_often_it_is_seen(self):
        self.observe_items([])
        self.observe_items([item("run:a1", "running", key="running:0")])
        self.assertFalse(self.items()["run:a1"]["unread"], "running is not news")
        self.assertTrue(self.observe_items([item("run:a1", "finished")]))
        self.assertTrue(self.items()["run:a1"]["unread"])
        frames = len(self.frames)
        # a replayed frame, a reconnect, a second beat: the same transition
        self.assertFalse(self.observe_items([item("run:a1", "finished")]))
        self.assertFalse(self.observe_items([item("run:a1", "finished")]))
        self.assertEqual(len(self.frames), frames, "nothing changed, nothing announced")
        self.assertEqual(attention.reading()["unread"], 1)

    def test_acknowledging_an_older_key_never_hides_a_newer_transition(self):
        self.observe_items([])
        self.observe_items([item("ask:k-1", "answer-ready", key="answer-ready:q1:1", kind="ask")])
        seen = self.items()["ask:k-1"]["key"]
        # a follow-up is answered between the page's read and its click
        self.observe_items([item("ask:k-1", "answer-ready", key="answer-ready:q2:1", kind="ask")])
        out = attention.acknowledge({"items": [{"id": "ask:k-1", "key": seen}]})
        self.assertEqual(out["stale"], ["ask:k-1"])
        self.assertIn("stays unread", out["said"])
        self.assertTrue(self.items()["ask:k-1"]["unread"])
        attention.acknowledge({"items": [{"id": "ask:k-1", "key": "answer-ready:q2:1"}]})
        self.assertFalse(self.items()["ask:k-1"]["unread"])

    def test_acknowledgements_from_two_tabs_both_stand_and_repeat_as_one(self):
        self.observe_items([])
        self.observe_items([item("run:a", "failed"), item("run:b", "finished")])
        a = attention.acknowledge({"items": [{"id": "run:a", "key": "failed:1"}]})
        b = attention.acknowledge({"items": [{"id": "run:b", "key": "finished:1"}]})
        again = attention.acknowledge({"items": [{"id": "run:a", "key": "failed:1"}]})
        self.assertEqual((a["changed"], b["changed"], again["changed"]), (["run:a"], ["run:b"], []))
        self.assertEqual(attention.reading()["unread"], 0)
        # and the acknowledgement survives a restart
        attention.reset()
        self.assertEqual(attention.reading()["unread"], 0)
        self.assertEqual(len(attention.reading()["items"]), 2)

    def test_mark_unread_puts_one_back(self):
        self.observe_items([])
        self.observe_items([item("run:a", "failed")])
        attention.acknowledge({"items": [{"id": "run:a", "key": "failed:1"}]})
        attention.acknowledge({"items": [{"id": "run:a", "key": "failed:1"}], "unread": True})
        self.assertTrue(self.items()["run:a"]["unread"])

    def test_acknowledging_is_bounded_and_validated(self):
        for bad in (None, [], {"items": []}, {"items": "run:a"},
                    {"items": [{"id": "../x", "key": "k"}]},
                    {"items": [{"id": "run:a", "key": "k" * 300}]},
                    {"items": [{"id": "run:a", "key": "k"}] * (attention.MAX_ACK + 1)}):
            with self.assertRaises(attention.Refused):
                attention.acknowledge(bad)
        self.observe_items([])
        out = attention.acknowledge({"items": [{"id": "run:gone", "key": "failed:1"}]})
        self.assertEqual(out["missing"], ["run:gone"])

    def test_one_odd_id_does_not_refuse_the_whole_batch(self):
        self.observe_items([])
        odd = "question:12-naïve ~draft:q1"     # a card stem with a space, ~ and non-ASCII
        self.observe_items([item("run:a", "failed"),
                            item(odd, "needs-answer", key="needs-answer:b1", kind="question")])
        out = attention.acknowledge({"items": [{"id": "run:a", "key": "failed:1"},
                                               {"id": odd, "key": "needs-answer:b1"},
                                               {"id": "run:b", "key": "bad key!"}]})
        self.assertEqual(sorted(out["changed"]), sorted(["run:a", odd]))
        self.assertEqual(out["invalid"], ["run:b"])
        self.assertEqual(attention.reading()["unread"], 0)


class WhatASourceNoLongerHolds(Isolated):
    def test_an_item_that_left_its_source_says_so_and_a_question_stops_asking(self):
        self.observe_items([])
        self.observe_items([item("brain:bd-1", "proposal-ready", kind="brain-draft"),
                            item("question:7:q1", "needs-answer", kind="question"),
                            item("brain:bd-2", "running", key="running:1", kind="brain-draft")])
        self.observe_items([])
        found = self.items()
        self.assertTrue(found["brain:bd-1"]["gone"])
        self.assertEqual(found["brain:bd-1"]["state"], "proposal-ready",
                         "a vanished proposal is not promoted to applied")
        self.assertEqual(found["question:7:q1"]["state"], "resolved")
        self.assertFalse(found["question:7:q1"]["unread"])
        self.assertEqual(found["brain:bd-2"]["state"], "unknown",
                         "a run whose record went is unknown, never a success")

    def test_a_source_that_could_not_be_read_marks_nothing_gone(self):
        self.observe_items([])
        held = [item("run:a1", "running", key="running:0"),
                item("question:7-export:q1", "needs-answer", kind="question",
                     card={"file": "7-export.md"}),
                item("ask:k-1", "answer-ready", kind="ask", card={"file": "7-export.md"})]
        self.observe_items(held)
        before = self.items()
        frames = len(self.frames)
        # the run source raised; the card's records did not parse
        self.assertFalse(self.observe_items([], skipped={"run", "card:7-export.md"}))
        self.assertEqual(self.items(), before)
        self.assertEqual(len(self.frames), frames)
        self.assertEqual(self.items()["run:a1"]["state"], "running",
                         "a failed reading is not an unknown outcome")
        self.assertEqual(self.items()["question:7-export:q1"]["state"], "needs-answer")
        # one item whose own reading failed
        self.assertFalse(self.observe_items([held[0], held[1]], skipped={"id:ask:k-1"}))
        # and once the sources read again and do not hold them, they are gone
        self.observe_items([])
        self.assertEqual(self.items()["run:a1"]["state"], "unknown")

    def test_project_names_the_source_that_raised(self):
        with mock.patch.object(progress, "summaries", side_effect=OSError("disk")), \
                mock.patch.object(braindraft, "listing", side_effect=OSError("disk")), \
                mock.patch.object(refine, "listing", return_value=[]), \
                mock.patch.object(decisions, "board_conversations", return_value=[]):
            items, skipped = attention._project(BOARD)
        self.assertEqual(items, [])
        self.assertEqual(skipped, {"run", "brain"})


class Bounds(Isolated):
    def test_retention_and_the_cap_both_remove_and_both_are_counted(self):
        now = time.time()
        old = now - (config.ATTENTION_RETENTION_DAYS + 1) * 86400
        self.observe_items([], now=old)
        self.observe_items([item("run:old", "failed")], now=old)
        self.observe_items([item("run:old", "failed")], now=now)
        self.observe_items([], now=now)   # the old one is gone from its source and expired
        self.assertNotIn("run:old", self.items())
        self.assertEqual(attention.reading()["pruned"]["expired"], 1)
        with mock.patch.object(config, "ATTENTION_MAX", 20):
            first = [item(f"run:n{i:02}", "failed", at=attention._iso(now - 100 + i))
                     for i in range(20)]
            self.observe_items(first, now=now + 1)
            newer = [item(f"run:m{i}", "failed", at=attention._iso(now + i)) for i in range(5)]
            self.observe_items(newer, now=now + 2)
        self.assertEqual(len(self.items()), 20)
        self.assertEqual(attention.reading()["pruned"]["overflow"], 5,
                         "the five oldest records made way for five newer results")
        self.assertTrue(all(f"run:m{i}" in self.items() for i in range(5)))
        self.assertEqual(attention.reading()["bounds"]["retentionDays"],
                         config.ATTENTION_RETENTION_DAYS)

    def test_a_source_larger_than_the_cap_settles_and_stays_read(self):
        # the sources are unbounded (every run record on disk); the record
        # is not. Repeated readings must not re-admit what the cap removed
        now = time.time()
        source = [item(f"run:r{i:03}", "finished", key=f"finished:{i}",
                       at=attention._iso(now - 10_000 + i)) for i in range(30)]
        with mock.patch.object(config, "ATTENTION_MAX", 20):
            self.observe_items(source, now=now)          # the baseline
            self.assertEqual(attention.reading()["unread"], 0)
            self.assertEqual(attention.reading()["held"]["cap"], 10)
            frames, record = len(self.frames), attention.path().read_bytes()
            for beat in range(1, 6):
                self.assertFalse(self.observe_items(source, now=now + beat),
                                 "an unchanged source changes nothing")
            self.assertEqual(len(self.frames), frames, "and announces nothing")
            self.assertEqual(attention.path().read_bytes(), record, "and writes nothing")
            self.assertEqual(attention.reading()["unread"], 0)
            self.assertEqual(attention.reading()["pruned"]["overflow"], 0)
            kept = set(self.items())
            self.assertEqual(kept, {f"run:r{i:03}" for i in range(10, 30)},
                             "the newest by their own source time are the ones kept")
            # a new result arrives: it is listed, unread, and the oldest goes
            self.observe_items(source + [item("run:new", "failed", at=attention._iso(now + 9))],
                               now=now + 9)
            self.assertTrue(self.items()["run:new"]["unread"])
            self.assertEqual(attention.reading()["unread"], 1)
            self.assertNotIn("run:r010", self.items())
            # Mark all read stays read on the next beats
            attention.acknowledge({"items": [{"id": "run:new", "key": "failed:1"}]})
            for beat in range(10, 14):
                self.observe_items(source + [item("run:new", "failed",
                                                  at=attention._iso(now + 9))], now=now + beat)
            self.assertEqual(attention.reading()["unread"], 0)
            self.assertNotIn("run:r010", self.items(), "a removed item never comes back")

    def test_retention_does_not_bring_back_what_its_source_still_holds(self):
        day = 86400
        t0 = time.time() - 100 * day
        source = [item(f"run:o{i}", "finished", key=f"finished:{i}",
                       at=attention._iso(t0 - day)) for i in range(5)]
        self.observe_items(source, now=t0)                   # the baseline
        self.assertEqual(attention.reading()["unread"], 0)
        later = t0 + (config.ATTENTION_RETENTION_DAYS + 1) * day
        self.observe_items(source, now=later)                # expired together
        self.assertEqual(self.items(), {})
        self.assertEqual(attention.reading()["pruned"]["expired"], 5)
        self.observe_items(source, now=later + 1)            # now said as held back
        self.assertEqual(attention.reading()["held"]["retention"], 5)
        frames = len(self.frames)
        for k in range(1, 4):
            self.assertFalse(self.observe_items(source, now=later + k * day))
        self.assertEqual(len(self.frames), frames)
        self.assertEqual((self.items(), attention.reading()["unread"]), ({}, 0),
                         "nothing returns unread every thirty days")
        self.assertEqual(attention.reading()["pruned"]["expired"], 5)

    def test_a_record_this_version_cannot_read_is_set_aside_not_overwritten(self):
        config.ATTENTION_DIR.mkdir(parents=True)
        attention.path().write_text(json.dumps({"v": 99, "items": {}}), encoding="utf-8")
        attention.reset()
        self.observe_items([item("run:a", "failed")])
        kept = list(config.ATTENTION_DIR.glob("inbox.unreadable-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(json.loads(kept[0].read_text())["v"], 99)
        self.assertIn("could not be read", attention.reading()["note"])


class PrivateToThisInstallation(unittest.TestCase):
    def test_the_inbox_is_tool_state_never_work_state(self):
        # machine-local, beside the other runtime records — never in the
        # work root a sync shares, never in the application
        self.assertEqual(config.ATTENTION_DIR, config.STATE / "attention")
        self.assertFalse(str(config.ATTENTION_DIR).startswith(str(config.TASKS)))
        page = (CORE / "web" / "views" / "attention.js").read_text("utf-8")
        # the notification preference and what this browser announced stay
        # in the browser; nothing posts them
        self.assertIn("localStorage", page)
        self.assertEqual(page.count("post('"), 1)
        self.assertIn("post('/api/attention/ack'", page)
        self.assertNotIn("requestPermission", page.split("async function setNotify")[0],
                         "permission is asked only from the person's own opt-in")


class ReadingWritesNothing(Isolated):
    def test_the_get_route_reads_the_record_only(self):
        self.observe_items([])
        before = attention.path().read_bytes()
        with mock.patch.object(attention, "project") as projected:
            reply = {}

            class Req:
                def reply(self, status, body):
                    reply.update(status=status, body=body)
            routes.get_attention(Req())
        projected.assert_not_called()
        self.assertEqual(reply["status"], 200)
        self.assertEqual(attention.path().read_bytes(), before)
        self.assertIn("not synchronized", reply["body"]["scope"])

    def test_the_routes_are_a_read_and_a_browser_mutation(self):
        table = {(r.method, r.path): r.klass for r in routes.ROUTES}
        self.assertEqual(table[("GET", "/api/attention")], "read")
        self.assertEqual(table[("POST", "/api/attention/ack")], "browser-mutation")


class EachSourceSaysWhatItIs(Isolated):
    """The projection over the real sources' shapes."""

    def project(self, **patches):
        defaults = {"board_conversations": [], "drafts": {}, "refine": []}
        defaults.update(patches)
        with mock.patch.object(decisions, "board_conversations",
                               return_value=defaults["board_conversations"]), \
                mock.patch.object(braindraft, "listing", return_value=defaults["drafts"]), \
                mock.patch.object(refine, "listing", return_value=defaults["refine"]), \
                mock.patch.object(reasoning, "for_ask", return_value=defaults.get("jobs", [])):
            return {i["id"]: i for i in attention.project(BOARD)}

    def test_a_run_reads_its_progress_records_ending(self):
        progress.open_run("a-live", "7-export.md", "work", {"state": "unavailable"})
        progress.open_run("a-done", "7-export.md", "review", {"state": "unavailable"})
        progress.close_run("a-done", "finished")
        progress.open_run("a-cut", "7-export.md", "work", {"state": "unavailable"})
        progress.RUNS.clear()
        # a restart: the board that launched a-cut and a-live is gone
        progress.load()
        found = self.project()
        self.assertEqual(found["run:a-cut"]["state"], "interrupted")
        self.assertEqual(found["run:a-live"]["state"], "interrupted",
                         "running after a restart is never invented: its ending was not observed")
        self.assertEqual(found["run:a-done"]["state"], "finished")
        self.assertIn("not an acceptance", found["run:a-done"]["label"])
        self.assertEqual(found["run:a-done"]["open"],
                         {"agent": "run", "target": {"agent": "a-done", "card": "7-export.md"}})
        self.assertEqual(found["run:a-done"]["card"]["number"], "7")

    def test_a_running_run_is_running(self):
        progress.open_run("a-now", "7-export.md", "act-pr", {"state": "unavailable"})
        found = self.project()
        self.assertEqual(found["run:a-now"]["state"], "running")
        self.assertEqual(found["run:a-now"]["purpose"], "repair")

    def test_a_brain_proposal_is_not_applied_and_applied_is_not_shared(self):
        drafts = {"drafts": [
            {"id": "bd-1", "status": "proposed", "round": 1, "proposalRound": 1,
             "target": None, "request": "SECRET DRAFT TEXT", "updated": time.time()},
            {"id": "bd-2", "status": "applied", "round": 2, "proposalRound": 2,
             "target": "e-x", "updated": time.time()},
            {"id": "bd-3", "status": "questions", "round": 1, "updated": time.time()}],
            "evaluations": [{"id": "bd-4", "status": "proposed", "origin": "reconcile",
                             "round": 1, "updated": time.time()}],
            "seeds": [{"id": "bd-5", "status": "running", "round": 1, "updated": time.time()}]}
        found = self.project(drafts=drafts)
        self.assertEqual(found["brain:bd-1"]["state"], "proposal-ready")
        self.assertIn("not applied", found["brain:bd-1"]["label"])
        self.assertEqual(found["brain:bd-2"]["state"], "applied")
        self.assertIn("whether it is committed and shared is shown on the entry",
                      found["brain:bd-2"]["label"])
        self.assertEqual(found["brain:bd-2"]["href"], "/brain/e/e-x")
        self.assertEqual(found["brain:bd-3"]["state"], "needs-answer")
        self.assertEqual(found["brain:bd-4"]["owner"], "policy")
        self.assertEqual(found["brain:bd-4"]["open"], {"agent": "brainjob", "target": {"draft": "bd-4"}})
        self.assertEqual(found["brain:bd-5"]["href"], "/brain/seed")
        # a private draft's text never reaches the item, so never the record
        self.assertNotIn("SECRET", json.dumps(found))

    def test_an_answer_says_nothing_was_executed(self):
        conv = {"id": "k-abc", "title": "Why is 7 waiting?", "exchanges": [
            {"ask": {"id": "a-1", "at": "2026-11-26T10:00:00Z"},
             "replies": [{"status": "valid", "at": "2026-11-26T10:01:00Z"}]}]}
        found = self.project(board_conversations=[conv])
        a = found["ask:k-abc"]
        self.assertEqual(a["state"], "answer-ready")
        self.assertIn("nothing was executed", a["label"])
        self.assertEqual(a["open"], {"agent": "orch", "target": {"kind": "board"},
                                     "data": {"conv": "k-abc", "chosen": True}})
        conv["exchanges"].append({"ask": {"id": "a-2"}, "replies": []})
        running = self.project(board_conversations=[conv],
                               jobs=[{"id": "j", "status": "running"}])["ask:k-abc"]
        self.assertEqual(running["state"], "running")
        lost = self.project(board_conversations=[conv])["ask:k-abc"]
        self.assertEqual(lost["state"], "unknown", "a question with no run and no reply is unknown")

    def test_a_card_question_opens_the_slot_the_card_names(self):
        # review of 204: a zero-padded card's file number (`07`) is the
        # page's canonical `7`, so the inbox opens the card's own slot
        self.assertEqual(attention._card_id({"number": "07", "file": "07-export.md"}), "7")
        self.assertEqual(attention._card_id({"number": None, "file": "notes.md"}), "notes")
        task = {"file": "07-export.md", "number": "07", "title": "Export", "stage": "to-do",
                "body": '"kind": "ask"'}
        conv = {"id": "k-07", "title": "Why?", "exchanges": [
            {"ask": {"id": "a-1", "at": "2026-11-26T10:00:00Z"},
             "replies": [{"status": "valid", "at": "2026-11-26T10:01:00Z"}]}]}
        with mock.patch.object(decisions, "board_conversations", return_value=[]), \
                mock.patch.object(decisions, "read_text", return_value={"conversations": [conv]}), \
                mock.patch.object(reasoning, "for_ask", return_value=[]):
            item = attention._ask_items([task])[0]
        self.assertEqual(item["open"]["target"], {"kind": "card", "card": "7"})
        self.assertEqual(item["href"], "/orchestrator/ask?card=7")

    def test_a_refinement_proposal_leaves_the_brief_unchanged(self):
        jobs = [{"id": "rf-1", "task": "7-export.md", "status": "proposed", "created": 1},
                {"id": "rf-2", "task": "7-export.md", "status": "discarded", "created": 2,
                 "review": "rl-0a1b"}]
        found = self.project(refine=jobs)
        self.assertEqual(found["refine:rf-1"]["state"], "proposal-ready")
        self.assertIn("unchanged until you apply", found["refine:rf-1"]["label"])
        self.assertEqual(found["refine:rf-1"]["href"], "/cards/7?tab=brief")
        self.assertEqual(found["refine:rf-2"]["state"], "closed")
        self.assertEqual(found["refine:rf-2"]["open"]["target"],
                         {"card": "7-export.md", "review": "rl-0a1b"})

    def test_an_open_owner_question_needs_an_answer(self):
        reading ={"questions": [{"id": "q-1", "state": "open", "basis": "o-1",
                                  "text": "Include hidden columns?", "asked": "2026-11-26T10:00:00Z"},
                                 {"id": "q-2", "state": "answered", "basis": "o-2"}]}
        board = {"stages": [{"tasks": [{**BOARD["stages"][0]["tasks"][0],
                                         "body": 'bench:orchestration "kind": "question"'}]}]}
        with mock.patch.object(decisions, "read_text", return_value=reading):
            found = {i["id"]: i for i in attention._questions(board["stages"][0]["tasks"])}
        self.assertEqual(list(found), ["question:7-export:q-1"])
        self.assertEqual(found["question:7-export:q-1"]["state"], "needs-answer")
        self.assertEqual(found["question:7-export:q-1"]["href"], "/orchestrator/decisions?card=7")


class TheBeat(Isolated):
    def test_it_wakes_only_on_frames_that_announce_a_change(self):
        attention.WAKE.clear()
        attention.wake("event")
        attention.wake("attention")
        self.assertFalse(attention.WAKE.is_set(), "its own frame and session events do not wake it")
        attention.wake("progress")
        self.assertTrue(attention.WAKE.is_set())
        attention.WAKE.clear()

    def test_composition_registers_the_hook_once(self):
        import board
        hooks = list(state.BROADCAST_HOOKS)
        try:
            board.wire()
            board.wire()
            self.assertEqual(state.BROADCAST_HOOKS.count(attention.wake), 1)
        finally:
            state.BROADCAST_HOOKS[:] = hooks


if __name__ == "__main__":
    unittest.main()
