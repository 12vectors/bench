"""Reading the page through tests/pagesource.py, and counting the lifts (WP02).

Two things are held here. Every test that reads the board page reads it
through `pagesource`, so splitting the page into a shell and `web/`
modules cannot break a test about a function because the function moved
file. And the **regex lifts** — the regular expressions those tests run
over page text to find a rule, a constant or a function — are counted
per file against a reviewed baseline that may only shrink: a lift is a
bridge to a real module tested under Node (tests/test_node_modules.py),
not the destination.

What is counted, mechanically: every call of `re.search`, `re.match`,
`re.fullmatch`, `re.findall`, `re.finditer`, `re.sub`, `re.subn`,
`re.split` and `re.compile` in a test module that reads the page through
`pagesource`. That over-counts a little (some of those calls read JSON or
an API reply, not the page) and it is exact about direction, which is
what a ratchet needs: a new lift raises a count, and a lift migrated to a
real module lowers one. `python3 tests/test_pagesource.py` prints the
current counts.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import pagesource
from tests.test_architecture import ratchet

TESTS = Path(__file__).resolve().parent
REGEX_CALLS = {"search", "match", "fullmatch", "findall", "finditer", "sub",
               "subn", "split", "compile"}
# Files that may read board.html by path: the governance test names the
# route, and the progress test feeds the file name to the event pipeline
# as data. Neither reads the page's text.
NOT_READERS = {"test_architecture.py", "test_agent_progress.py", "test_pagesource.py",
               "pagesource.py"}

# The reviewed baseline: regex lifts per page-reading test module at WP02.
# It may only shrink — lower a count when a lift moves to a real module.
# Recorded at refactor/bench-architecture a155bcd: 228 lifts in 39 modules.
# WP16 (task 125) moved the arm, request and sweep checks to real modules
# under tests/node/ and lowered the counts it touched.
LIFTS = {
    "test_add_to_phase.py": 5,
    "test_agents_settings_ui.py": 4,
    "test_archive_chip.py": 23,
    "test_board_title.py": 9,
    "test_boards_sync.py": 1,
    "test_card_actions.py": 14,
    "test_card_detail_ui.py": 1,
    "test_card_face.py": 6,
    "test_checks_definition.py": 6,
    "test_column_flex.py": 5,
    "test_completing_card.py": 3,
    "test_completion_publication.py": 1,
    "test_configuration_check_ui.py": 4,
    "test_delivery_surfaces.py": 3,
    "test_discussion_ui.py": 1,
    "test_drawer_markdown.py": 14,
    "test_execution_doctor.py": 1,
    "test_failed_run_visible.py": 4,
    "test_focus_well.py": 3,
    "test_gate_invalid_wording.py": 1,
    "test_header_logo.py": 14,
    "test_log_resize.py": 6,
    "test_model_chip.py": 4,
    "test_no_agent_commands.py": 3,
    "test_phase_card_refuses_work.py": 4,
    "test_phase_cards.py": 4,
    "test_phase_members_hidden.py": 4,
    "test_phase_watch.py": 9,
    "test_phases_view.py": 4,
    "test_profile_ui.py": 2,
    "test_progress_ui.py": 1,
    "test_review_markdown.py": 14,
    "test_scroll_kept.py": 4,
    "test_session_identity.py": 1,
    "test_settings_security.py": 2,
    "test_settings_ui.py": 18,
    "test_setup_guide_ui.py": 5,
    "test_tab_agents.py": 9,
}


def _imports_pagesource(tree: ast.AST) -> bool:
    """`from tests import pagesource [as x]`, `import tests.pagesource`
    and `from tests.pagesource import page [as x]` all count: how a module
    names the helper must not take it out of the count."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "tests.pagesource":
                return True
            if node.module == "tests" and any(a.name == "pagesource" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name == "tests.pagesource" for a in node.names):
                return True
    return False


