"""One resolved execution profile per launch intent, and the approved
context it pins.

`plans` answers *which checkout* a run works in. This module answers the
other half of the same question: **what that run may actually do there,
and which files it is guaranteed to have read.** The two compose and do
not overlap — a plan names branches, a profile names capabilities and
inputs — and both are resolved, validated and written down before a
billable model is invoked.

## Why it exists

Before this, the answer was implicit and scattered. `BOARD_AGENT_COMMANDS`
was compiled into permission rules for `work` and `act-pr` only, and
`review` got a hardcoded grant set inside each adapter. A prompt could
*point* at a workflow brief outside the checkout, but nothing checked the
file was there, nothing recorded which revision of it the run saw, and
nothing stopped the content changing under a run already in flight. So
three things had no answer at all:

- what, exactly, is this run allowed to run — by name, not by a
  configured/unconfigured boolean,
- which files is it guaranteed to have, and at which revision,
- and what did this *particular* run ride, as against what the settings
  say now.

A resolved profile answers all three, once, and everything downstream
reads the record rather than parsing the settings again.

## The three separations this module keeps

The reason the old arrangement could not be extended is that it conflated
four different things. They are kept apart here, and the data says which
is which:

1. **UI registration** — a `manager/local/commands/` executable is a `$`
   chip on a card. Appearing there grants a headless agent nothing:
   resolved profiles list those under `uiCommands`, every one marked
   `agentCapability: false`.
2. **Agent capability** — a named command grant in the profile. This is
   the only thing that reaches an adapter's permission rules.
3. **Prompt instruction** — what the run is *told* (the context block,
   the workflow brief). Prose persuades; it does not enforce, and
   `guarantees.promptOnly` says so out loud.
4. **Gate requirement** — what a later verification or merge gate will
   insist on (`verify`). Declared here, enforced by a later member, and
   marked `pending` until one exists.

## Where a profile comes from

Two structured inputs, in this order, and nothing else:

1. **A profile document** — `manager/local/profiles.json`, or wherever
   `BOARD_AGENT_PROFILES` points. Machine-local by construction: it names
   roots on *this* disk, so it lives with the tool and never in the
   application's history. `core/profiles.example.json` documents the whole
   schema.
2. **The legacy settings** — `BOARD_AGENT_COMMANDS` plus each adapter's
   own built-in git/gh grants. A board with no document resolves to
   exactly what it resolved to before this module existed, marked
   `source.kind: "legacy"`. That is the whole of "no new metadata keeps
   established behaviour", and it is why installing an update changes
   nothing until somebody writes a document.

A document never *widens* the legacy grants by accident: every grant it
adds is named, validated and listed, and `deny` is rendered by the
adapters as rules that beat allows.

## Fails closed, and says who enforces what

Two refusals matter more than the rest:

- **A required input that is missing, unreadable, a directory, or
  reachable only through a link out of its root refuses the launch** —
  by input name, with a remedy, before a model is invoked. A directory
  grant is not a read-only guarantee and is refused as one.
- **A profile that asks for a guarantee the installed backend cannot
  enforce refuses**, rather than quietly degrading to a sentence in the
  prompt. `requires` is checked against the capabilities the adapter
  declares (`capabilities.json` beside its `run`) and the two bench
  enforces itself.

And where a capability *is* available, the profile says how strongly:
`enforced`, `prefix-allowlist` (a prefix match, which is not a sandbox),
`prompt-only`, or `pending`. Nothing here claims a shipped adapter
confines a process; `guarantees` is the single place that answers it.

## What is recorded, and what that buys

`manifest()` resolves everything and changes nothing. The launch path
calls it with `pin=True`, which copies every approved input into a
read-only bundle under `local/state/context/`, and `record()` writes the
whole manifest to `local/state/manifests/<agent id>.json`. From then on:

- the run's inputs cannot change under it — the bundle is the declared
  boundary, and a later edit to a source file changes the *next* run,
- the frozen policy revision is comparable with the current one, so the
  board can show "this run rode that, the settings now say this"
  (`freshness`),
- and the UI reads the same manifest the launch rode, rather than a
  second settings parser that could disagree with it.

Machine-local, for plans' reason: it holds this machine's paths. Nothing
here is committed, pushed, or carried into a PR body.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import config

# The schema a document declares itself in, and the resolver that reads
# it. Both travel in every manifest: a profile recorded by one revision of
# bench must be legible to the next, and `freshness` is what notices when
# the resolver itself has moved under an existing run.
PROFILE_VERSION = 1
RUNNER_VERSION = 1

# The launch intents core signals. Every one of them resolves to a profile
# of its own — `review` included, which is the gap this module closes.
INTENTS = ("work", "act-pr", "review")

# ── identity types ─────────────────────────────────────────────────────
#
# Four identities, named once here because later members (verification,
# the reviewer, the doctor, the merge gate) have to refer to the same
# things bench refers to. They are opaque strings with a readable prefix
# and a short hash: comparable, loggable, and safe to put in a diagnostic
# because none of them carries content.

ID_PREFIXES = {
    "policy": "profile",        # which policy a run rode
    "context": "ctx",           # which input, at which content
    "request": "req",           # which launch asked
    "check": "check",           # which named check, at which definition
    "evidence": "ev",           # what a verification actually found
}


def _digest(*parts: str) -> str:
    """One stable hash over an ordered tuple of strings. `\\x00` separates,
    so no concatenation of two parts can be mistaken for another pair."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def policy_id(intent: str, revision: str) -> str:
    """Which policy a run rode: the intent and the revision of the
    resolved profile. Two runs with the same policy id were granted
    exactly the same thing."""
    return f"{ID_PREFIXES['policy']}:{intent}@{revision[:12]}"


def context_id(name: str, sha256: str) -> str:
    """Which input, at which content. The hash is the content's, so an
    edited file is a different identity rather than the same one with new
    bytes — which is the whole guarantee the pinning exists for."""
    return f"{ID_PREFIXES['context']}:{name}@{sha256[:12]}"


def request_id(intent: str, filename: str, when: float | None = None) -> str:
    """Which launch asked. One per resolution, so a refused preflight and
    the launch that follows it are distinguishable in a log."""
    stamp = f"{when if when is not None else time.time():.3f}"
    return (f"{ID_PREFIXES['request']}:{intent}:{filename[:-3] if filename.endswith('.md') else filename}"
            f"@{_digest(intent, filename, stamp)[:12]}")


def check_id(name: str, revision: str) -> str:
    """Which named check, at which definition. The revision is a digest of
    the check's whole resolved shape — executable, argument list, execution
    root, scopes, preparation — so an operator who edits a check gets a new
    identity rather than old evidence that claims to be about the new
    definition."""
    return f"{ID_PREFIXES['check']}:{name}@{revision[:12]}"


# What a verification records against a request, and what it is for.
# `verify.py` produces the first three; a merge gate is still a later
# member and `verified-outputs` says so.
EVIDENCE_KINDS = {
    "command-run": "a named command grant was actually run, and how it exited",
    "context-read": "a pinned input was actually read by the run",
    "verification": "a verification requirement was checked",
    "gate": "a merge gate reached a verdict",
}


def evidence_id(kind: str, request: str, detail: str = "") -> str:
    """The identity a verification stamps its findings with. Defined here
    so every consumer — the runner, the UI, a later gate — computes it the
    same way."""
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind '{kind}' — one of "
                         f"{', '.join(sorted(EVIDENCE_KINDS))}")
    return f"{ID_PREFIXES['evidence']}:{kind}@{_digest(kind, request, detail)[:12]}"


# ── capabilities, and who actually enforces them ───────────────────────
#
# The honest part. A capability is not a boolean: it matters enormously
# whether something is confined by a kernel, narrowed by a prefix match,
# or merely asked for in a prompt. Four words, used everywhere:
#
#   enforced          the named party really does prevent it
#   prefix-allowlist  a prefix match over the command line. It narrows
#                     what a run may launch; it is NOT a sandbox, and
#                     nothing here describes it as one
#   prompt-only       the run is told, and nothing stops it
#   pending           defined, not implemented — a `requires` naming it
#                     refuses rather than resolving to prompt-only

ENFORCEMENT = ("enforced", "prefix-allowlist", "prompt-only", "pending")

