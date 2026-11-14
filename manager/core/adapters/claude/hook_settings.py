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
  refine  read the card and the code, propose a brief on stdout — and
          nothing else: no allow-rule at all, and `run` withholds the
          edit tools *and* the shell (DISALLOWED_TOOLS), so no command
          can be launched however a rule is spelled. Bench validates the
          proposal and owns every write.

The project's test/check commands arrive in AGENT_COMMANDS as comma-
separated neutral command prefixes (set BOARD_AGENT_COMMANDS in
local/.env; contract in core/adapters/README.md). This adapter renders
them as Claude Code Bash() allow-rules; other adapters render the same
prefixes in their own rule syntax.

AGENT_PROFILE, when the board sets it, is the resolved execution profile
for this launch (core/profiles.py): named command grants and an explicit
list of denied capabilities. It is read as a **narrowing** of the stance
above and never a widening — its grants are the same project prefixes by
another name, and its `deny` words become Bash() deny-rules, which beat
allows. Absent, every rule below is byte for byte what it was before
profiles existed, which is what keeps a board with no profile document
and a custom adapter working unchanged.

AGENT_CONTEXT and AGENT_GUIDANCE, when set, are the approved read-only
inputs and the outside workflow briefs: each becomes one exact
`Read(//path)` grant, validated first (see "approved read-only inputs"
below). Absent, nothing about the document changes.

Absolute path to emit.py, so the hooks work from any worktree regardless
of what that checkout contains.
"""
import hashlib
import json
import os
import re
import stat
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
    "refine": [],
}
# The vendor's own tool names an intent is launched without. Spelled here
# once so `run` and `describe.py` cannot disagree. A name the installed CLI
# does not have is refused outright and kills the launch (task 10), so
# re-check these against the installed CLI whenever the list is touched.
READ_ONLY_TOOLS = ["Edit", "Write", "NotebookEdit"]
DISALLOWED_TOOLS = {
    "review": READ_ONLY_TOOLS,
    "refine": READ_ONLY_TOOLS + ["Bash"],
}
# The intents this adapter launches. Anything else is refused by `run`
# rather than guessed into one of these stances.
INTENTS = ("work", "act-pr", "review", "refine")
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

# A denied capability, in the profile's vocabulary, as the command
# prefixes that would exercise it. Narrowing only: no entry here grants
# anything, and a prefix nothing allows is simply belt and braces. The
# caveat from MODE_DENY_PREFIXES applies to all of them — a prefix rule
# cannot see a flag placed after a positional argument, so the prompt and
# the review loop still guard the exotic spellings.
DENY_CAPABILITY_PREFIXES = {
    "commit": ["git commit"],
    "push": ["git push"],
    "force-push": ["git push --force", "git push -f"],
    "rebase": ["git rebase"],
    "publish": ["gh pr create", "gh pr merge", "gh pr close", "gh release",
                "gh api --method POST", "gh api -X POST"],
    "reshape-lifecycle": ["git worktree", "git reset", "git checkout",
                          "git switch", "git branch", "git update-ref",
                          "git remote"],
    # "edit" and "shell" are tool-roster questions, not command ones: `run`
    # spells them with --disallowedTools, which is the only place the
    # vendor's own tool names belong.
    "edit": [],
    "shell": [],
}


def split_commands(raw: str) -> list[str]:
    """AGENT_COMMANDS: comma-separated neutral command prefixes."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def load_profile(raw: str | None) -> dict | None:
    """AGENT_PROFILE: the resolved execution profile for this launch, or
    None. Unparseable is None too — a malformed value must not be read as
    an empty deny list, and core refuses the launch before it writes one."""
    if not (raw or "").strip():
        return None
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def profile_prefixes(profile: dict | None) -> list[str]:
    """The named command grants, as the prefixes a rule is made of. The
    grant's name is its identity on the board; the rule only ever needs
    the command line."""
    if not profile:
        return []
    out = []
    for grant in profile.get("commands") or []:
        if isinstance(grant, dict) and str(grant.get("prefix") or "").strip():
            out.append(str(grant["prefix"]).strip())
    return out


# The intents that may report progress, when the board hands one a
# channel (AGENT_PROGRESS: the absolute path of core/bench-progress).
PROGRESS_MODES = {"work", "act-pr", "review"}


