"""Seeding the Brain, step three: review and an all-or-none Apply (task 194).

Held on #193's disposable split repositories and stub vendor: a real
inspection, a real generated draft, then

- **Review is a read.** Every section's target (`s-<section>`), action,
  text before and after, citations and strength, the handbook before and
  after, coverage and publication risk — and nothing written.
- **Apply writes only what was selected**, edited and checked, as one
  batch: the entries and the handbook's links, one scoped commit, no
  application file, card or launch.
- **None or all.** A crash part way is finished (or put back) by
  `recover()`; an unexpected failure is put back at once; a reader during
  the switch sees none or all; a double click is one outcome.
- **Drift and concurrency refuse.** A moved source, a changed policy, a
  changed Brain between review and Apply, and another writer between plan
  and switch each refuse with nothing written.
- **Re-seeding** previews against what is there now and keeps a person's
  classification and its reasons.
- **Receipts.** A failed commit is retried alone; a push reads pending,
  then shared once sync publishes it; a remote conflict is compared, never
  forced.

    python3 -m unittest tests.test_seedapply -v
"""

from __future__ import annotations

import subprocess
import threading
import time
import unittest
from unittest import mock

from tests.test_brainwrite import git, tree  # noqa: F401
from tests.test_seeddraft import _Seeding, README

import brain  # noqa: E402
import braindraft  # noqa: E402
import brainwrite  # noqa: E402
import handbook  # noqa: E402
import profiles  # noqa: E402
import taskfiles  # noqa: E402

SECTIONS = ("overview", "how-it-works", "ways-of-working", "direction", "operations")


class _Crash(BaseException):
    """What a process dying looks like to the code it dies in."""


class _Applying(_Seeding):
    commit_moves = True

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, brainwrite, "_FAULT", None)

    def tearDown(self):
        # the application and the cards never change; brain/ may
        self.assertEqual(tree(self.app), self.app_before, "the application changed")
        self.assertEqual(tree(self.work / "tasks"), self.cards_before, "a card changed")
        self.assertFalse(any(p.name.startswith((".bench-stage-", ".bench-was-"))
                             for p in self.work.rglob("*")), "a staged file was left behind")

    def review(self, draft: dict, sections=None) -> dict:
        return braindraft.seed_review({"id": draft["id"], "sections": sections})

    def apply(self, draft: dict, sections=None, request: str | None = None,
              review: dict | None = None) -> dict:
        review = review or self.review(draft, sections)
        return braindraft.seed_apply({"id": draft["id"], "request": request or self.req(),
                                      "round": draft["proposalRound"],
                                      "preview": review["preview"], "sections": sections})

    def ids(self) -> set[str]:
        return {e["id"] for e in brain.reading()["entries"]}

    def commits(self) -> int:
        return int(git(self.work, "rev-list", "--count", "HEAD").stdout.strip())


class Review(_Applying):

    def test_review_is_a_read_and_says_everything(self):
        draft = self.generate()
        before = tree(self.work)
        found = self.review(draft)
        self.assertEqual(tree(self.work), before)
        self.assertFalse((self.work / "brain").exists())
        rows = {r["section"]: r for r in found["sections"]}
        self.assertEqual(list(rows), list(SECTIONS))
        self.assertEqual(rows["overview"]["target"], "s-overview")
        self.assertEqual(rows["overview"]["action"], "create")
        self.assertTrue(rows["overview"]["defaultSelected"])
        self.assertIsNone(rows["overview"]["before"])
        self.assertIn("Orchard tracks fruit trees", rows["overview"]["after"])
        self.assertIn("## Sources and claims", rows["overview"]["after"])
        self.assertEqual(rows["overview"]["strength"]["word"], "sourced")
        self.assertEqual({c["ref"] for c in rows["overview"]["citations"]},
                         {"app:README.md", "app:server.py"})
        # a gap-only section is offered, not selected, and leaves coverage missing
        self.assertFalse(rows["operations"]["defaultSelected"])
        self.assertIn("operations", found["coverage"]["missing"])
        self.assertIsNone(found["handbook"]["before"])
        self.assertIn("- s-overview", found["handbook"]["after"])
        self.assertTrue(found["ready"], found["blockers"])
        self.assertIn("no application file", found["publication"]["never"])

    def test_a_declined_section_writes_nothing_and_stays_missing(self):
        draft = self.generate("no-gap")     # operations refused: it must name its gap
        found = self.review(draft)
        ops = next(r for r in found["sections"] if r["section"] == "operations")
        self.assertEqual(ops["action"], "declined")
        self.apply(draft)
        self.assertNotIn("s-operations", self.ids())
        self.assertEqual(handbook.read_record()["sections"]["operations"], [])


