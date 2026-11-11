"""One durable delivery status per card (card 98, design #94 §3, §7, §11).

`delivery.py` computes, server-side, the one status a card wears, its
primary action and who holds it, and the merge requirements that apply at
the current commit — from the authorities that already answer each
question. Every case runs against a disposable tool install, work root and
application checkout (`GateCase`): real git, real evidence written by the
real `verify.request`, stub check executables. Nothing here reaches
GitHub, and no live card is moved.

- **E17 — the copy matrix.** `TheCopyMatrix` is the F1–F32 table as
  executable fixtures: the status word, the primary action and, where the
  row says, the counts and the sentence.
- **The vocabulary is derived.** `TheVocabulary` holds `WORDS` to
  `ROWS` and to the fixtures, rather than repeating a word count.
- **E18 — restart.** `ARestartForgetsNothingDurable`: with empty agent
  memory and an empty poll, the persisted PR snapshot still says merged,
  with its observation time, and "on this board" qualifies absence.
- **E10 — counts.** `CountsAreRequiredOnly`.
- **E20 — reading is free.** `ReadingChangesNothing`: no state bytes, no
  fetch, no GitHub call, no process beyond read-only local git.
- **The cache is never authority.** `TheCacheFollowsItsInputs` and
  `MutationsStillRevalidate`.
- **Later handlers.** `ActionsAreHonest`: an action whose handler is a
  later card's is unavailable with the reason.
- **Check the remote** and **merged on GitHub** reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path

from tests.test_integration_gates import (CARD, FAILS, HANGS, PASSES,  # noqa: E402
                                          STEM, GateCase, card, git,
                                          wait_for)

import agents  # noqa: E402
import config  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import github  # noqa: E402
import httpd  # noqa: E402
import phases  # noqa: E402
import plans  # noqa: E402
import state  # noqa: E402
import taskfiles  # noqa: E402
import verify  # noqa: E402

STATUS = {"backlog": "Backlog", "to-do": "To Do", "in-progress": "In Progress",
          "review": "Review", "done": "Done"}
PR_URL = "https://github.com/example/app/pull/7"
PHASE = "80-ship-the-site.md"


class DeliveryCase(GateCase):

    def setUp(self):
        super().setUp()
        delivery.forget_cached()
        self.addCleanup(delivery.forget_cached)
        self.addCleanup(self._cancel_running)

    def _cancel_running(self):
        for ident in list(verify.RUNNING):
            verify.cancel(ident, "cancelled-with-run")

    # — the furniture —

    def place(self, stage: str, filename: str = CARD, title: str = "71 — Gate integration",
              **headers) -> None:
        for slug in config.STAGE_DIRS:
            (self.tasks / slug / filename).unlink(missing_ok=True)
        pr = headers.pop("pr", None)
        text = card(title, status=STATUS[stage], **headers)
        if pr:
            text = text.replace("**Priority:** High",
                                f"**Priority:** High\n**PR:** {pr}", 1)
        (self.tasks / stage / filename).write_text(text, encoding="utf-8")

    def full_gate(self, **kw) -> dict:
        """The fixtures' gate (design §11.1): `unit` plus a Bench review."""
        kw.setdefault("review", "bench")
        return self.gated(**kw)

    def passed_at_head(self, body: str = PASSES, **kw) -> str:
        wt = self.worktree()
        self.full_gate(body=body, **kw)
        head = self.commit_in(wt)
        self.manifest()
        self.ask()
        verify.forget_cached()
        return head

    def read(self, filename: str = CARD) -> dict:
        return delivery.for_card(filename)

    def word(self, filename: str = CARD) -> str:
        return self.read(filename)["status"]["word"]

    def snapshot(self, filename: str = CARD, **values) -> None:
        entry = {"head": None, "prState": "OPEN", "merged": False,
                 "url": PR_URL, "ts": time.time()}
        entry.update(values)
        github._persist_snapshot(filename, entry)

    def running_agent(self, name: str = "Wren") -> None:
        state.AGENTS["run-live"] = {"id": "run-live", "task": CARD,
                                    "status": "running", "name": name,
                                    "started": time.time(), "mode": "work"}

    def hang_a_check(self) -> threading.Thread:
        asking = threading.Thread(target=lambda: self.ask(), daemon=True)
        asking.start()
        self.assertTrue(wait_for(lambda: bool(verify.RUNNING)))
        return asking

    def origin(self) -> Path:
        bare = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        git(self.repo, "remote", "add", "origin", str(bare))
        git(self.repo, "push", "-q", "origin", "main")
        return bare


# ── E17: the copy matrix, F1–F32 ───────────────────────────────────────


