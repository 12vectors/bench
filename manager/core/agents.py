"""Headless Claude Code agents: launching, reaping, stopping, diffing.

Two kinds:
- work agents (start_agent) get an isolated git worktree + branch and may
  edit and commit; the board moves their card in-progress → testing.
- review agents (start_review) are read-only, run in the main checkout, and
  their relevance report is appended to the task file by the board.

Prompts live in .prompts/ and are read fresh on every launch.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

import config
import events
import state
from taskfiles import find_stage_of, move_task, read_task


def _clean_log(text: str, cap: int = 3000) -> str:
    """An agent's -p output is its final report; strip the hook-failure
    noise other tools may have interleaved."""
    lines = [l for l in text.strip().splitlines()
             if not ("hook" in l and "failed" in l)]
    return "\n".join(lines).strip()[-cap:]


def _file_report(record: dict, heading: str, report: str) -> None:
    """The report travels with the task, like every review does."""
    stage = find_stage_of(record["task"])
    if not stage or not report:
        return
    path = config.TASKS / stage / record["task"]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n---\n\n## {heading} — {stamp} ({record.get('name') or 'agent'})\n\n{report}\n")
    except OSError:
        pass


def _session_report(record: dict, report: str) -> None:
    """And it lands in the session's timeline as its closing entry."""
    if not report or not record.get("session"):
        return
    events.ingest_event({
        "v": 1, "session": record["session"], "kind": "report",
        "summary": f"{record.get('name') or 'the agent'}'s report on {record['task']}",
        "detail": report, "agent": record["id"], "task": record["task"],
    })


# Short coastal names in the Bench spirit — one per running agent, so the
# board reads "Wren is on #09", not "agent 09-application-layer-091203".
NAMES = ["Wren", "Juno", "Basil", "Piper", "Sage", "Reed", "Olive", "Finch",
         "Hazel", "Cleo", "Milo", "Fern", "Ada", "Otto", "Nell", "Skye"]


def _pick_name(stem: str) -> str:
    """Stable-ish per task (same task tends to get the same name back),
    skipping names already worn by a running agent."""
    with state.LOCK:
        used = {r.get("name") for r in state.AGENTS.values() if r["status"] == "running"}
    start = sum(ord(c) for c in stem) % len(NAMES)
    for i in range(len(NAMES)):
        name = NAMES[(start + i) % len(NAMES)]
        if name not in used:
            return name
    return NAMES[start]


def _agent_public(record: dict) -> dict:
    public = {k: record[k] for k in
              ("id", "task", "branch", "worktree", "status", "rc", "started", "session")}
    public["mode"] = record.get("mode", "work")
    public["name"] = record.get("name")
    # The model the launch was actually given; None = inherited the
    # vendor's own default. Honesty for the Sessions/Focus views.
    public["model"] = record.get("model")
    return public


def list_public() -> list[dict]:
    with state.LOCK:
        return [_agent_public(a) for a in state.AGENTS.values()]


def _assert_no_running_agent(filename: str) -> None:
    with state.LOCK:
        for record in state.AGENTS.values():
            if record["task"] == filename and record["status"] == "running":
                raise ValueError(f"an agent is already working on {filename}")


def _validate(filename: str, stage: str, allowed: set[str], why: str | None = None) -> None:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in allowed:
        raise ValueError(why or f"agents cannot start from {stage}/")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    _assert_no_running_agent(filename)


