"""The card's discussion panel in board.html (task 65).

The composer, the job states, the proposal's diff and its Apply / Discard
are drawn by a handful of pure functions in the page; this suite lifts
them out and runs them under node, so what is asserted is what the page
actually renders — above all that a comment, an explanation or a diff
line is drawn as text and never as markup. Node is not a dependency of
bench, so those checks skip without it; the source-level invariants at
the bottom always run.

    python3 -m unittest tests.test_discussion_ui -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "manager" / "core" / "board.html"
NODE = shutil.which("node")
HTML = BOARD.read_text(encoding="utf-8")


def lift(pattern: str, what: str) -> str:
    m = re.search(pattern, HTML, re.M | re.S)
    assert m, f"board.html lost {what}"
    return m.group(0)


def function(name: str) -> str:
    return lift(rf"^(?:async )?function {name}\(.*?\n\}}\n", f"{name}()")


HARNESS = "\n".join([
    lift(r"^const esc = \(s\) =>.*?\n.*?\n", "esc()"),
    lift(r"^const DISCUSSION_SECTION = .*?\n", "DISCUSSION_SECTION"),
    function("withoutDiscussion"),
    lift(r"^const JOB_WORD = \{.*?\n\};\n", "JOB_WORD"),
    function("actLabel"),
    function("diffHtml"),
    function("jobHtml"),
    function("discussionLive"),
    function("discussionPanel"),
    function("md"),
    "const S = { state: {}, commentDraft: {}, disc: {} };",
    "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
    "S.disc[input.file] = { data: input.data };",
    "const t = { file: input.file, stage: 'to-do' };",
    "process.stdout.write(JSON.stringify({",
    "  live: discussionLive(t), panel: discussionPanel(t),",
    "  stripped: withoutDiscussion(input.body || ''),",
    "  md: md(input.md || '') }));",
])

HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def view(**overrides) -> dict:
    data = {
        "entries": [
            {"id": "c-1", "kind": "comment", "author": "ron <b>", "at": "2026-11-08",
             "body": HOSTILE},
            {"id": "d-1", "kind": "decision", "outcome": "discarded",
             "proposal": "p-0", "author": "ron", "at": "2026-11-08",
             "body": HOSTILE},
        ],
        "jobs": [{"id": "p-1", "comment": "c-1", "status": "proposed",
                  "attempt": 1, "name": "Juno", "explanation": HOSTILE,
                  "sections": ["Acceptance"], "affected": [],
                  "fresh": {"fresh": True, "why": None},
                  "diff": [{"op": "+", "text": HOSTILE},
                           {"op": "-", "text": "old line"},
                           {"op": "…", "text": "4 unchanged lines"}]}],
        "blocked": None,
        "refine": {"ready": True, "blockers": [], "codes": []},
        "persistence": {"commits": True, "syncs": True, "stateRepo": True},
    }
    data.update(overrides)
    return data


@unittest.skipUnless(NODE, "node is needed to run the page's own renderers")
class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "discussion.js"
        cls.js.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def render(self, data: dict, body: str = "", md_src: str = "") -> dict:
        out = subprocess.run([NODE, str(self.js)], text=True, capture_output=True,
                             input=json.dumps({"file": "65-x.md", "data": data,
                                               "body": body, "md": md_src}))
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_nothing_anyone_wrote_is_rendered_as_markup(self):
        html = self.render(view())["live"]
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("ron &lt;b&gt;", html, "an author name is text too")
        self.assertIn("&lt;script&gt;", html)

    def test_a_proposal_shows_its_diff_and_both_decisions(self):
        html = self.render(view())["live"]
        self.assertIn('class="rdiff"', html)
        self.assertIn('class="d dadd"', html)
        self.assertIn('class="d ddel"', html)
        self.assertIn('data-act="apply"', html)
        self.assertIn('data-act="discard"', html)
        self.assertIn("proposal — not applied", html)

    def test_a_stale_proposal_offers_regenerate_not_apply(self):
        data = view()
        data["jobs"][0]["fresh"] = {"fresh": False, "why": "the brief has changed"}
        html = self.render(data)["live"]
        self.assertNotIn('data-act="apply"', html)
        self.assertIn('data-act="retry"', html)
        self.assertIn("stale", html)

    def test_each_job_state_is_drawn_in_words(self):
        for status, word, act in (("running", "refining…", "cancel"),
                                  ("failed", "failed", "retry"),
                                  ("cancelled", "cancelled", "retry")):
            with self.subTest(status):
                data = view()
                data["jobs"][0].update(status=status, error="API Error: 500")
                html = self.render(data)["live"]
                self.assertIn(word, html)
                self.assertIn(f'data-act="{act}"', html)

    def test_a_blocked_card_explains_and_disables_refining(self):
        data = view(blocked="Wren is implementing 65-x.md — hold the run with ‖ hold first",
                    jobs=[])
        out = self.render(data)
        self.assertIn("hold the run", out["live"])
        self.assertRegex(out["panel"], r'id="refineNew" disabled')
        self.assertIn('id="addComment"', out["panel"],
                      "a comment can always be added")

    def test_an_unsupported_board_says_so(self):
        data = view(refine={"ready": False, "blockers": ["the x adapter does not "
                                                          "list 'refine'"],
                            "codes": ["intent-unsupported"]}, jobs=[])
        out = self.render(data)
        self.assertIn("not available on this board", out["live"])
        self.assertRegex(out["panel"], r'id="refineNew" disabled')

    def test_sync_disabled_is_said_truthfully(self):
        data = view(persistence={"commits": False, "syncs": False, "stateRepo": True})
        self.assertIn("not committed", self.render(data)["live"])

    def test_the_discussion_is_not_rendered_as_part_of_the_brief(self):
        body = ("# t\n\nBrief text.\n\n## Discussion\n\n<!-- bench:discussion v1 -->\n\n"
                "### Comment c-1\n<!-- bench:entry {} -->\n\n> hi\n\n---\n\n"
                "## Work report — 2026-11-09 10:00 (Wren)\n\nkept\n")
        stripped = self.render(view(), body=body)["stripped"]
        self.assertNotIn("bench:entry", stripped)
        self.assertIn("Brief text.", stripped)
        self.assertIn("## Work report", stripped, "later history stays")

    def test_a_script_link_in_a_brief_is_not_a_link(self):
        html = self.render(view(), md_src="[click](javascript:alert(1)) and "
                                          "[spec](../../reference/a.md)")["md"]
        self.assertNotIn("javascript:", html.split("click")[0])
        self.assertNotIn('href="javascript', html)
        self.assertIn('href="../../reference/a.md"', html)


@unittest.skipUnless(NODE, "node is needed to parse the page's script")
class Script(unittest.TestCase):
    def test_the_page_script_parses(self):
        script = lift(r"<script>\n(.*)</script>", "its script").split("\n", 1)[1]
        script = script.rsplit("</script>", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run([NODE, "--check", str(path)], capture_output=True,
                                 text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class SourceInvariants(unittest.TestCase):
    def test_add_comment_and_update_with_agent_are_distinct_actions(self):
        self.assertIn(">Add comment</button>", HTML)
        self.assertIn("✎ Update card with agent", HTML)
        self.assertIn("'/api/card/comment'", HTML)
        self.assertIn("'/api/card/refine'", HTML)

    def test_the_token_spending_and_brief_writing_actions_arm_first(self):
        body = function("wireDiscussion") + function("wireLive")
        for label in ("Update card with agent", "Retry", "Apply"):
            self.assertRegex(body, rf"label: '{label}', confirm: true", label)

    def test_a_refinement_request_carries_a_request_key(self):
        self.assertIn("request: key", function("wireDiscussion"))
        self.assertIn("newRequestKey()", function("wireLive"))

    def test_the_drawer_draws_the_brief_without_the_discussion(self):
        self.assertIn("md(withoutDiscussion(t.body))", HTML)


LOADER = "\n".join([
    function("discStamp"),
    function("loadDiscussion"),
    "const S = { state: { refinements: {} }, disc: {}, selected: null };",
    "const agentOnTask = () => null;",
    "function renderDiscussion(t) {}",
    "const pending = [];",
    "global.fetch = (url) => new Promise(resolve => pending.push(resolve));",
    "const reply = (i, body) => { pending[i]({ ok: true, json: async () => body }); };",
    "const settle = () => new Promise(r => setTimeout(r, 0));",
    "(async () => {",
    "  const t = { file: '02-x.md', stage: 'to-do', mtime: 1 };",
    "  S.selected = t;",
    # the drawer opens: the first read is in flight
    "  loadDiscussion(t);",
    # a comment is saved meanwhile, and the page asks for a fresh read
    "  loadDiscussion(t, true);",
    # the first read answers with what the card held before the write
    "  reply(0, { entries: [] });",
    "  await settle(); await settle();",
    "  if (pending.length > 1) { reply(1, { entries: [{ id: 'c-1' }] });",
    "                            await settle(); await settle(); }",
    "  process.stdout.write(JSON.stringify({ fetches: pending.length,",
    "    shown: S.disc[t.file].data, loading: !!S.disc[t.file].loading }));",
    "})();",
])


@unittest.skipUnless(NODE, "node is needed to run the page's own loader")
class Loading(unittest.TestCase):
    def test_a_refresh_asked_for_during_a_read_is_not_dropped(self):
        """The browser case for card 65 failed intermittently with the saved
        comment missing from the discussion. One way that happens: the
        drawer's first read is still in flight when the comment is saved,
        the forced refresh after the write is dropped because a read is
        'loading', and the first read then answers with the card as it was
        before the comment — so nothing ever shows it. A refresh asked for
        during a read must be made once that read ends."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loader.js"
            path.write_text(LOADER, encoding="utf-8")
            out = subprocess.run([NODE, str(path)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(2, got["fetches"], "the refresh after the write was dropped")
        self.assertEqual([{"id": "c-1"}], got["shown"]["entries"])
        self.assertFalse(got["loading"])


if __name__ == "__main__":
    unittest.main()
