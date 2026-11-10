"""The configuration check and the launch check, in a browser (task 79).

Both entry points to the board-wide check (the header's ⚕ chip and
Settings → Diagnostics) and the task entry point (a card's launch check),
measured against the disposable backend. The states are the backend's
own: an empty board, a policy document that does not parse (partial), one
requiring a guarantee nothing enforces (unsupported), one missing a
required input (blocked), a reading the policy has since moved under
(stale) and a check that could not be asked (error). Only the execution
failure is a fixture, made by editing a report the backend really
returned, because bench's own controlled execution is built to pass.

Nothing here launches a model: the vendor CLI is the backend's refusing
stub, and the one launch fired is the stale-result case, which the
launch's own preflight refuses before any process. Paths are copied
through a recorded clipboard, since a headless browser's clipboard cannot
be read back. Run through verify/browser.
"""

import json
import unittest

import benchverify as bv

S = bv.SESSION
DIALOG ="#sheetwrap [role=dialog]"
CARD = "03-measure-the-board-layout.md"
REVIEW_CARD = "04-check-keyboard-focus.md"
STACKED = "07-build-on-a-stacked-branch"
STACKED_BASE = ("task/74-a-deliberately-long-stacked-base-branch-name-"
                "that-has-to-wrap-inside-a-narrow-sheet")


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


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


def load(width=1440, height=900, zoom=None):
    b = S["browser"]
    if zoom:
        b.zoom(width, height, zoom)
    else:
        b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    b.eval("document.activeElement && document.activeElement.blur()")
    return b


def policy_path():
    return S["backend"].tool / "manager" / "local" / "profiles.json"


class Policy:
    """Writes this board's execution policy for one test and puts back
    whatever was there."""

    def __enter__(self):
        self.path = policy_path()
        self.saved = self.path.read_bytes() if self.path.exists() else None
        return self

    def write(self, document=None, raw=None):
        self.path.write_text(raw if raw is not None else json.dumps(document),
                             encoding="utf-8")

    def __exit__(self, *exc):
        if self.saved is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(self.saved)


def open_global():
    """Through the header chip, by keyboard, as a person reaches it."""
    b = S["browser"]
    b.eval("document.querySelector('#doctorchip').focus()")
    b.key("Enter")
    if not b.wait(f"!!document.querySelector('{DIALOG} .ccheck, {DIALOG} .cissue')", 20):
        raise bv.CheckFailed("the configuration check never drew a result")
    return b


def close_sheet():
    b = S["browser"]
    b.key("Escape")
    if not b.wait("!document.querySelector('#sheetwrap').classList.contains('open')", 5):
        raise bv.CheckFailed("Escape did not close the sheet")


def lead_text():
    return S["browser"].eval(
        f"(() => {{ const d = document.querySelector('{DIALOG} .ccheck');"
        " if (!d) return ''; let t = '';"
        " for (const el of d.children) { if (el.tagName === 'DETAILS') break;"
        " t += el.innerText + '\\n'; } return t; })()")


def open_launch(card=CARD):
    """Through the card's tools, as a person reaches it."""
    b = S["browser"]
    sel = f'#board article.card[data-file="{card}"]'
    if not b.wait(f"!!document.querySelector('{sel}')", 10):
        raise bv.CheckFailed(f"{card} is not on the board")
    # A card just written to disk is redrawn when the watcher notices it,
    # and a click that lands on the card being replaced opens nothing: so
    # the ⋯ is clicked again while its menu is still closed.
    chip = f"{sel} .cardtools.open [data-profile]"
    for _ in range(4):
        if not b.eval(f"!!document.querySelector('{sel} .cardtools.open')"):
            b.click(f"{sel} [data-more]")
        if b.wait(f"!!document.querySelector('{chip}')", 3):
            break
    else:
        raise bv.CheckFailed(f"{card}'s tools carry no launch check")
    b.click(chip)
    if not b.wait(f"!!document.querySelector('{DIALOG} .lrows, {DIALOG} .cissue')", 30):
        raise bv.CheckFailed("the launch check never resolved")
    return b


def rows():
    """The launch check's rows, label → value text."""
    return S["browser"].eval(
        f"Object.fromEntries([...document.querySelectorAll('{DIALOG} > .lrows .lrow')]"
        ".map(r => [r.querySelector('.k').innerText, r.querySelector('.v').innerText]))")


