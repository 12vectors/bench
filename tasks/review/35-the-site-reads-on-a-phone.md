# 35 — Make the site read on a phone

**Status:** Review
**PR:** https://github.com/12vectors/bench/pull/31
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


---

## Work report — 2026-07-31 14:58 (Piper)

contents gutter goes and its strip appears, tables become their own scrollers, and an unbreakable token in running text breaks. At 760px the sidebar goes and its menu appears, the type scale steps down (`--t-hero` 52→36, `--t-title` 40→30, and again at 480px to 31/26; `--display` stays Zilla Slab at every width), and everything a finger has to hit gets `min-height: var(--tap)` — a new 44px token. At 480px the padding tightens, the masthead and footer spacers drop so the rows wrap, body text goes 15→16px, and the hero buttons go full width.
- **`tests/test_site_responsive.py`** — new, 22 tests, four groups: every media query is a `max-width` below the design and the strips are `display:none` in the base sheet; the step that hides a column is the step that shows its strip, and the strips in the *built* pages carry the same links as the columns; a table scrolls inside itself with a `local`/`scroll` edge shadow and a long token stays inside its `pre`; and the site still ships no `.js` and no `<script>`.
- **`site/README.md`** — a "Below the design's width" section documenting the three steps and the two consequences an editor of `article.html` needs to know.

### For the reviewer, first

1. **`site/static/site.css:397`, the lede rule.** The contents strip is a sibling between the `<h1>` and the body's first paragraph even where it is `display:none`, so `.prose h1 + p` alone would silently demote the lede on every article page at desktop width. The selector now names `.menu-contents + p` too, and a test pins it. This is the one place the change could have altered the 1180px rendering.
2. **`site/static/site.css:413`, the table rule.** `display:block` is what makes a `<table>` a scroll container without a wrapper element (which would have meant changing `site/build.py`, and the card scoped this to the templates and stylesheet). It has a real cost, noted in the comment: below 1080px a table *narrower* than its column no longer stretches to fill it. No page slices a table today, so this is a rule waiting for content rather than a visible change.

### To know, not to act on

- The acceptance criteria about pixel widths (390 / 768 / 1280, no horizontal body scroll) were verified by construction and by reading, not by a browser: this environment has no headless browser, and the tests assert stylesheet and markup structure instead. Someone with `npx wrangler@4 dev --config site/wrangler.jsonc` in front of them should give the three widths a look before merge — particularly the landing page's terminal at 390px and the folded nav on `/concepts/pull-requests/`.
- `.menu-contents:not(:has(a))` hides the contents strip on a page whose body has no `<h2>`s. In a browser without `:has()` support the rule is dropped and such a page would show an empty strip; every current article page has `<h2>`s, so this is only a guard for future content.
- Out of scope and untouched, as the card asked: no night theme, no desktop redesign, no navigation product beyond the one menu.
