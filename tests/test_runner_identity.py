"""Card 121 (WP12) — the verify train: one runner identity, one owner.

`verify.py` was split into `evidence` (the record), `resultidentity`
(what a verdict is about) and `staleness` (whether a stored pass still
holds). Before the split the runner dimension of every result identity
was the hash of `verify.py`'s own bytes, so moving authority out of that
file would have left it outside the hash. What this holds:

- **One owner, read at call time.** The revision is
  `resultidentity.RUNNER_REVISION`, a digest over the declared
  `RUNNER_FILES`. `verify` has no such attribute: reading or patching the
  old name fails loudly, naming the owner, and no core module copies the
  value — `RunnerRevisionHasOneOwner`, `ReadersResolveAtCallTime`.
- **The boundary is an audit.** Every core module a runner file imports
  is a runner file or is named in `BOUND_ELSEWHERE` with its reason; the
  verdict-path functions live in runner files; `redact` is reached only
  for display — `TheBoundaryIsAudited`.
- **Golden dimensions.** For a fixture, every dimension of the result
  identity is the value the code computed before the move; only `runner`
  differs, and it differs by definition — `GoldenDimensions`.
- **Every owner's memo is cleared** by `verify.forget_cached` —
  `MemosAreClearedByEveryOwner`.
- **Real workers.** A duplicate request joins the run in flight; a
  cancellation stops the check's whole process group, removes the owned
  temporary directory and records `cancelled` — `TheLifecycleHolds`. The
  publication-window race is reproduced with real threads and real check
  processes in `tests/test_check_reuse.py`
  (`NewestRelevantAnswerDecides.test_a_rerun_reserved_after_the_read_but_before_the_reuse_wins`).
- **Old evidence stays readable, and visibly stale.** A record written
  under another runner revision reads `stale / runner-changed`, whether it
  has a result identity or is legacy, and is never rewritten —
  `OldEvidenceReadsRunnerChanged`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_check_reuse import ReuseCase  # noqa: E402
from tests.test_integration_gates import wait_for  # noqa: E402

import config  # noqa: E402
import evidence  # noqa: E402
import resultidentity  # noqa: E402
import staleness  # noqa: E402
import verify  # noqa: E402

CORE = Path(config.CORE)
RUNNER_MODULES = tuple(name[:-3] for name in resultidentity.RUNNER_FILES)


def core_modules() -> set[str]:
    return {p.stem for p in CORE.glob("*.py")}


def imports_of(module: str) -> set[str]:
    """Every core module `module` imports, at the top level or inside a
    function."""
    tree = ast.parse((CORE / f"{module}.py").read_text("utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found & core_modules()


def functions(module: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse((CORE / f"{module}.py").read_text("utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


# The functions a recorded verdict, its storage or its reading as still
# holding passes through. Each must be defined in a runner file.
VERDICT_PATH = {
    "verify": ("_from_exit", "_run_process", "_kill", "_execute", "_prepare",
               "_finish", "request", "_reusable", "_prospective",
               "_record_reuse", "cancel", "cancel_for_agent", "recover",
               "_execution_root", "_resolve_inputs", "_deliver_inputs"),
    "evidence": ("store", "read", "all_evidence", "for_task", "path_of",
                 "store_reuse"),
    "resultidentity": ("source_identity", "program_identity", "child_env",
                       "env_identity", "env_key", "identity", "result_id",
                       "coverage_id", "prepare_definition", "inputs_id",
                       "root_class", "comparable", "git", "runner_revision"),
    "staleness": ("freshness", "outcome_now", "identity_drift",
                  "equivalence", "accepted_now"),
}

# Where a scrubbed text is made for a reader. Nothing else may scrub.
DISPLAY = {("evidence", "scrub"), ("evidence", "log_text"),
           ("verify", "_clean_line"), ("verify", "_bench_words"),
           ("verify", "_diagnose")}


class RunnerRevisionHasOneOwner(unittest.TestCase):

    def copy(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in resultidentity.RUNNER_FILES:
            shutil.copy2(CORE / name, tmp / name)
        return tmp

    def test_the_revision_is_a_digest_over_the_declared_files(self):
        here = self.copy()
        self.assertEqual(resultidentity.runner_revision(here),
                         resultidentity.RUNNER_REVISION)
        for name in resultidentity.RUNNER_FILES:
            with self.subTest(file=name):
                moved = self.copy()
                (moved / name).write_bytes((moved / name).read_bytes() + b"\n")
                self.assertNotEqual(resultidentity.runner_revision(moved),
                                    resultidentity.RUNNER_REVISION,
                                    f"an edit to {name} must change the runner")
        gone = self.copy()
        (gone / resultidentity.RUNNER_FILES[-1]).unlink()
        self.assertIsNone(resultidentity.runner_revision(gone),
                          "an unreadable runner file is an unknown runner")

    def test_the_revision_is_no_longer_verify_py_alone(self):
        self.assertIn("verify.py", resultidentity.RUNNER_FILES)
        alone = hashlib.sha256((CORE / "verify.py").read_bytes()).hexdigest()
        self.assertNotEqual(resultidentity.RUNNER_REVISION, alone,
                            "one honest transition: the definition changed")
        for name in resultidentity.RUNNER_FILES:
            self.assertTrue((CORE / name).is_file(), name)

    def test_the_old_names_fail_loudly(self):
        self.assertFalse(hasattr(verify, "RUNNER_REVISION"))
        self.assertEqual(verify.MOVED["RUNNER_REVISION"], "resultidentity")
        self.assertEqual(verify.MOVED["_store"], "evidence")
        self.assertEqual(verify.MOVED["outcome_now"], "staleness")
        for name, owner in sorted(verify.MOVED.items()):
            with self.subTest(name=name):
                # the new owner holds it (a private name under its public
                # spelling where the move made it public)
                public = name.lstrip("_")
                owning = __import__(owner)
                self.assertTrue(hasattr(owning, name) or hasattr(owning, public)
                                or name in ("_SECRET_PATTERNS", "_identity",
                                            "_ID_OK"),
                                f"{owner} holds neither {name} nor {public}")
                with self.assertRaisesRegex(AttributeError, owner):
                    getattr(verify, name)
                with self.assertRaises(AttributeError):
                    mock.patch.object(verify, name, "x").start()
        with self.assertRaisesRegex(AttributeError, "no attribute"):
            verify.never_was_a_name  # noqa: B018

    def test_no_core_module_copies_the_revision(self):
        for path in sorted(CORE.glob("*.py")):
            if path.name == "resultidentity.py":
                continue
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and \
                        node.module == "resultidentity":
                    self.fail(f"{path.name} imports names from resultidentity "
                              f"and would copy them at import")
                if isinstance(node, ast.Name) and node.id == "RUNNER_REVISION":
                    self.fail(f"{path.name}:{node.lineno} reads a bare "
                              f"RUNNER_REVISION — ask resultidentity at call time")


class TheBoundaryIsAudited(unittest.TestCase):

    def test_every_import_of_a_runner_file_is_inside_or_bound_elsewhere(self):
        inside = set(RUNNER_MODULES)
        bound = set(resultidentity.BOUND_ELSEWHERE)
        self.assertFalse(inside & bound)
        seen = set()
        for module in RUNNER_MODULES:
            if module not in core_modules():
                continue
            for dependency in imports_of(module):
                seen.add(dependency)
                self.assertIn(dependency, inside | bound,
                              f"{module} imports {dependency}, which is neither "
                              f"a runner file nor bound elsewhere with a reason")
        self.assertEqual(bound - seen, set(),
                         "a BOUND_ELSEWHERE entry no runner file imports: "
                         "delete it")
        for reason in resultidentity.BOUND_ELSEWHERE.values():
            self.assertRegex(reason, r"^(re-derived|provenance only|"
                                     r"coordination only|display only):")

    def test_the_stdlib_leaves_inside_import_nothing_from_core(self):
        for module in ("records", "gitproc"):
            self.assertEqual(imports_of(module), set(), module)

    def test_the_verdict_path_lives_in_runner_files(self):
        for module, names in VERDICT_PATH.items():
            self.assertIn(f"{module}.py", resultidentity.RUNNER_FILES)
            defined = functions(module)
            for name in names:
                self.assertIn(name, defined, f"{module}.{name}")

    def test_redact_is_reached_for_display_only(self):
        for module in RUNNER_MODULES:
            if module not in core_modules():
                continue
            for name, fn in functions(module).items():
                for node in ast.walk(fn):
                    uses_redact = (isinstance(node, ast.Attribute)
                                   and isinstance(node.value, ast.Name)
                                   and node.value.id == "redact")
                    scrubs = (isinstance(node, ast.Call) and (
                        (isinstance(node.func, ast.Attribute)
                         and node.func.attr == "scrub")
                        or (isinstance(node.func, ast.Name)
                            and node.func.id == "scrub")))
                    if uses_redact:
                        self.assertEqual((module, name), ("evidence", "scrub"))
                    if scrubs:
                        self.assertIn((module, name), DISPLAY,
                                      f"{module}.{name} scrubs text")
                        self.assertNotIn(name, VERDICT_PATH.get(module, ()))


class GoldenDimensions(unittest.TestCase):
    """The identity dimensions for one fixed fixture, as the code before
    the move computed them (these literals were taken from `verify.py` at
    5af09ee and compared with this code on the same inputs). Only `runner`
    may differ, and does, by definition."""

    STEPS = [{"name": "deps", "executable": "/usr/bin/python3",
              "argv": ["-m", "pip", "install", "-e", "."], "network": True,
              "required": True}]
    INPUTS = [{"name": "brief", "contextId": "brief@" + "a" * 16}]
    KEY = b"k" * 32

    def fixture(self) -> dict:
        env = resultidentity.env_identity({"PATH": "/bin", "DB": "x"}, ["DB"],
                                          self.KEY)
        return resultidentity.identity(
            {"checkSubstance": "check:" + "b" * 16, "scope": "fast",
             "scopeArgs": ["-k", "fast"]},
            {"sourceId": "src:" + "c" * 16, "bound": True},
            {"programId": "prog:" + "d" * 16, "bound": True},
            self.STEPS, [{"programId": "prog:" + "e" * 16, "bound": True}],
            self.INPUTS, "fresh", env["envId"])

    def test_every_dimension_but_the_runner_is_unchanged(self):
        found = self.fixture()
        self.assertEqual(found["coverage"], "cov:7bdd953bf3e1d698")
        self.assertEqual(found["prepare"]["definition"], "prep:03308a6a765ea532")
        self.assertEqual(found["inputs"], "inputs:cf8ce39443657cef")
        self.assertEqual(found["env"], "env:bdcf4501ba08b32d")
        self.assertEqual(found["source"], "src:" + "c" * 16)
        self.assertEqual(found["check"], "check:" + "b" * 16)
        self.assertEqual(found["program"], "prog:" + "d" * 16)
        self.assertEqual(found["rootClass"], "fresh")
        self.assertEqual(found["v"], 1)
        self.assertEqual(resultidentity.key_id(self.KEY), "key:62900c4e3da8")
        self.assertEqual(found["runner"], resultidentity.RUNNER_REVISION)
        self.assertEqual(
            resultidentity.result_id({**found, "runner": "r" * 64}),
            "result:0ee42c5143bd3990")
        self.assertNotEqual(resultidentity.result_id(found),
                            resultidentity.result_id({**found, "runner": "r" * 64}))
        self.assertEqual(resultidentity.IDENTITY_DIMENSIONS,
                         ("source", "check", "coverage", "program", "prepare",
                          "inputs", "rootClass", "env", "runner"))


class MemosAreClearedByEveryOwner(unittest.TestCase):

    # Registries and counters: what is true now, not a memo of a reading.
    NOT_MEMOS = {("verify", "RUNNING"), ("verify", "_KEYS"),
                 ("verify", "_RESERVED"), ("verify", "_SLOTS"),
                 ("verify", "_SLOTS_FOR"), ("evidence", "_EPOCH"),
                 ("verify", "MOVED")}

    def test_forget_cached_clears_every_owner(self):
        staleness._FRESH["x"] = (time.time(), {})
        staleness._DRIFT["x"] = (time.time(), {})
        resultidentity._HASHED[("x",)] = "y"
        verify._BOARD = ((0,), frozenset(), {})
        verify._OFFERED = ((0,), ("unit",))
        verify.forget_cached()
        self.assertEqual(staleness._FRESH, {})
        self.assertEqual(staleness._DRIFT, {})
        self.assertEqual(resultidentity._HASHED, {})
        self.assertIsNone(verify._BOARD)
        self.assertIsNone(verify._OFFERED)

    def test_every_module_level_memo_has_a_forget(self):
        """A memo a module grows later must be cleared by `forget_cached`
        too, or a test — and a root changed under the board — reads what it
        read before."""
        for module in ("verify", "evidence", "resultidentity", "staleness"):
            tree = ast.parse((CORE / f"{module}.py").read_text("utf-8"))
            forgets = " ".join(
                ast.unparse(n) for n in tree.body
                if isinstance(n, ast.FunctionDef)
                and n.name in ("forget", "forget_cached"))
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else \
                    [node.target] if isinstance(node, ast.AnnAssign) else []
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    name = target.id
                    if not name.startswith("_") or name.isupper() is False and \
                            not name[1:].isupper():
                        continue
                    if (module, name) in self.NOT_MEMOS or name.startswith("__"):
                        continue
                    value = node.value
                    if isinstance(value, (ast.Dict, ast.Constant)) and (
                            not isinstance(value, ast.Constant)
                            or value.value is None):
                        with self.subTest(memo=f"{module}.{name}"):
                            self.assertIn(name, forgets)


class TheLifecycleHolds(ReuseCase):
    """Real check processes, real worker threads."""

    TREE = """#!{python}
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open("@PIDS@", "w").write(str(os.getpid()) + " " + str(child.pid))
print("working", flush=True)
time.sleep(600)
"""

    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:                                  # a zombie is not alive
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                return handle.read().split(")")[-1].split()[0] != "Z"
        except OSError:
            return False

    def test_a_duplicate_joins_and_a_cancel_stops_the_whole_group(self):
        pids = self.tmp / "pids"
        document = self.document(body=self.TREE.replace("@PIDS@", str(pids)))
        self.declares(document)
        self.manifest(agent="run-1")
        self.manifest(agent="run-2")
        self.fresh()
        first: dict = {}
        worker = threading.Thread(
            target=lambda: first.update(self.ask(agent="run-1")), daemon=True)
        worker.start()
        self.assertTrue(wait_for(lambda: pids.is_file()
                                 and len(pids.read_text().split()) == 2, 30))
        parent, child = (int(p) for p in pids.read_text().split())
        running = next(iter(verify.RUNNING))
        joined = self.ask(agent="run-2")
        self.assertTrue(joined.get("duplicate"), "the second request joined")
        self.assertEqual(joined["id"], running)
        tmp = verify._owned_tmp(evidence.read(running))
        self.assertIsNotNone(tmp)
        self.assertTrue(tmp.is_dir())
        verify.cancel(running)
        worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(first["outcome"], "cancelled")
        self.assertTrue(wait_for(lambda: not self.alive(parent)
                                 and not self.alive(child), 15),
                        "the check and its own child are both gone")
        self.assertFalse(tmp.exists(), "the owned temporary directory went")
        self.assertEqual(verify.RUNNING, {})
        self.assertIsNone(verify.reserved(first["task"], "unit", None))
        stored = evidence.read(running)
        self.assertEqual(stored["outcome"], "cancelled")
        self.assertEqual(len(self.evidence_files()), 1,
                         "one run, one record — the join wrote none")

    def test_reserved_is_a_copy_without_the_process(self):
        self.flag.write_text("hang", encoding="utf-8")
        document = self.counted()
        self.declares(document)
        self.manifest(agent="run-1")
        self.fresh()
        worker = threading.Thread(target=lambda: self.ask(agent="run-1"),
                                  daemon=True)
        worker.start()
        self.assertTrue(wait_for(lambda: self.runs() == 1, 30))
        task = next(iter(verify.RUNNING.values()))["task"]
        found = verify.reserved(task, "unit", None)
        self.assertEqual(found["outcome"], "running")
        self.assertNotIn("proc", found)
        found["outcome"] = "tampered"
        self.assertEqual(verify.reserved(task, "unit", None)["outcome"], "running")
        self.assertIsNone(verify.reserved(task, "unit", "fast"))
        verify.cancel(found["id"])
        worker.join(30)


class OldEvidenceReadsRunnerChanged(ReuseCase):

    def test_a_record_of_another_runner_reads_stale_and_is_not_rewritten(self):
        document = self.counted()
        first = self.run_check(document)
        self.assertEqual(first["outcome"], "passed")
        path = evidence.path_of(first["id"])
        record = json.loads(path.read_text(encoding="utf-8"))
        old = "f" * 64                                  # the old definition
        record["runner"]["revision"] = old
        record["identity"]["runner"] = old
        record["resultId"] = resultidentity.result_id(record["identity"])
        path.write_text(json.dumps(record), encoding="utf-8")
        before = path.read_bytes()
        self.fresh()
        reading = staleness.outcome_now(evidence.read(first["id"]))
        self.assertEqual(reading["outcome"], "stale")
        self.assertEqual(reading["reason"], "runner-changed")
        self.assertIn("run checks again", reading["why"])
        self.assertEqual(path.read_bytes(), before, "never rewritten")
        # and it is not reused: the next request runs once
        again = self.again(document, "run-2")
        self.assertNotIn("reused", again)
        self.assertEqual(self.runs(), 2)

    def test_a_legacy_record_reads_runner_changed_too(self):
        document = self.counted()
        first = self.run_check(document)
        path = evidence.path_of(first["id"])
        record = json.loads(path.read_text(encoding="utf-8"))
        for key in ("resultId", "identity"):
            record.pop(key, None)
        record["runner"]["revision"] = "f" * 64
        path.write_text(json.dumps(record), encoding="utf-8")
        self.fresh()
        fresh = staleness.freshness(evidence.read(first["id"]))
        self.assertEqual(fresh["rules"], "legacy")
        self.assertIn("runner-changed", fresh["reasons"])
        self.assertTrue(fresh["stale"])

    def test_readers_resolve_the_revision_at_call_time(self):
        document = self.counted()
        with mock.patch.object(resultidentity, "RUNNER_REVISION", "r-next"):
            found = self.run_check(document)
            self.assertEqual(found["runner"]["revision"], "r-next")
            self.assertEqual(found["identity"]["runner"], "r-next")
            self.assertEqual(
                staleness.outcome_now(evidence.read(found["id"]))["outcome"],
                "passed")
        self.fresh()
        reading = staleness.outcome_now(evidence.read(found["id"]))
        self.assertEqual((reading["outcome"], reading["reason"]),
                         ("stale", "runner-changed"))


if __name__ == "__main__":
    unittest.main()
