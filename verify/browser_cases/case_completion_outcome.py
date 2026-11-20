"""An unfinished completion, as the card and the merge sheet show it
(task 166), in the browser.

A review card carries the record an older board's duplicate local merge
left: the card's head delivered on the remote through another merge, the
merge made here unpublished. The card wears `Not published` and says
"Delivered elsewhere" with the next step; the merge sheet leads with the
same outcome, names the step (keep the local merge under a ref of your
own, bring the base back to the remote, retry) and folds the identities
— head, merge, the remote's tip, the operation and an actor said to be
unverified — away until asked for. Read in both themes, at 1440 and 390.

Everything here is a disposable card and record written into the
fixture and removed afterwards. Nothing is merged, pushed or completed:
the sheet is opened and closed, and its retry button is never pressed.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "09-completion-outcome-fixture"
FILE = f"{STEM}.md"
CARD = ("# A card whose work the host already merged\n\n**Status:** Review\n"
        "**Priority:** Medium\n**PR:** https://github.com/acme/widget/pull/9\n\n"
        "A disposable review card.\n")
HEAD, MERGE, REMOTE = "a1" * 20, "b2" * 20, "c3" * 20
RECORD = {
    "branch": f"task/{STEM}", "target": "main", "mode": "local",
    "remote": "origin", "commit": HEAD, "merge": MERGE, "remoteTip": REMOTE,
    "publication": "failed", "outcome": "diverged", "deliveredElsewhere": True,
    "diverged": True,
    "detail": "! [rejected] main -> main (fetch first) <b>not markup</b>",
    "action": (f"origin/main already holds {HEAD[:12]} through another merge "
               f"({REMOTE[:12]}), so this card's work is delivered; the merge "
               f"this checkout made, {MERGE[:12]}, is not on origin and must not "
               f"be pushed over it. Keep it under a reference of your own (git "
               f"branch recovery/{STEM} {MERGE[:12]}), bring main back to "
               f"origin/main yourself, then merge & clean up again: it "
               f"reconciles without merging. Bench does not reset or "
               f"force-push main."),
    "operation": {"id": "cmp-0a1b2c3d4e5f", "source": "api", "actor": None,
                  "actorSource": None, "head": HEAD, "target": "main",
                  "mode": "local", "at": 1795200000.0},
    "history": [{"id": "cmp-0a1b2c3d4e5f", "source": "api", "actor": None,
                 "at": 1795200000.0, "outcome": "diverged",
                 "publication": "failed"}],
    "task": FILE, "at": 1795200000.0,
}
SHEET = "#sheetwrap .sheet"
PANEL = f"{SHEET} .panel[data-outcome]"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class AnUnfinishedCompletion(unittest.TestCase):

    def setUp(self):
        card = S["backend"].work / "tasks" / "review" / FILE
        record = (S["backend"].tool / "manager" / "local" / "state" /
                  "completions" / f"{STEM}.json")
        record.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(CARD, encoding="utf-8")
        record.write_text(json.dumps(RECORD), encoding="utf-8")
        self.addCleanup(card.unlink, True)
        self.addCleanup(record.unlink, True)
        self.addCleanup(lambda: S["browser"].eval(
            "closeSheet(); localStorage.removeItem('bench-theme')"))

    def load(self, width=1440, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if not b.wait(f"!!findTask({json.dumps(FILE)})", 15):
            raise bv.CheckFailed("the disposable review card never reached the board")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        return b

    def open_sheet(self, b):
        b.eval(f"move({json.dumps(FILE)}, 'review', 'done')")
        if not b.wait(f"!!document.querySelector({json.dumps(PANEL)})", 10):
            raise bv.CheckFailed("the merge sheet did not show the unfinished completion")

    def test_the_card_says_delivered_elsewhere_and_the_next_step(self):
        code, body = S["backend"].get(f"/api/card/delivery?file={FILE}")
        self.assertEqual(200, code)
        status = json.loads(body)["status"]
        self.assertEqual("not-published", status["word"])
        self.assertIn("Delivered elsewhere", status["sentence"])
        self.assertIn(f"recovery/{STEM}", status["sentence"])
        b = self.load()
        face = b.wait("(() => { const c = document.querySelector("
                      f"'#board article.card[data-file=\"{FILE}\"]');"
                      " return !!c && /Not published/.test(c.innerText); })()", 10)
        self.assertTrue(face, "the card's face does not wear Not published")

    def test_the_sheet_reads_in_both_themes_at_1440_and_390(self):
        seen = {}
        for dark in (True, False):
            theme = "night" if dark else "day"
            for w, h in [(1440, 900), (390, 780)]:
                b = self.load(w, h, dark)
                self.open_sheet(b)
                read = b.eval(
                    "(() => { const p = document.querySelector("
                    f"{json.dumps(PANEL)}); const s = p.closest('.sheet');"
                    " const d = p.querySelector('details.cmp-evidence');"
                    " return {head: p.querySelector('.phead').innerText,"
                    " text: p.innerText, open: d && d.open,"
                    " ship: (document.querySelector('#sh-ship') || {}).innerText || '',"
                    " overflow: s.scrollWidth - s.clientWidth,"
                    " doc: document.documentElement.scrollWidth - innerWidth,"
                    " bold: !!p.querySelector('b')}; })()")
                where = f"{w}px {theme}"
                self.assertIn("Delivered elsewhere", read["head"], where)
                self.assertIn(f"recovery/{STEM}", read["text"], where)
                self.assertIn("does not merge twice", read["text"], where)
                self.assertIn("Retry publication", read["ship"], where)
                self.assertFalse(read["open"], f"identities unfolded by default at {where}")
                self.assertFalse(read["bold"], f"the remote's words became markup at {where}")
                self.assertLessEqual(read["overflow"], 1, f"the sheet scrolls sideways at {where}")
                self.assertLessEqual(read["doc"], 1, f"the page scrolls sideways at {where}")
                shot(f"completion-outcome-{w}-{theme}")
                b.click(f"{PANEL} details.cmp-evidence summary")
                ids = b.eval(
                    "(() => { const d = document.querySelector("
                    f"{json.dumps(PANEL + ' details.cmp-evidence')});"
                    " const s = d.closest('.sheet');"
                    " return {open: d.open, text: d.innerText,"
                    " overflow: s.scrollWidth - s.clientWidth}; })()")
                self.assertTrue(ids["open"], where)
                for value in (HEAD[:12], MERGE[:12], REMOTE[:12], "cmp-0a1b2c3d4e5f",
                              "actor not verified", "<b>not markup</b>"):
                    self.assertIn(value, ids["text"], f"{value} missing at {where}")
                self.assertLessEqual(ids["overflow"], 1,
                                     f"the identities scroll sideways at {where}")
                shot(f"completion-outcome-evidence-{w}-{theme}")
                seen[where] = True
                b.eval("closeSheet()")
        S["metrics"]["completionOutcome"] = seen


if __name__ == "__main__":
    unittest.main()
