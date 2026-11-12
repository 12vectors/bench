"""The Settings page: how this board is configured, what has actually been
verified, and what to configure next.

Three questions, and before this they were answerable only by reading
JSON and logs. The configuration was real — `profiles.py` resolved it,
`verify.py` ran what it declared, `gates.py` enforced what it required
and `doctor.py` diagnosed all three — but the only *operator* surface was
a text editor and a terminal. So the facts this module assembles are not
new. What is new is that they are assembled in one place, in words, with
the distinction between them kept.

**It owns no policy.** Every value here comes from the authority that
already answers for it:

| the question | the authority |
| --- | --- |
| what may this run do, and read | `profiles.resolve()` |
| what does this board require before a merge | `profiles.gate_policy()` |
| what did the project's checks actually find | `verify.py`'s records |
| may this merge happen | `gates.evaluate()` |
| what would stop a launch, asked without paying | `doctor.report()` |
| where are the roots, and what is each setting | `config.py` |

A second reader of the profile document would be the one defect this
feature could introduce, so there is not one: a draft is validated by
being put through `profiles.document_from()` and `profiles.resolve()` —
the same parser and the same resolver a launch rides. This module's own
code does three things only: it **names** states so "not configured" can
never render as a pass, it **compares** two resolutions so a change can
be previewed, and it **writes one file** atomically.

## What is editable, and what is not

One file: the profile document (`manager/local/profiles.json`, or
wherever `BOARD_AGENT_PROFILES` points). That is where checks,
preparation steps, approved context, command grants and gates live, and
it is read fresh on every launch — so an edit applies to the next launch
and never to a run in flight.

`manager/local/.env` is **read-only here**, with its provenance shown and
the instruction to change it. Not for want of a writer: `config.py`
snapshots that file at import, so a value written from the page would not
be in force until a restart, and a value that the real environment also
sets would not be in force at all. A control that loses to a
higher-precedence source is worse than no control, so the page says which
source won and how to change it there.

## What a save cannot do

Nothing that moves work. Applying a document writes a file and files a
history line; it starts no agent, moves no card, merges nothing,
publishes nothing, reruns no check and restarts nothing. A run already in
flight keeps the manifest it launched with, because that manifest is a
record on disk and this never touches it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import agentsettings
import config
import doctor
import gates
import profiles
import sync
import taskfiles
import verify

VERSION = 1

# Where the whole reading lives, and what that means for who may read it.
LOCATION = ("local to this board — it names this disk's paths and this "
            "machine's reading of a remote, and nothing in it is committed, "
            "pushed or carried into a pull request")


# ── the vocabulary ─────────────────────────────────────────────────────
#
# Eight words, because the one word this page exists to refuse is a ninth
# covering all of them. "No gate configured" is not a passed gate; a check
# nobody has run is not a check that passed; a guarantee nothing enforces
# is not a guarantee. Each of these is a different thing to do next, so
# each gets a word and a colour is never the carrier of the meaning.

STATES = {
    "not-configured": ("nobody has configured this. That is not a fault and "
                       "it is not a pass: nothing is required here and "
                       "nothing is proved"),
    # The value is kept (other readers and the setup guide's fingerprint
    # use it); its definition makes no claim about history, because a
    # registered check may well have run on many cards (#92 §3).
    "configured-untested": ("configured. This page does not show whether "
                            "it has run: results are kept per card, and what "
                            "it would do is resolved here, not established"),
    "unsupported": ("nothing installed here can deliver this, so bench "
                    "reports it as unsupported rather than as a guarantee it "
                    "cannot make"),
    "invalid": ("the configuration declares it and bench refuses it. The "
                "code and the remedy are on the row"),
    "blocked": ("it could not be asked — a permission, a dependency or a "
                "capability was missing before anything ran"),
    "failed": "it ran and said no",
    "stale": ("it passed, and what it was about has changed since, so it "
              "says nothing about the state of things now"),
    "verified": ("bench itself ran it and recorded a pass bound to the exact "
                 "source it ran against"),
}

# The one sentence section 2 leads with, kept here so the page, the docs
# and the tests cannot drift into three wordings of it.
OWNERSHIP = "Your team provides the checks; Bench runs them."

OWNERSHIP_DETAIL = (
    "A developer or a coding agent can supply the programs. The operator "
    "approves what authority they run with. The project stays responsible "
    "for whether they cover the risks that matter: passing a program proves "
    "that program passed, not that the application is correct."
)

# Who owns what — the division this feature is most often misread as
# changing. It does not change it; it writes it down where the
# configuration is.
RESPONSIBILITIES = [
    {"what": "Decide what correct behaviour looks like",
     "who": "you and your project team"},
    {"what": "Write and maintain tests, build checks and verification "
             "scripts", "who": "your developers, with agent assistance if "
                                "useful"},
    {"what": "Approve runnable commands, test locations and required checks",
     "who": "the operator: whoever configures this bench installation"},
    {"what": "Execute an approved check and record its result and code "
             "version", "who": "bench"},
    {"what": "Enforce the configured conditions before integration or "
             "acceptance", "who": "bench's gates"},
    {"what": "Judge whether the checks cover the important risks, and review "
             "the work", "who": "you and your reviewers"},
]

# What to ask a developer for, when the answer to "which checks are we
# providing" is "none yet". Prose on the page; a checklist here so it is
# one list rather than one per renderer.
SUPPLY_CHECKLIST = [
    "Identify the checks that matter, and say what they do **not** cover.",
    "Make them run with no interactive questions, and exit non-zero when a "
    "check fails. A wrapper that prints OK after a failed command hides it.",
    "Supply any preparation step they need. Use disposable test data, never "
    "the live database, and document the dependencies and access required.",
    "Register the approved command and its scopes in this board's execution "
    "policy — then, separately, decide which of them a gate must require.",
    "Demonstrate a passing case and a deliberately failing one in a safe "
    "fixture, and check that bench shows the right outcome and blocks the "
    "gate on the failure.",
]

# A worked example that reuses a test suite the project already has,
# rather than inventing a bench-specific testing framework.
WORKED_EXAMPLE = {
    "why": ("The command is one your team already runs. Registering it as a "
            "named check is what lets bench run it itself, bind the result "
            "to a commit, and enforce it — none of which changes what the "
            "command does."),
    "document": {
        "v": profiles.PROFILE_VERSION,
        "checks": [{"name": "unit",
                    "run": "python3 -m unittest discover -s tests",
                    "root": "worktree",
                    "scopes": {"fast": ["-k", "fast"]},
                    "timeout": 900}],
        "profiles": {"work": {"verify": ["unit"]},
                     "act-pr": {"verify": ["unit"]},
                     "review": {"verify": ["unit"]}},
    },
    "then": ("Registering it makes nothing mandatory. A gate is the separate "
             "decision: `\"gates\": {\"acceptance\": {\"checks\": [\"unit\"]}}` "
             "is what makes a merge wait for it."),
}


# ── Bench-run verification, external CI, and review ────────────────────
#
# Three things a single green tick would collapse. The same project test
# program can feed the first two, and that is exactly why the distinction
# has to be stated rather than left to be inferred from a colour.

KINDS = {
    "bench": {
        "label": "Bench-run checks",
        "what": ("a named check in this board's execution policy. Bench "
                 "builds the argument list itself, runs it with no shell, "
                 "waits on it, and records the exit status its own wait() "
                 "returned, bound to the commit and the program it ran"),
        "who": "bench, on this machine",
        "gateable": True,
        "authority": "verify.py's own evidence records",
    },
    "ci": {
        "label": "External CI status",
        "what": ("the check runs GitHub reports on a pull request. Bench "
                 "polls them, folds them into the card's verdict and will "
                 "not merge over a failing one — and it is not evidence a "
                 "named Bench check passed, because bench did not run it "
                 "and cannot bind it to a check identity"),
        "who": "your CI provider, on its own runners",
        "gateable": False,
        "authority": "github.py's poll of the pull request's check runs",
    },
    "review": {
        "label": "Review requirements",
        "what": ("a judgement, not a program: bench's own recorded review "
                 "verdict, or a formal GitHub review state. A COMMENTED "
                 "review is neither"),
        "who": "a reviewer — a person, or a review agent",
        "gateable": True,
        "authority": "gates.py's two review records, kept apart",
    },
}

# Said plainly, because the honest answer here is a limit rather than a
# feature. Reading an external CI result back as named-check evidence
# would need a mapping bench does not have, and inventing one silently is
# the failure this paragraph exists to refuse.
CI_LIMITS = [
    "A green GitHub status is never converted into evidence for a named "
    "Bench check. Nothing maps one onto the other, and a gate requiring "
    "`unit` is not met by a workflow called `unit`.",
    "Bench reads CI as the pull request's own state: it folds a failing "
    "check into the card's verdict and refuses the merge GitHub refuses. "
    "That behaviour is unchanged by this page.",
    "A required-workflow policy engine, and an adapter that turned an "
    "external run into runner evidence, are both out of scope here. They "
    "are named as follow-up rather than approximated.",
    "CI typically checks pushed code on separate runners; CD packages or "
    "deploys it. Bench verification implies neither: nothing on this page "
    "deploys anything.",
]

# The other three operations a reader may expect this page to perform, and
# does not.
SEPARATE_OPERATIONS = [
    {"what": "merging the application's source", "where": "a person's click "
     "on the card — merge & clean up, or the pull request on GitHub"},
    {"what": "updating this bench installation",
     "where": "`./.task-manager/update.sh`, then `install.py`, then a restart"},
    {"what": "cutting a bench release", "where": "`release.sh`, from bench's "
     "own repository"},
    {"what": "deploying the documentation site",
     "where": "the site's own deploy, outside this board entirely"},
]


# ── four facts an exit code cannot tell apart ──────────────────────────

ACCESS_FACTS = {
    "ui-registration": ("an executable in `manager/local/commands/` is a $ "
                        "chip a person clicks. Appearing there grants a "
                        "headless run nothing"),
    "agent-capability": ("a named command grant — the only thing that "
                         "reaches an adapter's permission rules"),
    "prompt-instruction": ("what the run is told. Prose persuades; it does "
                           "not enforce"),
    "gate-requirement": ("what a verification or a merge gate will insist "
                         "on. Declared here, enforced at the mutation"),
}


# ── which diagnostic a reader should go and fix which field for ────────
#
# Presentational and nothing more. The verdict is `profiles`'s: every code
# below is one `profiles.DIAGNOSTICS` names, and this maps it to the place
# in the document a person would edit. A code missing from here still
# shows, with no field — a wrong *field* is a worse failure than none.

FIELD_OF = {
    "policy-unreadable": "",
    "name-invalid": "",
    "grant-invalid": "profiles",
    "grant-duplicate": "profiles",
    "grant-reserved": "profiles",
    "grant-root-unavailable": "profiles",
    "context-unreadable": "context",
    "context-refused": "context",
    "context-duplicate": "context",
    "check-invalid": "checks",
    "check-root-unsafe": "checks",
    "check-reshapes-repository": "checks",
    "check-names-credential": "checks",
    "check-duplicate": "checks",
    "check-undeclared": "profiles",
    "check-unrunnable": "checks",
    "check-input-undeclared": "checks",
    "check-env-invalid": "checks",
    "prepare-invalid": "prepare",
    "prepare-reshapes-repository": "prepare",
    "prepare-names-credential": "prepare",
    "prepare-duplicate": "prepare",
    "prepare-undeclared": "checks",
    "deny-unknown": "profiles",
    "publication-widened": "profiles",
    "requirement-unsupported": "requires",
    "gate-scope-unknown": "gates",
    "gate-invalid": "gates",
    "gate-check-undeclared": "gates",
    "gate-scope-undeclared": "gates",
    "gate-review-unknown": "gates",
    "gate-review-duplicate": "gates",
    "gate-signoff-unknown": "gates",
}

# The top-level keys this editor understands. A key outside this list is
# preserved exactly as the file had it — an operator's own annotation, or a
# field a later bench reads, must survive a save made by this one.
KNOWN_KEYS = ("v", "requires", "context", "commands", "checks", "prepare",
              "verify", "profiles", "gates")

HISTORY_CAP = 200


class Conflict(ValueError):
    """A save made against a revision that is no longer the current one.

    Its own class because the answer is different from every other
    refusal: nothing is wrong with the document, somebody else's save
    landed first, and the remedy is to re-read and re-apply rather than to
    fix a field.
    """

    def __init__(self, message: str, current: str) -> None:
        super().__init__(message)
        self.current = current


# ── secrets never reach a reader ───────────────────────────────────────

MASK = "••••••••"

# A value worth masking whatever its name: the shapes a credential pasted
# into a settings file actually takes.
SECRET_VALUES = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")

# A setting name that holds a credential rather than a path or a flag.
SECRET_KEYS = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|APIKEY|_KEY$|AUTH)",
    re.IGNORECASE)


def secret_key(name: str) -> bool:
    """Does this setting's *name* say it holds a credential?

    Name-based, and deliberately generous: a value bench cannot recognise
    is still masked when the key says what it is. The credential list
    `config` already keeps for the verification environment is folded in,
    so the two cannot disagree about what counts.
    """
    return bool(SECRET_KEYS.search(name)) or name in config.CREDENTIAL_ENV


def scrub(text: str) -> str:
    """Anything credential-shaped out of text on its way to a reader.

    The same job `verify.log_text` does for a check's output, applied to
    the one other place bytes bench did not write get through: a value
    read out of a settings file.
    """
    return SECRET_VALUES.sub(MASK, str(text or ""))


def masked(name: str, value: str) -> tuple[str, bool]:
    """One setting's value as it may be shown, and whether it was masked.

    Two returns rather than a sentinel, because the page has to say "this
    is redacted" in words — a reader who cannot tell a mask from a value
    would read `••••••••` as the setting's content and try to keep it.
    """
    if value and secret_key(name):
        return MASK, True
    scrubbed = scrub(value)
    return scrubbed, scrubbed != (value or "")


# ── where each setting's value comes from ──────────────────────────────


def env_file_values() -> dict[str, str]:
    """`manager/local/.env` as it is on disk *now*, parsed the way
    `config` parses it.

    Read fresh rather than taken from `config._ENV`, because the two are
    different facts and the difference is one a reader needs: `config`
    snapshotted the file when this board started, so a file edited since
    is not in force. `catalog()` reports both and says which is running.
    """
    values: dict[str, str] = {}
    path = config.LOCAL / ".env"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


# Every setting the page catalogues, with what it is for, when a change to
# it takes effect, and which section of the page it belongs to. The
# defaults are `config`'s own, read from `config` rather than repeated, so
# this table cannot drift from what the board actually does.
#
# `appliesAt` is the real backend behaviour and nothing more optimistic:
# `config` reads `.env` once at import, so almost everything here needs a
# restart. The two that do not are named `fresh`, because they are read on
# every launch.
CATALOGUE = (
    ("BOARD_PORT", "installation", "restart",
     "the port this board serves on"),
    ("BOARD_TITLE", "installation", "restart",
     "what the tab calls this project"),
    ("BOARD_APP_ROOT", "installation", "restart",
     "the application this board manages"),
    ("BOARD_WORK_ROOT", "installation", "restart",
     "where tasks/, plans/ and reference/ live"),
    ("BOARD_WORKTREES", "installation", "restart",
     "where the application's task branches are checked out"),
    ("BOARD_APP_BASE", "installation", "restart",
     "the application branch a card's work starts from by default"),
    ("BOARD_STATE_BASE", "installation", "restart",
     "the work state's own branch"),
    ("BOARD_GIT_REMOTE", "installation", "restart",
     "which remote the application's branches and PRs go to"),
    ("BOARD_STATE_GIT_REMOTE", "installation", "restart",
     "which remote the board's own bookkeeping syncs through"),
    ("BOARD_APP_MERGE", "installation", "restart",
     "whether merge & clean up merges locally or on origin"),
    ("BOARD_COMMIT_MOVES", "installation", "restart",
     "whether a move claims the card and commits itself"),
    ("BOARD_SYNC", "installation", "restart",
     "whether boards converge through the work state's remote"),
    ("BOARD_SYNC_INTERVAL", "installation", "restart",
     "how often the pull beat runs"),
    ("BOARD_GH_BIN", "installation", "restart",
     "the gh binary PR operations use"),
    ("BOARD_AGENT_ADAPTER", "agents", "next-launch",
     "which coding-agent harness headless jobs run through by default"),
    ("BOARD_AGENT_ADAPTER_WORK", "agents", "next-launch",
     "the harness implementation runs through, when it differs from the "
     "default"),
    ("BOARD_AGENT_ADAPTER_ACT_PR", "agents", "next-launch",
     "the harness PR fixes run through, when it differs from the default"),
    ("BOARD_AGENT_ADAPTER_REVIEW", "agents", "next-launch",
     "the harness reviews run through, when it differs from the default"),
    ("BOARD_AGENT_MODEL", "agents", "next-launch",
     "the model every launch rides unless an intent overrides it"),
    ("BOARD_AGENT_MODEL_WORK", "agents", "next-launch",
     "the model implementation rides, when it differs from the default"),
    ("BOARD_AGENT_MODEL_ACT_PR", "agents", "next-launch",
     "the model PR fixes ride, when it differs from the default"),
    ("BOARD_AGENT_MODEL_REVIEW", "agents", "next-launch",
     "the model reviews ride, when it differs from the default"),
    ("BOARD_AGENT_MODEL_REFINE", "agents", "next-launch",
     "the model a card refinement rides, when it should differ from the "
     "general one"),
    ("BOARD_AGENT_COMMANDS", "access", "restart",
     "the legacy command prefixes a work or act-pr run may launch, used "
     "only where the execution policy configures nothing for that intent"),
    ("BOARD_AGENT_PROFILES", "access", "restart",
     "where the execution policy document is, when it is not "
     "manager/local/profiles.json"),
    ("BOARD_VERIFY_TIMEOUT", "verification", "fresh",
     "the longest a named check may run"),
    ("BOARD_VERIFY_PREPARE_TIMEOUT", "verification", "fresh",
     "the longest a preparation step may run"),
    ("BOARD_VERIFY_PARALLEL", "verification", "restart",
     "how many verifications bench runs at once"),
    ("BOARD_VERIFY_ENV_DENY", "verification", "fresh",
     "extra environment variable names a check never inherits"),
    ("BOARD_PR_POLL_INTERVAL", "installation", "restart",
     "how often open pull requests are polled"),
    ("BOARD_FETCH_TIMEOUT", "installation", "restart",
     "how long a branch-point fetch may take before a launch gives up"),
)

APPLIES_AT = {
    "restart": ("this board read manager/local/.env when it started, so a "
                "change to it is in force after a restart"),
    "fresh": ("read on every use, so a change to it applies to the next one "
              "without a restart"),
    "next-launch": ("the execution policy document is read fresh on every "
                    "launch, so a change applies to the next launch and "
                    "never to a run already in flight"),
}
# The agent keys are the exception to "a .env change needs a restart":
# `config.agent_choice` reads them on every launch (task 77).
AGENT_APPLIES = ("read from manager/local/.env on every launch, so a change "
                 "applies to the next launch without a restart and never to a "
                 "run already in flight — edit it in Settings → Agents")


def catalog() -> list[dict]:
    """Every catalogued setting: effective value, where that value came
    from, whether this page can change it, and when a change applies.

    Provenance is **observed**, never guessed. The process environment
    beats `.env`, which beats the default, and that is the order `config`
    applies — so a key present in `os.environ` is reported as coming from
    the environment and as locked, with the instruction to change it
    where it is actually set. A control that lost to a higher-precedence
    source would be worse than no control.
    """
    on_disk = env_file_values()
    out = []
    for key, section, applies, why in CATALOGUE:
        default = _default_for(key)
        if key in os.environ:
            source, locked = "environment", True
            raw = os.environ[key]
            how = (f"{key} is set in the environment this board was started "
                   f"with, which beats manager/local/.env. Change it where "
                   f"it is exported, then restart the board.")
        elif key in on_disk:
            source, locked = "local-file", False
            raw = on_disk[key]
            how = (f"{key} is set in manager/local/.env. Edit that file and "
                   f"restart the board; it is gitignored and belongs to this "
                   f"machine.")
        else:
            source, locked = "default", False
            raw = default
            how = (f"{key} is not set anywhere, so the documented default "
                   f"applies. manager/core/.env.example is where the whole "
                   f"list is documented.")
        agent_key = key in config.AGENT_KEYS
        if agent_key and source != "environment":
            how = (f"{key} is edited in Settings → Agents, which writes "
                   f"manager/local/.env; the next launch reads it.")
        shown, redacted = masked(key, raw)
        # An agent key is read live, so what is running is what the file
        # says now — never a stale startup value (task 77).
        running = (config.agent_setting(key, on_disk)["value"] if agent_key
                   else config.setting(key, default))
        running_shown, _ = masked(key, running)
        if agent_key and source == "default":
            running_shown = shown
        out.append({
            "key": key, "section": section, "why": why,
            "value": shown, "redacted": redacted,
            "default": masked(key, default)[0],
            "source": source, "locked": locked,
            "editableHere": agent_key and not locked,
            "how": how,
            "appliesAt": applies,
            "appliesWhy": AGENT_APPLIES if agent_key else APPLIES_AT[applies],
            # What the *running* board has, which is a different fact from
            # what the file says now. Only sent when they disagree, so an
            # ordinary row carries no confusing second value.
            "running": None if running_shown == shown else running_shown,
            "movedSinceStart": running_shown != shown,
        })
    return out


def _default_for(key: str) -> str:
    """The documented default for one setting, taken from
    `manager/core/.env.example` so there is one place it is written down.

    The example file is the reference the docs render; reading it here is
    what stops this table from becoming a second, slowly diverging copy
    of it. A key the example does not name gets an empty default, which
    is what `config.setting` would do with it anyway.
    """
    for line in _env_example().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip("'\"")
    return ""


_EXAMPLE_CACHE: dict[str, str] = {}


def _env_example() -> str:
    if "text" not in _EXAMPLE_CACHE:
        try:
            _EXAMPLE_CACHE["text"] = (config.CORE / ".env.example").read_text(
                encoding="utf-8")
        except OSError:
            _EXAMPLE_CACHE["text"] = ""
    return _EXAMPLE_CACHE["text"]


# ── the installation itself ────────────────────────────────────────────


def installation() -> dict:
    """What build is running, from where, in which layout.

    `revision` is **unknown** rather than invented when the tool is not a
    git checkout — a release artifact unpacked from a tarball genuinely
    has no commit, and reporting one would be a provenance the process
    cannot determine.
    """
    tool_repo = config.git_root(config.TM_ROOT)
    revision, described = None, None
    if tool_repo is not None:
        revision = _git_at(tool_repo, "rev-parse", "HEAD")
        described = _git_at(tool_repo, "describe", "--tags", "--always",
                            "--dirty")
    if config.EXTERNAL:
        mode = "external"
        mode_why = ("the tool, the application and the work state are three "
                    "separately named roots: nothing of bench's is in the "
                    "application's history")
    elif config.TM_ROOT == config.REPO:
        mode = "source"
        mode_why = ("the tool is the repository it sits in — a bench source "
                    "checkout, which is how bench itself is developed")
    else:
        mode = "vendored"
        mode_why = ("the tool lives inside the application it manages, and "
                    "its tasks/ are in that repository's history")
    return {
        "version": _version_text(),
        "revision": revision,
        "described": described,
        "revisionKnown": revision is not None,
        "revisionWhy": (None if revision is not None else
                        "this install is not a git checkout — a release "
                        "artifact has no commit of its own, and bench will "
                        "not invent one"),
        "mode": mode, "modeWhy": mode_why,
        "layout": config.layout(),
        "roots": [
            {"name": "the tool", "path": str(config.TM_ROOT),
             "holds": "manager/core, manager/local, prompts, adapters, "
                      "runtime state"},
            {"name": "the application", "path": str(config.REPO),
             "holds": "code, branches, worktrees, pull requests"},
            {"name": "the work state", "path": str(config.work_root()),
             "holds": "tasks/, plans/, reference/"},
            {"name": "the worktrees", "path": str(config.WORKTREES),
             "holds": "the application's task branches, checked out"},
        ],
        "git": {
            "application": {
                "repo": str(config.app_repo()), "base": config.APP_BASE,
                "remote": config.git_remote(),
                "remoteSetting": config.GIT_REMOTE or None,
                "mergeOnOrigin": config.app_merge_on_origin(),
                "ghAvailable": config.gh_available(),
                "ghRepo": config.gh_repo(),
                "what": "branch, worktree, commit code, push, open, review "
                        "and merge a code pull request",
            },
            "workState": {
                "repo": (str(config.state_repo()) if config.state_repo()
                         else None),
                "base": config.STATE_BASE,
                "remote": config.state_git_remote(),
                "remoteSetting": config.state_remote_setting() or None,
                "what": "create, edit, move and archive a ticket, record an "
                        "outcome, commit and sync board state",
            },
        },
        "sync": {
            "enabled": config.SYNC,
            "commitMoves": config.COMMIT_MOVES,
            "interval": config.SYNC_INTERVAL,
            "status": sync.status(),
            "what": ("BOARD_SYNC is about the board's own state and nothing "
                     "else. How the application is merged is "
                     "BOARD_APP_MERGE's answer"),
        },
        "localOnly": [
            {"what": "manager/local/.env", "why": "it names the roots on this "
             "disk and the binaries on this PATH"},
            {"what": "manager/local/profiles.json", "why": "it names roots on "
             "this disk and capabilities this adapter declares"},
            {"what": "manager/local/state/", "why": "agent, drive and command "
             "logs, session timelines, recorded plans, manifests, evidence, "
             "review checkouts and gate records — this machine's reading of "
             "a remote and this machine's paths"},
        ],
        "shared": [
            {"what": "tasks/", "why": "the tickets a teammate should read"},
            {"what": "plans/ and reference/",
             "why": "the implementation plans and the supporting documents"},
        ],
        "separateOperations": list(SEPARATE_OPERATIONS),
        "problems": config.problems(),
    }


def _version_text() -> str:
    try:
        return (config.CORE / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _git_at(repo: Path, *args: str) -> str | None:
    try:
        result = config.git_run(repo, *args, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


# ── what a run may do, and read ────────────────────────────────────────


def access(doc: dict | None = None) -> dict:
    """The resolved profile for every launch intent, from the one
    resolver.

    `policy` is passed straight through to `profiles.resolve`, so the same
    function answers for the board's own document and for a draft being
    previewed. Nothing here reads the document itself.
    """
    adapter = profiles.adapter_identity()
    table = profiles.capability_table(adapter)
    readings = {}
    for intent in profiles.INTENTS:
        try:
            found = doctor.intent_reading(intent, None, policy=doc)
        except (ValueError, OSError) as exc:
            readings[intent] = {"intent": intent, "error": str(exc)}
            continue
        shown = profiles.public(found) or {}
        shown["summary"] = profiles.summary(found)
        shown["state"] = _intent_state(found)
        shown["fields"] = _fields(found.get("diagnostics") or [])
        readings[intent] = shown
    return {
        "intents": readings,
        "adapter": {"name": adapter["name"], "identity": adapter["identity"],
                    "version": adapter["version"],
                    "declared": adapter["declared"],
                    "capabilities": adapter["capabilities"],
                    "unsupported": adapter["unsupported"],
                    "note": adapter.get("note")},
        "capabilities": table,
        "facts": dict(ACCESS_FACTS),
        "uiCommands": config.commands(),
        "frozen": frozen_runs(),
        "enforcement": list(profiles.ENFORCEMENT),
        "pending": dict(profiles.PENDING_CAPABILITIES),
    }


def _intent_state(found: dict) -> str:
    """One intent's reading, in the page's own vocabulary."""
    if found.get("blockers"):
        return "invalid"
    if (found.get("source") or {}).get("kind") == "legacy":
        return "not-configured"
    return "configured-untested"


def _fields(diagnostics: list[dict]) -> list[dict]:
    """Each diagnostic with the place in the document a person would go
    and edit. The verdict is `profiles`'s; only the pointer is ours."""
    return [{"code": d.get("code"), "why": d.get("why"),
             "remedy": d.get("remedy"),
             "field": FIELD_OF.get(d.get("code") or "", None)}
            for d in diagnostics]


