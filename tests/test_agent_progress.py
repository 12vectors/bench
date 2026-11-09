"""Agent-reported progress, and one record per raw event (task 73).

A stream of tool events proves activity, not progress. These tests hold
the contract that lets a run say what it has done, is doing, has left and
is blocked on — and the board keep that apart from everything it observed:

- the channel exists only where the adapter declares it, and is one
  executable plus a run-scoped token — no shell, no task write, no
  lifecycle action rides along;
- a report is validated whole at ingestion: identity, schema, limits,
  unsafe text, references; a refusal on a real run is counted on its
  channel health;
- replays, late arrivals, bursts and restarts leave the right current
  update, the right history, and no cross-run attribution;
- the run's ending supersedes "doing now";
- the duplicated tool events seen while bench developed bench came from
  two event bridges registered for one session, and are now recorded once
  — while distinct repeated edits and a tool's start/finish stay distinct.

The end-to-end cases launch the real claude adapter `run` with a stub
standing in for the vendor binary, so a worker, a reviewer and a repair
run each publish through exactly the rules a headless launch is given.

    python3 -m unittest tests.test_agent_progress -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "adapters" / "claude"))

import config  # noqa: E402
import events  # noqa: E402
import httpd  # noqa: E402
import progress  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

import hook_settings  # noqa: E402

sys.path.insert(0, str(CORE / "adapters" / "opencode"))
import permission_config  # noqa: E402

CARD = "73-show-meaningful-live-agent-progress-updates.md"
OTHER = "74-some-other-card.md"
AVAILABLE = {"state": "available", "why": "test"}


class Base(unittest.TestCase):
    """Every record, log and evidence file in a temporary state root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-progress-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name in ("PROGRESS_DIR", "SESSIONS_DIR", "EVIDENCE_DIR",
                     "PROGRESS_MIN_GAP", "PROGRESS_REMIND"):
            self.addCleanup(setattr, config, name, getattr(config, name))
        config.PROGRESS_DIR = self.tmp / "progress"
        config.SESSIONS_DIR = self.tmp / "sessions"
        config.EVIDENCE_DIR = self.tmp / "evidence"
        config.PROGRESS_MIN_GAP = 0.0
        config.PROGRESS_REMIND = 600.0
        for registry in (progress.RUNS, progress._NOTIFY, state.SESSIONS, state.EVENTS,
                         state.AGENTS, events._WRITTEN, events._SEEN,
                         events.DROPPED):
            registry.clear()
            self.addCleanup(registry.clear)
        events._BRIDGED.clear()
        self.addCleanup(events._BRIDGED.clear)
        self.broadcasts: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.broadcasts.append

    def open(self, agent="run-1", task=CARD, intent="work") -> str:
        token = progress.open_run(agent, task, intent, AVAILABLE)
        self.assertTrue(token)
        return token

    def send(self, token, agent="run-1", **fields):
        body = {"v": 1, "agent": agent, "token": token,
                "id": fields.pop("id", f"p-{time.monotonic_ns()}")}
        body.update(fields)
        return progress.report(body)

    def refused(self, token, code, agent="run-1", **fields) -> progress.Refused:
        with self.assertRaises(progress.Refused) as caught:
            self.send(token, agent=agent, **fields)
        self.assertEqual(caught.exception.code, code, caught.exception.why)
        return caught.exception


# ── the contract ────────────────────────────────────────────────────────


class Contract(Base):

    def test_a_report_is_recorded_whole_and_labelled_agent_reported(self):
        token = self.open()
        result = self.send(token, stage="UI", doing="adding the Focus panel",
                           done=["profiles wired", "focused tests pass"],
                           remaining=["full regression"],
                           narrative="Launch integration is committed.")
        self.assertEqual(result["result"], "recorded")
        found = progress.public("run-1", full=True)
        current = found["current"]
        self.assertEqual(current["source"], "agent-reported")
        self.assertEqual(current["doing"], "adding the Focus panel")
        self.assertEqual(current["done"], ["profiles wired", "focused tests pass"])
        self.assertTrue(found["active"])
        self.assertEqual([m["text"] for m in found["milestones"]],
                         ["profiles wired", "focused tests pass"])
        self.assertEqual(len(found["history"]), 1)

    def test_nothing_about_the_token_reaches_a_reader(self):
        token = self.open()
        self.send(token, doing="x")
        text = json.dumps([progress.public("run-1", full=True),
                           progress.board_public()])
        self.assertNotIn(token, text)
        self.assertNotIn("tokenHash", text)
        self.assertNotIn("seen", progress.public("run-1"))
        on_disk = (config.PROGRESS_DIR / "run-1.json").read_text()
        self.assertNotIn(token, on_disk)

    def test_an_omitted_field_is_not_reported_never_carried_over(self):
        token = self.open()
        self.send(token, doing="a", blocked="needs approval", remaining=["b"])
        self.send(token, doing="c")
        current = progress.public("run-1")["current"]
        self.assertNotIn("blocked", current)
        self.assertNotIn("remaining", current)

    def test_milestones_survive_a_later_update_that_does_not_repeat_them(self):
        token = self.open()
        self.send(token, done=["one"])
        self.send(token, done=["two", "one"])
        self.send(token, doing="three")
        self.assertEqual([m["text"] for m in progress.public("run-1")["milestones"]],
                         ["one", "two"])

    def test_the_prompt_names_the_one_command_and_says_what_an_update_is_not(self):
        block = progress.prompt_block("work", True)
        self.assertIn(str(progress.shim()), block)
        self.assertIn("agent-reported", block)
        self.assertIn("not evidence", block)
        self.assertIn("never ends the run", block)
        self.assertIn("not your closing report", block)
        self.assertEqual(progress.prompt_block("work", False), "")
        self.assertEqual(progress.prompt_block("refine", True), "")


