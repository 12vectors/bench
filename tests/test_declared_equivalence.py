"""Card 104 — a differently named check counts only with proven equivalence.

Design #94 §5 and §6: a record filed under another check's name answers a
requirement only when the required check declares it (`"accepts"`), the
two definitions agree in every substantive dimension, the record ran in
the same root class (a worktree is never a pinned checkout, and a pinned
checkout is only `fresh` when the runner found it clean), and its own
result identity holds — source, programs and interpreters, preparation,
inputs, environment and runner. Neither the declaration nor equal
substance is enough alone. Once it qualifies, the record goes through the
very same head, coverage, denial and newest-first filters as a record of
the check's own name, and it keeps its own name, evidence id, requester
and checkout wherever it is cited.

Every case runs against a disposable tool install, work root and
application checkout (`GateCase` / `ReviewCase`) with stub executables
that count their own executions. Nothing here reads or writes a live
board, SAFER, a remote or operator policy.

- R3 / E6 — `R3StaysTrue`.
- E7 — `EquivalentEvidenceCounts`, `SourceAndReviewAtOneHead`.
- E8 — `EachDimensionRefusesReuse` (definition dimensions, one subtest
  each) and `TheRecordMustHoldToo` (program, interpreter, env value,
  runner, source, dirty, legacy, a reused pinned checkout).
- browser vs backend, text is never evidence — `NotThroughLabels`.
- precedence and authorisation — `PrecedenceAndAuthorisation`.
- validation and directionality — `TheDeclarationIsValidated`.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests.test_check_reuse import COUNTED, ReuseCase  # noqa: E402
from tests.test_integration_gates import CARD, git  # noqa: E402
from tests.test_review_checkouts import CARD as REVIEW_CARD  # noqa: E402
from tests.test_review_checkouts import RECORDER, ReviewCase  # noqa: E402

import config  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import profiles  # noqa: E402
import settings  # noqa: E402
import state  # noqa: E402
import verify  # noqa: E402

TWIN = "unit-twin"


class EquivalenceCase(ReuseCase):

    def twins(self, *, accepts: bool = True, root: str = "source",
              twin: dict | None = None, unit: dict | None = None,
              gate=None) -> dict:
        """`unit`, which a gate requires, and `unit-twin`, the same
        definition under another name — with `twin` and `unit` overriding
        either side's fields."""
        body = COUNTED.replace("@COUNT@", str(self.count)) \
                      .replace("@FLAG@", str(self.flag))
        run = str(self.executable("unit", body))
        mine = {"name": "unit", "run": run, "root": root, **(unit or {})}
        if accepts:
            mine["accepts"] = [TWIN]
        other = {"name": TWIN, "run": run, "root": root, **(twin or {})}
        return {"v": 1, "checks": [mine, other],
                "context": [{"name": "brief",
                             "path": "work:reference/brief.md"}],
                "prepare": [{"name": "deps",
                             "run": str(self.executable("deps", "#!/bin/sh\n"
                                                        "exit 0\n"))}],
                "profiles": {"work": {"verify": ["unit", TWIN]},
                             "review": {"verify": ["unit", TWIN]}},
                "gates": {"acceptance": {"checks": [gate or "unit"]}}}

    def twin_passes(self, document: dict, *, agent: str = "rev-1",
                    scope: str | None = None) -> dict:
        self.declares(document)
        self.manifest("work", agent=agent)
        self.fresh()
        found = self.ask(TWIN, agent=agent, scope=scope)
        self.assertEqual(found["outcome"], "passed", found.get("why"))
        self.fresh()
        return found

    def row(self) -> dict:
        self.fresh()
        return self.decide()["requirements"][0]


# ── R3 / E6 ────────────────────────────────────────────────────────────


