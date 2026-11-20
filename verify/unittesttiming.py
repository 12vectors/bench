#!/usr/bin/env python3
"""`python3 -m unittest` with a stopwatch: the same discovery, the same
runner and the same output, plus a JSON file saying where the time went.

    python3 verify/unittesttiming.py --timings out.json discover -s tests -t . -v

Everything after `--timings <file>` is handed to `unittest.main` exactly as
`python3 -m unittest` would hand it, so discovery, verbosity, the
`Ran N tests` line, the `OK`/`FAILED (...)` summary, skips and the exit
status are unittest's own. Nothing about a test's outcome is decided here;
this only reads the clock.

What is measured, all wall-clock `time.perf_counter()` seconds in this
process:

- **each test**, from `startTest` to `stopTest` — which in unittest is
  `setUp`, the test method, `tearDown` and the `addCleanup` callbacks;
- **each class fixture**: `setUpClass` (with `addClassCleanup`s that run
  on its failure) and `tearDownClass` + class cleanups, timed around
  `TestSuite._handleClassSetUp` / `_tearDownPreviousClass`;
- **each module fixture**: `setUpModule` / `tearDownModule`, timed around
  `TestSuite._handleModuleFixture` / `_handleModuleTearDown`;
- **discovery** (importing every test module) and the **run** as a whole.

`unattributed` is the run's elapsed time less every test and fixture: the
runner's own bookkeeping and printing. If those private suite hooks are
ever missing (another Python), fixtures are not timed, the file says
`"fixtures": "unavailable"`, and their time shows up as unattributed
rather than being guessed at.

The file is written when the run ends, whatever the outcome, and before
unittest's exit status is returned. A plain `python3 -m unittest` is
untouched by any of this: the collector exists only in this process.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import unittest
import unittest.suite

SCHEMA = "bench-unittest-timings/1"


class Clock:
    """Every reading of one run. Module-level so the suite hooks and the
    result class share it without either knowing about the other."""

    def __init__(self):
        self.tests: list[dict] = []
        self.fixtures: list[dict] = []
        self.hooked = False
        self.module_teardowns = 0.0

    def fixture(self, kind: str, owner: str, seconds: float) -> None:
        # fixture hooks fire for every class and module, including the
        # ones that define none; a zero-cost reading is kept so the totals
        # add up, but trivially short ones are folded on output
        self.fixtures.append({"kind": kind, "owner": owner, "seconds": seconds})


CLOCK = Clock()


def _class_name(cls) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _install_hooks() -> bool:
    S = unittest.suite.TestSuite
    names = ("_handleClassSetUp", "_tearDownPreviousClass",
             "_handleModuleFixture", "_handleModuleTearDown")
    if not all(hasattr(S, n) for n in names):
        return False
    orig = {n: getattr(S, n) for n in names}

    # Each hook is called before every test and returns at once unless the
    # class (or module) changed; only a real transition is a reading.
    def class_setup(self, test, result):
        cls = test.__class__
        if getattr(result, "_previousTestClass", None) is cls:
            return orig["_handleClassSetUp"](self, test, result)
        t = time.perf_counter()
        try:
            return orig["_handleClassSetUp"](self, test, result)
        finally:
            CLOCK.fixture("setUpClass", _class_name(cls), time.perf_counter() - t)

    def class_teardown(self, test, result):
        prev = getattr(result, "_previousTestClass", None)
        if prev is None or prev is test.__class__:
            return orig["_tearDownPreviousClass"](self, test, result)
        t = time.perf_counter()
        try:
            return orig["_tearDownPreviousClass"](self, test, result)
        finally:
            CLOCK.fixture("tearDownClass", _class_name(prev), time.perf_counter() - t)

    def module_setup(self, test, result):
        prev = getattr(result, "_previousTestClass", None)
        module = test.__class__.__module__
        if prev is not None and prev.__module__ == module:
            return orig["_handleModuleFixture"](self, test, result)
        # unittest tears the previous module down from inside this hook;
        # that reading is the teardown hook's, so it is taken back out here
        t, nested = time.perf_counter(), CLOCK.module_teardowns
        try:
            return orig["_handleModuleFixture"](self, test, result)
        finally:
            CLOCK.fixture("setUpModule", module, time.perf_counter() - t
                          - (CLOCK.module_teardowns - nested))

    def module_teardown(self, result):
        prev = getattr(result, "_previousTestClass", None)
        t = time.perf_counter()
        try:
            return orig["_handleModuleTearDown"](self, result)
        finally:
            if prev is not None:
                seconds = time.perf_counter() - t
                CLOCK.module_teardowns += seconds
                CLOCK.fixture("tearDownModule", prev.__module__, seconds)

    S._handleClassSetUp = class_setup
    S._tearDownPreviousClass = class_teardown
    S._handleModuleFixture = module_setup
    S._handleModuleTearDown = module_teardown
    return True


class TimedResult(unittest.TextTestResult):
    """unittest's own text result, reading the clock at start and stop.
    Outcomes are recorded by the parent class exactly as they would be."""

    def startTest(self, test):
        self._t0 = time.perf_counter()
        self._outcome = "passed"
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        seconds = time.perf_counter() - getattr(self, "_t0", time.perf_counter())
        cls = test.__class__
        CLOCK.tests.append({"id": test.id(), "class": _class_name(cls),
                            "module": cls.__module__, "seconds": seconds,
                            "outcome": self._outcome})

    def _mark(self, word):
        self._outcome = word

    def addError(self, test, err):
        self._mark("error")
        super().addError(test, err)

    def addFailure(self, test, err):
        self._mark("failure")
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self._mark("skipped")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._mark("expected-failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._mark("unexpected-success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._mark("failure" if issubclass(err[0], test.failureException) else "error")
        super().addSubTest(test, subtest, err)


def summarise(clock: Clock, discovery: float, run: float, elapsed: float) -> dict:
    tests = sum(t["seconds"] for t in clock.tests)
    fixtures = sum(f["seconds"] for f in clock.fixtures)
    classes: dict[str, dict] = {}
    for t in clock.tests:
        c = classes.setdefault(t["class"], {"class": t["class"], "module": t["module"],
                                            "tests": 0, "testSeconds": 0.0,
                                            "fixtureSeconds": 0.0})
        c["tests"] += 1
        c["testSeconds"] += t["seconds"]
    modules: dict[str, dict] = {}
    for f in clock.fixtures:
        if f["kind"] in ("setUpClass", "tearDownClass"):
            c = classes.setdefault(f["owner"], {"class": f["owner"],
                                                "module": f["owner"].rsplit(".", 1)[0],
                                                "tests": 0, "testSeconds": 0.0,
                                                "fixtureSeconds": 0.0})
            c["fixtureSeconds"] += f["seconds"]
    for c in classes.values():
        c["seconds"] = c["testSeconds"] + c["fixtureSeconds"]
        m = modules.setdefault(c["module"], {"module": c["module"], "tests": 0,
                                             "seconds": 0.0})
        m["tests"] += c["tests"]
        m["seconds"] += c["seconds"]
    for f in clock.fixtures:
        if f["kind"] in ("setUpModule", "tearDownModule"):
            m = modules.setdefault(f["owner"], {"module": f["owner"], "tests": 0,
                                                "seconds": 0.0})
            m["seconds"] += f["seconds"]
    return {
        "seconds": {"elapsed": elapsed, "discovery": discovery, "run": run,
                    "tests": tests, "fixtures": fixtures,
                    "unattributed": max(0.0, run - tests - fixtures)},
        "counts": {"tests": len(clock.tests),
                   **{o: sum(1 for t in clock.tests if t["outcome"] == o)
                      for o in sorted({t["outcome"] for t in clock.tests})}},
        "classes": sorted(classes.values(), key=lambda c: -c["seconds"]),
        "modules": sorted(modules.values(), key=lambda m: -m["seconds"]),
    }


def calibrate(n: int = 2000) -> float:
    """What the collector adds to one test, measured: the clock readings,
    the id and class name, and the record the result keeps — the work
    TimedResult does beyond unittest's own, repeated `n` times on a test
    that is never run. Seconds per test."""

    class Probe(unittest.TestCase):
        def runTest(self):
            pass

    probe, sink = Probe(), []
    t = time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        seconds = time.perf_counter() - t0
        cls = probe.__class__
        sink.append({"id": probe.id(), "class": _class_name(cls),
                     "module": cls.__module__, "seconds": seconds, "outcome": "passed"})
    return (time.perf_counter() - t) / n


def write(path: str, clock: Clock, discovery: float, run: float, elapsed: float,
          argv: list[str], exit_status) -> None:
    per_test = calibrate()
    w0 = time.perf_counter()
    doc = {"schema": SCHEMA,
           "runtime": {"python": platform.python_version(),
                       "implementation": platform.python_implementation(),
                       "executable": sys.executable,
                       "platform": platform.platform(),
                       "cpus": os.cpu_count(),
                       "cwd": os.getcwd(), "argv": argv,
                       "clock": "time.perf_counter, wall seconds in the test process"},
           "fixtureTiming": "measured" if clock.hooked else "unavailable",
           "exitStatus": exit_status,
           **summarise(clock, discovery, run, elapsed),
           "tests": clock.tests,
           "fixtures": [f for f in clock.fixtures if f["seconds"] >= 0.0005]}
    # Measured, not assumed: the per-test cost above times the tests run,
    # and how long summarising took (writing comes after the run ends, so
    # it is outside every number in `seconds`)
    doc["overhead"] = {"perTestSeconds": per_test,
                       "testsTotalSeconds": per_test * len(clock.tests),
                       "summariseSeconds": time.perf_counter() - w0,
                       "how": "calibrate(): the collector's own per-test work "
                              "repeated 2000 times in this process"}
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--timings":
        print("usage: unittesttiming.py --timings <file> <unittest arguments…>",
              file=sys.stderr)
        return 2
    path, rest = os.path.abspath(argv[1]), argv[2:]
    # as `python -m unittest` does: the working directory is importable,
    # this script's own directory is not
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    sys.path.insert(0, "")
    CLOCK.hooked = _install_hooks()
    marks = {"start": time.perf_counter()}

    class TimedRunner(unittest.TextTestRunner):
        resultclass = TimedResult

        def run(self, test):
            marks["run"] = time.perf_counter()
            try:
                return super().run(test)
            finally:
                marks["end"] = time.perf_counter()

    status = None
    try:
        unittest.main(module=None, argv=["python -m unittest", *rest],
                      testRunner=TimedRunner)
    except SystemExit as exc:
        status = exc.code
        raise
    finally:
        end = marks.get("end", time.perf_counter())
        run_at = marks.get("run", end)
        write(path, CLOCK, run_at - marks["start"], end - run_at,
              end - marks["start"], rest, status)
    return 0  # unittest.main always exits; this is not reached


if __name__ == "__main__":
    sys.exit(main())
