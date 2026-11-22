"""The delivery status: one answer per card to three questions.

1. **What is happening?**
2. **What do I do next?**
3. **What, if anything, prevents delivery?**

Before this, every surface worked those out for itself — the card face
from one set of chips, the Overview from a cascade in the page, the merge
dialog from the gate — and they could disagree. This module is the one
place the answer is computed, server-side, so the face, the Overview and
the dialog draw the same result (design #94 §3).

## It owns no policy and no enforcement

Every fact comes from the authority that already answers for it, and
nothing here is a second reading of any of them:

| question | authority |
| --- | --- |
| stage, assignee, PR line, phase membership | `taskfiles` (the collected card) |
| an agent running; the last run failed | `state.AGENTS`, the failure `agents` records |
| completion in flight; merged but not published | `state.COMPLETING`, `completion.recovery()` |
| recorded plan; stacked target | `plans.recorded()`, `plans.declared()` |
| which commit the merge would take, and its state | `gates.candidate()`, `gates.head_for()` |
| requirements at that commit | `gates.evaluate()` |
| the PR as last observed | `changes.pr_snapshot()` (memory, or the persisted snapshot) |
| review findings | `acceptance.summary()` |
| a phase run | the phase runner's last pass, handed in by the caller |

**Mutations never read this result.** `agents.start_agent`,
`verify.request`, `gates.override` and `completion._complete` keep their own
checks and run them again at the moment they act. A cached reading here
is presentation, never authorisation.

## Reading changes nothing

It fetches nothing, asks GitHub nothing, runs no check, launches no
agent and writes no record. The only processes it can cause are the
read-only local git queries the authorities above already make (the
branch walk, a containment test, a source digest), and those go through
`config.git_run`, which keeps them reads: no textconv, external diff,
filter, fsmonitor, hook or signature program, and no optional index
write, whatever the repository configures. Two explicit actions
live beside it and are POSTs a person clicks: **Check the remote**
(`check_remote`, one bounded fetch of the card's own two branch names)
and the launch check (`launch_check`, the launch's own preflight).

## Precedence

Lifecycle first, then genuine current problems, then future
requirements (design §3.3). The rows are evaluated top to bottom and the
first that applies wins; `ROWS` lists them in that order and every word
in `WORDS` is produced by exactly one row. One deliberate refinement of
the table: a pull request already merged on GitHub is read *before* the
review-stage requirement rows, so administrative closure never asks for
checks, a review or sign-off that can no longer change anything.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import acceptance
import completion as completion_mod   # `completion` names a reading here
import config
import evidence
import gatepolicy
import gates
import githost
import changes
import gitrepo
import integration
import plans
import profiles
import required
import stacks
import state
import taskfiles
import verify

V = 1

# ── the vocabulary ─────────────────────────────────────────────────────
#
# One word per status, with its label and tone. The word carries the
# meaning; the tone only repeats it. The design's prose gave a count for
# this table that did not match the table — so the count is not repeated
# here: the tests derive it from `ROWS` and the copy matrix instead.

TONES = ("neutral", "working", "attention", "alarm", "calm", "idle")

WORDS = {
    "merging": ("Merging", "working"),
    "not-published": ("Not published", "alarm"),
    "run-failed": ("Run failed", "alarm"),
    "agent-working": ("{name} is working", "working"),
    "phase-running": ("Phase running", "working"),
    "not-started": ("Not started", "neutral"),
    "queued": ("Queued", "neutral"),
    "returned": ("Returned — earlier work exists", "neutral"),
    "ready-to-start": ("Ready to start", "neutral"),
    "in-progress": ("In progress", "neutral"),
    "waiting-for-phase": ("Waiting for phase", "neutral"),
    "waiting-for-parent": ("Waiting for parent", "neutral"),
    "merged-elsewhere": ("Merged on GitHub", "neutral"),
    "cannot-assess": ("Can't assess", "alarm"),
    "rules-invalid": ("Merge rules invalid", "alarm"),
    "check-failed": ("Check failed", "alarm"),
    "cannot-run-check": ("Check could not run", "alarm"),
    "changes-requested": ("Changes requested", "alarm"),
    "checks-running": ("Checks running", "working"),
    "checks-needed": ("Checks needed", "neutral"),
    "review-needed": ("Review needed", "neutral"),
    "signoff-needed": ("Sign-off needed", "neutral"),
    "target-changed": ("Target changed — integration check needed",
                       "attention"),
    "target-conflicts": ("Conflicts with target — repair needed", "alarm"),
    "ready-to-merge": ("Ready to merge", "calm"),
    "ready-overridden": ("Ready — overridden", "alarm"),
    "review": ("Review", "neutral"),
    "delivered": ("Delivered", "idle"),
}

# The precedence, in order: (row, condition in words, the words it may
# produce). Data rather than prose so a test can hold the code to it.
ROWS = (
    (1, "merge & clean up is in flight", ("merging",)),
    (2, "a completion merged and did not publish", ("not-published",)),
    (3, "the latest run on this stage failed", ("run-failed",)),
    (4, "an agent is running on the card", ("agent-working",)),
    (5, "a phase card is running a member", ("phase-running",)),
    (6, "backlog or to-do",
     ("not-started", "queued", "returned", "cannot-assess")),
    (7, "in progress, nobody on it, a current-commit problem",
     ("check-failed", "cannot-run-check", "changes-requested")),
    (8, "in progress, nobody on it", ("ready-to-start", "in-progress")),
    (9, "review, a phase member", ("waiting-for-phase",)),
    (17, "review, the PR was merged on GitHub", ("merged-elsewhere",)),
    (11, "review, merge rules do not resolve", ("rules-invalid",)),
    (10, "review, no commit to assess", ("cannot-assess",)),
    (12, "review, a current-commit failure or change request",
     ("check-failed", "cannot-run-check", "changes-requested")),
    (13, "review, a required check is running", ("checks-running",)),
    # read before 14-16 and 18 (task 189): a head that does not hold the
    # target it lands in, where the merge rule requires a check — checks or
    # a review still to come would be about work not yet combined with it
    (22, "review, a head that does not hold the current target",
     ("target-changed", "target-conflicts")),
    (14, "review, a required check is needed", ("checks-needed",)),
    (15, "review, the required review is not met", ("review-needed",)),
    (16, "review, sign-off pending", ("signoff-needed",)),
    # read before 18 and 19: a stacked card whose own requirements are met
    # (or not configured) still cannot land before the card it is stacked
    # on has (task 117)
    (21, "review, stacked on a card whose work has not landed",
     ("waiting-for-parent",)),
    (18, "review, every requirement met or overridden",
     ("ready-to-merge", "ready-overridden")),
    (19, "review, an ungated board", ("review",)),
    (20, "done", ("delivered",)),
)

# Words no row produces yet, and the card that brings them. Empty since
# card 103 built the opt-in sign-off gate field (design #94 §8.3).
NOT_YET: dict[str, str] = {}

# The actions a result may name. `handler` is the route that performs it
# today; None means a later #94 card brings it, and the action is then
# always `enabled: false` with `why` saying so — never a button that does
# nothing. `kind` is `api` (a POST), `view` (opens something on the page,
# changes nothing) or `link` (an external URL).
ACTIONS = {
    "move": {"handler": "/api/move", "kind": "api", "arm": False},
    "start": {"handler": "/api/agent/start", "kind": "api", "arm": True},
    "takeover": {"handler": "/api/agent/start", "kind": "api", "arm": True},
    "hold": {"handler": "/api/agent/stop", "kind": "api", "arm": True},
    "run-phase": {"handler": "/api/phase/run", "kind": "api", "arm": True},
    "hold-phase": {"handler": "/api/phase/stop", "kind": "api", "arm": True},
    "act-pr": {"handler": "/api/agent/act-pr", "kind": "api", "arm": True},
    "review-pr": {"handler": "/api/agent/review-pr", "kind": "api",
                  "arm": True},
    "open-pr": {"handler": "/api/pr/open", "kind": "api", "arm": False},
    "complete": {"handler": "/api/task/complete", "kind": "api",
                 "arm": True},
    # task 189: bring the accepted target into the card's own branch,
    # additively; a conflict hands one ↻ act on PR run the details
    "update-target": {"handler": "/api/card/integration/update",
                      "kind": "api", "arm": True},
    "cancel-check": {"handler": "/api/verify/cancel", "kind": "api",
                     "arm": True},
    "check-remote": {"handler": "/api/card/delivery/check-remote",
                     "kind": "api", "arm": False},
    "launch-check": {"handler": "/api/card/delivery/launch-check",
                     "kind": "api", "arm": False},
    # Opens its confirmation, which reads what a click would request and
    # holds the armed fire — so the button itself does not arm (as the
    # completion sheet is merge & clean up's confirmation).
    "run-required-checks": {"handler": "/api/verify/required", "kind": "api",
                            "arm": False},
    # Opens the card's Acceptance section, where Track acceptance and each
    # criterion's decision already live: the action changes nothing itself.
    "confirm-criteria": {"handler": None, "kind": "view", "arm": False},
    "read-failure": {"handler": None, "kind": "view", "arm": False},
    "evidence": {"handler": None, "kind": "view", "arm": False},
    "gates": {"handler": None, "kind": "view", "arm": False},
    "phases": {"handler": None, "kind": "view", "arm": False},
    "settings-merge-rules": {"handler": None, "kind": "view", "arm": False},
    "open-pr-url": {"handler": None, "kind": "link", "arm": False},
}

def action(key: str, label: str, *, who: str = "you",
           enabled: bool = True, why: str | None = None, **extra) -> dict:
    """One action, as every surface draws it. An action whose handler
    belongs to a later card is unavailable with that reason, whatever the
    caller asked for."""
    spec = ACTIONS[key]
    if spec.get("later"):
        enabled, why = False, spec["later"]
    out = {"action": key, "label": label, "who": who,
           "enabled": bool(enabled), "why": why if not enabled else None,
           "arm": spec["arm"], "kind": spec["kind"],
           "handler": spec["handler"]}
    out.update(extra)
    return out


def _short(commit: str | None, fallback: str = "this commit") -> str:
    return str(commit)[:12] if commit else fallback


# ── the inputs, read once per pass ─────────────────────────────────────


def _integration_public(fit: dict | None) -> dict | None:
    """The integration reading as a card carries it (task 189)."""
    if not fit:
        return None
    keys = ("state", "label", "tone", "sentence", "next", "target",
            "targetTip", "targetRef", "authority", "required", "fresh",
            "behind", "incoming", "provider", "guarantees", "unpushed")
    out = {k: fit.get(k) for k in keys if k in fit}
    attempt = fit.get("attempt")
    if attempt:
        out["attempt"] = {k: attempt.get(k) for k in (
            "at", "outcome", "targetTip", "newHead", "conflicts", "actor",
            "pushed")}
    return out


def _remote_tips() -> dict[str, str]:
    """What this checkout's remote-tracking refs already say about task
    and phase branches — the last fetch, not a new one. One local read."""
    remote = config.app_remote()
    if not remote:
        return {}
    found: dict[str, str] = {}
    result = gitrepo.app_git("for-each-ref",
                             "--format=%(refname) %(objectname)",
                             f"refs/remotes/{remote}/")
    if result.returncode != 0:
        return found
    prefix = f"refs/remotes/{remote}/"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith(prefix):
            found[parts[0][len(prefix):]] = parts[1]
    return found


def _context(phase_state: dict | None = None) -> dict:
    """Every board-wide read a pass needs, taken once.

    `phase_state` is the phase runner's last pass (`phases.public_state()`),
    handed in by the caller: `phases` sits to the right of this module in
    the dependency order, so it is read where both are in reach."""
    with state.LOCK:
        agents = [dict(record) for record in state.AGENTS.values()]
        completing = {k: dict(v) for k, v in state.COMPLETING.items()}
    policy = gatepolicy.gate_policy()
    return {
        "tips": gates.branch_tips(),
        "remoteTips": _remote_tips(),
        "policy": policy,
        "records": evidence.all_evidence(),
        "agents": agents,
        "completing": completing,
        "phases": {k: dict(v) for k, v in (phase_state or {}).items()},
        "me": taskfiles.actor_name() if config.COMMIT_MOVES else "",
        "remote": config.app_remote(),
        "gh": githost.available(),
        "onOrigin": config.app_merge_on_origin(),
        "now": time.time(),
        # which checks a person's work reading may ask for — the reading
        # Run required checks rides — and the batches in flight
        "workChecks": _work_checks(),
        "batches": required.running(),
        # cards with an update against their target in flight (task 189)
        "updating": set(integration.UPDATING),
    }


def _work_checks() -> set[str]:
    """The checks a person's work reading may ask for, resolved without a
    card (the list does not depend on one) and without writing anything."""
    try:
        found = profiles.resolve(required.INTENT)
    except (ValueError, OSError):
        return set()
    return {c["name"] for c in found.get("verify") or []}


def _runs(task: dict, ctx: dict) -> tuple[dict | None, dict | None]:
    """The run alive on this card, and its latest run's failure when that
    failure is about the stage the card is in now."""
    mine = sorted((r for r in ctx["agents"] if r.get("task") == task["file"]),
                  key=lambda r: r.get("started") or 0, reverse=True)
    alive = next((r for r in mine if r.get("status") == "running"), None)
    failure = None
    if mine and alive is None:
        latest = mine[0]
        found = latest.get("failure")
        if found and (found.get("stage") in (None, task["stage"])):
            failure = dict(found, name=latest.get("name"))
    return alive, failure


def _target(task: dict, plan: dict | None) -> str:
    """Where this card's work lands, read without a fetch: the recorded
    plan, else the card's own headers. (`plans.pr_base` may ask the remote
    whether a phase branch exists; a reading must not.)"""
    if plan and plan.get("prBase"):
        return plan["prBase"]
    if task.get("phase") and not task.get("isPhase"):
        return config.PHASE_BRANCH_PREFIX + task["phase"]["file"][:-3]
    return plans.declared(task)["prBase"]


def _completion(task: dict, plan: dict | None, target: str,
                pr: dict | None, ctx: dict) -> dict:
    """What merge & clean up would do, from the same facts
    `completion.completion_facts` reads — minus anything that would reach a
    remote. A target this checkout cannot see locally is said to be
    unconfirmed rather than looked up."""
    app_base = (plan or {}).get("appBase") or config.APP_BASE
    stacked = target != app_base
    seen = (target in ctx["tips"] or target in ctx["remoteTips"]
            or target == app_base)
    refusal = ""
    if stacked:
        refusal = (f"this card lands through its PR into {target}, not "
                   f"{app_base} — merge & clean up does not finish a "
                   f"stacked card")
    elif ctx["onOrigin"] and not task.get("pr") and not (pr or {}).get("url"):
        refusal = ("this board merges on origin and the card has no PR — "
                   "open one first (↑ open PR)")
    elif ctx["onOrigin"] and not ctx["gh"]:
        refusal = (f"{changes.host_unavailable()}, and this board merges the "
                   f"application on origin (BOARD_APP_MERGE)")
    return {"target": target, "appBase": app_base, "stacked": stacked,
            "targetSeen": seen, "refusal": refusal,
            "mode": "origin" if ctx["onOrigin"] else "local",
            "remote": ctx["remote"] or ""}


# ── one card ───────────────────────────────────────────────────────────


def _requirement(row: dict, at: str | None) -> dict:
    reason = row.get("reason") or ("passed" if row["state"] == "met" else "")
    if row["state"] == "pending":
        reason = row.get("reason") if row.get("reason") in (
            "running", "preparing") else "running"
    if row["state"] == "overridden":
        reason = "overridden"
    cls = gates.severity(reason)
    action_key = {"needed": "run-required-checks",
                  "problem": "read-failure",
                  "running": "cancel-check"}.get(cls) \
        if row["kind"] == "check" else None
    if row["kind"] == "review" and cls != "met":
        action_key = "review-pr" if row.get("policy") == "bench" \
            else "open-pr-url"
    if row["kind"] == "signoff" and cls not in ("met", "cannot-assess"):
        action_key = "confirm-criteria"
    name = row.get("name")
    if row["kind"] == "signoff":
        name = "Sign-off on acceptance criteria"
    if row["kind"] == "review":
        name = {"bench": "Review by Bench", "github": "GitHub approval",
                "separate-reviewer": "Approval by a separate reviewer"}.get(
                    row.get("policy"), f"Review ({row.get('policy')})")
    return {"kind": row["kind"], "name": name,
            "check": row.get("check"),
            "coverage": ("full run" if not row.get("scope")
                         else f"scope {row['scope']}")
            if row["kind"] == "check" else None,
            "state": row["state"], "reason": reason, "class": cls,
            "staleReason": row.get("staleReason"),
            "plain": gates.plain(reason, at),
            "why": row.get("why"), "remedy": row.get("remedy"),
            "action": action_key, "evidence": row.get("evidence"),
            # a record of a declared equivalent (card 104): its own check
            # name, evidence id, requester and checkout, never relabelled
            "reusedFrom": dict(row["acceptedFrom"])
            if row.get("acceptedFrom") else None,
            "equivalence": dict(row["equivalence"])
            if row.get("equivalence") else None,
            "otherCommits": row.get("otherCommits", 0)}


def _reviews(task: dict, candidate: dict, verdict_now: dict | None) -> dict:
    """Two review records, kept apart and each bound to the revision it is
    about: Bench's own verdict (`gates.verdict_at`, the durable record) and
    the newest round a review agent filed in the card's text. `current`
    says whether that revision is the commit this card would merge; one
    about another commit is history, never today's verdict. GitHub's
    formal state is the PR poll's, read beside these and not folded in."""
    commit = candidate.get("commit") if candidate["state"] == "present" else None
    bench = verdict_now
    if bench is None:
        recorded = gates.verdicts_for(task["file"])
        bench = recorded[0] if recorded else None
    rounds = [r for r in acceptance.rounds(task.get("body") or "")
              if r.get("verdict")]
    report = rounds[-1] if rounds else None

    def about(head: str | None) -> bool | None:
        if not commit or not head:
            return None
        return commit.startswith(head) or head.startswith(commit)

    return {
        "bench": ({"verdict": bench.get("verdict"), "head": bench.get("head"),
                   "current": about(bench.get("head"))} if bench else None),
        "report": ({"verdict": report["verdict"], "head": report.get("commit"),
                    "current": about(report.get("commit"))} if report else None),
    }