def progress_rules(mode: str, shim: str | None) -> list[str]:
    """The progress channel as an allow-rule: that one executable, by
    absolute path, and nothing composed from it. Absent unless the board
    set AGENT_PROGRESS, so a launch without a channel renders exactly the
    document it always did."""
    shim = (shim or "").strip()
    if mode not in PROGRESS_MODES or not shim.startswith("/"):
        return []
    return [f"Bash({shim})", f"Bash({shim}:*)"]


def allow_rules(mode: str, commands: list[str],
                profile: dict | None = None) -> list[str]:
    """Bash() allow-rules for one intent: the exact prefix and any longer
    command starting with it.

    With a profile, its named grants replace the AGENT_COMMANDS list — the
    same project prefixes, resolved and validated by name. The built-in
    git/gh prefixes per intent are the adapter's own knowledge and are not
    a profile's to change.

    A profile's grants are rendered for **every** intent, `review`
    included. AGENT_COMMANDS is not: the read-only intent never had the
    project's commands and must not gain them by the back door. The
    difference matters because a review may be granted the one thing
    bench adds itself — the shim that asks for a named check — and
    verifying is not committing: a review that can ask bench to run the
    tests still has no commit and no push rule anywhere in this document.

    `refine` gets nothing, profile or not: it has no shell to run a rule
    against, and a rule nothing can use is still a rule a reader has to
    reason about.
    """
    if mode == "refine":
        return []
    prefixes = list(MODE_PREFIXES.get(mode, []))
    if profile:
        prefixes += [c for c in profile_prefixes(profile) if c not in prefixes]
    elif mode in MODES_WITH_PROJECT_COMMANDS:
        prefixes += [c for c in commands if c not in prefixes]
    rules = []
    for prefix in prefixes:
        rules += [f"Bash({prefix})", f"Bash({prefix}:*)"]
    return rules


def deny_rules(mode: str, profile: dict | None = None) -> list[str]:
    """Bash() deny-rules for one intent — deny beats allow.

    The mode's own denies first, then whatever the profile names. Without
    a profile this is exactly the list it always was, which is why a board
    that configured nothing sees no change at all.
    """
    prefixes = list(MODE_DENY_PREFIXES.get(mode, []))
    for word in (profile or {}).get("deny") or []:
        for prefix in DENY_CAPABILITY_PREFIXES.get(str(word), []):
            if prefix not in prefixes:
                prefixes.append(prefix)
    rules = []
    for prefix in prefixes:
        rules += [f"Bash({prefix})", f"Bash({prefix}:*)"]
    return rules


# ── approved read-only inputs, as exact file reads (task 126) ───────────
#
# AGENT_CONTEXT (the files bench pinned for this run) and AGENT_GUIDANCE
# (the workflow briefs core's prompt guidance names outside the checkout)
# are both lists of {"name", "path", "sha256", "access": "read-only"}.
# Each becomes one `Read(//<absolute path>)` allow-rule — `//` is
# filesystem-root absolute in Claude Code's rule syntax; a single leading
# slash would be relative to the settings source — plus an `Edit(//…)`
# deny-rule, so a read grant can never be read as licence to change the
# document (Edit rules cover every built-in file-writing tool).
#
# Every entry is validated before a rule is made, and a bad one refuses
# the whole launch rather than being dropped: the prompt tells the run it
# may read these files, so a silently missing grant is a silently weakened
# requirement. Refused: a malformed entry, a path that is not absolute,
# not canonical (a symlink anywhere in it, `.`/`..`, doubled slashes), or
# carries any character outside a conservative set — which excludes every
# gitignore-style pattern character (`* ? [ ] ! \`), the rule delimiters
# `( )`, whitespace and control characters, so no path can become a
# wildcard or a directory grant — a path that is not a regular file, and
# content whose SHA-256 is not the one bench approved (drift fails closed).
#
# Neither variable present renders exactly the document it always did.

CONTEXT_EXIT = 65
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._@+=,:~/-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReadRefused(ValueError):
    """An approved input this adapter cannot grant exactly."""


def _parse_inputs(raw: str | None, variable: str) -> list:
    if not (raw or "").strip():
        return []
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        raise ReadRefused(f"{variable} is not JSON, so the approved inputs it "
                          f"names cannot be granted")
    if not isinstance(found, list):
        raise ReadRefused(f"{variable} is not a list of approved inputs")
    return found


