"""manager/local/ belongs to the destination project (task 78).

Up to task 78 bench's repository tracked manager/local/AGENTS.md, CLAUDE.md
and checks — bench's own definition of done (`python3 -m unittest`) and its
unittest matcher — so a source checkout staged for another project handed
that project's agents bench's testing policy. These cases hold the
separation end to end, against the real artifact release.sh builds and a
real git clone of a source repository made from this checkout:

- neither a release nor a source checkout carries a manager/local/ file
  other than the .gitkeep placeholders; install.py seeds the two neutral
  starters from manager/core/seed/local/, one file at a time, only where
  each is missing, and neither names a test framework;
- what a project already has there survives install, reinstall and update
  byte for byte, vendored and external;
- the four launch prompts, rendered without launching anything, point at
  the destination's brief and never at bench's unittest policy;
- protect-local.py keeps a source checkout's own notes through the git
  update that stops tracking bench's, and changes nothing when it cannot
  tell whose a file is.

Every root is disposable, and every git repository is local.

    python3 -m unittest tests.test_local_ownership
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "local-ownership"
SEED = REPO / "manager" / "core" / "seed" / "local"
LEGACY = ("AGENTS.md", "CLAUDE.md", "checks")
# A destination project's testing command, distinct from bench's.
PROJECT_COMMAND = "npm run verify:everything"
BENCH_DOD = "python3 -m unittest"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOARD_", "BENCH_", "GIT_"))}
    env.update({"GIT_AUTHOR_NAME": "dev", "GIT_AUTHOR_EMAIL": "dev@example.test",
                "GIT_COMMITTER_NAME": "dev",
                "GIT_COMMITTER_EMAIL": "dev@example.test"})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                            text=True, env=clean_env())
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}:\n"
                             f"{result.stdout}{result.stderr}")
    return result


def snapshot(root: Path) -> dict:
    """Every file under `root`, by relative path, as bytes (links by
    target) — the exact-byte comparison the preservation cases make."""
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[rel] = ("link", os.readlink(path))
        elif path.is_file():
            found[rel] = path.read_bytes()
    return found


# The parts of this checkout a source installation needs. tests/, site/
# and verify/ are left out only to keep each clone small: nothing in them
# is read by an install, and none of them is under manager/local/.
SKIP_TOPS = ("tests/", "site/", "verify/")


def make_source_repo(dest: Path) -> Path:
    """A git repository holding this checkout's tracked files as they are
    on disk now — what `git clone <bench>` would hand a person, built from
    the working tree so an uncommitted change is tested too."""
    listed = git(REPO, "ls-files", "-z").stdout.split("\0")
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", "main")
    for rel in listed:
        if not rel or rel.startswith(SKIP_TOPS):
            continue
        source = REPO / rel
        if not source.is_file() or source.is_symlink():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # The exec bit as git records it, whatever this disk's copy says.
    (dest / "protect-local.py").chmod(0o755)
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "bench, as a source checkout")
    return dest


def make_project(dest: Path, brief: str | None = None) -> Path:
    """A disposable application that is not bench: its own git history,
    its own AGENTS.md naming its own testing command, a .claude/ so the
    adapter's wire has somewhere to write."""
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", "main")
    (dest / "package.json").write_text('{"name": "app"}\n', encoding="utf-8")
    (dest / "AGENTS.md").write_text(
        brief or f"# App rules\n\nThe definition of done is "
                 f"`{PROJECT_COMMAND}`.\n", encoding="utf-8")
    (dest / ".claude").mkdir()
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "the application")
    return dest


def install(tm: Path, *args: str, cwd: Path | None = None,
            **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(tm / "install.py"), *args],
                          capture_output=True, text=True,
                          cwd=str(cwd or tm.parent), env=clean_env(**env),
                          stdin=subprocess.DEVNULL)


