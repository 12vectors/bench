"""Acceptance maintained by the runs, outcome first (task 190), in the
browser.

Four disposable cards carry what a work run and the independent review
reported, in the board-written provenance line bench files under each
report: mixed outcomes (accepted, not met, not verified, needs your
decision, met — awaiting review, needs recheck), everything awaiting
review, everything not verified, and one decision left. The Acceptance
section must lead with the server's outcome and next step, list every
criterion with its whole wording, its state in words and its reason, and
open its evidence and history from the keyboard; the Overview must carry
the same conclusion. At 1280px and 390px, narrow and expanded, Daylight
and Night. Reading writes nothing and posts nothing.

The "before" for these screenshots is the reference the card supplied
(reference/190-acceptance-before.png): the old Acceptance tab with its
Track acceptance button, at the narrow width these shots match.
"""

import hashlib
import json
import unittest

import benchverify as bv

S = bv.SESSION
DRAWER = "#drawer.open"


def rev(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:12]


def prov(role: str, who: str, results: list, problems=(), missing=0, kind="pr") -> str:
    meta = {"v": 1, "kind": kind, "reviewer": who, "filed": "2026-11-20T10:00:00Z",
            "acceptance": {"v": 1, "role": role, "reported": True, "head": None,
                           "results": results, "problems": list(problems),
                           "missing": missing}}
    return f"<!-- bench:review {json.dumps(meta)} -->"


HEAD = "# {title}\n\n**Status:** Review\n**Priority:** Medium\n\n## Context\n\n" \
       "A disposable card for the acceptance results browser case.\n\n## Acceptance\n\n"

MIXED_ITEMS = [
    ("aa0001", "Saving a comment runs no agent and launches nothing"),
    ("aa0002", "The brief is unchanged until Apply is pressed"),
    ("aa0003", "The narrow layout keeps every criterion readable at 390px"),
    ("aa0004", "Hidden columns follow the product owner's export rule"),
    ("aa0005", "Counts on the face open their section"),
    ("aa0006", "Keyboard users reach every criterion and its evidence"),
]
OLD_SIX = "Keyboard users reach every criterion"


def checklist(items) -> str:
    return "".join(f"- [ ] {text} <!-- ac:{cid} -->\n" for cid, text in items)


def mixed() -> str:
    r = {cid: rev(text) for cid, text in MIXED_ITEMS}
    work = prov("implementer", "Juno", [
        {"rev": r[c], "criterion": c, "result": "met",
         "reason": "implemented and covered by tests/test_x.py"} for c, _ in MIXED_ITEMS[:5]],
        kind="work")
    review = prov("reviewer", "Olive", [
        {"rev": r["aa0001"], "criterion": "aa0001", "result": "accepted",
         "reason": "read the handler: saving writes the card and starts nothing"},
        {"rev": r["aa0002"], "criterion": "aa0002", "result": "not-met",
         "reason": "Discard rewrites the brief in refine.py line 412"},
        {"rev": r["aa0003"], "criterion": "aa0003", "result": "not-verified",
         "reason": "needs a 390px browser run nobody made at this head"},
        {"rev": r["aa0004"], "criterion": "aa0004", "result": "decision",
         "reason": "Should hidden columns be exported, or left out?"},
        {"rev": rev(OLD_SIX), "criterion": "aa0006", "result": "accepted",
         "reason": "Tab reaches every row"}],
        problems=["not understood: - maybe later"])
    return (HEAD.format(title="Acceptance results — mixed") + checklist(MIXED_ITEMS) +
            "\n---\n\n## Work report — 2026-11-20 09:00 (Juno)\n\n" + work +
            "\n\nDone — committed.\n\n---\n\n## PR review at 0123456789ab — 2026-11-20 10:00 (Olive)\n\n"
            + review + "\n\nPR REVIEW: REQUEST CHANGES\n\nDiscard is not read-only.\n")


