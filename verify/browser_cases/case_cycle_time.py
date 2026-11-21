"""Time on this card, measured in a browser (task 165).

Two cards on the disposable backend:

- **The real reading.** A card with two overlapping check records written
  into the backend's evidence directory exactly where `verify` keeps them
  (both `failed`, so no freshness question reaches git). The page reads
  `/api/card/timing` itself; the case holds every drawn duration to the
  reply the page received and to a fresh read of the route, asserts the
  checks' union (300s) and their sum (400s) exactly, and holds the read to
  no POST and no byte changed on disk.
- **The states a fixture backend cannot produce** — a check and a run in
  flight, a phase member's two waits, unknown and anomalous intervals —
  are a reading served by wrapping the page's own `fetch`, as
  case_progress.py does: nothing is launched and nothing is billed.

Both are drawn at 1440×900 and 390×844, in Daylight and Night, with every
fold open, measured for sideways overflow and clipped text, and
photographed. Run through verify/browser.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
FILE = "165-time-fixture.md"
CARD = """# 165 — Time fixture

**Status:** In Progress
**Priority:** Medium

A card whose checks overlapped, so its verification time is a union.
"""
SECTION = "document.querySelector('#dsec-overview .cycletime')"
DRAWER = "document.querySelector('#drawerbody')"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def dur(seconds) -> str:
    """lib/time.js's duration(), for comparing what is drawn."""
    if seconds is None:
        return "none recorded"
    s = max(0, round(seconds))
    if s < 60:
        return f"{s}s"
    m, h = s // 60, s // 3600
    d = h // 24
    if d:
        return f"{d}d {h % 24}h"
    if h:
        return f"{h}h {m % 60}m"
    return f"{m}m {s % 60}s"


def evidence_record(ident: str, now: float, start: float, end: float) -> dict:
    return {"v": 1, "id": ident, "task": FILE, "stage": "in-progress", "agent": None,
            "actor": "you", "intent": "work", "check": "unit", "scope": None,
            "requested": now - start, "started": now - start, "ended": now - end,
            "duration": start - end, "outcome": "failed", "reason": "exit-nonzero",
            "why": "the check exited non-zero", "rc": 1, "prepare": [],
            "source": {"commit": "0" * 40, "bound": True, "dirty": False},
            "location": "local", "logBytes": 0, "logTruncated": False}


