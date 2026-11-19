"""What the top-level Orchestrator view reads from the server (task 145).

The view owns no orchestration logic: it draws `GET /api/orchestration`,
`GET /api/card/decisions` and a compact summary in `/api/state`. Three
small server additions carry it, and each is held here:

- `/api/state` says whether orchestration is on, each scope's pause, and
  which scope holds each managed card (or why none does, or that a person
  took it back) — one file read, nothing written, nothing launched;
- the reading carries the agreement document the view edits from: the
  keys this version reads, notes scrubbed, nothing a hand edit added;
- a card's decisions reading names the brief an answer is bound to.

    python3 -m unittest tests.test_orchestrator_view -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import agreements  # noqa: E402
import decisions  # noqa: E402
import orchestrator  # noqa: E402
import routes  # noqa: E402
import taskfiles  # noqa: E402
from tests import test_decisions as td  # noqa: E402
from tests import test_orchestration_scopes as ts  # noqa: E402

SECRET = "ghp_" + "Z9y8" * 9


class StateSummary(ts.Sandbox):

    def test_off_without_an_agreement_and_reading_writes_nothing(self):
        before = ts.snapshot(self.local, self.work)
        found = orchestrator.board_summary(ts.board(ts.card("7")))
        self.assertEqual(found["status"], "off")
        self.assertFalse(found["configured"])
        self.assertEqual(found["cards"], {})
        self.assertEqual(ts.snapshot(self.local, self.work), before)

    def test_scope_pause_and_manual_cards_are_said(self):
        doc = ts.agreement(handback=["8"], exclusions=["9"])
        doc["phases"] = {"20": {"enabled": True, "paused": True,
                                "allocation": {"implementation": 1}}}
        self.put_in_force(doc)
        tasks = ts.board(ts.card("7"), ts.card("8", "in-progress"),
                         ts.card("9", "review"), ts.card("10", "backlog"),
                         ts.card("20", "in-progress", is_phase=True),
                         ts.card("21", phase="20"),
                         ts.card("31", phase="30"))
        before = ts.snapshot(self.local, self.work)
        found = orchestrator.board_summary(tasks)
        self.assertEqual(ts.snapshot(self.local, self.work), before)
        self.assertEqual(found["status"], "enabled")
        self.assertEqual(found["revision"], agreements.effective()["revision"])
        cards = found["cards"]
        self.assertEqual(cards["7-card.md"], {"scope": "bench", "why": None,
                                              "manual": None})
        self.assertEqual(cards["8-card.md"]["manual"], "handback")
        self.assertEqual(cards["9-card.md"]["manual"], "excluded")
        # backlog is never managed, so it is not listed
        self.assertNotIn("10-card.md", cards)
        self.assertEqual(cards["21-card.md"]["scope"], "phase:20")
        self.assertEqual(cards["31-card.md"], {"scope": None, "why": "legacy-phase",
                                               "manual": None})
        phase = next(s for s in found["scopes"] if s["id"] == "phase:20")
        self.assertTrue(phase["paused"])
        self.assertEqual(phase["pausedBy"], "phase:20")
        self.assertFalse(phase["active"])

    def test_a_bench_pause_reads_paused_and_reaches_its_phases(self):
        doc = ts.agreement(paused=True)
        doc["phases"] = {"20": {"enabled": True}}
        self.put_in_force(doc)
        found = orchestrator.board_summary(ts.board(ts.card("7")))
        self.assertEqual(found["status"], "paused")
        phase = next(s for s in found["scopes"] if s["id"] == "phase:20")
        self.assertEqual(phase["pausedBy"], "bench")

    def test_an_invalid_agreement_reads_invalid_and_holds_no_card(self):
        doc = ts.agreement()
        doc["bench"]["capacity"]["implementation"] = "two"
        agreements.document_path().write_text(json.dumps(doc), encoding="utf-8")
        found = orchestrator.board_summary(ts.board(ts.card("7")))
        self.assertEqual(found["status"], "invalid")
        self.assertEqual(found["cards"], {})

    def test_the_state_payload_carries_it(self):
        text = (REPO / "manager" / "core" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('"orchestration": orchestrator.board_summary(collected)', text)


class TheDocumentTheViewEdits(ts.Sandbox):

    def test_effective_and_draft_documents_are_known_keys_with_notes_scrubbed(self):
        doc = ts.agreement(note=f"token {SECRET}")
        doc["bench"]["handmade"] = "kept off the page"
        doc["phases"] = {"20": {"enabled": True, "note": "phase note",
                                "allocation": {"implementation": 1}},
                         "not-a-card": {"enabled": True}}
        # an unknown key refuses the document; written directly, as a hand
        # edit would be, so the reading still has a document to show
        agreements.document_path().write_text(json.dumps(doc), encoding="utf-8")
        agreements.save_draft(ts.agreement(paused=True), "none")
        found = orchestrator.reading(ts.board(ts.card("7")))
        effective = found["effective"]["document"]
        self.assertNotIn(SECRET, json.dumps(found))
        self.assertIn("••••", effective["bench"]["note"])
        self.assertNotIn("handmade", effective["bench"])
        self.assertEqual(sorted(effective["phases"]), ["20"])
        self.assertEqual(effective["phases"]["20"]["note"], "phase note")
        draft = found["draft"]["document"]
        self.assertTrue(draft["bench"]["paused"])
        self.assertEqual(draft["kind"], agreements.KIND)
        # the revision a save is guarded by is the file's own, not the
        # public copy's
        self.assertEqual(found["draft"]["revision"],
                         agreements.revision(agreements.draft_document()[0]))

    def test_no_agreement_has_no_document(self):
        found = orchestrator.reading(ts.board(ts.card("7")))
        self.assertIsNone(found["effective"]["document"])
        self.assertIsNone(found["draft"])

    def test_public_document_refuses_what_is_not_a_document(self):
        self.assertIsNone(agreements.public_document(None))
        self.assertIsNone(agreements.public_document(["v", 1]))
        self.assertEqual(agreements.public_document({"bench": "x", "phases": 3}),
                         {"v": None, "kind": None, "bench": {}, "phases": {}})


class TheAnswerBinding(td.Board):

    def test_the_reading_names_the_brief_an_answer_is_bound_to(self):
        self.ask()
        found = decisions.for_card("51-export-as-csv.md", "to-do")
        self.assertEqual(found["briefSha"], self.brief_sha())
        q = next(q for q in found["questions"] if q["state"] == "open")
        # the page sends back exactly what it read, and that is accepted
        result = decisions.answer("51-export-as-csv.md", "to-do", question=q["id"],
                                  basis=q["basis"], brief_sha=found["briefSha"],
                                  actor_seen=found["actor"]["name"], choice=0,
                                  request="req-view-0001")
        self.assertFalse(result.get("duplicate"))


class ThePagePaths(unittest.TestCase):

    def test_orchestrator_addresses_are_the_page_and_nothing_else_is(self):
        for path in ("/orchestrator", "/orchestrator/decisions",
                     "/orchestrator/agreement", "/orchestrator/no-such-section"):
            self.assertTrue(routes.ui_path(path), path)
        for path in ("/orchestrator/", "/orchestrator/Work", "/orchestrator/a_b",
                     "/orchestrator/a/b", "/orchestrators"):
            self.assertFalse(routes.ui_path(path), path)
        rows = {(r.method, r.path): r.klass for r in routes.ROUTES}
        self.assertEqual(rows[("GET", "/orchestrator")], "read")
        self.assertEqual(rows[("GET", "/orchestrator/*")], "read")


if __name__ == "__main__":
    unittest.main()


class TheControlsRead(unittest.TestCase):
    """Task 147: armed and busy Orchestrator buttons say their words in
    both themes. The browser case measures computed colours; this keeps
    the rules that make it so from being dropped by an edit."""

    def setUp(self):
        self.css = (REPO / "manager" / "core" / "web" / "board.css").read_text(encoding="utf-8")

    def rule(self, selector):
        import re
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            if selector in [s.strip() for s in m.group(1).split(",")]:
                return m.group(2)
        self.fail(f"no rule for {selector}")

    def test_armed_and_busy_outrank_a_primary_under_the_pointer(self):
        for sel in (".o-btn.primary.armed:hover", ".o-btn.armed:hover"):
            body = self.rule(sel)
            self.assertIn("color:var(--alarm)", body)
            self.assertIn("background:var(--surface)", body)
            self.assertIn("filter:none", body)
        for sel in (".o-btn.primary.busy:hover", ".o-btn.primary.busy"):
            body = self.rule(sel)
            self.assertIn("color:var(--accent)", body)
            self.assertIn("background:var(--surface)", body)
        self.assertIn("opacity:1", self.rule(".o-btn.busy:disabled"))
