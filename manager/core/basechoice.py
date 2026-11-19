"""Choosing a card's work base: the application base, or committed work in
Review (task 150).

When the orchestrator picks up a card it decides — before a branch, a
worktree or a run exists — whether the work starts from the normal
application base or from a prerequisite's committed work already in
Review. Card A introduces an API and sits in Review; card B, which
declares `**Depends on:** A`, builds the UI on it: B starts from A's
pinned commit and B's pull request targets A's branch, so a reviewer sees
B's change and not a second copy of A's. Independent card C starts from
`main`.

## What counts as evidence

The rule is deliberately narrow, because a wrong base is expensive and
silent:

- **Only a declared dependency is a candidate.** The card's own
  `**Depends on:**` names it. A card merely sitting in Review, a similar
  title or files both cards touch are not evidence that one needs the
  other, and they never select a base — they are listed as considered and
  not selected, with why.
- **Only committed work is a base.** The candidate's work branch must
  exist here and hold commits the application base does not. What its
  worktree has uncommitted is invisible to this reading on purpose.
- **Done is the normal base.** A dependency already delivered is in the
  application base; nothing to stack on.
- **One prerequisite, or a chain.** Two unrelated Review prerequisites are
  not combined, cherry-picked or integrated on a branch bench invents: the
  decision is `blocked` with the remedy (sequence them, or decide the base
  with a header). Where one candidate's commit already contains the other
  — a stack of its own — the top of it is the base.
- **Constraints outrank it.** A card's own `**Base branch:**` governs
  (`explicit`), and a phase member's base is its phase branch
  (`integration`). A member that needs a Review card outside its phase is
  a conflict, recorded as `blocked`, never resolved by escaping the phase.

## What is written, and when

`assess` is a read: local refs only — no fetch, no write, no launch — so
the schedule and a card's reading may show it freely. `decide` writes the
decision onto the card as a `base` record (`decisions.note`) and is called
only by the scheduler's execute step, immediately before a launch it is
authorized to make. The record pins the branch, the exact commit, the
parent card, its head and PR, the normal base, the intended PR target, a
fingerprint of the inputs and the rationale. Writing it is idempotent: the
same inputs find the record already there, so a repeated tick, a restart
or a sync writes nothing twice.

`observe` is the reassessment: for a card whose decision stacked it on
another, a parent that moved, landed, went back or vanished writes one
`stale` record naming what changed and what to do. A card not yet started
is decided again before it starts (`plans._decided` refuses a start on a
stale decision); work already under way keeps its recorded plan — its
source is never changed under its worker — and the existing stack actions
(**Retarget onto**, **Update from**, **Finish: clean up**) are the
confirmed, separately authorized way on (`stackactions`).
"""

from __future__ import annotations

import hashlib
import json

import config
import decisions
import gitrepo
import plans
import taskfiles

V = 1


# ── local git, read only ───────────────────────────────────────────────

def _git(*args: str):
    return gitrepo.git(config.app_repo(), *args, timeout=30)


def _tip(branch: str) -> str:
    """The commit a branch names here, read exactly as the launch's own
    resolver reads it after its fetch (`plans.branch_tip`: the remote's
    copy as last fetched, else this checkout's). Never a fetch. One reading
    for the decision, its freshness and the preflight is what keeps a card
    from being refused by one and called current by the other."""
    return plans.branch_tip(branch)


def under_way(task: dict) -> bool:
    """Has this card's work started here (its task branch exists)? Then
    its recorded plan governs, and no decision is made or remade for it."""
    return not task.get("isPhase") and plans.work_exists(task["file"])


def _ahead(base: str, tip: str) -> int:
    """How many commits `tip` has that `base` does not."""
    if not tip:
        return 0
    args = ["rev-list", "--count", tip] + ([f"^{base}"] if base else [])
    out = _git(*args)
    try:
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except ValueError:
        return 0


def _contains(commit: str, tip: str) -> bool:
    return bool(commit and tip) and \
        _git("merge-base", "--is-ancestor", commit, tip).returncode == 0


# ── the board ──────────────────────────────────────────────────────────

