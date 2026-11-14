"""HTTP surface: the socket, the doors, the body, and the SSE stream.

What each path does lives in `routes.py`, one handler per route, with the
request class it belongs to. This module finds a request's route, applies
the protection that class names, reads and checks a POST's body, and only
then calls the handler — so no handler is reached by a request its class
would refuse, and none is reached with a body that is not a JSON object
of a bounded size.
"""

from __future__ import annotations

import json
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: F401
from urllib.parse import parse_qs, unquote, urlparse

import config
import routes
import state

# Re-exported: these payloads are what the page and the tests read, and
# they were always reached as `httpd.<name>`.
from routes import (doctor_payload, gates_payload, page_bytes,  # noqa: F401
                    policy_payload, run_diagnostic, state_payload)


# ── writing a policy is an authority boundary ──────────────────────────
#
# Everything else this server does is bookkeeping about work: a move, a
# launch, a verification request. Changing the execution policy is
# different in kind — it is what *decides* what a launch may do and what a
# merge must have — so the one mutation on this board that edits policy is
# held to a stricter door than the rest, and the door is checked in the
# server rather than in the page.
#
# The board binds to loopback and has no authentication, which is the
# whole threat model here: a page on another origin, opened in the same
# browser, can post to `http://127.0.0.1:26071` without ever reading a
# reply. Three things together refuse that, and none of them trusts a
# field in the body:
#
# - a JSON content type, so a cross-origin HTML form (which may only send
#   form encodings) is not a request this will parse;
# - a custom header, which a form cannot set at all and a cross-origin
#   `fetch` can only set by asking for a CORS preflight — and nothing
#   here answers `OPTIONS`, so the preflight fails and the real request
#   is never sent;
# - an `Origin`/`Sec-Fetch-Site` check, for the browsers that send them:
#   a stated origin that is not this board's is refused by name.
#
# A loopback client that is not a browser — `curl`, a script, a test —
# sends the header and is allowed, which is correct: it already has the
# operator's own filesystem access and could edit the file directly.

SETTINGS_HEADER = "X-Bench-Settings"
SETTINGS_TOKEN = "1"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def origin_problem(headers, port: int, token: bool = True) -> str | None:
    """Why this request may not change policy, or None.

    Read from headers only. Nothing in the body may make a request
    trusted: that is what a forged post would fill in. `token=False` is
    the card-write door, which asks everything but the custom header.
    """
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return (f"a policy change must be sent as application/json "
                f"(this was {ctype or 'nothing'}). Nothing was changed.")
    if token and (headers.get(SETTINGS_HEADER) or "").strip() != SETTINGS_TOKEN:
        return (f"a policy change must carry the {SETTINGS_HEADER}: "
                f"{SETTINGS_TOKEN} header. A cross-origin form cannot set it "
                f"and a cross-origin fetch cannot send it without a "
                f"preflight this board does not answer. Nothing was changed.")
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return (f"this request states Sec-Fetch-Site: {site}, so it did not "
                f"come from this board's own page. Nothing was changed.")
    origin = (headers.get("Origin") or "").strip()
    if origin and origin.lower() != "null":
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return (f"this request states Origin: {origin}, which is not this "
                    f"board. Nothing was changed.")
        if parsed.port and parsed.port != port:
            return (f"this request states Origin: {origin}, which is a "
                    f"different board on this machine. Nothing was changed.")
    return None


def card_write_problem(headers, port: int) -> str | None:
    """Why this request may not write a card's discussion or ask for a
    refinement, or None (task 84).

    The policy door minus its custom header, which the card's own page has
    never sent: a JSON content type — so a cross-origin form, which can
    only send form encodings, is not parsed, and a cross-origin `fetch`
    needs a preflight nothing here answers — and, from browsers that state
    them, an origin that is this board's. A comment can start a
    refinement, so it is held to more than the body's word."""
    problem = origin_problem(headers, port, token=False)
    return problem.replace("a policy change", "a card write") if problem else None


def door_problem(klass: str, headers, port: int) -> str | None:
    """Why a request of this class may not reach its handler, or None.
    Read from headers only, before the body is read."""
    if klass == "policy-mutation":
        return origin_problem(headers, port)
    if klass == "card-write":
        return card_write_problem(headers, port)
    return None


class BodyRefused(Exception):
    def __init__(self, status: int, code: str, why: str):
        super().__init__(why)
        self.status, self.code, self.why = status, code, why


