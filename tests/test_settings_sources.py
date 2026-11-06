"""The Settings page's reading (task 74): every section resolved from the
backend authority that already answers for it, across five fixtures.

The failure this suite exists to catch is a page that *looks* right and
answers from somewhere else. `settings.py` owns no policy, so each
assertion here is of the form "what the page would show equals what the
resolver says", plus the one thing the module does add: a state vocabulary
in which "no gate configured" cannot be read as a passed gate.

Five fixtures, because the honest answer is different in each:

- **default/legacy** — no document at all. Every board that existed
  before execution profiles.
- **partially configured** — a document that declares a check for one
  intent and leaves the others on the legacy settings.
- **fully configured** — checks, preparation, profiles and gates.
- **invalid** — a gate requiring a check the document declares nowhere,
  which is refused where the document is read.
- **unsupported** — a profile requiring `filesystem-sandbox`, which
  nothing installed enforces.

Every probe runs in a fresh interpreter with the inherited `BOARD_*`,
`AGENT_*` and `BENCH_*` variables removed, because `config` reads its
settings at import and a developer's own board leaking into them is
exactly the pollution that makes a settings test lie.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"

# ── fixtures ───────────────────────────────────────────────────────────

LEGACY = None

PARTIAL = {
    "v": 1,
    "checks": [{"name": "unit", "run": "python3 -m unittest discover -s tests",
                "root": "worktree"}],
    "profiles": {"work": {"verify": ["unit"]}},
}

FULL = {
    "v": 1,
    "prepare": [{"name": "deps", "run": "python3 -m pip install -e .",
                 "network": True}],
    "checks": [
        {"name": "unit", "run": "python3 -m unittest discover -s tests",
         "root": "worktree", "prepare": ["deps"], "timeout": 900,
         "scopes": {"fast": ["-k", "fast"]}},
        {"name": "proposed", "run": "python3 -m unittest discover -s tests",
         "root": "review"},
    ],
    "profiles": {"work": {"verify": ["unit"]},
                 "act-pr": {"verify": ["unit"]},
                 "review": {"verify": ["proposed"]}},
    "gates": {"member": [],
              "integration": {"checks": ["unit"], "review": "bench"},
              "acceptance": {"checks": [{"check": "unit", "scope": "fast"}]},
              "overrides": {"allow": False}},
}
# `member` as a list is itself a refusal; the valid full fixture wants an
# object, and the broken shape is the `INVALID` fixture's job.
FULL["gates"]["member"] = {"checks": ["unit"]}

INVALID = {
    "v": 1,
    "checks": [{"name": "unit", "run": "python3 -m unittest",
                "root": "worktree"}],
    "gates": {"acceptance": {"checks": ["nothing-declares-this"]}},
}

UNSUPPORTED = {
    "v": 1,
    "requires": ["filesystem-sandbox"],
    "checks": [{"name": "unit", "run": "python3 -m unittest",
                "root": "worktree"}],
    "profiles": {"work": {"verify": ["unit"]}},
}

BROKEN_JSON = "{ this is not json"


def child_env(extra: dict | None = None) -> dict:
    """The process environment with every board variable removed.

    `config` folds the real environment over `manager/local/.env`, so a
    developer's own `BOARD_APP_ROOT` would decide what these tests
    resolve. Removing them is what the task asks for, and it is the only
    way a fixture is the thing under test.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
    env.update(extra or {})
    return env


PRELUDE = (
    "import json, os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pathlib import Path\n"
    "import config\n"
    # the history and any other runtime write go to the fixture's own
    # directory, so a test never writes into the checkout it is testing
    "config.STATE = Path(os.environ['TEST_STATE'])\n"
    "config.EVIDENCE_DIR = config.STATE / 'evidence'\n"
    "import profiles, settings, httpd\n"
)


