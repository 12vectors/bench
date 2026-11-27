"""Exact Brain changes through the real page (task 209).

A disposable backend of this case's own, in the external split-Git layout,
with a Brain entry of four sections (one paragraph written twice). The
vendor CLI is the disposable stub (`benchverify.Backend`): it answers a
`brain` round with the update this case wrote — no model is launched. The
case drives the page's own controls by pointer and keyboard and keeps
screenshots to look at:

- **Ask Brain Agent** on the entry → a proposed update, opened on its
  exact changes against the revision it read: the base named, the
  sections changed, every hunk;
- **Apply** (armed) → the panel says what changed and offers **View
  changes**, with saved, committed and shared said apart;
- **View changes** → the entry, its first change (a deletion, said where
  the removed text was) scrolled to and focused; **Next change** by the
  keyboard to an insertion in a list, marked as the whole list and
  nothing beside it; **Previous change**; a reload keeps the address and
  lands on the first change again;
- **Open the conversation** → the same Brain Agent request, Applied;
- the inbox's result link names the change;
- a person's later edit of a changed passage → *edited since*: that
  change is no longer marked, the revision diff is open, and the person's
  text is never highlighted as the agent's;
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

ENTRY = "e-release-checklist"
BASE = f"""# Release checklist

**Brain:** 1
**Id:** {ENTRY}
**Kind:** Guide
**Classification:** Current
**Summary:** How a release is cut.

Releases are cut from main by whoever is on duty.

## Prepare

- update the changelog
- check the version
- tag the commit

## Publish

Check that CI is green.

Confirm the version.

Upload the tarball.

## Announce

Post in the channel.