class Apply(_Applying):

    def test_only_the_selected_edited_sections_and_the_handbook_are_written(self):
        draft = self.generate()
        commits = self.commits()
        choices = [{"section": "overview", "selected": True, "title": "Orchard, briefly",
                    "body": "Orchard keeps a list of a farm's fruit trees."},
                   {"section": "ways-of-working", "selected": False}]
        out = self.apply(draft, choices)
        self.assertEqual(out["draft"]["status"], "applied")
        self.assertEqual(self.ids(), {"s-overview", "s-how-it-works", "s-direction"})
        entry = brain.entry("s-overview")
        self.assertEqual(entry["title"], "Orchard, briefly")
        self.assertEqual(entry["classification"], "Unclassified")
        self.assertIn("Orchard keeps a list", entry["body"])
        self.assertIn("app:README.md", entry["body"])
        self.assertTrue(any(b["text"].startswith("app:README.md@")
                            for b in entry["bindings"]), entry["bindings"])
        rec = handbook.read_record()
        self.assertEqual(rec["sections"]["overview"], ["s-overview"])
        self.assertEqual(rec["sections"]["ways-of-working"], [])
        # one commit, touching only brain/
        self.assertEqual(self.commits(), commits + 1)
        names = git(self.work, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertTrue(names and all(n.startswith("brain/") for n in names), names)
        self.assertEqual(len(names), 4)
        self.assertEqual(out["operation"]["commit"]["state"], "committed")
        self.assertEqual(self.calls(), 1, "applying launched something")

    def test_a_double_click_is_one_outcome(self):
        draft = self.generate()
        review = self.review(draft)
        first = self.apply(draft, request="seed-apply-twice-1", review=review)
        commits = self.commits()
        again = self.apply(draft, request="seed-apply-twice-1", review=review)
        self.assertTrue(again["already"])
        self.assertEqual(self.commits(), commits)
        self.assertEqual(first["operation"]["files"], again["operation"]["files"])
        with self.assertRaises(braindraft.Refused):
            self.apply(draft, request="seed-apply-twice-2", review=review)

    def test_a_lost_reply_is_settled_from_the_journal(self):
        draft = self.generate()
        with mock.patch.object(braindraft, "_seed_applied", side_effect=_Crash):
            with self.assertRaises(_Crash):
                self.apply(draft, request="seed-lost-reply-1")
        self.assertEqual(braindraft.DRAFTS[draft["id"]]["status"], "applying")
        braindraft.recover()
        self.assertEqual(braindraft.get(draft["id"])["status"], "applied")
        again = self.apply(draft, request="seed-lost-reply-1",
                           review={"preview": "whatever"})
        self.assertTrue(again["already"])

    def test_a_gap_only_section_can_be_applied_and_says_what_is_missing(self):
        draft = self.generate()
        self.apply(draft, [{"section": "operations", "selected": True}])
        body = brain.entry("s-operations")["body"]
        self.assertIn("### Not covered yet", body)
        self.assertIn("how Orchard is deployed", body)

    def test_a_source_dump_and_a_credential_are_refused(self):
        draft = self.generate()
        with mock.patch.object(braindraft, "VERBATIM_LIMIT", 20):
            found = self.review(draft, [{"section": "overview", "selected": True,
                                         "body": README}])
            self.assertIn("source-dump", {b["code"] for b in found["blockers"]})
            with self.assertRaises(braindraft.Refused) as caught:
                self.apply(draft, [{"section": "overview", "selected": True,
                                    "body": README}])
        self.assertEqual(caught.exception.extra["code"], "source-dump")
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, [{"section": "overview", "selected": True,
                                "body": f"The token is {secret}"}])
        self.assertEqual(caught.exception.extra["code"], "credential")
        self.assertFalse((self.work / "brain").exists())


