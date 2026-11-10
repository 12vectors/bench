"""Drives: launching the project's app from a task's worktree.

Core knows nothing about how any app starts — the project's driver does
(local/driver/start, an executable the project owns and evolves). The
contract:

  - env in: DRIVE_WORKTREE, DRIVE_BRANCH, DRIVE_TASK, DRIVE_REPO (the
    application's root, which is not the tool's and not the work state's)
  - exit non-zero quickly and whatever was printed becomes the refusal
    shown in the ticker ("port 5176 is yours", "branch has migrations")
  - print `DRIVE URL: <url>` when the app is up — the card links there
  - keep running until parked; the board SIGTERMs the process group

One drive at a time: you look at one version of the app.

A configured **preview** (task 91, `previews.py`) rides this same
lifecycle — one process group, one log, SIGTERM to park, re-adoption after
a restart — with a launch *spec* instead of the driver: an argument list
bench built from the operator's preview configuration, run in the card's
worktree. The spec adds four things the driver contract never had, and
only for previews, so a driver-only board behaves exactly as before:

  - the **source** it was started from (commit, branch, a digest of
    uncommitted work) and the configuration revision, recorded at start
    so a later commit or edit is read as *stale*, never as current;
  - a **readiness** probe on loopback: `up` means the configured check
    answered, not that a process exists or printed a line;
  - an **owner token** in the child's environment, so a restarted board
    re-adopts only a process group it can show is its own, and never
    signals one it cannot;
  - a **stop that escalates** — SIGTERM, a grace period, then SIGKILL of
    that one group — and reports whether the group is gone and the port
    released, rather than assuming either.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import config
import state

DRIVE: dict | None = None      # the current (or last) drive
URL_RE = re.compile(r"^DRIVE URL:\s*(\S+)", re.MULTILINE)
STATE_FILE = config.STATE / "drive.json"
# What survives a restart. Never a secret value: those exist only in the
# child's environment and in memory, for scrubbing the log.
PERSISTED = ("task", "status", "url", "started", "log", "pgid", "kind",
             "port", "source", "configRevision", "readiness", "ready",
             "grace", "owner", "worktree", "branch", "readyAt", "checkedAt")
PUBLIC = ("task", "status", "url", "started", "line", "reason", "kind",
          "port", "source", "configRevision", "readiness", "exitCode",
          "cleanup", "worktree", "branch", "checkedAt", "readyAt",
          "lastProbe")
LEGACY_PUBLIC = ("task", "status", "url", "started", "line", "reason")
OWNER_ENV = "BENCH_PREVIEW_OWNER"


def _persist() -> None:
    """The drive outlives the board (it is its own process group), so its
    identity lives on disk; a restarted board re-adopts it via adopt()."""
    import json
    try:
        if DRIVE and DRIVE["status"] in ("starting", "up"):
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(
                {k: DRIVE.get(k) for k in PERSISTED if k in DRIVE}))
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


def owned_by(pgid: int, token: str) -> bool | None:
    """Is this process group's leader the one bench started with `token`?
    True or False when /proc answers; None when it cannot be read — which
    is not a yes. A pid can be reused, and a board that signalled a
    stranger's process because a file named its number would be stopping
    an unrelated service."""
    if not token:
        return False
    try:
        raw = Path(f"/proc/{int(pgid)}/environ").read_bytes()
    except (OSError, ValueError):
        return None
    return f"{OWNER_ENV}={token}".encode() in raw.split(b"\0")


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Could something bind this loopback port now? Asked the way a dev
    server binds — SO_REUSEADDR, so a socket in TIME_WAIT is not read as
    held."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def probe(drive: dict, timeout: float = 2.0) -> dict:
    """One readiness question, asked over loopback: did the configured path
    answer with the configured status (and text)? The app answers a
    request like any browser's; bench runs nothing of its own here."""
    import urllib.error
    import urllib.request
    ready = drive.get("ready") or {}
    url = drive.get("url") or ""
    target = url.rstrip("/") + (ready.get("path") or "/")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    at = time.time()
    try:
        with opener.open(target, timeout=timeout) as resp:
            status, body = resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, b""
    except (OSError, ValueError) as exc:
        return {"ok": False, "at": at, "url": target,
                "answer": f"no answer ({type(exc).__name__})"}
    want = int(ready.get("status") or 200)
    text = ready.get("contains")
    if status != want:
        return {"ok": False, "at": at, "url": target, "status": status,
                "answer": f"answered {status}, expected {want}"}
    if text and text.encode("utf-8") not in body:
        return {"ok": False, "at": at, "url": target, "status": status,
                "answer": f"answered {status} without the expected text"}
    return {"ok": True, "at": at, "url": target, "status": status,
            "answer": f"answered {status}" + (" with the expected text"
                                              if text else "")}


