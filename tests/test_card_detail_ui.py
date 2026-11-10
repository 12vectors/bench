"""The card detail view's Acceptance and Reviews sections (task 81).

The server computes what a card's criteria and findings *are*
(`acceptance.read_text`); the page draws it. This suite feeds the real
server reading of fixture cards to the page's own renderers under node, so
what is asserted is what a person sees: an untracked card says its results
are not recorded, only an accepted criterion wears a tick, an agent's claim
is labelled as one, a historic request for changes is never drawn as
approval, and nothing anyone wrote becomes markup. Node is not a
dependency of bench, so the rendering checks skip without it; the
source-level invariants at the bottom always run.

    python3 -m unittest tests.test_card_detail_ui -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "manager" / "core"))

import acceptance  # noqa: E402
import progress  # noqa: E402
import taskfiles  # noqa: E402

BOARD = ROOT / "manager" / "core" / "board.html"
NODE = shutil.which("node")
HTML = BOARD.read_text(encoding="utf-8")
FILE = "90-x.md"
HOSTILE = '<img src=x onerror=alert(1)>'


def lift(pattern: str, what: str) -> str:
    m = re.search(pattern, HTML, re.M | re.S)
    assert m, f"board.html lost {what}"
    return m.group(0)


def function(name: str) -> str:
    return lift(rf"^(?:async )?function {name}\(.*?\n\}}\n", f"{name}()")


HARNESS = "\n".join([
    lift(r"^const esc = \(s\) =>.*?\n.*?\n", "esc()"),
    lift(r"^const DTABS = .*?\];\n", "DTABS"),
    lift(r"^const ACC_GLYPH = .*?\n", "ACC_GLYPH"),
    *(function(name) for name in (
        "actLabel", "ago", "whenOf", "stampOf", "cardSplit", "recordHtml",
        "evidenceOptions", "criterionHtml", "accActor", "acceptanceHtml",
        "roundLine", "sinceRound", "findingHtml", "md", "reportHtml", "reviewsHtml",
        "acceptanceChips", "dtabsHtml")),
    "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
    "const S = { state: { acceptance: { [input.file]: input.summary } },",
    "  accOpen: {}, accDraft: {}, evidence: { [input.file]: { evidence: [] } },",
    "  acc: { [input.file]: { data: input.acc } } };",
    "const t = { file: input.file, stage: 'review', body: input.body };",
    "const split = cardSplit(input.body);",
    "process.stdout.write(JSON.stringify({",
    "  acceptance: acceptanceHtml(t, input.acc), reviews: reviewsHtml(t, input.acc),",
    "  chips: acceptanceChips(t), tabs: dtabsHtml(t, 'acceptance', input.acc),",
    "  split }));",
])

HEAD = """# 90 — Detail view

**Status:** Review
**Priority:** Medium

## Acceptance

