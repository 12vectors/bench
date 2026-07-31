#!/usr/bin/env python3
"""Generate the bench minisite from the repo's own markdown.

    python3 site/build.py                 # writes site/dist/
    python3 site/build.py --out /tmp/x    # somewhere else

Nothing here is transcribed. Every page body is a heading slice of a file
that already documents bench — AGENTS.md, README.md, the settings example,
the adapter contract — and site/pages.json is the only place that mapping
is written down. That is the whole point: renaming a section in AGENTS.md
must break this build, loudly, naming the route and the heading it can no
longer find, rather than quietly emitting a page with an empty body.

The stdlib-only law binds `manager/core/` — the tool people install. This
directory is neither shipped nor installed (see manager/core/release-manifest,
"Anything not listed here does not ship"), so it may depend on a real
markdown parser: markdown-it-py, pinned in site/requirements.txt.

## How a slice becomes a page

Given `"from": "## Claiming a card"` the builder takes the lines after
that heading up to the next heading of the same level or shallower (or to
an explicit `"to"` heading), then promotes what is left by `level - 1` so
the section's own `###` sub-headings land as the page's `<h2>`s. The
`from` heading itself is dropped: the layout renders the page title from
the manifest, and a body that repeated it would say it twice.

Templates are `string.Template`, so placeholders are `$name` and a literal
dollar is `$$` — `str.format` was not an option with a stylesheet's worth
of braces in play.

## What the host needs from the build

Two things here exist for the way the site is served (site/wrangler.jsonc,
Cloudflare Workers static assets), and both are documented where they are
implemented below:

- `site/root/` is copied verbatim to the TOP of the output, because
  `_headers` — the caching and security policy — is read by the host from
  the root and nowhere else.
- the stylesheet and the icon are linked with a `?v=<hash>` of their own
  contents (see `stamp`), which is what lets `_headers` cache them for a
  year without a deploy ever going unseen.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from html import escape
from pathlib import Path
from string import Template

SITE = Path(__file__).resolve().parent
REPO = SITE.parent

# Written into every output directory the builder owns, so a later run
# knows the tree is its own before removing it. --out pointed at
# something else refuses rather than deleting a stranger's files.
MARKER = ".bench-site"

# Copied verbatim to the TOP of the build, unlike static/ which lands in
# a subdirectory of its own. These are files the host reads rather than
# pages it serves — `_headers` is the whole list — and they have to sit
# at the root because that is where Cloudflare looks for them.
ROOT = "root"

# The assets a template links directly, and the placeholder each one is
# offered under. See stamp() for why they carry a query string.
STAMPED = {"stylesheet": "static/site.css", "icon": "static/favicon.svg"}

ATX = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
EXTERNAL = ("http://", "https://", "//", "mailto:", "tel:", "data:")


class BuildError(Exception):
    """A failure a person must read and fix: a missing source, a renamed
    heading, a dead link, a manifest that does not make sense."""


# ── reading markdown ──────────────────────────────────────────────────

def headings(text: str):
    """(line index, level, text) for every ATX heading *outside* a code
    fence. The fence tracking is not a nicety: AGENTS.md fences a task
    file template whose first line is `# Task title`, and matching that
    would slice the document in half."""
    fence = None
    for index, line in enumerate(text.splitlines()):
        opener = FENCE.match(line)
        if opener:
            marker = opener.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) \
                    and not line.strip().strip(marker[0]):
                fence = None
            continue
        if fence is not None:
            continue
        found = ATX.match(line)
        if found:
            yield index, len(found.group(1)), found.group(2).strip()


def wanted(value: str):
    """A manifest heading as (level or None, text). `## Stages` pins the
    level too; a bare `Stages` matches the heading wherever it sits."""
    text = value.lstrip("#")
    level = len(value) - len(text)
    return (level or None), text.strip()


def find_heading(marks: list, value: str, after: int = -1):
    level, text = wanted(value)
    for index, found_level, found_text in marks:
        if index <= after:
            continue
        if found_text == text and (level is None or level == found_level):
            return index, found_level
    return None


def promote(text: str, by: int) -> str:
    """Shift every heading in a slice `by` levels shallower, so a section
    lifted out of a larger document keeps its internal hierarchy while
    starting at <h2> under the page's own <h1>."""
    if by <= 0:
        return text
    lines = text.splitlines()
    for index, level, _ in list(headings(text)):
        found = ATX.match(lines[index])
        lines[index] = "#" * max(1, level - by) + " " + found.group(2).strip()
    return "\n".join(lines)