class TheCopyMatrix(DeliveryCase):
    """Design §11.1, one test per row. Each asserts the word and the
    primary action — the two things every surface draws — and the counts
    or sentence where the row names them."""

    def expect(self, word: str, primary: str | None, found: dict | None = None):
        found = found or self.read()
        self.assertEqual(found["status"]["word"], word, found["status"])
        got = (found["primary"] or {}).get("action")
        self.assertEqual(got, primary, found["primary"])
        return found

    def test_f01_backlog_never_started(self):
        self.full_gate()
        self.place("backlog")
        found = self.expect("not-started", "move")
        self.assertEqual(found["primary"]["to"], "to-do")
        self.assertEqual(found["candidate"]["state"], "none-yet")

    def test_f02_to_do(self):
        self.place("to-do")
        found = self.expect("queued", "move")
        self.assertEqual(found["primary"]["to"], "in-progress")

    def test_f03_in_progress_nobody_no_commit(self):
        self.full_gate()
        found = self.expect("ready-to-start", "start")
        self.assertEqual(found["counts"], {"required": 2, "met": 0})
        self.assertEqual({r["class"] for r in found["requirements"]},
                         {"needed"}, "future requirements are not alarms")
        self.assertNotIn("can start", found["status"]["sentence"].lower())

    def test_f04_agent_running(self):
        wt = self.worktree()
        self.full_gate()
        self.commit_in(wt)
        self.running_agent()
        found = self.expect("agent-working", None)
        self.assertEqual(found["status"]["label"], "Wren is working")
        self.assertEqual([a["action"] for a in found["others"]], ["hold"])
        self.assertEqual(found["counts"], {"required": 2, "met": 0})

    def test_f05_in_progress_check_failed_at_head(self):
        head = self.passed_at_head(body=FAILS)
        found = self.expect("check-failed", "start")
        self.assertIn(head[:12], found["status"]["sentence"])
        self.assertIn("read-failure", [a["action"] for a in found["others"]])

    def test_f06_launch_refused_is_a_fresh_check_not_a_claim(self):
        self.full_gate()
        self.place("in-progress", base_branch="no-such-branch")
        found = self.expect("ready-to-start", "start")
        self.assertIn("launch-check", [a["action"] for a in found["others"]])
        checked = delivery.launch_check(CARD, refusals=agents.launch_refusals)
        self.assertFalse(checked["ok"])
        self.assertIn("no-such-branch", checked["says"])

    def test_f07_review_check_missing(self):
        head = self.passed_at_head()
        for record in verify.for_task(CARD):
            verify.path_of(record["id"]).unlink()
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.expect("checks-needed", "run-required-checks")
        self.assertFalse(found["primary"]["enabled"])
        self.assertIn("later", found["primary"]["why"])
        self.assertIn(f"has not run on {head[:12]}", found["status"]["sentence"])
        self.assertEqual(found["counts"], {"required": 2, "met": 0})

    def test_f08_review_check_running(self):
        wt = self.worktree()
        self.full_gate(body=HANGS)
        self.commit_in(wt)
        self.manifest()
        self.hang_a_check()
        self.place("review", pr=PR_URL)
        # RUNNING fills a moment before the record binds its source.
        wait_for(lambda: self.word() == "checks-running")
        found = self.expect("checks-running", None)
        self.assertEqual([a["action"] for a in found["others"]],
                         ["cancel-check"])

    def test_f09_review_check_failed(self):
        self.passed_at_head(body=FAILS)
        self.place("review", pr=PR_URL)
        self.expect("check-failed", "act-pr")

    def test_f10_review_preparation_failed(self):
        wt = self.worktree()
        document = self.full_gate()
        document["prepare"] = [{"name": "deps",
                                "run": str(self.executable("deps", FAILS))}]
        document["checks"][0]["prepare"] = ["deps"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "prepare-failed")
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.expect("cannot-run-check", "act-pr")
        self.assertIn("preparation", found["status"]["sentence"])

    def test_f11_review_cancelled_at_head(self):
        wt = self.worktree()
        self.full_gate(body=HANGS)
        head = self.commit_in(wt)
        self.manifest()
        asking = self.hang_a_check()
        self._cancel_running()
        asking.join(timeout=20)
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.expect("checks-needed", "run-required-checks")
        self.assertIn(f"cancelled before a result on {head[:12]}",
                      found["status"]["sentence"])

    def test_f12_review_preparation_changed_since_it_passed(self):
        wt = self.worktree()
        document = self.full_gate()
        document["prepare"] = [{"name": "deps",
                                "run": str(self.executable("deps", PASSES))}]
        document["checks"][0]["prepare"] = ["deps"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask()["outcome"], "passed")
        document["prepare"][0]["run"] = str(self.executable("deps2", PASSES))
        self.declares(document)
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.expect("checks-needed", "run-required-checks")
        row = found["requirements"][0]
        self.assertEqual(row["reason"], "stale")
        self.assertEqual(row["staleReason"], "prepare-changed")

    def test_f13_checks_met_no_verdict(self):
        self.passed_at_head()
        self.place("review", pr=PR_URL)
        found = self.expect("review-needed", "review-pr")
        self.assertEqual(found["counts"], {"required": 2, "met": 1})

    def test_f14_checks_met_no_pr(self):
        self.passed_at_head()
        self.place("review")
        found = self.expect("review-needed", "open-pr")
        self.assertIn("no pull request", found["status"]["sentence"])

    def test_f15_bench_asked_for_changes(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=head)
        self.place("review", pr=PR_URL)
        found = self.expect("changes-requested", "act-pr")
        self.assertIn(head[:12], found["status"]["sentence"])

    def test_f16_all_met(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        found = self.expect("ready-to-merge", "complete")
        self.assertTrue(found["primary"]["enabled"])
        self.assertIn(head[:12], found["status"]["sentence"])
        self.assertEqual(found["counts"], {"required": 2, "met": 2})

    def test_f17_a_new_commit_after_f16(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        newer = self.commit_in(config.WORKTREES / STEM, "a later push\n")
        self.place("review", pr=PR_URL)
        found = self.expect("checks-needed", "run-required-checks")
        self.assertIn(newer[:12], found["status"]["sentence"])
        self.assertEqual(found["counts"], {"required": 2, "met": 0})

    def test_f18_pr_already_merged_on_github(self):
        head = self.passed_at_head(body=FAILS)
        self.place("review", pr=PR_URL)
        self.snapshot(head=head, prState="MERGED", merged=True)
        found = self.expect("merged-elsewhere", "complete")
        self.assertEqual(found["primary"]["label"], "Finish: clean up")
        self.assertEqual(found["requirements"], [],
                         "administrative closure asks for no obsolete "
                         "check or review")
        self.assertEqual(found["counts"], {"required": 0, "met": 0})
        self.assertEqual(found["history"]["obsolete"], 2)

    def test_f18_a_merged_pr_does_not_hide_work_pushed_after_it(self):
        # Returned or reopened work under a card whose PR merged earlier:
        # the snapshot is about the old head, the branch has moved on, and
        # the new commit's requirements still apply.
        merged = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=merged)
        self.place("review", pr=PR_URL)
        self.snapshot(head=merged, prState="MERGED", merged=True)
        newer = self.commit_in(config.WORKTREES / STEM, "work after the merge\n")
        found = self.read()
        self.assertNotEqual(found["status"]["word"], "merged-elsewhere")
        self.assertEqual(found["candidate"]["commit"], newer)
        self.assertEqual(found["counts"], {"required": 2, "met": 0})
        self.assertEqual(found["history"]["obsolete"], 0)
        self.assertNotEqual((found["primary"] or {}).get("label"),
                            "Finish: clean up")
        self.assertIn("does not deliver this work", found["status"]["sentence"])

    def test_f18_a_merged_pr_ahead_of_this_board_still_closes(self):
        # The local branch is behind what GitHub merged: it holds nothing
        # the merge did not, so administrative closure stands.
        older = self.passed_at_head(body=FAILS)
        wt = config.WORKTREES / STEM
        merged = self.commit_in(wt, "pushed from elsewhere\n")
        git(wt, "reset", "-q", "--hard", older)
        self.place("review", pr=PR_URL)
        self.snapshot(head=merged, prState="MERGED", merged=True)
        found = self.expect("merged-elsewhere", "complete")
        self.assertEqual(found["counts"], {"required": 0, "met": 0})

    def test_f19_stacked_all_met(self):
        git(self.repo, "branch", "feature/x", "main")
        wt = self.worktree(base="feature/x")
        self.full_gate()
        head = self.commit_in(wt)
        self.manifest()
        self.ask()
        verify.forget_cached()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL, base_branch="feature/x")
        found = self.expect("ready-to-merge", "open-pr-url")
        self.assertIn("feature/x", found["status"]["sentence"])
        self.assertTrue(self.read()["completion"]["stacked"])

    def phase_card(self, stage: str = "in-progress", members=("71",)) -> None:
        body = "## Cards\n\n" + "\n".join(f"- {m} — a member" for m in members)
        (self.tasks / stage / PHASE).write_text(
            card("80 — Ship the site", status=STATUS[stage], type="Phase",
                 body=body), encoding="utf-8")

    def test_f20_phase_member_in_review(self):
        self.full_gate(scope="member")
        self.phase_card()
        self.place("review")
        found = self.expect("waiting-for-phase", None)
        self.assertIn("#80", found["status"]["sentence"])

    def test_f21_phase_integration_check_not_run(self):
        self.gated(scope="integration")
        self.place("done")
        self.phase_card(stage="review")
        git(self.repo, "branch", "phase/80-ship-the-site", "main")
        found = self.expect("checks-needed", "run-required-checks",
                            self.read(PHASE))
        self.assertEqual(found["decision"]["scope"], "integration")
        self.assertEqual(found["candidate"]["ref"], "phase/80-ship-the-site")

    def test_f22_returned_with_its_branch(self):
        wt = self.worktree()
        self.commit_in(wt)
        self.place("backlog")
        found = self.expect("returned", "move")
        self.assertEqual(found["primary"]["to"], "in-progress")

    def test_f23_returned_branch_gone_evidence_remains(self):
        self.origin()
        self.passed_at_head()
        git(self.repo, "worktree", "remove", "--force", str(config.WORKTREES / STEM))
        git(self.repo, "branch", "-D", f"task/{STEM}")
        self.place("backlog")
        found = self.expect("cannot-assess", "check-remote")
        self.assertEqual(found["candidate"]["state"], "unavailable")
        self.assertEqual(found["status"]["tone"], "alarm")

    def test_f24_reopened_after_delivery(self):
        github._record_delivered(CARD, "1a2b3c4d5e6f7a8b9c0d", "main", "local")
        self.place("backlog")
        found = self.expect("not-started", "move")
        self.assertEqual(found["candidate"]["state"], "delivered")
        self.assertIn("delivered at 1a2b3c4d5e6f", found["status"]["sentence"])

    def test_f25_held_by_someone_else_no_local_branch(self):
        self.origin()
        self.patch(COMMIT_MOVES=True)
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "istos"
        self.place("in-progress", assignee="elena")
        found = self.expect("ready-to-start", "takeover")
        sentence = found["status"]["sentence"]
        self.assertIn("elena holds it", sentence)
        self.assertIn("on this board", sentence)
        self.assertIn("check-remote", [a["action"] for a in found["others"]])

    def test_f26_after_a_restart_all_met(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        self.snapshot(head=head, ts=time.time() - 120)
        github.PR_STATE.clear()
        state.AGENTS.clear()
        found = self.expect("ready-to-merge", "complete")
        self.assertEqual(found["source"]["pr"]["from"], "disk")
        self.assertGreaterEqual(found["source"]["pr"]["age"], 119)

    def test_f27_override_in_force(self):
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "ron"
        wt = self.worktree()
        self.full_gate(body=FAILS, overrides={"allow": True})
        head = self.commit_in(wt)
        self.manifest()
        self.ask()
        verify.forget_cached()
        gates.override(CARD, "acceptance", actor="ron",
                       reason="the runner is down, demo at 5")
        self.place("review", pr=PR_URL)
        found = self.expect("ready-overridden", "complete")
        self.assertEqual(found["status"]["tone"], "alarm")
        self.assertIn("ron proceeded over", found["status"]["sentence"])
        self.assertIn(head[:12], found["status"]["sentence"])
        self.assertIn("Still unmet", found["status"]["sentence"])

    def test_f28_merge_rules_do_not_parse(self):
        self.declares({"v": 1, "gates": {"acceptance": {"checks": ["unit"]}}})
        self.place("review", pr=PR_URL)
        self.expect("rules-invalid", "settings-merge-rules")

    def test_f29_ungated_review_with_pr(self):
        wt = self.worktree()
        self.commit_in(wt)
        self.place("review", pr=PR_URL)
        found = self.expect("review", "review-pr")
        self.assertIn("not a pass", found["status"]["sentence"])
        self.assertIn("complete", [a["action"] for a in found["others"]])
        self.assertEqual(found["counts"], {"required": 0, "met": 0})

    def test_f29_ungated_bench_change_request_at_head_is_not_lost(self):
        """Task 99: the face used to wear "changes asked" from a cascade
        of its own. Drawing the delivery status instead must not lose it —
        so Bench's own REQUEST CHANGES at the current commit is a current
        problem even where no review is required, and one at an older
        commit is history."""
        wt = self.worktree()
        older = self.commit_in(wt)
        gates.record_verdict(CARD, verdict="REQUEST CHANGES", head=older)
        self.place("review", pr=PR_URL)
        found = self.expect("changes-requested", "act-pr")
        self.assertIn(older[:12], found["status"]["sentence"])
        self.commit_in(wt, "addressed\n")
        delivery.forget_cached()
        # a review is on record, so the ungated row offers the merge first
        # and the review again among the alternatives
        found = self.expect("review", "complete")
        self.assertIn("review-pr", [a["action"] for a in found["others"]])

    def test_f30_the_safer36_shape(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        document = json.loads((self.local / "profiles.json").read_text())
        (self.work / "reference" / "card37-mockup.md").write_text(
            "the next card's mockup\n", encoding="utf-8")
        document["context"] = [{"name": "card37-mockup",
                                "path": "work:reference/card37-mockup.md",
                                "required": False}]
        self.declares(document)
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.expect("ready-to-merge", "complete")
        self.assertIn(head[:12], found["status"]["sentence"])

    def test_f31_not_published(self):
        directory = github._recovery_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{STEM}.json").write_text(json.dumps({
            "branch": f"task/{STEM}", "target": "main", "mode": "local",
            "remote": "origin", "commit": "9" * 40, "publication": "failed",
            "detail": "remote: protected branch"}), encoding="utf-8")
        self.place("review", pr=PR_URL)
        found = self.expect("not-published", "complete")
        self.assertEqual(found["primary"]["label"], "Retry publication")
        self.assertIn("protected branch", found["status"]["sentence"])

    def test_f32_in_progress_failed_run(self):
        state.AGENTS["run-dead"] = {
            "id": "run-dead", "task": CARD, "status": "failed",
            "started": time.time(), "name": "Juno",
            "failure": {"rc": 1, "stage": "in-progress", "mode": "work",
                        "excerpt": "API Error: 500 upstream"}}
        found = self.expect("run-failed", "start")
        self.assertIn("rc=1", found["status"]["sentence"])
        self.assertIn("API Error", found["status"]["sentence"])

    def test_a_failure_in_another_stage_is_not_worn_here(self):
        state.AGENTS["run-dead"] = {
            "id": "run-dead", "task": CARD, "status": "failed",
            "started": time.time(),
            "failure": {"rc": 1, "stage": "review", "mode": "review-pr",
                        "excerpt": "boom"}}
        self.expect("ready-to-start", "start")

    def test_merging_in_flight(self):
        self.place("review", pr=PR_URL)
        state.COMPLETING[CARD] = {"started": time.time(), "step": "parking"}
        self.addCleanup(state.COMPLETING.clear)
        found = self.expect("merging", None)
        self.assertIn("parking", found["status"]["sentence"])

    def test_a_running_phase(self):
        self.phase_card()
        phases.SNAPSHOTS[PHASE] = {"running": True, "members": [
            {"number": "71", "state": "working"}]}
        found = self.expect("phase-running", None, delivery.for_card(
            PHASE, None, phases.public_state()))
        self.assertEqual(
            delivery.board(taskfiles.collect(), phases.public_state())
            [PHASE]["status"]["word"], "phase-running")
        self.assertIn("#71", found["status"]["sentence"])
        self.assertEqual([a["action"] for a in found["others"]],
                         ["hold-phase"])

    def test_done_is_delivered(self):
        self.place("done")
        self.expect("delivered", None)


