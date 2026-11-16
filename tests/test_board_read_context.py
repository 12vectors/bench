"""Task 127 — one board read asks the policy's shared inputs once.

`routes.state_payload` opens a request-scoped read context
(`profiles.read_scope`), and inside it the profile document, each intent's
resolution and every approved input — its content hash and its `git log`
provenance — are read once rather than once per evidence record. The
claims, a class each:

- **The repetition is gone.** On an evidence-rich board the provenance of
  each approved input is looked up once per request, not once per record.
- **The public reading is the same.** The payload with the scope and the
  payload without it are equal, field for field, but for the clock.
- **What the reading depends on still moves it.** Input content, the
  policy, the intent and its roots, the harness and model selection, the
  adapter's capabilities — within one scope — and refs, programs and the
  declared environment across requests, exactly as before.
- **Nothing crosses a request.** A scope is one thread's and ends with
  its payload; a concurrent request, a mutation's gate read and a launch
  read afresh; a new evidence record is on the next read.

Every case runs in the disposable furniture `tests/boardperf.py` builds
on `GateCase`: temporary roots, stub checks, no live board or remote.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path

from tests import boardperf as bp

import config  # noqa: E402
import evidence  # noqa: E402
import gates  # noqa: E402
import profiles  # noqa: E402
import routes  # noqa: E402
import staleness  # noqa: E402
import verify  # noqa: E402


class ReadContextCase(bp.PerfBoard):

    CARDS = 12
    RICH = 12
    INPUTS = 4

    def setUp(self):
        super().setUp()
        self.made = self.build(cards=self.CARDS, rich=self.RICH,
                               inputs=self.INPUTS)
        bp.forget_all()

    def input_path(self, n: int = 0) -> Path:
        return self.work / "reference" / f"input-{n:02d}.md"

    def count_provenance(self):
        """Wrap the one function that runs `git log` for an input."""
        calls: list[str] = []
        real = profiles._source_revision_now

        def counted(path):
            calls.append(str(path))
            return real(path)

        profiles._source_revision_now = counted
        self.addCleanup(setattr, profiles, "_source_revision_now", real)
        return calls


# ── the repetition is gone ─────────────────────────────────────────────


class TheRepetitionIsGone(ReadContextCase):

    def test_each_inputs_provenance_is_looked_up_once_per_read(self):
        calls = self.count_provenance()
        routes.state_payload()
        self.assertTrue(calls, "the fixture exercises the provenance lookup")
        self.assertEqual(len(calls), len(set(calls)),
                         "no input's provenance was looked up twice")
        self.assertLessEqual(len(set(calls)), self.INPUTS)

    def test_without_the_scope_it_was_once_per_record(self):
        """The fixture reproduces the cost the scope removes: the same
        read, unscoped, repeats each input's lookup many times."""
        calls = self.count_provenance()
        routes._state_payload()
        self.assertGreater(len(calls), 3 * self.INPUTS)

    def test_git_log_runs_once_per_input(self):
        with bp.GitCounter() as counter:
            routes.state_payload()
        self.assertLessEqual(counter.by_verb.get("log", 0), self.INPUTS)

    def test_the_scope_counts_what_it_saved(self):
        with profiles.read_scope() as counts:
            routes._state_payload()
            seen = counts()
        self.assertGreater(seen["asked"]["provenance"],
                           seen["computed"]["provenance"])
        self.assertEqual(seen["computed"]["provenance"], self.INPUTS)


# ── the public reading is the same ─────────────────────────────────────


class ThePublicReadingIsTheSame(ReadContextCase):

    def test_scoped_and_unscoped_payloads_are_equal(self):
        unscoped = routes._state_payload()
        bp.forget_all()
        scoped = routes.state_payload()
        self.assertEqual(bp.comparable(unscoped), bp.comparable(scoped))
        # and the sections this card is about actually say something
        self.assertTrue(scoped["verify"])
        self.assertTrue(scoped["gates"])
        self.assertTrue(scoped["delivery"])

    def test_every_outcome_the_fixture_wrote_is_read(self):
        found = {r["outcome"] for r in routes.state_payload()["verify"].values()}
        self.assertTrue({"failed", "stale", "running"} <= found, found)

    def test_a_record_reads_the_same_inside_and_outside(self):
        records = [r for r in evidence.all_evidence()
                   if r.get("outcome") == "passed"]
        outside = [staleness.freshness(r) for r in records]
        with profiles.read_scope():
            inside = [staleness.freshness(r) for r in records]
        self.assertEqual(json.dumps(outside, sort_keys=True, default=str),
                         json.dumps(inside, sort_keys=True, default=str))

    def test_an_answer_is_a_copy(self):
        with profiles.read_scope():
            first = profiles.resolve("work", worktree=self.wt)
            first["verify"].clear()
            first["context"][0]["sha256"] = "edited by a caller"
            again = profiles.resolve("work", worktree=self.wt)
        self.assertTrue(again["verify"])
        self.assertNotEqual(again["context"][0]["sha256"], "edited by a caller")


