"""The Settings view, measured in a browser against the disposable
backend — and against that backend's *real* state, which these cases
change as they go.

The backend starts with no execution policy document at all, so the first
cases read a genuinely unconfigured board: the recommendation to register
a check is there because nothing is registered, not because a string says
so. Then the editor registers one through the page — draft, validate,
preview, apply — and the later cases read a configured board. The error
state is a draft the resolver refuses, with the field named.

Every assertion is a measurement or a fact read out of the DOM: a box
inside the viewport, a document no wider than the window, a control the
keyboard reaches and a screen reader can name, a file on disk that
changed or did not. None of it is a judgement of how the page looks; the
screenshots are taken for a person to do that.

Run through verify/browser, which provides the browser, the backend and
the artifact directory; run any other way these refuse rather than skip.
"""

import json
import unittest

import benchverify as bv

SECTIONS = ["overview", "verification", "acceptance", "agents", "access",
            "diagnostics", "installation", "policy"]
# 390px is the narrow layout the card names; 640x400 is 1280x800 at 200%
# zoom in CSS pixels (not actual browser zoom — see verify/README.md)
WIDTHS = [(1440, 900), (1024, 768), (640, 400), (390, 780)]
NAMED_ROLES = {"button", "link", "combobox", "textbox", "checkbox",
               "menuitem", "tab"}

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


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


def go_section(section: str):
    """Choose a section the way a person does at this width: straight from
    the sidebar on a desktop, or — below 760px, where the sidebar folds
    into one Sections button — by opening that button first."""
    b = S["browser"]
    item = f'#snav [data-ssection="{section}"]'
    if b.eval(f"document.querySelector('{item}').offsetParent === null"):
        b.click('#snavtoggle')
        if not b.wait(f"document.querySelector('{item}').offsetParent !== null", 5):
            raise bv.CheckFailed("the Sections button did not open the section list")
    b.click(item)
    if not b.wait(f"document.querySelector('{item}')"
                  ".getAttribute('aria-current') === 'page'", 5):
        raise bv.CheckFailed(f"the {section} section did not become current")


def set_theme(theme: str):
    """Through the header's own theme button, which is what a person uses."""
    b = S["browser"]
    if b.eval("document.documentElement.dataset.theme") != theme:
        b.click('#themebtn')
    if not b.wait(f"document.documentElement.dataset.theme === '{theme}'", 5):
        raise bv.CheckFailed(f"the theme button did not switch to {theme}")


def open_settings(width=1440, height=900, section="overview", theme="dark"):
    """The view, through the header switcher a person actually uses."""
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    set_theme(theme)
    b.click('#views button[data-view="settings"]')
    if not b.wait("document.querySelector('#view-settings')"
                  ".classList.contains('on')", 5):
        raise bv.CheckFailed("the Settings switcher opened no view")
    if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= "
                  f"{len(SECTIONS)}", 15):
        raise bv.CheckFailed("the settings sections never arrived from the "
                             "backend")
    if section != "overview":
        go_section(section)
    b.eval("document.activeElement && document.activeElement.blur()")
    return b


def draft_in(doc: dict) -> None:
    """Replace the editor's draft through its own JSON escape hatch — the
    one control that can express a whole document, and the one a test can
    drive without typing into fourteen fields."""
    b = S["browser"]
    # opened the way a person opens it: the fold sits at the bottom of a
    # long section, so this also proves the click reaches it there
    if not b.eval("document.querySelector('#s-rawfold').open"):
        b.click('#s-rawfold > summary')
    if not b.wait("document.querySelector('#s-rawfold').open"
                  " && document.querySelector('#s-raw').offsetHeight > 0", 5):
        raise bv.CheckFailed("the raw document field never opened")
    b.eval("(() => { const t = document.querySelector('#s-raw');"
           f" t.value = {json.dumps(json.dumps(doc, indent=2))};"
           " t.dispatchEvent(new Event('input', {bubbles: true})); })()")
    b.click('#s-useraw')
    # `S` is a top-level `const`, so it is a global binding and not a
    # property of `window` — `window.S` would be undefined here
    if not b.wait("!!(typeof S !== 'undefined' && S.draft && S.draft.dirty)", 5):
        raise bv.CheckFailed("the draft was not taken from the JSON field")


class Navigation(unittest.TestCase):
    def test_settings_is_reachable_from_the_header_and_draws_every_section(self):
        b = open_settings()
        current = b.eval("[...document.querySelectorAll('#snav [data-ssection]')]"
                         ".map(e => e.dataset.ssection)")
        self.assertEqual(SECTIONS, current)
        self.assertEqual(1, b.eval("document.querySelectorAll('.view.on').length"))
        self.assertIn("settings", b.eval("document.title"))
        self.assertEqual([], [e.get("exceptionDetails", {}).get("text")
                              for e in b.console],
                         "the page threw while opening Settings")
        S["metrics"]["settingsSections"] = current
        shot("settings-overview-1440x900")

    def test_every_section_opens_and_fits_at_every_width(self):
        found = {}
        for w, h in WIDTHS:
            for section in SECTIONS:
                b = open_settings(w, h, section)
                # The way to the sections — the current sidebar item on a
                # desktop, the Sections button below 760px — must sit wholly
                # inside the window. The section head scrolls with the
                # content, so of it only the sideways fit is asserted.
                way = ('#snavtoggle' if b.eval(
                    "document.querySelector('#snavtoggle').offsetParent !== null")
                    else '#snav [aria-current="page"]')
                m = measure(["#view-settings", "#shead", "#sside", way, "#sbody",
                             "header", "#views"])
                problems = bv.layout_problems(
                    {**m, "boxes": {k: v for k, v in m["boxes"].items()
                                    if k in (way, "header", "#views")}})
                head = m["boxes"]["#shead"]
                if not head or head[0] < -1 or head[0] + head[2] > w + 1:
                    problems.append(f"the section head extends outside the "
                                    f"{w}px viewport: {head}")
                if m["scrollWidth"] > w + 1:
                    problems.append(f"horizontal overflow "
                                    f"{m['scrollWidth']}px > {w}px")
                body = m["boxes"]["#sbody"]
                if not body or body[2] <= 0:
                    problems.append("the section body has no width")
                elif body[0] < -1 or body[0] + body[2] > w + 1:
                    problems.append(f"the section body extends outside the "
                                    f"{w}px viewport: {body}")
                found[f"{section}@{w}x{h}"] = {**m, "problems": problems}
                self.assertEqual([], problems, f"{section} at {w}x{h}")
            shot(f"settings-sections-{w}x{h}")
        S["metrics"]["settingsLayout"] = found
        # said in the artifact itself, so nobody reads the 640x400 shots as
        # a zoom test: no browser zoom level was set for them
        S["metrics"]["settingsZoom"] = {
            "viewport": "640x400",
            "kind": "css-viewport-approximation",
            "of": "1280x800 at 200% zoom",
            "actualBrowserZoom": False,
        }


