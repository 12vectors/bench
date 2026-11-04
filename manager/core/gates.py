"""The gate: integration and acceptance, on current evidence.

`plans` says which checkout a run works in. `profiles` says what it may do
there. `verify` says whether the project's own checks passed and against
what. `reviews` says which revision a PR review was of. This module is the
question all four leave open: **may this merge happen?**

Three things used to answer it, and none of them is evidence:

- a process exited zero, which is the thing being judged reporting on
  itself,
- commits appeared on a branch, which says work was done and nothing about
  whether it works,
- and no CI result came back, which the phase runner read as "advance" so
  that a project without CI could not deadlock — honest then, and exactly
  the case a configured gate exists to stop.

So a board that declares a gate gets one rule, and it is narrow: **the
mutation runs when bench's own recorded evidence says `passed` for every
required check, against the exact commit about to be merged, and when the
review policy the operator chose is satisfied by a record that genuinely
says what it is read as saying.** Anything else is reported — by name,
with the remedy — and the mutation does not run.

## What it never does

- **It never produces evidence.** Only `verify.py` does. A gate reads
  records, compares them with the head, and says. It runs no check, moves
  no card, touches no branch and publishes nothing, so asking is free and
  a refusal costs exactly one read.
- **It never relaunches an agent.** An unmet gate is a human decision
  point, for a dead run's reasons: nothing retries by itself, and an
  outage would make an automatic rerun a thundering herd. The remedy
  names the rerun path (`↻` per check in the evidence sheet); it does not
  take it.
- **It never reads a comment as an approval.** GitHub's formal review
  state and a Bench verdict are two different records of two different
  things, and `COMMENTED` is neither. The account that opened a pull
  request cannot approve it — GitHub refuses — so a board whose reviewer
  runs as that account has a *limitation*, which is surfaced as one
  rather than papered over by accepting the comment it fell back to.
- **It never switches accounts or asks for a credential.** A policy that
  wants a second reviewer identity and has not got one reads unmet.
- **It never resolves a contradiction by guessing.** Evidence from
  another commit, a verdict about another head, an override authorising
  another head: each is reported as what it is.

## Two records of its own, and both are machine-local

`verify` owns the evidence and `reviews` owns the checkouts. This module
owns two smaller things, under `local/state/gates/`:

- **the Bench verdict** — what bench's own PR reviewer found, bound to the
  head it was pinned to, written down independently of anything GitHub
  says. A board with no `gh` still has verdicts; a board whose formal
  review state says `COMMENTED` still has the verdict the reviewer
  actually reached.
- **the overrides** — one record per authorised proceed-anyway, naming
  the actor, the reason, the exact head and the exact gates that were
  unmet. An override never forges evidence and never authorises a future
  head: the head is in the record, and the next commit is a different
  question.

Both hold this machine's paths and this machine's reading of a remote, so
they live with the tool and never in the work state's shared history —
the law `state/plans/`, `state/manifests/`, `state/evidence/` and
`state/reviews/` already follow.

## Where it is enforced

Not in the UI. The page reads the same decision the mutation reads, and
the mutation re-reads it immediately before the irreversible step:

- `phases._member_state` holds a member out of `ready` while its gate is
  unmet, and `phases._merge_member` asks again with the branch tip in
  hand before the merge runs.
- `github._complete` asks before it parks the drive, and again
  immediately before the merge — so a push that lands in between is a
  refusal rather than a stale-evidence window.

A replica evaluates and renders exactly as the actor's board does, and
advances nothing: the actor rule is `phases._mine`'s and the
merge-and-clean-up is a person's click. Nothing here reaches a remote.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import config
import profiles
import state
import verify

# One decision word per answer, and the word carries the meaning rather
# than the colour — the law the verification outcomes are written under.
#
#   ungated      this board requires nothing here. Not a pass
#   allowed      every requirement is met by a record that says so
#   pending      a required check is running right now; nothing to decide
#   blocked      a requirement is unmet, and the mutation does not run
#   overridden   an authorised person proceeded over named unmet gates
DECISIONS = {
    "ungated": ("no gate is configured for this, so nothing is required and "
                "nothing is proved — the mutation runs as it always did"),
    "allowed": ("every requirement is met by a record bound to the commit "
                "about to be merged"),
    "pending": ("a required check is running — the answer is not in yet, and "
                "nothing is decided from its absence"),
    "blocked": ("a requirement is unmet, so the mutation does not run. The "
                "card, its branch, its worktree and every record are left "
                "exactly as they are"),
    "overridden": ("an authorised person proceeded over gates that are still "
                   "unmet. This is not a pass, and the record says who, why "
                   "and at which head"),
}

# One word per requirement, for the same reason.
STATES = ("met", "unmet", "pending", "overridden")

# Why a requirement is not met. A slug per cause, because "it failed" is
# not one fact and a reader branching on the reason must not have a test
# failure, a denied permission and a check nobody asked for collapse into
# one colour.
REASONS = {
    "not-run": ("nothing has asked bench for this check, so there is no "
                "evidence either way. An absent record is not a pass"),
    "failed": "the check ran and said no",
    "prepare-failed": ("the check never ran: a preparation step it requires "
                       "would not complete"),
    "denied": "the request to run the check was refused",
    "cancelled": "the verification was cancelled before it reached a verdict",
    "error": "the verification did not reach a verdict",
    "stale": ("the evidence passed, and what it was about has changed since "
              "— so it says nothing about the code here now"),
    "other-commit": ("the only passing evidence ran against a different "
                     "commit from the one about to be merged"),
    "source-dirty": ("the passing evidence ran with uncommitted work in the "
                     "checkout, so it was not about the commit that would "
                     "merge"),
    "source-unbound": ("the evidence could not be bound to a source, so it "
                       "is not reusable as proof of anything"),
    "no-verdict": ("no Bench review verdict has been recorded for this card"),
    "verdict-changes": ("bench's own review asked for changes"),
    "verdict-other-head": ("the Bench verdict on record is about a different "
                           "head"),
    "no-approval": ("the pull request has no formal GitHub review state of "
                    "APPROVED"),
    "changes-requested": ("a formal GitHub review asks for changes"),
    "approval-other-head": ("the formal approval on record was given for a "
                            "different commit"),
    "same-account": ("the only review activity is from the account that "
                     "proposed the head, and GitHub does not accept an "
                     "approval from it — the comment it fell back to is not "
                     "a formal review state"),
    "no-separate-reviewer": ("every formal approval is from the account that "
                             "proposed the head, and this policy asks for a "
                             "separate reviewer identity"),
    "head-unknown": ("bench cannot name the commit this would merge, so "
                     "there is nothing to bind evidence to"),
    "policy-refused": ("this board's gate policy does not resolve, so no "
                       "gate can be evaluated and none is assumed met"),
}

# How a reader gets from an unmet requirement to a met one. Per reason,
# because the remedy for "nobody asked" and the remedy for "it failed"
# are different sentences and a single generic one would be useless.
REMEDIES = {
    "not-run": ("Ask bench to run it: the ◫ evidence sheet has an armed ↻ "
                "per check, which runs it in the card's own checkout and "
                "files a new record. Nothing relaunches an agent."),
    "failed": ("Read the check's log in the ◫ evidence sheet, fix what it "
               "found, commit, and run it again. A failing check is the one "
               "outcome a gate exists for."),
    "prepare-failed": ("The dependency step is the thing to fix, not the "
                       "test: its own row in the evidence sheet says how it "
                       "ended."),
    "denied": ("The request was refused before anything ran — the ⚿ profile "
               "sheet says by what. Fix the policy, then ask again."),
    "cancelled": "Ask for the check again; a cancellation decided nothing.",
    "error": ("Ask for the check again. A record left running by a board "
              "that restarted is closed as an error and never re-run by "
              "itself."),
    "stale": ("Run the check again against the code as it stands. Nothing "
              "re-uses a pass whose binding has gone."),
    "other-commit": ("Run the check again now that the commit is what it is. "
                     "A new commit is a new question, which is the whole "
                     "reason evidence is bound to one."),
    "source-dirty": ("Commit the work, then ask for the check — a gate is "
                     "about the commit that merges, and a checkout with "
                     "uncommitted files in it is not that commit."),
    "source-unbound": ("The record says why it could not be bound (usually "
                       "too much untracked work in the checkout). Clean the "
                       "checkout and run it again."),
    "no-verdict": ("Run ◔ review PR on this card. Its verdict is recorded by "
                   "bench against the head it was pinned to, whatever GitHub "
                   "accepts."),
    "verdict-changes": ("Address what the review asked for with ↻ act on PR, "
                        "then review again."),
    "verdict-other-head": ("Review the current head: a verdict is about the "
                           "commit it was pinned to and is never retracted, "
                           "but it does not travel to the next one."),
    "no-approval": ("Get a formal review on the pull request. A comment — "
                    "however it is worded — is not a review state, and bench "
                    "will not read one as one."),
    "changes-requested": ("Address the review, push, and ask again. A later "
                          "approval supersedes it; a comment does not."),
    "approval-other-head": ("The approval was given for an earlier commit. "
                            "Ask the reviewer to approve the head as it "
                            "stands."),
    "same-account": ("This is a configuration fact, not a fault of the run: "
                     "GitHub refuses an approval from the account that opened "
                     "the pull request. Either gate on a Bench verdict "
                     "(\"review\": \"bench\"), which bench records itself, or "
                     "have a second identity review it. Bench never switches "
                     "accounts or asks for another credential to satisfy "
                     "this."),
    "no-separate-reviewer": ("Have somebody other than the account that "
                             "proposed the head approve it, or choose a "
                             "review policy that does not ask for a separate "
                             "identity."),
    "head-unknown": ("The branch this would merge does not resolve in the "
                     "application checkout. There is nothing to gate yet."),
    "policy-refused": ("Fix the gate block in the profile document — every "
                       "refusal is named with its remedy in the ⚕ readiness "
                       "report and the ⚖ sheet."),
}

# What a gate requires of a review, in the words `profiles` validated.
REVIEW_POLICIES = profiles.REVIEW_POLICIES

# Where the two records this module owns live. Machine-local, for the
# reason every other record under state/ is.
_LOCK = threading.Lock()

# What the PR poll last saw of GitHub's formal review state, per card.
# Kept in memory *and* on disk: in memory because the poll feeds it and
# the page reads it, on disk because an approval's binding to a commit
# must survive a restart — see `observe_reviews`.
REVIEW_STATE: dict[str, dict] = {}

_ID_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _short(commit: str | None, fallback: str = "an unrecorded commit") -> str:
    """A commit as a diagnostic names it: twelve characters, or words when
    there is none. Written once because a gate's sentences are full of
    them and `None[:12]` is the shape of bug that reads as a blank."""
    return str(commit)[:12] if commit else fallback

# The compact per-card reading, memoised the way `verify.board` is: one
# epoch bumped by every write here, and a time bucket, because a decision
# can change without anything being written (the branch moved under it).
_BOARD: tuple | None = None
_EPOCH = 0
_TTL = 15.0


def _bump() -> None:
    global _BOARD, _EPOCH
    with _LOCK:
        _EPOCH += 1
    _BOARD = None


def forget_cached() -> None:
    """Drop the memoised board reading. Nothing on the serving path needs
    it — the epoch and the TTL do that work — but a test, and anything
    that changes a root under the board's feet, needs the door."""
    _bump()


