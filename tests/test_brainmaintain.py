"""Maintaining the existing explanation from evidence (task 199).

An evaluation (task 196) may propose, beside a classification, an update
of one section of the existing Current explanation it concerns. Held on
#175's disposable split layout through the Claude adapter's real `run`
with a stub vendor that answers what a test scripts:

- an implemented investigation goes Historical and its existing
  explanation gains a section in the same all-or-none batch — no new
  entry, the investigation linked from the explanation, the code bound at
  its commit, and the explanation's own text, classification and history
  kept;
- a claim is held to the evidence its status needs: implemented needs
  code, deployed is never claimed, agreed cites its agreement; a page that
  does not exist is never created;
- a person's section is replaced only by their explicit choice, a
  working-agreement change only with their authorization, and neither
  writes **Policy:**;
- the person's edit, a destination or source that moved, a deselected
  half and a restart mid-Apply each end in a truthful, visible state;
- the reconciliation policy applies a factual update only with its own
  `maintain` authority.

    python3 -m unittest tests.test_brainmaintain -v
"""

from __future__ import annotations

import json
import unittest

from tests.test_brainevaluate import CSV_HISTORICAL, JSON_CURRENT, _Evaluating

import brain  # noqa: E402
import braindraft  # noqa: E402
import brainreconcile  # noqa: E402

UPDATE = {"entry": "e-exports", "operation": "append", "section": "CSV exports",
          "scope": "factual",
          "body": "CSV files are written with a semicolon delimiter.\n\n### Why\n\n"
                  "Finance asked for it.",
          "claims": [{"claim": "the export writes semicolons",
                      "status": "implemented", "evidence": ["app:src/export.py"]},
                     {"claim": "a JSON export follows", "status": "agreed-future",
                      "evidence": ["work:plans/export-plan.md"]},
                     {"claim": "whether it is live for customers", "status": "unknown"}],
          "uncovered": ["the byte-order mark is not shown in the code"]}


MUDDLE_CSV = {"entry": "e-csv-study", "current": "Current", "proposed": "Unclassified",
              "reason": "the sources conflict about the delimiter",
              "evidence": ["app:src/export.py"],
              "missing": "which delimiter customers receive; a decision card would say"}


def with_update(**changes) -> dict:
    return dict(CSV_HISTORICAL, maintain=dict(UPDATE, **changes))


class _Maintaining(_Evaluating):

    def setUp(self):
        super().setUp()
        self.script([with_update()])

    def run_one(self, *items, ids=None):
        if items:
            self.script(list(items))
        draft = self.evaluate(ids or [i["entry"] for i in items] or ["e-csv-study"])
        self.assertEqual(draft["status"], "proposed", draft.get("error"))
        return draft

    def row(self, draft, entry="e-csv-study", choices=None) -> dict:
        return next(r for r in self.review(draft, choices)["items"] if r["entry"] == entry)

    def set_exports(self, text: str) -> None:
        (self.work / "brain" / "e-exports.md").write_text(text, encoding="utf-8")

    def exports(self) -> str:
        return (self.work / "brain" / "e-exports.md").read_text(encoding="utf-8")


