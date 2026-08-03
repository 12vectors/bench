# site/ — bench.12vectors.com

A static minisite whose content is *generated* from the files that
already define bench. Nothing here is transcribed prose: every page body
is a heading slice of `AGENTS.md`, `README.md` or
`manager/core/adapters/README.md` — or, where the source is not markdown
at all, built from it by a named generator (`manager/core/.env.example`
becomes `/reference/settings`) — and `pages.json` is the only place that
mapping is written down.

That is the point. Rename a section in `AGENTS.md` and this build stops,
naming the route and the heading it can no longer find. A documented
behaviour and the behaviour itself cannot drift apart when one is cut
from the other.

The landing page is the exception that proves it. Its words are authored
in `templates/home.html` (`"source": null`), so the two facts it must
never get subtly wrong are not written there at all: the install
one-liner is read out of `README.md`'s "Install into a repo" block and
the version out of `manager/core/VERSION`, and the template is offered
them as `$install_block` and `$version`.

An article page authors one sentence of its own — the lede under the
title — because a slice starts mid-document and a reader arriving from
the nav is owed a line saying what they are looking at. That is the whole
allowance. When a section reads badly on the web, the fix is the section:
edit `AGENTS.md` so it reads well in both places rather than forking the
prose into this directory.

```bash
python3 -m pip install -r site/requirements.txt   # once
python3 site/build.py                             # → site/dist/
```

`site/dist/` is gitignored — it is output, rebuilt on every deploy. The
woff2 files under `static/fonts/` are not: they are committed, so a
clean checkout builds the real typography without asking anyone for it,
and a deploy is the same bytes from any machine.

`site/` never reaches a host repo: releases ship exactly what
`../manager/core/release-manifest` lists, and it does not list this
directory. `tests/test_release_artifact.py` asserts that out loud rather
than leaving it to inference.

## Below the design's width

The docs design is drawn at a fixed 1180px. Everything narrower is the
stylesheet's business — three `max-width` steps, no script, and nothing
that takes effect at or above the width the design defines:

| Step | What goes | What replaces it |
| --- | --- | --- |
| 1080px | the contents gutter | "On this page" folds into a `<details>` strip under the title; tables become their own scrollers |
| 760px | the section sidebar | the same links open from a `<details>` menu under the masthead; the type scale steps down and every tappable thing reaches 44px |
| 480px | the desktop's padding | the masthead's spacer, so the row wraps; body text goes up a notch |

Two things follow from that and are worth knowing before editing
`templates/article.html`:

- **The two side columns are written twice** — once as the column, once
  as the strip — and both are filled from the same `$sidebar` and `$toc`,
  so the folded copy cannot say something the column does not.
- **The contents strip is a sibling between the `<h1>` and the body's
  first paragraph**, even at widths where it is `display:none`. That is
  why the lede rule in `static/site.css` names `.menu-contents + p` as
  well as `h1 + p`.

`tests/test_site_responsive.py` holds all of it: that every query is a
`max-width` below the design, that the step hiding a column is the step
showing its strip, that a table scrolls inside itself rather than
widening the page, and that the site still ships no JavaScript.

## Where the site lives

