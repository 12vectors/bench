"""Golden policy identities (task 120, WP11 of the maintainability plan).

Moving the merge-rule policy out of `profiles` is meant to change *where*
the code is and nothing it computes. The things it computes are
persisted: a run's manifest keeps its `policyId`, an evidence record its
`checkId` and policy revision, an override its gate scope identity, a
pinned bundle its revision. A digest that moved by one byte would make
every one of those read as stale or withdrawn, on every board, with no
change anybody made.

So the identities are pinned here, against fixture documents, as the
values the code produced *before* the move
(`tests/fixtures/policy-golden/golden.json`), and so is the order of
every refusal — `blockers[0]` is the sentence a launch and a merge refuse
with, so a reordering is a behaviour change too.

Every value is portable: the fixture install is a temporary directory
whose paths are normalised to `<tmp>`, the adapter is a fixture adapter
with a fixed version, the core directory the verification shim is named
under is a fixed path, and the document's revision is a digest of its
text. Nothing here reaches the network, the operator's install or a real
agent.

To see what the code produces now (to compare by eye, never to paper
over a difference):

    python3 -m tests.test_policy_golden
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import gates  # noqa: E402
import profiles  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "policy-golden"
GOLDEN = FIXTURES / "golden.json"

# A fixed, non-existent core directory: the verification shim is granted
# by absolute path, and that path is part of a resolved profile's revision.
FAKE_CORE = Path("/opt/bench-golden/manager/core")

GATE_SCOPES_IN_ORDER = ("member", "integration", "acceptance")


def _documents() -> dict[str, str]:
    """The fixture documents, by name, as raw text (the revision of a
    document is a digest of its text, so the text is the fixture)."""
    return {name: (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
            for name in ("full", "broken-gates", "broken-profiles",
                         "scalar", "not-json", "wrong-version")}


class _Install:
    """A disposable tool install, work root and application directory,
    with `config` pointed at it for the life of one observation."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.tool = self.root / "tool"
        self.local = self.tool / "manager" / "local"
        self.work = self.root / "work"
        self.app = self.root / "app"
        for path in (self.local / "state", self.work / "tasks",
                     self.work / "reference", self.app):
            path.mkdir(parents=True, exist_ok=True)
        adapter = self.local / "adapters" / "golden"
        adapter.mkdir(parents=True)
        (adapter / "run").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (adapter / "capabilities.json").write_text(json.dumps({
            "version": "golden-1",
            "intents": ["work", "act-pr", "review", "refine"],
            "capabilities": {"command-allowlist": "prefix-allowlist",
                             "context-pinning": "enforced",
                             "read-only-tools": "enforced",
                             "shell-withheld": "enforced",
                             "progress-report": "enforced"},
            "unsupported": {"filesystem-sandbox": "not in the fixture"},
        }), encoding="utf-8")
        (self.local / "brief.md").write_text("# the brief\n",
                                             encoding="utf-8")
        (self.work / "reference" / "design.md").write_text(
            "# design\n", encoding="utf-8")
        self._saved: dict = {}

    def __enter__(self) -> "_Install":
        state = self.local / "state"
        values = dict(
            TM_ROOT=self.tool, LOCAL=self.local, CORE=FAKE_CORE,
            REPO=self.app, TASKS=self.work / "tasks",
            WORKTREES=self.root / "worktrees",
            CONTEXT_DIR=state / "context", MANIFESTS_DIR=state / "manifests",
            GATES_DIR=state / "gates", EVIDENCE_DIR=state / "evidence",
            AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
            APP_SETTING=str(self.app), WORK_SETTING=str(self.work),
            EXTERNAL=True, VERIFY_TIMEOUT=3600.0, PREPARE_TIMEOUT=3600.0,
            # the fixture harness for every intent, whatever the process
            # environment this suite runs in says (an in-process setting
            # wins over it, and the locked environment is emptied)
            ADAPTER="golden", _ENV_LOCKED={})
        for attr, value in values.items():
            self._saved[attr] = getattr(config, attr)
            setattr(config, attr, value)
        gates.forget_cached()
        return self

    def __exit__(self, *exc) -> None:
        for attr, value in self._saved.items():
            setattr(config, attr, value)
        gates.forget_cached()
        self._tmp.cleanup()

    def declares(self, raw: str | None) -> dict:
        path = self.local / "profiles.json"
        if raw is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(raw, encoding="utf-8")
        gates.forget_cached()
        return profiles.document()

    def portable(self, value):
        """The observation with this install's paths named `<tmp>`."""
        text = json.dumps(value, sort_keys=True)
        return json.loads(text.replace(str(self.root), "<tmp>"))


def gate_policy(doc: dict) -> dict:
    return profiles.gate_policy(doc)


def gate_policy_public(found: dict) -> dict:
    return profiles.gate_policy_public(found)


def verification_block(manifest: dict) -> str:
    return profiles.verification_block(manifest)