# ── the review status a card face wears (task 152) ─────────────────────
#
# One plain answer to "has the work as it stands now been approved by
# Bench's own PR review?", read from the same records the review rows
# and the merge gate read: Bench's durable verdict bound to the exact
# commit (`gates.verdict_at`, which already drops a verdict given before
# the newest retarget), the runs in this board's registry, and the
# candidate commit. Never from the lane, GitHub's formal state, a
# verdict parsed out of the card's text or a review checkout's label:
# each of those is shown elsewhere in its own words, and none of them is
# Bench's approval of this commit.
#
# It is a fact about review, not a delivery status: an approved review
# can sit beside a failed check, a missing sign-off or any other unmet
# requirement, and says nothing about whether the card may merge.

REVIEW_WORDS = {
    # word: (label, tone)
    "review-approved": ("Review approved", "calm"),
    "review-changes": ("Changes requested", "alarm"),
    "review-running": ("Review in progress", "working"),
    "review-outdated": ("Review outdated", "neutral"),
    "review-incomplete": ("Review incomplete", "alarm"),
    "review-unavailable": ("Review status unavailable", "neutral"),
    "review-pending": ("Review pending", "neutral"),
}

_NOT_MERGE = ("This is Bench's own PR review, not the hosting provider's "
              "formal approval, a "
              "person's acceptance or permission to merge.")


