"""The page's script runs strict, whole, clean and under its policy (task 124, WP15).

The board's script moved from one inline, non-strict classic `<script>` to
native ES modules held to a Content Security Policy. A parse check proves
only that the text is syntax; whether the page *runs* under module
semantics — strict mode, no TDZ read across modules, every import
resolved — and under its policy is something only a browser executing it
can show. So this case:

1. drives the real page through every view, every Settings section and a
   card's detail view, and asserts that nothing threw and that the browser
   reported **zero** Content Security Policy violations;
2. reads what the document actually runs: exactly one script, the build's
   `main.js` as a module, no inline script and no handler attribute, under
   the policy the server sent;
3. proves the policy is enforced, not merely sent: an inline script
   injected into the live page does not run, and is reported;
4. drives the `stale-page` door from a real browser (a write naming
   another build is refused and changes nothing) and the page's own
   answer to it (the reload banner, with the unsaved-work warning);
5. takes **matched-view screenshots**: the same fixture, viewport and theme
   served by the pre-change build (`BASELINE`, the launch base, run as a
   second board over the same application and work state) and by the
   candidate, compared pixel by pixel at a stated tolerance.
"""

import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
import time
import unittest
import urllib.request
import zlib
from pathlib import Path

import benchverify as bv

S = bv.SESSION
ROOT = Path(__file__).resolve().parents[2]
VIEWS = ["board", "phases", "flight", "focus", "settings", "board"]
# the build before the script moved: the base WP15 started from, whose page
# is the one inline classic script
BASELINE = "7780f042f125e0f587535119d464888fc49fe951"
# the matched views: each existing viewport family the suite measures, in
# both themes. A view is compared when both builds draw it from the same
# data; Settings is left out because it names each board's own build, port
# and paths, which differ by construction.
MATCHED_SIZES = [(1280, 800), (390, 844)]
MATCHED_VIEWS = ["board", "phases", "flight", "focus", "detail"]
# at most this share of pixels may differ between the two builds' shots;
# relative times ("3s") and the caret are the expected sources of noise
TOLERANCE = 0.005
CARD = "04-check-keyboard-focus.md"


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def load(width=1280, height=800):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/")
    if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
        raise bv.CheckFailed("the board never drew its five stage columns")
    return b


def thrown(b) -> list:
    return [e.get("exceptionDetails", {}).get("text") for e in b.console]


def csp(b) -> list:
    return list(getattr(b, "csp", []))


# ── PNG, read with the standard library ────────────────────────────────


def pixels(path: Path) -> tuple[int, int, list]:
    """(width, height, rows of RGBA bytes) for an 8-bit, non-interlaced
    RGB or RGBA PNG — what Chromium's screenshots are."""
    data = path.read_bytes()
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise bv.SetupError(f"{path.name}: a PNG this reader does not decode")
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, prev = [], bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return width, height, rows


def differing(a: Path, b: Path) -> float:
    """The share of pixels that differ between two screenshots."""
    wa, ha, ra = pixels(a)
    wb, hb, rb = pixels(b)
    if (wa, ha) != (wb, hb):
        return 1.0
    bpp = len(ra[0]) // wa
    diff = 0
    for x, y in zip(ra, rb):
        if x == y:
            continue
        for i in range(0, len(x), bpp):
            if x[i:i + 3] != y[i:i + 3]:
                diff += 1
    return diff / (wa * ha)


# ── the pre-change build, as a second board over the same data ─────────


