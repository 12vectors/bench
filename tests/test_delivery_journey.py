"""The evidence-to-merge journey, held together (card 106, design #94 §11–§17).

Cards 95–105 each landed one part of the design with its own regression
tests. This module is what keeps the whole of it honest as one journey,
against disposable fixtures only (`GateCase`: a temporary tool install,
work root and application repository, stub check programs). No live card,
policy, evidence record, agent or operator runtime is read or changed.

- **The scenario ledger.** Every regression scenario the design names —
  E1–E20 (backend), B1–B5 (browser) and the F1–F32 copy matrix — is read
  out of `reference/94-evidence-to-merge-workflow.md` itself and must map
  to tests that exist. A renamed or deleted test fails here rather than
  leaving a scenario silently uncovered.
- **R3 and R4, reconciled.** The design's uncollected repro module is
  retired. R1, R2 and R5 were inverted by cards 95–97; R3 (a different
  name never counts) is kept as a negative invariant; R4 is inverted here:
  the ◫ inventory still lists every offered check, and the headline count
  is the merge requirements' alone.
- **The journeys, counted.** `tests/fixtures/delivery-journeys.json` holds
  design §12's journeys as explicit rows. Their decision, look and run
  counts are computed, every interaction kept "after" names the distinct
  assurance it buys, and the documentation's table must state the
  computed numbers.
- **The SAFER36 journey, executed.** A pass recorded with a result
  identity survives unrelated policy context with zero further check
  executions — a second ask is answered by the first record. A legacy pass
  (no result identity) of the same shape needs exactly one fresh run, and
  its record is not rewritten.
- **The vocabulary is derived.** A status-word count stated in the
  documentation must be the module's own.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import unittest
from pathlib import Path

from tests.test_delivery_status import PR_URL, DeliveryCase  # noqa: E402
from tests.test_integration_gates import (CARD, PASSES,  # noqa: E402
                                          GateCase)

import delivery  # noqa: E402  (on sys.path once the case modules are imported)
import gates  # noqa: E402
import verify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "reference" / "94-evidence-to-merge-workflow.md"
JOURNEYS = ROOT / "tests" / "fixtures" / "delivery-journeys.json"
BROWSER = ROOT / "verify" / "browser_cases"

# ── the scenario ledger ────────────────────────────────────────────────
#
# Each scenario names the tests that exercise it: "module.Class.method"
# for the backend suite, "browser:case_file.Class.method" for a browser
# case (read by parsing, since its module needs a live browser session).

LEDGER: dict[str, tuple[str, ...]] = {
    "E1": ("test_result_identity.UnrelatedPolicyIsProvenance."
           "test_unrelated_next_card_context_preserves_a_same_head_pass",
           "test_evidence_to_merge.TheSafer36Shape."
           "test_new_evidence_survives_unrelated_context",
           "test_delivery_journey.TheSafer36JourneyCounted."
           "test_new_identity_needs_no_rerun"),
    "E2": ("test_result_identity.DeclaredInputs.test_an_input_that_disappears_is_named",
           "test_result_identity.EachDimensionHasItsReason."
           "test_each_mutation_gives_its_specific_reason"),
    "E3": ("test_result_identity.EachDimensionHasItsReason."
           "test_each_mutation_gives_its_specific_reason",
           "test_gate_remediation.TheProgramIsPartOfTheEvidence."
           "test_replacing_the_executable_at_the_same_path_invalidates_it",
           "test_gate_remediation.TheProgramIsPartOfTheEvidence."
           "test_a_script_handed_to_an_interpreter_is_bound",
           "test_result_identity.DeclaredEnvironment.test_the_value_is_bound_by_name"),
    "E4": ("test_evidence_to_merge.TheSafer36Shape."
           "test_the_reason_cites_h_and_its_real_stale_reason",
           "test_evidence_to_merge.Precedence."
           "test_r2_a_stale_pass_at_h_is_the_reason_not_gs_cancellation"),
    "E5": ("test_evidence_to_merge.Precedence.test_a_newer_failure_at_h_beats_its_earlier_pass",
           "test_evidence_to_merge.Precedence.test_a_running_rerun_at_h_is_never_hidden",
           "test_evidence_to_merge.Precedence.test_a_failure_on_another_head_is_history",
           "test_evidence_to_merge.Precedence."
           "test_a_pass_on_another_head_is_named_and_not_cherry_picked",
           "test_evidence_to_merge.Precedence.test_a_changed_definition_gives_its_own_reason",
           "test_evidence_to_merge.Precedence.test_a_narrow_pass_never_covers_the_full_run",
           "test_evidence_to_merge.Precedence.test_a_denial_ran_nothing_and_supersedes_nothing",
           "test_evidence_to_merge.Precedence."
           "test_a_cancellation_after_a_pass_at_h_does_not_resurrect_it",
           "test_evidence_to_merge.FindingsAreNotTheChecksGate."
           "test_an_open_finding_does_not_unmeet_a_passing_check"),
    "E6": ("test_declared_equivalence.R3StaysTrue."
           "test_another_name_with_the_same_argv_never_counts_without_accepts",),
    "E7": ("test_declared_equivalence.EquivalentEvidenceCounts."
           "test_an_equivalent_pass_meets_the_gate_with_its_own_provenance",
           "test_declared_equivalence.SourceAndReviewAtOneHead."
           "test_the_review_pass_meets_the_source_requirement_and_is_reused"),
    "E8": ("test_declared_equivalence.EachDimensionRefusesReuse."
           "test_each_dimension_names_itself",),
    "E9": ("test_declared_equivalence.NotThroughLabels."
           "test_a_verdict_or_progress_claim_is_never_check_evidence",
           "test_check_reuse.TextIsNeverEvidence.test_a_review_verdict_is_not_a_reusable_pass",
           "test_check_reuse.TextIsNeverEvidence.test_a_progress_claim_is_not_evidence"),
    "E10": ("test_delivery_status.CountsAreRequiredOnly."
            "test_an_optional_check_never_inflates_the_count",
            "test_delivery_status.CountsAreRequiredOnly.test_history_is_counted_apart",
            "test_delivery_journey.TheHeadlineIsRequiredOnly."
            "test_r4_the_inventory_lists_everything_the_headline_only_obligations"),
    "E11": ("test_evidence_to_merge.AnOverrideNeedsATarget."
            "test_no_branch_is_refused_and_nothing_is_written",
            "test_evidence_to_merge.TheRouteCannotBeBypassed."
            "test_no_target_is_refused_over_http"),
    "E12": ("test_evidence_to_merge.AnOverrideIsBoundToItsHeadAndRule."
            "test_a_new_head_is_not_covered",),
    "E13": ("test_evidence_to_merge.TheMergeRevalidatesTheEvidence."
            "test_a_push_between_the_reading_and_the_merge_is_refused",
            "test_gate_remediation.TheMergeIsOfTheCommitTheGateRead."
            "test_the_origin_merge_is_conditional_on_the_gated_head",
            "test_gate_remediation.TheMergeIsOfTheCommitTheGateRead."
            "test_a_gh_without_the_flag_is_refused_not_worked_around"),
    "E14": ("test_result_identity.LegacyRecordsKeepTheirRules."
            "test_unrelated_policy_still_stales_a_legacy_record_unrewritten",
            "test_evidence_to_merge.TheSafer36Shape."
            "test_a_legacy_record_needs_one_fresh_run_and_is_never_rewritten",
            "test_delivery_journey.TheSafer36JourneyCounted."
            "test_legacy_identity_needs_exactly_one_rerun"),
    "E15": ("test_result_identity.AuthorisationStaysSeparate."
            "test_a_reusable_pass_does_not_authorise_a_forbidden_request",
            "test_check_reuse.AuthorisationComesFirst."
            "test_a_newly_forbidden_intent_is_denied_before_reuse"),
    "E16": ("test_check_diagnostics.TheExcerptIsBoundedAndScrubbed."
            "test_secrets_are_masked_before_anything_is_cut",
            "test_check_diagnostics.TheExcerptIsBoundedAndScrubbed."
            "test_a_twenty_thousand_line_log_is_capped_with_the_count_said",
            "test_check_diagnostics.OnlyTheRunsOwnCard.test_another_cards_evidence_is_refused",
            "test_check_diagnostics.MissingDetailsAreSaidHonestly."
            "test_a_clipped_log_says_it_was_clipped",
            "test_check_diagnostics.TheExcerptIsBoundedAndScrubbed."
            "test_marker_shaped_lines_are_kept_but_cannot_start_a_line"),
    # E17 is the F1–F32 copy matrix; every F row is held separately below.
    "E17": ("test_delivery_status.TheVocabulary."
            "test_the_copy_matrix_covers_every_word_it_can",
            "test_delivery_surfaces.TheSurfacesAgree.test_f16_all_met"),
    "E18": ("test_delivery_status.ARestartForgetsNothingDurable."
            "test_a_merged_pr_survives_empty_memory_with_its_time",
            "test_delivery_status.TheCopyMatrix.test_f18_pr_already_merged_on_github",
            "test_delivery_status.TheCopyMatrix.test_f26_after_a_restart_all_met"),
    "E19": ("test_signoff_and_review_lists.SignoffIsOptIn."
            "test_an_untracked_card_needs_track_acceptance",
            "test_signoff_and_review_lists.SignoffIsOptIn."
            "test_partially_accepted_is_still_needed",
            "test_signoff_and_review_lists.SignoffIsOptIn."
            "test_all_accepted_at_the_head_is_met"),
    "E20": ("test_delivery_status.ReadingChangesNothing."
            "test_state_card_and_delivery_reads_write_fetch_and_launch_nothing",
            "test_delivery_status.ReadingChangesNothing.test_the_http_route_is_a_read"),
    "B1": ("browser:case_delivery_status.OneStatusEverywhere."
           "test_1_b1_every_surface_reads_the_backends_word_and_action",),
    "B2": ("browser:case_delivery_status.OneStatusEverywhere."
           "test_3_b2_narrow_and_wide_layouts",
           "browser:case_delivery_status.OneStatusEverywhere."
           "test_8_f16_review_signals_and_settled_dialog_rows_are_readable"),
    "B3": ("browser:case_delivery_status.OneStatusEverywhere."
           "test_4_b3_keyboard_arms_without_firing_and_escape_returns_focus",
           "browser:case_delivery_status.OneStatusEverywhere."
           "test_9_b3_space_arms_without_firing"),
    "B4": ("browser:case_delivery_status.OneStatusEverywhere."
           "test_5_b4_future_requirements_are_not_alarms",),
    "B5": ("browser:case_delivery_status.OneStatusEverywhere."
           "test_6_b5_reading_writes_nothing_and_a_live_update_keeps_the_place",),
}


def design_ids(prefix: str) -> list[str]:
    """The scenario ids the design's own tables name, in order."""
    text = DESIGN.read_text(encoding="utf-8")
    found = re.findall(rf"^\| ({prefix}\d+) \|", text, flags=re.M)
    return list(dict.fromkeys(found))