class Channel(Base):

    def test_available_only_where_the_adapter_declares_it(self):
        declared = {"name": "claude",
                    "capabilities": {"progress-report": "prefix-allowlist"}}
        silent = {"name": "custom", "capabilities": {}}
        self.assertEqual(progress.channel("work", declared)["state"], "available")
        self.assertEqual(progress.channel("act-pr", declared)["state"], "available")
        self.assertEqual(progress.channel("review", declared)["state"], "available")
        self.assertEqual(progress.channel("refine", declared)["state"], "unavailable")
        missing = progress.channel("work", silent)
        self.assertEqual(missing["state"], "unavailable")
        self.assertIn("progress-report", missing["why"])

    def test_both_shipped_adapters_declare_it(self):
        for name in ("claude", "opencode"):
            data = json.loads((CORE / "adapters" / name / "capabilities.json").read_text())
            self.assertEqual(data["capabilities"]["progress-report"], "prefix-allowlist")

    def test_an_unavailable_channel_gets_no_token_and_refuses_reports(self):
        token = progress.open_run("run-x", CARD, "work",
                                  {"state": "unavailable", "why": "adapter"})
        self.assertIsNone(token)
        with self.assertRaises(progress.Refused) as caught:
            progress.report({"agent": "run-x", "token": "anything", "id": "p-12345678",
                             "doing": "x"})
        self.assertEqual(caught.exception.code, "unavailable")
        found = progress.public("run-x")
        self.assertEqual(found["channel"]["state"], "unavailable")
        self.assertIsNone(found["current"])

    def test_the_shim_is_shipped_executable(self):
        self.assertTrue(os.access(progress.shim(), os.X_OK))


class Identity(Base):

    def test_a_run_cannot_report_for_another_run(self):
        mine = self.open("run-a")
        self.open("run-b", task=OTHER)
        self.refused(mine, "identity", agent="run-b", doing="spoof")
        self.assertIsNone(progress.public("run-b")["current"])
        self.assertEqual(progress.public("run-b")["counts"]["rejected"], 1)
        self.assertEqual(progress.public("run-b")["lastRejection"]["code"], "identity")

    def test_an_unknown_run_and_a_missing_token_are_refused(self):
        with self.assertRaises(progress.Refused) as caught:
            progress.report({"agent": "nobody", "token": "x", "id": "p-12345678"})
        self.assertEqual(caught.exception.status, 404)
        self.open()
        with self.assertRaises(progress.Refused) as caught:
            progress.report({"agent": "run-1", "id": "p-12345678", "doing": "x"})
        self.assertEqual(caught.exception.code, "identity")

    def test_an_ended_run_takes_no_more_reports(self):
        token = self.open()
        self.send(token, doing="working")
        progress.close_run("run-1", "finished")
        self.refused(token, "closed", doing="still here")
        found = progress.public("run-1")
        self.assertFalse(found["active"])
        self.assertEqual(found["outcome"], "finished")
        self.assertEqual(found["current"]["doing"], "working")