# ── where the two records live ─────────────────────────────────────────


def dir_for() -> Path:
    return config.GATES_DIR


def _path(filename: str, kind: str) -> Path:
    stem = filename[:-3] if filename.endswith(".md") else filename
    if not _ID_OK.match(stem or ""):
        raise ValueError(f"not a card this board can record against: {filename!r}")
    return dir_for() / f"{stem}.{kind}.json"


def _read_json(path: Path, fallback):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str),
                    encoding="utf-8")


# ── application git, read once per pass ────────────────────────────────


def _tips() -> dict[str, str]:
    """Every local branch and the commit it points at, in one call.

    One `for-each-ref` rather than a `rev-parse` per card: the board reads
    this on every state load, and a subprocess per card is what makes a
    panel expensive enough that somebody caches the *answer* instead of
    the lookup.
    """
    result = config._app_git("for-each-ref", "--format=%(refname:short) %(objectname)",
                             "refs/heads")
    found: dict[str, str] = {}
    if result.returncode != 0:
        return found
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[parts[0]] = parts[1]
    return found


def scope_for(task: dict) -> str:
    """Which gate guards *accepting* this card. A phase card's acceptance
    is its integration head's — the revision every member was merged into,
    which is a different commit from any of them and is the whole reason
    the two scopes are separate words."""
    return "integration" if task.get("isPhase") else "acceptance"


