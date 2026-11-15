"""The derived stack reading: which card a card's work is stacked on, what
is stacked on it, and the state of each edge (task 117, WP09 of the
maintainability plan, §3.4).

A stack is derived, never stored twice. The graph — parent, children,
loops — is `taskfiles.weave_stacks`, read from the cards' own
`**Base branch:**` headers; this module adds what only git and the
records this board already keeps can say about each edge, the three
targets a stacked card has, its place on its path, and the server's own
words for all of it. `/api/state` carries the result as `stacks`, the
card's ⌖ chip and the Overview's **Stack** block draw it, and `delivery`
reads it for its one word, `waiting-for-parent`. The page computes none
of it.

**A read, and only a read.** Every input is local: branch tips from this
checkout's own refs and remote-tracking refs (the last fetch, never a new
one), containment through `git merge-base --is-ancestor`, the recorded
plans, the PR snapshots the poll persisted and the delivered records merge
& clean up wrote. Nothing here fetches, asks a hosting provider, writes a
file, runs a check or makes a worktree — **Check the remote** is the one
explicit action that refreshes the remote-tracking refs it reads.

**No mutation.** Nothing here rebases, force-pushes, retargets or merges a
child into its parent. An edge's `next` names what a person does, and who.

**Three targets, named apart.** The *selected base* is the branch the
work was cut from (and the commit); the *review target* is the branch its
change request targets; the *landing target* is where the work must end
up for `done/` — the application base, reached through the parent's own
landing. A phase member's relation to its phase is `integration`, with the
phase's own semantics; it is shown, never replaced.

**Positions are about a path.** A stack whose cards each carry at most one
child is a chain, and a card's place reads `2/3`. Once any card in it
carries two, there is no single order, so a card reads `step 2` — its depth
on its own path from the bottom — and no count of the whole is invented.
"""

from __future__ import annotations

import subprocess

import changes
import completion
import config
import gitrepo
import plans

V = 1

# Each edge state: its label, its tone (the page's register; the word
# carries the meaning), what to do next and who holds it. `None` for
# `next` means there is nothing to do.
EDGES = {
    "on-parent": ("On its parent", "neutral", None, None),
    "parent-moved": ("Parent moved on", "neutral",
                     "bring the parent's new commits in (act on the PR, or "
                     "an additive merge by hand) — bench will not rebase",
                     "you"),
    "parent-rewritten": ("Parent rewritten", "alarm",
                         "settle it by hand — the parent no longer contains "
                         "the commit this work was cut from, and bench will "
                         "not rebase", "you"),
    "parent-landed": ("Parent landed", "neutral",
                      "retarget this card onto where its parent landed "
                      "(⌖ replan), then its change request", "you"),
    "parent-landed-differently": (
        "Parent landed differently", "alarm",
        "the parent was merged without its commits (a squash or rebase "
        "merge): bring the target into this branch first, then retarget",
        "you"),
    "parent-closed": ("Parent closed", "alarm",
                      "decide which base this work belongs on, then "
                      "⌖ replan", "you"),
    "parent-gone": ("Parent branch gone", "alarm",
                    "decide which base this work belongs on, then ⌖ replan",
                    "you"),
    "parent-not-started": ("Parent not started", "neutral",
                           "start the parent first — this work is cut from "
                           "its branch", "you"),
    "parent-retargeted": ("Parent retargeted on the host", "neutral",
                          "the parent's change request targets another "
                          "branch than its plan — settle the parent's "
                          "target first (⌖ replan on the parent)", "you"),
    "target-changed-on-host": (
        "Target changed on the host", "alarm",
        "adopt the host's target with ⌖ replan, or change it back", "you"),
    "not-started": ("Not started", "neutral", None, None),
    "external-base": ("External base", "neutral", None, None),
    "parent-missing": ("Parent card missing", "alarm",
                       "fix the card's **Base branch:**, or put its parent "
                       "card back on the board", "you"),
    "parent-ambiguous": ("Parent ambiguous", "alarm",
                         "make the **Base branch:** name one card", "you"),
    "cycle": ("Stacked in a loop", "alarm",
              "change one card's **Base branch:** — a stack needs a bottom",
              "you"),
    "integration": ("Phase member", "neutral", None, None),
}

# The states in which the parent's work is no longer what this card waits
# on: it landed (one way or another), or it is not there to wait for.
LANDED = ("parent-landed", "parent-landed-differently")


# ── local reads, once per pass ─────────────────────────────────────────


def _git(*args: str):
    """A local git read in the application, never raising: `None` when
    git could not be run at all."""
    try:
        return gitrepo.app_git(*args)
    except (OSError, subprocess.SubprocessError):
        return None


