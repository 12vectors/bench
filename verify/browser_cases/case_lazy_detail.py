"""A lightweight board, and each card's text read on opening (task 129).

`/api/state` lists cards without their Markdown; the detail view reads a
card's text from `GET /api/card` when it is opened. In a real browser:

1. the state the page reads carries no card's text, and is measured —
   bytes, the time to the first card — beside `/api/tasks`, which still
   serves every card's whole text (the shape the list had before);
2. opening a history-heavy card shows its brief, its reviews and its
   activity, and the time it took is measured;
3. switching cards quickly, with the first card's reply held and handed
   back last, never shows the first card's text in the second's view;
4. a failed read says so in an alert with a way to ask again, and the
   loading state is announced as busy;
5. a card edited on disk while open is read again at its new revision,
   an unsent comment surviving the redraw, and state reloads (as a
   reconnect makes) ask for nothing while the revision stands;
6. a card moved on disk while open is followed to its new stage, and one
   deleted closes the view.

The measurements are written to `lazy-detail-measurements.json` beside
the screenshots, and printed.
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
SMALL = 60
HEAVY = ("92-heavy-history-one.md", "93-heavy-history-two.md", "94-heavy-history-three.md")
BRIEF_LINE = "The brief of {name}, which the detail view must show."
HISTORY_SENTINEL = "HISTORY-SENTINEL"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def small(n: int) -> str:
    return (f"# Small card {n}\n\n**Status:** Backlog\n**Priority:** Low\n\n"
            f"## Context\n\nA short card.\n\n## Acceptance\n\n- [ ] It holds\n")


def heavy(name: str) -> str:
    report = (f"{HISTORY_SENTINEL} The run wrote this down at length, as real reports are. " * 40)
    rounds = "".join(
        f"\n---\n\n## Work report — 2026-11-{i + 1:02d} 10:00 (Wren)\n\n{report}\n"
        f"\n---\n\n## PR review at {i:012x} — 2026-11-{i + 1:02d} 12:00 (Olive)\n\n{report}\n\n"
        f"PR REVIEW: REQUEST CHANGES\n" for i in range(20))
    return (f"# {name[:-3]}\n\n**Status:** Review\n**Priority:** Medium\n\n"
            f"## Context\n\n{BRIEF_LINE.format(name=name)}\n\n## Acceptance\n\n"
            f"- [ ] The brief is shown\n- [ ] The history is shown\n{rounds}")


# Holds every /api/card reply whose URL contains `__holdFor` until the case
# releases it, counts /api/card requests, and can make them fail.
GATE = """(() => {
  const real = window.__realFetch || window.fetch;
  window.__realFetch = real;
  window.__cards = { asked: [] }; window.__held = []; window.__holdFor = null; window.__failCards = false;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (!u.startsWith('/api/card?')) return real(url, opts);
    __cards.asked.push(u);
    if (__failCards) return new Response(JSON.stringify({ error: 'the test refused it' }), { status: 500 });
    if (__holdFor && u.includes(__holdFor)) await new Promise((go) => __held.push(go));
    return real(url, opts);
  };
  return true;
})()"""

RESTORE = "(() => { if (window.__realFetch) window.fetch = window.__realFetch; return true; })()"

BRIEF = "(document.querySelector('#dsec-brief') || {}).innerText || ''"


class LazyDetail(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.tasks = backend.work / "tasks"
        cls.files = []
        for n in range(SMALL):
            path = cls.tasks / "backlog" / f"{200 + n}-small-card-{n}.md"
            path.write_text(small(n), encoding="utf-8")
            cls.files.append(path)
        for name in HEAVY:
            path = cls.tasks / "review" / name
            path.write_text(heavy(name), encoding="utf-8")
            cls.files.append(path)
        cls.measured = {}

    @classmethod
    def tearDownClass(cls):
        for path in cls.files:
            path.unlink(missing_ok=True)
        for stage in ("done", "review"):
            for name in HEAVY:
                (cls.tasks / stage / name).unlink(missing_ok=True)
        out = S["shots"] / "lazy-detail-measurements.json"
        out.write_text(json.dumps(cls.measured, indent=1, sort_keys=True), encoding="utf-8")
        print("\nlazy-detail measurements:", json.dumps(cls.measured, sort_keys=True))

    def tearDown(self):
        b = S["browser"]
        b.eval(RESTORE)
        b.eval("(() => { S.selected = null; renderDrawer(); return true; })()")

    def load(self):
        b = S["browser"]
        b.viewport(1280, 900)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('#board article.card').length > 0", 15),
                        "the board never drew a card")
        return b

    def open(self, name: str):
        b = S["browser"]
        sel = f'#board article.card[data-file="{name}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 10), f"{name} is not on the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        b.click("#dtab-brief")

    def test_1_the_list_carries_no_text_and_is_measured(self):
        backend = S["backend"]
        code, state = backend.get("/api/state")
        self.assertEqual(code, 200)
        code, tasks = backend.get("/api/tasks")
        self.assertEqual(code, 200)
        payload = json.loads(state)
        cards = [t for s in payload["board"]["stages"] for t in s["tasks"]]
        self.assertTrue(cards)
        self.assertFalse([t["file"] for t in cards if "body" in t or "parts" in t],
                         "a card in the state still carries its text")
        self.assertNotIn(HISTORY_SENTINEL.encode(), state, "a history reached the list")
        self.assertIn(HISTORY_SENTINEL.encode(), tasks)
        self.assertLess(len(state), len(tasks))
        b = self.load()
        first = b.eval("performance.now()")
        timing = b.eval("""(() => {
          const e = performance.getEntriesByType('resource').find(r => r.name.includes('/api/state'));
          return e ? { bytes: e.encodedBodySize, transfer: e.transferSize, ms: e.duration } : null; })()""")
        full = b.eval("""(async () => {
          const t0 = performance.now(); const r = await fetch('/api/tasks'); const text = await r.text();
          return { bytes: text.length, ms: performance.now() - t0 }; })()""")
        lean = b.eval("""(async () => {
          const t0 = performance.now(); const r = await fetch('/api/state'); const text = await r.text();
          return { bytes: text.length, ms: performance.now() - t0 }; })()""")
        # the state as it was before: the same payload with the board as
        # `/api/tasks` serves it, every card's text included
        before = dict(payload, board=json.loads(tasks))
        before_bytes = len(json.dumps(before).encode())
        after_bytes = len(json.dumps(payload).encode())
        self.measured["a_stateBytesBefore"] = before_bytes
        self.measured["a_stateBytesAfter"] = after_bytes
        self.measured["cards"] = {"small": SMALL, "heavy": len(HEAVY), "listed": len(cards)}
        self.measured["stateBytes"] = len(state)
        self.measured["tasksBytesFullText"] = len(tasks)
        self.measured["stateCardBytes"] = len(json.dumps(payload["board"]).encode())
        self.measured["tasksCardBytes"] = len(json.dumps(json.loads(tasks)["stages"]).encode())
        self.assertLess(after_bytes * 2, before_bytes,
                        f"the list is not under half the board it was: {after_bytes} of {before_bytes}")
        self.measured["firstCardMsUpperBound"] = first
        self.measured["stateResource"] = timing
        self.measured["inPageFetch"] = {"state": lean, "tasksFullText": full}
        shot("lazy-board-1280x900")

    def test_2_a_heavy_card_opens_with_brief_reviews_and_activity(self):
        b = self.load()
        b.eval(GATE)
        b.eval("(() => { window.__t0 = performance.now(); return true; })()")
        self.open(HEAVY[0])
        line = BRIEF_LINE.format(name=HEAVY[0])
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(line)})", 10),
                        "the brief never appeared")
        self.measured["detailOpenMs"] = b.eval("performance.now() - window.__t0")
        self.assertEqual(1, b.eval("__cards.asked.length"), "one read of the text")
        self.assertNotIn(HISTORY_SENTINEL, b.eval(BRIEF), "history is not the brief")
        b.click("#dtab-reviews")
        self.assertTrue(b.wait("document.querySelectorAll('#dsec-reviews details.rpt').length > 0", 10),
                        "the review rounds were not drawn")
        b.click("#dtab-activity")
        self.assertTrue(b.wait("document.querySelector('#dsec-activity').innerText.includes('Wren')", 10),
                        "the activity was not drawn")
        self.assertTrue(b.eval("!!document.querySelector('#dsec-reviews .rsrc')"),
                        "a report has no Original Markdown")
        shot("lazy-heavy-detail-activity-1280x900")

    def test_3_rapid_switching_never_shows_another_cards_text(self):
        b = self.load()
        b.eval(GATE)
        b.eval(f"(() => {{ window.__holdFor = {json.dumps(HEAVY[1])}; return true; }})()")
        self.open(HEAVY[1])
        self.assertTrue(b.wait("__held.length === 1", 5), "the first card's read did not go out")
        self.assertTrue(b.eval("!!document.querySelector('#dsec-brief .dload[aria-busy=\"true\"][role=\"status\"]')"),
                        "loading is not announced")
        shot("lazy-loading-1280x900")
        self.open(HEAVY[2])
        line2 = BRIEF_LINE.format(name=HEAVY[2])
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(line2)})", 10),
                        "the second card's brief never appeared")
        # the first card's reply lands last
        b.eval("(() => { __held.shift()(); return true; })()")
        time.sleep(0.5)
        text = b.eval(BRIEF)
        self.assertIn(line2, text)
        self.assertNotIn(BRIEF_LINE.format(name=HEAVY[1]), text,
                         "a late reply about another card replaced the open one")
        self.assertEqual(HEAVY[2], b.eval("S.selected.file"))

    def test_4_a_failed_read_says_so_and_can_be_asked_again(self):
        b = self.load()
        b.eval(GATE)
        b.eval("(() => { window.__failCards = true; return true; })()")
        self.open(HEAVY[0])
        self.assertTrue(b.wait("!!document.querySelector('#dsec-brief .dload.bad[role=\"alert\"] #dretry')", 10),
                        "the failure was not said, with a way to ask again")
        shot("lazy-read-failed-1280x900")
        b.eval("(() => { window.__failCards = false; return true; })()")
        b.click("#dretry")
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(BRIEF_LINE.format(name=HEAVY[0]))})", 10),
                        "asking again did not show the brief")

    def test_5_an_edit_is_read_again_and_an_unsent_comment_survives(self):
        b = self.load()
        b.eval(GATE)
        self.open(HEAVY[0])
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(BRIEF_LINE.format(name=HEAVY[0]))})", 10))
        asked = b.eval("__cards.asked.length")
        # reloads at the same revision — what a reconnect makes — ask for nothing
        for _ in range(3):
            b.eval("loadState().then(() => true)")
        time.sleep(0.3)
        self.assertEqual(asked, b.eval("__cards.asked.length"),
                         "a state reload at the same revision read the text again")
        self.assertTrue(b.wait("!!document.querySelector('#commentbox')", 5))
        b.eval("(() => { const c = document.querySelector('#commentbox'); c.value = 'not sent yet';"
               " c.dispatchEvent(new Event('input')); return true; })()")
        path = self.tasks / "review" / HEAVY[0]
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## Acceptance", "Edited on disk while open.\n\n## Acceptance", 1),
                        encoding="utf-8")
        b.eval("loadState().then(() => true)")
        self.assertTrue(b.wait(f"({BRIEF}).includes('Edited on disk while open.')", 10),
                        "the new revision was not read")
        self.assertEqual("not sent yet", b.eval("document.querySelector('#commentbox').value"),
                         "the unsent comment was lost")
        b.eval("(() => { S.commentDraft = {}; return true; })()")
        path.write_text(text, encoding="utf-8")

    def test_6_a_moved_card_is_followed_and_a_deleted_one_closes(self):
        b = self.load()
        self.open(HEAVY[2])
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(BRIEF_LINE.format(name=HEAVY[2]))})", 10))
        src = self.tasks / "review" / HEAVY[2]
        dst = self.tasks / "done" / HEAVY[2]
        text = src.read_text(encoding="utf-8")
        dst.write_text(text.replace("**Status:** Review", "**Status:** Done"), encoding="utf-8")
        src.unlink()
        b.eval("loadState().then(() => true)")
        self.assertTrue(b.wait("S.selected && S.selected.stage === 'done'", 10),
                        "the view did not follow the card")
        self.assertTrue(b.wait(f"({BRIEF}).includes({json.dumps(BRIEF_LINE.format(name=HEAVY[2]))})", 10),
                        "the moved card's brief was not read from its new stage")
        dst.unlink()
        b.eval("loadState().then(() => true)")
        self.assertTrue(b.wait("!S.selected && !document.querySelector('#drawer.open')", 10),
                        "the view stayed open on a deleted card")
        src.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
