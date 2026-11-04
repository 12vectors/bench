"""Per-intent execution profiles and pinned approved context (task 67).

The subject is what a run is *allowed* to do and which files it is
*guaranteed* to have, so nothing here mocks the resolver: every case
writes a real profile document, real files on disk, real symlinks, and
reads back the resolved manifest the launch would ride. Launch cases use
a disposable application repository and a stub adapter in place of a
coding agent, so no token is spent.

Two claims are load-bearing and each has its own class:

- **A refusal lands before the model.** A required input that is missing,
  unreadable, a directory or reachable only through a link out of its
  root refuses the launch, and the card is left with no branch, no
  worktree, no claim and no run.
- **Nothing here claims an enforcement it does not have.** A command
  grant becomes a prefix allow-rule; that is narrowing, not a sandbox,
  and `guarantees` says which is which. A profile that *requires* a
  guarantee the installed backend cannot make is refused rather than
  degraded to a sentence in the prompt.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import config  # noqa: E402
import httpd  # noqa: E402
import plans  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CLAUDE = REPO / "manager" / "core" / "adapters" / "claude"
OPENCODE = REPO / "manager" / "core" / "adapters" / "opencode"

CARD = "67-resolve-execution-profiles.md"
STEM = CARD[:-3]
BRANCH = f"task/{STEM}"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_settings = _load("hook_settings_67", CLAUDE / "hook_settings.py")
permission_config = _load("permission_config_67", OPENCODE / "permission_config.py")

# An agent that does the job and records the environment it was handed, so
# the policy the board resolved can be checked against the policy that
# actually reached the vendor's rule generator.
WORKS = """#!/usr/bin/env python3
import json, os, subprocess
cwd = os.environ["AGENT_CWD"]
if os.path.isdir(os.path.join(cwd, ".git")) or os.path.exists(os.path.join(cwd, ".git")):
    open(os.path.join(cwd, "done.txt"), "w").write("the work\\n")
    subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
    subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                    "commit", "-q", "-m", "did it"], check=True)
keep = {k: v for k, v in os.environ.items()
        if k in ("AGENT_PROFILE", "AGENT_CONTEXT", "AGENT_COMMANDS",
                 "AGENT_MODE", "AGENT_CWD")}
keep["prompt"] = os.environ.get("AGENT_PROMPT", "")
# Beside the script, not through the environment: the board's child_env()
# is built from the settings it loaded at import, so a variable a test
# exports afterwards never reaches the adapter.
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "launch.json"), "w").write(json.dumps(keep))
print("WORK REPORT: did it")
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def card(title: str, *, status: str = "In Progress") -> str:
    return (f"# {title}\n\n**Status:** {status}\n**Priority:** High\n\n"
            f"What this card is for, at enough length to be a brief.\n")


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class ProfileCase(unittest.TestCase):
    """A disposable tool install, work root and application checkout, with
    config pointed at all three — so a document naming `tool:`, `work:`
    and `app:` roots is resolved against directories this test owns.

    No symlinks are used to make any of it work: the roots are named
    settings, which is the whole of card 67's "operator-local separate
    roots work without symlinks".

    `LAYOUT` is where the board's own files sit relative to the
    application, and the resolution cases are run under all three further
    down: `external` (the tool, the tickets and the code in three places),
    `vendored` (a `.task-manager/` inside the project) and `flat` (the
    self-hosted source checkout, where the tool *is* the project).
    Filesystem layout must not change what a run is allowed to do.
    """

    LAYOUT = "external"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.repo = self.tmp / "app"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "tester")
        (self.repo / "code.txt").write_text("main's line\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# the application's brief\n",
                                             encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

        # where the tool and the work state sit for this layout
        if self.LAYOUT == "external":
            self.tool, self.work = self.tmp / "tool", self.tmp / "work"
        elif self.LAYOUT == "vendored":
            self.tool = self.work = self.repo / ".task-manager"
        else:
            self.tool = self.work = self.repo

        self.tasks = self.work / "tasks"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        (self.work / "reference").mkdir(parents=True, exist_ok=True)

        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        (self.local / "commands").mkdir(parents=True)
        self.adapter = self.local / "adapters" / config.ADAPTER / "run"
        self.adapter_is(WORKS)
        self.state = self.local / "state"
        self.launch_log = self.adapter.parent / "launch.json"
        # The stub stands in for the shipped adapter, so it declares what
        # the shipped one declares. An adapter that promises nothing is a
        # case of its own (`capabilities_are(None)`), not the default.
        self.capabilities_are(json.loads(
            (CLAUDE / "capabilities.json").read_text(encoding="utf-8")))

        self.patch(REPO=self.repo, TASKS=self.tasks, TM_ROOT=self.tool,
                   LOCAL=self.local, CORE=config.CORE,
                   WORKTREES=self.tmp / "worktrees",
                   PLANS_DIR=self.state / "plans",
                   MANIFESTS_DIR=self.state / "manifests",
                   CONTEXT_DIR=self.state / "context",
                   AGENT_DIR=self.state / "agent",
                   SESSIONS_DIR=self.state / "sessions",
                   AGENT_PROFILES="", AGENT_COMMANDS="python3 -m unittest",
                   APP_BASE="main", GIT_REMOTE="",
                   COMMIT_MOVES=False, SYNC=False, FETCH_TIMEOUT=10.0)
        # EXTERNAL/APP_SETTING are read at import; these tests point the
        # roots directly, which is the same resolved layout.
        external = self.LAYOUT == "external"
        self.patch(APP_SETTING=str(self.repo) if external else "",
                   WORK_SETTING=str(self.work) if external else "",
                   EXTERNAL=external)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES):
            registry.clear()
            self.addCleanup(registry.clear)
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        self.write(card("67 — Resolve execution profiles"))

    # — the furniture —

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def adapter_is(self, script: str) -> None:
        self.adapter.write_text(script, encoding="utf-8")
        self.adapter.chmod(self.adapter.stat().st_mode | stat.S_IEXEC)

    def capabilities_are(self, payload: dict | None) -> None:
        """What the installed adapter declares it can enforce. `None`
        removes the file — an adapter that promises nothing."""
        path = self.adapter.parent / "capabilities.json"
        if payload is None:
            if path.exists():
                path.unlink()
            return
        path.write_text(json.dumps(payload), encoding="utf-8")

    def declares(self, document: dict | str) -> Path:
        """Write the profile document this board resolves from."""
        path = self.local / "profiles.json"
        path.write_text(document if isinstance(document, str)
                        else json.dumps(document), encoding="utf-8")
        return path

    def write(self, text: str, *, name: str = CARD,
              stage: str = "in-progress") -> None:
        (self.tasks / stage / name).write_text(text, encoding="utf-8")

    def resolve(self, intent: str = "work", *, name: str = CARD,
                stage: str = "in-progress") -> dict:
        return profiles.manifest(intent, name, stage)

    def grants(self, found: dict) -> list[str]:
        return [g["name"] for g in found["commands"]]

    def prefixes(self, found: dict) -> list[str]:
        return [g["prefix"] for g in found["commands"]]

    def inputs(self, found: dict) -> dict:
        return {i["name"]: i for i in found["context"]}

    def run_agent(self, *, name: str = CARD) -> dict:
        record = agents.start_agent(name, "in-progress")
        marks = len(state.BOARD_EVENTS)
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"),
            "the stub agent never finished")
        self.assertTrue(wait_for(lambda: len(state.BOARD_EVENTS) > marks),
                        "the run never narrated its ending")
        return state.AGENTS[record["id"]]

    def launched(self) -> dict:
        self.assertTrue(self.launch_log.is_file(),
                        "the adapter was never launched")
        return json.loads(self.launch_log.read_text(encoding="utf-8"))

    def assertNothingStarted(self) -> None:
        """A refused launch leaves the card exactly as it was."""
        branches = git(self.repo, "for-each-ref",
                       "--format=%(refname:short)", "refs/heads/").stdout.split()
        self.assertNotIn(BRANCH, branches)
        self.assertFalse((config.WORKTREES / STEM).exists())
        self.assertEqual(agents.list_public(), [])
        self.assertFalse(self.launch_log.exists(),
                         "the adapter was launched for a refused profile")
        self.assertFalse(config.CONTEXT_DIR.exists(),
                         "a refused launch pinned a context bundle")


# ── the three intents, each with its own answer ────────────────────────


