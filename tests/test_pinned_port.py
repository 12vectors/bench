"""A pinned port stays pinned. start.sh's port handling, run end to end as
a subprocess against a scratch host whose board.py is a stub that records
how it was launched. Run with: python3 -m unittest discover -s tests

The bug this guards: the free-port probe bound without SO_REUSEADDR while
the board's ThreadingHTTPServer sets it, so a socket the just-stopped board
left in TIME_WAIT read as "taken by something else" — and start.sh then
walked to the next port and wrote that over the user's own BOARD_PORT pin.
The sockets here are real: TIME_WAIT is produced by an actual closed
connection, and a holder is an actual listener.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES = ["backlog", "to-do", "in-progress", "review", "done"]

# A board.py that serves nothing and only records the port it was told to
# use — every assertion about "which port did we start on" reads this.
STUB_BOARD = """#!/usr/bin/env python3
import json, pathlib, sys
pathlib.Path(__file__).with_name("launched.json").write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8")
"""

ENV_TEMPLATE = """# The port the board serves on.
BOARD_PORT={port}

# Seconds between disk polls of the stage directories.
BOARD_WATCH_INTERVAL=2
BOARD_SYNC=
"""


def make_host(root: Path, port: int) -> Path:
    """A host project whose board is pinned to `port`: the real start.sh and
    install.py, a stub board.py, and a local/.env with the pin plus a couple
    of other settings, so a rewrite that damages the file is visible."""
    host = root / "host"
    (host / ".claude").mkdir(parents=True)
    tm = host / ".task-manager"
    tm.mkdir()
    shutil.copy(REPO / "start.sh", tm / "start.sh")
    shutil.copy(REPO / "install.py", tm / "install.py")
    shutil.copytree(REPO / "manager" / "core" / "adapters" / "claude",
                    tm / "manager" / "core" / "adapters" / "claude")
    shutil.copy(REPO / "manager" / "core" / ".env.example",
                tm / "manager" / "core" / ".env.example")
    (tm / "manager" / "core" / "board.py").write_text(STUB_BOARD,
                                                      encoding="utf-8")
    for stage in STAGES + ["archive"]:
        d = tm / "tasks" / stage
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
    local = tm / "manager" / "local"
    local.mkdir(parents=True)
    # An .env on disk also disarms install.py's first-boot clean, so the
    # start under test does nothing but wire, probe and launch.
    (local / ".env").write_text(ENV_TEMPLATE.format(port=port),
                                encoding="utf-8")
    return tm


def clean_env() -> dict:
    """No BOARD_* leaking in from the developer's shell: the pin under test
    is the one in local/.env. BROWSER keeps the browser-open path silent —
    `python3 -m webbrowser` runs it, and a test must not raise a window."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
    env["BROWSER"] = "/usr/bin/true"
    return env


def run_start(tm: Path, wait: str = "2", **overrides) -> subprocess.CompletedProcess:
    env = clean_env()
    env["BOARD_PORT_WAIT"] = wait
    env.update(overrides)
    return subprocess.run(
        ["bash", str(tm / "start.sh")], capture_output=True, text=True,
        cwd=tm.parent, env=env, stdin=subprocess.DEVNULL, timeout=120)


def launched(tm: Path):
    """The arguments the stub board.py was started with, or None if start.sh
    never got that far."""
    marker = tm / "manager" / "core" / "launched.json"
    return json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None


def free_port(count: int = 1) -> int:
    """The first of `count` consecutive ports nothing holds — including
    nothing in TIME_WAIT, since the probe here binds without SO_REUSEADDR."""
    for base in range(27100, 27600, count):
        held, free = [], True
        for candidate in range(base, base + count):
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", candidate))
            except OSError:
                free = False
                s.close()
                break
            held.append(s)
        for s in held:
            s.close()
        if free:
            return base
    raise AssertionError("no run of free ports to test with")


def leave_time_wait(port: int) -> None:
    """Leave a real socket in TIME_WAIT on `port`: connect to a listener and
    close the server's end first, which is exactly what a board shutting
    down does to the browser tab still attached to it."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = listener.accept()
    conn.close()
    client.close()
    listener.close()


def plain_bind_refuses(port: int) -> bool:
    """Would the old probe — a bind with no SO_REUSEADDR — call this port
    busy? False means the platform does not reproduce the bug at all, and
    the test says so rather than passing on nothing."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


