# 20 — Replica etiquette: the actor's board acts, everyone else renders

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/18
**Assignee:** istos
**Priority:** High — without it, task 19 turns every board action into N duplicate side effects
**Type:** Feature
**Depends on:** 18, 19 — etiquette for a fleet that exists only once
claims and sync do

The board doesn't just render state, it reacts to it: a card entering
review opens a PR; entering in-progress arms launches. With N synced
boards watching one truth, a move must trigger its side effects on
exactly one of them — the actor's — or the team gets N PR-open
attempts, duplicate agents, and merge stampedes. And ownership must
mean something: launching work on someone else's claimed card should
be a deliberate act, not an accident.

## Context

- `github.py` opens a PR when a card enters review; today that fires
  on the board that observed the move. Under task 19, every board
  observes every move — the trigger must distinguish "I did this"
  from "this arrived".
- The `**PR:**` line already gates re-opening — the idempotency
  pattern to generalize, as the backstop behind actor-only triggers.
- One-agent-per-task lives in board memory (`state.py` registries) —
  invisible to other machines. The card file's assignee (task 18) is
  the cross-machine version.
- Merge & clean-up merges locally and pushes — in a synced team that
  fights the "main advances only through origin" discipline task 19
  documents.

**Affected areas:** `agents.py` (launch guard), `github.py` (actor-only
PR opening, merge via origin), `taskfiles.py`/`watch.py` (marking
remote-arrived moves), AGENTS.md.

## What to build

- **Remote moves are inert.** A move that arrives via sync (commit
  author ≠ this board's identity, or applied by the pull rather than
  the UI) renders and narrates but triggers nothing: no PR opening,
  no launch arming, no worktree work. Side effects belong to the
  board whose user made the move.
- **Idempotency as the backstop.** The actor-only rule prevents
  duplication; file-carried gates (`**PR:**` line before `gh pr
  create`; branch-exists checks before worktree creation) make the
  rare double harmless. Both layers, deliberately.
- **Claims gate launches.** ▸ start work on a card assigned to
  someone else refuses with who owns it; an explicit second path
  (arm-style, or clearing the assignee first) allows deliberate
  takeover — never accidental. Unassigned cards in team mode claim on
  launch, reusing 18's write.
- **Merges go through origin.** With `BOARD_SYNC=1`, merge & clean-up
  merges via `gh pr merge` (origin-side) instead of locally, then
  lets the sync beat deliver the result — local main never diverges
  by design. Single-player keeps the local merge path untouched.

**Out of scope** — tempting neighbours left alone:

- Shared liveness (a teammate's running agent stays a static
  "in-progress, assigned to X" on your board).
- Cross-machine agent registries or any coordination service beyond
  the files and origin.
- Permissions/roles — anyone can take over deliberately; git history
  is the audit.

## Acceptance

- [ ] Given A moves a card to review, when the move syncs to B, then
      exactly one PR exists and B's github poller adopts it read-only.
- [ ] Given a card assigned to A, when B clicks ▸ start work, then
      the launch refuses naming A; the deliberate-takeover path works
      and reassigns via 18's write.
- [ ] Given the rare double-fire anyway, then the file-carried gates
      make the second attempt a no-op, not an error stampede.
- [ ] Given `BOARD_SYNC=1`, when merge & clean-up runs, then the
      merge commit originates on origin and local main only ever
      fast-forwards. With sync off, the local merge path is
      byte-identical to today.