def readers(tests: Path = TESTS) -> list[Path]:
    """Test modules that read the page through pagesource, however they
    import it."""
    return sorted(p for p in tests.glob("test_*.py") if p.name not in NOT_READERS
                  and _imports_pagesource(ast.parse(p.read_text("utf-8"))))


def _shell_aliases(text: str) -> set[str]:
    """Names a module binds to the shell's path: `pagesource.SHELL` itself,
    `from tests.pagesource import SHELL [as X]` and `X = pagesource.SHELL`."""
    names = {r"pagesource\.SHELL"}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module == "tests.pagesource":
            names |= {re.escape(a.asname or a.name) for a in node.names
                      if a.name == "SHELL"}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "SHELL":
            names |= {re.escape(t.id) for t in node.targets if isinstance(t, ast.Name)}
    return names


def lifts(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
               and node.func.attr in REGEX_CALLS)


def inventory(tests: Path = TESTS) -> dict:
    """Lifts per page-reading module; a reader with none is not listed,
    so its first lift reads as new."""
    return {p.name: n for p in readers(tests) if (n := lifts(p))}


def direct_reads(tests: Path = TESTS) -> list[str]:
    """Test code that names board.html as a path instead of asking
    pagesource (string mentions in messages and comments are fine), or
    reads the shell file itself through `pagesource.SHELL` or a name bound
    to it — which would miss every module under `web/`. Using SHELL as a
    path (its `.parent`, say) is fine."""
    found = []
    for path in sorted(tests.glob("test_*.py")):
        if path.name in NOT_READERS:
            continue
        text = path.read_text("utf-8")
        shell = "|".join(sorted(_shell_aliases(text)))
        reads_shell = re.compile(rf"\b({shell})\b\s*\)?\s*\.\s*(read_text|read_bytes|open)\("
                                 rf"|\bopen\(\s*({shell})\b")
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r'"board\.html"\s*\)?\s*(\.read_|$)', line) or \
                    re.search(r'/\s*"board\.html"', line) or reads_shell.search(line):
                found.append(f"{path.name}:{n}: {line.strip()}")
    return found


