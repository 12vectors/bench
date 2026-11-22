"""Recovering a report that was not filed, and rebuilding the review
index, in the real browser (task 169).

A disposable backend of this case's own starts with three filing
operations an earlier board left pending — as a board that stopped part
way through filing would — and no review index:

- a relevance review for card 7, whose card is still here;
- a relevance review for card 8, whose card is still here;
- a work report for card 30, whose card has since been deleted.

The board reconciles them at start (nothing is appended). The case then:

- reads the Brain's **Report filing and the review index** fold, Night
  and Daylight at 1280px and 390px: the three reports and their states,
  the reviews already on cards listed *and* said not indexed, the missing
  card said as missing — nothing overflowing;
- **Retry filing** from the Brain by pointer (armed, then confirmed): the
  report lands on card 7 once, under the heading allocated before, and a
  further retry reports nothing written again;
- **Retry filing** from card 8's Activity by keyboard, and the card's
  banner leaves;
- the deleted card's report is **not** recreated anywhere;
- **Rebuild index**: every review on the cards reads indexed, and no card
  or classification record changes.
"""

import hashlib
import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION

REL7 = "## Relevance review — 2026-11-22 09:00 (Hazel)"
REL8 = "## Relevance review — 2026-11-22 09:05 (Wren)"
WORK30 = "## Work report — 2026-11-22 09:10 (Juno)"
OLD7 = "## Relevance review — 2026-11-20 10:00 (Fern)"
BODY7 = "RELEVANCE REVIEW: Still relevant\n\nThe routing card still holds."
BODY8 = "RELEVANCE REVIEW: Partly done\n\nHalf of this card has landed."
BODY30 = "WORK REPORT: did it\n\nThe card this was for has gone."


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rid(heading: str) -> str:
    return "rl-" + sha(heading.strip())[:10]


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def op(board, card, number, kind, heading, body, review=None, name="Hazel"):
    block = f"{heading}\n\n{body}"
    op_id = "f-" + sha(f"{board}\0{card}\0{heading}")[:20]
    return op_id, {
        "v": 1, "id": op_id, "kind": kind, "board": str(board), "card": card, "number": number,
        "run": f"review-{number}-0900", "runName": name, "model": "opus-fixture",
        "heading": heading, "review": review, "reviewType": "relevance" if review else None,
        "block": block, "blockSha": sha(block), "reportSha": sha(body)[:12],
        "bytes": len(body.encode("utf-8")), "clipped": False,
        "created": "2026-11-22T09:00:00Z", "status": "pending", "attempts": [],
        "commit": None, "indexed": review is None}


