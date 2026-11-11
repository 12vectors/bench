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
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

import config
import drive as drive_mod
import gates
import plans
import reports
import reviews
import state
from taskfiles import (STATUS_RE, collect, commit_edit, find_stage_of,
                       member_entry, move_task, move_together, read_task)

PR_STATE: dict[str, dict] = {}   # filename -> {verdict, detail, url, ts}
_OPENING: set[str] = set()       # filenames with a PR-open in flight


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


def _contains(branch: str, tip: str) -> bool:
    """Is `branch` already in `tip`? The same question phases.py asks of a
    member — a merge leaves no record but this one, and it is the only
    thing that can say a phase's ending speaks for a particular card."""
    return _run(["git", "merge-base", "--is-ancestor", branch, tip]).returncode == 0


def branch_of(filename: str) -> str:
    """The branch a card's work lives on. Ordinary cards get `task/<stem>`;
    a phase runs on `phase/<stem>`, its own integration branch (phases.py),
    and from review/ onwards it is reviewed, driven and completed through
    the same apparatus as any other card.

    `config.branch_for` is the one answer — drivers and local commands
    ask it too, so nothing can disagree about which branch a phase
    card's work is on."""
    return config.branch_for(filename[:-3])


def _woven(filename: str, stage: str) -> dict:
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
    path.write_text(text, encoding="utf-8")
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
    task = _woven(filename, stage)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_suffix(".json.tmp")
        scratch.write_text(json.dumps(stored, indent=2, sort_keys=True),
                           encoding="utf-8")
        scratch.replace(path)
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


def delivered_record(filename: str) -> dict | None:
    """The completion this board finished for a card: what it merged, into
    which target, and when. Written once the ticket has moved, so a card
    reopened afterwards can say its earlier work was delivered."""
    try:
        data = json.loads(_snapshot_path(filename, "delivered")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("commit") else None


def _record_delivered(filename: str, commit: str | None, target: str,
                      how: str) -> None:
    if not commit:
        return
    path = _snapshot_path(filename, "delivered")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "task": filename,
                                    "commit": commit, "target": target,
                                    "how": how, "at": time.time()},
                                   indent=2, sort_keys=True),
                        encoding="utf-8")
    except OSError:
        pass


def public_state() -> dict:
    return {f: {k: v.get(k) for k in ("verdict", "review", "ci", "copilot",
                                      "conflicts",
                                      "base", "wantBase", "baseMismatch",
                                      "head", "prState", "merged",
                                      "detail", "url")}
            for f, v in PR_STATE.items()}


def open_pr_async(filename: str) -> None:
    threading.Thread(target=maybe_open_pr, args=(filename,), daemon=True).start()


def complete_task(filename: str, stage: str) -> dict:
    """The user chose "merge & clean up" on a move to done: park the drive
    if it is this task's, merge the branch, remove the worktree and
    branches, then move the card. Every step narrates; a conflict aborts
    cleanly and the card stays.

    Where the merge happens is `config.app_merge_on_origin()` and nothing
    else — never ticket sync read on its own. The local merge merges into
    the application's base in this checkout and pushes it; the merge on
    origin is `gh pr merge`, so local base only ever fast-forwards to it.

    Merging, publishing, cleaning up and moving the ticket are four
    outcomes, not one. Nothing is cleaned up and the card does not move
    until the application remote is *seen* to hold the merged commit (or
    there is no remote, which is a local-only completion and says so). A
    merge whose publication failed or could not be confirmed is recorded
    (`recovery`), so the retry — after a restart too — finishes it from
    what the remote actually holds rather than merging again.

    None of it is quick, and all of it is destructive, so the card is
    claimed before the first step and given back in a `finally` after the
    last one. The claim is what the card wears while this runs and what
    refuses a second request; see `state.claim_completing`.
    """
    if stage not in config.STAGE_DIRS or stage == "done":
        raise ValueError("complete runs on a live-stage card")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")

    if not state.claim_completing(filename, "merging and cleaning up…"):
        raise ValueError(f"{filename} is already being completed — the card "
                         f"is showing each step; nothing was started twice")
    try:
        return _complete(filename, stage)
    finally:
        # every exit: merged, conflicted, on the wrong branch, or crashed
        state.release_completing(filename)


