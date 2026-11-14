"""Who is calling: a claimed run proved by its token, the Host a request
names, and reference files that cannot script this board (task 113, WP05).

Each check has two modes, and each mode is held here on its own:
`report` (the default) counts and shows what would be refused and refuses
nothing; `enforce` refuses. The real-board cases run the candidate's own
core on a loopback port with the inherited `BOARD_*`, `AGENT_*` and
`BENCH_*` variables removed (the `test_settings_security` fixture); the
run they speak for is a progress record left on disk before the board
starts, holding only the hash of its capability, as a launch leaves it.

    python3 -m unittest tests.test_caller_identity -v
"""

from __future__ import annotations

import hashlib
import http.client
import itertools
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "manager" / "core"
sys.path.insert(0, str(CORE))

import callers  # noqa: E402
import config  # noqa: E402
import progress  # noqa: E402

from tests.test_settings_security import Board  # noqa: E402

RUN = "run-7"
TOKEN = "the-runs-own-capability-0123456789"
SESSION = "sess-wp05"


def seed_run(board: Board) -> None:
    """A run this board launched: its progress record with the hash of its
    capability, exactly what `progress.issue_capability` keeps."""
    folder = board.tool / "manager" / "local" / "state" / "agent" / "progress"
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "v": progress.VERSION, "agent": RUN, "task": "01-a-card.md",
        "intent": "work", "tokenHash": None,
        "capabilityHash": hashlib.sha256(TOKEN.encode()).hexdigest(),
        "channel": {"state": "unavailable", "why": "fixture"},
        "opened": 1.0, "closed": None, "outcome": None, "current": None,
        "history": [], "seen": [], "milestones": [], "milestonesOmitted": [],
        "accepted": 0, "coalesced": 0, "duplicates": 0, "rejected": 0,
        "lastRejection": None, "lastReport": None, "lastReceived": None,
        "lastReminded": None, "seq": 0}
    (folder / f"{RUN}.json").write_text(json.dumps(record), encoding="utf-8")


def raw(board: Board, method: str, path: str, *, host: str | None = None,
        body: dict | None = None, headers: dict | None = None):
    """One request with the Host exactly as given (http.client would
    otherwise state the one it connected to)."""
    conn = http.client.HTTPConnection("127.0.0.1", board.port, timeout=20)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host)
        data = json.dumps(body).encode() if body is not None else b""
        sent = {"Content-Type": "application/json", "X-Bench-Settings": "1",
                **(headers or {})}
        if method == "POST":
            sent["Content-Length"] = str(len(data))
        for key, value in sent.items():
            conn.putheader(key, value)
        conn.endheaders()
        if data:
            conn.send(data)
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, dict(resp.getheaders()), payload
    finally:
        conn.close()


_IDS = itertools.count(1)


def event(**extra) -> dict:
    """A normalized event with a fresh id, so no copy is a replay."""
    return {"v": 1, "session": SESSION, "kind": "read", "summary": "read: a",
            "id": f"PostToolUse:tu_{next(_IDS)}", **extra}


# ── the parts, in-process ──────────────────────────────────────────────