class ProfilesPerIntent(ProfileCase):
    """`work`, `act-pr` and `review` resolve distinctly, and each says
    exactly what it may do rather than whether it is configured."""

    def test_every_intent_resolves(self):
        for intent in profiles.INTENTS:
            with self.subTest(intent=intent):
                found = self.resolve(intent)
                self.assertEqual(found["intent"], intent)
                self.assertEqual(found["blockers"], [])
                self.assertTrue(found["policyId"].startswith(f"profile:{intent}@"))

    def test_an_unknown_intent_is_refused_rather_than_defaulted(self):
        with self.assertRaises(ValueError) as caught:
            profiles.resolve("deploy")
        self.assertIn("unknown launch intent", str(caught.exception))

    def test_the_three_policies_are_not_the_same_policy(self):
        ids = {i: self.resolve(i)["policyId"] for i in profiles.INTENTS}
        self.assertEqual(len(set(ids.values())), 3, ids)

    def test_work_commits_in_its_worktree_and_publishes_nothing(self):
        found = self.resolve("work")
        self.assertEqual(found["publication"],
                         {"commit": True, "push": False, "publish": False})
        self.assertEqual(found["cwd"]["policy"], "worktree")
        self.assertEqual([w["root"] for w in found["writable"]], ["worktree"])
        denied = [d["capability"] for d in found["denied"]]
        self.assertIn("push", denied)
        self.assertIn("publish", denied)

    def test_act_pr_is_work_plus_push_and_nothing_else(self):
        work, act = self.resolve("work"), self.resolve("act-pr")
        self.assertTrue(act["publication"]["push"])
        self.assertFalse(act["publication"]["publish"])
        self.assertEqual(self.prefixes(work), self.prefixes(act))

    def test_review_is_read_only_runs_in_the_application_and_gets_no_checks(self):
        found = self.resolve("review")
        self.assertEqual(found["cwd"]["policy"], "app")
        self.assertEqual(found["cwd"]["path"], str(self.repo))
        self.assertEqual(found["writable"], [])
        self.assertEqual(found["commands"], [],
                         "the read-only intent must not inherit the project's "
                         "check commands")
        self.assertEqual(found["publication"],
                         {"commit": False, "push": False, "publish": True})
        denied = [d["capability"] for d in found["denied"]]
        for word in ("edit", "commit", "push"):
            self.assertIn(word, denied)

    def test_review_has_no_worktree_root_at_all(self):
        self.assertNotIn("worktree", self.resolve("review")["roots"])
        self.assertIn("worktree", self.resolve("work")["roots"])

    def test_the_roots_are_the_resolved_ones_not_guesses(self):
        found = self.resolve("work")
        self.assertEqual(found["roots"]["app"], str(self.repo))
        self.assertEqual(found["roots"]["work"], str(self.work))
        self.assertEqual(found["roots"]["tool"], str(self.tool))
        self.assertEqual(found["roots"]["worktree"],
                         str(config.WORKTREES / STEM))

    def test_a_profile_says_what_it_grants_not_that_it_is_configured(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "unit-tests", "run": "python3 -m unittest"},
            {"name": "lint", "run": "ruff check"}]}}})
        found = self.resolve("work")
        self.assertEqual(self.grants(found), ["unit-tests", "lint"])
        self.assertEqual(self.prefixes(found),
                         ["python3 -m unittest", "ruff check"])
        self.assertEqual([g["executable"] for g in found["commands"]],
                         ["python3", "ruff"])
        self.assertEqual(found["commands"][0]["argv"], ["-m", "unittest"])


# ── the legacy path ────────────────────────────────────────────────────


class LegacySettingsKeepWorking(ProfileCase):
    """A board with no profile document resolves exactly as it did before
    this module existed, and says so in `source.kind`."""

    def test_no_document_is_the_legacy_source(self):
        found = self.resolve("work")
        self.assertEqual(found["source"]["kind"], "legacy")
        self.assertIn("BOARD_AGENT_COMMANDS", found["source"]["describes"])
        self.assertIsNone(profiles.document_path())

    def test_board_agent_commands_become_named_grants(self):
        self.patch(AGENT_COMMANDS="python3 -m unittest,npm test")
        found = self.resolve("work")
        self.assertEqual(self.prefixes(found),
                         ["python3 -m unittest", "npm test"])
        for grant in found["commands"]:
            self.assertEqual(grant["source"], "BOARD_AGENT_COMMANDS")
            self.assertTrue(grant["agentCapability"])

    def test_an_empty_setting_grants_nothing_and_still_resolves(self):
        self.patch(AGENT_COMMANDS=" , ")
        found = self.resolve("work")
        self.assertEqual(found["commands"], [])
        self.assertEqual(found["blockers"], [],
                         "an unconfigured project is not a refused launch")

    def test_the_legacy_review_intent_gains_nothing(self):
        self.patch(AGENT_COMMANDS="python3 -m unittest")
        self.assertEqual(self.resolve("review")["commands"], [])

    def test_a_document_that_covers_one_intent_leaves_the_others_legacy(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "lint", "run": "ruff check"}]}}})
        self.assertEqual(self.prefixes(self.resolve("work")), ["ruff check"])
        act = self.resolve("act-pr")
        self.assertEqual(self.prefixes(act), ["python3 -m unittest"])
        self.assertIn("declares nothing for 'act-pr'",
                      " ".join(act["notes"]))

    def test_a_legacy_prefix_that_is_not_a_command_line_is_reported_not_fatal(self):
        self.patch(AGENT_COMMANDS="python3 -m unittest,make test && echo done")
        found = self.resolve("work")
        self.assertEqual(self.prefixes(found), ["python3 -m unittest"])
        self.assertEqual(found["blockers"], [],
                         "a legacy setting must not refuse a launch that used "
                         "to work")
        self.assertIn("BOARD_AGENT_COMMANDS", " ".join(found["notes"]))

    def test_the_adapters_generate_the_old_rules_without_a_profile(self):
        """The compatibility seam itself: the rule generators take the
        profile as an optional argument, and with none they produce byte
        for byte what they produced before profiles existed."""
        commands = ["python3 -m unittest"]
        for mode in ("work", "act-pr", "review"):
            with self.subTest(mode=mode):
                self.assertEqual(hook_settings.allow_rules(mode, commands),
                                 hook_settings.allow_rules(mode, commands, None))
                self.assertEqual(hook_settings.deny_rules(mode),
                                 hook_settings.deny_rules(mode, None))
                self.assertEqual(
                    permission_config.build_config(mode, commands),
                    permission_config.build_config(mode, commands, "", None))
        # and no deny key appears for the two intents that never had one
        for mode in ("work", "review"):
            self.assertNotIn(
                "deny", hook_settings.settings(mode, commands)["permissions"])

    def test_an_adapter_that_declares_nothing_still_runs_a_legacy_profile(self):
        self.capabilities_are(None)
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertFalse(found["adapter"]["declared"])
        self.assertIn("declares no capabilities.json", found["adapter"]["note"])


# ── a profile narrows; it never widens ─────────────────────────────────


