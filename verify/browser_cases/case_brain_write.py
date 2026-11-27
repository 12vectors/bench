"""The Brain, written through the real page (task 158).

A disposable backend of this case's own, in the external split-Git layout,
with `BOARD_COMMIT_MOVES` on, and one existing entry in its `brain/`. The
case drives the page's own buttons as a person would, and takes
screenshots to look at:

- **create**: New entry, typed title, summary and body, Preview then
  Write, Save — the page lands on the new entry's stable address and says
  it was saved and committed, and that it is not shared (sync is off);
- **edit and cancel**: Cancel changes nothing on disk; Save changes the
  title and keeps the id;
- **a conflict**: the file changes on disk while the editor is open; Save
  is refused with both versions shown, the draft kept, and "Use the
  current version and keep my draft" lets the person save over what they
  have now seen;
- **classify**: Superseded without a replacement is refused in place, the
  draft kept; a reason and a replacement save and appear in the history;
- **the override**, set and cleared, and the **revisions** fold;
- **retire**, armed then confirmed: a tombstone at the same address;
- **a failed commit**: saved, said to have failed, and Retry commit
  commits it;
- **keyboard, narrow and both themes**: New entry by keyboard, a visible
  focus ring, nothing overflowing at 390px;
- and nothing launched: no agent ran, and the application and the cards
  are byte for byte what they were.
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


OLD = ("# Old routing notes\n\n**Brain:** 1\n**Id:** e-old-notes\n**Kind:** Guide\n"
       "**Classification:** Current\n**Summary:** Early notes.\n\nThe first notes.\n")


# The page's GETs of one Brain entry, held until the case releases them:
# each is still answered by the real backend, only later. Nothing else the
# page asks is touched.
GATE_ENTRY_READS = """(() => {
  const real = window.fetch.bind(window);
  const gate = { held: [], hold: true };
  gate.release = () => { gate.hold = false; gate.held.splice(0).forEach(go => go()); };
  gate.restore = () => { gate.release(); window.fetch = real; };
  window.__brainGate = gate;
  window.fetch = (url, opts) => {
    if (gate.hold && String(url).startsWith('/api/brain/entry')) {
      return new Promise(resolve => gate.held.push(() => resolve(real(url, opts))));
    }
    return real(url, opts);
  };
  return true;
})()"""


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class BrainWriting(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-write-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        # Backend sets its own defaults over the environment it is given, so
        # committing board edits is turned on here, after it, or not at all
        cls.backend.env["BOARD_COMMIT_MOVES"] = "1"
        work = cls.backend.work
        git(work, "config", "user.name", "brain-tester")
        git(work, "config", "user.email", "t@example.com")
        (work / "brain").mkdir()
        (work / "brain" / "old-notes.md").write_text(OLD, encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "brain fixture")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(cls.backend.app)
        cls.cards_before = tree(work / "tasks")
        cls.entry_id = None        # never `id`: that is TestCase.id()

    @classmethod
    def tearDownClass(cls):
        b = S["browser"]
        try:
            b.eval("localStorage.removeItem('bench-theme')")
        except (bv.CheckFailed, bv.SetupError):
            pass
        b.origin = cls.origin
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

    def type(self, b, field, text):
        """Focus the field and replace its text, firing the input event a
        keyboard would (both browser transports support this)."""
        sel = f'#bshell [data-bf="{field}"]'
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.focus(); "
               f"f.value = {json.dumps(text)}; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); })()")

    def choose(self, b, field, value):
        sel = f'#bshell [data-bf="{field}"]'
        b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel)}); f.value = {json.dumps(value)};"
               " f.dispatchEvent(new Event('change', {bubbles: true})); })()")

    def text(self, b):
        return b.eval("document.querySelector('#bshell').innerText")

    def wait_text(self, b, words, timeout=12):
        cond = " && ".join(f"document.querySelector('#bshell').innerText.includes({json.dumps(w)})"
                           for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b)[:1500]}")

    def entry_file(self):
        return self.backend.work / "brain" / f"{type(self).entry_id}.md"

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_create_with_preview_and_save(self):
        b = self.open("/brain")
        # New entry opens the Brain Agent's composer (task 175); writing it
        # yourself is its secondary action, and opens the same form as before
        b.click('#bshell [data-bact="ag-new"]')
        # the composer opens in the shared panel beside the Brain (task 205)
        if not b.wait("!!document.querySelector('#agentpanel [data-bact=\"ag-manual\"]')", 5):
            raise bv.CheckFailed("New entry opened no composer")
        b.click('#agentpanel [data-bact="ag-manual"]')
        if not b.wait("!!document.querySelector('#bshell .b-form [data-bf=\"title\"]')", 5):
            raise bv.CheckFailed("New entry opened no form")
        self.type(b, "title", "How Brain writes are protected")
        self.type(b, "summary", "Revision-bound saves, one file each.")
        self.type(b, "body", "Every save names the revision it read.\n\n## Why\n\n- no lost edits\n")
        b.click('#bshell [data-bact="preview"]')
        if not b.wait("!!document.querySelector('#bshell .b-preview li')", 5):
            raise bv.CheckFailed("Preview drew no Markdown")
        shot("brain-write-create-preview-night")
        b.click('#bshell [data-bact="write"]')
        self.assertIn("no lost edits", b.eval("document.querySelector('#bshell [data-bf=\"body\"]').value"),
                      "the draft survived Preview")
        b.click('#bshell [data-bact="save"]')
        if not b.wait("location.pathname.startsWith('/brain/e/e-how-brain-writes-are-protected-')", 10):
            raise bv.CheckFailed("Save did not land on the new entry: " + self.text(b)[:800])
        type(self).entry_id = b.eval("location.pathname.split('/').pop()")
        self.wait_text(b, ["Saved", "Committed", "Not shared", "How Brain writes are protected"])
        self.assertTrue(self.entry_file().exists())
        self.assertIn(f"board: brain {type(self).entry_id} created",
                      git(self.backend.work, "log", "-1", "--format=%s").stdout)
        shot("brain-write-created-night")
        b.eval("S.dark = false; applyTheme(); true")
        shot("brain-write-created-day")

    def test_2_cancel_changes_nothing_and_save_edits_in_place(self):
        b = self.open(f"/brain/e/{type(self).entry_id}")
        before = self.entry_file().read_bytes()
        b.click('#bshell [data-bact="edit"]')
        self.type(b, "title", "A title nobody saves")
        b.click('#bshell [data-bact="cancel"]')
        b.wait("!document.querySelector('#bshell .b-form')", 5)
        self.assertEqual(self.entry_file().read_bytes(), before, "Cancel wrote")
        b.click('#bshell [data-bact="edit"]')
        self.type(b, "title", "How Brain writes stay safe")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["How Brain writes stay safe", "edited"])
        self.assertEqual(b.eval("location.pathname"), f"/brain/e/{type(self).entry_id}")

    def test_3_a_concurrent_change_is_a_conflict_that_keeps_the_draft(self):
        b = self.open(f"/brain/e/{type(self).entry_id}")
        b.click('#bshell [data-bact="edit"]')
        self.type(b, "body", "My careful rewrite.")
        path = self.entry_file()
        path.write_text(path.read_text().replace("Every save names", "A teammate says every save names"))
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Not saved: the entry changed", "What you tried to save", "On disk now",
                           "A teammate says"])
        self.assertEqual(b.eval("document.querySelector('#bshell [data-bf=\"body\"]').value"),
                         "My careful rewrite.", "the draft is kept")
        self.assertIn("A teammate says", path.read_text(), "nothing was overwritten")
        shot("brain-write-conflict-night")
        # Hold the page's re-read of the entry, so the moment between "Use
        # the current version" and the reading landing is observable rather
        # than a race: the board answers for real, the page just gets the
        # answer when the case releases it.
        b.eval(GATE_ENTRY_READS)
        # by keyboard: save() put focus on the conflict panel; Tab reaches
        # its first control, Enter presses it
        self.assertTrue(b.wait("document.activeElement && document.activeElement.matches('#bshell .b-conflict')", 5),
                        "the refused save focused the conflict panel")
        b.key("Tab")
        self.assertEqual(b.eval("document.activeElement.dataset.bact"), "reread",
                         "Tab from the conflict reaches 'Use the current version'")
        b.key("Enter")
        if not b.wait("!document.querySelector('#bshell .b-conflict')"
                      " && window.__brainGate.held.length > 0", 5):
            raise bv.CheckFailed("'Use the current version' asked for no fresh reading: " + self.text(b)[:800])
        # while the reading is outstanding: the draft, a usable focus, a
        # neutral note, Save held — and never the stale refusal warning
        during = self.text(b)
        self.assertNotIn("Saving will be refused", during,
                         "the old reading's revision raised the stale-entry warning")
        self.assertIn("Reading the current version", during)
        self.assertTrue(b.eval("document.querySelector('#bshell [data-bact=\"save\"]').disabled"),
                        "Save is offered before the current revision is known")
        self.assertEqual(b.eval("document.activeElement && document.activeElement.dataset.bf"), "title",
                         "focus fell out of the form when its button went away")
        self.assertEqual(b.eval("document.querySelector('#bshell [data-bf=\"body\"]').value"),
                         "My careful rewrite.", "the draft is kept while re-reading")
        shot("brain-write-conflict-rereading-night")
        b.eval("window.__brainGate.release(); true")
        if not b.wait("!document.querySelector('#bshell .b-rereading')"
                      " && !document.querySelector('#bshell [data-bact=\"save\"]').disabled", 10):
            raise bv.CheckFailed("the current version never settled: " + self.text(b)[:800])
        after = self.text(b)
        self.assertNotIn("Saving will be refused", after, "the settled base still disagrees with disk")
        self.assertNotIn("Not saved", after)
        self.assertEqual(b.eval("document.activeElement && document.activeElement.dataset.bf"), "title",
                         "the reading landing took focus away")
        self.assertEqual(b.eval("document.querySelector('#bshell [data-bf=\"body\"]').value"),
                         "My careful rewrite.", "the draft survived the reading")
        b.eval("window.__brainGate.restore(); true")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["My careful rewrite.", "edited"])
        self.assertIn("My careful rewrite.", path.read_text(), "the save after re-reading landed")

    def test_4_classify_refuses_then_records_a_reason(self):
        b = self.open("/brain/e/e-old-notes")
        b.click('#bshell [data-bact="classify"]')
        b.click('#bshell input[data-bf="classification"][value="Superseded"]')
        self.assertTrue(b.wait("!!document.querySelector('#bshell [data-bf=\"supersededBy\"]')", 5))
        self.type(b, "reason", "folded into the new guide")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Not saved", "replaces this one"])
        self.assertEqual(b.eval("document.querySelector('#bshell [data-bf=\"reason\"]').value"),
                         "folded into the new guide")
        self.choose(b, "supersededBy", type(self).entry_id)
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Superseded by", "classified Superseded", "folded into the new guide"])
        shot("brain-write-classified-night")

    def test_5_override_set_clear_and_revisions(self):
        b = self.open(f"/brain/e/{type(self).entry_id}")
        b.click('#bshell [data-bact="override"]')
        self.type(b, "reason", "a person decided this")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Manual override", "manual override set"])
        b.click('#bshell [data-bact="override"]')
        self.type(b, "reason", "automation may assess it")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["manual override cleared"])
        b.click('#bshell .b-revs summary')
        self.wait_text(b, ["override cleared", "created"])

    def test_6_a_failed_commit_is_said_and_retried(self):
        lock = self.backend.work / ".git" / "index.lock"
        b = self.open(f"/brain/e/{type(self).entry_id}")
        lock.write_text("")
        try:
            b.click('#bshell [data-bact="edit"]')
            self.type(b, "summary", "Saved while git was busy.")
            b.click('#bshell [data-bact="save"]')
            self.wait_text(b, ["Commit failed", "nothing was rolled back", "Retry commit"], 20)
            self.assertIn("Saved while git was busy.", self.entry_file().read_text())
            shot("brain-write-commit-failed-night")
        finally:
            lock.unlink(missing_ok=True)
        b.click('#bshell [data-bact="retry-commit"]')
        self.wait_text(b, ["Committed"])
        self.assertNotIn("Commit failed", self.text(b))

    def test_7_retire_is_armed_and_keeps_a_tombstone(self):
        b = self.open(f"/brain/e/{type(self).entry_id}")
        b.click('#bshell [data-bact="retire"]')
        self.type(b, "reason", "replaced by the handbook")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Confirm: retire this entry"])
        self.assertTrue(self.entry_file().exists())
        self.assertNotIn("Removed", self.entry_file().read_text())
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["This entry was removed", "replaced by the handbook"])
        b.goto(f"/brain/e/{type(self).entry_id}")
        self.wait_text(b, ["This entry was removed"])
        shot("brain-write-retired-night")

    def test_8_keyboard_narrow_and_both_themes(self):
        for dark in (True, False):
            b = self.open("/brain", 390, 844, dark)
            b.eval("document.querySelector('#bshell .b-title').focus()")
            b.key("Tab")
            self.assertEqual(b.eval("document.activeElement.dataset.bact"), "ag-new")
            ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
            self.assertNotEqual(ring, "none", "the focused button shows a ring")
            b.key("Enter")
            if not b.wait("!!document.querySelector('#agentpanel .b-form')", 5):
                raise bv.CheckFailed("Enter on New entry opened no form")
            self.no_overflow(b, f"composer narrow {'night' if dark else 'day'}")
            shot(f"brain-write-composer-narrow-{'night' if dark else 'day'}")
            b.click('#agentpanel [data-bact="ag-manual"]')
            if not b.wait("!!document.querySelector('#bshell .b-form [data-bf=\"title\"]')", 5):
                raise bv.CheckFailed("Write it yourself opened no form")
            self.no_overflow(b, f"form narrow {'night' if dark else 'day'}")
            shot(f"brain-write-form-narrow-{'night' if dark else 'day'}")
            b.click('#bshell [data-bact="cancel"]')
            b.goto("/brain/e/e-old-notes")
            b.wait("!!document.querySelector('#btitle')", 10)
            self.no_overflow(b, "entry narrow")

    def test_9_nothing_else_moved_and_nothing_launched(self):
        self.assertEqual(tree(self.backend.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before, "a card changed")
        code, raw = self.backend.get("/api/state")
        state = json.loads(raw)
        self.assertEqual(state.get("agents") or [], [], "an agent ran")


if __name__ == "__main__":
    unittest.main()
