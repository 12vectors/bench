"""Configuring agent harnesses, models and prompts in Settings (task 77).

What is held here:

- **One resolver.** `config.agent_choice()` is what the manifest, the
  preflight, the doctor, the Settings page and `_launch` all read; a board
  with no new overrides resolves exactly as the global-only settings did.
- **Per-purpose harnesses reach the launch.** Implementation and reviews
  on different installed adapters are resolved, capability-checked,
  diagnosed and launched on their own harness, with their own model; an
  uninstalled harness refuses before any side effect.
- **Templates are held to their contract**, before save and before launch,
  and task content is inserted as data.
- **Saving is safe.** Only the agent keys of `.env` and the four named
  prompt overrides are written; locked keys, invalid input and stale
  revisions write nothing; a failed write puts everything back; a reset is
  recoverable; nothing is launched.

    python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import agentsettings  # noqa: E402
import config  # noqa: E402
import doctor  # noqa: E402
import profiles  # noqa: E402
import state  # noqa: E402
import templates  # noqa: E402

from tests.test_execution_profiles import (CARD, CLAUDE, ProfileCase,  # noqa: E402
                                           card, git, wait_for)

# A stub harness: records what it was handed — the whole environment's
# agent-related keys and the prompt — and commits so a work run reaches
# review/ like a real one.
RECORDS = """#!/usr/bin/env python3
import json, os, subprocess, sys
cwd = os.environ["AGENT_CWD"]
if os.environ.get("AGENT_MODE") in ("work", "act-pr") and os.path.exists(os.path.join(cwd, ".git")):
    open(os.path.join(cwd, "done.txt"), "a").write("the work\\n")
    subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
    subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                    "commit", "-q", "-m", "did it"], check=True)
keep = {k: v for k, v in os.environ.items()
        if k.startswith(("AGENT_", "BOARD_AGENT_"))}
keep["harness"] = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "launch-" + os.environ["AGENT_MODE"] + ".json"), "w").write(json.dumps(keep))
print("WORK REPORT: did it")
print("RELEVANCE REVIEW: Still relevant")
"""


class AgentCase(ProfileCase):
    """ProfileCase, with the agent settings read from this test's own
    `.env` — the process environment is neutralised so a leaky shell
    cannot decide what is resolved."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "_ENV_LOCKED", config._ENV_LOCKED)
        config._ENV_LOCKED = {}
        # the module attributes back to what they started as, so no
        # in-process override is read either
        self.patch(ADAPTER=config._BOOT["ADAPTER"],
                   AGENT_MODEL=config._BOOT["AGENT_MODEL"],
                   AGENT_MODELS=config._BOOT["AGENT_MODELS"])
        self.patch(STATE=self.state)
        caps = json.loads((CLAUDE / "capabilities.json").read_text("utf-8"))
        # The default stub stands in for `claude` (ProfileCase put it at
        # local/adapters/claude); two more harnesses beside it.
        for name in ("alpha", "beta"):
            self.harness(name, caps)
        self.harness(config.ADAPTER, caps)

    def harness(self, name: str, caps: dict | None = None) -> Path:
        directory = self.local / "adapters" / name
        directory.mkdir(parents=True, exist_ok=True)
        run = directory / "run"
        run.write_text(RECORDS, encoding="utf-8")
        run.chmod(run.stat().st_mode | stat.S_IEXEC)
        if caps is not None:
            (directory / "capabilities.json").write_text(json.dumps(caps),
                                                         encoding="utf-8")
        return directory

    def env_file(self, text: str) -> Path:
        path = self.local / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def launched(self, harness: str, mode: str) -> dict:
        path = self.local / "adapters" / harness / f"launch-{mode}.json"
        self.assertTrue(wait_for(path.is_file), f"{harness} never launched {mode}")
        return json.loads(path.read_text("utf-8"))


