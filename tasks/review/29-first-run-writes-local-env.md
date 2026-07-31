# 29 — A first run writes local/.env, asking the questions it can't guess

**Status:** Review
**Assignee:** istos
**Priority:** Medium — nothing is broken without it, but two of bench's
defining settings are invisible to anyone who hasn't read `.env.example`
**Type:** Feature

Today a project can run bench for months without ever having a
`manager/local/.env`: everything falls back to the defaults in
`core/.env.example`, and the two settings that change what bench *is* —
claim-on-move and syncing through origin/main — are off unless someone
reads the example file and copies it. Nothing tells them the file exists.
Make the first run create it: `install.py` (and `start.sh`, which calls
it) notices there is no `local/.env`, asks a handful of questions it
cannot answer for the project, and writes an `.env` that is a real,
commented settings file rather than an empty one.

## Context

- `install.py` resolves the adapter and runs its `wire`; it already owns
  the first-boot story (`first_boot`, `first_boot_clean`) and is the
  natural place for this. It runs on every `start.sh` and after every
  `update.sh`, so it must stay idempotent and silent once wired.
- `start.sh` calls `python3 install.py || true` and then, only in the
  port-clash branch, writes a bare `BOARD_PORT=<n>` line into
  `local/.env`, creating the file if absent. That is the one existing
  writer and it must keep working on top of whatever setup writes.
- `core/.env.example` is the source of truth for what settings exist and
  what they mean — every key documented in a comment block. The written
  file should be that file with the answers substituted, not a
  hand-rolled three-line stub.
- `core/config.py` reads `local/.env` with process env > file > default,
  so a missing key is never an error; this task changes discoverability,
  not resolution.
- Tests live in `tests/`; `test_install_first_boot.py` already exercises
  `install.py`'s first-run behaviour and is where this belongs.

**Affected areas:** `install.py`, `start.sh`, `manager/core/.env.example`,
`tests/`. No board module changes — nothing in `core/` reads settings
differently afterwards.

## What to build

- A setup step in `install.py` that runs when `manager/local/.env` does
  not exist, **after** `first_boot_clean` (see Risks — writing `.env`
  earlier would flip the first-boot guard and leave the distribution's
  own cards in a host project).