# ── the vocabulary, derived rather than counted ────────────────────────


class TheVocabulary(unittest.TestCase):

    def test_every_word_has_a_row_and_every_row_word_exists(self):
        produced = {word for _, _, words in delivery.ROWS for word in words}
        self.assertEqual(produced, set(delivery.WORDS))
        for word, (label, tone) in delivery.WORDS.items():
            with self.subTest(word=word):
                self.assertTrue(label)
                self.assertIn(tone, delivery.TONES)

    def test_the_copy_matrix_covers_every_word_it_can(self):
        """Every word but those a later card brings has a fixture asserting
        it — counted from the test source, so the claim cannot drift."""
        source = Path(__file__).read_text(encoding="utf-8")
        missing = [w for w in delivery.WORDS
                   if w not in delivery.NOT_YET
                   and f'"{w}"' not in source.split("class TheVocabulary")[0]]
        self.assertEqual(missing, [])
        rows = sorted({int(name.split("_")[1][1:])
                       for name in dir(TheCopyMatrix)
                       if name.startswith("test_f") and name[6:8].isdigit()})
        self.assertEqual(rows, list(range(1, 33)), "F1–F32, each one")

    def test_it_imports_nothing_to_its_right_in_the_module_map(self):
        """AGENTS.md's dependency order: delivery sits after
        events / github / drive / sync and before agentsettings."""
        import re
        source = Path(delivery.__file__).read_text(encoding="utf-8")
        imported = set(re.findall(r"^import (\w+)$", source, re.MULTILINE))
        brief = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(
            encoding="utf-8")
        line = next(ln for ln in brief.splitlines()
                    if ln.startswith("config → "))
        order = [name.strip() for step in line.split("→")
                 for name in step.split("/")]
        self.assertIn("delivery", order)
        left = set(order[:order.index("delivery")])
        project = imported & set(order)
        self.assertEqual(project - left, set(), "only modules to its left")

    def test_severity_and_plain_cover_every_gate_reason(self):
        for reason in gates.REASONS:
            with self.subTest(reason=reason):
                self.assertIn(gates.severity(reason), gates.SEVERITY_CLASSES)
                self.assertTrue(gates.plain(reason, "a" * 40))
        for reason in ("cancelled", "stale", "other-commit"):
            self.assertEqual(gates.severity(reason), "needed",
                             "a missing result is not a failure")
        self.assertEqual(gates.severity("a-word-nobody-wrote"), "problem")


