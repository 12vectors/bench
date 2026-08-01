# 51 — Add a card to a phase without opening the file

**Status:** To Do
**Assignee:** istos
**Priority:** Low — the convenience path; phase cards mostly arrive with
their lists already written
**Type:** Feature
**Depends on:** 48 — the list this appends to has to exist and be read

A `⟶ phase` action on a backlog or to-do card, offering the phase cards
currently in `to-do/` and appending the card to the one you pick. It is
the small path: most phases are written whole, members and dependencies
already in place, before anything reaches the board. This is for the card
you decide belongs after all.

## Context

- The phase card's `## Cards` section is the single place membership
  lives (48), so adding a card means appending one line to *that* file —
  not editing the card being added.
- The board already writes into task files where the file must stay the
  source of truth: the `**PR:**` line goes in through `commit_edit()`
  (`manager/core/taskfiles.py:242`), which commits under the same gate a
  move does and, in team mode, reaches the other boards.
- A choice between several options has a pattern too: the drag-to-`done/`
  sheet (`completeSheet`, `board.html:1362`) puts a short list of named
  outcomes in front of you rather than guessing.
- Card actions arm before they fire, and the status pill's slot holds at
  most two per state. On backlog and to-do cards that slot is close to
  free — `◔ still true?` is often the only one there.
- Task `../backlog/47-an-archive-button-on-the-card.md` puts a chip in
  the footer row for a similar reason; the two should not fight for the
  same corner.

**Affected areas:** `manager/core/board.html` (the action and its sheet),
`manager/core/httpd.py` (a route), `manager/core/taskfiles.py` (the
append).

## What to build

- **A `⟶ phase` action** on cards in `backlog/` and `to-do/` that are not
  already in a phase, and not themselves phase cards.
- **A sheet listing the phase cards in `to-do/`**, each with its number
  and title and how many cards it already holds. Picking one appends the
  card to the end of that phase's `## Cards` section and closes the
  sheet.
- **`to-do/` only.** A phase in `in-progress/` is running: its branch
  exists, its members are being worked in the order the list had when it
  started, and appending mid-flight is a different feature with different
  questions. Offer it and someone will find out the hard way.
- **The append goes through the board's own write path**, so it commits
  itself under `BOARD_COMMIT_MOVES` and syncs like every other board-made
  change to a task file. An addition that never leaves one working tree
  is not an addition the phase will run.
- **Written the way a person writes it** — `- 33 — <title>` — so the
  section stays something you would have typed. A machine-shaped line in
  a file people author by hand is how a format stops being pleasant.
- **Nothing else moves.** The card stays in its stage; joining a phase is
  not a commitment to start it, and the phase decides when it runs.
- **No phases in `to-do/` → the action is absent**, not present and
  empty. The board offers what it can do.

**Out of scope** — tempting neighbours left alone:

- Removing a card from a phase, and reordering the list. Both are file
  edits for now; both are better judged once phases have run a few
  times.
- Adding to a running phase.
- Creating a phase card from the board. Phase cards are written, and
  writing them is where their brief comes from.
- Multi-select — adding several cards in one gesture — which needs a
  selection model the board does not have.

## Acceptance

- [ ] Given a card in `backlog/` or `to-do/` and at least one phase card
      in `to-do/`, hovering offers `⟶ phase`; choosing a phase appends
      the card to that phase's `## Cards` and the member chip from 48
      appears on it.
- [ ] The appended line names the card's number and title, at the end of
      the section, and the rest of the phase card is untouched.
- [ ] With `BOARD_COMMIT_MOVES` on, the append is committed on its own,
      messaged like the board's other bookkeeping; with it off, nothing
      is committed and the file is simply edited.
- [ ] Phase cards in `in-progress/`, `review/` or `done/` are not
      offered.
- [ ] A card already in a phase does not offer the action.
- [ ] With no phase in `to-do/`, the action does not appear.
- [ ] Edge case: a phase card with no `## Cards` section yet gets one,
      rather than the line being appended to the end of the file.
- [ ] Edge case: two boards adding to the same phase produce two lines,
      not a lost one — the second append reads the file as it is on disk
      rather than as it was rendered.

## Notes

The reason this is Low and not Medium: the intended way a phase arrives
is fully formed — a card written with its members listed and each
member's `Depends on` filled in — because that is a thing you can read in
a diff and reason about before any of it runs. This action is for the
afterthought, and it should stay small enough not to become the way
phases are assembled.
