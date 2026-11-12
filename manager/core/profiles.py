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
   insist on. Two halves, and they are different facts: `verify` is what
   a *run* may ask bench for, and the document's top-level `"gates"`
   block is what an *integration or acceptance* refuses without. Both are
   validated here, in the policy layer; `gates.py` is what evaluates the
   second against recorded evidence and enforces it.

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
#
# `refine` (task 65) is the narrowest: it reads the card and the code and
# proposes a new brief on stdout, and bench validates and writes it. It
# has no worktree, no command grant, no shell and no edit tool, and it
# runs only through an adapter that says it can launch it that way — see
# REFINE_REQUIRES. An adapter that has never heard of it is refused rather
# than handed AGENT_MODE=refine and left to guess a stance.
INTENTS = ("work", "act-pr", "review", "refine")

# The intents with no worktree of their own: they read, and a card's
# checkout is not made for them.
READ_ONLY_INTENTS = ("review", "refine")

# What an adapter must declare, as `enforced`, before a refinement may be
# launched through it. Both are the vendor's own tool roster, which is the
# only place "cannot write" and "cannot run a command" are real rather
# than a sentence in a prompt.
REFINE_REQUIRES = ("read-only-tools", "shell-withheld")

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
    "review": "rev",            # which revision a review was of
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


def review_id(filename: str, head: str) -> str:
    """Which revision a review was of: the card, and the commit the pull
    request's head actually pointed at when bench pinned it.

    The head is the identity because it is the only thing a verdict can
    honestly be about. A PR whose head has moved is a different review
    question, so it gets a different id rather than the same one with new
    code under it — which is what lets old evidence read as historical
    instead of as satisfaction of the new head's gate.
    """
    stem = filename[:-3] if filename.endswith(".md") else filename
    return f"{ID_PREFIXES['review']}:{stem}@{(head or 'unknown')[:12]}"


def check_id(name: str, revision: str) -> str:
    """Which named check, at which definition. The revision is a digest of
    the check's whole resolved shape — executable, argument list, execution
    root, scopes, preparation — so an operator who edits a check gets a new
    identity rather than old evidence that claims to be about the new
    definition."""
    return f"{ID_PREFIXES['check']}:{name}@{revision[:12]}"


# What a verification records against a request, and what it is for.
# `verify.py` produces the first three; `gates.py` produces the fourth —
# a gate's own verdict about one guarded mutation, which is a reading of
# the other three and never a substitute for them.
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
    "verified-outputs": ("enforced",
                         "a configured gate refuses the mutation it guards "
                         "unless bench's own evidence for every required "
                         "check reads `passed` against the exact commit about "
                         "to be merged. The status is the runner's, the "
                         "binding is the commit, and neither an agent's "
                         "report nor its exit code is an input to it. What it "
                         "does not claim is that the check is a good one: "
                         "bench runs the operator's command and reads what it "
                         "returned"),
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
    "shell-withheld": "the vendor's shell tool is withheld from the run, so it "
                      "can launch no command at all",
    "model-selection": "the configured model reaches the vendor untranslated",
    "progress-report": "the run may run bench's progress shim (AGENT_PROGRESS) "
                       "and nothing else comes with that rule — what it "
                       "reports is shown as agent-reported, never as a result",
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
    "network-policy": ("neither an adapter nor bench exposes a network "
                       "boundary a profile could set, so a preparation step "
                       "that needs the network has whatever network this "
                       "machine has"),
}


# ── diagnostics: the code each refusal came from ───────────────────────
#
# `blockers` is prose, and it has to stay prose: it is what the page leads
# with and what `require()` raises. But an operator-facing doctor has to be
# able to say "this is the *required input unreadable* diagnostic" without
# reading English, and nothing downstream may re-derive a refusal by
# matching on its wording — a reworded sentence would silently stop being
# the diagnostic it was.
#
# So every refusal carries the code it came from, named here with the
# remedy that applies to the whole class of it. One table, beside the
# refusals that raise it, and **no second evaluator anywhere**: a consumer
# reads the code off a resolution it did not make. It never decides
# whether something is blocked.
DIAGNOSTICS = {
    "policy-unreadable": (
        "the profile document cannot be read",
        "Fix the document named above, or unset BOARD_AGENT_PROFILES to "
        "resolve on the legacy settings. Bench will not fall back to them "
        "under a policy you were in the middle of changing."),
    "name-invalid": (
        "a name in the document is not a name",
        "Names match [a-z0-9][a-z0-9._-]* — they are identities in the "
        "manifest, the prompt and the evidence, not prose."),
    "grant-invalid": (
        "a named command grant is not one executable and fixed arguments",
        "Write the grant's \"run\" as one executable and fixed arguments, in "
        "the normalised form. Compound commands, pipelines, redirections, "
        "globs and substitutions are refused because a prefix allow-rule "
        "cannot see what they would expand to."),
    "grant-duplicate": (
        "two command grants share one name",
        "Rename one of them: a grant's name is its identity in the manifest "
        "and on the board."),
    "grant-reserved": (
        "a command grant uses a name bench owns",
        f"Rename the grant. '{'bench-verify'}' is bench's own shim for asking "
        f"for a named check, and it is added to the intents that have one."),
    "grant-root-unavailable": (
        "a command grant names a root this intent does not have",
        "A read-only run has no worktree. Give the grant a root the intent "
        "actually has, or declare it on an intent that does."),
    "context-unreadable": (
        "a required approved input is not there to be read",
        "Put the file where the selection names it, inside the root it names, "
        "or mark the selection \"required\": false if the run is still useful "
        "without it. A directory is refused as a read-only guarantee, because "
        "it would let a run read whatever is dropped into it later."),
    "context-refused": (
        "a context selection is refused before it is read",
        "Name one file, relative to one of the named roots, with no '..' and "
        "no basename that reads as a credential. Approving a document never "
        "approves the secret that happens to sit beside it."),
    "context-duplicate": (
        "two context inputs share one name",
        "Rename one of them: an input's name is the file it is pinned at, so "
        "two selections sharing one would pin over each other."),
    "check-invalid": (
        "a named check is not something bench can run",
        "A check is one executable and fixed arguments: bench splits it once "
        "and hands the result to execve, with no shell to expand anything. "
        "Quoting is allowed here (a check is never rendered into a rule); "
        "pipelines, redirections, globs and substitutions are not."),
    "check-root-unsafe": (
        "a check names an execution root bench will not verify in",
        "Declare the check with \"root\": \"worktree\" (the card's own "
        "checkout), \"source\" (a detached checkout bench makes for the "
        "verification) or \"review\" (the checkout a PR review was pinned). "
        "The application checkout is never one: that is the live code and the "
        "live services."),
    "check-reshapes-repository": (
        "a check would rewrite the repository it is meant to read",
        "The execution root shares its git metadata with every other worktree "
        "of the application. Use a reading subcommand — `git diff --check`, "
        "`git ls-files` — and leave pushes, resets and branch moves to the "
        "lifecycle bench owns."),
    "check-names-credential": (
        "a check's argument list names a file that reads as a credential",
        "Drop the argument. A check is not how a secret gets read, and bench "
        "refuses the argument rather than the file."),
    "check-duplicate": (
        "two checks share one name",
        "Rename one of them: a check's name is what a run asks for and what "
        "its evidence is filed under."),
    "check-undeclared": (
        "an intent asks for a check this document does not declare",
        "Declare it in the document's top-level \"checks\" list, or name one "
        "of that intent's own command grants. A requirement that resolves to "
        "nothing is how a board reports a check that never ran as one that "
        "passed, so it is refused instead."),
    "check-input-undeclared": (
        "a check declares an input the document's context does not select",
        "Add the input to the document's top-level \"context\" list, under "
        "the same name, or drop it from the check's \"inputs\". A check's "
        "result is bound to the inputs it declares, so an input bench cannot "
        "read is refused rather than bound to nothing."),
    "check-env-invalid": (
        "a check declares an environment variable bench cannot bind",
        "Name variables the check actually receives: one environment "
        "variable name each ([A-Za-z_][A-Za-z0-9_]*), listed once. Bench "
        "removes credentials, the launch environment and its own address "
        "before a check runs and sets HOME, TMPDIR and the cache variables "
        "itself, so none of those can be declared."),
    "check-unrunnable": (
        "an intent asks for a check bench cannot build a command line from",
        "Fix the grant or check named above, or stop naming it in this "
        "intent's \"verify\" list."),
    "prepare-invalid": (
        "a preparation step is not something bench can run",
        "Same shape as a check: one executable and fixed arguments. A step "
        "that does not validate is refused rather than skipped, because the "
        "check that needs it would then run unprepared and fail for the "
        "wrong reason."),
    "prepare-reshapes-repository": (
        "a preparation step would rewrite the repository",
        "Prepare dependencies, not history. See check-reshapes-repository."),
    "prepare-names-credential": (
        "a preparation step names a file that reads as a credential",
        "Drop the argument."),
    "prepare-duplicate": (
        "two preparation steps share one name",
        "Rename one of them."),
    "prepare-undeclared": (
        "an intent or a check names a preparation step nothing declares",
        "Add it to the document's top-level \"prepare\" list, or drop the "
        "reference. Bench will not run a step it cannot read."),
    "deny-unknown": (
        "an intent denies a capability bench has no word for",
        "Use one of the known capability words. A typo here would read as a "
        "denial that silently does nothing."),
    "publication-widened": (
        "an intent asks for a publication side effect it does not have",
        "A profile narrows and never widens. Use the intent whose own prompt "
        "demands that side effect — `review` asking for push is refused by "
        "name."),
    "requirement-unsupported": (
        "the profile requires a guarantee nothing installed here enforces",
        "Drop the word from \"requires\", or install a backend that declares "
        "it. A profile requiring a guarantee that exists only in the prompt "
        "is refused rather than reported as honoured."),
    "gate-scope-unknown": (
        "the \"gates\" block names something that is not a gate",
        "A gate is declared per scope: \"member\" (a phase merging one "
        "card's branch into its integration branch), \"integration\" (a "
        "phase's own branch being accepted) or \"acceptance\" (an ordinary "
        "card being merged and cleaned up). A misspelled scope would read as "
        "a gate that silently gates nothing."),
    "gate-invalid": (
        "a gate requirement is not the shape a gate is read in",
        "Each scope takes \"checks\" (a list of declared check names), "
        "\"review\" (one review policy word) and nothing else; "
        "\"overrides\" takes \"allow\" and an optional \"actors\" list."),
    "gate-check-undeclared": (
        "a gate requires a check this document does not declare",
        "Declare it in the document's top-level \"checks\" list. A gate "
        "naming nothing is indistinguishable from no gate at all, so it is "
        "refused rather than resolved to an empty requirement."),
    "gate-scope-undeclared": (
        "a gate requires a scope its check does not declare",
        "Declare the scope under the check's \"scopes\", or require the "
        "check as a bare name — which means its unscoped full run."),
    "harness-unavailable": (
        "the coding-agent harness chosen for this intent is not installed",
        "Choose an installed harness in Settings → Agents (or in "
        "manager/local/.env), or install the adapter directory under "
        "manager/local/adapters/<name>/ with an executable run. Choosing a "
        "harness installs nothing, and bench will not fall back to another."),
    "intent-unsupported": (
        "the installed adapter cannot launch this intent as bench requires",
        "Install an adapter whose capabilities.json lists the intent under "
        "\"intents\" and declares what it needs as enforced — both shipped "
        "adapters do. Bench refuses rather than handing an adapter an intent "
        "it might run with a wider stance, such as implementation's."),
    "intent-restricted": (
        "a profile gives an intent something that intent never has",
        "Remove it. A refinement reads and proposes; it runs no command, asks "
        "for no check and writes nothing — bench validates its proposal and "
        "owns the write."),
    "gate-review-unknown": (
        "a gate asks for a review policy bench has no word for",
        "Use one of the review policy words. `none` gates nothing, `bench` "
        "requires a recorded Bench verdict on the exact head, `github` "
        "requires a formal APPROVED review state on GitHub, and "
        "`separate-reviewer` requires that approval to be authored by "
        "somebody other than the account that proposed the head — a "
        "COMMENTED review is never read as either."),
}


