"""The journey's held-index jam keeps its own contract (task 155).

Release 0.6's final candidate failed CI on Python 3.11 in
`test_orchestration_journey.External.test_a_move_staged_before_its_commit_failed_is_recorded_by_the_next_edit`
with the probe's own timeout — *the jam was never placed after the
staged add* — and `jammed`, `committed: false` and `lockHeld` all set.
That was the fixture, not the product, and this module holds the order
of events that produced it:

1. the standalone card's run ends first, and its reaper's move into
   review/ — the colliding commit — gets through `git add`, inside the
   product's own `taskfiles.COMMIT_LOCK`;
2. the fixture then waited there, holding that lock, for the jam;
3. but the jam is placed inside the member's move commit, and before
   that move the pass makes other board commits of its own (the
   member's `orchestration base` and `orchestration action` records),
   each of which needs `COMMIT_LOCK`;
4. so the pass could not reach the jam until the stager's bounded wait
   ran out — a deadlock of the fixture's own making, ended by its
   timeout.

Which of the two threads reached the fixture first was timing alone, so
the journey passed most of the time. Here the adverse order is forced:
the colliding move is started and has reached the fixture *before* the
pass's intervening board commit is made. The waits are shortened, not
lengthened — a fixture that deadlocks fails in seconds, by its own
recorded timeout, never by passing.

Every case runs against a throwaway git repository; the fixture's
patches of `taskfiles` are undone and every thread it starts is joined
before teardown.

    python3 -m unittest tests.test_jam_fixture -v
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402


def _driver():
    """`journey_driver` as a module of this process: it reads its core and
    its step from argv at import, and its import wires sync's commit hook,
    which is taken out again so nothing here can publish."""
    hooks = list(state.COMMIT_HOOKS)
    argv = sys.argv
    sys.argv = [argv[0], str(CORE), json.dumps({"step": "none"})]
    try:
        return importlib.import_module("tests.product.journey_driver")
    finally:
        sys.argv = argv
        state.COMMIT_HOOKS[:] = hooks


driver = _driver()

MEMBER = "21-export-model.md"
SOLO = "30-fix-the-footer.md"
MOVER = "ada"
# the fixture's bounded waits, shortened: a deadlock shows as a recorded
# timeout in seconds rather than a minute
SHORT = 3
# how long this test waits for a thread to reach a point it must reach
REACH = 20


def card(number: str, title: str, status: str) -> str:
    return (f"# {number} — {title}\n\n**Status:** {status}\n"
            f"**Priority:** High\n\nBuild it.\n")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


class _Jam:
    TASKS_UNDER = ""
    EXTERNAL = False

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-jam-fixture-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo = tmp / "repo"
        self.tasks = self.repo / self.TASKS_UNDER / "tasks"
        for slug in config.STAGE_DIRS:
            (self.tasks / slug).mkdir(parents=True)
            (self.tasks / slug / ".gitkeep").write_text("", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        git(self.repo, "config", "user.name", MOVER)
        git(self.repo, "config", "user.email", "ada@example.com")
        self.patch(TASKS=self.tasks, REPO=self.repo, SESSIONS_DIR=tmp / "sessions",
                   STATE=tmp / "state", COMMIT_MOVES=True)
        if self.EXTERNAL:
            app = tmp / "app"
            subprocess.run(["git", "init", "-q", "-b", "main", str(app)],
                           check=True, capture_output=True)
            git(app, "config", "user.name", MOVER)
            git(app, "config", "user.email", "ada@example.com")
            (app / "app.txt").write_text("app\n", encoding="utf-8")
            git(app, "add", "-A")
            git(app, "commit", "-q", "-m", "app root")
            self.app, self.app_head = app, git(app, "rev-parse", "HEAD").stdout.strip()
            self.patch(EXTERNAL=True, REPO=app)
        state.BOARD_EVENTS.clear()
        (self.tasks / "to-do" / MEMBER).write_text(
            card("21", "Export model", "To Do"), encoding="utf-8")
        (self.tasks / "in-progress" / SOLO).write_text(
            card("30", "Fix the footer", "In Progress"), encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "root")
        self.assertEqual(config.state_repo(), self.repo)
        # the fixture patches taskfiles and keeps its record in JAM: all of
        # it is put back, whatever the test does
        for name in ("commit_paths", "_git"):
            self.addCleanup(setattr, taskfiles, name, getattr(taskfiles, name))
        self.addCleanup(driver.JAM.clear)
        for name in ("RACE_SECONDS", "JAM_SECONDS"):
            self.addCleanup(setattr, driver, name, getattr(driver, name))
            setattr(driver, name, SHORT)
        driver.JAM.clear()

    def tearDown(self):
        if self.EXTERNAL:
            self.assertEqual(git(self.app, "rev-parse", "HEAD").stdout.strip(),
                             self.app_head, "no commit reached the application")
            self.assertEqual(git(self.app, "status", "--porcelain").stdout, "")

    def patch(self, **values) -> None:
        for attr, value in values.items():
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)

    def reached(self, condition) -> bool:
        deadline = time.monotonic() + REACH
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_a_colliding_commit_that_starts_first_does_not_hold_the_pass_back(self):
        """The CI order, forced: the reaper's move into review/ reaches the
        fixture before the pass's own intervening commit. That commit must
        go through at once — the colliding commit may not wait for the jam
        while holding the board's commit lock — and then the intended
        collision must still happen: the add staged before the jam, both
        commits meeting the held index and failing."""
        driver.jam({"file": MEMBER, "to": "in-progress",
                    "collide": {"file": SOLO, "message": "→ review", "staged": True}})
        failures: list = []

        def reaper():
            try:
                taskfiles.move_task(SOLO, "in-progress", "review", actor="agent")
            except BaseException as exc:  # noqa: BLE001 — reported below
                failures.append(repr(exc))

        thread = threading.Thread(target=reaper, name="reap:30-fix-the-footer")
        thread.start()
        self.addCleanup(thread.join, REACH)
        # the colliding commit has reached the fixture: waiting for the
        # member's move (repaired), or staged and waiting for the jam
        # inside the commit lock (the defect)
        self.assertTrue(self.reached(lambda: driver.JAM.get("collideArrived")
                                     or driver.JAM.get("stagedBeforeJam")),
                        f"the colliding commit never reached the fixture: {driver.JAM}")

        # the pass's own board commit before the member's move, as the
        # member's orchestration records are in the journey
        path = self.tasks / "to-do" / MEMBER
        path.write_text(path.read_text(encoding="utf-8") + "\nrecorded\n",
                        encoding="utf-8")
        started = time.monotonic()
        between = taskfiles.commit_edit(MEMBER, "to-do", "orchestration action")
        waited = time.monotonic() - started

        taskfiles.move_task(MEMBER, "to-do", "in-progress", actor="agent")
        thread.join(REACH)
        self.assertFalse(thread.is_alive(), "the reaper's move ended")
        self.assertEqual(failures, [])

        jam = {k: v for k, v in driver.JAM.items() if k != "commits"}
        self.assertIsNone(jam["timeout"], f"a probe timeout is not a reproduction: {jam}")
        self.assertLess(waited, SHORT, f"the pass's commit waited {waited:.1f}s "
                        f"behind the colliding commit: {jam}")
        self.assertIs(between, True, "the pass's own commit went through")
        self.assertTrue(jam["stagedBeforeJam"], f"the add staged before the jam: {jam}")
        self.assertTrue(jam["jammed"], jam)
        self.assertIs(jam["collided"], False, f"the colliding commit failed: {jam}")
        self.assertIs(jam["committed"], False, f"the jammed commit failed: {jam}")
        self.assertTrue(jam["lockHeld"], jam)
        self.assertFalse((Path(git(self.repo, "rev-parse", "--absolute-git-dir")
                               .stdout.strip()) / "index.lock").exists(),
                         "the jam let go of the index")

    def test_the_jams_own_commit_arriving_first_still_collides(self):
        """The order the journey usually took: the member's move reaches
        the jam first and waits there, outside the commit lock, for the
        colliding commit to stage; only then does the reaper's move start."""
        driver.jam({"file": MEMBER, "to": "in-progress",
                    "collide": {"file": SOLO, "message": "→ review", "staged": True}})
        failures: list = []

        def reaper():
            try:
                if not self.reached(lambda: driver.JAM.get("armed")):
                    failures.append("the member's move never reached the jam")
                taskfiles.move_task(SOLO, "in-progress", "review", actor="agent")
            except BaseException as exc:  # noqa: BLE001 — reported below
                failures.append(repr(exc))

        reap = threading.Thread(target=reaper, name="reap:30-fix-the-footer")
        reap.start()
        self.addCleanup(reap.join, REACH)
        taskfiles.move_task(MEMBER, "to-do", "in-progress", actor="agent")
        reap.join(REACH)
        self.assertFalse(reap.is_alive(), "the reaper's move ended")
        self.assertEqual(failures, [])
        jam = {k: v for k, v in driver.JAM.items() if k != "commits"}
        self.assertIsNone(jam["timeout"], f"a probe timeout is not a reproduction: {jam}")
        self.assertTrue(jam["stagedBeforeJam"], jam)
        self.assertTrue(jam["jammed"], jam)
        self.assertIs(jam["collided"], False, jam)
        self.assertIs(jam["committed"], False, jam)


class Vendored(_Jam, unittest.TestCase):
    TASKS_UNDER = ".task-manager"


class SourceHosted(_Jam, unittest.TestCase):
    TASKS_UNDER = ""


class External(_Jam, unittest.TestCase):
    TASKS_UNDER = ""
    EXTERNAL = True


if __name__ == "__main__":
    unittest.main()
