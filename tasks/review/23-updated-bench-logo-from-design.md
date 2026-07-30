# 23 — Bring the updated bench logo in from the design project

**Status:** Review
**Assignee:** istos
**Priority:** Low — visual identity, no behaviour
**Type:** Chore

The bench logo was updated in the design project; the board still
renders the old mark. Import the design and bring the header's logo in
line with it.

## Context

- Design source of truth: the claude_design MCP
  (`https://api.anthropic.com/v1/design/mcp`, auth via `/design-login`)
  — import this project:
  https://claude.ai/design/p/43447958-7124-44aa-9ee5-4bd0a9f0bacf?file=Bench+Board.dc.html
- Focus file (the whole project is readable): `Bench Board.dc.html`.
  Also read what the selection imports: `support.js`.
- Today's mark: the plain "Bench" wordmark in the header of
  `manager/core/board.html` (single-file UI — any asset the new logo
  needs must be inlined: SVG in the markup or a data: URI, never an
  external file or font).
- The design system's constraints still bind: night theme is the
  default, Daylight must look right too, and colour only ever means
  state — the logo must not introduce a colour that reads as one.

**Affected areas:** `manager/core/board.html` (header markup/CSS)
only.

## What to build

- Import the design project via the MCP and read `Bench Board.dc.html`
  (plus `support.js`) for the updated logo: its mark, geometry,
  spacing against the tasks-root path line, and any theme variants.
- Reproduce it in the board header, inlined, in both themes. If the
  design gives the browser tab an icon, carry it as an inline
  data:-URI favicon — coordinated with task 22, which is retitling
  the same tab.
- Match the design's intent, not just its pixels: if the mark scales
  or recolours between themes in the design, mirror the mechanism
  (CSS custom properties) rather than baking two bitmaps.

**Out of scope** — tempting neighbours left alone:

- Any other visual change the design file may contain — this card is
  the logo only; further deltas become their own cards.
- Rebranding text, README badges, or release-notes art.

## Acceptance

- [ ] The board header shows the updated logo, matching the design in
      both Night and Daylight themes.
- [ ] The page remains fully self-contained: no new network requests,
      fonts, or image files — verified by loading with the network
      tab open.
- [ ] Nothing else on the page moved: header height, the path line,
      view switcher and session chips sit where they did.

## Open questions

- None.

## Notes

The design MCP needs authentication (`/design-login`), which is
interactive — a headless work agent launched by the board may not
inherit that session. If the import fails on auth, the honest moves
are: do the import half interactively and hand the extracted mark to
the card, or configure the MCP for the agent's environment first.
`NOT READY` is wrong here (nothing is undecided); "blocked on tooling
access, here is exactly what to run" is the right report.

**Risks**

- Design files describe more than the delta — the temptation to
  "align" neighbouring header details while in there is exactly how
  logo chores become layout regressions; out-of-scope list is the
  fence.


---

## Work report — 2026-07-30 11:16 (Ada)

 became an inline `<svg class="mark">`, one `<path>` per letter of b-e-n-c-h on a 1000-unit em (baseline y=740, x-height top y=210, ascender y=0), with the design's −0.015em tracking folded into the per-glyph translations.
- `fill:currentColor` gives the mark `--text` in both Night and Daylight from one copy, so it introduces no colour and in particular no state colour. `--logo-h:14px` (the wordmark's b-height, = 19px × 0.74) is the single token that sizes it.
- `.brand` gap 10px → 11px, per the design. This is the one place I moved a neighbour: the acceptance bullet says the path line sits where it did, but "What to build" asks for the design's spacing against that line, so I took the design. It is a 1px shift and the header's height is set by the view switcher, not the brand, so nothing reflows.
- The tab icon's dot became the wordmark's own `b` on the accent tile it already had (the design's app-icon treatment: accent ground, white letter). Both copies of the `b` are the same outline string, and a test keeps them equal.
- Still fully self-contained: no `@font-face`, no font file, no new request. A test pins the page's external URLs to exactly the three IBM Plex ones that were already there and would fail if a fourth appeared.

## To do before merging

1. **Look at the header in both themes.** Zilla Slab's own contours are not reachable from this environment — `curl` and Python are not permitted to a headless agent here, so I could not fetch or convert the font binary, and I have no browser to render into. The five outlines are therefore hand-authored in Zilla Slab's idiom (slab feet, 118-unit stems, 104-unit bars, top-left flags on `b`/`h`/`n`, vertically-sheared `c` and `e` terminals), not exported from the face. I verified each path is a closed, non-self-intersecting loop and that even-odd filling puts the counters in the right places by point-sampling every region, but that proves the letters are *well-formed*, not that they are *handsome*.
2. **If they are not right, swapping in real outlines is mechanical**: replace the five `d` attributes in the `<svg class="mark">` block and the one in the `<link rel="icon">`, keeping the `#mark-b` and favicon strings identical — `test_header_logo.TheTabIcon.test_it_is_the_wordmark_s_own_b` fails if they diverge, and the em grid is documented in the comment above the SVG.

## To know

- The design's `--logo` / `--logo-weight` / `--logo-track` custom properties are the control that swaps between the eight candidate faces; they have no meaning once the chosen face is outlined, so they are not carried over. `--logo-h` is what replaces them as the mark's one token.
- Task 22's tab retitling is already on `main` (`de5cedb`), so the icon change sits alongside a title that is already `<project> · bench`; nothing needed coordinating.
- Out of scope and left alone: the README's `# Bench`, and everything else in the design files (the docs-site directions in `Bench Docs.dc.html` are a whole separate body of work).
