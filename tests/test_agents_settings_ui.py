"""Settings → Agents in the page (task 77): the script still parses, the
section is wired the way the rest of Settings is, and the pieces that are
pure functions behave — run in node, as the other UI tests do.

    python3 -m unittest discover -s tests -t . -v
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")


def scripts() -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))


def lift(name: str) -> str:
    """One top-level function's source, by brace matching."""
    start = HTML.index(f"function {name}(")
    depth, i = 0, HTML.index("{", start)
    while True:
        ch = HTML[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:i + 1]
        i += 1


class ThePageScript(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available — the script is unchecked")

    def test_the_whole_script_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.js"
            path.write_text(scripts(), encoding="utf-8")
            out = subprocess.run([self.node, "--check", str(path)],
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def node_eval(self, body: str):
        out = subprocess.run([self.node, "-e", body], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_line_diff_marks_additions_and_removals(self):
        found = self.node_eval(lift("lineDiff") + """
console.log(JSON.stringify([
  lineDiff('a\\nb\\nc', 'a\\nb\\nc'),
  lineDiff('a\\nb\\nc', 'a\\nB\\nc\\nd'),
]));""")
        self.assertEqual(found[0], "")
        self.assertIn("- b", found[1])
        self.assertIn("+ B", found[1])
        self.assertIn("+ d", found[1])

    def test_the_summary_names_the_default_and_counts_overrides(self):
        page = {
            "rows": {
                "default": {"harness": {"value": "claude"}, "model": {"value": ""}},
                "work": {"harness": {"value": ""}, "model": {"value": ""}},
                "act-pr": {"harness": {"value": ""}, "model": {"value": "x"}},
                "review": {"harness": {"value": "opencode"}, "model": {"value": ""}},
            },
            "templates": [{"source": "local"}, {"source": "built-in"}],
        }
        found = self.node_eval(lift("agentsSummary") +
                               f"\nconsole.log(JSON.stringify(agentsSummary({json.dumps(page)})));")
        self.assertEqual(found, "claude · harness default · 2 purpose overrides "
                                "· 1 local prompt")


class TheSectionIsWiredLikeTheRest(unittest.TestCase):

    def test_it_is_a_configure_section_after_merge_rules(self):
        sections = re.search(r"const SETTINGS_SECTIONS = \[(.*?)\n\];", HTML, re.S).group(1)
        keys = re.findall(r"\['(\w+)', '([^']+)', '(\w+)'", sections)
        self.assertIn(("agents", "Agents", "Configure"), keys)

    def test_every_write_goes_through_the_agents_endpoints_with_the_header(self):
        body = lift("agentsPost")
        self.assertIn("/api/settings/agents/${step}", body)
        self.assertIn("headers: SETTINGS_HEADERS", body)

    def test_apply_is_armed_and_inputs_do_not_repaint_while_typing(self):
        wire = lift("wireAgents")
        self.assertIn("if (step === 'apply' && !d.armed)", wire)
        # text fields update the draft on input without a repaint
        self.assertIn("else el.addEventListener('input', commit)", wire)
        self.assertIn("S.settingsSection === 'agents' && S.agentsDraft && "
                      "S.agentsDraft.dirty", lift("settingsEditing"))

    def test_every_control_has_a_name(self):
        for fn in ("agentRow", "agentTemplate", "settingsAgents"):
            src = lift(fn)
            for tag in re.findall(r"<(?:button|select)\b[^`]*?>", src):
                if "aria-label" in tag:
                    continue
                self.assertRegex(tag, r'id="ag-', f"{fn}: {tag[:80]}")
            # every select/input/textarea with an id has a label for it
            for ident in re.findall(r'<(?:input|textarea)[^>]*id="(ag-[a-z]+)-', src):
                self.assertIn(f'<label for="{ident}-', src, fn)

    def test_text_from_the_server_is_escaped(self):
        tpl = lift("agentTemplate")
        self.assertIn("${esc(text)}</textarea>", tpl)
        self.assertIn("${esc(render.rendered)}", tpl)
        self.assertIn("${esc(diff || 'identical to the built-in')}", tpl)


if __name__ == "__main__":
    unittest.main()
