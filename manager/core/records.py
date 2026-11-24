"""Atomic and durable records: one way to write a file whole (task 110, WP03b).

A stdlib leaf. It imports no core module and takes every path as an
argument, so it knows nothing about which record is which: the module
that owns a record family decides where it lives, which lock guards its
read-modify-write, and what a failure means. This module only promises
how the bytes reach the disk.

Two guarantees, and they are different properties:

- **Atomic visibility** (every write). The content goes to a temporary
  file with a unique name in the *same directory* — created exclusively,
  so no two writers ever share one — and `os.replace` puts it in place.
  A reader sees the old file or the new one, never a torn one, and two
  writers racing on one record leave one whole version, never a mix.
- **Durability** (`durable=True`). The temporary file is flushed and
  `fsync`ed before the replace, and the directory after it, so the new
  content survives a power failure as well. A replace without the flush
  can lose the new content, while still never exposing a torn file. Used
  for records that attest or authorize (ARCHITECTURE.md lists them).

Cleanup removes only the temporary file this call created. A failed
write never unlinks the target or any file it did not make itself, so a
caller-owned file that merely looks like a temporary one is left alone.

The temporary name is `.<name>.<random>.bench-tmp`: hidden, and never
ending in `.md` or `.json`, so a stage watcher or a `*.json` listing
never mistakes a write in flight for a record.

What this module does *not* provide is cross-process compare-and-swap.
Comparing a revision and then writing is two steps; another process can
write between them. A record family with more than one writing process
holds an `InterprocessLock` across the read, the comparison and the
write, or routes every write through one owning process.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
from pathlib import Path

TMP_SUFFIX = ".bench-tmp"


def _temporary(path: Path) -> tuple[int, Path]:
    """Create a fresh temporary file beside `path`, exclusively. Its mode
    honours the umask exactly as `Path.write_text` would for a new file."""
    for _ in range(100):
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}{TMP_SUFFIX}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        return fd, tmp
    raise FileExistsError(f"no free temporary name beside {path}")


def _sync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sync_directory(directory) -> None:
    """Flush a directory's entries (a rename into it) to disk, as far as
    the platform allows. Never raises."""
    _sync_dir(Path(directory))


def write_bytes(path, data: bytes, *, durable: bool = False,
                mkdir: bool = True) -> None:
    """Replace `path` with `data` atomically; with `durable`, also on disk.

    An existing file's permission bits are kept, as an in-place write
    would have kept them. The named path itself is replaced: a symbolic
    link there is replaced by a file, not written through — callers that
    must not do either refuse links before calling. Raises `OSError` on
    failure, with the previous file intact and no temporary file left.
    """
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _temporary(path)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            try:
                mode = os.stat(path, follow_symlinks=False).st_mode
            except FileNotFoundError:
                mode = None
            if mode is not None and not os.path.islink(path):
                os.fchmod(fd, mode & 0o7777)
            if durable:
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    if durable:
        _sync_dir(path.parent)


def write_text(path, text: str, *, durable: bool = False,
               encoding: str = "utf-8", mkdir: bool = True) -> None:
    """`write_bytes` for text. Encoding happens first, so text that cannot
    be encoded fails before anything is created."""
    write_bytes(path, text.encode(encoding), durable=durable, mkdir=mkdir)


def dumps(value, *, indent: int | None = 2, sort_keys: bool = True,
          default=None, newline: bool = False) -> str:
    text = json.dumps(value, indent=indent, sort_keys=sort_keys,
                      default=default)
    return text + "\n" if newline else text


def write_json(path, value, *, durable: bool = False,
               indent: int | None = 2, sort_keys: bool = True,
               default=None, newline: bool = False,
               mkdir: bool = True) -> None:
    """Serialize, then write atomically. A value that cannot be serialized
    raises before any file is touched. The formatting options exist so a
    record keeps exactly the bytes its owner always wrote."""
    write_text(path, dumps(value, indent=indent, sort_keys=sort_keys,
                           default=default, newline=newline),
               durable=durable, mkdir=mkdir)


def read_json(path, default=None, *, expect: type | None = None):
    """The parsed record, or `default` when it is missing, unreadable, not
    JSON, or (with `expect`) not of that type. Never raises for a record
    that is simply not there or not whole."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return default
    if expect is not None and not isinstance(data, expect):
        return default
    return data


def update_json(path, fn, lock, *, default=None, expect: type | None = None,
                durable: bool = False, **dumps_kwargs):
    """Read-modify-write under `lock`, which the owning module supplies and
    owns. `fn` receives the current value (or `default`) and returns the
    new one; returning None writes nothing. Returns what was written, or
    None. The lock is what makes this safe — this function adds none."""
    with lock:
        current = read_json(path, default, expect=expect)
        updated = fn(current)
        if updated is None:
            return None
        write_json(path, updated, durable=durable, **dumps_kwargs)
        return updated


class InterprocessLock:
    """A lock held across threads *and* processes on one machine.

    Within a process it is a reentrant lock, so it nests like the
    `threading.RLock` it usually replaces. The outermost acquisition also
    takes an exclusive `flock` on a lock file, which every other process
    using the same lock file waits for. `where` is the lock file's path, or
    a callable returning it, read at each outermost acquisition so a path
    configured after import is honoured. The lock file is never removed:
    removing it would let two processes lock two different files.
    """

    def __init__(self, where):
        self._where = where
        self._local = threading.RLock()
        self._depth = 0
        self._fd: int | None = None
        self._owner: int | None = None

    @property
    def local(self) -> threading.RLock:
        """The in-process half alone: excludes this process's writers and
        touches no file. For a reader that must not write anything — the
        record it reads is replaced atomically, so another process can
        never show it a torn one."""
        return self._local

    def path(self) -> Path:
        return Path(self._where() if callable(self._where) else self._where)

    def acquire(self) -> bool:
        self._local.acquire()
        try:
            if self._depth == 0:
                target = self.path()
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self._fd = fd
                self._owner = threading.get_ident()
            self._depth += 1
        except BaseException:
            self._local.release()
            raise
        return True

    def release(self) -> None:
        if self._depth <= 0 or self._owner != threading.get_ident():
            raise RuntimeError("release of an InterprocessLock this thread "
                               "does not hold")
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            fd, self._fd, self._owner = self._fd, None, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self._local.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