def _launch(mode: str, prompt: str, cwd: Path, agent_id: str, filename: str, log_path: Path):
    """Run one headless job through the configured agent adapter.

    The adapter contract: `run` gets AGENT_PROMPT, AGENT_MODE (the intent:
    work = mutate and commit, act-pr = work + push, review = read-only +
    post PR verdicts), AGENT_COMMANDS (the project's runnable command
    prefixes) and AGENT_MODEL (the configured model, when there is one)
    plus the BOARD_* passthrough for its event bridge; its stdout is the
    job log; exit 0 = completed. Returns (proc, log_file, model) with
    model = '' when the launch inherits the vendor default.
    """
    adapter = config.adapter_dir()
    if adapter is None:
        raise ValueError(
            f"agent adapter '{config.ADAPTER}' not found — expected "
            f"local/adapters/{config.ADAPTER}/run or core/adapters/{config.ADAPTER}/run")
    env = config.child_env()
    env.update({
        "AGENT_PROMPT": prompt,
        "AGENT_MODE": mode,
        "AGENT_COMMANDS": config.AGENT_COMMANDS,
        "AGENT_CWD": str(cwd),
        "BOARD_AGENT_ID": agent_id,
        "BOARD_TASK": filename,
        "BOARD_PORT": str(state.serve_port),
    })
    model = config.agent_model(mode)
    if model:
        env["AGENT_MODEL"] = model
    else:
        # Inherit = the variable is simply absent. Popping also stops a
        # stray AGENT_MODEL in the board's own environment leaking through.
        env.pop("AGENT_MODEL", None)
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            [str(adapter / "run")], cwd=str(cwd), env=env,
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as exc:
        log_file.close()
        raise ValueError(f"could not launch adapter {adapter}: {exc}")
    return proc, log_file, model


def _fresh_branch_point() -> tuple[str | None, str | None]:
    """Where a brand-new task branch should start: the newest main that
    exists. With an `origin` remote, fetch its main (bounded by
    FETCH_TIMEOUT) and branch from origin/main — never touching the main
    checkout itself, the fetched ref is only the branch point. No remote,
    a failed fetch or a timeout all mean today's behaviour: branch from
    HEAD, because launching must never be blocked by network weather.

    Returns (start point, ticker note); (None, …) means HEAD. The note is
    non-None whenever the branch point deserves a mention — origin/main
    ahead of this checkout, or a fetch that had to be skipped.
    """
    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(config.REPO), *args],
                              capture_output=True, text=True)

    if "origin" not in _git("remote").stdout.split():
        return None, None
    try:
        fetched = subprocess.run(
            ["git", "-C", str(config.REPO), "fetch", "origin", "main"],
            capture_output=True, text=True, timeout=config.FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "fetch of origin/main timed out; branched from local HEAD"
    if fetched.returncode != 0 or \
            _git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        return None, "fetch of origin/main failed; branched from local HEAD"
    # Counted against HEAD, not main: HEAD is the fallback base, so this is
    # exactly what launching would have missed — accurate even when the
    # board checkout sits on another branch.
    ahead = _git("rev-list", "--count", "HEAD..origin/main").stdout.strip()
    if ahead.isdigit() and int(ahead) > 0:
        return "origin/main", (f"branched from origin/main, "
                               f"{ahead} ahead of this checkout")
    return "origin/main", None


def start_agent(filename: str, stage: str) -> dict:
    # Moving a card to in-progress is the commitment; only then does work start.
    _validate(filename, stage, {"in-progress"},
              "work starts from in-progress/ — move the card there first")

    stem = filename[:-3]
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(config.REPO), *args],
                              capture_output=True, text=True)

    branch_exists = _git("rev-parse", "--verify", "--quiet", branch).returncode == 0
    continuing = worktree.exists()
    base_note = None
    if continuing:
        # earlier work exists — the agent continues on it rather than refusing
        current = subprocess.run(
            ["git", "-C", str(worktree), "branch", "--show-current"],
            capture_output=True, text=True).stdout.strip()
        if current != branch:
            raise ValueError(
                f"worktree {worktree} is on '{current}', not {branch} — fix it by hand")
        base = _git("merge-base", "main", branch).stdout.strip() \
            or _git("rev-parse", "HEAD").stdout.strip()
    else:
        config.WORKTREES.mkdir(exist_ok=True)
        if branch_exists:
            base = _git("merge-base", "main", branch).stdout.strip()
            result = _git("worktree", "add", str(worktree), branch)
        else:
            point, base_note = _fresh_branch_point()
            if point:
                base = _git("rev-parse", point).stdout.strip()
                result = _git("worktree", "add", "--no-track", "-b", branch,
                              str(worktree), point)
            else:
                base = _git("rev-parse", "HEAD").stdout.strip()
                result = _git("worktree", "add", "-b", branch, str(worktree))
        if result.returncode != 0:
            raise ValueError(f"git worktree add failed: {result.stderr.strip()[:300]}")

    task = read_task(config.TASKS / "in-progress" / filename, "in-progress")

    agent_id = f"{stem}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = config.prompt("work.md").format(
        branch=branch, filename=filename, body=task["body"])
    proc, log_file, model = _launch("work", prompt, worktree, agent_id, filename, log_path)

    name = _pick_name(stem)
    record = {
        "id": agent_id, "task": filename, "branch": branch,
        "worktree": str(worktree), "base": base, "status": "running",
        "rc": None, "started": time.time(), "session": None,
        "log": str(log_path), "proc": proc, "origin": stage, "mode": "work",
        "name": name, "model": model or None,
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    summary = (f"{name} is back on {filename} — continuing branch {branch}"
               if continuing else
               f"{name} started on {filename} (branch {branch})")
    if base_note:
        summary += f" — {base_note}"
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": summary,
    })
    threading.Thread(target=_reap_agent, args=(agent_id, proc, log_file),
                     daemon=True).start()
    return _agent_public(record)


