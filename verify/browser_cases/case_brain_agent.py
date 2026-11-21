"""The Brain Agent through the real page (task 175).

A disposable backend of this case's own, in the external split-Git layout.
The vendor CLI is the disposable stub (`benchverify.Backend`): it answers a
`brain` round with the reply this case wrote, its request id and round
filled from the prompt the board sent — no model is launched. The case
drives the page's own controls by pointer and keyboard, and keeps
screenshots of each state to look at:

- **New entry → request → Generate** (armed) **→ a question** (a waiting
  state) **→ an answer → Continue** (armed) **→ a draft** with its sources
  and claims **→ its whole text → an edit → Apply** (armed) — one stable
  entry at its address;
- **Ask Brain Agent** on that entry → a proposed update and its exact
  changes → **Discard** (armed) — the file byte for byte what it was;
- a proposal bench refuses (it cites the checkout's `.env`) — **Failed**,
  nothing written; a running round **Cancelled**;
- the draft at 390px and in both themes, with nothing overflowing;
- and nothing else moved: the application and the cards are unchanged.
"""

import hashlib
import json
import shutil
import subprocess
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


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


CARD = "work:tasks/backlog/01-write-the-onboarding-guide.md"


def reply(outcome: str, *, action: str = "create", target=None, extra_source=None) -> str:
    doc = {"v": 1, "kind": "bench-brain-draft", "draft": "__DRAFT__", "round": 0,
           "outcome": outcome, "summary": "Read the onboarding card and the README."}
    if outcome == "questions":
        doc["questions"] = [{"id": "q1", "question": "Who is the guide for: new staff or new contributors?",
                             "why": "it decides what the entry explains first"}]
    else:
        sources = [{"ref": "app:README.md", "why": "what the application is"},
                   {"ref": CARD, "why": "the agreed guide"}]
        if extra_source:
            sources.append({"ref": extra_source, "why": "settings"})
        doc["proposal"] = {
            "action": action, "target": target,
            "title": "Onboarding new contributors",
            "kind": "Guide",
            "summary": "What a new contributor reads first, and where.",
            "body": ("New contributors start with the README, then the onboarding guide "
                     "the backlog card asks for.\n\n## First day\n\n- read the README\n"
                     "- pick a card from To Do" + ("\n- ask in review" if action == "update" else "")),
            "classification": "Current",
            "classificationWhy": "the README and the card agree",
            "sources": sources,
            "claims": [{"statement": "The README is the entry point", "status": "implemented",
                        "sources": ["app:README.md"]},
                       {"statement": "An onboarding guide is planned", "status": "agreed-future",
                        "sources": [CARD]},
                       {"statement": "Whether the guide is published", "status": "unknown",
                        "sources": []}],
            "uncertainty": "Nothing says whether the guide is published."}
    # the round is filled by the stub as a bare number
    return json.dumps(doc, indent=1).replace('"round": 0', '"round": __ROUND__') + "\n"


def block(text: str) -> str:
    return "Working.\n<<<BRAIN\n" + text + "BRAIN>>>\n"