- [ ] Edge case — the actor's board dies mid-side-effect (moved the
      card, crashed before PR opened): any board can complete it
      explicitly (the review-stage card without a PR shows an "open
      PR" action), but none does so automatically.

## Open questions

- None.

## Notes

Closing card of the multi-user arc (18 claim → 19 sync → 20
etiquette). The principle worth keeping even beyond this card:
**state syncs; reactions don't.** Every future automation hung off a
stage transition inherits the same question — "am I the actor?" —
and should answer it the same way.

**Risks**

- Actor detection must survive restarts (the board that moved a card
  reboots before its PR opens) — hence the explicit-completion action
  in the last acceptance item rather than a fragile in-memory flag.
- `gh pr merge` needs merge rights on origin for every team member —
  a real-world constraint the docs must state, since the local-merge
  path hid it.


---

## Work report — 2026-07-30 11:19 (Otto)

mmitted on `task/20-replicas-render-actors-act` — two commits (`755d40d` implementation + tests, `96ec71f` docs), with `python3 -m unittest` green at 251 tests (33 of them new, in `tests/test_actor_acts.py`). Nothing is blocked and nothing was left out of scope.

**What changed**

- **Remote moves are inert.** `manager/core/watch.py` now returns `(actor, remote)` from `_actor`, and a move a pull applied narrates and renders but opens no PR. A plain `mv` on this disk still acts — inert means "happened elsewhere", not "unattributed".
- **Idempotency as the backstop.** In `manager/core/github.py` the `**PR:**` line now commits itself in team mode through a new `taskfiles.commit_edit` (sharing the move's pathspec-scoped commit machinery), so the gate travels to the other boards instead of sitting in one working tree — where it would also have stalled sync, which never runs over uncommitted changes. A `gh pr create` that races anyway adopts the PR GitHub already holds rather than erroring.
- **Claims gate launches.** `agents.start_agent` refuses a card someone else holds, naming them; the card offers **▸ take over** as the deliberate armed second path, which reassigns via `taskfiles.set_assignee`. An unheld card claims itself on launch. All of it only under `BOARD_COMMIT_MOVES`: with the gate off nothing writes an assignee, so nothing reads one as a lock.
- **Merges go through origin.** With `BOARD_SYNC=1`, `complete_task` runs `gh pr merge` and lets the sync beat deliver the result; local `main` only ever fast-forwards. Sync off keeps the local merge path unchanged.
- **The dead-actor edge case.** The startup `reconcile` stands down in team mode, and a review card with a branch and no PR carries an **↑ open PR** action (`POST /api/pr/open`) — asked for by a person, never completed automatically by another board.

**What a reviewer should look at first**

1. `manager/core/watch.py:32` — the one function that decides "am I the actor?". Every future stage-transition automation inherits it, so it is the load-bearing change.
2. `manager/core/github.py:415` (`_merge_on_origin`) — the origin-side merge, and the two constraints it exposes that the local path hid.
3. `tests/test_actor_acts.py` — two real clones of a real bare upstream plus a stub `gh`; the `elsewhere()` helper is what keeps one process's in-memory registries out of the other's evidence.

**Worth knowing (no action needed)**

- `gh pr merge` needs merge rights on the repo for every team member who clicks merge & clean up. That is a real-world constraint the local-merge path did not have; it is stated in AGENTS.md and `manager/core/.env.example`.
- The launch guard covers **▸ start work** only, not the read-only reviews or ↻ act on PR — reading someone else's card is never a stampede, and the acceptance criterion named start work specifically.
- On a review card that has a branch but no PR, **↑ open PR** takes the slot **◔ still true?** would have had, keeping to the two-actions-per-state rule.


---

## PR review — 2026-07-30 11:50 (Ada)

PR REVIEW: APPROVE

**State of the work:** The PR is complete, committed on `task/20-replicas-render-actors-act` (two commits), and by the author's report all 251 tests pass, 33 of them new. I read the full diff and all surrounding code; I could not execute the suite myself because this sandbox blocks the git/subprocess the tests spawn, so the green result is the author's report plus my read of a thorough, well-structured test file — not my own run. My approval rests on the code reading.

**What I checked and found good**
- **The load-bearing change (`manager/core/watch.py:32`) is correct.** `_actor` now returns `(actor, remote)`; `remote` is true only when a move was absent from this board's expectations *and* sync attributes it to a commit author — i.e. it arrived over origin. The PR trigger in `narrate` fires only when `not remote`. UI move acts once (expectation consumed), pulled move is inert, plain `mv` on this disk still acts. All three paths are tested, including the race-loser's reverted file returning as an inert move.
- **Idempotency backstop is real.** `_write_pr_line` commits the `**PR:**` line via `taskfiles.commit_edit` (team mode only) so the gate reaches other boards rather than stalling sync as an uncommitted change; a racing `gh pr create` adopts the existing PR (`_already_exists`/`_existing_pr`) instead of erroring; a genuine failure still raises.
- **Claims gate launches** (`manager/core/agents.py`, `_claim_for_launch`) with an armed `▸ take over` path threaded through `httpd.py`; unassigned claims on launch, your own card is untouched, off-mode reads no lock.
- **Merges via origin** under `BOARD_SYNC` (`github.py:_merge_on_origin`); the single-player path is the old code extracted verbatim as `_merge_locally`. Dead-actor edge handled: `reconcile` stands down in team mode, and the `↑ open PR` action (`/api/pr/open`) covers a half-done side effect.
- **Layering is clean** — only `taskfiles.py` touches task files; the `_commit`/`commit_edit`/`set_assignee` refactor is tidy. The merge-rights-on-origin constraint is documented in AGENTS.md and `.env.example`.

**To know (not blocking)**
- Under `BOARD_SYNC`, `complete_task` commits the move-to-done on local main while origin holds the new merge commit, so local main diverges until the next sync beat rebases the board's own commit onto origin's merge. This is task-19 sync behaviour, not introduced here — but the new tests assert only the pre-beat state and don't drive a beat to prove convergence. Worth a manual two-board smoke test before a team relies on merge & clean-up under sync.

**For the human deciding this card:** one action — either merge it yourself (a formal approving review can't come from the PR author's own identity), or have a second teammate click ◔ review PR so GitHub records an approval from a different account.
