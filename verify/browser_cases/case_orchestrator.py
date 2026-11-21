"""The top-level Orchestrator view (task 145), driven in a browser against
the disposable backend's real orchestration routes.

The backend gains three cards of this case's own: #40, in progress, whose
Orchestration record holds an agent-reported response (with markup and a
credential-shaped token in it) and three open questions — one answerable,
one addressed to someone else, one asked against an older brief — and a
phase card #41 in Backlog listing #42. Recovery and the operation ledger
get one exhausted incident and one operation of unknown outcome, written
as the engine writes them. Everything else is done through the page:

- the Orchestrator is a top-level view with its own address, and every
  section reads without launching, checking or making a worktree;
- an agreement is drafted, edited, saved, previewed and applied, and a
  delegated merge under ungated rules is said to be unverified;
- the Bench pause reaches the phase scope; the phase's own pause does not
  reach the Bench scope; a phase adoption this board may not make is
  refused in words and changes nothing;
- an answer is bound to what the page read: the server refuses a stale
  basis, the page refuses what it may not answer, and the answer lands on
  the card and in the resolved history;
- a manual takeover and its reversal, and a run's cancellation with its
  confirmation and effects stated, stopping exactly that run;
- recovery exhaustion and the unknown outcome are shown and settled;
- the board strip and the card's Overview lead to the exact scope;
- both themes at 1440 and 390 px, without document overflow, with typed
  text and open folds surviving a repaint.

The agreement is never applied with this installation designated, and
BOARD_ORCHESTRATE_INTERVAL holds the pass off: nothing is launched by
orchestration in this case. Everything the case added is removed after it.

Run through verify/browser; any other way these refuse.
"""

import json
import shutil
import time
import unittest

import benchverify as bv

S = bv.SESSION
CARD = "40-export-reports-as-csv.md"
PHASE = "41-ship-the-reports.md"
MEMBER = "42-saved-report-filters.md"
TOKEN = "ghp_" + "Q7w8" * 9
OP = "op-" + "e" * 20
INCIDENT = "in-" + "f" * 16


def record(meta: dict) -> str:
    return "<!-- bench:orchestration " + json.dumps(meta, sort_keys=True) + " -->"


def question(qid, text, **extra):
    return record({"v": 1, "id": qid, "kind": "question", "source": "agent-reported",
                   "actor": "bench", "at": "2026-11-18T10:00:00Z", "card": "40",
                   "question": qid, "text": text, "need": "questions",
                   "operation": "rs-145", **extra})


CARD_TEXT = "\n".join([
    "# 40 — Export reports as CSV", "", "**Status:** In Progress", "**Priority:** High", "",
    "Let a person export a report as CSV.", "", "## Acceptance", "",
    "- [ ] The export holds every visible column", "", "## Orchestration record", "",
    record({"v": 1, "id": "r-dddddddddd", "kind": "response", "source": "agent-reported",
            "at": "2026-11-18T09:59:00Z", "card": "40", "operation": "rs-145",
            "need": "questions", "status": "valid", "model": "review-model",
            "summary": "One decision is open. <img src=x onerror=alert(1)> " + TOKEN,
            "findings": [{"id": "f1", "statement": "No row limit <script>alert(1)</script>",
                          "severity": "warning", "evidence": ["card:40"]}],
            "actions": [{"id": "a1", "action": "ask-owner", "card": "40",
                         "why": "a product call", "evidence": []}],
            "questions": ["q-aaaaaaaaaa"]}),
    question("q-aaaaaaaaaa", "What should happen above 50,000 rows?",
             options=["Cap at 50,000 rows", "Stream the whole report",
                      "Background job with an email link"],
             recommendation="Cap at 50,000 rows",
             why="The brief does not define large exports. **A cap** needs no queue.",
             evidence=["card:40"]),
    question("q-bbbbbbbbbb", "Which teams may export?", options=["All", "Admins"],
             owner="someone-else"),
    question("q-cccccccccc", "Should exports be logged?", options=["Yes", "No"],
             briefRev="0000000000000000"),
    ""])

PHASE_TEXT = ("# 41 — Ship the reports\n\n**Status:** Backlog\n**Priority:** Medium\n"
              "**Type:** Phase\n\n## Cards\n\n- 42 — Saved report filters\n")
