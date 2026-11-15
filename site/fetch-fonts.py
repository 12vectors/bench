#!/usr/bin/env python3
"""Download the site's woff2 files, and the board's, from Google Fonts.

    python3 site/fetch-fonts.py            # the site's faces, what is missing
    python3 site/fetch-fonts.py --force    # the site's faces, all again
    python3 site/fetch-fonts.py --core     # the board's whole Plex set
    python3 site/fetch-fonts.py --core --from DIR   # the same, offline

The site self-hosts its type: no page bench serves may make a request to
a third party, and a linked font CDN is exactly that request. This script
is the one moment the fonts come from Google — at build time, on someone's
machine — after which the files are the site's own.

For the site it asks the CSS API for each face on its own, so the reply
names exactly one woff2 per request and there is nothing to guess about
which URL is which weight. The `latin` subset is what that API already
serves.

`--core` is the board's half (task 123). The page used to link Google
Fonts for the six Plex faces it uses; it now serves them itself from
`manager/core/web/fonts/`. This mode asks for exactly the stylesheet the
page linked (`PAGE_CSS`) and keeps **every** subset it names, not only
`latin`, so no script the page rendered in Plex falls back. Each woff2 is
written under its upstream file name, unmodified, and `plex.css` is the
upstream stylesheet with each `url(https://fonts.gstatic.com/…/<file>)`
replaced by `url(<file>)` and nothing else changed — `upstream_of()`
turns it back into the upstream bytes, which is how a test proves the
provenance `SOURCES.txt` records (the stylesheet's hash, and each file's
hash, size and upstream URL). `--from DIR` reads `DIR/plex-upstream.css`
and the woff2 files, by upstream name, from a directory instead of the
network; that is how the approved set was bundled. Neither mode fetches a
licence: see the README beside the fonts.

Stdlib only. Network required unless `--from` is given; offline, copy the
site's files in by hand — site/static/fonts/README.md lists the names.
"""

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "static" / "fonts"
CORE_FONTS = Path(__file__).resolve().parents[1] / "manager" / "core" / "web" / "fonts"
API = "https://fonts.googleapis.com/css2"
# The stylesheet board.html linked before task 123, byte for byte.
PAGE_CSS = (f"{API}?family=IBM+Plex+Mono:wght@400;500"
            "&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap")
# A modern browser UA is what makes the API answer in woff2 rather than
# in one of the older formats it keeps for old clients.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
WOFF2 = re.compile(r"url\((https://[^)]+\.woff2)\)")
GSTATIC = re.compile(r"url\((https://fonts\.gstatic\.com/s/[a-z0-9]+/v[0-9]+/"
                     r"([A-Za-z0-9_-]+\.woff2))\)")
LOCAL = re.compile(r"url\(([A-Za-z0-9_-]+\.woff2)\)")

# (output name, family, css spec) — the css spec is the `family=` value.
FACES = [
    ("IBMPlexSans-Regular.woff2", "IBM Plex Sans", "IBM+Plex+Sans:ital,wght@0,400"),
    ("IBMPlexSans-Italic.woff2", "IBM Plex Sans", "IBM+Plex+Sans:ital,wght@1,400"),
    ("IBMPlexSans-Medium.woff2", "IBM Plex Sans", "IBM+Plex+Sans:ital,wght@0,500"),
    ("IBMPlexSans-SemiBold.woff2", "IBM Plex Sans", "IBM+Plex+Sans:ital,wght@0,600"),
    ("IBMPlexMono-Regular.woff2", "IBM Plex Mono", "IBM+Plex+Mono:ital,wght@0,400"),
    ("IBMPlexMono-Medium.woff2", "IBM Plex Mono", "IBM+Plex+Mono:ital,wght@0,500"),
    ("ZillaSlab-SemiBold.woff2", "Zilla Slab", "Zilla+Slab:wght@600"),
]


def get(url: str, *, binary: bool = False):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as reply:
        raw = reply.read()
    return raw if binary else raw.decode("utf-8")


def fetch(face: tuple, force: bool) -> str:
    name, family, spec = face
    target = FONTS / name
    if target.exists() and not force:
        return f"  kept     {name}"
    css = get(f"{API}?family={spec}&display=swap&subset=latin")
    urls = WOFF2.findall(css)
    if not urls:
        raise RuntimeError(
            f"{family}: the CSS API answered with no woff2 url. It may have "
            f"changed its reply for this User-Agent; fetch by hand.")
    # The API lists subsets in order; latin is the one the site needs and
    # is the last block it emits for these families.
    target.write_bytes(get(urls[-1], binary=True))
    return f"  wrote    {name}  ({len(urls)} subsets offered)"


