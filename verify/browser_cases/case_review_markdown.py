"""Reports read as documents (task 83), in the browser.

The review round from card 77's screenshot — a bold label with its list on
the next line, "Before merging", nested bullets, identifiers, a table, a
fence — drawn in the Reviews section as rendered Markdown in both themes,
beside a "before" view at the same viewport that rebuilds the old
`<pre class="rtext">` from the same text, so a reviewer compares like with
like. Then: proportional prose and monospace code by computed style,
readable contrast, headings nested under the section's own, keyboard
reach for links and the original-Markdown fold, hostile content inert,
nothing posted, fetched off the board or written by reading, wide code
and tables scrolling in place at 390px and a real 200% zoom, and a live
update that keeps the open rounds and the reading position.

The card is a disposable one written into the fixture's work state and
removed afterwards.
"""

import json
import re
import unittest
from pathlib import Path

import benchverify as bv

S = bv.SESSION
STEM = "10-review-markdown-fixture"
FILE = f"{STEM}.md"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "review-markdown"
ROUND = (FIXTURES / "round-like-77.md").read_text(encoding="utf-8").strip()
# the hostile fixture without the lines a card's own parser reads as
# records — rendering them is the unit suite's case; here they would
# forge a verdict and a finding on the disposable card
HOSTILE = "\n".join(l for l in (FIXTURES / "hostile.md").read_text(encoding="utf-8").split("\n")
                    if not l.startswith(("PR REVIEW:", "<!-- bench:", "**Status:**"))).strip()
CARD = f"""# Review markdown fixture

**Status:** Review
**Priority:** Medium

## Context

A disposable card for the review-rendering browser case.

---

## PR review at 1111111111aa — 2026-11-07 10:00 (Basil)

{HOSTILE}

PR REVIEW: REQUEST CHANGES

---

## Relevance review — 2026-11-07 12:00 (Juno)

**Verdict:** Still relevant — `board.html` still draws reports as a `<pre>`.

---

## PR review at 0123456789ab — 2026-11-08 15:20 (Olive)

{ROUND}
"""

DRAWER = "document.querySelector('#drawerbody')"
# PR review rounds sit in their own group of Reviews since task 84
LATEST = "#dsec-reviews .rvgroup > details.rpt[data-keep^=\"rv-\"]"
BODY = f"{LATEST} .rmd"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


# relative luminance contrast of two computed CSS colours, in the page
CONTRAST = """((fgEl) => {
  const rgb = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  const bgOf = (el) => { for (; el; el = el.parentElement) {
      const c = rgb(getComputedStyle(el).backgroundColor);
      if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) return c; }
    return [255, 255, 255]; };
  const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const a = lum(rgb(getComputedStyle(fgEl).color)), b = lum(bgOf(fgEl));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
})"""


