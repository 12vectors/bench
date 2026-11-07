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

SECTIONS = ["overview", "verification", "acceptance", "access",
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


def open_settings(width=1440, height=900, section="overview"):
    """The view, through the header switcher a person actually uses."""
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    b.click('#views button[data-view="settings"]')
    if not b.wait("document.querySelector('#view-settings')"
                  ".classList.contains('on')", 5):
        raise bv.CheckFailed("the Settings switcher opened no view")
    if not b.wait("document.querySelector('#snav').children.length >= 7", 15):
        raise bv.CheckFailed("the settings sections never arrived from the "
                             "backend")
    if section != "overview":
        b.click(f'#snav [data-ssection="{section}"]')
        if not b.wait(f"document.querySelector('#snav [data-ssection=\"{section}\"]')"
                      ".getAttribute('aria-current') === 'true'", 5):
            raise bv.CheckFailed(f"the {section} section did not become current")
    b.eval("document.activeElement && document.activeElement.blur()")
    return b


def draft_in(doc: dict) -> None:
    """Replace the editor's draft through its own JSON escape hatch — the
    one control that can express a whole document, and the one a test can
    drive without typing into fourteen fields."""
    b = S["browser"]
    b.eval("document.querySelector('#s-rawfold').open = true")
    if not b.wait("!!document.querySelector('#s-raw')", 5):
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
                m = measure(["#view-settings", "#shead", "#snav", "#sbody",
                             "header", "#views"])
                problems = bv.layout_problems(
                    {**m, "boxes": {k: v for k, v in m["boxes"].items()
                                    if k in ("#shead", "#snav", "header",
                                             "#views")}})
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
                      "Not inspected", "Unavailable", "3 profiles"):
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
        self.assertIsNone(b.eval("S.draft"), "the draft outlived its apply")

        # and the configured state is what the backend now resolves
        b.click('#snav [data-ssection="verification"]')
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
        self.assertIn("If you apply",
                      b.eval("document.querySelector('#s-editor').innerText"))
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
        self.assertIn("321s limit", b.eval("document.querySelector('#sbody').innerText"))

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
        b.click('#snav [data-ssection="overview"]')
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