class Validation(Base):

    def setUp(self):
        super().setUp()
        self.token = self.open()

    def test_malformed_reports_are_refused_and_counted(self):
        self.refused(self.token, "schema", doing=42)
        self.refused(self.token, "schema", doing="x", surprise="field")
        self.refused(self.token, "schema", done={"not": "a list"})
        self.refused(self.token, "schema", id="short", doing="x")
        self.refused(self.token, "empty")
        self.refused(self.token, "empty", doing="   ")
        self.assertEqual(progress.public("run-1")["counts"]["rejected"], 6)
        self.assertIsNone(progress.public("run-1")["current"])

    def test_oversized_fields_and_lists_are_refused(self):
        self.refused(self.token, "too-long", doing="x" * 281)
        self.refused(self.token, "too-long", narrative="x" * 801)
        self.refused(self.token, "too-long", done=["m"] * 13)
        self.refused(self.token, "too-long", done=["x" * 201])
        self.refused(self.token, "too-long", doing="x",
                     refs=[{"commit": "abcdef1"}] * 9)

    def test_unsafe_text_is_refused(self):
        self.refused(self.token, "unsafe", doing="bell\x07")
        self.refused(self.token, "unsafe", doing="esc \x1b[31m red")
        self.refused(self.token, "unsafe", doing="rtl ‮ override")

    def test_a_closing_marker_cannot_ride_in_on_an_update(self):
        for line in ("NOT READY: nope", "PR REVIEW: APPROVE",
                     "RELEVANCE REVIEW: Already done", "ADDRESSED: all of it"):
            self.refused(self.token, "marker", narrative=f"first\n{line}")

    def test_markup_is_kept_as_text_and_credentials_are_masked(self):
        self.send(self.token, doing="<script>alert(1)</script>",
                  narrative="token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 used")
        current = progress.public("run-1")["current"]
        self.assertEqual(current["doing"], "<script>alert(1)</script>")
        self.assertNotIn("ghp_abcdefghijklmnop", current["narrative"])
        self.assertIn("«redacted»", current["narrative"])

    def test_references_are_commits_or_this_cards_evidence_only(self):
        evidence = self.evidence("ev-mine", CARD, "failed")
        self.evidence("ev-theirs", OTHER, "passed")
        result = self.send(self.token, doing="x", refs=[
            {"commit": "ABCDEF1234"}, {"evidence": evidence},
            {"evidence": "ev-theirs"}, {"url": "https://example.com"},
            {"path": "/etc/passwd"}, {"commit": "not-a-sha"}])
        self.assertEqual(len(result["dropped"]), 4)
        refs = progress.public("run-1")["current"]["refs"]
        self.assertEqual([(r["kind"], r["id"]) for r in refs],
                         [("commit", "abcdef1234"), ("evidence", "ev-mine")])

    def test_a_narrative_pass_cannot_promote_failed_evidence(self):
        evidence = self.evidence("ev-mine", CARD, "failed")
        self.send(self.token, narrative="All tests passed.",
                  refs=[{"evidence": evidence}])
        ref = progress.public("run-1")["current"]["refs"][0]
        self.assertEqual(ref["outcome"], "failed")
        self.assertEqual(ref["source"], "bench")
        self.assertEqual(ref["check"], "unit")
        self.assertEqual(progress.public("run-1")["current"]["source"],
                         "agent-reported")

    def test_a_missing_evidence_record_reads_not_found_never_passed(self):
        evidence = self.evidence("ev-gone", CARD, "passed")
        self.send(self.token, doing="x", refs=[{"evidence": evidence}])
        verify.path_of(evidence).unlink()
        ref = progress.public("run-1")["current"]["refs"][0]
        self.assertFalse(ref["found"])
        self.assertNotIn("outcome", ref)

    def evidence(self, evidence_id, task, outcome):
        path = verify.path_of(evidence_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "v": 1, "id": evidence_id, "task": task, "check": "unit",
            "scope": None, "outcome": outcome,
            "source": {"commit": "a" * 40, "dirty": []}, "ended": time.time()}))
        return evidence_id


class Ordering(Base):

    def setUp(self):
        super().setUp()
        self.token = self.open()

    def test_a_repeated_request_is_answered_as_a_duplicate(self):
        first = self.send(self.token, id="p-same-0001", doing="one")
        again = self.send(self.token, id="p-same-0001", doing="one")
        self.assertEqual((first["result"], again["result"]), ("recorded", "duplicate"))
        found = progress.public("run-1", full=True)
        self.assertEqual(found["updates"], 1)
        self.assertEqual(found["counts"]["duplicates"], 1)

    def test_a_late_arrival_is_filed_without_displacing_the_newer_update(self):
        now = time.time()
        self.send(self.token, id="p-newer-001", at=now, doing="newer")
        result = self.send(self.token, id="p-older-001", at=now - 30, doing="older")
        self.assertEqual(result["result"], "filed")
        found = progress.public("run-1", full=True)
        self.assertEqual(found["current"]["doing"], "newer")
        self.assertEqual([h["doing"] for h in found["history"]], ["older", "newer"])

    def test_a_burst_is_coalesced_but_a_blocker_never_is(self):
        config.PROGRESS_MIN_GAP = 60.0
        now = time.time()
        self.send(self.token, at=now, doing="a")
        coalesced = self.send(self.token, at=now + 1, doing="b")
        self.assertEqual(coalesced["result"], "coalesced")
        blocked = self.send(self.token, at=now + 2, doing="c", blocked="needs approval")
        self.assertEqual(blocked["result"], "recorded")
        after = self.send(self.token, at=now + 3, doing="d")
        self.assertEqual(after["result"], "recorded")
        history = progress.public("run-1", full=True)["history"]
        self.assertEqual([h["doing"] for h in history], ["b", "c", "d"])
        self.assertEqual(progress.public("run-1")["current"]["doing"], "d")

    def test_a_run_is_capped(self):
        self.addCleanup(setattr, progress, "MAX_REPORTS", progress.MAX_REPORTS)
        progress.MAX_REPORTS = 3
        for n in range(3):
            self.send(self.token, doing=f"step {n}")
        refused = self.refused(self.token, "rate-limited", doing="one more")
        self.assertEqual(refused.status, 429)

    def test_the_history_is_bounded(self):
        for n in range(progress.HISTORY_CAP + 5):
            self.send(self.token, doing=f"step {n}")
        history = progress.public("run-1", full=True)["history"]
        self.assertEqual(len(history), progress.HISTORY_CAP)
        self.assertEqual(history[-1]["doing"], f"step {progress.HISTORY_CAP + 4}")