| | |
| --- | --- |
| Host | Cloudflare Workers, [static assets](https://developers.cloudflare.com/workers/static-assets/) — no script, no KV, no database |
| Account | the Cloudflare account holding the `12vectors.com` zone. `npx wrangler whoami` must list it before a deploy will work |
| Worker | `bench-site` |
| Route | `bench.12vectors.com`, a **custom domain** — Cloudflare owns the hostname at the zone level and creates the DNS record itself |
| Served from | `site/dist/`, uploaded whole on every deploy |
| Analytics | [Fathom](https://usefathom.com), site `ZPKDEHCV` — cookieless, no personal data, no consent banner. The only third party the pages touch |
| Config | `site/wrangler.jsonc` (routing) and `site/root/_headers` (caching, security) |

Deploys are run by hand, by a person, exactly as releases are
(`../release.sh`). There is no deploy pipeline and no Cloudflare token in
repository secrets; adding a GitHub Action on merge to `main` is a
separate decision with a separate cost, and a follow-up card.

## Deploying

From a clean checkout, three commands. Re-running the whole sequence is
safe: the build empties and rewrites `dist/`, and `wrangler deploy`
replaces the Worker's assets rather than adding to them.

```bash
python3 -m pip install -r site/requirements.txt   # once per machine
python3 site/build.py                             # → site/dist/
npx wrangler@4 deploy --config site/wrangler.jsonc
```

`npx wrangler@4 login` first, once per machine, against an account that
can see the `12vectors.com` zone. Paths inside `wrangler.jsonc` are
relative to that file, so the command works from anywhere in the repo.

The first deploy is the one that takes the hostname over. It creates the
DNS record for `bench.12vectors.com` and routes it to the Worker;
anything else answering on that name stops answering. Every deploy after
it is an asset upload.

### Preview it locally

```bash
python3 site/build.py
npx wrangler@4 dev --config site/wrangler.jsonc     # → http://localhost:8787
```

`dev` serves `dist/` through the same static-assets router as production,
so trailing-slash redirects and the 404 page behave as they will live.
The custom domain is ignored locally.

### After a deploy, check these four

The things this repository's tests cannot reach, because they are
answers rather than files:

1. `https://bench.12vectors.com/` serves the landing page over TLS.
2. `https://bench.12vectors.com/concepts/claiming-a-card` redirects to
   the same path with a trailing slash, and no url anywhere ends in
   `.html`. Measured, the redirect is a **307** — the host's choice, not
   a setting this repo holds, so check that it redirects at all rather
   than which code it picks.
3. A path that does not exist — `/nope/` — renders the site's own 404
   page **with a 404 status**, not the landing page with a 200.
4. `curl -sI https://bench.12vectors.com/` shows
   `x-content-type-options`, `referrer-policy`,
   `strict-transport-security` and a `cache-control` that revalidates —
   the last one coming from the host's default now, not from `_headers`,
   which is exactly why it is checked rather than assumed. Then
   `curl -sI https://bench.12vectors.com/static/site.css` must show
   `max-age=31536000, immutable` **once**, with no second `max-age`
   beside it.

## How it is served

- **One url per page.** `html_handling: "force-trailing-slash"` redirects
  `/concepts/claiming-a-card` to `/concepts/claiming-a-card/`, which is
  what the pages link and what `<link rel=canonical>` names. Pages are
  written as `<route>/index.html`, so no url ends in `.html`.
- **A real 404.** `not_found_handling: "404-page"` serves `dist/404.html`
  with a 404 status. That page is a normal manifest entry (`/404.html`,
  layout `notfound`) — the site's own design, its own nav, and a link
  back to the landing page.
- **One rule sets `Cache-Control`, and it is `/static/*`.** The host
  *concatenates* a header two matching rules both set — it does not
  override, whatever the ordering suggests. Measured live: `/*` and
  `/static/*` each setting `Cache-Control` produced
  `max-age=0, must-revalidate, max-age=31536000, immutable` on the
  stylesheet, and the first `max-age` wins in every browser, so the
  year-long cache never happened. Now only `/static/*` sets it — safe
  because the stylesheet and the icon are linked with a `?v=<hash>` of
  their own contents — and HTML takes the host's own revalidating
  default, which check 4 below confirms after every deploy.
- **Baseline headers, and one third party by choice.** `nosniff`, a
  referrer policy, a year of HSTS, `X-Frame-Options`, and a
  Content-Security-Policy of `default-src 'none'` with `'self'` for
  styles and fonts. The one origin named besides this one is
  `cdn.usefathom.com`: Fathom serves the analytics script and receives
  its pageviews. It is named under `script-src`, `connect-src` **and
  `img-src`** — the beacon is an image request, so an `img-src` that
  forgets it loads the script and blocks the pageview, with a clean 200
  on every check. It sets no cookie and collects nothing about a person,
  which is why the site still needs no consent banner — but it is a
  third party, and the policy names it rather than opening the door
  generally. A second one would fail
  `tests/test_site_deploy.py` rather than slip in.

## What is where

| Path | What it is |
| --- | --- |
| `pages.json` | The manifest: one entry per route, and the site's whole IA |
| `build.py` | The generator — slicing, drift detection, rendering, links |
| `templates/` | One `string.Template` per layout (`$name`, `$$` for a literal dollar) |
| `static/` | Copied to `dist/static/` verbatim: the stylesheet, the icon, the fonts |
| `root/` | Copied to the **top** of `dist/` verbatim: `_headers`, which the host reads and never serves |
| `wrangler.jsonc` | The Worker: assets directory, url handling, 404, custom domain |
| `requirements.txt` | `markdown-it-py`, pinned. The only dependency |
| `fetch-fonts.py` | Refetches the committed woff2 files, or adds a face |

## A manifest entry

```json
{
  "path": "/concepts/claiming-a-card/",
  "title": "Claiming a card",
  "layout": "article",
  "section": "Concepts",
  "description": "…",
  "source": "AGENTS.md",
  "from": "## Claiming a card",
  "to": "## Syncing boards"
}
```

- **`path`** starts with `/` and ends with `/`; `/x/y/` is written to
  `dist/x/y/index.html`. A route that names an `.html` file instead is
  written to exactly that path — `/404.html` is the only one, and it
  exists because the host looks for that literal filename.
- **`layout`** names a file in `templates/`.
- **`section`** groups the page in the nav and the sidebar, and puts it
  on the reading order prev/next walks. The IA is read out of this file
  in this file's order — nothing is derived from the directory layout. A
  `null` section keeps the page out of the nav, the sidebar and the flow,
  which is how the 404 page stays off all three.
- **`source`** is repo-relative, or `null` for a landing page whose body
  is authored in its template rather than sliced.
- **`from`** is the heading the slice starts at. It is matched on the
  heading's text; writing the `#`s (`## Stages`) pins the level too.
  Headings inside fenced code blocks never match. It is also what
  "Edit this page on GitHub" anchors to, so the link opens the section
  rather than the top of a 700-line file.
- **`to`** is optional. Without it the slice runs to the next heading of
  the same level or shallower.
- **`description`** is the page's meta description, and doubles as the
  visible lede under the title.
- **`lede`** is optional, and only worth setting when the sentence a
  reader should see differs from the one a search engine should. It is
  the only prose a page may author.
- **`generate`** replaces `from`/`to` when the source is not markdown.
  It names a generator in `build.py`'s `GENERATORS`; `settings` is the
  only one, and it turns an env file into a page. A page cannot be both
  a slice and a generated page, and a `generate` naming nothing stops
  the build listing what exists.

## The settings page is parsed, not transcribed

`/reference/settings` is `manager/core/.env.example` read on every
build. The file's own shape is the page's: blocks separated by blank
lines, a comment block documenting the keys directly under it, a comment
block with no keys below it kept as a remark. One `##` entry per group —
so the four `BOARD_AGENT_MODEL*` keys, which share a comment in the
file, share a heading here — opening with the file's own `NAME=value`
lines and followed by that group's comment as prose.

Two failures rather than two silences: **a key with no comment above it
stops the build**, and so does a key set twice. A settings page that
disagrees with the settings file is worse than no settings page, so
neither can happen quietly. Generated bodies are also rendered with raw
HTML off — a comment writes `<git user.name>` meaning a placeholder, and
a parser honouring HTML would swallow it.

`tests/test_site_reference.py` holds the promise a reader cares about:
add a key to `.env.example` with its comment, rebuild, and it is on the
page with its default, with nothing in `site/` edited.

## The reference layout

`/reference/*` renders in `templates/reference.html` (1c Logbook), which
is the article's three columns with the contents gutter given over to a
**console**: the page's entries once more in the machine register, each
linking to its own anchor. On the settings page those are the keys with
their defaults, one line per key; on a sliced contract page they are its
headings. Nothing about it is per-page authoring — a page whose body has
no entries renders no console and keeps the gutter's links.

The console is the same anchors "On this page" carries, which is why the
1080px step can fold the entire column away: the contents strip already
hands them back. Everything else on the page — the sidebar, the folded
menus, prev/next, "Edit this page" — is the site's furniture, not the
layout's.

The `from` heading itself is dropped — the layout renders the page title
— and what remains is promoted by `level - 1`, so a section's `###`
sub-headings land as the page's `<h2>`s. Promotion stops at `<h2>`: a
slice that deliberately runs past its own section ("Stages" through
"Moving a task") carries headings at the `from` level, and those become
`<h2>` peers rather than a second `<h1>` on a page that already has one.

## What fails the build

Each of these exits non-zero with a message naming the route:

- a `source` file that no longer exists;
- a `from` or `to` heading the source no longer contains;
- a slice that comes out empty;
- a `generate` naming no generator, or set on a page that is also a
  slice or has no source;
- a setting in `.env.example` with no comment above it, one set twice,
  or a line there that is neither a comment nor `NAME=value`;
- a markdown link to a repo path that does not exist, or that escapes
  the repo — a dead relative link must never reach the site;
- an internal link on any rendered page — a door on the landing page as
  much as a link inside a slice — that resolves to nothing this build
  writes;
- a missing or empty `manager/core/VERSION`, or a `README.md` whose
  "Install into a repo" section has lost its command block;
- a `version` key in `pages.json`, which would be a second copy of a
  number that has one home;
- a template placeholder the builder does not supply;
- a file in `root/` that a route would also write.

Repo-relative links that *do* resolve are rewritten: to a site route if
`link_routes` maps the file to one, otherwise to the file on GitHub.
