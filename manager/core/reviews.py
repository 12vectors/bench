"""The checkout a pull-request review runs in: pinned to the reviewed
head, owned by the run, and never the live application.

`plans` answers which checkout a *work* run works in. `profiles` answers
what a run may do there, and `verify` runs the project's checks itself.
This module answers the question those three left open for the read-only
side: **which revision is being reviewed, and where does the reviewer
execute it?**

## Why the live checkout is the wrong answer

`start_pr_review` used to launch in `config.REPO`. Three things follow
from that, and each is a reason this module exists:

- **It is not the reviewed code.** The application checkout is on
  whatever branch a person left it on, with whatever they have edited in
  it. A verdict posted from there is about something nobody chose.
- **It is not pinned.** A PR's head moves — a push lands mid-review — and
  a review of "the branch, whenever you looked" cannot say what it
  covered. A commit can.
- **It is shared.** A test suite run in it takes a working tree, a
  driver and every other worktree's git metadata down with it, which is
  exactly why a project's own verification wrapper is right to refuse
  it (`profiles.CHECK_ROOTS` has never included `app`).

So bench resolves the pull request's repository, base and head itself,
brings that commit into the application's object store, and adds a
**detached worktree of that one commit** under
`manager/local/state/reviews/`. The run works there. Nothing it does can
reach a tracked file of anyone else's checkout, and the revision cannot
change under it, because a detached checkout has no branch to follow.

## What is enforced, and what is merely resolved

Said narrowly, as `verify` says it. Bench **enforces** that the review
starts in the pinned checkout (it is the child process's cwd), that the
checkout is detached at one commit bench put there, that a directory
which is a checkout of something else is refused rather than used, and
that nothing uncommitted is ever removed. Bench **resolves** the head
from GitHub when it can; when `gh` cannot answer, the branch tip in this
checkout is pinned instead and the record says `authority: "local"` in
those words — a local-only board stays usable and stays honest, which is
the same bargain `plans` strikes with `local-only`.

What this is *not* is a sandbox. `filesystem-sandbox` is the capability
that would confine a run to the directory it was started in, nothing
shipped enforces it, and a profile requiring it is refused
(`profiles.PENDING_CAPABILITIES`).

## Ownership, and what that buys

One record per run, under `manager/local/state/reviews/<agent id>.json`,
written before the model is invoked. It names the task, the run, the
checkout, the base and head identities, the pull request's state and the
process that made it. Machine-local for the manifest's reason: it holds
this machine's paths.

From that, four things the board could not do before:

- **A second run never reuses a first's checkout by accident.** A
  directory another task or another run owns is refused by name.
- **Cleanup is what this board made and nothing else.** A release
  removes only a checkout whose record says bench added it, only when
  nothing uncommitted is in it, and only after the path has been checked
  to be inside the reviews directory with every link followed.
- **A restart owns up.** A record left unreleased by a board that is gone
  is closed on the next start, naming the restart.
- **A verdict is bound to a revision.** `freshness()` compares the pinned
  head with what the PR poll last saw. A head that moved makes the review
  *historical*: it still says what it said, about the commit it said it
  about, and it is never shown as satisfying the new head.

## Historical, as against current

A merged or closed pull request is not a thing to push to. A phase
member's PR is merged into the phase branch early and stays merged, so
most member reviews on a board end up historical by design — and
"historical member review" and "validation of the current integration
head" are two different facts about two different revisions.
`repair_problem()` is where that becomes a refusal: **↻ act on PR**
declines a merged or closed target by name instead of pushing to a branch
whose PR cannot carry it, and names the integration head as the separate
target it is.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import config
import plans
import profiles
import state

VERSION = 1

# How strongly bench knows what it pinned. Two words, used everywhere, for
# the reason `profiles.ENFORCEMENT` has four: "the PR's head, read from
# GitHub" and "the branch tip in this checkout" are different claims and a
# reader must be able to tell them apart.
AUTHORITIES = {
    "github": "read from the pull request itself",
    "local": "read from this checkout's own branch, because GitHub could "
             "not be asked — the review is of what is here",
    "plan": "the card's recorded execution plan named the branch; the "
            "commit is this checkout's",
}

# What a review is of. A member card's PR is reviewed on its way into its
# phase's branch; a phase card's PR is the integration head itself.
TARGETS = {
    "member": "one card's own pull request",
    "integration": "a phase's integration branch, which carries its "
                   "members' commits",
}

# The last thing the PR poll saw, per card: the head commit, the base and
# the pull request's state. In memory like every other registry here, and
# fed by the poll `github.py` already runs — so asking whether a pinned
# head is still current costs a dictionary lookup rather than a call to
# GitHub per card per state payload.
OBSERVED: dict[str, dict] = {}

# And the cost of answering about it. The board's own reading rides every
# SSE nudge, and a record is a file: without a memo this would be a read
# per review ever made, per nudge, for ever. Two things can move the
# answer and both are in the key — a record being written (the epoch) and
# the pull request's head moving under one (the observations) — so the
# cache is exact rather than merely short-lived. `verify.board` keeps one
# for the same reason.
_EPOCH = 0
_BOARD: tuple | None = None

_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _git(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """git in the **application** repository, which is the only repository
    a branch, a commit or a worktree here belongs to."""
    try:
        return subprocess.run(["git", "-C", str(config.app_repo()), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def _gh(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """`gh pr <verb> …` against **this board's** application repository.

    `--repo` for `github._gh_pr`'s reason: gh otherwise infers the
    repository from a checkout that may have several remotes, and a review
    must not resolve its head from a fork somebody added.
    """
    slug = config.gh_repo()
    head = [config.GH_BIN, "pr", args[0]]
    if slug:
        head += ["--repo", slug]
    try:
        return subprocess.run(head + list(args[1:]), cwd=str(config.app_repo()),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def pr_number(url: str | None) -> str | None:
    """The pull request's number, out of the url the card carries. None
    when the line is not a PR url — a card with prose where its `**PR:**`
    should be is a card nothing can be resolved for."""
    match = re.search(r"/pull/(\d+)", str(url or ""))
    return match.group(1) if match else None


# ── what the PR poll saw ───────────────────────────────────────────────


def observe(filename: str, *, head: str | None = None, base: str | None = None,
            status: str | None = None, source: str = "poll") -> dict:
    """Record the pull request's current head, as somebody read it.

    Called by the PR poll (`github._poll_pr`, every `BOARD_PR_POLL_INTERVAL`)
    and by `resolve_head` below. One authority for "what is the head now",
    so the freshness of a review is answered from the beat that already
    runs rather than from a gh call per card per read.
    """
    entry = OBSERVED.setdefault(filename, {})
    if head:
        entry["commit"] = head
    if base:
        entry["base"] = base
    if status:
        entry["state"] = status.upper()
    entry.update({"observed": time.time(), "source": source})
    return dict(entry)


def observed(filename: str) -> dict | None:
    entry = OBSERVED.get(filename)
    return dict(entry) if entry else None


def forget_observed(filename: str) -> None:
    OBSERVED.pop(filename, None)


# ── resolving what is being reviewed ───────────────────────────────────


def identity(filename: str, task: dict | None = None, *,
             fetch: bool = True) -> dict:
    """The pull request's repository, base and head — before anything is
    created.

    Read, fetch, answer. It adds no worktree, claims nothing and launches
    no model, so every refusal it reports costs one read and leaves the
    card exactly as it was. `problem` is the whole answer to "may a review
    be pinned"; empty means yes.

    `fetch=False` asks the same question without bringing the commit into
    this checkout — what **↻ act on PR** and the API need, where the
    answer wanted is the pull request's state rather than a checkout.
    """
    task = task or {}
    stem = filename[:-3] if filename.endswith(".md") else filename
    url = str(task.get("pr") or "").strip() or None
    found: dict = {
        "v": VERSION,
        "task": filename,
        "pr": url,
        "number": pr_number(url),
        "branch": config.branch_for(stem),
        "target": "integration" if task.get("isPhase") else "member",
        "repository": {"slug": config.gh_repo(),
                       "remote": config.git_remote(),
                       "authority": "github" if config.gh_repo() else "local"},
        "base": {"ref": None, "commit": None, "authority": None},
        "head": {"ref": None, "commit": None, "authority": None},
        "state": None, "merged": False, "closed": False, "historical": False,
        "crossRepository": False,
        "reviewId": None, "resolved": time.time(),
        "notes": [], "problem": None,
    }
    if not url:
        found["problem"] = (f"{filename} has no PR, so there is no head to "
                            f"review — open one (↑ open PR on the card) first")
        return found
    if not found["number"]:
        found["problem"] = (f"{filename}'s **PR:** line ({url}) is not a pull "
                            f"request url, so bench cannot ask which revision "
                            f"it proposes — fix the line on the card")
        return found

    _from_github(found)
    if not found["head"]["commit"]:
        _from_this_checkout(found)
    if not found["head"]["commit"]:
        found["problem"] = (
            f"bench cannot say which revision {filename}'s PR proposes: "
            f"{'; '.join(found['notes']) or 'GitHub could not be asked'}, and "
            f"this checkout has no branch {found['branch']}. A review runs in "
            f"a checkout of the reviewed head, so there is nothing to pin")
        return found
    if fetch and not _have(found["head"]["commit"]):
        _bring_it_here(found)
    if fetch and not _have(found["head"]["commit"]):
        found["problem"] = (
            f"the commit {found['head']['commit'][:12]} that {filename}'s PR "
            f"names as its head is not in this checkout and could not be "
            f"fetched: {'; '.join(found['notes'][-2:]) or 'no remote to fetch from'}. "
            f"Bench will not review a different revision in its place")
        return found
    _base_of(found, task)
    found["reviewId"] = profiles.review_id(filename, found["head"]["commit"])
    observe(filename, head=found["head"]["commit"],
            base=found["base"]["ref"], status=found["state"] or "",
            source="resolved")
    return found


# What a pull request has to say about itself. One call, and the fields are
# named rather than globbed: a gh too old to know one of them fails the
# whole read, and then the local fallback below says so out loud instead of
# a half-filled identity pretending to be GitHub's answer.
PR_FIELDS = ("number,state,url,baseRefName,baseRefOid,headRefName,"
             "headRefOid,mergedAt,isCrossRepository")


def _from_github(found: dict) -> None:
    if not config.gh_available():
        found["notes"].append(
            "gh is not installed, so the pull request's own head could not "
            "be read")
        return
    result = _gh("view", found["number"], "--json", PR_FIELDS)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        found["notes"].append(
            f"gh could not read PR #{found['number']}"
            + (f": {detail[-1][:140]}" if detail else ""))
        return
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        found["notes"].append(f"gh's answer about PR #{found['number']} did "
                              f"not parse as JSON")
        return
    if not isinstance(data, dict):
        return
    head = str(data.get("headRefOid") or "").strip().lower()
    status = str(data.get("state") or "").strip().upper()
    found["state"] = status or None
    found["merged"] = status == "MERGED" or bool(data.get("mergedAt"))
    found["closed"] = status == "CLOSED"
    found["historical"] = found["merged"] or found["closed"]
    found["crossRepository"] = bool(data.get("isCrossRepository"))
    found["base"]["ref"] = str(data.get("baseRefName") or "") or None
    base_oid = str(data.get("baseRefOid") or "").strip().lower()
    if _SHA.match(base_oid):
        found["base"]["commit"] = base_oid
        found["base"]["authority"] = "github"
    if _SHA.match(head):
        found["head"].update({
            "ref": str(data.get("headRefName") or "") or None,
            "commit": head, "authority": "github"})
    else:
        found["notes"].append(
            f"gh named no head commit for PR #{found['number']}")
    if found["crossRepository"]:
        found["notes"].append(
            "the PR's head is on a fork, so bench fetches it as "
            f"refs/pull/{found['number']}/head rather than as a branch")


def _from_this_checkout(found: dict) -> None:
    """The fallback, and it is a *labelled* one.

    `plans` refuses rather than falling back when a card names a base it
    cannot reach, because falling back there means a different branch. The
    fallback here is the same branch read from a different authority, so a
    board with no `gh` keeps working — and the record says `authority:
    "local"`, which is the sentence that stops a verdict reading as though
    GitHub's head had been checked.
    """
    branch = found["branch"]
    commit = _git("rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    if commit.returncode != 0 or not commit.stdout.strip():
        return
    found["head"].update({"ref": branch, "commit": commit.stdout.strip(),
                          "authority": "local"})
    found["notes"].append(
        f"the pull request's own head could not be read, so the tip of "
        f"{branch} in this checkout was pinned instead — the review is of "
        f"what is here, which is not necessarily what the PR shows")


def _have(commit: str) -> bool:
    return _git("cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def _bring_it_here(found: dict) -> None:
    """Fetch the reviewed commit, by the two refs that can name it.

    `refs/pull/<n>/head` is the one that works for a fork, and the branch
    name is the one that works for a remote with no pull refs (every
    test's local bare upstream). Bounded by `BOARD_REVIEW_FETCH_TIMEOUT`:
    unlike a branch point, there is no second-best revision to fall back
    to, so a slow remote is waited for rather than reviewed around.
    """
    remote = config.git_remote()
    if not remote:
        found["notes"].append(
            "this checkout has no remote, so the reviewed commit cannot be "
            "fetched")
        return
    refs = [f"refs/pull/{found['number']}/head"]
    if found["head"].get("ref"):
        refs.append(found["head"]["ref"])
    for ref in refs:
        result = _git("fetch", "--quiet", remote, ref,
                      timeout=config.REVIEW_FETCH_TIMEOUT)
        if result.returncode == 0 and _have(found["head"]["commit"]):
            found["notes"].append(f"fetched {ref} from {remote} to pin the "
                                  f"reviewed revision")
            return
    found["notes"].append(
        f"fetching {' and '.join(refs)} from {remote} did not bring "
        f"{found['head']['commit'][:12]} into this checkout")


def _base_of(found: dict, task: dict) -> None:
    """What the head is proposed *into*. GitHub's answer when there is
    one; otherwise the card's own recorded plan, which is the same answer
    `github._pr_base` opens the PR against."""
    if not found["base"]["ref"]:
        found["base"]["ref"] = plans.pr_base(found["task"], task or None)
        found["base"]["authority"] = "plan"
    if found["base"]["commit"]:
        return
    remote = config.git_remote()
    for ref in [found["base"]["ref"],
                f"{remote}/{found['base']['ref']}" if remote else None]:
        if not ref:
            continue
        commit = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if commit.returncode == 0 and commit.stdout.strip():
            found["base"]["commit"] = commit.stdout.strip()
            found["base"]["authority"] = found["base"]["authority"] or "local"
            return
    found["notes"].append(
        f"the base {found['base']['ref']} is not in this checkout, so the "
        f"review names it by branch rather than by commit")


def resolve_head(filename: str, task: dict | None = None) -> dict | None:
    """Ask the pull request what its head is *now*, authoritatively.

    One gh call, so it is for the moments that are worth one: the end of a
    review, before a verdict is attributed to the current head, and a
    person opening the review panel. Everything on a beat reads
    `observed()` instead.
    """
    found = identity(filename, task, fetch=False)
    if found["head"]["commit"]:
        return observe(filename, head=found["head"]["commit"],
                       base=found["base"]["ref"],
                       status=found["state"] or "", source="resolved")
    return observed(filename)


# ── the checkout, and who owns it ──────────────────────────────────────


def checkout_path(stem: str, commit: str) -> Path:
    """Where a review of this card at this commit is checked out.

    The commit is in the name, which is what makes two reviews of two
    heads two directories: a second review never lands on top of the
    first's, and a reused one is provably of the same revision.
    """
    return config.REVIEWS_DIR / "src" / f"{stem}-{(commit or 'unknown')[:12]}"


def record_path(agent_id: str) -> Path:
    return config.REVIEWS_DIR / f"{_slug(agent_id)}.json"


def _slug(agent_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(agent_id or "unknown"))


def _store(record: dict) -> dict:
    global _EPOCH
    with state.LOCK:
        _EPOCH += 1
    config.REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    record_path(record["agent"]).write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    return record


def owned(agent_id: str) -> dict | None:
    """One run's record, read back off disk."""
    try:
        data = json.loads(record_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def all_records() -> list[dict]:
    """Every review checkout this board has recorded, newest first."""
    found = []
    if not config.REVIEWS_DIR.is_dir():
        return found
    for path in config.REVIEWS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("agent"):
            found.append(data)
    found.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return found


def for_task(filename: str) -> list[dict]:
    return [r for r in all_records() if r.get("task") == filename]


def live_for_task(filename: str) -> dict | None:
    """The record of the review currently holding a checkout for this card,
    if one is. What a person's rerun of a `review` check runs in."""
    return next((r for r in for_task(filename) if not r.get("released")), None)


def pinned_path_for(filename: str) -> Path | None:
    """The checkout a review of this card is in, or would be put in — when
    bench can say so without asking GitHub anything.

    For the page's reading of a review profile. The preflight has to show
    the directory the run would work in, and it must not cost a call to
    GitHub per open: a checkout still held answers it exactly, and
    otherwise the head the PR poll last saw does. Neither resolves, pins
    or fetches, and when neither knows, the honest answer is None — bench
    has not pinned one, and the profile then shows no review root.
    """
    record = live_for_task(filename)
    if record and record.get("path"):
        return Path(str(record["path"]))
    head = (observed(filename) or {}).get("commit")
    if not head:
        return None
    stem = filename[:-3] if filename.endswith(".md") else filename
    return checkout_path(stem, head)


def provision(filename: str, stage: str, agent_id: str, found: dict) -> dict:
    """Add the run-owned checkout of the reviewed head, and record it.

    Refuses rather than repairs, every time. A directory that is a
    checkout of something else, one sitting at a different commit, and one
    another run owns are three different reasons and each is named: bench
    does not reset, switch or remove a checkout to make room for a review.
    """
    if found.get("problem"):
        raise ValueError(found["problem"])
    commit = found["head"]["commit"]
    stem = filename[:-3] if filename.endswith(".md") else filename
    path = checkout_path(stem, commit)

    escape = config.escapes(path, config.REVIEWS_DIR)
    if escape is not None:
        raise ValueError(
            f"the review checkout for {filename} would land at {escape}, "
            f"outside {config.REVIEWS_DIR} — a link on the way there points "
            f"out of the directory bench owns, and bench neither writes nor "
            f"removes through it")

    made = False
    note = None
    if path.exists():
        mismatch = config.worktree_mismatch(path)
        if mismatch:
            raise ValueError(mismatch)
        held = _held_by_another(path, agent_id, filename)
        if held:
            raise ValueError(held)
        head = _git_in(path, "rev-parse", "HEAD")
        at = head.stdout.strip() if head.returncode == 0 else ""
        if at != commit:
            where = at[:12] if at else "an unknown commit"
            raise ValueError(
                f"{path} already exists and is at {where} rather than the "
                f"reviewed {commit[:12]} — bench will not reset a checkout it "
                f"finds. Move it aside")
        note = (f"reusing bench's own checkout of {commit[:12]}, which is the "
                f"revision this review is of")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        added = _git("worktree", "add", "--detach", str(path), commit,
                     timeout=max(config.REVIEW_FETCH_TIMEOUT, 120))
        if added.returncode != 0:
            detail = (added.stderr or added.stdout).strip()
            raise ValueError(
                f"bench could not check {commit[:12]} out for the review of "
                f"{filename}"
                + (f": {detail.splitlines()[-1][:200]}" if detail else ""))
        made = True
        note = f"bench checked {commit[:12]} out for this review"

    record = {
        "v": VERSION,
        "agent": agent_id,
        "task": filename,
        "stage": stage,
        "intent": "review",
        "path": str(path),
        "made": made,
        "owned": made,
        "detached": True,
        "pid": os.getpid(),
        "created": time.time(),
        "pr": found["pr"], "number": found["number"],
        "repository": found["repository"],
        "branch": found["branch"],
        "base": found["base"], "head": found["head"],
        "state": found["state"], "merged": found["merged"],
        "closed": found["closed"], "historical": found["historical"],
        "crossRepository": found["crossRepository"],
        "target": found["target"],
        "reviewId": found["reviewId"],
        "notes": [*found["notes"], *([note] if note else [])],
        "location": "local",
        "released": None, "releasedAt": None, "kept": None, "removed": False,
    }
    return _store(record)


def _git_in(path: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(path), *args],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def _held_by_another(path: Path, agent_id: str, filename: str) -> str | None:
    """Why this directory is not this run's to use: a record says another
    run, or another card, is still holding it. Ownership is what is read,
    not the name of the directory — the whole point of recording it."""
    for record in all_records():
        if record.get("agent") == agent_id or record.get("released"):
            continue
        if Path(str(record.get("path") or "")) != path:
            continue
        if record.get("task") != filename:
            return (f"{path} is the review checkout recorded for "
                    f"{record.get('task')} (run {record.get('agent')}) — bench "
                    f"does not take over another card's checkout")
        return (f"{path} is still recorded as held by the review run "
                f"{record.get('agent')} on this card — hold that run, or let "
                f"it finish, before starting another")
    return None


def release(agent_id: str, reason: str = "the review ended") -> dict | None:
    """Give the checkout back. What bench made, bench removes — and
    nothing it did not, and nothing with work in it.

    Four guards in order, because each is a different way for a removal to
    be wrong: a record that says bench did not make the checkout, a path
    that resolves outside the reviews directory, a directory that is a
    checkout of something else, and uncommitted work in it. Removing a
    worktree with work in it is the one unrecoverable step anywhere near
    here, so it is asked before it is taken rather than left to git.
    """
    record = owned(agent_id)
    if record is None or record.get("released"):
        return record
    path = Path(str(record.get("path") or ""))
    record["released"] = reason
    record["releasedAt"] = time.time()
    if not record.get("made"):
        record["kept"] = ("bench did not make this checkout, so it was left "
                          "exactly as it was found")
        return _store(record)
    escape = config.escapes(path, config.REVIEWS_DIR)
    if escape is not None:
        record["kept"] = (f"{path} resolves to {escape}, outside "
                          f"{config.REVIEWS_DIR} — bench does not remove "
                          f"through a link out of the directory it owns")
        return _store(record)
    if not path.exists():
        record["removed"] = True
        record["kept"] = None
        return _store(record)
    mismatch = config.worktree_mismatch(path)
    if mismatch:
        record["kept"] = mismatch
        return _store(record)
    dirty = config.dirty_files(path)
    if dirty:
        record["kept"] = (
            f"{len(dirty)} uncommitted change{'' if len(dirty) == 1 else 's'} "
            f"in bench's own review checkout at {path}, so it was kept rather "
            f"than removed")
        return _store(record)
    removed = _git("worktree", "remove", str(path), timeout=60)
    if removed.returncode != 0:
        detail = (removed.stderr or removed.stdout).strip()
        record["kept"] = (
            f"git would not remove the review checkout at {path}"
            + (f": {detail.splitlines()[-1][:120]}" if detail else ""))
        return _store(record)
    record["removed"] = True
    return _store(record)


def release_for_task(filename: str, reason: str) -> list[dict]:
    """Every checkout still held for this card, given back. Ownership is
    per run, so this is a loop over records rather than a guess from a
    path."""
    given = []
    for record in for_task(filename):
        if record.get("released"):
            continue
        found = release(record["agent"], reason)
        if found:
            given.append(found)
    return given


def recover() -> list[dict]:
    """What a restarted board owes the checkouts it no longer owns.

    A record still unreleased, made by a process that is not this one, is
    from a board that died. Left alone it would read as a live review for
    ever and its directory would accumulate — so it is released, which
    removes the checkout only under every guard `release` applies. Nothing
    is re-run and no verdict is invented: whether to review again is a
    person's decision.
    """
    closed = []
    for record in all_records():
        if record.get("released") or record.get("pid") == os.getpid():
            continue
        found = release(record["agent"],
                        "this board stopped while the review was running")
        if found:
            closed.append(found)
    if closed:
        kept = [r for r in closed if r.get("kept")]
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": "",
            "summary": (f"{len(closed)} review checkout"
                        f"{'' if len(closed) == 1 else 's'} left by an earlier "
                        f"board released"
                        + (f" — {len(kept)} kept, with work in "
                           f"{'it' if len(kept) == 1 else 'them'}" if kept
                           else "") + "; nothing was reviewed again")})
    return closed


