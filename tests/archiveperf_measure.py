"""Task 128 — measure one archive end to end, before and after.

Not part of the suite: the name does not match `test*.py`, so discovery
never runs it. Run one scenario by name, in the foreground:

    python3 -m unittest tests.archiveperf_measure.Measure.test_rich_100

Each scenario writes its raw output to `develop/perf/128-<scenario>.json`
and prints one summary line per arm and cache state.
`develop/perf/README.md` reads them.

**The fixture.** Task 127's evidence-rich board (`tests/boardperf.py`):
real profile document, real check executables, a real application
checkout with a branch per card and evidence records on each. Here its
work root's `tasks/` is committed and `BOARD_COMMIT_MOVES` is on, so
every archive is a real `git commit` in the work-state repository. The
board is served by the real `httpd.Handler` on a loopback port, and a
real `/api/stream` connection receives the frames. Nothing reads or
writes a live board: every card archived is a disposable fixture card.

**The two arms are the two pages.** The server is the same in both — the
reply's extra fields cost nothing measurable — and what differs is what
the page does with an archive, which is driven here over HTTP exactly as
the page's own code does it:

- `before` — the page as it was: on the archive's `board` frame it reads
  the state (read A); the action awaits the POST, toasts, then
  `await loadState()`, which queues behind A as the follow-up (read B).
  The card leaves the page when A is painted (A was sent after the write,
  so it holds it); the action ends when B is.
- `after` — this card's page: the card leaves when the POST answers; the
  frame and the confirmation share one reading (whichever arrives first
  asks, `boardChanged`), and the board is reconciled when that reading
  lands.

**What each sample records**, in seconds from sending the POST:
`post` (the reply arrived), `gone` (the card left the page), `reconciled`
(a reading holding the archive has arrived) and `actionDone` (the archive
action's promise settled — the chip is free again), plus `stateReads`
(whole-board readings this archive caused) and `frameBeforeReply`.

**Cache states.** `warm` — a state read immediately before the archive,
so every memo is as a busy board has it. `expired` — the clock moved 20 s
on first, past every memo's time bucket (as task 127's `expiry`), by
offsetting `time.time`, so nothing actually waits.

**Order.** Arms alternate ABBA within each cache state.

Not modelled: the watcher thread is not running, so the later frame it
sends when it notices the archive on disk (one more reading in either
arm, see the README) is not in these numbers.
"""

from __future__ import annotations

import http.client
import json
import platform
import queue
import statistics
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests import boardperf as bp
from tests.boardperf_measure import Clock

import config  # noqa: E402
import httpd  # noqa: E402
import state  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "develop" / "perf"
EXPIRE = 20.0


