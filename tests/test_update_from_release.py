"""update.sh consumes published releases: replace manager/core/ wholesale
plus the manifest's top-level files, touch nothing else, and refuse loudly
— changing nothing — when there is no release or the asset lies about its
version. Hermetic: gh and curl are PATH stubs, the "release" is a real
artifact built by release.sh, and the installed project is a real unpack
of it.

    python3 -m unittest discover -s tests
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

GH_STUB = """\
#!/usr/bin/env bash
# gh stand-in: serves $STUB_TARBALL as the one release, tagged $STUB_TAG.
[ "${STUB_FAIL:-}" = "1" ] && exit 1
case "${1:-} ${2:-}" in
  "release view")
    printf '%s\\n' "${STUB_TAG:?}"
    ;;
  "release download")
    out=""
    prev=""
    for arg in "$@"; do
      [ "$prev" = "--output" ] && out="$arg"
      prev="$arg"
    done
    cp "${STUB_TARBALL:?}" "${out:?}"
    ;;
  *) exit 1 ;;
esac
"""

CURL_STUB = """\
#!/usr/bin/env bash
exit 22
"""


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


class UpdateFromRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-update-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(
            ["bash", str(REPO / "release.sh"), "--tarball", str(cls.tarball),
             "--source", "example/bench"],
            capture_output=True, text=True)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"release.sh failed:\n{result.stdout}{result.stderr}")
        cls.version = (REPO / "manager" / "core" / "VERSION").read_text().strip()

        cls.stubs = cls.scratch / "bin"
        cls.stubs.mkdir()
        for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
            stub = cls.stubs / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def make_install(self) -> Path:
        """A host project installed from the artifact, then lived in:
        its own card, settings, checks, notes and state."""
        host = Path(tempfile.mkdtemp(prefix="host-", dir=self.scratch))
        tm = host / ".task-manager"
        tm.mkdir()
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)
        # The artifact ships no work state: the installer lays it down, as
        # start.sh does on the documented path.
        subprocess.run(
            [sys.executable, str(tm / "install.py")], cwd=host,
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith(("BOARD_", "BENCH_"))})
        (tm / "tasks" / "backlog" / "20-host-card.md").write_text(
            "# The host's own\n", encoding="utf-8")
        local = tm / "manager" / "local"
        (local / ".env").write_text("BOARD_PORT=26071\n", encoding="utf-8")
        (local / "checks").write_text("mine: \\bmine\\b\n", encoding="utf-8")
        (local / "CLAUDE.md").write_text("# Host notes\n", encoding="utf-8")
        (local / "state" / "sessions").mkdir(parents=True)
        (local / "state" / "sessions" / "s1.jsonl").write_text(
            '{"event":"kept"}\n', encoding="utf-8")
        (tm / "tasks" / "task-template.md").write_text(
            "# My own template\n", encoding="utf-8")
        return tm

    def run_update(self, tm: Path, **extra: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "BENCH_", "STUB_"))}
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("STUB_TAG", f"v{self.version}")
        env.setdefault("STUB_TARBALL", str(self.tarball))
        env.update(extra)
        return subprocess.run(["bash", str(tm / "update.sh")],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=tm.parent, env=env)

    def test_update_replaces_core_and_top_level_and_nothing_else(self):
        tm = self.make_install()
        # An "older install": stale core content and doctored top-level
        # files that the release must put back.
        (tm / "manager" / "core" / "VERSION").write_text("0\n")
        (tm / "manager" / "core" / "stale.py").write_text("gone = True\n")
        (tm / "start.sh").write_text("#!/bin/sh\necho old\n")
        survivors = {
            path: (tm / path).read_bytes()
            for path in ["tasks/backlog/20-host-card.md",
                         "manager/local/.env", "manager/local/checks",
                         "manager/local/CLAUDE.md",
                         "manager/local/state/sessions/s1.jsonl",
                         "tasks/task-template.md"]}

        result = self.run_update(tm)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"version 0 → {self.version}", result.stdout)
        self.assertFalse((tm / "manager" / "core" / "stale.py").exists(),
                         "core must be replaced wholesale")
        with tarfile.open(self.tarball) as tar:
            for name in ("manager/core/VERSION", "manager/core/board.py",
                         "start.sh", "update.sh", "CLAUDE.md"):
                shipped = tar.extractfile(f"./{name}").read()
                self.assertEqual((tm / name).read_bytes(), shipped,
                                 f"{name} must match the release")
        for path, content in survivors.items():
            self.assertEqual((tm / path).read_bytes(), content,
                             f"{path} must survive byte-identical")

    def test_update_heals_a_non_executable_install_py(self):
        # The v0.1-alpha field report: installs unpacked from an artifact
        # that carried mode 644 stay broken by themselves, because `cp`
        # onto an existing file keeps the destination's mode. update.sh's
        # chmod line is what heals them — existing victims, not only fresh
        # installs.
        tm = self.make_install()
        (tm / "install.py").chmod(0o644)

        result = self.run_update(tm)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.access(tm / "install.py", os.X_OK),
            "update.sh must restore install.py's executable bit")

    def test_agents_brief_replaces_an_old_vendor_named_copy(self):
        # Ported from the retired test_update_round_trip.py (whose harness
        # targeted the removed git-clone mechanism): an install from the
        # pre-rename era has the full brief as CLAUDE.md and no AGENTS.md;
        # updating must deliver the brief under the cross-vendor name and
        # turn CLAUDE.md into the pointer.
        tm = self.make_install()
        old_brief = "# Task Workflow\n\nThe old full vendor-named brief.\n"
        (tm / "CLAUDE.md").write_text(old_brief, encoding="utf-8")
        (tm / "AGENTS.md").unlink()

        result = self.run_update(tm)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.tarball) as tar:
            agents = tar.extractfile("./AGENTS.md").read().decode("utf-8")
        self.assertEqual((tm / "AGENTS.md").read_text(encoding="utf-8"),
                         agents)
        self.assertIn("# Task Workflow", agents)
        pointer = (tm / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AGENTS.md", pointer)
        self.assertNotEqual(pointer, old_brief)

    def test_no_published_release_changes_nothing(self):
        tm = self.make_install()
        before = snapshot(tm)

        result = self.run_update(tm, STUB_FAIL="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No published release found for example/bench",
                      result.stderr)
        self.assertIn("Nothing was changed", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_version_tag_disagreement_is_refused(self):
        tm = self.make_install()
        before = snapshot(tm)

        result = self.run_update(tm, BENCH_REF="v999")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"contains core VERSION {self.version}", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_asset_with_escaping_member_paths_is_refused(self):
        tm = self.make_install()
        bad = self.scratch / "bad-members.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo("../escape")
            payload = b"outside\n"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        before = snapshot(tm)

        result = self.run_update(tm, STUB_TARBALL=str(bad))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("escape its own root", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_unsafe_manifest_copy_path_is_refused(self):
        # A well-formed asset whose manifest reaches outside .task-manager/
        # must be refused whole — before core/ is touched.
        tm = self.make_install()
        workdir = self.scratch / "bad-manifest"
        with tarfile.open(self.tarball) as tar:
            tar.extractall(workdir)
        manifest = workdir / "manager" / "core" / "release-manifest"
        manifest.write_text(manifest.read_text(encoding="utf-8")
                            + "copy ../outside\n", encoding="utf-8")
        bad = self.scratch / "bad-manifest.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            tar.add(workdir, arcname=".")
        before = snapshot(tm)

        result = self.run_update(tm, STUB_TARBALL=str(bad))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe path: ../outside", result.stderr)
        self.assertEqual(snapshot(tm), before)

    def test_exact_tag_via_bench_ref(self):
        tm = self.make_install()
        result = self.run_update(tm, BENCH_REF=f"v{self.version}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Updated core from v{self.version}", result.stdout,
                      result.stdout + result.stderr)

    # ── the Brain survives updates (task 173) ──
    #
    # brain/ is work state: it lives in the work root, which is the install
    # itself vendored or source-hosted and BOARD_WORK_ROOT externally.
    # update.sh replaces manager/core/ and the manifest's top-level files,
    # and the documented install.py that follows re-wires; neither may
    # change a byte, a mode, a time or an inode of an existing entry — or
    # of anything else a person keeps there. Each layout runs the real
    # updater twice from an older install (old → new, then new → new) with
    # install.py after each, as the update's own output tells a person to.

    def install_env(self) -> dict:
        return {k: v for k, v in os.environ.items()
                if not k.startswith(("BOARD_", "BENCH_", "STUB_"))}

    def git(self, repo: Path, *args: str) -> str:
        env = {**self.install_env(), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"git {args}: {result.stderr}")
        return result.stdout

    def run_install(self, tm: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(tm / "install.py"), *args], cwd=tm,
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=self.install_env())
        self.assertEqual(result.returncode, 0,
                         f"install.py {args} failed:\n{result.stdout}{result.stderr}")
        return result

    def layout(self, kind: str):
        """An installed tool in one layout, and the work root holding its
        brain/. The installs are real unpacks of the artifact."""
        top = Path(tempfile.mkdtemp(prefix=f"brain-{kind}-", dir=self.scratch))
        if kind == "external":
            app, work, tm = top / "app", top / "work", top / "bench"
            for repo in (app, work):
                repo.mkdir()
                self.git(repo, "init", "-q", "-b", "main")
            (app / "README.md").write_text("the application\n", encoding="utf-8")
            self.git(app, "add", "-A")
            self.git(app, "commit", "-qm", "app")
            tm.mkdir()
            with tarfile.open(self.tarball) as tar:
                tar.extractall(tm)
            self.run_install(tm, "--app", str(app), "--work", str(work))
            return tm, work, {"app": app}
        host = top / "host"
        host.mkdir()
        self.git(host, "init", "-q", "-b", "main")
        (host / ".claude").mkdir()     # a project the claude adapter can wire
        tm = host / ".task-manager" if kind == "vendored" else host
        tm.mkdir(exist_ok=True)
        with tarfile.open(self.tarball) as tar:
            tar.extractall(tm)
        self.run_install(tm)
        return tm, tm, {}

    def write_brain(self, work: Path) -> Path:
        brain = work / "brain"
        brain.mkdir()
        entries = {
            "routing.md": "# Report routing\n\n**Brain:** 1\n**Id:** e-report-routing\n"
                          "**Kind:** Guide\n**Classification:** Current\n"
                          "**Summary:** Filed on its own card.\n\nThe body.\n\n"
                          "## Classification history\n\n- 2026-11-19 · Current · ron — shipped\n",
            "old.md": "# Old notes\n\n**Brain:** 1\n**Id:** e-old\n**Kind:** Decision\n"
                      "**Classification:** Superseded\n**Superseded by:** e-report-routing\n\nOld.\n",
            "future.md": "# From a newer bench\n\n**Brain:** 2\n**Id:** e-future\n\nKept as is.\n",
            "notes.txt": "not an entry; reserved for later versions\n",
            ".draft.md": "a hidden draft somebody keeps here\n",
        }
        for i, (name, text) in enumerate(entries.items()):
            path = brain / name
            path.write_bytes(text.encode("utf-8"))
            path.chmod(0o640 if i % 2 else 0o600)
            os.utime(path, ns=(1_700_000_000_000_000_000 + i, 1_700_000_000_000_000_000 + i))
        (brain / "routing-link.md").symlink_to("routing.md")
        return brain

    def brain_state(self, brain: Path) -> dict:
        """Everything a person could notice about brain/: every name, its
        type, bytes or link target, mode, size, mtime and inode — and the
        directory's own."""
        def facts(p: Path):
            st = p.lstat()
            if p.is_symlink():
                content = ("link", os.readlink(p))
            elif p.is_dir():
                content = ("dir", None)
            else:
                content = ("file", p.read_bytes())
            return content, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino
        state = {".": facts(brain)[1:]}
        for p in sorted(brain.rglob("*")):
            state[p.relative_to(brain).as_posix()] = facts(p)
        return state

    def assert_brain_survives_updates(self, kind: str):
        tm, work, roots = self.layout(kind)
        brain = self.write_brain(work)
        if kind == "external":
            # the work state's own repository holds it, as the board's
            # commits would
            self.git(work, "add", "brain")
            self.git(work, "commit", "-qm", "board: brain entries")
        head = self.git(work, "rev-parse", "HEAD").strip() if kind == "external" else None
        status = self.git(work, "status", "--porcelain", "--", "brain") if kind == "external" else None
        before = self.brain_state(brain)
        self.assertEqual(len(before), 7, before.keys())
        app_before = snapshot(roots["app"]) if "app" in roots else None
        # an older install: the release must actually replace its core
        (tm / "manager" / "core" / "VERSION").write_text("0\n")

        for round_, since in (("old → new", "0"), ("repeat", self.version)):
            result = self.run_update(tm)
            self.assertEqual(result.returncode, 0, f"{kind} {round_}: " + result.stdout + result.stderr)
            self.assertIn(f"version {since} → {self.version}", result.stdout, f"{kind} {round_}")
            self.assertEqual((tm / "manager" / "core" / "VERSION").read_text().strip(), self.version)
            self.assertEqual(self.brain_state(brain), before,
                             f"{kind} {round_}: update.sh changed brain/")
            self.run_install(tm)
            self.assertEqual(self.brain_state(brain), before,
                             f"{kind} {round_}: install.py after the update changed brain/")
        if kind == "external":
            self.assertEqual(self.git(work, "rev-parse", "HEAD").strip(), head)
            self.assertEqual(self.git(work, "status", "--porcelain", "--", "brain"), status)
            # the tool root has no brain/ of its own, before or after
            self.assertFalse((tm / "brain").exists(), "an update created brain/ beside the tool")
            self.assertEqual(snapshot(roots["app"]), app_before, "the application changed")
        else:
            self.assertEqual(work, tm)

    def test_brain_survives_updates_vendored(self):
        self.assert_brain_survives_updates("vendored")

    def test_brain_survives_updates_source_hosted(self):
        self.assert_brain_survives_updates("source")

    def test_brain_survives_updates_external(self):
        self.assert_brain_survives_updates("external")

    def test_env_beats_stamp_and_dev_checkout_has_no_source(self):
        # The repo's own update.sh is unstamped: with no BENCH_SOURCE
        # anywhere it must refuse and say how to configure one.
        tm = self.scratch / "dev-checkout"
        tm.mkdir()
        shutil.copy(REPO / "update.sh", tm / "update.sh")
        result = self.run_update(tm)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BENCH_SOURCE", result.stderr)

        # BENCH_SOURCE (any GitHub spelling) resurrects it — the stub
        # then serves the release as usual.
        (tm / "manager" / "core").mkdir(parents=True)
        result = self.run_update(
            tm, BENCH_SOURCE="git@github.com:example/bench.git")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((tm / "manager" / "core" / "board.py").is_file())


if __name__ == "__main__":
    unittest.main()
