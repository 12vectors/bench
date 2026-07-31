"""The landing page is the one page most visitors read, and the only one
whose words are authored rather than cut from a heading. That is the risk
this file is about.

Three promises, each mechanised below:

- **The facts on it are read, not typed.** The install one-liner comes out
  of README.md and the version out of manager/core/VERSION, so changing
  either source changes the page and losing either stops the build.
- **The terminal is a transcript, not a mood.** Every line it shows is
  held against the source that prints it — install.py's setup questions,
  board.py's startup. A reworded prompt fails here rather than quietly
  turning the strongest thing on the page into fiction.
- **Every door opens.** The six cards point at routes this build writes,
  and a link that resolves to nothing fails the build instead of shipping.

    python3 -m unittest discover -s tests
"""

import html
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_site_build import (BUILDER, REPO, SITE, ScratchCase,
                                   needs_renderer, run_build)

INSTALL = REPO / "install.py"
BOARD = REPO / "manager" / "core" / "board.py"
CONFIG = REPO / "manager" / "core" / "config.py"

# The hero terminal, line by line, minus the install command: what the
# page shows, a fragment of it that the source can be searched for, and
# the file that prints it. Two fragments are shorter than the line they
# check because the f-string that produces them is wrapped across two
# source lines — the page still has to carry the whole line.
#
# The page shows fewer lines than a real first run does; its title bar
# says "abridged". It shows none a first run does not.
#
# Shorter since the board shot joined the hero: the terminal gives up
# height so the screenshot below it clears the fold. Trimming lines is
# allowed — every line here must still be shown, and still be real — but
# a line added back has to earn both halves of that again.
TRANSCRIPT = [
    ("solo or team?", "solo or team?", INSTALL),
    ("which agent adapter?", "which agent adapter?", INSTALL),
    ("Task board for", "Task board for", BOARD),
    ("http://127.0.0.1:", "http://127.0.0.1:", BOARD),
    ("26071", "26071", CONFIG),
]

# Words for things bench does not have. Turn 1 of the design documented
# several of them; the page must never grow them back.
FICTION = ("bench.toml", "brew install", "npm install", "sqlite", "webhook",
           "sign up", "sign in", "log in", "your account", "api key",
           "lanes", "7331", "cloud", "pricing", "free trial")


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class LandingCase(unittest.TestCase):
    """The real site, built once into a scratch directory."""

    @classmethod
    def setUpClass(cls):
        try:
            import markdown_it  # noqa: F401
        except ImportError:  # pragma: no cover - environment
            raise unittest.SkipTest("markdown-it-py is not installed")
        cls.out = Path(tempfile.mkdtemp(prefix="bench-landing-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(f"site/build.py failed:\n{result.stdout}"
                               f"{result.stderr}")
        cls.manifest = json.loads(text_of(SITE / "pages.json"))
        cls.home = text_of(cls.out / "index.html")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)

    def terminal(self) -> str:
        """The hero terminal's text, as a reader sees it."""
        block = re.search(r'<pre class="terminal-body">(.*?)</pre>',
                          self.home, re.DOTALL)
        self.assertTrue(block, "the landing page has no terminal")
        return html.unescape(re.sub(r"<[^>]+>", "", block.group(1)))


class TheFactsAreRead(LandingCase):
    """Neither of these is allowed to be a copy. A landing page with a
    subtly wrong curl is worse than no landing page."""

    def test_the_install_command_is_the_readmes_own_line_for_line(self):
        shown = self.terminal()
        for line in BUILDER.install_command(REPO):
            self.assertIn(line, shown,
                          "the terminal does not show README.md's install "
                          "command as README.md writes it")

    def test_the_version_shown_is_the_one_in_the_version_file(self):
        version = text_of(REPO / "manager" / "core" / "VERSION").strip()
        self.assertIn(version, self.home)

    def test_the_manifest_keeps_no_second_copy_of_the_version(self):
        self.assertNotIn("version", self.manifest["site"],
                         "pages.json names a version; the build reads "
                         "manager/core/VERSION")


