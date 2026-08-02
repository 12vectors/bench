"""A phase runs itself, on a branch of its own.

A phase is a card that lists its cards (`taskfiles.weave_phases`). Running
one means working that list into a single integration branch: `phase/<stem>`
cut from the newest main, each member branched from the phase's tip, run
headless, merged back when its checks are green, and the next one started.
At the end one PR into `main`, for a human. The board never merges into
`main` — a phase branch is the board's own, and merging into it is
bookkeeping in the same family as committing a move.

**The runner is a beat, not an agent.** Everything it decides is already
structured state — a card's stage, a PR's CI verdict, whether one branch is
contained in another — so an agent paid to poll would be the wrong tool at
the wrong price.

**The beat is stateless.** On each pass it recomputes, from disk and from
git, which members are finished, which is first unfinished and what that
one needs. It holds no registry of where a phase "is": a restarted board
resumes a phase by looking, and the same logic answers "what now?" whether
the last event was a launch, a merge or a crash. Two things carry the
memory, and both are durable:

- **git** — a member is merged when its branch is contained in the phase
  branch. That is what makes a restart safe from repeating a merge.
- **the phase card** — a `## Phase log` section the runner appends one line
  to per decision (a run started, a member started, a member merged, a
  halt). It is the record a person reads, and the only thing that can tell
  "the phase already started this member and its run ended badly" from "the
  phase has not reached this member yet". Without it a restart would
  silently relaunch a run that died.

**Halt, never skip.** Five conditions stop a phase, each of them already a
visible state on the card: a member that declined (`NOT READY`), a run that
exited non-zero, a clean exit that committed nothing, CI red, and a merge
into the phase branch that is not mechanical. A phase that steps over a
failed card builds the rest on a foundation that never landed. Halting is
recorded in the log and nothing retries by itself; running the phase again
is a person's decision and appends the line that clears the halt.

**One board runs it.** The actor rule decides, and the phase card's
**Assignee** is where it is written down — the same claim that gates
starting work. A replica renders the phase and advances nothing.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

import agents
import config
import github
import state
import taskfiles

LOG_HEADING = "Phase log"

# One log line: `- <date> <time> · <entry>`. The entry is what is parsed;
# the stamp in front of it is for the reader.
LOG_LINE_RE = re.compile(r"^-\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+·\s+(.+?)\s*$")
STARTED_RE = re.compile(r"^(\d+) started\b")
HALTED_RE = re.compile(r"^halted(?: at (\d+))? — (.*)$")
RUN_RE = re.compile(r"^run started\b")
STOPPED_RE = re.compile(r"^stopped — (.*)$")

# What the last computed pass saw, for the API to render without paying for
# a git walk on every request. A display cache, not a decision: nothing here
# is ever read back to decide anything.
SNAPSHOTS: dict[str, dict] = {}

_LOCK = threading.Lock()          # one pass at a time, whoever asks for it


def _git(*args: str, cwd=None, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(cwd or config.REPO), *args],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 128, "", "timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 128, "", str(exc))


def _branch_exists(branch: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet", branch).returncode == 0


def _contains(branch: str, tip: str) -> bool:
    """Is `branch` already in `tip`? The stateless record of a merge."""
    return _git("merge-base", "--is-ancestor", branch, tip).returncode == 0


def _say(filename: str, summary: str) -> None:
    state.record_board_event({"kind": "phase", "actor": "board",
                              "file": filename, "summary": summary})


# ── the log on the phase card ──────────────────────────────────────────


def log_entries(text: str) -> list[str]:
    """The `## Phase log` section's entries, in the order they were made."""
    section = re.search(rf"^##\s+{LOG_HEADING}\s*$(.*?)(?=^##\s|\Z)", text,
                        re.MULTILINE | re.DOTALL)
    if not section:
        return []
    entries = []
    for line in section.group(1).splitlines():
        match = LOG_LINE_RE.match(line.strip())
        if match:
            entries.append(match.group(1))
    return entries


def _write_log(phase: dict, entry: str) -> bool:
    """One line into the log, where it reaches git like every other write
    the board makes to a task file. The stage is asked of the disk: a pass
    that finishes a phase moves the card, and the record is written to
    wherever the card actually is. Returns whether the line landed."""
    stage = taskfiles.find_stage_of(phase["file"]) or phase["stage"]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return taskfiles.append_to_section(phase["file"], stage, LOG_HEADING,
                                       f"- {stamp} · {entry}", "phase log")


