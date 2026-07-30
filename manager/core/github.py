"""GitHub plumbing: opening PRs from agent worktree branches, requesting
Copilot reviews, and polling PR state for cards sitting in review/.

All of it is mechanical `git` + `gh` — no Claude involvement. The PR url is
written into the task file (`**PR:** <url>`), keeping the file the single
source of truth; only the volatile review/check state lives in memory.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import config
import drive as drive_mod
import state
from taskfiles import STATUS_RE, find_stage_of, move_task, read_task

PR_STATE: dict[str, dict] = {}   # filename -> {verdict, detail, url, ts}
_OPENING: set[str] = set()       # filenames with a PR-open in flight


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or config.REPO),
                          capture_output=True, text=True, timeout=timeout)


def remote() -> str | None:
    if config.GIT_REMOTE:
        return config.GIT_REMOTE
    result = _run(["git", "remote"])
    names = result.stdout.split()
    return names[0] if names else None


def gh_available() -> bool:
    return shutil.which(config.GH_BIN) is not None


def _branch_exists(branch: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet", branch]).returncode == 0


def _write_pr_line(filename: str, url: str) -> None:
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


def maybe_open_pr(filename: str) -> None:
    """Card entered review/ — open a PR for its branch if one can be opened.

    Quiet when there is simply no branch (hand-written tasks); loud in the
    ticker when a PR *should* be possible but something stands in the way.
    """
    if filename in _OPENING:
        return
    _OPENING.add(filename)
    try:
        _open_pr(filename)
    finally:
        _OPENING.discard(filename)


def _open_pr(filename: str) -> None:
    branch = f"task/{filename[:-3]}"
    if not _branch_exists(branch):
        return  # nothing to publish — a hand-moved card without agent work
    stage = find_stage_of(filename)
    if stage != "review":
        return
    task = read_task(config.TASKS / stage / filename, stage)
    if task.get("pr"):
        return  # already open

    rname = remote()
    if rname is None or not gh_available():
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"no PR for {filename}: " +
                       ("no git remote configured" if rname is None else "gh is not installed")})
        return

    # The PR's diff is computed against the remote main — refuse to open one
    # that would drag unpushed main commits along with it.
    _run(["git", "fetch", rname, "main"], timeout=120)
    ahead = _run(["git", "rev-list", "--count", f"{rname}/main..main"]).stdout.strip()
    if ahead.isdigit() and int(ahead) > 0:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"won't open a PR for {filename}: main is {ahead} commits "
                       f"ahead of {rname} — push main first, then move the card again"})
        return

    push = _run(["git", "push", "-u", rname, branch], timeout=180)
    if push.returncode != 0:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"push failed for {branch}: {push.stderr.strip()[:140]}"})
        return

    body = (f"Task: `{filename}` — tracked in `.task-manager/tasks/review/`.\n\n"
            f"Opened by the board when the card moved to review.")
    log_tail = _agent_log_tail(filename)
    if log_tail:
        body += f"\n\n## Agent summary\n\n{log_tail}"
    result = _run([config.GH_BIN, "pr", "create", "--head", branch, "--base", "main",
                   "--title", task["title"], "--body", body], timeout=120)
    if result.returncode != 0:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"PR creation failed for {branch}: {result.stderr.strip()[:140]}"})
        return
    url = next((l.strip() for l in result.stdout.splitlines() if "/pull/" in l), result.stdout.strip())
    _write_pr_line(filename, url)
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"PR opened for {filename}: {url}"})
    state.broadcast({"type": "board"})
    _poll_pr(filename, url)  # first CI/review snapshot without waiting a cycle


def _agent_log_tail(filename: str, cap: int = 1500) -> str:
    with state.LOCK:
        records = [r for r in state.AGENTS.values()
                   if r["task"] == filename and r.get("mode") == "work"]
    if not records:
        return ""
    latest = max(records, key=lambda r: r["started"])
    try:
        text = Path(latest["log"]).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-cap:]


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


def _fold(data: dict, prev: dict) -> dict:
    """One gh pr-view payload + the previous snapshot → the new snapshot.
    Pure fold: fetching, events and broadcasts stay in _poll_pr."""
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

    # A conflict is changes-needed-by-you, not a CI failure: it beats any
    # approval but leaves the CI chip telling its own story.
    verdict = ("red" if (changes or ci == "fail" or conflicts)
               else "green" if approved else "pending")
    detail_bits = []
    if reviews:
        detail_bits.append(f"{len(reviews)} review{'s' if len(reviews) > 1 else ''}")
    if ci:
        detail_bits.append({"fail": "checks failing", "running": "checks running",
                            "pass": "checks ok"}[ci])
    if conflicts:
        detail_bits.append("conflicts with main")
    if copilot:
        detail_bits.append("copilot " + {"asked": "asked", "approved": "approved",
                                         "changes": "asked for changes",
                                         "commented": "commented"}[copilot])
    return {"verdict": verdict, "ci": ci, "copilot": copilot,
            "conflicts": conflicts, "detail": " · ".join(detail_bits)}


def _poll_pr(filename: str, url: str) -> None:
    number = url.rstrip("/").rsplit("/", 1)[-1]
    result = _run([config.GH_BIN, "pr", "view", number,
                   "--json", "reviews,reviewRequests,statusCheckRollup,state,mergeable"],
                  timeout=60)
    if result.returncode != 0:
        return
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return
    prev = PR_STATE.get(filename, {})
    entry = _fold(data, prev)
    entry.update({"url": url, "ts": time.time()})
    PR_STATE[filename] = entry
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
            "summary": (f"{filename}'s PR conflicts with main — ↻ act on PR "
                        f"can attempt the resolution" if entry["conflicts"]
                        else f"{filename}'s PR no longer conflicts with main")})
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
    return {f: {k: v.get(k) for k in ("verdict", "ci", "copilot", "conflicts", "detail", "url")}
            for f, v in PR_STATE.items()}


def open_pr_async(filename: str) -> None:
    threading.Thread(target=maybe_open_pr, args=(filename,), daemon=True).start()


def complete_task(filename: str, stage: str) -> dict:
    """The user chose "merge & clean up" on a move to done: park the drive
    if it is this task's, merge the branch into main, push (which marks the
    PR merged), remove the worktree and branches, then move the card.
    Every step narrates; a conflict aborts cleanly and the card stays."""
    if stage not in config.STAGE_DIRS or stage == "done":
        raise ValueError("complete runs on a live-stage card")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")

    stem = filename[:-3]
    branch = f"task/{stem}"

    # 1. the app must not keep running code that is about to be merged away
    d = drive_mod.DRIVE
    if d and d.get("task") == filename and d.get("status") in ("starting", "up"):
        drive_mod.stop()
        for _ in range(40):
            if not drive_mod._alive(d):
                break
            time.sleep(0.5)

    merged = False
    if _branch_exists(branch):
        current = _run(["git", "branch", "--show-current"]).stdout.strip()
        if current != "main":
            raise ValueError(f"the repo is on '{current}', not main — switch first")
        result = _run(["git", "merge", "--no-edit", branch], timeout=120)
        if result.returncode != 0:
            _run(["git", "merge", "--abort"])
            detail = (result.stdout.strip() or result.stderr.strip())[-160:]
            raise ValueError(f"merge conflict — resolve by hand ({detail})")
        merged = True
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"merged {branch} into main"})

        rname = remote()
        if rname:
            push = _run(["git", "push", rname, "main"], timeout=180)
            if push.returncode != 0:
                state.record_board_event({
                    "kind": "agent", "actor": "board", "file": filename,
                    "summary": f"merged locally but the push failed — push main "
                               f"yourself ({push.stderr.strip()[:100]})"})
            else:
                _run(["git", "push", rname, "--delete", branch], timeout=60)
                state.record_board_event({
                    "kind": "agent", "actor": "board", "file": filename,
                    "summary": f"pushed main (PR marked merged) and deleted {branch} on {rname}"})

        worktree = config.WORKTREES / stem
        if worktree.exists():
            _run(["git", "worktree", "remove", "--force", str(worktree)])
        _run(["git", "branch", "-d", branch])
        PR_STATE.pop(filename, None)
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": f"cleaned up: worktree and local branch for {stem} removed"})

    move_task(filename, stage, "done", actor="you")
    state.broadcast({"type": "board"})
    return {"merged": merged}


def task_branches() -> list[str]:
    """Stems of all task/* branches — the UI uses this to say honestly
    whether a review card has work attached."""
    result = _run(["git", "for-each-ref", "--format=%(refname:short)",
                   "refs/heads/task/"])
    return [ref[len("task/"):] for ref in result.stdout.split() if ref.startswith("task/")]


def reconcile() -> None:
    """Catch up on moves the watcher never saw (board was down): any card
    already sitting in review/ with a branch but no PR gets its PR opened
    now. Runs once at startup."""
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
