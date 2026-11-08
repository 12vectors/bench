"""Done means delivered: a merge that did not publish is not a completion
(task 63).

Merge & clean up used to record a failed push in the ticker and carry on —
delete the branch, remove the worktree, sweep the phase, move the card to
done/. These run the real thing against disposable repositories with
local bare remotes: a pre-receive hook that refuses, a push whose reply is
lost, a push that "succeeds" without the remote holding anything, a
remote that moved on, a GitHub merge that is refused or whose reply is
lost, a separate ticket repository. Nothing here reaches a network or a
real PR.

    python3 -m unittest tests.test_completion_publication -v
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import drive as drive_mod  # noqa: E402
import github  # noqa: E402
import state  # noqa: E402

FILENAME = "63-a-card-to-deliver.md"
STEM = FILENAME[:-3]
BRANCH = f"task/{STEM}"
PR_URL = "https://github.com/acme/widget/pull/63"

CARD = ("# 63 — A card to deliver\n\n"
        "**Status:** Review\n**Priority:** High\n{pr}\n"
        "Body text long enough that git sees a rename rather than a delete\n"
        "and an add when the file moves between stage directories.\n")

REFUSE = "#!/bin/sh\necho 'protected branch hook declined: main is protected' >&2\nexit 1\n"

# gh for the merge on origin: logs every call, and does to the bare remote
# what the GH_MODE says GitHub did.
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import fake_origin
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
mode = os.environ.get("GH_MODE", "ok")
if args[:2] == ["pr", "view"]:
    print(json.dumps({{"url": "{PR_URL}", "baseRefName": "main"}}))
elif args[:2] == ["pr", "merge"] and "--help" not in args:
    if mode == "refused":
        sys.stderr.write("GraphQL: Required status check is expected (mergePullRequest)\\n")
        sys.exit(1)
    if mode == "lost-reply":
        fake_origin.merge_pr(fake_origin.origin_url(), "{BRANCH}")
        sys.stderr.write("Post https://api.github.com/graphql: net/http: timeout\\n")
        sys.exit(1)
    if mode == "silent":
        sys.exit(0)           # says merged; the remote holds nothing new
    fake_origin.merge_pr(fake_origin.origin_url(), "{BRANCH}")
sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class Delivery(unittest.TestCase):
    """One application checkout with a bare `origin`, one card in review/
    with a branch, a worktree and a commit on it. The ticket lives in the
    application's repository (vendored) unless a subclass says otherwise."""

    MERGE = "local"
    PR = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-deliver-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.origin)], check=True, capture_output=True)
        self.app = self.tmp / "app"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.app)],
                       check=True, capture_output=True)
        git(self.app, "config", "user.name", "ada")
        git(self.app, "config", "user.email", "ada@example.com")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        self.tasks = self.tasks_root()
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True, exist_ok=True)
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "root")
        git(self.app, "push", "-q", "origin", "main")
        (self.tasks / "review" / FILENAME).write_text(
            CARD.format(pr=f"**PR:** {PR_URL}\n" if self.PR else ""),
            encoding="utf-8")

        self.gh_log = self.tmp / "gh.log"
        gh = self.tmp / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        os.environ["GH_LOG"] = str(self.gh_log)
        self.addCleanup(os.environ.pop, "GH_LOG", None)
        self.addCleanup(os.environ.pop, "GH_MODE", None)

        self.patch(REPO=self.app, TASKS=self.tasks,
                   WORKTREES=self.tmp / "worktrees",
                   SESSIONS_DIR=self.tmp / "sessions",
                   PLANS_DIR=self.tmp / "state" / "plans",
                   COMPLETIONS_DIR=self.tmp / "state" / "completions",
                   SYNC=False, COMMIT_MOVES=False, APP_MERGE=self.MERGE,
                   APP_BASE="main", GIT_REMOTE="", GH_BIN=str(gh))
        self.layout()

        self.worktree = config.WORKTREES / STEM
        git(self.app, "worktree", "add", "-q", "-b", BRANCH, str(self.worktree))
        (self.worktree / "feature.txt").write_text("the work\n", encoding="utf-8")
        git(self.worktree, "add", "-A")
        git(self.worktree, "commit", "-q", "-m", "the work")
        git(self.worktree, "push", "-q", "origin", BRANCH)
        self.work = git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        for registry in (state.COMPLETING, state.BOARD_EVENTS,
                         state.EXPECTED_MOVES, github.PR_STATE):
            registry.clear()
            self.addCleanup(registry.clear)
        self.addCleanup(setattr, drive_mod, "DRIVE", drive_mod.DRIVE)
        drive_mod.DRIVE = None
        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

    def tasks_root(self) -> Path:
        return self.app / "tasks"

    def layout(self) -> None:
        """Vendored: one repository for both identities."""

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    # — reading the outcome —

    def stage_of(self) -> str | None:
        for slug in config.STAGE_DIRS:
            if (self.tasks / slug / FILENAME).is_file():
                return slug
        return None

    def complete(self) -> dict:
        return github.complete_task(FILENAME, self.stage_of())

    def remote_has(self, ref: str) -> str:
        return git(self.origin, "rev-parse", "--verify", "--quiet",
                   f"refs/heads/{ref}").stdout.strip()

    def remote_holds_work(self) -> bool:
        return git(self.origin, "merge-base", "--is-ancestor", self.work,
                   "refs/heads/main").returncode == 0

    def local_holds_work(self) -> bool:
        return git(self.app, "merge-base", "--is-ancestor", self.work,
                   "main").returncode == 0

    def branch_here(self) -> bool:
        return git(self.app, "rev-parse", "--verify", "--quiet",
                   BRANCH).returncode == 0

    def said(self) -> str:
        return "\n".join(e["summary"] for e in state.BOARD_EVENTS
                         if e.get("file") == FILENAME)

    def refuse_pushes(self) -> None:
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text(REFUSE, encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    def accept_pushes(self) -> None:
        (self.origin / "hooks" / "pre-receive").unlink()

    def merges_on_main(self) -> int:
        return int(git(self.app, "rev-list", "--count", "--merges",
                       "main").stdout.strip() or 0)

    def gh_merges(self) -> int:
        if not self.gh_log.exists():
            return 0
        return sum(1 for line in self.gh_log.read_text().splitlines()
                   if line.startswith('["pr", "merge"') and "--help" not in line)

    def assert_held(self, result: dict, publication: str) -> None:
        """Merged, not delivered: everything a retry needs is still here."""
        self.assertTrue(result["merged"])
        self.assertFalse(result["published"])
        self.assertFalse(result["moved"])
        self.assertEqual(result["publication"]["state"], publication)
        self.assertEqual(self.stage_of(), "review", "an undelivered card was marked Done")
        self.assertTrue(self.branch_here(), "the task branch was deleted")
        self.assertTrue(self.worktree.is_dir(), "the worktree was removed")
        self.assertEqual(self.remote_has(BRANCH), self.work,
                         "the remote task branch was deleted")
        self.assertEqual(github.recovery(FILENAME)["publication"], publication)
        self.assertEqual(state.COMPLETING, {}, "the card is given back")


class TheLocalMergeIsDeliveredOrHeld(Delivery):

    def test_the_normal_path_publishes_cleans_up_and_moves(self):
        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertEqual(result["publication"]["state"], "published")
        self.assertTrue(self.remote_holds_work())
        self.assertEqual(result["cleanup"]["worktree"], "removed")
        self.assertEqual(result["cleanup"]["branch"], "deleted")
        self.assertEqual(result["cleanup"]["remoteBranch"], "gone")
        self.assertEqual(self.remote_has(BRANCH), "")
        self.assertTrue(result["moved"])
        self.assertEqual(self.stage_of(), "done")
        self.assertIsNone(github.recovery(FILENAME), "a finished completion leaves no record")
        self.assertIn(f"origin/main holds {self.work[:12]}", self.said())

    def test_a_rejected_push_keeps_everything_and_says_what_happened(self):
        self.refuse_pushes()

        result = self.complete()

        self.assert_held(result, "failed")
        self.assertTrue(self.local_holds_work(), "the local merge did land")
        self.assertFalse(self.remote_holds_work())
        self.assertIn("main is protected", result["publication"]["detail"],
                      "the remote's refusal is shown, not swallowed")
        said = self.said()
        self.assertIn("merged into main in this checkout", said)
        self.assertIn("but the push failed", said)
        self.assertIn("Nothing was cleaned up and the card stays in review/", said)
        self.assertNotIn("cleaned up:", said)
        self.assertNotIn("rolled back", said, "no rollback is claimed")

    def test_the_retry_publishes_once_without_merging_again(self):
        git(self.app, "commit", "-q", "--allow-empty", "-m", "main moved on")
        git(self.app, "push", "-q", "origin", "main")
        self.refuse_pushes()
        self.complete()
        merges = self.merges_on_main()
        self.accept_pushes()
        state.BOARD_EVENTS.clear()

        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertEqual(self.merges_on_main(), merges, "the merge was made twice")
        self.assertIn("nothing is merged again", self.said())
        self.assertTrue(self.remote_holds_work())
        self.assertEqual(self.stage_of(), "done")
        self.assertFalse(self.branch_here())
        self.assertIsNone(github.recovery(FILENAME))

    def test_a_restart_keeps_the_recovery_context(self):
        """The record is on disk: a board started afresh reads it, the page
        is told about it, and the retry still finishes it."""
        self.refuse_pushes()
        self.complete()
        state.BOARD_EVENTS.clear()
        github.PR_STATE.clear()            # what a restart loses

        self.assertIn(FILENAME, github.recoveries())
        import httpd
        self.assertIn(FILENAME, httpd.state_payload()["unpublished"])
        self.assertEqual(github.recoveries()[FILENAME]["commit"], self.work)

        self.accept_pushes()
        self.assertTrue(self.complete()["published"])
        self.assertEqual(github.recoveries(), {})

    def test_a_reply_lost_after_the_push_landed_is_published(self):
        original = github._run

        def lossy(cmd, *args, **kwargs):
            done = original(cmd, *args, **kwargs)
            if cmd[:2] == ["git", "push"] and "--delete" not in cmd \
                    and not any(str(p).startswith("--force") for p in cmd):
                return subprocess.CompletedProcess(
                    cmd, 128, "", "fatal: the remote end hung up unexpectedly")
            return done
        self.addCleanup(setattr, github, "_run", original)
        github._run = lossy

        result = self.complete()

        self.assertEqual(result["publication"]["state"], "published")
        self.assertIn("the reply was lost", self.said())
        self.assertEqual(self.stage_of(), "done")

    def test_a_push_that_reports_success_but_delivers_nothing_is_not_published(self):
        """An unrelated success — the exit status of something that did not
        put the commit on the remote's base — is not delivery."""
        original = github._run

        def hollow(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "push"] and "--delete" not in cmd \
                    and not any(str(p).startswith("--force") for p in cmd):
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return original(cmd, *args, **kwargs)
        self.addCleanup(setattr, github, "_run", original)
        github._run = hollow

        result = self.complete()

        self.assert_held(result, "unconfirmed")
        self.assertIn("does not contain it", result["publication"]["detail"])

    def test_a_remote_that_moved_on_is_reported_as_diverged(self):
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True)
        git(other, "-c", "user.name=bo", "-c", "user.email=bo@x", "commit", "-q",
            "--allow-empty", "-m", "somebody else's main")
        git(other, "push", "-q", "origin", "main")

        result = self.complete()

        self.assert_held(result, "failed")
        self.assertTrue(result["publication"]["diverged"])
        self.assertIn("has commits this checkout does not", self.said())
        # the person brings origin's main in — additively — and retries
        git(self.app, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
        result = self.complete()
        self.assertTrue(result["published"], result)
        self.assertEqual(self.stage_of(), "done")

    def test_a_dirty_worktree_is_kept_and_reported(self):
        (self.worktree / "notes.txt").write_text("in the middle of it\n",
                                                 encoding="utf-8")

        result = self.complete()

        self.assertTrue(result["published"])
        self.assertEqual(result["cleanup"]["worktree"], "kept")
        self.assertEqual(result["cleanup"]["branch"], "kept")
        self.assertIn("1 uncommitted change in it", result["cleanup"]["notes"][0])
        self.assertTrue((self.worktree / "notes.txt").is_file())
        self.assertEqual(self.stage_of(), "done", "the work is delivered")

    def test_a_branch_git_will_not_delete_is_reported_not_claimed(self):
        original = github._run

        def stubborn(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "branch", "-d"]:
                return subprocess.CompletedProcess(
                    cmd, 1, "", f"error: cannot delete branch '{BRANCH}'")
            return original(cmd, *args, **kwargs)
        self.addCleanup(setattr, github, "_run", original)
        github._run = stubborn

        result = self.complete()

        self.assertEqual(result["cleanup"]["branch"], "kept")
        self.assertTrue(self.branch_here())
        self.assertNotIn("cleaned up: worktree and local branch", self.said())
        self.assertIn("git would not delete it", self.said())
        self.assertEqual(self.stage_of(), "done")

    def test_a_branch_that_moved_during_recovery_is_kept_and_not_done(self):
        """The retry publishes what was merged — and only that. Newer work
        on the branch is not in the base, so the card is not Done."""
        self.refuse_pushes()
        self.complete()
        (self.worktree / "late.txt").write_text("later\n", encoding="utf-8")
        git(self.worktree, "add", "-A")
        git(self.worktree, "commit", "-q", "-m", "after the merge")
        late = git(self.worktree, "rev-parse", "HEAD").stdout.strip()
        self.accept_pushes()

        result = self.complete()

        self.assertTrue(result["published"])
        self.assertTrue(result.get("partial"), result)
        self.assertEqual(self.stage_of(), "review")
        self.assertEqual(git(self.app, "rev-parse", BRANCH).stdout.strip(), late)
        self.assertFalse(git(self.origin, "merge-base", "--is-ancestor", late,
                             "refs/heads/main").returncode == 0)

    def test_just_moving_the_card_merges_pushes_and_cleans_nothing(self):
        import taskfiles
        before = git(self.origin, "rev-parse", "main").stdout

        taskfiles.move_task(FILENAME, "review", "done")

        self.assertEqual(self.stage_of(), "done")
        self.assertFalse(self.local_holds_work())
        self.assertEqual(git(self.origin, "rev-parse", "main").stdout, before)
        self.assertTrue(self.branch_here())
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.remote_has(BRANCH), self.work)

    def test_a_changed_merge_mode_needs_a_decision(self):
        self.refuse_pushes()
        self.complete()
        self.patch(APP_MERGE="origin")

        with self.assertRaises(ValueError) as caught:
            self.complete()

        self.assertIn("publication is unfinished", str(caught.exception))
        self.assertEqual(self.stage_of(), "review")
        self.assertIsNotNone(github.recovery(FILENAME))

    def test_forgetting_the_record_changes_no_git(self):
        self.refuse_pushes()
        self.complete()
        before = (git(self.app, "rev-parse", "main").stdout,
                  git(self.app, "rev-parse", BRANCH).stdout)

        self.assertTrue(github.forget_recovery(FILENAME))

        self.assertIsNone(github.recovery(FILENAME))
        self.assertEqual(before, (git(self.app, "rev-parse", "main").stdout,
                                  git(self.app, "rev-parse", BRANCH).stdout))
        self.assertTrue(self.worktree.is_dir())

    def test_the_record_keeps_machine_local_data_out_of_the_ticket(self):
        self.refuse_pushes()
        self.complete()
        ticket = (self.tasks / "review" / FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp), ticket)
        self.assertNotIn("publication", ticket)
        record = config.COMPLETIONS_DIR / f"{STEM}.json"
        self.assertTrue(record.is_file())
        self.assertNotIn(str(config.WORKTREES), record.read_text(encoding="utf-8"))


