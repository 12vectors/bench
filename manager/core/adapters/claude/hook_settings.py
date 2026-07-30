#!/usr/bin/env python3
"""Print the --settings JSON for one headless launch: the event-bridge
hooks plus the permission allowlist for the launch's intent.

Usage: hook_settings.py [work|act-pr|review]     (no argument = hooks only)

Headless runs have no human at a permission prompt, so anything not
auto-approved is auto-denied. Each intent is granted exactly the side
effects its own prompt demands — never bypassPermissions: the worktree
is isolated, the shell is not.

  work    file edits (acceptEdits in `run`) + local git bookkeeping
          (add/commit/status/diff) + the project's test/check commands.
          No push.
  act-pr  the work stance + `git push` (the PR must update) + reading
          the PR's reviews and line comments through gh + `git fetch`
          and `git merge` so a conflicted PR can be resolved by merging
          main into the branch. The branch is public, so resolution is
          additive only: rebase and the force-push spellings are denied
          outright (deny beats allow, catching what the plain
          `git push` prefix would otherwise cover).
  review  read-only (edit tools disallowed in `run`) + reading the PR
          it judges + posting the verdict with gh pr review/comment.

The project's test/check commands arrive in AGENT_COMMANDS as comma-
separated neutral command prefixes (set BOARD_AGENT_COMMANDS in
local/.env; contract in core/adapters/README.md). This adapter renders
them as Claude Code Bash() allow-rules; other adapters render the same
prefixes in their own rule syntax.

Absolute path to emit.py, so the hooks work from any worktree regardless
of what that checkout contains.
"""
import json
import os
import sys
from pathlib import Path

# Universal git/gh prefixes per intent; project commands are appended.
MODE_PREFIXES = {
    "work": ["git add", "git commit", "git status", "git diff"],
    "act-pr": ["git add", "git commit", "git status", "git diff",
               "git fetch", "git merge",
               "git push", "gh pr view", "gh pr diff", "gh api"],
    "review": ["git status", "git diff", "git log", "git show",
               "gh pr view", "gh pr diff", "gh pr review", "gh pr comment"],
}
# History must never rewrite under a public PR: deny the canonical force
# and rebase spellings even though nothing allows them — `git push` alone
# would otherwise cover `git push --force` by prefix. (Prefix rules can't
# catch a flag placed after the refspec; the prompt and the review loop
# guard the exotic spellings.)
MODE_DENY_PREFIXES = {
    "act-pr": ["git push --force", "git push -f", "git rebase"],
}
# Which intents run the project's own test/check commands.
MODES_WITH_PROJECT_COMMANDS = {"work", "act-pr"}


def split_commands(raw: str) -> list[str]:
    """AGENT_COMMANDS: comma-separated neutral command prefixes."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def allow_rules(mode: str, commands: list[str]) -> list[str]:
    """Bash() allow-rules for one intent: the exact prefix and any longer
    command starting with it."""
    prefixes = list(MODE_PREFIXES.get(mode, []))
    if mode in MODES_WITH_PROJECT_COMMANDS:
        prefixes += [c for c in commands if c not in prefixes]
    rules = []
    for prefix in prefixes:
        rules += [f"Bash({prefix})", f"Bash({prefix}:*)"]
    return rules


def deny_rules(mode: str) -> list[str]:
    """Bash() deny-rules for one intent — deny beats allow."""
    rules = []
    for prefix in MODE_DENY_PREFIXES.get(mode, []):
        rules += [f"Bash({prefix})", f"Bash({prefix}:*)"]
    return rules


def settings(mode: str, commands: list[str]) -> dict:
    emit = Path(__file__).resolve().parent / "emit.py"
    hook = {"type": "command", "command": f'python3 "{emit}"', "timeout": 5}
    plain = [{"hooks": [hook]}]
    out: dict = {"hooks": {
        "SessionStart": plain,
        "Stop": plain,
        "SessionEnd": plain,
        "PreToolUse": [{"matcher": "Bash", "hooks": [hook]}],
        "PostToolUse": [{"matcher": "*", "hooks": [hook]}],
    }}
    perms = {}
    rules = allow_rules(mode, commands)
    if rules:
        perms["allow"] = rules
    denies = deny_rules(mode)
    if denies:
        perms["deny"] = denies
    if perms:
        out["permissions"] = perms
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    commands = split_commands(os.environ.get("AGENT_COMMANDS", ""))
    print(json.dumps(settings(mode, commands)))