def _review_runs(task: dict, ctx: dict) -> list[dict]:
    """This card's PR-review runs, newest first. A relevance check rides
    the same `review` mode but pins no pull request, so it carries no
    `review` record and is not one of these."""
    return sorted((r for r in ctx["agents"]
                   if r.get("task") == task["file"] and r.get("review")),
                  key=lambda r: r.get("started") or 0, reverse=True)


def review_status(task: dict, ctx: dict, candidate: dict,
                  verdict_now: dict | None) -> dict:
    """The one review status a card wears: its word, label, sentence and
    tone, and the commit it is about. Reads only; see the block above."""
    filename = task["file"]
    commit = candidate.get("commit") if candidate.get("state") == "present" \
        else None
    runs = _review_runs(task, ctx)
    # A run whose process ended while its verdict is still being recorded
    # (`agents._reap_pr_review`) is in progress, not incomplete.
    running = next((r for r in runs if r.get("status") == "running"
                    or r.get("verdictPending")), None)
    recorded = gates.verdicts_for(filename)

    def out(word: str, sentence: str, head: str | None = None) -> dict:
        label, tone = REVIEW_WORDS[word]
        return {"word": word, "label": label, "tone": tone,
                "sentence": sentence, "head": head, "commit": commit}

    if running is not None:
        pinned = (running.get("review") or {}).get("head")
        about = (f"of {_short(pinned)}" if pinned
                 else "of the pull request's head")
        stale = bool(commit and pinned and pinned != commit)
        return out("review-running",
                   f"{running.get('name') or 'An agent'} is reviewing the PR "
                   f"{about}." + (f" The branch has since moved to "
                                  f"{_short(commit)}, so its verdict will be "
                                  f"about an earlier commit." if stale else "")
                   + " Nothing is approved until it records a verdict.",
                   pinned)
    if not commit:
        why = {"none-yet": "there is no implementation commit on this board "
                           "yet",
               "delivered": "the work was delivered and its branch is gone",
               }.get(candidate.get("state"),
                     "the commit this card would merge cannot be read here")
        return out("review-unavailable",
                   f"Which commit a review would be about is not known: "
                   f"{why}. No approval is shown without one.")
    if verdict_now is not None:
        head = verdict_now.get("head")
        if verdict_now.get("verdict") == "APPROVE":
            return out("review-approved",
                       f"Bench's PR review approved {_short(head)}, the "
                       f"commit this card would merge. {_NOT_MERGE}", head)
        return out("review-changes",
                   f"Bench's PR review asked for changes at {_short(head)}, "
                   f"the commit this card would merge.", head)
    # Nothing about this commit. A review run that ended without a verdict
    # after the newest recorded one is an incomplete review, said so.
    newest = recorded[0] if recorded else None
    since = float((newest or {}).get("recorded") or 0)
    last = runs[0] if runs else None
    if last is not None and last.get("status") != "running" \
            and float(last.get("started") or 0) > since:
        return out("review-incomplete",
                   f"The last PR review ({last.get('name') or 'an agent'}) "
                   f"ended without a verdict. Nothing about "
                   f"{_short(commit)} is approved; ◔ review PR asks again.")
    before = gates.verdict_before_retarget(filename, commit)
    if before is not None:
        word = ("approved" if before.get("verdict") == "APPROVE"
                else "asked for changes at")
        return out("review-outdated",
                   f"Bench's PR review {word} {_short(commit)} before the "
                   f"card's target changed, so it is about another diff. "
                   f"{_short(commit)} needs a fresh review.",
                   before.get("head"))
    if newest is not None:
        word = ("approved" if newest.get("verdict") == "APPROVE"
                else "asked for changes at")
        return out("review-outdated",
                   f"Bench's PR review {word} {_short(newest.get('head'))}, "
                   f"an earlier commit. {_short(commit)} has not been "
                   f"reviewed, so that verdict is history.",
                   newest.get("head"))
    rounds = [r for r in acceptance.rounds(task.get("body") or "")
              if r.get("verdict")]
    if rounds:
        # Text in the card claims a verdict Bench holds no record of: a
        # legacy or hand-written summary. Never read as an approval: about
        # an earlier commit it is history, and about this one (or no
        # recorded one) it is a claim bench cannot confirm.
        report = rounds[-1]
        said = report.get("commit") or ""
        if said and not (commit.startswith(said) or said.startswith(commit)):
            return out("review-outdated",
                       f"The card's text reports a PR review "
                       f"({report['verdict'].lower()}) of {_short(said)}, "
                       f"an earlier commit, and Bench has no recorded "
                       f"verdict about {_short(commit)}.", said)
        return out("review-unavailable",
                   f"The card's text reports a PR review "
                   f"({report['verdict'].lower()}) at "
                   f"{_short(report.get('commit'), 'an unrecorded revision')}"
                   f", but Bench has no recorded verdict for it, so it is "
                   f"not shown as an approval.", report.get("commit"))
    return out("review-pending",
               f"No Bench PR review of {_short(commit)} yet. ◔ review PR "
               f"asks for one.")


def _optional(policy_doc: dict, required: set[str], mine: list[dict]) -> int:
    """Checks this card may ask for, or has records of, that no gate
    requires — counted apart, never in a headline (design §7)."""
    offered = set(verify.offered_checks(policy_doc)) if policy_doc else set()
    ran = {r.get("check") for r in mine if r.get("check")}
    return len((offered | ran) - required)