class EmptyAndConfiguredAndErrorStates(unittest.TestCase):
    def test_an_unconfigured_board_says_so_and_recommends_what_to_supply(self):
        """The backend has no policy document, so this is the empty state
        read from real resolution rather than from a hardcoded string."""
        b = open_settings()
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("not-configured", text)
        self.assertIn("no check is registered", text)
        # card 76: values first, in the task's words, with the limitations
        # that change what a merge means kept in view
        for words in ("None registered", "No merge requirements",
                      "Not inspected", "Unavailable", "4 profiles"):
            self.assertIn(words, text)
        # the recommendations are one click away rather than the headline
        b.eval("document.querySelector('#sbody details.srecs').open = true")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("Register your project's test program", text)
        self.assertIn("You supply:", text)
        # and nothing anywhere reads as a pass
        self.assertNotIn("verified", text)
        states = b.eval("[...document.querySelectorAll('#sbody .sw')]"
                        ".map(e => e.textContent)")
        self.assertTrue(states, "no state is drawn as a word")
        self.assertNotIn("verified", states)
        S["metrics"]["settingsEmptyStates"] = states
        shot("settings-empty-overview")

    def test_a_refused_draft_shows_the_field_and_writes_nothing(self):
        b = open_settings(1440, 900, "policy")
        draft_in({"v": 1,
                  "checks": [{"name": "unit", "run": "python3 -m unittest",
                              "root": "app"}]})
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked)", 15):
            raise bv.CheckFailed("validate never came back")
        ok = b.eval("S.draft.checked.ok")
        self.assertFalse(ok, "a check at the application checkout validated")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("check-root-unsafe", text)
        self.assertIn("would be refused", text)
        self.assertIn("Nothing has been written", text)
        # Apply is still unavailable
        self.assertTrue(b.eval("document.querySelector('#s-apply').disabled"))
        S["metrics"]["settingsRefusedDraft"] = b.eval(
            "S.draft.checked.errors.map(e => e.code + '|' + (e.field || ''))")
        shot("settings-error-state")

    def test_the_round_trip_registers_a_check_the_backend_then_resolves(self):
        """Draft, validate, preview, apply — and then the configured state,
        read back from the backend rather than from the page's own memory."""
        b = open_settings(1440, 900, "policy")
        draft_in({"v": 1,
                  "checks": [{"name": "unit",
                              "run": "python3 -m unittest discover -s tests",
                              "root": "worktree"}],
                  "profiles": {"work": {"verify": ["unit"]}}})
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked && S.draft.checked.ok)", 15):
            raise bv.CheckFailed("a valid draft did not validate: " +
                                 str(b.eval("S.draft && S.draft.checked")))
        b.click('#s-preview')
        if not b.wait("!!(S.draft && S.draft.preview)", 15):
            raise bv.CheckFailed("preview never came back")
        changes = b.eval("S.draft.preview.changes.map(c => c.kind)")
        self.assertIn("check-added", changes, changes)
        self.assertIn("access-changed", changes, changes)
        preview_text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("nothing restarts", preview_text)
        self.assertIn("no card moves", preview_text)
        shot("settings-preview")

        # the write is armed: the first click arms, the second fires
        b.click('#s-apply')
        if not b.wait("document.querySelector('#s-apply')"
                      ".classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm on the first click")
        b.click('#s-apply')
        if not b.wait("!!(S.settings && S.settings.page.overview"
                      ".policy.kind === 'file')", 20):
            raise bv.CheckFailed("the applied document never reached the "
                                 "backend: " +
                                 str(b.eval("S.settings && S.settings.page"
                                            ".overview.policy")))
        # Apply drops the draft; Advanced policy, still on screen, then
        # opens a fresh one from what the server now holds (ensureDraft).
        # What must not survive is the *applied* draft: no unsaved edits,
        # no validation or preview of the old text, the new revision, and
        # exactly the document the backend resolved.
        self.assertTrue(b.eval(
            "(() => { const d = S.draft, e = S.settings.page.editing.draft;"
            " return !d || (!d.dirty && d.checked === null && d.preview === null"
            " && d.raw === null && d.revision === e.revision"
            " && JSON.stringify(d.document) === JSON.stringify(e.document)); })()"),
            "the draft outlived its apply: " + str(b.eval("S.draft")))
        self.assertEqual(["unit"], b.eval(
            "S.settings.page.editing.draft.document.checks.map(c => c.name)"))

        # and the configured state is what the backend now resolves
        go_section("verification")
        if not b.wait("document.querySelector('#sbody').innerText"
                      ".includes('unit')", 10):
            raise bv.CheckFailed("the registered check is not drawn")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("python3 -m unittest discover -s tests", text)
        self.assertIn("Optional", text)
        self.assertIn("Your team provides the checks; Bench runs them.", text)
        S["metrics"]["settingsApplied"] = b.eval(
            "({kind: S.settings.page.overview.policy.kind,"
            " checks: S.settings.page.verification.checks.map(c => c.name),"
            " history: S.settings.page.history.length})")
        shot("settings-configured-verification")

        # and the change is on the record, identified without its values
        history = b.eval("S.settings.page.history")
        self.assertTrue(history, "the applied change left no history entry")
        self.assertIn("checks", history[0]["changedKeys"])
        self.assertNotIn("unittest", json.dumps(history))


