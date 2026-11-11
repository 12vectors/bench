"""A quiet card face, with every tool one labelled control away (task 76).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants — the ones that, if broken, would put
the card back where this task found it (status and ID repeated, a row of
operational chrome on every face) or, worse, would buy that quiet by
hiding a failure or a missing requirement.

The measurable half — the drawn card, the menu by keyboard, 390px — is in
`verify/browser_cases/case_board.py`.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"
sys.path.insert(0, str(BOARD.parent))


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")
        cls.card = re.search(r"function cardFor\(task\) \{.*?\n\}\n",
                             cls.html, re.S).group(0)

    def body(self, name: str) -> str:
        start = self.html.index(f"function {name}(")
        return self.html[start:self.html.index("\n}\n", start)]


class OneIdAndNoRoutineStatus(Source):
    def test_the_title_drops_only_its_own_number(self):
        body = self.body("cardTitle")
        self.assertIn("canonicalNumber(m[1]) === canonicalNumber(task.number)", body,
                      "a number that is not this card's is the author's title")
        self.assertIn("esc(cardTitle(task))", self.card)
        self.assertNotIn("esc(task.title)", self.card)

    def test_the_status_pill_is_the_delivery_status_and_nothing_else(self):
        """Task 99: the pill is the server's delivery status, drawn only
        for a word on the face list. No cascade of its own: the only two
        readings the page still adds are the quiet ones the server does
        not make — a phase not started or held, and a day-idle card — and
        only when the delivery status has nothing to say on the face."""
        self.assertIn("let promoted = !!(d && FACE_WORDS.has(d.status.word));", self.card)
        self.assertIn("let pill = promoted ? deliveryPill(d) : null;", self.card)
        self.assertEqual(self.card.count("promoted = true;"), 2, "phase idle, idle")
        self.assertIn("if (!promoted && flight && flight.idle)", self.card)
        self.assertIn("const idle = !promoted && !agent", self.card)
        for gone in ("text: 'approved'", "text: 'changes asked'", "text: 'run failed'",
                     "text: 'not published'", "text: 'completing'", "prVerdict(task)"):
            self.assertNotIn(gone, self.card, gone)
        self.assertRegex(self.card, r"promoted\s*\?\s*`<span class=\"pill status")

    def test_the_face_words_are_the_designs(self):
        """§3.6: active work, current problems, an override, publication,
        and the review step. The column's own words stay off the face."""
        import delivery  # noqa: E402 — on the path via the package's tests
        words = re.search(r"const FACE_WORDS = new Set\(\[(.*?)\]\);", self.html, re.S).group(1)
        on_face = set(re.findall(r"'([a-z-]+)'", words))
        self.assertLessEqual(on_face, set(delivery.WORDS), "every face word is a server word")
        for quiet in ("not-started", "queued", "ready-to-start", "in-progress",
                      "review", "delivered"):
            self.assertNotIn(quiet, on_face, quiet)
        for loud in ("run-failed", "check-failed", "cannot-run-check", "changes-requested",
                     "cannot-assess", "rules-invalid", "ready-overridden", "not-published",
                     "agent-working", "checks-running", "merging", "checks-needed",
                     "review-needed", "ready-to-merge", "merged-elsewhere"):
            self.assertIn(loud, on_face, loud)

    def test_no_edited_time_chatter_on_the_face(self):
        self.assertNotIn("'edited ' + ago(", self.card)
        # the detail view keeps when the file changed
        self.assertIn("edited ${esc(when)}", self.body("renderDrawer"))

    def test_the_who_row_is_drawn_for_an_owner_only(self):
        self.assertIn("const showWho = !!agent || !!task.assignee || "
                      "task.stage === 'in-progress';", self.card)


