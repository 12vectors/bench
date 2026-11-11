"""Card 94's evidence-to-merge regressions, kept once each is fixed.

`tests/repro_94_evidence_to_merge.py` pins the behaviour the #94 design
changes; as each implementation card lands, its case moves here with the
assertion inverted (design §15.3). Every case runs against a disposable
tool install, work root and application checkout (`GateCase`), never a
live board.

Card 95 (design §15.1 step 1) — an override needs a real target:

- E11 / R5 — no implementation commit, or one application git cannot
  read: the override is refused, nothing is persisted, and a direct HTTP
  call cannot get past it.
- `overrides.eligible` is the backend's answer, and the sheet draws it.
- E12 — an override at H ceases to apply at a new head, or when what the
  scope requires changes; a stale page cannot record one for another head.
- E13 — the merge still revalidates against the exact commit under an
  override; `--match-head-commit` stays covered in
  `tests/test_gate_remediation.py`.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.test_integration_gates import (CARD, FAILS, STEM, GateCase,  # noqa: E402
                                          card, git)

import gates  # noqa: E402  (on sys.path once the case module is imported)
import github  # noqa: E402
import httpd  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

REASON = "the check runner is down, demo at 5"
NO_TARGET = ("there is no implementation commit to override at, so nothing "
             "was recorded")


class OverrideCase(GateCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "istos"

    def nothing_written(self) -> None:
        self.assertEqual(gates.overrides_for(CARD), [])
        self.assertEqual(list((self.state / "gates").glob("*.overrides.json"))
                         if (self.state / "gates").is_dir() else [], [])


# ── E11 / R5: no target, no record ─────────────────────────────────────


class AnOverrideNeedsATarget(OverrideCase):

    def test_no_branch_is_refused_and_nothing_is_written(self):
        """R5 inverted: the card has no implementation commit at all."""
        self.gated(overrides={"allow": True})
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertIsNone(found["head"]["commit"])
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_an_unreadable_target_is_refused_too(self):
        """A branch application git cannot answer for is not a target: the
        tips read comes back empty, as it does for a broken repository."""
        self.worktree()
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        self.addCleanup(setattr, gates, "_tips", gates._tips)
        gates._tips = lambda: {}
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_a_stale_head_against_no_target_is_still_no_target(self):
        """A page that saw a commit, then the branch went away: the refusal
        is the specific one, not "the head moved to nothing"."""
        self.gated(overrides={"allow": True})
        with self.assertRaises(ValueError) as caught:
            gates.override(CARD, "acceptance", actor="istos", reason=REASON,
                           head="0" * 40)
        self.assertIn(NO_TARGET, str(caught.exception))
        self.nothing_written()

    def test_the_reading_says_it_is_not_eligible_and_why(self):
        self.gated(overrides={"allow": True})
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "no-target")
        self.assertIn("no implementation commit", over["ineligible"]["why"])

    def test_the_sheet_payload_carries_the_backends_answer(self):
        self.gated(overrides={"allow": True})
        scope = httpd.gates_payload(CARD, "in-progress")["scopes"][0]
        self.assertFalse(scope["overrides"]["eligible"])
        self.assertEqual(scope["overrides"]["ineligible"]["code"], "no-target")

    def test_a_valid_target_is_eligible_and_records(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        payload = httpd.gates_payload(CARD, "in-progress")
        self.assertTrue(payload["scopes"][0]["overrides"]["eligible"])
        self.assertIsNone(payload["scopes"][0]["overrides"]["ineligible"])
        record = gates.override(CARD, "acceptance", actor="istos",
                                reason=REASON)["override"]
        self.assertTrue(record["head"])
        self.assertTrue(record["scopePolicy"])

    def test_eligibility_names_the_actor_the_policy_refuses(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True, "actors": ["elena"]})
        # actor-agnostic on the board's reading …
        self.assertTrue(self.decide()["overrides"]["eligible"])
        # … and specific to this board's identity on the sheet
        scope = httpd.gates_payload(CARD, "in-progress")["scopes"][0]
        self.assertFalse(scope["overrides"]["eligible"])
        self.assertEqual(scope["overrides"]["ineligible"]["code"],
                         "actor-not-named")

    def test_a_board_without_overrides_says_so(self):
        self.commit_in(self.worktree())
        self.gated()
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "not-allowed")

    def test_an_override_in_force_is_not_offered_again(self):
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        over = self.decide()["overrides"]
        self.assertFalse(over["eligible"])
        self.assertEqual(over["ineligible"]["code"], "in-force")


class TheRouteCannotBeBypassed(OverrideCase):
    """`/api/gates/override` on a real socket: the path a stale page, or a
    script, takes."""

    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.url + "/api/gates/override",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_no_target_is_refused_over_http(self):
        self.gated(overrides={"allow": True})
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": "f" * 40})
        self.assertEqual(status, 409)
        self.assertIn(NO_TARGET, body["error"])
        self.nothing_written()

    def test_a_request_naming_no_head_is_refused(self):
        """Without a head a direct call would authorise whatever the branch
        holds when it lands — the stale-page case with the check removed."""
        self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        for head in (None, "", "   "):
            with self.subTest(head=head):
                payload = {"file": CARD, "scope": "acceptance",
                           "reason": REASON}
                if head is not None:
                    payload["head"] = head
                status, body = self.post(payload)
                self.assertEqual(status, 400)
                self.assertIn("nothing was recorded", body["error"])
        self.nothing_written()

    def test_a_stale_page_cannot_record_for_another_head(self):
        wt = self.worktree()
        seen = self.commit_in(wt)
        self.gated(overrides={"allow": True})
        self.commit_in(wt, "pushed while the sheet was open\n")
        gates.forget_cached()
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": seen})
        self.assertEqual(status, 409)
        self.assertIn("the head moved while you were looking", body["error"])
        self.nothing_written()

    def test_an_explicit_valid_override_is_recorded(self):
        head = self.commit_in(self.worktree())
        self.gated(overrides={"allow": True})
        status, body = self.post({"file": CARD, "scope": "acceptance",
                                  "reason": REASON, "head": head})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["override"]["head"], head)
        self.assertEqual(body["override"]["actor"], "istos")
        self.assertEqual(body["gate"]["decision"], "overridden")


# ── E12: an override authorises one commit, under one rule ─────────────


class AnOverrideIsBoundToItsHeadAndRule(OverrideCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)

    def test_a_new_head_is_not_covered(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        self.commit_in(self.wt, "a new head\n")
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertTrue(found["overrides"]["eligible"],
                        "the new head is a new question, and may be asked")

    def test_a_change_to_what_the_scope_requires_withdraws_it(self):
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        # the required check is redefined: the rule the person proceeded
        # over is not the rule in force now
        document["checks"][0]["run"] = str(self.executable("unit-v2", FAILS))
        self.declares(document)
        found = self.decide()
        self.assertEqual(found["decision"], "blocked")
        self.assertIsNone(found["overrides"]["applied"])

    def test_restricting_who_may_override_withdraws_it(self):
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        document["gates"]["overrides"] = {"allow": True, "actors": ["elena"]}
        self.declares(document)
        self.assertEqual(self.decide()["decision"], "blocked")

    def test_an_unrelated_policy_edit_does_not(self):
        """Only what this scope requires is bound: another scope's rule or
        an approved context file is not a decision about this gate."""
        document = self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        (self.work / "reference" / "mock.md").write_text("x\n", encoding="utf-8")
        document["context"] = [{"name": "mock", "path": "work:reference/mock.md",
                                "required": False}]
        document["gates"]["member"] = {"checks": ["unit"]}
        self.declares(document)
        self.assertEqual(self.decide()["decision"], "overridden")

    def test_an_existing_record_keeps_its_meaning(self):
        """A record written before the scope binding existed has no
        `scopePolicy`, and is read exactly as it was: bound to its head and
        its gates."""
        self.gated(overrides={"allow": True})
        legacy = {"v": 1, "id": "ovr:legacy00001", "task": CARD,
                  "scope": "acceptance", "actor": "istos", "reason": REASON,
                  "head": self.head, "headRef": f"task/{STEM}",
                  "gates": ["unit"], "unmet": [], "policyId": "gates:old",
                  "policyRevision": "old", "recorded": 1.0}
        path = gates._path(CARD, "overrides")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([legacy]), encoding="utf-8")
        gates.forget_cached()
        found = self.decide()
        self.assertEqual(found["decision"], "overridden")
        self.assertEqual(found["overrides"]["applied"]["id"], "ovr:legacy00001")

    def test_an_override_forges_nothing(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        found = self.decide()
        self.assertEqual(found["requirements"][0]["state"], "overridden")
        self.assertEqual(found["requirements"][0]["reason"], "not-run")
        self.assertEqual(verify.for_task(CARD), [])
        self.assertNotEqual(found["decision"], "allowed")


# ── E13: the merge revalidates against the exact commit ────────────────


class TheMergeRevalidatesUnderAnOverride(OverrideCase):

    def setUp(self):
        super().setUp()
        self.wt = self.worktree()
        self.head = self.commit_in(self.wt)
        (self.tasks / "review" / CARD).write_text(
            card("71 — Gate integration", status="Review"), encoding="utf-8")
        (self.tasks / "in-progress" / CARD).unlink()

    def test_an_override_at_h_lets_h_merge(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["merged"])
        self.assertEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", self.head,
                   "main").returncode)

    def test_a_push_after_the_override_is_refused_at_the_merge(self):
        self.gated(overrides={"allow": True})
        gates.override(CARD, "acceptance", actor="istos", reason=REASON)
        self.assertEqual(self.decide()["decision"], "overridden")
        landed: list[str] = []
        original = github._assert_lands_in_the_base

        def push_lands(filename, target):
            original(filename, target)
            if not landed:
                landed.append(self.commit_in(self.wt, "a late push\n"))
                verify.forget_cached()
                gates.forget_cached()

        self.addCleanup(setattr, github, "_assert_lands_in_the_base", original)
        github._assert_lands_in_the_base = push_lands
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertNotEqual(
            0, git(self.repo, "merge-base", "--is-ancestor", landed[0],
                   "main").returncode,
            "the commit nobody overrode must not reach the base")
        self.assertEqual(self.stage_of(CARD), "review")


if __name__ == "__main__":
    unittest.main()