def edit_in_place(kind: str, name: str):
    """Open a contextual editor through its own Edit button."""
    b = S["browser"]
    button = f'#sbody [data-sedit="{kind}"][data-sname="{name}"]'
    if not b.wait(f"!!document.querySelector('{button}')", 10):
        raise bv.CheckFailed(f"no Edit button for the {kind} {name!r}")
    b.click(button)
    if not b.wait("!!document.querySelector('#s-editor')", 5):
        raise bv.CheckFailed(f"Edit opened no editor for the {kind} {name!r}")


def set_field(path: str, value):
    S["browser"].eval(f"(() => {{ const e = document.querySelector('[data-field=\"{path}\"]');"
                      f" if (e.type === 'checkbox') e.checked = {json.dumps(bool(value))};"
                      f" else e.value = {json.dumps(str(value))};"
                      " e.dispatchEvent(new Event('change', {bubbles: true})); })()")


class InContextEditing(unittest.TestCase):
    """Card 76: a check is edited in Checks and a merge rule in Merge rules,
    on the advanced editor's draft and through its four steps. These run
    after the round trip above registered `unit`, against the backend's
    real document, and read every outcome back from the backend."""

    def test_a_check_edit_applies_and_survives_a_reload(self):
        b = open_settings(1440, 900, "verification")
        edit_in_place("check", "unit")
        set_field("checks:0:timeout", 321)
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked && S.draft.checked.ok)", 15):
            raise bv.CheckFailed("the contextual edit did not validate: " +
                                 str(b.eval("S.draft && S.draft.checked")))
        b.click('#s-preview')
        if not b.wait("!!(S.draft && S.draft.preview)", 15):
            raise bv.CheckFailed("preview never came back")
        self.assertIn("check-changed", b.eval("S.draft.preview.changes.map(c => c.kind)"))
        # the consequence block is drawn in the editor itself. Its heading
        # is styled upper-case, so it is read as written (textContent), not
        # as Chromium's innerText renders it
        self.assertEqual("If you apply", b.eval(
            "(document.querySelector('#s-editor .s-conseq .label') || {}).textContent"))
        self.assertIn("check-changed", b.eval(
            "[...document.querySelectorAll('#s-editor .s-diff .k')].map(e => e.textContent)"))
        shot("settings-editor-check-1440x900")
        b.click('#s-apply')
        if not b.wait("document.querySelector('#s-apply').classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm on the first click")
        b.click('#s-apply')
        if not b.wait("S.draft === null && !!S.settings && S.settings.page.verification"
                      ".checks.some(c => c.timeout === 321)", 20):
            raise bv.CheckFailed("the backend never resolved the applied edit")
        # a refresh is a new page: what it shows is the server's document
        b = open_settings(1440, 900, "verification")
        self.assertIn("321 seconds", b.eval("document.querySelector('#sbody').innerText"))

    def test_b_an_invalid_edit_is_refused_at_the_field_and_writes_nothing(self):
        b = open_settings(1440, 900, "verification")
        before = b.eval("S.settings.page.editing.draft.revision")
        edit_in_place("check", "unit")
        set_field("checks:0:run", "python3 -m unittest | tee out.txt")
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked)", 15):
            raise bv.CheckFailed("validate never came back")
        self.assertFalse(b.eval("S.draft.checked.ok"), "a pipeline validated")
        self.assertGreater(b.eval("document.querySelectorAll('#s-editor .ferr').length"), 0,
                           "the refusal is not drawn in the editor")
        self.assertTrue(b.eval("document.querySelector('#s-apply').disabled"))
        S["metrics"]["settingsContextRefusal"] = b.eval(
            "S.draft.checked.errors.map(e => e.code + '|' + (e.field || ''))")
        shot("settings-editor-check-refused")
        b.click('#s-discard')
        if not b.wait("S.draft === null && !!S.settings", 15):
            raise bv.CheckFailed("Discard did not drop the draft")
        self.assertEqual(before, b.eval("S.settings.page.editing.draft.revision"),
                         "a refused edit changed the document")

    def test_c_a_merge_rule_previews_its_consequence_and_cancel_writes_nothing(self):
        b = open_settings(390, 780, "acceptance")
        before = b.eval("S.settings.page.editing.draft.revision")
        edit_in_place("gate", "acceptance")
        set_field("gates:acceptance:checks:unit", True)
        b.click('#s-validate')
        if not b.wait("!!(S.draft && S.draft.checked && S.draft.checked.ok)", 15):
            raise bv.CheckFailed("requiring a declared check did not validate")
        b.click('#s-preview')
        if not b.wait("!!(S.draft && S.draft.preview)", 15):
            raise bv.CheckFailed("preview never came back")
        self.assertIn("gate-changed", b.eval("S.draft.preview.changes.map(c => c.kind)"))
        m = measure(["#s-editor", "#s-apply"])
        self.assertLessEqual(m["scrollWidth"], 391, "the editor widens the page at 390px")
        self.assertEqual([], bv.layout_problems({**m, "boxes": {"#s-apply": m["boxes"]["#s-apply"]}}))
        shot("settings-editor-merge-rule-390x780")
        # Close keeps the draft, and focus goes back to the Edit button
        b.click('#s-close')
        self.assertTrue(b.wait("!document.querySelector('#s-editor')", 5))
        self.assertTrue(b.wait("document.activeElement && document.activeElement.dataset"
                               " && document.activeElement.dataset.sedit === 'gate'", 5),
                        "closing the editor did not return focus to its Edit button")
        # and every other section says there is an unsaved draft
        go_section("overview")
        self.assertTrue(b.wait("document.querySelector('#sbody').innerText"
                               ".includes('Unsaved draft.')", 5))
        shot("settings-unsaved-draft-390x780")
        b.click('#sbody [data-sdraft="discard"]')
        if not b.wait("S.draft === null && !!S.settings", 15):
            raise bv.CheckFailed("discarding from the notice did not drop the draft")
        self.assertEqual(before, b.eval("S.settings.page.editing.draft.revision"),
                         "cancelling a merge-rule edit changed the document")


