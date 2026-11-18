"""The evidence record: what a verification is, on disk, and the words it
is read in.

`verify` runs a check; this module owns what the run leaves behind and
nothing else. One JSON file per verification under `local/state/evidence/`
(written when the request is accepted and rewritten when it ends — the
final write durable, because it attests), the check's whole output beside
it, the reuse records a second asker leaves, and the closed vocabulary
every reader branches on: the outcomes, their reasons and the remedy for
each way a pass stops holding.

It is also the owner of the **evidence epoch**: a counter bumped on every
store, which is how a memoised board reading knows a verification has
ended since it was taken (`epoch()`, read by `gates` and `delivery`).

Moved out of `verify.py` verbatim by task 121 (WP12c). What changed in
the move is said where it changed: the final write goes through `records`
durably (a reader sees the old file or the new one, never a torn one),
and a log is scrubbed by `redact`'s one table rather than a copy of it —
which masks strictly more. This module is inside the runner revision
(`resultidentity.RUNNER_FILES`): storage integrity is part of what a
recorded verdict is worth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import config
import records
import redact
import state
import verifierreport


# ── the outcome vocabulary ─────────────────────────────────────────────
#
# Closed, and every word means one thing a reader would do differently
# about. `failed` is reserved for a check that really ran and really said
# no: a wrapper that could not start, a timeout and a cancellation are not
# test failures and are never recorded as any.

OUTCOMES = {
    "running": "bench is running it now",
    "preparing": "bench is running this check's preparation steps",
    "passed": "the check ran and exited zero",
    "failed": "the check ran and exited non-zero — the project's own verdict",
    "denied": "it was refused before anything ran",
    "prepare-failed": "a required preparation step failed, so the check did "
                      "not run",
    "not-run": "declared for this run and never asked for",
    "cancelled": "stopped on purpose, by a person or by the run ending",
    "stale": "it passed, and what it was about has changed since",
    "error": "bench could not get an answer — the executable, the timeout or "
             "this board stopping",
}

# Outcomes that are still moving. Everything else is what the record says
# for good, subject only to `freshness()`.
LIVE = ("running", "preparing")

# Why, in one slug, so a consumer branches on data rather than on prose.
REASONS = {
    "exit-zero": "the check exited 0",
    "exit-nonzero": "the check exited non-zero",
    "signal": "the check was killed by a signal",
    "timeout": "the check was still running when its timeout expired",
    "no-executable": "the check's executable could not be run",
    "runner-error": "bench itself failed while running the check",
    "unknown-check": "no check of that name is available to this run",
    "unknown-scope": "no scope of that name is declared on that check",
    "root-unavailable": "the execution root this check needs does not exist",
    "root-foreign": "the directory the root names is a checkout of something "
                    "else",
    "profile-blocked": "the run's own execution profile does not resolve",
    "no-profile": "there is no recorded profile for the run that asked",
    "prepare-exit": "a required preparation step exited non-zero",
    "prepare-error": "a required preparation step could not be run",
    "cancelled-by-request": "a person cancelled it",
    "cancelled-with-run": "the run that asked for it was stopped",
    "board-restart": "this board stopped while it was running",
    "never-asked": "nothing asked for it",
    "source-changed": "the source it ran against has changed since",
    "policy-changed": "the policy or the check's definition has changed since",
    "program-changed": ("the program the check or a preparation step ran has "
                        "changed at its path since, or was never identified"),
    "runner-changed": "bench's own runner has changed since it ran",
    "head-changed": "the pull request's head has moved on since, so the "
                    "result is about an earlier revision",
    "pr-ended": "the pull request has been merged or closed since, so the "
                "result is historical rather than current",
    # The dimensions of a result identity (design §4.1), each its own slug
    # because each is a different thing to go and look at. Legacy records
    # — written before result identities — never produce these; they keep
    # `policy-changed` and `context-changed`.
    "check-changed": "the check's own definition has changed since it ran",
    "scope-changed": "the scope it ran under now runs different arguments",
    "prepare-changed": "a preparation step it needed has changed since",
    "input-changed": "an input the check declares has changed since",
    "env-changed": "the environment the check was given has changed since",
    "identity-unknown": "the record's result identity cannot be read, so it "
                        "cannot be shown to hold",
    "input-unavailable": "a required input the check declares could not be "
                         "read, so nothing was run",
    "env-key-unusable": "the machine-local key that binds declared "
                        "environment values cannot be used",
    # A check that opts into the verifier report contract (task 135):
    # which row of its consistency rule decided the outcome. Owned by
    # `verifierreport`, beside the rule that produces them.
    **verifierreport.REASONS,
}

# What to do about each way a result stops holding, said once so the gate,
# the evidence sheet and an agent's diagnostics name the same remedy. The
# legacy reasons keep the sentence they always had at the gate.
STALE_REMEDIES = {
    "check-changed": ("Run the check again: its command, root, time limit or "
                      "declared inputs or environment changed since it "
                      "passed."),
    "scope-changed": ("Run the check again over the scope as it is now "
                      "declared."),
    "prepare-changed": ("Run the check again: a preparation step it needs "
                        "runs something different now."),
    "program-changed": ("Run the check again: a program it invoked was "
                        "replaced at the same path."),
    "input-changed": ("Run the check again with the input as it is now — "
                      "or, if the change is unintended, put the input back."),
    "env-changed": ("Run the check again in the environment as it is now. "
                    "Declared values are bound by a keyed hash on this "
                    "machine, so a replaced or lost key also needs one "
                    "fresh run."),
    "runner-changed": "Bench was updated since this ran — run checks again.",
    "identity-unknown": ("Run the check again: this record's identity cannot "
                         "be read, and an unknown identity never counts as a "
                         "pass."),
    "source-changed": "Commit, then run checks against the code as it is.",
}

# The source half of a freshness reading. Everything else a new record can
# report is a change to what the check *is* — its definition, programs,
# inputs or environment — which is what makes a record outdated rather
# than merely about other code.
SOURCE_REASONS = ("source-changed", "head-changed", "pr-ended")


# ── identities and paths ───────────────────────────────────────────────

_ID_OK = re.compile(r"^[A-Za-z0-9._:@-]{1,160}$")


def is_evidence_id(value) -> bool:
    """Whether a value has the shape of an evidence id — the one question
    another module may ask before handing an id to `read()`. Shape only:
    it says nothing about whether such a record exists."""
    return isinstance(value, str) and bool(_ID_OK.match(value))


def slug(evidence_id: str) -> str:
    """One evidence id as one filename. The ids carry `:` and `@`, which
    are legal in a POSIX name and awkward everywhere else."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", evidence_id)