- It asks a short, closed set of questions. Everything else is written at
  its documented default:
  - **Solo or team?** Solo → `BOARD_COMMIT_MOVES=` and `BOARD_SYNC=`
    (today's behaviour). Team → both on, since sync implies
    commit-moves; the prompt should say in one line what team mode buys
    (moves claim and commit themselves; boards converge through
    origin/main) and that it needs a shared `origin`.
  - **Which agent adapter?** `claude` (default) or `opencode` —
    enumerated from the adapter directories rather than hardcoded, so a
    project's own `local/adapters/` shows up.
  - **What command runs this project's tests?** `BOARD_AGENT_COMMANDS`,
    defaulting to what the example ships. This is the one a headless
    agent cannot work around: a missing test command is a test the work
    agent may not run.
  - Every question takes the default on a bare Enter, and the whole
    sequence is skippable in one keystroke.
- The file written is `core/.env.example` with the answered values
  substituted into their existing lines — comments and all keys intact,
  so the rest of the settings are discoverable by opening it.
- **A non-interactive run never asks and never blocks.** No TTY on stdin
  (CI, `update.sh`, a hook, `start.sh` under a wrapper) → print one line
  saying no `.env` exists, defaults apply, and `install.py --setup` will
  ask — then carry on. `--dry-run` reports what it would ask and writes
  nothing.
- `install.py --setup` re-runs the questions on demand, and is the only
  way to get them once an `.env` exists: pre-filling from the current
  file, rewriting it in place. An existing `.env` is otherwise never
  touched — no repair, no merging in new keys.
- `start.sh` needs no logic of its own; it already calls `install.py`
  before the port dance, which is the right order (setup writes the file,
  the port branch then edits it).

**Out of scope**

- Reconciling an existing `.env` against a newer `.env.example` (new keys
  after an update). Worth doing, separate card.
- Any board-side settings UI, or making settings editable from the page.
- Changing what any setting defaults to, or making team mode the default.

## Acceptance

- [ ] Given a project with no `manager/local/.env` and a TTY, when
      `install.py` runs, then it asks the questions above and writes
      `local/.env` containing every key and comment from
      `core/.env.example` with the answers substituted.
- [ ] Given the same, when every question is answered with a bare Enter,
      then the written file is behaviourally identical to the shipped
      defaults — the board behaves exactly as it does today.
- [ ] Answering "team" writes both `BOARD_COMMIT_MOVES` and `BOARD_SYNC`
      on; answering "solo" leaves both empty.
- [ ] Given `local/.env` already exists, when `install.py` runs, then it
      asks nothing, writes nothing and reports "ok" as it does today —
      including immediately after the setup run (idempotence).
- [ ] Given no TTY on stdin, when `install.py` runs with no `.env`, then
      it prints one line and exits normally without writing or blocking —
      `update.sh` → `install.py` in a script still completes.
- [ ] `install.py --dry-run` with no `.env` writes nothing.
- [ ] `install.py --setup` with an existing `.env` asks again, offering
      the current values as defaults, and rewrites the file.
- [ ] Edge case: a first boot in a vendored project both clears the
      distribution's cards **and** writes `.env` — the first-boot guard
      still reads false on the next run and the cards stay cleared.
- [ ] Edge case: `start.sh` taking a fallback port after setup ran leaves
      the rest of the file intact and the port line correct.

## Open questions

- None.

## Notes

Writing the whole of `.env.example` rather than only the answered keys is
the deliberate choice: the file becomes the place a project reads to find
out what it can change, which is exactly what is missing today. The cost
is that it snapshots the example at install time — the drift that the
out-of-scope reconciliation card would address.

**Risks**

- **Guard ordering.** `first_boot()` returns false as soon as
  `local/.env` or `local/state/` exists. Setup writing `.env` before
  `first_boot_clean` runs would permanently skip the clean and leave
  bench's own cards in the host project. Order is load-bearing, and the
  edge-case acceptance above is what catches a regression.
- **Blocking a script.** `install.py` sits on the path of `start.sh`,
  `update.sh` and anything automated. A prompt that waits on stdin in
  those contexts hangs the board start with no output explaining why —
  hence the TTY check being an acceptance criterion rather than a nicety.
- **Turning team mode on is not free.** It needs a reachable `origin`,
  merge rights for whoever clicks merge, and the discipline that local
  main only advances through the board. The question should say so in a
  line; a project that answers "team" by accident gets a board that
  narrates sync problems it cannot fix.


---

## Work report — 2026-07-31 07:56 (Fern)

Done and committed. Closing report:

---

**The work is complete, committed on `task/29-first-run-writes-local-env` as `6352054`, and the full suite passes (`python3 -m unittest` — 320 tests, OK).** Nothing is blocked; `start.sh` needed no change, as the task predicted.

**What changed**

- `install.py` grew a setup step: with no `manager/local/.env` it asks three questions — solo or team (team turns `BOARD_COMMIT_MOVES` and `BOARD_SYNC` on together, with a note on what team mode costs), which agent adapter (enumerated from `manager/core/adapters/` plus `manager/local/adapters/`, never hardcoded), and what command runs the project's tests (`BOARD_AGENT_COMMANDS`) — then writes `manager/core/.env.example` with those answers substituted into their existing lines, every other key and comment intact. Bare Enter takes each default; Ctrl-D skips the rest and writes the defaults; Ctrl-C cancels and writes nothing. It runs after `first_boot_clean`, never asks when stdin is not a terminal (one line, then carries on), does nothing under `--dry-run` beyond reporting, and never touches an existing `.env` except under the new `--setup`, which pre-fills from the current file and rewrites in place. `--setup` is stripped from what is passed through to the adapter's `wire`.
- `install.py` also now flushes stdout before launching the adapter's `wire`, so a piped run's output is no longer out of order (the child's report used to print before the parent's).
- `manager/core/.env.example`, `AGENTS.md` ("The first run writes local/.env", plus the settings paragraph) and `README.md` document the behaviour.
- `tests/test_install_first_boot.py` gained thirteen tests covering every acceptance bullet. Interactive runs get a real pty; every other run gets `/dev/null` on stdin, which is also the non-interactive case being asserted. The existing `run_install` helper now pins `stdin=subprocess.DEVNULL` explicitly, so tests cannot inherit a developer's terminal and hang.

**Two things to look at first**

1. `install.py:437` (`setup`) — the guard ordering and the TTY check are the two risks the task names; `test_first_boot_both_clears_the_cards_and_writes_the_env` and `test_without_a_terminal_it_says_so_and_writes_nothing` are what catch a regression in each.
2. `tests/test_install_first_boot.py:run_install_tty` — the pty harness. It kills and fails loudly on a 60-second timeout, so an unanswered question surfaces as a test failure rather than a hung suite. It is POSIX-only, which matches the rest of bench.

**One judgment call worth confirming:** the solo/team question is binary, so `--setup` normalizes a hand-edited half-team file (`BOARD_COMMIT_MOVES=1` with `BOARD_SYNC=` empty). That file defaults the question to "team", and answering either way writes both keys together. That follows the acceptance criteria as written, but it means `--setup` cannot express "commit moves without sync" — a project that wants it edits the file directly, as it does today.
