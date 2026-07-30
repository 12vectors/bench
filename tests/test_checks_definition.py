"""Project-owned definition-of-done checks (task 03).

What counts as a check is project knowledge: core ships a default
definition (core/checks), a same-named file in local/ replaces it
wholesale, the claude adapter classifies agent commands against the
resolved file, and the Focus panel renders one row per entry from the
same definition served over /api/state. These tests pin the resolution
order, the two parsers' agreement, the generic pass/fail judgment that
replaced per-tool output parsing, and the absence of the origin
project's stack anywhere else in core.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))
sys.path.insert(0, str(REPO / "manager" / "core" / "adapters" / "claude"))

import config  # noqa: E402
import emit  # noqa: E402

CORE = REPO / "manager" / "core"


class ShippedDefault(unittest.TestCase):
    """A project defining nothing sees today's rows — as the default."""

    def test_default_rows_and_patterns(self):
        original = config.LOCAL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.LOCAL = Path(tmp)  # no local/checks
                checks = config.checks()
        finally:
            config.LOCAL = original
        self.assertEqual([c["label"] for c in checks],
                         ["pytest", "lint-imports", "frontend"])
        by_label = {c["label"]: c["pattern"] for c in checks}
        self.assertTrue(re.search(by_label["pytest"], "python3 -m pytest -q"))
        self.assertTrue(re.search(by_label["lint-imports"], "lint-imports"))
        for cmd in ("npm run test", "vue-tsc --noEmit", "npx vitest run"):
            self.assertTrue(re.search(by_label["frontend"], cmd), cmd)
        self.assertFalse(re.search(by_label["pytest"], "python3 -m unittest"))


class LocalOverride(unittest.TestCase):
    """local/checks beats core/checks wholesale, like prompts."""

    def _with_local(self, text):
        original = config.LOCAL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.LOCAL = Path(tmp)
                (Path(tmp) / "checks").write_text(text, encoding="utf-8")
                return config.checks()
        finally:
            config.LOCAL = original

    def test_local_definition_wins(self):
        checks = self._with_local("# ours\nsmoke: \\bmake smoke\\b\n"
                                  "types: \\bmypy\\b\n")
        self.assertEqual([c["label"] for c in checks], ["smoke", "types"])

    def test_replacement_is_wholesale_even_when_empty(self):
        self.assertEqual(self._with_local("# nothing to check\n"), [])

    def test_malformed_lines_are_skipped(self):
        checks = self._with_local("no separator here\n"
                                  ": pattern without label\n"
                                  "label without pattern:\n"
                                  "bad-regex: [unclosed\n"
                                  "good: \\bok\\b\n")
        self.assertEqual([c["label"] for c in checks], ["good"])

    def test_bench_defines_its_own(self):
        """Bench's real local/checks: the stdlib suite, so the self-hosted
        Focus view shows a check that can actually run here."""
        checks = config.checks()
        self.assertEqual([c["label"] for c in checks], ["unittest"])
        self.assertTrue(re.search(checks[0]["pattern"], "python3 -m unittest"))


class AdapterReadsTheSameFile(unittest.TestCase):
    """emit.py resolves and parses the identical definition, so a label
    edit flows to classification and rendering alike."""

    def test_parsers_agree_on_the_shipped_default(self):
        original = emit.MANAGER
        try:
            emit.MANAGER = Path(tempfile.mkdtemp())  # no local, no core
            (emit.MANAGER / "core").mkdir()
            (emit.MANAGER / "core" / "checks").write_text(
                (CORE / "checks").read_text(encoding="utf-8"), encoding="utf-8")
            adapter_view = [(label, pattern.pattern)
                            for label, pattern in emit.check_defs()]
        finally:
            emit.MANAGER = original
        core_original = config.LOCAL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.LOCAL = Path(tmp)
                board_view = [(c["label"], c["pattern"]) for c in config.checks()]
        finally:
            config.LOCAL = core_original
        self.assertEqual(adapter_view, board_view)

    def test_local_wins_in_the_adapter_too(self):
        original = emit.MANAGER
        try:
            with tempfile.TemporaryDirectory() as tmp:
                emit.MANAGER = Path(tmp)
                (Path(tmp) / "core").mkdir()
                (Path(tmp) / "core" / "checks").write_text(
                    "core-only: \\bx\\b\n", encoding="utf-8")
                (Path(tmp) / "local").mkdir()
                (Path(tmp) / "local" / "checks").write_text(
                    "ours: \\bmake check\\b\n", encoding="utf-8")
                defs = emit.check_defs()
        finally:
            emit.MANAGER = original
        self.assertEqual([label for label, _ in defs], ["ours"])

    def test_missing_files_mean_no_checks_not_a_crash(self):
        original = emit.MANAGER
        try:
            with tempfile.TemporaryDirectory() as tmp:
                emit.MANAGER = Path(tmp)
                self.assertEqual(emit.check_defs(), [])
        finally:
            emit.MANAGER = original