class ALocalOnlyApplication(Delivery):
    """No remote at all: no push is required, and none is claimed."""

    def setUp(self):
        super().setUp()
        git(self.app, "remote", "remove", "origin")

    def test_it_completes_and_says_nothing_was_published(self):
        result = self.complete()

        self.assertEqual(result["publication"]["state"], "local-only")
        self.assertFalse(result["published"])
        self.assertTrue(result["moved"])
        self.assertEqual(self.stage_of(), "done")
        self.assertIn("nothing was pushed or merged on GitHub", self.said())
        self.assertNotIn("PR marked merged", self.said())

    def test_the_sheet_is_told_there_is_nothing_to_publish(self):
        facts = github.completion_facts(FILENAME, "review")
        self.assertEqual((facts["mode"], facts["publication"], facts["remote"]),
                         ("local", "none", ""))


class TheMergeOnOrigin(Delivery):
    MERGE = "origin"
    PR = True

    def test_a_merge_github_made_is_confirmed_then_cleaned_up(self):
        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertTrue(self.remote_holds_work())
        self.assertEqual(self.stage_of(), "done")
        self.assertIn("confirmed: origin/main holds", self.said())

    def test_a_refused_merge_changes_nothing(self):
        os.environ["GH_MODE"] = "refused"

        with self.assertRaises(ValueError) as caught:
            self.complete()

        self.assertIn("Required status check", str(caught.exception),
                      "a branch-protection refusal stays visible")
        self.assertEqual(self.stage_of(), "review")
        self.assertTrue(self.branch_here())
        self.assertEqual(self.remote_has(BRANCH), self.work)
        self.assertIsNone(github.recovery(FILENAME), "nothing merged, nothing to recover")

    def test_a_lost_reply_is_reconciled_not_refused(self):
        os.environ["GH_MODE"] = "lost-reply"

        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertIn("the reply was lost", self.said())
        self.assertEqual(self.stage_of(), "done")

    def test_a_merge_gh_reports_but_the_remote_lacks_is_held(self):
        """gh exiting 0 is not the remote holding the work — a wrong
        repository, a queued merge. Held; and the retry does not ask
        GitHub to merge what it already has."""
        os.environ["GH_MODE"] = "silent"

        result = self.complete()

        self.assert_held(result, "unconfirmed")
        self.assertIn("GitHub reported the merge", self.said())
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fake_origin
        fake_origin.merge_pr(str(self.origin), BRANCH)   # it lands later
        asked = self.gh_merges()

        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertEqual(self.gh_merges(), asked, "GitHub was asked to merge twice")
        self.assertEqual(self.stage_of(), "done")


