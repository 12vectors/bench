"""GitHub plumbing: opening PRs from agent worktree branches, requesting
Copilot reviews, and polling PR state for cards sitting in review/.

Every git and gh command here runs in `config.REPO` — **the application**
— against the remote `config.git_remote()` names and the base branch
`config.APP_BASE` names. Those are resolved independently of the work
state's own remote and branch (task 56): a code PR must never target the
work-state remote.

All of it is mechanical `git` + `gh` — no Claude involvement. The PR url is
written into the task file (`**PR:** <url>`), keeping the file the single
source of truth; only the volatile review/check state lives in memory.

With replicas watching one truth, *who* opens a PR matters: the trigger is
the actor's board (watch.py refuses to fire on a move a pull applied) and
the `**PR:**` line is the backstop behind it — carried by the file, so a
second attempt from anywhere finds the PR already there, and a `gh pr
create` that races anyway adopts the open PR instead of erroring. Polling
is read-only and every board does it.

Merge & clean up — the merge, its publication, the recovery record, the
cleanup and a phase's sweep — is `completion.py`'s (task 115, WP07). This
module observes the pull request and opens it; completion asks it through
the accessors below (`pr_state`, `forget_pr`, `pr_info`, `merge_pr`,
`merge_is_conditional`, `woven`) and never reaches into its registry.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

import config
import gates
import plans
import records
import reports
import reviews
import state
from taskfiles import (STATUS_RE, collect, commit_edit, find_stage_of,
                       read_task)

PR_STATE: dict[str, dict] = {}   # filename -> {verdict, detail, url, ts}
_OPENING: set[str] = set()       # filenames with a PR-open in flight


def pr_state(filename: str) -> dict | None:
    """What the poll last folded for this card's PR — the live entry, not a
    copy, so a reader sees the same registry the poll writes. Never a
    fetch."""
    return PR_STATE.get(filename)


def forget_pr(filename: str) -> None:
    """Stop carrying a card's PR reading: its work is merged and its branch
    is gone, so there is no PR left worth watching (completion asks this,
    never pops the registry itself)."""
    PR_STATE.pop(filename, None)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    if cmd and cmd[0] == "git":
        # A git read runs nothing else (config.git_argv); anything else —
        # a merge, a push, a worktree removal — is exactly as written.
        root, rest = None, list(cmd[1:])
        if rest[:1] == ["-C"] and len(rest) >= 2:
            root, rest = rest[1], rest[2:]
        where = str(cwd or config.REPO)
        return subprocess.run(config.git_argv(root, *rest), cwd=where,
                              env=config.git_env(root or where, *rest),
                              capture_output=True, text=True, timeout=timeout)
    return subprocess.run(cmd, cwd=str(cwd or config.REPO),
                          capture_output=True, text=True, timeout=timeout)


def _gh_pr(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """`gh pr <verb> …` against **this board's** application repository.

    gh otherwise infers the repository from the checkout's remotes, and
    with more than one — a fork, a backup, an upstream somebody added —
    that inference can act on a different repository than the one the
    board rides. `--repo` removes the guess, and goes right after the
    verb so the command still reads `gh pr create …`. A remote whose url
    is not an owner/name on a host (every test's local bare upstream)
    leaves gh to infer exactly as it did before."""
    slug = config.gh_repo()
    head = [config.GH_BIN, "pr", args[0]]
    if slug:
        head += ["--repo", slug]
    return _run(head + list(args[1:]), timeout=timeout)


def _api_repo() -> str:
    """The owner/repo for a `gh api` path. gh has no --repo there, so the
    slug goes into the path; `{owner}/{repo}` is gh's own placeholder
    expansion, the behaviour when there is nothing better to name."""
    return config.gh_repo() or "{owner}/{repo}"


def remote() -> str | None:
    """Where this board's work goes. Resolved in config, which sync asks
    too — the two halves of team mode must never name different remotes."""
    return config.git_remote()


def gh_available() -> bool:
    """One reading, in config, so what preflight promises and what this
    module then attempts can never disagree about `gh`."""
    return config.gh_available()


def _branch_exists(branch: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet", branch]).returncode == 0


def branch_of(filename: str) -> str:
    """The branch a card's work lives on. Ordinary cards get `task/<stem>`;
    a phase runs on `phase/<stem>`, its own integration branch (phases.py),
    and from review/ onwards it is reviewed, driven and completed through
    the same apparatus as any other card.

    `config.branch_for` is the one answer — drivers and local commands
    ask it too, so nothing can disagree about which branch a phase
    card's work is on."""
    return config.branch_for(filename[:-3])


def woven(filename: str, stage: str) -> dict:
    """The card as the board shows it. A card's phase is *derived* across
    the whole board (`taskfiles.weave_phases`), and both what a PR is based
    on and what a phase's PR says depend on that reading, so a lone
    `read_task` is not enough here."""
    for group in collect()["stages"]:
        for task in group["tasks"]:
            if task["file"] == filename:
                return task
    return read_task(config.TASKS / stage / filename, stage)


def _pr_base(task: dict) -> str:
    """What a PR is opened against — the card's recorded execution plan, or
    what its structured inputs say when no plan was ever recorded.

    The recorded plan is the load-bearing part: the automatic open on the
    move into `review/` and the explicit **↑ open PR** read the same
    answer, and an operator who changes the application default between
    launch and review does not retarget work already under way. `plans`
    holds the whole of that reasoning, the phase-member rule included — a
    member's branch was cut from its phase's branch, so that is the only
    base whose diff is the member's own work, and a phase branch this
    board knows only through the remote is still the base rather than the
    application default.
    """
    return plans.pr_base(task.get("file") or "", task)


def _task_location(stage: str = "review") -> str:
    """Where the card actually is, in words a reader of this PR can act on.

    A path only when the tasks genuinely live in this repository; when
    they are kept outside it — the point of an external workspace — the
    body says so rather than naming a `.task-manager/tasks/…` that does
    not exist here (task 56, issue requirement 6)."""
    rel = config.tasks_relative_to_app()
    if rel:
        return f"tracked in `{rel}/{stage}/`"
    return "tracked outside this repository, on the bench board"


def _pr_body(filename: str, task: dict) -> str:
    """The PR's body. A phase's PR is the one a whole run produces, so it
    says what is in it: the member list, in the order it ran."""
    where = _task_location()
    if task.get("isPhase"):
        cards = "\n".join(f"- {m['number']} — {m['title']}"
                          for m in task.get("members") or [])
        return (f"Phase: `{filename}` — {where}.\n\n"
                f"Opened by the board when every card in the phase had been "
                f"merged into its branch.\n\n## Cards in this phase\n\n"
                f"{cards or '_none_'}\n")
    body = (f"Task: `{filename}` — {where}.\n\n"
            f"Opened by the board when the card moved to review.")
    summary = _agent_report(filename)
    if summary:
        body += f"\n\n## Agent summary\n\n{summary}"
    return body


def _write_pr_line(filename: str, url: str) -> None:
    """The url joins the header — and in team mode commits itself, so the
    gate that stops a second board opening a second PR travels to the other
    boards rather than sitting in one working tree (where it would also
    stall sync, which never runs over uncommitted changes)."""
    stage = find_stage_of(filename)
    if not stage:
        return
    path = config.TASKS / stage / filename
    text = path.read_text(encoding="utf-8")
    if re.search(r"^\*\*PR:\*\*", text, re.MULTILINE):
        return
    if STATUS_RE.search(text):
        text = STATUS_RE.sub(lambda m: f"{m.group(0)}\n**PR:** {url}", text, count=1)
    else:
        text = f"**PR:** {url}\n\n" + text
    records.write_text(path, text, mkdir=False)
    commit_edit(filename, stage, "PR opened")


class _Quiet(ValueError):
    """A reason not worth the ticker: no branch, or a PR already open. The
    automatic path swallows these; the explicit action still shows them."""


def maybe_open_pr(filename: str) -> None:
    """Card entered review/ on *this* board — open a PR for its branch if
    one can be opened.

    Quiet when there is simply no branch (hand-written tasks) or a PR is
    already on the card; loud in the ticker when a PR *should* be possible
    but something stands in the way.
    """
    if filename in _OPENING:
        return
    _OPENING.add(filename)
    try:
        _open_pr(filename)
    except _Quiet:
        pass
    except ValueError as exc:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": str(exc)})
    finally:
        _OPENING.discard(filename)


def open_pr_now(filename: str) -> str:
    """The explicit action behind ↑ open PR: no board ever completes a
    half-done side effect on its own (the actor's board may have died
    between moving the card and opening the PR), so a person asks for it —
    and hears the reason when it cannot happen."""
    if filename in _OPENING:
        raise ValueError(f"a PR for {filename} is already being opened")
    _OPENING.add(filename)
    try:
        return _open_pr(filename)
    finally:
        _OPENING.discard(filename)


def _open_pr(filename: str) -> str:
    branch = branch_of(filename)
    if not _branch_exists(branch):
        # nothing to publish — a hand-moved card without agent work
        raise _Quiet(f"{filename} has no {branch} branch — nothing to open a PR from")
    stage = find_stage_of(filename)
    if stage != "review":
        raise _Quiet(f"{filename} is not in review/ — PRs open from there")
    task = woven(filename, stage)
    if task.get("pr"):
        raise _Quiet(f"{filename} already has a PR: {task['pr']}")

    rname = remote()
    if rname is None or not gh_available():
        raise ValueError(f"no PR for {filename}: " +
                         ("no git remote configured" if rname is None
                          else "gh is not installed"))

    base = _pr_base(task)
    gone = plans.target_problem(filename, base)
    if gone:
        raise ValueError(gone)
    # A PR GitHub already has, and what it is aimed at. A target that
    # disagrees with this card's own is reported, never retargeted and
    # never duplicated: one of the two readings is wrong, and which is a
    # person's call.
    open_already = _existing_pr_info(branch)
    if open_already and open_already.get("base") and open_already["base"] != base:
        raise ValueError(
            f"{filename} already has a PR into {open_already['base']} "
            f"({open_already['url']}), and its plan targets {base} — nothing was "
            f"opened or retargeted. Settle which base is right (⌖ replan, or "
            f"change the PR on GitHub)")
    if base == config.APP_BASE:
        # The PR's diff is computed against the remote base — refuse to open
        # one that would drag unpushed base commits along with it.
        _run(["git", "fetch", rname, base], timeout=120)
        ahead = _run(["git", "rev-list", "--count",
                      f"{rname}/{base}..{base}"]).stdout.strip()
        if ahead.isdigit() and int(ahead) > 0:
            raise ValueError(f"won't open a PR for {filename}: {base} is {ahead} "
                             f"commits ahead of {rname} — push {base} first, then "
                             f"move the card again")
    elif _branch_exists(base):
        # A phase branch is the board's own: publish it so the member's PR
        # has a base on the remote to be opened against. When only the
        # remote carries it — a member PR opened from a board that did not
        # run the phase — it is already there, and there is nothing local
        # to push.
        _run(["git", "push", "-u", rname, base], timeout=180)

    push = _run(["git", "push", "-u", rname, branch], timeout=180)
    if push.returncode != 0:
        raise ValueError(f"push failed for {branch}: {push.stderr.strip()[:140]}")

    body = _pr_body(filename, task)
    result = _gh_pr("create", "--head", branch, "--base", base,
                    "--title", task["title"], "--body", body, timeout=120)
    if result.returncode != 0:
        # The rare double-fire: two attempts crossed and GitHub already has
        # the PR. Adopt it — one PR still exists, and the card learns its
        # url. Anything else is a real failure.
        adopted = _existing_pr(branch) if _already_exists(result) else ""
        if not adopted:
            raise ValueError(f"PR creation failed for {branch}: "
                             f"{result.stderr.strip()[:140]}")
        _write_pr_line(filename, adopted)
        plans.remember_target(filename, branch, base)
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{filename}'s PR was already open — adopted it: {adopted}"})
        state.broadcast({"type": "board"})
        _poll_pr(filename, adopted)
        return adopted
    url = next((l.strip() for l in result.stdout.splitlines() if "/pull/" in l), result.stdout.strip())
    _write_pr_line(filename, url)
    # Work that reached GitHub without a plan gets one now, so the target
    # stops moving: the merge at the end reads this, not the setting.
    plans.remember_target(filename, branch, base)
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"PR opened for {filename} into {base}: {url}"})
    state.broadcast({"type": "board"})
    _poll_pr(filename, url)  # first CI/review snapshot without waiting a cycle
    return url