class OneResolver(AgentCase):

    def test_no_overrides_resolve_exactly_as_the_global_settings_did(self):
        self.env_file("BOARD_AGENT_MODEL=big\nBOARD_AGENT_MODEL_REVIEW=cheap\n")
        for intent in ("work", "act-pr", "review", "refine"):
            choice = config.agent_choice(intent)
            self.assertEqual(choice["harness"]["value"], "claude")
            self.assertTrue(choice["harness"]["inherited"])
        self.assertEqual(config.agent_model("work"), "big")
        self.assertEqual(config.agent_model("act-pr"), "big")
        self.assertEqual(config.agent_model("review"), "cheap")
        self.assertEqual(config.agent_model("refine"), "big")

    def test_nothing_set_means_harness_default_for_every_intent(self):
        for intent in ("work", "act-pr", "review"):
            model = config.agent_choice(intent)["model"]
            self.assertEqual(model["value"], "")
            self.assertTrue(model["harnessDefault"])

    def test_a_per_purpose_harness_overrides_the_default_for_that_purpose_only(self):
        self.env_file("BOARD_AGENT_ADAPTER=alpha\nBOARD_AGENT_ADAPTER_REVIEW=beta\n"
                      "BOARD_AGENT_ADAPTER_WORK=\n")
        self.assertEqual(config.agent_choice("work")["harness"]["value"], "alpha")
        self.assertTrue(config.agent_choice("work")["harness"]["inherited"])
        review = config.agent_choice("review")["harness"]
        self.assertEqual((review["value"], review["inherited"], review["setting"]),
                         ("beta", False, "BOARD_AGENT_ADAPTER_REVIEW"))
        # refine has no harness override: it rides the default
        self.assertEqual(config.agent_choice("refine")["harness"]["value"], "alpha")

    def test_a_saved_change_reaches_the_next_resolution_without_a_restart(self):
        self.env_file("BOARD_AGENT_MODEL=one\n")
        self.assertEqual(config.agent_model("work"), "one")
        self.env_file("BOARD_AGENT_MODEL=two\n")
        self.assertEqual(config.agent_model("work"), "two")

    def test_the_environment_is_locked_and_beats_the_file(self):
        self.env_file("BOARD_AGENT_MODEL=from-file\n")
        config._ENV_LOCKED = {"BOARD_AGENT_MODEL": "from-env"}
        found = config.agent_setting("BOARD_AGENT_MODEL")
        self.assertEqual((found["value"], found["source"], found["locked"]),
                         ("from-env", "environment", True))

    def test_local_adapters_beat_core_and_are_listed_once(self):
        listed = {h["name"]: h for h in config.adapters_installed()}
        self.assertEqual(listed["claude"]["where"], "local")
        self.assertTrue(listed["claude"]["shadows"])
        self.assertEqual(listed["opencode"]["where"], "core")
        self.assertEqual(config.adapter_dir("claude"),
                         self.local / "adapters" / "claude")

    def test_a_harness_name_is_never_a_path(self):
        for name in ("../core/adapters/claude", "/bin", "", ".hidden"):
            self.assertIsNone(config.adapter_dir(name), name)