def _complete(filename: str, stage: str) -> dict:
    """The steps themselves, run under the claim complete_task holds.

    Four outcomes, told apart: the merge into the base, its publication on
    the application remote, the cleanup of the card's workspace, and the
    ticket's move. Each later one waits for the one before it, and the
    result says which of them happened.
    """
    stem = filename[:-3]
    branch = branch_of(filename)   # a phase's own branch, or task/<stem>
    task = _woven(filename, stage)
    # A completion that merged and then could not publish left a record. A
    # retry finishes *that* — the commit it merged, into the target it
    # merged into, the way it merged — or refuses when the situation has
    # changed under it, rather than quietly finishing something else.
    pending = recovery(filename)
    on_origin = False
    resuming = False
    if pending:
        on_origin = config.app_merge_on_origin()
        _assert_same_situation(filename, task, pending, on_origin)
        resuming = _merge_landed(pending, on_origin)
    if not resuming:
        # A pull request already merged on GitHub, outside this board: the
        # gate was about a merge this board would make, and there is none
        # to make. Finishing it is administrative — publication is read
        # off the remote, the base catches up and the workspace is
        # cleared — so obsolete checks, reviews and sign-off are not asked
        # for. Confirmed against the remote itself, never the snapshot
        # alone.
        landed = _merged_elsewhere(filename, task, branch)
        if landed:
            pending = {"commit": landed,
                       "target": plans.pr_base(filename, task),
                       "mode": "origin"}
            on_origin = True
            resuming = True
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"{filename}'s PR was merged on GitHub at "
                            f"{landed[:12]} — reconciling: nothing is merged "
                            f"again and no merge rule is asked for")})
    # Before the drive is parked, and well before anything irreversible:
    # what this board's acceptance gate requires, and whether the
    # evidence on record satisfies it against the commit this would
    # merge. A refusal here leaves the card, its branch, its worktree and
    # every record exactly as they are, and says the same sentence the
    # page says — one reading, one authority. A retry whose merge already
    # landed is past the gate: the gate was about the merge, which was of
    # the commit it read, and the merge cannot be taken back.
    scope = gates.scope_for(task)
    if not resuming:
        gates.require(gates.evaluate(filename, scope, task=task),
                      "merge & clean up")
    # Read before anything is destroyed and acted on only after the merge
    # lands: the phase branch is deleted in the middle of this, and it is
    # the only thing that can say which members it carried.
    brought = _brought(task, branch) if task.get("isPhase") else []

    # 1. the app must not keep running code that is about to be merged away
    d = drive_mod.DRIVE
    if d and d.get("task") == filename and d.get("status") in ("starting", "up"):
        drive_mod.stop()
        for _ in range(40):
            if not drive_mod._alive(d):
                break
            time.sleep(0.5)

    merged = False
    swept: list[dict] = []
    held: list[dict] = []
    unmerged: dict | None = None
    publication: dict | None = None
    cleanup: dict | None = None
    if _branch_exists(branch) or pending:
        # The same target the whole card has used, validated once more
        # before the one irreversible step: a target that has disappeared
        # is a changed situation rather than a licence to pick a new one,
        # and a card stacked on another branch is not this action's to
        # finish at all.
        target = plans.pr_base(filename, task)
        gone = plans.target_problem(filename, target)
        if gone:
            raise ValueError(gone)
        _assert_lands_in_the_base(filename, target)
        if resuming:
            pinned = pending["commit"]
            merged_tip = pinned
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"retrying the publication of {target} — "
                            f"{pinned[:12]} is already merged "
                            + ("on origin" if on_origin else "in this checkout")
                            + "; nothing is merged again")})
        else:
            # And again, immediately before the one irreversible step. The
            # first reading was before the drive was parked, which takes as
            # long as it takes; a push landing in between is a different
            # commit, and this is the read that is actually about the
            # commit the merge will take.
            decision = gates.evaluate(filename, scope, task=task)
            gates.require(decision, "the merge")
            # Reading twice narrows the window; it does not close it. So
            # the commit this reading validated is the one the merge takes
            # — the local merge by SHA, origin's with `--match-head-commit`
            # — and a branch that moves after this line is a refusal or a
            # kept branch, never an unverified commit in the base.
            pinned = _pinned_head(decision, branch)
            on_origin = config.app_merge_on_origin()
            merged_tip = pinned or _tip(branch)
            # (an ungated board pins nothing, and calls them exactly as before)
            pin = (pinned,) if pinned else ()
            if on_origin:
                _merge_on_origin(filename, stage, branch, *pin)
            else:
                _merge_locally(filename, branch, *pin)
            # Written the moment the merge has landed and before anything
            # is published: a board that dies from here on comes back
            # knowing what it merged, and a retry reconciles from that.
            _remember(filename, {
                "branch": branch, "target": target,
                "mode": "origin" if on_origin else "local",
                "remote": remote() or "", "commit": merged_tip,
                "publication": "pending", "detail": ""})
        merged = True

        publication = _publish(filename, target, merged_tip, on_origin)
        if publication["state"] not in ("published", "local-only"):
            # Merged, and not delivered. Nothing below runs: the branch,
            # the worktree, the remote branch and the phase's members are
            # exactly what a retry, or a person, needs to finish it.
            _remember(filename, {
                "branch": branch, "target": target,
                "mode": "origin" if on_origin else "local",
                "remote": publication["remote"], "commit": merged_tip,
                "publication": publication["state"],
                "detail": publication["detail"],
                "diverged": publication.get("diverged", False)})
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": _held_sentence(filename, stage, publication,
                                          on_origin)})
            state.broadcast({"type": "board"})
            return {"merged": True, "published": False,
                    "publication": publication, "moved": False,
                    "stage": stage, "swept": [],
                    "recovery": recovery_public(filename)}
        if on_origin:
            _catch_up_base(filename)

        # Nothing uncommitted is thrown away — the same rule a merged
        # phase member's workspace is cleared under. The work is in the
        # base branch by now, so nothing in there is *needed*; that is a
        # judgement about committed files, and an uncommitted change is
        # exactly what it does not cover. Nor is a commit made after the
        # gate read the head: the branch holding it is kept.
        cleanup, unmerged = _clean_up(filename, branch, pinned, merged_tip,
                                      on_origin)
        if not unmerged:
            # a branch with unmerged commits still has a PR worth watching
            PR_STATE.pop(filename, None)

        # the merge is published, so the members' work is in the base too
        swept, held = _sweep(filename, task, brought, merged_tip)

    result = {"merged": merged, "swept": [card["file"] for card in swept]}
    if publication:
        result["published"] = publication["state"] == "published"
        result["publication"] = publication
    if cleanup:
        result["cleanup"] = cleanup
    if held:
        result["kept"] = [card["file"] for card in held]
    if unmerged:
        # `done/` means the card's work is in the base, and its newest
        # commits are not: the gated commit merged, the card stays where
        # it was, and the completion says it was partial rather than
        # leaving `merged` to be read as accepted.
        forget_recovery(filename)
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{filename} partly completed — merged "
                        f"{unmerged['merged'][:12]}, the commit the gate read; "
                        f"{unmerged['branch']} has moved on to "
                        f"{unmerged['tip'][:12]}, which is not merged, so the "
                        f"card stays in {stage}/")})
        state.broadcast({"type": "board"})
        result.update({"partial": True, "stage": stage, "unmerged": unmerged,
                       "moved": False})
        return result
    move_task(filename, stage, "done", actor="you")
    if merged and publication:
        _record_delivered(filename, publication.get("commit"),
                          publication.get("target") or "",
                          "origin" if on_origin else "local")
    # Only once the ticket has moved: until then a retry needs to know
    # what was merged, and a cleanup that stopped half way is finished by
    # the same retry rather than by guessing.
    forget_recovery(filename)
    result["moved"] = True
    state.broadcast({"type": "board"})
    return result


