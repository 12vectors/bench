"""HTTP surface: the page, the JSON API, and the SSE stream."""

from __future__ import annotations

import json
import queue
import subprocess
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

import agents
import commands
import config
import drive
import events
import github
import state
import taskfiles


def state_payload() -> dict:
    with state.LOCK:
        sessions = sorted(
            (dict(m) for m in state.SESSIONS.values()),
            key=lambda m: m.get("last") or m.get("started") or 0, reverse=True)
        board_events = list(state.BOARD_EVENTS[-80:])
    return {
        "board": taskfiles.collect(),
        "sessions": sessions,
        "agents": agents.list_public(),
        "prs": github.public_state(),
        "drive": drive.public(),
        "hasDriver": config.driver_path() is not None,
        "branches": github.task_branches(),
        "commands": config.commands(),
        "commandRuns": commands.public(),
        "archivedCount": taskfiles.archived_count(),
        "boardEvents": board_events,
        "now": time.time(),
    }


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
            page = config.CORE / "board.html"
            if not page.is_file():
                self._send(500, b"board.html is missing", "text/plain")
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
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
                task = taskfiles.move_task(payload["file"], payload["from"], payload["to"])
                self._json(200, {"task": task})
            elif path == "/api/events":
                events.ingest_event(self._read_body())
                self._json(200, {"ok": True})
            elif path == "/api/agent/start":
                payload = self._read_body()
                agent = agents.start_agent(payload["file"], payload["stage"])
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
            elif path == "/api/pr/copilot":
                payload = self._read_body()
                url = github.request_copilot(payload["file"])
                self._json(200, {"url": url})
            elif path == "/api/task/complete":
                payload = self._read_body()
                self._json(200, github.complete_task(payload["file"], payload["from"]))
            elif path == "/api/archive":
                payload = self._read_body()
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
