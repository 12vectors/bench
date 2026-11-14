"""Task 126: approved read-only inputs become *actual* read access in the
Claude adapter — one exact `Read(//absolute/path)` grant per approved
file, and nothing broader.

Before this, `profiles.adapter_context` handed the adapter each pinned
input's path and hash, and the adapter generated no rule from it: a
headless run (no human at a permission prompt) was refused the very files
its prompt said it was approved to read. The same was true of the workflow
briefs `config.prompt_guidance` names outside the checkout. Run with:
python3 -m unittest discover -s tests

Everything here is generated permission text, asserted against the real
adapter and a stub vendor binary. Whether the installed CLI *honours*
the rules is a separate, opt-in question: `develop/claude-access-smoke.py`.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
CLAUDE = CORE / "adapters" / "claude"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import doctor  # noqa: E402
import profiles  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_settings = _load("hook_settings_126", CLAUDE / "hook_settings.py")

# The launch environment of whoever runs the suite must not leak into a
# fixture: a board-launched run carries its own AGENT_* values.
LEAKY = ("AGENT_CONTEXT", "AGENT_GUIDANCE", "AGENT_PROFILE", "AGENT_PROGRESS",
         "AGENT_MODEL", "AGENT_COMMANDS", "AGENT_MODE", "AGENT_PROMPT",
         "AGENT_CWD")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: Path, name: str = "architecture-plan", **extra) -> dict:
    item = {"name": name, "path": str(path),
            "source": f"work:reference/{path.name}",
            "sha256": _sha(path), "access": "read-only"}
    item.update(extra)
    return item


def _clean_env(**values) -> dict:
    env = dict(os.environ)
    for key in LEAKY:
        env.pop(key, None)
    env.update(values)
    return env


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="bench-126-")))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.plan = self.root / "architecture-plan.md"
        self.plan.write_text("# plan\n", encoding="utf-8")
        self.brief = self.root / "AGENTS.md"
        self.brief.write_text("# brief\n", encoding="utf-8")

    def script(self, mode: str, **values) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(CLAUDE / "hook_settings.py"), mode],
            env=_clean_env(**values), capture_output=True, text=True)


class ApprovedContextBecomesARead(Fixture):
    """The regression the card asks for first: observed failing on the
    unchanged adapter, which rendered no rule at all from AGENT_CONTEXT."""

    def test_generated_settings_grant_an_exact_read_of_each_pinned_input(self):
        result = self.script("work", AGENT_CONTEXT=json.dumps([_entry(self.plan)]))
        self.assertEqual(result.returncode, 0, result.stderr)
        allow = json.loads(result.stdout)["permissions"]["allow"]
        # `//` is filesystem-root absolute in Claude Code's rule syntax; a
        # single leading slash is relative to the settings source.
        self.assertIn(f"Read(/{self.plan})", allow)
        self.assertTrue(f"Read(/{self.plan})".startswith("Read(//"))

    def test_every_intent_gets_the_grant_and_an_edit_deny(self):
        reads = hook_settings.approved_reads(
            json.dumps([_entry(self.plan)]),
            json.dumps([_entry(self.brief, "workflow-brief-1")]))
        for mode in ("work", "act-pr", "review", "refine"):
            with self.subTest(mode=mode):
                perms = hook_settings.settings(mode, [], None, None,
                                               reads)["permissions"]
                for path in (self.plan, self.brief):
                    self.assertIn(f"Read(/{path})", perms["allow"])
                    # a read grant is never licence to change the document
                    self.assertIn(f"Edit(/{path})", perms["deny"])
                    self.assertNotIn(f"Edit(/{path})", perms["allow"])
                    self.assertNotIn(f"Write(/{path})", perms["allow"])

    def test_nothing_broader_is_ever_generated(self):
        reads = hook_settings.approved_reads(json.dumps([_entry(self.plan)]),
                                             None)
        text = json.dumps(hook_settings.settings("work", ["npm test"], None,
                                                 None, reads)["permissions"])
        for broad in ("Read(*", "Read)", '"Read"', "Read(//**", "Read(~",
                      "additionalDirectories", "--add-dir",
                      "bypassPermissions", "Bash(*", '"Bash"',
                      f"Read(/{self.root})", f"Read(/{self.root}/**)"):
            self.assertNotIn(broad, text)
        read_rules = [r for r in json.loads(text)["allow"]
                      if r.startswith("Read(")]
        self.assertEqual(read_rules, [f"Read(/{self.plan})"])

    def test_existing_command_policy_is_unchanged_beside_the_reads(self):
        reads = hook_settings.approved_reads(json.dumps([_entry(self.plan)]),
                                             None)
        for mode in ("work", "act-pr", "review", "refine"):
            with self.subTest(mode=mode):
                before = hook_settings.settings(mode, ["npm test"])
                after = hook_settings.settings(mode, ["npm test"], None, None,
                                               reads)
                perms_before = before.get("permissions") or {}
                perms_after = after["permissions"]
                self.assertEqual(
                    [r for r in perms_after.get("allow", [])
                     if not r.startswith("Read(")],
                    perms_before.get("allow", []))
                self.assertEqual(
                    [r for r in perms_after.get("deny", [])
                     if not r.startswith("Edit(")],
                    perms_before.get("deny", []))
                self.assertEqual(hook_settings.launch_flags(mode),
                                 hook_settings.launch_flags(mode))

    def test_no_context_renders_exactly_the_legacy_document(self):
        for mode in ("work", "act-pr", "review", "refine", ""):
            with self.subTest(mode=mode):
                result = self.script(mode, AGENT_COMMANDS="npm test")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 hook_settings.settings(mode, ["npm test"]))
                empty = self.script(mode, AGENT_COMMANDS="npm test",
                                    AGENT_CONTEXT="[]", AGENT_GUIDANCE="")
                self.assertEqual(json.loads(empty.stdout),
                                 hook_settings.settings(mode, ["npm test"]))

    def test_the_same_file_named_twice_is_granted_once(self):
        reads = hook_settings.approved_reads(
            json.dumps([_entry(self.brief, "pinned")]),
            json.dumps([_entry(self.brief, "workflow-brief-1")]))
        self.assertEqual([r["path"] for r in reads], [str(self.brief)])


class WhatIsRefused(Fixture):
    """Malformed, unapproved or escaping input: refused whole, never
    turned into a broader rule and never silently dropped."""

    def assertRefused(self, context=None, guidance=None):
        with self.assertRaises(hook_settings.ReadRefused):
            hook_settings.approved_reads(context, guidance)
        result = self.script("work", AGENT_CONTEXT=context or "",
                             AGENT_GUIDANCE=guidance or "")
        self.assertEqual(result.returncode, hook_settings.CONTEXT_EXIT)
        self.assertEqual(result.stdout, "")
        self.assertIn("refusing the launch", result.stderr)

    def test_malformed_variables(self):
        self.assertRefused(context="{not json")
        self.assertRefused(context=json.dumps({"name": "x"}))
        self.assertRefused(guidance=json.dumps(["just a string"]))

    def test_malformed_entries(self):
        good = _entry(self.plan)
        for broken in ({**good, "name": ""}, {**good, "access": "read-write"},
                       {k: v for k, v in good.items() if k != "access"},
                       {**good, "sha256": None}, {**good, "sha256": "abc"},
                       {**good, "path": None}, {**good, "path": 7}):
            with self.subTest(entry=broken):
                self.assertRefused(context=json.dumps([broken]))

    def test_paths_that_are_not_one_exact_file(self):
        star = self.root / "star*.md"
        star.write_text("x", encoding="utf-8")
        spaced = self.root / "a b.md"
        spaced.write_text("x", encoding="utf-8")
        paren = self.root / "p(1).md"
        paren.write_text("x", encoding="utf-8")
        newline = self.root / "nl\n.md"
        newline.write_text("x", encoding="utf-8")
        for path in (star, spaced, paren, newline):
            with self.subTest(path=str(path)):
                self.assertRefused(context=json.dumps([_entry(path)]))
        sha = _sha(self.plan)
        for raw in ("architecture-plan.md", f"{self.root}/../{self.root.name}"
                    f"/architecture-plan.md", f"{self.root}//architecture-plan.md",
                    f"{self.root}/./architecture-plan.md", f"{self.root}/",
                    f"{self.root}/**", f"{self.root}/*.md", "~/x.md"):
            with self.subTest(raw=raw):
                self.assertRefused(context=json.dumps([{
                    "name": "x", "path": raw, "sha256": sha,
                    "access": "read-only"}]))

    def test_a_directory_is_never_granted(self):
        directory = self.root / "dir"
        directory.mkdir()
        self.assertRefused(context=json.dumps([{
            "name": "d", "path": str(directory), "sha256": "0" * 64,
            "access": "read-only"}]))

    def test_a_link_is_refused_even_to_an_approved_file(self):
        link = self.root / "link.md"
        link.symlink_to(self.plan)
        self.assertRefused(context=json.dumps([_entry(link)]))
        linked_dir = self.root / "linked"
        linked_dir.symlink_to(self.root)
        self.assertRefused(context=json.dumps([{
            "name": "x", "path": str(linked_dir / self.plan.name),
            "sha256": _sha(self.plan), "access": "read-only"}]))

    def test_a_missing_file_is_refused(self):
        self.assertRefused(context=json.dumps([{
            "name": "x", "path": str(self.root / "gone.md"),
            "sha256": "0" * 64, "access": "read-only"}]))

    def test_drift_from_the_approved_content_fails_closed(self):
        entry = _entry(self.brief, "workflow-brief-1")
        self.brief.write_text("# brief, edited after approval\n", encoding="utf-8")
        self.assertRefused(guidance=json.dumps([entry]))

    def test_one_file_approved_with_two_hashes_is_refused(self):
        self.assertRefused(context=json.dumps([_entry(self.plan)]),
                           guidance=json.dumps([{**_entry(self.plan),
                                                 "sha256": "1" * 64}]))

    def test_one_bad_entry_refuses_the_rest_too(self):
        self.assertRefused(context=json.dumps([
            _entry(self.plan), {**_entry(self.brief), "path": f"{self.root}/*"}]))


def _stub(directory: Path) -> tuple[Path, Path]:
    capture = directory / "argv.json"
    stub = directory / "stub.py"
    stub.write_text("import json, sys\n"
                    f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
                    encoding="utf-8")
    wrapper = directory / "bin"
    wrapper.write_text(f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n",
                       encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper, capture


class RunAndDescribeAgree(Fixture):
    """`run` (bash) and `describe.py` (python) build the argument list
    separately; the reads must come out identical in both, delivered to a
    stub vendor binary, with every pre-existing flag unchanged."""

    def _values(self, mode: str) -> dict:
        return {"AGENT_MODE": mode, "AGENT_PROMPT": "do the task",
                "AGENT_COMMANDS": "python3 -m unittest",
                "AGENT_CWD": str(self.root),
                "AGENT_PROGRESS": "/opt/bench/core/bench-progress",
                "AGENT_CONTEXT": json.dumps([_entry(self.plan)]),
                "AGENT_GUIDANCE": json.dumps([_entry(self.brief,
                                                     "workflow-brief-1")])}

    def _run(self, values: dict) -> tuple[subprocess.CompletedProcess, list | None]:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper, capture = _stub(Path(tmp))
            result = subprocess.run(
                ["bash", str(CLAUDE / "run")], cwd=self.root,
                env=_clean_env(BOARD_CLAUDE_BIN=str(wrapper), **values),
                capture_output=True, text=True, timeout=60)
            argv = (json.loads(capture.read_text(encoding="utf-8"))
                    if capture.exists() else None)
            return result, argv

    def _describe(self, values: dict) -> dict:
        result = subprocess.run(["python3", "describe.py"], cwd=CLAUDE,
                                env=_clean_env(**values), capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_stub_receives_what_describe_reports(self):
        expected_flags = {
            "work": ["--permission-mode", "acceptEdits"],
            "act-pr": ["--permission-mode", "acceptEdits"],
            "review": ["--permission-mode", "default", "--disallowedTools",
                       "Edit", "Write", "NotebookEdit"],
            "refine": ["--permission-mode", "default", "--disallowedTools",
                       "Edit", "Write", "NotebookEdit", "Bash"],
        }
        for mode, flags in expected_flags.items():
            with self.subTest(mode=mode):
                values = self._values(mode)
                result, argv = self._run(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                described = self._describe(values)
                self.assertIsNone(described["refused"])
                self.assertEqual(described["argv"], argv)
                self.assertEqual(argv[4:], flags)
                settings = json.loads(argv[argv.index("--settings") + 1])
                allow, deny = (settings["permissions"]["allow"],
                               settings["permissions"].get("deny", []))
                for path in (self.plan, self.brief):
                    self.assertIn(f"Read(/{path})", allow)
                    self.assertIn(f"Edit(/{path})", deny)
                self.assertEqual(sorted(r["rule"] for r in described["reads"]),
                                 sorted([f"Read(/{self.plan})",
                                         f"Read(/{self.brief})"]))
                # the pre-existing policy, unchanged by the reads
                legacy = hook_settings.settings(
                    mode, ["python3 -m unittest"], None,
                    "/opt/bench/core/bench-progress")
                self.assertEqual([r for r in allow if not r.startswith("Read(")],
                                 (legacy.get("permissions") or {}).get("allow", []))
                self.assertEqual([r for r in deny if not r.startswith("Edit(")],
                                 (legacy.get("permissions") or {}).get("deny", []))
                if mode == "refine":
                    self.assertTrue(described["noShell"])
                    self.assertFalse([r for r in allow if r.startswith("Bash(")])
                if mode in ("review", "refine"):
                    self.assertTrue(described["readOnly"])
                if mode != "act-pr":
                    self.assertNotIn("Bash(git push:*)", allow)

    def test_a_refused_input_never_reaches_the_vendor(self):
        values = self._values("work")
        self.brief.write_text("# drifted\n", encoding="utf-8")
        result, argv = self._run(values)
        self.assertEqual(result.returncode, hook_settings.CONTEXT_EXIT)
        self.assertIsNone(argv, "the vendor binary was started anyway")
        self.assertIn("no longer holds the content bench approved", result.stderr)
        described = self._describe(values)
        self.assertIn("no longer holds", described["refused"])
        self.assertEqual(described["reads"], [])


class CoreContract(unittest.TestCase):
    """Core's half: which briefs reach AGENT_GUIDANCE, by which path and
    which approved hash, across vendored and external layouts."""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="bench-126c-")))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr in ("REPO", "TASKS", "LOCAL"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))

    def point_at(self, app: Path, work: Path, local: Path) -> None:
        config.REPO, config.TASKS, config.LOCAL = app, work / "tasks", local

    def external(self):
        app = self.tmp / "app"
        app.mkdir()
        (app / "AGENTS.md").write_text("# app rules\n", encoding="utf-8")
        work = self.tmp / "work"
        (work / "tasks").mkdir(parents=True)
        (work / "AGENTS.md").write_text("# board rules\n", encoding="utf-8")
        local = self.tmp / "tool" / "local"
        local.mkdir(parents=True)
        (local / "AGENTS.md").write_text("# operator rules\n", encoding="utf-8")
        self.point_at(app, work, local)
        return app, work, local

    def test_vendored_hands_over_nothing(self):
        app = self.tmp / "app"
        (app / "nested").mkdir(parents=True)
        (app / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        self.point_at(app, app, app / "nested")
        self.assertEqual(profiles.adapter_guidance(None), [])

    def test_external_briefs_are_the_ones_the_prompt_names(self):
        _app, work, local = self.external()
        guidance = profiles.adapter_guidance(None)
        paths = [g["path"] for g in guidance]
        self.assertEqual(paths, [str(work / "AGENTS.md"), str(local / "AGENTS.md")])
        text = config.prompt_guidance()
        for path in paths:
            self.assertIn(path, text)
        for g in guidance:
            self.assertEqual(g["access"], "read-only")
            self.assertEqual(g["sha256"], _sha(Path(g["path"])))
        # and the adapter grants exactly those
        reads = hook_settings.approved_reads(None, json.dumps(guidance))
        self.assertEqual([r["path"] for r in reads], paths)

    def test_a_linked_brief_is_named_and_granted_canonically(self):
        _app, work, local = self.external()
        real = self.tmp / "elsewhere.md"
        real.write_text("# linked\n", encoding="utf-8")
        (local / "AGENTS.md").unlink()
        (local / "AGENTS.md").symlink_to(real)
        guidance = profiles.adapter_guidance(None)
        self.assertIn(str(real), [g["path"] for g in guidance])
        self.assertIn(str(real), config.prompt_guidance())
        hook_settings.approved_reads(None, json.dumps(guidance))

    def test_a_pinned_brief_must_still_be_the_approved_content(self):
        _app, _work, local = self.external()
        brief = local / "AGENTS.md"
        approved = _sha(brief)
        found = {"context": [{"name": "workflow-brief", "status": "pinned",
                              "path": str(brief), "sha256": approved,
                              "pinned": str(self.tmp / "pinned.md")}]}
        entry = next(g for g in profiles.adapter_guidance(found)
                     if g["path"] == str(brief))
        self.assertEqual(entry["approvedAt"], "pinned-context")
        self.assertEqual(entry["sha256"], approved)
        hook_settings.approved_reads(None, json.dumps([entry]))
        brief.write_text("# operator rules, edited after the pin\n",
                         encoding="utf-8")
        entry = next(g for g in profiles.adapter_guidance(found)
                     if g["path"] == str(brief))
        self.assertEqual(entry["sha256"], approved,
                         "the approved hash must stay the pinned content")
        with self.assertRaises(hook_settings.ReadRefused):
            hook_settings.approved_reads(None, json.dumps([entry]))

    def test_a_context_source_is_not_granted_by_naming_it(self):
        """The pinned copy is what is approved; the original a context
        entry was copied from is not, unless it is a workflow brief."""
        _app, work, _local = self.external()
        source = work / "reference.md"
        source.write_text("# ref\n", encoding="utf-8")
        pinned = self.tmp / "bundle" / "reference.md"
        pinned.parent.mkdir()
        pinned.write_bytes(source.read_bytes())
        found = {"context": [{"name": "reference", "status": "pinned",
                              "source": "work:reference.md",
                              "path": str(source), "pinned": str(pinned),
                              "sha256": _sha(pinned)}]}
        context = profiles.adapter_context(found)
        self.assertEqual([c["path"] for c in context], [str(pinned)])
        guidance = profiles.adapter_guidance(found)
        self.assertNotIn(str(source), [g["path"] for g in guidance])
        reads = hook_settings.approved_reads(json.dumps(context),
                                             json.dumps(guidance))
        self.assertNotIn(str(source), [r["path"] for r in reads])


FAKE_VENDOR = r'''#!/usr/bin/env python3
"""A stand-in for the CLI that applies the generated rules the way the
card says the vendor does: Read needs an exact Read(//path) allow, an
Edit(//path) deny refuses a write, and nothing else is offered."""
import json, re, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
perms = json.loads(args[args.index("--settings") + 1]).get("permissions", {})
allow, deny = perms.get("allow", []), perms.get("deny", [])
withheld = args[args.index("--disallowedTools") + 1:] if "--disallowedTools" in args else []
n = 0
def call(name, inp, ok, body):
    global n
    n += 1
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{n}", "name": name, "input": inp}]}}))
    print(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"t{n}", "is_error": not ok,
         "content": body}]}}))