# What bench enforces itself, whatever adapter is installed — and each
# `why` is written to claim the narrow thing that is actually true. A copy
# made before the launch really does stop the *source* changing under the
# run; it is not a boundary on what the run may then do to its own copy,
# and saying otherwise here would make the whole display a lie.
BENCH_CAPABILITIES = {
    "pinned-context": ("enforced",
                       "bench copies each approved input into a bundle and "
                       "hashes it before the launch, so an edit to the source "
                       "cannot change what this run was given. The copy is "
                       "written read-only; a run with a shell could still "
                       "alter its own copy, which is why the hash on record "
                       "is of what was pinned"),
    "working-directory": ("enforced",
                          "the launch is started in the directory bench names, "
                          "as the child process's cwd. That is where it begins, "
                          "not a boundary it is held inside"),
    "context-boundary": ("enforced",
                         "an input reachable only through a link out of its "
                         "root, or naming a directory, is refused rather "
                         "than pinned — so an approved document cannot stand "
                         "in for an unapproved one"),
    "named-verification": ("enforced",
                           "a run asks for a check by name. Bench builds the "
                           "argument list itself from the operator's document "
                           "and runs it with no shell, so a name is the whole "
                           "of what a run can choose — not a command line"),
    "prepared-inputs": ("enforced",
                        "bench runs each declared preparation step before the "
                        "check that needs it, in the same isolated root, and "
                        "records how it exited. A required step that fails "
                        "stops the check rather than letting it fail for the "
                        "wrong reason"),
    "verification-evidence": ("enforced",
                              "the outcome on record is the runner's own exit "
                              "status, written to disk before the asking run "
                              "is told anything. A model cannot attest that a "
                              "check passed, and an agent exiting zero never "
                              "makes one passing"),
    "isolated-environment": ("enforced",
                             "a check's environment is built by bench rather "
                             "than inherited: publication credentials are "
                             "removed by name, and HOME, TMPDIR and the cache "
                             "variables point into a directory made for this "
                             "verification. That is where its writes are "
                             "*directed*, not a boundary they are held inside "
                             "— filesystem-sandbox is the capability that "
                             "would be, and nothing enforces it"),
}

# What an adapter may declare in `capabilities.json` beside its `run`.
# Core knows the vocabulary; each adapter answers for itself.
ADAPTER_CAPABILITIES = {
    "command-allowlist": "named command grants become the vendor's allow-rules",
    "command-denylist": "denied capabilities become deny-rules that beat allows",
    "read-only-tools": "the vendor's edit tools are withheld from the run",
    "model-selection": "the configured model reaches the vendor untranslated",
}

# Capabilities this card names so later members have one word for them,
# and which nothing yet implements. A profile that *requires* one is
# refused — that refusal is the feature. Resolving it to a sentence in a
# prompt would be the failure.
PENDING_CAPABILITIES = {
    "filesystem-sandbox": ("no shipped adapter or verification backend can "
                           "confine a run's reads to a named set of files; "
                           "the pinned bundle is a guaranteed *input*, not a "
                           "boundary on reading, and an isolated execution "
                           "root is a directory rather than a jail"),
    "verified-outputs": ("verifications run and their evidence is recorded; "
                         "the merge gate that *insists* on a named check "
                         "having passed is a later member"),
    "network-policy": ("neither an adapter nor bench exposes a network "
                       "boundary a profile could set, so a preparation step "
                       "that needs the network has whatever network this "
                       "machine has"),
}


def adapter_identity() -> dict:
    """Which adapter this board launches through, and what it says it can
    enforce — read from `capabilities.json` beside its `run`.

    An adapter without the file is not an error and not a liar: it
    declares nothing, so nothing may *require* anything of it, and a
    legacy profile (which requires nothing) runs through it exactly as it
    always did. That is what keeps a project's own
    `local/adapters/<name>/` working across this change.
    """
    directory = config.adapter_dir()
    found: dict = {
        "name": config.ADAPTER,
        "path": str(directory) if directory else None,
        "identity": _tool_relative(directory) if directory else None,
        "version": None,
        "capabilities": {},
        "unsupported": {},
        "declared": False,
    }
    if directory is None:
        found["note"] = (f"agent adapter '{config.ADAPTER}' was not found, so "
                         f"nothing could be read about what it enforces")
        return found
    path = directory / "capabilities.json"
    if not path.is_file():
        found["note"] = (f"{found['identity']} declares no capabilities.json, so "
                         f"it is taken to promise nothing — a profile may still "
                         f"run through it, but may not require guarantees of it")
        return found
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        found["note"] = f"{found['identity']}/capabilities.json could not be read: {exc}"
        return found
    if not isinstance(data, dict):
        found["note"] = f"{found['identity']}/capabilities.json is not an object"
        return found
    declared = data.get("capabilities")
    found["declared"] = True
    found["version"] = str(data.get("version") or "")
    found["capabilities"] = {
        str(k): str(v) for k, v in (declared or {}).items()
        if str(v) in ENFORCEMENT}
    found["unsupported"] = {str(k): str(v)
                            for k, v in (data.get("unsupported") or {}).items()}
    return found


def capability_table(adapter: dict) -> dict:
    """Every capability this board can answer for, and how strongly, with
    who answers for it. One table, asked by `requires` validation and
    rendered by the UI — so the page cannot show a guarantee the launch
    would not have honoured."""
    table: dict = {}
    for name, (strength, why) in BENCH_CAPABILITIES.items():
        table[name] = {"enforcement": strength, "by": "bench", "why": why}
    for name, why in ADAPTER_CAPABILITIES.items():
        strength = adapter["capabilities"].get(name)
        table[name] = {
            "enforcement": strength or "pending",
            "by": f"the {adapter['name']} adapter",
            "why": why if strength else
                   (adapter["unsupported"].get(name)
                    or f"the {adapter['name']} adapter does not declare it"),
        }
    for name, why in PENDING_CAPABILITIES.items():
        table[name] = {"enforcement": "pending", "by": "nothing yet", "why": why}
    return table


# ── the roots a profile may name ───────────────────────────────────────
#
# Four, and each is one of the roots config already resolves. A profile
# names them by word rather than by path: the document is machine-local
# but it is still read on a machine whose paths are its own, and a
# relocated install must not silently point an approved input somewhere
# else.

ROOT_WORDS = ("worktree", "app", "work", "tool")


def roots(worktree: Path | str | None) -> dict:
    """The approved roots for one launch, resolved. `worktree` is None for
    a read-only intent that runs in the application checkout — then the
    word is simply unavailable and naming it is a blocker."""
    found = {
        "app": str(config.app_repo()),
        "work": str(config.work_root()),
        "tool": str(config.TM_ROOT),
    }
    if worktree is not None:
        found["worktree"] = str(worktree)
    return found


def _tool_relative(path: Path | None) -> str | None:
    """A path named relative to the tool install, which is the only way to
    name one that means the same thing on the next machine. Falls back to
    the absolute path when it is genuinely elsewhere."""
    if path is None:
        return None
    try:
        return path.resolve().relative_to(config.TM_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


# ── reading the document ───────────────────────────────────────────────


def document_path() -> Path | None:
    """Where the profile document is, when there is one.

    `BOARD_AGENT_PROFILES` wins and is used as named — an absolute path as
    given, a relative one against the tool install, never against a
    working directory bench cannot see. Unset falls back to
    `manager/local/profiles.json`, which is where a project's own belongs.
    """
    configured = (config.AGENT_PROFILES or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = config.TM_ROOT / candidate
        return candidate
    candidate = config.LOCAL / "profiles.json"
    return candidate if candidate.is_file() else None


def document() -> dict:
    """The profile document, read fresh, plus what to say about it.

    Three answers, and each is a real one. `{"kind": "legacy"}` is a board
    with no document, which resolves exactly as it did before profiles
    existed. `{"kind": "file"}` is a document that parsed. `{"kind":
    "broken"}` is one that did not — and that is a **blocker** on every
    intent rather than a silent fall back to the legacy grants, because an
    operator who wrote a policy and mistyped it must not be given the
    permissions they were trying to change.
    """
    path = document_path()
    if path is None:
        return {"kind": "legacy", "path": None, "identity": None,
                "revision": _digest("legacy", config.AGENT_COMMANDS),
                "profiles": {}, "problem": None,
                "describes": "BOARD_AGENT_COMMANDS and the adapter's built-in grants"}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"kind": "broken", "path": str(path),
                "identity": _tool_relative(path), "revision": "",
                "profiles": {}, "describes": None,
                "problem": (f"the profile document {_tool_relative(path)} could not "
                            f"be read ({exc}) — fix it or unset "
                            f"BOARD_AGENT_PROFILES; bench will not fall back to "
                            f"the legacy grants under a policy you meant to change")}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"kind": "broken", "path": str(path),
                "identity": _tool_relative(path),
                "revision": _digest(raw), "profiles": {}, "describes": None,
                "problem": (f"the profile document {_tool_relative(path)} is not "
                            f"valid JSON (line {exc.lineno}, column {exc.colno}: "
                            f"{exc.msg}) — nothing was resolved from it")}
    if not isinstance(data, dict):
        data = {}
    version = data.get("v")
    out = {"kind": "file", "path": str(path), "identity": _tool_relative(path),
           "revision": _digest(raw), "profiles": data.get("profiles") or {},
           "describes": _tool_relative(path), "problem": None,
           "documentVersion": version,
           "requires": data.get("requires") or [],
           "context": data.get("context") or [],
           "commands": data.get("commands") or [],
           # The two *definition* lists. Top-level and nowhere else: a
           # check reviewed once is the same check whichever intent asks
           # for it, and an intent's own `verify`/`prepare` are names.
           "checks": data.get("checks") or [],
           "prepare": data.get("prepare") or [],
           "verify": data.get("verify") or []}
    if version != PROFILE_VERSION:
        out["problem"] = (
            f"the profile document {out['identity']} declares "
            f"\"v\": {version!r}, and this bench reads v{PROFILE_VERSION} — "
            f"nothing was resolved from it. An unreadable policy refuses; it "
            f"does not quietly become the old one")
        out["kind"] = "broken"
    if not isinstance(out["profiles"], dict):
        out["profiles"] = {}
    return out


