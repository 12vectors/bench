"""Adopting legacy `reference/` material into the Brain (task 172).

Every case runs on the disposable split-Git layout `test_brainwrite` uses,
with a mixed `reference/`: Markdown, an image, a PDF, an HTML prototype and
sources that must be refused.

- **Inventory and preview are passive**: nothing under the work root moves,
  no git object or ref appears, and unsafe sources are said, not opened.
- **One file, one entry, stable identity**: catalogue and explanation are
  separate entries, both Unclassified, and the source's bytes never change.
- **Frozen at preview**: source drift, destination drift, a repeated request,
  a second request, a restart mid-write and a failed commit each end in one
  entry or none, with human text intact.
- **A changed source is shown, never merged**: refresh re-records the bytes
  and leaves the prose, classification and override alone.

    python3 -m unittest tests.test_adoption -v
"""

from __future__ import annotations

import hashlib
import json
import os
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

import adoption  # noqa: E402
import brain  # noqa: E402
import brainwrite  # noqa: E402
import config  # noqa: E402
import handbook  # noqa: E402
import taskfiles  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
HTML = b"<!doctype html><script>alert(1)</script><h1>Prototype</h1>\n"
GUIDE = "# The payments API\n\nCall it with a key.\n\n## Limits\n\nTen a second.\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Adoption(_Layout):

    def setUp(self):
        super().setUp()
        self.ref = self.work / "reference"
        self.ref.mkdir()
        (self.ref / "api.md").write_text(GUIDE)
        (self.ref / "screen.png").write_bytes(PNG)
        (self.ref / "spec.pdf").write_bytes(PDF)
        (self.ref / "proto.html").write_bytes(HTML)
        (self.work / "plans").mkdir()
        (self.work / "plans" / "p.md").write_text("see reference/api.md.\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "reference material")
        self.originals = {p.name: p.read_bytes() for p in self.ref.iterdir()}
        self.plans_before = tree(self.work / "plans")
        adoption._MEMO.clear()

    def tearDown(self):
        for name, data in self.originals.items():
            if (self.ref / name).exists() and name not in getattr(self, "edited", ()):
                self.assertEqual((self.ref / name).read_bytes(), data, name)
        self.assertEqual(tree(self.work / "plans"), self.plans_before)
        super().tearDown()

    def item(self, name: str) -> dict:
        return next(i for i in adoption.inventory()["items"] if i["name"] == name)

    def adopt(self, name="api.md", op="catalogue", **over) -> dict:
        data = (self.ref / name).read_bytes()
        payload = {"request": self.req(), "op": op, "name": name, "source": sha(data),
                   "expected": "none", "title": f"About {name}", "summary": "Adopted."}
        if op == "explanation":
            payload.update(kind="Guide", body="Our own account of the API.")
        payload.update(over)
        return brainwrite.write("adopt", payload)

    def refused(self, payload_or_kind="adopt", payload=None, status=409):  # noqa: D102
        kind, body = ("adopt", payload_or_kind) if isinstance(payload_or_kind, dict) \
            else (payload_or_kind, payload)
        return super().refused(kind, body, status)

    def base(self, name="api.md", op="catalogue", **over) -> dict:
        data = (self.ref / name).read_bytes()
        return {"op": op, "name": name, "source": sha(data), "expected": "none",
                "title": "T", **over}


class Inventory(_Adoption):

    def test_mixed_inventory_reads_actual_bytes_and_writes_nothing(self):
        before = tree(self.work)
        head = git(self.work, "rev-parse", "HEAD").stdout
        objects = git(self.work, "count-objects", "-v").stdout
        with mock.patch("subprocess.Popen", wraps=__import__("subprocess").Popen) as spawned:
            out = adoption.inventory()
            adoption.preview("api.md")
        for call in spawned.call_args_list:
            self.assertEqual(call.args[0][0], config.GIT if hasattr(config, "GIT") else "git")
        by = {i["name"]: i for i in out["items"]}
        self.assertEqual(by["api.md"]["type"], "markdown")
        self.assertEqual(by["screen.png"]["type"], "image")
        self.assertEqual(by["spec.pdf"]["type"], "pdf")
        self.assertEqual(by["proto.html"]["type"], "html")
        self.assertEqual(by["api.md"]["sha256"], sha(GUIDE.encode()))
        self.assertEqual(by["api.md"]["size"], len(GUIDE))
        self.assertEqual(by["api.md"]["address"], "/files/reference/api.md")
        self.assertEqual(by["api.md"]["owner"]["state"], "known")
        self.assertEqual(by["api.md"]["owner"]["name"], "ronald")
        self.assertEqual([h["name"] for h in by["api.md"]["inbound"]], ["p.md"])
        self.assertEqual(tree(self.work), before)
        self.assertEqual(git(self.work, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(git(self.work, "count-objects", "-v").stdout, objects)
        self.assertFalse((self.work / "brain").exists())

    def test_inbound_links_name_cards(self):
        card = self.work / "tasks" / "backlog" / "09-links.md"
        card.write_text("# 09 — Links\n\n**Status:** Backlog\n**Priority:** Low\n\n"
                        "[spec](../../reference/spec.pdf)\n")
        self.addCleanup(card.unlink)
        self.cards_before = tree(self.work / "tasks")
        holders = self.item("spec.pdf")["inbound"]
        self.assertEqual(holders[0]["kind"], "card")
        self.assertEqual(holders[0]["address"], "/cards/9")
        os.unlink(card)
        self.addCleanup(card.write_text, "")
        self.cards_before = tree(self.work / "tasks")

    def test_an_untracked_file_has_no_known_owner(self):
        (self.ref / "new.md").write_text("# New\n")
        self.originals["new.md"] = b"# New\n"
        self.assertEqual(self.item("new.md")["owner"]["state"], "unknown")

    def test_unsafe_sources_are_listed_and_refused(self):
        outside = self.dir / "private.md"
        outside.write_text("# secret plans\n")
        os.symlink(outside, self.ref / "escape.md")
        (self.ref / "deploy.env").write_text("X=1\n")
        (self.ref / "aws-secret.txt").write_text("nothing\n")
        (self.ref / "notes.md").write_text("api_key = sk-abcdefghijklmnopqrstuvwxyz123456\n")
        (self.ref / "fake.png").write_text("not an image")
        (self.ref / "bad.md").write_bytes(b"\xff\xfe not utf8")
        (self.ref / "with space.md").write_text("x")
        (self.ref / "folder").mkdir()
        self.edited = ("escape.md",)
        by = {i["name"]: i for i in adoption.inventory()["items"]}
        for name in ("escape.md", "deploy.env", "aws-secret.txt", "notes.md"):
            self.assertEqual(by[name]["state"], "excluded", name)
        for name in ("fake.png", "bad.md", "with space.md", "folder"):
            self.assertEqual(by[name]["state"], "unsupported", name)
        self.assertIsNone(by["escape.md"]["sha256"])
        self.assertIsNone(by["notes.md"]["sha256"], "a credential file's digest is withheld")
        for name in ("escape.md", "deploy.env", "notes.md", "fake.png", "bad.md"):
            data = b"x"
            with self.assertRaises(brainwrite.Refused) as caught:
                brainwrite.write("adopt", {"request": self.req(), "op": "catalogue", "name": name,
                                           "source": sha(data), "expected": "none", "title": "T"})
            self.assertEqual(caught.exception.extra.get("code"), "source-refused", name)
        self.assertFalse((self.work / "brain").exists())
        self.assertNotIn("secret plans", json.dumps(by))

    def test_a_linked_reference_directory_is_refused(self):
        moved = self.dir / "moved"
        os.rename(self.ref, moved)
        os.symlink(moved, self.ref)
        self.addCleanup(lambda: (os.unlink(self.ref), os.rename(moved, self.ref)))
        out = adoption.inventory()
        self.assertEqual(out["directory"]["state"], "refused")
        self.assertEqual(out["items"], [])
        self.assertEqual(adoption.preview("api.md")["choices"], [])

    def test_traversal_is_not_a_name(self):
        for bad in ("../x", "a/b", "", ".env"):
            with self.assertRaises(adoption.NotAName):
                adoption.preview(bad)

    def test_a_missing_directory_is_said(self):
        for p in self.ref.iterdir():
            p.unlink()
        self.ref.rmdir()
        self.addCleanup(self._restore)
        self.assertEqual(adoption.inventory()["directory"]["state"], "missing")

    def _restore(self):
        self.ref.mkdir(exist_ok=True)
        for name, data in self.originals.items():
            (self.ref / name).write_bytes(data)


class Preview(_Adoption):

    def test_markdown_offers_catalogue_by_default_and_a_seeded_explanation(self):
        out = adoption.preview("api.md")
        choices = {c["op"]: c for c in out["choices"]}
        self.assertTrue(choices["catalogue"]["default"])
        self.assertTrue(choices["catalogue"]["available"])
        self.assertFalse(choices["explanation"]["available"], "catalogue comes first")
        self.assertEqual(choices["catalogue"]["defaults"]["title"], "The payments API")
        self.assertEqual(choices["explanation"]["defaults"]["body"], GUIDE)
        self.assertEqual(out["sourceText"], GUIDE)

    def test_html_and_binary_are_never_converted(self):
        for name in ("proto.html", "screen.png", "spec.pdf"):
            out = adoption.preview(name)
            self.assertIsNone(out["sourceText"], name)
            explanation = next(c for c in out["choices"] if c["op"] == "explanation")
            self.assertEqual(explanation["defaults"]["body"], "", name)
            self.assertFalse(explanation["seeded"], name)

    def test_a_hostile_heading_is_not_reused_as_the_title(self):
        (self.ref / "h.md").write_text("# <img src=x onerror=alert(1)>\n")
        self.originals["h.md"] = (self.ref / "h.md").read_bytes()
        title = adoption.preview("h.md")["choices"][0]["defaults"]["title"]
        self.assertEqual(title, "h.md")


class Apply(_Adoption):

    def test_catalogue_creates_one_stable_unclassified_entry(self):
        out = self.adopt()
        self.assertEqual(out["id"], adoption.entry_id("catalogue", "api.md"))
        self.assertEqual(out["commit"]["state"], "committed")
        e = brain.entry(out["id"])
        self.assertEqual(e["entryKind"], "Attachment")
        self.assertEqual(e["classification"], "Unclassified")
        self.assertEqual(e["adopted"], {"name": "api.md", "sha256": sha(GUIDE.encode()),
                                        "role": "catalogue"})
        self.assertEqual(e["sources"][0]["address"], "/files/reference/api.md")
        self.assertTrue(e["sources"][0]["present"])
        self.assertEqual(e["bindings"][0]["target"], "reference/api.md")
        files = git(self.work, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(files, [f"brain/{out['id']}.md"])
        self.assertEqual(self.item("api.md")["state"], "catalogued")

    def test_binary_and_prototype_are_catalogued_with_bytes_untouched(self):
        for name in ("screen.png", "spec.pdf", "proto.html"):
            out = self.adopt(name)
            self.assertEqual((self.ref / name).read_bytes(), self.originals[name])
            self.assertNotIn("alert(1)", brain.entry(out["id"])["body"])

    def test_explanation_needs_the_catalogue_and_names_it(self):
        e = self.refused(self.base(op="explanation", kind="Guide", body="Mine."))
        self.assertEqual(e.extra["code"], "catalogue-first")
        catalogue = self.adopt()["id"]
        out = self.adopt(op="explanation")
        self.assertNotEqual(out["id"], catalogue)
        entry = brain.entry(out["id"])
        self.assertEqual(entry["classification"], "Unclassified")
        self.assertEqual(entry["adopted"]["role"], "explanation")
        self.assertEqual([r["id"] for r in entry["related"]], [catalogue])
        about = adoption.about(brain.reading()["entries"][0])
        self.assertEqual(about["counterpart"]["role"] in ("catalogue", "explanation"), True)

    def test_the_explanation_cannot_be_empty_or_of_another_kind(self):
        self.adopt()
        self.refused(self.base(op="explanation", kind="Guide", body="  "), status=400)
        self.refused(self.base(op="explanation", kind="Attachment", body="x"), status=400)

    def test_an_unknown_field_is_refused(self):
        self.refused(self.base(classification="Current"), status=400)

    def test_a_person_classifies_it_afterwards(self):
        out = self.adopt()
        e = brain.entry(out["id"])
        brainwrite.write("classify", {"request": self.req(), "id": out["id"],
                                      "expected": e["revision"], "classification": "Historical",
                                      "reason": "an old export, kept for the record"})
        self.assertEqual(brain.entry(out["id"])["classification"], "Historical")

    def test_a_handbook_section_can_link_it_by_membership(self):
        out = self.adopt()
        handbook._MEMO.clear()
        brainwrite.write("handbook", {"request": self.req(), "expected": "none",
                                      "section": "operations", "entries": [out["id"]],
                                      "reason": "the runbook export"})
        section = next(s for s in handbook.reading()["sections"] if s["id"] == "operations")
        self.assertEqual(section["items"][0]["id"], out["id"])
        self.assertNotEqual(section["coverage"]["state"], "covered",
                            "an Unclassified adoption is not operative coverage")

    def test_every_other_file_is_unchanged(self):
        before = tree(self.work)
        out = self.adopt()
        after = tree(self.work)
        added = set(after) - set(before)
        self.assertEqual(added, {f"brain/{out['id']}.md"})
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)


class Guards(_Adoption):

    def test_source_drift_is_refused(self):
        payload = self.base()
        (self.ref / "api.md").write_text(GUIDE + "changed\n")
        self.edited = ("api.md",)
        e = self.refused(payload)
        self.assertEqual(e.extra["code"], "source-drift")
        self.assertTrue(e.extra["stale"])
        self.assertFalse((self.work / "brain").exists())

    def test_double_submit_is_one_entry(self):
        payload = {"request": "double-submit-1", **self.base()}
        first = brainwrite.write("adopt", dict(payload))
        again = brainwrite.write("adopt", dict(payload))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(list((self.work / "brain").glob("*.md"))), 1)
        log = git(self.work, "log", "--format=%s", "--", "brain").stdout.strip().split("\n")
        self.assertEqual(len(log), 1)

    def test_a_second_request_never_overwrites(self):
        first = self.adopt()
        path = self.work / "brain" / f"{first['id']}.md"
        before = path.read_bytes()
        e = self.refused(self.base())
        self.assertEqual(e.extra["code"], "already-adopted")
        self.assertEqual(e.extra["existing"], first["id"])
        self.assertEqual(path.read_bytes(), before)

    def test_a_restart_mid_write_finishes_the_same_entry(self):
        payload = {"request": "restart-mid-write", **self.base()}
        with mock.patch.object(brainwrite, "_finish", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                brainwrite.write("adopt", dict(payload))
        self.assertFalse(list((self.work / "brain").glob("a-*.md"))
                         if (self.work / "brain").exists() else [])
        out = brainwrite.write("adopt", dict(payload))
        self.assertTrue(out["saved"])
        self.assertEqual(len(list((self.work / "brain").glob("*.md"))), 1)

    def test_a_lost_reply_is_answered_from_the_record(self):
        payload = {"request": "lost-reply-1", **self.base()}
        first = brainwrite.write("adopt", dict(payload))
        found = brainwrite.operation("lost-reply-1")
        self.assertEqual(found["id"], first["id"])
        self.assertTrue(found["saved"])

    def test_a_failed_commit_keeps_the_entry_and_retries_once(self):
        lock = Path(git(self.work, "rev-parse", "--git-path", "index.lock").stdout.strip())
        lock = lock if lock.is_absolute() else self.work / lock
        lock.write_text("")
        try:
            out = brainwrite.write("adopt", {"request": "commit-fails-1", **self.base()})
        finally:
            lock.unlink()
        self.assertTrue(out["saved"])
        self.assertEqual(out["commit"]["state"], "failed")
        self.assertTrue((self.work / "brain" / f"{out['id']}.md").exists())
        again = brainwrite.retry({"request": "commit-fails-1", "action": "commit"})
        self.assertEqual(again["commit"]["state"], "committed")
        log = git(self.work, "log", "--format=%s", "--", "brain").stdout.strip().split("\n")
        self.assertEqual(len(log), 1)

    def test_a_hand_written_file_at_the_id_is_never_replaced(self):
        target = adoption.entry_id("catalogue", "api.md")
        (self.work / "brain").mkdir()
        path = self.work / "brain" / f"{target}.md"
        path.write_text("a person's own notes, not an entry\n")
        e = self.refused(self.base())
        self.assertEqual(e.extra["code"], "already-adopted")
        self.assertEqual(path.read_text(), "a person's own notes, not an entry\n")

    def test_a_retired_catalogue_is_not_reused(self):
        out = self.adopt()
        e = brain.entry(out["id"])
        brainwrite.write("retire", {"request": self.req(), "id": out["id"],
                                    "expected": e["revision"], "reason": "no longer wanted"})
        self.assertEqual(self.item("api.md")["state"], "retired")
        self.assertEqual(adoption.preview("api.md")["choices"], [])
        refused = self.refused(self.base())
        self.assertEqual(refused.extra["code"], "already-adopted")


class Changed(_Adoption):

    def changed(self) -> tuple[str, bytes]:
        out = self.adopt()
        new = (GUIDE + "\n## Added\n\nNew words.\n").encode()
        (self.ref / "api.md").write_bytes(new)
        self.edited = ("api.md",)
        return out["id"], new

    def test_a_changed_source_is_shown_with_what_it_was(self):
        entry_id, _ = self.changed()
        self.assertEqual(self.item("api.md")["state"], "changed")
        out = adoption.preview("api.md")
        refresh = next(c for c in out["choices"] if c["op"] == "refresh")
        self.assertEqual(refresh["destination"]["id"], entry_id)
        self.assertEqual(out["previousText"], GUIDE)
        self.assertIn("## Added", out["sourceText"])
        self.assertEqual(adoption.about(brain.reading()["entries"][0])["state"], "changed")

    def test_refresh_keeps_a_persons_text_classification_and_override(self):
        entry_id, new = self.changed()
        e = brain.entry(entry_id)
        brainwrite.write("edit", {"request": self.req(), "id": entry_id, "expected": e["revision"],
                                  "title": "Renamed by a person", "kind": "Attachment",
                                  "summary": "Mine.", "sources": ["reference/api.md"],
                                  "body": "A person's own words."})
        e = brain.entry(entry_id)
        brainwrite.write("classify", {"request": self.req(), "id": entry_id,
                                      "expected": e["revision"], "classification": "Historical",
                                      "reason": "kept for the record"})
        e = brain.entry(entry_id)
        brainwrite.write("override", {"request": self.req(), "id": entry_id,
                                      "expected": e["revision"], "action": "set",
                                      "reason": "a person decided"})
        e = brain.entry(entry_id)
        git(self.work, "add", "reference/api.md")
        git(self.work, "commit", "-qm", "the file changed")
        brainwrite.write("adopt", {"request": self.req(), "op": "refresh", "name": "api.md",
                                   "source": sha(new), "expected": e["revision"],
                                   "reason": "read the new version"})
        after = brain.entry(entry_id)
        self.assertEqual(after["title"], "Renamed by a person")
        self.assertEqual(after["body"], "A person's own words.")
        self.assertEqual(after["classification"], "Historical")
        self.assertIsNotNone(after["override"])
        self.assertEqual(after["adopted"]["sha256"], sha(new))
        self.assertEqual(len(after["history"]), len(e["history"]) + 1)
        self.assertEqual(len([b for b in after["bindings"]
                              if b["target"] == "reference/api.md"]), 1)
        head = git(self.work, "log", "-1", "--format=%H", "--", "reference/api.md").stdout.strip()
        self.assertEqual(after["bindings"][0]["revision"], head)
        self.assertEqual(self.item("api.md")["state"], "catalogued")

    def test_refresh_is_held_to_the_entry_revision(self):
        entry_id, new = self.changed()
        stale = brain.entry(entry_id)["revision"]
        e = brain.entry(entry_id)
        brainwrite.write("classify", {"request": self.req(), "id": entry_id,
                                      "expected": e["revision"], "classification": "Current",
                                      "reason": "assessed meanwhile"})
        before = (self.work / "brain" / f"{entry_id}.md").read_bytes()
        refused = self.refused({"op": "refresh", "name": "api.md", "source": sha(new),
                                "expected": stale, "reason": "read it"})
        self.assertEqual(refused.extra["code"], "destination-changed")
        self.assertEqual((self.work / "brain" / f"{entry_id}.md").read_bytes(), before)

    def test_an_uncommitted_change_is_not_bound_to_a_commit(self):
        entry_id, new = self.changed()
        e = brain.entry(entry_id)
        brainwrite.write("adopt", {"request": self.req(), "op": "refresh", "name": "api.md",
                                   "source": sha(new), "expected": e["revision"],
                                   "reason": "read the new version"})
        self.assertEqual(brain.entry(entry_id)["bindings"], [])

    def test_an_explanation_waits_for_the_new_bytes(self):
        self.changed()
        choice = next(c for c in adoption.preview("api.md")["choices"] if c["op"] == "explanation")
        self.assertFalse(choice["available"])
        refused = self.refused(self.base(op="explanation", kind="Guide", body="Mine."))
        self.assertEqual(refused.extra["code"], "catalogue-stale")


class OverHttp(unittest.TestCase):
    """The real server, on a disposable board of its own."""

    def board(self) -> Board:
        def seed(board):
            (board.work / "reference").mkdir()
            (board.work / "reference" / "api.md").write_text(GUIDE)
            (board.work / "reference" / "screen.png").write_bytes(PNG)
        return Board(self, prepare=seed)

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

    def test_the_whole_journey_and_the_old_address_keeps_serving(self):
        board = self.board()
        status, out = self.call(board, "GET", "/api/brain/adoption")
        self.assertEqual(status, 200)
        self.assertEqual(len(out["adoption"]["items"]), 2)
        status, out = self.call(board, "GET", "/api/brain/adoption/preview?name=api.md")
        self.assertEqual(status, 200)
        preview = out["preview"]
        status, out = self.call(board, "POST", "/api/brain/adoption/apply", {
            "request": "http-adopt-1", "op": "catalogue", "name": "api.md",
            "source": preview["sha256"], "expected": "none", "title": "The API"})
        self.assertEqual(status, 200, out)
        entry_id = out["operation"]["id"]
        status, out = self.call(board, "GET", f"/api/brain/entry?id={entry_id}")
        self.assertEqual(out["entry"]["adoption"]["state"], "unchanged")
        with urllib.request.urlopen(board.url + "/files/reference/api.md", timeout=10) as res:
            self.assertEqual(res.read(), GUIDE.encode())

    def test_a_foreign_page_cannot_adopt(self):
        board = self.board()
        status, _ = self.call(board, "POST", "/api/brain/adoption/apply", {
            "request": "http-foreign-1", "op": "catalogue", "name": "api.md",
            "source": sha(GUIDE.encode()), "expected": "none", "title": "T"},
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertFalse((board.work / "brain").exists())

    def test_a_bad_name_is_400(self):
        board = self.board()
        status, out = self.call(board, "GET", "/api/brain/adoption/preview?name=..%2Fx")
        self.assertEqual(status, 400)
        self.assertEqual(out["code"], "not-a-name")


if __name__ == "__main__":
    unittest.main()
