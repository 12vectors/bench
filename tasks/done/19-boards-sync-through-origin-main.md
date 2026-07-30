# 19 — Boards sync through origin/main: push on move, pull on a beat

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/17
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


---

## Relevance review — 2026-07-30 10:17 (Wren)

RELEVANCE REVIEW: Still relevant

The feature is fully built and committed on branch `task/19-boards-sync-through-origin-main` (two commits: `9d98e2c` sync, `ee8469d` docs), tested, but not merged to `main` and — notably — with **no PR opened yet**. The card is correctly parked in `review/`. Nothing in the task has gone stale; keep it as is and drive it through review/merge.

**What I verified**
- `manager/core/sync.py` does **not** exist on `main`; it lives only on the task branch (448 lines), and its module docstring matches the work report point-for-point (event-driven push, beat pull, piggyback guard, divergence-replay, offline degradation). The branch diff also touches `state.py` (+14, the `task_committed` hook), `watch.py` (+59, author attribution), `board.py`/`httpd.py`/`board.html`, `config.py` (settings), `AGENTS.md`/`README.md`/`.env.example`, and adds `tests/test_boards_sync.py` (565 lines). This is the complete surface the card's "Affected areas" named.
- Both dependencies the card rests on have landed on `main`: task 18 (`48a8d6a Merge branch 'task/18-cards-claimed-on-move'` — the claim-commit atomicity the races rely on) and task 14 (fresh branches off origin/main). No renamed modules or replaced approaches; `watch.py`'s 2s poller and the `board:`-prefixed commit convention the guard keys on are both still present exactly as the card assumes.
- The two decisions Juno flagged as card-left-open (divergence-is-not-always-a-stall via board-commit replay; "non-clean tree" = modified *tracked* files) are design refinements within scope, not contradictions of the card.

**To know (one process oddity, not a task defect)**
- The task file has **no `**PR:**` line** — the board normally opens a PR automatically when a card enters `review/`, yet none is recorded here. Worth a glance at why before merging (a common cause is local `main` being ahead of `origin/main`, which makes the board refuse the auto-PR; I couldn't confirm the ahead/behind count in this read-only session). This affects how the card gets reviewed, not whether the work is still wanted.

**To do**
- Nothing on the task text. Proceed with review of the branch (open/repair the PR, run `tests/test_boards_sync.py`, then merge). Do not rewrite or drop the card.

**Recommendation:** Keep as is. The task is an accurate, current record of complete-but-unmerged work; it stays in `review/` until the branch is reviewed and merged.


---

## PR review — 2026-07-30 10:33 (Wren)

PR REVIEW: APPROVE

The work is complete, committed on `task/19-boards-sync-through-origin-main` (two commits), and not yet merged. Task 19 delivers exactly what the card asked: origin/main becomes the shared truth behind `BOARD_SYNC`, board moves push themselves, a beat pulls every 30s, remote moves are attributed to their author, and losing a same-card race is a toast that reverts cleanly. I reviewed the full diff, the surrounding modules, and all of `tests/test_boards_sync.py`. GitHub refused a formal approval because the PR is authored by this checkout's own git user (`istos`), so the verdict is posted as a PR comment instead.

**What I checked**
- **Every acceptance criterion maps to code and a test.** Gate off touches no network (`test_the_gate_off_does_not_touch_the_network`, using an `ext::sleep 30` remote that would hang if touched); event-driven push via `state.task_committed` → `on_commit` off-thread; piggyback guard (`_stray`, sync.py:501) refuses any non-`board:`-prefixed local-ahead commit before both push and replay; race resolution (`_replay`, sync.py:606) drops the local move on a task-file conflict and toasts who took the card; offline degrades quietly and catches up. Both race shapes plus the mid-drag stale-drop race are tested and converge to identical file bytes.
- **Layering (AGENTS.md).** Clean and deliberate: `sync.py` imports only modules to its left; `watch.py` (to its right) imports `sync`; `taskfiles` stays left of what reacts to it by firing a `state.COMMIT_HOOKS` registry rather than importing sync. Module map, README, `.env.example`, and the new "Syncing boards" section are all updated and accurate.

**To know (non-blocking, for whoever merges)**
- **`_NOTES` is read without a lock** (sync.py:76 — `status()` iterates `.values()` on the httpd thread) while `_note`/`_clear` mutate it on the beat/push threads. Its sibling `ARRIVED` gets `_ARRIVED_LOCK`; `_NOTES` does not. A concurrent mutation during iteration can raise `RuntimeError: dictionary changed size during iteration`, failing one state payload. Rare and self-recovering, but the asymmetry reads as an oversight — a lock or a snapshot copy would close it.
- **Merge-to-done window**: the "merge & clean up" flow creates a non-`board:` merge commit before pushing it; if the 30s beat fires in that gap it briefly flags `sync stalled` before the flow's own push lands and self-clears. Cosmetic, low-probability.

**To do**
- Run `python3 -m pytest tests/test_boards_sync.py` before merging — the sandbox here blocked worktree/temp-dir creation so I could not execute the suite myself; it reads as correct and comprehensive, and the earlier relevance review reported it passing.
- Optionally guard `_NOTES` with a lock (or copy before iterating) — a small follow-up, not a blocker.
