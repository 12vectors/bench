"""The application preview (task 91), driven through the setup guide in a
browser against the disposable backend's real lifecycle.

The backend's application gains a tiny web app, a disposable fixture
seeder and a branch for the review card; the preview's secret lives in the
backend's own machine-local secrets file. Everything the page shows is
compared with `GET /api/preview`, and every process it starts is stopped
through the page and confirmed gone from the backend's own record.

What is covered: discovery read without running anything; the
configuration validated, its call-out acknowledged and saved on an armed
click; Prepare and Start each armed; readiness reached and the URL served
from the disposable fixture; the source shown and then shown *stale* after
a commit in the card's worktree (the task result revalidated, never
relabelled); the scrubbed log; Check readiness and Stop fired from the
keyboard; nothing of the preview's group left behind; both themes, and a
390 px layout without horizontal overflow.

Named to run after case_setup, whose decisions need the preview area
undecided. Run through verify/browser; any other way these refuse.
"""

import json
import os
import subprocess
import time
import unittest
import urllib.request

import benchverify as bv

S = bv.SESSION
SECRET = "pv-secret-value-91-browser"
CARD = "04-check-keyboard-focus.md"

SERVE = r'''
import http.server, json, os, pathlib
port, host = int(os.environ["PORT"]), os.environ["HOST"]
print("token", os.environ.get("API_TOKEN", ""), flush=True)
open("pid", "w").write(str(os.getpid()))
rows = json.loads(pathlib.Path("data/fixture.json").read_text())
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"<h1>preview fixture rows={len(rows)}</h1>".encode())
print("listening", flush=True)
http.server.ThreadingHTTPServer((host, port), H).serve_forever()
'''
SEED = ("import json, pathlib\np = pathlib.Path('data'); p.mkdir(exist_ok=True)\n"
        "(p / 'fixture.json').write_text(json.dumps([1, 2, 3]))\n")
CONFIG = {"v": 1, "start": "python3 serve.py", "port": "auto",
          "prepare": [{"name": "fixture", "run": "python3 seed.py",
                       "data": "disposable"}],
          "fixture": "fixture",
          "ready": {"path": "/", "status": 200, "contains": "rows=3", "timeout": 20},
          "env": {"API_TOKEN": {"secret": "PREVIEW_TOKEN"}},
          "stop": {"grace": 2}}


def setUpModule():
    if not S.get("browser"):
        raise bv.SetupError("run these through verify/browser, which provides "
                            "the browser and the disposable backend")


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.email=bv@example.com",
                        "-c", "user.name=bench-verify", *args], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise bv.SetupError(f"fixture git {args}: {r.stderr.strip()}")
    return r.stdout.strip()


def api() -> dict:
    code, body = S["backend"].get("/api/preview?file=" + CARD)
    if code != 200:
        raise bv.CheckFailed(f"GET /api/preview answered {code}")
    return json.loads(body)["preview"]


def shot(name: str):
    path = S["shots"] / f"{name}.png"
    S["browser"].screenshot(path)
    S["required"].append(path)


def word(b) -> str:
    return b.eval("(document.querySelector('#preview-life .setup-boot .sbadge')"
                  " || {}).textContent")