def frozen_runs() -> list[dict]:
    """Every run with a recorded manifest that is still alive, and whether
    the settings have moved under it.

    The point this exists to make: an edit applied now does not reach any
    of these. Each one rode a manifest written before it started, that
    record is on disk, and nothing in this module touches it.
    """
    out = []
    try:
        import agents
        live = agents.list_public()
    except Exception:  # pragma: no cover - agents is always importable
        live = []
    for entry in live:
        frozen = profiles.recorded(entry.get("id") or "")
        if frozen is None:
            continue
        out.append({
            "agent": entry.get("id"), "name": entry.get("name"),
            "mode": entry.get("mode"), "task": entry.get("file"),
            "policyId": frozen.get("policyId"),
            "revision": (frozen.get("source") or {}).get("revision"),
            "freshness": profiles.freshness(frozen),
            "why": ("this run keeps the policy it launched with. A settings "
                    "change applies to the next launch"),
        })
    return out


# ── the checks, and the steps that prepare them ────────────────────────


def verification(doc: dict | None = None) -> dict:
    """Every registered check and preparation step, as the policy layer
    resolved them — with the distinction a reader most needs kept: which
    of them a gate actually requires.
    """
    doc = profiles.document() if doc is None else doc
    steps, step_trouble = profiles.document_steps(doc)
    checks, check_trouble = profiles.document_checks(doc, steps)
    policy = profiles.gate_policy(doc)
    required: dict[str, set[str | None]] = {}
    for scope, entry in policy["scopes"].items():
        for want in entry["checks"]:
            required.setdefault(want["name"], set()).add(want["gateScope"])
    # Resolved once per intent, not once per check: `resolve` hashes every
    # approved input, and asking it per row would turn a page load into a
    # quadratic read of the same files.
    askable = {intent: _intent_check_names(doc, intent)
               for intent in profiles.INTENTS}
    roots = _check_root_words()

    rows = []
    for name in sorted(checks):
        check = checks[name]
        rows.append({
            "name": name,
            "state": "configured-untested",
            "run": check["run"],
            "executable": check["executable"],
            "argv": list(check["argv"]),
            "root": check["root"],
            "rootWhy": roots.get(check["root"]),
            "scopes": {word: list(args)
                       for word, args in sorted(check["scopes"].items())},
            # `prepare` holds the resolved steps; a reader wants the names
            # here and reads the steps' own rows below for the detail.
            "prepare": [step["name"] for step in check["prepare"]],
            # what the result is bound to beyond its source and programs:
            # the approved inputs it is handed and the environment
            # variables whose values are bound — names only, never values
            "inputs": [entry["name"] for entry in check.get("inputs") or []],
            "env": list(check.get("env") or []),
            "timeout": check["timeout"],
            "checkId": check["checkId"],
            "checkSubstance": check.get("checkSubstance"),
            "help": check.get("help"),
            "required": check["required"],
            "intents": sorted(intent for intent in profiles.INTENTS
                              if name in askable[intent]),
            "requirement": sorted(
                ("full run" if s is None else f"scope {s}")
                for s in required.get(name, ())),
            "requiredBy": sorted(scope for scope, entry
                                 in policy["scopes"].items()
                                 if any(w["name"] == name
                                        for w in entry["checks"])),
        })

    step_rows = []
    for name in sorted(steps):
        step = steps[name]
        step_rows.append({
            "name": name, "run": step["run"],
            "executable": step["executable"], "argv": list(step["argv"]),
            "network": bool(step.get("network")),
            "networkEnforcement": step.get("networkEnforcement"),
            "required": step["required"],
            "help": step.get("help"),
            "usedBy": sorted(c for c, v in checks.items()
                             if name in [s["name"] for s in v["prepare"]]),
        })

    return {
        "ownership": OWNERSHIP,
        "ownershipDetail": OWNERSHIP_DETAIL,
        "responsibilities": list(RESPONSIBILITIES),
        "checklist": list(SUPPLY_CHECKLIST),
        "example": json.loads(json.dumps(WORKED_EXAMPLE)),
        "state": ("not-configured" if not rows else
                  "invalid" if (step_trouble or check_trouble) else
                  "configured-untested"),
        "checks": rows,
        "prepare": step_rows,
        "roots": roots,
        "rootsNote": ROOTS_NOTE,
        # `document_steps` and `document_checks` hand back `(code, why)`
        # pairs — the codes are `profiles.DIAGNOSTICS`'s own, which is why
        # they are passed through rather than relabelled here.
        "problems": [{"code": code, "why": why, "field": FIELD_OF.get(code)}
                     for code, why in [*step_trouble, *check_trouble]],
        "outcomes": dict(verify.OUTCOMES),
        "reasons": dict(verify.REASONS),
        "preparationIsNotVerification": (
            "A preparation step gets a disposable environment ready — "
            "approved dependencies, a test database. A preparation step that "
            "fails means the checks did not run; it is recorded "
            "`prepare-failed` and it is not a failing test result."),
        "registrationIsNotRequirement": (
            "Registering a check makes nothing mandatory. A gate is the "
            "separate decision, and a board with no gate has no required "
            "verification barrier at all."),
        "evidenceScope": (
            "What is configured here is installation-wide. Evidence is "
            "per card and per commit: open a card's ◫ evidence sheet for "
            "what actually ran against it. Nothing on this page implies "
            "that any task has current passing evidence."),
        "trustLimits": [
            "Bench enforces the argument list, the absence of publication "
            "credentials in the check's environment, the directory it starts "
            "in, and that a check never runs in the application checkout.",
            "Bench directs where caches, virtualenvs and build output land, "
            "by pointing HOME, TMPDIR and the cache variables into a "
            "directory made for the verification.",
            "That is not an operating-system sandbox. A check with a path "
            "can still read or write anything this user can, and the network "
            "is whatever the machine has. `filesystem-sandbox` is the "
            "capability that would change this, and nothing installed here "
            "enforces it.",
        ],
        "envDenied": config.verify_env_deny(),
        "shim": str(profiles.verify_shim()),
        "limits": {"timeout": config.VERIFY_TIMEOUT,
                   "prepareTimeout": config.PREPARE_TIMEOUT,
                   "parallel": config.VERIFY_PARALLEL},
    }


