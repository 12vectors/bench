"""Completed phase lanes close themselves (task 161), measured in a browser.

A lane whose every listed card is in Merged in is drawn as its head and one
summary line; the columns and the log are kept, hidden, behind a toggle. A
phase with work still to merge is drawn whole. Disposable fixtures on the
backend's work state: a phase complete on first load, a phase one member
short, and ten more completed phases so the page is the long one this card
is about. The last member is brought in by a move on disk, and the lane
must close on the live update, without a reload; moving it back opens it.
The toggle is driven by keyboard and pointer, a person's choice must
survive a redraw with focus kept, and the summary must say the members are
merged and the phase is not delivered. Both themes, at 1440 and 390 px.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION

DONE_PHASE = "81-ship-the-complete-phase"
PART_PHASE = "84-ship-the-partial-phase"
LAST = "86-the-last-member"
DRIFTING = "98-a-drifting-phase"
MANY = [(90 + i, 110 + i) for i in range(10)]   # (phase, its one member)

CONTRAST = """((fgEl) => {
  const rgb = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  const bgOf = (el) => { for (; el; el = el.parentElement) {
      const c = rgb(getComputedStyle(el).backgroundColor);
      if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) return c; }
    return [255, 255, 255]; };
  const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const a = lum(rgb(getComputedStyle(fgEl).color)), b = lum(bgOf(fgEl));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
})"""


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def metric(name, value):
    S["metrics"].setdefault("phaseLanes", {})[name] = value


def lane(stem: str) -> str:
    return f'#lanes .lane[data-phase="{stem}.md"]'


def write(stage: str, stem: str, title: str, sections: str = "") -> object:
    """A card; with `sections` (its `## Cards` and any log) a phase card.
    The prose comes before the sections: a line inside `## Cards` that
    names no card is phase drift, and a drifting phase never closes."""
    path = S["backend"].work / "tasks" / stage / f"{stem}.md"
    path.write_text(f"# {title}\n\n**Status:** {bv.STATUS[stage]}\n**Priority:** Low\n"
                    + ("**Type:** Phase\n" if sections else "")
                    + "\nA disposable card for the phase-lane case.\n"
                    + (f"\n{sections}" if sections else ""), encoding="utf-8")
    return path


class PhaseLanes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.files = []
        add = lambda *a: cls.files.append(write(*a))
        # complete on first load: two members merged in, read off the log
        add("review", "82-first-member", "82 — First member")
        add("done", "83-second-member", "83 — Second member")
        add("review", DONE_PHASE, "81 — Ship the complete phase",
            "## Cards\n\n- 82 — First member\n- 83 — Second member\n\n"
            "## Phase log\n\n- 2026-08-01 10:20 · 82 merged into phase/" + DONE_PHASE + "\n")
        # one member short: 85 merged in, 86 still in progress
        add("review", "85-merged-member", "85 — Merged member")
        add("in-progress", LAST, "86 — The last member")
        add("in-progress", PART_PHASE, "84 — Ship the partial phase",
            "## Cards\n\n- 85 — Merged member\n- 86 — The last member\n\n"
            "## Phase log\n\n- 2026-08-01 10:20 · 85 merged into phase/" + PART_PHASE + "\n")
        # a long page of completed phases
        for phase, member in MANY:
            add("done", f"{member}-member-of-{phase}", f"{member} — Member of phase {phase}")
            add("done", f"{phase}-a-finished-phase", f"{phase} — A finished phase",
                f"## Cards\n\n- {member} — Member of phase {phase}\n")
        # every card it lists is done, but one line names no card: drift
        add("done", "120-member-of-a-drifting-phase", "120 — Member of a drifting phase")
        add("done", DRIFTING, "98 — A drifting phase",
            "## Cards\n\n- 120 — Member of a drifting phase\n- a line naming no card\n")

    @classmethod
    def tearDownClass(cls):
        for path in cls.files:
            path.unlink(missing_ok=True)
        for stage in ("in-progress", "done"):
            (S["backend"].work / "tasks" / stage / f"{LAST}.md").unlink(missing_ok=True)
        S["browser"].eval("localStorage.removeItem('bench-theme')")

    def open(self, width=1440, height=900):
        b = S["browser"]
        b.viewport(width, height)
        b.goto("/phases")
        if not b.wait(f"!!document.querySelector('{lane(DONE_PHASE)}')"
                      f" && !!document.querySelector('{lane(PART_PHASE)}')"
                      f" && document.querySelectorAll('#lanes .lane').length >= 12", 15):
            raise bv.CheckFailed("the Phases view never drew the fixture's lanes")
        b.eval("document.activeElement && document.activeElement.blur()")
        return b

    def facts(self, stem):
        return S["browser"].eval(
            f"(() => {{ const l = document.querySelector('{lane(stem)}'); if (!l) return null;"
            " const st = l.querySelector('.lstages'), lg = l.querySelector('.plog'),"
            " t = l.querySelector('.ltoggle'), sum = l.querySelector('.lsum');"
            " return { closed: l.classList.contains('closed'),"
            " stagesShown: !!st.getClientRects().length, logShown: !!(lg && lg.getClientRects().length),"
            " toggle: t ? { expanded: t.getAttribute('aria-expanded'),"
            "   controls: t.getAttribute('aria-controls'), name: t.getAttribute('aria-label') } : null,"
            " summary: sum ? sum.innerText : null,"
            " height: Math.round(l.getBoundingClientRect().height) }; })()")

    def test_1_a_complete_phase_is_closed_on_first_load_and_a_partial_one_is_not(self):
        b = self.open()
        done, part = self.facts(DONE_PHASE), self.facts(PART_PHASE)
        self.assertTrue(done["closed"], done)
        self.assertFalse(done["stagesShown"] or done["logShown"], done)
        self.assertEqual(done["toggle"]["expanded"], "false")
        for target in done["toggle"]["controls"].split():
            self.assertTrue(b.eval(f"!!document.getElementById({json.dumps(target)})"),
                            f"aria-controls names {target}, which is not on the page")
        self.assertIn("merged into the phase branch", done["summary"])
        self.assertIn("not delivered", done["summary"])
        self.assertIn("Open the phase card", done["summary"])
        self.assertFalse(part["closed"], part)
        self.assertTrue(part["stagesShown"] and part["logShown"], part)
        self.assertIsNone(part["toggle"], "an unfinished lane has nothing to fold")
        self.assertIsNone(part["summary"])
        self.assertLess(done["height"], part["height"] / 2, (done, part))
        # a delivered phase says delivered, not merely merged
        finished = self.facts(f"{MANY[0][0]}-a-finished-phase")
        self.assertTrue(finished["closed"], finished)
        self.assertIn("Delivered", finished["summary"])
        drifting = self.facts(DRIFTING)
        self.assertFalse(drifting["closed"], f"a drifting phase closed itself: {drifting}")
        self.assertIsNone(drifting["toggle"], drifting)
        closed = b.eval("document.querySelectorAll('#lanes .lane.closed').length")
        self.assertGreaterEqual(closed, 11)
        metric("firstLoad", {"complete": done, "partial": part, "closedLanes": closed})
        shot("phase-lanes-first-load-1440")

    def test_2_the_last_member_merging_closes_the_lane_live_and_reopening_work_opens_it(self):
        b = self.open()
        b.eval("window.__noReload = 1")
        self.assertFalse(self.facts(PART_PHASE)["closed"])
        work = S["backend"].work / "tasks"
        moved = write("done", LAST, "86 — The last member")
        (work / "in-progress" / f"{LAST}.md").unlink()
        try:
            if not b.wait(f"document.querySelector('{lane(PART_PHASE)}').classList.contains('closed')", 15):
                raise bv.CheckFailed("the lane did not close when its last member landed")
            self.assertEqual(b.eval("window.__noReload"), 1, "the page reloaded")
            closed = self.facts(PART_PHASE)
            self.assertIn("All 2 cards merged", closed["summary"])
            shot("phase-lanes-live-close-1440")
        finally:
            write("in-progress", LAST, "86 — The last member")
            moved.unlink(missing_ok=True)
        if not b.wait(f"!document.querySelector('{lane(PART_PHASE)}').classList.contains('closed')", 15):
            raise bv.CheckFailed("the lane did not open again when work came back")
        again = self.facts(PART_PHASE)
        self.assertTrue(again["stagesShown"], again)
        self.assertEqual(b.eval("window.__noReload"), 1, "the page reloaded")
        metric("liveUpdate", {"closed": closed, "reopened": again})

    def test_3_keyboard_and_pointer_toggle_and_the_choice_survives_a_redraw(self):
        b = self.open()
        toggle = f"{lane(DONE_PHASE)} .ltoggle"
        b.eval(f"document.querySelector('{toggle}').focus()")
        b.key("Enter")
        if not b.wait(f"!document.querySelector('{lane(DONE_PHASE)}').classList.contains('closed')", 5):
            raise bv.CheckFailed("Enter on the toggle did not open the lane")
        opened = self.facts(DONE_PHASE)
        self.assertTrue(opened["stagesShown"] and opened["logShown"], opened)
        self.assertEqual(opened["toggle"]["expanded"], "true")
        focused = f"document.activeElement === document.querySelector('{toggle}')"
        self.assertTrue(b.eval(focused), "focus left the toggle")
        scroll = b.eval("document.querySelector('#view-phases').scrollTop")
        # ordinary redraws and a fresh reading of the board
        b.eval("render(); render();")
        b.eval("loadState()")
        b.wait("true", 1)
        self.assertFalse(self.facts(DONE_PHASE)["closed"], "a redraw closed a lane a person opened")
        self.assertTrue(b.eval(focused), "a redraw took focus off the toggle")
        self.assertEqual(scroll, b.eval("document.querySelector('#view-phases').scrollTop"))
        shot("phase-lanes-reopened-1440")
        b.key("Space")
        if not b.wait(f"document.querySelector('{lane(DONE_PHASE)}').classList.contains('closed')", 5):
            raise bv.CheckFailed("Space on the toggle did not close the lane")
        self.assertTrue(b.eval(focused), "focus left the toggle on closing")
        b.click(toggle)
        if not b.wait(f"!document.querySelector('{lane(DONE_PHASE)}').classList.contains('closed')", 5):
            raise bv.CheckFailed("a click on the toggle did not open the lane")
        b.click(toggle)
        if not b.wait(f"document.querySelector('{lane(DONE_PHASE)}').classList.contains('closed')", 5):
            raise bv.CheckFailed("a second click did not close the lane")
        # nothing on disk moved: the toggle is presentation
        work = S["backend"].work / "tasks"
        self.assertTrue((work / "review" / f"{DONE_PHASE}.md").exists())
        self.assertTrue((work / "review" / "82-first-member.md").exists())

    def test_4_both_themes_at_desktop_and_narrow_widths(self):
        found = {}
        for dark in (False, True):
            for w, h in ((1440, 900), (390, 780)):
                b = self.open(w, h)
                b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
                # colours ease over .14s: measure the theme, not the tween
                b.wait("false", 0.6)
                which = "dark" if dark else "light"
                self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), w + 1,
                                     f"the Phases view overflows at {w}px {which}")
                m = b.eval(
                    f"(() => {{ const l = document.querySelector('{lane(DONE_PHASE)}');"
                    " const t = l.querySelector('.ltoggle'), s = l.querySelector('.lsum b'),"
                    " a = l.querySelector('.lsum .lsacts .chip2'), r = l.getBoundingClientRect(),"
                    " tr = t.getBoundingClientRect();"
                    f" const c = {CONTRAST};"
                    " return { toggleInside: tr.left >= r.left - 1 && tr.right <= r.right + 1,"
                    " toggleH: Math.round(tr.height), summaryContrast: c(s), toggleContrast: c(t),"
                    " actionContrast: c(a), laneH: Math.round(r.height) }; })()")
                self.assertTrue(m["toggleInside"], m)
                for k in ("summaryContrast", "toggleContrast", "actionContrast"):
                    self.assertGreaterEqual(m[k], 4.5, f"{k} at {w}px {which}: {m}")
                found[f"{which}-{w}"] = m
                shot(f"phase-lanes-{which}-{w}")
        b.eval("localStorage.removeItem('bench-theme')")
        metric("themes", found)


if __name__ == "__main__":
    unittest.main()