def _refs() -> tuple[dict[str, str], dict[str, str]]:
    """Every local branch tip, and every remote-tracking tip of the
    application remote — the last fetch, not a new one. One read."""
    remote = config.app_remote()
    heads: dict[str, str] = {}
    tracked: dict[str, str] = {}
    spec = ["refs/heads/"] + ([f"refs/remotes/{remote}/"] if remote else [])
    found = _git("for-each-ref", "--format=%(refname) %(objectname)", *spec)
    if found is None or found.returncode != 0:
        return heads, tracked
    prefix = f"refs/remotes/{remote}/" if remote else None
    for line in found.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, sha = parts
        if name.startswith("refs/heads/"):
            heads[name[len("refs/heads/"):]] = sha
        elif prefix and name.startswith(prefix):
            tracked[name[len(prefix):]] = sha
    return heads, tracked


def context() -> dict:
    """The board-wide reads a pass shares. Local only."""
    heads, tracked = _refs()
    return {"heads": heads, "tracked": tracked, "contains": {},
            "remote": config.app_remote()}


def _tip(ctx: dict, branch: str | None) -> str:
    if not branch:
        return ""
    return ctx["heads"].get(branch) or ctx["tracked"].get(branch) or ""


def _contains(ctx: dict, commit: str, tip: str) -> bool | None:
    """Is `commit` an ancestor of (or equal to) `tip`? `None` when git
    cannot say — a commit this checkout does not have, say."""
    if not commit or not tip:
        return None
    if commit == tip:
        return True
    key = (commit, tip)
    if key not in ctx["contains"]:
        found = _git("merge-base", "--is-ancestor", commit, tip)
        if found is None:
            ctx["contains"][key] = None
        else:
            ctx["contains"][key] = {0: True, 1: False}.get(found.returncode)
    return ctx["contains"][key]


# ── one card ───────────────────────────────────────────────────────────


def _name(task: dict | None, file: str | None = None) -> str:
    if task and task.get("number"):
        return f"#{task['number']}"
    return (task or {}).get("file") or file or "a card"


def _work_branch(task: dict) -> str:
    stem = task["file"][:-3]
    return (config.PHASE_BRANCH_PREFIX if task.get("isPhase")
            else config.TASK_BRANCH_PREFIX) + stem


def _review_target(task: dict, plan: dict | None) -> tuple[str, str]:
    """What the card's change request targets, and what said so — the
    recorded plan first, never a remote lookup."""
    if plan and plan.get("prBase"):
        return plan["prBase"], plan.get("prSource") or "the recorded plan"
    if task.get("phase") and not task.get("isPhase"):
        branch = config.PHASE_BRANCH_PREFIX + task["phase"]["file"][:-3]
        return branch, f"membership of phase {_name(task['phase'])}"
    shape = plans.declared(task)
    return shape["prBase"], shape["prSource"]


def _landing(plan: dict | None) -> str:
    plan = plan or {}
    return plan.get("landing") or plan.get("appBase") or config.APP_BASE


def _delivered(task: dict, plan: dict | None, ctx: dict) -> str | None:
    """How the parent's work landed, if it did: `merged` (a real merge —
    this board's merge & clean up, or the host's with the parent's tip in
    its target), `differently` (the host says merged and the tip is not
    in the target: a squash or a rebase), else None."""
    if completion.delivered_record(task["file"]) or task["stage"] == "done":
        return "merged"
    snap = changes.pr_snapshot(task["file"]) or {}
    if not snap.get("merged"):
        return None
    target, _ = _review_target(task, plan)
    tip = str(snap.get("head") or "") or _tip(ctx, _work_branch(task))
    held = _contains(ctx, tip, _tip(ctx, target))
    return "differently" if held is False else "merged"


