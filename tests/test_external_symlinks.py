"""Links out of the work root, where the paths stop telling the truth.

`problems()` fails closed on a layout whose roots overlap — but it
compared the paths *as written*, and a symlink makes that comparison a
lie. `<work>/tasks`, one stage directory inside it, `archive/`, `plans/`
or a single ticket can be a link into the application, and then every
board-made write — a move's rewritten Status line, an agent's closing
report, a claim, the task template the installer copies — lands in the
checkout an external layout exists to keep clean.

So containment is asked of the **resolved** path, at both altitudes:

- the directories, by `config.problems()`, before the installer writes a
  line and whether or not the stage directories are required yet;
- one card, by `taskfiles`, at each of the doors that writes through it.

A root reached *through* a legitimate link is still inside itself, so a
symlinked workspace is not refused for being one.

Nothing here spends a token or touches a remote.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "manager" / "core"))

import config  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402

STAGES = ("backlog", "to-do", "in-progress", "review", "done")
CARD = "56-a-card.md"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True)
    git(path, "config", "user.name", "tester")
    git(path, "config", "user.email", "tester@example.test")
    return path


def work_skeleton(path: Path) -> Path:
    for slug in (*STAGES, "archive"):
        (path / "tasks" / slug).mkdir(parents=True, exist_ok=True)
    (path / "plans").mkdir(exist_ok=True)
    (path / "reference").mkdir(exist_ok=True)
    return path


def card(title: str = "56 — A card", status: str = "In Progress") -> str:
    return (f"# {title}\n\n**Status:** {status}\n**Priority:** High\n\n"
            f"What this card is for, at enough length to read as a brief.\n")


class LinksOutOfTheWorkRoot(unittest.TestCase):
    """The directory half: a work state whose own subdirectories point
    somewhere else is a layout bench refuses to serve."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-links-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = git_init(self.tmp / "app")
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "the application")

    def reload(self, **env) -> None:
        saved = {k: v for k, v in os.environ.items() if k.startswith("BOARD_")}

        def restore():
            for key in [k for k in os.environ if k.startswith("BOARD_")]:
                os.environ.pop(key, None)
            os.environ.update(saved)
            importlib.reload(config)

        self.addCleanup(restore)
        for key in [k for k in os.environ if k.startswith("BOARD_")]:
            os.environ.pop(key, None)
        os.environ.update({k: str(v) for k, v in env.items()})
        importlib.reload(config)

    def external(self, work: Path) -> None:
        self.reload(BOARD_APP_ROOT=self.app, BOARD_WORK_ROOT=work)

    # — the escapes —

    def test_a_tasks_directory_linked_into_the_application_is_refused(self):
        """The whole board, kept in the application's own files, with
        every path-as-written check above it satisfied."""
        work = git_init(self.tmp / "work")
        inside = work_skeleton(self.app / "tickets")
        (work / "tasks").symlink_to(inside / "tasks")
        self.external(work)

        problems = config.problems()

        self.assertTrue(problems, "a linked tasks/ is refused")
        self.assertTrue(any("tasks" in p for p in problems), problems)
        self.assertTrue(any(str(self.app) in p for p in problems),
                        "and it names where the writes would have landed")

    def test_a_single_stage_linked_into_the_application_is_refused(self):
        work = work_skeleton(git_init(self.tmp / "work"))
        target = self.app / "wip"
        target.mkdir()
        (work / "tasks" / "in-progress").rmdir()
        (work / "tasks" / "in-progress").symlink_to(target)
        self.external(work)

        problems = config.problems()

        self.assertTrue(any("in-progress" in p for p in problems), problems)

    def test_the_archive_linked_into_the_application_is_refused(self):
        """Archiving is a move, so archive/ is a write target like any
        stage — and the one a card is renamed *into*."""
        work = work_skeleton(git_init(self.tmp / "work"))
        target = self.app / "attic"
        target.mkdir()
        (work / "tasks" / "archive").rmdir()
        (work / "tasks" / "archive").symlink_to(target)
        self.external(work)

        problems = config.problems()

        self.assertTrue(any("archive" in p for p in problems), problems)

    def test_plans_linked_into_the_application_is_refused(self):
        """plans/ is what the installer wires an agent's plan files to, so
        a link out of it writes into the application by the same hand."""
        work = work_skeleton(git_init(self.tmp / "work"))
        target = self.app / "plans"
        target.mkdir()
        (work / "plans").rmdir()
        (work / "plans").symlink_to(target)
        self.external(work)

        problems = config.problems()

        self.assertTrue(any("plans" in p for p in problems), problems)

    def test_a_task_template_linked_into_the_application_is_refused(self):
        """The template is a write target like the directories around it —
        and the only one whose link can *dangle*, because the installer
        copies onto a destination that is not supposed to be there yet. A
        target that does not exist is not an absent write: `shutil.copy`
        follows the link and creates the file at the far end, inside the
        application. So presence is asked of the link, not of its target.
        """
        work = work_skeleton(git_init(self.tmp / "work"))
        (work / "tasks" / "task-template.md").symlink_to(self.app / "new-file.md")
        self.external(work)

        problems = config.problems(require_tasks=False, require_work=False)

        self.assertTrue(any("task-template.md" in p for p in problems),
                        problems)
        self.assertTrue(any(str(self.app) in p for p in problems),
                        "and it names where the copy would have landed")
        self.assertFalse((self.app / "new-file.md").exists(),
                         "asking the question created nothing")

    def test_a_template_link_inside_the_work_root_is_fine(self):
        """Containment, not a ban on links — and a dangling link that
        lands inside the work state is the board's own business."""
        work = work_skeleton(git_init(self.tmp / "work"))
        (work / "tasks" / "task-template.md").symlink_to(work / "template.md")
        self.external(work)

        self.assertEqual(config.problems(), [])

    def test_a_stage_linked_anywhere_outside_the_work_root_is_refused(self):
        """Not only into the application: the rule is the board's state
        stays in the board's state, so any escape is one."""
        work = work_skeleton(git_init(self.tmp / "work"))
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (work / "tasks" / "review").rmdir()
        (work / "tasks" / "review").symlink_to(elsewhere)
        self.external(work)

        self.assertTrue(any("review" in p for p in config.problems()),
                        config.problems())

    def test_the_escape_is_refused_before_the_installer_writes(self):
        """install.py asks with the stage directories not required yet,
        because it may be about to create them — and that answer has to
        carry the escape too, or the refusal arrives after the template
        has been copied through the link."""
        work = git_init(self.tmp / "work")
        work_skeleton(self.app / "tickets")
        (work / "tasks").symlink_to(self.app / "tickets" / "tasks")
        self.external(work)

        problems = config.problems(require_tasks=False, require_work=False)

        self.assertTrue(problems, "asked the installer's way, it still refuses")
        self.assertTrue(any(str(self.app) in p for p in problems), problems)

    # — and the links that are nobody's mistake —

    def test_a_work_root_reached_through_a_link_is_still_itself(self):
        """The fix must not ban a symlinked workspace: a root is resolved
        before anything is compared against it."""
        real = work_skeleton(git_init(self.tmp / "real-work"))
        link = self.tmp / "work-link"
        link.symlink_to(real)
        self.external(link)

        self.assertEqual(config.problems(), [])

    def test_a_link_inside_the_work_root_is_fine(self):
        """Containment is the question, not whether a link was used: a
        stage pointing at another directory in the same work state writes
        where it always did."""
        work = work_skeleton(git_init(self.tmp / "work"))
        (work / "spill").mkdir()
        (work / "tasks" / "done").rmdir()
        (work / "tasks" / "done").symlink_to(work / "spill")
        self.external(work)

        self.assertEqual(config.problems(), [])

    def test_a_plain_external_layout_is_unchanged(self):
        self.external(work_skeleton(git_init(self.tmp / "work")))
        self.assertEqual(config.problems(), [])

    def test_the_vendored_layout_is_unchanged(self):
        """Legacy installs are not asked a new question they could fail:
        the tool's own tasks/ is a real directory inside the tool."""
        self.reload()
        self.assertFalse(config.EXTERNAL)
        self.assertEqual(config.problems(), [])


