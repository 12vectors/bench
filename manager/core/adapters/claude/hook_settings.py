#!/usr/bin/env python3
"""Print the --settings JSON that wires this adapter's event bridge into a
headless Claude session. Absolute path to emit.py, so it works from any
worktree regardless of what that checkout contains."""
import json
from pathlib import Path

EMIT = Path(__file__).resolve().parent / "emit.py"
hook = {"type": "command", "command": f'python3 "{EMIT}"', "timeout": 5}
plain = [{"hooks": [hook]}]
print(json.dumps({"hooks": {
    "SessionStart": plain,
    "Stop": plain,
    "SessionEnd": plain,
    "PreToolUse": [{"matcher": "Bash", "hooks": [hook]}],
    "PostToolUse": [{"matcher": "*", "hooks": [hook]}],
}}))