def _merged_covers(pr: dict, candidate: dict) -> bool:
    """Does a PR merged on GitHub speak for the work here now? Only when
    the card's commit on this board is the head that PR merged, or is
    contained in it. A branch that gained commits after the merge — work
    returned, or reopened under the same card — is new, undelivered work:
    the old merge says nothing about it, so its requirements still apply.
    A card with no commit here has nothing newer to hide. One local read,
    and an unknown merged head is not a cover."""
    if candidate.get("state") != "present":
        return True
    commit, head = candidate.get("commit"), (pr or {}).get("head")
    if not head:
        return False
    return commit == head or gates.contained(commit, head)


def compute(task: dict, ctx: dict | None = None, *, full: bool = False) -> dict:
    """The delivery status of one card. `ctx` is the board-wide read,
    hoisted; `full` adds the parts only the card's own reading carries."""
    ctx = ctx or _context()
    filename, stage = task["file"], task["stage"]
    plan = plans.recorded(filename)
    pr = changes.pr_snapshot(filename)
    recovery = completion_mod.recovery_public(filename)
    scope = gates.scope_for(task)
    member = bool(task.get("phase")) and not task.get("isPhase")
    if member:
        scope = "member"
    mine = [r for r in ctx["records"] if r.get("task") == filename]
    candidate = gates.candidate(
        filename, scope, task=task, tips=ctx["tips"], records=mine, pr=pr,
        completion=recovery, remote_tips=ctx["remoteTips"], plan=plan,
        delivery_record=completion_mod.delivered_record(filename))
    alive, failure = _runs(task, ctx)
    target = _target(task, plan)
    completion = _completion(task, plan, target, pr, ctx)
    holder = task.get("assignee") or ""
    others_hold = bool(ctx["me"]) and bool(holder) and holder != ctx["me"]
    on_board = ("on this board"
                if (others_hold or ctx["remote"]) else "")
    findings = (acceptance.summary(task.get("body") or "", filename) or {})
    open_findings = int(findings.get("openFindings") or 0)
    # Bench's own verdict at the current commit. Where a review is a
    # requirement, its row already carries this; where none is, a request
    # for changes at this exact commit is still a current problem — the
    # card face used to say so from a cascade of its own, and must not
    # lose it now that it draws this result instead.
    verdict_now = (gates.verdict_at(filename, candidate.get("commit"))
                   if candidate["state"] == "present" else None)

    decision = None
    if candidate["state"] == "present" or stage in ("review", "in-progress"):
        try:
            decision = gates.evaluate(filename, scope, task=task,
                                      tips=ctx["tips"], policy=ctx["policy"],
                                      records=ctx["records"])
        except (ValueError, OSError) as exc:
            decision = {"decision": "blocked", "requirements": [],
                        "unmet": [], "pending": [], "gated": True,
                        "summary": f"this card's gate could not be read: {exc}",
                        "head": {"commit": None}, "overrides": {},
                        "readError": str(exc)}
    at = candidate.get("commit") if candidate["state"] == "present" else None
    rows = (decision or {}).get("requirements") or []
    merged_elsewhere = bool(pr and pr.get("merged")) and stage == "review" \
        and not member and _merged_covers(pr, candidate)
    requirements = [] if merged_elsewhere else [_requirement(r, at)
                                                for r in rows]
    if candidate["state"] != "present" and stage != "review":
        # Before there is a commit, a requirement is a step that lies
        # ahead — listed, in the neutral register, never "can't assess".
        for row in requirements:
            if row["reason"] == "head-unknown":
                row.update({"class": "needed", "plain": "Needed before merge",
                            "action": None})
    required = {r.get("check") for r in rows if r.get("kind") == "check"}
    counts = {"required": len(requirements),
              "met": len([r for r in requirements if r["state"] == "met"])}
    history = {
        "older": sum(r.get("otherCommits", 0) for r in rows),
        "optional": _optional(profiles.document(), required, mine),
        "obsolete": len(rows) if merged_elsewhere else 0,
    }
    gated = bool((decision or {}).get("gated")) or \
        (decision or {}).get("decision") == "blocked"

    # task 189: the candidate against the target it lands in, kept apart
    # from the review — read locally, from the last fetch
    fit = None
    if stage == "review" and candidate["state"] == "present" \
            and not merged_elsewhere:
        fit = integration.reading(
            task, head=candidate.get("commit"), decision=decision,
            pr_state=changes.pr_state(filename), plan=plan,
            remote_tips=ctx["remoteTips"] if ctx["remote"] else None,
            # only an update of this card, or the repair run one handed a
            # conflict to, is "updating" — a review or an ordinary repair
            # running on the card is not (the run is shown as itself)
            updating=bool((alive or {}).get("integration"))
            or filename in ctx.get("updating", ()))

    status, sentence, primary, others = _decide(
        task=task, ctx=ctx, candidate=candidate, decision=decision,
        requirements=requirements, alive=alive, failure=failure,
        recovery=recovery, pr=pr, completion=completion,
        open_findings=open_findings, on_board=on_board,
        others_hold=others_hold, holder=holder, member=member,
        merged_elsewhere=merged_elsewhere, gated=gated,
        verdict_now=verdict_now, waiting=stacks.waiting(_stack(task, ctx)),
        fit=fit)

    open_failed = (changes.open_failure(filename)
                   if stage == "review" and not task.get("pr")
                   and not (pr or {}).get("url") else None)
    if open_failed and status != "not-published":
        # task 166: the actual reason the PR could not be opened, not a
        # generic "no pull request" — and the supported way to retry it
        sentence = (f"{sentence} Opening its PR failed: "
                    f"{open_failed['detail']} Settle that, then ↑ open PR.")
    if pr and pr.get("merged") and not merged_elsewhere \
            and candidate["state"] == "present" and not member:
        sentence = (f"{sentence} The PR merged on GitHub at "
                    f"{_short(pr.get('head'))} does not contain "
                    f"{_short(candidate.get('commit'))}, so it does not "
                    f"deliver this work.")
    label, tone = WORDS[status]
    if status == "agent-working":
        label = label.replace("{name}", (alive or {}).get("name") or "An agent")
    if status == "returned" and candidate["state"] == "unavailable":
        tone = "alarm"
    observed = (pr or {}).get("observed")
    out = {
        "v": V, "task": filename, "stage": stage,
        "status": {"word": status, "label": label, "sentence": sentence,
                   "tone": tone},
        "primary": primary,
        "others": others,
        "candidate": {"state": candidate["state"],
                      "commit": candidate.get("commit"),
                      "ref": candidate.get("ref"),
                      "qualifier": on_board or None,
                      "remote": candidate.get("remote")},
        "requirements": requirements,
        "counts": counts,
        "history": history,
        "reviews": _reviews(task, candidate, verdict_now),
        "review": review_status(task, ctx, candidate, verdict_now),
        "integration": _integration_public(fit),
        "source": {
            "pr": ({"observed": observed, "from": pr.get("from"),
                    "age": round(ctx["now"] - observed, 1)
                    if observed else None} if pr else None),
            "local": True,
        },
        "read": {"at": ctx["now"], "local": True},
    }
    decided = ({"decision": decision.get("decision"),
                "scope": decision.get("scope", scope),
                "summary": decision.get("summary"),
                "gated": decision.get("gated"),
                "head": decision.get("head")}
               if decision else None)
    # The identity of everything the full reading says beyond the compact
    # one — every requirement row with its prose and remedy, the merge
    # rule's reading and the completion — so the page refetches the rows
    # whenever they change, even when the word, the counts and the
    # classes do not (a check replaced by another, a remedy reworded).
    out["detail"] = hashlib.sha256(json.dumps(
        {"requirements": requirements, "decision": decided,
         "completion": completion, "recovery": recovery},
        sort_keys=True, default=str).encode()).hexdigest()[:16]
    if full:
        out["candidate"].update(why=candidate.get("why"),
                                traces=candidate.get("traces"))
        out["decision"] = decided
        out["completion"] = completion
        out["pr"] = pr
        out["recovery"] = recovery
    return out


