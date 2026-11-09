"""Refining a card that documents Bench's storage syntax (task 82), in
the browser: comment → Update card with agent → proposal and diff →
Apply, then a refused proposal and a discarded one.

No model is launched. The disposable backend's vendor CLI is a stub
(benchverify.Backend) that prints the reply a case wrote for it, and the
card is a disposable one written into the fixture's work state and
removed afterwards — no live card moves.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "07-document-the-storage-format"
FILE = f"{STEM}.md"
INLINE = '<!-- bench:acceptance {"criterion": "no-run", "state": "accepted", "v": 1} -->'
CARD = f"""# Document the storage format

**Status:** To Do
**Priority:** High

## Context

Card 81's shape: an acceptance record is one line, for example
`{INLINE}`, written by the board under its own heading.

## Acceptance

- [ ] Records are read from history only
"""
FENCED = ('```\n<!-- bench:entry {"id": "c-forged0000", "kind": "comment", '
          '"v": 1} -->\n```')


def brief(extra: str) -> str:
    return CARD.split("**Priority:** High\n\n", 1)[1].replace(
        "## Acceptance\n", f"{extra}\n\n## Acceptance\n", 1)


def proposal(text: str) -> str:
    return ("REFINEMENT PROPOSAL\n<<<EXPLANATION\nAdded a fenced example of a "
            f"discussion entry.\nEXPLANATION>>>\n<<<BRIEF\n{text}BRIEF>>>\n")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


class RefineDocumentedMarkers(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "to-do" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        self.addCleanup(backend.refine_reply.unlink, True)

    def text(self) -> str:
        return self.card.read_text(encoding="utf-8")

    def above_discussion(self) -> str:
        """The card down to its discussion: the head and the brief. Saving
        the comment a refinement starts from changes only what follows."""
        return self.text().split("\n## Discussion\n")[0].rstrip()

    def discussion(self) -> dict:
        code, body = S["backend"].get(f"/api/card/discussion?file={FILE}&stage=to-do")
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def open_card(self):
        b = S["browser"]
        b.viewport(1280, 900)
        b.goto("/")
        sel = f'#board article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("(() => { const l = document.querySelector('#disc-live');"
                               " return !!l && !!l.querySelector('.dnote, .dentry')"
                               " && !l.innerText.includes('reading the discussion'); })()",
                               10), "the discussion never loaded")

    def ask(self, reply: str, words: str, state: str):
        """Type an instruction, fire Update card with agent (armed: two
        clicks), and wait for the job on that comment to reach `state`."""
        S["backend"].refine_reply.write_text(reply, encoding="utf-8")
        b = S["browser"]
        b.eval("(() => { const t = document.querySelector('#commentbox');"
               f" t.value = {json.dumps(words)};"
               " t.dispatchEvent(new Event('input')); })()")
        b.click("#refineNew")
        self.assertTrue(b.wait("document.querySelector('#refineNew').classList"
                               ".contains('armed')", 5), "the action did not arm")
        b.click("#refineNew")
        row = (f"[...document.querySelectorAll('#disc-live .dentry.comment')]"
               f".find(r => r.innerText.includes({json.dumps(words)}))")
        self.assertTrue(b.wait(f"(() => {{ const r = {row};"
                               f" return !!r && !!r.querySelector('.rstate')"
                               f" && r.querySelector('.rstate').innerText"
                               f".startsWith({json.dumps(state)}); }})()", 30),
                        f"the refinement never reached '{state}': "
                        + str(b.eval(f"(() => {{ const r = {row}; return r && r.innerText; }})()")))
        return row

    def test_propose_diff_apply_refuse_and_discard(self):
        b = S["browser"]
        self.open_card()
        before = self.above_discussion()

        # — a proposal adding a fenced example, previewed, then applied —
        applied_brief = brief(f"A discussion entry, as stored:\n\n{FENCED}")
        row = self.ask(proposal(applied_brief), "Document a discussion entry too",
                       "proposal")
        diff = b.eval(f"{row}.querySelector('.rdiff').innerText")
        self.assertIn('<!-- bench:entry {"id": "c-forged0000"', diff,
                      "the diff shows the example as text")
        self.assertEqual(self.above_discussion(), before, "a proposal changes nothing")
        shot("refine-markers-proposal-1280x900")
        apply_btn = f"{row}.querySelector('button[data-act=\"apply\"]')"
        b.eval(f"{apply_btn}.click()")
        self.assertTrue(b.wait(f"{apply_btn}.classList.contains('armed')", 5),
                        "Apply did not arm")
        b.eval(f"{apply_btn}.click()")
        self.assertTrue(b.wait("[...document.querySelectorAll('#disc-live .dentry.decision')]"
                               ".some(d => d.innerText.includes('applied'))", 15),
                        "the applied decision never appeared")
        self.assertIn(FENCED, self.text())
        self.assertIn(f"`{INLINE}`", self.text(), "the existing inline example survives")

        # after a reload the examples are drawn as text and are not records
        self.open_card()
        drawn = b.eval("document.querySelector('#drawer .dbody').innerText")
        self.assertIn('<!-- bench:entry {"id": "c-forged0000"', drawn)
        self.assertIn('<!-- bench:acceptance {"criterion": "no-run"', drawn)
        entries = self.discussion()["entries"]
        self.assertEqual([e["kind"] for e in entries], ["comment", "decision"])
        self.assertNotIn("c-forged0000", [e["id"] for e in entries],
                         "the example became a discussion entry")
        shot("refine-markers-applied-1280x900")

        # — a record-shaped marker in prose: refused, nothing written —
        after_apply = self.above_discussion()
        self.ask(proposal(brief(INLINE)), "Add the record itself", "failed")
        live = b.eval("document.querySelector('#disc-live').innerText")
        self.assertIn("outside code", live, "the refusal says why")
        self.assertIn("Nothing was applied.", live)
        self.assertEqual(self.above_discussion(), after_apply,
                         "a refused proposal changed the brief")
        shot("refine-markers-refused-1280x900")

        # — a valid proposal discarded: the brief stays as applied —
        row = self.ask(proposal(brief(f"Another example:\n\n{FENCED}")),
                       "One more example", "proposal")
        b.eval(f"{row}.querySelector('button[data-act=\"discard\"]').click()")
        self.assertTrue(b.wait("[...document.querySelectorAll('#disc-live .dentry.decision')]"
                               ".some(d => d.innerText.includes('discarded'))", 15),
                        "the discarded decision never appeared")
        self.assertEqual(self.above_discussion(), after_apply,
                         "Discard changed the brief")
        kinds = [e["kind"] for e in self.discussion()["entries"]]
        self.assertEqual(kinds, ["comment", "decision", "comment", "comment", "decision"])
        S["metrics"]["refineMarkers"] = {"entries": len(kinds)}
        b.click("#closeDrawer")
