"""Task 127 — measure one board read, before and after the read scope.

Not part of the suite: the name does not match `test*.py`, so discovery
never runs it. Run one scenario by name, in the foreground:

    python3 -m unittest tests.boardperf_measure.Measure.test_rich_100

Each scenario writes its raw output to `develop/perf/127-<scenario>.json`
and prints a one-line summary per arm. `develop/perf/README.md` reads
them.

**The two arms.** `baseline` is `routes._state_payload()` called with no
read scope open, which is the read exactly as it was before task 127 —
the scope is the only thing the change added to the path. `optimized` is
`routes.state_payload()`, which opens it. Same interpreter, same process,
same fixture, so nothing but the scope differs.

**Order balance.** Samples alternate ABBA (baseline, optimized,
optimized, baseline, …), so a warm disk cache or a JIT-free interpreter's
first-call costs fall on both arms equally rather than on whichever ran
first.

**What each sample takes.**

- `cold` — every board memo dropped (`boardperf.forget_all`), then one
  read: a board's first read after a start.
- `warm` — the same read again immediately: the memos as a busy board
  has them.
- `expiry` — the clock moved 20 s on, past every memo's time bucket
  (`staleness.FRESH_TTL` 10 s, the gate and delivery buckets 15 s), then
  one read: what a board pays each time its buckets turn over. Done by
  offsetting `time.time`, so nothing actually waits.
- `concurrent` — memos dropped, then four reads started together on four
  threads: wall time and the slowest thread. Skipped for a baseline arm
  whose cold read exceeds `CONCURRENT_LIMIT` seconds, and said so.

Plus, once per arm, each section (`verify.board`, `gates.board`,
`delivery.board`) timed on its own from cold, and for the optimized arm
the read scope's own counts — how often each question was asked and how
often it was actually computed. And once per scenario, whether the two
arms' public payloads are equal but for the clock (`boardperf.comparable`).
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from tests import boardperf as bp

import profiles  # noqa: E402
import routes  # noqa: E402
import staleness  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "develop" / "perf"
CONCURRENT_LIMIT = 30.0
THREADS = 4
FRESH_TTL = staleness.FRESH_TTL
# rich-500 was measured with the slow baseline's warm read; at 1000 cards
# it would not fit one ten-minute run, and (as rich-500 shows) a read
# longer than the freshness bucket is never warm anyway
SKIP_SLOW_WARM = True


class Clock:
    """`time.time` moved on by `offset` seconds, everywhere."""

    def __init__(self):
        self.offset = 0.0
        self._real = time.time

    def __enter__(self):
        time.time = lambda: self._real() + self.offset
        return self

    def __exit__(self, *exc):
        time.time = self._real


def read(arm: str) -> dict:
    if arm == "baseline":
        reader = routes._state_payload
    else:
        reader = routes.state_payload
    with bp.GitCounter() as counter:
        started = time.perf_counter()
        payload = reader()
        took = time.perf_counter() - started
    return {"seconds": round(took, 4), "git": counter.count,
            "byVerb": dict(sorted(counter.by_verb.items())),
            "_payload": payload}


def strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_")}


def concurrent(arm: str) -> dict:
    bp.forget_all()
    reader = routes._state_payload if arm == "baseline" else routes.state_payload
    times: list[float] = []
    payloads: list[str] = []
    barrier = threading.Barrier(THREADS)
    lock = threading.Lock()

    def one():
        barrier.wait()
        started = time.perf_counter()
        payload = reader()
        took = time.perf_counter() - started
        with lock:
            times.append(took)
            payloads.append(bp.comparable(payload))

    workers = [threading.Thread(target=one) for _ in range(THREADS)]
    with bp.GitCounter() as counter:
        started = time.perf_counter()
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        wall = time.perf_counter() - started
    return {"threads": THREADS, "wall": round(wall, 4),
            "slowest": round(max(times), 4), "git": counter.count,
            "agree": len(set(payloads)) == 1}


def sections(arm: str) -> dict:
    bp.forget_all()
    collected = routes.taskfiles.collect()
    if arm == "baseline":
        return {k: {"seconds": round(v["seconds"], 4), "git": v["git"]}
                for k, v in bp.sections(collected).items()}
    with profiles.read_scope() as counts:
        found = bp.sections(collected)
        seen = counts()
    out = {k: {"seconds": round(v["seconds"], 4), "git": v["git"]}
           for k, v in found.items()}
    out["readScope"] = seen
    return out


def host() -> dict:
    def run(*argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "platform": platform.platform(), "cpus": os.cpu_count(),
            "git": run("git", "--version"),
            "commit": run("git", "-C", str(OUT.parents[1]), "rev-parse",
                          "--short=12", "HEAD"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def scenario(name: str, *, cards: int, rich: int, pairs: int) -> dict:
    board = bp.PerfBoard().open()
    try:
        started = time.perf_counter()
        made = board.build(cards=cards, rich=rich)
        built = time.perf_counter() - started
        order = []
        for n in range(pairs):
            order += (["baseline", "optimized"] if n % 2 == 0
                      else ["optimized", "baseline"])
        samples: dict[str, list[dict]] = {"baseline": [], "optimized": []}
        equal = None
        with Clock() as clock:
            for arm in order:
                bp.forget_all()
                cold = read(arm)
                slow = arm == "baseline" and cold["seconds"] > CONCURRENT_LIMIT
                sample = {"cold": strip(cold)}
                if slow and SKIP_SLOW_WARM:
                    sample["warm"] = {
                        "skipped": f"baseline cold read took "
                                   f"{cold['seconds']:.1f}s, longer than the "
                                   f"{FRESH_TTL:.0f}s freshness bucket, so a "
                                   f"second read is not warm; not repeated"}
                else:
                    sample["warm"] = strip(read(arm))
                clock.offset += 20.0
                if slow:
                    sample["expiry"] = {
                        "skipped": f"baseline cold read took "
                                   f"{cold['seconds']:.1f}s; an expiry read "
                                   f"repeats the same work and is not "
                                   f"repeated at this size"}
                else:
                    sample["expiry"] = strip(read(arm))
                if slow:
                    sample["concurrent"] = {
                        "skipped": f"baseline cold read took "
                                   f"{cold['seconds']:.1f}s, over the "
                                   f"{CONCURRENT_LIMIT:.0f}s limit; four at "
                                   f"once is impractical here"}
                else:
                    sample["concurrent"] = concurrent(arm)
                samples[arm].append(sample)
                sample["_cold_payload"] = bp.comparable(cold["_payload"])
            first = {arm: samples[arm][0]["_cold_payload"] for arm in samples}
            equal = first["baseline"] == first["optimized"]
            if not equal:
                a = json.loads(first["baseline"])
                b = json.loads(first["optimized"])
                differing = sorted(k for k in set(a) | set(b)
                                   if a.get(k) != b.get(k))
            else:
                differing = []
            slowest = max(s["cold"]["seconds"] for s in samples["baseline"])
            parts = {"optimized": sections("optimized")}
            parts["baseline"] = (sections("baseline")
                                 if slowest <= CONCURRENT_LIMIT else
                                 {"skipped": f"baseline cold read took "
                                             f"{slowest:.1f}s; sections not "
                                             f"timed again at this size"})
        for arm in samples:
            for sample in samples[arm]:
                sample.pop("_cold_payload", None)

        def summary(arm: str, kind: str) -> dict:
            values = [s[kind]["seconds"] for s in samples[arm] if kind in s
                      and "seconds" in s[kind]]
            gits = [s[kind]["git"] for s in samples[arm] if kind in s
                    and "git" in s[kind]]
            if not values:
                return {}
            return {"n": len(values), "median": round(statistics.median(values), 4),
                    "min": min(values), "max": max(values),
                    "gitMedian": statistics.median(gits) if gits else None}

        result = {
            "scenario": name, "host": host(), "fixture": made,
            "buildSeconds": round(built, 2), "order": order,
            "payloadsEqual": equal, "payloadDiffers": differing,
            "variableFields": list(bp.VARIABLE),
            "summary": {arm: {kind: summary(arm, kind)
                              for kind in ("cold", "warm", "expiry")}
                        for arm in samples},
            "sections": parts, "samples": samples,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"127-{name}.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        for arm in samples:
            print(name, arm, json.dumps(result["summary"][arm]))
        print(name, "payloadsEqual", equal, differing)
        return result
    finally:
        board.close()


class Measure(unittest.TestCase):

    def test_rich_100(self):
        scenario("rich-100", cards=100, rich=100, pairs=4)

    def test_rich_500(self):
        scenario("rich-500", cards=500, rich=500, pairs=1)

    def test_rich_1000(self):
        scenario("rich-1000", cards=1000, rich=1000, pairs=1)

    def test_light_100(self):
        scenario("light-100", cards=100, rich=10, pairs=4)

    def test_light_500(self):
        scenario("light-500", cards=500, rich=10, pairs=2)

    def test_light_1000(self):
        scenario("light-1000", cards=1000, rich=10, pairs=2)
