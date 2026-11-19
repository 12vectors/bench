"""Scoped orchestration ships whole, installs off and survives updates
(task 146).

Against a real artifact built by release.sh and real unpacks of it:

- the curated distribution carries every engine module, the
  Orchestrator view and its manifest entry — and no agreement, no
  designation and no orchestration state of bench's own;
- a fresh install, and the same install run again, lays down no
  agreement and no designation, and a pass of the scheduler reads `off`
  and launches nothing (D16: shipping is not authorization);
- an update from a release replaces core and leaves a project's
  agreement, its designation, its cards and its runtime records byte for
  byte, and the agreement reads exactly as it did before;
- an install that never enabled anything is still off after the update.

    python3 -m unittest tests.test_orchestration_install -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.test_release_artifact import build_artifact
from tests.test_update_from_release import CURL_STUB, GH_STUB

REPO = Path(__file__).resolve().parents[1]

# the engine, task by task (137–145): each must ship, or an installed
# board cannot import board.py
ENGINE = ("agreements", "execowner", "operations", "orchestrator", "movements",
          "decisions", "reasoning", "scheduler", "coordinate", "phasescope",
          "recovery")

AGREEMENT = {"v": 1, "bench": {"enabled": True, "paused": False,
                               "authority": {"implement": True}}}

PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import agreements, execowner, scheduler
reading = agreements.effective()
print(json.dumps({"configured": reading["configured"], "valid": reading["valid"],
                  "revision": reading["revision"],
                  "designation": execowner.read_designation()[0],
                  "pass": scheduler.run_pass()}))
"""


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "BENCH_", "STUB_", "AGENT_"))}
    env.update(extra)
    return env


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
            and "__pycache__" not in p.parts}