def _already_exists(result: subprocess.CompletedProcess) -> bool:
    return "already exists" in (result.stderr + result.stdout).lower()


def _existing_pr_info(branch: str) -> dict | None:
    """The PR GitHub already has for this branch: its url and the branch it
    targets. `None` when there is none, or when gh cannot say — a reading
    this cannot make is never read as agreement."""
    if not gh_available():
        return None
    found = _gh_pr("view", branch, "--json", "url,baseRefName", timeout=60)
    if found.returncode != 0:
        return None
    try:
        data = json.loads(found.stdout)
    except json.JSONDecodeError:
        return None
    url = str(data.get("url") or "")
    if "/pull/" not in url:
        return None
    return {"url": url, "base": str(data.get("baseRefName") or "")}


def _existing_pr(branch: str) -> str:
    """The url of the PR already open for this branch, if gh can name it."""
    info = _existing_pr_info(branch)
    return info["url"] if info else ""


def pr_info(branch: str) -> dict | None:
    """The owner's answer to "which PR is open for this branch, and into
    what": `{url, base}`, or None when there is none or gh cannot say."""
    return _existing_pr_info(branch)


def merge_pr(number: str, *condition: str,
             timeout: int = 180) -> subprocess.CompletedProcess:
    """Ask the host to merge pull request `number` with a merge commit.
    `condition` is passed through — `--match-head-commit <sha>` when a gate
    validated one. Whether the merge was *delivered* is completion's to
    read off the remote afterwards, never this exit status."""
    return _gh_pr("merge", number, "--merge", *condition, timeout=timeout)


