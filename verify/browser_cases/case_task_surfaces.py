"""Every remaining agent purpose in the shared conversation panel (task
206), driven in a browser against the disposable backend.

Task 205 put Orchestrator questions and Brain Agent drafts in the shared
right-hand panel. This case drives the purposes task 206 moves into it:

- **Card refinement.** A card's Discussion offers **Open in conversation
  panel**; the panel is about that card, the card stays the page. **Add
  comment** saves a comment and starts nothing; **Update card with agent**
  (armed) produces a proposal; **Discard** leaves the brief as it was;
  only an armed **Apply** changes it. The comments and decisions are the
  card's, read back after a reload, and the unsent text survives it.
- **A run inspected.** A card's Overview, Focus and Sessions offer
  **Inspect** for the latest run; the panel shows the run's identity, its
  agent-reported progress apart from what bench observed, and the exact
  Focus and session links. It draws no composer, says follow-up is not
  supported and names ‖ hold on the card instead.
- **A Brain seed draft and the reconciliation queue.** Opened from their
  entry points, each shows its observed state and the workspace its
  approval, review and Apply stay in; neither draws an answer or Apply
  control.
- Escape closes the panel and gives focus back; both themes at 1440 and
  390 px (full screen); opening and reading launch nothing.

The vendor CLI is the backend's stub (a refinement prints only what this
case wrote to `refine_reply`), the run is case_progress's fixture laid
over the page's own `fetch`, and the seed draft is a fixture answer to
`/api/brain/drafts?id=`. The case's card is written into the fixture's
work state and removed afterwards; no live card, Brain or agent is used.

Its refinements stay in the board's agent registry, which nothing clears,
so this file is named to run after every case that asserts the board has
never launched an agent (case_board, case_settings), as case_task_questions
is.

Run through verify/browser; any other way these refuse.
"""

import json
import time
import unittest

import benchverify as bv
import case_progress as cp

S = bv.SESSION
FILE = "46-tidy-the-export-brief.md"
CARD = ("# 46 — Tidy the export brief\n\n**Status:** To Do\n**Priority:** Medium\n\n"
        "## Context\n\nThe export brief needs its scope said once.\n\n"
        "## Acceptance\n\n- [ ] The scope is explicit\n")
NEW_BRIEF = ("## Context\n\nThe export brief needs its scope said once.\n\n"
             "## Scope\n\nCSV only; no hidden columns.\n\n"
             "## Acceptance\n\n- [ ] The scope is explicit\n")
PANEL = "document.querySelector('#agentpanel')"
OPEN = "document.querySelector('#agentpanel:not([hidden]) .ap-body')"


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
    return ("REFINEMENT PROPOSAL\n<<<EXPLANATION\nSaid the scope once, under its own heading.\n"
            f"EXPLANATION>>>\n<<<BRIEF\n{text}BRIEF>>>\n")


def panel_text(b) -> str:
    return b.eval(f"{PANEL}.textContent") or ""


def jobs() -> list:
    local = S["backend"].tool / "manager" / "local" / "state" / "refine"
    return sorted(p.name for p in local.rglob("*.json")) if local.is_dir() else []


def ask_button(b, purpose: str, target: dict):
    """An entry point as the page draws one, put on the page and clicked:
    the panel's own listener opens the conversation."""
    b.eval("(() => { const x = document.createElement('button'); x.id = 'case-ask'; x.type = 'button'; "
           f"x.dataset.agentAsk = {json.dumps(purpose)}; x.dataset.agentTarget = {json.dumps(json.dumps(target))}; "
           "x.textContent = 'open'; document.body.appendChild(x); x.click(); x.remove(); return true; })()")


def close(b):
    b.eval("(() => { document.querySelector('#agentpanel [data-pact=\"close\"]')?.click(); "
           "try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")


