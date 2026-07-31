"""The reference section: /reference/settings and the three contract
pages, in the 1c Logbook layout.

The other site suites are about slices — a heading renamed in AGENTS.md
stops the build. This one is about the promise a *settings* page makes,
which is stronger and easier to break quietly: the page is not written,
it is read out of `manager/core/.env.example`, so a key added there
appears here with nobody editing site/, and a key documented nowhere
fails the build rather than reaching the site bare.

The rest is the layout: the pinned console carries the page's own
entries and nothing it invented, and a reference page keeps every piece
of furniture an article has — the sidebar, the folded menus, prev/next
and "Edit this page".

    python3 -m unittest discover -s tests
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_site_build import (BUILDER, HAS_MARKDOWN_IT, REPO, SITE,
                                   ScratchCase, needs_renderer, run_build)

ENV_EXAMPLE = "manager/core/.env.example"

CONSOLE_LINE = re.compile(
    r'<a class="console-line" href="#([^"]+)">(.*?)</a>', re.S)
CONSOLE_KEY = re.compile(r'<span class="console-key">([^<]*)</span>')
CONSOLE_VALUE = re.compile(r'<span class="console-value">([^<]*)</span>')
HEADING_ID = re.compile(r'<h2 id="([^"]+)">(.*?)</h2>', re.S)
SIDE_HERE = re.compile(r'class="side-link side-here" href="([^"]+)"')
NAV = re.compile(r'<details class="menu">(.*?)</details>', re.S)
CONTENTS = re.compile(
    r'<details class="menu menu-contents">(.*?)</details>', re.S)


def settings(text: str) -> dict:
    """{name: value} for every NAME=value line in an env file, read the
    naive way on purpose — the point of comparing against it is that it
    shares no code with the builder's own parser."""
    found = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line.strip():
            continue
        name, _, value = line.partition("=")
        if name.strip() and name.strip() == name:
            found[name] = value
    return found