def _phase_repair_action(filename: str, has_pr: bool, pr: dict | None) -> dict | None:
    """↻ act on PR on a phase card in review (task 174): it repairs the
    phase's own integration branch and PR, named on the action, so the
    person sees the target before the confirmation. A pull request this
    board observed merged or closed has nothing to push to, and says so
    disabled. Read from the PR snapshot only — the launch asks the whole
    target contract again (`agents.repair_target`)."""
    if not has_pr:
        return None
    branch = config.PHASE_BRANCH_PREFIX + filename[:-3]
    ended = str((pr or {}).get("prState") or "").upper()
    if (pr or {}).get("merged") or ended in ("MERGED", "CLOSED"):
        word = "merged" if (pr or {}).get("merged") or ended == "MERGED" \
            else "closed"
        return action("act-pr", "↻ act on PR", enabled=False,
                      why=f"The integration PR is {word}, so a push to "
                          f"{branch} would not reach it. Open a card for "
                          f"the change instead.", target=branch)
    return action("act-pr", "↻ act on PR", target=branch,
                  title=f"An agent repairs the integration PR on {branch} — "
                        f"the same branch and PR; its members stay merged")


def _decide(*, task, ctx, candidate, decision, requirements, alive,
            failure, recovery, pr, completion, open_findings, on_board,
            others_hold, holder, member, merged_elsewhere, gated,
            verdict_now=None, waiting=None, fit=None):
    """The precedence (design §3.3), first row that applies wins. Returns
    (word, sentence, primary, others)."""
    stage = task["stage"]
    filename = task["file"]
    others: list[dict] = []
    commit = candidate.get("commit")
    here = _short(commit)
    no_commit = (f"No implementation commit {on_board} yet."
                 if on_board else "No implementation commit yet.")
    has_pr = bool(task.get("pr") or (pr or {}).get("url"))
    pr_url = task.get("pr") or (pr or {}).get("url")
    is_phase = bool(task.get("isPhase"))

    def start_action() -> dict:
        if is_phase:
            return action("run-phase",
                          "▸ take over" if others_hold else "▸ run phase")
        if others_hold:
            return action("takeover", "▸ take over")
        return action("start", "▸ start work")

    # 1. merge & clean up is in flight
    if filename in ctx["completing"]:
        step = ctx["completing"][filename].get("step") or ""
        return ("merging", f"Merge & clean up is running. {step}".strip(),
                None, [])

    # 2. merged, not published
    if recovery:
        said = recovery.get("detail") or "the remote does not hold it yet"
        target = recovery.get("target") or "the base"
        if recovery.get("deliveredElsewhere"):
            # task 166: the work is on the remote through another merge;
            # the merge made here is the thing left unpublished
            sentence = (f"Delivered elsewhere: the remote's {target} already "
                        f"holds {_short(recovery.get('commit'))} through "
                        f"another merge, and this checkout's merge "
                        f"{_short(recovery.get('merge'))} is not published.")
        else:
            merged = (f" (merge {_short(recovery.get('merge'))})"
                      if recovery.get("merge")
                      and recovery.get("merge") != recovery.get("commit")
                      else "")
            sentence = (f"Merged locally into {target} at "
                        f"{_short(recovery.get('commit'))}{merged}, but not on "
                        f"the remote: {said}.")
        if recovery.get("action"):
            sentence += f" Next: {recovery['action']}"
        return ("not-published", sentence,
                action("complete", "Retry publication"), [])

    # 3. the latest run on this stage failed
    if failure is not None:
        mode = failure.get("mode") or "work"
        rc = failure.get("rc")
        again = {"act-pr": action("act-pr", "↻ act on PR"),
                 "review-pr": action("review-pr", "◔ review PR"),
                 "review": action("start", "▸ start work")
                 if stage == "in-progress" else None}.get(mode) \
            or (start_action() if stage == "in-progress" else None)
        others = [action("read-failure", "Read what happened")]
        tail = (failure.get("excerpt") or "").strip().splitlines()
        sentence = f"The last run failed (rc={rc})."
        if tail:
            sentence += f" It ended on: {tail[-1][:160]}"
        return ("run-failed", sentence,
                again or action("read-failure", "Read what happened"),
                others if again else [])

    # 4. an agent is running
    if alive is not None:
        running = [r for r in requirements if r["class"] == "running"]
        hold = action("hold", "‖ hold", agent=alive.get("id"))
        sentence = f"{alive.get('name') or 'An agent'} is working on it."
        if running:
            sentence += " Checks are running."
        return ("agent-working", sentence, None, [hold])

    # 5. a phase card is running a member
    snap = ctx["phases"].get(filename) or {}
    if is_phase and snap.get("running"):
        working = next((m for m in snap.get("members") or []
                        if m.get("state") in ("working", "checking")), None)
        sentence = "The phase is running" + (
            f" — #{working['number']} is in flight." if working else ".")
        return ("phase-running", sentence, None,
                [action("hold-phase", "‖ hold")])

    # 6. backlog / to-do
    if stage in ("backlog", "to-do"):
        nxt = "to-do" if stage == "backlog" else "in-progress"
        move = action("move", f"Move to {nxt}", to=nxt)
        state_ = candidate["state"]
        if state_ == "present":
            return ("returned",
                    f"Returned with work on {candidate.get('ref')} at {here}. "
                    f"Move it to in-progress to continue.",
                    action("move", "Move to in-progress", to="in-progress"),
                    [])
        if state_ == "unavailable":
            check = _check_remote_action(ctx, candidate)
            return ("cannot-assess",
                    "Earlier work exists for this card, but its branch is not "
                    "on this board.", check or move,
                    [move] if check else [])
        word = "not-started" if stage == "backlog" else "queued"
        if state_ == "delivered":
            return (word, f"Not started — an earlier implementation was "
                          f"delivered at {here}.", move, [])
        if stage == "backlog":
            return (word, "Move it to to-do when it is next.", move, [])
        return (word, "Move it to in-progress, then ▸ start work.", move, [])

    # 7 / 8. in progress, nobody on it
    if stage == "in-progress":
        # the launch this stage offers is ▸ start work (or ▸ take over), so
        # the check names that intent and that ownership rule itself rather
        # than leaving the page to assume one
        launch = action("launch-check", "Check whether it would start",
                        intent="work", takeover=bool(others_hold))
        problem = _current_problem(requirements, candidate, open_findings,
                                   decision, verdict_now)
        if is_phase and snap.get("halted"):
            return ("run-failed",
                    f"The phase halted: {snap.get('halted')}",
                    action("phases", "⟶ phases"),
                    [action("hold-phase", "‖ hold")])
        if problem and not is_phase:
            word, sentence = problem
            return (word, sentence, start_action(),
                    [action("read-failure", "Read the failure"), launch])
        extra = [launch]
        if candidate["state"] == "unavailable" or (
                candidate["state"] == "none-yet" and others_hold):
            check = _check_remote_action(ctx, candidate)
            if check:
                extra.append(check)
        if candidate["state"] == "present":
            return ("in-progress",
                    f"Work is on {candidate.get('ref')} at {here}.",
                    start_action(), extra)
        lead = f"{holder} holds it. " if others_hold else ""
        tail = (" Their branch may not be fetched."
                if others_hold and candidate["state"] == "none-yet" else "")
        return ("ready-to-start", f"{lead}{no_commit}{tail}".strip(),
                start_action(), extra)

    # 20. done
    if stage == "done":
        return ("delivered",
                f"Delivered{f' at {here}' if commit else ''}.", None, [])

    # review from here on
    # 9. a phase member waits for its phase
    if member:
        phase = task["phase"]
        return ("waiting-for-phase",
                f"Waiting for phase #{phase.get('number')} "
                f"{phase.get('title') or ''} to merge it.".replace("  ", " "),
                None, [action("phases", "⟶ phases")])

    # 17 (read early). The PR was merged on GitHub.
    if merged_elsewhere:
        finish = action("complete", "Finish: clean up")
        if completion["stacked"]:
            finish = action("complete", "Finish: clean up", enabled=False,
                            why=completion["refusal"])
        return ("merged-elsewhere",
                f"The PR was merged on GitHub at "
                f"{_short((pr or {}).get('head'))}. Delivered through the PR; "
                f"nothing is left to review.", finish, [])

    # 11. merge rules do not resolve — read before 10: rules that do not
    # resolve make every assessment impossible, and their fix is Settings
    if decision and decision.get("decision") == "blocked" and any(
            (r.get("reason") == "policy-refused") for r in requirements):
        return ("rules-invalid",
                f"Merge rules invalid: {decision.get('summary')}",
                action("settings-merge-rules", "Open Settings → Merge rules"),
                [])

    # 10. nothing to assess
    if candidate["state"] in ("unavailable", "none-yet") and gated:
        check = _check_remote_action(ctx, candidate)
        # a phase card's work is its integration branch: walking the card
        # back would start nothing (task 174) — its lane is where it is read
        back = (action("phases", "⟶ phases") if is_phase else
                action("move", "Move back to in-progress", to="in-progress"))
        return ("cannot-assess",
                f"Cannot assess: {no_commit[0].lower()}{no_commit[1:]}",
                check or back, [back] if check else [])

    if is_phase:
        act_pr = _phase_repair_action(filename, has_pr, pr)
    else:
        act_pr = action("act-pr", "↻ act on PR") if has_pr \
            and not (pr or {}).get("merged") else None
    review_pr = action("review-pr", "◔ review PR") if has_pr else None

    # 12. a current-commit problem
    problem = _current_problem(requirements, candidate, open_findings,
                               decision, verdict_now)
    if problem:
        word, sentence = problem
        if is_phase:
            # a phase repairs its integration branch in place (task 174):
            # never "move back", which would start no supported run
            fix = act_pr if has_pr else action("open-pr", "↑ open PR")
        else:
            fix = act_pr or action("move", "Move back to in-progress",
                                   to="in-progress")
        rest = [action("read-failure", "Read the failure")]
        if review_pr:
            rest.append(review_pr)
        return (word, sentence, fix, rest)

    checks = [r for r in requirements if r["kind"] == "check"]
    batch = (ctx.get("batches") or {}).get(task["file"])
    # 13. running
    running = [r for r in checks if r["class"] == "running"]
    if running:
        rest = [action("cancel-check", "Cancel", evidence=r["evidence"])
                for r in running if r.get("evidence")]
        if batch:
            rest.insert(0, _batch_action(batch))
        return ("checks-running",
                f"{_names(running)} {'is' if len(running) == 1 else 'are'} "
                f"running on {here}.", None, rest)

    # 22, read before 14–16 (task 189). The target moved past this head
    # while its checks or review are still to come: they would be spent on
    # work not yet combined with the accepted target, and asked for again
    # once it is. So the update comes first; the checks and the review stay
    # offered beside it, never hidden.
    if decision and decision.get("decision") == "blocked":
        later = []
        if any(r["class"] == "needed" for r in checks):
            later.append(action("run-required-checks", "Run required checks"))
        if review_pr:
            later.append(review_pr)
        stale = _stale_target(task, ctx, fit, here, filename, alive,
                              "its checks or review are still to come",
                              later)
        if stale:
            return stale

    # 14. needed
    needed = [r for r in checks if r["class"] == "needed"]
    if needed:
        first = needed[0]
        sentence = f"Checks needed: {_needed_sentence(first, here)}"
        if len(needed) > 1:
            sentence += f" (and {len(needed) - 1} more)"
        rest = [action("evidence", "◫ evidence")]
        if review_pr:
            rest.append(review_pr)
        if batch:
            return ("checks-needed", sentence, _batch_action(batch), rest)
        offered = ctx.get("workChecks") or set()
        if not any(r.get("check") in offered for r in needed):
            # The rule requires what a person's request may not ask for:
            # the configuration is the remedy, never a wider grant.
            run = action("run-required-checks", "Run required checks",
                         enabled=False,
                         why=required.remedy_unavailable(
                             needed[0].get("check") or ""))
        else:
            run = action("run-required-checks", "Run required checks")
        return ("checks-needed", sentence, run, rest)

    # 15. review
    review_rows = [r for r in requirements if r["kind"] == "review"
                   and r["state"] not in ("met", "overridden")]
    if review_rows:
        row = review_rows[0]
        if not has_pr:
            return ("review-needed", "Review needed: no pull request yet.",
                    action("open-pr", "↑ open PR"), [])
        if row["name"] == "Review by Bench":
            return ("review-needed", f"Review needed for {here}.",
                    review_pr, [])
        return ("review-needed", f"Review needed: {row['plain']}.",
                action("open-pr-url", "Open the PR", url=pr_url),
                [review_pr] if review_pr else [])

    # 16. sign-off — only where a merge rule asks for one (design §8.3).
    # Without `signoff` there is no such row, so nothing here asks anyone
    # to confirm criteria as a matter of course.
    signoff_rows = [r for r in requirements if r["kind"] == "signoff"
                    and r["state"] not in ("met", "overridden")]
    if signoff_rows:
        row = signoff_rows[0]
        label = ("Track acceptance" if row["reason"] == "signoff-untracked"
                 else "Confirm criteria")
        return ("signoff-needed",
                f"Sign-off needed for {here}: {row['plain'][0].lower()}"
                f"{row['plain'][1:]}.",
                action("confirm-criteria", label), [])

    # 21 (read before 18 and 19). Stacked on a card whose work has not
    # landed: whatever this card's own requirements say, it lands after
    # its parent does. The edge's own words come with it when it is not
    # simply sitting on its parent.
    if waiting and (decision is None or decision.get("decision") in (
            "allowed", "overridden", "ungated")):
        parent = waiting["parent"]
        who = f"#{parent['number']}" if parent.get("number") else parent["file"]
        lead = (f"Requirements met for {here}. "
                if decision and decision.get("decision") != "ungated"
                else "No merge requirements are configured. That is not a "
                     "pass. ")
        edge = waiting.get("edge") or {}
        said = (f" {edge.get('sentence')}"
                if edge.get("state") not in (None, "on-parent") else "")
        sentence = (f"{lead}Waiting for {who} to land: this card's change "
                    f"request targets {waiting['target']}, and it lands in "
                    f"{waiting['landing']} after its parent does.{said}")
        if not has_pr and candidate["state"] == "present" and ctx["remote"]:
            return ("waiting-for-parent", sentence,
                    action("open-pr", "↑ open PR"), [])
        rest = [review_pr] if review_pr else []
        if pr_url:
            return ("waiting-for-parent", sentence,
                    action("open-pr-url", "Open the PR", url=pr_url), rest)
        return ("waiting-for-parent", sentence, None, rest)

    # 22 (read before 18, task 189; and before 14-16, above). Met at a
    # head that does not hold the target it lands in: see `_stale_target`.
    if decision and decision.get("decision") in ("allowed", "overridden"):
        stale = _stale_target(task, ctx, fit, here, filename, alive,
                              "requirements are met")
        if stale:
            return stale

    # 18. met or overridden
    if decision and decision.get("decision") in ("allowed", "overridden"):
        overridden = decision["decision"] == "overridden"
        word = "ready-overridden" if overridden else "ready-to-merge"
        if completion["stacked"]:
            return (word,
                    f"Requirements met for {here}. This card lands through "
                    f"its PR into {completion['target']}, not merge & clean "
                    f"up.",
                    action("open-pr-url", "Open the PR", url=pr_url)
                    if pr_url else None, [])
        merge = action("complete", "Merge & clean up",
                       enabled=not completion["refusal"],
                       why=completion["refusal"] or None)
        if overridden:
            applied = ((decision.get("overrides") or {}).get("applied")
                       or {})
            return (word,
                    f"{applied.get('actor') or 'Someone'} proceeded over "
                    f"{', '.join(applied.get('gates') or [])} at {here}: "
                    f"{applied.get('reason') or ''}. Still unmet.",
                    merge, [])
        if (fit or {}).get("unpushed"):
            # task 189: its own clean update was refused by the remote, so
            # the PR does not show this head; pushing it again comes first
            busy = filename in ctx.get("updating", ())
            push = action("update-target", integration.UPDATE_ACTION,
                          enabled=not busy and not alive,
                          why=("an update or a run is already working on it"
                               if busy or alive else None),
                          target=fit.get("target"))
            return (word, f"Checks and review complete for {here}, but its "
                          f"update against {fit.get('target')} was not "
                          f"pushed: the pull request does not show it yet.",
                    push, [merge])
        return (word, f"Ready to merge: checks and review complete for "
                      f"{here}.", merge, [])

    # 19. ungated
    if decision is None or decision.get("decision") == "ungated":
        merge = action("complete", "Merge & clean up",
                       enabled=not completion["refusal"],
                       why=completion["refusal"] or None)
        if not has_pr and candidate["state"] == "present" and ctx["remote"]:
            return ("review", "No merge requirements are configured. That "
                              "is not a pass.",
                    action("open-pr", "↑ open PR"), [merge])
        verdicts = gates.verdicts_for(filename)
        if has_pr and not verdicts:
            return ("review", "No merge requirements are configured. That "
                              "is not a pass.", review_pr, [merge])
        rest = [review_pr] if review_pr else []
        return ("review", "No merge requirements are configured. That is "
                          "not a pass.", merge, rest)

    # Anything else is a reading this table did not foresee: say so in
    # the alarm register rather than inventing a next step.
    return ("cannot-assess",
            f"Cannot assess: the gate reads {decision.get('decision')} — "
            f"{decision.get('summary')}", action("gates", "⚖ merge rules"),
            [])