for path in re.findall(r"Read tool on (\S+?)\.\n", prompt):
    if f"Read(/{path})" in allow:
        call("Read", {"file_path": path}, True, open(path).read())
    else:
        call("Read", {"file_path": path}, False, "Permission to read denied")
for path in re.findall(r"Write tool to overwrite (\S+) ", prompt):
    if "Write" in withheld or f"Edit(/{path})" in deny:
        call("Write", {"file_path": path}, False, "Permission denied")
    else:
        open(path, "w").write("changed")
        call("Write", {"file_path": path}, True, "written")
if "git commit" in prompt:
    call("Bash", {"command": "git commit --allow-empty -m probe"},
         any(r.startswith("Bash(git commit") for r in allow), "denied")
'''


class SmokeHarnessPlumbing(unittest.TestCase):
    """The opt-in real-vendor probe, exercised end to end against a stand-in
    vendor so its fixture, its wrapper and its verdicts are tested without
    spending anything. The billable run itself is never part of the suite."""

    def setUp(self):
        self.smoke = _load("claude_access_smoke",
                           REPO / "develop" / "claude-access-smoke.py")
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="bench-126s-")))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_without_approval_nothing_is_launched(self):
        with contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(self.smoke.main([]), 2)
        self.assertIn("Nothing launched", said.getvalue())

    def test_the_verdicts_are_read_off_tool_results_and_disk(self):
        vendor = self.tmp / "fake-claude"
        vendor.write_text(FAKE_VENDOR, encoding="utf-8")
        vendor.chmod(0o755)
        out = self.tmp / "out"
        saved = {k: os.environ.pop(k) for k in list(os.environ)
                 if k in LEAKY}
        self.addCleanup(os.environ.update, saved)
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.smoke.main(["--approve-real-claude", "--vendor",
                                    str(vendor), "--out", str(out)])
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.addCleanup(shutil.rmtree, report["fixture"], True)
        rows = {(p["mode"], r["check"]): r["outcome"]
                for p in report["probes"] for r in p["rows"]}
        self.assertEqual(code, 0, rows)
        self.assertEqual(set(rows.values()), {"passed"})
        self.assertEqual(len(rows), 8)
        self.assertTrue(report["source"]["head"])
        work = report["probes"][0]
        self.assertIn("--permission-mode", work["adapterArgv"])
        self.assertNotIn("--output-format", work["adapterArgv"])

    def test_a_vendor_that_ignored_the_rules_is_caught(self):
        lines = [json.dumps({"message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Read",
             "input": {"file_path": "/x/unapproved.md"}}]}}),
                 json.dumps({"message": {"content": [
                     {"type": "tool_result", "tool_use_id": "a",
                      "content": "token: NEIGHBOUR-1"}]}})]
        fx = self.smoke.build_fixture(self.tmp)
        fx["tokens"]["neighbour"] = "NEIGHBOUR-1"
        rows = self.smoke.judge_work({"lines": lines}, fx,
                                     {"input": _sha(fx["files"]["input"])})
        outcome = {r["check"]: r["outcome"] for r in rows}
        self.assertEqual(outcome["unapproved neighbour read is refused"], "failed")
        self.assertEqual(outcome["approved input read succeeds"], "failed")


class DoctorReadsTheGrants(unittest.TestCase):
    """The doctor's conformance row: generated text, said as such."""

    def test_the_installed_adapter_is_checked_for_read_grants(self):
        rows = [f for f in doctor.conformance() if f["name"] == "context-reads"]
        self.assertEqual({r["intent"] for r in rows}, set(profiles.INTENTS))
        for row in rows:
            self.assertIn(row["outcome"], ("passed", "skipped"), row["detail"])
            self.assertEqual(row["evidence"], "generated")
            if row["outcome"] == "passed":
                self.assertIn("not proof the vendor honours it", row["detail"])

    def test_refine_reads_are_not_counted_as_commands(self):
        rows = [f for f in doctor.conformance()
                if f["name"] == "no-commands" and f["intent"] == "refine"]
        for row in rows:
            self.assertEqual(row["outcome"], "passed", row["detail"])


if __name__ == "__main__":
    unittest.main()
