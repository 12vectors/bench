# The board's fonts

The board's page sets its text in IBM Plex Sans and IBM Plex Mono, and
it serves both itself. Before task 123 it linked Google Fonts, which made
every page view a request to a third party; now it makes none.

## What is here

- **`plex.css`** declares the six faces the page uses: Plex Sans 400, 500
  and 600 upright and 400 italic, and Plex Mono 400 and 500. Each face is
  split into the subsets Google serves: latin, latin-ext, cyrillic,
  cyrillic-ext, vietnamese, greek (Sans) and symbols2 (Mono). That is 36
  `@font-face` rules. The file is the stylesheet the page used to link,
  byte for byte, except that each `url(https://fonts.gstatic.com/…/<file>)`
  now reads `url(<file>)`. The `unicode-range` values, weights, styles and
  `font-display` are untouched, so no script the page used to render in
  Plex falls back now.
- **24 `.woff2` files.** These are the upstream files, under their
  upstream names, unmodified: nothing was re-subset, renamed or
  converted. Plex Sans upright uses one file per subset for all three
  weights; the stylesheet names it three times.
- **`SOURCES.txt`** records where each file came from: the stylesheet's
  URL and its sha256, then each file's sha256, size, name and upstream
  URL. A test holds the bundle to it, and turns `plex.css` back into the
  upstream stylesheet to compare that hash too.
- **`IBMPlex.LICENSE.txt`** is the SIL Open Font License 1.1, with IBM's
  copyright notice and the reserved font name "Plex". It applies to both
  families. The upstream licence files for Plex Sans and Plex Mono are
  byte-identical. This copy is also identical to the one the site ships
  in `site/static/fonts/`, and a test checks that.

The 700 weight, bold markup and Mono italic are synthesised by the
browser. That was true with the linked stylesheet, which declared the
same six faces, and it is unchanged.

## Where they come from

The set was fetched once from Google Fonts' CSS API — `v23` of both
families, as the stylesheet's URLs name them — and committed in November
2026. The licence came from
[IBM/plex](https://github.com/IBM/plex). To refresh the set:

```bash
python3 site/fetch-fonts.py --core               # from the network
python3 site/fetch-fonts.py --core --from DIR    # from a directory holding
                                                 # plex-upstream.css and the woff2 files
python3 manager/core/assets.py --write           # then the asset manifest
```

The script rewrites the woff2 files, `plex.css` and `SOURCES.txt`. It
does not fetch the licence; if upstream changes it, update this copy and
the site's copy by hand.

## What the page relies on, and what it does not

These files cover the text the page renders in Plex. The page also uses
symbols that Plex does not have, such as `⌖ ◫ ⚖ ⟶`. Those were never
drawn in Plex: the browser draws them from a system font, as before.
`tests/fixtures/page-symbols.json` lists them. The browser case
`verify/browser_cases/case_fonts.py` checks that the bundled set draws
the same glyphs from Plex as the stylesheet the page used to link, and
that the symbols in that list still come from a system font. It does not
prove that every system has a font for them.
