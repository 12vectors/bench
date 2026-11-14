"""The board UI as it exists, measured in a browser against a disposable
backend serving five cards — one per stage. Run through verify/browser,
which provides the browser, the backend and the artifact directory; run
any other way these refuse rather than skip.

Every assertion is a measurement: a box inside the viewport, a document
no wider than the window, a control the keyboard reaches and a screen
reader can name. None of it is a judgement of how the board looks; the
screenshots are taken for a person to do that.
"""

import json
import re
import unittest

import benchverify as bv

STAGES = ["backlog", "to-do", "in-progress", "review", "done"]
# a card names itself once: a title that repeats its own number is drawn
# without it, because the card's top row already carries `#06`
TITLES = [re.sub(r"^\d+ — ", "", t)
          for cards in bv.FIXTURE_CARDS.values() for _, t, _ in cards]
WIDTHS = [(1440, 900), (1280, 800), (1024, 768)]
# 1280x800 at 200% zoom: the CSS viewport a zoomed-in reader actually has
ZOOMED = (640, 400)
HEADER = ["header", "#views", "#doctorchip", "#livechip", "#themebtn",
          "#refresh", "#statusbar"]
NAMED_ROLES = {"button", "link", "combobox", "textbox", "checkbox", "menuitem", "tab"}

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def measure(selectors) -> dict:
    return S["browser"].eval("(() => { const boxes = {};"
                             f" for (const s of {json.dumps(list(selectors))}) {{"
                             " const e = document.querySelector(s);"
                             " if (!e) { boxes[s] = null; continue; }"
                             " const r = e.getBoundingClientRect();"
                             " boxes[s] = [Math.round(r.x), Math.round(r.y),"
                             " Math.round(r.width), Math.round(r.height)]; }"
                             " return {viewport: {width: innerWidth, height: innerHeight},"
                             " scrollWidth: document.documentElement.scrollWidth, boxes}; })()")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1440, height=900):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    if not b.wait(" && ".join(f"document.querySelector('#board').innerText.includes({json.dumps(t)})"
                              for t in TITLES), 10):
        raise bv.CheckFailed("the board drew its columns but not every fixture card")
    b.eval("document.activeElement && document.activeElement.blur()")


class Board(unittest.TestCase):
    def test_board_draws_every_stage_and_card_from_the_backend(self):
        load()
        b = S["browser"]
        stages = b.eval("[...document.querySelectorAll('.drop[data-stage]')]"
                        ".map(d => d.dataset.stage)")
        self.assertEqual(STAGES, [s for s in stages if s in STAGES])
        text = b.eval("document.querySelector('#board').innerText")
        for title in TITLES:
            self.assertIn(title, text)
        self.assertIn("bench-verify fixture", b.eval("document.title"))
        self.assertEqual([], [e.get("exceptionDetails", {}).get("text") for e in b.console],
                         "the page threw while loading")
        S["metrics"]["board"] = {"stages": stages, "title": b.eval("document.title")}
        # not board-1440x900: the layout case takes that one at the same size
        shot("board-drawn-1440x900")

    def test_layout_fits_the_viewport_at_common_widths(self):
        found = {}
        for w, h in WIDTHS:
            load(w, h)
            m = measure(HEADER + ["#board"] + [f'.drop[data-stage="{s}"]' for s in STAGES])
            found[f"{w}x{h}"] = m
            problems = bv.layout_problems({**m, "boxes": {k: v for k, v in m["boxes"].items()
                                                          if k in HEADER}})
            if m["scrollWidth"] > w + 1:
                problems.append(f"horizontal overflow {m['scrollWidth']}px > {w}px")
            cols = sorted((m["boxes"][f'.drop[data-stage="{s}"]'] or [0, 0, 0, 0])
                          for s in STAGES)
            for a, c in zip(cols, cols[1:]):
                if a[0] + a[2] > c[0] + 1:
                    problems.append(f"stage columns overlap at {w}px: {a} / {c}")
            if any(not c[2] for c in cols):
                problems.append(f"a stage column has no width at {w}px")
            found[f"{w}x{h}"]["problems"] = problems
            shot(f"board-{w}x{h}")
            self.assertEqual([], problems, f"at {w}x{h}")
        S["metrics"]["layout"] = found