class KeyboardAndScreenReader(unittest.TestCase):
    def test_the_settings_switcher_is_reachable_by_keyboard_with_a_focus_ring(self):
        for w, h in [(1440, 900), (390, 780)]:
            b = open_settings(w, h)
            b.eval("document.activeElement && document.activeElement.blur()")
            order = []
            for _ in range(40):
                b.key("Tab")
                here = b.eval("(() => { const e = document.activeElement;"
                              " return e && (e.dataset && e.dataset.view"
                              " ? 'view:' + e.dataset.view : (e.id || e.tagName)); })()")
                order.append(here)
                if here == "view:settings":
                    break
            self.assertEqual("view:settings", order[-1],
                             f"Tab never reached the Settings tab at {w}x{h}: "
                             f"{order}")
            ring = b.eval("(() => { const s = getComputedStyle(document.activeElement);"
                          " return {outlineStyle: s.outlineStyle,"
                          " outlineWidth: s.outlineWidth, boxShadow: s.boxShadow}; })()")
            visible = (ring["outlineStyle"] not in ("none", "")
                       and ring["outlineWidth"] not in ("0px", "")) \
                or ring["boxShadow"] != "none"
            self.assertTrue(visible,
                            f"the focused Settings tab shows no focus "
                            f"indicator at {w}x{h}: {ring}")
            shot(f"settings-tab-focused-{w}x{h}")

    def test_an_editor_field_takes_focus_and_keeps_it_through_a_repaint(self):
        b = open_settings(1440, 900, "policy")
        draft_in({"v": 1, "checks": [{"name": "unit",
                                      "run": "python3 -m unittest",
                                      "root": "worktree"}]})
        field = 'input[data-field="checks:0:name"]'
        if not b.wait(f"!!document.querySelector('{field}')", 10):
            raise bv.CheckFailed("the check's name field was never drawn")
        b.eval(f"document.querySelector('{field}').focus()")
        self.assertEqual("f-checks:0:name",
                         b.eval("document.activeElement.id"))
        # the change handler repaints the section; focus must come back to
        # the field the person was in
        b.eval(f"(() => {{ const e = document.querySelector('{field}');"
               " e.value = 'renamed';"
               " e.dispatchEvent(new Event('change', {bubbles: true})); })()")
        self.assertTrue(b.wait("document.activeElement.id === 'f-checks:0:name'",
                               5),
                        "a repaint dropped focus out of the field being edited")
        self.assertEqual("renamed",
                         b.eval("S.draft.document.checks[0].name"))
        shot("settings-editor-focus")

    def test_the_escape_key_closes_a_dialog_opened_from_settings(self):
        """Settings itself is a view, not a dialog. The readiness
        inspector is the dialog reachable from here, and it must still
        take focus and close on Escape."""
        b = open_settings()
        b.eval("document.querySelector('#doctorchip').focus()")
        b.key("Enter")
        dialog = "#sheetwrap [role=dialog]"
        if not b.wait(f"!!document.querySelector('{dialog}')", 15):
            raise bv.CheckFailed("Enter on the readiness chip opened no dialog")
        b.wait("!document.querySelector('#sheetwrap').innerText.includes('…')", 15)
        m = measure([dialog])
        self.assertEqual([], bv.layout_problems(m))
        shot("settings-dialog-open")
        b.key("Escape")
        self.assertTrue(b.wait("!document.querySelector('#sheetwrap')"
                               ".classList.contains('open')", 5),
                        "Escape did not close the dialog")

    def test_every_control_in_every_section_has_an_accessible_name(self):
        found = {}
        for section in SECTIONS:
            b = open_settings(1280, 800, section)
            nodes = b.ax_nodes()
            controls, unnamed = 0, []
            for n in nodes:
                if n.get("ignored"):
                    continue
                role = (n.get("role") or {}).get("value")
                if role not in NAMED_ROLES:
                    continue
                controls += 1
                if not ((n.get("name") or {}).get("value") or "").strip():
                    unnamed.append({"role": role,
                                    "backendDOMNodeId": n.get("backendDOMNodeId")})
            found[section] = {"controls": controls, "unnamed": unnamed}
            self.assertGreater(controls, 5,
                               f"the {section} section has almost no controls "
                               f"in the accessibility tree")
            self.assertEqual([], unnamed,
                             f"controls with no accessible name in {section}")
        S["metrics"]["settingsAccessibility"] = found
        shot("settings-accessibility-1280x800")


class RepaintsKeepYourPlace(unittest.TestCase):
    """Every SSE frame, and the 30s safety refetch, repaints Settings.
    Explanations live in folds since card 76, so a repaint that rebuilt
    them closed snapped shut whatever a person had just opened — and took
    focus and the caret out of the JSON field mid-typing."""

    def test_an_opened_fold_survives_a_background_repaint(self):
        b = open_settings(1440, 900, "policy")
        b.click('#s-rawfold > summary')
        self.assertTrue(b.wait("document.querySelector('#s-rawfold').open", 5))
        b.eval("render()")
        self.assertTrue(b.eval("document.querySelector('#s-rawfold').open"),
                        "a background repaint closed the fold the person opened")
        # and a section of its own starts with its folds closed
        go_section("verification")
        go_section("policy")
        self.assertFalse(b.eval("document.querySelector('#s-rawfold').open"))

    def test_a_field_being_typed_in_is_not_repainted_from_under(self):
        b = open_settings(1440, 900, "policy")
        b.click('#s-rawfold > summary')
        self.assertTrue(b.wait("document.querySelector('#s-rawfold').open", 5))
        b.click('#s-raw')
        b.eval("(() => { const t = document.querySelector('#s-raw');"
               " t.value = '{\"v\": 1, \"half'; t.dispatchEvent(new Event('input')); })()")
        b.eval("window.__raw = document.querySelector('#s-raw'); render()")
        self.assertTrue(b.eval("document.activeElement === window.__raw"
                               " && document.activeElement.isConnected"),
                        "a background repaint took focus out of the field")
        self.assertEqual('{"v": 1, "half',
                         b.eval("document.querySelector('#s-raw').value"))
        b.eval("document.activeElement.blur()")


