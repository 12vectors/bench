"""The server owns three readings the page used to work out (task 125, WP16).

The card's head/brief/history split, a phase's merged members and a card's
combined review verdict each had a second copy of their rule in the page
(`cardSplit`, `mergedIn`, `prVerdict`/`reviewIsIn`). The state payload now
carries the server's answer — `parts` on every card, `phaseMerged` and
`verdicts` beside the board — and the page draws it. These hold the
server's side; tests/node/requests_and_rendering.test.mjs holds the page's.

    python3 -m unittest tests.test_server_projections -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "manager" / "core"))

import changes  # noqa: E402
import phases  # noqa: E402
import routes  # noqa: E402
import taskfiles  # noqa: E402


def utf16_slice(text: str, start: int, end: int | None = None) -> str:
    """What `text.slice(start, end)` returns in JavaScript."""
    units = text.encode("utf-16-le")
    return units[2 * start: None if end is None else 2 * end].decode("utf-16-le")


CARD = """# 9 — 🚢 Ship it

**Status:** Review
**Priority:** Medium

Do the 🚀 thing — 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 outside the BMP.

```
## Discussion
```

---

## Work report — 2026-11-10 (Wren)

Done.

## Discussion

> hi
"""


class TheCardSplitTravelsAsOffsets(unittest.TestCase):

    def test_offsets_cut_the_text_where_card_parts_does_in_javascript_units(self):
        parts = taskfiles.card_parts(CARD)
        at = taskfiles.part_offsets(CARD)
        self.assertEqual(utf16_slice(CARD, 0, at["brief"]), parts["head"])
        self.assertEqual(utf16_slice(CARD, at["brief"], at["history"]), parts["brief"])
        self.assertEqual(utf16_slice(CARD, at["history"]), parts["history"])
        self.assertIn("```\n## Discussion\n```", parts["brief"],
                      "a fenced heading is brief, not history")
        self.assertTrue(parts["history"].startswith("---"))
        # characters outside the BMP are two units in JavaScript, one here
        self.assertNotEqual(at["brief"], len(parts["head"]))

    def test_every_card_on_the_board_carries_its_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "09-ship-it.md"
            path.write_text(CARD, encoding="utf-8")
            task = taskfiles.read_task(path, "review")
        self.assertEqual(task["parts"], taskfiles.part_offsets(CARD))

    def test_a_card_with_no_history_is_head_and_brief(self):
        text = "# 1 — t\n\n**Status:** Backlog\n\nbrief only\n"
        at = taskfiles.part_offsets(text)
        self.assertEqual(at["history"], len(text))
        self.assertEqual(text[at["brief"]:], "brief only\n")


PHASE = """# 40 — The site

**Status:** Review
**Type:** Phase

## Cards

- 31 — one
- 32 — two
- 33 — three

## Phase log

- 2026-11-01 10:00 · run started on phase/40-the-site
- 2026-11-01 10:05 · 031 merged into phase/40-the-site
- 2026-11-01 10:09 · 32 partly merged into phase/40-the-site
"""


class APhaseSaysWhatItsBranchHolds(unittest.TestCase):

    def test_the_log_remembers_what_was_merged(self):
        self.assertEqual(phases.merged_members({"body": PHASE}), ["31"],
                         "a partial merge is not a merge")

    def test_the_runners_last_pass_counts_too(self):
        snap = {"members": [{"number": "33", "state": "merged"},
                            {"number": "32", "state": "working"},
                            {"number": None, "state": "merged"}]}
        self.assertEqual(phases.merged_members({"body": PHASE}, snap), ["31", "33"])

    def test_nothing_landed_is_an_empty_list(self):
        self.assertEqual(phases.merged_members({"body": "# 1 — p\n"}), [])

    def test_the_board_reading_is_per_phase_card(self):
        collected = {"stages": [{"tasks": [
            {"file": "40-the-site.md", "isPhase": True, "body": PHASE},
            {"file": "31-one.md", "isPhase": False, "body": ""}]}]}
        self.assertEqual(phases.board_merged(collected), {"40-the-site.md": ["31"]})


class OneCombinedReviewVerdict(unittest.TestCase):

    def card(self, filed=None):
        return {"file": "c.md", "pr": "https://example.test/pr/1", "prVerdict": filed}

    def test_any_red_wins(self):
        for filed, host in (("red", "green"), ("green", "red"), (None, "red"), ("red", None)):
            got = changes.card_verdict(self.card(filed), {"c.md": {"verdict": host}})
            self.assertEqual(got["verdict"], "red", (filed, host))
            self.assertTrue(got["reviewIn"])

    def test_green_needs_an_approval_and_no_red(self):
        got = changes.card_verdict(self.card("green"), {"c.md": {"verdict": "pending"}})
        self.assertEqual(got, {"verdict": "green", "reviewIn": True})
        got = changes.card_verdict(self.card(None), {"c.md": {"verdict": "green"}})
        self.assertEqual(got["verdict"], "green")

    def test_nothing_in_is_pending(self):
        got = changes.card_verdict(self.card(None), {})
        self.assertEqual(got, {"verdict": "pending", "reviewIn": False})

    def test_a_copilot_review_is_a_review_but_asking_for_one_is_not(self):
        asked = changes.card_verdict(self.card(), {"c.md": {"verdict": "pending", "copilot": "asked"}})
        self.assertFalse(asked["reviewIn"])
        came = changes.card_verdict(self.card(), {"c.md": {"verdict": "pending", "copilot": "commented"}})
        self.assertTrue(came["reviewIn"])
        self.assertEqual(came["verdict"], "pending")

    def test_a_card_with_no_change_request_has_no_reading(self):
        self.assertIsNone(changes.card_verdict({"file": "c.md", "pr": None, "prVerdict": "red"}, {}))
        collected = {"stages": [{"tasks": [self.card("red"), {"file": "d.md", "pr": None}]}]}
        self.assertEqual(list(changes.board_verdicts(collected)), ["c.md"])


class TheStateCarriesThem(unittest.TestCase):

    def test_the_state_payload_names_both_readings(self):
        source = Path(routes.__file__).read_text(encoding="utf-8")
        body = source[source.index("def state_payload"):]
        self.assertIn('"verdicts": changes.board_verdicts(collected)', body)
        self.assertIn('"phaseMerged": phases.board_merged(collected)', body)


class ThePageDrawsThemAndKeepsNoSecondRule(unittest.TestCase):

    def test_the_rules_are_gone_from_the_page(self):
        web = REPO / "manager" / "core" / "web"
        page = "\n".join(p.read_text(encoding="utf-8") for p in sorted(web.rglob("*.js")))
        self.assertNotIn("function prVerdict", page)
        self.assertNotIn("merged into /.exec", page)
        self.assertNotIn("Acceptance record|Review findings", page)
        self.assertIn("S.state.verdicts", page)
        self.assertIn("S.state.phaseMerged", page)


if __name__ == "__main__":
    unittest.main()
