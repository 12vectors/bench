"""Reports read as documents (task 83).

A review round, a work report, a PR update and a relevance review used to
reach the detail view as one escaped `<pre>`: emphasis markers and
backticks left visible, "Before merging" no heavier than the prose around
it, nested bullets flattened into indentation. They now go through
`md()` — the renderer the brief already uses, not a second one — wrapped
by `reportHtml()`, which keeps the original Markdown one fold away.

Like `test_drawer_markdown`, this lifts the page's own functions out of
board.html and runs them under node: the renderer is a pure function of
its input, so its output is what to assert on. The source-level checks at
the bottom always run.

    python3 -m unittest tests.test_review_markdown -v
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
FIXTURES = ROOT / "tests" / "fixtures" / "review-markdown"
NODE = shutil.which("node")
HTML = BOARD.read_text(encoding="utf-8")


def lift(pattern: str, what: str) -> str:
    m = re.search(pattern, HTML, re.M | re.S)
    assert m, f"board.html lost {what}"
    return m.group(0)


MD_FN = r"^function md\(src[^)]*\) \{\n.*?\n\}\n"
REPORT_FN = r"^function reportHtml\(t, text, key\) \{\n.*?\n\}\n"

HARNESS = (
    lift(r"^const esc = \(s\) =>.*?\n.*?\n", "its esc() helper")
    + lift(MD_FN, "its md() renderer")
    + "const S = { accOpen: {} };\n"
    + lift(REPORT_FN, "its reportHtml() helper")
    + "const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
    + "process.stdout.write(job.report\n"
    + "  ? reportHtml({ file: 'x.md' }, job.src, job.key || 'k')\n"
    + "  : md(job.src, job.opts || {}));\n"
)


def text_of(html: str) -> str:
    """What a reader sees: tags dropped, entities decoded."""
    out = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
        out = out.replace(a, b)
    return re.sub(r"\s+", " ", out).strip()


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class Case(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "report.js"
        cls.js.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def run_js(self, job: dict) -> str:
        out = subprocess.run([NODE, str(self.js)], input=json.dumps(job), text=True,
                             capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def md(self, src: str, **opts) -> str:
        return self.run_js({"src": src, "opts": opts})

    def report(self, src: str, key: str = "rv-1") -> str:
        return self.run_js({"src": src, "report": True, "key": key})


class TheScreenshotCase(Case):
    """The round card 77's Reviews tab showed as a monospace dump."""

    SRC = (FIXTURES / "round-like-77.md").read_text(encoding="utf-8")

    def rendered(self) -> str:
        return self.md(self.SRC, base=4)

    def test_before_merging_is_a_heading_under_the_sections_own(self):
        html = self.rendered()
        self.assertIn("<h4>Before merging</h4>", html)
        self.assertIn("<h5>To know (not blocking)</h5>", html)
        self.assertNotRegex(html, r"<h[123]>", "a report heading outranks the drawer's own")

    def test_labels_are_emphasised_and_their_lists_are_lists(self):
        html = self.rendered()
        self.assertIn("<p><strong>What I checked</strong></p><ul>", html)
        self.assertIn("<strong>Typing a model must enable Apply.</strong>", html)
        self.assertIn("<em>names and hashes</em>", html)

    def test_nested_bullets_nest(self):
        html = self.rendered()
        self.assertRegex(html, r"<li>The template contract in <code>templates\.py</code>:<ul><li>")
        self.assertIn("<ol><li><strong>Typing a model", html)

    def test_identifiers_are_inline_code(self):
        html = self.rendered()
        for ident in ("config.agent_choice(intent)", "wireAgents()", "{body.x}", "NOT READY:"):
            self.assertIn(f"<code>{ident}</code>", html)

    def test_no_markdown_punctuation_survives_outside_code(self):
        html = self.rendered()
        prose = re.sub(r"<pre>.*?</pre>|<code>.*?</code>", "", html, flags=re.S)
        seen = text_of(prose)
        for mark in ("**", "`", "## ", "### ", "| ---", "\n- "):
            self.assertNotIn(mark, seen, f"{mark!r} is still visible")
        self.assertNotRegex(seen, r"(^| )[-*] ", "a bullet marker is visible as text")

    def test_code_keeps_its_markup_literal(self):
        html = self.rendered()
        block = re.search(r"<pre><code>(.*?)</code></pre>", html, re.S).group(1)
        self.assertIn("**not bold**, `not code`", block)
        self.assertNotIn("<strong>", block)
        self.assertIn('    return {&quot;harness&quot;', block, "the fence lost its indentation")

    def test_tables_quotes_and_links(self):
        html = self.rendered()
        self.assertIn('<div class="tablewrap"><table>', html)
        self.assertIn("<td><code>opencode</code> drawn as <code>not installed</code>", html)
        self.assertIn("<blockquote>Formal GitHub approval is a separate fact", html)
        self.assertIn('<a href="https://github.com/12vectors/bench/pull/77#issuecomment-1234567890"'
                      ' rel="noopener noreferrer">', html)
        self.assertIn("1234567890</a>.", html, "the sentence's full stop joined the link")

    def test_the_verdict_line_is_prose_not_a_badge(self):
        html = self.rendered()
        self.assertIn("<p>PR REVIEW: REQUEST CHANGES</p>", html)
        self.assertNotIn("class=", re.sub(r'class="(tablewrap)"', "", html),
                         "styling invented a class — a status the prose never had")

    def test_every_word_of_the_report_is_still_there(self):
        """Rendering drops punctuation, never content (a fence's language
        tag is a label for the block, not text of it)."""
        words = set(re.findall(r"[A-Za-z]{4,}", self.SRC)) - {"python"}
        seen = text_of(self.rendered())
        missing = sorted(w for w in words if w not in seen)
        self.assertEqual(missing, [])


