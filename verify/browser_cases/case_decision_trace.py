"""Orchestrator → Decision trace (task 184), against the disposable backend.

The case writes a small, known trace into the backend's own state — the
version 1 records `decisiontrace` writes — and reads it through the page:

- passes listed newest first with their outcome in words: an
  implementation launch, a review launched beside a repair refused at
  execution, three identical idle passes folded into one entry with an
  exact count, a pass not evaluated (a replica), an error pass whose text
  carries markup (drawn as text), and an unreadable line named;
- an entry expanded by pointer and by keyboard shows what was selected,
  the alternatives skipped, the facts considered and the next step;
- outcome filtering, an entry's deep link, a missing entry, and a card's
  context with its way back to the card;
- reading changes nothing: the trace, the ledger and the cards are byte
  for byte the same afterwards, and no pass ran;
- both themes at 1440 and 390 px, with screenshots and no overflow.

BOARD_ORCHESTRATE_INTERVAL holds the scheduler off and no agreement is in
force, so nothing here is evaluated or launched. Everything the case
added is removed after it.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
EPOCH = "c0ffee01"
CARDS = {
    "1841-export-csv.md": ("to-do", "Export the table as CSV"),
    "1842-import-csv.md": ("to-do", "Import CSV"),
    "1843-totals-review.md": ("review", "Totals in review"),
    "1844-fix-rounding.md": ("review", "Fix rounding"),
    "1845-old-report.md": ("in-progress", "Old report"),
}


def eid(seq: int) -> str:
    return f"{EPOCH}-{seq}"


def card(n: str, *, scope="bench", stage, action="implement", state, code,
         reason, selected=False, outcome, result=None, pool="implementation",
         kind="implement", facts=None) -> dict:
    name = next(f for f in CARDS if f.startswith(n + "-"))
    return {"kind": kind, "card": name, "number": n, "scope": scope, "stage": stage,
            "action": action, "pool": pool, "state": state, "code": code,
            "reason": reason, "selected": selected, "outcome": outcome,
            "result": result, "facts": facts or {"priority": "Medium",
                                                 "title": f"{n} — {CARDS[name][1]}"}}


def record(seq: int, at: str, decisions: list, headline: str, **extra) -> dict:
    base = {"v": 1, "kind": "pass", "epoch": EPOCH, "seq": seq, "id": eid(seq),
            "at": at, "atEpoch": 0, "status": "evaluated", "boot": "b1",
            "idle": False, "fingerprint": f"fp{seq}", "decisions": decisions,
            "headline": headline, "seconds": 0.12,
            "agreement": {"revision": "a1b2c3d4"},
            "owner": {"role": "designated-here"},
            "capacity": {"limits": {"implementation": 1}, "inUse": {"implementation": 0}}}
    base.update(extra)
    return base


def trace_lines() -> list[str]:
    idle = [card("1842", stage="to-do", state="blocked", code="waiting-dependency",
                 reason="it depends on card 1841, which is in in-progress/",
                 outcome="blocked", facts={"dependsOn": ["1841"], "priority": "Low"}),
            card("1845", stage="in-progress", state="blocked",
                 code="card-launches-exhausted",
                 reason="card 1845 has had 6 of its 6 launches", outcome="blocked")]
    lines = [
        record(1, "2026-11-21T10:00:00Z", [
            card("1841", stage="to-do", state="chosen", code="chosen",
                 reason="chosen this pass", selected=True, outcome="launched",
                 result={"outcome": "launched", "key": "op-" + "4" * 20, "agent": "run-1"},
                 facts={"priority": "High", "basis": "9f86d081884c7d65",
                        "title": "1841 — Export the table as CSV"}),
            card("1842", stage="to-do", state="waiting", code="slots-full",
                 reason="all 1 implementation slots on this board are in use; it is "
                        "admitted, in its turn, when one frees", outcome="waiting")],
            "implement #1841: launched"),
        record(2, "2026-11-21T10:00:30Z", [
            card("1843", kind="delivery", stage="review", action="review", pool="review",
                 state="ready", code="ready", reason="checks met; an independent review is next",
                 selected=True, outcome="launched",
                 result={"outcome": "launched", "key": "op-" + "5" * 20},
                 facts={"head": "a" * 40}),
            card("1844", kind="delivery", stage="review", action="repair",
                 state="ready", code="ready", reason="changes were requested at this head",
                 selected=True, outcome="refused",
                 result={"outcome": "refused", "code": "authority-revoked",
                         "reason": "not-eligible: ada moved it last"})],
            "review #1843: launched; repair #1844: refused"),
        record(3, "2026-11-21T10:01:00Z", idle, "Wait / No action: 2 blocked",
               idle=True, fingerprint="fp-idle"),
    ]
    out = [json.dumps(r, sort_keys=True) for r in lines]
    for seq, at in ((4, "2026-11-21T10:01:30Z"), (5, "2026-11-21T10:02:00Z")):
        out.append(json.dumps({"v": 1, "kind": "repeat", "of": eid(3), "epoch": EPOCH,
                               "seq": seq, "id": eid(seq), "at": at,
                               "fingerprint": "fp-idle", "boot": "b1"}))
    out.append("{this line is not a record")
    out.append(json.dumps(record(6, "2026-11-21T10:02:30Z", [],
                                 "Not evaluated: another installation executes",
                                 status="skipped", why="replica",
                                 reason="another installation executes", idle=True),
                          sort_keys=True))
    out.append(json.dumps(record(7, "2026-11-21T10:03:00Z", idle[:1],
                                 "The pass stopped with an error",
                                 status="error",
                                 error="RuntimeError: <script>alert(1)</script>"),
                          sort_keys=True))
    return out


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api(path) -> tuple[int, dict]:
    code, body = S["backend"].get(path)
    return code, json.loads(body or b"{}")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


TEXT = "(document.querySelector('#oshell') || document.body).innerText"


def text_has(words: str) -> str:
    return f"{TEXT}.includes({json.dumps(words)})"


def visit(path, ready, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto(path)
    if not b.wait(ready, 20):
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads "
                             f"{b.eval(TEXT + '.slice(0, 600)')!r}")
    return b


def entry_sel(seq: int) -> str:
    return f'[data-o="tr-{eid(seq)}"]'


def snapshot(*roots) -> dict:
    found = {}
    for root in roots:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found[str(path)] = path.read_bytes()
    return found


class DecisionTrace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.tasks = backend.work / "tasks"
        for name, (stage, title) in CARDS.items():
            n = name.split("-")[0]
            (cls.tasks / stage / name).write_text(
                f"# {n} — {title}\n\n**Status:** x\n**Priority:** Medium\n\nA brief.\n",
                encoding="utf-8")
        cls.state = backend.tool / "manager" / "local" / "state" / "orchestration"
        cls.saved = cls.state.with_name("orchestration.184-saved")
        if cls.state.exists():
            shutil.move(str(cls.state), str(cls.saved))
        cls.trace = cls.state / "trace"
        cls.trace.mkdir(parents=True)
        (cls.trace / "seg-000000000001.jsonl").write_text(
            "\n".join(trace_lines()) + "\n", encoding="utf-8")
        (cls.trace / "meta.json").write_text(json.dumps({
            "v": 1, "kind": "bench-decision-trace", "epoch": EPOCH,
            "started": "2026-11-21T10:00:00Z"}), encoding="utf-8")
        deadline = time.time() + 15
        while time.time() < deadline:
            _code, st = api("/api/state")
            files = [t["file"] for s in st["board"]["stages"] for t in s["tasks"]]
            if all(f in files for f in CARDS):
                break
            time.sleep(0.3)
        else:
            raise bv.SetupError("the backend never listed the case's cards")

    @classmethod
    def tearDownClass(cls):
        for name, (stage, _title) in CARDS.items():
            for where in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
                (cls.tasks / where / name).unlink(missing_ok=True)
        shutil.rmtree(cls.state, ignore_errors=True)
        if cls.saved.exists():
            shutil.move(str(cls.saved), str(cls.state))
        time.sleep(2.5)

    def test_1_passes_are_listed_newest_first_with_their_outcomes(self):
        code, body = api("/api/orchestration/trace")
        self.assertEqual(200, code)
        self.assertEqual([eid(7), eid(6), eid(3), eid(2), eid(1)],
                         [e["id"] for e in body["trace"]["entries"]])
        b = visit("/orchestrator/trace", "document.querySelectorAll('.o-trace-item').length >= 5")
        theme(b, "dark")
        text = b.eval(TEXT)
        for words in ("Decision trace", "What the trace records", "deterministic",
                      "agent-reported", "×3 identical passes", "Wait / No action",
                      "Not evaluated", "Error — partial pass", "1 unreadable line(s)",
                      "never committed or synced", "Last pass evaluated"):
            self.assertIn(words, text)
        self.assertEqual(5, b.eval("document.querySelectorAll('.o-trace-item').length"))
        self.assertTrue(b.eval("document.querySelector('[data-o=\"nav-trace\"]')"
                               ".getAttribute('aria-current') === 'page'"))
        # untrusted text is drawn as text
        b.click(entry_sel(7) + " > summary")
        if not b.wait(text_has("The pass stopped with an error"), 10):
            raise bv.CheckFailed("the error pass did not open")
        self.assertIn("<script>alert(1)</script>", b.eval(TEXT))
        self.assertEqual(0, b.eval("document.querySelectorAll('#oshell script').length"))
        shot("decision-trace-list-1440-dark")

    def test_2_an_entry_says_why_this_card_and_why_not_that_one(self):
        b = visit("/orchestrator/trace", f"document.querySelector('{entry_sel(1)}')")
        b.click(entry_sel(1) + " > summary")
        if not b.wait(text_has("Selected this pass"), 10):
            raise bv.CheckFailed("the launch entry did not open")
        body = f"document.querySelector('{entry_sel(1)}').innerText"
        text = b.eval(body)
        self.assertIn("Then: Launched", text)
        self.assertIn("Alternatives skipped (1)", text)
        self.assertIn("Facts considered", text)
        b.click(entry_sel(1) + " .o-tdec.selected details:nth-of-type(2) > summary")
        if not b.wait(f"{body}.includes('slots-full')", 5):
            raise bv.CheckFailed("alternatives skipped did not name the waiting card")
        b.click(entry_sel(1) + " .o-tdec.selected details:nth-of-type(1) > summary")
        if not b.wait(f"{body}.includes('9f86d081884c7d65')", 5):
            raise bv.CheckFailed("facts considered did not show the brief revision")
        # the waiting card: why not, and what happens next
        self.assertIn("Next (automatic)", b.eval(body))
        # revalidation keeps both halves: selected, then refused
        # read the entry itself: the outcome filter's options carry the
        # same words, so the whole view's text would already match
        b.click(entry_sel(2) + " > summary")
        refused = f"document.querySelector('{entry_sel(2)}').innerText"
        if not b.wait(f"{refused}.includes('Refused at execution') && "
                      f"{refused}.includes('revalidated at execution')", 10):
            raise bv.CheckFailed("the refused repair did not say it was refused when "
                                 "revalidated at execution: " + str(b.eval(refused))[:400])
        shot("decision-trace-entry-1440-dark")
        # keyboard: a folded idle entry opens from its summary
        b.eval(f"document.querySelector('{entry_sel(3)} > summary').focus()")
        b.key("Enter")
        if not b.wait(f"document.querySelector('{entry_sel(3)}').open", 5):
            raise bv.CheckFailed("Enter on a summary did not open the entry")
        if not b.wait(text_has("3 consecutive passes"), 10):
            raise bv.CheckFailed("the folded entry did not give its exact count")
        self.assertIn("Next (a person)", b.eval(TEXT))

    def test_3_filters_deep_links_and_the_card_context(self):
        b = visit("/orchestrator/trace", "document.querySelector('.o-tfilters select[data-k=\"outcome\"]')")
        b.eval("(() => { const s = document.querySelector('.o-tfilters select[data-k=\"outcome\"]');"
               " s.value = 'refused'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        b.click("[data-o='trace-apply']")
        if not b.wait("document.querySelectorAll('.o-trace-item').length === 1", 10):
            raise bv.CheckFailed("filtering by outcome did not leave the one refused pass")
        self.assertIn("repair #1844", b.eval(TEXT))
        b.click("[data-o='trace-clear']")
        if not b.wait("document.querySelectorAll('.o-trace-item').length === 5", 10):
            raise bv.CheckFailed("clearing the filters did not bring every pass back")
        b = visit(f"/orchestrator/trace?entry={eid(4)}", text_has("Linked entry"))
        if not b.wait(text_has("3 consecutive passes"), 10):
            raise bv.CheckFailed("a repeat's id did not open the entry it folds into")
        b = visit("/orchestrator/trace?entry=deadbeef-999", text_has("No entry"))
        b = visit("/orchestrator/trace?card=1844", text_has("Back to #1844"))
        self.assertEqual(1, b.eval("document.querySelectorAll('.o-trace-item').length"))
        b.click("a[href='/cards/1844']")
        if not b.wait("location.pathname === '/cards/1844'", 10):
            raise bv.CheckFailed("the way back did not open the card")
        shot("decision-trace-card-return-1440-dark")

    def test_4_reading_the_trace_changes_nothing(self):
        tool_state = S["backend"].tool / "manager" / "local" / "state"
        before = snapshot(tool_state / "orchestration", self.tasks)
        b = visit("/orchestrator/trace", "document.querySelectorAll('.o-trace-item').length >= 5")
        for seq in (1, 2, 3, 7):
            b.click(entry_sel(seq) + " > summary")
        b.click("[data-o='trace-reload']")
        time.sleep(1.5)
        self.assertEqual(before, snapshot(tool_state / "orchestration", self.tasks))
        _code, found = api("/api/orchestration")
        self.assertIsNone(found["schedule"].get("lastPass"))
        self.assertFalse((tool_state / "orchestration" / "operations").exists())

    def test_5_both_themes_at_both_widths(self):
        for width, height in ((1440, 900), (390, 844)):
            for which in ("light", "dark"):
                b = visit("/orchestrator/trace", "document.querySelectorAll('.o-trace-item').length >= 5",
                          width, height)
                theme(b, which)
                b.click(entry_sel(1) + " > summary")
                b.wait(text_has("Selected this pass"), 10)
                over = b.eval("document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(over, 1, f"{width}px {which}: the page scrolls sideways")
                shot(f"decision-trace-{width}-{which}")
