"""Choosing agent models per launch intent (task 12): the BOARD_AGENT_MODEL
settings resolve intent → model in config, travel to the adapter as
AGENT_MODEL (absent when empty — never an empty flag value), and each
adapter renders the opaque name natively. With nothing set, launches are
byte-identical to the inherit-everything behaviour.

The `run` scripts are exercised end-to-end against stub binaries
(BOARD_CLAUDE_BIN / BOARD_OPENCODE_BIN), the same seam a live board uses.
Run with: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
CLAUDE = CORE / "adapters" / "claude"
OPENCODE = CORE / "adapters" / "opencode"

sys.path.insert(0, str(CORE))

import agents  # noqa: E402
import config  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


permission_config = _load("permission_config", OPENCODE / "permission_config.py")

# Neutralize any local/.env or shell leakage: process env beats .env, and
# an empty value is exactly "nothing configured".
UNSET = {"BOARD_AGENT_MODEL": "", "BOARD_AGENT_MODEL_WORK": "",
         "BOARD_AGENT_MODEL_ACT_PR": "", "BOARD_AGENT_MODEL_REVIEW": ""}


def _resolve(settings: dict) -> dict:
    """config.agent_model per intent, in a fresh interpreter so the given
    settings are what config reads at import."""
    env = dict(os.environ)
    env.update(UNSET)
    env.update(settings)
    out = subprocess.check_output(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import config, json; "
         "print(json.dumps({m: config.agent_model(m) "
         "for m in ('work', 'act-pr', 'review')}))",
         str(CORE)],
        env=env, text=True)
    return json.loads(out)


class ModelResolution(unittest.TestCase):
    def test_nothing_set_means_inherit_for_every_intent(self):
        self.assertEqual(_resolve({}),
                         {"work": "", "act-pr": "", "review": ""})

    def test_the_general_setting_covers_all_intents(self):
        self.assertEqual(_resolve({"BOARD_AGENT_MODEL": "vendor-x"}),
                         {"work": "vendor-x", "act-pr": "vendor-x",
                          "review": "vendor-x"})

    def test_a_per_intent_setting_beats_the_general_one_for_that_intent_only(self):
        resolved = _resolve({"BOARD_AGENT_MODEL": "big",
                             "BOARD_AGENT_MODEL_REVIEW": "cheap"})
        self.assertEqual(resolved,
                         {"work": "big", "act-pr": "big", "review": "cheap"})

    def test_per_intent_alone_leaves_the_others_inheriting(self):
        resolved = _resolve({"BOARD_AGENT_MODEL_ACT_PR": "pusher"})
        self.assertEqual(resolved,
                         {"work": "", "act-pr": "pusher", "review": ""})


class LaunchEnv(unittest.TestCase):
    """_launch's half of the contract: AGENT_MODEL set iff a model is
    configured — absent means absent, even against a leaky environment."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.capture = tmp / "env.json"
        adapter = tmp / "stub-adapter"
        adapter.mkdir()
        run = adapter / "run"
        run.write_text("#!/usr/bin/env python3\n"
                       "import json, os\n"
                       f"open({str(self.capture)!r}, 'w').write(json.dumps(dict(os.environ)))\n")
        run.chmod(run.stat().st_mode | stat.S_IXUSR)
        self._saved = (config.AGENT_MODEL, config.AGENT_MODELS,
                       config.adapter_dir, config.child_env)
        config.adapter_dir = lambda: adapter
        # A stray AGENT_MODEL inherited by the board process must not leak.
        config.child_env = lambda: {"PATH": os.environ.get("PATH", ""),
                                    "AGENT_MODEL": "stray-from-the-shell"}

    def tearDown(self):
        (config.AGENT_MODEL, config.AGENT_MODELS,
         config.adapter_dir, config.child_env) = self._saved
        self._tmp.cleanup()

    def _launch_env(self, mode: str) -> tuple[dict, str]:
        log = Path(self._tmp.name) / "job.log"
        proc, log_file, model = agents._launch(
            mode, "do the task", Path(self._tmp.name), "id-1", "t.md", log)
        proc.wait()
        log_file.close()
        return json.loads(self.capture.read_text()), model

    def test_no_model_configured_means_no_variable_at_all(self):
        config.AGENT_MODEL = ""
        config.AGENT_MODELS = {"work": "", "act-pr": "", "review": ""}
        env, model = self._launch_env("work")
        self.assertNotIn("AGENT_MODEL", env)
        self.assertEqual(model, "")

    def test_the_resolved_model_arrives_as_agent_model(self):
        config.AGENT_MODEL = "big"
        config.AGENT_MODELS = {"work": "", "act-pr": "", "review": "cheap"}
        env, model = self._launch_env("review")
        self.assertEqual(env["AGENT_MODEL"], "cheap")
        self.assertEqual(model, "cheap")
        env, model = self._launch_env("work")
        self.assertEqual(env["AGENT_MODEL"], "big")
        self.assertEqual(model, "big")


