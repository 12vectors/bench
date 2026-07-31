"""The guides and concept pages: the middle of the site, and the half of
the promise tests/test_site_build.py does not cover.

That file is about the generator — a renamed heading stops the build, a
dead link stops the build. This one is about what a reader actually gets
once it has run: the 1a Harbour furniture around each slice (sidebar,
on-this-page, prev/next, "Edit this page"), and the three markdown
constructs the sources really contain rendering as themselves rather than
as escaped text.

The rule underneath all of it: no page body is authored twice. A page
authors its title and one lede sentence; everything else on it was cut
out of AGENTS.md or README.md by site/pages.json.

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

# The two the layout owes a reader at the foot of every article.
FLOW_LINK = re.compile(
    r'class="flow-link flow-(prev|next)" href="([^"]+)"')
HEADING_ID = re.compile(r'<h2 id="([^"]+)"')
TOC_LINK = re.compile(r'class="toc-link" href="#([^"]+)"')
SIDE_HERE = re.compile(r'class="side-link side-here" href="([^"]+)"')
GUTTER_LINK = re.compile(r'class="gutter-link" href="([^"]+)"')
DOOR = re.compile(r'class="door[^"]*" href="([^"]+)"')
LEDE = re.compile(r'<p class="prose-lede">(.*?)</p>', re.S)


class BuiltSite(unittest.TestCase):
    """The real manifest, built once into a scratch directory."""

    @classmethod
    def setUpClass(cls):
        if not HAS_MARKDOWN_IT:
            raise unittest.SkipTest("markdown-it-py is not installed")
        cls.out = Path(tempfile.mkdtemp(prefix="bench-pages-")).resolve()
        cls.result = run_build(REPO, cls.out)
        if cls.result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"site/build.py failed:\n{cls.result.stdout}"
                f"{cls.result.stderr}")
        cls.manifest = json.loads(
            (SITE / "pages.json").read_text(encoding="utf-8"))
        cls.flow = BUILDER.flow(cls.manifest)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)

    def page(self, route: str) -> str:
        return BUILDER.target_for(self.out, route).read_text("utf-8")

    def articles(self) -> list:
        """Every entry on the flow — the guides and the concepts, which
        are exactly the pages this task built."""
        return list(self.flow)


class EveryRouteRendersInTheArticleLayout(BuiltSite):
    """Acceptance: the routes render in 1a, and every body on them is a
    slice rather than something a person typed into the site."""

    def test_the_seven_concepts_and_the_install_guide_are_all_there(self):
        """Named one by one rather than counted: a route quietly dropped
        from the manifest is exactly the failure this catches."""
        routes = {page["path"] for page in self.articles()}
        for route in ("/guides/install/",
                      "/concepts/stages/",
                      "/concepts/task-files/",
                      "/concepts/claiming-a-card/",
                      "/concepts/agents-on-the-board/",
                      "/concepts/pull-requests/",
                      "/concepts/team-mode/",
                      "/concepts/three-layer-law/",
                      "/concepts/adapters/"):
            self.assertIn(route, routes)
            self.assertTrue(BUILDER.target_for(self.out, route).is_file(),
                            f"{route} produced no page")

    def test_each_one_is_the_three_column_layout(self):
        for entry in self.articles():
            html = self.page(entry["path"])
            self.assertEqual("article", entry["layout"], entry["path"])
            for furniture in ('class="page-article"', 'class="side"',
                              'class="prose"', 'class="gutter"',
                              'class="crumbs"'):
                self.assertIn(furniture, html,
                              f'{entry["path"]} is missing {furniture}')

    def test_no_body_is_authored_twice(self):
        """Every article names a source file and a heading to cut from.
        The lede is the single exception, and it is one sentence in the
        manifest — not a body."""
        for entry in self.articles():
            self.assertTrue(entry.get("source"),
                            f'{entry["path"]} has no source')
            self.assertTrue(entry.get("from"),
                            f'{entry["path"]} has no from heading')

    def test_the_lede_is_present_and_is_the_manifests_own_sentence(self):
        for entry in self.articles():
            found = LEDE.search(self.page(entry["path"]))
            self.assertIsNotNone(found, f'{entry["path"]} has no lede')
            wanted = entry.get("lede") or entry["description"]
            self.assertEqual(wanted.strip(),
                             found.group(1).replace("&#x27;", "'").strip())


class TheSidebarAndTheContentsFollowThePage(BuiltSite):
    """The left rail says where you are in the site; the right rail says
    where you are in the page. Neither is authored: one is the manifest,
    the other is the promoted slice's own h2s."""

    def test_the_sidebar_marks_exactly_the_page_you_are_on(self):
        for entry in self.articles():
            here = SIDE_HERE.findall(self.page(entry["path"]))
            self.assertEqual([entry["path"]], here,
                             f'{entry["path"]} does not mark itself in the '
                             f"sidebar")

    def test_the_sidebar_lists_every_other_page_too(self):
        html = self.page("/concepts/stages/")
        for entry in self.articles():
            self.assertIn(f'href="{entry["path"]}"', html,
                          f'the sidebar has no link to {entry["path"]}')

    def test_the_header_nav_marks_the_section(self):
        self.assertIn('class="nav-link nav-here"',
                      self.page("/concepts/stages/"))

    def test_on_this_page_is_the_bodys_own_h2s_in_order(self):
        """Not a subset and not a superset: the same anchors, the same
        order. A slice that grows a sub-heading grows a contents entry
        with nobody editing the site."""
        for entry in self.articles():
            html = self.page(entry["path"])
            self.assertEqual(HEADING_ID.findall(html), TOC_LINK.findall(html),
                             f'{entry["path"]}: the contents list and the '
                             f"headings disagree")

    def test_a_page_with_sub_headings_really_has_a_contents_list(self):
        """Guards the test above against passing on two empty lists."""
        html = self.page("/concepts/stages/")
        self.assertIn("On this page", html)
        self.assertIn('href="#backlog"', html)
        self.assertIn('href="#moving-a-task"', html)