class ANarrowingOnly(ProfileCase):
    def test_review_cannot_ask_for_push(self):
        self.declares({"v": 1, "profiles": {"review": {
            "publication": {"push": True}}}})
        found = self.resolve("review")
        self.assertTrue(found["blockers"])
        self.assertIn("never widens", found["blockers"][0])
        self.assertFalse(found["publication"]["push"])

    def test_work_cannot_ask_for_publish(self):
        self.declares({"v": 1, "profiles": {"work": {
            "publication": {"publish": True}}}})
        self.assertTrue(self.resolve("work")["blockers"])

    def test_turning_a_side_effect_off_is_allowed(self):
        self.declares({"v": 1, "profiles": {"act-pr": {
            "publication": {"push": False}}}})
        found = self.resolve("act-pr")
        self.assertEqual(found["blockers"], [])
        self.assertFalse(found["publication"]["push"])

    def test_a_profile_may_deny_more_and_the_denials_are_named(self):
        self.declares({"v": 1, "profiles": {"act-pr": {"deny": ["push"]}}})
        found = self.resolve("act-pr")
        self.assertEqual(found["blockers"], [])
        self.assertIn("push", [d["capability"] for d in found["denied"]])
        self.assertTrue(all(d["why"] for d in found["denied"]))

    def test_an_unknown_denied_capability_is_refused(self):
        self.declares({"v": 1, "profiles": {"work": {"deny": ["network"]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("unknown capability", found["blockers"][0])

    def test_the_denials_reach_the_claude_rules_as_rules_that_beat_allows(self):
        policy = profiles.adapter_policy(self.resolve("work"))
        deny = hook_settings.deny_rules("work", policy)
        for prefix in ("git push", "gh pr merge", "git worktree"):
            self.assertIn(f"Bash({prefix}:*)", deny)
        settings = hook_settings.settings("work", [], policy)
        self.assertIn("deny", settings["permissions"])

    def test_the_denials_reach_the_opencode_rules_last(self):
        policy = profiles.adapter_policy(self.resolve("work"))
        bash = permission_config.bash_rules("work", [], policy)
        self.assertEqual(bash["git push *"], "deny")
        self.assertEqual(bash["gh pr merge*"], "deny")
        keys = list(bash)
        self.assertGreater(keys.index("git push *"), keys.index("*"))

    def test_review_denying_edit_reaches_opencodes_edit_key(self):
        policy = profiles.adapter_policy(self.resolve("review"))
        built = permission_config.build_config("review", [], "", policy)
        self.assertEqual(built["permission"]["edit"], "deny")

    def test_a_grant_cannot_reshape_the_lifecycle_bench_planned(self):
        """Card 58's guarantee, restated in profile terms: the words that
        would let a run pick a different branch, worktree or base are
        denied capabilities on every intent."""
        for intent in profiles.INTENTS:
            denied = [d["capability"] for d in self.resolve(intent)["denied"]]
            self.assertIn("reshape-lifecycle", denied, intent)


# ── named command grants are validated ─────────────────────────────────


class CommandGrantsAreValidated(ProfileCase):
    def _refuses(self, grant: dict, fragment: str) -> str:
        self.declares({"v": 1, "profiles": {"work": {"commands": [grant]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"], f"{grant} was accepted")
        self.assertIn(fragment, found["blockers"][0])
        return found["blockers"][0]

    def test_a_compound_command_is_refused(self):
        self._refuses({"name": "tests", "run": "make test && rm -rf /"}, "&")

    def test_a_pipeline_is_refused(self):
        self._refuses({"name": "tests", "run": "cat x | sh"}, "|")

    def test_a_command_substitution_is_refused(self):
        self._refuses({"name": "tests", "run": "python3 $(cat cmd)"}, "$")

    def test_a_backtick_substitution_is_refused(self):
        self._refuses({"name": "tests", "run": "python3 `cat cmd`"}, "`")

    def test_a_redirection_is_refused(self):
        self._refuses({"name": "tests", "run": "make test > /etc/passwd"}, ">")

    def test_a_glob_is_refused(self):
        self._refuses({"name": "tests", "run": "rm *"}, "*")

    def test_a_semicolon_is_refused(self):
        self._refuses({"name": "tests", "run": "make test; rm -rf ."}, ";")

    def test_a_relative_executable_is_refused(self):
        self._refuses({"name": "tests", "run": "scripts/test.sh"},
                      "bare name resolved on PATH")

    def test_an_executable_climbing_out_with_dotdot_is_refused(self):
        self._refuses({"name": "tests", "run": "/usr/bin/../bin/sh"}, "'..'")

    def test_an_empty_run_is_refused(self):
        self._refuses({"name": "tests", "run": "   "}, "has no \"run\"")

    def test_a_malformed_name_is_refused(self):
        self._refuses({"name": "Unit Tests", "run": "python3 -m unittest"},
                      "a-z0-9")

    def test_a_missing_name_is_refused(self):
        self._refuses({"run": "python3 -m unittest"}, "a-z0-9")

    def test_an_unknown_root_is_refused(self):
        self._refuses({"name": "tests", "run": "python3 -m unittest",
                       "root": "anywhere"}, "execution root")

    def test_a_grant_may_not_be_declared_twice(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "tests", "run": "python3 -m unittest"},
            {"name": "tests", "run": "npm test"}]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("declared twice", found["blockers"][0])

    def test_a_grant_not_written_in_normal_form_is_refused(self):
        self._refuses({"name": "tests", "run": "python3   -m    unittest"},
                      "normalises to")

    def test_a_grant_that_is_not_an_object_is_refused(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": ["make test"]}}})
        self.assertTrue(self.resolve("work")["blockers"])

    def test_a_worktree_root_is_refused_for_the_read_only_intent(self):
        self.declares({"v": 1, "profiles": {"review": {"commands": [
            {"name": "tests", "run": "python3 -m unittest",
             "root": "worktree"}]}}})
        found = self.resolve("review")
        self.assertTrue(found["blockers"])
        self.assertIn("no worktree", found["blockers"][0])

    def test_an_absolute_executable_is_accepted(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "tests", "run": "/usr/bin/env python3 -m unittest"}]}}})
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertEqual(found["commands"][0]["executable"], "/usr/bin/env")

    def test_a_prefix_grant_is_never_called_enforcement(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "tests", "run": "python3 -m unittest"}]}}})
        found = self.resolve("work")
        self.assertEqual(found["commands"][0]["grant"], "prefix-allowlist")
        self.assertIn("command-allowlist", found["guarantees"]["prefixMatched"])
        self.assertNotIn("command-allowlist", found["guarantees"]["enforced"])
        self.assertIn("not a sandbox", found["guarantees"]["caveat"])


# ── approved context ───────────────────────────────────────────────────


class ApprovedContextResolves(ProfileCase):
    def _select(self, **selection) -> dict:
        self.declares({"v": 1, "profiles": {"work": {
            "context": [selection]}}})
        return self.resolve("work")

    def test_a_file_in_the_tool_root_resolves_and_is_hashed(self):
        brief = self.local / "AGENTS.md"
        brief.write_text("# the board's own brief\n", encoding="utf-8")
        found = self._select(name="workflow-brief",
                             path="tool:manager/local/AGENTS.md")
        self.assertEqual(found["blockers"], [])
        item = self.inputs(found)["workflow-brief"]
        self.assertTrue(item["readable"])
        self.assertEqual(item["status"], "resolved")
        self.assertEqual(item["bytes"], len(brief.read_bytes()))
        self.assertEqual(len(item["sha256"]), 64)
        self.assertTrue(item["contextId"].startswith("ctx:workflow-brief@"))
        self.assertEqual(item["access"], "read-only")

    def test_a_file_in_the_work_root_resolves(self):
        design = self.work / "reference" / "design.md"
        design.write_text("# the design system\n", encoding="utf-8")
        found = self._select(name="design", path="work:reference/design.md")
        self.assertEqual(found["blockers"], [])
        self.assertTrue(self.inputs(found)["design"]["readable"])

    def test_a_file_in_the_application_resolves_with_its_commit(self):
        found = self._select(name="brief", path="app:AGENTS.md")
        item = self.inputs(found)["brief"]
        self.assertEqual(found["blockers"], [])
        self.assertTrue(item["revision"].startswith("git:"),
                        f"a tracked file names its commit, got {item['revision']}")

    def test_an_untracked_file_names_its_mtime_and_size(self):
        loose = self.work / "reference" / "loose.md"
        loose.write_text("notes\n", encoding="utf-8")
        item = self.inputs(self._select(
            name="loose", path="work:reference/loose.md"))["loose"]
        self.assertTrue(item["revision"].startswith("mtime:"), item["revision"])

    def test_the_prompt_names_the_inputs_without_replacing_the_brief(self):
        (self.work / "reference" / "design.md").write_text("x\n", encoding="utf-8")
        found = profiles.pin(self._select(name="design",
                                          path="work:reference/design.md"))
        block = profiles.prompt_block(found)
        self.assertIn("design", block)
        self.assertIn("read-only inputs", block)
        self.assertIn("do not replace", block)
        self.assertIn("not permission to edit", block)
        # and the protected brief is still the sentence config writes
        self.assertIn("Read AGENTS.md at the repo root first",
                      config.prompt_guidance())

    def test_no_selection_means_no_block_at_all(self):
        self.assertEqual(profiles.prompt_block(self.resolve("work")), "")

    def test_the_intents_may_select_different_inputs(self):
        (self.work / "reference" / "r.md").write_text("review\n", encoding="utf-8")
        (self.work / "reference" / "w.md").write_text("work\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {
            "work": {"context": [{"name": "w", "path": "work:reference/w.md"}]},
            "review": {"context": [{"name": "r", "path": "work:reference/r.md"}]}}})
        self.assertEqual(list(self.inputs(self.resolve("work"))), ["w"])
        self.assertEqual(list(self.inputs(self.resolve("review"))), ["r"])

    def test_a_shared_selection_applies_to_every_intent(self):
        (self.work / "reference" / "all.md").write_text("all\n", encoding="utf-8")
        self.declares({"v": 1,
                       "context": [{"name": "all", "path": "work:reference/all.md"}],
                       "profiles": {"work": {}, "review": {}, "act-pr": {}}})
        for intent in profiles.INTENTS:
            self.assertEqual(list(self.inputs(self.resolve(intent))), ["all"],
                             intent)


