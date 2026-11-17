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

# The board list without the cards' text (task 129)

`/api/state` no longer carries each card's Markdown; the detail view reads
it from `GET /api/card` when a card is opened. Measured by the browser
case `verify/browser_cases/case_lazy_detail.py`, which writes
`lazy-detail-measurements.json` beside its screenshots and prints the
same line into the `bench-browser` log (evidence
`ev:verification@424ef17b5ac5`, commit `2edd875`).

The fixture is the browser suite's disposable board plus 60 small cards
and 3 history-heavy ones (20 work reports and 20 review rounds each, about
128 KB of text per card): 69 cards in all.

| measure | before | after |
| --- | --- | --- |
| `/api/state` bytes, uncompressed | 474,218 | 100,941 (−79 %) |
| the board section alone | 414,810 (as `/api/tasks` serves it) | 41,533 |
| first card drawn, from navigation start (upper bound) | not measured | 225 ms |
| opening a heavy card until its brief is drawn | not measured | 227 ms |

**How "before" is computed.** Both columns come from one moment on one
board: the same state payload with its `board` swapped for the one
`/api/tasks` serves, which still carries every card's whole text. That is
exactly the list's old shape, because the sections beside it did not
change. The case asserts the new payload is under half the old one.

**What is not shown.** Timings for the old code were not taken: the
browser suite runs one build, and the old one was not served beside it.
The first-card figure is an upper bound — the case reads the clock once
the card is seen, so it includes polling. Compression (gzip on
`/api/state`) is a separate option the card asks to measure on its own;
it was not measured or enabled here, and it would not stand in for this.

**Running the checks.** Run `bench-backend` and `bench-browser` one after
the other, not together, at the same commit. They share one source
checkout per commit, and the run that finishes first removes it from
under the other. That ends the other run as a setup error with no verdict,
and leaves a directory that refuses later runs at that commit.

# One archive, end to end (task 128)

Raw output of `tests/archiveperf_measure.py`, in `128-<scenario>.json`:
the host, the fixture, the sample order and every sample. Regenerate one
scenario, in the foreground, with:

```bash
python3 -m unittest tests.archiveperf_measure.Measure.test_rich_100
```

The fixture is task 127's board with its `tasks/` committed and
`BOARD_COMMIT_MOVES` on, so each archive is a real commit in the work
state's repository. It is served by the real `httpd.Handler` on a
loopback port with a real `/api/stream` connection. Every archived card
is a disposable fixture card; no live board is touched.

The server is the same in both arms. What differs is what the page does
with an archive, driven over HTTP the way the page's code does it:

- **before** — the page as it was. The archive's `board` frame asks for
  a reading (A); the action awaits the POST, then `await loadState()`,
  which queues behind A (B). The card leaves when A lands, and the
  action ends when B lands. Two whole-board reads.
- **after** — this card. The card leaves when the POST answers. The
  frame and the confirmation share one reading, whichever arrives first,
  and the board is reconciled when it lands. One whole-board read.

`warm` reads the state immediately before the archive. `expired` moves
the clock 20 s on first, past every memo's time bucket. Arms alternate
ABBA. Medians of 4 samples per cell, in seconds from sending the POST:

| scenario | cache | arm | POST answered | card gone | reconciled | action done | state reads |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rich-100 | warm | before | 0.027 | 0.211 | 0.211 | 0.316 | 2 |
| rich-100 | warm | after | 0.029 | **0.029** | 0.230 | 0.029 | 1 |
| rich-100 | expired | before | 0.028 | 2.241 | 2.241 | 2.349 | 2 |
| rich-100 | expired | after | 0.029 | **0.029** | 2.282 | 0.029 | 1 |
| light-100 | warm | before | 0.026 | 0.151 | 0.151 | 0.241 | 2 |
| light-100 | warm | after | 0.024 | **0.024** | 0.145 | 0.024 | 1 |
| light-100 | expired | before | 0.030 | 0.382 | 0.382 | 0.468 | 2 |
| light-100 | expired | after | 0.027 | **0.027** | 0.392 | 0.027 | 1 |

The archive itself, commit included, answers in under 30 ms at both
sizes. Before, the card stayed on the page for a whole-board read after
that — 2.2 s on an evidence-rich board whose memos had expired — and
the archive's chip stayed busy for a second read. After, the card leaves
on the reply and the chip frees with it. Reconciliation still costs one
read, now in the background, so "reconciled" is unchanged within noise.

