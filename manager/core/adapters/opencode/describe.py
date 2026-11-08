"""What this adapter would hand opencode for one launch — as JSON, in the
normalised shape core reads.

    python3 describe.py

    AGENT_MODE / AGENT_PROMPT / AGENT_COMMANDS / AGENT_PROFILE /
    AGENT_CONTEXT / AGENT_MODEL / AGENT_CWD in; one JSON object on stdout.

The counterpart of the claude adapter's `describe.py`, and it exists for
the same reason: bench's readiness doctor checks that every named grant
became a rule, that `review` was given no way to write and that the
working directory is the one bench named — and it does that without core
knowing anything about opencode's last-match-wins glob syntax.
Translating is this adapter's job, and so is answering for it.

It launches nothing: no opencode binary, no model, no config file written.
The contract and the normalised keys are documented in
core/adapters/README.md.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import permission_config  # noqa: E402


def described() -> dict:
    mode = os.environ.get("AGENT_MODE", "work")
    prompt = os.environ.get("AGENT_PROMPT", "")
    commands = permission_config.split_commands(
        os.environ.get("AGENT_COMMANDS", ""))
    profile = permission_config.load_profile(os.environ.get("AGENT_PROFILE"))
    model = os.environ.get("AGENT_MODEL", "").strip()
    config = permission_config.build_config(mode, commands, model, profile)

    bash = (config.get("permission") or {}).get("bash") or {}
    try:
        context = json.loads(os.environ.get("AGENT_CONTEXT") or "[]")
    except (TypeError, ValueError):
        context = []

    return {
        "adapter": "opencode",
        "intent": mode,
        # what `run` passes: the generated config reaches the launch
        # through OPENCODE_CONFIG rather than argv, so argv is all of it
        "argv": ["run", prompt],
        "cwd": os.environ.get("AGENT_CWD", ""),
        # opencode's rules are one map of globs to verdicts; allow and
        # deny are its two halves, which is the shape core reads
        "allow": [pattern for pattern, verdict in bash.items()
                  if verdict == "allow"],
        "deny": [pattern for pattern, verdict in bash.items()
                 if verdict == "deny"],
        "readOnly": (config.get("permission") or {}).get("edit") == "deny",
        # no allow anywhere in the bash map: no command can be launched
        "noShell": bool(bash) and all(v == "deny" for v in bash.values()),
        "context": context if isinstance(context, list) else [],
        # this adapter's own half, shown rather than interpreted
        "config": config,
        "configVia": "OPENCODE_CONFIG",
    }


if __name__ == "__main__":
    print(json.dumps(described()))