class TheTerminalIsATranscript(LandingCase):
    """The strongest element on the page and the easiest one to fake."""

    def test_every_line_shown_is_a_line_something_really_prints(self):
        shown = self.terminal()
        for line, fragment, source in TRANSCRIPT:
            with self.subTest(line=line):
                self.assertIn(line, shown,
                              "the terminal no longer shows this")
                self.assertIn(fragment, text_of(source),
                              f"{source.relative_to(REPO)} no longer prints "
                              f"this — the landing page is now fiction")

    def test_the_abridgement_is_declared_where_the_transcript_is(self):
        """It shows fewer lines than a first run prints. Saying so in the
        terminal's own title bar is the difference between an excerpt and
        a claim that this is the whole output."""
        self.assertIn("abridged", self.home)


class TheDoorsOpen(LandingCase):
    """Six doors, each a page this site builds."""

    def doors(self) -> list:
        return re.findall(r'<a class="door[^"]*" href="([^"]+)"', self.home)

    def test_there_are_six_and_they_are_distinct(self):
        self.assertEqual(6, len(self.doors()))
        self.assertEqual(6, len(set(self.doors())))

    def test_each_one_is_a_route_the_build_writes(self):
        routes = {page["path"] for page in self.manifest["pages"]}
        for door in self.doors():
            with self.subTest(door=door):
                self.assertIn(door, routes)
                self.assertTrue(
                    BUILDER.target_for(self.out, door).is_file(),
                    f"{door} is in the manifest but produced no page")

    def test_both_hero_buttons_go_somewhere_real(self):
        """One to the install guide, one to the repository."""
        hero = re.search(r'<div class="hero-actions">(.*?)</div>',
                         self.home, re.DOTALL).group(1)
        links = re.findall(r'href="([^"]+)"', hero)
        self.assertEqual(2, len(links))
        routes = {page["path"] for page in self.manifest["pages"]}
        self.assertIn("/guides/install/", links)
        self.assertIn("/guides/install/", routes)
        self.assertIn(self.manifest["site"]["repo_url"], links)


class WhatThePageMayNotSay(LandingCase):
    """Nothing here claims a feature bench lacks, counts anything it
    cannot count, or needs a script to be read."""

    def test_the_fake_telemetry_is_gone(self):
        """Turn 1's "Most opened this week" was analytics this site does
        not have and will not get."""
        self.assertNotIn("Most opened", self.home)

    def test_no_page_claims_something_bench_does_not_have(self):
        lowered = self.home.lower()
        for word in FICTION:
            with self.subTest(word=word):
                self.assertNotIn(word, lowered)

    def test_the_page_needs_no_javascript(self):
        for page in self.manifest["pages"]:
            html_text = text_of(BUILDER.target_for(self.out, page["path"]))
            with self.subTest(page=page["path"]):
                # The analytics tag is deferred and nothing reads it back:
                # every word, link and menu works with scripting off. Any
                # *other* script would be the page needing one.
                scripts = re.findall(r"<script\b[^>]*>", html_text, re.I)
                for tag in scripts:
                    self.assertIn("cdn.usefathom.com", tag,
                                  "a script the page would depend on")
                    self.assertIn("defer", tag,
                                  "even analytics waits for the page")
                self.assertNotIn("<noscript", html_text.lower())
                self.assertIsNone(
                    re.search(r"\son[a-z]+=", html_text, re.IGNORECASE),
                    "an inline event handler is a page that needs a script")

    def test_it_carries_the_card_a_pasted_url_needs(self):
        canonical = (self.manifest["site"]["base_url"].rstrip("/") + "/")
        entry = next(p for p in self.manifest["pages"] if p["path"] == "/")
        self.assertIn(f'<title>{entry["title"]}</title>', self.home)
        for tag, value in (("og:type", "website"),
                           ("og:title", entry["title"]),
                           ("og:description", entry["description"]),
                           ("og:url", canonical)):
            with self.subTest(tag=tag):
                self.assertIn(f'<meta property="{tag}" content="{value}">',
                              self.home)
        self.assertIn(f'<link rel="canonical" href="{canonical}">', self.home)
        self.assertIn(f'<meta name="description" content='
                      f'"{entry["description"]}">', self.home)


