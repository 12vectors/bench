"""The second installation: bench beside the application rather than
inside it, and the documentation a person finds it through.

There are two ways to install bench and only one of them used to be
written down anywhere a reader would look. The risk this file is about is
not that the new pages stop building — tests/test_site_build.py already
makes a renamed heading a loud failure — it is that they describe
something bench does not do. Documentation drifts in a direction the
build cannot see: a flag `install.py` never accepted, a `BOARD_*` key
that is not a setting, a question count nobody updated.

So every factual claim the external docs make is held against the file
that owns the fact:

- the flags, against `install.py`'s own argument handling;
- the settings, against `manager/core/.env.example`, which is where a
  default is written down and where the Settings page is generated from;
- the first run's question count, against the installer that asks them;
- the two git identities, against the requirement that each is named
  with its own remote and its own base, so a ticket commit can never be
  read as an application push.

And the navigation half: both installs reachable from the landing page
and from the sidebar, with the vendored quick start still saying what it
said.

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

AGENTS = REPO / "AGENTS.md"
README = REPO / "README.md"
INSTALL = REPO / "install.py"
ENV_EXAMPLE = REPO / "manager" / "core" / ".env.example"

# The two routes, named rather than discovered: a reader arriving at the
# docs has to find both installs, and a route quietly renamed is exactly
# the failure that would leave one of them unreachable.
VENDORED = "/guides/install/"
EXTERNAL = "/guides/outside-the-application/"
WORKSPACES = "/concepts/external-workspaces/"
OPERATIONS = "/concepts/external-operations/"
PLANS = "/concepts/execution-plans/"
SETTINGS = "/reference/settings/"

# Where each slice of external documentation lives, as the manifest
# itself names it. The tests below read the sections through the
# builder's own slicer, so what a test inspects and what the site
# publishes cannot be two different pieces of text.
SECTIONS = {
    "agents": (AGENTS, "## Running outside the application",
               "## Seeing the board"),
    "readme": (README, "## Install outside the repo", "## Update"),
}

LONG_FLAG = re.compile(r"--[a-z][a-z0-9-]*")
# The two shapes install.py accepts a flag in: `"--x" in args`, and an
# entry in LAYOUT_FLAGS, which maps a flag carrying a path to the
# setting it writes.
SWITCH = re.compile(r'"(--[a-z0-9-]+)" in args')
LAYOUT_FLAG = re.compile(r'"(--[a-z0-9-]+)": "BOARD_[A-Z_]+"')
SETTING_NAME = re.compile(r"BOARD_[A-Z0-9_]+")
ENV_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
# `_ask(` is the installer's one question-asking helper, so counting its
# calls counts the questions. The detected test command is deliberately
# not one of them, which is the whole point of the count.
ASKED = re.compile(r"^\s*(?:reply = )?_ask\(", re.MULTILINE)
COUNT_WORD = {1: "one", 2: "two", 3: "three", 4: "four"}


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(name: str) -> str:
    """One documented section, cut the way site/build.py cuts it."""
    source, start, end = SECTIONS[name]
    page = {"path": f"<{name}>", "from": start, "to": end}
    return BUILDER.slice_section(text_of(source), page,
                                 source.relative_to(REPO).as_posix())


def flags_install_py_reads() -> set:
    """Every long flag `install.py` accepts, read out of its source
    rather than kept as a second list here.

    Precisely the two shapes it accepts one in: a switch tested against
    the argument list, and a layout flag that carries a value and maps
    to a setting. Flags it *passes on* to git or to an adapter's `wire`
    are not flags a reader may type, so they are not in here."""
    source = text_of(INSTALL)
    return set(SWITCH.findall(source)) | set(LAYOUT_FLAG.findall(source))


def settings_that_exist() -> set:
    return set(ENV_KEY.findall(text_of(ENV_EXAMPLE)))


def questions_the_first_run_asks() -> int:
    return len(ASKED.findall(text_of(INSTALL)))


class TheRepositoryDocumentsBothInstalls(unittest.TestCase):
    """The sources, before any build: the README is the entry point and
    AGENTS.md is the reference, and both have to be true."""

    def test_the_readme_has_an_entry_point_for_each_mode(self):
        readme = text_of(README)
        self.assertIn("## Install into a repo", readme)
        self.assertIn("## Install outside the repo", readme)
        self.assertLess(readme.index("## Install into a repo"),
                        readme.index("## Install outside the repo"),
                        "the vendored install is the one most readers "
                        "want, so it stays first")

    def test_the_external_entry_point_shows_a_runnable_command(self):
        body = section("readme")
        self.assertIn("install.py --app", body)
        self.assertIn("--work", body)
        self.assertIn("```bash", body)

    def test_both_roots_in_the_example_are_absolute(self):
        """A relative root is one of the refusals, so an example that
        used one would be an example that does not run."""
        for argument in re.findall(r"--(?:app|work|worktrees) (\S+)",
                                   section("readme")):
            with self.subTest(argument=argument):
                self.assertTrue(argument.startswith("/"),
                                f"{argument} is not an absolute path")

    def test_the_external_docs_name_only_flags_install_py_reads(self):
        """The README's section is the one that shows a command line, so
        an invented flag here is one a reader would paste."""
        real = flags_install_py_reads()
        self.assertIn("--app", real, "install.py no longer reads --app")
        for flag in set(LONG_FLAG.findall(section("readme"))):
            with self.subTest(flag=flag):
                self.assertIn(flag, real,
                              f"the README offers {flag}, which install.py "
                              f"does not read")

    def test_the_reference_names_every_flag_and_invents_none(self):
        """AGENTS.md says four flags decide a layout and that there are
        no others, so that paragraph is the one place a reader counts
        them. It has to name each of the four, every other flag the
        installer accepts, and nothing the installer does not."""
        body = section("agents")
        paragraph = body[body.index("Four flags decide a layout"):]
        paragraph = paragraph[:paragraph.index("\n\n")]
        named = set(LONG_FLAG.findall(paragraph))
        self.assertLessEqual({"--app", "--work", "--worktrees",
                              "--app-hooks"}, named,
                             "one of the four layout flags is unnamed")
        self.assertEqual(flags_install_py_reads(), named)

    def test_every_setting_the_external_docs_name_is_a_real_setting(self):
        """A key that is not in the settings example is a key nothing
        reads — and the Settings page is generated from that file, so it
        would be documented in one place and absent from the other."""
        real = settings_that_exist()
        for name in SECTIONS:
            for setting in sorted(set(SETTING_NAME.findall(section(name)))):
                with self.subTest(section=name, setting=setting):
                    self.assertIn(setting, real,
                                  f"{setting} is named in the docs but is "
                                  f"not a setting in "
                                  f"manager/core/.env.example")

    def test_each_git_identity_is_named_with_its_own_remote_and_base(self):
        """The failure this documentation exists to prevent is reading a
        ticket commit as an application push. Both halves have to be on
        the page, by name, or the distinction is not drawn."""
        body = section("agents")
        for setting in ("BOARD_GIT_REMOTE", "BOARD_APP_BASE",
                        "BOARD_STATE_GIT_REMOTE", "BOARD_STATE_BASE"):
            with self.subTest(setting=setting):
                self.assertIn(setting, body)

    def test_the_three_degrees_of_ticket_versioning_are_all_documented(self):
        """Local-only, committed, synced. The first is the one a reader
        would otherwise discover as a refusal."""
        body = section("agents")
        self.assertIn("git init", body,
                      "the way to make the work state a repository is not "
                      "named")
        self.assertIn("BOARD_COMMIT_MOVES", body)
        self.assertIn("BOARD_SYNC", body)

    def test_the_machine_local_state_is_named_as_uncommitted(self):
        body = section("agents")
        self.assertIn("manager/local/state/plans/", body)
        self.assertIn("manager/local/.env", body)

    def test_plans_are_reused_rather_than_restated(self):
        """The external section explains how the two features meet and
        sends the reader to the plans reference for the rest of it. A
        copy of that section here is the thing this asserts against."""
        body = section("agents")
        self.assertIn("Execution plans", body)
        for restated in ("### Where a plan comes from",
                         "### Preflight: before any git or agent side "
                         "effect"):
            self.assertNotIn(restated, body,
                             "the plans reference has been copied into "
                             "the external section")

    def test_the_question_count_in_the_docs_is_the_installers_own(self):
        """The install guide's description said three for as long as the
        installer asked three. It asks two — the test command is
        detected — so the number is read from the installer here rather
        than written down twice."""
        asked = questions_the_first_run_asks()
        self.assertEqual(2, asked,
                         "install.py no longer asks two questions: update "
                         "the README, the landing page and the install "
                         "guide's description together with this number")
        word = COUNT_WORD[asked]
        manifest = json.loads(text_of(SITE / "pages.json"))
        entry = next(page for page in manifest["pages"]
                     if page["path"] == VENDORED)
        self.assertIn(f"{word} questions", entry["description"])
        self.assertIn(f"the {word} questions", text_of(README))

    def test_no_shipped_text_still_says_three_questions(self):
        """The stale claim, hunted everywhere a reader could meet it."""
        hunted = [README, AGENTS, SITE / "pages.json",
                  *sorted((SITE / "templates").glob("*.html"))]
        for path in hunted:
            with self.subTest(path=path.relative_to(REPO).as_posix()):
                self.assertNotIn("three questions", text_of(path))


@unittest.skipUnless(HAS_MARKDOWN_IT, "markdown-it-py is not installed")
class TheSiteOffersBothInstalls(unittest.TestCase):
    """The built site: both modes findable, and the pages saying what
    their sources say."""

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="bench-external-")).resolve()
        result = run_build(REPO, cls.out)
        if result.returncode != 0:  # not assert: must survive python -O
            raise RuntimeError(f"site/build.py failed:\n{result.stdout}"
                               f"{result.stderr}")
        cls.manifest = json.loads(text_of(SITE / "pages.json"))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "out"):
            shutil.rmtree(cls.out, ignore_errors=True)

    def page(self, route: str) -> str:
        return BUILDER.target_for(self.out, route).read_text("utf-8")

    def entry(self, route: str) -> dict:
        return next(page for page in self.manifest["pages"]
                    if page["path"] == route)

    def test_every_new_route_is_a_page(self):
        for route in (EXTERNAL, WORKSPACES, OPERATIONS):
            with self.subTest(route=route):
                self.assertTrue(BUILDER.target_for(self.out, route).is_file(),
                                f"{route} produced no page")

    def test_the_two_installs_are_the_two_guides(self):
        guides = [page["path"] for page in self.manifest["pages"]
                  if page.get("section") == "Guides"]
        self.assertEqual([VENDORED, EXTERNAL], guides)

    def test_the_landing_page_offers_both(self):
        home = self.page("/")
        self.assertIn(f'href="{VENDORED}"', home)
        self.assertIn(f'href="{EXTERNAL}"', home)

    def test_the_landing_page_says_what_the_second_mode_is(self):
        """A door with no sentence beside it is a door nobody opens."""
        home = self.page("/")
        door = home[home.index(f'href="{EXTERNAL}"'):]
        self.assertIn("git repository of their own", door)

    def test_the_sidebar_reaches_both_from_anywhere(self):
        for route in (VENDORED, EXTERNAL, WORKSPACES, OPERATIONS):
            with self.subTest(route=route):
                self.assertIn(f'href="{route}"', self.page("/concepts/stages/"))

    def test_the_vendored_quick_start_is_untouched(self):
        """The new section sits after it in the README, which moved the
        guide's `to` heading. The page it produces must still be the
        vendored install and nothing else."""
        install = self.page(VENDORED)
        self.assertIn("mkdir .task-manager", install)
        self.assertIn(".task-manager/start.sh", install)
        self.assertNotIn("--app", install)
        self.assertNotIn("BOARD_APP_ROOT", install)

    def test_the_external_guide_is_the_external_section(self):
        guide = self.page(EXTERNAL)
        self.assertIn("BOARD_APP_ROOT", guide)
        self.assertIn("BOARD_WORK_ROOT", guide)
        self.assertIn("--app-hooks", guide)
        self.assertIn("install.py --app", guide)

    def test_the_four_roots_and_the_two_identities_render_as_tables(self):
        html = self.page(WORKSPACES)
        self.assertIn("<table>", html)
        self.assertIn("<th>Root</th>", html)
        self.assertIn("<th>Operation</th>", html)
        for root in ("BOARD_APP_ROOT", "BOARD_WORK_ROOT", "BOARD_WORKTREES"):
            with self.subTest(root=root):
                self.assertIn(root, html)

    def test_the_layout_diagram_arrives_as_a_code_block(self):
        html = self.page(WORKSPACES)
        self.assertIn("<pre><code", html)
        self.assertIn("a grouping directory, not a repo", html)

    def test_the_settings_link_lands_on_the_settings_reference(self):
        """The one link_routes mapping: AGENTS.md links the settings file
        and the site sends it to the page generated from that file,
        rather than out to the raw file on GitHub."""
        self.assertIn(f'href="{SETTINGS}"', self.page(WORKSPACES))

    def test_the_plans_reference_is_named_and_reachable(self):
        html = self.page(WORKSPACES)
        self.assertIn("Execution plans", html)
        self.assertIn(f'href="{PLANS}"', html,
                      "the sidebar does not reach the plans reference from "
                      "the external guide")

    def test_the_operations_page_carries_the_refusals_and_the_hooks(self):
        html = self.page(OPERATIONS)
        for promised in ("git init", "settings.local.json", "opencode",
                         "start.sh", "stop.sh"):
            with self.subTest(promised=promised):
                self.assertIn(promised, html)

    def test_the_operator_and_the_candidate_are_on_that_page(self):
        """Developing bench with bench is the one external layout a
        reader of this repository is most likely to want, and it is a
        consequence of a refusal rather than a feature with a flag."""
        html = self.page(OPERATIONS)
        self.assertIn("operator", html.lower())
        self.assertIn("candidate", html.lower())
        self.assertIn("does not manage itself", html)

    def test_each_new_page_has_a_lede_of_its_own(self):
        for route in (EXTERNAL, WORKSPACES, OPERATIONS):
            with self.subTest(route=route):
                entry = self.entry(route)
                self.assertTrue(entry.get("lede"),
                                f"{route} has no lede, so a reader arriving "
                                f"mid-document is told nothing")


@needs_renderer
class DriftOnTheExternalPages(ScratchCase):
    """The same promise the rest of the site makes: a heading renamed in
    a source stops the build, naming what it could not find. Asserted on
    the new slices rather than assumed to be inherited."""

    def test_renaming_the_external_section_stops_the_build(self):
        self.repo.edit("AGENTS.md", "## Running outside the application",
                       "## Outside the app")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode,
                            "a renamed section built cleanly")
        self.assertIn(WORKSPACES, result.stderr)
        self.assertIn("## Running outside the application", result.stderr)
        self.assertFalse(self.repo.out.exists(),
                         "a failed build wrote pages anyway")

    def test_renaming_the_readmes_external_heading_stops_the_build(self):
        self.repo.edit("README.md", "## Install outside the repo",
                       "## The other install")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("## Install outside the repo", result.stderr)
        self.assertFalse(self.repo.out.exists())

    def test_losing_the_boundary_between_the_two_concept_pages_is_named(self):
        """`### Validation fails closed` ends one page and begins the
        next, so it is the heading whose loss would silently merge
        them."""
        self.repo.edit("AGENTS.md", "### Validation fails closed",
                       "### Fail closed")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("### Validation fails closed", result.stderr)

    def test_an_empty_external_section_is_not_a_page(self):
        text = (self.repo.root / "AGENTS.md").read_text("utf-8")
        start = text.index("## Running outside the application")
        end = text.index("### Validation fails closed")
        (self.repo.root / "AGENTS.md").write_text(
            text[:start] + "## Running outside the application\n\n"
            + text[end:], encoding="utf-8")
        result = self.repo.build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("is empty", result.stderr)


if __name__ == "__main__":
    unittest.main()