class Milestones(Base):
    """The checklist is kept on the record, apart from the history, so
    nothing that trims the history can take a reported milestone off it."""

    def setUp(self):
        super().setUp()
        self.token = self.open()

    def texts(self, agent="run-1"):
        return [m["text"] for m in progress.public(agent)["milestones"]]

    def test_done_then_doing_inside_the_gap_keeps_the_milestone(self):
        config.PROGRESS_MIN_GAP = 20.0
        now = time.time()
        self.send(self.token, at=now, done=["profiles wired"])
        result = self.send(self.token, at=now + 3, doing="starting UI")
        self.assertEqual(result["result"], "coalesced")
        history = progress.public("run-1", full=True)["history"]
        self.assertEqual(len(history), 1)
        self.assertNotIn("done", history[0])
        self.assertEqual(self.texts(), ["profiles wired"])
        self.assertAlmostEqual(progress.public("run-1")["milestones"][0]["at"], now, places=3)

    def test_a_milestone_outlives_the_history_cap(self):
        self.send(self.token, done=["the very first milestone"])
        for n in range(progress.HISTORY_CAP + 5):
            self.send(self.token, doing=f"step {n}")
        found = progress.public("run-1", full=True)
        self.assertEqual(len(found["history"]), progress.HISTORY_CAP)
        self.assertFalse(any(h.get("done") for h in found["history"]))
        self.assertEqual(self.texts(), ["the very first milestone"])

    def test_a_late_arrival_dates_a_milestone_by_its_earliest_report(self):
        progress.RUNS["run-1"]["opened"] -= 100      # a report's time is held after opening
        now = time.time()
        self.send(self.token, id="p-newer-001", at=now, done=["b", "a"])
        self.send(self.token, id="p-older-001", at=now - 30, done=["a"])
        found = progress.public("run-1")["milestones"]
        self.assertEqual([m["text"] for m in found], ["a", "b"])
        self.assertAlmostEqual(found[0]["at"], now - 30, places=3)
        # a later repeat does not move a milestone's first-seen time
        self.send(self.token, at=now + 10, done=["a"])
        self.assertAlmostEqual(progress.public("run-1")["milestones"][0]["at"],
                               now - 30, places=3)

    def test_a_replayed_report_adds_nothing(self):
        self.send(self.token, id="p-same-0001", done=["one"])
        self.send(self.token, id="p-same-0001", done=["one", "smuggled"])
        self.assertEqual(self.texts(), ["one"])

    def test_the_checklist_is_bounded_and_says_how_much_it_left_out(self):
        self.addCleanup(setattr, progress, "MILESTONE_CAP", progress.MILESTONE_CAP)
        progress.MILESTONE_CAP = 3
        now = time.time()
        self.send(self.token, at=now, done=["m1", "m2", "m3", "m4"])
        self.send(self.token, at=now + 1, done=["m4", "m5"])
        found = progress.public("run-1")
        self.assertEqual(self.texts(), ["m1", "m2", "m3"])
        self.assertEqual(found["milestonesOmitted"], 2)

    def test_milestones_persist_across_a_restart(self):
        config.PROGRESS_MIN_GAP = 20.0
        now = time.time()
        self.send(self.token, at=now, done=["kept"])
        self.send(self.token, at=now + 1, doing="coalesced over it")
        progress.RUNS.clear()
        progress.load()
        self.assertEqual(self.texts(), ["kept"])

    def test_a_legacy_record_rebuilds_its_checklist_from_its_history(self):
        self.send(self.token, done=["old one"])
        path = config.PROGRESS_DIR / "run-1.json"
        record = json.loads(path.read_text())
        del record["milestones"], record["milestonesOmitted"]
        path.write_text(json.dumps(record))
        # read straight off a legacy record, before any load
        self.assertEqual([m["text"] for m in progress.milestones(record)], ["old one"])
        progress.RUNS.clear()
        progress.load()
        self.assertEqual(self.texts(), ["old one"])
        self.assertIn("milestones", json.loads(path.read_text()))


