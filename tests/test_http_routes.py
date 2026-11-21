"""Every route is declared, and every POST body is checked before it is
acted on (task 112, WP04a).

`routes.py` declares each route with its request class; `httpd.py` finds
the route, applies the class's door and reads the body — a JSON object of
a bounded size — before any handler runs. These run a real board on a
loopback port (the fixture `test_settings_security` uses, with the
inherited `BOARD_*`, `AGENT_*` and `BENCH_*` variables removed) and probe
the one thing each refusal promises: nothing was changed.

    python3 -m unittest tests.test_http_routes -v
"""

from __future__ import annotations

import http.client
import json
import unittest

from tests.test_settings_security import Board

CARD = "01-a-card.md"


class _ClassCleanup:
    """Board wants `case.addCleanup`; a class-scoped board cleans up with
    the class."""

    def __init__(self, cls):
        self.addCleanup = cls.addClassCleanup


def raw_post(board: Board, path: str, body: bytes, headers: dict) -> tuple[int, dict]:
    """A POST with headers exactly as given — no Content-Length computed
    for us, so a lying or malformed one can be sent."""
    conn = http.client.HTTPConnection("127.0.0.1", board.port, timeout=20)
    try:
        conn.putrequest("POST", path, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        data = resp.read()
        try:
            return resp.status, json.loads(data or b"{}")
        except json.JSONDecodeError:
            return resp.status, {"body": data.decode("utf-8", "replace")}
    finally:
        conn.close()


class BodiesAreCheckedBeforeAnythingHappens(unittest.TestCase):
    """A move is the probe: if any of these reached its handler the card
    would leave review/."""

    @classmethod
    def setUpClass(cls):
        cls.board = Board(_ClassCleanup(cls))
        cls.card = cls.board.work / "tasks" / "review" / CARD
        cls.move = {"file": CARD, "from": "review", "to": "in-progress"}

    def assertUnmoved(self):
        self.assertTrue(self.card.is_file(), "the card moved")
        self.assertFalse((self.board.work / "tasks" / "in-progress" / CARD).exists())

    def post(self, body: bytes, **headers):
        sent = {"Content-Type": "application/json",
                "Content-Length": str(len(body)), **headers}
        return raw_post(self.board, "/api/move", body, sent)

    def test_an_oversized_body_is_refused_unread(self):
        padded = dict(self.move, pad="x" * (1024 * 1024 + 10))
        code, data = self.post(json.dumps(padded).encode())
        self.assertEqual(413, code, data)
        self.assertEqual("too-long", data["code"])
        self.assertIn("Nothing was changed", data["error"])
        self.assertUnmoved()

    def test_a_lying_content_length_beyond_the_cap_is_refused(self):
        body = json.dumps(self.move).encode()
        code, data = raw_post(self.board, "/api/move", body, {
            "Content-Type": "application/json",
            "Content-Length": str(64 * 1024 * 1024)})
        self.assertEqual(413, code, data)
        self.assertUnmoved()

    def test_malformed_json_is_refused(self):
        code, data = self.post(b'{"file": "01-a-card.md", "from":')
        self.assertEqual(400, code, data)
        self.assertEqual("schema", data["code"])
        self.assertUnmoved()

    def test_a_body_that_is_not_utf8_is_refused(self):
        code, data = self.post(b'{"file": "\xff\xfe"}')
        self.assertEqual(400, code, data)
        self.assertUnmoved()

    def test_json_that_is_not_an_object_is_refused(self):
        for body in (json.dumps([self.move]), '"01-a-card.md"', "42", "null",
                     "true"):
            with self.subTest(body=body):
                code, data = self.post(body.encode())
                self.assertEqual(400, code, data)
                self.assertEqual("schema", data["code"])
                self.assertIn("JSON object", data["error"])
        self.assertUnmoved()

    def test_a_negative_content_length_is_refused(self):
        body = json.dumps(self.move).encode()
        code, data = raw_post(self.board, "/api/move", body, {
            "Content-Type": "application/json", "Content-Length": "-5"})
        self.assertEqual(400, code, data)
        self.assertEqual("bad-length", data["code"])
        self.assertUnmoved()

    def test_a_non_numeric_content_length_is_refused(self):
        body = json.dumps(self.move).encode()
        for value in ("abc", "12abc", "1e3", "0x10", " "):
            with self.subTest(length=value):
                code, data = raw_post(self.board, "/api/move", body, {
                    "Content-Type": "application/json", "Content-Length": value})
                self.assertIn(code, (400,), data)
        self.assertUnmoved()

    def test_a_chunked_body_is_refused(self):
        payload = json.dumps(self.move).encode()
        chunked = b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload)
        code, data = raw_post(self.board, "/api/move", chunked, {
            "Content-Type": "application/json", "Transfer-Encoding": "chunked"})
        self.assertEqual(411, code, data)
        self.assertUnmoved()

    def test_the_progress_route_keeps_its_own_smaller_cap(self):
        body = json.dumps({"agent": "x", "token": "y",
                           "doing": "x" * (17 * 1024)}).encode()
        code, data = raw_post(self.board, "/api/progress", body, {
            "Content-Type": "application/json",
            "Content-Length": str(len(body))})
        self.assertEqual(413, code, data)
        self.assertEqual("too-long", data["code"])

    def test_an_empty_body_is_still_an_empty_object(self):
        """Bodyless POSTs keep working: an absent body reads as `{}`. The
        undo with nothing archived answers its own refusal (409), not a
        body error."""
        code, data = raw_post(self.board, "/api/unarchive", b"", {
            "Content-Type": "application/json", "Content-Length": "0"})
        self.assertEqual(409, code, data)
        self.assertIn("nothing to unarchive", data["error"])

    def test_an_unknown_post_is_not_found_and_reads_nothing(self):
        code, _ = raw_post(self.board, "/api/no-such-route", b"{}", {
            "Content-Type": "application/json", "Content-Length": "2"})
        self.assertEqual(404, code)

    def test_options_is_never_answered(self):
        """Nothing answers a CORS preflight, so a cross-origin fetch that
        needs one is never sent."""
        conn = http.client.HTTPConnection("127.0.0.1", self.board.port, timeout=20)
        try:
            conn.request("OPTIONS", "/api/move", headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type"})
            resp = conn.getresponse()
            resp.read()
            self.assertNotIn(resp.status, (200, 204))
            self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))
            self.assertIsNone(resp.getheader("Access-Control-Allow-Methods"))
        finally:
            conn.close()

    def test_a_well_formed_move_still_moves(self):
        """And the checks refuse only what they name: last, because it
        moves the card the others probe with."""
        code, data = self.post(json.dumps(
            {"file": CARD, "from": "review", "to": "review"}).encode())
        # a move to the stage it is in is the move's own business — the
        # point is that the body reached the handler
        self.assertNotIn(code, (400, 411, 413), data)