# Contrast, measured rather than eyeballed. Every colour is resolved through
# a 1x1 canvas, so color-mix() and oklab() values read as the pixels they
# paint, and a translucent background is composited over its ancestors'.
CONTRAST_JS = """(() => {
  const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = (c) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000';
    ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (top, under) => top.slice(0, 3).map((v, i) =>
    v * top[3] + under[i] * (1 - top[3])).concat(1);
  const bg = (el) => { const stack = [];
    for (let e = el; e; e = e.parentElement) stack.push(rgba(getComputedStyle(e).backgroundColor));
    return stack.reverse().reduce((acc, c) => over(c, acc), [255, 255, 255, 1]); };
  const lum = (c) => { const [r, g, b] = c.slice(0, 3).map(v => { v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const out = {};
  for (const sel of %s) {
    const el = [...document.querySelectorAll(sel)].find(e => e.offsetParent !== null);
    if (!el) { out[sel] = null; continue; }
    const back = bg(el), fore = over(rgba(getComputedStyle(el).color), back);
    const [a, b] = [lum(fore), lum(back)].sort((x, y) => y - x);
    out[sel] = Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100;
  }
  return out; })()"""

CONTRAST_TEXT = ["#shead h1", "#shead .s-sub", "#shead .s-ident", ".s-navhead",
                 '#snav button[aria-current="page"]',
                 '#snav button[aria-current="false"]', "#sbody .s-ptitle",
                 "#sbody .sline .sval", "#sbody .shelp", "#sbody .sb.link",
                 "#sbody .sb.primary", "#sbody .sb:not(.link):not(.primary)",
                 "#sbody .sbadge.warn", "#sbody .sbadge.dim", "#sbody .sw",
                 "#sbody p"]

# the three views the card's reference screenshots show. A contrast
# selector a section does not draw is recorded as absent (null), not as a
# pass
REFERENCE_VIEWS = ["overview", "verification", "diagnostics"]


class SurfaceThemesAndHitAreas(unittest.TestCase):
    """Card 80: the reference views in both themes at a desktop and a phone
    width, the current section told visually and to assistive technology,
    the narrow Sections button, usable hit areas, measured contrast, and no
    leak of the Settings tokens into the rest of the board. These run after
    the round trip registered `unit`, so Checks draws a configured check."""

    def test_the_reference_views_in_both_themes(self):
        found = {}
        for theme in ("light", "dark"):
            for w, h in [(1440, 900), (390, 780)]:
                for section in REFERENCE_VIEWS:
                    b = open_settings(w, h, section, theme)
                    m = measure(["#sbody"])
                    self.assertLessEqual(m["scrollWidth"], w + 1,
                                         f"{section} {theme} at {w}px overflows")
                    ratios = b.eval(CONTRAST_JS % json.dumps(CONTRAST_TEXT))
                    low = {k: v for k, v in ratios.items() if v is not None and v < 4.5}
                    found[f"{section}-{theme}-{w}x{h}"] = ratios
                    self.assertEqual({}, low, f"text under 4.5:1 in {section}, "
                                              f"{theme}, {w}x{h}")
        S["metrics"]["settingsContrast"] = found
        # one screenshot per view, theme and width, each after its own open
        for theme in ("light", "dark"):
            for w, h in [(1440, 900), (390, 780)]:
                open_settings(w, h, "overview", theme)
                shot(f"settings-ref-overview-{theme}-{w}x{h}")
                open_settings(w, h, "verification", theme)
                shot(f"settings-ref-checks-{theme}-{w}x{h}")
                open_settings(w, h, "diagnostics", theme)
                shot(f"settings-ref-diagnostics-{theme}-{w}x{h}")

    def test_a_contextual_editor_in_both_themes(self):
        for theme in ("light", "dark"):
            b = open_settings(1440, 900, "verification", theme)
            edit_in_place("check", "unit")
            b.eval("document.querySelector('#s-editor').scrollIntoView({block: 'start'})")
            ratios = b.eval(CONTRAST_JS % json.dumps(
                ["#s-editor label", "#s-editor input[type=text]", "#s-editor .label",
                 "#s-editor #s-validate", "#s-editor #s-discard"]))
            self.assertEqual({}, {k: v for k, v in ratios.items()
                                  if v is not None and v < 4.5}, theme)
            shot(f"settings-ref-editor-{theme}-1440x900")
            b.click('#s-discard')
            if not b.wait("S.draft === null && !!S.settings", 15):
                raise bv.CheckFailed("Discard did not drop the draft")

    def test_the_current_section_is_marked_three_ways(self):
        b = open_settings(1440, 900, "acceptance", "light")
        marks = b.eval("[...document.querySelectorAll('#snav [data-ssection]')].map(e => {"
                       " const s = getComputedStyle(e); return {key: e.dataset.ssection,"
                       " current: e.getAttribute('aria-current'), weight: s.fontWeight,"
                       " bar: s.borderLeftColor, barWidth: s.borderLeftWidth,"
                       " height: e.getBoundingClientRect().height}; })")
        current = [m for m in marks if m["current"] == "page"]
        others = [m for m in marks if m["current"] != "page"]
        self.assertEqual(["acceptance"], [m["key"] for m in current])
        self.assertTrue(all(m["current"] == "false" for m in others))
        # the weight and the bar, not colour alone
        self.assertGreaterEqual(int(current[0]["weight"]), 600)
        self.assertTrue(all(int(m["weight"]) < 600 for m in others))
        self.assertNotIn(current[0]["bar"], {m["bar"] for m in others})
        self.assertTrue(all(m["height"] >= 40 for m in marks), marks)
        self.assertEqual("Merge rules", b.eval("document.querySelector('#shead h1').textContent"))
        S["metrics"]["settingsSidebarMarks"] = marks
        # and the keyboard walks the sidebar in order, with a visible ring
        b.eval("document.querySelector('#snav [data-ssection=\"overview\"]').focus()")
        b.key("Tab")
        self.assertEqual("verification", b.eval("document.activeElement.dataset.ssection"))
        ring = b.eval("getComputedStyle(document.activeElement).outlineStyle")
        self.assertNotEqual("none", ring, "the focused sidebar item shows no ring")
        # a background repaint does not throw focus out of the sidebar
        b.eval("render()")
        self.assertEqual("verification", b.eval("document.activeElement.dataset"
                                                " && document.activeElement.dataset.ssection"))
        shot("settings-sidebar-focus-1440x900")

    def test_narrow_screens_open_the_sections_from_one_named_button(self):
        b = open_settings(390, 780, "overview", "light")
        toggle = measure(["#snavtoggle"])["boxes"]["#snavtoggle"]
        self.assertTrue(toggle and toggle[3] >= 44, f"the Sections button: {toggle}")
        self.assertEqual("false", b.eval("document.querySelector('#snavtoggle')"
                                         ".getAttribute('aria-expanded')"))
        self.assertTrue(b.eval("[...document.querySelectorAll('#snav [data-ssection]')]"
                               ".every(e => e.offsetParent === null)"),
                        "the section list is drawn before the button opens it")
        b.click('#snavtoggle')
        self.assertEqual("true", b.eval("document.querySelector('#snavtoggle')"
                                        ".getAttribute('aria-expanded')"))
        heights = b.eval("[...document.querySelectorAll('#snav [data-ssection]')]"
                         ".map(e => Math.round(e.getBoundingClientRect().height))")
        self.assertEqual(len(SECTIONS), len(heights))
        self.assertTrue(all(h >= 44 for h in heights), heights)
        self.assertLessEqual(measure(["#snav"])["scrollWidth"], 391)
        shot("settings-narrow-sections-open-390x780")
        b.click('#snav [data-ssection="diagnostics"]')
        self.assertTrue(b.wait("document.querySelector('#snavtoggle')"
                               ".getAttribute('aria-expanded') === 'false'", 5))
        self.assertEqual("snavtoggle", b.eval("document.activeElement.id"),
                         "choosing a section did not give focus back to the button")
        self.assertEqual("Diagnostics", b.eval("document.querySelector('#snavnow').textContent"))
        shot("settings-narrow-diagnostics-390x780")

    def test_every_control_has_a_usable_hit_area_at_390px(self):
        small = {}
        for section in SECTIONS:
            b = open_settings(390, 780, section)
            found = b.eval("[...document.querySelectorAll('#view-settings button,"
                           " #view-settings summary, #view-settings label.cb')]"
                           ".filter(e => e.offsetParent !== null).map(e => {"
                           " const r = e.getBoundingClientRect();"
                           " return [e.id || e.dataset.ssection || e.dataset.sedit ||"
                           " e.textContent.trim().slice(0, 30), Math.round(r.width),"
                           " Math.round(r.height)]; }).filter(([, w, h]) => w < 44 || h < 44)")
            if found:
                small[section] = found
        S["metrics"]["settingsSmallTargets"] = small
        self.assertEqual({}, small, "controls under 44px at 390px")

    def test_the_settings_tokens_do_not_leak_into_other_views(self):
        b = open_settings(1440, 900, "overview", "light")
        self.assertEqual("rgb(255, 255, 255)", b.eval(
            "getComputedStyle(document.querySelector('#sbody .panel')).backgroundColor"))
        b.click('#views button[data-view="board"]')
        self.assertTrue(b.wait("document.querySelector('#view-board').classList.contains('on')", 5))
        leaks = b.eval("['#view-board', '#view-phases', '#view-flight', '#view-focus', 'header']"
                       ".map(s => [s, getComputedStyle(document.querySelector(s))"
                       ".getPropertyValue('--s-panel').trim()]).filter(([, v]) => v)")
        self.assertEqual([], leaks, "a Settings token reached another view")
        # the board keeps the design system's own ground
        self.assertEqual(b.eval("getComputedStyle(document.documentElement)"
                                ".getPropertyValue('--bg').trim()"), "#dde9ec")
        shot("settings-board-after-settings-light-1440x900")