MEMBER_TEXT = ("# 42 — Saved report filters\n\n**Status:** Backlog\n**Priority:** Low\n\n"
               "Remember a report's filters.\n")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def api(path="/api/orchestration") -> dict:
    code, body = S["backend"].get(path)
    if code != 200:
        raise bv.CheckFailed(f"GET {path} answered {code}")
    return json.loads(body)


def scope(sid: str) -> dict:
    return next(s for s in api()["orchestration"]["effective"]["scopes"] if s["id"] == sid)


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def metric(name, value):
    S["metrics"].setdefault("orchestrator", {})[name] = value


def theme(b, which):
    if b.eval("document.documentElement.dataset.theme") != which:
        b.reveal("#themebtn")
        b.click("#themebtn")


def visit(path, ready, width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto(path)
    if not b.wait(ready, 20):
        seen = b.eval(PAGE_TEXT)
        raise bv.CheckFailed(f"{path}: never showed {ready}; the page reads {seen!r}")
    return b


PAGE_TEXT = "(document.querySelector('#oshell') || document.body).innerText.slice(0, 400)"


def double(b, selector):
    """An armed control: the first click arms, the second fires."""
    b.click(selector)
    found = f"document.querySelector({json.dumps(selector)})"
    if not b.wait(f"{found} && {found}.classList.contains('armed')", 5):
        raise bv.CheckFailed(f"{selector} did not arm on the first click")
    b.click(selector)


def passive_facts() -> dict:
    """What a read must never change."""
    backend = S["backend"]
    state = json.loads(backend.get("/api/state")[1])
    local = backend.tool / "manager" / "local" / "state"
    listing = lambda p: sorted(x.name for x in p.iterdir()) if p.is_dir() else []
    return {"agents": len(state.get("agents") or []),
            "worktrees": listing(backend.trees),
            "evidence": listing(local / "evidence"),
            "operations": listing(local / "orchestration" / "operations"),
            "reasoning": listing(local / "reasoning")}


class Orchestrator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        tasks = backend.work / "tasks"
        (tasks / "in-progress" / CARD).write_text(CARD_TEXT, encoding="utf-8")
        (tasks / "backlog" / PHASE).write_text(PHASE_TEXT, encoding="utf-8")
        (tasks / "backlog" / MEMBER).write_text(MEMBER_TEXT, encoding="utf-8")
        cls.local = backend.tool / "manager" / "local"
        # the watcher narrates new files every two seconds: the board lists them
        deadline = time.time() + 15
        while time.time() < deadline:
            files = [t["file"] for s in api("/api/state")["board"]["stages"] for t in s["tasks"]]
            if CARD in files and PHASE in files:
                break
            time.sleep(0.3)
        else:
            raise bv.SetupError("the backend never listed the case's cards")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        if backend.refine_hold.exists():
            backend.refine_hold.unlink()
        for stage in ("backlog", "to-do", "in-progress", "review", "done", "archive"):
            for name in (CARD, PHASE, MEMBER):
                (backend.work / "tasks" / stage / name).unlink(missing_ok=True)
        (cls.local / "orchestration.json").unlink(missing_ok=True)
        shutil.rmtree(cls.local / "state" / "orchestration", ignore_errors=True)
        # the board lists them no longer (task 176: a condition, where a
        # fixed 2.5s used to stand in for the watcher's beat)
        deadline = time.time() + 15
        while time.time() < deadline:
            files = {t["file"] for s in api("/api/state")["board"]["stages"] for t in s["tasks"]}
            if not files & {CARD, PHASE, MEMBER}:
                break
            time.sleep(0.2)    # polling the condition above
        else:
            raise bv.SetupError("the backend still lists the orchestrator case's cards")

    # ── 1. a top-level view, and reading it changes nothing ──────────────

    def test_1_off_and_every_section_only_reads(self):
        before = passive_facts()
        b = visit("/orchestrator", "/Nothing starts by itself/.test(document.querySelector('#oshell').innerText)")
        theme(b, "dark")
        self.assertTrue(b.eval("document.querySelector('#view-orchestrator').classList.contains('on')"))
        self.assertEqual("page", b.eval("document.querySelector('#views [data-view=\"orchestrator\"]')"
                                        ".getAttribute('aria-current')"))
        self.assertEqual("/orchestrator", b.eval("document.querySelector('#views [data-view=\"orchestrator\"]')"
                                                 ".getAttribute('href')"))
        self.assertEqual("Off", b.eval("document.querySelector('#oshell .o-status .o-badge').textContent"))
        self.assertIn("Nothing starts by itself", b.eval("document.querySelector('#oshell').innerText"))
        sections = b.eval("[...document.querySelectorAll('#oshell .o-nav a')].map(a => a.getAttribute('href'))")
        self.assertEqual(["/orchestrator", "/orchestrator/ask", "/orchestrator/trace", "/orchestrator/work",
                          "/orchestrator/decisions",
                          "/orchestrator/phases", "/orchestrator/recovery", "/orchestrator/activity",
                          "/orchestrator/agreement"], sections)
        shot("orchestrator-off-1440-dark")
        for href in sections[1:]:
            b.click(f"#oshell .o-nav a[href='{href}']")
            if not b.wait(f"location.pathname === '{href}' && !document.querySelector('#oshell [aria-busy]')", 10):
                raise bv.CheckFailed(f"{href} did not settle")
        # the section's address is shareable: a fresh load opens it
        b = visit("/orchestrator/agreement", "/No draft/.test(document.querySelector('#oshell').innerText)")
        # every reading the page asked for has landed and been painted
        b.settled()
        self.assertEqual(before, passive_facts(), "reading the Orchestrator changed something")
        metric("passiveReads", "unchanged")

    # ── 2. the operating agreement: draft, edit, save, preview, apply ────

    def test_2_agreement_preview_and_apply(self):
        b = visit("/orchestrator/agreement", "document.querySelector('[data-o=\"start-draft-new\"]')")
        double(b, "[data-o='start-draft-new']")
        if not b.wait("document.querySelector('#ag-bench-enabled')", 10):
            raise bv.CheckFailed("starting a draft drew no editor")
        self.assertFalse(api()["orchestration"]["effective"]["configured"],
                         "starting a draft put something in force")
        for box in ("#ag-bench-enabled", "#ag-bench-authority-implement",
                    "#ag-bench-authority-review", "#ag-bench-authority-merge"):
            b.click(box)
        b.eval("(() => { const i = document.querySelector('#ag-newphase'); i.focus(); i.value = '41';"
               " i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.click("[data-o='addphase']")
        if not b.wait("document.querySelector('#ag-phases-41-enabled')", 5):
            raise bv.CheckFailed("adding a phase drew no phase fields")
        self.assertIn("unsaved edits", b.eval("document.querySelector('.o-draft h3').innerText"))
        self.assertTrue(b.eval("document.querySelector('[data-o=\"apply\"]').disabled"),
                        "Apply is offered over unsaved edits")
        b.click("[data-o='preview']")
        if not b.wait("document.querySelector('.o-preview')", 10):
            raise bv.CheckFailed("Preview drew nothing")
        text = b.eval("document.querySelector('.o-preview').innerText")
        self.assertIn("nothing was written", text)
        self.assertIn("the Bench scope: scope false → true", text)
        self.assertIn("phase #41: scope false → true", text)
        self.assertIn("not verified", text)
        self.assertFalse(api()["orchestration"]["effective"]["configured"], "a preview wrote")
        shot("orchestrator-agreement-preview-1440-dark")
        double(b, "[data-o='save-draft']")
        deadline = time.time() + 15
        while time.time() < deadline:
            draft = api()["orchestration"]["draft"]
            if draft and "41" in (draft["document"] or {}).get("phases", {}):
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("saving did not write the edited draft")
        self.assertTrue(draft["valid"], draft["problems"])
        self.assertTrue(draft["document"]["bench"]["authority"]["merge"])
        self.assertFalse(api()["orchestration"]["effective"]["configured"], "saving applied")
        # the page has read the saved revision before Apply is offered
        rev = draft["revision"]
        if not b.wait(f"/{rev}/.test(document.querySelector('.o-draft h3')?.innerText || '')"
                      " && !/unsaved edits/.test(document.querySelector('.o-draft h3').innerText)"
                      " && !document.querySelector('[data-o=\"apply\"]').disabled", 15):
            raise bv.CheckFailed("the saved draft could not be applied")
        double(b, "[data-o='apply']")
        if not b.wait("document.querySelector('#oshell .o-status .o-badge').textContent !== 'Off'", 15):
            raise bv.CheckFailed("applying left the view reading Off")
        found = api()["orchestration"]
        self.assertTrue(found["effective"]["configured"] and found["effective"]["valid"])
        self.assertTrue(scope("bench")["authority"]["merge"]["allowed"])
        self.assertEqual("phase:41", scope("phase:41")["id"])
        # applying started nothing: this installation was never designated
        self.assertFalse(found["installation"]["executes"])
        self.assertEqual(0, (found["operations"]["counts"] or {}).get("intent", 0))

    # ── 3. parent and child pause ────────────────────────────────────────

    def test_3_parent_pause_reaches_the_phase_and_the_child_pause_does_not_climb(self):
        b = visit("/orchestrator", "document.querySelector('[data-o=\"pause:bench\"]')")
        effect = b.eval("document.querySelector('.o-status .o-effect').innerText")
        self.assertIn("Runs already working continue", effect)
        self.assertIn("Nothing is cancelled", effect)
        double(b, "[data-o='pause:bench']")
        if not b.wait("document.querySelector('#oshell .o-status .o-badge').textContent === 'Paused'", 10):
            raise bv.CheckFailed("the Bench pause was not shown")
        self.assertTrue(scope("bench")["paused"])
        self.assertEqual("bench", scope("phase:41")["pausedBy"])
        b = visit("/orchestrator/phases", "document.querySelector('#ophase-41')")
        self.assertIn("Paused by the Bench scope", b.eval("document.querySelector('#ophase-41 h3').innerText"))
        shot("orchestrator-phases-bench-paused-1440-dark")
        b = visit("/orchestrator", "document.querySelector('[data-o=\"resume:bench\"]')")
        double(b, "[data-o='resume:bench']")
        if not b.wait("document.querySelector('[data-o=\"pause:bench\"]')", 10):
            raise bv.CheckFailed("resuming was not shown")
        self.assertFalse(scope("bench")["paused"])
        b = visit("/orchestrator/phases?card=41", "document.querySelector('[data-o=\"pause:phase:41\"]')")
        double(b, "[data-o='pause:phase:41']")
        if not b.wait("document.querySelector('[data-o=\"resume:phase:41\"]')", 10):
            raise bv.CheckFailed("the phase pause was not shown")
        self.assertEqual("phase:41", scope("phase:41")["pausedBy"])
        self.assertFalse(scope("bench")["paused"], "a phase pause reached its parent")
        self.assertNotEqual("Paused", b.eval("document.querySelector('#oshell .o-status .o-badge').textContent"))
        double(b, "[data-o='resume:phase:41']")
        if not b.wait("document.querySelector('[data-o=\"pause:phase:41\"]')", 10):
            raise bv.CheckFailed("resuming the phase was not shown")

    def test_4_an_adoption_this_board_may_not_make_is_refused_in_words(self):
        b = visit("/orchestrator/phases?card=41", "document.querySelector('[data-o=\"adopt-phase:41\"]')")
        double(b, "[data-o='adopt-phase:41']")
        if not b.wait("document.querySelector('.o-notice.alarm')", 10):
            raise bv.CheckFailed("a refused adoption said nothing")
        self.assertTrue(b.eval("document.querySelector('.o-notice.alarm').innerText.trim().length > 10"))
        entry = next(p for p in api()["schedule"]["phases"]["phases"] if p["card"] == PHASE)
        self.assertEqual("phase runner", entry["owner"])
        metric("refusedAdoption", b.eval("document.querySelector('.o-notice.alarm').innerText")[:160])

    # ── 5. answers, bound to what the page read ──────────────────────────

    def test_5_answer_validation_and_history(self):
        b = visit("/orchestrator/decisions?card=40", "document.querySelector('[data-o=\"opt-q-aaaaaaaaaa-0\"]')")
        main = "document.querySelector('#oshell')"
        # untrusted text is inert, and the token is masked
        self.assertIsNone(b.eval(f"{main}.querySelector('img, script')"))
        self.assertNotIn(TOKEN, b.eval(f"{main}.innerText"))
        self.assertIn("«redacted»", b.eval(f"{main}.innerText"))
        self.assertIn("agent-reported", b.eval(f"{main}.innerText"))
        self.assertIn("proposals, not executed", b.eval(f"{main}.innerText"))
        # what the page may not answer, it says so and does not offer
        foreign = "document.querySelector('[data-o=\"answer:%s:q-bbbbbbbbbb\"]')" % CARD
        stale = "document.querySelector('[data-o=\"answer:%s:q-cccccccccc\"]')" % CARD
        self.assertTrue(b.eval(f"{foreign}.disabled"), "a question for someone else is answerable")
        self.assertTrue(b.eval(f"{stale}.disabled"), "a question to re-evaluate is answerable")
        self.assertIn("needs re-evaluation", b.eval(f"{main}.innerText"))
        # and the server holds the same line: a stale basis writes nothing
        data = api(f"/api/card/decisions?file={CARD}&stage=in-progress")
        q = next(x for x in data["questions"] if x["id"] == "q-aaaaaaaaaa")
        code, body = S["backend"].post("/api/card/decisions/answer", {
            "file": CARD, "stage": "in-progress", "question": q["id"], "basis": "r-0000000000",
            "briefSha": data["briefSha"], "actor": data["actor"]["name"], "choice": 0,
            "request": "case-145-stale-0001"})
        self.assertEqual(409, code, body[:300])
        # a person's answer, typed and chosen; a repaint keeps the typing
        b.click("[data-o='opt-q-aaaaaaaaaa-0']")
        b.eval("(() => { const t = document.querySelector('[data-o=\"note-q-aaaaaaaaaa\"]'); t.focus();"
               " t.value = 'Cap it for the first delivery'; t.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.eval("render()")
        self.assertEqual("Cap it for the first delivery",
                         b.eval("document.querySelector('[data-o=\"note-q-aaaaaaaaaa\"]').value"))
        self.assertTrue(b.eval("document.activeElement.matches('[data-o=\"note-q-aaaaaaaaaa\"]')"),
                        "a repaint took focus from the field being typed in")
        shot("orchestrator-decision-1440-dark")
        double(b, f"[data-o='answer:{CARD}:q-aaaaaaaaaa']")
        if not b.wait("/answer recorded on the card/.test(document.querySelector('.o-notice')?.innerText || '')", 15):
            raise bv.CheckFailed("the answer was not confirmed: " + str(b.eval("document.querySelector('.o-notice')?.innerText")))
        data = api(f"/api/card/decisions?file={CARD}&stage=in-progress")
        q = next(x for x in data["questions"] if x["id"] == "q-aaaaaaaaaa")
        self.assertEqual("answered", q["state"])
        self.assertEqual("Cap at 50,000 rows", q["answer"]["choice"])
        self.assertEqual("Cap it for the first delivery", q["answer"]["note"])
        text = (S["backend"].work / "tasks" / "in-progress" / CARD).read_text(encoding="utf-8")
        self.assertIn('"kind": "answer"', text)
        if not b.wait("document.querySelector('[data-okeep=\"closed-%s\"]')" % CARD, 15):
            raise bv.CheckFailed("the answered question did not move to the resolved history")
        fold = "document.querySelector('[data-okeep=\"closed-%s\"]')" % CARD
        b.click(f"[data-okeep='closed-{CARD}'] summary")
        if not b.wait(f"{fold} && {fold}.open", 5):
            raise bv.CheckFailed("the resolved history did not open")
        self.assertIn("Answered by", b.eval(f"{fold}.innerText"))
        # an open fold survives a repaint
        b.eval("render()")
        self.assertTrue(b.eval(f"{fold}.open"), "a repaint closed the fold a person opened")

    # ── 6. manual takeover, and cancelling one named run ─────────────────

    def test_6_takeover_and_cancellation(self):
        backend = S["backend"]
        b = visit("/orchestrator/work?card=40", "document.querySelector('[data-o=\"handback:40\"]')")
        self.assertIn("branch, worktree and history are kept",
                      b.eval("document.querySelector('.o-detail').innerText"))
        double(b, "[data-o='handback:40']")
        if not b.wait("document.querySelector('[data-o=\"reclaim:40\"]')", 10):
            raise bv.CheckFailed("the takeover was not shown")
        self.assertIn("40", scope("bench")["handback"])
        self.assertIn("Driven by you", b.eval("document.querySelector('#oshell').innerText"))
        shot("orchestrator-takeover-1440-dark")
        double(b, "[data-o='reclaim:40']")
        if not b.wait("document.querySelector('[data-o=\"handback:40\"]')", 10):
            raise bv.CheckFailed("reclaiming was not shown")
        self.assertNotIn("40", scope("bench")["handback"])
        # a run on the card: a held refinement, the one run a stub can keep alive
        backend.refine_hold.write_text("hold", encoding="utf-8")
        code, body = backend.post("/api/card/refine", {
            "file": CARD, "stage": "in-progress", "text": "Tighten the wording",
            "request": f"case-145-{time.time()}"})
        self.assertEqual(200, code, body[:300])
        deadline = time.time() + 20
        while time.time() < deadline:
            running = [a for a in api("/api/state")["agents"]
                       if a["task"] == CARD and a["status"] == "running"]
            if running:
                break
            time.sleep(0.3)
        else:
            raise bv.SetupError("the held refinement never ran on the card: " + body[:300].decode(errors="replace"))
        b = visit("/orchestrator/work?card=40", "document.querySelector('[data-oact=\"cancel\"]')")
        agent = b.eval("document.querySelector('[data-oact=\"cancel\"]').dataset.agent")
        b.click("[data-oact='cancel']")
        if not b.wait("document.querySelector('[data-oact=\"cancel\"]').classList.contains('armed')", 5):
            raise bv.CheckFailed("cancelling did not ask for confirmation")
        said = b.eval("document.querySelector('[data-oact=\"cancel\"]').closest('.o-action').innerText")
        self.assertIn("Confirm", said)
        self.assertIn("Branch, worktree and history are kept", said)
        self.assertIn("No other card, run or scope is affected", said)
        shot("orchestrator-cancel-confirm-1440-dark")
        b.click("[data-oact='cancel']")
        deadline = time.time() + 15
        while time.time() < deadline:
            runs = [a for a in api("/api/state")["agents"] if a["id"] == agent]
            if runs and runs[0]["status"] != "running":
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("the cancelled run is still running")
        backend.refine_hold.unlink()
        self.assertFalse(scope("bench")["paused"], "cancelling paused the board")

    # ── 7. recovery exhaustion and the unknown outcome ───────────────────

    def test_7_recovery_exhaustion_and_unknown_outcome(self):
        state = self.local / "state" / "orchestration"
        (state / "incidents").mkdir(parents=True, exist_ok=True)
        (state / "operations").mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        (state / "incidents" / f"{INCIDENT}.json").write_text(json.dumps({
            "v": 1, "id": INCIDENT, "file": CARD, "card": "40", "scope": "bench",
            "type": "failed", "run": "agent-x", "status": "exhausted",
            "code": "attempts-exhausted", "reason": "two diagnoses did not resolve the setup failure",
            "cap": 2, "openedAt": stamp, "openedEpoch": now, "updatedAt": stamp,
            "attempts": [{"n": 1, "status": "settled", "outcome": "diagnosed", "at": stamp},
                         {"n": 2, "status": "settled", "outcome": "diagnosed", "at": stamp}]}))
        (state / "operations" / f"{OP}.json").write_text(json.dumps({
            "v": 1, "key": OP, "action": "implement", "file": CARD,
            "card": "40", "scope": "bench", "pool": "implementation", "attempt": 1,
            "status": "unknown", "basis": "b", "intentAt": stamp, "intentEpoch": now,
            "day": time.strftime("%Y-%m-%d", time.gmtime(now)), "reason": "the launch raised"}))
        b = visit("/orchestrator", "document.querySelector('#oshell .o-status .o-badge')")
        if not b.wait("document.querySelector('#oshell .o-status .o-badge').textContent === 'Outcome unknown'", 15):
            raise bv.CheckFailed("an unknown outcome did not lead the status")
        b = visit("/orchestrator/recovery", f"document.querySelector('[data-o=\"resolve:{INCIDENT}\"]')")
        text = b.eval("document.querySelector('#oshell').innerText")
        self.assertIn("Exhausted", text)
        self.assertIn("2 of 2 diagnostic attempts launched", text)
        self.assertIn("no further automatic attempt will launch", text)
        self.assertIn("Bench will not repeat it blindly", text)
        shot("orchestrator-recovery-1440-dark")
        double(b, f"[data-o='op-not:{OP}']")
        deadline = time.time() + 10
        while time.time() < deadline and api()["orchestration"]["operations"]["counts"].get("unknown"):
            time.sleep(0.3)
        self.assertEqual(0, api()["orchestration"]["operations"]["counts"].get("unknown", 0))
        b = visit("/orchestrator/recovery", f"document.querySelector('[data-o=\"resolve:{INCIDENT}\"]')")
        double(b, f"[data-o='resolve:{INCIDENT}']")
        deadline = time.time() + 10
        while time.time() < deadline:
            found = [i for i in api()["schedule"]["recovery"]["incidents"] if i["id"] == INCIDENT]
            if found and found[0]["status"] == "resolved":
                break
            time.sleep(0.3)
        else:
            raise bv.CheckFailed("marking the incident handled did not resolve it")

    # ── 8. entry from the board and the card ─────────────────────────────

    def test_8_board_strip_and_card_entry_reach_the_exact_scope(self):
        b = visit("/board", "!document.querySelector('#orchstrip').hidden", 1440, 900)
        self.assertIn("Open Orchestrator", b.eval("document.querySelector('#orchstrip').innerText"))
        b.goto("/cards/40")
        link = "document.querySelector('#drawer a[href^=\"/orchestrator\"]')"
        if not b.wait(link, 20):
            raise bv.CheckFailed("the card's Overview has no way into the Orchestrator")
        # two of its questions are still open, so the way in is its decisions
        self.assertIn("2 open questions", b.eval("document.querySelector('#drawer').innerText"))
        self.assertEqual("/orchestrator/decisions?card=40", b.eval(f"{link}.getAttribute('href')"))
        b.click("#drawer a[href^='/orchestrator']")
        if not b.wait("location.pathname === '/orchestrator/decisions' && location.search === '?card=40'"
                      " && document.querySelector('.o-detail')", 10):
            raise bv.CheckFailed("the card's link did not open its scope")
        self.assertFalse(b.eval("document.querySelector('#drawer').classList.contains('open')"))
        b.eval("history.back()")
        if not b.wait("location.pathname === '/cards/40'", 10):
            raise bv.CheckFailed("Back did not return to the card")

    # ── 9. themes and widths ─────────────────────────────────────────────

    def test_9_both_themes_desktop_and_narrow(self):
        before = passive_facts()
        pages = (("/orchestrator", "overview"), ("/orchestrator/decisions?card=40", "decisions"),
                 ("/orchestrator/work?card=40", "work"), ("/orchestrator/agreement", "agreement"))
        for which in ("light", "dark"):
            for w, h in ((1440, 900), (390, 780)):
                for path, name in pages:
                    b = visit(path, "document.querySelector('#oshell .o-status')", w, h)
                    theme(b, which)
                    if not b.wait("!document.querySelector('#oshell [aria-busy]')", 10):
                        raise bv.CheckFailed(f"{path} did not settle")
                    width = b.eval("document.documentElement.scrollWidth")
                    self.assertLessEqual(width, w + 1, f"{path} overflows at {w}px {which}")
                    unnamed = b.eval("[...document.querySelectorAll('#oshell button, #oshell a, #oshell input, #oshell textarea')]"
                                     ".filter(x => !(x.getAttribute('aria-label') || x.textContent || x.labels?.[0]?.textContent || '').trim()).length")
                    self.assertEqual(0, unnamed, f"{path}: a control has no accessible name")
                    # every state badge says its state in words
                    blank = b.eval("[...document.querySelectorAll('#oshell .o-badge')].filter(x => !x.textContent.trim()).length")
                    self.assertEqual(0, blank)
                    if w == 390:
                        small = b.eval("[...document.querySelectorAll('#oshell .o-btn')].filter(x => x.offsetParent &&"
                                       " x.getBoundingClientRect().height < 39).length")
                        self.assertEqual(0, small, f"{path}: a control is under 40px at 390px")
                    if name in ("overview", "decisions") or (w == 390 and name == "agreement"):
                        shot(f"orchestrator-{name}-{w}-{which}")
                metric(f"width-{which}-{w}", width)
        b = visit("/board", "document.querySelector('#views')", 390, 780)
        self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), 391,
                             "the six-view switcher overflows at 390px")
        shot("orchestrator-board-strip-390-dark")
        self.assertEqual(before, passive_facts(), "looking at the views changed something")


if __name__ == "__main__":
    unittest.main()
