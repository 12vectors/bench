"""Comments on selected Brain text through the real page (task 210).

A disposable backend of this case's own, in the external split-Git layout,
with a Brain entry whose text repeats a line. The vendor CLI is the
disposable stub (`benchverify.Backend`): it answers a `brain` round with
the update this case wrote — no model is launched. The case drives the
page's own controls and keeps screenshots to look at:

- a selection in the entry's body → the **Comment** toolbar → **Post
  comment**: the thread is saved in `brain/comments/`, attributed, its
  passage marked in the text, and no drafting request exists; a reload
  reads it back;
- a repeated line selected alone is refused (it occurs twice and nothing
  says which), with nothing written;
- **Choose a passage…** by the keyboard alone → a paragraph → **Ask
  agent** (armed) → one Brain Agent request, its conversation opened in
  the shared panel; **Apply** there → the thread reads *Resolved by the
  applied edit*, with **View changes**, and the other comment stays open;
- a filed review says it is discussed on its card;
- 1280 and 390 pixels, both themes, nothing overflowing;
- and nothing else moved: the application and the cards are unchanged.
"""

import hashlib
import json
import shutil
import subprocess
import time
import unittest

import benchverify as bv

S = bv.SESSION

ENTRY = "e-release-notes"
BASE = f"""# Release notes

**Brain:** 1
**Id:** {ENTRY}
**Kind:** Guide
**Classification:** Current
**Summary:** How release notes are written.

Releases are cut from main by whoever is on duty.

## Write

Collect the merged cards and **summarise** each one.

Post in the channel.

## Check

Post in the channel.

Confirm the version.
"""

NEW_BODY = ("Releases are cut from main by the release manager, never by whoever is on duty.\n\n"
            "## Write\n\nCollect the merged cards and **summarise** each one.\n\nPost in the channel.\n\n"
            "## Check\n\nPost in the channel.\n\nConfirm the version.")


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


def reply() -> str:
    doc = {"v": 1, "kind": "bench-brain-draft", "draft": "__DRAFT__", "round": 0,
           "outcome": "proposal", "summary": "Named who cuts a release.",
           "proposal": {
               "action": "update", "target": ENTRY, "title": "Release notes",
               "kind": "Guide", "summary": "How release notes are written.", "body": NEW_BODY,
               "classification": "Current", "classificationWhy": "the README says so",
               "sources": [{"ref": "app:README.md", "why": "who cuts a release"}],
               "claims": [{"statement": "the release manager cuts releases", "status": "implemented",
                           "sources": ["app:README.md"]}],
               "uncertainty": "Nothing says who the release manager is."}}
    text = json.dumps(doc, indent=1).replace('"round": 0', '"round": __ROUND__') + "\n"
    return "Working.\n<<<BRAIN\n" + text + "BRAIN>>>\n"