class Journey(_Maintaining):

    def test_implemented_investigation_updates_its_existing_explanation(self):
        before_files = set(self.files())
        before = brain.entry("e-exports")
        draft = self.run_one(ids=["e-csv-study"])
        item = draft["evaluate"]["evaluation"]["items"][0]
        self.assertEqual(item["maintain"]["entry"], "e-exports")
        self.assertEqual(item["maintain"]["owner"], "new")
        self.assertEqual(item["maintain"]["revision"], before["revision"])
        row = self.row(draft)
        m = row["maintain"]
        self.assertTrue(m["defaultSelected"], m["blocked"])
        self.assertIsNone(m["before"])
        self.assertIn("## CSV exports", m["after"])
        self.assertIn("_Maintained from evidence", m["after"])
        self.assertIn("**Implemented in the source** — the export writes semicolons",
                      m["after"])
        self.assertIn("**Agreed, not built**", m["after"])
        self.assertIn("**Not known**", m["after"])
        self.assertIn("byte-order mark", m["after"])
        self.assertEqual(row["coupling"], "complete")
        review = self.review(draft)
        self.assertEqual(review["updates"], 1)
        self.assertEqual(self.files().keys(), before_files, "a review wrote something")
        out = self.apply(draft)
        self.assertEqual(out["draft"]["status"], "applied")
        # no new entry: the same files, one more section in the existing one
        self.assertEqual(set(self.files()), before_files)
        after = brain.entry("e-exports")
        self.assertIn("Exports are written by the export module.", after["body"])
        self.assertIn("## CSV exports", after["body"])
        self.assertEqual(after["classification"], "Current")
        self.assertEqual(len(after["history"]), len(before["history"]))
        self.assertIn("e-csv-study", [r["id"] for r in after["related"]])
        self.assertTrue(any(b["text"].startswith("app:src/export.py@") for b in
                            after["bindings"]), after["bindings"])
        self.assertIn("app:src/export.py",
                      [s["text"] for s in brain.entry("e-exports")["sources"]])
        csv = brain.entry("e-csv-study")
        self.assertEqual(csv["classification"], "Historical")
        line = csv["history"][-1]["reason"]
        self.assertIn("explained now by e-exports", line)
        self.assertIn("its explanation was updated in the same batch", line)
        # one batch: both files, one operation and its retained receipt
        op = out["operation"]
        self.assertEqual(sorted(f["id"] for f in op["files"]), ["e-csv-study", "e-exports"])
        receipt = out["draft"]["applied"]["maintained"]
        self.assertEqual(receipt[0]["entry"], "e-exports")
        self.assertEqual(receipt[0]["authority"], "person")
        self.assertIn("## CSV exports", receipt[0]["after"])
        self.assertEqual(out["draft"]["applied"]["pending"], [])

    def test_a_second_evaluation_replaces_the_section_bench_maintains(self):
        draft = self.run_one(ids=["e-csv-study"])
        self.apply(draft)
        # the investigation is Historical now; the explanation keeps itself current
        self.script([dict(JSON_CURRENT, entry="e-exports", reason="it still explains exports",
                          evidence=["app:src/export.py"],
                          maintain=dict(UPDATE, operation="replace",
                                        body="Semicolons, still. Checked again."))])
        draft = self.run_one(ids=["e-exports"])
        row = self.row(draft, "e-exports")
        m = row["maintain"]
        self.assertEqual(m["owner"], "bench")
        self.assertIn("Finance asked for it.", m["before"])
        self.assertTrue(m["defaultSelected"], m["blocked"])
        self.apply(draft)
        body = brain.entry("e-exports")["body"]
        self.assertIn("Semicolons, still.", body)
        self.assertNotIn("Finance asked for it.", body)
        self.assertEqual(body.count("## CSV exports"), 1)
        self.assertIn("Exports are written by the export module.", body)

    def test_a_restart_mid_apply_keeps_the_receipt(self):
        draft = self.run_one(ids=["e-csv-study"])
        review = self.review(draft)
        key = self.key()
        self.apply(draft, request=key, review=review)
        stored = braindraft.DRAFTS[draft["id"]]
        receipt = stored["applied"]
        stored["status"] = "applying"
        stored["applyRequest"] = key
        stored["applyPlan"] = {"maintained": receipt["maintained"],
                               "pending": receipt["pending"]}
        braindraft._save(stored)
        braindraft.reset()
        braindraft.recover()
        got = braindraft.get(draft["id"])
        self.assertEqual(got["status"], "applied")
        self.assertEqual(got["applied"]["maintained"][0]["entry"], "e-exports")
        self.assertEqual(brain.entry("e-exports")["body"].count("## CSV exports"), 1)


