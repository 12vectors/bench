# 33 — Build the landing page: Dockside, on things bench really does

**Status:** Review
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


---

## Work report — 2026-07-31 12:10 (Nell)

build now *refuses* one if it reappears, so there cannot be a second copy. `tests/test_site_landing.py` changes both sources in a scratch repo and asserts the rebuilt page moved with them, and asserts a missing `VERSION`, a renamed install heading and an install section with no command block each stop the build.

- **The terminal, which the card called the most dangerous thing on the page.** It shows `README.md`'s own command lines and then lines that `install.py`, `manager/core/board.py` and `manager/core/config.py` really print. It shows *fewer* lines than a first run does — the three question notes and the adapter's wiring report are cut — and it says so in its own title bar (`first run · abridged`) rather than passing an excerpt off as the whole output. `TheTerminalIsATranscript` in `tests/test_site_landing.py` holds each shown line against the source file that prints it, so rewording a prompt in `install.py` fails the suite.

- **A dead internal link is now a build failure.** `check_links()` runs after every page renders and before anything is written, so a bad link leaves the previous good build standing, exactly as heading drift already did. This is what makes "the doors must not 404" enforceable rather than a review checklist item.

- **The strip is the version, not analytics.** Turn 1's "most opened this week" is gone; the strip carries `0.2-alpha` read from `VERSION`, a sentence about `update.sh` and `BENCH_REF`, and a link to the releases page. I did not fetch the latest release at build time: that would put a network call in the build and make the output depend on when it ran.

- **Head tags.** `/` carries its own `<title>`, description, canonical, and `og:type`/`og:site_name`/`og:title`/`og:description`/`og:url` plus `twitter:card=summary`. There is deliberately no `og:image` — the site has no raster art, and a broken image url is worse than none. The other two layouts (`article`, `notfound`) were left as they were; extending the card set to them is task 34's or 35's call.

- **Two changes outside the landing page you should expect in the diff.** The home page no longer carries a generated body, so the tests in `tests/test_site_build.py` that read stage content off `/` now read it off `/concepts/stages/`; and the scratch repos in `tests/test_site_build.py` and `tests/test_site_deploy.py` copy `manager/core/VERSION` alongside the markdown, because every build now reads it. I also changed the terminal's `word-break:break-all` to `overflow-wrap:anywhere` in `site/static/site.css`, so the 72-character release url still wraps but the transcript's prose lines no longer break mid-word.

- **First thing to look at:** `site/build.py`'s `install_command()` and `render_command()`. Everything about this card that matters — that the command on the front page is the command in `README.md`, prompt markers and all — rests on those two being right about where the block starts, where it ends, and which lines are continuations rather than new commands.