def card_sel(stem: str) -> str:
    return f'#board article.card[data-file="{stem}.md"]'


class CardFace(unittest.TestCase):
    """Card 76: one ID, a readable title, no routine status, and every tool
    one labelled control away — by pointer and by keyboard."""

    def test_routine_cards_name_themselves_once_and_carry_no_chrome(self):
        load()
        b = S["browser"]
        faces = b.eval("[...document.querySelectorAll('#board article.card')].map(c => ({"
                       " file: c.dataset.file,"
                       " refs: c.querySelectorAll('.ref').length,"
                       " title: c.querySelector('.title').innerText,"
                       " pills: c.querySelectorAll('.toprow .pill.status').length,"
                       " faceChips: c.querySelectorAll(':scope > .chiprow .chip2').length,"
                       " more: (c.querySelector('[data-more]') || {}).ariaLabel || null }))")
        S["metrics"]["cardFaces"] = faces
        self.assertEqual(len(TITLES), len(faces))
        for f in faces:
            self.assertEqual(1, f["refs"], f)
            self.assertNotRegex(f["title"], r"^\s*#?\d+\s*[—–-]", f"{f['file']} repeats its ID")
            # nothing in the fixture is live, failed, idle or awaiting a
            # verdict, so the column says everything a pill would have
            self.assertEqual(0, f["pills"], f"{f['file']} repeats its column's status")
            # …except a done card's archive, the one thing left to do on
            # it, which sits on its face (task 85)
            expected = 1 if f["file"].startswith("05-") else 0
            self.assertEqual(expected, f["faceChips"], f"{f['file']}: chips on its face")
        # cards with tools offer them through one labelled control; a done
        # card's only tool is on its face, so it has no empty menu
        archivable = [f for f in faces if f["file"].split("/")[-1][:2] in ("01", "02", "06")]
        for f in archivable:
            self.assertTrue(f["more"] and f["more"].startswith("Tools for #"), f)
        shot("cards-quiet-1440x900")

    def test_the_tools_menu_opens_and_closes_by_keyboard_and_returns_focus(self):
        load(1280, 800)
        b = S["browser"]
        more = f"{card_sel('01-write-the-onboarding-guide')} [data-more]"
        b.eval(f"document.querySelector('{more}').focus()")
        b.key("Enter")
        opened = b.wait(f"!!document.querySelector('{card_sel('01-write-the-onboarding-guide')}"
                        " .cardtools.open')", 5)
        self.assertTrue(opened, "Enter on ⋯ did not open the card's tools")
        self.assertTrue(b.wait("!!document.activeElement.closest('.cardtools.open')", 5),
                        "focus did not move into the opened tools")
        self.assertEqual("true", b.eval(f"document.querySelector('{more}')"
                                        ".getAttribute('aria-expanded')"))
        labels = b.eval(f"[...document.querySelectorAll('{card_sel('01-write-the-onboarding-guide')}"
                        " .cardtools.open .chip2:not(.onface)')].map(e => e.innerText.trim())")
        self.assertTrue(any("archive" in l for l in labels), labels)
        shot("cards-tools-open-1280x800")
        b.key("Escape")
        self.assertTrue(b.wait(f"!document.querySelector('{card_sel('01-write-the-onboarding-guide')}"
                               " .cardtools.open')", 5), "Escape did not close the tools")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.more"
                               " === '01-write-the-onboarding-guide.md'", 5),
                        "focus did not return to the ⋯ control")

    def test_the_detail_view_carries_every_tool_and_gives_focus_back(self):
        load(1280, 800)
        b = S["browser"]
        card = card_sel("02-tidy-the-settings-reference")
        b.eval(f"document.querySelector('{card}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')", 5),
                        "Enter on a focused card did not open its detail view")
        tools = b.eval("[...document.querySelectorAll('#drawer .dtools .chip2')]"
                       ".map(e => e.innerText.trim())")
        S["metrics"]["detailTools"] = tools
        self.assertTrue(any("archive" in t for t in tools), tools)
        self.assertTrue(any("still true" in t for t in tools), tools)
        shot("card-detail-1280x800")
        b.click("#closeDrawer")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.file"
                               " === '02-tidy-the-settings-reference.md'", 5),
                        "closing the detail view did not return focus to its card")

    def test_cards_fit_at_390px_and_at_200_percent_zoom(self):
        found = {}
        for w, h in [(390, 780), ZOOMED]:
            load(w, h)
            b = S["browser"]
            m = b.eval("(() => { const out = [];"
                       " for (const c of document.querySelectorAll('#board article.card')) {"
                       "  const r = c.getBoundingClientRect(),"
                       "        t = c.querySelector('.title').getBoundingClientRect();"
                       "  out.push({file: c.dataset.file, card: [r.x, r.width],"
                       "            title: [t.x, t.width], scroll: c.scrollWidth,"
                       "            client: c.clientWidth}); }"
                       " return {scrollWidth: document.documentElement.scrollWidth,"
                       "         cards: out}; })()")
            found[f"{w}x{h}"] = m
            self.assertLessEqual(m["scrollWidth"], w + 1,
                                 f"page-wide horizontal overflow at {w}x{h}")
            for c in m["cards"]:
                self.assertLessEqual(c["scroll"], c["client"] + 1,
                                     f"{c['file']} overflows its own card at {w}x{h}")
                self.assertLessEqual(c["title"][0] + c["title"][1],
                                     c["card"][0] + c["card"][1] + 1,
                                     f"{c['file']}'s title is clipped at {w}x{h}")
            shot(f"cards-{w}x{h}")
        S["metrics"]["cardsNarrow"] = found


