"""Task 122 (WP13): a running board executes the code that was on disk
when it started.

`tests/test_architecture.py` holds the source to it statically — no core
module imports another from inside a function, and `board.py` imports
every core module at its top level. This file holds the running process
to it:

- importing `board`, loading the selected hosting provider and wiring the
  composition puts every core module and that provider in memory, and
  does so without starting a thread, running a process or opening a
  connection;
- the one piece of wiring (`drive.on_ended` ← `previews.record_last`) is
  explicit, idempotent and survives a callback that cannot write;
- `settings.frozen_runs` reads the run registry from `state`, where it
  lives, with the rows it always produced.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

PROBE = r"""
import json, socket, subprocess, sys, threading
sys.path.insert(0, sys.argv[1])

# `config` resolves the tool, application and work roots when it is
# imported — with `git rev-parse`, as it did before WP13, and before any
# other core module could be loaded. What is recorded is everything after
# it: the eager imports, the provider load and the wiring WP13 added.
import config
before = set(sys.modules)

spawned, connected = [], []
real_popen = subprocess.Popen
class Recording(real_popen):
    def __init__(self, *a, **kw):
        spawned.append(repr(a[0] if a else kw.get("args"))[:120])
        super().__init__(*a, **kw)
subprocess.Popen = Recording
real_connect = socket.socket.connect
def connect(self, address):
    connected.append(repr(address))
    return real_connect(self, address)
socket.socket.connect = connect

import board
board.load_provider()
board.wire()
board.wire()          # idempotent: the hook is registered once

import drive, githost, previews
from pathlib import Path
core = Path(sys.argv[1])
modules = sorted(p.stem for p in core.glob("*.py"))
print(json.dumps({
    "missing": [m for m in modules if m not in sys.modules],
    "early": sorted(m for m in modules if m in before and m != "config"),
    "provider": githost.name(),
    "loaded": sorted(githost._LOADED),
    "hooks": [f"{h.__module__}.{h.__name__}" for h in drive._ENDED],
    "threads": threading.active_count(),
    "spawned": spawned,
    "connected": connected,
}))
"""


def _clean_env(tmp: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "AGENT_", "GIT_"))}
    env["HOME"] = tmp
    return env


class StartupLoadsEverything(unittest.TestCase):

    def probe(self, **extra) -> dict:
        with tempfile.TemporaryDirectory(prefix="bench-eager-") as tmp:
            env = dict(_clean_env(tmp), **extra)
            out = subprocess.run([sys.executable, "-c", PROBE, str(CORE)],
                                 env=env, cwd=tmp, text=True,
                                 capture_output=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-3000:])
        return json.loads(out.stdout.splitlines()[-1])

    def test_every_core_module_and_the_selected_provider_are_in_memory(self):
        found = self.probe()
        self.assertEqual(found["missing"], [],
                         "a core module was left to be imported on demand")
        self.assertIn(found["provider"], found["loaded"])

    def test_a_none_provider_board_loads_the_none_provider(self):
        found = self.probe(BOARD_GIT_PROVIDER="none")
        self.assertEqual(found["provider"], "none")
        self.assertEqual(found["loaded"], ["none"])

    def test_loading_starts_nothing_and_asks_nothing(self):
        found = self.probe()
        # only config's own leaf (gitproc) may precede the recording
        self.assertLessEqual(set(found["early"]), {"gitproc"})
        self.assertEqual(found["threads"], 1, "an import started a thread")
        self.assertEqual(found["spawned"], [],
                         "an import or the provider load ran a process")
        self.assertEqual(found["connected"], [],
                         "an import or the provider load opened a connection")

    def test_the_drive_end_hook_is_wired_once(self):
        self.assertEqual(self.probe()["hooks"], ["previews.record_last"])


class TheDriveEndHook(unittest.TestCase):

    def setUp(self):
        import drive
        self.drive = drive
        self.addCleanup(setattr, drive, "_ENDED", list(drive._ENDED))
        drive._ENDED.clear()

    def test_registered_callbacks_are_called_when_a_drive_ends(self):
        calls = []
        self.drive.on_ended(lambda: calls.append("ended"))
        self.drive._remember()
        self.assertEqual(calls, ["ended"])

    def test_registering_twice_keeps_one(self):
        def hook():
            pass
        self.drive.on_ended(hook)
        self.drive.on_ended(hook)
        self.assertEqual(self.drive._ENDED, [hook])

    def test_a_callback_that_cannot_write_does_not_stop_the_drive(self):
        calls = []

        def unwritable():
            raise OSError("read-only state directory")
        self.drive.on_ended(unwritable)
        self.drive.on_ended(lambda: calls.append("next"))
        self.drive._remember()            # nothing raised
        self.assertEqual(calls, ["next"])

    def test_nothing_registered_is_nothing_called(self):
        self.drive._remember()

    def test_the_scrubber_belongs_to_the_drive(self):
        self.assertEqual(self.drive.scrub("v=local-dev-value",
                                          ["local-dev-value"]), "v=«secret»")
        import previews
        self.assertFalse(hasattr(previews, "scrub"),
                         "a second scrubber would be a second owner")


class FrozenRunsReadTheRegistry(unittest.TestCase):

    def test_a_live_run_with_a_manifest_is_listed_as_it_always_was(self):
        import settings
        import state
        record = {"id": "agent-wp13", "task": "07-x.md", "name": "Wren",
                  "status": "running", "proc": None}
        frozen = {"policyId": "pol-1", "source": {"revision": "rev-1"}}
        with mock.patch.dict(state.AGENTS, {"agent-wp13": record}, clear=True), \
                mock.patch("profiles.recorded",
                           side_effect=lambda i: frozen if i == "agent-wp13" else None), \
                mock.patch("profiles.freshness", return_value={"state": "fresh"}):
            rows = settings.frozen_runs()
        self.assertEqual(rows, [{
            "agent": "agent-wp13", "name": "Wren", "mode": "work",
            # the public row never carried a `file` key, so `task` has
            # always been empty here; WP13 changes where it is read, not
            # what it says
            "task": None,
            "policyId": "pol-1", "revision": "rev-1",
            "freshness": {"state": "fresh"},
            "why": ("this run keeps the policy it launched with. A settings "
                    "change applies to the next launch"),
        }])

    def test_a_run_without_a_manifest_is_not_listed(self):
        import settings
        import state
        with mock.patch.dict(state.AGENTS, {"a": {"id": "a"}}, clear=True), \
                mock.patch("profiles.recorded", return_value=None):
            self.assertEqual(settings.frozen_runs(), [])


if __name__ == "__main__":
    unittest.main()