class BuiltSite(unittest.TestCase):
    """The real manifest, built once into a scratch directory."""

    @classmethod
    def setUpClass(cls):
        if not HAS_MARKDOWN_IT:
            raise unittest.SkipTest("markdown-it-py is not installed")
        cls.out = Path(tempfile.mkdtemp(prefix="bench-reference-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"site/build.py failed:\n{result.stdout}{result.stderr}")
        cls.manifest = json.loads(
            (SITE / "pages.json").read_text(encoding="utf-8"))
        cls.env = (REPO / ENV_EXAMPLE).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)

    def page(self, route: str) -> str:
        return BUILDER.target_for(self.out, route).read_text("utf-8")

    def entries(self) -> list:
        return [page for page in self.manifest["pages"]
                if page["layout"] == "reference"]


class TheSectionJoinsTheSite(BuiltSite):
    """Reference is a section beside Guides and Concepts — in the nav, in
    the sidebar and on the reading order the arrows walk."""

    def test_the_four_routes_are_all_there(self):
        """Named one by one rather than counted: a route quietly dropped
        from the manifest is exactly the failure this catches."""
        routes = {page["path"] for page in self.entries()}
        for route in ("/reference/settings/", "/reference/adapters/",
                      "/reference/driver/", "/reference/commands/"):
            self.assertIn(route, routes)
            self.assertTrue(BUILDER.target_for(self.out, route).is_file(),
                            f"{route} produced no page")

    def test_reference_is_a_section_of_its_own(self):
        names = [group["name"] for group in BUILDER.sections(self.manifest)]
        self.assertEqual(["Guides", "Concepts", "Reference"], names)

    def test_the_nav_reaches_it_from_a_concept_page(self):
        html = self.page("/concepts/stages/")
        self.assertIn('href="/reference/settings/">Reference</a>', html)

    def test_a_reference_page_marks_itself_in_the_sidebar(self):
        for entry in self.entries():
            here = SIDE_HERE.findall(self.page(entry["path"]))
            self.assertTrue(here, f'{entry["path"]} does not mark itself')
            self.assertEqual({entry["path"]}, set(here), entry["path"])

    def test_it_is_on_the_flow_the_arrows_walk(self):
        order = [page["path"] for page in BUILDER.flow(self.manifest)]
        for entry in self.entries():
            self.assertIn(entry["path"], order)
        self.assertEqual("/reference/commands/", order[-1],
                         "the reference section is not the end of the flow")


class TheSettingsPageIsTheEnvFile(BuiltSite):
    """Acceptance: every setting in .env.example, exactly once, with the
    default the file actually gives it."""

    def setUp(self):
        self.html = self.page("/reference/settings/")
        self.expected = settings(self.env)

    def test_it_reads_as_a_settings_file_rather_than_a_short_list(self):
        """Guards every test below from passing on an empty page."""
        self.assertGreater(len(self.expected), 15,
                           "the env example has almost no settings in it")
        self.assertIn("BOARD_PORT", self.expected)
        self.assertIn("BENCH_SOURCE", self.expected)

    def test_every_setting_appears_exactly_once_in_the_console(self):
        listed = [CONSOLE_KEY.search(line).group(1)
                  for _, line in CONSOLE_LINE.findall(self.html)]
        self.assertEqual(sorted(self.expected), sorted(listed),
                         "the console and the env file disagree about "
                         "which settings exist")
        self.assertEqual(len(listed), len(set(listed)),
                         "a setting is listed twice")

    def test_each_one_carries_the_default_the_file_gives_it(self):
        shown = {}
        for _, line in CONSOLE_LINE.findall(self.html):
            value = CONSOLE_VALUE.search(line)
            shown[CONSOLE_KEY.search(line).group(1)] = \
                value.group(1) if value else None
        for name, value in self.expected.items():
            self.assertEqual(value, shown[name],
                             f"{name}'s default on the page is not the "
                             f"one in {ENV_EXAMPLE}")

    def test_an_empty_default_is_shown_as_empty_rather_than_dropped(self):
        """BOARD_TITLE= is a default: the board falls back to the repo
        directory's name. A page that skipped the line would read as a
        setting with no default at all."""
        self.assertEqual("", self.expected["BOARD_TITLE"])
        self.assertIn('<span class="console-key">BOARD_TITLE</span>'
                      '<span class="console-sign">=</span>'
                      '<span class="console-value"></span>', self.html)

    def test_every_console_line_points_at_a_heading_on_the_page(self):
        anchors = {slug for slug, _ in HEADING_ID.findall(self.html)}
        targets = {slug for slug, _ in CONSOLE_LINE.findall(self.html)}
        self.assertTrue(targets)
        self.assertEqual(set(), targets - anchors,
                         "a console line links to an anchor that is not "
                         "on the page")

    def test_the_keys_are_grouped_the_way_the_file_groups_them(self):
        """The four model keys sit under one comment in the file, so they
        are one entry on the page — and the three headings below are the
        shape "grouped as that file groups them" takes."""
        headings = [text for _, text in HEADING_ID.findall(self.html)]
        self.assertIn("BOARD_PORT", headings)
        self.assertIn("BOARD_CLAUDE_BIN, BOARD_OPENCODE_BIN", headings)
        self.assertIn("BOARD_AGENT_MODEL, BOARD_AGENT_MODEL_WORK, "
                      "BOARD_AGENT_MODEL_ACT_PR, BOARD_AGENT_MODEL_REVIEW",
                      headings)

    def test_a_default_is_the_files_own_line_and_says_it_is_one(self):
        """The flag under each heading is the line copied out of the
        file, not a retyping of the value — and it is fenced as `env` so
        the stylesheet can draw a value you set differently from a
        terminal that printed something."""
        self.assertIn('<pre><code class="language-env">BOARD_AGENT_COMMANDS='
                      "python3 -m unittest\n</code></pre>", self.html)
        css = (SITE / "static" / "site.css").read_text("utf-8")
        self.assertIn("code.language-env", css,
                      "nothing in the stylesheet tells a default from a "
                      "code block")

    def test_the_documentation_is_the_files_own_comment(self):
        """Not a paraphrase written into site/: sentences out of the
        comment blocks, arriving as the markdown they were written as."""
        self.assertIn("Pinned by default so the URL is bookmarkable",
                      self.html)
        self.assertIn("a test runner missing from this list", self.html)
        self.assertIn("<code>board: &lt;number&gt; → &lt;stage&gt; "
                      "(&lt;name&gt;)</code>", self.html)

    def test_a_placeholder_in_a_comment_survives_as_text(self):
        """`<git user.name>` is a placeholder, and a markdown parser that
        honoured HTML would post it into the page as a tag and show
        nothing. Generated bodies are rendered with HTML off."""
        self.assertIn("&lt;git user.name&gt;", self.html)
        self.assertNotIn("<git user.name>", self.html)

    def test_a_comment_documenting_no_key_is_kept_as_a_remark(self):
        """The note about the `checks` file sits between two settings and
        belongs to neither. It stays on the page — it is documentation —
        as a blockquote rather than as the next key's description."""
        self.assertIn("<blockquote>", self.html)
        note = self.html.split("<blockquote>")
        self.assertTrue(
            any("definition-of-done check" in part for part in note),
            "the note about the checks file is not on the page")

    def test_edit_this_page_opens_the_file_itself(self):
        """There is no `from` heading to anchor at: the page is the whole
        file."""
        blob = self.manifest["site"]["blob_base"].rstrip("/") + "/"
        self.assertIn(f'href="{blob}{ENV_EXAMPLE}"', self.html)


class TheContractPagesAreSlices(BuiltSite):
    """Acceptance: slices of the contract files, not paraphrases."""

    def test_the_adapter_contract_is_the_adapters_readme(self):
        html = self.page("/reference/adapters/")
        self.assertIn("stdout is captured by the board as the job log",
                      html)
        self.assertIn("<code>work</code>", html)
        # The normalized event schema, indented in the README, arrives as
        # the code block it is.
        self.assertIn("<pre><code>", html)
        self.assertIn("&quot;v&quot;: 1", html)

    def test_the_driver_contract_is_agents_md(self):
        html = self.page("/reference/driver/")
        self.assertIn("refuse fast with a printed reason", html)
        self.assertIn("DRIVE URL", html)

    def test_the_command_contract_is_agents_md(self):
        html = self.page("/reference/commands/")
        self.assertIn("<code>CMD_WORKTREE</code>", html)
        self.assertIn("Commands arm on first click and run on the second",
                      html)

    def test_each_one_edits_at_the_section_it_was_cut_from(self):
        blob = self.manifest["site"]["blob_base"].rstrip("/") + "/"
        for entry in self.entries():
            if not entry.get("from"):
                continue
            wanted = (blob + entry["source"] + "#"
                      + BUILDER.github_anchor(entry["from"]))
            self.assertIn(f'href="{wanted}"', self.page(entry["path"]),
                          f'{entry["path"]}: "Edit this page" does not open '
                          f'{entry["source"]} at {entry["from"]}')

    def test_a_pages_console_is_its_own_headings(self):
        """The adapter page's entries are the contract's `###` headings,
        promoted — so the console is generated for a sliced page too,
        with no per-page authoring anywhere."""
        html = self.page("/reference/adapters/")
        anchors = dict(HEADING_ID.findall(html))
        lines = CONSOLE_LINE.findall(html)
        self.assertEqual(sorted(anchors), sorted(slug for slug, _ in lines))
        self.assertIn("run — execute one headless job to completion",
                      [CONSOLE_KEY.search(line).group(1) for _, line in lines],
                      "the console shows the heading's markdown backticks")


class TheLayoutKeepsEveryArticlesFurniture(BuiltSite):
    """A reader must not lose the nav, the contents or the arrows by
    walking into the reference section."""

    def test_the_three_columns_are_there(self):
        for entry in self.entries():
            html = self.page(entry["path"])
            for furniture in ('class="page-reference"', 'class="side"',
                              'class="prose"', 'class="gutter"',
                              'class="crumbs"', 'class="prose-lede"'):
                self.assertIn(furniture, html,
                              f'{entry["path"]} is missing {furniture}')

    def test_the_section_nav_is_folded_in_as_well(self):
        listed = [page for page in self.manifest["pages"]
                  if page.get("section")]
        for entry in self.entries():
            menu = NAV.search(self.page(entry["path"]))
            self.assertIsNotNone(menu, f'{entry["path"]} has no folded nav')
            for page in listed:
                self.assertIn(f'href="{page["path"]}"', menu.group(1),
                              f'{entry["path"]}\'s menu cannot reach '
                              f'{page["path"]}')

    def test_the_folded_contents_carries_the_anchors_the_console_does(self):
        """Below 1080px the whole gutter goes, console and all. The strip
        that replaces it has to reach everything the console did, or a
        phone loses the index entirely."""
        for entry in self.entries():
            html = self.page(entry["path"])
            strip = CONTENTS.search(html)
            self.assertIsNotNone(strip,
                                 f'{entry["path"]} has no contents strip')
            for slug, _ in CONSOLE_LINE.findall(html):
                self.assertIn(f'href="#{slug}"', strip.group(1),
                              f'{entry["path"]}\'s contents strip drops '
                              f"#{slug}")

    def test_the_arrows_are_rendered(self):
        for entry in self.entries():
            self.assertIn('<nav class="flow">', self.page(entry["path"]),
                          f'{entry["path"]} has no prev/next')

    def test_it_ships_no_script_of_its_own(self):
        for entry in self.entries():
            html = self.page(entry["path"])
            for tag in re.findall(r"<script\b[^>]*>", html, re.I):
                self.assertIn("cdn.usefathom.com", tag, entry["path"])


@needs_renderer
class ANewSettingReachesTheSiteByItself(ScratchCase):
    """The headline acceptance: add a key to .env.example with its
    comment, rebuild, and it is on the page — no edit to site/ anywhere.
    """

    def page(self) -> str:
        return (self.repo.out / "reference" / "settings"
                / "index.html").read_text("utf-8")

    def append(self, block: str) -> None:
        path = self.repo.root / ENV_EXAMPLE
        path.write_text(path.read_text("utf-8") + block, encoding="utf-8")

    def test_a_new_key_appears_with_its_default_and_its_comment(self):
        self.append("\n# How long the kettle boils for, in seconds. The\n"
                    "# board waits this long before it gives up.\n"
                    "BOARD_KETTLE_TIMEOUT=90\n")
        result = self.repo.build()
        self.assertEqual(0, result.returncode, result.stderr)

        html = self.page()
        self.assertIn('<h2 id="board-kettle-timeout">BOARD_KETTLE_TIMEOUT'
                      "</h2>", html)
        self.assertIn("How long the kettle boils for", html)
        self.assertIn('<span class="console-key">BOARD_KETTLE_TIMEOUT</span>'
                      '<span class="console-sign">=</span>'
                      '<span class="console-value">90</span>', html)

    def test_a_multi_line_comment_does_not_swallow_the_next_key(self):
        """The edge case named on the card: two entries in a row, the
        first with a three-line comment. The second keeps its own
        heading, its own default and its own description."""
        self.append("\n# One. This comment runs to three lines and it\n"
                    "# mentions BOARD_SECOND_KEY in passing, which is\n"
                    "# exactly how a parser gets this wrong.\n"
                    "BOARD_FIRST_KEY=first\n"
                    "\n"
                    "# Two. Its own comment, its own entry.\n"
                    "BOARD_SECOND_KEY=second\n")
        result = self.repo.build()
        self.assertEqual(0, result.returncode, result.stderr)

        html = self.page()
        self.assertIn('<h2 id="board-first-key">BOARD_FIRST_KEY</h2>', html)
        self.assertIn('<h2 id="board-second-key">BOARD_SECOND_KEY</h2>', html)
        first = html.split('<h2 id="board-first-key">')[1]
        first, second = first.split('<h2 id="board-second-key">')
        self.assertIn("This comment runs to three lines", first)
        self.assertIn("BOARD_FIRST_KEY=first", first)
        self.assertNotIn("BOARD_SECOND_KEY=second", first)
        self.assertIn("Its own comment, its own entry.", second)
        self.assertNotIn("This comment runs to three lines", second)

    def test_two_keys_under_one_comment_stay_one_entry(self):
        self.append("\n# A pair, documented together as the file does it.\n"
                    "BOARD_LEFT=l\n"
                    "BOARD_RIGHT=r\n")
        result = self.repo.build()
        self.assertEqual(0, result.returncode, result.stderr)

        html = self.page()
        self.assertIn("<h2 id=\"board-left-board-right\">BOARD_LEFT, "
                      "BOARD_RIGHT</h2>", html)
        for name in ("BOARD_LEFT", "BOARD_RIGHT"):
            self.assertIn(f'<a class="console-line" href="#board-left-'
                          f'board-right"><span class="console-key">{name}'
                          f"</span>", html)


@needs_renderer
class ADriftingEnvFileStopsTheBuild(ScratchCase):
    """A settings page that disagrees with the settings file is worse
    than no settings page, so every way they can disagree is a failure
    naming the route."""

    def append(self, block: str) -> None:
        path = self.repo.root / ENV_EXAMPLE
        path.write_text(path.read_text("utf-8") + block, encoding="utf-8")

    def test_a_key_documented_nowhere_fails_rather_than_being_skipped(self):
        self.append("\nBOARD_UNDOCUMENTED=1\n")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode,
                            "an undocumented setting built cleanly")
        self.assertIn("/reference/settings/", result.stderr)
        self.assertIn("BOARD_UNDOCUMENTED", result.stderr)
        self.assertFalse(self.repo.out.exists(),
                         "a failed build wrote pages anyway")

    def test_a_key_set_twice_fails(self):
        self.append("\n# Once.\nBOARD_PORT=1\n")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("BOARD_PORT", result.stderr)
        self.assertIn("twice", result.stderr)

    def test_a_line_that_is_neither_a_comment_nor_a_setting_fails(self):
        self.append("\nexport BOARD_PORT 26071\n")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(ENV_EXAMPLE, result.stderr)

    def test_a_missing_env_file_is_readable(self):
        (self.repo.root / ENV_EXAMPLE).unlink()
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(ENV_EXAMPLE, result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_an_env_file_with_no_settings_at_all_fails(self):
        (self.repo.root / ENV_EXAMPLE).write_text(
            "# Nothing but prose in here.\n", encoding="utf-8")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/reference/settings/", result.stderr)

    def test_renaming_the_command_contract_names_its_route(self):
        """The contract pages are slices, so they drift like every other
        slice does — loudly. The heading renamed here is one exactly one
        entry names: "## Drives" would be reported against the concept
        page above it, which also ends there."""
        self.repo.edit("AGENTS.md", "## Local commands", "## Chores")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/reference/commands/", result.stderr)
        self.assertIn("## Local commands", result.stderr)

    def test_renaming_where_the_driver_page_ends_names_its_route(self):
        self.repo.edit("AGENTS.md", "## The activity bar and the archive",
                       "## The activity bar")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/reference/driver/", result.stderr)
        self.assertIn("## The activity bar and the archive", result.stderr)

    def test_renaming_the_adapter_contract_heading_names_its_route(self):
        self.repo.edit("manager/core/adapters/README.md", "## The contract",
                       "## The interface")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/reference/adapters/", result.stderr)


@needs_renderer
class TheManifestChecksAGeneratedPage(ScratchCase):
    """`generate` is a second way to make a body, so it gets the same
    treatment `from` does: a mistake in pages.json is a build failure,
    not a surprise on the site."""

    def one(self, page: dict):
        self.repo.pages(page)
        return self.repo.build()

    def test_an_unknown_generator_lists_the_ones_that_exist(self):
        result = self.one({
            "path": "/x/", "title": "X", "layout": "reference",
            "section": "Reference", "source": ENV_EXAMPLE,
            "generate": "flags"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("flags", result.stderr)
        self.assertIn("settings", result.stderr)

    def test_a_generator_without_a_source_is_refused(self):
        result = self.one({
            "path": "/x/", "title": "X", "layout": "reference",
            "section": "Reference", "source": None, "generate": "settings"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("generate", result.stderr)

    def test_a_generated_page_may_not_also_be_a_slice(self):
        result = self.one({
            "path": "/x/", "title": "X", "layout": "reference",
            "section": "Reference", "source": ENV_EXAMPLE,
            "generate": "settings", "from": "## Anything"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not a slice", result.stderr)

    def test_a_source_with_neither_still_says_from(self):
        result = self.one({
            "path": "/x/", "title": "X", "layout": "reference",
            "section": "Reference", "source": ENV_EXAMPLE})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("from", result.stderr)


class TheEnvParserOnItsOwn(unittest.TestCase):
    """The block rule, on strings rather than on the shipped file: a
    blank line separates entries, a comment belongs to the keys under it,
    and a bare `#` is a paragraph break inside one."""

    def blocks(self, text: str) -> list:
        return BUILDER.env_blocks(text, "test.env")

    def test_a_comment_binds_to_every_key_under_it(self):
        blocks = self.blocks("# One comment.\nA=1\nB=2\n")
        self.assertEqual(1, len(blocks))
        self.assertEqual(["One comment."], blocks[0]["comment"])
        self.assertEqual([("A", "1", "A=1"), ("B", "2", "B=2")],
                         blocks[0]["keys"])

    def test_a_blank_line_ends_an_entry(self):
        blocks = self.blocks("# One.\nA=1\n\n# Two.\nB=2\n")
        self.assertEqual([["One."], ["Two."]],
                         [block["comment"] for block in blocks])

    def test_a_comment_straight_after_a_key_opens_the_next_entry(self):
        blocks = self.blocks("# One.\nA=1\n# Two.\nB=2\n")
        self.assertEqual(2, len(blocks))
        self.assertEqual([("B", "2", "B=2")], blocks[1]["keys"])

    def test_a_bare_hash_is_a_paragraph_break(self):
        blocks = self.blocks("# One.\n#\n# Two.\nA=1\n")
        self.assertEqual(["One.", "", "Two."], blocks[0]["comment"])
        self.assertEqual(["One.", "Two."],
                         BUILDER.comment_paragraphs(blocks[0]["comment"]))

    def test_a_comment_with_no_keys_is_a_block_of_its_own(self):
        blocks = self.blocks("# Just a note.\n\nA=1\n")
        self.assertEqual([], blocks[0]["keys"])
        self.assertEqual([], blocks[1]["comment"])

    def test_a_value_containing_an_equals_sign_keeps_it(self):
        self.assertEqual([("A", "x=y", "A=x=y")],
                         self.blocks("# c\nA=x=y\n")[0]["keys"])

    def test_a_value_with_spaces_is_taken_verbatim(self):
        self.assertEqual([("A", "python3 -m unittest",
                           "A=python3 -m unittest")],
                         self.blocks("# c\nA=python3 -m unittest\n")[0]["keys"])


if __name__ == "__main__":
    unittest.main()
