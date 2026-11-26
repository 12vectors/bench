"""The shared agent conversation panel (task 205): the boundaries that
hold whatever the page draws.

The panel is a presentation shell. It must not be able to send anything
itself, it adds no route or store on the server, and its entry points
are explicit links rather than anything that runs on reading. The
behaviour — slots per target, unsent text and request identities kept
across navigation and reload — is held in tests/node/agent_panel.test.mjs;
the journeys in a real browser are verify/browser_cases/case_task_questions.py
and case_brain_agent.py.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "manager" / "core"
WEB = CORE / "web"


class TheShellSendsNothing(unittest.TestCase):
    def test_panel_imports_no_network_and_writes_only_this_tabs_storage(self):
        text = (WEB / "lib" / "panel.js").read_text("utf-8")
        imports = re.findall(r"^import .* from '([^']+)';", text, re.M)
        self.assertEqual(sorted(imports), ["./dom.js", "./layout.js"])
        for word in ("fetch(", "post(", "XMLHttpRequest", "sendBeacon", "EventSource"):
            self.assertNotIn(word, text, f"the panel shell must not send anything ({word})")
        # only sessionStorage holds conversations; localStorage only a width
        self.assertEqual(re.findall(r"localStorage\.setItem\(([A-Z_]+)", text), ["WIDTH"])

    def test_no_route_was_added_for_it(self):
        routes = (CORE / "routes.py").read_text("utf-8")
        paths = re.findall(r"Route\(\"[A-Z]+\", \"([^\"]+)\"", routes)
        self.assertTrue(paths, "the route table was read")
        self.assertEqual([p for p in paths if "panel" in p or "conversation" in p], [],
                         "the panel rides the routes its agents already own")

    def test_the_orchestrator_panel_posts_only_to_the_ask_routes(self):
        text = (WEB / "views" / "orchestrator.js").read_text("utf-8")
        start = text.index("function panelAction(")
        end = text.index("/* ── overview")
        posted = set(re.findall(r"send\('(/api/[a-z/]+)'", text[start:end]))
        self.assertEqual(posted, {"/api/ask", "/api/ask/cancel", "/api/ask/retry"})


class ThePageMountsOnePanel(unittest.TestCase):
    def test_board_html_has_one_panel_beside_the_views(self):
        html = (CORE / "board.html").read_text("utf-8")
        self.assertEqual(html.count('id="agentpanel"'), 1)
        main = html[html.index("<main>"):html.index("</main>")]
        self.assertIn('id="agentpanel"', main, "a flex sibling of the view, so the page stays in view")
        self.assertIn('data-agent-ask="orch"', html, "the Board's bar asks about the board")

    def test_entry_points_are_links_that_a_new_tab_can_still_take(self):
        words = (WEB / "lib" / "orchwords.js").read_text("utf-8")
        self.assertIn("href=\"${esc(orchHref('ask', id))}\"", words)
        self.assertIn("askAttrs('orch', target)", words)


if __name__ == "__main__":
    unittest.main()