# ── is this review still about the current head? ───────────────────────


def freshness(record: dict) -> dict:
    """What has changed since this review was pinned.

    Compared, never resolved: this function moves nothing, re-reviews
    nothing and re-pins nothing. The binding is the head commit, because
    that is the only thing a verdict can be about — a checkout that has
    since been removed says nothing, since removing it is what the ending
    does on purpose.
    """
    if not record:
        return {"fresh": True, "changed": [], "caveats": [], "stale": False,
                "historical": False, "pinned": None, "head": None}
    pinned = ((record.get("head") or {}).get("commit") or "")
    now = observed(record.get("task") or "") or {}
    current = str(now.get("commit") or "")
    changed: list[str] = []
    status = str(now.get("state") or record.get("state") or "").upper()
    if current and pinned and current != pinned:
        changed.append(
            f"the PR's head has moved on — this review is of {pinned[:12]} "
            f"and the pull request is now at {current[:12]}, so what it found "
            f"is history rather than a verdict on the code there now")
    if status in ("MERGED", "CLOSED") and not record.get("historical"):
        changed.append(
            f"the pull request has been {status.lower()} since this review "
            f"ran, so it is a record of what was proposed rather than a "
            f"judgement of anything open")
    # Not a change, and kept apart from them for that reason: a review
    # pinned from the local branch has not been *superseded* by anything.
    # What it lacks is the binding, and a reader needs that said without
    # every review on a board without `gh` wearing the alarm for it.
    caveats: list[str] = []
    if (record.get("head") or {}).get("authority") == "local":
        caveats.append(
            "the reviewed head was read from this checkout's own branch "
            "rather than from the pull request, so it was never bound to "
            "what GitHub shows")
    return {
        "fresh": not changed,
        "changed": changed,
        "caveats": caveats,
        # `stale` is the reading a badge must take: a review whose head has
        # moved is never shown as the current head's approval.
        "stale": bool(changed),
        "historical": bool(record.get("historical")) or status in ("MERGED", "CLOSED"),
        "pinned": pinned or None,
        "head": current or None,
        "state": status or None,
        "observed": now.get("observed"),
    }


