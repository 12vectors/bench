"""Who writes records how (task 110, WP03b).

- **Revision comparison is not cross-process compare-and-swap.** The
  setup decisions have two supported writing processes on one machine:
  the board and `board.py --setup-guide` (the CLI calls exactly the
  functions the routes call). Two writers that read the same revision are
  paused right after their revision check. Under the interprocess lock
  the second cannot even reach its check until the first has written,
  and is then refused as stale; with the old in-process lock in its place
  (the control) both pass and the first decision is silently lost.
- **One writer owner inside the board.** Settings' policy apply and
  Settings → Agents' apply compare a revision and then write; two threads
  paused after the same comparison would both write. Each now holds its
  module's apply lock across the comparison and the write, so the second
  is refused as a conflict.
- **Every writer is atomic.** No owning module outside `verify.py` writes
  a record or a card in place any more; the remaining in-place writes are
  a reviewed list (logs, append-only journals, exclusive creates and
  disposable fixtures), held here so it can only shrink.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(REPO / "tests"))

from test_setup_guide import Project  # noqa: E402

WRITER = """
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import config, setupguide
if os.environ.get("CONTROL_OLD_LOCK"):
    import threading
    setupguide.LOCK = threading.RLock()     # what it was before task 110
real = setupguide._guard_revision
def paused(payload, read):
    real(payload, read)                     # the revision check passed …
    open(os.environ["MARK"], "w").close()   # … say so, and wait
    while not os.path.exists(os.environ["GO"]):
        time.sleep(0.02)
setupguide._guard_revision = paused
area = os.environ["AREA"]
try:
    R = {"ok": setupguide.decide({"area": area, "decision": "defer",
                                  "reason": "come back later",
                                  "expected": "none",
                                  "request": "req-" + area + "-0001"})["ok"]}
except setupguide.Refused as e:
    R = {"refused": str(e), "status": e.status, **e.extra}