def rich(now: float) -> dict:
    """A reading with every state at once, in the server's shape."""
    cats = [
        ("lead", "Lead time", 5400, True, None),
        ("queued", "Queued", 1200, False, None),
        ("in-progress", "In progress (stage)", 2400, False, None),
        ("implementation", "Implementation runs", 1600, False, None),
        ("verification", "Verification (checks in flight)", 1000, True, 1200),
        ("outside-checks", "Agent time outside checks", 1000, True, None),
        ("review-stage", "In review (stage)", 1800, True, None),
        ("review-runs", "Review runs", 400, False, None),
        ("repair", "Repair runs", 400, True, None),
        ("integration-wait", "Waiting for integration", 1800, True, None),
        ("delivery-wait", "Waiting for delivery to main", None, False, None),
    ]
    return {"timing": {
        "v": 1, "card": FILE, "stage": "review", "now": now,
        "lead": {"start": {"at": now - 5400, "how": "created"}, "end": None, "open": True,
                 "seconds": 5400, "reopened": 1},
        "stages": [{"stage": "backlog", "from": now - 5400, "to": now - 4200, "open": False,
                    "seconds": 1200, "how": "created", "source": "commit", "commit": "a1b2c3d4e5f6"},
                   {"stage": "in-progress", "from": now - 4200, "to": now - 1800, "open": False,
                    "seconds": 2400, "how": "person", "source": "commit", "commit": "b1b2c3d4e5f6"},
                   {"stage": "review", "from": now - 1800, "to": None, "open": True,
                    "seconds": 1800, "how": "automated", "source": "journal", "commit": None}],
        "categories": [{"key": k, "label": l, "note": "The fixture's note for " + l + ".",
                        "seconds": s, "recorded": s is not None, "open": o,
                        **({"summed": summed} if summed else {})} for k, l, s, o, summed in cats],
        "runs": [{"agent": "a-very-long-agent-identifier-that-must-wrap-0123456789", "intent": "work",
                  "kind": "implementation", "label": "Implementation run", "opened": now - 4200,
                  "closed": now - 2600, "ended": "observed", "outcome": "finished", "seconds": 1600,
                  "open": False, "checks": {"count": 4, "seconds": 700}, "outsideChecks": 900},
                 {"agent": "b-run", "intent": "work", "kind": "implementation",
                  "label": "Implementation run", "opened": now - 5000, "closed": None,
                  "ended": "unobserved", "outcome": "unobserved", "seconds": None, "open": False,
                  "checks": {"count": 0, "seconds": None}, "outsideChecks": None},
                 {"agent": "c-run", "intent": "act-pr", "kind": "repair",
                  "label": "Repair run (act on PR)", "opened": now - 400, "closed": None,
                  "ended": "running", "outcome": None, "seconds": 400, "open": True,
                  "checks": {"count": 1, "seconds": 300}, "outsideChecks": 100}],
        "verification": {"seconds": 1000, "summed": 1200, "execution": 1080, "preparation": 50,
                         "overlapped": 200, "count": 2, "reused": 1,
                         "attempts": {"unit": 1, "browser": 1},
                         "records": [{"id": "ev:verification@fixture-browser-0000000000000000000000",
                                      "check": "browser", "scope": None, "attempt": 1,
                                      "outcome": "running", "reason": None, "live": True,
                                      "requested": now - 300, "started": now - 280, "ended": None,
                                      "seconds": 300, "execution": 280, "preparation": None,
                                      "ranNothing": False, "commit": "0123456789ab",
                                      "agent": "c-run", "actor": "agent", "intent": "act-pr",
                                      "phase": "running the check"},
                                     {"id": "ev-unit-1", "check": "unit", "scope": "fast",
                                      "attempt": 1, "outcome": "failed", "reason": "exit-nonzero",
                                      "live": False, "requested": now - 3800,
                                      "started": now - 3750, "ended": now - 3100, "seconds": 700,
                                      "execution": 650, "preparation": 50, "ranNothing": False,
                                      "commit": "0123456789ab", "agent": "a-run",
                                      "actor": "agent", "intent": "work", "phase": None}]},
        "integration": {"phase": "8", "phaseFile": "8-p.md", "integratedAt": None,
                        "integratedHow": None, "deliveredAt": None, "deliveredHow": None,
                        "precision": None, "waitIntegration": 1800, "waitDelivery": None,
                        "note": "In the phase branch is not delivered: the card reaches the "
                                "application base only when its phase does."},
        "active": [{"kind": "check", "name": "browser", "scope": None, "phase": "running the check",
                    "since": now - 300, "elapsed": 300, "attempt": 1,
                    "evidence": "ev:verification@fixture-browser-0000000000000000000000",
                    "next": {"label": "Open the evidence sheet (◫)", "kind": "evidence",
                             "why": "the check's live record"}},
                   {"kind": "run", "name": "Repair run (act on PR)", "agent": "c-run",
                    "since": now - 400, "elapsed": 400,
                    "lastReport": {"at": now - 100, "stage": "fixing",
                                   "doing": "re-running the narrow layout case", "blocked": None,
                                   "source": "agent-reported"},
                    "reportAge": 100, "lastActivity": now - 50, "activityAge": 50,
                    "process": "alive",
                    "next": {"label": "‖ hold", "kind": "hold",
                             "why": "stops this run and the checks it asked for"}}],
        "anomalies": [{"code": "move-order", "detail": "the move out of to-do is dated before "
                                                       "the move into it"}],
        "unknown": [{"what": "run b-run", "why": "the board restarted while it ran, so its ending "
                                                 "was not observed and its time is in no total"}],
        "delivered": None, "read": {"at": now, "local": True, "writes": False},
    }}


