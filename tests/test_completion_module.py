"""WP07 (task 115): merge & clean up lives in `completion.py`, and the pull
request stays with `github.py`.

What moving it must not change, held here beside the suites that already
exercise the completion itself (test_completion_publication,
test_completing_card, test_phase_finishes, test_gate_remediation …),
which now patch `completion`:

- a recovery record and a delivered record written by a board from before
  the move are read, and the unfinished completion is finished, by the new
  module — same paths, same shapes;
- `completion` holds no copy of `changes.PR_STATE`: it asks through the
  owner's accessors, which hand back the live entry;
- the names moved and left nothing behind in `github`, and the seams that
  name the enforcement point (`gates.seams()`, `delivery.seams()`) name
  functions that exist;
- the exact-head pin is unchanged, and so is the host merge's argv.

    python3 -m unittest tests.test_completion_module -v
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import completion  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import githost  # noqa: E402
import changes  # noqa: E402
from tests.test_completion_publication import (  # noqa: E402
    BRANCH, FILENAME, STEM, Delivery, git)

MOVED = ("complete_task", "_complete", "recovery", "recoveries",
         "recovery_public", "forget_recovery", "_remember", "_recovery_dir",
         "_recovery_path", "_assert_same_situation", "_merge_landed",
         "_remote_holds", "_publish", "_held_sentence", "_clean_up",
         "completion_facts", "_merged_elsewhere", "_brought", "_tip",
         "_pinned_head", "_delete_remote_branch", "_clear_member",
         "_moved_on", "_sweep", "_cards", "_assert_lands_in_the_base",
         "_merge_locally", "_merge_on_origin", "_catch_up_base",
         "delivered_record", "_record_delivered")


class TheNamesMoved(unittest.TestCase):

    def test_completion_defines_them_and_github_does_not(self):
        for name in MOVED:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(completion, name, None)))
                self.assertFalse(hasattr(changes, name),
                                 f"github.{name} was left behind")

    def test_the_pr_side_stays_with_github(self):
        for name in ("maybe_open_pr", "open_pr_now", "poller", "reconcile",
                     "pr_snapshot", "public_state", "task_branches",
                     "request_copilot", "branch_of", "remote", "gh_available"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(changes, name, None)))
                self.assertFalse(hasattr(completion, name))


class NoRegistryIsCopied(unittest.TestCase):

    def setUp(self):
        changes.PR_STATE.clear()
        self.addCleanup(changes.PR_STATE.clear)

    def test_pr_state_hands_back_the_live_entry(self):
        entry = {"verdict": "green", "ci": "pass", "url": "u"}
        changes.PR_STATE["x.md"] = entry
        self.assertIs(changes.pr_state("x.md"), entry)
        entry["ci"] = "fail"            # the poll updating in place
        self.assertEqual(changes.pr_state("x.md")["ci"], "fail")
        self.assertIsNone(changes.pr_state("y.md"))

    def test_forget_pr_empties_the_owners_registry(self):
        changes.PR_STATE["x.md"] = {"verdict": "green"}
        changes.forget_pr("x.md")
        changes.forget_pr("never-there.md")      # not an error
        self.assertEqual(changes.PR_STATE, {})

    def test_completion_and_phases_never_name_the_registry(self):
        for module in ("completion", "phases", "delivery"):
            tree = ast.parse((CORE / f"{module}.py").read_text(encoding="utf-8"))
            named = [node.lineno for node in ast.walk(tree)
                     if (isinstance(node, ast.Name) and node.id == "PR_STATE")
                     or (isinstance(node, ast.Attribute)
                         and node.attr in ("PR_STATE", "_OPENING"))]
            with self.subTest(module=module):
                self.assertEqual(named, [])
        self.assertFalse(hasattr(completion, "PR_STATE"))


class SeamsNameWhatExists(unittest.TestCase):

    def test_the_gate_names_completion_as_the_enforcement_point(self):
        said = gates.seams()["enforced_at"]["an acceptance"]
        self.assertTrue(said.startswith("completion._complete "), said)
        self.assertTrue(callable(completion._complete))
        self.assertNotIn("changes._complete", repr(gates.seams()))

    def test_every_dotted_read_of_delivery_resolves(self):
        modules = {"completion": completion, "changes": changes, "gates": gates}
        checked = 0
        for read in delivery.seams()["reads"]:
            name, _, attr = read.partition(".")
            if name in modules and attr and " " not in attr:
                checked += 1
                with self.subTest(read=read):
                    self.assertTrue(hasattr(modules[name], attr))
        self.assertIn("completion.recovery_public", delivery.seams()["reads"])
        self.assertIn("completion.delivered_record", delivery.seams()["reads"])
        self.assertGreaterEqual(checked, 4)


class TheExactHeadPinIsUnchanged(unittest.TestCase):

    def test_an_ungated_board_pins_nothing(self):
        self.assertIsNone(completion._pinned_head({"decision": "ungated"}, BRANCH))

    def test_a_gated_reading_pins_the_commit_it_read_on_this_branch(self):
        decision = {"decision": "allowed",
                    "head": {"ref": BRANCH, "commit": "a" * 40}}
        self.assertEqual(completion._pinned_head(decision, BRANCH), "a" * 40)

    def test_a_reading_of_another_branch_or_no_commit_is_refused(self):
        for head in ({"ref": "task/other", "commit": "a" * 40},
                     {"ref": BRANCH, "commit": None}, {}):
            with self.subTest(head=head):
                with self.assertRaises(ValueError) as caught:
                    completion._pinned_head({"decision": "allowed",
                                             "head": head}, BRANCH)
                self.assertIn("nothing was merged", str(caught.exception))


class TheHostMergeArgvIsUnchanged(unittest.TestCase):
    """`completion` asks `changes.merge_pr`; the argv is the one `_gh_pr`
    built for `_merge_on_origin` before the move — now built by the
    GitHub provider behind `githost` (task 116)."""

    def setUp(self):
        self.calls = []
        for name in ("run", "slug", "available", "can"):
            self.addCleanup(setattr, githost, name, getattr(githost, name))
        self.addCleanup(setattr, config, "GH_BIN", config.GH_BIN)
        self.addCleanup(githost._CONDITIONAL.clear)
        config.GH_BIN = "gh"
        githost.available = lambda: True
        real_can = githost.can
        githost.can = lambda cap, active=False: True if cap == "merge" \
            else real_can(cap, active)

        def run(cmd, timeout=60):
            self.calls.append((list(cmd), timeout))
            return subprocess.CompletedProcess(cmd, 0, "--match-head-commit", "")
        githost.run = run

    def test_with_a_repository_slug_and_a_condition(self):
        githost.slug = lambda: "acme/widget"
        githost._CONDITIONAL.update({"state": "available", "cli": "gh",
                                     "at": time.monotonic()})
        changes.merge_pr("7", "--match-head-commit", "abc", timeout=180)
        self.assertEqual(self.calls, [(["gh", "pr", "merge", "--repo",
                                        "acme/widget", "7", "--merge",
                                        "--match-head-commit", "abc"], 180)])

    def test_without_a_slug_gh_infers_as_before(self):
        githost.slug = lambda: None
        changes.merge_pr("7")
        self.assertEqual(self.calls, [(["gh", "pr", "merge", "7", "--merge"], 180)])

    def test_whether_it_can_be_conditional_is_githubs_probe(self):
        self.assertTrue(changes.merge_is_conditional())
        self.assertEqual(self.calls[0][0], ["gh", "pr", "merge", "--help"])


class RecordsFromBeforeTheMove(Delivery):
    """Records an older board's `github` wrote — same directory, same file
    name, same keys — are read by `completion`, and the unfinished one is
    finished by it."""

    def setUp(self):
        super().setUp()
        self.gates_dir = Path(tempfile.mkdtemp(prefix="bench-gates-")).resolve()
        self.addCleanup(shutil.rmtree, self.gates_dir, True)
        self.patch(GATES_DIR=self.gates_dir)

    def test_an_old_recovery_record_is_finished_without_merging_again(self):
        # what an older board left: it merged locally, then died (or the
        # push failed) before the remote was seen to hold the merge
        git(self.app, "merge", "--no-ff", "--no-edit", "-m",
            f"Merge branch '{BRANCH}'", BRANCH)
        merged_main = git(self.app, "rev-parse", "main").stdout.strip()
        legacy = {"branch": BRANCH, "target": "main", "mode": "local",
                  "remote": "origin", "commit": self.work,
                  "publication": "failed",
                  "detail": "pre-receive hook declined", "diverged": False,
                  "task": FILENAME, "at": 1762000000.0}
        directory = config.COMPLETIONS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{STEM}.json").write_text(json.dumps(legacy),
                                               encoding="utf-8")

        self.assertEqual(completion.recovery(FILENAME)["commit"], self.work)
        self.assertIn(FILENAME, completion.recoveries())
        self.assertEqual(completion.recovery_public(FILENAME)["publication"],
                         "failed")

        result = self.complete()

        self.assertTrue(result["published"], result)
        self.assertTrue(result["moved"])
        self.assertEqual(self.stage_of(), "done")
        self.assertIn("nothing is merged again", self.said())
        self.assertEqual(git(self.app, "rev-parse", "main").stdout.strip(),
                         merged_main, "the merge was made a second time")
        self.assertTrue(self.remote_holds_work())
        self.assertIsNone(completion.recovery(FILENAME))
        delivered = completion.delivered_record(FILENAME)
        self.assertEqual((delivered["commit"], delivered["how"]),
                         (self.work, "local"))
        self.assertTrue((self.gates_dir / f"{STEM}.delivered.json").is_file(),
                        "the delivered record moved somewhere new")

    def test_an_old_delivered_record_is_read(self):
        path = self.gates_dir / f"{STEM}.delivered.json"
        path.write_text(json.dumps({"v": 1, "task": FILENAME,
                                    "commit": "1a2b3c4d5e6f7a8b9c0d",
                                    "target": "main", "how": "origin",
                                    "at": 1762000000.0}), encoding="utf-8")
        found = completion.delivered_record(FILENAME)
        self.assertEqual((found["commit"], found["how"]),
                         ("1a2b3c4d5e6f7a8b9c0d", "origin"))
        path.write_text(json.dumps({"v": 1, "task": FILENAME}), encoding="utf-8")
        self.assertIsNone(completion.delivered_record(FILENAME),
                          "a record naming no commit is not a delivery")

    def test_a_failed_publication_is_never_done(self):
        self.refuse_pushes()
        result = self.complete()
        self.assert_held(result, "failed")
        self.assertIsNone(completion.delivered_record(FILENAME))


if __name__ == "__main__":
    unittest.main()