def merge_is_conditional() -> bool:
    """Can this host's merge be made conditional on the head it is told?"""
    return _gh_merge_is_conditional()


def _agent_report(filename: str) -> str:
    """The work agent's closing report, clipped exactly as the task file
    clips it (reports.report — head first, one cap for both). The PR and
    the record must never tell different stories about the same run."""
    with state.LOCK:
        records = [r for r in state.AGENTS.values()
                   if r["task"] == filename and r.get("mode") == "work"]
    if not records:
        return ""
    latest = max(records, key=lambda r: r["started"])
    try:
        text = Path(latest["log"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return reports.report(text, log_path=latest.get("log"))


def request_copilot(filename: str) -> str:
    """Ask GitHub Copilot to review the task's PR. Works iff Copilot code
    review is enabled for the repo — otherwise gh returns an error we relay."""
    stage = find_stage_of(filename)
    if stage is None:
        raise ValueError(f"{filename} is not on the board")
    task = read_task(config.TASKS / stage / filename, stage)
    url = task.get("pr")
    if not url:
        raise ValueError(f"{filename} has no PR yet")
    number = url.rstrip("/").rsplit("/", 1)[-1]
    result = _run([config.GH_BIN, "api", "-X", "POST",
                   f"repos/{_api_repo()}/pulls/{number}/requested_reviewers",
                   "-f", "reviewers[]=copilot-pull-request-reviewer[bot]"], timeout=60)
    if result.returncode != 0:
        raise ValueError(f"Copilot said no: {result.stderr.strip()[:160]}")
    entry = PR_STATE.setdefault(filename, {"verdict": "pending", "ci": None,
                                           "url": url, "detail": "", "ts": 0})
    entry["copilot"] = "asked"
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"Copilot review requested on {filename}'s PR"})
    state.broadcast({"type": "board"})
    return url


