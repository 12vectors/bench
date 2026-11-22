"""Integration readiness: a review candidate against the target it would
land in, now (task 189).

A card's review judges its work against its brief, at its own head. That
says nothing about the target it lands in: A and B can both pass review
against an earlier `main`, and once A is admitted B's result does not
establish that B still works on the `main` that holds A. Git reports a
textual conflict, or a clean merge that still breaks combined behaviour,
and a provider's "mergeable" is the first of those at most.

So this module keeps two facts apart from the review:

- **Target freshness** — whether the candidate head *contains* the
  target's current tip. A head that contains it is the combined candidate:
  its tree is the target with this card's work on top, so a check bench
  ran at that exact head (source-bound, as every check is) is evidence
  about the combination. A head that does not is reviewed work whose
  combination with the accepted target is unverified — however clean a
  provider says the merge would be.
- **Admission** — the guarded merge into the target, serialized per
  repository and target branch (`admission`), and refused at the moment
  of mutation when the candidate no longer contains the target the remote
  holds (`require_current`).

Only accepted work counts as the target: the target branch as the
remote's copy was last fetched, else this checkout's — never another
pull request, and never a speculative combination of pending ones.

**When freshness is required.** Where the merge rule in force requires
at least one check (`applies`): the combined behaviour is otherwise not
verified by anything, and an ungated board, or one requiring only a
review, merges exactly as it always did — its reading still says the
target moved, as information.

**What a guard can promise.** A local merge publishes with a plain,
non-forced push, which git refuses when the remote target moved: that is
a compare-and-set on the target. A merge on the host is pinned to the
head (`--match-head-commit`) and the target is read immediately before
it; the host does not let bench make the merge conditional on the
target's tip, so a target that moves between that read and the host's
merge is a race bench detects afterwards (the next reading reads the new
target) and discloses — it does not claim atomicity there. `GUARANTEES`
says the same thing as data.

Reading (`reading`, `target_tip`) is a read: local refs and local git
only. `require_current` asks the remote (`git ls-remote`) because it is
the last step before a mutation, like `completion.remote_holds`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import re
import threading
import time
from pathlib import Path

import config
import gates
import gitrepo
import plans
import records

V = 1

# Every state a reading may carry: (label, tone). The words are what the
# card, the detail view and the Orchestrator say.
STATES = {
    "ready-to-admit": ("Ready to admit", "calm"),
    "up-to-date": ("Up to date with target", "neutral"),
    "integration-check-needed": ("Integration check needed", "neutral"),
    "integration-check-running": ("Integration check running", "working"),
    "integration-check-failed": ("Integration check failed", "alarm"),
    "target-changed": ("Target changed — integration check needed",
                       "attention"),
    "conflicts": ("Conflicts with target — repair needed", "alarm"),
    "updating": ("Updating against target", "working"),
    "unknown": ("Integration unknown", "alarm"),
    "no-candidate": ("No candidate to integrate", "neutral"),
}

# What each guard is, said apart: a provider's atomic guarantee, and what
# bench can only detect.
GUARANTEES = {
    "local": ("the merge is published with a plain push, which git refuses "
              "when the remote target moved since it was read — a "
              "compare-and-set on the target"),
    "origin": ("the host merge is pinned to the head (--match-head-commit); "
               "the target is read immediately before it but the host does "
               "not make the merge conditional on the target's tip, so a "
               "target that moves in between is detected afterwards, not "
               "prevented"),
    "lock": ("one admission per repository and target branch at a time on "
             "this machine; another installation is held by the remote "
             "guard above, not by this lock"),
}

UPDATE_ACTION = "Update against target and verify"


class Stale(ValueError):
    """The candidate does not contain the target it would land in."""

    def __init__(self, code: str, sentence: str, **facts):
        super().__init__(sentence)
        self.code = code
        self.facts = facts


class Busy(ValueError):
    """Another admission into the same target is in progress."""


# ── the target ────────────────────────────────────────────────────────


def target_for(task: dict, plan: dict | None = None) -> str:
    """Where this card's work lands, read without a fetch: the recorded
    plan, else the card's own headers — a phase member's phase branch, a
    phase card's application base."""
    plan = plans.recorded(task["file"]) if plan is None else plan
    if plan and plan.get("prBase"):
        return plan["prBase"]
    if task.get("phase") and not task.get("isPhase"):
        return config.PHASE_BRANCH_PREFIX + task["phase"]["file"][:-3]
    return plans.declared(task)["prBase"]


def _rev(ref: str) -> str:
    out = gitrepo.app_git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return out.stdout.strip() if out.returncode == 0 else ""


