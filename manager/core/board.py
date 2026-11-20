#!/usr/bin/env python3
"""Live kanban board for ../tasks/ — board, session timeline, heads-up display.

    python3 .task-manager/manager/board.py            # serve on :26071, open a browser
    python3 .task-manager/manager/board.py --port 9000 --no-open
    python3 .task-manager/manager/board.py --doctor    # readiness, then exit
    python3 .task-manager/manager/board.py --setup-guide [--json]   # setup state

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
    reasoning.py  orchestrator reasoning jobs: pinned context, one public response
    coordinate.py a review card's checks, independent review, repair and merge
    scheduler.py  bounded orchestration passes over a durable operation ledger
    watch.py      2s disk poller narrating moves made outside the API
    httpd.py      HTTP routes, SSE stream, the page itself
    .prompts/     agent prompt templates (read fresh on every launch)

Stdlib only, no install.

Every core module is imported here, at the top, and the selected hosting
provider is loaded before the board serves (task 122, WP13). A running
board therefore executes the code that was on disk when it started — a
core update takes effect on restart, all of it at once — rather than
whichever module a request happened to import first. No core module
imports another from inside a function; `tests/test_architecture.py`
fails one that does. Importing reads code and defines names: it starts no
thread, opens no socket and runs no probe; everything that does is below,
in `main`, as it was.
"""

from __future__ import annotations

import argparse
import errno
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

import acceptance  # noqa: F401  (eager: see the docstring)
import agents  # noqa: F401
import agentsettings  # noqa: F401
import agreements  # noqa: F401
import artifacts  # noqa: F401
import appinspect  # noqa: F401
import assets
import basechoice  # noqa: F401
import brain  # noqa: F401
import callers  # noqa: F401
import changes
import commands  # noqa: F401
import completion  # noqa: F401
import config
import coordinate  # noqa: F401
import decisions  # noqa: F401
import delivery  # noqa: F401
import doctor
import drive
import events
import evidence  # noqa: F401
import execowner  # noqa: F401
import gatepolicy  # noqa: F401
import gates  # noqa: F401
import movements  # noqa: F401
import githost
import gitproc  # noqa: F401
import gitrepo  # noqa: F401
import httpd
import inputs  # noqa: F401
import operations  # noqa: F401
import orchestrator  # noqa: F401
import phases
import phasescope  # noqa: F401
import plans  # noqa: F401
import previews
import profiles  # noqa: F401
import progress
import readiness  # noqa: F401
import reasoning
import records  # noqa: F401
import recovery  # noqa: F401
import redact  # noqa: F401
import refine
import reports  # noqa: F401
import required
import resultidentity  # noqa: F401
import results  # noqa: F401
import reviews
import routes  # noqa: F401
import scheduler
import settings  # noqa: F401
import setupbase  # noqa: F401
import setupguide
import setuptasks  # noqa: F401
import stackactions  # noqa: F401
import stacks  # noqa: F401
import staleness  # noqa: F401
import state
import sync
import taskfiles  # noqa: F401
import templates  # noqa: F401
import verifierreport  # noqa: F401
import verify
import watch
import workbase  # noqa: F401


def load_provider() -> None:
    """Load the hosting provider code a running board may execute — the
    selected provider, and `none`, which `githost.agent_verbs` falls back
    to when the selected one does not act on this remote — so the board
    executes the providers that were on disk when it started. Loading
    reads each module and checks its declared functions; it asks the
    network, `gh` and the remote nothing. A provider that does not load is
    not a reason to refuse to serve: it is reported where it always was
    (the Configuration check, Settings), so startup is unchanged by it."""
    for load in (githost.module, lambda: githost.load("none")):
        try:
            load()
        except githost.Refused:
            pass


def wire() -> None:
    """The one place a lower module is told about a higher one, so neither
    imports the other from inside a function (task 122).

    - `drive` runs a preview's process but does not know what a preview
      keeps once it has ended; `previews.record_last` does, and is
      registered as the drive's end hook. Idempotent.
    """
    drive.on_ended(previews.record_last)


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


def boot_assets() -> None:
    """Take the page's asset snapshot, or refuse to serve (task 123).

    The page and every file `web/manifest.json` lists are read into memory
    and checked against their hashes before the port is bound; a missing,
    linked, oversized or changed asset means this install would serve a
    page that cannot load what it names, so the board says which and does
    not start. Under BOARD_ASSETS=disk the same checks run except the
    hashes, and nothing is kept."""
    if config.ASSETS_PROBLEM:
        print(f"note: {config.ASSETS_PROBLEM}")
    try:
        assets.boot()
    except assets.Refused as refused:
        print("This board cannot serve its page's assets:\n")
        for problem in refused.problems:
            print(f"  - {problem}")
        print(f"\n  assets      {assets.web_dir()}"
              f"\n\nAn interrupted update leaves exactly this: finish it "
              f"(update.sh) or restore manager/core/, then start again. "
              f"Editing the page? python3 {config.CORE / 'assets.py'} "
              f"--write rewrites the manifest, and BOARD_ASSETS=disk serves "
              f"from disk without a snapshot.")
        raise SystemExit(2) from None


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
    parser.add_argument("--setup-guide", action="store_true",
                        help="print the project setup guide (the same "
                             "reading as the browser's), or record a "
                             "decision with it; see --setup-guide --help")
    argv = sys.argv[1:]
    load_provider()
    wire()
    if "--setup-guide" in argv:
        refuse_broken_layout()
        raise SystemExit(setupguide.cli([a for a in argv
                                         if a != "--setup-guide"]))
    if "--doctor" in argv:
        refuse_broken_layout()
        raise SystemExit(doctor.cli([a for a in argv if a != "--doctor"]))
    args = parser.parse_args(argv)
    refuse_broken_layout()
    boot_assets()
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
    # A batch of required checks a dead board was running is closed the
    # same way: the check in flight reads "no result — the board
    # restarted", the rest not requested. Nothing is asked for again.
    required.recover()
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
    # An orchestrator reasoning run a dead board left in flight lost its
    # response with the process: closed as failed, naming the restart, and
    # never re-run by itself (task 139).
    reasoning.recover()
    # Agent-reported progress survives the restart as history. A run this
    # board was tracking is closed `unobserved` — its ending will not be
    # seen, so its last update must stop reading as "doing now".
    progress.load()
    threading.Thread(target=watch.watcher, daemon=True).start()
    threading.Thread(target=changes.poller, daemon=True).start()
    threading.Thread(target=changes.reconcile, daemon=True).start()
    threading.Thread(target=phases.beat, daemon=True).start()
    # The orchestration beat (task 141): with no agreement in force, or one
    # whose Bench scope is not enabled, each pass reads one file and does
    # nothing. Its first pass waits one interval, so a restart never
    # launches anything in the moment it comes up.
    threading.Thread(target=scheduler.beat, daemon=True).start()
    if config.SYNC:
        # Team mode: board commits publish themselves and a beat pulls what
        # the other boards published. Off, neither thread nor hook exists.
        sync.install()
        threading.Thread(target=sync.beat, daemon=True).start()
    drive.adopt()
    # A preview preparation a dead board left running is closed, its
    # process group stopped only when it is provably bench's own; nothing
    # is prepared again by itself.
    previews.recover()

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