# ── E18: a restart keeps what is durable ───────────────────────────────


class ARestartForgetsNothingDurable(DeliveryCase):

    def test_a_merged_pr_survives_empty_memory_with_its_time(self):
        head = self.passed_at_head()
        self.place("review", pr=PR_URL)
        github.PR_STATE[CARD] = {"head": head, "prState": "MERGED",
                                 "merged": True, "url": PR_URL,
                                 "ts": time.time() - 300}
        github._persist_snapshot(CARD, github.PR_STATE[CARD])
        # the restart: every in-memory registry empty
        github.PR_STATE.clear()
        state.AGENTS.clear()
        gates.REVIEW_STATE.clear()
        found = self.read()
        self.assertEqual(found["status"]["word"], "merged-elsewhere")
        self.assertEqual(found["source"]["pr"]["from"], "disk")
        self.assertAlmostEqual(found["source"]["pr"]["observed"],
                               time.time() - 300, delta=5)

    def test_the_poll_persists_at_its_own_boundary(self):
        self.place("review", pr=PR_URL)
        self.addCleanup(setattr, github, "_gh_pr", github._gh_pr)
        payload = {"state": "MERGED", "headRefOid": "c" * 40,
                   "mergedAt": "2026-11-11T10:00:00Z", "reviews": [],
                   "statusCheckRollup": [], "baseRefName": "main"}
        github._gh_pr = lambda *a, **k: subprocess.CompletedProcess(
            a, 0, json.dumps(payload), "")
        github._poll_pr(CARD, PR_URL)
        stored = json.loads((self.state / "gates" / f"{STEM}.pr.json")
                            .read_text())
        self.assertTrue(stored["merged"])
        self.assertEqual(stored["head"], "c" * 40)
        self.assertIn("observed", stored)

    def test_absence_is_qualified_as_on_this_board(self):
        self.origin()
        found = self.read()
        self.assertEqual(found["candidate"]["state"], "none-yet")
        self.assertEqual(found["candidate"]["qualifier"], "on this board")
        self.assertIn("on this board", found["status"]["sentence"])

    def test_agent_memory_decides_no_candidate(self):
        self.place("backlog")
        state.AGENTS["ghost"] = {"id": "ghost", "task": CARD,
                                 "status": "done", "started": time.time()}
        self.assertEqual(self.read()["candidate"]["state"], "none-yet")


# ── E10 / R4: counts are required obligations only ─────────────────────


