# Task 178 — backend dead time, and whether to shard

Every reading below is labelled **measured** (with its conditions) or
**hypothesis**. Source: `87f40651aedd` (application main, with #166 and
the phase-156 merge; the review's pin `7f65b62` is its ancestor). Host: the
shared devbox, 8 CPUs, Python 3.14.4, with other agent sessions active
(load average 5.9–7.9 before any load of ours). Card 163's directed-runner
readings are in `163-backend-timing.md`.

## 1. Environment versus load (measured)

`178_env_load_compare.py` copies the checkout without `.git` or any
`__pycache__` (cold, as bench's fresh source checkout is) and runs each
module three times under three environments:

- **shell** — this shell's environment, bytecode allowed;
- **directed** — `benchverify.child_env` exactly as the runner built it
  before this task: fresh `HOME`/XDG/`TMPDIR`, `PYTHONDONTWRITEBYTECODE=1`;
- **directed + cache** — the same with a run-owned `PYTHONPYCACHEPREFIX`
  (what `child_env` builds now).

Then again with 4 busy-loop processes (cpus/2) running for the duration
(load average rose to 12.2). Seconds per run, three runs each:

| | quiet | 4-process load |
| --- | --- | --- |
| shell `test_gate_invalid_wording` | 0.78, 0.39, 0.45 | 0.94, 0.57, 0.46 |
| directed `test_gate_invalid_wording` | 0.78, 0.85, 0.75 | 0.93, 0.92, 0.98 |
| directed + cache `test_gate_invalid_wording` | 1.17, 0.42, 0.41 | 1.58, 0.66, 0.63 |
| shell `test_gate_compare` | 5.28, 5.02, 5.00 | 6.89, 6.56, 7.06 |
| directed `test_gate_compare` | 5.40, 5.09, 5.04 | 8.12, 7.16, 8.04 |
| directed + cache `test_gate_compare` | 5.01, 4.83, 4.75 | 7.77, 7.65, 7.72 |
| shell `import board` (subprocess) | 0.125, 0.120, 0.132 | 0.150, 0.157, 0.171 |
| directed `import board` | 0.454, 0.445, 0.449 | 0.537, 0.577, 0.566 |
| directed + cache `import board` | 0.127, 0.115, 0.116 | 0.143, 0.149, 0.136 |

What this establishes:

- **The directed environment costs a fixed, deterministic amount per
  Python process**: a cold compile of `manager/core`, about 0.33s per
  `import board` (0.45s against 0.12s). With the cache it is paid once per
  run. That is dead time, asserted on by nothing, and it is removed (§3).
- **Neither the directed environment nor this load reproduces the
  historical stalls.** Under bench's runner `test_gate_compare` once took
  119.2s for 8 tests and `test_gate_invalid_wording` 92.5s; here the worst
  reading is 8.1s and 0.98s. A CPU load of half the machine slowed them by
  30–60%, not by 100×.
- **Unresolved:** what the 70–170s one-off stalls are. Not the directed
  environment (measured, above); not CPU load of this size (measured,
  above). Still open, as hypotheses: I/O or memory pressure from
  concurrent full suites (the verification parallel limit is 6 on this
  board, per the lead's readback), or a lock or timeout inside the code the
  stalled tests run. A stall's own stack was never captured, and a
  reproduction needs one (§5).

## 2. Ranking: is there dead time left? (measured)

The whole suite, clean shell, run as four consecutive module groups with
`python3 -m unittest --durations`: 4909 tests, all passing, none skipped,
**1045.2s** of test time (205.4 + 246.2 + 317.5 + 276.1). Bench's runner
took 1561–1569s for 4657 tests at card 163's heads, with 175–300s of
those runs in stalls. The two are not comparable as a speed-up (different
heads, different counts, a warm shell); they bound what is environmental.

The slowest single tests in the clean shell, and what each waits on:

| s | test | wait |
| --- | --- | --- |
| 13.9–8.3 | `test_orchestration_journey` failed-move and phase-scope journeys (12 tests) | real work: 15–20 driver passes, each a fresh process, real git |
| 11.0 ×2 | `RemotePlaywright` hung-operation and never-connects | a deliberate timeout the test asserts on — kept |
| 10.3 | `test_setup_preservation.DecisionsIntegrity` | 48 sub-tests, each a fresh `Project.run` process |
| 8.0 | `test_reasoning.Refusals.test_each_bad_response_is_refused` | 9 scenarios, each one adapter launch, polled every 50ms |
| 5.5 | `test_node_modules` | the node suite |
| 5.4–3.9 | `ScratchCopyCatchesViolations` | a tree copy per test |
| 5.3 | `test_verifier_setup_outcomes … long_and_many_findings` | subprocess runs |
| 3.9–3.0 | `test_application_previews.HonestEndings` | readiness timeout and SIGTERM grace the tests assert on — kept |

An audit of every `sleep` in `tests/` (81 calls) and of `GateCase`'s
`VERIFY_TIMEOUT=60.0` users found each sleep either inside a poll loop
with a condition, inside a stub program that is meant to hang (and is
killed or joined by the test), a deliberate clock step
(`test_failed_run_visible`: agent ids are stamped to the second), or a
0.2–0.3s window before asserting that *nothing further* happened
(`test_required_checks`, `test_verify_on_review`, `test_agent_progress`:
no second run, no extra notice) — an absence has no completion signal to
wait on, and together these are a few seconds. No test
in the ranking waits out a timeout it does not assert on; the two that did
were fixed by card 163. So there is no further 60s-class fix to make, and
the remaining per-test costs are process start-up — which §3 reduces under
bench's runner, where it was paid cold.

## 3. The change (measured mechanism; whole-suite effect below)

`verify/benchverify.py` `child_env` no longer sets
`PYTHONDONTWRITEBYTECODE=1`. It sets `PYTHONPYCACHEPREFIX` to
`<run work>/cache/pycache` and removes any inherited value of either. The
checkout still receives no `__pycache__` (that was the reason for the old
setting), the cache belongs to the run, and every check that builds its
child through `child_env` (backend, browser, docs, preflight) gets it.

Who removes it: `Run.finish` removes only `<run work>/tmp`, so
`<run work>/cache` — now holding the bytecode cache beside the pip and npm
caches it already held — is left to whoever owns the work directory. Under
bench's own runner the work directory is made in the check's `TMPDIR`,
which bench removes when the check ends, so the cache goes with it. A
wrapper run by hand leaves its work directory, cache included, in the
scratch directory (`--scratch`, `BENCH_VERIFY_SCRATCH`, else the system
temporary directory), exactly as it already left the pip and npm caches.

One further cost, from Python's documented behaviour and not measured
here: with `PYTHONPYCACHEPREFIX` set, Python looks for every module's
bytecode under the prefix only, so the standard library's precompiled
`.pyc` files are not used either. The first child process of each run
compiles the standard-library modules it imports into the run's cache;
later children read them from there. That one-off cost is inside every
"new" reading below, since each repetition starts from an empty cache. Regressions:
`tests.test_verify_entrypoints.BytecodeCache` — the variable is the run's,
an inherited one is replaced, and two child imports write one `.pyc` into
the prefix and nothing into the checkout.

One property changes with it, said plainly: a cached `.pyc` is validated
by the source's modification second and size, so a test that rewrites a
`.py` file to the same size within one second and imports it again in a
new process could read the older bytecode. That is exactly how CI and any
developer shell already run the suite (`python3 -m unittest` writes
`__pycache__`), so no test can depend on the old no-cache behaviour
without already failing there.

**The first run under bench's runner (measured).** `bench-backend` at
`c2f1b16a5ccf`, evidence `ev:verification@83b015cde84c`, 2026-11-21 11:43
UTC, serial, on the shared host:

| | card 163 baseline `464f1fa` | card 163 candidate `ab83b88` | this candidate `c2f1b16` |
| --- | --- | --- | --- |
| tests | 4657 | 4657 | 4911 |
| check took | 1573.0s | 1565.9s | 1142.8s |
| elapsed / tests / fixtures | 1568.5 / 1531.2 / 35.9s | 1561.4 / 1527.3 / 32.7s | 1137.8 / 1100.3 / 35.7s |
| mean test | 0.33s | 0.33s | 0.22s |
| slowest single test | 86.6s (a stall) | 169.6s (a stall) | 12.2s (a journey) |

What it does and does not show:

- No test stalled in this run: the slowest is a journey at 12.2s, and
  `test_orchestration_journey.FailureWindows` and
  `test_failed_move_commit.External`, which held the 163 candidate's
  140.6s and 169.6s stalls, took 27.0s and 15.0s in total.
- The runner's test time (1100.3s) is now within about 5% of the clean
  shell's (1045.2s, §2), for nearly the same suite (4911 against 4909
  tests; the two are this task's regressions).
- **Not separated:** how much of the 425s drop is the bytecode cache and
  how much is the absence of the 175–300s of stalls. The test ids differ
  (4911 against 4657; the suite grew), the heads differ, and this is one
  run. `verify/timing-report` refuses to compare different id sets, rightly.
  Subtracting the stalls from card 163's runs leaves about 1270–1390s, so
  the cache's share is plausibly 130–250s — an estimate, not a
  measurement. The same-ids comparison below separates the cache's
  effect for the subprocess-heavy modules; the whole suite under the old
  environment was not re-run.

**Same test ids, old versus new environment (measured).**
`178_same_ids_compare.py` runs four subprocess-heavy modules serially in a
cold copy of the candidate (`425d484`/`d14553a`: this card's code; the
two differ only in the measurement script) under the environment
`child_env` built before this task (*old*: `PYTHONDONTWRITEBYTECODE=1`)
and the one it builds now (*new*: a run-owned `PYTHONPYCACHEPREFIX`,
empty at the start of each repetition, so each *new* reading pays its own
first compile, standard library included). Old and new run back to back
in each repetition, the order alternating (old first, new first, old
first) so host drift falls on both. Each child ran `-v`, and the test ids
the two reported were compared and equal in every module and repetition:
139 ids (41 + 60 + 2 + 36), every run exit 0, nothing skipped, and no
`__pycache__` written into the copied checkout. No load was added. The
host was the shared devbox, 8 CPUs, Python 3.14.4, 2026-11-21 between
about 12:40 and 13:30 UTC, with other agent sessions working: the one-minute
load average read 1.9–8.2 across the runs (3.3–8.2 in repetitions 1–2,
1.9–4.3 in repetition 3), with one bench backend and one bench browser
verification in flight during repetitions 1–2 and 0–2 during repetition
3. (The script's `verifiers` count over-counted in repetitions 1–2 by
matching agent prompts that mention a verifier; it is anchored on the
interpreter now, and the in-flight count above was read by hand.)

Seconds per module, repetitions 1, 2, 3:

| module (tests) | old | new | mean old → new |
| --- | --- | --- | --- |
| `test_orchestration_journey` (41) | 320.4, 352.0, 351.9 | 218.1, 232.9, 257.5 | 341.4 → 236.1 (−105.3s, −31%) |
| `test_failed_move_commit` (60) | 45.0, 45.4, 45.7 | 45.2, 45.5, 46.0 | 45.3 → 45.6 (no change) |
| `test_setup_preservation` (2, 48 sub-tests) | 12.5, 12.6, 13.1 | 10.6, 10.2, 10.6 | 12.7 → 10.4 (−2.3s) |
| `test_reasoning` (36) | 11.6, 10.9, 12.1 | 11.7, 11.1, 12.5 | 11.5 → 11.8 (no change) |
| **all four** | 389.4, 420.8, 422.8 | 285.6, 299.6, 326.5 | 411.0 → 303.9 (−107.1s, −26%) |

What it establishes, and what it does not:

- **Measured, beyond the variance seen here:** for the journeys and the
  setup-preservation module the slowest *new* reading is faster than the
  fastest *old* one (257.5 < 320.4; 10.6 < 12.5), in all three
  repetitions, whichever ran first. The spread within one environment
  (32s old, 39s new for the journeys) is a third of the difference
  between them.
- **No effect** where the child processes are git and shell rather than
  Python importing `manager/core` (`test_failed_move_commit`), or few
  (`test_reasoning`): the differences there (+0.3s) are inside the spread.
- **Not measured:** the whole suite. The other modules were not run under
  both environments, so the 130–250s whole-suite estimate above stays an
  estimate; what is measured is 107s across these four modules, and that
  none of the four got slower beyond the spread.
- **Not established:** anything about the historical stalls; none occurred
  in these runs, under either environment.

## 4. Sharding: deferred (decision)

The card ships sharding only if measurements support a benefit. They do
not yet:

- the historical stalls, the largest variable cost, are unexplained, and
  concurrency is one of the remaining hypotheses — shards would add load
  to a host whose stalls may be load;
- the measured CPU-load sensitivity (30–60% slower at half the machine)
  means two or three shards on a host already running several agents and
  up to six verifications would not divide the wall time by their number;
- isolation is not proved: the journeys run real git repositories and
  driver processes, `RemotePlaywright` and `HonestEndings` assert on wall
  clocks, previews bind loopback ports, and no audit of shared ports,
  global git configuration or fixed paths has been done;
- shards are more concurrent verification on the shared host, which this
  card must not introduce as a live capacity increase.

`verify/backend` stays serial. Nothing was added behind a flag.

## 5. What would reopen it

1. A stall caught in the act: under bench's runner, a full
   `bench-backend` with a per-test watchdog that dumps every thread's
   stack (`faulthandler.dump_traceback_later`) for any test passing 30s,
   while the host's load, I/O wait and the other verifications in flight
   are recorded at start and end.
2. Five serial runs of one head on a quiet host (no other verification in
   flight) compared with `verify/timing-report`: if the stalls vanish when
   the host is quiet, they are contention, and sharding is ruled out
   until verification is scheduled apart; if they persist, they are in the
   code and are fixed there first.
3. Only then, a shard prototype with the guards the card lists — the
   discovered test-id multiset equal to the union of the shards', separate
   `HOME`/`TMPDIR` per shard, wall-clock and journey classes in one serial
   lane, any shard skip, failure or missing output failing the whole —
   measured five times against serial.

Proposed 10–14 minute totals remain an estimate; nothing here measured
them.
