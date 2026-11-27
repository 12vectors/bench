"""Exact Brain changes (task 209): a proposal reviewed against the exact
revision it was drafted from, and a retained, read-only record of what
its Apply changed — navigable to the passage and honest once the entry
moves on.

Two halves:

- `brainchange`, pure: hunks with their sections, insertions,
  deletion-only edits, renamed and moved sections, a heading inside a
  code fence, bounds, and `locate` — exact while the entry is the written
  revision, relocated only on a unique match of the change and its
  context, unresolved for repeated or edited text;
- the Brain Agent's draft on #175's disposable split layout through the
  Claude adapter's real `run` with a stub vendor: the review's base and
  diff, the record an Apply keeps, a later human edit, a retirement, a
  lost reply settled at restart, a conflict that records nothing, a new
  entry, the read route, and the write's saved/committed states.

    python3 -m unittest tests.test_brainchange -v
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import brain  # noqa: E402
import brainchange  # noqa: E402
import braindraft  # noqa: E402
import brainwrite  # noqa: E402
import httpd  # noqa: E402
import state  # noqa: E402

import tests.test_braindraft as drafting  # noqa: E402
from tests.test_braindraft import _Board, EXISTING  # noqa: E402


BASE = """Opening paragraph.

## Install

Run the installer.

## Use

Open the board.
Move a card.

## Recover

