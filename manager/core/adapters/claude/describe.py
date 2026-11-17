"""What this adapter would hand Claude Code for one launch — as JSON, in
the normalised shape core reads.

    python3 describe.py

    AGENT_MODE / AGENT_PROMPT / AGENT_COMMANDS / AGENT_PROFILE /
    AGENT_CONTEXT / AGENT_MODEL / AGENT_CWD in, exactly as a launch sets
    them; one JSON object on stdout.

This exists so bench's readiness doctor can check an adapter's
*conformance* — that every named grant became a rule, that the arguments
survived, that `review` was given no way to write, that the working
directory is the one bench named — while core continues to know nothing
about any vendor's rule syntax. Translating is this adapter's job, and so
is answering for the translation. `capabilities.json` names the command
that runs this, so core launches what the adapter declares rather than
guessing at an interpreter.

It launches nothing. No vendor binary, no model, no file written: what
comes back is the same document `run` would pass with `--settings`, plus
the flags that would go beside it. Reading it is free, which is what lets
the board's inspector open without asking for anything.

The argument list is built here *and* in `run`, which is a drift risk and
a deliberate one: core cannot read bash. The pair is held together by
bench's own conformance fixtures, which assert that what this prints is
what `run` actually passes to the binary it launches. Change one and the
other has to follow, in review.

Normalised keys core relies on (anything else is this adapter's own, and
is shown rather than interpreted):

    adapter   this adapter's name
    intent    the launch intent described
    argv      the whole argument list the vendor binary would receive
    cwd       the directory the run would start in
    allow     permission rules that permit, as rule text
    deny      permission rules that forbid, as rule text
    readOnly  whether the vendor's edit tools are withheld
    noShell   whether the vendor's shell tool is withheld, so no command
              of any kind can be launched
    context   the pinned inputs as delivered — paths and hashes, never
              content. `contextContents` is deliberately never a key
    reads     the approved inputs (AGENT_CONTEXT) and workflow briefs
              (AGENT_GUIDANCE) as the exact read grants generated for
              them: name, path, kind, sha256 and the rule text. Generated
              permission metadata — not evidence the vendor honoured it
    refused   why `run` would refuse the launch before the vendor starts
              (an approved input it cannot grant exactly), or null
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_settings  # noqa: E402


def described() -> dict:
    mode = os.environ.get("AGENT_MODE", "work")
    prompt = os.environ.get("AGENT_PROMPT", "")
    commands = hook_settings.split_commands(os.environ.get("AGENT_COMMANDS", ""))
    profile = hook_settings.load_profile(os.environ.get("AGENT_PROFILE"))
    refused = None
    try:
        reads = hook_settings.approved_reads(os.environ.get("AGENT_CONTEXT"),
                                             os.environ.get("AGENT_GUIDANCE"))
    except hook_settings.ReadRefused as exc:
        reads, refused = [], str(exc)
    settings = hook_settings.settings(mode, commands, profile,
                                      os.environ.get("AGENT_PROGRESS"), reads)
    model = os.environ.get("AGENT_MODEL", "").strip()

    argv = ["-p", prompt, "--settings", json.dumps(settings)]
    if model:
        argv += ["--model", model]
    argv += hook_settings.launch_flags(mode)
    withheld = hook_settings.DISALLOWED_TOOLS.get(mode) or []
    read_only = all(t in withheld for t in hook_settings.READ_ONLY_TOOLS)

    try:
        context = json.loads(os.environ.get("AGENT_CONTEXT") or "[]")
    except (TypeError, ValueError):
        context = []

    permissions = settings.get("permissions") or {}
    return {
        "adapter": "claude",
        "intent": mode,
        "argv": argv,
        "cwd": os.environ.get("AGENT_CWD", ""),
        "allow": permissions.get("allow") or [],
        "deny": permissions.get("deny") or [],
        "readOnly": read_only,
        # whether the run is launched with no shell at all
        "noShell": "Bash" in withheld,
        "context": context if isinstance(context, list) else [],
        # the approved inputs as exact read grants: generated permission
        # text, which is not the same fact as a vendor having honoured it
        "reads": [{"name": r["name"], "path": r["path"], "kind": r["kind"],
                   "sha256": r["sha256"],
                   "rule": hook_settings.read_rule(r["path"]),
                   "denyEdit": hook_settings.edit_deny_rule(r["path"])}
                  for r in reads],
        # why `run` would refuse this launch before the vendor starts, or
        # None; `argv` is then what it would have been without the reads
        "refused": refused,
        # this adapter's own half, shown rather than interpreted
        "settings": settings,
        "hooks": sorted(settings.get("hooks") or {}),
    }


if __name__ == "__main__":
    print(json.dumps(described()))
