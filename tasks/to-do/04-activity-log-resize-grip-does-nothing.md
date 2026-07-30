# 04 — Make the activity log's resize grip actually resize

**Status:** To Do
**Priority:** Medium — a visible, advertised control ("drag to resize") that silently does nothing
**Type:** Bug

Dragging the activity bar's grip highlights it and moves the cursor, but
the panel never changes size. The drag JavaScript is healthy; the CSS it
writes to has lost authority. Verified live against the running board
with a synthetic drag through the real handlers.

## Context

- `board.html:1669-1687` — the grip's handlers: mousedown arms, mousemove
  writes `#logbody.style.height` (clamped 80px–60vh), mouseup persists
  `offsetHeight` to `localStorage['bench-log-h']`. All of this runs — a
  dispatched drag left `style.height: 306px` on the element.
- `board.html:212` — `#logbody{flex:1;min-height:0;overflow-y:auto;…}`.
  `flex:1` is `flex: 1 1 0%`: inside the column-flex `#logpanel`
  (`board.html:186-190`, `flex:none`, no height of its own) the flex
  algorithm sizes `#logbody` from basis 0% + grow, and the inline
  `height` property is simply not consulted. Measured: `offsetHeight`
  stays 156 while `style.height` reads 306px.
- Same dead write on load: `board.html:1671-1672` restores the saved
  height into `style.height` — equally ignored.
- Compounding data loss: because mouseup saves `offsetHeight` (the
  flex-computed value, not the intended one), every drag attempt
  overwrites the remembered size with the status quo — a saved `856`
  became `156` during verification.
- The regression shape: the handlers were written for a `#logbody` whose
  `height` was authoritative; `flex:1` arrived later (plausibly when
  `#loghead` joined the panel and it became a flex column) and decoupled
  them. The drawer grip (`board.html:1647-1665`) writes `#drawer`'s
  width where no flex sizing competes — check it still works, but it
  should be unaffected.

## What to build

Re-couple the height the handler writes to the height the layout uses —
smallest honest fix wins. Two candidate shapes; pick one, don't do both:

- `#logbody` becomes `flex: 0 0 auto` (height authoritative again), with
  `max-height` guarding against overflow when content is huge; or
- keep `flex:1` and have the handler (and the load-time restore) write
  `style.flexBasis` instead of `style.height`, with grow/shrink zeroed.

Either way: the saved-height restore on load must use the same channel,
and mouseup should persist the value the drag computed, not a re-read
`offsetHeight` — that re-read is what corrupted the stored size.

## Acceptance

- [ ] Dragging the grip up and down visibly resizes the log panel,
      clamped to the existing 80px–60vh bounds.
- [ ] The chosen size survives reload (localStorage round-trip restores
      the dragged height, not a flex-computed one).
- [ ] Collapsing and reopening the log (activity bar toggle) keeps the
      chosen size.
- [ ] The drawer's width grip still works — same interaction family,
      must not regress.

## Open questions

- None.

## Notes

Verified 2026-07-29 against the live self-hosted board via a synthetic
drag through the real listeners: handlers fired, `style.height` updated,
layout ignored it, and mouseup overwrote `bench-log-h` 856 → 156.