# ── a completion that merged and has not published yet ──────────────────


def _recovery_dir() -> Path:
    """Beside the recorded plans unless configured: the record finishes
    the work a plan started, and is machine-local for the same reason."""
    return config.COMPLETIONS_DIR or config.PLANS_DIR.parent / "completions"


def _recovery_path(filename: str) -> Path:
    return _recovery_dir() / f"{filename[:-3]}.json"


def recovery(filename: str) -> dict | None:
    """What an unfinished completion of this card merged, and where its
    publication stands — or None. Machine-local (`local/state/`), like the
    plan it sits beside: it is this board's reading of its own checkout and
    its remote, and nothing in it belongs in a ticket somebody pulls."""
    try:
        data = json.loads(_recovery_path(filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("commit"):
        return None
    return data


def _remember(filename: str, record: dict) -> None:
    """Write the record whole or not at all: a temporary file, then
    `os.replace`, so a board killed mid-write leaves the previous one."""
    _recovery_dir().mkdir(parents=True, exist_ok=True)
    stored = dict(record, task=filename, at=time.time())
    path = _recovery_path(filename)
    scratch = path.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(stored, indent=2, sort_keys=True),
                       encoding="utf-8")
    scratch.replace(path)


def forget_recovery(filename: str) -> bool:
    """Drop the record — the completion finished, or a person decided what
    the unfinished one means. It touches no git: nothing is merged, pushed,
    reset or removed by forgetting."""
    try:
        _recovery_path(filename).unlink()
        return True
    except OSError:
        return False


_RECOVERY_KEYS = ("branch", "target", "mode", "remote", "commit",
                  "publication", "detail", "diverged", "at")


def recovery_public(filename: str) -> dict | None:
    found = recovery(filename)
    return {key: found.get(key) for key in _RECOVERY_KEYS} if found else None


def recoveries() -> dict:
    """Every card with an unfinished completion, for the board's state —
    a directory listing, and empty on a board where nothing failed."""
    directory = _recovery_dir()
    if not directory.is_dir():
        return {}
    found = {}
    for path in sorted(directory.glob("*.json")):
        name = f"{path.stem}.md"
        public = recovery_public(name)
        if public:
            found[name] = public
    return found


def _assert_same_situation(filename: str, task: dict, pending: dict,
                           on_origin: bool) -> None:
    """A retry finishes the completion that was started, or nothing.

    The record names the target the merge went into and the way it went.
    If either has changed since — a replan, an edited `BOARD_APP_MERGE` —
    finishing it now would publish one thing and clean up after another,
    so it is refused and a person decides which reading is right."""
    target = plans.pr_base(filename, task)
    mode = "origin" if on_origin else "local"
    was = pending.get("target") or target
    how = pending.get("mode") or mode
    if was == target and how == mode:
        return
    changed = (f"it now targets {target}" if was != target
               else f"this board now merges {'on origin' if on_origin else 'locally'}")
    raise ValueError(
        f"{filename} was merged into {was} ({how}) at "
        f"{str(pending.get('commit'))[:12]} and its publication is unfinished, "
        f"but {changed}. Nothing was done: publish {was} yourself, or discard "
        f"the unfinished-completion record from the merge sheet once you have "
        f"decided which is right")


def _merge_landed(pending: dict, on_origin: bool) -> bool:
    """Is the merge a record describes already in place? Locally that is
    the checkout's own base; on origin it is what the remote holds."""
    commit, target = pending.get("commit") or "", pending.get("target") or ""
    if not commit or not target:
        return False
    if not on_origin:
        return bool(_tip(commit)) and _contains(commit, target)
    rname = remote()
    return bool(rname) and _remote_holds(rname, target, commit)[0] is True


def _remote_holds(rname: str, target: str, commit: str) -> tuple:
    """Does `target` on the application remote contain `commit`?

    Read from the remote itself — `ls-remote`, then a fetch of that exact
    branch when its tip is not here yet — never from a remote-tracking
    ref, which says what the remote held when somebody last looked.
    Returns (answer, the remote's tip, why not): the answer is None when
    the remote could not be read at all, which is *not* a no.
    """
    listed = _run(["git", "ls-remote", rname, f"refs/heads/{target}"],
                  timeout=60)
    if listed.returncode != 0:
        why = ((listed.stderr.strip() or "git gave no detail")
               .splitlines() or [""])[-1]
        return None, "", f"{rname} could not be read ({why[:120]})"
    words = listed.stdout.split()
    if not words:
        return False, "", f"{rname} has no {target} branch"
    tip = words[0]
    if tip == commit:
        return True, tip, ""
    if not _tip(tip):
        _run(["git", "fetch", "--no-tags", rname, f"refs/heads/{target}"],
             timeout=120)
    if not _tip(tip):
        return None, tip, (f"{rname}/{target} is at {tip[:12]}, which could not "
                           f"be fetched to compare")
    if _contains(commit, tip):
        return True, tip, ""
    return False, tip, f"{rname}/{target} is at {tip[:12]}, which does not contain it"


def _publish(filename: str, target: str, commit: str, on_origin: bool) -> dict:
    """Deliver the merge, and believe only the remote about whether it was.

    The local merge pushes the base; the merge on origin already happened
    there. Either way the answer is what the application remote — the one
    `config.git_remote()` names, never the work state's — holds afterwards:
    a push that reported failure but landed (a lost reply) is published,
    and one that reported success while the remote does not hold the
    commit is not. No remote at all is a local-only completion, said in
    those words rather than dressed as a publication.
    """
    rname = remote()
    out = {"mode": "origin" if on_origin else "local", "remote": rname or "",
           "target": target, "commit": commit, "state": "", "detail": "",
           "remoteTip": "", "diverged": False}
    if not rname:
        if on_origin:
            out.update(state="unconfirmed",
                       detail="this application has no remote to confirm "
                              "the merge against")
        else:
            out.update(state="local-only",
                       detail="this application has no remote, so nothing "
                              "was published")
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"{target} is local-only — no application remote "
                            f"is configured, so nothing was pushed or merged "
                            f"on GitHub")})
        return out
    pushed = None
    if not on_origin:
        pushed = _run(["git", "push", rname,
                       f"refs/heads/{target}:refs/heads/{target}"], timeout=180)
    holds, tip, why = _remote_holds(rname, target, commit)
    out["remoteTip"] = tip
    refused = ""
    if pushed is not None and pushed.returncode != 0:
        lines = [line.strip() for line in (pushed.stderr.strip()
                 or pushed.stdout.strip() or "git gave no detail").splitlines()]
        # what the remote itself said (a protection rule, a hook) and the
        # rejection line, ahead of git's own boilerplate
        said = [line for line in lines if line.startswith(("remote:", "!"))]
        refused = " ".join(said or lines[-3:])[:240]
    if holds is True:
        out["state"] = "published"
        if refused:
            said = (f"the push of {target} reported a failure, but {rname}/"
                    f"{target} holds {commit[:12]} — it landed and the reply "
                    f"was lost ({refused[:100]})")
        elif on_origin:
            said = f"confirmed: {rname}/{target} holds {commit[:12]}"
        else:
            said = (f"pushed {target} to {rname} — {rname}/{target} holds "
                    f"{commit[:12]} (PR marked merged)")
        state.record_board_event({"kind": "agent", "actor": "board",
                                  "file": filename, "summary": said})
        return out
    out.update(state="failed" if refused else "unconfirmed",
               detail=" · ".join(part for part in (refused, why) if part))
    if holds is False and tip and _tip(tip) and not _contains(tip, target):
        # the remote has commits this checkout's base does not: a push
        # cannot fast-forward it, and nothing here will force it
        out["diverged"] = True
    return out


