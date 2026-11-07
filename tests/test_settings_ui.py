"""The Settings view itself (task 74).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants — the ones that, if broken, would put
the page back where this card found it: a settings page that answers from
somewhere other than the backend, or one that renders "nothing configured"
in the colour of a pass.

Four are load-bearing:

- **One resolver.** Every section is drawn from `/api/settings`. Nothing
  in the page compiles a grant, reads a `.env`, decides an outcome or
  parses a policy document.
- **The word carries the meaning.** Every state is drawn as a word from
  the server's own vocabulary, with its definition on the title. Colour
  never stands alone.
- **Four steps, and only the last writes.** Validate, preview and apply
  are three requests; apply is armed and stays unavailable until a
  validation of the current draft came back clean.
- **Reading is reading.** Opening the view, and Check configuration, are
  GETs. The three executing stages are armed actions that go through the
  board's existing `/api/doctor/run`; the fourth has no control at all.

The measurable half — layout at 390px, focus, the accessibility tree —
is in `verify/browser_cases/case_settings.py`, which runs a real browser
against a real backend.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "manager" / "core" / "board.html"
sys.path.insert(0, str(REPO / "manager" / "core"))

import profiles  # noqa: E402
import settings  # noqa: E402


def without_comments(text: str) -> str:
    """The source with its block comments removed.

    Naming an authority in a comment — "`profiles.gate_policy()` for what
    a merge must have" — is documentation, and the thing being forbidden
    is *calling* it. Line comments are left alone: `//` is also what a URL
    has in it."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        cls.code = without_comments(cls.html)

    def body(self, name: str) -> str:
        start = self.html.index(f"function {name}(")
        return self.html[start:self.html.index("\n}\n", start)]

    def css(self) -> str:
        return self.html[self.html.index("/* ── settings ──"):
                         self.html.index("/* ── phases: a swimlane each ── */")]

    def declarations(self) -> list[str]:
        """Every CSS declaration in the settings block, so a failure names
        the one line at fault rather than printing the whole stylesheet."""
        return [d.strip() for rule in re.findall(r"\{([^}]*)\}", self.css())
                for d in rule.split(";") if d.strip()]


class ReachableThroughNormalNavigation(Source):
    def test_the_header_switcher_carries_settings(self):
        self.assertIn('<button data-view="settings">Settings</button>',
                      self.html)
        self.assertIn('<div class="view" id="view-settings">', self.html)

    def test_the_tab_title_names_the_view(self):
        self.assertRegex(self.html, r"settings: 'settings'")

    def test_the_view_is_rendered_by_the_same_dispatch_as_every_other(self):
        body = self.body("render")
        self.assertIn("S.view === 'settings'", body)
        self.assertIn("renderSettings()", body)

    def test_every_section_is_reachable_from_the_nav(self):
        body = self.body("renderSettings")
        self.assertIn("SETTINGS_SECTIONS.map", body)
        self.assertIn("data-ssection", body)
        for key in ("overview", "verification", "acceptance", "access",
                    "diagnostics", "installation", "policy"):
            self.assertIn(f"'{key}'", self.html, key)

    def test_the_switcher_says_when_the_policy_refuses_something(self):
        body = self.body("renderSettingsTab")
        self.assertIn("S.state?.settings", body)
        self.assertIn("vhalt", body)
        # and a board that configured nothing reads as it always did
        self.assertIn("How this board is configured", body)

    def test_a_dirty_draft_holds_the_stream_repaint(self):
        """render() runs on every SSE frame. Repainting the editor from
        under a half-typed field would lose the edit."""
        self.assertIn("function settingsEditing()", self.html)
        self.assertIn("if (!settingsEditing()) renderSettings();", self.html)


