"""A card's Inputs tab (task 132), in the browser against the disposable
backend: a card in To Do whose brief names a screenshot, an API
specification and a design document bench has no approved way to
retrieve.

1. the Inputs tab is reachable by keyboard beside the other sections, and
   leads with the card's state in words — Needs information — with every
   input, its state, its reason and its fix;
2. a person's move into In Progress is refused, the card stays in To Do,
   and the refusal opens the Inputs tab;
3. classifying the design document optional writes the section into the
   brief, the tab reads Ready, and the classification survives a reload;
4. Recheck information starts a check and reads back;
5. an edit to the brief on disk makes the tab follow it, and the move then
   goes through;
6. at 390px nothing scrolls sideways, and focus stays on a classify
   control across a live redraw.

Every card here is a disposable one written into the fixture's work state
and removed afterwards.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
FILE = "08-inputs-fixture.md"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
CARD = """# Inputs fixture

**Status:** To Do
**Priority:** High

## Context

Match the [login screenshot](../../reference/inputs-fixture-login.png) and
follow the [API specification](../../reference/inputs-fixture-api.md). The
design is in [the design document](https://wiki.example.invalid/design).

## Acceptance

- [ ] It matches the screenshot
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


class InputsTab(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        work = backend.work
        self.refs = [work / "reference" / "inputs-fixture-login.png",
                     work / "reference" / "inputs-fixture-api.md"]
        self.refs[0].parent.mkdir(parents=True, exist_ok=True)
        self.refs[0].write_bytes(PNG)
        self.refs[1].write_text("# API\n\nPOST /session\n", encoding="utf-8")
        self.card = work / "tasks" / "to-do" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        for path in self.refs:
            self.addCleanup(path.unlink, True)
        for stage in ("to-do", "in-progress"):
            self.addCleanup((work / "tasks" / stage / FILE).unlink, True)

    def reading(self) -> dict:
        code, body = S["backend"].get(f"/api/card/inputs?file={FILE}")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open_inputs(self, width=1280, height=900):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        sel = f'#board article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        # by keyboard: the tab row is one stop, the arrows move along it
        b.eval("document.querySelector('#dtab-overview').focus()")
        for key in ("brief", "inputs"):
            b.key("ArrowRight")
            self.assertTrue(b.wait(f"document.activeElement && document.activeElement.id === 'dtab-{key}'"
                                   f" && !document.querySelector('#dsec-{key}').hidden", 5),
                            f"ArrowRight did not reach the {key} tab")
        self.assertTrue(b.wait("!!document.querySelector('#inpState')", 10),
                        "the Inputs reading never drew")

    def text(self) -> str:
        return S["browser"].eval("document.querySelector('#dsec-inputs').innerText")

    def test_inputs_tab_refusal_classification_and_recovery(self):
        b = S["browser"]
        self.open_inputs()
        text = self.text()
        self.assertIn("Needs information", text)
        self.assertIn("reference/inputs-fixture-login.png", text)
        self.assertIn("reference/inputs-fixture-api.md", text)
        self.assertIn("wiki.example.invalid", text)
        self.assertIn("BOARD_INPUT_FETCH_HOSTS", text)
        self.assertIn("Fix:", text)
        self.assertEqual(b.eval("document.querySelectorAll('#dsec-inputs .inprow').length"), 3)
        self.assertTrue(b.eval("!!document.querySelector('#dsec-inputs [role=alert]')"),
                        "Needs information is announced")
        self.assertTrue(b.eval(f"!!document.querySelector('#board article.card[data-file=\"{FILE}\"] "
                               f"[data-section=\"inputs\"]')"), "the face does not say it")
        shot("inputs-needs-information-1280x900")

        # a person's move into in-progress is refused and opens the tab
        b.eval("(() => { S.dtab[%s] = 'overview'; renderDrawer(); return true; })()" % json.dumps(FILE))
        b.eval(f"rawMove({json.dumps(FILE)}, 'to-do', 'in-progress')")
        self.assertTrue(b.wait("!document.querySelector('#dsec-inputs').hidden", 10),
                        "the refusal did not open the Inputs tab")
        self.assertTrue(self.card.exists(), "the card left To Do")
        shot("inputs-refused-move-1280x900")

        # classify the design document optional: the section is written
        index = b.eval("[...document.querySelectorAll('#dsec-inputs .inprow')]"
                       ".findIndex(r => r.innerText.includes('wiki.example.invalid'))")
        self.assertGreaterEqual(index, 0)
        b.eval(f"(() => {{ const s = document.querySelector('#inp-cls-input-{index}');"
               f" s.value = 'optional'; s.dispatchEvent(new Event('change')); return true; }})()")
        b.click(f"[data-inp-save=\"input:{index}\"]")
        self.assertTrue(b.wait("(document.querySelector('#inpState') || {}).innerText === 'Ready'", 15),
                        "classifying the blocker did not make the card ready")
        brief = self.card.read_text(encoding="utf-8")
        self.assertIn("## Inputs and required tools", brief)
        self.assertIn("- input, optional: https://wiki.example.invalid/design", brief)
        self.assertEqual(self.reading()["state"], "ready")
        shot("inputs-ready-after-classify-1280x900")

        # it survives a reload
        self.open_inputs()
        self.assertIn("Ready", self.text())
        self.assertIn("Optional", self.text())

        # Recheck information starts a check and reads back
        b.click("#inpRecheck")
        self.assertTrue(b.wait("(document.querySelector('#inpState') || {}).innerText === 'Ready'"
                               " && /Last check/.test(document.querySelector('#dsec-inputs').innerText)", 15),
                        "the recheck never read back")

        # an edit on disk: the API document goes missing, the tab follows
        self.refs[1].unlink()
        b.eval("loadState()")
        self.assertTrue(b.wait("(document.querySelector('#inpState') || {}).innerText === 'Needs information'", 15),
                        "the tab did not follow a change on disk")
        self.refs[1].write_text("# API again\n", encoding="utf-8")
        b.eval("loadState()")
        self.assertTrue(b.wait("(document.querySelector('#inpState') || {}).innerText === 'Ready'", 15))
        self.assertTrue(b.eval(f"rawMove({json.dumps(FILE)}, 'to-do', 'in-progress')"),
                        "the move was not allowed once ready")
        self.assertTrue(b.wait(f"!!document.querySelector('#board article.card[data-file=\"{FILE}\"]')", 10))

    def test_narrow_width_and_focus_survive_a_redraw(self):
        b = S["browser"]
        self.open_inputs(390, 844)
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, "the Inputs tab scrolls sideways at 390px")
        b.eval("document.querySelector('#inp-cls-input-0').focus()")
        b.eval("renderDrawer()")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.id === 'inp-cls-input-0'", 5),
                        "focus was dropped by a live redraw")
        shot("inputs-390x844")