class TicketsThatPointElsewhere(unittest.TestCase):
    """The per-card half. A stage directory can be sound at startup and a
    single card inside it still be a link out of tasks/ — written the
    moment anyone drops one in, which is after `problems()` has answered.

    Each door in `taskfiles` writes *through* such a link: `_relocate`
    rewrites the Status line before it renames, `append_to_task` opens the
    target in append mode, a claim rewrites the header. So each one is
    asked, and the file at the other end is left exactly as it was.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-tickets-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = git_init(self.tmp / "app")
        self.readme = self.app / "README.md"
        self.readme.write_text("# the application\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "the application")
        self.work = work_skeleton(git_init(self.tmp / "work"))
        self.tasks = self.work / "tasks"

        for attr, value in (("REPO", self.app), ("WORK", self.work),
                            ("TASKS", self.tasks), ("APP_SETTING", str(self.app)),
                            ("WORK_SETTING", str(self.work)), ("EXTERNAL", True),
                            ("COMMIT_MOVES", False), ("SYNC", False)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        config._GIT_ROOTS.clear()
        self.addCleanup(config._GIT_ROOTS.clear)

        state.BOARD_EVENTS.clear()
        self.addCleanup(state.BOARD_EVENTS.clear)
        state.EXPECTED_MOVES.clear()
        self.addCleanup(state.EXPECTED_MOVES.clear)
        self.addCleanup(setattr, state, "broadcast", state.broadcast)
        state.broadcast = lambda payload: None

    def linked(self, stage: str = "in-progress", name: str = CARD) -> Path:
        """A card in a stage directory that is really the application's
        README — the shape of a tidy-looking mistake and of a deliberate
        one alike."""
        path = self.tasks / stage / name
        path.symlink_to(self.readme)
        return path

    def real(self, stage: str = "in-progress", name: str = CARD) -> Path:
        path = self.tasks / stage / name
        path.write_text(card(), encoding="utf-8")
        return path

    def untouched(self) -> None:
        self.assertEqual(self.readme.read_text(encoding="utf-8"),
                         "# the application\n",
                         "the file at the other end of the link is unchanged")

    def summaries(self) -> list[str]:
        return [e["summary"] for e in state.BOARD_EVENTS]

    # — the doors —

    def test_moving_a_linked_card_is_refused(self):
        self.linked()

        with self.assertRaises(ValueError) as caught:
            taskfiles.move_task(CARD, "in-progress", "review")

        self.assertIn("link", str(caught.exception).lower())
        self.untouched()
        self.assertFalse((self.tasks / "review" / CARD).exists())

    def test_a_move_into_a_linked_stage_is_refused(self):
        """The destination is a write too — and a stage that became a
        link after the board started is exactly what `problems()` cannot
        have seen."""
        self.real()
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (self.tasks / "review").rmdir()
        (self.tasks / "review").symlink_to(outside)

        with self.assertRaises(ValueError) as caught:
            taskfiles.move_task(CARD, "in-progress", "review")

        self.assertIn("link", str(caught.exception).lower())
        self.assertFalse((outside / CARD).exists())
        self.assertTrue((self.tasks / "in-progress" / CARD).is_file(),
                        "the card stays where it was")

    def test_archiving_a_linked_card_is_refused(self):
        self.linked("to-do")

        with self.assertRaises(ValueError) as caught:
            taskfiles.archive_task(CARD, "to-do")

        self.assertIn("link", str(caught.exception).lower())
        self.untouched()

    def test_appending_a_report_through_a_link_is_refused(self):
        self.linked("review")

        wrote = taskfiles.append_to_task(CARD, "review",
                                         "\n## Work report\n\nbuilt it\n", "report")

        self.assertFalse(wrote, "the append reports that it did not happen")
        self.untouched()
        self.assertTrue(any("link" in s.lower() for s in self.summaries()),
                        self.summaries())

    def test_appending_to_a_section_through_a_link_is_refused(self):
        self.linked("in-progress")

        wrote = taskfiles.append_to_section(CARD, "in-progress", "Phase log",
                                            "- started", "log")

        self.assertFalse(wrote)
        self.untouched()

    def test_claiming_a_linked_card_is_refused(self):
        self.linked()

        with self.assertRaises(ValueError) as caught:
            taskfiles.set_assignee(CARD, "in-progress", "someone")

        self.assertIn("link", str(caught.exception).lower())
        self.untouched()

    # — and the card that is just a card —

    def test_a_real_card_moves_appends_and_claims_exactly_as_before(self):
        self.real()

        taskfiles.set_assignee(CARD, "in-progress", "tester")
        self.assertTrue(taskfiles.append_to_task(CARD, "in-progress",
                                                 "\n## Work report\n\nbuilt\n",
                                                 "report"))
        moved = taskfiles.move_task(CARD, "in-progress", "review")

        self.assertEqual(moved["stage"], "review")
        text = (self.tasks / "review" / CARD).read_text(encoding="utf-8")
        self.assertIn("**Status:** Review", text)
        self.assertIn("**Assignee:** tester", text)
        self.assertIn("built", text)

    def test_a_card_linked_inside_tasks_is_left_alone(self):
        """The rule is containment, not a ban on links: a card pointing
        at another file under tasks/ writes where it always did."""
        kept = self.tasks / "backlog" / "56-kept.md"
        kept.write_text(card(status="Backlog"), encoding="utf-8")
        link = self.tasks / "to-do" / CARD
        link.symlink_to(kept)

        self.assertTrue(taskfiles.append_to_task(CARD, "to-do", "\nnote\n", "note"))
        self.assertIn("note", kept.read_text(encoding="utf-8"))


class TheInstallerRefusesBeforeWriting(unittest.TestCase):
    """install.py asks `problems()` before it writes a line, and the
    escape has to be in that answer: the skeleton it builds and the task
    template it copies would otherwise go through the link, into the
    application, before anything had the chance to refuse."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-linkinst-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.tool = self.tmp / "tool"
        shutil.copytree(BENCH / "manager" / "core",
                        self.tool / "manager" / "core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.tool / "manager" / "local" / "adapters").mkdir(parents=True)
        shutil.copy(BENCH / "install.py", self.tool / "install.py")

        self.app = git_init(self.tmp / "app")
        (self.app / ".claude").mkdir()
        (self.app / "code.txt").write_text("shipped\n", encoding="utf-8")
        git(self.app, "add", "code.txt")
        git(self.app, "commit", "-q", "-m", "the application")

        self.work = git_init(self.tmp / "work")
        self.env_file = self.tool / "manager" / "local" / ".env"

    def install(self) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        return subprocess.run(
            [sys.executable, str(self.tool / "install.py"),
             "--app", str(self.app), "--work", str(self.work)],
            capture_output=True, text=True, cwd=str(self.tmp), env=env,
            stdin=subprocess.DEVNULL)

    def test_it_declines_and_writes_nothing_through_the_link(self):
        tickets = self.app / "tickets"
        (tickets / "tasks").mkdir(parents=True)
        (self.work / "tasks").symlink_to(tickets / "tasks")

        result = self.install()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be served", result.stdout)
        self.assertFalse(self.env_file.exists(), "nothing was recorded")
        self.assertEqual([], list((tickets / "tasks").iterdir()),
                         "and nothing was created inside the application")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(),
                         "", "the application is untouched — git sees nothing, "
                         "not even the empty directory the test made itself")

    def test_a_dangling_template_link_is_refused_before_any_write(self):
        """The one escape the directory checks missed. Everything the
        skeleton builds already exists, so the only write left is the
        template — and its destination is a link to a file in the
        application that is not there yet. `exists()` said no write was
        pending; the copy followed the link and wrote into the checkout.

        The refusal has to land before the layout is recorded and before
        the adapter wires anything, so the run is judged by what it left
        behind as much as by its exit code."""
        work_skeleton(self.work)
        target = self.app / "new-file.md"
        (self.work / "tasks" / "task-template.md").symlink_to(target)

        result = self.install()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be served", result.stdout)
        self.assertIn("task-template.md", result.stdout)
        self.assertFalse(target.exists(),
                         "the template was never copied through the link")
        self.assertFalse(self.env_file.exists(), "nothing was recorded")
        self.assertFalse((self.app / ".claude" / "settings.json").exists(),
                         "and the application's settings were never touched")
        self.assertEqual(git(self.app, "status", "--porcelain").stdout.strip(),
                         "", "the application is untouched")

    def test_a_sound_layout_still_installs(self):
        result = self.install()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.work / "tasks" / "backlog").is_dir())
        self.assertTrue((self.work / "tasks" / "task-template.md").is_file())


if __name__ == "__main__":
    unittest.main()