print(json.dumps(R))
"""


class TwoSetupWriters(unittest.TestCase):

    def start(self, p, area, control):
        mark, go = p.dir / f"{area}.read", p.dir / f"{area}.go"
        env = dict(p.env, AREA=area, MARK=str(mark), GO=str(go))
        if control:
            env["CONTROL_OLD_LOCK"] = "1"
        proc = subprocess.Popen([sys.executable, "-c", WRITER, str(p.core)],
                                env=env, cwd=str(p.dir), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.kill)
        return proc, mark, go

    def wait_for(self, path, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            if path.exists():
                return True
            time.sleep(0.02)
        return False

    def finish(self, proc):
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err[-2000:])
        return json.loads(out.splitlines()[-1])

    def test_under_the_interprocess_lock_the_second_writer_is_refused(self):
        p = Project(self)
        a, a_read, a_go = self.start(p, "instructions", control=False)
        self.assertTrue(self.wait_for(a_read), "the first writer never checked")
        b, b_read, b_go = self.start(p, "preview", control=False)
        # the second writer read the same revision ("none") on its page; it
        # is now waiting for the lock, so it cannot pass its check yet
        self.assertFalse(self.wait_for(b_read, 1.5),
                         "the second writer checked while the first held the lock")
        b_go.touch()
        a_go.touch()
        self.assertEqual(self.finish(a), {"ok": True})
        refused = self.finish(b)
        self.assertEqual(refused["status"], 409, refused)
        self.assertTrue(refused.get("stale"), refused)
        self.assertFalse(b_read.exists(), "a refused writer never passed its check")
        decided = p.decisions()["decisions"]
        self.assertEqual(sorted(decided), ["instructions"])

    def test_control_revision_comparison_alone_loses_an_update(self):
        """The reason the lock exists: both pass the same comparison, both
        write, and the first decision disappears without a refusal."""
        p = Project(self)
        a, a_read, a_go = self.start(p, "instructions", control=True)
        b, b_read, b_go = self.start(p, "preview", control=True)
        self.assertTrue(self.wait_for(a_read) and self.wait_for(b_read))
        a_go.touch()
        self.assertEqual(self.finish(a), {"ok": True})
        b_go.touch()
        self.assertEqual(self.finish(b), {"ok": True})
        self.assertEqual(sorted(p.decisions()["decisions"]), ["preview"],
                         "the control shows the lost update the lock prevents")

    def test_the_lock_file_is_machine_local_and_never_in_the_work_root(self):
        p = Project(self)
        done = p.call("decide", {"area": "instructions", "decision": "defer",
                                 "reason": "later", "expected": "none",
                                 "request": "req-lock-0001"})
        self.assertIn("ok", done)
        self.assertTrue((p.state / "setup" / "decisions.lock").exists())
        work_files = {q.relative_to(p.work).as_posix()
                      for q in p.work.rglob("*") if q.is_file()
                      and ".git" not in q.relative_to(p.work).parts}
        self.assertNotIn("setup/decisions.lock", work_files)
        self.assertEqual({f for f in work_files if f.startswith("setup/")},
                         {"setup/decisions.json"})


class OneWriterOwnerInTheBoard(unittest.TestCase):
    """Two threads paused right after the same revision comparison."""

    def race(self, module, compare_name, call, conflict):
        real = getattr(module, compare_name)
        both_compared = threading.Barrier(2, timeout=2)
        results = []

        def paused(*a, **k):
            value = real(*a, **k)
            try:
                both_compared.wait()        # only possible without the lock
            except threading.BrokenBarrierError:
                pass
            return value

        def go(n):
            try:
                results.append(("ok", call(n)))
            except conflict as exc:
                results.append(("conflict", str(exc)))

        with mock.patch.object(module, compare_name, paused):
            threads = [threading.Thread(target=go, args=(n,)) for n in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        return sorted(kind for kind, _ in results)

    def test_settings_apply_refuses_the_second_of_two_racing_writers(self):
        import settings
        tmp = Path(tempfile.mkdtemp(prefix="bench-owner-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        doc = tmp / "profiles.json"
        doc.write_text(json.dumps({"v": 1}) + "\n")
        base = settings.revision_of(doc)
        with mock.patch.object(settings, "document_target", lambda: doc), \
                mock.patch.object(settings, "validate",
                                  lambda payload: {"ok": True, "errors": []}), \
                mock.patch.object(settings, "read_draft",
                                  lambda payload: payload["document"]), \
                mock.patch.object(settings, "_record_change",
                                  lambda *a, **k: {}), \
                mock.patch.object(settings, "draft", lambda: {}):
            kinds = self.race(
                settings, "revision_of",
                lambda n: settings.apply({"document": {"v": 1, "n": n},
                                          "revision": base}),
                settings.Conflict)
        self.assertEqual(kinds, ["conflict", "ok"])

    def test_agent_settings_apply_holds_its_lock_across_compare_and_write(self):
        import agentsettings
        seen = []
        real_revision = agentsettings.revision

        def spy():
            seen.append(agentsettings._APPLY_LOCK.locked())
            return real_revision()

        with mock.patch.object(agentsettings, "revision", spy), \
                mock.patch.object(agentsettings, "validate",
                                  lambda payload: {"ok": True, "errors": []}), \
                mock.patch.object(agentsettings, "_draft",
                                  lambda payload: ({}, {})), \
                mock.patch.object(agentsettings, "_changed_keys",
                                  lambda a, b: []):
            try:
                agentsettings.apply({"revision": "not-it"})
            except agentsettings.Conflict:
                pass
        self.assertTrue(seen and all(seen))


# In-place writes that remain, each for a reason: a log or a journal is
# appended to, a card or fixture is created exclusively or is disposable,
# or the file is not a record. (module, enclosing function) → why.
IN_PLACE = {
    ("agentsettings", "apply"): "backup copies under a fresh, run-unique "
                                "recovery directory, written before anything is replaced",
    ("agentsettings", "_record"): "append-only sanitized history (open 'a')",
    ("settings", "_record_change"): "append-only sanitized history (open 'a')",
    ("taskfiles", "append_to_task"): "append under WRITE_LOCK (open 'a')",
    ("state", "persist"): "append-only session JSONL (open 'a')",
    ("agents", "_launch"): "the run's own log stream (open 'wb')",
    ("commands", "run"): "the command's own log stream (open 'wb')",
    ("drive", "start"): "the drive's own log stream (open 'wb')",
    ("previews", "_prepare_run"): "a preparation step's own log stream (open 'wb')",
    ("doctor", "*"): "disposable doctor fixtures under a temporary directory",
    ("setupguide", "create_task"): "an exclusive create (O_EXCL) of a new card",
    ("setuptasks", "create"): "exclusive creates (O_EXCL) of new cards",
    ("profiles", "pin"): "a launch's read-only pinned copy, written before the "
                         "model starts and hashed after landing",
}


def in_place_writes() -> set[tuple[str, str]]:
    """Every `write_text`, `write_bytes`, `json.dump(` and `open(… 'w'/'a')`
    in core outside verify.py and records.py, by module and enclosing
    function."""
    found = set()
    for path in sorted(CORE.glob("*.py")):
        if path.stem in ("verify", "records"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[str] = []

        class Visit(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()
            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else \
                    f.id if isinstance(f, ast.Name) else ""
                hit = name in ("write_text", "write_bytes") or \
                    (name == "dump" and isinstance(f, ast.Attribute)
                     and getattr(f.value, "id", "") == "json")
                if name in ("open", "fdopen"):
                    modes = [a.value for a in node.args[1:2]
                             if isinstance(a, ast.Constant)]
                    modes += [k.value.value for k in node.keywords
                              if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    if not modes and isinstance(f, ast.Attribute) and node.args:
                        modes = [a.value for a in node.args[:1]
                                 if isinstance(a, ast.Constant)]
                    hit = any(isinstance(m, str) and ("w" in m or "a" in m)
                              for m in modes)
                if isinstance(f, ast.Attribute) and \
                        getattr(f.value, "id", "") == "records":
                    hit = False                 # the atomic path itself
                if hit:
                    found.add((path.stem, stack[0] if stack else "<module>"))
                self.generic_visit(node)

        Visit().visit(tree)
    return found


class NoRecordIsWrittenInPlace(unittest.TestCase):

    def test_the_remaining_in_place_writes_are_the_reviewed_list(self):
        found = {(m, "*") if m == "doctor" else (m, f)
                 for m, f in in_place_writes()}
        self.assertEqual(found - set(IN_PLACE), set(),
                         "a new in-place write: write it through records, or "
                         "review it into IN_PLACE with its reason")
        self.assertEqual(set(IN_PLACE) - found, set(),
                         "an in-place write disappeared: delete its entry so "
                         "the list only shrinks")


def durable_call_sites() -> set[str]:
    """`module.function` for every call passing `durable=True` (literally)
    in core outside records.py — the writers that attest or authorize."""
    found = set()
    for path in sorted(CORE.glob("*.py")):
        if path.stem == "records":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and any(
                        k.arg == "durable" and isinstance(k.value, ast.Constant)
                        and k.value.value is True for k in node.keywords):
                    found.add(f"{path.stem}.{fn.name}")
    return found


class DurableWritersAreListed(unittest.TestCase):

    def test_the_architecture_document_lists_every_durable_writer(self):
        text = (CORE / "ARCHITECTURE.md").read_text(encoding="utf-8")
        start = text.index("```records durable\n") + len("```records durable\n")
        listed = set(text[start:text.index("```", start)].split())
        self.assertEqual(listed, durable_call_sites())

    def test_the_attesting_families_are_among_them(self):
        """The plan's list (§6.2), outside verify.py: verdicts, overrides,
        the completion recovery record, delivered records, plans,
        manifests, setup decisions and the policy document."""
        self.assertLessEqual({"gates.record_verdict", "gates.override",
                              "completion._remember",
                              "completion._record_delivered", "plans.record",
                              "profiles.record", "setupguide._write_decisions",
                              "settings.apply"}, durable_call_sites())


if __name__ == "__main__":
    unittest.main()