def run(script: str, *, doc=LEGACY, raw: str | None = None,
        extra: dict | None = None) -> object:
    """Run `script` against a fixture document in a fresh interpreter and
    return whatever it put in `R`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        env = {"TEST_STATE": str(root / "state")}
        if raw is not None:
            path = root / "profiles.json"
            path.write_text(raw, encoding="utf-8")
            env["BOARD_AGENT_PROFILES"] = str(path)
        elif doc is not None:
            path = root / "profiles.json"
            path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            env["BOARD_AGENT_PROFILES"] = str(path)
        else:
            # `legacy` is a board with no document, and bench reads
            # BOARD_AGENT_PROFILES *as named* — a path that is not there is
            # a `broken` document, not an absent one. So the setting goes
            # empty, which falls back to manager/local/profiles.json; the
            # module guard below refuses to run if this checkout has one.
            env["BOARD_AGENT_PROFILES"] = ""
        env.update(extra or {})
        out = subprocess.run(
            [sys.executable, "-c", PRELUDE + script + "\nprint(json.dumps(R))",
             str(CORE)],
            env=child_env(env), text=True, capture_output=True, cwd=str(REPO))
    if out.returncode != 0:
        raise AssertionError(f"probe failed: {out.stderr[-3000:]}")
    return json.loads(out.stdout.splitlines()[-1])


def page(**kwargs) -> dict:
    return run("R = settings.page()", **kwargs)


def setUpModule():
    """The legacy fixture is "no document anywhere", and the only place
    bench looks when `BOARD_AGENT_PROFILES` is empty is
    `manager/local/profiles.json`. An operator who installed one into this
    checkout would make the legacy probes resolve a real policy, so say so
    rather than fail somewhere confusing."""
    installed = CORE.parent / "local" / "profiles.json"
    if installed.is_file():
        raise unittest.SkipTest(
            f"{installed} exists, so there is no legacy fixture to resolve "
            f"here — move it aside to run these")


class EveryValueComesFromTheResolver(unittest.TestCase):
    """The one defect this feature could introduce is a second reader of
    the same document. These assert equality with the authority rather
    than equality with a literal."""

    def test_the_resolved_profiles_equal_the_readiness_reading(self):
        """`doctor.intent_reading` is the convention both the inspector and
        this page take: a board-level reading has no card, so it resolves
        against the path a worktree would be at rather than leaving the
        word out and refusing every grant that runs in one."""
        found = run(
            "import doctor\n"
            "page = settings.page()\n"
            "R = {i: [page['access']['intents'][i]['commands'],\n"
            "         [g['name'] for g in\n"
            "          doctor.intent_reading(i, None)['commands']]]\n"
            "     for i in profiles.INTENTS}", doc=FULL)
        for intent, (shown, resolved) in found.items():
            self.assertEqual([g["name"] for g in shown], resolved,
                             f"{intent}'s grants are not the resolver's")
        self.assertIn("bench-verify",
                      [g["name"] for g in found["work"][0]],
                      "a configured check reaches no grant to ask for it")

    def test_the_gate_policy_equals_profiles_gate_policy(self):
        same = run("page = settings.page()\n"
                   "R = page['acceptance']['policy'] == "
                   "profiles.gate_policy_public(profiles.gate_policy())",
                   doc=FULL)
        self.assertTrue(same, "the page shows a gate policy of its own")

    def test_the_checks_equal_the_documents_resolved_checks(self):
        found = run(
            "page = settings.page()\n"
            "steps, _ = profiles.document_steps(profiles.document())\n"
            "known, _ = profiles.document_checks(profiles.document(), steps)\n"
            "R = [[c['name'] for c in page['verification']['checks']],\n"
            "     sorted(known),\n"
            "     [c['checkId'] for c in page['verification']['checks']],\n"
            "     [known[n]['checkId'] for n in sorted(known)]]", doc=FULL)
        self.assertEqual(found[0], found[1])
        self.assertEqual(found[2], found[3],
                         "the page shows a check identity it computed itself")

    def test_a_legacy_board_is_reported_as_legacy_not_as_configured(self):
        found = page(doc=LEGACY)
        self.assertTrue(found["overview"]["legacy"])
        self.assertEqual("legacy", found["overview"]["policy"]["kind"])
        for intent, shown in found["access"]["intents"].items():
            self.assertEqual("not-configured", shown["state"], intent)
            self.assertEqual("legacy", shown["summary"]["sourceKind"], intent)

    def test_a_partial_document_falls_back_per_intent_and_says_so(self):
        found = page(doc=PARTIAL)
        work = found["access"]["intents"]["work"]
        review = found["access"]["intents"]["review"]
        self.assertEqual(["unit"], work["summary"]["checks"])
        self.assertEqual([], review["summary"]["checks"],
                         "review was given a check the document never gave it")
        self.assertTrue(any("declares nothing for 'review'" in note
                            for note in review["notes"]),
                        f"the fallback is not stated: {review['notes']}")


class NotConfiguredIsNeverAPass(unittest.TestCase):
    def test_ungated_is_its_own_word_and_never_allowed(self):
        found = page(doc=LEGACY)
        self.assertEqual("not-configured", found["acceptance"]["state"])
        for scope, entry in found["acceptance"]["policy"]["scopes"].items():
            self.assertFalse(entry["gated"], scope)
        self.assertIn("deliberately not `allowed`",
                      found["acceptance"]["ungatedIsNotAPass"])
        # and `ungated` is in the decision vocabulary with that meaning
        self.assertIn("nothing is proved",
                      found["acceptance"]["decisions"]["ungated"])
        self.assertNotIn("verified",
                         [row["state"] for row in found["overview"]["rows"]])

    def test_every_state_word_is_defined_and_used_as_a_word(self):
        found = page(doc=FULL)
        for row in found["overview"]["rows"]:
            self.assertIn(row["state"], found["states"], row["id"])
        self.assertIn("not a fault", found["states"]["not-configured"])
        self.assertIn("not", found["states"]["unsupported"])

    def test_a_registered_check_is_not_a_required_one(self):
        found = page(doc=PARTIAL)
        unit = found["verification"]["checks"][0]
        self.assertEqual([], unit["requiredBy"],
                         "a registered check is shown as required")
        self.assertIn("makes nothing mandatory",
                      found["verification"]["registrationIsNotRequirement"])
        # and the fully configured board does require it, by name
        full = page(doc=FULL)
        by_name = {c["name"]: c for c in full["verification"]["checks"]}
        self.assertEqual(["acceptance", "integration", "member"],
                         by_name["unit"]["requiredBy"])
        self.assertEqual([], by_name["proposed"]["requiredBy"])

    def test_installation_wide_configuration_does_not_claim_card_evidence(self):
        found = page(doc=FULL)
        self.assertIn("per card and per commit",
                      found["verification"]["evidenceScope"])
        for check in found["verification"]["checks"]:
            self.assertEqual("configured-untested", check["state"],
                             "a check with no evidence reads as verified")


class AnInvalidDocumentIsRefusedNotDefaulted(unittest.TestCase):
    def test_a_gate_requiring_an_undeclared_check_is_refused_by_name(self):
        found = page(doc=INVALID)
        self.assertEqual("invalid", found["acceptance"]["state"])
        self.assertTrue(found["acceptance"]["policy"]["blockers"])
        codes = [f["code"] for f in found["acceptance"]["fields"]]
        self.assertIn("gate-check-undeclared", codes, codes)
        fields = {f["code"]: f["field"] for f in found["acceptance"]["fields"]}
        self.assertEqual("gates", fields["gate-check-undeclared"])

    def test_a_document_that_does_not_parse_is_shown_as_a_refusal(self):
        found = page(raw=BROKEN_JSON)
        self.assertEqual("broken", found["overview"]["policy"]["kind"])
        self.assertIn("not valid JSON", found["overview"]["policy"]["problem"])
        # and it grants nothing rather than falling back to the legacy set
        for intent, shown in found["access"]["intents"].items():
            self.assertEqual("invalid", shown["state"], intent)
            self.assertEqual([], shown["commands"], intent)
            self.assertTrue(shown["blockers"], intent)
        self.assertTrue(found["overview"]["worst"] == "invalid")

    def test_the_broken_document_is_the_first_recommendation(self):
        found = page(raw=BROKEN_JSON)
        first = found["recommendations"][0]
        self.assertEqual("high", first["priority"])
        self.assertEqual("policy-broken", first["id"])


class AnUnsupportedGuaranteeIsNotAGuarantee(unittest.TestCase):
    def test_requiring_a_filesystem_sandbox_refuses_the_launch(self):
        found = page(doc=UNSUPPORTED)
        work = found["access"]["intents"]["work"]
        self.assertTrue(work["blockers"])
        codes = [f["code"] for f in work["fields"]]
        self.assertIn("requirement-unsupported", codes, codes)

    def test_isolation_reads_unsupported_on_every_shipped_adapter(self):
        found = page(doc=FULL)
        row = [r for r in found["overview"]["rows"] if r["id"] == "isolation"][0]
        self.assertEqual("unsupported", row["state"])
        self.assertIn("no operating-system boundary is enforced", row["detail"])

    def test_the_capability_table_names_who_answers_for_each(self):
        found = page(doc=FULL)
        table = found["access"]["capabilities"]
        self.assertIn("filesystem-sandbox", table)
        self.assertEqual("pending", table["filesystem-sandbox"]["enforcement"])
        for name, cap in table.items():
            self.assertIn(cap["enforcement"],
                          ["enforced", "prefix-allowlist", "prompt-only",
                           "pending"], name)
            self.assertTrue(cap["by"], name)


class Recommendations(unittest.TestCase):
    def test_a_board_with_no_check_is_told_to_register_one_and_what_to_supply(self):
        found = page(doc=LEGACY)
        by_id = {r["id"]: r for r in found["recommendations"]}
        self.assertIn("register-a-check", by_id)
        rec = by_id["register-a-check"]
        self.assertEqual("high", rec["priority"])
        self.assertIn("exits non-zero", rec["supply"])
        self.assertTrue(rec["prerequisites"])
        self.assertEqual("verification", rec["section"])
        self.assertIn("changes nothing", rec["inert"])

    def test_registered_but_ungated_is_told_that_registration_is_not_enough(self):
        by_id = {r["id"]: r for r in page(doc=PARTIAL)["recommendations"]}
        self.assertIn("gate-a-check", by_id)
        self.assertNotIn("register-a-check", by_id)

    def test_a_member_gate_without_an_integration_gate_is_recommended_one(self):
        doc = json.loads(json.dumps(FULL))
        doc["gates"].pop("integration")
        by_id = {r["id"]: r for r in page(doc=doc)["recommendations"]}
        self.assertIn("gate-the-integration-head", by_id)
        self.assertIn("different commit",
                      by_id["gate-the-integration-head"]["why"])

    def test_a_fully_gated_board_is_not_told_to_gate_anything(self):
        by_id = {r["id"]: r for r in page(doc=FULL)["recommendations"]}
        self.assertNotIn("gate-a-check", by_id)
        self.assertNotIn("register-a-check", by_id)
        self.assertNotIn("gate-the-integration-head", by_id)

    def test_recommendations_are_ordered_worst_first(self):
        order = {"high": 0, "medium": 1, "low": 2}
        found = [r["priority"] for r in page(doc=INVALID)["recommendations"]]
        self.assertEqual(sorted(found, key=lambda p: order[p]), found)


class BenchChecksCiAndReviewStayThree(unittest.TestCase):
    def test_a_green_ci_job_is_never_named_bench_evidence(self):
        found = page(doc=FULL)["ci"]
        self.assertFalse(found["kinds"]["ci"]["gateable"])
        self.assertTrue(found["kinds"]["bench"]["gateable"])
        joined = " ".join(found["limits"])
        self.assertIn("never converted into evidence", joined)
        self.assertIn("cannot bind an external run", found["ci"]["why"])

    def test_no_deployment_is_implied(self):
        found = page(doc=FULL)["ci"]
        self.assertIn("Nothing here deploys", found["noDeployment"])
        self.assertIn("packaging and deployment are separate",
                      found["noDeployment"])

    def test_the_unsupported_mapping_is_named_as_follow_up(self):
        found = page(doc=FULL)["ci"]
        self.assertTrue(found["followUp"])
        self.assertTrue(any("declared mapping" in f for f in found["followUp"]))

    def test_the_three_kinds_name_the_authority_each_comes_from(self):
        found = page(doc=FULL)["ci"]["kinds"]
        self.assertIn("verify.py", found["bench"]["authority"])
        self.assertIn("github.py", found["ci"]["authority"])
        self.assertIn("gates.py", found["review"]["authority"])


class ReviewModesAreOnlyOfferedWhereTheyCanBeEnforced(unittest.TestCase):
    def test_a_board_without_gh_is_told_github_review_cannot_be_read(self):
        found = run("page = settings.page()\n"
                    "R = [page['acceptance']['reviewModes'],\n"
                    "     config.gh_available()]", doc=FULL,
                    extra={"BOARD_GH_BIN": "definitely-not-a-binary"})
        modes, gh = found
        self.assertFalse(gh)
        by_name = {m["name"]: m for m in modes}
        self.assertFalse(by_name["github"]["available"])
        self.assertFalse(by_name["separate-reviewer"]["available"])
        self.assertTrue(by_name["bench"]["available"])
        self.assertIn("no usable `gh`", by_name["github"]["unavailableWhy"])

    def test_the_same_account_limitation_is_stated(self):
        found = page(doc=FULL)["acceptance"]
        self.assertIn("does not accept an approval", found["sameAccount"])
        self.assertIn("never switches accounts", found["sameAccount"])

    def test_the_two_review_records_are_kept_apart(self):
        found = page(doc=FULL)["acceptance"]
        self.assertIn("bench's own verdict",
                      [m["record"] for m in found["reviewModes"]])
        self.assertIn("GitHub's formal review state",
                      [m["record"] for m in found["reviewModes"]])
        self.assertIn("not GitHub's branch protection",
                      found["notBranchProtection"])


class InstallationAndSources(unittest.TestCase):
    def test_the_build_and_the_revision_are_reported_and_unknown_is_unknown(self):
        found = page(doc=FULL)["installation"]
        self.assertEqual((CORE / "VERSION").read_text().strip(),
                         found["version"])
        # bench's own checkout is a git repository, so the revision is known
        self.assertTrue(found["revisionKnown"])
        self.assertEqual(40, len(found["revision"]))

    def test_the_two_git_identities_are_separate(self):
        found = page(doc=FULL)["installation"]["git"]
        self.assertIn("pull request", found["application"]["what"])
        self.assertIn("ticket", found["workState"]["what"])
        self.assertIn("base", found["application"])
        self.assertIn("base", found["workState"])

    def test_the_separate_operations_are_named(self):
        found = page(doc=FULL)["installation"]["separateOperations"]
        what = " ".join(o["what"] for o in found)
        for word in ("merging", "updating", "release", "deploying"):
            self.assertIn(word, what)

    def test_local_and_shared_state_are_drawn_apart(self):
        found = page(doc=FULL)["installation"]
        local = " ".join(x["what"] for x in found["localOnly"])
        shared = " ".join(x["what"] for x in found["shared"])
        self.assertIn(".env", local)
        self.assertIn("state/", local)
        self.assertIn("tasks/", shared)
        self.assertNotIn("tasks/", local)


class ProvenanceIsObservedNotGuessed(unittest.TestCase):
    def test_a_setting_in_the_environment_is_locked_and_says_where(self):
        rows = run("R = settings.catalog()", doc=FULL,
                   extra={"BOARD_APP_BASE": "trunk"})
        row = [r for r in rows if r["key"] == "BOARD_APP_BASE"][0]
        self.assertEqual("environment", row["source"])
        self.assertTrue(row["locked"])
        self.assertEqual("trunk", row["value"])
        self.assertIn("Change it where it is exported", row["how"])

    def test_an_unset_setting_reports_the_documented_default(self):
        rows = run("R = settings.catalog()", doc=FULL)
        row = [r for r in rows if r["key"] == "BOARD_APP_BASE"][0]
        self.assertEqual("default", row["source"])
        self.assertEqual("main", row["value"])
        self.assertFalse(row["locked"])

    def test_no_row_is_editable_from_the_page_and_each_says_why(self):
        rows = run("R = [settings.catalog(), "
                   "settings.page()['editing']['envIsReadOnly']]", doc=FULL)
        catalog, why = rows
        for row in catalog:
            self.assertFalse(row["editableHere"], row["key"])
            self.assertTrue(row["how"], row["key"])
            self.assertIn(row["appliesAt"], ("restart", "fresh"), row["key"])
        self.assertIn("beats it", why)

    def test_the_defaults_come_from_the_documented_example(self):
        """The example file is the reference the docs render. Reading it is
        what keeps the catalogue from becoming a second, diverging copy."""
        rows = run("R = settings.catalog()", doc=FULL)
        text = (CORE / ".env.example").read_text(encoding="utf-8")
        for row in rows:
            if not row["default"]:
                continue
            self.assertIn(f"{row['key']}={row['default']}", text, row["key"])


class DiagnosticsDistinguishInspectionFromExecution(unittest.TestCase):
    def test_the_coverage_table_draws_every_stage_as_not_asked(self):
        found = page(doc=FULL)["diagnostics"]
        self.assertEqual(4, len(found["coverage"]))
        for row in found["coverage"]:
            self.assertFalse(row["asked"], row["mode"])
            self.assertIn("not asked", row["why"])
            self.assertTrue(row["proves"])
            self.assertTrue(row["doesNotProve"])

    def test_the_four_outcome_words_keep_deferred_apart_from_passed(self):
        found = page(doc=FULL)["diagnostics"]
        self.assertEqual(sorted(found["outcomes"]),
                         ["deferred", "failed", "passed", "skipped"])
        self.assertIn("not 'passed'", found["outcomes"]["deferred"])

    def test_the_real_agent_stage_has_no_control_on_this_page(self):
        found = page(doc=FULL)["diagnostics"]["modes"]
        self.assertFalse(found["real-agent"]["uiPath"]["available"])
        self.assertIn("--approve-real-agent",
                      found["real-agent"]["uiPath"]["how"])
        self.assertIn("spends tokens",
                      found["real-agent"]["uiPath"]["approval"])
        # and the three cheap stages do have a supported path
        for mode in ("inspect", "conformance", "runtime"):
            self.assertTrue(found[mode]["uiPath"]["available"], mode)

    def test_read_only_inspection_says_what_it_cannot_do(self):
        found = page(doc=FULL)["diagnostics"]
        joined = " ".join(found["cannot"])
        for word in ("install a dependency", "spend a model token",
                     "repair a setting", "activate a gate"):
            self.assertIn(word, joined)
        self.assertIn("No project program runs", found["inspectIs"])

    def test_every_diagnostic_code_comes_from_profiles(self):
        same = run("R = sorted(settings.page()['diagnostics']"
                   "['diagnosticCodes']) == sorted(profiles.DIAGNOSTICS)",
                   doc=FULL)
        self.assertTrue(same, "the page has a diagnostic table of its own")

    def test_every_field_pointer_names_a_real_diagnostic(self):
        unknown = run("R = sorted(set(settings.FIELD_OF) - "
                      "set(profiles.DIAGNOSTICS))", doc=FULL)
        self.assertEqual([], unknown,
                         "FIELD_OF points at a code profiles does not name")


class NothingRunsWhenThePageIsRead(unittest.TestCase):
    """The acceptance criterion with teeth: no model and no project command
    during page load or Check configuration.

    Asserted by recording what was actually launched, rather than by
    reading the code and hoping. Only git and gh may be run — both are
    reads bench already makes to answer where things are — and nothing
    that resembles the fixture's own check command may appear at all.
    """

    SPY = (
        "launched = []\n"
        "import subprocess as _sp\n"
        "_run, _popen = _sp.run, _sp.Popen\n"
        "def run(args, *a, **k):\n"
        "    launched.append(list(args) if not isinstance(args, str) else [args])\n"
        "    return _run(args, *a, **k)\n"
        "def popen(args, *a, **k):\n"
        "    launched.append(list(args) if not isinstance(args, str) else [args])\n"
        "    return _popen(args, *a, **k)\n"
        "_sp.run, _sp.Popen = run, popen\n"
    )

    def test_reading_the_page_launches_nothing_but_git_and_gh(self):
        found = run(self.SPY + "settings.page()\n"
                    "R = sorted({os.path.basename(a[0]) for a in launched})",
                    doc=FULL)
        self.assertTrue(set(found) <= {"git", "gh"},
                        f"reading the settings launched {found}")

    def test_reading_the_page_never_runs_the_projects_own_check(self):
        found = run(self.SPY + "settings.page()\n"
                    "R = [a for a in launched if 'unittest' in ' '.join(a)]",
                    doc=FULL)
        self.assertEqual([], found,
                         "reading the settings ran the project's test command")

    def test_check_configuration_launches_nothing_but_git_and_gh(self):
        """The read-only action, through the doctor's own GET path."""
        found = run(self.SPY + "import doctor\n"
                    "doctor.report([])\n"
                    "R = sorted({os.path.basename(a[0]) for a in launched})",
                    doc=FULL)
        self.assertTrue(set(found) <= {"git", "gh"},
                        f"check configuration launched {found}")

    def test_check_configuration_never_runs_a_check_or_an_adapter(self):
        found = run(self.SPY + "import doctor\n"
                    "doctor.report([])\n"
                    "R = [a for a in launched if 'unittest' in ' '.join(a)\n"
                    "     or 'claude' in ' '.join(a) or 'opencode' in ' '.join(a)]",
                    doc=FULL)
        self.assertEqual([], found,
                         "check configuration ran a check or an adapter")

    def test_reading_the_page_writes_nothing(self):
        found = run("before = sorted(str(p) for p in "
                    "Path(os.environ['TEST_STATE']).rglob('*'))\n"
                    "doc = Path(os.environ['BOARD_AGENT_PROFILES'])\n"
                    "was = doc.read_bytes()\n"
                    "settings.page()\n"
                    "R = [before == sorted(str(p) for p in "
                    "Path(os.environ['TEST_STATE']).rglob('*')),\n"
                    "     was == doc.read_bytes()]", doc=FULL)
        self.assertEqual([True, True], found,
                         "reading the settings wrote something")