@needs_renderer
class ChangingASourceChangesThePage(ScratchCase):
    """The acceptance criterion the other way round: edit the file the
    fact is read from, rebuild, and the page has moved."""

    def home(self) -> str:
        return (self.repo.out / "index.html").read_text("utf-8")

    def test_editing_the_version_file_moves_the_version_on_the_page(self):
        self.repo.edit("manager/core/VERSION", "0.2-alpha", "9.9-rc1")
        self.assertEqual(0, self.repo.build().returncode)
        self.assertIn("9.9-rc1", self.home())
        self.assertNotIn("0.2-alpha", self.home())

    def test_editing_the_readme_moves_the_command_on_the_page(self):
        self.repo.edit("README.md", "mkdir .task-manager && curl -L \\",
                       "mkdir .bench && curl -sSL \\")
        self.assertEqual(0, self.repo.build().returncode)
        self.assertIn("mkdir .bench &amp;&amp; curl -sSL", self.home())
        self.assertNotIn("mkdir .task-manager &amp;&amp; curl -L",
                         self.home())

    def test_a_missing_version_file_stops_the_build(self):
        (self.repo.root / "manager" / "core" / "VERSION").unlink()
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("manager/core/VERSION", result.stderr)

    def test_a_renamed_install_section_stops_the_build(self):
        self.repo.edit("README.md", "## Install into a repo",
                       "## Getting started")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("## Install into a repo", result.stderr)

    def test_an_install_section_with_no_command_stops_the_build(self):
        text = (self.repo.root / "README.md").read_text("utf-8")
        start = text.index("## Install into a repo")
        end = text.index("## Update")
        (self.repo.root / "README.md").write_text(
            text[:start] + "## Install into a repo\n\nSoon.\n\n" + text[end:],
            encoding="utf-8")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("no fenced command block", result.stderr)

    def test_a_version_in_the_manifest_is_refused(self):
        manifest = self.repo.manifest()
        manifest["site"]["version"] = "1.0"
        self.repo.write_manifest(manifest)
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("VERSION", result.stderr)


@needs_renderer
class ADeadInternalLinkStopsTheBuild(ScratchCase):
    """A door that 404s is the failure this page cannot be allowed to
    ship, so it is a build failure rather than a review finding."""

    def test_a_link_to_a_route_the_manifest_lost_is_named(self):
        manifest = self.repo.manifest()
        manifest["pages"] = [page for page in manifest["pages"]
                             if page["path"] != "/guides/install/"]
        self.repo.write_manifest(manifest)
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/guides/install/", result.stderr)
        self.assertIn("does not write", result.stderr)

    def test_it_fails_before_anything_is_written(self):
        """As drift does: the last good build stays up."""
        manifest = self.repo.manifest()
        manifest["pages"] = [page for page in manifest["pages"]
                             if page["path"] != "/concepts/team-mode/"]
        self.repo.write_manifest(manifest)
        self.repo.build()
        self.assertFalse(self.repo.out.exists(),
                         "a build with a dead link wrote pages anyway")

    def test_a_relative_link_is_refused(self):
        home = self.repo.plain_home()
        (self.repo.root / "site" / "templates" / "plain.html").write_text(
            '<a href="guides/install/">x</a>$title$body', encoding="utf-8")
        manifest = self.repo.manifest()
        manifest["pages"] = [home]
        self.repo.write_manifest(manifest)
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("guides/install/", result.stderr)

    def test_a_stamped_asset_url_is_not_mistaken_for_a_dead_one(self):
        """The stylesheet is linked with a ?v=<hash>; the check has to
        read past the query string rather than call it a dead link."""
        result = self.repo.build()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("/static/site.css?v=",
                      (self.repo.out / "index.html").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