def simple(title: str, role: str, word: str, reason: str, last=None) -> str:
    items = [("bb0001", "Exports open in a spreadsheet"),
             ("bb0002", "Exports keep the column order"),
             ("bb0003", "Exports name the file after the table")]
    results = [{"rev": rev(t), "criterion": c, "result": word, "reason": reason}
               for c, t in items]
    if last:
        results[-1].update(result=last[0], reason=last[1])
    heading = ("## Work report — 2026-11-20 09:00 (Juno)" if role == "implementer"
               else "## PR review at 0123456789ab — 2026-11-20 10:00 (Olive)")
    return (HEAD.format(title=title) + checklist(items) + "\n---\n\n" + heading + "\n\n"
            + prov(role, "Juno" if role == "implementer" else "Olive", results,
                   kind="work" if role == "implementer" else "pr")
            + "\n\nReport text.\n")


CARDS = {
    "9190-acceptance-mixed": (mixed(), "Acceptance: changes needed"),
    "9191-acceptance-awaiting": (simple("Acceptance results — awaiting", "implementer", "met",
                                        "implemented; tests cover it"),
                                 "Acceptance: awaiting review"),
    "9192-acceptance-unverified": (simple("Acceptance results — unverified", "reviewer",
                                          "not-verified", "no evidence at this head"),
                                   "Acceptance: incomplete"),
    "9193-acceptance-decision": (simple("Acceptance results — decision", "reviewer", "accepted",
                                        "read the export code",
                                        last=("decision", "Should the file name carry the date?")),
                                 "Acceptance: needs your decision"),
}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class AcceptanceResults(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.files = []
        for stem, (text, _) in CARDS.items():
            path = backend.work / "tasks" / "review" / f"{stem}.md"
            path.write_text(text, encoding="utf-8")
            cls.files.append(path)

    @classmethod
    def tearDownClass(cls):
        for path in cls.files:
            path.unlink(missing_ok=True)
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    def reading(self, stem: str) -> dict:
        code, body = S["backend"].get(f"/api/card/acceptance?file={stem}.md&stage=review")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open(self, stem: str, *, width=1280, height=900, dark=None, tab="acceptance", wide=False):
        b = S["browser"]
        b.viewport(width, height)
        b.goto(f"/cards/{stem.split('-')[0]}?tab={tab}")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        if not b.wait(f"!!document.querySelector('{DRAWER} #dsec-{tab}:not([hidden])')", 15):
            raise bv.CheckFailed(f"{stem}: the {tab} section never opened")
        if wide and not b.eval("!!S.dwide"):
            b.click("#dwide")
            b.wait("!!S.dwide", 5)
        if not b.wait("!!document.querySelector('#accSummary')"
                      if tab == "acceptance" else
                      f"document.querySelector('{DRAWER} #dsec-overview').innerText.includes('Acceptance:')", 15):
            raise bv.CheckFailed(f"{stem}: the acceptance outcome never drew")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        return b

    def no_sideways(self, where: str):
        b = S["browser"]
        over = b.eval("(() => { const d = document.querySelector('#drawerbody');"
                      " return d.scrollWidth - d.clientWidth; })()")
        self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {where}")

    def test_1_mixed_outcomes_lead_with_the_outcome_in_every_view(self):
        stem = "9190-acceptance-mixed"
        before = self.files[0].read_text(encoding="utf-8")
        server = self.reading(stem)
        self.assertEqual([i["state"] for i in server["items"]],
                         ["accepted", "needs-work", "not-verified", "decision",
                          "reported", "recheck"])
        seen = {}
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                for wide in ((False, True) if width == 1280 else (False,)):
                    b = self.open(stem, width=width, height=height, dark=dark, wide=wide)
                    summary = b.eval("document.querySelector('#accSummary').innerText")
                    self.assertIn("Acceptance: changes needed", summary)
                    for words in ("1 Not met", "1 Needs your decision", "1 Needs recheck",
                                  "1 Not verified", "1 Met — awaiting review", "1 Accepted"):
                        self.assertIn(words, summary)
                    self.assertIn("Next — the implementation agent", summary)
                    rows = b.eval("[...document.querySelectorAll('#dsec-acceptance details.arow')]"
                                  ".map(d => ({word: d.querySelector('.aword').innerText,"
                                  " text: d.querySelector('.afull').innerText,"
                                  " reason: (d.querySelector('.areason') || {}).innerText || ''}))")
                    self.assertEqual([r["word"].lower() for r in rows],
                                     ["accepted", "not met", "not verified",
                                      "needs your decision", "met — awaiting review",
                                      "needs recheck"])
                    self.assertEqual([r["text"] for r in rows], [t for _, t in MIXED_ITEMS],
                                     "every criterion is shown in full")
                    self.assertIn("refine.py line 412", rows[1]["reason"])
                    self.assertIn("hidden columns", rows[3]["reason"])
                    self.assertIn("reworded", rows[5]["reason"])
                    self.assertIn("not understood", b.eval(
                        "document.querySelector('#dsec-acceptance .accprob').innerText"))
                    self.no_sideways(f"{width} dark={dark} wide={wide}")
                    name = (f"acceptance-mixed-{width}-{'night' if dark else 'day'}"
                            f"{'-expanded' if wide else ''}")
                    shot(name)
                    seen[name] = summary.splitlines()[0]
                    self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                    if wide:
                        b.click("#dwide")
        self.assertEqual(before, self.files[0].read_text(encoding="utf-8"),
                         "reading the card wrote to it")
        S["metrics"]["acceptanceResults"] = seen

    def test_2_keyboard_opens_evidence_and_history(self):
        stem = "9190-acceptance-mixed"
        b = self.open(stem)
        first = "#dsec-acceptance details.arow"
        b.eval(f"document.querySelector('{first} > summary').focus()")
        self.assertTrue(b.eval(f"document.activeElement === document.querySelector('{first} > summary')"))
        b.key("Enter")
        self.assertTrue(b.wait(f"document.querySelector('{first}').open", 5),
                        "Enter did not open the criterion")
        detail = b.eval(f"document.querySelector('{first} .abody').innerText")
        self.assertIn("reported by the independent review Olive", detail)
        self.assertIn("History", detail)
        self.assertIn("Record your decision", detail)
        # the decision form is folded: a correction, not a routine step
        self.assertFalse(b.eval(f"document.querySelector('{first} .acorrect').open"))
        shot("acceptance-mixed-expanded-row-1280")
        b.key("Enter")
        self.assertTrue(b.wait(f"!document.querySelector('{first}').open", 5),
                        "Enter did not close the criterion")

    def test_3_the_overview_carries_the_same_conclusion(self):
        stem = "9190-acceptance-mixed"
        b = self.open(stem, tab="overview")
        text = b.eval(f"document.querySelector('{DRAWER} #dsec-overview .ovnow').innerText")
        self.assertIn("Acceptance: changes needed", text)
        self.assertIn("Next — the implementation agent", text)
        order = b.eval("(() => { const y = s => document.querySelector(s).getBoundingClientRect().top;"
                       " return [y('#dsec-overview .ovnow'), y('#dsec-overview .ovreq')]; })()")
        self.assertEqual(order, sorted(order), "Where it stands stays above What is required")
        self.assertNotIn("Track acceptance",
                         b.eval(f"document.querySelector('{DRAWER}').innerText"))
        shot("acceptance-overview-mixed-1280")
        b.click(f"{DRAWER} #dsec-overview .ovnow [data-goto=\"acceptance\"]")
        self.assertTrue(b.wait("!document.querySelector('#dsec-acceptance').hidden", 5),
                        "the Overview's acceptance link did not open the section")

    def test_4_awaiting_unverified_and_decision_states(self):
        for stem, (_, headline) in CARDS.items():
            if stem.endswith("mixed"):
                continue
            for width, height in ((1280, 900), (390, 844)):
                b = self.open(stem, width=width, height=height, dark=False)
                summary = b.eval("document.querySelector('#accSummary').innerText")
                self.assertIn(headline, summary, stem)
                self.assertEqual(self.reading(stem)["outcome"]["headline"], headline)
                self.no_sideways(f"{stem} at {width}")
                shot(f"{stem}-{width}")
        b = self.open("9193-acceptance-decision", dark=True)
        self.assertIn("Next — you", b.eval("document.querySelector('#accSummary').innerText"))
        self.assertIn("file name carry the date",
                      b.eval("document.querySelector('#accSummary').innerText"))
        shot("9193-acceptance-decision-1280-night")
        b = self.open("9191-acceptance-awaiting")
        self.assertEqual(0, b.eval("[...document.querySelectorAll('#dsec-acceptance .abox')]"
                                   ".filter(x => x.innerText === '✓').length"),
                         "an implementer's success wears no tick")


if __name__ == "__main__":
    unittest.main()
