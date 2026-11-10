"""The setup guide's page (task 88), read as source.

The browser case (`verify/browser_cases/case_setup.py`) measures it in a
sandboxed Chromium; these pin the promises that are properties of the
code itself: the guide is a Settings section reached from an invitation
that never hides the board, it reads one GET and writes through the two
setup routes only, with the policy door's header and a request identity,
and it never computes a percentage.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "manager" / "core" / "board.html").read_text(encoding="utf-8")


def lift(name: str) -> str:
    match = re.search(rf"\n(?:async )?function {name}\(.*?\n}}\n", HTML, re.S)
    if not match:
        raise AssertionError(f"function {name} not found")
    return match.group(0)


class TheGuideIsASettingsSection(unittest.TestCase):
    def test_it_is_listed_after_the_overview_in_configure(self):
        sections = re.search(r"const SETTINGS_SECTIONS = \[(.*?)\n\];", HTML, re.S).group(1)
        keys = re.findall(r"\['(\w+)', '([^']+)', '(\w+)'", sections)
        self.assertEqual(("setup", "Setup guide", "Configure"), keys[1])
        self.assertIn("if (key === 'setup') return settingsSetup();",
                      lift("settingsSection"))

    def test_the_invitation_is_hidden_until_there_is_something_to_settle(self):
        self.assertRegex(HTML, r'id="setupchip" role="button" tabindex="0" hidden')
        body = lift("renderSetupChip")
        self.assertIn("el.hidden = !g || !g.summary.invite", body)
        self.assertIn("'Enter'", HTML[HTML.index("$('#setupchip').addEventListener('keydown'"):][:200])

    def test_the_handoff_link_opens_the_guide(self):
        self.assertIn("location.hash === '#setup'", HTML)
        boot = (REPO / "bootstrap.py").read_text(encoding="utf-8")
        self.assertIn('"setup": url + "#setup"', boot)


class ReadsOnceWritesThroughTheDoor(unittest.TestCase):
    def test_reading_is_one_get(self):
        self.assertIn("fetch('/api/setup')", lift("fetchSetup"))

    def test_writes_carry_the_header_the_revision_and_a_request_identity(self):
        body = lift("setupPost")
        self.assertIn("headers: SETTINGS_HEADERS", body)
        self.assertIn("request: setupRequestId(key)", body)
        self.assertIn("expected: S.setup.revision", body)
        wire = lift("wireSetup")
        self.assertIn("'/api/setup/task'", wire)
        self.assertIn("'/api/setup/decide'", wire)
        self.assertEqual(set(re.findall(r"'/api/setup/(\w+)'", HTML)),
                         {"task", "decide"})

    def test_a_lost_answer_keeps_the_request_identity_so_a_retry_resumes(self):
        body = lift("setupPost")
        catch = body[body.index("} catch (e) {"):]
        self.assertNotIn("delete S.setupPending", catch.split("} finally {")[0])

    def test_creating_a_card_is_armed(self):
        self.assertIn("!btn.classList.contains('armed')", lift("wireSetup"))


class NoPercentage(unittest.TestCase):
    def test_the_summary_counts_words_and_never_divides(self):
        body = lift("settingsSetup")
        self.assertNotIn("%", re.sub(r"<[^>]+>", "", body.replace("%;", "")))
        self.assertIn("g.summary.counts", body)

    def test_every_status_word_has_a_style(self):
        for word in ("decision-needed", "configured", "tested", "deferred",
                     "not-applicable", "not-inspected", "failed", "blocked"):
            self.assertRegex(HTML, rf"\.sw\.{re.escape(word)}\b", word)


if __name__ == "__main__":
    unittest.main()