class ReportFiling(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "report-filing-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        tasks = cls.backend.work / "tasks"
        cls.card7 = tasks / "review" / "07-route-reports.md"
        cls.card7.write_text("# 07 — Route reports\n\n**Status:** Review\n**Priority:** High\n\n"
                             "Route each report.\n\n---\n\n" + OLD7 + "\n\n"
                             "RELEVANCE REVIEW: Still relevant\n\nAn earlier review.\n",
                             encoding="utf-8")
        cls.card8 = tasks / "in-progress" / "08-half-done.md"
        cls.card8.write_text("# 08 — Half done\n\n**Status:** In Progress\n**Priority:** Low\n\n"
                             "Half of it.\n", encoding="utf-8")
        state = cls.backend.tool / "manager" / "local" / "state" / "reports" / "filing"
        state.mkdir(parents=True)
        cls.ops = {}
        for key, args in (("7", ("07-route-reports.md", "7", "Relevance review", REL7, BODY7,
                                 rid(REL7))),
                          ("8", ("08-half-done.md", "8", "Relevance review", REL8, BODY8,
                                 rid(REL8), "Wren")),
                          ("30", ("30-gone.md", "30", "Work report", WORK30, BODY30, None,
                                  "Juno"))):
            op_id, record = op(tasks, *args)
            (state / f"{op_id}.json").write_text(json.dumps(record), encoding="utf-8")
            cls.ops[key] = op_id
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.index = cls.backend.tool / "manager" / "local" / "state" / "brain" / "review-index.json"

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def open(self, path, width=1280, height=860, dark=True, ready="#bshell .b-title"):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait(f"!!document.querySelector({json.dumps(ready)})", 15):
            raise bv.CheckFailed(f"{path}: nothing drew: "
                                 + str(b.eval("document.body.innerText")[:600]))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def filing(self, b):
        if not b.wait("!!document.querySelector('.b-filing')", 10):
            raise bv.CheckFailed("no filing fold was drawn: " + self.text(b)[:800])
        return self.text(b, ".b-filing")

    def copies(self, path, heading):
        return sum(1 for line in path.read_text(encoding="utf-8").split("\n")
                   if line.rstrip() == heading)

    def test_1_the_states_are_said_apart(self):
        before = tree(self.backend.work)
        for width, height, label in ((1280, 860, "desktop"), (390, 844, "narrow")):
            for dark in (True, False):
                b = self.open("/brain", width, height, dark)
                fold = self.filing(b)
                self.assertTrue(b.eval("document.querySelector('.b-filing').open"),
                                "the fold opens while something needs a person")
                for word in ("Reports that need you (3)", "no longer on this board",
                             "Retry filing", "retained on this board only",
                             "This board has no review index yet", "not indexed",
                             "local to this board", "Rebuild index"):
                    self.assertIn(word, fold, f"{label}: {word}")
                # a review already on a card is listed, and said not indexed here
                self.assertIn("not indexed", self.text(b, '[data-bkindsec="Review"]'))
                self.no_overflow(b, f"filing {label}")
                shot(f"report-filing-{label}-{'night' if dark else 'day'}")
        self.assertEqual(tree(self.backend.work), before, "reading wrote something")
        self.assertFalse(self.index.exists(), "reading wrote an index")

    def test_2_retry_from_the_brain_files_once(self):
        b = self.open("/brain")
        self.filing(b)
        button = f'.b-filing [data-bact="rf-file"][data-barg="{self.ops["7"]}"]'
        b.click(button)
        if not b.wait(f"(document.querySelector({json.dumps(button)})?.textContent || '')"
                      ".startsWith('Confirm')", 5):
            raise bv.CheckFailed("Retry filing did not arm")
        shot("report-filing-armed")
        self.assertEqual(self.copies(self.card7, REL7), 0, "arming wrote something")
        b.click(button)
        deadline = time.time() + 10
        while time.time() < deadline and self.copies(self.card7, REL7) == 0:
            time.sleep(0.2)
        self.assertEqual(self.copies(self.card7, REL7), 1)
        self.assertIn(BODY7, self.card7.read_text(encoding="utf-8"))
        if not b.wait(f"!document.querySelector({json.dumps(button)})", 10):
            raise bv.CheckFailed("the filed report still reads as needing a person")
        self.assertIn("Reports that need you (2)", self.filing(b))
        shot("report-filing-after-retry")
        # asking again by hand writes nothing again
        again = b.eval(
            "(async () => { const r = await fetch('/api/reports/filing/retry', {method: 'POST',"
            " headers: {'Content-Type': 'application/json', 'X-Bench-Build': window.PAGE_BUILD || ''},"
            f" body: JSON.stringify({{operation: {json.dumps(self.ops['7'])}, action: 'file'}})}});"
            " return [r.status, (await r.json()).operation?.duplicate]; })()")
        if isinstance(again, list) and again and again[0] == 200:
            self.assertTrue(again[1])
        self.assertEqual(self.copies(self.card7, REL7), 1)

    def test_3_retry_from_the_card_by_keyboard(self):
        b = self.open("/cards/8?tab=activity", ready="#drawerbody .dfiling")
        banner = self.text(b, "#drawerbody .dfiling")
        self.assertIn("1 report needs you", banner)
        self.assertIn("Retry filing", banner)
        shot("report-filing-card-activity")
        sel = "#drawerbody .dfiling [data-rfop]"
        b.eval(f"document.querySelector({json.dumps(sel)}).focus(); true")
        b.key("Enter")
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Retry filing on the card did not arm")
        b.key("Enter")
        deadline = time.time() + 10
        while time.time() < deadline and self.copies(self.card8, REL8) == 0:
            time.sleep(0.2)
        self.assertEqual(self.copies(self.card8, REL8), 1)
        if not b.wait("!document.querySelector('#drawerbody .dfiling')", 10):
            raise bv.CheckFailed("the card's banner did not leave once its report was filed")
        b.viewport(390, 844)
        time.sleep(0.3)
        shot("report-filing-card-narrow")

    def test_4_a_deleted_card_is_never_recreated(self):
        b = self.open("/brain")
        button = f'.b-filing [data-bact="rf-file"][data-barg="{self.ops["30"]}"]'
        b.click(button)
        b.wait(f"(document.querySelector({json.dumps(button)})?.textContent || '').startsWith('Confirm')", 5)
        b.click(button)
        time.sleep(1.0)
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            self.assertFalse((self.backend.work / "tasks" / stage / "30-gone.md").exists())
        self.assertIn("no longer on this board", self.filing(b))

    def test_5_rebuild_indexes_every_review_and_changes_no_card(self):
        before = tree(self.backend.work)
        b = self.open("/brain")
        button = '.b-filing [data-bact="rf-rebuild"]'
        self.filing(b)
        b.click(button)
        b.wait(f"(document.querySelector({json.dumps(button)})?.textContent || '').startsWith('Confirm')", 5)
        b.click(button)
        if not b.wait("(document.querySelector('.b-filing')?.innerText || '')"
                      ".includes('Every review on the cards is indexed')", 10):
            raise bv.CheckFailed("the rebuild did not index every review: " + self.text(b, ".b-filing"))
        self.assertTrue(self.index.is_file())
        self.assertEqual(tree(self.backend.work), before, "rebuilding changed a card")
        self.assertNotIn("not indexed", self.text(b, '[data-bkindsec="Review"]'))
        shot("report-filing-rebuilt")
        b.eval("S.dark = false; applyTheme(); true")
        shot("report-filing-rebuilt-day")
