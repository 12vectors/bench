"""Atomic and durable records, `records` (task 110, WP03b).

What this holds:

- atomic visibility: concurrent writers (threads and separate processes)
  and readers on one record never observe a torn file — every read is
  one whole version some writer wrote;
- a failed replacement leaves the previous file byte for byte and no
  temporary file, and cleanup never deletes a file the call did not
  create (a caller-owned file that merely looks like a temporary one);
- a value that cannot be serialized or encoded fails before anything is
  created;
- `durable=True` flushes the file before the replace and the directory
  after it; the default does neither;
- an existing file's permission bits survive the replace;
- `read_json` is tolerant; `update_json` runs under the caller's lock;
- `InterprocessLock` nests within a thread, excludes other threads and
  excludes another process holding the same lock file.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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

import records  # noqa: E402


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir()
                  if p.name.endswith(records.TMP_SUFFIX))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-records-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class AtomicVisibility(Base):

    def payloads(self, n=6, size=200_000):
        # large enough that a non-atomic write is observable mid-way
        return [{"writer": i, "fill": chr(ord("a") + i) * size}
                for i in range(n)]

    def test_concurrent_thread_writers_and_readers_never_see_a_torn_file(self):
        path = self.tmp / "rec.json"
        versions = self.payloads()
        records.write_json(path, versions[0])
        stop = threading.Event()
        torn, seen = [], set()

        def reader():
            while not stop.is_set():
                raw = path.read_text(encoding="utf-8")
                try:
                    seen.add(json.loads(raw)["writer"])
                except ValueError:
                    torn.append(len(raw))

        def writer(value):
            for _ in range(15):
                records.write_json(path, value)

        readers = [threading.Thread(target=reader) for _ in range(3)]
        writers = [threading.Thread(target=writer, args=(v,)) for v in versions]
        for t in readers + writers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        for t in readers:
            t.join()
        self.assertEqual(torn, [], "a reader saw a partial file")
        self.assertIn(json.loads(path.read_text())["writer"], range(len(versions)))
        self.assertEqual(leftovers(self.tmp), [])

    def test_concurrent_process_writers_never_tear_and_never_collide(self):
        path = self.tmp / "rec.json"
        records.write_json(path, {"writer": -1, "fill": ""})
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); import records\n"
            "i = int(sys.argv[3])\n"
            "for _ in range(20):\n"
            "    records.write_json(sys.argv[2], {'writer': i, 'fill': chr(97+i)*150000})\n")
        procs = [subprocess.Popen([sys.executable, "-c", script, str(CORE),
                                   str(path), str(i)]) for i in range(4)]
        torn, seen = [], set()
        while any(p.poll() is None for p in procs):
            raw = path.read_text(encoding="utf-8")
            try:
                value = json.loads(raw)
            except ValueError:
                torn.append(len(raw))
                continue
            seen.add(value["writer"])
            self.assertEqual(value["fill"], chr(97 + value["writer"]) * 150000
                             if value["writer"] >= 0 else "")
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(torn, [])
        self.assertIn(json.loads(path.read_text())["writer"], range(4))
        self.assertEqual(leftovers(self.tmp), [],
                         "unique temporary names: no writer left another's file")

    def test_temporary_names_are_unique_hidden_and_not_records(self):
        path = self.tmp / "card.md"
        names = []
        real_replace = os.replace

        def spy(src, dst):
            names.append(Path(src).name)
            return real_replace(src, dst)

        with mock.patch.object(records.os, "replace", spy):
            for _ in range(20):
                records.write_text(path, "x")
        self.assertEqual(len(set(names)), 20)
        for name in names:
            self.assertTrue(name.startswith(".card.md."))
            self.assertTrue(name.endswith(".bench-tmp"))
            self.assertFalse(name.endswith((".md", ".json")))
        self.assertEqual(Path(os.path.dirname(path)), self.tmp)


class Failures(Base):

    def test_replacement_failure_keeps_the_previous_file_and_no_temporary(self):
        path = self.tmp / "rec.json"
        records.write_json(path, {"v": 1})
        before = path.read_bytes()
        with mock.patch.object(records.os, "replace",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                records.write_json(path, {"v": 2})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(leftovers(self.tmp), [])

    def test_cleanup_never_deletes_a_file_the_call_did_not_create(self):
        path = self.tmp / "rec.json"
        records.write_text(path, "old")
        # caller-owned files that look like this writer's temporaries
        theirs = [self.tmp / ".rec.json.0123456789ab.bench-tmp",
                  self.tmp / f"rec.json.bench-{os.getpid()}.tmp",
                  self.tmp / "rec.json.tmp"]
        for p in theirs:
            p.write_text("caller-owned")
        with mock.patch.object(records.os, "replace",
                               side_effect=OSError("refused")):
            with self.assertRaises(OSError):
                records.write_text(path, "new")
        records.write_text(path, "newer")              # and on success too
        for p in theirs:
            self.assertEqual(p.read_text(), "caller-owned", p.name)
        self.assertEqual(path.read_text(), "newer")
        self.assertEqual(sorted(leftovers(self.tmp)),
                         [".rec.json.0123456789ab.bench-tmp"])

    def test_a_name_collision_picks_another_temporary(self):
        path = self.tmp / "rec.json"
        taken = self.tmp / ".rec.json.aaaaaaaaaaaa.bench-tmp"
        taken.write_text("someone else's")
        tokens = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
        with mock.patch.object(records.secrets, "token_hex",
                               lambda n: next(tokens)):
            records.write_text(path, "mine")
        self.assertEqual(taken.read_text(), "someone else's")
        self.assertEqual(path.read_text(), "mine")

    def test_unserializable_or_unencodable_fails_before_anything_exists(self):
        path = self.tmp / "rec.json"
        with self.assertRaises(TypeError):
            records.write_json(path, {"x": object()})
        with self.assertRaises(UnicodeEncodeError):
            records.write_text(path, "\udcff")
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_an_interrupted_write_leaves_the_target_untouched(self):
        path = self.tmp / "rec.json"
        records.write_text(path, "whole")
        with mock.patch.object(records.os, "write",
                               side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                records.write_text(path, "partial")
        self.assertEqual(path.read_text(), "whole")
        self.assertEqual(leftovers(self.tmp), [])


class Durability(Base):

    def fsyncs(self, durable):
        path = self.tmp / "sub" / "rec.json"
        calls = []
        real = os.fsync

        def spy(fd):
            calls.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return real(fd)

        order = []
        real_replace = os.replace

        def replace(a, b):
            order.append(("replace", len(calls)))
            return real_replace(a, b)

        with mock.patch.object(records.os, "fsync", spy), \
                mock.patch.object(records.os, "replace", replace):
            records.write_json(path, {"a": 1}, durable=durable)
        return calls, order

    def test_durable_flushes_the_file_before_and_the_directory_after(self):
        calls, order = self.fsyncs(True)
        self.assertEqual(calls, [False, True])
        self.assertEqual(order, [("replace", 1)],
                         "the file is flushed before it is put in place")

    def test_the_default_is_atomic_without_a_flush(self):
        calls, _ = self.fsyncs(False)
        self.assertEqual(calls, [])


class Contents(Base):

    def test_formatting_matches_what_owners_always_wrote(self):
        path = self.tmp / "rec.json"
        value = {"b": 1, "a": [1, 2]}
        records.write_json(path, value)
        self.assertEqual(path.read_text(),
                         json.dumps(value, indent=2, sort_keys=True))
        records.write_json(path, value, indent=None, sort_keys=False)
        self.assertEqual(path.read_text(), json.dumps(value))
        records.write_json(path, value, newline=True)
        self.assertEqual(path.read_text(),
                         json.dumps(value, indent=2, sort_keys=True) + "\n")
        records.write_json(path, {"t": {1}}, default=str)
        self.assertEqual(json.loads(path.read_text()), {"t": "{1}"})

    def test_existing_permission_bits_survive_and_new_files_honour_umask(self):
        path = self.tmp / "script"
        path.write_text("#!/bin/sh\n")
        path.chmod(0o750)
        records.write_text(path, "#!/bin/sh\necho\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
        fresh = self.tmp / "fresh"
        fresh.write_text("reference")
        records.write_text(self.tmp / "new", "x")
        self.assertEqual(stat.S_IMODE((self.tmp / "new").stat().st_mode),
                         stat.S_IMODE(fresh.stat().st_mode))

    def test_read_json_is_tolerant(self):
        path = self.tmp / "rec.json"
        self.assertIsNone(records.read_json(path))
        self.assertEqual(records.read_json(path, {}), {})
        path.write_text("{not json")
        self.assertEqual(records.read_json(path, []), [])
        path.write_bytes(b"\xff\xfe")
        self.assertEqual(records.read_json(path, "d"), "d")
        path.write_text("[1]")
        self.assertEqual(records.read_json(path, {}, expect=dict), {})
        self.assertEqual(records.read_json(path, {}), [1])

    def test_update_json_runs_under_the_callers_lock(self):
        path = self.tmp / "count.json"
        lock = threading.Lock()

        def bump(value):
            self.assertTrue(lock.locked())
            return {"n": value["n"] + 1}

        def work():
            for _ in range(50):
                records.update_json(path, bump, lock, default={"n": 0})

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(records.read_json(path), {"n": 200})
        self.assertIsNone(records.update_json(path, lambda v: None, lock))
        self.assertEqual(records.read_json(path), {"n": 200})


HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import records
lock = records.InterprocessLock(sys.argv[2])
with lock:
    print("held", flush=True)
    sys.stdin.readline()
"""


