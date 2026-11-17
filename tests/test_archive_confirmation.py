"""A confirmed archive tells the page enough to paint it (task 128).

The page takes the card off as soon as `/api/archive` answers, without
waiting for a whole-board reading, so the reply has to be the authority
for exactly what it describes: the card that left and the stage it left,
the archive count it left behind, whether git recorded the move, and the
board revision whose reading will hold it. The same revision is on the
`board` frame the archive broadcasts and — read before the disk is — on
every `/api/state`, which is what lets the page tell an older reading
from a newer one.

These run the real server over a throwaway git repository, so the
commits, and the commit that fails, are real.

    python3 -m unittest tests.test_archive_confirmation -v
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import httpd  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

CARD = ("# 44 — A card that gets archived\n\n"
        "**Status:** Backlog\n"
        "**Priority:** High\n\n"
        "Body text.\n")
FILENAME = "44-a-card.md"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class ArchiveReply(unittest.TestCase):

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-archive-reply-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in config.STAGE_DIRS:
            (self.repo / "tasks" / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", "Mover One")
        git(self.repo, "config", "user.email", "mover@example.com")
        self.patch(TASKS=self.repo / "tasks", REPO=self.repo,
                   SESSIONS_DIR=tmp / "sessions", COMMIT_MOVES=True)
        state.BOARD_EVENTS.clear()
        state.EXPECTED_MOVES.clear()
        self.addCleanup(setattr, state, "LAST_ARCHIVED", state.LAST_ARCHIVED)
        state.LAST_ARCHIVED = None
        (self.repo / "tasks" / "backlog" / FILENAME).write_text(CARD, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")

        # a stream client, as the page holds one
        self.frames: queue.Queue = queue.Queue()
        with state.LOCK:
            state.CLIENTS.add(self.frames)
        self.addCleanup(self.drop_client)

        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def drop_client(self):
        with state.LOCK:
            state.CLIENTS.discard(self.frames)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def board_frames(self) -> list[dict]:
        out = []
        while True:
            try:
                msg = json.loads(self.frames.get_nowait())
            except queue.Empty:
                return out
            if msg.get("type") == "board":
                out.append(msg)

    # — the confirmation —

    def test_the_reply_is_the_confirmation_the_page_paints_from(self):
        before = state.board_rev()
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})

        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["file"], FILENAME)
        self.assertEqual(reply["from"], "backlog")
        self.assertIs(reply["committed"], True, "git recorded it")
        self.assertEqual(reply["archivedCount"], 1, "the count it left behind")
        self.assertEqual(reply["boot"], state.BOOT)
        self.assertEqual(reply["rev"], before + 1)
        self.assertTrue((self.repo / "tasks" / "archive" / FILENAME).is_file())

    def test_the_archive_frame_carries_the_same_revision(self):
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        self.assertEqual(status, 200, reply)
        frames = self.board_frames()
        self.assertEqual([f["rev"] for f in frames], [reply["rev"]],
                         "one board frame, naming the revision the reply names")

    def test_a_state_reading_names_the_revision_it_read_before_the_disk(self):
        older = routes.state_payload()
        self.assertEqual(older["boardBoot"], state.BOOT)
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        self.assertEqual(status, 200, reply)
        newer = routes.state_payload()

        self.assertLess(older["boardRev"], reply["rev"],
                        "a reading from before the archive is older than it")
        self.assertGreaterEqual(newer["boardRev"], reply["rev"],
                                "a reading after it holds it")
        files = [t["file"] for s in newer["board"]["stages"] for t in s["tasks"]]
        self.assertNotIn(FILENAME, files)
        self.assertEqual(newer["archivedCount"], reply["archivedCount"])

    def test_other_frames_take_no_revision(self):
        before = state.board_rev()
        self.assertIsNone(state.broadcast({"type": "agents"}))
        self.assertEqual(state.board_rev(), before)
        self.assertEqual(state.broadcast({"type": "board"}), before + 1)

    # — partial outcomes and refusals —

    def test_a_commit_that_fails_is_a_moved_card_said_to_be_uncommitted(self):
        # git refuses to touch an index another process holds
        (self.repo / ".git" / "index.lock").write_text("", encoding="utf-8")
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})

        self.assertEqual(status, 200, "the move happened: the disk is the truth")
        self.assertIs(reply["committed"], False)
        self.assertTrue((self.repo / "tasks" / "archive" / FILENAME).is_file())
        self.assertTrue(any("committing it failed" in e.get("summary", "")
                            for e in state.BOARD_EVENTS),
                        "and the activity log says why")

    def test_without_commit_moves_nothing_is_claimed_about_git(self):
        self.patch(COMMIT_MOVES=False)
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        self.assertEqual(status, 200, reply)
        self.assertIsNone(reply["committed"])

    def test_a_refusal_moves_nothing_and_announces_nothing(self):
        before = state.board_rev()
        status, reply = self.post("/api/archive", {"file": FILENAME, "from": "to-do"})

        self.assertEqual(status, 409, "a refusal, in the board's word for one")
        self.assertIn("no longer in to-do/", reply["error"])
        self.assertEqual(state.board_rev(), before, "no change, no revision")
        self.assertEqual(self.board_frames(), [])
        self.assertTrue((self.repo / "tasks" / "backlog" / FILENAME).is_file())

    def test_a_second_archive_of_the_same_card_is_refused_not_repeated(self):
        first, _ = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        second, reply = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        self.assertEqual((first, second), (200, 409))
        self.assertIn("no longer in backlog/", reply["error"])

    # — the way back —

    def test_the_undo_reply_carries_the_same_confirmation(self):
        _, archived = self.post("/api/archive", {"file": FILENAME, "from": "backlog"})
        status, reply = self.post("/api/unarchive", {})

        self.assertEqual(status, 200, reply)
        self.assertEqual((reply["file"], reply["to"]), (FILENAME, "backlog"))
        self.assertIs(reply["committed"], True)
        self.assertEqual(reply["archivedCount"], 0)
        self.assertEqual(reply["rev"], archived["rev"] + 1)
        self.assertEqual(reply["boot"], state.BOOT)
        again, refused = self.post("/api/unarchive", {})
        self.assertEqual(again, 409, "one undo per archive")
        self.assertIn("nothing to unarchive", refused["error"])


if __name__ == "__main__":
    unittest.main()
