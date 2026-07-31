"""The docs design is drawn at a fixed 1180px, and the site's first
traffic is a link pasted into a chat and opened on a phone. This file is
about what happens below the design's width — and, just as much, about
what does not happen at it.

Four promises, each mechanised below:

- **Desktop is the design.** Every media query in the stylesheet is a
  max-width at or below 1080px, and the two folded strips are
  display:none in the base sheet. Nothing here can change the 1180px
  rendering the design defines.
- **A column that goes away hands back a way to reach it.** The section
  nav and the "on this page" list are written twice — the column and a
  <details> strip carrying the same links — and the step that hides each
  column is the step that shows its strip.
- **Only a code block or a table scrolls sideways.** A table becomes its
  own scroller with an edge that says there is more; a long unbroken
  token breaks or scrolls inside `pre` rather than widening the page.
- **No script.** One menu that opens and closes is a <details>; the site
  ships no JavaScript at all, and this is where that stays true.

    python3 -m unittest discover -s tests
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_site_build import (BUILDER, REPO, SITE, ScratchCase,
                                   needs_renderer, run_build)

CSS = SITE / "static" / "site.css"
TEMPLATES = SITE / "templates"

# The design's own frame. Nothing in the stylesheet may take effect at or
# above it, which is what "the desktop rendering is unchanged" means in a
# form a test can check.
DESIGN_WIDTH = 1180

# The step at which each column folds into its strip, and the strip it
# hands over to. Read as: at this width the column is gone and the
# <details> is there instead.
FOLDS = [
    ("1080px", ".gutter", ".menu-contents"),   # "on this page"
    ("760px", ".side", ".menu"),               # the section nav
]

# Everything a finger has to hit, once the layout is a phone's. Links
# inside running prose are deliberately not here: a line of text cannot
# be 44px tall and still be a line of text.
TAPPABLE = [".wordmark", ".nav-link", ".button", ".side-link", ".toc-link",
            ".strip-link", ".footer-repo"]


def stylesheet() -> str:
    """site.css with its comments removed — every test here reads
    declarations, and a comment is prose about them."""
    return re.sub(r"/\*.*?\*/", "", CSS.read_text("utf-8"), flags=re.S)


def media_blocks(css: str) -> list:
    """[(condition, body, (start, end))] for every @media, matched on
    braces rather than a regex, because the body of one is full of
    them. The span is what lets base_sheet cut the block back out."""
    out = []
    for opener in re.finditer(r"@media([^{]+)\{", css):
        depth, index = 1, opener.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        out.append((opener.group(1).strip(), css[opener.end():index - 1],
                    (opener.start(), index)))
    return out


def base_sheet(css: str) -> str:
    """The stylesheet with every @media block cut out: what a browser at
    the design's width is left with."""
    kept, cursor = [], 0
    for _, _, (start, end) in media_blocks(css):
        kept.append(css[cursor:start])
        cursor = end
    kept.append(css[cursor:])
    return "".join(kept)


def rules(body: str) -> dict:
    """{selector: its declarations} for one flat block of CSS. A grouped
    selector is recorded under each of its parts, and a selector written
    twice accumulates — which is how the cascade reads it."""
    found = {}
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
        for one in selectors.split(","):
            key = " ".join(one.split())
            if key.startswith("@") or not key:
                continue
            found[key] = found.get(key, "") + declarations
    return found


def widths(condition: str) -> list:
    """The px widths a media condition names."""
    return [float(value) for value in
            re.findall(r"max-width\s*:\s*([\d.]+)px", condition)]


def block_at(css: str, width: str) -> dict:
    """The rules of the @media block for `max-width:<width>`."""
    for condition, body, _ in media_blocks(css):
        if f"max-width:{width}" in condition.replace(" ", ""):
            return rules(body)
    raise AssertionError(f"the stylesheet has no max-width:{width} block")