# ── WP04b: the door every person-operated write passes ─────────────────

ALLOWED = "https://bench.example.ts.net"

# Each of these is one character, one label or one scheme away from an
# origin this board accepts, and each is refused.
NEAR_MISSES = (
    "https://bench.example.ts.net.evil.example",
    "https://evil-bench.example.ts.net",
    "https://example.ts.net",
    "http://bench.example.ts.net",
    "https://bench.example.ts.net:8443",
    "https://bench.example.ts.net/",
    "https://user@bench.example.ts.net",
)


def mutation_routes() -> dict[str, str]:
    """`path` → class for every POST, read from the table the server
    dispatches by — so a route added later is covered without editing
    this test."""
    from tests.test_architecture import CORE, routes
    return {key.split(" ", 1)[1]: klass
            for key, (klass, _) in routes(CORE / "routes.py").items()
            if key.startswith("POST ")}


def snapshot(board: Board) -> dict:
    """What a write could change: every file under the work state, the
    policy document and the local settings."""
    found = {}
    for root in (board.work, board.tool / "manager" / "local"):
        for path in sorted(root.rglob("*")):
            if path.is_file() and "state" not in path.relative_to(root).parts[:1] \
                    and ".git" not in path.parts:
                found[str(path)] = path.read_bytes()
    return found


