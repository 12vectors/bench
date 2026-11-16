"""The page's own writes, on loopback and through a proxy (task 112, WP04).

Every person-operated POST now passes a door that accepts an `Origin`
only when it is this board on the port it serves, or exactly one of
`BOARD_ALLOWED_ORIGINS`. This case drives the real page — its own
`rawMove` and its two formerly bodyless writes — three ways against a
second disposable backend made here:

- **loopback**, at the backend's own URL: every write lands;
- **through an allowlisted proxy**: an isolated reverse proxy on another
  loopback port, forwarding every header as it came (so the backend sees
  the browser's own `Origin` and `Sec-Fetch-Site`), whose origin is the
  backend's one `BOARD_ALLOWED_ORIGINS` entry: every write lands;
- **through an unlisted proxy**: the same proxy on a third port whose
  origin is not listed: the page reads, and every write is refused with
  nothing on disk changed.

Nothing here changes a real device, an operator setting or a live
origin: the backend, both proxies and their ports are made for this case
and taken down with it. The shared backend the other cases read is not
touched.
"""

import http.client
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import benchverify as bv

S = bv.SESSION
CARD = "01-write-the-onboarding-guide.md"

# hop-by-hop headers a proxy does not forward (RFC 9110 §7.6.1)
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding",
       "te", "trailer", "upgrade"}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def proxy(upstream: dict, seen: list) -> ThreadingHTTPServer:
    """A reverse proxy on a loopback port of its own, forwarding to
    `upstream["port"]` with the request's headers as they came (minus
    hop-by-hop) and streaming the reply, so the page's event stream works
    through it. `seen` collects (path, Origin, Sec-Fetch-Site) for every
    POST it forwarded."""

    class Forward(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            if self.command == "POST":
                seen.append((self.path, self.headers.get("Origin"),
                             self.headers.get("Sec-Fetch-Site")))
            conn = http.client.HTTPConnection("127.0.0.1", upstream["port"],
                                              timeout=60)
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP}
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                conn.close()

        do_GET = _forward
        do_POST = _forward

    server = ThreadingHTTPServer(("127.0.0.1", 0), Forward)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class PageWritesOverLoopbackAndAProxy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        cls.upstream = {"port": None}
        cls.seen_allowed, cls.seen_unlisted = [], []
        # the proxies come first, so the backend can be told the one
        # origin it allows; each on its own loopback port, nothing beyond
        cls.allowed = proxy(cls.upstream, cls.seen_allowed)
        cls.unlisted = proxy(cls.upstream, cls.seen_unlisted)
        cls.allowed_origin = f"http://127.0.0.1:{cls.allowed.server_address[1]}"
        cls.unlisted_origin = f"http://127.0.0.1:{cls.unlisted.server_address[1]}"
        base = shared.base.parent / "proxy-fixture"
        shutil.rmtree(base, ignore_errors=True)
        env = dict(shared.env)
        env["BOARD_ALLOWED_ORIGINS"] = cls.allowed_origin
        # the shared backend's tool install holds the candidate's core as
        # copied for this run, so it is the root this one copies from too
        cls.backend = bv.Backend(shared.tool, base, env, shared.python)
        cls.upstream["port"] = cls.backend.port
        cls.backend.start()
        cls.origin = S["browser"].origin

    @classmethod
    def tearDownClass(cls):
        S["browser"].origin = cls.origin
        for server in (cls.allowed, cls.unlisted):
            server.shutdown()
            server.server_close()
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def where(self) -> str:
        tasks = self.backend.work / "tasks"
        for stage in ("backlog", "to-do", "in-progress", "review", "done"):
            if (tasks / stage / CARD).is_file():
                return stage
        return "missing"

    def open_at(self, origin: str):
        b = S["browser"]
        b.origin = origin
        b.viewport(1440, 900)
        b.goto("/")
        if not b.wait("!!document.querySelector('#board article.card"
                      f"[data-file=\"{CARD}\"]')", 15):
            raise bv.CheckFailed(f"the board never drew its cards at {origin}")
        return b

    @staticmethod
    def status_of(b, path: str) -> list:
        """The page posting a bodyless write the way it now does: its
        status and the error it was answered with."""
        # the page names its build on every write (task 124)
        return b.eval(f"fetch({path!r}, {{method: 'POST', headers: "
                      "{'Content-Type': 'application/json', 'X-Bench-Build': "
                      "document.querySelector('meta[name=\"bench-build\"]').content},"
                      " body: '{}'})"
                      ".then(async r => [r.status, (await r.json()).error || ''])")

    def write_round(self, b) -> dict:
        """The page's own writes: a move away and back through `rawMove`,
        and the two that used to send no body."""
        start = self.where()
        away = "to-do" if start == "backlog" else "backlog"
        moved = b.eval(f"rawMove({CARD!r}, {start!r}, {away!r})")
        after = self.where()
        back = b.eval(f"rawMove({CARD!r}, {away!r}, {start!r})") if moved else None
        return {"start": start, "moved": moved, "afterMove": after, "back": back,
                "park": self.status_of(b, "/api/drive/stop"),
                "undo": self.status_of(b, "/api/unarchive"),
                "end": self.where()}

    def assertLanded(self, got: dict):
        self.assertTrue(got["moved"], got)
        self.assertNotEqual(got["start"], got["afterMove"], got)
        self.assertTrue(got["back"], got)
        # nothing is driven and nothing was archived on this backend, so
        # both bodyless writes get their handler's own refusal — which
        # means each passed the door and reached the handler
        self.assertEqual([409, "nothing is being driven"], got["park"], got)
        self.assertEqual(409, got["undo"][0], got)
        self.assertIn("nothing to unarchive", got["undo"][1])
        self.assertEqual(got["start"], got["end"], got)

    def test_1_loopback_writes_land(self):
        b = self.open_at(self.backend.url)
        got = self.write_round(b)
        shot("proxy-writes-loopback")
        self.assertLanded(got)
        S["metrics"]["proxyWritesLoopback"] = got

    def test_2_an_allowlisted_proxy_writes_land(self):
        b = self.open_at(self.allowed_origin)
        got = self.write_round(b)
        shot("proxy-writes-allowlisted")
        self.assertLanded(got)
        # what the backend was actually sent: the browser's own origin,
        # forwarded unchanged — not loopback on the backend's port
        origins = {o for (_, o, _) in self.seen_allowed}
        sites = {s for (_, _, s) in self.seen_allowed}
        self.assertEqual({self.allowed_origin}, origins, self.seen_allowed)
        self.assertEqual({"same-origin"}, sites, self.seen_allowed)
        S["metrics"]["proxyWritesAllowlisted"] = {
            **got, "forwardedOrigins": sorted(origins),
            "posts": len(self.seen_allowed)}

    def test_3_an_unlisted_proxy_reads_but_cannot_write(self):
        b = self.open_at(self.unlisted_origin)
        start = self.where()
        moved = b.eval(f"rawMove({CARD!r}, {start!r}, 'in-progress')")
        refused = b.eval(
            "fetch('/api/move', {method: 'POST', headers: {'Content-Type': "
            f"'application/json'}}, body: JSON.stringify({{file: {CARD!r}, "
            f"from: {start!r}, to: 'in-progress'}})}})"
            ".then(async r => [r.status, (await r.json()).error])")
        park = self.status_of(b, "/api/drive/stop")
        shot("proxy-writes-unlisted-refused")
        self.assertFalse(moved)
        self.assertEqual(403, refused[0], refused)
        self.assertIn("BOARD_ALLOWED_ORIGINS", refused[1])
        self.assertEqual(403, park[0], park)
        self.assertIn("Nothing was changed", park[1])
        self.assertEqual(start, self.where(), "a refused write moved the card")
        self.assertTrue(self.seen_unlisted, "nothing went through the proxy")
        S["metrics"]["proxyWritesUnlisted"] = {
            "moved": moved, "status": refused[0], "park": park[0],
            "forwardedOrigins": sorted({o for (_, o, _) in self.seen_unlisted})}
