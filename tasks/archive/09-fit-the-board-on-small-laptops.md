# 09 — Fit all five columns on small laptop screens

**Status:** Archived
**PR:** https://github.com/12vectors/bench/pull/8
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


---

## Work report — 2026-07-30 07:32 (Ada)

Committed and tested. The work is complete — here is the closing report.

---

The fix is committed on this branch (`e1b9a8f`) and the full suite (`python3 -m unittest discover -s tests`, 80 tests including 5 new ones) passes. Two files changed: `manager/core/board.html` and a new `tests/test_column_flex.py`.

**What changed.** The kanban columns no longer demand a fixed 296px each. In `manager/core/board.html` the `.kcol` rule went from `flex:0 0 var(--col); width:var(--col)` to `flex:1 1 0; min-width:var(--col-min); max-width:var(--col)`, and a new `--col-min:240px` design token sits beside `--col` in the `:root` block. The effect, per viewport width:

- **≥1556px** — every column caps at `--col` (296px) and the row stays left-aligned, so the board is pixel-identical to before.
- **1280–1556px** — all five columns shrink evenly (equal flex factors from a zero basis). At the 240px floor the total footprint is 5×240 + 4×10px gap + 36px board padding = 1,276px, so 1440, 1366 and 1280 viewports all fit with no horizontal scroll.
- **below ~1276px** — the pre-existing `overflow-x:auto` on `#board` takes over, unchanged.

Card internals were already width-tolerant (`min-width:0`, ellipsis, `overflow-wrap`), so no card CSS needed touching, and drag-and-drop is element-based, unaffected by column width. I also checked the header, Sessions and Focus views for 1280px cutoffs as the task asked: the Sessions rail is fixed 250px beside a `min-width:0` fluid pane, the Focus grid uses `minmax(0,…)` fractions with a 900px breakpoint, and the header flex-wraps — all pass as-is, so nothing speculative was changed.

**How it was verified.** This repo's convention for `board.html` (no frontend runner) is source-level invariant tests; `tests/test_column_flex.py` follows the pattern of `tests/test_focus_well.py` and pins: the floor is a token the `.kcol` rule actually uses; columns grow/shrink with a `--col` cap still at 296px; the arithmetic *five floors + four gaps + board padding ≤ 1280* (recomputed from the live CSS values, so a drive-by token edit that breaks the fit fails the test); a floor no lower than 240px; and the `overflow-x:auto` fallback surviving.

For the reviewer:

- Look first at the two-line CSS change in `manager/core/board.html` (lines 23 and 94) — everything else is tests.
- The one thing tests cannot prove is how it *looks*: worth a 30-second window-resize on a real 13" MacBook before merging, since that screenshot is what motivated the card.
