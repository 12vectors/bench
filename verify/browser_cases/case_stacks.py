"""A stacked card in a real browser (task 117, WP09).

Three disposable cards, each stacked on the one before by its own
`**Base branch:**` header, with their branches cut in the fixture's own
application repository; a fourth stacked on the first makes the stack
branch. Nothing reaches a remote: `gh` is the fixture's refusing stub.
Everything is put back afterwards. Run through verify/browser.

- **The words are the server's.** The ⌖ chip on each card's face reads
  exactly `stacks[file].chip` from `/api/state`, and the Overview's Stack
  block names the edge with the server's label and state, and the three
  targets with the server's branches.
- **No global position for a branching stack.** Once the fourth card
  joins, the chips say `step n` and never `n/m`.
- **Matched viewports, no overflow.** At 1280×900 and 390×844 the board
  and the open detail view scroll nothing sideways, and the Stack block
  fits inside the detail view.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
DRAWER = "document.querySelector('#drawerbody')"
CARDS = [
    ("91-stack-bottom", "Stack bottom", None),
    ("92-stack-middle", "Stack middle", "task/91-stack-bottom"),
    ("93-stack-top", "Stack top", "task/92-stack-middle"),
]
SIDE = ("94-stack-side", "Stack side", "task/91-stack-bottom")
VIEWPORTS = [(1280, 900), (390, 844)]


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def card_sel(stem: str) -> str:
    return f'#board article.card[data-file="{stem}.md"]'


def stacks() -> dict:
    status, body = S["backend"].get("/api/state")
    if status != 200:
        raise bv.CheckFailed(f"/api/state answered {status}")
    return json.loads(body).get("stacks") or {}


class AStackedCard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.files, cls.branches = [], []
        for stem, title, base in CARDS:
            cls.add(stem, title, base)

    @classmethod
    def add(cls, stem, title, base):
        backend = S["backend"]
        card = backend.work / "tasks" / "review" / f"{stem}.md"
        head = f"**Base branch:** {base}\n**PR base:** {base}\n" if base else ""
        card.write_text(f"# {title}\n\n**Status:** Review\n**Priority:** Low\n{head}\n"
                        f"A disposable card for the stack case.\n", encoding="utf-8")
        cls.files.append(card)
        ref = f"task/{stem}"
        backend._git(backend.app, "checkout", "-q", "-b", ref, base or "main")
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m",
                     f"fixture: {title}")
        backend._git(backend.app, "checkout", "-q", "main")
        cls.branches.append(ref)

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for card in cls.files:
            card.unlink(missing_ok=True)
        for ref in reversed(cls.branches):
            try:
                backend._git(backend.app, "branch", "-D", ref)
            except bv.SetupError:
                pass

    def load(self, width, height):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        self.assertTrue(b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15),
                        "the board never drew its stage columns")
        self.assertTrue(b.wait(f"!!document.querySelector('{card_sel(CARDS[-1][0])}')", 15),
                        "the stacked cards never reached the board")
        self.assertTrue(b.wait("Object.keys(S.state.stacks || {}).length >= 3", 15),
                        "the page never read a stack reading")
        return b

    def chip_text(self, stem: str):
        return S["browser"].eval(
            f"(() => {{ const c = [...document.querySelectorAll('{card_sel(stem)} [data-plan]')]"
            f"; return c.length ? c[0].innerText.replace(/^⌖\\s*/, '').trim() : null; }})()")

    def open_detail(self, stem: str):
        b = S["browser"]
        b.eval(f"document.querySelector('{card_sel(stem)}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open #dsec-overview .stack')", 10),
                        f"{stem}: the Overview drew no Stack block")
        b.wait("document.querySelector('#drawer').getAnimations()"
               ".every(a => a.playState === 'finished')", 5)

    def close_detail(self):
        b = S["browser"]
        b.key("Escape")
        b.wait("!document.querySelector('#drawer').classList.contains('open')", 5)

    def no_overflow(self, where: str):
        b = S["browser"]
        doc = b.eval("document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(doc, 1, f"the page scrolls sideways at {where}")

    def test_1_a_chain_wears_the_servers_words(self):
        found = stacks()
        self.assertEqual(found["93-stack-top.md"]["chip"], "on #92 · 3/3")
        self.assertEqual(found["92-stack-middle.md"]["edge"]["state"], "on-parent")
        seen = {}
        for width, height in VIEWPORTS:
            b = self.load(width, height)
            for stem, _, _ in CARDS:
                with self.subTest(stem=stem, width=width):
                    want = found[f"{stem}.md"]["chip"]
                    self.assertEqual(self.chip_text(stem), want)
            self.no_overflow(f"{width}px, the board")
            self.open_detail("93-stack-top")
            block = b.eval("""(() => { const s = document.querySelector('#drawer #dsec-overview .stack');
              const e = s.querySelector('.stkedge');
              return { state: e && e.dataset.state, label: e && e.querySelector('b').innerText,
                       rows: s.querySelectorAll('.stklist .stkrow').length,
                       text: s.innerText,
                       over: s.scrollWidth - s.clientWidth,
                       right: s.getBoundingClientRect().right,
                       drawerRight: document.querySelector('#drawerbody').getBoundingClientRect().right }; })()""")
            top = found["93-stack-top.md"]
            self.assertEqual(block["state"], top["edge"]["state"])
            self.assertEqual(block["label"].rstrip("."), top["edge"]["label"])
            self.assertEqual(block["rows"], 3, "the path from the bottom, all three")
            for key in ("selectedBase", "reviewTarget"):
                self.assertIn(top["targets"][key]["branch"], block["text"])
            self.assertIn(top["targets"]["landingTarget"]["sentence"], block["text"])
            self.assertLessEqual(block["over"], 1, f"the Stack block scrolls at {width}px")
            self.assertLessEqual(block["right"], block["drawerRight"] + 1)
            over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
            self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {width}px")
            shot(f"stack-overview-{width}x{height}")
            self.close_detail()
            seen[width] = {"chips": {s: self.chip_text(s) for s, _, _ in CARDS}}
        S["metrics"]["stacksChain"] = seen

    def test_2_a_branching_stack_counts_no_whole(self):
        self.add(*SIDE)
        found = stacks()
        self.assertEqual(found["94-stack-side.md"]["chip"], "on #91 · step 2")
        self.assertEqual(found["93-stack-top.md"]["position"]["kind"], "path")
        for width, height in VIEWPORTS:
            b = self.load(width, height)
            self.assertTrue(b.wait(f"!!document.querySelector('{card_sel(SIDE[0])}')", 15))
            for stem in [c[0] for c in CARDS] + [SIDE[0]]:
                with self.subTest(stem=stem, width=width):
                    text = self.chip_text(stem)
                    self.assertEqual(text, found[f"{stem}.md"]["chip"])
                    self.assertNotRegex(text or "", r"\d+/\d+")
            self.no_overflow(f"{width}px, a branching stack")
            shot(f"stack-branching-board-{width}x{height}")
        S["metrics"]["stacksBranching"] = {s: found[f"{s}.md"]["chip"]
                                          for s in [c[0] for c in CARDS] + [SIDE[0]]}
