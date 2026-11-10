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


class SetupCardsPanel(unittest.TestCase):
    """Task 89: the proposed setup cards. Preview writes nothing and is
    the only way to the create button; create confirms the preview by its
    digest with the revision and a request identity; start is separate and
    armed; and nothing here moves a card or launches by itself."""

    def test_it_is_drawn_from_the_one_get(self):
        self.assertIn("setupCards(g, ro)", lift("settingsSetup"))
        self.assertNotIn("fetch(", lift("setupCards"))

    def test_create_confirms_the_preview_by_digest(self):
        wire = lift("wireSetupCards")
        self.assertIn("digest: S.setupPreview.digest", wire)
        # the create button exists only inside a preview
        body = lift("setupCards")
        self.assertLess(body.index("const preview = pv"),
                        body.index('data-cardact="create:all"'))
        post = lift("setupCardsPost")
        self.assertIn("'/api/setup/cards/' + act", post)
        self.assertIn("body.request = setupRequestId(key)", post)
        self.assertIn("body.expected = S.setup.revision", post)
        self.assertIn("headers: SETTINGS_HEADERS", post)

    def test_create_and_start_are_armed_and_separate(self):
        wire = lift("wireSetupCards")
        self.assertIn("(act === 'create' || act === 'start') && "
                      "!btn.classList.contains('armed')", wire)
        self.assertIn("setupCardsPost('start', key, { gap: gid })", wire)
        self.assertNotIn("start", lift("setupSelection"))

    def test_an_edit_after_preview_drops_the_preview(self):
        self.assertIn("if (S.setupPreview) { S.setupPreview = null;",
                      lift("wireSetupCards"))


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