LONG_BRANCH = "phase/66-configurable-verifiable-headless-execution"
LONG_PHASE = ("74-configurable-verifiable-headless-execution-for-every-launch-intent")
LONG_CARDS = {
    # card 71 of the supplied screenshot: one long execution-plan target
    "71-gate-integration-and-acceptance": (
        "backlog", "71 — Gate integration and acceptance on current verification "
        "and review evidence", f"**Base branch:** {LONG_BRANCH}\n"),
    # two long branches, so the arrow variant has to fit as well
    "72-stacked-on-a-long-branch": (
        "backlog", "72 — Stacked on a long branch",
        "**Base branch:** task/56-external-workspaces-and-a-second-long-segment\n"
        "**PR base:** task/57-another-rather-long-pull-request-target-branch\n"),
    # one unbroken label: nothing to wrap at
    "73-an-unbroken-reference": (
        "backlog", "73 — An unbroken reference",
        "**Base branch:** release/" + "unbrokenreferencename" * 4 + "\n"),
    # a phase with a long title, and the member that wears it
    LONG_PHASE: (
        "to-do", "74 — Configurable, verifiable headless execution for every "
        "launch intent the board knows", "**Type:** Phase\n"),
    "75-a-member-of-the-long-phase": (
        "backlog", "75 — A member of the long phase", ""),
}
FIT_JS = """window.__fit = (host, chipSel) => {
  host = typeof host === 'string' ? document.querySelector(host) : host;
  if (!host) return null;
  const r = host.getBoundingClientRect(), s = getComputedStyle(host);
  const lo = r.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
  const hi = r.right - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight);
  const chips = [...host.querySelectorAll(chipSel)].filter(e => e.getClientRects().length)
    .map(e => { const b = e.getBoundingClientRect();
      return { text: e.textContent.replace(/\\s+/g, ' ').trim(),
        left: Math.round(b.left), right: Math.round(b.right),
        inside: b.left >= lo - 1 && b.right <= hi + 1,
        plan: e.hasAttribute('data-plan'), phase: e.hasAttribute('data-phase'),
        name: e.getAttribute('aria-label') || e.textContent.replace(/\\s+/g, ' ').trim(),
        parts: [...e.querySelectorAll('.g2, .cpos, .carr')].map(g => {
          const q = g.getBoundingClientRect();
          return { w: Math.round(q.width), whole: g.scrollWidth <= g.clientWidth + 1
                   || g.clientWidth === 0 && q.width > 0,
                   inside: q.left >= b.left - 1 && q.right <= b.right + 1 }; }),
        clipped: [...e.querySelectorAll('.clab')].some(l => l.scrollWidth > l.clientWidth + 1) }; });
  return { lo: Math.round(lo), hi: Math.round(hi), scroll: host.scrollWidth,
           client: host.clientWidth, chips };
};"""
# the board the screenshot showed: no containment at all, which is how the
# check proves it would have caught the spill
UNCONTAINED = (".chiprow .chip2,.cardtools .chip2{flex:none!important;max-width:none!important}"
               ".chip2 .clab{overflow:visible!important}")


