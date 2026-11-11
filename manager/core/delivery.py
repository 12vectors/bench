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
| completion in flight; merged but not published | `state.COMPLETING`, `github.recovery()` |
| recorded plan; stacked target | `plans.recorded()`, `plans.declared()` |
| which commit the merge would take, and its state | `gates.candidate()`, `gates.head_for()` |
| requirements at that commit | `gates.evaluate()` |
| the PR as last observed | `github.pr_snapshot()` (memory, or the persisted snapshot) |
| review findings | `acceptance.summary()` |
| a phase run | the phase runner's last pass, handed in by the caller |

**Mutations never read this result.** `agents.start_agent`,
`verify.request`, `gates.override` and `github._complete` keep their own
checks and run them again at the moment they act. A cached reading here
is presentation, never authorisation.

## Reading changes nothing

It fetches nothing, asks GitHub nothing, runs no check, launches no
agent and writes no record. The only processes it can cause are the
read-only local git queries the authorities above already make (the
branch walk, a containment test, a source digest). Two explicit actions
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
import config
import gates
import github
import plans
import profiles
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
    (14, "review, a required check is needed", ("checks-needed",)),
    (15, "review, the required review is not met", ("review-needed",)),
    (16, "review, sign-off pending", ("signoff-needed",)),
    (18, "review, every requirement met or overridden",
     ("ready-to-merge", "ready-overridden")),
    (19, "review, an ungated board", ("review",)),
    (20, "done", ("delivered",)),
)

# Words no row produces yet, and the card that brings them. Sign-off is
# an opt-in gate field that does not exist until it is built (design §8.3).
NOT_YET = {"signoff-needed": "the sign-off gate field (design #94 §8.3) "
                             "is not implemented yet"}

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
    "cancel-check": {"handler": "/api/verify/cancel", "kind": "api",
                     "arm": True},
    "check-remote": {"handler": "/api/card/delivery/check-remote",
                     "kind": "api", "arm": False},
    "launch-check": {"handler": "/api/card/delivery/launch-check",
                     "kind": "api", "arm": False},
    "run-required-checks": {"handler": None, "kind": "api", "arm": True,
                            "later": ("Run required checks arrives with a "
                                      "later #94 card (design §10.2). Until "
                                      "then, ask for each check with ↻ in "
                                      "the ◫ evidence sheet")},
    "confirm-criteria": {"handler": None, "kind": "view", "arm": False,
                         "later": ("sign-off (design §8.3) is not "
                                   "implemented yet")},
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


def _remote_tips() -> dict[str, str]:
    """What this checkout's remote-tracking refs already say about task
    and phase branches — the last fetch, not a new one. One local read."""
    remote = config.app_remote()
    if not remote:
        return {}
    found: dict[str, str] = {}
    result = config._app_git("for-each-ref",
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
    policy = profiles.gate_policy()
    return {
        "tips": gates._tips(),
        "remoteTips": _remote_tips(),
        "policy": policy,
        "records": verify.all_evidence(),
        "agents": agents,
        "completing": completing,
        "phases": {k: dict(v) for k, v in (phase_state or {}).items()},
        "me": taskfiles.actor_name() if config.COMMIT_MOVES else "",
        "remote": config.app_remote(),
        "gh": config.gh_available(),
        "onOrigin": config.app_merge_on_origin(),
        "now": time.time(),
    }


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
    `github.completion_facts` reads — minus anything that would reach a
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
        refusal = ("gh is not installed, and this board merges the "
                   "application on origin (BOARD_APP_MERGE)")
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
    name = row.get("name")
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
            "reusedFrom": None,
            "otherCommits": row.get("otherCommits", 0)}


def _optional(policy_doc: dict, required: set[str], mine: list[dict]) -> int:
    """Checks this card may ask for, or has records of, that no gate
    requires — counted apart, never in a headline (design §7)."""
    offered = set(verify._offered(policy_doc)) if policy_doc else set()
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
    return commit == head or gates._contained(commit, head)