class R3StaysTrue(EquivalenceCase):

    def test_another_name_with_the_same_argv_never_counts_without_accepts(self):
        document = self.twins(accepts=False)
        self.twin_passes(document)
        row = self.row()
        self.assertEqual(row["state"], "unmet")
        self.assertEqual(row["reason"], "not-run")
        self.assertNotIn("equivalents", row)
        self.assertNotIn("acceptedFrom", row)
        # and a request for `unit` runs, rather than reusing the twin
        found = self.again(document, "run-2")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 2)

    def test_a_declaration_alone_with_different_substance_counts_for_nothing(self):
        document = self.twins(twin={"run": None})
        document["checks"][1]["run"] = document["checks"][0]["run"] + " --other"
        self.twin_passes(document)
        row = self.row()
        self.assertEqual(row["reason"], "not-run")
        self.assertEqual(row["equivalence"]["dimension"], "command")
        self.assertEqual(row["equivalence"]["check"], TWIN)
        self.assertIn("different command", row["why"])
        self.assertFalse(row["equivalents"][0]["equivalent"])
        found = self.again(document, "run-2")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 2)


# ── E7 ─────────────────────────────────────────────────────────────────


class EquivalentEvidenceCounts(EquivalenceCase):

    def test_an_equivalent_pass_meets_the_gate_with_its_own_provenance(self):
        document = self.twins()
        twin = self.twin_passes(document, agent="rev-1")
        stored = verify.path_of(twin["id"]).read_bytes()
        row = self.row()
        self.assertEqual(row["state"], "met", row["why"])
        self.assertEqual(row["evidence"], twin["id"])
        self.assertEqual(row["acceptedFrom"]["check"], TWIN)
        self.assertEqual(row["acceptedFrom"]["evidence"], twin["id"])
        self.assertEqual(row["acceptedFrom"]["agent"], "rev-1")
        self.assertEqual(row["acceptedFrom"]["rootPolicy"], "source")
        self.assertEqual(row["acceptedFrom"]["rootClass"], "fresh")
        self.assertIn(f"'{TWIN}'", row["why"])
        self.assertIn("accepts", row["why"])
        self.assertTrue(row["equivalents"][0]["equivalent"])
        # never copied, never relabelled as `unit`
        self.assertEqual(verify.path_of(twin["id"]).read_bytes(), stored)
        self.assertEqual(verify.read(twin["id"])["check"], TWIN)
        self.assertEqual(len(self.evidence_files()), 1)
        # the delivery reading carries the same provenance
        self.assertEqual(delivery._requirement(row, twin["source"]["commit"])
                         ["reusedFrom"]["check"], TWIN)

    def test_a_request_for_the_required_check_reuses_it_and_runs_nothing(self):
        document = self.twins()
        twin = self.twin_passes(document, agent="rev-1")
        found = self.again(document, "run-2")
        self.assertEqual(self.runs(), 1, "the equivalent pass was reused")
        self.assertEqual(found["reused"], twin["id"])
        self.assertEqual(found["check"], TWIN, "the evidence keeps its name")
        reuse = found["reuse"]
        self.assertEqual(reuse["check"], "unit")
        self.assertEqual(reuse["acceptedFrom"], TWIN)
        self.assertEqual(reuse["original"]["agent"], "rev-1")
        self.assertEqual(reuse["original"]["check"], TWIN)
        self.assertEqual(verify.public_reuse(reuse)["acceptedFrom"], TWIN)
        self.assertTrue(any("which it accepts" in e.get("summary", "")
                            for e in state.BOARD_EVENTS))

    def test_again_still_runs(self):
        document = self.twins()
        self.twin_passes(document)
        self.declares(document)
        self.manifest(agent="run-2")
        self.fresh()
        found = self.ask(agent="run-2", again=True)
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 2)

    def test_two_worktree_checks_of_one_definition_are_equivalent(self):
        document = self.twins(root="worktree")
        twin = self.twin_passes(document)
        row = self.row()
        self.assertEqual(row["state"], "met", row["why"])
        self.assertEqual(row["evidence"], twin["id"])


