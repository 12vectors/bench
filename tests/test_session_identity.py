"""A session remembers who it was (task 45).

`You` used to be the label the board reached for when it could not
attribute a session — and every session read back from disk was one of
those, because the agent id lived only in memory. Past agent runs came
back wearing the person's name.

So identity is persisted beside the event log, in a small whole file of
its own: the agent id, the agent's name and the model it rode (neither of
which is anywhere in the event stream), and the task. The label then has
three registers instead of two — the name, `You` for a session positively
known to have carried no agent, and a neutral `Session` for a log written
before any of this was recorded.

These tests drive the real ingest → persist → reload path with the
sessions directory pointed at a temporary one; the browser half (a
replayed run wearing its model chip) is the page's own functions run in
node, as in test_model_chip.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import events  # noqa: E402
import state  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"

TASK = "45-a-past-agent-session-is-not-you.md"


class Sessions(unittest.TestCase):
    """One temporary sessions directory; each test writes its own history."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-identity-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, config, "SESSIONS_DIR", config.SESSIONS_DIR)
        config.SESSIONS_DIR = self.tmp / "sessions"

        for registry in (state.SESSIONS, state.EVENTS, state.AGENTS,
                         events._WRITTEN):
            registry.clear()
            self.addCleanup(registry.clear)
        state.BOARD_EVENTS.clear()
        self.addCleanup(state.BOARD_EVENTS.clear)

        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda payload: None

    # ── the world the board sees ──────────────────────────────────────────

    def launch(self, agent_id: str, name: str, model: str | None,
               task: str = TASK) -> dict:
        """A launch record, as agents.py registers one."""
        record = {"id": agent_id, "task": task, "name": name, "model": model,
                  "session": None, "status": "running", "mode": "work"}
        state.AGENTS[agent_id] = record
        return record

    def ingest(self, sid: str, summary: str, agent: str | None = None,
               task: str | None = None, kind: str = "edit") -> None:
        event = {"v": 1, "session": sid, "kind": kind, "summary": summary}
        if agent:
            event["agent"] = agent
        if task:
            event["task"] = task
        events.ingest_event(event)

    def restart(self) -> None:
        """What a board restart costs: every registry goes, the disk stays."""
        for registry in (state.SESSIONS, state.EVENTS, state.AGENTS,
                         events._WRITTEN):
            registry.clear()
        events.load_disk_sessions()

    def label(self, sid: str) -> str:
        return state.SESSIONS[sid]["label"]

    # ── the bug ───────────────────────────────────────────────────────────

    def test_an_agent_session_replayed_after_a_restart_is_still_the_agent(self):
        """Acceptance 1: the row carries the agent's name and task, not You."""
        self.launch("45-a-past-agent-120000", "Nell", "claude-opus-4-8")
        self.ingest("sess-nell", "editing events.py", agent="45-a-past-agent-120000")
        self.ingest("sess-nell", "Nell's report on " + TASK, kind="report",
                    agent="45-a-past-agent-120000")
        self.assertEqual(self.label("sess-nell"), "Nell · #45")

        self.restart()
        meta = state.SESSIONS["sess-nell"]
        self.assertEqual(meta["label"], "Nell · #45")
        self.assertEqual(meta["task"], TASK)
        self.assertEqual(meta["agentId"], "45-a-past-agent-120000")
        self.assertEqual(meta["agentModel"], "claude-opus-4-8")

    def test_a_human_session_replayed_from_disk_still_reads_you(self):
        """Acceptance 2: this fix does not relabel the person's own work."""
        self.ingest("sess-mine-0123456789", "reading AGENTS.md")
        self.assertEqual(self.label("sess-mine-0123456789"), "You · sess-min")

        self.restart()
        self.assertEqual(self.label("sess-mine-0123456789"), "You · sess-min")

    def test_a_log_from_before_identities_were_recorded_claims_nothing(self):
        """Acceptance 3: no identity on disk means unknown, not you. Old
        logs are not retro-attributed in either direction."""
        config.SESSIONS_DIR.mkdir(parents=True)
        (config.SESSIONS_DIR / "sess-oldrun.jsonl").write_text(
            json.dumps({"ts": 1, "session": "sess-oldrun", "kind": "edit",
                        "summary": "editing board.html"}) + "\n"
            + json.dumps({"ts": 2, "session": "sess-oldrun", "kind": "report",
                          "summary": f"Piper's report on {TASK}"}) + "\n",
            encoding="utf-8")

        events.load_disk_sessions()
        meta = state.SESSIONS["sess-oldrun"]
        self.assertEqual(meta["label"], "Session · sess-old")
        self.assertNotIn("You", meta["label"])
        self.assertIsNone(meta["agentId"])

    def test_restarting_the_board_changes_no_label(self):
        """Acceptance 4, across all three registers at once."""
        self.launch("review-45-a-past-agent-130000", "Piper", None)
        self.ingest("sess-piper", "reading the card",
                    agent="review-45-a-past-agent-130000")
        self.ingest("sess-mine", "running the tests")
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        (config.SESSIONS_DIR / "sess-old.jsonl").write_text(
            json.dumps({"ts": 1, "session": "sess-old", "kind": "edit",
                        "summary": "from before"}) + "\n", encoding="utf-8")
        events.load_disk_sessions()

        before = {sid: m["label"] for sid, m in state.SESSIONS.items()}
        self.restart()
        after = {sid: m["label"] for sid, m in state.SESSIONS.items()}
        self.assertEqual(before, after)
        self.assertEqual(before["sess-piper"], "Piper · #45")

    def test_an_agent_id_that_arrives_late_relabels_the_session(self):
        """Acceptance 6: events can precede the id; the label catches up,
        and what it catches up to is what the restart reads back."""
        self.launch("45-a-past-agent-140000", "Reed", "claude-sonnet-5")
        self.ingest("sess-late", "session started", kind="session")
        self.assertEqual(self.label("sess-late"), "You · sess-lat")

        self.ingest("sess-late", "editing state.py", agent="45-a-past-agent-140000")
        self.assertEqual(self.label("sess-late"), "Reed · #45")

        self.restart()
        self.assertEqual(self.label("sess-late"), "Reed · #45")

    def test_a_name_the_board_never_saw_falls_back_to_agent_not_to_you(self):
        """Three states, three words: `Agent` when the id is known and the
        name is not is the one thing `You` must never be said of."""
        config.SESSIONS_DIR.mkdir(parents=True)
        (config.SESSIONS_DIR / "sess-nameless.jsonl").write_text(
            json.dumps({"ts": 1, "session": "sess-nameless", "kind": "edit",
                        "summary": "worked"}) + "\n", encoding="utf-8")
        state.persist_identity("sess-nameless", {
            "agentId": "45-a-past-agent-150000", "name": None,
            "model": None, "task": TASK})

        events.load_disk_sessions()
        self.assertEqual(self.label("sess-nameless"), "Agent · #45")

    def test_a_review_agent_without_a_name_keeps_saying_review(self):
        config.SESSIONS_DIR.mkdir(parents=True)
        (config.SESSIONS_DIR / "sess-rev.jsonl").write_text(
            json.dumps({"ts": 1, "session": "sess-rev", "kind": "read",
                        "summary": "read the card"}) + "\n", encoding="utf-8")
        state.persist_identity("sess-rev", {
            "agentId": "review-45-a-past-agent-160000", "name": None,
            "model": None, "task": TASK})

        events.load_disk_sessions()
        self.assertEqual(self.label("sess-rev"), "Review · #45")

    # ── the file itself ───────────────────────────────────────────────────

    def test_the_identity_is_a_file_of_its_own_beside_the_log(self):
        """The risk the card names: the logs are append-only JSONL whose
        first line every reader takes for an event. Identity must not be a
        header line, and must not be read back as a session of its own."""
        self.launch("45-a-past-agent-170000", "Wren", "claude-opus-4-8")
        self.ingest("sess-wren", "editing", agent="45-a-past-agent-170000")

        log = config.SESSIONS_DIR / "sess-wren.jsonl"
        first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first["kind"], "edit")
        self.assertEqual(
            json.loads((config.SESSIONS_DIR / "sess-wren.who.json")
                       .read_text(encoding="utf-8")),
            {"agentId": "45-a-past-agent-170000", "name": "Wren",
             "model": "claude-opus-4-8", "task": TASK})

        self.restart()
        self.assertEqual(list(state.SESSIONS), ["sess-wren"])

    def test_a_corrupt_identity_file_reads_as_unknown_not_as_a_crash(self):
        config.SESSIONS_DIR.mkdir(parents=True)
        (config.SESSIONS_DIR / "sess-bad.jsonl").write_text(
            json.dumps({"ts": 1, "session": "sess-bad", "kind": "edit",
                        "summary": "worked"}) + "\n", encoding="utf-8")
        (config.SESSIONS_DIR / "sess-bad.who.json").write_text(
            "half a fi", encoding="utf-8")

        events.load_disk_sessions()
        self.assertEqual(self.label("sess-bad"), "Session · sess-bad")

    def test_the_sidecar_is_rewritten_only_when_what_we_know_changes(self):
        """It is written whole on every change, so a session that says the
        same thing a hundred times must not rewrite it a hundred times."""
        self.launch("45-a-past-agent-180000", "Juno", None)
        writes: list[tuple[str, dict]] = []
        real = state.persist_identity
        self.addCleanup(setattr, state, "persist_identity", real)
        state.persist_identity = lambda sid, identity: (
            writes.append((sid, identity)), real(sid, identity))[1]

        self.ingest("sess-juno", "one")                                   # you
        self.ingest("sess-juno", "two", agent="45-a-past-agent-180000")   # linked
        for n in range(5):
            self.ingest("sess-juno", f"more {n}", agent="45-a-past-agent-180000")
        self.assertEqual([i["name"] for _, i in writes], [None, "Juno"])

    def test_a_running_event_persists_nothing_at_all(self):
        """Running events are the live line, not history — they are not in
        the log, so they must not conjure an identity file beside it."""
        events.ingest_event({"v": 1, "session": "sess-live", "kind": "command",
                             "summary": "npm test", "running": True})
        self.assertFalse((config.SESSIONS_DIR / "sess-live.who.json").exists())
        self.assertFalse((config.SESSIONS_DIR / "sess-live.jsonl").exists())