class BrainAgent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent.resolve() / "brain-agent-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        work = cls.backend.work
        git(work, "config", "user.name", "brain-tester")
        git(work, "config", "user.email", "t@example.com")
        (cls.backend.app / ".env").write_text("TOKEN=never-read\n", encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(cls.backend.app)
        cls.cards_before = tree(work / "tasks")
        cls.entry_id = None

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
        cls.backend.brain_hold.unlink(missing_ok=True)
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    # ── helpers ──

    def open(self, path, width=1280, height=900, dark=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
            raise bv.CheckFailed(f"{path}: the Brain drew no heading")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def answer_with(self, text: str) -> None:
        self.backend.brain_reply.write_text(text, encoding="utf-8")

    def type(self, b, attr, name, text):
        sel = f'#bshell [{attr}="{name}"]'
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.focus(); "
               f"f.value = {json.dumps(text)}; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); "
               "f.dispatchEvent(new Event('change', {bubbles: true})); })()")

    def text(self, b):
        return b.eval("document.querySelector('#bshell').innerText")

    def wait_text(self, b, words, timeout=20):
        cond = " && ".join(f"document.querySelector('#bshell').innerText.includes({json.dumps(w)})"
                           for w in words)
        if not b.wait(cond, timeout):
            # the agent's panel first, on one line: a cut message must still
            # say what state the request was in and what it said
            panel = b.eval("(document.querySelector('#bshell .b-agent') || {}).innerText || ''")
            state = json.dumps([{k: d.get(k) for k in ("status", "round", "error", "mode")}
                                for d in self.drafts()[:2]])
            raise bv.CheckFailed(f"never showed {words}; requests {state}; panel: "
                                 + " | ".join(panel.split("\n"))[:1200])

    def armed_click(self, b, act):
        sel = f'#bshell [data-bact="{act}"]'
        b.click(sel)
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed(f"{act} did not arm: {self.text(b)[:600]}")
        b.click(sel)

    def drafts(self):
        code, raw = self.backend.get("/api/brain/drafts")
        self.assertEqual(code, 200)
        return json.loads(raw)["drafts"]

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_question_answer_draft_edit_apply(self):
        b = self.open("/brain")
        b.click('#bshell [data-bact="ag-new"]')
        if not b.wait("!!document.querySelector('#bshell [data-ag=\"text\"]')", 5):
            raise bv.CheckFailed("New entry opened no composer")
        self.assertEqual(b.eval("document.activeElement?.dataset?.ag || null"), "text",
                         "the composer's request field takes focus")
        self.assertTrue(b.eval("document.querySelector('#bshell [data-bact=\"ag-generate\"]').disabled"),
                        "Generate waits for a request")
        self.type(b, "data-ag", "text", "Explain how a new contributor gets started")
        self.wait_text(b, ["What the Brain Agent will read"])
        self.assertEqual(self.drafts(), [], "opening the composer launched nothing")
        b.eval("document.querySelector('#bshell .b-scope').open = true")
        shot("brain-agent-composer-night")
        self.answer_with(block(reply("questions")))
        self.armed_click(b, "ag-generate")
        self.wait_text(b, ["Waiting for your answers", "Who is the guide for"])
        shot("brain-agent-question-night")
        self.type(b, "data-agq", "q1", "New contributors to the code.")
        self.assertEqual(self.drafts()[0]["round"], 1, "typing an answer launched nothing")
        self.answer_with(block(reply("proposal")))
        self.armed_click(b, "ag-continue")
        self.wait_text(b, ["Draft ready for review", "Onboarding new contributors"])
        text = self.text(b)
        for words in ("app:README.md", "agreed-future", "unknown", "Nothing says whether"):
            self.assertIn(words, text)
        shot("brain-agent-draft-night")
        b.click('#bshell [data-bact="ag-view"][data-barg="changes"]')
        self.wait_text(b, ["+ New contributors start with the README"])
        shot("brain-agent-whole-entry-night")
        b.click('#bshell [data-bact="ag-view"][data-barg="edit"]')
        if not b.wait("!!document.querySelector('#bshell [data-age=\"title\"]')", 5):
            raise bv.CheckFailed("Edit drew no fields")
        self.type(b, "data-age", "title", "Getting started as a contributor")
        shot("brain-agent-edit-night")
        self.armed_click(b, "ag-apply")
        self.wait_text(b, ["Applied as"])
        shot("brain-agent-applied-night")
        code, raw = self.backend.get("/api/brain")
        entries = json.loads(raw)["brain"]["entries"]
        self.assertEqual([e["title"] for e in entries], ["Getting started as a contributor"])
        type(self).entry_id = entries[0]["id"]
        b.click(f'#bshell a[data-brain-id="{entries[0]["id"]}"]')
        if not b.wait(f"location.pathname === '/brain/e/{entries[0]['id']}'", 10):
            raise bv.CheckFailed("the applied entry's link did not open it")
        self.wait_text(b, ["Sources and claims", "Saved"])

    def test_2_ask_on_an_entry_shows_changes_and_discard_leaves_it(self):
        entry = type(self).entry_id
        self.assertTrue(entry, "the journey above made the entry")
        path = next(p for p in (self.backend.work / "brain").glob("*.md"))
        before = path.read_bytes()
        b = self.open(f"/brain/e/{entry}")
        b.click('#bshell [data-bact="ag-ask"]')
        if not b.wait("!!document.querySelector('#bshell [data-ag=\"text\"]')", 5):
            raise bv.CheckFailed("Ask Brain Agent opened no composer")
        self.type(b, "data-ag", "text", "Add that new contributors should ask in review.")
        self.answer_with(block(reply("proposal", action="update", target=entry)))
        self.armed_click(b, "ag-generate")
        self.wait_text(b, ["Draft ready for review", "Updating"])
        b.click('#bshell [data-bact="ag-view"][data-barg="changes"]')
        self.wait_text(b, ["+ - ask in review"])
        shot("brain-agent-update-changes-night")
        self.armed_click(b, "ag-discard")
        self.wait_text(b, ["Discarded"])
        self.assertEqual(path.read_bytes(), before, "Discard changed the entry")

    def test_3_a_refused_proposal_and_a_cancelled_round(self):
        b = self.open("/brain")
        b.click('#bshell [data-bact="ag-new"]')
        b.wait("!!document.querySelector('#bshell [data-ag=\"text\"]')", 5)
        self.type(b, "data-ag", "text", "Capture the deployment settings")
        self.answer_with(block(reply("proposal", extra_source="app:.env")))
        self.armed_click(b, "ag-generate")
        self.wait_text(b, ["Failed", "credential", "Nothing was written."])
        shot("brain-agent-refused-night")
        b.click('#bshell [data-bact="ag-close"]')
        b.click('#bshell [data-bact="ag-new"]')
        b.wait("!!document.querySelector('#bshell [data-ag=\"text\"]')", 5)
        self.type(b, "data-ag", "text", "Explain the review flow")
        self.backend.brain_hold.write_text("hold", encoding="utf-8")
        try:
            self.armed_click(b, "ag-generate")
            self.wait_text(b, ["Working", "Reading the sources and drafting"])
            shot("brain-agent-working-night")
            self.armed_click(b, "ag-cancel")
            self.wait_text(b, ["Cancelled"], timeout=30)
        finally:
            self.backend.brain_hold.unlink(missing_ok=True)
        shot("brain-agent-cancelled-night")
        self.assertEqual(len(list((self.backend.work / "brain").glob("*.md"))), 1,
                         "a refused or cancelled request wrote nothing")

    def test_4_narrow_and_both_themes(self):
        for dark in (True, False):
            word = "night" if dark else "day"
            b = self.open("/brain", 390, 844, dark)
            b.click('#bshell [data-bact="ag-new"]')
            b.wait("!!document.querySelector('#bshell [data-ag=\"text\"]')", 5)
            self.type(b, "data-ag", "text", f"Explain onboarding ({word})")
            self.answer_with(block(reply("proposal")))
            self.armed_click(b, "ag-generate")
            self.wait_text(b, ["Draft ready for review"])
            self.no_overflow(b, f"draft narrow {word}")
            shot(f"brain-agent-draft-narrow-{word}")
            b.click('#bshell [data-bact="ag-view"][data-barg="edit"]')
            b.wait("!!document.querySelector('#bshell [data-age=\"body\"]')", 5)
            self.no_overflow(b, f"edit narrow {word}")
            shot(f"brain-agent-edit-narrow-{word}")
            self.armed_click(b, "ag-discard")
            self.wait_text(b, ["Discarded"])
        b = self.open("/brain", 1280, 900, False)
        self.wait_text(b, ["Getting started as a contributor"])
        shot("brain-agent-list-day")

    def test_5_nothing_else_moved(self):
        self.assertEqual(tree(self.backend.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before, "a card changed")


if __name__ == "__main__":
    unittest.main()