def _check_root_words() -> dict:
    """The execution locations a check may name, in words — and the
    sentence that keeps a checkout from reading as a sandbox."""
    return {
        "worktree": ("the card's own checkout, which bench made for its "
                     "branch"),
        "source": ("a detached checkout of one exact commit that bench makes "
                   "for the verification and owns. Clean by construction, so "
                   "its evidence is what a gate can read"),
        "review": ("the isolated checkout pinned to the pull request's exact "
                   "proposed commit, for a PR-review run. It never falls "
                   "back to the live application"),
    }


ROOTS_NOTE = ("These are execution locations, not security sandboxes. The "
              "application checkout is not among them: that is the live code "
              "and the live services, shared with every other worktree's git "
              "metadata.")


def _intent_check_names(doc: dict, intent: str) -> set[str]:
    """Which checks one intent may ask bench to run, read off the resolved
    profile rather than off the document a second time."""
    try:
        found = doctor.intent_reading(intent, None, policy=doc)
    except (ValueError, OSError):
        return set()
    return {c["name"] for c in found.get("verify") or []}


def _access_words(doc: dict, intent: str) -> dict:
    """One intent's access, reduced to what a preview compares.

    Names rather than whole structures: a reader wants "this run gains the
    `build` grant", not a diff of two resolved manifests. Hashes and paths
    are deliberately out — they move for reasons that are not a change to
    what the run may do.
    """
    try:
        found = doctor.intent_reading(intent, None, policy=doc)
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    return {
        "grants": sorted(g["name"] for g in found.get("commands") or []),
        "checks": sorted(c["name"] for c in found.get("verify") or []),
        "inputs": sorted(i["name"] for i in found.get("context") or []),
        "denied": sorted(d["capability"] for d in found.get("denied") or []),
        "blocked": bool(found.get("blockers")),
    }


