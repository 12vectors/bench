"""Classifying without a reason, in the real browser (task 202).

A disposable backend of this case's own holds one maintained entry and a
card with one filed PR review. The case drives the page with the pointer
and the keyboard, as a person would, and reads the history back:

- **a new entry** is created Current with the Reason (optional) field
  left empty: it saves, and its history line says who, when and *no
  reason given* — never a made-up explanation;
- **a maintained entry** is classified with no reason by the keyboard
  (Tab to Save, Enter), then corrected with a short comment by the
  pointer: the comment is kept as typed, and both lines read back;
- **a review** is classified with no reason, then corrected with a short
  comment, through its own form; an override still asks for its reason;
- the copy says the field is optional, and nothing says it is required;
- **both themes and 390px**: nothing overflows, with screenshots.
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


R1 = "## PR review at 0123456789ab — 2026-11-20 10:00 (Juno)"
RID = "rv-" + hashlib.sha256(R1.strip().encode("utf-8")).hexdigest()[:10]
CARD = ("# 07 — Route review reports\n\n**Status:** Review\n**Priority:** High\n\n"
        "Route each report.\n\n---\n\n" + R1 + "\n\nPR REVIEW: APPROVE\n\nLooks right.\n")
ENTRY = ("# Old routing notes\n\n**Brain:** 1\n**Id:** e-old-notes\n**Kind:** Guide\n"
         "**Classification:** Unclassified\n**Summary:** Early notes.\n\nThe first notes.\n\n"
         "## Classification history\n\n"
         "- 2026-11-01 · Unclassified · ada — an older line, with its reason\n")


class ReasonlessClassification(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-reasonless-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        work = cls.backend.work
        (work / "tasks" / "review" / "07-route-review-reports.md").write_text(CARD, encoding="utf-8")
        (work / "brain").mkdir()
        (work / "brain" / "old-notes.md").write_text(ENTRY, encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin

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

    # ── helpers ──

    def open(self, path, width=1280, height=900, dark=True, ready="#bshell .b-title"):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait(f"!!document.querySelector({json.dumps(ready)})", 15):
            raise bv.CheckFailed(f"{path}: nothing drew: "
                                 + str(b.eval("document.body.innerText")[:600]))
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def set(self, b, sel, text, event="input"):
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.focus(); "
               f"f.value = {json.dumps(text)}; "
               f"f.dispatchEvent(new Event({json.dumps(event)}, {{bubbles: true}})); }})()")

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def wait_text(self, b, words, sel="#bshell", timeout=12):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b)[:1500]}")

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    def history(self, b):
        return b.eval("[...document.querySelectorAll('#bshell .b-history li')]"
                      ".map(li => li.innerText)")

    def tab_to(self, b, sel, limit=40):
        """Tab until the focused element matches `sel`, as a keyboard would."""
        for _ in range(limit):
            if b.eval(f"!!document.activeElement && document.activeElement.matches({json.dumps(sel)})"):
                return
            b.key("Tab")
        raise bv.CheckFailed(f"Tab never reached {sel}")

    # ── the journey ──

    def test_1_a_new_entry_is_created_classified_with_no_reason(self):
        b = self.open("/brain")
        b.click('#bshell [data-bact="ag-new"]')
        if not b.wait("!!document.querySelector('#bshell [data-bact=\"ag-manual\"]')", 5):
            raise bv.CheckFailed("New entry opened no composer")
        b.click('#bshell [data-bact="ag-manual"]')
        if not b.wait("!!document.querySelector('#bshell .b-form [data-bf=\"title\"]')", 5):
            raise bv.CheckFailed("New entry opened no form")
        self.set(b, '#bshell [data-bf="title"]', "Reasonless guide")
        b.click('#bshell input[data-bf="classification"][value="Current"]')
        form = self.text(b, "#bshell .b-form")
        self.assertIn("Reason (optional)", form)
        self.assertNotIn("Required unless", form)
        self.assertFalse(b.eval("document.querySelector('#bshell [data-bf=\"reason\"]').required"))
        b.click('#bshell [data-bact="save"]')
        if not b.wait("location.pathname.startsWith('/brain/e/e-reasonless-guide-')", 10):
            raise bv.CheckFailed("Save without a reason did not land: " + self.text(b)[:800])
        self.wait_text(b, ["Saved", "no reason given"])
        lines = self.history(b)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("Current", lines[0])
        self.assertIn("no reason given", lines[0])
        self.assertNotIn("not yet assessed", lines[0])
        path = self.backend.work / "brain" / (b.eval("location.pathname.split('/').pop()") + ".md")
        line = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")][0]
        self.assertNotIn("—", line, "an absent reason was written as something")
        shot("brain-reasonless-created-night")

    def test_2_an_entry_is_classified_by_keyboard_then_corrected_with_a_comment(self):
        b = self.open("/brain/e/e-old-notes")
        b.click('#bshell [data-bact="classify"]')
        if not b.wait("!!document.querySelector('#bshell [data-bf=\"reason\"]')", 5):
            raise bv.CheckFailed("Classify opened no form")
        b.click('#bshell input[data-bf="classification"][value="Current"]')
        self.assertIn("Reason (optional)", self.text(b, "#bshell .b-form"))
        # whitespace only is no reason; Save by the keyboard
        self.set(b, '#bshell [data-bf="reason"]', "   ")
        self.tab_to(b, '#bshell [data-bact="save"]')
        b.key("Enter")
        self.wait_text(b, ["classified Current"])
        if not b.wait("!document.querySelector('#bshell .b-form')", 10):
            raise bv.CheckFailed("the form stayed open: " + self.text(b)[:800])
        # a correction, by the pointer, with a short comment
        b.click('#bshell [data-bact="classify"]')
        b.wait("!!document.querySelector('#bshell [data-bf=\"reason\"]')", 5)
        b.click('#bshell input[data-bf="classification"][value="Historical"]')
        self.set(b, '#bshell [data-bf="reason"]', "ok")
        shot("brain-reasonless-correct-form-night")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["classified Historical"])
        b.wait("!document.querySelector('#bshell .b-form')", 10)
        lines = self.history(b)   # newest first
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("Historical", lines[0])
        self.assertIn("— ok", lines[0])
        self.assertIn("Current", lines[1])
        self.assertIn("no reason given", lines[1])
        self.assertIn("an older line, with its reason", lines[2], "an older line no longer reads")
        shot("brain-reasonless-history-night")
        b.eval("S.dark = false; applyTheme(); true")
        shot("brain-reasonless-history-day")

    def test_3_a_review_is_classified_without_a_reason_then_corrected(self):
        b = self.open(f"/brain/r/7/{RID}")
        b.click('#bshell [data-bact="rv-classify"]')
        if not b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5):
            raise bv.CheckFailed("Classify opened no review form")
        self.assertIn("Reason (optional)", self.text(b, "#bshell .b-form"))
        self.assertFalse(b.eval("document.querySelector('[data-rvf=\"reason\"]').required"))
        self.set(b, '[data-rvf="classification"]', "Current", "change")
        b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
        self.tab_to(b, '#bshell [data-bact="rv-save"]')
        b.key("Enter")
        if not b.wait("!document.querySelector('[data-rvf]')", 10):
            raise bv.CheckFailed("the reasonless review classification was not saved: "
                                 + self.text(b)[:800])
        self.wait_text(b, ["no reason given"], ".b-side")
        b.click('#bshell [data-bact="rv-classify"]')
        b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
        self.set(b, '[data-rvf="classification"]', "Historical", "change")
        b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
        self.set(b, '[data-rvf="reason"]', "old")
        b.click('#bshell [data-bact="rv-save"]')
        if not b.wait("!document.querySelector('[data-rvf]')", 10):
            raise bv.CheckFailed("the corrected review classification was not saved: "
                                 + self.text(b)[:800])
        self.wait_text(b, ["— old"], ".b-side")
        lines = self.history(b)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("Historical", lines[0])
        self.assertIn("Current", lines[1])
        self.assertIn("no reason given", lines[1])
        shot("brain-reasonless-review-night")
        # an override's reason is still asked for
        if b.eval("!!document.querySelector('#bshell [data-bact=\"rv-override\"]')"):
            b.click('#bshell [data-bact="rv-override"]')
            b.wait("!!document.querySelector('[data-rvf=\"reason\"]')", 5)
            self.assertTrue(b.eval("document.querySelector('[data-rvf=\"reason\"]').required"))
            self.assertNotIn("optional", self.text(b, "#bshell .b-form"))
            b.click('#bshell [data-bact="rv-cancel"]')

    def test_4_narrow_and_both_themes(self):
        for dark in (True, False):
            label = "night" if dark else "day"
            b = self.open("/brain/e/e-old-notes", 390, 844, dark)
            b.click('#bshell [data-bact="classify"]')
            b.wait("!!document.querySelector('#bshell [data-bf=\"reason\"]')", 5)
            self.no_overflow(b, f"classify form narrow {label}")
            shot(f"brain-reasonless-form-narrow-{label}")
            b.click('#bshell [data-bact="cancel"]')
            b.wait("!document.querySelector('#bshell .b-form')", 5)
            self.no_overflow(b, f"entry narrow {label}")
            b = self.open(f"/brain/r/7/{RID}", 390, 844, dark)
            self.no_overflow(b, f"review narrow {label}")
            shot(f"brain-reasonless-review-narrow-{label}")


if __name__ == "__main__":
    unittest.main()
