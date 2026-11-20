"""The Brain, read (task 157): the version 1 entry contract, the index and
the single-entry resolver, and what reading them must never do.

- **The contract.** A valid entry's metadata and body come from the same
  bytes, named by a revision that is a digest of those bytes — so an
  uncommitted edit is a new revision and git is never asked. Identity is
  the `**Id:**` line: renaming the file, retitling and reclassifying keep
  the id and its address. A tombstone is told apart from an id nothing
  carries; two files claiming one id are both refused; an invalid,
  unversioned or other-version file is a bounded diagnostic, never a crash
  and never interpreted.
- **Containment.** A traversal is not an id; a link — the directory itself
  or a file in it — is never followed; an oversized file or a FIFO is
  refused; a name that is not flat is not read.
- **Passivity.** A missing `brain/` is a state and is not created; reading
  writes nothing, starts no process and opens no socket.
- **Over HTTP.** `/api/brain`, `/api/brain/entry`, the page at `/brain`
  and `/brain/e/<id>`, and the old `/files/reference/` route unchanged.
- **Three layouts.** Vendored, source-hosted and external installs each
  read the `brain/` of their own work root and no other, and reinstalling
  leaves an existing `brain/` byte for byte.

    python3 -m unittest tests.test_brain -v
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))

import brain  # noqa: E402
import config  # noqa: E402

from tests.test_settings_security import Board  # noqa: E402


def entry_text(entry_id="e-report-routing", *, title="How review reports stay on their own board",
               kind="Guide", klass="Current", extra="", body="It explains the **routing**.\n",
               history="- 2026-11-20 · Current · ronald — explains the shipped routing\n") -> str:
    return (f"# {title}\n\n**Brain:** 1\n**Id:** {entry_id}\n**Kind:** {kind}\n"
            f"**Classification:** {klass}\n**Summary:** What the routing does.\n"
            f"{extra}\n{body}\n## Classification history\n\n{history}")


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.is_symlink(), hashlib.sha256(
                p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else "")
            for p in sorted(root.rglob("*")) if ".git" not in p.parts}


class _WorkRoot(unittest.TestCase):
    """A work root of this test's own, with config pointed at it."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-brain-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.work = self.dir / "work"
        for stage, _ in config.STAGES:
            (self.work / "tasks" / stage).mkdir(parents=True)
        (self.work / "tasks" / "archive").mkdir()
        patcher = mock.patch.object(config, "TASKS", self.work / "tasks")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.brain = self.work / "brain"

    def write(self, name: str, text: str | bytes) -> Path:
        self.brain.mkdir(exist_ok=True)
        path = self.brain / name
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return path