def head_for(filename: str, scope: str, tips: dict[str, str] | None = None) -> dict:
    """The commit this gate is about: the tip of the branch the guarded
    mutation would merge.

    Read out of application git rather than taken from a caller, because
    the binding is the whole point — a decision made about a commit that
    is not the one about to move is the stale-evidence window this exists
    to close.
    """
    stem = filename[:-3] if filename.endswith(".md") else filename
    tips = _tips() if tips is None else tips
    if scope == "integration":
        ref = config.PHASE_BRANCH_PREFIX + stem
    elif scope == "member":
        ref = config.TASK_BRANCH_PREFIX + stem
    else:
        ref = (config.PHASE_BRANCH_PREFIX + stem
               if (config.PHASE_BRANCH_PREFIX + stem) in tips
               else config.TASK_BRANCH_PREFIX + stem)
    return {"ref": ref, "commit": tips.get(ref), "known": ref in tips}


# ── the Bench review verdict, kept apart from GitHub's ─────────────────


VERDICTS = ("APPROVE", "REQUEST CHANGES")


def record_verdict(filename: str, *, verdict: str, head: str | None,
                   agent: str | None = None, actor: str = "agent",
                   review_id: str | None = None, note: str = "",
                   published: str | None = None) -> dict:
    """What bench's own reviewer found, written down as bench's own fact.

    Independent of GitHub by construction: this is recorded from the
    reviewer's marker and the head bench pinned it to, whether or not
    `gh pr review` accepted anything. That separation is the point — a
    board whose reviewer runs as the account that opened the pull request
    cannot publish a formal approval, and the verdict it reached is still
    a verdict, still bound to a commit, and still readable a month later.

    The head is the binding, so a verdict is appended rather than
    replacing: the record of what was found at an earlier commit is not
    retracted by a later one, exactly as a PR review's own heading in the
    task file is not.
    """
    word = str(verdict or "").strip().upper()
    if word not in VERDICTS:
        raise ValueError(f"a Bench verdict is one of {', '.join(VERDICTS)} — "
                         f"not {verdict!r}")
    path = _path(filename, "verdicts")
    found = _read_json(path, [])
    record = {
        "v": 1, "task": filename, "verdict": word,
        "head": str(head or "") or None,
        "agent": agent, "actor": actor, "reviewId": review_id,
        "note": note or "", "recorded": time.time(),
        # Whether the verdict also reached GitHub as a formal review
        # state, and what happened if it did not. Recorded here because
        # "bench found this" and "GitHub holds this" are two facts and
        # this module's whole job is not to let one read as the other.
        "published": published,
    }
    with _LOCK:
        found = _read_json(path, [])
        found.append(record)
        _write_json(path, found[-50:])
    _bump()
    return record


def verdicts_for(filename: str) -> list[dict]:
    """Every Bench verdict recorded for this card, newest first."""
    found = _read_json(_path(filename, "verdicts"), [])
    return sorted((r for r in found if isinstance(r, dict)),
                  key=lambda r: r.get("recorded") or 0, reverse=True)


def verdict_at(filename: str, head: str | None) -> dict | None:
    """The newest Bench verdict about this exact head, or None."""
    if not head:
        return None
    for record in verdicts_for(filename):
        if record.get("head") == head:
            return record
    return None


# ── GitHub's formal review state, observed and bound to a commit ───────


def needs_github_reviews() -> bool:
    """Does any configured gate actually care about GitHub's formal review
    state? Asked by the PR poll before it spends an extra API call per
    card: a board that gates on nothing, or gates only on a Bench verdict,
    pays exactly what it paid before."""
    try:
        policy = gate_policy()
    except (OSError, ValueError):
        return False
    return any(entry["review"] in ("github", "separate-reviewer")
               for entry in policy["scopes"].values())


