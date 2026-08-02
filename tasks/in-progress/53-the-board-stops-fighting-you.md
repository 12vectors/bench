# 53 — Phase: the board stops fighting you

**Status:** In Progress
**Assignee:** istos
**Priority:** Medium — neither member is urgent alone; together they are
the difference between watching a busy board and wrestling one
**Type:** Phase

Two small pieces of board ergonomics that both live in
`manager/core/board.html`: the reading surface should hold still while
agents work, and archiving a card should not require dragging it the
length of the board. Run as a phase because they touch the same file —
sequencing them means the second one starts from a worktree that already
contains the first, rather than colliding with it.

## Cards

- 52 — A redraw throws away where you were looking
- 47 — An archive button on the card, where the card is

## Why this order

52 first, and not because 47 needs it — neither card depends on the
other, so neither carries a `**Depends on:**` line. The order is
editorial: 52 changes how `renderBoard()` works, 47 adds a chip to the
footer row it renders. Doing the mechanical change to the render path
first, and hanging new interface off it second, is the calmer way round.
If they ran the other way the phase would still work; the diffs would
just be noisier to read.

The reason to phase them at all is the shared file. Two agents launched
independently would both branch from `main`, both edit `board.html`, and
the second would land on a merge conflict in a file it had every right
to think it owned. A phase gives the second one the first one's work as
its starting point.

## What done looks like

- A column scrolled halfway down stays there while agents emit events,
  and the board's horizontal position survives a redraw too.
- Every card in `backlog/`, `to-do/` and `done/` carries a `⌸` chip that
  arms on the first click and archives on the second, with ⌘Z still the
  undo the toast promises.
- One PR into `main` carrying both, reviewed together — they are the same
  file and the same afternoon's worth of work.

## Notes

Both members were written against the code as it stands and name the
lines they change: `renderBoard()` at `board.html:945` and the wipe at
`:947` for 52, the tray glyph at `:1581` and `ARCHIVE_FROM` in
`taskfiles.py:111` for 47. If either has drifted by the time this runs,
`◔ still true?` on the member is cheaper than finding out inside a
headless run.

This is also the first phase this board has run, so it is worth watching
rather than walking away from: what the halt conditions feel like in
practice, whether the `## Phase log` says enough to reconstruct what
happened, and whether one PR carrying two cards is the right unit for
review or one too many.

## Phase log

- 2026-08-01 17:00 · run started on phase/53-the-board-stops-fighting-you
- 2026-08-01 17:00 · 52 started
- 2026-08-01 17:15 · 52 merged into phase/53-the-board-stops-fighting-you
- 2026-08-01 17:15 · 47 started
- 2026-08-01 17:28 · halted at 47 — its run ended without reaching review/
- 2026-08-02 07:18 · run started on phase/53-the-board-stops-fighting-you
- 2026-08-02 07:18 · 47 merged into phase/53-the-board-stops-fighting-you
- 2026-08-02 07:18 · every card merged — opening the phase PR

