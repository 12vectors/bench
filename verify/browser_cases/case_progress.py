"""Agent-reported progress, measured in a browser (task 73).

The disposable backend serves the five fixture cards and runs no agent, so
the run behind card 03 is a fake service: the page's own `fetch` is
wrapped to add one running agent, its session, its events and its
progress to what the backend returns — nothing is launched, nothing is
billed, and no operator session is involved. Each state the task names is
drawn, measured and photographed: milestone progression, quiet activity, a
genuine blocker, verification start and end, a terminal failure, and a
reconnect that brings a newer update. Run through verify/browser.
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
CARD = "03-measure-the-board-layout.md"
AGENT = "fx-run-1"
SID = "fx-session-0001"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def fixture(now: float, *, current=None, history=None, milestones=None, active=True,
            outcome=None, status="running", tool_ago=5, verify=()):
    agent = {"id": AGENT, "task": CARD, "status": status, "session": SID,
             "name": "Wren", "mode": "work", "branch": "task/03-measure-the-board-layout",
             "worktree": "/tmp/fixture", "started": now - 1800, "rc": None if active else 1,
             "model": None, "ended": None if active else now - 60,
             "failure": None if active else {"rc": 1, "ended": now - 60,
                                              "excerpt": "API Error: 500", "stage": "in-progress",
                                              "mode": "work"}}
    session = {"id": SID, "label": "Wren · #03", "agentId": AGENT, "agentName": "Wren",
               "agentModel": None, "task": CARD, "started": now - 1800, "last": now - tool_ago,
               "count": 3, "status": "active" if active else "ended", "known": True,
               "lastSummary": "edited manager/core/board.html", "lastKind": "edit"}
    events = [{"ts": now - 900, "session": SID, "kind": "read", "summary": "read AGENTS.md",
               "id": "PostToolUse:r1"},
              {"ts": now - 600, "session": SID, "kind": "edit",
               "summary": "edited manager/core/board.html", "id": "PostToolUse:e1"},
              {"ts": now - tool_ago, "session": SID, "kind": "edit",
               "summary": "edited manager/core/board.html", "id": "PostToolUse:e2"}]
    history = history if history is not None else ([current] if current else [])
    last = max((h["at"] for h in history), default=None)
    progress = {"agent": AGENT, "task": CARD, "intent": "work",
                "channel": {"state": "available", "why": "fixture"},
                "opened": now - 1800, "closed": None if active else now - 60,
                "outcome": outcome, "active": active, "current": current,
                "lastReport": last, "lastReceived": last,
                "counts": {"accepted": len(history), "coalesced": 0, "duplicates": 0,
                           "rejected": 0},
                "lastRejection": None, "updates": len(history),
                "milestones": milestones or [], "history": history}
    board = [{"kind": "verify", "actor": "agent", "file": CARD, "ts": now - ago,
              "summary": text} for ago, text in verify]
    return {"agent": agent, "session": session, "events": events, "progress": progress,
            "boardEvents": board}


INSTALL = """
(() => {
  window.__fx = %s;
  if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts) => {
    const u = String(url), fx = window.__fx;
    if (u.startsWith('/api/progress')) {
      return new Response(JSON.stringify({progress: fx.progress}), {status: 200});
    }
    if (u.startsWith('/api/session')) {
      return new Response(JSON.stringify({meta: fx.session, events: fx.events}), {status: 200});
    }
    const res = await window.__realFetch(url, opts);
    if (!u.startsWith('/api/state')) return res;
    const state = await res.json();
    state.agents = (state.agents || []).filter(a => a.id !== fx.agent.id).concat([fx.agent]);
    state.sessions = [fx.session].concat((state.sessions || []).filter(s => s.id !== fx.session.id));
    state.progress = Object.assign({}, state.progress || {}, {[fx.progress.task]: Object.assign({}, fx.progress, {history: undefined})});
    state.boardEvents = (state.boardEvents || []).concat(fx.boardEvents);
    return new Response(JSON.stringify(state), {status: 200});
  };
  S.progressFull = {};
  S.loaded = {};
  S.events = {};
  S.focusSid = %s;
  S.flightSid = %s;
  loadState();
  return true;
})()
"""


def install(fx: dict):
    S["browser"].eval(INSTALL % (json.dumps(fx), json.dumps(SID), json.dumps(SID)))


def board(width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")


def card_text() -> str:
    return S["browser"].eval(
        f"(document.querySelector('[data-file=\"{CARD}\"]') || {{}}).innerText || ''")


def view(name: str):
    b = S["browser"]
    b.click(f'#views button[data-view="{name}"]')
    b.wait(f"document.querySelector('#view-{name}') && "
           f"getComputedStyle(document.querySelector('#view-{name}')).display !== 'none'", 5)


def focus_text() -> str:
    return S["browser"].eval("(document.querySelector('#cgrid') || {}).innerText || ''")


MILESTONE = {"id": "p-2", "at": 0, "stage": "UI", "doing": "adding the permissions panel and UI tests",
             "done": ["profile resolution and launch integration committed",
                      "focused tests passed"],
             "remaining": ["full regression testing"],
             "narrative": "Profile resolution and launch integration are committed. "
                          "I am now adding the permissions panel and UI tests.",
             "source": "agent-reported"}


def milestone_fixture(now, **kw):
    first = {"id": "p-1", "at": now - 1700, "stage": "start",
             "doing": "reading the profiles code", "remaining": ["launch wiring", "UI"],
             "source": "agent-reported"}
    current = dict(MILESTONE, at=now - 120)
    return fixture(now, current=current, history=[first, current],
                   milestones=[{"text": t, "at": now - 120} for t in current["done"]], **kw)


class Progress(unittest.TestCase):

    def test_milestone_progression_on_the_card_and_in_focus(self):
        now = time.time()
        board()
        install(milestone_fixture(now))
        b = S["browser"]
        self.assertTrue(b.wait(f"(document.querySelector('[data-file=\"{CARD}\"] .pwell') || 0)", 10),
                        "the card never drew the run's progress")
        text = card_text()
        self.assertIn("agent-reported", text)
        self.assertIn("adding the permissions panel", text)
        self.assertNotIn("%", text)
        shot("progress-card-milestone-1440x900")
        view("focus")
        self.assertTrue(b.wait("document.querySelector('#cgrid .prog') !== null", 10))
        # the heading is drawn uppercase by CSS, so innerText reads
        # "PROGRESS" while the label itself, and the section's accessible
        # name, are "Progress": assert the semantic text, not the rendering
        heading = b.eval("(() => { const s = document.querySelector('#cgrid .prog');"
                         " const l = s && s.querySelector('.prog-head .label');"
                         " return [s && s.getAttribute('aria-label'),"
                         " l && l.textContent.trim()]; })()")
        self.assertEqual(["Progress", "Progress"], heading)
        text = focus_text()
        for words in ("agent-reported", "Doing now", "focused tests passed",
                      "full regression testing", "Last progress update",
                      "Last tool activity", "Process running"):
            self.assertIn(words, text)
        self.assertNotIn("stuck", text.lower())
        shot("progress-focus-milestone-1440x900")

    def test_quiet_activity_is_said_as_quiet(self):
        now = time.time()
        board()
        current = dict(MILESTONE, at=now - 1500)
        install(fixture(now, current=current, history=[current], tool_ago=4))
        view("focus")
        b = S["browser"]
        self.assertTrue(b.wait("(document.querySelector('#cgrid .phonest') || {}).innerText"
                               "?.includes('No recent progress update')", 10), focus_text())
        text = focus_text()
        self.assertIn("tool activity continues", text)
        for invented in ("stuck", "thinking"):
            self.assertNotIn(invented, text.lower())
        shot("progress-focus-quiet-1280x800")

    def test_a_blocker_and_verification_start_and_end(self):
        now = time.time()
        board()
        current = {"id": "p-3", "at": now - 30, "stage": "verification",
                   "doing": "waiting for approval",
                   "blocked": "Verification is blocked: this command requires approval "
                              "that the headless run cannot obtain.",
                   "narrative": "Code is committed, but these tests have not run.",
                   "source": "agent-reported"}
        install(fixture(now, current=current, history=[current],
                        verify=[(300, "verifying unit (fixture)"),
                                (200, "unit — failed (fixture)")]))
        b = S["browser"]
        self.assertTrue(b.wait(f"(document.querySelector('[data-file=\"{CARD}\"] .pblock') || 0)", 10))
        self.assertIn("Blocked:", card_text())
        shot("progress-card-blocked-1440x900")
        view("focus")
        self.assertTrue(b.wait("document.querySelector('#cgrid .prog dd.blocked') !== null", 10))
        b.eval("document.querySelector('#cgrid .prog details').open = true")
        text = focus_text()
        self.assertIn("The run reports that it is blocked.", text)
        self.assertIn("unit — failed (fixture)", text)
        self.assertIn("verifying unit (fixture)", text)
        shot("progress-focus-blocked-verification-1440x900")

    def test_a_terminal_failure_supersedes_doing_now(self):
        now = time.time()
        board()
        install(milestone_fixture(now, active=False, outcome="failed", status="failed"))
        view("focus")
        b = S["browser"]
        self.assertTrue(b.wait("(document.querySelector('#cgrid .prog') || {}).innerText"
                               "?.includes('The run failed')", 10), focus_text())
        text = focus_text()
        self.assertIn("Last said", text)
        self.assertNotIn("Doing now", text)
        view("board")
        self.assertFalse(b.eval(f"!!document.querySelector('[data-file=\"{CARD}\"] .pwell')"))
        shot("progress-focus-failed-1280x800")

    def test_a_reconnect_brings_the_newer_update_and_keeps_history(self):
        now = time.time()
        board()
        install(milestone_fixture(now))
        view("focus")
        b = S["browser"]
        self.assertTrue(b.wait("document.querySelector('#cgrid .prog') !== null", 10))
        newer = {"id": "p-4", "at": now - 5, "stage": "tests",
                 "doing": "running the full regression", "done": ["UI tests written"],
                 "source": "agent-reported"}
        fx = milestone_fixture(now)
        fx["progress"]["history"].append(newer)
        fx["progress"]["current"] = newer
        fx["progress"]["lastReport"] = newer["at"]
        fx["progress"]["counts"]["accepted"] = 3
        fx["progress"]["milestones"].append({"text": "UI tests written", "at": newer["at"]})
        # the stream dropped and came back: the page re-reads, as onopen does
        install(fx)
        self.assertTrue(b.wait("(document.querySelector('#cgrid .prog') || {}).innerText"
                               "?.includes('running the full regression')", 10), focus_text())
        text = focus_text()
        self.assertIn("Progress timeline (3 updates)", text)
        self.assertIn("focused tests passed", text)          # earlier milestone kept
        shot("progress-focus-reconnected-1440x900")

    def test_sessions_filter_and_narrow_layout(self):
        now = time.time()
        board(390, 844)
        install(milestone_fixture(now))
        view("flight")
        b = S["browser"]
        self.assertTrue(b.wait("[...document.querySelectorAll('#ffilters button')]"
                               ".some(x => x.innerText === 'Progress updates')", 10))
        b.eval("[...document.querySelectorAll('#ffilters button')]"
               ".find(x => x.innerText === 'Progress updates').click()")
        self.assertTrue(b.wait("(document.querySelector('#ftl') || {}).innerText"
                               "?.includes('agent-reported')", 10))
        tl = b.eval("document.querySelector('#ftl').innerText")
        self.assertNotIn("edited manager/core/board.html", tl)   # technical activity filtered out
        shot("progress-sessions-filter-390x844")
        view("focus")
        self.assertTrue(b.wait("document.querySelector('#cgrid .prog') !== null", 10))
        fits = b.eval("(() => { const r = document.querySelector('#cgrid .prog').getBoundingClientRect();"
                      " return {right: Math.round(r.right), width: innerWidth,"
                      " scroll: document.documentElement.scrollWidth}; })()")
        self.assertLessEqual(fits["right"], fits["width"] + 1, fits)
        self.assertLessEqual(fits["scroll"], fits["width"] + 1, fits)
        # the timeline fold is a native control: keyboard-reachable and named
        named = b.eval("(() => { const s = document.querySelector('#cgrid .prog details summary');"
                       " return s ? s.innerText : ''; })()")
        self.assertIn("Progress timeline", named)
        shot("progress-focus-narrow-390x844")
