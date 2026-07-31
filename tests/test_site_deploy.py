"""bench.12vectors.com is a Cloudflare Worker serving site/dist/ as
static assets. What can be tested here is everything the deploy depends
on *before* it reaches Cloudflare: that the config says what the site
needs it to say, that the build produces the files that config names,
and that the three of them — wrangler.jsonc, pages.json, README.md —
cannot drift apart about which domain this is.

What cannot be tested here is a live response. `not_found_handling` is
asserted as configuration plus the 404.html it points at; that a request
to a missing path comes back 404 is Cloudflare's half, and the deploy
checklist in site/README.md is where a person confirms it.

    python3 -m unittest discover -s tests
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
WRANGLER = SITE / "wrangler.jsonc"
HEADERS = SITE / "root" / "_headers"
README = SITE / "README.md"

# Set on every response, whatever it is. The task's three, plus the two
# that come free with them.
BASELINE = ("X-Content-Type-Options", "Referrer-Policy",
            "Strict-Transport-Security")

try:
    import markdown_it  # noqa: F401
    HAS_MARKDOWN_IT = True
except ImportError:  # pragma: no cover - environment
    HAS_MARKDOWN_IT = False


# ── reading the two config formats by hand ────────────────────────────

COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)


def read_jsonc(path: Path) -> dict:
    """wrangler.jsonc is JSON with comments, and comments are how that
    file explains itself. Strings are matched first so a `//` inside one
    survives."""
    def keep(match):
        text = match.group(0)
        return text if text.startswith('"') else " "
    return json.loads(COMMENT.sub(keep, path.read_text(encoding="utf-8")))


def read_headers(path: Path) -> list:
    """`_headers` as [(pattern, {header: value})], in file order — an
    unindented line opens a rule, an indented one adds a header to it."""
    rules, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            current = (line.strip(), {})
            rules.append(current)
        elif current is not None:
            name, _, value = line.strip().partition(":")
            current[1][name.strip()] = value.strip()
    return rules


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def rule(pattern: str) -> dict:
    for found, headers in read_headers(HEADERS):
        if found == pattern:
            return headers
    raise AssertionError(f"_headers has no rule for {pattern}")


def run_build(repo: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "site" / "build.py"), "--out", str(out)],
        capture_output=True, text=True, cwd=repo)


class Built(unittest.TestCase):
    """The real site, built once into a scratch directory."""

    @classmethod
    def setUpClass(cls):
        if not HAS_MARKDOWN_IT:
            raise unittest.SkipTest("markdown-it-py is not installed")
        cls.out = Path(tempfile.mkdtemp(prefix="bench-deploy-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(
                f"site/build.py failed:\n{result.stdout}{result.stderr}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)


# ── the worker config ─────────────────────────────────────────────────

class TheWorkerServesTheBuild(unittest.TestCase):
    """site/wrangler.jsonc, read the way wrangler reads it."""

    def setUp(self):
        self.config = read_jsonc(WRANGLER)
        self.assets = self.config.get("assets", {})

    def test_the_assets_directory_is_what_the_builder_writes(self):
        """Paths in wrangler config are relative to the config file, so
        this one has to resolve to site/dist and not to a sibling of the
        repo root."""
        directory = (WRANGLER.parent / self.assets["directory"]).resolve()
        self.assertEqual(directory, (SITE / "dist").resolve())

    def test_there_is_no_worker_script(self):
        """The site is files. A `main` would mean a fetch handler to
        maintain, and nothing here needs one."""
        self.assertNotIn("main", self.config)

    def test_a_url_has_exactly_one_form(self):
        """force-trailing-slash: /x redirects to /x/, which is the url
        the pages link and the one rel=canonical names. Anything else
        leaves two urls serving one page."""
        self.assertEqual("force-trailing-slash",
                         self.assets.get("html_handling"))

    def test_an_unknown_path_gets_the_sites_own_404(self):
        """Not single-page-application, which answers 200 with the
        landing page and tells a crawler every typo is a real url."""
        self.assertEqual("404-page", self.assets.get("not_found_handling"))

    def test_the_route_is_a_custom_domain(self):
        routes = self.config.get("routes", [])
        self.assertEqual(1, len(routes), "expected exactly one route")
        self.assertTrue(routes[0].get("custom_domain"),
                        "the route is not a custom domain")

    def test_the_worker_is_named(self):
        self.assertTrue(self.config.get("name"))

    def test_a_compatibility_date_is_pinned(self):
        self.assertRegex(self.config.get("compatibility_date", ""),
                         r"^\d{4}-\d{2}-\d{2}$")


class TheDomainIsWrittenDownOnce(unittest.TestCase):
    """Three files name this site's address. They are allowed to say it
    three times; they are not allowed to disagree."""

    def setUp(self):
        self.config = read_jsonc(WRANGLER)
        self.manifest = json.loads(
            (SITE / "pages.json").read_text(encoding="utf-8"))

    def test_the_route_is_the_host_the_pages_canonicalise_to(self):
        """A rel=canonical pointing somewhere the Worker does not answer
        is worse than none at all."""
        canonical = urlparse(self.manifest["site"]["base_url"])
        self.assertEqual("https", canonical.scheme)
        self.assertEqual(canonical.netloc,
                         self.config["routes"][0]["pattern"])

    def test_the_readme_names_the_worker_and_the_route(self):
        """Acceptance: the next person should not have to guess where
        the site lives."""
        readme = README.read_text(encoding="utf-8")
        self.assertIn(self.config["name"], readme)
        self.assertIn(self.config["routes"][0]["pattern"], readme)

    def test_the_readme_names_the_deploy_command(self):
        self.assertRegex(readme_text(), r"wrangler[^\n]*\bdeploy\b")

    def test_the_readme_names_the_account(self):
        self.assertRegex(readme_text(), r"(?i)\baccount\b")


# ── the 404 page ──────────────────────────────────────────────────────

class TheNotFoundPageIsAPage(Built):
    """`not_found_handling: "404-page"` is half of it. The other half is
    that a 404.html exists, looks like the rest of the site, and offers a
    way out."""

    def setUp(self):
        self.page = (self.out / "404.html").read_text("utf-8")

    def test_it_is_written_where_the_host_looks(self):
        """A 404/index.html would never be found: the host wants a file
        called 404.html at the root of the assets directory."""
        self.assertTrue((self.out / "404.html").is_file())
        self.assertFalse((self.out / "404" / "index.html").exists())

    def test_it_wears_the_sites_design(self):
        self.assertIn('rel="stylesheet" href="/static/site.css', self.page)
        self.assertIn("404", self.page)

    def test_it_offers_the_way_back(self):
        self.assertIn('href="/"', self.page)

    def test_it_claims_no_canonical_url(self):
        """A 404 is not a page to canonicalise to, and telling a crawler
        to index it is worse still."""
        self.assertNotIn('rel="canonical"', self.page)
        self.assertIn('name="robots" content="noindex"', self.page)

    def test_it_stays_out_of_the_nav_and_the_sidebar(self):
        home = (self.out / "index.html").read_text("utf-8")
        article = (self.out / "concepts" / "claiming-a-card"
                   / "index.html").read_text("utf-8")
        for html in (home, article):
            self.assertNotIn("404.html", html)


# ── headers ───────────────────────────────────────────────────────────

class TheHeaderPolicyTravelsWithTheBuild(Built):
    """site/root/_headers is the policy; the build has to carry it to
    the root of dist/ or the host never reads it."""

    def test_it_reaches_the_root_of_the_build(self):
        shipped = self.out / "_headers"
        self.assertTrue(shipped.is_file())
        self.assertEqual(HEADERS.read_text("utf-8"), shipped.read_text("utf-8"))

    def test_it_is_not_also_served_as_a_page(self):
        """The host consumes _headers rather than serving it, but the
        build must not have made a route out of it either."""
        self.assertFalse((self.out / "_headers" / "index.html").exists())

    def test_no_page_needs_what_the_policy_forbids(self):
        """The CSP carries no 'unsafe-inline', so an inline <script> or a
        style="" attribute is a page that renders right in every test
        here and wrong in production. The only place that can be caught
        before a deploy is the build output."""
        for path in sorted(self.out.rglob("*.html")):
            html = path.read_text("utf-8")
            where = path.relative_to(self.out)
            self.assertNotIn("<script", html.lower(),
                             f"{where} has a script the CSP would block")
            self.assertNotRegex(
                html, r"""\sstyle\s*=\s*["']""",
                f"{where} has an inline style the CSP would block")