def compute(task: dict, ctx: dict | None = None, *, full: bool = False) -> dict:
    """The delivery status of one card. `ctx` is the board-wide read,
    hoisted; `full` adds the parts only the card's own reading carries."""
    ctx = ctx or _context()
    filename, stage = task["file"], task["stage"]
    plan = plans.recorded(filename)
    pr = github.pr_snapshot(filename)
    recovery = github.recovery_public(filename)
    scope = gates.scope_for(task)
    member = bool(task.get("phase")) and not task.get("isPhase")
    if member:
        scope = "member"
    mine = [r for r in ctx["records"] if r.get("task") == filename]
    candidate = gates.candidate(
        filename, scope, task=task, tips=ctx["tips"], records=mine, pr=pr,
        completion=recovery, remote_tips=ctx["remoteTips"], plan=plan,
        delivery_record=github.delivered_record(filename))
    alive, failure = _runs(task, ctx)
    target = _target(task, plan)
    completion = _completion(task, plan, target, pr, ctx)
    holder = task.get("assignee") or ""
    others_hold = bool(ctx["me"]) and bool(holder) and holder != ctx["me"]
    on_board = ("on this board"
                if (others_hold or ctx["remote"]) else "")
    findings = (acceptance.summary(task.get("body") or "", filename) or {})
    open_findings = int(findings.get("openFindings") or 0)

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

    status, sentence, primary, others = _decide(
        task=task, ctx=ctx, candidate=candidate, decision=decision,
        requirements=requirements, alive=alive, failure=failure,
        recovery=recovery, pr=pr, completion=completion,
        open_findings=open_findings, on_board=on_board,
        others_hold=others_hold, holder=holder, member=member,
        merged_elsewhere=merged_elsewhere, gated=gated)

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
        "source": {
            "pr": ({"observed": observed, "from": pr.get("from"),
                    "age": round(ctx["now"] - observed, 1)
                    if observed else None} if pr else None),
            "local": True,
        },
        "read": {"at": ctx["now"], "local": True},
    }
    if full:
        out["candidate"].update(why=candidate.get("why"),
                                traces=candidate.get("traces"))
        out["decision"] = ({"decision": decision.get("decision"),
                            "scope": decision.get("scope", scope),
                            "summary": decision.get("summary"),
                            "gated": decision.get("gated"),
                            "head": decision.get("head")}
                           if decision else None)
        out["completion"] = completion
        out["pr"] = pr
        out["recovery"] = recovery
    return out