class NoneOrAll(_Applying):

    def test_a_crash_part_way_is_finished_at_start_up(self):
        draft = self.generate()

        def crash(phase):
            if phase == "switched:1":
                raise _Crash()
        brainwrite._FAULT = crash
        with self.assertRaises(_Crash):
            self.apply(draft, request="seed-crash-1")
        brainwrite._FAULT = None
        braindraft.recover()
        self.assertEqual(self.ids(), {"s-overview", "s-how-it-works", "s-ways-of-working",
                                      "s-direction"})
        self.assertEqual(len(handbook.read_record()["sections"]["overview"]), 1)
        self.assertEqual(braindraft.get(draft["id"])["status"], "applied")

    def test_a_crash_while_staging_is_finished_by_the_same_request(self):
        draft = self.generate()

        def crash(phase):
            if phase == "staged:2":
                raise _Crash()
        brainwrite._FAULT = crash
        review = self.review(draft)
        with self.assertRaises(_Crash):
            self.apply(draft, request="seed-crash-stage-1", review=review)
        brainwrite._FAULT = None
        self.assertFalse((self.work / "brain" / "s-overview.md").exists())
        out = self.apply(draft, request="seed-crash-stage-1", review=review)
        self.assertEqual(out["draft"]["status"], "applied")
        self.assertIn("s-direction", self.ids())

    def test_an_unexpected_failure_puts_everything_back(self):
        draft = self.generate()

        def fail(phase):
            if phase == "switched:2":
                raise RuntimeError("disk trouble")
        brainwrite._FAULT = fail
        commits = self.commits()
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft)
        self.assertEqual(caught.exception.extra["code"], "interrupted")
        self.assertEqual(self.ids(), set())
        self.assertEqual(handbook.read_record()["state"], "missing")
        self.assertEqual(self.commits(), commits)
        self.assertEqual(braindraft.get(draft["id"])["status"], "proposed")

    def test_a_reader_during_the_switch_sees_none_or_all(self):
        draft = self.generate()
        started = threading.Event()
        seen = []

        def slow(phase):
            if phase == "switched:0":
                started.set()
                time.sleep(0.4)
        brainwrite._FAULT = slow

        def read():
            started.wait(10)
            seen.append(len(self.ids()))
        reader = threading.Thread(target=read)
        reader.start()
        self.apply(draft)
        reader.join(10)
        self.assertEqual(seen, [4])

    def test_another_writer_between_plan_and_switch_is_never_overwritten(self):
        draft = self.generate()
        self.apply(draft, [{"section": "direction", "selected": False}])
        # a second seed updates s-overview; a person edits it meanwhile
        self.manifest = self.inspect()
        second = self.generate()
        path = self.work / "brain" / "s-overview.md"

        def edit(phase):
            if phase == "switching":
                path.write_text(path.read_text().replace("Orchard", "Orchards"))
        brainwrite._FAULT = edit
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(second, [{"section": "overview", "selected": True}])
        brainwrite._FAULT = None
        self.assertEqual(caught.exception.extra["code"], "conflict")
        self.assertIn("Orchards", path.read_text())
        self.assertNotIn("s-direction", self.ids())


class Drift(_Applying):

    def test_a_moved_source_refuses_and_invalidates_its_sections(self):
        draft = self.generate()
        review = self.review(draft)
        (self.app / "README.md").write_text(README + "\nChanged.\n")
        self.app_before = tree(self.app)
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, review=review)
        self.assertEqual(caught.exception.extra["code"], "source-drift")
        self.assertFalse((self.work / "brain").exists())
        after = braindraft.get(draft["id"])
        self.assertEqual(after["conflict"]["code"], "source-drift")
        self.assertIn("overview", after["seed"]["draft"]["invalidated"])
        # deselecting the affected sections is the person's way on
        rows = self.review(draft)["sections"]
        self.assertFalse(next(r for r in rows if r["section"] == "overview")
                         ["defaultSelected"])

    def test_a_changed_policy_refuses(self):
        draft = self.generate()
        real = profiles.resolve

        def other(intent, *a, **k):
            found = real(intent, *a, **k)
            return dict(found, policyId="another-policy")
        with mock.patch.object(profiles, "resolve", other):
            with self.assertRaises(braindraft.Refused) as caught:
                self.apply(draft)
        self.assertEqual(caught.exception.extra["code"], "policy-drift")
        self.assertFalse((self.work / "brain").exists())

    def test_a_brain_change_after_review_is_a_stale_preview(self):
        draft = self.generate()
        review = self.review(draft)
        brainwrite.write("create", {"request": "human-entry-1", "title": "By hand",
                                    "kind": "Guide", "body": "x"})
        with self.assertRaises(braindraft.Refused) as caught:
            self.apply(draft, review=review)
        self.assertEqual(caught.exception.extra["code"], "preview-stale")
        self.assertNotIn("s-overview", self.ids())


