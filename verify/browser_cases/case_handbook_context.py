"""The project handbook a run would be given, in the launch check (task 203).

A disposable backend of this case's own, in the external split-Git layout,
with a `brain/` holding an overview, an inferred convention in Ways of
working and a Historical design note, linked from the handbook record.
The case opens a card's launch check (⚿ in its tools) and reads:

- **populated**: one quiet *Handbook supplied* row, the selection one fold
  away — opened by keyboard — saying what is supplied, what is only
  indexed (the Historical note), that the convention is not approved
  policy, and that supplied bytes are not proof the run read them;
- **empty**: the record emptied on disk and **Check again** reads *No
  handbook content yet*, with nothing drawn as a failure;
- **unreadable**: the record replaced by a link reads *Handbook could not
  be read* as a note, not an alert, and the launch is still offered;
- **narrow, both themes**: nothing overflows at 390px; screenshots;
- and nothing launched or pinned: no context bundle was written, and the
  application and the cards are unchanged.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

import benchverify as bv

S = bv.SESSION
DIALOG = "#sheetwrap [role=dialog]"
CARD = "03-measure-the-board-layout.md"
PANEL = f"{DIALOG} .hbctx"


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


def entry(entry_id, title, body, klass="Current", extra=""):
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {entry_id}\n**Kind:** Guide\n"
            f"**Classification:** {klass}\n**Summary:** {title}.\n{extra}\n{body}\n")


RECORD = ("# Project handbook\n\n**Brain handbook:** 1\n\n## Overview\n\n- e-hc-overview\n\n"
          "## How it works\n\n- e-hc-old\n\n## Ways of working\n\n- e-hc-habits\n")
EMPTY = "# Project handbook\n\n**Brain handbook:** 1\n"


class HandbookContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "handbook-context-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        work, app = cls.backend.work, cls.backend.app
        brain = work / "brain"
        (brain / "handbook").mkdir(parents=True)
        files = {
            "e-hc-overview": entry("e-hc-overview", "What this project is",
                                   "It is a board for coding agents.",
                                   extra="**Standing:** implemented\n"),
            "e-hc-old": entry("e-hc-old", "The first design", "It ran phases.",
                              klass="Historical"),
            "e-hc-habits": entry("e-hc-habits", "Small commits", "Commit small.",
                                 extra="**Policy:** inferred — seen in history\n"),
        }
        for name, text in files.items():
            (brain / f"{name}.md").write_text(text, encoding="utf-8")
        cls.record = brain / "handbook" / "handbook.md"
        cls.record.write_text(RECORD, encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "handbook context fixture")
        cls.backend.start()
        cls.origin = S["browser"].origin
        cls.app_before = tree(app)
        cls.cards_before = tree(work / "tasks")
        cls.context_dir = cls.backend.tool / "manager" / "local" / "state" / "context"
        cls.context_before = tree(cls.context_dir) if cls.context_dir.exists() else {}

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

    def setUp(self):
        self.record.unlink(missing_ok=True)
        self.record.write_text(RECORD, encoding="utf-8")

    # ── helpers ──

    def open_launch(self, width=1280, height=900, dark=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto("/board")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its five stage columns")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        sel = f'#board article.card[data-file="{CARD}"]'
        if not b.wait(f"!!document.querySelector('{sel}')", 10):
            raise bv.CheckFailed(f"{CARD} is not on the board")
        chip = f"{sel} .cardtools.open [data-profile]"
        for _ in range(4):
            if not b.eval(f"!!document.querySelector('{sel} .cardtools.open')"):
                b.click(f"{sel} [data-more]")
            if b.wait(f"!!document.querySelector('{chip}')", 3):
                break
        else:
            raise bv.CheckFailed(f"{CARD}'s tools carry no launch check")
        b.click(chip)
        if not b.wait(f"!!document.querySelector('{PANEL}')", 30):
            raise bv.CheckFailed("the launch check drew no handbook row: " + self.text(b)[:800])
        return b

    def text(self, b, sel=DIALOG):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def again(self, b, words):
        b.click(f"{DIALOG} #lc-again")
        cond = " && ".join(f"(document.querySelector({json.dumps(PANEL)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, 20):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, PANEL)}")

    def assert_quiet(self, b):
        """Guidance is never drawn as a failure or an alert."""
        self.assertFalse(b.eval(f"!!document.querySelector('{PANEL} [role=alert], "
                                f"{PANEL} .bad, {PANEL} .fail')"))

    # ── cases ──

    def test_populated_reads_quietly_and_the_detail_opens_by_keyboard(self):
        b = self.open_launch()
        row = self.text(b, PANEL)
        self.assertIn("Handbook supplied", row)
        self.assertIn("2 entries supplied", row)
        self.assert_quiet(b)
        b.eval(f"document.querySelector('{PANEL} details summary').focus()")
        b.key("Enter")
        if not b.wait(f"document.querySelector('{PANEL} details').open", 5):
            raise bv.CheckFailed("Enter on the summary did not open the selection")
        detail = self.text(b, f"{PANEL} pre")
        for words in ("What this project is", "indexed only", "The first design",
                      "an inferred convention, not approved policy",
                      "standing: implemented", "not observed"):
            self.assertIn(words, detail)
        # identities, not text: the page never carries an entry's body
        self.assertNotIn("It is a board for coding agents.", self.text(b))
        shot("handbook-context-populated-1280-dark")
        b.eval("S.dark = false; applyTheme(); true")
        shot("handbook-context-populated-1280-light")

    def test_empty_reads_no_content_yet(self):
        b = self.open_launch()
        self.record.write_text(EMPTY, encoding="utf-8")
        self.again(b, ["No handbook content yet"])
        self.assertIn("starts normally", self.text(b, PANEL))
        self.assert_quiet(b)
        self.assertTrue(b.eval(f"!!document.querySelector('{DIALOG} #lc-launch')"),
                        "an empty handbook held the launch")
        shot("handbook-context-empty-1280-dark")

    def test_an_unreadable_record_is_a_note_and_the_launch_stays_offered(self):
        b = self.open_launch(dark=False)
        target = self.backend.base / "elsewhere.md"
        target.write_text(RECORD, encoding="utf-8")
        self.record.unlink()
        self.record.symlink_to(target)
        self.again(b, ["Handbook could not be read"])
        self.assert_quiet(b)
        self.assertTrue(b.eval(f"!!document.querySelector('{DIALOG} #lc-launch')"),
                        "an unreadable handbook held the launch")
        b.eval(f"document.querySelector('{PANEL} details').open = true")
        self.assertIn("handbook-refused", self.text(b, f"{PANEL} pre"))
        shot("handbook-context-unreadable-1280-light")

    def test_narrow_in_both_themes(self):
        for dark in (True, False):
            b = self.open_launch(390, 844, dark=dark)
            b.eval(f"document.querySelector('{PANEL} details').open = true")
            width = b.eval("document.documentElement.scrollWidth")
            self.assertLessEqual(width, 390, "the launch check overflows at 390px")
            box = b.eval(f"(() => {{ const r = document.querySelector('{PANEL}')"
                         ".getBoundingClientRect(); return [r.left, r.right]; })()")
            self.assertGreaterEqual(box[0], 0)
            self.assertLessEqual(box[1], 391)
            shot(f"handbook-context-narrow-390-{'dark' if dark else 'light'}")
            b.key("Escape")

    def test_zz_nothing_was_launched_or_pinned(self):
        self.assertEqual(tree(self.backend.app), self.app_before)
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before)
        now = tree(self.context_dir) if self.context_dir.exists() else {}
        self.assertEqual(now, self.context_before, "an inspection pinned a bundle")


if __name__ == "__main__":
    unittest.main()
