"""The `refine` launch intent, resolved the same way everywhere (task 65).

A card refinement reads and proposes; bench writes. These cases hold the
intent to that through every layer that names intents: the profile
resolver, both shipped adapters (their `run` against a stub vendor
binary, and their `describe`), the readiness doctor, Settings → Agent
access, and the HTTP routes — and they check that reading any of it
launches nothing.

    python3 -m unittest tests.test_refine_intent -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import doctor  # noqa: E402
import httpd  # noqa: E402
import profiles  # noqa: E402
import refine  # noqa: E402
import settings  # noqa: E402
import state  # noqa: E402

CLAUDE = CORE / "adapters" / "claude"
OPENCODE = CORE / "adapters" / "opencode"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_settings = _load("hook_settings_refine", CLAUDE / "hook_settings.py")
permission_config = _load("permission_config_refine", OPENCODE / "permission_config.py")

PROFILE = {"commands": [{"name": "unit", "prefix": "python3 -m unittest",
                         "root": "worktree"}],
           "deny": ["edit", "shell", "commit", "push"]}


def run_adapter(adapter: Path, stub_env: str, mode: str,
                profile: dict | None = None) -> tuple[int, dict, str]:
    """The adapter's own `run`, with a stub standing in for the vendor:
    what it would actually hand the binary for one launch."""
    with tempfile.TemporaryDirectory() as tmp:
        capture = Path(tmp) / "seen.json"
        stub = Path(tmp) / "vendor"
        stub.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            f"cfg = os.environ.get('OPENCODE_CONFIG')\n"
            f"json.dump({{'argv': sys.argv[1:], 'config': "
            f"json.load(open(cfg)) if cfg else None}}, open({str(capture)!r}, 'w'))\n",
            encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("AGENT_")}
        env.update({stub_env: str(stub), "AGENT_MODE": mode,
                    "AGENT_PROMPT": "refine this", "AGENT_COMMANDS": "npm test"})
        if profile is not None:
            env["AGENT_PROFILE"] = json.dumps(profile)
        result = subprocess.run(["bash", str(adapter / "run")], env=env,
                                capture_output=True, text=True, cwd=tmp)
        seen = (json.loads(capture.read_text(encoding="utf-8"))
                if capture.is_file() else {})
        return result.returncode, seen, result.stderr


class ClaudeRefine(unittest.TestCase):
    def test_run_withholds_edit_tools_and_the_shell(self):
        rc, seen, err = run_adapter(CLAUDE, "BOARD_CLAUDE_BIN", "refine", PROFILE)
        self.assertEqual(rc, 0, err)
        argv = seen["argv"]
        at = argv.index("--disallowedTools")
        self.assertEqual(argv[at + 1:], ["Edit", "Write", "NotebookEdit", "Bash"])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
        settings_doc = json.loads(argv[argv.index("--settings") + 1])
        self.assertNotIn("allow", settings_doc.get("permissions", {}),
                         "not even a profile's grants are rendered")

    def test_run_matches_what_describe_reports(self):
        rc, seen, _ = run_adapter(CLAUDE, "BOARD_CLAUDE_BIN", "refine")
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_")}
        env.update(AGENT_MODE="refine", AGENT_PROMPT="refine this",
                   AGENT_COMMANDS="npm test")
        described = json.loads(subprocess.run(
            ["python3", "describe.py"], cwd=CLAUDE, env=env,
            capture_output=True, text=True, check=True).stdout)
        self.assertEqual(described["argv"], seen["argv"])
        self.assertTrue(described["readOnly"])
        self.assertTrue(described["noShell"])
        self.assertEqual(described["allow"], [])

    def test_review_is_byte_for_byte_what_it_was(self):
        rc, seen, _ = run_adapter(CLAUDE, "BOARD_CLAUDE_BIN", "review")
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--disallowedTools") + 1:],
                         ["Edit", "Write", "NotebookEdit"])

    def test_an_unknown_mode_is_refused_rather_than_run_as_work(self):
        rc, seen, err = run_adapter(CLAUDE, "BOARD_CLAUDE_BIN", "implement-everything")
        self.assertEqual(rc, 64)
        self.assertEqual(seen, {}, "the vendor binary was never launched")
        self.assertIn("unknown AGENT_MODE", err)


class OpencodeRefine(unittest.TestCase):
    def test_run_denies_edit_and_every_command(self):
        rc, seen, err = run_adapter(OPENCODE, "BOARD_OPENCODE_BIN", "refine", PROFILE)
        self.assertEqual(rc, 0, err)
        permission = seen["config"]["permission"]
        self.assertEqual(permission["edit"], "deny")
        self.assertEqual(permission["bash"], {"*": "deny"})

    def test_describe_reports_no_shell(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_")}
        env.update(AGENT_MODE="refine", AGENT_PROMPT="x")
        described = json.loads(subprocess.run(
            ["python3", "describe.py"], cwd=OPENCODE, env=env,
            capture_output=True, text=True, check=True).stdout)
        self.assertTrue(described["readOnly"])
        self.assertTrue(described["noShell"])
        self.assertEqual(described["allow"], [])
        work = permission_config.build_config("work", ["npm test"])
        self.assertEqual(work["permission"]["edit"], "allow",
                         "the other intents are untouched")

    def test_an_unknown_mode_is_refused(self):
        rc, seen, err = run_adapter(OPENCODE, "BOARD_OPENCODE_BIN", "nope")
        self.assertEqual(rc, 64)
        self.assertEqual(seen, {})


class Resolution(unittest.TestCase):
    """The profile resolver, through each shipped adapter and a document."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-refine-intent-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr in ("ADAPTER", "AGENT_PROFILES", "LOCAL"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
        config.LOCAL = self.tmp / "local"
        config.AGENT_PROFILES = ""

    def resolve(self, doc: dict | None = None) -> dict:
        if doc is None:
            return profiles.resolve("refine")
        path = self.tmp / "profiles.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return profiles.resolve("refine", policy=profiles.document_at(path))

    def test_both_shipped_adapters_resolve_it_with_nothing_granted(self):
        for adapter in ("claude", "opencode"):
            with self.subTest(adapter):
                config.ADAPTER = adapter
                found = self.resolve()
                self.assertEqual(found["blockers"], [])
                self.assertEqual(found["commands"], [])
                self.assertEqual(found["verify"], [])
                self.assertEqual(found["writable"], [])
                self.assertEqual(found["cwd"]["policy"], "app")
                self.assertNotIn("worktree", found["roots"])
                self.assertIn("shell", [d["capability"] for d in found["denied"]])
                self.assertFalse(any(found["publication"].values()))
                for need in profiles.REFINE_REQUIRES:
                    self.assertIn(need, found["guarantees"]["enforced"])

    def test_a_document_cannot_widen_it(self):
        config.ADAPTER = "claude"
        cases = {
            "commands": {"commands": [{"name": "x", "run": "npm test"}]},
            "verify": {"verify": ["unit"]},
            "publication": {"publication": {"commit": True}},
        }
        for name, extra in cases.items():
            with self.subTest(name):
                found = self.resolve({"v": 1, "checks": [
                    {"name": "unit", "run": "python3 -m unittest", "root": "worktree"}],
                    "profiles": {"refine": extra}})
                self.assertTrue(found["blockers"], name)
                self.assertEqual(found["commands"], [])

    def test_shared_commands_and_checks_never_reach_it(self):
        config.ADAPTER = "claude"
        found = self.resolve({"v": 1, "commands": [{"name": "x", "run": "npm test"}],
                              "checks": [{"name": "unit", "run": "python3 -m unittest",
                                          "root": "worktree"}],
                              "verify": ["unit"], "profiles": {}})
        self.assertEqual(found["blockers"], [])
        self.assertEqual(found["commands"], [])
        self.assertEqual(found["verify"], [])

    def test_shell_may_not_be_denied_where_no_adapter_can_withhold_it(self):
        config.ADAPTER = "claude"
        path = self.tmp / "profiles.json"
        path.write_text(json.dumps({"v": 1, "profiles": {"work": {"deny": ["shell"]}}}),
                        encoding="utf-8")
        found = profiles.resolve("work", worktree=self.tmp / "wt",
                                 policy=profiles.document_at(path))
        self.assertIn("deny-unknown", [d["code"] for d in found["diagnostics"]])

    def test_the_legacy_intents_still_resolve_through_an_undeclared_adapter(self):
        adapter = {"name": "old", "intents": None}
        for intent in profiles.LEGACY_INTENTS:
            self.assertTrue(profiles.intent_supported(adapter, intent))
        self.assertFalse(profiles.intent_supported(adapter, "refine"))


