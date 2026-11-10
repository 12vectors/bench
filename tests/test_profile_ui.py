"""What the board says about a run's permissions and inputs (task 67).

board.html is a single file with inline JS and no frontend test runner, so
these are source-level invariants — the ones that, if broken, would put
the page back where this card found it: a page that cannot say what a run
may do, or one that says it from a second settings parser that could
disagree with the launch.

Four of them are load-bearing:

- **One resolver.** The sheet and the Focus panel both read the server's
  resolved manifest (`/api/profile`, `/api/manifest`). Nothing in the page
  may compute a grant, a root or a readable status from the settings.
- **A run's frozen policy is not the current one.** The panel reads the
  recorded manifest by agent id, and says so when the settings have moved.
- **Configured is not enforced.** The page never calls a prefix allow-rule
  a sandbox, and it names who answers for each guarantee.
- **No contents.** Hashes and identities are shown; approved documents'
  contents are never fetched or drawn.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "manager" / "core" / "board.html"
sys.path.insert(0, str(REPO / "manager" / "core"))

import httpd  # noqa: E402
import profiles  # noqa: E402


class OneResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_sheet_asks_the_server_for_the_resolved_profile(self):
        # the launch check (task 79) asks the same route for the same card
        self.assertIn("read('/api/profile?' + q", self.html)
        self.assertIn("'file=' + encodeURIComponent(task.file)", self.html)
        self.assertIn("&intent=", self.html)

    def test_the_focus_panel_reads_the_recorded_manifest_by_agent(self):
        self.assertIn("'/api/manifest?agent='", self.html)
        self.assertRegex(self.html, r"function fetchManifest\(agentId\)")
        self.assertIn("S.manifests[agent.id]", self.html)

    def test_the_page_never_compiles_a_grant_of_its_own(self):
        """The one thing a second parser would do. Nothing in the page may
        split a command list or decide which intent gets which built-in
        git grant — those answers belong to profiles.py and reach the page
        already resolved.

        Naming `BOARD_AGENT_COMMANDS` in prose is a different thing and is
        allowed: the header's `no agent commands` chip tells a reader which
        setting to go and fill in. What it must never do is read it, so the
        two places it appears are checked to be sentences."""
        for forbidden in ("MODE_PREFIXES", "split_commands",
                          "DENY_CAPABILITY", "agent_commands"):
            self.assertNotIn(forbidden, self.html,
                             f"the page resolves {forbidden} itself")
        for m in re.finditer(r"BOARD_AGENT_COMMANDS", self.html):
            line = self.html[self.html.rfind("\n", 0, m.start()):
                             self.html.find("\n", m.end())]
            self.assertRegex(line, r'["`\']',
                             "BOARD_AGENT_COMMANDS is read rather than named")

    def test_the_grant_and_input_rows_read_the_served_fields(self):
        self.assertRegex(self.html, r"function grantRows\(found\)")
        self.assertRegex(self.html, r"function inputRows\(found\)")
        # the row's state is the server's `status`, not a guess from the path
        self.assertIn("INPUT_STATE[i.status]", self.html)
        self.assertIn("found.commands", self.html)
        self.assertIn("found.context", self.html)

    def test_both_surfaces_render_the_same_rows(self):
        """One renderer each for grants and inputs, called from the sheet
        and the Focus panel — so a card's pre-launch reading and a run's
        recorded one cannot drift into two vocabularies."""
        for name in ("grantRows", "inputRows", "guaranteeLines",
                     "profileDetail"):
            calls = len(re.findall(rf"\b{name}\(", self.html))
            self.assertGreaterEqual(calls, 3,
                                    f"{name} is declared but not shared "
                                    f"between the sheet and the panel")


class ConfiguredIsNotEnforced(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_page_never_calls_a_prefix_rule_a_sandbox(self):
        shown = self.html.lower()
        for claim in ("sandboxed", "sandbox:", "confined to",
                      "cannot read anything else"):
            self.assertNotIn(claim, shown,
                             f"the page claims {claim!r}, which no shipped "
                             f"adapter delivers")

    def test_the_enforcement_fold_names_who_answers(self):
        self.assertIn("what is actually enforced, and by whom", self.html)
        self.assertIn("Narrowed by prefix match, not confined", self.html)
        self.assertIn("Asked for, enforced by nothing yet", self.html)
        # and the server's own caveat is carried rather than reworded
        self.assertIn("g.caveat", self.html)

    def test_the_guarantee_lines_name_the_party_for_each_capability(self):
        self.assertRegex(self.html, r"\(table\[n\] \|\| \{\}\)\.by")

    def test_blockers_lead_with_the_remedy_the_server_wrote(self):
        # the launch check leads with the refusal and each diagnostic's fix
        self.assertIn("Blocked — this launch would be refused", self.html)
        self.assertIn("fix: d.remedy", self.html)
        self.assertIn("(p.blockers || []).forEach", self.html)
        self.assertIn("i.remedy", self.html)

    def test_the_sheet_says_nothing_was_pinned(self):
        self.assertIn("nothing was pinned", self.html)


class AFrozenPolicyIsNotTheCurrentOne(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_panel_says_when_the_settings_have_moved(self):
        self.assertIn("settings have moved", self.html)
        self.assertIn("this run keeps what it rode", self.html)
        self.assertIn("cached.freshness", self.html)

    def test_the_runs_own_revisions_fold_separately(self):
        self.assertIn("this run's frozen revisions", self.html)
        self.assertIn("Policy, revisions and provenance", self.html)

    def test_a_session_with_no_agent_is_told_it_was_granted_nothing(self):
        self.assertIn("your own session", self.html)
        self.assertIn("bench granted it nothing", self.html)


class TheChipOnlyAppearsWhenThereIsSomethingToSay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_a_legacy_board_gets_no_chip(self):
        """The same law the plan chip follows: a board that configured
        nothing renders exactly what it always did."""
        window = self.html[self.html.index("const policy = S.state.policy;"):]
        window = window[:window.index("if (prState && prState.ci)")]
        self.assertIn("policy.kind === 'broken'", window)
        self.assertIn("policy.kind === 'file'", window)
        self.assertNotIn("legacy", window,
                         "the chip must be absent on a legacy board, not "
                         "present and empty")

    def test_a_broken_document_wears_the_alarm(self):
        self.assertRegex(self.html,
                         r"policy\.kind === 'broken'[\s\S]{0,200}cls: 'bad'")

    def test_the_chip_opens_the_profile_sheet(self):
        self.assertIn('data-profile="1"', self.html)
        self.assertRegex(self.html,
                         r"\[data-profile\][\s\S]{0,200}profileSheet\(task\)")

    def test_the_sheet_offers_each_intent(self):
        self.assertIn("INTENT_LABEL", self.html)
        for intent in profiles.INTENTS:
            self.assertIn(f"'{intent}'", self.html)
        self.assertIn("data-intent", self.html)

    def test_the_default_intent_follows_the_stage(self):
        self.assertIn("PROFILE_INTENT_FOR", self.html)
        self.assertRegex(self.html,
                         r"PROFILE_INTENT_FOR = \{ 'in-progress': 'work', "
                         r"review: 'act-pr' \}")


class NoContentsReachThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = BOARD.read_text(encoding="utf-8")

    def test_the_page_has_no_way_to_fetch_an_approved_documents_contents(self):
        """`/files/` serves plans/ and reference/ for the drawer, and that
        is a reading decision of its own. Nothing in the profile surface
        may reach for an approved input's bytes: approval is for the run,
        not for the page."""
        for name in ("permissionsPanel", "profileSheet", "profileDetail",
                     "inputRows"):
            start = self.html.index(f"function {name}(") if f"function {name}(" \
                in self.html else self.html.index(f"async function {name}(")
            body = self.html[start:start + 4000]
            self.assertNotIn("/files/", body,
                             f"{name} fetches an input's contents")

    def test_only_identities_and_hashes_are_drawn(self):
        self.assertIn("sha256 ${(i.sha256 || '').slice(0, 16)}", self.html)
        self.assertIn("i.source", self.html)

    def test_the_server_sends_no_contents_to_send(self):
        """The page's half is only safe because the server's allowlist has
        nothing to leak — asserted here too, because this is the pair."""
        self.assertNotIn("contents", profiles.PUBLIC)
        self.assertNotIn("contents", profiles.PUBLIC_INPUT)
        self.assertNotIn("content", profiles.PUBLIC_INPUT)


class TheStatePayloadNamesThePolicy(unittest.TestCase):
    def test_the_payload_is_cheap_and_names_the_document(self):
        payload = httpd.policy_payload()
        self.assertIn(payload["kind"], ("legacy", "file", "broken"))
        self.assertEqual(payload["intents"], list(profiles.INTENTS))
        self.assertIn("adapter", payload)
        # cheap means no per-input hashing on every state load
        self.assertNotIn("context", payload)
        self.assertNotIn("commands", payload)

    def test_the_payload_carries_the_adapters_own_declaration(self):
        adapter = httpd.policy_payload()["adapter"]
        for key in ("name", "identity", "version", "declared",
                    "capabilities", "unsupported"):
            self.assertIn(key, adapter)


if __name__ == "__main__":
    unittest.main()