def _check_state(check: dict) -> str:
    """One check-run or status-context → pass | fail | running."""
    conclusion = (check.get("conclusion") or check.get("state") or "").upper()
    status = (check.get("status") or "").upper()
    if conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED", "ERROR", "STARTUP_FAILURE"):
        return "fail"
    if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        return "pass"
    if status in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"):
        return "running"
    return "running"


def _is_copilot(login) -> bool:
    return "copilot" in str(login or "").lower()


def _conflict_state(data: dict, prev: dict) -> bool | None:
    """GitHub computes mergeability lazily: UNKNOWN means "not computed
    yet", never "fine" — keep the previous reading so the chip does not
    flap while GitHub thinks."""
    mergeable = str(data.get("mergeable") or "").upper()
    if mergeable == "CONFLICTING":
        return True
    if mergeable == "MERGEABLE":
        return False
    return prev.get("conflicts")


def _fold(data: dict, prev: dict, base: str | None = None) -> dict:
    """One gh pr-view payload + the previous snapshot → the new snapshot.
    Pure fold: fetching, events and broadcasts stay in _poll_pr.

    `base` is the card's own recorded PR target. The PR's actual
    `baseRefName` is folded against it, so a PR that is open against
    something other than what this card planned is a reading the board
    carries rather than a surprise at merge time. Omitted = the
    application default, which is what every card targeted before cards
    could name a target of their own.
    """
    reviews = data.get("reviews") or []
    checks = data.get("statusCheckRollup") or []
    changes = any(r.get("state") == "CHANGES_REQUESTED" for r in reviews)
    approved = any(r.get("state") == "APPROVED" for r in reviews)

    # Copilot: asked is a pending entry in reviewRequests; done is a review
    # authored by the copilot bot — the request entry disappears once it lands.
    cop_reviews = [r for r in reviews
                   if _is_copilot((r.get("author") or {}).get("login"))]
    cop_requested = any(
        _is_copilot(rr.get("login") or rr.get("slug") or rr.get("name"))
        for rr in (data.get("reviewRequests") or []))
    if cop_reviews:
        copilot = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}.get(
            cop_reviews[-1].get("state"), "commented")
    elif cop_requested:
        copilot = "asked"
    else:
        copilot = "asked" if prev.get("copilot") == "asked" else None

    states = [_check_state(c) for c in checks]
    ci = ("fail" if "fail" in states
          else "running" if "running" in states
          else "pass" if states else None)

    conflicts = _conflict_state(data, prev)
    want = base or config.APP_BASE
    actual = str(data.get("baseRefName") or "") or None
    mismatch = actual if (actual and actual != want) else None

    # A conflict is changes-needed-by-you, not a CI failure: it beats any
    # approval but leaves the CI chip telling its own story. A PR aimed at
    # the wrong branch is the same shape of fact: it needs a person, so it
    # beats an approval too.
    verdict = ("red" if (changes or ci == "fail" or conflicts or mismatch)
               else "green" if approved else "pending")
    # Which revision the PR currently proposes, and whether it is still
    # open. Not a verdict and not a chip of its own: it is what tells a
    # recorded review whether it still speaks for the pull request
    # (`reviews.freshness`), read off the poll that was already happening
    # rather than from a call per card.
    head = str(data.get("headRefOid") or "") or None
    status = str(data.get("state") or "").upper() or None
    detail_bits = []
    if reviews:
        detail_bits.append(f"{len(reviews)} review{'s' if len(reviews) > 1 else ''}")
    if ci:
        detail_bits.append({"fail": "checks failing", "running": "checks running",
                            "pass": "checks ok"}[ci])
    if conflicts:
        detail_bits.append(f"conflicts with {want}")
    if mismatch:
        detail_bits.append(f"open against {mismatch}, not {want}")
    if copilot:
        detail_bits.append("copilot " + {"asked": "asked", "approved": "approved",
                                         "changes": "asked for changes",
                                         "commented": "commented"}[copilot])
    return {"verdict": verdict, "ci": ci, "copilot": copilot,
            # the review objects alone, apart from CI, conflicts and the
            # base that `verdict` folds in — not bound to a commit here
            "review": ("changes" if changes else "approved" if approved
                       else None),
            "conflicts": conflicts, "base": actual, "wantBase": want,
            "baseMismatch": mismatch, "head": head, "prState": status,
            "merged": status == "MERGED" or bool(data.get("mergedAt")),
            "detail": " · ".join(detail_bits)}


