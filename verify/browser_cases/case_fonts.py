"""The board's own Plex fonts: offline, every subset, and drawn as before (task 123).

The page used to link Google Fonts. It now serves the same faces itself,
from `web/fonts/` in its boot snapshot. This case shows, in the real
browser, what the backend tests cannot:

1. **Nothing off the board.** Every request the page makes, the fonts
   included and every subset forced to load, is to the board's own origin.

2. **Glyph coverage, proved by drawing.** `document.fonts.check` and CSS
   matching say a face *would* be used, not that it has the glyph, so
   neither is used as proof. Each codepoint is drawn on a canvas:

   - the Plex stack is drawn with two *different* fallback families
     behind it (`"IBM Plex Sans", monospace` and `"IBM Plex Sans",
     serif`), and each fallback is drawn alone;
   - a draw with no ink is `blank`; a draw identical to the same stack's
     drawing of an unassigned codepoint (U+0378) is `tofu`, the missing-
     glyph box — both rejected before any comparison;
   - the two fallbacks alone must draw the codepoint differently, or the
     comparison cannot tell them apart: another pair is tried, and with
     none the codepoint is `unproven`, not passed;
   - with distinct fallbacks, the two Plex stacks drawing identically
     (and unlike the fallback) means the glyph came from Plex — `plex`;
     each Plex stack drawing exactly what its fallback draws alone means
     it did not — `fallback`; anything else is `unproven`.

   Controls hold the method to account: the fallback pair differs for
   `A`; `A` is `plex` in all six faces (positive); a family that does not
   exist is `fallback` (negative font control); `中`, outside every
   declared range, is never `plex` (negative codepoint control); and an
   unassigned codepoint (U+0379) is rejected — `tofu` where the browser
   draws one shared missing-glyph box, `unproven` where, as in the
   Playwright Chromium this was first run on, it draws each missing
   codepoint differently but identically in every stack. It is never
   `plex` or `fallback`. The same limit applies to the page's symbols: a
   symbol every stack draws identically is reported `unproven`, because
   this method cannot tell a system font's glyph from a missing one.

   Every declared `@font-face` rule is sampled — the start, middle and end
   of each range segment, letters, digits, punctuation and symbols only —
   and must yield at least one `plex` glyph; named letters of each script
   must be `plex` in every face that declares them. Every other sampled
   codepoint is reported with its verdict, unproven ones included.

3. **The page's symbols.** Each codepoint in `tests/fixtures/page-symbols.json`
   is probed in both families. Those no Plex range declares can never be
   `plex`; whether this browser's system fonts draw them, or draw tofu,
   is recorded, not claimed.

4. **Drawn as before.** The baseline is the stylesheet the page used to
   link — rebuilt from `plex.css` and `SOURCES.txt` and checked against
   the recorded hash of Google's reply — with each upstream url replaced
   by a `data:` url of the same bytes, each checked against the hash
   SOURCES.txt records for that url. So the baseline makes no request at
   all, and differs from the old page only in where identical bytes come
   from. Every probe verdict and every drawing must be identical under the
   baseline and the candidate. The matched view is a full-viewport type
   specimen over the frozen page — every face, its scripts and the page's
   own symbols — shot under the candidate, the baseline and the candidate
   again: the two candidate shots must agree (else the comparison proves
   nothing) and the baseline must equal them. The live board is shot
   under both too and kept for a person, with whether the two matched;
   it is not the assertion, because it was seen to move between shots on
   its own.
"""

import base64
import hashlib
import json
import re
import unicodedata
import unittest
from pathlib import Path

import benchverify as bv

S = bv.SESSION
ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "manager" / "core" / "web" / "fonts"
INVENTORY = ROOT / "tests" / "fixtures" / "page-symbols.json"

FACE = re.compile(r"/\* (?P<subset>[a-z0-9-]+) \*/\n@font-face \{\n(?P<body>[^}]*)\}", re.M)
LOCAL = re.compile(r"url\(([A-Za-z0-9_-]+\.woff2)\)")

# letters each script must draw from Plex, in every face declaring them
NAMED = {"latin": "Ag", "latin-ext": "Łő", "cyrillic": "Жя", "greek": "Ωλ",
         "vietnamese": "ạỹ", "symbols2": "─│"}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def descriptor(body, name):
    found = re.search(rf"^  {name}: ([^;]*);$", body, re.M)
    return found.group(1) if found else ""