class ASeparateTicketRepository(Delivery):
    """External layout: the ticket's own repository and remote accept
    everything; the application's refuses. The card must not move — a
    ticket-state push is not the application's publication."""

    def tasks_root(self) -> Path:
        self.state_origin = self.tmp / "state-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.state_origin)], check=True, capture_output=True)
        self.work_root = self.tmp / "work"
        subprocess.run(["git", "clone", "-q", str(self.state_origin),
                        str(self.work_root)], check=True, capture_output=True)
        git(self.work_root, "config", "user.name", "ada")
        git(self.work_root, "config", "user.email", "ada@example.com")
        (self.work_root / "README").write_text("tickets\n", encoding="utf-8")
        git(self.work_root, "add", "-A")
        git(self.work_root, "commit", "-q", "-m", "tickets")
        git(self.work_root, "push", "-q", "origin", "main")
        return self.work_root / "tasks"

    def layout(self) -> None:
        self.patch(APP_SETTING=str(self.app), WORK_SETTING=str(self.work_root),
                   EXTERNAL=True, WORK=self.work_root, COMMIT_MOVES=True,
                   STATE_BASE="main", STATE_GIT_REMOTE="")
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)
        git(self.work_root, "add", "-A")
        git(self.work_root, "commit", "-q", "-m", "board: 63 → review (setup)")

    def test_the_application_push_failing_keeps_the_ticket_in_review(self):
        self.refuse_pushes()
        tickets = git(self.work_root, "rev-parse", "HEAD").stdout

        result = self.complete()

        self.assert_held(result, "failed")
        self.assertEqual(git(self.work_root, "rev-parse", "HEAD").stdout, tickets,
                         "no ticket commit for a completion that did not happen")

    def test_delivered_code_commits_the_move_in_the_ticket_repository(self):
        app_main = git(self.app, "log", "--format=%s", "main").stdout

        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertEqual(self.stage_of(), "done")
        moves = git(self.work_root, "log", "--format=%s").stdout
        self.assertIn("board: 63 → done", moves)
        self.assertNotIn("board:", git(self.app, "log", "--format=%s", "main").stdout,
                         "a ticket move reached the application's history")
        self.assertNotEqual(app_main, git(self.app, "log", "--format=%s", "main").stdout)

    def test_the_sheet_says_ticket_sync_does_not_publish_code(self):
        self.patch(SYNC=True)
        facts = github.completion_facts(FILENAME, "review")
        self.assertTrue(facts["ticketsSeparate"])
        self.assertTrue(facts["ticketSync"])
        self.assertEqual(facts["mode"], "local",
                         "separate ticket sync does not make the app merge on origin")


