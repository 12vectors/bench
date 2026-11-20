"""The task detail view by a reader's questions (task 149), in a real browser.

The fixture cards in `tests/fixtures/task-information/` — a long legacy
card like #64, a rough backlog card, an active card, a blocked card with
open questions, a card in review with a request for changes, a card whose
brief has nothing to quote and a delivered card with no recorded result —
are put on the disposable board and opened. For each:

1. the Overview's four sections stand in the reader's order — the work,
   where it stands and what happens next, what is required, the reviews —
   ahead of the supporting detail and the tools; the work is the brief's
   own words (or says there are none), never the title;
2. at 1280x900 the delivery status is on screen when the card opens;
3. nothing scrolls sideways or is clipped at 1280px, 390px or 320px wide,
   in both themes; 320 CSS px is the reflow width WCAG 1.4.10 names for
   1280px content at 400% zoom, and this case emulates it with the
   viewport width (the browser's own zoom is not driven);
4. by keyboard: "Read the full brief" opens the Brief with focus on its
   tab, and the Brief's section index moves focus to the heading;
5. deep links open the card on the right section;
6. a live redraw keeps the reader's scroll position, the focused control
   and unsent text.

Screenshots are written for a person to look at; the walkthrough in
`develop/design/task-information/` names which is which.
"""

import json
import shutil
import time
import unittest
from pathlib import Path

import benchverify as bv

S = bv.SESSION
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "task-information"
STAGES = {"Backlog": "backlog", "To Do": "to-do", "In Progress": "in-progress",
          "Review": "review", "Done": "done"}
DRAWER = "document.querySelector('#drawerbody')"
ORDER = ("(() => { const y = s => { const e = document.querySelector(s);"
         " return e ? e.getBoundingClientRect().top : -1; };"
         " return [y('#dsec-overview .ovwork'), y('#dsec-overview .ovnow'),"
         " y('#dsec-overview .ovreq'), y('#dsec-overview .ovrev'),"
         " y('#dsec-overview .ovdetail')]; })()")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def stage_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("**Status:**"):
            return STAGES[line.split(":**", 1)[1].strip()]
    raise AssertionError("a fixture names its status")