class LongChips(unittest.TestCase):
    """Card 72: a chip carrying a long branch, phase or unbroken label
    stays inside its card — on the face, in the open tools row and in the
    detail view — at ordinary and narrow widths and at 200% zoom. Its glyphs
    and a phase position stay whole, its full value stays its accessible
    name, and the ⌖ preflight draws it whole beside a copy control."""

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        cls.files = []
        for stem, (stage, title, head) in LONG_CARDS.items():
            card = backend.work / "tasks" / stage / f"{stem}.md"
            body = "A disposable card for the long-chip case.\n"
            if stem == LONG_PHASE:
                body += "\n## Cards\n\n- 75 — A member of the long phase\n"
            card.write_text(f"# {title}\n\n**Status:** {bv.STATUS[stage]}\n**Priority:** Low\n"
                            f"{head}\n{body}", encoding="utf-8")
            cls.files.append(card)
        # card 71's base exists, so its preflight resolves rather than refusing
        backend._git(backend.app, "branch", LONG_BRANCH)

    @classmethod
    def tearDownClass(cls):
        for card in cls.files:
            card.unlink(missing_ok=True)
        try:
            S["backend"]._git(S["backend"].app, "branch", "-D", LONG_BRANCH)
        except bv.SetupError:
            pass

    def view(self, w, h, which="board"):
        load(w, h)
        b = S["browser"]
        if which != "board":
            b.click(f'#views button[data-view="{which}"]')
        stem = "75-a-member-of-the-long-phase" if which == "phases" else \
            "71-gate-integration-and-acceptance"
        self.assertTrue(b.wait(f"!!document.querySelector('#view-{which}.on "
                               f"article.card[data-file=\"{stem}.md\"] .chip2')", 10),
                        f"the {which} view never drew {stem} with its chips")
        b.eval(FIT_JS)

    def assert_fits(self, fit, where, *, want_plan=False, want_phase=False):
        self.assertIsNotNone(fit, f"{where}: nothing to measure")
        self.assertLessEqual(fit["scroll"], fit["client"] + 1, f"{where}: overflows its box")
        self.assertTrue(fit["chips"], f"{where}: no chips drawn")
        for c in fit["chips"]:
            self.assertTrue(c["inside"], f"{where}: {c['text']!r} spills past "
                            f"[{fit['lo']}, {fit['hi']}]: [{c['left']}, {c['right']}]")
            for p in c["parts"]:
                self.assertTrue(p["w"] > 0 and p["inside"] and p["whole"],
                                f"{where}: a glyph or position of {c['text']!r} is cut: {p}")
        if want_plan:
            self.assertTrue(any(c["plan"] for c in fit["chips"]), f"{where}: no ⌖ chip")
        if want_phase:
            self.assertTrue(any(c["phase"] for c in fit["chips"]), f"{where}: no ⟶ chip")

    def test_face_chips_stay_inside_their_cards(self):
        found = {}
        for w, h in [(1440, 900), (390, 780), ZOOMED]:
            self.view(w, h)
            b = S["browser"]
            for stem in ("71-gate-integration-and-acceptance", "72-stacked-on-a-long-branch",
                         "73-an-unbroken-reference"):
                fit = b.eval(f"__fit('{card_sel(stem)}', ':scope > .chiprow .chip2')")
                found[f"{stem}@{w}x{h}"] = fit
                self.assert_fits(fit, f"{stem} at {w}x{h}", want_plan=True)
                plan = next(c for c in fit["chips"] if c["plan"])
                # the stored value is whole in the chip's accessible name,
                # however much of it the width shows
                if stem.startswith("71-"):
                    self.assertIn(LONG_BRANCH, plan["name"])
            self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), w + 1,
                                 f"page-wide horizontal overflow at {w}x{h}")
            # a short label is not cut: the phase card's own summary chip
            short = b.eval(f"__fit('{card_sel(LONG_PHASE)}', ':scope > .chiprow .chip2')")
            self.assert_fits(short, f"the phase card at {w}x{h}", want_phase=True)
            self.assertFalse(any(c["clipped"] for c in short["chips"]),
                             f"a short chip label was ellipsized at {w}x{h}: {short}")
            shot(f"long-chips-face-{w}x{h}")
        # at the narrowest card the long target genuinely needs cutting:
        # proof the fixture exercises the ellipsis rather than fitting by luck
        narrow = found[f"71-gate-integration-and-acceptance@390x780"]
        self.assertTrue(any(c["clipped"] for c in narrow["chips"] if c["plan"]), narrow)
        S["metrics"]["longChipsFace"] = found

    def test_without_containment_the_same_card_spills(self):
        """The negative control, and the 'before' screenshot: with the fix's
        rules overridden, the measurement sees exactly the spill card 71's
        screenshot showed."""
        self.view(390, 780)
        b = S["browser"]
        b.eval("(() => { const s = document.createElement('style'); s.id = 'uncontained';"
               f" s.textContent = {json.dumps(UNCONTAINED)};"
               " document.head.appendChild(s); })()")
        try:
            fit = b.eval(f"__fit('{card_sel('71-gate-integration-and-acceptance')}',"
                         " ':scope > .chiprow .chip2')")
            shot("long-chips-uncontained-before-390x780")
        finally:
            b.eval("document.getElementById('uncontained').remove()")
        S["metrics"]["longChipsUncontained"] = fit
        self.assertTrue(any(c["plan"] and not c["inside"] for c in fit["chips"]),
                        f"without containment the ⌖ chip should spill at 390px: {fit}")
        shot("long-chips-contained-after-390x780")

    def test_phase_member_chips_fit_on_the_face_and_in_the_open_tools(self):
        found = {}
        member = "75-a-member-of-the-long-phase"
        sel = f'#view-phases article.card[data-file="{member}.md"]'
        for w, h in [(1440, 900), (390, 780), ZOOMED]:
            self.view(w, h, "phases")
            b = S["browser"]
            face = b.eval(f"__fit('{sel}', ':scope > .chiprow .chip2')")
            self.assert_fits(face, f"the member's face at {w}x{h}", want_phase=True)
            phase = next(c for c in face["chips"] if c["phase"])
            self.assertTrue(phase["text"].endswith("1/1"), phase)
            self.assertIn("Configurable, verifiable headless execution for every launch "
                          "intent the board knows", phase["name"])
            # the open tools row: a member's plan chip is there, not on its face
            b.eval(f"document.querySelector('{sel} [data-more]').click()")
            self.assertTrue(b.wait(f"!!document.querySelector('{sel} .cardtools.open')", 5),
                            f"the member's tools did not open at {w}x{h}")
            b.eval(FIT_JS)
            tools = b.eval(f"__fit('{sel}', '.cardtools.open .chip2')")
            self.assert_fits(tools, f"the member's open tools at {w}x{h}", want_plan=True)
            card = b.eval(f"__fit('{sel}', '.chip2')")
            self.assertLessEqual(card["scroll"], card["client"] + 1,
                                 f"the member card overflows with its tools open at {w}x{h}")
            plan = next(c for c in tools["chips"] if c["plan"])
            self.assertIn(f"phase/{LONG_PHASE}", plan["name"])
            found[f"{w}x{h}"] = {"face": face, "tools": tools}
            shot(f"long-chips-phase-tools-{w}x{h}")
            b.key("Escape")
        S["metrics"]["longChipsPhase"] = found

    def test_the_detail_views_tools_fit(self):
        found = {}
        stem = "72-stacked-on-a-long-branch"
        for w, h in [(1280, 800), (390, 780), ZOOMED]:
            self.view(w, h)
            b = S["browser"]
            b.eval(f"document.querySelector('{card_sel(stem)}').focus()")
            b.key("Enter")
            self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')"
                                   " && !!document.querySelector('#drawer .dtools .chip2')", 5),
                            f"the detail view did not open with its tools at {w}x{h}")
            b.eval(FIT_JS)
            fit = b.eval("__fit('#drawer .dtools', '.chip2')")
            self.assert_fits(fit, f"the detail tools at {w}x{h}", want_plan=True)
            arrow = next(c for c in fit["chips"] if c["plan"])
            self.assertIn("→", arrow["text"])
            found[f"{w}x{h}"] = fit
            shot(f"long-chips-detail-{w}x{h}")
            b.click("#closeDrawer")
        S["metrics"]["longChipsDetail"] = found

    def test_the_preflight_shows_the_whole_value_and_copies_it(self):
        self.view(390, 780)
        b = S["browser"]
        chip = f"{card_sel('71-gate-integration-and-acceptance')} > .chiprow [data-plan]"
        # by keyboard: the face chip is a button, so the value is not hover-only
        b.eval(f"document.querySelector('{chip}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#sheetwrap .prows.full')", 15),
                        "the ⌖ preflight did not resolve")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.id === 'pf-title'",
                               5), "the preflight did not take focus")
        m = b.eval("(() => { const sheet = document.querySelector('#sheetwrap .sheet');"
                   " const sr = sheet.getBoundingClientRect();"
                   " return { copies: [...sheet.querySelectorAll('[data-copy]')].map(c =>"
                   "   ({ value: c.dataset.copy, name: c.getAttribute('aria-label') })),"
                   "  values: [...sheet.querySelectorAll('.prows.full .v')].map(v => {"
                   "   const r = v.getBoundingClientRect(); return { text: v.textContent,"
                   "   cut: v.scrollWidth > v.clientWidth + 1,"
                   "   inside: r.left >= sr.left - 1 && r.right <= sr.right + 1 }; }) }; })()")
        S["metrics"]["longChipsPreflight"] = m
        shot("long-chips-preflight-390x780")
        self.assertIn(LONG_BRANCH, [c["value"] for c in m["copies"]], m)
        self.assertTrue(all(c["name"] and c["name"].startswith("Copy ") for c in m["copies"]), m)
        start = next(v for v in m["values"] if LONG_BRANCH in v["text"])
        self.assertFalse(start["cut"], f"the preflight cut the value it exists to show: {start}")
        self.assertTrue(all(v["inside"] for v in m["values"]), m)
        b.key("Escape")
        self.assertTrue(b.wait("document.activeElement && document.activeElement.matches"
                               "('[data-plan]')", 5),
                        "closing the preflight did not give focus back to the ⌖ chip")