class OneResolver(Source):
    def test_the_page_reads_the_servers_whole_settings_payload(self):
        self.assertIn("fetch('/api/settings')", self.html)
        self.assertIn("S.settings = { at: Date.now(), page: data.settings }",
                      self.html)

    def test_the_page_never_parses_a_policy_document_of_its_own(self):
        """The one thing a second parser would do. Grants, roots, readable
        statuses, gate decisions and settings provenance all arrive
        resolved."""
        for forbidden in ("MODE_PREFIXES", "split_commands", "DENY_CAPABILITY",
                          "agent_commands", "gate_policy", "document_checks",
                          "document_steps", "capability_table"):
            self.assertNotIn(forbidden, self.code,
                             f"the page resolves {forbidden} itself")

    def test_the_page_never_reads_an_env_file(self):
        for forbidden in ("local/.env'", '.env"', "env_file_values"):
            self.assertNotIn(forbidden, self.code,
                             "the page reads a settings file itself")

    def test_the_sections_are_drawn_from_the_served_fields(self):
        for name, fields in (
                ("settingsOverview", ["o.rows", "page.recommendations"]),
                ("settingsVerification", ["v.checks", "v.prepare", "v.roots"]),
                ("settingsAcceptance", ["a.policy.scopes", "a.reviewModes"]),
                ("settingsAccess", ["a.intents", "a.capabilities", "a.frozen"]),
                ("settingsDiagnostics", ["d.coverage", "d.modes"]),
                ("settingsInstallation", ["i.roots", "page.settings"])):
            body = self.body(name)
            for field in fields:
                self.assertIn(field, body, f"{name} does not draw {field}")

    def test_the_editor_sends_the_whole_document_and_the_revision(self):
        self.assertIn("{ document: S.draft.document }", self.html)
        self.assertIn("revision: S.draft.revision", self.html)

    def test_the_draft_is_the_servers_document_including_unknown_keys(self):
        self.assertIn("JSON.parse(JSON.stringify(d.document))",
                      self.body("ensureDraft"))
        body = self.body("settingsPolicy")
        self.assertIn("ensureDraft(page)", body)
        self.assertIn("d.unknownKeys", body)
        self.assertIn("preserved exactly", body)

    def test_every_editor_rides_the_one_draft_and_the_one_step_bar(self):
        """Card 76: a check is edited in Checks and a merge rule in Merge
        rules, and both are the advanced editor's draft and its four steps
        — not a second mechanism that could validate or write differently."""
        for name in ("settingsPolicy", "checkEditor", "gateEditor",
                     "overridesEditor"):
            self.assertIn("draftBar(page, ", self.body(name), name)
        for name in ("settingsVerification", "settingsAcceptance"):
            self.assertIn("ensureDraft(page)", self.body(name), name)
        self.assertEqual(1, self.html.count('id="s-steps"'),
                         "one step bar, drawn by draftBar")
        # the draft survives navigating away, and says so wherever you are
        self.assertIn("Unsaved draft.", self.body("renderSettings"))
        self.assertIn("draftBarShown(section)", self.body("renderSettings"))

    def test_a_contextual_editor_shows_every_error_somewhere(self):
        """Errors about the item being edited are drawn at its fields; the
        bar draws every other one, so none is hidden off-screen."""
        bar = self.body("draftBar")
        self.assertIn("errs.filter(e => !here(e))", bar)
        self.assertIn("errorLines(draftErrors(mine))", self.body("checkEditor"))
        self.assertIn("errorLines(draftErrors(mine))", self.body("gateEditor"))

    def test_a_scoped_requirement_is_never_flattened(self):
        body = self.body("writeField")
        self.assertIn("list.filter(c => nameOf(c) !== parts[3])", body)
        self.assertNotIn("gate.checks = [...set]", body)

    def test_a_successful_apply_reads_the_document_back(self):
        body = self.body("settingsApply")
        self.assertRegex(body, r"S\.draft = null;\s*S\.sedit = null;\s*"
                               r"await fetchSettings\(true\)")