class TheCompactReadingCarriesWhatEverySurfaceDraws(DeliveryCase):
    """Task 99: the face, the Overview and the merge dialog draw the one
    compact reading in `/api/state`, so it carries the alternatives, the
    history counts and the requirement classes — and agrees with the full
    reading on every field the surfaces share."""

    def test_compact_and_full_agree(self):
        self.passed_at_head(body=FAILS)
        self.place("review", pr=PR_URL)
        task = delivery._find(CARD, None)
        compact = delivery.board({"stages": [{"tasks": [task]}]})[CARD]
        full = self.read()
        for key in ("status", "counts", "history"):
            self.assertEqual(compact[key], full[key], key)
        for key, value in compact["candidate"].items():
            self.assertEqual(value, full["candidate"][key], key)
        self.assertEqual(compact["primary"]["action"], full["primary"]["action"])
        self.assertEqual([a["action"] for a in compact["others"]],
                         [a["action"] for a in full["others"]])
        self.assertEqual(compact["classes"],
                         sorted({r["class"] for r in full["requirements"]}))
        self.assertIn("problem", compact["classes"])

    def test_the_state_route_carries_it(self):
        self.full_gate()
        self.place("in-progress")
        found = httpd.state_payload()["delivery"][CARD]
        self.assertEqual(found["status"]["word"], "ready-to-start")
        self.assertEqual(found["classes"], ["needed"],
                         "a requirement that lies ahead is needed, never a problem")

    # — the rows' identity: what the page keys its full reading by —

    def compact(self) -> dict:
        delivery.forget_cached()
        task = delivery._find(CARD, None)
        return delivery.board({"stages": [{"tasks": [task]}]})[CARD]

    def summary(self, found: dict) -> tuple:
        return (found["status"], found["counts"], found["classes"],
                found["candidate"], found["history"])

    def two_checks(self, required: list[str]) -> dict:
        document = self.gated()
        document["checks"].append({"name": "lint",
                                   "run": str(self.executable("lint", PASSES)),
                                   "root": "worktree"})
        for intent in document["profiles"].values():
            intent["verify"] = ["unit", "lint"]
        document["gates"]["acceptance"]["checks"] = required
        self.declares(document)
        return document

    def test_compact_and_full_carry_one_detail_identity(self):
        self.passed_at_head(body=FAILS)
        self.place("review", pr=PR_URL)
        self.assertTrue(self.compact()["detail"])
        self.assertEqual(self.compact()["detail"], self.read()["detail"])

    def test_a_rule_replaced_with_the_same_totals_is_a_new_detail(self):
        """The supervisor's finding: the approved rule moves from one
        check to another, while the word, the counts, the classes and the
        candidate stay exactly as they were. The rows changed, so the
        identity the page keys them by must change with them."""
        wt = self.worktree()
        self.two_checks(["unit"])
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask("unit")["outcome"], "passed")
        self.assertEqual(self.ask("lint")["outcome"], "passed")
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        before, rows_before = self.compact(), self.read()["requirements"]
        self.assertEqual(before["status"]["word"], "ready-to-merge")
        self.two_checks(["lint"])
        after, rows_after = self.compact(), self.read()["requirements"]
        self.assertEqual(self.summary(before), self.summary(after),
                         "the fixture must keep every summary field equal")
        self.assertNotEqual([r["check"] for r in rows_before],
                            [r["check"] for r in rows_after])
        self.assertNotEqual(before["detail"], after["detail"])
        self.assertEqual(after["detail"], self.read()["detail"])

    def test_new_evidence_behind_the_same_summary_is_a_new_detail(self):
        """Same summary, different rows: a rerun of the required check
        passes again, so every summary field — the sentence included — is
        unchanged, while the row now names a different evidence record."""
        self.passed_at_head()
        self.place("review", pr=PR_URL)
        before, row_before = self.compact(), self.read()["requirements"][0]
        self.place("in-progress")
        self.assertEqual(self.ask("unit")["outcome"], "passed")
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        after, row_after = self.compact(), self.read()["requirements"][0]
        self.assertEqual(self.summary(before), self.summary(after))
        self.assertNotEqual(row_before["evidence"], row_after["evidence"])
        self.assertNotEqual(before["detail"], after["detail"])

    def test_the_detail_is_stable_while_nothing_changes(self):
        self.passed_at_head(body=FAILS)
        self.place("review", pr=PR_URL)
        self.assertEqual(self.compact()["detail"], self.compact()["detail"])
        self.assertEqual(self.read()["detail"], self.read()["detail"])

    def test_the_launch_check_names_its_intent_and_ownership(self):
        self.full_gate()
        self.place("in-progress")
        launch = next(a for a in self.compact()["others"]
                      if a["action"] == "launch-check")
        self.assertEqual(launch["intent"], "work")
        self.assertFalse(launch["takeover"])


class CountsAreRequiredOnly(DeliveryCase):

    def test_an_optional_check_never_inflates_the_count(self):
        wt = self.worktree()
        document = self.gated()
        document["checks"].append({"name": "docs",
                                   "run": str(self.executable("docs", PASSES)),
                                   "root": "worktree"})
        for intent in document["profiles"].values():
            intent["verify"] = ["unit", "docs"]
        self.declares(document)
        self.commit_in(wt)
        self.manifest()
        self.assertEqual(self.ask("unit")["outcome"], "passed")
        verify.forget_cached()
        self.place("review", pr=PR_URL)
        found = self.read()
        self.assertEqual(found["counts"], {"required": 1, "met": 1})
        self.assertEqual(found["history"]["optional"], 1)
        self.assertEqual(found["status"]["word"], "ready-to-merge")
        self.assertNotIn("docs", [r["check"] for r in found["requirements"]])

    def test_history_is_counted_apart(self):
        head = self.passed_at_head()
        self.commit_in(config.WORKTREES / STEM, "newer\n")
        self.place("review", pr=PR_URL)
        found = self.read()
        self.assertEqual(found["history"]["older"], 1)
        self.assertEqual(found["counts"]["required"], 2)
        self.assertNotIn(head[:12], found["requirements"][0]["plain"])


# ── E20: reading changes nothing ───────────────────────────────────────


READ_ONLY_GIT = {"for-each-ref", "rev-parse", "merge-base", "config",
                 "status", "diff", "ls-files", "hash-object", "remote",
                 "rev-list", "cat-file", "show", "log"}


