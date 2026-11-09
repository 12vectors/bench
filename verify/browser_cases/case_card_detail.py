"""The card detail view (task 81), in the browser: identity and the next
step first, five sections reachable by keyboard, acceptance tracked and
confirmed with a count that follows, a finding carried through a reported
fix to its resolution, long reports, 390px, real 200% zoom, and a live
update that keeps the reader's place.

Every card here is a disposable one written into the fixture's work state
and removed afterwards; no live card's acceptance is touched. The mockup
this was designed from is not in the repository, so the screenshots are
the record a reviewer compares against it.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "09-structured-detail-fixture"
FILE = f"{STEM}.md"
LONG = "\n\n".join(f"Paragraph {i}: the reviewer read the change against the brief "
                   f"and the running app, and wrote this down at length so the "
                   f"detail view has a long report to hold." for i in range(60))
CARD = f"""# Structured detail fixture

**Status:** Review
**Priority:** High

## Context

A disposable card for the detail view's browser case.

## Acceptance

- [ ] The detail view opens on the title and the next step
- [x] Keyboard users reach every section and tool
- [ ] Counts on the face open their section
- [ ] A long report stays readable at 390px

---

## Work report — 2026-11-08 12:00 (Juno)

{LONG}

---

## PR review at 0123456789ab — 2026-11-08 15:20 (Olive)

The tools row sits above the title.

PR REVIEW: REQUEST CHANGES

---

## PR update — 2026-11-08 16:00 (Wren)

