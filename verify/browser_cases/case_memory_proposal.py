"""The #151 Memory proposal package renders as the board serves it.

`develop/proposals/151-memory/plans/` is a proposal and five static
mockups meant for a work repository's `plans/`. This case puts them
exactly there — in the work root of a second disposable backend built
from the candidate's own `manager/core` — and opens every page at its
real `/files/plans/…` address in this suite's sandboxed browser:

- **the served response**: `text/html`, `nosniff`, the sandbox policy with
  `script-src 'none'` — the package works under the installed handler
  and nothing about it asks for that policy to be weakened; the index is
  served as Markdown and the board's own drawer renders it;
- **the links**: every link between pages names a page in the package
  that the board answers with a 200, and every `#fragment` names an id
  that page has;
- **the layout**: no page is wider than a 390px viewport, both themes
  apply (Night by default, Daylight through `#day`), and keyboard focus
  lands on a real link with a visible outline;
- **screenshots** of every page at 1280px and 390px, in both themes.

Measurements are read on the served page. Where the sandbox keeps the
automation from reading that document, the same bytes are measured in a
same-origin document without the policy, and the metrics say which. The
screenshots are always of the served page. The backend is made for this
case and taken down with it; the shared one is not touched.
"""

import json
import re
import shutil
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import benchverify as bv

S = bv.SESSION
PACKAGE = Path(__file__).resolve().parents[2] / "develop" / "proposals" / "151-memory" / "plans"
PAGES = ["151-memory-proposal.html", "151-memory-mock-1-library.html",
         "151-memory-mock-2-review.html", "151-memory-mock-3-ticket.html",
         "151-memory-mock-4-guide-vs-history.html", "151-memory-mock-5-states.html"]
INDEX = "151-memory-index.md"
HREF = re.compile(r'href="([^"]*)"')


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")
    missing = [n for n in [*PAGES, INDEX] if not (PACKAGE / n).is_file()]
    if missing:
        raise bv.SetupError(f"the #151 package is missing {', '.join(missing)} under {PACKAGE}")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


# What the layout and focus measurements read, in one expression.
MEASURE = """(() => {
  const root = document.documentElement;
  const a = document.activeElement;
  const cs = a ? getComputedStyle(a) : null;
  return {
    title: document.title,
    width: innerWidth,
    scrollWidth: root.scrollWidth,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    text: (document.body.innerText || '').slice(0, 160),
    links: document.querySelectorAll('a[href]').length,
    scripts: document.querySelectorAll('script').length,
    focusTag: a ? a.tagName : null,
    focusHref: a && a.getAttribute ? a.getAttribute('href') : null,
    focusOutline: cs ? cs.outlineStyle + ' ' + cs.outlineWidth : null,
    focusLeft: a && a.getBoundingClientRect ? a.getBoundingClientRect().left : null,
  };
})()"""