class SourceAndReviewAtOneHead(ReviewCase):
    """E7 with real pinned checkouts: a `review` check run in the checkout
    a PR review pinned, at the same head H as the card's branch, answers
    a `source` check that accepts it."""

    def document(self, *, review_run: str | None = None) -> dict:
        recorder = self.bin / "check"
        recorder.write_text(RECORDER.format(python=__import__("sys").executable,
                                            record=str(self.checks)),
                            encoding="utf-8")
        recorder.chmod(0o755)
        return {
            "v": 1,
            "checks": [
                {"name": "unit", "run": str(recorder), "root": "source",
                 "accepts": ["unit-review"]},
                {"name": "unit-review", "run": review_run or str(recorder),
                 "root": "review"}],
            "profiles": {"work": {"verify": ["unit"]},
                         "review": {"verify": ["unit-review"]}},
            "gates": {"acceptance": {"checks": ["unit"]}},
        }

    def test_the_review_pass_meets_the_source_requirement_and_is_reused(self):
        head = self.proposes()
        self.declares(self.document())
        record, _ = self.pinned("rev-1")
        found = verify.request(check="unit-review", agent="rev-1")
        self.assertEqual(found["outcome"], "passed", found["why"])
        self.assertEqual(found["identity"]["rootClass"], "fresh")
        self.assertEqual(found["source"]["commit"], head)
        verify.forget_cached()

        row = gates.evaluate(REVIEW_CARD, "acceptance")["requirements"][0]
        self.assertEqual(row["state"], "met", row["why"])
        self.assertEqual(row["evidence"], found["id"])
        self.assertEqual(row["acceptedFrom"]["check"], "unit-review")
        self.assertEqual(row["acceptedFrom"]["rootPolicy"], "review")
        self.assertEqual(row["acceptedFrom"]["agent"], "rev-1")

        person = verify.request(check="unit", task=REVIEW_CARD, stage="review",
                                actor="you")
        self.assertEqual(len(self.invocations()), 1,
                         "no duplicate execution at the same head")
        self.assertEqual(person["reused"], found["id"])
        self.assertEqual(person["reuse"]["acceptedFrom"], "unit-review")
        self.assertEqual(person["reuse"]["commit"], head)
        self.assertEqual(verify.read(found["id"])["root"]["path"],
                         record["path"], "the original checkout is kept")

    def test_a_review_command_that_differs_is_refused_and_named(self):
        self.proposes()
        self.declares(self.document(review_run=str(self.bin / "check")
                                    + " --review"))
        self.pinned("rev-1")
        verify.request(check="unit-review", agent="rev-1")
        verify.forget_cached()
        row = gates.evaluate(REVIEW_CARD, "acceptance")["requirements"][0]
        self.assertEqual(row["state"], "unmet")
        self.assertEqual(row["equivalence"]["dimension"], "command")


# ── E8 ─────────────────────────────────────────────────────────────────


class EachDimensionRefusesReuse(EquivalenceCase):
    """One subtest per definition dimension: the twin differs in exactly
    that, passes, and neither the gate nor a request takes it for `unit`."""

    def variants(self):
        return [
            ("root class", {"twin": {"root": "worktree"}}, "root"),
            ("argv", {"twin": {"run": "+ --x"}}, "command"),
            ("timeout", {"twin": {"timeout": 30}}, "timeout"),
            ("scope arguments", {"unit": {"scopes": {"fast": ["-k", "a"]}},
                                 "twin": {"scopes": {"fast": ["-k", "b"]}},
                                 "scope": "fast"}, "scope"),
            ("preparation", {"twin": {"prepare": ["deps"]}}, "prepare"),
            ("declared inputs", {"twin": {"inputs": ["brief"]}}, "inputs"),
            ("declared env", {"twin": {"env": ["BENCH_TEST_DB"]}}, "env"),
        ]

    def test_each_dimension_names_itself(self):
        for label, spec, dimension in self.variants():
            with self.subTest(label):
                self.count.write_text("", encoding="utf-8")
                for path in self.evidence_files():
                    path.unlink()
                twin = dict(spec.get("twin") or {})
                document = self.twins(unit=spec.get("unit"), twin=twin)
                if str(twin.get("run", "")).startswith("+"):
                    document["checks"][1]["run"] = \
                        document["checks"][0]["run"] + twin["run"][1:]
                self.twin_passes(document, scope=spec.get("scope"))
                row = self.row()
                self.assertEqual(row["state"], "unmet", label)
                self.assertEqual(row["equivalence"]["dimension"], dimension,
                                 row["why"])
                self.assertIn(f"'{TWIN}'", row["equivalence"]["why"])
                found = self.again(document, "run-2", scope=spec.get("scope"))
                self.assertNotIn("reused", found, label)
                self.assertEqual(self.runs(), 2, label)