def target_tip(target: str, remote_tips: dict | None = None) -> dict:
    """The accepted target's tip as this checkout knows it: the remote's
    copy as last fetched, else the local branch — the reading
    `basechoice` and the launch use for a branch. A read."""
    remote = config.app_remote()
    if remote:
        tip = (remote_tips or {}).get(target) if remote_tips is not None \
            else _rev(f"refs/remotes/{remote}/{target}")
        if tip:
            return {"commit": tip, "ref": f"{remote}/{target}",
                    "authority": "remote-tracking"}
    tip = _rev(f"refs/heads/{target}")
    if tip:
        return {"commit": tip, "ref": target, "authority": "local"}
    return {"commit": "", "ref": target, "authority": "unknown"}


def _count(spec: str) -> int | None:
    out = gitrepo.app_git("rev-list", "--count", spec)
    try:
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except ValueError:
        return None


def incoming(head: str, tip: str, limit: int = 20) -> list[dict]:
    """The accepted target commits the candidate does not hold yet, newest
    first and bounded: what an update would bring in. A read."""
    out = gitrepo.app_git("log", f"--max-count={limit}", "--format=%H %s",
                          f"{head}..{tip}")
    found = []
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        sha, _, subject = line.partition(" ")
        found.append({"commit": sha, "subject": subject[:160]})
    return found


# What a work root holds that is bookkeeping, not the application: where
# the work state shares the application's repository (a vendored or
# source-hosted install), every card move is a `board:` commit on the
# target, and none of it is a change a candidate must be combined with.
WORK_STATE_DIRS = ("tasks", "plans", "reference", "brain", "setup",
                   "orchestration")


def _work_state_prefixes() -> tuple[str, ...]:
    if not config.app_shares_state_repository():
        return ()
    try:
        rel = config.work_root().resolve().relative_to(
            config.app_repo().resolve()).as_posix()
    except (ValueError, OSError):
        return ()
    lead = "" if rel in ("", ".") else rel.rstrip("/") + "/"
    return tuple(f"{lead}{d}/" for d in WORK_STATE_DIRS)


def application_change(head: str, tip: str) -> bool | None:
    """Does the target at `tip` change anything but the work state since
    `head` forked from it? True, False, or None when git cannot say. Where
    the work state has a repository of its own, every change counts."""
    prefixes = _work_state_prefixes()
    if not prefixes:
        return True
    out = gitrepo.app_git("diff", "--name-only", f"{head}...{tip}")
    if out.returncode != 0:
        return None
    return any(not path.startswith(prefixes)
               for path in out.stdout.splitlines() if path.strip())


def holds(tip: str, head: str) -> bool:
    """Does `head` hold the target at `tip`, for integration: contains it,
    or differs from it only by the work state's own bookkeeping."""
    if gates.contained(tip, head):
        return True
    return application_change(head, tip) is False


def tree_of(commit: str) -> str:
    return _rev(f"{commit}^{{tree}}") if commit else ""


# ── whether freshness is required ─────────────────────────────────────


def applies(decision: dict | None) -> bool:
    """Is target freshness a condition of admission here? Where the merge
    rule in force requires at least one check — the combined candidate is
    what that check must have run on."""
    if not decision or decision.get("decision") == "ungated":
        return False
    return any(r.get("kind") == "check"
               for r in decision.get("requirements") or [])


# ── update attempts, kept per card ────────────────────────────────────


_ATTEMPTS = 20


def state_dir() -> Path:
    """Beside the recorded plans and the completion records: machine-local,
    for the same reason."""
    return config.PLANS_DIR.parent / "integration"


def attempts_path(filename: str) -> Path:
    return state_dir() / f"{filename[:-3]}.json"


# Cards with an update against their target in flight in this process,
# so a second request — a double click, the orchestrator beside a person
# — is refused rather than doubled, and the page can say so.
UPDATING: set[str] = set()
UPDATING_LOCK = threading.Lock()


def attempts(filename: str) -> list[dict]:
    """This board's update-against-target attempts for the card, oldest
    first. Machine-local, like the completion records."""
    found = records.read_json(attempts_path(filename), expect=dict) or {}
    return [a for a in found.get("attempts") or [] if isinstance(a, dict)]


def record_attempt(filename: str, attempt: dict) -> dict:
    """Append one attempt (bounded) and return it."""
    path = attempts_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = attempts(filename)[-(_ATTEMPTS - 1):]
    entry = {"v": V, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             **{k: v for k, v in attempt.items() if v is not None}}
    if isinstance(entry.get("conflicts"), list):
        entry["conflicts"] = [str(c)[:200] for c in entry["conflicts"][:20]]
    kept.append(entry)
    records.write_json(path, {"v": V, "kind": "bench-integration-attempts",
                              "file": filename, "attempts": kept},
                       durable=True)
    return entry


# ── the reading ───────────────────────────────────────────────────────


