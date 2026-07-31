# 34 — Generate the guides and concept pages from AGENTS.md

**Status:** In Progress
**Assignee:** istos
**Priority:** High — the doors on the landing page have to open onto
something
**Type:** Feature
**Depends on:** 30, 31 — the corrected design, and the build that slices
markdown

Fill the site's middle: an install guide from `README.md` and a concept
page per idea from `AGENTS.md`, rendered in design 1a (**Harbour**) — three
columns, section nav left, on-this-page right.

## Context

- Design: `Bench Docs.dc.html`, panel `1a`, as corrected by task 30. Its
  furniture — breadcrumb, lede, TL;DR callout, a table, a code block
  with a caption, a numbered walk-through, a warning callout, the
  right-hand TOC — maps onto what these sections already contain.
- `../../AGENTS.md` is the source. Its sections are already the concepts:
  Stages · Moving a task · Claiming a card · Task file format · Agents
  working the board · Pull requests · Syncing boards · Drives · Local
  commands · Agent adapters · Installing into a project · Seeing the
  board · Live view · The activity bar and the archive · Updating.
- `../../README.md` carries install, update and the three-layer law.
- The v1 IA agreed for the site is landing, guides and concepts. The
  settings and contract reference is deliberately later (36).

**Affected areas:** `site/pages.json`, the `article` template, and
`AGENTS.md` itself where a slice does not stand up in public.

## What to build

- The `article` layout: breadcrumb, title, lede, body, left section nav
  with the current page marked, right-hand on-this-page list built from
  the page's own `h2`s, and prev/next along the flow.
- Routes, each one a manifest entry naming its source and heading slice:
  - `/start` — install and first run, from `README.md`
  - `/concepts/stages` — the five directories, and moving between them
  - `/concepts/cards` — the task file format, claiming, assignees
  - `/concepts/agents` — start work, what a run does, a run that died
  - `/concepts/reviews` — PRs, review agents, Copilot, CI, merging
  - `/concepts/team` — `BOARD_SYNC`, state syncs and reactions don't
  - `/concepts/layers` — core / adapters / drivers / local, from
    `README.md`'s three-layer law plus the adapter contract's summary
- **Fix the source, not the page.** Some slices are written for an agent
  inside an installed project and will read oddly in public
  ("read it too", "this file", the directory tree). Where that happens,
  edit `AGENTS.md` so it reads well in both places — it is read by more
  agents than people, and a fork of the prose into the site defeats the
  whole approach. Where it genuinely cannot be fixed, the manifest may
  carry a short authored lede above the slice, but never a rewritten
  body.
- **Edit this page** links to the source file's section on GitHub, so a
  reader who spots a mistake lands on the file that is actually wrong.
- The screenshots the design implies are not screenshots yet: use the
  design's own terminal and card mock-ups, or nothing. A stale
  screenshot of the board is a maintenance bill.

**Out of scope** — tempting neighbours left alone:

- The reference section (36) — settings, the adapter contract, the
  driver and command contracts.
- Search, ⌘K, versioned docs, a changelog.
- Reorganising `AGENTS.md`. Fixing a sentence that reads badly in public
  is in scope; restructuring the brief is a different card.

## Acceptance

- [ ] Every route above renders in the 1a layout, with working left nav,
      TOC and prev/next.
- [ ] Each page's body is a generated slice of a repo file — no page
      body is authored twice.
- [ ] Given a section renamed in `AGENTS.md`, when the site builds, then
      it fails naming the route (inherited from 31, and worth asserting
      here on a real page).
- [ ] Every "Edit this page" link opens the file the page was built
      from.
- [ ] Edge case: a slice containing a table, a fenced code block and a
      nested list renders correctly in all three — those appear in
      Stages, Pull requests and Syncing boards respectively.
- [ ] The six landing-page doors (33) all resolve.

## Notes

If a concept needs a paragraph the brief does not have, that is a signal
`AGENTS.md` is missing it — write it there and let the site pick it up.