def slice_section(text: str, page: dict, source: str) -> str:
    """The body of one page, or a BuildError naming what drifted."""
    route = page["path"]
    marks = list(headings(text))
    start = find_heading(marks, page["from"])
    if start is None:
        raise BuildError(
            f'{route}: {source} has no heading "{page["from"]}" — the '
            f"section was renamed, moved or deleted. Fix the heading or "
            f"update the entry in site/pages.json.")
    start_line, level = start

    if page.get("to"):
        end = find_heading(marks, page["to"], after=start_line)
        if end is None:
            raise BuildError(
                f'{route}: {source} has no heading "{page["to"]}" after '
                f'"{page["from"]}" — the slice has no end. Fix the heading '
                f"or update the entry in site/pages.json.")
        end_line = end[0]
    else:
        following = [i for i, found_level, _ in marks
                     if i > start_line and found_level <= level]
        end_line = following[0] if following else len(text.splitlines())

    body = "\n".join(text.splitlines()[start_line + 1:end_line]).strip("\n")
    if not body.strip():
        raise BuildError(
            f'{route}: the slice of {source} from "{page["from"]}" is '
            f"empty. A page with no body is a drift, not a page.")
    return promote(body, level - 1)


# ── links ─────────────────────────────────────────────────────────────

def rewrite_link(href: str, *, page: dict, source: str, manifest: dict,
                 repo: Path) -> str:
    """A repo-relative link out of a markdown file is a dead path on the
    web. Send it to the site route that covers it, or to the file on
    GitHub — and refuse to emit anything else."""
    if not href or href.startswith("#") or href.startswith(EXTERNAL):
        return href
    target, hash_mark, fragment = href.partition("#")
    if not target:
        return href
    rel = os.path.normpath(os.path.join(os.path.dirname(source), target))
    if rel.startswith(".."):
        raise BuildError(
            f'{page["path"]}: {source} links to "{href}", which is outside '
            f"the repository. Only links within the repo can be rewritten.")
    if not (repo / rel).exists():
        raise BuildError(
            f'{page["path"]}: {source} links to "{href}", which does not '
            f"exist ({rel}). A dead link in a source file is a dead link "
            f"on the site.")
    route = manifest.get("link_routes", {}).get(rel)
    if route:
        return route + hash_mark + fragment
    blob = manifest["site"]["blob_base"].rstrip("/") + "/"
    return blob + rel + hash_mark + fragment