class RefusalNotices(Base):

    def test_a_burst_of_refusals_is_counted_whole_but_broadcast_bounded(self):
        self.addCleanup(setattr, progress, "REJECT_NOTIFY_GAP", progress.REJECT_NOTIFY_GAP)
        progress.REJECT_NOTIFY_GAP = 0.2
        token = self.open()
        for _ in range(25):
            self.refused(token, "empty")
        self.assertEqual(progress.public("run-1")["counts"]["rejected"], 25)
        notices = lambda: [b for b in self.broadcasts if b.get("type") == "progress"]
        self.assertEqual(len(notices()), 1)
        deadline = time.time() + 3
        while len(notices()) < 2 and time.time() < deadline:
            time.sleep(0.02)
        # one trailing notice, so the last refusal of the burst is shown
        self.assertEqual(len(notices()), 2)
        time.sleep(0.3)
        self.refused(token, "empty")
        self.assertEqual(len(notices()), 3)

    def test_evidence_ids_are_validated_through_verifys_public_boundary(self):
        self.assertTrue(verify.is_evidence_id("ev:verification@4cfab7d84ca9"))
        self.assertFalse(verify.is_evidence_id("../etc/passwd"))
        self.assertFalse(verify.is_evidence_id(None))
        self.assertNotIn("verify._ID_OK", (CORE / "progress.py").read_text())


class Lifecycle(Base):

    def test_terminal_states_supersede_doing_now(self):
        for outcome in ("finished", "failed", "declined", "held"):
            agent = f"run-{outcome}"
            token = self.open(agent, task=f"{outcome}.md")
            self.send(token, agent=agent, doing="mid-flight")
            progress.close_run(agent, outcome)
            found = progress.public(agent)
            self.assertFalse(found["active"], outcome)
            self.assertEqual(found["outcome"], outcome)

    def test_a_restart_keeps_history_and_closes_what_it_cannot_observe(self):
        token = self.open()
        self.send(token, doing="before the restart", done=["one"])
        progress.RUNS.clear()
        self.assertEqual(progress.load(), 1)
        found = progress.public("run-1", full=True)
        self.assertEqual(found["outcome"], "unobserved")
        self.assertFalse(found["active"])
        self.assertEqual(found["current"]["doing"], "before the restart")
        self.assertEqual(len(found["history"]), 1)
        self.refused(token, "closed", doing="after")

    def test_a_new_run_does_not_inherit_the_old_runs_current_update(self):
        old = self.open("run-old")
        self.send(old, agent="run-old", doing="old work")
        progress.close_run("run-old", "failed")
        time.sleep(0.01)
        self.open("run-new")
        on_card = progress.board_public()[CARD]
        self.assertEqual(on_card["agent"], "run-new")
        self.assertIsNone(on_card["current"])
        self.assertTrue(on_card["active"])
        # the older run's account is still there, as its own history
        self.assertEqual(progress.public("run-old")["current"]["doing"], "old work")


class Reminder(Base):

    def test_once_per_interval_at_a_tool_boundary_and_never_after_the_end(self):
        token = self.open()
        opened = progress.RUNS["run-1"]["opened"]
        self.assertIsNone(progress.reminder("run-1", opened + 10))
        first = progress.reminder("run-1", opened + 601)
        self.assertIn(str(progress.shim()), first)
        self.assertIn("Do not stop", first)
        self.assertIsNone(progress.reminder("run-1", opened + 700))
        self.send(token, doing="x")
        progress.close_run("run-1", "finished")
        self.assertIsNone(progress.reminder("run-1", opened + 99999))

    def test_off_when_configured_off_or_no_channel(self):
        config.PROGRESS_REMIND = 0
        self.open()
        self.assertIsNone(progress.reminder("run-1", time.time() + 99999))
        config.PROGRESS_REMIND = 600
        progress.open_run("run-2", CARD, "work", {"state": "unavailable"})
        self.assertIsNone(progress.reminder("run-2", time.time() + 99999))

    def test_the_event_ingest_hands_it_back_to_the_bridge(self):
        self.open()
        state.AGENTS["run-1"] = {"id": "run-1", "task": CARD, "session": None,
                                 "status": "running", "name": "Wren", "model": None}
        progress.RUNS["run-1"]["opened"] -= 1000
        reply = events.ingest_event({"v": 1, "session": "s1", "kind": "edit",
                                     "summary": "edited x", "agent": "run-1",
                                     "id": "PostToolUse:t1"})
        self.assertIn("remind", reply)
        quiet = events.ingest_event({"v": 1, "session": "s1", "kind": "edit",
                                     "summary": "edited y", "agent": "run-1",
                                     "id": "PostToolUse:t2"})
        self.assertEqual(quiet, {})


# ── duplicate raw events ────────────────────────────────────────────────