class MemoryProposalPackage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        base = shared.base.parent / "memory-proposal-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        plans = cls.backend.work / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        for name in [*PAGES, INDEX]:
            shutil.copy2(PACKAGE / name, plans / name)
        cls.backend.start()
        cls.origin = S["browser"].origin
        S["metrics"]["memoryProposal"] = {"pages": {}, "package": str(PACKAGE)}

    @classmethod
    def tearDownClass(cls):
        S["browser"].origin = cls.origin
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def metrics(self) -> dict:
        return S["metrics"]["memoryProposal"]

    def fetch(self, path: str):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.backend.url + path, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def browser(self, width: int, height: int):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(width, height)
        return b

    def measure(self, page: str, fragment: str = "", tabs: int = 0) -> dict:
        """The served page's own reading, or — where the sandbox keeps the
        automation out — the same bytes in an unsandboxed same-origin
        document, labelled as such."""
        b = S["browser"]
        try:
            for _ in range(tabs):
                b.key("Tab")
            got = b.eval(MEASURE)
            if isinstance(got, dict) and got.get("width"):
                got["readFrom"] = "served page"
                return got
        except (bv.CheckFailed, bv.SetupError):
            pass
        html = (PACKAGE / page).read_text(encoding="utf-8")
        b.goto("/api/state")
        b.eval("(() => { document.open(); document.write(%s); document.close(); })()"
               % json.dumps(html))
        if fragment:
            b.eval(f"location.hash = {fragment!r}")
        for _ in range(tabs):
            b.key("Tab")
        got = b.eval(MEASURE)
        got["readFrom"] = "same bytes, unsandboxed same-origin document"
        return got

    def test_1_every_page_is_served_sandboxed_and_script_free(self):
        seen = {}
        for name in PAGES:
            code, headers, body = self.fetch(f"/files/plans/{name}")
            h = {k.lower(): v for k, v in headers.items()}
            self.assertEqual(200, code, name)
            self.assertTrue(h.get("content-type", "").startswith("text/html"), (name, h))
            self.assertEqual("nosniff", h.get("x-content-type-options"), name)
            csp = h.get("content-security-policy", "")
            self.assertIn("sandbox", csp, name)
            self.assertIn("script-src 'none'", csp, name)
            self.assertNotIn(b"<script", body.lower(), name)
            self.assertNotIn(b'rel="stylesheet"', body, f"{name}: a linked stylesheet "
                             f"would be sent as octet-stream and refused")
            seen[name] = {"type": h.get("content-type"), "csp": csp, "bytes": len(body)}
        code, headers, body = self.fetch(f"/files/plans/{INDEX}")
        h = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(200, code)
        self.assertTrue(h.get("content-type", "").startswith("text/markdown"), h)
        seen[INDEX] = {"type": h.get("content-type"), "bytes": len(body)}
        code, _, _ = self.fetch("/files/plans/151-memory-not-there.html")
        self.assertEqual(404, code)
        self.metrics()["served"] = seen

    def test_2_every_link_resolves_on_the_board(self):
        checked = []
        for name in [*PAGES, INDEX]:
            text = (PACKAGE / name).read_text(encoding="utf-8")
            targets = HREF.findall(text) + re.findall(r"\]\((/files/[^)\s]+)\)", text)
            for href in targets:
                path, _, frag = href.partition("#")
                if path == "":
                    target = name
                elif path.startswith("/files/plans/"):
                    target = path[len("/files/plans/"):]
                else:
                    self.assertNotIn(":", path, f"{name}: {href} leaves the board")
                    self.assertNotIn("/", path, f"{name}: {href} is not a flat plans/ name")
                    target = path
                self.assertIn(target, [*PAGES, INDEX], f"{name}: {href} is not in the package")
                code, _, _ = self.fetch(f"/files/plans/{target}")
                self.assertEqual(200, code, f"{name}: {href}")
                if frag and frag != "day":
                    body = (PACKAGE / target).read_text(encoding="utf-8")
                    self.assertIn(f'id="{frag}"', body, f"{name}: {href} names no id")
                checked.append(f"{name} → {href}")
        self.assertGreater(len(checked), 40)
        self.metrics()["linksChecked"] = len(checked)

    def test_3_pages_render_in_both_themes_at_desktop_and_narrow_widths(self):
        pages = self.metrics()["pages"]
        for name in PAGES:
            stem = name[len("151-memory-"):-len(".html")]
            readings = {}
            for label, (w, h) in (("desktop", (1280, 900)), ("narrow", (390, 844))):
                for theme, frag in (("night", ""), ("day", "day")):
                    b = self.browser(w, h)
                    b.goto(f"/files/plans/{name}" + (f"#{frag}" if frag else ""))
                    time.sleep(0.4)
                    shot(f"memory-{stem}-{label}-{theme}")
                    m = self.measure(name, frag)
                    readings[f"{label}-{theme}"] = m
                    self.assertLessEqual(m["scrollWidth"], m["width"] + 1,
                                         f"{name} overflows at {w}px ({theme}): {m}")
                    self.assertEqual(0, m["scripts"], name)
                    self.assertGreater(m["links"], 3, name)
                    self.assertIn("#151", m["title"], name)
            self.assertNotEqual(readings["desktop-night"]["bodyBg"],
                                readings["desktop-day"]["bodyBg"],
                                f"{name}: Daylight did not change the theme")
            pages[name] = {k: {f: v[f] for f in ("width", "scrollWidth", "bodyBg", "readFrom")}
                           for k, v in readings.items()}

    def test_4_keyboard_focus_reaches_real_links_visibly(self):
        focus = {}
        for name in PAGES:
            b = self.browser(1280, 900)
            b.goto(f"/files/plans/{name}")
            time.sleep(0.3)
            m = self.measure(name, tabs=1)
            # the first stop is the skip link, brought into view
            self.assertEqual("A", m["focusTag"], f"{name}: {m}")
            self.assertEqual("#main", m["focusHref"], f"{name}: {m}")
            self.assertGreaterEqual(m["focusLeft"], 0, f"{name}: skip link stays off screen")
            m2 = self.measure(name, tabs=1) if m["readFrom"] == "served page" else \
                self.measure(name, tabs=2)
            self.assertEqual("A", m2["focusTag"], f"{name}: {m2}")
            self.assertTrue(m2["focusOutline"].startswith("solid"),
                            f"{name}: focus is not visible: {m2}")
            if name == PAGES[3]:
                shot("memory-mock-3-ticket-keyboard-focus")
            focus[name] = {"first": m["focusHref"], "second": m2["focusHref"],
                           "outline": m2["focusOutline"], "readFrom": m2["readFrom"]}
        self.metrics()["focus"] = focus

    def test_5_the_index_opens_in_the_board_drawer_and_links_onward(self):
        b = self.browser(1280, 900)
        b.goto("/")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its stage columns")
        if not b.eval("document.getElementById('logpanel').classList.contains('open')"):
            b.click("#actbtn")
        if not b.wait(f"!!document.querySelector('.xfile[data-name=\"{INDEX}\"]')", 10):
            raise bv.CheckFailed("the Activity panel does not list the index under plans/")
        b.eval("document.querySelectorAll('#loghead details').forEach(d => d.open = true)")
        b.click(f'.xfile[data-name="{INDEX}"]')
        if not b.wait("document.getElementById('drawer').classList.contains('open')", 10):
            raise bv.CheckFailed("the index did not open in the drawer")
        text = b.eval("document.getElementById('drawerbody').innerText")
        self.assertIn("Project Memory", text)
        self.assertIn("Owner decisions", text)
        hrefs = b.eval("[...document.querySelectorAll('#drawerbody a[href]')]"
                       ".map(a => a.getAttribute('href'))")
        self.assertIn("/files/plans/151-memory-proposal.html", hrefs)
        self.assertEqual(6, len([h for h in hrefs if h.startswith("/files/plans/151-memory-")]),
                         hrefs)
        shot("memory-index-in-board-drawer")
        self.metrics()["drawer"] = {"links": hrefs, "text": text[:300]}