def no_narrow_columns(width):
    """Every value column in the open sheet is at least half its row: a long
    path or branch makes a taller row, never a column of single words."""
    return S["browser"].eval(
        f"[...document.querySelectorAll('{DIALOG} .lrow')]"
        ".filter(r => r.offsetParent)"
        ".map(r => [r.querySelector('.k').innerText,"
        " Math.round(r.querySelector('.v').getBoundingClientRect().width),"
        " Math.round(r.getBoundingClientRect().width),"
        " getComputedStyle(r.querySelector('.v')).textAlign])"
        f".filter(([k, v, w, a]) => v < w * {0.45 if width > 520 else 0.9} || a !== 'left'"
        " && a !== 'start')")


class GlobalCheck(unittest.TestCase):
    def test_an_inspected_only_board_reads_a_specific_result_and_one_scope(self):
        b = load(1440, 900)
        open_global()
        lead = lead_text()
        S["metrics"]["configCheck"] = {"lead": lead}
        shot("config-check-after-1440x900")
        self.assertIn("No configuration issues found", lead)
        self.assertEqual(1, lead.count("Agent execution has not been tested."), lead)
        for caveat in ("nothing failing", "a reading is not a proof",
                       "spends nothing", "What would happen"):
            self.assertNotIn(caveat, b.eval(f"document.querySelector('{DIALOG}').innerText"))
        self.assertNotIn("✓", lead)
        # focus went into the dialog, on its title
        self.assertEqual("cc-title", b.eval("document.activeElement.id"))
        # successful detail is collapsed, and still one click away
        fold = f"{DIALOG} details.cfold"
        summaries = b.eval(f"[...document.querySelectorAll('{fold}')].map(d =>"
                           " [d.querySelector('summary').innerText, d.open])")
        passed = [s for s in summaries if "with no issues" in s[0]]
        self.assertTrue(passed and not passed[0][1], summaries)
        b.eval(f"[...document.querySelectorAll('{fold}')].find(d => d.querySelector"
               "('summary').innerText.includes('with no issues')).querySelector('summary').click()")
        self.assertIn("Configuration valid", b.eval(f"document.querySelector('{DIALOG}').innerText"))
        # nothing untested reads as passed
        untested = b.eval(f"[...document.querySelectorAll('{fold}')].some(d => d.querySelector"
                          "('summary').innerText.includes('not tested'))")
        b.eval(f"[...document.querySelectorAll('{fold}')].find(d => d.querySelector"
               "('summary').innerText.includes('not tested')).querySelector('summary').click()")
        words = b.eval(f"(() => {{ const d = [...document.querySelectorAll('{fold}')].find(d =>"
                       " d.querySelector('summary').innerText.includes('not tested'));"
                       " return [...d.querySelectorAll('.cbadge')].map(e => e.innerText); })()")
        self.assertTrue(untested)
        self.assertTrue(words)
        for word in words:
            self.assertNotIn(word, ("Configuration valid", "Execution passed",
                                    "Conformance passed", "passed"))
        shot("config-check-expanded-1440x900")
        close_sheet()
        self.assertEqual("doctorchip", b.eval("document.activeElement.id"),
                         "closing did not give focus back to the chip")

    def test_switching_purposes_keeps_focus_and_narrows_the_lists(self):
        b = load(1440, 900)
        open_global()
        b.click(f'{DIALOG} [data-doctorintent="review"]')
        self.assertTrue(b.wait(f"document.querySelector('{DIALOG} [data-doctorintent=\"review\"]')"
                               ".getAttribute('aria-pressed') === 'true'", 5))
        self.assertEqual("review", b.eval("document.activeElement.dataset.doctorintent"))
        labels = b.eval(f"[...document.querySelectorAll('{DIALOG} [data-doctorintent]')]"
                        ".map(e => e.innerText)")
        self.assertEqual(["All purposes", "Implementation", "PR fixes", "Review",
                          "Card refinement"], labels)
        rows_ = b.eval(f"[...document.querySelectorAll('{DIALOG} .crow .ca')].map(e => e.innerText)")
        self.assertNotIn("Implementation", rows_)
        S["metrics"]["configCheckPurposes"] = labels
        shot("config-check-review-purpose")
        close_sheet()

    def test_paths_are_one_per_row_left_aligned_and_copyable(self):
        b = load(1440, 900)
        open_global()
        b.eval("(() => { window.__copied = []; Object.defineProperty(navigator, 'clipboard',"
               " {configurable: true, value: {writeText: (v) => {"
               " window.__copied.push(v); return Promise.resolve(); }}}); return true; })()")
        b.eval(f"[...document.querySelectorAll('{DIALOG} details.cfold > summary')]"
               ".find(s => s.innerText === 'Workspace locations').click()")
        paths = b.eval(f"[...document.querySelectorAll('{DIALOG} .lrow')]"
                       ".filter(r => r.offsetParent && r.querySelector('.ccopy'))"
                       ".map(r => [r.querySelector('.k').innerText, r.querySelector('.v').innerText,"
                       " r.querySelector('.ccopy').getAttribute('aria-label'),"
                       " getComputedStyle(r.querySelector('.v')).textAlign])")
        labels = [p[0] for p in paths]
        for label in ("Application", "Task storage", "Worktrees"):
            self.assertIn(label, labels)
        for label, value, aria, align in paths:
            self.assertEqual(f"Copy {label}", aria)
            self.assertIn(align, ("left", "start"))
            self.assertTrue(value.startswith("/"), value)
        self.assertTrue(dict((p[0], p[1]) for p in paths)["Application"].endswith("/app"),
                        paths)
        b.click(f"{DIALOG} .lrow .ccopy")
        self.assertTrue(b.wait("window.__copied.length === 1", 5), "Copy copied nothing")
        self.assertEqual(paths[0][1], b.eval("window.__copied[0]"))
        self.assertEqual("Copied", b.eval(f"document.querySelector('{DIALOG} .ccopy').innerText"))
        self.assertEqual([], no_narrow_columns(1440))
        S["metrics"]["configCheckPaths"] = paths
        shot("config-check-locations-1440x900")
        close_sheet()

    def test_narrow_and_zoomed_layouts_have_no_overflow_or_narrow_columns(self):
        found = {}
        for name, (w, h, z) in {"390x844": (390, 844, None),
                                "1280x800@200%": (1280, 800, 2)}.items():
            b = load(w, h, zoom=z)
            open_global()
            b.eval(f"[...document.querySelectorAll('{DIALOG} details.cfold')]"
                   ".forEach(d => d.open = true)")
            m = measure([DIALOG, f"{DIALOG} .cheadline"])
            narrow = no_narrow_columns(m["viewport"]["width"])
            found[name] = {**m, "narrow": narrow}
            shot(f"config-check-{name.replace('%', 'pct').replace('@', '-at-')}")
            self.assertEqual([], bv.layout_problems(m), name)
            self.assertEqual([], narrow, f"narrow value columns at {name}")
            close_sheet()
        S["metrics"]["configCheckNarrow"] = found