Restart the board.
"""


class Diff(unittest.TestCase):
    """The pure difference, hunk by hunk."""

    def test_an_insertion_names_its_section_and_place(self):
        after = BASE.replace("Move a card.\n", "Move a card.\nArchive a card.\n")
        d = brainchange.diff(BASE, after)
        self.assertEqual(len(d["hunks"]), 1)
        h = d["hunks"][0]
        self.assertEqual((h["kind"], h["added"], h["removed"]), ("insert", 1, 0))
        self.assertEqual(h["section"], "Use")
        self.assertEqual(d["sections"], ["Use"])
        self.assertEqual(brainchange.lines_of(after)[h["afterAt"]], "Archive a card.")
        self.assertIn(["+", "Archive a card."], h["lines"])
        self.assertTrue(h["contextBefore"] and h["contextAfter"])

    def test_a_deletion_only_change_keeps_its_place(self):
        after = BASE.replace("Move a card.\n", "")
        d = brainchange.diff(BASE, after)
        h = d["hunks"][0]
        self.assertEqual((h["kind"], h["added"], h["removed"]), ("delete", 0, 1))
        rec = {"exact": True, "resultRevision": "r1", "body": d}
        where = brainchange.locate(rec, after, current_revision="r1")[0]
        self.assertEqual(where["state"], "current")
        self.assertEqual(where["start"], where["end"])
        # the removed line was right after "Open the board."
        self.assertEqual(brainchange.lines_of(after)[where["start"] - 1], "Open the board.")

    def test_a_renamed_heading_and_a_moved_section_are_said(self):
        renamed = BASE.replace("## Recover", "## Recovery")
        d = brainchange.diff(BASE, renamed)
        self.assertEqual(d["renamed"], [{"from": "Recover", "to": "Recovery"}])
        moved = ("Opening paragraph.\n\n## Recover\n\nRestart the board.\n\n## Install\n\n"
                 "Run the installer.\n\n## Use\n\nOpen the board.\nMove a card.\n")
        d = brainchange.diff(BASE, moved)
        kinds = sorted(h["kind"] for h in d["hunks"])
        self.assertIn("insert", kinds)
        self.assertIn("delete", kinds)
        self.assertEqual(d["added"], d["removed"])

    def test_a_heading_inside_a_fence_is_code_not_a_section(self):
        body = "Intro.\n\n```\n## not a heading\nx = 1\n```\n\nAfter.\n"
        after = body.replace("x = 1", "x = 2")
        h = brainchange.diff(body, after)["hunks"][0]
        self.assertEqual(h["section"], "Opening")

    def test_a_new_entry_is_one_insertion(self):
        d = brainchange.diff("", "# not a header\n\nBody.")
        self.assertEqual([h["kind"] for h in d["hunks"]], ["insert"])
        self.assertEqual(d["removed"], 0)

    def test_it_is_bounded_and_says_so(self):
        before = "\n".join(f"line {i}" for i in range(2000))
        after = "\n".join(f"line {i}" + (" changed" if i % 10 == 0 else "")
                          for i in range(2000))
        d = brainchange.diff(before, after)
        self.assertEqual(len(d["hunks"]), brainchange.MAX_HUNKS)
        self.assertTrue(d["truncated"])
        self.assertGreater(d["omittedHunks"], 0)

    def test_the_browser_journeys_fixture_is_four_separate_changes(self):
        # verify/browser_cases/case_brain_changes.py navigates these by number
        before = ("Releases are cut from main by whoever is on duty.\n\n## Prepare\n\n"
                  "- update the changelog\n- check the version\n- tag the commit\n\n## Publish\n\n"
                  "Check that CI is green.\n\nConfirm the version.\n\nUpload the tarball.\n\n"
                  "## Announce\n\nPost in the channel.\n\nPost in the channel.")
        after = ("## Prepare\n\n- update the changelog\n- check the version\n- tag the commit\n"
                 "- ship the build notes\n\n## Publish\n\nCheck that CI is green.\n\nConfirm the version.\n\n"
                 "Run release.sh, which uploads the tarball.\n\n## Announce\n\n"
                 "Post in the channel.\n\nPost in the channel.\n\n## Sources and claims\n\n- x")
        d = brainchange.diff(before, after)
        got = [(h["kind"], h["section"]) for h in d["hunks"]]
        self.assertEqual(got, [("delete", "Opening"), ("insert", "Prepare"),
                               ("replace", "Publish"), ("insert", "Sources and claims")])

    def test_header_fields_are_compared_apart(self):
        got = brainchange.fields({"title": "A", "summary": "s", "kind": "Guide"},
                                 {"title": "B", "summary": "s", "kind": "Guide"})
        self.assertEqual(got, [{"field": "title", "before": "A", "after": "B"}])

    def test_sources_and_related_are_compared_as_lists(self):
        base = {"title": "A", "summary": "", "kind": "Guide",
                "sources": ["#12"], "related": ["e-a"]}
        got = brainchange.fields(base, {**base, "sources": ["#12", "plans/x.md"],
                                        "related": ["e-a"]})
        self.assertEqual(got, [{"field": "sources", "before": "#12",
                                "after": "#12, plans/x.md"}])
        # a base retained before these were compared says nothing about them
        old = {"title": "A", "summary": "", "kind": "Guide"}
        self.assertEqual(brainchange.fields(old, {**old, "sources": ["#1"], "related": ["e-b"]}), [])
        # a new entry lists what it starts with
        new = brainchange.fields(None, {"title": "A", "summary": "", "kind": "Guide",
                                        "sources": ["#1"], "related": []})
        self.assertIn({"field": "sources", "before": None, "after": "#1"}, new)


class Locate(unittest.TestCase):
    """Where a recorded change is now: exact, relocated or unresolved —
    never unrelated text."""

    def record(self, before: str, after: str) -> dict:
        return brainchange.record(draft="bd-0000000001", round_no=1, request="r",
                                  entry_id="e-x", file="e-x.md",
                                  base={"revision": "b", "title": "", "summary": "",
                                        "kind": "", "body": before},
                                  result={"revision": "a", "title": "", "summary": "",
                                          "kind": "", "body": after}, at="t")

    def test_a_later_edit_elsewhere_relocates_the_change_exactly(self):
        after = BASE.replace("Move a card.\n", "Move a card.\nArchive a card.\n")
        rec = self.record(BASE, after)
        later = "A new first line.\n\n" + after
        where = brainchange.locate(rec, later, current_revision="c")[0]
        self.assertEqual(where["state"], "relocated")
        self.assertEqual(brainchange.lines_of(later)[where["start"]], "Archive a card.")

    def test_an_edit_of_the_changed_passage_is_unresolved(self):
        after = BASE.replace("Move a card.\n", "Move a card.\nArchive a card.\n")
        rec = self.record(BASE, after)
        later = after.replace("Archive a card.", "Archive two cards.")
        where = brainchange.locate(rec, later, current_revision="c")[0]
        self.assertEqual(where["state"], "unresolved")
        self.assertNotIn("start", where)

    def test_repeated_text_is_never_guessed_at(self):
        before = "## A\n\nSame.\n\n## B\n\nSame.\n"
        after = "## A\n\nSame.\nNew.\n\n## B\n\nSame.\n"
        rec = self.record(before, after)
        # the change and its context now occur twice: no place is claimed
        later = after + "\n" + after
        where = brainchange.locate(rec, later, current_revision="c")[0]
        self.assertEqual(where["state"], "unresolved")
        self.assertIn("more than once", where["why"])
        # where its context still tells it apart, it is placed exactly
        apart = "## A\n\nSame.\nNew.\n\n## B\n\nSame.\nNew.\n\n## C\n"
        where = brainchange.locate(rec, apart, current_revision="c")[0]
        self.assertEqual(where["state"], "relocated")
        self.assertEqual(where["start"], 3)

    TWO_EDITS_BEFORE = "## A\n\nFirst para.\n\nMiddle para.\n\nLast para.\n\n## B\n\nTail."
    TWO_EDITS_AFTER = ("## A\n\nFirst para, edited.\n\nMiddle para.\n\nLast para, edited.\n"
                       "Last para, extra line.\n\n## B\n\nTail.")

    def marked(self, text: str, where: dict) -> list[str]:
        lines = brainchange.lines_of(text)
        return [x for r in where["ranges"] for x in lines[r["start"]:r["end"]]]

    def test_two_nearby_edits_in_one_hunk_mark_exactly_the_edited_lines(self):
        # one unchanged paragraph between two edits: difflib groups them into
        # one hunk, and only the edits — never the paragraph between — are marked
        rec = self.record(self.TWO_EDITS_BEFORE, self.TWO_EDITS_AFTER)
        self.assertEqual(len(rec["body"]["hunks"]), 1)
        self.assertEqual(len(rec["body"]["hunks"][0]["edits"]), 2)
        where = brainchange.locate(rec, self.TWO_EDITS_AFTER, current_revision="a")[0]
        self.assertEqual(where["state"], "current")
        self.assertEqual(self.marked(self.TWO_EDITS_AFTER, where),
                         ["First para, edited.", "Last para, edited.", "Last para, extra line."])
        # after an edit elsewhere the same two edits are found again, and only they
        later = "A new opening line.\n\n" + self.TWO_EDITS_AFTER
        where = brainchange.locate(rec, later, current_revision="c")[0]
        self.assertEqual(where["state"], "relocated")
        self.assertEqual(self.marked(later, where),
                         ["First para, edited.", "Last para, edited.", "Last para, extra line."])
        # an edit of the unchanged paragraph between them unplaces the change
        moved = self.TWO_EDITS_AFTER.replace("Middle para.", "Middle para, rewritten.")
        where = brainchange.locate(rec, moved, current_revision="c")[0]
        self.assertEqual(where["state"], "unresolved")

    def test_a_record_without_edits_is_read_as_one_edit(self):
        after = BASE.replace("Move a card.\n", "Move a card.\nArchive a card.\n")
        rec = self.record(BASE, after)
        for h in rec["body"]["hunks"]:
            h.pop("edits")
            h.pop("afterSpan")
        where = brainchange.locate(rec, after, current_revision="a")[0]
        self.assertEqual(self.marked(after, where), ["Archive a card."])

    def test_a_record_without_a_retained_base_claims_no_diff(self):
        rec = brainchange.record(draft="bd-1", round_no=1, request="r", entry_id="e-x",
                                 file="e-x.md", base=None,
                                 result={"revision": "a", "body": "x"}, at="t")
        self.assertFalse(rec["exact"])
        self.assertIsNone(rec["body"])
        self.assertEqual(brainchange.locate(rec, "x", current_revision="a"), [])


SECTIONED = EXISTING.replace(
    "Exports are written by the export module.\n",
    "Exports are written by the export module.\n\n## Format\n\nComma separated.\n\n"
    "## Encoding\n\nUTF-8.\n")


class Applied(_Board):
    """The Brain Agent's draft: reviewed against its base, recorded at
    Apply, and read honestly afterwards."""

    def setUp(self):
        super().setUp()
        self.entry_file.write_text(SECTIONED, encoding="utf-8")

    def drafted(self) -> dict:
        before = brain.entry("e-exports")
        draft = self.start("propose", target="e-exports", expected=before["revision"])
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        return draft

    def edited_body(self) -> str:
        body = brain.entry("e-exports")["body"]
        return (body.replace("Comma separated.", "Semicolon separated.")
                .replace("## Encoding\n\nUTF-8.", "## Encoding\n\nUTF-8 with a BOM.\n\n"
                         "## Delivery\n\nWritten to the export folder."))

    def test_the_review_shows_the_exact_base_and_its_changes(self):
        draft = self.drafted()
        changes = draft["changes"]
        base_rev = brain.entry("e-exports")["revision"]
        self.assertEqual(changes["baseRevision"], base_rev)
        self.assertEqual(changes["base"]["revision"], base_rev)
        self.assertIn("## Format", changes["base"]["body"])
        self.assertTrue(changes["body"]["hunks"])
        self.assertIn("title", [f["field"] for f in changes["fields"]])
        # the Source and Related lists an update writes are compared too
        base = changes["base"]
        self.assertIn("sources", base)
        self.assertIn("related", base)
        p = draft["proposal"]
        named = [f["field"] for f in changes["fields"]]
        self.assertEqual("sources" in named, list(p["sources"]) != base["sources"])
        self.assertEqual("related" in named, list(p["related"]) != base["related"])
        # the listing stays light: no diff in it
        listed = braindraft.listing()["drafts"][0]
        self.assertNotIn("changes", listed)
        # a hand edit after drafting: the base shown is still what was read
        self.entry_file.write_text(SECTIONED.replace("UTF-8.", "UTF-16."), encoding="utf-8")
        again = braindraft.get(draft["id"])["changes"]
        self.assertEqual(again["baseRevision"], base_rev)
        self.assertNotEqual(again["currentRevision"], base_rev)

    def test_apply_keeps_a_record_that_leads_to_the_first_change(self):
        draft = self.drafted()
        base_rev = brain.entry("e-exports")["revision"]
        done = self.apply(draft, body=self.edited_body(), title="How exports are written")
        self.assertEqual(done["draft"]["status"], "applied")
        summary = done["draft"]["change"]
        self.assertTrue(summary["exact"])
        self.assertEqual(summary["baseRevision"], base_rev)
        self.assertEqual(summary["entry"], "e-exports")
        view = braindraft.change_view(draft["id"])
        self.assertEqual(view["state"], "current")
        rec = view["change"]
        self.assertEqual(rec["resultRevision"], brain.entry("e-exports")["revision"])
        self.assertEqual(rec["request"], done["draft"]["applied"]["request"])
        self.assertEqual(rec["round"], draft["proposalRound"])
        self.assertIn("Format", rec["body"]["sections"])
        self.assertIn("Encoding", rec["body"]["sections"])
        lines = brainchange.lines_of(brain.entry("e-exports")["body"])
        first = view["locations"][0]
        self.assertEqual(first["state"], "current")
        self.assertEqual(lines[first["start"]], "Semicolon separated.")
        self.assertTrue(all(loc["state"] == "current" for loc in view["locations"]))
        # every marked line is exactly a line the change added — never the
        # unchanged lines between two edits in one hunk
        hunks = {h["n"]: h for h in rec["body"]["hunks"]}
        for loc in view["locations"]:
            marked = [x for r in loc["ranges"] for x in lines[r["start"]:r["end"]]]
            self.assertEqual(marked, hunks[loc["n"]]["addedLines"])
        self.assertTrue(view["operation"]["saved"])
        self.assertEqual(view["operation"]["commit"]["state"], "off")

    def test_a_later_human_edit_reads_changed_since_and_never_highlights_other_text(self):
        draft = self.drafted()
        self.apply(draft, body=self.edited_body())
        now = brain.entry("e-exports")
        # a person's later edit of the changed passage, through the writer
        brainwrite.write("edit", {
            "request": self.key(), "id": "e-exports", "expected": now["revision"],
            "title": now["title"], "kind": now["entryKind"], "summary": now["summary"],
            "sources": [s["text"] for s in now["sources"]], "related": [],
            "body": now["body"].replace("Semicolon separated.", "Tab separated.")})
        view = braindraft.change_view(draft["id"])
        self.assertEqual(view["state"], "changed-since")
        states = {loc["n"]: loc["state"] for loc in view["locations"]}
        self.assertEqual(states[1], "unresolved")
        later = brainchange.lines_of(brain.entry("e-exports")["body"])
        for loc in view["locations"]:
            if loc["state"] == "relocated":
                self.assertNotIn("Tab separated.", later[loc["start"]:loc["end"]])
        # the historical diff is still the record's, byte for byte
        self.assertIn(["+", "Semicolon separated."], view["change"]["body"]["hunks"][0]["lines"])

    def test_a_retired_entry_is_said_and_nothing_is_located(self):
        draft = self.drafted()
        self.apply(draft, body=self.edited_body())
        now = brain.entry("e-exports")
        brainwrite.write("retire", {"request": self.key(), "id": "e-exports",
                                    "expected": now["revision"], "reason": "superseded"})
        view = braindraft.change_view(draft["id"])
        self.assertEqual(view["state"], "retired")
        self.assertEqual(view["locations"], [])
        self.assertTrue(view["change"]["body"]["hunks"])

    def test_a_lost_reply_is_settled_with_the_same_record_at_restart(self):
        draft = self.drafted()
        done = self.apply(draft, body=self.edited_body())
        first = braindraft.DRAFTS[draft["id"]]["change"]
        raw = braindraft.DRAFTS[draft["id"]]
        # play a crash after the write and before the draft was saved: the
        # base retained before the write is still on the record
        raw["status"] = "applying"
        raw["applyBase"] = raw["rounds"][-1]["base"]
        raw.pop("applied")
        raw.pop("change")
        braindraft.recover()
        again = braindraft.change_view(draft["id"])
        self.assertEqual(again["state"], "current")
        self.assertEqual(again["change"]["body"], first["body"])
        self.assertEqual(again["change"]["id"], first["id"])
        self.assertEqual(again["change"]["request"], done["draft"]["applied"]["request"])
        # Apply again answers with the one write; nothing is reapplied
        replay = self.apply(draft, body=self.edited_body())
        self.assertTrue(replay["already"])
        self.assertEqual(len(brain.reading()["entries"]), 1)

    def test_a_conflict_records_no_change_and_keeps_the_human_edit(self):
        draft = self.drafted()
        hand = SECTIONED.replace("UTF-8.", "UTF-8, edited by hand.")
        self.entry_file.write_text(hand, encoding="utf-8")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, body=self.edited_body())
        self.assertEqual(self.entry_file.read_text(encoding="utf-8"), hand)
        raw = braindraft.DRAFTS[draft["id"]]
        self.assertNotIn("change", raw)
        self.assertNotIn("applyBase", raw)
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.change_view(draft["id"])
        self.assertEqual(caught.exception.status, 404)

    def test_a_new_entry_is_recorded_as_one_insertion(self):
        draft = self.start("propose")
        self.assertIsNone(draft["changes"]["base"])
        done = self.apply(draft)
        view = braindraft.change_view(draft["id"])
        self.assertEqual(view["state"], "current")
        self.assertEqual(view["entry"]["id"], done["operation"]["id"])
        self.assertTrue(view["change"]["exact"])
        self.assertIsNone(view["change"]["baseRevision"])
        self.assertEqual(view["change"]["body"]["removed"], 0)

    def test_the_route_reads_and_launches_nothing(self):
        draft = self.drafted()
        self.apply(draft, body=self.edited_body())
        calls = self.calls()
        server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        before = self.entry_file.read_bytes()
        with urllib.request.urlopen(f"{base}/api/brain/change?draft={draft['id']}",
                                    timeout=10) as reply:
            got = json.loads(reply.read())
        self.assertEqual(got["state"], "current")
        self.assertNotIn("applyBase", json.dumps(got))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{base}/api/brain/change?draft=bd-0000000000", timeout=10)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(self.entry_file.read_bytes(), before)
        self.assertEqual(self.calls(), calls)
        self.assertEqual([a for a in state.AGENTS.values() if a["status"] == "running"], [])


class Upgrade(Applied):
    """Requests written before task 209: a round with no retained base, and
    an Apply with no change record — each said, never guessed."""

    # the journeys above are Applied's; these two are the upgrade's own
    test_the_review_shows_the_exact_base_and_its_changes = None
    test_apply_keeps_a_record_that_leads_to_the_first_change = None
    test_a_later_human_edit_reads_changed_since_and_never_highlights_other_text = None
    test_a_retired_entry_is_said_and_nothing_is_located = None
    test_a_lost_reply_is_settled_with_the_same_record_at_restart = None
    test_a_conflict_records_no_change_and_keeps_the_human_edit = None
    test_a_new_entry_is_recorded_as_one_insertion = None
    test_the_route_reads_and_launches_nothing = None

    def test_an_older_round_is_reviewed_against_the_entry_only_while_it_is_that_revision(self):
        draft = self.drafted()
        raw = braindraft.DRAFTS[draft["id"]]
        for rnd in raw["rounds"]:
            rnd.pop("base", None)              # as a pre-209 round was written
        got = braindraft.get(draft["id"])["changes"]
        self.assertEqual(got["baseRevision"], brain.entry("e-exports")["revision"])
        self.assertTrue(got["body"]["hunks"])
        self.entry_file.write_text(SECTIONED.replace("UTF-8.", "UTF-16."), encoding="utf-8")
        got = braindraft.get(draft["id"])["changes"]
        self.assertIsNone(got["base"])
        self.assertIn("not retained", got["missing"])

    def test_an_apply_without_a_record_says_so(self):
        draft = self.drafted()
        self.apply(draft, body=self.edited_body())
        raw = braindraft.DRAFTS[draft["id"]]
        raw.pop("change")                      # as a pre-209 Apply was recorded
        view = braindraft.change_view(draft["id"])
        self.assertFalse(view["change"]["exact"])
        self.assertIn("before change records were kept", view["change"]["why"])
        self.assertEqual(view["locations"], [])
        self.assertFalse(braindraft.get(draft["id"])["change"]["exact"])


class VendoredChange(drafting.Vendored):
    """The same record in a vendored layout (#175's fixture): the work
    root, the tool and its state inside the application checkout."""

    # the vendored drafting journeys are test_braindraft's own
    test_the_journey_holds_and_the_boundary_is_the_deny_rules = None
    test_a_citation_of_bench_private_state_through_the_app_is_refused = None

    def test_a_vendored_update_keeps_an_exact_record(self):
        self.entry_file.write_text(SECTIONED, encoding="utf-8")
        before = brain.entry("e-exports")
        draft = self.start("propose", target="e-exports", expected=before["revision"])
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        body = before["body"].replace("Comma separated.", "Semicolon separated.")
        self.apply(draft, body=body)
        view = braindraft.change_view(draft["id"])
        self.assertEqual(view["state"], "current")
        self.assertTrue(view["change"]["exact"])
        self.assertEqual(view["change"]["baseRevision"], before["revision"])
        lines = brainchange.lines_of(brain.entry("e-exports")["body"])
        self.assertEqual(lines[view["locations"][0]["start"]], "Semicolon separated.")


class ThePageMarks(unittest.TestCase):
    """The entry page marks only located changes, in whole Markdown blocks
    — run in node from the page's own source, as the other page-script
    tests are."""

    def run_page(self, body: str, after_body: str, before_body: str, current: str) -> str:
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available — the page script is unchecked")
        from tests.test_agents_settings_ui import lift
        rec = brainchange.record(draft="bd-0000000001", round_no=1, request="r",
                                 entry_id="e-x", file="e-x.md",
                                 base={"revision": "b", "title": "", "summary": "", "kind": "",
                                       "body": before_body},
                                 result={"revision": "a", "title": "", "summary": "", "kind": "",
                                         "body": after_body}, at="t")
        view = {"change": rec, "locations": brainchange.locate(
            rec, current, current_revision="a" if current == after_body else "c")}
        script = ("const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
                  ".replace(/\"/g,'&quot;');\n"
                  "const md = (s) => '<md>' + esc(s) + '</md>';\n"
                  "const KIND_WORD = { insert: 'Added', delete: 'Removed', replace: 'Changed' };\n"
                  + lift("changeLabel") + "\n" + lift("cutPoints") + "\n" + lift("bodyWithChanges")
                  + f"\nconsole.log(bodyWithChanges({json.dumps(body)}, {json.dumps(view)}));")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_a_change_inside_a_list_marks_the_whole_list_and_nothing_else(self):
        before = "Intro.\n\n- one\n- two\n\n## Next\n\nUnrelated."
        after = "Intro.\n\n- one\n- two\n- three\n\n## Next\n\nUnrelated."
        html = self.run_page(after, after, before, after)
        self.assertIn('id="bchg-1"', html)
        marked = html.split('<section', 1)[1].split('</section>', 1)[0]
        self.assertIn("- one\n- two\n- three", marked)
        self.assertNotIn("Unrelated", marked)
        self.assertNotIn("Intro.", marked)
        self.assertIn("Change 1 of 1: added 1 line", html)

    def test_a_deletion_is_said_where_it_was_with_its_text(self):
        before = "Para one.\n\nGone para.\n\nPara three."
        after = "Para one.\n\nPara three."
        html = self.run_page(after, after, before, after)
        self.assertIn("b-chg-gone", html)
        self.assertIn("Gone para.", html)
        self.assertLess(html.index("Para one."), html.index("b-chg-gone"))
        self.assertLess(html.index("b-chg-gone"), html.index("Para three."))

    def test_a_fence_is_never_split(self):
        before = "Intro.\n\n```\nx = 1\n\ny = 2\n```\n\nEnd."
        after = before.replace("y = 2", "y = 3")
        html = self.run_page(after, after, before, after)
        marked = html.split('<section', 1)[1].split('</section>', 1)[0]
        self.assertIn("```\nx = 1\n\ny = 3\n```", marked)

    def test_two_nearby_edits_mark_each_edit_and_not_the_paragraph_between(self):
        before, after = Locate.TWO_EDITS_BEFORE, Locate.TWO_EDITS_AFTER
        html = self.run_page(after, after, before, after)
        blocks = [s.split('</section>', 1)[0] for s in html.split('<section')[1:]]
        self.assertEqual(len(blocks), 2, html)
        self.assertIn("First para, edited.", blocks[0])
        self.assertIn("Last para, edited.\nLast para, extra line.", blocks[1])
        for b in blocks:
            self.assertNotIn("Middle para.", b)
        # the change's id and what it replaced are given once, on its first block
        self.assertEqual(html.count('id="bchg-1"'), 1)
        self.assertIn('id="bchg-1"', blocks[0])
        self.assertIn("(continued)", blocks[1])
        self.assertEqual(html.count("What this replaced"), 1)

    def test_hostile_text_is_escaped(self):
        before = "Safe."
        after = "Safe.\n\n<script>alert(1)</script>"
        html = self.run_page(after, after, before, after)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