# ── the board's set (--core) ─────────────────────────────────────────────


def localize(upstream: str) -> tuple[str, list[tuple[str, str]]]:
    """The upstream stylesheet with every gstatic woff2 url replaced by its
    bare file name, and the (file, url) pairs in first-seen order. Refuses
    a stylesheet that would keep any other url or an @import, so nothing
    off the board survives localizing."""
    pairs, seen = [], {}
    for url, name in GSTATIC.findall(upstream):
        if seen.setdefault(name, url) != url:
            raise RuntimeError(f"{name} is named by two different urls")
        if (name, url) not in pairs:
            pairs.append((name, url))
    local = GSTATIC.sub(lambda m: f"url({m.group(2)})", upstream)
    leftover = [u for u in re.findall(r"url\(([^)]*)\)", local)
                if not LOCAL.fullmatch(f"url({u})")]
    if leftover or "@import" in local:
        raise RuntimeError(f"the stylesheet names urls that are not gstatic "
                           f"woff2 files ({leftover[:3]}); refusing to localize it")
    return local, pairs


def upstream_of(local: str, pairs) -> str:
    """localize() undone: the upstream stylesheet's exact text."""
    urls = dict(pairs)
    return LOCAL.sub(lambda m: f"url({urls[m.group(1)]})", local)


def sources(css_sha: str, files) -> str:
    """SOURCES.txt, deterministic: the stylesheet, then one line per file."""
    lines = ["# Upstream provenance of the board's Plex fonts (task 123).",
             "# Written by: python3 site/fetch-fonts.py --core",
             "# plex.css is this stylesheet with each url() reduced to its file name.",
             f"stylesheet {PAGE_CSS}",
             f"stylesheet-sha256 {css_sha}",
             "# sha256 bytes file upstream-url"]
    for name, url, data in files:
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name} {url}")
    return "\n".join(lines) + "\n"


def read_sources(text: str) -> dict:
    """SOURCES.txt parsed: {"stylesheet", "sha256", "files": [(sha, size,
    name, url)]}. For the tests that hold the bundle to it."""
    out = {"files": []}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(" ")
        if parts[0] == "stylesheet":
            out["stylesheet"] = parts[1]
        elif parts[0] == "stylesheet-sha256":
            out["sha256"] = parts[1]
        else:
            sha, size, name, url = parts
            out["files"].append((sha, int(size), name, url))
    return out


def core(source: Path | None) -> int:
    if source is not None:
        upstream = (source / "plex-upstream.css").read_text(encoding="utf-8")
    else:
        upstream = get(PAGE_CSS)
    local, pairs = localize(upstream)
    if upstream_of(local, pairs) != upstream:
        raise RuntimeError("localize() is not reversible for this stylesheet")
    files = []
    for name, url in pairs:
        data = ((source / name).read_bytes() if source is not None
                else get(url, binary=True))
        if data[:4] != b"wOF2":
            raise RuntimeError(f"{name} is not a woff2 file")
        files.append((name, url, data))
    CORE_FONTS.mkdir(parents=True, exist_ok=True)
    print(f"board fonts → {CORE_FONTS}")
    for name, _url, data in files:
        (CORE_FONTS / name).write_bytes(data)
    (CORE_FONTS / "plex.css").write_text(local, encoding="utf-8")
    css_sha = hashlib.sha256(upstream.encode("utf-8")).hexdigest()
    (CORE_FONTS / "SOURCES.txt").write_text(sources(css_sha, files),
                                            encoding="utf-8")
    print(f"  wrote    {len(files)} woff2 files, plex.css and SOURCES.txt")
    print("then:     python3 manager/core/assets.py --write")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="re-download faces that are already here")
    parser.add_argument("--core", action="store_true",
                        help="the board's whole Plex set, into manager/core/web/fonts/")
    parser.add_argument("--from", dest="source", type=Path,
                        help="with --core: read plex-upstream.css and the woff2 "
                             "files from this directory instead of the network")
    args = parser.parse_args(argv)
    if args.source is not None and not args.core:
        parser.error("--from goes with --core")
    if args.core:
        try:
            return core(args.source.resolve() if args.source else None)
        except (OSError, urllib.error.URLError, RuntimeError) as failure:
            print(f"error: {failure}", file=sys.stderr)
            return 1

    FONTS.mkdir(parents=True, exist_ok=True)
    print(f"fonts → {FONTS}")
    for face in FACES:
        try:
            print(fetch(face, args.force))
        except (urllib.error.URLError, RuntimeError) as failure:
            print(f"error: {failure}", file=sys.stderr)
            return 1
    print("done — the site now serves its own type, same-origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