def wait_word(b, words, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if word(b) in words:
            return word(b)
        time.sleep(0.25)
    raise bv.CheckFailed(f"the page never read {words}: it reads {word(b)!r}; "
                         f"the backend says {api()['lifecycle']['status']}")


def open_preview(width=1440, height=900, theme="dark"):
    b = S["browser"]
    b.viewport(width, height)
    b.goto("/#setup")
    if not b.wait("!!document.querySelector('#preview-life')", 15):
        b.goto("/")
        if not b.wait("!!document.querySelector('#setupchip')", 15):
            raise bv.CheckFailed("the board did not load")
        b.eval("document.querySelector('#setupchip').hidden = false")
        b.click('#setupchip')
        if not b.wait("!!document.querySelector('#preview-life')", 15):
            raise bv.CheckFailed("the guide drew no Application preview panel")
    if b.eval("document.documentElement.dataset.theme") != theme:
        b.click('#themebtn')
    return b


def double(b, selector):
    """An armed control: the first click arms, the second fires."""
    b.click(selector)
    if not b.wait(f"document.querySelector('{selector}').classList.contains('armed')", 5):
        raise bv.CheckFailed(f"{selector} did not arm on the first click")
    b.click(selector)


_DEBUG = "/home/dev/12v/bench/worktrees/91-configure-safe-application-previews/.pv-debug.log"


class P1Lifecycle(unittest.TestCase):
    def run(self, result=None):  # TEMPORARY DIAGNOSTIC — removed before the final commit
        out = super().run(result)
        try:
            with open(_DEBUG, "a") as fh:
                for test, text in (result.failures + result.errors):
                    if test is self:
                        fh.write(f"== {self.id()}\n{text[-3000:]}\n")
                fh.write(f"-- ran {self.id()}\n")
        except OSError:
            pass
        return out

    @classmethod
    def setUpClass(cls):
        backend = S["backend"]
        app = backend.app
        (app / "serve.py").write_text(SERVE)
        (app / "seed.py").write_text(SEED)
        (app / ".gitignore").write_text("data/\npid\n")
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", "a disposable web app for the preview case")
        git(app, "branch", "-f", "task/" + CARD[:-3])
        secrets = backend.tool / "manager" / "local" / "preview.secrets"
        secrets.write_text(f"PREVIEW_TOKEN={SECRET}\n")
        secrets.chmod(0o600)

    def test_1_discovery_and_configuration_run_nothing(self):
        b = open_preview()
        found = api()
        self.assertEqual(found["lifecycle"]["status"], word(b))
        self.assertIn("not a working preview", b.eval(
            "document.querySelector('#preview-life').textContent"))
        self.assertIsNone(found["lifecycle"]["preview"])
        trees = S["backend"].trees
        b.eval("(() => { const t = document.querySelector('#pv-draft');"
               f" t.value = {json.dumps(json.dumps(CONFIG, indent=2))};"
               " t.dispatchEvent(new Event('input', {bubbles: true})); })()")
        b.click('[data-pvact="validate"]')
        if not b.wait("!!document.querySelector('[data-pvack=\"destructive-step\"]')", 10):
            raise bv.CheckFailed("validation did not call out the data step")
        b.click('[data-pvack="destructive-step"]')
        double(b, '[data-pvact="configure"]')
        wait_word(b, {"configured"})
        self.assertFalse((trees / CARD[:-3]).exists(),
                         "configuring made a worktree")
        self.assertTrue(api()["configuration"]["valid"])
        shot("preview-configured-1440x900")

    def test_2_prepare_start_reach_and_revalidate(self):
        b = open_preview()
        self.assertEqual(CARD, b.eval("document.querySelector('#pv-card').value"))
        double(b, '[data-pvact="prepare"]')
        wait_word(b, {"prepared"})
        double(b, '[data-pvact="start"]')
        wait_word(b, {"ready"})
        found = api()
        live = found["lifecycle"]["preview"]
        head = git(S["backend"].app, "rev-parse", "task/" + CARD[:-3])
        self.assertEqual(head, live["source"]["commit"])
        url = (b.eval("(document.querySelector('#pv-url') || {}).href") or "").rstrip("/")
        self.assertEqual(live["url"], url)
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertIn("rows=3", opener.open(url + "/", timeout=5).read().decode())
        self.assertIn(head[:12], b.eval("document.querySelector('#pv-live').textContent"))
        shot("preview-ready-1440x900")

        b.click('[data-pvact="log"]')
        if not b.wait("!!document.querySelector('#pv-log')", 10):
            raise bv.CheckFailed("the log never appeared")
        log = b.eval("document.querySelector('#pv-log').textContent")
        self.assertIn("listening", log)
        self.assertNotIn(SECRET, log)
        self.assertNotIn(SECRET, b.eval("document.body.innerHTML"))

        # the card's result moves on: the running preview is not relabelled
        tree = S["backend"].trees / CARD[:-3]
        (tree / "later.txt").write_text("later\n")
        git(tree, "add", "later.txt")
        git(tree, "commit", "-q", "-m", "a later result")
        b.click('[data-setupact="reread:all"]')
        if not b.wait("document.querySelector('#pv-live').textContent"
                      ".includes('not the selected source')", 15):
            raise bv.CheckFailed("a later commit did not mark the preview stale")
        self.assertEqual("stale", api()["lifecycle"]["currency"]["state"])
        shot("preview-stale-1440x900")

    def test_3_keyboard_check_and_stop_leave_nothing_behind(self):
        b = open_preview()
        pidfile = S["backend"].trees / CARD[:-3] / "pid"
        if word(b) != "ready" or not pidfile.is_file():
            raise bv.CheckFailed(f"no running preview to check and stop: the page "
                                 f"reads {word(b)!r}")
        pid = int(pidfile.read_text())
        b.eval("document.querySelector('[data-pvact=\"check\"]').focus()")
        self.assertEqual("check", b.eval("document.activeElement.dataset.pvact"))
        b.key("Enter")
        if not b.wait("[...document.querySelectorAll('.toast, #toast')]"
                      ".some(t => t.textContent.includes('readiness'))", 10):
            raise bv.CheckFailed("Check readiness from the keyboard said nothing")
        b.eval("document.querySelector('[data-pvact=\"stop\"]').focus()")
        b.key("Enter")
        wait_word(b, {"stopped"})
        cleanup = api()["lifecycle"]["preview"]["cleanup"]
        self.assertEqual("gone", cleanup["group"])
        self.assertEqual("released", cleanup["port"])
        try:
            os.kill(pid, 0)
            alive = "Z" not in open(f"/proc/{pid}/stat").read().split(")")[1][:3]
        except (ProcessLookupError, OSError):
            alive = False
        self.assertFalse(alive, "the preview's process outlived Stop")

    def test_4_themes_and_narrow_layout_fit(self):
        for theme in ("light", "dark"):
            for w, h in ((1440, 900), (390, 780)):
                b = open_preview(w, h, theme)
                width = b.eval("document.documentElement.scrollWidth")
                self.assertLessEqual(width, w + 1, f"horizontal overflow at {w}px {theme}")
                box = b.eval("(() => { const r = document.querySelector('#preview-life')"
                             ".getBoundingClientRect(); return [r.x, r.right]; })()")
                self.assertGreaterEqual(box[0], -1)
                self.assertLessEqual(box[1], w + 1)
                if w == 390:
                    shot(f"preview-{theme}-390x780")
        names = b.eval("[...document.querySelectorAll('#preview-life button')]"
                       ".filter(x => !(x.getAttribute('aria-label') || x.textContent).trim())"
                       ".length")
        self.assertEqual(0, names, "a preview control has no accessible name")


if __name__ == "__main__":
    unittest.main()
