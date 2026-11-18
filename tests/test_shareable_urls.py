"""Shareable addresses for the board's views and cards (task 134).

Every place the page can show has a path — `/board`, `/cards/<card>`,
`/phases[/<card>]`, `/sessions[/<session>]`, `/focus[/<session>]`,
`/settings[/<section>]` — and opening one directly, or refreshing it, is
the page itself. These hold the server's half against a real board on a
loopback port:

- the shell is served for exactly those shapes, with the page's own
  security headers, and nothing else became a page: an unknown path, a
  missing API endpoint, an unlisted asset and a malformed identifier keep
  their 404, and the Host check still refuses before any of it;
- `GET /api/card/locate` finds a card by number or stem in any stage or the
  archive, names a shared number as ambiguous, and an unknown one as gone;
- opening any of it changes nothing: no card moves, no file is written in
  the work state, no agent is launched.

The page's half — the parser, the history boundary, Back and Forward — is
in `tests/node/routes.test.mjs` and `verify/browser_cases/case_shareable_urls.py`.

    python3 -m unittest tests.test_shareable_urls -v
"""

from __future__ import annotations

import hashlib
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_settings_security import Board

CORE = Path(__file__).resolve().parents[1] / "manager" / "core"
sys.path.insert(0, str(CORE))

import routes  # noqa: E402