class TheWordCarriesTheMeaning(Source):
    def test_every_state_is_drawn_as_a_word_with_its_definition(self):
        body = self.body("stateWord")
        self.assertIn("esc(word)", body)
        self.assertIn('title="${esc(why)}"', body)

    def test_the_vocabulary_is_the_servers(self):
        """A word the server does not define would be a ninth state the
        page invented."""
        words = set(re.findall(r"\.sw\.([a-z-]+)\{", self.html))
        self.assertTrue(words, "no state words are styled at all")
        self.assertEqual(set(), words - set(settings.STATES),
                         "the page styles a state the server does not name")

    def test_ungated_is_never_drawn_as_a_pass(self):
        body = self.body("settingsAcceptance")
        self.assertIn("ungatedIsNotAPass", body)
        # the chip for an ungated scope wears the dim register, not --calm
        self.assertRegex(body, r"entry\.gated \? 'ok' : 'dim'")

    def test_a_registered_check_says_it_is_not_required(self):
        """Card 76 words it `Optional`, with the definition on the chip's
        title — and a required one says which rule requires it."""
        body = self.body("settingsVerification")
        self.assertIn("Optional</span>", body)
        self.assertIn('title="${esc(v.registrationIsNotRequirement)}"', body)
        self.assertIn("required: ", body)

    def test_the_overview_names_no_requirement_as_no_requirement(self):
        """No gate is not a passed gate: the overview says so in words, and
        a check nobody requires is Optional rather than anything green."""
        body = self.body("settingsOverview")
        self.assertIn("'No merge requirements'", body)
        self.assertIn("'Not inspected'", body)
        self.assertIn("'Unavailable'", body)
        self.assertIn("'Optional'", body)
        self.assertIn("'Invalid'", body)
        # every row has a named route to its details or its editor
        self.assertIn("act: 'Edit checks'", body)
        self.assertIn("act: 'Edit merge rules'", body)
        self.assertIn("act: 'Check configuration'", body)

    def test_an_unsupported_capability_is_not_a_pretend_switch(self):
        body = self.body("settingsAccess")
        self.assertIn("a.adapter.unsupported", body)
        self.assertIn("refused rather than reported", body)
        self.assertNotIn('type="checkbox"', body,
                         "the access section offers a control for something "
                         "it only reports")

    def test_the_page_never_calls_a_prefix_rule_a_sandbox(self):
        shown = self.html.lower()
        for claim in ("sandboxed", "sandbox:", "confined to",
                      "cannot read anything else"):
            self.assertNotIn(claim, shown,
                             f"the page claims {claim!r}, which no shipped "
                             f"adapter delivers")

    def test_a_runs_frozen_policy_is_drawn_as_frozen(self):
        body = self.body("settingsAccess")
        self.assertIn("frozen, not current", body)
        self.assertIn("this run keeps what it rode", self.html)

    def test_intended_writes_and_os_isolation_are_two_rows(self):
        """The headline must not read as a boundary that a folded-away
        caveat then retracts."""
        body = self.body("settingsAccess")
        self.assertIn("intended writes:", body)
        self.assertIn("OS isolation:", body)
        self.assertIn("none enforced", body)
        self.assertIn("found.isolation", body)


class FourStepsAndOnlyTheLastWrites(Source):
    def test_validate_and_preview_are_their_own_requests(self):
        self.assertIn("/api/settings/${step}", self.html)
        body = self.body("settingsPost")
        self.assertIn("nothing was written", body)

    def test_apply_is_armed_and_goes_through_its_own_route(self):
        self.assertIn("fetch('/api/settings/apply'", self.html)
        body = self.body("wirePolicyEditor")
        self.assertIn("wireAction(apply", body)
        self.assertRegex(body, r"confirm: 'write it\?'")

    def test_apply_is_unavailable_until_a_validation_came_back_clean(self):
        body = self.body("draftBar")
        self.assertRegex(body, r"id=\"s-apply\"[\s\S]{0,180}disabled")
        self.assertIn("Apply stays unavailable until a validation", body)

    def test_an_edit_puts_apply_back_behind_a_fresh_validation(self):
        body = self.body("writeField")
        self.assertIn("S.draft.checked = null", body)
        self.assertIn("S.draft.preview = null", body)

    def test_discard_writes_nothing_and_re_reads(self):
        body = self.body("wirePolicyEditor")
        self.assertRegex(body, r"#s-discard[\s\S]{0,260}S\.draft = null")
        self.assertIn("draft discarded — nothing was written", body)

    def test_the_policy_requests_carry_the_header_the_server_insists_on(self):
        self.assertIn("'X-Bench-Settings': '1'", self.html)
        self.assertIn("SETTINGS_HEADERS", self.body("settingsPost"))
        self.assertIn("SETTINGS_HEADERS", self.body("settingsApply"))

    def test_a_conflict_re_reads_rather_than_retrying(self):
        body = self.body("settingsApply")
        self.assertIn("data.conflict", body)
        self.assertIn("S.draft = null; fetchSettings(true)", body)

    def test_the_preview_draws_the_consequences_the_server_computed(self):
        body = self.body("draftConsequences")
        for field in ("draft.preview.changes", "draft.preview.evidence",
                      "draft.preview.pending", "draft.preview.sideEffects"):
            self.assertIn(field, body, field)