def diagnostic(code: str, why: str, remedy: str | None = None) -> dict:
    """One refusal, with the code it came from and a remedy.

    An unknown code is not silently accepted: the whole value of the code
    is that a consumer can switch on it, and a typo would be a diagnostic
    nothing recognises.
    """
    if code not in DIAGNOSTICS:
        raise ValueError(f"unknown diagnostic code '{code}' — one of "
                         f"{', '.join(sorted(DIAGNOSTICS))}")
    title, standing = DIAGNOSTICS[code]
    return {"code": code, "title": title, "why": why,
            "remedy": remedy or standing}


class _Trouble(list):
    """The `blockers` list every consumer already reads, plus the code each
    entry came from.

    A list subclass rather than a second structure, so the prose half is
    built and read exactly as it was: nothing downstream has to know this
    exists, and `blockers[0]` is still the earliest thing that went wrong.
    """

    def __init__(self) -> None:
        super().__init__()
        self.coded: list[dict] = []

    def add(self, code: str, why: str, remedy: str | None = None) -> None:
        self.append(why)
        self.coded.append(diagnostic(code, why, remedy))

    def add_all(self, code: str, whys) -> None:
        for why in whys:
            if isinstance(why, tuple):
                self.add(why[0], why[1])
            else:
                self.add(code, why)


def adapter_identity(name: str | None = None) -> dict:
    """Which adapter a launch goes through, and what it says it can
    enforce — read from `capabilities.json` beside its `run`.

    `name` is the harness `config.agent_choice()` resolved for the intent
    being asked about; None is the default harness. Each launch intent may
    ride a different one (task 77), so capabilities are always read for
    the harness that would actually run.

    An adapter without the file is not an error and not a liar: it
    declares nothing, so nothing may *require* anything of it, and a
    legacy profile (which requires nothing) runs through it exactly as it
    always did. That is what keeps a project's own
    `local/adapters/<name>/` working across this change.
    """
    if name is None:
        name = config.agent_choice("default")["harness"]["value"]
    directory = config.adapter_dir(name)
    found: dict = {
        "name": name,
        "path": str(directory) if directory else None,
        "identity": _tool_relative(directory) if directory else None,
        "version": None,
        "capabilities": {},
        "unsupported": {},
        "declared": False,
        # which launch intents the adapter says it can run. None = it does
        # not say, which is every adapter written before the list existed;
        # such an adapter keeps the three intents it always ran and is
        # refused the ones added since (see `intent_supported`).
        "intents": None,
    }
    if directory is None:
        found["note"] = (f"agent adapter '{name}' was not found, so "
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
    if isinstance(data.get("intents"), list):
        found["intents"] = [str(i) for i in data["intents"]]
    # what this harness needs before it can run anything — the adapter's
    # own words, shown beside its selection in Settings → Agents
    if isinstance(data.get("setup"), str):
        found["setup"] = data["setup"][:600]
    return found


_SOURCE_WORDS = {"environment": "the process environment (locked)",
                 "process": "an in-process setting (locked)",
                 "local-file": "manager/local/.env",
                 "default": "the documented default"}


def agent_selection(choice: dict) -> dict:
    """What a manifest keeps of `config.agent_choice()`: the harness and
    model names and where each came from. Never a value from anywhere
    else in the environment.

    `model.value` empty is "harness default": bench passed no model and
    does not know which one the vendor resolved, and says so rather than
    guessing."""
    harness, model = choice["harness"], choice["model"]
    return {
        "harness": {"name": harness["value"], "setting": harness["setting"],
                    "source": harness["source"],
                    "inherited": harness["inherited"]},
        "model": {"value": model["value"], "setting": model["setting"],
                  "source": model["source"], "inherited": model["inherited"],
                  "harnessDefault": model["harnessDefault"],
                  "words": (model["value"] if model["value"] else
                            "harness default — no model passed; the vendor "
                            "resolved it and bench does not know which")},
    }


# The intents every adapter ran before adapters declared any. An adapter
# that lists nothing is taken to run exactly these, so nothing that worked
# yesterday stops working; anything newer must be declared to be launched.
LEGACY_INTENTS = ("work", "act-pr", "review")


def intent_supported(adapter: dict, intent: str) -> bool:
    declared = adapter.get("intents")
    return intent in (LEGACY_INTENTS if declared is None else declared)


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

ROOT_WORDS = ("worktree", "review", "app", "work", "tool")


def roots(worktree: Path | str | None, review: Path | str | None = None) -> dict:
    """The approved roots for one launch, resolved. `worktree` is None for
    a read-only intent, and naming the word is then a blocker.

    `review` is the other half of the same rule, and it is the root a PR
    review actually works in: a checkout bench made for that one run,
    detached at the exact commit the pull request's head named
    (`reviews.py`). It is available only to a launch bench has pinned one
    for, so a relevance check — which provisions nothing, fetches nothing
    and reviews no PR — simply does not have the word.
    """
    found = {
        "app": str(config.app_repo()),
        "work": str(config.work_root()),
        "tool": str(config.TM_ROOT),
    }
    if worktree is not None:
        found["worktree"] = str(worktree)
    if review is not None:
        found["review"] = str(review)
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
    """The profile document this board resolves with, read fresh."""
    return document_at(document_path())


def document_at(path: Path | None) -> dict:
    """One profile document, read fresh, plus what to say about it.

    Three answers, and each is a real one. `{"kind": "legacy"}` is a board
    with no document, which resolves exactly as it did before profiles
    existed. `{"kind": "file"}` is a document that parsed. `{"kind":
    "broken"}` is one that did not — and that is a **blocker** on every
    intent rather than a silent fall back to the legacy grants, because an
    operator who wrote a policy and mistyped it must not be given the
    permissions they were trying to change.

    Taking the path rather than finding it is what lets the doctor resolve
    a *fixture* document through this exact reader — one parser, used by
    the launch, the page and the diagnosis alike.
    """
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
    return document_from(raw, path)


def document_from(raw: str, path: Path) -> dict:
    """The same reading, from text already in hand rather than off disk.

    Split out for the one caller that has a document nobody has written
    yet: the Settings editor, validating a draft. It must get the answer a
    launch would get — the same parser, the same codes, the same revision
    — and the only way to guarantee that is for there to be one parser.
    `document_at` is this function plus the read.
    """
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
           "verify": data.get("verify") or [],
           # The gate requirements, kept as written rather than
           # defaulted: `gate_policy` has to tell "no gate block" from
           # "a gate block that is not an object", and the second is a
           # refusal rather than an absence.
           "gates": data.get("gates")}
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