class Classification(unittest.TestCase):
    """Bash commands classify against the resolved definitions; the label
    carries into the summary; judgment is generic, not per-tool."""

    def setUp(self):
        self._original = emit.MANAGER
        self._tmp = tempfile.TemporaryDirectory()
        emit.MANAGER = Path(self._tmp.name)
        (emit.MANAGER / "core").mkdir()
        (emit.MANAGER / "core" / "checks").write_text(
            "suite: \\bunittest\\b\nlint: \\bmake lint\\b\n", encoding="utf-8")

    def tearDown(self):
        emit.MANAGER = self._original
        self._tmp.cleanup()

    def _bash(self, cmd, out="", hook="PostToolUse"):
        return emit.classify(hook, "Bash", {"command": cmd},
                             {"stdout": out, "stderr": ""})

    def test_matching_command_becomes_a_check_with_its_label(self):
        ev = self._bash("python3 -m unittest discover -s tests",
                        "Ran 7 tests in 0.1s\n\nOK")
        self.assertEqual(ev["kind"], "check")
        self.assertTrue(ev["ok"])
        self.assertTrue(ev["summary"].startswith("suite — "))

    def test_label_edits_flow_to_the_event(self):
        (emit.MANAGER / "core" / "checks").write_text(
            "renamed: \\bunittest\\b\n", encoding="utf-8")
        ev = self._bash("python3 -m unittest", "OK")
        self.assertTrue(ev["summary"].startswith("renamed — "))

    def test_counted_failures_fail(self):
        ev = self._bash("python3 -m unittest", "2 failed, 5 passed in 1.2s")
        self.assertEqual(ev["kind"], "check")
        self.assertFalse(ev["ok"])
        self.assertIn("2 failed", ev["summary"])
        self.assertIn("5 passed", ev["summary"])

    def test_unjudgeable_output_stays_neutral(self):
        ev = self._bash("make lint", "some chatter")
        self.assertEqual(ev["kind"], "check")
        self.assertIsNone(ev["ok"])

    def test_unmatched_commands_stay_commands(self):
        self.assertEqual(self._bash("ls -la")["kind"], "command")

    def test_git_classification_survives(self):
        ev = self._bash('git commit -m "a message"')
        self.assertEqual(ev["kind"], "git")

    def test_running_events_keep_the_check_kind(self):
        ev = self._bash("python3 -m unittest", hook="PreToolUse")
        self.assertEqual(ev["kind"], "check")
        self.assertTrue(ev["running"])


class GenericJudgment(unittest.TestCase):
    """No tool names: counts, broken totals and OK/FAILED verdict lines."""

    def test_ladder(self):
        cases = [
            ("3 passed in 0.5s", True),
            ("1 failed, 2 passed", False),
            ("2 errors", False),
            ("Ran 7 tests in 0.1s\n\nOK", True),
            ("Ran 7 tests in 0.1s\n\nFAILED (failures=1)", False),
            ("0 broken contracts", True),
            ("4 broken contracts", False),
            ("nothing recognizable", None),
            ("", None),
        ]
        for out, expected in cases:
            ok, _ = emit.judge(out)
            self.assertEqual(ok, expected, f"judge({out!r})")


class ServedToTheBrowser(unittest.TestCase):
    """The state API carries the definition; Focus renders from it with
    no fixed rows and no duplicated patterns."""

    @classmethod
    def setUpClass(cls):
        cls.html = (CORE / "board.html").read_text(encoding="utf-8")

    def test_state_payload_includes_checks(self):
        import httpd
        payload = httpd.state_payload()
        self.assertIn("checks", payload)
        self.assertEqual(payload["checks"], config.checks())

    def test_focus_renders_from_served_definition(self):
        self.assertIn("S.state?.checks", self.html)
        self.assertIn("new RegExp(c.pattern)", self.html)
        self.assertIn("no checks defined", self.html)

    def test_no_fixed_rows_or_duplicated_patterns(self):
        for fossil in ("lint-imports", "vitest", "vue-tsc", "pytest"):
            self.assertNotIn(fossil, self.html, f"board.html still hardcodes {fossil}")


class NoFossilsInCore(unittest.TestCase):
    """Nothing in manager/core/ names the origin project's stack outside
    the shipped default checks definition."""

    def test_core_is_clean(self):
        allowed = CORE / "checks"
        pattern = re.compile(r"pytest|lint-imports|vue-tsc|vitest")
        for path in sorted(CORE.rglob("*")):
            if not path.is_file() or path == allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertIsNone(pattern.search(text),
                              f"{path.relative_to(REPO)} names a stack "
                              "that belongs in the checks definition")


if __name__ == "__main__":
    unittest.main()