# ── validating one named command grant ─────────────────────────────────
#
# These values end up in an argument list and in a permission rule, so
# they are refused by the parser rather than by the process that would
# have run them — the same law `taskfiles.ref_problem` applies to a branch
# name.

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# Anything that makes one command line into two, or makes its text depend
# on the shell rather than on the grant. A prefix allow-rule matches text;
# a substitution is text the rule cannot see.
SHELL_BREAKERS = (";", "&", "|", "`", "$", ">", "<", "(", ")", "\n", "\r",
                  "{", "}", "*", "?", "!", "~", "#", "'", '"', "\\", "[", "]")


def _grant_problem(raw: dict, available: dict) -> str | None:
    """Why this grant cannot be used, or None. One sentence, naming the
    grant, because it is shown beside it."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return (f"a command grant is named {name!r} — a grant's name is its "
                f"identity in the manifest and the UI, so it must match "
                f"[a-z0-9][a-z0-9._-]*")
    run = str(raw.get("run") or "").strip()
    if not run:
        return f"the command grant '{name}' has no \"run\" — there is nothing to allow"
    for breaker in SHELL_BREAKERS:
        if breaker in run:
            return (f"the command grant '{name}' contains {breaker!r} in its "
                    f"\"run\" — a grant is one executable and fixed arguments, "
                    f"not a shell line: compound commands, redirections, globs "
                    f"and substitutions are refused because a prefix rule "
                    f"cannot see what they expand to")
    try:
        parts = shlex.split(run)
    except ValueError as exc:
        return f"the command grant '{name}' does not parse as a command line ({exc})"
    if not parts:
        return f"the command grant '{name}' has an empty \"run\""
    if shlex.join(parts) != run:
        return (f"the command grant '{name}' is written {run!r}, which "
                f"normalises to {shlex.join(parts)!r} — write it in the "
                f"normalised form so the rule and the grant read alike")
    executable = parts[0]
    if ".." in Path(executable).parts:
        return (f"the command grant '{name}' names the executable "
                f"{executable!r}, which climbs out of its own path with '..'")
    if "/" in executable and not executable.startswith("/"):
        return (f"the command grant '{name}' names the executable "
                f"{executable!r} — give a bare name resolved on PATH, or an "
                f"absolute path; a relative path depends on a working "
                f"directory the rule does not pin")
    root = str(raw.get("root") or "worktree")
    if root not in ROOT_WORDS:
        return (f"the command grant '{name}' names the execution root "
                f"{root!r} — one of {', '.join(ROOT_WORDS)}")
    if root not in available:
        return (f"the command grant '{name}' runs in the {root} root, which "
                f"this intent has none of — a read-only run has no worktree")
    return None


def _grant(raw: dict, *, source: str, available: dict) -> dict:
    """One validated grant, as the manifest carries it. The `prefix` is
    what an adapter renders; `enforcement` says what that rendering is
    actually worth."""
    parts = shlex.split(str(raw.get("run")))
    return {
        "name": str(raw.get("name")).strip(),
        "run": shlex.join(parts),
        "executable": parts[0],
        "argv": parts[1:],
        "prefix": shlex.join(parts),
        "root": str(raw.get("root") or "worktree"),
        "help": str(raw.get("help") or "").strip() or None,
        "source": source,
        "grant": "prefix-allowlist",
        "agentCapability": True,
    }


def _legacy_grants(intent: str, available: dict) -> tuple[list[dict], list[str]]:
    """`BOARD_AGENT_COMMANDS` as named grants, for a board with no
    document. The prefixes are the project's own half exactly as the
    adapters already read them — this names them, it does not change them.

    `review` gets none, which is not an oversight: the read-only intent
    never ran the project's checks, and giving it them here would be the
    silent broadening this module exists to avoid.
    """
    if intent == "review":
        return [], []
    grants, problems = [], []
    for index, prefix in enumerate(config.agent_commands()):
        raw = {"name": f"command-{index + 1}", "run": prefix, "root": "worktree"}
        problem = _grant_problem(raw, available)
        if problem:
            # A legacy prefix that is not a clean command line is reported
            # and dropped from the *named* list, and the adapter still
            # receives AGENT_COMMANDS verbatim — so nothing a board could
            # run yesterday stops running today because of a name.
            problems.append(f"{problem} (from BOARD_AGENT_COMMANDS)")
            continue
        grants.append(_grant(raw, source="BOARD_AGENT_COMMANDS",
                             available=available))
    return grants, problems


# ── named checks, and the preparation they need ────────────────────────
#
# A command grant says what the *run* may launch. A **check** says what
# *bench* will run on the run's behalf, and the difference is the whole
# point: a grant is a prefix a model composes a command line against, and
# a check is a name. The run asks for `unit-tests`; bench builds the
# argument list from the operator's document, runs it with no shell, and
# records how it exited. Nothing a model writes reaches an argv.
#
# Which is why a check is validated harder than a grant. It is resolved
# here — in the policy layer, beside the grants and the inputs, and ahead
# of any process — and `verify.py` only ever executes what this returned.

# Where a check may run. Deliberately *not* the four root words: a check
# never runs in the application checkout, because that is the live code
# and services the machine is actually using. `worktree` is the card's own
# checkout, which bench made; `source` is a detached checkout of one exact
# commit that bench makes for this verification and owns.
CHECK_ROOTS = ("worktree", "source")

# The longest a check may be allowed to run, whatever the document says.
# A per-check `timeout` narrows the board's setting and never widens it.
MAX_CHECK_TIMEOUT = 6 * 60 * 60

# A check verifies; it does not reshape the repository it verifies in. The
# execution root is a real checkout sharing its git metadata with the
# application, so these subcommands are refused where the document is
# read — an operator cannot declare a "check" that pushes, rewrites
# history, moves a branch or edits the git configuration every other
# worktree shares. (`git` itself is fine: `git diff --check`, `git
# ls-files` and the rest are exactly the sort of thing a check is.)
GIT_RESHAPING = {
    "push", "worktree", "reset", "checkout", "switch", "branch", "remote",
    "update-ref", "config", "clean", "rebase", "filter-branch", "gc",
    "prune", "submodule", "clone", "init", "am", "cherry-pick", "revert",
    "merge", "commit", "tag", "notes", "replace", "stash", "restore",
}


def _runnable_problem(name: str, what: str, run: str) -> str | None:
    """What bench refuses to *run itself*, over and above what it refuses
    to let a run launch. Two things, and both are about the fact that
    bench holds the argv here: a git subcommand that reshapes the
    repository, and an argument that names a file reading as a credential.
    """
    parts = shlex.split(run)
    if Path(parts[0]).name == "git":
        subcommand = next((p for p in parts[1:] if not p.startswith("-")), "")
        if subcommand in GIT_RESHAPING:
            return (f"the {what} '{name}' runs `git {subcommand}`, which "
                    f"reshapes the repository rather than reading it — the "
                    f"execution root shares its git metadata with every other "
                    f"worktree of the application, and bench will not run "
                    f"something that rewrites it in the name of verifying")
    for token in parts[1:]:
        if SECRET_NAMES.search(Path(token).name):
            return (f"the {what} '{name}' names {token!r}, whose basename reads "
                    f"as a credential — a check is not how a secret gets read, "
                    f"and bench refuses the argument rather than the file")
    return None


def _tokens_problem(name: str, what: str, tokens: list) -> str | None:
    """Why this argument list cannot be used. The tokens are an operator's,
    not a model's, and they never meet a shell — so a metacharacter in one
    is a literal argument rather than a hazard. What is refused is a token
    that is not a string, and one that climbs out of the root the check
    runs in."""
    for token in tokens:
        if not isinstance(token, str):
            return (f"{what} of the check '{name}' holds {token!r}, which is "
                    f"not a string — an argument list is strings, not "
                    f"structure")
        if ".." in Path(token).parts:
            return (f"{what} of the check '{name}' holds {token!r}, which "
                    f"climbs out of the root the check runs in with '..'")
    return None


def _check_problem(raw: dict, known_prepare: set[str]) -> str | None:
    """Why this check cannot be offered, or None. One sentence, naming the
    check, because it is shown beside it."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return (f"a check is named {name!r} — a check's name is what a run "
                f"asks for and what its evidence is filed under, so it must "
                f"match [a-z0-9][a-z0-9._-]*")
    # The executable and its fixed arguments are a command grant's problem
    # exactly, so they are asked the same question — including the shell
    # breakers, which matter more here: bench runs this itself.
    probe = {"name": name, "run": raw.get("run"), "root": "worktree"}
    problem = _grant_problem(probe, {"worktree": "/"})
    if problem:
        return problem.replace(f"command grant '{name}'", f"check '{name}'")
    problem = _runnable_problem(name, "check", str(raw.get("run")).strip())
    if problem:
        return problem
    root = str(raw.get("root") or "worktree")
    if root not in CHECK_ROOTS:
        return (f"the check '{name}' names the execution root {root!r} — one "
                f"of {', '.join(CHECK_ROOTS)}. A check never runs in the "
                f"application checkout: that is the live code and the live "
                f"services, and verifying in it is how a test suite takes a "
                f"working tree down with it")
    raw_scopes = raw.get("scopes")
    if raw_scopes is not None and not isinstance(raw_scopes, dict):
        return (f"the check '{name}' declares \"scopes\" that are not an "
                f"object — write {{\"<name>\": [\"<argument>\", …]}}, so a run "
                f"selects a scope by name and never by writing arguments")
    for key, tokens in (raw_scopes or {}).items():
        if not NAME_RE.match(str(key)):
            return (f"the check '{name}' declares the scope {key!r} — a scope "
                    f"is selected by name, so it must match "
                    f"[a-z0-9][a-z0-9._-]*")
        if not isinstance(tokens, list):
            return (f"the scope '{key}' of the check '{name}' is not a list of "
                    f"arguments")
        trouble = _tokens_problem(name, f"the scope '{key}'", tokens)
        if trouble:
            return trouble
        for token in tokens:
            if SECRET_NAMES.search(Path(str(token)).name):
                return (f"the scope '{key}' of the check '{name}' names "
                        f"{token!r}, whose basename reads as a credential")
    raw_prepare = raw.get("prepare")
    if raw_prepare is not None and not isinstance(raw_prepare, list):
        return (f"the check '{name}' declares \"prepare\" that is not a list "
                f"of preparation-step names")
    for step in raw_prepare or []:
        if str(step) not in known_prepare:
            return (f"the check '{name}' needs the preparation step "
                    f"{str(step)!r}, which the document does not declare — add "
                    f"it to the document's top-level \"prepare\" list, or drop "
                    f"the reference; bench will not run a step it cannot read")
    timeout = raw.get("timeout")
    if timeout is not None:
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            return f"the check '{name}' declares a \"timeout\" that is not a number"
        if value <= 0 or value > MAX_CHECK_TIMEOUT:
            return (f"the check '{name}' declares a timeout of {timeout} — give "
                    f"a number of seconds between 1 and {MAX_CHECK_TIMEOUT}")
    return None