# ── rendering ─────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def render_markdown(body: str, *, page: dict, source: str, manifest: dict,
                    repo: Path):
    """(html, [(slug, text)] for the h2s) — heading ids and rewritten
    links are done on the token stream, not with regexes over HTML."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as missing:  # pragma: no cover - environment
        raise BuildError(
            "markdown-it-py is not installed. It is the site's only "
            "dependency: python3 -m pip install -r site/requirements.txt"
        ) from missing

    renderer = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    tokens = renderer.parse(body)
    contents, seen = [], {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            text = tokens[index + 1].content
            slug = slugify(text)
            seen[slug] = seen.get(slug, 0) + 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            token.attrSet("id", slug)
            if token.tag == "h2":
                contents.append((slug, text))
        elif token.type == "inline":
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet("href", rewrite_link(
                        child.attrGet("href"), page=page, source=source,
                        manifest=manifest, repo=repo))
                elif child.type == "image":
                    child.attrSet("src", rewrite_link(
                        child.attrGet("src"), page=page, source=source,
                        manifest=manifest, repo=repo))
    return renderer.renderer.render(tokens, renderer.options, {}), contents


def read_source(page: dict, repo: Path) -> str:
    source = page["source"]
    path = repo / source
    if not path.is_file():
        raise BuildError(
            f'{page["path"]}: source file {source} does not exist. The '
            f"file was renamed or moved; update site/pages.json.")
    return path.read_text(encoding="utf-8")


# ── the shell around a body ───────────────────────────────────────────

def sections(manifest: dict) -> list:
    """The IA, read out of the manifest in manifest order: the sections
    that have pages, each with its pages. Nothing is derived from the
    directory layout — pages.json is where the site's shape is written."""
    groups: list = []
    for page in manifest["pages"]:
        name = page.get("section")
        if not name:
            continue
        for group in groups:
            if group["name"] == name:
                group["pages"].append(page)
                break
        else:
            groups.append({"name": name, "pages": [page]})
    return groups


def render_nav(manifest: dict, current: dict) -> str:
    out = []
    for group in sections(manifest):
        first = group["pages"][0]
        active = " nav-here" if current.get("section") == group["name"] else ""
        out.append(f'<a class="nav-link{active}" href="{first["path"]}">'
                   f'{escape(group["name"])}</a>')
    return "\n".join(out)


def render_sidebar(manifest: dict, current: dict) -> str:
    out = []
    for group in sections(manifest):
        out.append('<div class="side-group">')
        out.append(f'<span class="side-label">{escape(group["name"])}</span>')
        for page in group["pages"]:
            here = " side-here" if page["path"] == current["path"] else ""
            out.append(f'<a class="side-link{here}" href="{page["path"]}">'
                       f'{escape(page["title"])}</a>')
        out.append("</div>")
    return "\n".join(out)


def render_contents(contents: list) -> str:
    if not contents:
        return ""
    out = ['<span class="toc-label">On this page</span>']
    for slug, text in contents:
        out.append(f'<a class="toc-link" href="#{slug}">{escape(text)}</a>')
    return "\n".join(out)


def render_breadcrumb(page: dict) -> str:
    if not page.get("section"):
        return ""
    return (f'<span>{escape(page["section"])}</span><span>›</span>'
            f'<span class="crumb-here">{escape(page["title"])}</span>')


def load_template(name: str, site: Path) -> Template:
    path = site / "templates" / f"{name}.html"
    if not path.is_file():
        available = sorted(p.stem for p in (site / "templates").glob("*.html"))
        raise BuildError(
            f'no template for layout "{name}". Templates present: '
            f'{", ".join(available) or "none"}.')
    return Template(path.read_text(encoding="utf-8"))


def stamp(site: Path) -> dict:
    """`{"stylesheet": "/static/site.css?v=<hash>", …}` — the urls the
    templates link the stylesheet and the icon by.

    Nothing in static/ is renamed by the build: `site.css` stays
    `site.css`, which is what anyone reading the tree (and the host's
    own `_headers` globs) can rely on. So the fingerprint rides in the
    query string instead. Caches key on the whole url, which is what
    makes `immutable` in site/root/_headers safe: edit the stylesheet
    and the deploy publishes it under a url nothing has ever cached,
    while the year-long cache still holds for everyone else."""
    urls = {}
    for name, rel in STAMPED.items():
        path = site / rel
        if not path.is_file():
            # Absent is missing_assets()' story to tell, not a crash here.
            urls[name] = "/" + rel
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        urls[name] = f"/{rel}?v={digest}"
    return urls