class PerPurposeHarnesses(AgentCase):

    def setUp(self):
        super().setUp()
        caps = json.loads((CLAUDE / "capabilities.json").read_text("utf-8"))
        # beta cannot enforce read-only tools: a refinement refuses on it,
        # which is how the test sees capabilities were read *per harness*
        beta = dict(caps, capabilities={k: v for k, v in caps["capabilities"].items()
                                        if k != "read-only-tools"})
        self.harness("beta", beta)
        self.env_file("BOARD_AGENT_ADAPTER=alpha\nBOARD_AGENT_ADAPTER_REVIEW=beta\n"
                      "BOARD_AGENT_MODEL=m-default\nBOARD_AGENT_MODEL_REVIEW=m-review\n")

    def test_preflight_capabilities_and_diagnostics_agree_per_intent(self):
        work = profiles.manifest("work", CARD, "in-progress")
        review = profiles.manifest("review", CARD, "in-progress")
        self.assertEqual(work["adapter"]["name"], "alpha")
        self.assertEqual(work["selection"]["model"]["value"], "m-default")
        self.assertEqual(review["adapter"]["name"], "beta")
        self.assertEqual(review["selection"]["model"]["value"], "m-review")
        self.assertEqual(review["capabilities"]["read-only-tools"]["enforcement"],
                         "pending")
        self.assertEqual(work["capabilities"]["read-only-tools"]["enforcement"],
                         "enforced")
        inspected = doctor.inspect()
        rows = {f["name"]: f for f in inspected["findings"]
                if f["name"].startswith("harness:")}
        self.assertIn("alpha", rows["harness:work"]["detail"])
        self.assertIn("m-default", rows["harness:work"]["detail"])
        self.assertIn("beta", rows["harness:review"]["detail"])
        self.assertIn("m-review", rows["harness:review"]["detail"])
        page = agentsettings.rows()
        self.assertEqual(page["review"]["effective"]["adapter"]["name"], "beta")
        self.assertEqual(page["work"]["effective"]["adapter"]["name"], "alpha")

    def test_work_and_review_launch_on_their_own_harness_and_model(self):
        agents.start_agent(CARD, "in-progress")
        work = self.launched("alpha", "work")
        self.assertEqual(work["AGENT_MODEL"], "m-default")
        self.assertEqual(work["harness"], "alpha")
        # the agent settings themselves never travel
        self.assertFalse([k for k in work if k in config.AGENT_KEYS], work.keys())
        self.write(card("67 — Resolve execution profiles"), stage="to-do")
        agents.start_review(CARD, "to-do")
        review = self.launched("beta", "review")
        self.assertEqual(review["AGENT_MODEL"], "m-review")

    def test_an_inherited_model_is_absent_not_empty(self):
        self.env_file("BOARD_AGENT_ADAPTER=alpha\n")
        agents.start_agent(CARD, "in-progress")
        self.assertNotIn("AGENT_MODEL", self.launched("alpha", "work"))

    def test_the_record_keeps_harness_model_and_template_identity(self):
        record = agents.start_agent(CARD, "in-progress")
        self.launched("alpha", "work")
        frozen = profiles.recorded(record["id"])
        self.assertEqual(frozen["selection"]["harness"]["name"], "alpha")
        self.assertEqual(frozen["template"]["name"], "work.md")
        self.assertEqual(frozen["template"]["source"], "built-in")
        self.assertEqual(frozen["template"]["sha256"],
                         templates.digest(templates.builtin("work.md")))
        self.assertNotIn("text", frozen["template"])
        # a later change does not repin it; freshness says what moved
        self.env_file("BOARD_AGENT_ADAPTER=claude\n")
        self.assertEqual(profiles.recorded(record["id"])["selection"]["harness"]["name"],
                         "alpha")
        self.assertTrue(any("harness or model" in c for c in
                            profiles.freshness(frozen)["changed"]))

    def test_an_uninstalled_harness_refuses_before_any_side_effect(self):
        self.env_file("BOARD_AGENT_ADAPTER=nowhere\n")
        before = (self.tasks / "in-progress" / CARD).read_text("utf-8")
        with self.assertRaisesRegex(ValueError, "not installed"):
            agents.start_agent(CARD, "in-progress")
        self.assertFalse((self.tmp / "worktrees" / CARD[:-3]).exists())
        self.assertEqual(git(self.repo, "branch", "--list", "task/*").stdout.strip(), "")
        self.assertEqual((self.tasks / "in-progress" / CARD).read_text("utf-8"), before)
        self.assertEqual(state.AGENTS, {})
        found = profiles.manifest("work", CARD, "in-progress")
        self.assertIn("harness-unavailable", [d["code"] for d in found["diagnostics"]])