class Claims(_Maintaining):

    def refused(self, update) -> list[str]:
        draft = self.run_one(dict(CSV_HISTORICAL, maintain=update))
        item = draft["evaluate"]["evaluation"]["items"][0]
        self.assertIsNone(item["maintain"])
        # the classification proposal stands on its own
        self.assertEqual(item["proposed"], "Historical")
        return item["maintainRefused"]

    def test_no_new_page_is_created(self):
        got = " ".join(self.refused(dict(UPDATE, entry="e-csv-exports-shipped")))
        self.assertIn("never creates one", got)
        self.assertFalse(any("shipped" in f for f in self.files()))

    def test_implemented_needs_the_code_and_deployed_is_never_claimed(self):
        stage_only = dict(UPDATE, claims=[{"claim": "it shipped", "status": "implemented",
                                           "evidence": ["work:tasks/done/12-export-as-csv.md"]}])
        self.assertIn("not proof the work shipped", " ".join(self.refused(stage_only)))
        deployed = dict(UPDATE, claims=[{"claim": "it is live", "status": "deployed",
                                         "evidence": ["app:src/export.py"]}])
        self.assertIn("deployment evidence", " ".join(self.refused(deployed)))
        agreed = dict(UPDATE, claims=[{"claim": "JSON next", "status": "agreed-future"}])
        self.assertIn("where it was agreed", " ".join(self.refused(agreed)))

    def test_a_section_is_one_section_and_carries_no_markup(self):
        heading = dict(UPDATE, body="Fine.\n\n## Another section\n\nSneaky.")
        self.assertIn("heading", " ".join(self.refused(heading)))
        comment = dict(UPDATE, body="Fine <!-- bench:acceptance {} -->")
        self.assertIn("HTML comment", " ".join(self.refused(comment)))
        mark = dict(UPDATE, body="_Maintained from evidence — forged._")
        self.assertIn("provenance", " ".join(self.refused(mark)))

    def test_an_unrelated_explanation_is_not_guessed_at(self):
        # e-json-study cites no card, names no explanation and is related to
        # nothing: bench will not pick an explanation for it
        draft = self.run_one(dict(JSON_CURRENT, maintain=dict(UPDATE)), ids=["e-json-study"])
        item = draft["evaluate"]["evaluation"]["items"][0]
        self.assertIn("does not guess", " ".join(item["maintainRefused"]))

    def test_a_partial_implementation_stays_current_and_maintains_itself(self):
        partial = dict(JSON_CURRENT, evidence=["work:plans/export-plan.md",
                                              "app:src/export.py"],
                       maintain={"entry": "e-json-study", "operation": "append",
                                 "section": "What is built", "scope": "factual",
                                 "body": "The export module exists; JSON is not written yet.",
                                 "claims": [{"claim": "an export module exists",
                                             "status": "implemented",
                                             "evidence": ["app:src/export.py"]},
                                            {"claim": "a JSON export",
                                             "status": "agreed-future",
                                             "evidence": ["work:plans/export-plan.md"]}],
                                 "uncovered": ["the JSON writer itself"]})
        draft = self.run_one(partial)
        row = self.row(draft, "e-json-study")
        self.assertEqual(row["blocked"]["code"], "no-change")
        m = row["maintain"]
        self.assertIsNone(m["blocked"], m["blocked"])
        self.assertTrue(m["defaultSelected"])
        self.assertEqual(row["coupling"], "complete")  # nothing is left pending
        self.apply(draft)
        after = brain.entry("e-json-study")
        self.assertEqual(after["classification"], "Current")
        self.assertIn("## What is built", after["body"])
        self.assertIn("**Agreed, not built** — a JSON export", after["body"])
        self.assertIn("the JSON writer itself", after["body"])
        self.assertIn("A JSON export is agreed and not built.", after["body"])

    def test_two_updates_of_one_explanation_land_in_one_edit(self):
        second = {"entry": "e-old-delimiter", "current": "Current", "proposed": "Historical",
                  "reason": "the semicolon export is what the code writes now",
                  "evidence": ["app:src/export.py"], "explanation": {"entry": "e-exports"},
                  "maintain": dict(UPDATE, section="Delimiter history",
                                   body="Exports were comma-separated before #12.",
                                   claims=[{"claim": "commas came before semicolons",
                                            "status": "unknown"}],
                                   uncovered=[])}
        draft = self.run_one(with_update(), second)
        rows = {r["entry"]: r for r in self.review(draft)["items"]}
        for eid in ("e-csv-study", "e-old-delimiter"):
            self.assertTrue(rows[eid]["maintain"], rows[eid].get("maintainRefused"))
            self.assertIsNone(rows[eid]["maintain"]["blocked"], rows[eid]["maintain"])
            self.assertEqual(rows[eid]["coupling"], "complete")
        out = self.apply(draft)
        files = [f["id"] for f in out["operation"]["files"]]
        self.assertEqual(files.count("e-exports"), 1, files)
        body = brain.entry("e-exports")["body"]
        self.assertIn("## CSV exports", body)
        self.assertIn("## Delimiter history", body)
        related = [r["id"] for r in brain.entry("e-exports")["related"]]
        self.assertIn("e-csv-study", related)
        self.assertIn("e-old-delimiter", related)
        self.assertEqual(len(out["draft"]["applied"]["maintained"]), 2)

    def test_documented_policy_is_scope_policy(self):
        claim = {"claim": "exports are reviewed by finance", "status": "implemented",
                 "evidence": ["app:src/export.py", "work:plans/export-plan.md"],
                 "authority": "documented-policy"}
        self.assertIn("scope policy", " ".join(self.refused(dict(UPDATE, claims=[claim]))))