def observe_reviews(filename: str, *, reviews: list[dict], author: str | None,
                    head: str | None, authority: str = "api") -> dict:
    """What GitHub says the formal review state is, bound to commits.

    `reviews` is the REST shape — one entry per submitted review, with the
    author's login, the state, and **the commit it was of**. That last
    field is why this reads the REST endpoint rather than the poll's
    `gh pr view`: an approval with no commit on it could only ever be
    bound by guessing, and a gate that guessed would be worse than none.

    Two readings come out, and they stay apart:

    - **the formal state** — the strongest thing GitHub holds, per
      reviewer, with the commit each was given for. `COMMENTED` is not
      one: it is recorded, and it is never counted as an approval or as a
      change request.
    - **the limitation** — whether the only review activity is from the
      account that proposed the head. GitHub refuses an approval from it,
      so a board reviewing its own pull requests has a configuration
      fact to be told about rather than a verdict to be given.
    """
    author_login = (author or "").lower()
    seen: list[dict] = []
    for entry in reviews or []:
        if not isinstance(entry, dict):
            continue
        login = str(((entry.get("user") or {}).get("login")
                     if isinstance(entry.get("user"), dict)
                     else entry.get("author") or "")).strip()
        word = str(entry.get("state") or "").strip().upper()
        seen.append({
            "id": str(entry.get("id") or ""),
            "login": login,
            "state": word,
            # The commit the review was of. GitHub gives it on the REST
            # review object; without it a review binds to nothing and is
            # reported that way rather than assumed current.
            "commit": str(entry.get("commit_id") or "") or None,
            "submitted": str(entry.get("submitted_at") or "") or None,
            "self": bool(login) and login.lower() == author_login,
        })
    formal = [r for r in seen if r["state"] in ("APPROVED", "CHANGES_REQUESTED")]
    comments = [r for r in seen if r["state"] == "COMMENTED"]
    own_comments = [r for r in comments if r["self"]]
    limitation = None
    if own_comments and not [r for r in formal if r["self"]]:
        limitation = {
            "kind": "same-account",
            "login": (own_comments[0]["login"] or author or "the same account"),
            "why": ("GitHub does not accept an approval or a change request "
                    "from the account that opened the pull request, so a "
                    "review posted as that account arrives as a COMMENTED "
                    "review however it is worded. Bench records it as a "
                    "comment and reads it as one."),
        }
    found = {
        "task": filename, "author": author, "head": head,
        "authority": authority, "observed": time.time(),
        "reviews": seen, "formal": formal, "comments": comments,
        "limitation": limitation,
    }
    with _LOCK:
        REVIEW_STATE[filename] = found
        _write_json(_path(filename, "reviews"), found)
    _bump()
    return found


def github_reviews(filename: str) -> dict | None:
    """The formal review state this board last observed — from memory, or
    off disk after a restart. Persisted because a commit-bound approval
    that a restart forgot would be an approval this board re-bound to
    whatever the head had become, which is exactly the drift towards a
    pass nothing here is allowed to do."""
    found = REVIEW_STATE.get(filename)
    if found is not None:
        return found
    stored = _read_json(_path(filename, "reviews"), {})
    if stored:
        REVIEW_STATE[filename] = stored
        return stored
    return None


def forget_reviews(filename: str) -> None:
    """Drop what was observed about one card — used when a card's PR state
    is dropped, so nothing outlives the pull request it was about."""
    with _LOCK:
        REVIEW_STATE.pop(filename, None)
    _bump()


# ── evaluating one gate ────────────────────────────────────────────────


def gate_policy() -> dict:
    """This board's gate requirements. One call into `profiles`, which is
    the only thing that parses a policy document — there is no second
    reader here and there must not grow one."""
    return profiles.gate_policy()


def _check_requirement(filename: str, check: dict, head: dict,
                       records: list[dict]) -> dict:
    """One required check → met, pending or unmet, and why.

    The rule, in full: a record satisfies when it is for this check, when
    `verify.outcome_now` *still* reads `passed` (so a pass whose binding
    has gone reads `stale` here, as it does everywhere), when the source
    it ran against was bound, when that source was clean, and when its
    commit is the commit about to be merged.

    The clean-source condition is the one that is easy to miss and hard to
    argue with: a check that ran with uncommitted files in the checkout
    did not run against the commit that would merge. It ran against
    something that only ever existed on one disk.
    """
    mine = [r for r in records if r.get("check") == check["name"]]
    best: dict | None = None
    live = False
    seen: list[dict] = []
    for record in mine:
        reading = verify.outcome_now(record)
        source = record.get("source") or {}
        row = {
            "evidence": record.get("id"),
            "outcome": reading["outcome"],
            "recorded": reading["recorded"],
            "reason": reading["reason"],
            "why": reading["why"],
            "commit": source.get("commit"),
            "dirty": list(source.get("dirty") or []),
            "bound": bool(source.get("bound")),
            "rootPolicy": (record.get("root") or {}).get("policy"),
            "scope": record.get("scope"),
            "ended": record.get("ended"),
            "started": record.get("started"),
            "agent": record.get("agent"),
            "actor": record.get("actor"),
            "freshness": reading["freshness"],
        }
        seen.append(row)
        if record.get("outcome") in verify.LIVE:
            live = True
        if best is None:
            best = row
    row = {
        "kind": "check", "name": check["name"], "command": check.get("run"),
        "rootPolicy": check.get("root"), "checkId": check.get("checkId"),
        "help": check.get("help"), "history": seen[:8],
    }
    met = next((r for r in seen
                if r["outcome"] == "passed" and r["bound"] and not r["dirty"]
                and r["commit"] and r["commit"] == head.get("commit")), None)
    name = check["name"]
    if met is not None:
        return {**row, "state": "met", "reason": "passed",
                "why": (f"bench ran '{name}' against "
                        f"{_short(met['commit'])} and it passed"),
                "evidence": met["evidence"], "commit": met["commit"],
                "remedy": None}
    if not head.get("commit"):
        return {**row, "state": "unmet", "reason": "head-unknown",
                "why": REASONS["head-unknown"],
                "remedy": REMEDIES["head-unknown"], "evidence": None,
                "commit": None}
    if live:
        running = next(r for r in seen if r["outcome"] in verify.LIVE)
        return {**row, "state": "pending", "reason": running["outcome"],
                "why": (f"'{name}' is {running['outcome']} — the answer is "
                        f"not in yet"),
                "remedy": None, "evidence": running["evidence"],
                "commit": running["commit"]}
    if best is None:
        return {**row, "state": "unmet", "reason": "not-run",
                "why": (f"nothing has asked bench for '{name}', so there is "
                        f"no evidence either way — an absent record is not a "
                        f"pass"),
                "remedy": REMEDIES["not-run"], "evidence": None,
                "commit": None}
    # There is evidence, and none of it satisfies. Which of the several
    # ways that can be true matters, because they are different things to
    # go and do — so the reason is the most specific one that applies to
    # the newest record rather than a shared "unmet".
    passed = [r for r in seen if r["outcome"] == "passed"]
    if passed:
        candidate = passed[0]
        dirty = len(candidate["dirty"])
        if not candidate["bound"]:
            reason = "source-unbound"
            why = (f"'{name}' passed, and the source it ran against could "
                   f"not be bound: "
                   f"{candidate['why'] or REASONS['source-unbound']}")
        elif dirty:
            reason = "source-dirty"
            why = (f"'{name}' passed with {dirty} uncommitted "
                   f"file{'' if dirty == 1 else 's'} in the checkout, so it "
                   f"was not about the commit that would merge")
        else:
            reason = "other-commit"
            why = (f"'{name}' passed against {_short(candidate['commit'])}, "
                   f"and this would merge {_short(head['commit'])}")
        return {**row, "state": "unmet", "reason": reason, "why": why,
                "remedy": REMEDIES[reason], "evidence": candidate["evidence"],
                "commit": candidate["commit"]}
    reason = best["outcome"] if best["outcome"] in REASONS else "error"
    said = verify.OUTCOMES.get(best["outcome"]) or REASONS.get(reason, reason)
    return {**row, "state": "unmet", "reason": reason,
            "why": f"'{name}': {best['why'] or said}",
            "remedy": REMEDIES.get(reason, REMEDIES["failed"]),
            "evidence": best["evidence"], "commit": best["commit"]}


