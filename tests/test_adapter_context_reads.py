"""Task 126: approved read-only inputs become *actual* read access in the
Claude adapter — one exact `Read(//absolute/path)` grant per approved
file, and nothing broader.

Before this, `profiles.adapter_context` handed the adapter each pinned
input's path and hash, and the adapter generated no rule from it: a
headless run (no human at a permission prompt) was refused the very files
its prompt said it was approved to read. Run with:
python3 -m unittest discover -s tests
"""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLAUDE = REPO / "manager" / "core" / "adapters" / "claude"


def _entry(path: Path, name: str = "architecture-plan") -> dict:
    return {"name": name, "path": str(path),
            "source": f"work:reference/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "access": "read-only"}


class ApprovedContextBecomesARead(unittest.TestCase):
    """The regression the card asks for first: observed failing on the
    unchanged adapter, which rendered no rule at all from AGENT_CONTEXT."""

    def test_generated_settings_grant_an_exact_read_of_each_pinned_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            plan = root / "architecture-plan.md"
            plan.write_text("# plan\n", encoding="utf-8")
            env = dict(os.environ)
            for key in ("AGENT_GUIDANCE", "AGENT_PROFILE", "AGENT_PROGRESS"):
                env.pop(key, None)
            env.update({"AGENT_CONTEXT": json.dumps([_entry(plan)]),
                        "AGENT_COMMANDS": ""})
            result = subprocess.run(
                ["python3", str(CLAUDE / "hook_settings.py"), "work"],
                env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            allow = json.loads(result.stdout)["permissions"]["allow"]
            # `//` is filesystem-root absolute in Claude Code's rule
            # syntax; a single leading slash is relative to the settings.
            self.assertIn(f"Read(/{plan})", allow)
            self.assertTrue(f"Read(/{plan})".startswith("Read(//"))


if __name__ == "__main__":
    unittest.main()