class Served:
    """The board on a loopback port, with one stream connection whose
    frames are timestamped as they arrive."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), httpd.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.frames: queue.Queue = queue.Queue()
        self._stream = http.client.HTTPConnection("127.0.0.1", self.port, timeout=None)
        self._stream.request("GET", "/api/stream")
        self._resp = self._stream.getresponse()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in self._resp:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("data: "):
                    self.frames.put((time.perf_counter(), json.loads(line[6:])))
        except (OSError, ValueError):
            pass

    def close(self):
        try:
            self._stream.close()
        finally:
            self.server.shutdown()
            self.server.server_close()

    def get_state(self) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        try:
            conn.request("GET", "/api/state")
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        try:
            conn.request("POST", path, body=json.dumps(body),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        finally:
            conn.close()

    def drain(self):
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def board_frame(self, timeout: float = 30.0) -> tuple[float, dict]:
        end = time.perf_counter() + timeout
        while True:
            at, msg = self.frames.get(timeout=max(0.01, end - time.perf_counter()))
            if msg.get("type") == "board":
                return at, msg


def holds(reading: dict, file: str) -> bool:
    return any(t["file"] == file for s in reading["board"]["stages"] for t in s["tasks"])


def archive_before(srv: Served, file: str) -> dict:
    """The page as it was: frame → read A; POST → await, then a follow-up
    read B queued behind A. Gone when A lands; done when B does."""
    srv.drain()
    reads = {"n": 0}
    a_done: dict = {}

    def read_a():
        at, _ = srv.board_frame()
        a_done["frame"] = at
        reads["n"] += 1
        reading = srv.get_state()
        a_done["at"] = time.perf_counter()
        a_done["holds"] = not holds(reading, file)

    reader = threading.Thread(target=read_a)
    t0 = time.perf_counter()
    reader.start()
    status, reply = srv.post("/api/archive", {"file": file, "from": "backlog"})
    t_post = time.perf_counter()
    assert status == 200, reply
    reader.join()                       # B is queued behind A
    reads["n"] += 1
    reading = srv.get_state()
    t_b = time.perf_counter()
    assert not holds(reading, file)
    assert a_done["holds"], "read A was sent after the write and holds it"
    return {"post": t_post - t0, "gone": a_done["at"] - t0,
            "reconciled": a_done["at"] - t0, "actionDone": t_b - t0,
            "stateReads": reads["n"], "frameBeforeReply": a_done["frame"] < t_post,
            "committed": reply.get("committed")}


def archive_after(srv: Served, file: str) -> dict:
    """This card's page: gone on the reply; one reading, asked by whichever
    of the frame and the reply arrives first, reconciles."""
    srv.drain()
    t0 = time.perf_counter()
    status, reply = srv.post("/api/archive", {"file": file, "from": "backlog"})
    t_post = time.perf_counter()
    assert status == 200, reply
    frame_at, frame = srv.board_frame()
    assert frame["rev"] == reply["rev"]
    # the page asks once, at min(frame, reply); the other finds that
    # reading's floor at or above its revision and asks nothing
    reading = srv.get_state()
    t_r = time.perf_counter()
    assert reading["boardRev"] >= reply["rev"] and not holds(reading, file)
    return {"post": t_post - t0, "gone": t_post - t0, "reconciled": t_r - t0,
            "actionDone": t_post - t0, "stateReads": 1,
            "frameBeforeReply": frame_at < t_post,
            "committed": reply.get("committed")}


ARMS = {"before": archive_before, "after": archive_after}


def summary(samples: list[dict]) -> dict:
    keys = ("post", "gone", "reconciled", "actionDone")
    out = {k: round(statistics.median(s[k] for s in samples), 4) for k in keys}
    out["stateReads"] = sorted({s["stateReads"] for s in samples})
    out["n"] = len(samples)
    return out


def host() -> dict:
    def run(*argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"
    return {"python": platform.python_version(), "platform": platform.platform(),
            "cpus": __import__("os").cpu_count(), "git": run("git", "--version")}


def scenario(name: str, *, cards: int, rich: int, pairs: int) -> dict:
    real_broadcast = state.broadcast
    board = bp.PerfBoard().open()
    # GateCase records frames instead of sending them; this harness needs
    # the real fan-out to the stream (its cleanup puts the recorder back)
    state.broadcast = real_broadcast
    saved = config.COMMIT_MOVES
    srv = None
    try:
        made = board.build(cards=cards, rich=rich)
        bp.git(board.work, "add", "-A", "tasks")
        bp.git(board.work, "commit", "-q", "-m", "fixture cards")
        config.COMMIT_MOVES = True
        backlog = sorted(p.name for p in (board.tasks / "backlog").glob("*.md"))
        need = 2 * 2 * 2 * pairs
        if len(backlog) < need:
            raise RuntimeError(f"{len(backlog)} backlog cards, {need} needed")
        srv = Served()
        srv.get_state()                 # the board's first read, off the clock
        order = ["before", "after", "after", "before"] * pairs
        samples: dict = {}
        with Clock() as clock:
            for cache in ("warm", "expired"):
                samples[cache] = {arm: [] for arm in ARMS}
                for arm in order:
                    if cache == "warm":
                        srv.get_state()
                    else:
                        clock.offset += EXPIRE
                    file = backlog.pop(0)
                    sample = ARMS[arm](srv, file)
                    sample["file"] = file
                    samples[cache][arm].append(sample)
                    head = bp.git(board.work, "log", "-1", "--pretty=%s").stdout.strip()
                    sample["commit"] = head
                    if not head.startswith("board: "):
                        raise RuntimeError(f"the archive did not commit: {head}")
        result = {
            "task": 128, "scenario": name, "host": host(), "fixture": made,
            "order": order, "expireSeconds": EXPIRE,
            "summary": {cache: {arm: summary(s) for arm, s in arms.items()}
                        for cache, arms in samples.items()},
            "samples": samples,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"128-{name}.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        for cache, arms in result["summary"].items():
            for arm, s in arms.items():
                print(name, cache, arm, json.dumps(s))
        return result
    finally:
        config.COMMIT_MOVES = saved
        if srv:
            srv.close()
        board.close()


class Measure(unittest.TestCase):

    def test_rich_100(self):
        scenario("rich-100", cards=100, rich=100, pairs=2)

    def test_light_100(self):
        scenario("light-100", cards=100, rich=10, pairs=2)


if __name__ == "__main__":
    sys.exit(unittest.main())
