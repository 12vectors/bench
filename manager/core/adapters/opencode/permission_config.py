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
          line comments through gh + `git fetch`/`git merge` so a
          conflicted PR can be resolved by merging main into the
          branch. The branch is public, so resolution is additive
          only: rebase and the force-push spellings get explicit deny
          rules, placed last so they win over the `git push *` allow.
  review  "edit": "deny" + reading the PR it judges + posting the
          verdict with gh pr review/comment. Everything else denied.

The project's test/check commands arrive in AGENT_COMMANDS as comma-
separated neutral command prefixes (set BOARD_AGENT_COMMANDS in
local/.env); here each becomes "<prefix>" and "<prefix> *" allow rules.

AGENT_MODEL, when set, becomes the config's top-level "model" key —
opencode's "provider/model-id" form (opencode.ai/docs/config), passed
through untranslated. Absent = no key, opencode's own resolution applies.

AGENT_PROFILE, when the board sets it, is the resolved execution profile
for this launch (core/profiles.py). It is read as a **narrowing** of the
stance above and never a widening: its named grants are the same project
prefixes resolved by name, and its `deny` words become deny rules placed
last, where last-match-wins makes them beat every allow. Absent, every
rule here is byte for byte what it was before profiles existed.
"""
import json
import os
import sys

# Universal git/gh prefixes per intent; project commands are appended.
MODE_PREFIXES = {
    "work": ["git add", "git commit", "git status", "git diff"],
    "act-pr": ["git add", "git commit", "git status", "git diff",
               "git fetch", "git merge",
               "git push", "gh pr view", "gh pr diff", "gh api"],
    "review": ["git status", "git diff", "git log", "git show",
               "gh pr view", "gh pr diff", "gh pr review", "gh pr comment"],
}
# History must never rewrite under a public PR: deny the force and rebase
# spellings even though nothing allows them — "git push *" would otherwise
# cover them. Globs run over the whole command line, so the flag is caught
# wherever it sits.
MODE_DENY_PATTERNS = {
    "act-pr": ["git rebase", "git rebase *",
               "git push --force*", "git push * --force*",
               "git push -f", "git push -f *", "git push * -f *"],
}
# Which intents run the project's own test/check commands.
MODES_WITH_PROJECT_COMMANDS = {"work", "act-pr"}

# A denied capability, in the profile's vocabulary, as the glob patterns
# that would exercise it. Narrowing only, and placed last so last-match-
# wins puts them over every allow above.
DENY_CAPABILITY_PATTERNS = {
    "commit": ["git commit", "git commit *"],
    "push": ["git push", "git push *"],
    "force-push": ["git push --force*", "git push * --force*", "git push -f",
                   "git push -f *", "git push * -f *"],
    "rebase": ["git rebase", "git rebase *"],
    "publish": ["gh pr create*", "gh pr merge*", "gh pr close*", "gh release*",
                "gh api * --method POST*", "gh api * -X POST*"],
    "reshape-lifecycle": ["git worktree*", "git reset*", "git checkout*",
                          "git switch*", "git branch*", "git update-ref*",
                          "git remote*"],
    # "edit" is the "edit" permission key, not a bash pattern.
    "edit": [],
}


def split_commands(raw: str) -> list[str]:
    """AGENT_COMMANDS: comma-separated neutral command prefixes."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def load_profile(raw: str | None) -> dict | None:
    """AGENT_PROFILE: the resolved execution profile for this launch, or
    None. Unparseable is None too — a malformed value must not read as an
    empty deny list, and core refuses the launch before it writes one."""
    if not (raw or "").strip():
        return None
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def profile_prefixes(profile: dict | None) -> list[str]:
    """The named command grants, as the prefixes a rule is made of."""
    if not profile:
        return []
    out = []
    for grant in profile.get("commands") or []:
        if isinstance(grant, dict) and str(grant.get("prefix") or "").strip():
            out.append(str(grant["prefix"]).strip())
    return out


def bash_rules(mode: str, commands: list[str],
               profile: dict | None = None) -> dict:
    """Deny everything, then allow each prefix exactly and any longer
    command starting with it. Insertion order is the rule order.

    With a profile, its named grants replace the AGENT_COMMANDS list and
    its denied capabilities are appended after the mode's own — so the
    narrowing always wins. Without one the dict is exactly what it was
    before profiles existed.
    """
    prefixes = list(MODE_PREFIXES.get(mode, []))
    project = profile_prefixes(profile) if profile else commands
    if mode in MODES_WITH_PROJECT_COMMANDS:
        prefixes += [c for c in project if c not in prefixes]
    rules = {"*": "deny"}
    for prefix in prefixes:
        rules[prefix] = "allow"
        rules[f"{prefix} *"] = "allow"
    denies = list(MODE_DENY_PATTERNS.get(mode, []))
    for word in (profile or {}).get("deny") or []:
        for pattern in DENY_CAPABILITY_PATTERNS.get(str(word), []):
            if pattern not in denies:
                denies.append(pattern)
    for pattern in denies:
        rules[pattern] = "deny"  # last, so it wins over the allows
    return rules


def build_config(mode: str, commands: list[str], model: str = "",
                 profile: dict | None = None) -> dict:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "deny" if (mode == "review" or "edit" in
                               ((profile or {}).get("deny") or []))
                    else "allow",
            "bash": bash_rules(mode, commands, profile),
        },
    }
    if model:
        config["model"] = model
    return config


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "work"
    commands = split_commands(os.environ.get("AGENT_COMMANDS", ""))
    model = os.environ.get("AGENT_MODEL", "").strip()
    print(json.dumps(build_config(
        mode, commands, model, load_profile(os.environ.get("AGENT_PROFILE")))))