def _tasks(board: dict) -> list[dict]:
    return [t for g in board.get("stages") or [] for t in g.get("tasks") or []]


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def _branch(task: dict) -> str:
    stem = task["file"][:-3]
    return (config.PHASE_BRANCH_PREFIX if task.get("isPhase")
            else config.TASK_BRANCH_PREFIX) + stem


def _brief_sha(task: dict) -> str:
    body = task.get("body")
    if body is None:
        try:
            body = (config.TASKS / task["stage"] / task["file"]).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            body = ""
    return taskfiles.brief_sha(taskfiles.card_parts(body)["brief"])[:16]


def _same_phase(a: dict, b: dict) -> bool:
    pa, pb = a.get("phase") or {}, b.get("phase") or {}
    return bool(pa and pb and pa.get("file") == pb.get("file"))


# ── assess: a read ─────────────────────────────────────────────────────

def assess(task: dict, board: dict) -> dict:
    """The decision as it would be made now, and everything it rests on.

    `decision` is one of `normal`, `stacked`, `explicit`, `integration`,
    `blocked`, or None when a dependency is not ready to be judged yet
    (still being worked: the scheduler's dependency guard holds the card).
    A read of local refs and parsed cards; nothing is fetched or written.
    """
    tasks = _tasks(board)
    by_number = {_number(t): t for t in tasks if _number(t)}
    app_base = config.APP_BASE
    base_tip = _tip(app_base)
    deps = [d for d in (task.get("dependsOn") or [])]
    out = {"v": V, "card": task["file"], "number": _number(task),
           "appBase": app_base, "decision": None, "code": None,
           "summary": "", "candidates": [], "considered": [],
           "evidence": [], "inputs": None}
    inputs: list = [task["file"], _brief_sha(task), task.get("baseBranch"),
                    (task.get("phase") or {}).get("file"), sorted(deps), app_base]
    candidates, problems, waiting = [], [], []
    for dep in deps:
        other = by_number.get(dep)
        if other is None:
            problems.append(("dependency-unknown",
                             f"it depends on card {dep}, which is not on this board"))
            continue
        name = f"#{dep}"
        if other["stage"] == "done":
            out["considered"].append({"card": dep, "selected": False,
                                      "why": f"{name} is done — its work is in "
                                             f"the normal base"})
            continue
        if other["stage"] != "review":
            waiting.append(dep)
            out["considered"].append({"card": dep, "selected": False,
                                      "why": f"{name} is in {other['stage']}/ — "
                                             f"it has no work to review yet"})
            continue
        if task.get("phase") and _same_phase(task, other):
            out["considered"].append({"card": dep, "selected": False,
                                      "why": f"{name} is in the same phase — "
                                             f"the phase branch integrates it"})
            continue
        branch = _branch(other)
        tip = _tip(branch)
        entry = {"card": dep, "file": other["file"], "branch": branch,
                 "head": tip, "pr": other.get("pr"), "selected": False}
        inputs.append([other["file"], other["stage"], tip])
        if not tip:
            entry["why"] = (f"{name} is in Review and {branch} is not here — "
                            f"its committed work cannot be read")
            problems.append(("prerequisite-unavailable", entry["why"]))
        elif _ahead(base_tip, tip) == 0:
            entry["why"] = (f"{branch} holds no commit {app_base} does not — "
                            f"there is no committed work of {name}'s to build on")
            problems.append(("no-committed-work", entry["why"]))
        elif (other.get("stack") or {}).get("parent") == task["file"]:
            entry["why"] = f"{name} is itself built on this card — a cycle"
            problems.append(("dependency-cycle", entry["why"]))
        else:
            entry["why"] = (f"declared dependency {name} is in Review with "
                            f"{_ahead(base_tip, tip)} committed change(s) "
                            f"{app_base} does not have")
            candidates.append(entry)
        out["considered"].append(entry)
    # Review cards this card does not name: never selected, and said so.
    for other in tasks:
        if other["stage"] == "review" and _number(other) not in deps \
                and other["file"] != task["file"] and not other.get("isPhase"):
            out["considered"].append({
                "card": _number(other), "selected": False,
                "why": f"#{_number(other)} is in Review but is not a declared "
                       f"dependency — a Review stage, a similar title or "
                       f"overlapping files are not evidence"})
    out["inputs"] = "i" + hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str)
                                   .encode("utf-8")).hexdigest()[:16]

    if task.get("baseBranch"):
        out.update(decision="explicit", code="explicit-header",
                   branch=task["baseBranch"],
                   summary=(f"the card's own **Base branch:** "
                            f"{task['baseBranch']} governs"))
        if candidates and not any(c["branch"] == task["baseBranch"] for c in candidates):
            out["summary"] += (" — note: declared Review prerequisite(s) "
                               + ", ".join(f"#{c['card']}" for c in candidates)
                               + " are not on it; the header was not overridden")
        return out
    if task.get("phase"):
        if candidates:
            names = ", ".join(f"#{c['card']}" for c in candidates)
            out.update(decision="blocked", code="phase-conflict",
                       summary=(f"this phase member depends on {names}, in Review "
                                f"outside its phase — a member starts from its "
                                f"phase branch and may not escape it. Add the "
                                f"prerequisite to the phase, wait for it to be "
                                f"delivered, or remove the dependency"))
        else:
            out.update(decision="integration", code="phase-integration",
                       summary="a phase member starts from its phase's branch")
        return out
    if problems:
        code, why = problems[0]
        out.update(decision="blocked", code=code,
                   summary=why + " — nothing is stacked on evidence bench cannot "
                         "read; fix it, or decide the base with a **Base "
                         "branch:** header")
        return out
    if waiting:
        out.update(decision=None, code="waiting-dependency",
                   summary="a dependency is still being worked")
        return out
    if not candidates:
        out.update(decision="normal", code="no-prerequisite",
                   summary=(f"no declared dependency has committed work in "
                            f"Review — starting from {app_base}"))
        return out
    top = [c for c in candidates
           if all(c is o or _contains(o["head"], c["head"]) for o in candidates)]
    if len(candidates) > 1 and not top:
        names = ", ".join(f"#{c['card']}" for c in candidates)
        out.update(decision="blocked", code="multiple-prerequisites",
                   summary=(f"it depends on {names}, unrelated work in Review — "
                            f"bench does not combine branches. Wait for all but "
                            f"one to be delivered, stack them on each other, or "
                            f"decide the base with a **Base branch:** header"))
        return out
    chosen = top[0] if top else candidates[0]
    chosen["selected"] = True
    others = [c for c in candidates if c is not chosen]
    out.update(decision="stacked", code="declared-prerequisite",
               branch=chosen["branch"], commit=chosen["head"],
               prBase=chosen["branch"], parent=chosen["card"],
               parentFile=chosen["file"], parentHead=chosen["head"],
               parentPr=chosen.get("pr") or "",
               evidence=[f"card:{chosen['card']}"],
               summary=(f"building on #{chosen['card']}'s committed work "
                        f"({chosen['branch']} at {chosen['head'][:9]}): "
                        f"{chosen['why']}. Its PR targets {chosen['branch']} "
                        f"until #{chosen['card']} lands; the normal base "
                        f"{app_base} is kept for then"
                        + (". It already contains " + ", ".join(
                            f"#{c['card']}" for c in others) if others else "")))
    return out