RENDER = """
import json, config, profiles, templates
fields = templates.sample_fields()
fields.update(config.prompt_fields("01-card.md", "in-progress"))
prompts = {n: templates.render(n, templates.require(n)["text"], fields)
           for n in templates.NAMES}
print(json.dumps({
    "prompts": prompts,
    "repo": str(config.REPO),
    "outside": [str(p) for p in config.guidance_outside_app()],
    "checks": [c["label"] for c in config.checks()],
    "policy": profiles.document()["kind"],
    "gated": profiles.gate_policy()["configured"],
}))
"""


def render(tm: Path) -> dict:
    """The four launch prompts as an install would fill them — the real
    templates, the real guidance, rendered in a child with the install's
    own core on its path. Nothing is launched."""
    result = subprocess.run([sys.executable, "-c", RENDER], capture_output=True,
                            text=True, cwd=str(tm / "manager" / "core"),
                            env=clean_env())
    if result.returncode != 0:
        raise AssertionError(f"rendering failed:\n{result.stderr}")
    return json.loads(result.stdout)


class Briefs:
    """What a rendered run is told to read, and what that says."""

    def read_set(self, rendered: dict) -> str:
        """Every prompt plus every brief they name: the application's
        AGENTS.md at the repo root, and anything named outside it."""
        texts = list(rendered["prompts"].values())
        root_brief = Path(rendered["repo"]) / "AGENTS.md"
        if root_brief.is_file():
            texts.append(root_brief.read_text(encoding="utf-8"))
        for path in rendered["outside"]:
            texts.append(Path(path).read_text(encoding="utf-8"))
        return "\n".join(texts)

    def assert_destination_policy(self, rendered: dict) -> None:
        for name, text in rendered["prompts"].items():
            self.assertIn("AGENTS.md", text, f"{name} names no brief")
        everything = self.read_set(rendered)
        self.assertIn(PROJECT_COMMAND, everything)
        self.assertNotIn(BENCH_DOD, everything,
                         "bench's own definition of done reached a run")
        self.assertNotIn("unittest", rendered["checks"])
        self.assertEqual("legacy", rendered["policy"],
                         "a profile document arrived with the install")
        self.assertFalse(rendered["gated"])


# ── the sources: what ships and what is seeded ───────────────────────