"""


def card(criteria: str, history: str = "") -> str:
    return HEAD + criteria + ("\n---\n\n" + history if history else "")


def record(meta: dict) -> str:
    return taskfiles.record_line("acceptance", {"v": 1, **meta})


@unittest.skipUnless(NODE, "node is needed to run the page's own renderers")
class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "detail.js"
        cls.js.write_text(HARNESS, encoding="utf-8")
        progress.RUNS.clear()

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def render(self, text: str) -> dict:
        acc = acceptance.read_text(text, FILE)
        acc["reports"] = acceptance.reports(text)
        acc["actor"] = {"name": "Reviewer", "source": "git"}
        out = subprocess.run(
            [NODE, str(self.js)], text=True, capture_output=True,
            input=json.dumps({"file": FILE, "acc": acc, "body": text,
                              "summary": acceptance.summary(text, FILE)}))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_an_untracked_card_says_results_are_not_recorded_and_ticks_nothing(self):
        out = self.render(card("- [x] Ticked by hand\n- [ ] Not ticked\n"))
        html = out["acceptance"]
        self.assertIn("Acceptance results not recorded", html)
        self.assertIn("a legacy claim, not an acceptance", html)
        self.assertIn('id="accTrack"', html)
        self.assertNotIn("abox", html, "no criterion row, so no tick box at all")
        self.assertEqual(out["chips"], [], "no count on the face of an untracked card")

    def test_only_an_accepted_criterion_wears_the_tick_and_the_count_says_accepted(self):
        text = card(
            "- [ ] Opens on the title <!-- ac:title -->\n"
            "- [ ] Keyboard reaches every section <!-- ac:keys -->\n"
            "- [ ] Counts link to sections <!-- ac:counts -->\n",
            "## Acceptance record\n\n" +
            record({"id": "r-1", "criterion": "title", "state": "accepted",
                    "source": "manual", "actor": "ron", "at": "2026-11-09T10:00:00Z",
                    "rev": acceptance.criterion_rev("Opens on the title"),
                    "note": "seen at 390px " + HOSTILE}) + "\n" +
            record({"id": "r-2", "criterion": "keys", "state": "needs-work",
                    "source": "manual", "actor": "ron", "at": "2026-11-09T10:01:00Z",
                    "rev": acceptance.criterion_rev("Keyboard reaches every section"),
                    "note": "tabs skip"}) + "\n")
        out = self.render(text)
        html = out["acceptance"]
        self.assertIn("<b>1 of 3</b> criteria accepted", html)
        self.assertNotIn("complete", html.split("criteria accepted")[1][:60])
        self.assertEqual(html.count('aria-hidden="true">✓</span>'), 1)
        self.assertIn('class="arow needs-work"', html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        chip = out["chips"][0]
        self.assertEqual(chip["label"], "1/3 accepted")
        self.assertEqual(chip["section"], "acceptance")
        self.assertIn("Not a measure of completion", chip["title"])
        self.assertIn('<span class="n">1/3</span>', out["tabs"])

    def test_a_reworded_criterion_shows_recheck_with_the_prior_result_kept(self):
        text = card("- [ ] Opens on the title and state <!-- ac:title -->\n",
                    "## Acceptance record\n\n" +
                    record({"id": "r-1", "criterion": "title", "state": "accepted",
                            "source": "manual", "actor": "ron",
                            "at": "2026-11-09T10:00:00Z",
                            "rev": acceptance.criterion_rev("Opens on the title"),
                            "note": "seen"}) + "\n")
        html = self.render(text)["acceptance"]
        self.assertIn('class="arow recheck"', html)
        self.assertIn("reworded after this decision", html)
        self.assertIn("<b>accepted</b>", html, "the earlier decision stays readable")
        self.assertIn("<b>0 of 1</b>", html)

    def test_a_historic_request_for_changes_is_explained_never_approved(self):
        text = card(
            "- [ ] One <!-- ac:one -->\n",
            "## PR review at 0123456789ab — 2026-11-08 15:20 (Juno)\n\n"
            f"The title is second. {HOSTILE}\n\nPR REVIEW: REQUEST CHANGES\n\n---\n\n"
            "## PR update — 2026-11-08 16:00 (Wren)\n\nMoved it.\n")
        rnd = acceptance.rounds(text)[0]["id"]
        text += ("\n## Review findings\n\n" + taskfiles.record_line("finding", {
            "v": 1, "id": "e-1", "finding": "f-1", "event": "opened",
            "text": "Title is second", "round": rnd, "actor": "ron",
            "at": "2026-11-08T15:30:00Z"}) + "\n" + taskfiles.record_line("finding", {
            "v": 1, "id": "e-2", "finding": "f-1", "event": "fix-reported",
            "note": "moved", "actor": "wren", "at": "2026-11-08T16:05:00Z"}) + "\n")
        out = self.render(text)
        html = out["reviews"]
        self.assertIn("Juno asked for changes at 0123456789ab", html)
        self.assertNotIn("approved", html.split("Review rounds")[1].split("</summary>")[0])
        self.assertIn("1 PR update since", html)
        self.assertIn("1 finding fix reported", html)
        self.assertIn("fix reported — awaiting recheck", html)
        self.assertIn("Resolve after recheck", html)
        self.assertNotIn("<img", html)
        self.assertEqual(out["chips"][-1]["label"], "1 fix to recheck")

    def test_the_page_splits_a_card_where_the_server_does(self):
        text = card("```\n## Work report — 2026-01-01 10:00 (x)\n```\n- [ ] A <!-- ac:a -->\n",
                    "## Work report — 2026-11-08 10:00 (Juno)\n\nDid it.\n")
        out = self.render(text)
        self.assertEqual(out["split"], taskfiles.card_parts(text))


class SourceInvariants(unittest.TestCase):
    def test_the_drawer_has_five_sections_and_keeps_its_tools(self):
        body = function("renderDrawer")
        for key in ("overview", "brief", "acceptance", "reviews", "activity"):
            self.assertIn(f"dsec('{key}'", body)
        self.assertIn('role="tablist"', function("dtabsHtml"))
        self.assertIn("ArrowRight", function("wireDetail"))
        self.assertIn('<h2 class="dtitle"', body, "the title leads the detail view")
        self.assertLess(body.index('class="dtitle"'), body.index("dtabsHtml("),
                        "identity before the sections")

    def test_viewing_assigns_nothing(self):
        """Only the armed Track action posts to /track; loading reads."""
        self.assertIn("fetch(`/api/card/acceptance?file=", function("loadAcc"))
        self.assertEqual(HTML.count("/api/card/acceptance/track"), 1)
        self.assertIn("label: 'Track acceptance', confirm: true", function("wireDetail"))

    def test_a_finished_run_never_reads_as_doing_now(self):
        body = function("progressFact")
        self.assertIn("history, not current", body)
        self.assertIn("if (p.active)", body)


if __name__ == "__main__":
    unittest.main()
