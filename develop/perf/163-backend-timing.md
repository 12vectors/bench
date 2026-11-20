# Task 163 — where the backend suite's time goes

How the numbers were collected is in `verify/README.md`, "Where the
backend suite's time goes". Everything below is labelled either
**measured** (a reading with its source) or **hypothesis** (not measured).

## Baseline (measured)

`bench-backend` at `464f1fada42f`, run by bench in a fresh source checkout,
evidence `ev:verification@1ec92f011698`. The ranking it printed is kept
verbatim in [`163-baseline-464f1fa.txt`](163-baseline-464f1fa.txt). The
whole log and `timings.json` are in that evidence's artifacts.

| reading | seconds |
| --- | --- |
| elapsed | 1568.5 |
| discovery | 1.3 |
| tests (`setUp` + method + `tearDown` + cleanups) | 1531.2 |
| class and module fixtures | 35.9 |
| unattributed (the runner's own bookkeeping) | 0.2 |
| collector overhead | 0.8µs per test, measured in-process |

4657 tests ran, none skipped. The mean test is 0.33s. Fixtures are 2% of
the run, so **repeated class/module setup is not the dominant cost**. The
time is inside tests.

The eight slowest single tests took 393.8s, 25% of the run:

| test | s | cause |
| --- | --- | --- |
| `test_gate_compare … same_resolver_twice…` | 86.6 | see "Environment-dependent" below |
| `test_gate_invalid_wording … sheet_never_says_absent…` | 70.9 | see "Environment-dependent" below |
| `test_required_checks.OnlyMissingRequired.test_unavailable_check…` | 60.4 | **measured**: waits out `VERIFY_TIMEOUT` (60s) |
| `test_required_checks.OnlyMissingRequired.test_requests_are_a_persons…` | 60.4 | **measured**: the same |
| `test_gate_invalid_wording … panel_draws…` | 20.7 | see "Environment-dependent" below |
| three `test_orchestration_journey.Vendored` journeys | 17.5–19.5 | real lifecycle journeys (git, bare origin, adapter `run`), not waits |

## The improvement made (measured)

`OnlyMissingRequired.setUp` registers a stub check `slow` whose flag is
`hang` (it sleeps 600s). Two of its five tests join a run of `slow` that is
in flight, so they need it hanging. The other two ran a batch that
*requested* `slow` itself, and the batch then waited out `GateCase`'s
`VERIFY_TIMEOUT=60.0` before it recorded an error and moved on. Neither
test asserts anything about `slow`. They now set its flag to `pass` first
(`not_hanging()`). Their assertions are unchanged, the two join tests still
hang it, and no timeout value changed.

Same shell, same machine, `python3 -m unittest
tests.test_required_checks.OnlyMissingRequired`:

| | runs | seconds per class run |
| --- | --- | --- |
| before (`464f1fa`) | 1 | 122.2 |
| after (`a928f54`) | 3, in one process | 5.47 total, so about 1.8 each |

## Environment-dependent hotspots (measured gap, cause is a hypothesis)

The `gate_compare` and `gate_invalid_wording` classes are slow under
bench's runner and fast in an ordinary shell on the same machine:

| class | bench runner (baseline) | plain `python3 -m unittest` in the worktree |
| --- | --- | --- |
| `test_gate_compare` (31 tests) | 119.2s for 8 of them | 5.1s for all 31 |
| `test_gate_invalid_wording` (5 tests) | 92.5s | 0.37s |

So the cost is not in the tests' own logic. **Hypothesis, not measured:**
the directed environment bench gives a check (`verify/benchverify.py`: a
fresh `HOME`, XDG and cache directories, `PYTHONDONTWRITEBYTECODE=1`, a
fresh source checkout with no `__pycache__`) makes the subprocesses these
tests start (`verify/gate-compare` re-importing the whole of
`manager/core`, `node` started by `node -e`) pay start-up costs that a warm
shell does not. Other load on the machine during the run is the other
candidate. This run could not set environment variables to separate the
two (its command grant is `python3 -m unittest`), so that is left as the
next measurement. It needs the same command under the same environment
as the runner.

## Comparison (measured)

Both runs used `bench-backend` on the same machine with the same runner
and environment, the same 4657 test ids, all passing, none skipped:

| | baseline `464f1fa` | candidate `ab83b88` |
| --- | --- | --- |
| evidence | `ev:verification@1ec92f011698` | `ev:verification@15524bacda14` |
| check took | 1573.0s | 1565.9s |
| elapsed (collector) | 1568.5s | 1561.4s |
| tests / fixtures | 1531.2s / 35.9s | 1527.3s / 32.7s |
| `OnlyMissingRequired` | 121.8s | under 7.7s (no longer in the top 15) |
| collector overhead | 0.8µs/test | 0.6µs/test |

**The totals did not move, and the reason is measured.** Each run holds a
few one-off stalls in tests that are fast everywhere else, and the stalls
land in different tests in each run:

| test | in the run where it stalled | elsewhere |
| --- | --- | --- |
| `test_gate_compare … same_resolver_twice…` | 86.6s (baseline) | not in the candidate's top 25 (≤7.5s); its whole module takes 5.1s in a shell |
| `test_gate_invalid_wording` (2 tests) | 70.9s + 20.7s (baseline) | not in the candidate's top 15 classes; 0.37s in a shell |
| `test_failed_move_commit.External … quoted_cards_failed_move…` | 169.6s (candidate) | baseline class 15.6s for 20 tests; 20.2s in a shell with the stalled journey below |
| `test_orchestration_journey.FailureWindows … crash_before_the_launch…` | 140.6s (candidate) | baseline class 51.5s for 11 tests |

Together that is roughly 175s of stall in the baseline and 300s in the
candidate. The 120s the fix removed is real (the class reading above) but
smaller than the run-to-run spread these stalls make. Two single runs
cannot show a whole-suite speed-up, and none is claimed. **Hypothesis,
not measured:** the stalls are contention from other work on the shared
machine, or the directed environment's cold start-up described above. A
test does not stall in one run and finish in under a second in the next
because of anything in its own logic.

## Remaining hotspots (measured, candidate run)

- `test_orchestration_journey` (Vendored, SourceHosted, External,
  FailureWindows): about 370s across 38 tests even without a stall. These
  are the end-to-end journeys (real git repositories, a bare origin, the
  adapter's own `run`), at 8–18s each. Shortening them means sharing a
  journey's prepared board between tests, which needs its own isolation
  evidence. Out of scope here.
- `test_architecture.ScratchCopyCatchesViolations`: 33–39s for 19 tests,
  each copying the tree into a scratch directory.
- `test_verify_entrypoints.RemotePlaywright`: 26s, mostly two
  `…times_out…` tests at 11s each. Each waits out a deliberate timeout it
  asserts on, so this is not changed.

## Reproducing

    bench-verify bench-backend          # prints the ranking into its evidence
    bench-verify --details <evidence>   # reads it back
    verify/timing-report --baseline A… --candidate B…   # compares timings.json sets
