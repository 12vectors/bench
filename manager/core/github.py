"""GitHub plumbing: opening PRs from agent worktree branches, requesting
Copilot reviews, and polling PR state for cards sitting in review/.

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
import plans
import reports
import state
from taskfiles import (STATUS_RE, collect, commit_edit, find_stage_of,
                       member_entry, move_task, move_together, read_task)

PR_STATE: dict[str, dict] = {}   # filename -> {verdict, detail, url, ts}
_OPENING: set[str] = set()       # filenames with a PR-open in flight


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or config.REPO),
                          capture_output=True, text=True, timeout=timeout)


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
    the same apparatus as any other card."""
    phase = f"phase/{filename[:-3]}"
    return phase if _branch_exists(phase) else f"task/{filename[:-3]}"


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
    base whose diff is the member's own work.
    """
    return plans.pr_base(task.get("file") or "", task)


def _pr_body(filename: str, task: dict) -> str:
    """The PR's body. A phase's PR is the one a whole run produces, so it
    says what is in it: the member list, in the order it ran."""
    if task.get("isPhase"):
        cards = "\n".join(f"- {m['number']} — {m['title']}"
                          for m in task.get("members") or [])
        return (f"Phase: `{filename}` — tracked in `.task-manager/tasks/review/`.\n\n"
                f"Opened by the board when every card in the phase had been "
                f"merged into its branch.\n\n## Cards in this phase\n\n"
                f"{cards or '_none_'}\n")
    body = (f"Task: `{filename}` — tracked in `.task-manager/tasks/review/`.\n\n"
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
        # The PR's diff is computed against the remote base — refuse to open one
        # that would drag unpushed base commits along with it.
        _run(["git", "fetch", rname, base], timeout=120)
        ahead = _run(["git", "rev-list", "--count",
                      f"{rname}/{base}..{base}"]).stdout.strip()
        if ahead.isdigit() and int(ahead) > 0:
            raise ValueError(f"won't open a PR for {filename}: {base} is {ahead} commits "
                             f"ahead of {rname} — push {base} first, then move the card again")
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
    result = _run([config.GH_BIN, "pr", "create", "--head", branch, "--base", base,
                   "--title", task["title"], "--body", body], timeout=120)
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
    found = _run([config.GH_BIN, "pr", "view", branch, "--json",
                  "url,baseRefName"], timeout=60)
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
                   f"repos/{{owner}}/{{repo}}/pulls/{number}/requested_reviewers",
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
            "conflicts": conflicts, "base": actual, "wantBase": want,
            "baseMismatch": mismatch, "detail": " · ".join(detail_bits)}


def _poll_pr(filename: str, url: str) -> None:
    number = url.rstrip("/").rsplit("/", 1)[-1]
    result = _run([config.GH_BIN, "pr", "view", number,
                   "--json", "reviews,reviewRequests,statusCheckRollup,state,mergeable"
                             ",baseRefName"],
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


def public_state() -> dict:
    return {f: {k: v.get(k) for k in ("verdict", "ci", "copilot", "conflicts",
                                      "base", "wantBase", "baseMismatch",
                                      "detail", "url")}
            for f, v in PR_STATE.items()}


def open_pr_async(filename: str) -> None:
    threading.Thread(target=maybe_open_pr, args=(filename,), daemon=True).start()


def complete_task(filename: str, stage: str) -> dict:
    """The user chose "merge & clean up" on a move to done: park the drive
    if it is this task's, merge the branch, remove the worktree and
    branches, then move the card. Every step narrates; a conflict aborts
    cleanly and the card stays.

    Where the merge happens depends on team mode. Single-player merges into
    the local main and pushes it, exactly as it always did; with
    `BOARD_SYNC` on the merge is made on origin through `gh pr merge`, so
    local main only ever fast-forwards to it — the discipline the whole
    sync design rests on.

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
    """The steps themselves, run under the claim complete_task holds."""
    stem = filename[:-3]
    branch = branch_of(filename)   # a phase's own branch, or task/<stem>
    task = _woven(filename, stage)
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
    if _branch_exists(branch):
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
        if config.SYNC:
            _merge_on_origin(filename, stage, branch)
        else:
            _merge_locally(filename, branch)
        merged = True

        worktree = config.WORKTREES / stem
        if worktree.exists():
            _run(["git", "worktree", "remove", "--force", str(worktree)])
        # -D under sync: main here has not merged the branch yet (origin
        # did), so the safe delete would refuse something already landed.
        _run(["git", "branch", "-D" if config.SYNC else "-d", branch])
        PR_STATE.pop(filename, None)
        # The plan described work that is now in the base. Keeping it would
        # make a reopened card resume against a branch that no longer exists.
        plans.forget(filename)
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"cleaned up: worktree and local branch for {stem} removed"})

        # the merge succeeded, so the members' work is in main too
        swept = _sweep(filename, task, brought)

    move_task(filename, stage, "done", actor="you")
    state.broadcast({"type": "board"})
    return {"merged": merged, "swept": [card["file"] for card in swept]}


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