def _held_sentence(filename: str, stage: str, publication: dict,
                   on_origin: bool) -> str:
    """What the ticker says about a merge that did not publish: what
    already happened, what did not, and what a person does next."""
    target, rname = publication["target"], publication["remote"] or "the remote"
    commit = publication["commit"][:12]
    where = ("GitHub reported the merge" if on_origin
             else f"merged into {target} in this checkout")
    what = ("but the push failed" if publication["state"] == "failed"
            else "but its publication could not be confirmed")
    sentence = (f"{filename}: {where} at {commit}, {what} — {rname}/{target} "
                f"does not hold it ({publication['detail'][:160]}). Nothing "
                f"was cleaned up and the card stays in {stage}/")
    if publication.get("diverged"):
        sentence += (f"; {rname}/{target} has commits this checkout does not — "
                     f"bring them into {target} (pull, then merge) and retry")
    else:
        sentence += "; resolve it, then merge & clean up again"
    return sentence


def _clean_up(filename: str, branch: str, pinned: str | None, merged: str,
              on_origin: bool) -> tuple[dict, dict | None]:
    """The card's own workspace, after its merge is published. Returns what
    became of each part — never "removed" for something git kept — and the
    unmerged tip when the branch moved past the commit the gate read."""
    stem = filename[:-3]
    worktree = config.WORKTREES / stem
    out = {"worktree": "absent", "branch": "absent", "remoteBranch": "none",
           "notes": []}
    unmerged = None
    kept = ""
    now = _tip(branch)
    if pinned and now and now != pinned:
        kept = (f"{branch} has moved on to {now[:12]} since the gate read "
                f"{pinned[:12]}, and those commits were not merged")
        # What merged is a historical head; the card's newest work is
        # not in the base, so the card is not Done.
        unmerged = {"branch": branch, "merged": pinned, "tip": now}
        if worktree.exists():
            out["worktree"] = "kept"
    elif worktree.exists():
        out["worktree"] = "kept"
        dirty = config.dirty_files(worktree)
        if dirty:
            kept = (f"{len(dirty)} uncommitted change"
                    f"{'' if len(dirty) == 1 else 's'} in it")
        else:
            removed = _run(["git", "worktree", "remove", str(worktree)])
            if removed.returncode != 0:
                kept = ((removed.stderr.strip() or removed.stdout.strip()
                         or "git would not remove it").splitlines()[-1][:120])
            else:
                out["worktree"] = "removed"
    if kept:
        out["notes"].append(kept)
        if now:
            out["branch"] = "kept"
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"worktree for {stem} kept — {kept}; the branch "
                       f"is kept with it"})
    elif now:
        # -D when origin made the merge: the base here has not caught up
        # yet, so the safe delete would refuse something landed.
        deleted = _run(["git", "branch", "-D" if on_origin else "-d", branch])
        if deleted.returncode == 0:
            out["branch"] = "deleted"
            # The plan described work that is now in the base, on a branch
            # that has just gone. A workspace *kept* keeps its plan,
            # because the branch it names is still the one holding the work.
            plans.forget(filename)
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"cleaned up: worktree and local branch for {stem} "
                            f"removed" if out["worktree"] == "removed" else
                            f"cleaned up: local branch {branch} removed")})
        else:
            out["branch"] = "kept"
            why = ((deleted.stderr.strip() or "git would not delete it")
                   .splitlines() or [""])[-1][:120]
            out["notes"].append(f"local branch kept — {why}")
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": f"{branch} kept — git would not delete it ({why})"})
    else:
        plans.forget(filename)        # a retry after the branch already went
    rname = remote()
    if rname:
        if _delete_remote_branch(rname, branch, merged, filename):
            out["remoteBranch"] = "gone"
        else:
            out["remoteBranch"] = "kept"
    return out, unmerged