class TheContract(_WorkRoot):

    def test_a_valid_entry_reads_whole_from_one_set_of_bytes(self):
        path = self.write("routing.md", entry_text(
            extra="**Source:** #148, plans/x.md, https://example.com/a\n"
                  "**Related:** e-report-isolation\n"))
        self.write("isolation.md", entry_text("e-report-isolation",
                                              title="Why reports leaked",
                                              kind="Investigation", klass="Historical",
                                              history=""))
        found = brain.entry("e-report-routing")
        self.assertEqual(found["state"], "entry")
        self.assertEqual(found["title"], "How review reports stay on their own board")
        self.assertEqual((found["entryKind"], found["classification"]), ("Guide", "Current"))
        self.assertEqual(found["revision"],
                         hashlib.sha256(path.read_bytes()).hexdigest()[:16])
        self.assertIn("It explains the **routing**.", found["body"])
        self.assertNotIn("Classification history", found["body"],
                         "the history is metadata, not body")
        self.assertEqual(found["history"], [{"at": "2026-11-20", "classification": "Current",
                                             "actor": "ronald",
                                             "reason": "explains the shipped routing"}])
        self.assertEqual(found["related"], [{"id": "e-report-isolation",
                                             "title": "Why reports leaked", "present": True}])
        kinds = [(s["kind"], s["present"]) for s in found["sources"]]
        self.assertEqual(kinds, [("card", False), ("file", False), ("web", None)])
        self.assertEqual(brain.entry("e-report-isolation")["linkedFrom"],
                         [{"id": "e-report-routing",
                           "title": "How review reports stay on their own board"}])

    def test_an_edit_is_a_new_revision_without_a_commit(self):
        path = self.write("routing.md", entry_text())
        before = brain.index()
        path.write_text(entry_text(body="Changed.\n"), encoding="utf-8")
        after = brain.index()
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertNotEqual(before["entries"][0]["revision"], after["entries"][0]["revision"])
        self.assertIn("Changed.", brain.entry("e-report-routing")["body"])

    def test_rename_retitle_and_reclassify_keep_the_identity(self):
        path = self.write("routing.md", entry_text())
        path.rename(self.brain / "renamed-file.md")
        (self.brain / "renamed-file.md").write_text(entry_text(
            title="A new title", klass="Historical",
            history="- 2026-11-20 · Current · ronald — first\n"
                    "- 2026-11-21 · Historical · ronald — shipped\n"), encoding="utf-8")
        found = brain.entry("e-report-routing")
        self.assertEqual((found["state"], found["title"], found["classification"],
                          found["file"]),
                         ("entry", "A new title", "Historical", "renamed-file.md"))
        self.assertEqual(found["warnings"], [])

    def test_a_tombstone_is_not_an_unknown_id(self):
        self.write("old.md", entry_text("e-old", extra="**Removed:** folded into e-new\n"
                                                       "**Replaced by:** e-new\n"))
        self.write("new.md", entry_text("e-new", title="The replacement"))
        gone = brain.entry("e-old")
        self.assertEqual(gone["state"], "tombstone")
        self.assertEqual(gone["removed"], "folded into e-new")
        self.assertEqual(gone["replacedBy"], [{"id": "e-new", "title": "The replacement",
                                               "present": True}])
        self.assertEqual(brain.entry("e-never-was")["state"], "unknown")
        listed = {e["id"]: e["tombstone"] for e in brain.index()["entries"]}
        self.assertEqual(listed, {"e-old": True, "e-new": False})

    def test_two_files_claiming_one_id_are_both_refused(self):
        self.write("a.md", entry_text("e-same", title="A"))
        self.write("b.md", entry_text("e-same", title="B"))
        found = brain.entry("e-same")
        self.assertEqual(found["state"], "duplicate")
        self.assertEqual(sorted(found["files"]), ["a.md", "b.md"])
        index = brain.index()
        self.assertEqual(index["entries"], [])
        self.assertEqual([d["code"] for d in index["diagnostics"]], ["duplicate-id"])

    def test_invalid_records_are_diagnosed_not_shown(self):
        cases = {
            "no-id.md": entry_text("").replace("**Id:** \n", ""),
            "bad-kind.md": entry_text("e-bad-kind", kind="Wiki"),
            "bad-class.md": entry_text("e-bad-class", klass="Archived"),
            "twice.md": entry_text("e-twice", extra="**Kind:** Guide\n"),
            "untitled.md": entry_text("e-untitled").split("\n", 2)[2],
            "bad-id.md": entry_text("E/../x"),
        }
        for name, text in cases.items():
            self.write(name, text)
        self.write("binary.md", b"\xff\xfe\x00not text")
        index = brain.index()
        self.assertEqual(index["entries"], [])
        codes = {d["file"]: d["code"] for d in index["diagnostics"]}
        self.assertEqual(codes, {name: "invalid" for name in [*cases, "binary.md"]})
        self.assertEqual(brain.entry("e-bad-kind")["state"], "invalid")
        self.assertIn("Wiki", brain.entry("e-bad-kind")["problems"][0])

    def test_other_versions_and_unversioned_files_are_not_interpreted(self):
        self.write("v2.md", entry_text("e-future").replace("**Brain:** 1", "**Brain:** 2"))
        self.write("notes.md", "# Some notes\n\nNo contract here.\n")
        index = brain.index()
        self.assertEqual(index["entries"], [])
        codes = {d["file"]: d["code"] for d in index["diagnostics"]}
        self.assertEqual(codes, {"v2.md": "unsupported", "notes.md": "unversioned"})
        future = brain.entry("e-future")
        self.assertEqual(future["state"], "unsupported")
        self.assertNotIn("body", future)

    def test_missing_targets_cycles_and_history_disagreements_are_warnings(self):
        self.write("a.md", entry_text("e-a", klass="Superseded",
                                      extra="**Superseded by:** e-b\n**Related:** e-ghost\n"))
        self.write("b.md", entry_text("e-b", klass="Superseded",
                                      extra="**Superseded by:** e-a\n"))
        warnings = " | ".join(brain.entry("e-a")["warnings"])
        self.assertIn("e-ghost, which is not an entry", warnings)
        self.assertIn("loops back", warnings)
        self.assertIn("newest classification history line says Current", warnings)

    def test_unknown_fields_and_unreadable_history_lines_are_noted(self):
        self.write("a.md", entry_text("e-a", extra="**Colour:** blue\n",
                                      history="- yesterday it was fine\n"))
        warnings = " | ".join(brain.entry("e-a")["warnings"])
        self.assertIn("**Colour:** is not a version 1 field", warnings)
        self.assertIn("could not be read", warnings)

    def test_the_header_ends_at_its_first_blank_line(self):
        body = ("**Note:** read this first.\n\n**Kind:** Decision\n\n"
                "**Removed:** not really\n\nThe rest.\n")
        self.write("a.md", entry_text("e-a", body=body))
        found = brain.entry("e-a")
        self.assertEqual((found["state"], found["entryKind"]), ("entry", "Guide"))
        self.assertEqual(found["warnings"], [])
        for line in ("**Note:** read this first.", "**Kind:** Decision",
                     "**Removed:** not really", "The rest."):
            self.assertIn(line, found["body"])
        # a blank line inside the header ends it: what follows is body
        split = entry_text("e-b").replace("**Kind:** Guide\n", "\n**Kind:** Guide\n")
        self.write("b.md", split)
        self.assertEqual(brain.entry("e-b")["state"], "invalid")
        self.assertIn("**Kind:**", brain.entry("e-b")["problems"][0])

    def test_a_history_heading_in_a_fence_or_before_another_section_is_body(self):
        quoted = ("The format, as an example:\n\n```markdown\n## Classification history\n\n"
                  "- 2026-01-01 · Historical · someone — an example\n```\n\nAfter the example.\n")
        self.write("a.md", entry_text("e-a", body=quoted))
        found = brain.entry("e-a")
        self.assertEqual([h["actor"] for h in found["history"]], ["ronald"])
        self.assertIn("- 2026-01-01 · Historical · someone — an example\n```", found["body"])
        self.assertIn("After the example.", found["body"])
        # ~~~ fences count too, and a history heading followed by another
        # section is not the history: the whole body is kept
        tilde = entry_text("e-b", body="~~~\n## Classification history\n~~~\n",
                           history="- 2026-11-20 · Current · ronald — x\n\n## Later\n\nKept.\n")
        self.write("b.md", tilde)
        found = brain.entry("e-b")
        self.assertEqual(found["history"], [])
        self.assertIn("## Later", found["body"])
        self.assertIn("Kept.", found["body"])

    def test_a_byte_order_mark_is_not_part_of_the_title(self):
        self.write("a.md", "﻿" + entry_text("e-a"))
        found = brain.entry("e-a")
        self.assertEqual((found["state"], found["title"]),
                         ("entry", "How review reports stay on their own board"))

    def test_a_present_card_and_file_are_said_to_be_present(self):
        (self.work / "tasks" / "done" / "148-a-card.md").write_text("# A\n")
        (self.work / "plans").mkdir()
        (self.work / "plans" / "x.md").write_text("plan\n")
        self.write("a.md", entry_text("e-a", extra="**Source:** #0148, plans/x.md, "
                                                   "javascript:alert(1), reference/../x\n"))
        sources = brain.entry("e-a")["sources"]
        self.assertEqual([(s["kind"], s["present"], s["address"]) for s in sources],
                         [("card", True, "/cards/148"), ("file", True, "/files/plans/x.md"),
                          ("text", None, None), ("text", None, None)])

    def test_invisible_characters_never_reach_a_reader(self):
        self.write("a.md", entry_text("e-a", title="Safe‮txt.exe"))
        self.assertEqual(brain.entry("e-a")["title"], "Safetxt.exe")