**The request count, bounded.** One archive asks for at most two
whole-board reads in the page:

1. one for the archive itself, shared by its confirmation and its
   `board` frame (each names the same revision, and a reading already out
   that was sent after the revision was known holds it). If a reading
   sent *before* the archive is still out when it is confirmed, that
   reading cannot hold it, so one follow-up is queued behind it — the
   older reading is painted with the archive laid over it, never with the
   card back;
2. one for the watcher's later frame, sent when it notices the file
   gone from the stage directory (about `BOARD_WATCH_INTERVAL` later). It
   names a newer revision, so it reads once. The harness runs no watcher,
   so this read is in neither arm above; the old page made it too.

A frame naming a revision the page has already painted asks for nothing.

**Not measured here.** The browser's own paint (the harness stops at
the data the page acts on). `verify/browser_cases/case_archive_confirmation.py`
checks the real page in the sandboxed browser instead: the card leaves
while a whole-board read is held back.

# Cards painted by key (task 130)

Raw output of `verify/browser_cases/case_keyed_cards.py`, in
`130-keyed-before.txt` and `130-keyed-after.txt`: every sample, one line
per board size and scenario, exactly as the `bench-browser` log printed
them. Both arms ran in the same sandboxed container Chromium
(151.0.7922.34, viewport 1440×900) on one Linux host, one commit apart:
`b4f4fb2` (the page as on `refactor/bench-architecture`, evidence
`ev:verification@0782c9479225`) and `2fb5479` (keyed painting, evidence
`ev:verification@16aa8180d68b`). The case is the same at both.

The boards are synthetic: the fixture's own cards cloned in the page into
a state of N cards spread over the five stages, painted through the
page's own `render()`. So this measures the page's rendering and the
browser's layout and paint, not the server's read (task 127 measures
that). Five repetitions per scenario. `visible` runs from the start of
the paint until the next frame has run, which includes style, layout and
paint; `script` is the synchronous `render()`; `layout` is what a read of
`offsetHeight` straight afterwards forces.

## Results (median `visible` ms)

| cards | scenario | before | after |
| --- | --- | --- | --- |
| 100 | one card changed | 32.8 | 8.5 |
| 100 | session event, board unchanged | 32.9 | 16.4 |
| 500 | one card changed | 151.4 | 12.1 |
| 500 | session event, board unchanged | 167.0 | 16.1 |
| 1000 | first paint | 323.0 | 288.3 |
| 1000 | **one card changed** | **307.6** | **24.8** |
| 1000 | session event, board unchanged | 317.7 | 16.8 |
| 1000 | identical reading | 313.7 | 13.6 |
| 1000 | one column reversed | 307.6 | 50.4 |
| 1000 | one card moved to another column | 308.0 | 20.1 |
| 1000 | 20 readings in a burst (total) | 1435.4 | 151.7 |
| 1000 | click on a card's ⋯ tools | 17.3 ¹ | 14.1 |

**The target is met on this host:** one changed card at 1000 cards is
visible in 24.8 ms (median; 23.9–27.7 ms over five samples), under the
50 ms target. After the change a paint keeps every card node whose
drawing did not change (`kept` = 999 of 1000 for one changed card, 1000
of 1000 for a session event). Reversing a 200-card column moves every
node and recreates none; it is the slowest update, at 43–63 ms, around
the target.

¹ Before, the first two tools clicks at 1000 cards took 342 and 347 ms
(a whole-board rebuild each) and the last three took 4–17 ms with about
1 ms of script, so they did not repaint the board at all; why those three
clicks painted nothing was not investigated. The median hides that; the
raw lines show it. At 500 cards every click before cost 151–173 ms.

## What is not improved, and what is not measured

- **The first paint is unchanged in kind**: at 1000 cards it still
  builds 1000 cards (288 ms after, 323 ms before). Windowing the columns
  would be the way to cut it, and it is not part of this card: it would
  omit cards from the DOM (and so from in-page search), which the card
  rules out. Reported here as a possible follow-up, not done.
- **The detail view and the activity bar** still repaint on every
  frame as before; this card keys the Board's cards and the Phases view's
  lanes and cards. The column and lane headers are rewritten only when
  their text changes.
- **One host, one browser.** The numbers are relative. Interaction
  latency is measured as a scripted click to the next frame, not as
  input-to-paint from a physical device.