class TheDesignsWidthIsUntouched(unittest.TestCase):
    """Desktop is the design; this is what happens below it. Both halves
    of that sentence are checkable, and the second is the dangerous one:
    a rule that leaks upwards redraws a layout nobody asked to redraw."""

    def setUp(self):
        self.css = stylesheet()

    def test_every_query_is_a_max_width_below_the_design(self):
        for condition, _, _ in media_blocks(self.css):
            if "width" not in condition:
                continue  # prefers-reduced-motion, which is not a size
            self.assertNotIn("min-width", condition,
                             f"@media{condition} turns something on as the "
                             f"screen gets wider")
            found = widths(condition)
            self.assertTrue(found, f"@media{condition} names no max-width")
            for value in found:
                self.assertLess(
                    value, DESIGN_WIDTH,
                    f"@media{condition} takes effect at the design's width")

    def test_the_folded_strips_do_not_render_at_the_design_width(self):
        """`.menu` covers both — the contents strip carries both classes
        — so the base sheet hides the pair in one declaration."""
        base = rules(base_sheet(self.css))
        self.assertIn("display:none", base.get(".menu", "").replace(" ", ""),
                      "the folded menus are not hidden by default")

    def test_the_lede_survives_the_strip_between_it_and_the_title(self):
        """The contents strip is a sibling between the h1 and the body's
        first paragraph even when it is display:none, so `h1 + p` alone
        would silently demote the lede on every article page."""
        base = rules(base_sheet(self.css))
        self.assertIn(".prose .menu-contents + p", base)
        self.assertIn(".prose h1 + p", base)


class EachColumnHandsBackAStrip(unittest.TestCase):
    """"Collapsed is fine, absent is not" — so the step that hides a
    column is the step that shows the <details> replacing it."""

    def setUp(self):
        self.css = stylesheet()

    def test_the_step_that_hides_a_column_shows_its_menu(self):
        for width, column, strip in FOLDS:
            block = block_at(self.css, width)
            self.assertIn("display:none", block.get(column, "").replace(" ", ""),
                          f"{column} is not hidden at {width}")
            self.assertIn("display:block", block.get(strip, "").replace(" ", ""),
                          f"{strip} does not appear where {column} goes")

    def test_a_menu_row_is_a_tap_target_at_every_width_it_shows(self):
        """The contents strip appears at 1080px, above the step where the
        tap-target rules live, so the panel sizes its own rows."""
        base = rules(base_sheet(self.css))
        for selector in (".menu-panel .side-link", ".menu-panel .toc-link"):
            self.assertIn("min-height:var(--tap)",
                          base.get(selector, "").replace(" ", ""),
                          f"{selector} is not a tap target")


class TapTargetsAreFingerSized(unittest.TestCase):
    def setUp(self):
        self.css = stylesheet()

    def test_the_size_is_a_token_and_it_is_44px(self):
        base = rules(base_sheet(self.css))
        self.assertIn("--tap:44px", base.get(":root", "").replace(" ", ""))

    def test_everything_a_finger_hits_is_at_least_that_tall(self):
        block = block_at(self.css, "760px")
        for selector in TAPPABLE:
            self.assertIn("min-height:var(--tap)",
                          block.get(selector, "").replace(" ", ""),
                          f"{selector} is smaller than a fingertip on a phone")