class Containment(_WorkRoot):

    def test_a_traversal_is_not_an_id(self):
        for bad in ("../tasks", "e/../../x", "..", "E-UPPER", "a", "x" * 65, "",
                    "e.md", "e%2f", None):
            with self.assertRaises(brain.NotAnId, msg=repr(bad)):
                brain.entry(bad)

    def test_a_linked_file_is_never_followed(self):
        outside = self.dir / "secret.md"
        outside.write_text(entry_text("e-secret"), encoding="utf-8")
        self.brain.mkdir()
        (self.brain / "link.md").symlink_to(outside)
        index = brain.index()
        self.assertEqual(index["entries"], [])
        self.assertEqual([(d["file"], d["code"]) for d in index["diagnostics"]],
                         [("link.md", "refused")])
        self.assertEqual(brain.entry("e-secret")["state"], "unknown")

    def test_a_linked_directory_is_never_followed(self):
        outside = self.dir / "elsewhere"
        outside.mkdir()
        (outside / "a.md").write_text(entry_text("e-a"), encoding="utf-8")
        self.brain.symlink_to(outside)
        index = brain.index()
        self.assertEqual(index["directory"]["state"], "refused")
        self.assertIn("link", index["directory"]["why"])
        self.assertEqual(brain.entry("e-a")["state"], "unknown")

    def test_oversized_files_fifos_and_odd_names_are_refused(self):
        self.write("big.md", entry_text("e-big", body="x" * (brain.MAX_ENTRY_BYTES + 1)))
        os.mkfifo(self.brain / "pipe.md")
        self.write("has space.md", entry_text("e-space"))
        (self.brain / "sub.md").mkdir()
        index = brain.index()
        self.assertEqual(index["entries"], [])
        self.assertEqual(sorted(d["file"] for d in index["diagnostics"]),
                         ["big.md", "has space.md", "pipe.md", "sub.md"])

    def test_hidden_and_other_files_are_skipped_not_read(self):
        self.write(".hidden.md", entry_text("e-hidden"))
        self.write("picture.png", b"\x89PNG")
        index = brain.index()
        self.assertEqual((index["entries"], index["skipped"], index["diagnostics"]),
                         ([], 1, []))


