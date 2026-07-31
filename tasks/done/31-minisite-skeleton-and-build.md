# 31 — Stand up site/ and generate its pages from the repo's own markdown

**Status:** Done
**PR:** https://github.com/12vectors/bench/pull/26
**Assignee:** istos
**Priority:** High — nothing else about the site can start until there is a
build and a place to put pages
**Type:** Feature

bench has no public page. Add a `site/` directory holding a minisite whose
content is *generated* from the files that already define bench — so a
documented behaviour and the behaviour itself cannot drift apart. This card
is the machinery and the visual foundation; the pages themselves are 33
and 34.

## Context

- The site is served from Cloudflare Workers at `bench.12vectors.com`
  (task 32). This card only has to produce a directory of static files.
- `site/` must never reach a host repo. Releases ship exactly what
  `../../manager/core/release-manifest` lists and nothing else ("Anything
  not listed here does not ship"), so adding `site/` to the repo root is
  free — but the artifact test should say so out loud rather than leave
  it to inference.
- Content sources, all already maintained: `../../AGENTS.md` (the
  workflow brief and the concepts), `../../README.md` (install, update,
  the three-layer law), `../../manager/core/.env.example` (settings),
  `../../manager/core/adapters/README.md` (the adapter contract).
- The design to build against is task 30's turn 2 of `Bench Docs.dc.html`
  in the design project — its Daylight palette, IBM Plex Sans/Mono and
  Zilla Slab display face are the visual contract.

**Affected areas:** a new top-level `site/`, plus `.gitignore` and the
artifact test in `tests/`. No `manager/` code changes.

## What to build

- **`site/build.py`** — reads a manifest, renders pages into
  `site/dist/`, copies `site/static/` alongside. One command, no
  arguments needed: `python3 site/build.py`.
- **`site/pages.json`** — the manifest, and the only place the site's IA
  is written down. One entry per route: `path`, `title`, `layout`,
  `source` (a repo-relative markdown file) and the heading slice to take
  from it (`from` heading, optional `to`). A landing page whose body is
  authored rather than sliced says so with `source: null`.
- **Loud drift detection.** A `from`/`to` heading the source no longer
  contains fails the build with the route, the file and the missing
  heading named. That failure is the whole point of generating: renaming
  a section in `AGENTS.md` must break the site build, not silently drop
  a page.
- **Templates** in `site/templates/`, one per layout from the design
  (`home`, `article` to start). Substitution uses `string.Template`'s
  `$name` placeholders — not `str.format`, whose braces collide with
  every line of CSS in the file.
- **Markdown rendering** via a pinned third-party library in
  `site/requirements.txt` (`markdown-it-py` unless the implementer has a
  better one), not a hand-rolled parser: `AGENTS.md` leans on tables,
  fenced code, nested lists and inline code, and getting those subtly
  wrong is a bad place to spend risk. The stdlib-only law binds
  `manager/core/` — the tool people install — and `site/` is neither
  shipped nor installed.
- **`site/static/site.css`** — the Daylight token set, named as
  `board.html` names its own (`--accent`, `--calm`, `--alarm`, `--idle`)
  so the two surfaces stay one system, plus the type scale from the
  design.
- **Self-hosted fonts** in `site/static/fonts/` (woff2, subset): the
  design links Google Fonts, which the shipped site must not — no
  third-party request should be needed to read bench's docs.
- **Rewritten links.** Markdown that links between repo files
  (`manager/core/adapters/README.md`, `../../plans/…`) must come out as
  working site routes or GitHub links, never as dead relative paths.

**Out of scope** — tempting neighbours left alone:

- Deployment, `wrangler`, DNS — all of task 32.
- The landing page's authored content (33) and the guide/concept page
  set (34). Ship this card with one placeholder route each, enough to
  prove both layouts render.
- Search, a changelog, versioned docs.

## Acceptance

- [ ] `python3 site/build.py` on a clean checkout writes `site/dist/`
      with a page per `pages.json` entry, and exits non-zero with a
      readable message if a source file or a named heading is missing.
- [ ] Given a heading renamed in `AGENTS.md`, when the build runs, then
      it fails naming the route and the heading — it does not emit a
      page with an empty body.
- [ ] The built pages make no third-party network requests: fonts, CSS
      and any script are same-origin, verified with the network tab open.
- [ ] `site/dist/` is gitignored; `site/` sources are committed.
- [ ] The release artifact test asserts `site/` is absent from
      `bench.tar.gz`, so a future manifest edit cannot ship it by
      accident.
- [ ] Both layout templates render with real content from `AGENTS.md`,
      not lorem — one home route and one article route is enough here.

## Notes

The IA is deliberately a manifest rather than convention-over-directory:
the pages are slices of documents written for another purpose, and the
mapping between "a section of AGENTS.md" and "a page of the site" is
editorial judgement that deserves to be visible in one file.

**Risks** — `AGENTS.md` is written for an agent working inside an
installed project ("read it too", "this file"). Some sections will read
oddly in public. Where a slice cannot stand alone, the fix is to improve
`AGENTS.md` — it is read by more agents than people — not to fork the
prose into the site.


---

## Work report — 2026-07-31 11:31 (Basil)

us contract.** The `from` heading is matched on its text (writing the `#`s pins the level too) and is *dropped* from the body — the layout renders the page title, so keeping it would say the name twice. What remains is promoted by `level - 1`, so a section's `###` sub-headings land as the page's `<h2>`s and feed the article's "On this page" list. Headings inside fenced code blocks never match, which matters: AGENTS.md fences a task template whose first line is `# Task title`.

- **Drift, verified end to end.** Renaming `## Claiming a card` in a scratch copy of AGENTS.md makes the build exit 1 with a message naming the route, the file and the heading, and write no page. The same holds for a renamed `to` heading, a deleted source file, and a slice that comes out empty. Every page renders before the output directory is touched, so a drifting rebuild leaves the previous good build standing rather than replacing a working site with a broken one — that is asserted too.

- **Links.** Repo-relative markdown links are rewritten to GitHub blob URLs, or to a site route when `link_routes` in `pages.json` maps the file to one (empty for now; task 34 will fill it). A link to a path that does not exist, or one that escapes the repo, stops the build. Neither current source section contains a markdown link — `AGENTS.md` and `README.md` have exactly two between them, both outside the sliced ranges — so this path is covered by synthetic sources in the tests rather than by the shipped pages.

- **The same-origin check, precisely.** `tests/test_site_build.py` asserts that every `<link>` whose `rel` opens a connection (stylesheet, icon, preload, preconnect, …) and every `<script|img|iframe src>` starts with `/`, that no page preconnects, and that `site/static/site.css` contains no `@import` and no `url()` outside `/static/`. `rel=canonical` is deliberately excluded — it is a statement about the page, not a fetch. This is a mechanical check on the built files, not an observation of a browser's network tab.

- **`site/` never ships.** `tests/test_release_artifact.py` gains `test_the_public_minisite_never_ships`, which asserts no `site/` member is in `bench.tar.gz`, and `tests/test_site_build.py` asserts no release-manifest entry names it. The manifest already excluded it by saying nothing about it; both tests exist because correct-by-omission is what a future manifest edit undoes silently.

- **`.gitignore`** gains `site/dist/`. That file is a `once` manifest entry, so the line reaches host repos on install, where it ignores a directory that will never exist — harmless, and the alternative (a `site/.gitignore`) would have hidden the change from the place a reviewer looks.

- **First thing to look at:** `site/build.py`'s `headings()` and `slice_section()`. Everything the card is really about — that a renamed section breaks the site rather than emptying a page — rests on those two functions being right about where a section starts and stops, including the code-fence case.