# ── acceptance, reviews and overrides ──────────────────────────────────


def acceptance(doc: dict | None = None) -> dict:
    """What this board requires before each guarded mutation, from the gate
    policy — plus what makes a passing record stop counting.

    The three scopes are drawn apart because they guard three different
    commits. The two review records are drawn apart because they are two
    different records and neither stands in for the other.
    """
    policy = profiles.gate_policy(doc)
    public = profiles.gate_policy_public(policy)
    gh = config.gh_available()
    modes = []
    for word, why in profiles.REVIEW_POLICIES.items():
        available, unavailable_why = True, None
        if word in ("github", "separate-reviewer") and not gh:
            available = False
            unavailable_why = (
                "this board has no usable `gh`, so bench cannot read "
                "GitHub's formal review objects at all. A gate asking for "
                "one would be permanently unmet. `bench` is the policy for a "
                "board without GitHub access.")
        modes.append({"name": word, "why": why, "available": available,
                      "unavailableWhy": unavailable_why,
                      "record": ("bench's own verdict" if word == "bench"
                                 else "GitHub's formal review state"
                                 if word != "none" else None)})
    return {
        "state": ("invalid" if public["blockers"] else
                  "configured-untested" if public["configured"] else
                  "not-configured"),
        "policy": public,
        "scopes": dict(profiles.GATE_SCOPES),
        "decisions": dict(gates.DECISIONS),
        "reasons": dict(gates.REASONS),
        "remedies": dict(gates.REMEDIES),
        "reviewModes": modes,
        "fields": _fields(public["diagnostics"]),
        "ungatedIsNotAPass": (
            "A board that declares no gate reads `ungated`, which is "
            "deliberately not `allowed`: nothing is required there and "
            "nothing is proved. It behaves exactly as every board did before "
            "gates existed, and that is allowed — it is simply not the same "
            "protection."),
        "headBinding": (
            "A required check is met by one record only: one bench wrote, "
            "that still reads `passed`, whose source was bound and clean, "
            "whose commit is the commit about to move, and which is the "
            "newest relevant answer for the requirement's coverage. So the "
            "order on a gated board is commit, then ask — and committing "
            "again means asking again."),
        "coverage": (
            "A bare check name is the unscoped full run, and it covers every "
            "scope of that check. A scope covers only itself, so a narrow "
            "pass never satisfies a broader requirement. A gate that needs "
            "both says so by requiring both."),
        "supersede": (
            "The commit about to move is fixed first, and only records about "
            "that commit are weighed; a record about another commit is "
            "history, never the reason. Among them the newest answer is the "
            "gate's answer. A failed, errored or cancelled rerun supersedes "
            "an earlier pass; one still running holds the gate pending; a "
            "pass that went stale gives its own reason and is never a pass. "
            "A denied request ran nothing and supersedes nothing."),
        "sameAccount": (
            "GitHub does not accept an approval or a change request from the "
            "account that opened a pull request. A board whose reviewer runs "
            "as that account can only leave a comment, and bench reads it as "
            "one however it is worded — so `github` and `separate-reviewer` "
            "read unmet there. Bench never switches accounts, asks for a "
            "second credential, or downgrades a formal-review requirement to "
            "a comment."),
        "outstandingChanges": (
            "An outstanding change request blocks, whoever else approved. Per "
            "reviewer the latest formal state stands; a later review from the "
            "same reviewer supersedes their earlier one, a dismissal clears "
            "it, another reviewer's approval clears nobody, and a COMMENTED "
            "review supersedes nothing."),
        "notBranchProtection": (
            "This is bench's reading of the review objects its poll last saw. "
            "It is not GitHub's branch protection, whose required reviews, "
            "code owners and dismissal settings GitHub applies separately "
            "when it merges."),
        "overrides": dict(public["overrides"]),
        "overrideLaw": (
            "An override forges nothing. No check becomes passed, no verdict "
            "appears, and the decision afterwards reads `overridden` — which "
            "says in the same breath that the gate is still unmet. It "
            "authorises one commit, it records the actor, the reason, the "
            "exact head and the gates that were unmet, and it lapses rather "
            "than stretching."),
        "editingIsNotMerging": (
            "Changing a policy here writes a file. It merges nothing, moves "
            "no card and starts no automation — the mutation each gate "
            "guards is still a person's click or the phase runner's own "
            "beat."),
        "gh": {"available": gh, "repo": config.gh_repo()},
    }


