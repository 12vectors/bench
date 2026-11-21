"""Ticket cycle time and the verification breakdown (task 165).

`cycletime.build` is pure, so most of this file is fixtures with exact
totals: nested and overlapping checks, a retried check, concurrent runs, a
card queued and reopened, a phase member's two waits, missing data, clock
anomalies and intervals still running. Each fixture states its clock in
plain numbers so a reader can check the arithmetic by hand.

`ThePassiveRead` runs the real gatherer on a disposable board — a work
state with real git moves, a progress record and evidence records — through
the HTTP route, and holds it to three things: the route's totals are the
module's, nothing under the state or tasks directories changes, and no
process but a read-only git query starts.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.test_integration_gates import CARD, GateCase, git

import config  # noqa: E402
import cycletime  # noqa: E402
import evidence  # noqa: E402
import httpd  # noqa: E402
import movements  # noqa: E402
import progress  # noqa: E402


def move(at, to, frm=None, kind="person"):
    return {"at": at, "to": to, "from": frm, "kind": kind, "source": "commit",
            "commit": "abc"}


def moves(*chronological):
    """Moves oldest first in, newest first out — as `movements.history`."""
    return {"events": list(reversed(chronological)), "complete": True, "problem": None}


def run(agent, intent, opened, closed=None, outcome=None, current=None):
    return {"agent": agent, "intent": intent, "opened": opened, "closed": closed,
            "outcome": outcome if closed else None, "current": current}


def check(ident, name, agent, requested, ended=None, outcome="passed",
          started=None, prepare=(), reason=None):
    return {"id": ident, "check": name, "agent": agent, "requested": requested,
            "started": started if started is not None else requested,
            "ended": ended, "outcome": outcome, "reason": reason,
            "prepare": [{"name": "deps", "started": a, "ended": b} for a, b in prepare],
            "source": {"commit": "0123456789abcdef"}}


def cat(reading, key):
    return next(c for c in reading["categories"] if c["key"] == key)


class Intervals(unittest.TestCase):

    def test_union_merges_nested_overlapping_and_touching(self):
        self.assertEqual(cycletime.union([(5, 9), (0, 3), (2, 4), (6, 7), (9, 10)]),
                         [(0, 4), (5, 10)])
        self.assertEqual(cycletime.length(cycletime.union([(0, 10), (2, 3)])), 10)

    def test_a_reversed_interval_is_not_one(self):
        self.assertEqual(cycletime.union([(5, 1)]), [])

    def test_overlap(self):
        self.assertEqual(cycletime.overlap([(0, 10)], [(5, 15), (20, 30)]), 5)

    def test_times_read_as_numbers_or_iso_and_nothing_else(self):
        self.assertEqual(cycletime.ts(12.5), 12.5)
        self.assertEqual(cycletime.ts("1970-01-01T00:00:10+00:00"), 10.0)
        self.assertIsNone(cycletime.ts(None))
        self.assertIsNone(cycletime.ts(0))
        self.assertIsNone(cycletime.ts("yesterday"))
        self.assertIsNone(cycletime.ts(True))


# The main fixture's clock: created 1000, to-do 1100, in-progress 1300,
# review 3000; read at 4000.
#   work run A 1300–2900 (1600); review run B 3100–3500 (400);
#   act-pr run C 3600–now (400, running)
#   checks: e1 unit 1500–1800 failed (300, prep 1500–1550, check 1550–1800)
#           e2 unit 1900–2300 passed (400) — the retry
#           e3 browser 2000–2200 passed (200) — nested inside e2
#           e6 unit 2500 denied — ran nothing
#           e5 browser 3700–now running (300)
#   union 300 + 400 + 300 = 1000; summed 1200; overlapped 200
NOW = 4000.0


def main_fixture():
    return {
        "card": {"file": "9-x.md", "stage": "review", "number": "9"},
        "moves": moves(move(1000, "backlog", kind="created"),
                       move(1100, "to-do", "backlog"),
                       move(1300, "in-progress", "to-do"),
                       move(3000, "review", "in-progress")),
        "runs": [run("A", "work", 1300, 2900, "finished"),
                 run("B", "review", 3100, 3500, "finished"),
                 run("C", "act-pr", 3600, None,
                     current={"at": 3900, "stage": "fixing", "doing": "the failing case"})],
        "evidence": [
            check("e1", "unit", "A", 1500, 1800, "failed", started=1550,
                  prepare=[(1500, 1550)]),
            check("e2", "unit", "A", 1900, 2300, started=1950),
            check("e3", "browser", "A", 2000, 2200),
            check("e6", "unit", "A", 2500, None, "denied"),
            check("e5", "browser", "C", 3700, None, "running", started=3720),
        ],
        "reuses": [{"check": "unit", "requested": 3200}],
        "live": {"C": {"lastTool": 3950.0, "process": "alive"}},
    }


class OverlappingChecksRetriesAndConcurrentRuns(unittest.TestCase):

    def setUp(self):
        self.r = cycletime.build(main_fixture(), NOW)

    def test_lead_time_is_the_only_total(self):
        self.assertEqual(self.r["lead"]["seconds"], 3000)
        self.assertTrue(self.r["lead"]["open"])
        self.assertEqual(self.r["lead"]["start"], {"at": 1000.0, "how": "created"})

    def test_stage_totals(self):
        self.assertEqual(cat(self.r, "queued")["seconds"], 300)
        self.assertEqual(cat(self.r, "in-progress")["seconds"], 1700)
        self.assertEqual(cat(self.r, "review-stage")["seconds"], 1000)
        self.assertTrue(cat(self.r, "review-stage")["open"])

    def test_verification_is_a_union_and_the_sum_is_apart(self):
        v = self.r["verification"]
        self.assertEqual(v["seconds"], 1000)
        self.assertEqual(v["summed"], 1200)
        self.assertEqual(v["overlapped"], 200)
        self.assertEqual(v["preparation"], 50)
        # check processes: 250 + 350 + 200 + 280 (running, from its start)
        self.assertEqual(v["execution"], 1080)
        self.assertEqual(v["attempts"], {"unit": 3, "browser": 2})
        self.assertEqual(v["reused"], 1)
        self.assertEqual(cat(self.r, "verification")["seconds"], 1000)
        self.assertEqual(cat(self.r, "verification")["summed"], 1200)

    def test_a_retry_is_numbered_and_a_denial_ran_nothing(self):
        rows = {e["id"]: e for e in self.r["verification"]["records"]}
        self.assertEqual(rows["e2"]["attempt"], 2)
        self.assertTrue(rows["e6"]["ranNothing"])
        self.assertIsNone(rows["e6"]["seconds"])

    def test_runs_and_time_outside_checks(self):
        runs = {r["agent"]: r for r in self.r["runs"]}
        self.assertEqual(runs["A"]["seconds"], 1600)
        self.assertEqual(runs["A"]["checks"], {"count": 4, "seconds": 700})
        self.assertEqual(runs["A"]["outsideChecks"], 900)
        self.assertEqual(runs["B"]["seconds"], 400)
        self.assertIsNone(runs["B"]["outsideChecks"])
        self.assertEqual(runs["C"]["seconds"], 400)
        self.assertTrue(runs["C"]["open"])
        self.assertEqual(runs["C"]["outsideChecks"], 100)
        self.assertEqual(cat(self.r, "implementation")["seconds"], 1600)
        self.assertEqual(cat(self.r, "repair")["seconds"], 400)
        self.assertEqual(cat(self.r, "review-runs")["seconds"], 400)
        # (1600 + 400) less the 1000 a check was in flight inside them
        self.assertEqual(cat(self.r, "outside-checks")["seconds"], 1000)

    def test_no_label_calls_it_coding_time(self):
        note = cat(self.r, "outside-checks")["note"]
        self.assertIn("Not measured coding time", note)

    def test_what_is_active_says_report_and_activity_apart(self):
        active = {a["kind"]: a for a in self.r["active"]}
        self.assertEqual(active["check"]["name"], "browser")
        self.assertEqual(active["check"]["elapsed"], 300)
        self.assertEqual(active["check"]["evidence"], "e5")
        self.assertEqual(active["run"]["elapsed"], 400)
        self.assertEqual(active["run"]["reportAge"], 100)
        self.assertEqual(active["run"]["lastReport"]["source"], "agent-reported")
        self.assertEqual(active["run"]["activityAge"], 50)
        self.assertEqual(active["run"]["next"]["kind"], "hold")

    def test_categories_overlap_and_are_never_summed_into_a_total(self):
        keys = [c["key"] for c in self.r["categories"]]
        self.assertEqual(keys[0], "lead")
        self.assertNotIn("total", keys)
        parts = sum(c["seconds"] or 0 for c in self.r["categories"][1:])
        self.assertGreater(parts, self.r["lead"]["seconds"],
                           "the categories overlap, which is why none is a total")

    def test_it_is_deterministic(self):
        self.assertEqual(cycletime.build(main_fixture(), NOW), self.r)
        self.assertEqual(self.r["anomalies"], [])


class QueuedAndReopened(unittest.TestCase):

    def test_every_visit_is_summed_and_lead_ends_at_the_last_done(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "done"},
            "moves": moves(move(1000, "backlog", kind="created"),
                           move(1100, "to-do", "backlog"),
                           move(1200, "in-progress", "to-do"),
                           move(1500, "review", "in-progress"),
                           move(1600, "done", "review"),
                           move(2000, "in-progress", "done"),
                           move(2500, "review", "in-progress"),
                           move(2600, "done", "review")),
        }, 5000.0)
        self.assertEqual(r["lead"]["seconds"], 1600)
        self.assertFalse(r["lead"]["open"])
        self.assertEqual(r["lead"]["reopened"], 1)
        self.assertEqual(r["lead"]["end"], {"at": 2600.0, "how": "moved to done"})
        self.assertEqual(cat(r, "queued")["seconds"], 200)
        self.assertEqual(cat(r, "in-progress")["seconds"], 800)
        self.assertEqual(cat(r, "review-stage")["seconds"], 200)
        self.assertEqual(len(r["stages"]), 8)
        # nothing ran: absent, not zero
        self.assertIsNone(cat(r, "implementation")["seconds"])
        self.assertFalse(cat(r, "implementation")["recorded"])
        self.assertIsNone(r["verification"]["seconds"])

    def test_a_card_waiting_in_the_queue_is_open(self):
        r = cycletime.build({"card": {"file": "9-x.md", "stage": "to-do"},
                             "moves": moves(move(1000, "backlog", kind="created"),
                                            move(1500, "to-do", "backlog"))}, 2000.0)
        self.assertEqual(cat(r, "queued")["seconds"], 1000)
        self.assertTrue(cat(r, "queued")["open"])
        self.assertIsNone(cat(r, "in-progress")["seconds"])


class PhaseIntegration(unittest.TestCase):

    def member(self, integrated=None, delivered=None, stage="review", now=3000.0):
        return cycletime.build({
            "card": {"file": "9-x.md", "stage": stage, "number": "9",
                     "phase": {"file": "8-p.md", "number": "8"}},
            "moves": moves(move(1000, "to-do", kind="created"),
                           move(1200, "in-progress", "to-do"),
                           move(2000, "review", "in-progress")),
            "integration": {"integratedAt": integrated, "deliveredAt": delivered,
                            "logRead": True},
        }, now)

    def test_waiting_for_integration_while_not_yet_merged(self):
        r = self.member()
        self.assertEqual(cat(r, "integration-wait")["seconds"], 1000)
        self.assertTrue(cat(r, "integration-wait")["open"])
        self.assertIsNone(cat(r, "delivery-wait")["seconds"])
        self.assertIsNone(r["integration"]["integratedAt"])

    def test_integrated_is_not_delivered(self):
        r = self.member(integrated=2600.0)
        self.assertEqual(cat(r, "integration-wait")["seconds"], 600)
        self.assertEqual(cat(r, "delivery-wait")["seconds"], 400)
        self.assertTrue(cat(r, "delivery-wait")["open"])
        self.assertIn("not delivered", r["integration"]["note"])
        self.assertEqual(r["integration"]["precision"], "minute")

    def test_delivered_with_its_phase(self):
        r = self.member(integrated=2600.0, delivered=2900.0)
        self.assertEqual(cat(r, "delivery-wait")["seconds"], 300)
        self.assertFalse(cat(r, "delivery-wait")["open"])

    def test_a_card_in_no_phase_has_no_integration_rows(self):
        r = cycletime.build(main_fixture(), NOW)
        self.assertIsNone(r["integration"])
        self.assertNotIn("integration-wait", [c["key"] for c in r["categories"]])

    def test_the_phase_log_line_is_read_in_local_minutes(self):
        body = ("# 8 — p\n\n## Phase log\n\n- 2026-11-20 10:05 · run started\n"
                "- 2026-11-20 10:40 · 9 partly merged into phase/8-p — kept\n"
                "- 2026-11-20 10:42 · 09 merged into phase/8-p\n")
        lines = cycletime.log_lines(body)
        self.assertEqual(len(lines), 3)
        merged = [s for s, t in lines if cycletime.MERGED_RE.match(t)]
        self.assertEqual(merged, ["2026-11-20 10:42"])
        self.assertEqual(cycletime.log_time("2026-11-20 10:42"),
                         time.mktime(time.strptime("2026-11-20 10:42", "%Y-%m-%d %H:%M")))
        self.assertIsNone(cycletime.log_time("not a time"))


class MissingDataAndClockAnomalies(unittest.TestCase):

    def test_an_unobserved_ending_is_unknown_not_a_duration(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "in-progress"},
            "moves": moves(move(1000, "in-progress", "to-do")),
            "runs": [run("A", "work", 1100, 1900, "unobserved")],
            "evidence": [check("e1", "unit", "A", 1200, 1900, "error",
                               reason="board-restart")],
        }, 2000.0)
        a = r["runs"][0]
        self.assertIsNone(a["seconds"])
        self.assertEqual(a["ended"], "unobserved")
        self.assertIsNone(cat(r, "implementation")["seconds"])
        self.assertIsNone(r["verification"]["seconds"])
        whats = " ".join(u["what"] for u in r["unknown"])
        self.assertIn("run A", whats)
        self.assertIn("check unit", whats)
        self.assertIn("creation time", whats)
        self.assertEqual(r["lead"]["start"]["how"], "first-move")

    def test_moves_out_of_order_are_labelled_and_given_no_duration(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "review"},
            "moves": moves(move(1000, "backlog", kind="created"),
                           move(2000, "in-progress", "backlog"),
                           move(1500, "review", "in-progress")),
        }, 3000.0)
        codes = [a["code"] for a in r["anomalies"]]
        self.assertIn("move-order", codes)
        in_progress = [s for s in r["stages"] if s["stage"] == "in-progress"][0]
        self.assertIsNone(in_progress["seconds"])
        self.assertIsNone(cat(r, "in-progress")["seconds"])

    def test_a_move_made_without_a_record_leaves_the_end_unknown(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "review"},
            "moves": moves(move(1000, "backlog", kind="created"),
                           move(1200, "in-progress", "backlog")),
        }, 3000.0)
        self.assertIn("unrecorded-move", [a["code"] for a in r["anomalies"]])
        self.assertIsNone(r["stages"][-1]["seconds"])
        self.assertEqual(r["lead"]["seconds"], 2000)

    def test_a_future_time_is_an_anomaly_and_left_out(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "in-progress"},
            "moves": moves(move(1000, "in-progress", kind="created")),
            "runs": [run("A", "work", 9000, None)],
        }, 2000.0)
        self.assertIn("future-time", [a["code"] for a in r["anomalies"]])
        self.assertIsNone(r["runs"][0]["seconds"])

    def test_a_check_ending_before_its_request(self):
        r = cycletime.build({
            "card": {"file": "9-x.md", "stage": "in-progress"},
            "moves": moves(move(1000, "in-progress", kind="created")),
            "evidence": [check("e1", "unit", None, 1500, 1400)],
        }, 2000.0)
        self.assertIn("check-order", [a["code"] for a in r["anomalies"]])
        self.assertIsNone(r["verification"]["seconds"])

    def test_no_history_at_all(self):
        r = cycletime.build({"card": {"file": "9-x.md", "stage": "backlog"},
                             "moves": {"events": [], "complete": False,
                                       "problem": "no-repository"}}, 2000.0)
        self.assertIsNone(r["lead"]["seconds"])
        self.assertIsNone(r["lead"]["start"])
        whats = [u["what"] for u in r["unknown"]]
        self.assertIn("stage history", whats)
        self.assertIn("early stage history", whats)


class ThePassiveRead(GateCase):
    """The real gatherer on a disposable board, through the HTTP route."""

    def setUp(self):
        super().setUp()
        self.patch(STATE=self.state)
        movements.forget()
        self.addCleanup(movements.forget)
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "config", "user.email", "t@t")
        git(self.work, "config", "user.name", "tester")
        live = self.tasks / "in-progress" / CARD
        text = live.read_text(encoding="utf-8")
        live.unlink()
        (self.tasks / "to-do" / CARD).write_text(text, encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "create")
        git(self.work, "mv", f"tasks/to-do/{CARD}", f"tasks/in-progress/{CARD}")
        git(self.work, "commit", "-q", "-m", "start")
        now = time.time()
        progress.RUNS["run-1"] = {
            "v": 1, "agent": "run-1", "task": CARD, "intent": "work",
            "opened": now - 600, "closed": now - 60, "outcome": "finished",
            "current": None, "history": [], "channel": {"state": "available"}}
        self.addCleanup(progress.RUNS.pop, "run-1", None)
        for ident, a, b in (("ev-a", 500, 300), ("ev-b", 400, 200)):
            evidence.store({"id": ident, "task": CARD, "check": "unit", "agent": "run-1",
                            "requested": now - a, "started": now - a, "ended": now - b,
                            "outcome": "passed", "reason": "exit-zero", "prepare": []})

    def fingerprint(self) -> dict:
        found = {}
        for root in (self.state, self.tasks):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found

    def test_the_route_is_the_module_and_reading_changes_nothing(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        before = self.fingerprint()
        calls: list[list[str]] = []
        real = subprocess.Popen

        class Recorded(real):
            def __init__(self, cmd, *a, **k):
                calls.append([str(c) for c in cmd])
                super().__init__(cmd, *a, **k)

        subprocess.Popen = Recorded
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/card/timing?file={CARD}") as reply:
                body = json.loads(reply.read())
        finally:
            subprocess.Popen = real
        self.assertEqual(self.fingerprint(), before, "no state or card byte changed")
        for cmd in calls:
            self.assertEqual(Path(cmd[0]).name, "git", f"{cmd}: only git reads")
            self.assertNotIn(cmd[-1] if cmd else "", ("commit", "push", "fetch"))
        t = body["timing"]
        direct = cycletime.build(cycletime.gather(CARD), t["now"])
        self.assertEqual(t["categories"], direct["categories"])
        self.assertEqual(t["lead"], direct["lead"])
        self.assertEqual(t["verification"]["seconds"], 300)   # 500→200, union
        self.assertEqual(t["verification"]["summed"], 400)
        self.assertEqual(t["runs"][0]["seconds"], 540)
        self.assertEqual([s["stage"] for s in t["stages"]], ["to-do", "in-progress"])
        self.assertTrue(t["read"]["local"])
        self.assertFalse(t["read"]["writes"])

    def test_an_unknown_card_is_refused(self):
        with self.assertRaises(ValueError):
            cycletime.for_card("nope.md")
        with self.assertRaises(ValueError):
            cycletime.for_card("../x.md")


if __name__ == "__main__":
    unittest.main()