def completion_facts(filename: str, stage: str) -> dict:
    """What **merge & clean up** would do for this card, read from the same
    authorities the completion itself reads — the merge mode from
    `config.app_merge_on_origin()`, the target from the recorded plan, the
    stacked-card refusal from `_assert_lands_in_the_base`, the remote from
    `config.git_remote()` — so the sheet describes the completion that
    would run, not one inferred from ticket sync. Read-only: no fetch, no
    `gh` call, no write."""
    task = _woven(filename, stage)
    target = plans.pr_base(filename, task)
    saved = plans.recorded(filename)
    on_origin = config.app_merge_on_origin()
    rname = remote()
    facts = {
        "file": filename, "branch": branch_of(filename),
        "mode": "origin" if on_origin else "local",
        "target": target,
        "appBase": (saved or {}).get("appBase") or config.APP_BASE,
        "remote": rname or "",
        "publication": ("pr-merge" if on_origin
                        else "push" if rname else "none"),
        "ticketSync": bool(config.SYNC),
        "ticketsSeparate": not config._one_repository(),
        "pr": task.get("pr") or "",
        "stacked": False, "refusal": "",
        "recovery": recovery_public(filename),
    }
    refusal = plans.target_problem(filename, target)
    if not refusal:
        try:
            _assert_lands_in_the_base(filename, target)
        except ValueError as exc:
            facts["stacked"] = True
            refusal = str(exc)
    if not refusal and on_origin and not facts["pr"] and not facts["recovery"]:
        refusal = (f"{filename} has no PR, and this board merges the "
                   f"application on origin — open one first (↑ open PR)")
    if not refusal and on_origin and not gh_available():
        refusal = ("gh is not installed, and this board merges the "
                   "application on origin (BOARD_APP_MERGE)")
    facts["refusal"] = refusal or ""
    return facts


def _merged_elsewhere(filename: str, task: dict, branch: str) -> str | None:
    """The commit a pull request merged on GitHub delivered, or None.

    Asked only when the poll's snapshot says the PR was merged, and then
    of the remote itself (`_remote_holds`, bounded): the snapshot is what
    makes the question worth one remote read, and the remote is what
    answers it."""
    snap = pr_snapshot(filename)
    if not snap or not snap.get("merged"):
        return None
    rname = remote()
    tip = _tip(branch) or str(snap.get("head") or "")
    if not rname or not tip:
        return None
    target = plans.pr_base(filename, task)
    return tip if _remote_holds(rname, target, tip)[0] is True else None


