"""Card 97 — a result is invalidated only by what it depends on.

Design #94 §4.1, §14.3 and §15 step 3: every new evidence record carries a
result identity (`resultId`) built from its source, the check's substance,
its coverage, its programs, its preparation, its declared inputs, its root
class, its declared environment and the runner — and, apart from it, the
provenance of how the run came about. Freshness compares the identity's
dimensions; the policy revision is history, never a reason. A record
written before identities existed keeps today's rules, byte for byte.

Every case runs against a disposable tool install, work root and
application checkout (`GateCase`) with stub check executables. Nothing
here reads or writes a live board, SAFER, a remote or operator policy.

- E1 / R1 — `UnrelatedPolicyIsProvenance`.
- E2, E3 — `EachDimensionHasItsReason` (one subtest per mutation) and
  `DeclaredInputs`.
- E14 — `LegacyRecordsKeepTheirRules`.
- E15 — `AuthorisationStaysSeparate`.
- validation — `ChecksDeclareInputsAndEnv`.
- secrets and keys — `DeclaredEnvironment`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from tests.test_integration_gates import (CARD, FAILS, PASSES,  # noqa: E402
                                          GateCase, git)

import config  # noqa: E402  (on sys.path once the case module is imported)
import gates  # noqa: E402
import profiles  # noqa: E402
import settings  # noqa: E402
import verify  # noqa: E402

SECRET = "postgres://bench:hunter2-very-secret@db.example/test"

# A check that proves what it was handed: it exits 0 only when the pinned
# copy of `brief` is there and says what the test expects.
READS_INPUT = """#!{python}
import os, sys
where = os.environ.get("BENCH_VERIFY_INPUTS")
path = os.path.join(where or "", "brief.md")
if not where or not os.path.isfile(path):
    print("no brief was handed over")
    sys.exit(1)
