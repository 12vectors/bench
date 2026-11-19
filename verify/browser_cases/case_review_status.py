"""Review approval, upfront on every Review card (task 152), in a real
browser.

Disposable cards are written into the fixture's own work state, each with
a branch cut in the fixture's own application repository, and Bench's
verdicts are written as the records `gates.record_verdict` keeps. Nothing
reaches a remote, nothing is launched, and everything is put back.

- Every Review card says on its face, in words, whether Bench's review
  approved the commit it would merge: *Review approved*, *Review pending*,
  *Review outdated* — read from the backend's own reading, and the same
  words lead the card's Signals and its Reviews section.
- 1280px and 390px, Daylight and Night: the line is visible, readable
  (its text is not clipped to nothing and is not colour alone) and
  contained in its card; screenshots are kept.
- The keyboard: Tab reaches the line, Enter opens the card on Reviews.
- Live updates, without a reload: pending → approved (a verdict lands),
  approved → outdated (a new commit), and a running review (drawn from a
  reading the page is handed) — while a focused control on another card
  keeps its focus and an open detail view stays open.
- Reading posts nothing and writes nothing.
"""

import hashlib
import json
import time
import unittest

import benchverify as bv

S = bv.SESSION
PR = "https://github.com/example/fixture/pull/{n}"
CARDS = ["91-review-status-approved", "92-review-status-pending",
         "93-review-status-outdated"]


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def sel(stem: str) -> str:
    return f'#board article.card[data-file="{stem}.md"]'


def read(stem: str) -> dict:
    status, body = S["backend"].get(f"/api/card/delivery?file={stem}.md")
    if status != 200:
        raise bv.CheckFailed(f"/api/card/delivery answered {status}: {body[:300]!r}")
    return json.loads(body)["delivery"]


def line(stem: str) -> dict | None:
    return S["browser"].eval(f"""(() => {{
      const el = document.querySelector('{sel(stem)} .revstat');
      if (!el) return null;
      const card = el.closest('article').getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const label = el.querySelector('.rvl');
      const cs = getComputedStyle(label);
      return {{ word: el.dataset.revword, text: label.innerText,
               visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden',
               labelClipped: label.scrollWidth > label.clientWidth + 1,
               inside: r.left >= card.left - 0.5 && r.right <= card.right + 0.5,
               color: cs.color, bg: getComputedStyle(el).backgroundColor,
               tag: el.tagName }}; }})()""")


