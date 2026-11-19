"""A phase refresh names the base it actually merged (task 61).

`phases._freshen` merges the application base into a running phase's
branch on the beat. It always used the configured base for git and then
said "merged main into …" in the ticker whatever that base was. These
cases drive the refresh by hand — `phases.freshen`, one beat's worth, on a
disposable repository (and, where a remote matters, a disposable bare
origin) — and hold the git operation and its event to each other: every
assertion about the sentence sits beside one about which commits the
phase branch now holds, so a string alone cannot make a wrong-base merge
pass.

    python3 -m unittest tests.test_phase_freshen_base -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402
import phases  # noqa: E402
import state  # noqa: E402

from tests.test_phase_runs import PHASE, PHASE_BRANCH, PhaseCase, git  # noqa: E402


class FreshenCase(PhaseCase):
    """The phase card from `PhaseCase`, its branch cut by the runner's own
    `ensure_branch` — no member is launched, nothing is left unattended."""

    BASE = "main"

    def setUp(self):
        super().setUp()
        if self.BASE != "main":
            # The application's base is `trunk`; `main` exists too and
            # carries a commit `trunk` never had, so merging the wrong one
            # is visible in the history, not only in the words.
            git(self.repo, "branch", self.BASE)
            self.commit("main-only.txt", "only on main\n", "a commit only main has")
            git(self.repo, "checkout", "-q", self.BASE)
            if self.REMOTE:
                git(self.repo, "push", "-q", "origin", "main", self.BASE)
        self.patch(APP_BASE=self.BASE)
        self.main_only = (self.tip("main") if self.BASE != "main" else None)

        phases.ensure_branch(self.phase())
        state.BOARD_EVENTS.clear()

    # — helpers —

    def phase(self) -> dict:
        return {"file": PHASE, "stage": "in-progress"}

    def commit(self, name: str, text: str, message: str) -> str:
        (self.repo / name).write_text(text, encoding="utf-8")
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", message)
        return self.tip("HEAD")

    def holds(self, commit: str, branch: str = PHASE_BRANCH) -> bool:
        return git(self.repo, "merge-base", "--is-ancestor", commit,
                   branch).returncode == 0

    def merges(self) -> list[str]:
        return [s for s in self.summaries() if s.startswith("merged ")]

    def assert_main_untouched(self) -> None:
        if self.main_only:
            self.assertFalse(self.holds(self.main_only),
                             "nothing from main was merged into the phase")
            self.assertEqual([s for s in self.summaries() if "merged main" in s],
                             [], "and nothing claims it was")


class ALocalNonDefaultBase(FreshenCase):
    """No remote: the refresh merges the local `trunk`."""

    BASE = "trunk"

    def test_the_event_names_trunk_and_the_merge_took_trunk(self):
        landed = self.commit("later.txt", "landed on trunk\n", "landed on trunk")
        self.assertFalse(self.holds(landed))

        phases.freshen(self.phase())

        self.assertTrue(self.holds(landed), "trunk's new commit is in the phase")
        self.assertEqual(self.merges(), [f"merged trunk into {PHASE_BRANCH}"])
        self.assertIn(f"phase: merge trunk into {PHASE_BRANCH}",
                      self.merges_on(PHASE_BRANCH))
        self.assert_main_untouched()

    def test_an_unchanged_base_announces_nothing(self):
        phases.freshen(self.phase())

        self.assertEqual(self.merges(), [])
        self.assertEqual(self.merges_on(PHASE_BRANCH), [])

    def test_a_conflict_halts_and_announces_no_merge(self):
        worktree = config.WORKTREES / PHASE[:-3]
        (worktree / "code.txt").write_text("the phase's line\n", encoding="utf-8")
        git(worktree, "commit", "-q", "-am", "the phase edits code.txt")
        self.commit("code.txt", "trunk's line\n", "trunk edits code.txt")
        before = self.tip(PHASE_BRANCH)

        with self.assertRaises(phases.Halt) as caught:
            phases.freshen(self.phase())

        self.assertIn(f"merging trunk into {PHASE_BRANCH} conflicts",
                      str(caught.exception))
        self.assertEqual(self.tip(PHASE_BRANCH), before, "nothing landed")
        self.assertEqual(self.merges(), [])


class ARemoteNonDefaultBase(FreshenCase):
    """With an origin: the refresh merges `origin/trunk` — the remote's
    copy of the configured base — and says `trunk`."""

    REMOTE = True
    BASE = "trunk"

    def test_the_event_names_trunk_and_the_merge_took_origin_trunk(self):
        landed = self.commit("later.txt", "landed on trunk\n", "landed on trunk")
        git(self.repo, "push", "-q", "origin", self.BASE)
        # The local `trunk` is moved back, so only `origin/trunk` holds
        # the new commit: the merge must have taken the remote ref.
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.assertFalse(self.holds(landed, "trunk"))

        phases.freshen(self.phase())

        self.assertTrue(self.holds(landed), "origin/trunk's commit is in the phase")
        self.assertEqual(self.merges(), [f"merged trunk into {PHASE_BRANCH}"])
        self.assertIn(f"phase: merge origin/trunk into {PHASE_BRANCH}",
                      self.merges_on(PHASE_BRANCH))
        self.assertTrue(self.holds(landed, f"origin/{PHASE_BRANCH}"),
                        "and the refreshed phase branch was published")
        self.assert_main_untouched()


class TheDefaultMainBase(FreshenCase):
    """Nothing configured: the wording and the merge are what they were."""

    def test_main_is_merged_and_said_exactly_as_before(self):
        landed = self.commit("later.txt", "landed on main\n", "landed on main")

        phases.freshen(self.phase())

        self.assertTrue(self.holds(landed))
        self.assertEqual(self.merges(), [f"merged main into {PHASE_BRANCH}"])
        self.assertIn(f"phase: merge main into {PHASE_BRANCH}",
                      self.merges_on(PHASE_BRANCH))

    def test_an_unchanged_main_announces_nothing(self):
        phases.freshen(self.phase())

        self.assertEqual(self.merges(), [])


class TheDefaultMainBaseOnARemote(FreshenCase):
    REMOTE = True

    def test_origin_main_is_merged_and_said_as_main(self):
        landed = self.commit("later.txt", "landed on main\n", "landed on main")
        git(self.repo, "push", "-q", "origin", "main")

        phases.freshen(self.phase())

        self.assertTrue(self.holds(landed))
        self.assertEqual(self.merges(), [f"merged main into {PHASE_BRANCH}"])


if __name__ == "__main__":
    unittest.main()