# ── diagnostics: what may be asked, and what each answer is worth ──────


def diagnostics() -> dict:
    """The four distances, the coverage table with nothing asked, and
    which of them this page can actually set running.

    Deliberately **no findings**: assembling this runs nothing. The
    findings come from the doctor, through `GET /api/doctor`, when a
    person asks for them — which is a read of its own and is what the
    page's **Check configuration** action does.
    """
    return {
        "modes": {name: {**spec, "uiPath": _mode_path(name)}
                  for name, spec in doctor.MODES.items()},
        "evidence": dict(doctor.EVIDENCE),
        "outcomes": dict(doctor.OUTCOMES),
        "coverage": doctor.coverage([], []),
        "redaction": doctor.redaction(),
        "location": doctor.LOCATION,
        "inspectIs": (
            "Check configuration is read-only static inspection. It resolves "
            "the policy for every launch intent, reads the approved inputs, "
            "and puts bench's own deliberately broken fixture documents "
            "through the same resolver. No project program runs, no adapter "
            "hook is invoked, nothing is pinned and no token is spent."),
        "cannot": [
            "install a dependency",
            "run a test or any other project program",
            "spend a model token",
            "repair a setting",
            "activate a gate",
            "move a card, touch a branch or publish anything",
        ],
        "unknownIsNotPassed": (
            "A fact that would need something to execute stays unknown or "
            "deferred. `deferred` is deliberately not `passed`, and a mode "
            "nobody asked for is drawn as `not asked` — a stage missing from "
            "a coverage table is a stage a reader assumes was fine."),
        "diagnosticCodes": {code: {"title": title, "remedy": remedy}
                            for code, (title, remedy)
                            in sorted(profiles.DIAGNOSTICS.items())},
    }


# Which doctor modes this page can start, and how. Honest about the two it
# cannot: a button that looked actionable and did nothing would be worse
# than a row that says where to run it.
_MODE_PATHS = {
    "inspect": {"available": True, "endpoint": "GET /api/doctor",
                "how": "the Check configuration action on this page",
                "approval": None},
    "conformance": {"available": True,
                    "endpoint": "POST /api/doctor/run",
                    "how": "an armed action here and in the ⚕ "
                           "Configuration check",
                    "approval": "a second, deliberate click"},
    "runtime": {"available": True, "endpoint": "POST /api/doctor/run",
                "how": "an armed action here and in the ⚕ "
                       "Configuration check",
                "approval": "a second, deliberate click"},
    "real-agent": {"available": False, "endpoint": None,
                   "how": "python3 manager/core/board.py --doctor "
                          "--real-agent --approve-real-agent",
                   "approval": "explicit operator authorisation; it spends "
                               "tokens on this board's configured account, "
                               "so this page offers no control for it"},
}


def _mode_path(name: str) -> dict:
    return dict(_MODE_PATHS.get(name, {"available": False, "endpoint": None,
                                       "how": None, "approval": None}))


# ── Bench-run checks, external CI, and review ──────────────────────────


def ci(doc: dict | None = None) -> dict:
    """The three things a single tick would collapse, kept apart — and the
    mapping bench does not have, named as the follow-up it is."""
    doc = profiles.document() if doc is None else doc
    policy = profiles.gate_policy(doc)
    reviewing = any(entry["review"] != "none"
                    for entry in policy["scopes"].values())
    gated = any(entry["gated"] for entry in policy["scopes"].values())
    return {
        "kinds": dict(KINDS),
        "limits": list(CI_LIMITS),
        "bench": {"configured": bool(_checks_of(doc)), "gated": gated},
        "ci": {"polled": True, "interval": config.PR_POLL_INTERVAL,
               "ghAvailable": config.gh_available(),
               "what": ("reviews, check runs and GitHub's mergeable state for "
                        "open pull requests of review-stage cards, folded "
                        "into one verdict: any changes-requested review or "
                        "failing check wins over any approval"),
               "gateable": False,
               "why": ("bench cannot bind an external run to a check "
                       "identity it did not build or wait on, so it is never "
                       "read as named-check evidence")},
        "review": {"required": reviewing,
                   "policies": {scope: entry["review"]
                                for scope, entry in policy["scopes"].items()}},
        "followUp": [
            "An adapter that turned an external CI run into named-check "
            "evidence — it would need a declared mapping from workflow to "
            "check identity, and a source binding bench can verify.",
            "A required-workflow policy engine, for boards whose acceptance "
            "rule is really about GitHub's own required checks.",
        ],
        "noDeployment": (
            "Nothing here deploys. Bench verification is a check bench ran "
            "against a commit; packaging and deployment are separate "
            "operations that this board does not perform."),
    }


# ── the overview, and what to configure next ───────────────────────────


def overview(doc: dict | None = None) -> dict:
    """Seven rows and a state each: what is set up, and what each of those
    words actually licenses a reader to believe."""
    doc = profiles.document() if doc is None else doc
    verif = verification(doc)
    accept = acceptance(doc)
    acc = access(doc)
    adapter = acc["adapter"]
    isolation_supported = "filesystem-sandbox" in (adapter["capabilities"] or [])
    worst = _worst_intent_state(acc["intents"])
    rows = [
        {"id": "checks", "label": "Registered checks", "section": "verification",
         "state": verif["state"],
         "detail": (f"{len(verif['checks'])} named check"
                    f"{'' if len(verif['checks']) == 1 else 's'}, "
                    f"{len(verif['prepare'])} preparation step"
                    f"{'' if len(verif['prepare']) == 1 else 's'}"
                    if verif["checks"] or verif["prepare"] else
                    "no check is registered, so bench runs none of its own")},
        {"id": "acceptance", "label": "Acceptance requirements",
         "section": "acceptance", "state": accept["state"],
         "detail": _gate_detail(accept["policy"])},
        {"id": "review", "label": "Review configuration",
         "section": "acceptance",
         "state": ("not-configured"
                   if all(e["review"] == "none"
                          for e in accept["policy"]["scopes"].values())
                   else "configured-untested"),
         "detail": ", ".join(
             f"{scope}: {entry['review']}"
             for scope, entry in sorted(accept["policy"]["scopes"].items()))},
        {"id": "overrides", "label": "Override policy", "section": "acceptance",
         "state": ("configured-untested"
                   if accept["overrides"]["allowed"] else "not-configured"),
         "detail": (("allowed" + (
             f", restricted to {', '.join(accept['overrides']['actors'])}"
             if accept["overrides"]["actors"] else ", for any git identity"))
             if accept["overrides"]["allowed"] else
             "no override path is enabled, so an unmet gate is unmet")},
        {"id": "access", "label": "Intended agent access", "section": "access",
         "state": worst,
         "detail": _access_detail(acc["intents"])},
        {"id": "isolation", "label": "Actual isolation", "section": "access",
         "state": "unsupported" if not isolation_supported else
                  "configured-untested",
         "detail": ("the installed adapter declares `filesystem-sandbox` "
                    "unsupported, so the writable locations are the intended "
                    "write scope and no operating-system boundary is enforced"
                    if not isolation_supported else
                    "the adapter declares a filesystem sandbox")},
        {"id": "diagnostics", "label": "Diagnostic coverage",
         "section": "diagnostics", "state": "not-configured",
         "detail": ("nothing has been asked in this reading. Check "
                    "configuration is free and runs no project program; the "
                    "three executing stages are separate deliberate "
                    "actions")},
    ]
    return {
        "rows": rows,
        "states": dict(STATES),
        "worst": _worst(row["state"] for row in rows),
        "legacy": (doc.get("kind") == "legacy"),
        "legacyIsAllowed": (
            "A board with no execution policy document is a working board: "
            "cards move, agents run, branches are cut and pull requests "
            "open. What it does not have is a named check bench can run or a "
            "gate that can require one — so it is allowed, and it is not "
            "equivalent protection."),
        "policy": {"kind": doc.get("kind"), "identity": doc.get("identity"),
                   "revision": doc.get("revision"),
                   "describes": doc.get("describes"),
                   "problem": doc.get("problem")},
    }