def _brought(task: dict, phase_branch: str) -> list[dict]:
    """The members a phase actually merged — the only cards its ending
    speaks for.

    Two things have to be true, and they are the same pair phases.py reads
    a member as `merged` by: the card settled into `review/` or `done/`,
    and its branch is contained in the phase branch — or there was never a
    branch to bring, which is a member finished before the phase reached
    it. Containment alone is not enough, for the reason it is not enough
    there either: a run that exits without committing leaves an empty
    branch that is trivially contained.

    A member that halted, was held or was walked back satisfies neither.
    Its card stays where it is and so do its worktree and its branch:
    there is work in them, and it is the reason a person will look at this
    phase afterwards. Removing a worktree with work in it is the one
    unrecoverable thing here.
    """
    brought = []
    for member in task.get("members") or []:
        if member.get("stage") not in ("review", "done"):
            continue
        branch = f"task/{member['file'][:-3]}"
        if _branch_exists(branch) and not _contains(branch, phase_branch):
            continue
        brought.append(member)
    return brought


def _tip(ref: str) -> str:
    """The commit `ref` names right now, or "" when it names none."""
    found = _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return found.stdout.strip() if found.returncode == 0 else ""


def _pinned_head(decision: dict, branch: str) -> str | None:
    """The commit a gated merge must take: the one the gate just validated.

    An ungated board merges the branch as it always did, so it pins
    nothing. A gated (or overridden) decision must name a commit on the
    very branch this would merge — a gate that read another ref, or no
    commit at all, has validated nothing this merge could take.
    """
    if decision.get("decision") == "ungated":
        return None
    head = decision.get("head") or {}
    commit = head.get("commit")
    if not commit or head.get("ref") != branch:
        raise ValueError(
            f"the gate read {head.get('ref') or 'no branch'} at "
            f"{str(commit or 'no commit')[:12]}, and this would merge "
            f"{branch} — nothing was merged, because a gate is only about the "
            f"commit it read")
    return commit


def _gh_merge_is_conditional() -> bool:
    """Can this gh make a merge conditional on the head it is told?"""
    found = _run([config.GH_BIN, "pr", "merge", "--help"], timeout=30)
    return "--match-head-commit" in (found.stdout + found.stderr)


def _delete_remote_branch(rname: str, branch: str, merged: str,
                          filename: str) -> bool:
    """Delete `branch` on the remote only while it holds nothing but what
    was merged — read with `ls-remote`, then deleted with a lease on that
    exact value, so a push landing in between makes git refuse rather than
    bench delete a commit nobody merged. Returns whether it was deleted."""
    listed = _run(["git", "ls-remote", rname, f"refs/heads/{branch}"], timeout=60)
    words = listed.stdout.split() if listed.returncode == 0 else None
    if words == []:
        return True                      # nothing there to delete
    tip = words[0] if words else ""
    if not tip or not (tip == merged or _contains(tip, merged)):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{branch} kept on {rname} — it holds "
                        f"{tip[:12] or 'something bench could not read'}, "
                        f"which is not in what was merged")})
        return False
    pushed = _run(["git", "push", f"--force-with-lease=refs/heads/{branch}:{tip}",
                   rname, "--delete", branch], timeout=60)
    if pushed.returncode != 0:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{branch} kept on {rname} — it moved while bench was "
                        f"deleting it ({pushed.stderr.strip()[:100]})")})
        return False
    return True


