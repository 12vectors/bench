"""Scratch runner for this run only (not committed): runs SCRATCH_TARGETS
in a child with BOARD_/AGENT_ settings removed, as verify/backend does."""
import os
import subprocess
import sys
import unittest


class Scratch(unittest.TestCase):
    def test_run(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_"))}
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "zz_targets.txt")) as fh:
            targets = fh.read().split()
        out = subprocess.run([sys.executable, "-m", "unittest", *targets],
                             env=env, capture_output=True, text=True)
        lines = (out.stdout + out.stderr).splitlines()
        keep = [l for l in lines if l.startswith(("ERROR:", "FAIL:", "Ran ", "OK", "FAILED"))
                or "Error:" in l or "assert" in l.lower()]
        print("\n".join(keep[-int(os.environ.get("SCRATCH_LINES", "80")):]))