def fetch(board: Board, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(board.url + path, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def snapshot(root: Path) -> dict:
    """Every file under `root` with its content hash: what "nothing was
    written" is checked against."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class _ClassCleanup:
    def __init__(self, cls):
        self.addCleanup = cls.addClassCleanup


class TheShapes(unittest.TestCase):
    """`routes.ui_path` — the one rule for which paths are the page."""

    def test_the_documented_destinations_are_pages(self):
        for path in ("/board", "/phases", "/sessions", "/focus", "/settings",
                     "/cards/134", "/cards/a-card-without-a-number", "/cards/0134",
                     "/phases/53", "/sessions/6f1c2d3e-aaaa-bbbb-cccc-0123456789ab",
                     "/focus/abc", "/settings/acceptance", "/settings/no-such-section"):
            self.assertTrue(routes.ui_path(path), path)

    def test_nothing_else_is(self):
        for path in ("/cards", "/cards/", "/cards/134/extra", "/cards/.hidden",
                     "/cards/a%20b", "/cards/..", "/cards/" + "x" * 201,
                     "/settings/Upper", "/settings/a_b", "/sessions/", "/board/x",
                     "/boards", "/api", "/nowhere", "/phases/53/", "/focus/a/b"):
            self.assertFalse(routes.ui_path(path), path)


class DirectEntry(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.board = Board(_ClassCleanup(cls))
        tasks = cls.board.work / "tasks"
        (tasks / "backlog" / "07-a-numbered-card.md").write_text(
            "# 07 — A numbered card\n\n**Status:** Backlog\n**Priority:** Low\n\nBrief.\n",
            encoding="utf-8")
        (tasks / "backlog" / "notes-without-number.md").write_text(
            "# Notes\n\n**Status:** Backlog\n**Priority:** Low\n\nBrief.\n", encoding="utf-8")
        (tasks / "archive").mkdir(exist_ok=True)
        (tasks / "archive" / "08-an-archived-card.md").write_text(
            "# 08 — An archived card\n\n**Status:** Archived\n**Priority:** Low\n\n"
            "ARCHIVED-TEXT\n", encoding="utf-8")
        (tasks / "to-do" / "09-one.md").write_text(
            "# 09 — One\n\n**Status:** To Do\n**Priority:** Low\n", encoding="utf-8")
        (tasks / "done" / "09-two.md").write_text(
            "# 09 — Two\n\n**Status:** Done\n**Priority:** Low\n", encoding="utf-8")

    def test_every_destination_is_the_page_with_its_protections(self):
        code, _, root = fetch(self.board, "/")
        self.assertEqual(200, code)
        for path in ("/board", "/cards/7", "/cards/7?tab=reviews", "/cards/999",
                     "/cards/08", "/phases", "/phases/7", "/sessions", "/sessions/abc",
                     "/focus", "/focus/abc", "/settings", "/settings/agents",
                     "/settings/not-a-section"):
            code, headers, body = fetch(self.board, path)
            self.assertEqual(200, code, path)
            self.assertEqual(root, body, f"{path} is not the page / serves")
            self.assertIn("text/html", headers.get("Content-Type", ""), path)
            self.assertIn("script-src 'self'", headers.get("Content-Security-Policy", ""), path)
            self.assertEqual("nosniff", headers.get("X-Content-Type-Options"), path)
            # its assets are named absolutely, so a nested path finds them
            self.assertIn(b'src="/web/', body, path)

    def test_everything_else_keeps_its_error(self):
        for path in ("/nowhere", "/cards", "/cards/", "/cards/7/extra", "/cards/.x",
                     "/board/x", "/settings/BAD", "/api/nope", "/api/cards/7",
                     "/web/not-a-build/main.js", "/files/plans/nothing.md"):
            code, headers, body = fetch(self.board, path)
            self.assertEqual(404, code, path)
            self.assertNotIn(b"<html", body.lower(), f"{path} served the page")

    def test_an_api_endpoint_is_still_its_own_answer(self):
        code, headers, body = fetch(self.board, "/api/card/locate?id=7")
        self.assertEqual(200, code)
        self.assertIn("application/json", headers.get("Content-Type", ""))

    def test_an_unlisted_host_is_refused_under_enforce(self):
        board = Board(self, env={"BOARD_HOST_CHECK": "enforce"})
        code, _, _ = fetch(board, "/cards/1", {"Host": f"evil.example:{board.port}"})
        self.assertEqual(421, code)
        code, _, _ = fetch(board, "/cards/1")
        self.assertEqual(200, code)

    def test_locate_by_number_stem_and_in_the_archive(self):
        code, data = self.board.get("/api/card/locate?id=7")
        self.assertEqual(200, code, data)
        self.assertEqual(("backlog", False), (data["stage"], data["archived"]))
        self.assertEqual("07-a-numbered-card.md", data["card"]["file"])
        self.assertNotIn("body", data["card"], "a card on the board is listed, not read")
        # leading zeros are the same number
        self.assertEqual(200, self.board.get("/api/card/locate?id=007")[0])
        code, data = self.board.get("/api/card/locate?id=notes-without-number")
        self.assertEqual((200, "notes-without-number.md"), (code, data["card"]["file"]))
        code, data = self.board.get("/api/card/locate?id=8")
        self.assertEqual(200, code, data)
        self.assertEqual(("archive", True), (data["stage"], data["archived"]))
        self.assertIn("ARCHIVED-TEXT", data["card"]["body"])
        self.assertIn("parts", data["card"])
        self.assertFalse(data["card"]["statusMismatch"], "Archived is an archived card's status")

    def test_locate_says_ambiguous_gone_and_invalid(self):
        code, data = self.board.get("/api/card/locate?id=9")
        self.assertEqual(409, code, data)
        self.assertEqual("ambiguous", data["code"])
        self.assertEqual(["to-do/09-one.md", "done/09-two.md"], data["files"])
        code, data = self.board.get("/api/card/locate?id=09-two")
        self.assertEqual((200, "done"), (code, data["stage"]))
        code, data = self.board.get("/api/card/locate?id=4242")
        self.assertEqual((404, "gone"), (code, data["code"]))
        for bad in ("", "..%2Fx", "a%2Fb", ".hidden", "x" * 201):
            code, data = self.board.get("/api/card/locate?id=" + bad)
            self.assertEqual((400, "invalid"), (code, data.get("code")), bad)

    def test_opening_addresses_changes_nothing(self):
        before = snapshot(self.board.work)
        _, state = self.board.get("/api/state")
        agents = len(state.get("agents", []))
        for path in ("/board", "/cards/7?tab=activity", "/cards/8", "/cards/9",
                     "/phases/7", "/sessions/abc", "/focus/abc", "/settings/agents",
                     "/settings/setup", "/api/card/locate?id=7", "/api/card/locate?id=8"):
            fetch(self.board, path)
        self.assertEqual(before, snapshot(self.board.work), "opening an address wrote to the work state")
        _, state = self.board.get("/api/state")
        self.assertEqual(agents, len(state.get("agents", [])), "opening an address launched something")


if __name__ == "__main__":
    unittest.main()
