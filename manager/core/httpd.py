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

import callers
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

#
# Since WP04 every POST passes a door, chosen by its route's class rather
# than by whether its handler remembered to call one. The policy door is
# the browser-mutation door plus the custom header; the run and event
# doors are the same door, because a bridge or a shim states no origin and
# a page on another origin has no business with either.
#
# Two things about the origin, because they used to be wrong:
#
# - "this board" is the port it actually serves on, not BOARD_PORT. A
#   board started with `--port`, or one that hopped, refused its own page.
# - `Origin: null` is refused. It is what a sandboxed frame, a `data:`
#   page or a file sends, and none of those is this board's page.
#
# Beyond loopback, an origin is accepted only when it is *exactly* one of
# BOARD_ALLOWED_ORIGINS (a local proxy's, say). This is protection from
# other pages in the operator's browser, not authentication: Host,
# Forwarded and X-Forwarded-* are never read to decide anything, and
# listing an origin admits no peer the proxy does not already admit.

SETTINGS_HEADER = "X-Bench-Settings"
SETTINGS_TOKEN = "1"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")

# The origin rule lives with the other caller checks (WP05), so an
# observation of an Origin in Settings says what this door would.
_origin_refusal = callers.origin_refusal

def write_problem(headers, port: int, *, what: str = "a write",
                  token: bool = False, allowed=None) -> str | None:
    """Why this request may not reach a mutation's handler, or None.

    Read from headers only, before the body is read: nothing in the body
    may make a request trusted — that is what a forged post would fill in.
    `port` is the port this board serves on; `allowed` defaults to
    BOARD_ALLOWED_ORIGINS. A request that states no Origin and no
    Sec-Fetch-Site — a shim, a bridge, `curl` — is not a browser, and is
    held to the content type (and the custom header, where `token`) only.
    """
    allowed = config.ALLOWED_ORIGINS if allowed is None else allowed
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return (f"{what} must be sent as application/json "
                f"(this was {ctype or 'nothing'}). A cross-origin form can "
                f"only send form encodings. Nothing was changed.")
    if token and (headers.get(SETTINGS_HEADER) or "").strip() != SETTINGS_TOKEN:
        return (f"{what} must carry the {SETTINGS_HEADER}: "
                f"{SETTINGS_TOKEN} header. A cross-origin form cannot set it "
                f"and a cross-origin fetch cannot send it without a "
                f"preflight this board does not answer. Nothing was changed.")
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return (f"this request states Sec-Fetch-Site: {site}, so it did not "
                f"come from this board's own page. Nothing was changed.")
    origin = headers.get("Origin")
    if origin is not None:
        refusal = _origin_refusal(origin.strip(), port, allowed)
        if refusal:
            return f"{refusal}. Nothing was changed."
    return None


def origin_problem(headers, port: int, token: bool = True,
                   allowed=None) -> str | None:
    """Why this request may not change policy, or None: the write door
    plus the custom header (`token=False` drops the header)."""
    return write_problem(headers, port, what="a policy change", token=token,
                         allowed=allowed)


def card_write_problem(headers, port: int, allowed=None) -> str | None:
    """Why this request may not write a card's discussion or ask for a
    refinement, or None (task 84) — the browser-mutation door, which every
    person-operated write now passes."""
    return write_problem(headers, port, what="a card write", allowed=allowed)


def states_a_browser(headers) -> bool:
    """Whether the request says it came from a browser. Every browser
    states an Origin on a cross-origin POST, and the fetch-metadata ones a
    Sec-Fetch-Site on every request; a shim or a bridge states neither."""
    return headers.get("Origin") is not None \
        or headers.get("Sec-Fetch-Site") is not None


def door_problem(klass: str, headers, port: int) -> str | None:
    """Why a request of this class may not reach its handler, or None.
    Read from headers only, before the body is read."""
    if klass == "read":
        return None
    if klass == "policy-mutation":
        return origin_problem(headers, port)
    return write_problem(headers, port)


