"""Queueing a whole phase with one move, in a real browser (task 188).

Disposable cards of this case's own: phase #70 with members #71 and #72
(#72 depends on #71), every one of them in Backlog; and phase #75 in To
Do whose list names #76 and a card no board has (#99). An operating
agreement with the Bench scope only — no phase entry, nothing adopted —
is put in force through the board's own routes. This installation is
never designated to execute and the orchestration pass is held off, so
nothing here is launched, merged or moved by an orchestrator; the
scheduling half is held by tests/test_phase_queue.py.

- **Before the move** the phase reads *not queued*, and so do its
  Backlog members.
- **One move queues the phase and every member.** After the person's
  move of #70 into To Do, #71 and #72 stay stored in Backlog and read
  *queued with phase* — in the API (`queue.inherited`), on their cards in
  the Phases lane, and in Orchestrator → Phases — while the phase's own
  reading says the Orchestrator prepares it next. Nobody moved a member.
- **A list that does not resolve blocks, with its reason**, on #75's
  face and #76's card.
- **Both themes, 1280 and 390 px.** Reading any of it writes nothing.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
PHASE = "70-pq-ship-the-queue.md"
FIRST = "71-pq-first-member.md"
LATER = "72-pq-later-member.md"
BROKEN = "75-pq-broken-list.md"
STRAY = "76-pq-stray-member.md"
SETTINGS = {"X-Bench-Settings": "1"}
VIEWPORTS = [(1280, 900), (390, 844)]
ALL = (PHASE, FIRST, LATER, BROKEN, STRAY)


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def api(path: str) -> dict:
    code, body = S["backend"].get(path)
    if code != 200:
        raise bv.CheckFailed(f"GET {path} answered {code}")
    return json.loads(body)


def task(name: str) -> dict:
    state = api("/api/state")
    for stage in (state.get("board") or {}).get("stages") or []:
        for t in stage.get("tasks") or []:
            if t.get("file") == name:
                return t
    return {}


def life(name: str = PHASE) -> dict:
    return (api("/api/state").get("phaseLifecycle") or {}).get(name) or {}


def stage_of(name: str) -> str | None:
    for stage in ("backlog", "to-do", "in-progress", "review", "done"):
        if (S["backend"].work / "tasks" / stage / name).exists():
            return stage
    return None


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


def card(title, number, status, extra="", body="A disposable card for the queue case."):
    return (f"# {number} — {title}\n\n**Status:** {status}\n**Priority:** Low\n{extra}\n"
            f"{body}\n")


def until(predicate, timeout=15, what="the board"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise bv.CheckFailed(f"{what} never settled")


class AQueuedPhase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        tasks = backend.work / "tasks"
        cls.local = backend.tool / "manager" / "local"
        (tasks / "backlog" / PHASE).write_text(card(
            "PQ ship the queue", "70", "Backlog", "**Type:** Phase\n",
            "A disposable phase.\n\n## Cards\n\n- 71 — PQ first member\n"
            "- 72 — PQ later member\n"), encoding="utf-8")
        (tasks / "backlog" / FIRST).write_text(
            card("PQ first member", "71", "Backlog"), encoding="utf-8")
        (tasks / "backlog" / LATER).write_text(
            card("PQ later member", "72", "Backlog", "**Depends on:** 71\n"),
            encoding="utf-8")
        (tasks / "to-do" / BROKEN).write_text(card(
            "PQ broken list", "75", "To Do", "**Type:** Phase\n",
            "A phase whose list names a card no board has.\n\n## Cards\n\n"
            "- 76 — PQ stray member\n- 99 — nobody\n"), encoding="utf-8")
        (tasks / "backlog" / STRAY).write_text(
            card("PQ stray member", "76", "Backlog"), encoding="utf-8")
        # the Bench scope only: no phase entry, nothing adopted (task 188)
        doc = {"v": 1, "kind": "bench-orchestration-agreement",
               "bench": {"enabled": True, "paused": False,
                         "authority": {w: True for w in (
                             "readiness", "reasoning", "implement", "review",
                             "repair", "recovery", "integrate", "merge")},
                         "capacityModel": "shared",
                         "capacity": {"implementation": 2, "review": 1,
                                      "reasoning": 0, "dailyStarts": 20,
                                      "launchesPerCard": 6, "repairRounds": 2,
                                      "transientRetries": 2, "runAgeMinutes": 120,
                                      "recoveryAttempts": 2},
                         "scopeDefaults": {"implementation": 1, "review": 1,
                                           "reasoning": 1, "dailyStarts": 10},
                         "exclusions": [], "handback": []},
               "phases": {}}
        reading = api("/api/orchestration")["orchestration"]
        draft = (reading.get("draft") or {}).get("revision") or "none"
        code, body = backend.post("/api/orchestration/agreement/draft",
                                  {"document": doc, "expected": draft},
                                  headers=SETTINGS)
        if code != 200:
            raise bv.SetupError(f"could not save the draft: {body[:300]!r}")
        saved = json.loads(body)["result"]
        if not saved.get("ok") or saved.get("problems"):
            raise bv.SetupError(f"the case's agreement is invalid: {saved}")
        effective = api("/api/orchestration")["orchestration"]["effective"]["revision"]
        code, body = backend.post("/api/orchestration/agreement/apply",
                                  {"expected": effective, "draft": saved["draft"]},
                                  headers=SETTINGS)
        if code != 200:
            raise bv.SetupError(f"could not apply the agreement: {body[:300]!r}")
        until(lambda: life().get("word") == "not-queued", 20,
              "the unqueued phase's lifecycle")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        time.sleep(1)
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            for name in ALL:
                (backend.work / "tasks" / stage / name).unlink(missing_ok=True)
        (cls.local / "orchestration.json").unlink(missing_ok=True)
        shutil.rmtree(cls.local / "state" / "orchestration", ignore_errors=True)
        time.sleep(2.5)

    def stored(self) -> dict:
        return {n: stage_of(n) for n in ALL}

    def lane_member(self, name: str) -> str:
        return S["browser"].eval(f"""(() => {{
          const c = document.querySelector('.lane article.card[data-file="{name}"]');
          return c ? c.innerText : '';
        }})()""") or ""

    def test_1_before_the_move_nothing_is_queued(self):
        self.assertEqual("not-queued", life()["word"])
        self.assertEqual("phase scope", life()["owner"])
        self.assertIn("move the phase card into To Do", life()["next"]["text"])
        for name in (FIRST, LATER):
            q = task(name).get("queue") or {}
            self.assertEqual(("not-queued", False), (q.get("state"), q.get("inherited")))

    def test_2_one_move_queues_the_phase_and_every_member(self):
        before = self.stored()
        code, body = S["backend"].post("/api/move", {"file": PHASE, "from": "backlog",
                                                     "to": "to-do"})
        self.assertEqual(200, code, body[:300])
        until(lambda: (task(FIRST).get("queue") or {}).get("inherited"), 15,
              "the inherited queue")
        for name in (FIRST, LATER):
            q = task(name)["queue"]
            self.assertEqual(("queued", "to-do", "backlog", True),
                             (q["state"], q["effective"], q["stage"], q["inherited"]),
                             name)
        expected = dict(before, **{PHASE: "to-do"})
        self.assertEqual(expected, self.stored(), "only the phase card moved")
        reading = life()
        self.assertEqual("preparing", reading["word"])
        self.assertEqual(sorted([FIRST, LATER]), sorted(reading["inherited"]))
        projected = next(p for p in api("/api/orchestration")
                         ["schedule"]["phases"]["phases"] if p["card"] == PHASE)
        self.assertEqual("preparing", projected["lifecycle"]["word"],
                         "the API state and the Orchestrator say one thing")
        self.assertFalse(projected["named"], "no per-phase setting exists")
        for width, height in VIEWPORTS:
            for which in ("dark", "light"):
                b = S["browser"]
                b.viewport(width, height)
                b.goto("/phases")
                theme(b, which)
                if not b.wait(f"/queued with phase/.test((document.querySelector("
                              f"'.lane article.card[data-file=\"{FIRST}\"]') || {{}}).innerText || '')", 15):
                    raise bv.CheckFailed(f"the lane never said #71 is queued: "
                                         f"{self.lane_member(FIRST)!r}")
                self.assertIn("queued with phase", self.lane_member(LATER))
                shot(f"phase-queue-lane-{width}-{which}")
                b.goto("/orchestrator/phases?card=70")
                if not b.wait("/Queued with the phase, stored in Backlog/.test("
                              "(document.querySelector('#ophase-70') || {}).innerText || '')", 15):
                    raise bv.CheckFailed("Orchestrator → Phases never named the queued members")
                text = b.eval("document.querySelector('#ophase-70').innerText")
                self.assertNotIn("phase runner", text)
                self.assertIsNone(b.eval("document.querySelector('[data-o^=\"adopt-phase\"]')"))
                shot(f"phase-queue-orchestrator-{width}-{which}")
        self.assertEqual(expected, self.stored(), "reading moved nothing")

    def test_3_a_list_that_does_not_resolve_says_why(self):
        reading = life(BROKEN)
        self.assertEqual("blocked", reading["word"])
        self.assertIn("99", reading["line"])
        q = task(STRAY)["queue"]
        self.assertEqual(("blocked", False), (q["state"], q["inherited"]))
        self.assertIn("99", q["reason"])
        for width, height in VIEWPORTS:
            for which in ("dark", "light"):
                b = S["browser"]
                b.viewport(width, height)
                b.goto("/phases")
                theme(b, which)
                if not b.wait(f"/not queued/.test((document.querySelector("
                              f"'.lane article.card[data-file=\"{STRAY}\"]') || {{}}).innerText || '')", 15):
                    raise bv.CheckFailed(f"#76 never said it is not queued: "
                                         f"{self.lane_member(STRAY)!r}")
                shot(f"phase-queue-blocked-{width}-{which}")
        self.assertEqual("backlog", stage_of(STRAY))


if __name__ == "__main__":
    unittest.main()
