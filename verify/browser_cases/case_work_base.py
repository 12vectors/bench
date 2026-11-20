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
  moved reads "stale; decided again before it starts" as a status (its work
  has not started); a blocked one leads with
  the conflict and names the owner's way through.
- **It survives a reload,** and the identity the page draws is the one on
  the card.
- **The reader's fold choice survives a redraw** (task 155), even one
  landing between a click, Enter or Space and the `toggle` event the
  browser queues for it: open and shut, by pointer and keyboard, after a
  reading, in both themes at both widths, and never crossing to another
  card.
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
        cls.tip = bv._git(backend.app, "rev-parse", A_BRANCH).stdout.decode().strip()
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
        # by its address, and waited for by its own title: a drawer still
        # closing from the card before is never read as this one's
        title = self.cards[stem][0]
        b.goto(f"/cards/{self.cards[stem][1]}")
        self.assertTrue(b.wait(
            "!!document.querySelector('#drawer.open #dsec-overview .workbase') && "
            f"document.querySelector('#drawer').innerText.includes({json.dumps(title)})",
            15), f"{stem}: the Overview drew no Work base block")
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
            self.assertIn("stale; decided again before it starts", stale["head"])
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

    # ── task 155: a redraw before the fold's queued toggle ──────────────
    #
    # A <details> changes `open` at once when a person clicks its summary
    # or presses Enter or Space on it; the page hears of it only through a
    # `toggle` event the browser queues for later. A live redraw (a stream
    # frame, a reading arriving) landing in between replaced the fold and
    # drew it from the choice before the click — shut, its evidence gone.
    # A MutationObserver on the fold's `open` attribute runs its callback
    # as a microtask of the input's own task, so it calls the page's real
    # `render()` exactly in that window, every time: no sleep, no luck.

    RACE_JS = """(() => {
      const d = document.querySelector('#drawer .workbase details');
      const log = [];
      d.addEventListener('toggle', () => log.push('toggle:' + (d.isConnected ? 'attached' : 'detached')));
      const mo = new MutationObserver(() => {
        mo.disconnect();
        log.push('open:' + d.open);
        render();
        const now = document.querySelector('#drawer .workbase details');
        log.push('redrawn:' + (now === d ? 'same' : 'replaced') + ':' + (now && now.open));
      });
      mo.observe(d, { attributes: true, attributeFilter: ['open'] });
      window.__wbRace = log;
    })()"""

    FOLD_JS = """(() => { const d = document.querySelector('#drawer .workbase details');
      return { open: d.open, text: document.querySelector('#drawer .workbase').innerText,
               facts: d.querySelector('.dfacts').getBoundingClientRect().height,
               log: window.__wbRace || null }; })()"""

    def drawn(self, number: str) -> None:
        """Another card's Overview, without a page load: the address
        changes as a link would change it, so S and the stream stay."""
        b = S["browser"]
        title = {c[1]: c[0] for c in self.cards.values()}[number]
        b.eval(f"history.pushState(null, '', '/cards/{number}');"
               " dispatchEvent(new PopStateEvent('popstate'))")
        self.assertTrue(b.wait(
            "!!document.querySelector('#drawer.open #dsec-overview .workbase details') && "
            f"document.querySelector('#drawer .dtitle').innerText.includes({json.dumps(title)})",
            15), f"#{number}: the Overview drew no Work base fold")

    def raced(self, act) -> dict:
        """`act` changes the fold as a person does; a real redraw lands
        before its toggle is delivered. Waits for that late toggle, so the
        reading below is after both."""
        b = S["browser"]
        b.eval(self.RACE_JS)
        act()
        self.assertTrue(b.wait("(window.__wbRace || []).some(e => e.startsWith('toggle:'))", 5),
                        f"the fold's toggle was never delivered: {b.eval('window.__wbRace')}")
        got = b.eval(self.FOLD_JS)
        # the ordering this case exists for, observed rather than assumed
        self.assertEqual(got["log"][0].split(":")[0], "open", got["log"])
        self.assertTrue(got["log"][1].startswith("redrawn:replaced:"), got["log"])
        self.assertEqual(got["log"][2], "toggle:detached", got["log"])
        b.eval("window.__wbRace = null")
        return got

    def assertOpenWithEvidence(self, got, how):
        self.assertTrue(got["open"], f"{how}: the fold was drawn shut by the redraw ({got['log']})")
        for want in (A_BRANCH, self.tip[:12], "g-72aaaaaaaa"):
            self.assertIn(want, got["text"], f"{how}: the evidence is not readable")
        self.assertGreater(got["facts"], 0, f"{how}: the facts take no space")

    def assertShut(self, got, how):
        self.assertFalse(got["open"], f"{how}: the fold was drawn open again ({got['log']})")
        self.assertNotIn(self.tip[:12], got["text"], f"{how}: the closed fold still shows its facts")

    def test_3_a_redraw_before_the_queued_toggle_keeps_the_readers_choice(self):
        before = self.snapshot()
        summary = "#drawer .workbase details > summary"
        b = S["browser"]
        for dark in (True, False):
            for width, height in VIEWPORTS:
                where = f"{width}px {'night' if dark else 'day'}"
                self.load(width, height)
                b.goto("/cards/72")
                b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
                self.drawn("72")
                self.assertFalse(b.eval(self.FOLD_JS)["open"], f"{where}: drawn open")
                # a pointer opens it, a redraw lands first: it stays open
                self.assertOpenWithEvidence(self.raced(lambda: b.click(summary)),
                                            f"{where}: pointer open")
                # a reading and a stream frame later, still open
                b.eval("window.__ls = 0; Promise.resolve(loadState())"
                       ".then(() => { render(); window.__ls = 1; })")
                self.assertTrue(b.wait("window.__ls === 1", 15), f"{where}: the reading never ended")
                self.assertOpenWithEvidence(b.eval(self.FOLD_JS), f"{where}: after a reading")
                shot(f"workbase-kept-open-{width}-{'night' if dark else 'day'}")
                # another card: its own fold, not this one's choice
                self.drawn("73")
                self.assertFalse(b.eval("document.querySelector('#drawer .workbase details').open"),
                                 f"{where}: #72's choice crossed to #73")
                self.drawn("72")
                self.assertOpenWithEvidence(b.eval(self.FOLD_JS), f"{where}: back on #72")
                # a pointer closes it, a redraw lands first: it stays shut
                self.assertShut(self.raced(lambda: b.click(summary)), f"{where}: pointer close")
                b.eval("render()")
                self.assertShut(b.eval(self.FOLD_JS), f"{where}: closed, then a stream frame")
                # the keyboard, both ways
                b.eval(f"document.querySelector('{summary}').focus()")
                self.assertOpenWithEvidence(self.raced(lambda: b.key("Enter")),
                                            f"{where}: Enter open")
                b.eval(f"document.querySelector('{summary}').focus()")
                self.assertShut(self.raced(lambda: b.key("Space")), f"{where}: Space close")
                self.close()
        b.eval("localStorage.removeItem('bench-theme')")
        # reading and folding ran nothing: no card, ref or worktree changed
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