class Refinement(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "to-do" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.addCleanup(backend.refine_reply.unlink, True)
        self.addCleanup(lambda: close(S["browser"]))
        deadline = time.time() + 15
        while time.time() < deadline:
            code, body = backend.get("/api/state")
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
            if code == 200 and FILE in text:
                break
            time.sleep(0.3)

    def brief(self) -> str:
        return self.card.read_text(encoding="utf-8").split("\n## Discussion\n")[0]

    def discussion(self) -> dict:
        code, body = S["backend"].get(f"/api/card/discussion?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open_panel(self, width=1440, height=900):
        b = S["browser"]
        b.viewport(width, height)
        b.eval("(() => { try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
        b.goto("/cards/46?tab=brief")
        if not b.wait("!!document.querySelector('#disc-live') && !!document.querySelector('.discussion .ap-open')", 15):
            raise bv.CheckFailed("the card's Discussion never offered the conversation panel")
        b.click(".discussion .ap-open")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-commentbox')", 10):
            raise bv.CheckFailed("Open in conversation panel opened no refinement conversation: "
                                 + panel_text(b)[:300])
        return b

    def test_1_comment_starts_nothing_propose_discard_apply(self):
        before_jobs = jobs()
        brief_before = self.brief()
        b = self.open_panel()
        theme(b, "dark")
        self.assertEqual("/cards/46", b.eval("location.pathname"), "the card stays the page")
        self.assertIn("46", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        self.assertIn("Card refinement", panel_text(b))
        self.assertEqual("ap-commentbox", b.eval("document.activeElement && document.activeElement.id"),
                         "the composer takes focus")
        # — Add comment saves and starts nothing —
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'Just a note <b>for later</b>'); return true; })()")
        b.click("#agentpanel [data-pref='comment']")
        if not b.wait("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
                      ".some(r => r.innerText.includes('for later'))", 10):
            raise bv.CheckFailed("the saved comment never appeared in the panel")
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel .dentry.comment b')"),
                         "a comment is drawn as text, never markup")
        time.sleep(1)
        self.assertEqual(before_jobs, jobs(), "Add comment launched a refinement")
        self.assertEqual(self.discussion()["jobs"], [], "a comment alone asked for nothing")
        self.assertEqual(self.brief(), brief_before)
        shot("agent-refine-panel-1440-dark")

        # — Update card with agent: armed, then a proposal; Discard keeps the brief —
        S["backend"].refine_reply.write_text(proposal(NEW_BRIEF), encoding="utf-8")
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'Say the scope once'); return true; })()")
        b.click("#agentpanel [data-pref='refine']")
        if not b.wait("document.querySelector('#agentpanel [data-pref=\"refine\"]').classList.contains('armed')", 5):
            raise bv.CheckFailed("Update card with agent did not arm on the first click")
        b.click("#agentpanel [data-pref='refine']")
        proposed = ("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
                    ".find(r => r.innerText.includes('Say the scope once'))")
        if not b.wait(f"(() => {{ const r = {proposed}; return !!r && !!r.querySelector('.rdiff'); }})()", 30):
            raise bv.CheckFailed("no proposal arrived in the panel: " + panel_text(b)[:400])
        self.assertEqual(self.brief(), brief_before, "a proposal changes nothing")
        shot("agent-refine-proposal-1440-dark")
        b.eval(f"{proposed}.querySelector('button[data-act=\"discard\"]').click()")
        if not b.wait(f"(() => {{ const r = {proposed}; return !!r && r.innerText.includes('discarded'); }})()", 15):
            raise bv.CheckFailed("Discard was not recorded: " + panel_text(b)[:400])
        self.assertEqual(self.brief(), brief_before, "Discard preserved the brief")

        # — a second ask, applied: only an armed Apply changes the brief —
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'Now apply the scope'); return true; })()")
        b.click("#agentpanel [data-pref='refine']")
        b.wait("document.querySelector('#agentpanel [data-pref=\"refine\"]').classList.contains('armed')", 5)
        b.click("#agentpanel [data-pref='refine']")
        second = ("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
                  ".find(r => r.innerText.includes('Now apply the scope'))")
        if not b.wait(f"(() => {{ const r = {second}; return !!r && !!r.querySelector('button[data-act=\"apply\"]'); }})()", 30):
            raise bv.CheckFailed("the second proposal never offered Apply: " + panel_text(b)[:400])
        apply_btn = f"{second}.querySelector('button[data-act=\"apply\"]')"
        b.eval(f"{apply_btn}.click()")
        if not b.wait(f"{apply_btn} && {apply_btn}.classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm on the first click")
        self.assertEqual(self.brief(), brief_before, "an armed Apply has changed nothing yet")
        b.eval(f"{apply_btn}.click()")
        deadline = time.time() + 15
        while time.time() < deadline and "CSV only" not in self.brief():
            time.sleep(0.3)
        self.assertIn("CSV only", self.brief(), "Apply changed the brief")
        outcomes = [e.get("outcome") for e in self.discussion()["entries"] if e["kind"] == "decision"]
        self.assertEqual(sorted(outcomes), ["applied", "discarded"], "both decisions are the card's")

        # — a reload gives the conversation back, read from the card —
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'unsent words'); return true; })()")
        time.sleep(0.5)
        b.goto("/cards/46?tab=brief")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-commentbox')", 20):
            raise bv.CheckFailed("a reload did not give the refinement conversation back")
        b.wait("document.querySelectorAll('#agentpanel .dentry').length >= 4", 10)
        self.assertEqual("unsent words", b.eval("document.querySelector('#ap-commentbox').value"),
                         "the unsent text survived the reload")
        text = panel_text(b)
        self.assertIn("Now apply the scope", text)
        self.assertIn("applied", text)

        # — Escape closes and gives focus back —
        b.eval("document.querySelector('#ap-commentbox').focus()")
        b.key("Escape")
        self.assertTrue(b.wait(f"{PANEL}.hidden", 5), "Escape did not close the panel")

    def test_2_narrow_light_full_screen(self):
        b = self.open_panel(390, 844)
        theme(b, "light")
        self.assertTrue(b.eval("document.body.classList.contains('agent-full')"), "a phone is full screen")
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391, "no document overflow")
        self.assertTrue(b.eval("!!document.querySelector('#agentpanel [data-pact=\"close\"]')"
                               ".getAttribute('aria-label')"), "Close has an accessible name")
        shot("agent-refine-panel-390-light")
        theme(b, "dark")


