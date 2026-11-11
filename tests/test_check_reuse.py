"""Card 100 — an equal, current pass is returned rather than run again.

Design #94 §5.3 (first route), §6 and §10: a second request for the same
check at the same commit, with the same result identity in every
dimension, runs nothing. It gets the original evidence record back — its
handle, its requester, its checkout and its provenance untouched — and a
small reuse record names who asked this time. `again` (the ◫ sheet's ↻,
`bench-verify --again`) always runs. The newest relevant answer decides:
a later failure, cancellation or a run in flight is never stepped past.

Every case runs against a disposable tool install, work root and
application checkout (`GateCase`) with stub check executables that count
their own executions in a file outside every identity. Nothing here reads
or writes a live board, SAFER, a remote or operator policy.

- Acceptance 1 (executes once, the original handle) — `SameCheckTwice`.
- Acceptance 2 (what refuses reuse, what does not) — `WhatRefusesReuse`,
  `WhatDoesNotRefuseReuse`.
- Acceptance 3 (newest relevant answer, --again, concurrency) —
  `NewestRelevantAnswerDecides`, `AgainRuns`.
- Acceptance 4 (authorisation first; text is never evidence) —
  `AuthorisationComesFirst`, `TextIsNeverEvidence` (E9, E15).
- Acceptance 5 (legacy, secrets) — `LegacyAndSecrets` (E14).
- The shim — `TheShim`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.test_integration_gates import CARD, wait_for  # noqa: E402
from tests.test_result_identity import SECRET, IdentityCase  # noqa: E402

import config  # noqa: E402
import gates  # noqa: E402
import verify  # noqa: E402

SHIM = Path(config.CORE) / "bench-verify"

# A check whose verdict is read from a flag file and which counts its own
# executions — both outside the source, the check's definition and every
# other dimension of its identity, so only bench's own decision about
# whether to run it changes the count.
COUNTED = """#!{python}
import sys, time
open("@COUNT@", "a").write("ran\\n")
flag = open("@FLAG@").read().strip()
if flag == "hang":
    print("working", flush=True)
    time.sleep(600)
