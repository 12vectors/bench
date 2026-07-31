# 35 — Make the site read on a phone

**Status:** In Progress
**Assignee:** istos
**Priority:** Medium — a public URL gets opened on phones whatever the
design was drawn at
**Type:** Feature
**Depends on:** 33, 34 — there must be pages to make responsive

The docs design is drawn at a fixed 1180px with three-column articles and
a two-column hero. None of that survives a 390px screen, and the site's
first traffic will be a link pasted into a chat and opened on a phone.
Make every built page readable down to small handsets without redrawing
the desktop design.

## Context

- Design: `Bench Docs.dc.html` — panels 1a, 1b and 1c are all fixed-width
  desktop frames; the design has no small-screen state to copy, so this
  card decides one within its rules.
- The board solved the same problem once already: task
  `../archive/09-fit-the-board-on-small-laptops.md` is the precedent for
  how far this project bends a layout before it breaks it.
- The site's tokens and templates come from task 31; this card changes
  their CSS, not the build.

**Affected areas:** `site/static/site.css` and `site/templates/`.

## What to build

- **Article (1a)** — the two side columns are the first to go: fold the
  on-this-page list into a collapsed strip under the title, and the
  section nav into a menu the page can open. Body text keeps its
  measure; nothing horizontally scrolls except code.
- **Home (1b)** — hero and terminal stack, terminal below the claim. The
  six doors go two-up, then one-up.
- **Code and terminals** scroll inside their own container. The page
  body must never scroll sideways, on any page, at any width.
- **Tables** — the ones sliced out of `AGENTS.md` are the real hazard.
  Give them a scrolling container and a visible edge, so a reader can
  tell there is more to the right.
- **Type scale** that holds: the design's 52px hero and 40px article
  titles need a smaller step on narrow screens without losing the Zilla
  Slab display voice.
- Tap targets on every link and button that a finger has to hit.

**Out of scope** — tempting neighbours left alone:

- A night theme for the site. The board is Night-first, the site is
  Daylight-first, and reconciling them is a design question, not a
  responsive one — its own card if it is wanted.
- Redesigning any desktop layout. Desktop is the design; this is what
  happens below it.
- A mobile navigation product: one menu that opens and closes is enough.

## Acceptance

- [ ] At 390px, 768px and 1280px wide, every built route is readable and
      the page body has no horizontal scroll.
- [ ] Given a page with a wide table from `AGENTS.md`, when it is viewed
      at 390px, then the table scrolls within its own container and the
      page does not.
- [ ] The section nav and on-this-page list are reachable on small
      screens — collapsed is fine, absent is not.
- [ ] Edge case: with a long unbroken token in a code block (a URL, a
      `curl` one-liner), nothing overflows the viewport.
- [ ] The desktop rendering is unchanged from 33 and 34 at the design's
      width.

## Notes

Do this in the templates and stylesheet, with container queries or plain
media queries — nothing here justifies a script.
