"""The closing-report contract shipped in the core prompt templates:
every template carries the identical reader-first block (so the four
never drift apart), the board's machine-parsed marker lines survive it,
the templates still render through str.format, and a local/prompts/
override still beats the core default."""

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import config  # noqa: E402

PROMPTS = REPO / "manager" / "core" / "prompts"
TEMPLATES = ("work.md", "review.md", "review-pr.md", "act-pr.md")

# The first line of the shared block; the block runs from here to EOF in
# every template.
SENTINEL = "Whatever the outcome, write the report itself for its reader"

# Dummy values for every placeholder any template uses.
FIELDS = {"branch": "task/x", "stage": "backlog", "filename": "x.md",
          "body": "content", "pr": "https://example.test/pr/1"}


def _tail(name: str) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    idx = text.find(SENTINEL)
    if idx < 0:
        raise AssertionError(f"{name} lost the shared report contract")
    return text[idx:]


class SharedContract(unittest.TestCase):
    def test_block_identical_across_all_templates(self):
        reference = _tail(TEMPLATES[0])
        for name in TEMPLATES[1:]:
            self.assertEqual(_tail(name), reference,
                             f"{name} drifted from work.md's report contract")

    def test_contract_names_audience_and_ordering(self):
        block = " ".join(_tail("work.md").split())
        self.assertIn("did not watch the work", block)
        self.assertIn("Lead with the state of the work", block)
        self.assertIn("short list", block)
        self.assertIn("repo-relative", block)


class MarkerLines(unittest.TestCase):
    """agents.py finds these exact strings in the log; the prompts must
    keep demanding them verbatim."""

    def test_work_keeps_not_ready(self):
        text = (PROMPTS / "work.md").read_text(encoding="utf-8")
        self.assertIn("NOT READY: <one-line reason>", text)
        self.assertIn("FIRST line", text)

    def test_review_keeps_relevance_verdicts(self):
        text = (PROMPTS / "review.md").read_text(encoding="utf-8")
        self.assertIn("RELEVANCE REVIEW: <Still relevant | Partly done | "
                      "Already done | Needs rewrite>", text)

    def test_review_pr_keeps_verdict(self):
        text = (PROMPTS / "review-pr.md").read_text(encoding="utf-8")
        self.assertIn("PR REVIEW: <APPROVE | REQUEST CHANGES>", text)

    def test_act_pr_keeps_addressed(self):
        text = (PROMPTS / "act-pr.md").read_text(encoding="utf-8")
        self.assertIn("ADDRESSED: <one line on what changed>", text)


class TemplatesRender(unittest.TestCase):
    """Prompts are filled via str.format, so an unescaped brace anywhere
    (the contract block included) breaks the launch."""

    def test_all_templates_format_cleanly(self):
        for name in TEMPLATES:
            text = (PROMPTS / name).read_text(encoding="utf-8")
            rendered = text.format(**FIELDS)
            self.assertTrue(rendered.strip(), f"{name} rendered empty")
            self.assertIn(SENTINEL, rendered)


class LocalOverrideStillWins(unittest.TestCase):
    """local/prompts/<name> beats core/prompts/<name> — resolution order
    untouched by the contract change."""

    def test_override_and_fallback(self):
        original = config.LOCAL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.LOCAL = Path(tmp)
                core_default = config.prompt("work.md")
                self.assertIn(SENTINEL, core_default)

                (Path(tmp) / "prompts").mkdir()
                (Path(tmp) / "prompts" / "work.md").write_text(
                    "project override", encoding="utf-8")
                self.assertEqual(config.prompt("work.md"), "project override")
        finally:
            config.LOCAL = original


if __name__ == "__main__":
    unittest.main()
