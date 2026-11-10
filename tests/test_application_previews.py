"""Safe application previews (task 91).

Five acts — configure, prepare, start, check readiness, stop — on the
drive lifecycle bench already had. The promises under test:

- **Discovery and reading are inert.** A project's start scripts are read
  as proposals; a GET of the guide or the preview runs, prepares and
  writes nothing.
- **Configuration refuses what is unsafe and calls out what is risky.**
  Exposure beyond loopback, tunnels, shell composition, literal secrets,
  production defaults and undeclared destructive data steps are refused;
  external services, network use and declared-disposable data steps must
  each be acknowledged before the save.
- **End to end, against a disposable web app with a disposable fixture:**
  prepare and start are explicit; a missing secret, an open secrets file,
  an unavailable dependency and a port held by an unrelated service each
  refuse with nothing run and nothing of anyone else's stopped; the
  preview is bound to its source and reads stale after a commit; logs
  carry no secret value; stop leaves no process of the preview's group
  and releases its port, escalating past an app that ignores SIGTERM.
- **Honest endings.** A readiness timeout, an exit before ready and an
  exit after ready each read as themselves; a restarted board reconnects
  only to a process group it can show is its own, and asks readiness
  again before calling it ready.
- **Not applicable** is a decision with a reason and a linked
  alternative — a registered check or a card — never an invented one.

    python3 -m unittest tests.test_application_previews -v
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_setup_guide import (HEADERS, Project, by_id, free_port, git,
                                    snapshot)

SECRET = "s3cr3t-preview-value-91"

SERVE = r'''
import http.server, json, os, pathlib, signal, subprocess, sys
port, host = int(os.environ["PORT"]), os.environ["HOST"]
if os.environ.get("IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
token = os.environ.get("API_TOKEN", "")
print("token is", token, flush=True)
print(f"API_TOKEN={token}", flush=True)
open("pid", "w").write(str(os.getpid()))
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open("child-pid", "w").write(str(child.pid))
data = pathlib.Path("data/fixture.json")
rows = json.loads(data.read_text()) if data.exists() else None
mode = os.environ.get("APP_MODE", "")

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path == "/exit":
            self.send_response(200); self.end_headers()
            os._exit(4)
        body = f"preview fixture rows={len(rows) if rows is not None else 'none'} mode={mode}"
        self.send_response(200); self.end_headers()
        self.wfile.write(body.encode())

print("listening", flush=True)
http.server.ThreadingHTTPServer((host, port), H).serve_forever()
'''

SEED = r'''
import json, pathlib
p = pathlib.Path("data"); p.mkdir(exist_ok=True)
(p / "fixture.json").write_text(json.dumps([{"id": i} for i in range(3)]))
print("seeded 3 disposable rows")
'''


def config_doc(port, **over) -> dict:
    doc = {"v": 1, "start": "python3 serve.py", "port": port,
           "prepare": [{"name": "fixture", "run": "python3 seed.py",
                        "data": "disposable"}],
           "fixture": "fixture",
           "ready": {"path": "/", "status": 200, "contains": "rows=3",
                     "timeout": 10},
           "env": {"APP_ENV": "development", "API_TOKEN": {"secret": "PREVIEW_TOKEN"}},
           "stop": {"grace": 1}}
    doc.update(over)
    return doc


def validate(p: Project, docs: list) -> list:
    return p.run("import previews\nR = [previews.validate(d) for d in "
                 f"{docs!r}]")


def codes(found: dict) -> set:
    return {x["code"] for x in found["problems"]}


def wcodes(found: dict) -> set:
    return {x["code"] for x in found["warnings"]}


class ConfigurationRefusesAndCallsOut(unittest.TestCase):
    def test_exposure_tunnels_shells_secrets_and_production_are_refused(self):
        p = Project(self)
        base = {"v": 1, "start": "python3 serve.py"}
        cases = [
            dict(base, host="0.0.0.0"),
            dict(base, network="public"),
            dict(base, start="python3 serve.py --host 0.0.0.0"),
            dict(base, start="vite --host"),
            dict(base, start="ngrok http 3000"),
            dict(base, start="npm run dev && open http://x"),
            dict(base, start="./serve.sh"),
            dict(base, start="chromium --no-sandbox"),
            dict(base, env={"API_KEY": "abc123"}),
            dict(base, env={"NODE_ENV": "production"}),
            dict(base, env={"X": {"secret": "GH_TOKEN"}}),
            dict(base, prepare=[{"name": "db", "run": "python3 manage.py migrate"}]),
            dict(base, fixture="nope"),
            dict(base, port=80),
            dict(base, surprise=True),
        ]
        want = ["exposure", "exposure", "exposure", "exposure", "tunnel",
                "compound", "relative-executable", "unsafe-flag",
                "literal-secret", "production-default", "credential-reference",
                "destructive-undeclared", "fixture-undeclared", "port-invalid",
                "unknown-key"]
        for found, code, doc in zip(validate(p, cases), want, cases):
            self.assertFalse(found["valid"], doc)
            self.assertIn(code, codes(found), doc)
            for problem in found["problems"]:
                self.assertTrue(problem["remedy"], problem)

    def test_risky_but_legitimate_choices_are_called_out_not_refused(self):
        p = Project(self)
        found, = validate(p, [{
            "v": 1, "start": "python3 serve.py --host {host} --port {port}",
            "prepare": [{"name": "deps", "run": "npm ci", "network": True},
                        {"name": "db", "run": "python3 manage.py migrate",
                         "data": "disposable"}],
            "env": {"API_BASE": "https://api.example.com/v1",
                    "DATABASE_URL": "postgres://127.0.0.1/preview"}}])
        self.assertTrue(found["valid"], found["problems"])
        self.assertEqual({"network-step", "destructive-step", "external-service",
                          "readiness-shallow"}, wcodes(found))


class DiscoveryIsInert(unittest.TestCase):
    def test_a_start_script_is_proposed_and_nothing_is_run_or_written(self):
        p = Project(self)
        (p.app / "package.json").write_text(json.dumps(
            {"scripts": {"dev": "vite --port 5173"}, "dependencies": {"react": "1"}}))
        (p.app / "package-lock.json").write_text("{}")
        (p.app / ".env.example").write_text("API_TOKEN=\nAPP_NAME=x\n")
        before = snapshot(p.tool, p.app, p.work)
        found = p.run("import previews\nR = previews.state()")
        self.assertEqual(before, snapshot(p.tool, p.app, p.work))
        self.assertEqual("not-configured", found["lifecycle"]["status"])
        d = found["discovery"]
        self.assertIn("not a working preview", d["inert"])
        self.assertEqual("web", d["kind"])
        self.assertEqual(5173, d["start"][0]["port"])
        self.assertEqual("npm ci", d["prepare"][0]["run"])
        self.assertTrue(d["prepare"][0]["network"])
        self.assertEqual({"API_TOKEN": True, "APP_NAME": False},
                         {e["name"]: e["secret"] for e in d["env"]})
        self.assertEqual({"secret": "API_TOKEN"},
                         found["proposal"]["env"]["API_TOKEN"])
        area = by_id(p.read())["preview"]
        self.assertEqual("decision-needed", area["status"])
        self.assertIn("not a working preview", area["recommendation"])

    def test_a_library_is_offered_not_applicable_and_a_smoke_check(self):
        p = Project(self)
        (p.app / "pyproject.toml").write_text("[project]\nname='lib'\n")
        found = p.run("import previews\nR = previews.state()")
        self.assertEqual("non-web", found["discovery"]["kind"])
        self.assertIn("smoke check", found["discovery"]["alternative"])
        self.assertIsNone(found["proposal"])
        area = by_id(p.read())["preview"]
        self.assertIn("not-applicable", area["allowed"])
        self.assertIn("smoke check", area["recommendation"])


class NotApplicableLinksAnAlternative(unittest.TestCase):
    POLICY = {"v": 1, "checks": [{"name": "cli-smoke",
                                  "run": "python3 -m lib --help",
                                  "root": "worktree"}]}

    def test_a_registered_check_or_a_card_is_linked_and_nothing_else(self):
        p = Project(self, policy=self.POLICY)
        rev = p.read()["revision"]
        refused = p.call("decide", {
            "area": "preview", "decision": "not-applicable",
            "reason": "a library, nothing to serve", "expected": rev,
            "request": "na-000001", "alternative": {"check": "invented"}})
        self.assertEqual(409, refused["status"])
        self.assertFalse((p.work / "setup").exists())
        done = p.call("decide", {
            "area": "preview", "decision": "not-applicable",
            "reason": "a library, nothing to serve", "expected": rev,
            "request": "na-000002", "alternative": {"check": "cli-smoke"}})
        self.assertTrue(done["ok"]["ok"], done)
        area = by_id(p.read())["preview"]
        self.assertEqual("not-applicable", area["status"])
        self.assertEqual({"check": "cli-smoke"}, area["decision"]["alternative"])
        self.assertEqual({"check": "cli-smoke"},
                         p.decisions()["decisions"]["preview"]["alternative"])
        again = p.call("decide", {
            "area": "preview", "decision": "defer", "reason": "later on",
            "expected": p.read()["revision"], "request": "na-000003",
            "alternative": {"task": "07"}})
        self.assertIn("only when the application preview is marked not "
                      "applicable", again["refused"])

    def test_a_card_can_be_the_alternative(self):
        p = Project(self)
        done = p.call("decide", {
            "area": "preview", "decision": "not-applicable",
            "reason": "infrastructure only", "expected": p.read()["revision"],
            "request": "na-000004", "alternative": {"task": "#7"}})
        self.assertEqual("07", done["ok"] and
                         p.decisions()["decisions"]["preview"]["alternative"]["task"])


class Board:
    """A real board serving the disposable project, driven over HTTP."""

    def __init__(self, case: unittest.TestCase, p: Project):
        self.case, self.p = case, p
        self.port = free_port()
        self.proc = None
        self.start()
        case.addCleanup(self.stop)

    def start(self):
        env = dict(self.p.env, BOARD_PORT=str(self.port), PREVIEW_TOKEN="")
        env.pop("PREVIEW_TOKEN")
        self.log = open(self.p.dir / "board.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.p.core / "board.py"), "--port",
             str(self.port), "--no-open"], cwd=str(self.p.tool), env=env,
            stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError((self.p.dir / "board.log").read_text())
            try:
                if self.get("/api/state")[0] == 200:
                    return
            except AssertionError:
                time.sleep(0.15)
        raise AssertionError("the board never answered")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()

    def _open(self, req):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")
        except OSError as exc:
            self.log.flush()
            raise AssertionError(f"{exc}; board log:\n" + (
                self.p.dir / "board.log").read_text(errors="replace")[-3000:])

    def get(self, path):
        return self._open(urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}"))

    def post(self, path, payload, headers=HEADERS):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(payload).encode(),
                                     method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        return self._open(req)

    def preview(self) -> dict:
        status, body = self.get("/api/preview?file=12-web.md")
        self.case.assertEqual(200, status, body)
        return body["preview"]

    def wait(self, words, timeout=20) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.preview()
            if found["lifecycle"]["status"] in words:
                return found
            time.sleep(0.2)
        raise AssertionError(f"never reached {words}: {found['lifecycle']}")

    def settled(self, timeout=20) -> dict:
        """The preview once its ending is recorded: cleanup says what it
        left behind."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.preview()
            if (found["lifecycle"]["preview"] or {}).get("cleanup"):
                return found
            time.sleep(0.2)
        raise AssertionError(f"the ending was never recorded: {found['lifecycle']}")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # a zombie is not alive
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split(")")[1][:3]
    except OSError:
        return True


