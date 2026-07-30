# 04 — Make the activity log's resize grip actually resize

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/14
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


---

## Work report — 2026-07-30 08:22 (Finch)

The fix is committed and the full suite passes. Closing report:

---

The fix is committed on this branch as `f9d55ef` and the full test suite passes (153 tests, including 6 new ones). Dragging the activity log's grip now actually resizes the panel, the dragged size is what gets saved, and the saved size is what gets restored.

**What changed.** Two coupled edits in `manager/core/board.html`, plus a new test file:

- The `#logbody` rule (line 239) changes from `flex:1` to `flex:none`, so the inline `height` the drag handlers write is authoritative again — `flex:1` had the flex algorithm sizing the element from a zero basis and ignoring the height property entirely. A `max-height:60vh` guard (mirroring the drag clamp's ceiling) prevents overflow from a huge saved value or a shrunk window. Of the task's two candidate shapes, this is the CSS-side one; the handlers keep writing `style.height` as before.
- The resize block (around line 1805) now tracks the clamped height it computes in a variable, and mouseup persists that value to `localStorage['bench-log-h']` instead of re-reading `offsetHeight` — the re-read is what corrupted the stored size (856 → 156) while the write was dead. The load-time restore runs through the same clamp and writes the same `height` property, so a value saved on a taller window restores within today's 60vh.
- `tests/test_log_resize.py` adds source-level invariants in the style of `tests/test_column_flex.py` (board.html has no frontend test runner): height authoritativeness, the max-height guard, the 80px–60vh clamp bounds, the no-offsetHeight-on-save rule, restore-and-drag using one channel, and a drawer-grip non-regression — the drawer writes width on the `position:fixed` `#drawer` itself, so no flex competes there and it was never affected.

**How it was verified.** `python3 -m unittest discover -s tests -v` — all 153 pass. I confirmed the new tests fail against the old code's shape (they assert the absence of exactly the two defects: `flex:1` on `#logbody` and `offsetHeight` inside the `bench-log-h` save). Collapse/reopen keeping the size follows from the toggle only flipping the `open` class on `#logpanel` — the inline height on `#logbody` is untouched. I did not drive a live browser session from this worktree.

**For the reviewer:**

- Look first at the `#logbody` rule and the activity-log block in `manager/core/board.html` — the whole behavioral change is those two spots.
- If you want a live check: open the board, drag the grip both directions, reload, and confirm the height sticks; also drag the task drawer's edge grip once to confirm the sibling interaction.
- One judgment call to sanity-check: I dropped `min-height:0` from `#logbody` — it only mattered for flex shrinking, which `flex:none` ends, and `overflow-y:auto` keeps the automatic minimum at zero.


---

## PR review — 2026-07-30 08:29 (Olive)

PR REVIEW: APPROVE

The fix is committed (`f9d55ef`) and correctly solves the task; I approve it. The change is minimal and lands in exactly the right place. Note: I could not post a GitHub `--approve` — GitHub blocks approving your own PR and the board pushed this one under this account — so the verdict is posted as a PR comment instead. **Merging is still yours.**

To know:
- **The behavioral fix** (`manager/core/board.html`): `#logbody` goes from `flex:1` to `flex:none;max-height:60vh` (line 239). Under `flex:1` the flex algorithm sized the element from a zero basis and ignored the inline `height` the drag handlers wrote — the write was dead. `flex:none` makes `height` authoritative again; the parent `#logpanel` is a `flex:none` column, so it grows with the body and the panel visibly resizes. The resize block (~lines 1806–1824) now routes both the load-time restore and mousemove through a shared `clamp()` (80px–60vh), and mouseup persists the drag-computed `h` instead of re-reading `offsetHeight` — that re-read was the data-loss bug (saved 856 → 156).
- **All four acceptance criteria check out against the code**: drag resizes clamped 80–60vh; survives reload (restore + save use one channel and the same clamp); collapse/reopen keeps the size (the activity toggle only flips `.open`/display on `#logpanel`, leaving `#logbody`'s inline height untouched); the drawer grip is untouched and unaffected (`#drawer` is `position:fixed`, so no flex competes with its `width` write).
- **Judgment call, sound**: dropping `min-height:0` from `#logbody` only mattered for flex shrink-below-content, which `flex:none` ends.
- **Layering (AGENTS.md)**: a pure UI change confined to `core/board.html`, no cross-layer leakage.

To do (for whoever merges):
- The new `tests/test_log_resize.py` adds 6 source-level invariants in the established `test_column_flex.py` style (board.html has no frontend runner). I verified each assertion by hand against the actual code, but **could not execute the suite from this review sandbox** (worktree/fetch are blocked here). Before merging, trust the work report's 153-pass run or re-run `python3 -m unittest discover -s tests` yourself.
- Optional: a 30-second live check — open the board, drag the log grip both ways, reload, confirm the height sticks, and drag the task drawer's edge once to confirm the sibling interaction.
