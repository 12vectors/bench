#!/usr/bin/env python3
"""Live kanban board for ../tasks/ — board, session timeline, heads-up display.

    python3 .task-manager/manager/board.py            # serve on :26071, open a browser
    python3 .task-manager/manager/board.py --port 9000 --no-open

The manager sits cleanly on top of the tasks/ directory: it reads and moves
task files, but the tasks work as a plain folder kanban without it. See
../CLAUDE.md for the workflow and the module map:

    config.py     paths, stages, launch configuration
    state.py      shared registries, event persistence, SSE fan-out
    taskfiles.py  reading/moving task files (the only code touching tasks/)
    events.py     hook payloads → displayable events, session registry
    agents.py     headless work/review agents: launch, reap, stop, diff
    watch.py      2s disk poller narrating moves made outside the API
    httpd.py      HTTP routes, SSE stream, the page itself
    .prompts/     agent prompt templates (read fresh on every launch)

Stdlib only, no install.
"""

from __future__ import annotations

import argparse
import errno
import threading
import webbrowser
from http.server import ThreadingHTTPServer

import config
import drive
import events
import github
import httpd
import state
import watch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    args = parser.parse_args()
    state.serve_port = args.port

    url = f"http://127.0.0.1:{args.port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), httpd.Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # The port is pinned, so this is nearly always the board already running.
        print(f"Port {args.port} is already in use — assuming the board is up at {url}")
        if not args.no_open:
            webbrowser.open(url)
        return

    config.SESSIONS_DIR.mkdir(exist_ok=True)
    config.AGENT_DIR.mkdir(exist_ok=True)
    events.load_disk_sessions()
    threading.Thread(target=watch.watcher, daemon=True).start()
    threading.Thread(target=github.poller, daemon=True).start()
    threading.Thread(target=github.reconcile, daemon=True).start()
    drive.adopt()

    print(f"Task board for {config.TASKS}\n  {url}\n  Ctrl-C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