class _Artifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-orch-install-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = build_artifact(cls.tarball)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(f"release.sh failed:\n{result.stdout}{result.stderr}")
        with tarfile.open(cls.tarball) as tar:
            cls.files = {m.name.removeprefix("./") for m in tar.getmembers()
                         if m.isfile()}
        cls.version = (REPO / "manager" / "core" / "VERSION").read_text().strip()
        cls.stubs = cls.scratch / "bin"
        cls.stubs.mkdir()
        for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
            (cls.stubs / name).write_text(body, encoding="utf-8")
            (cls.stubs / name).chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def install(self) -> Path:
        host = Path(tempfile.mkdtemp(prefix="host-", dir=self.scratch))
        (host / ".claude").mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(host)], check=True,
                       capture_output=True)
        tm = host / ".task-manager"
        tm.mkdir()
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm, filter="tar")
        self.run_install(tm)
        return tm

    def run_install(self, tm: Path) -> subprocess.CompletedProcess:
        done = subprocess.run([sys.executable, str(tm / "install.py")], cwd=tm.parent,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=clean_env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def probe(self, tm: Path) -> dict:
        done = subprocess.run([sys.executable, "-c", PROBE, str(tm / "manager" / "core")],
                              cwd=tm.parent, capture_output=True, text=True,
                              env=clean_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.splitlines()[-1])

    def update(self, tm: Path) -> subprocess.CompletedProcess:
        env = clean_env(STUB_TAG=f"v{self.version}", STUB_TARBALL=str(self.tarball))
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(["bash", str(tm / "update.sh")], capture_output=True,
                              text=True, cwd=tm.parent, env=env)


class TheDistributionCarriesTheEngine(_Artifact):

    def test_every_engine_module_and_the_view_ship(self):
        for module in ENGINE:
            self.assertIn(f"manager/core/{module}.py", self.files, module)
        self.assertIn("manager/core/web/views/orchestrator.js", self.files)
        manifest = json.loads((REPO / "manager" / "core" / "web" /
                               "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("views/orchestrator.js", json.dumps(manifest),
                      "the page serves only what its asset manifest lists")
        # the reasoning intent is declared by the adapter that runs it
        caps = json.loads((REPO / "manager" / "core" / "adapters" / "claude" /
                           "capabilities.json").read_text(encoding="utf-8"))
        self.assertIn("reason", caps["intents"])

    def test_no_agreement_designation_or_state_ships(self):
        for name in self.files:
            self.assertNotIn("orchestration.json", name)
            self.assertNotIn("designation.json", name)
            self.assertFalse(name.startswith("manager/local/state"), name)
            self.assertFalse(name.startswith("orchestration/"), name)


class FreshAndRepeatedInstallsAreOff(_Artifact):

    def test_a_fresh_install_has_no_agreement_and_launches_nothing(self):
        tm = self.install()
        self.assertFalse((tm / "manager" / "local" / "orchestration.json").exists())
        self.assertFalse((tm / "orchestration").exists())
        found = self.probe(tm)
        self.assertEqual((found["configured"], found["designation"]), (False, None))
        self.assertEqual(found["pass"], {"ran": False, "why": "off"})

    def test_installing_again_writes_nothing_new_and_stays_off(self):
        tm = self.install()
        (tm / "tasks" / "to-do" / "7-waiting.md").write_text(
            "# 7 — Waiting\n\n**Status:** To Do\n**Priority:** High\n", encoding="utf-8")
        before = snapshot(tm)
        self.run_install(tm)
        after = snapshot(tm)
        self.assertEqual(sorted(set(after) - set(before)), [],
                         "a repeated install creates no file")
        for name in ("tasks/to-do/7-waiting.md", "manager/local/.env"):
            if name in before:
                self.assertEqual(after[name], before[name], name)
        self.assertEqual(self.probe(tm)["pass"], {"ran": False, "why": "off"})


class AnUpdateKeepsTheProjectsOrchestration(_Artifact):

    def test_the_agreement_designation_and_cards_survive_byte_for_byte(self):
        tm = self.install()
        local = tm / "manager" / "local"
        (local / "orchestration.json").write_text(json.dumps(AGREEMENT), encoding="utf-8")
        card = tm / "tasks" / "in-progress" / "7-working.md"
        card.write_text("# 7 — Working\n\n**Status:** In Progress\n**Priority:** High\n"
                        "\n## Orchestration record\n\n<!-- bench:orchestration "
                        "{\"v\": 1} -->\n", encoding="utf-8")
        designation = tm / "orchestration" / "designation.json"
        designation.parent.mkdir()
        designation.write_text(json.dumps({"v": 1, "installation": "i-elsewhere",
                                           "revision": 3}), encoding="utf-8")
        ledger = local / "state" / "orchestration" / "operations" / "op-x.json"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"key": "op-x", "status": "unknown"}', encoding="utf-8")
        kept = {p: p.read_bytes() for p in
                (local / "orchestration.json", card, designation, ledger)}
        before = self.probe(tm)
        (tm / "manager" / "core" / "VERSION").write_text("0\n")     # an older install

        done = self.update(tm)

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for path, content in kept.items():
            self.assertEqual(path.read_bytes(), content, f"{path} survived the update")
        self.run_install(tm)
        for path, content in kept.items():
            self.assertEqual(path.read_bytes(), content, f"{path} survived install.py")
        after = self.probe(tm)
        self.assertEqual((after["configured"], after["revision"]),
                         (True, before["revision"]),
                         "the agreement reads exactly as it did")
        # another installation's designation is not taken over: nothing
        # here executes, update or not (D13)
        self.assertEqual(after["pass"]["ran"], False)
        self.assertNotEqual(after["pass"]["why"], "off")

    def test_an_install_that_never_enabled_anything_is_off_after_the_update(self):
        tm = self.install()
        (tm / "manager" / "core" / "VERSION").write_text("0\n")
        done = self.update(tm)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse((tm / "manager" / "local" / "orchestration.json").exists())
        self.assertEqual(self.probe(tm)["pass"], {"ran": False, "why": "off"})


if __name__ == "__main__":
    unittest.main()