def _alternatives(found: dict) -> str:
    rest = [c for c in found["considered"] if not c.get("selected")]
    return "; ".join(c["why"] for c in rest)[:1800]


# ── decide: the one write before a launch ──────────────────────────────

def decide(task: dict, board: dict | None = None) -> dict:
    """Assess and, when the answer is a decision, put it on the card —
    once per set of inputs. Returns the assessment with `record` (the id
    on the card) and `written` (whether this call wrote it)."""
    board = taskfiles.collect() if board is None else board
    recorded = task.get("workBase") or {}
    if under_way(task):
        # Work already under way rides the plan it was started on: a retry,
        # a resume or a restart never remakes the decision under it — not
        # at a parent's new commit, and not as `normal` once the parent
        # landed (the Stack block and its confirmed actions are the way on).
        accepted = recorded.get("accepted") or {}
        return {"v": V, "card": task["file"], "decision": "under-way",
                "code": "work-under-way", "record": accepted.get("id"),
                "written": False, "appBase": config.APP_BASE,
                "summary": ("its work is under way on its recorded plan; the "
                            "work-base decision is not made again")}
    found = assess(task, board)
    if found["decision"] is None:
        return found
    latest = recorded.get("latest") or {}
    if latest.get("inputs") == found["inputs"] and \
            latest.get("decision") == found["decision"]:
        return {**found, "record": latest.get("id"), "written": False}
    fields = {"decision": found["decision"], "code": found["code"],
              "appBase": found["appBase"], "inputs": found["inputs"],
              "summary": found["summary"][:1900],
              "alternatives": _alternatives(found)}
    for key in ("branch", "commit", "prBase", "parent", "parentFile",
                "parentHead", "parentPr", "evidence"):
        if found.get(key):
            fields[key] = found[key]
    stage = taskfiles.find_stage_of(task["file"])
    if not stage:
        return {**found, "record": None, "written": False}
    try:
        done = decisions.note(task["file"], stage, "base", fields,
                              key=f"base-{found['inputs']}-{latest.get('id') or 'none'}")
    except (ValueError, OSError) as exc:
        return {**found, "record": None, "written": False, "error": str(exc)}
    written = not done.get("duplicate")
    _blocker(task["file"], stage, found, was_blocked=latest.get("decision") == "blocked")
    return {**found, "record": done.get("id"), "written": written}