class TimeOnThisCard(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "in-progress" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.evidence = backend.tool / "manager" / "local" / "state" / "evidence"
        self.evidence.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for ident, a, b in (("ev-ct-a", 500, 300), ("ev-ct-b", 400, 200)):
            path = self.evidence / f"{ident}.json"
            path.write_text(json.dumps(evidence_record(ident, now, a, b)), encoding="utf-8")
            self.addCleanup(path.unlink, True)
        self.addCleanup(lambda: S["browser"].eval("localStorage.removeItem('bench-theme')"))

    def digest(self) -> str:
        h = hashlib.sha256()
        backend = S["backend"]
        for root in (self.evidence, backend.work / "tasks"):
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                h.update(str(path).encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    def js(self, expression: str):
        got = S["browser"].eval(
            "(() => { try { return {ok: true, v: (" + expression + ")}; }"
            " catch (e) { return {ok: false, v: String(e && e.message)}; } })()")
        self.assertTrue(got and got.get("ok"), f"{expression[:160]} — {got}")
        return got.get("v")

    def open_card(self, width, height, dark, inject=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        if inject is not None:
            b.eval("{ const fx = %s; const f = window.fetch; window.fetch = (u, o) => "
                   "String(u).startsWith('/api/card/timing') ? Promise.resolve(new Response("
                   "JSON.stringify(fx), {status: 200})) : f.call(window, u, o); }"
                   % json.dumps(inject))
        sel = f'article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the fixture card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        self.assertTrue(b.wait(f"!!({SECTION} && {SECTION}.querySelector('.ctcats'))", 15),
                        "Time on this card never drew")
        # every fold open, so the evidence under them is measured too
        self.js(f"(() => {{ for (const d of {SECTION}.querySelectorAll('details')) d.open = true;"
                f" return true; }})()")
        return b

    def drawn(self) -> dict:
        return self.js(
            f"Object.fromEntries([...{SECTION}.querySelectorAll('.ctcats li')].map(li => "
            f"[li.dataset.ct, li.querySelector('.ctval').innerText.trim()]))")

    def readable(self, b, name: str):
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, f"{name}: the detail view scrolls sideways")
        page = b.eval("document.documentElement.scrollWidth - innerWidth")
        self.assertLessEqual(page, 1, f"{name}: the page scrolls sideways")
        bad = self.js(
            f"[...{SECTION}.querySelectorAll('.ctval, .ctlabel, .ctline li, .ctruns li, .ctchecks li, "
            f".ctactive li, .ctgaps li')].filter(el => {{ const r = el.getBoundingClientRect(); "
            f"const s = getComputedStyle(el); return !el.innerText.trim() || r.width <= 0 || "
            f"el.scrollWidth > el.clientWidth + 1 || s.color === s.backgroundColor; }})"
            f".map(el => el.className + ': ' + el.innerText.slice(0, 60))")
        self.assertEqual([], bad, f"{name}: unreadable or clipped")
        self.js(f"({SECTION}.scrollIntoView({{block: 'start'}}), true)")
        shot(name)

    def test_1_the_real_reading_is_what_is_drawn_and_reading_writes_nothing(self):
        before = self.digest()
        for width, height in ((1440, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    b = self.open_card(width, height, dark)
                    got = self.js(f"S.timing[{json.dumps(FILE)}].data.timing")
                    v = got["verification"]
                    self.assertEqual(v["seconds"], 300, "the two checks' union")
                    self.assertEqual(v["summed"], 400, "their sum, apart")
                    self.assertEqual(v["attempts"], {"unit": 2})
                    drawn = self.drawn()
                    for c in got["categories"]:
                        self.assertTrue(drawn[c["key"]].startswith(dur(c["seconds"])),
                                        f"{c['key']}: drawn {drawn[c['key']]!r}, "
                                        f"the reading says {c['seconds']}")
                    text = self.js(f"{SECTION}.innerText")
                    self.assertIn("not parts of one total", text)
                    self.assertIn("ev-ct-a", text)
                    self.assertIn("attempt 2", text)
                    # a fresh read of the route says the same closed totals
                    fresh = self.js(
                        f"window.__ct = null, fetch('/api/card/timing?file={FILE}').then(r => "
                        f"r.json()).then(j => window.__ct = j), true")
                    self.assertTrue(fresh)
                    self.assertTrue(b.wait("!!window.__ct", 10))
                    again = self.js("window.__ct.timing")
                    self.assertEqual(again["verification"]["seconds"], 300)
                    self.assertEqual(again["verification"]["summed"], 400)
                    self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                    self.readable(b, f"cycle-time-{width}-{'night' if dark else 'day'}")
        self.assertEqual(before, self.digest(), "reading wrote something")

    def test_2_running_unknown_and_phase_states_are_said_in_words(self):
        fx = rich(time.time())
        for width, height in ((1440, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    b = self.open_card(width, height, dark, inject=fx)
                    drawn = self.drawn()
                    for c in fx["timing"]["categories"]:
                        self.assertTrue(drawn[c["key"]].startswith(dur(c["seconds"])), c["key"])
                    self.assertEqual(drawn["delivery-wait"], "none recorded")
                    self.assertIn("(20m 0s summed across checks)", drawn["verification"])
                    text = self.js(f"{SECTION}.innerText")
                    for words in ("Checking now: browser", "5m 0s so far",
                                  "agent-reported, not verified", "activity, not progress",
                                  "ending not observed", "Not recorded: run b-run",
                                  "Clock or order anomaly (move-order)",
                                  "not yet merged into the phase branch",
                                  "not measured coding time", "reopened 1×"):
                        self.assertIn(words, text)
                    lead = self.js("document.querySelector('#ctLead').innerText")
                    self.assertEqual(lead, "1h 30m")
                    fact = self.js("document.querySelector('#dsec-overview .ovfacts').innerText")
                    self.assertIn("1h 30m lead time so far", fact)
                    self.readable(b, f"cycle-time-states-{width}-{'night' if dark else 'day'}")


if __name__ == "__main__":
    unittest.main()
