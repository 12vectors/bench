# site/ — bench.12vectors.com

A static minisite whose content is *generated* from the files that
already define bench. Nothing here is transcribed prose: every page body
is a heading slice of `AGENTS.md`, `README.md`,
`manager/core/.env.example` or `manager/core/adapters/README.md`, and
`pages.json` is the only place that mapping is written down.

That is the point. Rename a section in `AGENTS.md` and this build stops,
naming the route and the heading it can no longer find. A documented
behaviour and the behaviour itself cannot drift apart when one is cut
from the other.

```bash
python3 -m pip install -r site/requirements.txt   # once
python3 site/fetch-fonts.py                       # once, needs network
python3 site/build.py                             # → site/dist/
```

`site/dist/` is gitignored — it is output, rebuilt on every deploy.

`site/` never reaches a host repo: releases ship exactly what
`../manager/core/release-manifest` lists, and it does not list this
directory. `tests/test_release_artifact.py` asserts that out loud rather
than leaving it to inference.

## Where the site lives

| | |
| --- | --- |
| Host | Cloudflare Workers, [static assets](https://developers.cloudflare.com/workers/static-assets/) — no script, no KV, no database |
| Account | the Cloudflare account holding the `12vectors.com` zone. `npx wrangler whoami` must list it before a deploy will work |
| Worker | `bench-site` |
| Route | `bench.12vectors.com`, a **custom domain** — Cloudflare owns the hostname at the zone level and creates the DNS record itself |
| Served from | `site/dist/`, uploaded whole on every deploy |
| Config | `site/wrangler.jsonc` (routing) and `site/root/_headers` (caching, security) |

Deploys are run by hand, by a person, exactly as releases are
(`../release.sh`). There is no deploy pipeline and no Cloudflare token in
repository secrets; adding a GitHub Action on merge to `main` is a
separate decision with a separate cost, and a follow-up card.

## Deploying

From a clean checkout, four commands. Re-running the whole sequence is
safe: the build empties and rewrites `dist/`, and `wrangler deploy`
replaces the Worker's assets rather than adding to them.

```bash
python3 -m pip install -r site/requirements.txt   # once per machine
python3 site/fetch-fonts.py                       # once per checkout
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
   `.html`.
3. A path that does not exist — `/nope/` — renders the site's own 404
   page **with a 404 status**, not the landing page with a 200.
4. `curl -sI https://bench.12vectors.com/` shows
   `x-content-type-options`, `referrer-policy`,
   `strict-transport-security` and a `cache-control` that revalidates.

## How it is served

- **One url per page.** `html_handling: "force-trailing-slash"` redirects
  `/concepts/claiming-a-card` to `/concepts/claiming-a-card/`, which is
  what the pages link and what `<link rel=canonical>` names. Pages are
  written as `<route>/index.html`, so no url ends in `.html`.
- **A real 404.** `not_found_handling: "404-page"` serves `dist/404.html`
  with a 404 status. That page is a normal manifest entry (`/404.html`,
  layout `notfound`) — the site's own design, its own nav, and a link
  back to the landing page.
- **Two caching policies, because there are two kinds of file.** HTML
  revalidates on every view, so a deploy is visible on the next reload
  with nobody clearing anything. Everything under `/static/` is kept for
  a year and never re-checked, which is only safe because the stylesheet
  and the icon are linked with a `?v=<hash>` of their own contents:
  change the file and the url changes with it.
- **Baseline headers, no third parties.** `nosniff`, a referrer policy,
  a year of HSTS, `X-Frame-Options`, and a Content-Security-Policy of
  `default-src 'none'` with `'self'` for styles, fonts and images. The
  site collects nothing and loads nothing from anywhere else; the CSP is
  that promise in a form the browser enforces.

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
| `fetch-fonts.py` | Downloads the self-hosted woff2 files, once |

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
- **`section`** groups the page in the nav and the sidebar. The IA is
  read out of this file in this file's order — nothing is derived from
  the directory layout. A `null` section keeps the page out of both,
  which is how the 404 page stays off the nav.
- **`source`** is repo-relative, or `null` for a landing page whose body
  is authored in its template rather than sliced.
- **`from`** is the heading the slice starts at. It is matched on the
  heading's text; writing the `#`s (`## Stages`) pins the level too.
  Headings inside fenced code blocks never match.
- **`to`** is optional. Without it the slice runs to the next heading of
  the same level or shallower.

The `from` heading itself is dropped — the layout renders the page title
— and what remains is promoted by `level - 1`, so a section's `###`
sub-headings land as the page's `<h2>`s.

## What fails the build

Each of these exits non-zero with a message naming the route:

- a `source` file that no longer exists;
- a `from` or `to` heading the source no longer contains;
- a slice that comes out empty;
- a markdown link to a repo path that does not exist, or that escapes
  the repo — a dead relative link must never reach the site;
- a template placeholder the builder does not supply;
- a file in `root/` that a route would also write.

Repo-relative links that *do* resolve are rewritten: to a site route if
`link_routes` maps the file to one, otherwise to the file on GitHub.