class Views(unittest.TestCase):
    def test_each_view_opens_from_the_switcher_and_fits(self):
        load(1280, 800)
        b = S["browser"]
        seen = {}
        for view in ["phases", "flight", "focus", "board"]:
            b.click(f'#views button[data-view="{view}"]')
            ok = b.wait(f"document.querySelector('#view-{view}').classList.contains('on')"
                        f" && document.querySelectorAll('.view.on').length === 1", 5)
            self.assertTrue(ok, f"the {view} view did not become the only visible view")
            m = measure([f"#view-{view}", "#views"])
            pressed = b.eval(f"document.querySelector('#views button[data-view=\"{view}\"]')"
                             ".classList.contains('on')")
            self.assertTrue(pressed, f"the {view} switcher button is not marked current")
            box = m["boxes"][f"#view-{view}"]
            self.assertTrue(box and box[2] > 0 and box[3] > 0, f"#view-{view} has no size")
            self.assertLessEqual(m["scrollWidth"], 1281, f"{view} overflows horizontally")
            seen[view] = m
            shot(f"view-{view}")
        S["metrics"]["views"] = seen


class Readiness(unittest.TestCase):
    def test_the_readiness_inspector_is_reachable_and_dismissable_by_keyboard(self):
        b = S["browser"]
        dialog = "#sheetwrap [role=dialog]"
        S["metrics"]["tabOrder"], S["metrics"]["doctorchipFocus"] = {}, {}
        S["metrics"]["readinessSheet"] = {}
        for w, h in WIDTHS + [ZOOMED]:
            size = f"{w}x{h}"
            load(w, h)
            order = []
            for _ in range(40):
                b.key("Tab")
                here = b.eval("document.activeElement && (document.activeElement.id || "
                              "document.activeElement.tagName)")
                order.append(here)
                if here == "doctorchip":
                    break
            S["metrics"]["tabOrder"][size] = order
            self.assertEqual("doctorchip", order[-1],
                             f"Tab never reached the readiness chip at {size}: {order}")
            ring = b.eval("(() => { const s = getComputedStyle(document.activeElement);"
                          " return {outlineStyle: s.outlineStyle, outlineWidth: s.outlineWidth,"
                          " boxShadow: s.boxShadow}; })()")
            S["metrics"]["doctorchipFocus"][size] = ring
            visible = (ring["outlineStyle"] not in ("none", "") and
                       ring["outlineWidth"] not in ("0px", "")) or ring["boxShadow"] != "none"
            self.assertTrue(visible,
                            f"the focused readiness chip shows no focus indicator at {size}: {ring}")
            shot(f"readiness-focused-{w}x{h}")
            b.key("Enter")
            self.assertTrue(b.wait("document.querySelector('#sheetwrap').classList.contains('open')"
                                   f" && document.querySelector('{dialog}')", 10),
                            f"Enter on the readiness chip opened no dialog at {size}")
            b.wait("!document.querySelector('#sheetwrap').innerText.includes('…')", 10)
            m = measure([dialog, f"{dialog} .stitle"])
            m["scroll"] = b.eval(f"(() => {{ const d = document.querySelector('{dialog}');"
                                 " return {scrollHeight: d.scrollHeight, clientHeight: d.clientHeight,"
                                 " overflowY: getComputedStyle(d).overflowY}; })()")
            S["metrics"]["readinessSheet"][size] = m
            # taken first: a dialog that does not fit is the screenshot a
            # person most needs to see
            shot(f"readiness-open-{w}x{h}")
            self.assertEqual([], bv.layout_problems(m), f"at {size}")
            # since task 79 the configuration check leads with its result and
            # folds the rest, so at a large viewport it may fit; whatever its
            # length, the dialog scrolls inside itself rather than being cut off
            sc = m["scroll"]
            self.assertIn(sc["overflowY"], ("auto", "scroll"),
                          f"the readiness dialog cannot scroll its content at {size}")
            if sc["scrollHeight"] <= sc["clientHeight"]:
                self.assertLessEqual(m["boxes"][dialog][1] + m["boxes"][dialog][3],
                                     h + 1, f"the dialog runs off the viewport at {size}")
            # the actions at the end are reachable: scrolled to its end, the
            # Close button lies inside both the dialog and the viewport
            b.eval(f"(() => {{ const d = document.querySelector('{dialog}');"
                   " d.scrollTop = d.scrollHeight; })()")
            end = measure([dialog, "#sh-nodoc"])
            S["metrics"]["readinessSheet"][size]["scrolledToEnd"] = end
            shot(f"readiness-end-{w}x{h}")
            self.assertEqual([], bv.layout_problems(end), f"scrolled to its end at {size}")
            d, c = end["boxes"][dialog], end["boxes"]["#sh-nodoc"]
            self.assertTrue(c[1] >= d[1] - 1 and c[1] + c[3] <= d[1] + d[3] + 1,
                            f"the Close action lies outside the scrolled dialog at {size}: "
                            f"{c} / {d}")
            b.key("Escape")
            self.assertTrue(b.wait("!document.querySelector('#sheetwrap').classList.contains('open')",
                                   5), f"Escape did not close the readiness dialog at {size}")