class RunInspection(unittest.TestCase):

    def tearDown(self):
        b = S["browser"]
        close(b)
        b.eval("(() => { if (window.__realFetch) window.fetch = window.__realFetch; return true; })()")

    def test_inspect_a_run_from_the_card_and_focus(self):
        now = time.time()
        cp.board()
        cp.install(cp.milestone_fixture(now))
        b = S["browser"]
        theme(b, "dark")
        b.eval(f"document.querySelector('[data-file=\"{cp.CARD}\"]').focus()")
        b.key("Enter")
        if not b.wait("!!document.querySelector('#drawer [data-agent-ask=\"run\"]')", 10):
            raise bv.CheckFailed("the card's Overview offered no Inspect this run")
        b.click("#drawer [data-agent-ask='run']")
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .prog')", 10):
            raise bv.CheckFailed("Inspect this run opened no run in the panel: " + panel_text(b)[:300])
        text = panel_text(b)
        self.assertIn("Run inspection", text)
        self.assertIn("Implementation run", text)
        self.assertIn("agent-reported", text)
        self.assertIn("observed by bench", text)
        self.assertIn("adding the permissions panel", text, "its reported progress")
        self.assertIn("No follow-up here", text)
        self.assertIn("‖ hold", text)
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea, #agentpanel input[type=text]')"),
                         "no composer on a coding run")
        self.assertEqual(f"/focus/{cp.SID}?card=3",
                         b.eval("document.querySelector('#agentpanel a[href^=\"/focus/\"]').getAttribute('href')"))
        shot("agent-run-inspect-1440-dark")
        # Focus offers the same run
        b.click(f"#agentpanel a[href^='/focus/']")
        if not b.wait("!!document.querySelector('#cgrid [data-agent-ask=\"run\"]')", 10):
            raise bv.CheckFailed("Focus offered no Inspect in panel")
        b.viewport(390, 844)
        b.wait(f"{OPEN}", 5)
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391)
        theme(b, "light")
        shot("agent-run-inspect-390-light")
        theme(b, "dark")


class BrainBatchAndQueue(unittest.TestCase):

    def tearDown(self):
        b = S["browser"]
        close(b)
        b.eval("(() => { if (window.__realFetch2) window.fetch = window.__realFetch2; return true; })()")

    def test_a_seed_draft_and_the_queue_keep_their_workspaces(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/brain")
        b.wait("!!document.querySelector('#bshell')", 15)
        draft = {"id": "bd-case206", "mode": "seed", "status": "questions", "round": 1, "maxRounds": 4,
                 "conversation": [{"who": "you", "kind": "request", "text": "Seed the handbook", "at": "10:00"},
                                  {"who": "agent", "kind": "questions", "text": "Which deploy target?", "at": "10:02"}],
                 "questions": [{"id": "q1", "question": "Which deploy target? <img src=x>", "why": "Operations"}],
                 "progress": {"done": ["sources pinned"], "doing": None, "remaining": ["your answers"],
                              "blocked": "your answers", "toolActivity": {"last": None}},
                 "seed": {"applies": "Sections reach the Brain only through the reviewed Apply."}}
        b.eval("(() => { window.__realFetch2 = window.__realFetch2 || window.fetch.bind(window); "
               f"const d = {json.dumps(draft)}; "
               "window.fetch = async (url, opts) => String(url).startsWith('/api/brain/drafts?id=bd-case206') "
               "? new Response(JSON.stringify({draft: d}), {status: 200}) : window.__realFetch2(url, opts); return true; })()")
        ask_button(b, "brainjob", {"draft": "bd-case206", "mode": "seed"})
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .ap-transcript')", 10):
            raise bv.CheckFailed("the seed draft never opened in the panel: " + panel_text(b)[:300])
        text = panel_text(b)
        self.assertIn("Waiting for your answers", text)
        self.assertIn("Which deploy target?", text)
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel img')"), "question text is text")
        self.assertEqual("/brain/seed", b.eval("document.querySelector('#agentpanel .ap-wsbtn').getAttribute('href')"))
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea, #agentpanel [data-sact]')"),
                         "answers, review and Apply stay in the workspace")
        shot("agent-seed-draft-1440-dark")
        # the reconciliation queue, observed: its own reading, not configured here
        ask_button(b, "reconcile", {"kind": "reconcile"})
        if not b.wait(f"{OPEN} && /Not configured|Off|On/.test(document.querySelector('#agentpanel .ap-body').textContent)", 10):
            raise bv.CheckFailed("the queue never read: " + panel_text(b)[:300])
        text = panel_text(b)
        self.assertIn("launched by the reconciliation policy, not by you", text)
        self.assertIn("What you can do here", text, "its limits are said")
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea')"))
        # both conversations are kept, switchable
        self.assertTrue(b.eval("!!document.querySelector('#agentpanel [data-pact=\"switch\"]')"))


if __name__ == "__main__":
    unittest.main()
