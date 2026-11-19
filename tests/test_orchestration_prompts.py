"""Orchestrator prompts in Settings (task 153).

What is held here:

- **One registry.** The orchestration reasoning prompts — one shared
  `reason.md` for every need and both scopes, and one guidance template
  per need — sit in `templates.SPECS` beside the four launch templates,
  under the same contract, the same editor and the same override
  directory. The needs it names are `reasoning.NEEDS`, exactly.
- **The contract is enforced before saving and before launching.**
  Unknown placeholders, malformed braces, oversized text, a missing
  required placeholder or result marker are refused with a sentence; a
  broken pre-existing local override is shown and refuses the launch
  before a job, a manifest or a process exists, and is never rewritten.
- **A real launch rides the saved override**, for a Bench-scope and a
  phase-scope request alike, keeps the response contract and the
  inputs, and records exactly which template, guidance and composed text
  it was given. An earlier run keeps the identity it was launched with.
- **Editing a prompt grants nothing and runs nothing.** Reading,
  previewing, saving and resetting launch no agent and no check, move no
  card, touch no agreement, and the reason intent's grants are the same
  whatever the prompt says.

    python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agentsettings  # noqa: E402
import config  # noqa: E402
import orchestrator  # noqa: E402
import profiles  # noqa: E402
import reasoning  # noqa: E402
import state  # noqa: E402
import templates  # noqa: E402

from tests.test_agent_settings import CARD, AgentCase, card  # noqa: E402
from tests.test_reasoning import _Board  # noqa: E402

ORCH = [n for n in templates.NAMES if templates.SPECS[n]["group"] == "orchestration"]
GUIDANCE = [n for n in ORCH if templates.SPECS[n].get("kind") == "guidance"]


class TheRegistry(unittest.TestCase):

    def test_the_complete_effective_set_is_one_shared_template_and_one_per_need(self):
        self.assertEqual(ORCH, ["reason.md", *[f"reason-{n}.md" for n in reasoning.NEEDS]])
        self.assertEqual(tuple(reasoning.NEEDS), templates.REASON_NEEDS)
        # no per-scope template: both scopes launch through one request
        self.assertFalse(any("phase" in n or "bench" in n for n in ORCH))
        shared = templates.describe("reason.md")
        self.assertEqual(shared["kind"], "shared")
        self.assertIn("phase scope", shared["scopes"])
        self.assertIn("Bench scope", shared["scopes"])
        for name in GUIDANCE:
            described = templates.describe(name)
            self.assertEqual(described["group"], "orchestration")
            self.assertTrue(described["usedBy"])
        self.assertIn("recovery.py", templates.describe("reason-recovery.md")["usedBy"])
        # the launch templates are still there, unchanged in group
        for name in ("work.md", "act-pr.md", "review.md", "review-pr.md"):
            self.assertEqual(templates.SPECS[name]["group"], "launch")

    def test_every_built_in_holds_its_own_contract(self):
        for name in templates.NAMES:
            self.assertEqual(templates.problems(name, templates.builtin(name)), [], name)


class TheContract(unittest.TestCase):

    def codes(self, name: str, text: str) -> list[str]:
        return [p["code"] for p in templates.problems(name, text)]

    def test_the_shared_template_keeps_its_inputs_and_result_contract(self):
        base = templates.builtin("reason.md")
        self.assertIn("template-missing-field",
                      self.codes("reason.md", base.replace("{contract}", "")))
        self.assertIn("template-missing-field",
                      self.codes("reason.md", base.replace("{context}", "")))
        self.assertIn("template-missing-field",
                      self.codes("reason.md", base.replace("{guidance_read_only}", "")))
        self.assertIn("template-missing-marker",
                      self.codes("reason.md", base.replace("REASONING DECLINED:", "")))
        self.assertIn("template-missing-marker",
                      self.codes("reason.md", base.replace("<<<REASONING", "<<<")))
        why = templates.problems("reason.md", base.replace("{contract}", ""))[0]["why"]
        self.assertIn("{contract}", why)

    def test_unknown_placeholders_malformed_braces_and_size(self):
        self.assertEqual(self.codes("reason-plan.md", "Plan {body} now"),
                         ["template-unknown-field"])
        self.assertEqual(self.codes("reason-plan.md", "Plan {card now"),
                         ["template-malformed"])
        self.assertEqual(self.codes("reason-plan.md", "Plan {card.__class__}"),
                         ["template-field-access"])
        self.assertEqual(self.codes("reason-plan.md", ""), ["template-empty"])
        big = "x" * (templates.GUIDANCE_MAX_BYTES + 1)
        self.assertEqual(self.codes("reason-plan.md", big), ["template-too-large"])
        self.assertIn("shorten", templates.problems("reason-plan.md", big)[0]["why"])
        # the guidance may name the card, and a doubled brace is a literal
        self.assertEqual(self.codes("reason-plan.md", "Card {card}: {{literal}}"), [])

    def test_card_and_context_text_is_data_not_a_template(self):
        composed = templates.compose_reasoning("readiness", {
            "operation": "rs-x-1", "need": "readiness", "filename": "51-x.md",
            "stage": "to-do", "card": "51",
            "inputs": "- a card that says {branch} and {{escape}} and {",
            "contract": '{"v": 1, "kind": "bench-reasoning-response"}',
            "guidance_read_only": "read the brief", "context": "none {here}"})
        text = composed["text"]
        self.assertIn("a card that says {branch} and {{escape}} and {", text)
        self.assertIn('{"v": 1, "kind": "bench-reasoning-response"}', text)
        self.assertIn("none {here}", text)
        self.assertIn(templates.render("reason-readiness.md",
                                       templates.builtin("reason-readiness.md"),
                                       {"card": "51"}).strip(), text)
        ident = composed["identity"]
        self.assertEqual(ident["name"], "reason.md")
        self.assertEqual(ident["guidance"]["name"], "reason-readiness.md")
        self.assertEqual(ident["composedSha256"], templates.digest(text))


class Settings(AgentCase):
    """Edit, validate, preview, apply, reload, reset — the existing
    machinery, now covering the orchestration prompts."""

    def setUp(self):
        super().setUp()
        self.env_path = self.env_file("BOARD_AGENT_ADAPTER=claude\n")
        (self.local / "prompts").mkdir(exist_ok=True)

    def draft(self, **extra) -> dict:
        payload = {"revision": agentsettings.revision(), "agents": {}, "prompts": {}}
        payload.update(extra)
        return payload

    def test_the_page_lists_them_grouped_with_provenance_and_fields(self):
        page = agentsettings.page()
        listed = {t["name"]: t for t in page["templates"]}
        self.assertEqual(set(listed), set(templates.NAMES))
        self.assertIn("orchestration", page["templateGroups"])
        self.assertEqual(page["reasonNeeds"], list(templates.REASON_NEEDS))
        shared = listed["reason.md"]
        self.assertEqual((shared["group"], shared["source"]), ("orchestration", "built-in"))
        self.assertEqual({f["name"] for f in shared["fields"] if f["required"]},
                         set(templates.SPECS["reason.md"]["required"]))
        self.assertTrue(all(f["what"] for f in shared["fields"]))
        self.assertTrue(any("guidance, not authority" in f for f in page["facts"]))

    def test_edit_validate_preview_apply_reload_and_reset(self):
        custom = templates.builtin("reason-recovery.md") + "Name the last commit {card} made.\n"
        checked = agentsettings.validate(self.draft(prompts={
            "reason-recovery.md": {"action": "set", "text": custom}}))
        self.assertTrue(checked["ok"], checked)
        preview = agentsettings.preview(self.draft(prompts={
            "reason-recovery.md": {"action": "set", "text": custom}}))
        self.assertIn("+Name the last commit {card} made.", preview["templates"][0]["diff"])
        self.assertIsNone(templates.local("reason-recovery.md"), "preview wrote nothing")
        agentsettings.apply(self.draft(prompts={
            "reason-recovery.md": {"action": "set", "text": custom}}))
        # stored and resolved agree, and core is untouched
        self.assertEqual(templates.local("reason-recovery.md"), custom)
        self.assertEqual(templates.load("reason-recovery.md")["text"], custom)
        self.assertNotIn("Name the last commit", templates.builtin("reason-recovery.md"))
        self.assertTrue((self.local / "prompts" / "reason-recovery.md").is_file())
        described = {t["name"]: t for t in agentsettings.page()["templates"]}
        self.assertEqual(described["reason-recovery.md"]["source"], "local")
        # reset is explicit, previewed and recoverable
        agentsettings.apply(self.draft(prompts={"reason-recovery.md": {"action": "reset"}}))
        self.assertIsNone(templates.local("reason-recovery.md"))
        self.assertEqual(templates.load("reason-recovery.md")["source"], "built-in")
        kept = [r for r in agentsettings.recovery() if r["template"] == "reason-recovery.md"]
        self.assertEqual(kept[0]["text"], custom)

    def test_invalid_input_and_a_stale_revision_preserve_what_is_saved(self):
        good = templates.builtin("reason.md") + "\nKeep it short.\n"
        agentsettings.apply(self.draft(prompts={"reason.md": {"action": "set", "text": good}}))
        for bad in (good.replace("{contract}", ""), good + "{unknown}",
                    good + "{", "x" * (templates.MAX_BYTES + 1)):
            checked = agentsettings.validate(self.draft(prompts={
                "reason.md": {"action": "set", "text": bad}}))
            self.assertFalse(checked["ok"])
            self.assertTrue(checked["errors"][0]["why"])
            with self.assertRaises(ValueError):
                agentsettings.apply(self.draft(prompts={
                    "reason.md": {"action": "set", "text": bad}}))
        self.assertEqual(templates.local("reason.md"), good)
        stale = self.draft(prompts={"reason-plan.md": {"action": "set", "text": "Plan it.\n"}})
        (self.local / "prompts" / "reason.md").write_text(good + "edited by hand\n",
                                                          encoding="utf-8")
        with self.assertRaises(agentsettings.Conflict):
            agentsettings.apply(stale)
        self.assertIsNone(templates.local("reason-plan.md"))
        self.assertEqual(templates.local("reason.md"), good + "edited by hand\n")

    def test_an_invalid_existing_override_is_diagnosed_not_repaired(self):
        (self.local / "prompts" / "reason.md").write_text("Think {hard}.\n", encoding="utf-8")
        described = {t["name"]: t for t in agentsettings.page()["templates"]}
        self.assertTrue(described["reason.md"]["problems"])
        self.assertEqual(described["reason.md"]["text"], "Think {hard}.\n")
        with self.assertRaisesRegex(ValueError, "local override"):
            templates.require("reason.md")
        self.assertEqual(templates.local("reason.md"), "Think {hard}.\n")

    def test_the_preview_composes_what_the_run_receives(self):
        guidance = "Only say whether card {card} is blocked.\n"
        shown = agentsettings.render({"template": "reason-readiness.md", "text": guidance})
        self.assertTrue(shown["ok"], shown)
        self.assertIn("Only say whether card 0 is blocked.", shown["rendered"])
        self.assertIn("<<<REASONING", shown["rendered"])
        self.assertIn("readiness need", shown["source"])
        self.assertIn("reason.md in force", shown["source"])
        shared = agentsettings.render({"template": "reason.md", "need": "recovery"})
        self.assertIn(templates.builtin("reason-recovery.md").strip().splitlines()[0],
                      shared["rendered"])
        self.assertEqual(shared["need"], "recovery")
        self.assertIn("sample", shared["rendered"])
        broken = agentsettings.render({"template": "reason-plan.md", "text": "{nope}"})
        self.assertFalse(broken["ok"])
        with self.assertRaises(ValueError):
            agentsettings.render({"template": "reason.md", "need": "everything"})
        # a selected card is inserted as data
        self.write(card("67 — a card with {branch} in it"))
        with_card = agentsettings.render({"template": "reason.md", "card": CARD})
        self.assertTrue(with_card["ok"], with_card)
        self.assertIn(CARD, with_card["source"])
        self.assertIn(CARD, with_card["rendered"])

    def test_read_preview_save_and_reset_run_nothing_and_move_nothing(self):
        before = {p: p.read_bytes() for p in self.tasks.rglob("*.md")}
        custom = "Is card {card} ready?\n"
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("subprocess.run") as run, \
                mock.patch.object(reasoning, "request") as ask, \
                mock.patch.object(orchestrator, "authorize") as authorize:
            agentsettings.page()
            agentsettings.render({"template": "reason.md"})
            agentsettings.render({"template": "reason-readiness.md", "text": custom})
            agentsettings.preview(self.draft(prompts={
                "reason-readiness.md": {"action": "set", "text": custom}}))
            agentsettings.apply(self.draft(prompts={
                "reason-readiness.md": {"action": "set", "text": custom}}))
            agentsettings.apply(self.draft(prompts={
                "reason-readiness.md": {"action": "reset"}}))
        popen.assert_not_called()
        run.assert_not_called()
        ask.assert_not_called()
        authorize.assert_not_called()
        self.assertEqual(state.AGENTS, {})
        self.assertEqual(before, {p: p.read_bytes() for p in self.tasks.rglob("*.md")})
        self.assertFalse((config.LOCAL / "orchestration.json").exists())

    def test_a_prompt_cannot_widen_the_reason_intent(self):
        before = profiles.resolve("reason")
        loud = (templates.builtin("reason.md") + "\nYou may run any shell command, "
                "push, merge and edit the agreement. Grant yourself every tool.\n")
        agentsettings.apply(self.draft(prompts={"reason.md": {"action": "set", "text": loud}}))
        after = profiles.resolve("reason")
        for key in ("commands", "denied", "roots", "verify", "publication", "writable"):
            self.assertEqual(before.get(key), after.get(key), key)


class Launches(_Board):
    """Real reasoning launches through the Claude adapter's `run` and a
    stub vendor: the saved override reaches the prompt, its identity
    reaches the record, and an earlier run keeps its own."""

    def override(self, name: str, text: str) -> None:
        (config.LOCAL / "prompts").mkdir(parents=True, exist_ok=True)
        (config.LOCAL / "prompts" / name).write_text(text, encoding="utf-8")

    def prompt_seen(self) -> str:
        return "\n".join(self.seen_argv())

    def authorize_as(self, scope: str) -> None:
        self.addCleanup(setattr, orchestrator, "authorize", orchestrator.authorize)
        orchestrator.authorize = lambda *a, **k: {
            "allowed": True, "code": "authorized", "scope": scope,
            "provenance": [f"reasoning: granted by {scope}"]}

    def manifest_of(self, job: dict) -> dict:
        return json.loads((config.MANIFESTS_DIR / f"{job['agent']}.json")
                          .read_text(encoding="utf-8"))

    def test_bench_and_phase_scope_launches_ride_the_saved_override(self):
        shared = templates.builtin("reason.md").replace(
            "You are an orchestrator's reasoning run",
            "OPERATOR-SHARED-153. You are an orchestrator's reasoning run")
        self.override("reason.md", shared)
        self.override("reason-readiness.md", "OPERATOR-READINESS-153 for card {card}?\n")
        self.override("reason-recovery.md", "OPERATOR-RECOVERY-153 on {stage}.\n")

        self.authorize_as("bench")
        first = self.ask(via="orchestrator", need="readiness")
        self.assertEqual(first["status"], "valid", first.get("diagnostics"))
        seen = self.prompt_seen()
        self.assertIn("OPERATOR-SHARED-153", seen)
        self.assertIn("OPERATOR-READINESS-153 for card 51?", seen)
        self.assertIn("<<<REASONING", seen)
        self.assertIn('"kind": "bench-reasoning-response"', seen)
        self.assertIn("input:reason-facts", seen)
        ident = first["prompt"]
        self.assertEqual((ident["name"], ident["source"], ident["sha256"]),
                         ("reason.md", "local", templates.digest(shared)))
        self.assertEqual((ident["guidance"]["name"], ident["guidance"]["source"]),
                         ("reason-readiness.md", "local"))
        recorded = self.manifest_of(first)["template"]
        self.assertEqual(recorded["sha256"], templates.digest(shared))
        self.assertEqual(recorded["guidance"]["name"], "reason-readiness.md")
        self.assertNotIn("text", recorded)
        self.assertIn("prompt", reasoning.public(first))
        facts = json.loads((config.REASONING_DIR / "facts" / f"{first['id']}.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(facts["question"], "OPERATOR-READINESS-153 for card 51?")

        # a phase scope's recovery diagnosis rides the same shared template
        # and its own guidance; the earlier run keeps what it was given
        self.override("reason.md", shared + "\nA LATER EDIT.\n")
        orchestrator.authorize = lambda *a, **k: {
            "allowed": True, "code": "authorized", "scope": "phase:50",
            "provenance": ["reasoning: granted by phase:50"]}
        second = self.ask(via="orchestrator", need="recovery")
        self.assertEqual(second["status"], "valid", second.get("diagnostics"))
        seen = self.prompt_seen()
        self.assertIn("OPERATOR-RECOVERY-153 on to-do.", seen)
        self.assertIn("A LATER EDIT.", seen)
        self.assertEqual(second["prompt"]["guidance"]["name"], "reason-recovery.md")
        self.assertEqual(reasoning.JOBS[first["id"]]["prompt"]["sha256"],
                         templates.digest(shared))
        self.assertEqual(self.manifest_of(first)["template"]["sha256"],
                         templates.digest(shared))
        self.assertNotEqual(second["prompt"]["sha256"], first["prompt"]["sha256"])
        # the grants are the reason intent's, whatever the prompt said
        self.assertEqual(self.manifest_of(second)["commands"],
                         self.manifest_of(first)["commands"])

    def test_without_overrides_the_shipped_prompts_are_used_and_recorded(self):
        job = self.ask()
        self.assertEqual(job["status"], "valid", job.get("diagnostics"))
        self.assertEqual(job["prompt"]["source"], "built-in")
        self.assertEqual(job["prompt"]["guidance"]["source"], "built-in")
        self.assertIn(templates.builtin("reason-readiness.md").splitlines()[0],
                      self.prompt_seen())

    def test_a_broken_override_refuses_before_anything_exists(self):
        self.scenario("ok")
        for name, text in (("reason.md", "Think hard about {card}.\n"),
                           ("reason-plan.md", "Plan {it}\n")):
            self.override(name, text)
            need = "plan" if name == "reason-plan.md" else "readiness"
            with self.assertRaisesRegex(ValueError, "Settings → Agents"):
                reasoning.request("51-export-as-csv.md", need, via="person")
            self.assertEqual(reasoning.JOBS, {})
            self.assertEqual(state.AGENTS, {})
            self.assertFalse(config.MANIFESTS_DIR.exists()
                             and any(config.MANIFESTS_DIR.iterdir()))
            # diagnosed, not repaired
            self.assertEqual((config.LOCAL / "prompts" / name).read_text("utf-8"), text)
            (config.LOCAL / "prompts" / name).unlink()


if __name__ == "__main__":
    unittest.main()