def browser_tests(filename: str) -> set[str]:
    """`Class.method` for every test in one browser case file, by parsing
    it: the module itself needs a live, sandboxed browser to import."""
    tree = ast.parse((BROWSER / filename).read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                    names.add(f"{node.name}.{item.name}")
    return names


def resolves(ref: str) -> bool:
    if ref.startswith("browser:"):
        module, _, rest = ref[len("browser:"):].partition(".")
        return rest in browser_tests(f"{module}.py")
    module, cls, method = ref.split(".")
    found = getattr(importlib.import_module(f"tests.{module}"), cls, None)
    return found is not None and callable(getattr(found, method, None))


class TheScenarioLedger(unittest.TestCase):

    def test_the_design_names_the_scenarios_this_ledger_covers(self):
        self.assertEqual(design_ids("E"), [f"E{n}" for n in range(1, 21)])
        self.assertEqual(design_ids("B"), [f"B{n}" for n in range(1, 6)])
        self.assertEqual(design_ids("F"), [f"F{n}" for n in range(1, 33)])

    def test_every_backend_and_browser_scenario_maps_to_tests_that_exist(self):
        for ident in design_ids("E") + design_ids("B"):
            with self.subTest(scenario=ident):
                self.assertIn(ident, LEDGER, f"{ident} has no test in the ledger")
                missing = [r for r in LEDGER[ident] if not resolves(r)]
                self.assertEqual(missing, [], f"{ident} names tests that do not exist")

    def test_every_f_row_has_its_own_copy_matrix_test(self):
        matrix = importlib.import_module("tests.test_delivery_status").TheCopyMatrix
        names = [n for n in dir(matrix) if n.startswith("test_f")]
        for ident in design_ids("F"):
            with self.subTest(row=ident):
                prefix = f"test_f{int(ident[1:]):02d}_"
                self.assertTrue(any(n.startswith(prefix) for n in names),
                                f"{ident} has no {prefix}* in TheCopyMatrix")

    def test_the_surfaces_check_every_row_the_matrix_checks(self):
        """`TheSurfacesAgree` re-runs the whole matrix through the page's
        compact reading: one word, one action and required-only counts on
        every surface, per row."""
        surfaces = importlib.import_module("tests.test_delivery_surfaces").TheSurfacesAgree
        matrix = importlib.import_module("tests.test_delivery_status").TheCopyMatrix
        self.assertTrue(issubclass(surfaces, matrix))

    def test_the_uncollected_repro_module_is_retired(self):
        self.assertFalse((ROOT / "tests" / "repro_94_evidence_to_merge.py").exists(),
                         "its cases are regression tests now (R3 and R4 here)")


# ── R4, inverted ───────────────────────────────────────────────────────


class TheHeadlineIsRequiredOnly(DeliveryCase):
    """R4: a required check passed at the head and an optional one nobody
    asked for. The design's repro pinned the ◫ inventory's `1 of 2` as
    the card's headline. Now the inventory still lists every offered
    check — that is what the evidence sheet is for — while the headline,
    the status and the gate count required obligations only."""

    def test_r4_the_inventory_lists_everything_the_headline_only_obligations(self):
        wt = self.worktree()
        document = self.gated()
        document["checks"].append({"name": "docs",
                                   "run": str(self.executable("docs", PASSES)),
                                   "root": "worktree"})
        for intent in document["profiles"].values():
            intent["verify"] = ["unit", "docs"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask("unit")["outcome"], "passed")
        verify.forget_cached()
        self.place("review", pr=PR_URL)

        inventory = verify.board({"stages": [{"tasks": [{"file": CARD}]}]})[CARD]
        self.assertEqual((inventory["checks"], inventory["passed"]), (2, 1))
        self.assertEqual(inventory["outcome"], "not-run")

        found = self.read()
        self.assertEqual(found["counts"], {"required": 1, "met": 1})
        self.assertEqual(found["history"]["optional"], 1)
        self.assertEqual(found["status"]["word"], "ready-to-merge")
        self.assertEqual(self.decide()["decision"], "allowed")
        compact = delivery.compact(found)
        self.assertEqual(compact["counts"], {"required": 1, "met": 1})


# ── the journeys, counted ──────────────────────────────────────────────

KINDS = ("decision", "look", "run")


def load_journeys() -> dict:
    return json.loads(JOURNEYS.read_text(encoding="utf-8"))


def tally(rows: list[dict]) -> dict:
    """Decisions, required looks and check runs, from explicit rows. A row
    marked `optional` is counted apart; `k` is the number of acceptance
    criteria, kept symbolic as the design keeps it."""
    out = {"decisions": 0, "k": 0, "looks": 0, "runs": 0, "optionalDecisions": 0}
    for row in rows:
        n = row.get("count", 1)
        if row["kind"] == "decision":
            if row.get("optional"):
                out["optionalDecisions"] += n
            elif n == "k":
                out["k"] += 1
            else:
                out["decisions"] += n
        elif row["kind"] == "look" and not row.get("optional"):
            out["looks"] += n
        elif row["kind"] == "run":
            out["runs"] += n
    return out


def written(t: dict) -> str:
    """A tally as the summary table writes it: `3 + k`, `5`."""
    return f"{t['decisions']} + k" if t["k"] else str(t["decisions"])


class TheJourneysAreCounted(unittest.TestCase):

    def test_rows_are_well_formed(self):
        for name, journey in load_journeys()["journeys"].items():
            for when in ("before", "after"):
                for row in journey[when]:
                    with self.subTest(journey=name, when=when, row=row["what"]):
                        self.assertIn(row["kind"], KINDS)
                        self.assertTrue(row["what"].strip())

    def test_the_counts_are_the_designs(self):
        expected = load_journeys()["expected"]
        for name, journey in load_journeys()["journeys"].items():
            for when in ("before", "after"):
                with self.subTest(journey=name, when=when):
                    self.assertEqual(tally(journey[when]), expected[name][when])

    def test_every_kept_interaction_names_its_distinct_assurance(self):
        for name, journey in load_journeys()["journeys"].items():
            for row in journey["after"]:
                if row["kind"] == "decision":
                    with self.subTest(journey=name, row=row["what"]):
                        self.assertTrue(row.get("assurance", "").strip(),
                                        "a kept decision says what it buys")

    def test_after_needs_no_obligatory_look_and_no_redundant_run(self):
        for name, journey in load_journeys()["journeys"].items():
            with self.subTest(journey=name):
                after = tally(journey["after"])
                before = tally(journey["before"])
                self.assertEqual(after["looks"], 0)
                self.assertFalse(any(r.get("redundant") for r in journey["after"]))
                self.assertLessEqual(after["runs"], before["runs"])
                self.assertLessEqual(after["decisions"], before["decisions"])

    def test_the_documentation_states_the_computed_counts(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for name, journey in load_journeys()["journeys"].items():
            for when in ("before", "after"):
                t = tally(journey[when])
                label = journey["label"][when]
                row = (f"| {label} | {written(t)} | {t['looks']} | {t['runs']} |")
                with self.subTest(row=row):
                    self.assertTrue(row in text, f"AGENTS.md lacks the row {row}")


# ── the SAFER36 journey, executed ──────────────────────────────────────


def executions() -> int:
    """Check executions on record for the card. A reuse is not evidence
    (`all_evidence()` never lists one), so this counts runs, not asks."""
    verify.forget_cached()
    return len(verify.for_task(CARD))


class TheSafer36JourneyCounted(GateCase):
    """Design §12.1 'after', executed: one pass at H, then approved
    context for the next card joins the policy, then the review side asks
    for the same check at the same H."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.document = self.gated()
        self.head = self.commit_in(self.wt)
        self.manifest(agent="run-1")
        self.assertEqual(self.ask(agent="run-1")["outcome"], "passed")
        self.assertEqual(executions(), 1)

    def add_unrelated_context(self) -> None:
        (self.work / "reference" / "card37-mockup.md").write_text(
            "a mockup for the next card\n", encoding="utf-8")
        self.document["context"] = [{"name": "card37-mockup",
                                     "path": "work:reference/card37-mockup.md",
                                     "required": False}]
        self.declares(self.document)
        verify.forget_cached()
        gates.forget_cached()

    def strip_identity(self) -> None:
        for record in verify.for_task(CARD):
            path = verify.path_of(record["id"])
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("resultId", "identity", "provenance", "inputs",
                        "prepareSteps", "checkSubstance"):
                data.pop(key, None)
            path.write_text(json.dumps(data, indent=2, sort_keys=True),
                            encoding="utf-8")
        verify.forget_cached()
        gates.forget_cached()

    def test_new_identity_needs_no_rerun(self):
        self.add_unrelated_context()
        self.assertEqual(self.decide()["decision"], "allowed")
        self.manifest(agent="run-2")
        second = self.ask(agent="run-2")
        self.assertEqual(second["outcome"], "passed")
        self.assertTrue(second.get("reused"), second)
        self.assertEqual(executions(), 1, "zero redundant reruns")
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_legacy_identity_needs_exactly_one_rerun(self):
        self.strip_identity()
        legacy = {p: p.read_bytes() for p in (self.state / "evidence").glob("*.json")}
        self.add_unrelated_context()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertEqual(found["unmet"][0]["staleReason"], "policy-changed")
        self.manifest(agent="run-2")
        fresh = self.ask(agent="run-2")
        self.assertEqual(fresh["outcome"], "passed")
        self.assertFalse(fresh.get("reused"), "a legacy pass is never reused")
        self.assertEqual(executions(), 2, "exactly one fresh run")
        gates.forget_cached()
        self.assertEqual(self.decide()["decision"], "allowed")
        # a third ask is answered by the fresh record: the one rerun was enough
        self.manifest(agent="run-3")
        self.assertTrue(self.ask(agent="run-3").get("reused"))
        self.assertEqual(executions(), 2)
        for path, data in legacy.items():
            self.assertEqual(path.read_bytes(), data, "history is not rewritten")


# ── the vocabulary, derived ────────────────────────────────────────────


class TheDocumentedVocabularyIsTheModules(unittest.TestCase):

    def test_a_stated_word_count_is_the_modules(self):
        words = len(delivery.WORDS)
        for path in (ROOT / "AGENTS.md", ROOT / "README.md"):
            text = path.read_text(encoding="utf-8")
            for stated in re.findall(r"(\d+) (?:delivery )?status words", text):
                with self.subTest(file=path.name):
                    self.assertEqual(int(stated), words)

    def test_every_word_the_documentation_tables_is_a_word(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        start = text.index("### Every status word")
        table = text[start:text.index("\n## ", start)]
        listed = re.findall(r"^\| `([a-z-]+)` \|", table, flags=re.M)
        self.assertEqual(sorted(listed), sorted(delivery.WORDS))


if __name__ == "__main__":
    unittest.main()