class PrevAndNextWalkTheFlow(BuiltSite):
    """The arrows follow the sidebar's order, because a reader who used
    one and then the other must not be sent somewhere else."""

    def links(self, route: str) -> dict:
        return dict((direction, target) for direction, target
                    in FLOW_LINK.findall(self.page(route)))

    def test_each_page_points_at_its_neighbours(self):
        order = [entry["path"] for entry in self.flow]
        for index, route in enumerate(order):
            found = self.links(route)
            self.assertEqual(order[index - 1] if index else None,
                             found.get("prev"), f"{route}: wrong previous")
            self.assertEqual(order[index + 1] if index + 1 < len(order)
                             else None, found.get("next"),
                             f"{route}: wrong next")

    def test_the_ends_of_the_flow_have_one_arrow_each(self):
        first, last = self.flow[0]["path"], self.flow[-1]["path"]
        self.assertNotIn("prev", self.links(first))
        self.assertIn("next", self.links(first))
        self.assertIn("prev", self.links(last))
        self.assertNotIn("next", self.links(last))

    def test_an_absent_neighbour_keeps_its_slot(self):
        """`next →` sits on the right on the first page as on every
        other, which is a spacer in the markup rather than a rule in the
        stylesheet."""
        html = self.page(self.flow[0]["path"])
        flow = html[html.index('<nav class="flow">'):]
        self.assertLess(flow.index('<span class="spacer">'),
                        flow.index("flow-link"))

    def test_the_landing_page_and_the_404_are_not_on_the_flow(self):
        """They have no section, so they are not steps in a reading
        order — and an article that linked "previous: not found" would be
        a strange thing to ship."""
        off = [page["path"] for page in self.manifest["pages"]
               if not page.get("section")]
        self.assertEqual({"/", "/404.html"}, set(off))
        for route in off:
            self.assertNotIn('<nav class="flow">', self.page(route))


class EditThisPageOpensTheSection(BuiltSite):
    """A reader who spots a mistake has to land on the file that is
    actually wrong — and, on a 700-line brief, at the section that is."""

    def test_it_names_the_source_file_and_its_section(self):
        blob = self.manifest["site"]["blob_base"].rstrip("/") + "/"
        for entry in self.articles():
            wanted = (blob + entry["source"] + "#"
                      + BUILDER.github_anchor(entry["from"]))
            self.assertIn(f'href="{wanted}"', self.page(entry["path"]),
                          f'{entry["path"]}: "Edit this page" does not open '
                          f'{entry["source"]} at {entry["from"]}')

    def test_the_anchor_is_the_one_github_gives_that_heading(self):
        """Spot-checked against the real headings rather than only
        against the function that made them."""
        self.assertEqual("claiming-a-card",
                         BUILDER.github_anchor("## Claiming a card"))
        self.assertEqual("the-three-layer-law",
                         BUILDER.github_anchor("## The three-layer law"))
        self.assertEqual("install-into-a-repo",
                         BUILDER.github_anchor("## Install into a repo"))
        self.assertEqual("state-syncs-reactions-dont",
                         BUILDER.github_anchor("### State syncs; reactions "
                                               "don't"))

    def test_the_gutter_also_offers_the_issue_tracker(self):
        links = GUTTER_LINK.findall(self.page("/concepts/stages/"))
        self.assertIn(self.manifest["site"]["issues_url"], links)

    def test_the_authored_pages_point_at_the_repository_instead(self):
        """The landing page is not a slice, so there is no section to
        send anyone to."""
        for entry in self.manifest["pages"]:
            if entry.get("source"):
                continue
            self.assertNotIn("#", self.page(entry["path"]).split(
                'class="gutter-link" href="')[-1].split('"')[0])