print("flag:", flag)
sys.exit(1 if flag == "fail" else 0)
"""


class ReuseCase(IdentityCase):

    def setUp(self):
        super().setUp()
        self.count = self.tmp / "count"
        self.flag = self.tmp / "flag"
        self.flag.write_text("pass", encoding="utf-8")
        config._ENV.pop("BENCH_TEST_DB", None)

    def counted(self, **check) -> dict:
        body = COUNTED.replace("@COUNT@", str(self.count)) \
                      .replace("@FLAG@", str(self.flag))
        return self.document(body=body, **check)

    def runs(self) -> int:
        try:
            return len(self.count.read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def fresh(self) -> None:
        verify.forget_cached()
        gates.forget_cached()

    def again(self, document: dict, agent: str, *, intent: str = "work",
              **kwargs) -> dict:
        """A second request, by a new run of `intent` frozen on `document`
        as it is now."""
        self.declares(document)
        self.manifest(intent, agent=agent,
                      worktree=self.wt if intent != "review" else None)
        self.fresh()
        return self.ask(agent=agent, **kwargs)

    def evidence_files(self) -> list[Path]:
        return sorted(config.EVIDENCE_DIR.glob("*.json"))


# ── acceptance 1 ───────────────────────────────────────────────────────


class SameCheckTwice(ReuseCase):

    def test_two_requests_at_one_head_execute_once(self):
        document = self.counted()
        first = self.run_check(document)
        self.assertEqual(first["outcome"], "passed")
        self.assertNotIn("reused", first)
        stored = verify.path_of(first["id"]).read_bytes()
        log = verify.log_path_of(first["id"]).read_bytes()
        second = self.again(document, "run-2")
        self.assertEqual(self.runs(), 1, "the second request ran nothing")
        self.assertEqual(second["outcome"], "passed")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["reused"], first["id"])
        # the evidence and its log are byte for byte what the first run wrote
        self.assertEqual(verify.path_of(first["id"]).read_bytes(), stored)
        self.assertEqual(verify.log_path_of(first["id"]).read_bytes(), log)
        self.assertEqual(len(self.evidence_files()), 1,
                         "no evidence was cloned or re-labelled")
        self.assertEqual(second["agent"], "run-1",
                         "the evidence keeps its own requester")

    def test_the_reuse_record_is_verifiable_and_names_the_requester(self):
        document = self.counted(root="source")
        first = self.run_check(document)
        second = self.again(document, "run-2", intent="review")
        reuse = second["reuse"]
        self.assertEqual(reuse["kind"], "reuse")
        self.assertTrue(verify.is_evidence_id(reuse["id"]))
        self.assertEqual(reuse["reused"], first["id"])
        self.assertEqual(reuse["agent"], "run-2")
        self.assertEqual(reuse["original"]["agent"], "run-1")
        self.assertEqual(reuse["commit"], first["source"]["commit"])
        # verifiable: the identity the requester would have had is the
        # evidence's own, digest for digest
        original = verify.read(first["id"])
        self.assertEqual(reuse["resultId"], original["resultId"])
        self.assertEqual(verify.result_id(reuse["identity"]),
                         original["resultId"])
        self.assertEqual(verify.read_reuse(reuse["id"]), reuse)
        self.assertEqual([r["id"] for r in verify.reuses_for_task(CARD)],
                         [reuse["id"]])
        # the page may show it, and it is not evidence
        public = verify.public(second)
        self.assertEqual(public["reused"], first["id"])
        self.assertEqual(public["reuse"]["id"], reuse["id"])
        self.assertNotIn(reuse["id"], [r["id"] for r in verify.all_evidence()])
        self.assertTrue(any("reused" in e.get("summary", "")
                            for e in __import__("state").BOARD_EVENTS))

    def test_the_gate_reads_the_original_evidence(self):
        document = self.counted()
        first = self.run_check(document)
        self.again(document, "run-2")
        self.fresh()
        row = self.decide()["requirements"][0]
        self.assertEqual(row["state"], "met")
        self.assertEqual(row["evidence"], first["id"])

    def test_a_fresh_source_checkout_is_reused_across_its_location(self):
        """A `source` check's checkout is made and removed per run; a second
        request — another requester, the checkout gone — reuses."""
        document = self.counted(root="source")
        first = self.run_check(document)
        self.assertEqual(first["outcome"], "passed")
        self.assertEqual(first["identity"]["rootClass"], "fresh")
        self.assertTrue(first["root"].get("removed"))
        self.fresh()
        person = verify.request(check="unit", task=CARD, stage="in-progress",
                                actor="you")
        self.assertEqual(self.runs(), 1)
        self.assertEqual(person["reused"], first["id"])
        self.assertEqual(person["reuse"]["actor"], "you")
        self.assertFalse(Path(first["root"]["path"]).exists(),
                         "deciding reuse made no checkout")


# ── acceptance 2 ───────────────────────────────────────────────────────


class WhatRefusesReuse(ReuseCase):
    """One subtest per difference: a pass, one change, then a request that
    must run (a new record, a second execution, no reuse)."""

    def mutations(self):
        def commit(doc):
            self.commit_in(self.wt, "a newer commit\n")

        def dirty(doc):
            (self.wt / "untracked.txt").write_text("new\n", encoding="utf-8")

        def argv(doc):
            doc["checks"][0]["run"] += " --different"

        def program(doc):
            path = self.bin / "unit"
            path.write_text(path.read_text() + "# replaced\n", encoding="utf-8")

        def coverage(doc):
            return {"scope": "fast"}

        def scope_args(doc):
            doc["checks"][0]["scopes"]["all"] = ["-k", "everything"]

        def preparation(doc):
            doc["prepare"][0]["run"] += " --upgrade"

        def input_(doc):
            self.brief.write_text("a different brief\n", encoding="utf-8")

        def env(doc):
            config._ENV["BENCH_TEST_DB"] = "elsewhere"

        def runner(doc):
            self.addCleanup(setattr, verify, "RUNNER_REVISION",
                            verify.RUNNER_REVISION)
            verify.RUNNER_REVISION = "e" * 64

        def root(doc):
            doc["checks"][0]["root"] = "source"

        return [("source: a new commit", commit),
                ("source: uncommitted work", dirty),
                ("check: its argv", argv),
                ("program: replaced at its path", program),
                ("coverage: a scope instead of the full run", coverage),
                ("coverage: the scope's arguments", scope_args),
                ("preparation", preparation),
                ("declared input", input_),
                ("declared environment value", env),
                ("runner", runner),
                ("root class", root)]

    def test_each_difference_runs_anew(self):
        for index, (label, mutate) in enumerate(self.mutations()):
            with self.subTest(label):
                from tests.test_integration_gates import git
                git(self.wt, "reset", "-q", "--hard")
                git(self.wt, "clean", "-qfd")
                config._ENV["BENCH_TEST_DB"] = "the test database"
                self.brief.write_text("the approved brief\n", encoding="utf-8")
                self.count.unlink(missing_ok=True)
                document = self.counted(
                    scopes={"fast": ["-k", "fast"], "all": ["-k", "all"]},
                    prepare=["deps"], inputs=["brief"], env=["BENCH_TEST_DB"])
                # forced: the previous subtest may have left an equal pass
                first = self.again(document, f"base-{index}", scope="all",
                                   again=True)
                self.assertEqual(first["outcome"], "passed", first.get("why"))
                asked = mutate(document) or {}
                second = self.again(document, f"next-{index}",
                                    scope=asked.get("scope", "all"))
                self.assertNotIn("reused", second, label)
                self.assertNotEqual(second["id"], first["id"], label)
                self.assertEqual(self.runs(), 2,
                                 f"{label}: {second['outcome']} — "
                                 f"{second.get('why')}")
                self.assertNotEqual(second.get("resultId"), first["resultId"],
                                    label)


class WhatDoesNotRefuseReuse(ReuseCase):

    def test_only_the_requester_differs(self):
        document = self.counted(root="source")
        first = self.run_check(document)
        for who, kwargs in (("another run", {"agent": "run-2"}),
                            ("a review run", {"agent": "run-3",
                                              "intent": "review"})):
            with self.subTest(who):
                again = self.again(document, kwargs.pop("agent"), **kwargs)
                self.assertEqual(again["reused"], first["id"])
        self.assertEqual(self.runs(), 1)

    def test_an_unrelated_policy_edit_does_not_refuse_it(self):
        """R1: approved context for another card moves no dimension."""
        document = self.counted()
        first = self.run_check(document)
        (self.work / "reference" / "card37.md").write_text("x\n",
                                                           encoding="utf-8")
        document["context"].append({"name": "card37",
                                    "path": "work:reference/card37.md"})
        again = self.again(document, "run-2")
        self.assertEqual(again["reused"], first["id"])
        self.assertNotEqual(again["reuse"]["policyRevision"],
                            first["policyRevision"],
                            "the reuse records the asker's own policy")

    def test_a_scoped_pass_is_reused_for_the_same_scope(self):
        document = self.counted(scopes={"fast": ["-k", "fast"]})
        first = self.run_check(document, scope="fast")
        again = self.again(document, "run-2", scope="fast")
        self.assertEqual(again["reused"], first["id"])
        self.assertEqual(self.runs(), 1)


# ── acceptance 3 ───────────────────────────────────────────────────────


class NewestRelevantAnswerDecides(ReuseCase):

    def test_a_later_failure_is_not_stepped_past(self):
        document = self.counted()
        self.run_check(document)
        self.flag.write_text("fail", encoding="utf-8")
        failed = self.again(document, "run-2", again=True)
        self.assertEqual(failed["outcome"], "failed")
        self.flag.write_text("pass", encoding="utf-8")
        third = self.again(document, "run-3")
        self.assertNotIn("reused", third)
        self.assertEqual(third["outcome"], "passed")
        self.assertEqual(self.runs(), 3)

    def test_a_later_cancellation_is_not_stepped_past(self):
        document = self.counted()
        first = self.run_check(document)
        self.flag.write_text("hang", encoding="utf-8")
        self.declares(document)
        self.manifest(agent="run-2")
        done: dict = {}
        asking = threading.Thread(target=lambda: done.update(
            self.ask(agent="run-2", again=True)), daemon=True)
        asking.start()
        self.assertTrue(wait_for(lambda: any(
            r.get("proc") is not None for r in list(verify.RUNNING.values()))))
        for ident in list(verify.RUNNING):
            verify.cancel(ident)
        asking.join(timeout=20)
        self.assertEqual(done.get("outcome"), "cancelled")
        self.flag.write_text("pass", encoding="utf-8")
        third = self.again(document, "run-3")
        self.assertNotIn("reused", third)
        self.assertNotEqual(third["id"], first["id"])

    def test_a_run_in_flight_is_joined_not_bypassed_by_an_older_pass(self):
        document = self.counted()
        first = self.run_check(document)
        self.flag.write_text("hang", encoding="utf-8")
        self.declares(document)
        self.manifest(agent="run-2")
        self.manifest(agent="run-3")
        rerun: dict = {}
        asking = threading.Thread(target=lambda: rerun.update(
            self.ask(agent="run-2", again=True)), daemon=True)
        asking.start()
        try:
            self.assertTrue(wait_for(lambda: any(
                (verify.read(i) or {}).get("outcome") == "running"
                for i in list(verify.RUNNING))))
            self.fresh()
            joined = self.ask(agent="run-3")
            self.assertNotIn("reused", joined)
            self.assertTrue(joined.get("duplicate"))
            self.assertNotEqual(joined["id"], first["id"])
        finally:
            for ident in list(verify.RUNNING):
                verify.cancel(ident)
            asking.join(timeout=20)
        self.assertFalse(asking.is_alive())


class AgainRuns(ReuseCase):

    def test_again_records_a_new_check(self):
        document = self.counted()
        first = self.run_check(document)
        forced = self.again(document, "run-2", again=True)
        self.assertEqual(self.runs(), 2)
        self.assertNotEqual(forced["id"], first["id"])
        self.assertTrue(forced["again"])
        self.assertNotIn("reused", forced)
        self.assertEqual(verify.read(forced["id"])["outcome"], "passed")
        # and the newer pass is what a plain request now reuses
        later = self.again(document, "run-3")
        self.assertEqual(later["reused"], forced["id"])
        self.assertEqual(self.runs(), 2)

    def test_again_while_one_is_running_joins_it(self):
        document = self.counted()
        self.flag.write_text("hang", encoding="utf-8")
        self.declares(document)
        self.manifest(agent="run-1")
        self.manifest(agent="run-2")
        first: dict = {}
        asking = threading.Thread(target=lambda: first.update(
            self.ask(agent="run-1")), daemon=True)
        asking.start()
        try:
            # the check itself has started (it counts before it hangs)
            self.assertTrue(wait_for(lambda: self.runs() == 1))
            joined = self.ask(agent="run-2", again=True)
            self.assertTrue(joined.get("duplicate"))
            self.assertEqual(len(verify.RUNNING), 1, "never doubled")
        finally:
            for ident in list(verify.RUNNING):
                verify.cancel(ident)
            asking.join(timeout=20)
        self.assertEqual(self.runs(), 1)


# ── acceptance 4 ───────────────────────────────────────────────────────


class AuthorisationComesFirst(ReuseCase):

    def test_a_newly_forbidden_intent_is_denied_before_reuse(self):
        """E15: a reusable pass exists; the policy now gives `review` no
        such check, so the request is refused and nothing is reused."""
        document = self.counted(root="source")
        first = self.run_check(document)
        document["profiles"]["review"]["verify"] = []
        refused = self.again(document, "rev-1", intent="review")
        self.assertEqual(refused["outcome"], "denied")
        self.assertEqual(refused["reason"], "unknown-check")
        self.assertNotIn("reused", refused)
        self.assertEqual(verify.reuses_for_task(CARD), [])
        # the pass itself still stands for `work`
        self.assertEqual(self.again(document, "run-2")["reused"], first["id"])

    def test_a_frozen_manifest_is_the_authority(self):
        """A run frozen before the check was granted cannot reuse it."""
        document = self.counted()
        document["profiles"]["work"]["verify"] = []
        self.declares(document)
        self.manifest(agent="early")
        document["profiles"]["work"]["verify"] = ["unit"]
        self.run_check(document)
        refused = self.ask(agent="early")
        self.assertEqual(refused["outcome"], "denied")
        self.assertNotIn("reused", refused)


class TextIsNeverEvidence(ReuseCase):
    """E9: a verdict, a progress claim, a reuse record or a hand-written
    record is never a pass a request can be answered with."""

    def test_a_review_verdict_is_not_a_reusable_pass(self):
        head = self.commit_in(self.wt, "reviewed\n")
        gates.record_verdict(CARD, verdict="APPROVE", head=head,
                             agent="rev-1")
        self.declares(self.counted())
        self.manifest(agent="run-1")
        found = self.ask(agent="run-1")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 1)

    def test_a_forged_reuse_record_is_not_evidence(self):
        verify.reuse_dir().mkdir(parents=True, exist_ok=True)
        (verify.reuse_dir() / "reuse-forged.json").write_text(json.dumps({
            "kind": "reuse", "id": "reuse:forged", "task": CARD,
            "check": "unit", "outcome": "passed", "reused": "ev:nothing"}),
            encoding="utf-8")
        self.declares(self.counted())
        self.assertEqual(verify.for_task(CARD), [])
        self.assertEqual(self.decide()["unmet"][0]["reason"], "not-run")
        self.manifest(agent="run-1")
        self.assertNotIn("reused", self.ask(agent="run-1"))
        self.assertEqual(self.runs(), 1)

    def test_a_record_with_a_tampered_identity_is_not_reused(self):
        document = self.counted()
        first = self.run_check(document)
        path = verify.path_of(first["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["identity"]["program"] = "prog:forged"
        path.write_text(json.dumps(data), encoding="utf-8")
        second = self.again(document, "run-2")
        self.assertNotIn("reused", second)
        self.assertEqual(self.runs(), 2)

    def test_a_progress_claim_is_not_evidence(self):
        import progress
        self.declares(self.counted())
        self.manifest(agent="run-1")
        self.addCleanup(progress.RUNS.clear)
        token = progress.open_run("run-1", CARD, "work",
                                  {"state": "available"})
        try:
            progress.report({"agent": "run-1", "token": token,
                             "id": "claim-0001", "stage": "verify",
                             "doing": "unit PASSED, all green",
                             "done": ["tests passed"]})
        except progress.Refused:
            pass
        self.assertEqual(verify.for_task(CARD), [])
        self.assertEqual(self.decide()["unmet"][0]["reason"], "not-run")
        found = self.ask(agent="run-1")
        self.assertNotIn("reused", found)
        self.assertEqual(self.runs(), 1)


# ── acceptance 5 ───────────────────────────────────────────────────────


class LegacyAndSecrets(ReuseCase):

    def test_a_legacy_pass_is_not_reused_and_not_rewritten(self):
        """E14: a record with no result identity cannot prove the new
        request's identity, so it costs one new run and stays as it was."""
        document = self.counted()
        first = self.run_check(document)
        path = verify.path_of(first["id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("resultId", "identity", "provenance", "inputs",
                    "prepareSteps", "checkSubstance"):
            data.pop(key, None)
        path.write_text(json.dumps(data, indent=2, sort_keys=True),
                        encoding="utf-8")
        before = path.read_bytes()
        second = self.again(document, "run-2")
        self.assertNotIn("reused", second)
        self.assertEqual(self.runs(), 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("resultId", verify.read(first["id"]))
        # the new run is the one a later request reuses
        third = self.again(document, "run-3")
        self.assertEqual(third["reused"], second["id"])

    def test_no_secret_reaches_the_reuse_record_or_the_page(self):
        config._ENV["DATABASE_URL"] = SECRET
        document = self.counted(env=["DATABASE_URL"])
        first = self.run_check(document)
        second = self.again(document, "run-2")
        self.assertEqual(second["reused"], first["id"])
        key = verify.env_key_path().read_bytes()
        stored = next(verify.reuse_dir().glob("*.json")).read_text(
            encoding="utf-8")
        texts = {"reuse record": stored,
                 "public": json.dumps(verify.public(second)),
                 "ticker": json.dumps(__import__("state").BOARD_EVENTS,
                                      default=str)}
        for where, text in texts.items():
            with self.subTest(where):
                self.assertNotIn("hunter2", text)
                self.assertNotIn(key.hex(), text)
                self.assertNotIn("hmac", text)
        self.assertEqual(verify.public(second)["reuse"]["original"]["agent"],
                         "run-1")


# ── the shim ───────────────────────────────────────────────────────────


class TheShim(unittest.TestCase):
    """`bench-verify` against a stand-in board: what it sends, and what it
    prints when the answer is a reuse."""

    def serve(self, answer: dict) -> tuple[int, list]:
        bodies: list = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):          # noqa: N802 — the stdlib's name
                length = int(self.headers.get("Content-Length") or 0)
                bodies.append(json.loads(self.rfile.read(length) or b"{}"))
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], bodies

    def shim(self, port: int, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BOARD_", "AGENT_"))}
        env.update(BOARD_PORT=str(port), BOARD_AGENT_ID="run-2")
        return subprocess.run([sys.executable, str(SHIM), *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_again_is_sent_and_a_plain_request_does_not_send_it(self):
        port, bodies = self.serve({"evidence": {"outcome": "passed",
                                                "check": "unit", "id": "ev:a"}})
        self.assertEqual(self.shim(port, "--again", "unit", "fast").returncode, 0)
        self.assertEqual(self.shim(port, "unit").returncode, 0)
        self.assertEqual(bodies[0], {"agent": "run-2", "check": "unit",
                                     "scope": "fast", "again": True})
        self.assertEqual(bodies[1], {"agent": "run-2", "check": "unit"})

    def test_a_reuse_is_printed_with_its_original_and_exits_zero(self):
        port, _ = self.serve({"evidence": {
            "outcome": "passed", "check": "unit", "id": "ev:verification@abc",
            "reused": "ev:verification@abc", "agent": "run-1",
            "reuse": {"id": "reuse:123", "original": {"agent": "run-1",
                                                      "intent": "work"}}}})
        result = self.shim(port, "unit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REUSED:   ev:verification@abc", result.stdout)
        self.assertIn("run for:  run-1 (work)", result.stdout)
        self.assertIn("reuse:    reuse:123", result.stdout)
        self.assertIn("--again", result.stdout)

    def test_the_page_reruns_with_again(self):
        html = (Path(config.CORE) / "board.html").read_text(encoding="utf-8")
        start = html.index("async function rerunCheck")
        self.assertIn("again: true", html[start:start + 600])


if __name__ == "__main__":
    unittest.main()
