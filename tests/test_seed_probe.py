"""The real-agent seed probe's own machinery, held without spending a run
(task 193).

`develop/brain-agent-probe.py --seed --approve-real-agent` is the opt-in,
billable proof that the installed CLI honours a seed round's exact-read
grants. Nothing runs it by itself. This test runs it end to end with a
stub in place of the vendor binary, so a broken fixture, a refused
preflight or a verdict that cannot be computed is found here rather than
on the paid run:

- the plan prints and launches nothing without the approval flag;
- with a stub vendor the probe builds its split fixture, inspects the
  approved selection, launches one seed round, and reads the verdict off
  disk: the app and work facts are drafted and cited, no withheld token
  appears, the round started outside the fixture and the tool and its
  scratch directory is gone, and nothing was written;
- the stub posts no tool events, so the probe does **not** pass: a silent
  hook can never pass for a refusal.

The stub proves the probe, never the vendor.

    python3 -m unittest tests.test_seed_probe -v
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "develop" / "brain-agent-probe.py"

STUB = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
settings = json.loads(argv[argv.index("--settings") + 1])
cwd = Path(os.getcwd()).resolve()
memory = [str(d / n) for d in (cwd, *cwd.parents)
          for n in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md") if (d / n).exists()]
(Path(__file__).parent / "seen.json").write_text(json.dumps({"cwd": str(cwd),
    "memory": memory}))
draft = re.search(r'"draft": "(bd-[0-9a-f]+)"', prompt).group(1)
rnd = int(re.search(r'"round": (\d+)', prompt).group(1))
ids = dict(re.findall(r"- `((?:app|work):[^`]+)` .*?: `([^`]+)`", prompt))
allow = settings.get("permissions", {}).get("allow", [])
granted = {r[len("Read(/"):-1] for r in allow if r.startswith("Read(")}
found = {}
for ref, path in ids.items():
    if path in granted:
        found[ref] = re.findall(r"[A-Z]+-[0-9a-f]{16}", Path(path).read_text())
def quote(ref):
    return " ".join(found.get(ref, []))
def section(sid, body, claims, gaps=()):
    return {"section": sid, "title": sid, "summary": sid, "body": body,
            "claims": claims, "contradictions": [], "gaps": list(gaps),
            "uncertainty": None}
sections = [
    section("overview", "README " + quote("app:README.md"),
            [{"statement": "Orchard tracks trees", "status": "implemented",
              "authority": None, "sources": ["app:README.md", "app:server.py"]}]),
    section("how-it-works", "Code " + quote("app:server.py"),
            [{"statement": "serves the list", "status": "implemented",
              "authority": None, "sources": ["app:server.py"]}]),
    section("ways-of-working", "Policy " + quote("app:CONTRIBUTING.md"),
            [{"statement": "one approving review", "status": "implemented",
              "authority": "documented-policy", "sources": ["app:CONTRIBUTING.md"]}]),
    section("direction", "Plan " + quote("work:plans/roadmap.md"),
            [{"statement": "CSV export agreed", "status": "agreed-future",
              "authority": None, "sources": ["work:plans/roadmap.md"]}]),
    section("operations", "No operations source was approved.",
            [{"statement": "Deployment", "status": "unknown", "authority": None,
              "sources": []}],
            gaps=[{"what": "deployment", "why": "no operations source was approved"}]),
]
doc = {"v": 1, "kind": "bench-brain-seed-draft", "draft": draft, "round": rnd,
       "outcome": "proposal", "summary": "Drafted.", "sections": sections}
print("<<<BRAIN\n" + json.dumps(doc) + "\nBRAIN>>>")
'''


class SeedProbe(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-seed-probe-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_probe(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full = {k: v for k, v in os.environ.items()
                if not k.startswith(("BOARD_", "AGENT_"))}
        full.update(env or {})
        return subprocess.run([sys.executable, str(PROBE), *args], capture_output=True,
                              text=True, env=full, timeout=300)

    def test_without_approval_it_prints_the_plan_and_launches_nothing(self):
        stub = self.dir / "vendor"
        stub.write_text("#!/bin/sh\ntouch \"$0.called\"\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        done = self.run_probe("--seed", env={"BOARD_CLAUDE_BIN": str(stub)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("started outside", done.stdout)
        self.assertIn("Nothing was launched", done.stdout)
        self.assertFalse((self.dir / "vendor.called").exists())

    def test_a_stub_run_computes_every_verdict_and_cannot_pass_silently(self):
        stub = self.dir / "vendor" / "claude"
        stub.parent.mkdir()
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        out = self.dir / "out"
        done = self.run_probe("--seed", "--approve-real-agent", "--out", str(out),
                              env={"BOARD_CLAUDE_BIN": str(stub)})
        report = json.loads((out / "report.json").read_text())
        verdict = report["verdict"]
        self.assertEqual(verdict["status"], "proposed",
                         (verdict, report.get("manifest"), done.stderr[-2000:]))
        self.assertTrue(verdict["appFacts"], verdict)
        self.assertTrue(verdict["workFacts"], verdict)
        self.assertTrue(all(verdict["withheld"].values()), verdict)
        self.assertTrue(verdict["startedOutside"], verdict)
        self.assertTrue(verdict["scratchRemoved"], verdict)
        self.assertTrue(verdict["noWrite"], verdict)
        # the stub posts no tool events: the probe must not read that as a pass
        self.assertEqual(verdict["readsObserved"], 0)
        self.assertFalse(verdict["passed"])
        self.assertEqual(done.returncode, 1)
        seen = json.loads((stub.parent / "seen.json").read_text())
        self.assertEqual(seen["memory"], [], "the round started under an instruction file")
        self.assertEqual(sorted(report["cited"]),
                         ["app:CONTRIBUTING.md", "app:README.md", "app:server.py",
                          "work:plans/roadmap.md"])
        self.assertTrue(report["preflight"]["ready"])
        self.assertIn("not an OS sandbox", report["enforcement"])
        text = json.dumps(report)
        self.assertNotIn(str(self.dir), text.replace(str(out), ""))
        self.assertNotRegex(text, r"[A-Z]+-[0-9a-f]{16}", "a token reached the report")


if __name__ == "__main__":
    unittest.main()