def _check_rows(decision: dict | None) -> list[dict]:
    return [r for r in (decision or {}).get("requirements") or []
            if r.get("kind") == "check"]


def reading(task: dict, *, head: str | None, decision: dict | None,
            pr_state: dict | None = None, plan: dict | None = None,
            remote_tips: dict | None = None, updating: bool = False) -> dict:
    """The integration readiness of one review candidate. A read: local
    refs, local git and the records already on disk.

    `pr_state` is the PR poll's last reading (`changes.pr_state`), whose
    `conflicts` is the provider's mergeability — a textual reading only,
    and only about the head the poll saw. `updating` is a run or an update
    in flight on the card."""
    filename = task["file"]
    target = target_for(task, plan)
    tip = target_tip(target, remote_tips)
    out = {"v": V, "target": target, "targetTip": tip["commit"] or None,
           "targetRef": tip["ref"], "authority": tip["authority"],
           "head": head, "required": applies(decision), "behind": None,
           "incoming": [], "provider": None, "attempt": None,
           "guarantees": GUARANTEES["origin" if config.app_merge_on_origin()
                                    else "local"]}
    if not head:
        return _word(out, "no-candidate", "there is no implementation commit "
                     "on this board to integrate", None)
    if not tip["commit"]:
        return _word(out, "unknown", f"{target} cannot be read in this "
                     f"checkout, so whether {head[:12]} holds it is not "
                     f"known", "check-remote")
    fresh = holds(tip["commit"], head)
    out["fresh"] = fresh
    seen = (pr_state or {})
    mergeable = seen.get("conflicts")
    out["provider"] = {"conflicts": mergeable,
                       "head": seen.get("head"),
                       "about": ("this head" if seen.get("head") == head
                                 else "an earlier head" if seen.get("head")
                                 else "unknown")}
    mine = [a for a in attempts(filename)
            if a.get("head") == head and a.get("target") == target]
    last = mine[-1] if mine else None
    out["attempt"] = last
    if updating:
        return _word(out, "updating", f"an update of {head[:12]} against "
                     f"{target} is in progress", None)
    if not fresh:
        out["behind"] = _count(f"{head}..{tip['commit']}")
        out["incoming"] = incoming(head, tip["commit"], limit=8)
        conflicted = (last and last.get("outcome") == "conflict"
                      and last.get("targetTip") == tip["commit"]) or (
            mergeable is True and seen.get("head") == head)
        behind = out["behind"]
        n = f"{behind} commit{'' if behind == 1 else 's'}" \
            if behind is not None else "commits"
        if conflicted:
            files = (last or {}).get("conflicts") or []
            on = f" on {', '.join(files[:4])}" if files else ""
            return _word(out, "conflicts", f"{target} at {tip['commit'][:12]} "
                         f"has {n} {head[:12]} does not hold, and merging them "
                         f"conflicts{on} — a repair on this card's own branch "
                         f"is needed", "update")
        said = ("" if mergeable is None else
                " The provider reports no textual conflict, which is not "
                "evidence that the combination works.")
        return _word(out, "target-changed", f"{target} moved to "
                     f"{tip['commit'][:12]} ({n} {head[:12]} does not hold); "
                     f"this card's checks and review are about work that has "
                     f"not been combined with it.{said}", "update")
    # fresh: the head is the combined candidate, and its checks are about it
    rows = _check_rows(decision)
    out["combined"] = {"head": head, "tree": tree_of(head),
                       "target": target, "targetTip": tip["commit"]}
    if not rows:
        return _word(out, "up-to-date", f"{head[:12]} holds {target} at "
                     f"{tip['commit'][:12]}; no check is required, so the "
                     f"combination is not verified", None)
    if any(r.get("state") == "pending" for r in rows):
        return _word(out, "integration-check-running", f"a required check is "
                     f"running on {head[:12]}, which holds {target} at "
                     f"{tip['commit'][:12]}", None)
    failed = [r for r in rows if r.get("state") == "unmet"
              and r.get("reason") in ("failed", "prepare-failed", "error",
                                      "denied")]
    if failed:
        return _word(out, "integration-check-failed", f"{failed[0].get('name')} "
                     f"did not pass on {head[:12]}, the combination of this "
                     f"work with {target} at {tip['commit'][:12]}", "repair")
    if any(r.get("state") != "met" for r in rows):
        return _word(out, "integration-check-needed", f"{head[:12]} holds "
                     f"{target} at {tip['commit'][:12]}; its required checks "
                     f"have not passed on it yet", "run-required-checks")
    if (decision or {}).get("decision") in ("allowed", "overridden"):
        return _word(out, "ready-to-admit", f"{head[:12]} holds {target} at "
                     f"{tip['commit'][:12]} and every requirement is met on "
                     f"it; admission still asks the merge rules, the "
                     f"authority and the target again immediately before it",
                     "complete")
    return _word(out, "up-to-date", f"{head[:12]} holds {target} at "
                 f"{tip['commit'][:12]} and its required checks passed on it; "
                 f"other requirements are not met yet", None)