def _review_requirement(filename: str, policy: str, head: dict) -> dict:
    """The review half → met, or unmet with the reason and the remedy.

    Four policies, and the three that are not `none` are deliberately not
    interchangeable: a Bench verdict is bench's own record of what its
    reviewer found, a formal GitHub state is what GitHub holds and will
    enforce, and a separate reviewer identity is a question about *who*.
    A comment satisfies none of them.
    """
    row = {"kind": "review", "name": policy, "policy": policy,
           "policyWhy": REVIEW_POLICIES.get(policy, policy)}
    commit = head.get("commit")
    if policy == "none":
        return {**row, "state": "met", "reason": "ungated",
                "why": "no review is required by this gate", "remedy": None}
    observed = github_reviews(filename) or {}
    limitation = observed.get("limitation")
    row["limitation"] = limitation
    if policy == "bench":
        found = verdict_at(filename, commit)
        row["verdict"] = found
        if found and found["verdict"] == "APPROVE":
            return {**row, "state": "met", "reason": "bench-approved",
                    "why": (f"bench's own review of {_short(commit)} "
                            f"approved it"),
                    "remedy": None}
        if found:
            return {**row, "state": "unmet", "reason": "verdict-changes",
                    "why": (f"bench's own review of {_short(commit)} asked "
                            f"for changes"),
                    "remedy": REMEDIES["verdict-changes"]}
        others = verdicts_for(filename)
        if others:
            was = _short(others[0].get("head"), "an unrecorded head")
            return {**row, "state": "unmet", "reason": "verdict-other-head",
                    "why": (f"the newest Bench verdict is about {was}, and "
                            f"this would merge "
                            f"{_short(commit, 'nothing known')}"),
                    "remedy": REMEDIES["verdict-other-head"],
                    "verdict": others[0]}
        return {**row, "state": "unmet", "reason": "no-verdict",
                "why": REASONS["no-verdict"], "remedy": REMEDIES["no-verdict"]}

    formal = observed.get("formal") or []
    row["formal"] = formal
    row["author"] = observed.get("author")
    at_head = [r for r in formal if r.get("commit") and r["commit"] == commit]
    changes = [r for r in at_head if r["state"] == "CHANGES_REQUESTED"]
    approvals = [r for r in at_head if r["state"] == "APPROVED"]
    if changes and not approvals:
        return {**row, "state": "unmet", "reason": "changes-requested",
                "why": (f"a formal GitHub review of {_short(commit)} asks "
                        f"for changes"),
                "remedy": REMEDIES["changes-requested"]}
    separate = [r for r in approvals if not r["self"]]
    if policy == "separate-reviewer":
        if separate:
            return {**row, "state": "met", "reason": "approved-by-another",
                    "why": (f"{separate[0]['login']} approved "
                            f"{_short(commit)} on GitHub, and is not the "
                            f"account that proposed it"),
                    "remedy": None}
        if approvals:
            return {**row, "state": "unmet", "reason": "no-separate-reviewer",
                    "why": REASONS["no-separate-reviewer"],
                    "remedy": REMEDIES["no-separate-reviewer"]}
    elif approvals:
        return {**row, "state": "met", "reason": "approved",
                "why": (f"GitHub holds a formal APPROVED review of "
                        f"{_short(commit)} from {approvals[0]['login']}"),
                "remedy": None}
    if limitation:
        return {**row, "state": "unmet", "reason": "same-account",
                "why": (f"{limitation['why']} The only review activity on "
                        f"this pull request is from "
                        f"{limitation['login']}, which is the account that "
                        f"proposed the head."),
                "remedy": REMEDIES["same-account"]}
    if formal:
        was = _short(formal[0].get("commit"))
        return {**row, "state": "unmet", "reason": "approval-other-head",
                "why": (f"the formal review state GitHub holds was given for "
                        f"{was}, and this would merge "
                        f"{_short(commit, 'nothing known')}"),
                "remedy": REMEDIES["approval-other-head"]}
    return {**row, "state": "unmet", "reason": "no-approval",
            "why": REASONS["no-approval"], "remedy": REMEDIES["no-approval"]}