def _decide(*, task, ctx, candidate, decision, requirements, alive,
            failure, recovery, pr, completion, open_findings, on_board,
            others_hold, holder, member, merged_elsewhere, gated):
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
        return ("not-published",
                f"Merged locally into {recovery.get('target') or 'the base'} "
                f"at {_short(recovery.get('commit'))}, but not on the remote: "
                f"{said}.",
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
        launch = action("launch-check", "Check whether it would start")
        problem = _current_problem(requirements, candidate, open_findings,
                                   decision)
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
        back = action("move", "Move back to in-progress", to="in-progress")
        return ("cannot-assess",
                f"Cannot assess: {no_commit[0].lower()}{no_commit[1:]}",
                check or back, [back] if check else [])

    act_pr = action("act-pr", "↻ act on PR") if has_pr and not is_phase \
        and not (pr or {}).get("merged") else None
    review_pr = action("review-pr", "◔ review PR") if has_pr else None

    # 12. a current-commit problem
    problem = _current_problem(requirements, candidate, open_findings,
                               decision)
    if problem:
        word, sentence = problem
        fix = act_pr or action("move", "Move back to in-progress",
                               to="in-progress")
        rest = [action("read-failure", "Read the failure")]
        if review_pr:
            rest.append(review_pr)
        return (word, sentence, fix, rest)

    checks = [r for r in requirements if r["kind"] == "check"]
    # 13. running
    running = [r for r in checks if r["class"] == "running"]
    if running:
        rest = [action("cancel-check", "Cancel", evidence=r["evidence"])
                for r in running if r.get("evidence")]
        return ("checks-running",
                f"{_names(running)} {'is' if len(running) == 1 else 'are'} "
                f"running on {here}.", None, rest)

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
        return ("checks-needed", sentence,
                action("run-required-checks", "Run required checks"), rest)

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


def _names(rows: list[dict]) -> str:
    names = [str(r.get("name")) for r in rows]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 \
        else (names[0] if names else "nothing")


def _needed_sentence(row: dict, here: str) -> str:
    reason, name = row["reason"], row["name"]
    if reason == "cancelled":
        return f"{name} was cancelled before a result on {here}."
    if reason == "stale":
        return (f"{name} passed on {here}, and "
                f"{(row.get('why') or 'what it depends on changed').split(': ', 1)[-1]}")
    if reason == "source-dirty":
        return f"{name} ran with uncommitted files. Commit, then run checks."
    return f"{name} has not run on {here}."


def _current_problem(requirements: list[dict], candidate: dict,
                     open_findings: int, decision: dict | None):
    """A genuine problem on the *current* commit: a check that failed or
    could not run there, a change request at it, or open findings. A
    failure at another commit is history and never this (design §6)."""
    if candidate["state"] != "present":
        return None
    failed = [r for r in requirements if r["kind"] == "check"
              and r["reason"] == "failed"]
    unable = [r for r in requirements if r["kind"] == "check"
              and r["reason"] in ("prepare-failed", "denied", "error")]
    changes = [r for r in requirements if r["kind"] == "review"
               and r["reason"] in ("verdict-changes", "changes-requested")]
    here = _short(candidate.get("commit"))
    review_bit = ""
    if changes:
        review_bit = ("changes were requested by the Bench review of "
                      f"{here}" if changes[0]["reason"] == "verdict-changes"
                      else "a GitHub review asks for changes")
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

COMPACT = ("status", "primary", "candidate", "counts")


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
        snap = github.pr_snapshot(task["file"]) or {}
        snaps.append((task["file"], snap.get("head"), snap.get("merged"),
                      snap.get("prState"), snap.get("observed")))
    parts = {
        "epochs": [_EPOCH, gates._EPOCH, verify._EPOCH],
        "policy": ctx["policy"].get("revision"),
        "tips": sorted(ctx["tips"].items()),
        "remoteTips": sorted(ctx["remoteTips"].items()),
        "cards": [(t["file"], t["stage"], t.get("assignee"),
                   hashlib.sha256((t.get("body") or "").encode()).hexdigest())
                  for t in tasks],
        "agents": sorted((a.get("id"), a.get("task"), a.get("status"),
                          bool(a.get("failure")))
                         for a in ctx["agents"]),
        "completing": sorted(ctx["completing"]),
        "recoveries": github.recoveries(),
        "phases": sorted((k, v.get("running"), v.get("halted"))
                         for k, v in ctx["phases"].items()),
        "snapshots": snaps,
        # The recorded plan (a ⌖ replan rewrites it) and the delivered
        # record (a finished merge & clean up writes it) are both read by
        # every card's reading, so both are part of its identity.
        "plans": [(t["file"], _file_stamp(plans.plan_path(t["file"])))
                  for t in tasks],
        "delivered": [(t["file"], github.delivered_record(t["file"]))
                      for t in tasks],
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
    out["primary"] = ({k: found["primary"][k] for k in
                       ("action", "label", "who", "enabled", "why", "arm")}
                      if found["primary"] else None)
    out["pr"] = found["source"]["pr"]
    return out


def board(collected: dict, phase_state: dict | None = None) -> dict:
    """The compact reading for every card, for `/api/state`. Memoised by a
    fingerprint of every input and a time bucket — the cache is never
    authority, and a mutation never reads it."""
    global _BOARD
    tasks = [task for stage in collected.get("stages", [])
             for task in stage["tasks"]]
    ctx = _context(phase_state)
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
                "primary": None, "candidate": {"state": "unavailable"},
                "counts": {"required": 0, "met": 0}, "pr": None}
    with _LOCK:
        _BOARD = (key, found)
    return found


def for_card(filename: str, stage: str | None = None,
             phase_state: dict | None = None) -> dict:
    """The full reading for one card, always recomputed."""
    task = _find(filename, stage)
    return compute(task, _context(phase_state), full=True)


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
        return subprocess.run(["git", "-C", str(config.app_repo()), *args],
                              capture_output=True, text=True,
                              timeout=timeout)
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
                  "github.recovery_public", "github.pr_snapshot",
                  "github.delivered_record", "plans.recorded",
                  "plans.declared", "gates.candidate", "gates.evaluate",
                  "acceptance.summary", "phases.public_state (passed in)",
                  "verify.all_evidence"],
        "never": ["fetches on a read", "calls GitHub", "runs a check",
                  "launches an agent", "writes a record on a read",
                  "authorises a mutation"],
        "explicit": {"check-remote": check_remote.__doc__.split("\n")[0],
                     "launch-check": launch_check.__doc__.split("\n")[0]},
    }