class Discussion(unittest.TestCase):
    """Card 65: the detail view's discussion. A comment saved from the
    composer is on the card after a reload, is drawn as text, and starts
    nothing. Update card with agent is offered beside it and is never
    clicked here — it would launch a real model."""

    STEM = "02-tidy-the-settings-reference"

    def open_detail(self):
        b = S["browser"]
        b.eval(f"document.querySelector('{card_sel(self.STEM)}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("document.querySelector('#drawer').classList.contains('open')"
                               " && !!document.querySelector('#commentbox')", 5),
                        "the detail view did not open with a comment box")
        # the discussion and its composer live in the Brief section (task 81)
        b.click("#dtab-brief")
        self.assertTrue(b.wait("!document.querySelector('#dsec-brief').hidden", 5),
                        "the Brief section did not open")
        # "reading the discussion…" is itself a .dnote, so the placeholder
        # alone must not count as loaded
        self.assertTrue(b.wait("(() => { const l = document.querySelector('#disc-live');"
                               " return !!l && !!l.querySelector('.dnote, .dentry')"
                               " && !l.innerText.includes('reading the discussion'); })()",
                               10),
                        "the discussion never loaded")

    def test_a_comment_is_saved_shown_after_reload_and_starts_nothing(self):
        load(1280, 800)
        b = S["browser"]
        self.open_detail()
        words = "<b>not bold</b> Also cover switching external root A to B"
        b.eval("(() => { const t = document.querySelector('#commentbox');"
               f" t.value = {json.dumps(words)};"
               " t.dispatchEvent(new Event('input')); })()")
        b.click("#addComment")
        self.assertTrue(b.wait("document.querySelector('#disc-live').innerText"
                               ".includes('Also cover switching external root A to B')", 10),
                        "the saved comment did not appear in the discussion")
        self.assertEqual(0, b.eval("document.querySelectorAll('#disc-live .cbody b').length"),
                         "a comment's text was rendered as markup")
        self.assertEqual("", b.eval("document.querySelector('#commentbox').value"),
                         "the composer was not cleared after saving")
        self.assertTrue(b.wait("S.state && S.state.agents.length === 0", 5),
                        "saving a comment started an agent")
        refine = b.eval("(() => { const e = document.querySelector('#refineNew');"
                        " return e && e.innerText.trim(); })()")
        self.assertIn("Update card with agent", refine or "")
        shot("card-discussion-1280x800")

        load(1280, 800)
        self.open_detail()
        self.assertTrue(b.wait("document.querySelector('#disc-live').innerText"
                               ".includes('Also cover switching external root A to B')", 10),
                        "the comment did not survive a reload")
        self.assertNotIn("bench:entry", b.eval("document.querySelector('#drawer .dbody')"
                                               ".innerText"),
                         "the discussion leaked into the rendered brief")
        b.click("#closeDrawer")


class Accessibility(unittest.TestCase):
    def test_every_interactive_control_has_an_accessible_name(self):
        load(1280, 800)
        nodes = S["browser"].ax_nodes()
        controls, unnamed = 0, []
        for n in nodes:
            if n.get("ignored"):
                continue
            role = (n.get("role") or {}).get("value")
            if role not in NAMED_ROLES:
                continue
            controls += 1
            if not ((n.get("name") or {}).get("value") or "").strip():
                unnamed.append({"role": role, "nodeId": n.get("nodeId"),
                                "backendDOMNodeId": n.get("backendDOMNodeId")})
        S["metrics"]["accessibility"] = {"controls": controls, "unnamed": unnamed}
        self.assertGreater(controls, 5, "the accessibility tree has almost no controls")
        self.assertEqual([], unnamed, "controls with no accessible name")
        shot("accessibility-1280x800")