def adopt() -> None:
    """Board startup: re-adopt a drive a previous board left running.
    The file can lie (crash, reboot) — believe it only if the process
    group is actually alive, and, for a preview, only if it is provably
    the one bench started."""
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
    if data.get("kind") == "preview":
        if not owned_by(int(data["pgid"]), str(data.get("owner") or "")):
            try:
                STATE_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            state.record_board_event({
                "kind": "agent", "actor": "board", "file": data.get("task"),
                "summary": (f"a preview process group ({data.get('pgid')}) "
                            f"recorded by an earlier board could not be shown "
                            f"to be bench's own — left alone, not signalled")})
            return
        # reconnecting is not being ready: ask again before saying so
        data["readiness"] = "rechecking"
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
    keys = PUBLIC if DRIVE.get("kind") == "preview" else LEGACY_PUBLIC
    return {k: DRIVE.get(k) for k in keys}


def current() -> dict | None:
    """The drive as this board holds it (the live record, not a copy)."""
    return DRIVE


def worktree_for(filename: str) -> tuple[str, str, Path]:
    """The card's stem, branch and worktree — recreated from the branch
    when it was cleaned up, refused when the directory is something
    else's checkout. One answer for the driver and the preview alike."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    # A phase card's work is its own integration branch, not task/<stem>
    # — one answer, in config, so this cannot drive a branch that was
    # never cut (task 56).
    branch = config.branch_for(stem)
    worktree = config.WORKTREES / stem
    mismatch = config.worktree_mismatch(worktree)
    if mismatch:
        raise ValueError(mismatch)
    if not worktree.exists():
        if subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--verify",
                           "--quiet", branch], capture_output=True).returncode != 0:
            raise ValueError(f"no worktree and no branch {branch} — nothing to drive")
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(config.REPO), "worktree", "add", str(worktree), branch],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"could not recreate the worktree: {result.stderr.strip()[:200]}")
    return stem, branch, worktree


def start(filename: str, spec: dict | None = None) -> dict:
    """Launch the driver for a card — or, with `spec`, the preview
    `previews.py` validated and built for it."""
    global DRIVE
    driver = config.driver_path()
    if spec is None and driver is None:
        raise ValueError(
            "no driver: create local/driver/start (see core/driver.example/) — "
            "an executable that launches this project's app from a worktree")
    with state.LOCK:
        if DRIVE and DRIVE["status"] in ("starting", "up"):
            raise ValueError(f"already driving {DRIVE['task']} — park it first")

    stem, branch, worktree = worktree_for(filename)

    config.DRIVES_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.DRIVES_DIR / f"{stem}-{time.strftime('%H%M%S')}.log"
    if spec is None:
        env = config.child_env()
        env["BOARD_PORT"] = str(state.serve_port)
        argv, cwd, what = [str(driver)], config.REPO, "the driver"
    else:
        env = dict(spec["env"])
        argv, cwd, what = list(spec["argv"]), worktree, "the preview"
    env.update({
        "DRIVE_WORKTREE": str(worktree),
        "DRIVE_BRANCH": branch,
        "DRIVE_TASK": filename,
        "DRIVE_REPO": str(config.REPO),
    })
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env,
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)  # own process group: park kills everything
    except OSError as exc:
        log_file.close()
        raise ValueError(f"could not launch {what}: {exc}")

    DRIVE = {"task": filename, "status": "starting", "url": None,
             "started": time.time(), "proc": proc, "log": str(log_path),
             "pgid": proc.pid}  # start_new_session: pid == pgid
    if spec is not None:
        DRIVE.update({
            "kind": "preview", "url": spec["url"], "port": spec["port"],
            "source": spec["source"], "configRevision": spec["configRevision"],
            "ready": spec["ready"], "readiness": "checking",
            "grace": spec.get("grace", 10), "owner": env.get(OWNER_ENV),
            "worktree": str(worktree), "branch": branch,
            "secrets": list(spec.get("secrets") or ())})
    _persist()
    _record_event(filename, f"{'preview' if spec else 'driver'} starting for "
                            f"{filename}")
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
    preview = drive.get("kind") == "preview"
    while _alive(drive):
        text = _tail(log)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            new_line = lines[-1][:200]
            if preview:
                import previews
                new_line = previews.scrub(new_line, drive.get("secrets"))
            if drive.get("line") != new_line:
                drive["line"] = new_line
                state.broadcast({"type": "board"})
        if preview:
            _ready_step(drive)
        else:
            match = URL_RE.search(text)
            if match and drive["status"] == "starting":
                drive["status"] = "up"
                drive["url"] = match.group(1)
                _persist()
                _record_event(filename, f"driving {filename} at {match.group(1)}")
        time.sleep(0.5 if preview else 1)
    if log_file is not None:
        log_file.close()
    if preview:
        _preview_ended(drive, proc.returncode if proc is not None else None)
        _persist()
        return
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


def _ready_step(drive: dict) -> None:
    """One beat of a preview's readiness: while starting, ask until the
    configured check answers or its timeout passes; after a reconnect, ask
    once before calling it ready again."""
    filename = drive["task"]
    if drive["status"] == "starting":
        answer = probe(drive)
        drive["lastProbe"] = answer
        if answer["ok"]:
            drive.update(status="up", readiness="ready", readyAt=time.time(),
                         checkedAt=answer["at"])
            _persist()
            _remember()
            _record_event(filename, f"preview of {filename} ready at "
                                    f"{drive['url']} — {answer['answer']}")
            return
        limit = float((drive.get("ready") or {}).get("timeout") or 60)
        if time.time() - drive["started"] > limit:
            drive.update(status="unready", readiness="unready",
                         checkedAt=answer["at"],
                         reason=(f"readiness timed out after {int(limit)}s: "
                                 f"{answer['answer']}"))
            _persist()
            _record_event(filename, f"preview of {filename} not ready — "
                                    f"{drive['reason']}; stopping the process "
                                    f"group bench started")
            _terminate(drive)
    elif drive["status"] == "up" and drive.get("readiness") == "rechecking":
        answer = probe(drive)
        drive.update(readiness="ready" if answer["ok"] else "unready",
                     checkedAt=answer["at"], lastProbe=answer)
        if not answer["ok"]:
            drive["reason"] = f"after reconnecting: {answer['answer']}"
        _persist()
        state.broadcast({"type": "board"})


def _preview_ended(drive: dict, rc: int | None) -> None:
    """Say how a preview ended, and what was left: the group gone or not,
    the port released or held by something bench did not start."""
    filename = drive["task"]
    gone = not _group_alive(drive.get("pgid"))
    if not gone:
        # the leader exited and left children in its group: they are
        # bench's own, so they come down with it
        _terminate(drive, wait=True)
        gone = not _group_alive(drive.get("pgid"))
    port = drive.get("port")
    released = port_free(int(port)) if port else None
    drive["cleanup"] = {
        "group": "gone" if gone else "still running",
        "port": (None if port is None else "released" if released else
                 "in use by something bench did not start — left alone"
                 if gone else "in use by the preview's own group")}
    drive["exitCode"] = rc
    _scrub_log(drive)
    if drive["status"] == "parked":
        drive["status"] = "stopped"
        _record_event(filename, f"preview of {filename} stopped — process "
                                f"group {drive['cleanup']['group']}, port "
                                f"{drive['cleanup']['port']}")
    elif drive["status"] == "unready":
        _record_event(filename, f"preview of {filename} is down after its "
                                f"readiness failure — process group "
                                f"{drive['cleanup']['group']}")
    elif drive["status"] == "starting":
        lines = [l.strip() for l in _tail(Path(drive["log"])).splitlines()
                 if l.strip()]
        import previews
        drive["status"] = "failed"
        drive["reason"] = previews.scrub(
            f"exited before it was ready (rc={rc}): "
            + (lines[-1][:160] if lines else "no output"), drive.get("secrets"))
        _record_event(filename, f"preview of {filename} failed to start — "
                                f"{drive['reason']}")
    else:
        drive["status"] = "exited"
        drive["reason"] = ("exited unexpectedly"
                           + (f" (rc={rc})" if rc is not None else ""))
        _record_event(filename, f"preview of {filename} {drive['reason']}")
    drive.pop("secrets", None)
    _remember()


def _remember() -> None:
    """The newest preview's outcome, kept by previews.py after the drive
    is gone."""
    import previews
    try:
        previews.record_last()
    except OSError:
        pass


def _scrub_log(drive: dict) -> None:
    """Once the process is gone its log is rewritten scrubbed: the secret
    values the preview was given, and anything credential-shaped."""
    import previews
    path = Path(drive.get("log") or "")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        clean = previews.scrub(text, drive.get("secrets"))
        if clean != text:
            path.write_text(clean, encoding="utf-8")
    except OSError:
        pass


def _group_alive(pgid) -> bool:
    try:
        os.killpg(int(pgid), 0)
        return True
    except (ProcessLookupError, PermissionError, TypeError, ValueError):
        return False


def _terminate(drive: dict, wait: bool = False) -> None:
    """SIGTERM the one group bench started; for a preview, SIGKILL it if it
    is still there after its grace period. Nothing else is signalled."""
    pgid = drive.get("pgid")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, TypeError):
        return
    if drive.get("kind") != "preview":
        return

    def escalate():
        deadline = time.time() + float(drive.get("grace") or 10)
        while time.time() < deadline:
            if not _group_alive(pgid):
                return
            time.sleep(0.1)
        try:
            os.killpg(pgid, signal.SIGKILL)
            drive["killed"] = True
        except (ProcessLookupError, PermissionError, TypeError):
            return
        for _ in range(50):
            if not _group_alive(pgid):
                return
            time.sleep(0.1)
    if wait:
        escalate()
    else:
        threading.Thread(target=escalate, daemon=True).start()


def stop() -> dict:
    global DRIVE
    with state.LOCK:
        if DRIVE is None or DRIVE["status"] not in ("starting", "up"):
            raise ValueError("nothing is being driven")
        DRIVE["status"] = "parked"
        filename = DRIVE["task"]
        drive = DRIVE
    _persist()
    _record_event(filename, f"parking {filename} — taking the app down…")
    _terminate(drive)
    return public()
