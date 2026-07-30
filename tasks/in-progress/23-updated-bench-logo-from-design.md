# 23 — Bring the updated bench logo in from the design project

**Status:** In Progress
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