def _review_objects(number: str) -> list[dict] | None:
    """GitHub's own review objects for one pull request, from the REST
    endpoint rather than from `gh pr view`.

    One field is the whole reason: `commit_id`, which says **which
    revision each review was of**. `gh pr view --json reviews` does not
    carry it, and an approval with no commit on it could only ever be
    bound to a head by guessing — so a gate that needed one would be
    guessing about the thing it exists to be sure of.

    Asked only when a configured gate actually requires a formal GitHub
    review state (`gates.needs_github_reviews`), so a board that gates
    nothing, or gates on bench's own verdict, pays exactly what it paid
    before this existed.
    """
    result = _run([config.GH_BIN, "api", "--paginate",
                   f"repos/{_api_repo()}/pulls/{number}/reviews"], timeout=60)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _poll_pr(filename: str, url: str) -> None:
    number = url.rstrip("/").rsplit("/", 1)[-1]
    result = _gh_pr("view", number,
                    "--json",
                    "reviews,reviewRequests,statusCheckRollup,state,mergeable"
                    ",baseRefName,headRefOid,mergedAt,author",
                    timeout=60)
    if result.returncode != 0:
        return
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return
    prev = PR_STATE.get(filename, {})
    entry = _fold(data, prev, plans.pr_base(filename))
    entry.update({"url": url, "ts": time.time()})
    PR_STATE[filename] = entry
    # Persisted at the poll's own update boundary, so a restart still knows
    # what GitHub said — a merge on GitHub included — and since when.
    _persist_snapshot(filename, entry)
    # One authority for "what is the head now", fed by this beat: a
    # recorded review asks `reviews` rather than GitHub, so the freshness
    # of every pinned review costs a dictionary lookup.
    if entry.get("head") or entry.get("prState"):
        reviews.observe(filename, head=entry.get("head"),
                        base=entry.get("base") or entry.get("wantBase"),
                        status=entry.get("prState") or "")
    # And what the *formal* review state is, bound to the commit each
    # review was given for — a separate reading, because a gate that asks
    # for a GitHub approval is asking about a record GitHub holds and not
    # about the folded verdict this beat computes for the chip.
    if gates.needs_github_reviews():
        objects = _review_objects(number)
        if objects is not None:
            gates.observe_reviews(
                filename, reviews=objects,
                author=str((data.get("author") or {}).get("login") or "")
                or None,
                head=entry.get("head"))
    if prev.get("baseMismatch") != entry["baseMismatch"] and entry["baseMismatch"]:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{filename}'s PR is open against {entry['baseMismatch']}, "
                       f"not {entry['wantBase']} — nothing was retargeted; "
                       f"settle which base is right before merging"})
        state.broadcast({"type": "board"})
    verdict, ci, copilot = entry["verdict"], entry["ci"], entry["copilot"]
    if prev.get("copilot") in (None, "asked") and copilot in ("approved", "changes", "commented"):
        word = {"approved": "approved it", "changes": "asked for changes",
                "commented": "commented"}[copilot]
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"Copilot reviewed {filename}'s PR and {word}"})
        state.broadcast({"type": "board"})
    if bool(prev.get("conflicts")) != bool(entry["conflicts"]):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{filename}'s PR conflicts with {entry['wantBase']} — "
                        f"↻ act on PR can attempt the resolution"
                        if entry["conflicts"] else
                        f"{filename}'s PR no longer conflicts with "
                        f"{entry['wantBase']}")})
        state.broadcast({"type": "board"})
    elif prev.get("verdict") != verdict and verdict != "pending":
        word = "approved" if verdict == "green" else "changes asked"
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"GitHub on {filename}'s PR: {word}"})
        state.broadcast({"type": "board"})
    elif prev.get("ci") != ci and ci in ("fail", "pass") and prev.get("ci") is not None:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"CI on {filename}'s PR: "
                       + ("all checks pass" if ci == "pass" else "checks failing")})
        state.broadcast({"type": "board"})


