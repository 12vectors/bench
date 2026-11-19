"""A card's work base in a real browser (task 150).

Disposable cards in the fixture's own board, the prerequisite's branch cut
in the fixture's own application repository, and the orchestrator's
`base` records written into each card's `## Orchestration record` exactly
as `decisions.note` writes them. Nothing reaches a remote; everything is
put back afterwards. Run through verify/browser.

- **Normal and stacked, in plain words.** The Overview's Work base block
  reads "Starting from main" for an independent card and "Building on
  #71's committed work" for one stacked on a Review prerequisite, and its
  fold holds the exact branch, the pinned commit, the PR target and the
  normal base — the record identities `/api/state` reports.
- **Stale and blocked say so, with the remedy.** A decision whose parent
  moved reads "stale, reassessing" as a status; a blocked one leads with
  the conflict and names the owner's way through.
- **It survives a reload,** and the identity the page draws is the one on
  the card.
- **Reading runs nothing.** Opening every card and its fold leaves each
  card's bytes, the application's refs and the worktrees exactly as they
  were.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
APP_BASE = "main"
PARENT = ("71-wb-the-api", "WB the API")
A_BRANCH = f"task/{PARENT[0]}"
VIEWPORTS = [(1280, 900), (390, 844)]
AT = "2026-11-19T12:00:00Z"


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


def record(rid: str, card: str, kind: str = "base", **fields) -> str:
    meta = {"v": 1, "id": rid, "kind": kind, "source": "observed", "actor": "ada",
            "actorSource": "git", "actorKind": "orchestrator", "at": AT,
            "card": card, **fields}
    return (f"<!-- bench:orchestration {json.dumps(meta, sort_keys=True)} -->\n"
            f"- 2026-11-19 12:00 UTC · {kind} {fields.get('decision', '')}\n")


def card_text(title: str, number: str, records: list[str], depends: str = "") -> str:
    head = f"**Depends on:** {depends}\n" if depends else ""
    return (f"# {number} — {title}\n\n**Status:** To Do\n**Priority:** Low\n{head}\n"
            f"A disposable card for the work-base case.\n\n"
            f"## Orchestration record\n\n"
            f"<!-- bench:orchestration-record v1 — written by the board. -->\n\n"
            + "".join(records))


class AWorkBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.files = []
        parent = backend.work / "tasks" / "review" / f"{PARENT[0]}.md"
        parent.write_text(f"# 71 — {PARENT[1]}\n\n**Status:** Review\n**Priority:** Low\n\n"
                          f"The prerequisite.\n", encoding="utf-8")
        cls.files.append(parent)
        backend._git(backend.app, "checkout", "-q", "-b", A_BRANCH, APP_BASE)
        backend._git(backend.app, "commit", "-q", "--allow-empty", "-m", "fixture: the API")
        cls.tip = bv._git(backend.app, "rev-parse", A_BRANCH).stdout.strip()
        backend._git(backend.app, "checkout", "-q", APP_BASE)
        stacked = dict(decision="stacked", code="declared-prerequisite",
                       branch=A_BRANCH, commit=cls.tip, prBase=A_BRANCH,
                       appBase=APP_BASE, parent="71", parentFile=f"{PARENT[0]}.md",
                       parentHead=cls.tip, inputs="i0123456789abcdef",
                       summary=f"building on #71's committed work ({A_BRANCH})",
                       alternatives="#79 is in Review but is not a declared dependency")
        cls.cards = {
            "72-wb-the-ui": ("WB the UI", "72", "71",
                             [record("g-72aaaaaaaa", "72", **stacked)]),
            "73-wb-independent": ("WB independent", "73", "",
                                  [record("g-73aaaaaaaa", "73", decision="normal",
                                          code="no-prerequisite", appBase=APP_BASE,
                                          inputs="i1123456789abcdef",
                                          summary="no declared dependency has "
                                                  "committed work in Review")]),
            "74-wb-stale": ("WB stale", "74", "71",
                            [record("g-74aaaaaaaa", "74", **stacked),
                             record("g-74bbbbbbbb", "74", decision="stale",
                                    code="parent-moved", branch=A_BRANCH,
                                    commit=cls.tip, parent="71", parentHead=cls.tip,
                                    inputs="s2123456789abcdef",
                                    summary="the parent moved; not started yet: the "
                                            "orchestrator decides its base again "
                                            "before it starts")]),
            "75-wb-blocked": ("WB blocked", "75", "71, 79",
                              [record("g-75aaaaaaaa", "75", decision="blocked",
                                      code="multiple-prerequisites", appBase=APP_BASE,
                                      inputs="i3123456789abcdef",
                                      summary="it depends on #71, #79, unrelated work "
                                              "in Review — bench does not combine "
                                              "branches. Wait for all but one to be "
                                              "delivered, stack them on each other, or "
                                              "decide the base with a **Base branch:** "
                                              "header"),
                               record("b-75aaaaaaaa", "75", kind="blocker",
                                      blocker="b-75bbbbbbbb", state="open",
                                      operation="work-base",
                                      text="work base multiple-prerequisites")]),
        }
        for stem, (title, number, depends, records) in cls.cards.items():
            path = backend.work / "tasks" / "to-do" / f"{stem}.md"
            path.write_text(card_text(title, number, records, depends), encoding="utf-8")
            cls.files.append(path)

    @classmethod
    def tearDownClass(cls):
        backend = S["backend"]
        for path in cls.files:
            path.unlink(missing_ok=True)
        try:
            backend._git(backend.app, "branch", "-D", A_BRANCH)
        except bv.SetupError:
            pass

    def snapshot(self) -> dict:
        backend = S["backend"]
        refs = bv._git(backend.app, "for-each-ref", "--format=%(refname) %(objectname)")
        trees = sorted(p.name for p in backend.trees.iterdir()) \
            if backend.trees.exists() else []
        return {"cards": {p.name: p.read_bytes() for p in self.files},
                "refs": refs.stdout, "trees": trees}

    def state_plans(self) -> dict:
        status, body = S["backend"].get("/api/state")
        self.assertEqual(status, 200)
        return json.loads(body).get("plans") or {}

    def load(self, width, height):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        self.assertTrue(b.wait(f"!!document.querySelector('{card_sel('75-wb-blocked')}')", 15),
                        "the work-base cards never reached the board")
        return b

    def overview(self, stem: str) -> dict:
        b = S["browser"]
        b.eval(f"document.querySelector('{card_sel(stem)}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open #dsec-overview .workbase')",
                               10), f"{stem}: the Overview drew no Work base block")
        b.eval("(() => { const d = document.querySelector('#drawer .workbase details');"
               " if (d) d.open = true; })()")
        found = b.eval("""(() => { const w = document.querySelector('#drawer .workbase');
          const h = w.querySelector('.wbhead');
          return { head: h.innerText, decision: h.dataset.decision,
                   role: h.getAttribute('role'), text: w.innerText,
                   over: w.scrollWidth - w.clientWidth }; })()""")
        return found

    def close(self):
        b = S["browser"]
        b.key("Escape")
        b.wait("!document.querySelector('#drawer').classList.contains('open')", 5)

    def test_1_each_decision_in_plain_words_with_its_evidence(self):
        before = self.snapshot()
        plans = self.state_plans()
        self.assertEqual(plans["72-wb-the-ui.md"]["workBase"]["accepted"]["id"], "g-72aaaaaaaa")
        seen = {}
        for width, height in VIEWPORTS:
            self.load(width, height)
            ui = self.overview("72-wb-the-ui")
            self.assertTrue(ui["head"].startswith("Building on #71's committed work"), ui)
            for want in (A_BRANCH, self.tip[:12], "g-72aaaaaaaa", APP_BASE,
                         "not a declared dependency", "not agent-reported"):
                self.assertIn(want, ui["text"])
            self.assertLessEqual(ui["over"], 1, f"the Work base block scrolls at {width}px")
            shot(f"workbase-stacked-{width}x{height}")
            self.close()
            normal = self.overview("73-wb-independent")
            self.assertTrue(normal["head"].startswith(f"Starting from {APP_BASE}"), normal)
            shot(f"workbase-normal-{width}x{height}")
            self.close()
            stale = self.overview("74-wb-stale")
            self.assertIn("stale, reassessing", stale["head"])
            self.assertEqual(("stale", "status"), (stale["decision"], stale["role"]))
            self.assertIn("decides its base again", stale["text"])
            shot(f"workbase-stale-{width}x{height}")
            self.close()
            blocked = self.overview("75-wb-blocked")
            self.assertIn("Work base blocked", blocked["head"])
            self.assertIn("multiple-prerequisites", blocked["head"])
            self.assertIn("Base branch", blocked["text"], "the owner's remedy is shown")
            shot(f"workbase-blocked-{width}x{height}")
            self.close()
            seen[width] = {"stacked": ui["head"], "normal": normal["head"],
                           "stale": stale["head"], "blocked": blocked["head"]}
        # reading ran nothing: no card, ref or worktree changed
        self.assertEqual(before, self.snapshot())
        S["metrics"]["workBase"] = seen

    def test_2_it_survives_a_reload(self):
        b = self.load(1280, 900)
        b.goto("/cards/72")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .workbase')", 15))
        b.goto("/cards/72")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .workbase')", 15),
                        "the Work base block did not come back after a reload")
        head = b.eval("document.querySelector('#drawer .workbase .wbhead').innerText")
        self.assertTrue(head.startswith("Building on #71"), head)
        self.close()


if __name__ == "__main__":
    unittest.main()