class GlobalStates(unittest.TestCase):
    """Each state the backend can actually be in, read through the sheet."""

    def lead_for(self, document=None, raw=None, name=None):
        with Policy() as p:
            p.write(document, raw)
            load(1440, 900)
            open_global()
            lead = lead_text()
            if name:
                shot(name)
            close_sheet()
        return lead

    def test_a_policy_that_does_not_parse_is_partial_and_names_its_setting(self):
        lead = self.lead_for(raw="{ not json", name="config-check-partial")
        self.assertIn("partially inspected", lead)
        self.assertIn("Execution permissions", lead)
        self.assertIn("Invalid", lead)
        self.assertIn("Fix:", lead)
        self.assertNotIn("No configuration issues found", lead)

    def test_an_unsupported_requirement_names_the_purpose_it_blocks(self):
        lead = self.lead_for({"v": 1, "profiles": {"review": {"requires": [
            "filesystem-sandbox"]}}}, name="config-check-unsupported")
        self.assertRegex(lead, r"\d+ configuration issues? found")
        self.assertIn("Review", lead)
        self.assertIn("Unsupported", lead)
        self.assertIn("inspected", lead)
        self.assertIn("requirement-unsupported", lead)

    def test_a_missing_required_input_blocks_implementation(self):
        lead = self.lead_for({"v": 1, "profiles": {"work": {"context": [
            {"name": "brief", "path": "tool:no-such-brief.md"}]}}},
            name="config-check-blocked")
        self.assertIn("Implementation", lead)
        self.assertIn("context-unreadable", lead)
        self.assertIn("Fix:", lead)

    def test_a_reading_the_policy_moved_under_reads_stale(self):
        with Policy() as p:
            p.write({"v": 1, "profiles": {"work": {}}})
            b = load(1440, 900)
            open_global()
            p.write({"v": 1, "profiles": {"work": {}, "review": {}}})
            b.eval("loadState()")
            if not b.wait("S.state.policy.revision !== S.doctor.report.policy.revision", 10):
                raise bv.CheckFailed("the board never saw the policy move")
            b.eval("renderDoctor(); paintDoctorSheet(S.doctor.report, null)")
            lead = lead_text()
            shot("config-check-stale")
            self.assertIn("Stale", lead)
            self.assertIn("stale", b.eval("document.querySelector('#doctorchip').innerText"))
            close_sheet()

    def test_a_check_that_could_not_be_asked_says_so(self):
        b = load(1440, 900)
        b.eval("(() => { window.__realFetch = window.__realFetch || window.fetch.bind(window);"
               " window.fetch = (u, o) => String(u) === '/api/doctor'"
               " ? Promise.resolve(new Response(JSON.stringify({error: 'fixture: the board"
               " could not build the report'}), {status: 500}))"
               " : window.__realFetch(u, o); return true; })()")
        b.eval("document.querySelector('#doctorchip').focus()")
        b.key("Enter")
        if not b.wait(f"!!document.querySelector('{DIALOG} .cissue')", 10):
            raise bv.CheckFailed("an error drew nothing")
        text = b.eval(f"document.querySelector('{DIALOG}').innerText")
        shot("config-check-error")
        self.assertIn("did not complete", text)
        self.assertIn("fixture: the board could not build the report", text)
        self.assertNotIn("No configuration issues found", text)
        close_sheet()
        b.eval("(() => { window.fetch = window.__realFetch; return true; })()")

    def test_execution_results_are_their_own_and_change_the_scope_line(self):
        b = load(1440, 900)
        open_global()
        # bench's own controlled execution, through its armed control
        b.eval(f"[...document.querySelectorAll('{DIALOG} details.cfold > summary')]"
               ".find(s => s.innerText === 'Run execution diagnostics').click()")
        b.click(f'{DIALOG} [data-doctormode="runtime"]')
        b.click(f'{DIALOG} [data-doctormode="runtime"]')
        if not b.wait("S.doctor && S.doctor.report.ran.includes('runtime')", 120):
            raise bv.CheckFailed("controlled execution never came back")
        lead = lead_text()
        shot("config-check-runtime-ran")
        self.assertIn("Controlled execution:", lead)
        self.assertNotIn("Agent execution has not been tested", lead)
        self.assertIn("installed agent CLI has not been tested", lead)
        # an execution failure, made from the report the backend returned
        b.eval("(() => { const r = S.doctor.report;"
               " const f = r.findings.find(x => x.mode === 'runtime');"
               " f.outcome = 'failed'; f.state = 'execution-failed';"
               " f.stateWord = 'Execution failed'; f.remedy = 'fixture: read the log';"
               " r.summary.issues = [f.id];"
               " r.summary.headline = 'No configuration issues found; 1 execution"
               " diagnostic failed';"
               " const st = r.summary.stages.find(s => s.mode === 'runtime');"
               " st.state = 'failed'; st.word = 'Failed (1)';"
               " paintDoctorSheet(r, null); })()")
        lead = lead_text()
        shot("config-check-execution-failed")
        self.assertIn("Execution failed", lead)
        self.assertIn("Controlled execution", lead)
        self.assertIn("No configuration issues found", lead)
        close_sheet()


