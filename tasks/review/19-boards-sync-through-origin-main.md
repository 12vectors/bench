# 19 — Boards sync through origin/main: push on move, pull on a beat

**Status:** Review
**Assignee:** istos
**Priority:** High — this is the multi-user feature; 18 without it is bookkeeping
**Type:** Feature
**Depends on:** 18 — the claim commit is what gets pushed, and its
same-commit atomicity is what makes the races below resolve correctly

One machine's `tasks/` is the truth today; everyone else's is stale
until someone remembers to push and pull. Make origin/main the truth
and every board a converging replica: board-made moves push
immediately, every board pulls on a short beat, remote moves appear in
the ticker attributed to their author, and losing a same-card race is
a toast, not a mystery.

## Context

- `watch.py` polls the stage directories every 2s — remote changes
  that arrive via pull are already noticed and narrated; today they
  would read "disk", this card upgrades them to the commit author.
- Task 14 (landed) already points fresh agent branches at
  origin/main; this card gives the *board state* the same treatment.
- The precondition that makes pulling safe is bench's own discipline:
  code work lives in worktrees and PRs, so the main checkout stays
  clean and fast-forwardable. Team mode assumes — and the docs must
  say — that local main advances only through the board and origin.
- Push publishes every local-ahead commit, not just the board's —
  the piggyback hazard below is the sharpest edge in this card.

**Affected areas:** a new small `sync.py` (or a sibling thread beside
`watch.py`), `config.py` (settings), `state.py`/ticker attribution,
AGENTS.md (team-mode discipline).

## What to build

- **Gate**: `BOARD_SYNC=1` (default off; implies `BOARD_COMMIT_MOVES`).
  Off = today's behaviour exactly.
- **Push, event-driven**: after each board-made task commit, push. On
  non-fast-forward: fetch, rebase the board commits, push again. If
  the rebase conflicts on a task file, the local move loses: revert
  it, re-read the remote version, and toast who took it
  ("07 claimed by elena — your move was undone").
- **Piggyback guard**: before any auto-push, every local-ahead commit
  on main must be `board: `-prefixed. Anything else → no push, one
  clear ticker warning naming the stray commit. Never publish a
  human's unpushed work as a side effect.
- **Pull, periodic**: fetch + fast-forward-only merge every
  `BOARD_SYNC_INTERVAL` (default ~30s) and once at startup. Never
  pull into a non-clean tree or past a divergence — narrate and wait
  for a human instead. The watcher then narrates arrived moves with
  the commit author's name instead of "disk".
- **Offline**: unreachable origin degrades to local-only silently
  sane — commits queue, a quiet ticker note says sync is behind,
  next successful fetch catches up. No errors every 30s.

**Out of scope** — tempting neighbours left alone:

- Reacting to synced state with side effects — replicas render only
  (task 20 owns the etiquette).
- Syncing `local/state/` — liveness stays per-board by design.
- Any transport other than git via origin; any branch other than main.

## Acceptance

- [ ] Given two clones with `BOARD_SYNC=1`, when A moves a card, then
      within one beat B's board shows the move and B's ticker
      attributes it to A's git name.
- [ ] Given both move the same card in one window, then exactly one
      claim survives on origin, the loser's board reverts with the
      take-over toast, and both converge to the same file bytes.
- [ ] Given a human's non-board commit sitting on local main, when
      the board would push, then it refuses with the named warning
      and the human's commit stays private.
- [ ] Edge case — origin unreachable: boards keep working locally,
      one quiet ticker note, full convergence after connectivity
      returns; no error spam.
- [ ] With the gate off, no fetch, no push, no behaviour change.

## Open questions

- None.

## Notes

Middle card of the multi-user arc (18 claim → 19 sync → 20 etiquette).
Git is the lock server and main the linearizer: push races are the
concurrency control, which is why the claim must ride in the move's
own commit (18) and why replicas must not react (20).

**Risks**

- ff-only pulls plus the piggyback guard mean a divergent main stalls
  sync until a human resolves it — correct, but the stall must be
  loudly visible or two halves of a team quietly stop converging.
- A 30s beat × N boards is N fetches/min against origin — fine for
  GitHub, worth a doc line for rate-limited setups.
- Reverting a lost move while the user is mid-drag needs care in the
  UI — the board re-reads disk on every render, which should make
  this safe, but test the race deliberately.


---

## Work report — 2026-07-30 09:48 (Juno)

sts, including 34 new ones. Nothing is pushed or merged.

### What changed

- **`manager/core/sync.py` (new)** — the whole feature, gated on `BOARD_SYNC`. Push is event-driven; pull is a beat (`BOARD_SYNC_INTERVAL`, default 30s, plus once at startup). A purely-behind board fast-forwards; a diverged one replays its own `board: ` commits on top and never merges past the divergence. A replay that conflicts on a task file drops the local move and toasts `07 claimed by elena — your move was undone`. The piggyback guard sits in front of every push *and* every replay. Unreachable origin, uncommitted changes, a checkout off main and a stray commit each narrate once and hold a header chip rather than repeating every beat.
- **`manager/core/state.py`** — a `COMMIT_HOOKS` registry plus `task_committed()`; `taskfiles.py` fires it after a move's commit. This is how push stays event-driven without `taskfiles` importing anything to its right in the module map.
- **`manager/core/watch.py`** — moves that arrived over origin are attributed to the commit's author instead of `disk`; the narration moved out of the polling loop into a testable `narrate()`.
- **`manager/core/board.py` / `httpd.py` / `board.html`** — the beat and hook start only with the gate on; `sync.status()` rides the state payload; a header chip appears *only* when sync stops converging; the SSE stream gained a `toast` type so the server can address the person, not just the ticker.
- **`AGENTS.md`, `README.md`, `manager/core/.env.example`** — a "Syncing boards" section covering the two disciplines team mode assumes (local main advances only through the board and origin; sync never merges), plus the fetch-rate note for metered remotes.

### What a reviewer should look at first

1. `manager/core/sync.py:168` (`_publish`) and `:262` (`_replay`) — the push guard and the race resolution are where this card either works or quietly loses someone's work.
2. `tests/test_boards_sync.py` — every case runs two real clones of a real bare upstream against real git, including both race shapes (same target and different targets), the stale-drag race the card flagged as a risk, and the "gate off touches no network" case (proved with a remote that never answers).
3. `manager/core/state.py:26` — the hook registry, if you disagree with that being the way to keep `taskfiles` left of `sync` in the module map.

### Two decisions I made that the card left open

- **A divergence is not always a stall.** The card says never to pull past a divergence. When *every* local-ahead commit is a board commit, the board rebases them instead of stalling — that replay *is* the race resolution, and the piggyback guard means a human's commit still stalls it loudly. A pure "always stall" reading would leave a board that queued moves while offline stuck until someone moved a card.
- **"Non-clean tree" means modified tracked files.** Untracked files do not stall sync; a stray scratch file on someone's disk should not stop a team converging.
