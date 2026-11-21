"""Card 177 (WP2) — focused verification made usable, and only valid reuse.

Three things an operator developing bench is offered, each held here:

- **The focused-first prompt section** (`develop/operator-local/prompts/`):
  composed onto the built-in work and PR-fix templates it passes the
  board's own template contract, keeps every placeholder and marker, and
  names only operations bench really has. `ComposedPrompts`.
- **The policy change plan** (`develop/operator-local/focused_policy.py`):
  one batched, additive draft — focused scopes, the browser report
  contract, the `--probe` preparation — that preserves every other grant,
  gate and key, overwrites nothing silently, and is applied only through
  Settings' revision-guarded validate, preview and apply. `ThePlan`,
  `ThroughSettings`.
- **The cycle itself**, on disposable checks: a focused pass never
  satisfies the full requirement; the full pass the work run asked for is
  returned to the review run without running again; a new commit is a new
  question. `TheCycle`. (Each identity dimension that refuses reuse, and
  declared cross-name equivalence with its positive and negative cases,
  are held in `tests/test_check_reuse.py`, `tests/test_declared_equivalence.py`
  and `tests/test_focused_verification.py`.)

Nothing here reads or writes a live board, the operator's policy or an
operating agreement.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_check_reuse import ReuseCase
from tests.test_settings_editing import Fixture

REPO = Path(__file__).resolve().parents[1]
OPERATOR = REPO / "develop" / "operator-local"
PROMPTS = OPERATOR / "prompts"
CORE = REPO / "manager" / "core"

import templates  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _load("operator_compose", PROMPTS / "compose.py")
focused_policy = _load("operator_focused_policy", OPERATOR / "focused_policy.py")


# ── the focused-first prompt section ───────────────────────────────────


class ComposedPrompts(unittest.TestCase):

    def test_each_override_passes_the_template_contract(self):
        for name in compose.NAMES:
            with self.subTest(name):
                text = compose.composed(name)
                self.assertEqual([], templates.problems(name, text))
                # the built-in, byte for byte, then the section: every
                # placeholder and marker the board parses is still there
                self.assertTrue(text.startswith(
                    (CORE / "prompts" / name).read_text().rstrip("\n")))
                for marker in templates.SPECS[name]["markers"]:
                    self.assertIn(marker, text)

    def test_the_section_is_data_safe_and_names_real_operations(self):
        section = compose.SECTION.read_text(encoding="utf-8")
        # no placeholder can hide in it: str.format would read a brace
        self.assertNotIn("{", section)
        self.assertNotIn("}", section)
        shim = (CORE / "bench-verify").read_text(encoding="utf-8")
        for flag in ("--details", "--again"):
            self.assertIn(flag, section)
            self.assertIn(flag, shim, f"bench-verify has no {flag}")
        for phrase in ("bench-verify <check> <scope>", "bare name",
                       "final committed head", "not a whole-suite result",
                       "REUSED", "independent review", "evidence id",
                       "Never invent a command grant"):
            self.assertIn(phrase, section)
        # it names no scope that might not be declared, and no path
        self.assertNotRegex(section, r"bench-verify bench-\w+ \w")
        self.assertNotIn("/home/", section)

    def test_installing_refuses_to_replace_an_existing_override(self):
        out = Path(tempfile.mkdtemp(prefix="bench-prompts-"))
        self.addCleanup(__import__("shutil").rmtree, out, True)
        (out / "work.md").write_text("mine\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as said:
            status = compose.main(["--out", str(out)])
        self.assertIn("already there", said.getvalue())
        self.assertEqual(1, status)
        self.assertEqual("mine\n", (out / "work.md").read_text())
        self.assertEqual(compose.composed("act-pr.md"),
                         (out / "act-pr.md").read_text())

    def test_the_operator_notes_name_the_focused_loop(self):
        text = (OPERATOR / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in ("python3 -m unittest", "bare name", "focused",
                       "prompts/focused-first.md", "focused_policy.py",
                       "setupRetries"):
            self.assertIn(phrase, text)


# ── the policy change plan ─────────────────────────────────────────────


def live_shaped() -> dict:
    """A document shaped like the one the lead read back: scopes empty, no
    report contract, no probe, a separately named review variant the
    review profile does not offer, a focused check of its own name, and a
    gate on the full backend run. Paths are relative words, never a disk."""
    return {
        "v": 1,
        "context": [{"name": "workflow-brief",
                     "path": "tool:manager/local/AGENTS.md", "required": False}],
        "prepare": [
            {"name": "backend-deps", "run": "python3 verify/preflight backend",
             "required": True},
            {"name": "browser-start", "run": "python3 verify/preflight browser",
             "required": True},
            {"name": "docs-deps", "run": "python3 verify/preflight docs",
             "required": True},
        ],
        "checks": [
            {"name": "bench-backend", "run": "python3 verify/backend",
             "root": "source", "prepare": ["backend-deps"], "timeout": 1800},
            {"name": "bench-backend-proposed", "run": "python3 verify/backend",
             "root": "review", "prepare": ["backend-deps"], "timeout": 1800},
            {"name": "bench-browser", "run": "python3 verify/browser",
             "root": "source", "prepare": ["browser-start"], "timeout": 900},
            {"name": "bench-docs", "run": "python3 verify/docs",
             "root": "source", "prepare": ["docs-deps"], "timeout": 900},
            {"name": "bench-contracts",
             "run": "python3 -m unittest tests.test_gitrepo",
             "root": "source", "prepare": ["backend-deps"], "timeout": 900},
        ],
        "profiles": {
            "work": {"verify": ["bench-backend", "bench-browser",
                                "bench-docs", "bench-contracts"]},
            "act-pr": {"verify": ["bench-backend", "bench-browser"]},
            "review": {"verify": ["bench-backend", "bench-browser"]},
        },
        "gates": {"acceptance": {"checks": ["bench-backend"],
                                 "review": "bench"}},
        "x-operator-note": "an unknown key the plan must carry over",
    }


def draft_of(document: dict, revision: str = "r" * 64) -> dict:
    return {"document": document, "revision": revision}


class ThePlan(unittest.TestCase):

    def test_one_batched_additive_change(self):
        before = live_shaped()
        found = focused_policy.plan(draft_of(copy.deepcopy(before)))
        after = found["document"]
        checks = {c["name"]: c for c in after["checks"]}
        self.assertEqual(set(focused_policy.SCOPES["bench-backend"]),
                         set(checks["bench-backend"]["scopes"]))
        self.assertEqual(set(focused_policy.SCOPES["bench-browser"]),
                         set(checks["bench-browser"]["scopes"]))
        self.assertEqual("v1", checks["bench-browser"]["report"])
        steps = {s["name"]: s for s in after["prepare"]}
        self.assertEqual("python3 verify/preflight browser --probe",
                         steps["browser-start"]["run"])
        # a focused selection is a scope, never the bare name's run
        for name in ("bench-backend", "bench-browser"):
            self.assertEqual(
                {c["name"]: c for c in before["checks"]}[name]["run"],
                checks[name]["run"])
        # only the browser's result identity moves (report, probe); the
        # backend gains scopes, which leave a full-run pass standing
        self.assertEqual(["bench-browser"], found["invalidates"])
        self.assertEqual(["bench-backend"], found["scopesOnly"])
        self.assertEqual("r" * 64, found["baseRevision"])

    def test_everything_else_is_carried_over_untouched(self):
        before = live_shaped()
        after = focused_policy.plan(draft_of(copy.deepcopy(before)))["document"]
        for key in ("gates", "profiles", "context", "x-operator-note", "v"):
            self.assertEqual(before[key], after[key], key)
        untouched = ("bench-backend-proposed", "bench-docs", "bench-contracts")
        was = {c["name"]: c for c in before["checks"]}
        now = {c["name"]: c for c in after["checks"]}
        for name in untouched:
            self.assertEqual(was[name], now[name], name)
        self.assertEqual(set(was), set(now), "no check created or removed")
        self.assertNotIn("accepts", json.dumps(after),
                         "no equivalence is declared: none is needed")
        for name in ("backend-deps", "docs-deps"):
            self.assertEqual(
                {s["name"]: s for s in before["prepare"]}[name],
                {s["name"]: s for s in after["prepare"]}[name])

    def test_the_input_draft_is_not_mutated(self):
        before = live_shaped()
        given = draft_of(before)
        focused_policy.plan(given)
        self.assertEqual(live_shaped(), given["document"])

    def test_it_is_idempotent(self):
        once = focused_policy.plan(draft_of(live_shaped()))
        twice = focused_policy.plan(draft_of(once["document"]))
        self.assertTrue(twice["unchanged"])
        self.assertEqual([], twice["changes"])
        self.assertEqual([], twice["warnings"])
        self.assertEqual(once["document"], twice["document"])

    def test_nothing_is_overwritten_silently(self):
        doc = live_shaped()
        checks = {c["name"]: c for c in doc["checks"]}
        checks["bench-backend"]["scopes"] = {"contracts": ["--select",
                                                           "tests.test_x"]}
        checks["bench-browser"]["report"] = "v2"
        found = focused_policy.plan(draft_of(doc))
        now = {c["name"]: c for c in found["document"]["checks"]}
        self.assertEqual(["--select", "tests.test_x"],
                         now["bench-backend"]["scopes"]["contracts"])
        self.assertEqual("v2", now["bench-browser"]["report"])
        self.assertEqual(2, len(found["warnings"]), found["warnings"])
        # the other scopes are still added beside the kept one
        self.assertIn("architecture", now["bench-backend"]["scopes"])

    def test_a_missing_check_is_named_never_created(self):
        doc = live_shaped()
        doc["checks"] = [c for c in doc["checks"] if c["name"] != "bench-browser"]
        found = focused_policy.plan(draft_of(doc))
        self.assertNotIn("bench-browser",
                         {c["name"] for c in found["document"]["checks"]})
        self.assertTrue(any("bench-browser" in w for w in found["warnings"]))
        steps = {s["name"]: s for s in found["document"]["prepare"]}
        self.assertNotIn("--probe", steps["browser-start"]["run"])

    def test_a_shared_preparation_step_names_every_check_it_restales(self):
        doc = live_shaped()
        doc["checks"].append({"name": "browser-smoke",
                              "run": "python3 verify/browser",
                              "root": "source", "prepare": ["browser-start"]})
        found = focused_policy.plan(draft_of(doc))
        self.assertIn("browser-smoke", found["invalidates"])

    def test_the_scopes_are_the_shipped_examples(self):
        example = json.loads((REPO / "verify" / "examples" / "focused-checks"
                              / "profiles.focused.example.json").read_text())
        for check in example["checks"]:
            self.assertEqual(check["scopes"],
                             focused_policy.SCOPES[check["name"]])

    def test_not_a_draft_is_refused(self):
        with self.assertRaises(ValueError):
            focused_policy.plan({"checks": []})


class ThroughSettings(unittest.TestCase):
    """The plan made from `settings.draft()`, then bench's own validate,
    preview and revision-guarded apply, then the readback."""

    def test_validate_preview_apply_and_exact_readback(self):
        fx = Fixture(self, doc=live_shaped())
        before = fx.raw()
        found = fx.run(
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('p', {str(OPERATOR / 'focused_policy.py')!r})\n"
            "p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)\n"
            "plan = p.plan(settings.draft())\n"
            "v = settings.validate({'document': plan['document']})\n"
            "pv = settings.preview({'document': plan['document']})\n"
            "R = {'plan': plan, 'validate': v, 'preview': pv}")
        self.assertEqual(before, fx.raw(), "validate and preview wrote nothing")
        plan, preview = found["plan"], found["preview"]
        self.assertTrue(found["validate"]["ok"], found["validate"]["errors"])
        self.assertEqual(preview["baseRevision"], plan["baseRevision"])
        kinds = {(c["kind"], c["what"]) for c in preview["changes"]}
        self.assertEqual({("check-changed", "bench-browser"),
                          ("check-scopes-changed", "bench-backend")},
                         kinds, "no gate, access or grant change")
        self.assertEqual(sorted(plan["invalidates"]),
                         sorted(preview["evidence"]["invalidated"]))

        applied = fx.run(
            f"plan = json.loads({json.dumps(json.dumps(plan))})\n"
            "R = settings.apply({'document': plan['document'], "
            "'revision': plan['baseRevision']})")
        self.assertEqual(plan["baseRevision"], applied["previousRevision"])
        self.assertEqual(plan["document"], fx.parsed(), "exact readback")
        self.assertIn("change what a run already in flight is allowed to do",
                      applied["didNot"])

    def test_preview_names_the_identity_dimension_not_the_check_id(self):
        """The check id carries the scopes and not a preparation step's
        definition; a result identity is the other way round. Preview
        reports what the evidence will actually do."""
        doc = {"v": 1,
               "prepare": [{"name": "deps", "run": "python3 -m pip --version"}],
               "checks": [{"name": "unit", "run": "python3 -m unittest",
                           "root": "source", "prepare": ["deps"]}],
               "profiles": {"work": {"verify": ["unit"]}}}
        cases = (
            ("a scope", lambda d: d["checks"][0].update(
                scopes={"fast": ["-k", "fast"]}),
             {("check-scopes-changed", "unit")}, []),
            ("a preparation step", lambda d: d["prepare"][0].update(
                run="python3 -m pip --version --verbose"),
             {("check-changed", "unit")}, ["unit"]),
            ("the report contract", lambda d: d["checks"][0].update(report="v1"),
             {("check-changed", "unit")}, ["unit"]),
        )
        for label, mutate, kinds, invalidated in cases:
            with self.subTest(label):
                fx = Fixture(self, doc=doc)
                draft = copy.deepcopy(doc)
                mutate(draft)
                found = fx.run(
                    f"R = settings.preview({{'document': json.loads("
                    f"{json.dumps(json.dumps(draft))})}})")
                self.assertTrue(found["ok"], found["errors"])
                self.assertEqual(kinds, {(c["kind"], c["what"])
                                         for c in found["changes"]})
                self.assertEqual(invalidated, found["evidence"]["invalidated"])

    def test_a_plan_from_an_older_reading_cannot_overwrite_a_newer_save(self):
        fx = Fixture(self, doc=live_shaped())
        stale = fx.run("R = settings.draft()")
        newer = live_shaped()
        newer["checks"][3]["timeout"] = 1200
        fx.doc_path.write_text(json.dumps(newer, indent=2) + "\n",
                               encoding="utf-8")
        written = fx.raw()
        plan = focused_policy.plan(stale)
        refused = fx.run(
            f"plan = json.loads({json.dumps(json.dumps(plan))})\n"
            "try:\n"
            "    settings.apply({'document': plan['document'], "
            "'revision': plan['baseRevision']})\n"
            "    R = 'applied'\n"
            "except settings.Conflict as exc:\n"
            "    R = 'conflict'\n")
        self.assertEqual("conflict", refused)
        self.assertEqual(written, fx.raw(), "the newer save is intact")


# ── the cycle, on disposable checks ────────────────────────────────────


CONTRACTS = ["--select", "tests.test_gitrepo"]


class TheCycle(ReuseCase):
    """Work run: focused, then full at its final commit. Review run: the
    same full check at the same head, answered by reuse."""

    def planned(self) -> dict:
        document = self.counted(root="source", scopes={"contracts": list(CONTRACTS)})
        document["profiles"]["act-pr"] = {"verify": ["unit"]}
        return document

    def test_focused_never_accepts_and_the_full_pass_is_reused_by_review(self):
        document = self.planned()
        focused = self.run_check(document, scope="contracts")
        self.assertEqual("passed", focused["outcome"], focused.get("why"))
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertNotEqual("met", row["state"],
                            "a focused pass does not satisfy full acceptance")

        full = self.again(document, "run-1b")
        self.assertNotIn("reused", full, "a focused pass is not reused as full")
        self.assertEqual("passed", full["outcome"], full.get("why"))
        self.assertEqual(2, self.runs())

        review = self.again(document, "review-1", intent="review")
        self.assertEqual(full["id"], review["reused"])
        self.assertEqual(2, self.runs(), "the review ran nothing")
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertEqual("met", row["state"])
        self.assertEqual(full["id"], row["evidence"])

    def test_a_new_commit_after_review_is_a_new_question(self):
        document = self.planned()
        full = self.run_check(document)
        self.commit_in(self.wt, "a repair\n")
        self.fresh()
        repair = self.again(document, "repair-1", intent="act-pr")
        self.assertNotIn("reused", repair)
        self.assertNotEqual(full["id"], repair["id"])
        self.assertEqual(2, self.runs())

    def test_adding_scopes_leaves_a_full_pass_standing(self):
        """A scope is coverage asked for by name: declaring one moves no
        dimension of a full run's result identity, so nothing reruns."""
        document = self.counted(root="source")
        first = self.run_check(document)
        planned = copy.deepcopy(document)
        planned["checks"][0]["scopes"] = {"contracts": list(CONTRACTS)}
        self.declares(planned)
        self.assertEqual("passed", self.reading(first)["outcome"])
        review = self.again(planned, "review-2", intent="review")
        self.assertEqual(first["id"], review["reused"])
        self.assertEqual(1, self.runs())

    def test_the_report_contract_or_a_changed_preparation_restales_once(self):
        """What the plan's `invalidates` names: each costs one fresh run,
        which the review then reuses."""
        for label, mutate, reason in (
                ("report", lambda d: d["checks"][0].update(report="v1"),
                 "check-changed"),
                ("probe", lambda d: d["prepare"][0].update(
                    run=d["prepare"][0]["run"] + " --probe"),
                 "prepare-changed")):
            with self.subTest(label):
                self.count.unlink(missing_ok=True)
                document = self.counted(root="source", prepare=["deps"])
                first = self.again(document, f"base-{label}", again=True)
                self.assertEqual("passed", first["outcome"], first.get("why"))
                planned = copy.deepcopy(document)
                mutate(planned)
                self.declares(planned)
                reading = self.reading(first)
                self.assertEqual("stale", reading["outcome"])
                self.assertEqual(reason, reading["reason"])
                if label == "report":
                    # the stub writes no report: a contract check that
                    # writes none is no pass — exactly why the browser's
                    # wrapper must speak v1 before it is declared
                    continue
                fresh = self.again(planned, f"run-{label}")
                self.assertNotIn("reused", fresh)
                self.assertEqual(2, self.runs())
                review = self.again(planned, f"review-{label}", intent="review")
                self.assertEqual(fresh["id"], review["reused"])
                self.assertEqual(2, self.runs())


if __name__ == "__main__":
    unittest.main()