def web_project(case) -> Project:
    p = Project(case)
    (p.app / "serve.py").write_text(SERVE)
    (p.app / "seed.py").write_text(SEED)
    (p.app / ".gitignore").write_text("data/\npid\nchild-pid\n")
    git(p.app, "add", "-A")
    git(p.app, "-c", "user.email=t@example.com", "-c", "user.name=tester",
        "commit", "-q", "-m", "a disposable web app")
    git(p.app, "branch", "task/12-web")
    (p.work / "tasks" / "review" / "12-web.md").write_text(
        "# 12 — Web\n\n**Status:** Review\n**Priority:** Low\n")
    return p


def write_secret(p: Project, mode=0o600):
    path = p.local / "preview.secrets"
    path.write_text(f"PREVIEW_TOKEN={SECRET}\n")
    path.chmod(mode)


class ALifecycleEndToEnd(unittest.TestCase):
    def test_configure_prepare_start_reach_and_stop_a_disposable_web_app(self):
        p = web_project(self)
        app_port = free_port()
        b = Board(self, p)
        first = b.preview()
        self.assertEqual("not-configured", first["lifecycle"]["status"])
        self.assertEqual(["12-web.md"], [c["file"] for c in first["cards"]])
        draft = config_doc(app_port)

        # configure: validate writes nothing; save is armed and acknowledged
        status, checked = b.post("/api/preview/validate", {"draft": draft})
        self.assertEqual(200, status)
        self.assertEqual(["destructive-step"], checked["result"]["acknowledge"])
        self.assertFalse((p.local / "preview.json").exists())
        status, _ = b.post("/api/preview/configure",
                           {"draft": draft, "approve": True, "expected": "none"},
                           headers={"Content-Type": "application/json"})
        self.assertEqual(403, status)
        status, refused = b.post("/api/preview/configure",
                                 {"draft": draft, "approve": True,
                                  "expected": "none"})
        self.assertEqual(409, status)
        self.assertEqual(["destructive-step"], refused["acknowledge"])
        status, saved = b.post("/api/preview/configure", {
            "draft": draft, "approve": True, "expected": "none",
            "acknowledged": ["destructive-step"]})
        self.assertEqual(200, status, saved)
        self.assertEqual("nothing", saved["result"]["ran"])
        rev = saved["result"]["revision"]
        self.assertEqual("configured", b.preview()["lifecycle"]["status"])
        self.assertFalse((p.dir / "worktrees" / "12-web").exists(),
                         "configuring made no worktree and ran nothing")

        card = {"file": "12-web.md", "expected": rev}
        # prepare and start are explicit
        status, body = b.post("/api/preview/start", card)
        self.assertEqual(400, status)
        self.assertIn("approve", body["error"])
        status, body = b.post("/api/preview/start", dict(card, approve=True))
        self.assertEqual("unprepared", body.get("code"), body)
        # a missing secret refuses with nothing run
        status, body = b.post("/api/preview/prepare", dict(card, approve=True))
        self.assertEqual(("missing-secret", ["PREVIEW_TOKEN"]),
                         (body.get("code"), body.get("missing")), body)
        self.assertNotIn(SECRET, json.dumps(body))
        write_secret(p, 0o644)
        status, body = b.post("/api/preview/prepare", dict(card, approve=True))
        self.assertEqual("secrets-file-open", body.get("code"), body)
        self.assertFalse((p.dir / "worktrees" / "12-web" / "data").exists())
        write_secret(p)
        status, body = b.post("/api/preview/prepare", dict(card, approve=True))
        self.assertEqual(200, status, body)
        prepared = b.wait({"prepared"})
        steps = prepared["lifecycle"]["prepare"]["steps"]
        self.assertEqual([("fixture", "passed", "disposable")],
                         [(s["name"], s["outcome"], s["data"]) for s in steps])

        # a port held by an unrelated service: refused, the service untouched
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", app_port))
        squatter.listen(1)
        try:
            status, body = b.post("/api/preview/start", dict(card, approve=True))
            self.assertEqual("port-in-use", body.get("code"), body)
            self.assertIn("Nothing was stopped", body["error"])
            squatter.getsockname()           # still ours, still open
        finally:
            squatter.close()

        status, body = b.post("/api/preview/start", dict(card, approve=True))
        self.assertEqual(200, status, body)
        ready = b.wait({"ready"})
        life = ready["lifecycle"]
        head = subprocess.run(["git", "-C", str(p.app), "rev-parse",
                               "task/12-web"], capture_output=True,
                              text=True).stdout.strip()
        self.assertEqual(head, life["preview"]["source"]["commit"])
        self.assertEqual("task/12-web", life["preview"]["branch"])
        self.assertEqual(f"http://127.0.0.1:{app_port}", life["preview"]["url"])
        self.assertEqual("current", life["currency"]["state"])
        self.assertEqual(app_port, life["ownership"]["port"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        page = opener.open(life["preview"]["url"] + "/", timeout=5).read().decode()
        self.assertIn("rows=3", page)          # the disposable fixture
        self.assertIn("mode=", page)

        # logs carry no secret value, and neither does any shared state
        status, logged = b.get("/api/preview/log")
        self.assertIn("listening", logged["log"])
        self.assertNotIn(SECRET, logged["log"])
        self.assertIn("«secret»", logged["log"])
        for blob in (json.dumps(b.preview()), json.dumps(b.get("/api/setup")[1]),
                     (p.local / "preview.json").read_text()):
            self.assertNotIn(SECRET, blob)

        status, probed = b.post("/api/preview/check", {})
        self.assertTrue(probed["result"]["probe"]["ok"], probed)

        # a later commit does not relabel the running process as current
        tree = p.dir / "worktrees" / "12-web"
        (tree / "later.txt").write_text("later\n")
        git(tree, "add", "later.txt")
        git(tree, "-c", "user.email=t@example.com", "-c", "user.name=tester",
            "commit", "-q", "-m", "later")
        stale = b.preview()["lifecycle"]
        self.assertEqual("ready", stale["status"])
        self.assertEqual("stale", stale["currency"]["state"])
        self.assertIn("started from", stale["currency"]["reasons"][0])

        pid = int((tree / "pid").read_text())
        child = int((tree / "child-pid").read_text())
        status, _ = b.post("/api/preview/stop", {})
        self.assertEqual(200, status)
        done = b.wait({"stopped"})["lifecycle"]["preview"]
        self.assertEqual({"group": "gone", "port": "released"}, done["cleanup"])
        self.assertFalse(alive(pid))
        self.assertFalse(alive(child), "the preview's own child was left behind")
        self.assertTrue(drive_port_free(app_port))
        raw = (p.state / "drives").glob("12-web-*.log")
        for path in raw:
            self.assertNotIn(SECRET, path.read_text())
        area = by_id(b.get("/api/setup")[1]["setup"])["preview"]
        self.assertEqual("tested", area["status"])


def drive_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def configured(case, p: Project, b: Board, **over) -> dict:
    doc = config_doc("auto", **over)
    status, body = b.post("/api/preview/configure", {
        "draft": doc, "approve": True,
        "expected": b.preview()["configuration"]["revision"],
        "acknowledged": ["destructive-step", "readiness-shallow"]})
    case.assertEqual(200, status, body)
    return {"file": "12-web.md", "approve": True,
            "expected": body["result"]["revision"]}


def prepared(case, b: Board, card: dict) -> None:
    status, body = b.post("/api/preview/prepare", card)
    case.assertEqual(200, status, body)
    b.wait({"prepared"})


class HonestEndings(unittest.TestCase):
    def test_readiness_timeout_stops_only_the_group_it_started(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b, ready={"path": "/", "contains": "never",
                                             "timeout": 2})
        prepared(self, b, card)
        status, body = b.post("/api/preview/start", card)
        self.assertEqual(200, status, body)
        life = b.wait({"unready"})["lifecycle"]
        self.assertIn("readiness timed out", life["preview"]["reason"])
        self.assertIn("without the expected text", life["preview"]["reason"])
        pid = int((p.dir / "worktrees" / "12-web" / "pid").read_text())
        ended = b.settled()["lifecycle"]
        self.assertEqual("unready", ended["status"])
        self.assertEqual("gone", ended["preview"]["cleanup"]["group"])
        self.assertFalse(alive(pid))
        self.assertEqual("failed", by_id(b.get("/api/setup")[1]["setup"])
                         ["preview"]["status"])

    def test_an_exit_before_ready_and_after_ready_read_as_themselves(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b, start="python3 -c raise(SystemExit(3))",
                          prepare=[], fixture=None)
        status, body = b.post("/api/preview/start", card)
        self.assertEqual(200, status, body)
        failed = b.wait({"failed"})["lifecycle"]["preview"]
        self.assertIn("exited before it was ready (rc=3)", failed["reason"])

        card = configured(self, p, b)
        prepared(self, b, card)
        b.post("/api/preview/start", card)
        url = b.wait({"ready"})["lifecycle"]["preview"]["url"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(url + "/exit", timeout=5).read()
        except OSError:
            pass
        exited = b.wait({"exited"})["lifecycle"]["preview"]
        self.assertIn("exited unexpectedly", exited["reason"])
        self.assertEqual("gone", exited["cleanup"]["group"])

    def test_an_unavailable_dependency_refuses_with_nothing_run(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b, start="definitely-not-installed-91 serve",
                          prepare=[], fixture=None)
        status, body = b.post("/api/preview/start", card)
        self.assertEqual("unavailable-dependency", body.get("code"), body)
        self.assertEqual("configured", b.preview()["lifecycle"]["status"])

    def test_stop_escalates_past_an_app_that_ignores_sigterm(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b, env={"IGNORE_TERM": "1",
                                           "API_TOKEN": {"secret": "PREVIEW_TOKEN"}})
        prepared(self, b, card)
        b.post("/api/preview/start", card)
        b.wait({"ready"})
        pid = int((p.dir / "worktrees" / "12-web" / "pid").read_text())
        b.post("/api/preview/stop", {})
        done = b.wait({"stopped"})["lifecycle"]["preview"]
        self.assertEqual("gone", done["cleanup"]["group"])
        self.assertFalse(alive(pid))

    def test_a_stale_configuration_and_a_stale_preparation_refuse_start(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b)
        prepared(self, b, card)
        status, body = b.post("/api/preview/start", dict(card, expected="0" * 16))
        self.assertEqual(409, status)
        self.assertTrue(body["stale"])
        tree = p.dir / "worktrees" / "12-web"
        (tree / "edit.txt").write_text("edit\n")
        status, body = b.post("/api/preview/start", card)
        self.assertEqual("unprepared", body.get("code"), body)
        self.assertIn("uncommitted work changed", body["error"])


class Reconnect(unittest.TestCase):
    def test_a_restarted_board_reconnects_and_asks_readiness_again(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b)
        prepared(self, b, card)
        b.post("/api/preview/start", card)
        b.wait({"ready"})
        pid = int((p.dir / "worktrees" / "12-web" / "pid").read_text())
        self.addCleanup(lambda: _kill(pid))
        b.stop()
        self.assertTrue(alive(pid), "the preview outlives the board")
        b.start()
        life = b.wait({"ready"})["lifecycle"]
        self.assertIn(life["preview"]["readiness"], ("ready",))
        self.assertTrue(life["preview"]["checkedAt"])
        b.post("/api/preview/stop", {})
        b.wait({"stopped"})
        self.assertFalse(alive(pid))

    def test_a_group_it_cannot_show_is_its_own_is_left_alone(self):
        p = web_project(self)
        write_secret(p)
        b = Board(self, p)
        card = configured(self, p, b)
        prepared(self, b, card)
        b.post("/api/preview/start", card)
        b.wait({"ready"})
        pid = int((p.dir / "worktrees" / "12-web" / "pid").read_text())
        self.addCleanup(lambda: _kill(pid))
        b.stop()
        record = p.state / "drive.json"
        data = json.loads(record.read_text())
        data["owner"] = "not-the-token"
        record.write_text(json.dumps(data))
        b.start()
        life = b.preview()["lifecycle"]
        self.assertIsNone(life["preview"])
        self.assertTrue(alive(pid), "an unproven group is never signalled")
        status, body = b.post("/api/preview/stop", {})
        self.assertEqual(409, status)
        self.assertTrue(alive(pid))


PAGE = Path(__file__).resolve().parents[1] / "manager" / "core" / "board.html"


class ThePageKeepsTheActsApart(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_five_separate_controls_and_the_two_that_run_code_are_armed(self):
        for act in ("validate", "configure", "prepare", "cancel", "start",
                    "check", "stop"):
            self.assertIn(f'data-pvact="{act}"', self.html)
        armed = self.html[self.html.index("function wirePreview"):]
        self.assertIn("['configure', 'prepare', 'start'].includes(act) && "
                      "!btn.classList.contains('armed')", armed)
        self.assertIn("approve: true", armed)
        self.assertIn('rel="noopener noreferrer"', self.html)
        self.assertIn("this process is not the selected source", self.html)

    @unittest.skipUnless(__import__("shutil").which("node"), "node is not installed")
    def test_the_page_script_parses(self):
        import re
        import shutil
        import tempfile
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", self.html, re.S)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.js"
            path.write_text("\n;\n".join(scripts), encoding="utf-8")
            out = subprocess.run([shutil.which("node"), "--check", str(path)],
                                 capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr[-2000:])


def _kill(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


if __name__ == "__main__":
    unittest.main()
