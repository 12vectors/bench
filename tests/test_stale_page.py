"""A page of an earlier build cannot write after a restart (task 124, WP15).

The board serves the asset build it booted with, and its page names that
build. When the board restarts into another build, an open page's code is
no longer the code that would have drawn the rules it is about to act on.
So a browser's mutation from it is refused — `stale-page`, 409, nothing
changed, reload to update — while every caller that is not a browser
page keeps working exactly as before: the run shims, the event bridges,
`curl`. Both protocols are exercised here against a real board.

And the page carries its script policy: a Content Security Policy that
allows the build's own modules and nothing else.
"""

import http.client
import json
import sys
import unittest
from pathlib import Path

from tests.test_settings_security import Board

CORE = Path(__file__).resolve().parents[1] / "manager" / "core"
sys.path.insert(0, str(CORE))

import assets  # noqa: E402
import httpd  # noqa: E402

CARD = "01-a-card.md"


def rebuilt(board) -> None:
    """Give the board's copy of core a different build before it starts:
    one module edited and the manifest rewritten, as an update would."""
    web = board.tool / "manager" / "core" / "web"
    with open(web / "words.js", "a", encoding="utf-8") as fh:
        fh.write("// the next release\n")
    (web / "manifest.json").write_bytes(assets.render(web))


def post(board, path: str, payload: dict, headers: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", board.port, timeout=20)
    try:
        body = json.dumps(payload).encode()
        conn.request("POST", path, body=body,
                     headers={"Content-Length": str(len(body)), **headers})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


class _Cleanup:
    def __init__(self, cls):
        self.addCleanup = cls.addClassCleanup


class AfterARestartIntoAnotherBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the page was loaded from the first board; the second is the same
        # install after an update and a restart
        first = Board(_Cleanup(cls))
        cls.old_build = first.build()
        cls.board = Board(_Cleanup(cls), prepare=rebuilt)
        cls.new_build = cls.board.build()

    def setUp(self):
        self.assertNotEqual(self.old_build, self.new_build)
        self.origin = f"http://127.0.0.1:{self.board.port}"

    def move(self, headers, frm="review", to="in-progress"):
        return post(self.board, "/api/move", {"file": CARD, "from": frm, "to": to},
                    {"Content-Type": "application/json", **headers})

    def at(self, stage: str) -> bool:
        return (self.board.work / "tasks" / stage / CARD).is_file()

    def test_1_the_old_page_is_refused_and_nothing_changes(self):
        for headers in ({"Origin": self.origin, "Sec-Fetch-Site": "same-origin",
                         "X-Bench-Build": self.old_build},
                        {"Sec-Fetch-Site": "same-origin", "X-Bench-Build": self.old_build},
                        # a page from before pages named their build
                        {"Origin": self.origin, "Sec-Fetch-Site": "same-origin"}):
            with self.subTest(headers=headers):
                code, data = self.move(headers)
                self.assertEqual(409, code, data)
                self.assertEqual("stale-page", data["code"])
                self.assertTrue(data["stale"])
                self.assertEqual(self.new_build, data["build"])
                self.assertIn("Reload", data["error"])
                self.assertIn("Nothing was changed", data["error"])
                self.assertIn("discards", data["error"], "the unsaved-work warning")
                self.assertTrue(self.at("review"), "a refused write moved the card")

    def test_2_a_policy_write_from_the_old_page_is_refused_too(self):
        code, data = post(self.board, "/api/settings/validate", {"document": {"v": 1}},
                          {"Content-Type": "application/json", "X-Bench-Settings": "1",
                           "Origin": self.origin, "Sec-Fetch-Site": "same-origin",
                           "X-Bench-Build": self.old_build})
        self.assertEqual(409, code, data)
        self.assertEqual("stale-page", data["code"])

    def test_3_the_reloaded_page_writes(self):
        code, data = self.move({"Origin": self.origin, "Sec-Fetch-Site": "same-origin",
                                "X-Bench-Build": self.new_build})
        self.assertEqual(200, code, data)
        self.assertTrue(self.at("in-progress"))
        code, data = self.move({"Origin": self.origin, "Sec-Fetch-Site": "same-origin",
                                "X-Bench-Build": self.new_build}, "in-progress", "review")
        self.assertEqual(200, code, data)

    def test_4_a_local_caller_is_never_asked_for_a_build(self):
        """A shim, a bridge or curl states no Origin and no Sec-Fetch-Site:
        not a browser, so neither a missing nor a mismatched build matters."""
        for headers in ({}, {"X-Bench-Build": self.old_build}):
            with self.subTest(headers=headers):
                code, data = self.move(headers)
                self.assertEqual(200, code, data)
                code, data = self.move(headers, "in-progress", "review")
                self.assertEqual(200, code, data)

    def test_5_the_event_bridge_and_run_shims_are_untouched(self):
        code, data = post(self.board, "/api/events",
                          {"v": 1, "session": "s-stale", "kind": "read",
                           "summary": "read: a", "id": "PostToolUse:tu_stale1"},
                          {"Content-Type": "application/json"})
        self.assertEqual(200, code, data)
        # a progress report with no build reaches the run-token check, which
        # refuses an unknown run on its own terms — never as a stale page
        code, data = post(self.board, "/api/progress",
                          {"agent": "no-such-run", "token": "x", "doing": "y"},
                          {"Content-Type": "application/json"})
        self.assertNotEqual("stale-page", data.get("code"), data)

    def test_6_the_page_names_the_build_the_door_checks(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.board.port, timeout=20)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            html = resp.read().decode()
            policy = resp.getheader("Content-Security-Policy")
        finally:
            conn.close()
        self.assertIn(f'<meta name="bench-build" content="{self.new_build}">', html)
        self.assertIn(f'<script type="module" src="/web/{self.new_build}/main.js">', html)
        self.assertEqual(assets.PAGE_POLICY, policy)


class TheRuleItself(unittest.TestCase):
    def test_only_the_browser_classes_and_only_a_stated_browser(self):
        wrong = {"Origin": "http://127.0.0.1:1", "X-Bench-Build": "0" * 16}
        for klass in ("read", "run-authenticated", "event-ingest"):
            self.assertIsNone(httpd.build_problem(klass, wrong), klass)
        for klass in ("browser-mutation", "policy-mutation"):
            self.assertIsNotNone(httpd.build_problem(klass, wrong), klass)
            self.assertIsNone(httpd.build_problem(klass, {"X-Bench-Build": "0" * 16}))
            self.assertIsNone(httpd.build_problem(
                klass, {"Origin": "x", "X-Bench-Build": assets.current_build()}))


class ThePagePolicy(unittest.TestCase):
    def test_the_policy_admits_the_builds_script_and_nothing_else(self):
        directives = dict(d.strip().split(" ", 1)
                          for d in assets.PAGE_POLICY.split(";"))
        self.assertEqual("'self'", directives["script-src"])
        self.assertEqual("'self'", directives["connect-src"])
        for name in ("object-src", "base-uri", "form-action", "frame-ancestors",
                     "frame-src", "worker-src"):
            self.assertEqual("'none'", directives[name], name)
        for name, value in directives.items():
            self.assertNotIn("unsafe-eval", value, name)
            self.assertNotIn("*", value, name)
        self.assertNotIn("unsafe-inline", directives["script-src"])
        # images: the favicon is an SVG data: url and the only image
        self.assertEqual("'self' data:", directives["img-src"])


if __name__ == "__main__":
    unittest.main()