class Hostile(Case):
    """Report text is untrusted: nothing in it runs, loads or poses."""

    SRC = (FIXTURES / "hostile.md").read_text(encoding="utf-8")

    def test_raw_html_is_text(self):
        html = self.md(self.SRC, base=4)
        for tag in ("<script", "<img", "<iframe", "<svg", "<b ", "<!--"):
            self.assertNotIn(tag, html)
        self.assertNotRegex(html, r"<[a-z]+[^>]*\son\w+=", "an event attribute reached a tag")
        self.assertIn("&lt;script&gt;", html)

    def test_only_safe_links_are_links(self):
        html = self.md(self.SRC)
        hrefs = re.findall(r'href="([^"]*)"', html)
        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertTrue(href.startswith("https://"), f"{href!r} is a link")
        for label in ("click me", "data", "vbscript", "protocol relative", "backslashes",
                      "entity colon"):
            # a nested paren in the target ends the match early; what is
            # left of it is text either way
            self.assertRegex(html, rf"<li>{label}\)?</li>", f"{label!r} is not plain text")

    def test_a_relative_link_stays_one(self):
        html = self.md("[the spec](../../reference/spec.md) and [top](#acceptance)")
        self.assertIn('href="../../reference/spec.md"', html)
        self.assertIn('href="#acceptance"', html)

    def test_an_image_is_never_fetched(self):
        html = self.md("![a pixel](https://evil.example/p.png)")
        self.assertNotIn("<img", html)
        self.assertNotIn("src=", html)
        self.assertIn(">a pixel</a>", html)

    def test_metadata_shaped_text_stays_inert(self):
        html = self.md(self.SRC)
        self.assertIn("<code>&lt;!-- bench:acceptance", html)
        self.assertIn("<p>&lt;!-- bench:finding", html)
        self.assertIn("<p><strong>Status:</strong> Done</p>", html)
        self.assertIn("<p>PR REVIEW: APPROVE</p>", html)

    def test_held_span_characters_cannot_be_forged(self):
        html = self.md("0 and `x`")
        self.assertIn("<code>x</code>", html)
        self.assertNotIn("", html)


class ReportHtml(Case):
    """The wrapper both report surfaces share."""

    def test_rendered_body_and_exact_original(self):
        src = (FIXTURES / "round-like-77.md").read_text(encoding="utf-8").rstrip("\n")
        html = self.report(src, key="rv-abc")
        self.assertTrue(html.startswith('<div class="rtext rmd dbody"><p><strong>Verdict'), html[:80])
        raw = re.search(r'<pre class="rraw"[^>]*>(.*?)</pre>', html, re.S).group(1)
        for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
            raw = raw.replace(a, b)
        self.assertEqual(raw, src, "the original view is not the report byte for byte")
        self.assertIn('data-keep="src:rv-abc"', html)
        self.assertIn("Copy original Markdown", html)

    def test_the_original_view_is_escaped_too(self):
        html = self.report("<script>alert(1)</script>")
        self.assertNotIn("<script", html)


class SourceInvariants(unittest.TestCase):
    """Always run: the shape of the change, for a machine without node."""

    def test_no_report_is_drawn_as_a_raw_pre_any_more(self):
        self.assertNotIn('<pre class="rtext">', HTML)
        self.assertEqual(HTML.count("reportHtml(t, r.report, r.id)"), 1, "review rounds")
        self.assertEqual(HTML.count("reportHtml(t, it.report, it.key)"), 1, "activity reports")

    def test_one_renderer(self):
        self.assertEqual(len(re.findall(r"^function md\(", HTML, re.M)), 1)
        fn = lift(REPORT_FN, "reportHtml")
        self.assertIn("md(text, { base: 4 })", fn)

    def test_reports_are_styled_with_the_boards_tokens_only(self):
        rules = re.findall(r"#drawer \.(?:rmd|rsrc|rpt \.rtext\.rmd)[^{]*\{([^}]*)\}", HTML)
        self.assertGreater(len(rules), 10)
        for body in rules:
            self.assertNotRegex(body, r"#[0-9a-fA-F]{3,8}\b|rgb\(", "a literal colour, not a token")
        pre = re.search(r"#drawer \.dbody pre\{([^}]*)\}", HTML).group(1)
        self.assertIn("overflow-x:auto", pre, "wide code must scroll in place")
        wrap = re.search(r"#drawer \.dbody \.tablewrap\{([^}]*)\}", HTML).group(1)
        self.assertIn("overflow-x:auto", wrap, "wide tables must scroll in place")

    def test_opening_a_report_posts_nothing(self):
        """The copy button touches the clipboard and nothing else."""
        start = HTML.index("body.querySelectorAll('.rcopy')")
        handler = HTML[start:HTML.index("}));", start)]
        self.assertNotIn("fetch(", handler)
        self.assertNotIn("postAcc", handler)


if __name__ == "__main__":
    unittest.main()
