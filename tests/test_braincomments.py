"""Comments on selected Brain text and anchored agent requests (task 210).

- `Anchoring` — a selection mapped onto the entry's Markdown by letters and
  digits: rich Markdown, Unicode, repeated quotes refused or told apart by
  their context, never attached to a guess.
- `Locating` — where a passage is now: current, relocated only on a unique
  match with its context, outdated or detached otherwise; never re-anchored
  by itself.
- `Threads` — Post comment writes one record and launches nothing; replies
  never resolve; manual resolve and reopen keep attributed history; the
  record's revision guard, request identity, bounds and scrubbing.
- `Asking` — the Claude adapter's real `run` against the stub vendor on
  #175's split fixture: Ask agent starts exactly one drafting request with
  the passage and instruction, repeats find it, and settlement resolves a
  thread only when the applied change touched its passage; a conflict, a
  cancel or an unrelated edit keeps it open with its reason.
- `Committed` — with BOARD_COMMIT_MOVES on, each write is one scoped
  commit in the work-state repository holding no local path or secret.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "manager" / "core"))
sys.path.insert(0, str(ROOT))

import brain  # noqa: E402
import braincomments  # noqa: E402
import braindraft  # noqa: E402
import brainthreads  # noqa: E402
import brainwrite  # noqa: E402
import config  # noqa: E402
import httpd  # noqa: E402

from tests.test_braindraft import _Board, EXISTING, git  # noqa: E402

RICH = """Intro with **bold words** and a [linked phrase](https://example.com/x) here.

## Steps

1. Open the board.
2. Move a card.
3. Open the board.

## Notes

Café naïve — ünïcode stays: 日本語のテキスト.

```
code fence that says Open the board
```
"""


class Anchoring(unittest.TestCase):

    def test_rich_markdown_maps_to_its_source(self):
        a = braincomments.anchor(RICH, "r1", "bold words and a linked phrase")
        self.assertEqual(RICH[a["start"]:a["end"]], "bold words** and a [linked phrase")
        self.assertEqual(a["lineStart"], 0)
        self.assertEqual(a["section"], "Opening")

    def test_unicode_is_matched_exactly(self):
        a = braincomments.anchor(RICH, "r1", "ünïcode stays: 日本語")
        self.assertEqual(RICH[a["start"]:a["end"]], "ünïcode stays: 日本語")
        self.assertEqual(a["section"], "Notes")

    def test_a_repeated_quote_is_refused_until_its_context_tells_it_apart(self):
        # "Open the board" is in item 1, item 3 and the code fence
        with self.assertRaises(braincomments.Invalid) as caught:
            braincomments.anchor(RICH, "r1", "Open the board")
        self.assertEqual(caught.exception.extra["code"], "ambiguous")
        # what the page shows before item 3: the list's numbers are not text
        a = braincomments.anchor(RICH, "r1", "Open the board", prefix="Move a card.\n",
                                 suffix="")
        self.assertEqual(a["lineStart"], RICH.split("\n").index("3. Open the board."))
        # the ordered-list numbers are not text: the context matches without them
        b = braincomments.anchor(RICH, "r1", "Open the board", suffix="Move a card")
        self.assertEqual(b["lineStart"], RICH.split("\n").index("1. Open the board."))

    def test_text_not_in_the_source_and_too_little_text_are_refused(self):
        with self.assertRaises(braincomments.Invalid) as caught:
            braincomments.anchor(RICH, "r1", "Nothing like this")
        self.assertEqual(caught.exception.extra["code"], "no-match")
        with self.assertRaises(braincomments.Invalid) as caught:
            braincomments.anchor(RICH, "r1", " — ")
        self.assertEqual(caught.exception.extra["code"], "too-short")
        # a link's target is never selectable text
        with self.assertRaises(braincomments.Invalid):
            braincomments.anchor(RICH, "r1", "example com x")


class Locating(unittest.TestCase):

    def entry(self, body: str, revision: str = "r2", state: str = "entry") -> dict:
        return {"state": state, "body": body, "revision": revision}

    def anchored(self) -> dict:
        return braincomments.anchor(RICH, "r1", "Move a card")

    def test_the_same_revision_is_exact(self):
        a = self.anchored()
        where = braincomments.locate(a, self.entry(RICH, "r1"))
        self.assertEqual(where["state"], "current")
        self.assertEqual(where["start"], a["start"])

    def test_an_edit_elsewhere_and_a_section_move_relocate_exactly(self):
        a = self.anchored()
        later = "A new opening line.\n\n" + RICH
        where = braincomments.locate(a, self.entry(later))
        self.assertEqual(where["state"], "relocated")
        self.assertEqual(later[where["start"]:where["end"]], "Move a card")
        # the Steps section moved after Notes: its own text still places it
        steps = RICH[RICH.index("## Steps"):RICH.index("## Notes")]
        moved = RICH.replace(steps, "") + "\n" + steps
        where = braincomments.locate(a, self.entry(moved))
        self.assertEqual(where["state"], "relocated")
        self.assertEqual(where["section"], "Steps")

    def test_edited_deleted_and_repeated_passages_are_never_guessed(self):
        a = self.anchored()
        edited = RICH.replace("Move a card", "Drag a card")
        self.assertEqual(braincomments.locate(a, self.entry(edited))["state"], "detached")
        context = RICH.replace("2. Move a card.\n3. Open", "2. Move a card.\n3. Close")
        self.assertEqual(braincomments.locate(a, self.entry(context))["state"], "outdated")
        # the whole list repeated in another section: two exact matches
        doubled = RICH + "\n## Again\n\n1. Open the board.\n2. Move a card.\n3. Open the board.\n"
        where = braincomments.locate(a, self.entry(doubled))
        self.assertEqual(where["state"], "outdated")
        self.assertIn("2 times", where["why"])
        self.assertEqual(braincomments.locate(a, self.entry(RICH, state="tombstone"))["state"],
                         "detached")


class Threads(_Board):
    """The record, through the Brain's one writer, on #175's split fixture."""

    def comments(self) -> dict:
        return brainthreads.reading("e-exports")

    def post(self, op: str, **payload) -> dict:
        now = self.comments()
        body = {"request": self.key(), "entry": "e-exports", "expected": now["revision"],
                **payload}
        return brainthreads.write(op, body)

    def open_comment(self, text="Is this still true?", quote="written by the export module"):
        rev = brain.entry("e-exports")["revision"]
        return self.post("open", entryRevision=rev, quote=quote, text=text)

    def test_post_comment_persists_reloads_and_launches_nothing(self):
        before = self.entry_file.read_bytes()
        out = self.open_comment()
        self.assertTrue(out["operation"]["saved"])
        record = self.work / "brain" / "comments" / "e-exports.md"
        self.assertTrue(record.is_file())
        self.assertEqual(self.entry_file.read_bytes(), before)
        self.assertEqual(self.calls(), 0)
        self.assertEqual(state_agents(), [])
        # a fresh reading — a reload — reads the same attributed thread
        again = self.comments()
        self.assertEqual(len(again["threads"]), 1)
        t = again["threads"][0]
        self.assertEqual(t["state"], "open")
        self.assertEqual(t["mode"], "comment")
        self.assertEqual(t["location"]["state"], "current")
        self.assertTrue(t["opened"]["actor"])
        self.assertEqual(t["anchor"]["quote"], "written by the export module")
        # the Brain index lists the entry and does not read the record as a file
        index = brain.index()
        self.assertEqual([e["id"] for e in index["entries"]], ["e-exports"])
        self.assertFalse(index.get("refused"))

    def test_a_repeat_and_a_stale_page_write_once(self):
        rev = brain.entry("e-exports")["revision"]
        body = {"request": self.key(), "entry": "e-exports", "expected": "none",
                "entryRevision": rev, "quote": "export module", "text": "One"}
        brainthreads.write("open", dict(body))
        again = brainthreads.write("open", dict(body))
        self.assertTrue(again["operation"].get("duplicate"))
        self.assertEqual(len(self.comments()["threads"]), 1)
        # another page that read the record before the first write is refused
        with self.assertRaises(brainthreads.Refused) as caught:
            brainthreads.write("open", dict(body, request=self.key()))
        self.assertEqual(caught.exception.extra["code"], "comments-changed")
        self.assertEqual(len(self.comments()["threads"]), 1)
        # and a page that read the entry before a hand edit cannot anchor
        self.entry_file.write_text(EXISTING.replace("module.", "module, today."),
                                   encoding="utf-8")
        with self.assertRaises(brainthreads.Refused) as caught:
            self.post("open", entryRevision=rev, quote="export module", text="Two")
        self.assertEqual(caught.exception.extra["code"], "entry-changed")

    def test_replies_never_resolve_and_resolve_reopen_keep_history(self):
        self.open_comment()
        self.open_comment(text="And this one?", quote="Exports are written")
        first, second = [t["id"] for t in self.comments()["threads"]]
        self.post("reply", thread=first, text="I think it is.")
        self.assertEqual({t["state"] for t in self.comments()["threads"]}, {"open"})
        with self.assertRaises(brainthreads.Refused):
            self.post("resolve", thread=first)          # a resolution says why
        self.post("resolve", thread=first, text="Checked the code.")
        self.post("reopen", thread=first, text="The code changed again.")
        threads = {t["id"]: t for t in self.comments()["threads"]}
        self.assertEqual(threads[first]["state"], "open")
        self.assertEqual(threads[second]["state"], "open")
        kinds = [e["kind"] for e in threads[first]["history"]]
        self.assertEqual(kinds, ["open", "reply", "resolve", "reopen"])
        self.assertTrue(all(e.get("actor") for e in threads[first]["history"]))
        # a person's resolve cannot claim to be an applied edit
        self.post("resolve", thread=second, text="Fine.", by="edit", draft="bd-0000000001")
        res = {t["id"]: t for t in self.comments()["threads"]}[second]["resolution"]
        self.assertEqual(res["by"], "person")
        self.assertNotIn("draft", res)

    def test_the_record_is_bounded_scrubbed_and_inert(self):
        self.open_comment(text="key sk-ant-abcdefghijklmnopqrstuvwxyz0123 <script>x</script> "
                               "--> **Status:** Done")
        text = (self.work / "brain" / "comments" / "e-exports.md").read_text("utf-8")
        self.assertNotIn("sk-ant-abcdefghijklmnopqrstuvwxyz0123", text)
        self.assertNotIn("<script>", text)
        # only the board's own comment markers end with -->
        for line in text.splitlines():
            if "-->" in line:
                self.assertTrue(line.startswith("<!-- bench:brain-comment"), line)
        self.assertNotIn(str(self.tmp), text)
        with self.assertRaises(brainthreads.Refused):
            self.open_comment(text="x" * (braincomments.MAX_TEXT + 1))
        with self.assertRaises(brainthreads.Refused):
            self.post("open", entryRevision=brain.entry("e-exports")["revision"],
                      quote="export module", text="hi", body="replace the entry")

    def test_re_anchoring_is_explicit_and_held_to_the_entry_now(self):
        self.open_comment(quote="written by the export module")
        tid = self.comments()["threads"][0]["id"]
        self.entry_file.write_text(EXISTING.replace("written by the export module",
                                                    "produced by the exporter"),
                                   encoding="utf-8")
        t = self.comments()["threads"][0]
        self.assertEqual(t["location"]["state"], "detached")
        rev = brain.entry("e-exports")["revision"]
        self.post("reanchor", thread=tid, entryRevision=rev, quote="produced by the exporter")
        t = self.comments()["threads"][0]
        self.assertEqual(t["location"]["state"], "current")
        self.assertEqual(t["anchor"]["quote"], "produced by the exporter")
        self.assertEqual([e["kind"] for e in t["history"]], ["open", "reanchor"])

    def test_a_retired_entry_and_a_filed_review_are_not_annotated(self):
        rev = brain.entry("e-exports")["revision"]
        brainwrite.write("retire", {"request": self.key(), "id": "e-exports",
                                    "expected": rev, "reason": "replaced"})
        reading = self.comments()
        self.assertFalse(reading["commentable"])
        with self.assertRaises(brainthreads.Refused) as caught:
            self.post("open", entryRevision=brain.entry("e-exports")["revision"],
                      quote="export module", text="x")
        self.assertEqual(caught.exception.extra["code"], "not-commentable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with self.assertRaises(urllib.error.HTTPError) as got:
            urllib.request.urlopen(f"{base}/api/brain/comments?card=12&review=rv-0123456789",
                                   timeout=10)
        self.assertEqual(got.exception.code, 409)
        reply = json.loads(got.exception.read())
        self.assertEqual(reply["code"], "review-immutable")
        self.assertEqual(reply["card"], "/cards/12?tab=reviews&review=rv-0123456789")


def state_agents() -> list:
    import state
    return [a for a in state.AGENTS.values() if a.get("status") == "running"]


class Asking(Threads):
    """Ask agent through the drafting path, and settlement."""

    def ask(self, quote="written by the export module", text="Name the module exactly",
            request=None, **extra) -> dict:
        self.scenario("propose")
        rev = brain.entry("e-exports")["revision"]
        body = {"request": request or self.key(), "entry": "e-exports",
                "expected": self.comments()["revision"], "entryRevision": rev,
                "quote": quote, "text": text, **extra}
        return brainthreads.ask(body), body

    def test_ask_starts_one_request_with_the_passage_and_repeats_find_it(self):
        self.scenario("propose")
        out, body = self.ask()
        draft = self.settle(out["draft"])
        self.assertEqual(self.calls(), 1)
        self.assertIn("> written by the export module", draft["text"])
        self.assertIn("Name the module exactly", draft["text"])
        self.assertEqual(draft["target"], "e-exports")
        # a double click, a resend after a lost reply: the same request
        again = brainthreads.ask(dict(body))
        self.assertEqual(again["draft"], out["draft"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.calls(), 1)
        t = self.comments()["threads"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["mode"], "ask")
        self.assertEqual([lk["draft"] for lk in t[0]["links"]], [out["draft"]])
        self.assertEqual(t[0]["links"][0]["live"]["state"], "proposed")
        # the entry is unchanged until a guarded Apply
        self.assertEqual(self.entry_file.read_text("utf-8"), EXISTING)

    def test_an_applied_edit_of_the_passage_resolves_its_thread_only(self):
        self.open_comment(text="Unrelated note", quote="Exports are written")
        other = self.comments()["threads"][0]["id"]
        out, _ = self.ask()
        draft = self.settle(out["draft"])
        body = brain.entry("e-exports")["body"].replace(
            "written by the export module", "written by export.py")
        done = self.apply(draft, body=body)
        self.assertEqual(done["draft"]["status"], "applied")
        brainthreads.settle(out["draft"])
        threads = {t["id"]: t for t in self.comments()["threads"]}
        asked = threads[out["thread"]]
        self.assertEqual(asked["state"], "resolved")
        self.assertEqual(asked["resolution"]["by"], "edit")
        self.assertEqual(asked["resolution"]["draft"], out["draft"])
        self.assertTrue(asked["resolution"]["change"].startswith("ch-"))
        # the comment nobody asked about is untouched by the edit
        self.assertEqual(threads[other]["state"], "open")
        # settling again writes nothing more
        n = len(asked["history"])
        brainthreads.settle(out["draft"])
        again = {t["id"]: t for t in self.comments()["threads"]}[out["thread"]]
        self.assertEqual(len(again["history"]), n)
        # a person's reopen is not undone by a later settle or a restart
        self.post("reopen", thread=out["thread"], text="Not quite.")
        brainthreads.reconcile()
        self.assertEqual({t["id"]: t for t in self.comments()["threads"]}[out["thread"]]
                         ["state"], "open")

    def test_an_edit_elsewhere_keeps_the_thread_open_with_its_reason(self):
        self.entry_file.write_text(EXISTING.replace(
            "Exports are written by the export module.\n",
            "Exports are written by the export module.\n\n## Format\n\nComma separated.\n"),
            encoding="utf-8")
        out, _ = self.ask()
        draft = self.settle(out["draft"])
        body = brain.entry("e-exports")["body"].replace("Comma separated.",
                                                         "Semicolon separated.")
        self.apply(draft, body=body)
        brainthreads.settle(out["draft"])
        t = self.comments()["threads"][0]
        self.assertEqual(t["state"], "open")
        self.assertEqual(t["outcomes"][-1]["outcome"], "unrelated")

    def test_a_failed_apply_leaves_the_entry_and_the_thread_as_they_were(self):
        out, _ = self.ask()
        draft = self.settle(out["draft"])
        hand = EXISTING.replace("Exports are", "Today exports are")
        self.entry_file.write_text(hand, encoding="utf-8")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft)
        brainthreads.settle(out["draft"])
        self.assertEqual(self.entry_file.read_text("utf-8"), hand)
        t = self.comments()["threads"][0]
        self.assertEqual(t["state"], "open")
        self.assertEqual(t["outcomes"][-1]["outcome"], "conflict")

    def test_a_cancelled_request_keeps_the_thread_open(self):
        out, _ = self.ask()
        self.settle(out["draft"])
        braindraft.cancel({"id": out["draft"]})
        brainthreads.settle(out["draft"])
        brainthreads.settle(out["draft"])
        t = self.comments()["threads"][0]
        self.assertEqual(t["state"], "open")
        self.assertEqual([o["outcome"] for o in t["outcomes"]], ["cancelled"])

    def test_a_request_that_cannot_start_keeps_the_comment_and_says_why(self):
        self.addCleanup(setattr, config, "ADAPTER", config.ADAPTER)
        config.ADAPTER = "opencode"
        with self.assertRaises(brainthreads.Refused) as caught:
            self.ask()
        self.assertIn("thread", caught.exception.extra)
        t = self.comments()["threads"][0]
        self.assertEqual(t["outcomes"][-1]["outcome"], "not-launched")
        self.assertEqual(self.calls(), 0)

    def test_a_follow_up_is_linked_to_the_same_thread(self):
        out, _ = self.ask()
        self.settle(out["draft"])
        follow, _ = self.ask(thread=out["thread"], text="Also mention the BOM",
                             quote=None)
        self.assertEqual(follow["thread"], out["thread"])
        self.assertNotEqual(follow["draft"], out["draft"])
        t = self.comments()["threads"][0]
        self.assertEqual(len(t["links"]), 2)
        self.assertEqual([m["kind"] for m in t["messages"]], ["open", "reply"])


class Committed(Threads):
    """BOARD_COMMIT_MOVES on, in a work-state repository of its own."""

    def setUp(self):
        super().setUp()
        git(self.work, "init", "-q")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qm", "work")
        git(self.work, "config", "user.name", "reviewer")
        git(self.work, "config", "user.email", "r@example.invalid")
        for attr, value in (("COMMIT_MOVES", True), ("EXTERNAL", True)):
            self.addCleanup(setattr, config, attr, getattr(config, attr))
            setattr(config, attr, value)
        if config.state_repo() is None:
            self.skipTest("this layout resolves no work-state repository here")

    def test_each_write_is_one_scoped_commit(self):
        out = self.open_comment()
        self.assertEqual(out["operation"]["commit"]["state"], "committed",
                         out["operation"]["commit"])
        files = git(self.work, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(files, ["brain/comments/e-exports.md"])
        subject = git(self.work, "log", "-1", "--format=%s")
        self.assertTrue(subject.startswith("board: brain e-exports comment t-"), subject)
        shown = git(self.work, "show", "HEAD:brain/comments/e-exports.md")
        self.assertNotIn(str(self.tmp), shown)
        self.assertIn('"actor": "reviewer"', shown)


if __name__ == "__main__":
    unittest.main()


# keep subprocess imported for a reader extending the fixtures
_ = subprocess