def _observe_document(install: _Install, raw: str | None) -> dict:
    doc = install.declares(raw)
    out: dict = {"document": {k: doc.get(k) for k in
                              ("kind", "identity", "revision", "describes",
                               "problem")}}
    steps, step_trouble = profiles.document_steps(doc)
    checks, check_trouble = profiles.document_checks(doc, steps)
    out["checks"] = {name: {"revision": c.get("revision"),
                            "checkId": c.get("checkId"),
                            "checkSubstance": c.get("checkSubstance"),
                            "status": c.get("status")}
                     for name, c in sorted(checks.items())}
    out["checkTrouble"] = [list(t) if isinstance(t, tuple) else t
                           for t in [*step_trouble, *check_trouble]]

    policy = gate_policy(doc)
    out["gate"] = {
        "revision": policy["revision"], "policyId": policy["policyId"],
        "configured": policy["configured"],
        "blockers": list(policy["blockers"]),
        "codes": [d["code"] for d in policy["diagnostics"]],
        "scopePolicyIds": {s: gates.scope_policy_id(policy, s)
                           for s in GATE_SCOPES_IN_ORDER},
        "public": gate_policy_public(policy),
    }

    out["resolve"] = {}
    for intent in profiles.INTENTS:
        worktree = (None if intent in profiles.READ_ONLY_INTENTS
                    else install.root / "worktrees" / "71-golden")
        found = profiles.resolve(intent, worktree=worktree)
        out["resolve"][intent] = {
            "revision": found["revision"], "policyId": found["policyId"],
            "blockers": list(found["blockers"]),
            "codes": [d["code"] for d in found["diagnostics"]],
            "guarantees": found["guarantees"]["configured"],
            "verify": [c["checkId"] for c in found["verify"]],
            "verification": verification_block(found),
        }
        if intent == "work" and not found["blockers"] and found["context"]:
            pinned = profiles.pin(found)
            out["resolve"][intent]["bundle"] = pinned["bundle"]["revision"]
    return install.portable(out)


def observe() -> dict:
    """What the code computes now for every fixture document."""
    out: dict = {}
    with _Install() as install:
        out["legacy"] = _observe_document(install, None)
        for name, raw in _documents().items():
            out[name] = _observe_document(install, raw)
    return out


class PolicyIdentitiesAreGolden(unittest.TestCase):
    """Every persisted identity and every refusal's order, for each fixture
    document, is the value the code produced before the move."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.now = observe()

    def test_the_fixtures_are_the_ones_the_golden_values_were_taken_from(self):
        self.assertEqual(sorted(self.golden), sorted(self.now))

    def test_document_and_check_identities(self):
        for name in self.golden:
            with self.subTest(document=name):
                self.assertEqual(self.now[name]["document"],
                                 self.golden[name]["document"])
                self.assertEqual(self.now[name]["checks"],
                                 self.golden[name]["checks"])
                self.assertEqual(self.now[name]["checkTrouble"],
                                 self.golden[name]["checkTrouble"])

    def test_gate_policy_identities_and_blocker_order(self):
        for name in self.golden:
            with self.subTest(document=name):
                self.assertEqual(self.now[name]["gate"],
                                 self.golden[name]["gate"])

    def test_resolved_profiles_bundles_and_prompts(self):
        for name in self.golden:
            for intent in profiles.INTENTS:
                with self.subTest(document=name, intent=intent):
                    self.assertEqual(self.now[name]["resolve"][intent],
                                     self.golden[name]["resolve"][intent])

    def test_the_fixtures_exercise_what_they_are_for(self):
        """A golden file of empty answers would pin nothing."""
        full = self.golden["full"]
        self.assertTrue(full["gate"]["configured"])
        self.assertEqual(full["gate"]["blockers"], [])
        self.assertTrue(all(full["gate"]["public"]["scopes"][s]["gated"]
                            for s in GATE_SCOPES_IN_ORDER))
        self.assertIn("bundle", full["resolve"]["work"])
        self.assertIn("This board gates its merges",
                      full["resolve"]["work"]["verification"])
        self.assertIn("Ask for every required check",
                      full["resolve"]["work"]["verification"])
        self.assertNotIn("Ask for every required check",
                         full["resolve"]["review"]["verification"])
        self.assertGreaterEqual(len(self.golden["broken-gates"]["gate"]
                                    ["blockers"]), 8)
        self.assertGreaterEqual(len(self.golden["broken-profiles"]["resolve"]
                                    ["work"]["blockers"]), 3)
        for name in ("not-json", "wrong-version"):
            self.assertEqual(self.golden[name]["gate"]["codes"][0],
                             "policy-unreadable")


if __name__ == "__main__":
    # `python3 -m tests.test_policy_golden [<file>]` — prints, or writes to
    # <file>, what the code produces now. Comparing it with golden.json by
    # eye is the use; copying it over golden.json is the identity change
    # this test exists to refuse, and needs a reason in the commit.
    text = json.dumps(observe(), indent=2, sort_keys=True) + "\n"
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