def _stale_target(task: dict, ctx: dict, fit: dict | None, here: str,
                  filename: str, alive, said: str,
                  rest: list | None = None):
    """Row 22 (task 189): a head that does not hold the target it lands in,
    where the merge rule requires a check. Its checks are about work not
    yet combined with the accepted target, so it is not ready — whatever a
    provider says about mergeability — and its next step is the update. A
    card on a named base no card owns (it lands through its PR) is read
    the same way; one stacked on another card keeps its stack edge's own
    reading (`parent-moved` and **Update from <parent>**, task 119)."""
    if not integration.stale(fit) or (_stack(task, ctx) or {}).get("parent"):
        return None
    word = ("target-conflicts" if fit["state"] == "conflicts"
            else "target-changed")
    if fit["state"] == "unknown":
        return (word, f"{said[0].upper()}{said[1:]} for {here}, but "
                f"{fit['sentence']}.",
                _check_remote_action(ctx, {})
                or action("gates", "⚖ merge rules"), list(rest or []))
    busy = filename in ctx.get("updating", ())
    update = action("update-target", integration.UPDATE_ACTION,
                    enabled=not busy and not alive,
                    why=("an update or a run is already working on it"
                         if busy or alive else None),
                    target=fit["target"])
    lead = ("Conflicts with target" if word == "target-conflicts"
            else "Target changed")
    return (word, f"{lead}: {said} for {here}, but {fit['sentence']}",
            update, list(rest or []))