class TemplateContract(unittest.TestCase):

    def test_the_built_in_templates_hold_their_own_contract(self):
        for name in templates.NAMES:
            self.assertEqual(templates.problems(name, templates.builtin(name)), [],
                             name)

    def codes(self, name: str, text: str) -> list[str]:
        return [p["code"] for p in templates.problems(name, text)]

    def test_malformed_braces(self):
        text = templates.builtin("work.md") + "\nan { unmatched brace\n"
        self.assertEqual(self.codes("work.md", text), ["template-malformed"])

    def test_unknown_fields_are_named_per_template(self):
        # {pr} is a review-pr field and not a work one
        text = templates.builtin("work.md") + "\n{pr}\n"
        self.assertIn("template-unknown-field", self.codes("work.md", text))
        self.assertNotIn("template-unknown-field",
                         self.codes("review-pr.md", templates.builtin("review-pr.md")))

    def test_reaching_into_a_value_is_refused(self):
        text = templates.builtin("review.md") + "\n{body.__class__}\n{body!r}\n{0}\n"
        codes = self.codes("review.md", text)
        self.assertIn("template-field-access", codes)
        self.assertIn("template-field-format", codes)
        self.assertIn("template-positional", codes)

    def test_missing_required_inputs_and_markers(self):
        text = templates.builtin("work.md").replace("{plan}", "").replace(
            "NOT READY:", "NOT-READY")
        codes = self.codes("work.md", text)
        self.assertIn("template-missing-field", codes)
        self.assertIn("template-missing-marker", codes)
        for name, marker in (("act-pr.md", "ADDRESSED:"),
                             ("review.md", "RELEVANCE REVIEW:"),
                             ("review-pr.md", "PR REVIEW:")):
            self.assertIn("template-missing-marker",
                          self.codes(name, templates.builtin(name).replace(marker, "")))

    def test_task_content_is_data_not_a_template(self):
        fields = templates.sample_fields()
        fields["body"] = "a card that says {branch} and {{escape}} and {"
        rendered = templates.render("work.md", templates.builtin("work.md"), fields)
        self.assertIn("a card that says {branch} and {{escape}} and {", rendered)