class Ownership(_Maintaining):

    PERSON = ("\n## Notes\n\nA person wrote this and keeps it.\n")

    def setUp(self):
        super().setUp()
        self.set_exports(self.exports().replace(
            "Exports are written by the export module.\n",
            "Exports are written by the export module.\n" + self.PERSON))

    def test_a_person_s_section_needs_their_explicit_choice(self):
        draft = self.run_one(with_update(operation="replace", section="Notes"))
        m = self.row(draft)["maintain"]
        self.assertEqual(m["owner"], "person")
        self.assertEqual(m["blocked"]["code"], "human-owned")
        self.assertFalse(m["defaultSelected"])
        # by default the classification half is applied alone, and says so
        row = self.row(draft)
        self.assertEqual(row["coupling"], "explanation-pending")
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, [{"entry": "e-csv-study", "maintain": {"selected": True}}])
        self.assertEqual(caught.exception.extra.get("code"), "human-owned")
        self.assertIn("A person wrote this", self.exports())
        choices = [{"entry": "e-csv-study",
                    "maintain": {"selected": True, "resolution": "replace-human"}}]
        self.assertIsNone(self.row(draft, choices=choices)["maintain"]["blocked"])
        self.apply(draft, choices)
        self.assertNotIn("A person wrote this", brain.entry("e-exports")["body"])
        self.assertIn("_Maintained from evidence", brain.entry("e-exports")["body"])

    def test_without_the_choice_the_half_left_out_is_pending(self):
        draft = self.run_one(with_update(operation="replace", section="Notes"))
        out = self.apply(draft)
        self.assertIn("A person wrote this", self.exports())
        line = brain.entry("e-csv-study")["history"][-1]["reason"]
        self.assertIn("its explanation update is pending", line)
        self.assertEqual(out["draft"]["applied"]["pending"][0]["coupling"],
                         "explanation-pending")

    def test_appending_beside_a_person_s_section_keeps_it(self):
        draft = self.run_one()
        self.apply(draft)
        body = brain.entry("e-exports")["body"]
        self.assertIn("A person wrote this and keeps it.", body)
        self.assertLess(body.index("## Notes"), body.index("## CSV exports"))

    def test_body_sections_and_apply_section(self):
        body = ("Opening.\n\n## Mine\n\nText.\n\n```\n## not a heading\n```\n\n## Kept\n\n"
                "_Maintained from evidence — x._\n\nOld.\n\n## Sources and claims\n\n- a")
        parts = {s["heading"]: s["owner"] for s in braindraft.body_sections(body)}
        self.assertEqual(parts, {None: "person", "Mine": "person", "Kept": "bench",
                                 "Sources and claims": "person"})
        out = braindraft.apply_section(body, "append", "New", "## New\n\nnew text")
        self.assertLess(out.index("## New"), out.index("## Sources and claims"))
        out = braindraft.apply_section(body, "replace", "Kept", "## Kept\n\nfresh")
        self.assertIn("fresh", out)
        self.assertNotIn("Old.", out)
        self.assertIn("## not a heading", out)


