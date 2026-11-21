"""Scoped orchestration is documented for both readers, and the website
really carries it (task 146).

The canonical text is AGENTS.md's "Orchestration" section — a guide for
the product owner first, then the technical reference — and README.md's
introduction. The site cuts its pages out of them (site/pages.json), so
these tests build the site and read the generated pages back: a mapping
whose heading moved, or a page that silently lost its body, fails here.

    python3 -m unittest tests.test_orchestration_docs -v
"""

from __future__ import annotations

import html
import re
import unittest

from tests.test_site_pages import BuiltSite
from tests.test_site_build import REPO

AGENTS = (REPO / "AGENTS.md").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")

PAGES = {
    "/guides/orchestration/": ("technical project manager",
                               "Export the table as CSV", "Include hidden columns?",
                               "What it never does", "Turning it on, the first time"),
    "/concepts/orchestration-agreements/": ("Off until a person turns it on",
                                            "Last mover, not last editor",
                                            # task 182
                                            "Shared capacity, first come first served",
                                            "Migrating a reserved agreement",
                                            "uses 1 slot, not 2"),
    "/concepts/orchestration-reasoning/": ("The response, version 1",
                                           "The card keeps everything"),
    "/concepts/orchestration-scheduling/": ("Declared overlap (D10)",
                                            "Recovery: diagnose, don", "circuit breaker"),
    "/concepts/orchestration-delivery/": ("Independent means a different model",
                                          "Phase scopes: one scheduler per phase",
                                          "in the phase branch, not delivered"),
    "/guides/ask-orchestrator/": ("Ask about this card", "Ask is not act",
                                  "Where replies are kept", "Asking needs no orchestration authority"),
    "/concepts/orchestrator-view/": ("Nine sections",
                                     # task 184
                                     "The decision trace: why it acted",
                                     "Repeated waits are folded, never dropped",
                                     "What is proved end to end",
                                     "Maintenance boundaries"),
}


def text_of(page: str) -> str:
    """The page's prose as a reader sees it: tags dropped, entities read."""
    body = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(body))


class TheCanonicalText(unittest.TestCase):

    def test_the_owners_guide_names_every_decision(self):
        guide = AGENTS.split("### Start here: you own the product", 1)[1].split(
            "### Operating agreements, scopes and one installation", 1)[0]
        for number in range(1, 17):
            self.assertRegex(guide, rf"\| D{number} \|", f"D{number} is in the map")
        for words in ("product owner", "Move one card into To Do", "different model",
                      "ungated", "Upgrading bench never turns it on"):
            self.assertIn(words, guide)

    def test_every_section_the_map_points_at_exists(self):
        guide = AGENTS.split("**The sixteen decisions it is built on**", 1)[1].split(
            "###", 1)[0]
        headings = re.findall(r"^#{2,3} (.+)$", AGENTS, re.M)
        for pointer in re.findall(r'"([^"]+)"', guide):
            stem = pointer.rstrip("…").strip()
            self.assertTrue(any(h.startswith(stem) for h in headings),
                            f"the map points at {pointer!r}, which is no heading")

    def test_the_journeys_named_in_the_docs_exist(self):
        for path in ("tests/test_orchestration_journey.py",
                     "tests/test_orchestration_install.py",
                     "tests/product/fakehost.py",
                     "develop/reasoning-probe.py", "develop/claude-access-smoke.py"):
            self.assertIn(path, AGENTS)
            self.assertTrue((REPO / path).is_file(), path)

    def test_the_readme_introduces_it_for_a_casual_reader(self):
        self.assertIn("orchestrator", README)
        self.assertIn("Delegate work to an orchestrator", README)
        self.assertIn("off until you apply an operating", README.replace("\n", " "))


class TheSiteCarriesIt(BuiltSite):

    def test_every_orchestration_page_is_built_with_its_text(self):
        for route, phrases in PAGES.items():
            with self.subTest(route=route):
                entry = next(p for p in self.manifest["pages"] if p["path"] == route)
                self.assertEqual(entry["source"], "AGENTS.md")
                prose = text_of(self.page(route))
                for phrase in phrases:
                    self.assertIn(phrase, prose, f"{route} lacks {phrase!r}")

    def test_the_pages_cover_the_section_without_a_gap(self):
        """Each page starts where the previous one stops, from the guide to
        the next top-level section, so no part of the section is missing
        from the site."""
        entries = [p for p in self.manifest["pages"] if p["path"] in PAGES]
        for before, after in zip(entries, entries[1:]):
            self.assertEqual(before["to"], after["from"], after["path"])
        self.assertEqual(entries[-1]["to"], "## Pull requests")
        self.assertEqual(entries[0]["from"],
                         "### Start here: you own the product, the orchestrator "
                         "runs the project")


if __name__ == "__main__":
    unittest.main()