class SavingIsSafe(AgentCase):

    ENV = ("# my comment, kept\nBOARD_PORT=26999\n"
           "BOARD_AGENT_ADAPTER=claude\nUNRELATED=keep me\n# trailing note\n")

    def setUp(self):
        super().setUp()
        self.env_path = self.env_file(self.ENV)
        (self.local / "prompts").mkdir(exist_ok=True)

    def draft(self, **extra) -> dict:
        payload = {"revision": agentsettings.revision(), "agents": {}, "prompts": {}}
        payload.update(extra)
        return payload

    def test_reading_writes_nothing_and_launches_nothing(self):
        before = sorted(p for p in self.tmp.rglob("*"))
        with mock.patch("subprocess.Popen") as popen:
            page = agentsettings.page()
            agentsettings.preview(self.draft(agents={"BOARD_AGENT_MODEL": "x"}))
            agentsettings.render({"template": "work.md"})
        popen.assert_not_called()
        self.assertEqual(before, sorted(p for p in self.tmp.rglob("*")))
        self.assertEqual({t["name"] for t in page["templates"]}, set(templates.NAMES))
        self.assertIn("alpha", {h["name"] for h in page["harnesses"]})

    def test_apply_writes_only_the_agent_keys_and_keeps_everything_else(self):
        result = agentsettings.apply(self.draft(agents={
            "BOARD_AGENT_ADAPTER": "alpha", "BOARD_AGENT_ADAPTER_REVIEW": "beta",
            "BOARD_AGENT_MODEL_WORK": "w-model"}))
        text = self.env_path.read_text("utf-8")
        self.assertIn("# my comment, kept\nBOARD_PORT=26999\nBOARD_AGENT_ADAPTER=alpha\n"
                      "UNRELATED=keep me\n# trailing note\n", text)
        self.assertIn("BOARD_AGENT_ADAPTER_REVIEW=beta\n", text)
        self.assertIn("BOARD_AGENT_MODEL_WORK=w-model\n", text)
        self.assertEqual(config.agent_choice("review")["harness"]["value"], "beta")
        self.assertEqual(config.agent_model("work"), "w-model")
        self.assertEqual(sorted(result["changedKeys"]),
                         ["BOARD_AGENT_ADAPTER", "BOARD_AGENT_ADAPTER_REVIEW",
                          "BOARD_AGENT_MODEL_WORK"])
        # history names keys, never values
        line = agentsettings.history_path().read_text("utf-8")
        self.assertNotIn("w-model", line)
        self.assertNotIn("beta", line)

    def test_preview_marks_only_the_purposes_whose_launch_changes(self):
        """The page sends every agent key it shows, unchanged ones too; a
        preview must describe the file the apply would write, so only the
        overridden purpose reads as changed."""
        everything = {key: config.agent_setting(key)["value"]
                      for key in config.AGENT_KEYS}
        everything["BOARD_AGENT_ADAPTER_REVIEW"] = "beta"
        preview = agentsettings.preview(self.draft(agents=everything))
        changed = {p["purpose"]: p["changed"] for p in preview["purposes"]}
        self.assertEqual(changed, {"work": False, "act-pr": False, "review": True})
        result = agentsettings.apply(self.draft(agents=everything))
        self.assertEqual(result["changedKeys"], ["BOARD_AGENT_ADAPTER_REVIEW"])

    def test_a_stale_revision_is_a_conflict_and_writes_nothing(self):
        payload = self.draft(agents={"BOARD_AGENT_MODEL": "x"})
        self.env_file(self.ENV + "BOARD_AGENT_MODEL=someone-else\n")
        before = self.env_path.read_text("utf-8")
        with self.assertRaises(agentsettings.Conflict):
            agentsettings.apply(payload)
        self.assertEqual(self.env_path.read_text("utf-8"), before)

    def test_an_unrelated_edit_is_not_a_conflict_and_survives(self):
        payload = self.draft(agents={"BOARD_AGENT_MODEL": "x"})
        self.env_file(self.ENV + "BOARD_TITLE=edited meanwhile\n")
        agentsettings.apply(payload)
        text = self.env_path.read_text("utf-8")
        self.assertIn("BOARD_TITLE=edited meanwhile", text)
        self.assertIn("BOARD_AGENT_MODEL=x", text)

    def test_invalid_input_writes_nothing(self):
        before = self.env_path.read_text("utf-8")
        for agents_in in ({"BOARD_AGENT_ADAPTER": "nowhere"},
                          {"BOARD_AGENT_ADAPTER": "../../etc"},
                          {"BOARD_AGENT_ADAPTER": ""},
                          {"BOARD_AGENT_MODEL": "two words"},
                          {"BOARD_AGENT_MODEL": "x\nBOARD_SYNC=1"},
                          {"BOARD_AGENT_MODEL": "sk-abcdefghijklmnopqrstuvwxyz"}):
            with self.assertRaises(ValueError, msg=agents_in):
                agentsettings.apply(self.draft(agents=agents_in))
        for bad in ({"BOARD_SYNC": "1"}, {"../.env": "x"}):
            with self.assertRaises(ValueError):
                agentsettings.validate(self.draft(agents=bad))
        with self.assertRaises(ValueError):
            agentsettings.validate(self.draft(prompts={"../core/prompts/work.md":
                                                       {"action": "reset"}}))
        self.assertEqual(self.env_path.read_text("utf-8"), before)

    def test_a_locked_key_cannot_be_edited(self):
        config._ENV_LOCKED = {"BOARD_AGENT_MODEL": "env-model"}
        checked = agentsettings.validate(self.draft(agents={"BOARD_AGENT_MODEL": "other"}))
        self.assertEqual([e["code"] for e in checked["errors"]], ["setting-locked"])
        self.assertIn("restart", checked["errors"][0]["why"])
        # sending the locked value back unchanged is fine, and writes nothing
        agentsettings.apply(self.draft(agents={"BOARD_AGENT_MODEL": "env-model"}))
        self.assertNotIn("BOARD_AGENT_MODEL", self.env_path.read_text("utf-8"))

    def test_a_template_is_saved_validated_reset_and_recoverable(self):
        custom = templates.builtin("work.md") + "\nA local addition.\n"
        agentsettings.apply(self.draft(prompts={"work.md": {"action": "set",
                                                            "text": custom}}))
        self.assertEqual(templates.load("work.md")["source"], "local")
        self.assertTrue((self.local / "prompts" / "work.md").is_file())
        # never core
        self.assertNotIn("A local addition", templates.builtin("work.md"))

        broken = custom.replace("NOT READY:", "")
        with self.assertRaisesRegex(ValueError, "NOT READY"):
            agentsettings.apply(self.draft(prompts={"work.md": {"action": "set",
                                                                "text": broken}}))
        self.assertEqual(templates.local("work.md"), custom)

        preview = agentsettings.preview(self.draft(prompts={"work.md":
                                                            {"action": "reset"}}))
        self.assertIn("-A local addition.", preview["templates"][0]["diff"])
        self.assertTrue(preview["templates"][0]["recoverable"])
        agentsettings.apply(self.draft(prompts={"work.md": {"action": "reset"}}))
        self.assertIsNone(templates.local("work.md"))
        recovered = [r for r in agentsettings.recovery() if r["template"] == "work.md"]
        self.assertEqual(recovered[0]["text"], custom)

    def test_an_existing_broken_override_is_shown_and_refuses_the_launch(self):
        (self.local / "prompts" / "work.md").write_text("no placeholders at all\n",
                                                        encoding="utf-8")
        described = {t["name"]: t for t in agentsettings.page()["templates"]}
        self.assertTrue(described["work.md"]["problems"])
        self.assertEqual(described["work.md"]["text"], "no placeholders at all\n")
        before = (self.tasks / "in-progress" / CARD).read_text("utf-8")
        with self.assertRaisesRegex(ValueError, "local override"):
            agents.start_agent(CARD, "in-progress")
        self.assertFalse((self.tmp / "worktrees" / CARD[:-3]).exists())
        self.assertEqual((self.tasks / "in-progress" / CARD).read_text("utf-8"), before)
        # and it is not silently rewritten
        self.assertEqual(templates.local("work.md"), "no placeholders at all\n")

    def test_a_failed_combined_save_puts_everything_back(self):
        custom = templates.builtin("review.md") + "\nmine\n"
        before_env = self.env_path.read_text("utf-8")
        real = agentsettings._replace
        calls = []

        def flaky(path, text):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(path, text)

        with mock.patch.object(agentsettings, "_replace", side_effect=flaky):
            with self.assertRaisesRegex(OSError, "put back"):
                agentsettings.apply(self.draft(
                    agents={"BOARD_AGENT_MODEL": "new"},
                    prompts={"review.md": {"action": "set", "text": custom}}))
        self.assertEqual(self.env_path.read_text("utf-8"), before_env)
        self.assertIsNone(templates.local("review.md"))

    def test_a_render_uses_samples_or_a_selected_card_and_runs_nothing(self):
        rendered = agentsettings.render({"template": "work.md"})["rendered"]
        self.assertIn("00-sample-card.md", rendered)
        self.write(card("67 — a card with {branch} in it"))
        rendered = agentsettings.render({"template": "work.md", "card": CARD})
        self.assertIn("a card with {branch} in it", rendered["rendered"])
        self.assertIn(CARD, rendered["source"])
        with self.assertRaises(ValueError):
            agentsettings.render({"template": "work.md", "card": "../../etc/passwd"})
        bad = agentsettings.render({"template": "work.md", "text": "{nope}"})
        self.assertFalse(bad["ok"])

    def test_saving_launches_nothing_and_moves_nothing(self):
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("subprocess.run") as run:
            agentsettings.apply(self.draft(agents={"BOARD_AGENT_MODEL": "x"}))
        popen.assert_not_called()
        run.assert_not_called()
        self.assertEqual(state.AGENTS, {})
        self.assertTrue((self.tasks / "in-progress" / CARD).is_file())


if __name__ == "__main__":
    unittest.main()
