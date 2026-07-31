"""site/build.py generates the minisite from the repo's own markdown, so
the failure that matters most is the quiet one: a section renamed in
AGENTS.md and a page that still builds, now empty. Every test here is
about that promise — the build stops, loudly, naming the route and the
heading — plus the two things the built pages owe a reader: real content
and not one third-party request.

    python3 -m unittest discover -s tests
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
BUILD = SITE / "build.py"


def builder():
    """site/build.py as a module, so a test asking "where does this route
    land?" asks the builder rather than keeping its own copy of the rule.
    Its top-level imports are stdlib only; the markdown parser is
    imported inside the function that needs it."""
    spec = importlib.util.spec_from_file_location("bench_site_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = builder()

# The files a build reads out of the repo: the two the pages are cut
# from, the one the version is read from, and every file a markdown link
# inside a slice resolves to — the builder checks those exist, so a
# scratch repo without them fails for a reason that has nothing to do
# with the test.
SOURCES = ["AGENTS.md", "README.md", "manager/core/VERSION",
           "manager/core/adapters/README.md"]

# A layout with no markup of its own, written into a scratch site when a
# test wants to exercise the builder rather than a shipped template.
PLAIN = "<!doctype html>\n<title>$title</title>\n$body\n"

try:
    import markdown_it  # noqa: F401
    HAS_MARKDOWN_IT = True
except ImportError:  # pragma: no cover - environment
    HAS_MARKDOWN_IT = False

needs_renderer = unittest.skipUnless(
    HAS_MARKDOWN_IT,
    "markdown-it-py is not installed: python3 -m pip install -r "
    "site/requirements.txt")


def run_build(repo: Path, out: Path) -> subprocess.CompletedProcess:
    """The build exactly as a person runs it — a real process, so the
    exit code under test is the one a shell would see."""
    return subprocess.run(
        [sys.executable, str(repo / "site" / "build.py"), "--out", str(out)],
        capture_output=True, text=True, cwd=repo)


class ScratchRepo:
    """A copy of site/ plus the markdown it reads, so a test can rename a
    heading without touching the real AGENTS.md."""

    def __init__(self, root: Path):
        self.root = root
        shutil.copytree(SITE, root / "site",
                        ignore=shutil.ignore_patterns("dist", "__pycache__"))
        for name in SOURCES:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, root / name)

    @property
    def out(self) -> Path:
        return self.root / "site" / "dist"

    def plain_home(self) -> dict:
        """A landing page with no markup, so a manifest cut down to one
        page under test still answers the `/` every layout's wordmark
        links to — which the build now checks."""
        (self.root / "site" / "templates" / "plain.html").write_text(
            PLAIN, encoding="utf-8")
        return {"path": "/", "title": "Home", "layout": "plain",
                "section": None, "source": None}

    def pages(self, *entries: dict) -> None:
        """The manifest reduced to these pages, plus that landing page."""
        manifest = self.manifest()
        manifest["pages"] = [self.plain_home(), *entries]
        self.write_manifest(manifest)

    def manifest(self) -> dict:
        return json.loads(
            (self.root / "site" / "pages.json").read_text(encoding="utf-8"))

    def write_manifest(self, manifest: dict) -> None:
        (self.root / "site" / "pages.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        if old not in text:  # not assert: must survive python -O
            raise RuntimeError(f"{name} does not contain {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def build(self) -> subprocess.CompletedProcess:
        return run_build(self.root, self.out)


class ScratchCase(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="bench-site-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        self.repo = ScratchRepo(root)


class TheRealSiteBuilds(unittest.TestCase):
    """`python3 site/build.py` on a clean checkout, into a scratch
    directory so the developer's own dist/ is left alone."""

    @classmethod
    def setUpClass(cls):
        if not HAS_MARKDOWN_IT:
            raise unittest.SkipTest("markdown-it-py is not installed")
        cls.out = Path(tempfile.mkdtemp(prefix="bench-dist-")).resolve()
        cls.result = run_build(REPO, cls.out)
        if cls.result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"site/build.py failed:\n{cls.result.stdout}"
                f"{cls.result.stderr}")
        cls.manifest = json.loads(
            (SITE / "pages.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)

    def page(self, route: str) -> str:
        return BUILDER.target_for(self.out, route).read_text("utf-8")

    def test_one_page_per_manifest_entry(self):
        for entry in self.manifest["pages"]:
            target = BUILDER.target_for(self.out, entry["path"])
            self.assertTrue(target.is_file(),
                            f'{entry["path"]} produced no page')

    def test_the_static_directory_travels_with_the_pages(self):
        self.assertTrue((self.out / "static" / "site.css").is_file())
        self.assertTrue((self.out / "static" / "favicon.svg").is_file())

    def test_the_notes_beside_the_assets_stay_out_of_the_build(self):
        """The host uploads the assets directory whole, so anything left
        in static/ is a public url. The fonts' README is a note to
        whoever refreshes them; their licences are not — the OFL asks
        that its text travel with the files it covers."""
        self.assertEqual([], sorted(self.out.rglob("*.md")),
                         "a maintainer's note became a page of the site")
        fonts = self.out / "static" / "fonts"
        self.assertTrue((fonts / "IBMPlex.LICENSE.txt").is_file())
        self.assertTrue((fonts / "ZillaSlab.LICENSE.txt").is_file())
        self.assertTrue(sorted(fonts.glob("*.woff2")),
                        "the faces the licences cover are not in the build")

    def test_the_article_layout_renders_real_content_from_agents_md(self):
        """Not lorem: the words on the page are the words in the file.
        (The home layout is authored rather than sliced — its own facts
        are tests/test_site_landing.py's subject.)"""
        stages = self.page("/concepts/stages/")
        article = self.page("/concepts/claiming-a-card/")
        # Sliced out of AGENTS.md's "## Stages".
        self.assertIn("Most tasks live here for most of their life", stages)
        self.assertIn("a stale <code>in-progress/</code> makes the board",
                      stages)
        # ...and out of "## Claiming a card".
        self.assertIn("The first claim sticks", article)
        self.assertIn("Identity is git's, so it collides like git's",
                      article)
        self.assertIn("<code>git config user.name</code>", article)

    def test_the_page_title_is_not_repeated_by_the_body(self):
        """The `from` heading is dropped: the layout renders the title."""
        article = self.page("/concepts/claiming-a-card/")
        self.assertEqual(1, article.count("Claiming a card</h1>"))
        self.assertNotIn("Claiming a card</h2>", article)

    def test_sub_headings_are_promoted_to_the_pages_own_level(self):
        """AGENTS.md's `### backlog/` under `## Stages` becomes an <h2>
        with an anchor, so the layout's contents list can reach it."""
        stages = self.page("/concepts/stages/")
        self.assertIn('<h2 id="backlog">backlog/</h2>', stages)
        self.assertIn('<h2 id="in-progress">in-progress/</h2>', stages)

    def test_the_ia_comes_out_of_the_manifest(self):
        article = self.page("/concepts/claiming-a-card/")
        self.assertIn('href="/concepts/claiming-a-card/"', article)
        self.assertIn("Concepts", article)

    def test_the_only_third_party_is_the_one_that_was_chosen(self):
        """Every asset a browser would fetch — stylesheet, icon, font —
        is same-origin, and nothing preconnects anywhere. The single
        exception is the analytics script, named here and in the CSP, so
        a second third party has to be a deliberate edit to this list
        rather than something that slipped into a template. A <link
        rel=canonical> is a statement about this page, not a fetch, so it
        is not one of these; anything that opens a connection is."""
        chosen = ("https://cdn.usefathom.com/",)
        fetching = {"stylesheet", "icon", "shortcut icon", "preload",
                    "prefetch", "preconnect", "dns-prefetch", "manifest",
                    "modulepreload"}
        for entry in self.manifest["pages"]:
            html = self.page(entry["path"])
            for tag in re.findall(r"<link\b[^>]*>", html, re.IGNORECASE):
                rel = re.search(r'rel=["\']([^"\']+)', tag, re.IGNORECASE)
                href = re.search(r'href=["\']([^"\']+)', tag, re.IGNORECASE)
                if not rel or rel.group(1).lower() not in fetching:
                    continue
                self.assertTrue(
                    href and href.group(1).startswith("/"),
                    f'{entry["path"]} loads {tag} from somewhere else')
            for url in re.findall(
                    r"""<(?:script|img|iframe)\b[^>]*\bsrc=["']([^"']+)""",
                    html, re.IGNORECASE):
                self.assertTrue(
                    url.startswith("/") or url.startswith(chosen),
                    f'{entry["path"]} loads {url} from somewhere else')

        css = (self.out / "static" / "site.css").read_text("utf-8")
        self.assertNotIn("@import", css)
        for url in re.findall(r"""url\(\s*["']?([^"')]+)""", css):
            self.assertTrue(url.startswith("/static/"),
                            f"site.css fetches {url} from somewhere else")

    def test_the_stylesheet_carries_the_boards_four_state_tokens(self):
        """The site and the board are one system, so colour is named the
        same in both — see manager/core/board.html's :root."""
        css = (self.out / "static" / "site.css").read_text("utf-8")
        for token in ("--accent:", "--calm:", "--alarm:", "--idle:"):
            self.assertIn(token, css)

    def test_the_missing_fonts_are_named_rather_than_assumed(self):
        """A woff2 the stylesheet wants and the build does not have is a
        warning on stderr, never a silent fallback — and never a fetch
        from a font CDN."""
        wanted = re.findall(r"""url\(\s*["']?(/static/fonts/[^"')]+)""",
                            (self.out / "static" / "site.css").read_text("utf-8"))
        self.assertTrue(wanted, "the stylesheet self-hosts nothing")
        for url in wanted:
            present = (self.out / url.lstrip("/")).exists()
            self.assertEqual(present, f"warning: {url}" not in self.result.stderr,
                             f"{url}: present on disk and warned about, or "
                             f"absent and silent")


@needs_renderer
class DriftStopsTheBuild(ScratchCase):
    """The whole reason the site is generated: a heading that moved must
    break the build, not empty a page."""

    def test_a_renamed_heading_names_the_route_and_the_heading(self):
        """The heading renamed here is one exactly one manifest entry
        names. A heading that is also the *end* of the page above it —
        most of them are, the document being a chain — would be reported
        against whichever route the build reaches first, which is true but
        makes a poor test of "names the route"."""
        self.repo.edit("AGENTS.md", "## Agents working the board",
                       "## Agents at work on the board")
        result = self.repo.build()

        self.assertNotEqual(result.returncode, 0,
                            "a renamed heading built cleanly")
        self.assertIn("/concepts/agents-on-the-board/", result.stderr)
        self.assertIn("AGENTS.md", result.stderr)
        self.assertIn("## Agents working the board", result.stderr)

    def test_a_renamed_heading_emits_no_page_at_all(self):
        """Not "a page with an empty body" — nothing is written. Every
        page renders before the output directory is touched, so a build
        that drifts leaves whatever was there alone rather than replacing
        a working site with a broken one."""
        self.repo.edit("AGENTS.md", "## Claiming a card",
                       "## Claiming a task card")
        self.repo.build()
        self.assertFalse(self.repo.out.exists(),
                         "a failed build wrote pages anyway")

    def test_a_drifting_rebuild_leaves_the_last_good_build_standing(self):
        self.assertEqual(0, self.repo.build().returncode)
        good = (self.repo.out / "concepts" / "claiming-a-card"
                / "index.html").read_text("utf-8")
        self.repo.edit("AGENTS.md", "## Claiming a card",
                       "## Claiming a task card")
        self.assertNotEqual(0, self.repo.build().returncode)
        self.assertEqual(good, (self.repo.out / "concepts"
                                / "claiming-a-card"
                                / "index.html").read_text("utf-8"))

    def test_a_renamed_to_heading_is_caught_too(self):
        self.repo.edit("AGENTS.md", "## Syncing boards", "## Board sync")
        result = self.repo.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("## Syncing boards", result.stderr)
        self.assertIn("/concepts/claiming-a-card/", result.stderr)

    def test_a_missing_source_file_is_readable(self):
        (self.repo.root / "AGENTS.md").unlink()
        result = self.repo.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENTS.md", result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_a_section_emptied_to_its_heading_fails(self):
        """The subtler drift: the heading survives, its content moves
        elsewhere. An empty page is a drift, not a page."""
        self.repo.pages({
            "path": "/hollow/", "title": "Hollow", "layout": "article",
            "section": "Concepts", "source": "AGENTS.md",
            "from": "## Empty", "to": "## After",
        })
        self.repo.edit("AGENTS.md", "## Claiming a card",
                       "## Empty\n\n## After\n\n## Claiming a card")
        result = self.repo.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty", result.stderr)
        self.assertIn("/hollow/", result.stderr)

    def test_a_heading_inside_a_code_fence_is_not_a_heading(self):
        """AGENTS.md fences a task file template starting `# Task title`.
        Matching that would slice the document in half."""
        self.repo.pages({
            "path": "/fenced/", "title": "Fenced", "layout": "article",
            "section": "Concepts", "source": "AGENTS.md",
            "from": "# Task title",
        })
        result = self.repo.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("# Task title", result.stderr)


@needs_renderer
class LinksComeOutWorking(ScratchCase):
    """Markdown that links between repo files is a dead path on the web."""

    def build_one(self, body: str, *, link_routes=None):
        (self.repo.root / "SOURCE.md").write_text(
            f"# Doc\n\n## Section\n\n{body}\n", encoding="utf-8")
        self.repo.pages({
            "path": "/linked/", "title": "Linked", "layout": "plain",
            "section": "Concepts", "source": "SOURCE.md",
            "from": "## Section",
        })
        manifest = self.repo.manifest()
        manifest["link_routes"] = link_routes or {}
        self.repo.write_manifest(manifest)
        result = self.repo.build()
        page = self.repo.out / "linked" / "index.html"
        return result, page.read_text("utf-8") if page.exists() else ""

    def test_a_repo_relative_link_becomes_a_github_link(self):
        result, html = self.build_one("See [the brief](AGENTS.md).")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'href="https://github.com/12vectors/bench/blob/main/AGENTS.md"',
            html)

    def test_link_routes_win_over_github(self):
        result, html = self.build_one(
            "See [the brief](AGENTS.md#stages).",
            link_routes={"AGENTS.md": "/linked/"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('href="/linked/#stages"', html)

    def test_an_absolute_link_and_an_anchor_are_left_alone(self):
        result, html = self.build_one(
            "[out](https://example.com/x) and [here](#section).")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('href="https://example.com/x"', html)
        self.assertIn('href="#section"', html)

    def test_a_dead_relative_link_stops_the_build(self):
        result, _ = self.build_one("See [gone](docs/gone.md).")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/linked/", result.stderr)
        self.assertIn("docs/gone.md", result.stderr)

    def test_a_link_escaping_the_repo_stops_the_build(self):
        result, _ = self.build_one("See [up](../../etc/passwd).")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the repository", result.stderr)


@needs_renderer
class TheArticleGutterFollowsTheBody(ScratchCase):
    """"On this page" is not authored anywhere: it is the promoted body's
    own h2s, which is why a section that grows a sub-heading grows a
    contents entry without anyone editing the site."""

    def test_promoted_sub_headings_become_the_contents_list(self):
        self.repo.pages({
            "path": "/concepts/team-mode/", "title": "Syncing boards",
            "layout": "article", "section": "Concepts",
            "source": "AGENTS.md", "from": "## Syncing boards",
            "to": "## Task file format",
        })
        result = self.repo.build()
        self.assertEqual(result.returncode, 0, result.stderr)

        html = (self.repo.out / "concepts" / "team-mode"
                / "index.html").read_text("utf-8")
        # AGENTS.md's "### State syncs; reactions don't" under "## Syncing
        # boards" — promoted to an <h2>, anchored, and listed.
        self.assertIn('<h2 id="state-syncs-reactions-don-t">', html)
        self.assertIn('href="#state-syncs-reactions-don-t"', html)
        self.assertIn("On this page", html)


@needs_renderer
class TheManifestIsChecked(ScratchCase):
    """pages.json is the only place the IA is written down, so a mistake
    in it has to be a build failure rather than a surprise on the site."""

    def only(self, page: dict):
        manifest = self.repo.manifest()
        manifest["pages"] = [page]
        self.repo.write_manifest(manifest)
        return self.repo.build()

    def with_landing(self, page: dict):
        self.repo.pages(page)
        return self.repo.build()

    def test_an_unknown_layout_lists_the_ones_that_exist(self):
        result = self.only({
            "path": "/x/", "title": "X", "layout": "logbook",
            "source": None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("logbook", result.stderr)
        self.assertIn("article", result.stderr)
        self.assertIn("home", result.stderr)

    def test_a_source_without_a_from_heading_is_refused(self):
        result = self.only({
            "path": "/x/", "title": "X", "layout": "article",
            "source": "AGENTS.md"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("from", result.stderr)

    def test_an_authored_page_says_so_with_a_null_source(self):
        """An authored page has no slice and so no drift: the builder
        offers it an empty $body and renders the template's own words."""
        result = self.with_landing({
            "path": "/authored/", "title": "Authored", "layout": "plain",
            "section": None, "source": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        html = (self.repo.out / "authored" / "index.html").read_text("utf-8")
        self.assertIn("<title>Authored</title>", html)

    def test_a_route_must_be_a_directory_path(self):
        result = self.only({
            "path": "/concepts/claiming", "title": "X", "layout": "article",
            "source": None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/concepts/claiming", result.stderr)

    def test_two_entries_cannot_claim_one_route(self):
        manifest = self.repo.manifest()
        manifest["pages"] = [
            {"path": "/x/", "title": "X", "layout": "article", "source": None},
            {"path": "/x/", "title": "Y", "layout": "article", "source": None},
        ]
        self.repo.write_manifest(manifest)
        result = self.repo.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("twice", result.stderr)


@needs_renderer
class TheOutputDirectoryIsOurs(ScratchCase):
    """--out is a path a person types, so it gets one guard: the builder
    empties directories it wrote and refuses anything else."""

    def test_a_rebuild_replaces_the_previous_build(self):
        self.assertEqual(0, self.repo.build().returncode)
        stale = self.repo.out / "gone" / "index.html"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale", encoding="utf-8")
        self.assertEqual(0, self.repo.build().returncode)
        self.assertFalse(stale.exists())

    def test_a_directory_it_did_not_write_is_refused(self):
        theirs = self.repo.root / "not-ours"
        theirs.mkdir()
        (theirs / "important.txt").write_text("mine", encoding="utf-8")
        result = run_build(self.repo.root, theirs)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing", result.stderr)
        self.assertTrue((theirs / "important.txt").exists())


class TheRepositoryKnowsAboutSite(unittest.TestCase):
    """The two facts about site/ that live outside site/."""

    def test_dist_is_gitignored_and_the_sources_are_not(self):
        ignored = subprocess.run(
            ["git", "check-ignore", "site/dist/index.html"],
            capture_output=True, text=True, cwd=REPO)
        self.assertEqual(ignored.returncode, 0,
                         "site/dist/ is not gitignored")
        tracked = subprocess.run(
            ["git", "check-ignore", "site/build.py", "site/pages.json"],
            capture_output=True, text=True, cwd=REPO)
        self.assertNotEqual(tracked.returncode, 0,
                            "site/ sources are gitignored")

    def test_the_release_manifest_does_not_mention_site(self):
        """Belt to test_release_artifact.py's braces: nothing in the
        manifest names site/, so nothing can ship it."""
        manifest = (REPO / "manager" / "core" / "release-manifest"
                    ).read_text("utf-8")
        for line in manifest.splitlines():
            if line.strip().startswith("#") or not line.strip():
                continue
            self.assertNotIn("site", line.split(None, 1)[1],
                             f"the release manifest names site/: {line}")


if __name__ == "__main__":
    unittest.main()