class Passivity(_WorkRoot):

    def test_a_missing_brain_is_a_state_and_is_not_created(self):
        index = brain.index()
        self.assertEqual(index["directory"]["state"], "missing")
        self.assertEqual((index["entries"], index["revision"]), ([], "none"))
        self.assertEqual(brain.entry("e-a")["state"], "unknown")
        self.assertFalse(self.brain.exists())

    def test_reading_writes_nothing_starts_nothing_and_opens_no_socket(self):
        self.write("a.md", entry_text("e-a", extra="**Source:** #1, https://example.com\n"))
        self.write("b.md", entry_text("e-b", klass="Historical"))
        before = snapshot(self.dir)

        def refuse(*a, **k):
            raise AssertionError("a passive read reached for a process or a socket")

        with mock.patch.object(subprocess, "Popen", side_effect=refuse), \
                mock.patch.object(subprocess, "run", side_effect=refuse), \
                mock.patch.object(socket, "socket", side_effect=refuse), \
                mock.patch.object(os, "system", side_effect=refuse):
            brain.index()
            brain.entry("e-a")
            brain.entry("e-unknown")
        self.assertEqual(snapshot(self.dir), before)


# ── over HTTP ──────────────────────────────────────────────────────────


class _ClassCleanup:
    def __init__(self, cls):
        self.addCleanup = cls.addClassCleanup