class TheTypeScaleSteps(unittest.TestCase):
    """"A smaller step on narrow screens without losing the Zilla Slab
    display voice" — so the sizes come down and the family does not."""

    def setUp(self):
        self.css = stylesheet()
        self.base = rules(base_sheet(self.css))[":root"]

    def token(self, declarations: str, name: str):
        found = re.search(rf"{name}\s*:\s*([\d.]+)px", declarations)
        return float(found.group(1)) if found else None

    def test_the_display_sizes_come_down_at_each_step(self):
        previous = {name: self.token(self.base, name)
                    for name in ("--t-hero", "--t-title")}
        for width in ("760px", "480px"):
            root = block_at(self.css, width).get(":root", "")
            for name, was in list(previous.items()):
                now = self.token(root, name)
                self.assertIsNotNone(
                    now, f"{name} is not stepped down at {width}")
                self.assertLess(now, was,
                                f"{name} does not get smaller at {width}")
                previous[name] = now

    def test_the_display_family_is_never_redefined(self):
        """The step is the size. Zilla Slab is the voice, at every
        width — a phone gets a smaller headline, not a different one."""
        for condition, body, _ in media_blocks(self.css):
            self.assertNotIn("--display:", body.replace(" ", ""),
                             f"@media{condition} changes the display face")


class OnlyCodeAndTablesScrollSideways(unittest.TestCase):
    def setUp(self):
        self.css = stylesheet()

    def test_a_code_block_is_its_own_scroller(self):
        base = rules(base_sheet(self.css))
        self.assertIn("overflow-x:auto",
                      base.get(".prose pre", "").replace(" ", ""))

    def test_a_table_becomes_its_own_scroller_with_an_edge(self):
        """display:block is what makes the table element a scroll
        container at all; the local/scroll pair is what makes its edge
        shadow appear only while there is more to the right."""
        table = block_at(self.css, "1080px").get(".prose table", "")
        flat = table.replace(" ", "")
        self.assertIn("display:block", flat)
        self.assertIn("overflow-x:auto", flat)
        self.assertIn("background-attachment:local,local,scroll,scroll", flat)

    def test_an_unbreakable_token_in_running_text_breaks(self):
        block = block_at(self.css, "1080px")
        self.assertIn("overflow-wrap:anywhere",
                      block.get(".prose :not(pre) > code", "").replace(" ", ""))

    def test_an_image_can_never_widen_the_page(self):
        base = rules(base_sheet(self.css))
        self.assertIn("max-width:100%",
                      base.get(".prose img", "").replace(" ", ""))


class EveryTemplateSaysHowWideItIs(unittest.TestCase):
    """Without the viewport meta a phone renders the page at 980px and
    scales it down, which makes every rule below a fiction."""

    def test_each_layout_declares_the_viewport(self):
        for template in sorted(TEMPLATES.glob("*.html")):
            markup = template.read_text("utf-8")
            self.assertIn('name="viewport"', markup,
                          f"{template.name} has no viewport meta")
            self.assertIn("width=device-width", markup, template.name)


