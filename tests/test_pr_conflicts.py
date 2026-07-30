"""Conflicted PRs become card state: the poller folds GitHub's
mergeable field into the PR snapshot, a conflict drops any
approved-green verdict as changes-needed-by-you (not a CI failure),
and GitHub's lazily-computed UNKNOWN keeps the previous reading so the
chip never flaps. The snapshot's `conflicts` key must reach the UI."""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import github  # noqa: E402


def payload(**overrides):
    """A gh pr-view JSON payload with the fields _fold reads."""
    data = {"reviews": [], "reviewRequests": [], "statusCheckRollup": []}
    data.update(overrides)
    return data


APPROVED = [{"state": "APPROVED"}]


class ConflictFolding(unittest.TestCase):
    def test_conflict_drops_green_even_when_approved(self):
        entry = github._fold(payload(reviews=APPROVED,
                                     mergeable="CONFLICTING"), {})
        self.assertTrue(entry["conflicts"])
        self.assertEqual(entry["verdict"], "red")
        self.assertIn("conflicts with main", entry["detail"])

    def test_conflict_is_not_a_ci_failure(self):
        entry = github._fold(payload(mergeable="CONFLICTING"), {})
        self.assertIsNone(entry["ci"])
        self.assertEqual(entry["verdict"], "red")

    def test_mergeable_approved_pr_stays_green(self):
        entry = github._fold(payload(reviews=APPROVED,
                                     mergeable="MERGEABLE"), {})
        self.assertFalse(entry["conflicts"])
        self.assertEqual(entry["verdict"], "green")

    def test_unknown_keeps_the_previous_reading_both_ways(self):
        # GitHub computes mergeability lazily after a push: UNKNOWN means
        # "not yet", never "fine" — the chip must not flap.
        still = github._fold(payload(mergeable="UNKNOWN"),
                             {"conflicts": True})
        self.assertTrue(still["conflicts"])
        self.assertEqual(still["verdict"], "red")
        clean = github._fold(payload(reviews=APPROVED, mergeable="UNKNOWN"),
                             {"conflicts": False})
        self.assertFalse(clean["conflicts"])
        self.assertEqual(clean["verdict"], "green")

    def test_unknown_on_first_sight_alarms_nobody(self):
        entry = github._fold(payload(mergeable="UNKNOWN"), {})
        self.assertIsNone(entry["conflicts"])
        self.assertEqual(entry["verdict"], "pending")
        self.assertNotIn("conflicts", entry["detail"])

    def test_resolution_lets_green_return(self):
        entry = github._fold(payload(reviews=APPROVED, mergeable="MERGEABLE"),
                             {"conflicts": True, "verdict": "red"})
        self.assertFalse(entry["conflicts"])
        self.assertEqual(entry["verdict"], "green")


class SnapshotReachesTheUI(unittest.TestCase):
    def test_public_state_carries_conflicts(self):
        github.PR_STATE["x.md"] = {"verdict": "red", "ci": None,
                                   "copilot": None, "conflicts": True,
                                   "detail": "conflicts with main",
                                   "url": "u", "ts": 1}
        try:
            self.assertTrue(github.public_state()["x.md"]["conflicts"])
        finally:
            github.PR_STATE.pop("x.md", None)

    def test_the_card_wears_an_alarm_coloured_chip(self):
        html = (REPO / "manager" / "core" / "board.html").read_text(
            encoding="utf-8")
        self.assertIn("prState.conflicts", html)
        self.assertIn("label: 'conflicts', glyph: '✕', cls: 'bad'", html)

    def test_poller_asks_github_for_mergeable(self):
        source = (REPO / "manager" / "core" / "github.py").read_text(
            encoding="utf-8")
        self.assertIn("statusCheckRollup,state,mergeable", source)


if __name__ == "__main__":
    unittest.main()