class ThePageIsReadThroughPagesource(unittest.TestCase):

    def test_page_is_the_shell_then_its_stylesheet(self):
        """Since task 123 the stylesheet is web/board.css; the manifest
        beside it, and the vendored font stylesheet in web/fonts/, are not
        page source."""
        self.assertTrue((pagesource.WEB / "fonts" / "plex.css").is_file())
        files = pagesource.files()
        self.assertEqual(files[0], pagesource.SHELL)
        self.assertIn(pagesource.WEB / "board.css", files)
        # since task 124 the script is modules under web/ too
        self.assertEqual(files[1:], sorted([pagesource.WEB / "board.css",
                                            *pagesource.scripts()]))
        self.assertGreater(len(pagesource.scripts()), 10)
        self.assertNotIn(pagesource.WEB / "fonts" / "plex.css", files)
        self.assertEqual(pagesource.page(), "\n".join(
            p.read_text("utf-8") for p in files))
        self.assertTrue(pagesource.page().startswith(pagesource.shell()))

    def test_a_split_page_is_read_whole_shell_first(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-pagesource-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "board.html").write_text("<script type=module src=web/a.js>", "utf-8")
        (tmp / "web" / "parts").mkdir(parents=True)
        (tmp / "web" / "b.js").write_text("function later() {}", "utf-8")
        (tmp / "web" / "a.js").write_text("function moved() {}", "utf-8")
        (tmp / "web" / "parts" / "c.css").write_text(".kcol {}", "utf-8")
        (tmp / "web" / "manifest.json").write_text("{}", "utf-8")
        self.assertEqual([p.relative_to(tmp).as_posix() for p in pagesource.files(tmp)],
                         ["board.html", "web/a.js", "web/b.js", "web/parts/c.css"])
        page = pagesource.page(tmp)
        for text in ("function moved()", "function later()", ".kcol", "<script"):
            self.assertIn(text, page)
        self.assertLess(page.index("<script"), page.index("function moved()"))
        self.assertLess(page.index("function moved()"), page.index("function later()"))
        self.assertEqual(pagesource.shell(tmp), "<script type=module src=web/a.js>")

    def test_no_test_reads_board_html_by_path(self):
        self.assertEqual(direct_reads(), [])

    def test_the_page_readers_are_found(self):
        self.assertGreaterEqual(len(readers()), 50, "too few page readers: vacuous")

    def fixture(self, files: dict) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="bench-readers-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, text in files.items():
            (tmp / name).write_text(text, "utf-8")
        return tmp

    def test_every_import_form_is_a_reader(self):
        lift = "import re\nre.search('x', TEXT)\n"
        tmp = self.fixture({
            "test_a.py": "from tests import pagesource\nTEXT = pagesource.page()\n" + lift,
            "test_b.py": "from tests.pagesource import page\nTEXT = page()\n" + lift,
            "test_c.py": "from tests.pagesource import page as p\nTEXT = p()\n" + lift,
            "test_d.py": "import tests.pagesource\nTEXT = tests.pagesource.shell()\n" + lift,
            "test_e.py": "from tests import pagesource as ps\nTEXT = ps.page()\n" + lift,
            "test_none.py": "TEXT = 'not the page'\n" + lift,
        })
        self.assertEqual([p.name for p in readers(tmp)],
                         ["test_a.py", "test_b.py", "test_c.py", "test_d.py",
                          "test_e.py"])
        self.assertEqual(set(inventory(tmp)), {"test_a.py", "test_b.py", "test_c.py",
                                               "test_d.py", "test_e.py"})

    def test_reading_the_shell_file_directly_is_caught(self):
        tmp = self.fixture({
            "test_a.py": "from tests import pagesource\nT = pagesource.SHELL.read_text()\n",
            "test_b.py": "from tests.pagesource import SHELL\nT = SHELL.read_text('utf-8')\n",
            "test_c.py": "from tests.pagesource import SHELL as S\nT = open(S).read()\n",
            "test_d.py": "from tests import pagesource\nB = pagesource.SHELL\n"
                         "T = B.read_bytes()\n",
            "test_e.py": "import tests.pagesource\nT = tests.pagesource.SHELL.read_text()\n",
            # a path use of SHELL is not a read of the page
            "test_ok.py": "from tests import pagesource\nB = pagesource.SHELL\n"
                          "P = B.parent\nT = pagesource.page()\n",
        })
        self.assertEqual(sorted({line.split(":")[0] for line in direct_reads(tmp)}),
                         ["test_a.py", "test_b.py", "test_c.py", "test_d.py",
                          "test_e.py"])


class RegexLiftsOnlyShrink(unittest.TestCase):
    maxDiff = None

    def test_lifts_only_shrink(self):
        self.assertEqual(ratchet("regex-lifts", inventory(), LIFTS), [])

    def test_the_count_is_not_vacuous(self):
        self.assertGreater(sum(inventory().values()), 100)

    def test_a_new_lift_fails_the_ratchet(self):
        tmp = Path(tempfile.mkdtemp(prefix="bench-lifts-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        reader = tmp / "test_reader.py"
        reader.write_text("import re\nfrom tests import pagesource\n"
                          "re.search('x', pagesource.page())\n", "utf-8")
        self.assertEqual(inventory(tmp), {"test_reader.py": 1})
        self.assertTrue(ratchet("regex-lifts", inventory(tmp), {}))
        self.assertTrue(ratchet("regex-lifts", {"test_reader.py": 2},
                                {"test_reader.py": 1}))
        self.assertEqual(ratchet("regex-lifts", {"test_reader.py": 1},
                                 {"test_reader.py": 1}), [])


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=4, sort_keys=True))
    print("total", sum(inventory().values()))