class TheSheetIsToldTheTruth(Delivery):
    """`completion_facts` is the sheet's only source, and it reads the
    authorities the completion reads."""

    def test_ticket_sync_with_a_local_app_merge_describes_a_local_merge(self):
        self.patch(SYNC=True, APP_MERGE="local")
        facts = github.completion_facts(FILENAME, "review")
        self.assertEqual((facts["mode"], facts["publication"], facts["target"]),
                         ("local", "push", "main"))
        self.assertTrue(facts["ticketSync"])

    def test_origin_merge_is_described_whether_or_not_ticket_sync_is_on(self):
        for sync_on in (False, True):
            self.patch(SYNC=sync_on, APP_MERGE="origin")
            facts = github.completion_facts(FILENAME, "review")
            self.assertEqual((facts["mode"], facts["publication"]),
                             ("origin", "pr-merge"))

    def test_a_configured_base_other_than_main_is_named(self):
        git(self.app, "branch", "trunk", "main")
        self.patch(APP_BASE="trunk")
        facts = github.completion_facts(FILENAME, "review")
        self.assertEqual((facts["target"], facts["appBase"]), ("trunk", "trunk"))
        self.assertEqual(facts["refusal"], "")

    def test_a_stacked_card_is_refused_in_both_modes(self):
        git(self.app, "branch", "task/62-the-prerequisite", "main")
        text = (self.tasks / "review" / FILENAME).read_text(encoding="utf-8")
        (self.tasks / "review" / FILENAME).write_text(
            text.replace("**Priority:** High",
                         "**Priority:** High\n**Base branch:** task/62-the-prerequisite"),
            encoding="utf-8")
        for mode in ("local", "origin"):
            self.patch(APP_MERGE=mode)
            facts = github.completion_facts(FILENAME, "review")
            self.assertTrue(facts["stacked"], mode)
            self.assertIn("is stacked on task/62-the-prerequisite", facts["refusal"])
            with self.assertRaises(ValueError):
                self.complete()
            self.assertEqual(self.stage_of(), "review")

    def test_an_unfinished_completion_reaches_the_sheet(self):
        self.refuse_pushes()
        self.complete()
        facts = github.completion_facts(FILENAME, "review")
        self.assertEqual(facts["recovery"]["publication"], "failed")
        self.assertEqual(facts["recovery"]["commit"], self.work)


