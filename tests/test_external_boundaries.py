"""The boundaries an external layout makes load-bearing (task 56).

Four of them, each a place where bench used to be able to act on the
wrong thing because two identities were one:

- **uncommitted work is never thrown away.** An unchanged HEAD is not a
  clean checkout: a run that edited and never committed leaves work on
  disk, and forcing its worktree out was the one unrecoverable act in
  the codebase.
- **a worktree is identified by its repository, not by its name.**
  `<worktrees>/<stem>` can already hold a checkout of something else.
- **merging the application is not the same decision as syncing the
  board's state.** `BOARD_SYNC` used to decide both.
- **gh is told which repository it acts on** rather than inferring it
  from a checkout that may have several remotes.

Disposable repositories, stub agents and a stub `gh` throughout.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import agents  # noqa: E402
import commands as commands_mod  # noqa: E402
import config  # noqa: E402
import drive as drive_mod  # noqa: E402
import github  # noqa: E402
import phases  # noqa: E402
import reports  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")
CARD = "56-a-card.md"
STEM = CARD[:-3]
PR_URL = "https://github.com/acme/widget/pull/56"

# Edits a file and commits nothing — the run whose worktree used to be
# forced out from under it.
LEAVES_WORK = """#!/usr/bin/env python3
import os, sys
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "code.txt"), "w").write("half a thought\\n")
open(os.path.join(cwd, "scratch.md"), "w").write("notes nobody else has\\n")
print("API Error: 500 overloaded")
sys.exit(1)
"""

TOUCHES_NOTHING = """#!/usr/bin/env python3
import sys
print("API Error: 500 overloaded")
sys.exit(1)
"""

WORKS = """#!/usr/bin/env python3
import os, subprocess
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "built.txt"), "w").write("built\\n")
subprocess.run(["git", "-C", cwd, "add", "-A"], check=True)
subprocess.run(["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=stub",
                "commit", "-q", "-m", "built it"], check=True)
