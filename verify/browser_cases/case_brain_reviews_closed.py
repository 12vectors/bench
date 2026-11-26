"""A closed ticket's PR reviews read Historical, in the real browser (task 201).

A disposable backend of this case's own holds one card in review/ with a
PR review round and a relevance review. The case moves it through the
board's own routes and reads the Brain after each step, by pointer, in
Night and Daylight, with screenshots:

- **closure**: moved to done/, its PR review reads Historical in the
  library, with a *closed ticket* chip, and on its page the panel says it
  is a lifecycle default — no person's decision, no agent's assessment;
  the relevance review stays Unclassified;
- **reopen**: moved back to in-progress/, it reads Unclassified again —
  never Current;
- **archive**: closed again and archived, Historical again;
- **human override**: Classify Current on the closed card wins, and the
  card's bytes are unchanged;
- **passive reads**: browsing between the moves writes nothing.
"""

import hashlib
import json
import shutil
import unittest

import benchverify as bv

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def rid(prefix: str, heading: str) -> str:
    return prefix + hashlib.sha256(heading.strip().encode("utf-8")).hexdigest()[:10]


R1 = "## PR review at 0123456789ab — 2026-11-20 10:00 (Juno)"
REL = "## Relevance review — 2026-11-21 12:00 (Hazel)"
NAME = "21-close-the-loop.md"
CARD = ("# 21 — Close the loop\n\n**Status:** Review\n**Priority:** High\n\nClose it.\n"
        f"\n\n---\n\n{R1}\n\nPR REVIEW: APPROVE\n\nThe round approves the change.\n"
        f"\n\n---\n\n{REL}\n\nRELEVANCE REVIEW: Still relevant\n\nStill needed.\n")


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class ClosedTicketReviews(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-reviews-closed-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        cls.tasks = cls.backend.work / "tasks"
        (cls.tasks / "review" / NAME).write_text(CARD, encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.rv, cls.rel = rid("rv-", R1), rid("rl-", REL)

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

    def open(self, path, dark=True, ready="#bshell .b-title"):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 860)
        b.goto(path)
        if not b.wait(f"!!document.querySelector({json.dumps(ready)})", 15):
            raise bv.CheckFailed(f"{path}: nothing drew: "
                                 + str(b.eval("document.body.innerText")[:600]))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def move(self, src, dst):
        code, body = self.backend.post("/api/move", {"file": NAME, "from": src, "to": dst})
        self.assertEqual(200, code, body[:300])

    def reading(self, label, want, chip):
        """The library row and the review page both read `want`, in both
        themes, and browsing writes nothing."""
        before = tree(self.backend.work)
        for dark in (True, False):
            b = self.open("/brain", dark)
            row = f'.b-list a[data-brain-id="21/{self.rv}"]'
            if not b.wait(f"!!document.querySelector({json.dumps(row)}) && "
                          f"document.querySelector({json.dumps(row)}).closest('.b-row').innerText"
                          f".includes({json.dumps(want)})", 15):
                raise bv.CheckFailed(f"{label}: the row never read {want}: "
                                     + self.text(b, '[data-bkindsec="Review"]')[:600])
            line = b.eval(f"document.querySelector({json.dumps(row)}).closest('.b-row').innerText")
            self.assertIn(chip, line, f"{label}: {line}")
            rel = b.eval(f"document.querySelector('.b-list a[data-brain-id=\"21/{self.rel}\"]')"
                         ".closest('.b-row').innerText")
            self.assertIn("Unclassified", rel, "the relevance review was reclassified")
            shot(f"brain-reviews-closed-{label}-list-{'night' if dark else 'day'}")
            b = self.open(f"/brain/r/21/{self.rv}", dark)
            self.assertIn(want, self.text(b, ".b-kicker"))
            shot(f"brain-reviews-closed-{label}-page-{'night' if dark else 'day'}")
        self.assertEqual(tree(self.backend.work), before, f"{label}: browsing wrote something")
        return b

    def test_1_close_reopen_archive_and_override(self):
        self.move("review", "done")
        b = self.reading("done", "Historical", "closed ticket")
        page = self.text(b)
        self.assertIn("lifecycle default", page)
        self.assertIn("from the closed ticket", page)
        self.assertEqual(b.eval("!!document.querySelector('[data-closed-default]')"), True)

        self.move("done", "in-progress")
        b = self.reading("reopened", "Unclassified", "not yet classified")
        self.assertNotIn("Current", self.text(b, ".b-kicker"))

        self.move("in-progress", "done")
        code, body = self.backend.post("/api/archive", {"file": NAME, "from": "done"})
        self.assertEqual(200, code, body[:300])
        self.reading("archived", "Historical", "closed ticket")

        card = self.tasks / "archive" / NAME
        kept = card.read_bytes()
        b = self.open(f"/brain/r/21/{self.rv}")
        b.click('#bshell [data-bact="rv-classify"]')
        b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
        b.eval("(() => { const s = document.querySelector('[data-rvf=\"classification\"]'); s.value = 'Current';"
               " s.dispatchEvent(new Event('change', {bubbles: true}));"
               " const r = document.querySelector('[data-rvf=\"reason\"]'); r.value = 'still explains the loop';"
               " r.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
        b.click('#bshell [data-bact="rv-save"]')
        if not b.wait("!document.querySelector('[data-rvf]') && "
                      "(document.querySelector('.b-side')?.innerText || '').includes('still explains the loop')", 10):
            raise bv.CheckFailed("the person's classification was not shown: " + self.text(b)[:800])
        self.assertIn("Current", self.text(b, ".b-kicker"))
        self.assertEqual(b.eval("!!document.querySelector('[data-closed-default]')"), False)
        shot("brain-reviews-closed-override")
        self.assertEqual(card.read_bytes(), kept, "classifying changed the card")
        self.assertTrue((self.backend.work / "brain" / "reviews" / f"c21-{self.rv}.md").is_file())