def go_settings_diagnostics(width=1440, height=900):
    b = load(width, height)
    b.click('#views button[data-view="settings"]')
    if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= 5", 15):
        raise bv.CheckFailed("the settings sections never arrived")
    b.eval("(() => { S.settingsSection = 'diagnostics'; renderSettings(); return true; })()")
    if not b.wait("!!document.querySelector('#sbody [data-sinspect]')", 5):
        raise bv.CheckFailed("Settings → Diagnostics has no Check configuration")
    return b


class SettingsEntry(unittest.TestCase):
    def test_settings_draws_the_same_result_as_the_sheet(self):
        found = {}
        for w, h in ((1440, 900), (390, 844)):
            b = go_settings_diagnostics(w, h)
            self.assertIn("Not checked yet", b.eval("document.querySelector('#sbody').innerText"))
            b.click('#sbody [data-sinspect]')
            if not b.wait("!!document.querySelector('#sbody .cheadline')", 30):
                raise bv.CheckFailed("Check configuration drew no result")
            self.assertEqual(["inspect"], b.eval("S.doctor.report.ran"))
            head = b.eval("document.querySelector('#sbody .cheadline').innerText")
            b.click('#sbody [data-spurpose="work"]')
            self.assertTrue(b.wait("document.querySelector('#sbody [data-spurpose=\"work\"]')"
                                   ".getAttribute('aria-pressed') === 'true'", 5))
            self.assertEqual("work", b.eval("document.activeElement.dataset.spurpose"))
            m = measure(["#sbody .cheadline"])
            found[f"{w}x{h}"] = {"headline": head, **m}
            shot(f"settings-config-check-{w}x{h}")
            self.assertLessEqual(m["scrollWidth"], w + 1)
            sheet = load(1440, 900)
            open_global()
            self.assertEqual(head, sheet.eval(f"document.querySelector('{DIALOG} .cheadline')"
                                              ".innerText"))
            close_sheet()
        S["metrics"]["settingsConfigCheck"] = found