# Worst-first, so a reader sees the thing to fix rather than the first row
# that happens to be fine.
_SEVERITY = ["invalid", "failed", "blocked", "stale", "unsupported",
             "not-configured", "configured-untested", "verified"]


def _worst(words) -> str:
    found = [w for w in _SEVERITY if w in set(words)]
    return found[0] if found else "not-configured"


def _worst_intent_state(intents: dict) -> str:
    return _worst(r.get("state", "invalid") for r in intents.values())


def _gate_detail(policy: dict) -> str:
    if policy["blockers"]:
        return f"{len(policy['blockers'])} refusal in the gate block"
    gated = [scope for scope, entry in policy["scopes"].items()
             if entry["gated"]]
    if not gated:
        return ("no gate is configured, so nothing is required before a "
                "merge and nothing is proved by one")
    return ", ".join(
        f"{scope}: "
        + (", ".join(c["requirement"] for c in policy["scopes"][scope]["checks"])
           or "review only")
        for scope in sorted(gated))


def _access_detail(intents: dict) -> str:
    parts = []
    for intent in profiles.INTENTS:
        found = intents.get(intent) or {}
        summary = found.get("summary") or {}
        if found.get("error"):
            parts.append(f"{intent}: could not resolve")
        elif found.get("blockers"):
            parts.append(f"{intent}: blocked")
        else:
            parts.append(f"{intent}: {summary.get('commands', 0)} grant"
                         f"{'' if summary.get('commands') == 1 else 's'}, "
                         f"{summary.get('readable', 0)} approved input"
                         f"{'' if summary.get('readable') == 1 else 's'}")
    return "; ".join(parts)


# ── what to configure next ─────────────────────────────────────────────


def recommendations(doc: dict | None = None) -> list[dict]:
    """Prioritised, derived from this board's actual configuration, and
    inert.

    Every entry says why it matters, what a person or a developer must
    *supply* (which is often not a setting at all), what has to be true
    first, and which section to open. Reading one changes nothing: there
    is no apply here, no permission is widened by being recommended, and a
    recommendation that could not be satisfied honestly is not offered.
    """
    doc = profiles.document() if doc is None else doc
    out: list[dict] = []
    steps, _ = profiles.document_steps(doc)
    checks, _ = profiles.document_checks(doc, steps)
    policy = profiles.gate_policy(doc)
    acc = access(doc)

    if doc.get("problem"):
        out.append(_rec("policy-broken", "high",
                        "Fix the execution policy document",
                        doc["problem"],
                        supply="a document this bench can read",
                        section="access", setting="BOARD_AGENT_PROFILES",
                        state="invalid"))

    for intent, found in acc["intents"].items():
        for blocker in (found.get("blockers") or [])[:2]:
            out.append(_rec(f"blocked-{intent}", "high",
                            f"A {intent} launch will not start",
                            blocker,
                            supply="whatever the refusal names — the remedy "
                                   "is in the message",
                            section="access", state="invalid"))

    if not checks:
        out.append(_rec(
            "register-a-check", "high",
            "Register your project's test program as a named check",
            "Bench runs no checks of its own here, so there is no evidence "
            "for any gate to require and no way for a merge to wait on the "
            "project's own verdict. Until a check is registered, the only "
            "account of whether the work passes is the agent's own sentence.",
            supply="a command your team already uses to test or build the "
                   "project, which exits non-zero when it fails and asks no "
                   "interactive questions",
            prerequisites=["a program to run — bench does not write your "
                           "tests, and installing bench did not create any"],
            section="verification", state="not-configured"))
    elif not any(entry["gated"] for entry in policy["scopes"].values()):
        out.append(_rec(
            "gate-a-check", "medium",
            "Decide which checks must pass before work is accepted",
            f"{len(checks)} check"
            f"{'' if len(checks) == 1 else 's'} are registered and none is "
            f"required. Registration alone makes nothing mandatory: every "
            f"gate on this board reads `ungated`, which means nothing is "
            f"required and nothing is proved.",
            supply="a decision about which check, at which coverage, each "
                   "guarded mutation must have",
            prerequisites=["each check seen passing — and failing — in this "
                           "operator's own environment first"],
            section="acceptance", state="not-configured"))

    gated_member = policy["scopes"].get("member", {}).get("gated")
    gated_integration = policy["scopes"].get("integration", {}).get("gated")
    if gated_member and not gated_integration:
        out.append(_rec(
            "gate-the-integration-head", "medium",
            "Add a final check on the integration head",
            "Members are gated and the phase's own integration head is not. "
            "That head is a different commit from every member's: the "
            "revision they were all merged into has never been checked by "
            "any of them, and every member passing proves nothing about it.",
            supply="nothing new — the same check, required at the "
                   "`integration` scope",
            section="acceptance", state="not-configured"))

    adapter = acc["adapter"]
    if "filesystem-sandbox" in (adapter["unsupported"] or []):
        out.append(_rec(
            "isolation-unsupported", "low",
            "Treat the writable locations as intended, not enforced",
            "The installed adapter declares `filesystem-sandbox` "
            "unsupported, and bench enforces no such boundary. A profile "
            "that *requires* it is refused rather than reported as "
            "honoured, which is the only honest answer available — so a "
            "check or an agent with a path can still reach files beside the "
            "ones it was given.",
            supply="an operating-system boundary, if your project needs one. "
                   "That is a sandbox backend, which is not something this "
                   "page can configure",
            section="access", state="unsupported"))

    for scope, entry in sorted(policy["scopes"].items()):
        if entry["review"] in ("github", "separate-reviewer") \
                and not config.gh_available():
            out.append(_rec(
                f"review-unsatisfiable-{scope}", "high",
                f"The {scope} gate asks for a formal GitHub review and this "
                f"board cannot read one",
                "There is no usable `gh` here, so bench cannot read GitHub's "
                "review objects at all and this requirement is permanently "
                "unmet.",
                supply="a working `gh` with access to the repository, or the "
                       "`bench` review policy instead",
                section="acceptance", state="blocked"))

    if checks and not _any_evidence():
        out.append(_rec(
            "test-the-setup", "medium",
            "Exercise the setup deliberately, before trusting it",
            "Checks are registered and bench has never recorded a "
            "verification on this board. A check that has not been seen "
            "passing — and failing — is configuration, not assurance.",
            supply="a safe fixture with a deliberately failing case, so the "
                   "failure is seen to be reported as a failure and to block "
                   "the gate",
            prerequisites=["the check's dependencies installed where bench "
                           "will run it"],
            section="diagnostics", state="configured-untested"))

    if not config.agent_commands() and doc.get("kind") == "legacy":
        out.append(_rec(
            "no-agent-commands", "high",
            "Give headless runs something to run",
            "BOARD_AGENT_COMMANDS is empty and there is no execution policy "
            "document, so a work or act-pr run may launch no project command "
            "at all. An agent that only edits files is still useful, and "
            "bench does not refuse the launch — but it cannot test what it "
            "wrote.",
            supply="the project's test command, in manager/local/.env",
            section="access", setting="BOARD_AGENT_COMMANDS",
            state="not-configured"))

    status = sync.status()
    if config.SYNC and status.get("notes"):
        out.append(_rec(
            "sync-stalled", "medium", "Sync is not converging",
            "; ".join(str(n.get("summary") or "") for n in status["notes"])
            or "the sync beat reported a problem",
            supply="whatever the note names — a remote, a clean checkout, or "
                   "a push of your own commits",
            section="installation", setting="BOARD_SYNC", state="blocked"))

    if accepted_overrides_unrestricted(policy):
        out.append(_rec(
            "overrides-unrestricted", "low",
            "Override is enabled for any git identity",
            "Any identity this checkout is configured with can proceed over "
            "an unmet gate. The record always names who, why and at which "
            "head, and it never forges a pass — but nothing narrows who may "
            "write one.",
            supply="an `actors` list in the gate block, if this board wants "
                   "one",
            section="acceptance", state="configured-untested"))

    # Priority first, then the order they were appended in — which is the
    # order a person should read them. A broken document is why every
    # intent is blocked, so it leads; sorting by name would have put the
    # consequences above the cause.
    order = {"high": 0, "medium": 1, "low": 2}
    return [rec for _, rec in
            sorted(enumerate(out),
                   key=lambda pair: (order[pair[1]["priority"]], pair[0]))]


def accepted_overrides_unrestricted(policy: dict) -> bool:
    return bool(policy["overrides"]["allowed"]
                and not policy["overrides"]["actors"])