class EveryResponseCarriesTheBaseline(unittest.TestCase):

    def setUp(self):
        self.everything = rule("/*")

    def test_the_three_headers_a_public_page_owes(self):
        for name in BASELINE:
            self.assertIn(name, self.everything)

    def test_hsts_is_a_year_and_does_not_preload(self):
        value = self.everything["Strict-Transport-Security"]
        self.assertIn("max-age=31536000", value)
        self.assertNotIn("preload", value,
                         "preload is a submission to browser vendors, "
                         "not a header to set in passing")

    def test_nothing_third_party_can_load(self):
        """The site's promise — no analytics, no font CDN — as something
        the browser enforces rather than something a test asserted once
        at build time."""
        policy = self.everything["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("*", policy)
        self.assertNotIn("//", policy, "the policy names another origin")


class TheTwoKindsOfFileAreCachedDifferently(unittest.TestCase):
    """The edge case in the task: a stale HTML page must not survive a
    deploy, while the assets it links may live for a year."""

    def test_html_revalidates_on_every_view(self):
        value = rule("/*")["Cache-Control"]
        self.assertIn("max-age=0", value)
        self.assertIn("must-revalidate", value)
        self.assertNotIn("immutable", value)

    def test_fingerprinted_assets_are_kept_for_a_year(self):
        value = rule("/static/*")["Cache-Control"]
        self.assertIn("max-age=31536000", value)
        self.assertIn("immutable", value)

    def test_the_general_rule_comes_first(self):
        """Order is the whole argument: /static/* overrides /* on
        Cache-Control, and a host that merged them instead of overriding
        would land on max-age=0 — the safe side."""
        patterns = [pattern for pattern, _ in read_headers(HEADERS)]
        self.assertLess(patterns.index("/*"), patterns.index("/static/*"))


# ── fingerprinting ────────────────────────────────────────────────────

class Scratch(unittest.TestCase):
    """A copy of site/ plus the markdown it reads, so a test can edit the
    stylesheet without touching the real one."""

    def setUp(self):
        if not HAS_MARKDOWN_IT:
            raise unittest.SkipTest("markdown-it-py is not installed")
        self.root = Path(tempfile.mkdtemp(prefix="bench-deploy-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(SITE, self.root / "site",
                        ignore=shutil.ignore_patterns("dist", "__pycache__"))
        # The markdown the pages are cut from, the file the version shown
        # on them is read from, and whatever a link inside a slice points
        # at. One list, in tests/test_site_build.py — a scratch repo that
        # is missing one of them fails for a reason no test here is about.
        from tests.test_site_build import SOURCES

        for name in SOURCES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, self.root / name)
        self.out = self.root / "site" / "dist"

    def build(self):
        return run_build(self.root, self.out)

    def home(self) -> str:
        return (self.out / "index.html").read_text("utf-8")


class TheStylesheetUrlFollowsTheStylesheet(Scratch):
    """`immutable` for a year is only safe because the url changes when
    the file does. That is the claim under test."""

    STAMP = re.compile(r'href="(/static/site\.css\?v=([0-9a-f]{10}))"')

    def test_the_pages_link_a_stamped_url(self):
        self.assertEqual(0, self.build().returncode)
        self.assertRegex(self.home(), self.STAMP.pattern)

    def test_the_file_still_sits_at_its_plain_path(self):
        """Only the link carries the stamp — nothing in static/ is
        renamed, so the /static/* glob in _headers still matches and the
        tree stays readable."""
        self.assertEqual(0, self.build().returncode)
        self.assertTrue((self.out / "static" / "site.css").is_file())

    def test_editing_the_stylesheet_changes_the_stamp(self):
        self.assertEqual(0, self.build().returncode)
        before = self.STAMP.search(self.home()).group(2)

        css = self.root / "site" / "static" / "site.css"
        css.write_text(css.read_text("utf-8") + "\n.lost{color:red}\n",
                       encoding="utf-8")
        self.assertEqual(0, self.build().returncode)
        after = self.STAMP.search(self.home()).group(2)

        self.assertNotEqual(before, after,
                            "a changed stylesheet kept its url, so a "
                            "year-long cache would keep the old one")

    def test_a_rebuild_that_changes_nothing_keeps_the_stamp(self):
        """The other half: a deploy that did not touch the stylesheet
        must not invalidate everyone's cache."""
        self.assertEqual(0, self.build().returncode)
        before = self.STAMP.search(self.home()).group(2)
        self.assertEqual(0, self.build().returncode)
        self.assertEqual(before, self.STAMP.search(self.home()).group(2))


class TheRootTreeIsCopiedVerbatim(Scratch):

    def test_a_root_file_lands_at_the_top_of_the_build(self):
        (self.root / "site" / "root" / "robots.txt").write_text(
            "User-agent: *\n", encoding="utf-8")
        self.assertEqual(0, self.build().returncode)
        self.assertEqual("User-agent: *\n",
                         (self.out / "robots.txt").read_text("utf-8"))

    def test_a_root_file_a_route_also_claims_stops_the_build(self):
        """Both would be written, one would win, and which one is not
        something a person should have to work out from a diff."""
        (self.root / "site" / "root" / "404.html").write_text(
            "mine", encoding="utf-8")
        result = self.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("404.html", result.stderr)


class TheDeployIsInTheRepository(unittest.TestCase):
    """The two facts about deployment that live outside a config file."""

    def test_the_worker_config_and_the_headers_are_tracked(self):
        tracked = subprocess.run(
            ["git", "check-ignore", "site/wrangler.jsonc", "site/root/_headers"],
            capture_output=True, text=True, cwd=REPO)
        self.assertNotEqual(tracked.returncode, 0,
                            "the deploy config is gitignored")

    def test_the_release_artifact_still_does_not_ship_the_site(self):
        """A Worker config in site/ must not start travelling into host
        projects with a release."""
        manifest = (REPO / "manager" / "core" / "release-manifest"
                    ).read_text("utf-8")
        for line in manifest.splitlines():
            if line.strip().startswith("#") or not line.strip():
                continue
            self.assertNotIn("site", line.split(None, 1)[1],
                             f"the release manifest names site/: {line}")


if __name__ == "__main__":
    unittest.main()