class TheRecordMustHoldToo(EquivalenceCase):
    """The definitions agree; the twin's own record does not hold."""

    def test_a_replaced_program_or_interpreter_is_outdated(self):
        for label in ("executable", "interpreter"):
            with self.subTest(label):
                for path in self.evidence_files():
                    path.unlink()
                shell = self.bin / "shell"
                shutil.copy("/bin/sh", shell)
                document = self.twins()
                script = Path(document["checks"][0]["run"])
                script.write_text(f"#!{shell}\necho ok\n", encoding="utf-8")
                self.twin_passes(document)
                if label == "executable":
                    script.write_text(script.read_text() + "# replaced\n",
                                      encoding="utf-8")
                else:
                    with shell.open("ab") as handle:
                        handle.write(b"\0replaced")
                row = self.row()
                self.assertEqual(row["reason"], "stale", row["why"])
                self.assertEqual(row["staleReason"], "program-changed")
                self.assertEqual(row["acceptedFrom"]["check"], TWIN)

    def test_a_declared_env_value_or_the_runner_moved(self):
        config._ENV["BENCH_TEST_DB"] = "one"
        document = self.twins(unit={"env": ["BENCH_TEST_DB"]},
                              twin={"env": ["BENCH_TEST_DB"]})
        self.twin_passes(document)
        self.assertEqual(self.row()["state"], "met")
        config._ENV["BENCH_TEST_DB"] = "two"
        row = self.row()
        self.assertEqual(row["staleReason"], "env-changed", row["why"])
        config._ENV["BENCH_TEST_DB"] = "one"
        self.assertEqual(self.row()["state"], "met")
        self.addCleanup(setattr, verify, "RUNNER_REVISION",
                        verify.RUNNER_REVISION)
        verify.RUNNER_REVISION = "f" * 64
        self.assertEqual(self.row()["staleReason"], "runner-changed")

    def test_another_commit_is_history(self):
        document = self.twins()
        twin = self.twin_passes(document)
        self.commit_in(self.wt, "a newer commit\n")
        row = self.row()
        self.assertEqual(row["reason"], "other-commit", row["why"])
        self.assertNotEqual(row.get("evidence"), twin["id"])

    def test_a_dirty_source_is_not_about_the_head(self):
        document = self.twins(root="worktree")
        (self.wt / "untracked.txt").write_text("new\n", encoding="utf-8")
        self.twin_passes(document)
        row = self.row()
        self.assertEqual(row["reason"], "source-dirty", row["why"])
        self.assertEqual(row["acceptedFrom"]["check"], TWIN)

    def test_an_unbound_source_proves_nothing(self):
        document = self.twins(root="worktree")
        twin = self.twin_passes(document)
        path = verify.path_of(twin["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["source"]["bound"] = False
        path.write_text(json.dumps(data), encoding="utf-8")
        row = self.row()
        self.assertNotEqual(row["state"], "met")
        self.assertIn(row["reason"], ("source-unbound", "stale"), row["why"])

    def test_a_legacy_record_cannot_be_shown_equivalent(self):
        document = self.twins()
        twin = self.twin_passes(document)
        path = verify.path_of(twin["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("resultId", "identity"):
            data.pop(key, None)
        path.write_text(json.dumps(data), encoding="utf-8")
        row = self.row()
        self.assertEqual(row["reason"], "not-run")
        self.assertEqual(row["equivalence"]["dimension"], "legacy-record")

    def test_a_pinned_checkout_that_was_not_clean_is_not_fresh(self):
        (self.wt / ".gitignore").write_text("cache/\n", encoding="utf-8")
        self.commit_in(self.wt, "ignore the cache\n")
        sha = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        root = config.VERIFY_DIR / "src" / f"{CARD[:-3]}-{sha[:12]}"
        root.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "--detach", str(root), sha)
        (root / "cache").mkdir()
        (root / "cache" / "built").write_text("stale\n", encoding="utf-8")
        document = self.twins()
        twin = self.twin_passes(document)
        self.assertEqual(twin["identity"]["rootClass"], "reused")
        row = self.row()
        self.assertEqual(row["equivalence"]["dimension"], "root-class")
        self.assertIn("not a clean pinned checkout", row["why"])


# ── labels, output and text ────────────────────────────────────────────


BROWSER = """#!{python}
print("unit passed: 412 tests, OK")
"""


class NotThroughLabels(EquivalenceCase):

    def test_a_browser_check_is_not_a_backend_check_whatever_it_prints(self):
        document = self.twins()
        document["checks"][1]["run"] = str(self.executable("browser", BROWSER))
        document["checks"][1]["help"] = "unit — the same tests"
        self.twin_passes(document)
        row = self.row()
        self.assertEqual(row["state"], "unmet")
        self.assertEqual(row["equivalence"]["dimension"], "command")

    def test_a_verdict_or_progress_claim_is_never_check_evidence(self):
        document = self.twins()
        self.declares(document)
        head = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        gates.record_verdict(CARD, verdict="APPROVE", head=head, agent="rev-1")
        row = self.row()
        self.assertEqual(row["reason"], "not-run")
        self.assertEqual(row.get("equivalents"), [])
        self.manifest(agent="run-1")
        self.assertNotIn("reused", self.ask(agent="run-1"))
        self.assertEqual(self.runs(), 1)


# ── precedence and authorisation ───────────────────────────────────────


class PrecedenceAndAuthorisation(EquivalenceCase):

    def test_a_newer_failure_of_the_required_check_beats_an_equivalent_pass(self):
        document = self.twins()
        self.twin_passes(document)
        self.flag.write_text("fail", encoding="utf-8")
        self.declares(document)
        self.manifest(agent="run-2")
        self.fresh()
        self.assertEqual(self.ask(agent="run-2", again=True)["outcome"], "failed")
        row = self.row()
        self.assertEqual(row["reason"], "failed", row["why"])
        self.assertNotIn("acceptedFrom", row)

    def test_a_newer_equivalent_failure_beats_a_pass_of_the_required_check(self):
        document = self.twins()
        self.declares(document)
        self.manifest(agent="run-1")
        self.fresh()
        self.assertEqual(self.ask(agent="run-1")["outcome"], "passed")
        self.flag.write_text("fail", encoding="utf-8")
        self.manifest(agent="rev-1")
        self.fresh()
        self.assertEqual(self.ask(TWIN, agent="rev-1")["outcome"], "failed")
        row = self.row()
        self.assertEqual(row["reason"], "failed", row["why"])
        self.assertEqual(row["acceptedFrom"]["check"], TWIN)
        self.assertIn("newer than the pass", row["why"])
        # and a request for `unit` does not step past it
        self.flag.write_text("pass", encoding="utf-8")
        found = self.again(document, "run-3")
        self.assertNotIn("reused", found)

    def test_an_equivalent_run_in_flight_is_newer_than_any_pass(self):
        document = self.twins()
        self.twin_passes(document)
        head = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        verify.RUNNING["ev:flight"] = {"task": CARD, "check": TWIN,
                                       "scope": None}
        self.addCleanup(verify.RUNNING.pop, "ev:flight", None)
        found = self.again(document, "run-2")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 2)
        # the gate reads a live equivalent record the same way
        verify.RUNNING.pop("ev:flight", None)
        live = dict(verify.read(found["id"]), id="ev:live", check=TWIN,
                    outcome="running", requested=found["requested"] + 10,
                    source={"commit": head, "bound": True, "dirty": []})
        verify.path_of("ev:live").write_text(json.dumps(live), encoding="utf-8")
        row = self.row()
        self.assertEqual(row["state"], "pending", row["why"])
        self.assertEqual(row["acceptedFrom"]["check"], TWIN)

    def test_authorisation_is_checked_before_any_reuse(self):
        """E15 across names: the twin's pass exists, `review` may no longer
        ask for `unit`, so the request is refused and nothing is reused."""
        document = self.twins()
        self.twin_passes(document)
        document["profiles"]["review"]["verify"] = [TWIN]
        refused = self.again(document, "rev-2", intent="review")
        self.assertEqual(refused["outcome"], "denied")
        self.assertNotIn("reused", refused)
        self.assertEqual(verify.reuses_for_task(CARD), [])

    def test_a_declaration_removed_from_the_policy_now_stops_counting(self):
        document = self.twins()
        self.twin_passes(document)
        self.manifest(agent="run-2")         # frozen while `accepts` held
        document["checks"][0].pop("accepts")
        self.declares(document)
        self.fresh()
        self.assertEqual(self.row()["reason"], "not-run")
        found = self.ask(agent="run-2")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 2)


# ── the declaration ────────────────────────────────────────────────────


class TheDeclarationIsValidated(EquivalenceCase):

    def codes(self, document: dict) -> list[str]:
        doc = profiles.document_from(json.dumps(document),
                                     self.local / "profiles.json")
        return sorted({d["code"] for d in profiles.resolve(
            "work", worktree=self.wt, policy=doc)["diagnostics"]
            if d["code"] != "check-undeclared"})

    def test_malformed_unknown_and_cyclic_declarations_are_refused(self):
        cases = [
            ("not a list", "unit-twin", "check-accepts-invalid"),
            ("not a name", ["Not A Name"], "check-accepts-invalid"),
            ("itself", ["unit"], "check-accepts-invalid"),
            ("twice", [TWIN, TWIN], "check-accepts-invalid"),
            ("unknown", ["nothing-declares-this"], "check-accepts-undeclared"),
        ]
        for label, accepts, code in cases:
            with self.subTest(label):
                document = self.twins()
                document["checks"][0]["accepts"] = accepts
                self.assertEqual(self.codes(document), [code])
        with self.subTest("a cycle"):
            document = self.twins()
            document["checks"][1]["accepts"] = ["unit"]
            self.assertEqual(self.codes(document), ["check-accepts-cyclic"])
        with self.subTest("a chain"):
            document = self.twins()
            third = dict(document["checks"][1], name="unit-third")
            document["checks"][1]["accepts"] = ["unit-third"]
            document["checks"].append(third)
            self.assertEqual(self.codes(document), ["check-accepts-cyclic"])

    def test_a_refused_declaration_gates_shut_rather_than_open(self):
        document = self.twins()
        document["checks"][0]["accepts"] = ["nothing-declares-this"]
        self.declares(document)
        policy = gates.gate_policy()
        self.assertFalse(policy["configured"])
        self.assertIn("check-accepts-undeclared",
                      [d["code"] for d in policy["diagnostics"]])

    def test_every_code_has_a_remedy(self):
        for code in ("check-accepts-invalid", "check-accepts-undeclared",
                     "check-accepts-cyclic"):
            self.assertIn(code, profiles.DIAGNOSTICS)

    def test_directionality_one_way_only(self):
        """`unit` accepts the twin; the twin does not accept `unit`."""
        document = self.twins(gate=TWIN)
        self.declares(document)
        self.manifest(agent="run-1")
        self.fresh()
        self.assertEqual(self.ask(agent="run-1")["outcome"], "passed")
        row = self.row()
        self.assertEqual(row["check"], TWIN)
        self.assertEqual(row["reason"], "not-run")

    def test_accepts_changes_no_check_identity_and_nothing_is_migrated(self):
        plain = self.twins(accepts=False)
        declared = self.twins()
        steps = {}
        a, _ = profiles.document_checks(
            profiles.document_from(json.dumps(plain), self.local / "p.json"),
            steps)
        b, _ = profiles.document_checks(
            profiles.document_from(json.dumps(declared), self.local / "p.json"),
            steps)
        for key in ("revision", "checkId", "checkSubstance"):
            self.assertEqual(a["unit"][key], b["unit"][key], key)
        self.assertEqual(a["unit"]["accepts"], [])
        self.assertEqual(b["unit"]["accepts"], [TWIN])

    def test_settings_validation_names_the_code(self):
        document = self.twins()
        document["checks"][0]["accepts"] = ["nothing-declares-this"]
        found = settings.validate({"document": document})
        self.assertFalse(found["ok"])
        self.assertIn("check-accepts-undeclared",
                      [e["code"] for e in found["errors"]])


if __name__ == "__main__":
    unittest.main()