class AgentRecord(unittest.TestCase):
    def test_public_record_carries_the_model_none_means_inherited(self):
        base = {"id": "a", "task": "t.md", "branch": None, "worktree": None,
                "status": "running", "rc": None, "started": 0.0,
                "session": None, "mode": "review", "name": "Wren"}
        self.assertIsNone(agents._agent_public(base)["model"])
        self.assertEqual(
            agents._agent_public({**base, "model": "cheap"})["model"], "cheap")


def _write_stub(directory: Path, name: str, script: str) -> Path:
    stub = directory / name
    stub.write_text(script, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


class ClaudeRunModel(unittest.TestCase):
    """AGENT_MODEL reaches the claude launch as --model; absent (or empty,
    which must never happen but costs nothing to survive) = no flag."""

    def _run(self, model: str | None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "args.json"
            stub = _write_stub(Path(tmp), "claude-stub",
                               "#!/usr/bin/env python3\n"
                               "import json, sys\n"
                               f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            env = dict(os.environ)
            env.pop("AGENT_MODEL", None)
            env.update({"BOARD_CLAUDE_BIN": str(wrapper),
                        "AGENT_PROMPT": "do the task", "AGENT_MODE": "work",
                        "AGENT_COMMANDS": "python3 -m unittest"})
            if model is not None:
                env["AGENT_MODEL"] = model
            result = subprocess.run(["bash", str(CLAUDE / "run")], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(capture.read_text())

    def test_unset_launches_byte_identical_to_today(self):
        args = self._run(None)
        self.assertNotIn("--model", args)
        self.assertEqual(args, self._run(""))  # empty behaves like absent

    def test_set_appends_model_and_changes_nothing_else(self):
        args = self._run("claude-model-x")
        i = args.index("--model")
        self.assertEqual(args[i + 1], "claude-model-x")
        self.assertEqual(args[:i] + args[i + 2:], self._run(None))


class OpencodeRunModel(unittest.TestCase):
    """AGENT_MODEL reaches the opencode launch as the generated config's
    model key; absent = no key, config byte-identical to today."""

    def _run(self, model: str | None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "capture.json"
            stub = _write_stub(Path(tmp), "opencode-stub",
                               "#!/usr/bin/env python3\n"
                               "import json, os\n"
                               f"open({str(capture)!r}, 'w').write("
                               "json.dumps(json.load(open(os.environ['OPENCODE_CONFIG']))))\n")
            wrapper = _write_stub(Path(tmp), "bin",
                                  f"#!/usr/bin/env bash\nexec python3 {stub} \"$@\"\n")
            env = dict(os.environ)
            env.pop("AGENT_MODEL", None)
            env.update({"BOARD_OPENCODE_BIN": str(wrapper),
                        "AGENT_PROMPT": "do the task", "AGENT_MODE": "review",
                        "AGENT_COMMANDS": "python3 -m unittest"})
            if model is not None:
                env["AGENT_MODEL"] = model
            result = subprocess.run(["bash", str(OPENCODE / "run")], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(capture.read_text())

    def test_unset_launches_byte_identical_to_today(self):
        cfg = self._run(None)
        self.assertNotIn("model", cfg)
        self.assertEqual(cfg, permission_config.build_config(
            "review", ["python3 -m unittest"]))

    def test_set_lands_as_the_config_model_key_untranslated(self):
        cfg = self._run("anthropic/model-x")
        self.assertEqual(cfg["model"], "anthropic/model-x")
        del cfg["model"]
        self.assertEqual(cfg, self._run(None))

    def test_build_config_only_grows_the_key_when_given_a_model(self):
        commands = ["python3 -m unittest"]
        self.assertNotIn("model", permission_config.build_config("work", commands))
        self.assertEqual(
            permission_config.build_config("work", commands, "p/m")["model"], "p/m")


if __name__ == "__main__":
    unittest.main()
