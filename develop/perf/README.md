# Board read measurements (task 127)

Raw output of `tests/boardperf_measure.py`, one JSON file per scenario,
kept as it was written. Each file holds the host, the fixture, the
sample order, every sample (time, git process count and git calls by
verb), the per-section timings, the read scope's own counts and whether
the two arms' public payloads were equal.

Regenerate one scenario, in the foreground, with:

```bash
python3 -m unittest tests.boardperf_measure.Measure.test_rich_100
```

## What is compared

- **baseline** — `routes._state_payload()` with no read scope open: the
  `/api/state` read exactly as it was before task 127.
- **optimized** — `routes.state_payload()`, which opens
  `profiles.read_scope` around the same function.

The scope is the only thing that differs, so both arms run in one
process, on one interpreter (Python 3.14.4 here), against one fixture,
in ABBA order. The fixture (`tests/boardperf.py`) is a disposable board.
Every card has a branch, and each evidence-rich card carries a record of
a `worktree` check and a `source` check, both declaring six approved
inputs. The record outcomes rotate through passed, failed, stale and
running. "light" boards have 10 evidence cards among the rest.

## Results (median seconds, git processes)

| scenario | samples | baseline cold | optimized cold | optimized expiry | optimized warm | payloads equal |
| --- | --- | --- | --- | --- | --- | --- |
| rich-100 | 4 + 4 | 9.17 s · 2055 | 2.45 s · 521 | 2.36 s · 521 | 0.087 s · 11 | yes |
| rich-500 | 1 + 1 | 83.9 s · 18848 | 13.7 s · 3023 | 13.9 s · 3017 | 11.8 s · 2525 ¹ | yes |
| rich-1000 | 1 + 1 | 168.8 s · 37616 | 28.3 s · 6023 | 28.6 s · 6023 | 28.9 s · 6023 ¹ | yes |
| light-100 | 4 + 4 | 1.08 s · 258 | 0.28 s · 72 | 0.36 s · 72 | 0.06 s · 11 | yes |
| light-500 | 2 + 2 | 1.31 s · 259 | 0.47 s · 72 | 0.49 s · 72 | 0.09 s · 11 | yes |
| light-1000 | 2 + 2 | 1.44 s · 259 | 0.54 s · 72 | 0.57 s · 72 | 0.14 s · 11 | yes |

Concurrent reads (four threads started together, memos dropped first)
agreed with each other in every sample. At rich-100 they took 7.4–7.8 s
wall time before and 2.9–3.3 s after. At rich-500 and rich-1000 the
optimized arm took 19.0 s and 53.1 s; the baseline arm was not run.

Inside one optimized rich-1000 read, the scope was asked for the
document 2758 times and read it once. Provenance was asked 12 times and
looked up 6 times, once per input file, where the baseline did 6 per
passing record. Input selections were asked 15042 times and read 12
times. Resolutions were asked 1754 times and computed 752 times, once per
distinct intent and root pair (per-card worktrees).

¹ A read longer than the 10 s freshness bucket (`staleness.FRESH_TTL`)
outlives its own memos. The next read is effectively an expiry read
rather than a warm one. That holds before and after this change.

## What is not shown, and why

- **Baseline expiry, warm, concurrent and section timings above 30 s.**
  A baseline cold read at 500 cards took 84 s, and at 1000 cards 169 s.
  Repeating it three or four times more per sample would not fit one
  ten-minute run. Each skipped entry is in the JSON with its reason.
- **The 2 s cold target is met for lightweight boards of every size,
  not for evidence-rich ones.** rich-100 reads cold in 2.45 s. What is
  left is per-record git work inside the runner files, with no repeated
  shared provenance in it:
  - `resultidentity.source_identity` for each `worktree` record: about
    five git processes each.
  - A `rev-parse` of each card's own branch for each `source` record.
  - The filter-driver `git config` read before each `diff` and `status`.

  On a real board each card has its own worktree, so these calls are
  distinct work. In this fixture every `worktree` record shares one
  checkout, so a request-scoped memo there would save more here than it
  would on a real board. Changing any runner file
  (`resultidentity.RUNNER_FILES`) changes the runner revision. Every
  recorded pass would then read `runner-changed` and need one fresh run.
  That is a decision for a separate card, not a speed shortcut taken
  here.
- **Host.** One Linux machine (`host` in each file). These numbers are
  relative; the investigation's own host may differ.