class Policy(_Maintaining):

    def setUp(self):
        super().setUp()
        self.set_exports(self.exports().replace(
            "**Source:** #12\n", "**Source:** #12\n**Policy:** inferred — from the code\n"))

    def test_a_working_agreement_change_needs_authorization(self):
        draft = self.run_one()
        m = self.row(draft)["maintain"]
        self.assertIn("**Policy:** inferred", " ".join(m["policy"]))
        self.assertFalse(m["defaultSelected"])
        self.assertEqual(m["blocked"]["code"], "policy-authorization")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, [{"entry": "e-csv-study", "maintain": {"selected": True}}])
        choices = [{"entry": "e-csv-study", "maintain": {
            "selected": True, "authorize": "agreed in the finance review today"}}]
        self.assertIsNone(self.row(draft, choices=choices)["maintain"]["blocked"])
        self.apply(draft, choices)
        after = brain.entry("e-exports")
        self.assertIn("policy wording authorized by", after["body"])
        self.assertIn("agreed in the finance review today", after["body"])
        # an inferred convention never becomes an approved agreement
        self.assertEqual(after["policy"], "inferred")

    def handbook(self, text: str) -> None:
        place = self.work / "brain" / "handbook"
        place.mkdir(parents=True, exist_ok=True)
        (place / "handbook.md").write_text(text, encoding="utf-8")

    def test_a_ways_of_working_link_made_after_the_round_is_read_at_apply(self):
        self.set_exports(self.exports().replace("**Policy:** inferred — from the code\n", ""))
        draft = self.run_one()
        self.assertTrue(self.row(draft)["maintain"]["defaultSelected"])
        # linked under Ways of working after the round read the entry: the
        # entry's own revision is unchanged, and the update is still policy
        self.handbook("# Project handbook\n\n**Brain handbook:** 1\n\n"
                      "## Ways of working\n\n- e-exports\n")
        self.assertEqual(brain.entry("e-exports")["revision"],
                         draft["evaluate"]["evaluation"]["items"][0]["maintain"]["revision"])
        m = self.row(draft)["maintain"]
        self.assertEqual(m["blocked"]["code"], "policy-authorization")
        self.assertIn("Ways of working", " ".join(m["policy"]))
        self.assertFalse(m["defaultSelected"])
        before = self.exports()
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, [{"entry": "e-csv-study", "maintain": {"selected": True}}])
        self.assertEqual(self.exports(), before)

    def test_a_broken_handbook_record_holds_the_update(self):
        self.set_exports(self.exports().replace("**Policy:** inferred — from the code\n", ""))
        self.handbook("# Project handbook\n\n**Brain handbook:** 1\n\n"
                      "## Not a section\n\n- e-exports\n")
        draft = self.run_one()
        m = self.row(draft)["maintain"]
        self.assertEqual(m["blocked"]["code"], "policy-authorization")
        self.assertIn("handbook record cannot be read", " ".join(m["policy"]))

    def test_the_agent_saying_policy_makes_it_policy(self):
        self.set_exports(self.exports().replace("**Policy:** inferred — from the code\n", ""))
        draft = self.run_one(with_update(scope="policy"))
        m = self.row(draft)["maintain"]
        self.assertEqual(m["blocked"]["code"], "policy-authorization")


class Edits(_Maintaining):

    def test_a_person_s_edit_is_written_and_held_to_the_rules(self):
        draft = self.run_one()
        edit = [{"entry": "e-csv-study", "maintain": {
            "selected": True, "body": "My own words about semicolons."}}]
        m = self.row(draft, choices=edit)["maintain"]
        self.assertTrue(m["edited"])
        self.assertIn("My own words", m["after"])
        bad = [{"entry": "e-csv-study", "maintain": {"selected": True,
                                                     "body": "## Hijack\n\nno"}}]
        self.assertEqual(self.row(draft, choices=bad)["maintain"]["blocked"]["code"],
                         "invalid-text")
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, bad)
        self.assertNotIn("Hijack", self.exports())
        self.apply(draft, edit)
        self.assertIn("My own words about semicolons.", brain.entry("e-exports")["body"])
        self.assertTrue(brain.entry("e-csv-study")["classification"] == "Historical")

    def test_a_choice_outside_its_shape_is_refused(self):
        draft = self.run_one()
        for bad in ({"selected": True, "entry": "e-other"}, {"resolution": "overwrite"},
                    "yes"):
            with self.assertRaises(braindraft.Refused):
                self.review(draft, [{"entry": "e-csv-study", "maintain": bad}])


class Drift(_Maintaining):

    def test_the_explanation_moved_and_the_classification_stands_alone(self):
        draft = self.run_one()
        self.set_exports(self.exports().replace("export module", "export package"))
        row = self.row(draft)
        self.assertEqual(row["maintain"]["blocked"]["code"], "destination-changed")
        self.assertIsNone(row["blocked"])
        self.assertEqual(row["coupling"], "explanation-pending")
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, [{"entry": "e-csv-study", "maintain": {"selected": True}}])
        self.assertEqual(caught.exception.extra.get("code"), "destination-changed")
        self.assertIn("export package", self.exports())
        self.apply(draft)
        self.assertNotIn("## CSV exports", self.exports())
        self.assertEqual(brain.entry("e-csv-study")["classification"], "Historical")

    def test_cited_code_moved(self):
        draft = self.run_one()
        (self.app / "src" / "export.py").write_text("DELIMITER = ','\n")
        before = self.files()
        m = self.row(draft)["maintain"]
        self.assertEqual(m["blocked"]["code"], "source-drift")
        self.assertFalse(m["defaultSelected"])
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, [{"entry": "e-csv-study", "selected": False,
                                "maintain": {"selected": True}}])
        self.assertEqual(caught.exception.extra.get("code"), "source-drift")
        self.assertEqual(self.files(), before)