def poller() -> None:
    """Watch PRs of cards sitting in review/. Silent when there are none."""
    while True:
        time.sleep(config.PR_POLL_INTERVAL)
        if not gh_available():
            continue
        directory = config.TASKS / "review"
        if not directory.is_dir():
            continue
        try:
            for path in directory.glob("*.md"):
                task = read_task(path, "review")
                if task.get("pr"):
                    _poll_pr(task["file"], task["pr"])
        except OSError:
            continue


# ── what the poll saw, kept across a restart ───────────────────────────
#
# `PR_STATE` is memory, so a restarted board used to know nothing about a
# pull request until the next poll — including that it had been merged on
# GitHub. The poll now writes a small snapshot beside the gate records at
# the moment it updates its own state (never on a read), and the delivery
# status reads it back with the time it was observed, so "as of 14:02" is
# the honest age of what it says.

SNAPSHOT_KEYS = ("head", "prState", "merged", "base", "wantBase",
                 "baseMismatch", "ci", "verdict", "conflicts", "url")


def _snapshot_path(filename: str, kind: str = "pr") -> Path:
    return config.GATES_DIR / f"{filename[:-3]}.{kind}.json"


def _persist_snapshot(filename: str, entry: dict) -> None:
    """One bounded record per card, overwritten whole — only the fields a
    delivery reading needs, and the time they were observed."""
    stored = {key: entry.get(key) for key in SNAPSHOT_KEYS}
    stored.update({"v": 1, "task": filename,
                   "observed": entry.get("ts") or time.time()})
    path = _snapshot_path(filename)
    try:
        records.write_json(path, stored)
    except OSError:
        pass