def _rec(rec_id: str, priority: str, title: str, why: str, *,
         supply: str, section: str, state: str,
         setting: str | None = None,
         prerequisites: list[str] | None = None) -> dict:
    return {
        "id": rec_id, "priority": priority, "title": title, "why": why,
        "supply": supply, "prerequisites": list(prerequisites or []),
        "section": section, "setting": setting, "state": state,
        # Said on every row rather than once in a heading: a reader who
        # skims one recommendation must not be left wondering whether
        # looking at it did something.
        "inert": "Reading this changes nothing. No setting, permission or "
                 "gate is altered by a recommendation.",
    }


def _any_evidence() -> bool:
    """Has this board ever recorded a verification at all?

    A directory listing, not a read of every record: the recommendation
    only needs to know whether the answer is "none ever".
    """
    try:
        return any(p.suffix == ".json"
                   for p in config.EVIDENCE_DIR.iterdir())
    except OSError:
        return False


# ── the draft, and the four steps that are not one step ────────────────


def document_target() -> Path:
    """The file an apply would write.

    `profiles.document_path()` returns None when there is no document, so
    this resolves where one *would* go: `BOARD_AGENT_PROFILES` as named,
    else `manager/local/profiles.json`. One answer, so a save cannot land
    somewhere a launch would not read.
    """
    configured = (config.AGENT_PROFILES or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = config.TM_ROOT / configured
        return candidate
    return config.LOCAL / "profiles.json"


def _raw_at(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def revision_of(path: Path) -> str:
    """The revision an apply is made against: the digest of the file's
    bytes, which is the same value `document()` reports, or `""` for a
    file that does not exist yet.

    Byte-level on purpose. A revision computed from the parsed object
    would call two different files the same, and the whole point is to
    detect that somebody else's save landed in between.
    """
    raw = _raw_at(path)
    return profiles._digest(raw) if raw is not None else ""


def draft() -> dict:
    """The document as an editable object, with the revision to send back.

    The **whole** parsed object, including keys this editor does not
    understand, because preserving them is only possible if the editor
    was given them.
    """
    path = document_target()
    raw = _raw_at(path)
    parsed, parse_error = None, None
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            parse_error = (f"the document on disk is not valid JSON (line "
                           f"{exc.lineno}, column {exc.colno}: {exc.msg})")
    if not isinstance(parsed, dict):
        if raw is not None and parse_error is None:
            parse_error = ("the document on disk is not a JSON object, so "
                           "there is nothing here to edit field by field")
        parsed = None
    return {
        "path": str(path),
        "identity": profiles._tool_relative(path),
        "exists": raw is not None,
        "revision": revision_of(path),
        "document": parsed if parsed is not None
        else {"v": profiles.PROFILE_VERSION},
        "parseError": parse_error,
        "knownKeys": list(KNOWN_KEYS),
        "unknownKeys": sorted(k for k in (parsed or {})
                              if k not in KNOWN_KEYS),
        "schemaVersion": profiles.PROFILE_VERSION,
        "example": str(config.CORE / "profiles.example.json"),
        "appliesAt": "next-launch",
        "appliesWhy": APPLIES_AT["next-launch"],
    }


def read_draft(payload: dict) -> dict:
    """One submitted document, type-checked before anything else looks at
    it. A non-object is refused here rather than deep inside a resolver."""
    found = payload.get("document")
    if not isinstance(found, dict):
        raise ValueError("the submitted document is not a JSON object — send "
                         "the whole document under \"document\"")
    # `ensure_ascii=False`, because the mask is not ASCII and the default
    # would escape it into `•` and miss it entirely.
    if MASK in json.dumps(found, ensure_ascii=False):
        raise ValueError(
            f"the submitted document contains the redaction mark {MASK!r}. A "
            f"masked value is never written back as the mask: put the real "
            f"value in, or leave the field as it was")
    return found


def validate(payload: dict) -> dict:
    """A draft, read through the one parser and resolved through the one
    resolver — and nothing written.

    `profiles.document_from` is the same function `document_at` uses, so a
    draft gets the diagnostics a launch would get, with the same codes. The
    only thing this adds is the *field* each diagnostic is about, which is
    presentation and is allowed to be missing.
    """
    found = read_draft(payload)
    path = document_target()
    raw = json.dumps(found, indent=2, sort_keys=False) + "\n"
    doc = profiles.document_from(raw, path)
    errors: list[dict] = []
    if doc.get("problem"):
        errors.append({"code": "policy-unreadable", "field": "",
                       "why": doc["problem"]})
    intents = {}
    for intent in profiles.INTENTS:
        try:
            # the reading the readiness inspector takes, so a draft is
            # judged exactly as the board's own policy is
            resolved = doctor.intent_reading(intent, None, policy=doc)
        except (ValueError, OSError) as exc:
            errors.append({"code": "resolve-failed", "field": "",
                           "why": f"'{intent}' could not be resolved: {exc}"})
            continue
        intents[intent] = profiles.summary(resolved)
        for entry in resolved.get("diagnostics") or []:
            errors.append({"code": entry.get("code"),
                           "field": FIELD_OF.get(entry.get("code") or ""),
                           "intent": intent, "why": entry.get("why"),
                           "remedy": entry.get("remedy")})
    gate = profiles.gate_policy(doc)
    for entry in gate["diagnostics"]:
        errors.append({"code": entry.get("code"),
                       "field": FIELD_OF.get(entry.get("code") or ""),
                       "scope": "gates", "why": entry.get("why"),
                       "remedy": entry.get("remedy")})
    seen, unique = set(), []
    for entry in errors:
        key = (entry.get("code"), entry.get("why"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return {
        "ok": not unique,
        "revision": profiles._digest(raw),
        "baseRevision": revision_of(path),
        "errors": unique,
        "intents": intents,
        "gate": profiles.gate_policy_public(gate),
        "wroteNothing": True,
        "unknownKeys": sorted(k for k in found if k not in KNOWN_KEYS),
    }


def preview(payload: dict) -> dict:
    """What applying this draft would change, read from both resolutions.

    Two resolutions, compared — never a prediction. Before is this board's
    document as it stands; after is the draft through the same resolver.
    Everything below is a difference between those two readings.
    """
    found = read_draft(payload)
    path = document_target()
    after_raw = json.dumps(found, indent=2, sort_keys=False) + "\n"
    before = profiles.document()
    after = profiles.document_from(after_raw, path)
    checked = validate(payload)

    before_gate = profiles.gate_policy(before)
    after_gate = profiles.gate_policy(after)
    before_checks = _checks_of(before)
    after_checks = _checks_of(after)

    changes: list[dict] = []
    for name in sorted(set(before_checks) | set(after_checks)):
        was, now = before_checks.get(name), after_checks.get(name)
        if was and not now:
            changes.append({"kind": "check-removed", "what": name,
                            "detail": "bench can no longer run this check, "
                                      "and nothing may require it"})
        elif now and not was:
            changes.append({"kind": "check-added", "what": name,
                            "detail": f"{now['run']} in the {now['root']} "
                                      f"checkout"})
        elif was and now and was["checkId"] != now["checkId"]:
            changes.append({"kind": "check-changed", "what": name,
                            "detail": "its definition changed, so every "
                                      "passing record of it now reads stale"})

    for scope in sorted(profiles.GATE_SCOPES):
        was = _requirement_words(before_gate["scopes"].get(scope))
        now = _requirement_words(after_gate["scopes"].get(scope))
        if was != now:
            changes.append({
                "kind": "gate-changed", "what": scope,
                "before": was, "after": now,
                "detail": _gate_change_detail(scope, was, now)})

    for intent in profiles.INTENTS:
        was = _access_words(before, intent)
        now = _access_words(after, intent)
        if was != now:
            changes.append({"kind": "access-changed", "what": intent,
                            "before": was, "after": now,
                            "detail": "what a "
                                      f"{intent} run may do or read changes "
                                      f"at its next launch"})

    invalidated = [c["what"] for c in changes
                   if c["kind"] in ("check-changed", "check-removed")]
    return {
        "ok": checked["ok"],
        "errors": checked["errors"],
        "baseRevision": revision_of(path),
        "revision": checked["revision"],
        "changes": changes,
        "unchanged": not changes,
        "evidence": {
            "invalidated": invalidated,
            "count": _evidence_count(invalidated),
            "why": ("a check's identity is part of what binds its evidence. "
                    "Change the definition and every recorded pass of it "
                    "reads `stale` — it is not deleted, and it is not "
                    "re-run; the gate simply stops counting it"
                    if invalidated else
                    "no recorded evidence changes meaning"),
        },
        "pending": _pending_acceptance(after_gate),
        "appliesAt": "next-launch",
        "appliesWhy": APPLIES_AT["next-launch"],
        "sideEffects": [
            "one file is written: " + str(path),
            "nothing restarts — no board, no agent, no service",
            "no card moves, nothing merges and nothing is published",
            "no check is re-run and no gate is activated by the write "
            "itself; a gate takes effect the next time the mutation it "
            "guards is attempted",
            "a run already in flight keeps the manifest it launched with",
        ],
        "wroteNothing": True,
    }


def _checks_of(doc: dict) -> dict:
    steps, _ = profiles.document_steps(doc)
    checks, _ = profiles.document_checks(doc, steps)
    return checks


def _requirement_words(entry: dict | None) -> dict:
    if not entry:
        return {"checks": [], "review": "none"}
    return {"checks": sorted(c["requirement"] for c in entry["checks"]),
            "review": entry["review"]}


def _gate_change_detail(scope: str, was: dict, now: dict) -> str:
    if not was["checks"] and was["review"] == "none":
        return (f"{scope} becomes gated: it was `ungated`, where nothing is "
                f"required and nothing is proved")
    if not now["checks"] and now["review"] == "none":
        return (f"{scope} becomes `ungated` — nothing will be required "
                f"before it, and nothing proved by it")
    return f"what {scope} requires changes"


def _evidence_count(names: list[str]) -> int:
    """How many recorded passes the listed checks have, across every card.

    A count rather than a list: the point is the size of what stops
    counting, and the per-card detail is on the card's own evidence sheet.
    """
    if not names:
        return 0
    total = 0
    try:
        collected = taskfiles.collect()
    except (OSError, ValueError):
        return 0
    for group in collected.get("stages", []):
        for task in group.get("tasks", []):
            for record in verify.for_task(task["file"]):
                if record.get("check") in names \
                        and record.get("outcome") == "passed":
                    total += 1
    return total


def _pending_acceptance(after_gate: dict) -> list[dict]:
    """Cards whose acceptance decision the draft would change.

    Read from `gates.evaluate` with the draft's policy — the authority the
    merge itself reads, asked a second time with a different policy rather
    than approximated here. Reading a gate runs nothing.
    """
    out: list[dict] = []
    try:
        collected = taskfiles.collect()
    except (OSError, ValueError):
        return out
    current = profiles.gate_policy()
    for group in collected.get("stages", []):
        if group.get("slug") != "review":
            continue
        for task in group.get("tasks", []):
            scope = "integration" if task.get("isPhase") else "acceptance"
            try:
                was = gates.evaluate(task["file"], scope, task=task,
                                     policy=current)
                now = gates.evaluate(task["file"], scope, task=task,
                                     policy=after_gate)
            except (ValueError, OSError):
                continue
            if was.get("decision") != now.get("decision"):
                out.append({"file": task["file"], "scope": scope,
                            "before": was.get("decision"),
                            "after": now.get("decision"),
                            "why": "this card is waiting in review/, and the "
                                   "change alters what its merge requires"})
    return out


def apply(payload: dict, *, actor: str | None = None) -> dict:
    """Write the draft, once, atomically, and only over the revision the
    editor was shown.

    The order matters and each step is a refusal rather than a repair:

    1. the document must be an object, and must not carry the mask;
    2. it must **validate** — an invalid policy gates everything shut, so
       bench will not write one on an operator's behalf;
    3. the revision it was edited from must still be the current one;
    4. unknown top-level keys from the file on disk are preserved;
    5. the write is `os.replace` of a temporary file in the same
       directory, so a reader never sees a half-written document and a
       failed write leaves the previous file byte for byte;
    6. a sanitized line goes into the history.

    Nothing else happens. This starts no agent, moves no card, merges
    nothing, reruns no check and restarts nothing.
    """
    found = read_draft(payload)
    path = document_target()
    base = str(payload.get("revision") or "")
    current = revision_of(path)
    checked = validate({"document": found})
    if not checked["ok"]:
        raise ValueError(
            "the document was not written: "
            + "; ".join(str(e.get("why")) for e in checked["errors"][:3]))
    if base != current:
        raise Conflict(
            f"the document has changed since this draft was opened "
            f"({'it did not exist then' if not base else base[:12]} → "
            f"{current[:12] or 'it has been removed'}). Nothing was written: "
            f"re-read the settings and apply again, so a newer save is not "
            f"overwritten by an older one.", current)

    existing = {}
    raw_existing = _raw_at(path)
    if raw_existing is not None:
        try:
            parsed = json.loads(raw_existing)
            existing = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            existing = {}
    merged = dict(found)
    preserved = []
    for key, value in existing.items():
        if key not in KNOWN_KEYS and key not in merged:
            merged[key] = value
            preserved.append(key)

    raw = json.dumps(merged, indent=2, sort_keys=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".bench-{os.getpid()}.tmp")
    try:
        temp.write_text(raw, encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise OSError(
            f"the document could not be written ({exc}). The previous "
            f"settings are intact: nothing was changed.") from exc

    entry = _record_change(path, before=current, after=revision_of(path),
                           document=merged, previous=existing, actor=actor)
    return {
        "revision": revision_of(path),
        "previousRevision": current,
        "path": str(path),
        "identity": profiles._tool_relative(path),
        "preservedKeys": preserved,
        "history": entry,
        "draft": draft(),
        "appliesAt": "next-launch",
        "appliesWhy": APPLIES_AT["next-launch"],
        "didNot": ["start an agent", "move a card", "merge anything",
                   "publish anything", "re-run a check", "restart the board",
                   "change what a run already in flight is allowed to do"],
    }


# ── a sanitized history ────────────────────────────────────────────────


def history_path() -> Path:
    return config.STATE / "settings" / "history.jsonl"


def _record_change(path: Path, *, before: str, after: str, document: dict,
                   previous: dict, actor: str | None) -> dict:
    """One line per applied change: enough to identify what moved, when,
    and by whom **where that is actually known**.

    Identity is `taskfiles.actor_name()` — this checkout's git identity,
    the same one a claim and an override are recorded against. There is no
    authentication on this board, so an unknown identity is recorded as
    unknown rather than as a name somebody typed: a forged attribution is
    worse than an absent one.

    No values. The keys that changed, and counts — a settings file can
    hold a path out of somebody's home directory, and the history is not
    the place to copy one.
    """
    entry = {
        "v": VERSION,
        "at": time.time(),
        "actor": actor or None,
        "actorKnown": bool(actor),
        "actorWhy": (None if actor else
                     "this board records no authenticated identity, and git "
                     "config named none — so none is claimed"),
        "document": profiles._tool_relative(path),
        "before": before or None,
        "after": after,
        "changedKeys": sorted(
            k for k in set(document) | set(previous)
            if document.get(k) != previous.get(k)),
        "counts": {
            "checks": len(document.get("checks") or []),
            "prepare": len(document.get("prepare") or []),
            "context": len(document.get("context") or []),
            "profiles": len(document.get("profiles") or {}),
            "gatedScopes": sorted(
                scope for scope in profiles.GATE_SCOPES
                if isinstance((document.get("gates") or {}), dict)
                and isinstance((document.get("gates") or {}).get(scope), dict)),
        },
    }
    entry["recorded"] = True
    try:
        target = history_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        # The change landed; the note about it did not. Losing the note
        # must not undo a write that already succeeded, and saying so is
        # better than pretending it was recorded.
        entry["recorded"] = False
    return entry


def history() -> list[dict]:
    """The change log, newest first, capped. Sanitized when it was written,
    and scrubbed again on the way out."""
    out: list[dict] = []
    try:
        raw = history_path().read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines()[-HISTORY_CAP:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(json.loads(scrub(json.dumps(entry))))
    out.reverse()
    return out


# ── the whole page ─────────────────────────────────────────────────────


def page() -> dict:
    """Everything the Settings page reads, in one request.

    Assembling it runs no project program, launches no model, invokes no
    adapter hook, pins nothing, provisions no checkout and writes nothing.
    It resolves the policy, reads records already on disk, and asks git for
    the tool's own revision.
    """
    doc = profiles.document()
    return {
        "v": VERSION,
        "generated": time.time(),
        "location": LOCATION,
        "states": dict(STATES),
        "overview": overview(doc),
        "recommendations": recommendations(doc),
        "verification": verification(doc),
        "acceptance": acceptance(doc),
        "access": access(doc),
        "diagnostics": diagnostics(),
        "installation": installation(),
        "ci": ci(doc),
        "settings": catalog(),
        # Settings → Agents: harness, model and prompt templates per
        # purpose, with their own draft, preview and apply (task 77)
        "agents": agentsettings.page(),
        "editing": {
            "draft": draft(),
            "steps": [
                {"step": "edit", "what": "change the draft. Nothing is "
                 "written, and leaving the page discards it"},
                {"step": "validate", "what": "the draft through bench's own "
                 "parser and resolver, with field errors. Writes nothing"},
                {"step": "preview", "what": "what would change: "
                 "requirements, access, evidence that stops counting, and "
                 "cards waiting in review/. Writes nothing"},
                {"step": "apply", "what": "one atomic write of one file, "
                 "refused if the revision moved under you"},
            ],
            "endpoints": {
                "draft": "GET /api/settings",
                "validate": "POST /api/settings/validate",
                "preview": "POST /api/settings/preview",
                "apply": "POST /api/settings/apply",
            },
            "envIsReadOnly": (
                "manager/local/.env is shown here and not edited here — "
                "except its agent keys, which Settings → Agents edits because "
                "every launch reads them fresh. config reads the rest once "
                "when the board starts, and the real environment beats it — "
                "so a control on this page could lose to a higher-precedence "
                "source or appear to take effect when it had not. Each row "
                "says which source won and how to change it there."),
            "secrets": (
                "Secrets are not managed here. Credential-shaped names and "
                "values are masked in every response and in the page, and a "
                "masked value is refused if it is submitted back — so a mask "
                "can never be written over the value it hid."),
        },
        "history": history(),
    }


def board_summary() -> dict:
    """The cheap reading the board's own state payload carries, so the
    header can say whether Settings has anything to shout about without
    resolving the whole page on every state load.

    One document read and one gate-policy resolution. Deliberately not the
    recommendations: those resolve all three intents and hash the approved
    inputs.
    """
    doc = profiles.document()
    policy = profiles.gate_policy(doc)
    steps, step_trouble = profiles.document_steps(doc)
    checks, check_trouble = profiles.document_checks(doc, steps)
    return {
        "configured": doc.get("kind") == "file",
        "problem": doc.get("problem"),
        "invalid": bool(doc.get("problem") or step_trouble or check_trouble
                        or policy["blockers"]),
        "checks": len(checks),
        "gated": sorted(scope for scope, entry in policy["scopes"].items()
                        if entry["gated"]),
    }