def evaluate(filename: str, scope: str, *, task: dict | None = None,
             tips: dict[str, str] | None = None,
             policy: dict | None = None,
             records: list[dict] | None = None) -> dict:
    """May this mutation happen? One reading, and it changes nothing.

    Everything is resolved here: the requirements from the policy
    document, the commit from application git, the evidence from disk, the
    verdict and the formal review state from their own records. Nothing
    is cached between the reading and the mutation — a caller asks again
    immediately before the irreversible step, with this function's own
    answer, which is how a push that lands in between becomes a refusal
    rather than a stale-evidence window.

    `tips`, `policy` and `records` are the same reads, hoisted: a caller
    asking about a whole board passes them once rather than paying a
    branch walk, a document read and a scan of every evidence file per
    card. They are inputs to the same comparison and never a different
    one — a decision made from a hoisted read is the decision the
    mutation makes, which is why `board()` may share them and why the
    guarded paths deliberately do not.
    """
    if scope not in profiles.GATE_SCOPES:
        raise ValueError(f"unknown gate scope '{scope}' — one of "
                         f"{', '.join(sorted(profiles.GATE_SCOPES))}")
    policy = gate_policy() if policy is None else policy
    entry = policy["scopes"][scope]
    head = head_for(filename, scope, tips)
    found = {
        "v": 1, "task": filename, "scope": scope, "why": entry["why"],
        "policyId": policy["policyId"], "policyRevision": policy["revision"],
        "policySource": dict(policy["source"]),
        "configured": policy["configured"],
        "gated": entry["gated"], "head": head,
        "review": entry["review"], "reviewWhy": entry["reviewWhy"],
        "requirements": [], "unmet": [], "pending": [],
        "overrides": {"allowed": policy["overrides"]["allowed"],
                      "why": policy["overrides"]["why"],
                      "actors": list(policy["overrides"]["actors"]),
                      "applied": None},
        "blockers": list(policy["blockers"]),
        "diagnostics": list(policy["diagnostics"]),
        "location": "local",
        "read": time.time(),
    }
    if policy["blockers"]:
        # A policy that does not resolve grants nothing. It is the same
        # law a broken document follows for every launch intent: an
        # operator mid-edit is not handed the gate they were changing.
        found.update({
            "decision": "blocked",
            "unmet": [{"kind": "policy", "name": "gate policy",
                       "state": "unmet", "reason": "policy-refused",
                       "why": policy["blockers"][0],
                       "remedy": REMEDIES["policy-refused"]}],
            "summary": (f"this board's gate policy does not resolve: "
                        f"{policy['blockers'][0]}"),
        })
        found["requirements"] = list(found["unmet"])
        return found
    if not entry["gated"]:
        found.update({
            "decision": "ungated",
            "summary": (f"no gate is configured for {scope} on this board, so "
                        f"nothing is required here and nothing is proved"),
        })
        return found

    mine = ([r for r in records if r.get("task") == filename]
            if records is not None else verify.for_task(filename))
    rows = [_check_requirement(filename, check, head, mine)
            for check in entry["checks"]]
    rows.append(_review_requirement(filename, entry["review"], head))
    rows = [r for r in rows if not (r["kind"] == "review"
                                    and r["policy"] == "none")]
    found["requirements"] = rows
    unmet = [r for r in rows if r["state"] == "unmet"]
    pending = [r for r in rows if r["state"] == "pending"]

    applied = _override_in_force(filename, scope, head, unmet)
    if applied is not None and unmet:
        for row in rows:
            if row["state"] == "unmet":
                row["state"] = "overridden"
                row["override"] = applied["id"]
        found["overrides"]["applied"] = applied
        found.update({
            "decision": "overridden",
            "unmet": [], "pending": pending,
            "summary": (f"{applied['actor']} proceeded over {_names(unmet)} "
                        f"at {_short(head.get('commit'))} — "
                        f"{applied['reason']}. The gate is still unmet; this "
                        f"records who decided otherwise"),
            "overriddenGates": [r["name"] for r in unmet],
        })
        return found

    found["unmet"] = unmet
    found["pending"] = pending
    at = _short(head.get("commit"), "an unknown head")
    if unmet:
        found.update({
            "decision": "blocked",
            "summary": (f"{_names(unmet)} {'is' if len(unmet) == 1 else 'are'} "
                        f"unmet at {at}: {unmet[0]['why']}"),
        })
    elif pending:
        found.update({
            "decision": "pending",
            "summary": (f"{_names(pending)} "
                        f"{'is' if len(pending) == 1 else 'are'} still running "
                        f"— nothing is decided from that"),
        })
    else:
        found.update({
            "decision": "allowed",
            "summary": (f"every requirement is met against {at}: "
                        f"{_names(rows)}"),
        })
    return found


def _names(rows: list[dict]) -> str:
    names = [r["name"] for r in rows]
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else "nothing"


def passable(decision: dict) -> bool:
    """Would this decision let the mutation run? `ungated` does, because a
    board that configured no gate behaves exactly as it did before gates
    existed — and the decision says `ungated` rather than `allowed`, so
    nothing reads it as a verification that happened."""
    return decision["decision"] in ("allowed", "ungated", "overridden")