class DuplicateEvents(Base):
    """The mechanism first, then the fix."""

    def test_the_mechanism_two_bridges_for_one_session(self):
        """A worktree of a repository that wires the bridge in its own
        `.claude/settings.json` (bench does) gets a second registration
        from the launch's `--settings`. The vendor de-duplicates identical
        hook commands only — and these two name different paths."""
        project = json.loads((REPO / ".claude" / "settings.json").read_text())
        launch = hook_settings.settings("work", [])
        def commands(doc):
            return [h["command"] for group in doc["hooks"]["PostToolUse"]
                    for h in group["hooks"]]
        self.assertTrue(any("emit.py" in c for c in commands(project)))
        self.assertTrue(any("emit.py" in c for c in commands(launch)))
        self.assertTrue(set(commands(project)).isdisjoint(commands(launch)),
                        "identical commands would have been de-duplicated by "
                        "the vendor; distinct ones run twice")

    def test_without_identity_two_copies_were_two_entries(self):
        """What the board did with the doubled posts before ids: kept both."""
        for _ in range(2):
            events.ingest_event({"v": 1, "session": "legacy", "kind": "edit",
                                 "summary": "edited manager/core/board.html",
                                 "file": "manager/core/board.html"})
        self.assertEqual(len(state.EVENTS["legacy"]), 2)

    def test_a_replayed_copy_with_the_same_id_is_recorded_once(self):
        raw = {"v": 1, "session": "s", "kind": "edit", "summary": "edited a",
               "id": "PostToolUse:toolu_1"}
        events.ingest_event(dict(raw))
        self.assertEqual(events.ingest_event(dict(raw)), {"duplicate": True})
        self.assertEqual(len(state.EVENTS["s"]), 1)
        self.assertEqual(events.DROPPED["s"], 1)

    def test_an_older_bridges_id_less_copy_is_dropped_once_ids_flow(self):
        events.ingest_event({"v": 1, "session": "s", "kind": "edit",
                             "summary": "edited a", "id": "PostToolUse:toolu_1"})
        events.ingest_event({"v": 1, "session": "s", "kind": "edit",
                             "summary": "edited a"})
        self.assertEqual(len(state.EVENTS["s"]), 1)

    def test_distinct_repeated_edits_and_check_runs_stay_distinct(self):
        for n in range(3):
            events.ingest_event({"v": 1, "session": "s", "kind": "edit",
                                 "summary": "edited board.html",
                                 "id": f"PostToolUse:toolu_e{n}"})
        for n in range(2):
            events.ingest_event({"v": 1, "session": "s", "kind": "check",
                                 "summary": "unit — 3 passed",
                                 "id": f"PostToolUse:toolu_c{n}"})
        self.assertEqual([e["kind"] for e in state.EVENTS["s"]],
                         ["edit", "edit", "edit", "check", "check"])

    def test_a_tools_start_and_finish_are_two_events(self):
        events.ingest_event({"v": 1, "session": "s", "kind": "command",
                             "summary": "running: make", "running": True,
                             "id": "PreToolUse:toolu_9"})
        reply = events.ingest_event({"v": 1, "session": "s", "kind": "command",
                                     "summary": "ran: make",
                                     "id": "PostToolUse:toolu_9"})
        self.assertNotIn("duplicate", reply)
        self.assertEqual(len(state.EVENTS["s"]), 1)       # the start is live-only
        self.assertEqual(len([b for b in self.broadcasts if b.get("type") == "event"]), 2)

    def test_a_replay_across_a_restart_is_still_one(self):
        raw = {"v": 1, "session": "s", "kind": "edit", "summary": "edited a",
               "id": "PostToolUse:toolu_1"}
        events.ingest_event(dict(raw))
        state.EVENTS.clear()
        state.SESSIONS.clear()
        events._SEEN.clear()
        events._BRIDGED.clear()
        self.assertEqual(events.ingest_event(dict(raw)), {"duplicate": True})

    def test_the_boards_own_report_event_does_not_make_a_session_suspect(self):
        events.ingest_event({"v": 1, "session": "oc", "kind": "report",
                             "summary": "report", "id": "board:report:run-1"})
        events.ingest_event({"v": 1, "session": "oc", "kind": "end",
                             "summary": "session ended"})
        self.assertEqual(len(state.EVENTS["oc"]), 2)


