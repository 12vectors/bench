"""Where the work state is created, by the real release path (task 64).

The artifact used to unpack tasks/, plans/ and reference/ under the tool
before anything knew the layout, so an external install was left with a
second, apparently active skeleton beside the authoritative work root.
Now the artifact ships no work state, the installer lays it down at the
work root the layout resolves to, the task template comes from a
tool-owned seed under manager/core/seed/, and what an older release left
beside the tool is removed only when it is demonstrably untouched.

Every scenario here runs the real thing: release.sh builds the artifact,
tar unpacks it, install.py and update.sh run as subprocesses — against
disposable application, tool and work roots. The "older release" is the
new artifact with what every earlier release unpacked put back (empty
stage directories with .gitkeep files, plans/, reference/ and the last
shipped template, kept verbatim in tests/fixtures/legacy-release/).

    python3 -m unittest tests.test_work_state_layout -v
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "manager" / "core" / "seed" / "task-template.md"
LEGACY_TEMPLATE = (Path(__file__).resolve().parent / "fixtures"
                   / "legacy-release" / "task-template.md")
STAGES = ("backlog", "to-do", "in-progress", "review", "done")
SKELETON = [f"tasks/{s}" for s in (*STAGES, "archive")] + ["plans",
                                                           "reference"]

GH_STUB = """\
#!/usr/bin/env bash
case "${1:-} ${2:-}" in
  "release view") printf '%s\\n' "${STUB_TAG:?}" ;;
  "release download")
    out=""; prev=""
    for arg in "$@"; do [ "$prev" = "--output" ] && out="$arg"; prev="$arg"; done
    cp "${STUB_TARBALL:?}" "${out:?}" ;;
  *) exit 1 ;;
esac
"""

GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "BENCH_", "STUB_"))}
    env.update(GIT_ID)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True,
                          env=clean_env()).stdout


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("app\n", encoding="utf-8")
    git(path, "add", "README")
    git(path, "commit", "-q", "-m", "init")
    return path


def snapshot(root: Path) -> dict:
    """Every file and directory under `root`, files by content."""
    if not root.exists():
        return {}
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts:   # importing core, not writing
            continue
        if path.is_symlink():
            found[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            found[rel] = "dir"
        else:
            found[rel] = path.read_bytes()
    return found


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Releases(unittest.TestCase):
    """The new artifact, an older one, and the tools to install either."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-layout-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"release.sh failed:\n{result.stdout}"
                               f"{result.stderr}")
        cls.version = (REPO / "manager" / "core" / "VERSION"
                       ).read_text().strip()
        cls.old_tarball = cls.scratch / "bench-old.tar.gz"
        cls.build_old_artifact()
        cls.stubs = cls.scratch / "bin"
        cls.stubs.mkdir()
        (cls.stubs / "gh").write_text(GH_STUB, encoding="utf-8")
        (cls.stubs / "gh").chmod(0o755)
        (cls.stubs / "curl").write_text("#!/usr/bin/env bash\nexit 22\n")
        (cls.stubs / "curl").chmod(0o755)

    @classmethod
    def build_old_artifact(cls):
        """What every release before this one unpacked: the work skeleton
        and the template beside the tool, and no seed."""
        stage = cls.scratch / "old-stage"
        stage.mkdir()
        with tarfile.open(cls.tarball) as tar:
            tar.extractall(stage)
        for rel in SKELETON:
            (stage / rel).mkdir(parents=True)
            (stage / rel / ".gitkeep").touch()
        shutil.copyfile(LEGACY_TEMPLATE, stage / "tasks" / "task-template.md")
        shutil.rmtree(stage / "manager" / "core" / "seed")
        (stage / "manager" / "core" / "VERSION").write_text("0.0.1\n")
        with tarfile.open(cls.old_tarball, "w:gz") as tar:
            tar.add(stage, arcname=".")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))

    # — helpers —

    def unpack(self, tm: Path, tarball: Path | None = None) -> Path:
        tm.mkdir(parents=True)
        with tarfile.open(tarball or self.tarball) as tar:
            tar.extractall(tm)
        return tm

    def install(self, tm: Path, *args: str,
                **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(tm / "install.py"), *args],
            capture_output=True, text=True, cwd=str(tm.parent),
            env=clean_env(**env), stdin=subprocess.DEVNULL)

    def update(self, tm: Path) -> subprocess.CompletedProcess:
        env = clean_env(STUB_TAG=f"v{self.version}",
                        STUB_TARBALL=self.tarball)
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(["bash", str(tm / "update.sh")],
                              capture_output=True, text=True, cwd=str(tm),
                              env=env)

    def ok(self, result: subprocess.CompletedProcess) -> str:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def assert_skeleton(self, root: Path, template: bytes | None = None):
        for rel in SKELETON:
            self.assertTrue((root / rel).is_dir(), f"{root}/{rel} missing")
        tpl = root / "tasks" / "task-template.md"
        self.assertTrue(tpl.is_file(), f"{tpl} missing")
        self.assertEqual(tpl.read_bytes(),
                         template if template is not None
                         else SEED.read_bytes())

    def assert_no_tool_skeleton(self, tm: Path):
        for top in ("tasks", "plans", "reference"):
            self.assertFalse((tm / top).exists(),
                             f"{tm / top} looks like a second active board")

    def external(self, name="ws"):
        ws = self.tmp / name
        app = git_repo(ws / "app")
        tool = ws / "bench"
        work = ws / "work"
        return ws, app, tool, work

    def vendored_host(self):
        host = git_repo(self.tmp / "host")
        (host / ".claude").mkdir()
        return host, host / ".task-manager"


