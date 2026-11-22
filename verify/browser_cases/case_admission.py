"""Review is not admission (task 189), in a real browser.

A disposable Review card is written into the fixture's own work state,
with its branch cut in the fixture's own application repository and a
target branch of its own (`**Base branch:**`), so `main` is never moved.
The target then gains accepted work the card does not hold, and an
update attempt that conflicted is recorded the way
`integration.record_attempt` keeps it. Nothing reaches a remote, nothing
is launched, and everything is put back.

- The card's face says where it stands against its target, in words —
  *Conflicts with target — repair needed* · the target — on a line of its
  own under the review line, at 1280px and 390px, Daylight and Night:
  visible, not clipped to nothing, inside its card; screenshots kept.
- The card's Overview leads its merge requirements with **Against the
  target**: the target and its revision, that the head does not hold it,
  the accepted commit it would bring in, and that a provider's
  mergeability is not evidence the combination works.
- Reading posts nothing and writes nothing.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
STEM = "94-admission-conflict"
TARGET = "fixture-target-189"
PR = "https://github.com/example/fixture/pull/94"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


SEL = f'#board article.card[data-file="{STEM}.md"]'


def read() -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={STEM}.md")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def face() -> dict | None:
    return S["browser"].eval(f"""(() => {{
      const el = document.querySelector('{SEL} .intstat');
      if (!el) return null;
      const card = el.closest('article').getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const label = el.querySelector('.rvl');
      const cs = getComputedStyle(label);
      return {{ state: el.dataset.integration, text: label.innerText,
               all: el.innerText,
               visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden',
               labelClipped: label.scrollWidth > label.clientWidth + 1
                 && label.clientWidth < 40,
               inside: r.left >= card.left - 0.5 && r.right <= card.right + 0.5,
               color: cs.color, bg: getComputedStyle(el).backgroundColor }}; }})()""")


class AgainstTheTarget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        git = backend._git
        app = backend.app
        cls.branches = []
        # the target: a branch of its own, so main is never moved
        git(app, "branch", TARGET, "main")
        cls.branches.append(TARGET)
        git(app, "checkout", "-q", "-b", f"task/{STEM}", TARGET)
        (app / "admission-shared.txt").write_text("the card's version\n", encoding="utf-8")
        git(app, "add", "admission-shared.txt")
        git(app, "commit", "-q", "-m", "fixture: the card's work")
        cls.branches.append(f"task/{STEM}")
        git(app, "checkout", "-q", TARGET)
        (app / "admission-shared.txt").write_text("accepted elsewhere\n", encoding="utf-8")
        git(app, "add", "admission-shared.txt")
        git(app, "commit", "-q", "-m", "accepted: another card's work")
        git(app, "checkout", "-q", "main")
        cls.card = backend.work / "tasks" / "review" / f"{STEM}.md"
        cls.card.write_text(
            f"# Admission fixture\n\n**Status:** Review\n**Priority:** Low\n"
            f"**Base branch:** {TARGET}\n**PR:** {PR}\n\n"
            f"A disposable card for the admission case.\n", encoding="utf-8")
        found = read()
        cls.head = found["candidate"]["commit"]
        cls.tip = (found.get("integration") or {}).get("targetTip")
        if not cls.head or not cls.tip:
            raise bv.SetupError(f"the fixture card read {found.get('integration')!r}")
        cls.record = backend.tool / "manager" / "local" / "state" / "integration" / f"{STEM}.json"
        cls.record.parent.mkdir(parents=True, exist_ok=True)
        cls.record.write_text(json.dumps({
            "v": 1, "kind": "bench-integration-attempts", "file": f"{STEM}.md",
            "attempts": [{"v": 1, "at": "2026-11-22T10:00:00Z", "head": cls.head,
                          "target": TARGET, "targetTip": cls.tip,
                          "outcome": "conflict", "actor": "person",
                          "conflicts": ["admission-shared.txt"]}]}),
            encoding="utf-8")
        # the card's text changes after its record did
        cls.card.write_text(cls.card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        cls.card.unlink(missing_ok=True)
        cls.record.unlink(missing_ok=True)
        for ref in reversed(cls.branches):
            try:
                backend._git(backend.app, "branch", "-D", ref)
            except bv.SetupError:
                pass
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    def load(self, width=1280, height=900, dark=None):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        if dark is not None:
            b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        js = f"(document.querySelector('{SEL} .intstat') || {{}}).dataset?.integration === 'conflicts'"
        deadline = time.time() + 40
        while time.time() < deadline:
            if b.wait(js, 4):
                return b
            b.eval("loadState()")
        raise bv.CheckFailed(f"the face never read conflicts; it holds {face()}")

    def digest(self) -> str:
        backend = S["backend"]
        h = hashlib.sha256()
        roots = [backend.tool / "manager" / "local" / "state" / sub
                 for sub in ("gates", "evidence", "plans", "integration")]
        roots += [backend.work / "tasks", backend.app / ".git" / "refs"]
        for root in roots:
            for path in sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []:
                h.update(str(path).encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    def test_1_the_face_says_it_in_words_at_both_widths_and_themes(self):
        found = read()
        self.assertEqual("conflicts", found["integration"]["state"])
        self.assertFalse(found["integration"]["fresh"])
        before = self.digest()
        seen = {}
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    b = self.load(width, height, dark)
                    got = face()
                    self.assertIsNotNone(got)
                    self.assertEqual("Conflicts with target — repair needed", got["text"])
                    self.assertIn(TARGET, got["all"])
                    self.assertTrue(got["visible"], got)
                    self.assertFalse(got["labelClipped"], got)
                    self.assertTrue(got["inside"], got)
                    self.assertNotEqual(got["color"], got["bg"])
                    over = b.eval("document.documentElement.scrollWidth - innerWidth")
                    self.assertLessEqual(over, 1)
                    b.eval(f"document.querySelector('{SEL}').scrollIntoView({{block:'center'}})")
                    name = f"admission-face-{width}-{'night' if dark else 'day'}"
                    shot(name)
                    seen[name] = got["text"]
                    self.assertEqual([], b.eval("window.__posts"), "reading posted something")
        self.assertEqual(before, self.digest(), "reading wrote something")
        S["metrics"]["admission"] = seen

    def test_2_the_overview_says_against_the_target(self):
        before = self.digest()
        for width, height, dark in ((1280, 900, False), (390, 844, True)):
            with self.subTest(width=width, dark=dark):
                b = self.load(width, height, dark)
                b.eval(f"document.querySelector('{SEL}').click()")
                ok = b.wait("!!document.querySelector('.dint[data-integration=\"conflicts\"]')", 20)
                self.assertTrue(ok, "the Overview drew no Against the target block")
                text = b.eval("document.querySelector('.dint').innerText")
                self.assertIn("Against the target", text)
                self.assertIn("Conflicts with target", text)
                self.assertIn(self.tip[:12], text)
                self.assertIn("Review is not admission", text)
                self.assertIn("Provider mergeability", text)
                b.eval("document.querySelector('.dint').scrollIntoView({block:'start'})")
                shot(f"admission-overview-{width}-{'night' if dark else 'day'}")
                self.assertEqual([], b.eval("window.__posts"), "reading posted something")
                b.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        self.assertEqual(before, self.digest(), "reading wrote something")


if __name__ == "__main__":
    unittest.main()