def label(record: dict) -> str:
    """One word for what this review is, for a badge that must not imply
    more: `current` only while the pinned head is still the PR's."""
    if not record:
        return "none"
    fresh = freshness(record)
    if fresh["historical"]:
        return "historical"
    if fresh["stale"]:
        return "superseded"
    return "current"


# ── what ↻ act on PR must not do ───────────────────────────────────────


def repair_problem(filename: str, task: dict | None = None) -> str | None:
    """Why acting on this PR's review is refused, or None.

    A merged or closed pull request is not a target a push repairs: the
    commits would land on a branch whose PR cannot carry them, nothing
    would reach the base, and the board would have said "acted on the PR"
    about a change that moved nothing. A phase member is the common case —
    its PR is merged into the phase branch early and stays merged — so the
    refusal names the integration head as the separate target it is rather
    than retargeting anything itself.
    """
    found = identity(filename, task, fetch=False)
    status = str(found.get("state") or "").upper()
    if status not in ("MERGED", "CLOSED"):
        return None
    base = (found["base"].get("ref")
            or plans.pr_base(filename, task or None))
    word = "merged" if status == "MERGED" else "closed"
    where = ("Validating or repairing what is in "
             f"{base} now is a different target: review the card that owns "
             f"{base} (a phase card's own PR), or open a card for the change."
             if found["target"] == "member" else
             f"Open a card for the change rather than pushing to a {word} "
             f"pull request.")
    return (f"{filename}'s PR ({found['pr']}) is {word}, so there is nothing "
            f"to act on: a push to {found['branch']} would not change that "
            f"pull request and would not reach {base}. {where} Nothing was "
            f"pushed")


