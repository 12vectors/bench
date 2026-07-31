#!/usr/bin/env python3
"""Download the site's woff2 files into site/static/fonts/.

    python3 site/fetch-fonts.py            # fetch what is missing
    python3 site/fetch-fonts.py --force    # fetch everything again

The site self-hosts its type: no page bench serves may make a request to
a third party, and a linked font CDN is exactly that request. This script
is the one moment the fonts come from Google — at build time, on someone's
machine — after which the files are the site's own.

It asks the CSS API for each face on its own, so the reply names exactly
one woff2 per request and there is nothing to guess about which URL is
which weight. The `latin` subset is what that API already serves.

Stdlib only, network required. If you are offline, copy the files in by
hand; site/static/fonts/README.md lists the seven names.
"""

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "static" / "fonts"
API = "https://fonts.googleapis.com/css2"
# A modern browser UA is what makes the API answer in woff2 rather than
# in one of the older formats it keeps for old clients.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
WOFF2 = re.compile(r"url\((https://[^)]+\.woff2)\)")

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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="re-download faces that are already here")
    args = parser.parse_args(argv)

    FONTS.mkdir(parents=True, exist_ok=True)
    print(f"fonts → {FONTS}")
    for face in FACES:
        try:
            print(fetch(face, args.force))
        except (urllib.error.URLError, RuntimeError) as failure:
            print(f"error: {face[0]}: {failure}", file=sys.stderr)
            return 1
    print("done — the site now serves its own type, same-origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