def fetch(board: Board, path: str) -> tuple[int, dict, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(board.url + path), timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class OverHttp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        def prepare(board):
            (board.work / "brain").mkdir()
            (board.work / "brain" / "a.md").write_text(entry_text("e-a"), encoding="utf-8")
            (board.work / "brain" / "gone.md").write_text(
                entry_text("e-gone", extra="**Removed:** no longer true\n"), encoding="utf-8")
            (board.work / "reference").mkdir()
            (board.work / "reference" / "spec.md").write_text("# Spec\n", encoding="utf-8")
        cls.board = Board(_ClassCleanup(cls), prepare=prepare)

    def test_the_index_and_an_entry(self):
        status, data = self.board.get("/api/brain")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(e["id"] for e in data["brain"]["entries"]), ["e-a", "e-gone"])
        status, data = self.board.get("/api/brain/entry?id=e-a")
        self.assertEqual((status, data["entry"]["state"]), (200, "entry"))
        status, data = self.board.get("/api/brain/entry?id=e-gone")
        self.assertEqual((status, data["entry"]["state"]), (200, "tombstone"))

    def test_unknown_and_malformed_ids(self):
        status, data = self.board.get("/api/brain/entry?id=e-nope")
        self.assertEqual((status, data["entry"]["state"]), (404, "unknown"))
        for bad in ("../tasks", "%2e%2e%2ftasks", ""):
            status, data = self.board.get("/api/brain/entry?id=" + bad)
            self.assertEqual((status, data.get("code")), (400, "not-an-id"), bad)

    def test_the_page_addresses(self):
        for path in ("/brain", "/brain/e/e-a", "/brain/e/e-never-was"):
            status, headers, body = fetch(self.board, path)
            self.assertEqual(status, 200, path)
            self.assertIn("script-src 'self'", headers.get("Content-Security-Policy", ""))
            self.assertIn(b"main.js", body)
        for path in ("/brain/", "/brain/e", "/brain/e/", "/brain/e/..", "/brain/x/e-a",
                     "/brain/e/e-a/more", "/brain/e/E-A", "/brains"):
            self.assertEqual(fetch(self.board, path)[0], 404, path)

    def test_the_old_reference_route_is_unchanged(self):
        status, headers, body = fetch(self.board, "/files/reference/spec.md")
        self.assertEqual((status, body), (200, b"# Spec\n"))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(fetch(self.board, "/files/brain/a.md")[0], 404)

    def test_reading_over_http_changes_nothing_in_the_work_state(self):
        before = snapshot(self.board.work)
        for path in ("/api/brain", "/api/brain/entry?id=e-a", "/brain/e/e-a"):
            fetch(self.board, path)
        self.assertEqual(snapshot(self.board.work), before)


class ARouteForUiPaths(unittest.TestCase):

    def test_brain_addresses(self):
        import routes
        for path in ("/brain", "/brain/e/e-a", "/brain/e/0abc"):
            self.assertTrue(routes.ui_path(path), path)
        for path in ("/brain/", "/brain/e", "/brain/e/", "/brain/e/a.b", "/brain/e/A",
                     "/brain/q/e-a", "/brain/e/e-a/x"):
            self.assertFalse(routes.ui_path(path), path)


# ── three layouts ──────────────────────────────────────────────────────


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith(("BOARD_", "BENCH_"))}