def path_of(evidence_id: str) -> Path:
    return config.EVIDENCE_DIR / f"{slug(evidence_id)}.json"


def log_path_of(evidence_id: str) -> Path:
    return config.EVIDENCE_DIR / "logs" / f"{slug(evidence_id)}.log"


# ── the evidence epoch ─────────────────────────────────────────────────
#
# Asking whether a pass is still fresh costs git, so readings are memoised
# (`staleness`, `verify.board`, `gates.board`, `delivery`). The epoch is
# what makes those memos safe: a terminal record never changes, so the
# only things that can move an answer are the source and the policy —
# which is what each memo's TTL is for — and any *new* record bumps the
# epoch, so a cached reading can never outlive a verification that has
# since ended.

_EPOCH = 0


def epoch() -> int:
    """How many times evidence has been stored since this board started.
    A reader puts it in a memo's key; nothing else may be read off it."""
    with state.LOCK:
        return _EPOCH


# ── the evidence record ────────────────────────────────────────────────


def store(record: dict) -> dict:
    """Write the record, every time it changes. Twice at least — once when
    the request is accepted and once when it ends — because an evidence
    file that only appeared at the end would be missing in exactly the
    case a reader most needs it: while it is running, and after a crash.

    Atomic always (`records`: a reader sees the old record or the new one,
    never a torn file), and durable once the outcome is final: a record
    that attests a verdict is flushed with its directory before anyone is
    told it."""
    global _EPOCH
    with state.LOCK:
        _EPOCH += 1
    stored = {k: v for k, v in record.items() if k not in ("proc", "cancelling")}
    if stored.get("outcome") in LIVE:
        records.write_json(path_of(record["id"]), stored, default=str)
    else:
        records.write_json(path_of(record["id"]), stored, default=str,
                           durable=True)
    return stored


