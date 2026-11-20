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

See the candidate section appended below, from `bench-backend` at the
final commit.