def agents_api(path: str, body: dict) -> dict:
    """Post to Settings → Agents from the page itself, as another tab of
    the same board would — the server's own door, the server's answer."""
    return S["browser"].eval(
        "(async () => { const r = await fetch('/api/settings/agents/" + path + "',"
        " {method: 'POST', headers: SETTINGS_HEADERS,"
        f" body: JSON.stringify({json.dumps(body)})}});"
        " return {status: r.status, data: await r.json()}; })()")


def agents_step(step: str, until: str, why: str):
    b = S["browser"]
    b.click(f'#sbody [data-agstep="{step}"]')
    if not b.wait(until, 20):
        raise bv.CheckFailed(why + ": " + str(b.eval(
            "S.agentsDraft && {checked: S.agentsDraft.checked,"
            " stale: S.agentsDraft.stale}")))


def set_control(selector: str, value: str, event: str = "change"):
    S["browser"].eval(f"(() => {{ const e = document.querySelector('{selector}');"
                      f" e.value = {json.dumps(value)};"
                      f" e.dispatchEvent(new Event('{event}', {{bubbles: true}})); }})()")


class AgentsSection(unittest.TestCase):
    """Settings → Agents (task 77), against the backend's real agent
    settings: compact rows, inheritance, a per-purpose edit applied and
    read back, a prompt previewed, saved and reset, a refused template, a
    conflict, and the keyboard and narrow layouts. Cases run in name
    order and leave the board's agent settings as they found them."""

    def test_a_compact_rows_say_what_each_purpose_inherits(self):
        b = open_settings(1440, 900, "agents")
        if not b.wait("document.querySelectorAll('[data-agrow]').length === 4", 10):
            raise bv.CheckFailed("the four agent rows were never drawn")
        rows = b.eval("[...document.querySelectorAll('[data-agrow]')]"
                      ".map(e => e.dataset.agrow)")
        self.assertEqual(["default", "work", "act-pr", "review"], rows)
        option = b.eval("document.querySelector('#ag-h-review option').textContent")
        self.assertTrue(option.startswith("Inherit default ("), option)
        model = b.eval("document.querySelector('#ag-mi-work option').textContent")
        self.assertTrue(model.startswith("Inherit default"), model)
        text = b.eval("document.querySelector('#sbody').innerText")
        for words in ("Implementation", "PR fixes", "Reviews", "Card refinement",
                      "Task relevance review", "PR review", "Built-in",
                      "In force now"):
            self.assertIn(words, text)
        # the overview names it too
        go_section("overview")
        self.assertIn("Agents", b.eval("document.querySelector('#sbody').innerText"))
        S["metrics"]["agentsRows"] = rows
        go_section("agents")
        shot("settings-agents-rows-1440x900")

    def test_b_a_per_purpose_override_applies_and_the_backend_resolves_it(self):
        b = open_settings(1440, 900, "agents")
        b.wait("!!document.querySelector('#ag-h-review')", 10)
        set_control('#ag-h-review', 'opencode')
        set_control('#ag-mi-review', 'set')
        if not b.wait("!document.querySelector('#ag-m-review').disabled", 5):
            raise bv.CheckFailed("choosing 'Use a model' did not enable the field")
        set_control('#ag-m-review', 'provider/review-model', 'input')
        agents_step("validate", "!!(S.agentsDraft.checked && S.agentsDraft.checked.ok)",
                    "a valid agent draft did not validate")
        agents_step("preview", "!!S.agentsDraft.preview", "preview never came back")
        review = b.eval("S.agentsDraft.preview.purposes.find(p => p.purpose === 'review')")
        self.assertTrue(review["changed"])
        self.assertEqual("opencode", review["after"]["harness"]["name"])
        self.assertEqual("provider/review-model", review["after"]["model"]["value"])
        self.assertTrue(review["inheritanceChanged"])
        work = b.eval("S.agentsDraft.preview.purposes.find(p => p.purpose === 'work')")
        self.assertFalse(work["changed"], "a review override changed implementation")
        preview_text = b.eval("document.querySelector('[data-agpreview]').innerText")
        self.assertIn("next launch", preview_text)
        shot("settings-agents-preview")
        b.click('#sbody [data-agstep="apply"]')
        if not b.wait("document.querySelector('#sbody [data-agstep=\"apply\"]')"
                      ".classList.contains('armed')", 5):
            raise bv.CheckFailed("Apply did not arm on the first click")
        b.click('#sbody [data-agstep="apply"]')
        if not b.wait("!!(S.settings && S.settings.page.agents.rows.review"
                      ".effective.selection.harness.name === 'opencode')", 20):
            raise bv.CheckFailed("the applied change never reached the backend")
        row = b.eval("S.settings.page.agents.rows.review")
        self.assertEqual("BOARD_AGENT_ADAPTER_REVIEW",
                         row["effective"]["selection"]["harness"]["setting"])
        self.assertEqual("provider/review-model",
                         row["effective"]["selection"]["model"]["value"])
        # implementation still inherits the default
        self.assertTrue(b.eval("S.settings.page.agents.rows.work.effective"
                               ".selection.harness.inherited"))
        history = b.eval("S.settings.page.agents.history")
        self.assertIn("BOARD_AGENT_ADAPTER_REVIEW", history[0]["changedKeys"])
        self.assertNotIn("review-model", json.dumps(history))
        S["metrics"]["agentsApplied"] = row["effective"]["selection"]
        shot("settings-agents-applied")
        # back as it was, through the same door
        back = agents_api("apply", {"revision": b.eval("S.settings.page.agents.revision"),
                                    "agents": {"BOARD_AGENT_ADAPTER_REVIEW": "",
                                               "BOARD_AGENT_MODEL_REVIEW": ""}})
        self.assertEqual(200, back["status"], back)

    def probe_c_a_prompt_is_previewed_saved_reset_and_recoverable(self):
        b = open_settings(1440, 900, "agents")
        b.click('#sbody [data-agtpl="review.md"]')
        if not b.wait("document.activeElement && document.activeElement.id === 'ag-t-review.md'", 5):
            raise bv.CheckFailed("Edit did not put focus in the template")
        b.eval("(() => { const t = document.querySelector('#ag-t-review\\\\.md');"
               " t.value = t.value + '\\nA LOCAL NOTE FOR REVIEWERS\\n';"
               " t.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.click('#sbody [data-agrender="review.md"]')
        if not b.wait("!!document.querySelector('[data-agrendered=\"review.md\"]')", 10):
            raise bv.CheckFailed("the rendered preview never arrived")
        rendered = b.eval("document.querySelector('[data-agrendered=\"review.md\"]').innerText")
        self.assertIn("A LOCAL NOTE FOR REVIEWERS", rendered)
        self.assertIn("00-sample-card.md", rendered)
        self.assertNotIn("{body}", rendered)
        b.click('#sbody [data-agdiff="review.md"]')
        if not b.wait("!!document.querySelector('[data-agdiffout=\"review.md\"]')", 5):
            raise bv.CheckFailed("the diff from the built-in was not drawn")
        self.assertIn("+ A LOCAL NOTE FOR REVIEWERS",
                      b.eval("document.querySelector('[data-agdiffout=\"review.md\"]').innerText"))
        shot("settings-agents-prompt-preview")
        b.click('#sbody [data-agstep="apply"]')
        b.click('#sbody [data-agstep="apply"]')
        if not b.wait("!!(S.settings && S.settings.page.agents.templates"
                      ".find(t => t.name === 'review.md').source === 'local')", 20):
            raise bv.CheckFailed("the local override was never saved")
        # reset: staged, previewed, applied, and the replaced text recoverable
        if not b.eval("!!document.querySelector('[data-agreset=\"review.md\"]')"):
            b.click('#sbody [data-agtpl="review.md"]')
        b.wait("!!document.querySelector('[data-agreset=\"review.md\"]')", 5)
        b.click('#sbody [data-agreset="review.md"]')
        agents_step("preview", "!!S.agentsDraft.preview", "the reset preview never came back")
        change = b.eval("S.agentsDraft.preview.templates[0]")
        self.assertEqual("reset", change["action"])
        self.assertTrue(change["recoverable"])
        self.assertIn("-A LOCAL NOTE FOR REVIEWERS", change["diff"])
        b.click('#sbody [data-agstep="apply"]')
        b.click('#sbody [data-agstep="apply"]')
        if not b.wait("!!(S.settings && S.settings.page.agents.templates"
                      ".find(t => t.name === 'review.md').source === 'built-in')", 20):
            raise bv.CheckFailed("the reset never reached the backend")
        recovered = b.eval("S.settings.page.agents.recovery"
                           ".filter(r => r.template === 'review.md').length")
        self.assertGreater(recovered, 0, "the replaced text was not kept for recovery")
        shot("settings-agents-prompt-reset")

    def probe_d_a_broken_template_is_refused_and_nothing_is_written(self):
        b = open_settings(1440, 900, "agents")
        before = b.eval("S.settings.page.agents.revision")
        b.click('#sbody [data-agtpl="work.md"]')
        b.wait("!!document.querySelector('#ag-t-work\\\\.md')", 5)
        b.eval("(() => { const t = document.querySelector('#ag-t-work\\\\.md');"
               " t.value = t.value.replace('NOT READY:', 'NOT-READY').replace('{plan}', '{nope}');"
               " t.dispatchEvent(new Event('input', {bubbles: true})); })()")
        agents_step("validate", "!!(S.agentsDraft.checked && !S.agentsDraft.checked.ok)",
                    "a broken template validated")
        codes = b.eval("S.agentsDraft.checked.errors.map(e => e.code)")
        self.assertIn("template-missing-marker", codes)
        self.assertIn("template-unknown-field", codes)
        self.assertTrue(b.eval("!!document.querySelector('[data-agerr=\"template-missing-marker\"]')"))
        b.click('#sbody [data-agstep="apply"]')
        b.click('#sbody [data-agstep="apply"]')
        b.wait("!S.agentsDraft.armed", 10)
        self.assertEqual(before, b.eval("S.settings.page.agents.revision"))
        shot("settings-agents-refused")
        b.click('#sbody [data-agstep="discard"]')
        self.assertTrue(b.wait("S.agentsDraft === null || !S.agentsDraft.dirty", 10))

    def probe_e_a_stale_draft_is_a_conflict_not_an_overwrite(self):
        b = open_settings(1440, 900, "agents")
        set_control('#ag-m-default', 'mine', 'input')
        # another tab saves first
        other = agents_api("apply", {"revision": b.eval("S.settings.page.agents.revision"),
                                     "agents": {"BOARD_AGENT_MODEL": "theirs"}})
        self.assertEqual(200, other["status"], other)
        b.click('#sbody [data-agstep="apply"]')
        b.click('#sbody [data-agstep="apply"]')
        if not b.wait("!!(S.agentsDraft && S.agentsDraft.stale)", 15):
            raise bv.CheckFailed("an apply over a stale revision was not refused")
        self.assertIn("changed on disk",
                      b.eval("document.querySelector('#sbody').innerText"))
        shot("settings-agents-conflict")
        b.click('#sbody [data-agstep="discard"]')
        b.wait("!!(S.settings && S.settings.page.agents.rows.default.model.value === 'theirs')", 15)
        back = agents_api("apply", {"revision": b.eval("S.settings.page.agents.revision"),
                                    "agents": {"BOARD_AGENT_MODEL": ""}})
        self.assertEqual(200, back["status"], back)

    def probe_f_the_rows_are_reachable_by_keyboard_and_fit_a_narrow_screen(self):
        for w, h in [(1440, 900), (390, 780)]:
            b = open_settings(w, h, "agents")
            b.eval("document.querySelector('#s-title').focus()")
            reached = []
            for _ in range(60):
                b.key("Tab")
                here = b.eval("document.activeElement && document.activeElement.id")
                reached.append(here)
                if here == "ag-m-default":
                    break
            self.assertIn("ag-h-default", reached, f"Tab never reached the harness at {w}")
            self.assertEqual("ag-m-default", reached[-1], f"Tab never reached the model at {w}")
            m = measure(["#sbody", "[data-agrow=review]"])
            self.assertLessEqual(m["scrollWidth"], w + 1, f"horizontal overflow at {w}")
            row = m["boxes"]["[data-agrow=review]"]
            self.assertTrue(row and row[0] >= -1 and row[0] + row[2] <= w + 1, row)
            shot(f"settings-agents-{w}x{h}")


class ReadingRunsNothing(unittest.TestCase):
    def test_check_configuration_inspects_without_running_a_project_program(self):
        b = open_settings(1440, 900, "diagnostics")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("not asked", text)
        self.assertIn("No project program runs", text)
        b.click('#sbody [data-sinspect]')
        if not b.wait("!!(S.doctor && S.doctor.report)", 60):
            raise bv.CheckFailed("Check configuration never came back")
        report = b.eval("({ran: S.doctor.report.ran,"
                        " findings: S.doctor.report.findings.length,"
                        " outcomes: S.doctor.report.findings"
                        ".map(f => f.outcome)})")
        self.assertEqual(["inspect"], report["ran"],
                         "Check configuration ran more than inspection")
        self.assertGreater(report["findings"], 0)
        # the deferred row is there and is not drawn as a pass
        self.assertIn("deferred", report["outcomes"])
        after = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("deferred", after)
        S["metrics"]["settingsInspection"] = report
        shot("settings-inspected")

    def test_the_real_agent_stage_offers_no_control(self):
        b = open_settings(1440, 900, "diagnostics")
        text = b.eval("document.querySelector('#sbody').innerText")
        self.assertIn("no control here", text)
        self.assertIn("--approve-real-agent", text)
        buttons = b.eval("[...document.querySelectorAll('#sbody [data-sdoctor]')]"
                         ".map(e => e.dataset.sdoctor)")
        self.assertNotIn("real-agent", buttons,
                         "the page offers a control for the stage that spends "
                         "tokens")
        S["metrics"]["settingsDoctorButtons"] = buttons

    def test_cancel_discards_the_draft_and_changes_nothing(self):
        b = open_settings(1440, 900, "policy")
        before = b.eval("S.settings.page.editing.draft.revision")
        draft_in({"v": 1, "checks": []})
        b.click('#s-discard')
        if not b.wait("S.draft === null", 15):
            raise bv.CheckFailed("Discard did not drop the draft")
        self.assertTrue(b.wait("!!(S.settings && S.settings.page)", 15))
        self.assertEqual(before,
                         b.eval("S.settings.page.editing.draft.revision"),
                         "discarding a draft changed the document")
        shot("settings-discarded")
