#!/usr/bin/env python3
"""Live kanban board for ../tasks/ — board, session timeline, heads-up display.

    python3 .task-manager/manager/board.py            # serve on :26071, open a browser
    python3 .task-manager/manager/board.py --port 9000 --no-open
    python3 .task-manager/manager/board.py --doctor    # readiness, then exit

The manager sits cleanly on top of the tasks/ directory: it reads and moves
task files, but the tasks work as a plain folder kanban without it. See
../AGENTS.md for the workflow and the module map:

    config.py     paths, stages, launch configuration
    state.py      shared registries, event persistence, SSE fan-out
    taskfiles.py  reading/moving task files (the only code touching tasks/)
    events.py     hook payloads → displayable events, session registry
    sync.py       origin/main as the shared board: push on move, pull on a beat
    reviews.py    the pinned, run-owned checkout a PR review runs in
    verify.py     named checks bench runs itself, and the evidence they leave
    doctor.py     what this board would grant, run and refuse — no model
    agents.py     headless work/review agents: launch, reap, stop, diff
    phases.py     a phase run: its own branch, its members merged into it
    refine.py     card comments, refinement jobs, Apply and Discard
    watch.py      2s disk poller narrating moves made outside the API
    httpd.py      HTTP routes, SSE stream, the page itself
    .prompts/     agent prompt templates (read fresh on every launch)

Stdlib only, no install.
"""

from __future__ import annotations

import argparse
import errno
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

import config
import doctor
import drive
import events
import github
import httpd
import phases
import refine
import reviews
import state
import sync
import verify
import watch


def refuse_broken_layout() -> None:
    """A layout that does not hold together is a refusal, not a warning.

    Every fallback available from here leads to the wrong repository — the
    tool's own, or the application's — so the board says what is wrong and
    does not serve. `config.problems()` is the one authority; install.py
    prints the same list (task 56).
    """
    problems = config.problems()
    if not problems:
        return
    layout = config.layout()
    print("This board cannot serve the layout it is configured with:\n")
    for problem in problems:
        print(f"  - {problem}")
    print(f"\n  tool        {layout['tool']}"
          f"\n  application {layout['app']}"
          f"\n  work state  {layout['work']}"
          f"\n  worktrees   {layout['worktrees']}"
          f"\n\nSettings live in {config.LOCAL / '.env'} "
          f"(defaults in {config.CORE / '.env.example'}).")
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    # Declared so `board.py --help` names it, and dispatched *before*
    # parsing so `--doctor --help` reaches the doctor's own options rather
    # than this parser's. Wiring, which is all this module is: every
    # option after `--doctor`, and every word it prints, belongs to
    # doctor.py.
    parser.add_argument("--doctor", action="store_true",
                        help="print what this board would grant, run and "
                             "refuse, and exit (see --doctor --help)")
    argv = sys.argv[1:]
    if "--doctor" in argv:
        refuse_broken_layout()
        raise SystemExit(doctor.cli([a for a in argv if a != "--doctor"]))
    args = parser.parse_args(argv)
    refuse_broken_layout()
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

    # parents=True: local/state/ is gitignored and ships empty, so a fresh
    # checkout has neither it nor its children — boot must create the chain.
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
    events.load_disk_sessions()
    # Evidence a dead board left marked `running` is evidence this board
    # does not own, and the one thing that must never happen is a
    # verification drifting towards "passed" because nobody closed it.
    # Nothing is re-run: asking again is a person's decision.
    verify.recover()
    # And a review checkout a dead board left holding one: the record is
    # the only thing that can say who owned it, so an unreleased record
    # from another process is released here — removing the checkout only
    # under every guard `reviews.release` applies, and never one with
    # uncommitted work in it. Nothing is reviewed again.
    reviews.recover()
    # A card refinement a dead board left running lost its result with the
    # process. It is closed as failed, naming the restart, and never re-run
    # — a proposal waiting for Apply or Discard survives as it was.
    refine.recover()
    threading.Thread(target=watch.watcher, daemon=True).start()
    threading.Thread(target=github.poller, daemon=True).start()
    threading.Thread(target=github.reconcile, daemon=True).start()
    threading.Thread(target=phases.beat, daemon=True).start()
    if config.SYNC:
        # Team mode: board commits publish themselves and a beat pulls what
        # the other boards published. Off, neither thread nor hook exists.
        sync.install()
        threading.Thread(target=sync.beat, daemon=True).start()
    drive.adopt()

    print(f"Task board for {config.TASKS}")
    if config.EXTERNAL:
        # Three roots are a thing a person should be able to check at a
        # glance, and the one moment they are all certainly on screen is
        # the line that says the board is up.
        print(f"  application {config.REPO}\n"
              f"  work state  {config.work_root()}"
              + (f" (git: {config.state_repo()})" if config.state_repo()
                 else " (no git — the board commits nothing)") + "\n"
              f"  worktrees   {config.WORKTREES}")
    print(f"  {url}\n  Ctrl-C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
