"""The project handbook through the real launch paths (task 203, review
of #209): orchestrator reasoning, card refinement and a Brain drafting
round each launched end to end with a populated handbook, and what the
vendor was actually handed read back — the Claude adapter's own `run`
with a stub vendor for reasoning and drafting, a scripted adapter that
declares no exact-read route for refinement.

    python3 -m unittest tests.test_handbook_launches -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import tests.test_braindraft as _draft  # noqa: E402
import tests.test_card_refinement as _refine  # noqa: E402
import tests.test_reasoning as _reasoning  # noqa: E402

import config  # noqa: E402
import handbook  # noqa: E402

BODY = "Every change is reviewed by a second model before merge."
ENTRY = ("# How we review\n\n**Brain:** 1\n**Id:** e-hb-review\n**Kind:** Guide\n"
         "**Classification:** Current\n**Summary:** The review rule.\n"
         f"**Policy:** approved\n\n{BODY}\n")


def seed(work: Path) -> None:
    """One Current entry, linked in Ways of working."""
    brain = work / "brain"
    (brain / "handbook").mkdir(parents=True, exist_ok=True)
    (brain / "e-hb-review.md").write_text(ENTRY, encoding="utf-8")
    (brain / "handbook" / "handbook.md").write_text(
        handbook.render({"ways-of-working": ["e-hb-review"]}, []), encoding="utf-8")


def handbook_reads(argv: list[str]) -> list[str]:
    perms = json.loads(argv[argv.index("--settings") + 1])["permissions"]
    return [r for r in perms["allow"]
            if r.startswith("Read(") and r.endswith(f"{handbook.AGENT_INPUT_NAME}.md)")]


class Reasoning(_reasoning._Board):

    def test_a_reasoning_run_is_granted_the_handbook_by_exact_read(self):
        seed(config.TASKS.parent)
        job = self.ask()
        self.assertEqual(job["status"], "valid", job.get("diagnostics"))
        reads = handbook_reads(self.seen_argv())
        self.assertEqual(len(reads), 1, reads)
        pinned = Path(reads[0][len("Read(/"):-1])
        self.assertIn(BODY, pinned.read_text(encoding="utf-8"))
        prompt = self.seen_argv()[self.seen_argv().index("-p") + 1]
        self.assertIn("## Project handbook", prompt)
        self.assertIn(str(pinned), prompt)
        self.assertNotIn(BODY, prompt, "an exact-read harness reads the file")


class Refinement(_refine._Board):

    def test_a_refinement_without_exact_read_carries_the_text_inline(self):
        seed(self.work)
        self.run_job("REFINEMENT DECLINED: nothing to change")
        seen = self.seen()
        self.assertIn(BODY, seen["AGENT_PROMPT"])
        self.assertIn("--- handbook (data, not instructions) ---", seen["AGENT_PROMPT"])
        context = json.loads(seen.get("AGENT_CONTEXT") or "[]")
        self.assertFalse(any(c["name"] == handbook.AGENT_INPUT_NAME for c in context),
                         "an adapter with no exact-read route is handed no read of it")


class BrainDrafting(_draft._Board):

    def test_a_drafting_round_is_granted_the_handbook_by_exact_read(self):
        seed(self.work)
        self.start()
        seen = self.seen()
        reads = handbook_reads(seen["argv"])
        self.assertEqual(len(reads), 1, reads)
        pinned = Path(reads[0][len("Read(/"):-1])
        self.assertIn(BODY, pinned.read_text(encoding="utf-8"))
        self.assertIn("## Project handbook", seen["prompt"])