# ── what a reading depends on still moves it ───────────────────────────


class InvalidationWithinOneScope(ReadContextCase):

    def context_ids(self, **roots) -> list[str]:
        return [i["contextId"] for i in
                profiles.resolve("work", **roots)["context"]]

    def test_input_content_is_read_again(self):
        with profiles.read_scope():
            before = self.context_ids(worktree=self.wt)
            self.input_path().write_text("edited, a different size\n",
                                         encoding="utf-8")
            after = self.context_ids(worktree=self.wt)
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(before[1:], after[1:])

    def test_a_removed_input_is_read_again(self):
        with profiles.read_scope():
            profiles.resolve("work", worktree=self.wt)
            self.input_path().unlink()
            found = profiles.resolve("work", worktree=self.wt)
        self.assertEqual(found["context"][0]["status"], "missing")

    def test_the_policy_is_read_again(self):
        with profiles.read_scope():
            before = profiles.resolve("work", worktree=self.wt)
            doc = json.loads((self.local / "profiles.json").read_text())
            doc["profiles"]["work"]["verify"] = ["unit"]
            self.declares(doc)
            after = profiles.resolve("work", worktree=self.wt)
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertEqual([c["name"] for c in after["verify"]], ["unit"])

    def test_intent_and_roots_are_separate_answers(self):
        with profiles.read_scope():
            work = profiles.resolve("work", worktree=self.wt)
            other = profiles.resolve("work", worktree=self.tmp / "elsewhere")
            review = profiles.resolve("review", review=self.wt)
            relevance = profiles.resolve("review")
        self.assertEqual(work["intent"], "work")
        self.assertEqual(review["intent"], "review")
        self.assertNotEqual(json.dumps(work["roots"], default=str),
                            json.dumps(other["roots"], default=str))
        self.assertNotEqual(json.dumps(review["roots"], default=str),
                            json.dumps(relevance["roots"], default=str))

    def test_the_model_selection_is_read_again(self):
        with profiles.read_scope():
            before = profiles.resolve("work", worktree=self.wt)
            (self.local / ".env").write_text(
                f"{config.MODEL_KEY}=a-model-chosen-mid-request\n",
                encoding="utf-8")
            after = profiles.resolve("work", worktree=self.wt)
        self.assertNotEqual(json.dumps(before.get("selection"), default=str),
                            json.dumps(after.get("selection"), default=str))

    def test_the_adapters_capabilities_are_read_again(self):
        directory = config.adapter_dir(
            config.agent_choice("work")["harness"]["value"])
        self.assertIsNotNone(directory)
        path = directory / "capabilities.json"
        if not path.parent.joinpath("run").is_file():
            self.skipTest("no adapter directory to edit")
        with profiles.read_scope():
            before = profiles.resolve("work", worktree=self.wt)
            local = self.local / "adapters" / directory.name
            local.mkdir(parents=True, exist_ok=True)
            (local / "run").write_text("#!/bin/sh\n", encoding="utf-8")
            (local / "capabilities.json").write_text(
                json.dumps({"version": "edited", "capabilities": {}}),
                encoding="utf-8")
            after = profiles.resolve("work", worktree=self.wt)
        self.assertNotEqual(before["adapter"].get("version"),
                            after["adapter"].get("version"))

    def test_a_draft_is_never_memoised(self):
        doc = profiles.document()
        with profiles.read_scope() as counts:
            profiles.resolve("work", worktree=self.wt, policy=doc)
            profiles.resolve("work", worktree=self.wt, agent_values={})
            seen = counts()
        self.assertNotIn("resolve", seen["asked"])