def _record(phase: dict, entry: str) -> None:
    """Record a decision, and refuse to act if it cannot be written down.

    The log is the durable memory a restart reads to tell "this member has
    already started" from "the phase has not reached it yet". An action that
    outran its own record — a launch or a merge with no line behind it —
    is exactly what a restarted board would repeat. So a log that will not
    take the line is itself a halt, raised here before the action it was
    meant to precede ever happens. The halt path writes best-effort
    (`_write_log`) so that recording the halt can never raise in turn."""
    if not _write_log(phase, entry):
        raise _Halt("could not write the phase log — refusing to act without "
                    "the record a restart reads")


def _this_run(entries: list[str]) -> list[str]:
    """The entries since the last `run started` — the run in progress.

    A run is scoped to its own line, so running a phase again after a halt
    starts from a clean slate: a member whose run died is launchable again,
    which is exactly what asking for the run again meant. Nothing is lost
    by scoping it, because what has *merged* is answered by git, not here.
    """
    for index in range(len(entries) - 1, -1, -1):
        if RUN_RE.match(entries[index]):
            return entries[index + 1:]
    return entries


def _started(entries: list[str]) -> set[str]:
    """Members this run has already launched, by canonical number — `07`,
    `7` and `#007` are one card wherever a number is read or written."""
    return {taskfiles.canonical_number(match.group(1)) for match in
            (STARTED_RE.match(e) for e in _this_run(entries)) if match}


def run_state(entries: list[str]) -> dict:
    """Where the phase stands, read off its own log — the whole of what a
    restarted board, and the chip in the header, know about a run.

    Four answers, and the last line that says one of them wins: `running`
    (a run was started and nothing has ended it), `halted` (with the reason
    and the member it happened at), `stopped` (a person held it) and `idle`
    (a card whose phase has never been run). Running it again is what
    clears a halt or a stop, because that line is the person's decision.
    """
    for entry in reversed(entries):
        halted = HALTED_RE.match(entry)
        if halted:
            return {"state": "halted", "reason": halted.group(2),
                    "at": halted.group(1)}
        stopped = STOPPED_RE.match(entry)
        if stopped:
            return {"state": "stopped", "reason": stopped.group(1), "at": None}
        if RUN_RE.match(entry):
            return {"state": "running", "reason": "", "at": None}
    return {"state": "idle", "reason": "", "at": None}


def _halt_reason(entries: list[str]) -> str | None:
    """The halt still in force, as one line — the member it happened at and
    why, which is what the log's reader and the ticker both want."""
    where = run_state(entries)
    if where["state"] != "halted":
        return None
    return (f"{where['at']}: {where['reason']}" if where["at"]
            else where["reason"])


# ── reading the board ──────────────────────────────────────────────────


def _cards() -> tuple[dict[str, dict], dict[str, dict]]:
    """Every card, by filename and by canonical number, woven — so a
    member already knows the phase that holds it."""
    tasks = [task for stage in taskfiles.collect()["stages"]
             for task in stage["tasks"]]
    by_file = {task["file"]: task for task in tasks}
    by_number = {}
    for task in tasks:
        if task["number"]:
            by_number.setdefault(taskfiles.canonical_number(task["number"]), task)
    return by_file, by_number


def _running_on(filename: str) -> bool:
    with state.LOCK:
        return any(r["task"] == filename and r["status"] == "running"
                   for r in state.AGENTS.values())


def _failure_note(filename: str) -> str:
    """What the run that just died said, when this board still remembers
    it. A restart forgets, and the state says the same thing either way."""
    with state.LOCK:
        records = [r for r in state.AGENTS.values()
                   if r["task"] == filename and r.get("failure")]
    if not records:
        return ""
    latest = max(records, key=lambda r: r["started"])
    excerpt = (latest["failure"].get("excerpt") or "").splitlines()
    return excerpt[-1].strip()[:120] if excerpt else ""


