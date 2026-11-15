"""Conflicted PRs become card state: the poller folds GitHub's
mergeable field into the PR snapshot, a conflict drops any
approved-green verdict as changes-needed-by-you (not a CI failure),
and GitHub's lazily-computed UNKNOWN keeps the previous reading so the
chip never flaps. The snapshot's `conflicts` key must reach the UI."""

import sys
import unittest
from pathlib import Path
from tests import pagesource

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import githost  # noqa: E402
import changes  # noqa: E402


def payload(**overrides):
    """A gh pr-view JSON payload, normalized at the edge exactly as the
    poll does it (the GitHub provider), so _fold reads core's words."""
    data = {"reviews": [], "reviewRequests": [], "statusCheckRollup": []}
    data.update(overrides)
    return githost.load("github").normalize_status(data)


APPROVED = [{"state": "APPROVED"}]


class ConflictFolding(unittest.TestCase):
    def test_conflict_drops_green_even_when_approved(self):
        entry = changes._fold(payload(reviews=APPROVED,
                                     mergeable="CONFLICTING"), {})
        self.assertTrue(entry["conflicts"])
        self.assertEqual(entry["verdict"], "red")
        self.assertIn("conflicts with main", entry["detail"])

    def test_conflict_is_not_a_ci_failure(self):
        entry = changes._fold(payload(mergeable="CONFLICTING"), {})
        self.assertIsNone(entry["ci"])
        self.assertEqual(entry["verdict"], "red")

    def test_mergeable_approved_pr_stays_green(self):
        entry = changes._fold(payload(reviews=APPROVED,
                                     mergeable="MERGEABLE"), {})
        self.assertFalse(entry["conflicts"])
        self.assertEqual(entry["verdict"], "green")

    def test_unknown_keeps_the_previous_reading_both_ways(self):
        # GitHub computes mergeability lazily after a push: UNKNOWN means
        # "not yet", never "fine" — the chip must not flap.
        still = changes._fold(payload(mergeable="UNKNOWN"),
                             {"conflicts": True})
        self.assertTrue(still["conflicts"])
        self.assertEqual(still["verdict"], "red")
        clean = changes._fold(payload(reviews=APPROVED, mergeable="UNKNOWN"),
                             {"conflicts": False})
        self.assertFalse(clean["conflicts"])
        self.assertEqual(clean["verdict"], "green")

    def test_unknown_on_first_sight_alarms_nobody(self):
        entry = changes._fold(payload(mergeable="UNKNOWN"), {})
        self.assertIsNone(entry["conflicts"])
        self.assertEqual(entry["verdict"], "pending")
        self.assertNotIn("conflicts", entry["detail"])

    def test_resolution_lets_green_return(self):
        entry = changes._fold(payload(reviews=APPROVED, mergeable="MERGEABLE"),
                             {"conflicts": True, "verdict": "red"})
        self.assertFalse(entry["conflicts"])
        self.assertEqual(entry["verdict"], "green")


class SnapshotReachesTheUI(unittest.TestCase):
    def test_public_state_carries_conflicts(self):
        changes.PR_STATE["x.md"] = {"verdict": "red", "ci": None,
                                   "copilot": None, "conflicts": True,
                                   "detail": "conflicts with main",
                                   "url": "u", "ts": 1}
        try:
            self.assertTrue(changes.public_state()["x.md"]["conflicts"])
        finally:
            changes.PR_STATE.pop("x.md", None)

    def test_the_card_wears_an_alarm_coloured_chip(self):
        html = pagesource.page()
        self.assertIn("prState.conflicts", html)
        self.assertIn("label: 'conflicts', glyph: '✕', cls: 'bad'", html)

    def test_poller_asks_github_for_mergeable(self):
        source = (REPO / "manager" / "core" / "providers" / "github"
                  / "provider.py").read_text(encoding="utf-8")
        self.assertIn("statusCheckRollup,state,mergeable", source)


if __name__ == "__main__":
    unittest.main()