class TheHostIsReadExactly(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(callers.parse_host("127.0.0.1:26071"), ("127.0.0.1", 26071))
        self.assertEqual(callers.parse_host("LocalHost:26071"), ("localhost", 26071))
        self.assertEqual(callers.parse_host("[::1]:26071"), ("::1", 26071))
        self.assertEqual(callers.parse_host("[::1]"), ("::1", None))
        self.assertEqual(callers.parse_host("bench.example.ts.net"),
                         ("bench.example.ts.net", None))
        # exact: a trailing dot is a different name, kept as written
        self.assertEqual(callers.parse_host("localhost.:26071"), ("localhost.", 26071))
        for bad in ("", "   ", "::1:26071", "[::1", "[::1]26071", "[nothost]:1",
                    "host:0", "host:65536", "host:-1", "host:port", "a b:1",
                    "user@host:1", "host/path", None):
            self.assertIsNone(callers.parse_host(bad), bad)

    def test_loopback_on_the_served_port_and_the_allowed_origins(self):
        allowed = config.parse_origins(
            "https://bench.example.ts.net, http://127.0.0.1:9000")[0]
        ok = callers.allowed_hosts(26071, allowed)
        for host in ("127.0.0.1:26071", "localhost:26071", "[::1]:26071",
                     "bench.example.ts.net", "bench.example.ts.net:443",
                     "127.0.0.1:9000"):
            self.assertIn(callers.parse_host(host), ok, host)
        for host in ("127.0.0.1:26072", "localhost", "127.0.0.1",
                     "bench.example.ts.net:80", "bench.example.ts.net:26071",
                     "localhost.:26071", "evil.example", "[::2]:26071",
                     "0.0.0.0:26071"):
            self.assertIsNotNone(callers.host_problem(host, 26071, allowed), host)

    def test_a_missing_host_is_a_problem_not_a_pass(self):
        self.assertIn("no Host", callers.host_problem(None, 26071, frozenset()))
        self.assertIn("no Host", callers.host_problem("", 26071, frozenset()))

    def test_what_is_shown_is_sanitized_and_bounded(self):
        self.assertEqual(callers.sanitize("a\x00b‮c\x7f"), "a?b?c?")
        long = callers.sanitize("h" * 500)
        self.assertEqual(len(long), callers.VALUE_CAP + 1)
        self.assertTrue(long.endswith("…"))


class TheModeIsExplicit(unittest.TestCase):
    def test_only_the_two_words_are_taken(self):
        self.assertEqual(config.parse_mode("K", ""), ("report", None))
        self.assertEqual(config.parse_mode("K", "report"), ("report", None))
        self.assertEqual(config.parse_mode("K", " Enforce "), ("enforce", None))
        for typo in ("enforced", "on", "1", "true", "strict"):
            mode, problem = config.parse_mode("K", typo)
            self.assertEqual("report", mode, typo)
            self.assertIn("refuses nothing", problem)

    def test_the_shipped_default_is_report(self):
        """No update or merge of this source advances a board to
        enforcement: the documented default, and the running default, are
        both `report`."""
        example = (CORE / ".env.example").read_text(encoding="utf-8")
        self.assertIn("\nBOARD_RUN_IDENTITY=report\n", example)
        self.assertIn("\nBOARD_HOST_CHECK=report\n", example)
        src = (CORE / "config.py").read_text(encoding="utf-8")
        self.assertIn('setting("BOARD_RUN_IDENTITY", "report")', src)
        self.assertIn('setting("BOARD_HOST_CHECK", "report")', src)


class ClaimCase(unittest.TestCase):
    def setUp(self):
        callers.reset()
        self.addCleanup(callers.reset)
        progress.RUNS.clear()
        self.addCleanup(progress.RUNS.clear)
        progress.RUNS[RUN] = {
            "agent": RUN, "capabilityHash":
                hashlib.sha256(TOKEN.encode()).hexdigest()}
        self.narrated = []
        for target, value in (
                ("callers.state.record_board_event", self.narrated.append),
                ("progress._store", lambda record: None),
                ("progress._notify_health", lambda agent: None)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def mode(self, value: str):
        patcher = mock.patch.object(config, "RUN_IDENTITY_MODE", value)
        patcher.start()
        self.addCleanup(patcher.stop)


class AClaimedRunIsProvedInEitherMode(ClaimCase):
    def test_the_right_token_proves_it(self):
        for mode in ("report", "enforce"):
            self.mode(mode)
            body = {"check": "unit", "agent": RUN, "token": TOKEN}
            self.assertEqual(callers.claim("/api/verify", body), body)
            got = callers.claim("/api/events", event(agent=RUN, task="t", token=TOKEN))
            self.assertEqual(got["agent"], RUN)
            self.assertNotIn("token", got)
        self.assertEqual(callers.observations()["counts"]["claimsProved"], 4)
        self.assertEqual(self.narrated, [])
        self.assertNotIn("unproven", progress.RUNS[RUN])

    def test_a_person_names_no_run_and_passes_untouched(self):
        self.mode("enforce")
        body = {"check": "unit", "file": "01-a-card.md"}
        self.assertEqual(callers.claim("/api/verify", body), body)
        self.assertEqual(callers.claim("/api/move", {"agent": RUN}), {"agent": RUN})

    def test_progress_proves_in_constant_time_by_the_issued_hash(self):
        self.assertIsNone(progress.proves(RUN, TOKEN))
        self.assertEqual(progress.proves(RUN, TOKEN + "x"), "identity")
        self.assertEqual(progress.proves(RUN, ""), "no-token")
        self.assertEqual(progress.proves(RUN, None), "no-token")
        self.assertEqual(progress.proves("another-run", TOKEN), "unknown-run")
        progress.RUNS["old-run"] = {"agent": "old-run"}
        self.assertEqual(progress.proves("old-run", TOKEN), "no-capability")
        with mock.patch.object(progress.hmac, "compare_digest",
                               wraps=progress.hmac.compare_digest) as cmp:
            progress.proves(RUN, "wrong")
        cmp.assert_called_once()


class ReportOnlyCountsAndRefusesNothing(ClaimCase):
    def setUp(self):
        super().setUp()
        self.mode("report")

    def test_an_unproven_verification_goes_on_and_is_counted(self):
        for token in (None, "", "another-runs-token"):
            body = {"check": "unit", "agent": RUN}
            if token is not None:
                body["token"] = token
            self.assertEqual(callers.claim("/api/verify", body), body)
        self.assertEqual(progress.RUNS[RUN]["unproven"], {"verify": 3})
        self.assertEqual(progress.RUNS[RUN]["lastUnproven"]["code"], "identity")
        counts = callers.observations()["counts"]
        self.assertEqual((counts["claimsUnproven"], counts["claimsRefused"]), (3, 0))
        # narrated once, not three times, and it says nothing was refused
        self.assertEqual(len(self.narrated), 1)
        self.assertIn("report", self.narrated[0]["summary"])
        self.assertIn("nothing was refused", self.narrated[0]["summary"])

    def test_an_unproven_event_keeps_its_link_and_is_counted(self):
        got = callers.claim("/api/events", event(agent=RUN, task="01-a-card.md"))
        self.assertEqual((got["agent"], got["task"]), (RUN, "01-a-card.md"))
        self.assertEqual(progress.RUNS[RUN]["unproven"], {"events": 1})
        self.assertEqual(callers.observations()["counts"]["eventsUnlinked"], 0)

    def test_a_run_the_board_never_launched_is_counted_without_a_channel(self):
        body = {"check": "unit", "agent": "nobody"}
        self.assertEqual(callers.claim("/api/verify", body), body)
        self.assertEqual(callers.observations()["counts"]["claimsUnproven"], 1)
        self.assertNotIn("nobody", progress.RUNS)

    def test_report_mode_is_shown_as_report_not_a_pass(self):
        seen = callers.observations()
        self.assertEqual(seen["runIdentity"]["mode"], "report")


class EnforcementRefusesAndUnlinks(ClaimCase):
    def setUp(self):
        super().setUp()
        self.mode("enforce")

    def test_an_unproven_verification_is_refused(self):
        for body in ({"check": "unit", "agent": RUN},
                     {"check": "unit", "agent": RUN, "token": "guess"},
                     {"check": "unit", "agent": "nobody", "token": TOKEN}):
            with self.assertRaises(callers.ClaimRefused) as caught:
                callers.claim("/api/verify", body)
            self.assertEqual(caught.exception.code, "run-unproven")
            self.assertIn("Nothing was run", caught.exception.why)
            self.assertNotIn(TOKEN, caught.exception.why)
        self.assertEqual(callers.observations()["counts"]["claimsRefused"], 3)

    def test_another_runs_token_cannot_borrow_this_runs_manifest(self):
        progress.RUNS["run-8"] = {"agent": "run-8", "capabilityHash":
                                  hashlib.sha256(b"run-8-token").hexdigest()}
        with self.assertRaises(callers.ClaimRefused):
            callers.claim("/api/verify", {"check": "unit", "agent": RUN,
                                          "token": "run-8-token"})

    def test_an_unproven_event_is_recorded_with_no_run_link(self):
        got = callers.claim("/api/events",
                            event(agent=RUN, task="01-a-card.md", token="guess"))
        for key in ("agent", "task", "token"):
            self.assertNotIn(key, got)
        self.assertEqual(got["session"], SESSION)
        self.assertEqual(got["kind"], "read")
        self.assertEqual(callers.observations()["counts"]["eventsUnlinked"], 1)
        self.assertIn("no run link", self.narrated[0]["summary"])


# ── the boundary, on a real board ──────────────────────────────────────


class _Cleanup:
    def __init__(self, cls):
        self.addCleanup = cls.addClassCleanup


class BoardCase(unittest.TestCase):
    ENV: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.board = Board(_Cleanup(cls), env=cls.ENV, prepare=seed_run)
        cls.host = f"127.0.0.1:{cls.board.port}"

    def post_event(self, **extra):
        code, _, data = raw(self.board, "POST", "/api/events", host=self.host,
                            body=event(**extra))
        return code, json.loads(data or b"{}")

    def session(self) -> dict:
        return self.board.get(f"/api/session?id={SESSION}")[1]["meta"]

    def callers_seen(self) -> dict:
        return self.board.get("/api/settings")[1]["settings"]["installation"]["callers"]

    def run_progress(self) -> dict:
        return self.board.get(f"/api/progress?agent={RUN}")[1]["progress"]

    def assertTokenNowhere(self):
        local = self.board.tool / "manager" / "local" / "state"
        for path in list(local.rglob("*")) + list(self.board.work.rglob("*")):
            if path.is_file() and path.suffix in (".jsonl", ".json", ".md"):
                self.assertNotIn(TOKEN, path.read_text(errors="replace"), path)
        self.assertNotIn(TOKEN, (self.board.dir / "board.log").read_text(errors="replace"))
        self.assertNotIn(TOKEN, json.dumps(self.board.get("/api/settings")[1]))
        self.assertNotIn(TOKEN, json.dumps(self.board.get("/api/state")[1]))


class ARealBoardInReportMode(BoardCase):
    ENV = {}

    def test_1_an_unproven_event_still_links_and_is_counted(self):
        code, _ = self.post_event(agent=RUN, task="01-a-card.md")
        self.assertEqual(200, code)
        self.assertEqual(self.session()["agentId"], RUN)
        self.assertEqual(self.run_progress()["unproven"], {"events": 1})
        seen = self.callers_seen()
        self.assertEqual(seen["runIdentity"]["mode"], "report")
        self.assertEqual(seen["counts"]["claimsUnproven"], 1)
        self.assertEqual(seen["counts"]["eventsUnlinked"], 0)

    def test_2_an_unproven_verification_reaches_the_runner(self):
        """Reported, the request goes on: what answers is verify's own
        reply about the check, not the boundary's refusal."""
        code, _, data = raw(self.board, "POST", "/api/verify", host=self.host,
                            body={"check": "unit", "file": "01-a-card.md",
                                  "agent": RUN})
        self.assertNotEqual("run-unproven", json.loads(data or b"{}").get("code"), data)
        self.assertEqual(self.run_progress()["unproven"].get("verify"), 1)

    def test_3_an_unlisted_host_is_answered_and_observed(self):
        code, _, _ = raw(self.board, "GET", "/api/state", host="evil.example:80")
        self.assertEqual(200, code)
        code, _, _ = raw(self.board, "GET", "/api/state",
                         host=f"[::1]:{self.board.port}")
        self.assertEqual(200, code)
        seen = self.callers_seen()
        self.assertEqual(seen["hostCheck"]["mode"], "report")
        hosts = {h["value"]: h for h in seen["hosts"]}
        self.assertFalse(hosts["evil.example:80"]["allowed"])
        self.assertIn("BOARD_ALLOWED_ORIGINS", hosts["evil.example:80"]["why"])
        self.assertTrue(hosts[f"[::1]:{self.board.port}"]["allowed"])
        self.assertGreaterEqual(seen["counts"]["hostUnlisted"], 1)
        self.assertEqual(seen["counts"]["hostRefused"], 0)

    def test_4_proxy_headers_are_noted_by_name_never_by_value(self):
        raw(self.board, "GET", "/api/state", host=self.host,
            headers={"X-Forwarded-Host": "secret-forwarded-value.example",
                     "Origin": "http://evil.example"})
        seen = self.callers_seen()
        self.assertGreaterEqual(seen["forwardedHeaders"].get("X-Forwarded-Host", 0), 1)
        self.assertNotIn("secret-forwarded-value", json.dumps(seen))
        origins = {o["value"]: o for o in seen["origins"]}
        self.assertFalse(origins["http://evil.example"]["allowed"])

    def test_5_the_token_is_nowhere(self):
        self.post_event(agent=RUN, task="01-a-card.md", token=TOKEN, id="tok:1")
        self.assertTokenNowhere()


class ARealBoardEnforcingRunIdentity(BoardCase):
    ENV = {"BOARD_RUN_IDENTITY": "enforce"}

    def test_1_an_unproven_verification_is_refused_and_nothing_runs(self):
        evidence = self.board.tool / "manager" / "local" / "state" / "evidence"
        before = sorted(evidence.rglob("*")) if evidence.exists() else []
        for extra in ({}, {"token": "guess"}):
            code, _, data = raw(self.board, "POST", "/api/verify", host=self.host,
                                body={"check": "unit", "file": "01-a-card.md",
                                      "agent": RUN, **extra})
            reply = json.loads(data)
            self.assertEqual(403, code, reply)
            self.assertEqual("run-unproven", reply["code"])
        after = sorted(evidence.rglob("*")) if evidence.exists() else []
        self.assertEqual(before, after)
        self.assertEqual(self.callers_seen()["counts"]["claimsRefused"], 2)

    def test_2_an_event_without_a_token_never_links_the_session(self):
        code, _ = self.post_event(agent=RUN, task="01-a-card.md")
        self.assertEqual(200, code)
        meta = self.session()
        self.assertIsNone(meta["agentId"])
        self.assertIsNone(meta["task"])
        self.assertEqual(self.callers_seen()["counts"]["eventsUnlinked"], 1)

    def test_3_an_event_with_the_runs_token_links_it(self):
        code, _ = self.post_event(agent=RUN, task="01-a-card.md", token=TOKEN,
                                  id="tok:2")
        self.assertEqual(200, code)
        self.assertEqual(self.session()["agentId"], RUN)
        self.assertTokenNowhere()

    def test_4_a_proved_verification_passes_the_boundary(self):
        code, _, data = raw(self.board, "POST", "/api/verify", host=self.host,
                            body={"check": "unit", "file": "01-a-card.md",
                                  "agent": RUN, "token": TOKEN})
        self.assertNotEqual("run-unproven", json.loads(data or b"{}").get("code"), data)


class ARealBoardEnforcingTheHost(BoardCase):
    ENV = {"BOARD_HOST_CHECK": "enforce"}

    def test_loopback_on_the_served_port_is_answered(self):
        for host in (self.host, f"localhost:{self.board.port}",
                     f"[::1]:{self.board.port}"):
            code, _, _ = raw(self.board, "GET", "/api/state", host=host)
            self.assertEqual(200, code, host)

    def test_another_host_is_refused_and_nothing_moves(self):
        card = self.board.work / "tasks" / "review" / "01-a-card.md"
        for host in ("evil.example", f"evil.example:{self.board.port}",
                     f"127.0.0.1:{self.board.port + 1}", "localhost",
                     f"localhost.:{self.board.port}", ""):
            code, _, data = raw(self.board, "GET", "/", host=host)
            self.assertEqual(421, code, host)
            code, _, data = raw(self.board, "POST", "/api/move", host=host,
                                body={"file": "01-a-card.md", "from": "review",
                                      "to": "in-progress"})
            self.assertEqual(421, code, host)
            self.assertEqual("host-unlisted", json.loads(data)["code"])
            self.assertTrue(card.is_file(), host)
        seen = self.callers_seen()
        self.assertEqual(seen["hostCheck"]["mode"], "enforce")
        self.assertGreaterEqual(seen["counts"]["hostRefused"], 12)


class ARealBoardEnforcingTheHostBehindAProxy(BoardCase):
    ENV = {"BOARD_HOST_CHECK": "enforce",
           "BOARD_ALLOWED_ORIGINS": "https://bench.example.ts.net"}

    def test_the_proxys_host_is_answered_and_its_neighbours_are_not(self):
        for host in ("bench.example.ts.net", "bench.example.ts.net:443"):
            self.assertEqual(200, raw(self.board, "GET", "/api/state", host=host)[0], host)
        for host in ("bench.example.ts.net:80", "other.example.ts.net",
                     "example.ts.net"):
            self.assertEqual(421, raw(self.board, "GET", "/api/state", host=host)[0], host)
        allowed = self.callers_seen()["hostCheck"]["allowed"]
        self.assertIn("bench.example.ts.net", allowed)


# ── reference files ────────────────────────────────────────────────────


class ReferenceFilesAreInert(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def files(board):
            ref = board.work / "reference"
            ref.mkdir()
            (ref / "page.html").write_text(
                "<h1>Doc</h1><script>fetch('/api/move')</script>")
            (ref / "pic.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
            (ref / "notes.md").write_text("# notes\n")
            (ref / "spec.pdf").write_bytes(b"%PDF-1.4\n")
            (ref / "blob.xhtml").write_text("<html/>")
        cls.board = Board(_Cleanup(cls), prepare=files)

    def headers(self, name: str) -> dict:
        code, headers, _ = raw(self.board, "GET", f"/files/reference/{name}",
                               host=f"127.0.0.1:{self.board.port}")
        self.assertEqual(200, code, name)
        return {k.lower(): v for k, v in headers.items()}

    def test_html_and_svg_are_sandboxed_and_nosniff(self):
        for name, ctype in (("page.html", "text/html"), ("pic.svg", "image/svg+xml")):
            got = self.headers(name)
            self.assertTrue(got["content-type"].startswith(ctype))
            self.assertEqual(got["x-content-type-options"], "nosniff")
            policy = [p.strip() for p in got["content-security-policy"].split(";")]
            self.assertEqual(policy[0], "sandbox", "no allow-scripts, allow-same-origin")
            self.assertIn("script-src 'none'", policy)
            self.assertIn("form-action 'none'", policy)

    def test_every_other_file_is_nosniff_and_unknown_types_do_not_render(self):
        for name in ("notes.md", "spec.pdf", "blob.xhtml"):
            got = self.headers(name)
            self.assertEqual(got["x-content-type-options"], "nosniff", name)
            self.assertNotIn("content-security-policy", got, name)
        self.assertEqual(self.headers("blob.xhtml")["content-type"],
                         "application/octet-stream")


# ── the bridges forward the token ──────────────────────────────────────


class TheClaudeBridgeForwardsTheToken(unittest.TestCase):
    def capture(self):
        got = []

        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], got

    def emit(self, port: int, **env) -> None:
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("BOARD_", "AGENT_"))}
        clean.update(BOARD_PORT=str(port), **env)
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1",
                   "tool_name": "Read", "tool_input": {"file_path": "a.py"},
                   "tool_use_id": "tu_1"}
        subprocess.run([sys.executable, str(CORE / "adapters" / "claude" / "emit.py")],
                       input=json.dumps(payload), text=True, env=clean,
                       capture_output=True, timeout=30, check=True)

    def test_the_token_rides_beside_the_agent(self):
        port, got = self.capture()
        self.emit(port, BOARD_AGENT_ID=RUN, BOARD_RUN_TOKEN=TOKEN)
        self.assertEqual(got[0]["agent"], RUN)
        self.assertEqual(got[0]["token"], TOKEN)

    def test_no_agent_or_no_token_sends_none(self):
        port, got = self.capture()
        self.emit(port, BOARD_RUN_TOKEN=TOKEN)
        self.emit(port, BOARD_AGENT_ID=RUN)
        self.assertEqual(len(got), 2)
        for body in got:
            self.assertNotIn("token", body)


if __name__ == "__main__":
    unittest.main()
