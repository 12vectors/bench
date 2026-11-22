"""Archive all: the Done column cleared in one confirmed request (task 187).

Every case runs against a throwaway work-state repository, so the commits,
the refusals and the history are real ones. What is held:

- the reading names exactly the cards Archive all would take, and every
  card in done/ it would leave, with why (phase cards, members of an open
  phase, cards an agent works on);
- a batch archives exactly the frozen set, through the single archive
  door: one `board: <n> → archived` commit per card, nothing else moved;
- a card completed after confirmation is not taken, and a card that moved,
  went or became protected is skipped with its reason;
- the same request twice is one batch; an identity reused for another set,
  and a second batch while one runs, are refused;
- partial outcomes are reported apart, and a retry targets only what is
  outstanding — a failed archive, or an archive whose commit failed (which
  the retry commits without moving the card again);
- a restart-interrupted batch reads `interrupted`, with what it did not
  reach outstanding;
- sync's answer is recorded apart from the local archive;
- ⌘Z after Archive all brings the set back to done/.

    python3 -m unittest tests.test_archive_all -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agents  # noqa: E402
import archiveall  # noqa: E402
import config  # noqa: E402
import records  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402
import taskfiles  # noqa: E402

MOVER = "Mover One"


def card(number: int, title: str, status: str, extra: str = "") -> str:
    return (f"# {number} — {title}\n\n**Status:** {status}\n**Priority:** Low\n"
            f"{extra}\nBody of {number}, kept byte for byte.\n")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class Req:
    def __init__(self, body=None, **query):
        self.body = body or {}
        self.query = {k: [v] for k, v in query.items()}
        self.status = self.answer = None

    def reply(self, status, payload):
        self.status, self.answer = status, payload


class ArchiveAll(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-archive-all-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        for slug in (*config.STAGE_DIRS, "archive"):
            (self.repo / "tasks" / slug).mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", MOVER)
        git(self.repo, "config", "user.email", "mover@example.com")
        self.patch(TASKS=self.repo / "tasks", REPO=self.repo, STATE=tmp / "state",
                   SESSIONS_DIR=tmp / "sessions", COMMIT_MOVES=True, SYNC=False)
        state.BOARD_EVENTS.clear()
        state.EXPECTED_MOVES.clear()
        state.LAST_ARCHIVED = None
        archiveall._BATCHES.clear()
        archiveall._RUNNING = None
        self.addCleanup(archiveall._BATCHES.clear)

        self.write("done", "71-first.md", card(71, "First", "Done"))
        self.write("done", "72-second.md", card(72, "Second", "Done"))
        self.write("done", "73-third.md", card(73, "Third", "Done"))
        self.write("review", "74-in-review.md", card(74, "In review", "Review"))
        self.write("backlog", "75-backlog.md", card(75, "Backlog", "Backlog"))
        # a phase still open, holding a member already in done/
        self.write("in-progress", "80-phase.md",
                   card(80, "A phase", "In Progress", "**Type:** Phase\n")
                   + "\n## Cards\n\n- 81 — Member\n")
        self.write("done", "81-member.md", card(81, "Member", "Done"))
        # a finished phase card sits in done/ itself
        self.write("done", "90-done-phase.md",
                   card(90, "Done phase", "Done", "**Type:** Phase\n")
                   + "\n## Cards\n\n- 91 — Done member\n")
        self.write("done", "91-done-member.md", card(91, "Done member", "Done"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.base = self.commits()

    def patch(self, **values):
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def write(self, stage, name, text):
        (self.repo / "tasks" / stage / name).write_text(text, encoding="utf-8")

    def where(self, name):
        found = [s for s in (*config.STAGE_DIRS, "archive")
                 if (self.repo / "tasks" / s / name).is_file()]
        return found[0] if len(found) == 1 else found

    def commits(self):
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def run_batch(self, request, files, retry_of=None):
        batch, again = archiveall.start(request, files, retry_of, background=False)
        return batch, again

    def outcomes(self, batch):
        return {i["file"]: i["outcome"] for i in batch["items"]}

    # — the reading —

    def test_reading_names_the_eligible_set_and_every_exclusion(self):
        reading = archiveall.reading()
        self.assertEqual([r["file"] for r in reading["eligible"]],
                         ["71-first.md", "72-second.md", "73-third.md",
                          "91-done-member.md"])
        self.assertEqual(reading["count"], 4)
        excluded = {r["file"]: r["code"] for r in reading["excluded"]}
        self.assertEqual(excluded, {"81-member.md": "in-phase",
                                    "90-done-phase.md": "phase-card"})
        self.assertEqual(self.commits(), self.base, "a reading writes nothing")

    def test_reading_excludes_a_card_an_agent_works_on(self):
        with mock.patch.object(agents, "working_on",
                               lambda files: [{}] if "72-second.md" in files else []):
            reading = archiveall.reading()
        self.assertNotIn("72-second.md", [r["file"] for r in reading["eligible"]])
        self.assertIn(("72-second.md", "agent-working"),
                      [(r["file"], r["code"]) for r in reading["excluded"]])

    # — a batch —

    def test_archives_exactly_the_frozen_set_one_commit_each(self):
        frozen = ["71-first.md", "72-second.md", "73-third.md"]
        # completed after the person confirmed: not in the request
        self.write("done", "76-late.md", card(76, "Late", "Done"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "late")
        base = self.commits()

        batch, again = self.run_batch("req-1", frozen)

        self.assertFalse(again)
        self.assertEqual(batch["status"], "finished")
        self.assertEqual(batch["archived"], 3)
        self.assertEqual(batch["outstanding"], [])
        for name in frozen:
            self.assertEqual(self.where(name), "archive")
            self.assertIn("**Status:** Archived",
                          (self.repo / "tasks/archive" / name).read_text())
        self.assertEqual(self.where("76-late.md"), "done", "not scooped up")
        self.assertEqual(self.where("74-in-review.md"), "review")
        self.assertEqual(self.where("75-backlog.md"), "backlog")
        self.assertEqual(self.where("90-done-phase.md"), "done")
        self.assertEqual(self.commits(), base + 3)
        log = git(self.repo, "log", "-3", "--pretty=%s").stdout.splitlines()
        self.assertEqual(sorted(log), [f"board: {n} → archived ({MOVER})"
                                       for n in (71, 72, 73)])
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")
        self.assertEqual(state.LAST_ARCHIVED["batch"],
                         [{"file": n, "from": "done"} for n in frozen])

    def test_changed_and_protected_cards_are_skipped_not_moved(self):
        # after confirmation: one went back to review, one vanished, one is
        # now a member of an open phase, one an agent picked up
        (self.repo / "tasks/done/71-first.md").rename(self.repo / "tasks/review/71-first.md")
        (self.repo / "tasks/done/72-second.md").unlink()
        frozen = ["71-first.md", "72-second.md", "73-third.md", "81-member.md",
                  "90-done-phase.md", "91-done-member.md"]
        with mock.patch.object(agents, "working_on",
                               lambda files: [{}] if "91-done-member.md" in files else []):
            batch, _ = self.run_batch("req-2", frozen)
        got = {i["file"]: (i["outcome"], i.get("code")) for i in batch["items"]}
        self.assertEqual(got, {
            "71-first.md": ("skipped", "moved"),
            "72-second.md": ("skipped", "gone"),
            "73-third.md": ("archived", None),
            "81-member.md": ("skipped", "in-phase"),
            "90-done-phase.md": ("skipped", "phase-card"),
            "91-done-member.md": ("skipped", "agent-working"),
        })
        self.assertEqual(self.where("71-first.md"), "review")
        self.assertEqual(self.where("81-member.md"), "done")
        self.assertEqual(self.where("90-done-phase.md"), "done")
        self.assertEqual(batch["outstanding"], [], "skipped is not outstanding")

    def test_the_same_request_twice_is_one_batch(self):
        frozen = ["71-first.md", "72-second.md"]
        first, _ = self.run_batch("req-3", frozen)
        count = self.commits()
        again, duplicate = self.run_batch("req-3", frozen)
        self.assertTrue(duplicate)
        self.assertEqual(again["items"], first["items"])
        self.assertEqual(self.commits(), count, "no second history")

    def test_the_same_request_after_a_restart_answers_from_its_record(self):
        frozen = ["71-first.md"]
        self.run_batch("req-4", frozen)
        archiveall._BATCHES.clear()          # a restarted process
        count = self.commits()
        batch, duplicate = self.run_batch("req-4", frozen)
        self.assertTrue(duplicate)
        self.assertEqual(batch["archived"], 1)
        self.assertEqual(self.commits(), count)

    def test_an_identity_reused_for_another_set_is_refused(self):
        self.run_batch("req-5", ["71-first.md"])
        with self.assertRaises(ValueError):
            self.run_batch("req-5", ["72-second.md"])
        self.assertEqual(self.where("72-second.md"), "done")

    def test_a_second_batch_while_one_runs_is_refused(self):
        archiveall._RUNNING = "someone-else"
        self.addCleanup(setattr, archiveall, "_RUNNING", None)
        with self.assertRaises(ValueError):
            self.run_batch("req-6", ["71-first.md"])
        self.assertEqual(self.where("71-first.md"), "done")

    def test_malformed_requests_are_refused_before_anything(self):
        for request, files in ((None, ["71-first.md"]), ("ok", []),
                               ("ok", ["../escape.md"]), ("ok", ["x.txt"]),
                               ("bad id!", ["71-first.md"])):
            with self.subTest(request=request, files=files):
                with self.assertRaises(ValueError):
                    self.run_batch(request, files)
        self.assertEqual(self.commits(), self.base)

    def test_a_card_already_archived_is_reported_not_moved_again(self):
        taskfiles.archive_task("71-first.md", "done")
        count = self.commits()
        batch, _ = self.run_batch("req-7", ["71-first.md", "72-second.md"])
        self.assertEqual(self.outcomes(batch),
                         {"71-first.md": "already-archived", "72-second.md": "archived"})
        self.assertEqual(self.commits(), count + 1)

    def test_a_failed_card_does_not_stop_the_rest_and_a_retry_targets_it(self):
        # a file of that name already in the archive: the single door refuses
        self.write("archive", "72-second.md", card(72, "Older copy", "Archived"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "older copy")
        batch, _ = self.run_batch("req-8", ["71-first.md", "72-second.md", "73-third.md"])
        self.assertEqual(self.outcomes(batch), {"71-first.md": "archived",
                                                "72-second.md": "failed",
                                                "73-third.md": "archived"})
        self.assertIn("already exists in archive", batch["items"][1]["reason"])
        self.assertEqual(batch["outstanding"], ["72-second.md"])
        self.assertEqual(self.where("72-second.md"), ["done", "archive"])

        # the person clears the clash, then retries only what is outstanding
        (self.repo / "tasks/archive/72-second.md").unlink()
        git(self.repo, "commit", "-qam", "drop the old copy")
        retry, _ = self.run_batch("req-9", batch["outstanding"], "req-8")
        self.assertEqual(self.outcomes(retry), {"72-second.md": "archived"})
        self.assertEqual(self.where("72-second.md"), "archive")

    def test_a_commit_failure_is_archived_uncommitted_and_a_retry_commits_it(self):
        lock = self.repo / ".git" / "index.lock"
        lock.write_text("")
        try:
            batch, _ = self.run_batch("req-10", ["71-first.md"])
        finally:
            lock.unlink()
        item = batch["items"][0]
        self.assertEqual((item["outcome"], item["committed"]), ("archived", False))
        self.assertEqual(batch["uncommitted"], 1)
        self.assertEqual(batch["outstanding"], ["71-first.md"])
        self.assertEqual(self.where("71-first.md"), "archive")
        self.assertNotEqual(git(self.repo, "status", "--porcelain").stdout, "")
        count = self.commits()

        retry, _ = self.run_batch("req-11", ["71-first.md"], "req-10")
        self.assertEqual(self.outcomes(retry), {"71-first.md": "committed"})
        self.assertEqual(retry["outstanding"], [])
        self.assertEqual(self.commits(), count + 1, "one commit, no second move")
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")
        self.assertEqual(self.where("71-first.md"), "archive")

    def test_an_interrupted_batch_says_so_and_keeps_what_it_did_not_reach(self):
        record = {"v": 1, "request": "req-12", "files": ["71-first.md", "72-second.md"],
                  "status": "running", "items": [{"file": "71-first.md",
                                                  "outcome": "archived",
                                                  "committed": True}],
                  "total": 2}
        records.write_json(archiveall._path("req-12"), record)
        batch = archiveall.get("req-12")
        self.assertEqual(batch["status"], "interrupted")
        self.assertEqual(batch["outstanding"], ["72-second.md"])

    def test_sync_is_recorded_apart_from_the_local_archive(self):
        self.patch(SYNC=True)
        with mock.patch.object(sync, "push_now", return_value="offline"), \
             mock.patch.object(sync, "status",
                               return_value={"enabled": True, "state": "offline",
                                             "detail": "origin unreachable"}):
            batch, _ = self.run_batch("req-13", ["71-first.md"])
        self.assertEqual(batch["archived"], 1)
        self.assertEqual(batch["sync"]["outcome"], "offline")
        self.assertEqual(batch["sync"]["status"]["state"], "offline")

    def test_a_crash_between_move_and_commit_is_committed_by_the_next_batch(self):
        # the board's own move whose commit failed, then a restart lost the
        # batch's record: the journal names the move, the batch does not
        lock = self.repo / ".git" / "index.lock"
        lock.write_text("")
        try:
            self.run_batch("req-20", ["71-first.md"])
        finally:
            lock.unlink()
        archiveall._BATCHES.clear()
        archiveall._path("req-20").unlink()
        count = self.commits()

        batch, _ = self.run_batch("req-21", ["71-first.md", "72-second.md"])
        self.assertEqual(self.outcomes(batch), {"71-first.md": "committed",
                                                "72-second.md": "archived"})
        self.assertEqual(self.commits(), count + 2, "no second move of 71")
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")

    def test_a_move_with_no_record_at_all_is_said_uncommitted_not_hidden(self):
        # a crash before even the journal line: bench will not attribute a
        # move it cannot account for, and says the card is not committed
        text = (self.repo / "tasks/done/71-first.md").read_text()
        (self.repo / "tasks/archive/71-first.md").write_text(
            text.replace("**Status:** Done", "**Status:** Archived"))
        (self.repo / "tasks/done/71-first.md").unlink()
        batch, _ = self.run_batch("req-22", ["71-first.md"])
        item = batch["items"][0]
        self.assertEqual((item["outcome"], item["committed"]), ("archived", False))
        self.assertEqual(batch["outstanding"], ["71-first.md"])
        self.assertIn("by hand", item["reason"])

    def test_the_board_is_parsed_once_for_a_batch_nobody_else_touches(self):
        real = taskfiles.collect
        with mock.patch.object(taskfiles, "collect", side_effect=real) as spy:
            batch, _ = self.run_batch("req-23", ["71-first.md", "72-second.md",
                                                 "73-third.md"])
        self.assertEqual(batch["archived"], 3)
        self.assertEqual(spy.call_count, 1)

    def test_a_change_made_mid_batch_is_read_before_the_next_card(self):
        # 72 becomes a member of the open phase while 71 is being archived
        real = taskfiles.archive_task

        def archive_then_join(filename, source, *rest):
            result = real(filename, source, *rest)
            if filename == "71-first.md":
                phase = self.repo / "tasks/in-progress/80-phase.md"
                phase.write_text(phase.read_text() + "- 72 — Second\n")
            return result

        with mock.patch.object(taskfiles, "archive_task", archive_then_join):
            batch, _ = self.run_batch("req-24", ["71-first.md", "72-second.md"])
        got = {i["file"]: (i["outcome"], i.get("code")) for i in batch["items"]}
        self.assertEqual(got, {"71-first.md": ("archived", None),
                               "72-second.md": ("skipped", "in-phase")})
        self.assertEqual(self.where("72-second.md"), "done")

    def test_undo_brings_the_whole_set_back_to_done(self):
        self.run_batch("req-14", ["71-first.md", "72-second.md"])
        req = Req()
        routes.post_unarchive(req)
        self.assertEqual(req.status, 200)
        self.assertEqual([r["file"] for r in req.answer["batch"]],
                         ["71-first.md", "72-second.md"])
        self.assertEqual(self.where("71-first.md"), "done")
        self.assertIn("**Status:** Done",
                      (self.repo / "tasks/done/71-first.md").read_text())

    # — the routes —

    def test_routes_read_and_start(self):
        req = Req()
        routes.get_archive_all(req)
        self.assertEqual((req.status, req.answer["count"]), (200, 4))

        start = Req({"request": "req-15", "files": ["73-third.md"]})
        real = archiveall.start
        with mock.patch.object(archiveall, "start",
                               lambda r, f, o: real(r, f, o, background=False)):
            routes.post_archive_all(start)
        self.assertEqual(start.status, 200)
        self.assertFalse(start.answer["duplicate"])

        status = Req(request="req-15")
        routes.get_archive_all(status)
        self.assertEqual(status.answer["batch"]["archived"], 1)
        missing = Req(request="nope")
        routes.get_archive_all(missing)
        self.assertEqual(missing.status, 404)


class ArchiveAllSync(ArchiveAll):
    """The same board, syncing through a disposable bare remote: the batch
    publishes once at its end, and a refused push leaves the cards archived
    here and says so."""

    def setUp(self):
        super().setUp()
        self.origin = self.repo.parent / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "branch", "-q", "--set-upstream-to=origin/main", "main")
        self.patch(SYNC=True, FETCH_TIMEOUT=10.0)
        saved = list(state.COMMIT_HOOKS)
        self.addCleanup(lambda: state.COMMIT_HOOKS.__setitem__(slice(None), saved))
        state.COMMIT_HOOKS[:] = []
        self.hooked: list[str] = []
        state.COMMIT_HOOKS.append(self.hooked.append)
        sync._NOTES.clear()
        self.addCleanup(sync._NOTES.clear)

    def origin_head(self):
        return git(self.origin, "rev-parse", "main").stdout.strip()

    def test_a_batch_publishes_once_at_its_end(self):
        real = sync.push_now
        with mock.patch.object(sync, "push_now", side_effect=real) as push:
            batch, _ = self.run_batch("req-30", ["71-first.md", "72-second.md",
                                                 "73-third.md"])
        self.assertEqual(batch["archived"], 3)
        self.assertEqual(batch["commits"], 3)
        self.assertEqual(push.call_count, 1, "one publish for the batch")
        self.assertEqual(self.hooked, [], "no per-card push was started")
        self.assertEqual(batch["sync"]["outcome"], "ok")
        self.assertEqual(self.origin_head(),
                         git(self.repo, "rev-parse", "HEAD").stdout.strip())

    def test_a_refused_push_leaves_the_cards_archived_and_says_so(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'protected' >&2\nexit 1\n")
        hook.chmod(0o755)
        before = self.origin_head()
        batch, _ = self.run_batch("req-31", ["71-first.md", "72-second.md"])
        self.assertEqual(batch["archived"], 2)
        self.assertEqual(batch["outstanding"], [], "the archive itself succeeded")
        self.assertNotEqual(batch["sync"]["outcome"], "ok")
        self.assertEqual(self.origin_head(), before, "nothing reached the remote")
        self.assertEqual(self.where("71-first.md"), "archive")
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")

    def test_sync_is_recorded_apart_from_the_local_archive(self):
        # the mocked flavour of the base class is covered by the two above
        pass


if __name__ == "__main__":
    unittest.main()
