"""Seeding the Brain, step one: inspecting approved sources (task 192).

Every case runs on disposable repositories — the split-Git layout
`test_brainwrite` uses (an application and a separate work repository),
and a vendored and a source-hosted layout built here.

- **Identities, read back.** Each root's repository, branch, commit and
  uncommitted work; the layout; whether the two are one repository; and the
  Brain destination with its revisions.
- **Refused, never repaired.** A layout `config.problems()` refuses, a work
  root in no repository, a work state inside the application's repository
  and a `brain/` that is a link each refuse — nothing is listed or
  inspected, no repository is created, nothing falls back to application
  git.
- **Static and passive.** The listing opens no file; listing and
  inspection run only local git reads, launch no process but git, open no
  socket and write nothing — not to either repository, not to bench's state.
- **What is read is exactly what was approved**, with bytes, hashes,
  slices and git state, and absent kept apart from inaccessible; secrets,
  dependency trees, logs, links and escapes are excluded with why.
- **The manifest is a contract.** Versioned, bounded, digest-bound,
  authorizing nothing; `drift` reports what moved and refuses a tampered
  manifest.

    python3 -m unittest tests.test_seedsources -v
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

from tests.test_brainwrite import _Layout, git, tree  # noqa: E402  (sets sys.path)
from tests.test_settings_security import Board  # noqa: E402

import config  # noqa: E402
import seedsources  # noqa: E402

README = "# Orchard\n\nOrchard tracks fruit trees.\n\n## Running\n\nmake run\n"
CONTRIB = "# Contributing\n\nEvery change needs a review.\n"
READ_VERBS = {"rev-parse", "symbolic-ref", "status", "ls-files", "ls-tree"}


def sha(data) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


class _Watch:
    """Records every git verb and every process; refuses any socket."""

    def __init__(self, test: unittest.TestCase):
        self.verbs: list[str] = []
        self.processes: list[str] = []
        real_git = config.git_run
        real_popen = subprocess.Popen

        def git_run(root, *args, **kw):
            self.verbs.append(args[0] if args else "")
            return real_git(root, *args, **kw)

        def popen(argv, *a, **kw):
            self.processes.append(os.path.basename(str(argv[0] if isinstance(argv, list)
                                                       else argv).split()[0]))
            return real_popen(argv, *a, **kw)

        def connect(*_a, **_k):
            raise AssertionError("a socket was opened")

        for p in (mock.patch.object(config, "git_run", git_run),
                  mock.patch("subprocess.Popen", popen),
                  mock.patch.object(socket.socket, "connect", connect)):
            p.start()
            test.addCleanup(p.stop)


def git_state(repo: Path) -> tuple:
    """What a write to a repository would change: its files, refs and index."""
    index = repo / ".git" / "index"
    return (tree(repo), git(repo, "rev-parse", "HEAD").stdout,
            git(repo, "for-each-ref").stdout, git(repo, "count-objects", "-v").stdout,
            index.read_bytes() if index.exists() else b"")


class _Seed(_Layout):

    def setUp(self):
        super().setUp()
        (self.app / "README.md").write_text(README)
        (self.app / "CONTRIBUTING.md").write_text(CONTRIB)
        (self.app / "docs").mkdir()
        (self.app / "docs" / "architecture.md").write_text("# Architecture\n\nTwo services.\n")
        (self.app / "deploy").mkdir()
        (self.app / "deploy" / "runbook.md").write_text("# Runbook\n\nRestart it.\n")
        (self.app / ".env").write_text("API_KEY=hunter2\n")
        (self.app / "node_modules" / "left-pad").mkdir(parents=True)
        (self.app / "node_modules" / "left-pad" / "index.js").write_text("module.exports=1\n")
        (self.app / "server.log").write_text("GET / 200\n")
        (self.app / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
        git(self.app, "add", "-f", "-A")
        git(self.app, "commit", "-qm", "docs")
        (self.app / "notes.md").write_text("untracked notes\n")
        (self.work / "plans").mkdir()
        (self.work / "plans" / "roadmap.md").write_text("# Roadmap\n\nNext: exports.\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "plans")
        problems = mock.patch.object(config, "problems", lambda *a, **k: [])
        problems.start()
        self.addCleanup(problems.stop)
        self.app_before = tree(self.app)
        self.cards_before = tree(self.work / "tasks")

    def tearDown(self):
        # links and unreadable files a case made are skipped, not followed
        self.assertEqual(tree_safe(self.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.work / "tasks"), self.cards_before, "a card changed")

    def inspect(self, *sources, **extra) -> dict:
        return seedsources.inspect({"sources": [
            {"root": s[0], "path": s[1], **(s[2] if len(s) > 2 else {})} for s in sources],
            **extra})

    def by_id(self, manifest: dict) -> dict:
        return {i["id"]: i for i in manifest["items"]}


class Identities(_Seed):

    def test_independent_roots_are_read_back(self):
        out = seedsources.identities()
        self.assertFalse(out["refused"], out["problems"])
        self.assertEqual(out["layout"], "external")
        self.assertEqual(out["separation"], "independent")
        self.assertEqual(out["app"]["repository"], str(self.app))
        self.assertEqual(out["app"]["commit"], git(self.app, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual(out["app"]["branch"], "main")
        self.assertEqual(out["app"]["dirty"], 1)          # notes.md, untracked
        self.assertEqual(out["work"]["root"], str(self.work))
        self.assertEqual(out["work"]["commit"], git(self.work, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual(out["destination"]["brain"]["state"], "missing")
        self.assertEqual(out["destination"]["brain"]["revision"], "none")
        self.assertEqual(out["destination"]["handbook"]["revision"], "none")
        self.assertIn("never a source", out["tool"]["role"])

    def test_a_refused_layout_lists_and_inspects_nothing(self):
        with mock.patch.object(config, "problems",
                               lambda *a, **k: ["BOARD_WORK_ROOT=x does not exist"]):
            read = seedsources.reading()
            self.assertTrue(read["refused"])
            self.assertEqual(read["problems"][0]["code"], "layout-invalid")
            self.assertEqual(read["candidates"], {"app": [], "work": []})
            with self.assertRaises(seedsources.Refused) as caught:
                self.inspect(("app", "README.md"))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "mapping-refused")

    def test_a_work_root_in_no_repository_refuses_without_creating_one(self):
        os.rename(self.work / ".git", self.dir / "work-git-aside")
        self.addCleanup(os.rename, self.dir / "work-git-aside", self.work / ".git")
        config._GIT_ROOTS.clear() if hasattr(config, "_GIT_ROOTS") else None
        self.addCleanup(lambda: config._GIT_ROOTS.clear() if hasattr(config, "_GIT_ROOTS") else None)
        out = seedsources.identities()
        codes = [p["code"] for p in out["problems"]]
        self.assertIn("work-not-repository", codes)
        self.assertIn("git init", out["problems"][codes.index("work-not-repository")]["remedy"])
        self.assertFalse((self.work / ".git").exists(), "a repository was created")
        self.assertTrue(out["refused"])

    def test_a_work_state_in_the_application_repository_is_refused_externally(self):
        with mock.patch.object(config, "state_repo", lambda: self.app):
            out = seedsources.identities()
        self.assertIn("work-in-app-repository", [p["code"] for p in out["problems"]])

    def test_a_brain_that_is_a_link_out_is_refused(self):
        outside = self.dir / "elsewhere"
        outside.mkdir()
        os.symlink(outside, self.work / "brain")
        out = seedsources.identities()
        self.assertIn("destination-refused", [p["code"] for p in out["problems"]])


class Listing(_Seed):

    def test_candidates_are_names_and_sizes_with_exclusions_said(self):
        with mock.patch("builtins.open", side_effect=AssertionError("a file was opened")), \
                mock.patch("os.open", side_effect=AssertionError("a file was opened")):
            read = seedsources.reading()
        app = {c["path"]: c for c in read["candidates"]["app"]}
        work = {c["path"]: c for c in read["candidates"]["work"]}
        self.assertEqual(app["README.md"]["bytes"], len(README))
        self.assertEqual(app["README.md"]["category"], "docs")
        self.assertTrue(app["README.md"]["default"])
        self.assertEqual(app["CONTRIBUTING.md"]["category"], "instructions")
        self.assertEqual(app["CONTRIBUTING.md"]["treatment"], "policy")
        self.assertEqual(app["src.py"]["category"], "code")
        self.assertFalse(app["src.py"]["default"])
        self.assertFalse(app["notes.md"]["tracked"])
        for rel, word in ((".env", "credential"), ("node_modules/left-pad/index.js", "dependency"),
                          ("server.log", "log"), ("logo.png", "binary")):
            self.assertEqual(app[rel]["state"], "excluded", rel)
            self.assertIn(word, app[rel]["why"], rel)
            self.assertIsNone(app[rel].get("sha256"))
        self.assertEqual(work["plans/roadmap.md"]["category"], "plan")
        self.assertEqual(work["tasks/review/07-a-card.md"]["category"], "card")
        self.assertEqual(read["authorizes"], seedsources.AUTHORIZES)
        self.assertEqual([s["id"] for s in read["sections"]],
                         ["overview", "how-it-works", "ways-of-working", "direction", "operations"])


class Inspect(_Seed):

    def test_exactly_the_approved_selection_is_read(self):
        m = self.inspect(("app", "README.md"), ("app", "CONTRIBUTING.md"),
                         ("app", "src.py", {"treatment": "evidence"}),
                         ("work", "plans/roadmap.md"))
        self.assertEqual(m["v"], 1)
        self.assertEqual(m["kind"], "bench-brain-seed-manifest")
        items = self.by_id(m)
        self.assertEqual(set(items), {"app:README.md", "app:CONTRIBUTING.md", "app:src.py",
                                      "work:plans/roadmap.md"})
        readme = items["app:README.md"]
        self.assertEqual((readme["state"], readme["bytes"], readme["sha256"], readme["git"]),
                         ("read", len(README), sha(README), "committed"))
        self.assertEqual(items["app:CONTRIBUTING.md"]["treatment"], "policy")
        self.assertEqual(items["work:plans/roadmap.md"]["git"], "committed")
        self.assertEqual(m["repositories"]["app"]["commit"],
                         git(self.app, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual(m["selection"]["sections"][0], "overview")
        self.assertIn("nothing", m["authorizes"])
        self.assertEqual(m["digest"], seedsources.digest_of(m))
        # no source text travels in the manifest: identities and hashes only
        self.assertNotIn("fruit trees", json.dumps(m))

    def test_modified_and_untracked_content_has_its_own_identity(self):
        (self.app / "README.md").write_text(README + "\nEdited.\n")
        self.app_before = tree(self.app)
        items = self.by_id(self.inspect(("app", "README.md"), ("app", "notes.md")))
        self.assertEqual(items["app:README.md"]["git"], "modified")
        self.assertEqual(items["app:README.md"]["sha256"], sha(README + "\nEdited.\n"))
        self.assertEqual(items["app:notes.md"]["git"], "untracked")

    def test_a_slice_is_hashed_apart_from_its_file(self):
        items = self.by_id(self.inspect(("app", "README.md", {"lines": [1, 3]}),
                                        ("app", "README.md", {"lines": [50, 60]})))
        part = items["app:README.md#L1-L3"]
        first = "".join(README.splitlines(keepends=True)[:3])
        self.assertEqual((part["bytes"], part["sha256"]), (len(first), sha(first)))
        self.assertEqual(part["fileSha256"], sha(README))
        self.assertEqual(items["app:README.md#L50-L60"]["state"], "absent")

    def test_absent_is_not_inaccessible(self):
        (self.app / "secretless").mkdir()
        locked = self.app / "secretless" / "locked.md"
        locked.write_text("hidden\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        self.app_before = tree_safe(self.app)
        items = self.by_id(self.inspect(("app", "missing.md"), ("app", "secretless/locked.md")))
        self.assertEqual(items["app:missing.md"]["state"], "absent")
        if os.geteuid() != 0:
            self.assertEqual(items["app:secretless/locked.md"]["state"], "inaccessible")
            self.assertIsNone(items["app:secretless/locked.md"]["sha256"])

    def test_secrets_escapes_and_exclusions_are_refused_not_read(self):
        outside = self.dir / "outside.md"
        outside.write_text("outside\n")
        os.symlink(outside, self.app / "escape.md")
        os.symlink(self.dir, self.app / "updir")
        (self.app / "config.md").write_text(
            "token: ghp_" + "a" * 36 + "\n")
        self.app_before = tree_safe(self.app)
        items = self.by_id(self.inspect(
            ("app", ".env"), ("app", "escape.md"), ("app", "updir/outside.md"),
            ("app", "config.md"), ("app", "docs/architecture.md"),
            ("app", "node_modules/left-pad/index.js"), exclude=["docs/*"]))
        for rid in ("app:.env", "app:escape.md", "app:updir/outside.md", "app:config.md",
                    "app:docs/architecture.md", "app:node_modules/left-pad/index.js"):
            self.assertEqual(items[rid]["state"], "excluded", rid)
            self.assertIsNone(items[rid]["sha256"], rid)
        self.assertIn("credential", items["app:config.md"]["why"])
        self.assertIn("link", items["app:escape.md"]["why"])
        self.assertTrue(items["app:docs/architecture.md"]["byYou"])

    def test_a_malformed_selection_is_refused_before_anything_is_read(self):
        for payload, code in (({"sources": []}, "nothing-selected"),
                              ({"sources": [{"root": "tool", "path": "x"}]}, "bad-root"),
                              ({"sources": [{"root": "app", "path": "../etc/passwd"}]}, "bad-path"),
                              ({"sources": [{"root": "app", "path": "/etc/passwd"}]}, "bad-path"),
                              ({"sources": [{"root": "app", "path": "a", "lines": [3, 1]}]},
                               "bad-slice"),
                              ({"sources": [{"root": "app", "path": "a", "treatment": "obey"}]},
                               "bad-treatment"),
                              ({"sources": [{"root": "app", "path": "a"}],
                                "sections": ["marketing"]}, "bad-sections"),
                              ({"sources": [{"root": "app", "path": "a"}] * 2}, "duplicate"),
                              ({"sources": [{"root": "app", "path": f"f{n}"}
                                            for n in range(seedsources.MAX_SELECTED + 1)]},
                               "too-many")):
            with self.assertRaises(seedsources.Refused) as caught:
                seedsources.inspect(payload)
            self.assertEqual(caught.exception.code, code, payload)

    def test_coverage_names_gaps_and_never_fills_them(self):
        m = self.inspect(("app", "README.md"), sections=["overview", "operations",
                                                         "ways-of-working"])
        gaps = {g["section"] for g in m["coverage"]["gaps"]}
        self.assertEqual(gaps, {"operations", "ways-of-working"})
        m = self.inspect(("app", "README.md"), ("app", "deploy/runbook.md"),
                         ("app", "CONTRIBUTING.md"), sections=["operations", "ways-of-working"])
        self.assertEqual(m["coverage"]["gaps"], [])


class Passive(_Seed):

    def test_listing_and_inspection_run_only_git_reads_and_write_nothing(self):
        before = (git_state(self.app), git_state(self.work))
        watch = _Watch(self)
        seedsources.reading()
        m = self.inspect(("app", "README.md"), ("app", "src.py"), ("work", "plans/roadmap.md"),
                         ("app", "missing.md"))
        seedsources.drift(m)
        self.assertTrue(watch.verbs)
        self.assertLessEqual(set(watch.verbs), READ_VERBS, watch.verbs)
        self.assertEqual(set(watch.processes), {"git"}, watch.processes)
        self.assertEqual((git_state(self.app), git_state(self.work)), before)
        self.assertFalse((self.dir / "state").exists(), "bench's state was written")
        self.assertFalse((self.work / "brain").exists())


class Manifest(_Seed):

    def test_drift_reports_moved_bytes_commits_and_destination(self):
        m = self.inspect(("app", "README.md"), ("work", "plans/roadmap.md"))
        self.assertEqual(seedsources.drift(m), [])
        (self.app / "README.md").write_text("# Changed\n")
        git(self.app, "commit", "-qam", "change")
        self.app_before = tree(self.app)
        (self.work / "brain").mkdir()
        (self.work / "brain" / "e.md").write_text(
            "# E\n\n**Brain:** 1\n**Id:** e-one\n**Kind:** Guide\n**Classification:** Current\n")
        moved = {d["what"] for d in seedsources.drift(m)}
        self.assertIn("app:README.md", moved)
        self.assertIn("app commit", moved)
        self.assertIn("destination brain", moved)
        self.assertNotIn("work:plans/roadmap.md", moved)

    def test_a_tampered_or_foreign_manifest_is_refused(self):
        m = self.inspect(("app", "README.md"))
        forged = json.loads(json.dumps(m))
        forged["selection"]["sources"].append({"root": "app", "path": ".env",
                                               "treatment": "cite"})
        with self.assertRaises(seedsources.Refused) as caught:
            seedsources.drift(forged)
        self.assertEqual(caught.exception.code, "tampered")
        with self.assertRaises(seedsources.Refused) as caught:
            seedsources.drift({**m, "v": 2})
        self.assertEqual(caught.exception.code, "unsupported")

    def test_the_same_reading_digests_the_same(self):
        a = self.inspect(("app", "README.md"))
        b = self.inspect(("app", "README.md"))
        self.assertEqual(a["digest"], b["digest"])


def tree_safe(root: Path) -> dict:
    """`tree`, skipping what this process may not read or what escapes."""
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or p.is_symlink() or not p.is_file():
            continue
        try:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            out[str(p.relative_to(root))] = "unreadable"
    return out


class _OneRepo(unittest.TestCase):
    """A vendored or source-hosted layout: one repository holds both."""

    hosted = False

    def setUp(self):
        import tempfile
        import shutil
        self.dir = Path(tempfile.mkdtemp(prefix="bench-seed-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = self.dir / "app"
        self.app.mkdir()
        git(self.app, "init", "-q", "-b", "main")
        git(self.app, "config", "user.name", "ronald")
        git(self.app, "config", "user.email", "r@example.com")
        tool = self.app if self.hosted else self.app / ".task-manager"
        for stage, _ in config.STAGES:
            (tool / "tasks" / stage).mkdir(parents=True)
            (tool / "tasks" / stage / ".gitkeep").write_text("")
        (tool / "tasks" / "to-do" / "03-card.md").write_text("# 03 — Card\n")
        (tool / "manager" / "local").mkdir(parents=True)
        (tool / "manager" / "local" / "AGENTS.md").write_text("# local notes\n")
        (tool / "manager" / "core").mkdir(parents=True)
        (tool / "manager" / "core" / "board.py").write_text("print('bench')\n")
        (self.app / "README.md").write_text(README)
        git(self.app, "add", "-A")
        git(self.app, "commit", "-qm", "one")
        for p in (mock.patch.multiple(config, TASKS=tool / "tasks", REPO=self.app,
                                      EXTERNAL=False, TM_ROOT=tool, STATE=self.dir / "state"),
                  mock.patch.object(config, "problems", lambda *a, **k: [])):
            p.start()
            self.addCleanup(p.stop)
        self.tool = tool

    def test_one_repository_is_shared_not_refused(self):
        before = git_state(self.app)
        read = seedsources.reading()
        self.assertFalse(read["refused"], read["problems"])
        self.assertEqual(read["layout"], "source-hosted" if self.hosted else "vendored")
        self.assertEqual(read["separation"], "shared")
        app = {c["path"] for c in read["candidates"]["app"]}
        work = {c["path"] for c in read["candidates"]["work"]}
        self.assertIn("README.md", app)
        self.assertIn("tasks/to-do/03-card.md", work)
        # bench's private configuration is never a source, and the work
        # state's directories are listed once, as the work root's
        self.assertFalse(any("manager/local" in p for p in app), app)
        self.assertFalse(any(p.endswith("03-card.md") for p in app), app)
        if not self.hosted:
            self.assertFalse(any(p.startswith(".task-manager/") for p in app), app)
        m = seedsources.inspect({"sources": [
            {"root": "work", "path": "tasks/to-do/03-card.md"},
            {"root": "app", "path": "README.md"}]})
        items = {i["id"]: i for i in m["items"]}
        self.assertEqual(items["work:tasks/to-do/03-card.md"]["git"], "committed")
        self.assertEqual(items["app:README.md"]["git"], "committed")
        refused = seedsources.inspect({"sources": [
            {"root": "app", "path": ("" if self.hosted else ".task-manager/")
             + "manager/local/AGENTS.md"}]})
        self.assertEqual(refused["items"][0]["state"], "excluded")
        # here the work root is the tool's own root: its private
        # configuration is not a work source either
        private = seedsources.inspect({"sources": [
            {"root": "work", "path": "manager/local/AGENTS.md"}]})
        self.assertEqual(private["items"][0]["state"], "excluded")
        self.assertIsNone(private["items"][0]["sha256"])
        self.assertEqual(git_state(self.app), before)


class Vendored(_OneRepo):
    hosted = False


class SourceHosted(_OneRepo):
    hosted = True


class OverHttp(unittest.TestCase):
    """The real server, on a disposable board of its own."""

    def board(self) -> Board:
        def prepare(board):
            (board.work / "plans").mkdir(exist_ok=True)
            (board.work / "plans" / "roadmap.md").write_text("# Roadmap\n")
        return Board(self, prepare=prepare)

    def call(self, board, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(board.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def test_inventory_inspection_and_the_page_address(self):
        board = self.board()
        status, out = self.call(board, "GET", "/api/brain/seed")
        self.assertEqual(status, 200, out)
        seed = out["seed"]
        self.assertEqual(seed["v"], 1)
        self.assertFalse(seed["refused"], seed["problems"])
        self.assertEqual(seed["layout"], "external")
        self.assertEqual(seed["separation"], "independent")
        status, out = self.call(board, "POST", "/api/brain/seed/inspect", {
            "sources": [{"root": "work", "path": "plans/roadmap.md"}]})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["manifest"]["items"][0]["state"], "read")
        status, out = self.call(board, "POST", "/api/brain/seed/inspect", {"sources": []})
        self.assertEqual((status, out["code"]), (400, "nothing-selected"))
        with urllib.request.urlopen(board.url + "/brain/seed?from=setup", timeout=10) as res:
            self.assertEqual(res.status, 200)

    def test_a_foreign_page_cannot_inspect(self):
        board = self.board()
        status, _ = self.call(board, "POST", "/api/brain/seed/inspect", {
            "sources": [{"root": "work", "path": "plans/roadmap.md"}]},
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