@needs_renderer
class TheBuiltPagesCarryTheMenus(unittest.TestCase):
    """The stylesheet's half of this is above; here is the markup's.
    Built pages, not templates — a strip the builder fills with nothing
    is the failure that would look fine in a template."""

    NAV = re.compile(r'<details class="menu">(.*?)</details>', re.S)
    CONTENTS = re.compile(
        r'<details class="menu menu-contents">(.*?)</details>', re.S)

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="bench-phone-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(f"site/build.py failed:\n{result.stderr}")
        cls.manifest = json.loads((SITE / "pages.json").read_text("utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def articles(self):
        for entry in self.manifest["pages"]:
            if entry["layout"] == "article":
                yield entry, BUILDER.target_for(
                    self.out, entry["path"]).read_text("utf-8")

    def test_every_article_carries_the_section_nav_folded(self):
        listed = [page for page in self.manifest["pages"] if page.get("section")]
        for entry, html in self.articles():
            menu = self.NAV.search(html)
            self.assertIsNotNone(menu, f'{entry["path"]} has no folded nav')
            for page in listed:
                self.assertIn(f'href="{page["path"]}"', menu.group(1),
                              f'{entry["path"]}\'s menu cannot reach '
                              f'{page["path"]}')

    def test_the_folded_contents_lists_what_the_gutter_lists(self):
        for entry, html in self.articles():
            strip = self.CONTENTS.search(html)
            self.assertIsNotNone(strip,
                                 f'{entry["path"]} has no contents strip')
            for slug in re.findall(r'<h2 id="([^"]+)"', html):
                self.assertIn(f'href="#{slug}"', strip.group(1),
                              f'{entry["path"]}\'s contents strip drops '
                              f'#{slug}')

    def test_at_least_one_page_proves_the_strip_is_not_empty(self):
        """Guards the test above from passing on a page that happens to
        have no h2s at all."""
        filled = [entry["path"] for entry, html in self.articles()
                  if "toc-link" in self.CONTENTS.search(html).group(1)]
        self.assertTrue(filled, "no article page's contents strip has links")

    def test_the_menus_need_no_script(self):
        """One menu that opens and closes is a <details>. The site ships
        no JavaScript of its own — the only script on a page is the
        third-party analytics tag, which no menu, link or word depends
        on, and the CSP in site/root/_headers names it explicitly."""
        self.assertEqual([], sorted(self.out.rglob("*.js")))
        for entry in self.manifest["pages"]:
            html = BUILDER.target_for(self.out, entry["path"]).read_text("utf-8")
            for tag in re.findall(r"<script\b[^>]*>", html, re.I):
                self.assertIn("cdn.usefathom.com", tag,
                              f'{entry["path"]} loads a script of its own')

    def test_every_built_page_declares_the_viewport(self):
        for entry in self.manifest["pages"]:
            html = BUILDER.target_for(self.out, entry["path"]).read_text("utf-8")
            self.assertIn(
                '<meta name="viewport" content="width=device-width, '
                'initial-scale=1">', html, entry["path"])


@needs_renderer
class TheHazardsInGeneratedMarkdown(ScratchCase):
    """The two shapes a slice of AGENTS.md can take that no amount of
    layout CSS reflows: a table wider than a phone, and a token longer
    than one. Both have to reach the page intact — the container is what
    scrolls, not the page — so this builds them rather than trusting
    that no source file has one yet."""

    URL = ("https://github.com/12vectors/bench/releases/download/"
           "v0.2-alpha/bench-0.2-alpha.tar.gz")

    def build_one(self, body: str) -> str:
        (self.repo.root / "SOURCE.md").write_text(
            f"# Doc\n\n## Section\n\n{body}\n", encoding="utf-8")
        self.repo.pages({
            "path": "/hazard/", "title": "Hazard", "layout": "article",
            "section": "Concepts", "source": "SOURCE.md",
            "from": "## Section",
        })
        result = self.repo.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.repo.out / "hazard" / "index.html").read_text("utf-8")

    def test_a_wide_table_reaches_the_page_as_a_table(self):
        """It stays a <table>: the scrolling container is the table
        element itself (see the stylesheet's max-width:1080px block), so
        nothing in the build has to wrap it and nothing in the markup
        can forget to."""
        html = self.build_one(
            "| Setting | Default | What it does |\n"
            "| --- | --- | --- |\n"
            "| `BOARD_PORT` | `26071` | the port the board serves on |\n"
            "| `BOARD_SYNC` | `0` | origin/main is the truth, every "
            "board a replica |\n")
        self.assertIn("<table>", html)
        self.assertIn("<code>BOARD_SYNC</code>", html)

    def test_a_long_token_in_a_code_block_stays_in_the_code_block(self):
        html = self.build_one(f"```\ncurl -fsSL {self.URL} | tar xz\n```")
        fence = re.search(r"<pre>(.*?)</pre>", html, re.S)
        self.assertIsNotNone(fence, "the code block did not survive")
        self.assertIn(self.URL, fence.group(1))

    def test_a_long_token_in_running_text_stays_inline_code(self):
        html = self.build_one(f"Fetch it from `{self.URL}` and untar it.")
        self.assertIn(f"<code>{self.URL}</code>", html)


if __name__ == "__main__":
    unittest.main()