def _member_state(task: dict, branch: str, started: set[str]) -> tuple[str, str]:
    """One member → (state, why). The five halting conditions and the three
    ways a member can be in flight, all derived from what the card and git
    already say.

    - `merged`  — it is settled *and* its work is in the phase branch (or
                  there was never a branch to bring)
    - `running` — an agent is on it
    - `ready`   — it reached review/ and its checks are not against it
    - `waiting` — checks still running
    - `halt`    — one of the five; the reason is for the log and the ticker
    - `pending` — the phase has not reached it yet

    Containment alone does not mean merged, and the difference matters: a
    run that exits cleanly without committing leaves a branch that *is*
    contained in the phase branch — an empty one. Reading that as merged is
    exactly how a broken launch would hide, so the card has to have settled
    into review/ or done/ as well.
    """
    # An agent on the card outranks everything the card says: whatever it is
    # doing, the phase must not act on the same task underneath it — which
    # is also what keeps a card moved by hand mid-run from being launched
    # or merged twice.
    if _running_on(task["file"]):
        return "running", ""
    member_branch = f"task/{task['file'][:-3]}"
    has_branch = _branch_exists(member_branch)
    settled = task["stage"] in ("review", "done")
    if settled and (not has_branch or _contains(member_branch, branch)):
        return "merged", ""
    number = taskfiles.canonical_number(task["number"]) if task["number"] else ""
    if number not in started and not has_branch:
        return "pending", ""

    # The phase started this one (or someone did). Where it ended decides.
    if settled:
        # Green means the checks are not against it: red halts, running
        # waits, and a member with no checks at all advances — a project
        # without CI must not deadlock every phase it runs.
        ci = (github.PR_STATE.get(task["file"]) or {}).get("ci")
        if ci == "fail":
            return "halt", "its CI is red"
        if ci == "running":
            return "waiting", "its checks are still running"
        return "ready", ""
    if task["stage"] == "in-progress":
        note = _failure_note(task["file"])
        return "halt", ("its run ended without reaching review/"
                        + (f" — {note}" if note else ""))
    return "halt", (f"it is back in {task['stage']}/ — the run declined it, "
                    f"or someone walked it back")


def _snapshot(phase: dict, by_file: dict[str, dict]) -> dict:
    """The whole phase, recomputed. Nothing is remembered between passes."""
    branch = agents.phase_branch(phase["file"])
    entries = log_entries(phase["body"])
    where = run_state(entries)
    started = _started(entries)
    members = []
    for listed in phase["members"]:
        task = by_file.get(listed["file"])
        if task is None:                      # vanished between reads
            continue
        member_state, why = _member_state(task, branch, started)
        # canonical throughout: the log, `**Depends on:**` and a filename's
        # own number all have to be the same key or none of them match
        members.append({"number": taskfiles.canonical_number(task["number"])
                        if task["number"] else None, "file": task["file"],
                        "title": task["title"], "stage": task["stage"],
                        "state": member_state, "why": why,
                        "dependsOn": task["dependsOn"]})
    halted = where["state"] == "halted"
    return {"file": phase["file"], "branch": branch, "stage": phase["stage"],
            "title": phase["title"], "number": phase["number"],
            "members": members,
            # the halt as one line for the log's reader, and in its parts for
            # anything that renders it — the same reading, said twice
            "halted": _halt_reason(entries),
            "haltedAt": where["at"] if halted else None,
            "haltedWhy": where["reason"] if halted else None,
            # is a run in force? The log answers it, so a restarted board and
            # the chip in the header read the same thing
            "running": where["state"] == "running",
            "stopped": where["state"] == "stopped",
            "stoppedBy": where["reason"] if where["state"] == "stopped" else None,
            # the branch is the whole of "this phase has been started"
            "started": _branch_exists(branch)}


def public_state() -> dict:
    """What the API shows: the last pass's reading, per phase card."""
    return {name: dict(snapshot) for name, snapshot in SNAPSHOTS.items()}


# ── the phase branch ───────────────────────────────────────────────────


class _Halt(Exception):
    """A condition the phase stops on: the reason a person needs, and the
    member it happened at when there is one."""

    def __init__(self, reason: str, member: dict | None = None):
        super().__init__(reason)
        self.member = member


def _worktree(phase: dict) -> Path:
    """The phase branch needs a working tree to merge into — the same
    arrangement every other branch here gets, recreated from the branch if
    it was cleaned up."""
    branch = agents.phase_branch(phase["file"])
    path = config.WORKTREES / phase["file"][:-3]
    if path.exists():
        return path
    config.WORKTREES.mkdir(parents=True, exist_ok=True)
    result = _git("worktree", "add", str(path), branch)
    if result.returncode != 0:
        raise _Halt(f"could not put a worktree on {branch}: "
                    f"{result.stderr.strip()[:140]}")
    return path