print("brief:", open(path).read().strip())
sys.exit(0 if open(path).read() == "the approved brief\\n" else 1)
"""


class IdentityCase(GateCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.commit_in(self.wt)
        self.brief = self.work / "reference" / "brief.md"
        self.brief.write_text("the approved brief\n", encoding="utf-8")
        self.addCleanup(setattr, config, "_ENV", config._ENV)
        config._ENV = dict(config._ENV)

    def document(self, **check) -> dict:
        body = check.pop("body", PASSES)
        declared = {"name": "unit", "run": str(self.executable("unit", body)),
                    "root": "worktree", **check}
        return {"v": 1, "checks": [declared],
                "context": [{"name": "brief",
                             "path": "work:reference/brief.md"}],
                "prepare": [{"name": "deps",
                             "run": str(self.executable("deps", PASSES))}],
                "profiles": {"work": {"verify": ["unit"]},
                             "review": {"verify": ["unit"]}},
                "gates": {"acceptance": {"checks": ["unit"]}}}

    def run_check(self, document: dict, *, scope: str | None = None,
                  agent: str = "run-1") -> dict:
        self.declares(document)
        self.manifest(agent=agent)
        found = self.ask(agent=agent, scope=scope)
        verify.forget_cached()
        gates.forget_cached()
        return found

    def reading(self, found: dict) -> dict:
        verify.forget_cached()
        gates.forget_cached()
        return verify.outcome_now(verify.read(found["id"]))


# ── what a new record carries ──────────────────────────────────────────


class ANewRecordCarriesItsIdentity(IdentityCase):

    def test_every_dimension_and_the_provenance_are_recorded(self):
        found = self.run_check(self.document())
        self.assertEqual(found["outcome"], "passed")
        self.assertTrue(found["resultId"].startswith("result:"))
        identity = found["identity"]
        for dimension in verify.IDENTITY_DIMENSIONS:
            self.assertIn(dimension, identity)
        self.assertEqual(identity["rootClass"], "worktree")
        self.assertEqual(identity["runner"], verify.RUNNER_REVISION)
        self.assertEqual(found["resultId"], verify.result_id(identity))
        self.assertEqual(found["checkSubstance"], identity["check"])
        provenance = found["provenance"]
        self.assertEqual(provenance["policyRevision"], found["policyRevision"])
        self.assertEqual(provenance["requestId"], found["requestId"])
        self.assertEqual(provenance["agent"], "run-1")

    def test_the_name_is_not_part_of_the_substance(self):
        """Two checks running one definition have one substance; the
        requester, the model and the temporary paths are in no dimension."""
        document = self.document()
        twin = dict(document["checks"][0], name="unit-review")
        document["checks"].append(twin)
        document["profiles"]["work"]["verify"] = ["unit", "unit-review"]
        self.declares(document)
        self.manifest()
        first = self.ask("unit")
        second = self.ask("unit-review")
        self.assertEqual(first["checkSubstance"], second["checkSubstance"])
        for dimension in ("check", "coverage", "program", "prepare", "inputs",
                          "rootClass", "env", "runner", "source"):
            self.assertEqual(first["identity"][dimension],
                             second["identity"][dimension], dimension)
        self.assertNotEqual(first["env"]["tmp"], second["env"]["tmp"])
        # and the name still keeps them apart at the gate (R3, unchanged)
        self.assertNotEqual(first["checkId"], second["checkId"])

    def test_a_fresh_source_checkout_is_said_to_be_fresh(self):
        found = self.run_check(self.document(root="source"))
        self.assertEqual(found["outcome"], "passed")
        self.assertEqual(found["identity"]["rootClass"], "fresh")

    def test_a_source_checkout_holding_ignored_state_is_not_fresh(self):
        """Bench reuses a `source` checkout of the same commit. One that
        already holds ignored state is `reused`, not `fresh`."""
        (self.wt / ".gitignore").write_text("cache/\n", encoding="utf-8")
        self.commit_in(self.wt, "ignore the cache\n")
        sha = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        root = config.VERIFY_DIR / "src" / f"{CARD[:-3]}-{sha[:12]}"
        root.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "--detach", str(root), sha)
        (root / "cache").mkdir()
        (root / "cache" / "built").write_text("stale build\n", encoding="utf-8")
        found = self.run_check(self.document(root="source"))
        self.assertEqual(found["identity"]["rootClass"], "reused")


# ── E1 / R1 ────────────────────────────────────────────────────────────


class UnrelatedPolicyIsProvenance(IdentityCase):

    def test_unrelated_next_card_context_preserves_a_same_head_pass(self):
        """R1, a discovered regression for NEW evidence: approved context
        for the next card is added, nothing the check declares moved."""
        document = self.document()
        found = self.run_check(document)
        self.assertEqual(found["outcome"], "passed")
        mock = self.work / "reference" / "card37-mockup.md"
        mock.write_text("a mockup for the next card\n", encoding="utf-8")
        document["context"].append({"name": "card37-mockup",
                                    "path": "work:reference/card37-mockup.md",
                                    "required": False})
        self.declares(document)
        reading = self.reading(found)
        self.assertEqual(reading["outcome"], "passed")
        self.assertEqual(reading["freshness"]["reasons"], [])
        self.assertEqual(reading["freshness"]["rules"], "identity")
        provenance = reading["freshness"]["provenance"]
        self.assertTrue(provenance["policyChanged"])
        self.assertEqual(provenance["policyRevision"], found["policyRevision"])
        self.assertNotEqual(provenance["policyNow"], found["policyRevision"])
        self.assertIn("provenance, not a reason", provenance["note"])
        # the record itself is unchanged: provenance is what it was
        self.assertEqual(verify.read(found["id"])["policyRevision"],
                         found["policyRevision"])
        self.assertEqual(self.decide()["decision"], "allowed")

    def test_editing_the_undeclared_context_file_changes_nothing(self):
        document = self.document()
        found = self.run_check(document)
        self.brief.write_text("rewritten, but unit never declared it\n",
                              encoding="utf-8")
        self.assertEqual(self.reading(found)["outcome"], "passed")

    def test_a_new_grant_or_model_choice_changes_nothing(self):
        document = self.document()
        found = self.run_check(document)
        document["profiles"]["work"]["commands"] = [
            {"name": "lint", "run": "python3 -m pyflakes"}]
        self.declares(document)
        self.assertEqual(self.reading(found)["outcome"], "passed")


# ── E2, E3 ─────────────────────────────────────────────────────────────


class EachDimensionHasItsReason(IdentityCase):
    """One subtest per mutation: a fresh pass, one change, one reason."""

    def mutations(self):
        def dirty(doc):
            (self.wt / "untracked.txt").write_text("new\n", encoding="utf-8")

        def commit(doc):
            self.commit_in(self.wt, "a newer commit\n")

        def argv(doc):
            doc["checks"][0]["run"] += " --different"

        def timeout(doc):
            doc["checks"][0]["timeout"] = 30

        def scope_args(doc):
            doc["checks"][0]["scopes"]["fast"] = ["-k", "slow"]

        def scope_gone(doc):
            doc["checks"][0]["scopes"] = {"other": ["-x"]}

        def executable(doc):
            path = self.bin / "unit"
            path.write_text(path.read_text() + "# replaced\n", encoding="utf-8")

        def interpreter(doc):
            with (self.bin / "shell").open("ab") as handle:
                handle.write(b"\0replaced")

        def prepare_argv(doc):
            doc["prepare"][0]["run"] += " --upgrade"

        def prepare_network(doc):
            doc["prepare"][0]["network"] = True

        def prepare_required(doc):
            doc["prepare"][0]["required"] = False

        def prepare_program(doc):
            path = self.bin / "deps"
            path.write_text(path.read_text() + "# replaced\n", encoding="utf-8")

        def declared_input(doc):
            self.brief.write_text("a different brief\n", encoding="utf-8")

        def input_declaration(doc):
            doc["checks"][0]["inputs"] = []

        def env_value(doc):
            config._ENV["BENCH_TEST_DB"] = "elsewhere"

        def env_names(doc):
            config._ENV["BENCH_TEST_SOMETHING_NEW"] = "1"

        def runner(doc):
            self.addCleanup(setattr, verify, "RUNNER_REVISION",
                            verify.RUNNER_REVISION)
            verify.RUNNER_REVISION = "f" * 64

        return [
            ("dirty source", dirty, "source-changed", "changed since it ran"),
            ("new commit", commit, "source-changed", "moved on"),
            ("argv", argv, "check-changed", "'unit'"),
            ("timeout", timeout, "check-changed", "'unit'"),
            ("scope arguments", scope_args, "scope-changed", "'fast'"),
            ("scope removed", scope_gone, "scope-changed", "no longer declared"),
            ("executable", executable, "program-changed", "changed since it ran"),
            ("interpreter", interpreter, "program-changed", "interpreter"),
            ("preparation argv", prepare_argv, "prepare-changed", "'deps'"),
            ("preparation network", prepare_network, "prepare-changed", "'deps'"),
            ("preparation required", prepare_required, "prepare-changed", "'deps'"),
            ("preparation program", prepare_program, "program-changed", "'deps'"),
            ("declared input", declared_input, "input-changed", "'brief'"),
            ("input declaration", input_declaration, "check-changed", "'unit'"),
            ("declared env value", env_value, "env-changed", "BENCH_TEST_DB"),
            ("passed env names", env_names, "env-changed",
             "BENCH_TEST_SOMETHING_NEW"),
            ("runner", runner, "runner-changed", "Bench was updated"),
        ]

    def baseline(self) -> dict:
        shell = self.bin / "shell"
        if not shell.exists():
            shutil.copy("/bin/sh", shell)
        script = self.bin / "unit"
        document = self.document(scopes={"fast": ["-k", "fast"]},
                                 prepare=["deps"], inputs=["brief"],
                                 env=["BENCH_TEST_DB"])
        # `unit` is a shell script whose #! names a copy of /bin/sh, so the
        # interpreter is a file this test owns and can replace
        script.write_text(f"#!{shell}\necho ok\n", encoding="utf-8")
        return document

    def test_each_mutation_gives_its_specific_reason(self):
        for label, mutate, reason, named in self.mutations():
            with self.subTest(label):
                git(self.wt, "reset", "-q", "--hard")
                git(self.wt, "clean", "-qfd")
                config._ENV["BENCH_TEST_DB"] = "the test database"
                config._ENV.pop("BENCH_TEST_SOMETHING_NEW", None)
                self.brief.write_text("the approved brief\n", encoding="utf-8")
                document = self.baseline()
                found = self.run_check(document, scope="fast")
                self.assertEqual(found["outcome"], "passed", found.get("why"))
                self.assertEqual(self.reading(found)["outcome"], "passed")
                mutate(document)
                self.declares(document)
                reading = self.reading(found)
                self.assertEqual(reading["outcome"], "stale", label)
                self.assertIn(reason, reading["freshness"]["reasons"], label)
                self.assertNotIn("policy-changed",
                                 reading["freshness"]["reasons"])
                why = reading["freshness"]["changed"][
                    reading["freshness"]["reasons"].index(reason)]
                self.assertIn(named, why, label)
                if reason not in verify.SOURCE_REASONS:
                    self.assertIn(verify.STALE_REMEDIES[reason], why)

    def test_an_unreadable_identity_fails_closed_with_a_remedy(self):
        found = self.run_check(self.document())
        path = verify.path_of(found["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["identity"]["program"] = "prog:tampered"
        path.write_text(json.dumps(data), encoding="utf-8")
        reading = self.reading(found)
        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "identity-unknown")
        self.assertIn("Run the check again", reading["why"])
        del data["identity"]["env"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.reading(found)["reason"], "identity-unknown")

    def test_a_check_no_longer_declared_is_check_changed(self):
        document = self.document()
        found = self.run_check(document)
        document["checks"][0]["name"] = "renamed"
        document["profiles"] = {"work": {"verify": ["renamed"]}}
        document["gates"] = {}
        self.declares(document)
        reading = self.reading(found)
        self.assertEqual(reading["reason"], "check-changed")
        self.assertIn("no longer declared", reading["why"])

    def test_at_the_gate_an_outdated_failure_is_not_todays_failure(self):
        """A failure at H under a command since replaced is outdated: it
        decides nothing, and the reason is the dimension that moved."""
        document = self.document(body=FAILS)
        found = self.run_check(document)
        self.assertEqual(found["outcome"], "failed")
        self.assertEqual(self.decide()
                         ["unmet"][0]["reason"], "failed")
        document["checks"][0]["run"] += " --fixed"
        self.declares(document)
        verify.forget_cached()
        gates.forget_cached()
        row = self.decide()["unmet"][0]
        self.assertEqual(row["reason"], "stale")
        self.assertEqual(row["staleReason"], "check-changed")
        self.assertEqual(row["remedy"], verify.STALE_REMEDIES["check-changed"])

    def test_at_the_gate_a_stale_pass_names_its_dimension(self):
        document = self.document()
        self.run_check(document)
        self.brief.write_text("unrelated: unit does not declare it\n",
                              encoding="utf-8")
        document["checks"][0]["run"] += " --different"
        self.declares(document)
        verify.forget_cached()
        gates.forget_cached()
        row = self.decide()["unmet"][0]
        self.assertEqual(row["staleReason"], "check-changed")
        self.assertIn("'unit'", row["why"])


# ── declared inputs: validated, delivered, bound ───────────────────────


class DeclaredInputs(IdentityCase):

    def test_a_declared_input_is_delivered_as_hashed(self):
        found = self.run_check(self.document(body=READS_INPUT, inputs=["brief"]))
        self.assertEqual(found["outcome"], "passed", found.get("why"))
        item = found["inputs"][0]
        self.assertEqual(item["status"], "delivered")
        digest = hashlib.sha256(b"the approved brief\n").hexdigest()
        self.assertEqual(item["sha256"], digest)
        self.assertEqual(item["contextId"], profiles.context_id("brief", digest))
        self.assertNotIn("_data", item)
        log = verify.log_text(found["id"])["text"]
        self.assertIn("brief: the approved brief", log)
        # the copy was the verification's own, and went with it
        self.assertFalse(Path(found["env"]["tmp"]).exists())

    def test_an_undeclared_input_is_not_handed_over(self):
        found = self.run_check(self.document(body=READS_INPUT))
        self.assertEqual(found["outcome"], "failed")
        self.assertEqual(found["inputs"], [])
        self.assertIn("no brief was handed over",
                      verify.log_text(found["id"])["text"])

    def test_a_required_input_that_is_missing_refuses_and_runs_nothing(self):
        """The launch pinned it; by the time the check is asked for, the
        file has gone. The runner refuses rather than run unbound."""
        self.declares(self.document(body=READS_INPUT, inputs=["brief"]))
        self.manifest()
        self.brief.unlink()
        found = self.ask()
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "input-unavailable")
        self.assertIn("'brief'", found["why"])
        self.assertIsNone(found["rc"])
        self.assertNotIn("resultId", found)
        self.assertFalse(verify.log_path_of(found["id"]).exists())

    def test_an_optional_missing_input_runs_and_is_bound_as_absent(self):
        self.brief.unlink()
        document = self.document(inputs=["brief"])
        document["context"][0]["required"] = False
        found = self.run_check(document)
        self.assertEqual(found["outcome"], "passed")
        self.assertEqual(found["inputs"][0]["status"], "absent")
        self.assertIsNone(found["inputs"][0]["contextId"])
        self.assertEqual(self.reading(found)["outcome"], "passed")
        self.brief.write_text("now it exists\n", encoding="utf-8")
        reading = self.reading(found)
        self.assertEqual(reading["reason"], "input-changed")
        self.assertIn("was absent", reading["why"])

    def test_an_input_that_disappears_is_named(self):
        found = self.run_check(self.document(inputs=["brief"]))
        self.brief.unlink()
        reading = self.reading(found)
        self.assertEqual(reading["reason"], "input-changed")
        self.assertIn("no longer there", reading["why"])


class ChecksDeclareInputsAndEnv(IdentityCase):
    """Real validation: the parser a launch, the gate and Settings share."""

    def codes(self, **check) -> list[str]:
        """The refusal codes, less the echo: a check that does not validate
        is not offered, so the intent naming it also reads
        `check-undeclared`."""
        doc = profiles.document_from(json.dumps(self.document(**check)),
                                     self.local / "profiles.json")
        return [d["code"] for d in profiles.resolve(
            "work", worktree=self.wt, policy=doc)["diagnostics"]
            if d["code"] != "check-undeclared"]

    def test_an_input_the_context_does_not_select_is_refused(self):
        self.assertEqual(self.codes(inputs=["mockup"]),
                         ["check-input-undeclared"])

    def test_an_input_from_the_checks_own_source_is_refused(self):
        document = self.document(inputs=["mine"])
        document["context"].append({"name": "mine",
                                    "path": "worktree:code.txt"})
        doc = profiles.document_from(json.dumps(document),
                                     self.local / "profiles.json")
        found = profiles.resolve("work", worktree=self.wt, policy=doc)
        self.assertIn("check-invalid", [d["code"] for d in found["diagnostics"]])
        self.assertIn("is its source", " ".join(found["blockers"]))

    def test_invalid_environment_selections_are_refused(self):
        for env in (["not a name"], ["GITHUB_TOKEN"], ["HOME"],
                    ["BENCH_VERIFY_INPUTS"], ["AGENT_PROMPT"], ["BOARD_PORT"],
                    ["BOARD_SOMETHING"], ["DB", "DB"], "DB", [7]):
            with self.subTest(env=env):
                self.assertEqual(self.codes(env=env), ["check-env-invalid"])

    def test_valid_declarations_resolve_and_reach_the_manifest(self):
        self.assertEqual(self.codes(inputs=["brief"], env=["DATABASE_URL"]), [])
        doc = profiles.document_from(
            json.dumps(self.document(inputs=["brief"], env=["DATABASE_URL"])),
            self.local / "profiles.json")
        check = profiles.resolve("work", worktree=self.wt,
                                 policy=doc)["verify"][0]
        self.assertEqual(check["inputs"], [{"name": "brief",
                                            "source": "work:reference/brief.md",
                                            "required": True}])
        self.assertEqual(check["env"], ["DATABASE_URL"])

    def test_settings_validation_names_the_codes(self):
        found = settings.validate({"document": self.document(
            inputs=["nope"], env=["HOME"])})
        self.assertFalse(found["ok"])
        codes = {e["code"]: e["field"] for e in found["errors"]}
        self.assertEqual(codes.get("check-input-undeclared"), "checks")

    def test_a_check_declaring_neither_keeps_its_identity(self):
        """No silent migration: an existing check's revision and checkId
        are what they were before inputs and env existed."""
        self.declares(self.document())
        check = profiles.resolve("work", worktree=self.wt)["verify"][0]
        old = profiles._digest("unit", check["run"], "worktree",
                               json.dumps({}, sort_keys=True), json.dumps([]),
                               f"{check['timeout']:.0f}")
        self.assertEqual(check["revision"], old)
        self.assertEqual(check["checkId"], profiles.check_id("unit", old))


# ── declared environment: keyed, never stored ──────────────────────────


class DeclaredEnvironment(IdentityCase):

    def setUp(self):
        super().setUp()
        config._ENV["DATABASE_URL"] = SECRET

    def test_no_raw_value_or_key_reaches_json_logs_or_the_page(self):
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        self.assertEqual(found["outcome"], "passed")
        key_path = verify.env_key_path()
        key = key_path.read_bytes()
        self.assertEqual(len(key), verify.ENV_KEY_BYTES)
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        declared = found["env"]["declared"][0]
        self.assertEqual(declared["name"], "DATABASE_URL")
        self.assertTrue(declared["set"])
        self.assertTrue(found["env"]["keyId"].startswith("key:"))
        texts = {
            "evidence": verify.path_of(found["id"]).read_text(encoding="utf-8"),
            "log": verify.log_path_of(found["id"]).read_text(encoding="utf-8"),
            "public": json.dumps(verify.public(verify.read(found["id"]))),
            "reading": json.dumps(self.reading(found)),
            "gate": json.dumps(self.decide(), default=str),
            "settings": json.dumps(settings.verification(), default=str),
            "diagnostics": json.dumps(profiles.resolve(
                "work", worktree=self.wt)["diagnostics"]),
        }
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertNotIn("hunter2", text)
                self.assertNotIn(key.hex(), text)
        self.assertNotIn("hmac", json.dumps(texts["public"]))
        self.assertIn("hmac", texts["evidence"])

    def test_the_value_is_bound_by_name(self):
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        config._ENV["DATABASE_URL"] = SECRET.replace("hunter2", "hunter3")
        reading = self.reading(found)
        self.assertEqual(reading["reason"], "env-changed")
        self.assertIn("DATABASE_URL", reading["why"])
        self.assertNotIn("hunter", reading["why"])

    def test_an_unset_declared_variable_is_bound_as_unset(self):
        config._ENV.pop("DATABASE_URL")
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        self.assertFalse(found["env"]["declared"][0]["set"])
        self.assertEqual(self.reading(found)["outcome"], "passed")
        config._ENV["DATABASE_URL"] = "now set"
        self.assertEqual(self.reading(found)["reason"], "env-changed")

    def test_freshness_creates_no_key_and_no_state(self):
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        verify.env_key_path().unlink()
        before = sorted(str(p) for p in self.state.rglob("*"))
        reading = self.reading(found)
        verify.identity_drift(verify.read(found["id"]))
        self.assertEqual(sorted(str(p) for p in self.state.rglob("*")), before)
        self.assertFalse(verify.env_key_path().exists())
        self.assertEqual(reading["reason"], "env-changed")
        self.assertIn("gone or unusable", reading["why"])
        self.assertIn("one fresh run", reading["why"])

    def test_a_replaced_key_invalidates_conservatively(self):
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        path = verify.env_key_path()
        path.chmod(0o600)
        path.write_bytes(os.urandom(verify.ENV_KEY_BYTES))
        reading = self.reading(found)
        self.assertEqual(reading["reason"], "env-changed")
        self.assertIn("replaced", reading["why"])
        # and one fresh run binds under the new key
        fresh = self.run_check(self.document(env=["DATABASE_URL"]),
                               agent="run-2")
        self.assertEqual(self.reading(fresh)["outcome"], "passed")

    def test_a_check_without_env_makes_no_key(self):
        self.run_check(self.document())
        self.assertFalse(verify.env_key_path().exists())

    def test_a_key_bench_will_not_use_refuses_the_run(self):
        path = verify.env_key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"short")
        path.chmod(0o600)
        found = self.run_check(self.document(env=["DATABASE_URL"]))
        self.assertEqual(found["outcome"], "denied")
        self.assertEqual(found["reason"], "env-key-unusable")
        self.assertIn("Remove it", found["why"])
        self.assertEqual(path.read_bytes(), b"short", "never replaced quietly")


# ── E14 ────────────────────────────────────────────────────────────────


class LegacyRecordsKeepTheirRules(IdentityCase):

    def legacy(self, found: dict) -> Path:
        path = verify.path_of(found["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("resultId", "identity", "provenance", "inputs",
                    "prepareSteps", "checkSubstance"):
            data.pop(key, None)
        data["env"] = {k: v for k, v in data["env"].items()
                       if k in ("removed", "home", "tmp", "why")}
        path.write_text(json.dumps(data, indent=2, sort_keys=True),
                        encoding="utf-8")
        return path

    def test_unrelated_policy_still_stales_a_legacy_record_unrewritten(self):
        document = self.document()
        found = self.run_check(document)
        path = self.legacy(found)
        before = path.read_bytes()
        document["context"].append({"name": "card37-mockup",
                                    "path": "work:reference/card37.md",
                                    "required": False})
        self.declares(document)
        reading = self.reading(found)
        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "policy-changed")
        self.assertEqual(reading["freshness"]["rules"], "legacy")
        self.decide()
        verify.identity_drift(verify.read(found["id"]))
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(verify.identity_drift(verify.read(found["id"])))
        self.assertEqual(gates._partition(verify.read(found["id"]), None),
                         "legacy")


# ── E15 ────────────────────────────────────────────────────────────────


class AuthorisationStaysSeparate(IdentityCase):

    def test_a_reusable_pass_does_not_authorise_a_forbidden_request(self):
        document = self.document()
        found = self.run_check(document)
        self.assertEqual(found["outcome"], "passed")
        # the operator takes `unit` away from both intents; it stays
        # declared, so the old pass's identity is unchanged
        document["profiles"] = {"work": {"verify": []},
                                "review": {"verify": []}}
        self.declares(document)
        self.assertEqual(self.reading(found)["outcome"], "passed")
        for intent in ("work", "review"):
            with self.subTest(intent=intent):
                refused = verify.request(check="unit", task=CARD,
                                         intent=intent, stage="in-progress",
                                         actor="person")
                self.assertEqual(refused["outcome"], "denied")
                self.assertEqual(refused["reason"], "unknown-check")

    def test_a_pass_of_equal_substance_does_not_meet_another_requirement(self):
        document = self.document()
        twin = dict(document["checks"][0], name="unit-review")
        document["checks"].append(twin)
        document["profiles"]["work"]["verify"] = ["unit"]
        document["gates"] = {"acceptance": {"checks": ["unit-review"]}}
        found = self.run_check(document)
        self.assertEqual(found["outcome"], "passed")
        row = self.decide()["unmet"][0]
        self.assertEqual(row["check"], "unit-review")
        self.assertEqual(row["reason"], "not-run")


if __name__ == "__main__":
    unittest.main()