class LaunchCheck(unittest.TestCase):
    def test_an_in_progress_card_opens_on_implementation_with_its_plan(self):
        b = load(1440, 900)
        open_launch(CARD)
        r = rows()
        S["metrics"]["launchCheckWork"] = r
        shot("launch-check-implementation-1440x900")
        self.assertEqual("true", b.eval(f"document.querySelector('{DIALOG} [data-intent=\"work\"]')"
                                        ".getAttribute('aria-pressed')"))
        self.assertIn("Implementation", r["Purpose"])
        self.assertIn("main", r["Starts from"])
        self.assertIn("task/03-measure-the-board-layout", r["Branch"])
        self.assertIn("main", r["PR target"])
        self.assertTrue(r["Harness"].startswith("claude"), r)
        # no model was chosen, and none is invented
        self.assertTrue(r["Model"].startswith("harness default"), r)
        self.assertIn("none enforced", r["OS isolation"])
        self.assertIn("intended scope", r["Intended writes"])
        # the next action is the card's own, armed, and is not fired here
        self.assertEqual("▸ start work", b.eval(f"document.querySelector('{DIALOG} #lc-launch')"
                                               ".getAttribute('aria-label')"))
        text = b.eval(f"document.querySelector('{DIALOG}').innerText")
        self.assertIn("Merge rules, Bench checks, CI and review", text)
        self.assertEqual("lc-title", b.eval("document.activeElement.id"))
        close_sheet()

    def test_switching_to_review_shows_the_read_only_intent(self):
        b = load(1440, 900)
        open_launch(CARD)
        b.click(f'{DIALOG} [data-intent="review"]')
        # the sheet repaints through "Resolving…", which has no purpose
        # buttons, so the wait must not dereference one that is not there
        if not b.wait(f"document.querySelector('{DIALOG} [data-intent=\"review\"]')"
                      "?.getAttribute('aria-pressed') === 'true' && !!document.querySelector"
                      f"('{DIALOG} .lrows')", 15):
            raise bv.CheckFailed("the Review purpose never drew")
        r = rows()
        shot("launch-check-review")
        self.assertIn("relevance check", r["Purpose"])
        self.assertIn("read-only", r["Access"])
        self.assertNotIn("Branch", r)
        self.assertEqual("◔ still true?", b.eval(f"document.querySelector('{DIALOG} #lc-launch')"
                                                 ".getAttribute('aria-label')"))
        close_sheet()

    def test_a_review_card_opens_on_review(self):
        b = load(1440, 900)
        open_launch(REVIEW_CARD)
        self.assertEqual("true", b.eval(f"document.querySelector('{DIALOG} [data-intent=\"review\"]')"
                                        ".getAttribute('aria-pressed')"))
        close_sheet()

    def test_a_stacked_card_shows_its_own_branches_not_main(self):
        backend = S["backend"]
        backend._git(backend.app, "branch", STACKED_BASE)
        card = backend.work / "tasks" / "in-progress" / f"{STACKED}.md"
        card.write_text(f"# Build on a stacked branch\n\n**Status:** In Progress\n"
                        f"**Priority:** Low\n**Base branch:** {STACKED_BASE}\n\n"
                        "A disposable card stacked on another card's branch.\n",
                        encoding="utf-8")
        try:
            found = {}
            for name, (w, h, z) in {"1440x900": (1440, 900, None),
                                    "390x844": (390, 844, None),
                                    "1280x800@200%": (1280, 800, 2)}.items():
                b = load(w, h, zoom=z)
                open_launch(f"{STACKED}.md")
                r = rows()
                m = measure([DIALOG, f"{DIALOG} .stitle"])
                narrow = no_narrow_columns(m["viewport"]["width"])
                found[name] = {"rows": r, **m, "narrow": narrow}
                shot(f"launch-check-stacked-{name.replace('%', 'pct').replace('@', '-at-')}")
                self.assertTrue(r["Starts from"].startswith(STACKED_BASE), r)
                self.assertTrue(r["PR target"].startswith(STACKED_BASE), r)
                self.assertNotIn("main", r["Starts from"])
                self.assertEqual([], bv.layout_problems(m), name)
                self.assertEqual([], narrow, f"narrow value columns at {name}")
                close_sheet()
            S["metrics"]["launchCheckStacked"] = found
        finally:
            card.unlink(missing_ok=True)

    def test_a_blocked_launch_names_the_fix_and_offers_no_launch(self):
        with Policy() as p:
            p.write({"v": 1, "profiles": {"work": {"requires": ["filesystem-sandbox"]}}})
            b = load(1440, 900)
            open_launch(CARD)
            text = b.eval(f"document.querySelector('{DIALOG}').innerText")
            shot("launch-check-blocked")
            self.assertIn("Blocked — this launch would be refused", text)
            self.assertIn("Fix:", text)
            self.assertIn("requirement-unsupported", text)
            self.assertFalse(b.eval(f"!!document.querySelector('{DIALOG} #lc-launch')"))
            close_sheet()

    def test_a_stale_launch_check_cannot_bypass_the_launchs_own_preflight(self):
        """Opened while the launch was allowed, then the policy changes:
        firing the stale sheet's launch is refused by the server, with its
        reason, and nothing starts."""
        with Policy() as p:
            p.write({"v": 1, "profiles": {"work": {}}})
            b = load(1440, 900)
            open_launch(CARD)
            self.assertTrue(b.eval(f"!!document.querySelector('{DIALOG} #lc-launch')"))
            p.write({"v": 1, "profiles": {"work": {"requires": ["filesystem-sandbox"]}}})
            b.eval("(() => { window.__toasts = []; const t0 = toast;"
                   " window.toast = toast = (m, bad) => { window.__toasts.push([m, !!bad]);"
                   " return t0(m, bad); }; return true; })()")
            b.click(f"{DIALOG} #lc-launch")
            b.click(f"{DIALOG} #lc-launch")
            if not b.wait("window.__toasts.some(([m, bad]) => bad)", 30):
                raise bv.CheckFailed("the launch neither refused nor answered")
            refusal = b.eval("window.__toasts.filter(([m, bad]) => bad).map(([m]) => m)")
            S["metrics"]["launchCheckStaleRefusal"] = refusal
            shot("launch-check-stale-refused")
            self.assertTrue(any("filesystem-sandbox" in m for m in refusal), refusal)
            b.eval("loadState()")
            self.assertEqual([], b.eval("(S.state.agents || []).filter(a => a.task === "
                                        f"{json.dumps(CARD)})"))
            self.assertFalse((S["backend"].trees / CARD[:-3]).exists(),
                             "a refused launch left a worktree")
            close_sheet()


if __name__ == "__main__":
    unittest.main()
