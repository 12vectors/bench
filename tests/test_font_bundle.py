"""The board serves its own Plex fonts, every subset, with provenance (task 123).

`manager/core/web/fonts/` holds the 24 upstream woff2 files the page's
old Google Fonts stylesheet named, under their upstream names and
unmodified; `plex.css`, that stylesheet with each url reduced to its file
name; `SOURCES.txt`, the upstream hashes and urls; and the SIL OFL. This
holds the bundle to its record, so a file swapped, dropped, renamed or
re-subset is a failure here rather than a glyph that quietly falls back.

What the standard library cannot do is read a woff2 character map, so
glyph coverage is proved in the browser (`verify/browser_cases/
case_fonts.py`). This module keeps that case's inputs honest: the faces
and ranges it probes. It reads no page source; tests/test_font_page.py
holds the page to the bundle and to its symbol inventory.

    python3 -m unittest tests.test_font_bundle -v
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "manager" / "core"
FONTS = CORE / "web" / "fonts"
SITE_LICENCE = REPO / "site" / "static" / "fonts" / "IBMPlex.LICENSE.txt"
INVENTORY = REPO / "tests" / "fixtures" / "page-symbols.json"
sys.path.insert(0, str(CORE))

import assets  # noqa: E402


def _fetch_fonts():
    spec = importlib.util.spec_from_file_location(
        "fetch_fonts", REPO / "site" / "fetch-fonts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetchfonts = _fetch_fonts()

FACE = re.compile(
    r"/\* (?P<subset>[a-z0-9-]+) \*/\n@font-face \{\n(?P<body>[^}]*)\}", re.M)


def descriptor(body: str, name: str) -> str:
    found = re.search(rf"^  {name}: ([^;]*);$", body, re.M)
    return found.group(1) if found else ""


def ranges(value: str) -> list[tuple[int, int]]:
    """A unicode-range value as inclusive (low, high) pairs."""
    out = []
    for part in value.split(","):
        part = part.strip().removeprefix("U+")
        low, _, high = part.partition("-")
        out.append((int(low, 16), int(high or low, 16)))
    return out


def faces(css: str) -> list[dict]:
    out = []
    for m in FACE.finditer(css):
        body = m.group("body")
        src = re.search(r"url\(([^)]*)\) format\('woff2'\)", body)
        out.append({"subset": m.group("subset"),
                    "family": descriptor(body, "font-family").strip("'"),
                    "style": descriptor(body, "font-style"),
                    "weight": descriptor(body, "font-weight"),
                    "display": descriptor(body, "font-display"),
                    "file": src.group(1) if src else None,
                    "ranges": ranges(descriptor(body, "unicode-range"))})
    return out


def covered(cp: int, rs) -> bool:
    return any(low <= cp <= high for low, high in rs)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (FONTS / "plex.css").read_text(encoding="utf-8")
        cls.faces = faces(cls.css)
        cls.sources = fetchfonts.read_sources(
            (FONTS / "SOURCES.txt").read_text(encoding="utf-8"))
        cls.manifest = json.loads((CORE / "web" / "manifest.json")
                                  .read_bytes())["assets"]


class TheFilesAreTheUpstreamFiles(Fixture):
    def test_every_recorded_file_is_here_unmodified(self):
        """Each woff2 matches the hash and size SOURCES.txt records for its
        upstream url, keeps its upstream name, and is a woff2 file."""
        self.assertEqual(len(self.sources["files"]), 24)
        for sha, size, name, url in self.sources["files"]:
            data = (FONTS / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha, name)
            self.assertEqual(len(data), size, name)
            self.assertEqual(data[:4], b"wOF2", name)
            self.assertRegex(url, r"\Ahttps://fonts\.gstatic\.com/s/ibmplex(sans|mono)/v23/")
            self.assertEqual(url.rsplit("/", 1)[1], name,
                             "a file is shipped under its upstream name")

    def test_nothing_else_is_a_font_and_everything_is_in_the_manifest(self):
        on_disk = sorted(p.name for p in FONTS.iterdir() if p.suffix == ".woff2")
        self.assertEqual(on_disk, sorted(n for _s, _b, n, _u in self.sources["files"]))
        for name in [*on_disk, "plex.css", "SOURCES.txt", "README.md",
                     "IBMPlex.LICENSE.txt"]:
            self.assertIn(f"fonts/{name}", self.manifest)
            self.assertIsNone(assets.path_problem(f"fonts/{name}"), name)
        for name in on_disk:
            self.assertEqual(self.manifest[f"fonts/{name}"]["type"], "font/woff2")

    def test_the_stylesheet_is_the_one_the_page_linked_with_its_urls_localized(self):
        """plex.css turned back with the recorded urls is, byte for byte,
        the stylesheet SOURCES.txt hashed — the one board.html linked."""
        self.assertEqual(self.sources["stylesheet"], fetchfonts.PAGE_CSS)
        pairs = [(n, u) for _s, _b, n, u in self.sources["files"]]
        upstream = fetchfonts.upstream_of(self.css, pairs)
        self.assertEqual(hashlib.sha256(upstream.encode("utf-8")).hexdigest(),
                         self.sources["sha256"])
        local, again = fetchfonts.localize(upstream)
        self.assertEqual(local, self.css)
        self.assertEqual(again, pairs, "SOURCES.txt is in first-seen order")
        self.assertNotIn("http", self.css)

    def test_localizing_refuses_what_it_cannot_make_local(self):
        for bad in ("@font-face{src:url(https://example.com/x.woff2)}",
                    "@import url(x.css);",
                    "@font-face{src:url(data:font/woff2;base64,AA==)}"):
            with self.assertRaises(RuntimeError, msg=bad):
                fetchfonts.localize(bad)


class TheSixFacesEverySubset(Fixture):
    SANS = {"cyrillic-ext", "cyrillic", "greek", "vietnamese", "latin-ext", "latin"}
    MONO = {"cyrillic-ext", "cyrillic", "symbols2", "vietnamese", "latin-ext", "latin"}

    def test_exactly_the_six_faces_the_page_linked(self):
        declared = {(f["family"], f["style"], f["weight"]) for f in self.faces}
        self.assertEqual(declared, {
            ("IBM Plex Sans", "normal", "400"), ("IBM Plex Sans", "normal", "500"),
            ("IBM Plex Sans", "normal", "600"), ("IBM Plex Sans", "italic", "400"),
            ("IBM Plex Mono", "normal", "400"), ("IBM Plex Mono", "normal", "500")})
        self.assertEqual(len(self.faces), 36)
        self.assertEqual(self.css.count("@font-face"), 36)

    def test_every_face_has_every_subset_and_names_a_bundled_file(self):
        """No language coverage was dropped: each face carries each of its
        family's subsets, not only latin."""
        by_face: dict = {}
        for f in self.faces:
            by_face.setdefault((f["family"], f["style"], f["weight"]), set()).add(f["subset"])
            self.assertTrue((FONTS / f["file"]).is_file(), f["file"])
            self.assertEqual(f["display"], "swap")
            self.assertTrue(f["ranges"], f)
        for (family, _style, _weight), subsets in by_face.items():
            self.assertEqual(subsets, self.SANS if family == "IBM Plex Sans" else self.MONO)


class TheLicenceTravelsWithTheFonts(Fixture):
    def test_it_is_the_ofl_with_ibms_notice_and_the_site_s_copy(self):
        text = (FONTS / "IBMPlex.LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn('Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"', text)
        self.assertIn("SIL Open Font License, Version 1.1", text)
        self.assertEqual(hashlib.sha256((FONTS / "IBMPlex.LICENSE.txt").read_bytes()).hexdigest(),
                         hashlib.sha256(SITE_LICENCE.read_bytes()).hexdigest(),
                         "the board's and the site's Plex licences differ")

    def test_the_readme_names_the_licence_and_the_record(self):
        readme = (FONTS / "README.md").read_text(encoding="utf-8")
        for name in ("IBMPlex.LICENSE.txt", "SOURCES.txt", "plex.css",
                     "site/fetch-fonts.py --core", "page-symbols.json"):
            self.assertIn(name, readme)


if __name__ == "__main__":
    unittest.main()