class ApprovedContextFailsClosed(ProfileCase):
    def _select(self, intent: str = "work", **selection) -> dict:
        self.declares({"v": 1, "profiles": {intent: {"context": [selection]}}})
        return self.resolve(intent)

    def test_a_required_missing_file_refuses_and_names_the_input(self):
        found = self._select(name="design", path="work:reference/design.md")
        self.assertTrue(found["blockers"])
        blocker = found["blockers"][0]
        self.assertIn("'design'", blocker)
        self.assertIn("does not exist", blocker)
        self.assertIn(str(self.work / "reference" / "design.md"), blocker)
        self.assertIn("mark the selection", blocker, "the remedy travels with it")

    def test_an_optional_missing_file_is_a_note_and_the_run_goes_ahead(self):
        found = self._select(name="design", path="work:reference/design.md",
                             required=False)
        self.assertEqual(found["blockers"], [])
        self.assertIn("goes ahead without it", " ".join(found["notes"]))
        self.assertEqual(self.inputs(found)["design"]["status"], "missing")

    def test_an_unreadable_file_refuses_with_a_remedy(self):
        path = self.work / "reference" / "secretless.md"
        path.write_text("contents\n", encoding="utf-8")
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("running as a user permissions do not apply to")
        found = self._select(name="brief", path="work:reference/secretless.md")
        self.assertTrue(found["blockers"])
        self.assertIn("could not be read", found["blockers"][0])
        self.assertIn("readable by the user the board runs as",
                      found["blockers"][0])

    def test_a_directory_is_refused_as_a_read_only_guarantee(self):
        found = self._select(name="refs", path="work:reference")
        self.assertTrue(found["blockers"])
        self.assertIn("directory grant", found["blockers"][0])

    def test_a_root_on_its_own_is_refused(self):
        found = self._select(name="everything", path="work:")
        self.assertTrue(found["blockers"])
        self.assertIn("directory grant", found["blockers"][0])

    def test_dotdot_cannot_climb_out_of_a_root(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not approved\n", encoding="utf-8")
        found = self._select(name="escape", path="work:../outside.md")
        self.assertTrue(found["blockers"])
        self.assertIn("'..'", found["blockers"][0])

    def test_an_absolute_path_is_refused(self):
        found = self._select(name="escape", path=f"work:{self.tmp / 'x.md'}")
        self.assertTrue(found["blockers"])
        self.assertIn("absolute", found["blockers"][0])

    def test_a_symlink_out_of_a_root_is_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not approved\n", encoding="utf-8")
        link = self.work / "reference" / "link.md"
        link.symlink_to(outside)
        found = self._select(name="escape", path="work:reference/link.md")
        self.assertTrue(found["blockers"])
        self.assertIn(str(outside), found["blockers"][0])
        self.assertIn("outside the work root", found["blockers"][0])

    def test_a_symlinked_directory_in_the_path_is_refused_too(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "doc.md").write_text("not approved\n", encoding="utf-8")
        (self.work / "reference" / "linked").symlink_to(outside)
        found = self._select(name="escape", path="work:reference/linked/doc.md")
        self.assertTrue(found["blockers"])
        self.assertIn("outside the work root", found["blockers"][0])

    def test_an_input_may_not_be_declared_twice(self):
        (self.work / "reference" / "a.md").write_text("a\n", encoding="utf-8")
        (self.local / "AGENTS.md").write_text("b\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "brief", "path": "work:reference/a.md"},
            {"name": "brief", "path": "tool:manager/local/AGENTS.md"}]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("declared twice", found["blockers"][0])

    def test_an_unknown_root_word_is_refused(self):
        found = self._select(name="x", path="home:.ssh/id_rsa")
        self.assertTrue(found["blockers"])
        self.assertIn("names the root", found["blockers"][0])

    def test_a_path_without_a_root_is_refused(self):
        found = self._select(name="x", path="reference/design.md")
        self.assertTrue(found["blockers"])
        self.assertIn("which root", found["blockers"][0])

    def test_the_worktree_root_is_unavailable_to_the_read_only_intent(self):
        found = self._select("review", name="x", path="worktree:README.md")
        self.assertTrue(found["blockers"])
        self.assertIn("no worktree", found["blockers"][0])

    def test_a_credential_shaped_name_is_refused_even_if_it_is_there(self):
        for name in (".env", "deploy.pem", "id_rsa", "api-token.txt",
                     "service-credentials.json", ".netrc"):
            with self.subTest(name=name):
                (self.work / "reference" / name).write_text("s3cret\n",
                                                            encoding="utf-8")
                found = self._select(name="thing",
                                     path=f"work:reference/{name}")
                self.assertTrue(found["blockers"], name)
                self.assertIn("reads as a credential", found["blockers"][0])

    def test_an_oversize_file_is_refused_with_the_cap_named(self):
        big = self.work / "reference" / "big.md"
        big.write_bytes(b"x" * (profiles.MAX_CONTEXT_BYTES + 1))
        found = self._select(name="big", path="work:reference/big.md")
        self.assertTrue(found["blockers"])
        self.assertIn(str(profiles.MAX_CONTEXT_BYTES), found["blockers"][0])

    def test_a_refusal_never_quotes_the_file_it_refused(self):
        """Diagnostics name paths and identities, never content — the one
        rule that keeps a refusal safe to put in a ticker or a log."""
        (self.work / "reference" / "token.txt").write_text(
            "SUPER-SECRET-VALUE\n", encoding="utf-8")
        found = self._select(name="thing", path="work:reference/token.txt")
        self.assertNotIn("SUPER-SECRET-VALUE", json.dumps(found))


class ReadableContextIsImmutable(ProfileCase):
    def setUp(self):
        super().setUp()
        self.source = self.work / "reference" / "design.md"
        self.source.write_text("the original design\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "design", "path": "work:reference/design.md"}]}}})

    def test_pinning_copies_the_file_read_only(self):
        found = profiles.pin(self.resolve("work"))
        item = self.inputs(found)["design"]
        self.assertEqual(item["status"], "pinned")
        pinned = Path(item["pinned"])
        self.assertEqual(pinned.read_text(encoding="utf-8"),
                         "the original design\n")
        self.assertEqual(stat.S_IMODE(pinned.stat().st_mode), 0o444)
        self.assertEqual(found["bundle"]["files"], 1)
        self.assertEqual(found["bundle"]["access"], "read-only")

    def test_resolving_twice_over_unchanged_files_reuses_one_bundle(self):
        first = profiles.pin(self.resolve("work"))["bundle"]["revision"]
        second = profiles.pin(self.resolve("work"))["bundle"]["revision"]
        self.assertEqual(first, second)

    def test_an_edit_after_pinning_changes_nothing_about_the_run(self):
        found = profiles.pin(self.resolve("work"))
        frozen = profiles.record("agent-1", found)
        pinned = Path(self.inputs(found)["design"]["pinned"])
        before = self.inputs(found)["design"]["sha256"]

        self.source.write_text("a completely different design\n",
                               encoding="utf-8")

        self.assertEqual(pinned.read_text(encoding="utf-8"),
                         "the original design\n")
        again = profiles.recorded("agent-1")
        self.assertEqual(
            {i["name"]: i["sha256"] for i in again["context"]},
            {"design": before},
            "a recorded manifest must not be repinned by a later edit")
        self.assertEqual(frozen["revision"], again["revision"])

    def test_an_untouched_board_is_fresh_even_with_worktree_grants(self):
        """The comparison has to ask the question the launch asked. A
        command grant runs in the worktree root by default, so a freshness
        check that re-resolved without one would refuse that grant and
        then report its own refusal as a changed policy."""
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit-tests", "run": "python3 -m unittest",
                          "root": "worktree"}],
            "context": [{"name": "design", "path": "work:reference/design.md"}]}}})
        frozen = profiles.record("agent-1", profiles.pin(self.resolve("work")))
        self.assertEqual([g["name"] for g in frozen["commands"]], ["unit-tests"])
        verdict = profiles.freshness(frozen)
        self.assertTrue(verdict["fresh"], verdict["changed"])

    def test_freshness_reports_the_edit_without_changing_the_record(self):
        frozen = profiles.record("agent-1", profiles.pin(self.resolve("work")))
        self.assertTrue(profiles.freshness(frozen)["fresh"])
        self.source.write_text("edited\n", encoding="utf-8")
        verdict = profiles.freshness(frozen)
        self.assertFalse(verdict["fresh"])
        self.assertIn("'design' has been edited", " ".join(verdict["changed"]))
        self.assertIn("run's own copy is unaffected", " ".join(verdict["changed"]))

    def test_freshness_reports_an_edited_policy_document(self):
        frozen = profiles.record("agent-1", profiles.pin(self.resolve("work")))
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "lint", "run": "ruff check"}], "context": [
            {"name": "design", "path": "work:reference/design.md"}]}}})
        verdict = profiles.freshness(frozen)
        self.assertFalse(verdict["fresh"])
        self.assertIn("resolved policy for this intent has changed",
                      " ".join(verdict["changed"]))

    def test_a_changed_source_revision_is_a_different_context_identity(self):
        first = self.inputs(self.resolve("work"))["design"]["contextId"]
        self.source.write_text("edited\n", encoding="utf-8")
        second = self.inputs(self.resolve("work"))["design"]["contextId"]
        self.assertNotEqual(first, second)

    def test_a_refused_profile_pins_nothing(self):
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "gone", "path": "work:reference/gone.md"}]}}})
        with self.assertRaises(ValueError):
            profiles.require(profiles.manifest("work", CARD, "in-progress",
                                               pin_inputs=True))
        self.assertFalse(config.CONTEXT_DIR.exists())