Moved the title above the tools.
"""

DRAWER = "document.querySelector('#drawerbody')"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class StructuredDetail(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "review" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)

    def reading(self) -> dict:
        code, body = S["backend"].get(f"/api/card/acceptance?file={FILE}&stage=review")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open_card(self, width=1280, height=900, zoom=None):
        b = S["browser"]
        if zoom:
            b.zoom(width, height, zoom)
        else:
            b.viewport(width, height)
        b.goto("/")
        sel = f'#board article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        self.assertTrue(b.wait("!!(S.acc[%s] || {}).data" % json.dumps(FILE), 10),
                        "acceptance never loaded")

    def tab(self, key: str):
        b = S["browser"]
        b.click(f"#dtab-{key}")
        self.assertTrue(b.wait(f"!document.querySelector('#dsec-{key}').hidden", 5),
                        f"the {key} section did not open")

    def no_sideways_scroll(self, where: str):
        b = S["browser"]
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {where}")
        self.assertLessEqual(b.eval("document.querySelector('#drawer').getBoundingClientRect().right"),
                             b.eval("window.innerWidth") + 1, f"the drawer is off-screen at {where}")

    def test_identity_sections_acceptance_findings_and_layout(self):
        b = S["browser"]
        self.open_card()

        # — identity, state and the next step lead; tools come after —
        title = b.eval("document.querySelector('#drawer .dtitle').innerText")
        self.assertEqual(title, "Structured detail fixture")
        order = b.eval("(() => { const y = s => document.querySelector(s).getBoundingClientRect().top;"
                       " return [y('#drawer .dtitle'), y('#dsec-overview .dnext'),"
                       " y('#dsec-overview .cardtools, #dsec-overview #drawertools')]; })()")
        self.assertLess(order[0], order[1], "the title is not above the next step")
        self.assertLess(order[1], order[2], "the next step is not above the tools")
        overview = b.eval("document.querySelector('#dsec-overview').innerText")
        self.assertIn("Acceptance results not recorded", overview)
        self.assertIn("Olive asked for changes at 0123456789ab", overview)
        self.assertIn("1 PR update since", overview)
        shot("detail-overview-1280x900")

        # — every section by keyboard: the tab row is one stop, arrows move —
        b.eval("document.querySelector('#dtab-overview').focus()")
        for key in ("brief", "acceptance", "reviews", "activity"):
            b.key("ArrowRight")
            self.assertTrue(b.wait(f"document.activeElement && document.activeElement.id === 'dtab-{key}'"
                                   f" && !document.querySelector('#dsec-{key}').hidden", 5),
                            f"ArrowRight did not reach {key}")
        b.key("ArrowRight")
        self.assertTrue(b.wait("document.activeElement.id === 'dtab-overview'", 5),
                        "the tab row does not wrap")

        # — a legacy card: results not recorded, nothing ticked, nothing written —
        self.tab("acceptance")
        before = self.card.read_text(encoding="utf-8")
        text = b.eval("document.querySelector('#dsec-acceptance').innerText")
        self.assertIn("Acceptance results not recorded", text)
        self.assertIn("a legacy claim, not an acceptance", text)
        self.assertEqual(self.card.read_text(encoding="utf-8"), before,
                         "viewing the card wrote to it")
        self.assertFalse(b.eval(f"!!document.querySelector('#board article.card[data-file=\"{FILE}\"] [data-section]')"),
                         "an untracked card wears a count")
        shot("detail-acceptance-untracked-1280x900")

        # — Track: armed, then ids, pending, and the legacy tick as a claim —
        b.click("#accTrack")
        self.assertTrue(b.wait("document.querySelector('#accTrack').classList.contains('armed')", 5),
                        "Track did not arm")
        b.click("#accTrack")
        self.assertTrue(b.wait("document.querySelectorAll('#dsec-acceptance details.arow').length === 4", 10),
                        "tracking did not draw four criteria")
        reading = self.reading()
        self.assertEqual([c["state"] for c in reading["criteria"]],
                         ["pending", "reported", "pending", "pending"])
        self.assertIn("- [x] Keyboard users reach every section and tool <!-- ac:",
                      self.card.read_text(encoding="utf-8"), "the original tick was not kept")
        face = f"#board article.card[data-file=\"{FILE}\"] [data-section=\"acceptance\"]"
        self.assertTrue(b.wait(f"(document.querySelector('{face}') || {{}}).innerText"
                               f" && document.querySelector('{face}').innerText.includes('0/4 accepted')", 10),
                        "the face did not show 0/4 accepted")

        # — a manual visual acceptance, with a note and a reference —
        first = reading["criteria"][0]["id"]
        b.click(f"#dsec-acceptance details[data-keep=\"{first}\"] > summary")
        b.eval(f"(() => {{ const n = document.querySelector('#acc-note-{first}');"
               " n.value = 'Checked by eye at 1280 and 390px: title, then next step.';"
               " n.dispatchEvent(new Event('input'));"
               f" const r = document.querySelector('#acc-ref-{first}');"
               " r.value = 'detail-overview-1280x900.png'; r.dispatchEvent(new Event('input')); }})()")
        b.click(f"[data-decide=\"{first}\"]")
        self.assertTrue(b.wait("document.querySelector('#dsec-acceptance .acount').innerText"
                               ".includes('1 of 4')", 10), "the count did not reach 1 of 4")
        self.assertTrue(b.wait(f"document.querySelector('{face}').innerText.includes('1/4 accepted')", 10),
                        "the face count did not follow")
        row = f"#dsec-acceptance details[data-keep=\"{first}\"]"
        self.assertEqual(b.eval(f"document.querySelector('{row} .abox').innerText"), "✓")
        record = b.eval(f"document.querySelector('{row} .arec').innerText")
        self.assertIn("confirmed by a person", record)
        self.assertIn("detail-overview-1280x900.png", record)
        self.assertEqual(b.eval("document.querySelectorAll('#dsec-acceptance .abox')"
                                ".length - [...document.querySelectorAll('#dsec-acceptance .abox')]"
                                ".filter(x => x.innerText !== '✓').length"), 1,
                         "a criterion nobody accepted wears a tick")
        shot("detail-acceptance-confirmed-1280x900")

        # — a stale page cannot overwrite the newer decision —
        second = reading["criteria"][2]
        code, body = S["backend"].post("/api/card/acceptance/decide", {
            "file": FILE, "stage": "review", "criterion": first, "state": "needs-work",
            "rev": reading["criteria"][0]["rev"], "basis": reading["criteria"][0]["basis"],
            "note": "from a page that never saw the acceptance"})
        self.assertEqual(code, 409, body[:200])
        self.assertTrue(json.loads(body).get("stale"))

        # — survives a reload —
        self.open_card()
        self.tab("acceptance")
        self.assertIn("1 of 4", b.eval("document.querySelector('#dsec-acceptance .acount').innerText"))

        # — a finding, its reported fix, and the resolution on a recheck —
        self.tab("reviews")
        b.click("#dsec-reviews details[data-keep=\"newfinding\"] > summary")
        rnd = self.reading()["rounds"][0]["id"]
        b.eval("(() => { const t = document.querySelector('#fnd-text');"
               " t.value = 'The tools row sits above the title'; t.dispatchEvent(new Event('input'));"
               f" const s = document.querySelector('#fnd-round'); s.value = {json.dumps(rnd)};"
               " s.dispatchEvent(new Event('change')); })()")
        b.click("#fndOpen")
        self.assertTrue(b.wait("document.querySelector('#dsec-reviews .acount').innerText.startsWith('1 open')", 10),
                        "the finding was not recorded open")
        fid = self.reading()["findings"][0]["id"]
        frow = f"#dsec-reviews details[data-keep=\"{fid}\"]"

        def event(button: str, note: str, word: str):
            b.eval(f"(() => {{ const n = document.querySelector('#fnd-note-{fid}');"
                   f" n.value = {json.dumps(note)}; n.dispatchEvent(new Event('input')); }})()")
            b.click(f"{frow} button[data-event=\"{button}\"]")
            self.assertTrue(b.wait(f"(document.querySelector('{frow} .aword') || {{}}).innerText"
                                   f" === {json.dumps(word)}", 10), f"the finding never read '{word}'")

        event("fix-reported", "Moved the title above the tools in the PR update.",
              "fix reported — awaiting recheck")
        self.assertIn("fix reported", b.eval("document.querySelector('#dsec-overview .dnext').innerText"))
        shot("detail-reviews-fix-reported-1280x900")
        event("resolved", "Rechecked at 1280px: the title leads.", "resolved")
        chain = b.eval(f"document.querySelector('{frow} .chain').innerText")
        for word in ("found", "fix reported", "resolved"):
            self.assertIn(word, chain)
        self.assertIn("Olive asked for changes at 0123456789ab",
                      b.eval("document.querySelector('#dsec-reviews').innerText"),
                      "the historic verdict was rewritten")
        shot("detail-reviews-resolved-1280x900")

        # — activity: a long report, and a live update keeps the reading place —
        self.tab("activity")
        report = ("[...document.querySelectorAll('#dsec-activity details.rpt')]"
                  ".find(d => d.querySelector('summary').innerText.includes('Work report'))")
        b.click(f"#dsec-activity details.rpt[data-keep^=\"rep:Work report\"] > summary")
        self.assertTrue(b.wait(f"{report}.open", 5), "the long report did not open")
        b.eval(f"{DRAWER}.scrollTop = {report}.offsetTop + 600")
        self.assertGreater(b.eval(f"{DRAWER}.scrollTop"), 400,
                           "the long report did not make the view scroll")
        b.eval(f"{DRAWER}.dispatchEvent(new Event('scroll'))")
        kept = b.eval(f"{report}.getBoundingClientRect().top")
        code, body = S["backend"].post("/api/card/acceptance/decide", {
            "file": FILE, "stage": "review", "criterion": second["id"], "state": "accepted",
            "rev": second["rev"], "basis": second["basis"],
            "note": "accepted from another page"})
        self.assertEqual(code, 200, body[:200])
        self.assertTrue(b.wait("document.querySelector('#dsec-activity').innerText"
                               ".includes('accepted from another page')", 15),
                        "the live update never arrived")
        self.assertTrue(b.wait(f"{report}.open", 5), "a live update closed the report")
        self.assertLessEqual(abs(b.eval(f"{report}.getBoundingClientRect().top") - kept), 4,
                             "a live update moved what the reader was reading")
        shot("detail-activity-long-report-1280x900")

        # — 390px and a real 200% zoom: no sideways scroll, title in view —
        for where, args in (("390x844", dict(width=390, height=844)),
                            ("1280x800 at 200% zoom", dict(width=1280, height=800, zoom=2))):
            self.open_card(**args)
            if "zoom" in args:
                self.assertEqual(b.eval("window.devicePixelRatio"), 2)
                self.assertEqual(b.eval("window.innerWidth"), 640)
            self.no_sideways_scroll(where)
            for key in ("overview", "acceptance", "reviews"):
                self.tab(key)
                self.no_sideways_scroll(f"{where}, {key}")
            self.assertGreaterEqual(b.eval("document.querySelector('#drawer .dtitle')"
                                           ".getBoundingClientRect().top"), -1)
            shot("detail-reviews-" + where.replace(" ", "-").replace("%", "pc"))
        b.viewport(1280, 800)
        S["metrics"]["cardDetail"] = {"criteria": 4, "accepted": 2, "findings": 1}