class TheDoorsOpenOntoArticles(BuiltSite):
    """Task 33 put six doors on the landing page. This is the other end of
    them."""

    def test_every_door_lands_on_a_page_in_the_flow(self):
        routes = {entry["path"] for entry in self.flow}
        doors = DOOR.findall(self.page("/"))
        self.assertEqual(6, len(doors))
        for door in doors:
            self.assertIn(door, routes,
                          f"the door to {door} opens onto nothing")


@needs_renderer
class MarkdownComesOutAsMarkup(BuiltSite):
    """The edge case: a table, a fenced code block and a nested list have
    to render as themselves. The first two are in the repo's own slices —
    the header-field table in "Task file format", the command blocks in
    the install guide and the stage diagram in "Stages". A nested list is
    not, so ARenderedSliceKeepsItsShape below builds one on purpose rather
    than pretending this suite covers it."""

    def test_a_table_renders_as_a_table(self):
        html = self.page("/concepts/task-files/")
        self.assertIn("<table>", html)
        self.assertIn("<th>Field</th>", html)
        self.assertIn("<td><strong>Status</strong></td>", html)
        self.assertNotIn("| Field |", html)

    def test_a_fenced_block_renders_as_a_code_block(self):
        install = self.page("/guides/install/")
        self.assertIn("<pre><code", install)
        self.assertIn("mkdir .task-manager", install)
        self.assertIn("backlog → to-do → in-progress → review → done",
                      self.page("/concepts/stages/"))

    def test_a_fenced_heading_is_not_mistaken_for_a_heading(self):
        """"Task file format" fences a task file starting `# Task title`.
        It has to arrive as code, and it must not have sliced the page."""
        html = self.page("/concepts/task-files/")
        self.assertIn("# Task title", html)
        self.assertNotIn("<h1>Task title</h1>", html)
        self.assertIn("**Depends on:** 03, 05", html)


@needs_renderer
class ARenderedSliceKeepsItsShape(ScratchCase):
    """All three constructs in one slice, so the renderer is tested on the
    shapes rather than on the sections that happen to have them today."""

    BODY = """
| Stage | What it means |
| --- | --- |
| `review/` | built, not yet trusted |

```bash
./.task-manager/start.sh
```

- the board
  - narrates moves
  - opens PRs
- and never merges
"""

    def test_a_table_a_fence_and_a_nested_list_all_survive(self):
        (self.repo.root / "SOURCE.md").write_text(
            f"# Doc\n\n## Section\n{self.BODY}\n", encoding="utf-8")
        self.repo.pages({
            "path": "/shapes/", "title": "Shapes", "layout": "article",
            "section": "Concepts", "description": "one of each.",
            "source": "SOURCE.md", "from": "## Section",
        })
        result = self.repo.build()
        self.assertEqual(0, result.returncode, result.stderr)

        html = (self.repo.out / "shapes" / "index.html").read_text("utf-8")
        self.assertIn("<th>Stage</th>", html)
        self.assertIn("<code>review/</code>", html)
        self.assertIn("<pre><code", html)
        self.assertIn("start.sh", html)
        # The nested list: a <ul> inside an <li>, not two flat lists.
        self.assertRegex(html, r"<li>the board\s*<ul>")
        self.assertIn("<li>opens PRs</li>", html)


@needs_renderer
class DriftOnARealConceptPage(ScratchCase):
    """Inherited from task 31 and worth asserting on a page that ships:
    a section renamed in AGENTS.md stops the build, naming the route."""

    def test_renaming_task_file_format_names_its_route(self):
        self.repo.edit("AGENTS.md", "## Task file format",
                       "## The task file")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode,
                            "a renamed section built cleanly")
        self.assertIn("/concepts/task-files/", result.stderr)
        self.assertIn("## Task file format", result.stderr)
        self.assertFalse(self.repo.out.exists(),
                         "a failed build wrote pages anyway")

    def test_renaming_agent_adapters_names_its_route(self):
        self.repo.edit("AGENTS.md", "## Agent adapters", "## Adapters")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/concepts/adapters/", result.stderr)


if __name__ == "__main__":
    unittest.main()
