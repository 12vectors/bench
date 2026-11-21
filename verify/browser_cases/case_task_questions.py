"""Asking the orchestrator (task 181), driven in a browser against the
disposable backend's real routes and the Claude adapter's real `run`.

The backend's vendor CLI is a stub: without `ask_answers` every reasoning
run fails as "no model here"; with it, a question is answered from the
facts bench pinned for it, and `ask_hold` keeps the run in flight. The
case adds one card of its own, #43 in To Do, and removes everything it
added afterwards:

- opening the Ask section, reloading it and reading a conversation
  launch nothing;
- a card's Overview offers **Ask about this card**, which opens the
  section with that card as the context;
- a question whose run fails says so, and **Retry** asks again; a run in
  flight shows its observed state and can be cancelled; an answer shows
  what is happening now, why, what happens next and who can unblock it,
  the recorded facts with evidence that opens, the gaps, and suggestions
  that are links, never actions;
- a follow-up keeps the conversation without restating the subject;
- a board-wide question is kept in a shared work-state conversation file;
- hostile question text is shown as text; nothing on the board moves;
- the controls are reachable by keyboard; both themes at 1440 and 390 px
  without document overflow.

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
HOSTILE = "Why? <img src=x onerror=alert(1)> **bold** <!-- bench:orchestration {} -->"


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


def visit(path, ready, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto(path)
    if not b.wait(ready, 20):
        seen = b.eval("(document.querySelector('#oshell') || document.body).innerText.slice(0, 500)")
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads {seen!r}")
    return b


def double(b, selector):
    """An armed control: the first click arms, the second fires. The click
    waits until nothing in the section is still being read: a reading that
    lands between measuring the control and pressing it redraws what sits
    above it, the control moves, and the press lands on something else."""
    b.wait("!document.querySelector('#oshell [aria-busy=\"true\"]')", 15)
    b.click(selector)
    found = f"document.querySelector({json.dumps(selector)})"
    if not b.wait(f"{found} && {found}.classList.contains('armed')", 5):
        raise bv.CheckFailed(f"{selector} did not arm on the first click")
    b.click(selector)


def type_question(b, text):
    b.eval("(() => { const f = document.querySelector('#o-asktext'); f.focus(); "
           f"f.value = {json.dumps(text)}; "
           "f.dispatchEvent(new Event('input', {bubbles: true})); f.blur(); })()")


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


# Read with textContent: a status badge sits in an uppercased eyebrow, and
# innerText reports the rendered case
THREAD = "document.querySelector('#oshell .o-askthread')"


class Ask(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        (backend.work / "tasks" / "to-do" / CARD).write_text(CARD_TEXT, encoding="utf-8")
        deadline = time.time() + 15
        while time.time() < deadline:
            if CARD in stages():
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
        shutil.rmtree(backend.work / "orchestration" / "conversations", ignore_errors=True)
        time.sleep(2.5)

    def test_1_opening_and_reloading_launch_nothing(self):
        before = reasoning_jobs()
        b = visit("/orchestrator/ask", "document.querySelector('#oshell .o-askcomposer') "
                  "&& !document.querySelector('#oshell [aria-busy]')")
        theme(b, "dark")
        text = b.eval("document.querySelector('#oshell').innerText")
        self.assertIn("Ask orchestrator", text)
        self.assertIn("one read-only reasoning run", text)
        self.assertIn("acts on nothing", text)
        b.goto("/orchestrator/ask")
        b.wait("document.querySelector('#oshell .o-askcomposer')", 20)
        time.sleep(1)
        self.assertEqual(before, reasoning_jobs(), "opening the section launched a run")

    def test_2_a_card_offers_ask_and_a_failed_run_says_so_then_retries(self):
        b = visit("/cards/43", "document.querySelector('#drawer a.o-asklink')")
        self.assertEqual("/orchestrator/ask?card=43",
                         b.eval("document.querySelector('#drawer a.o-asklink').getAttribute('href')"))
        b.click("#drawer a.o-asklink")
        if not b.wait("location.pathname === '/orchestrator/ask' && location.search === '?card=43'"
                      " && document.querySelector('#o-asktext')", 15):
            raise bv.CheckFailed("Ask about this card did not open the Ask section")
        # the label's own text, not its uppercased rendering
        label = b.eval("document.querySelector('.o-askcomposer label').textContent")
        self.assertIn("Card #43", label)
        before = stages()
        type_question(b, HOSTILE)
        double(b, "#oshell [data-o='ask-submit']")
        if not b.wait(f"{THREAD} && /Failed — no answer/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("a failed run was not shown as failed: "
                                 + b.eval("document.querySelector('#oshell').innerText.slice(0, 600)"))
        # the hostile text is text, never markup
        self.assertEqual(0, b.eval("document.querySelectorAll('#oshell .o-askq img').length"))
        self.assertIn("<img src=x", b.eval("document.querySelector('#oshell .o-askq').innerText"))
        self.assertEqual(before, stages(), "asking moved a card")
        body = (S["backend"].work / "tasks" / "to-do" / CARD).read_text(encoding="utf-8")
        self.assertIn('"kind": "ask"', body, "the question is kept on the card")
        shot("ask-failed-1440-dark")
        # now the stub answers, held long enough to see the run in flight
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        S["backend"].ask_hold.write_text("1", encoding="utf-8")
        double(b, "#oshell [data-oact='askRetry']")
        if not b.wait("document.querySelector('#oshell .o-askactive')", 20):
            raise bv.CheckFailed("the run in flight was not shown")
        self.assertIn("read-only reasoning run", b.eval("document.querySelector('.o-askactive').textContent"))
        shot("ask-running-1440-dark")
        S["backend"].ask_hold.unlink()
        if not b.wait(f"/Answered/.test({THREAD}.textContent) && !document.querySelector('.o-askactive')", 60):
            raise bv.CheckFailed("the answer never arrived; the server holds "
                                 + json.dumps(server_replies("43"))[:1500] + "; the page reads "
                                 + str(b.eval(f"{THREAD} && {THREAD}.textContent.slice(0, 600)")))
        text = b.eval(f"{THREAD}.textContent")
        for words in ("What is happening now", "Who can unblock it", "Recorded facts",
                      "What the records do not say", "not actions", "agent-reported interpretation"):
            self.assertIn(words, text)
        # the evidence opens: bench's readings, as a read-only document
        href = b.eval("document.querySelector('.o-askrail a').getAttribute('href')")
        self.assertTrue(href.startswith("/api/ask/facts?operation="), href)
        facts = api(href)["facts"]
        self.assertEqual(facts["need"], "ask")
        # a suggestion is a link to an existing control, never a button
        self.assertEqual("/orchestrator/agreement?card=43",
                         b.eval("document.querySelector('.o-asksugg a').getAttribute('href')"))
        self.assertEqual(0, b.eval("document.querySelectorAll('.o-asksugg button').length"))
        shot("ask-answered-1440-dark")

    def test_3_a_follow_up_keeps_the_conversation(self):
        b = visit("/orchestrator/ask?card=43", "document.querySelector('#oshell .o-askconv')")
        b.click("#oshell .o-askconv")
        if not b.wait(f"{THREAD}", 15):
            raise bv.CheckFailed("the conversation did not open")
        self.assertIn("Ask a follow-up", b.eval("document.querySelector('.o-askcomposer label').textContent"))
        type_question(b, "And what would unblock it?")
        double(b, "#oshell [data-o='ask-submit']")
        if not b.wait(f"document.querySelectorAll('#oshell .o-exchange').length === 2 && "
                      f"!document.querySelector('.o-askactive') && "
                      f"document.querySelectorAll('#oshell .o-reply').length >= 3", 40):
            raise bv.CheckFailed("the follow-up did not land in the same conversation")
        convs = api("/api/ask?card=43")["conversations"]
        self.assertEqual(1, len(convs))
        self.assertEqual(2, convs[0]["count"])

    def test_4_a_board_question_and_a_cancelled_one(self):
        S["backend"].ask_answers.write_text("1", encoding="utf-8")
        b = visit("/orchestrator/ask", "document.querySelector('#oshell [data-o=\"askkind-board\"]')")
        b.click("#oshell [data-o='askkind-board']")
        b.wait("document.querySelector('[data-o=\"askkind-board\"]').checked", 5)
        type_question(b, "What is consuming capacity?")
        double(b, "#oshell [data-o='ask-submit']")
        if not b.wait(f"{THREAD} && /Answered/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("the board question was not answered")
        self.assertIn("board’s shared conversation file", b.eval(f"{THREAD}.textContent"))
        files = list((S["backend"].work / "orchestration" / "conversations").glob("k-*.md"))
        self.assertEqual(1, len(files), "one shared work-state conversation file")
        # a held run is cancelled from the page
        S["backend"].ask_hold.write_text("1", encoding="utf-8")
        type_question(b, "Which card waited longest?")
        double(b, "#oshell [data-o='ask-submit']")
        if not b.wait("document.querySelector('#oshell [data-oact=\"askCancel\"]')", 20):
            raise bv.CheckFailed("no cancel control for the run in flight")
        double(b, "#oshell [data-oact='askCancel']")
        if not b.wait(f"/Cancelled — no answer/.test({THREAD}.textContent)", 40):
            raise bv.CheckFailed("the cancelled run was not shown as cancelled")
        S["backend"].ask_hold.unlink(missing_ok=True)

    def test_5_keyboard_themes_and_widths(self):
        before = reasoning_jobs()
        b = visit("/orchestrator/ask?card=43", "document.querySelector('#o-asktext')")
        b.eval("document.querySelector('#o-asktext').focus()")
        b.key("Tab")
        focused = b.eval("document.activeElement && document.activeElement.textContent")
        self.assertIn("Ask", focused or "", "the Ask button follows the question by keyboard")
        for which in ("light", "dark"):
            for w, h in ((1440, 900), (390, 780)):
                b = visit("/orchestrator/ask?card=43", "document.querySelector('#oshell .o-askcomposer')"
                          " && !document.querySelector('#oshell [aria-busy]')", w, h)
                theme(b, which)
                if b.eval("document.querySelector('#oshell .o-askconv')"):
                    b.click("#oshell .o-askconv")
                    b.wait(f"{THREAD}", 15)
                width = b.eval("document.documentElement.scrollWidth")
                self.assertLessEqual(width, w + 1, f"the Ask section overflows at {w}px {which}")
                unnamed = b.eval("[...document.querySelectorAll('#oshell button, #oshell a, #oshell textarea, #oshell select, #oshell input')]"
                                 ".filter(x => !(x.getAttribute('aria-label') || x.textContent || x.labels?.[0]?.textContent || '').trim()).length")
                self.assertEqual(0, unnamed, "a control has no accessible name")
                shot(f"ask-{w}-{which}")
        self.assertEqual(before, reasoning_jobs(), "looking launched a run")


if __name__ == "__main__":
    unittest.main()
