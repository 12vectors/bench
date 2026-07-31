# 33 — Build the landing page: Dockside, on things bench really does

**Status:** In Progress
**Assignee:** istos
**Priority:** High — the front door, and the only page most visitors read
**Type:** Feature
**Depends on:** 30, 31 — the corrected design, and the build that renders it

Turn design 1b (**Dockside**) into `/`: a terminal hero, a one-paragraph
claim, and a set of doors into the rest of the site. Everything factual on
it is generated from the repo, so the front page cannot go stale.

## Context

- Design: `Bench Docs.dc.html`, panel `1b`, as corrected by task 30 —
  terminal hero on the right, headline and two buttons on the left, six
  cards below, a strip at the foot.
- The claim to make is `../../README.md`'s opening paragraph, which
  already says what bench is in five lines: task files in stage
  directories are the only source of truth, a stdlib-only board narrates
  what happens to them, team mode makes `origin/main` the truth.
- Two facts on this page must never be typed by hand: the install
  one-liner (it is in `README.md`, and it is what people paste) and the
  version (`../../manager/core/VERSION`).

**Affected areas:** `site/` — the `home` template, the landing route in
`pages.json`, and whatever `build.py` needs to extract the two generated
facts.

## What to build

- The `home` layout, faithful to the corrected design: hero, terminal,
  the doors grid, the footer.
- **A truthful terminal.** It shows the real install one-liner followed
  by what `start.sh` actually prints on a first run — including that the
  first run asks its three questions and writes `manager/local/.env`.
  Extract the command from `README.md` rather than transcribing it; a
  landing page with a subtly wrong `curl` is worse than no landing page.
- **Six real doors**, each linking to a page task 34 generates:
  install and first run · the five stages · agents on the board ·
  PRs and review · team mode (`BOARD_SYNC`) · the three-layer law.
- **Drop the fake telemetry.** Turn 1's "Most opened this week" strip is
  analytics the site does not have and will not get. Replace it with
  something true — the current version and what changed, read from
  `VERSION` and the latest release — or drop the strip.
- Both hero buttons go somewhere real: install, and the repo.
- The page carries its own `<title>`, description and Open Graph tags —
  this is the URL that will be pasted into chats.

**Out of scope** — tempting neighbours left alone:

- The guide and concept pages the doors point at (34). Building this
  card against stub routes is expected; the doors just must not 404 by
  the time both land.
- Responsive behaviour beyond not breaking (35).
- A newsletter, a waitlist, a star counter, a demo video.

## Acceptance

- [ ] `/` renders the corrected 1b layout with bench's real palette and
      type, at the design's desktop width.
- [ ] The install command shown is byte-identical to `README.md`'s, and
      the shown version matches `manager/core/VERSION` — both verified by
      changing the source and rebuilding.
- [ ] Every link on the page resolves within the built site or to a real
      external URL; the build fails on a dead internal link rather than
      shipping one.
- [ ] Nothing on the page claims a feature bench lacks — no CLI, no
      accounts, no webhooks, no database.
- [ ] Edge case: with JavaScript disabled the page is fully readable.
      Nothing here needs a script.

## Notes

The terminal is the strongest thing in the design and the most dangerous:
a fabricated transcript is the single easiest way to lose a developer
audience's trust. Paste real output.