# ── required guarantees fail rather than degrade ───────────────────────


class RequiredGuaranteesFailClosed(ProfileCase):
    def test_a_capability_nothing_enforces_refuses_the_profile(self):
        self.declares({"v": 1, "profiles": {"work": {
            "requires": ["filesystem-sandbox"]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("filesystem-sandbox", found["blockers"][0])
        self.assertIn("exists only in the prompt", found["blockers"][0])

    def test_every_pending_capability_refuses_when_required(self):
        for word in profiles.PENDING_CAPABILITIES:
            with self.subTest(capability=word):
                self.declares({"v": 1, "profiles": {"work": {
                    "requires": [word]}}})
                self.assertTrue(self.resolve("work")["blockers"], word)

    def test_an_unknown_capability_refuses(self):
        self.declares({"v": 1, "profiles": {"work": {
            "requires": ["gpu-isolation"]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("does not know", found["blockers"][0])

    def test_a_capability_bench_itself_enforces_is_honoured(self):
        self.declares({"v": 1, "profiles": {"work": {
            "requires": ["pinned-context", "context-boundary",
                         "working-directory"]}}})
        self.assertEqual(self.resolve("work")["blockers"], [])

    def test_a_capability_the_adapter_declares_is_honoured(self):
        self.capabilities_are({"v": 1, "version": "9", "capabilities": {
            "read-only-tools": "enforced"}})
        self.declares({"v": 1, "profiles": {"review": {
            "requires": ["read-only-tools"]}}})
        self.assertEqual(self.resolve("review")["blockers"], [])

    def test_the_same_requirement_refuses_on_an_adapter_that_withholds_it(self):
        self.capabilities_are({"v": 1, "version": "9", "capabilities": {}})
        self.declares({"v": 1, "profiles": {"review": {
            "requires": ["read-only-tools"]}}})
        found = self.resolve("review")
        self.assertTrue(found["blockers"])
        self.assertIn("read-only-tools", found["blockers"][0])

    def test_an_adapters_own_reason_is_relayed(self):
        self.capabilities_are({"v": 1, "capabilities": {}, "unsupported": {
            "read-only-tools": "this vendor has no tool roster"}})
        table = self.resolve("review")["capabilities"]
        self.assertIn("no tool roster", table["read-only-tools"]["why"])

    def test_the_shipped_adapters_declare_what_they_cannot_do(self):
        for directory in (CLAUDE, OPENCODE):
            with self.subTest(adapter=directory.name):
                data = json.loads(
                    (directory / "capabilities.json").read_text(encoding="utf-8"))
                self.assertEqual(data["v"], 1)
                self.assertIn("filesystem-sandbox", data["unsupported"])
                for strength in data["capabilities"].values():
                    self.assertIn(strength, profiles.ENFORCEMENT)
                self.assertEqual(data["capabilities"]["command-allowlist"],
                                 "prefix-allowlist",
                                 "a prefix rule must not be called enforcement")


class ABrokenDocumentRefuses(ProfileCase):
    def test_invalid_json_refuses_every_intent_rather_than_falling_back(self):
        self.declares("{not json")
        for intent in profiles.INTENTS:
            found = self.resolve(intent)
            self.assertTrue(found["blockers"], intent)
            self.assertIn("not valid JSON", found["blockers"][0])
            self.assertEqual(found["commands"], [],
                             "a broken document must not resolve to the legacy "
                             "grants it was written to replace")

    def test_a_version_this_bench_does_not_read_refuses(self):
        self.declares({"v": 99, "profiles": {"work": {}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("v99", found["blockers"][0].replace('"v": 99', "v99"))
        self.assertIn("does not quietly become the old one", found["blockers"][0])

    def test_a_document_named_by_the_setting_is_used_as_named(self):
        elsewhere = self.tmp / "policies" / "bench.json"
        elsewhere.parent.mkdir()
        elsewhere.write_text(json.dumps({"v": 1, "profiles": {"work": {
            "commands": [{"name": "lint", "run": "ruff check"}]}}}),
            encoding="utf-8")
        self.patch(AGENT_PROFILES=str(elsewhere))
        found = self.resolve("work")
        self.assertEqual(self.prefixes(found), ["ruff check"])
        self.assertEqual(found["source"]["path"], str(elsewhere))

    def test_a_relative_setting_resolves_against_the_tool_not_the_cwd(self):
        (self.tool / "policies").mkdir()
        (self.tool / "policies" / "p.json").write_text(
            json.dumps({"v": 1, "profiles": {"work": {"commands": []}}}),
            encoding="utf-8")
        self.patch(AGENT_PROFILES="policies/p.json")
        self.assertEqual(profiles.document_path(),
                         self.tool / "policies" / "p.json")

    def test_a_setting_naming_nothing_refuses_rather_than_falling_back(self):
        self.patch(AGENT_PROFILES=str(self.tmp / "nope.json"))
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("could not be read", found["blockers"][0])

    def test_the_precedence_is_recorded_in_order(self):
        self.assertEqual(self.resolve("work")["source"]["precedence"][0],
                         "BOARD_AGENT_PROFILES")
        self.assertIn("BOARD_AGENT_COMMANDS",
                      self.resolve("work")["source"]["precedence"][-1])


# ── the four things that used to be one thing ──────────────────────────


class FourSeparateQuestions(ProfileCase):
    """UI registration, agent capability, prompt instruction and gate
    requirement are different facts, and the data says which is which."""

    def test_a_ui_command_grants_a_headless_run_nothing(self):
        chore = self.local / "commands" / "reseed"
        chore.write_text("#!/bin/sh\n# help: reseed the database\n",
                         encoding="utf-8")
        chore.chmod(chore.stat().st_mode | stat.S_IEXEC)
        found = self.resolve("work")
        registered = {c["name"]: c for c in found["uiCommands"]}
        self.assertIn("reseed", registered)
        self.assertFalse(registered["reseed"]["agentCapability"])
        self.assertEqual(registered["reseed"]["help"], "reseed the database")
        self.assertNotIn("reseed", self.grants(found))
        self.assertNotIn("reseed", " ".join(self.prefixes(found)))

    def test_a_verification_requirement_is_a_check_not_a_grant(self):
        """Card 68 made `verify` real, and the separation survived it: a
        check is something *bench* runs, and declaring one still grants
        the run nothing but the shim that asks for it."""
        self.declares({"v": 1,
                       "checks": [{"name": "unit-tests",
                                   "run": "python3 -m unittest",
                                   "help": "the definition of done"}],
                       "profiles": {"work": {"verify": ["unit-tests"]}}})
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertEqual([v["name"] for v in found["verify"]], ["unit-tests"])
        self.assertEqual(found["verify"][0]["status"], "available")
        self.assertEqual(found["verify"][0]["enforcement"], "enforced")
        self.assertEqual(self.grants(found), ["bench-verify"],
                         "declaring a check grants the shim that asks for "
                         "it, and not the command itself")
        self.assertNotIn("python3 -m unittest", " ".join(self.prefixes(found)))

    def test_a_verification_naming_nothing_refuses(self):
        """The one behaviour 67 could not have: a requirement that names
        nothing used to resolve `pending`, which is indistinguishable from
        a check that will never run."""
        self.declares({"v": 1, "profiles": {"work": {
            "verify": ["coverage"]}}})
        found = self.resolve("work")
        self.assertTrue(found["blockers"])
        self.assertIn("coverage", found["blockers"][0])
        self.assertIn("never ran", found["blockers"][0])

    def test_a_preparation_requirement_is_run_before_the_check(self):
        self.declares({"v": 1,
                       "prepare": [{"name": "migrate",
                                    "run": "python3 -c pass"}],
                       "checks": [{"name": "unit-tests",
                                   "run": "python3 -m unittest",
                                   "prepare": ["migrate"]}],
                       "profiles": {"work": {"verify": ["unit-tests"]}}})
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertEqual([p["name"] for p in found["verify"][0]["prepare"]],
                         ["migrate"])
        self.assertIn("prepared-inputs", found["guarantees"]["enforced"])

    def test_a_grant_may_be_promoted_to_a_check_by_name(self):
        """The documented compatibility path, which is what keeps a
        document written for 67 working: a `verify` entry naming one of
        the intent's own command grants resolves to a check built from
        it."""
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit-tests", "run": "python3 -m unittest"}],
            "verify": [{"name": "unit-tests"}]}}})
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertEqual([v["name"] for v in found["verify"]], ["unit-tests"])
        self.assertIn("promoted", found["verify"][0]["source"])
        self.assertEqual(found["verify"][0]["argv"], ["-m", "unittest"])

    def test_a_pending_requirement_is_never_reported_as_a_guarantee(self):
        self.declares({"v": 1, "profiles": {"work": {
            "verify": [{"name": "unit-tests"}]}}})
        guarantees = self.resolve("work")["guarantees"]
        self.assertNotIn("verified-outputs", guarantees["enforced"])
        self.assertNotIn("verified-outputs", guarantees["prefixMatched"])

    def test_guarantees_separate_configured_from_enforced(self):
        (self.work / "reference" / "d.md").write_text("x\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "tests", "run": "python3 -m unittest"}],
            "context": [{"name": "d", "path": "work:reference/d.md"}]}}})
        guarantees = self.resolve("work")["guarantees"]
        self.assertIn("command-allowlist", guarantees["configured"])
        self.assertIn("pinned-context", guarantees["configured"])
        # pinning is bench's own, and really is enforced
        self.assertIn("pinned-context", guarantees["enforced"])
        self.assertIn("working-directory", guarantees["enforced"])
        # a prefix rule is not
        self.assertIn("command-allowlist", guarantees["prefixMatched"])

    def test_the_capability_table_names_who_answers_for_each(self):
        table = self.resolve("work")["capabilities"]
        self.assertEqual(table["pinned-context"]["by"], "bench")
        self.assertIn("adapter", table["command-allowlist"]["by"])
        self.assertEqual(table["filesystem-sandbox"]["enforcement"], "pending")
        for entry in table.values():
            self.assertIn(entry["enforcement"], profiles.ENFORCEMENT)
            self.assertTrue(entry["why"])

    def test_the_things_bench_enforces_claim_only_what_they_do(self):
        """The reason to read these sentences in a test: each is a claim a
        reader will rely on, and the narrow truth is narrower than the
        obvious phrasing. A copy stops the *source* changing; it does not
        confine the run. A cwd is where a process starts, not a cage."""
        table = self.resolve("work")["capabilities"]
        pinned = table["pinned-context"]["why"]
        self.assertIn("edit to the source cannot change", pinned)
        self.assertIn("could still alter its own copy", pinned)
        self.assertIn("not a boundary it is held inside",
                      table["working-directory"]["why"])


# ── identities and the seams later members use ─────────────────────────


class IdentitiesAndSeams(unittest.TestCase):
    def test_a_policy_identity_names_its_intent(self):
        self.assertTrue(profiles.policy_id("work", "a" * 64)
                        .startswith("profile:work@"))

    def test_a_context_identity_changes_with_the_content(self):
        self.assertNotEqual(profiles.context_id("brief", "a" * 64),
                            profiles.context_id("brief", "b" * 64))

    def test_a_request_identity_is_unique_per_resolution(self):
        first = profiles.request_id("work", "01-x.md")
        time.sleep(0.002)
        self.assertNotEqual(first, profiles.request_id("work", "01-x.md"))

    def test_an_evidence_identity_is_stable_and_typed(self):
        one = profiles.evidence_id("verification", "req:work:01@abc", "unit-tests")
        self.assertEqual(one, profiles.evidence_id(
            "verification", "req:work:01@abc", "unit-tests"))
        with self.assertRaises(ValueError):
            profiles.evidence_id("vibes", "req:work:01@abc")

    def test_the_seams_name_every_pending_capability(self):
        seams = profiles.seams()
        self.assertEqual(set(seams["pending"]),
                         set(profiles.PENDING_CAPABILITIES))
        self.assertEqual(set(seams["evidenceKinds"]),
                         set(profiles.EVIDENCE_KINDS))
        for who in ("a runner", "a doctor", "a gate", "a reviewer"):
            self.assertIn(who, seams["extend"])
        self.assertEqual(set(seams["identities"]),
                         {"policy", "context", "request", "evidence"})

    def test_the_shipped_example_document_matches_the_schema(self):
        """The portable example is documentation that has to stay true:
        it declares the version the resolver reads and every capability it
        requires is one bench knows."""
        data = json.loads((REPO / "manager" / "core" / "profiles.example.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["v"], profiles.PROFILE_VERSION)
        self.assertEqual(set(data["profiles"]), set(profiles.INTENTS))
        known = set(profiles.BENCH_CAPABILITIES) | set(
            profiles.ADAPTER_CAPABILITIES) | set(profiles.PENDING_CAPABILITIES)
        for word in data["requires"]:
            self.assertIn(word, known)
        for profile in data["profiles"].values():
            for word in profile.get("deny", []):
                self.assertIn(word, profiles.DENIALS)


# ── what leaves the module ─────────────────────────────────────────────


class NothingPrivateTravels(ProfileCase):
    def test_public_carries_identities_and_never_content(self):
        source = self.work / "reference" / "design.md"
        source.write_text("A-VERY-PRIVATE-DESIGN\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "design", "path": "work:reference/design.md"}]}}})
        found = profiles.pin(self.resolve("work"))
        shown = json.dumps(profiles.public(found))
        self.assertNotIn("A-VERY-PRIVATE-DESIGN", shown)
        self.assertIn("design", shown)
        self.assertIn(self.inputs(found)["design"]["sha256"], shown)

    def test_public_is_an_allowlist(self):
        found = self.resolve("work")
        found["smuggled"] = "a credential"
        found["context"] = [{"name": "x", "contents": "a credential"}]
        shown = profiles.public(found)
        self.assertNotIn("smuggled", shown)
        self.assertNotIn("contents", shown["context"][0])

    def test_public_of_nothing_is_nothing(self):
        self.assertIsNone(profiles.public(None))

    def test_the_summary_is_the_compact_reading_a_row_wears(self):
        (self.work / "reference" / "d.md").write_text("x\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "tests", "run": "python3 -m unittest"}],
            "context": [{"name": "d", "path": "work:reference/d.md"}]}}})
        summary = profiles.summary(self.resolve("work"))
        self.assertEqual(summary["intent"], "work")
        self.assertEqual(summary["commands"], 1)
        self.assertEqual(summary["inputs"], 1)
        self.assertEqual(summary["readable"], 1)
        self.assertEqual(summary["sourceKind"], "file")
        self.assertEqual(summary["blockers"], [])

    def test_the_adapter_is_told_the_policy_and_the_paths_only(self):
        (self.work / "reference" / "d.md").write_text("PRIVATE\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "tests", "run": "python3 -m unittest"}],
            "context": [{"name": "d", "path": "work:reference/d.md"}]}}})
        found = profiles.pin(self.resolve("work"))
        policy = profiles.adapter_policy(found)
        self.assertEqual(set(policy), {"v", "intent", "policyId", "cwd",
                                       "commands", "deny", "publication",
                                       "requires"})
        self.assertNotIn("PRIVATE", json.dumps(policy))
        handed = profiles.adapter_context(found)
        self.assertEqual([i["name"] for i in handed], ["d"])
        self.assertNotIn("PRIVATE", json.dumps(handed))
        self.assertTrue(handed[0]["path"].startswith(str(config.CONTEXT_DIR)))


# ── the launch itself ──────────────────────────────────────────────────


class TheLaunchRefusesBeforeTheModel(ProfileCase):
    def test_a_missing_required_input_refuses_and_leaves_nothing_behind(self):
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "design", "path": "work:reference/design.md"}]}}})
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("'design'", str(caught.exception))
        self.assertNothingStarted()

    def test_an_escaping_input_refuses_the_launch(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not approved\n", encoding="utf-8")
        (self.work / "reference" / "link.md").symlink_to(outside)
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "escape", "path": "work:reference/link.md"}]}}})
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertNothingStarted()

    def test_an_unenforceable_requirement_refuses_the_launch(self):
        self.declares({"v": 1, "profiles": {"work": {
            "requires": ["filesystem-sandbox"]}}})
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("filesystem-sandbox", str(caught.exception))
        self.assertNothingStarted()

    def test_a_bad_command_grant_refuses_the_launch(self):
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "tests", "run": "make test && curl evil.test"}]}}})
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertNothingStarted()

    def test_a_relevance_check_refuses_the_same_way(self):
        self.declares({"v": 1, "profiles": {"review": {"context": [
            {"name": "checklist", "path": "work:reference/checklist.md"}]}}})
        with self.assertRaises(ValueError) as caught:
            agents.start_review(CARD, "in-progress")
        self.assertIn("'checklist'", str(caught.exception))
        self.assertEqual(agents.list_public(), [])
        self.assertFalse(self.launch_log.exists())

    def test_a_refusal_does_not_claim_the_card(self):
        self.patch(COMMIT_MOVES=True)
        self.declares({"v": 1, "profiles": {"work": {
            "requires": ["filesystem-sandbox"]}}})
        before = (self.tasks / "in-progress" / CARD).read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            agents.start_agent(CARD, "in-progress")
        self.assertEqual((self.tasks / "in-progress" / CARD)
                         .read_text(encoding="utf-8"), before)
        self.assertNotIn("**Assignee:**", before)


