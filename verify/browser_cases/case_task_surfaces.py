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
- **Refinement from a relevance review.** The review's **Open in
  conversation panel** opens a conversation of its own about that review:
  its Comment is linked to the review, its armed **Update card from this
  review** asks a refinement from it, Discard keeps the brief and the
  filed review is never changed. A reload and a board restart give the
  conversation and its unsent text back and run nothing again; the
  card-wide conversation is another slot.
- **Ended runs.** A PR review run and a PR repair run that have ended are
  inspected from Sessions: the review's filed report matched by the run
  identity bench recorded, the review linked on the card and in the
  Brain, and the evidence linked to its exact record.
- **An evaluation draft** opens with its workspace (the Brain), and the
  queue opens from Automatic classification's own entry point.
- Escape closes the panel and gives focus back; both themes at 1440 and
  390 px (full screen); opening and reading launch nothing.

Not covered here: the vendored, source-hosted and external layouts (the
fixture is the standard disposable one), and an SSE reconnect without a
reload.

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

    def open_panel(self, width=1440, height=900, scheme=None):
        b = S["browser"]
        # the theme is switched on the Board at desktop width, before a
        # phone-width card view and a full-screen panel cover the header
        if scheme:
            b.viewport(1440, 900)
            b.goto("/board")
            b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
            theme(b, scheme)
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
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel .dentry.comment .cbody b')"),
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
        b = self.open_panel(390, 844, scheme="light")
        self.assertTrue(b.eval("document.body.classList.contains('agent-full')"), "a phone is full screen")
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391, "no document overflow")
        self.assertTrue(b.eval("!!document.querySelector('#agentpanel [data-pact=\"close\"]')"
                               ".getAttribute('aria-label')"), "Close has an accessible name")
        shot("agent-refine-panel-390-light")
        b.click("#agentpanel [data-pact='close']")
        b.wait(f"{PANEL}.hidden", 5)
        b.viewport(1440, 900)
        b.goto("/board")
        b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
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
        # narrow and light: the theme switched with the panel closed (full
        # screen covers the header), then the run reopened from Focus
        b.click("#agentpanel [data-pact='close']")
        b.wait(f"{PANEL}.hidden", 5)
        b.viewport(390, 844)
        theme(b, "light")
        b.eval("document.querySelector('#cgrid [data-agent-ask=\"run\"]').click()")
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .prog')", 10):
            raise bv.CheckFailed("Inspect in panel from Focus opened no run: " + panel_text(b)[:300])
        self.assertTrue(b.eval("document.body.classList.contains('agent-full')"), "a phone is full screen")
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391)
        shot("agent-run-inspect-390-light")
        b.click("#agentpanel [data-pact='close']")
        b.wait(f"{PANEL}.hidden", 5)
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
        # an evaluation draft: the same read-only shell, its workspace the Brain
        evaluation = dict(draft, id="bd-case206e", mode="evaluate", status="ready",
                          conversation=[{"who": "you", "kind": "request", "text": "Evaluate two entries", "at": "10:00"}],
                          questions=[], evaluate={"entries": ["e-a", "e-b"]})
        b.eval("(() => { const prev = window.fetch; "
               f"const d = {json.dumps(evaluation)}; "
               "window.fetch = async (url, opts) => String(url).startsWith('/api/brain/drafts?id=bd-case206e') "
               "? new Response(JSON.stringify({draft: d}), {status: 200}) : prev(url, opts); return true; })()")
        ask_button(b, "brainjob", {"draft": "bd-case206e", "mode": "evaluate"})
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .ap-transcript') && "
                      "/Evaluate two entries/.test(document.querySelector('#agentpanel').textContent)", 10):
            raise bv.CheckFailed("the evaluation draft never opened in the panel: " + panel_text(b)[:300])
        self.assertEqual("/brain", b.eval("document.querySelector('#agentpanel .ap-wsbtn').getAttribute('href')"),
                         "its review and Apply stay in Evaluate classifications")
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea, #agentpanel [data-evact]')"),
                         "no answer, review or Apply control in the panel")
        # the reconciliation queue, observed — opened from its own entry
        # point in Automatic classification, not a synthetic button
        close(b)
        # Automatic classification is drawn under the library, which an
        # empty Brain does not have: one entry, written for this step and
        # removed afterwards (with brain/ itself when this case made it)
        brain = S["backend"].work / "brain"
        made = not brain.exists()
        brain.mkdir(exist_ok=True)
        entry = brain / "e-case206.md"
        entry.write_text("# A fixture entry for case 206\n\n**Brain:** 1\n**Id:** e-case206\n**Kind:** Guide\n"
                         "**Classification:** Current\n**Summary:** Written by the browser case.\n\nBody.\n",
                         encoding="utf-8")

        def tidy():
            entry.unlink(missing_ok=True)
            if made:
                try:
                    brain.rmdir()
                except OSError:
                    pass
        self.addCleanup(tidy)
        b.goto("/brain")
        # the panel shows its reading only once a person asks for it
        if not b.wait("!!document.querySelector('[data-rcact=\"show\"]') || "
                      "!!document.querySelector('button.ap-open[data-agent-ask=\"reconcile\"]')", 20):
            raise bv.CheckFailed("the Brain drew no Automatic classification panel")
        b.eval("(() => { document.querySelector('[data-rcact=\"show\"]')?.click(); return true; })()")
        if not b.wait("!!document.querySelector('button.ap-open[data-agent-ask=\"reconcile\"]')", 20):
            raise bv.CheckFailed("Automatic classification offered no Observe in the conversation panel")
        b.eval("document.querySelector('button.ap-open[data-agent-ask=\"reconcile\"]').click()")
        if not b.wait(f"{OPEN} && /Not configured|Off|On/.test(document.querySelector('#agentpanel .ap-body').textContent)", 10):
            raise bv.CheckFailed("the queue never read: " + panel_text(b)[:300])
        text = panel_text(b)
        self.assertIn("launched by the reconciliation policy, not by you", text)
        self.assertIn("What you can do here", text, "its limits are said")
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea')"))
        shot("agent-reconcile-observed-1440-dark")