# A body plausible for any route: nothing here is meant to be valid, only
# to show that a refusal happens at the door whatever the body says.
PROBE = {"file": CARD, "stage": "review", "from": "review",
         "to": "in-progress", "name": "x", "id": "x", "check": "unit",
         "phase": "02-x.md", "scope": "acceptance", "head": "0" * 40,
         "document": {"v": 1}, "revision": ""}


class EveryMutationRefusesAHostilePage(unittest.TestCase):
    """Every POST in the table, not a sample: a cross-origin form, a
    no-CORS fetch, `Origin: null`, a foreign Sec-Fetch-Site and every near
    miss of an allowlisted origin are refused, and nothing on disk moves."""

    @classmethod
    def setUpClass(cls):
        cls.board = Board(_ClassCleanup(cls),
                          env={"BOARD_ALLOWED_ORIGINS": ALLOWED})
        cls.routes = mutation_routes()

    def refuse_everywhere(self, headers: dict, body: bytes | None = None):
        before = snapshot(self.board)
        data = body if body is not None else json.dumps(PROBE).encode()
        for path, klass in sorted(self.routes.items()):
            sent = dict(headers)
            if klass == "policy-mutation":
                sent.setdefault("X-Bench-Settings", "1")
            sent["Content-Length"] = str(len(data))
            with self.subTest(path=path, headers=headers):
                code, reply = raw_post(self.board, path, data, sent)
                self.assertEqual(403, code, reply)
                self.assertIn("Nothing was changed", reply["error"])
        self.assertEqual(before, snapshot(self.board))

    def test_the_table_covers_every_person_operated_write(self):
        classes = set(self.routes.values())
        self.assertLessEqual(classes, {"browser-mutation", "policy-mutation",
                                       "run-authenticated", "event-ingest"})
        # task 137 added seven orchestration writes, all policy-mutation;
        # task 140 two person writes (answer, withdraw), browser-mutation;
        # task 141 one, settling a held operation, policy-mutation;
        # task 143 one, adopting or releasing a phase, policy-mutation;
        # task 144 one, resolving an incident or resetting the breaker,
        # policy-mutation; task 158 six Brain writes (create, edit,
        # classify, override, retire, retry), browser-mutation; task 181
        # three (ask, retry, cancel a question), browser-mutation
        self.assertEqual(90, len(self.routes))
        self.assertEqual(35, sum(1 for k in self.routes.values()
                                 if k == "policy-mutation"))

    def test_a_cross_origin_form_is_refused(self):
        self.refuse_everywhere(
            {"Content-Type": "application/x-www-form-urlencoded",
             "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site",
             "Sec-Fetch-Mode": "navigate"},
            body=b"file=01-a-card.md&from=review&to=in-progress")

    def test_a_no_cors_fetch_is_refused(self):
        """text/plain is the content type a no-CORS fetch may send with a
        JSON-looking body and no preflight."""
        self.refuse_everywhere(
            {"Content-Type": "text/plain;charset=UTF-8",
             "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site",
             "Sec-Fetch-Mode": "no-cors"})

    def test_a_json_request_from_another_origin_is_refused(self):
        self.refuse_everywhere({"Content-Type": "application/json",
                                "Origin": "https://evil.example"})

    def test_origin_null_is_refused(self):
        self.refuse_everywhere({"Content-Type": "application/json",
                                "Origin": "null"})

    def test_a_same_site_page_is_refused(self):
        self.refuse_everywhere({"Content-Type": "application/json",
                                "Sec-Fetch-Site": "same-site"})

    def test_near_misses_of_an_allowed_origin_are_refused(self):
        for origin in NEAR_MISSES:
            self.refuse_everywhere({"Content-Type": "application/json",
                                    "Origin": origin})

    def test_near_misses_of_this_board_are_refused(self):
        port = self.board.port
        for origin in (f"http://127.0.0.1:{port + 1}", "http://127.0.0.1",
                       "http://localhost", f"https://127.0.0.1:{port}",
                       f"http://127.0.0.1.evil.example:{port}",
                       f"http://localhost.evil.example:{port}"):
            self.refuse_everywhere({"Content-Type": "application/json",
                                    "Origin": origin})