def _clear_member(member: dict) -> str:
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
            # -D under sync for the same reason the card's own branch takes
            # it: main here has not caught up with the merge origin made.
            _run(["git", "branch", "-D" if config.SYNC else "-d", branch])
            rname = remote()
            if rname:
                _run(["git", "push", rname, "--delete", branch], timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"{stem}: workspace kept — {str(exc)[:120]}"
    PR_STATE.pop(member["file"], None)
    plans.forget(member["file"])
    return ""


def _sweep(filename: str, task: dict, brought: list[dict]) -> list[dict]:
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
    """
    members = task.get("members") or []
    if not members:                      # a phase card listing nobody
        return []
    kept = [note for note in (_clear_member(member) for member in brought) if note]
    moved = move_together([(member["file"], member["stage"]) for member in brought
                           if member["stage"] != "done"],
                          "done", f"with phase {task['number'] or filename[:-3]}")
    left = len(members) - len(brought)
    name = (member_entry(task["number"], task["title"]) if task["number"]
            else task["title"])
    summary = f"phase {name} finished — "
    summary += (f"{_cards(len(brought))} merged into it went to done/, worktrees "
                f"and branches cleaned up" if brought
                else "it had merged nothing, so no card went with it")
    if left:
        summary += (f" · {_cards(left)} it never merged "
                    + ("stays where it is" if left == 1 else "stay where they are"))
    state.record_board_event({"kind": "phase", "actor": "board",
                              "file": filename, "summary": summary})
    for note in kept:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"{note} — nothing uncommitted is thrown away"})
    return moved


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
    """
    if target == config.APP_BASE:
        return
    raise ValueError(
        f"{filename} is stacked on {target}, not {config.APP_BASE} — merge & "
        f"clean up would finish it without its work reaching "
        f"{config.APP_BASE}. Land its PR into {target} first; once {target} "
        f"itself has merged, retarget the card (⌖ replan) and finish it here")


def _merge_locally(filename: str, branch: str) -> None:
    """Single-player: merge into the checkout's own base branch and push it.

    The target is the card's recorded PR target, asked here rather than
    passed in so this function and the PR that was opened can never read
    different answers. `_complete` has already refused anything but the
    application base (see `_assert_lands_in_the_base`); reading it again
    rather than assuming it is what keeps the branch names below honest.
    """
    target = plans.pr_base(filename)
    current = _run(["git", "branch", "--show-current"]).stdout.strip()
    if current != target:
        raise ValueError(f"the repo is on '{current}', not {target} — switch first")
    result = _run(["git", "merge", "--no-edit", branch], timeout=120)
    if result.returncode != 0:
        _run(["git", "merge", "--abort"])
        detail = (result.stdout.strip() or result.stderr.strip())[-160:]
        raise ValueError(f"merge conflict — resolve by hand ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"merged {branch} into {target}"})

    rname = remote()
    if rname:
        push = _run(["git", "push", rname, target], timeout=180)
        if push.returncode != 0:
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": f"merged locally but the push failed — push {target} "
                           f"yourself ({push.stderr.strip()[:100]})"})
        else:
            _run(["git", "push", rname, "--delete", branch], timeout=60)
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": filename,
                "summary": f"pushed {target} (PR marked merged) and deleted "
                           f"{branch} on {rname}"})


def _merge_on_origin(filename: str, stage: str, branch: str) -> None:
    """Team mode: the merge commit is made by GitHub, on origin.

    Replicas keep converging only while local main advances by
    fast-forward, so the board never creates a merge commit of its own —
    it asks origin for one and lets the sync beat deliver it. Needs merge
    rights on the repo for whoever clicks, which the local path did not.
    """
    task = read_task(config.TASKS / stage / filename, stage)
    url = task.get("pr")
    if not url:
        raise ValueError(
            f"{filename} has no PR, and with BOARD_SYNC on the merge is made on "
            f"origin — open a PR for {branch} first (↑ open PR on the card)")
    if not gh_available():
        raise ValueError("gh is not installed — with BOARD_SYNC on the merge runs on origin")
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
    result = _run([config.GH_BIN, "pr", "merge", number, "--merge"], timeout=180)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-160:]
        raise ValueError(f"origin would not merge the PR — resolve it on GitHub ({detail})")
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"merged {filename}'s PR on origin — local main fast-forwards "
                   f"on the next sync beat"})
    rname = remote()
    if rname:
        _run(["git", "push", rname, "--delete", branch], timeout=60)


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