def _word(out: dict, word: str, sentence: str, nxt: str | None) -> dict:
    label, tone = STATES[word]
    out.update(state=word, label=label, tone=tone, sentence=sentence,
               next=nxt)
    return out


def stale(found: dict | None) -> bool:
    """Is this reading one whose admission freshness would refuse?"""
    return bool(found) and bool(found.get("required")) and \
        found.get("state") in ("target-changed", "conflicts", "unknown")


# ── admission: one at a time per target, and checked at the mutation ──


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
# Who holds each target's admission in this process, for the page.
HOLDERS: dict[str, dict] = {}


def target_key(target: str) -> str:
    repo = str(config.app_repo())
    return hashlib.sha256(f"{repo}\x00{target}".encode()).hexdigest()[:16]


def _lock_path(key: str) -> Path:
    return state_dir() / "locks" / f"{key}.lock"


@contextlib.contextmanager
def admission(target: str, card: str, step: str = "merge"):
    """Hold the admission of `target` (in this repository) for one card.
    Refused at once — never queued — when another admission into the same
    target holds it, in this process or another one on this machine;
    different targets never wait for each other."""
    key = target_key(target)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        holder = HOLDERS.get(key) or {}
        raise Busy(f"another admission into {target} is in progress "
                   f"({holder.get('card') or 'another card'}, "
                   f"{holder.get('step') or 'merging'}) — nothing was merged; "
                   f"it is asked again once that one ends")
    fd = None
    try:
        path = _lock_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            fd = None
            raise Busy(f"another process on this machine is admitting work "
                       f"into {target} — nothing was merged")
        HOLDERS[key] = {"card": card, "target": target, "step": step,
                        "since": time.time()}
        yield {"key": key, "target": target}
    finally:
        HOLDERS.pop(key, None)
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        lock.release()


def holder(target: str) -> dict | None:
    found = HOLDERS.get(target_key(target))
    return dict(found) if found else None


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _ls_remote(remote: str, target: str) -> tuple[str | None, str]:
    """The remote's tip of `target`: (sha, "") when read, ("", "") when the
    remote has no such branch, (None, why) when it could not be asked."""
    out = gitrepo.app_git("ls-remote", "--heads", remote,
                          f"refs/heads/{target}",
                          timeout=config.FETCH_TIMEOUT + 20)
    if out.returncode != 0:
        said = (out.stderr.strip().splitlines() or ["no detail"])[-1][:160]
        return None, said
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"refs/heads/{target}" \
                and _SHA.match(parts[0]):
            return parts[0], ""
    return "", ""


def require_current(filename: str, decision: dict | None, head: str,
                    target: str, *, ask_remote: bool = True) -> dict:
    """Immediately before an admission: the candidate must contain the
    target it lands in — this checkout's copy and the remote's own, read
    now. Raises `Stale` with the reason and the supported way on; returns
    the combined identity the merge is about. Where freshness does not
    apply (`applies`), says so and checks nothing."""
    if not applies(decision):
        return {"applies": False, "head": head, "target": target}
    local = _rev(f"refs/heads/{target}")
    remote = config.app_remote() if ask_remote else None
    remote_tip = ""
    if remote:
        remote_tip, why = _ls_remote(remote, target)
        if remote_tip is None:
            raise Stale("target-unreadable",
                        f"{remote}/{target} could not be read ({why}), so "
                        f"whether {head[:12]} holds the accepted target is "
                        f"not known — nothing was merged; ask again once "
                        f"the remote answers", target=target)
    for tip, where in ((local, target), (remote_tip, f"{remote}/{target}")):
        if not tip:
            continue
        if not _rev(tip):
            raise Stale("target-moved",
                        f"{where} is at {tip[:12]}, which this checkout has "
                        f"not fetched — the target moved after this card was "
                        f"read; nothing was merged. Check the remote, then "
                        f"{UPDATE_ACTION.lower()}",
                        target=target, targetTip=tip)
        if not holds(tip, head):
            raise Stale("target-changed",
                        f"{where} is at {tip[:12]}, which {head[:12]} does "
                        f"not hold — its checks are not about the combination "
                        f"with the accepted target. Nothing was merged: "
                        f"{UPDATE_ACTION.lower()} first",
                        target=target, targetTip=tip)
    tip = remote_tip or local
    # (which guarantee applies is the caller's to say: the merge mode is
    # read by the merge itself, once, after the last gate read)
    return {"applies": True, "head": head, "target": target,
            "targetTip": tip, "tree": tree_of(head)}
