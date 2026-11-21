# Verification contention and protecting acceptance runs (task 179, WP4)

This records what is measured, what is not, the capacity recommendation
that follows, and which operational actions are applied versus pending.
Measured numbers and hypotheses are kept apart, as in
`163-backend-timing.md`.

## What this card shipped (application code, not activated settings)

- `manager/core/checkload.py`: a beat that writes a diagnostic sidecar per
  verification under `state/evidence/load/`. It records the check's phase
  (waiting, preparing, running), the other checks in each phase, the slot
  limit, the agent runs the board tracked, and the host's load average,
  CPU count and available memory. Up to 16 peers are listed with the
  seconds observed together and executing together. It is outside
  `resultidentity.RUNNER_FILES`, so no runner revision, result identity
  or reuse decision moved.
- `verify/contention-report`: separates `before` (request → first
  process), `slot` (the record's `queued`), `prep` and `exec`. It computes
  overlap from the records, so historic records take part. It compares
  quiet and overlapped full runs per check, and it calls a sample of fewer
  than three per side too small to read.

Neither piece changes how many checks run, in what order, or with what
outcome.

## Live configuration, reconciled

| item | value | source |
| --- | --- | --- |
| shipped default `BOARD_VERIFY_PARALLEL` | 2 | `manager/core/config.py`, `.env.example` |
| live `BOARD_VERIFY_PARALLEL` | **6** | the lead's Settings readback, recorded in the card's input review. This run could not read the operator's `.env` or Settings, so this is not re-read here |
| when it takes effect | at board start (`config` reads it at import; Settings marks it `restart`) | `manager/core/settings.py` |
| slot wait limit | `max(check timeout, 60)` s, then the request ends `error / timeout` with *nothing ran* | `verify._execute` |
| check timeout | 1800 s by default (`BOARD_VERIFY_TIMEOUT`) | `config.py` |
| one card's **Run required checks** batch | already one check at a time, in merge-rule order | `required.py` |

So the overlaps that matter come from *different* cards and runs, such as
an agent's own `bench-verify` beside another card's batch. They do not
come from one card's batch.

Active checks and host load at the moment of writing were **not
observed**. This run is not approved to read the operator's evidence or
state directory.

## The sample, and what it does and does not support

There is no fresh measurement. The card forbids generating load beside
active user work without approval, and quiet-versus-loaded runs are
WP3 step 1's measurement, to be reused rather than duplicated. The
comparable historic sample is the retained records the Fable 5.2 review
read (`plans/verification-efficiency-fable52-review.md`, §3):

| check | full runs | known overlapped | known quiet | execution, s |
| --- | --- | --- | --- | --- |
| bench-browser | 9 | 2 (`3368f9834f38`: 538, setup error; `dfa4e025ac4b`: 368, failed) | **unknown** for the other 7 | 368–538 overall |
| bench-backend | 5 | 1 known beside browser suites (`986703c8c715`: 1471.6, failed on a wall-clock test) | unknown | 1456–1573 |

What this supports:

- A full backend run takes about 25 minutes and a full browser run 6–9
  minutes. These are measured.
- The two overlapped browser runs bracket the whole range: the slowest
  run and the fastest. **Overlap alone does not explain duration** in
  this sample.
- Both known-overlapped failures are of the kind load can plausibly
  cause: adapter operation timeouts, and a wall-clock assertion. That is
  an association in two or three runs, not a cause.

What it does not support: any ratio of overlapped to quiet duration, any
failure rate under load, or a choice between parallel 1 and 2. Running
`verify/contention-report` on the board's own evidence directory gives
the overlap of every historic run, not only the ones the review named. It
was not run here, because that directory is outside what this run may
read. It is the first pending step below.

## Throughput versus latency, quantified from the measured durations

Take one card needing a full backend (B ≈ 1500–1570 s) and a full browser
(W ≈ 370–540 s) run:

- **Run together (parallel ≥ 2):** acceptance latency ≈ max(B, W) ≈ 1570 s,
  if neither is slowed.
- **Serialized (parallel 1):** latency ≈ B + W ≈ 1870–2110 s. That is
  +300–540 s (+20–35 %) per card, every time.
- **The cost of a load-induced failure:** a full rerun, so +W (370–540 s)
  for a browser and +B (1460–1570 s) for a backend. Running together saves
  W per card. A browser failure it adds costs about the same W, so added
  browser failures alone never make serializing cheaper in time. They do
  cost attention and evidence noise. An added *backend* failure costs B,
  so running together stops paying once overlap causes backend failures
  in more than about W / B ≈ 0.25–0.35 of overlapped runs (roughly one in
  three or four).
- **Throughput at parallel 1 has a hard edge.** A request waits for a slot
  at most `max(timeout, 60)` = 1800 s. Behind one backend run (≈1570 s) a
  second request just fits. Behind two (≈3100 s) the third ends
  `error / timeout`, "a slot never came free", and nothing runs.
  Serializing without raising the slot wait therefore turns a queue into
  errors once two backend runs are pending.
- **Parallel 6 is outside anything measured.** Six concurrent full suites
  on one host are not represented in the sample. The one backend
  wall-clock failure and the slowest browser run both had company.

## Recommendation

1. **Do not serialize (parallel 1) now.** It costs 20–35 % latency on every
   card. With the 1800 s slot wait it turns a two-backend queue into
   `error / timeout`. The sample cannot show it would remove a single
   failure.
2. **Return `BOARD_VERIFY_PARALLEL` from 6 to the shipped default of 2.**
   This keeps one backend and one browser able to run together, which is
   the latency win above. It removes the unmeasured 3–6 concurrent suites
   that are the most plausible source of transport timeouts and
   wall-clock failures. It changes no gate, no evidence identity and no
   agent budget.
3. **Operational practice until WP1 (browser harness) and WP3 step 1
   (contention versus cold start) land:** avoid starting a full
   `bench-browser` while a full `bench-backend` for another card is
   running when the result is the acceptance run of record. This is a
   person's or orchestrator's sequencing choice, not a new scheduler.
4. **Decide parallel 1 versus 2 from the sidecars.** After at least three
   comparable full runs per side per check, run `python3 verify/contention-report`.
   If overlapped browser runs' median execution is above 1.25× quiet,
   **or** overlapped runs fail with transport or setup timeouts that quiet
   runs do not show at a rate above about 1 in 4, then propose an
   exclusive slot for full suites as its own card. A sidecar `slots` field
   confirms which limit each run actually ran under.

## Operational plan: applied versus pending

| action | status | when | how it is read back | rollback |
| --- | --- | --- | --- | --- |
| ship `checkload` and `verify/contention-report` (this card's PR) | **applied in application Git only**, pending review and merge | on merge | `tests/test_checkload.py`, and `bench-backend` at the PR head | revert the PR. The sidecars are machine-local files, which a revert leaves unread and which can be deleted |
| promote the merged code into the operator install | **pending** an operator's promotion (`update.sh` or git, then `install.py`) | at the operator's next restart | `state/evidence/load/` gains a file for the next check, and Settings → Installation shows the revision | promote the previous revision and restart |
| read the historic overlap: `python3 verify/contention-report <tool>/manager/local/state/evidence` | **pending** an operator, read-only | any time | the report itself. Record the sanitized summary in work Git | none needed: it writes nothing |
| `BOARD_VERIFY_PARALLEL`: 6 → 2 in `manager/local/.env` | **pending approval**. Not applied | the value is read **only at board start**, so saving the file is next-start configuration, not activation | before restart: Settings → Installation shows the file value with a `restart` chip. After restart: it shows 2, and each new sidecar's `first.slots` reads 2 | set it back to 6 in `.env` and restart the same way |
| restart to apply it | **pending**, and only when `stop.sh` does not refuse (no agent running) and no verification is in flight | an idle window | as above | as above |
| parallel 1, an exclusive full-suite slot, or a longer slot wait | **not proposed** until the sidecar comparison in recommendation 4 says so | — | — | — |

Nothing in this card restarted a board, patched a semaphore in a running
process, cancelled a check or changed an agent budget. Running agents and
checks are untouched by every pending step except the restart itself.
The `stop.sh` guard and the idle window exist to keep it that way.

## Unknowns and limits

- Whether historic overlaps beyond the three named runs were quiet or
  loaded is unknown until the report is run on the operator's evidence.
- Sidecar readings are within one 5-second interval of the real start and
  end. A check shorter than that may not be seen at all, and a sidecar
  without `end` is a run whose ending a restart hid.
- Load average counts runnable processes on the whole host, including work
  bench did not start. It is a host signal, not a share of the check.
- Cold subprocess start-up versus machine contention (the 163 hypotheses)
  is WP3 step 1's question. These sidecars give that measurement its load
  context, and they do not answer it alone.
