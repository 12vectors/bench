# 38 — A card being merged and cleaned up looks like it, and holds still

**Status:** Review
**Assignee:** istos
**Priority:** Medium — a daily-path illusion, with a double-fire hazard
sitting behind it
**Type:** Feature

Choosing **Merge & clean up** on a card dragged to `done/` starts a long
piece of work — park the drive, merge on GitHub, remove the worktree,
delete the branch, move the card — and the card shows none of it. It sits
in `review/` looking idle for as long as the merge takes, fully
interactive, while the board is midway through disassembling its branch.
Give the card the state it is actually in, and take its actions away until
it is out of it.

## Context

- The sheet's ship handler is `manager/core/board.html:1386-1397`: it
  calls `closeSheet()`, fires one toast ("the ticker narrates each
  step"), then awaits `POST /api/task/complete` and only redraws when
  that returns.
- What it is waiting for is `github.complete_task()`
  (`manager/core/github.py:383`): stopping this task's drive and polling
  up to 20 seconds for it to die (`:404-409`), `_merge_on_origin()` or
  `_merge_locally()`, `git worktree remove --force`, `git branch -D`,
  then `move_task(... 'done')`. Only the last step changes anything the
  card renders.
- The steps *are* narrated — each records a board event against the file
  and broadcasts — so the ticker tells the story while the card
  contradicts it.
- Nothing guards a second request. `/api/task/complete`
  (`manager/core/httpd.py:197`) takes the call and starts work; the card
  is still draggable, so a second drag re-opens the sheet, and **↻ act on
  PR**, **◔ review PR**, **⛭ drive** and **↩ back** all remain armed on a
  card whose branch is being deleted.
- The vocabulary for this already exists and should be reused rather than
  reinvented: `--accent` plus the breathe animation means an agent is
  working (`board.html:207-239` carries the `rest → armed → busy` states
  for hover actions), `◌` accent chips already mark a starting agent
  (`:1088`) and a running local command (`:1115`), and a working card
  already carries a live activity line.

**Affected areas:** `manager/core/board.html` (card render, the complete
sheet, drag), `manager/core/state.py` (where shared registries live),
`manager/core/github.py` and `manager/core/httpd.py` (claim, release,
refuse).

## What to build

- **A card-level busy state, server-held.** `complete_task` claims the
  card in a registry in `state.py` before it does anything, and releases
  it in a `finally` — success, conflict or crash. `/api/state` exposes
  it, so the board renders from truth rather than from what this tab
  happens to have clicked.
- **Render it in the design's own terms.** The card takes the accent
  border and a breathing status pill in the slot the status pill already
  owns — the same language as an agent working, because that is what is
  happening. No new colour: this is not an alarm and not a settled state.
- **Say which step.** The steps are already recorded as board events
  against the file; show the latest one on the card the way a working
  card carries its activity line, so "parking the drive", "merged
  task/29-… into main" and "cleaned up: worktree and local branch
  removed" land on the card and not only in the ticker.
- **Take the actions away.** While a card is claimed: no hover actions,
  not draggable, and the drawer's actions for it inert. Suppressed, not
  merely ignored on click — an action that looks available and does
  nothing is the same lie in a different place.
- **Refuse the second request.** A `complete` for a card already claimed
  returns a readable error rather than starting a second merge, and the
  toast says the card is already being completed.
- **Release loudly on failure.** A merge conflict aborts cleanly today
  and the card stays in `review/`; it must also come fully back to life,
  with the existing error toast unchanged. A card stuck busy forever is
  worse than the problem this card fixes.

**Out of scope** — tempting neighbours left alone:

- Hover actions that already run through `rest → armed → busy`
  (`board.html:1191-1230`). They are fine; the sheet path is what skipped
  the pattern.
- What `complete_task` actually does. Not one git command changes here.
- Cross-board liveness. Agent registries stay in each board's own
  memory, and "State syncs; reactions don't" applies: a replica sees this
  card unchanged until the move arrives. Making busy states travel is a
  different design decision entirely.
- A progress bar or step counter. The steps are not uniform and their
  count is not known up front; the latest narrated line is the honest
  rendering.

## Acceptance

- [ ] Given a `review/` card with a branch, when **Merge & clean up** is
      chosen, then the card takes the busy treatment immediately — before
      the drive is parked — and holds it until it lands in `done/`.
- [ ] While busy, the card cannot be dragged, shows no hover actions, and
      its drawer actions do nothing — verified by trying each of **↻ act
      on PR**, **◔ review PR**, **⛭ drive** and **↩ back**.
- [ ] The card shows what is happening, tracking the steps the ticker
      narrates rather than a single static word.
- [ ] Given a second `POST /api/task/complete` for a card already being
      completed, when it arrives, then it is refused with a readable
      message and no second merge is attempted.
- [ ] Given a merge conflict, when the request fails, then the card
      returns to `review/` fully interactive and the failure toast is
      unchanged from today.
- [ ] Edge case: the board restarted mid-completion leaves no card stuck
      busy — the registry is in memory and dies with the process, and the
      card renders from disk as it always did.
- [ ] Edge case: a card with no branch and no PR still moves to `done/`
      with no sheet and no flash of busy — that path is untouched.
- [ ] With `BOARD_SYNC` on, a second board shows the card unchanged
      during the merge and picks up the move when it syncs.

## Notes

The gap is narrow but it is on the most consequential click on the board:
the one that merges. Everything else the board does at length — agents,
drives, commands, Copilot — already wears its work on the card. This one
path narrates into the bar and leaves the card claiming nothing is
happening.

**Risks** — the claim has to be released on every exit from
`complete_task`, including the `raise ValueError` paths for a conflict and
for a repo on the wrong branch. A `finally` around the body, not a release
after each failure point.


---

## Work report — 2026-07-31 16:57 (Otto)

Done and committed on `task/38-a-card-being-completed-looks-like-it` — three commits, working tree clean, full suite green.

**A card being merged and cleaned up now wears a server-held busy state; committed in three commits and verified with `python3 -m unittest` (617 tests, OK), 23 of them new.**

What changed:

- `manager/core/state.py` — a `COMPLETING` registry (filename → `{started, step}`) with `claim_completing` / `release_completing` / `completing_public` / `publish_completing`. `record_board_event` folds the latest summary narrated against a claimed file into its `step`, so the steps that already reach the ticker become the card's line without any caller reporting twice. It is memory only, so a board killed mid-completion leaves nothing stuck.
- `manager/core/github.py` — `complete_task` validates, claims, then runs the (unchanged) steps in a new `_complete` helper inside `try/finally`, releasing on every exit. A claim that fails raises before the `try`, so a refused second request never releases the run it lost to. Not one git command changed.
- `manager/core/httpd.py` — `/api/state` carries `completing`.
- `manager/core/board.html` — the card takes `.card.completing` (accent border, `cursor:default`), a breathing `completing` pill in the status pill's slot, and the latest step in the activity well. While claimed it builds no hover actions, drops the drive and `$`-command chips, and sets `draggable = false`; the drawer's pill reads `completing` too. A new SSE `completing` message keeps it live between full state loads, and the ship toast now says the card shows each step.
- `AGENTS.md` — a paragraph under "merge & clean up" describing the state, its scope and its release.

Two things worth a reviewer's eye first:

1. `tests/test_completing_card.py::ACompletionInFlight::test_a_second_request_mid_completion_starts_no_second_merge` — the second `complete_task` is fired re-entrantly from inside the first one's merge, which is the closest an in-process test gets to the real race; it asserts the refusal message, that only one merge ran, and that the first run still finished.
2. I could not verify this in a browser — no browser tooling is available in this session, so the card face is covered by source-level invariants in `TheCardFace` (the same approach `tests/test_card_actions.py` takes) rather than by looking at it. A human clicking **Merge & clean up** once is the check I did not do.
