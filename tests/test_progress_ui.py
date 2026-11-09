"""What the page draws of agent-reported progress (task 73).

The progress renderers are lifted out of board.html and run in node with
fixture state, as test_model_chip runs the model chip: milestone
progression, quiet-but-active, reporting unavailable, a genuine blocker,
verification start and end, a terminal failure, a new run after an old
one, and references to bench's own evidence. Every case checks the words,
because the words carry the meaning — and that nothing invents a reason
for silence or a percentage from anything.

Skipped where node is absent.

    python3 -m unittest tests.test_progress_ui -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"
CARD = "73-progress.md"

PARTS = (
    r"const esc = \(s\) =>.*?\}\[c\]\)\);",
    r"function fmtClock\(ts\) \{.*?\n\}",
    r"function fmtShort\(ts\) \{.*?\n\}",
    r"function ago\(ts\) \{.*?\n\}",
    r"const PROGRESS_QUIET = \d+;",
    r"function progressOf\(agentId, file\) \{.*?\n\}",
    r"const PROCESS_WORDS = \{.*?\};",
    r"const ENDED_WORDS = \{.*?\};",
    r"function processWord\(agent, p\) \{.*?\n\}",
    r"function progressSentence\(p, agent, tool\) \{.*?\n\}",
    r"function progressWell\(task, agent\) \{.*?\n\}",
    r"function progressRef\(ref\) \{.*?\n\}",
    r"function progressSection\(agent, meta, task\) \{.*?\n\}",
)


def _harness() -> str:
    html = BOARD.read_text(encoding="utf-8")
    out = []
    for pattern in PARTS:
        found = re.search(pattern, html, re.S)
        if found is None:
            raise AssertionError(f"board.html no longer defines {pattern!r}")
        out.append(found.group(0))
    return "\n".join(out)


NOW = time.time()


def run(agent_id="run-1", status="running", **extra):
    return {"id": agent_id, "task": CARD, "status": status, "session": "s1",
            "name": "Wren", **extra}


def progress(agent_id="run-1", *, current=None, active=True, outcome=None,
             channel="available", last=None, history=None, milestones=None,
             counts=None, rejection=None):
    return {"agent": agent_id, "task": CARD, "intent": "work",
            "channel": {"state": channel, "why": "the custom adapter does not declare 'progress-report'"},
            "opened": NOW - 3600, "closed": None if active else NOW - 120,
            "outcome": outcome, "active": active, "current": current,
            "lastReport": last, "lastReceived": last,
            "counts": counts or {"accepted": len(history or []), "coalesced": 0,
                                 "duplicates": 0, "rejected": 0},
            "lastRejection": rejection,
            "updates": len(history or []), "milestones": milestones or [],
            "history": history or []}


class ProgressUI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — progress rendering unrun")
        cls.src = _harness()

    def setUp(self):
        # the fixtures' clock is the test's own: in a long suite, minutes can
        # pass between import and the case, and "25m ago" must stay 25m
        global NOW
        NOW = time.time()

    def call(self, expr: str, *, p=None, tool=None, board_events=None) -> str:
        state = {"progress": {CARD: p} if p else {},
                 "boardEvents": board_events or []}
        events = [{"ts": tool, "kind": "edit", "summary": "edited x"}] if tool else []
        script = (
            f"const S = {{state: {json.dumps(state)}, progressFull: {{}},"
            f" events: {{s1: {json.dumps(events)}}}, openFolds: new Set()}};\n"
            "function fetchProgress() {}\n" + self.src +
            f"\nconsole.log(JSON.stringify({expr}));")
        out = subprocess.run([self.node, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def section(self, agent, p, tool=None, **kw):
        meta = {"id": "s1", "last": tool, "task": CARD}
        return self.call(f"progressSection({json.dumps(agent)}, {json.dumps(meta)}, "
                         f"{{file: {json.dumps(CARD)}}})", p=p, tool=tool, **kw)

    def assertHonest(self, html):
        lowered = html.lower()
        for invented in ("stuck", "thinking", "%"):
            self.assertNotIn(invented, lowered)

    # ── milestone progression ────────────────────────────────────────────

    def test_milestones_doing_remaining_are_drawn_and_labelled(self):
        current = {"id": "p2", "at": NOW - 60, "stage": "UI", "doing": "adding the panel",
                   "remaining": ["full regression"], "done": ["profiles wired"],
                   "narrative": "Launch integration is committed.", "source": "agent-reported"}
        html = self.section(run(), progress(current=current, last=NOW - 60,
                            milestones=[{"text": "profiles wired", "at": NOW - 300},
                                        {"text": "focused tests pass", "at": NOW - 120}],
                            history=[current]), tool=NOW - 5)
        for words in ("agent-reported", "Doing now", "adding the panel", "profiles wired",
                      "focused tests pass", "full regression", "Launch integration is committed.",
                      "Last progress update", "Last tool activity", "Process</b> running",
                      "Progress timeline (1 update)"):
            self.assertIn(words, html)
        self.assertIn("none reported", html)          # no blocker was reported
        self.assertHonest(html)

    # ── silence, said as silence ─────────────────────────────────────────

    def test_quiet_but_active_says_so_and_invents_nothing(self):
        current = {"id": "p1", "at": NOW - 1500, "doing": "writing tests"}
        html = self.section(run(), progress(current=current, last=NOW - 1500,
                                            history=[current]), tool=NOW - 10)
        self.assertIn("No recent progress update (last 25m ago); tool activity continues.", html)
        self.assertHonest(html)

    def test_no_update_yet(self):
        html = self.section(run(), progress(), tool=NOW - 10)
        self.assertIn("No progress update yet; tool activity continues.", html)
        quiet = self.section(run(), progress(), tool=NOW - 5000)
        self.assertIn("No progress update yet.", quiet)
        self.assertNotIn("tool activity continues", quiet)

    def test_reporting_unavailable_is_its_own_state(self):
        html = self.section(run(), progress(channel="unavailable"), tool=NOW - 10)
        self.assertIn("Reporting unavailable", html)
        self.assertIn("does not declare", html)
        self.assertNotIn("Doing now", html)
        self.assertHonest(html)

    def test_a_run_with_no_record_says_that_rather_than_nothing(self):
        html = self.section(run(), None, tool=NOW - 10)
        self.assertIn("No progress was recorded for this run", html)

    # ── a genuine blocker ────────────────────────────────────────────────

    def test_a_blocker_is_drawn_in_words_on_the_card_and_in_focus(self):
        current = {"id": "p3", "at": NOW - 30, "doing": "waiting",
                   "blocked": "the suite needs approval this run cannot get"}
        p = progress(current=current, last=NOW - 30, history=[current])
        html = self.section(run(), p, tool=NOW - 10)
        self.assertIn('class="blocked"', html)
        self.assertIn("The run reports that it is blocked.", html)
        well = self.call(f"progressWell({{file: {json.dumps(CARD)}}}, {json.dumps(run())})", p=p)
        self.assertIn("Blocked: the suite needs approval", well)
        self.assertIn("agent-reported", well)

    # ── verification start and end, from bench ───────────────────────────

    def test_bench_verification_events_join_the_timeline_labelled_bench(self):
        current = {"id": "p1", "at": NOW - 400, "doing": "running the suite"}
        board = [{"kind": "verify", "file": CARD, "ts": NOW - 390, "summary": "verifying unit"},
                 {"kind": "verify", "file": CARD, "ts": NOW - 100, "summary": "unit — passed"}]
        html = self.section(run(), progress(current=current, last=NOW - 400, history=[current]),
                            tool=NOW - 10, board_events=board)
        self.assertIn("2 verification events", html)
        self.assertIn('<span class="src">bench</span><span>unit — passed', html)
        self.assertIn('<span class="src">agent-reported</span>', html)

    # ── evidence, not narrative ──────────────────────────────────────────

    def test_narrative_success_never_reads_as_a_pass(self):
        current = {"id": "p1", "at": NOW - 30, "narrative": "All tests passed.",
                   "refs": [{"kind": "evidence", "id": "ev-1", "found": True, "check": "unit",
                             "scope": None, "outcome": "failed", "recorded": "failed",
                             "commit": "abcdef123456", "source": "bench"},
                            {"kind": "evidence", "id": "ev-2", "found": True, "check": "unit",
                             "scope": "fast", "outcome": "stale", "recorded": "passed",
                             "reason": "source-changed", "commit": "abcdef123456"},
                            {"kind": "evidence", "id": "ev-3", "found": False}]}
        html = self.section(run(), progress(current=current, last=NOW - 30, history=[current]))
        self.assertIn('<b class="bad">failed</b>', html)
        self.assertIn("recorded passed, now source-changed", html)
        self.assertIn("scope fast", html)
        self.assertIn("full run", html)
        self.assertIn("not found on this board", html)
        self.assertIn("recorded by bench", html)
        self.assertNotIn('class="ok">passed', html)

    # ── terminal states ──────────────────────────────────────────────────

    def test_a_failed_run_stops_looking_active(self):
        current = {"id": "p1", "at": NOW - 600, "doing": "half way"}
        p = progress(current=current, last=NOW - 600, active=False, outcome="failed",
                     history=[current])
        html = self.section(run(status="failed"), p, tool=NOW - 200)
        self.assertIn("The run failed", html)
        self.assertIn("history now, not what it is doing", html)
        self.assertIn("Last said", html)
        self.assertNotIn("Doing now", html)
        self.assertIn("Process</b> failed", html)
        well = self.call(f"progressWell({{file: {json.dumps(CARD)}}}, "
                         f"{json.dumps(run(status='failed'))})", p=p)
        self.assertEqual(well, "")

    def test_a_restarted_board_says_it_did_not_see_the_end(self):
        p = progress(current={"id": "p1", "at": NOW - 900, "doing": "x"}, last=NOW - 900,
                     active=False, outcome="unobserved")
        html = self.section({"id": "run-1", "replayed": True}, p)
        self.assertIn("ended unobserved — the board restarted", html)

    def test_a_new_run_never_wears_the_old_runs_progress(self):
        p = progress("run-old", current={"id": "p1", "at": NOW - 60, "doing": "old"},
                     last=NOW - 60)
        well = self.call(f"progressWell({{file: {json.dumps(CARD)}}}, "
                         f"{json.dumps(run('run-new'))})", p=p)
        self.assertEqual(well, "")
        html = self.section(run("run-new"), p)
        self.assertNotIn(">old<", html)

    # ── safe rendering ───────────────────────────────────────────────────

    def test_reported_text_is_text(self):
        current = {"id": "p1", "at": NOW - 30, "doing": "<img src=x onerror=alert(1)>",
                   "stage": "<b>x</b>"}
        p = progress(current=current, last=NOW - 30, history=[current])
        for html in (self.section(run(), p),
                     self.call(f"progressWell({{file: {json.dumps(CARD)}}}, {json.dumps(run())})", p=p)):
            self.assertNotIn("<img", html)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_channel_health_counts_refusals_separately(self):
        p = progress(counts={"accepted": 3, "coalesced": 1, "duplicates": 2, "rejected": 1},
                     rejection={"why": "the report's token does not belong to this run"})
        html = self.section(run(), p)
        self.assertIn("Reporting channel: available · 3 updates received · 1 coalesced · "
                      "2 duplicates ignored · 1 refused", html)


class ProgressPlacement(unittest.TestCase):
    """Where it is drawn, read from the page as written."""

    def setUp(self):
        self.html = BOARD.read_text(encoding="utf-8")

    def test_card_focus_and_sessions_all_carry_it(self):
        self.assertIn("liveLine = progressWell(task, agent) + liveLine;", self.html)
        self.assertIn("progressSection(agent, meta, task) +", self.html)
        self.assertIn("['progress', 'Progress updates', new Set(['progress']), 'session']",
                      self.html)

    def test_the_activity_bar_does_not_offer_a_filter_it_cannot_fill(self):
        self.assertIn("FILTERS.filter(f => f[3] !== 'session')", self.html)

    def test_a_progress_message_rereads_rather_than_patches(self):
        self.assertIn("msg.type === 'progress'", self.html)


if __name__ == "__main__":
    unittest.main()