def require(decision: dict, doing: str = "this") -> dict:
    """The one door a guarded mutation comes through.

    Raises `ValueError` with the sentence the page shows, from the same
    reading the page read — so a disabled action and a backend refusal can
    never give two different reasons for one state.
    """
    if passable(decision):
        return decision
    unmet = decision.get("unmet") or []
    pending = decision.get("pending") or []
    if decision["decision"] == "pending":
        raise ValueError(
            f"{doing} waits on {_names(pending)} — bench is running "
            f"{'it' if len(pending) == 1 else 'them'} now, and nothing is "
            f"decided from a check that has not finished.")
    parts = [f"{doing} is blocked by this board's {decision['scope']} gate: "
             f"{decision['summary']}"]
    for row in unmet:
        if row.get("remedy"):
            parts.append(f"{row['name']}: {row['remedy']}")
    if decision["overrides"]["allowed"]:
        parts.append("An authorised override can proceed over it, and is "
                     "recorded against whoever does — it never makes the "
                     "gate met.")
    raise ValueError(" ".join(parts))


# ── overrides: explicit, bound, and never a pass ───────────────────────


def _override_in_force(filename: str, scope: str, head: dict,
                       unmet: list[dict]) -> dict | None:
    """The override that covers *this* head and *these* gates, or None.

    Three conditions, and each exists because of a way an override could
    otherwise widen itself: the scope must match (an override of a
    member's merge is not an override of the phase's acceptance), the head
    must match exactly (an override authorises one commit, and the next
    one is a different question), and every currently-unmet gate must be
    named in it (a gate that became unmet afterwards was never
    authorised).
    """
    if not unmet:
        return None
    commit = head.get("commit")
    if not commit:
        return None
    wanted = {row["name"] for row in unmet}
    for record in overrides_for(filename):
        if record.get("scope") != scope or record.get("head") != commit:
            continue
        if wanted <= set(record.get("gates") or []):
            return record
    return None


def overrides_for(filename: str) -> list[dict]:
    """Every override recorded for this card, newest first. Persisted, so
    a restart neither forgets one (which would stop a merge somebody
    authorised) nor applies one twice."""
    found = _read_json(_path(filename, "overrides"), [])
    return sorted((r for r in found if isinstance(r, dict)),
                  key=lambda r: r.get("recorded") or 0, reverse=True)


def override(filename: str, scope: str, *, actor: str, reason: str,
             head: str | None = None, task: dict | None = None) -> dict:
    """Proceed over named unmet gates, on the record.

    What it writes down is the whole of what makes it safe to have at all:
    who, why, the exact commit, and exactly which gates were unmet when
    they decided. What it does *not* write is evidence — no check becomes
    passed, no verdict appears, and the decision afterwards reads
    `overridden`, never `allowed`.

    It refuses four things rather than recording a weaker version of them:
    an override on a board whose policy does not enable one, an actor the
    policy does not name, an override of a gate that is not unmet, and an
    override of a head other than the one the caller is looking at — which
    is how a person confirming a sheet that has gone stale is caught.
    """
    who = str(actor or "").strip()
    text = " ".join(str(reason or "").split())
    decision = evaluate(filename, scope, task=task)
    if not decision["overrides"]["allowed"]:
        raise ValueError(
            f"this board does not allow overriding a gate: "
            f"{decision['overrides']['why']}. Add \"overrides\": "
            f"{{\"allow\": true}} to the document's \"gates\" block if an "
            f"authorised proceed-anyway is something this project wants.")
    allowed = decision["overrides"]["actors"]
    if allowed and who not in allowed:
        raise ValueError(
            f"{who or 'an unnamed actor'} is not one of the identities this "
            f"board's gate policy allows to override "
            f"({', '.join(allowed)}) — nothing was recorded.")
    if not who:
        raise ValueError(
            "an override is recorded against a person, and this checkout has "
            "no git identity to record — set git config user.name, or do not "
            "override.")
    if len(text) < 8:
        raise ValueError(
            "an override needs a reason somebody can read later: it is the "
            "only thing in the record that says why an unmet gate was "
            "proceeded over.")
    if decision["decision"] == "overridden":
        # Already in force for this head and these gates. Returned rather
        # than appended: a second click, a replayed request and a board
        # that restarted mid-confirmation must not produce two records of
        # one decision.
        return {"override": decision["overrides"]["applied"],
                "decision": decision, "duplicate": True}
    if not decision["unmet"]:
        raise ValueError(
            f"there is nothing to override on {filename}: its {scope} gate "
            f"reads {decision['decision']} — {decision['summary']}")
    commit = decision["head"].get("commit")
    if head and head != commit:
        raise ValueError(
            f"the head moved while you were looking: you confirmed an "
            f"override of {_short(head)} and {decision['head']['ref']} is now "
            f"at {_short(commit, 'nothing')}. An override authorises one "
            f"commit, so nothing was recorded — read the gate again.")
    gates = [row["name"] for row in decision["unmet"]]
    stamp = _digest(filename, scope, str(commit), who, str(time.time()))[:12]
    record = {
        "v": 1, "id": f"ovr:{stamp}",
        "task": filename, "scope": scope, "actor": who, "reason": text,
        "head": commit, "headRef": decision["head"]["ref"],
        "gates": gates,
        "unmet": [{"name": row["name"], "kind": row["kind"],
                   "reason": row["reason"], "why": row["why"]}
                  for row in decision["unmet"]],
        "policyId": decision["policyId"],
        "policyRevision": decision["policyRevision"],
        "recorded": time.time(),
    }
    path = _path(filename, "overrides")
    with _LOCK:
        found = _read_json(path, [])
        if any(r.get("scope") == scope and r.get("head") == commit
               and set(r.get("gates") or []) == set(gates)
               for r in found if isinstance(r, dict)):
            _bump()
            return {"override": record, "decision": evaluate(filename, scope),
                    "duplicate": True}
        found.append(record)
        _write_json(path, found[-50:])
    _bump()
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": filename,
        "summary": (f"{who} overrode the {scope} gate on {filename} at "
                    f"{_short(commit)} — {_names(decision['unmet'])} "
                    f"{'is' if len(gates) == 1 else 'are'} still unmet: "
                    f"{text}")})
    state.broadcast({"type": "board"})
    return {"override": record, "decision": evaluate(filename, scope),
            "duplicate": False}


