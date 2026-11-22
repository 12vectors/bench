"""Adopting legacy `reference/` material through the real page (task 172).

A disposable backend of this case's own, in the external split-Git layout
with `BOARD_COMMIT_MOVES` on and a mixed `reference/`. The case drives the
page's own controls:

- **the inventory**: Markdown, image, PDF and HTML prototype listed with
  their types, a credential-bearing file and a link refused, and opening the
  list and a preview writes nothing;
- **catalogue**: two presses make one Unclassified entry, the original still
  served byte for byte;
- **explanation**: a separate entry, its difference from the file shown;
- **classify by hand**: the person's word, on the ordinary entry page;
- **drift**: a file changed under an open preview is refused, nothing written;
- **changed source**: shown with its difference, re-recorded with a reason,
  the person's classification kept;
- **keyboard, narrow, both themes**: nothing overflows at 390px; screenshots.
"""

import hashlib
import json
import os
import shutil
import subprocess
import unittest
import urllib.request

import benchverify as bv

S = bv.SESSION
GUIDE = "# Payments API\n\nCall it with a key.\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
HTML = b"<!doctype html><script>window.pwned = 1</script><h1>Prototype</h1>\n"


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
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
            and ".git" not in p.parts}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class Adoption(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "brain-adoption-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        cls.backend.env["BOARD_COMMIT_MOVES"] = "1"
        work = cls.backend.work
        git(work, "config", "user.name", "adoption-tester")
        git(work, "config", "user.email", "t@example.com")
        cls.ref = work / "reference"
        cls.ref.mkdir(exist_ok=True)
        (cls.ref / "api.md").write_text(GUIDE)
        (cls.ref / "screen.png").write_bytes(PNG)
        (cls.ref / "spec.pdf").write_bytes(PDF)
        (cls.ref / "proto.html").write_bytes(HTML)
        (cls.ref / "leak.md").write_text("api_key = sk-abcdefghijklmnopqrstuvwxyz123456\n")
        outside = base / "outside.md"
        outside.write_text("# private\n")
        os.symlink(outside, cls.ref / "escape.md")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "adoption fixture")
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
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    # ── helpers ──

    def open(self, width=1280, height=900, dark=True):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        b.goto("/brain")
        if not b.wait("!!document.querySelector('#bshell [data-bact=\"ad-open\"]')", 15):
            raise bv.CheckFailed("the Brain offered no reference review: " + self.text(b)[:800])
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
        b.click('#bshell [data-bact="ad-open"]')
        if not b.wait("!!document.querySelector('#bshell .b-adlist')", 10):
            raise bv.CheckFailed("the inventory never drew: " + self.text(b)[:800])
        return b

    def text(self, b, sel="#bshell"):
        return b.eval(f"document.querySelector({json.dumps(sel)})?.innerText || ''")

    def wait_text(self, b, words, sel="#bshell", timeout=12):
        cond = " && ".join(f"(document.querySelector({json.dumps(sel)})?.innerText || '')"
                           f".includes({json.dumps(w)})" for w in words)
        if not b.wait(cond, timeout):
            raise bv.CheckFailed(f"never showed {words}: {self.text(b, sel)[:1500]}")

    def pick(self, b, name):
        b.click(f'#bshell [data-bact="ad-pick"][data-barg="{name}"]')
        if not b.wait("!!document.querySelector('#bshell .b-adops, #bshell .b-adopt [role=alert]')", 10):
            raise bv.CheckFailed(f"{name}: no preview: " + self.text(b)[:800])

    def choose(self, b, op):
        b.click(f'#bshell [data-adop="{op}"]')
        b.wait(f"document.querySelector('#bshell [data-adop=\"{op}\"]')?.checked", 5)

    def fill(self, b, field, value):
        b.eval(f"(() => {{ const f = document.querySelector('#bshell [data-ad=\"{field}\"]'); "
               f"f.focus(); f.value = {json.dumps(value)}; "
               "f.dispatchEvent(new Event('input', {bubbles: true})); "
               "f.dispatchEvent(new Event('change', {bubbles: true})); return true; })()")

    def apply(self, b):
        b.click('#bshell [data-bact="ad-confirm"]')
        if not b.wait("!!document.querySelector('#bshell [data-bact=\"ad-apply\"]')", 5):
            raise bv.CheckFailed("Apply asked for no confirmation: " + self.text(b)[:800])
        b.click('#bshell [data-bact="ad-apply"]')

    def entries(self):
        folder = self.backend.work / "brain"
        return sorted(p.name for p in folder.glob("a-*.md")) if folder.exists() else []

    def no_overflow(self, b, where):
        wide = b.eval("(() => { const s = document.querySelector('#bshell');"
                      " return [s.scrollWidth, s.clientWidth, document.documentElement.scrollWidth,"
                      " innerWidth]; })()")
        self.assertLessEqual(wide[0], wide[1] + 1, f"{where}: the Brain view overflows {wide}")
        self.assertLessEqual(wide[2], wide[3] + 1, f"{where}: the page overflows {wide}")

    # ── the journey, in order ──

    def test_1_inventory_and_preview_write_nothing(self):
        before = tree(self.backend.work)
        head = git(self.backend.work, "rev-parse", "HEAD").stdout
        b = self.open()
        listed = self.text(b, "#bshell .b-adlist")
        for words in ("api.md", "markdown", "screen.png", "image", "spec.pdf", "pdf",
                      "proto.html", "html"):
            self.assertIn(words, listed)
        leak = self.text(b, '#bshell [data-aditem="leak.md"]')
        self.assertIn("Excluded", leak)
        self.assertIn("credential", leak)
        self.assertNotIn("sk-abc", self.text(b))
        self.assertIn("link", self.text(b, '#bshell [data-aditem="escape.md"]'))
        self.pick(b, "proto.html")
        self.assertNotIn("Prototype", self.text(b, "#bshell .b-adform"),
                         "the prototype's content is not imported into the form")
        self.assertFalse(b.eval("!!window.pwned"), "the prototype's script ran")
        shot("brain-adoption-preview-night")
        self.assertEqual(tree(self.backend.work), before)
        self.assertEqual(git(self.backend.work, "rev-parse", "HEAD").stdout, head)

    def test_2_catalogue_makes_one_entry_and_keeps_the_original(self):
        b = self.open()
        self.pick(b, "api.md")
        self.assertTrue(b.eval("document.querySelector('#bshell [data-adop=\"catalogue\"]').checked"),
                        "catalogue is the default")
        self.assertTrue(b.eval("document.querySelector('#bshell [data-adop=\"explanation\"]').disabled"))
        self.apply(b)
        self.wait_text(b, ["Saved", "Committed"], "#bshell .b-adopt")
        self.assertEqual(len(self.entries()), 1)
        self.wait_text(b, ["Catalogued"], '#bshell [data-aditem="api.md"]')
        text = (self.backend.work / "brain" / self.entries()[0]).read_text()
        self.assertIn("**Classification:** Unclassified", text)
        with urllib.request.urlopen(self.backend.url + "/files/reference/api.md", timeout=10) as res:
            self.assertEqual(res.read().decode(), GUIDE)

    def test_3_explanation_is_a_separate_entry_with_its_difference_shown(self):
        b = self.open()
        self.pick(b, "api.md")
        self.choose(b, "explanation")
        self.fill(b, "body", "# Payments API\n\nOur maintained account.\n")
        self.wait_text(b, ["Our maintained account"], "#bshell [data-addiff]")
        self.assertTrue(b.eval("!!document.querySelector('#bshell [data-addiff] .b-diff-del')"))
        shot("brain-adoption-explanation-night")
        self.apply(b)
        self.wait_text(b, ["Saved", "Committed"], "#bshell .b-adopt")
        self.assertEqual(len(self.entries()), 2)

    def test_4_a_person_classifies_it_on_the_entry_page(self):
        b = S["browser"]
        b.origin = self.backend.url
        entry = next(n for n in self.entries() if n.startswith("a-ref-"))[:-3]
        b.goto(f"/brain/e/{entry}")
        self.wait_text(b, ["Adopted from", "Unchanged since adopted", "Explanation"])
        b.click('#bshell [data-bact="classify"]')
        b.wait("!!document.querySelector('#bshell [data-bf=\"reason\"]')", 5)
        b.eval("(() => { const s = document.querySelector('#bshell [data-bf=\"classification\"]');"
               " s.value = 'Historical'; s.dispatchEvent(new Event('change', {bubbles: true}));"
               " const r = document.querySelector('#bshell [data-bf=\"reason\"]'); r.focus();"
               " r.value = 'an older export, kept for the record';"
               " r.dispatchEvent(new Event('input', {bubbles: true})); return true; })()")
        b.click('#bshell [data-bact="save"]')
        self.wait_text(b, ["Historical", "Saved"])
        text = (self.backend.work / "brain" / f"{entry}.md").read_text()
        self.assertIn("**Classification:** Historical", text)

    def test_5_drift_under_an_open_preview_is_refused(self):
        b = self.open()
        self.pick(b, "spec.pdf")
        before = self.entries()
        original = (self.ref / "spec.pdf").read_bytes()
        (self.ref / "spec.pdf").write_bytes(original + b"% changed\n")
        try:
            self.apply(b)
            self.wait_text(b, ["changed since this preview"], "#bshell .b-adform")
            self.assertEqual(self.entries(), before)
        finally:
            (self.ref / "spec.pdf").write_bytes(original)

    def test_6_a_changed_source_is_shown_and_re_recorded(self):
        (self.ref / "api.md").write_text(GUIDE + "\n## Added\n\nNew words.\n")
        git(self.backend.work, "add", "reference/api.md")
        git(self.backend.work, "commit", "-qm", "the file changed")
        b = self.open()
        self.wait_text(b, ["Changed since adopted"], '#bshell [data-aditem="api.md"]')
        self.pick(b, "api.md")
        self.choose(b, "refresh")
        self.wait_text(b, ["New words."], "#bshell .b-adform")
        self.fill(b, "reason", "read the new version")
        shot("brain-adoption-changed-night")
        self.apply(b)
        self.wait_text(b, ["Saved", "source re-recorded"], "#bshell .b-adopt")
        entry = next(n for n in self.entries() if n.startswith("a-ref-"))
        text = (self.backend.work / "brain" / entry).read_text()
        self.assertIn("**Classification:** Historical", text, "the person's classification stands")

    def test_7_keyboard_narrow_and_both_themes(self):
        b = self.open(width=390, height=844, dark=False)
        self.no_overflow(b, "the inventory at 390px")
        b.eval("document.querySelector('#bshell [data-bact=\"ad-pick\"][data-barg=\"screen.png\"]').focus(); true")
        b.key("Enter")
        if not b.wait("!!document.querySelector('#bshell .b-adops')", 10):
            raise bv.CheckFailed("Enter opened no preview: " + self.text(b)[:800])
        self.assertEqual(b.eval("document.activeElement?.dataset?.bact"), "ad-back",
                         "focus moves into the preview")
        self.no_overflow(b, "the preview at 390px")
        shot("brain-adoption-preview-narrow-day")
        b.key("Enter")
        b.wait("!!document.querySelector('#bshell .b-adlist')", 10)
        self.assertEqual(b.eval("document.activeElement?.dataset?.barg"), "screen.png",
                         "Back returns focus to the file")
        b.eval("S.dark = true; applyTheme(); true")
        self.no_overflow(b, "night at 390px")
        shot("brain-adoption-list-narrow-night")

    def test_8_nothing_else_changed(self):
        self.assertEqual(tree(self.backend.app), self.app_before)
        self.assertEqual(tree(self.backend.work / "tasks"), self.cards_before)
        self.assertEqual((self.ref / "screen.png").read_bytes(), PNG)
        self.assertEqual((self.ref / "proto.html").read_bytes(), HTML)