class TheLaunchRecordsWhatItRode(ProfileCase):
    def setUp(self):
        super().setUp()
        self.source = self.work / "reference" / "design.md"
        self.source.write_text("the design\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit-tests", "run": "python3 -m unittest"}],
            "context": [{"name": "design", "path": "work:reference/design.md"}],
            "verify": [{"name": "unit-tests"}]}}})

    def test_the_manifest_is_frozen_with_the_run(self):
        record = self.run_agent()
        frozen = profiles.recorded(record["id"])
        self.assertIsNotNone(frozen)
        self.assertEqual(frozen["intent"], "work")
        self.assertEqual(frozen["task"], CARD)
        self.assertEqual(frozen["agent"], record["id"])
        self.assertNotIn("blockers", frozen)
        self.assertEqual([i["status"] for i in frozen["context"]], ["pinned"])
        self.assertTrue(frozen["requestId"].startswith("req:work:"))

    def test_the_run_row_wears_the_compact_reading(self):
        record = self.run_agent()
        public = [a for a in agents.list_public() if a["id"] == record["id"]][0]
        self.assertEqual(public["profile"]["intent"], "work")
        # the project's own grant, and bench's shim for the declared check
        self.assertEqual(public["profile"]["commands"], 2)
        self.assertEqual(public["profile"]["checks"], ["unit-tests"])
        self.assertEqual(public["profile"]["readable"], 1)
        self.assertNotIn("manifest", public,
                         "the whole manifest belongs behind /api/manifest, "
                         "not in every state payload")

    def test_the_adapter_was_handed_the_policy_and_the_pinned_inputs(self):
        self.run_agent()
        env = self.launched()
        policy = json.loads(env["AGENT_PROFILE"])
        self.assertEqual(policy["intent"], "work")
        self.assertEqual([g["prefix"] for g in policy["commands"]],
                         ["python3 -m unittest",
                          str(profiles.verify_shim())])
        self.assertIn("push", policy["deny"])
        handed = json.loads(env["AGENT_CONTEXT"])
        self.assertEqual([i["name"] for i in handed], ["design"])
        self.assertTrue(Path(handed[0]["path"]).is_file())

    def test_agent_commands_still_reaches_the_adapter_verbatim(self):
        """The compatibility seam at the launch: an adapter override that
        has never heard of profiles is handed the environment it always
        was, so it keeps working untouched."""
        self.run_agent()
        self.assertEqual(self.launched()["AGENT_COMMANDS"],
                         config.AGENT_COMMANDS)

    def test_the_prompt_names_the_pinned_input(self):
        self.run_agent()
        prompt = self.launched()["prompt"]
        self.assertIn("design", prompt)
        self.assertIn("read-only inputs", prompt)
        self.assertIn("Read AGENTS.md at the repo root first", prompt,
                      "the protected workflow brief is not replaced")

    def test_the_ticker_names_the_policy_without_quoting_anything(self):
        self.run_agent()
        summaries = " ".join(e.get("summary", "") for e in state.BOARD_EVENTS)
        self.assertIn("profile profile:work@", summaries)
        self.assertIn("2 named command grants", summaries)
        self.assertIn("1 pinned input", summaries)

    def test_a_legacy_board_says_nothing_about_policy_in_the_ticker(self):
        (self.local / "profiles.json").unlink()
        self.run_agent()
        summaries = " ".join(e.get("summary", "") for e in state.BOARD_EVENTS)
        self.assertNotIn("profile profile:", summaries)

    def test_the_document_changing_mid_run_does_not_repin_the_run(self):
        record = self.run_agent()
        frozen = profiles.recorded(record["id"])
        self.declares({"v": 1, "profiles": {"work": {"commands": [
            {"name": "lint", "run": "ruff check"}]}}})
        again = profiles.recorded(record["id"])
        self.assertEqual(again["revision"], frozen["revision"])
        self.assertEqual([g["name"] for g in again["commands"]],
                         ["unit-tests", "bench-verify"])
        self.assertFalse(profiles.freshness(again)["fresh"])

    def test_a_manifest_outlives_the_card_until_someone_drops_it(self):
        """Unlike a plan, which stops describing anything once the work
        merges, a manifest is the record of what one run was granted — so
        nothing removes it on the card's behalf, and `forget` is the
        explicit way out."""
        record = self.run_agent()
        self.assertIsNotNone(profiles.recorded(record["id"]))
        self.assertTrue(profiles.forget(record["id"]))
        self.assertIsNone(profiles.recorded(record["id"]))
        self.assertFalse(profiles.forget(record["id"]),
                         "forgetting twice is not an error worth raising")

    def test_a_launch_that_fails_on_git_leaves_no_worktree_behind(self):
        """The pin is before the worktree, so the ordering can be checked
        from the other side: a refusal at the git step must not have left a
        checkout, and the content-addressed bundle it already wrote is
        reused rather than cleaned up."""
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        (config.WORKTREES / STEM).mkdir()
        (config.WORKTREES / STEM / "squatter.txt").write_text("x\n",
                                                              encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn("not a git", str(caught.exception))
        self.assertEqual(agents.list_public(), [])

    def test_act_pr_and_review_record_their_own_intents(self):
        self.declares({"v": 1, "profiles": {
            "review": {"context": [
                {"name": "design", "path": "work:reference/design.md"}]}}})
        record = agents.start_review(CARD, "in-progress")
        self.assertTrue(wait_for(
            lambda: state.AGENTS[record["id"]]["status"] != "running"))
        frozen = profiles.recorded(record["id"])
        self.assertEqual(frozen["intent"], "review")
        self.assertEqual(frozen["commands"], [])
        self.assertEqual(frozen["cwd"]["path"], str(self.repo))


# ── what the board serves ──────────────────────────────────────────────


def _write_stub(directory: Path, name: str, script: str) -> Path:
    stub = directory / name
    stub.write_text(script, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


class ThePolicyReachesTheVendor(unittest.TestCase):
    """The last seam: `AGENT_PROFILE` has to survive the `run` script and
    land in the generated permission document, or every refusal above is
    bookkeeping. Exercised against stub binaries — the same
    BOARD_CLAUDE_BIN / BOARD_OPENCODE_BIN seam a live board uses."""

    POLICY = {
        "v": 1, "intent": "work", "policyId": "profile:work@deadbeefcafe",
        "cwd": {"policy": "worktree", "path": "/tmp/wt"},
        "commands": [{"name": "lint", "prefix": "ruff check",
                      "root": "worktree"}],
        "deny": ["push", "publish", "reshape-lifecycle"],
        "publication": {"commit": True, "push": False, "publish": False},
        "requires": [],
    }

    def _env(self, tmp: Path, **extra) -> dict:
        env = dict(os.environ)
        env.pop("AGENT_MODEL", None)
        env.pop("AGENT_PROFILE", None)
        env.update({"AGENT_PROMPT": "do the task", "AGENT_MODE": "work",
                    "AGENT_COMMANDS": "python3 -m unittest"})
        env.update(extra)
        return env

    def test_the_claude_launch_carries_the_named_grant_and_the_denials(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "args.json"
            stub = _write_stub(Path(tmp), "claude-stub",
                               "#!/usr/bin/env python3\n"
                               "import json, sys\n"
                               f"open({str(capture)!r}, 'w')"
                               ".write(json.dumps(sys.argv[1:]))\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            result = subprocess.run(
                ["bash", str(CLAUDE / "run")],
                env=self._env(Path(tmp), BOARD_CLAUDE_BIN=str(wrapper),
                              AGENT_PROFILE=json.dumps(self.POLICY)),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(capture.read_text(encoding="utf-8"))
            perms = json.loads(args[args.index("--settings") + 1])["permissions"]
            # the profile's named grant replaced the legacy prefix list
            self.assertIn("Bash(ruff check:*)", perms["allow"])
            self.assertNotIn("Bash(python3 -m unittest:*)", perms["allow"])
            # and the denials beat the allows
            for prefix in ("git push", "gh pr merge", "git worktree"):
                self.assertIn(f"Bash({prefix}:*)", perms["deny"])

    def test_the_opencode_launch_carries_the_same_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "capture.json"
            stub = _write_stub(
                Path(tmp), "opencode-stub",
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "payload = {'argv': sys.argv[1:],\n"
                "           'config': json.load(open(os.environ['OPENCODE_CONFIG']))}\n"
                f"open({str(capture)!r}, 'w').write(json.dumps(payload))\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            result = subprocess.run(
                ["bash", str(OPENCODE / "run")],
                env=self._env(Path(tmp), BOARD_OPENCODE_BIN=str(wrapper),
                              AGENT_PROFILE=json.dumps(self.POLICY)),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            bash = json.loads(capture.read_text(encoding="utf-8")
                              )["config"]["permission"]["bash"]
            self.assertEqual(bash["ruff check *"], "allow")
            self.assertNotIn("python3 -m unittest *", bash)
            self.assertEqual(bash["git push *"], "deny")

    def test_a_malformed_policy_is_not_read_as_an_empty_deny_list(self):
        """A value that does not parse means "no profile", which is the
        pre-profile stance — never "nothing is denied"."""
        for raw in ("{not json", "[]", "   "):
            with self.subTest(raw=raw):
                self.assertIsNone(hook_settings.load_profile(raw))
                self.assertIsNone(permission_config.load_profile(raw))


class TheApiServesOneResolver(ProfileCase):
    def test_the_state_payload_names_the_policy_in_force(self):
        self.declares({"v": 1, "profiles": {"work": {}}})
        policy = httpd.policy_payload()
        self.assertEqual(policy["kind"], "file")
        self.assertEqual(policy["identity"], "manager/local/profiles.json")
        self.assertEqual(policy["intents"], list(profiles.INTENTS))
        self.assertIsNone(policy["problem"])
        self.assertEqual(policy["adapter"]["name"], config.ADAPTER)

    def test_a_legacy_board_says_so(self):
        policy = httpd.policy_payload()
        self.assertEqual(policy["kind"], "legacy")
        self.assertIsNone(policy["identity"])

    def test_a_broken_document_reaches_the_page_as_a_problem(self):
        self.declares("{nope")
        self.assertIn("not valid JSON", httpd.policy_payload()["problem"])

    def test_the_resolved_profile_the_page_shows_is_the_one_a_launch_rides(self):
        (self.work / "reference" / "d.md").write_text("x\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "tests", "run": "python3 -m unittest"}],
            "context": [{"name": "d", "path": "work:reference/d.md"}]}}})
        shown = profiles.public(profiles.manifest("work", CARD, "in-progress"))
        record = self.run_agent()
        rode = profiles.recorded(record["id"])
        self.assertEqual(shown["revision"], rode["revision"])
        self.assertEqual(shown["policyId"], rode["policyId"])
        self.assertEqual([g["prefix"] for g in shown["commands"]],
                         [g["prefix"] for g in rode["commands"]])

    def test_the_page_reading_pins_nothing(self):
        (self.work / "reference" / "d.md").write_text("x\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "d", "path": "work:reference/d.md"}]}}})
        profiles.public(profiles.manifest("work", CARD, "in-progress"))
        self.assertFalse(config.CONTEXT_DIR.exists())

    def test_a_blocked_profile_is_served_with_its_blockers_and_remedies(self):
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "design", "path": "work:reference/design.md"}]}}})
        shown = profiles.public(profiles.manifest("work", CARD, "in-progress"))
        self.assertTrue(shown["blockers"])
        self.assertIn("mark the selection", shown["blockers"][0])