def _clear_member(member: dict, merged: str | None = None) -> str:
    """One merged member's workspace, cleared exactly as completing an
    ordinary card clears its own: the worktree, the local branch, the
    branch on the remote. Returns what to say when something was kept.

    The worktree comes out without `--force`, which is the whole
    difference from the card's own cleanup above. A member's work is in
    `main` by the time this runs, so nothing in there is *needed* — but
    that is a judgement about committed files, and an uncommitted change
    is exactly what it does not cover. A dirty worktree is reported and
    kept, with its branch, rather than forced: this is the one
    unrecoverable step in the ending, and it is asked before it is taken
    rather than left to git to refuse.
    """
    stem = member["file"][:-3]
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem
    # A member branch that gained commits after the phase merged it holds
    # work the merge did not carry; clearing it would delete that.
    if merged and _branch_exists(branch) and not _contains(branch, merged):
        return (f"{stem}: worktree and branch kept — {branch} has moved on "
                f"since the phase merged it")
    try:
        if worktree.exists():
            dirty = _run(["git", "-C", str(worktree), "status",
                          "--porcelain"]).stdout.split("\n")
            dirty = [line for line in dirty if line.strip()]
            if dirty:
                return (f"{stem}: worktree kept — {len(dirty)} uncommitted "
                        f"change{'' if len(dirty) == 1 else 's'} in it")
            removed = _run(["git", "worktree", "remove", str(worktree)])
            if removed.returncode != 0:
                detail = (removed.stderr.strip() or removed.stdout.strip()
                          or "git would not remove it")
                return f"{stem}: worktree kept — {detail.splitlines()[-1][:120]}"
        if _branch_exists(branch):
            # -D when origin made the merge, for the same reason the
            # card's own branch takes it: the base here has not caught up.
            _run(["git", "branch",
                  "-D" if config.app_merge_on_origin() else "-d", branch])
            rname = remote()
            if rname and merged:
                _delete_remote_branch(rname, branch, merged, member["file"])
            elif rname:
                _run(["git", "push", rname, "--delete", branch], timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"{stem}: workspace kept — {str(exc)[:120]}"
    PR_STATE.pop(member["file"], None)
    plans.forget(member["file"])
    return ""


def _moved_on(member: dict, merged: str | None) -> bool:
    """Has this member's branch gained commits the phase's merge did not
    carry? Then its newest work is not in the base, whatever its card says."""
    branch = f"task/{member['file'][:-3]}"
    return bool(merged and _branch_exists(branch) and not _contains(branch, merged))


def _sweep(filename: str, task: dict, brought: list[dict],
           merged: str | None = None) -> tuple[list[dict], list[dict]]:
    """Finish the cards the phase finished.

    A member stops at `review/` on purpose — `done/` has always meant
    merged into `main`, and merged into a phase branch is not that. This
    is the moment it becomes that: the phase's PR is in `main`, so every
    card it carried is too, and leaving them in the column whose note is
    "your move" would hand back a pile of work already judged.

    One ending, so it is told as one: the cards move together in a single
    commit (`taskfiles.move_together`) and the ticker gets one line naming
    the phase and how many went with it, rather than five moves scrolling
    past. What was *not* swept is named too — a phase that left cards
    behind is a phase somebody still has to look at.

    A member whose branch moved on after the phase merged it is the one
    exception among those brought: only part of its work is in the base,
    so its workspace is kept (`_clear_member`) and so is its card, where it
    is. Returns (moved, held) — the cards that went to done/, and those.
    """
    members = task.get("members") or []
    if not members:                      # a phase card listing nobody
        return [], []
    held = [member for member in brought if _moved_on(member, merged)]
    whole = [member for member in brought if member not in held]
    kept = [note for note in (_clear_member(member, merged) for member in brought)
            if note]
    moved = move_together([(member["file"], member["stage"]) for member in whole
                           if member["stage"] != "done"],
                          "done", f"with phase {task['number'] or filename[:-3]}")
    left = len(members) - len(brought)
    name = (member_entry(task["number"], task["title"]) if task["number"]
            else task["title"])
    summary = f"phase {name} finished — "
    summary += (f"{_cards(len(whole))} merged into it went to done/, worktrees "
                f"and branches cleaned up" if whole
                else "it had merged nothing, so no card went with it"
                if not held else "no card went to done/ with it")
    for member in held:
        summary += (f" · {member['file'][:-3]} has moved on since the phase "
                    f"merged it, so only part of it is merged and it stays in "
                    f"{member['stage']}/")
    if left:
        summary += (f" · {_cards(left)} it never merged "
                    + ("stays where it is" if left == 1 else "stay where they are"))
    state.record_board_event({"kind": "phase", "actor": "board",
                              "file": filename, "summary": summary})
    for note in kept:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{note} — nothing uncommitted is thrown away"})
    return moved, held


def _cards(count: int) -> str:
    return f"{count} card{'' if count == 1 else 's'}"


def _assert_lands_in_the_base(filename: str, target: str) -> None:
    """`done/` has always meant merged into the application base, and a
    stacked card merged into its prerequisite is not that — exactly as a
    phase member merged into its phase branch is not that.

    So **merge & clean up** refuses a stacked card in both modes, and for
    one reason rather than two: not because the local path cannot do it
    (it cannot — it merges into whatever this checkout has out, which is
    how a stacked card's work lands in the default with nobody deciding
    it should) but because finishing it is not this action's to do. The
    stack lands when its prerequisite's own PR lands; then this card's
    target has gone, the board says so, and a replan onto the base is the
    person's decision that makes it finishable.

    The application base is the one the work was planned against, read
    from the record: a card launched on `main` before the operator moved
    `BOARD_APP_BASE` to `trunk` is not stacked on `main`, and finishing it
    into `main` is exactly what it was planned to do.
    """
    saved = plans.recorded(filename)
    app_base = (saved or {}).get("appBase") or config.APP_BASE
    if target == app_base:
        return
    raise ValueError(
        f"{filename} is stacked on {target}, not {app_base} — merge & "
        f"clean up would finish it without its work reaching "
        f"{app_base}. Land its PR into {target} first; once {target} "
        f"itself has merged, retarget the card (⌖ replan) and finish it here")


def _merge_locally(filename: str, branch: str, commit: str | None = None) -> None:
    """The local merge: merge into the application's own base branch in
    this checkout. Publishing it is a separate step (`_publish`), because a
    merge that landed here and a base that reached the remote are two
    different facts.

    The target is the card's recorded PR target, asked here rather than
    passed in so this function and the PR that was opened can never read
    different answers. `_complete` has already refused anything but the
    application base (see `_assert_lands_in_the_base`); reading it again
    rather than assuming it is what keeps the branch names below honest.

    `commit`, when a gate validated one, is what is merged — the SHA, not
    the branch, so a commit landing on the branch after the gate read it
    is not carried in. The message still names the branch.
    """
    target = plans.pr_base(filename)
    current = _run(["git", "branch", "--show-current"]).stdout.strip()
    if current != target:
        raise ValueError(f"the repo is on '{current}', not {target} — switch first")
    # Already in? Then there is nothing to merge, and saying "merged" would
    # describe work this attempt did not do — a retry after a crash
    # between the merge and its publication lands here.
    if _contains(commit or branch, target):
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{branch} is already in {target} — nothing merged again"})
        return
    result = _run(["git", "merge", "--no-edit", "-m", f"Merge branch '{branch}'",
                   commit] if commit else
                  ["git", "merge", "--no-edit", branch], timeout=120)
    if result.returncode != 0:
        _run(["git", "merge", "--abort"])
        detail = (result.stdout.strip() or result.stderr.strip())[-160:]
        raise ValueError(f"merge conflict — resolve by hand ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"merged {branch} into {target}"})


def _merge_on_origin(filename: str, stage: str, branch: str,
                     commit: str | None = None) -> None:
    """Team mode: the merge commit is made by GitHub, on origin.

    Replicas keep converging only while local main advances by
    fast-forward, so the board never creates a merge commit of its own —
    it asks origin for one and lets the sync beat deliver it. Needs merge
    rights on the repo for whoever clicks, which the local path did not.

    `commit`, when a gate validated one, makes the merge conditional:
    `gh pr merge --match-head-commit` has GitHub refuse it if the pull
    request's head is anything else by the time it merges. A gh that
    cannot say that is refused rather than worked around — an
    unconditional merge over a gate is the race the gate exists to close.

    A reply can be lost — `gh` timing out on a merge GitHub made — so the
    remote's base is read before asking and again after a refusal: a merge
    already there is not asked for twice, and one that landed is not
    reported as refused. Whether it was *delivered* is `_publish`'s to
    say afterwards, from the remote, not from gh's exit status.
    """
    rname = remote()
    intended = commit or _tip(branch)
    target = plans.pr_base(filename)

    def landed() -> bool:
        return bool(rname and intended) and \
            _remote_holds(rname, target, intended)[0] is True

    if landed():
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{rname}/{target} already holds {intended[:12]} — the "
                        f"PR was merged already, so it is not merged again")})
        return
    if commit and gh_available() and not _gh_merge_is_conditional():
        raise ValueError(
            f"this board's gate validated {commit[:12]}, and the installed gh "
            f"cannot make the merge conditional on it — `gh pr merge --help` "
            f"offers no --match-head-commit. Nothing was merged: upgrade gh, "
            f"because bench will not merge unconditionally over a gate")
    task = read_task(config.TASKS / stage / filename, stage)
    url = task.get("pr")
    if not url:
        raise ValueError(
            f"{filename} has no PR, and this board merges the application on "
            f"origin — open a PR for {branch} first (↑ open PR on the card)")
    if not gh_available():
        raise ValueError("gh is not installed — this board merges the "
                        "application on origin (BOARD_APP_MERGE)")
    number = url.rstrip("/").rsplit("/", 1)[-1]
    # origin merges the PR into whatever the PR targets, so the one thing
    # worth checking is that the PR targets what this card planned. A
    # contradiction is reported, never resolved by retargeting.
    target = plans.pr_base(filename, task)
    actual = (_existing_pr_info(branch) or {}).get("base")
    if actual and actual != target:
        raise ValueError(
            f"{filename}'s PR is open against {actual}, and the card's plan "
            f"targets {target} — nothing was merged. Settle which base is right "
            f"(⌖ replan, or change the PR on GitHub) first")
    condition = ["--match-head-commit", commit] if commit else []
    try:
        result = _gh_pr("merge", number, "--merge", *condition, timeout=180)
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess([], 1, "", "gh timed out")
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-160:]
        if landed():
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": (f"gh reported a failure ({detail[:100]}), but "
                            f"{rname}/{target} holds {intended[:12]} — the merge "
                            f"landed and the reply was lost")})
            return
        raise ValueError(f"origin would not merge the PR — resolve it on GitHub ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": (f"merged {filename}'s PR on origin"
                    + (f" at {commit[:12]}" if commit else ""))})