def render_page(page: dict, manifest: dict, *, site: Path, repo: Path,
                stamps: dict = None) -> str:
    source = page.get("source")
    if source:
        body, contents = render_markdown(
            slice_section(read_source(page, repo), page, source),
            page=page, source=source, manifest=manifest, repo=repo)
    else:
        # An authored landing page says so with "source": null. Its words
        # live in the template, so there is no slice and nothing to drift.
        body, contents = "", []

    config = manifest["site"]
    blob = config["blob_base"].rstrip("/") + "/"
    stamps = stamps if stamps is not None else stamp(site)
    fields = {
        "stylesheet": stamps["stylesheet"],
        "icon": stamps["icon"],
        "title": escape(page["title"]),
        "description": escape(page.get("description")
                              or config.get("description", "")),
        "site_title": escape(config["title"]),
        "site_tagline": escape(config.get("tagline", "")),
        "version": escape(config.get("version", "")),
        "body": body,
        "toc": render_contents(contents),
        "nav": render_nav(manifest, page),
        "sidebar": render_sidebar(manifest, page),
        "breadcrumb": render_breadcrumb(page),
        "section": escape(page.get("section") or ""),
        "repo_url": config["repo_url"],
        "issues_url": config.get("issues_url", config["repo_url"]),
        "source_url": (blob + source) if source else config["repo_url"],
        "source_path": escape(source or ""),
        "canonical": config.get("base_url", "").rstrip("/") + page["path"],
    }
    try:
        return load_template(page["layout"], site).substitute(fields)
    except KeyError as unknown:
        raise BuildError(
            f'{page["layout"]}.html uses an unknown placeholder '
            f"${unknown.args[0]}. Known: "
            f'{", ".join("$" + k for k in sorted(fields))}.') from unknown
    except ValueError as bad:
        raise BuildError(
            f"{page['layout']}.html: {bad}. A literal dollar sign in a "
            f"template must be written $$.") from bad


# ── the manifest ──────────────────────────────────────────────────────

