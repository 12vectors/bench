"""The Brain, written (task 158): create, edit, classify, override and
retire maintained entries — protected, revision-bound writes of one file
under `<work root>/brain/`, committed through the work state's own scoped
commit and published by sync.

Every case runs on a disposable split-Git layout: an application
repository and a separate work-state repository (with a bare remote where
sharing is the question), and config pointed at them in this process.

- **What is written.** A create allocates a stable id once and makes
  `brain/` only then; an edit keeps the id, the unknown header fields and
  the classification history; a classification appends one attributed
  line; Superseded needs a resolvable, non-looping replacement or an
  explicit invalidation; an override is set and cleared on the record; a
  retirement keeps a tombstone at its address.
- **What is refused, with nothing written.** A stale revision (another
  editor, an external or synced change) answers 409 with the current
  entry; markup, line breaks and control characters in one-line fields,
  the history heading or an unclosed fence in a body, a link for brain/,
  unknown targets and loops.
- **One operation per request.** A repeated request answers with the first
  result; a journalled intent left by a restart is finished rather than
  repeated; the same identity for another payload is refused.
- **Saved, committed, shared.** A failed commit leaves the bytes saved and
  says so, and a retry commits them; unrelated staged and untracked work
  is untouched; sync's remote holding the bytes reads shared, an
  unreachable remote reads failed; the application and the cards are
  byte-for-byte unchanged.
- **Over HTTP.** The write routes are behind the browser-mutation door.

    python3 -m unittest tests.test_brainwrite -v
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
import brainwrite  # noqa: E402
import config  # noqa: E402
import state  # noqa: E402
import sync  # noqa: E402

from tests.test_settings_security import Board  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check)


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


class _Layout(unittest.TestCase):
    """An application repository and a separate work-state repository."""

    commit_moves = True
    remote = False

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bench-brainwrite-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app, self.work = self.dir / "app", self.dir / "work"
        for repo in (self.app, self.work):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "ronald")
            git(repo, "config", "user.email", "r@example.com")
            git(repo, "config", "commit.gpgsign", "false")
        (self.app / "src.py").write_text("print('app')\n")
        (self.app / "AGENTS.md").write_text("# app rules\n")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-qm", "app")
        for stage, _ in config.STAGES:
            (self.work / "tasks" / stage).mkdir(parents=True)
            (self.work / "tasks" / stage / ".gitkeep").write_text("")
        (self.work / "tasks" / "archive").mkdir()
        (self.work / "tasks" / "review" / "07-a-card.md").write_text(
            "# 07 — A card\n\n**Status:** Review\n**Priority:** Low\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "work")
        if self.remote:
            self.bare = self.dir / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)],
                           check=True, capture_output=True)
            git(self.work, "remote", "add", "origin", str(self.bare))
            git(self.work, "push", "-q", "origin", "main")
            git(self.work, "fetch", "-q", "origin")
        patcher = mock.patch.multiple(
            config, TASKS=self.work / "tasks", REPO=self.app, EXTERNAL=True,
            STATE=self.dir / "state", COMMIT_MOVES=self.commit_moves,
            SYNC=self.remote, STATE_GIT_REMOTE="", STATE_BASE="main")
        patcher.start()
        self.addCleanup(patcher.stop)
        sync._NOTES.clear()
        self.addCleanup(sync._NOTES.clear)
        self.app_before = tree(self.app)
        self.cards_before = tree(self.work / "tasks")
        self.n = 0

    def tearDown(self):
        self.assertEqual(tree(self.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.work / "tasks"), self.cards_before, "a card changed")

    def req(self) -> str:
        self.n += 1
        return f"test-{self._testMethodName[-30:]}-{self.n}"

    def create(self, **over) -> dict:
        payload = {"request": self.req(), "title": "How reports are routed",
                   "kind": "Guide", "summary": "Where a report is filed.",
                   "sources": ["#148", "plans/148.md", "https://example.com/n"],
                   "body": "A report is filed on its own card.\n\n## Detail\n\nMore."}
        payload.update(over)
        return brainwrite.write("create", payload)

    def entry(self, entry_id: str) -> dict:
        return brain.entry(entry_id)

    def refused(self, kind: str, payload: dict, status: int = 400) -> brainwrite.Refused:
        before = tree(self.work)
        with self.assertRaises(brainwrite.Refused) as caught:
            brainwrite.write(kind, {"request": self.req(), **payload})
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        self.assertEqual(tree(self.work), before, "a refused write changed the work root")
        return caught.exception


class CreateAndEdit(_Layout):

    def test_create_makes_brain_and_one_valid_entry_committed_alone(self):
        self.assertFalse((self.work / "brain").exists())
        brain.index()                                  # reading never makes it
        self.assertFalse((self.work / "brain").exists())
        (self.work / "notes.txt").write_text("staged by a person\n")
        git(self.work, "add", "notes.txt")
        (self.work / "scratch.txt").write_text("untracked\n")
        out = self.create()
        self.assertTrue(out["saved"])
        self.assertEqual(out["commit"]["state"], "committed")
        self.assertTrue(brain.ID_RE.match(out["id"]))
        self.assertTrue(out["id"].startswith("e-how-reports-are-routed-"))
        e = self.entry(out["id"])
        self.assertEqual(e["state"], "entry")
        self.assertEqual(e["classification"], "Unclassified")
        self.assertEqual(e["title"], "How reports are routed")
        self.assertEqual([s["text"] for s in e["sources"]],
                         ["#148", "plans/148.md", "https://example.com/n"])
        self.assertEqual(len(e["history"]), 1)
        self.assertEqual(e["history"][0]["actor"], "ronald")
        self.assertEqual(e["warnings"], [], e["warnings"])
        files = git(self.work, "show", "--name-only", "--format=%s", "HEAD").stdout.split("\n")
        self.assertTrue(files[0].startswith(f"board: brain {out['id']} created (ronald)"))
        self.assertEqual([f for f in files[1:] if f], [f"brain/{out['id']}.md"])
        status = git(self.work, "status", "--porcelain").stdout
        self.assertIn("A  notes.txt", status, "the person's staged file is still staged")
        self.assertIn("?? scratch.txt", status)

    def test_an_edit_keeps_id_history_and_unknown_fields(self):
        out = self.create()
        path = self.work / "brain" / out["file"]
        text = path.read_text().replace("**Kind:** Guide",
                                        "**Kind:** Guide\n**Owner team:** docs")
        path.write_text(text)
        e = self.entry(out["id"])
        res = brainwrite.write("edit", {
            "request": self.req(), "id": out["id"], "expected": e["revision"],
            "title": "How review reports are routed", "kind": "Decision",
            "summary": "", "sources": "#148", "related": [],
            "body": "Rewritten body.\n\n```\n## Classification history\n```\n"})
        e2 = self.entry(out["id"])
        self.assertEqual(e2["id"], out["id"])
        self.assertEqual(e2["title"], "How review reports are routed")
        self.assertEqual(e2["entryKind"], "Decision")
        self.assertIsNone(e2["summary"])
        self.assertIn("```\n## Classification history\n```", e2["body"])
        self.assertEqual(e2["history"], e["history"])
        self.assertIn("**Owner team:** docs", path.read_text())
        self.assertEqual(res["revision"], e2["revision"])

    def test_a_stale_revision_is_a_conflict_carrying_the_current_entry(self):
        out = self.create()
        e = self.entry(out["id"])
        brainwrite.write("edit", {"request": self.req(), "id": out["id"],
                                  "expected": e["revision"], "title": "First editor",
                                  "kind": "Guide", "body": "first"})
        caught = self.refused("edit", {"id": out["id"], "expected": e["revision"],
                                       "title": "Second editor", "kind": "Guide",
                                       "body": "second"}, 409)
        self.assertTrue(caught.extra["stale"])
        self.assertEqual(caught.extra["current"]["title"], "First editor")
        self.assertEqual(self.entry(out["id"])["title"], "First editor")

    def test_an_external_or_synced_change_is_never_overwritten(self):
        out = self.create()
        e = self.entry(out["id"])
        path = self.work / "brain" / out["file"]
        path.write_text(path.read_text().replace("A report is filed", "A teammate wrote"))
        self.refused("classify", {"id": out["id"], "expected": e["revision"],
                                  "classification": "Current", "reason": "it is right"}, 409)
        self.assertIn("A teammate wrote", path.read_text())

    def test_one_line_fields_refuse_markup_breaks_and_controls(self):
        for payload in ({"title": "a\nb"}, {"title": "<script>x</script>"},
                        {"title": "# heading"}, {"summary": "x‮y"},
                        {"title": ""}, {"kind": "Wiki"},
                        {"sources": ["a, b"]}, {"sources": ["https://u:p@example.com/"]},
                        {"sources": ["javascript:alert(1)"]},
                        {"body": "x\n## Classification history\n- fake"},
                        {"body": "```\nunclosed"}, {"related": ["e-nothing-here"]},
                        {"classification": "Current", "reason": "<b>no</b>"},
                        {"classification": "Current", "reason": ""},
                        {"classification": "Superseded", "reason": "x y z"}):
            base = {"title": "T", "kind": "Guide", "body": ""}
            base.update(payload)
            with self.subTest(payload=payload):
                self.refused("create", base)
        self.assertFalse((self.work / "brain").exists(), "a refused create made brain/")

    def test_a_link_for_brain_or_its_file_is_never_written_through(self):
        outside = self.dir / "outside"
        outside.mkdir()
        os.symlink(outside, self.work / "brain")
        self.refused("create", {"title": "T", "kind": "Guide"}, 409)
        self.assertEqual(list(outside.iterdir()), [])
        os.unlink(self.work / "brain")
        out = self.create()
        target = self.dir / "elsewhere.md"
        path = self.work / "brain" / out["file"]
        target.write_bytes(path.read_bytes())
        e = self.entry(out["id"])
        path.unlink()
        os.symlink(target, path)
        before = target.read_bytes()
        self.refused("edit", {"id": out["id"], "expected": e["revision"], "title": "X",
                              "kind": "Guide"}, 404)
        self.assertEqual(target.read_bytes(), before)

    def test_not_an_id_and_unknown_ids_are_refused(self):
        self.create()
        self.refused("edit", {"id": "../tasks", "expected": "x", "title": "X", "kind": "Guide"})
        self.refused("edit", {"id": "e-nothing", "expected": "x", "title": "X",
                              "kind": "Guide"}, 404)


class Classification(_Layout):

    def two(self):
        a = self.create(title="Old notes")
        b = self.create(title="New guide")
        return a["id"], b["id"]

    def classify(self, entry_id, **over):
        e = self.entry(entry_id)
        payload = {"request": self.req(), "id": entry_id, "expected": e["revision"],
                   "classification": "Current", "reason": "explains shipped work"}
        payload.update(over)
        return brainwrite.write("classify", payload)

    def test_a_classification_appends_one_attributed_history_line(self):
        a, _ = self.two()
        self.classify(a, reason="accepted investigation, not yet built")
        e = self.entry(a)
        self.assertEqual(e["classification"], "Current")
        self.assertEqual([h["classification"] for h in e["history"]],
                         ["Unclassified", "Current"])
        self.assertEqual(e["history"][-1]["reason"], "accepted investigation, not yet built")
        self.assertEqual(e["history"][-1]["actor"], "ronald")
        self.classify(a, classification="Historical", reason="the fix shipped")
        self.assertEqual(len(self.entry(a)["history"]), 3)

    def test_superseded_needs_a_resolvable_replacement_or_an_invalidation(self):
        a, b = self.two()
        e = self.entry(a)
        base = {"id": a, "expected": e["revision"], "classification": "Superseded",
                "reason": "replaced"}
        self.refused("classify", base)
        self.refused("classify", {**base, "supersededBy": "e-missing-entry"})
        self.refused("classify", {**base, "supersededBy": a})
        self.refused("classify", {**base, "supersededBy": b, "invalidation": "both"})
        self.classify(a, classification="Superseded", supersededBy=b, reason="replaced")
        e = self.entry(a)
        self.assertEqual(e["classification"], "Superseded")
        self.assertEqual([x["id"] for x in e["supersededBy"]], [b])
        self.assertEqual(e["warnings"], [])
        # b superseded by a would loop
        eb = self.entry(b)
        caught = self.refused("classify", {"id": b, "expected": eb["revision"],
                                           "classification": "Superseded",
                                           "supersededBy": a, "reason": "loop"})
        self.assertIn("loop", str(caught))
        # back to Current clears the link
        self.classify(a, classification="Current", reason="reopened")
        self.assertEqual(self.entry(a)["supersededBy"], [])

    def test_an_invalidation_is_recorded_without_a_replacement(self):
        a, _ = self.two()
        self.classify(a, classification="Superseded", invalidation="the approach was dropped",
                      reason="no successor")
        e = self.entry(a)
        self.assertEqual(e["invalidated"], "the approach was dropped")
        self.assertEqual(e["warnings"], [])
        self.assertIn("invalidated without a replacement", e["history"][-1]["reason"])

    def test_an_override_is_set_and_cleared_on_the_record(self):
        a, _ = self.two()
        e = self.entry(a)
        brainwrite.write("override", {"request": self.req(), "id": a,
                                      "expected": e["revision"], "action": "set",
                                      "reason": "a person decided this"})
        e = self.entry(a)
        self.assertTrue(e["override"].startswith("manual · ronald · "))
        self.assertIn("manual override set", e["history"][-1]["reason"])
        self.assertTrue(next(x for x in brain.index()["entries"] if x["id"] == a)["override"])
        brainwrite.write("override", {"request": self.req(), "id": a,
                                      "expected": e["revision"], "action": "clear",
                                      "reason": "automation may assess it"})
        e = self.entry(a)
        self.assertIsNone(e["override"])
        self.assertEqual(len(e["history"]), 3)
        self.refused("override", {"id": a, "expected": e["revision"], "action": "clear",
                                  "reason": "nothing to clear"}, 409)

    def test_retirement_keeps_a_tombstone_at_its_address(self):
        a, b = self.two()
        e = self.entry(a)
        self.refused("retire", {"id": a, "expected": e["revision"], "reason": "x y z",
                                "replacedBy": a})
        brainwrite.write("retire", {"request": self.req(), "id": a, "expected": e["revision"],
                                    "reason": "folded into the guide", "replacedBy": b})
        e = self.entry(a)
        self.assertEqual(e["state"], "tombstone")
        self.assertEqual(e["removed"], "folded into the guide")
        self.assertEqual([x["id"] for x in e["replacedBy"]], [b])
        self.assertTrue((self.work / "brain" / f"{a}.md").exists())
        self.refused("edit", {"id": a, "expected": e["revision"], "title": "X",
                              "kind": "Guide"}, 409)
        eb = self.entry(b)
        self.refused("classify", {"id": b, "expected": eb["revision"],
                                  "classification": "Superseded", "supersededBy": a,
                                  "reason": "retired target"})


class Operations(_Layout):

    def test_a_repeated_request_answers_with_the_first_result(self):
        payload = {"request": "repeat-request-1", "title": "Once", "kind": "Guide"}
        first = brainwrite.write("create", dict(payload))
        again = brainwrite.write("create", dict(payload))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(list((self.work / "brain").glob("*.md"))), 1)
        log = git(self.work, "log", "--format=%s").stdout
        self.assertEqual(log.count("created"), 1)
        with self.assertRaises(brainwrite.Refused) as caught:
            brainwrite.write("create", {**payload, "title": "Different"})
        self.assertEqual(caught.exception.status, 409)

    def test_an_intent_left_by_a_restart_is_finished_not_repeated(self):
        # the intent was journalled and the process died before the write
        reading = brain.reading()
        plan = brainwrite._plan_create({"title": "Interrupted", "kind": "Guide"}, reading,
                                       "ronald")
        payload = {"request": "restart-request-1", "title": "Interrupted", "kind": "Guide"}
        digest = brainwrite._digest({k: v for k, v in payload.items() if k != "request"}
                                    | {"kind": "create"})
        brainwrite._journal_put("restart-request-1", {
            "kind": "create", "digest": digest, "status": "started", "at": "x",
            "id": plan["id"], "file": plan["file"], "expected": None,
            "target": brain.revision(plan["text"].encode()), "text": plan["text"],
            "what": "created", "actor": "ronald"})
        out = brainwrite.write("create", dict(payload))
        self.assertTrue(out["saved"])
        self.assertEqual(out["id"], plan["id"])
        # and once more, the bytes already there: nothing written twice
        brainwrite._journal_put("restart-request-1", {
            **brainwrite._journal()["restart-request-1"], "status": "started",
            "text": plan["text"]})
        again = brainwrite.write("create", dict(payload))
        self.assertTrue(again["saved"])
        self.assertEqual(len(list((self.work / "brain").glob("*.md"))), 1)
        self.assertEqual(len(brain.entry(plan["id"])["history"]), 1)

    def test_a_lost_reply_is_answered_from_the_record(self):
        out = self.create(request="lost-reply-req-1")
        found = brainwrite.operation("lost-reply-req-1")
        self.assertEqual(found["id"], out["id"])
        self.assertEqual(found["commit"]["state"], "committed")
        self.assertEqual(found["push"]["state"], "off")
        self.assertIsNone(brainwrite.operation("never-made-req-1"))

    def test_revisions_list_commits_and_saves(self):
        out = self.create()
        e = self.entry(out["id"])
        brainwrite.write("classify", {"request": self.req(), "id": out["id"],
                                      "expected": e["revision"], "classification": "Current",
                                      "reason": "explains it"})
        revs = brainwrite.revisions(out["id"])
        self.assertEqual(len(revs["commits"]), 2)
        self.assertIn("classified Current", revs["commits"][0]["subject"])
        self.assertEqual(len(revs["saves"]), 2)
        self.assertFalse(revs["uncommitted"])


class CommitFailure(_Layout):

    def test_a_failed_commit_keeps_the_saved_bytes_and_says_so(self):
        lock = self.work / ".git" / "index.lock"
        lock.write_text("")
        out = self.create(request="commit-fail-req-1")
        self.assertTrue(out["saved"])
        self.assertEqual(out["commit"]["state"], "failed")
        self.assertTrue(out["commit"]["detail"])          # git's own words, clipped
        self.assertEqual(out["push"]["state"], "not-committed")
        self.assertTrue((self.work / "brain" / out["file"]).exists())
        self.assertEqual(self.entry(out["id"])["state"], "entry")
        lock.unlink()
        again = brainwrite.retry({"request": "commit-fail-req-1", "action": "commit"})
        self.assertEqual(again["commit"]["state"], "committed")
        self.assertIn(f"brain/{out['file']}",
                      git(self.work, "show", "--name-only", "HEAD").stdout)


class CommitsOff(_Layout):
    commit_moves = False

    def test_saved_locally_is_said_as_not_committed(self):
        out = self.create()
        self.assertEqual(out["commit"]["state"], "off")
        self.assertIn(f"?? brain/", git(self.work, "status", "--porcelain").stdout)
        with self.assertRaises(brainwrite.Refused):
            brainwrite.retry({"request": "test-" + self._testMethodName[-30:] + "-1",
                              "action": "commit"})


class Shared(_Layout):
    remote = True

    def test_a_pushed_commit_reads_shared(self):
        out = self.create(request="shared-req-1")
        self.assertIn(out["push"]["state"], ("pending", "shared"))
        res = brainwrite.retry({"request": "shared-req-1", "action": "push"})
        self.assertEqual(res["push"]["state"], "shared", res)
        shown = git(self.work, "show", f"origin/main:brain/{out['file']}").stdout
        self.assertIn("How reports are routed", shown)

    def test_an_unreachable_remote_reads_failed_not_shared(self):
        out = self.create(request="offline-req-1")
        git(self.work, "remote", "set-url", "origin", str(self.dir / "gone.git"))
        res = brainwrite.retry({"request": "offline-req-1", "action": "push"})
        self.assertEqual(res["push"]["state"], "failed", res)
        self.assertEqual(res["commit"]["state"], "committed")
        self.assertTrue((self.work / "brain" / out["file"]).exists())


class OverHttp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        class _Cleanup:
            def __init__(self):
                self.fns = []

            def addCleanup(self, fn, *a):
                self.fns.append((fn, a))
        cls.cleanup = _Cleanup()
        cls.board = Board(cls.cleanup)

    @classmethod
    def tearDownClass(cls):
        for fn, a in reversed(cls.cleanup.fns):
            fn(*a)

    def post(self, path, body, headers=None):
        data = json.dumps(body).encode()
        request = urllib.request.Request(self.board.url + path, data=data, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=20) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_writes_are_behind_the_browser_mutation_door(self):
        body = {"request": "http-req-0001", "title": "Over HTTP", "kind": "Guide"}
        status, _ = self.post("/api/brain/create", body,
                              {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertFalse((self.board.work / "brain").exists())
        status, data = self.post("/api/brain/create", body)
        self.assertEqual(status, 200, data)
        entry_id = data["operation"]["id"]
        with urllib.request.urlopen(self.board.url + "/api/brain/operation?request=http-req-0001",
                                    timeout=20) as res:
            self.assertEqual(json.loads(res.read())["operation"]["id"], entry_id)
        status, data = self.post("/api/brain/edit", {"request": "http-req-0002", "id": entry_id,
                                                     "expected": "0" * 16, "title": "X",
                                                     "kind": "Guide"})
        self.assertEqual(status, 409)
        self.assertTrue(data["stale"])
        self.assertEqual(data["current"]["title"], "Over HTTP")


if __name__ == "__main__":
    unittest.main()