def claim_problem(klass: str, headers, body: dict) -> str | None:
    """A browser request naming a run (WP04 §4.3 boundary). A run speaks
    through its shim, which states no origin; a page never names a run. So
    a request that says it is a browser's — whatever its origin — and
    carries an `agent` is refused: no route class lets a page borrow a
    run's frozen authority. Whether a shim's claim is *proved* is the run
    token's business (WP05), not the route class's."""
    if klass in ("browser-mutation", "policy-mutation") \
            and states_a_browser(headers) and body.get("agent"):
        return ("a browser request cannot speak for a run — a run asks "
                "through its own shim, which states no origin. Nothing was "
                "changed.")
    return None


# ── user files are documents, not pages of this board (WP05) ───────────
#
# plans/ and reference/ hold what people and agents wrote. Served as they
# were, an .html or .svg among them ran its scripts as this origin, and so
# could call every API the page can. Now every file is sent with nosniff,
# so nothing is read as a type it was not served as (an unknown suffix is
# octet-stream and downloads), and the two active types are sent with a
# CSP sandbox: an opaque origin, no script, no form, no plugin. They still
# render — markup, styles and images — and a reference document that needs
# script will not run it (plan §9.2, decision 6). A PDF is left out of the
# sandbox only because Chromium's viewer is a plugin the sandbox refuses;
# nosniff still holds it to the type it was served as.
ACTIVE_SUFFIXES = (".html", ".svg")
SANDBOX_POLICY = ("sandbox; script-src 'none'; object-src 'none'; "
                  "base-uri 'none'; form-action 'none'")


def file_headers(suffix: str) -> dict:
    """The headers one served user file is sent with."""
    headers = {"X-Content-Type-Options": "nosniff"}
    if suffix.lower() in ACTIVE_SUFFIXES:
        headers["Content-Security-Policy"] = SANDBOX_POLICY
    return headers


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

    def send(self, code: int, body: bytes, content_type: str,
             headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        headers = dict(headers or {})
        # no-store unless a route says otherwise — which only the asset
        # route does, for a build's immutable files (task 123)
        self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
        for name, value in headers.items():
            self.send_header(name, value)
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

    def _host_refused(self, method: str) -> bool:
        """The Host check (WP05): observed on every request, refused only
        where BOARD_HOST_CHECK is enforce. A refusal reads and changes
        nothing."""
        problem = callers.check_request(self.headers, self.served_port(),
                                        method)
        if problem is None:
            return False
        if method == "POST":
            self.reply(421, {"error": problem, "code": "host-unlisted"})
        else:
            self.send(421, problem.encode("utf-8"),
                      "text/plain; charset=utf-8")
        return True

    def do_GET(self) -> None:
        raw = self.path
        if self._host_refused("GET"):
            return
        route = self._route("GET")
        if route is None:
            self.path = raw
            self.send(404, b"not found", "text/plain")
            return
        route.handler(self)

    def do_POST(self) -> None:
        if self._host_refused("POST"):
            return
        route = self._route("POST")
        if route is None:
            self.send(404, b"not found", "text/plain")
            return
        problem = door_problem(route.klass, self.headers, self.served_port())
        if problem:
            self.reply(403, {"error": problem})
            return
        try:
            self.body = self._read_body(route.max_body)
        except BodyRefused as refused:
            self.reply(refused.status, {"error": refused.why,
                                        "code": refused.code})
            return
        problem = claim_problem(route.klass, self.headers, self.body)
        if problem:
            self.reply(403, {"error": problem, "code": "browser-claims-run"})
            return
        # A shim's claim to be a run is proved by that run's own token
        # (WP05) — reported, or refused where BOARD_RUN_IDENTITY enforces.
        try:
            self.body = callers.claim(self.url.path, self.body)
        except callers.ClaimRefused as refused:
            self.reply(403, {"error": refused.why, "code": refused.code})
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
        suffix = file_path.suffix.lower()
        ctype = self._MIME.get(suffix, "application/octet-stream")
        self.send(200, file_path.read_bytes(), ctype, file_headers(suffix))

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