class Holder:
    """Something on the port that is not this project's board: an HTTP
    server that answers /api/state with `root` (None = answers nothing the
    probe recognises, i.e. a stranger)."""

    def __init__(self, port: int, root: str | None = None):
        payload = json.dumps({"board": {"root": root}}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/state" and root is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_error(404)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class PinnedPort(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.port = free_port(2)
        self.tm = make_host(self.scratch, self.port)
        self.env_file = self.tm / "manager" / "local" / ".env"
        self.env_before = self.env_file.read_text(encoding="utf-8")

    def env_values(self) -> dict:
        return dict(
            line.split("=", 1)
            for line in self.env_file.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#"))

    def test_a_socket_in_time_wait_does_not_move_the_pin(self):
        """The reported bug: stop the board, start it again, and the socket
        its own shutdown left behind sends it to the next port."""
        leave_time_wait(self.port)
        if not plain_bind_refuses(self.port):
            self.skipTest("this platform lets a plain bind reuse TIME_WAIT")
        result = run_start(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(launched(self.tm), ["--port", str(self.port)])
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         self.env_before)
        self.assertNotIn("Rewriting BOARD_PORT", result.stdout)
        self.assertNotIn("is busy", result.stdout)

    def test_the_probe_agrees_with_the_server_it_probes_for(self):
        """The root cause, on its own: start.sh's is_free must call a port
        free exactly when the board could bind it — free over TIME_WAIT
        (ThreadingHTTPServer sets SO_REUSEADDR), busy under a listener."""
        text = (REPO / "start.sh").read_text(encoding="utf-8")
        probe = (text.split('python3 - "$1" <<\'PY\'\n', 1)[1]
                     .split("\nPY\n", 1)[0])
        leave_time_wait(self.port)
        if not plain_bind_refuses(self.port):
            self.skipTest("this platform lets a plain bind reuse TIME_WAIT")

        def is_free(port: int) -> bool:
            return subprocess.run([sys.executable, "-c", probe, str(port)],
                                  capture_output=True).returncode == 0

        self.assertTrue(is_free(self.port), "TIME_WAIT read as occupied")
        holder = Holder(self.port + 1)
        self.addCleanup(holder.stop)
        self.assertFalse(is_free(self.port + 1), "a listener read as free")

    def test_a_holder_that_lets_go_during_the_wait_keeps_the_pin(self):
        """A predecessor still shutting down when the new start probes: the
        brief retry is what keeps the restart on its own port."""
        holder = Holder(self.port)
        timer = threading.Timer(1.5, holder.stop)
        timer.start()
        self.addCleanup(timer.cancel)
        result = run_start(self.tm, wait="20")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Port {self.port} is busy — waiting", result.stdout)
        self.assertEqual(launched(self.tm), ["--port", str(self.port)])
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         self.env_before)

    def test_a_foreign_holder_makes_it_wait_then_walk_and_say_so(self):
        """Genuinely occupied: the hop is right — the hooks and agents read
        BOARD_PORT and must reach the live board — but it has to be said in
        full, since it overwrites something the user chose."""
        holder = Holder(self.port)
        self.addCleanup(holder.stop)
        result = run_start(self.tm, wait="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        moved = self.port + 1
        self.assertIn(f"Port {self.port} is busy — waiting up to 2s",
                      result.stdout)
        self.assertIn(f"Port {self.port} is held by another process — "
                      f"using {moved} instead.", result.stdout)
        self.assertIn(f"manager/local/.env: {self.port} → {moved}",
                      result.stdout)
        self.assertIn(f"To reclaim {self.port}", result.stdout)
        self.assertIn(f"set BOARD_PORT={self.port} in manager/local/.env",
                      result.stdout)
        self.assertNotIn("to manager/.env", result.stdout)
        self.assertEqual(launched(self.tm), ["--port", str(moved)])
        values = self.env_values()
        self.assertEqual(values["BOARD_PORT"], str(moved))
        self.assertEqual(values["BOARD_WATCH_INTERVAL"], "2")
        self.assertIn("# Seconds between disk polls of the stage directories.",
                      self.env_file.read_text(encoding="utf-8"))

    def test_our_own_board_short_circuits_before_any_probe(self):
        """A board of ours already answering there is the one case that
        neither probes nor hops: it opens the tab and stops."""
        holder = Holder(self.port, root=str(self.tm / "tasks"))
        self.addCleanup(holder.stop)
        result = run_start(self.tm)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Board already running at http://127.0.0.1:{self.port}/",
                      result.stdout)
        self.assertIsNone(launched(self.tm))
        self.assertNotIn("is busy", result.stdout)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         self.env_before)


if __name__ == "__main__":
    unittest.main()
