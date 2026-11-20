"""A scoped phase's lifecycle and recovery in a real browser (task 160).

A disposable phase card #60 of this case's own, adopted into its phase
scope exactly as `phasescope.adopt` records it (one `scope adopted by …`
line in its phase log), its branch cut in the fixture's application
repository, and an operating agreement naming it put in force through
the board's own routes. This installation is never designated to execute
and the orchestration pass is held off, so nothing here is launched,
merged or moved by an orchestrator. Its members are #61, the first in
run order, and #62, which depends on #61 and sits in In Progress with
no run, no reservation and no branch — the #157/#158 accident.

- **The face names the owner and the state.** The phase card never says
  "not started" or offers ▸ run phase once adopted: with the member moved
  early it names that and the way back; with a run on #61 it reads
  "phase scope — on #61" in the working register. `/api/state`'s
  `phaseLifecycle` and `GET /api/orchestration` say the same word.
- **The way back is a person's, and guarded.** In the Phases lane #62
  carries ↩ to to-do, fired by keyboard and by pointer (armed, then
  fired); the lane's stage columns take a dropped member through the
  same move. A member with a run on it is refused with ‖ hold named, and
  nothing moves.
- **Both themes, 1280 and 390 px.** Reading any of it writes nothing.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
PHASE = "60-lc-ship-the-lifecycle.md"
FIRST = "61-lc-first-member.md"
LATER = "62-lc-later-member.md"
BRANCH = "phase/60-lc-ship-the-lifecycle"
SETTINGS = {"X-Bench-Settings": "1"}
VIEWPORTS = [(1280, 900), (390, 844)]


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


def life() -> dict:
    return (api("/api/state").get("phaseLifecycle") or {}).get(PHASE) or {}


def stage_of(name: str) -> str | None:
    for stage in ("backlog", "to-do", "in-progress", "review", "done"):
        if (S["backend"].work / "tasks" / stage / name).exists():
            return stage
    return None


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


def card(title, number, status, extra="", body="A disposable card for the lifecycle case."):
    return (f"# {number} — {title}\n\n**Status:** {status}\n**Priority:** Low\n{extra}\n"
            f"{body}\n")


def until(predicate, timeout=15, what="the board"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise bv.CheckFailed(f"{what} never settled")


class AScopedPhase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        tasks = backend.work / "tasks"
        cls.local = backend.tool / "manager" / "local"
        (tasks / "in-progress" / PHASE).write_text(card(
            "LC ship the lifecycle", "60", "In Progress", "**Type:** Phase\n",
            "A disposable phase.\n\n## Cards\n\n- 61 — LC first member\n"
            "- 62 — LC later member\n\n## Phase log\n\n"
            "- 2026-11-20 09:00 · scope adopted by ada — the phase:60 scope "
            "schedules this phase from here; the phase runner stands down\n"),
            encoding="utf-8")
        (tasks / "in-progress" / FIRST).write_text(
            card("LC first member", "61", "In Progress"), encoding="utf-8")
        (tasks / "in-progress" / LATER).write_text(
            card("LC later member", "62", "In Progress", "**Depends on:** 61\n"),
            encoding="utf-8")
        backend._git(backend.app, "branch", BRANCH, "main")
        # an agreement naming the phase, through the board's own routes
        doc = {"v": 1, "kind": "bench-orchestration-agreement",
               "bench": {"enabled": True, "paused": False,
                         "authority": {w: True for w in (
                             "readiness", "reasoning", "implement", "review",
                             "repair", "recovery", "integrate", "merge")},
                         "capacity": {"implementation": 2, "review": 1,
                                      "reasoning": 0, "dailyStarts": 20,
                                      "launchesPerCard": 6, "repairRounds": 2,
                                      "transientRetries": 2, "runAgeMinutes": 120,
                                      "recoveryAttempts": 2},
                         "exclusions": [], "handback": []},
               # one launch a day, so the page explains what that covers
               "phases": {"60": {"enabled": True,
                                 "allocation": {"implementation": 1,
                                                "dailyStarts": 1}}}}
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
        until(lambda: life().get("word") == "moved-early", 20,
              "the scoped phase's lifecycle")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        if backend.refine_hold.exists():
            backend.refine_hold.unlink()
        time.sleep(1)
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            for name in (PHASE, FIRST, LATER):
                (backend.work / "tasks" / stage / name).unlink(missing_ok=True)
        try:
            backend._git(backend.app, "branch", "-D", BRANCH)
        except bv.SetupError:
            pass
        (cls.local / "orchestration.json").unlink(missing_ok=True)
        shutil.rmtree(cls.local / "state" / "orchestration", ignore_errors=True)
        time.sleep(2.5)

    def passive(self) -> dict:
        backend = S["backend"]
        ops = self.local / "state" / "orchestration" / "operations"
        return {"cards": {n: stage_of(n) for n in (PHASE, FIRST, LATER)},
                "ops": sorted(p.name for p in ops.iterdir()) if ops.is_dir() else [],
                "refs": bv._git(backend.app, "for-each-ref",
                                "--format=%(refname) %(objectname)").stdout}

    def face(self) -> dict:
        return S["browser"].eval(f"""(() => {{
          const c = document.querySelector('#board article.card[data-file="{PHASE}"]');
          if (!c) return null;
          return {{ text: c.innerText, cls: c.className,
                    acts: [...c.querySelectorAll('.hoveracts button')].map(b => b.innerText) }};
        }})()""")

    def board(self, width, height):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/board")
        if not b.wait(f"!!document.querySelector('#board article.card[data-file=\"{PHASE}\"]')", 15):
            raise bv.CheckFailed("the phase card never reached the board")
        return b

    def test_1_a_member_moved_early_is_named_with_its_way_back(self):
        before = self.passive()
        self.assertEqual("phase scope", life()["owner"])
        projected = next(p for p in api("/api/orchestration")
                         ["schedule"]["phases"]["phases"] if p["card"] == PHASE)
        self.assertEqual("moved-early", projected["lifecycle"]["word"],
                         "API state and the Orchestrator say one thing")
        rows = {m["card"]: m["state"] for m in projected["members"]}
        self.assertEqual("moved-early", rows[LATER])
        for width, height in VIEWPORTS:
            for which in ("dark", "light"):
                b = self.board(width, height)
                theme(b, which)
                if not b.wait(f"/phase scope — #62 sits in In Progress.*move #62 back/s.test("
                              f"document.querySelector("
                              f"'#board article.card[data-file=\"{PHASE}\"]').innerText)", 10):
                    raise bv.CheckFailed(f"the face never named the moved member: {self.face()}")
                face = self.face()
                self.assertNotIn("not started", face["text"])
                self.assertIn("halted", face["cls"], "a mistake to recover from wears the alarm")
                self.assertFalse(any("run phase" in a for a in face["acts"]), face)
                self.assertTrue(any("orchestrator" in a for a in face["acts"]), face)
                shot(f"phase-lifecycle-moved-early-{width}-{which}")
        self.assertEqual(before, self.passive(), "reading changed something")

    def test_2_the_way_back_by_keyboard_pointer_and_drop(self):
        b = S["browser"]
        b.viewport(1280, 900)
        b.goto("/phases")
        sel = f".lane article.card[data-file=\"{LATER}\"] .hoveracts button"
        if not b.wait(f"[...document.querySelectorAll('{sel}')].some(x => /to to-do/.test(x.innerText))", 15):
            raise bv.CheckFailed("the moved member offered no way back in its lane")
        # keyboard: the card's actions show while it holds focus, as they
        # do on hover; focus the card, then the action; Enter arms, Enter fires
        card = f".lane article.card[data-file=\"{LATER}\"]"
        b.eval(f"document.querySelector('{card}').focus()")
        b.hover(card)
        b.eval(f"[...document.querySelectorAll('{sel}')].find(x => /to to-do/.test(x.innerText)).focus()")
        if not b.wait(f"document.activeElement && /to to-do/.test(document.activeElement.innerText)", 5):
            raise bv.CheckFailed("the way back could not take keyboard focus")
        b.key("Enter")
        if not b.wait(f"[...document.querySelectorAll('{sel}')].some(x => x.classList.contains('armed'))", 5):
            raise bv.CheckFailed("the way back did not ask for confirmation")
        # the armed window redraws the card; the keyboard stays on the action
        b.hover(card)
        b.eval(f"(() => {{ const x = document.querySelector('{sel}.armed');"
               f" if (x && document.activeElement !== x) x.focus(); }})()")
        b.key("Enter")
        until(lambda: stage_of(LATER) == "to-do", 10, "the keyboard move back")
        until(lambda: life().get("word") != "moved-early", 10, "the lifecycle after the move")
        # pointer: put it back by mistake again, then click twice
        code, body = S["backend"].post("/api/move", {"file": LATER, "from": "to-do",
                                                     "to": "in-progress"})
        self.assertEqual(200, code, body[:300])
        until(lambda: life().get("word") == "moved-early", 10, "the second mistake")
        b.goto("/phases")
        if not b.wait(f"[...document.querySelectorAll('{sel}')].some(x => /to to-do/.test(x.innerText))", 15):
            raise bv.CheckFailed("the way back did not come back")
        b.eval(f"document.querySelectorAll('{sel}').forEach((x, i) => x.dataset.lcIdx = i)")
        idx = b.eval(f"[...document.querySelectorAll('{sel}')].findIndex(x => /to to-do/.test(x.innerText))")
        target = f"{sel}[data-lc-idx=\"{idx}\"]"
        b.hover(f".lane article.card[data-file=\"{LATER}\"]")
        b.click(target)
        if not b.wait(f"!!document.querySelector('{sel}.armed')", 5):
            raise bv.CheckFailed("the pointer's first click did not arm the way back")
        b.hover(f".lane article.card[data-file=\"{LATER}\"]")
        b.click(f"{sel}.armed")
        until(lambda: stage_of(LATER) == "to-do", 10, "the pointer move back")
        shot("phase-lifecycle-moved-back-1280-light")
        # a drop on the lane's Backlog column is the same move
        b.goto("/phases")
        if not b.wait(f"!!document.querySelector('.lane[data-phase=\"{PHASE}\"] .lcol[data-stage=\"backlog\"]')", 10):
            raise bv.CheckFailed("the lane's Backlog column takes no drop")
        b.eval(f"""(() => {{
          const col = document.querySelector('.lane[data-phase="{PHASE}"] .lcol[data-stage="backlog"]');
          const dt = new DataTransfer();
          dt.setData('application/json', JSON.stringify({{ file: '{LATER}', from: 'to-do' }}));
          col.dispatchEvent(new DragEvent('dragover', {{ dataTransfer: dt, bubbles: true, cancelable: true }}));
          col.dispatchEvent(new DragEvent('drop', {{ dataTransfer: dt, bubbles: true, cancelable: true }}));
        }})()""")
        until(lambda: stage_of(LATER) == "backlog", 10, "the drop on Backlog")
        self.assertEqual("in-progress", stage_of(FIRST), "nothing else moved")

    def test_3_a_run_on_a_member_reads_as_the_scopes_work_and_holds_its_card(self):
        backend = S["backend"]
        backend.refine_hold.write_text("hold", encoding="utf-8")
        code, body = backend.post("/api/card/refine", {
            "file": FIRST, "stage": "in-progress", "text": "Tighten the wording",
            "request": f"case-160-{time.time()}"})
        self.assertEqual(200, code, body[:300])
        until(lambda: life().get("word") == "working", 20, "the run on #61")
        self.assertIn("on #61", life()["line"])
        for width, height in VIEWPORTS:
            for which in ("dark", "light"):
                b = self.board(width, height)
                theme(b, which)
                if not b.wait(f"/phase scope — on #61/.test(document.querySelector("
                              f"'#board article.card[data-file=\"{PHASE}\"]').innerText)", 10):
                    raise bv.CheckFailed(f"the face never named the work: {self.face()}")
                face = self.face()
                self.assertNotIn("not started", face["text"])
                self.assertNotIn("run phase", face["text"])
                self.assertIn("running", face["cls"])
                self.assertFalse(any("run phase" in a for a in face["acts"]), face)
                shot(f"phase-lifecycle-working-{width}-{which}")
        # the guarded way back refuses a member with a run on it
        code, body = backend.post("/api/move", {"file": FIRST, "from": "in-progress",
                                                "to": "to-do"})
        self.assertEqual(409, code, body[:300])
        refusal = json.loads(body)
        self.assertEqual("member-working", refusal["code"])
        self.assertIn("‖ hold", refusal["error"])
        self.assertEqual("in-progress", stage_of(FIRST))
        backend.refine_hold.unlink()
        until(lambda: life().get("word") != "working", 30, "the run's ending")


if __name__ == "__main__":
    unittest.main()
