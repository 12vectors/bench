"""Last-mover eligibility: an orchestrator acts on its own user's last move (task 138).

Every case runs against throwaway git repositories — two users, `ada`
(this board) and `elena` (a teammate), and for the sync cases two clones
of one bare origin — so attribution is read off real commits, never a
mock. Each class is one acceptance criterion of the card:

- a foreign To Do move is ineligible; a foreign card a current-user move
  brought into To Do is eligible;
- content edits by either user, the board's automated moves and records,
  and sync's transport (pulls, replays, merges) never stand in for the
  qualifying person's move;
- the latest person's move changes eligibility at once, and nothing
  pulls a Backlog, Done or held card back or acquires it;
- rename, first-run reconstruction, identity changes, same-user synced
  moves and ambiguous or missing history each have one deterministic
  answer;
- the orchestrator's launch path and its passive reading carry the same
  answer from the same resolver; a phase delegates its members without a
  second scope acquiring them, and reading launches nothing.

    python3 -m unittest tests.test_orchestration_eligibility -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agreements  # noqa: E402
import config  # noqa: E402
import execowner  # noqa: E402
import movements  # noqa: E402
import orchestrator  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

ADA, ELENA = "ada", "elena"
CARD = ("# 7 — Export to CSV\n\n**Status:** {status}\n**Priority:** Medium\n\n"
        "Someone's idea.\n")
FILE = "7-export-to-csv.md"


def git(cwd: Path, *args: str, user: str | None = None) -> str:
    who = ["-c", f"user.name={user}", "-c", f"user.email={user}@example.com"] \
        if user else []
    result = subprocess.run(["git", "-C", str(cwd), *who, *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout


def card_text(stage: str, number: str = "7", title: str = "Export to CSV",
              extra: str = "") -> str:
    return CARD.replace("# 7 — Export to CSV", f"# {number} — {title}").format(
        status=config.STAGE_LABELS[stage]) + extra


class Board(unittest.TestCase):
    """One work-state repository, its own state directory, and the
    current user's identity set by `as_user`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-elig-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.origin)], check=True, capture_output=True)
        self.repo = self.clone("a")
        self.saved = {}
        self.patch(COMMIT_MOVES=True, STATE=self.tmp / "state",
                   LOCAL=self.tmp / "local", SESSIONS_DIR=self.tmp / "sessions")
        (self.tmp / "local").mkdir()
        self.use(self.repo)
        for p in (mock.patch.object(config, "work_root", lambda: self.current),):
            p.start()
            self.addCleanup(p.stop)
        self.as_user(ADA)
        movements.forget()
        self.addCleanup(movements.forget)
        saved = dict(state.AGENTS)
        state.AGENTS.clear()
        self.addCleanup(lambda: (state.AGENTS.clear(), state.AGENTS.update(saved)))
        self.addCleanup(execowner.release_lock)

    def clone(self, name: str) -> Path:
        path = self.tmp / name
        if not any(self.origin.joinpath("refs", "heads").iterdir()):
            path.mkdir()
            git(path, "init", "-q", "-b", "main")
            for slug in sorted(config.STAGE_DIRS) + ["archive"]:
                (path / "tasks" / slug).mkdir(parents=True)
                (path / "tasks" / slug / ".gitkeep").write_text("")
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", "root", user="root")
            git(path, "remote", "add", "origin", str(self.origin))
            git(path, "push", "-q", "origin", "main")
        else:
            subprocess.run(["git", "clone", "-q", str(self.origin), str(path)],
                           check=True, capture_output=True)
        return path

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def use(self, repo: Path) -> None:
        """Point the board at one clone: its tasks/ and its repository."""
        self.current = repo
        self.patch(TASKS=repo / "tasks", REPO=repo)
        p = mock.patch.object(config, "state_repo", lambda: repo)
        p.start()
        self.addCleanup(p.stop)

    def as_user(self, name: str, repo: Path | None = None) -> None:
        repo = repo or self.current
        git(repo, "config", "user.name", name)
        git(repo, "config", "user.email", f"{name.replace(' ', '.')}@example.com")

    # — the cards —

    def path(self, stage: str, filename: str = FILE) -> Path:
        return self.current / "tasks" / stage / filename

    def create(self, stage: str, *, by: str, filename: str = FILE,
               number: str = "7", title: str = "Export to CSV",
               extra: str = "") -> None:
        """Someone writes a card by hand and commits it."""
        self.path(stage, filename).write_text(card_text(stage, number, title, extra))
        git(self.current, "add", "-A")
        git(self.current, "commit", "-q", "-m", f"add {filename}", user=by)

    def move(self, source: str, target: str, *, by: str, actor: str = "you",
             filename: str = FILE) -> None:
        """A move made through the board, by whoever this board is."""
        self.as_user(by)
        taskfiles.move_task(filename, source, target, actor=actor)

    def edit(self, stage: str, *, by: str, filename: str = FILE) -> None:
        path = self.path(stage, filename)
        path.write_text(path.read_text() + f"\nAn edit by {by}.\n")
        git(self.current, "add", "-A")
        git(self.current, "commit", "-q", "-m", f"edit by {by}", user=by)

    def task(self, filename: str = FILE) -> dict:
        board = taskfiles.collect()
        return next(t for s in board["stages"] for t in s["tasks"]
                    if t["file"] == filename)

    def answer(self, filename: str = FILE) -> dict:
        return movements.eligibility(self.task(filename))

    # — orchestration in force, for the cases that ask authorize —

    def orchestrate(self, phases: dict | None = None) -> None:
        doc = agreements.new_draft()
        doc["bench"].update(enabled=True)
        doc["bench"]["authority"] = {w: True for w in agreements.AUTHORITIES}
        if phases:
            doc["phases"] = phases
        agreements.save_draft(doc, agreements.revision(agreements.draft_document()[0]))
        agreements.apply(agreements.effective()["revision"], agreements.revision(doc))
        execowner.designate("none")
        self.assertTrue(execowner.acquire_lock())


