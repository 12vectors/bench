"""The drawer renders a wrapped list item as one item (task 41).

`md()` in board.html used to treat every *physical* line inside a block as
a unit. Task files are hard-wrapped at ~74 columns, so the second line of
an item became its own bullet, `- [ ]` rendered as a literal bracket pair,
nested lists flattened, and paragraphs kept the author's ragged edge via
`<br>`.

board.html is a single file with inline JS and no frontend test runner, so
this suite lifts `esc()` and `md()` straight out of the page and runs them
under node — the renderer is a pure function of its input, so its actual
output is what to assert on. Node is not a dependency of bench itself, so
those checks skip when it is absent; the source-level invariants at the
bottom always run and are in the same style as the board's other
`test_*.py` checks on board.html.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

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


HARNESS = (
    lift(r"^const esc = \(s\) =>.*?\n.*?\n", "its esc() helper")
    + lift(r"^function md\(src\) \{\n.*?\n\}\n", "its md() renderer")
    + "process.stdout.write(md(require('fs').readFileSync(0, 'utf8')));\n"
)


class RendererCase(unittest.TestCase):
    """Base: run the page's own md() over a markdown string."""

    @classmethod
    def setUpClass(cls):
        if not NODE:
            return
        cls._dir = tempfile.TemporaryDirectory()
        cls.js = Path(cls._dir.name) / "md.js"
        cls.js.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if NODE:
            cls._dir.cleanup()

    def render(self, src: str) -> str:
        out = subprocess.run([NODE, str(self.js)], input=src, text=True,
                             capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    INNERMOST = re.compile(r"<(ul|ol)>((?:(?!<(?:ul|ol)>).)*?)</\1>", re.S)

    def items(self, html: str) -> list[str]:
        """The text of each top-level <li>: nested lists dropped, markup
        stripped, so a test can assert on what the reader sees."""
        body = re.sub(r"</(ul|ol)>\s*$", "", re.sub(r"^\s*<(ul|ol)>", "", html))
        while self.INNERMOST.search(body):            # peel nested lists off
            body = self.INNERMOST.sub("", body)
        body = re.sub(r'<span class="box".*?</span>', "", body)  # the tick glyph
        return [re.sub(r"<[^>]+>", "", li).strip()
                for li in re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)]


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class WrappedItemsTests(RendererCase):
    """One bullet per item, however the author wrapped it."""

    def test_a_wrapped_item_is_one_item(self):
        """The live bug: 'serves the built landing page' / 'over' were two
        bullets because the source line broke between them."""
        html = self.render(
            "- Given a request for the site, when it is served, then the\n"
            "  worker serves the built landing page\n"
            "- A second item\n")
        self.assertEqual(html.count("<li"), 2)
        self.assertEqual(
            self.items(html),
            ["Given a request for the site, when it is served, then the "
             "worker serves the built landing page", "A second item"])

    def test_continuation_lines_join_with_a_space(self):
        """Three source lines, one item, and no word welded to its
        neighbour across the join."""
        items = self.items(self.render(
            "- one two\n  three four\n  five six\n"))
        self.assertEqual(items, ["one two three four five six"])

    def test_a_continuation_starting_with_a_word_is_not_a_bullet(self):
        """'and' opening a wrapped line is prose, not a new item."""
        items = self.items(self.render(
            "- The renderer is line-based\n  and the task files are hard-wrapped\n"))
        self.assertEqual(items, ["The renderer is line-based "
                                 "and the task files are hard-wrapped"])

    def test_inline_code_with_a_hyphen_stays_one_item(self):
        """A hyphen inside backticks is not a marker; the item keeps its
        code span and does not split."""
        html = self.render(
            "- The strip is `/^\\s*[-*]\\s+/` and it does\n  nothing here\n")
        self.assertEqual(html.count("<li"), 1)
        self.assertIn("<code>", html)

    def test_ordered_lists_group_the_same_way(self):
        html = self.render(
            "1. The board creates a git worktree on a new\n"
            "   branch from the newest main it can see\n"
            "2. The agent works in the worktree\n")
        self.assertTrue(html.startswith("<ol>"), html[:40])
        self.assertEqual(len(self.items(html)), 2)


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class TaskListTests(RendererCase):
    """`- [ ]` / `- [x]` become checkboxes, and only ever glyphs."""

    SRC = ("- [ ] Given an Acceptance list whose items wrap, then there\n"
           "      is exactly one bullet per item\n"
           "- [x] Fenced code blocks are unchanged\n")

    def test_no_bracket_survives_as_text(self):
        html = self.render(self.SRC)
        text = " ".join(self.items(html))
        self.assertNotIn("[", text)
        self.assertNotIn("]", text)

    def test_the_item_text_survives_beside_the_box(self):
        self.assertEqual(
            self.items(self.render(self.SRC)),
            ["Given an Acceptance list whose items wrap, then there "
             "is exactly one bullet per item",
             "Fenced code blocks are unchanged"])

    def test_ticked_and_unticked_are_distinguishable(self):
        html = self.render(self.SRC)
        lis = re.findall(r"<li([^>]*)>", html)
        self.assertEqual(len(lis), 2)
        self.assertIn('class="tick"', lis[0])          # open: neutral
        self.assertIn('class="tick on"', lis[1])       # done: settled
        self.assertEqual(html.count('<span class="box"'), 2)

    def test_a_checkbox_is_not_interactive(self):
        """A span, never an <input> and never a handler: clicking it can
        do nothing, so it cannot quietly edit the file."""
        html = self.render(self.SRC + "\n- [X] upper case counts as ticked\n")
        self.assertNotIn("<input", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("contenteditable", html)
        self.assertIn('class="tick on"', re.findall(r"<li([^>]*)>", html)[-1])

    def test_a_bracket_that_is_not_a_checkbox_is_left_alone(self):
        items = self.items(self.render("- [see the spec](../ref.md) explains it\n"))
        self.assertEqual(items, ["see the spec explains it"])
        self.assertIn('href="../ref.md"', self.render(
            "- [see the spec](../ref.md) explains it\n"))


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class NestingTests(RendererCase):
    """Children indent under their parent instead of flattening beside it."""

    def test_a_nested_list_is_a_child_of_its_parent_item(self):
        html = self.render(
            "- parent one\n"
            "  - child a\n"
            "  - child b\n"
            "- parent two\n")
        self.assertEqual(self.items(html), ["parent one", "parent two"])
        self.assertRegex(html, r"parent one<ul><li>child a</li><li>child b</li></ul></li>")

    def test_the_nesting_closes_when_the_indent_returns(self):
        html = self.render(
            "- parent one\n"
            "  - child a\n"
            "- parent two\n")
        self.assertEqual(html.count("<ul>"), 2)
        self.assertEqual(html.count("</ul>"), 2)
        self.assertTrue(html.endswith("</ul>"))

    def test_a_wrapped_child_is_still_one_child(self):
        html = self.render(
            "- parent\n"
            "  - the child wraps across\n"
            "    two source lines\n")
        self.assertIn("<li>the child wraps across two source lines</li>", html)

    def test_deeper_nesting_degrades_rather_than_breaks(self):
        html = self.render(
            "- a\n  - b\n    - c\n- d\n")
        self.assertEqual(html.count("<ul>"), html.count("</ul>"))
        for text in ("a", "b", "c", "d"):
            self.assertIn(f"<li>{text}", html)

    def test_a_nested_ordered_list_under_a_bullet_keeps_its_tag(self):
        html = self.render("- parent\n  1. first\n  2. second\n")
        self.assertIn("<ol><li>first</li><li>second</li></ol>", html)


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class ReflowTests(RendererCase):
    """Prose wraps to the drawer, not to the author's editor."""

    def test_a_paragraph_has_no_hard_break(self):
        html = self.render(
            "The renderer is line-based and the task files are\n"
            "hard-wrapped, so almost every list on the board comes\n"
            "out wrong.\n")
        self.assertNotIn("<br>", html)
        self.assertIn("task files are hard-wrapped", html)

    def test_a_blockquote_reflows_too(self):
        html = self.render("&gt; a quoted line\n&gt; and its continuation\n"
                           .replace("&gt;", ">"))
        self.assertNotIn("<br>", html)
        self.assertIn("a quoted line and its continuation", html)

    def test_no_br_survives_anywhere_in_the_corpus(self):
        """Every card on the board plus AGENTS.md: the author's wrap column
        must not reach the browser."""
        docs = sorted(ROOT.glob("tasks/*/*.md")) + [ROOT / "AGENTS.md"]
        self.assertGreater(len(docs), 5, "no task files found to render")
        for doc in docs:
            with self.subTest(doc=doc.relative_to(ROOT).as_posix()):
                self.assertNotIn("<br>", self.render(
                    doc.read_text(encoding="utf-8")))


def source_bullets(src: str) -> int:
    """How many logical list items a document contains, counted from the
    source the way a reader counts them: markers only, fences skipped, and
    only in blocks that actually open with one."""
    marker = re.compile(r"^[ \t]*(?:[-*]|\d+\.)[ \t]+")
    total, fence = 0, False
    for block in re.split(r"\n{2,}", src):
        ticks = block.count("```")
        if fence or block.startswith("```"):
            fence = (ticks % 2 == 0) if fence else (ticks % 2 == 1)
            continue
        lines = block.rstrip().split("\n")
        if len(lines) >= 2 and re.match(r"^\s*\|.*\|\s*$", lines[0]) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[1]):
            continue                                   # a table, not a list
        if marker.match(lines[0]):
            total += sum(1 for l in lines if marker.match(l))
    return total


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class CorpusTests(RendererCase):
    """The whole board, not a fixture: one bullet per marker, no more."""

    def docs(self) -> list[Path]:
        found = sorted(ROOT.glob("tasks/*/*.md")) + [ROOT / "AGENTS.md"]
        self.assertGreater(len(found), 5, "no task files found to render")
        return found

    def test_every_document_renders_one_bullet_per_marker(self):
        """Before the fix a hard-wrapped item produced a bullet per source
        line; this is the acceptance criterion applied to every card."""
        for doc in self.docs():
            with self.subTest(doc=doc.relative_to(ROOT).as_posix()):
                src = doc.read_text(encoding="utf-8")
                self.assertEqual(self.render(src).count("<li"),
                                 source_bullets(src))

    def test_this_card_acceptance_list_is_checkboxes(self):
        """Task 41's own Acceptance section — the live case in the bug
        report — comes out as unticked boxes, no literal brackets."""
        cards = sorted(ROOT.glob("tasks/*/41-*.md"))   # whatever stage it sits in
        self.assertTrue(cards, "task 41's card is missing from the board")
        section = cards[0].read_text(encoding="utf-8").split("## Acceptance")[1]
        html = self.render(section.split("## Notes")[0].strip())
        self.assertEqual(html.count('<li class="tick">'), html.count("<li"))
        self.assertGreaterEqual(html.count("<li"), 8)
        items = self.items(html)
        for item in items:                        # the marker itself is consumed
            self.assertNotRegex(item, r"^\[[ xX]\]")
        self.assertIn("no bullet begins mid-sentence", items[0])


