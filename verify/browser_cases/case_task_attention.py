"""Agent activity and its inbox (task 207), driven in a browser against
the disposable backend.

- **A result arrives with the panel closed.** A card refinement is asked
  through the backend (the stub vendor answers with a proposal) while the
  page sits on the Brain with no conversation open. The header's
  **Activity** control counts it unread, one toast says so, and focus
  stays where it was; nothing navigates and no panel opens.
- **It survives every view, a reload and a restart.** The control is on
  Board, Brain, Orchestrator and Settings with the same count; after a
  reload and after a board restart the item is still unread, with the
  same state words — *Ready for your review — not applied* — and the
  brief is unchanged.
- **Looking marks nothing read.** Opening the list, filtering it and
  closing it (Escape gives focus back to the control) leave the item
  unread; the inbox record is unchanged.
- **Open opens the exact conversation.** Open on the item opens the
  card's refinement conversation in the shared panel with the proposal
  in it, and marks that one transition read; the brief is still
  unchanged — a proposal is not applied.
- **No permission prompt.** Nothing asks the browser for notification
  permission unless the person clicks Turn on.
- Desktop 1440 in the night theme and 390 (the list full screen) in
  daylight, by pointer and by keyboard.

Not covered here: the vendored, source-hosted and external layouts (the
fixture is the standard disposable one), and a browser notification
actually shown (the sandboxed container browser denies the permission).

The vendor CLI is the backend's stub (a refinement prints only what this
case wrote to `refine_reply`). The case's card is written into the
fixture's work state and removed afterwards; no live card, Brain or agent
is used. It launches a refinement, so it is named to run after every case
that asserts the board has never launched an agent (case_board,
case_settings).

Run through verify/browser; any other way these refuse.
"""

import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
FILE = "48-attention-for-the-export.md"
CARD = ("# 48 — Attention for the export\n\n**Status:** To Do\n**Priority:** Medium\n\n"
        "## Context\n\nThe export needs one heading.\n\n"
        "## Acceptance\n\n- [ ] One heading\n")
NEW_BRIEF = ("## Context\n\nThe export needs one heading.\n\n## Scope\n\nCSV only.\n\n"
             "## Acceptance\n\n- [ ] One heading\n")
BTN = "document.querySelector('#attnbtn')"
POP = "document.querySelector('#attnpop')"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


def proposal(text: str) -> str:
    return ("REFINEMENT PROPOSAL\n<<<EXPLANATION\nSaid the scope under its own heading.\n"
            f"EXPLANATION>>>\n<<<BRIEF\n{text}BRIEF>>>\n")


def inbox() -> dict:
    code, body = S["backend"].get("/api/attention")
    if code != 200:
        raise bv.CheckFailed(f"/api/attention answered {code}")
    return json.loads(body)


def record_bytes() -> bytes:
    path = S["backend"].tool / "manager" / "local" / "state" / "attention" / "inbox.json"
    return path.read_bytes() if path.exists() else b""