def load_manifest(site: Path) -> dict:
    path = site / "pages.json"
    if not path.is_file():
        raise BuildError(f"no manifest at {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        raise BuildError(f"pages.json is not valid JSON: {broken}") from broken

    for key in ("site", "pages"):
        if key not in manifest:
            raise BuildError(f'pages.json has no "{key}" key')
    for key in ("title", "repo_url", "blob_base"):
        if key not in manifest["site"]:
            raise BuildError(f'pages.json: site has no "{key}" key')

    seen = set()
    for page in manifest["pages"]:
        for key in ("path", "title", "layout"):
            if not page.get(key):
                raise BuildError(f'pages.json: an entry has no "{key}": '
                                 f"{json.dumps(page)}")
        route = page["path"]
        if not route.startswith("/") or not (route.endswith("/")
                                             or route.endswith(".html")):
            raise BuildError(
                f'pages.json: route "{route}" must start with "/" and '
                f'either end with "/" (a directory index) or name an '
                f'.html file (as /404.html does)')
        if route in seen:
            raise BuildError(f'pages.json: route "{route}" appears twice')
        seen.add(route)
        if "source" not in page:
            raise BuildError(
                f'{route}: no "source". A page generated from a file names '
                f'it; an authored page says "source": null.')
        if page["source"] and not page.get("from"):
            raise BuildError(
                f'{route}: "source" is {page["source"]} but there is no '
                f'"from" heading to slice from.')
        if not page["source"] and (page.get("from") or page.get("to")):
            raise BuildError(
                f'{route}: "source" is null, so "from"/"to" have nothing '
                f"to slice. Remove them or name a source.")
    return manifest


# ── output ────────────────────────────────────────────────────────────

def clear(out: Path) -> None:
    """Empty the output directory — but only one this builder made. A
    --out pointed at something else stops the build instead."""
    if not out.exists():
        return
    if not out.is_dir():
        raise BuildError(f"{out} is not a directory")
    if any(out.iterdir()) and not (out / MARKER).exists():
        raise BuildError(
            f"{out} is not empty and was not written by this builder "
            f"(no {MARKER}). Refusing to delete it.")
    shutil.rmtree(out)


def target_for(out: Path, route: str) -> Path:
    """Where a route is written. `/x/y/` is a directory index, the shape
    every page has; `/404.html` is that literal file, because Cloudflare's
    not-found handling looks for a `404.html` and would never find a
    `404/index.html`."""
    if not route.endswith("/"):
        return out / route.lstrip("/")
    inner = route.strip("/")
    return (out / inner / "index.html") if inner else (out / "index.html")


def copy_static(site: Path, out: Path) -> None:
    static = site / "static"
    if static.is_dir():
        shutil.copytree(static, out / "static",
                        ignore=shutil.ignore_patterns(".DS_Store"))


def root_files(site: Path) -> list:
    """Everything under site/root/, as (source, path relative to the
    output root). The tree is copied verbatim to the top of the build:
    these are files the *host* reads — `_headers` — rather than pages the
    site serves, and Cloudflare only looks for them at the root."""
    source = site / ROOT
    if not source.is_dir():
        return []
    return [(path, path.relative_to(source))
            for path in sorted(source.rglob("*"))
            if path.is_file() and path.name != ".DS_Store"]


def copy_root(site: Path, out: Path) -> list:
    written = []
    for path, relative in root_files(site):
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written.append(target)
    return written


def missing_assets(out: Path) -> list:
    """Every same-origin url() a stylesheet asks for that is not in the
    output. The fonts are self-hosted on purpose — the shipped site makes
    no third-party request — so an absent woff2 is worth saying out loud
    even though the page still renders on the fallback stack."""
    absent = []
    for sheet in sorted((out / "static").rglob("*.css")):
        for url in CSS_URL.findall(sheet.read_text(encoding="utf-8")):
            if url.startswith(EXTERNAL):
                continue
            target = (out / url.lstrip("/")) if url.startswith("/") \
                else (sheet.parent / url)
            if not target.exists():
                absent.append(url)
    return sorted(set(absent))


def build(*, repo: Path = REPO, site: Path = None, out: Path = None,
          log=print) -> list:
    site = site or (repo / "site")
    out = out or (site / "dist")
    manifest = load_manifest(site)
    stamps = stamp(site)

    # A file in root/ that a route also claims would be silently replaced
    # by whichever is written last, so it is a build failure instead.
    claimed = {str(target_for(Path(), page["path"])): page["path"]
               for page in manifest["pages"]}
    for _, relative in root_files(site):
        route = claimed.get(str(relative))
        if route:
            raise BuildError(
                f'site/{ROOT}/{relative} and the route "{route}" both write '
                f"{relative}. Rename one — the build will not pick a winner.")

    pages = [(page, render_page(page, manifest, site=site, repo=repo,
                                stamps=stamps))
             for page in manifest["pages"]]

    clear(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text(
        "written by site/build.py; safe to delete\n", encoding="utf-8")
    copy_static(site, out)
    for target in copy_root(site, out):
        log(f"  {'(root)':<34} {target.relative_to(out)}")

    written = []
    for page, html in pages:
        target = target_for(out, page["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        written.append(target)
        log(f"  {page['path']:<34} {target.relative_to(out)}")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the bench minisite into site/dist/.")
    parser.add_argument("--repo", type=Path, default=REPO,
                        help="repository root (default: the one above site/)")
    parser.add_argument("--site", type=Path, default=None,
                        help="site directory (default: <repo>/site)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: <site>/dist)")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    site = (args.site or repo / "site").resolve()
    out = (args.out or site / "dist").resolve()
    log = (lambda *a: None) if args.quiet else print

    try:
        written = build(repo=repo, site=site, out=out, log=log)
    except BuildError as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    for url in missing_assets(out):
        print(f"warning: {url} is referenced by the stylesheet but is not "
              f"in the build — see site/static/fonts/README.md",
              file=sys.stderr)
    log(f"{len(written)} pages → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