def _merge_into_phase(phase: dict, branch: str, what: str,
                      member: dict | None = None) -> bool:
    """Merge `branch` into the phase branch — additively, never rebasing,
    never force-pushing. Returns whether anything landed.

    A conflict is not something to be clever about: abort, leave the branch
    exactly as it was, and halt naming the files that collided.
    """
    worktree = _worktree(phase)
    phase_branch = agents.phase_branch(phase["file"])
    if _contains(branch, phase_branch):
        return False
    result = _git("merge", "--no-ff", "--no-edit", "-m",
                  f"phase: merge {branch} into {phase_branch}", branch,
                  cwd=worktree, timeout=180)
    if result.returncode != 0:
        conflicted = _git("diff", "--name-only", "--diff-filter=U",
                          cwd=worktree).stdout.split()
        _git("merge", "--abort", cwd=worktree)
        raise _Halt(f"merging {what} into {phase_branch} conflicts"
                    + (f" on {', '.join(conflicted[:4])}" if conflicted else "")
                    + " — a person has to settle it", member)
    return True


def _push_phase(phase: dict) -> None:
    """Publish the phase branch. Best effort: a phase runs perfectly well
    with no remote, and a member's PR simply does not open without one."""
    rname = github.remote()
    if not rname:
        return
    _git("push", "-u", rname, agents.phase_branch(phase["file"]), timeout=180)


def _freshen(phase: dict) -> None:
    """Merge main into the phase branch on the beat, so a phase that runs
    for hours does not drift into one enormous conflict at the end. A
    conflict here halts the phase like any other."""
    point, _ = agents._fresh_branch_point()
    if _merge_into_phase(phase, point or "main", "main"):
        _say(phase["file"], f"merged main into {agents.phase_branch(phase['file'])}")
        _push_phase(phase)


# ── the beat ───────────────────────────────────────────────────────────


def _mine(phase: dict) -> bool:
    """State syncs; reactions don't. Only the board whose user holds the
    phase card advances it — every replica renders the same phase and
    launches nothing. Outside team mode there is one board, and it acts.

    No local git identity is the one case that does not gate: it is exactly
    where `agents.claim_for_launch` cannot write an assignee and so cannot
    refuse a launch either. Gating the beat on it while the launch went
    through would strand a phase — branch cut, run recorded — that then
    never advances. So a board with no name is the lone actor here, the same
    as it is for starting work."""
    if not config.COMMIT_MOVES:
        return True
    me = taskfiles.actor_name()
    if not me:
        return True
    return phase.get("assignee") == me


def _unfinished_dependencies(member: dict, snapshot: dict,
                             by_number: dict[str, dict]) -> list[str]:
    """`**Depends on:**` guards rather than orders: the list says what runs
    next, this says whether it may. A dependency inside the phase is
    finished when the phase has merged it; one outside is finished when its
    card reaches done/ — the only claim the board can make about a card it
    is not running."""
    inside = {m["number"]: m for m in snapshot["members"] if m["number"]}
    unfinished = []
    for number in member["dependsOn"]:
        held = inside.get(number)
        if held is not None:
            if held["state"] != "merged":
                unfinished.append(number)
            continue
        card = by_number.get(number)
        if card is None or card["stage"] != "done":
            unfinished.append(number)
    return unfinished


def _launch(phase: dict, member: dict) -> None:
    """Start one member: move its card to in-progress if it is not there
    yet (moving a card there is the commitment work starts from), then
    launch the ordinary headless work agent on it — which branches from the
    phase's tip rather than from main."""
    if member["stage"] != "in-progress":
        taskfiles.move_task(member["file"], member["stage"], "in-progress",
                            actor="phase")
    # Recorded before the launch, not after: a crash in between must leave
    # the log saying a launch may have happened, never the reverse.
    _record(phase, f"{member['number']} started")
    try:
        agents.start_agent(member["file"], "in-progress")
    except ValueError as exc:
        raise _Halt(f"{member['number']} would not launch: {exc}")
    _say(phase["file"], f"{phase['file']}: started {member['number']} — "
                        f"{member['title']}")