class TheStatePayloadStaysCheap(unittest.TestCase):
    def test_the_board_summary_is_one_document_read(self):
        found = run("R = settings.board_summary()", doc=FULL)
        self.assertEqual({"configured", "problem", "invalid", "checks",
                          "gated"}, set(found))
        self.assertTrue(found["configured"])
        self.assertEqual(2, found["checks"])
        self.assertEqual(["acceptance", "integration", "member"],
                         found["gated"])

    def test_a_legacy_board_summary_says_nothing_to_shout_about(self):
        found = run("R = settings.board_summary()", doc=LEGACY)
        self.assertFalse(found["configured"])
        self.assertFalse(found["invalid"])
        self.assertEqual(0, found["checks"])
        self.assertEqual([], found["gated"])

    def test_a_broken_document_summary_is_invalid(self):
        found = run("R = settings.board_summary()", raw=BROKEN_JSON)
        self.assertTrue(found["invalid"])
        self.assertTrue(found["problem"])

    def test_the_state_payload_carries_it(self):
        found = run("R = httpd.state_payload()['settings']", doc=FULL)
        self.assertEqual(2, found["checks"])
        # cheap means the whole page is not in the state payload
        keys = run("R = sorted(httpd.state_payload())", doc=FULL)
        self.assertNotIn("recommendations", keys)


if __name__ == "__main__":
    unittest.main()