def _blocker_id(filename: str) -> str:
    raw = "\x00".join(("basechoice", filename))
    return "b-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _blocker(filename: str, stage: str, found: dict, *, was_blocked: bool) -> None:
    """A blocked decision is also an open blocker on the card — the
    conflict and its remedy where the owner reads what waits on them —
    and the next decision that is not blocked clears it. Idempotent by the
    decision's inputs, so a repeated pass writes nothing twice."""
    mine = _blocker_id(filename)
    if found["decision"] == "blocked":
        fields = {"blocker": mine, "state": "open", "operation": "work-base",
                  "text": f"work base {found['code']}: {found['summary']}"[:1800]}
        key = f"base-block-{found['inputs']}"
    elif was_blocked:
        fields = {"blocker": mine, "state": "cleared", "operation": "work-base",
                  "text": f"cleared: {found['summary']}"[:1800]}
        key = f"base-clear-{found['inputs']}"
    else:
        return
    try:
        decisions.note(filename, stage, "blocker", fields, key=key)
    except (ValueError, OSError):
        pass


def recheck(filename: str, *, fetch: bool = True) -> str | None:
    """Immediately before a launch: does the card's accepted decision still
    describe the code a new start would be built on? A reason when not.

    It asks the launch's own resolver (`plans.resolve_branch`, fetching
    as the preflight will), so the answer here is the answer the launch
    acts on. When it is no, the decision is recorded `stale` on the card
    right here — and the fetch has moved the reading `assess` takes — so
    the next pass decides again on what is there, rather than finding
    nothing changed and launching into the same refusal. Work already
    under way is never held to it: it keeps its recorded plan."""
    stage = taskfiles.find_stage_of(filename)
    try:
        task = taskfiles.read_task(config.TASKS / stage / filename, stage) \
            if stage else None
    except OSError:
        task = None
    if task is None:
        # nothing to hold the launch to here; the launch's own preflight
        # reads the card and refuses what it cannot read
        return None
    if under_way(task):
        return None
    decided, withdrawn = plans.decision(task)
    if withdrawn:
        return withdrawn
    if decided and decided.get("decision") == "stacked":
        resolved = plans.resolve_branch(decided["branch"], fetch=fetch)
        now = resolved.get("commit") or ""
        if now != decided["commit"]:
            why = (f"{decided['branch']} moved from {decided['commit'][:9]} to "
                   f"{(now or 'nothing')[:9]} after the decision was recorded")
            _stale(task, stage, decided, "parent-moved" if now else "parent-gone",
                   why, now)
            return why
    return None


def _stale(task: dict, stage: str, accepted: dict, code: str, why: str,
           head: str) -> dict | None:
    key = "s" + hashlib.sha256(f"{accepted.get('id')}|{code}|{head}".encode()) \
        .hexdigest()[:16]
    try:
        return decisions.note(task["file"], stage, "base", {
            "decision": "stale", "code": code,
            "branch": accepted["branch"], "commit": accepted["commit"],
            "parent": accepted.get("parent") or "",
            "parentHead": head or accepted["commit"],
            "inputs": key,
            "summary": (f"{why}; not started yet: the orchestrator decides its "
                        f"base again before it starts")[:1900]},
            key=f"base-stale-{key}")
    except (ValueError, OSError):
        return None