class ReviewMarkdown(unittest.TestCase):

    def setUp(self):
        backend = S["backend"]
        self.card = backend.work / "tasks" / "review" / FILE
        self.card.write_text(CARD, encoding="utf-8")
        self.addCleanup(self.card.unlink, True)
        # the theme is remembered in the browser; later cases get the default
        self.addCleanup(lambda: S["browser"].eval("localStorage.removeItem('bench-theme')"))

    def theme(self, dark: bool):
        b = S["browser"]
        b.eval(f"S.dark = {str(dark).lower()}; applyTheme();")
        self.assertEqual(b.eval("document.documentElement.dataset.theme"), "dark" if dark else "light")

    def open_reviews(self, width=1280, height=900, zoom=None, dark=None):
        b = S["browser"]
        if zoom:
            b.zoom(width, height, zoom)
        else:
            b.viewport(width, height)
        b.goto("/")
        if dark is not None:
            self.theme(dark)
        # every non-GET request from here on is recorded: reading must post nothing
        b.eval("window.__posts = []; { const f = window.fetch; window.fetch = (u, o) => {"
               " if (o && o.method && o.method !== 'GET') window.__posts.push(String(u));"
               " return f.call(window, u, o); }; }")
        sel = f'#board article.card[data-file="{FILE}"]'
        self.assertTrue(b.wait(f"!!document.querySelector('{sel}')", 15),
                        "the disposable card never reached the board")
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        self.assertTrue(b.wait("!!document.querySelector('#drawer.open .dtitle')", 5),
                        "the detail view did not open")
        self.assertTrue(b.wait("!!(S.acc[%s] || {}).data" % json.dumps(FILE), 10),
                        "the card's records never loaded")
        self.assertTrue(b.wait("document.querySelector('#drawer').getAnimations()"
                               ".every(a => a.playState === 'finished')", 5),
                        "the drawer never finished opening")
        b.click("#dtab-reviews")
        self.assertTrue(b.wait("!document.querySelector('#dsec-reviews').hidden"
                               f" && !!document.querySelector('{BODY}')", 5),
                        "the Reviews section did not draw a rendered round")

    def no_sideways_scroll(self, where: str):
        b = S["browser"]
        over = b.eval(f"{DRAWER}.scrollWidth - {DRAWER}.clientWidth")
        self.assertLessEqual(over, 1, f"the detail view scrolls sideways at {where}")
        for what in ("pre", ".tablewrap"):
            box = b.eval(f"(() => {{ const el = document.querySelector('{BODY} {what}');"
                         " const r = el.getBoundingClientRect();"
                         " const d = document.querySelector('#drawerbody').getBoundingClientRect();"
                         " return [r.right - d.right, getComputedStyle(el).overflowX]; })()")
            self.assertLessEqual(box[0], 1, f"{what} spills out of the drawer at {where}")
            self.assertEqual(box[1], "auto", f"{what} does not scroll in place at {where}")

    def before_view(self):
        """Swap the rendered body for the markup the board drew before task
        83 — `<pre class="rtext">` of the same text — so the screenshot is
        a like-for-like 'before' at this viewport and theme."""
        S["browser"].eval(f"(() => {{ const body = document.querySelector('{BODY}');"
                          " const pre = document.createElement('pre'); pre.className = 'rtext';"
                          " pre.textContent = body.parentElement.querySelector('.rraw').textContent;"
                          " body.dataset.was = '1'; body.replaceWith(pre); })()")

    def test_the_screenshot_case_reads_as_a_document(self):
        b = S["browser"]
        before_bytes = self.card.read_bytes()

        for dark in (False, True):
            name = "dark" if dark else "light"
            self.open_reviews(dark=dark)

            # — the structure a reader scans by —
            self.assertTrue(b.eval(f"document.querySelector('{LATEST}').open"),
                            "the latest round is not open")
            heads = b.eval(f"[...document.querySelectorAll('{BODY} h4, {BODY} h5, {BODY} h6')]"
                           ".map(h => h.tagName + ':' + h.innerText)")
            self.assertEqual(heads, ["H4:Before merging", "H5:To know (not blocking)"])
            self.assertEqual(b.eval(f"document.querySelectorAll('{BODY} h1, {BODY} h2, {BODY} h3').length"), 0)
            self.assertEqual(b.eval(f"document.querySelectorAll('{BODY} > ul > li > ul > li').length"), 2,
                             "the nested bullets did not nest")
            self.assertEqual(b.eval(f"document.querySelectorAll('{BODY} > ol > li').length"), 2)
            text = b.eval(f"document.querySelector('{BODY}').innerText")
            for mark in ("**", "`", "## ", "| ---"):
                self.assertNotIn(mark, re.sub(r"def agent_choice.*?\n", "", text, flags=re.S)
                                 .replace("**not bold**, `not code`", ""), f"{mark!r} is visible")

            # — proportional prose, monospace only for code —
            fonts = b.eval(f"(() => {{ const f = s => getComputedStyle(document.querySelector('{BODY} ' + s)).fontFamily;"
                           " return [f('p'), f('li'), f('code'), f('pre')]; })()")
            self.assertIn("Plex Sans", fonts[0])
            self.assertIn("Plex Sans", fonts[1])
            self.assertIn("Mono", fonts[2])
            self.assertIn("Mono", fonts[3])
            weight = b.eval(f"getComputedStyle(document.querySelector('{BODY} h4')).fontWeight")
            self.assertGreaterEqual(int(weight), 600)

            # — contrast in this theme: prose, headings, links, code —
            for what in ("p", "li", "h4", "a", "code", "td"):
                ratio = b.eval(f"{CONTRAST}(document.querySelector('{BODY} {what}'))")
                self.assertGreaterEqual(ratio, 4.5, f"{what} contrast {ratio:.2f} in the {name} theme")

            # — metadata stays in the summary, apart from the prose —
            summary = b.eval(f"document.querySelector('{LATEST} > summary').innerText")
            self.assertIn("Olive", summary)
            self.assertIn("0123456789ab", summary)
            self.assertNotIn("Before merging", summary)
            shot(f"review-markdown-after-{name}-1280x900")

            # — the same text the way the board drew it before —
            self.before_view()
            shot(f"review-markdown-before-{name}-1280x900")

        # — keyboard: the link and the original-Markdown fold are reachable —
        self.open_reviews(dark=False)
        # focus is asked for until it holds: a state load landing between
        # the two calls redraws the drawer, which is not this check's subject
        self.assertTrue(b.wait(f"(() => {{ const a = document.querySelector('{BODY} a');"
                               " a.focus(); return document.activeElement === a; })()", 5),
                        "a link in the report cannot take focus")
        self.assertEqual(b.eval(f"document.querySelector('{BODY} a').getAttribute('rel')"),
                         "noopener noreferrer")
        src = f"{LATEST} > details.rsrc"
        self.assertTrue(b.wait(f"(() => {{ const s = document.querySelector('{src} > summary');"
                               " s.focus(); return document.activeElement === s; })()", 5),
                        "the original-Markdown fold cannot take focus")
        b.click(f"{src} > summary")
        self.assertTrue(b.wait(f"document.querySelector('{src}').open", 5),
                        "the original Markdown did not open")
        raw = b.eval(f"document.querySelector('{src} .rraw').textContent")
        self.assertEqual(raw, ROUND, "the original view is not the report as filed")
        self.assertTrue(b.eval(f"!!document.querySelector('{src} button.rcopy')"))
        shot("review-markdown-original-light-1280x900")

        # — the earlier rounds and the relevance review render the same way —
        b.click("#dsec-reviews details[data-keep=\"oldrounds\"] > summary")
        old = "#dsec-reviews details[data-keep=\"oldrounds\"] details.rpt"
        b.click(f"{old} > summary")
        self.assertTrue(b.wait(f"document.querySelector('{old}').open", 5))
        hostile = f"{old} .rmd"
        self.assertEqual(b.eval(f"document.querySelectorAll('{hostile} script, {hostile} img,"
                                f" {hostile} iframe, {hostile} svg').length"), 0)
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{hostile} *')]"
                                ".filter(el => [...el.attributes].some(a => /^on/i.test(a.name))).length"), 0,
                         "an event attribute reached the page")
        hrefs = b.eval(f"[...document.querySelectorAll('{hostile} a')].map(a => a.getAttribute('href'))")
        for href in hrefs:
            # the web, or a relative path: no scheme, so nothing to run
            self.assertTrue(href.startswith("https://") or ":" not in href,
                            f"{href!r} became a link")
            self.assertNotRegex(href, r'[<>"\s]', f"{href!r} carries markup")
        self.assertIn("<script>", b.eval(f"document.querySelector('{hostile}').innerText"),
                      "the script tag was dropped rather than shown as text")
        shot("review-markdown-hostile-light-1280x900")
        # the relevance review is read in Reviews, under its own group
        # (task 84); Activity dates it and links there
        rel = "#dsec-reviews details.rvrel"
        self.assertTrue(b.wait(f"!!document.querySelector('{rel} .rmd strong')", 5),
                        "the relevance review is not rendered")
        self.assertEqual(b.eval(f"document.querySelector('{rel} .rmd code').innerText"), "board.html")
        b.click("#dtab-activity")
        self.assertTrue(b.wait("!document.querySelector('#dsec-activity').hidden", 5))
        self.assertTrue(b.eval("!!document.querySelector('#dsec-activity [data-goreview^=\"rl-\"]')"),
                        "Activity does not link to the relevance review")

        # — reading changed nothing and reached nowhere —
        self.assertEqual(b.eval("window.__posts"), [], "reading a report posted something")
        # the board's own stylesheet loads its typefaces from Google Fonts
        # (a choice that predates this card); anything else off the board's
        # origin came from a report
        foreign = b.eval("performance.getEntriesByType('resource').map(e => e.name)"
                         ".filter(u => !u.startsWith(location.origin))"
                         ".filter(u => !/^https:\\/\\/fonts\\.(googleapis|gstatic)\\.com\\//.test(u))")
        self.assertEqual(foreign, [], "a report made the page fetch from elsewhere")
        self.assertEqual(self.card.read_bytes(), before_bytes, "reading the card wrote to it")

    def test_narrow_zoom_and_a_live_update(self):
        b = S["browser"]
        for where, args in (("390x844", dict(width=390, height=844)),
                            ("1280x800 at 200% zoom", dict(width=1280, height=800, zoom=2))):
            for dark in (False, True):
                self.open_reviews(dark=dark, **args)
                if "zoom" in args:
                    self.assertEqual(b.eval("window.devicePixelRatio"), 2)
                    self.assertEqual(b.eval("window.innerWidth"), 640)
                self.no_sideways_scroll(where)
                b.eval(f"document.querySelector('{BODY} h4').scrollIntoView()")
                tag = where.replace(" ", "-").replace("%", "pc")
                shot(f"review-markdown-after-{'dark' if dark else 'light'}-{tag}")
                self.before_view()
                b.eval(f"document.querySelector('{LATEST} pre.rtext').scrollIntoView()")
                shot(f"review-markdown-before-{'dark' if dark else 'light'}-{tag}")

        # — expanded reading mode keeps the measure and the scroll-in-place —
        self.open_reviews(width=1440, height=900, dark=False)
        b.click("#dwide")
        self.assertTrue(b.wait("S.dwide === true", 5))
        self.assertTrue(b.wait(f"!!document.querySelector('{BODY}')", 5))
        self.no_sideways_scroll("1440x900 expanded")
        measure = b.eval(f"document.querySelector('{BODY}').getBoundingClientRect().width"
                         f" / parseFloat(getComputedStyle(document.querySelector('{BODY}')).fontSize)")
        self.assertLessEqual(measure, 50, "the prose runs the full width of the expanded view")
        shot("review-markdown-expanded-light-1440x900")
        b.click("#dwide")
        self.assertTrue(b.wait("S.dwide === false", 5))

        # — a live update keeps the section, the open round and the place —
        self.open_reviews(width=1280, height=800, dark=False)
        b.eval(f"{DRAWER}.scrollTop = document.querySelector('{BODY} h4').offsetTop")
        b.eval(f"{DRAWER}.dispatchEvent(new Event('scroll'))")
        kept = b.eval(f"document.querySelector('{BODY} h4').getBoundingClientRect().top")
        code, body = S["backend"].post("/api/card/finding", {
            "file": FILE, "stage": "review", "event": "opened",
            "text": "Typing a model does not enable Apply"})
        self.assertEqual(code, 200, body[:200])
        self.assertTrue(b.wait("document.querySelector('#dsec-reviews .acount').innerText"
                               ".startsWith('1 open')", 15), "the live update never arrived")
        self.assertFalse(b.eval("document.querySelector('#dsec-reviews').hidden"),
                         "a live update left the Reviews section")
        self.assertTrue(b.eval(f"document.querySelector('{LATEST}').open"),
                        "a live update closed the round")
        self.assertLessEqual(abs(b.eval(f"document.querySelector('{BODY} h4').getBoundingClientRect().top") - kept),
                             4, "a live update moved what the reader was reading")
        b.viewport(1280, 800)
        S["metrics"]["reviewMarkdown"] = {"themes": 2, "widths": ["1280", "390", "1440 expanded",
                                                                   "1280 at 200% zoom"]}