def parse(css):
    rules = []
    for m in FACE.finditer(css):
        body = m.group("body")
        segs = []
        for part in descriptor(body, "unicode-range").split(","):
            low, _, high = part.strip().removeprefix("U+").partition("-")
            segs.append((int(low, 16), int(high or low, 16)))
        rules.append({"subset": m.group("subset"),
                      "family": descriptor(body, "font-family").strip("'"),
                      "style": descriptor(body, "font-style"),
                      "weight": descriptor(body, "font-weight"),
                      "segments": segs})
    return rules


def drawable(cp):
    if 0xD800 <= cp <= 0xDFFF:
        return False
    return unicodedata.category(chr(cp))[0] in "LNPS"


def samples(segments):
    out = []
    for low, high in segments:
        for start in (low, (low + high) // 2, high):
            for cp in range(start, min(start + 32, high + 1)):
                if drawable(cp):
                    if cp not in out:
                        out.append(cp)
                    break
    return out


def sources():
    files, sheet = [], {}
    for line in (FONTS / "SOURCES.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(" ")
        if parts[0] in ("stylesheet", "stylesheet-sha256"):
            sheet[parts[0]] = parts[1]
        else:
            files.append((parts[0], int(parts[1]), parts[2], parts[3]))
    return sheet, files


def baseline_css():
    """The stylesheet the page linked, rebuilt and checked by hash, with its
    urls turned into data: urls of the hash-checked upstream bytes."""
    sheet, files = sources()
    local = (FONTS / "plex.css").read_text(encoding="utf-8")
    urls = {name: url for _sha, _size, name, url in files}
    upstream = LOCAL.sub(lambda m: f"url({urls[m.group(1)]})", local)
    if hashlib.sha256(upstream.encode("utf-8")).hexdigest() != sheet["stylesheet-sha256"]:
        raise bv.CheckFailed("plex.css does not turn back into the recorded upstream stylesheet")
    data = {}
    for sha, size, name, url in files:
        raw = (FONTS / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) != size:
            raise bv.CheckFailed(f"{name} is not the recorded upstream file")
        data[url] = "data:font/woff2;base64," + base64.b64encode(raw).decode("ascii")
    return re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+)\)",
                  lambda m: f"url({data[m.group(1)]})", upstream)


# The probe, installed once per page. `faces` is [{family, style, weight,
# cps: [...]}]; it answers {key: verdict} with key "family|style|weight|cp".
PROBE_JS = r"""
window.__benchFontProbe = async (faces) => {
  const N = 96, c = document.createElement('canvas');
  c.width = N; c.height = N;
  const x = c.getContext('2d', { willReadFrequently: true });
  const draw = (font, text) => {
    x.clearRect(0, 0, N, N); x.font = font; x.textBaseline = 'alphabetic';
    x.fillStyle = '#000'; x.fillText(text, 12, 66);
    const d = x.getImageData(0, 0, N, N).data;
    let h = 2166136261, ink = 0;
    for (let i = 3; i < d.length; i += 4) { if (d[i]) ink++; h = Math.imul(h ^ d[i], 16777619); }
    return { h: (h >>> 0).toString(16), ink };
  };
  const PAIRS = [['monospace', 'serif'], ['serif', 'sans-serif'], ['monospace', 'sans-serif']];
  const out = {};
  for (const f of faces) {
    const fs = (stack) => `${f.style} ${f.weight} 48px ${stack}`;
    const fam = `"${f.family}"`;
    const text = f.cps.map(cp => String.fromCodePoint(cp)).join('') + '͸';
    // loading is how the subsets arrive; it is not taken as proof of anything
    await document.fonts.load(fs(fam), text);
    await document.fonts.ready;
    const refs = {};
    const ref = (stack) => refs[stack] || (refs[stack] = draw(fs(stack), '͸'));
    for (const cp of f.cps) {
      const ch = String.fromCodePoint(cp);
      const key = `${f.family}|${f.style}|${f.weight}|${cp}`;
      const first = draw(fs(`${fam}, ${PAIRS[0][0]}`), ch);
      if (!first.ink) { out[key] = { v: 'blank' }; continue; }
      if (first.h === ref(`${fam}, ${PAIRS[0][0]}`).h) { out[key] = { v: 'tofu', h: first.h }; continue; }
      let r = { v: 'unproven', why: 'every fallback pair draws it identically', h: first.h };
      for (const [A, B] of PAIRS) {
        const a = draw(fs(A), ch), b = draw(fs(B), ch);
        if (a.h === b.h) continue;
        const pA = draw(fs(`${fam}, ${A}`), ch), pB = draw(fs(`${fam}, ${B}`), ch);
        const sysTofu = a.h === ref(A).h || !a.ink;
        if (pA.h === pB.h && pA.h !== a.h && pA.h !== b.h) r = { v: 'plex', h: pA.h, pair: A + '/' + B };
        else if (pA.h === a.h && pB.h === b.h) r = { v: 'fallback', h: pA.h, pair: A + '/' + B, systemTofu: sysTofu };
        else r = { v: 'unproven', why: 'the Plex stacks disagree with both rules', h: pA.h, pair: A + '/' + B };
        break;
      }
      out[key] = r;
    }
  }
  return out;
};
true;
"""