class ReadingChangesNothing(DeliveryCase):

    def fingerprint(self) -> dict:
        found = {}
        for root in (self.state, self.tasks):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found

    def test_state_card_and_delivery_reads_write_fetch_and_launch_nothing(self):
        self.origin()
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        self.snapshot(head=head)
        before = self.fingerprint()
        calls: list[list[str]] = []
        real_popen = subprocess.Popen

        class Recorded(real_popen):
            def __init__(self, cmd, *a, **k):
                calls.append([str(c) for c in cmd])
                super().__init__(cmd, *a, **k)

        subprocess.Popen = Recorded
        try:
            httpd.state_payload()
            delivery.for_card(CARD)
            httpd.gates_payload(CARD, "review")
        finally:
            subprocess.Popen = real_popen
        self.assertEqual(self.fingerprint(), before, "no state byte changed")
        for cmd in calls:
            with self.subTest(cmd=cmd):
                self.assertEqual(Path(cmd[0]).name, "git",
                                 "only git, never gh or a check")
                rest = cmd[1:]
                while rest and rest[0] in ("-C", "-c"):
                    rest = rest[2:]
                while rest and rest[0].startswith("-"):
                    rest = rest[1:]
                verb = rest[0]
                self.assertIn(verb, READ_ONLY_GIT, f"{cmd} is not a read")

    def test_the_http_route_is_a_read(self):
        server = httpd.ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler) \
            if hasattr(httpd, "ThreadingHTTPServer") else None
        if server is None:
            from http.server import ThreadingHTTPServer
            server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        import urllib.request
        port = server.server_address[1]
        before = self.fingerprint()
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/card/delivery?file={CARD}") as r:
            body = json.loads(r.read())
        self.assertEqual(body["delivery"]["status"]["word"], "ready-to-start")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as r:
            body = json.loads(r.read())
        self.assertEqual(body["delivery"][CARD]["status"]["word"],
                         "ready-to-start")
        self.assertEqual(self.fingerprint(), before)


HELPER = """#!/bin/sh
kind=$1; shift
echo "$kind" >> "{marks}"
case $kind in
  clean) exec cat ;;
  textconv) exec cat "$1" ;;
  *) exit 0 ;;
esac
"""