# ── what leaves this module ────────────────────────────────────────────

# The API and the UI see exactly these keys, for `profiles.public`'s
# reason: the failure being prevented is a new key carrying something that
# should not travel. Nothing here holds a file's contents, an environment
# or a credential — paths, refs, commits and prose bench wrote.
PUBLIC = ("v", "agent", "task", "stage", "intent", "path", "made", "owned",
          "detached", "created", "pr", "number", "repository", "branch",
          "base", "head", "state", "merged", "closed", "historical",
          "crossRepository", "target", "reviewId", "notes", "location",
          "released", "releasedAt", "kept", "removed")


def public(record: dict | None) -> dict | None:
    if not record:
        return None
    out = {key: record[key] for key in PUBLIC if key in record}
    out["freshness"] = freshness(record)
    out["label"] = label(record)
    return out


def summary(record: dict | None) -> dict | None:
    """The compact reading a run row or a card chip wears: which revision,
    where, and whether it still speaks for the pull request."""
    if not record:
        return None
    fresh = freshness(record)
    return {
        "reviewId": record.get("reviewId"),
        "task": record.get("task"),
        "path": record.get("path"),
        "head": (record.get("head") or {}).get("commit"),
        "headAuthority": (record.get("head") or {}).get("authority"),
        "base": (record.get("base") or {}).get("ref"),
        "baseCommit": (record.get("base") or {}).get("commit"),
        "pr": record.get("pr"), "number": record.get("number"),
        "target": record.get("target"),
        "label": label(record),
        "historical": fresh["historical"],
        "stale": fresh["stale"],
        "owned": bool(record.get("made")),
        "released": record.get("released"),
    }