def _edge(task: dict, parent: dict | None, plan: dict | None,
          by_file: dict, ctx: dict) -> tuple[str, str]:
    """This card's edge state and the sentence that explains it."""
    here = task.get("stack") or {}
    mapping = here.get("mapping") or "default"
    base = here.get("base")
    if here.get("cycle"):
        loop = " → ".join(_name(by_file.get(f), f)
                          for f in here["cycle"] + here["cycle"][:1])
        return "cycle", f"Its base makes a loop of stacked cards: {loop}."
    if mapping == "integration":
        phase = task.get("phase") or {}
        return "integration", (
            f"A member of phase {_name(phase)}: the phase runner merges it "
            f"into {base}, and the phase lands as a whole.")
    if mapping == "external":
        return "external-base", (
            f"Starts from {base}, a branch no card on this board owns.")
    if mapping == "missing":
        return "parent-missing", f"{here.get('why')}."
    if mapping == "ambiguous":
        return "parent-ambiguous", f"{here.get('why')}."
    if parent is None:
        return "", ""
    who = _name(parent)
    pbranch = _work_branch(parent)
    pplan = plans.recorded(parent["file"])
    mine = changes.pr_snapshot(task["file"]) or {}
    target, _ = _review_target(task, plan)
    if mine.get("baseMismatch") and not mine.get("merged") \
            and str(mine.get("prState") or "").upper() != "CLOSED":
        return "target-changed-on-host", (
            f"Its change request targets {mine['baseMismatch']} on the "
            f"host; its plan says {mine.get('wantBase') or target}.")
    landed = _delivered(parent, pplan, ctx)
    if landed == "differently":
        ptarget, _ = _review_target(parent, pplan)
        return "parent-landed-differently", (
            f"{who} was merged into {ptarget} without its own commits, so "
            f"{pbranch} is not in {ptarget}.")
    if landed == "merged":
        ptarget, _ = _review_target(parent, pplan)
        if target == pbranch:
            return "parent-landed", (
                f"{who} has landed in {ptarget}; this card still targets "
                f"{pbranch}.")
        return "parent-landed", f"{who} has landed in {ptarget}."
    psnap = changes.pr_snapshot(parent["file"]) or {}
    if str(psnap.get("prState") or "").upper() == "CLOSED" \
            and not psnap.get("merged"):
        return "parent-closed", (
            f"{who}'s change request was closed without being merged.")
    if psnap.get("baseMismatch"):
        return "parent-retargeted", (
            f"{who}'s change request targets {psnap.get('base')} on the "
            f"host, not {psnap.get('wantBase')}.")
    ptip = _tip(ctx, pbranch)
    if not ptip:
        if not pplan and not parent.get("pr") \
                and parent["stage"] in ("backlog", "to-do", "in-progress"):
            return "parent-not-started", (
                f"{who} has no work yet; this card is cut from {pbranch}.")
        return "parent-gone", (
            f"{pbranch} is not on this board or its last fetch, and {who} "
            f"has no delivery record.")
    ctip = _tip(ctx, _work_branch(task))
    if not ctip:
        return "not-started", (
            f"No work yet; it will start from {pbranch}.")
    start = ((plan or {}).get("stack") or {}).get("tip") \
        or (plan or {}).get("startCommit") or ""
    if start and _contains(ctx, start, ptip) is False:
        return "parent-rewritten", (
            f"{pbranch} no longer contains {start[:12]}, the commit this "
            f"work was cut from.")
    if _contains(ctx, ptip, ctip) is False:
        return "parent-moved", (
            f"{pbranch} is at {ptip[:12]}, which this branch does not "
            f"contain yet.")
    return "on-parent", f"Contains {pbranch} at {ptip[:12]}."


def _chain(task: dict, by_file: dict) -> list[dict]:
    """The path from the bottom of this card's stack to the card itself.
    A loop has no bottom; its chain is the card alone."""
    if (task.get("stack") or {}).get("cycle"):
        return [task]
    path = [task]
    node = task
    while (node.get("stack") or {}).get("parent") in by_file:
        node = by_file[node["stack"]["parent"]]
        if node in path:
            break
        path.append(node)
    return list(reversed(path))


def _linear(root: dict, by_file: dict) -> int | None:
    """The length of the stack under `root` when it is a chain (every card
    in it carries at most one child), else None — a branching stack has no
    single order to count along."""
    length, node, seen = 1, root, set()
    while True:
        kids = (node.get("stack") or {}).get("children") or []
        if len(kids) > 1:
            return None
        if not kids or kids[0] in seen or kids[0] not in by_file:
            return length
        seen.add(node["file"])
        node = by_file[kids[0]]
        length += 1


def _brief(task: dict) -> dict:
    return {"file": task["file"], "number": task.get("number"),
            "title": task.get("title"), "stage": task.get("stage"),
            "branch": _work_branch(task), "pr": task.get("pr") or None}