class ForeignToDoAndCurrentUserDelegation(Board):
    def test_a_foreign_users_to_do_move_is_ineligible(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ELENA)
        self.as_user(ADA)
        found = self.answer()
        self.assertFalse(found["eligible"])
        self.assertEqual(found["code"], "foreign-move")
        self.assertEqual(found["movement"]["mover"], ELENA)
        self.assertEqual(found["identity"]["name"], ADA)
        self.assertIn("not cryptographic", found["attribution"])
        self.assertTrue(found["remedy"])

    def test_a_foreign_card_the_current_user_moves_into_to_do_is_eligible(self):
        self.create("backlog", by=ELENA)        # elena wrote it
        self.edit("backlog", by=ELENA)          # and edited it
        self.move("backlog", "to-do", by=ADA)   # ada committed to it
        found = self.answer()
        self.assertTrue(found["eligible"], found)
        self.assertEqual(found["code"], "own-move")
        self.assertEqual(found["movement"]["from"], "backlog")
        self.assertEqual(found["movement"]["to"], "to-do")
        self.assertEqual(found["movement"]["source"], "commit")
        # and the same card reads as somebody else's on elena's board
        self.as_user(ELENA)
        self.assertEqual(self.answer()["code"], "foreign-move")

    def test_the_move_commit_carries_its_provenance(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        body = git(self.repo, "log", "-1", "--format=%B")
        self.assertTrue(body.startswith("board: 7 → to-do (ada)\n"),
                        "the subject sync and people read is unchanged")
        self.assertIn("Bench-Move: 1", body)
        self.assertIn("Bench-Mover: ada", body)
        self.assertIn("Bench-Mover-Kind: person", body)


class EditsRecordsAndTransportDoNotSubstitute(Board):
    def setUp(self):
        super().setUp()
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)

    def test_content_edits_by_either_user_neither_steal_nor_reacquire(self):
        self.edit("to-do", by=ELENA)
        self.as_user(ADA)
        self.assertTrue(self.answer()["eligible"], "an edit does not steal it")
        self.as_user(ELENA)
        self.assertEqual(self.answer()["code"], "foreign-move",
                         "nor does editing acquire it")
        self.edit("to-do", by=ADA)
        self.assertEqual(self.answer()["code"], "foreign-move")

    def test_comments_claims_and_records_are_not_moves(self):
        self.as_user(ELENA)
        taskfiles.add_comment(FILE, "to-do", "I think this needs a spec", ELENA)
        taskfiles.set_assignee(FILE, "to-do", ADA)
        taskfiles.append_to_section(FILE, "to-do", "Phase log", "- a line",
                                    "phase log")
        self.as_user(ADA)
        self.assertTrue(self.answer()["eligible"])

    def test_the_boards_automated_moves_preserve_the_delegation(self):
        self.move("to-do", "in-progress", by=ADA)
        # elena's board ran the agent that moved it on: bookkeeping, not
        # elena taking it over
        self.move("in-progress", "review", by=ELENA, actor="agent")
        self.as_user(ADA)
        found = self.answer()
        self.assertTrue(found["eligible"], found)
        self.assertEqual(found["movement"]["to"], "in-progress",
                         "the qualifying move is the person's, not the agent's")
        body = git(self.repo, "log", "-1", "--format=%B")
        self.assertIn("Bench-Mover-Kind: automated", body)

    def test_an_automated_move_never_qualifies_a_card_nobody_moved(self):
        self.create("in-progress", by=ELENA, filename="8-other.md", number="8")
        self.move("in-progress", "review", by=ADA, actor="phase",
                  filename="8-other.md")
        self.assertEqual(self.answer("8-other.md")["code"], "no-movement")

    def test_a_pull_of_someone_elses_edit_changes_nothing(self):
        git(self.repo, "push", "-q", "origin", "main")
        other = self.clone("b")
        self.use(other)
        self.edit("to-do", by=ELENA)
        git(other, "push", "-q", "origin", "main")
        self.use(self.repo)
        git(self.repo, "pull", "-q", "--ff-only", "origin", "main")
        self.as_user(ADA)
        self.assertTrue(self.answer()["eligible"])

    def test_a_replayed_own_move_stays_own_and_a_merge_attributes_nothing(self):
        git(self.repo, "push", "-q", "origin", "main")
        other = self.clone("b")
        # ada moves on her board, elena edits and pushes first
        self.move("to-do", "in-progress", by=ADA)
        self.use(other)
        self.edit("to-do", by=ELENA)
        git(other, "push", "-q", "origin", "main")
        # sync replays ada's commit on top: committer ada, author unchanged
        self.use(self.repo)
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "rebase", "-q", "origin/main", user=ADA)
        self.as_user(ADA)
        self.assertEqual(self.answer()["code"], "own-move")
        # a merge commit ada makes of elena's move is transport, not ada's move
        git(self.repo, "push", "-q", "origin", "main")
        self.use(other)
        git(other, "pull", "-q", "--ff-only", "origin", "main")
        self.move("in-progress", "review", by=ELENA)
        git(other, "push", "-q", "origin", "main")
        self.use(self.repo)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "local", user=ADA)
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", "origin/main",
            user=ADA)
        self.as_user(ADA)
        found = self.answer()
        self.assertEqual(found["code"], "foreign-move")
        self.assertEqual(found["movement"]["mover"], ELENA)