def pr_snapshot(filename: str) -> dict | None:
    """What this board last observed of a card's pull request: from the
    poll's memory when it has run since the board started, else from the
    snapshot it persisted. `from` says which, `observed` says when. Reads
    only — nothing is fetched and GitHub is not asked."""
    live = PR_STATE.get(filename)
    if live and live.get("ts"):
        found = {key: live.get(key) for key in SNAPSHOT_KEYS}
        found.update({"observed": live.get("ts"), "from": "poll"})
        return found
    try:
        data = json.loads(_snapshot_path(filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    found = {key: data.get(key) for key in SNAPSHOT_KEYS}
    found.update({"observed": data.get("observed"), "from": "disk"})
    return found


def public_state() -> dict:
    return {f: {k: v.get(k) for k in ("verdict", "review", "ci", "copilot",
                                      "conflicts",
                                      "base", "wantBase", "baseMismatch",
                                      "head", "prState", "merged",
                                      "detail", "url")}
            for f, v in PR_STATE.items()}


def open_pr_async(filename: str) -> None:
    threading.Thread(target=maybe_open_pr, args=(filename,), daemon=True).start()


def _gh_merge_is_conditional() -> bool:
    """Can this gh make a merge conditional on the head it is told?"""
    found = _run([config.GH_BIN, "pr", "merge", "--help"], timeout=30)
    return "--match-head-commit" in (found.stdout + found.stderr)


def task_branches() -> list[str]:
    """Stems of all task/* and phase/* branches — the UI uses this to say
    honestly whether a card has work attached, and a phase card's work is
    its own integration branch."""
    result = _run(["git", "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/task/", "refs/heads/phase/"])
    stems = []
    for ref in result.stdout.split():
        for prefix in ("task/", "phase/"):
            if ref.startswith(prefix):
                stems.append(ref[len(prefix):])
    return stems


def reconcile() -> None:
    """Catch up on moves the watcher never saw (board was down): any card
    already sitting in review/ with a branch but no PR gets its PR opened
    now. Runs once at startup.

    Not in team mode. A replica cannot tell whose move it missed, so every
    board starting up would race to open the same PR — and the card that
    needs one wears the explicit ↑ open PR action instead, which is a
    person deciding rather than N boards guessing."""
    if config.SYNC:
        return
    time.sleep(3)  # let the server settle first
    directory = config.TASKS / "review"
    if not directory.is_dir():
        return
    branches = set(task_branches())
    for path in sorted(directory.glob("*.md")):
        try:
            task = read_task(path, "review")
        except OSError:
            continue
        if not task.get("pr") and path.stem in branches:
            maybe_open_pr(path.name)