# Holds the page still for the matched screenshots: its timers are cleared
# and animation is off — for the candidate and the baseline alike, so all
# that differs is the faces. Since the page became modules (task 124) the
# redraw entry points on `window` are only its console seam: replacing them
# no longer reaches the page's own calls, so a stream event can still
# redraw the board underneath — which is why the board shots are recorded
# rather than asserted, and the specimen (drawn over it) is the assertion.
FREEZE_JS = """(() => {
  for (const f of ['scheduleRender', 'render', 'loadState', 'renderChip', 'toast', 'fetchSetup'])
    if (typeof window[f] === 'function') window[f] = () => {};
  const last = setTimeout(() => {}, 0);
  for (let i = 1; i <= last; i++) { clearTimeout(i); clearInterval(i); }
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
  document.head.appendChild(s);
  return true;
})()"""

# A full-viewport type specimen over the frozen page: static, so two shots
# of it differ only if the faces drawing it do. %s is the rows, as JSON.
SPECIMEN_JS = """(() => {
  const rows = %s;
  const d = document.createElement('div');
  d.id = 'bench-font-specimen';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#fff;' +
    'color:#000;padding:12px;overflow:hidden;font-size:17px;line-height:1.3';
  for (const r of rows) {
    const p = document.createElement('p');
    p.style.cssText = `margin:0 0 6px;font-family:"${r.family}",monospace;` +
      `font-style:${r.style};font-weight:${r.weight};word-break:break-all`;
    p.textContent = r.text;
    d.appendChild(p);
  }
  document.body.appendChild(d);
  return true;
})()"""

SETTLE_JS = """(async () => {
  for (let i = 0; i < 20; i++) {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await document.fonts.ready;
    if (document.fonts.status === 'loaded') return true;
  }
  return false;
})()"""