class ReadingIsReading(Source):
    def test_check_configuration_is_a_get_of_the_doctors_own_report(self):
        body = self.body("inspectConfiguration")
        self.assertIn("fetch('/api/doctor')", body)
        self.assertNotIn("method: 'POST'", body)

    def test_the_action_is_named_check_configuration(self):
        """The card's own decision, which supersedes the mockup's label."""
        self.assertIn("Check configuration", self.html)
        self.assertNotIn("Inspect readiness", self.html)

    def test_the_executing_stages_are_armed_and_name_their_side_effects(self):
        body = self.body("settingsDiagnostics")
        self.assertIn("mode.sideEffects", body)
        self.assertIn("data-sdoctor", body)
        wired = self.body("wireDiagnostics")
        self.assertIn("wireAction(btn", wired)
        self.assertRegex(wired, r"confirm: 'run it\?'")

    def test_the_run_goes_through_the_boards_existing_route(self):
        body = self.body("settingsDoctorRun")
        self.assertIn("fetch('/api/doctor/run'", body)
        self.assertIn("modes: [mode]", body)

    def test_a_stage_with_no_supported_control_is_not_a_fake_button(self):
        body = self.body("settingsDiagnostics")
        self.assertIn("no control here", body)
        self.assertIn("not offered here", body)
        self.assertIn("path.available", body)

    def test_the_coverage_table_says_what_was_not_asked(self):
        body = self.body("settingsDiagnostics")
        self.assertIn("not asked", body)
        self.assertIn("row.doesNotProve", body)
        self.assertIn("unknownIsNotPassed", body)


class NoContentsAndNoSecrets(Source):
    def test_the_settings_surface_never_fetches_a_files_contents(self):
        for name in ("settingsVerification", "settingsAccess",
                     "settingsPolicy", "settingsInstallation"):
            self.assertNotIn("/files/", self.body(name),
                             f"{name} fetches a file's contents")

    def test_the_page_says_secrets_are_not_managed_here(self):
        self.assertIn("page.editing.secrets", self.body("settingsPolicy"))

    def test_a_redacted_value_says_so_in_words(self):
        body = self.body("settingsInstallation")
        self.assertIn("(redacted)", body)
        self.assertIn("s.locked", body)


class LayoutIsOneColumn(Source):
    def test_the_view_scrolls_itself_and_keeps_the_place(self):
        self.assertIn("#view-settings{flex-direction:column;overflow-y:auto}",
                      self.html)
        self.assertIn("markScroll('v:settings'", self.html)
        self.assertIn("restoreScroll('v:settings'", self.html)

    def test_nothing_in_the_section_body_is_fixed_width(self):
        """390px and 200% zoom are the two sizes this has to survive, and a
        fixed width or a nowrap row is what breaks them."""
        decls = self.declarations()
        self.assertIn("max-width:980px", decls)
        fixed = [d for d in decls
                 if re.fullmatch(r"(min-)?width:\d+(\.\d+)?(px|em|rem|ch)", d)
                 and d not in ("width:6px", "height:6px")]
        self.assertEqual([], fixed,
                         f"a fixed width in the settings block: {fixed}")
        self.assertIn("flex-wrap:wrap", decls)
        # the one nowrap is the state word, which is three syllables
        self.assertEqual(1, decls.count("white-space:nowrap"),
                         f"nowrap rows: "
                         f"{[d for d in decls if 'nowrap' in d]}")

    def test_long_values_wrap_rather_than_widen_the_page(self):
        decls = self.declarations()
        self.assertIn("overflow-wrap:anywhere", decls)
        self.assertIn("min-width:0", decls)

    def test_every_control_carries_an_accessible_name(self):
        """Measured properly in the browser case; here, that no control in
        these renderers was written without one."""
        for name in ("renderSettings", "settingsOverview", "settingsPolicy",
                     "settingsDiagnostics", "field", "select", "checkbox",
                     "sline", "draftBar", "checkEditor", "gateEditor",
                     "overridesEditor", "reviewSelect", "settingsVerification"):
            body = self.body(name)
            buttons = len(re.findall(r"<(?:button|select|input|textarea)\b",
                                     body))
            labelled = len(re.findall(r"aria-label=", body))
            labels = len(re.findall(r"<label\b", body))
            self.assertGreaterEqual(labelled + labels, buttons,
                                    f"{name} draws a control with no name")


