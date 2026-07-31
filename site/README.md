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
Deployment is task 32's; this directory only has to produce the files.

`site/` never reaches a host repo: releases ship exactly what
`../manager/core/release-manifest` lists, and it does not list this
directory. `tests/test_release_artifact.py` asserts that out loud rather
than leaving it to inference.

## What is where

| Path | What it is |
| --- | --- |
| `pages.json` | The manifest: one entry per route, and the site's whole IA |
| `build.py` | The generator — slicing, drift detection, rendering, links |
| `templates/` | One `string.Template` per layout (`$name`, `$$` for a literal dollar) |
| `static/` | Copied to `dist/static/` verbatim: the stylesheet, the icon, the fonts |
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

- **`path`** starts and ends with `/`; `/x/y/` is written to
  `dist/x/y/index.html`.
- **`layout`** names a file in `templates/`.
- **`section`** groups the page in the nav and the sidebar. The IA is
  read out of this file in this file's order — nothing is derived from
  the directory layout.
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
- a template placeholder the builder does not supply.

Repo-relative links that *do* resolve are rewritten: to a site route if
`link_routes` maps the file to one, otherwise to the file on GitHub.