# ── observe: the prerequisite evolves ──────────────────────────────────

def freshness(task: dict, board: dict) -> dict | None:
    """For a card whose accepted decision stacks it on another: whether
    that still holds, and what to do when not. None otherwise."""
    found = task.get("workBase") or {}
    accepted = found.get("accepted") or {}
    if accepted.get("decision") != "stacked" or task.get("baseBranch") \
            or task.get("phase"):
        return None
    tasks = {t["file"]: t for t in _tasks(board)}
    parent = tasks.get(accepted.get("parentFile") or "")
    tip = _tip(accepted["branch"])
    started = under_way(task)
    code, why = None, None
    if parent is None:
        code, why = "parent-abandoned", (f"#{accepted.get('parent')} is no longer "
                                         f"on the board (archived or removed)")
    elif parent["stage"] == "done":
        code, why = "parent-landed", (f"#{accepted.get('parent')} was delivered")
    elif parent["stage"] in ("backlog", "to-do", "in-progress"):
        code, why = "parent-returned", (f"#{accepted.get('parent')} went back to "
                                        f"{parent['stage']}/ — its review did not "
                                        f"accept it")
    elif not tip:
        code, why = "parent-gone", f"{accepted['branch']} is not here any more"
    elif tip != accepted.get("commit"):
        code, why = "parent-moved", (f"{accepted['branch']} moved from "
                                     f"{accepted['commit'][:9]} to {tip[:9]}")
    if code is None:
        return {"state": "current", "started": started}
    if not started:
        remedy = ("not started yet: the orchestrator decides its base again "
                  "before it starts")
    elif code == "parent-landed":
        remedy = ("its work is under way: once the host shows the parent merged, "
                  "Retarget onto the normal base from the card's Stack block — "
                  "bench changes the PR target only through that confirmed action")
    elif code == "parent-moved":
        remedy = ("its work is under way and is not changed under its worker: "
                  "Update from the parent (an additive merge) is the confirmed "
                  "way to bring the new commits in")
    else:
        remedy = ("its work is under way and is not changed: decide its base "
                  "again with ⌖ replan, or finish it as it is")
    return {"state": "stale", "code": code, "why": why, "remedy": remedy,
            "started": started, "parentHead": tip,
            "key": "s" + hashlib.sha256(f"{accepted.get('id')}|{code}|{tip}".encode())
            .hexdigest()[:16]}


def observe(board: dict | None = None) -> list[dict]:
    """Write one `stale` record per change a stacked decision's parent
    went through. Touches no branch, worktree, run or PR."""
    board = taskfiles.collect() if board is None else board
    out = []
    for task in _tasks(board):
        if task["stage"] not in ("to-do", "in-progress", "review"):
            continue
        fresh = freshness(task, board)
        if not fresh or fresh["state"] != "stale":
            continue
        latest = (task.get("workBase") or {}).get("latest") or {}
        if latest.get("decision") == "stale" and latest.get("inputs") == fresh["key"]:
            continue
        accepted = task["workBase"]["accepted"]
        try:
            done = decisions.note(task["file"], task["stage"], "base", {
                "decision": "stale", "code": fresh["code"],
                "branch": accepted["branch"], "commit": accepted["commit"],
                "parent": accepted.get("parent") or "",
                "parentHead": fresh["parentHead"] or accepted["commit"],
                "inputs": fresh["key"],
                "summary": f"{fresh['why']}; {fresh['remedy']}"[:1900]},
                key=f"base-stale-{fresh['key']}")
        except (ValueError, OSError):
            continue
        if not done.get("duplicate"):
            out.append({"card": task["file"], "outcome": "base-stale",
                        "code": fresh["code"]})
    return out


def reading(task: dict, board: dict) -> dict:
    """What the Orchestrator view shows for one card: the recorded
    decision, its plain-language headline and its freshness. A read."""
    found = task.get("workBase")
    return {"headline": plans.headline(found, task),
            "accepted": (found or {}).get("accepted"),
            "latest": (found or {}).get("latest"),
            "freshness": freshness(task, board)}