class ThePageAndTheShimsStillWrite(unittest.TestCase):

    def test_a_board_on_a_non_default_port_accepts_its_own_page(self):
        """BOARD_PORT names one port and the board serves another (started
        with --port, or after a hop). Its own page states the served port,
        and that is what is accepted — for a plain write and a policy
        write alike. The configured port is not this board's origin."""
        from tests.test_settings_security import free_port
        board = Board(self, configured_port=free_port())
        page = board.page()
        code, data = board.post("/api/move", {
            "file": CARD, "from": "review", "to": "in-progress"}, headers=page)
        self.assertEqual(200, code, data)
        self.assertTrue((board.work / "tasks" / "in-progress" / CARD).is_file())
        code, data = board.post("/api/settings/validate", {"document": board.doc()},
                                headers={**page, "X-Bench-Settings": "1"})
        self.assertEqual(200, code, data)
        code, data = board.post("/api/move", {
            "file": CARD, "from": "in-progress", "to": "review"},
            headers={**page, "Origin": f"http://localhost:{board.port}"})
        self.assertEqual(200, code, data)
        configured = board.env["BOARD_PORT"]
        code, data = board.post("/api/move", {
            "file": CARD, "from": "review", "to": "in-progress"},
            headers={**page, "Origin": f"http://127.0.0.1:{configured}"})
        self.assertEqual(403, code, data)
        self.assertTrue((board.work / "tasks" / "review" / CARD).is_file())

    def test_an_allowed_origin_writes_exactly(self):
        board = Board(self, env={
            "BOARD_ALLOWED_ORIGINS": f" {ALLOWED} , https://*.example, ftp://x"})
        proxied = board.page(ALLOWED)
        code, data = board.post("/api/move", {
            "file": CARD, "from": "review", "to": "in-progress"}, headers=proxied)
        self.assertEqual(200, code, data)
        # the default port written out is the same origin
        code, data = board.post("/api/move", {
            "file": CARD, "from": "in-progress", "to": "review"},
            headers={**proxied, "Origin": ALLOWED + ":443"})
        self.assertEqual(200, code, data)
        # a wildcard entry is trusted for nothing
        code, data = board.post("/api/move", {
            "file": CARD, "from": "review", "to": "in-progress"},
            headers={**proxied, "Origin": "https://a.example"})
        self.assertEqual(403, code, data)

    def test_a_cli_caller_stating_no_origin_writes(self):
        """A shim, a bridge or curl states no Origin and no Sec-Fetch-Site.
        That is not a browser, and it is held to the content type only."""
        board = Board(self)
        code, data = board.post("/api/move", {
            "file": CARD, "from": "review", "to": "in-progress"},
            headers={"Content-Type": "application/json"})
        self.assertEqual(200, code, data)
        code, data = board.post("/api/move", {
            "file": CARD, "from": "in-progress", "to": "review"},
            headers={"Content-Type": "text/plain"})
        self.assertEqual(403, code, data)

    def test_the_progress_shim_reaches_its_handler(self):
        """The real shim, against a real board: refused by the run check
        (no such run), never by the door."""
        import os
        import subprocess
        import sys
        from tests.test_architecture import CORE
        board = Board(self)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
        env.update({"BOARD_PORT": str(board.port), "BOARD_AGENT_ID": "nobody",
                    "BOARD_PROGRESS_TOKEN": "t" * 32})
        out = subprocess.run(
            [sys.executable, str(CORE / "bench-progress"), "--doing", "x",
             "probe"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(2, out.returncode, out.stderr)
        self.assertIn("did not record", out.stderr)
        self.assertNotIn("Origin", out.stderr)
        self.assertNotIn("application/json", out.stderr)

    def test_the_event_bridge_reaches_its_handler(self):
        import os
        import subprocess
        import sys
        from tests.test_architecture import CORE
        board = Board(self)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_", "BENCH_"))}
        env["BOARD_PORT"] = str(board.port)
        subprocess.run(
            [sys.executable, str(CORE / "adapters" / "claude" / "emit.py")],
            input=json.dumps({"hook_event_name": "SessionStart",
                              "session_id": "wp04-bridge-probe"}),
            env=env, capture_output=True, text=True, timeout=60, check=True)
        code, data = board.get("/api/state")
        self.assertEqual(200, code)
        self.assertIn("wp04-bridge-probe",
                      [s.get("id") or s.get("session") for s in data["sessions"]])


class ARunClaimIsNotBorrowedByRouteClass(unittest.TestCase):
    """WP04's half of §4.3: a request naming a run is either a shim's —
    stating no origin, its identity for the run token to prove (WP05) — or
    a browser's, which is refused whatever its origin. Both paths have a
    fixture here so WP05 can tighten the first without loosening the
    second."""

    @classmethod
    def setUpClass(cls):
        cls.board = Board(_ClassCleanup(cls))

    def evidence(self) -> list:
        return sorted((self.board.tool / "manager" / "local" / "state"
                       / "evidence").glob("*.json")) \
            if (self.board.tool / "manager" / "local" / "state"
                / "evidence").is_dir() else []

    def test_a_browser_naming_a_run_is_refused_even_from_this_board(self):
        before = self.evidence()
        build = self.board.build()
        for origin in (f"http://127.0.0.1:{self.board.port}",
                       "https://evil.example", "null"):
            with self.subTest(origin=origin):
                code, data = self.board.post("/api/verify", {
                    "check": "unit", "file": CARD, "agent": "run-1",
                    "token": "x"}, headers={"Content-Type": "application/json",
                                            "Origin": origin,
                                            "X-Bench-Build": build})
                self.assertEqual(403, code, data)
        code, data = self.board.post("/api/verify", {
            "check": "unit", "file": CARD, "agent": "run-1"},
            headers={"Content-Type": "application/json",
                     "Sec-Fetch-Site": "same-origin", "X-Bench-Build": build})
        self.assertEqual(403, code, data)
        self.assertEqual("browser-claims-run", data["code"])
        self.assertEqual(before, self.evidence())

    def test_a_shim_naming_a_run_reaches_the_run_check(self):
        """No Origin, no Sec-Fetch-Site: the door lets it through, and the
        claim is the handler's to judge — an unknown run is refused there,
        not granted by the route's class."""
        code, data = self.board.post("/api/verify", {
            "check": "unit", "file": CARD, "agent": "no-such-run"},
            headers={"Content-Type": "application/json"})
        self.assertEqual(200, code, data)
        # verify answers the claim itself: a run it holds no frozen
        # profile for is denied, and ran nothing
        self.assertEqual("denied", data["evidence"]["outcome"])
        self.assertEqual("no-profile", data["evidence"]["reason"])
        self.assertEqual([], data["evidence"]["argv"])

    def test_a_person_asking_from_the_page_still_reaches_verify(self):
        code, data = self.board.post("/api/verify", {
            "check": "no-such-check", "file": CARD, "stage": "review"},
            headers=self.board.page())
        self.assertNotIn(code, (403, 409), data)


class ThePageSendsJsonOnEveryWrite(unittest.TestCase):
    def test_every_post_the_page_makes_states_json(self):
        from tests import pagesource
        html = pagesource.page()
        at = 0
        seen = 0
        while True:
            at = html.find("method: 'POST'", at)
            if at < 0:
                break
            call = html[at:at + 240]
            seen += 1
            with self.subTest(at=html[max(0, at - 60):at + 60]):
                self.assertTrue("'Content-Type': 'application/json'" in call
                                or "SETTINGS_HEADERS" in call, call)
            at += 1
        self.assertGreater(seen, 20)


if __name__ == "__main__":
    unittest.main()