print("WORK REPORT: built")
"""

# A gh that behaves enough like the real one to be worth asserting
# against: it records its arguments, and `pr merge` really does land the
# branch on the bare upstream, the way GitHub would.
FAKE_GH = f'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
log = os.environ.get("GH_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "create"]:
    print("{PR_URL}")
elif args[:2] == ["pr", "merge"]:
    upstream, branch = os.environ["GH_UPSTREAM"], os.environ["GH_BRANCH"]
    subprocess.run(["git", "-C", upstream, "update-ref", "refs/heads/main",
                    "refs/heads/" + branch], check=True)
elif args[:2] == ["pr", "view"] and "--jq" in args:
    print("{PR_URL}")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"reviews": [], "reviewRequests": [],
                      "statusCheckRollup": [], "state": "OPEN",
                      "mergeable": "MERGEABLE"}}))
else:
    sys.exit(0)
'''


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def wait_for(pred, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def card(title: str, *, status: str = "In Progress", kind: str | None = None,
         cards: str | None = None, pr: str | None = None) -> str:
    text = f"# {title}\n\n**Status:** {status}\n**Priority:** High\n"
    if kind:
        text += f"**Type:** {kind}\n"
    if pr:
        text += f"**PR:** {pr}\n"
    text += "\nWhat this card is for, at enough length to read as a brief.\n"
    if cards is not None:
        text += f"\n## Cards\n\n{cards}\n"
    return text


class Workspace(unittest.TestCase):
    """An external layout: tool, application (cloned from a bare
    upstream), work state, worktrees — none inside another."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-bounds-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.upstream = self.tmp / "app-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.upstream)], check=True, capture_output=True)
        self.app = self.tmp / "app"
        subprocess.run(["git", "clone", "-q", str(self.upstream), str(self.app)],
                       check=True, capture_output=True)
        git(self.app, "config", "user.name", "appdev")
        git(self.app, "config", "user.email", "appdev@example.test")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "the application")
        git(self.app, "push", "-q", "origin", "main")

        self.work = self.tmp / "work"
        self.tasks = self.work / "tasks"
        for slug in (*STAGES, "archive"):
            (self.tasks / slug).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)],
                       check=True, capture_output=True)
        git(self.work, "config", "user.name", "boardkeeper")
        git(self.work, "config", "user.email", "board@example.test")

        self.tool = self.tmp / "tool"
        self.local = self.tool / "manager" / "local"
        (self.local / "adapters" / config.ADAPTER).mkdir(parents=True)
        self.adapter = self.local / "adapters" / config.ADAPTER / "run"
        self.executable(self.adapter, WORKS)

        self.gh = self.tmp / "gh"
        self.executable(self.gh, FAKE_GH)
        self.gh_log = self.tmp / "gh.log"
        for key, value in (("GH_LOG", str(self.gh_log)),
                           ("GH_UPSTREAM", str(self.upstream)),
                           ("GH_BRANCH", f"task/{STEM}")):
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

        self.worktrees = self.tmp / "worktrees"
        self.patch(REPO=self.app, WORK=self.work, TASKS=self.tasks,
                   TM_ROOT=self.tool, LOCAL=self.local,
                   WORKTREES=self.worktrees, APP_SETTING=str(self.app),
                   WORK_SETTING=str(self.work), EXTERNAL=True,
                   STATE=self.local / "state",
                   AGENT_DIR=self.local / "state" / "agent",
                   DRIVES_DIR=self.local / "state" / "drives",
                   SESSIONS_DIR=self.local / "state" / "sessions",
                   GH_BIN=str(self.gh), COMMIT_MOVES=False, SYNC=False,
                   APP_BASE="main", STATE_BASE="main", APP_MERGE="",
                   GIT_REMOTE="", FETCH_TIMEOUT=10.0)
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

        for registry in (state.AGENTS, state.BOARD_EVENTS, state.EXPECTED_MOVES,
                         github.PR_STATE, phases.SNAPSHOTS, state.COMPLETING):
            registry.clear()
            self.addCleanup(registry.clear)
        state.COMMIT_HOOKS.clear()
        self.addCleanup(state.COMMIT_HOOKS.clear)

        self.sent: list[dict] = []
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = self.sent.append

        self.write(CARD, card("56 — A card"), "in-progress")

    def executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def write(self, filename: str, text: str, stage: str = "backlog") -> None:
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def stage_of(self, filename: str) -> str | None:
        for slug in STAGES:
            if (self.tasks / slug / filename).is_file():
                return slug
        return None

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    def settle(self) -> None:
        self.assertTrue(
            wait_for(lambda: not any(r["status"] == "running"
                                     for r in state.AGENTS.values())),
            "an agent never ended")
        ended = len([r for r in state.AGENTS.values()
                     if r["status"] != "running"])
        self.assertTrue(
            wait_for(lambda: self.sent.count({"type": "agents"}) >= ended),
            "the reaper never announced the ending")

    def branch_exists(self, branch: str) -> bool:
        return git(self.app, "rev-parse", "--verify", "--quiet",
                   branch).returncode == 0

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.is_file():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines()
                if line.strip()]