def _step_problem(raw: dict) -> str | None:
    """The same question for one preparation step."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return (f"a preparation step is named {name!r} — names match "
                f"[a-z0-9][a-z0-9._-]*")
    probe = {"name": name, "run": raw.get("run"), "root": "worktree"}
    problem = _grant_problem(probe, {"worktree": "/"})
    if problem:
        return problem.replace(f"command grant '{name}'",
                               f"preparation step '{name}'")
    return _runnable_problem(name, "preparation step",
                             str(raw.get("run")).strip())


def _check_revision(name: str, run: str, root: str, scopes: dict,
                    prepare: list[str], timeout: float) -> str:
    return _digest(name, run, root,
                   json.dumps(scopes, sort_keys=True),
                   json.dumps(prepare), f"{timeout:.0f}")


def _step(raw: dict, *, source: str) -> dict:
    """One validated preparation step, as the manifest carries it."""
    parts = shlex.split(str(raw.get("run")))
    network = raw.get("network", False) is True
    return {
        "name": str(raw.get("name")).strip(),
        "run": shlex.join(parts),
        "executable": parts[0],
        "argv": parts[1:],
        "required": raw.get("required", True) is not False,
        "network": network,
        "networkEnforcement": "pending" if network else None,
        "help": str(raw.get("help") or "").strip() or None,
        "source": source,
        "status": "available",
        "enforcement": "enforced",
    }


def _check(raw: dict, steps: dict, *, source: str, scale: float) -> dict:
    """One validated check, as the manifest carries it and as `verify`
    executes it. `argv` and every scope's arguments are already split: the
    runner never parses a string, which is what keeps a command line from
    being something a model could compose."""
    parts = shlex.split(str(raw.get("run")))
    scopes = {str(k): [str(t) for t in v]
              for k, v in (raw.get("scopes") or {}).items()}
    prepare = [str(s) for s in (raw.get("prepare") or [])]
    root = str(raw.get("root") or "worktree")
    timeout = min(float(raw["timeout"]) if raw.get("timeout") is not None
                  else scale, scale)
    revision = _check_revision(str(raw.get("name")).strip(), shlex.join(parts),
                               root, scopes, prepare, timeout)
    return {
        "name": str(raw.get("name")).strip(),
        "run": shlex.join(parts),
        "executable": parts[0],
        "argv": parts[1:],
        "root": root,
        "scopes": scopes,
        "prepare": [steps[n] for n in prepare],
        "timeout": timeout,
        "required": raw.get("required", True) is not False,
        "help": str(raw.get("help") or "").strip() or None,
        "source": source,
        "status": "available",
        "revision": revision,
        "checkId": check_id(str(raw.get("name")).strip(), revision),
        "enforcement": "enforced",
    }


# The one grant bench adds of its own accord, and the executable behind
# it: the shim a run invokes to *ask* for a check. It takes a check name
# and an optional scope name, talks to this board over the loopback, and
# prints what the runner found. It is present in a resolved profile only
# where that intent has a check to ask for.
VERIFY_GRANT = "bench-verify"


def verify_shim() -> Path:
    """Where the asking shim lives. Beside the rest of core, so it travels
    with an update and is found by absolute path from any worktree."""
    return config.CORE / "bench-verify"


def document_steps(doc: dict) -> tuple[dict, list[str]]:
    """The document's preparation steps, by name, and why any of them
    cannot be used. A step that does not validate is a blocker rather than
    a step quietly left out: the check that names it would otherwise run
    unprepared and fail for the wrong reason."""
    steps: dict[str, dict] = {}
    trouble: list[str] = []
    if doc["kind"] != "file":
        return steps, trouble
    raw = doc.get("prepare")
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            trouble.append(f"a preparation step is not an object: {entry!r}")
            continue
        problem = _step_problem(entry)
        if problem:
            trouble.append(problem)
            continue
        name = str(entry["name"]).strip()
        if name in steps:
            trouble.append(f"the preparation step '{name}' is declared twice "
                           f"— a step's name is its identity")
            continue
        steps[name] = _step(entry, source=doc["identity"] or "the document")
    return steps, trouble


def document_checks(doc: dict, steps: dict) -> tuple[dict, list[str]]:
    """The document's named checks, by name, and why any of them cannot be
    offered. Resolved once per document rather than once per intent, so
    two intents asking for `unit-tests` are asking for the same thing."""
    checks: dict[str, dict] = {}
    trouble: list[str] = []
    if doc["kind"] != "file":
        return checks, trouble
    raw = doc.get("checks")
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            trouble.append(f"a check is not an object: {entry!r}")
            continue
        problem = _check_problem(entry, set(steps))
        if problem:
            trouble.append(problem)
            continue
        name = str(entry["name"]).strip()
        if name in checks:
            trouble.append(f"the check '{name}' is declared twice — a check's "
                           f"name is what a run asks for, and two commands "
                           f"cannot share one")
            continue
        checks[name] = _check(entry, steps,
                              source=doc["identity"] or "the document",
                              scale=config.VERIFY_TIMEOUT)
    return checks, trouble


# ── approved context ───────────────────────────────────────────────────
#
# A project-selected set of files, delivered as verifiable readable
# inputs. What makes it a *guarantee* is three things in order: the path
# is resolved inside a named root and refused if a link takes it out, the
# content is hashed, and a copy is pinned before the model is invoked. The
# approval covers reading those files and nothing adjacent to them.

# Basenames that are refused outright. Approving a document is not
# approving the credential that happens to sit beside it, and a profile
# that names one is far likelier to be a mistake than an intention.
SECRET_NAMES = re.compile(
    r"(^\.env($|\.)|(^|[.-])(secret|secrets|credential|credentials|token|"
    r"tokens)($|[.-])|^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|pfx|p12|jks)$|"
    r"^\.npmrc$|^\.netrc$|^\.pgpass$)", re.IGNORECASE)

# A cap, so a profile cannot turn a launch into a file copy of somebody's
# home directory. Generous for a brief or a design document, and the
# refusal names the number.
MAX_CONTEXT_BYTES = 512 * 1024


def _source_revision(path: Path) -> str:
    """What revision a source file is at, named the way its own repository
    would name it: the commit that last touched it when it is tracked,
    else its size and modification time. Not a substitute for the content
    hash — it is the *provenance* half, and a reader needs both."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path.parent), "log", "-1",
             "--format=%H", "--", path.name],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return f"git:{result.stdout.strip()[:12]}"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        stat = path.stat()
        return f"mtime:{int(stat.st_mtime)}:{stat.st_size}"
    except OSError:
        return "unknown"