class ExceptionsStayOnTheFace(Source):
    def test_anything_bad_or_live_is_on_the_face(self):
        body = self.body("chipOnFace")
        self.assertIn("c.cls === 'bad' || c.cls === 'accent'", body)
        self.assertIn("if (c.phase) return true;", body,
                      "phase navigation is how a member reaches its parent")

    def test_a_resting_drive_is_a_tool_not_a_face_chip(self):
        """Only a running or refused drive changes the next action; the
        idle `drive ✳` chip would otherwise sit on every review card with
        a branch whenever a driver exists."""
        idle = next(l for l in self.card.splitlines()
                    if "label: 'drive', glyph: '✳'" in l)
        self.assertIn("face: false", idle)
        refused = next(l for l in self.card.splitlines()
                       if "label: 'drive', glyph: '✕'" in l)
        self.assertNotIn("face: false", refused)
        self.assertIn("if (c.face !== undefined) return c.face;",
                      self.body("chipOnFace"))

    def test_the_merge_requirements_chip_follows_the_face_rule(self):
        """§3.6, replacing "an unmet gate is always on the face": in
        review/ the ⚖ chip carries the required-only count (or ✕ for a
        current problem) on the face; before review an unmet requirement
        lies ahead and is listed in the tools, neutral; an override in
        force is on the face, in the alarm, in every stage."""
        chip = self.body("gateChip")
        self.assertIn("const c = d.counts", chip)
        self.assertIn("const review = task.stage === 'review';", chip)
        self.assertIn("gate: g.scope, face: review, title,", chip)
        self.assertIn("label: 'overridden', pre: '⚖', glyph: '!', cls: 'bad', gate: g.scope,\n"
                      "             face: true", chip)
        self.assertIn("`${c.required - c.met} needed`", chip)
        self.assertIn("problem ? 'bad'", chip)
        self.assertNotIn("g.unmet.length", chip, "the count is required obligations only")

    def test_evidence_is_on_the_face_only_beside_a_current_problem(self):
        """A failed, could-not-run or running check on the current commit
        puts ◫ on the face; a stale or historical row never does, and its
        label is not a count that could contradict the requirements."""
        on = re.search(r"const EVIDENCE_ON_FACE = new Set\(\[(.*?)\]\);", self.html, re.S).group(1)
        self.assertEqual(set(re.findall(r"'([a-z-]+)'", on)),
                         {"check-failed", "cannot-run-check", "checks-running"})
        chip = self.body("verifyChip")
        self.assertIn("face: !!(d && EVIDENCE_ON_FACE.has(d.status.word))", chip)
        self.assertIn("label: 'evidence'", chip)
        self.assertNotIn("label: `${v.passed}/${v.checks}`", chip)

    def test_acceptance_is_not_a_face_ceremony(self):
        """§8.1: the accepted count is a tool; it earns the face only when
        a person recorded that a criterion needs work or a recheck."""
        body = self.body("acceptanceChips")
        self.assertIn("section: 'acceptance', face: !!behind,", body)


class EveryToolOneControlAway(Source):
    def test_the_tools_menu_holds_every_chip(self):
        self.assertIn("const tools = chips.map(c => chipHtml(", self.card)
        self.assertIn("const face = chips.filter(c => chipOnFace(c, task));", self.card)

    def test_the_menu_control_is_labelled_and_reports_its_state(self):
        self.assertIn("more.setAttribute('aria-label', `Tools for ${ref}`);", self.card)
        self.assertIn("more.setAttribute('aria-expanded', String(menuOpen));", self.card)
        self.assertIn("role=\"group\"", self.card)

    def test_the_hover_actions_are_in_the_tools_too(self):
        self.assertIn("wireAction(twin, `${task.file}::${act.label}`, act);", self.card,
                      "the same key, so an armed window is one window")

    def test_the_detail_view_carries_every_tool(self):
        drawer = self.body("renderDrawer")
        self.assertIn("cardFor(t).querySelector('.cardtools')", drawer,
                      "the card's own builder, so the two cannot disagree")
        self.assertIn("signalsBlock(t)", drawer)

    def test_the_keyboard_reaches_the_card_and_its_actions(self):
        self.assertIn("el.tabIndex = 0;", self.card)
        self.assertIn("e.key === 'Enter' || e.key === ' '", self.card)
        self.assertIn(".card:focus-within .hoveracts", self.html)
        self.assertIn("focusCard(t.file);", self.body("renderDrawer"),
                      "closing the detail view returns focus to its card")

    def test_board_and_phases_draw_the_same_card(self):
        self.assertEqual(len(re.findall(r"appendChild\(cardFor\(", self.html)), 2)


class SignalsAreKeptApart(Source):
    def test_ci_review_bench_and_merge_requirements_are_four_rows(self):
        body = self.body("signalsBlock")
        for label in ("'Bench checks'", "'External CI'", "'Merge requirements'"):
            self.assertIn(label, body)
        self.assertIn("reviewSignals(d, pr)", body)
        self.assertNotIn("prVerdict(", body,
                         "the card's report and GitHub's folded state are not one review")
        review = self.body("reviewSignals")
        for label in ("'Bench review'", "'GitHub review'", "'Reported review'"):
            self.assertIn(label, review)
        self.assertIn("not a Bench check", body, "green CI is not a named check")
        self.assertIn("that is not a pass", body, "no gate is not a pass")

    def test_a_signal_wraps_rather_than_losing_its_revision(self):
        self.assertIn('class="pmembers signals"', self.body("signalsBlock"))
        self.assertIn("#drawer .pmembers.signals .ptitle{white-space:normal", self.html)

    def test_requirement_names_keep_a_readable_width(self):
        """The supervisor's 390px finding: `flex:1` gave a row's name a zero
        basis, so the state squeezed "Review by Bench" to three characters
        a line. The name keeps its own width, and a narrow row stacks."""
        self.assertIn(".dreqs .check-row .name,.msheet .check-row .name{flex:1 0 auto", self.html)
        narrow = self.html[self.html.index("@media (max-width:760px){\n    .dstatus .dsacts"):]
        narrow = narrow[:narrow.index("\n  }")]
        self.assertIn(".msheet .check-row{flex-wrap:wrap", narrow)
        self.assertIn(".msheet .check-row .state{flex:1 1 100%;text-align:left", narrow)
        self.assertIn('class="sheet msheet" role="dialog" aria-label="Merge requirements"',
                      self.html)

    def test_bench_checks_count_required_obligations_only(self):
        """§7 / R4: "Required: 1 of 1 passed on X. Optional: 2 (see ◫)." —
        never the count of every check the policy offers."""
        body = self.body("signalsBlock")
        self.assertIn("full.requirements.filter(r => r.kind === 'check')", body)
        self.assertIn("`Required: ${", body)
        self.assertIn("Optional: ${optional} (see ◫).", body)
        self.assertNotIn("v.passed} of ${v.checks}", body)