class Interprocess(Base):

    def test_reentrant_within_a_thread_and_exclusive_across_threads(self):
        lock = records.InterprocessLock(lambda: self.tmp / "l" / "x.lock")
        got = []
        with lock:
            with lock:
                t = threading.Thread(target=lambda: (lock.acquire(),
                                                     got.append(1),
                                                     lock.release()))
                t.start()
                t.join(0.3)
                self.assertEqual(got, [], "another thread waited")
        t.join(5)
        self.assertEqual(got, [1])
        self.assertTrue((self.tmp / "l" / "x.lock").exists(),
                        "the lock file is never removed")
        with self.assertRaises(RuntimeError):
            lock.release()

    def test_the_local_half_touches_no_file(self):
        lock = records.InterprocessLock(self.tmp / "never" / "x.lock")
        with lock.local:
            with lock:                  # nests, then takes the file lock
                pass
            self.assertTrue((self.tmp / "never" / "x.lock").exists())
        other = records.InterprocessLock(self.tmp / "absent.lock")
        with other.local:
            pass
        self.assertFalse((self.tmp / "absent.lock").exists())

    def test_another_process_holding_it_excludes_this_one(self):
        where = self.tmp / "x.lock"
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(CORE),
                                   str(where)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        lock = records.InterprocessLock(where)
        got = threading.Event()
        t = threading.Thread(target=lambda: (lock.acquire(), got.set(),
                                             lock.release()))
        t.start()
        self.assertFalse(got.wait(0.5), "acquired while another process held it")
        with self.assertRaises(RuntimeError):
            lock.release()                    # not this thread's to release
        holder.stdin.write("\n")
        holder.stdin.flush()
        self.assertTrue(got.wait(10), "released by the other process")
        t.join()
        self.assertEqual(holder.wait(10), 0)
        with lock:                              # still usable afterwards
            pass


if __name__ == "__main__":
    unittest.main()
