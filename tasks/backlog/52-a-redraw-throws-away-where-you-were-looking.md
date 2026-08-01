# 52 — A redraw throws away where you were looking

**Status:** Backlog
**Priority:** Medium — a papercut that gets worse exactly as the board
gets busy: every agent event is a redraw, so the more work is running the
less readable the board becomes
**Type:** Bug

Scroll down a column — `done/` with twenty cards in it — and the moment
anything happens anywhere on the board you are back at the top. Nothing
is lost but your place, and you lose it several times a minute while
agents are working, which is precisely when you are trying to read.

## Context

- `renderBoard()` (`manager/core/board.html:945`) starts with
  `board.innerHTML = ''` and rebuilds every column and every card from
  scratch. Each column's scrolling element — `.drop`, `overflow-y:auto`
  at `:130` — is therefore a brand-new node on every pass, and a new
  node's `scrollTop` is 0.
- `render()` runs **on every SSE frame**; the comment at `:919` says so
  in as many words, and `:842` coalesces the calls into an animation
  frame. A working agent emits an event per tool use, so the board
  redraws continuously while anything is happening.
- `#board` itself is `overflow-x:auto` (`:121`), so the same wipe also
  throws away horizontal position: a wide board scrolled right to watch
  `review/` snaps back to `backlog/`.
- **The fix has a precedent in this file.** The activity log already
  survives its own redraw: `S.logStick` records whether you were pinned
  to the bottom (`:2155`) and restores that afterwards (`:1612`). And
  the drawer deliberately resets to the top *only* when the selected
  task changes (`:1489`, `:1983`) — that one is intended and must stay.
  So the codebase already distinguishes "the user moved here on purpose"
  from "this content is new"; the columns simply never got the
  treatment.
- The same wipe-and-rebuild happens elsewhere and has the same effect:
  the session timeline (`#ftl`, `:1665`, scrolling at `.tl` `:353`), the
  sessions rail, and the Focus view (`:389`).

**Affected areas:** `manager/core/board.html` — `renderBoard()` first,
and the other renderers that replace a scrolling element's contents.

## What to build

- **Carry the scroll position across a redraw.** Before the wipe, record
  each scrolling container's offset keyed by something stable — the
  stage slug for a column, the session id for a timeline — and restore
  it once the new nodes are in place, within the same frame so nothing
  flashes.
- **Board-level scroll too**, not only the columns: `#board`'s
  `scrollLeft` is the same defect and the same fix.
- **Clamp rather than guess.** A column whose cards moved on may be
  shorter than it was; restoring past the new bottom should land at the
  bottom, not throw. Content changing above the viewport will shift what
  you are looking at — that is honest and unavoidable without a
  reconciling renderer, and it is not what this card is about.
- **Do not disturb the two behaviours that are already right.** The log
  stays stuck to the bottom when it was stuck to the bottom, and the
  drawer still returns to the top when you select a different card.
- **Do the same for the other rebuilt lists** — the session timeline
  above all, where the whole point is reading back through a long run
  while it is still producing events.

**Out of scope** — tempting neighbours left alone:

- Replacing the wipe-and-rebuild with a reconciling render. That is the
  real cure — nothing would be destroyed, so nothing would need
  restoring — and it is a far larger change to the way the page works.
  See Notes.
- Any change to how often `render()` runs, or to the coalescing at
  `:842`.
- Scroll anchoring for content inserted *above* the viewport.
- The card sheet, which is not the reported problem.

## Acceptance

- [ ] Given a column scrolled halfway down, when an SSE event redraws the
      board, then the column is still scrolled halfway down.
- [ ] Given the board scrolled horizontally to the right-hand columns,
      a redraw leaves it there.
- [ ] Given an agent emitting events continuously, a column can be read
      from top to bottom without it snapping back.
- [ ] Given the activity log scrolled to the bottom, it still follows new
      lines; scrolled up, it still stays where it was put.
- [ ] Given a different card selected in the drawer, the drawer still
      returns to the top.
- [ ] Given a session timeline scrolled back through, new events do not
      return it to the top.
- [ ] Edge case: a column that loses cards while scrolled to its bottom
      lands at the new bottom rather than throwing or blanking.
- [ ] Edge case: switching views and back does not restore a stale
      position from the previous visit.

## Notes

The deeper reason this happens is that the board redraws by demolition:
every frame throws the DOM away and builds it again. That is a
defensible choice for a page this size — it is why the rendering code is
short and easy to follow, and it is why the board is never subtly out of
step with the server. Scroll position is the visible cost of it, and it
is cheap to pay separately.

If demolition ever stops being viable — text selection, focus, hover
state and animation continuity all suffer from it in the same way — the
answer is a reconciling render, not a longer and longer list of things
to save and put back. Worth a card of its own the first time a second
symptom shows up.
