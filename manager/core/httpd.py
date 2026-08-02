"""HTTP surface: the page, the JSON API, and the SSE stream."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import time
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

import agents
import commands
import config
import drive
import events
import github
import phases
import state
import sync
import taskfiles


def state_payload() -> dict:
    with state.LOCK:
        sessions = sorted(
            (dict(m) for m in state.SESSIONS.values()),
            key=lambda m: m.get("last") or m.get("started") or 0, reverse=True)
        board_events = list(state.BOARD_EVENTS[-80:])
    return {
        "board": taskfiles.collect(),
        "project": config.PROJECT,
        "sessions": sessions,
        "agents": agents.list_public(),
        "prs": github.public_state(),
        # what the last pass of the phase runner saw: per phase card in
        # in-progress/, its branch, whether a run is in force or halted (and
        # why, and where), and each member's state — the header chip and the
        # phase card's own list are both read from here
        "phases": phases.public_state(),
        "drive": drive.public(),
        "hasDriver": config.driver_path() is not None,
        "branches": github.task_branches(),
        "commands": config.commands(),
        "commandRuns": commands.public(),
        # cards this board is midway through merging and cleaning up: the
        # busy state renders from here, not from what a tab happened to click
        "completing": state.completing_public(),
        "checks": config.checks(),
        # who this board is, so a card can tell "yours" from "someone
        # else's". Empty outside team mode: nothing claims anything there.
        "me": taskfiles.actor_name() if config.COMMIT_MOVES else "",
        "archivedCount": taskfiles.archived_count(),
        # which stages an archive may come from — the same set `archive_task`
        # refuses anything outside, sent so the card's ⌸ chip and the drag
        # gesture offer exactly what the server will do, from one authority
        "archiveFrom": sorted(taskfiles.ARCHIVE_FROM,
                              key=lambda slug: taskfiles.STAGE_ORDER.get(slug, 99)),
        "sync": sync.status(),
        "boardEvents": board_events,
        "now": time.time(),
    }


_TITLE = re.compile(rb"<title>.*?</title>", re.DOTALL)


def page_bytes() -> bytes:
    """board.html with the project's name rendered into its <title>, so the
    tab reads right on first paint rather than after the first state load."""
    html = (config.CORE / "board.html").read_bytes()
    title = escape(f"{config.PROJECT} · bench").encode("utf-8")
    return _TITLE.sub(lambda _: b"<title>" + title + b"</title>", html, count=1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html", "/board.html"):
            if not (config.CORE / "board.html").is_file():
                self._send(500, b"board.html is missing", "text/plain")
                return
            self._send(200, page_bytes(), "text/html; charset=utf-8")
        elif path == "/api/tasks":
            self._json(200, taskfiles.collect())
        elif path == "/api/state":
            self._json(200, state_payload())
        elif path == "/api/session":
            sid = (parse_qs(url.query).get("id") or [""])[0]
            with state.LOCK:
                meta = dict(state.SESSIONS.get(sid) or {})
            self._json(200, {"meta": meta, "events": events.session_events(sid)})
        elif path == "/api/diff":
            agent_id = (parse_qs(url.query).get("agent") or [""])[0]
            try:
                self._json(200, agents.agent_diff(agent_id))
            except (ValueError, subprocess.SubprocessError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/stream":
            self._stream()
        elif path.startswith("/files/"):
            self._extra_file(path)
        else:
            self._send(404, b"not found", "text/plain")

    _MIME = {".html": "text/html; charset=utf-8",
             ".md": "text/markdown; charset=utf-8",
             ".txt": "text/plain; charset=utf-8",
             ".json": "application/json",
             ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".svg": "image/svg+xml", ".pdf": "application/pdf"}

    def _extra_file(self, path: str) -> None:
        """Serve plans/ and reference/ files so the UI can open them."""
        parts = path.split("/", 3)
        if len(parts) != 4 or parts[2] not in ("plans", "reference"):
            self._send(404, b"not found", "text/plain")
            return
        name = unquote(parts[3])
        if "/" in name or ".." in name or name.startswith("."):
            self._send(404, b"not found", "text/plain")
            return
        file_path = config.TM_ROOT / parts[2] / name
        if not file_path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = self._MIME.get(file_path.suffix.lower(), "application/octet-stream")
        self._send(200, file_path.read_bytes(), ctype)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=500)
        with state.LOCK:
            state.CLIENTS.add(q)
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with state.LOCK:
                state.CLIENTS.discard(q)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path == "/api/move":
                payload = self._read_body()
                # a phase card stands for cards this view no longer draws,
                # so it does not move while one of them has an agent in it
                # — the refusal names ‖ hold, which stops both
                phases.assert_not_working(payload["file"])
                task = taskfiles.move_task(payload["file"], payload["from"], payload["to"])
                self._json(200, {"task": task})
            elif path == "/api/events":
                events.ingest_event(self._read_body())
                self._json(200, {"ok": True})
            elif path == "/api/agent/start":
                payload = self._read_body()
                # takeover: the second, deliberate click on a card someone
                # else holds — never the default a stale card face sends
                agent = agents.start_agent(payload["file"], payload["stage"],
                                           bool(payload.get("takeover")))
                self._json(200, {"agent": agent})
            elif path == "/api/agent/review":
                payload = self._read_body()
                agent = agents.start_review(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/agent/review-pr":
                payload = self._read_body()
                agent = agents.start_pr_review(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/agent/act-pr":
                payload = self._read_body()
                agent = agents.start_pr_fix(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/phase/run":
                payload = self._read_body()
                # takeover carries the same meaning it does for a launch:
                # the deliberate second click on someone else's card
                self._json(200, {"phase": phases.start_phase(
                    payload["file"], payload["stage"],
                    bool(payload.get("takeover")))})
            elif path == "/api/phase/stop":
                payload = self._read_body()
                # ‖ hold on a phase card: the run stops, nothing is unwound
                self._json(200, {"phase": phases.stop_phase(payload["file"],
                                                            payload["stage"])})
            elif path == "/api/phase/add":
                payload = self._read_body()
                # one line into the phase card, nothing into the card added:
                # membership lives in one place and joining one is not a move
                result = taskfiles.add_to_phase(payload["file"], payload["stage"],
                                                payload["phase"])
                state.record_board_event({
                    "kind": "phase", "actor": "you", "file": result["phase"],
                    "summary": f"{result['phase']} gained {result['entry']}"})
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/pr/open":
                payload = self._read_body()
                self._json(200, {"url": github.open_pr_now(payload["file"])})
            elif path == "/api/pr/copilot":
                payload = self._read_body()
                url = github.request_copilot(payload["file"])
                self._json(200, {"url": url})
            elif path == "/api/task/complete":
                payload = self._read_body()
                # merge & clean up ends with a move to done/, so the same
                # guard: a phase whose member is still working would take
                # its branch to main without that member's work in it
                phases.assert_not_working(payload["file"], "merge it")
                self._json(200, github.complete_task(payload["file"], payload["from"]))
            elif path == "/api/archive":
                payload = self._read_body()
                # archiving is a move, so it takes the same guard — and it
                # is the likeliest one: a phase you have given up on is
                # exactly the one you would tidy away mid-run
                phases.assert_not_working(payload["file"], "archive it")
                result = taskfiles.archive_task(payload["file"], payload["from"])
                with state.LOCK:
                    state.LAST_ARCHIVED = result
                state.record_board_event({
                    "kind": "move", "file": result["file"], "from": result["from"],
                    "to": "archive", "actor": "you",
                    "summary": f"{result['file']} archived (from {result['from']}/) — ⌘Z brings it back"})
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/unarchive":
                with state.LOCK:
                    last = state.LAST_ARCHIVED
                    state.LAST_ARCHIVED = None
                if not last:
                    raise ValueError("nothing to unarchive — the undo covers the last archive this board made")
                result = taskfiles.unarchive_task(last["file"], last["from"])
                state.record_board_event({
                    "kind": "move", "file": result["file"], "from": "archive",
                    "to": result["to"], "actor": "you",
                    "summary": f"{result['file']} brought back to {result['to']}/"})
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/command/run":
                payload = self._read_body()
                self._json(200, commands.run(payload["name"], payload["file"]))
            elif path == "/api/drive/start":
                payload = self._read_body()
                self._json(200, {"drive": drive.start(payload["file"])})
            elif path == "/api/drive/stop":
                self._json(200, {"drive": drive.stop()})
            elif path == "/api/agent/stop":
                payload = self._read_body()
                agent = agents.stop_agent(payload["id"])
                self._json(200, {"agent": agent})
            else:
                self._send(404, b"not found", "text/plain")
        except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
            self._json(409, {"error": str(exc)})