# ── what the board shows ───────────────────────────────────────────────


PUBLIC = ("v", "task", "scope", "why", "decision", "summary", "configured",
          "gated", "head", "review", "reviewWhy", "requirements", "unmet",
          "pending", "overrides", "blockers", "diagnostics", "policyId",
          "policyRevision", "policySource", "location", "read",
          "overriddenGates")


def public(decision: dict | None) -> dict | None:
    """One decision as the board may show it. An allowlist, for the reason
    `verify.public` and `profiles.public` keep one. Nothing in here has
    ever held an environment value or a file's contents: a gate names
    checks, commits, logins and reasons."""
    if not decision:
        return None
    return {key: decision[key] for key in PUBLIC if key in decision}


def board(collected: dict) -> dict:
    """The compact per-card reading the board view needs: which gate
    guards each card's acceptance and what it currently says.

    Only cards with something to say appear — the law every chip on this
    board follows. A board that declared no gates gets an empty object and
    renders exactly what it always did.
    """
    global _BOARD
    policy = gate_policy()
    if not policy["blockers"] and not any(
            entry["gated"] for entry in policy["scopes"].values()):
        return {}
    tasks = [task for stage in collected.get("stages", [])
             for task in stage["tasks"]]
    files = frozenset(task["file"] for task in tasks)
    with _LOCK:
        epoch = _EPOCH
    stamp = (epoch, policy["revision"], int(time.time() / _TTL), verify._EPOCH)
    if _BOARD is not None and _BOARD[0] == stamp and _BOARD[1] == files:
        return _BOARD[2]
    tips = _tips()
    # One branch walk, one document read and one scan of the evidence for
    # the whole board, rather than one of each per card: this runs on
    # every state load, and a per-card subprocess is what makes a payload
    # expensive enough that somebody caches a *decision* instead.
    records = verify.all_evidence()
    found: dict = {}
    for task in tasks:
        scope = scope_for(task)
        # A member of a running phase is gated twice over, and the one a
        # reader wants on the card is the one that is actually in its way:
        # its merge into the phase branch comes first.
        if task.get("phase") and not task.get("isPhase"):
            scope = "member"
        try:
            decision = evaluate(task["file"], scope, task=task, tips=tips,
                                policy=policy, records=records)
        except (ValueError, OSError) as exc:
            # The page must not go blank because one card's gate could not
            # be read, and it must not read as open either. One card's row
            # says what happened; the rest of the board is unaffected.
            found[task["file"]] = {
                "scope": scope, "decision": "blocked",
                "summary": f"this card's gate could not be read: {exc}",
                "unmet": [{"name": "gate", "kind": "policy",
                           "reason": "policy-refused"}],
                "pending": [], "required": 1, "met": 0,
                "head": None, "headRef": None, "override": False,
                "location": "local"}
            continue
        if decision["decision"] == "ungated" and not policy["blockers"]:
            continue
        found[task["file"]] = {
            "scope": scope, "decision": decision["decision"],
            "summary": decision["summary"],
            "unmet": [{"name": r["name"], "kind": r["kind"],
                       "reason": r["reason"]} for r in decision["unmet"]],
            "pending": [r["name"] for r in decision["pending"]],
            "required": len(decision["requirements"]),
            "met": len([r for r in decision["requirements"]
                        if r["state"] == "met"]),
            "head": decision["head"].get("commit"),
            "headRef": decision["head"].get("ref"),
            "override": bool((decision["overrides"] or {}).get("applied")),
            "location": "local",
        }
    _BOARD = (stamp, files, found)
    return found


def line(decision: dict) -> str:
    """One gate decision, as the ticker tells it."""
    word = {"allowed": "met", "blocked": "BLOCKED", "pending": "waiting",
            "ungated": "ungated", "overridden": "OVERRIDDEN"}.get(
                decision["decision"], decision["decision"])
    return (f"{decision['scope']} gate on {decision['task']} is {word} — "
            f"{decision['summary']}")


def status_for(filename: str, scope: str, *, task: dict | None = None) -> dict:
    """The decision for one card and scope, as the API returns it."""
    return public(evaluate(filename, scope, task=task))


def seams() -> dict:
    """What this module deliberately leaves to the ones around it, named
    here so nothing has to go looking.

    Asserted by the tests rather than printed: it is the contract, in the
    one place a reader of this module looks for it.
    """
    return {
        "decisions": dict(DECISIONS),
        "reasons": dict(REASONS),
        "evaluates": ("profiles.gate_policy() for the requirements, "
                      "verify.for_task()/outcome_now() for the evidence, "
                      "and application git for the commit. It parses no "
                      "policy document itself"),
        "enforced_at": {
            "a phase member's merge": ("phases._member_state holds it out of "
                                       "`ready`, and phases._merge_member "
                                       "calls require() again with the tip in "
                                       "hand"),
            "an acceptance": ("github._complete calls require() before it "
                              "parks the drive and again immediately before "
                              "the merge"),
        },
        "produces": {
            "the Bench verdict": ("agents._reap_pr_review records what its "
                                  "reviewer found, bound to the head "
                                  "reviews.py pinned, whatever GitHub "
                                  "accepted"),
            "the formal review state": ("github._poll_pr feeds "
                                        "observe_reviews with GitHub's own "
                                        "review objects, which carry the "
                                        "commit each review was of"),
        },
        "never": ["runs a check", "relaunches an agent", "moves a card",
                  "touches a branch", "reaches a remote",
                  "reads COMMENTED as an approval",
                  "forges evidence for an override"],
    }