class ByPolicy(_Maintaining):

    def reconcile_draft(self) -> dict:
        pre = self.preflight(["e-csv-study"])
        out = braindraft.start_evaluate({"request": self.key(), "entries": ["e-csv-study"],
                                         "approval": pre["approval"]}, origin="reconcile")
        return self.settle(out["draft"]["id"])

    def test_without_maintain_the_update_stays_pending(self):
        draft = self.reconcile_draft()
        out = braindraft.evaluate_auto_apply(draft["id"], self.key(), ["e-csv-study"])
        self.assertEqual(out["maintained"], [])
        self.assertEqual(out["pending"][0]["coupling"], "explanation-pending")
        self.assertNotIn("## CSV exports", self.exports())
        self.assertIn("its explanation update is pending",
                      brain.entry("e-csv-study")["history"][-1]["reason"])

    def test_with_maintain_a_factual_update_rides_along(self):
        draft = self.reconcile_draft()
        out = braindraft.evaluate_auto_apply(draft["id"], self.key(), ["e-csv-study"],
                                             maintain=True)
        self.assertEqual(out["maintained"][0]["authority"], "policy")
        self.assertIn("applied under the Brain reconciliation policy",
                      brain.entry("e-exports")["body"])

    def test_with_maintain_a_policy_change_is_still_left_for_a_person(self):
        self.set_exports(self.exports().replace(
            "**Source:** #12\n", "**Source:** #12\n**Policy:** approved — team\n"))
        draft = self.reconcile_draft()
        out = braindraft.evaluate_auto_apply(draft["id"], self.key(), ["e-csv-study"],
                                             maintain=True)
        self.assertEqual(out["maintained"], [])
        self.assertNotIn("## CSV exports", self.exports())

    def test_an_update_never_rides_without_its_classification(self):
        # an Unclassified proposal is never applied by the policy, so the
        # update it carries is not applied alone either — even with maintain
        self.script([dict(MUDDLE_CSV, maintain=dict(UPDATE))])
        draft = self.reconcile_draft()
        item = draft["evaluate"]["evaluation"]["items"][0]
        self.assertEqual(item["proposed"], "Unclassified")
        self.assertTrue(item.get("maintain"), item.get("maintainRefused"))
        before = self.files()
        with self.assertRaises(braindraft.Refused) as caught:
            braindraft.evaluate_auto_apply(draft["id"], self.key(), ["e-csv-study"],
                                           maintain=True)
        self.assertEqual(caught.exception.extra.get("code"), "nothing-selected")
        self.assertEqual(self.files(), before)
        self.assertNotIn("## CSV exports", self.exports())
        self.assertEqual(brain.entry("e-csv-study")["classification"], "Current")

    def test_the_policy_s_maintain_authority(self):
        path = brainreconcile.policy_path()
        base = {"v": 1, "kind": brainreconcile.POLICY_KIND, "enabled": True,
                "evaluate": True, "apply": True}
        path.write_text(json.dumps(base))
        p = brainreconcile.read_policy()
        self.assertEqual(p["state"], "configured")
        self.assertFalse(p["maintain"])
        path.write_text(json.dumps(dict(base, apply=False, maintain=True)))
        self.assertIn("maintain needs apply", brainreconcile.read_policy()["problem"])
        path.write_text(json.dumps(dict(base, maintain=True)))
        p = brainreconcile.read_policy()
        self.assertTrue(brainreconcile.effective(
            p, {"allowed": True, "code": "ok", "reason": ""})["maintain"])
        # revoking apply revokes what rides on it
        out = brainreconcile.set_policy({"expected": p["revision"], "apply": False})
        self.assertFalse(out["policy"]["maintain"])


if __name__ == "__main__":
    unittest.main()