@unittest.skipUnless(NODE, "node is needed to run the page's own md()")
class UntouchedNeighboursTests(RendererCase):
    """Fences, tables, headings and rules kept working."""

    TREE = ("```\n"
            ".task-manager/\n"
            "├── AGENTS.md             ← This file\n"
            "│   ├── VERSION, board.py\n"
            "└── manager/\n"
            "```\n")

    def test_a_fenced_block_keeps_its_line_breaks(self):
        """The ASCII directory tree in AGENTS.md goes through the file
        viewer; every newline inside the fence has to survive."""
        html = self.render(self.TREE)
        body = re.search(r"<pre><code>(.*?)</code></pre>", html, re.S).group(1)
        self.assertEqual(body.rstrip("\n").split("\n"), [
            ".task-manager/",
            "├── AGENTS.md             ← This file",
            "│   ├── VERSION, board.py",
            "└── manager/",
        ])

    def test_a_fence_spanning_blank_lines_still_closes(self):
        """The fence state machine spans blocks — a blank line inside a
        fence must not end it, and a bullet inside must stay literal."""
        html = self.render("```\nfirst\n\n- not a bullet\n```\n\nafter\n")
        self.assertEqual(html.count("<pre>"), 1)
        self.assertNotIn("<li>", html)
        self.assertIn("<p>after</p>", html)

    def test_a_table_after_a_list_is_still_a_table(self):
        """Card 30's wrong/right table is the live case."""
        html = self.render(
            "- a bullet that wraps\n  onto a second line\n\n"
            "| Turn 1 says | bench actually |\n| --- | --- |\n"
            "| `bench.toml` | `manager/local/.env` |\n")
        self.assertIn("<table>", html)
        self.assertIn("<th>Turn 1 says</th>", html)
        self.assertIn("<td><code>bench.toml</code></td>", html)
        self.assertEqual(html.count("<li"), 1)

    def test_headings_and_rules_are_unchanged(self):
        html = self.render("## What to build\n\n---\n\n- item\n")
        self.assertIn("<h2>What to build</h2>", html)
        self.assertIn("<hr>", html)
        self.assertIn("<li>item</li>", html)

    def test_html_in_the_source_is_still_escaped(self):
        html = self.render("- an item with <script>alert(1)</script> in it\n")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class SourceInvariantTests(unittest.TestCase):
    """Always-run checks on board.html itself, for the case where node is
    not installed: the shape of the fix, not its output."""

    def test_md_no_longer_maps_physical_lines_to_items(self):
        """`lines.map(l => '<li>…')` is the bug; if it comes back, every
        wrapped item sprouts a phantom bullet again."""
        md = lift(r"^function md\(src\) \{\n.*?\n\}\n", "its md() renderer")
        self.assertNotRegex(md, r"lines\.map\([^)]*<li>")

    def test_paragraphs_and_blockquotes_do_not_emit_br(self):
        md = lift(r"^function md\(src\) \{\n.*?\n\}\n", "its md() renderer")
        self.assertNotIn("<br>", md,
                         "md() must reflow prose, not preserve the author's "
                         "wrap column")

    def test_the_task_list_glyph_has_its_own_css(self):
        """Colour only ever means state: done reads as --calm, open stays
        neutral, and neither reads as an alarm."""
        rule = re.search(r"#drawer \.dbody li\.tick\.on \.box\{([^}]*)\}", HTML)
        self.assertIsNotNone(rule, "board.html lost the ticked-box rule")
        self.assertIn("var(--calm)", rule.group(1))
        self.assertNotIn("var(--alarm)", HTML[HTML.index("li.tick"):
                                              HTML.index("li.tick") + 600])

    def test_the_renderer_is_still_dependency_free(self):
        """board.html makes no network requests; this stays a function in
        it, not a library."""
        self.assertNotRegex(HTML, r"<script[^>]+src=")
        self.assertNotIn("cdn.", HTML)


if __name__ == "__main__":
    unittest.main()