def _merge_member(phase: dict, member: dict) -> None:
    branch = f"task/{member['file'][:-3]}"
    phase_branch = agents.phase_branch(phase["file"])
    # every advance is narrated, and finishing is its own half of one: what
    # the phase judged green is a different fact from what it then merged,
    # and a phase nobody can reconstruct afterwards is a phase nobody trusts
    _say(phase["file"], f"{phase['file']}: {member['number']} — "
                        f"{member['title']} is green")
    if _branch_exists(branch):
        _merge_into_phase(phase, branch, f"{member['number']}'s branch", member)
        _push_phase(phase)
    _record(phase, f"{member['number']} merged into {phase_branch}")
    _say(phase["file"], f"{phase['file']}: merged {member['number']} into {phase_branch}")


def _finish(phase: dict) -> None:
    """Every member is in. Push the branch, move the card to review/ and
    open the one PR this whole run exists to produce — from there the
    ordinary review apparatus applies unchanged."""
    _push_phase(phase)
    _record(phase, "every card merged — opening the phase PR")
    if phase["stage"] != "review":
        taskfiles.move_task(phase["file"], phase["stage"], "review", actor="phase")
    _say(phase["file"], f"{phase['file']}: every card merged — "
                        f"{agents.phase_branch(phase['file'])} is ready for review")
    github.maybe_open_pr(phase["file"])


def _halt(phase: dict, member: dict | None, reason: str) -> None:
    """Stop, and say so once — at three altitudes, like every other outcome
    a person must not miss. The log holds the halt from here on (so the next
    pass reads it rather than saying the same thing again), the ticker keeps
    the line, and a toast says it to whoever is looking: a halt is rare,
    actionable, and the whole argument for starting a phase and walking away.
    """
    at = f" at {member['number']}" if member and member["number"] else ""
    _write_log(phase, f"halted{at} — {reason}")   # best effort: never re-raise
    _say(phase["file"], f"{phase['file']} halted{at} — {reason}")
    state.broadcast({"type": "toast", "error": True,
                     "message": f"phase {phase['file']} halted{at} — {reason}"})


def _do_pass(phase: dict, snapshot: dict, by_number: dict[str, dict]) -> list[str]:
    """The one next thing, whatever it is. Returns what the phase is
    waiting on when a dependency holds it; raises `_Halt` when it stops."""
    _freshen(phase)
    for member in snapshot["members"]:
        if member["state"] == "merged":
            continue
        if member["state"] in ("running", "waiting"):
            return []                            # nothing to do but wait
        if member["state"] == "halt":
            raise _Halt(member["why"], member)
        if member["state"] == "ready":
            _merge_member(phase, member)
            member["state"] = "merged"
            phase = _reread(phase) or phase      # the log rewrote the card
            continue
        waiting_on = _unfinished_dependencies(member, snapshot, by_number)
        if waiting_on:
            return waiting_on                    # guarded, not skipped
        _launch(phase, member)
        return []
    _finish(phase)
    return []


def advance(phase: dict, by_file: dict[str, dict],
            by_number: dict[str, dict]) -> dict:
    """One phase, one pass. Returns the snapshot the API then shows.

    Nothing is carried between passes: the state is read at the top, acted
    on once, and read back at the bottom.
    """
    snapshot = _snapshot(phase, by_file)
    SNAPSHOTS[phase["file"]] = snapshot
    if not snapshot["running"] or not snapshot["started"] or not _mine(phase):
        return snapshot
    waiting_on: list[str] = []
    try:
        waiting_on = _do_pass(phase, snapshot, by_number)
    except _Halt as stop:
        _halt(phase, stop.member, str(stop))
    except (ValueError, OSError) as exc:
        _halt(phase, None, f"the run could not continue: {exc}")
    # The pass wrote to the card and to git, so what the API shows is read
    # back rather than patched — the same recompute, once more.
    before, fresh = snapshot, _reread(phase)
    if fresh:
        snapshot = _snapshot(fresh, _cards()[0])
    if waiting_on:
        snapshot["waitingOn"] = waiting_on
    SNAPSHOTS[phase["file"]] = snapshot
    if snapshot != before:
        state.broadcast({"type": "board"})   # a pass that did nothing is quiet
    return snapshot