class ReadingRunsNoRepositoryHelper(DeliveryCase):
    """A repository's own configuration can make a plain `git diff` or
    `git status` run a program — a textconv, an external diff driver, a
    clean filter, an fsmonitor hook, a signature check — or write its
    index. Reading the board asks git those questions, so the
    application here is configured to do every one of them, each helper
    marking a file when it runs, and the reads must leave no mark and no
    changed byte."""

    def setUp(self):
        super().setUp()
        self.marks = self.tmp / "marks"
        helper = self.tmp / "helper"
        helper.write_text(HELPER.format(marks=self.marks), encoding="utf-8")
        helper.chmod(0o755)
        self.helper = helper

    def arm(self) -> None:
        """Configure every helper, after the fixture's own commits."""
        common = Path(git(self.repo, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = self.repo / common
        (common / "info").mkdir(exist_ok=True)
        (common / "info" / "attributes").write_text(
            "* diff=probe filter=probe\n", encoding="utf-8")
        hooks = self.tmp / "hooks"
        hooks.mkdir(exist_ok=True)
        for name in ("post-index-change", "reference-transaction", "post-checkout"):
            hook = hooks / name
            hook.write_text(f"#!/bin/sh\n\"{self.helper}\" hook-{name}\n", encoding="utf-8")
            hook.chmod(0o755)
        for key, value in (
                ("diff.probe.textconv", f"{self.helper} textconv"),
                ("diff.probe.command", f"{self.helper} extdiff"),
                ("diff.external", f"{self.helper} external"),
                ("filter.probe.clean", f"{self.helper} clean"),
                ("filter.probe.smudge", "cat"),
                ("core.fsmonitor", f"{self.helper} fsmonitor"),
                ("core.hooksPath", str(hooks)),
                ("log.showSignature", "true"),
                ("gpg.program", f"{self.helper} gpg")):
            git(self.repo, "config", key, value)

    def bytes_of(self) -> dict:
        found = {}
        for root in (self.state, self.tasks, self.repo, config.WORKTREES):
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    found[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found

    def marked(self) -> list[str]:
        if not self.marks.exists():
            return []
        return self.marks.read_text(encoding="utf-8").split()

    def read_everything(self) -> dict:
        delivery.forget_cached()
        verify.forget_cached()
        httpd.state_payload()
        found = delivery.for_card(CARD)
        httpd.gates_payload(CARD, "review")
        return found

    def a_reviewed_head(self) -> tuple[str, Path]:
        self.origin()
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        self.snapshot(head=head)
        return head, config.WORKTREES / STEM

    def test_the_helpers_are_live_without_the_hardening(self):
        """The control: the same configuration does run helpers for an
        ordinary git, so the cases below prove something."""
        _, wt = self.a_reviewed_head()
        self.arm()
        (wt / "code.txt").write_text("edited after the pass\n", encoding="utf-8")
        git(wt, "diff", "HEAD")
        git(wt, "status", "--porcelain")
        self.assertTrue({"extdiff", "clean", "fsmonitor"} & set(self.marked()),
                        self.marked())

    def test_a_clean_checkout_is_read_without_running_a_helper(self):
        _, wt = self.a_reviewed_head()
        self.arm()
        before = self.bytes_of()
        found = self.read_everything()
        self.assertEqual(self.marked(), [], "a repository helper ran during a read")
        self.assertEqual(self.bytes_of(), before, "a read changed a byte")
        self.assertEqual(found["status"]["word"], "ready-to-merge", found["status"])

    def test_dirty_work_and_evidence_freshness_run_no_helper(self):
        _, wt = self.a_reviewed_head()
        self.arm()
        (wt / "code.txt").write_text("edited after the pass\n", encoding="utf-8")
        before = self.bytes_of()
        found = self.read_everything()
        self.assertEqual(self.marked(), [], "a repository helper ran during a read")
        self.assertEqual(self.bytes_of(), before, "a read changed a byte")
        # The uncommitted edit is still seen for what it is: the source
        # identity names it, so the pass bound to the clean commit no
        # longer reads as this checkout's.
        identity = verify.source_identity(wt)
        self.assertEqual(self.marked(), [])
        self.assertIn("code.txt", " ".join(identity["dirty"]))
        self.assertTrue(found["status"]["word"])

    def test_a_textconv_alone_does_not_run(self):
        """The acceptance probe exactly: a marker-only textconv through
        info/attributes, a dirty tracked file and passing evidence."""
        _, wt = self.a_reviewed_head()
        common = Path(git(self.repo, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = self.repo / common
        (common / "info").mkdir(exist_ok=True)
        (common / "info" / "attributes").write_text("code.txt diff=probe\n",
                                                   encoding="utf-8")
        git(self.repo, "config", "diff.probe.textconv", f"{self.helper} textconv")
        (wt / "code.txt").write_text("edited after the pass\n", encoding="utf-8")
        git(wt, "diff", "HEAD")
        self.assertIn("textconv", self.marked(), "the control runs it")
        self.marks.unlink()
        delivery.forget_cached()
        verify.forget_cached()
        delivery.for_card(CARD)
        self.assertEqual(self.marked(), [])

    def test_without_the_hardening_the_read_would_run_it(self):
        """What the switches are for: the same read through an unhardened
        git does run the helper, so a regression here is caught."""
        _, wt = self.a_reviewed_head()
        self.arm()
        (wt / "code.txt").write_text("edited after the pass\n", encoding="utf-8")
        real_argv, real_env = config.git_argv, config.git_env
        config.git_argv = lambda root, *a: ["git", "-C", str(root), *a]
        config.git_env = lambda root, *a: None
        try:
            self.read_everything()
        finally:
            config.git_argv, config.git_env = real_argv, real_env
        self.assertNotEqual(self.marked(), [])


class GitReadsAreHardened(unittest.TestCase):
    """`config.git_argv` and `git_env` change a read and nothing else."""

    def test_a_read_diff_gains_the_switches(self):
        argv = config.git_argv("/r", "diff", "HEAD")
        self.assertEqual(argv, ["git", "-C", "/r", "diff", "--no-textconv",
                                "--no-ext-diff", "HEAD"])
        env = config.git_env(None, "rev-parse", "HEAD")
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        keys = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
                for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(keys["core.hooksPath"], "/dev/null")
        self.assertEqual(keys["core.fsmonitor"], "false")

    def test_an_authorised_write_is_run_exactly_as_written(self):
        for args in (("merge", "--no-edit", "x"), ("push", "origin", "main"),
                     ("fetch", "origin"), ("worktree", "add", "p", "b"),
                     ("commit", "-m", "m")):
            with self.subTest(args=args):
                self.assertEqual(config.git_argv("/r", *args),
                                 ["git", "-C", "/r", *args])
                self.assertIsNone(config.git_env("/r", *args))


# ── the cache is never authority ───────────────────────────────────────


class TheCacheFollowsItsInputs(DeliveryCase):
    """Populate the board cache, change one authority input, read again —
    inside the same time bucket and without forgetting anything."""

    def board_word(self) -> str:
        return delivery.board(taskfiles.collect())[CARD]["status"]["word"]

    def setUp(self):
        super().setUp()
        self.head = self.passed_at_head()
        self.place("review", pr=PR_URL)
        self.assertEqual(self.board_word(), "review-needed")

    def test_a_verdict(self):
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        self.assertEqual(self.board_word(), "ready-to-merge")

    def test_a_new_commit(self):
        self.commit_in(config.WORKTREES / STEM, "moved on\n")
        self.assertEqual(self.board_word(), "checks-needed")

    def test_a_policy_change(self):
        document = json.loads((self.local / "profiles.json").read_text())
        document["gates"] = {"acceptance": {"checks": ["nope"]}}
        self.declares(document)
        self.assertEqual(self.board_word(), "rules-invalid")

    def test_new_evidence(self):
        self.declares(json.loads((self.local / "profiles.json").read_text()
                                 .replace(str(self.bin / "unit"),
                                          str(self.executable("unit2", FAILS)))))
        self.manifest(agent="run-2")
        self.ask(agent="run-2")
        self.assertEqual(self.board_word(), "check-failed")

    def test_a_finding_in_the_card(self):
        import acceptance
        self.addCleanup(setattr, taskfiles, "actor_name", taskfiles.actor_name)
        taskfiles.actor_name = lambda: "istos"
        acceptance.finding(CARD, "review", event="opened",
                           text="the empty state is unreadable")
        self.assertEqual(self.board_word(), "changes-requested")

    def test_a_completion_record(self):
        directory = github._recovery_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{STEM}.json").write_text(json.dumps(
            {"commit": self.head, "target": "main"}), encoding="utf-8")
        self.assertEqual(self.board_word(), "not-published")

    def test_a_lifecycle_change(self):
        state.COMPLETING[CARD] = {"started": time.time(), "step": ""}
        self.addCleanup(state.COMPLETING.clear)
        self.assertEqual(self.board_word(), "merging")
        state.COMPLETING.clear()
        self.running_agent()
        self.assertEqual(self.board_word(), "agent-working")
        state.AGENTS.clear()
        self.place("backlog")
        self.assertEqual(self.board_word(), "returned")

    def test_a_pr_snapshot(self):
        self.snapshot(head=self.head, prState="MERGED", merged=True)
        self.assertEqual(self.board_word(), "merged-elsewhere")

    def test_a_recorded_plan(self):
        # A ⌖ replan rewrites the recorded plan; a card retargeted onto a
        # stacked branch is no longer one merge & clean up finishes.
        gates.record_verdict(CARD, verdict="APPROVE", head=self.head)
        reading = lambda: delivery.board(taskfiles.collect())[CARD]
        self.assertEqual(reading()["primary"]["action"], "complete")
        git(self.repo, "branch", "feature/x", "main")
        config.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        plans.plan_path(CARD).write_text(json.dumps(
            {"v": 1, "task": CARD, "branch": f"task/{STEM}",
             "appBase": "main", "prBase": "feature/x"}), encoding="utf-8")
        self.assertEqual(reading()["primary"]["action"], "open-pr-url")


class TheCacheFollowsTheDeliveredRecord(DeliveryCase):

    def test_a_delivered_record(self):
        self.place("backlog")
        reading = lambda: delivery.board(taskfiles.collect())[CARD]
        self.assertEqual(reading()["candidate"]["state"], "none-yet")
        github._record_delivered(CARD, "1a2b3c4d5e6f7a8b9c0d", "main", "local")
        self.assertEqual(reading()["candidate"]["state"], "delivered")


# ── the launch check asks the launch's own refusals ───────────────────


class TheLaunchCheckIsTheLaunchsOwn(DeliveryCase):
    """Plan and profile resolving cleanly is not the whole of a launch:
    the stage, a phase card, ownership, a running agent and the prompt
    template refuse too. Each case asserts the check says what
    `start_agent` then does, and that the check wrote nothing."""

    def check(self, **kw) -> dict:
        return delivery.launch_check(CARD, refusals=agents.launch_refusals,
                                     **kw)

    def assert_same_refusal(self, stage: str, phrase: str) -> None:
        card_bytes = (self.tasks / stage / CARD).read_bytes()
        checked = self.check()
        self.assertFalse(checked["ok"], checked)
        self.assertIn(phrase, checked["says"])
        self.assertEqual((self.tasks / stage / CARD).read_bytes(), card_bytes)
        self.assertFalse((config.WORKTREES / STEM).exists())
        self.assertFalse(state.AGENTS)
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, stage)
        self.assertIn(phrase, str(caught.exception))
        self.assertEqual((self.tasks / stage / CARD).read_bytes(), card_bytes)

    def test_backlog_is_refused_as_the_launch_refuses_it(self):
        self.place("backlog")
        self.assert_same_refusal("backlog", "work starts from in-progress/")

    def test_a_phase_card_is_refused(self):
        self.place("in-progress", type="Phase")
        self.assert_same_refusal("in-progress", "is a phase card")

    def test_someone_elses_card_is_refused_without_a_claim(self):
        self.patch(COMMIT_MOVES=True)
        self.addCleanup(setattr, agents, "actor_name", agents.actor_name)
        agents.actor_name = lambda: "istos"
        self.place("in-progress", assignee="elena")
        self.assert_same_refusal("in-progress", "elena holds")
        self.assertFalse([b for b in self.check(takeover=True)["blockers"]
                          if "elena holds" in b])

    def test_a_running_agent_is_refused(self):
        self.place("in-progress")
        self.running_agent()
        self.addCleanup(state.AGENTS.clear)
        checked = self.check()
        self.assertFalse(checked["ok"])
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertEqual(checked["blockers"][0], str(caught.exception))

    def test_a_broken_template_override_is_refused(self):
        self.place("in-progress")
        prompts = config.LOCAL / "prompts"
        prompts.mkdir(parents=True, exist_ok=True)
        (prompts / "work.md").write_text("no placeholders {nope}\n",
                                         encoding="utf-8")
        checked = self.check()
        self.assertFalse(checked["ok"])
        with self.assertRaises(ValueError) as caught:
            agents.start_agent(CARD, "in-progress")
        self.assertIn(str(caught.exception), checked["blockers"])

    def test_a_clean_card_is_clear(self):
        self.place("in-progress")
        checked = self.check()
        self.assertTrue(checked["ok"], checked["blockers"])
        self.assertFalse((config.WORKTREES / STEM).exists())

    def test_without_the_launchs_refusals_nothing_is_said_to_be_clear(self):
        self.place("in-progress")
        with self.assertRaises(ValueError):
            delivery.launch_check(CARD)


class MutationsStillRevalidate(DeliveryCase):

    def test_ready_on_the_cache_is_not_a_merge_licence(self):
        head = self.passed_at_head()
        gates.record_verdict(CARD, verdict="APPROVE", head=head)
        self.place("review", pr=PR_URL)
        self.assertEqual(
            delivery.board(taskfiles.collect())[CARD]["status"]["word"],
            "ready-to-merge")
        self.commit_in(config.WORKTREES / STEM, "a push nobody checked\n")
        with self.assertRaises(ValueError) as caught:
            github.complete_task(CARD, "review")
        self.assertIn("blocked", str(caught.exception))
        self.assertEqual(taskfiles.find_stage_of(CARD), "review")


# ── actions a later card brings are honest about it ────────────────────


class ActionsAreHonest(unittest.TestCase):

    def test_an_action_without_a_handler_is_never_enabled(self):
        for key, spec in delivery.ACTIONS.items():
            with self.subTest(action=key):
                found = delivery.action(key, key, enabled=True)
                if spec.get("later"):
                    self.assertFalse(found["enabled"])
                    self.assertEqual(found["why"], spec["later"])
                elif spec["kind"] == "api":
                    self.assertTrue(found["enabled"])
                    self.assertIsNotNone(spec["handler"])

    def test_every_handler_is_a_route_the_server_answers(self):
        source = (Path(httpd.__file__)).read_text(encoding="utf-8")
        for key, spec in delivery.ACTIONS.items():
            if spec["handler"]:
                with self.subTest(action=key):
                    self.assertIn(f'"{spec["handler"]}"', source)


# ── Check the remote ───────────────────────────────────────────────────


class CheckTheRemote(DeliveryCase):

    def test_absent_refs_are_an_answer_not_an_error(self):
        self.origin()
        found = delivery.check_remote(CARD)
        self.assertIsNone(found["error"])
        self.assertEqual(found["fetched"], [])
        self.assertEqual(sorted(found["absent"]),
                         sorted([f"task/{STEM}", f"phase/{STEM}"]))

    def test_a_branch_only_the_remote_has_is_fetched_and_reported(self):
        bare = self.origin()
        other = self.tmp / "elsewhere"
        git(self.tmp, "clone", "-q", str(bare), str(other))
        git(other, "config", "user.email", "e@e")
        git(other, "config", "user.name", "elena")
        git(other, "checkout", "-q", "-b", f"task/{STEM}")
        (other / "code.txt").write_text("elena's work\n", encoding="utf-8")
        git(other, "commit", "-qam", "theirs")
        git(other, "push", "-q", "origin", f"task/{STEM}")
        self.assertEqual(self.read()["candidate"]["state"], "none-yet",
                         "viewing fetched nothing")
        found = delivery.check_remote(CARD)
        self.assertEqual(found["fetched"], [f"task/{STEM}"])
        after = self.read()
        self.assertEqual(after["candidate"]["state"], "unavailable")
        self.assertEqual(after["candidate"]["remote"]["ref"], f"task/{STEM}")
        self.assertNotIn(f"task/{STEM}", gates._tips(),
                         "no local branch was created")

    def test_no_remote_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            delivery.check_remote(CARD)
        self.assertIn("no remote", str(caught.exception))


# ── a PR merged on GitHub is reconciled, not re-gated ──────────────────


class MergedOnGitHub(DeliveryCase):

    def test_finish_cleans_up_without_asking_for_obsolete_checks(self):
        bare = self.origin()
        wt = self.worktree()
        self.full_gate(body=FAILS)
        head = self.commit_in(wt)
        git(self.repo, "push", "-q", "origin", f"task/{STEM}")
        # GitHub merges it: origin/main gains the branch's commit
        elsewhere = self.tmp / "github"
        git(self.tmp, "clone", "-q", str(bare), str(elsewhere))
        git(elsewhere, "config", "user.email", "g@g")
        git(elsewhere, "config", "user.name", "github")
        git(elsewhere, "fetch", "-q", "origin", f"task/{STEM}")
        git(elsewhere, "merge", "-q", "--no-ff", "-m", "Merge PR",
            "FETCH_HEAD")
        git(elsewhere, "push", "-q", "origin", "main")
        self.place("review", pr=PR_URL)
        self.snapshot(head=head, prState="MERGED", merged=True)
        self.assertEqual(self.word(), "merged-elsewhere")
        self.assertEqual(gates.evaluate(CARD, "acceptance")["decision"],
                         "blocked", "the gate is unmet, and not asked")
        result = github.complete_task(CARD, "review")
        self.assertTrue(result["moved"], result)
        self.assertEqual(taskfiles.find_stage_of(CARD), "done")
        self.assertEqual(github.delivered_record(CARD)["commit"], head)

    def test_an_unconfirmed_merge_is_still_gated(self):
        self.origin()
        wt = self.worktree()
        self.full_gate(body=FAILS)
        head = self.commit_in(wt)
        self.place("review", pr=PR_URL)
        # the snapshot says merged; the remote does not hold it
        self.snapshot(head=head, prState="MERGED", merged=True)
        with self.assertRaises(ValueError):
            github.complete_task(CARD, "review")
        self.assertEqual(taskfiles.find_stage_of(CARD), "review")


if __name__ == "__main__":
    unittest.main()