def _validated(entry, variable: str) -> dict:
    if not isinstance(entry, dict):
        raise ReadRefused(f"{variable} holds an entry that is not an object")
    name = str(entry.get("name") or "").strip()
    path = entry.get("path")
    sha = entry.get("sha256")
    label = f"{variable} entry '{name or '?'}'"
    if not name:
        raise ReadRefused(f"{variable} holds an entry with no name")
    if entry.get("access") != "read-only":
        raise ReadRefused(f"{label} is not marked read-only access")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ReadRefused(f"{label} has no absolute path")
    if not _SAFE_PATH.fullmatch(path):
        raise ReadRefused(f"{label} has a path with a character this adapter "
                          f"will not put in a permission rule (wildcard, "
                          f"bracket, parenthesis, whitespace or control "
                          f"character)")
    parts = path.split("/")[1:]
    if "//" in path or path.endswith("/") or any(p in ("", ".", "..")
                                                 for p in parts):
        raise ReadRefused(f"{label} has a path that is not canonical")
    if os.path.realpath(path) != path:
        raise ReadRefused(f"{label} resolves somewhere else through a link; "
                          f"only the canonical file path is granted")
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise ReadRefused(f"{label} is not there: {exc.strerror or exc}")
    if not stat.S_ISREG(st.st_mode):
        raise ReadRefused(f"{label} is not a regular file — a directory or "
                          f"special file is never granted")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise ReadRefused(f"{label} carries no SHA-256 to check it against")
    try:
        with open(path, "rb") as handle:
            got = hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        raise ReadRefused(f"{label} could not be read: {exc.strerror or exc}")
    if got != sha:
        raise ReadRefused(f"{label} no longer holds the content bench approved "
                          f"(sha256 {got[:12]}, approved {sha[:12]}); refusing "
                          f"rather than granting a changed file")
    return {"name": name, "path": path, "sha256": sha,
            "source": entry.get("source"), "kind": variable}


def approved_reads(context: str | None, guidance: str | None) -> list[dict]:
    """The validated, de-duplicated exact reads for one launch. Raises
    ReadRefused rather than returning a shorter list."""
    out: list[dict] = []
    seen: dict[str, str] = {}
    for variable, raw in (("AGENT_CONTEXT", context),
                          ("AGENT_GUIDANCE", guidance)):
        for entry in _parse_inputs(raw, variable):
            item = _validated(entry, variable)
            if item["path"] in seen:
                if seen[item["path"]] != item["sha256"]:
                    raise ReadRefused(f"{item['path']} is approved twice with "
                                      f"two different hashes")
                continue
            seen[item["path"]] = item["sha256"]
            out.append(item)
    return out


def read_rule(path: str) -> str:
    return f"Read(/{path})"


def edit_deny_rule(path: str) -> str:
    return f"Edit(/{path})"


def settings(mode: str, commands: list[str],
             profile: dict | None = None, progress: str | None = None,
             reads: list[dict] | None = None) -> dict:
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
    rules = allow_rules(mode, commands, profile) + progress_rules(mode, progress)
    if mode in INTENTS:
        rules += [read_rule(r["path"]) for r in reads or []]
    if rules:
        perms["allow"] = rules
    denies = deny_rules(mode, profile)
    if mode in INTENTS:
        denies += [edit_deny_rule(r["path"]) for r in reads or []]
    if denies:
        perms["deny"] = denies
    if perms:
        out["permissions"] = perms
    return out


def launch_flags(mode: str) -> list[str]:
    """The permission flags `run` passes beside --settings for one intent —
    the stance, and the vendor tools the run is launched without."""
    withheld = DISALLOWED_TOOLS.get(mode)
    if withheld:
        return ["--permission-mode", "default", "--disallowedTools", *withheld]
    return ["--permission-mode", "acceptEdits"]


if __name__ == "__main__":
    if sys.argv[1:2] == ["--flags"]:
        # one flag per line, for `run` to read into an array
        print("\n".join(launch_flags(sys.argv[2] if len(sys.argv) > 2 else "")))
        sys.exit(0)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    commands = split_commands(os.environ.get("AGENT_COMMANDS", ""))
    try:
        reads = approved_reads(os.environ.get("AGENT_CONTEXT"),
                               os.environ.get("AGENT_GUIDANCE"))
    except ReadRefused as exc:
        print(f"claude adapter: refusing the launch — {exc}. An approved "
              f"input that cannot be granted exactly is not granted more "
              f"broadly, and is not silently dropped.", file=sys.stderr)
        sys.exit(CONTEXT_EXIT)
    print(json.dumps(settings(mode, commands,
                              load_profile(os.environ.get("AGENT_PROFILE")),
                              os.environ.get("AGENT_PROGRESS"), reads)))
