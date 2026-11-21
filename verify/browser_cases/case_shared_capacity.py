"""Shared orchestration capacity in the Orchestrator view (task 182),
against the disposable backend's own routes.

- a legacy agreement that reserves slots for phases says so, and the
  migration is previewed (nothing written) and then applied by an armed
  click, bound to the revision the page read; afterwards a delivered phase
  uses no slot and the page speaks of global totals, never allocations;
- a repair of a card in Review whose outcome is unknown holds an
  implementation slot, and the page names it among the runs holding a
  slot, with the board's in-use and available counts;
- global limits and the default phase ceiling are edited in the draft,
  previewed and applied, and read back from the agreement in force;
- both themes at 1440 and 390 px, with screenshots, and no overflow.

The agreement is never applied with this installation designated, and
BOARD_ORCHESTRATE_INTERVAL holds the pass off: nothing is launched by
orchestration in this case. Everything the case added is removed after it.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
CARD = "1821-export-totals.md"
REVIEW = "1822-fix-the-totals.md"
PHASE = "1823-ship-the-ledger.md"
OP = "op-" + "8" * 20

CARD_TEXT = ("# 1821 — Export totals\n\n**Status:** To Do\n**Priority:** Medium\n\n"
             "Export the ledger totals.\n")
REVIEW_TEXT = ("# 1822 — Fix the totals\n\n**Status:** Review\n**Priority:** High\n\n"
               "The totals are off by one.\n")
PHASE_TEXT = ("# 1823 — Ship the ledger\n\n**Status:** Done\n**Priority:** Medium\n"
              "**Type:** Phase\n\n## Cards\n\n- 1821 — Export totals\n")

LEGACY = {"v": 1, "kind": "bench-orchestration-agreement",
          "bench": {"enabled": True, "paused": False,
                    "authority": {"implement": True, "review": True, "repair": True},
                    "capacity": {"implementation": 2, "review": 1, "reasoning": 0,
                                 "dailyStarts": 20},
                    "exclusions": [], "handback": []},
          "phases": {"1823": {"enabled": True, "allocation": {"implementation": 1}}}}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api(path="/api/orchestration") -> dict:
    code, body = S["backend"].get(path)
    if code != 200:
        raise bv.CheckFailed(f"GET {path} answered {code}")
    return json.loads(body)


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def metric(name, value):
    S["metrics"].setdefault("sharedCapacity", {})[name] = value


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


TEXT = "(document.querySelector('#oshell') || document.body).innerText"


def visit(path, ready, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto(path)
    if not b.wait(ready, 20):
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads "
                             f"{b.eval(TEXT + '.slice(0, 400)')!r}")
    return b


def double(b, selector):
    b.click(selector)
    found = f"document.querySelector({json.dumps(selector)})"
    if not b.wait(f"{found} && {found}.classList.contains('armed')", 5):
        raise bv.CheckFailed(f"{selector} did not arm on the first click")
    b.click(selector)


def text_has(words: str) -> str:
    return f"{TEXT}.includes({json.dumps(words)})"


class SharedCapacity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        tasks = backend.work / "tasks"
        (tasks / "to-do" / CARD).write_text(CARD_TEXT, encoding="utf-8")
        (tasks / "review" / REVIEW).write_text(REVIEW_TEXT, encoding="utf-8")
        (tasks / "done" / PHASE).write_text(PHASE_TEXT, encoding="utf-8")
        cls.local = backend.tool / "manager" / "local"
        cls.saved = {}
        for name in ("orchestration.json",):
            path = cls.local / name
            cls.saved[name] = path.read_bytes() if path.exists() else None
        state = cls.local / "state" / "orchestration"
        cls.saved_state = state.exists()
        if state.exists():
            shutil.copytree(state, cls.local / "state" / "orchestration.182-saved")
        (state / "draft.json").unlink(missing_ok=True)
        (cls.local / "orchestration.json").write_text(json.dumps(LEGACY), encoding="utf-8")
        deadline = time.time() + 15
        while time.time() < deadline:
            files = [t["file"] for s in api("/api/state")["board"]["stages"] for t in s["tasks"]]
            if CARD in files and REVIEW in files and PHASE in files:
                break
            time.sleep(0.3)
        else:
            raise bv.SetupError("the backend never listed the case's cards")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            for name in (CARD, REVIEW, PHASE):
                (backend.work / "tasks" / stage / name).unlink(missing_ok=True)
        for name, raw in cls.saved.items():
            path = cls.local / name
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(raw)
        state = cls.local / "state" / "orchestration"
        shutil.rmtree(state, ignore_errors=True)
        saved = cls.local / "state" / "orchestration.182-saved"
        if saved.exists():
            saved.rename(state)
        time.sleep(2.5)

    # ── 1. a reserved agreement, migrated ────────────────────────────────

    def test_1_a_reserved_agreement_is_migrated_explicitly(self):
        b = visit("/orchestrator", text_has("Capacity is reserved per phase"))
        theme(b, "dark")
        self.assertIn("Phase #1823 reserves 1 implementation", b.eval(TEXT))
        shot("shared-capacity-legacy-notice-1440-dark")
        before = (self.local / "orchestration.json").read_bytes()
        b = visit("/orchestrator/agreement", "document.querySelector('[data-o=\"migrate-preview\"]')")
        self.assertTrue(b.eval("document.querySelector('[data-o=\"migrate-apply\"]').disabled"),
                        "migrating is offered before a preview")
        b.click("[data-o='migrate-preview']")
        if not b.wait("document.querySelector('.o-preview')", 10):
            raise bv.CheckFailed("the migration preview drew nothing")
        preview = b.eval("document.querySelector('.o-preview').innerText")
        self.assertIn("nothing was written", preview)
        self.assertIn("Phase #1823: reserved 1 implementation", preview)
        self.assertIn("default phase ceiling", preview)
        self.assertEqual(before, (self.local / "orchestration.json").read_bytes(),
                         "a preview wrote the agreement")
        shot("shared-capacity-migration-preview-1440-dark")
        double(b, "[data-o='migrate-apply']")
        deadline = time.time() + 15
        while time.time() < deadline:
            if api()["orchestration"]["effective"].get("capacityModel") == "shared":
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("migrating did not put shared capacity in force")
        doc = json.loads((self.local / "orchestration.json").read_text())
        self.assertNotIn("allocation", doc["phases"]["1823"])
        self.assertTrue(doc["bench"]["authority"]["implement"], "authority kept")
        found = api()["orchestration"]
        self.assertFalse(found["installation"]["executes"])
        self.assertEqual({}, (found["usage"]["running"] or {}).get("phase:1823", {}),
                         "a delivered phase holds no slot")
        b = visit("/orchestrator", text_has("first come, first served"))
        self.assertNotIn("allocated", b.eval(TEXT))
        self.assertIn("0 of 2 in use on the board · 2 available", b.eval(TEXT))
        metric("migration", "previewed then applied")

    # ── 2. a Review repair holds a slot ──────────────────────────────────

    def test_2_a_review_repair_holds_a_slot_and_is_named(self):
        ops = self.local / "state" / "orchestration" / "operations"
        ops.mkdir(parents=True, exist_ok=True)
        (ops / f"{OP}.json").write_text(json.dumps({
            "v": 1, "kind": "bench-orchestration-operation", "key": OP,
            "action": "repair", "file": REVIEW, "card": "1822", "scope": "bench",
            "pool": "implementation", "basis": "a" * 40, "attempt": 1,
            "status": "unknown", "intentAt": "2026-11-21T12:00:00Z",
            "intentEpoch": time.time(), "day": time.strftime("%Y-%m-%d", time.gmtime()),
            "history": []}), encoding="utf-8")
        try:
            self._holds_a_slot()
        finally:
            (ops / f"{OP}.json").unlink(missing_ok=True)
        metric("reviewRepairHoldsSlot", True)

    def _holds_a_slot(self):
        b = visit("/orchestrator", text_has("1 of 2 in use on the board · 1 available"))
        text = b.eval(TEXT)
        self.assertIn("Runs holding a slot", text)
        self.assertIn("reserved by operation " + OP, text)
        self.assertIn("card in review", text)
        used = api()["orchestration"]["usage"]
        self.assertEqual(1, used["totals"]["implementation"])
        for which in ("dark", "light"):
            for width in (1440, 390):
                b = visit("/orchestrator", text_has("Runs holding a slot"), width=width)
                theme(b, which)
                if not b.wait(text_has("Runs holding a slot"), 10):
                    raise bv.CheckFailed("the capacity panel was lost on a theme change")
                over = b.eval("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(over, 1, f"{width}px {which} overflows by {over}px")
                b.eval("(() => { const h = [...document.querySelectorAll('#oshell h3')]"
                       ".find(x => /first come, first served/.test(x.textContent));"
                       " if (h) h.scrollIntoView(); return true; })()")
                shot(f"shared-capacity-holders-{width}-{which}")

    # ── 3. global limits and the default ceiling, edited ─────────────────

    def test_3_global_limits_and_default_ceiling_are_edited(self):
        b = visit("/orchestrator/agreement", "document.querySelector('[data-o=\"start-draft-from\"]')")
        theme(b, "light")
        double(b, "[data-o='start-draft-from']")
        if not b.wait("document.querySelector('#ag-bench-scopeDefaults-implementation')", 10):
            raise bv.CheckFailed("the draft drew no default phase ceiling")
        text = b.eval(TEXT)
        self.assertIn("Global limits — shared, first come first served", text)
        self.assertIn("Default phase ceiling — reserves nothing", text)
        for sel, value in (("#ag-bench-capacity-implementation", "6"),
                           ("#ag-bench-scopeDefaults-implementation", "3")):
            b.eval(f"(() => {{ const i = document.querySelector('{sel}'); i.focus(); i.value = '{value}';"
                   " i.dispatchEvent(new Event('input', {bubbles: true}));"
                   " i.dispatchEvent(new Event('change', {bubbles: true})); return true; })()")
        shot("shared-capacity-editor-1440-light")
        double(b, "[data-o='save-draft']")
        deadline = time.time() + 15
        while time.time() < deadline:
            draft = api()["orchestration"]["draft"]
            if draft and ((draft["document"] or {}).get("bench") or {}).get(
                    "scopeDefaults", {}).get("implementation") == 3:
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("saving did not write the edited limits")
        rev = draft["revision"]
        if not b.wait(f"/{rev}/.test(document.querySelector('.o-draft h3')?.innerText || '')"
                      " && !document.querySelector('[data-o=\"apply\"]').disabled", 15):
            raise bv.CheckFailed("the saved draft could not be applied")
        b.click("[data-o='preview']")
        if not b.wait("document.querySelector('.o-preview')", 10):
            raise bv.CheckFailed("Preview drew nothing")
        self.assertIn("scopeDefaults", b.eval("document.querySelector('.o-preview').innerText"))
        double(b, "[data-o='apply']")
        deadline = time.time() + 15
        while time.time() < deadline:
            eff = api()["orchestration"]["effective"]
            if (eff.get("scopeDefaults") or {}).get("implementation") == 3:
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("applying did not put the new limits in force")
        bench = next(s for s in eff["scopes"] if s["id"] == "bench")
        self.assertEqual(6, bench["capacity"]["implementation"]["total"])
        phase = next(s for s in eff["scopes"] if s["id"] == "phase:1823")
        self.assertEqual(3, phase["capacity"]["implementation"]["ceiling"])
        b = visit("/orchestrator", text_has("0 of 6 in use on the board · 6 available"), width=390)
        shot("shared-capacity-overview-390-light")
        metric("limitsEdited", {"global": 6, "defaultCeiling": 3})


if __name__ == "__main__":
    unittest.main()