class TheBoardsOwnFonts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = parse((FONTS / "plex.css").read_text(encoding="utf-8"))
        cls.inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        faces = {}
        for r in cls.rules:
            faces.setdefault((r["family"], r["style"], r["weight"]), []).append(r)
        cls.faces = faces
        page_cps = [int(h, 16) for k in ("declared", "monoOnly", "system")
                    for h in cls.inventory[k]]
        cls.plan = []
        for (family, style, weight), rules in sorted(faces.items()):
            cps = [ord("A"), 0x4E2D, 0x0379]
            for r in rules:
                cps += samples(r["segments"]) + [ord(c) for c in NAMED.get(r["subset"], "")]
            cps += page_cps
            cls.plan.append({"family": family, "style": style, "weight": weight,
                             "cps": list(dict.fromkeys(cps))})
        cls.plan.append({"family": "Bench Absent Face", "style": "normal",
                         "weight": "400", "cps": [ord("A")]})

    def setUp(self):
        b = S["browser"]
        b.viewport(1280, 800)
        b.goto("/")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its stage columns")
        self.assertTrue(b.eval(SETTLE_JS), "the page's fonts never finished loading")
        self.b = b

    def probe(self) -> dict:
        self.b.eval(PROBE_JS)
        return self.b.eval(f"window.__benchFontProbe({json.dumps(self.plan)})")

    def key(self, family, style, weight, cp):
        return f"{family}|{style}|{weight}|{cp}"

    def foreign(self) -> list:
        # a data: url is not a request; the baseline's faces are data: urls
        return self.b.eval("performance.getEntriesByType('resource').map(e => e.name)"
                           ".filter(u => !u.startsWith(location.origin) && !u.startsWith('data:'))")

    def test_1_nothing_is_fetched_off_the_board(self):
        b = self.b
        build = b.eval("document.querySelector('meta[name=\"bench-build\"]').content")
        sheets = b.eval("[...document.styleSheets].map(s => s.href)")
        origin = b.eval("location.origin")
        self.assertEqual(sheets, [f"{origin}/web/{build}/fonts/plex.css",
                                  f"{origin}/web/{build}/board.css"])
        self.probe()  # every subset the probe touches is now loaded
        self.assertEqual(self.foreign(), [], "the page fetched from off the board")
        fonts = b.eval("performance.getEntriesByType('resource').map(e => e.name)"
                       ".filter(u => u.endsWith('.woff2'))")
        self.assertTrue(fonts)
        for url in fonts:
            self.assertTrue(url.startswith(f"{origin}/web/{build}/fonts/"), url)
        loaded = b.eval("[...document.fonts].filter(f => f.status === 'loaded').length")
        S["metrics"]["fontRequests"] = {"foreign": [], "woff2": len(fonts),
                                        "facesLoaded": loaded}

    def test_2_the_probe_s_controls_hold_and_every_declared_range_draws_from_plex(self):
        got = self.probe()
        problems = []

        def want(ok, what):
            if not ok:
                problems.append(what)

        # controls
        absent = got[self.key("Bench Absent Face", "normal", "400", ord("A"))]
        want(absent["v"] == "fallback", f"negative font control gave {absent}")
        report = {"rules": [], "unproven": [], "named": {}}
        for (family, style, weight), rules in sorted(self.faces.items()):
            k = lambda cp: self.key(family, style, weight, cp)  # noqa: E731
            short = f"{family.split()[-1]} {style[0]}{weight}"
            a, cjk, tofu = got[k(ord("A"))], got[k(0x4E2D)], got[k(0x0379)]
            want(a["v"] == "plex" and a.get("pair") == "monospace/serif",
                 f"{short} positive control 'A' gave {a}")
            want(cjk["v"] != "plex", f"{short} negative control 中 gave {cjk}")
            # A missing glyph must never pass. Where the browser draws one
            # shared box it is `tofu`; this Chromium draws each unassigned
            # codepoint differently but alike in every stack, which the
            # identical-fallback rule rejects as `unproven`. Either is a
            # rejection; `plex` or `fallback` would be a false pass.
            want(tofu["v"] in ("tofu", "unproven", "blank"),
                 f"{short} missing-glyph control U+0379 gave {tofu}")
            report.setdefault("missingGlyphControl", {})[short] = tofu["v"]
            for r in rules:
                verdicts = {cp: got[k(cp)]["v"] for cp in samples(r["segments"])}
                proven = [cp for cp, v in verdicts.items() if v == "plex"]
                report["rules"].append({"face": f"{family} {style} {weight}",
                                        "subset": r["subset"], "sampled": len(verdicts),
                                        "plex": len(proven)})
                report["unproven"] += [f"{family} {style} {weight} {r['subset']} "
                                       f"U+{cp:04X} {v}" for cp, v in verdicts.items()
                                       if v != "plex"]
                want(proven, f"{short} {r['subset']}: no sample from Plex "
                             f"({sorted(set(verdicts.values()))})")
                for ch in NAMED.get(r["subset"], ""):
                    v = got[k(ord(ch))]
                    report["named"][f"{family} {style} {weight} {ch}"] = v["v"]
                    want(v["v"] == "plex", f"{short} {r['subset']} U+{ord(ch):04X} gave {v}")
        S["metrics"]["fontCoverage"] = {
            "rules": len(report["rules"]),
            "sampledNotPlex": len(report["unproven"]),
            "sampledPlex": sum(r["plex"] for r in report["rules"]),
            "missingGlyphControl": report.get("missingGlyphControl", {}),
            "problems": problems}
        out = S["shots"] / "font-coverage.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"verdicts": got, "report": report}, indent=1,
                                  sort_keys=True), encoding="utf-8")
        if problems:
            self.fail(f"{len(problems)} problems: " + " | ".join(problems[:4]))

    def test_3_the_page_s_symbols_keep_their_source(self):
        got = self.probe()
        inv = self.inventory
        seen = {"declaredPlex": [], "declaredNotPlex": [], "systemDrawn": [],
                "systemTofu": [], "systemUnproven": []}
        for family, style, weight in (("IBM Plex Sans", "normal", "400"),
                                      ("IBM Plex Mono", "normal", "400")):
            for h in inv["system"] + (inv["monoOnly"] if family.endswith("Sans") else []):
                r = got[self.key(family, style, weight, int(h, 16))]
                self.assertNotEqual(r["v"], "plex",
                                    f"U+{h} is in no {family} range and cannot be Plex")
                tag = f"{family.split()[-1]} U+{h}"
                if r["v"] == "fallback" and not r.get("systemTofu"):
                    seen["systemDrawn"].append(tag)
                elif r["v"] == "tofu" or r.get("systemTofu"):
                    seen["systemTofu"].append(tag)
                else:
                    seen["systemUnproven"].append(f"{tag} {r['v']}")
            for h in inv["declared"] + (inv["monoOnly"] if family.endswith("Mono") else []):
                r = got[self.key(family, style, weight, int(h, 16))]
                (seen["declaredPlex"] if r["v"] == "plex" else seen["declaredNotPlex"]).append(
                    f"{family.split()[-1]} U+{h} {r['v']}")
        S["metrics"]["pageSymbols"] = {k: len(v) for k, v in seen.items()}
        S["metrics"]["pageSymbolsNotProven"] = (seen["systemTofu"] + seen["systemUnproven"]
                                                + seen["declaredNotPlex"])
        # the page's ordinary punctuation is Plex's own
        for h in ("00B7", "2014", "2026"):
            self.assertIn(f"Sans U+{h} plex", seen["declaredPlex"])

    def test_4_the_bundle_draws_what_the_linked_stylesheet_drew(self):
        b = self.b
        candidate = self.probe()
        shots = S["shots"]
        css = baseline_css()
        settle = lambda: self.assertTrue(b.eval(SETTLE_JS))  # noqa: E731
        show = lambda on: b.eval(  # noqa: E731
            f"document.getElementById('bench-font-specimen').hidden = {str(not on).lower()}")

        def to_baseline():
            # the bundled stylesheet out, the old one (same bytes) in its place
            self.assertTrue(b.eval(
                "(() => { const l = document.querySelector('link[href$=\"/fonts/plex.css\"]');"
                " if (!l) return false; const s = document.createElement('style');"
                f" s.id = 'bench-baseline-fonts'; s.textContent = {json.dumps(css)};"
                " window.__benchPlexLink = l; l.replaceWith(s); return true; })()"))
            settle()
            self.assertEqual(b.eval("[...document.styleSheets].filter(s => s.href && "
                                    "s.href.includes('/fonts/')).length"), 0)

        def to_candidate():
            b.eval("document.getElementById('bench-baseline-fonts')"
                   ".replaceWith(window.__benchPlexLink)")
            settle()

        self.assertTrue(b.eval(FREEZE_JS))
        b.eval(SPECIMEN_JS % json.dumps(self.specimen()))
        show(False)
        settle()
        board_c = b.screenshot(shots / "fonts-candidate-board.png")
        show(True)
        settle()
        spec_c1 = b.screenshot(shots / "fonts-candidate-specimen.png")

        to_baseline()
        baseline = self.probe()
        spec_b = b.screenshot(shots / "fonts-baseline-specimen.png")
        show(False)
        settle()
        board_b = b.screenshot(shots / "fonts-baseline-board.png")
        show(True)

        to_candidate()
        spec_c2 = b.screenshot(shots / "fonts-candidate-specimen-again.png")
        S["required"] += [spec_c1, spec_b, board_c, board_b]
        self.assertEqual(self.foreign(), [], "the baseline fetched from off the board")

        differ = sorted(k for k in candidate
                        if candidate[k].get("v") != baseline[k].get("v")
                        or candidate[k].get("h") != baseline[k].get("h"))
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
        c1h, bh, c2h = digest(spec_c1), digest(spec_b), digest(spec_c2)
        S["metrics"]["fontScreens"] = {
            "probed": len(candidate), "probesDiffering": len(differ),
            "specimen": {"candidate": c1h, "baseline": bh, "candidateAgain": c2h,
                         "matched": bh == c1h == c2h},
            # the live board is recorded for a person to compare; it is not
            # the assertion, because it can move between shots on its own
            "board": {"candidate": digest(board_c), "baseline": digest(board_b),
                      "matched": digest(board_c) == digest(board_b)}}
        if differ:
            self.fail(f"{len(differ)} of {len(candidate)} probes differ, e.g. " +
                      "; ".join(f"{k} candidate={candidate[k]} baseline={baseline[k]}"
                                for k in differ[:3]))
        if c1h != c2h:
            self.fail("the specimen changed between the two candidate shots, so the "
                      "comparison proves nothing")
        if bh != c1h:
            self.fail("the specimen under the linked stylesheet's faces does not match "
                      "the specimen under the bundle")

    def specimen(self) -> list:
        """Rows of the type specimen: every face, its scripts, and the page's
        own non-ASCII characters, in that face."""
        page = "".join(chr(int(h, 16)) for k in ("declared", "monoOnly", "system")
                       for h in self.inventory[k])
        rows = []
        for (family, style, weight), rules in sorted(self.faces.items()):
            scripts = "".join(NAMED.get(r["subset"], "") for r in rules)
            rows.append({"family": family, "style": style, "weight": weight,
                         "text": f"{family} {style} {weight} — Aa Gg Qq 0123 ₴ № "
                                 f"{scripts} ŞşĂă ЖжҐґ {page}"})
        return rows


if __name__ == "__main__":
    unittest.main()