class TaskInformation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        tasks = S["backend"].work / "tasks"
        cls.placed = {}
        for src in sorted(FIXTURES.glob("*.md")):
            text = src.read_text(encoding="utf-8")
            dest = tasks / stage_of(text) / src.name
            shutil.copyfile(src, dest)
            cls.placed[src.name] = dest
        cls.measured = {}

    @classmethod
    def tearDownClass(cls):
        for path in cls.placed.values():
            path.unlink(missing_ok=True)
        S["metrics"]["task-information"] = cls.measured

    def tearDown(self):
        b = S["browser"]
        b.eval("(() => { S.selected = null; renderDrawer(); return true; })()")

    def open_card(self, name: str, width=1280, height=900, dark=False):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/")
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        sel = f'#board article.card[data-file="{name}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15), f"{name} never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        # the brief's outline, the delivery status and the records are read
        self.assertTrue(b.wait("!!document.querySelector('#dsec-overview .ovwork') &&"
                               " !document.querySelector('#dsec-overview .ovwork [aria-busy]') &&"
                               " !!document.querySelector('#dsec-overview .dstatus') &&"
                               f" !!(S.acc[{json.dumps(name)}] || {{}}).data", 15),
                        f"{name}: the Overview never finished reading")
        self.assertTrue(b.wait("document.querySelector('#drawer').getAnimations()"
                               ".every(a => a.playState === 'finished')", 5))
        return b

    def text(self, sel: str) -> str:
        return S["browser"].eval(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")

    def no_sideways(self, where: str):
        b = S["browser"]
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {where}")
        right = b.eval("document.querySelector('#drawer').getBoundingClientRect().right")
        self.assertLessEqual(right, b.eval("window.innerWidth") + 1, f"the drawer is off-screen at {where}")
        # no control in the open section is cut off by the drawer's edge
        clipped = b.eval("(() => { const box = document.querySelector('#drawerbody').getBoundingClientRect();"
                         " return [...document.querySelectorAll('.dsec:not([hidden]) button, .dsec:not([hidden]) a')]"
                         ".filter(e => e.offsetParent && !e.closest('details:not([open])'))"
                         ".filter(e => { const r = e.getBoundingClientRect();"
                         " return r.width && (r.left < box.left - 1 || r.right > box.right + 1); })"
                         ".map(e => e.innerText.slice(0, 40)); })()")
        self.assertEqual(clipped, [], f"controls clipped at {where}")

    def test_every_fixture_answers_the_readers_questions_in_order(self):
        expect = {
            "164-legacy-long.md": "Make first installation layout-aware",
            "201-backlog-rough.md": "Let a person pin a card",
            "202-active-run.md": "Add an \"Export CSV\" action",
            "203-blocked-questions.md": "Remove the legacy phase runner",
            "204-review-findings.md": "Activity shows local times",
            "205-missing-summary.md": "has no opening paragraph, problem section or scope section to quote",
            "206-unavailable-evidence.md": "Rename the ⛭ drive chip's visible label",
        }
        for name, words in expect.items():
            with self.subTest(card=name):
                b = self.open_card(name)
                order = b.eval(ORDER)
                self.assertTrue(all(y >= 0 for y in order), f"{name}: a section is missing: {order}")
                self.assertEqual(order, sorted(order), f"{name}: the sections are out of order: {order}")
                work = self.text("#dsec-overview .ovwork")
                self.assertIn(words, work)
                title = b.eval("document.querySelector('#drawer .dtitle').innerText")
                self.assertNotIn(title.split(" — ", 1)[-1], work, f"{name}: the summary repeats the title")
                # the next step is a word in text, not a colour alone
                word = self.text("#dsec-overview .dstatus .dsword").strip()
                self.assertTrue(word, f"{name}: the delivery status has no word")
                top = b.eval("document.querySelector('#dsec-overview .ovnow .dstatus').getBoundingClientRect().top")
                self.assertLess(top, 900, f"{name}: the delivery status is below the fold at 1280x900")
                self.measured[name] = {"order": order, "statusTop": top, "status": word}
                self.no_sideways(f"1280x900 {name}")
                shot(f"task-info-{name[:-3]}-overview-1280x900")

    def test_truthful_states_on_the_fixtures(self):
        b = self.open_card("206-unavailable-evidence.md")
        self.assertIn("No work report is on record on this card",
                      self.text("#dsec-overview .ovnow"))
        b = self.open_card("203-blocked-questions.md")
        self.assertIn("The brief lists 2 under “Open questions”", self.text("#dsec-overview .ovwork"))
        req = self.text("#dsec-overview .ovreq")
        self.assertIn("#999", req)
        self.assertIn("not on this board", req)
        self.assertLess(req.index("To do the work"), req.index("To merge it"))
        b = self.open_card("204-review-findings.md")
        self.assertIn("Olive asked for changes at 7d1e0aa94c21", self.text("#dsec-overview .ovrev"))
        self.assertIn("the run's own account, not a check result", self.text("#dsec-overview .ovnow"))
        # the code's reviews come before the brief's own in Reviews
        b.click("#dtab-reviews")
        groups = b.eval("[...document.querySelectorAll('#dsec-reviews .rvgroup > h3')].map(h => h.innerText)")
        self.assertEqual([g.split()[0] for g in groups], ["PR", "Relevance"])
        shot("task-info-204-review-findings-reviews-1280x900")

    def test_narrow_widths_and_both_themes(self):
        for name in ("164-legacy-long.md", "204-review-findings.md"):
            for dark in (False, True):
                mode = "dark" if dark else "light"
                for w, h in ((390, 844), (320, 720)):
                    with self.subTest(card=name, mode=mode, width=w):
                        self.open_card(name, width=w, height=h, dark=dark)
                        self.no_sideways(f"{w}x{h} {mode} {name}")
                        # the primary action, when there is one, is whole on screen
                        box = S["browser"].eval(
                            "(() => { const p = document.querySelector('#dsec-overview .dstatus .dact');"
                            " if (!p) return null; const r = p.getBoundingClientRect();"
                            " return [r.left, r.right, window.innerWidth]; })()")
                        if box:
                            self.assertGreaterEqual(box[0], 0)
                            self.assertLessEqual(box[1], box[2] + 1)
                        if w == 390:
                            shot(f"task-info-{name[:-3]}-overview-{w}x{h}-{mode}")

    def test_keyboard_brief_index_and_inputs(self):
        b = self.open_card("164-legacy-long.md")
        b.eval("document.querySelector('#dsec-overview .ovwork [data-goto=\"brief\"]').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!document.querySelector('#dsec-brief').hidden", 5), "Read the full brief did nothing")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.id === 'dtab-brief'", 5),
                        "focus did not land on the Brief tab")
        self.assertTrue(b.wait("location.search === '?tab=brief'", 5), "the address did not follow")
        # the header fields are a grid, not a run-on line
        self.assertTrue(b.eval("document.querySelectorAll('#dsec-brief dl.bmeta dt').length >= 4"))
        shot("task-info-164-legacy-long-brief-1280x900")
        b.eval("[...document.querySelectorAll('#dsec-brief [data-brief-h]')]"
               ".find(x => x.innerText === 'What to build').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.tagName === 'H2'"
                               " && document.activeElement.innerText === 'What to build'", 5),
                        "the index did not move focus to the heading")
        # an empty Inputs tab is one statement, its caveats folded
        b.click("#dtab-inputs")
        self.assertTrue(b.wait("!document.querySelector('#dsec-inputs').hidden &&"
                               " !document.querySelector('#dsec-inputs [aria-busy]')", 10))
        inputs = self.text("#dsec-inputs")
        self.assertIn("there is nothing to list", inputs)
        self.assertNotIn("No tools are listed", inputs)
        self.assertFalse(b.eval("!!document.querySelector('#dsec-inputs details[data-keep=\"inp-about\"]').open"))
        shot("task-info-164-legacy-long-inputs-1280x900")
        b.click("#dtab-acceptance")
        self.assertTrue(b.wait("document.querySelectorAll('#dsec-acceptance .aitem .alead').length >= 8", 5),
                        "criteria do not lead with their first clause")
        shot("task-info-164-legacy-long-acceptance-1280x900")

    def test_deep_links(self):
        b = S["browser"]
        b.viewport(1280, 900)
        b.goto("/cards/164?tab=brief")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open') &&"
                               " !document.querySelector('#dsec-brief').hidden &&"
                               " !!document.querySelector('#dsec-brief dl.bmeta')", 15),
                        "a deep link to the Brief did not open it")
        b.goto("/cards/202")
        self.assertTrue(b.wait("!!document.querySelector('#dsec-overview .ovwork') &&"
                               " !document.querySelector('#dsec-overview').hidden &&"
                               " /Export CSV/.test(document.querySelector('#dsec-overview .ovwork').innerText)", 15),
                        "a deep link to the card did not open its Overview on the work")

    def test_a_live_redraw_keeps_place_focus_and_unsent_text(self):
        b = self.open_card("164-legacy-long.md", width=1280, height=700)
        b.eval(f"{DRAWER}.scrollTop = document.querySelector('#dsec-overview .ovrev').offsetTop")
        before = b.eval(f"{DRAWER}.scrollTop")
        self.assertGreater(before, 0)
        b.eval("document.querySelector('#dsec-overview .ovrev [data-goreview]').focus()")
        b.eval("renderDrawer()")
        self.assertLessEqual(abs(b.eval(f"{DRAWER}.scrollTop") - before), 2, "a redraw moved the reader")
        self.assertTrue(b.eval("!!document.activeElement && document.activeElement.matches('#dsec-overview [data-goreview]')"),
                        "a redraw dropped focus")
        # an unsent comment in the Brief survives a whole board reading
        b.click("#dtab-brief")
        self.assertTrue(b.wait("!!document.querySelector('#commentbox')", 10))
        b.eval("(() => { const c = document.querySelector('#commentbox'); c.value = 'unsent note';"
               " c.dispatchEvent(new Event('input')); return true; })()")
        b.eval("loadState()")
        time.sleep(1.0)
        b.eval("renderDrawer()")
        self.assertEqual(b.eval("document.querySelector('#commentbox').value"), "unsent note")
        b.eval("(() => { const c = document.querySelector('#commentbox'); c.value = '';"
               " c.dispatchEvent(new Event('input')); return true; })()")

    # ── the owner's visual review: tools at the top, icon controls, gap ──

    TABS = ("overview", "brief", "inputs", "acceptance", "reviews", "activity")

    def tools_and_gap(self, where: str):
        """One tool group, labelled once, under the tab row and above the
        open section on every tab — and a deliberate gap under the line."""
        b = S["browser"]
        for tab in self.TABS:
            b.click(f"#dtab-{tab}")
            self.assertTrue(b.wait(f"!document.querySelector('#dsec-{tab}').hidden", 5), f"{tab} did not open")
            b.eval(f"{DRAWER}.scrollTop = 0")
            m = b.eval(
                "(() => { const r = s => { const e = document.querySelector(s);"
                " return e ? e.getBoundingClientRect() : null; };"
                " const sec = [...document.querySelectorAll('#drawerbody > .dsec')].find(s => !s.hidden);"
                " const first = sec && [...sec.children].find(c => c.getBoundingClientRect().height > 0);"
                " return { groups: document.querySelectorAll('#drawerbody .cardtools').length,"
                " labels: [...document.querySelectorAll('#drawerbody .tlabel')].map(l => l.innerText.trim()),"
                " headings: [...document.querySelectorAll('#drawerbody h2, #drawerbody h3, #drawerbody h4')]"
                ".filter(h => /^tools$/i.test(h.innerText.trim())).length,"
                " head: r('#drawer .dsticky').bottom, tabs: r('#drawer .dtabs').bottom,"
                " tools: r('#drawerbody > #drawertools'),"
                " first: first ? first.getBoundingClientRect().top : null }; })()")
            self.assertEqual(m["groups"], 1, f"{where} {tab}: {m['groups']} tool groups")
            self.assertEqual(m["labels"], ["Tools"], f"{where} {tab}: tool labels {m['labels']}")
            self.assertEqual(m["headings"], 0, f"{where} {tab}: a Tools heading over the tool group")
            tools = m["tools"]
            self.assertIsNotNone(tools, f"{where} {tab}: the tools are not under the tab row")
            self.assertGreaterEqual(tools["top"] - m["head"], 20,
                                    f"{where} {tab}: the tools start {tools['top'] - m['head']:.0f}px under the line")
            self.assertIsNotNone(m["first"], f"{where} {tab}: the section shows nothing")
            self.assertGreaterEqual(m["first"] - tools["bottom"], 12,
                                    f"{where} {tab}: the section starts against the tools")
            self.measured.setdefault("gap", {})[f"{where} {tab}"] = round(tools["top"] - m["head"])
        b.click("#dtab-overview")

    def controls_one_row(self, where: str):
        b = S["browser"]
        m = b.eval(
            "(() => { const bs = [...document.querySelectorAll('#drawer .dhead .dctl button')];"
            " const body = document.querySelector('#drawerbody').getBoundingClientRect();"
            " const t = document.querySelector('#drawer .dtitle').getBoundingClientRect();"
            " return { ids: bs.map(x => x.id), tops: bs.map(x => Math.round(x.getBoundingClientRect().top)),"
            " rights: bs.map(x => x.getBoundingClientRect().right), lefts: bs.map(x => x.getBoundingClientRect().left),"
            " names: bs.map(x => x.getAttribute('aria-label')), tips: bs.map(x => x.title),"
            " texts: bs.map(x => x.innerText.trim()), bodyL: body.left, bodyR: body.right,"
            " vw: window.innerWidth, titleW: t.width, titleR: t.right }; })()")
        self.assertEqual(m["ids"], ["dcopylink", "dwide", "closeDrawer"], where)
        self.assertEqual(len(set(m["tops"])), 1, f"{where}: the controls wrap onto two rows {m['tops']}")
        self.assertTrue(all(n for n in m["names"]) and all(t for t in m["tips"]),
                        f"{where}: an icon without a name or a tooltip")
        self.assertEqual(m["texts"], ["", "", ""], f"{where}: the controls still carry text")
        self.assertLessEqual(max(m["rights"]), min(m["vw"], m["bodyR"]) + 1, f"{where}: a control is cut off")
        self.assertGreaterEqual(min(m["lefts"]), m["bodyL"] - 1, f"{where}: a control is cut off")
        # the title keeps the width of the view, wrapping rather than shrinking
        self.assertGreaterEqual(m["titleW"], (m["bodyR"] - m["bodyL"]) * 0.8,
                                f"{where}: the title was squeezed to {m['titleW']:.0f}px")
        self.measured.setdefault("controls", {})[where] = {"tops": m["tops"], "titleW": round(m["titleW"])}

    def test_owner_review_tools_top_gap_and_icon_controls(self):
        name = "164-legacy-long.md"
        for dark in (False, True):
            mode = "dark" if dark else "light"
            for w, h in ((1280, 900), (390, 844), (320, 720)):
                with self.subTest(mode=mode, width=w):
                    b = self.open_card(name, width=w, height=h, dark=dark)
                    where = f"{w}x{h} {mode}"
                    self.controls_one_row(where)
                    self.tools_and_gap(where)
                    self.no_sideways(where)
                    shot(f"task-info-owner-header-{w}x{h}-{mode}")
                    if w == 1280:
                        # the expanded reading view keeps the same header
                        b.click("#dwide")
                        self.assertTrue(b.wait("S.dwide === true", 5))
                        self.controls_one_row(f"{where} expanded")
                        self.tools_and_gap(f"{where} expanded")
                        shot(f"task-info-owner-header-expanded-{w}x{h}-{mode}")
                        b.click("#dwide")
                        self.assertTrue(b.wait("S.dwide === false", 5))

    def test_owner_review_controls_behave(self):
        b = self.open_card("204-review-findings.md")
        # Expand/Narrow: its name and icon follow its state, focus stays on it
        b.click("#dwide")
        self.assertTrue(b.wait("S.dwide === true && document.querySelector('#drawer').classList.contains('wide')", 5))
        self.assertEqual(b.eval("document.querySelector('#dwide').getAttribute('aria-label')"), "Narrow the reading view")
        self.assertEqual(b.eval("document.querySelector('#dwide').getAttribute('aria-pressed')"), "true")
        self.assertTrue(b.eval("!!document.querySelector('#dwide svg[data-icon=\"narrow\"]')"))
        self.assertEqual(b.eval("document.activeElement && document.activeElement.id"), "dwide",
                         "focus left the Expand control")
        b.key("Enter")
        self.assertTrue(b.wait("S.dwide === false", 5), "Enter on the focused control did not narrow the view")
        self.assertEqual(b.eval("document.querySelector('#dwide').getAttribute('aria-label')"), "Expand the reading view")
        self.assertTrue(b.eval("!!document.querySelector('#dwide svg[data-icon=\"expand\"]')"))
        # Copy link: captured, with its confirmation
        b.eval("(() => { window.__copied = [];"
               " Object.defineProperty(navigator, 'clipboard', { configurable: true,"
               " value: { writeText: async (t) => { __copied.push(t); } } }); return true; })()")
        b.click("#dtab-reviews")
        self.assertTrue(b.wait("location.search === '?tab=reviews'", 5))
        b.click("#dcopylink")
        self.assertTrue(b.wait("__copied.length === 1", 5), "Copy link copied nothing")
        self.assertEqual(b.eval("__copied[0]"), b.eval("location.origin") + "/cards/204?tab=reviews")
        self.assertTrue(b.wait("document.querySelector('#toastmsg').textContent.includes('link copied')", 5),
                        "no confirmation that the link was copied")
        # Close by its button, and by Escape: focus goes back to the card
        b.click("#closeDrawer")
        self.assertTrue(b.wait("!document.querySelector('#drawer').classList.contains('open')", 5))
        self.assertTrue(b.wait("document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.file === '204-review-findings.md'", 5),
                        "Close did not give focus back to the card")
        b = self.open_card("204-review-findings.md")
        b.eval("document.querySelector('#closeDrawer').focus()")
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#drawer').classList.contains('open')", 5),
                        "Escape did not close the detail view")

    def test_owner_review_tools_survive_a_live_update(self):
        b = self.open_card("204-review-findings.md", width=1280, height=700)
        # a tool focused at the top keeps its focus, and stays one group,
        # through a redraw and a whole board reading (what an SSE frame asks)
        b.eval("[...document.querySelectorAll('#drawerbody > #drawertools button.chip2')]"
               ".filter(e => Object.keys(e.dataset).length).pop().focus()")
        self.assertTrue(b.eval("!!document.activeElement.closest('#drawertools')"), "no tool to focus")
        label = b.eval("document.activeElement.innerText")
        for _ in range(2):
            b.eval("loadState()")
            time.sleep(0.8)
            b.eval("renderDrawer()")
        self.assertEqual(b.eval("document.querySelectorAll('#drawerbody .cardtools').length"), 1,
                         "a redraw doubled the tools")
        self.assertTrue(b.eval("!!document.activeElement && !!document.activeElement.closest('#drawerbody > #drawertools')"),
                        "a redraw dropped focus from the tools")
        self.assertEqual(b.eval("document.activeElement.innerText"), label)
        # a redraw while expanded keeps the reading view expanded
        b.click("#dwide")
        b.eval("renderDrawer()")
        self.assertTrue(b.eval("document.querySelector('#drawer').classList.contains('wide')"
                               " && document.querySelector('#dwide').getAttribute('aria-pressed') === 'true'"))
        b.click("#dwide")


if __name__ == "__main__":
    unittest.main()