class Bridge(Base):
    """The claude bridge itself: a stable id, standing down when it is not
    the launch's canonical copy, and a reminder handed back as context."""

    def setUp(self):
        super().setUp()
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.port = server.server_address[1]

    def emit(self, script: Path, payload: dict, **env):
        full = {k: v for k, v in os.environ.items()
                if not k.startswith(("BOARD_", "AGENT_"))}
        full.update({"BOARD_PORT": str(self.port)}, **env)
        return subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                              capture_output=True, text=True, env=full, timeout=30)

    def payload(self, tool_use="toolu_42"):
        return {"session_id": "sess", "hook_event_name": "PostToolUse",
                "tool_name": "Edit", "tool_use_id": tool_use,
                "tool_input": {"file_path": "/x/board.html"}}

    def test_two_copies_of_the_bridge_record_one_event(self):
        canonical = CORE / "adapters" / "claude" / "emit.py"
        copy_dir = self.tmp / "worktree" / "manager" / "core" / "adapters" / "claude"
        copy_dir.mkdir(parents=True)
        copy = copy_dir / "emit.py"
        shutil.copy(canonical, copy)
        # both fire for one tool call, as the two registrations make them
        self.emit(canonical, self.payload(), BOARD_EMIT=str(canonical))
        self.emit(copy, self.payload(), BOARD_EMIT=str(canonical))
        self.assertEqual(len(state.EVENTS["sess"]), 1)
        # and even with no canonical named, the shared id records it once
        self.emit(canonical, self.payload("toolu_43"))
        self.emit(copy, self.payload("toolu_43"))
        self.assertEqual(len(state.EVENTS["sess"]), 2)
        self.assertEqual(state.EVENTS["sess"][0]["id"], "PostToolUse:toolu_42")

    def test_a_reminder_comes_back_as_post_tool_use_context(self):
        progress.open_run("run-1", CARD, "work", AVAILABLE)
        progress.RUNS["run-1"]["opened"] -= 1000
        state.AGENTS["run-1"] = {"id": "run-1", "task": CARD, "session": None,
                                 "status": "running", "name": "Wren", "model": None}
        done = self.emit(CORE / "adapters" / "claude" / "emit.py", self.payload(),
                         BOARD_AGENT_ID="run-1")
        out = json.loads(done.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("progress update", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(done.returncode, 0)


# ── adapters render one rule and nothing else ──────────────────────────


class AdapterRules(unittest.TestCase):
    SHIM = str(CORE / "bench-progress")

    def test_claude_renders_the_shim_only_when_handed_and_never_for_refine(self):
        for mode in ("work", "act-pr", "review"):
            before = hook_settings.settings(mode, ["make test"])
            after = hook_settings.settings(mode, ["make test"], progress=self.SHIM)
            added = set(after["permissions"]["allow"]) - set(before["permissions"]["allow"])
            self.assertEqual(added, {f"Bash({self.SHIM})", f"Bash({self.SHIM}:*)"}, mode)
            self.assertEqual(after["permissions"].get("deny"),
                             before["permissions"].get("deny"))
        refine = hook_settings.settings("refine", [], progress=self.SHIM)
        self.assertNotIn("permissions", refine)
        self.assertEqual(hook_settings.progress_rules("work", "relative/path"), [])

    def test_opencode_renders_the_shim_only_when_handed(self):
        for mode in ("work", "act-pr", "review"):
            before = permission_config.bash_rules(mode, [])
            after = permission_config.bash_rules(mode, [], progress=self.SHIM)
            self.assertEqual(set(after) - set(before), {self.SHIM, f"{self.SHIM} *"})
            # the mode's denies still come last, so they still win
            denies = permission_config.MODE_DENY_PATTERNS.get(mode, [])
            if denies:
                self.assertEqual(list(after)[-len(denies):], denies)
        self.assertEqual(permission_config.bash_rules("refine", [], progress=self.SHIM),
                         {"*": "deny"})


# ── end to end: a headless launch publishes through its own rules ──────


STUB = r'''#!/usr/bin/env python3
"""A stand-in for the vendor CLI. It does what a headless run may do and
nothing else: a command runs only if an allow rule in --settings covers
it, exactly as a run with nobody at the permission prompt."""
import json, os, subprocess, sys
args = sys.argv[1:]
settings = json.loads(args[args.index("--settings") + 1])
allow = settings.get("permissions", {}).get("allow", [])
def allowed(cmd):
    for rule in allow:
        body = rule[5:-1]
        if body.endswith(":*") and cmd.startswith(body[:-2]):
            return True
        if cmd == body:
            return True
    return False
shim = os.environ.get("AGENT_PROGRESS", "")
results = []
mode = os.environ["AGENT_MODE"]
for argv in ([shim, "--stage", "start", "--doing", f"{mode}: reading the task",
              "--remaining", "everything", "Approach: small steps."],
             [shim, "--stage", "build", "--done", "first milestone",
              "--doing", "second milestone", "--commit", "abcdef1"],
             [shim, "--blocked", "the suite needs approval this run cannot get",
              "--doing", "waiting", "Code is committed; tests have not run."]):
    cmd = " ".join(argv)
    if not allowed(cmd):
        results.append({"cmd": cmd, "denied": True})
        continue
    done = subprocess.run(argv, capture_output=True, text=True)
    results.append({"rc": done.returncode, "out": done.stdout, "err": done.stderr})
results.append({"bash_star": any(r in ("Bash(*)", "Bash") for r in allow),
                "allow": allow})
with open(os.environ["STUB_OUT"], "w") as fh:
    json.dump(results, fh)
print("closing report: the work is described here, after every update")
'''


class Headless(Base):

    def setUp(self):
        super().setUp()
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(setattr, state, "serve_port", state.serve_port)
        state.serve_port = server.server_address[1]
        stub = self.tmp / "claude-stub"
        stub.write_text(STUB)
        os.chmod(stub, 0o755)
        self.out = self.tmp / "stub-out.json"
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("BOARD_", "AGENT_"))}
        base.update(BOARD_CLAUDE_BIN=str(stub), STUB_OUT=str(self.out))
        self.addCleanup(setattr, config, "child_env", config.child_env)
        config.child_env = lambda: dict(base)
        self.addCleanup(setattr, config, "adapter_dir", config.adapter_dir)
        config.adapter_dir = lambda: CORE / "adapters" / "claude"
        import agents
        self.agents = agents

    def launch(self, mode: str, agent_id: str):
        log = self.tmp / f"{agent_id}.log"
        proc, log_file, _ = self.agents._launch(mode, "the prompt", self.tmp,
                                                agent_id, CARD, log)
        rc = proc.wait(timeout=60)
        log_file.close()
        return rc, log.read_text(), json.loads(self.out.read_text())

    def test_worker_reviewer_and_repair_runs_publish_without_approval(self):
        for mode in ("work", "review", "act-pr"):
            agent_id = f"fixture-{mode}"
            rc, log, results = self.launch(mode, agent_id)
            self.assertEqual(rc, 0, log)
            reports, rules = results[:3], results[3]
            self.assertTrue(all(r.get("rc") == 0 for r in reports), (mode, reports))
            self.assertFalse(rules["bash_star"])
            # the job went on after every update and ended on its own report
            self.assertIn("closing report", log)
            found = progress.public(agent_id, full=True)
            self.assertEqual(found["intent"], mode)
            self.assertEqual(len(found["history"]), 3, mode)
            self.assertEqual(found["current"]["blocked"],
                             "the suite needs approval this run cannot get")
            self.assertEqual([m["text"] for m in found["milestones"]], ["first milestone"])
            self.assertTrue(found["active"], "an update never ends the run")

    def test_an_adapter_without_the_capability_gets_no_channel(self):
        custom = self.tmp / "adapter"
        custom.mkdir()
        shutil.copy(CORE / "adapters" / "claude" / "run", custom / "run")
        shutil.copy(CORE / "adapters" / "claude" / "hook_settings.py", custom)
        shutil.copy(CORE / "adapters" / "claude" / "emit.py", custom)
        os.chmod(custom / "run", 0o755)
        config.adapter_dir = lambda: custom
        rc, _, results = self.launch("work", "fixture-legacy")
        self.assertEqual(rc, 0)
        self.assertTrue(all(r.get("denied") or r.get("rc") == 5 for r in results[:3]))
        found = progress.public("fixture-legacy")
        self.assertEqual(found["channel"]["state"], "unavailable")
        self.assertIsNone(found["current"])

    def test_the_shim_exits_by_kind_and_never_claims_a_record_it_lacks(self):
        token = self.open("run-s")
        shim = str(progress.shim())
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_"))}
        env.update(BOARD_PORT=str(state.serve_port), BOARD_AGENT_ID="run-s")
        ok = subprocess.run([shim, "--doing", "x"], env=dict(env, BOARD_PROGRESS_TOKEN=token),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        wrong = subprocess.run([shim, "--doing", "x"], env=dict(env, BOARD_PROGRESS_TOKEN="nope"),
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("does not belong to this run", wrong.stderr)
        none = subprocess.run([shim, "--doing", "x"], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(none.returncode, 5)
        gone = subprocess.run([shim, "--doing", "x"],
                              env=dict(env, BOARD_PROGRESS_TOKEN=token, BOARD_PORT="1"),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(gone.returncode, 5)
        self.assertIn("Carry on", gone.stderr)

    def test_an_oversized_body_is_refused_before_it_is_parsed(self):
        import urllib.error
        import urllib.request
        self.open("run-big")
        body = json.dumps({"agent": "run-big", "pad": "x" * (progress.MAX_BODY + 10)})
        request = urllib.request.Request(
            f"http://127.0.0.1:{state.serve_port}/api/progress", data=body.encode(),
            headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 413)

    def test_the_state_payload_and_the_timeline_endpoint_carry_it(self):
        import urllib.request
        token = self.open("run-p")
        self.send(token, agent="run-p", doing="x", done=["a"])
        with urllib.request.urlopen(
                f"http://127.0.0.1:{state.serve_port}/api/progress?file={CARD}",
                timeout=10) as response:
            found = json.loads(response.read())["progress"]
        self.assertEqual(found["agent"], "run-p")
        self.assertEqual(len(found["history"]), 1)
        self.assertEqual(progress.board_public()[CARD]["current"]["doing"], "x")


if __name__ == "__main__":
    unittest.main()