def _names(rows: list[dict]) -> str:
    names = [str(r.get("name")) for r in rows]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 \
        else (names[0] if names else "nothing")


def _batch_action(batch: dict) -> dict:
    """While a batch of required checks is in flight on the card, the
    action opens its progress (the same sheet) rather than asking again."""
    label = (f"Required checks: {batch.get('done', 0)} of "
             f"{batch.get('total', 0)} answered")
    return action("run-required-checks", label)


def _needed_sentence(row: dict, here: str) -> str:
    reason, name = row["reason"], row["name"]
    if reason == "cancelled":
        return f"{name} was cancelled before a result on {here}."
    if reason == "restarted":
        return f"{name} has no result on {here} — the board restarted."
    if reason == "stale":
        return (f"{name} passed on {here}, and "
                f"{(row.get('why') or 'what it depends on changed').split(': ', 1)[-1]}")
    if reason == "source-dirty":
        return f"{name} ran with uncommitted files. Commit, then run checks."
    return f"{name} has not run on {here}."


def _current_problem(requirements: list[dict], candidate: dict,
                     open_findings: int, decision: dict | None,
                     verdict_now: dict | None = None):
    """A genuine problem on the *current* commit: a check that failed or
    could not run there, a change request at it, or open findings. A
    failure at another commit is history and never this (design §6).

    `verdict_now` is Bench's own newest verdict about this exact commit.
    It counts only where no review requirement already speaks — a
    required review's row is the authority there, and it is read once."""
    if candidate["state"] != "present":
        return None
    failed = [r for r in requirements if r["kind"] == "check"
              and r["reason"] == "failed"]
    unable = [r for r in requirements if r["kind"] == "check"
              and r["reason"] in ("prepare-failed", "denied", "error")]
    changes = [r for r in requirements if r["kind"] == "review"
               and r["reason"] in ("verdict-changes", "changes-requested")]
    reviewed = any(r["kind"] == "review" for r in requirements)
    here = _short(candidate.get("commit"))
    review_bit = ""
    if changes:
        review_bit = ("changes were requested by the Bench review of "
                      f"{here}" if changes[0]["reason"] == "verdict-changes"
                      else "a GitHub review asks for changes")
    elif not reviewed and (verdict_now or {}).get("verdict") == "REQUEST CHANGES":
        review_bit = f"changes were requested by the Bench review of {here}"
    elif any(r["kind"] == "signoff" and r["reason"] == "signoff-needs-work"
             for r in requirements):
        review_bit = "a person recorded an acceptance criterion as needing work"
    elif open_findings:
        review_bit = (f"{open_findings} review finding"
                      f"{'' if open_findings == 1 else 's'} "
                      f"{'is' if open_findings == 1 else 'are'} open")
    if failed:
        sentence = f"Check failed: {_names(failed)} on {here}."
        if review_bit:
            sentence += f" Also, {review_bit}."
        return ("check-failed", sentence)
    if unable:
        row = unable[0]
        sentence = f"{row['name']} could not run: {row['plain'].lower()}."
        if review_bit:
            sentence += f" Also, {review_bit}."
        return ("cannot-run-check", sentence)
    if review_bit:
        return ("changes-requested",
                f"Changes requested: {review_bit}.")
    return None


def _check_remote_action(ctx: dict, candidate: dict) -> dict | None:
    if not ctx["remote"]:
        return None
    return action("check-remote", "Check the remote")


# ── the board's compact reading, cached ────────────────────────────────

_LOCK = threading.Lock()
_EPOCH = 0
_TTL = 15.0
_BOARD: tuple | None = None

COMPACT = ("status", "primary", "candidate", "counts", "history", "detail",
           "reviews", "review", "integration")

# What an action keeps in the compact reading: enough for the page to draw
# it and route it, never anything the page would re-decide from.
_ACTION_KEYS = ("action", "label", "who", "enabled", "why", "arm", "to",
                "url", "agent", "evidence", "intent", "takeover",
                # task 174: what an action acts on, drawn beside it
                "target", "title")


def bump() -> None:
    """Drop the memoised board reading: something this module's inputs
    depend on changed in a way the fingerprint cannot see."""
    global _EPOCH, _BOARD
    with _LOCK:
        _EPOCH += 1
        _BOARD = None


forget_cached = bump


