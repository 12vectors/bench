"""Drives: launching the project's app from a task's worktree.

Core knows nothing about how any app starts — the project's driver does
(local/driver/start, an executable the project owns and evolves). The
contract:

  - env in: DRIVE_WORKTREE, DRIVE_BRANCH, DRIVE_TASK, DRIVE_REPO
  - exit non-zero quickly and whatever was printed becomes the refusal
    shown in the ticker ("port 5176 is yours", "branch has migrations")
  - print `DRIVE URL: <url>` when the app is up — the card links there
  - keep running until parked; the board SIGTERMs the process group

One drive at a time: you look at one version of the app.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import config
import state

DRIVE: dict | None = None      # the current (or last) drive
URL_RE = re.compile(r"^DRIVE URL:\s*(\S+)", re.MULTILINE)
STATE_FILE = config.STATE / "drive.json"


def _persist() -> None:
    """The drive outlives the board (it is its own process group), so its
    identity lives on disk; a restarted board re-adopts it via adopt()."""
    import json
    try:
        if DRIVE and DRIVE["status"] in ("starting", "up"):
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(
                {k: DRIVE.get(k) for k in
                 ("task", "status", "url", "started", "log", "pgid")}))
        else:
            STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _alive(drive: dict) -> bool:
    proc = drive.get("proc")
    if proc is not None:
        return proc.poll() is None
    try:
        os.killpg(drive["pgid"], 0)
        return True
    except (ProcessLookupError, PermissionError, KeyError, TypeError):
        return False


def adopt() -> None:
    """Board startup: re-adopt a drive a previous board left running.
    The file can lie (crash, reboot) — believe it only if the process
    group is actually alive."""
    global DRIVE
    import json
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return
    try:
        os.killpg(int(data.get("pgid") or 0), 0)
    except (ProcessLookupError, PermissionError, ValueError):
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return
    DRIVE = {**data, "proc": None}
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": DRIVE["task"],
        "summary": f"re-adopted the running drive of {DRIVE['task']}"
                   + (f" at {DRIVE['url']}" if DRIVE.get("url") else "")})
    threading.Thread(target=_watch, args=(DRIVE, None, None), daemon=True).start()


def _record_event(filename: str, summary: str) -> None:
    state.record_board_event({"kind": "agent", "actor": "board",
                              "file": filename, "summary": summary})
    state.broadcast({"type": "board"})


def public() -> dict | None:
    if DRIVE is None:
        return None
    return {k: DRIVE.get(k) for k in ("task", "status", "url", "started", "line", "reason")}


def start(filename: str) -> dict:
    global DRIVE
    driver = config.driver_path()
    if driver is None:
        raise ValueError(
            "no driver: create local/driver/start (see core/driver.example/) — "
            "an executable that launches this project's app from a worktree")
    with state.LOCK:
        if DRIVE and DRIVE["status"] in ("starting", "up"):
            raise ValueError(f"already driving {DRIVE['task']} — park it first")

    stem = filename[:-3] if filename.endswith(".md") else filename
    branch = f"task/{stem}"
    worktree = config.WORKTREES / stem
    if not worktree.exists():
        if subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--verify",
                           "--quiet", branch], capture_output=True).returncode != 0:
            raise ValueError(f"no worktree and no branch {branch} — nothing to drive")
        config.WORKTREES.mkdir(exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(config.REPO), "worktree", "add", str(worktree), branch],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"could not recreate the worktree: {result.stderr.strip()[:200]}")

    config.DRIVES_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.DRIVES_DIR / f"{stem}-{time.strftime('%H%M%S')}.log"
    env = config.child_env()
    env.update({
        "DRIVE_WORKTREE": str(worktree),
        "DRIVE_BRANCH": branch,
        "DRIVE_TASK": filename,
        "DRIVE_REPO": str(config.REPO),
        "BOARD_PORT": str(state.serve_port),
    })
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            [str(driver)], cwd=str(config.REPO), env=env,
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)  # own process group: park kills everything
    except OSError as exc:
        log_file.close()
        raise ValueError(f"could not launch the driver: {exc}")

    DRIVE = {"task": filename, "status": "starting", "url": None,
             "started": time.time(), "proc": proc, "log": str(log_path),
             "pgid": proc.pid}  # start_new_session: pid == pgid
    _persist()
    _record_event(filename, f"driver starting for {filename}")
    threading.Thread(target=_watch, args=(DRIVE, proc, log_file), daemon=True).start()
    return public()


def _tail(log: Path, cap: int = 4096) -> str:
    try:
        with log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - cap))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _watch(drive: dict, proc: subprocess.Popen | None, log_file) -> None:
    """Tail the driver's log for the URL and a live progress line;
    narrate its ending. Works for owned and adopted drives alike."""
    filename = drive["task"]
    log = Path(drive["log"])
    while _alive(drive):
        text = _tail(log)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            new_line = lines[-1][:200]
            if drive.get("line") != new_line:
                drive["line"] = new_line
                state.broadcast({"type": "board"})
        match = URL_RE.search(text)
        if match and drive["status"] == "starting":
            drive["status"] = "up"
            drive["url"] = match.group(1)
            _persist()
            _record_event(filename, f"driving {filename} at {match.group(1)}")
        time.sleep(1)
    if log_file is not None:
        log_file.close()
    rc = proc.returncode if proc is not None else 0
    quick = time.time() - drive["started"] < 30
    if drive["status"] == "parked":
        _record_event(filename, f"parked — {filename}'s drive is down")
    elif rc != 0 and drive["status"] == "starting":
        lines = [l.strip() for l in _tail(log).splitlines() if l.strip()]
        reason = (lines[-1] if lines else f"rc={rc}")[:200]
        drive["status"] = "refused"
        drive["reason"] = reason
        label = "refused" if quick else "failed to come up —"
        _record_event(filename, f"driver {label} {filename}: {reason[:150]}")
    else:
        drive["status"] = "ended"
        _record_event(filename, f"{filename}'s drive ended"
                                + (f" (rc={rc})" if proc is not None else ""))
    _persist()


def stop() -> dict:
    global DRIVE
    with state.LOCK:
        if DRIVE is None or DRIVE["status"] not in ("starting", "up"):
            raise ValueError("nothing is being driven")
        DRIVE["status"] = "parked"
        pgid = DRIVE.get("pgid")
        filename = DRIVE["task"]
    _persist()
    _record_event(filename, f"parking {filename} — taking the app down…")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, TypeError):
        pass
    return public()