SELECT = """(() => {
  const prose = document.querySelector('#bshell .b-entry .b-prose');
  const want = %s;
  const walker = document.createTreeWalker(prose, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const at = n.nodeValue.indexOf(want);
    if (at < 0) continue;
    const r = document.createRange();
    r.setStart(n, at); r.setEnd(n, at + want.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    return true;
  }
  return false;
})()"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


class BrainComments(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent.resolve() / "brain-comments-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        work = cls.backend.work
        git(work, "config", "user.name", "comment-tester")
        git(work, "config", "user.email", "t@example.com")
        (work / "brain").mkdir(exist_ok=True)
        (work / "brain" / f"{ENTRY}.md").write_text(BASE, encoding="utf-8")
        git(work, "add", "brain")
        git(work, "commit", "-qm", "a Brain entry to comment on")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(cls.backend.app)
        cls.cards_before = tree(work / "tasks")

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
        b.eval("(() => { try { sessionStorage.removeItem('bench-agent-panel'); } catch (e) {} return true; })()")
        if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
            raise bv.CheckFailed(f"{path}: the Brain drew no heading")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        if not b.wait("!!document.querySelector('#bshell .b-comments [data-cm=\"pick\"]')", 15):
            raise bv.CheckFailed("the entry drew no comments panel: "
                                 + b.eval("document.querySelector('#bshell').innerText")[:900])
        return b

    def wait_text(self, b, words, timeout=20, where="#bshell .b-comments"):
        cond = " && ".join(f"(document.querySelector({json.dumps(where)})?.innerText || '').includes({json.dumps(w)})"
                           for w in words)
        if not b.wait(cond, timeout):
            seen = b.eval(f"(document.querySelector({json.dumps(where)}) || {{}}).innerText || ''")
            raise bv.CheckFailed(f"never showed {words} in {where}: " + " | ".join(seen.split("\n"))[:1200])

    def type_text(self, b, sel, text):
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.focus(); "
               f"f.value = {json.dumps(text)}; f.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

    def select(self, b, text):
        if not b.eval(SELECT % json.dumps(text)):
            raise bv.CheckFailed(f"could not select {text!r} in the entry")
        if not b.wait("!document.querySelector('#bselbar').hidden", 5):
            raise bv.CheckFailed("selecting text showed no toolbar")

    def comments(self):
        code, raw = self.backend.get(f"/api/brain/comments?entry={ENTRY}")
        self.assertEqual(code, 200, raw)
        return json.loads(raw)["comments"]

    def drafts(self):
        code, raw = self.backend.get("/api/brain/drafts")
        return json.loads(raw)["drafts"]

    def no_overflow(self, b, where):
        wide = b.eval("[document.documentElement.scrollWidth, innerWidth]")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_select_and_post_a_comment_launches_nothing(self):
        b = self.open(f"/brain/e/{ENTRY}")
        self.select(b, "Confirm the version.")
        shot("brain-comments-selection-night")
        b.click('#bselbar [data-cm="sel-comment"]')
        if not b.wait("!!document.querySelector('#bshell .b-cm-compose')", 5):
            raise bv.CheckFailed("Comment opened no composer")
        self.type_text(b, '#bshell [data-cmf="text"]', "Which version file? Name it.")
        b.click('#bshell [data-cm="post"]')
        self.wait_text(b, ["Comment saved", "1 open", "Which version file? Name it.", "In place"])
        self.assertEqual(self.drafts(), [], "posting a comment started no agent request")
        record = self.backend.work / "brain" / "comments" / f"{ENTRY}.md"
        self.assertTrue(record.is_file())
        self.assertIn("comment-tester", record.read_text("utf-8"))
        self.assertEqual((self.backend.work / "brain" / f"{ENTRY}.md").read_text("utf-8"), BASE)
        # the passage is marked in the text, and a reload reads it all back
        b.goto(f"/brain/e/{ENTRY}")
        self.wait_text(b, ["Which version file? Name it."])
        if not b.wait("(document.querySelector('#bshell .b-prose mark.b-anchor')?.innerText || '')"
                      ".includes('Confirm the version')", 10):
            raise bv.CheckFailed("the commented passage was not marked in the text")
        b.click('#bshell [data-cm="show"]')
        if not b.wait("document.activeElement?.classList.contains('b-anchor')", 5):
            raise bv.CheckFailed("Show in text did not move focus to the passage")
        shot("brain-comments-thread-night")

    def test_2_a_repeated_line_is_refused_and_nothing_is_written(self):
        b = self.open(f"/brain/e/{ENTRY}")
        before = self.comments()["revision"]
        self.select(b, "Post in the channel.")
        # the toolbar's own context is the whole line: strip it to the quote alone
        b.click('#bselbar [data-cm="sel-comment"]')
        self.type_text(b, '#bshell [data-cmf="quote"]', "Post in the channel")
        self.type_text(b, '#bshell [data-cmf="text"]', "Duplicate?")
        b.click('#bshell [data-cm="post"]')
        self.wait_text(b, ["occurs 2 times"])
        self.assertEqual(self.comments()["revision"], before, "a refused selection wrote nothing")
        shot("brain-comments-ambiguous-night")
        b.click('#bshell [data-cm="cancel"]')

    def test_3_keyboard_ask_agent_apply_resolves_only_its_thread(self):
        b = self.open(f"/brain/e/{ENTRY}")
        b.eval("document.querySelector('#bshell [data-cm=\"pick\"]').focus(); true")
        b.key("Enter")
        if not b.wait("document.activeElement?.classList.contains('b-cm-block')", 5):
            raise bv.CheckFailed("Choose a passage did not focus the first passage")
        self.assertIn("Releases are cut from main", b.eval("document.activeElement.innerText"))
        b.key("Enter")
        if not b.wait("document.activeElement?.dataset.cmf === 'text'", 5):
            raise bv.CheckFailed("choosing a passage did not move to the comment field")
        self.type_text(b, '#bshell [data-cmf="text"]', "Say who cuts a release.")
        self.backend.brain_reply.write_text(reply(), encoding="utf-8")
        b.click('#bshell [data-cm="ask"]')
        if not b.wait("document.querySelector('#bshell [data-cm=\"ask\"]')?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Ask agent did not arm")
        self.assertEqual(self.drafts(), [], "arming launched nothing")
        b.click('#bshell [data-cm="ask"]')
        self.wait_text(b, ["Asked the Brain Agent"])
        self.wait_text(b, ["Draft ready for review"], timeout=40, where="#agentpanel")
        self.assertEqual(len(self.drafts()), 1, "exactly one drafting request")
        draft = self.drafts()[0]
        self.assertIn("Releases are cut from main by whoever is on duty", draft["text"])
        shot("brain-comments-asked-night")
        sel = '#agentpanel [data-bact="ag-apply"]'
        b.click(sel)
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm")
        b.click(sel)
        self.wait_text(b, ["Applied to"], where="#agentpanel")
        b.click('#agentpanel [data-pact="close"]')
        b.goto(f"/brain/e/{ENTRY}")
        if not b.wait("(document.querySelector('#bshell .b-comments')?.innerText || '').includes('Resolved by the applied edit')"
                      " || (document.querySelector('#bshell .b-comments')?.innerText || '').includes('1 resolved')", 20):
            raise bv.CheckFailed("the asked thread was not resolved by its edit: "
                                 + b.eval("document.querySelector('#bshell .b-comments').innerText")[:900])
        c = self.comments()
        states = {t["mode"]: t["state"] for t in c["threads"]}
        self.assertEqual(states, {"comment": "open", "ask": "resolved"},
                         "only the asked thread is resolved by its edit")
        asked = next(t for t in c["threads"] if t["mode"] == "ask")
        self.assertEqual(asked["resolution"]["by"], "edit")
        b.eval("document.querySelector('#bshell .b-cm-closed').open = true; "
               "document.querySelector('#bshell .b-cm-closed').scrollIntoView(); true")
        self.wait_text(b, ["View changes"])
        shot("brain-comments-resolved-night")

    def test_4_desktop_and_narrow_in_both_themes(self):
        for width, height in ((1280, 900), (390, 844)):
            for dark in (True, False):
                word = ("night" if dark else "day") + ("" if width > 400 else "-narrow")
                b = self.open(f"/brain/e/{ENTRY}", width, height, dark)
                self.wait_text(b, ["Which version file? Name it."])
                b.eval("document.querySelector('#bshell .b-comments').scrollIntoView(); true")
                self.no_overflow(b, word)
                shot(f"brain-comments-panel-{word}")
                self.select(b, "summarise")
                self.no_overflow(b, word + " toolbar")
                shot(f"brain-comments-toolbar-{word}")

    def test_5_nothing_else_moved(self):
        self.assertEqual(tree(self.backend.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before, "a card changed")


if __name__ == "__main__":
    unittest.main()
