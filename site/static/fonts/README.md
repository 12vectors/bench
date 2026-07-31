# The site's fonts live here

Reading bench's documentation must not require a request to anyone else,
so the site links no font CDN. `site/static/site.css` declares its faces
against files in this directory, and everything the built pages fetch is
same-origin.

Seven files, exactly these names — they are what the `@font-face` rules
in `site.css` ask for:

| File | Family | Weight | Style |
| --- | --- | --- | --- |
| `IBMPlexSans-Regular.woff2` | IBM Plex Sans | 400 | normal |
| `IBMPlexSans-Italic.woff2` | IBM Plex Sans | 400 | italic |
| `IBMPlexSans-Medium.woff2` | IBM Plex Sans | 500 | normal |
| `IBMPlexSans-SemiBold.woff2` | IBM Plex Sans | 600 | normal |
| `IBMPlexMono-Regular.woff2` | IBM Plex Mono | 400 | normal |
| `IBMPlexMono-Medium.woff2` | IBM Plex Mono | 500 | normal |
| `ZillaSlab-SemiBold.woff2` | Zilla Slab | 600 | normal |

## Fetching them

```bash
python3 site/fetch-fonts.py
```

The script asks Google Fonts for the CSS these faces would need, reads
the `woff2` URLs out of the reply, and writes the files here under the
names above. What it downloads is already the `latin` subset Google
serves — the site's copy of the font, not a link to Google's.

Both families are licensed for this: IBM Plex under the SIL Open Font
License 1.1, Zilla Slab likewise. Keeping the downloaded `*.LICENSE.txt`
beside the woff2 files satisfies the licence's one obligation.

## If they are absent

`site/build.py` prints a `warning:` line naming every file the
stylesheet wants and the build does not have, and carries on. The pages
render on the fallback stack (`system-ui`, `ui-monospace`, `Georgia`)
and still make no third-party request — the typography is wrong, the
privacy promise is not.
