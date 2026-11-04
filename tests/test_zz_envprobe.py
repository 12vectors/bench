"""Temporary probe — deleted before the branch is finished.

Re-runs the whole suite in a clean environment (the board's own
`child_env()` exports BOARD_APP_ROOT/BOARD_WORK_ROOT into every headless
launch, which is backlog card 43), so the failures that belong to this
branch can be told from the ones that belong to the launch.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class CleanRun(unittest.TestCase):
    def test_clean(self):
        if os.environ.get("BENCH_PROBE"):
            self.skipTest("the inner run")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_"))}
        env["BENCH_PROBE"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-v"],
            cwd=str(REPO), env=env, capture_output=True, text=True,
            timeout=1800)
        lines = [l for l in result.stderr.splitlines()
                 if l.startswith(("FAIL:", "ERROR:", "Ran ", "OK", "FAILED"))]
        raise AssertionError("\n".join(sorted(lines)))
