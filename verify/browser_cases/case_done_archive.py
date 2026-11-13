"""Archive on the face of a done card (task 85), in the browser.

A done card carries its `⌸ archive` chip on the face — visible without
hovering, opening its detail view or its ⋯ tools — and every other stage
keeps it in the tools. The chip is the same one the tools carried: arm on
the first activation, fire on the second, through the one `/api/archive`
route, with the same refusals.

Every card archived or refused here is a disposable one written into the
fixture's work state and removed afterwards; the five fixture cards the
other cases read are never moved. The "before" screenshots are the same
page with the face chip row hidden by an injected style — what a done
card's face was before this change — taken at the same viewport and theme
as their "after".
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "08-done-archive-fixture"
FILE = f"{STEM}.md"
TITLE = ("A finished card whose title runs long enough to wrap across several "
         "lines of a narrow column beside its archive control")
CARD = f"# {TITLE}\n\n**Status:** Done\n**Priority:** Low\n\nA disposable done card.\n"
SEL = f'#board article.card[data-file="{FILE}"]'
CHIP = f"{SEL} > .chiprow [data-arch]"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class DoneCardArchive(unittest.TestCase):

    def setUp(self):
        work = S["backend"].work / "tasks"
        self.done = work / "done" / FILE
        self.archived = work / "archive" / FILE
        self.done.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.archived.unlink, True)
        self.addCleanup(self.done.unlink, True)
        self.addCleanup(lambda: S["browser"].eval("localStorage.removeItem('bench-theme')"))

    def load(self, width=1440, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if not b.wait(f"!!document.querySelector('{SEL}')", 15):
            raise bv.CheckFailed("the disposable done card never reached the board")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("document.activeElement && document.activeElement.blur()")
        return b

    def geometry(self) -> dict:
        return S["browser"].eval(
            "(() => { const box = e => { const r = e.getBoundingClientRect();"
            " return [r.left, r.top, r.right, r.bottom, r.width, r.height]; };"
            f" const c = document.querySelector('{SEL}'), a = document.querySelector('{CHIP}');"
            " return {card: box(c), chip: a && box(a), title: box(c.querySelector('.title')),"
            " top: box(c.querySelector('.toprow')), scroll: c.scrollWidth, client: c.clientWidth,"
            " doc: document.documentElement.scrollWidth, vw: innerWidth}; })()")

    def assertFits(self, where: str):
        g = self.geometry()
        self.assertIsNotNone(g["chip"], f"no archive chip on the face at {where}")
        card, chip, title = g["card"], g["chip"], g["title"]
        self.assertGreater(chip[4], 0, f"the chip has no width at {where}")
        self.assertGreaterEqual(chip[5], 24, f"the chip is under a 24px target at {where}")
        self.assertGreaterEqual(chip[0], card[0] - 0.5, f"the chip leaves the card at {where}")
        self.assertLessEqual(chip[2], card[2] + 0.5, f"the chip is clipped at {where}")
        self.assertGreaterEqual(chip[1], title[3] - 0.5, f"the chip overlaps the title at {where}")
        self.assertLessEqual(g["scroll"], g["client"] + 1, f"the card scrolls sideways at {where}")
        self.assertLessEqual(g["doc"], g["vw"] + 1, f"the page scrolls sideways at {where}")

    # — visible by stage, and only on done —

    def test_only_done_cards_wear_it_on_their_face(self):
        b = self.load()
        faces = b.eval("[...document.querySelectorAll('#board article.card')].map(c => ({"
                       " file: c.dataset.file, stage: c.closest('.drop').dataset.stage,"
                       " face: [...c.querySelectorAll(':scope > .chiprow [data-arch]')]"
                       "   .filter(e => e.getBoundingClientRect().width > 0).length,"
                       " tools: c.querySelectorAll('.cardtools [data-arch]:not(.onface)').length,"
                       " more: !!c.querySelector('[data-more]') }))")
        S["metrics"]["doneArchiveFaces"] = faces
        done = [f for f in faces if f["stage"] == "done"]
        self.assertGreaterEqual(len(done), 2, faces)
        for f in faces:
            if f["stage"] == "done":
                self.assertEqual(1, f["face"], f"{f['file']}: no visible archive on its face")
                self.assertEqual(0, f["tools"], f"{f['file']}: archive offered twice")
            else:
                self.assertEqual(0, f["face"], f"{f['file']}: archive on a {f['stage']} face")
        for f in faces:
            if f["stage"] in ("backlog", "to-do"):
                self.assertEqual(1, f["tools"], f"{f['file']}: lost its archive tool")
                self.assertTrue(f["more"], f"{f['file']}: lost its ⋯ tools")
            if f["stage"] in ("in-progress", "review"):
                self.assertEqual(0, f["tools"], f"{f['file']}: a working card is archivable")
        # visible without hover: nothing is under the pointer or focused
        self.assertFalse(b.eval(f"document.querySelector('{SEL}').matches(':hover, :focus-within')"))
        name = b.eval(f"document.querySelector('{CHIP}').innerText.trim()")
        self.assertIn("archive", name)

    def test_it_fits_in_both_themes_at_1440_and_390(self):
        sizes = {}
        for dark in (True, False):
            theme = "night" if dark else "day"
            for w, h in [(1440, 900), (390, 780)]:
                b = self.load(w, h, dark)
                b.eval(f"document.querySelector('{SEL}').scrollIntoView({{block: 'center'}})")
                self.assertFits(f"{w}px {theme}")
                sizes[f"{w} {theme}"] = self.geometry()["chip"]
                shot(f"done-archive-after-{w}-{theme}")
                b.eval("(() => { const s = document.createElement('style'); s.id = 'before85';"
                       " s.textContent = '#board .drop[data-stage=done] .card > .chiprow{display:none}';"
                       " document.head.appendChild(s); })()")
                shot(f"done-archive-before-{w}-{theme}")
                b.eval("document.querySelector('#before85').remove()")
        S["metrics"]["doneArchiveChip"] = sizes

    # — keyboard: focus, arm, fire, persist —

    def test_keyboard_arms_then_archives_and_it_persists(self):
        b = self.load(1280, 800)
        before = json.loads(S["backend"].get("/api/state")[1]).get("archivedCount", 0)
        # reached the way a keyboard user reaches it: Tab from the card
        b.eval(f"document.querySelector('{SEL}').focus()")
        for _ in range(8):
            if b.eval(f"document.activeElement === document.querySelector('{CHIP}')"):
                break
            b.key("Tab")
        self.assertTrue(b.eval(f"document.activeElement === document.querySelector('{CHIP}')"),
                        "Tab from the card never reached the archive chip")
        self.assertTrue(b.eval("document.activeElement.matches(':focus-visible')"))
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual("none", ring, "no visible focus ring on the chip")
        shot("done-archive-focused-1280")
        b.key("Enter")
        self.assertTrue(b.wait(f"document.querySelector('{CHIP}').classList.contains('armed')", 5),
                        "Enter did not arm the archive")
        self.assertIn("archive it?", b.eval(f"document.querySelector('{CHIP}').innerText"))
        self.assertFalse(b.eval("document.querySelector('#drawer').classList.contains('open')"),
                         "arming opened the card")
        self.assertTrue(self.done.exists(), "arming archived the card")
        shot("done-archive-armed-1280")
        b.key("Enter")
        self.assertTrue(b.wait(f"!document.querySelector('{SEL}')", 10),
                        "the card did not leave the board")
        self.assertTrue(self.archived.exists() and not self.done.exists())
        self.assertIn("**Status:** Archived", self.archived.read_text(encoding="utf-8"))
        self.assertFalse(b.eval("document.querySelector('#drawer').classList.contains('open')"),
                         "archiving opened the card")
        self.assertIn("⌘Z brings it back", b.eval("document.querySelector('#toastmsg').textContent"))
        self.assertEqual(before + 1,
                         json.loads(S["backend"].get("/api/state")[1]).get("archivedCount"))
        # a reload reads the disk: it is still archived
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15))
        time.sleep(0.5)
        self.assertFalse(b.eval(f"!!document.querySelector('{SEL}')"),
                         "the archived card came back on reload")

    # — pointer and touch: no detail view, no drag, one request —

    def test_a_click_never_opens_the_card_and_a_press_never_drags(self):
        b = self.load(1280, 800)
        b.click(CHIP)
        self.assertTrue(b.wait(f"document.querySelector('{CHIP}').classList.contains('armed')", 5))
        self.assertFalse(b.eval("document.querySelector('#drawer').classList.contains('open')"),
                         "a click on the chip opened the card")
        drag = b.eval(
            "(() => { const c = document.querySelector(%s), a = c.querySelector('[data-arch]');"
            " a.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, pointerType: 'mouse'}));"
            " const e = new DragEvent('dragstart', {bubbles: true, cancelable: true,"
            "   dataTransfer: new DataTransfer()});"
            " c.dispatchEvent(e);"
            " const out = {prevented: e.defaultPrevented, dragging: !!S.dragging,"
            "   cls: c.classList.contains('dragging')};"
            " window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));"
            " const e2 = new DragEvent('dragstart', {bubbles: true, cancelable: true,"
            "   dataTransfer: new DataTransfer()});"
            " c.dispatchEvent(e2); out.after = e2.defaultPrevented;"
            " c.dispatchEvent(new DragEvent('dragend', {bubbles: true})); return out; })()"
            % json.dumps(SEL))
        self.assertEqual({"prevented": True, "dragging": False, "cls": False, "after": False},
                         drag, "pressing the chip started a drag, or the card stayed undraggable")

    def test_a_tap_arms_and_fires_through_the_same_path(self):
        b = self.load(390, 780)
        tap = ("(() => { const a = document.querySelector(%s);"
               " a.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, pointerType: 'touch'}));"
               " window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, pointerType: 'touch'}));"
               " a.click(); })()" % json.dumps(CHIP))
        b.eval(tap)
        self.assertTrue(b.wait(f"document.querySelector('{CHIP}').classList.contains('armed')", 5),
                        "a tap did not arm the archive")
        b.eval(tap)
        self.assertTrue(b.wait(f"!document.querySelector('{SEL}')", 10), "a second tap did not archive")
        self.assertTrue(self.archived.exists())
        self.assertFalse(b.eval("document.querySelector('#drawer').classList.contains('open')"))

    # — pending, refusal and duplicates —

    def test_pending_then_refused_keeps_the_card_and_sends_one_request(self):
        # a real refusal from the real backend: a card of that name is
        # already in archive/, so /api/archive answers with its error
        self.archived.write_text(CARD.replace("Done", "Archived"), encoding="utf-8")
        b = self.load(1280, 800)
        # every archive request is counted and held for a moment, so the
        # pending state can be read; it still goes to the real route
        b.eval("window.__archives = 0; { const f = window.fetch; window.fetch = async (u, o) => {"
               " if (String(u) === '/api/archive') { window.__archives++;"
               "   await new Promise(r => setTimeout(r, 1500)); }"
               " return f(u, o); }; }")
        b.click(CHIP)
        self.assertTrue(b.wait(f"document.querySelector('{CHIP}').classList.contains('armed')", 5))
        b.click(CHIP)
        self.assertTrue(b.wait(f"document.querySelector('{CHIP}').classList.contains('busy')", 5),
                        "no pending state while the request is out")
        self.assertTrue(b.eval(f"document.querySelector('{CHIP}').disabled"))
        self.assertIn("archiving…", b.eval(f"document.querySelector('{CHIP}').innerText"))
        shot("done-archive-pending-1280")
        # more clicks while it is out change nothing
        for _ in range(3):
            b.eval(f"document.querySelector('{CHIP}').click()")
        self.assertTrue(b.wait("document.querySelector('#toast').classList.contains('err')", 10),
                        "the refusal was not reported")
        self.assertIn("already exists in archive/",
                      b.eval("document.querySelector('#toastmsg').textContent"))
        shot("done-archive-refused-1280")
        self.assertEqual(1, b.eval("window.__archives"), "repeated clicks sent more than one request")
        self.assertTrue(self.done.exists(), "a refused archive moved the card")
        self.assertTrue(b.wait(f"!!document.querySelector('{CHIP}')"
                               f" && !document.querySelector('{CHIP}').disabled"
                               f" && !document.querySelector('{CHIP}').classList.contains('busy')", 5),
                        "the card did not come back to rest after the refusal")
        self.assertEqual("Done", b.eval(f"document.querySelector('{SEL}').closest('.drop')"
                                        ".dataset.stage.replace('done', 'Done')"))


if __name__ == "__main__":
    unittest.main()