RL_HEAD = "## Relevance review — 2026-11-20 10:00 (Hazel)"
PR_HEAD = "## PR review at 0123abcd4567 — 2026-11-21 09:00 (Sage)"
EVIDENCE = "ev:verification@0123abcd4567"
REVIEWED = (CARD + "\n---\n\n" + RL_HEAD + "\n\n"
            '<!-- bench:review {"agent": "hazel-rl1", "kind": "relevance", "reviewer": "Hazel", "v": 1} -->\n\n'
            "RELEVANCE REVIEW: Needs rewrite\n\nThe scope is said twice; say it once.\n\n"
            + PR_HEAD + "\n\n"
            '<!-- bench:review {"agent": "sage-pr1", "evidence": ["' + EVIDENCE + '"], "kind": "pr", '
            '"reviewer": "Sage", "v": 1} -->\n\n'
            "PR REVIEW: APPROVE\n\nThe export writes one scope.\n")


def rl_id() -> str:
    import hashlib
    return "rl-" + hashlib.sha256(RL_HEAD.strip().encode("utf-8")).hexdigest()[:10]


def rv_id() -> str:
    import hashlib
    return "rv-" + hashlib.sha256(PR_HEAD.strip().encode("utf-8")).hexdigest()[:10]


class ReviewRefinementAndEndedRuns(unittest.TestCase):
    """A relevance review's conversation in the panel keeps that review —
    its comments are linked to it and its refinement is asked from it —
    across a reload and a board restart; and runs that have ended, a PR
    review and a PR repair, are inspected from their real entry points
    (Sessions) with their report, review and evidence linked exactly."""

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "to-do" / FILE
        self.card.write_text(REVIEWED, encoding="utf-8")
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

    def tearDown(self):
        S["browser"].eval("(() => { if (window.__realFetch) window.fetch = window.__realFetch; return true; })()")

    def brief(self) -> str:
        return self.card.read_text(encoding="utf-8").split("\n---\n")[0]

    def discussion(self) -> dict:
        code, body = S["backend"].get(f"/api/card/discussion?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open_review_panel(self, b):
        b.goto("/cards/46?tab=reviews")
        sel = f".rvcompose[data-review='{rl_id()}'] .ap-open"
        if not b.wait(f"!!document.querySelector(\"{sel}\")", 15):
            raise bv.CheckFailed("the relevance review offered no Open in conversation panel")
        b.eval(f"document.querySelector(\"{sel}\").click()")
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .ap-refine[data-preview=\"{rl_id()}\"]') "
                      "&& document.querySelector('#ap-commentbox')", 10):
            raise bv.CheckFailed("the panel did not open about the review: " + panel_text(b)[:300])

    def test_1_a_review_conversation_keeps_its_review_across_reload_and_restart(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/board")
        b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
        theme(b, "dark")
        b.eval("(() => { try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
        brief_before = self.brief()
        before_jobs = jobs()
        self.open_review_panel(b)
        b.wait("/Hazel/.test(document.querySelector('#agentpanel').textContent)", 10)
        text = panel_text(b)
        self.assertIn("About relevance review", text)
        self.assertIn("Update card from this review", text)
        self.assertIn("Hazel", text, "the review is named as the card holds it")
        # — Comment: saved, linked to the review, nothing launched —
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'Keep the CSV point'); return true; })()")
        b.click("#agentpanel [data-pref='comment']")
        if not b.wait("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
                      ".some(r => r.innerText.includes('Keep the CSV point'))", 10):
            raise bv.CheckFailed("the review comment never appeared in the panel: " + panel_text(b)[:300])
        time.sleep(1)
        self.assertEqual(before_jobs, jobs(), "a comment launched nothing")
        mine = [e for e in self.discussion()["entries"] if e["kind"] == "comment"]
        self.assertEqual([e.get("review") for e in mine], [rl_id()], "the comment is linked to the review")
        # — Update card from this review: armed, the review goes with it —
        S["backend"].refine_reply.write_text(proposal(NEW_BRIEF), encoding="utf-8")
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'Apply this review'); return true; })()")
        b.click("#agentpanel [data-pref='refine']")
        if not b.wait("document.querySelector('#agentpanel [data-pref=\"refine\"]').classList.contains('armed')", 5):
            raise bv.CheckFailed("Update card from this review did not arm on the first click")
        b.click("#agentpanel [data-pref='refine']")
        row = ("[...document.querySelectorAll('#agentpanel .dentry.comment')]"
               ".find(r => r.innerText.includes('Apply this review'))")
        if not b.wait(f"(() => {{ const r = {row}; return !!r && !!r.querySelector('.rdiff'); }})()", 30):
            raise bv.CheckFailed("no proposal from the review arrived in the panel: " + panel_text(b)[:400])
        # (this board's job store is per card file, so the Refinement
        # case's jobs for the same file name are there too: newest first)
        d = self.discussion()
        self.assertEqual(d["jobs"][0].get("review"), rl_id(), "the refinement was asked from the review")
        self.assertEqual(len([j for j in d["jobs"] if j.get("review") == rl_id()]), 1)
        self.assertEqual(self.brief(), brief_before, "a proposal changes nothing")
        self.assertIn("Asked from relevance review", panel_text(b))
        shot("agent-refine-review-1440-dark")
        b.eval(f"{row}.querySelector('button[data-act=\"discard\"]').click()")
        if not b.wait(f"(() => {{ const r = {row}; return !!r && r.innerText.includes('discarded'); }})()", 15):
            raise bv.CheckFailed("Discard was not recorded: " + panel_text(b)[:400])
        self.assertEqual(self.brief(), brief_before, "Discard preserved the brief")
        self.assertIn("RELEVANCE REVIEW: Needs rewrite\n\nThe scope is said twice; say it once.",
                      self.card.read_text(encoding="utf-8"), "the filed review is never changed")
        # — unsent words, a reload and a board restart —
        b.eval("(() => { const f = document.querySelector('#ap-commentbox'); f.focus(); "
               "document.execCommand('insertText', false, 'unsent review words'); return true; })()")
        time.sleep(0.5)
        jobs_before_restart = jobs()
        S["backend"].stop()
        S["backend"].start()
        b.goto("/cards/46?tab=reviews")
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .ap-refine[data-preview=\"{rl_id()}\"]') "
                      "&& document.querySelector('#ap-commentbox')", 20):
            raise bv.CheckFailed("after a restart the review conversation did not come back: " + panel_text(b)[:300])
        self.assertEqual("unsent review words", b.eval("document.querySelector('#ap-commentbox').value"))
        b.wait("[...document.querySelectorAll('#agentpanel .dentry.comment')].length >= 2", 10)
        self.assertIn("Keep the CSV point", panel_text(b), "its history is the card's")
        self.assertEqual(jobs_before_restart, jobs(), "a restart and a reload ran nothing again")
        self.assertEqual(len([j for j in self.discussion()["jobs"] if j.get("review") == rl_id()]), 1,
                         "one refinement from the review, never a duplicate")
        # the card-wide conversation is another slot: it has no review words
        b.goto("/cards/46?tab=brief")
        b.wait("!!document.querySelector('.discussion .ap-open')", 15)
        b.click(".discussion .ap-open")
        b.wait(f"{OPEN} && document.querySelector('#ap-commentbox') && "
               "!document.querySelector('#agentpanel .ap-refine[data-preview]')", 10)
        self.assertNotEqual("unsent review words", b.eval("document.querySelector('#ap-commentbox').value"))
        b.eval("document.querySelector('#ap-commentbox').focus()")
        b.key("Escape")
        self.assertTrue(b.wait(f"{PANEL}.hidden", 5), "Escape closed the panel")

    def install_run(self, b, fx, sid):
        b.eval(cp.INSTALL % (json.dumps(fx), json.dumps(sid), json.dumps(sid)))

    def inspect_from_sessions(self, b, sid):
        # an in-page navigation, as a link makes: a reload would drop the
        # fixture laid over the page's fetch
        b.eval(f"(() => {{ history.pushState({{}}, '', {json.dumps('/sessions/' + sid)}); "
               "dispatchEvent(new PopStateEvent('popstate')); return true; })()")
        if not b.wait("!!document.querySelector('button.ap-open[data-agent-ask=\"run\"]')", 15):
            raise bv.CheckFailed("Sessions offered no Inspect in panel for " + sid)
        b.eval("document.querySelector('button.ap-open[data-agent-ask=\"run\"]').click()")
        if not b.wait(f"{OPEN} && document.querySelector('#agentpanel .ap-ident')", 10):
            raise bv.CheckFailed("Inspect in panel from Sessions opened no run: " + panel_text(b)[:300])

    def test_2_ended_review_and_repair_runs_from_sessions(self):
        b = S["browser"]
        b.viewport(1440, 900)
        b.goto("/board")
        b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
        now = time.time()
        # an ended PR review run on this card: its report is matched by the
        # run identity bench recorded, its review and evidence linked
        fx = cp.fixture(now, active=False, status="done")
        sid = "sess-case206-review"
        fx["agent"].update(id="sage-pr1", task=FILE, session=sid, name="Sage", mode="review",
                           review={"head": "0123abcd4567"}, failure=None, rc=0)
        fx["session"].update(id=sid, agentId="sage-pr1", agentName="Sage", task=FILE, label="Sage · #46")
        for e in fx["events"]:
            e["session"] = sid
        fx["progress"].update(agent="sage-pr1", task=FILE, intent="review", outcome="finished")
        self.install_run(b, fx, sid)
        self.inspect_from_sessions(b, sid)
        if not b.wait("/Matched by the run identity/.test(document.querySelector('#agentpanel').textContent)", 15):
            raise bv.CheckFailed("the review run's filed report was not matched: " + panel_text(b)[:500])
        text = panel_text(b)
        self.assertIn("PR review run", text)
        self.assertIn("No follow-up here", text)
        def href(sel):
            return b.eval(f"(() => {{ const a = document.querySelector({json.dumps(sel)}); "
                          "return a ? a.getAttribute('href') : null; })()")
        self.assertEqual(f"/cards/46?tab=reviews&review={rv_id()}", href("#agentpanel a[href*='tab=reviews']"))
        self.assertEqual(f"/brain/r/46/{rv_id()}", href("#agentpanel a[href^='/brain/r/']"))
        self.assertEqual("/api/evidence/log?id=ev%3Averification%400123abcd4567",
                         href("#agentpanel a[href^='/api/evidence/log']"), "the evidence is its exact record")
        self.assertEqual(f"/sessions/{sid}?card=46", href("#agentpanel a[href^='/sessions/']"))
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea')"), "no composer")
        shot("agent-run-review-ended-1440-dark")
        close(b)
        # an ended PR repair run: its purpose from its recorded intent, its
        # named evidence linked, and no report claimed for it
        fx2 = cp.fixture(now, active=False, status="done", current=dict(cp.MILESTONE, at=now - 300, refs=[
            {"kind": "evidence", "id": EVIDENCE, "found": False}]))
        sid2 = "sess-case206-repair"
        fx2["agent"].update(id="juno-fix1", session=sid2, name="Juno", mode="work",
                            repair={"kind": "card"}, failure=None, rc=0)
        fx2["session"].update(id=sid2, agentId="juno-fix1", agentName="Juno", label="Juno · #03")
        for e in fx2["events"]:
            e["session"] = sid2
        fx2["progress"].update(agent="juno-fix1", intent="act-pr", outcome="finished")
        b.eval("(() => { if (window.__realFetch) window.fetch = window.__realFetch; return true; })()")
        self.install_run(b, fx2, sid2)
        self.inspect_from_sessions(b, sid2)
        b.wait("/PR repair run/.test(document.querySelector('#agentpanel').textContent)", 10)
        text = panel_text(b)
        self.assertIn("PR repair run", text)
        self.assertIn("not found on this board", text, "a missing record is said, never a pass")
        self.assertEqual("/api/evidence/log?id=ev%3Averification%400123abcd4567",
                         href("#agentpanel a[href^='/api/evidence/log']"))
        self.assertFalse(b.eval("!!document.querySelector('#agentpanel textarea')"), "no composer")
        # narrow and light, the theme switched before the panel covers the header
        close(b)
        b.viewport(390, 844)
        theme(b, "light")
        self.inspect_from_sessions(b, sid2)
        self.assertTrue(b.eval("document.body.classList.contains('agent-full')"))
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391)
        shot("agent-run-repair-ended-390-light")
        close(b)
        b.viewport(1440, 900)
        b.goto("/board")
        b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15)
        theme(b, "dark")


if __name__ == "__main__":
    unittest.main()
