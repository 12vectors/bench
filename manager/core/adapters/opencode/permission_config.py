#!/usr/bin/env python3
"""Print the opencode config JSON for one headless launch: the permission
rules for the launch's intent, plus the model when the board configured one.

Usage: permission_config.py [work|act-pr|review]

Same three stances as every adapter (the contract is in
core/adapters/README.md), rendered in opencode's native rule language:
glob patterns over the whole command line, last match wins, so "*" deny
comes first and the specific allows override it. Headless runs have no
human at a prompt — "ask" would hang — so every rule is allow or deny,
and never a blanket allow: the worktree is isolated, the shell is not.

  work    "edit": "allow" + git bookkeeping (add/commit/status/diff) and
          the project's test/check commands. No push.
  act-pr  the work stance + `git push` + reading the PR's reviews and
          line comments through gh.
  review  "edit": "deny" + reading the PR it judges + posting the
          verdict with gh pr review/comment. Everything else denied.

The project's test/check commands arrive in AGENT_COMMANDS as comma-
separated neutral command prefixes (set BOARD_AGENT_COMMANDS in
local/.env); here each becomes "<prefix>" and "<prefix> *" allow rules.

AGENT_MODEL, when set, becomes the config's top-level "model" key —
opencode's "provider/model-id" form (opencode.ai/docs/config), passed
through untranslated. Absent = no key, opencode's own resolution applies.
"""
import json
import os
import sys

# Universal git/gh prefixes per intent; project commands are appended.
MODE_PREFIXES = {
    "work": ["git add", "git commit", "git status", "git diff"],
    "act-pr": ["git add", "git commit", "git status", "git diff",
               "git push", "gh pr view", "gh pr diff", "gh api"],
    "review": ["git status", "git diff", "git log", "git show",
               "gh pr view", "gh pr diff", "gh pr review", "gh pr comment"],
}
# Which intents run the project's own test/check commands.
MODES_WITH_PROJECT_COMMANDS = {"work", "act-pr"}


def split_commands(raw: str) -> list[str]:
    """AGENT_COMMANDS: comma-separated neutral command prefixes."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def bash_rules(mode: str, commands: list[str]) -> dict:
    """Deny everything, then allow each prefix exactly and any longer
    command starting with it. Insertion order is the rule order."""
    prefixes = list(MODE_PREFIXES.get(mode, []))
    if mode in MODES_WITH_PROJECT_COMMANDS:
        prefixes += [c for c in commands if c not in prefixes]
    rules = {"*": "deny"}
    for prefix in prefixes:
        rules[prefix] = "allow"
        rules[f"{prefix} *"] = "allow"
    return rules


def build_config(mode: str, commands: list[str], model: str = "") -> dict:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "deny" if mode == "review" else "allow",
            "bash": bash_rules(mode, commands),
        },
    }
    if model:
        config["model"] = model
    return config


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "work"
    commands = split_commands(os.environ.get("AGENT_COMMANDS", ""))
    model = os.environ.get("AGENT_MODEL", "").strip()
    print(json.dumps(build_config(mode, commands, model)))
