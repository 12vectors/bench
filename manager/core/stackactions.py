"""Explicitly confirmed stack maintenance (task 119, WP10b of the
maintainability plan, §3.4).

The stack reading (`stacks`) says what state each edge is in and which
action a person might take. This module takes it — and only when a person
has seen the exact identities it would act on and confirmed them. Three
actions, no more:

- **Retarget onto <target>** — the child's change request targets the
  branch its parent landed in, instead of the parent's branch. Where the
  provider in force can change the base on the host *and* that capability
  reads `available` at the moment of the click (`githost.retarget_state`,
  a local `--help` read), bench makes the edit and reads the change back;
  the recorded plan and the card's `**PR base:**` follow what was read
  back, never what was hoped. `unknown` or `unavailable` makes **no**
  provider mutation call: the sheet names the exact manual step, and
  bench's plan changes only once the host is observed targeting the new
  branch (the same action then *adopts* it). The host edit is not
  conditional on anything (STACKING.md, C8), so it is not a
  compare-and-set: the head and base are read immediately before and
  after, pre-existing drift refuses, and a change that cannot be
  confirmed is reported as exactly that — nothing is undone on the host,
  and nothing is recorded.
- **Update from <parent branch>** — an additive merge of the parent's tip
  into the child's own worktree. A conflict is aborted and reported, with
  the files named; nothing is reset, rebased or force-pushed. Published
  with a plain push only when the parent's tip is already on the remote,
  so the child never publishes its parent's work for it.
- **Finish: clean up** — for a child whose head the application base on
  the remote already holds (read off the remote itself, not the last
  fetch): the workspace is cleared under merge & clean up's own rules and
  the card moves to `done/`. Nothing is merged.

Every action is armed in the page and bound to the identities its sheet
showed (`preview`): the child, its head, its parent, the parent's tip, the
current and the new target and the target's tip, the change request.
`run` reads them all again and refuses — naming what moved — when any
differs, and refuses a running agent on the card, a completion in flight,
a worktree that is dirty, on another branch or not this repository's, a
phase member (whose relation to its phase is the phase runner's) and a
phase card. Nothing here runs by itself: no beat, no move and no read
calls `run`.

A retarget changes what a review was about even when the head did not
move. It is recorded on the plan (`plans.retarget`), and `gates` stops
counting a Bench verdict or a formal approval given before it; check
evidence keeps its own identity rules untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import changes
import completion
import config
import gates
import githost
import gitrepo
import plans
import stacks
import state
import taskfiles

ACTIONS = ("retarget", "update", "cleanup")

# What each action does, in the sheet's own words, so the confirmation
# says what it costs before anything is asked.
EFFECTS = {
    "retarget": ("Changes what this card's change request targets. Its head "
                 "does not move, but its diff and integration do: a Bench "
                 "verdict or an approval given before is not carried over, "
                 "and a review against the new target is needed. Check "
                 "evidence keeps its own identity rules. Nothing is merged, "
                 "rebased or pushed."),
    "update": ("Merges the parent's tip into this card's branch in its own "
               "worktree — additively, a new merge commit. A conflict is "
               "aborted and reported; nothing is reset, rebased or "
               "force-pushed. The new head needs its checks and review again."),
    "cleanup": ("Removes this card's worktree and branches under merge & "
                "clean up's rules (uncommitted work is kept) and moves the "
                "card to done/. Nothing is merged: the application base on "
                "the remote already holds this work."),
}


class Moved(ValueError):
    """An identity the person confirmed is no longer what it was."""


# ── reading the situation, once per question ──────────────────────────


def _board(filename: str) -> tuple[dict, dict, dict]:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    collected = taskfiles.collect()
    by_file = {t["file"]: t for g in collected["stages"] for t in g["tasks"]}
    task = by_file.get(filename)
    if task is None:
        raise ValueError(f"{filename} is not on the board — refresh it")
    return task, by_file, stacks.context()


def _running(filename: str) -> bool:
    with state.LOCK:
        return any(r.get("task") == filename and r.get("status") == "running"
                   for r in state.AGENTS.values())


def _worktree_problem(task: dict, branch: str) -> str | None:
    """Why the card's worktree cannot be acted on, or None. No worktree at
    all is fine: nothing is in it to lose."""
    path = config.WORKTREES / task["file"][:-3]
    mismatch = gitrepo.worktree_mismatch(path)
    if mismatch:
        return mismatch
    if not path.exists():
        return None
    current = gitrepo.git(path, "branch", "--show-current").stdout.strip()
    if current != branch:
        return (f"the worktree at {path} is on '{current or 'a detached head'}'"
                f", not {branch} — nothing was changed; settle it by hand")
    dirty = gitrepo.dirty(path)
    if dirty:
        return (f"the worktree at {path} has {len(dirty)} uncommitted "
                f"change{'' if len(dirty) == 1 else 's'} — commit or put them "
                f"aside first; bench acts on committed work only")
    return None


def _change(task: dict) -> tuple[str | None, str | None]:
    url = task.get("pr") or None
    return (githost.change_ref(url) if url else None), url


def preview(filename: str, action: str) -> dict:
    """The confirmation sheet for one action: the identities it is bound
    to, what it would do and how, what stops it, and — for a retarget the
    host may not be asked to make — the exact manual step. Writes nothing
    and mutates nothing; a retarget's sheet reads the CLI's own help text
    (a local process) to say whether bench could make the edit."""
    if action not in ACTIONS:
        raise ValueError(f"'{action}' is not a stack action "
                         f"({', '.join(ACTIONS)})")
    task, by_file, ctx = _board(filename)
    here = task.get("stack") or {}
    out = {"action": action, "file": filename, "effects": EFFECTS[action],
           "blockers": [], "notes": [], "mode": None, "manual": None,
           "identities": {}, "label": ""}
    blockers = out["blockers"]
    if task.get("isPhase"):
        blockers.append(f"{filename} is a phase card — stack actions maintain "
                        f"ordinary stacked cards, and a phase lands as a whole")
    if here.get("mapping") == "integration":
        blockers.append(f"{filename} is a phase member: the phase runner merges "
                        f"it into its phase branch, and its target is the "
                        f"phase's — stack actions do not touch it")
    elif here.get("mapping") != "card" or here.get("parent") not in by_file:
        blockers.append(f"{filename} is not stacked on a card on this board "
                        f"({here.get('why') or here.get('mapping') or 'no stack'})")
    if blockers:
        return out
    parent = by_file[here["parent"]]
    reading = stacks.reading(task, by_file, ctx) or {}
    edge = (reading.get("edge") or {}).get("state")
    plan = plans.recorded(filename)
    cbranch = stacks.work_branch(task)
    pbranch = stacks.work_branch(parent)
    head = ctx["heads"].get(cbranch) or ""
    ptip = stacks.tip(ctx, pbranch)
    target = stacks.review_target(task, plan)
    number, url = _change(task)
    ids = {"child": filename, "childBranch": cbranch, "childHead": head,
           "parent": parent["file"], "parentBranch": pbranch,
           "parentTip": ptip, "target": target, "change": number}
    out["identities"] = ids
    out["edge"] = edge

    if task.get("stage") == "done":
        blockers.append(f"{filename} is already in done/")
    if not head:
        blockers.append(f"{cbranch} is not a branch in this checkout — there "
                        f"is no work here to act on")
    if _running(filename):
        blockers.append(f"an agent is working on {filename} — hold it first "
                        f"(‖ hold); nothing is stopped for you")
    if filename in state.completing_public():
        blockers.append(f"{filename} is being merged and cleaned up already")
    wt = _worktree_problem(task, cbranch)
    if wt:
        blockers.append(wt)

    if action == "retarget":
        _retarget_preview(out, task, parent, ctx, edge, target, number, url)
    elif action == "update":
        _update_preview(out, parent, ctx, edge, head, ptip, pbranch)
    else:
        _cleanup_preview(out, reading, ctx, head)
    return out


def _retarget_preview(out: dict, task: dict, parent: dict, ctx: dict,
                      edge: str | None, target: str, number: str | None,
                      url: str | None) -> None:
    ids, blockers = out["identities"], out["blockers"]
    landed = stacks.delivered(parent, ctx)
    onto = stacks.review_target(parent, plans.recorded(parent["file"])) \
        if landed else ""
    ids["newTarget"] = onto or None
    ids["newTargetTip"] = stacks.tip(ctx, onto) if onto else ""
    out["label"] = f"Retarget onto {onto}" if onto else "Retarget"
    if not landed:
        blockers.append(f"#{parent.get('number') or parent['file']} has not "
                        f"landed, so there is nowhere else for this card to "
                        f"target yet")
        return
    if onto == target:
        blockers.append(f"this card already targets {onto}")
        return
    if landed == "differently":
        held = stacks.contains(ctx, ids["newTargetTip"], ids["childHead"])
        if not held:
            blockers.append(
                f"the parent was merged into {onto} without its own commits "
                f"(a squash or rebase merge), and this branch does not "
                f"contain {onto} yet: bring {onto} into it first (an "
                f"additive merge), then retarget — bench will not rebase")
    if not ids["newTargetTip"] and not plans.remote_branch_commit(
            config.app_remote() or "", onto):
        blockers.append(f"{onto} is not a branch in this checkout or on the "
                        f"remote")
    if number is None:
        out["mode"] = "plan-only"
        out["notes"].append(
            "this card has no change request yet, so only bench's plan and "
            "the card's **PR base:** change; one opened later targets "
            f"{onto}")
        return
    snap = changes.pr_snapshot(task["file"]) or {}
    ids["hostBase"] = snap.get("base") or None
    if snap.get("base") == onto:
        out["mode"] = "adopt"
        out["notes"].append(
            f"the host was last seen targeting {onto} already, so bench reads "
            f"the change once more and records it — no edit is made")
        return
    found = githost.retarget_state()
    out["capability"] = found
    if found.get("state") == "available":
        out["mode"] = "host"
        out["notes"].append(
            f"bench will change {githost.words()['change']} #{number}'s base "
            f"from {target} to {onto} on the host, then read it back. The "
            f"edit is not conditional on the head: bench reads the head and "
            f"base immediately before and after, and only what it reads back "
            f"is recorded")
        return
    out["mode"] = "manual"
    out["manual"] = githost.manual_retarget(number, onto, url)
    said = found.get("state") or "unknown"
    reason = found.get("reason") or "no reason given"
    out["notes"].append(
        f"bench will not make this edit here — the provider's 'retarget' is "
        f"{said} ({reason}). Nothing is changed by bench; once the host is "
        f"seen targeting {onto}, this action records it")


def _update_preview(out: dict, parent: dict, ctx: dict, edge: str | None,
                    head: str, ptip: str, pbranch: str) -> None:
    blockers = out["blockers"]
    out["label"] = f"Update from {pbranch}"
    if _running(parent["file"]):
        blockers.append(f"an agent is working on {parent['file']}, so its tip "
                        f"is still moving — wait for it, or hold it")
    if edge == "parent-rewritten":
        blockers.append(f"{pbranch} no longer contains the commit this work "
                        f"was cut from — settle it by hand; bench will not "
                        f"rebase")
    if not ptip:
        blockers.append(f"{pbranch} is not on this board or its last fetch")
    elif head and stacks.contains(ctx, ptip, head):
        blockers.append(f"this branch already contains {pbranch} at "
                        f"{ptip[:12]}")
    remote = config.app_remote()
    out["mode"] = "merge"
    if remote:
        out["notes"].append(
            f"after the merge, the branch is pushed to {remote} with a plain "
            f"push — only if {pbranch} at {ptip[:12] or '?'} is on {remote} "
            f"already, so the parent's work is never published by its child")


def _cleanup_preview(out: dict, reading: dict, ctx: dict, head: str) -> None:
    landing = ((reading.get("targets") or {}).get("landingTarget") or {}) \
        .get("branch") or config.APP_BASE
    out["identities"]["landing"] = landing
    out["label"] = "Finish: clean up"
    out["mode"] = "cleanup"
    remote = config.app_remote()
    if not remote:
        out["blockers"].append(
            "this application has no remote, so delivery into "
            f"{landing} cannot be proven — nothing is cleaned up")
        return
    held = ctx["tracked"].get(landing) or ""
    if head and held and not stacks.contains(ctx, head, held):
        out["notes"].append(
            f"the last fetch of {remote}/{landing} does not hold {head[:12]}; "
            f"the remote itself is asked when you confirm")
    out["notes"].append(f"{remote}/{landing} is read from the remote itself "
                        f"before anything is removed")


# ── confirming ─────────────────────────────────────────────────────────


def digest(identities: dict) -> str:
    """A short fingerprint of what a sheet showed, for the page and the
    ticker; the binding itself is the identities, compared one by one."""
    text = json.dumps(identities, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _bound(fresh: dict, expected: dict) -> None:
    """Refuse when anything the person confirmed has moved since."""
    if not isinstance(expected, dict) or not expected:
        raise ValueError("a stack action needs the identities it was shown "
                         "(`expected`) — nothing was changed")
    moved = [f"{key} was {str(expected.get(key))[:12] or 'none'}, now "
             f"{str(fresh.get(key))[:12] or 'none'}"
             for key in sorted(set(fresh) | set(expected))
             if (fresh.get(key) or None) != (expected.get(key) or None)]
    if moved:
        raise Moved("what you confirmed has moved since you were shown it ("
                    + "; ".join(moved) + ") — nothing was changed; look again")


def run(filename: str, action: str, expected: dict) -> dict:
    """Do one confirmed action, after reading everything again."""
    fresh = preview(filename, action)
    if fresh["blockers"]:
        raise ValueError(fresh["blockers"][0])
    _bound(fresh["identities"], expected)
    if not state.claim_completing(filename, f"{fresh['label'].lower()}…"):
        raise ValueError(f"{filename} is being merged and cleaned up already")
    try:
        if action == "retarget":
            return _retarget(fresh)
        if action == "update":
            return _update(fresh)
        return _cleanup(fresh)
    finally:
        state.release_completing(filename)


def _say(filename: str, summary: str) -> None:
    state.record_board_event({"kind": "agent", "actor": "you",
                              "file": filename, "summary": summary})
    state.broadcast({"type": "board"})


def _record_retarget(fresh: dict, how: str, notes: list[str]) -> dict:
    ids = fresh["identities"]
    filename = fresh["file"]
    stage = taskfiles.find_stage_of(filename) or ""
    plans.retarget(filename, ids["newTarget"], old=ids["target"],
                   head=ids["childHead"], how=how)
    header = taskfiles.set_pr_base(filename, stage, ids["newTarget"])
    gates.invalidate()
    summary = (f"{filename} retargeted from {ids['target']} onto "
               f"{ids['newTarget']} ({how}) at {ids['childHead'][:12]} — a "
               f"review against {ids['newTarget']} is needed; earlier "
               f"verdicts and approvals are history")
    _say(filename, summary)
    return {"ok": True, "action": "retarget", "outcome": how,
            "sentence": summary, "notes": notes,
            "committed": header.get("committed", False),
            "identities": ids}


def _retarget(fresh: dict) -> dict:
    ids = fresh["identities"]
    mode = fresh["mode"]
    filename, number, onto = fresh["file"], ids["change"], ids["newTarget"]
    if mode == "plan-only":
        return _record_retarget(fresh, "plan-only", fresh["notes"])
    if mode == "manual":
        # Zero provider mutation calls: the step is the person's.
        raise ValueError(
            f"bench will not change the base on the host here — "
            f"{fresh['notes'][-1]}. Nothing was changed. The manual step: "
            f"{fresh['manual']}")
    # Read the change itself, immediately before: drift that was already
    # there refuses, so bench never "corrects" somebody else's edit.
    before = githost.change_identity(number)
    if not before.get("ok"):
        why = before.get("detail") or before.get("error")
        raise ValueError(f"the change request #{number} could not be read "
                         f"before retargeting ({why}) — nothing was changed")
    if before.get("state") != "open":
        raise ValueError(f"the change request #{number} is "
                         f"{before.get('state')}, so there is nothing to "
                         f"retarget — nothing was changed")
    if before.get("headCommit") and before["headCommit"] != ids["childHead"]:
        raise Moved(f"the change request #{number}'s head on the host is "
                    f"{before['headCommit'][:12]}, not {ids['childHead'][:12]} "
                    f"as you were shown — nothing was changed; look again")
    if before.get("base") == onto:
        return _record_retarget(fresh, "adopted", [
            f"the host already targets {onto}; bench recorded it and made "
            f"no edit"])
    if mode == "adopt":
        raise Moved(f"the change request #{number} targets "
                    f"{before.get('base')} on the host, not {onto} as it was "
                    f"last seen — nothing was changed; look again")
    if before.get("base") != ids["target"]:
        raise ValueError(f"the change request #{number} targets "
                         f"{before.get('base')} on the host, which is neither "
                         f"{ids['target']} (bench's plan) nor {onto} — that "
                         f"drift was already there, so nothing was changed; "
                         f"settle the target first (⌖ replan, or on the host)")
    try:
        edit = githost.retarget_change(number, onto)
    except githost.Refused as refused:
        raise ValueError(f"{refused} — nothing was changed. The manual step: "
                         f"{githost.manual_retarget(number, onto, None)}")
    after = githost.change_identity(number)
    notes = []
    if not after.get("ok"):
        reported = ("it reported success" if edit.get("ok") else
                    "it reported failure: " + (edit.get("detail") or "")[:120])
        summary = (f"{filename}: asked the host to retarget #{number} onto "
                   f"{onto} ({reported}), "
                   f"but the change could not be read back, so bench recorded "
                   f"nothing — the host may or may not have changed. The next "
                   f"poll shows it; this action adopts it then")
        _say(filename, summary)
        return {"ok": False, "action": "retarget", "outcome": "unconfirmed",
                "sentence": summary, "identities": ids}
    if after.get("base") != onto:
        summary = (f"{filename}: the host still targets "
                   f"{after.get('base')} after the edit"
                   + (f" ({(edit.get('detail') or '')[:160]})"
                      if edit.get("detail") else "")
                   + f" — bench recorded nothing and its plan still targets "
                   f"{ids['target']}")
        _say(filename, summary)
        return {"ok": False, "action": "retarget", "outcome": "failed",
                "sentence": summary, "identities": ids,
                "manual": githost.manual_retarget(number, onto, None)}
    if not edit.get("ok"):
        notes.append("the edit reported a failure, but the host now targets "
                     f"{onto} — recorded from what was read back")
    if after.get("headCommit") and after["headCommit"] != ids["childHead"]:
        notes.append(f"the head moved to {after['headCommit'][:12]} while it "
                     f"was retargeted — it needs its checks and review too")
    return _record_retarget(fresh, "host", notes)


def _update(fresh: dict) -> dict:
    ids = fresh["identities"]
    filename = fresh["file"]
    stem = filename[:-3]
    cbranch, pbranch, ptip = ids["childBranch"], ids["parentBranch"], ids["parentTip"]
    remote = config.app_remote()
    published_parent = None
    if remote:
        published_parent = plans.remote_branch_commit(remote, pbranch)
    found = gitrepo.ensure_worktree(stem, cbranch)
    if found["problem"]:
        raise ValueError(f"the worktree for {filename} could not be prepared "
                         f"({found['problem']}: {found['detail'] or 'no detail'})"
                         f" — nothing was changed")
    path = found["path"]
    if gitrepo.git(path, "rev-parse", "HEAD").stdout.strip() != ids["childHead"]:
        raise Moved(f"{cbranch} is no longer at {ids['childHead'][:12]} — "
                    f"nothing was changed; look again")
    merged = gitrepo.git(path, "merge", "--no-ff", "--no-edit", "-m",
                         f"stack: update {cbranch} from {pbranch} at "
                         f"{ptip[:12]}", ptip, timeout=180)
    if merged.returncode != 0:
        conflicted = gitrepo.git(path, "diff", "--name-only",
                                 "--diff-filter=U").stdout.split()
        gitrepo.git(path, "merge", "--abort")
        summary = (f"{filename}: updating {cbranch} from {pbranch} conflicts"
                   + (f" on {', '.join(conflicted[:6])}" if conflicted else "")
                   + " — the merge was aborted and the branch is exactly as "
                   "it was; a person settles it (bench will not rebase)")
        _say(filename, summary)
        return {"ok": False, "action": "update", "outcome": "conflict",
                "conflicts": conflicted, "sentence": summary,
                "identities": ids}
    new_head = gitrepo.git(path, "rev-parse", "HEAD").stdout.strip()
    pushed = "not pushed — this application has no remote"
    if remote:
        if published_parent and stacks.contains(stacks.context(), ptip,
                                                published_parent):
            done = gitrepo.git(config.app_repo(), "push", remote,
                               f"refs/heads/{cbranch}:refs/heads/{cbranch}",
                               timeout=180)
            said = (done.stderr.strip().splitlines() or [""])[-1][:120]
            pushed = (f"pushed to {remote}" if done.returncode == 0 else
                      f"not pushed — {remote} refused it ({said}); nothing "
                      f"was forced")
        else:
            pushed = (f"not pushed — {pbranch} at {ptip[:12]} is not on "
                      f"{remote}, and publishing the child would publish the "
                      f"parent's work for it; publish the parent first")
    summary = (f"{filename}: merged {pbranch} at {ptip[:12]} into {cbranch} "
               f"— new head {new_head[:12]}, {pushed}. Its checks and review "
               f"are about the new head now")
    _say(filename, summary)
    return {"ok": True, "action": "update", "outcome": "merged",
            "head": new_head, "pushed": pushed.startswith("pushed"),
            "sentence": summary, "identities": ids}


def _cleanup(fresh: dict) -> dict:
    ids = fresh["identities"]
    filename = fresh["file"]
    stage = taskfiles.find_stage_of(filename) or ""
    remote = config.app_remote()
    landing, head = ids["landing"], ids["childHead"]
    holds, tip, why = completion.remote_holds(remote, landing, head)
    if holds is not True:
        raise ValueError(f"{remote}/{landing} does not hold {head[:12]} "
                         f"({why or 'it is not there'}) — delivery is not "
                         f"proven, so nothing was cleaned up")
    result = completion.finish_delivered(filename, stage, ids["childBranch"],
                                         head, landing)
    summary = (f"{filename}: {remote}/{landing} holds {head[:12]} — cleaned "
               f"up without merging"
               + (" and moved to done/" if result.get("moved") else
                  f"; the card stays in {stage}/ ({result.get('why')})"))
    _say(filename, summary)
    return {"ok": bool(result.get("moved")), "action": "cleanup",
            "outcome": "delivered" if result.get("moved") else "partial",
            "cleanup": result.get("cleanup"), "sentence": summary,
            "identities": ids}