def _file_stamp(path) -> str | None:
    """A digest of one record file's bytes, or None when there is none —
    content rather than a modification time, so two writes inside one
    second are still two different keys."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _fingerprint(collected: dict, ctx: dict, tasks: list[dict]) -> str:
    """Everything a reading depends on, digested. The cache is keyed by
    this *and* a time bucket, so a change to any authority — the source,
    the policy, evidence, a verdict, a card's own text (findings, the PR
    line, its assignee), a completion, a run, a phase, the PR snapshot —
    is a different key, not a stale answer."""
    snaps = []
    for task in tasks:
        snap = changes.pr_snapshot(task["file"]) or {}
        snaps.append((task["file"], snap.get("head"), snap.get("merged"),
                      snap.get("prState"), snap.get("observed")))
    parts = {
        "epochs": [_EPOCH, gates.epoch(), evidence.epoch()],
        "policy": ctx["policy"].get("revision"),
        "tips": sorted(ctx["tips"].items()),
        "remoteTips": sorted(ctx["remoteTips"].items()),
        "cards": [(t["file"], t["stage"], t.get("assignee"),
                   hashlib.sha256((t.get("body") or "").encode()).hexdigest())
                  for t in tasks],
        "agents": sorted((a.get("id"), a.get("task"), a.get("status"),
                          bool(a.get("failure")),
                          bool(a.get("verdictPending")))
                         for a in ctx["agents"]),
        "completing": sorted(ctx["completing"]),
        "batches": sorted((k, (v or {}).get("done"), (v or {}).get("total"))
                          for k, v in (ctx.get("batches") or {}).items()),
        "workChecks": sorted(ctx.get("workChecks") or ()),
        "recoveries": completion_mod.recoveries(),
        "phases": sorted((k, v.get("running"), v.get("halted"))
                         for k, v in ctx["phases"].items()),
        "snapshots": snaps,
        # each stacked card's edge reads its parent's tip, records and
        # snapshot, so the stack reading is part of every card's identity
        "stacks": ctx.get("stacks") or {},
        # The recorded plan (a ⌖ replan rewrites it) and the delivered
        # record (a finished merge & clean up writes it) are both read by
        # every card's reading, so both are part of its identity.
        "plans": [(t["file"], _file_stamp(plans.plan_path(t["file"])))
                  for t in tasks],
        "delivered": [(t["file"], completion_mod.delivered_record(t["file"]))
                      for t in tasks],
        # why a review card's PR could not be opened (task 166)
        "prOpen": [(t["file"], changes.open_failure(t["file"]))
                   for t in tasks if t["stage"] == "review"],
        # task 189: the provider's mergeability and this board's update
        # attempts are read by every review card's integration reading
        "integration": [(t["file"],
                         (changes.pr_state(t["file"]) or {}).get("conflicts"),
                         (changes.pr_state(t["file"]) or {}).get("head"),
                         _file_stamp(integration.attempts_path(t["file"])))
                        for t in tasks if t["stage"] == "review"],
        "updating": sorted(ctx.get("updating") or ()),
        "records": len(ctx["records"]),
        "worktrees": sorted(p.name for p in config.WORKTREES.iterdir())
        if config.WORKTREES.is_dir() else [],
        "me": ctx["me"], "remote": ctx["remote"],
        "base": config.APP_BASE, "onOrigin": ctx["onOrigin"],
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True,
                                     default=str).encode()).hexdigest()


def compact(found: dict) -> dict:
    out = {key: found[key] for key in COMPACT}

    def slim(a: dict) -> dict:
        return {k: a[k] for k in _ACTION_KEYS if k in a}

    out["primary"] = slim(found["primary"]) if found["primary"] else None
    out["others"] = [slim(a) for a in found.get("others") or []]
    # the requirement rows' classes, without their prose: the face's one
    # ⚖ chip needs to know "needed" from "problem", and nothing more
    out["classes"] = sorted({r["class"] for r in found["requirements"]})
    out["pr"] = found["source"]["pr"]
    return out


def board(collected: dict, phase_state: dict | None = None,
          stack_state: dict | None = None) -> dict:
    """The compact reading for every card, for `/api/state`. Memoised by a
    fingerprint of every input and a time bucket — the cache is never
    authority, and a mutation never reads it. `stack_state` is the stack
    reading `/api/state` already took (`stacks.board`); read here when the
    caller has none."""
    global _BOARD
    tasks = [task for stage in collected.get("stages", [])
             for task in stage["tasks"]]
    ctx = _context(phase_state)
    # an edge is about two cards, so with no reading handed in it is taken
    # over the whole board — a caller may pass a subset of its cards
    ctx["stacks"] = (stack_state if stack_state is not None
                     else stacks.board(taskfiles.collect()))
    key = (_fingerprint(collected, ctx, tasks), int(time.time() / _TTL))
    with _LOCK:
        cached = _BOARD
    if cached is not None and cached[0] == key:
        return cached[1]
    found = {}
    for task in tasks:
        try:
            found[task["file"]] = compact(compute(task, ctx))
        except (ValueError, OSError) as exc:
            found[task["file"]] = {
                "status": {"word": "cannot-assess", "label": "Can't assess",
                           "sentence": f"The delivery status could not be "
                                       f"read: {exc}", "tone": "alarm"},
                "primary": None, "others": [], "classes": [],
                "candidate": {"state": "unavailable"},
                "counts": {"required": 0, "met": 0},
                "history": {"older": 0, "optional": 0, "obsolete": 0},
                "detail": None, "pr": None,
                "reviews": {"bench": None, "report": None},
                "review": {"word": "review-unavailable",
                           "label": REVIEW_WORDS["review-unavailable"][0],
                           "tone": "neutral", "head": None, "commit": None,
                           "sentence": "The review status could not be "
                                       "read here, so no approval is "
                                       "shown."}}
    with _LOCK:
        _BOARD = (key, found)
    return found


def for_card(filename: str, stage: str | None = None,
             phase_state: dict | None = None) -> dict:
    """The full reading for one card, always recomputed."""
    task = _find(filename, stage)
    return compute(task, _context(phase_state), full=True)


def _stack(task: dict, ctx: dict) -> dict | None:
    """This card's stack reading, taken once per pass for the whole board
    (`stacks.board`) and kept in `ctx`."""
    if ctx.get("stacks") is None:
        ctx["stacks"] = stacks.board(taskfiles.collect())
    return ctx["stacks"].get(task["file"])


def _find(filename: str, stage: str | None) -> dict:
    if not filename.endswith(".md") or "/" in filename:
        raise ValueError(f"no card {filename!r} on this board")
    for group in taskfiles.collect()["stages"]:
        for task in group["tasks"]:
            if task["file"] == filename:
                return task
    raise ValueError(f"no card {filename!r} on this board")


# ── the two explicit actions ───────────────────────────────────────────


def check_remote(filename: str) -> dict:
    """**Check the remote**: one bounded fetch of this card's own two
    branch names, `task/<stem>` and `phase/<stem>`, into remote-tracking
    refs — on a person's click, never on a read.

    Asks the remote which of the two exist first (`ls-remote`, bounded),
    so an absent ref is an answer rather than an error, and fetches only
    what is there. Creates no local branch, no worktree, and moves no
    card; what it found is what the next delivery reading reports.
    """
    _find(filename, None)
    stem = filename[:-3]
    remote = config.app_remote()
    if not remote:
        raise ValueError("this application has no remote, so there is "
                         "nothing to check — the board's own branches are "
                         "all there is")
    names = [config.TASK_BRANCH_PREFIX + stem,
             config.PHASE_BRANCH_PREFIX + stem]
    timeout = config.FETCH_TIMEOUT
    listed = _git_bounded(["ls-remote", "--heads", remote,
                           *[f"refs/heads/{n}" for n in names]], timeout)
    out = {"remote": remote, "asked": names, "found": {}, "fetched": [],
           "absent": [], "error": None, "at": time.time()}
    if listed is None or listed.returncode != 0:
        out["error"] = (f"{remote} could not be read"
                        + (f": {(listed.stderr or '').strip()[:160]}"
                           if listed is not None else " in time"))
        return out
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            out["found"][parts[1][len("refs/heads/"):]] = parts[0]
    out["absent"] = [n for n in names if n not in out["found"]]
    for name in out["found"]:
        done = _git_bounded(["fetch", "--no-tags", remote,
                             f"refs/heads/{name}:refs/remotes/{remote}/{name}"],
                            timeout)
        if done is not None and done.returncode == 0:
            out["fetched"].append(name)
        elif out["error"] is None:
            out["error"] = f"{name} could not be fetched"
    bump()
    state.broadcast({"type": "board"})
    return out


def _git_bounded(args: list[str], timeout: float):
    import subprocess
    try:
        return config.git_run(config.app_repo(), *args, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None


def launch_check(filename: str, stage: str | None = None,
                 intent: str = "work", *, refusals=None,
                 takeover: bool = False) -> dict:
    """An explicit, fresh launch check: the launch's own refusals, asked
    of the launch's own steps and nothing else — the stage and phase-card
    rule, one agent per card, the plan, the profile, the prompt template
    and, for work, the claim's ownership rule.

    `refusals` is `agents.launch_refusals`, handed in by the caller:
    `agents` sits to the right of this module in the dependency order, so
    it cannot be imported here, and the question must not be answered with
    a second, shorter list of its own — without it, nothing is checked and
    nothing is said to be clear. It may fetch, as `/api/plan` does,
    because a person asked; it claims nothing and starts nothing. A clean
    answer says the launch's own checks passed *now*; the launch asks
    again."""
    task = _find(filename, stage)
    stage = task["stage"]
    if refusals is None:
        raise ValueError("the launch check was not given the launch's own "
                         "refusals (agents.launch_refusals); nothing was "
                         "checked")
    blockers = [str(b) for b in refusals(filename, stage, intent, takeover)]
    return {"task": filename, "stage": stage, "intent": intent,
            "ok": not blockers, "blockers": blockers, "at": time.time(),
            "says": ("the launch's own preflight found nothing in the way "
                     "just now; it runs again when the launch is asked for"
                     if not blockers else
                     "the launch would be refused: " + blockers[0])}


def seams() -> dict:
    """What this module reads and what it never does — asserted by the
    tests, in the one place a reader looks for it."""
    return {
        "reads": ["taskfiles", "state.AGENTS", "state.COMPLETING",
                  "completion.recovery_public", "changes.pr_snapshot",
                  "changes.open_failure",
                  "completion.delivered_record", "plans.recorded",
                  "plans.declared", "gates.candidate", "gates.evaluate",
                  "acceptance.summary", "phases.public_state (passed in)",
                  "evidence.all_evidence"],
        "never": ["fetches on a read", "calls GitHub", "runs a check",
                  "launches an agent", "writes a record on a read",
                  "authorises a mutation"],
        "explicit": {"check-remote": check_remote.__doc__.split("\n")[0],
                     "launch-check": launch_check.__doc__.split("\n")[0]},
    }
