# 09 — Fit all five columns on small laptop screens

**Status:** To Do
**Priority:** Medium — daily-driver hardware; the Done column is cut off on a 13" MacBook
**Type:** Bug

The kanban's five columns are fixed-width and overflow the viewport on
small laptops: on a 13" MacBook the Done column bleeds off the right
edge and the board falls back to horizontal scrolling. Columns should
flex to share the width on laptop-class screens. This is bounded
adaptation, not full responsiveness — the target range is small laptops
(≈1280–1512 CSS px wide), nothing smaller.

## Context

- `manager/core/board.html:23` — `--col: 296px`, a fixed token.
- `board.html:94` — `.kcol{flex:0 0 var(--col);width:var(--col)}`: five
  rigid columns. Total footprint: 5×296 + 4×10 gap + 36 board padding ≈
  **1,556px**, versus ~1,440–1,470 CSS px on 13" MacBooks and 1,366 on
  an 11" Air. `#board`'s `overflow-x:auto` (line 93) is the only
  accommodation today.
- Card internals are already width-tolerant (`min-width:0`, ellipsis,
  `overflow-wrap` throughout), so columns can shrink without breaking
  card layout — the rigidity is only at the column level.
- Affected areas: `board.html` CSS only (the kanban view); no server
  change, no markup change expected.

## What to build

- Let columns yield: `flex: 1 1 0` with `max-width: var(--col)` so wide
  screens look exactly as today (columns cap at 296px, same as now) and
  narrower viewports shrink all five evenly. Set a floor
  (`min-width` ≈ 240–250px) below which shrinking stops — at 250px the
  five-column footprint is ~1,326px, fitting an 11" Air; at 240px it
  fits 1,280px viewports. Below the floor, the existing
  `overflow-x:auto` remains the fallback, unchanged.
- Keep the design system intact: the floor becomes a token next to
  `--col` (e.g. `--col-min`) at the top of `board.html` with the other
  tokens.
- Sanity-check the other two views and the chrome at 1280px: header
  (path + view switcher + session chips), Sessions timeline, Focus
  panels — they use fluid layouts already and likely just pass; fix
  only actual cutoffs found, nothing speculative.

Out of scope: tablet/phone layouts, reflowing columns into multiple
rows, touch interactions, and any redesign of card content. The board
is a desktop tool; this card only makes it honest on small desktops.

## Acceptance

- [ ] At 1440, 1366 and 1280 CSS px viewport widths, all five columns
      are fully visible with no horizontal scroll on the Board view.
- [ ] At ≥1556px the board is pixel-identical to today (columns at
      296px, left-aligned as now).
- [ ] Cards remain legible at the floor width: title wraps, footer
      chips and the author row truncate gracefully, no overlapping
      glyphs. Drag and drop still works on shrunken columns.
- [ ] Sessions and Focus views show no horizontal cutoff at 1280px.

## Open questions

- None.

## Notes

Measured from a screenshot on the owner's 13" MacBook (2026-07-29):
Done column clipped mid-card at the right viewport edge. Related in
spirit to 04 (the activity log grip) — both are the board's chrome not
keeping up with real usage; unrelated in code.
