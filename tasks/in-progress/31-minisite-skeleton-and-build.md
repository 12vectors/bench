# 31 — Stand up site/ and generate its pages from the repo's own markdown

**Status:** In Progress
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