class TheBrowserCaseIsWellFormed(unittest.TestCase):
    """The measurable half lives in a real browser, and this is the half of
    *that* a stdlib suite can check: the file compiles, it declares the
    sizes the card names, and no two screenshots share a path — a second
    shot to the same name overwrites the first one's evidence."""

    CASE = REPO / "verify" / "browser_cases" / "case_settings.py"

    def test_the_case_compiles(self):
        compile(self.CASE.read_text(encoding="utf-8"), str(self.CASE), "exec")

    def test_it_measures_the_narrow_layout_and_the_zoomed_viewport(self):
        src = self.CASE.read_text(encoding="utf-8")
        widths = eval(re.search(r"WIDTHS = (\[.*?\])", src, re.S).group(1))
        self.assertIn((390, 780), widths, "390px is not measured")
        self.assertIn((640, 400), widths,
                      "the 200% zoom CSS viewport is not measured")
        self.assertIn("bv.layout_problems", src)
        self.assertIn("horizontal overflow", src)

    def test_every_section_is_visited(self):
        src = self.CASE.read_text(encoding="utf-8")
        sections = eval(re.search(r"SECTIONS = (\[.*?\])", src, re.S).group(1))
        self.assertEqual(["overview", "verification", "acceptance", "access",
                          "diagnostics", "installation", "policy"], sections)

    def test_no_two_screenshots_share_a_path(self):
        from collections import Counter
        src = self.CASE.read_text(encoding="utf-8")
        widths = eval(re.search(r"WIDTHS = (\[.*?\])", src, re.S).group(1))
        names = []
        for name in re.findall(r'shot\(f?"([^"]+)"\)', src):
            if "{w}x{h}" in name and "sections" in name:
                names += [name.replace("{w}x{h}", f"{w}x{h}")
                          for w, h in widths]
            elif "{w}x{h}" in name:
                names += [name.replace("{w}x{h}", f"{w}x{h}")
                          for w, h in [(1440, 900), (390, 780)]]
            else:
                names.append(name)
        self.assertGreater(len(names), 10)
        self.assertEqual([], [n for n, c in Counter(names).items() if c > 1])

    def test_it_reads_real_backend_state_rather_than_page_strings(self):
        """The card's own requirement: the configured state is read back
        from the backend after an apply, not from the draft the page still
        holds."""
        src = self.CASE.read_text(encoding="utf-8")
        # the apply is confirmed by what the *backend* then resolves, not
        # by the draft the page still holds
        self.assertIn(".policy.kind === 'file'", src)
        self.assertIn("S.draft === null", src)
        self.assertIn("S.doctor.report.ran", src)
        self.assertIn("S.settings.page.history", src)


class TheIntentsAndScopesComeFromTheServer(Source):
    def test_the_editor_offers_exactly_the_launch_intents_bench_has(self):
        body = self.body("settingsPolicy")
        for intent in profiles.INTENTS:
            self.assertIn(f"'{intent}'", body, intent)

    def test_the_gate_scopes_are_read_from_the_payload(self):
        body = self.body("settingsPolicy")
        self.assertIn("Object.keys(page.acceptance.scopes)", body)

    def test_a_review_mode_the_board_cannot_enforce_is_offered_disabled(self):
        body = self.body("reviewSelect")
        self.assertIn("m.available ? '' : ' disabled'", body)
        self.assertIn("not available on this board", body)


if __name__ == "__main__":
    unittest.main()