# ── every layout, and what an install carries across an update ─────────


class EveryLayoutResolvesTheSame(ProfileCase):
    """The resolution cases that could plausibly move with the layout, run
    under each of the three. Which repository a root *is* changes; what a
    run is allowed to do must not."""

    def _document(self) -> None:
        (self.work / "reference" / "design.md").write_text(
            "the design\n", encoding="utf-8")
        (self.local / "AGENTS.md").write_text("the brief\n", encoding="utf-8")
        self.declares({"v": 1, "profiles": {"work": {
            "commands": [{"name": "unit-tests", "run": "python3 -m unittest"}],
            "context": [
                {"name": "design", "path": "work:reference/design.md"},
                {"name": "brief", "path": "tool:manager/local/AGENTS.md"}]}}})

    def test_the_named_roots_are_the_resolved_ones(self):
        found = self.resolve("work")
        self.assertEqual(found["roots"]["app"], str(self.repo))
        self.assertEqual(found["roots"]["work"], str(self.work))
        self.assertEqual(found["roots"]["tool"], str(self.tool))

    def test_a_document_resolves_its_inputs_against_those_roots(self):
        self._document()
        found = self.resolve("work")
        self.assertEqual(found["blockers"], [])
        self.assertEqual(sorted(self.inputs(found)), ["brief", "design"])
        for item in found["context"]:
            self.assertTrue(item["readable"], item["name"])

    def test_the_legacy_path_is_the_legacy_path_everywhere(self):
        self.assertEqual(self.resolve("work")["source"]["kind"], "legacy")
        self.assertEqual(self.prefixes(self.resolve("work")),
                         ["python3 -m unittest"])

    def test_a_launch_records_and_pins_under_the_tool(self):
        self._document()
        record = self.run_agent()
        frozen = profiles.recorded(record["id"])
        self.assertEqual(len(frozen["context"]), 2)
        for item in frozen["context"]:
            self.assertEqual(item["status"], "pinned")
            self.assertTrue(Path(item["pinned"]).is_relative_to(self.local),
                            "a pinned bundle belongs with the tool, never in "
                            "the application or the shared ticket history")

    def test_escapes_are_measured_against_the_root_this_layout_gives(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not approved\n", encoding="utf-8")
        (self.work / "reference" / "link.md").symlink_to(outside)
        self.declares({"v": 1, "profiles": {"work": {"context": [
            {"name": "escape", "path": "work:reference/link.md"}]}}})
        self.assertTrue(self.resolve("work")["blockers"])


class AVendoredInstall(EveryLayoutResolvesTheSame):
    LAYOUT = "vendored"


class ASelfHostedCheckout(EveryLayoutResolvesTheSame):
    LAYOUT = "flat"


class WhatAnInstallCarries(unittest.TestCase):
    """Clean reinstallation and update expectations: the resolver ships,
    the example ships, and a project's own policy survives an update that
    replaces `manager/core/` wholesale."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = (REPO / "manager" / "core" / "release-manifest"
                        ).read_text(encoding="utf-8")

    def test_the_resolver_and_its_example_ship_with_core(self):
        self.assertIn("tree manager/core", self.manifest,
                      "manager/core ships wholesale, which is how "
                      "profiles.py and profiles.example.json reach an install")
        for name in ("profiles.py", "profiles.example.json"):
            self.assertTrue((REPO / "manager" / "core" / name).is_file(), name)

    def test_the_shipped_adapters_carry_their_declarations(self):
        for directory in ("claude", "opencode"):
            self.assertTrue((REPO / "manager" / "core" / "adapters" /
                             directory / "capabilities.json").is_file(),
                            directory)

    def test_no_project_policy_is_ever_shipped(self):
        """A document names roots on one disk. Shipping one would point a
        fresh install at somebody else's machine."""
        self.assertNotIn("profiles.json", self.manifest.replace(
            "profiles.example.json", ""))
        self.assertFalse((REPO / "manager" / "local" / "profiles.json").exists(),
                         "bench's own repository must not carry one either")

    def test_a_projects_own_policy_is_gitignored(self):
        """It is machine-local, like local/.env — and in a vendored
        install `manager/local/` sits inside the application, so this is
        what keeps a policy out of the application's own history."""
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn("manager/local/profiles.json", ignored)

    def test_an_update_replaces_core_and_never_local(self):
        classes = {}
        for line in self.manifest.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, _, path = line.partition(" ")
            classes[path] = kind
        self.assertEqual(classes["manager/core"], "tree")
        for path, kind in classes.items():
            if path.startswith("manager/local"):
                self.assertIn(kind, ("keep", "seed", "once"),
                              f"{path} would be replaced by an update")

    def test_the_settings_example_documents_the_new_key(self):
        text = (REPO / "manager" / "core" / ".env.example"
                ).read_text(encoding="utf-8")
        self.assertIn("\nBOARD_AGENT_PROFILES=", text)
        self.assertIn("profiles.example.json", text)
        self.assertIn("BOARD_AGENT_COMMANDS plus the adapter's", text)


if __name__ == "__main__":
    unittest.main()