class Sources(unittest.TestCase):
    def test_bench_tracks_no_project_file_in_manager_local(self):
        tracked = git(REPO, "ls-files", "manager/local").stdout.split()
        self.assertTrue(tracked, "the .gitkeep placeholders are tracked")
        for name in tracked:
            self.assertEqual(".gitkeep", Path(name).name,
                             f"{name} is the destination's, not bench's")

    def test_the_neutral_starters_name_no_test_framework(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (SEED / name).read_text(encoding="utf-8").lower()
            for framework in ("unittest", "pytest", "npm test", "cargo test",
                              "go test", "jest", "vitest"):
                self.assertNotIn(framework, text, f"{name} names {framework}")
        self.assertIn("@AGENTS.md", (SEED / "CLAUDE.md").read_text("utf-8"))

    def test_benchs_own_policy_is_kept_in_a_developer_location(self):
        brief = REPO / "develop" / "operator-local" / "AGENTS.md"
        checks = REPO / "develop" / "operator-local" / "checks"
        self.assertIn(BENCH_DOD, brief.read_text(encoding="utf-8"))
        self.assertIn("unittest", checks.read_text(encoding="utf-8"))
        guide = (REPO / "develop" / "README.md").read_text(encoding="utf-8")
        self.assertIn("manager/local/AGENTS.md", guide)
        self.assertIn("protect-local.py", guide)

    def test_the_fingerprints_are_the_files_bench_once_tracked(self):
        installer = load(REPO / "install.py", "bench_install_78")
        protector = load(REPO / "protect-local.py", "bench_protect_78")
        self.assertEqual(installer.BENCH_POLICY, protector.BENCH_POLICY)
        self.assertEqual(installer.BENCH_NEUTRAL, protector.BENCH_NEUTRAL)
        self.assertIn(sha(FIXTURES / "AGENTS.md"),
                      installer.BENCH_POLICY["AGENTS.md"])
        self.assertIn(sha(FIXTURES / "checks"), installer.BENCH_POLICY["checks"])
        self.assertIn(sha(FIXTURES / "CLAUDE.md"),
                      installer.BENCH_NEUTRAL["CLAUDE.md"])
        self.assertIn(sha(FIXTURES / "CLAUDE.md.v1"),
                      installer.BENCH_NEUTRAL["CLAUDE.md"])
        # The current starter is not policy, and the pointer is unchanged.
        self.assertNotIn(sha(SEED / "AGENTS.md"),
                         installer.BENCH_POLICY["AGENTS.md"])
        self.assertIn(sha(SEED / "CLAUDE.md"),
                      installer.BENCH_NEUTRAL["CLAUDE.md"])


class Artifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-78-art-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
        with tarfile.open(cls.tarball) as tar:
            cls.files = {m.name.removeprefix("./") for m in tar.getmembers()
                         if m.isfile()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def test_it_carries_no_project_file_in_manager_local(self):
        local = sorted(n for n in self.files if n.startswith("manager/local/"))
        self.assertEqual(local, [f"manager/local/{d}/.gitkeep" for d in
                                 ("adapters", "commands", "driver", "prompts")])

    def test_it_carries_the_starters_as_tool_owned_seed(self):
        self.assertIn("manager/core/seed/local/AGENTS.md", self.files)
        self.assertIn("manager/core/seed/local/CLAUDE.md", self.files)

    def test_benchs_developer_material_never_ships(self):
        for name in self.files:
            self.assertFalse(name.startswith("develop/"), name)
        self.assertNotIn("protect-local.py", self.files)


# ── fresh installations into a project that is not bench ─────────────


class FreshInstalls(unittest.TestCase, Briefs):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-78-fresh-")).resolve()
        cls.source = make_source_repo(cls.scratch / "bench-src")
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))

    def vendored_from_source(self) -> Path:
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        git(self.tmp, "clone", "-q", str(self.source), str(tm))
        shutil.rmtree(tm / ".git")
        return tm

    def vendored_from_release(self) -> Path:
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        tm.mkdir()
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)
        return tm

    def external_from_source(self) -> tuple[Path, Path]:
        project = make_project(self.tmp / "app")
        tm = self.tmp / "bench"
        git(self.tmp, "clone", "-q", str(self.source), str(tm))
        return tm, project

    def assert_neutral_local(self, tm: Path) -> None:
        local = tm / "manager" / "local"
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertEqual((local / name).read_bytes(),
                             (SEED / name).read_bytes(), name)
        self.assertFalse((local / "checks").exists(),
                         "bench's checks were registered in a project")
        self.assertFalse((local / "profiles.json").exists())
        for path in local.rglob("*"):
            if path.is_file() and "state" not in path.parts:
                self.assertNotIn(BENCH_DOD, path.read_text(encoding="utf-8"),
                                 path)

    def test_a_vendored_source_installation(self):
        tm = self.vendored_from_source()
        result = install(tm)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("manager/local/AGENTS.md", result.stdout)
        self.assert_neutral_local(tm)
        self.assert_destination_policy(render(tm))

    def test_a_vendored_release_installation(self):
        tm = self.vendored_from_release()
        result = install(tm)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_neutral_local(tm)
        self.assert_destination_policy(render(tm))

    def test_source_and_release_lay_down_the_same_local_files(self):
        source, release = self.vendored_from_source(), None
        install(source)
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))
        release = self.vendored_from_release()
        install(release)

        def local_files(tm: Path) -> dict:
            local = tm / "manager" / "local"
            return {k: v for k, v in snapshot(local).items()
                    if not k.startswith("state/")}

        self.assertEqual(local_files(source), local_files(release))

    def test_an_external_source_installation(self):
        tm, project = self.external_from_source()
        work = self.tmp / "work"
        result = install(tm, "--app", str(project), "--work", str(work),
                         cwd=self.tmp)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_neutral_local(tm)
        rendered = render(tm)
        # Externally the tool's notes live outside the checkout and are
        # named by absolute path: they are the neutral starter.
        self.assertIn(str(tm / "manager" / "local" / "AGENTS.md"),
                      rendered["outside"])
        self.assert_destination_policy(rendered)
        self.assertEqual([], git(project, "status", "--porcelain").stdout
                         .splitlines(), "the application gained files")

    def test_an_external_projects_own_notes_are_what_runs_read(self):
        tm, project = self.external_from_source()
        local = tm / "manager" / "local"
        local.joinpath("AGENTS.md").write_text(
            f"# Ours\n\nAlso run `{PROJECT_COMMAND}` before review.\n",
            encoding="utf-8")
        mine = local.joinpath("AGENTS.md").read_bytes()
        install(tm, "--app", str(project), "--work", str(self.tmp / "work"),
                cwd=self.tmp)
        self.assertEqual(mine, local.joinpath("AGENTS.md").read_bytes())
        rendered = render(tm)
        self.assert_destination_policy(rendered)
        work_prompt = rendered["prompts"]["work.md"]
        self.assertIn(str(local / "AGENTS.md"), work_prompt)


