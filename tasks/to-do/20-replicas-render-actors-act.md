# 20 — Replica etiquette: the actor's board acts, everyone else renders

**Status:** To Do
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