# The page's own functions, run as the browser runs them — the chip on a
# replayed run is the visible half of this fix.
PARTS = (
    r"const esc = \(s\) =>.*?\}\[c\]\)\);",
    r"function agentFor\(sid\) \{.*?\n\}",
    r"function shortModel\(model\) \{.*?\n\}",
    r"function modelChip\(agent\) \{.*?\n\}",
)


class ReplayedChip(unittest.TestCase):
    """Acceptance 5: a replayed agent session wears its model chip — the
    same way every time, from what was persisted rather than from what
    happens to be in this board's memory."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — chip behaviour unrun")
        html = BOARD.read_text(encoding="utf-8")
        out = []
        for pattern in PARTS:
            m = re.search(pattern, html, re.S)
            if m is None:
                raise AssertionError(f"board.html no longer defines {pattern!r}")
            out.append(m.group(0))
        cls.src = "\n".join(out)

    def chip(self, sessions: list, agents: list, sid: str) -> str:
        script = (f"const S = {{ state: {json.dumps({'sessions': sessions, 'agents': agents})} }};\n"
                  + self.src
                  + f"\nconsole.log(JSON.stringify(modelChip(agentFor({json.dumps(sid)}))));")
        out = subprocess.run([self.node, "-e", script],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_replayed_agent_session_wears_the_model_it_rode(self):
        chip = self.chip(
            [{"id": "s1", "agentId": "45-x-120000", "agentName": "Nell",
              "agentModel": "claude-opus-4-8"}], [], "s1")
        self.assertIn(">opus-4-8</span>", chip)

    def test_a_replayed_session_that_inherited_the_default_still_says_nothing(self):
        chip = self.chip(
            [{"id": "s1", "agentId": "45-x-120000", "agentName": "Nell",
              "agentModel": None}], [], "s1")
        self.assertEqual(chip, "")

    def test_the_persons_own_replayed_session_has_no_run_behind_it(self):
        self.assertEqual(
            self.chip([{"id": "s1", "agentId": None}], [], "s1"), "")

    def test_a_live_record_still_wins_over_the_persisted_one(self):
        """The live record knows more (status, branch); the sidecar is the
        fallback, not a second source of truth."""
        chip = self.chip(
            [{"id": "s1", "agentId": "45-x-120000", "agentName": "Nell",
              "agentModel": "claude-opus-4-8"}],
            [{"id": "45-x-120000", "session": "s1", "name": "Nell",
              "model": "claude-sonnet-5", "status": "running"}], "s1")
        self.assertIn(">sonnet-5</span>", chip)


if __name__ == "__main__":
    unittest.main()