class ThreeLayouts(unittest.TestCase):
    """Each layout reads the `brain/` of the work root it resolves to, and
    a decoy `brain/` anywhere else is never read. A second install leaves
    an existing `brain/` exactly as it was, and an install that finds none
    makes none."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="bench-brain-layouts-")).resolve()
        cls.tarball = cls.scratch / "bench.tar.gz"
        result = subprocess.run(["bash", str(REPO / "release.sh"), "--tarball",
                                 str(cls.tarball), "--source", "example/bench"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"release.sh failed:\n{result.stdout}{result.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def run_ok(self, *args, cwd=None):
        result = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd,
                                env={**_clean_env(), "GIT_AUTHOR_NAME": "t",
                                     "GIT_AUTHOR_EMAIL": "t@example.invalid",
                                     "GIT_COMMITTER_NAME": "t",
                                     "GIT_COMMITTER_EMAIL": "t@example.invalid"},
                                stdin=subprocess.DEVNULL)
        return result

    def unpack(self, into: Path) -> None:
        into.mkdir(parents=True)
        subprocess.run(["tar", "-xzf", str(self.tarball), "-C", str(into)], check=True)

    def read(self, core: Path) -> dict:
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import brain; "
                "print(json.dumps({'index': brain.index(), 'entry': brain.entry('e-here')}))")
        result = subprocess.run([sys.executable, "-c", code, str(core)], capture_output=True,
                                text=True, cwd=str(core.parent.parent), env=_clean_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def check(self, core: Path, work: Path, decoys: list[Path], install: list[str],
              cwd: Path) -> None:
        self.assertFalse((work / "brain").exists(), "an install makes no brain/")
        for decoy in decoys:
            (decoy / "brain").mkdir(parents=True, exist_ok=True)
            (decoy / "brain" / "decoy.md").write_text(entry_text("e-decoy"), encoding="utf-8")
        empty = self.read(core)
        self.assertEqual(empty["index"]["directory"]["state"], "missing")
        (work / "brain").mkdir()
        (work / "brain" / "here.md").write_text(entry_text("e-here", title="Here"),
                                                encoding="utf-8")
        (work / "brain" / "custom-notes.txt").write_text("mine\n", encoding="utf-8")
        found = self.read(core)
        self.assertEqual([e["id"] for e in found["index"]["entries"]], ["e-here"])
        self.assertEqual(found["entry"]["title"], "Here")
        before = snapshot(work / "brain")
        result = self.run_ok(sys.executable, *install, cwd=str(cwd))
        self.assertEqual(snapshot(work / "brain"), before,
                         "reinstalling must leave brain/ byte for byte: "
                         + result.stdout + result.stderr)

    def test_vendored(self):
        tmp = Path(tempfile.mkdtemp(dir=self.scratch))
        host = tmp / "host"
        self.run_ok("git", "init", "-q", "-b", "main", str(host))
        (host / ".claude").mkdir()
        tm = host / ".task-manager"
        self.unpack(tm)
        self.run_ok(sys.executable, str(tm / "install.py"), cwd=str(host))
        self.assertTrue((tm / "tasks").is_dir())
        self.check(tm / "manager" / "core", tm, [host], [str(tm / "install.py")], host)

    def test_source(self):
        tmp = Path(tempfile.mkdtemp(dir=self.scratch))
        checkout = tmp / "bench"
        self.unpack(checkout)
        self.run_ok("git", "init", "-q", "-b", "main", str(checkout))
        self.run_ok("git", "-C", str(checkout), "add", "-A")
        self.run_ok("git", "-C", str(checkout), "commit", "-q", "-m", "bench")
        (checkout / "tasks").mkdir(exist_ok=True)
        self.check(checkout / "manager" / "core", checkout, [tmp],
                   [str(checkout / "install.py")], checkout)

    def test_external(self):
        tmp = Path(tempfile.mkdtemp(dir=self.scratch))
        tool, app, work = tmp / "tool", tmp / "app", tmp / "work"
        self.unpack(tool)
        self.run_ok("git", "init", "-q", "-b", "main", str(app))
        flags = [str(tool / "install.py"), "--app", str(app), "--work", str(work)]
        result = self.run_ok(sys.executable, *flags, cwd=str(tmp))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.check(tool / "manager" / "core", work, [tool, app], flags, tmp)


if __name__ == "__main__":
    unittest.main()
