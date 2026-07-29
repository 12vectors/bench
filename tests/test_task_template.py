"""The task template's load-bearing contract. Its headings are machinery,
not prose convention: the body becomes the work agent's brief verbatim,
Acceptance is what review agents judge against, and the Open questions
heading is what the NOT READY gate keys off — so the template must keep
them, exactly, in order. Task 07 enriched the template; these tests pin
both what it gained and what it must never grow.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parents[1]
            / "tasks" / "task-template.md")

LOAD_BEARING_HEADINGS = [
    "Context", "What to build", "Acceptance", "Open questions", "Notes",
]

# Organisation process the board either does mechanically or doesn't own —
# deliberately absent from the template, and it must not grow back.
ORG_PROCESS_TERMS = (
    "staging", "sign-off", "signoff", "qa ", "product owner",
    "browser", "device matrix", "contact",
)

# The slots task 07 added, each of which earns its keep on the card.
ENRICHED_SLOTS = (
    "**Depends on:**",
    "**Affected areas:**",
    "**Out of scope**",
    "**Risks**",
    "Given <",
    "Edge case",
)


class TemplateContract(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text()

    def test_load_bearing_headings_survive_in_order(self):
        headings = re.findall(r"^## (.+)$", self.text, re.MULTILINE)
        self.assertEqual(headings, LOAD_BEARING_HEADINGS)

    def test_status_is_an_exact_board_value(self):
        self.assertIn("**Status:** Backlog\n", self.text)

    def test_open_questions_section_names_the_gate(self):
        section = self.text.split("## Open questions", 1)[1]
        section = section.split("## ", 1)[0]
        self.assertIn("`NOT READY`", section)

    def test_enriched_slots_are_present(self):
        for slot in ENRICHED_SLOTS:
            with self.subTest(slot=slot):
                self.assertIn(slot, self.text)

    def test_new_slots_say_they_are_deletable(self):
        # "delete" appears with each optional slot, so authors trim rather
        # than leave hollow sections behind.
        for slot in ("**Depends on:**", "**Affected areas:**",
                     "**Out of scope**", "**Risks**"):
            with self.subTest(slot=slot):
                paragraph = self.text.split(slot, 1)[1].split("\n\n", 1)[0]
                self.assertIn("delete", paragraph.lower())

    def test_no_organisation_process_creeps_in(self):
        lowered = self.text.lower()
        for term in ORG_PROCESS_TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term, lowered)

    def test_template_sits_outside_every_stage_directory(self):
        # The board lists stage directories only; the template must stay a
        # sibling of them, not a card.
        self.assertEqual(TEMPLATE.parent.name, "tasks")


if __name__ == "__main__":
    unittest.main()