class UncommittedWorkSurvives(Workspace):
    """Nothing in this module is allowed to delete work a person has not
    committed. An unchanged HEAD says nothing about what is on disk."""

    def test_a_dead_run_that_left_edits_keeps_its_worktree_and_branch(self):
        self.executable(self.adapter, LEAVES_WORK)
        agents.start_agent(CARD, "in-progress")
        self.settle()

        worktree = self.worktrees / STEM
        self.assertTrue(worktree.is_dir(), "the worktree was kept")
        self.assertEqual((worktree / "code.txt").read_text(encoding="utf-8"),
                         "half a thought\n")
        self.assertEqual((worktree / "scratch.md").read_text(encoding="utf-8"),
                         "notes nobody else has\n",
                         "an untracked file is work too")
        self.assertTrue(self.branch_exists(f"task/{STEM}"),
                        "the branch is kept with the worktree")

    def test_it_says_why_the_worktree_was_kept(self):
        self.executable(self.adapter, LEAVES_WORK)
        agents.start_agent(CARD, "in-progress")
        self.settle()

        kept = [s for s in self.summaries() if "kept" in s]
        self.assertTrue(kept, self.summaries())
        self.assertIn("uncommitted change", kept[-1])

    def test_a_dead_run_that_touched_nothing_is_still_cleared(self):
        """The clearing is not removed, only made conditional: a failed
        run with nothing in it still gives the card straight back."""
        self.executable(self.adapter, TOUCHES_NOTHING)
        agents.start_agent(CARD, "in-progress")
        self.settle()

        self.assertFalse((self.worktrees / STEM).exists())
        self.assertFalse(self.branch_exists(f"task/{STEM}"))
        self.assertTrue(any("worktree cleared" in s for s in self.summaries()))

    def test_a_decline_that_left_notes_keeps_them(self):
        self.executable(self.adapter, """#!/usr/bin/env python3
import os
cwd = os.environ["AGENT_CWD"]
open(os.path.join(cwd, "questions.md"), "w").write("which store?\\n")
print("NOT READY: the card does not say which store to write to")
""")
        agents.start_agent(CARD, "in-progress")
        self.settle()

        self.assertTrue((self.worktrees / STEM / "questions.md").is_file())
        self.assertTrue(any("kept" in s and "uncommitted" in s
                            for s in self.summaries()), self.summaries())

    def test_merge_and_clean_up_keeps_a_dirty_worktree_and_says_so(self):
        agents.start_agent(CARD, "in-progress")
        self.settle()
        self.assertEqual(self.stage_of(CARD), "review")
        worktree = self.worktrees / STEM
        (worktree / "unsaved.txt").write_text("mine\n", encoding="utf-8")

        github.complete_task(CARD, "review")

        self.assertTrue(worktree.is_dir(), "the worktree was kept")
        self.assertEqual((worktree / "unsaved.txt").read_text(encoding="utf-8"),
                         "mine\n")
        self.assertTrue(self.branch_exists(f"task/{STEM}"),
                        "the branch is kept with it")
        self.assertTrue(any("worktree for" in s and "kept" in s
                            for s in self.summaries()), self.summaries())
        self.assertEqual(self.stage_of(CARD), "done",
                         "the card still finishes — the merge happened")

    def test_merge_and_clean_up_still_clears_a_clean_worktree(self):
        agents.start_agent(CARD, "in-progress")
        self.settle()

        github.complete_task(CARD, "review")

        self.assertFalse((self.worktrees / STEM).exists())
        self.assertFalse(self.branch_exists(f"task/{STEM}"))
        self.assertTrue(any("cleaned up" in s for s in self.summaries()))


