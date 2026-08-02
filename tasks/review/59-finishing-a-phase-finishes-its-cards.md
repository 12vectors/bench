# 59 — Finishing a phase finishes its cards, and clears up after them

**Status:** Review
**Assignee:** istos
**Priority:** Medium — without it a phase ends by handing you a pile of
cards you have already judged and a heap of checkouts nobody will
remember to delete
**Type:** Feature

When a phase's PR merges into `main`, its members' work is in `main` too —
but their cards are sitting in `review/`, where the board says they are
waiting on you, and every one of them has left a worktree and three
branches behind. **Merge & clean up** on a phase card should finish its
members too: move them to `done/`, and clear the workspaces they are done
with.

## Context

- A member stops at `review/`. The runner merges its branch into the
  phase branch and records it, and deliberately does not move the card:
  `done/` has always meant *merged into `main`*, and merged into a phase
  branch is not that. That reasoning is right and should survive this
  card.
- What makes it wrong at the end is card 56: members are hidden from the
  Board while the phase holds them, and reappear when it lets go. A phase
  reaching `done/` therefore returns three or five cards to `review/` in
  one redraw — all of them merged, none of them needing anything, and all
  of them in the column whose note is "your move".
- The operation to extend already exists and already does careful
  multi-step git work: `github.complete_task()` (`:387`) parks the drive,
  merges, cleans up the worktree and branch, then calls `move_task` for
  the one card. It narrates each step and aborts cleanly on a conflict.
- Board-made moves commit themselves under `BOARD_COMMIT_MOVES` and, in
  team mode, push — so a sweep of five cards is five commits or one, and
  which it is deserves a decision rather than an accident.
- **The cleanup half is missing entirely.** `complete_task` works from
  `stem = filename[:-3]`, so it removes the worktree and branch of the
  card being completed and knows nothing about members. Measured on phase
  53 after it finished: three worktrees still on disk
  (`.worktrees/47-…`, `.worktrees/52-…`, `.worktrees/53-…`, 3.8M each),
  both member branches still local, and both still on origin. A grep of
  `phases.py` finds worktrees created and never removed.
- An ordinary card already gets the full treatment on completion —
  worktree removed, local branch deleted, remote branch deleted after the
  push. Members should get exactly that, from the same place.

**Affected areas:** `manager/core/github.py` (`complete_task`), and
whatever narrates the result.

## What to build

- **The sweep, inside the merge.** When a phase card is completed, every
  member the phase lists moves to `done/` as part of the same operation —
  after the merge into `main` has actually succeeded, never before.
- **Clear each merged member's workspace**, the way completing an
  ordinary card clears its own: remove the worktree, delete the local
  branch, delete the branch on the remote. The member's work is in `main`
  by then, so there is nothing in any of them worth keeping.
- **Only what the phase merged.** A member that never reached the phase
  branch — halted, held, walked back — is neither moved nor cleared. Its
  card stays where it is, and so do its worktree and its branch: there is
  work in them, and it is the reason a person will look at the phase
  afterwards. The board already applies this rule to a failed run that
  committed something, and removing a worktree with work in it is the one
  unrecoverable thing in this card.
- **Narrate it as one thing.** The ticker should say a phase finished and
  how many cards went with it, not five separate moves scrolling past. A
  person watching should see one event, because one thing happened.
- **Decide the commit shape deliberately.** One commit for the sweep
  reads better in `git log` than five `board: NN → done` lines in a row,
  but the message must still say what moved; and in team mode it has to
  reach the other boards either way.
- **Abort together.** If the merge fails, nothing moves — the existing
  behaviour, extended to the members. A half-swept phase is worse than an
  unswept one.
- **The other endings do not sweep.** Archiving a phase card, or removing
  a member from its list, releases the members to the board in whatever
  stage they are actually in (card 56). Only *merging* means the work is
  in `main`, and only merging may say `done/`.

**Out of scope** — tempting neighbours left alone:

- Moving members while the phase runs. They stop at `review/` on purpose.
- Sweeping on any path other than **merge & clean up** — including "just
  move the card", which explicitly leaves the work alone.
- Closing the members' own PRs. Those were opened against the phase
  branch and are closed by their own merges.

## Acceptance

- [ ] Given a phase whose members are all merged, when **merge & clean
      up** succeeds, then the phase card and every merged member are in
      `done/`.
- [ ] The ticker reports it as one ending, naming the phase and the
      number of cards.
- [ ] Given the merge fails or conflicts, nothing moves — not the phase
      card, not one member.
- [ ] Given a member that never reached the phase branch, its card, its
      worktree and its branch are all left exactly as they are.