class LatestMoveDecidesAtOnce(Board):
    def test_a_later_move_by_someone_else_revokes_without_undoing_it(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        self.orchestrate()
        self.assertTrue(orchestrator.authorize("readiness", FILE)["allowed"])
        self.move("to-do", "in-progress", by=ELENA)
        self.as_user(ADA)
        refused = orchestrator.authorize("implement", FILE)
        self.assertFalse(refused["allowed"])
        self.assertEqual(refused["code"], "not-eligible")
        self.assertEqual(refused["eligibility"]["code"], "foreign-move")
        self.assertTrue(self.path("in-progress").is_file(),
                        "elena's move stands")
        # and ada moving it again is what brings it back
        self.move("in-progress", "to-do", by=ADA)
        self.assertTrue(orchestrator.authorize("readiness", FILE)["allowed"])

    def test_backlog_and_done_are_never_pulled_back(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        self.move("to-do", "backlog", by=ADA)
        self.orchestrate()
        before = git(self.repo, "rev-parse", "HEAD")
        refused = orchestrator.authorize("readiness", FILE)
        self.assertEqual(refused["code"], "stage-not-managed")
        self.move("backlog", "done", by=ADA)
        self.assertEqual(orchestrator.authorize("readiness", FILE)["code"],
                         "stage-not-managed")
        self.assertTrue(self.path("done").is_file())
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), before)

    def test_a_card_someone_else_holds_is_not_acquired(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ELENA)    # elena claims it
        self.move("to-do", "backlog", by=ELENA)    # walked back: unclaimed
        self.move("backlog", "to-do", by=ADA)      # ada claims it
        self.assertTrue(self.answer()["eligible"])
        self.as_user(ELENA)
        taskfiles.set_assignee(FILE, "to-do", ELENA)   # elena takes it over
        self.move("to-do", "in-progress", by=ADA)      # ada's move, elena holds
        found = self.answer()
        self.assertEqual(found["code"], "held-by-other")
        self.assertIn(ELENA, found["reason"])