class AttentionInbox(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "to-do" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.addCleanup(backend.refine_reply.unlink, True)
        deadline = time.time() + 15
        while time.time() < deadline:
            code, body = backend.get("/api/state")
            if code == 200 and FILE in body.decode("utf-8", "replace"):
                break
            time.sleep(0.3)

    def item(self, iid):
        return next((i for i in inbox()["items"] if i["id"] == iid), None)

    def wait_item(self, iid, state, timeout=30):
        deadline = time.time() + timeout
        found = None
        while time.time() < deadline:
            found = self.item(iid)
            if found and found["state"] == state:
                return found
            time.sleep(0.4)
        raise bv.CheckFailed(f"{iid} never read {state}: {found}")

    def brief(self) -> str:
        return self.card.read_text(encoding="utf-8").split("\n## Discussion\n")[0]

    def test_1_a_result_stays_discoverable_and_only_a_click_reads_it(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.eval("(() => { try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
        b.goto("/brain")
        b.wait(f"!!{BTN} && {BTN}.getAttribute('aria-label').startsWith('Agent activity')", 15)
        theme(b, "dark")
        # nothing may ask for notification permission unless the person clicks
        b.eval("(() => { window.__prompts = 0; if (window.Notification) { const o = Notification.requestPermission;"
               " Notification.requestPermission = (...a) => { window.__prompts += 1; return o.apply(Notification, a); }; }"
               " return true; })()")
        brief_before = self.brief()
        # — a refinement finishes while the panel is closed —
        S["backend"].refine_reply.write_text(proposal(NEW_BRIEF), encoding="utf-8")
        code, body = S["backend"].post("/api/card/refine", {"file": FILE, "stage": "to-do",
                                                           "text": "Say the scope once",
                                                           "request": "case-attention-1"})
        self.assertEqual(code, 200, body[:300])
        job = json.loads(body)
        jid = (job.get("job") or job).get("id")
        self.assertTrue(jid, body[:300])
        iid = f"refine:{jid}"
        found = self.wait_item(iid, "proposal-ready")
        self.assertTrue(found["unread"])
        self.assertIn("not applied", found["label"])
        b.eval("document.querySelector('.views a[data-view=\"brain\"]').focus()")
        if not b.wait(f"{BTN}.classList.contains('has-unread')", 20):
            raise bv.CheckFailed("the Activity control never counted the result: "
                                 + str(b.eval(f"{BTN}.getAttribute('aria-label')")))
        self.assertEqual("/brain", b.eval("location.pathname"), "nothing navigated")
        self.assertTrue(b.eval("document.querySelector('#agentpanel').hidden"), "no panel opened by itself")
        self.assertEqual("brain", b.eval("document.activeElement && document.activeElement.dataset.view"),
                         "focus stayed where it was")
        self.assertIn("unread", b.eval(f"{BTN}.getAttribute('aria-label')"))

        # — every top-level view carries the control and its count —
        for view in ("board", "orchestrator", "settings", "brain"):
            b.goto(f"/{view}")
            if not b.wait(f"{BTN}.classList.contains('has-unread')", 15):
                raise bv.CheckFailed(f"the Activity control lost its count on /{view}")
        self.assertTrue(self.item(iid)["unread"], "navigating marked nothing read")

        # — looking marks nothing read: open, filter, close —
        before = record_bytes()
        b.click("#attnbtn")
        if not b.wait(f"!{POP}.hidden && !!{POP}.querySelector('[data-item]')", 10):
            raise bv.CheckFailed("the activity list did not open")
        self.assertEqual("attn-title", b.eval("document.activeElement && document.activeElement.id"))
        b.click("#attnpop [data-attn='filter'][data-f='unread']")
        row = f"{POP}.querySelector('[data-item=\"{iid}\"]')"
        if not b.wait(f"!!{row}", 5):
            raise bv.CheckFailed("the result is not in the Unread filter: " + str(b.eval(f"{POP}.innerText"))[:400])
        self.assertIn("not applied", b.eval(f"{row}.innerText"))
        self.assertTrue(b.eval(f"{row}.classList.contains('unread')"))
        shot("agent-activity-1440-dark")
        b.key("Escape")
        self.assertTrue(b.wait(f"{POP}.hidden", 5), "Escape did not close the list")
        self.assertEqual("attnbtn", b.eval("document.activeElement && document.activeElement.id"),
                         "focus went back to the control")
        self.assertEqual(before, record_bytes(), "opening and closing the list wrote nothing")
        self.assertTrue(self.item(iid)["unread"])

        # — a reload and a board restart keep it unread, its words unchanged —
        b.goto("/board")
        b.wait(f"{BTN}.classList.contains('has-unread')", 15)
        S["backend"].stop()
        S["backend"].start()
        found = self.wait_item(iid, "proposal-ready")
        self.assertTrue(found["unread"], "a restart kept it unread")
        b.goto("/settings")
        if not b.wait(f"{BTN}.classList.contains('has-unread')", 20):
            raise bv.CheckFailed("after a restart the Activity control did not count the result")
        self.assertEqual(self.brief(), brief_before, "a proposal changed nothing")

        # — Open, by keyboard: the exact conversation, and that one transition read —
        b.eval("document.querySelector('#attnbtn').focus()")
        b.key("Enter")
        b.wait(f"!{POP}.hidden && !!{row}", 10)
        b.eval(f"{row}.querySelector('[data-attn=\"open\"]').focus()")
        b.key("Enter")
        if not b.wait("!document.querySelector('#agentpanel').hidden && "
                      "!!document.querySelector('#agentpanel .ap-refine, #agentpanel #ap-commentbox')", 15):
            raise bv.CheckFailed("Open did not open the card's refinement conversation")
        self.assertIn("48", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        if not b.wait("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
                      ".some(r => r.innerText.includes('Say the scope once') && !!r.querySelector('.rdiff'))", 15):
            raise bv.CheckFailed("the proposal is not in the opened conversation")
        deadline = time.time() + 10
        while time.time() < deadline and self.item(iid)["unread"]:
            time.sleep(0.3)
        self.assertFalse(self.item(iid)["unread"], "Open marked that transition read")
        self.assertEqual(self.brief(), brief_before, "opening a proposal applies nothing")
        self.assertEqual(0, b.eval("window.__prompts || 0"), "no notification permission was asked")
        b.eval("document.querySelector('#agentpanel [data-pact=\"close\"]')?.click()")

    def test_2_narrow_light_the_list_is_full_screen(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/board")
        b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
        theme(b, "light")
        b.viewport(390, 844)
        b.goto("/board")
        b.wait(f"!!{BTN}", 10)
        self.assertTrue(b.eval(f"!!{BTN}.getClientRects().length"), "the control is not folded behind More")
        self.assertTrue(b.eval(f"{BTN}.getAttribute('aria-label').startsWith('Agent activity')"))
        b.click("#attnbtn")
        if not b.wait(f"!{POP}.hidden", 10):
            raise bv.CheckFailed("the activity list did not open at 390px")
        self.assertTrue(b.eval(f"{POP}.classList.contains('narrow')"), "a phone shows the list full screen")
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391, "no document overflow")
        self.assertTrue(b.eval(f"!!{POP}.querySelector('[data-attn=\"close\"]').getAttribute('aria-label')"))
        shot("agent-activity-390-light")
        b.click("#attnpop [data-attn='close']")
        self.assertTrue(b.wait(f"{POP}.hidden", 5))
        b.viewport(1440, 900)
        theme(b, "dark")


if __name__ == "__main__":
    unittest.main()