- [ ] Given the phase completes, no merged member leaves a worktree,
      a local branch or a branch on the remote behind — `git worktree
      list` and `git branch` name only what was there before the phase.
- [ ] Removing a worktree never discards uncommitted changes: a member
      with a dirty worktree is reported and kept, not forced.
- [ ] Given "just move the card" instead, no member moves.
- [ ] Given the phase card is archived rather than merged, its members
      reappear on the board in their own stages and none of them is
      marked done.
- [ ] With `BOARD_COMMIT_MOVES` on, the sweep is committed and, in team
      mode, published — a sweep that never leaves one working tree is not
      a sweep.
- [ ] Edge case: a member already in `done/` — moved by hand — is not
      moved again and does not fail the operation.

## Notes

This is the card that makes the ending feel like an ending. Everything
else about phases is about the middle: running, halting, resuming. The
last thing a person does is drag one card to `done/`, and what should
happen is that the whole phase goes quiet — not that five cards they have
already reviewed reappear asking for attention.

The reason it is separate from card 56 rather than folded in: 56 is about
what the *Board view draws*, and this is about what the *board does*. One
is a rendering rule, the other moves files and commits them. Keeping them
apart also keeps 56 shippable on its own.

**Why the cleanup is not cosmetic.** A stale worktree is not just disk —
`start_agent` refuses to launch a card whose worktree already exists on
the wrong branch, so a leftover from a finished phase is a trap laid for
whoever reopens that card. And the accumulation is per member per phase:
five-card phases leave five checkouts and fifteen branches each time,
until somebody notices and does it by hand.


---

## Work report — 2026-08-02 10:08 (Piper)

WORK REPORT

The task is implemented, tested and committed on `task/59-finishing-a-phase-finishes-its-cards` as one commit (`b690060`). The full suite — `python3 -m unittest discover -s tests`, this project's whole definition of done — passes: 996 tests, including 30 new ones in `tests/test_phase_finishes.py`. Nothing is left uncommitted and nothing is blocked.

**What a reviewer should look at first:** `manager/core/github.py` — `_brought()` decides which cards the ending speaks for, and `_clear_member()` is the one place that deletes work. Everything unrecoverable in this card is in those two functions.

**What changed**

- `manager/core/github.py` — `_complete()` reads the phase's merged members *before* any destructive step (the phase branch is deleted in the middle of it, and containment is the only record of a merge), then sweeps only after the merge into `main` has actually succeeded. `_brought()` requires both halves of the runner's own reading of "merged": the card settled into `review/` or `done/`, and its branch contained in the phase branch (or no branch to bring). `_clear_member()` removes the worktree *without* `--force` — it checks `git status --porcelain` first, and a dirty worktree is reported in the ticker and kept with its branch — then deletes the local branch (`-D` under sync, for the same reason the card's own branch takes it) and the branch on the remote. `_sweep()` emits the single ending line and returns the swept cards, which `complete_task` now reports as `swept` for the toast.
- `manager/core/taskfiles.py` — new `move_together()`: several cards into one stage, in one commit named `board: 47, 52 → done with phase 53 (<name>)`. `_relocate`/`move_task` gained `commit=` and `quiet=` for it; `_member_entry` became public `member_entry` (github now uses it too).
- `manager/core/state.py` / `watch.py` — `expect_move(..., quiet=True)` and `claim_move()`: a mover can say it has already narrated a move, so the watcher renders it and skips only the ticker line. Every other side effect of a move still fires. `claim_expected()` remains as the actor-only wrapper.
- `manager/core/board.html` — the merge sheet on a phase card says how many cards go with it, "just move the card" says they stay, and the toast names how many went.
- `AGENTS.md` — new section "Finishing a phase finishes its cards", plus pointers from the merge-and-clean-up paragraph and from card 56's un-hiding rule.

**Two decisions worth confirming, both made deliberately**

- **The commit shape is one commit for the sweep, separate from the phase card's own move.** So `git log` reads `board: 31, 32 → done with phase 40 (tester)` followed by `board: 40 → done (tester)`. The card the person dragged still moves as itself; the cards that moved because it did move as one thing.
- **A member with a dirty worktree still moves to `done/`** — its work is in `main`, so the card is finished; only the workspace is kept, and the ticker says which one and why. If you would rather such a card stayed in `review/`, that is a one-line change in `_sweep()`.

**Not swept, on purpose:** a member that halted, was held or was walked back out of `review/`; a member the phase never reached; every card when the merge conflicts (the existing abort covers the sweep because it runs after the merge). Tests cover each, plus a member hand-moved to `done/` (cleaned, not moved twice), the team-mode path where `main` has not caught up with the merge origin made, and the remote branch actually disappearing from a real bare origin.