# Bytes read and thrown away from an oversized body so the client sees the
# 413 rather than a reset connection. Beyond it the connection is closed
# with the rest unread: a request this size is not one the page makes.
_DRAIN_LIMIT = 4 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        pass

    # ── the surface a handler in routes.py uses ────────────────────────

    def send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def reply(self, code: int, payload: dict) -> None:
        self.send(code, json.dumps(payload).encode("utf-8"), "application/json")

    # the names this class had before routes.py, kept for anything
    # outside core that still calls them
    _send = send
    _json = reply

    def served_port(self) -> int:
        """The port this board actually serves on — which is not always
        BOARD_PORT: `board.py --port` and a hop to a free port both serve
        elsewhere."""
        return int(self.server.server_address[1])

    # ── dispatch ───────────────────────────────────────────────────────

    def _route(self, method: str):
        self.url = urlparse(self.path)
        self.query = parse_qs(self.url.query)
        self.body = {}
        route = routes.find(method, self.url.path)
        # handlers read the path they were dispatched on, so a prefix
        # route sees the request's own
        self.path = self.url.path
        return route

    def do_GET(self) -> None:
        raw = self.path
        route = self._route("GET")
        if route is None:
            self.path = raw
            self.send(404, b"not found", "text/plain")
            return
        route.handler(self)

    def do_POST(self) -> None:
        route = self._route("POST")
        if route is None:
            self.send(404, b"not found", "text/plain")
            return
        problem = door_problem(route.klass, self.headers, config.PORT)
        if problem:
            self.reply(403, {"error": problem})
            return
        try:
            self.body = self._read_body(route.max_body)
        except BodyRefused as refused:
            self.reply(refused.status, {"error": refused.why,
                                        "code": refused.code})
            return
        try:
            route.handler(self)
        except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
            self.reply(409, {"error": str(exc)})

    # ── the body ───────────────────────────────────────────────────────

    def _read_body(self, cap: int) -> dict:
        """A POST's body: a JSON object of at most `cap` bytes, or a
        refusal — decided before any handler runs, so nothing is changed
        by a body this board would not parse. An absent body is `{}`, as
        it always was."""
        if self.headers.get("Transfer-Encoding"):
            raise BodyRefused(411, "length-required",
                              "a request body must state its Content-Length; "
                              "this one is chunked. Nothing was changed.")
        stated = self.headers.get("Content-Length")
        raw = (stated or "").strip()
        if stated is None:
            length = 0
        elif not raw.isdigit():
            raise BodyRefused(400, "bad-length",
                              f"Content-Length {raw[:40]!r} is not a "
                              f"non-negative whole number. Nothing was changed.")
        else:
            length = int(raw)
        if length > cap:
            if length <= _DRAIN_LIMIT:
                self.rfile.read(length)
            else:
                self.close_connection = True
            raise BodyRefused(413, "too-long",
                              f"a request body here is at most {cap} bytes, "
                              f"and this one is {length}. Nothing was changed.")
        data = self.rfile.read(length) if length else b""
        if not data.strip():
            return {}
        try:
            body = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BodyRefused(400, "schema", "the body is not JSON. "
                                             "Nothing was changed.")
        if not isinstance(body, dict):
            raise BodyRefused(400, "schema",
                              f"the body must be a JSON object, and this one "
                              f"is {type(body).__name__}. Nothing was changed.")
        return body

    # ── the two GETs that are not JSON replies ─────────────────────────

    _MIME = {".html": "text/html; charset=utf-8",
             ".md": "text/markdown; charset=utf-8",
             ".txt": "text/plain; charset=utf-8",
             ".json": "application/json",
             ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".svg": "image/svg+xml", ".pdf": "application/pdf"}

    def extra_file(self, path: str) -> None:
        """Serve plans/ and reference/ files so the UI can open them."""
        parts = path.split("/", 3)
        if len(parts) != 4 or parts[2] not in ("plans", "reference"):
            self.send(404, b"not found", "text/plain")
            return
        name = unquote(parts[3])
        if "/" in name or ".." in name or name.startswith("."):
            self.send(404, b"not found", "text/plain")
            return
        file_path = config.work_root() / parts[2] / name
        if not file_path.is_file():
            self.send(404, b"not found", "text/plain")
            return
        ctype = self._MIME.get(file_path.suffix.lower(), "application/octet-stream")
        self.send(200, file_path.read_bytes(), ctype)

    def stream(self) -> None:
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