class OneStatusAcrossSurfaces(Source):
    """B1 at source level: the face, the Overview, the detail head and the
    merge dialog all draw `S.state.delivery`, and nothing on the page
    re-derives a next step from the stage."""

    def test_no_status_cascade_is_left(self):
        self.assertNotIn("function nextStep(", self.html)
        self.assertNotIn("nextStep(", self.html)
        self.assertNotIn("confirm acceptance, then drag to done", self.html)
        self.assertNotIn("reported done — confirm or send back", self.html)

    def test_every_surface_reads_the_one_reading(self):
        self.assertIn("const d = deliveryOf(task);", self.card)
        self.assertIn("deliveryStatusHtml(t, d, 'ov')", self.body("overviewHtml"))
        self.assertIn("const pill = dl ? deliveryPill(dl)", self.body("renderDrawer"))
        sheet = self.html[self.html.index("async function gateSheet("):]
        sheet = sheet[:sheet.index("\n}\n")]
        self.assertIn("const d = deliveryOf(task) || full;", sheet)
        self.assertIn("deliveryStatusHtml(task, d, 'dlg')", sheet)
        self.assertIn("requirementsHtml(task, d, full, 'dlg')", sheet)

    def test_the_status_block_is_accessible(self):
        body = self.body("deliveryStatusHtml")
        self.assertIn('aria-labelledby="${id}"', body)
        self.assertIn("<h3 id=\"${id}\"", body)
        self.assertIn("role=\"${d.status.tone === 'alarm' ? 'alert' : 'status'}\"", body)
        # the primary first, then alternatives in one native disclosure
        self.assertLess(body.index("deliveryButtonHtml(d.primary"), body.index("<details class=\"dsmore\""))
        self.assertIn("More actions (${others.length})", body)

    def test_a_disabled_action_says_why_and_does_nothing(self):
        body = self.body("deliveryButtonHtml")
        self.assertIn("disabled aria-describedby=\"dwhy-${slot}\"", body)
        wire = self.body("wireDeliveryButtons")
        self.assertIn("if (!a || !a.enabled) return;", wire)
        self.assertIn("wireAction(btn, `${task.file}::delivery::${a.action}`", wire)

    def test_every_action_the_server_names_is_routed_or_a_view(self):
        """Only supported actions are wired: each enabled key the server
        can name has a route here, and the two later handlers (Run
        required checks, sign-off) arrive disabled from the server."""
        import delivery
        run = self.body("deliveryRun")
        for key, spec in delivery.ACTIONS.items():
            if spec.get("later") or key == "open-pr-url":
                continue
            self.assertIn(f"case '{key}':", run, key)
        self.assertNotIn("case 'run-required-checks':", run)
        self.assertNotIn("case 'confirm-criteria':", run)

    def test_requirements_are_supporting_detail_with_history_apart(self):
        body = self.body("requirementsHtml")
        self.assertIn("Optional checks and earlier runs (${optional})", body)
        self.assertIn("fold('technical', 'Technical details'", body)
        self.assertIn("that is not a pass", body)
        head = self.body("requirementsHeadline")
        self.assertIn("merge requirement", head)
        self.assertIn("d.counts", head)

    def test_a_repaint_keeps_folds_and_a_focused_action(self):
        self.assertIn("data-fold=\"${where}-more\"", self.body("deliveryStatusHtml"))
        self.assertIn("data-fold=\"${where}-${key}\"", self.body("requirementsHtml"))
        drawer = self.body("renderDrawer")
        self.assertIn("active.dataset.dslot", drawer)
        self.assertIn("body.querySelector(`[data-dslot=\"${dslot}\"]`)", drawer)

    def test_the_full_reading_is_fetched_only_when_the_compact_one_moved(self):
        body = self.body("loadDeliveryFull")
        self.assertIn("cur.key === key", body)
        self.assertIn("/api/card/delivery?file=", body)
        self.assertNotIn("method: 'POST'", body)


if __name__ == "__main__":
    unittest.main()
