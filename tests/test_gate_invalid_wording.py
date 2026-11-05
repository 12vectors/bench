"""An invalid gate policy is not an absent one (task 66 follow-up).

A gate block that does not resolve — `{"name": "unit", "scope": "all"}`
where a requirement takes `"check"` — blocks every scope. The page said
so in its Blocked panel and then, a few lines lower, said the opposite:
"no requirement", "this board declares no gate", "No gate is
configured". Both readings came from the same payload, because a broken
policy is `configured: false` and `gated: false` as well as `blocked`.

These render the page's own functions in node, fed with what the real
resolver and evaluator return for a malformed document and for a board
that declares nothing, so the wording is tested against the data it is
actually given.

    python3 -m unittest tests.test_gate_invalid_wording -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import gates  # noqa: E402
import profiles  # noqa: E402

from tests.test_integration_gates import CARD, PASSES, GateCase  # noqa: E402

BOARD = REPO / "manager" / "core" / "board.html"
NODE = shutil.which("node")

PARTS = (
    r"const esc = \(s\) =>.*?\);\n",
    r"const GATE_STATES = \{.*?\n\};",
    r"function gateState\(word\) \{.*?\n\}",
    r"function requirementLabel\(r\) \{.*?\n\}",
    r"function gateRow\(r\) \{.*?\n\}",
    r"function gatePanel\(decision\) \{.*?\n\}",
    r"async function gateSheet\(task\) \{.*?\n\}",
)

# The sentences that say a gate is absent. None may appear about a policy
# that does not resolve; the ungated board must keep saying them.
ABSENT = ("no requirement", "declares no gate", "No gate is configured")

STUBS = """
const S = { state: {} };
const els = {};
const el = (id) => els[id] || (els[id] = {
  innerHTML: '', value: '', classList: { add() {} }, addEventListener() {} });
const $ = (sel) => (sel === '#sh-override' ? null : el(sel));
const closeSheet = () => {}, toast = () => {}, loadState = async () => {};
const wireAction = () => {};
"""


def harness() -> str:
    page = BOARD.read_text(encoding="utf-8")
    parts = []
    for pattern in PARTS:
        found = re.search(pattern, page, re.S)
        if not found:
            raise AssertionError(f"board.html no longer has {pattern!r}")
        parts.append(found.group(0))
    return STUBS + "\n".join(parts)


@unittest.skipUnless(NODE, "node is needed to run the page's own rendering")
class InvalidIsNotUngated(GateCase):

    def setUp(self):
        super().setUp()
        self.src = harness()

    def malformed(self) -> None:
        """The operator's actual mistake: `name` where a requirement
        object takes `check`, and a scope the check does not declare."""
        self.declares({
            "v": 1,
            "checks": [{"name": "unit",
                        "run": str(self.executable("unit", PASSES)),
                        "root": "worktree"}],
            "profiles": {"work": {"verify": ["unit"]}},
            "gates": {"acceptance": {"checks": [{"name": "unit",
                                                 "scope": "all"}]}}})

    def payload(self) -> dict:
        """What /api/gates sends the sheet, built the way httpd builds it."""
        policy = profiles.gate_policy()
        return {"task": CARD, "stage": "in-progress",
                "scopes": [gates.public(gates.evaluate(CARD, "acceptance",
                                                       policy=policy))],
                "policy": profiles.gate_policy_public(policy),
                "verdicts": [], "github": None, "overrides": [], "actor": "t"}

    def sheet(self, data: dict) -> str:
        script = (self.src + f"\nconst DATA = {json.dumps(data)};\n"
                  "const fetch = async () => ({ ok: true, json: async () => DATA });\n"
                  "gateSheet({ file: 'x.md', stage: 'in-progress', title: 't',"
                  " number: '71' }).then(() =>"
                  " process.stdout.write(el('#sheetwrap').innerHTML));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def panel(self, decision: dict) -> str:
        script = (self.src + f"\nprocess.stdout.write(gatePanel("
                  f"{json.dumps(decision)}));")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    # — the premise: the payload is blocked, and also unconfigured/ungated —

    def test_the_malformed_policy_is_blocked_not_ungated(self):
        self.malformed()
        data = self.payload()
        (decision,) = data["scopes"]
        self.assertEqual(decision["decision"], "blocked")
        self.assertTrue(data["policy"]["blockers"])
        # the two flags the page used to read absence from
        self.assertFalse(decision["gated"])
        self.assertFalse(data["policy"]["configured"])

    # — the page —

    def test_the_sheet_never_says_absent_about_an_invalid_policy(self):
        self.malformed()
        html = self.sheet(self.payload())
        self.assertIn("Blocked", html)
        self.assertIn("does not resolve", html)
        for sentence in ABSENT:
            self.assertNotIn(sentence, html,
                             "an invalid policy was described as no gate")

    def test_the_panel_draws_the_policy_refusal_as_the_requirement(self):
        self.malformed()
        (decision,) = self.payload()["scopes"]
        html = self.panel(decision)
        self.assertIn("gate policy", html)
        self.assertIn("unmet", html)
        for sentence in ABSENT:
            self.assertNotIn(sentence, html)

    def test_the_sheet_says_invalid_is_not_ungated(self):
        self.malformed()
        html = self.sheet(self.payload())
        self.assertIn("not the same as having no gate", html)

    def test_a_board_that_declares_nothing_still_says_so(self):
        """Genuinely ungated keeps its words, exactly."""
        html = self.sheet(self.payload())
        self.assertIn("no requirement", html)
        self.assertIn("this board declares no gate for this", html)
        self.assertIn("No gate is configured, so nothing here is required "
                      "and nothing is proved.", html)
        self.assertNotIn("not the same as having no gate", html)


if __name__ == "__main__":
    unittest.main()