def reading(task: dict, by_file: dict, ctx: dict) -> dict | None:
    """One card's stack reading, or None for a card with nothing to say —
    on the application base, stacked on nothing, nothing stacked on it."""
    here = task.get("stack") or {}
    mapping = here.get("mapping") or "default"
    kids = [by_file[f] for f in here.get("children") or [] if f in by_file]
    if mapping == "default" and not kids:
        return None
    plan = plans.recorded(task["file"])
    parent = by_file.get(here.get("parent")) if mapping == "card" else None
    word, sentence = _edge(task, parent, plan, by_file, ctx)
    chain = _chain(task, by_file) if mapping in ("card", "default") else [task]
    stacked = len(chain) > 1 or bool(kids)
    count = _linear(chain[0], by_file) if stacked and not here.get("cycle") \
        else None
    index = len(chain)
    if not stacked:
        position = None
    elif count is not None:
        position = {"kind": "chain", "index": index, "of": count,
                    "label": f"{index}/{count}"}
    else:
        position = {"kind": "path", "index": index, "of": None,
                    "label": f"step {index}"}

    start_branch = (plan or {}).get("startBranch") or here.get("base") \
        or plans.declared(task)["startBranch"]
    target, target_source = _review_target(task, plan)
    landing = _landing(plan)
    via = [_work_branch(c) for c in reversed(chain[:-1])]
    if mapping == "integration":
        via = [here.get("base")]
    out = {
        "v": V, "file": task["file"], "mapping": mapping,
        "base": here.get("base"),
        "parent": _brief(parent) if parent else None,
        "children": [],
        "chain": [_brief(c) for c in chain],
        "position": position,
        "cycle": list(here.get("cycle") or []),
        "why": here.get("why"),
        "targets": {
            "selectedBase": {
                "branch": start_branch,
                "commit": ((plan or {}).get("stack") or {}).get("tip")
                or (plan or {}).get("startCommit") or None,
                "source": (plan or {}).get("source")
                or plans.declared(task)["source"]},
            "reviewTarget": {"branch": target, "source": target_source},
            "landingTarget": {
                "branch": landing, "via": via,
                "sentence": (f"Lands in {landing} once "
                             f"{' and then '.join(via)} "
                             f"{'has' if len(via) == 1 else 'have'} landed."
                             if via else f"Lands in {landing}.")},
        },
        "edge": None,
        "recorded": bool(plan),
        "planVersion": (plan or {}).get("v"),
    }
    if word:
        label, tone, nxt, who = EDGES[word]
        out["edge"] = {"state": word, "label": label, "tone": tone,
                       "sentence": sentence, "next": nxt, "who": who}
    for kid in kids:
        kplan = plans.recorded(kid["file"])
        kword, ksentence = _edge(kid, task, kplan, by_file, ctx)
        out["children"].append(dict(
            _brief(kid), state=kword, label=EDGES[kword][0] if kword else "",
            tone=EDGES[kword][1] if kword else "neutral", sentence=ksentence))
    out["chip"] = _chip(out)
    return out


def _chip(found: dict) -> str | None:
    """The ⌖ chip's words, or None where the plan chip already says it."""
    position = (found.get("position") or {}).get("label")
    parent = found.get("parent")
    if found["mapping"] == "card" and parent:
        who = f"#{parent['number']}" if parent.get("number") else parent["file"]
        return f"on {who}" + (f" · {position}" if position else "")
    if found["mapping"] in ("default",) and found["children"]:
        many = len(found["children"])
        under = (f"#{found['children'][0]['number']}" if many == 1
                 and found["children"][0].get("number")
                 else f"{many} cards")
        return f"under {under}" + (f" · {position}" if position else "")
    return None


# ── the board ──────────────────────────────────────────────────────────


def board(collected: dict, ctx: dict | None = None) -> dict:
    """Every card with something to say about its stack, by filename. A
    board with no stacked card sends an empty object. Local reads only."""
    tasks = [t for group in collected["stages"] for t in group["tasks"]]
    if not any((t.get("stack") or {}).get("mapping") not in (None, "default")
               or (t.get("stack") or {}).get("children") for t in tasks):
        return {}
    ctx = ctx or context()
    by_file = {t["file"]: t for t in tasks}
    out = {}
    for task in tasks:
        found = reading(task, by_file, ctx)
        if found is not None:
            out[task["file"]] = found
    return out


def waiting(found: dict | None) -> dict | None:
    """The parent this card waits on before it can land, or None: a card
    stacked on a card whose work has not landed. Read by `delivery` for its
    one word, `waiting-for-parent`."""
    if not found or found.get("mapping") != "card" or not found.get("parent"):
        return None
    edge = found.get("edge") or {}
    if edge.get("state") in LANDED or edge.get("state") == "cycle":
        return None
    return {"parent": found["parent"], "edge": edge,
            "target": found["targets"]["reviewTarget"]["branch"],
            "landing": found["targets"]["landingTarget"]["branch"]}