def board(collected: dict) -> dict:
    """The per-card reading the board view needs: the newest review this
    board pinned for each card, and whether it still speaks for the pull
    request.

    Only cards with a record appear, which is the law every other chip on
    this board follows — a board that has reviewed nothing gets an empty
    object and renders exactly what it always did.
    """
    global _BOARD
    files = frozenset(task["file"] for stage in collected.get("stages", [])
                      for task in stage["tasks"])
    with state.LOCK:
        stamp = (_EPOCH, frozenset(
            (name, seen.get("commit"), seen.get("state"))
            for name, seen in OBSERVED.items()))
    if _BOARD is not None and _BOARD[0] == stamp and _BOARD[1] == files:
        return _BOARD[2]
    found: dict = {}
    for record in all_records():          # newest first
        task = record.get("task") or ""
        if task not in files or task in found:
            continue
        reading = summary(record) or {}
        reading["checkout"] = record.get("path")
        reading["kept"] = record.get("kept")
        reading["live"] = not record.get("released")
        found[task] = reading
    _BOARD = (stamp, files, found)
    return found


def forget_cached() -> None:
    """Drop the memoised board reading. Nothing on the serving path calls
    it — the epoch and the observations do that work — but a test, and
    anything that replaces the reviews directory under the board's feet,
    needs the door."""
    global _BOARD
    _BOARD = None