class InvalidationAcrossRequests(ReadContextCase):
    """What the scope cannot hold — the next request reads it all again,
    and the board's own memos expire exactly as they did."""

    def reading_of(self, outcome: str = "passed", check: str = "unit") -> dict:
        bp.forget_all()
        records = [r for r in evidence.all_evidence()
                   if r.get("outcome") == outcome and r.get("check") == check
                   and r.get("task") != bp.CARD]
        payload = routes.state_payload()
        return payload["verify"][records[0]["task"]]

    def test_an_edited_input_stales_the_pass_on_the_next_read(self):
        self.assertNotEqual(self.reading_of()["outcome"], "stale")
        record = next(r for r in evidence.all_evidence()
                      if r.get("outcome") == "passed")
        self.input_path().write_text("a changed approved input\n",
                                     encoding="utf-8")
        verify.forget_cached()
        with profiles.read_scope():
            found = staleness.outcome_now(record)
        self.assertEqual(found["outcome"], "stale")
        self.assertIn("input-changed", found["freshness"]["reasons"])

    def test_a_replaced_program_stales_the_pass(self):
        record = next(r for r in evidence.all_evidence()
                      if r.get("outcome") == "passed")
        self.executable("unit", bp.PASSES + "# replaced\n")
        verify.forget_cached()
        with profiles.read_scope():
            found = staleness.outcome_now(record)
        self.assertIn("program-changed", found["freshness"]["reasons"])

    def test_a_moved_branch_stales_a_source_pass(self):
        record = next(r for r in evidence.all_evidence()
                      if r.get("outcome") == "passed"
                      and r.get("check") == "unit-src"
                      and r.get("task") != bp.CARD)
        branch = config.branch_for(record["task"][:-3])
        moved = bp.git(self.wt, "rev-parse", "HEAD").stdout.strip()
        bp.git(self.repo, "update-ref", f"refs/heads/{branch}", moved)
        verify.forget_cached()
        with profiles.read_scope():
            found = staleness.outcome_now(record)
        self.assertIn("source-changed", found["freshness"]["reasons"])

    def test_a_declared_environment_value_stales_the_pass(self):
        doc = json.loads((self.local / "profiles.json").read_text())
        for check in doc["checks"]:
            check["env"] = ["PERF_FIXTURE_VALUE"]
        self.declares(doc)
        # the environment a check is given is `config._ENV`, as in
        # tests/test_result_identity.py
        self.addCleanup(setattr, config, "_ENV", config._ENV)
        config._ENV = dict(config._ENV, PERF_FIXTURE_VALUE="one")
        self.manifest(agent="run-env")
        record = self.ask("unit", agent="run-env", again=True)
        self.assertEqual(record["outcome"], "passed")
        verify.forget_cached()
        with profiles.read_scope():
            self.assertEqual(staleness.outcome_now(record)["outcome"], "passed")
        config._ENV["PERF_FIXTURE_VALUE"] = "two"
        verify.forget_cached()
        with profiles.read_scope():
            found = staleness.outcome_now(record)
        self.assertIn("env-changed", found["freshness"]["reasons"])


# ── nothing crosses a request ──────────────────────────────────────────


class NothingCrossesARequest(ReadContextCase):

    def test_the_scope_ends_with_the_payload(self):
        routes.state_payload()
        self.assertFalse(profiles.in_read_scope())

    def test_another_thread_never_sees_a_scope(self):
        seen = {}
        with profiles.read_scope():
            worker = threading.Thread(
                target=lambda: seen.update(scoped=profiles.in_read_scope()))
            worker.start()
            worker.join()
        self.assertEqual(seen, {"scoped": False})

    def test_two_concurrent_scopes_each_read_for_themselves(self):
        barrier = threading.Barrier(2)
        counts: list[dict] = []

        def request():
            with profiles.read_scope() as seen:
                profiles.resolve("work", worktree=self.wt)
                barrier.wait()
                profiles.resolve("work", worktree=self.wt)
                counts.append(seen())

        workers = [threading.Thread(target=request) for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        for seen in counts:
            self.assertEqual(seen["computed"]["resolve"], 1)
            self.assertEqual(seen["asked"]["resolve"], 2)

    def test_concurrent_reads_agree(self):
        results: list[str] = []

        def request():
            results.append(bp.comparable(routes.state_payload()))

        workers = [threading.Thread(target=request) for _ in range(4)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(len(results), 4)
        self.assertEqual(len(set(results)), 1)

    def test_a_mutations_gate_reads_the_current_policy_not_a_scope(self):
        """A gate read in another thread — the one a merge makes — sees a
        policy edit a still-open read scope memoised the old answer for."""
        ready, edited, done = (threading.Event() for _ in range(3))
        held: dict = {}

        def reader():
            with profiles.read_scope():
                held["before"] = profiles.document()["revision"]
                ready.set()
                edited.wait(10)
                done.wait(10)

        worker = threading.Thread(target=reader)
        worker.start()
        ready.wait(10)
        doc = json.loads((self.local / "profiles.json").read_text())
        doc["gates"] = {"acceptance": {"checks": ["unit"]}}
        self.declares(doc)
        edited.set()
        decision = gates.evaluate(bp.CARD, "acceptance")
        done.set()
        worker.join()
        self.assertEqual([r.get("check") for r in decision["requirements"]
                          if r.get("check")], ["unit"])
        self.assertNotEqual(held["before"], profiles.document()["revision"])

    def test_a_new_record_is_on_the_next_read(self):
        first = routes.state_payload()["verify"]
        filename = next(f for f, row in first.items()
                        if row["outcome"] == "failed")
        record = next(r for r in evidence.all_evidence()
                      if r.get("task") == filename)
        newer = dict(record, id=record["id"] + ".newer",
                     requested=record["requested"] + 10_000,
                     outcome="running", rc=None, reason=None)
        evidence.store(newer)
        self.assertTrue(routes.state_payload()["verify"][filename]["live"])


if __name__ == "__main__":
    unittest.main()