def start_review(filename: str, stage: str) -> dict:
    """Fire a read-only agent that checks the task against the codebase."""
    _validate(filename, stage, config.STAGE_DIRS)

    task = read_task(config.TASKS / stage / filename, stage)
    agent_id = f"review-{filename[:-3]}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = config.prompt("review.md").format(
        stage=stage, filename=filename, body=task["body"])
    proc, log_file, model = _launch("review", prompt, config.REPO, agent_id, filename, log_path)

    name = _pick_name(filename)
    record = {
        "id": agent_id, "task": filename, "branch": None, "worktree": None,
        "base": None, "status": "running", "rc": None, "started": time.time(),
        "session": None, "log": str(log_path), "proc": proc,
        "origin": stage, "mode": "review", "name": name, "model": model or None,
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": f"{name} is checking {filename} is still true of the codebase",
    })
    threading.Thread(target=_reap_review, args=(agent_id, proc, log_file),
                     daemon=True).start()
    return _agent_public(record)


def _declined_reason(log_path: str) -> str | None:
    """First line of a `NOT READY:` marker in the agent's final output."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in re.finditer(r"^NOT READY:\s*(.*)$", text, re.MULTILINE):
        reason = match.group(1).strip()
        if reason.startswith("<"):
            continue  # the prompt's own template line, echoed into the log
        return reason or "open questions"
    return None


def _no_new_commits(record: dict) -> bool:
    """True iff the worktree's HEAD is still the commit the agent started
    from — i.e. the run produced no commits on the branch."""
    if not record.get("worktree") or not record.get("base"):
        return False
    head = subprocess.run(
        ["git", "-C", record["worktree"], "rev-parse", "HEAD"],
        capture_output=True, text=True)
    return head.returncode == 0 and head.stdout.strip() == record["base"]


def _discard_untouched_worktree(record: dict) -> bool:
    """Remove worktree + branch, but only if the agent committed nothing."""
    if not _no_new_commits(record):
        return False
    subprocess.run(["git", "-C", str(config.REPO), "worktree", "remove", "--force",
                    record["worktree"]], capture_output=True)
    subprocess.run(["git", "-C", str(config.REPO), "branch", "-D", record["branch"]],
                   capture_output=True)
    return True


def _finish(agent_id: str, proc: subprocess.Popen, log_file) -> tuple[dict, bool, int]:
    rc = proc.wait()
    log_file.close()
    with state.LOCK:
        record = state.AGENTS[agent_id]
        stopped = record["status"] == "stopped"
        record["status"] = "stopped" if stopped else ("done" if rc == 0 else "failed")
        record["rc"] = rc
    return record, stopped, rc


def _reap_agent(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = _finish(agent_id, proc, log_file)
    filename, branch = record["task"], record["branch"]
    name = record.get("name") or "the agent"

    declined = None if (stopped or rc != 0) else _declined_reason(record["log"])
    if declined is not None:
        with state.LOCK:
            record["status"] = "declined"
        # Send the card back for refinement and clear the way for a relaunch.
        back_to = record["origin"] if record["origin"] in ("backlog", "to-do") else "to-do"
        if find_stage_of(filename) == "in-progress":
            try:
                move_task(filename, "in-progress", back_to, actor="agent")
            except ValueError:
                pass
        cleaned = _discard_untouched_worktree(record)
        summary = (f"{name} declined {filename} — not ready: {declined}"
                   + ("" if cleaned else f" (worktree {record['worktree']} kept: it has commits)"))
    elif rc == 0 and not stopped:
        try:
            report = _clean_log(Path(record["log"]).read_text(encoding="utf-8",
                                                              errors="replace"))
        except OSError:
            report = ""
        _file_report(record, "Work report", report)
        _session_report(record, report)
        if _no_new_commits(record):
            # A "clean" exit with an empty branch is how permission bugs
            # hide: nothing reaches review/ silently.
            summary = (f"{name} exited cleanly on {filename} but committed "
                       f"NOTHING to {branch} — card stays in in-progress; "
                       f"read the report before relaunching")
        else:
            if find_stage_of(filename) == "in-progress":
                try:
                    move_task(filename, "in-progress", "review", actor="agent")
                except ValueError:
                    pass
            summary = f"{name} finished {filename} — review branch {branch}"
    elif stopped:
        summary = f"{name} was held on {filename} — nothing is lost"
    else:
        summary = f"{name} exited on {filename} rc={rc} — see its log"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "agents"})


def start_pr_review(filename: str, stage: str) -> dict:
    """Fire a read-only agent that reviews the task's PR and posts the
    verdict to GitHub as well as back to the board."""
    _validate(filename, stage, {"review"},
              "PR reviews run on cards in review/")
    task = read_task(config.TASKS / stage / filename, stage)
    if not task.get("pr"):
        raise ValueError(f"{filename} has no PR yet — nothing to review")

    branch = f"task/{filename[:-3]}"
    name = _pick_name(filename)
    agent_id = f"review-pr-{filename[:-3]}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = config.prompt("review-pr.md").format(
        filename=filename, pr=task["pr"], branch=branch, body=task["body"])
    proc, log_file, model = _launch("review", prompt, config.REPO, agent_id, filename, log_path)

    record = {
        "id": agent_id, "task": filename, "branch": branch, "worktree": None,
        "base": None, "status": "running", "rc": None, "started": time.time(),
        "session": None, "log": str(log_path), "proc": proc,
        "origin": stage, "mode": "review", "name": name, "model": model or None,
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": f"{name} is reviewing {filename}'s PR",
    })
    threading.Thread(target=_reap_pr_review, args=(agent_id, proc, log_file),
                     daemon=True).start()
    return _agent_public(record)


def start_pr_fix(filename: str, stage: str) -> dict:
    """Fire a work agent that addresses the review feedback on the task's PR,
    working in the task's existing worktree (recreated from the branch if it
    was cleaned up), committing and pushing to update the PR."""
    _validate(filename, stage, {"review"},
              "acting on a PR happens from review/")
    task = read_task(config.TASKS / stage / filename, stage)
    if not task.get("pr"):
        raise ValueError(f"{filename} has no PR to act on")

    stem = filename[:-3]
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem
    if not worktree.exists():
        if subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--verify",
                           "--quiet", branch], capture_output=True).returncode != 0:
            raise ValueError(f"branch {branch} does not exist locally — nothing to act in")
        config.WORKTREES.mkdir(exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(config.REPO), "worktree", "add", str(worktree), branch],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"could not recreate the worktree: {result.stderr.strip()[:200]}")

    name = _pick_name(stem)
    agent_id = f"fix-pr-{stem}-{time.strftime('%H%M%S')}"
    log_path = config.AGENT_DIR / "logs" / f"{agent_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = config.prompt("act-pr.md").format(
        filename=filename, branch=branch, pr=task["pr"], body=task["body"])
    # act-pr is the one intent allowed to push: the PR must update.
    proc, log_file, model = _launch("act-pr", prompt, worktree, agent_id, filename, log_path)

    record = {
        "id": agent_id, "task": filename, "branch": branch,
        "worktree": str(worktree), "base": None, "status": "running",
        "rc": None, "started": time.time(), "session": None,
        "log": str(log_path), "proc": proc, "origin": stage, "mode": "work",
        "name": name, "model": model or None,
    }
    with state.LOCK:
        state.AGENTS[agent_id] = record
    state.record_board_event({
        "kind": "agent", "actor": "agent", "file": filename,
        "summary": f"{name} is acting on the review of {filename}'s PR",
    })
    threading.Thread(target=_reap_pr_fix, args=(agent_id, proc, log_file),
                     daemon=True).start()
    return _agent_public(record)


def _reap_pr_fix(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = _finish(agent_id, proc, log_file)
    filename = record["task"]
    name = record.get("name") or "the agent"

    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("ADDRESSED:")
        report = text[idx:].strip() if idx >= 0 else text.strip()[-1500:]
        _file_report(record, "PR update", report)
        _session_report(record, report)
        summary = f"{name} acted on {filename}'s PR — re-review when ready"
    elif stopped:
        summary = f"{name} was held while acting on {filename}'s PR"
    else:
        summary = f"{name} failed acting on {filename}'s PR (rc={rc}) — see its log"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def _reap_pr_review(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = _finish(agent_id, proc, log_file)
    filename = record["task"]
    name = record.get("name") or "the reviewer"

    verdict = None
    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("PR REVIEW:")
        report = text[idx:].strip() if idx >= 0 else text.strip()[-1500:]
        match = re.search(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", report)
        verdict = match.group(1) if match else None
        _file_report(record, "PR review", report)
        _session_report(record, report)

    if stopped:
        summary = f"{name}'s PR review of {filename} was held"
    elif rc != 0 or verdict is None:
        summary = f"{name}'s PR review of {filename} ended without a verdict — see its log"
    else:
        word = "approved it" if verdict == "APPROVE" else "asked for changes"
        summary = f"{name} reviewed {filename}'s PR and {word}"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def _reap_review(agent_id: str, proc: subprocess.Popen, log_file) -> None:
    record, stopped, rc = _finish(agent_id, proc, log_file)
    filename = record["task"]

    verdict = None
    if rc == 0 and not stopped:
        try:
            text = Path(record["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        idx = text.find("RELEVANCE REVIEW")
        report = text[idx:].strip() if idx >= 0 else text.strip()[-1500:]
        verdict = report.splitlines()[0] if report else None
        _file_report(record, "Relevance review", report)
        _session_report(record, report)

    name = record.get("name") or "the review"
    if stopped:
        summary = f"{name}'s check of {filename} was held"
    elif rc != 0:
        summary = f"{name}'s check of {filename} exited rc={rc} — see its log"
    else:
        summary = f"{name} on {filename}: {verdict[:140] if verdict else 'report appended to the task'}"
    state.record_board_event({"kind": "agent", "actor": "agent", "file": filename,
                              "summary": summary})
    state.broadcast({"type": "board"})
    state.broadcast({"type": "agents"})


def stop_agent(agent_id: str) -> dict:
    with state.LOCK:
        record = state.AGENTS.get(agent_id)
        if record is None:
            raise ValueError("unknown agent")
        if record["status"] != "running":
            raise ValueError("agent is not running")
        record["status"] = "stopped"
        proc = record["proc"]
    proc.terminate()
    return _agent_public(record)


def agent_diff(agent_id: str) -> dict:
    with state.LOCK:
        record = state.AGENTS.get(agent_id)
    if record is None:
        raise ValueError("unknown agent")
    if not record.get("worktree"):
        return {"agent": agent_id, "files": []}
    result = subprocess.run(
        ["git", "-C", record["worktree"], "diff", "--numstat", record["base"]],
        capture_output=True, text=True, timeout=10)
    files = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            plus, minus, name = parts
            files.append({"file": name,
                          "plus": int(plus) if plus.isdigit() else 0,
                          "minus": int(minus) if minus.isdigit() else 0})
    return {"agent": agent_id, "files": files}