def _reread(phase: dict) -> dict | None:
    """The card as it stands now — the runner writes to it as it goes, and
    may have moved it. None once it has left the board entirely."""
    return _cards()[0].get(phase["file"])


def advance_all() -> dict:
    """One pass over every phase card being run on this board."""
    with _LOCK:
        by_file, by_number = _cards()
        live = {}
        for task in by_file.values():
            if task["isPhase"] and task["stage"] == "in-progress":
                live[task["file"]] = advance(task, by_file, by_number)
        for name in [n for n in SNAPSHOTS if n not in live]:
            SNAPSHOTS.pop(name, None)
        return live


def beat() -> None:
    """The runner: a plain thread, silent when no phase is running."""
    while True:
        time.sleep(config.PHASE_INTERVAL)
        try:
            advance_all()
        except Exception:                # noqa: BLE001 — a beat never dies
            pass


# ── starting one, and stopping it ──────────────────────────────────────


def _phase_card(filename: str, stage: str) -> dict:
    """The phase card an action names, or the reason it is not one."""
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage != "in-progress":
        raise ValueError("a phase runs from in-progress/ — move the card there first")
    if not (config.TASKS / stage / filename).is_file():
        raise ValueError(f"{filename} is not in {stage}/ — refresh the board")
    phase = _cards()[0].get(filename)
    if phase is None or not phase["isPhase"]:
        raise ValueError(f"{filename} is not a phase — a phase card is "
                         f"**Type:** Phase with a ## Cards section")
    return phase


def start_phase(filename: str, stage: str, takeover: bool = False) -> dict:
    """Run a phase — the first time, or again after a halt or a hold.

    Cutting `phase/<stem>` from the newest main is the whole of "starting":
    everything after it is the beat looking at what is there. Running a
    halted phase again is the person's decision that cleared the halt, so
    it appends the line that clears it and takes one pass immediately.
    """
    phase = _phase_card(filename, stage)
    if phase["phaseDrift"]:
        raise ValueError(f"{filename} does not resolve: {phase['phaseDrift'][0]} "
                         f"— fix the list before running it")
    if not phase["members"]:
        # Nothing to run, so nothing is started: no branch is cut and the
        # card stays exactly where its author put it.
        raise ValueError(f"{filename} lists no cards — nothing to run")

    agents.claim_for_launch(filename, stage, takeover)
    with _LOCK:                       # never alongside a pass of the beat
        return _start(phase, filename)


def _start(phase: dict, filename: str) -> dict:
    """Cut the branch (once), record the run, and take the first pass."""
    branch = agents.phase_branch(filename)
    note = ""
    if not _branch_exists(branch):
        # The same rule and the same timeout a task branch is cut by: the
        # newest main this checkout can see, and never a wait on the network.
        point, note = agents._fresh_branch_point()
        worktree = config.WORKTREES / filename[:-3]
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        result = (_git("worktree", "add", "--no-track", "-b", branch,
                       str(worktree), point) if point
                  else _git("worktree", "add", "-b", branch, str(worktree)))
        if result.returncode != 0:
            raise ValueError(f"could not cut {branch}: {result.stderr.strip()[:200]}")
        _push_phase(phase)
    if not _write_log(phase, f"run started on {branch}"):
        raise ValueError(f"could not record the run on {filename} — its phase "
                         f"log must be writable to run the phase safely")
    _say(filename, f"phase {filename} is running on {branch}"
                   + (f" — {note}" if note else ""))
    state.broadcast({"type": "board"})

    by_file, by_number = _cards()         # the log just rewrote the card
    return advance(by_file.get(filename) or phase, by_file, by_number)


