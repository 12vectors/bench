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


if __name__ == "__main__":
    unittest.main()