class AWorktreeIsItsRepository(Workspace):
    """A matching branch name says nothing about which repository a
    checkout belongs to, and `<worktrees>/<stem>` is a path two things can
    want."""

    def foreign_checkout(self) -> Path:
        """Another repository entirely, sitting exactly where this card's
        worktree goes — with a branch of the right name, so only the
        repository identity tells them apart."""
        other = self.tmp / "other"
        subprocess.run(["git", "init", "-q", "-b", "main", str(other)],
                       check=True, capture_output=True)
        git(other, "config", "user.name", "someone")
        git(other, "config", "user.email", "someone@example.test")
        (other / "theirs.txt").write_text("not ours\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "theirs")
        self.worktrees.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", str(other),
                        str(self.worktrees / STEM)],
                       check=True, capture_output=True)
        git(self.worktrees / STEM, "checkout", "-q", "-b", f"task/{STEM}")
        return other

    def test_start_work_refuses_a_checkout_of_another_repository(self):
        self.foreign_checkout()

        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")

        self.assertIn("another repository", str(caught.exception))
        self.assertTrue((self.worktrees / STEM / "theirs.txt").is_file(),
                        "and it is left exactly as it was")

    def test_acting_on_a_pr_refuses_it_too(self):
        self.foreign_checkout()
        self.write(CARD, card("56 — A card", status="Review", pr=PR_URL),
                   "review")
        (self.tasks / "in-progress" / CARD).unlink()

        with self.assertRaises(ValueError) as caught:
            agents.start_pr_fix(CARD, "review")

        self.assertIn("another repository", str(caught.exception))

    def test_a_driver_refuses_it(self):
        self.foreign_checkout()
        (self.local / "driver").mkdir(parents=True, exist_ok=True)
        self.executable(self.local / "driver" / "start",
                        "#!/usr/bin/env python3\nprint('DRIVE URL: x')\n")

        with self.assertRaises(ValueError) as caught:
            drive_mod.start(CARD)

        self.assertIn("another repository", str(caught.exception))

    def test_a_local_command_refuses_it(self):
        self.foreign_checkout()
        (self.local / "commands").mkdir(parents=True, exist_ok=True)
        self.executable(self.local / "commands" / "reseed",
                        "#!/usr/bin/env python3\nprint('done')\n")

        with self.assertRaises(ValueError) as caught:
            commands_mod.run("reseed", CARD)

        self.assertIn("another repository", str(caught.exception))

    def test_a_directory_that_is_not_a_checkout_at_all_is_refused(self):
        (self.worktrees / STEM).mkdir(parents=True)
        (self.worktrees / STEM / "stuff.txt").write_text("?\n", encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")

        self.assertIn("not a git checkout", str(caught.exception))

    def test_a_real_worktree_of_the_application_is_accepted(self):
        """The check must not refuse the thing it exists to protect."""
        agents.start_agent(CARD, "in-progress")
        self.settle()
        self.assertIsNone(config.worktree_mismatch(self.worktrees / STEM))

    def test_a_phase_refuses_to_start_over_a_foreign_checkout(self):
        phase, member = "60-ship-it.md", "61-first.md"
        self.write(member, card("61 — First", status="Backlog"))
        self.write(phase, card("60 — Ship it", kind="Phase",
                               cards="- 61 — First\n"), "in-progress")
        self.worktrees.mkdir(parents=True, exist_ok=True)
        (self.worktrees / phase[:-3]).mkdir()
        (self.worktrees / phase[:-3] / "junk").write_text("x", encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            phases.start_phase(phase, "in-progress")

        self.assertIn("not a git checkout", str(caught.exception))
        self.assertFalse(self.branch_exists("phase/60-ship-it"),
                         "nothing was cut over it")
        self.assertTrue((self.worktrees / phase[:-3] / "junk").is_file())


class MergingTheApplicationIsItsOwnDecision(Workspace):
    """`BOARD_SYNC` is about the board's state. It used to decide how the
    application was merged too, which is wrong the moment the two live in
    different repositories."""

    def test_state_sync_alone_does_not_move_the_merge_to_origin(self):
        self.patch(SYNC=True, COMMIT_MOVES=True)
        config._GIT_ROOTS.clear()

        self.assertFalse(config.app_merge_on_origin(),
                         "the state syncing says nothing about the app")

    def test_vendored_sync_still_merges_on_origin(self):
        """The behaviour that existed before the split, unchanged: one
        repository, so local base must only ever fast-forward."""
        self.patch(SYNC=True, COMMIT_MOVES=True, WORK_SETTING="",
                   EXTERNAL=False, WORK=self.app, TASKS=self.app / "tasks")
        config._GIT_ROOTS.clear()

        self.assertTrue(config.app_merge_on_origin())

    def test_the_setting_decides_either_way(self):
        self.patch(APP_MERGE="origin")
        self.assertTrue(config.app_merge_on_origin())
        self.patch(APP_MERGE="local", SYNC=True)
        self.assertFalse(config.app_merge_on_origin())

    def test_the_default_external_merge_is_local_and_lands_on_the_app(self):
        agents.start_agent(CARD, "in-progress")
        self.settle()

        github.complete_task(CARD, "review")

        subjects = git(self.app, "log", "--format=%s", "main").stdout
        self.assertIn("built it", subjects, "the work is in the app's base")
        self.assertEqual([], [call for call in self.gh_calls()
                              if call[:2] == ["pr", "merge"]],
                         "nothing was asked of origin")

    def test_an_origin_merge_catches_the_application_checkout_up(self):
        """With the state in its own repository, no sync beat watches the
        application — so the merge that origin made has to be fetched, or
        the checkout sits behind a base it has never seen."""
        self.patch(APP_MERGE="origin")
        agents.start_agent(CARD, "in-progress")
        self.settle()
        taskfiles.append_to_task(CARD, "review", f"\n**PR:** {PR_URL}\n", "pr")
        # the branch reaches origin the way opening its PR would put it there
        git(self.app, "push", "-q", "origin", f"task/{STEM}")
        before = git(self.app, "rev-parse", "main").stdout.strip()

        github.complete_task(CARD, "review")

        self.assertTrue(any(call[:2] == ["pr", "merge"]
                            for call in self.gh_calls()),
                        "origin was asked to merge")
        after = git(self.app, "rev-parse", "main").stdout.strip()
        self.assertNotEqual(after, before)
        self.assertEqual(
            after, git(self.upstream, "rev-parse", "main").stdout.strip(),
            "the application checkout caught up with origin")
        self.assertTrue(any("fast-forwarded main" in s
                            for s in self.summaries()), self.summaries())

    def test_the_next_launch_branches_from_the_caught_up_base(self):
        self.patch(APP_MERGE="origin")
        agents.start_agent(CARD, "in-progress")
        self.settle()
        taskfiles.append_to_task(CARD, "review", f"\n**PR:** {PR_URL}\n", "pr")
        git(self.app, "push", "-q", "origin", f"task/{STEM}")
        github.complete_task(CARD, "review")

        point, _ = agents._fresh_branch_point()

        self.assertEqual(point, "origin/main")
        self.assertEqual(
            git(self.app, "rev-parse", point).stdout.strip(),
            git(self.upstream, "rev-parse", "main").stdout.strip())

    def test_a_checkout_on_another_branch_is_told_rather_than_moved(self):
        self.patch(APP_MERGE="origin")
        agents.start_agent(CARD, "in-progress")
        self.settle()
        taskfiles.append_to_task(CARD, "review", f"\n**PR:** {PR_URL}\n", "pr")
        git(self.app, "push", "-q", "origin", f"task/{STEM}")
        git(self.app, "checkout", "-q", "-b", "side")

        github.complete_task(CARD, "review")

        self.assertEqual(git(self.app, "branch", "--show-current").stdout.strip(),
                         "side", "nothing switched the checkout under them")
        self.assertTrue(any("stays behind the merge" in s
                            for s in self.summaries()), self.summaries())


class GhIsToldWhichRepository(unittest.TestCase):
    """gh infers the repository from the checkout's remotes; with more
    than one that inference can act on the wrong repository."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-slug-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        for attr in ("REPO", "GIT_REMOTE", "GH_BIN"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
        config.REPO, config.GIT_REMOTE = self.repo, ""

    def slug_for(self, url: str) -> str | None:
        git(self.repo, "remote", "remove", "origin")
        git(self.repo, "remote", "add", "origin", url)
        return config.gh_repo()

    def test_the_url_forms_a_remote_can_take(self):
        for url in ("https://github.com/acme/widget.git",
                    "https://github.com/acme/widget",
                    "git@github.com:acme/widget.git",
                    "ssh://git@github.com/acme/widget.git",
                    "https://github.com/acme/widget/"):
            with self.subTest(url=url):
                self.assertEqual(self.slug_for(url), "acme/widget")

    def test_a_local_path_remote_names_nothing(self):
        """Every test's bare upstream is one of these, and inventing an
        owner/name from a directory path would pass gh a repository that
        does not exist."""
        self.assertIsNone(self.slug_for(str(self.tmp / "origin.git")))
        self.assertIsNone(self.slug_for("file:///srv/git/widget.git"))

    def test_the_configured_remote_is_the_one_asked(self):
        git(self.repo, "remote", "add", "backup",
            "https://github.com/someone/else.git")
        git(self.repo, "remote", "add", "fork",
            "https://github.com/acme/widget.git")
        config.GIT_REMOTE = "fork"

        self.assertEqual(config.gh_repo(), "acme/widget")

    def test_no_remote_at_all_names_nothing(self):
        self.assertIsNone(config.gh_repo())

    def test_the_slug_reaches_the_command_right_after_the_verb(self):
        git(self.repo, "remote", "add", "origin",
            "https://github.com/acme/widget.git")
        seen: list[list[str]] = []
        self.addCleanup(setattr, github, "_run", github._run)
        github._run = lambda cmd, **kw: (
            seen.append(cmd)
            or subprocess.CompletedProcess(cmd, 0, "", ""))
        config.GH_BIN = "gh"

        github._gh_pr("create", "--head", "task/x", "--base", "main")

        self.assertEqual(seen[0][:5],
                         ["gh", "pr", "create", "--repo", "acme/widget"])

    def test_without_a_slug_the_command_is_exactly_what_it_was(self):
        seen: list[list[str]] = []
        self.addCleanup(setattr, github, "_run", github._run)
        github._run = lambda cmd, **kw: (
            seen.append(cmd)
            or subprocess.CompletedProcess(cmd, 0, "", ""))
        config.GH_BIN = "gh"

        github._gh_pr("view", "7", "--json", "state")

        self.assertEqual(seen[0], ["gh", "pr", "view", "7", "--json", "state"])

    def test_the_api_path_carries_the_repository_or_ghs_placeholder(self):
        self.assertEqual(github._api_repo(), "{owner}/{repo}")
        git(self.repo, "remote", "add", "origin",
            "git@github.com:acme/widget.git")
        self.assertEqual(github._api_repo(), "acme/widget")


class BranchNamesComeFromOnePlace(Workspace):
    """A phase card's work is on `phase/<stem>`. A driver or a local
    command that assumed `task/<stem>` attached to nothing."""

    def test_a_phase_cards_branch_is_its_own(self):
        git(self.app, "branch", "phase/60-ship-it")
        self.assertEqual(config.branch_for("60-ship-it"), "phase/60-ship-it")
        self.assertEqual(github.branch_of("60-ship-it.md"), "phase/60-ship-it")

    def test_an_ordinary_card_is_unchanged(self):
        self.assertEqual(config.branch_for(STEM), f"task/{STEM}")

    def test_a_driver_is_handed_the_phase_branch(self):
        phase = "60-ship-it.md"
        self.write(phase, card("60 — Ship it", kind="Phase"), "review")
        git(self.app, "worktree", "add", "-q", "-b", "phase/60-ship-it",
            str(self.worktrees / "60-ship-it"))
        seen = self.tmp / "seen.json"
        (self.local / "driver").mkdir(parents=True, exist_ok=True)
        self.executable(self.local / "driver" / "start", f"""#!/usr/bin/env python3
import json, os
json.dump(dict(os.environ), open({str(seen)!r}, "w"))
print("DRIVE URL: http://127.0.0.1:9999/")
""")
        try:
            drive_mod.start(phase)
            self.assertTrue(wait_for(lambda: seen.is_file()))
        finally:
            try:
                drive_mod.stop()
            except ValueError:
                pass
            drive_mod.DRIVE = None

        env = json.loads(seen.read_text(encoding="utf-8"))
        self.assertEqual(env["DRIVE_BRANCH"], "phase/60-ship-it")

    def test_a_local_command_is_handed_the_phase_branch(self):
        phase = "60-ship-it.md"
        self.write(phase, card("60 — Ship it", kind="Phase"), "review")
        git(self.app, "worktree", "add", "-q", "-b", "phase/60-ship-it",
            str(self.worktrees / "60-ship-it"))
        seen = self.tmp / "cmd.json"
        (self.local / "commands").mkdir(parents=True, exist_ok=True)
        self.executable(self.local / "commands" / "reseed", f"""#!/usr/bin/env python3
import json, os
json.dump(dict(os.environ), open({str(seen)!r}, "w"))
""")
        commands_mod.run("reseed", phase)
        self.assertTrue(wait_for(lambda: seen.is_file()))

        env = json.loads(seen.read_text(encoding="utf-8"))
        self.assertEqual(env["CMD_BRANCH"], "phase/60-ship-it")


class ReferencesStayPortable(Workspace):
    """What reaches a shared ticket or a PR body must be readable by
    somebody on another machine."""

    def test_a_clipped_report_names_no_machine_path(self):
        log = self.local / "state" / "agent" / "logs" / f"{STEM}.log"
        text = reports.report("x" * 30000, log_path=str(log))

        self.assertIn("manager/local/state/agent/logs/", text)
        for absolute in (str(self.tool), str(self.app), str(self.tmp)):
            self.assertNotIn(absolute, text)

    def test_a_log_outside_the_tool_falls_back_to_its_name(self):
        text = reports.report("x" * 30000, log_path="/somewhere/else/run.log")

        self.assertIn("manager/local/state/agent/logs/run.log", text)
        self.assertNotIn("/somewhere/else", text)


if __name__ == "__main__":
    unittest.main()