# ── preservation: install, reinstall, update ─────────────────────────


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


class Preservation(unittest.TestCase):
    """A project that already has its notes, checks, settings, prompt
    override, profile and state keeps every byte through install,
    reinstall and update, in both layouts."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-78-keep-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
        cls.version = (REPO / "manager/core/VERSION").read_text().strip()
        cls.stubs = cls.scratch / "bin"
        cls.stubs.mkdir()
        (cls.stubs / "gh").write_text(GH_STUB, encoding="utf-8")
        (cls.stubs / "curl").write_text("#!/usr/bin/env bash\nexit 22\n",
                                        encoding="utf-8")
        for stub in cls.stubs.iterdir():
            stub.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))

    def unpack(self, tm: Path) -> None:
        tm.mkdir(parents=True)
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)

    def populate(self, local: Path, names=("AGENTS.md", "CLAUDE.md", "checks",
                                           ".env", "profiles.json",
                                           "prompts/work.md",
                                           "state/sessions/s1.jsonl")) -> None:
        bodies = {
            "AGENTS.md": b"# Ours\r\n\r\nRun `npm run verify:everything`.\n\xe2\x80\x94",
            "CLAUDE.md": b"Our own pointer, not bench's\n",
            "checks": b"verify: \\bverify:everything\\b\n",
            ".env": b"BOARD_PORT=26999\n",
            "profiles.json": b'{"v": 1}\n',
            "prompts/work.md": b"our override {body}\n",
            "state/sessions/s1.jsonl": b'{"event": "kept"}\n',
        }
        for name in names:
            path = local / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bodies[name])

    def update(self, tm: Path) -> subprocess.CompletedProcess:
        env = clean_env(STUB_TAG=f"v{self.version}",
                        STUB_TARBALL=str(self.tarball))
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(["bash", str(tm / "update.sh")], env=env,
                              capture_output=True, text=True, cwd=str(tm))

    def cycle(self, tm: Path, *args: str, cwd: Path | None = None) -> None:
        local = tm / "manager" / "local"
        before = snapshot(local)
        for step in ("install", "reinstall", "update", "install-after-update"):
            if step == "update":
                result = self.update(tm)
            else:
                result = install(tm, *args, cwd=cwd)
            self.assertEqual(0, result.returncode,
                             f"{step}: {result.stdout}{result.stderr}")
            after = snapshot(local)
            for rel, body in before.items():
                self.assertEqual(body, after.get(rel),
                                 f"{rel} changed during {step}")

    def test_vendored(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        self.populate(tm / "manager" / "local")
        self.cycle(tm)

    def test_external(self):
        project = make_project(self.tmp / "app")
        tm = self.tmp / "bench"
        self.unpack(tm)
        local = tm / "manager" / "local"
        self.populate(local)
        work = self.tmp / "work"
        # The layout is recorded in .env by the first run, so .env is
        # left out of the byte comparison and checked for its own value.
        (local / ".env").unlink()
        self.cycle(tm, "--app", str(project), "--work", str(work),
                   cwd=self.tmp)
        self.assertIn(f"BOARD_APP_ROOT={project}",
                      (local / ".env").read_text(encoding="utf-8"))

    def test_a_partial_directory_gains_only_what_is_missing(self):
        for present in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(present=present):
                project = make_project(self.tmp / f"app-{present}")
                tm = project / ".task-manager"
                self.unpack(tm)
                local = tm / "manager" / "local"
                self.populate(local, names=(present,))
                mine = (local / present).read_bytes()
                result = install(tm)
                self.assertEqual(0, result.returncode, result.stdout)
                missing = "CLAUDE.md" if present == "AGENTS.md" else "AGENTS.md"
                self.assertEqual(mine, (local / present).read_bytes())
                self.assertEqual((SEED / missing).read_bytes(),
                                 (local / missing).read_bytes())
                self.assertIn(f"manager/local/{missing}", result.stdout)
                self.assertNotIn(f"manager/local/{present}\n", result.stdout)

    def test_seeding_is_idempotent(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        first = install(tm)
        local = tm / "manager" / "local"
        stamps = {n: (local / n).stat().st_mtime_ns
                  for n in ("AGENTS.md", "CLAUDE.md")}
        second = install(tm)
        self.assertIn("seeded the neutral starters", first.stdout)
        self.assertNotIn("seeded", second.stdout)
        self.assertEqual(stamps, {n: (local / n).stat().st_mtime_ns
                                  for n in ("AGENTS.md", "CLAUDE.md")})

    def test_an_empty_file_is_the_projects_and_is_kept(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        (tm / "manager" / "local" / "AGENTS.md").write_bytes(b"")
        install(tm)
        self.assertEqual(b"", (tm / "manager" / "local" / "AGENTS.md")
                         .read_bytes())

    def test_dry_run_seeds_nothing(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        result = install(tm, "--dry-run")
        self.assertIn("would seed", result.stdout)
        self.assertFalse((tm / "manager" / "local" / "AGENTS.md").exists())

    def test_benchs_old_policy_is_reported_and_kept(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        local = tm / "manager" / "local"
        for name in ("AGENTS.md", "checks"):
            shutil.copyfile(FIXTURES / name, local / name)
        before = snapshot(local)
        for _ in range(2):
            result = install(tm)
            self.assertEqual(0, result.returncode, result.stdout)
            self.assertIn("bench's own development policy", result.stdout)
            self.assertIn("manager/local/AGENTS.md", result.stdout)
            self.assertIn("manager/local/checks", result.stdout)
            for rel in ("AGENTS.md", "checks"):
                self.assertEqual(before[rel], (local / rel).read_bytes())

    # — links —

    def test_a_dangling_link_is_never_written_through(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        outside = self.tmp / "outside" / "AGENTS.md"
        outside.parent.mkdir()
        (tm / "manager" / "local" / "AGENTS.md").symlink_to(outside)
        result = install(tm)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertFalse(outside.exists(), "a write followed the link")
        self.assertTrue((tm / "manager" / "local" / "AGENTS.md").is_symlink())

    def test_a_local_directory_leading_out_refuses_before_any_write(self):
        project = make_project(self.tmp / "app")
        tm = project / ".task-manager"
        self.unpack(tm)
        elsewhere = self.tmp / "elsewhere"
        shutil.move(str(tm / "manager" / "local"), str(elsewhere))
        (tm / "manager" / "local").symlink_to(elsewhere)
        before = snapshot(elsewhere)
        result = install(tm)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("outside the install", result.stdout)
        self.assertEqual(before, snapshot(elsewhere),
                         "something was written outside the install")


# ── a source checkout updated with git ───────────────────────────────


class GitUpdateMigration(unittest.TestCase):
    """An operator's source clone made before task 78 tracks bench's local
    notes. The update that stops tracking them goes through the documented
    sequence: protect-local.py from the target, git pull, install.py."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-78-git-")).resolve()
        upstream = make_source_repo(cls.scratch / "upstream")
        local = upstream / "manager" / "local"
        # The past: bench's notes tracked where the project's belong.
        for name in LEGACY:
            shutil.copyfile(FIXTURES / name, local / name)
        git(upstream, "add", "-A")
        git(upstream, "commit", "-q", "-m", "legacy: tracked local notes")
        cls.legacy = git(upstream, "rev-parse", "HEAD").stdout.strip()
        for name in LEGACY:
            (local / name).unlink()
        git(upstream, "add", "-A")
        git(upstream, "commit", "-q", "-m", "78: local notes untracked")
        cls.current = git(upstream, "rev-parse", "HEAD").stdout.strip()
        cls.upstream = upstream

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))
        self.tm = self.tmp / "bench"
        git(self.tmp, "clone", "-q", str(self.upstream), str(self.tm))
        git(self.tm, "reset", "-q", "--hard", self.legacy)
        self.local = self.tm / "manager" / "local"
        # The documented first step: the preflight from the target revision,
        # copied out before anything is checked out.
        self.preflight = self.tmp / "protect-local.py"
        self.preflight.write_text(
            git(self.tm, "show", f"{self.current}:protect-local.py").stdout,
            encoding="utf-8")

    def protect(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.preflight), *args],
                              capture_output=True, text=True,
                              cwd=str(self.tm), env=clean_env())

    def pull(self, check: bool = True) -> subprocess.CompletedProcess:
        return git(self.tm, "pull", "-q", "--ff-only", check=check)

    def index(self) -> list[str]:
        return git(self.tm, "ls-files", "manager/local").stdout.split()

    def assert_no_bench_policy(self) -> None:
        for path in self.local.rglob("*"):
            if path.is_file() and "state" not in path.parts:
                self.assertNotIn(BENCH_DOD, path.read_text(encoding="utf-8"),
                                 f"{path} carries bench's policy")

    def test_without_the_preflight_git_alone_loses_or_refuses(self):
        """Why the preflight exists: an edited note blocks the pull, and an
        untouched one is deleted — which is right for bench's policy only."""
        (self.local / "AGENTS.md").write_text("# ours\n", encoding="utf-8")
        refused = self.pull(check=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertEqual("# ours\n", (self.local / "AGENTS.md").read_text())

    def test_pristine_bench_policy_goes_and_the_neutral_starter_arrives(self):
        result = self.protect("--apply")
        self.assertEqual(0, result.returncode, result.stdout)
        for name in LEGACY:
            self.assertIn(f"bench      manager/local/{name}", result.stdout)
        self.assertFalse((self.local / "state" / "migrations").exists(),
                         "nothing of bench's needed protecting")
        self.pull()
        self.assertFalse((self.local / "AGENTS.md").exists())
        installed = install(self.tm, "--app", str(make_project(
            self.tmp / "app")), "--work", str(self.tmp / "work"), cwd=self.tmp)
        self.assertEqual(0, installed.returncode, installed.stdout)
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertEqual((SEED / name).read_bytes(),
                             (self.local / name).read_bytes())
        self.assertFalse((self.local / "checks").exists())
        self.assert_no_bench_policy()
        # Again, and again: nothing to migrate, nothing resurrected.
        again = self.protect("--apply")
        self.assertIn("nothing to migrate", again.stdout)
        install(self.tm, cwd=self.tmp)
        self.assert_no_bench_policy()

    def test_customised_notes_survive_byte_for_byte(self):
        mine = {"AGENTS.md": b"# Our project\n\nRun `make check`.\n",
                "checks": b"make: \\bmake check\\b\n"}
        for name, body in mine.items():
            (self.local / name).write_bytes(body)
        untracked = self.local / "prompts" / "work.md"
        untracked.write_bytes(b"our prompt {body}\n")
        env_file = self.local / ".env"
        env_file.write_bytes(b"BOARD_PORT=26888\n")

        dry = self.protect()
        self.assertEqual(0, dry.returncode, dry.stdout)
        self.assertIn("protect    manager/local/AGENTS.md", dry.stdout)
        self.assertIn("Dry run", dry.stdout)
        self.assertIn("manager/local/AGENTS.md", self.index(),
                      "a dry run changed the index")

        applied = self.protect("--apply")
        self.assertEqual(0, applied.returncode, applied.stdout)
        self.assertIn("git pull --ff-only", applied.stdout)
        record = json.loads((self.local / "state" / "migrations" /
                             "local-ownership" / "record.json").read_text())
        for name, body in mine.items():
            entry = record["files"][f"manager/local/{name}"]
            copy = self.tm / entry["copy"]
            self.assertEqual(body, copy.read_bytes())
            # Put back to bench's committed version, only once the copy
            # was verified, so the update can remove it.
            self.assertEqual((FIXTURES / name).read_bytes(),
                             (self.local / name).read_bytes())
        self.assertEqual("", git(self.tm, "status", "--porcelain",
                                 "--untracked-files=no").stdout)
        # Asked again before the pull: still protected, nothing redone.
        again = self.protect("--apply")
        self.assertEqual(0, again.returncode, again.stdout)
        self.assertIn("protected  manager/local/AGENTS.md", again.stdout)

        self.pull()
        self.assertFalse((self.local / "AGENTS.md").exists())
        install(self.tm, "--app", str(make_project(self.tmp / "app")),
                "--work", str(self.tmp / "work"), cwd=self.tmp)
        for name, body in mine.items():
            self.assertEqual(body, (self.local / name).read_bytes())
        self.assertEqual(b"our prompt {body}\n", untracked.read_bytes())
        self.assertIn(b"BOARD_PORT=26888\n", env_file.read_bytes())
        self.assertEqual((SEED / "CLAUDE.md").read_bytes(),
                         (self.local / "CLAUDE.md").read_bytes())

        # Repeated: no second record, no change.
        recorded = (self.local / "state" / "migrations" / "local-ownership"
                    / "record.json").read_bytes()
        self.assertIn("nothing to migrate", self.protect("--apply").stdout)
        self.assertEqual(recorded, (self.local / "state" / "migrations" /
                                    "local-ownership" / "record.json")
                         .read_bytes())

    def test_protecting_twice_before_the_pull_records_once(self):
        (self.local / "AGENTS.md").write_bytes(b"# ours\n")
        self.protect("--apply")
        record_path = (self.local / "state" / "migrations" / "local-ownership"
                       / "record.json")
        first = record_path.read_bytes()
        second = self.protect("--apply")
        self.assertEqual(0, second.returncode, second.stdout)
        self.assertIn("protected  manager/local/AGENTS.md", second.stdout)
        self.assertEqual(first, record_path.read_bytes())
        snapshots = list((record_path.parent / "snapshots").iterdir())
        self.assertEqual(1, len(snapshots))

    def test_a_committed_customisation_is_kept_through_a_merge(self):
        (self.local / "AGENTS.md").write_bytes(b"# committed here\n")
        git(self.tm, "commit", "-q", "-am", "our notes")
        applied = self.protect("--apply")
        self.assertEqual(0, applied.returncode, applied.stdout)
        self.assertIn("commit     manager/local/AGENTS.md", applied.stdout)
        self.assertIn("commit the untracking", applied.stdout)
        git(self.tm, "commit", "-q", "-m", "Untrack manager/local notes")
        git(self.tm, "pull", "-q", "--no-rebase", "--no-edit")
        self.assertEqual(b"# committed here\n",
                         (self.local / "AGENTS.md").read_bytes())

    def test_ambiguous_cases_change_nothing_and_say_what_to_decide(self):
        cases = {
            "link": lambda: ((self.local / "AGENTS.md").unlink(),
                             (self.local / "AGENTS.md").symlink_to(
                                 self.tmp / "elsewhere.md")),
            "staged-and-unstaged": lambda: (
                (self.local / "checks").write_bytes(b"one: x\n"),
                git(self.tm, "add", "manager/local/checks"),
                (self.local / "checks").write_bytes(b"two: y\n")),
        }
        for label, arrange in cases.items():
            with self.subTest(label):
                git(self.tm, "reset", "-q", "--hard", self.legacy)
                git(self.tm, "clean", "-qfdx", "manager/local")
                arrange()
                index = git(self.tm, "ls-files", "-s", "manager/local").stdout
                disk = snapshot(self.local)
                result = self.protect("--apply")
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn("Decision needed — nothing was changed",
                              result.stdout)
                self.assertEqual(index, git(self.tm, "ls-files", "-s",
                                            "manager/local").stdout)
                self.assertEqual(disk, snapshot(self.local))

    def test_a_file_edited_again_after_protecting_is_ambiguous(self):
        (self.local / "AGENTS.md").write_bytes(b"# first\n")
        self.protect("--apply")
        (self.local / "AGENTS.md").write_bytes(b"# second thoughts\n")
        index = git(self.tm, "ls-files", "-s", "manager/local").stdout
        result = self.protect("--apply")
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("changed again after it was protected", result.stdout)
        self.assertEqual(b"# second thoughts\n",
                         (self.local / "AGENTS.md").read_bytes())
        self.assertEqual(index, git(self.tm, "ls-files", "-s",
                                    "manager/local").stdout)

    def test_a_restore_never_overwrites_a_different_file(self):
        (self.local / "AGENTS.md").write_bytes(b"# protected version\n")
        self.protect("--apply")
        self.pull()
        (self.local / "AGENTS.md").write_bytes(b"# written meanwhile\n")
        record_path = (self.local / "state" / "migrations" /
                       "local-ownership" / "record.json")
        before = record_path.read_bytes()
        result = install(self.tm, cwd=self.tmp)
        self.assertIn("need a decision", result.stdout)
        self.assertIn("a different file is there now", result.stdout)
        self.assertEqual(b"# written meanwhile\n",
                         (self.local / "AGENTS.md").read_bytes())
        self.assertEqual(before, record_path.read_bytes())
        copy = self.tm / json.loads(before)["files"][
            "manager/local/AGENTS.md"]["copy"]
        self.assertEqual(b"# protected version\n", copy.read_bytes())

    def test_an_untracked_file_where_the_target_adds_one_is_named(self):
        # A target that adds a local file over an untracked one of ours.
        self.local.joinpath("driver", "start").write_bytes(b"ours\n")
        upstream_add = self.tmp / "upstream-add"
        git(self.tmp, "clone", "-q", str(self.upstream), str(upstream_add))
        (upstream_add / "manager/local/driver/start").write_bytes(b"theirs\n")
        git(upstream_add, "add", "-A")
        git(upstream_add, "commit", "-q", "-m", "adds a driver")
        git(self.tm, "fetch", "-q", str(upstream_add), "main")
        result = self.protect("--target", "FETCH_HEAD", "--apply")
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("manager/local/driver/start", result.stdout)
        self.assertEqual(b"ours\n",
                         self.local.joinpath("driver", "start").read_bytes())

    def test_a_state_directory_leading_out_refuses(self):
        (self.local / "AGENTS.md").write_bytes(b"# ours\n")
        elsewhere = self.tmp / "elsewhere-state"
        elsewhere.mkdir()
        (self.local / "state").symlink_to(elsewhere)
        result = self.protect("--apply")
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertEqual([], list(elsewhere.iterdir()))
        self.assertIn("manager/local/AGENTS.md", self.index())

    def test_an_install_that_is_not_its_own_checkout_has_nothing_to_do(self):
        vendored = self.tmp / "vendored"
        shutil.copytree(self.tm, vendored, symlinks=True,
                        ignore=shutil.ignore_patterns(".git"))
        result = subprocess.run([sys.executable, str(self.preflight),
                                 "--install", str(vendored)],
                                capture_output=True, text=True,
                                cwd=str(self.tmp), env=clean_env())
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("nothing to migrate", result.stdout)


if __name__ == "__main__":
    unittest.main()
