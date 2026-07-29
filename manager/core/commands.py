"""Local commands: project-owned executables run against a task's worktree.

Core knows nothing about what any command does — the project does
(local/commands/<name>, executables the project owns and evolves). The
contract:

  - env in: CMD_WORKTREE, CMD_BRANCH, CMD_TASK, CMD_REPO (+ BOARD_*)
  - stdout/stderr → a log under local/state/commands/
  - exit 0 = done; anything else = failed — either way the ticker narrates
    the ending with the log's last line

One run per (task, command) at a time.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import config
import state

RUNNING: dict[str, dict] = {}   # "task:name" -> record


def public() -> list[dict]:
    with state.LOCK:
        return [{"task": r["task"], "name": r["name"], "started": r["started"]}
                for r in RUNNING.values()]


def run(name: str, filename: str) -> dict:
    if not any(c["name"] == name for c in config.commands()):
        raise ValueError(f"no such command: {name}")
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    key = f"{filename}:{name}"
    with state.LOCK:
        if key in RUNNING:
            raise ValueError(f"{name} is already running on {filename}")

    stem = filename[:-3]
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem
    if not worktree.exists():
        if subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--verify",
                           "--quiet", branch], capture_output=True).returncode != 0:
            raise ValueError(f"no worktree and no branch {branch} — nothing to run against")
        config.WORKTREES.mkdir(exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(config.REPO), "worktree", "add", str(worktree), branch],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"could not recreate the worktree: {result.stderr.strip()[:200]}")

    log_dir = config.STATE / "commands"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stem}-{name}-{time.strftime('%H%M%S')}.log"
    env = config.child_env()
    env.update({
        "CMD_WORKTREE": str(worktree),
        "CMD_BRANCH": branch,
        "CMD_TASK": filename,
        "CMD_REPO": str(config.REPO),
        "BOARD_PORT": str(state.serve_port),
    })
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            [str(config.LOCAL / "commands" / name)], cwd=str(worktree), env=env,
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as exc:
        log_file.close()
        raise ValueError(f"could not launch {name}: {exc}")

    record = {"task": filename, "name": name, "started": time.time(),
              "proc": proc, "log": str(log_path)}
    with state.LOCK:
        RUNNING[key] = record
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"running {name} on {filename}'s worktree"})
    state.broadcast({"type": "board"})
    threading.Thread(target=_reap, args=(key, record, log_file), daemon=True).start()
    return {"name": name, "task": filename}


def _reap(key: str, record: dict, log_file) -> None:
    rc = record["proc"].wait()
    log_file.close()
    with state.LOCK:
        RUNNING.pop(key, None)
    try:
        lines = [l.strip() for l in Path(record["log"]).read_text(
            encoding="utf-8", errors="replace").splitlines() if l.strip()]
        last = lines[-1][:150] if lines else ""
    except OSError:
        last = ""
    verdict = "done" if rc == 0 else f"failed (rc={rc})"
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": record["task"],
        "summary": f"{record['name']} on {record['task']} {verdict}"
                   + (f" — {last}" if last else "")})
    state.broadcast({"type": "board"})