class TheSheetSource(unittest.TestCase):
    """board.html has no frontend runner — source-level invariants of what
    the person is told before and after they click."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")
        start = cls.html.index("function completeSheet")
        cls.sheet = cls.html[start:cls.html.index("\nfunction phaseSheet")]

    def test_the_sheet_no_longer_reads_ticket_sync(self):
        self.assertNotIn("S.state.sync", self.sheet,
                         "ticket sync does not decide how the application merges")
        self.assertIn("/api/task/completion", self.sheet)

    def test_the_three_choices_stay_distinct(self):
        for button in ("sh-keep", "sh-move", "sh-ship"):
            self.assertIn(button, self.sheet)
        self.assertIn("not a record that the code was published", self.sheet)

    def test_a_refusal_offers_no_merge(self):
        self.assertRegex(self.sheet, r"facts\.refusal\s*\?")

    def test_a_held_publication_is_not_toasted_as_done(self):
        self.assertIn("data.published === false", self.sheet)
        self.assertIn("publication failed", self.sheet)

    def test_the_card_wears_an_unpublished_merge(self):
        self.assertIn("(S.state.unpublished || {})[task.file]", self.html)
        self.assertIn("text: 'not published'", self.html)


class TheDocumentedContract(unittest.TestCase):
    def test_agents_md_describes_it(self):
        doc = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        section = doc.split("### Done means delivered")[1].split("\n## ")[0]
        for promise in ("Just move the card", "stays in `review/`", "retry",
                        "local-only", "lost", "operator", "BOARD_APP_MERGE"):
            self.assertIn(promise, section)
        self.assertTrue(re.search(r"ticket sync", section, re.I))


if __name__ == "__main__":
    unittest.main()
