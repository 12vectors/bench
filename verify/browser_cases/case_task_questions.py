"""Asking the orchestrator (tasks 181, 205), driven in a browser against
the disposable backend's real routes and the Claude adapter's real `run`.

Since task 205 a question is asked in the shared right-hand conversation
panel, beside whatever page is shown, with its target inferred from where
it was asked — no Board/Phase/Card form first.

The backend's vendor CLI is a stub: without `ask_answers` every reasoning
run fails as "no model here"; with it, a question is answered from the
facts bench pinned for it, and `ask_hold` keeps the run in flight. The
case adds a card of its own, #43 in To Do, and a phase, #44, and removes
everything it added afterwards:

- opening the Ask section, opening the panel, reloading and reading a
  conversation launch nothing; a reload gives the open panel back;
- a card's Overview offers **Ask about this card**, which opens the panel
  beside the card — the address stays the card's — about that card;
- a question whose run fails says so, and **Retry** asks again; a run in
  flight shows its observed state and can be cancelled; an answer shows
  what is happening now, why, what happens next and who can unblock it,
  the recorded facts with evidence that opens, the gaps, and suggestions
  that are links, never actions; one submission launches one run;
- a follow-up keeps the conversation, and the old address
  `/orchestrator/ask?card=43` opens it in the panel;
- a board-wide question from the Board's bar is kept in a shared
  work-state conversation file;
- a phase chosen with **Change** opens a conversation of its own and
  keeps the card's; an answer about a phase that changed reads as history;
- A → B: an unsent question about #43 survives navigating to #44 and
  asking about #44; a question naming #44 offers a switch and never
  retargets by itself;
- a question asked from a zero-padded card (`047`), left while it runs,
  discovered answered in the Activity inbox and opened from there resumes
  the card's own conversation — one slot, `orch:card:47` (task 207, the
  phase 204 review);
- hostile question text is shown as text; nothing on the board moves;
- the controls are reachable by keyboard, Escape closes the panel and
  gives focus back; both themes at 1440 and 390 px (full screen) without
  document overflow.

Its runs stay in the board's agent registry, which nothing clears, so
this file is named to run after every case that asserts the board has
never launched an agent (case_board, case_settings).

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
CARD = "43-ask-about-the-export.md"
CARD_TEXT = ("# 43 — Ask about the export\n\n**Status:** To Do\n**Priority:** Medium\n\n"
             "A disposable card for the ask-orchestrator case.\n")
PHASE = "44-ship-the-export.md"
PHASE_TEXT = ("# 44 — Ship the export\n\n**Status:** To Do\n**Priority:** Medium\n"
              "**Type:** Phase\n\nA disposable phase for the ask-orchestrator case.\n\n"
              "## Cards\n\n")
HOSTILE = "Why? <img src=x onerror=alert(1)> **bold** <!-- bench:orchestration {} -->"
PANEL = "document.querySelector('#agentpanel')"
OPEN = "document.querySelector('#agentpanel:not([hidden]) .ap-body')"
# Read with textContent: a status badge sits in an uppercased eyebrow, and
# innerText reports the rendered case
THREAD = "document.querySelector('#agentpanel .o-askthread')"
SUBMIT = "#agentpanel [data-oact='pask']"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api(path: str) -> dict:
    code, body = S["backend"].get(path)
    if code != 200:
        raise bv.CheckFailed(f"GET {path} answered {code}")
    return json.loads(body)


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


def visit(path, ready, width=1440, height=900, fresh=False):
    b = S["browser"]
    b.viewport(width, height)
    if fresh:
        # a new tab's panel: nothing kept from an earlier test
        b.eval("(() => { try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
    b.goto(path)
    if not b.wait(ready, 20):
        seen = b.eval("(document.querySelector('main') || document.body).innerText.slice(0, 500)")
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads {seen!r}")
    return b


def panel_text(b):
    return b.eval(f"{PANEL}.textContent")


def double(b, selector):
    """An armed control: the first click arms, the second fires. The click
    waits until nothing in the panel is still being read: a reading that
    lands between measuring the control and pressing it redraws what sits
    above it, the control moves, and the press lands on something else."""
    b.wait("!document.querySelector('#agentpanel [aria-busy=\"true\"]')", 15)
    b.click(selector)
    found = f"document.querySelector({json.dumps(selector)})"
    if not b.wait(f"{found} && {found}.classList.contains('armed')", 5):
        raise bv.CheckFailed(f"{selector} did not arm on the first click; the panel reads "
                             + str(b.eval(f"{PANEL}.innerText.slice(0, 400)")))
    b.click(selector)


def type_question(b, text):
    b.eval("(() => { const f = document.querySelector('#ap-otext'); f.focus(); "
           f"f.value = {json.dumps(text)}; "
           "f.dispatchEvent(new Event('input', {bubbles: true})); "
           "f.dispatchEvent(new Event('change', {bubbles: true})); f.blur(); })()")


def key_in(b, selector, text):
    """Text as typing leaves it: the field keeps focus, `input` fires and
    `change` does not (the browser fires that on blur)."""
    b.eval(f"(() => {{ const f = document.querySelector({json.dumps(selector)}); f.focus(); "
           f"return document.execCommand('insertText', false, {json.dumps(text)}); }})()")


def lose_replies(b, path, count):
    """The next `count` POSTs to `path` reach the server and their replies
    are lost on the way back, as a dropped connection loses them."""
    b.eval("(() => { const real = window.fetch; window.__lost = %d; "
           "window.fetch = async (url, opts) => { const r = await real(url, opts); "
           "if (window.__lost > 0 && String(url) === %s && opts && opts.method === 'POST') "
           "{ window.__lost--; throw new TypeError('the reply was lost'); } return r; }; "
           "return true; })()" % (count, json.dumps(path)))


def reasoning_jobs() -> list:
    local = S["backend"].tool / "manager" / "local" / "state" / "reasoning" / "jobs"
    return sorted(p.name for p in local.iterdir()) if local.is_dir() else []


def server_replies(card: str) -> list:
    """What the server holds for a card's conversations, for a failure's
    message: each question's replies and each run's state."""
    out = []
    for conv in api(f"/api/ask?card={card}")["conversations"]:
        thread = api(f"/api/ask/thread?conversation={conv['id']}")["conversation"]
        for x in thread["exchanges"]:
            out.append({"replies": [(r.get("status"), r.get("diagnostics"), (r.get("summary") or "")[:200])
                                    for r in x["replies"]],
                        "jobs": [(j.get("status"), j.get("error")) for j in x.get("jobs") or []]})
    return out


def stages() -> dict:
    return {t["file"]: s["slug"] if "slug" in s else s.get("stage")
            for s in api("/api/state")["board"]["stages"] for t in s["tasks"]}


class Ask(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        (backend.work / "tasks" / "to-do" / CARD).write_text(CARD_TEXT, encoding="utf-8")
        (backend.work / "tasks" / "to-do" / PHASE).write_text(PHASE_TEXT, encoding="utf-8")
        deadline = time.time() + 15
        while time.time() < deadline:
            listed = stages()
            if CARD in listed and PHASE in listed:
                break
            time.sleep(0.3)
        else:
            raise bv.SetupError("the backend never listed the case's card")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        backend.ask_answers.unlink(missing_ok=True)
        backend.ask_hold.unlink(missing_ok=True)
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            (backend.work / "tasks" / stage / CARD).unlink(missing_ok=True)
            (backend.work / "tasks" / stage / PHASE).unlink(missing_ok=True)
        shutil.rmtree(backend.work / "orchestration" / "conversations", ignore_errors=True)
        try:
            # close the panel first, so nothing this case opened is drawn
            # over the next case's page, then forget what this tab kept
            S["browser"].eval("(() => { document.querySelector('#agentpanel [data-pact=\"close\"]')?.click(); "
                              "try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
        except (bv.CheckFailed, bv.SetupError):
            pass
        time.sleep(2.5)

    def test_1_opening_and_reloading_launch_nothing(self):
        before = reasoning_jobs()
        b = visit("/orchestrator/ask", "document.querySelector('#oshell [data-o=\"ask-here\"]')"
                  " && !document.querySelector('#oshell [aria-busy]')", fresh=True)
        theme(b, "dark")
        text = b.eval("document.querySelector('#oshell').innerText")
        self.assertIn("Ask orchestrator", text)
        self.assertIn("one read-only reasoning run", text)
        self.assertFalse(b.eval("!!document.querySelector('#oshell input[type=radio]')"),
                         "no Board/Phase/Card form before asking")
        self.assertTrue(b.eval(f"{PANEL}.hidden"), "the panel waits to be asked for")
        b.click("#oshell [data-o='ask-here']")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 10):
            raise bv.CheckFailed("Ask about the whole board opened no panel")
        self.assertEqual("ap-otext", b.eval("document.activeElement && document.activeElement.id"),
                         "the composer takes focus")
        self.assertIn("the whole board", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        self.assertIn("does not change it", panel_text(b))
        shot("ask-panel-open-1440-dark")
        b.goto("/orchestrator/ask")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 20):
            raise bv.CheckFailed("a reload did not give the open conversation back")
        time.sleep(1)
        self.assertEqual(before, reasoning_jobs(), "opening the panel or reloading launched a run")

    def test_2_a_card_offers_ask_and_a_failed_run_says_so_then_retries(self):
        b = visit("/cards/43", "document.querySelector('#drawer a.o-asklink')", fresh=True)
        self.assertEqual("/orchestrator/ask?card=43",
                         b.eval("document.querySelector('#drawer a.o-asklink').getAttribute('href')"))
        b.click("#drawer a.o-asklink")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 15):
            raise bv.CheckFailed("Ask about this card did not open the panel")
        self.assertEqual("/cards/43", b.eval("location.pathname"), "the card stays the page")
        self.assertTrue(b.eval("document.querySelector('#drawer').classList.contains('open')"),
                        "the card stays in view beside the conversation")
        self.assertIn("Card #43", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        shot("ask-panel-beside-card-1440-dark")
        before, jobs = stages(), reasoning_jobs()
        type_question(b, HOSTILE)
        double(b, SUBMIT)
        if not b.wait(f"{THREAD} && /Failed — no answer/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("a failed run was not shown as failed: " + str(b.eval(f"{PANEL}.innerText.slice(0, 600)")))
        self.assertEqual(len(reasoning_jobs()), len(jobs) + 1, "one submission, one run")
        # the hostile text is text, never markup
        self.assertEqual(0, b.eval("document.querySelectorAll('#agentpanel .o-askq img').length"))
        self.assertIn("<img src=x", b.eval("document.querySelector('#agentpanel .o-askq').innerText"))
        self.assertEqual(before, stages(), "asking moved a card")
        body = (S["backend"].work / "tasks" / "to-do" / CARD).read_text(encoding="utf-8")
        self.assertIn('"kind": "ask"', body, "the question is kept on the card")
        shot("ask-failed-1440-dark")
        # now the stub answers, held long enough to see the run in flight
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        S["backend"].ask_hold.write_text("1", encoding="utf-8")
        double(b, "#agentpanel [data-oact='askRetry']")
        if not b.wait("document.querySelector('#agentpanel .o-askactive')", 20):
            raise bv.CheckFailed("the run in flight was not shown")
        self.assertIn("read-only reasoning run", b.eval("document.querySelector('#agentpanel .o-askactive').textContent"))
        shot("ask-running-1440-dark")
        S["backend"].ask_hold.unlink()
        if not b.wait(f"/Answered/.test({THREAD}.textContent) && !document.querySelector('#agentpanel .o-askactive')", 60):
            raise bv.CheckFailed("the answer never arrived; the server holds "
                                 + json.dumps(server_replies("43"))[:1500] + "; the page reads "
                                 + str(b.eval(f"{THREAD} && {THREAD}.textContent.slice(0, 600)")))
        text = b.eval(f"{THREAD}.textContent")
        for words in ("What is happening now", "Who can unblock it", "Recorded facts",
                      "What the records do not say", "not actions", "agent-reported interpretation"):
            self.assertIn(words, text)
        # the evidence opens: bench's readings, as a read-only document
        href = b.eval("document.querySelector('#agentpanel .o-askrail a').getAttribute('href')")
        self.assertTrue(href.startswith("/api/ask/facts?operation="), href)
        facts = api(href)["facts"]
        self.assertEqual(facts["need"], "ask")
        # a suggestion is a link to an existing control, never a button
        self.assertEqual("/orchestrator/agreement?card=43",
                         b.eval("document.querySelector('#agentpanel .o-asksugg a').getAttribute('href')"))
        self.assertEqual(0, b.eval("document.querySelectorAll('#agentpanel .o-asksugg button').length"))
        # model and accounting are one fold away, not the first thing read
        self.assertFalse(b.eval("document.querySelector('#agentpanel .ap-tech').open"))
        shot("ask-answered-1440-dark")

    def test_3_a_follow_up_keeps_the_conversation(self):
        # the task-181 address is a way into the panel, about its card
        b = visit("/orchestrator/ask?card=43", f"{THREAD}", fresh=True)
        self.assertIn("Ask a follow-up", b.eval("document.querySelector('#agentpanel .o-askcomposer label').textContent"))
        type_question(b, "And what would unblock it?")
        double(b, SUBMIT)
        if not b.wait("document.querySelectorAll('#agentpanel .o-exchange').length === 2 && "
                      "!document.querySelector('#agentpanel .o-askactive') && "
                      "document.querySelectorAll('#agentpanel .o-reply').length >= 3", 40):
            raise bv.CheckFailed("the follow-up did not land in the same conversation")
        convs = api("/api/ask?card=43")["conversations"]
        self.assertEqual(1, len(convs))
        self.assertEqual(2, convs[0]["count"])

    def test_4_a_board_question_and_a_cancelled_one(self):
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        b = visit("/board", "document.querySelector('#bnact .bn-ask')", fresh=True)
        b.click("#bnact .bn-ask")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 10):
            raise bv.CheckFailed("the Board's Ask orchestrator opened no panel")
        self.assertEqual("/board", b.eval("location.pathname"))
        type_question(b, "What is consuming capacity?")
        double(b, SUBMIT)
        if not b.wait(f"{THREAD} && /Answered/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("the board question was not answered")
        self.assertIn("board’s shared conversation file", b.eval(f"{THREAD}.textContent"))
        files = list((S["backend"].work / "orchestration" / "conversations").glob("k-*.md"))
        self.assertEqual(1, len(files), "one shared work-state conversation file")
        shot("ask-board-beside-board-1440-dark")
        # a held run is cancelled from the panel; closing would not stop it
        S["backend"].ask_hold.write_text("1", encoding="utf-8")
        type_question(b, "Which card waited longest?")
        double(b, SUBMIT)
        if not b.wait("document.querySelector('#agentpanel [data-oact=\"askCancel\"]')", 20):
            raise bv.CheckFailed("no cancel control for the run in flight")
        double(b, "#agentpanel [data-oact='askCancel']")
        if not b.wait(f"/Cancelled — no answer/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("the cancelled run was not shown as cancelled")
        S["backend"].ask_hold.unlink(missing_ok=True)

    def test_4b_a_phase_chosen_with_change_and_a_stale_answer(self):
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        b = visit("/cards/43", "document.querySelector('#drawer a.o-asklink')", fresh=True)
        b.click("#drawer a.o-asklink")
        b.wait(f"{OPEN} && document.querySelector('#agentpanel [data-pact=\"change\"]')", 15)
        b.click("#agentpanel [data-pact='change']")
        if not b.wait("document.querySelector('#ap-target')", 5):
            raise bv.CheckFailed("Change offered no targets")
        b.eval("(() => { const s = document.querySelector('#ap-target'); "
               "s.value = JSON.stringify({kind: 'phase', card: '44'}); "
               "s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        if not b.wait("/Phase #44/.test(document.querySelector('#agentpanel .ap-chip').textContent)", 5):
            raise bv.CheckFailed("the panel does not name phase #44: "
                                 + str(b.eval("document.querySelector('#agentpanel .ap-about').textContent")))
        type_question(b, "In what order will this phase deliver?")
        double(b, SUBMIT)
        if not b.wait(f"{THREAD} && /Answered/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("the phase question was not answered; the server holds "
                                 + json.dumps(server_replies("44"))[:1500])
        body = (S["backend"].work / "tasks" / "to-do" / PHASE).read_text(encoding="utf-8")
        self.assertIn('"kind": "ask"', body, "the phase's question is kept on the phase card")
        self.assertEqual(1, len(api("/api/ask?card=44")["conversations"]))
        self.assertEqual(1, len(api("/api/ask?card=43")["conversations"]), "the card's conversation is kept")
        state = b.eval("panelState()")
        self.assertIn("orch:card:43", state["slots"], "the old target's conversation stays in the panel")
        # the phase card changes after the answer: the answer is history
        with (S["backend"].work / "tasks" / "to-do" / PHASE).open("a", encoding="utf-8") as f:
            f.write("\nA person edited the brief after the answer.\n")
        b = visit("/orchestrator/ask?card=44", f"{THREAD}")
        if not b.wait(f"{THREAD} && /no longer current/.test({THREAD}.textContent)", 20):
            raise bv.CheckFailed("an answer about a phase that changed since was not "
                                 "said to be no longer current: "
                                 + str(b.eval(f"{THREAD} && {THREAD}.textContent.slice(0, 600)")))
        shot("ask-phase-stale-1440-dark")

    def test_4c_a_draft_about_a_stays_when_b_is_asked_about(self):
        before = reasoning_jobs()
        b = visit("/cards/43", "document.querySelector('#drawer a.o-asklink')", fresh=True)
        b.click("#drawer a.o-asklink")
        b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 15)
        type_question(b, "Unsent: does phase #44 wait on this?")
        # a reference to another card is offered, and nothing switches
        if not b.wait("document.querySelector('#agentpanel [data-o=\"pask-ref-44\"]')", 5):
            raise bv.CheckFailed("a question naming #44 offered no switch")
        self.assertIn("Card #43", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        # navigate to B while A has an unsent question, and ask about B
        b.goto("/cards/44")
        b.wait("document.querySelector('#drawer a.o-asklink')", 15)
        self.assertIn("Card #43", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"),
                      "navigating did not retarget the conversation")
        b.click("#drawer a.o-asklink")
        if not b.wait("/Phase #44/.test(document.querySelector('#agentpanel .ap-chip').textContent)", 10):
            raise bv.CheckFailed("Ask about this phase did not select #44")
        self.assertEqual("", b.eval("document.querySelector('#ap-otext').value"), "B's composer is B's own")
        # back to A through the panel's own list: its text is all there
        b.eval("(() => { const s = document.querySelector('#agentpanel [data-pact=\"switch\"]'); "
               "s.value = 'orch:card:43'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        if not b.wait("/Card #43/.test(document.querySelector('#agentpanel .ap-chip').textContent)", 5):
            raise bv.CheckFailed("switching back to #43 did not")
        self.assertEqual("Unsent: does phase #44 wait on this?", b.eval("document.querySelector('#ap-otext').value"))
        shot("ask-a-kept-after-b-1440-dark")
        self.assertEqual(before, reasoning_jobs(), "navigating and switching launched a run")

    def test_4d_typed_text_survives_a_reload_and_a_lost_reply_launches_once(self):
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        b = visit("/cards/43", "document.querySelector('#drawer a.o-asklink')", fresh=True)
        b.click("#drawer a.o-asklink")
        b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 15)
        # typed with the caret still in the composer: no change, no blur
        key_in(b, "#ap-otext", "Lost reply: why is this card waiting?")
        self.assertEqual("ap-otext", b.eval("document.activeElement && document.activeElement.id"))
        b.goto("/cards/43")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')?.value === "
                      "'Lost reply: why is this card waiting?'", 20):
            raise bv.CheckFailed("text typed in the composer did not survive a reload")
        before = reasoning_jobs()
        exchanges = sum(c["count"] for c in api("/api/ask?card=43")["conversations"])
        # the server receives the question and both replies are lost: the
        # page's own resend, then the person's after a reload
        lose_replies(b, "/api/ask", 2)
        b.wait("!document.querySelector(\"#agentpanel [data-oact='pask']\")?.disabled", 15)
        double(b, SUBMIT)
        if not b.wait("window.__lost === 0", 20):
            raise bv.CheckFailed("the submission was not sent twice")
        time.sleep(1)
        self.assertEqual(len(reasoning_jobs()), len(before) + 1, "the page's own resend launched a second run")
        b.goto("/cards/43")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')?.value === "
                      "'Lost reply: why is this card waiting?'", 20):
            raise bv.CheckFailed("a question whose reply was lost was not kept to resend")
        b.wait("!document.querySelector(\"#agentpanel [data-oact='pask']\")?.disabled", 15)
        double(b, SUBMIT)
        if not b.wait(f"{THREAD} && /Lost reply: why is this card waiting/.test({THREAD}.textContent) && "
                      "document.querySelector('#ap-otext')?.value === ''", 40):
            raise bv.CheckFailed("the resent question was not answered from the record: "
                                 + str(b.eval(f"{PANEL}.innerText.slice(0, 600)")))
        time.sleep(1)
        self.assertEqual(len(reasoning_jobs()), len(before) + 1, "a resend after a lost reply launched another run")
        self.assertEqual(exchanges + 1, sum(c["count"] for c in api("/api/ask?card=43")["conversations"]),
                         "one question was recorded once")

    def test_4e_an_answer_found_in_the_activity_inbox_opens_its_own_conversation(self):
        # The phase 204 review: ask from a card, leave while the answer is
        # still coming, discover it in the Activity inbox and open it — the
        # very conversation the card's Ask opened. The card's file number is
        # zero-padded (`047`), the page names it `47`: one slot, not two.
        backend = S["backend"]
        padded = "047-ask-from-the-inbox.md"
        card = backend.work / "tasks" / "to-do" / padded
        card.write_text("# 047 — Ask from the inbox\n\n**Status:** To Do\n**Priority:** Low\n\n"
                        "A disposable card for the inbox journey.\n", encoding="utf-8")
        self.addCleanup(lambda: [(backend.work / "tasks" / s / padded).unlink(missing_ok=True)
                                 for s in ("backlog", "to-do", "in-progress", "review", "done", "archive")])
        deadline = time.time() + 15
        while time.time() < deadline and padded not in stages():
            time.sleep(0.3)
        backend.ask_answers.write_text("1", encoding="utf-8")
        backend.ask_hold.write_text("1", encoding="utf-8")
        self.addCleanup(backend.ask_hold.unlink, True)
        b = visit("/cards/47", "document.querySelector('#drawer a.o-asklink')", fresh=True)
        theme(b, "dark")
        b.click("#drawer a.o-asklink")
        if not b.wait(f"{OPEN} && document.querySelector('#ap-otext')", 15):
            raise bv.CheckFailed("Ask about this card did not open the panel")
        self.assertIn("#47", b.eval("document.querySelector('#agentpanel .ap-chip').textContent"))
        type_question(b, "Inbox journey: why is #47 waiting?")
        double(b, SUBMIT)
        if not b.wait("document.querySelector('#agentpanel .o-askactive')", 20):
            raise bv.CheckFailed("the run in flight was not shown")
        # — leave while it runs: close the panel and go to the Board —
        b.click("#agentpanel [data-pact='close']")
        b.goto("/board")
        b.wait("!!document.querySelector('#attnbtn') && attentionState().loaded", 15)
        backend.ask_hold.unlink()

        def ours():
            code, raw = backend.get("/api/attention")
            items = json.loads(raw)["items"] if code == 200 else []
            return next((i for i in items if i["id"].startswith("ask:") and "#47" in i["title"]), None)
        deadline, item = time.time() + 60, None
        while time.time() < deadline:
            item = ours()
            if item and item["state"] == "answer-ready" and item["unread"]:
                break
            time.sleep(0.5)
        else:
            raise bv.CheckFailed(f"the answer never reached the inbox as unread: {item}")
        self.assertEqual({"kind": "card", "card": "47"}, item["open"]["target"],
                         "the inbox names the card as the page does")
        self.assertEqual("/board", b.eval("location.pathname"), "nothing navigated by itself")
        if not b.wait("document.querySelector('#attnbtn').classList.contains('has-unread')", 20):
            raise bv.CheckFailed("the Activity control never counted the answer")
        # — discover and resume —
        b.click("#attnbtn")
        row = f"document.querySelector('#attnpop [data-item={json.dumps(item['id'])}]')"
        if not b.wait(f"!document.querySelector('#attnpop').hidden && !!{row}", 10):
            raise bv.CheckFailed("the answer is not in the activity list")
        self.assertIn("nothing was executed", b.eval(f"{row}.innerText"))
        b.eval(f"{row}.querySelector('[data-attn=\"open\"]').click()")
        if not b.wait(f"{OPEN} && {THREAD} && /Answered/.test({THREAD}.textContent) && "
                      f"/Inbox journey/.test({THREAD}.textContent)", 20):
            raise bv.CheckFailed("Open did not resume the card's conversation: "
                                 + str(b.eval(f"{PANEL}.innerText.slice(0, 600)")))
        state = b.eval("panelState()")
        self.assertEqual("orch:card:47", state["active"])
        self.assertNotIn("orch:card:047", state["slots"], "the inbox opened a second conversation")
        shot("ask-resumed-from-activity-1440-dark")
        deadline = time.time() + 10
        while time.time() < deadline and ours()["unread"]:
            time.sleep(0.3)
        self.assertFalse(ours()["unread"], "Open marked that answer read")
        b.click("#agentpanel [data-pact='close']")

    def test_5_keyboard_themes_and_widths(self):
        before = reasoning_jobs()
        # Ask is enabled once the board has said what asking would launch
        b = visit("/orchestrator/ask?card=43", "document.querySelector('#ap-otext') && "
                  "document.querySelector(\"#agentpanel [data-oact='pask']\") && "
                  "!document.querySelector(\"#agentpanel [data-oact='pask']\").disabled", fresh=True)
        b.eval("document.querySelector('#ap-otext').focus()")
        b.key("Tab")
        focused = b.eval("document.activeElement && document.activeElement.textContent")
        self.assertIn("Ask", focused or "", "the Ask button follows the question by keyboard")
        # Escape closes the panel and gives focus back; nothing is stopped
        b.eval("document.querySelector('#ap-otext').focus()")
        b.key("Escape")
        if not b.wait(f"{PANEL}.hidden", 5):
            raise bv.CheckFailed("Escape did not close the panel")
        for which in ("light", "dark"):
            for w, h in ((1440, 900), (390, 780)):
                b = visit("/orchestrator/ask?card=43", "document.querySelector('#ap-otext')"
                          " && !document.querySelector('#agentpanel [aria-busy]')", w, h, fresh=True)
                theme(b, which)
                width = b.eval("document.documentElement.scrollWidth")
                self.assertLessEqual(width, w + 1, f"the panel overflows at {w}px {which}")
                if w == 390:
                    self.assertTrue(b.eval("document.body.classList.contains('agent-full')"),
                                    "a phone shows the conversation full screen")
                unnamed = b.eval("[...document.querySelectorAll('#agentpanel button, #agentpanel a, #agentpanel textarea, #agentpanel select, #agentpanel input')]"
                                 ".filter(x => !(x.getAttribute('aria-label') || x.textContent || x.labels?.[0]?.textContent || '').trim()).length")
                self.assertEqual(0, unnamed, "a control has no accessible name")
                shot(f"ask-{w}-{which}")
        # wider and full screen on a desktop, and back
        b = visit("/orchestrator/ask?card=43", "document.querySelector('#ap-otext')", 1440, 900)
        narrow = b.eval(f"{PANEL}.offsetWidth")
        b.click("#agentpanel [data-pact='size'][data-parg='wide']")
        b.wait(f"{PANEL}.offsetWidth > {narrow}", 5)
        self.assertGreater(b.eval(f"{PANEL}.offsetWidth"), narrow, "Widen widened it")
        b.click("#agentpanel [data-pact='size'][data-parg='full']")
        if not b.wait("document.body.classList.contains('agent-full')", 5):
            raise bv.CheckFailed("Full screen did not")
        shot("ask-full-1440-dark")
        b.click("#agentpanel [data-pact='size'][data-parg='side']")
        b.wait("!document.body.classList.contains('agent-full')", 5)
        self.assertEqual(before, reasoning_jobs(), "looking launched a run")


if __name__ == "__main__":
    unittest.main()