def _grant_problem(raw: dict, available: dict) -> tuple[str, str] | None:
    """Why this grant cannot be used, or None — as `(code, sentence)`.

    One sentence, naming the grant, because it is shown beside it; one
    code, because a doctor has to be able to recognise the class of
    refusal without reading the sentence.
    """
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return ("name-invalid",
                f"a command grant is named {name!r} — a grant's name is its "
                f"identity in the manifest and the UI, so it must match "
                f"[a-z0-9][a-z0-9._-]*")
    if not str(raw.get("run") or "").strip():
        return ("grant-invalid",
                f"the command grant '{name}' has no \"run\" — there is nothing to allow")
    run = str(raw.get("run") or "").strip()
    for breaker in SHELL_BREAKERS:
        if breaker in run:
            return ("grant-invalid",
                    f"the command grant '{name}' contains {breaker!r} in its "
                    f"\"run\" — a grant is one executable and fixed arguments, "
                    f"not a shell line: compound commands, redirections, globs "
                    f"and substitutions are refused because a prefix rule "
                    f"cannot see what they expand to")
    try:
        parts = shlex.split(run)
    except ValueError as exc:
        return ("grant-invalid",
                f"the command grant '{name}' does not parse as a command line ({exc})")
    if not parts:
        return ("grant-invalid", f"the command grant '{name}' has an empty \"run\"")
    if shlex.join(parts) != run:
        return ("grant-invalid",
                f"the command grant '{name}' is written {run!r}, which "
                f"normalises to {shlex.join(parts)!r} — write it in the "
                f"normalised form so the rule and the grant read alike")
    executable = parts[0]
    if ".." in Path(executable).parts:
        return ("grant-invalid",
                f"the command grant '{name}' names the executable "
                f"{executable!r}, which climbs out of its own path with '..'")
    if "/" in executable and not executable.startswith("/"):
        return ("grant-invalid",
                f"the command grant '{name}' names the executable "
                f"{executable!r} — give a bare name resolved on PATH, or an "
                f"absolute path; a relative path depends on a working "
                f"directory the rule does not pin")
    root = str(raw.get("root") or "worktree")
    if root not in ROOT_WORDS:
        return ("grant-invalid",
                f"the command grant '{name}' names the execution root "
                f"{root!r} — one of {', '.join(ROOT_WORDS)}")
    if root not in available:
        return ("grant-root-unavailable",
                f"the command grant '{name}' runs in the {root} root, which "
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
    if intent in READ_ONLY_INTENTS:
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
            problems.append(f"{problem[1]} (from BOARD_AGENT_COMMANDS)")
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
# commit that bench makes for this verification and owns; `review` is the
# checkout bench pinned for a PR review — detached at the reviewed head,
# owned by that run for its whole life (`reviews.py`), and the only root a
# review has that holds the *proposed* code rather than the card's own.
CHECK_ROOTS = ("worktree", "source", "review")

# Which of those roots a check may only have where a launch actually has
# one. A `review` check asked for by a run with no pinned checkout is not
# a broken document — it is a check that does not apply to that launch —
# so it is dropped with a note rather than blocking the intent. Blocking
# would take `◔ still true?` down on every board that declared one, and a
# relevance check provisions nothing to run it in.
ROOT_SCOPED_CHECKS = {"review": "review"}

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


# What a check's own command line may not contain. The grant list, minus
# the two quote characters — and the difference is the point. A *grant* is
# rendered into a prefix allow-rule, and a rule cannot see through a
# quote, so a quoted argument there would widen what it matched. A
# **check** is never rendered into a rule: bench splits it once, here,
# and hands the result to `execve`. So a path with spaces in it is
# expressible exactly the way a shell expresses one, and the things that
# would still make one command line into two are still refused.
CHECK_BREAKERS = tuple(c for c in SHELL_BREAKERS if c not in ("'", '"'))


def _runnable_problem(name: str, what: str, run: str,
                      kind: str = "check") -> tuple[str, str] | None:
    """Why bench cannot run this command line itself, as `(code, sentence)`.

    Everything a command grant is held to except the quoting ban (see
    `CHECK_BREAKERS`), plus the two refusals that exist only because bench
    is the one running it: a git subcommand that reshapes the repository,
    and an argument that names a file reading as a credential. Those two
    get codes of their own, because they are the two an operator fixes
    differently from a typo.
    """
    run = run.strip()
    if not run:
        return (f"{kind}-invalid",
                f"the {what} '{name}' has no \"run\" — there is nothing to run")
    for breaker in CHECK_BREAKERS:
        if breaker in run:
            return (f"{kind}-invalid",
                    f"the {what} '{name}' contains {breaker!r} in its \"run\" "
                    f"— a check is one executable and fixed arguments, not a "
                    f"shell line: compound commands, pipelines, redirections, "
                    f"globs and substitutions are refused because bench runs "
                    f"it with no shell and there is nothing to expand them")
    try:
        parts = shlex.split(run)
    except ValueError as exc:
        return (f"{kind}-invalid",
                f"the {what} '{name}' does not parse as a command line ({exc})")
    if not parts:
        return (f"{kind}-invalid", f"the {what} '{name}' has an empty \"run\"")
    if shlex.join(parts) != run:
        return (f"{kind}-invalid",
                f"the {what} '{name}' is written {run!r}, which normalises to "
                f"{shlex.join(parts)!r} — write it in the normalised form so "
                f"the document and the argument list read alike")
    executable = parts[0]
    if ".." in Path(executable).parts:
        return (f"{kind}-invalid",
                f"the {what} '{name}' names the executable {executable!r}, "
                f"which climbs out of its own path with '..'")
    if "/" in executable and not executable.startswith("/"):
        return (f"{kind}-invalid",
                f"the {what} '{name}' names the executable {executable!r} — "
                f"give a bare name resolved on PATH, or an absolute path; a "
                f"relative one depends on a working directory the document "
                f"cannot pin")
    if Path(parts[0]).name == "git":
        subcommand = next((p for p in parts[1:] if not p.startswith("-")), "")
        if subcommand in GIT_RESHAPING:
            return (f"{kind}-reshapes-repository",
                    f"the {what} '{name}' runs `git {subcommand}`, which "
                    f"reshapes the repository rather than reading it — the "
                    f"execution root shares its git metadata with every other "
                    f"worktree of the application, and bench will not run "
                    f"something that rewrites it in the name of verifying")
    for token in parts[1:]:
        if SECRET_NAMES.search(Path(token).name):
            return (f"{kind}-names-credential",
                    f"the {what} '{name}' names {token!r}, whose basename reads "
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


# ── what a check's result depends on, declared ─────────────────────────
#
# A check's result is bound to its source, its definition and its programs
# whether or not anyone declares anything. Two further things only the
# operator can name, because bench cannot discover them: the approved
# **inputs** a check reads (a context selection, by name, pinned and
# handed to it), and the **environment variables** whose values its
# verdict depends on. Declared, they become part of the result identity
# `verify` records; undeclared, they are not bound — the documented limit.

# Names bench takes out of a check's environment, or sets itself, so a
# declaration naming one would bind something the check never receives.
# `verify.child_env` builds the environment from these same lists.
VERIFY_ENV_STRIPPED = ("BOARD_PORT", "BOARD_AGENT_ID", "BOARD_TASK",
                       "BOARD_APP_ROOT", "BOARD_WORK_ROOT", "BOARD_TASKS_DIR",
                       "BOARD_WORKTREES_DIR", "GIT_DIR", "GIT_WORK_TREE",
                       "GIT_INDEX_FILE")
VERIFY_ENV_STRIPPED_PREFIXES = ("AGENT_",)
VERIFY_ENV_SET = ("HOME", "TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME",
                  "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                  "PIP_CACHE_DIR", "NPM_CONFIG_CACHE", "YARN_CACHE_FOLDER",
                  "CARGO_HOME", "GOCACHE", "GOMODCACHE", "BENCH_VERIFY",
                  "BENCH_VERIFY_CHECK", "BENCH_VERIFY_ROOT",
                  "BENCH_VERIFY_INPUTS", "PWD")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# A bound, not a policy: a check is handed its inputs as copies, and an
# identity over hundreds of variables is no longer something a reader can
# check by eye.
MAX_CHECK_INPUTS = 16
MAX_CHECK_ENV = 32
# The roots a check's input may be selected from. The worktree and the
# review checkout are the check's *source*, already bound by the source
# identity, so naming a file in them as an input would bind it twice and
# pin a copy of code that is about to be run in place anyway.
CHECK_INPUT_ROOTS = ("tool", "work", "app")


def _env_problem(name: str, declared) -> tuple[str, str] | None:
    """Why a check's `env` declaration cannot be bound, or None."""
    if declared is None:
        return None
    if not isinstance(declared, list):
        return ("check-env-invalid",
                f"the check '{name}' declares \"env\" that is not a list of "
                f"variable names")
    if len(declared) > MAX_CHECK_ENV:
        return ("check-env-invalid",
                f"the check '{name}' declares {len(declared)} environment "
                f"variables, over the {MAX_CHECK_ENV} bench binds per check")
    denied = set(config.verify_env_deny())
    seen: set[str] = set()
    for entry in declared:
        if not isinstance(entry, str) or not ENV_NAME_RE.match(entry):
            return ("check-env-invalid",
                    f"the check '{name}' declares the environment variable "
                    f"{entry!r}, which is not a variable name")
        if entry in seen:
            return ("check-env-invalid",
                    f"the check '{name}' declares the environment variable "
                    f"'{entry}' twice")
        seen.add(entry)
        if entry.startswith("BOARD_") and entry not in VERIFY_ENV_STRIPPED:
            return ("check-env-invalid",
                    f"the check '{name}' declares '{entry}', which is this "
                    f"board's own setting rather than configuration of the "
                    f"code under test")
        if entry in denied or entry in VERIFY_ENV_STRIPPED \
                or entry.startswith(VERIFY_ENV_STRIPPED_PREFIXES):
            return ("check-env-invalid",
                    f"the check '{name}' declares '{entry}', which bench "
                    f"removes before a check runs — binding its value would "
                    f"bind something the check never receives")
        if entry in VERIFY_ENV_SET:
            return ("check-env-invalid",
                    f"the check '{name}' declares '{entry}', which bench sets "
                    f"itself for every check — its value is a path made for "
                    f"one verification, not configuration the result depends "
                    f"on")
    return None


def _inputs_problem(name: str, declared,
                    known_inputs: dict) -> tuple[str, str] | None:
    """Why a check's `inputs` declaration cannot be honoured, or None."""
    if declared is None:
        return None
    if not isinstance(declared, list):
        return ("check-invalid",
                f"the check '{name}' declares \"inputs\" that is not a list "
                f"of context-selection names")
    if len(declared) > MAX_CHECK_INPUTS:
        return ("check-invalid",
                f"the check '{name}' declares {len(declared)} inputs, over the "
                f"{MAX_CHECK_INPUTS} bench pins per check")
    seen: set[str] = set()
    for entry in declared:
        word = str(entry or "").strip() if isinstance(entry, str) else entry
        if not isinstance(word, str) or not NAME_RE.match(word):
            return ("name-invalid",
                    f"the check '{name}' declares the input {entry!r} — an "
                    f"input is named as its context selection is, matching "
                    f"[a-z0-9][a-z0-9._-]*")
        if word in seen:
            return ("check-invalid",
                    f"the check '{name}' declares the input '{word}' twice")
        seen.add(word)
        selection = known_inputs.get(word)
        if selection is None:
            return ("check-input-undeclared",
                    f"the check '{name}' declares the input '{word}', which "
                    f"the document's top-level \"context\" list does not "
                    f"select — bench pins and hands a check only what an "
                    f"operator selected, and binds nothing it cannot read")
        spec = str(selection.get("path") or "").strip()
        word_root = spec.partition(":")[0]
        if word_root not in CHECK_INPUT_ROOTS:
            return ("check-invalid",
                    f"the check '{name}' declares the input '{word}', which "
                    f"is selected from the {word_root or 'unnamed'} root — a "
                    f"check's inputs come from "
                    f"{', '.join(CHECK_INPUT_ROOTS)}; the checkout it runs in "
                    f"is its source, and is bound as that")
        problem, _ = _selection_problem(selection, roots(None))
        if problem:
            return ("check-invalid",
                    f"the check '{name}' declares the input '{word}', and its "
                    f"selection is refused: {problem}")
    return None


def context_selections(doc: dict) -> dict:
    """The document's top-level context selections, by name — the only
    list a check's `inputs` may name. One list for the whole document
    because a check is declared once, for every intent that may ask for
    it, and its inputs must not depend on which intent asked."""
    raw = doc.get("context") if doc.get("kind") == "file" else None
    out: dict[str, dict] = {}
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            key = str(entry.get("name") or "").strip()
            if key and key not in out:
                out[key] = entry
    return out


def check_input(entry: dict) -> dict:
    """One declared input of a check, resolved now: read and hashed in the
    root its selection names, nothing written. The same reading a launch's
    context takes (`_resolve_selection`), so a check is handed exactly what
    an approved input is and is refused exactly where one is."""
    return _resolve_selection({"name": entry.get("name"),
                               "path": entry.get("source"),
                               "required": entry.get("required", True)},
                              roots(None))


def _check_problem(raw: dict, known_prepare: set[str],
                   known_inputs: dict | None = None) -> tuple[str, str] | None:
    """Why this check cannot be offered, or None — as `(code, sentence)`.
    One sentence, naming the check, because it is shown beside it."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return ("name-invalid",
                f"a check is named {name!r} — a check's name is what a run "
                f"asks for and what its evidence is filed under, so it must "
                f"match [a-z0-9][a-z0-9._-]*")
    problem = _runnable_problem(name, "check", str(raw.get("run") or ""))
    if problem:
        return problem
    root = str(raw.get("root") or "worktree")
    if root not in CHECK_ROOTS:
        return ("check-root-unsafe",
                f"the check '{name}' names the execution root {root!r} — one "
                f"of {', '.join(CHECK_ROOTS)}. A check never runs in the "
                f"application checkout: that is the live code and the live "
                f"services, and verifying in it is how a test suite takes a "
                f"working tree down with it")
    raw_scopes = raw.get("scopes")
    if raw_scopes is not None and not isinstance(raw_scopes, dict):
        return ("check-invalid",
                f"the check '{name}' declares \"scopes\" that are not an "
                f"object — write {{\"<name>\": [\"<argument>\", …]}}, so a run "
                f"selects a scope by name and never by writing arguments")
    for key, tokens in (raw_scopes or {}).items():
        if not NAME_RE.match(str(key)):
            return ("name-invalid",
                    f"the check '{name}' declares the scope {key!r} — a scope "
                    f"is selected by name, so it must match "
                    f"[a-z0-9][a-z0-9._-]*")
        if not isinstance(tokens, list):
            return ("check-invalid",
                    f"the scope '{key}' of the check '{name}' is not a list of "
                    f"arguments")
        trouble = _tokens_problem(name, f"the scope '{key}'", tokens)
        if trouble:
            return ("check-invalid", trouble)
        for token in tokens:
            if SECRET_NAMES.search(Path(str(token)).name):
                return ("check-names-credential",
                        f"the scope '{key}' of the check '{name}' names "
                        f"{token!r}, whose basename reads as a credential")
    raw_prepare = raw.get("prepare")
    if raw_prepare is not None and not isinstance(raw_prepare, list):
        return ("check-invalid",
                f"the check '{name}' declares \"prepare\" that is not a list "
                f"of preparation-step names")
    for step in raw_prepare or []:
        if str(step) not in known_prepare:
            return ("prepare-undeclared",
                    f"the check '{name}' needs the preparation step "
                    f"{str(step)!r}, which the document does not declare — add "
                    f"it to the document's top-level \"prepare\" list, or drop "
                    f"the reference; bench will not run a step it cannot read")
    timeout = raw.get("timeout")
    if timeout is not None:
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            return ("check-invalid",
                    f"the check '{name}' declares a \"timeout\" that is not a number")
        if value <= 0 or value > MAX_CHECK_TIMEOUT:
            return ("check-invalid",
                    f"the check '{name}' declares a timeout of {timeout} — give "
                    f"a number of seconds between 1 and {MAX_CHECK_TIMEOUT}")
    problem = _inputs_problem(name, raw.get("inputs"), known_inputs or {})
    if problem:
        return problem
    return _env_problem(name, raw.get("env"))


def _step_problem(raw: dict) -> tuple[str, str] | None:
    """The same question for one preparation step."""
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        return ("name-invalid",
                f"a preparation step is named {name!r} — names match "
                f"[a-z0-9][a-z0-9._-]*")
    return _runnable_problem(name, "preparation step",
                             str(raw.get("run") or ""), kind="prepare")


def _check_revision(name: str, run: str, root: str, scopes: dict,
                    prepare: list[str], timeout: float,
                    inputs: list | None = None,
                    env: list | None = None) -> str:
    # Inputs and env only when declared, so a check that declares neither
    # keeps the revision — and the checkId — it has always had.
    extra = [json.dumps(inputs, sort_keys=True)] if inputs else []
    extra += [json.dumps(env)] if env else []
    return _digest(name, run, root,
                   json.dumps(scopes, sort_keys=True),
                   json.dumps(prepare), f"{timeout:.0f}", *extra)


def check_substance(run: str, root: str, timeout: float, inputs: list,
                    env: list) -> str:
    """What a check *is*, without what it is called: its argument list, the
    root it runs in, its time limit, and the inputs and environment
    variables it declares. Unlike its revision this excludes the name, so
    two checks with the same substance are the same definition — which is
    the only thing a declared equivalence may ever rely on (design §5).
    Coverage and preparation are separate dimensions of a result, and are
    not part of it."""
    return "subst:" + _digest(
        "check-substance/1", run, root, f"{timeout:.0f}",
        json.dumps([[i["name"], i["source"], i["required"]] for i in inputs]),
        json.dumps(list(env)))[:16]


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


def _check(raw: dict, steps: dict, *, source: str, scale: float,
           selections: dict | None = None) -> dict:
    """One validated check, as the manifest carries it and as `verify`
    executes it. `argv` and every scope's arguments are already split: the
    runner never parses a string, which is what keeps a command line from
    being something a model could compose.

    `inputs` carries each declared input's *selection* — its name, where it
    is selected from and whether it is required — and never its content:
    the runner reads, hashes and hands the file over at the moment it
    runs, and the evidence records what was handed over."""
    parts = shlex.split(str(raw.get("run")))
    scopes = {str(k): [str(t) for t in v]
              for k, v in (raw.get("scopes") or {}).items()}
    prepare = [str(s) for s in (raw.get("prepare") or [])]
    root = str(raw.get("root") or "worktree")
    timeout = min(float(raw["timeout"]) if raw.get("timeout") is not None
                  else scale, scale)
    inputs = []
    for word in raw.get("inputs") or []:
        selection = (selections or {}).get(str(word).strip()) or {}
        inputs.append({"name": str(word).strip(),
                       "source": str(selection.get("path") or "").strip(),
                       "required": selection.get("required", True) is not False})
    env = [str(n) for n in (raw.get("env") or [])]
    revision = _check_revision(str(raw.get("name")).strip(), shlex.join(parts),
                               root, scopes, prepare, timeout,
                               [[i["name"], i["source"], i["required"]]
                                for i in inputs], env)
    return {
        "inputs": inputs,
        "env": env,
        "checkSubstance": check_substance(shlex.join(parts), root, timeout,
                                          inputs, env),
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


def document_steps(doc: dict) -> tuple[dict, list[tuple[str, str]]]:
    """The document's preparation steps, by name, and why any of them
    cannot be used. A step that does not validate is a blocker rather than
    a step quietly left out: the check that names it would otherwise run
    unprepared and fail for the wrong reason."""
    steps: dict[str, dict] = {}
    trouble: list[tuple[str, str]] = []
    if doc["kind"] != "file":
        return steps, trouble
    raw = doc.get("prepare")
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            trouble.append(("prepare-invalid",
                            f"a preparation step is not an object: {entry!r}"))
            continue
        problem = _step_problem(entry)
        if problem:
            trouble.append(problem)
            continue
        name = str(entry["name"]).strip()
        if name in steps:
            trouble.append(("prepare-duplicate",
                            f"the preparation step '{name}' is declared twice "
                            f"— a step's name is its identity"))
            continue
        steps[name] = _step(entry, source=doc["identity"] or "the document")
    return steps, trouble


def promoted_checks(grants: list[dict],
                    declared: dict) -> tuple[dict, list[tuple[str, str]]]:
    """This intent's command grants, as checks bench could run itself.

    The documented compatibility path. A grant and a check are the same
    shape — one executable, fixed arguments, a named root — so a `verify`
    entry that names a grant resolves to a check built from it rather than
    refusing. Two things still refuse, because they are the two a check is
    held to and a grant is not: a root a check may not use (the
    application checkout), and a command line that reshapes the repository
    instead of reading it.

    A name the document declares as a check wins: an explicit definition
    is never shadowed by a grant that happens to share its name.
    """
    out: dict = {}
    trouble: list[tuple[str, str]] = []
    for grant in grants:
        name = grant["name"]
        if name in declared or name == VERIFY_GRANT:
            continue
        raw = {"name": name, "run": grant["run"], "root": grant["root"],
               "help": grant["help"]}
        problem = _check_problem(raw, set())
        if problem:
            # Not a blocker on its own: a grant that cannot be a check is
            # perfectly fine as a grant, and only an intent that actually
            # asks for it by name has a problem. `_named` says so there.
            out[name] = {"name": name, "status": "unavailable",
                         "why": problem[1], "code": problem[0]}
            continue
        out[name] = _check(raw, {}, source=f"{grant['source']} (a command "
                                           f"grant, promoted)",
                           scale=config.VERIFY_TIMEOUT)
    return out, trouble


def _step_from_grant(check: dict) -> dict:
    """The same promotion for a preparation step — a grant named in an
    intent's `prepare` list runs before its checks."""
    if check.get("status") != "available":
        return check
    return {
        "name": check["name"], "run": check["run"],
        "executable": check["executable"], "argv": list(check["argv"]),
        "required": True, "network": False, "networkEnforcement": None,
        "help": check["help"], "source": check["source"],
        "status": "available", "enforcement": "enforced",
    }


def document_checks(doc: dict, steps: dict) -> tuple[dict, list[tuple[str, str]]]:
    """The document's named checks, by name, and why any of them cannot be
    offered. Resolved once per document rather than once per intent, so
    two intents asking for `unit-tests` are asking for the same thing."""
    checks: dict[str, dict] = {}
    trouble: list[tuple[str, str]] = []
    if doc["kind"] != "file":
        return checks, trouble
    raw = doc.get("checks")
    selections = context_selections(doc)
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            trouble.append(("check-invalid",
                            f"a check is not an object: {entry!r}"))
            continue
        problem = _check_problem(entry, set(steps), selections)
        if problem:
            trouble.append(problem)
            continue
        name = str(entry["name"]).strip()
        if name in checks:
            trouble.append(("check-duplicate",
                            f"the check '{name}' is declared twice — a check's "
                            f"name is what a run asks for, and two commands "
                            f"cannot share one"))
            continue
        checks[name] = _check(entry, steps,
                              source=doc["identity"] or "the document",
                              scale=config.VERIFY_TIMEOUT,
                              selections=selections)
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
        result = config.git_run(path.parent, "log", "-1", "--format=%H", "--",
                                path.name, timeout=10)
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
    if word == "review":
        # The reviewed code is the *subject* of a review, not an approved
        # input to it. Pinning a file out of it would also claim a
        # guarantee the ordering cannot make: inputs are hashed and copied
        # before the checkout the path would be in exists.
        return (f"the context input '{name}' is in the review root, which "
                f"holds the code under review rather than anything approved",
                "name a file under tool, work or app — the reviewed "
                "revision reaches the run as its working directory, not as "
                "a pinned input")
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
        "writable": [("worktree", "the card's own worktree — where the run is "
                                  "meant to edit and commit. Intended, not "
                                  "confined: nothing at the OS level stops a "
                                  "process writing elsewhere")],
        "publication": {"commit": True, "push": False, "publish": False},
        "deny": ["push", "publish", "force-push", "rebase", "reshape-lifecycle"],
        "why": "implement, check and commit inside the worktree bench prepared",
    },
    "act-pr": {
        "cwd": "worktree",
        "writable": [("worktree", "the card's own worktree — where the run is "
                                  "meant to commit and push so the PR updates. "
                                  "Intended, not confined: nothing at the OS "
                                  "level stops a process writing elsewhere")],
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
    "refine": {
        "cwd": "app",
        "writable": [],
        "publication": {"commit": False, "push": False, "publish": False},
        "deny": ["edit", "shell", "commit", "push", "publish", "force-push",
                 "rebase", "reshape-lifecycle"],
        "why": ("read the card and the code, and propose a new brief; bench "
                "validates the proposal and a person applies or discards it"),
    },
}

# What each denied word means, so a refusal and a UI row read alike and an
# adapter has something to render. Narrowing only: nothing here is a
# grant.
DENIALS = {
    "edit": "the vendor's file-editing tools are withheld",
    "shell": "the vendor's shell tool is withheld — no command may be run",
    "commit": "no commit may be made",
    "push": "nothing may be pushed",
    "publish": "nothing may be published — no PR opened, merged or released",
    "force-push": "history may never be rewritten under a public branch",
    "rebase": "history may never be rewritten under a public branch",
    "reshape-lifecycle": ("the branch, worktree and base bench planned may not "
                          "be changed by the run"),
}


def _isolation(table: dict) -> dict:
    """Whether anything holds a run's writes inside its intended scope.

    Read off the same capability table `requires` is checked against, so
    the summary and the refusal can never disagree: `filesystem-sandbox`
    enforced by something installed would make the intended scope a
    boundary, and nothing shipped enforces it.
    """
    entry = table.get("filesystem-sandbox") or {}
    enforced = entry.get("enforcement") == "enforced"
    return {
        "enforced": enforced,
        "by": entry.get("by"),
        "why": ("filesystem-sandbox is enforced here, so writes are held "
                "inside the intended scope"
                if enforced else
                "not enforced — filesystem-sandbox is not supported by this "
                "adapter or backend, so the writable scope is where the run "
                "is meant to write, not a boundary it is held inside. Command "
                "rules narrow what it may launch; they do not confine a "
                "process"),
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


def resolve(intent: str, *, worktree: Path | str | None = None,
            review: Path | str | None = None,
            policy: dict | None = None,
            agent_values: dict[str, str] | None = None) -> dict:
    """One intent's effective policy, with nothing written.

    `agent_values` is a draft of `manager/local/.env`'s agent keys, for
    Settings → Agents previewing a change through this same resolver
    (task 77). None reads the file as it is now, which every launch does.

    Everything in the returned document is either a fact about this board
    or a validated value from the profile document; `blockers` is the
    whole answer to "may a launch start" and empty means yes. The UI and
    the launch both read this function, which is the point — a page that
    parsed the settings a second time could disagree with the run.

    `policy` is a document already read by `document_at()`, for the one
    caller that resolves something other than this board's: the doctor,
    putting a deliberately broken fixture through this resolver so that
    the diagnostic an operator will see is the diagnostic bench actually
    produces. Default None reads the board's own, which is every other
    caller.
    """
    if intent not in INTENTS:
        raise ValueError(f"unknown launch intent '{intent}' — one of "
                         f"{', '.join(INTENTS)}")
    baseline = BASELINE[intent]
    doc = policy if policy is not None else document()
    # Which harness and model this intent rides, resolved once and carried
    # in the result — so the page, the preflight, the doctor and `_launch`
    # read one answer rather than each asking `config` again (task 77).
    choice = config.agent_choice(intent, agent_values)
    adapter = adapter_identity(choice["harness"]["value"])
    table = capability_table(adapter)
    available = roots(worktree, review)

    notes: list[str] = []
    # A PR review runs in the checkout bench pinned for it, not in the
    # application. That is this card's whole correction to the baseline:
    # the live checkout is where a person's editor, a driver and every
    # other worktree's git metadata are, and a review that executes
    # proposed code must not be started in it. With nothing pinned — a
    # relevance check — the word is absent and the baseline stands.
    cwd_word = baseline["cwd"]
    if intent == "review" and available.get("review"):
        cwd_word = "review"
        notes.append("this review runs in the checkout bench pinned for it, "
                     "detached at the reviewed revision, rather than in the "
                     "application checkout")
    cwd_path = available.get(cwd_word) or (str(worktree) if worktree else None)

    blockers = _Trouble()
    configured: list[str] = []
    prompt_only: list[str] = []

    declared = doc["profiles"].get(intent) if doc["kind"] == "file" else None
    if doc["problem"]:
        blockers.add("policy-unreadable", doc["problem"])
    if adapter.get("note"):
        notes.append(adapter["note"])

    # — whether the installed adapter can launch this intent at all —
    #
    # An adapter that has never heard of an intent would read AGENT_MODE
    # it does not recognise and fall into whatever its default stance is —
    # for a shell script written around `work`, that is implementation's.
    # So an intent added after an adapter was written is refused unless the
    # adapter lists it, and a refinement additionally needs the two
    # restrictions that make it read-only to be the vendor's, not a
    # sentence: no edit tool, no shell.
    restricted = intent == "refine"
    if not adapter.get("path"):
        # Before anything else about the adapter: a harness that is not
        # installed refuses here, in the reading every launch takes before
        # its first side effect, rather than after a worktree or a review
        # checkout already exists (task 77).
        blockers.add("harness-unavailable",
                     f"the '{adapter['name']}' harness chosen for '{intent}' "
                     f"({choice['harness']['setting']}, from "
                     f"{_SOURCE_WORDS.get(choice['harness']['source'], choice['harness']['source'])}) "
                     f"is not installed — there is no "
                     f"manager/local/adapters/{adapter['name']}/run or "
                     f"manager/core/adapters/{adapter['name']}/run")
    elif not intent_supported(adapter, intent):
        blockers.add("intent-unsupported",
                     f"the {adapter['name']} adapter does not list '{intent}' "
                     f"among the intents it can launch, so the launch is "
                     f"refused rather than handed to it to run under whatever "
                     f"stance it defaults to")
    elif restricted:
        for need in REFINE_REQUIRES:
            if (table.get(need) or {}).get("enforcement") != "enforced":
                blockers.add("intent-unsupported",
                             f"a refinement needs '{need}' enforced by the "
                             f"adapter, and the {adapter['name']} adapter does "
                             f"not declare it — refused rather than run with a "
                             f"restriction that exists only in the prompt")
    if restricted and isinstance(declared, dict):
        for key in ("commands", "verify", "prepare"):
            if declared.get(key):
                blockers.add("intent-restricted",
                             f"'refine' declares \"{key}\", and a refinement "
                             f"runs no command and asks for no check — it reads, "
                             f"proposes, and bench writes")
    if restricted and doc["kind"] == "file" and (doc.get("commands")
                                                 or doc.get("verify")):
        notes.append("the document's shared commands and checks do not apply "
                     "to 'refine': it is never granted a command")

    # — command grants —
    grants: list[dict] = []
    if restricted:
        pass
    elif doc["kind"] == "file" and isinstance(declared, dict):
        raw_grants = declared.get("commands")
        if raw_grants is None:
            raw_grants = doc.get("commands") or []
            if raw_grants:
                notes.append(f"'{intent}' names no commands of its own, so the "
                             f"document's shared list applies to it")
        seen: set[str] = set()
        for raw in raw_grants if isinstance(raw_grants, list) else []:
            if not isinstance(raw, dict):
                blockers.add("grant-invalid",
                             f"a command grant for '{intent}' is not an "
                             f"object: {raw!r}")
                continue
            problem = _grant_problem(raw, available)
            if problem:
                blockers.add(*problem)
                continue
            grant = _grant(raw, source=doc["identity"] or "the profile document",
                           available=available)
            if grant["name"] in seen:
                blockers.add("grant-duplicate",
                             f"the command grant '{grant['name']}' is "
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
            blockers.add("context-duplicate",
                         f"the context input '{name}' is declared twice for "
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
            # Two codes, because they are two different things to go and
            # do: a selection bench refused before reading anything is an
            # authoring mistake, and a file that is simply not there is an
            # operator's missing input.
            blockers.add(
                "context-refused" if item["status"] == "refused"
                else "context-unreadable",
                f"{item['why']}. {item['remedy']}", item["remedy"])
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
    blockers.add_all("prepare-invalid", step_trouble)
    available_checks, check_trouble = document_checks(doc, steps)
    blockers.add_all("check-invalid", check_trouble)

    # The compatibility path, and it is the whole of it: a `verify` or
    # `prepare` entry naming one of this intent's own **command grants**
    # resolves to a check built from that grant. A grant is already one
    # executable and fixed arguments validated exactly as a check is, so
    # the promotion adds nothing and refuses the two things a check
    # refuses extra — a root a check may not use, and a command line that
    # reshapes the repository. A document written before bench could run
    # anything therefore keeps working, and its declared requirement
    # becomes a real run rather than staying a note.
    promotable, promotion_trouble = promoted_checks(grants, available_checks)
    blockers.add_all("check-invalid", promotion_trouble)
    available_checks = {**available_checks, **promotable}
    steps = {**steps, **{n: _step_from_grant(c) for n, c in promotable.items()}}

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
                blockers.add("name-invalid",
                             f"'{intent}' names the {what} {name!r} — "
                             f"names match [a-z0-9][a-z0-9._-]*")
                continue
            if name in seen:
                continue
            seen.add(name)
            if name not in known:
                blockers.add(
                    "check-undeclared" if what == "check" else "prepare-undeclared",
                    f"'{intent}' asks for the {what} '{name}', which this "
                    f"document declares neither as a {what} nor as one of this "
                    f"intent's command grants. Declare it in the top-level "
                    f"\"{'checks' if what == 'check' else 'prepare'}\" list — "
                    f"bench will not run something it cannot read, and a "
                    f"requirement naming nothing is how a board reports a "
                    f"check that never ran as one that passed")
                continue
            if known[name].get("status") != "available":
                blockers.add(
                    "check-unrunnable" if what == "check" else "prepare-invalid",
                    f"'{intent}' asks for the {what} '{name}', and the command "
                    f"grant of that name cannot be run as one: "
                    f"{known[name].get('why')}")
                continue
            # A check declared against a root this launch does not have.
            # Not a blocker: see ROOT_SCOPED_CHECKS. The run simply may
            # not ask for it, so it is absent from what it is offered and
            # `verify` refuses the name by listing what it may ask for.
            needed = ROOT_SCOPED_CHECKS.get(known[name].get("root") or "")
            if needed and not available.get(needed):
                notes.append(
                    f"the check '{name}' runs in the {needed} root, which this "
                    f"'{intent}' launch has none of, so it is not offered to "
                    f"it — a PR review is pinned a checkout of the reviewed "
                    f"head and a relevance check is pinned nothing")
                continue
            out.append(known[name])
        return out

    verify = [] if restricted else _named("verify", available_checks, "check")
    prepare = [] if restricted else _named("prepare", steps, "preparation step")
    if verify:
        configured.append("named-verification")
        configured.append("verification-evidence")
    if prepare or any(c["prepare"] for c in verify):
        configured.append("prepared-inputs")
    if verify:
        configured.append("isolated-environment")
    # A gate is a property of the board rather than of this intent, so it
    # is read shallowly from the same document and never re-validated
    # here: `gate_policy()` is the one authority on whether a gate block
    # holds together, and a resolution of `work` must not grow a second
    # opinion about it. What this adds is the honest guarantee word —
    # `verified-outputs` is enforced on a board that gates something, and
    # absent on one that gates nothing.
    if _gates_something(doc):
        configured.append("verified-outputs")
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
            blockers.add(*problem)
        elif any(g["name"] == VERIFY_GRANT for g in grants):
            blockers.add("grant-reserved",
                         f"a command grant is named '{VERIFY_GRANT}', which "
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
            blockers.add("deny-unknown",
                         f"'{intent}' denies the unknown capability "
                         f"{word!r} — one of {', '.join(sorted(DENIALS))}")
            continue
        if word == "shell" and word not in deny_words:
            # Withholding the shell is a launch stance the adapters spell
            # per intent, not a rule they can add to any intent — so asking
            # for it where the stance does not exist is refused rather
            # than shown as a denial nothing carries out.
            blockers.add("deny-unknown",
                         f"'{intent}' denies 'shell', which the adapters "
                         f"only withhold for a refinement — a work, act-pr "
                         f"or review run cannot be launched without one here")
            continue
        if word not in deny_words:
            deny_words.append(word)
    denied = [{"capability": word, "why": DENIALS[word]} for word in deny_words]
    if adapter["capabilities"].get("command-denylist"):
        configured.append("command-denylist")
    if restricted:
        configured += list(REFINE_REQUIRES)

    # — publication, kept apart from the checks —
    publication = dict(baseline["publication"])
    declared_pub = declared.get("publication") if isinstance(declared, dict) else None
    for key in ("commit", "push", "publish"):
        if isinstance(declared_pub, dict) and key in declared_pub:
            asked = bool(declared_pub[key])
            if asked and not publication[key]:
                blockers.add(
                    "publication-widened",
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
            blockers.add("requirement-unsupported", problem)

    configured.append("working-directory")
    if choice["model"]["value"]:
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
        # the harness and model selection, with where each came from. Not
        # part of `revision`: which model rides a launch is not a
        # permission, and changing it must not make evidence read stale
        "selection": agent_selection(choice),
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
        # where the run is *meant* to write — intended scope, said as such,
        # because the only thing that could make it a boundary is the
        # `filesystem-sandbox` capability and `isolation` says whether
        # anything here enforces that
        "writable": [{"root": word, "path": available.get(word), "why": why,
                      "enforcement": "intended"}
                     for word, why in baseline["writable"]
                     if available.get(word)],
        "isolation": _isolation(table),
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
        "blockers": list(blockers),
        # the same refusals, each with the code it came from and a remedy
        # for the class of it. Added beside `blockers` rather than
        # replacing them, so every existing consumer reads exactly what it
        # read before and the doctor can switch on a code instead of
        # matching on a sentence.
        "diagnostics": blockers.coded,
    }


# ── the manifest one run rode ──────────────────────────────────────────


def manifest(intent: str, filename: str, stage: str, *,
             worktree: Path | str | None = None,
             review: Path | str | None = None,
             review_record: dict | None = None,
             pin_inputs: bool = False) -> dict:
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

    `review` is the pinned PR-review checkout, and `review_record` is what
    bench recorded about it — the repository, the base and head identities
    and whether the pull request is still the current one. Both are the
    caller's to supply, because provisioning a checkout is a side effect
    and resolving a profile is not: the UI's reading of a review profile
    asks with the path the launch *would* pin and nothing on disk.
    """
    if worktree is None and intent not in READ_ONLY_INTENTS:
        worktree = config.WORKTREES / filename[:-3]
    found = resolve(intent, worktree=worktree, review=review)
    found.update({
        "task": filename,
        "stage": stage,
        "requestId": request_id(intent, filename),
        "resolved": time.time(),
    })
    if review_record is not None:
        # Carried with the run for the whole of its life: `verify` reads
        # which checkout a `review` check runs in off this, and the Focus
        # view reads what the verdict was about off the same record rather
        # than asking GitHub what the head is now.
        found["review"] = review_record
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
                          worktree=(frozen.get("roots") or {}).get("worktree"),
                          review=(frozen.get("roots") or {}).get("review"))
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
    frozen_agent = frozen.get("selection") or {}
    if frozen_agent and frozen_agent != current.get("selection"):
        changed.append("the harness or model this intent rides has been "
                       "changed since the run was recorded; the run keeps "
                       "the one it started with")
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
          "adapter", "selection", "template", "capabilities", "cwd", "roots", "commands", "uiCommands",
          "context", "bundle", "prepare", "verify", "denied", "publication",
          "writable", "isolation", "requires", "guarantees", "notes",
          "blockers",
          # codes and remedies bench wrote — never anything read off disk
          "diagnostics",
          "task",
          "stage", "requestId", "resolved", "recorded", "agent", "review")

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
        # the codes alone, so a compact row can say *which* diagnostics
        # without carrying their prose
        "codes": [d["code"] for d in found.get("diagnostics") or []],
        "pending": list((found.get("guarantees") or {}).get("pending") or []),
        # where this run actually works, which for a PR review is bench's
        # own pinned checkout rather than the application
        "cwd": (found.get("cwd") or {}).get("policy"),
        "reviewId": (found.get("review") or {}).get("reviewId"),
        # which harness and model, so a run row says what rode it
        "harness": ((found.get("selection") or {}).get("harness") or {}).get("name"),
        "model": ((found.get("selection") or {}).get("model") or {}).get("value"),
        "template": found.get("template"),
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


def verification_block(found: dict | None) -> str:
    """What the run is told about the checks bench will run for it.

    Empty string when the profile declares none, so a board that
    configured nothing renders the prompt it always did, byte for byte.

    Where there *are* checks, the wording is deliberate on one point: the
    run is told that the evidence is already on the board, so reporting a
    pass the shim did not print is a claim the record contradicts. That is
    prose, and prose persuades rather than enforces — which is exactly why
    the enforcement lives in the runner and not here.
    """
    if not found:
        return ""
    checks = found.get("verify") or []
    if not checks:
        return ""
    shim = str(verify_shim())
    lines = [
        "This board runs the project's checks itself. You do not run them "
        "and you are not asked to vouch for them: you ask for one by name, "
        "bench builds the command line from the operator's own policy, runs "
        "it, and records the outcome — the exit status of its own child "
        "process — before telling you anything.",
        "",
        f"    {shim}                 list the checks you may ask for",
        f"    {shim} <check>         run one",
        f"    {shim} <check> <scope> run one over a declared scope",
        "",
        "Where an equal, current pass of the same check is already on record "
        "for this card at this commit, bench returns that record instead of "
        "running it again: the output says REUSED and names the original "
        "evidence. `--again` before the check's name asks for a new run "
        "regardless.",
        "",
        "Available to this run:",
    ]
    for check in checks:
        scopes = ", ".join(sorted(check["scopes"]))
        lines.append(f"- `{check['name']}` — runs `{check['run']}` in the "
                     f"{check['root']} root"
                     + (f"; scopes: {scopes}" if scopes else "")
                     + (f". {check['help']}" if check["help"] else ""))
        for step in check["prepare"]:
            lines.append(f"  - prepared first by `{step['name']}`, which bench "
                         f"also runs")
    lines += [
        "",
        "Its exit status says which kind of answer came back: 0 passed, 1 the "
        "check itself said no, 2 refused, 3 preparation failed, 4 no verdict "
        "was reached. Only 1 is a test failure — treat the rest as what they "
        "are rather than as a failing suite.",
        "",
        "The evidence is already on the board by the time you read the "
        "output, so your report should say what it said. Reporting a check as "
        "passing when the runner printed otherwise is a claim the record "
        "contradicts, and a reviewer reads the record.",
    ]
    # And, where this board actually gates a merge on one of them, the
    # one operational consequence a run has to know: the evidence is
    # bound to the commit. A check asked for over uncommitted work was
    # not about the commit that merges, so it satisfies nothing — which
    # makes "commit, then ask" the order rather than a preference.
    if _gates_something(document()):
        lines += [
            "",
            "This board gates its merges on those checks, and a result is "
            "bound to the commit it ran against: evidence from another "
            "commit, or from a checkout with uncommitted work in it, "
            "satisfies nothing. So **commit your work first and ask for the "
            "check afterwards**, and if you commit again, ask again. "
            "Nothing merges on a report, an exit code, or new commits.",
        ]
        lines += _required_lines(found, checks)
    return "\n".join(lines)


# The intents that commit, and so end with a commit the merge rules will
# read. A review run may still ask for what its profile grants (design
# #94 §10.1 — review-first stays possible, and an equal current pass is
# reused), but it is not told to cover the merge rules: it changes no
# commit.
_COMMITTING_INTENTS = ("work", "act-pr")


def _required_lines(found: dict, checks: list[dict]) -> list[str]:
    """Design #94 §10.1: a committing run asks for every required check it
    is authorised to ask for, after its last commit. What it may ask for is
    still its frozen profile — this names checks, it grants none — and a
    requirement it cannot ask for is said to be a person's or the
    configuration's, never something that will happen by itself."""
    if found.get("intent") not in _COMMITTING_INTENTS:
        return []
    try:
        policy = gate_policy()
    except (ValueError, OSError):
        return []
    if policy.get("blockers"):
        return []
    wanted: list[str] = []
    for scope in GATE_SCOPES:
        for check in (policy["scopes"].get(scope) or {}).get("checks") or []:
            label = check["name"] + (f" {check['gateScope']}"
                                     if check.get("gateScope") else "")
            if label not in wanted:
                wanted.append(label)
    if not wanted:
        return []
    mine = {c["name"] for c in checks}
    asked = [w for w in wanted if w.split(" ")[0] in mine]
    missing = [w for w in wanted if w.split(" ")[0] not in mine]
    out = [""]
    if asked:
        out.append(
            "Ask for every required check after your last commit; a pass "
            "before a later commit does not count. The merge rules here "
            "require: " + ", ".join(f"`{w}`" for w in asked) + " (run each "
            "as `bench-verify <check> [<scope>]`). Launching you was the "
            "person's consent to this run; these checks are the ones your "
            "own profile already lets you ask for, and asking grants "
            "nothing new.")
    if missing:
        out.append(
            "The merge rules also require "
            + ", ".join(f"`{w}`" for w in missing)
            + ", which this run may not ask for. Do not try to run it "
            "another way. Say so in your report: a person runs it from the "
            "card (Run required checks), or the configuration changes.")
    out.append(
        "Nothing runs these checks on its own: a merge rule requires them, "
        "it does not schedule them. If you do not ask, they stay not run "
        "until someone does.")
    return out


# ── gate requirements: what an integration refuses without ─────────────
#
# A `verify` list says what a *run* may ask bench to run. This says what a
# *mutation* insists on having found: the automatic merge of a phase
# member into its integration branch, the acceptance of a phase's own
# integration head, and the merge-and-clean-up of an ordinary card.
#
# It is read here, in the policy layer, for the reason every other
# document value is: these names end up deciding whether an irreversible
# git operation runs, and a requirement that resolves to nothing is
# indistinguishable from no requirement at all. `gates.py` evaluates what
# this returns against recorded evidence. It has no second parser.

# One scope per guarded mutation, each named for the thing it guards
# rather than for the code that calls it.
GATE_SCOPES = {
    "member": ("a phase merging one member's branch into its integration "
               "branch — the automatic step no person clicks"),
    "integration": ("a phase's own integration head being accepted — the "
                    "head every member was merged into, which is a "
                    "different revision from any of them"),
    "acceptance": ("an ordinary card being merged and cleaned up — the "
                   "one place work leaves the board"),
}

# What a gate may insist on about review, and what each word actually
# means. `none` is the default and gates nothing, which is what keeps a
# board that declared no gates behaving exactly as it did.
#
# The distinction the last three exist for: GitHub's formal review state
# and a Bench verdict are two different records of two different things,
# and a COMMENTED review is neither of them. A board whose only reviewer
# is the account that opened the pull request cannot produce a formal
# APPROVED at all — GitHub refuses it — so a policy that needs one says
# so and is reported as unmet rather than satisfied by the comment the
# attempt fell back to.
REVIEW_POLICIES = {
    "none": "no review is required by this gate",
    "bench": ("a Bench review verdict of APPROVE, recorded by bench itself "
              "against this exact head. Independent of GitHub: it is bench's "
              "own record of what its reviewer found"),
    "github": ("a formal GitHub review state of APPROVED on the pull "
               "request at this head, and no reviewer's change request still "
               "outstanding at any commit. A COMMENTED review is not an "
               "approval, however it is worded. This is bench's reading of "
               "the review objects, not GitHub's branch protection, which "
               "GitHub applies separately"),
    "separate-reviewer": ("a formal GitHub APPROVED authored by somebody "
                          "other than the account that proposed the head, "
                          "and no reviewer's change request still "
                          "outstanding. Bench never switches accounts or asks "
                          "for a second credential to satisfy this — it "
                          "reports it unmet. Not GitHub's branch protection, "
                          "which GitHub applies separately"),
}

GATE_POLICY_VERSION = 1


def _gates_something(doc: dict) -> bool:
    """Does this document gate anything at all? A shallow read, used only
    to decide whether `verified-outputs` is a guarantee this board makes.
    It validates nothing — `gate_policy()` is the only reader that does,
    and two validators would be the second parser this design refuses."""
    raw = doc.get("gates") if doc.get("kind") == "file" else None
    if not isinstance(raw, dict):
        return False
    for scope in GATE_SCOPES:
        entry = raw.get(scope)
        if not isinstance(entry, dict):
            continue
        if entry.get("checks") or str(entry.get("review") or "none") != "none":
            return True
    return False


def gate_policy(doc: dict | None = None) -> dict:
    """What this board's gates require, per scope — validated, with every
    refusal carrying its code.

    Three answers, and each is a real one:

    - **nothing declared** (`configured: False`) — a board with no
      `"gates"` block, which is every board that existed before this.
      Each scope resolves to no checks and `review: "none"`, and
      `gates.py` reports that as *ungated* in those words rather than as
      a pass.
    - **declared** — the scopes the document named, each with its checks
      resolved to the same definitions `verify` resolves to, so a gate
      and a run cannot disagree about what `unit` is.
    - **broken** — a document that does not parse or declares a schema
      this bench does not read. That is a blocker on every scope, the
      same way it is a blocker on every intent: an operator mid-edit must
      not have their gates silently become none.
    """
    doc = document() if doc is None else doc
    trouble = _Trouble()
    if doc.get("problem"):
        trouble.add("policy-unreadable", doc["problem"])
    steps, step_trouble = document_steps(doc)
    trouble.add_all("prepare-invalid", step_trouble)
    known, check_trouble = document_checks(doc, steps)
    trouble.add_all("check-invalid", check_trouble)

    raw = doc.get("gates") if doc.get("kind") == "file" else None
    declared = raw if isinstance(raw, dict) else {}
    if raw is not None and not isinstance(raw, dict):
        trouble.add("gate-invalid",
                    f"the document's \"gates\" is {type(raw).__name__}, and a "
                    f"gate block is an object keyed by scope — one of "
                    f"{', '.join(sorted(GATE_SCOPES))}")

    allow_overrides = False
    overrides = {"allowed": False, "actors": [],
                 "why": "no override path is enabled on this board, so an "
                        "unmet gate is unmet"}
    for key in declared:
        if key in GATE_SCOPES or key == "overrides":
            continue
        trouble.add("gate-scope-unknown",
                    f"the \"gates\" block names {key!r}, which is not a gate "
                    f"scope — one of {', '.join(sorted(GATE_SCOPES))}")

    raw_over = declared.get("overrides")
    if raw_over is not None:
        if not isinstance(raw_over, dict):
            trouble.add("gate-invalid",
                        f"\"gates.overrides\" is {type(raw_over).__name__} — it "
                        f"is an object with \"allow\" and an optional "
                        f"\"actors\" list")
        else:
            allow = raw_over.get("allow", False)
            if not isinstance(allow, bool):
                trouble.add("gate-invalid",
                            f"\"gates.overrides.allow\" is {allow!r} — it is "
                            f"true or false, because it decides whether a "
                            f"person may proceed over an unmet gate at all")
            actors = raw_over.get("actors")
            names: list[str] = []
            if actors is not None and not isinstance(actors, list):
                trouble.add("gate-invalid",
                            "\"gates.overrides.actors\" is not a list — it "
                            "names the git identities allowed to override")
            else:
                names = [str(a).strip() for a in (actors or []) if str(a).strip()]
            allow_overrides = bool(allow)
            overrides = {
                "allowed": allow_overrides,
                "actors": names,
                "why": ("an authorised person may proceed over an unmet gate, "
                        "and doing so is recorded against them — it never "
                        "makes the gate met"
                        if allow else
                        "the document turns the override path off, so an unmet "
                        "gate is unmet"),
            }

    scopes: dict = {}
    for scope, why in GATE_SCOPES.items():
        entry = declared.get(scope)
        checks: list[dict] = []
        review = "none"
        if entry is not None and not isinstance(entry, dict):
            trouble.add("gate-invalid",
                        f"the '{scope}' gate is {type(entry).__name__} — a "
                        f"gate is an object with \"checks\" and \"review\"")
            entry = None
        if isinstance(entry, dict):
            for key in entry:
                if key not in ("checks", "review"):
                    trouble.add("gate-invalid",
                                f"the '{scope}' gate declares {key!r}, which "
                                f"a gate has no meaning for — it takes "
                                f"\"checks\" and \"review\"")
            raw_checks = entry.get("checks")
            if raw_checks is not None and not isinstance(raw_checks, list):
                trouble.add("gate-invalid",
                            f"the '{scope}' gate's \"checks\" is not a list of "
                            f"declared check names")
                raw_checks = []
            seen: set[tuple] = set()
            for raw_req in raw_checks or []:
                # A requirement is a check and the coverage it needs. A bare
                # name is the legacy spelling and means the *unscoped full
                # run* — a scoped run of the same check is a different
                # argument list, and a narrow pass must never satisfy a
                # requirement written as the whole check.
                wanted_scope = None
                if isinstance(raw_req, dict):
                    extra = [k for k in raw_req if k not in ("check", "scope")]
                    if extra:
                        trouble.add("gate-invalid",
                                    f"the '{scope}' gate has a requirement "
                                    f"declaring {extra[0]!r} — a requirement "
                                    f"object takes \"check\" and an optional "
                                    f"\"scope\"")
                        continue
                    name = str(raw_req.get("check") or "").strip()
                    if raw_req.get("scope") is not None:
                        wanted_scope = str(raw_req["scope"]).strip()
                else:
                    name = str(raw_req).strip()
                if not NAME_RE.match(name):
                    trouble.add("name-invalid",
                                f"the '{scope}' gate requires the check "
                                f"{name!r} — names match [a-z0-9][a-z0-9._-]*")
                    continue
                if wanted_scope is not None and not NAME_RE.match(wanted_scope):
                    trouble.add("name-invalid",
                                f"the '{scope}' gate requires the scope "
                                f"{wanted_scope!r} of '{name}' — names match "
                                f"[a-z0-9][a-z0-9._-]*")
                    continue
                if (name, wanted_scope) in seen:
                    continue
                seen.add((name, wanted_scope))
                found = known.get(name)
                if found is None:
                    trouble.add(
                        "gate-check-undeclared",
                        f"the '{scope}' gate requires the check '{name}', "
                        f"which this document declares nowhere. A gate reads "
                        f"evidence filed under a check's own name, so a name "
                        f"nothing declares could never be satisfied — and a "
                        f"requirement that can never be met is refused here "
                        f"rather than blocking every merge later")
                    continue
                if found.get("status") != "available":
                    trouble.add("check-unrunnable",
                                f"the '{scope}' gate requires the check "
                                f"'{name}', which cannot be run as one: "
                                f"{found.get('why')}")
                    continue
                if wanted_scope is not None and \
                        wanted_scope not in (found.get("scopes") or {}):
                    offered = ", ".join(sorted(found.get("scopes") or {})) \
                        or "none"
                    trouble.add(
                        "gate-scope-undeclared",
                        f"the '{scope}' gate requires the scope "
                        f"'{wanted_scope}' of the check '{name}', which that "
                        f"check does not declare (it offers: {offered}). "
                        f"Evidence is filed under the scope a run asked for, "
                        f"so a scope nothing declares could never be met")
                    continue
                checks.append({
                    **found, "gateScope": wanted_scope,
                    "requirement": (f"{name}:{wanted_scope}"
                                    if wanted_scope else name)})
            raw_review = entry.get("review")
            if raw_review is not None:
                word = str(raw_review).strip()
                if word not in REVIEW_POLICIES:
                    trouble.add(
                        "gate-review-unknown",
                        f"the '{scope}' gate asks for the review policy "
                        f"{word!r} — one of "
                        f"{', '.join(sorted(REVIEW_POLICIES))}")
                else:
                    review = word
        scopes[scope] = {
            "scope": scope, "why": why,
            "declared": isinstance(entry, dict),
            "checks": checks,
            "review": review,
            "reviewWhy": REVIEW_POLICIES[review],
            # A gate with nothing to insist on is ungated, and the word is
            # the one the board shows. It is not a pass.
            "gated": bool(checks) or review != "none",
        }

    # Resolved last, because a document with *any* refusal in it is a
    # document whose author is mid-edit: handing them an override path out
    # of a policy that does not parse would be the one way a broken gate
    # block could let a merge through.
    overrides["allowed"] = allow_overrides and not trouble
    if allow_overrides and trouble:
        overrides["why"] = ("the override path is declared, and it is not "
                            "offered while this document has a refusal in it "
                            "— fix the policy first")

    revision = _digest(
        str(GATE_POLICY_VERSION), doc.get("revision") or "",
        json.dumps({s: {"checks": [[c["revision"], c["gateScope"]]
                                   if c["gateScope"] else c["revision"]
                                   for c in v["checks"]],
                        "review": v["review"]}
                    for s, v in scopes.items()}, sort_keys=True),
        json.dumps(overrides, sort_keys=True))
    return {
        "v": GATE_POLICY_VERSION,
        "configured": bool(declared) and not trouble,
        "revision": revision,
        "policyId": f"gates:{revision[:12]}",
        "source": {"kind": doc.get("kind"), "identity": doc.get("identity"),
                   "revision": doc.get("revision"),
                   "describes": doc.get("describes")},
        "scopes": scopes,
        "overrides": overrides,
        "reviewPolicies": dict(REVIEW_POLICIES),
        "blockers": list(trouble),
        "diagnostics": trouble.coded,
    }


def gate_policy_public(found: dict) -> dict:
    """One gate policy as the board may show it: the same allowlist law
    `public` keeps, applied to a structure that holds operator-local paths
    in its check definitions and nothing else of consequence."""
    return {
        "v": found["v"], "configured": found["configured"],
        "revision": found["revision"], "policyId": found["policyId"],
        "source": dict(found["source"]),
        "scopes": {scope: {
            "scope": scope, "why": entry["why"], "declared": entry["declared"],
            "gated": entry["gated"], "review": entry["review"],
            "reviewWhy": entry["reviewWhy"],
            "checks": [{"name": c["name"], "scope": c["gateScope"],
                        "requirement": c["requirement"],
                        "run": c["run"], "root": c["root"],
                        "checkId": c["checkId"], "required": c["required"],
                        "help": c["help"]} for c in entry["checks"]],
        } for scope, entry in found["scopes"].items()},
        "overrides": dict(found["overrides"]),
        "reviewPolicies": dict(found["reviewPolicies"]),
        "blockers": list(found["blockers"]),
        "diagnostics": list(found["diagnostics"]),
    }


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
            "review": "profiles.review_id(filename, head)",
        },
        "evidenceKinds": dict(EVIDENCE_KINDS),
        "pending": dict(PENDING_CAPABILITIES),
        "extend": {
            "a runner": ("`verify.py` — it reads `verify` and `prepare` off the "
                         "recorded manifest, runs each named check itself with "
                         "no shell, and files the evidence under "
                         "evidence_id('verification', requestId, …)"),
            "a doctor": ("`doctor.py` — it calls resolve() for each of "
                         "profiles.INTENTS, reads `diagnostics`, `blockers` "
                         "and `guarantees.pending` off what came back, and "
                         "resolves its own fixtures through document_at() and "
                         "resolve(policy=…). It has no second parser and must "
                         "not grow one: every code it reports is one "
                         "profiles.DIAGNOSTICS names"),
            "a gate": ("`gates.py` — it reads the requirements `gate_policy()` "
                       "returns, the evidence `verify.for_task()` returns, and "
                       "insists on a `passed` that `verify.outcome_now()` still "
                       "calls `passed` against the exact commit it is about to "
                       "let through. It declares 'verified-outputs', and it has "
                       "no second parser either: every refusal it reports about "
                       "a policy is a diagnostic this module named"),
            "a reviewer": ("read the frozen manifest for the run under review "
                           "and `freshness()` for what has moved since — a PR "
                           "review's own checkout, base and head are in "
                           "`manifest['review']`, resolved and owned by "
                           "`reviews.py`"),
        },
    }