def stop_phase(filename: str, stage: str) -> dict:
    """Hold a phase: stop, without unwinding anything.

    `‖ hold` means here what it means on any other card — the work stops
    and nothing is lost. So the line goes into the log (which is what the
    beat reads, so the next pass stands down), and the agent the phase has
    in flight is held exactly as its own card's hold would hold it. The
    phase branch, every member already merged into it, and every worktree
    are left precisely as they were: a hold is not an undo, and the only
    way back to `main` is still the PR at the end.

    A halted phase can be held too, and that is the other half of the
    halt's promise: it holds until the phase is run again *or* stopped,
    and a person who has read the halt and does not want to carry on needs
    a way to say so that is not walking the card backwards.

    A held member's card keeps its own work and its own state, so running
    the phase again may well halt on it — that is the honest reading of a
    run that ended without reaching review/, and it is a person's to settle.
    """
    phase = _phase_card(filename, stage)
    with _LOCK:                       # never alongside a pass of the beat
        where = run_state(log_entries(phase["body"]))
        if where["state"] not in ("running", "halted"):
            raise ValueError(f"{filename} is not running — nothing to hold "
                             f"(its phase log says: {where['state']})")
        who = taskfiles.actor_name() or "you"
        if not _write_log(phase, f"stopped — held by {who}"):
            raise ValueError(f"could not record the hold on {filename} — its "
                             f"phase log must be writable, or the next beat "
                             f"would carry on regardless")
        held = []
        for record in agents.working_on({m["file"] for m in phase["members"]}):
            try:
                agents.stop_agent(record["id"])
            except ValueError:        # it ended between the read and the ask
                continue
            held.append(record["task"])
        _say(filename, f"phase {filename} held by {who}"
                       + (f" — {', '.join(held)} stopped with it" if held else "")
                       + f" — {agents.phase_branch(filename)} is left as it is")
        snapshot = _snapshot(_reread(phase) or phase, _cards()[0])
        SNAPSHOTS[filename] = snapshot
    state.broadcast({"type": "board"})
    return snapshot


# ── and not moving it while it works ───────────────────────────────────


def _member_name(member: dict) -> str:
    """`31 — Stand up site/` — the same line the phase's own list would
    carry, built by the same helper, so the refusal names the member the
    way the card that holds it does."""
    if not member.get("number"):
        return member["file"]
    return taskfiles.member_entry(member["number"], member["title"])


def _joined(parts: list[str]) -> str:
    return (" and ".join([", ".join(parts[:-1]), parts[-1]])
            if len(parts) > 1 else (parts[0] if parts else ""))


def assert_not_working(filename: str, doing: str = "move the card") -> None:
    """A phase card does not move while one of its members has an agent in
    it. Refuse, and say the whole thing.

    The phase card stands for its members (they are not drawn on the Board
    at all), so moving it — to another stage, or out to `tasks/archive/`,
    which is a move like any other — while a member is mid-run is a move
    nobody can mean: the card lands somewhere its branch, its worktree and
    its live agent are not. The one action that settles it already exists
    and does exactly the right thing, so the refusal names it rather than
    just saying no: `‖ hold` stops the run and the agent it has in flight
    and unwinds nothing.

    What is refused is narrow on purpose. A phase *between* members has
    nothing to lose and moves freely, and so does a halted one — by
    construction nothing is running there, which is precisely when walking
    the card back is the thing to do. And "running" is read from the
    processes themselves (`agents.working_on`), never from what the log
    says was once started, so a member's run that died between two beats
    cannot lock its phase card for the life of the board.

    An ordinary card never reaches the question: the one file is read, it
    is not a phase, and the move goes through untouched.

    It reads and never writes, so it does not take `_LOCK` — a pass of the
    beat can spend minutes in a merge, and a move that waited on one would
    be a worse answer than the sliver it closes. A launch landing between
    this read and the rename is the same state a `mv` produces, and the
    runner already knows how to read it: a member walked out from under
    the phase halts it.
    """
    stage = taskfiles.find_stage_of(filename)
    if stage is None:
        return
    card = taskfiles.read_task(config.TASKS / stage / filename, stage)
    if not card["isPhase"] or not card["cards"]:
        return
    # only now the whole board, to resolve the listed numbers to cards
    phase = _cards()[0].get(filename)
    if phase is None:
        return
    members = {member["file"]: member for member in phase["members"]}
    working = {record["task"]: record
               for record in agents.working_on(set(members))}
    if not working:
        return
    parts = [f"{working[file].get('name') or 'an agent'} is on "
             f"{_member_name(member)}"
             for file, member in members.items() if file in working]
    # the phase by name, as the header chip says it — the number it opens
    # with would be the third one in a sentence that already has two
    name = taskfiles.LEADING_NUMBER_RE.sub("", phase["title"]).strip()
    raise ValueError(
        f"{name or phase['title']} is still working — {_joined(parts)}. ‖ hold stops "
        f"the phase and the agent it has in flight, and leaves the phase "
        f"branch, everything merged into it and every worktree exactly as "
        f"they are. Hold it first, then {doing}.")