class Baseline:
    def __init__(self):
        backend = S["backend"]
        self.tool = backend.base / "baseline-tool"
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", BASELINE,
                                  "manager/core"], capture_output=True)
        if archive.returncode != 0:
            raise bv.SetupError("the pre-change build is not in this checkout's history: "
                                + archive.stderr.decode(errors="replace")[-300:])
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(self.tool, filter="data")
        # the same machine-local state as the candidate's board — its
        # sessions, evidence and settings — so both draw the same data
        shutil.copytree(backend.tool / "manager" / "local", self.tool / "manager" / "local",
                        symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.lock", "*.sock"))
        self.port = bv.free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = dict(backend.env, BOARD_PORT=str(self.port))
        self.log = backend.base / "baseline-board.log"
        with open(self.log, "ab") as fh:
            self.proc = bv.spawn([backend.python,
                                  str(self.tool / "manager" / "core" / "board.py"),
                                  "--port", str(self.port), "--no-open"],
                                 cwd=str(self.tool), env=env, stdout=fh,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise bv.SetupError("the baseline board exited: "
                                    + self.log.read_text(errors="replace")[-800:])
            try:
                with opener.open(self.url + "/api/state", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except OSError:
                time.sleep(0.2)
        raise bv.SetupError("the baseline board did not answer")

    def stop(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, 15)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, 9)
        shutil.rmtree(self.tool, ignore_errors=True)


STILL_JS = """(() => {
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;' +
    'caret-color:transparent!important}';
  document.head.appendChild(s);
  document.activeElement && document.activeElement.blur();
  return true;
})()"""


class TheScriptRunsClean(unittest.TestCase):
    def test_1_boot_and_a_tour_throw_nothing_and_violate_nothing(self):
        # reports an earlier case raised belong to it, not to this page
        S["browser"].console.clear()
        S["browser"].csp.clear()
        b = load()
        toured = []
        for view in VIEWS:
            b.click(f'#views button[data-view="{view}"]')
            if not b.wait(f"document.querySelector('#view-{view}').classList.contains('on')", 10):
                raise bv.CheckFailed(f"the {view} view never opened")
            toured.append(view)
            if view == "settings":
                if not b.wait("document.querySelectorAll('#snav [data-ssection]').length > 3", 15):
                    raise bv.CheckFailed("Settings drew no sections")
                sections = b.eval("[...document.querySelectorAll('#snav [data-ssection]')]"
                                  ".map(e => e.dataset.ssection)")
                for sec in sections:
                    b.click(f'#snav [data-ssection="{sec}"]')
                    b.wait(f"document.querySelector('#snav [data-ssection=\"{sec}\"]')"
                           ".getAttribute('aria-current') === 'page'", 5)
                    toured.append(f"settings/{sec}")
        open_detail(b)
        tabs = b.eval("[...document.querySelectorAll('#drawer [role=tab]')].map(t => t.id)")
        for tab in tabs:
            b.click(f"#{tab}")
            toured.append(f"detail/{tab}")
        b.key("Escape")
        shot("script-policy-toured")
        S["metrics"]["scriptPolicyTour"] = toured
        S["metrics"]["scriptPolicyViolations"] = csp(b)
        self.assertEqual([], thrown(b), "the page threw during the tour: "
                         + json.dumps(toured))
        if csp(b):
            self.fail("the page broke its own policy: "
                      + " | ".join(v["text"][:240] for v in csp(b)))
        self.assertTrue(b.eval("typeof S === 'object' && !!S.state"),
                        "the page's state never loaded")

    def test_2_the_document_runs_one_module_and_no_inline_script(self):
        b = load()
        scripts = b.eval("[...document.scripts].map(s => ({src: s.getAttribute('src'),"
                         " type: s.type, inline: s.textContent.trim().length}))")
        build = b.eval("document.querySelector('meta[name=\"bench-build\"]').content")
        self.assertEqual([{"src": f"/web/{build}/main.js", "type": "module", "inline": 0}],
                         scripts)
        handlers = b.eval("[...document.querySelectorAll('*')].flatMap(e => [...e.attributes]"
                          ".filter(a => a.name.startsWith('on')).map(a => e.tagName + ' ' + a.name))")
        self.assertEqual([], handlers, "an inline handler attribute")
        policy = b.eval("fetch('/').then(r => r.headers.get('content-security-policy'))")
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-inline';", policy.split("script-src")[1].split(";")[0])
        modules = b.eval("performance.getEntriesByType('resource')"
                         ".filter(e => e.name.endsWith('.js')).map(e => e.name)")
        S["metrics"]["scriptPolicyModules"] = len(modules)
        self.assertGreater(len(modules), 30, "the page did not load its modules")
        S["metrics"]["scriptPolicyHeader"] = policy

    def test_3_the_policy_is_enforced_not_just_sent(self):
        b = load()
        b.csp.clear()
        b.eval("(() => { window.__injected = 0; const s = document.createElement('script');"
               " s.textContent = 'window.__injected = 1'; document.body.appendChild(s);"
               " return true; })()")
        time.sleep(0.5)
        b.eval("1")  # collect the reports the injection raised
        self.assertEqual(0, b.eval("window.__injected"), "an inline script ran")
        reported = csp(b)
        S["metrics"]["scriptPolicyInjectedReport"] = reported[:1]
        self.assertTrue(reported, "an inline script was refused without a report")
        b.csp.clear()

    def test_4_a_write_from_another_build_is_refused_and_the_page_says_reload(self):
        b = load()
        backend = S["backend"]
        card = backend.work / "tasks" / "review" / CARD
        answer = b.eval(
            "fetch('/api/move', {method: 'POST', headers: {'Content-Type': 'application/json',"
            " 'X-Bench-Build': '0000000000000000'}, body: JSON.stringify({file: "
            f"{json.dumps(CARD)}, from: 'review', to: 'in-progress'}})}})"
            ".then(async r => ({status: r.status, body: await r.json()}))")
        self.assertEqual(409, answer["status"], answer)
        self.assertEqual("stale-page", answer["body"]["code"])
        self.assertTrue(card.is_file(), "a refused write moved the card")
        # the page's own answer: its next write is refused the same way (the
        # refusal is played back, as a restart would make the board answer)
        b.eval("(() => { S.commentDraft['x.md'] = 'half a thought'; window.__realFetch = window.fetch;"
               " window.fetch = (u, o) => (o && o.method === 'POST') ? Promise.resolve(new Response("
               "JSON.stringify({error: 'stale', code: 'stale-page', stale: true, build: 'feedfacefeedface'}),"
               " {status: 409, headers: {'Content-Type': 'application/json'}})) : window.__realFetch(u, o);"
               " return true; })()")
        b.eval(f"move({json.dumps(CARD)}, 'review', 'in-progress')")
        if not b.wait("!document.querySelector('#stalebanner').hidden", 10):
            raise bv.CheckFailed("the page never said it is out of date")
        text = b.eval("document.querySelector('#stalebanner').innerText")
        S["metrics"]["staleBanner"] = text
        self.assertIn("out of date", text)
        self.assertIn("feedfacefeedface", text)
        self.assertIn("a comment you have not sent", text)
        self.assertTrue(b.eval("!!document.querySelector('#stalereload')"))
        self.assertEqual("alert", b.eval("document.querySelector('#stalebanner').getAttribute('role')"))
        shot("script-policy-stale-banner")
        b.eval("(() => { window.fetch = window.__realFetch; delete S.commentDraft['x.md'];"
               " return true; })()")
        self.assertTrue(card.is_file())
        self.assertEqual([], thrown(b))

    def test_5_matched_views_against_the_pre_change_build(self):
        b = S["browser"]
        base = Baseline()
        self.addCleanup(base.stop)
        results, worst = {}, 0.0
        for width, height in MATCHED_SIZES:
            for dark in (True, False):
                for view in MATCHED_VIEWS:
                    key = f"{view}-{width}x{height}-{'night' if dark else 'day'}"
                    pair = []
                    for which, url in (("baseline", base.url), ("candidate", b.origin)):
                        b.viewport(width, height)
                        b._navigate(url + "/")
                        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
                            raise bv.CheckFailed(f"{which} never drew the board")
                        b.wait("typeof S === 'object' && S.state && S.state.board", 10)
                        b.eval(f"S.dark = {str(dark).lower()}; applyTheme(); true")
                        if view == "detail":
                            open_detail(b)
                        elif view != "board":
                            b.click(f'#views button[data-view="{view}"]')
                            b.wait(f"document.querySelector('#view-{view}').classList.contains('on')", 10)
                        b.eval(STILL_JS)
                        time.sleep(0.4)
                        pair.append(shot(f"matched-{key}-{which}"))
                    share = differing(*pair)
                    worst = max(worst, share)
                    results[key] = round(share, 5)
        S["metrics"]["matchedViews"] = {"baseline": BASELINE, "tolerance": TOLERANCE,
                                        "differing": results, "worst": round(worst, 5)}
        over = {k: v for k, v in results.items() if v > TOLERANCE}
        self.assertEqual({}, over, f"views differ beyond {TOLERANCE:.1%} of their pixels")


def open_detail(b):
    sel = f'#board article.card[data-file="{CARD}"]'
    if not b.wait(f"!!document.querySelector('{sel}')", 10):
        raise bv.CheckFailed("the review card never reached the board")
    for _ in range(3):
        b.eval(f"document.querySelector('{sel}').focus()")
        b.key("Enter")
        if b.wait("document.querySelector('#drawer').classList.contains('open')", 5):
            break
    else:
        raise bv.CheckFailed("the card's detail view never opened")
    b.wait("document.querySelector('#drawer').getAnimations()"
           ".every(a => a.playState === 'finished')", 5)


if __name__ == "__main__":
    unittest.main()