def line(record: dict) -> str:
    """One pinned review, as the ticker tells it."""
    head = (record.get("head") or {}).get("commit") or "unknown"
    bits = [f"PR #{record.get('number')} at {head[:12]}"]
    if (record.get("head") or {}).get("authority") == "local":
        bits.append("head read from this checkout, not from GitHub")
    base = (record.get("base") or {}).get("ref")
    if base:
        bits.append(f"into {base}")
    if record.get("historical"):
        bits.append(f"{str(record.get('state') or 'closed').lower()} — a "
                    f"historical review")
    bits.append(f"in {record.get('path')}")
    return " · ".join(bits)


def prompt_block(record: dict | None) -> str:
    """What the review run is told about where it is and what it is of.

    The run chooses none of this and must not try: the checkout is bench's,
    detached on purpose, and the revision is the whole subject of the
    verdict. Prose persuades rather than enforces — the enforcement is that
    the process starts here and that `verify` runs the checks itself — but a
    run that is told plainly does not improvise a `git fetch`.
    """
    if not record:
        return ""
    head = (record.get("head") or {}).get("commit") or ""
    base = (record.get("base") or {}).get("ref") or "its base"
    lines = [
        f"The board has checked this pull request out for you. You are in "
        f"`{record.get('path')}`: a detached worktree of commit `{head}`, "
        f"which is the head PR #{record.get('number')} proposes into "
        f"`{base}`. It belongs to this run and bench removes it afterwards.",
        "",
        "Three things follow, and none of them is yours to change:",
        f"- Review **that commit**. Do not fetch, check out, pull, rebase or "
        f"switch anything: the revision is what the verdict is about, and a "
        f"review of a different one is a review of nothing. If the PR's head "
        f"has moved, say so in your report rather than following it.",
        "- This is not the application checkout and must not become one. "
        "Make no edits, no commits and no pushes here; the only thing this "
        "run publishes is its verdict.",
        "- Read the code around the diff from this checkout, which holds the "
        "proposed code exactly as the PR proposes it.",
    ]
    if (record.get("head") or {}).get("authority") == "local":
        lines.append(
            "- Bench could not read the pull request's own head, so this is "
            "the tip of the card's branch in the board's checkout. Say that "
            "in your report: it may not be what the PR shows.")
    if record.get("historical"):
        lines.append(
            f"- The pull request is "
            f"{str(record.get('state') or 'closed').lower()}. This is a "
            f"historical review of what it proposed, not a judgement of what "
            f"is in {base} now — do not describe it as validating {base}.")
    return "\n".join(lines)
