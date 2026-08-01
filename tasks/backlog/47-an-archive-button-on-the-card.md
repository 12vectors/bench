# 47 — An archive button on the card, where the card is

**Status:** Backlog
**Priority:** Medium — archiving works, but only by dragging a card the
length of the board to a tray at the far corner
**Type:** Feature

Today the only way to archive a card is to drag it onto the activity bar.
That is a long gesture for a common tidy-up, it is awkward on a trackpad,
and it is invisible to anyone who has not discovered it. Put the same
action on the card itself: an archive chip at the right-hand end of the
footer row, arming on the first click and firing on the second.

## Context

- The tray is `#tray` in `manager/core/board.html:587`, rendered at
  `:1581` with the glyph **`⌸`** (it swaps to `↓` while a card is over
  the bar). That glyph is the one the card's chip must wear — the whole
  point is that the two are visibly the same action.
- The rule about which cards may be archived already exists and is
  already enforced server-side: `ARCHIVE_FROM = {"backlog", "to-do",
  "done"}` in `manager/core/taskfiles.py:111`, with `archive_task()`
  refusing anything else at `:123` ("archive takes cards from backlog,
  to-do or done only"). `AGENTS.md` says the same in prose: cards in the
  working stages cannot be archived — finish or walk them back first.
- The routes exist: `POST /api/archive` and `/api/unarchive`
  (`manager/core/httpd.py:200`, `:211`), with `state.LAST_ARCHIVED`
  behind the ⌘Z undo the toast promises.
- The arm-then-fire pattern is built and general:
  `actLabel(rest, confirm, busy)` and `armAction()` (`board.html:1191`
  onwards) drive it, and the stylesheet already anticipates a chip using
  it — `button.chip2.armed` is styled at `:240` alongside the hover
  actions.
- The footer row is where `.chip2` lives: CI, copilot, PR, drive, and the
  `$` local-command chips.

**Affected areas:** `manager/core/board.html` — the card's footer row and
its click handling. No server change: the API and its guard are done.

## What to build

- **An archive chip at the right-hand end of the card's footer row**,
  wearing `⌸`. Quiet in its resting state — this is a tidy-up, not a
  destination — and taking the `--alarm` register when armed, exactly as
  the hover actions do.
- **Only on `backlog/`, `to-do/` and `done/` cards.** On in-progress and
  review cards it is not there at all, rather than present-and-refusing:
  the board's habit is to offer what it will do. `ARCHIVE_FROM` is the
  list; read it from the state the board already has rather than
  hard-coding a second copy in the page.
- **Arm on the first click, archive on the second**, through the same
  helper the other two-step actions use. Nothing about archiving should
  invent its own confirmation.
- **The undo stays the promise it is.** The existing toast — ⌘Z brings it
  back — must fire from this path too, because it is the sentence that
  makes a one-click archive safe.
- **The tray's count follows**, since it is the same action reaching the
  same place.

**Out of scope** — tempting neighbours left alone:

- The drag-to-the-bar gesture, which keeps working exactly as it does.
- Archiving from in-progress or review, by any route.
- Bulk archive, or archiving from the card sheet.
- What archiving does in git. Card 44 settled that: an archive goes out
  through `_relocate` like every other board-made write to a task file,
  attributed and committed under the same gate a move is.

## Acceptance

- [ ] Given a card in `backlog/`, `to-do/` or `done/`, when it is
      rendered, then its footer row ends with a `⌸` chip.
- [ ] Given a card in `in-progress/` or `review/`, no such chip appears.
- [ ] Clicking the chip once arms it visibly and archives nothing;
      clicking again archives the card, and it leaves the column.
- [ ] An armed chip disarms itself the way the other armed actions do,
      rather than staying armed until the page is reloaded.
- [ ] The toast after archiving names ⌘Z, and ⌘Z brings the card back to
      the stage it came from.
- [ ] The archive tray's count goes up by one.
- [ ] Edge case: a card archived from the board by someone else — or by
      hand on disk — while the chip is armed does not leave a stale armed
      chip behind or fire a second archive.
- [ ] Dragging a card to the bar still archives it.

## Notes

Nothing blocks this. **Card 44** already made an archive commit itself,
which is what makes a one-click archive safe to offer: before it, this
card would have turned an occasional uncommitted deletion into one a
click away, and in team mode an uncommitted deletion is what stops sync
publishing anything at all.

**Risks** — the footer row is described in `AGENTS.md` as carrying tool
chips, which are "destinations, not statuses", while actions live in the
status pill's slot on hover. An action chip in the footer is a
deliberate exception to that division: the pill's slot is capped at two
actions and already spent on most of these cards, and the card asks for
this control to sit bottom-right. If it reads as noise on a full board,
the fallback is to reveal it on hover like the other actions rather than
to move it back into the pill.
