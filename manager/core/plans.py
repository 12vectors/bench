"""One recorded execution plan, carried through a card's whole life.

Bench owns the git lifecycle. The coding agent implements inside the
checkout bench prepared, and never chooses a different one — so *which*
checkout that is has to be decided once, written down, and read back by
everything downstream. This module is that decision.

## What a plan is

A plan answers, for one card, every question a launch used to answer
implicitly and separately:

- which application branch the work starts from (`startBranch`), the ref
  that names it (`startRef`) and the commit it actually resolved to
  (`startCommit`),
- which branch the work lives on (`branch`) and where its worktree goes,
- which branch its PR targets (`prBase`),
- which structured input selected each of those (`source`, `prSource`) —
  never prose, never a guess,
- what this machine can actually do with it (`capabilities`, `delivery`),
- and what stops it, if anything (`blockers`).

## Where it comes from

Three structured inputs, in this order, and nothing else:

1. **Phase membership.** A member inherits its phase's branch and targets
   it; that is what a phase's branch is *for*. A member card that also
   names its own base is two instructions for one decision and is
   rejected (`taskfiles.weave_phases` flags it, and `preflight` refuses
   to launch on it) rather than silently resolved one way.
2. **The card's own headers** — `**Base branch:**` and `**PR base:**`.
   An explicit base selects the application branch to start from, and the
   PR target defaults to that same branch unless `**PR base:**` says
   otherwise. Syntax is validated where the file is read
   (`taskfiles.ref_problem`): these values reach an argument list, so a
   value that is not a branch name is refused by the parser rather than by
   the process that would have run it.
3. **The application default** — `config.APP_BASE`, `main` unless the
   operator changed it. A card with neither header and no phase resolves
   exactly as it always did, which is the whole of "no new metadata keeps
   established behaviour".

A `**Depends on:**` list is *not* one of them. It guards whether a card may
run (`phases._unfinished_dependencies`); it has never been an instruction
to stack a branch, and reading it as one is how a card ends up based on
work nobody asked it to be based on.

## Why it is recorded

The plan is written to `local/state/plans/<stem>.json` before the agent
starts, and read back by every later step: resuming the run, opening the
PR automatically on the move into `review/`, opening it explicitly with
**↑ open PR**, acting on review feedback, and merging at the end. That is
one target chosen once, so an operator who changes `BOARD_APP_BASE`
between launch and review does not retarget work already under way, and a
restarted board still knows what this branch was cut from.

It is machine-local on purpose: it holds this machine's worktree path and
this run's resolved commit. Shared ticket history gets the portable half —
the branch names and the commit — through the ticker, the agent's brief and
the PR body, none of which is a path on somebody's disk.

## What it refuses

Preflight has no side effects: it reads, fetches (bounded) and answers.
Every refusal therefore lands before a branch, a worktree or an agent
exists, which is the point — a blocked launch leaves no ghost run and no
half-made git artifact. And an explicitly required base that cannot be
resolved is a refusal, never a fallback: falling back to the application
default is exactly the failure this module exists to stop.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import config
import taskfiles

# Where a recorded plan lives, and what the file is called. One per card,
# replaced on a replan — the plan is a property of the card's current work,
# not a history of every plan it ever had (the ticker keeps that).
PLAN_VERSION = 1


def plan_path(filename: str) -> Path:
    return config.PLANS_DIR / f"{filename[:-3]}.json"


# ── the application's git ──────────────────────────────────────────────


def app_repo() -> Path:
    """The checkout a card's code is written in. Asked through config so
    the question stays separate from "where does task state live" even
    while one layout answers both with the same path."""
    return config.app_repo()


def app_remote() -> str | None:
    """The remote an application branch is published to and fetched from.

    Note what this is *not* used for: the default base's own branch point
    keeps its established rule (`agents._fresh_branch_point`, which fetches
    a remote literally named `origin` and never reaches past it). This
    remote resolves the branches a card names explicitly — the same remote
    its PR is opened on, which is the only one that could carry them.
    """
    return config.app_remote()


def _git(*args: str, cwd: Path | None = None,
         timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(cwd or app_repo()), *args],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 128, "", "timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def _branch_exists(branch: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet",
                f"refs/heads/{branch}").returncode == 0


def _ref_commit(ref: str) -> str:
    out = _git("rev-parse", "--verify", "--quiet", ref)
    return out.stdout.strip() if out.returncode == 0 else ""


def _remote_branch_commit(remote: str, branch: str) -> str:
    """The commit the remote has for this branch, asked of the remote
    itself. `git ls-remote` is the only reading that is true for a branch
    this checkout has never fetched — which is every stacked base opened on
    a board that did not create it."""
    out = _git("ls-remote", "--heads", remote, f"refs/heads/{branch}",
               timeout=config.FETCH_TIMEOUT + 20)
    if out.returncode != 0:
        return ""
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"refs/heads/{branch}":
            return parts[0]
    return ""


def _fetch(remote: str, branch: str) -> bool:
    """Bounded, and never fatal on its own: a fetch that fails leaves the
    local copy of the branch as the only candidate, and whether that is
    good enough is the caller's judgement."""
    try:
        done = subprocess.run(
            ["git", "-C", str(app_repo()), "fetch", remote,
             f"refs/heads/{branch}:refs/remotes/{remote}/{branch}"],
            capture_output=True, text=True, timeout=config.FETCH_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return False
    return done.returncode == 0


# ── resolving one named branch ─────────────────────────────────────────


def _resolve_branch(branch: str, *, fetch: bool = True) -> dict:
    """A branch name → the ref to start from, the commit it is, and why.

    Newest-first: the remote's copy when it can be reached, this
    checkout's when it cannot. A branch that resolves nowhere returns a
    `problem`, and the caller refuses — there is no third answer where the
    application default quietly takes over.
    """
    remote = app_remote()
    local = _ref_commit(f"refs/heads/{branch}")
    if remote and fetch:
        if _fetch(remote, branch):
            ref = f"{remote}/{branch}"
            commit = _ref_commit(ref)
            if commit:
                note = None
                if local and local != commit:
                    note = (f"{branch} on {remote} is ahead of this checkout's "
                            f"copy — started from {ref}")
                return {"ref": ref, "commit": commit, "branch": branch,
                        "note": note, "problem": None}
        elif local:
            return {"ref": branch, "commit": local, "branch": branch,
                    "note": (f"could not fetch {branch} from {remote} — started "
                             f"from this checkout's copy, which may be stale"),
                    "problem": None}
        elif _remote_branch_commit(remote, branch):
            # The remote has it and the fetch still failed: that is network
            # weather over a base we are not allowed to substitute.
            return {"ref": None, "commit": "", "branch": branch, "note": None,
                    "problem": (f"{branch} exists on {remote} but could not be "
                                f"fetched — nothing was started, because an "
                                f"explicit base is never swapped for another")}
    if local:
        return {"ref": branch, "commit": local, "branch": branch,
                "note": (None if not remote else
                         f"{branch} is local-only — it is not on {remote}"),
                "problem": None}
    where = f" on {remote}" if remote else ""
    return {"ref": None, "commit": "", "branch": branch, "note": None,
            "problem": (f"{branch} does not exist in this checkout{where} — "
                        f"create it, or fix the card's base")}


def _target_exists(branch: str) -> bool:
    """Is this PR target a branch that something could be opened against?
    Local or on the remote; a stacked base is often only the latter."""
    if _branch_exists(branch):
        return True
    remote = app_remote()
    return bool(remote and _remote_branch_commit(remote, branch))


# ── capabilities ───────────────────────────────────────────────────────


def capabilities() -> dict:
    """What this machine can actually do with a plan — asked, never assumed.

    A run that can edit and commit is useful on its own, so none of this
    refuses a launch. What it must not do is *claim* delivery it cannot
    make: a board with no remote, or without `gh`, cannot open the PR a
    card asked for, and the plan says so in `delivery` rather than letting
    the card discover it at the move into `review/`.
    """
    remote = app_remote()
    gh = config.gh_available()
    return {"remote": remote, "gh": gh, "pr": bool(remote) and gh}


def _delivery(plan: dict) -> None:
    """Fill in `delivery` and the note that explains it."""
    caps = plan["capabilities"]
    if caps["pr"]:
        plan["delivery"] = "pr"
        return
    plan["delivery"] = "local-only"
    missing = ("no git remote is configured" if not caps["remote"]
               else f"{config.GH_BIN} is not installed")
    asked = plan["prSource"] != "the application default"
    plan["notes"].append(
        (f"local-only: {missing}, so the PR into {plan['prBase']} this card "
         f"asks for cannot be opened automatically — the work itself is "
         f"unaffected" if asked else
         f"local-only: {missing}, so no PR is opened automatically"))


# ── the plan the card declares, before git is asked anything ───────────


def declared(task: dict) -> dict:
    """The plan as the card's own structured inputs state it: branch names
    and the reason each was chosen, with nothing fetched and nothing
    verified. This is what the board can afford to show for every card on
    every state request, and what `pr_base` falls back on when no plan was
    ever recorded.
    """
    phase = task.get("phase")
    drift = list(task.get("planDrift") or [])
    if phase:
        branch = f"phase/{phase['file'][:-3]}"
        said = f"membership of phase {phase.get('number') or phase['file']}"
        return {"startBranch": branch, "prBase": branch, "source": said,
                "prSource": said,
                "phase": {"file": phase["file"], "branch": branch},
                "drift": drift}
    base = task.get("baseBranch")
    pr_base = task.get("prBase")
    start = base or config.APP_BASE
    return {
        "startBranch": start,
        "prBase": pr_base or start,
        "source": ("the card's **Base branch:** header" if base
                   else "the application default"),
        "prSource": ("the card's **PR base:** header" if pr_base
                     else "the card's **Base branch:** header" if base
                     else "the application default"),
        "phase": None,
        "drift": drift,
    }


def summary(task: dict) -> dict | None:
    """The compact reading a card wears: where its work starts, what its PR
    targets, why, and whether a plan has been recorded for it.

    `None` for a card that is entirely on the defaults and has nothing to
    say — a board with no stacked card and no phase renders exactly what it
    always did.
    """
    shape = declared(task)
    saved = recorded(task["file"])
    plain = (shape["startBranch"] == config.APP_BASE
             and shape["prBase"] == config.APP_BASE)
    if plain and not saved and not shape["drift"]:
        return None
    out = {"startBranch": shape["startBranch"], "prBase": shape["prBase"],
           "source": shape["source"], "prSource": shape["prSource"],
           "drift": shape["drift"], "recorded": False, "default": plain}
    if saved:
        out.update({"recorded": True, "startRef": saved.get("startRef"),
                    "startCommit": saved.get("startCommit"),
                    "startBranch": saved.get("startBranch") or shape["startBranch"],
                    "prBase": saved.get("prBase") or shape["prBase"],
                    "source": saved.get("source") or shape["source"],
                    "prSource": saved.get("prSource") or shape["prSource"],
                    "provenance": saved.get("provenance"),
                    "delivery": saved.get("delivery")})
    return out


# ── the recorded plan ──────────────────────────────────────────────────


def board(collected: dict) -> dict:
    """Every card that has something to say about where its work goes, by
    filename. A board of ordinary cards on the defaults sends an empty
    object, so the page renders exactly what it always did."""
    out = {}
    for group in collected["stages"]:
        for task in group["tasks"]:
            shape = summary(task)
            if shape is not None:
                out[task["file"]] = shape
    return out


PORTABLE = ("task", "branch", "startBranch", "startRef", "startCommit",
            "prBase", "source", "prSource", "appBase", "appRemote",
            "provenance", "delivery", "recorded")


def record(plan: dict) -> dict:
    """Write the plan down, before the work it describes begins.

    Everything travels, this machine's worktree path included — the file is
    under `local/state/`, which is gitignored and never leaves the machine.
    What reaches shared history is the portable half (`portable`), and the
    two are kept apart here rather than at each reader.
    """
    config.PLANS_DIR.mkdir(parents=True, exist_ok=True)
    stored = dict(plan)
    stored["v"] = PLAN_VERSION
    stored["recorded"] = time.time()
    stored.pop("blockers", None)
    plan_path(plan["task"]).write_text(
        json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    return stored


def recorded(filename: str) -> dict | None:
    """The plan this card's work was started on, or None. None is a real
    answer with a documented path behind it (see `_resume`), not an error:
    every card and every worktree that existed before plans did has one."""
    try:
        data = json.loads(plan_path(filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def forget(filename: str) -> bool:
    """Drop the recorded plan — the card's work is over (merged, archived)
    and the next run on it plans afresh."""
    try:
        plan_path(filename).unlink()
        return True
    except OSError:
        return False


def portable(plan: dict) -> dict:
    """The half of a plan that may travel: branch names and a commit, no
    machine-local path and no runtime detail."""
    return {key: plan[key] for key in PORTABLE if key in plan}


# ── preflight ──────────────────────────────────────────────────────────


def _woven(filename: str, stage: str) -> dict:
    """The card as the board shows it — phase membership is derived across
    the whole board, and every decision here depends on it."""
    for group in taskfiles.collect()["stages"]:
        for task in group["tasks"]:
            if task["file"] == filename:
                return task
    return taskfiles.read_task(config.TASKS / stage / filename, stage)


def _foreign_worktree(path: Path) -> str | None:
    """Is something already sitting where this card's worktree goes, and is
    it ours? A directory git does not know as a worktree of this repository
    is foreign: it is refused rather than worked in or cleared away."""
    listed = _git("worktree", "list", "--porcelain").stdout
    paths = {line[len("worktree "):].strip()
             for line in listed.splitlines() if line.startswith("worktree ")}
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    if resolved in paths or str(path) in paths:
        return None
    return (f"{path} already exists and is not a git worktree of this "
            f"repository — move it aside; bench will not clear it")


def _dirty(worktree: Path) -> list[str]:
    out = _git("status", "--porcelain", cwd=worktree)
    return [line for line in out.stdout.splitlines() if line.strip()]


def _resume(plan: dict, task: dict, *, ignore_recorded: bool = False) -> None:
    """A card whose work already exists: validate it against the plan,
    destructively repairing nothing.

    Three shapes arrive here. A run this board planned (`recorded()` has
    the plan) is checked for agreement — a header that changed since the
    launch, or a branch that is not built on the recorded commit, needs an
    explicit replan rather than a silent reset. A run from before plans
    existed and from a card still on the defaults is **adopted**: its base
    is read back out of git and recorded with `provenance: "adopted"`, so
    legacy runs keep working and nothing invents a provenance they never
    had. A legacy run under a card that *does* name a base, where the two
    disagree, is the ambiguous case and gets the refusal.

    Nothing here removes a worktree, resets a branch or discards a commit.
    Uncommitted work and untracked files are exactly what a "repair" would
    cost, and no amount of tidiness is worth it.
    """
    worktree = Path(plan["worktree"])
    branch = plan["branch"]
    # A replan is the person saying "resolve this card afresh", so the
    # recorded plan stops being the authority for this one read. What it
    # does *not* stop is the rest: a replan may not claim a branch is based
    # on something it does not contain, and may not touch a worktree.
    saved = None if ignore_recorded else recorded(plan["task"])

    if worktree.exists():
        foreign = _foreign_worktree(worktree)
        if foreign:
            plan["blockers"].append(foreign)
            return
        current = _git("branch", "--show-current", cwd=worktree).stdout.strip()
        if current != branch:
            plan["blockers"].append(
                f"the worktree at {worktree} is on '{current}', not {branch} — "
                f"nothing was changed; settle it by hand")
            return
        plan["continuing"] = True
        dirty = _dirty(worktree)
        if dirty:
            plan["notes"].append(
                f"continuing in an existing worktree with {len(dirty)} "
                f"uncommitted change{'' if len(dirty) == 1 else 's'} in it — "
                f"they are left exactly as they are")
    elif _branch_exists(branch):
        plan["continuing"] = True
        plan["notes"].append(
            f"{branch} already exists — its worktree will be recreated from it")
    else:
        plan["provenance"] = "planned"
        return

    tip = _ref_commit(f"refs/heads/{branch}")

    if saved:
        changed = []
        if saved.get("startBranch") and saved["startBranch"] != plan["startBranch"]:
            changed.append(f"it was started from {saved['startBranch']}, and the "
                           f"card now resolves to {plan['startBranch']}")
        if saved.get("prBase") and saved["prBase"] != plan["prBase"]:
            changed.append(f"its PR target was recorded as {saved['prBase']}, and "
                           f"the card now resolves to {plan['prBase']}")
        if changed:
            plan["blockers"].append(
                f"{plan['task']}'s execution plan has changed since the work "
                f"started: {'; '.join(changed)}. Nothing was reset — replan the "
                f"card deliberately (⌖ replan) once you have decided which is right")
            return
        base = saved.get("startCommit") or ""
        if base and tip and not _contains(base, branch):
            plan["blockers"].append(
                f"{branch} does not contain {base[:9]}, the commit its recorded "
                f"plan was started from — the branch was rewritten or cut again. "
                f"Nothing was reset; replan it deliberately")
            return
        # The recorded plan governs. The base may well have moved on since,
        # and that is fine: the worktree holds what it was cut from.
        plan["startRef"] = saved.get("startRef") or plan["startRef"]
        plan["startCommit"] = base or plan["startCommit"]
        plan["provenance"] = saved.get("provenance") or "planned"
        return

    # No recorded plan: a run from before plans existed.
    explicit = bool(task.get("baseBranch") or task.get("prBase")
                    or task.get("phase"))
    resolved = plan["startCommit"]
    built_on = bool(resolved and tip and _contains(resolved, branch))
    if explicit and not built_on:
        plan["blockers"].append(
            f"{branch} has no recorded plan and was not cut from "
            f"{plan['startBranch']}, which is what {plan['task']} now asks for — "
            f"bench will not guess what it was based on and will not rebase it. "
            f"Replan the card, or finish this branch and start the new base on a "
            f"fresh one")
        return
    plan["provenance"] = "planned" if built_on else "adopted"
    if not built_on:
        # The compatible path: read the base back out of git rather than
        # claiming the branch was planned from anything.
        merge_base = _git("merge-base", plan["startBranch"], branch).stdout.strip()
        plan["startCommit"] = merge_base or tip
        plan["startRef"] = None
        plan["source"] = (f"adopted from the existing {branch} — "
                          f"it predates this card's recorded plan")
        plan["notes"].append(
            f"adopted an existing {branch}: its base was read back out of git "
            f"rather than planned, and its PR still targets {plan['prBase']}")


def _contains(commit: str, tip: str) -> bool:
    return _git("merge-base", "--is-ancestor", commit, tip).returncode == 0


def preflight(filename: str, stage: str, *, fetch: bool = True,
              task: dict | None = None, ignore_recorded: bool = False) -> dict:
    """Resolve and validate everything a launch needs, and change nothing.

    Returns the plan. `plan["blockers"]` is the whole answer to "may this
    start": empty means yes. Nothing here creates a branch, a worktree, a
    claim or a process, so a refusal costs exactly one read and leaves the
    card as it was.
    """
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    card = task if task is not None else _woven(filename, stage)
    shape = declared(card)
    stem = filename[:-3]
    branch = (f"phase/{stem}" if card.get("isPhase") else f"task/{stem}")
    plan: dict = {
        "task": filename,
        "stage": stage,
        "branch": branch,
        "worktree": str(config.WORKTREES / stem),
        "appBase": config.APP_BASE,
        "appRemote": app_remote(),
        "startBranch": shape["startBranch"],
        "startRef": None,
        "startCommit": "",
        "prBase": shape["prBase"],
        "source": shape["source"],
        "prSource": shape["prSource"],
        "phase": shape["phase"],
        "capabilities": capabilities(),
        "continuing": False,
        "provenance": "planned",
        "explicitBase": bool(card.get("baseBranch")),
        "explicitTarget": bool(card.get("prBase")),
        "notes": [],
        "blockers": list(shape["drift"]),
    }
    if plan["blockers"]:
        # The headers do not add up. Resolving them against git would be
        # answering a question the card has not finished asking.
        _delivery(plan)
        return plan

    resolved = _resolve_ref(plan, fetch=fetch)
    if resolved is not None and resolved.get("problem"):
        plan["blockers"].append(resolved["problem"])
        _delivery(plan)
        return plan

    # The PR target has to be a branch something can be opened against —
    # asked here, before the work rather than at the move into review/.
    if plan["prBase"] != config.APP_BASE and not _target_exists(plan["prBase"]):
        if plan["capabilities"]["remote"] or _branch_exists(plan["prBase"]):
            plan["blockers"].append(
                f"the PR target {plan['prBase']} ({plan['prSource']}) does not "
                f"exist in this checkout or on {plan['appRemote'] or 'any remote'} "
                f"— nothing was started")
        else:
            plan["notes"].append(
                f"the PR target {plan['prBase']} could not be verified: no remote "
                f"is configured, so only a local branch could have confirmed it")
    if plan["prBase"] != plan["startBranch"]:
        # Legal, and worth saying out loud: the diff a reviewer sees is the
        # branch against its target, so a card that starts somewhere else
        # puts that difference in the PR too.
        plan["notes"].append(
            f"this card starts from {plan['startBranch']} and targets "
            f"{plan['prBase']} — the PR's diff will include whatever "
            f"{plan['startBranch']} has that {plan['prBase']} does not")
    _delivery(plan)
    if plan["blockers"]:
        return plan
    _resume(plan, card, ignore_recorded=ignore_recorded)
    return plan


def replan(filename: str, stage: str) -> dict:
    """The explicit decision that retargets a card's work.

    Nothing retargets itself. A card whose headers changed under a running
    branch, a PR open against a base the card no longer names, a
    prerequisite that merged and vanished — each of those refuses and names
    this, and this is the only thing that moves it, because which base is
    right is a judgement about the work rather than a fact about git.

    It still refuses the one thing it cannot honestly do: a branch that
    does not contain the new base was not cut from it, and bench will not
    rebase, reset or delete a branch to make the claim true. Then the plan
    on record is the old one, unchanged — a refused replan changes nothing.
    """
    fresh = require(preflight(filename, stage, ignore_recorded=True))
    previous = recorded(filename)
    stored = record(fresh)
    return {"plan": stored, "previous": portable(previous) if previous else None}


def _resolve_ref(plan: dict, *, fetch: bool) -> dict | None:
    """Where the work starts, resolved to a ref and a commit.

    Three paths, and which one applies is already settled by the time this
    runs:

    - **A phase member** starts from its phase's *local* branch. The phase
      branch is the running board's own artifact, so the copy on the remote
      is at best a mirror of it; and a phase that has not been started yet
      has no branch at all, which is the one case where a member falls back
      to the ordinary default (exactly as `phase_branch_point` always did).
    - **An explicit base** is resolved by name — newest copy first — and
      never substituted. This is the path that must refuse rather than fall
      back, because falling back is the failure being fixed.
    - **The default base** keeps the rule it has always had:
      `agents._fresh_branch_point`, which fetches a remote named `origin`
      and uses this checkout's HEAD rather than making a launch wait on the
      network. The import is local because `agents` imports this module.
    """
    import agents                                   # noqa: PLC0415 — cycle
    phase = plan.get("phase")
    if phase:
        point, note = agents.phase_branch_point(plan["task"])
        if point:
            plan["startRef"] = point
            plan["startCommit"] = _ref_commit(f"refs/heads/{point}")
            if note:
                plan["notes"].append(note)
            return None
        plan["notes"].append(
            f"{phase['branch']} does not exist yet — this card's phase has not "
            f"been started, so the work starts from the application default")
        plan["startBranch"] = config.APP_BASE
        plan["prBase"] = config.APP_BASE
        plan["source"] = "the application default (its phase has no branch yet)"
        plan["prSource"] = plan["source"]
    elif plan["startBranch"] != config.APP_BASE or plan["explicitBase"]:
        found = _resolve_branch(plan["startBranch"], fetch=fetch)
        if found["problem"]:
            return found
        plan["startRef"] = found["ref"]
        plan["startCommit"] = found["commit"]
        if found["note"]:
            plan["notes"].append(found["note"])
        return found

    point, note = agents._fresh_branch_point() if fetch else (None, None)
    plan["startRef"] = point
    plan["startCommit"] = _ref_commit(point or "HEAD")
    if note:
        plan["notes"].append(note)
    if not plan["startCommit"]:
        plan["notes"].append(
            f"this checkout has no commit yet — {plan['branch']} starts the history")
    return None


def require(plan: dict) -> dict:
    """The plan, or the reason it cannot be used. One blocker is enough to
    refuse, and the first is the one to say: they are appended in the order
    the resolution asked the questions, so the first is the earliest thing
    that went wrong."""
    if plan["blockers"]:
        raise ValueError(plan["blockers"][0])
    return plan


# ── reading a plan back ────────────────────────────────────────────────


def pr_base(filename: str, task: dict | None = None) -> str:
    """What a PR for this card is opened against.

    The recorded plan first, and that is the load-bearing part: automatic
    opening on the move into `review/` and explicit **↑ open PR** read the
    same answer, and an operator who changes `BOARD_APP_BASE` after a
    launch does not retarget work already under way. Only a card with no
    recorded plan falls back to what its headers and its phase say now.
    """
    saved = recorded(filename)
    if saved and saved.get("prBase"):
        return saved["prBase"]
    if task is None:
        stage = taskfiles.find_stage_of(filename)
        if stage is None:
            return config.APP_BASE
        task = _woven(filename, stage)
    shape = declared(task)
    if shape["phase"]:
        # A phase branch is the board's own and is published as the phase
        # runs, so a board that did not run the phase may know it only
        # through the remote — that still makes it the base. A phase whose
        # branch exists nowhere was never started, and there is nothing to
        # stack on.
        branch = shape["phase"]["branch"]
        return branch if _target_exists(branch) else config.APP_BASE
    return shape["prBase"]


def remember_target(filename: str, branch: str, base: str) -> dict:
    """Pin the PR target of work that reached GitHub without a plan.

    A hand-made branch, or a run from before any of this existed, opens its
    PR against what its structured inputs say *now*. Once that PR exists,
    that answer has to stop moving: the merge at the end, the review loop
    and the next global setting change all read from here. So the open
    records it, marked `adopted` — the plan is a note of what was actually
    done, never a claim that it was planned.
    """
    saved = recorded(filename) or {}
    if saved.get("prBase") == base and saved.get("branch") == branch:
        return saved
    stored = dict(saved)
    stored.update({"task": filename, "branch": branch, "prBase": base,
                   "prSource": saved.get("prSource")
                   or f"adopted when the PR was opened against {base}"})
    stored.setdefault("startBranch", base)
    stored.setdefault("startRef", None)
    stored.setdefault("startCommit", "")
    stored.setdefault("source", saved.get("source")
                      or "adopted from the branch that already existed")
    stored.setdefault("appBase", config.APP_BASE)
    stored.setdefault("appRemote", app_remote())
    stored["provenance"] = saved.get("provenance") or "adopted"
    return record(stored)


def target_problem(filename: str, base: str) -> str | None:
    """Is the target this card's work is aimed at still there?

    Asked before every step that would act on it — opening the PR, acting
    on its reviews, merging and cleaning up. A prerequisite branch that was
    merged and deleted is not an error to route around: the situation
    changed, and which base the work belongs on now is a person's decision.
    """
    if base == config.APP_BASE:
        return None
    if _target_exists(base):
        return None
    return (f"{filename}'s work targets {base}, and that branch no longer "
            f"exists in this checkout or on {app_remote() or 'any remote'} — "
            f"it was probably merged and deleted. Retarget the card "
            f"deliberately (⌖ replan) rather than letting it land somewhere "
            f"it was not reviewed against")


def line(plan: dict) -> str:
    """The plan as one sentence, for the ticker: where it starts, what it
    targets, and which structured input said so."""
    where = plan.get("startRef") or plan.get("startBranch") or "HEAD"
    commit = (plan.get("startCommit") or "")[:9]
    said = f" ({plan['source']})" if plan.get("source") else ""
    target = f" → PR into {plan['prBase']}" if plan.get("prBase") else ""
    return (f"from {where}{f' at {commit}' if commit else ''}{said}{target}")


def brief(plan: dict) -> str:
    """The plan as the coding agent is told it: what bench already decided,
    so the run can *verify* its checkout rather than choose a lifecycle of
    its own. Branch names and a commit only — no path from this machine."""
    lines = [
        f"- Branch: `{plan['branch']}`",
        f"- Started from: `{plan.get('startRef') or plan.get('startBranch')}`"
        + (f" at `{(plan.get('startCommit') or '')[:12]}`"
           if plan.get("startCommit") else ""),
        f"- Selected by: {plan.get('source')}",
        f"- Its pull request will target: `{plan.get('prBase')}`"
        f" ({plan.get('prSource')})",
    ]
    if plan.get("delivery") == "local-only":
        lines.append("- Automatic pull-request delivery is unavailable on this "
                     "board, so the work stays local until someone publishes it.")
    return "\n".join(lines)