def _selection_problem(raw: dict, available: dict) -> tuple[str | None, str | None]:
    """Why this selection cannot become an input, and what to do about it.
    Returns (reason, remedy); (None, None) means it resolves."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return (f"a context selection is named {name!r}",
                "give it a name matching [a-z0-9][a-z0-9._-]* — the name is "
                "the input's identity in the manifest and the prompt")
    spec = str(raw.get("path") or "").strip()
    if ":" not in spec:
        return (f"the context input '{name}' names the path {spec!r}, which "
                f"does not say which root it is in",
                f"write it as <root>:<relative path>, with the root one of "
                f"{', '.join(ROOT_WORDS)}")
    word, _, relative = spec.partition(":")
    if word not in ROOT_WORDS:
        return (f"the context input '{name}' names the root {word!r}",
                f"use one of {', '.join(ROOT_WORDS)}")
    if word not in available:
        return (f"the context input '{name}' is in the {word} root, which this "
                f"intent has none of",
                "a read-only run has no worktree — name a file under tool, "
                "work or app instead")
    relative = relative.strip()
    if not relative:
        return (f"the context input '{name}' names no file inside {word}",
                "name one file; a root on its own is a directory grant, and a "
                "directory grant is not a read-only guarantee")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return (f"the context input '{name}' names {relative!r}, which is "
                f"absolute or climbs out of {word} with '..'",
                f"give a path relative to the {word} root, with no '..'")
    if SECRET_NAMES.search(candidate.name):
        return (f"the context input '{name}' names {candidate.name}, which "
                f"reads as a credential",
                "approving a document never approves a secret — if this file "
                "genuinely is not one, rename it or copy the part an agent "
                "needs into a file that is not named like a key")
    return None, None


def _resolve_selection(raw: dict, available: dict) -> dict:
    """One selection → one input, read and hashed, nothing written.

    Everything that can refuse refuses here, before the pin and long
    before a model: the shape of the path, the link that would take it out
    of its root, the directory posing as a file, the file that is not
    there and the one that cannot be read. A selection marked
    `"required": false` that fails is a note instead of a blocker — it is
    the only difference the flag makes.
    """
    name = str(raw.get("name") or "").strip()
    spec = str(raw.get("path") or "").strip()
    required = raw.get("required", True) is not False
    word = spec.partition(":")[0]
    found: dict = {
        "name": name, "source": spec, "root": word if word in ROOT_WORDS else None,
        "required": required, "readable": False, "status": "unresolved",
        "sha256": None, "bytes": None, "revision": None, "contextId": None,
        "pinned": None, "path": None, "why": None, "remedy": None,
        "access": "read-only",
    }
    problem, remedy = _selection_problem(raw, available)
    if problem:
        found.update({"status": "refused", "why": problem, "remedy": remedy})
        return found

    root = Path(available[word])
    relative = spec.partition(":")[2].strip()
    path = root / relative
    found["path"] = str(path)

    escaped = config.escapes(path, root)
    if escaped is not None:
        found.update({
            "status": "refused",
            "why": (f"the context input '{name}' resolves to {escaped}, outside "
                    f"the {word} root it is named in — a link out of a root is "
                    f"how an approved document becomes an unapproved one"),
            "remedy": (f"name a file that is genuinely inside {root}, or move "
                       f"the file there; bench does not follow a link out of a "
                       f"root it was told to read within")})
        return found
    if path.is_dir():
        found.update({
            "status": "refused",
            "why": (f"the context input '{name}' names the directory "
                    f"{word}:{relative}"),
            "remedy": ("name the files themselves — a directory grant lets a "
                       "run read whatever is dropped into it later, which is "
                       "the opposite of a pinned input")})
        return found
    if not path.is_file():
        found.update({
            "status": "missing",
            "why": (f"the context input '{name}' is required and "
                    f"{word}:{relative} does not exist (looked in {path})"
                    if required else
                    f"the optional context input '{name}' is not there "
                    f"({word}:{relative})"),
            "remedy": (f"create {path}, point the selection at a file that "
                       f"exists, or mark the selection \"required\": false if "
                       f"the run can do without it")})
        return found
    try:
        data = path.read_bytes()
    except OSError as exc:
        found.update({
            "status": "unreadable",
            "why": (f"the context input '{name}' ({word}:{relative}) could not "
                    f"be read: {exc.strerror or exc}"),
            "remedy": (f"make {path} readable by the user the board runs as, or "
                       f"drop the selection")})
        return found
    if len(data) > MAX_CONTEXT_BYTES:
        found.update({
            "status": "refused",
            "why": (f"the context input '{name}' is {len(data)} bytes, over the "
                    f"{MAX_CONTEXT_BYTES}-byte cap on a pinned input"),
            "remedy": ("pin the part a run actually needs rather than the whole "
                       "file — a bundle is an input, not an archive")})
        return found

    sha = hashlib.sha256(data).hexdigest()
    found.update({
        "readable": True, "status": "resolved", "sha256": sha,
        "bytes": len(data), "revision": _source_revision(path),
        "contextId": context_id(name, sha),
    })
    return found


def _bundle_revision(inputs: list[dict]) -> str:
    """One revision for a whole set of inputs: their names, their sources
    and their content hashes, in order. Two resolutions of unchanged files
    land on the same bundle, so a preflight opened five times does not
    leave five copies on disk."""
    parts: list[str] = []
    for item in inputs:
        parts += [item["name"], item["source"], item.get("sha256") or ""]
    return _digest(*parts)


def pin(manifest: dict) -> dict:
    """Copy every resolved input into a read-only bundle, and make that
    the run's declared boundary.

    This is the one write on the resolution path, and it is deliberately
    *not* part of `manifest()`: a preflight must be able to answer without
    leaving anything behind, exactly as `plans.preflight` does. The launch
    calls it once, after the blockers came back empty.

    What the bundle buys is immutability at a boundary a reader can name.
    A source file edited afterwards changes the next run; this run holds
    the bytes it was approved with, and `pinnedSha256` records what landed
    even in the rare case the file changed between the hash and the copy —
    the record says what the run got, never what it was meant to get.
    """
    inputs = [i for i in manifest["context"] if i["status"] == "resolved"]
    if not inputs:
        manifest["bundle"] = None
        return manifest
    revision = _bundle_revision(inputs)
    bundle = config.CONTEXT_DIR / revision[:16]
    bundle.mkdir(parents=True, exist_ok=True)
    for item in inputs:
        target = bundle / f"{item['name']}{Path(item['source']).suffix}"
        try:
            data = Path(item["path"]).read_bytes()
        except OSError as exc:
            item.update({"status": "unreadable", "readable": False,
                         "why": (f"the context input '{item['name']}' became "
                                 f"unreadable between validation and pinning: "
                                 f"{exc.strerror or exc}"),
                         "remedy": "relaunch once the file is readable again"})
            continue
        landed = hashlib.sha256(data).hexdigest()
        try:
            if target.exists():
                target.chmod(0o644)
            target.write_bytes(data)
            os.chmod(target, 0o444)
        except OSError as exc:
            item.update({"status": "unreadable", "readable": False,
                         "why": (f"the context input '{item['name']}' could not "
                                 f"be pinned into {bundle}: {exc.strerror or exc}"),
                         "remedy": "check the tool install's state directory is writable"})
            continue
        item["pinned"] = str(target)
        item["pinnedSha256"] = landed
        item["pinnedAt"] = time.time()
        item["status"] = "pinned"
        if landed != item["sha256"]:
            item["sha256"] = landed
            item["contextId"] = context_id(item["name"], landed)
            item["why"] = ("the source changed between validation and pinning; "
                           "the hash recorded is the content this run actually got")
    manifest["bundle"] = {"path": str(bundle), "revision": revision,
                          "identity": _tool_relative(bundle),
                          "files": len([i for i in inputs if i.get("pinned")]),
                          "access": "read-only"}
    return manifest


# ── resolving one profile ──────────────────────────────────────────────
#
# The baselines below are what each intent *is* when nothing configures
# it, expressed in the vocabulary a document uses — so a reader can see
# which half of a resolved profile came from bench and which from the
# project. Commit, push and publication are deliberately kept out of
# `commands`: they are not checks, they are how work leaves the machine,
# and conflating the two is what made "may it run the tests" and "may it
# push" one question.

BASELINE = {
    "work": {
        "cwd": "worktree",
        "writable": [("worktree", "the card's own worktree — the run edits and "
                                  "commits here and nowhere else")],
        "publication": {"commit": True, "push": False, "publish": False},
        "deny": ["push", "publish", "force-push", "rebase", "reshape-lifecycle"],
        "why": "implement, check and commit inside the worktree bench prepared",
    },
    "act-pr": {
        "cwd": "worktree",
        "writable": [("worktree", "the card's own worktree — the run commits "
                                  "and pushes so the PR updates")],
        "publication": {"commit": True, "push": True, "publish": False},
        "deny": ["publish", "force-push", "rebase", "reshape-lifecycle"],
        "why": "address the review on an open PR and push the result",
    },
    "review": {
        "cwd": "app",
        "writable": [],
        "publication": {"commit": False, "push": False, "publish": True},
        "deny": ["edit", "commit", "push", "force-push", "rebase",
                 "reshape-lifecycle"],
        "why": "read and judge; the only thing it publishes is its verdict",
    },
}

# What each denied word means, so a refusal and a UI row read alike and an
# adapter has something to render. Narrowing only: nothing here is a
# grant.
DENIALS = {
    "edit": "the vendor's file-editing tools are withheld",
    "commit": "no commit may be made",
    "push": "nothing may be pushed",
    "publish": "nothing may be published — no PR opened, merged or released",
    "force-push": "history may never be rewritten under a public branch",
    "rebase": "history may never be rewritten under a public branch",
    "reshape-lifecycle": ("the branch, worktree and base bench planned may not "
                          "be changed by the run"),
}


def _requirement_problem(name: str, table: dict) -> str | None:
    """Why a `requires` entry cannot be honoured. This is the refusal the
    card exists for: a profile that asks for a guarantee the installed
    backend cannot make must fail, not degrade into a sentence."""
    entry = table.get(name)
    if entry is None:
        return (f"the profile requires the capability '{name}', which bench "
                f"does not know — one of {', '.join(sorted(table))}")
    if entry["enforcement"] == "pending":
        return (f"the profile requires '{name}', and nothing on this board "
                f"enforces it ({entry['why']}) — the launch is refused rather "
                f"than run under a restriction that exists only in the prompt")
    if entry["enforcement"] == "prompt-only":
        return (f"the profile requires '{name}', which {entry['by']} can only "
                f"ask for and not enforce — the launch is refused rather than "
                f"reported as guaranteed")
    return None


def resolve(intent: str, *, worktree: Path | str | None = None) -> dict:
    """One intent's effective policy, with nothing written.

    Everything in the returned document is either a fact about this board
    or a validated value from the profile document; `blockers` is the
    whole answer to "may a launch start" and empty means yes. The UI and
    the launch both read this function, which is the point — a page that
    parsed the settings a second time could disagree with the run.
    """
    if intent not in INTENTS:
        raise ValueError(f"unknown launch intent '{intent}' — one of "
                         f"{', '.join(INTENTS)}")
    baseline = BASELINE[intent]
    doc = document()
    adapter = adapter_identity()
    table = capability_table(adapter)
    available = roots(worktree)

    cwd_word = baseline["cwd"]
    cwd_path = available.get(cwd_word) or (str(worktree) if worktree else None)

    notes: list[str] = []
    blockers: list[str] = []
    configured: list[str] = []
    prompt_only: list[str] = []

    declared = doc["profiles"].get(intent) if doc["kind"] == "file" else None
    if doc["problem"]:
        blockers.append(doc["problem"])
    if adapter.get("note"):
        notes.append(adapter["note"])

    # — command grants —
    grants: list[dict] = []
    if doc["kind"] == "file" and isinstance(declared, dict):
        raw_grants = declared.get("commands")
        if raw_grants is None:
            raw_grants = doc.get("commands") or []
            if raw_grants:
                notes.append(f"'{intent}' names no commands of its own, so the "
                             f"document's shared list applies to it")
        seen: set[str] = set()
        for raw in raw_grants if isinstance(raw_grants, list) else []:
            if not isinstance(raw, dict):
                blockers.append(f"a command grant for '{intent}' is not an "
                                f"object: {raw!r}")
                continue
            problem = _grant_problem(raw, available)
            if problem:
                blockers.append(problem)
                continue
            grant = _grant(raw, source=doc["identity"] or "the profile document",
                           available=available)
            if grant["name"] in seen:
                blockers.append(f"the command grant '{grant['name']}' is "
                                f"declared twice for '{intent}' — a grant's "
                                f"name is its identity")
                continue
            seen.add(grant["name"])
            grants.append(grant)
    elif doc["kind"] == "file":
        notes.append(f"the profile document {doc['identity']} declares nothing "
                     f"for '{intent}', so it resolves on the legacy settings")
        legacy, trouble = _legacy_grants(intent, available)
        grants += legacy
        notes += trouble
    elif doc["kind"] == "legacy":
        legacy, trouble = _legacy_grants(intent, available)
        grants += legacy
        notes += trouble
    # A broken document grants nothing at all. Resolving it to the legacy
    # settings would hand an operator the permissions they were in the
    # middle of changing, which is the one reading a mistyped policy must
    # never get — the blocker above has already refused the launch.

    if grants:
        configured.append("command-allowlist")

    # — the UI's own commands, which grant an agent nothing —
    ui_commands = [{"name": entry["name"], "help": entry["help"] or None,
                    "agentCapability": False,
                    "why": ("a $ chip on the card, run by a person against this "
                            "card's worktree — registering it here is not a "
                            "grant to any headless run")}
                   for entry in config.commands()]

    # — approved context —
    selections: list[dict] = []
    if doc["kind"] == "file":
        raw_context = None
        if isinstance(declared, dict):
            raw_context = declared.get("context")
        if raw_context is None:
            raw_context = doc.get("context") or []
        if isinstance(raw_context, list):
            selections = [s for s in raw_context if isinstance(s, dict)]
    # A name is an input's identity — in the manifest, in the prompt, and
    # as the file it is pinned at. Two selections sharing one would pin
    # over each other, so the second is refused rather than silently
    # deciding which document the run actually got.
    seen_inputs: set[str] = set()
    deduped: list[dict] = []
    for raw in selections:
        name = str(raw.get("name") or "").strip()
        if name and name in seen_inputs:
            blockers.append(f"the context input '{name}' is declared twice for "
                            f"'{intent}' — an input's name is its identity, "
                            f"and two files cannot share one")
            continue
        seen_inputs.add(name)
        deduped.append(raw)
    selections = deduped
    context = [_resolve_selection(raw, available) for raw in selections]
    for item in context:
        if item["status"] == "resolved":
            continue
        if item["required"]:
            blockers.append(f"{item['why']}. {item['remedy']}")
        else:
            notes.append(f"{item['why']} — the run goes ahead without it")
    if any(i["status"] == "resolved" for i in context):
        configured.append("pinned-context")

    # — named checks, and the preparation they need —
    #
    # The document declares them once, at the top level, and each intent
    # names which of them it may run. One definition, several intents: a
    # check reviewed once is the same check whichever intent asks for it,
    # and `review` gaining the right to *ask* is not `review` gaining the
    # right to commit.
    steps, step_trouble = document_steps(doc)
    blockers += step_trouble
    available_checks, check_trouble = document_checks(doc, steps)
    blockers += check_trouble

    def _named(key: str, known: dict, what: str) -> list[dict]:
        raw = declared.get(key) if isinstance(declared, dict) else None
        # A document-level `verify` is the shared default — one set of
        # checks every intent may ask for. There is deliberately no such
        # fallback for `prepare`: the top-level list is the step
        # *definitions*, and an intent's preparation is what its checks
        # name plus what it names itself.
        if raw is None and key == "verify" and doc["kind"] == "file":
            raw = doc.get("verify")
        out, seen = [], set()
        for entry in raw if isinstance(raw, list) else []:
            if isinstance(entry, dict):
                entry = entry.get("name")
            name = str(entry or "").strip()
            if not NAME_RE.match(name):
                blockers.append(f"'{intent}' names the {what} {name!r} — "
                                f"names match [a-z0-9][a-z0-9._-]*")
                continue
            if name in seen:
                continue
            seen.add(name)
            if name not in known:
                blockers.append(
                    f"'{intent}' asks for the {what} '{name}', which the "
                    f"document does not declare. Declare it in the top-level "
                    f"\"{'checks' if known is available_checks else 'prepare'}\" "
                    f"list — bench will not run something it cannot read, and "
                    f"a requirement naming nothing is how a board reports a "
                    f"check that never ran as one that passed")
                continue
            out.append(known[name])
        return out

    verify = _named("verify", available_checks, "check")
    prepare = _named("prepare", steps, "preparation step")
    if verify:
        configured.append("named-verification")
        configured.append("verification-evidence")
    if prepare or any(c["prepare"] for c in verify):
        configured.append("prepared-inputs")
    if verify:
        configured.append("isolated-environment")
    for step in [*prepare, *(s for c in verify for s in c["prepare"])]:
        if step["network"]:
            notes.append(
                f"the preparation step '{step['name']}' is declared as needing "
                f"the network, and bench enforces no network boundary "
                f"({PENDING_CAPABILITIES['network-policy']}) — it runs with "
                f"whatever this machine has, and the evidence says so")
    # Asking bench to run a check is itself something the run must be
    # allowed to launch, so it is a grant like any other — bench's own,
    # named, and present only where there is a check to ask for. It is one
    # executable with no arguments of its own, so the prefix rule an
    # adapter renders covers exactly `bench-verify <name> [<scope>]` and
    # nothing that could be composed from it.
    if verify:
        shim = {"name": VERIFY_GRANT, "run": str(verify_shim()),
                "root": cwd_word, "help": "ask bench to run a named check"}
        problem = _grant_problem(shim, available)
        if problem:
            blockers.append(problem)
        elif any(g["name"] == VERIFY_GRANT for g in grants):
            blockers.append(f"a command grant is named '{VERIFY_GRANT}', which "
                            f"is bench's own — rename it")
        else:
            grant = _grant(shim, source="bench", available=available)
            grant["why"] = ("the run asks for a check by name; bench builds "
                            "the argument list and runs it")
            grants.append(grant)
            configured.append("command-allowlist")

    # — denied capabilities —
    deny_words = list(baseline["deny"])
    extra = declared.get("deny") if isinstance(declared, dict) else None
    for word in extra if isinstance(extra, list) else []:
        word = str(word).strip()
        if word not in DENIALS:
            blockers.append(f"'{intent}' denies the unknown capability "
                            f"{word!r} — one of {', '.join(sorted(DENIALS))}")
            continue
        if word not in deny_words:
            deny_words.append(word)
    denied = [{"capability": word, "why": DENIALS[word]} for word in deny_words]
    if adapter["capabilities"].get("command-denylist"):
        configured.append("command-denylist")

    # — publication, kept apart from the checks —
    publication = dict(baseline["publication"])
    declared_pub = declared.get("publication") if isinstance(declared, dict) else None
    for key in ("commit", "push", "publish"):
        if isinstance(declared_pub, dict) and key in declared_pub:
            asked = bool(declared_pub[key])
            if asked and not publication[key]:
                blockers.append(
                    f"'{intent}' asks for publication.{key} = true, and the "
                    f"intent does not have it — a profile narrows what an "
                    f"intent may do and never widens it. Use the intent whose "
                    f"prompt actually demands that side effect")
                continue
            publication[key] = asked

    # — what the profile requires, and whether this board can honour it —
    requires = []
    raw_requires = (declared.get("requires") if isinstance(declared, dict)
                    else None)
    if raw_requires is None:
        raw_requires = doc.get("requires") or []
    for word in raw_requires if isinstance(raw_requires, list) else []:
        word = str(word).strip()
        requires.append(word)
        problem = _requirement_problem(word, table)
        if problem:
            blockers.append(problem)

    configured.append("working-directory")
    if config.agent_model(intent):
        configured.append("model-selection")

    enforced = sorted({name for name in configured
                       if table.get(name, {}).get("enforcement") == "enforced"})
    by_prefix = sorted({name for name in configured
                        if table.get(name, {}).get("enforcement") == "prefix-allowlist"})
    pending = sorted({name for name in configured
                      if table.get(name, {}).get("enforcement") == "pending"})

    revision = _digest(
        str(PROFILE_VERSION), str(RUNNER_VERSION), intent, doc["revision"],
        adapter["name"], adapter.get("version") or "",
        json.dumps([g["prefix"] for g in grants], sort_keys=True),
        json.dumps(deny_words, sort_keys=True),
        json.dumps(publication, sort_keys=True),
        json.dumps([(s.get("name"), s.get("path")) for s in selections],
                   sort_keys=True, default=str),
        # A check the operator edits is a different policy, so evidence
        # recorded under the old one reads as stale rather than as being
        # about the command that now bears its name.
        json.dumps([c["revision"] for c in verify]),
        json.dumps([s["run"] for s in prepare]))

    return {
        "v": PROFILE_VERSION,
        "intent": intent,
        "why": baseline["why"],
        "revision": revision,
        "policyId": policy_id(intent, revision),
        "runner": {"module": "profiles", "version": RUNNER_VERSION},
        "source": {"kind": doc["kind"], "identity": doc["identity"],
                   "path": doc["path"], "revision": doc["revision"],
                   "describes": doc["describes"],
                   "precedence": ["BOARD_AGENT_PROFILES",
                                  "manager/local/profiles.json",
                                  "BOARD_AGENT_COMMANDS and the adapter's "
                                  "built-in grants"]},
        "adapter": adapter,
        "capabilities": table,
        "cwd": {"policy": cwd_word, "path": cwd_path},
        "roots": available,
        "commands": grants,
        "uiCommands": ui_commands,
        "context": context,
        "bundle": None,
        "prepare": prepare,
        "verify": verify,
        "denied": denied,
        "publication": publication,
        "writable": [{"root": word, "path": available.get(word), "why": why}
                     for word, why in baseline["writable"]
                     if available.get(word)],
        "requires": requires,
        "guarantees": {
            "configured": sorted(set(configured)),
            "enforced": enforced,
            "prefixMatched": by_prefix,
            "promptOnly": prompt_only,
            "pending": pending,
            "caveat": ("a prefix allow-rule narrows which command lines a run "
                       "may launch. It is not a sandbox: nothing here confines "
                       "a process that is already running"),
        },
        "notes": notes,
        "blockers": blockers,
    }


# ── the manifest one run rode ──────────────────────────────────────────


def manifest(intent: str, filename: str, stage: str, *,
             worktree: Path | str | None = None, pin_inputs: bool = False) -> dict:
    """The resolved profile, tied to one card and one request.

    `pin_inputs=False` — the UI's reading and every preflight — writes
    nothing at all. `pin_inputs=True` is the launch's, and it is the
    moment the inputs stop being able to change. A manifest with blockers
    is never pinned: `require()` is between the two.

    The worktree is derived from the card when the caller does not name
    one, because the page and the launch must resolve the same roots: a
    board that showed a profile with no worktree root, and then launched
    one with it, would be the second parser this design exists to avoid.
    A read-only intent genuinely has none.
    """
    if worktree is None and intent != "review":
        worktree = config.WORKTREES / filename[:-3]
    found = resolve(intent, worktree=worktree)
    found.update({
        "task": filename,
        "stage": stage,
        "requestId": request_id(intent, filename),
        "resolved": time.time(),
    })
    if pin_inputs and not found["blockers"]:
        pin(found)
    return found


def require(found: dict) -> dict:
    """The manifest, or the first reason it cannot be used.

    Blockers are appended in the order the resolution asked its questions,
    so the first is the earliest thing that went wrong — and every one of
    them lands before a worktree, a claim or a billable model exists.
    """
    if found["blockers"]:
        raise ValueError(found["blockers"][0])
    return found


def manifest_path(agent_id: str) -> Path:
    return config.MANIFESTS_DIR / f"{agent_id}.json"


def record(agent_id: str, found: dict) -> dict:
    """Freeze this run's policy and inputs. Machine-local, for plans'
    reason: it holds this machine's paths, and no part of it belongs in a
    repository anybody else pulls."""
    config.MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    stored = dict(found)
    stored["agent"] = agent_id
    stored["recorded"] = time.time()
    stored.pop("blockers", None)
    manifest_path(agent_id).write_text(
        json.dumps(stored, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    return stored


def recorded(agent_id: str) -> dict | None:
    try:
        data = json.loads(manifest_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def forget(agent_id: str) -> bool:
    """Drop one run's manifest.

    Nothing in this card calls it, deliberately: unlike a plan, which
    describes a card's *current* work and stops describing anything once
    that work merges, a manifest is the record of what one run was granted
    — the thing a reviewer or a later gate goes back to. So it outlives
    the card, and the way to remove one is for a person (or a later
    member, whose cleanup policy this is) to ask.
    """
    try:
        manifest_path(agent_id).unlink()
        return True
    except OSError:
        return False


def freshness(frozen: dict) -> dict:
    """What has changed since this run was planned, and what has not.

    This is the half of the design that keeps "what the settings say now"
    from being read as "what that run rode". It compares, it never
    repins: a profile edited mid-run retargets the next launch and leaves
    this one exactly as it was recorded.
    """
    if not frozen:
        return {"fresh": True, "changed": []}
    changed: list[str] = []
    try:
        # Resolved against the roots this run *had*, which matters: a
        # command grant runs in the worktree root by default, and
        # re-resolving without one would refuse that grant and then report
        # the refusal as "the policy changed". The comparison has to ask
        # the same question the launch asked.
        current = resolve(frozen.get("intent") or "work",
                          worktree=(frozen.get("roots") or {}).get("worktree"))
    except ValueError:
        return {"fresh": True, "changed": []}
    if frozen.get("revision") and current["revision"] != frozen["revision"]:
        changed.append("the resolved policy for this intent has changed since "
                       "the run was recorded")
    if (frozen.get("source") or {}).get("revision") != current["source"]["revision"]:
        changed.append(f"the policy source ("
                       f"{current['source']['describes'] or 'the legacy settings'}"
                       f") has been edited")
    if (frozen.get("runner") or {}).get("version") != RUNNER_VERSION:
        changed.append(f"the resolver itself has moved to v{RUNNER_VERSION}")
    frozen_adapter = frozen.get("adapter") or {}
    if frozen_adapter.get("version") != current["adapter"].get("version") \
            or frozen_adapter.get("name") != current["adapter"].get("name"):
        changed.append("the agent adapter, or the version of it that declares "
                       "its capabilities, has changed")
    for item in frozen.get("context") or []:
        name, sha = item.get("name"), item.get("sha256")
        if not sha or not item.get("path"):
            continue
        try:
            now = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
        except OSError:
            changed.append(f"the source of the pinned input '{name}' can no "
                           f"longer be read; the run's own copy is unaffected")
            continue
        if now != sha:
            changed.append(f"the source of the pinned input '{name}' has been "
                           f"edited since it was pinned; the run's own copy is "
                           f"unaffected")
    return {"fresh": not changed, "changed": changed}


# ── what leaves this module ────────────────────────────────────────────

# The API and the UI see exactly these keys. An allowlist rather than a
# blocklist, because the failure being prevented is a *new* key carrying
# something that should not travel — a file's contents, an environment, a
# credential. Nothing here holds any of the three, and the only way to
# add one is to add it here on purpose.
PUBLIC = ("v", "intent", "why", "revision", "policyId", "runner", "source",
          "adapter", "capabilities", "cwd", "roots", "commands", "uiCommands",
          "context", "bundle", "prepare", "verify", "denied", "publication",
          "writable", "requires", "guarantees", "notes", "blockers", "task",
          "stage", "requestId", "resolved", "recorded", "agent")

# Per-input keys that may be shown. Contents are never among them, and a
# `why`/`remedy` is prose bench wrote rather than anything read off disk.
PUBLIC_INPUT = ("name", "source", "root", "required", "readable", "status",
                "sha256", "pinnedSha256", "bytes", "revision", "contextId",
                "pinned", "path", "why", "remedy", "access", "pinnedAt")


def public(found: dict | None) -> dict | None:
    """The manifest as the board may show it: the allowlist above, and
    inputs reduced to their identities. No file content has ever been in
    this structure and this is the door that keeps it that way."""
    if not found:
        return None
    out = {key: found[key] for key in PUBLIC if key in found}
    out["context"] = [{k: item[k] for k in PUBLIC_INPUT if k in item}
                      for item in found.get("context") or []]
    return out


def summary(found: dict | None) -> dict | None:
    """The compact reading a card or a run row wears: which policy, how
    many grants and inputs, and whether anything stops a launch."""
    if not found:
        return None
    context = found.get("context") or []
    return {
        "intent": found.get("intent"),
        "policyId": found.get("policyId"),
        "sourceKind": (found.get("source") or {}).get("kind"),
        "commands": len(found.get("commands") or []),
        "inputs": len(context),
        "readable": len([i for i in context if i.get("readable")]),
        # the named checks this run may ask bench to run, which is a
        # different fact from the command prefixes it may launch itself
        "checks": [c["name"] for c in found.get("verify") or []],
        "denied": len(found.get("denied") or []),
        "blockers": list(found.get("blockers") or []),
        "pending": list((found.get("guarantees") or {}).get("pending") or []),
    }


# ── what the adapter and the prompt are told ───────────────────────────


def adapter_policy(found: dict) -> dict:
    """The half of a manifest an adapter needs to generate its permission
    rules. Narrow on purpose: an adapter renders grants and denials, and
    has no business with the notes, the capability table or this board's
    roots beyond the one it runs in."""
    return {
        "v": PROFILE_VERSION,
        "intent": found["intent"],
        "policyId": found["policyId"],
        "cwd": found["cwd"],
        "commands": [{"name": g["name"], "prefix": g["prefix"],
                      "root": g["root"]} for g in found["commands"]],
        "deny": [d["capability"] for d in found["denied"]],
        "publication": found["publication"],
        "requires": found["requires"],
    }


def adapter_context(found: dict) -> list[dict]:
    """The pinned inputs, for an adapter that can grant reads narrowly.
    Paths and identities only — never content."""
    return [{"name": i["name"], "path": i.get("pinned") or i.get("path"),
             "source": i["source"], "sha256": i.get("sha256"),
             "access": "read-only"}
            for i in found.get("context") or []
            if i.get("status") in ("pinned", "resolved")]


def prompt_block(found: dict | None) -> str:
    """What the run is told about its effective inputs.

    Additive, and it says so: the workflow brief the prompts already point
    at is not replaced, reworded or summarised here — a protected brief
    stays exactly the sentence `config.prompt_guidance` writes. This block
    names the files bench has *guaranteed* the run has, by the path they
    were pinned at, and nothing else.

    Empty string when a profile selects no context, so a board that
    configured none renders the prompt it always did, byte for byte.
    """
    if not found:
        return ""
    usable = [i for i in found.get("context") or []
              if i.get("status") in ("pinned", "resolved")]
    if not usable:
        return ""
    lines = ["The board has pinned the following files as read-only inputs for "
             "this run, at the revision it validated them at. They are in "
             "addition to the workflow brief above, which they do not replace:"]
    for item in usable:
        where = item.get("pinned") or item.get("path")
        lines.append(f"- `{item['name']}` — `{where}` (from {item['source']}, "
                     f"{item.get('revision') or 'revision unknown'}, "
                     f"sha256 {str(item.get('sha256') or '')[:12]})")
    lines.append("Reading them is approved. That approval covers these files "
                 "and nothing beside them: it is not permission to edit them, "
                 "to read other files in the directories they came from, or to "
                 "publish their contents.")
    return "\n".join(lines)


# ── the seams later members plug into ──────────────────────────────────


def seams() -> dict:
    """What this card deliberately left for the members after it, named so
    none of them has to invent a second policy schema.

    Printed by nothing and asserted by the tests: it is the contract, in
    the one place a reader of this module will look for it.
    """
    return {
        "identities": {
            "policy": "profiles.policy_id(intent, revision)",
            "context": "profiles.context_id(name, sha256)",
            "request": "profiles.request_id(intent, filename)",
            "evidence": "profiles.evidence_id(kind, request, detail)",
        },
        "evidenceKinds": dict(EVIDENCE_KINDS),
        "pending": dict(PENDING_CAPABILITIES),
        "extend": {
            "a runner": ("`verify.py` — it reads `verify` and `prepare` off the "
                         "recorded manifest, runs each named check itself with "
                         "no shell, and files the evidence under "
                         "evidence_id('verification', requestId, …)"),
            "a doctor": ("call resolve() for each of profiles.INTENTS and report "
                         "`blockers` and `guarantees.pending` — it needs no "
                         "second parser and must not grow one"),
            "a gate": ("read the evidence `verify.for_task()` returns, insist on "
                       "a `passed` that `verify.freshness()` calls fresh, and "
                       "declare 'verified-outputs'"),
            "a reviewer": ("read the frozen manifest for the run under review "
                           "and `freshness()` for what has moved since"),
        },
    }