class ReviewStatusOnTheFace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.gates = backend.tool / "manager" / "local" / "state" / "gates"
        cls.gates.mkdir(parents=True, exist_ok=True)
        cls.files, cls.branches, cls.heads = [], [], {}
        for n, stem in enumerate(CARDS):
            card = backend.work / "tasks" / "review" / f"{stem}.md"
            card.write_text(f"# Review status fixture {n + 1}\n\n**Status:** Review\n"
                            f"**Priority:** Low\n**PR:** {PR.format(n=40 + n)}\n\n"
                            f"A disposable card for the review status case.\n",
                            encoding="utf-8")
            cls.files.append(card)
            ref = f"task/{stem}"
            backend._git(backend.app, "checkout", "-q", "-b", ref)
            backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                         f"fixture: {stem}")
            backend._git(backend.app, "checkout", "-q", "main")
            cls.branches.append(ref)
        for stem in CARDS:
            cls.heads[stem] = read(stem)["candidate"]["commit"]
        cls.verdict(CARDS[0], cls.heads[CARDS[0]])
        cls.verdict(CARDS[2], cls.heads[CARDS[2]])
        cls.advance(CARDS[2])

    @classmethod
    def verdict(cls, stem: str, head: str):
        path = cls.gates / f"{stem}.verdicts.json"
        found = json.loads(path.read_text()) if path.exists() else []
        found.append({"v": 1, "task": f"{stem}.md", "verdict": "APPROVE",
                      "head": head, "agent": None, "actor": "agent",
                      "reviewId": None, "note": "fixture",
                      "recorded": time.time(), "published": None})
        path.write_text(json.dumps(found), encoding="utf-8")
        cls.touch(stem)

    @classmethod
    def advance(cls, stem: str):
        backend = S["backend"]
        backend._git(backend.app, "checkout", "-q", f"task/{stem}")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m", "a later push")
        backend._git(backend.app, "checkout", "-q", "main")
        cls.touch(stem)

    @classmethod
    def touch(cls, stem: str):
        # the card's text changes after its records did, so no memoised
        # reading predates them
        card = S["backend"].work / "tasks" / "review" / f"{stem}.md"
        card.write_text(card.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for card in cls.files:
            card.unlink(missing_ok=True)
        for stem in CARDS:
            (cls.gates / f"{stem}.verdicts.json").unlink(missing_ok=True)
        for ref in cls.branches:
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
        for stem in CARDS:
            self.wait_word(stem, read(stem)["review"]["word"])
        return b

    def wait_word(self, stem: str, word: str):
        b = S["browser"]
        js = f"(document.querySelector('{sel(stem)} .revstat') || {{}}).dataset?.revword === {json.dumps(word)}"
        deadline = time.time() + 40
        while time.time() < deadline:
            if b.wait(js, 4):
                return
            b.eval("loadState()")
        raise bv.CheckFailed(f"{stem}: the face never read {word!r}; it holds {line(stem)}")

    def digest(self) -> str:
        backend = S["backend"]
        h = hashlib.sha256()
        roots = [backend.tool / "manager" / "local" / "state" / sub
                 for sub in ("gates", "evidence", "plans", "reviews")]
        roots += [backend.work / "tasks", backend.app / ".git" / "refs"]
        for root in roots:
            for path in sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []:
                h.update(str(path).encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    def test_1_the_face_says_it_in_words_at_both_widths_and_themes(self):
        expect = {CARDS[0]: ("review-approved", "Review approved"),
                  CARDS[1]: ("review-pending", "Review pending"),
                  CARDS[2]: ("review-outdated", "Review outdated")}
        for stem, (word, label) in expect.items():
            self.assertEqual(word, read(stem)["review"]["word"], stem)
        before = self.digest()
        seen = {}
        for width, height in ((1280, 900), (390, 844)):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    b = self.load(width, height, dark)
                    for stem, (word, label) in expect.items():
                        got = line(stem)
                        self.assertIsNotNone(got, stem)
                        self.assertEqual(word, got["word"], stem)
                        self.assertEqual(label, got["text"], stem)
                        self.assertTrue(got["visible"], stem)
                        self.assertFalse(got["labelClipped"], f"{stem}: {got}")
                        self.assertTrue(got["inside"], f"{stem}: {got}")
                        self.assertNotEqual(got["color"], got["bg"], stem)
                        self.assertEqual("BUTTON", got["tag"])
                    # the page never scrolls sideways at either width
                    over = b.eval("document.documentElement.scrollWidth - innerWidth")
                    self.assertLessEqual(over, 1)
                    b.eval(f"document.querySelector('{sel(CARDS[0])}').scrollIntoView({{block:'center'}})")
                    name = f"review-status-{width}-{'night' if dark else 'day'}"
                    shot(name)
                    seen[name] = {s: line(s)["text"] for s in CARDS}
                    self.assertEqual([], b.eval("window.__posts"), "reading posted something")
        self.assertEqual(before, self.digest(), "reading wrote something")
        S["metrics"]["reviewStatus"] = seen

    def test_2_keyboard_reaches_the_review_detail_and_it_agrees(self):
        b = self.load()
        stem = CARDS[0]
        b.eval(f"document.querySelector('{sel(stem)}').focus()")
        b.key("Tab")
        self.assertTrue(b.eval("document.activeElement.classList.contains('revstat')"),
                        b.eval("document.activeElement.outerHTML.slice(0, 200)"))
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .rvnow')", 10),
                        "Enter on the review line did not open the card's Reviews")
        now = b.eval("document.querySelector('#drawer .rvnow b').innerText")
        self.assertEqual("Review approved", now)
        self.assertEqual("review-approved",
                         b.eval("document.querySelector('#drawer .rvnow').dataset.revword"))
        shot("review-status-reviews-tab-1280")
        b.key("Escape")
        b.wait("!document.querySelector('#drawer').classList.contains('open')", 5)
        self.assertEqual([], b.eval("window.__posts"))

    def test_3_live_updates_keep_focus_and_the_open_view(self):
        b = self.load()
        # focus the outdated card's review line; the drawer is open on the
        # approved card's Reviews
        b.eval(f"document.querySelector('{sel(CARDS[0])} .revstat').click()")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .rvnow')", 10))
        b.eval(f"document.querySelector('{sel(CARDS[2])} .revstat').focus()")
        # pending → approved: a verdict lands on the pending card's head
        self.verdict(CARDS[1], self.heads[CARDS[1]])
        self.wait_word(CARDS[1], "review-approved")
        self.assertTrue(b.eval(
            f"document.activeElement === document.querySelector('{sel(CARDS[2])} .revstat')"),
            "a live update took focus from a card it did not change")
        self.assertTrue(b.eval("document.querySelector('#drawer').classList.contains('open')"),
                        "a live update closed the open detail view")
        # approved → outdated: a new commit on the approved card
        self.advance(CARDS[1])
        self.wait_word(CARDS[1], "review-outdated")
        # a fresh approval at the new head restores it
        self.verdict(CARDS[1], read(CARDS[1])["candidate"]["commit"])
        self.wait_word(CARDS[1], "review-approved")
        # a running review, as the server would carry it
        b.eval(f"""(() => {{ const d = S.state.delivery[{json.dumps(CARDS[1] + '.md')}];
          d.review = {{ word: 'review-running', label: 'Review in progress', tone: 'working',
                        sentence: 'Wren is reviewing the PR. Nothing is approved until it records a verdict.',
                        head: null, commit: null }};
          render(); }})()""")
        self.assertTrue(b.wait(
            f"document.querySelector('{sel(CARDS[1])} .revstat .rvl').innerText === 'Review in progress'", 5))
        self.assertTrue(b.eval(
            f"document.activeElement === document.querySelector('{sel(CARDS[2])} .revstat')"))
        shot("review-status-live-running-1280")
        b.key("Escape")
        self.assertEqual([], b.eval("window.__posts"))


if __name__ == "__main__":
    unittest.main()