def read(evidence_id: str) -> dict | None:
    """One stored record. Reading is reading: nothing is re-run, nothing is
    re-resolved, and opening a detail panel costs one file read."""
    if not _ID_OK.match(evidence_id or ""):
        return None
    try:
        data = json.loads(path_of(evidence_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def all_evidence() -> list[dict]:
    """Every record this board holds, newest first."""
    found = []
    if not config.EVIDENCE_DIR.is_dir():
        return found
    for path in config.EVIDENCE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            found.append(data)
    found.sort(key=lambda r: r.get("requested") or 0, reverse=True)
    return found


def for_task(filename: str) -> list[dict]:
    return [r for r in all_evidence() if r.get("task") == filename]


def for_agent(agent_id: str) -> list[dict]:
    return [r for r in all_evidence() if r.get("agent") == agent_id]


# ── secrets never reach a log a reader opens ───────────────────────────
#
# A check's own output is the project's, and a project's test suite prints
# what it likes. The record names paths, revisions and hashes and never
# content; the log is the one place bytes bench did not write get through,
# so it is scrubbed on the way out rather than trusted on the way in.

MARKER = "«redacted»"


def scrub(text: str) -> str:
    """The log as a reader may see it, with anything shaped like a
    credential replaced by a marker. Not a proof — no pattern set is —
    but the difference between a token printed by an unlucky test and a
    token in a panel somebody screenshots.

    `redact`'s one table, with this module's marker (task 121): the union
    of every table bench kept, so it masks strictly more than the copy it
    replaced, and a key=value secret keeps its `=` (`api_key=«redacted»`)."""
    return redact.scrub(text, MARKER)


def log_text(evidence_id: str, cap: int = 200_000) -> dict:
    """One verification's output, scrubbed and clipped to its tail — which
    is where a failing check says what failed."""
    record = read(evidence_id)
    if record is None:
        raise ValueError(f"no evidence {evidence_id}")
    path = Path(record.get("log") or log_path_of(evidence_id))
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"id": evidence_id, "text": "", "bytes": 0,
                "why": f"the log could not be read: {exc}"}
    clipped = len(raw.encode("utf-8", "replace")) > cap
    return {"id": evidence_id, "text": scrub(raw[-cap:]),
            "bytes": len(raw), "clipped": clipped,
            "scrubbed": True, "why": None}


# ── reuse records ──────────────────────────────────────────────────────
#
# What a second asker leaves when an equal, current answer was already on
# record (`verify`'s reuse route). Never evidence: `all_evidence` does not
# list them and no gate reads them.


def reuse_dir() -> Path:
    return config.EVIDENCE_DIR / "reuse"


def store_reuse(reuse: dict) -> None:
    """Write one reuse record, atomically. The caller holds the board lock
    its decision was made under, so this does not flush to disk: a reuse
    record is not evidence and no gate reads it, and an fsync under that
    lock would stall every other reader of the registry."""
    records.write_json(reuse_dir() / f"{slug(reuse['id'])}.json", reuse,
                       default=str)


def read_reuse(reuse_id: str) -> dict | None:
    if not _ID_OK.match(reuse_id or ""):
        return None
    try:
        data = json.loads((reuse_dir() / f"{slug(reuse_id)}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def reuses_for_task(filename: str) -> list[dict]:
    """Every reuse record for one card, newest first. Not evidence: the
    gate never reads these, and `all_evidence` never lists them."""
    found = []
    if not reuse_dir().is_dir():
        return found
    for path in reuse_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("task") == filename:
            found.append(data)
    found.sort(key=lambda r: r.get("requested") or 0, reverse=True)
    return found