class Reseed(_Applying):

    def test_reseeding_previews_current_prose_and_keeps_human_decisions(self):
        draft = self.generate()
        self.apply(draft)
        found = brain.entry("s-overview")
        brainwrite.write("classify", {"request": "human-classify-1", "id": "s-overview",
                                      "expected": found["revision"],
                                      "classification": "Current",
                                      "reason": "checked against the code by ronald"})
        found = brain.entry("s-overview")
        brainwrite.write("edit", {"request": "human-edit-1", "id": "s-overview",
                                  "expected": found["revision"], "title": found["title"],
                                  "kind": "Guide", "summary": found["summary"],
                                  "body": found["body"] + "\n\nA person's note."})
        self.manifest = self.inspect()      # re-seeding starts from a new inspection
        second = self.generate()
        rows = {r["section"]: r for r in self.review(second)["sections"]}
        row = rows["overview"]
        self.assertEqual(row["action"], "update")
        self.assertTrue(row["humanEdited"])
        self.assertFalse(row["defaultSelected"], "a person's edit is not replaced by default")
        self.assertIn("A person's note.", row["before"])
        self.assertTrue(row["risks"])
        # an untouched seed entry is offered as an update and stays selected
        self.assertEqual(rows["direction"]["action"], "update")
        commits = self.commits()
        self.apply(second, [{"section": "overview", "selected": True},
                            {"section": "how-it-works", "selected": False},
                            {"section": "ways-of-working", "selected": False},
                            {"section": "direction", "selected": False}])
        entry = brain.entry("s-overview")
        self.assertEqual(entry["classification"], "Current")
        self.assertTrue(any("checked against the code by ronald" in h.get("reason", "")
                            for h in entry["history"]), entry["history"])
        self.assertNotIn("A person's note.", entry["body"])
        self.assertEqual(handbook.read_record()["sections"]["overview"], ["s-overview"])
        self.assertEqual(self.commits(), commits + 1)


class Receipts(_Applying):

    def test_a_failed_commit_is_retried_alone(self):
        draft = self.generate()
        with mock.patch.object(taskfiles, "commit_paths", return_value=False):
            out = self.apply(draft, request="seed-commit-fail-1")
        self.assertEqual(out["operation"]["commit"]["state"], "failed")
        self.assertEqual(len(self.ids()), 4, "the files are saved whatever the commit did")
        commits = self.commits()
        again = brainwrite.retry({"request": "seed-commit-fail-1", "action": "commit"})
        self.assertEqual(again["commit"]["state"], "committed")
        self.assertEqual(self.commits(), commits + 1)
        self.assertEqual(again["push"]["state"], "off")


class Shared(_Applying):
    remote = True

    def setUp(self):
        super().setUp()
        # the fixture's own plans commit is published, as a person would have
        git(self.work, "push", "-q", "origin", "main")
        git(self.work, "fetch", "-q", "origin")

    def test_pending_then_shared_after_sync(self):
        draft = self.generate()
        out = self.apply(draft, request="seed-shared-1")
        self.assertEqual(out["operation"]["push"]["state"], "pending")
        shared = brainwrite.retry({"request": "seed-shared-1", "action": "push"})
        self.assertEqual(shared["push"]["state"], "shared", shared)

    def test_a_remote_conflict_is_compared_never_forced(self):
        draft = self.generate()
        self.apply(draft, request="seed-remote-1")
        other = self.dir / "teammate"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True,
                       capture_output=True)
        git(other, "config", "user.name", "elena")
        git(other, "config", "user.email", "e@example.com")
        (other / "brain").mkdir()
        (other / "brain" / "s-overview.md").write_text("# Elena's version\n")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "elena")
        git(other, "push", "-q", "origin", "main")
        out = brainwrite.retry({"request": "seed-remote-1", "action": "push"})
        self.assertIn(out["push"]["state"], ("conflict", "failed"), out)
        self.assertNotEqual(out["push"]["state"], "shared")
        remote_text = git(self.bare, "show", "main:brain/s-overview.md").stdout
        self.assertEqual(remote_text, "# Elena's version\n", "a remote version was forced over")
        if out["push"]["state"] == "conflict":
            found = brainwrite.compare("seed-remote-1")
            self.assertEqual(found["files"][0]["remote"], "# Elena's version\n")


if __name__ == "__main__":
    unittest.main()