Post in the channel.
"""

NEW_BODY = ("## Prepare\n\n- update the changelog\n- check the version\n- tag the commit\n"
            "- ship the build notes\n\n## Publish\n\nCheck that CI is green.\n\nConfirm the version.\n\n"
            "Run release.sh, which uploads the tarball.\n\n## Announce\n\n"
            "Post in the channel.\n\nPost in the channel.")


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
           "outcome": "proposal", "summary": "Updated the checklist from the README.",
           "proposal": {
               "action": "update", "target": ENTRY, "title": "Release checklist",
               "kind": "Guide", "summary": "How a release is cut.", "body": NEW_BODY,
               "classification": "Current", "classificationWhy": "the README says so",
               "sources": [{"ref": "app:README.md", "why": "how a release is made"}],
               "claims": [{"statement": "release.sh uploads the tarball", "status": "implemented",
                           "sources": ["app:README.md"]}],
               "uncertainty": "Nothing says who is on duty."}}
    text = json.dumps(doc, indent=1).replace('"round": 0', '"round": __ROUND__') + "\n"
    return "Working.\n<<<BRAIN\n" + text + "BRAIN>>>\n"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


class BrainChanges(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent.resolve() / "brain-changes-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        work = cls.backend.work
        git(work, "config", "user.name", "brain-tester")
        git(work, "config", "user.email", "t@example.com")
        (work / "brain").mkdir(exist_ok=True)
        (work / "brain" / f"{ENTRY}.md").write_text(BASE, encoding="utf-8")
        git(work, "add", "brain")
        git(work, "commit", "-qm", "a Brain entry to change")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(cls.backend.app)
        cls.cards_before = tree(work / "tasks")
        cls.draft = None

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

    def open(self, path, width=1280, height=900, dark=True, fresh=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto(path)
        if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
            raise bv.CheckFailed(f"{path}: the Brain drew no heading")
        if fresh and b.eval("(() => { try { const had = !!sessionStorage.getItem('bench-agent-panel');"
                            " sessionStorage.removeItem('bench-agent-panel'); return had; } catch (e) { return false; } })()"):
            b.goto(path)
            if not b.wait("!!document.querySelector('#bshell .b-title')", 15):
                raise bv.CheckFailed(f"{path}: the Brain drew no heading")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        return b

    def type(self, b, attr, name, text):
        sel = f'#agentpanel [{attr}="{name}"]'
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.focus(); "
               f"f.value = {json.dumps(text)}; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); "
               "f.dispatchEvent(new Event('change', {bubbles: true})); })()")

    def wait_text(self, b, words, timeout=20, where="#agentpanel"):
        cond = " && ".join(f"(document.querySelector({json.dumps(where)})?.innerText || '').includes({json.dumps(w)})"
                           for w in words)
        if not b.wait(cond, timeout):
            seen = b.eval(f"(document.querySelector({json.dumps(where)}) || {{}}).innerText || ''")
            raise bv.CheckFailed(f"never showed {words} in {where}: " + " | ".join(seen.split("\n"))[:1200])

    def armed_click(self, b, act, where="#agentpanel"):
        sel = f'{where} [data-bact="{act}"]'
        b.click(sel)
        if not b.wait(f"document.querySelector({json.dumps(sel)})?.classList.contains('armed')", 5):
            raise bv.CheckFailed(f"{act} did not arm")
        b.click(sel)

    def focused_change(self, b):
        return b.eval("(() => { const a = document.activeElement; const c = a && (a.closest('.b-chg') || a.closest('.b-chglist > li'));"
                      " if (!c) return null; const r = c.getBoundingClientRect();"
                      " return {text: c.innerText, top: r.top, bottom: r.bottom, h: innerHeight,"
                      " marked: c.classList.contains('b-chg')}; })()")

    def entry_text(self) -> str:
        return (self.backend.work / "brain" / f"{ENTRY}.md").read_text(encoding="utf-8")

    def no_overflow(self, b, where):
        wide = b.eval("[document.documentElement.scrollWidth, innerWidth]")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_review_the_exact_changes_then_apply(self):
        b = self.open(f"/brain/e/{ENTRY}")
        b.click('#bshell [data-bact="ag-ask"]')
        if not b.wait("!!document.querySelector('#agentpanel:not([hidden]) [data-ag=\"text\"]')", 5):
            raise bv.CheckFailed("Ask Brain Agent opened no composer")
        self.type(b, "data-ag", "text", "Bring the checklist in line with release.sh.")
        self.backend.brain_reply.write_text(reply(), encoding="utf-8")
        self.armed_click(b, "ag-generate")
        self.wait_text(b, ["Draft ready for review"])
        # an update opens on its changes against the exact revision it read
        self.wait_text(b, ["the exact entry this draft read", "Sections changed", "Prepare", "Publish",
                           "+ - ship the build notes", "− Upload the tarball."])
        self.assertTrue(b.eval("document.querySelector('#agentpanel [data-bact=\"ag-view\"][data-barg=\"changes\"]')"
                               ".getAttribute('aria-pressed') === 'true'"), "the Changes view is the one shown")
        self.assertEqual(self.entry_text(), BASE, "reviewing changed nothing")
        shot("brain-changes-review-night")
        self.armed_click(b, "ag-apply")
        self.wait_text(b, ["Applied to", "View changes", "Saved"])
        code, raw = self.backend.get("/api/brain/drafts")
        type(self).draft = json.loads(raw)["drafts"][0]["id"]
        shot("brain-changes-applied-night")

    def test_2_view_changes_leads_to_each_passage(self):
        draft = type(self).draft
        self.assertTrue(draft, "the journey above applied a change")
        b = self.open("/brain", fresh=False)
        # the inbox's result link names the change (it opens the entry)
        deadline, href = time.time() + 20, None
        while time.time() < deadline:
            code, raw = self.backend.get("/api/attention")
            item = next((i for i in json.loads(raw).get("items", []) if i["id"] == f"brain:{draft}"), None)
            if item and item.get("state") == "applied":
                href = item["href"]
                break
            time.sleep(0.4)
        self.assertEqual(href, f"/brain/e/{ENTRY}?change={draft}")
        b.goto(href)
        if not b.wait("!!document.querySelector('#bshell .b-chgnav [data-bact=\"chg-next\"]')", 15):
            raise bv.CheckFailed("View changes drew no navigator: "
                                 + b.eval("document.querySelector('#bshell').innerText")[:900])
        if not b.wait("!!document.activeElement?.closest('.b-chg')", 10):
            raise bv.CheckFailed("the first change was not focused")
        first = self.focused_change(b)
        self.assertIn("Change 1 of", first["text"])
        self.assertIn("Releases are cut from main", first["text"], "the deletion says what was removed, where")
        self.assertGreaterEqual(first["bottom"], 0)
        self.assertLessEqual(first["top"], first["h"], "the first change is scrolled into view")
        self.wait_text(b, ["The entry is still the revision this change wrote", "Saved"], where="#bshell")
        shot("brain-changes-first-night")
        # Next, by the keyboard
        b.eval("document.querySelector('#bshell [data-bact=\"chg-next\"]').focus(); true")
        b.key("Enter")
        if not b.wait("(document.activeElement?.closest('.b-chg')?.innerText || '').includes('Change 2 of')", 5):
            raise bv.CheckFailed("Next change did not move to the second change")
        second = self.focused_change(b)
        self.assertTrue(second["marked"], "the insertion is marked in the entry itself")
        self.assertIn("ship the build notes", second["text"])
        self.assertIn("update the changelog", second["text"], "the whole list is the marked block")
        self.assertNotIn("Upload", second["text"], "nothing beside the change is marked as it")
        self.assertLessEqual(second["top"], second["h"])
        shot("brain-changes-second-night")
        b.click('#bshell [data-bact="chg-next"]')
        if not b.wait("(document.activeElement?.closest('.b-chg')?.innerText || '').includes('Change 3 of')", 5):
            raise bv.CheckFailed("Next change did not reach the third change")
        self.assertIn("release.sh", self.focused_change(b)["text"])
        b.click('#bshell [data-bact="chg-prev"]')
        if not b.wait("(document.activeElement?.closest('.b-chg')?.innerText || '').includes('Change 2 of')", 5):
            raise bv.CheckFailed("Previous change did not go back")
        # the address survives a reload, and lands on the first change again
        b.goto(f"/brain/e/{ENTRY}?change={draft}")
        if not b.wait("(document.activeElement?.closest('.b-chg')?.innerText || '').includes('Change 1 of')", 15):
            raise bv.CheckFailed("a reload did not reopen the first change")
        self.assertEqual(b.eval("location.search"), f"?change={draft}")
        # back to the very conversation that made it
        b.click('#bshell [data-bact="ag-open"]')
        self.wait_text(b, ["Brain Agent · Applied", "Bring the checklist in line"])
        b.click('#agentpanel [data-pact="close"]')
        b.wait("document.querySelector('#agentpanel').hidden", 5)

    def test_3_desktop_and_narrow_in_both_themes(self):
        draft = type(self).draft
        for width, height in ((1280, 900), (390, 844)):
            for dark in (True, False):
                word = ("night" if dark else "day") + ("" if width > 400 else "-narrow")
                b = self.open(f"/brain/e/{ENTRY}?change={draft}", width, height, dark, fresh=False)
                if not b.wait("!!document.activeElement?.closest('.b-chg')", 15):
                    raise bv.CheckFailed(f"{word}: the first change was not focused")
                self.no_overflow(b, word)
                self.assertIn("removed", self.focused_change(b)["text"])
                shot(f"brain-changes-entry-{word}")
                b.eval("document.querySelector('#bshell #bchg-diff').open = true; "
                       "document.querySelector('#bshell #bchg-diff').scrollIntoView(); true")
                self.no_overflow(b, word + " diff")
                shot(f"brain-changes-diff-{word}")

    def test_4_a_later_edit_is_said_and_never_marked_as_the_agents(self):
        draft = type(self).draft
        code, raw = self.backend.get(f"/api/brain/entry?id={ENTRY}")
        e = json.loads(raw)["entry"]
        body = e["body"].replace("Run release.sh, which uploads the tarball.",
                                 "A person rewrote this publishing step.")
        code, raw = self.backend.post("/api/brain/edit", {
            "request": f"case-209-edit-{int(time.time())}", "id": ENTRY, "expected": e["revision"],
            "title": e["title"], "kind": e["entryKind"], "summary": e["summary"],
            "sources": [s["text"] for s in e["sources"]], "related": [], "body": body})
        self.assertEqual(code, 200, raw)
        b = self.open(f"/brain/e/{ENTRY}?change={draft}", fresh=False)
        self.wait_text(b, ["has been edited since"], where="#bshell")
        self.assertTrue(b.eval("document.querySelector('#bshell #bchg-diff').open"),
                        "the revision diff is open once the entry has moved on")
        marked = b.eval("[...document.querySelectorAll('#bshell .b-chg')].map(c => c.innerText).join('\\n')")
        self.assertNotIn("A person rewrote", marked, "a person's later text is never marked as the change")
        self.wait_text(b, ["not placed in the current text"], where="#bshell")
        self.assertIn("Run release.sh", b.eval("document.querySelector('#bshell #bchg-diff').innerText"),
                      "the historical diff is what was applied")
        shot("brain-changes-edited-since-night")

    def test_5_nothing_else_moved(self):
        self.assertEqual(tree(self.backend.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before, "a card changed")


if __name__ == "__main__":
    unittest.main()