class Inspection(unittest.TestCase):
    """The doctor and Settings read the intent; neither launches it."""

    def setUp(self):
        for attr in ("ADAPTER", "AGENT_PROFILES"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
        config.ADAPTER = "claude"
        config.AGENT_PROFILES = ""
        state.AGENTS.clear()
        self.addCleanup(state.AGENTS.clear)

    def test_the_doctor_reads_it_and_runs_nothing(self):
        report = doctor.report([])
        intents = [r["intent"] for r in report["intents"]]
        self.assertIn("refine", intents)
        self.assertEqual(state.AGENTS, {})

    def test_conformance_holds_the_restricted_contract(self):
        for adapter in ("claude", "opencode"):
            with self.subTest(adapter):
                config.ADAPTER = adapter
                rows = [r for r in doctor.conformance()
                        if r.get("intent") == "refine"]
                by_check = {r["name"]: r["outcome"] for r in rows}
                for check in ("no-commands", "read-only", "no-shell", "invocation"):
                    self.assertEqual(by_check.get(check), "passed",
                                     f"{adapter} {check}: {rows}")

    def test_settings_agent_access_shows_it(self):
        access = settings.access()
        self.assertIn("refine", access["intents"])
        row = access["intents"]["refine"]
        self.assertEqual(row["commands"], [])
        self.assertIn("shell-withheld", access["capabilities"])
        self.assertEqual(state.AGENTS, {})


class Routes(unittest.TestCase):
    """The HTTP surface: a GET launches nothing, and a POST is validated."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-refine-http-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        tasks = self.tmp / "tasks"
        for slug in config.STAGE_DIRS:
            (tasks / slug).mkdir(parents=True)
        (tasks / "backlog" / "07-card.md").write_text(
            "# 07 — A card\n\n**Status:** Backlog\n\nBrief.\n", encoding="utf-8")
        for attr, value in (("TASKS", tasks), ("COMMIT_MOVES", False),
                            ("REFINE_DIR", self.tmp / "refine")):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        refine.reset()
        self.addCleanup(refine.reset)
        state.AGENTS.clear()
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda message: None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_comment_then_read_the_discussion(self):
        code, body = self.call("/api/card/comment", {"file": "07-card.md",
                                                     "stage": "backlog",
                                                     "body": "<b>hi</b>"})
        self.assertEqual(code, 200, body)
        self.assertFalse(body["comment"]["committed"])
        code, view = self.call("/api/card/discussion?file=07-card.md&stage=backlog")
        self.assertEqual(code, 200)
        self.assertEqual(view["entries"][0]["body"], "<b>hi</b>",
                         "stored as text; the page escapes it")
        self.assertEqual(state.AGENTS, {}, "reading and commenting launch nothing")

    def test_bad_requests_are_refused(self):
        for body in ({"file": "../x.md", "stage": "backlog", "body": "x"},
                     {"file": "07-card.md", "stage": "backlog", "body": ""},
                     {"file": "07-card.md", "stage": "nowhere", "body": "x"}):
            code, answer = self.call("/api/card/comment", body)
            self.assertEqual(code, 409, body)
            self.assertIn("error", answer)
        code, answer = self.call("/api/card/refine/apply", {"id": "p-nope"})
        self.assertEqual(code, 409)


if __name__ == "__main__":
    unittest.main()