class FreshInstalls(Releases):

    def test_fresh_vendored_install_lays_the_skeleton_at_the_install(self):
        host, tm = self.vendored_host()
        self.unpack(tm)
        out = self.ok(self.install(tm))
        self.assert_skeleton(tm)
        for rel in SKELETON:
            self.assertTrue((tm / rel / ".gitkeep").is_file(),
                            "an empty stage must survive a commit")
        self.assertIn("created the work state", out)
        self.assertNotIn("removed", out)
        self.assertFalse((tm / "manager" / "local" / "state"
                          / "work-root").exists())

    def test_fresh_vendored_reinstall_changes_nothing(self):
        host, tm = self.vendored_host()
        self.unpack(tm)
        self.ok(self.install(tm))
        git(host, "add", "-A")
        git(host, "commit", "-q", "-m", "vendor bench")
        (tm / "tasks" / "backlog" / "01-mine.md").write_text("# 01\n")
        (tm / "tasks" / "task-template.md").write_text("# my template\n")
        (tm / "plans" / "p.md").write_text("plan\n")
        before, head = snapshot(tm), git(host, "rev-parse", "HEAD")
        status = git(host, "status", "--porcelain")

        out = self.ok(self.install(tm))

        self.assertEqual(snapshot(tm), before)
        self.assertEqual(git(host, "rev-parse", "HEAD"), head)
        self.assertEqual(git(host, "status", "--porcelain"), status)
        self.assertNotIn("created the work state", out)

    def test_fresh_external_install_lays_the_skeleton_only_at_the_work_root(self):
        ws, app, tool, work = self.external()
        self.unpack(tool)
        app_before = snapshot(app)

        out = self.ok(self.install(tool, "--app", str(app), "--work",
                                   str(work)))

        self.assert_skeleton(work)
        self.assert_no_tool_skeleton(tool)
        self.assertTrue((tool / "manager" / "core" / "seed"
                         / "task-template.md").is_file(),
                        "the seed stays where the tool keeps it")
        self.assertEqual(snapshot(app), app_before,
                         "nothing is written into the application")
        self.assertEqual(git(app, "status", "--porcelain",
                             "--ignored"), "")
        self.assertIn(str(work), out)

    def test_external_reinstall_from_persisted_settings_is_idempotent(self):
        ws, app, tool, work = self.external()
        self.unpack(tool)
        self.ok(self.install(tool, "--app", str(app), "--work", str(work)))
        git_repo(work)
        (work / "tasks" / "backlog" / "01-card.md").write_text("# 01\n")
        (work / "tasks" / "task-template.md").write_text("# edited\n")
        (work / "plans" / "p.md").write_text("plan\n")
        (work / "reference" / "spec.md").write_text("spec\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "board")
        before = (snapshot(work), snapshot(tool), snapshot(app))
        head = git(work, "rev-parse", "HEAD")

        for _ in range(2):
            out = self.ok(self.install(tool))   # no flags: the .env decides

        self.assertEqual((snapshot(work), snapshot(tool), snapshot(app)),
                         before)
        self.assertEqual(git(work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(work, "status", "--porcelain"), "")
        self.assertNotIn("created", out)
        self.assertNotIn("removed", out)
        self.assert_no_tool_skeleton(tool)

    def test_a_dry_run_writes_nothing_anywhere(self):
        ws, app, tool, work = self.external()
        self.unpack(tool)
        work.mkdir()
        before = (snapshot(ws))
        out = self.ok(self.install(tool, "--app", str(app), "--work",
                                   str(work), "--dry-run"))
        self.assertIn("would create the work state", out)
        self.assertEqual(snapshot(ws), before)


class LegacyToolLocalSkeletons(Releases):
    """An external install made by an older release: its tool directory
    still carries the skeleton that release unpacked."""

    def old_external(self):
        ws, app, tool, work = self.external()
        self.unpack(tool, self.old_tarball)
        # What the older installer recorded and built.
        (tool / "manager" / "local" / ".env").write_text(
            f"BOARD_APP_ROOT={app}\nBOARD_WORK_ROOT={work}\n"
            f"BOARD_WORKTREES={ws / 'worktrees'}\nBOARD_PORT=26999\n")
        for rel in SKELETON:
            (work / rel).mkdir(parents=True)
        shutil.copyfile(LEGACY_TEMPLATE, work / "tasks" / "task-template.md")
        (work / "tasks" / "backlog" / "01-live.md").write_text("# live\n")
        state = tool / "manager" / "local" / "state" / "sessions"
        state.mkdir(parents=True)
        (state / "s.jsonl").write_text('{"kept": 1}\n')
        return ws, app, tool, work

    def test_the_fixture_is_a_template_a_release_shipped(self):
        sys.path.insert(0, str(REPO))
        try:
            import install
        finally:
            sys.path.remove(str(REPO))
        self.assertIn(sha(LEGACY_TEMPLATE), install.SHIPPED_TEMPLATES)

    def test_a_pristine_legacy_skeleton_converges_after_update(self):
        ws, app, tool, work = self.old_external()
        work_before = snapshot(work)
        keep = {p: (tool / p).read_bytes() for p in (
            "manager/local/.env", "manager/local/state/sessions/s.jsonl",
            "manager/local/AGENTS.md")}

        self.ok(self.update(tool))
        out = self.ok(self.install(tool))

        self.assert_no_tool_skeleton(tool)
        self.assertIn("removed the unused, untouched skeleton", out)
        self.assertIn(str(work), out)
        self.assertEqual(snapshot(work), work_before)
        for path, content in keep.items():
            self.assertEqual((tool / path).read_bytes(), content, path)

        # Repeating the update and the re-wire resurrects nothing.
        again = snapshot(tool)
        self.ok(self.update(tool))
        out = self.ok(self.install(tool))
        self.assert_no_tool_skeleton(tool)
        self.assertNotIn("removed", out)
        self.assertEqual(snapshot(work), work_before)
        self.assertEqual(
            {k: v for k, v in snapshot(tool).items()
             if not k.startswith("manager/local/state/hooks")},
            {k: v for k, v in again.items()
             if not k.startswith("manager/local/state/hooks")})

    def test_customised_and_unknown_legacy_content_is_kept_and_named(self):
        ws, app, tool, work = self.old_external()
        (tool / "tasks" / "backlog" / "07-old.md").write_text("# old card\n")
        (tool / "plans" / "plan.md").write_text("an old plan\n")
        legacy = {p: snapshot(tool / p) for p in ("tasks", "plans")}

        self.ok(self.update(tool))
        out = self.ok(self.install(tool))

        for top, before in legacy.items():
            self.assertEqual(snapshot(tool / top), before,
                             f"{top}/ must be kept byte for byte")
        self.assertFalse((tool / "reference").exists(),
                         "the untouched reference/ beside it still goes")
        self.assertIn(f"the work state is at {work}", out)
        self.assertIn("tasks/backlog/07-old.md", out)
        self.assertIn("plans/plan.md", out)
        self.assertIn("not migrated", out)
        self.assertFalse((work / "tasks" / "backlog" / "07-old.md").exists(),
                         "nothing is migrated")

    def test_an_edited_legacy_template_keeps_its_tasks_directory(self):
        ws, app, tool, work = self.old_external()
        (tool / "tasks" / "task-template.md").write_text("# edited\n")
        before = snapshot(tool / "tasks")
        self.ok(self.update(tool))
        out = self.ok(self.install(tool))
        self.assertEqual(snapshot(tool / "tasks"), before)
        self.assertIn("its task template has been edited", out)
        self.assertFalse((tool / "plans").exists())

    def test_a_tracked_legacy_skeleton_is_never_removed(self):
        ws, app, tool, work = self.old_external()
        git_repo(tool)
        git(tool, "add", "tasks", "plans", "reference")
        git(tool, "commit", "-q", "-m", "tracked")
        before = {t: snapshot(tool / t) for t in ("tasks", "plans",
                                                  "reference")}
        self.ok(self.update(tool))
        out = self.ok(self.install(tool))
        for top, snap in before.items():
            self.assertEqual(snapshot(tool / top), snap)
        self.assertIn("git tracks it", out)

    def test_a_linked_legacy_directory_is_kept(self):
        ws, app, tool, work = self.old_external()
        shutil.rmtree(tool / "plans")
        elsewhere = ws / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.md").write_text("mine\n")
        (tool / "plans").symlink_to(elsewhere)
        self.ok(self.update(tool))
        out = self.ok(self.install(tool))
        self.assertTrue((tool / "plans").is_symlink())
        self.assertEqual((elsewhere / "keep.md").read_text(), "mine\n")
        self.assertIn("it is a link", out)

    def test_a_vendored_older_install_updates_and_keeps_its_board(self):
        host, tm = self.vendored_host()
        self.unpack(tm, self.old_tarball)
        (tm / "manager" / "local" / ".env").write_text("BOARD_PORT=26998\n")
        (tm / "tasks" / "backlog" / "02-host.md").write_text("# host\n")
        (tm / "tasks" / "task-template.md").write_text("# host template\n")
        (tm / "reference" / "r.md").write_text("ref\n")
        state = tm / "manager" / "local" / "state"
        state.mkdir()
        (state / "x").write_text("state\n")
        before = {p: snapshot(tm / p) for p in ("tasks", "plans",
                                                "reference")}

        for _ in range(2):
            self.ok(self.update(tm))
            out = self.ok(self.install(tm))

        for top, snap in before.items():
            self.assertEqual(snapshot(tm / top), snap,
                             f"vendored {top}/ is the board itself")
        self.assertEqual((state / "x").read_text(), "state\n")
        self.assertEqual((tm / "manager" / "local" / ".env").read_text(),
                         "BOARD_PORT=26998\n")
        self.assertNotIn("removed", out)


class ModeChanges(Releases):

    def test_vendored_to_external_keeps_the_old_board_and_the_destination(self):
        ws, app, _, work = self.external()
        host, tm = self.vendored_host()
        self.unpack(tm)
        self.ok(self.install(tm))
        (tm / "tasks" / "to-do" / "05-vendored.md").write_text("# 05\n")
        old = {t: snapshot(tm / t) for t in ("tasks", "plans", "reference")}
        # The destination already holds a board of its own.
        (work / "tasks" / "done").mkdir(parents=True)
        (work / "tasks" / "done" / "09-there.md").write_text("# 09\n")
        (work / "tasks" / "task-template.md").write_text("# theirs\n")
        dest_before = snapshot(work)

        out = self.ok(self.install(tm, "--app", str(app), "--work",
                                   str(work)))

        self.assertEqual(snapshot(tm / "tasks"), old["tasks"])
        for rel, content in dest_before.items():
            self.assertEqual(snapshot(work)[rel], content, rel)
        self.assert_skeleton(work, template=b"# theirs\n")
        self.assertIn("tasks/to-do/05-vendored.md", out)
        self.assertIn(f"the work state is at {work}", out)
        self.assertFalse((work / "tasks" / "to-do" / "05-vendored.md").exists())

    def test_external_to_vendored_leaves_the_external_root_alone(self):
        ws, app, _, work = self.external()
        host, tm = self.vendored_host()
        self.unpack(tm)
        self.ok(self.install(tm, "--app", str(app), "--work", str(work)))
        (work / "tasks" / "review" / "03-ext.md").write_text("# 03\n")
        work_before = snapshot(work)
        # Back to vendored: the layout keys are removed from the .env.
        env = tm / "manager" / "local" / ".env"
        env.write_text("".join(
            line + "\n" for line in env.read_text().splitlines()
            if not line.startswith(("BOARD_APP_ROOT=", "BOARD_WORK_ROOT=",
                                    "BOARD_WORKTREES="))))

        out = self.ok(self.install(tm))

        self.assertEqual(snapshot(work), work_before)
        self.assert_skeleton(tm)
        self.assertFalse((tm / "tasks" / "review" / "03-ext.md").exists(),
                         "no automatic import")
        self.assertIn("the work root changed", out)
        self.assertIn(str(work), out)

    def test_switching_external_roots_leaves_a_and_keeps_b(self):
        ws, app, tool, work_a = self.external()
        work_b = ws / "work-b"
        self.unpack(tool)
        self.ok(self.install(tool, "--app", str(app), "--work", str(work_a)))
        (work_a / "tasks" / "backlog" / "01-a.md").write_text("# a\n")
        a_before = snapshot(work_a)
        (work_b / "tasks" / "backlog").mkdir(parents=True)
        (work_b / "tasks" / "backlog" / "02-b.md").write_text("# b\n")
        (work_b / "tasks" / "task-template.md").write_text("# b template\n")

        out = self.ok(self.install(tool, "--work", str(work_b)))

        self.assertEqual(snapshot(work_a), a_before)
        self.assertEqual((work_b / "tasks" / "backlog" / "02-b.md"
                          ).read_text(), "# b\n")
        self.assert_skeleton(work_b, template=b"# b template\n")
        self.assertIn("the work root changed", out)
        self.assertIn(str(work_b.resolve()), out)
        self.assertIn(str(work_a.resolve()), out)
        self.assert_no_tool_skeleton(tool)


class UnsafeDestinations(Releases):

    def test_a_linked_tasks_directory_out_of_the_work_root_is_refused(self):
        ws, app, tool, work = self.external()
        self.unpack(tool)
        work.mkdir()
        (app / "outside").mkdir()
        (work / "tasks").symlink_to(app / "outside")
        app_before = snapshot(app)

        result = self.install(tool, "--app", str(app), "--work", str(work))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(app), app_before)
        self.assertFalse((work / "plans").exists(), "refused before writes")

    def test_a_vendored_tasks_link_out_of_the_install_is_refused(self):
        host, tm = self.vendored_host()
        self.unpack(tm)
        outside = self.tmp / "outside"
        outside.mkdir()
        (tm / "tasks").symlink_to(outside)

        result = self.install(tm)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("outside it", result.stdout)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((tm / "plans").exists(), "nothing was written")


if __name__ == "__main__":
    unittest.main()