def _catch_up_base(filename: str) -> None:
    """Bring the application's own base branch to the merge origin made.

    Vendored, the sync beat does this — it fetches and fast-forwards the
    one repository both identities share. With the state in a repository
    of its own, *nothing* does: sync never touches the application, and
    the checkout would sit behind a base it has never seen. New task
    branches are cut from `<remote>/<base>` after a fetch, so they are
    right either way; this is about the checkout a person reads and a
    driver runs in.

    Best effort, and narrated honestly: a checkout on another branch or
    with uncommitted changes is left exactly as it is.
    """
    base, rname = config.APP_BASE, remote()
    if not rname or config.SYNC and config._one_repository():
        return                      # the sync beat owns this checkout
    _run(["git", "fetch", rname, base], timeout=120)
    current = _run(["git", "branch", "--show-current"]).stdout.strip()
    if current != base:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"the application checkout is on '{current}', not "
                       f"{base} — it stays behind the merge origin made "
                       f"until you switch back and pull"})
        return
    result = _run(["git", "merge", "--ff-only", f"{rname}/{base}"], timeout=120)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip()
                  or "git gave no detail").splitlines()[-1][:140]
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"could not fast-forward {base} to {rname}/{base} "
                       f"after the merge ({detail}) — pull it yourself"})
        return
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"fast-forwarded {base} to {rname}/{base}"})


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