class DeterministicHistory(Board):
    def test_a_rename_keeps_the_movement(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        git(self.repo, "mv", f"tasks/to-do/{FILE}", "tasks/to-do/7-export-csv.md")
        git(self.repo, "commit", "-q", "-m", "retitle", user=ELENA)
        self.assertEqual(self.answer("7-export-csv.md")["code"], "own-move")

    def test_a_persons_git_mv_is_their_move_and_a_rename_with_it_too(self):
        self.create("backlog", by=ELENA)
        git(self.repo, "mv", f"tasks/backlog/{FILE}", "tasks/to-do/7-csv.md")
        git(self.repo, "commit", "-q", "-m", "picked it up", user=ADA)
        found = self.answer("7-csv.md")
        self.assertEqual(found["code"], "own-move")
        self.assertEqual(found["movement"]["how"], "commit-author")

    def test_first_run_reconstruction_from_history_and_after_a_restart(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        first = self.answer()
        movements.forget()          # a restarted board has no memo at all
        self.assertEqual(self.answer(), first)

    def test_a_legacy_board_move_is_ambiguous_until_moved_again(self):
        self.create("backlog", by=ELENA)
        git(self.repo, "mv", f"tasks/backlog/{FILE}", f"tasks/to-do/{FILE}")
        git(self.repo, "commit", "-q", "-m", "board: 7 → to-do (ada)", user=ADA)
        found = self.answer()
        self.assertEqual(found["code"], "ambiguous-move")
        self.assertEqual(found["movement"]["why"], "legacy-board-move")
        self.move("to-do", "in-progress", by=ADA)
        self.assertEqual(self.answer()["code"], "own-move")

    def test_a_mover_that_is_not_the_commits_author_is_ambiguous(self):
        self.create("backlog", by=ELENA)
        git(self.repo, "mv", f"tasks/backlog/{FILE}", f"tasks/to-do/{FILE}")
        git(self.repo, "commit", "-q", "-m",
            "board: 7 → to-do (ada)" + taskfiles.movement_trailers("m-1", ADA, "person"),
            user=ELENA)
        found = self.answer()
        self.assertEqual(found["code"], "ambiguous-move")
        self.assertEqual(found["movement"]["why"], "attribution-conflict")

    def test_identity_is_literal_and_a_changed_name_is_somebody_else(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        # literal, as every claim compares it: no case folding, and a
        # renamed identity is a different person to this board
        for other in ("Ada", "ADA", "Ada Lovelace"):
            self.as_user(other)
            self.assertEqual(self.answer()["code"], "foreign-move", other)
        git(self.repo, "config", "--unset", "user.name")
        with mock.patch.object(taskfiles, "actor_name", lambda: ""):
            self.assertEqual(self.answer()["code"], "no-identity")

    def test_same_user_moves_arriving_through_sync_qualify(self):
        self.create("backlog", by=ELENA)
        git(self.repo, "push", "-q", "origin", "main")
        laptop = self.clone("b")
        self.use(laptop)
        self.move("backlog", "to-do", by=ADA)      # ada, on another machine
        git(laptop, "push", "-q", "origin", "main")
        self.use(self.repo)
        git(self.repo, "pull", "-q", "--ff-only", "origin", "main")
        self.as_user(ADA)
        self.assertEqual(self.answer()["code"], "own-move")
        self.as_user(ELENA)
        self.assertEqual(self.answer()["code"], "foreign-move",
                         "arriving here does not make it this board's")

    def test_created_in_to_do_is_not_a_move(self):
        self.create("to-do", by=ADA)
        self.assertEqual(self.answer()["code"], "no-movement")

    def test_a_hand_move_after_the_last_record_is_unrecorded(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        shutil.move(str(self.path("to-do")), str(self.path("in-progress")))
        found = self.answer()
        self.assertEqual(found["code"], "unrecorded-move")

    def test_history_cut_short_says_so(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA, actor="agent")
        with mock.patch.object(movements, "HISTORY_LIMIT", 1):
            movements.forget()
            self.assertEqual(self.answer()["code"], "history-incomplete")

    def test_uncommitted_moves_are_journalled_and_survive_a_restart(self):
        self.create("backlog", by=ELENA)
        self.patch(COMMIT_MOVES=False)
        before = git(self.repo, "rev-parse", "HEAD")
        self.move("backlog", "to-do", by=ADA)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        movements.forget()
        found = self.answer()
        self.assertEqual(found["code"], "own-move")
        self.assertEqual(found["movement"]["source"], "journal")
        self.move("to-do", "in-progress", by=ELENA)
        self.as_user(ADA)
        self.assertEqual(self.answer()["code"], "foreign-move")

    def _journal_then_foreign_round_trip(self):
        """Ada moves the card without a commit, the tree is committed by
        hand, then Elena moves it on and back to the same stage with her
        own `git mv` commits — a person's moves that claim nothing, so no
        Assignee hides what the movement reading says."""
        self.create("backlog", by=ELENA)
        self.patch(COMMIT_MOVES=False)
        self.move("backlog", "to-do", by=ADA)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "wip", user=ADA)
        for source, target in (("to-do", "in-progress"), ("in-progress", "to-do")):
            git(self.repo, "mv", f"tasks/{source}/{FILE}", f"tasks/{target}/{FILE}")
            git(self.repo, "commit", "-q", "-m", f"7 to {target}", user=ELENA)
        self.as_user(ADA)
        movements.forget()
        self.assertIsNone(self.task().get("assignee"))

    def test_a_later_foreign_commit_outranks_an_older_journal_line(self):
        # PR 147 review, item 1: a journal line used to sit above every
        # commit, so Elena's later round trip back to To Do left the card
        # reading as Ada's own move.
        self._journal_then_foreign_round_trip()
        found = self.answer()
        self.assertEqual(found["code"], "foreign-move", found["reason"])
        self.assertEqual(found["movement"]["mover"], ELENA)
        self.assertEqual(found["movement"]["source"], "commit")
        line = [json.loads(l) for l in taskfiles.movement_journal()
                .read_text().splitlines()][0]
        self.assertRegex(line["base"] or "", r"^[0-9a-f]{40}$",
                         "a journal line records the head it was made on")

    def test_a_journal_line_without_a_head_is_placed_by_time(self):
        self._journal_then_foreign_round_trip()
        path = taskfiles.movement_journal()
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        for line in lines:          # written before `base` was recorded
            line.pop("base", None)
            line["at"] = "2000-01-01T00:00:00Z"
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        movements.forget()
        self.assertEqual(self.answer()["code"], "foreign-move")

    def test_a_journal_line_still_outranks_the_commits_before_it(self):
        self._journal_then_foreign_round_trip()
        self.patch(COMMIT_MOVES=False)
        self.move("to-do", "in-progress", by=ADA)
        movements.forget()
        found = self.answer()
        self.assertEqual(found["code"], "own-move", found["reason"])
        self.assertEqual(found["movement"]["source"], "journal")

    def test_a_committed_journal_line_is_not_counted_twice(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        self.assertFalse(taskfiles.movement_journal().exists(),
                         "a committed move is recorded by its commit alone")


class OneAnswerForLaunchAndPage(Board):
    PHASE = ("# 50 — Ship export\n\n**Status:** {status}\n**Priority:** High\n"
             "**Type:** Phase\n\n## Cards\n\n- 51 — The member\n"
             # adopted into its phase scope (task 143): what these cases
             # read is the delegation, not which scheduler runs it
             "\n## Phase log\n\n- 2026-11-01 10:00 · scope adopted by ada — "
             "the phase:50 scope schedules this phase from here\n")

    def phase(self) -> None:
        self.path("backlog", "50-ship.md").write_text(
            self.PHASE.format(status="Backlog"))
        self.path("to-do", "51-member.md").write_text(
            card_text("to-do", "51", "The member"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "write the phase", user=ELENA)

    def test_authorize_and_the_reading_carry_the_same_answer(self):
        self.create("backlog", by=ELENA)
        self.move("backlog", "to-do", by=ADA)
        self.create("to-do", by=ELENA, filename="9-theirs.md", number="9")
        self.move("to-do", "in-progress", by=ELENA, filename="9-theirs.md")
        self.as_user(ADA)
        self.orchestrate()
        before_agents = dict(state.AGENTS)
        head = git(self.repo, "rev-parse", "HEAD")
        found = orchestrator.reading()
        cards = {c["card"]: c for c in found["cards"]}
        for filename in (FILE, "9-theirs.md"):
            launch = orchestrator.authorize("implement", filename)
            self.assertEqual(launch.get("eligibility"), cards[filename]["eligibility"],
                             filename)
        self.assertTrue(cards[FILE]["eligibility"]["eligible"])
        self.assertEqual(cards["9-theirs.md"]["eligibility"]["code"], "foreign-move")
        self.assertEqual(found["identity"]["name"], ADA)
        self.assertIn("not cryptographic", found["identity"]["attribution"])
        # reading launched, moved and committed nothing
        self.assertEqual(state.AGENTS, before_agents)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "tasks"), "")

    def test_a_reading_of_many_cards_reads_the_history_once(self):
        for n in range(10, 16):
            self.create("to-do", by=ELENA, filename=f"{n}-c.md", number=str(n))
        self.orchestrate()
        calls = []
        real = movements._git_history
        with mock.patch.object(movements, "_git_history",
                               lambda: calls.append(1) or real()):
            found = orchestrator.reading()
        self.assertEqual(len(found["cards"]), 6)
        self.assertEqual(len(calls), 1, "one history read per reading")

    def test_a_phase_delegates_its_members_and_no_second_scope_acquires_them(self):
        self.phase()
        self.move("backlog", "to-do", by=ADA, filename="50-ship.md")
        self.orchestrate(phases={"50": {"enabled": True, "authority": {
            w: True for w in agreements.AUTHORITIES},
            "allocation": {"implementation": 1, "dailyStarts": 4}}})
        member = orchestrator.authorize("implement", "51-member.md")
        self.assertTrue(member["allowed"], member)
        self.assertEqual(member["scope"], "phase:50", "the phase's, never Bench's")
        self.assertEqual(member["eligibility"]["code"], "phase-delegated")
        # ada works the member; then elena moves it on by hand: the member's
        # own last move is elena's, and the phase does not override it
        self.move("to-do", "in-progress", by=ADA, filename="51-member.md")
        self.move("in-progress", "review", by=ELENA, filename="51-member.md")
        self.as_user(ADA)
        refused = orchestrator.authorize("review", "51-member.md")
        self.assertEqual(refused["code"], "not-eligible")
        self.assertEqual(refused["eligibility"]["code"], "member-unreconciled")
        self.assertEqual(refused["eligibility"]["movement"]["mover"], ELENA)
        # ada moving it herself is the visible reconciliation
        self.move("review", "in-progress", by=ADA, filename="51-member.md")
        self.assertTrue(orchestrator.authorize("implement", "51-member.md")["allowed"])
        # and a forward move from to-do by elena claims it: held, not taken
        self.move("in-progress", "to-do", by=ADA, filename="51-member.md")
        taskfiles.move_task("51-member.md", "to-do", "backlog")
        self.move("backlog", "to-do", by=ELENA, filename="51-member.md")
        self.as_user(ADA)
        self.assertEqual(self.answer("51-member.md")["code"], "member-held")

    def test_a_member_someone_else_holds_needs_reconciliation(self):
        self.phase()
        self.move("backlog", "to-do", by=ADA, filename="50-ship.md")
        self.as_user(ELENA)
        taskfiles.set_assignee("51-member.md", "to-do", ELENA)
        self.as_user(ADA)
        found = self.answer("51-member.md")
        self.assertEqual(found["code"], "member-held")
        self.assertEqual(found["via"], "phase:50")

    def test_a_phase_someone_else_committed_delegates_nothing(self):
        self.phase()
        self.move("backlog", "to-do", by=ELENA, filename="50-ship.md")
        self.as_user(ADA)
        found = self.answer("51-member.md")
        self.assertEqual(found["code"], "phase-not-delegated")
        self.assertEqual(found["phase"]["code"], "foreign-move")

    def test_a_legacy_phase_stays_the_runners_whatever_the_movement(self):
        self.phase()
        self.move("backlog", "to-do", by=ADA, filename="50-ship.md")
        self.orchestrate()          # no phase agreement for 50
        self.assertEqual(orchestrator.authorize("implement", "51-member.md")["code"],
                         "legacy-phase")


if __name__ == "__main__":
    unittest.main()
