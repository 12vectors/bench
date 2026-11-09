"""Reading, moving and writing task files — the only module that touches tasks/.

The directory a task file sits in *is* its status (see ../AGENTS.md). Nothing
here knows about agents or HTTP; it is the same folder kanban you could drive
by hand with mv.

Every write goes out through one of two doors — `_relocate` for anything
that changes which directory a card sits in, `append_to_task`/`commit_edit`
for anything written into it where it stands — and both commit under the
`COMMIT_MOVES` gate. That is deliberate: committing is a property of
writing to a task file, not something each caller has to remember.

And they commit to one repository: `config.state_repo()`, the work-state
repository. Vendored that is the application's own, because tasks/ lives
inside it; externally it is the repository holding the work root, and a
card moving columns must never produce a bookkeeping commit on the
application's base branch (task 56).

And they write *under tasks/* — not through a link out of it. A card, or
the stage directory it was dropped into, can point anywhere on disk, and
each of these doors would otherwise edit whatever it points at. Both are
refused here, per write, because `config.problems()` answers for the
layout at startup and a ticket arrives whenever someone writes one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import config
import state

# Every read-modify-write of a card's text in this module takes this lock,
# so two of the board's own writers — a comment being saved and a report
# being appended, say — cannot read the same text and each write back a
# version without the other's line in it. It cannot hold out a `git pull`
# or a person's editor, which is why the writers that matter most (a brief
# being replaced) also compare the file to what they read immediately
# before replacing it.
WRITE_LOCK = threading.RLock()

TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
PRIORITY_RE = re.compile(r"^\*\*Priority:\*\*\s*(.+?)\s*$", re.MULTILINE)
TYPE_RE = re.compile(r"^\*\*Type:\*\*\s*(.+?)\s*$", re.MULTILINE)
ASSIGNEE_RE = re.compile(r"^\*\*Assignee:\*\*\s*(.+?)\s*$", re.MULTILINE)
ASSIGNEE_LINE_RE = re.compile(r"^\*\*Assignee:\*\*[^\n]*\n?", re.MULTILINE)
PR_RE = re.compile(r"^\*\*PR:\*\*\s*(\S+)\s*$", re.MULTILINE)
PR_VERDICT_RE = re.compile(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", re.MULTILINE)
DEPENDS_RE = re.compile(r"^\*\*Depends on:\*\*\s*(.+?)\s*$", re.MULTILINE)
BASE_BRANCH_RE = re.compile(r"^\*\*Base branch:\*\*\s*(.+?)\s*$", re.MULTILINE)
PR_BASE_RE = re.compile(r"^\*\*PR base:\*\*\s*(.+?)\s*$", re.MULTILINE)
NUMBER_RE = re.compile(r"^(\d+)[-_]")

# A branch name as a card may write one. Deliberately narrower than
# `git check-ref-format`: this value selects what a launch branches from
# and what a PR targets, so it is read as data and never as a shell
# fragment — and a card that tried to smuggle one in is refused by the
# parser rather than by the process that would have run it.
REF_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

# A phase is a card that lists its cards: `**Type:** Phase` plus a `## Cards`
# section naming its members, one per line, in the order they run.
PHASE_TYPE = "phase"
CARDS_SECTION_RE = re.compile(r"^##\s+Cards\s*$(.*?)(?=^##\s|\Z)",
                              re.MULTILINE | re.DOTALL)
CARD_ITEM_RE = re.compile(r"^(?:[-*+]\s+)?#?0*(\d+)\b")
DEPENDS_ITEM_RE = re.compile(r"#?\s*0*(\d+)")
# titles are written `51 — Add a card to a phase`, and a member line names
# the number itself, so the title's own copy of it is dropped — the same
# cut the chip's label makes
LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[—–-]\s*")

STAGE_ORDER = {slug: index for index, (slug, _) in enumerate(config.STAGES)}
CLAIM_FROM = {"backlog", "to-do"}   # the unstarted stages: leaving one claims


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _split_reason(value: str | None) -> tuple[str | None, str | None]:
    """`High — because …` → (`High`, `because …`)."""
    if not value:
        return None, None
    parts = re.split(r"\s+[—–-]\s+", value, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else None)


def canonical_number(number: str) -> str:
    """`07`, `7` and `#007` are one card. Numbers are written by hand in
    prose (a `## Cards` line, a `Depends on:` header) and read against
    numbers taken from filenames, so both ends canonicalise the same way."""
    return str(int(number))


def _depends_on(text: str) -> list[str]:
    """The `**Depends on:**` header, parsed at last — into the task numbers
    it names, and nothing else. The line may also carry external
    preconditions in prose ("the API key"); those are for the reader, so
    only comma-separated items that are entirely a number are taken.
    """
    line = _first(DEPENDS_RE, text)
    if not line:
        return []
    numbers = []
    for part in line.split(","):
        match = DEPENDS_ITEM_RE.fullmatch(part.strip())
        if match:
            numbers.append(canonical_number(match.group(1)))
    return numbers


def ref_problem(value: str) -> str | None:
    """Why this is not a branch name a card may name, or None if it is.

    The two execution headers are the only place a person types a git ref
    into a task file, and both of them end up in an argument list. So the
    syntax is checked where the file is read — once, before anything has
    been created or fetched — and the reason is the reader's, not git's.
    """
    if not value:
        return "names no branch"
    if len(value) > 200:
        return "is too long to be a branch name"
    if not REF_CHARS_RE.match(value):
        return ("may only use letters, digits and . _ / - "
                "(it is a branch name, not a command)")
    if value.startswith("-") or value.startswith("/") or value.endswith("/"):
        return "is not a usable branch name (leading '-' or '/', or a trailing '/')"
    if value.endswith(".lock") or value.endswith("."):
        return "is not a usable branch name (git refuses a trailing '.' or '.lock')"
    if ".." in value or "//" in value or "@{" in value:
        return "is not a usable branch name (it contains '..', '//' or '@{')"
    if any(part in ("", ".") for part in value.split("/")):
        return "is not a usable branch name (an empty or '.' path segment)"
    return None


def _ref_header(pattern: re.Pattern[str], label: str, text: str,
                drift: list[str]) -> str | None:
    """One execution header, read and validated. A value that is not a
    branch name is dropped *and* flagged: the card wears the mistake rather
    than the board quietly reverting to its default, because reverting is
    exactly how card 58's wrong-base launch happened in the first place.

    Like **Priority** and **Depends on**, the value may carry a note after
    ` — `; a branch name holds no whitespace, so the split cannot cut one."""
    value, _note = _split_reason(_first(pattern, text))
    if value is None:
        return None
    value = value.strip().strip("`")
    problem = ref_problem(value)
    if problem:
        drift.append(f"**{label}:** {problem}")
        return None
    return value


def _listed_cards(text: str) -> tuple[list[str], list[str]]:
    """A phase card's `## Cards` section: the numbers it lists in document
    order (which is run order), and the unindented lines that name none.

    The number is what is parsed; whatever follows it is for the reader and
    is never matched against anything. Indented lines are a member's own
    continuation, so they are neither members nor mistakes.
    """
    section = CARDS_SECTION_RE.search(text)
    if not section:
        return [], []
    numbers, unreadable = [], []
    for line in section.group(1).splitlines():
        if not line.strip() or line[:1].isspace():
            continue
        match = CARD_ITEM_RE.match(line.strip())
        if match:
            numbers.append(canonical_number(match.group(1)))
        else:
            unreadable.append(line.strip())
    return numbers, unreadable


def read_task(path: Path, stage: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    priority, priority_note = _split_reason(_first(PRIORITY_RE, text))
    number_match = NUMBER_RE.match(path.name)
    declared = _first(STATUS_RE, text)
    # the latest appended `PR REVIEW:` marker wins — reviews accumulate
    verdicts = PR_VERDICT_RE.findall(text)
    kind = _split_reason(_first(TYPE_RE, text))[0]
    is_phase = (kind or "").lower() == PHASE_TYPE
    # a `## Cards` section means membership on a phase card and nothing at
    # all anywhere else — one direction, one authority
    listed, unreadable = _listed_cards(text) if is_phase else ([], [])
    # The two execution headers, validated as they are read. What they
    # *resolve to* is plans.py's question; this is only "did the author
    # write a branch name", answered before any git command exists to run.
    plan_drift: list[str] = []
    base_branch = _ref_header(BASE_BRANCH_RE, "Base branch", text, plan_drift)
    pr_base = _ref_header(PR_BASE_RE, "PR base", text, plan_drift)
    return {
        "pr": _first(PR_RE, text),
        # who holds the card — written by the board when a move claims it
        "assignee": _first(ASSIGNEE_RE, text),
        "prVerdict": {"APPROVE": "green", "REQUEST CHANGES": "red"}.get(
            verdicts[-1] if verdicts else None),
        "file": path.name,
        "stage": stage,
        "number": number_match.group(1) if number_match else None,
        "title": _first(TITLE_RE, text) or path.stem,
        "priority": priority,
        "priorityNote": priority_note,
        "type": kind,
        # What runs next is the phase's list; what may run is this line —
        # parsed here, acted on nowhere yet.
        "dependsOn": _depends_on(text),
        # Explicit execution metadata: which application branch this card's
        # work starts from, and which branch its PR targets. `None` on both
        # is the established default — the application base — and that is
        # what keeps a card written before any of this existed unchanged.
        "baseBranch": base_branch,
        "prBase": pr_base,
        # Why the headers do not add up, in the same spirit as `status
        # drift` and `phase drift`: flagged on the card, never guessed
        # around. `plans.preflight` adds what only git can answer.
        "planDrift": plan_drift,
        # A phase and its members: `cards` is what this card claims (empty
        # for everything that is not a phase), `members`, `phase` and the
        # drift between them are derived across the whole board by
        # `collect` — a member card says nothing about its phase.
        "isPhase": is_phase,
        "cards": listed,
        "members": [],
        "phase": None,
        "phaseDrift": [f'"{line}" names no card number' for line in unreadable],
        # Flagged in the UI when the file's own Status line contradicts the
        # directory it is in — the board should never quietly paper over that.
        "declaredStatus": declared,
        "statusMismatch": bool(declared)
        and declared.lower() != config.STAGE_LABELS[stage].lower(),
        "mtime": path.stat().st_mtime,
        "words": len(text.split()),
        "body": text,
    }


def _phase_name(phase: dict) -> str:
    return f"{phase['number'] or phase['file']} — {phase['title']}"


def weave_phases(stages: list[dict]) -> None:
    """Resolve every phase card's list against the board it sits on.

    Membership is derived, never stored twice: the phase card lists its
    members and this is where a member learns which phase holds it and
    where in the run it sits. What cannot be resolved is *flagged* rather
    than skipped — a number no card has, a card two phases both claim, a
    card one phase lists twice — because each is an authoring mistake that
    would otherwise surface much later as a runner behaving oddly.
    """
    tasks = [task for stage in stages for task in stage["tasks"]]
    by_number: dict[str, dict] = {}
    for task in tasks:
        if task["number"]:
            by_number.setdefault(canonical_number(task["number"]), task)

    held: dict[str, dict] = {}   # card number → the phase that already lists it
    phases = sorted((t for t in tasks if t["isPhase"]),
                    key=lambda t: (int(t["number"]) if t["number"] else 9999, t["file"]))
    for phase in phases:
        members, seen = [], set()
        for number in phase["cards"]:
            member = by_number.get(number)
            if member is None:
                phase["phaseDrift"].append(f"{number} is listed here but no card has that number")
                continue
            if number in seen:
                phase["phaseDrift"].append(f"{number} is listed twice by this phase")
                continue
            seen.add(number)
            if member["isPhase"]:
                phase["phaseDrift"].append(f"{number} is itself a phase — phases do not nest")
                continue
            owner = held.get(number)
            if owner is not None:
                # both phase cards wear it: from either one, the reader can
                # see the collision without hunting for the other list
                note = (f"{number} is listed by two phases — "
                        f"{_phase_name(owner)} and {_phase_name(phase)}")
                owner["phaseDrift"].append(note)
                phase["phaseDrift"].append(note)
                member["phaseDrift"].append(note)
                continue
            held[number] = phase
            # A phase member inherits the phase's branch and targets it —
            # that is the whole reason the phase has one. A card that also
            # names its own base is two instructions for one decision, so
            # it is rejected rather than silently resolved either way.
            for label, value in (("Base branch", member["baseBranch"]),
                                 ("PR base", member["prBase"])):
                if value:
                    note = (f"**{label}:** {value} conflicts with membership of "
                            f"phase {_phase_name(phase)} — a member inherits the "
                            f"phase branch and targets it; drop the header or the "
                            f"card from the phase")
                    member["planDrift"].append(note)
                    phase["planDrift"].append(note)
            members.append(member)
        phase["members"] = [{"number": m["number"], "file": m["file"],
                             "title": m["title"], "stage": m["stage"]} for m in members]
        for index, member in enumerate(members, start=1):
            member["phase"] = {"file": phase["file"], "number": phase["number"],
                               "title": phase["title"],
                               "index": index, "total": len(members)}


def collect() -> dict:
    stages = []
    for slug, label in config.STAGES:
        directory = config.TASKS / slug
        tasks = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                tasks.append(read_task(path, slug))
        tasks.sort(key=lambda t: (int(t["number"]) if t["number"] else 9999, t["file"]))
        stages.append({"slug": slug, "label": label, "tasks": tasks})
    weave_phases(stages)

    extras = {}
    for slug in ("plans", "reference"):
        # beside tasks/, wherever the work state lives — not in the tool
        directory = config.work_root() / slug
        extras[slug] = (
            sorted(p.name for p in directory.iterdir() if not p.name.startswith("."))
            if directory.is_dir()
            else []
        )
    return {"stages": stages, "extras": extras, "root": str(config.TASKS)}


def find_stage_of(filename: str) -> str | None:
    for slug in config.STAGE_DIRS:
        if (config.TASKS / slug / filename).is_file():
            return slug
    return None


def _escape(path: Path) -> str | None:
    """Why this is not a card bench will write to, or None.

    A file in a stage directory can be a symlink out of tasks/, and every
    door in this module writes *through* one: `_relocate` rewrites the
    Status line before it renames, `append_to_task` opens the target in
    append mode, a claim rewrites the header. A ticket pointing at the
    application's README is a board-made edit landing in the
    application's own files — exactly what `config.problems()` refuses a
    layout for, asked again per file because a card (or the stage
    directory it is dropped into) can appear long after the board started.
    """
    real = config.escapes_tasks(path)
    if real is None:
        return None
    return (f"{path.name} in {path.parent.name}/ is a link to {real}, "
            f"outside {config.TASKS} — bench does not write a card through "
            f"a link out of the board's own state")


def _refuse_escape(*paths: Path) -> None:
    """The raising flavour, for the doors a caller already handles a
    `ValueError` from — the move, the archive, the claim."""
    for path in paths:
        reason = _escape(path)
        if reason:
            raise ValueError(reason)


def _writable(filename: str, stage: str) -> Path | None:
    """The narrating flavour: the card's path, or None with the refusal in
    the ticker. The append doors answer *whether* they wrote rather than
    raising, and a report that cannot be recorded is narrated like any
    other write that did not happen."""
    path = config.TASKS / stage / filename
    reason = _escape(path)
    if reason is None:
        return path
    state.record_board_event({"kind": "agent", "actor": "board",
                              "file": filename, "summary": reason})
    return None


ARCHIVE_FROM = {"backlog", "to-do", "done"}


def archive_task(filename: str, source: str, actor: str = "you") -> dict:
    """Archive: out of the flow but never deleted. tasks/archive/ is not a
    stage — archived cards simply leave the board.

    It is still a board-made write to a task file, so it goes out through
    `_relocate` like every other one: attributed, and committed under the
    same gate a move is (an uncommitted deletion of a tracked file is
    precisely what stops sync publishing anything else).
    """
    if source not in ARCHIVE_FROM:
        raise ValueError("archive takes cards from backlog, to-do or done only")
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    src = config.TASKS / source / filename
    dst = config.TASKS / "archive" / filename
    if not src.is_file():
        raise ValueError(f"{filename} is no longer in {source}/ — refresh the board")
    if dst.exists():
        raise ValueError(f"{filename} already exists in archive/")
    text = src.read_text(encoding="utf-8")
    if STATUS_RE.search(text):
        text = STATUS_RE.sub("**Status:** Archived", text, count=1)
    _relocate(filename, src, dst, text, "archived", actor)
    return {"file": filename, "from": source}


def unarchive_task(filename: str, target: str, actor: str = "you") -> dict:
    """⌘Z: bring the last archived card back where it came from — and
    record the way back in git, exactly as the way out was."""
    if target not in ARCHIVE_FROM:
        raise ValueError("unknown stage to restore into")
    src = config.TASKS / "archive" / filename
    dst = config.TASKS / target / filename
    if not src.is_file():
        raise ValueError(f"{filename} is not in archive/")
    if dst.exists():
        raise ValueError(f"{filename} already exists in {target}/")
    text = src.read_text(encoding="utf-8")
    if STATUS_RE.search(text):
        text = STATUS_RE.sub(f"**Status:** {config.STAGE_LABELS[target]}", text, count=1)
    _relocate(filename, src, dst, text, target, actor)
    return {"file": filename, "to": target}


def archived_count() -> int:
    directory = config.TASKS / "archive"
    return len(list(directory.glob("*.md"))) if directory.is_dir() else 0


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """git in the **work-state** repository — never the application's.

    No work-state repository is not an error to raise: it is a failed run
    like any other, so every caller's existing narration covers it (and
    `_commit` says the useful version of it)."""
    root = config.state_repo()
    if root is None:
        return subprocess.CompletedProcess(args, 128, "",
                                           "no work-state repository")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=timeout)


def actor_name() -> str:
    """Who this board is: `git config user.name` in the work-state
    repository, which is the history the board's own commits appear in.
    Empty when git has no name — then nothing is claimed."""
    try:
        result = _git("config", "user.name", timeout=10)
    except (subprocess.SubprocessError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def claims(source: str, target: str) -> bool:
    """Claiming is moving: taking a card out of one of the unstarted stages
    towards work is the commitment, and the commitment names its owner."""
    return source in CLAIM_FROM and STAGE_ORDER[target] > STAGE_ORDER[source]


def _set_assignee(text: str, name: str) -> str:
    """First claim only — an existing assignee is never overwritten. The line
    joins the other header fields, right under Status."""
    if ASSIGNEE_RE.search(text):
        return text
    if STATUS_RE.search(text):
        return STATUS_RE.sub(lambda m: f"{m.group(0)}\n**Assignee:** {name}", text, count=1)
    return TITLE_RE.sub(lambda m: f"{m.group(0)}\n\n**Assignee:** {name}", text, count=1)


def set_assignee(filename: str, stage: str, name: str) -> None:
    """Write who holds a card where it stands, replacing whoever held it.

    A move's claim never overwrites — the first claim sticks. This is the
    other door: a launch claiming an unheld card, or the deliberate
    takeover of someone else's. It commits like every other board edit, so
    the new owner travels to the other boards.
    """
    path = config.TASKS / stage / filename
    _refuse_escape(path)
    with WRITE_LOCK:
        text = path.read_text(encoding="utf-8")
        updated = (ASSIGNEE_RE.sub(f"**Assignee:** {name}", text, count=1)
                   if ASSIGNEE_RE.search(text) else _set_assignee(text, name))
        if updated == text:
            return
        path.write_text(updated, encoding="utf-8")
    commit_edit(filename, stage, f"claimed by {name}")


def _commit(filename: str, message: str, spec: list[str], failure: str) -> bool:
    """One commit touching only this task file's paths.

    Staging is scoped to those paths (`git add` then a pathspec commit), so
    a developer's unrelated staged changes are neither committed nor
    unstaged. Hooks are skipped: this is the board's bookkeeping, not a code
    change. Anything going wrong is narrated — what the commit records has
    already happened on disk, which is the source of truth.

    No work-state repository at all is narrated as the configuration fact
    it is rather than as a git failure. `config.problems()` refuses this
    layout at startup, so reaching here means something bypassed it — and
    the one thing that must not happen is committing to the application
    instead.
    """
    if config.state_repo() is None:
        state.record_board_event({
            "kind": "agent", "actor": "board", "file": filename,
            "summary": (f"{filename}: nothing was committed — the work root "
                        f"{config.work_root()} is in no work-state "
                        f"repository, and bench does not commit the board's "
                        f"bookkeeping to the application instead")})
        return False
    try:
        result = _git("add", "-A", "--", *spec)
        if result.returncode == 0:
            result = _git("commit", "--no-verify", "-m", message, "--", *spec, timeout=60)
            if result.returncode == 0:
                state.task_committed(filename)   # sync (when on) publishes it
                return True
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1] if lines else f"git exited {result.returncode}"
    except (subprocess.SubprocessError, OSError) as exc:
        detail = str(exc)
    state.record_board_event({
        "kind": "agent", "actor": "board", "file": filename,
        "summary": f"{failure}: {detail[:140]}"})
    return False


def _number(filename: str) -> str | None:
    match = NUMBER_RE.match(filename)
    return match.group(1) if match else None


def _move_spec(src: Path, dst: Path) -> list[str]:
    """The paths one move's commit names.

    Both ends, so git records a rename rather than a delete and an add —
    and a card git has never seen (a brand-new backlog file) names only its
    destination, since a pathspec matching nothing in HEAD would fail the
    commit outright.
    """
    spec = [str(dst)]
    try:
        tracked = _git("ls-files", "--", str(src))
        if tracked.returncode == 0 and tracked.stdout.strip():
            spec.insert(0, str(src))   # git knew the old path: record its removal
    except (subprocess.SubprocessError, OSError):
        pass
    return spec


def _commit_move(filename: str, target: str, src: Path, dst: Path, who: str,
                 number: str | None) -> None:
    """The move and the claim in one commit."""
    _commit(filename, f"board: {number or filename[:-3]} → {target} ({who or 'board'})",
            _move_spec(src, dst), f"{filename} moved, but committing it failed")


def _relocate(filename: str, src: Path, dst: Path, text: str, target: str,
              actor: str, who: str | None = None, commit: bool = True,
              quiet: bool = False) -> None:
    """The one door out of a directory under tasks/: register who is doing
    it, write the file, move it, commit it.

    Every mover in this module goes through here, so committing is a
    property of *writing to a task file* rather than something each caller
    remembers — the way archiving forgot it. `target` is what the ticker
    and the commit message call the destination (a stage slug, or
    `archived`), and `who` is this checkout's git name when the caller has
    already paid for it.

    `commit` and `quiet` are what a batch of moves turns off: several cards
    that moved as one thing are committed as one thing and narrated as one
    thing (`move_together`), and both would be wrong done per file.

    Being the one door, it is also where a move is refused for leaving
    tasks/ — both ends, before anything is registered or written, so a
    linked card and a linked destination are the same answer.
    """
    _refuse_escape(src, dst)
    state.expect_move(filename, target, actor, quiet=quiet)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
    if config.COMMIT_MOVES and commit:
        _commit_move(filename, target, src, dst,
                     actor_name() if who is None else who, _number(filename))


def commit_edit(filename: str, stage: str, what: str) -> bool:
    """Commit a board-made edit to a card in place — the `**PR:**` line and
    anything else the board writes into a file it does not move.

    Team mode's own bookkeeping, so it carries the `board: ` prefix sync's
    piggyback guard looks for, and it fires the same commit hook a move
    does. With `BOARD_COMMIT_MOVES` off it does nothing at all: the edit
    stays in the working tree for a human to commit, exactly as before.
    """
    if not config.COMMIT_MOVES:
        return False
    path = config.TASKS / stage / filename
    return _commit(filename,
                   f"board: {_number(filename) or filename[:-3]} "
                   f"{what} ({actor_name() or 'board'})",
                   [str(path)],
                   f"{filename}: {what} recorded, but committing it failed")


def append_to_task(filename: str, stage: str, text: str, what: str) -> bool:
    """Append to a card where it stands, and commit the write.

    The other half of the same law: an agent's closing report is the
    permanent record the project keeps on purpose, so it reaches git like
    the `**PR:**` line does instead of sitting modified in one working
    tree. Returns whether the text was written (the commit is the gate's
    business, and is narrated if it fails).
    """
    path = _writable(filename, stage)
    if path is None:
        return False
    try:
        with WRITE_LOCK, path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        return False
    commit_edit(filename, stage, what)
    return True


def append_to_section(filename: str, stage: str, heading: str, line: str,
                      what: str) -> bool:
    """Add one line under `## <heading>`, creating the section at the end of
    the card when it is not there yet.

    The third door, and the narrowest: a running record where every entry is
    one line and belongs under one heading — the phase log. `append_to_task`
    would scatter those lines through the file as other sections (a work
    report, a review) landed between them, and the record would stop being
    readable in the one place a person looks. It commits like every other
    board-made write.
    """
    path = _writable(filename, stage)
    if path is None:
        return False
    with WRITE_LOCK:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        section = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)",
                             re.MULTILINE | re.DOTALL).search(text)
        if section:
            body = section.group(1).strip("\n")
            updated = f"## {heading}\n\n{body}\n{line}\n\n" if body else \
                      f"## {heading}\n\n{line}\n\n"
            text = text[:section.start()] + updated + text[section.end():]
        else:
            text = text.rstrip("\n") + f"\n\n## {heading}\n\n{line}\n"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            return False
    commit_edit(filename, stage, what)
    return True


PHASE_JOIN_FROM = {"backlog", "to-do"}   # a card joins a phase before it starts
PHASE_HOST = "to-do"                     # and only a phase still waiting accepts it


def member_entry(number: str, title: str) -> str:
    """`33 — The landing page` — the way a person writes it.

    The section is authored by hand and read by hand, so a line the board
    adds has to be one you would have typed. Nothing parses what follows
    the number, which is exactly why it must stay readable. The bullet is
    left off here so the ticker can say the same thing in prose.
    """
    rest = LEADING_NUMBER_RE.sub("", title).strip()
    return f"{number} — {rest}" if rest else number


def _phase_holding(number: str) -> dict | None:
    """The phase card that already lists this card, read off the disk.

    Membership runs one direction only, so the answer is only ever found by
    looking at every phase card — and it is looked for here rather than
    taken from what a tab rendered, because the file may have gained the
    card since.
    """
    for slug in config.STAGE_DIRS:
        directory = config.TASKS / slug
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            task = read_task(path, slug)
            if task["isPhase"] and number in task["cards"]:
                return task
    return None


def add_to_phase(filename: str, stage: str, phase_file: str) -> dict:
    """Append a card to the end of a phase's `## Cards` section.

    The convenience path for the card you decide belongs after all: one
    line into the phase card, and nothing at all into the card being added
    — membership lives in one place and this does not move it. It goes out
    through `append_to_section` like the phase log does, so it commits
    under the same gate every other board-made write to a task file does;
    an addition that never left one working tree is not an addition the
    phase would run.

    Only a phase in `to-do/` takes cards. One in `in-progress/` is
    running: its branch exists and its members are being worked in the
    order the list had when it started, so appending mid-flight is a
    different feature with different questions.
    """
    if stage not in PHASE_JOIN_FROM:
        raise ValueError("a card joins a phase from backlog/ or to-do/ only")
    for name in (filename, phase_file):
        if Path(name).name != name or not name.endswith(".md"):
            raise ValueError("bad filename")

    card_path = config.TASKS / stage / filename
    phase_path = config.TASKS / PHASE_HOST / phase_file
    if not card_path.is_file():
        raise ValueError(f"{filename} is no longer in {stage}/ — refresh the board")
    if not phase_path.is_file():
        raise ValueError(f"{phase_file} is no longer in {PHASE_HOST}/ — a phase takes "
                         "cards while it waits, not while it runs")

    card = read_task(card_path, stage)
    phase = read_task(phase_path, PHASE_HOST)
    if not phase["isPhase"]:
        raise ValueError(f"{phase_file} is not a phase card")
    if card["isPhase"]:
        raise ValueError(f"{filename} is a phase — phases do not nest")
    if not card["number"]:
        raise ValueError(f"{filename} has no number, and a phase lists its cards by number")

    number = canonical_number(card["number"])
    holder = _phase_holding(number)
    if holder is not None:
        where = ("this phase already" if holder["file"] == phase_file
                 else f"phase {_phase_name(holder)} already")
        raise ValueError(f"{where} lists {card['number']}")

    entry = member_entry(card["number"], card["title"])
    if not append_to_section(phase_file, PHASE_HOST, "Cards", f"- {entry}",
                             f"gained {number}"):
        raise ValueError(f"{phase_file} could not be written")
    return {"file": filename, "number": card["number"], "title": card["title"],
            "phase": phase_file, "phaseName": _phase_name(phase),
            "entry": entry, "line": f"- {entry}"}


def move_task(filename: str, source: str, target: str, actor: str = "you",
              commit: bool = True, quiet: bool = False) -> dict:
    """Move a task file between stage directories and fix its Status line.

    With `BOARD_COMMIT_MOVES` on, the move also claims the card (an
    **Assignee:** line, this checkout's git name) or releases it when the
    card is walked back to backlog, and commits the whole change.

    `commit` and `quiet` belong to `move_together` below — a lone move
    commits itself and narrates itself, as it always has.
    """
    if source not in config.STAGE_DIRS or target not in config.STAGE_DIRS:
        raise ValueError("unknown stage")
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")

    src = config.TASKS / source / filename
    dst = config.TASKS / target / filename
    if not src.is_file():
        raise ValueError(f"{filename} is no longer in {source}/ — refresh the board")
    if dst.exists():
        raise ValueError(f"{filename} already exists in {target}/")

    text = src.read_text(encoding="utf-8")
    label = config.STAGE_LABELS[target]
    if STATUS_RE.search(text):
        text = STATUS_RE.sub(f"**Status:** {label}", text, count=1)
    else:  # no Status line to keep in step — insert one under the title
        text = TITLE_RE.sub(lambda m: f"{m.group(0)}\n\n**Status:** {label}", text, count=1)

    name = ""
    if config.COMMIT_MOVES:
        name = actor_name()
        if target == "backlog":       # walked all the way back: unclaimed again
            text = ASSIGNEE_LINE_RE.sub("", text, count=1)
        elif name and claims(source, target):
            text = _set_assignee(text, name)

    _relocate(filename, src, dst, text, target, actor, who=name,
              commit=commit, quiet=quiet)
    return read_task(dst, target)


def move_together(moves: list[tuple[str, str]], target: str, note: str,
                  actor: str = "you") -> list[dict]:
    """Move several cards into one stage as one thing.

    A phase's ending is one event that happens to move five files, so the
    record it leaves should be one too: one commit naming every card that
    moved, rather than five `board: NN → done` lines in a row that a
    reader of `git log` has to reassemble into the thing that happened.
    The message still says what moved — the numbers are in it — and it
    carries the `board: ` prefix, so in team mode it reaches the other
    boards on the same push every other move rides.

    The moves are quiet for the same reason (see `state.expect_move`): the
    caller narrates the ending, and the ticker does not also scroll the
    parts. Each takes `moves` as `(filename, source stage)`.

    A card that cannot be moved — gone, or already in `target` — is
    skipped rather than failing the rest. This runs after work that has
    already landed in `main`, and finishing four cards beats abandoning
    the sweep over the fifth.
    """
    moved, spec = [], []
    for filename, source in moves:
        src = config.TASKS / source / filename
        dst = config.TASKS / target / filename
        try:
            moved.append(move_task(filename, source, target, actor=actor,
                                   commit=False, quiet=True))
        except (ValueError, OSError):
            continue
        spec += _move_spec(src, dst)
    if moved and config.COMMIT_MOVES:
        numbers = ", ".join(task["number"] or task["file"][:-3] for task in moved)
        _commit(moved[0]["file"],
                f"board: {numbers} → {target}" + (f" {note}" if note else "")
                + f" ({actor_name() or 'board'})",
                spec, f"{len(moved)} cards moved, but committing them failed")
    return moved


# ── the brief, its history, and the discussion beside it (task 65) ─────
#
# A card is three things in one file, and refining one only works if the
# three can be told apart by reading it:
#
#   head     the title and the `**Field:**` lines under it — the card's
#            identity and its lifecycle/publication metadata. Bench and
#            people write these; a refinement never does.
#   brief    everything after the head up to the first history boundary:
#            the description, scope, acceptance and notes. The active
#            requirement, and the only part a refinement may propose to
#            change.
#   history  the first history boundary onwards: every report the board
#            appended (`## <kind> — <date> (<name>)`, with the `---` rule
#            above it), a phase log, the discussion, and the acceptance
#            records card 81 writes. Kept verbatim by every write below.
#
# The discussion is one section, `## Discussion`, created at the end of the
# card the first time anything is said and appended to in place thereafter.
# Each entry is an `### ` heading, one machine-readable marker line, and
# the text itself as a blockquote. Quoting is the safety: a comment that
# says `**Status:** Done`, `PR REVIEW: APPROVE` or `## Cards` is a line
# starting with `>`, so no header, marker or section reader in this module
# — or the board's own regexes, which read the first match in the file —
# can mistake it for the real thing. The persistence contract is versioned
# (`v` in every marker) and documented in AGENTS.md, "Refining a card".

DISCUSSION_HEADING = "Discussion"
DISCUSSION_VERSION = 1
DISCUSSION_NOTE = ("<!-- bench:discussion v1 — written by the board. Entries are "
                   "history: the brief above is the requirement. -->")
HEADER_LINE_RE = re.compile(r"^\*\*[A-Za-z][A-Za-z ]{0,40}:\*\*")
PROTECTED_FIELD_RE = re.compile(
    r"^\s*\*\*(?:Status|Priority|Type|Assignee|Depends on|Base branch|PR base|PR):\*\*",
    re.MULTILINE | re.IGNORECASE)
TITLE_LINE_RE = re.compile(r"^#\s")
# A report the board appended: `## Work report — 2026-11-08 15:20 (Juno)`.
STAMPED_HEADING_RE = re.compile(r"^##\s+.+\s[—–-]\s\d{4}-\d{2}-\d{2}\b")
HISTORY_HEADING_RE = re.compile(
    r"^##\s+(?:Discussion|Phase log|Acceptance record)\s*$")
DISCUSSION_SECTION_RE = re.compile(r"^##\s+Discussion[ \t]*$.*?(?=^##\s|\Z)",
                                   re.MULTILINE | re.DOTALL)
ENTRY_MARK_RE = re.compile(r"^<!-- bench:entry (\{.*\}) -->[ \t]*$", re.MULTILINE)
ACCEPTANCE_RECORD_RE = re.compile(r"^<!-- bench:acceptance (\{.*\}) -->[ \t]*$",
                                  re.MULTILINE)
BENCH_MARK_RE = re.compile(r"<!--\s*bench:", re.IGNORECASE)
MACHINE_MARKER_RE = re.compile(
    r"^\s*(?:PR REVIEW|NOT READY|RELEVANCE REVIEW|REFINEMENT (?:PROPOSAL|DECLINED))\b",
    re.MULTILINE)
# A stable acceptance-criterion identity, written on the criterion's own
# line: `- [ ] Saving a comment invokes no agent <!-- ac:no-agent -->`.
CRITERION_ID_RE = re.compile(r"<!--\s*ac:([a-z0-9][a-z0-9-]{0,40})\s*-->")
CRITERION_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(.*)$")

MAX_COMMENT_CHARS = 8000
MAX_BRIEF_BYTES = 64 * 1024


class StaleCard(ValueError):
    """The card is not the one a proposal was made against any more."""


def _starts_history(line: str) -> bool:
    return bool(HISTORY_HEADING_RE.match(line) or STAMPED_HEADING_RE.match(line))


def _is_fence(line: str) -> bool:
    """A ``` line opens or closes a fenced block — the one rule both
    card_parts and the literal-text reading below use, so "inside a code
    block" means the same thing to the history split and to validation.
    `~~~` and four-space indentation are not fences here."""
    return line.lstrip().startswith("```")


def _blank_code_spans(line: str) -> str:
    """One line with the contents of each inline code span blanked. A span
    opens at a run of backticks and closes at the next run of the same
    length *on the same line*; a run with no partner, or a backtick after a
    backslash, opens nothing — so a span that is not plainly closed hides
    nothing from validation."""
    chars, i, n = list(line), 0, len(line)
    while i < n:
        if line[i] != "`" or (i and line[i - 1] == "\\"):
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        k, close = j, None
        while k < n:
            if line[k] != "`":
                k += 1
                continue
            m = k
            while m < n and line[m] == "`":
                m += 1
            if m - k == j - i:
                close = k
                break
            k = m
        if close is None:
            i = j
            continue
        for p in range(j, close):
            chars[p] = " "
        i = close + (j - i)
    return "".join(chars)


def prose_of(text: str) -> tuple[str, bool]:
    """The text with every literal region blanked: the contents of inline
    code spans and of closed ``` fenced blocks become spaces, line breaks
    kept, so offsets and line numbers still point into the original.
    Returns it with whether a fence was left open. The fence lines
    themselves are kept — an info string is not documentation.

    This is how validation tells documentation of Bench's storage syntax
    (a `<!-- bench:` example in code, drawn as visible text by every
    Markdown renderer) from a record (the same characters anywhere else,
    which a renderer hides as an HTML comment)."""
    out, fenced = [], False
    for line in text.splitlines(keepends=True):
        if _is_fence(line):
            fenced = not fenced
            out.append(line)
        elif fenced:
            out.append(re.sub(r"[^\r\n]", " ", line))
        else:
            out.append(_blank_code_spans(line))
    return "".join(out), fenced


def card_parts(text: str) -> dict:
    """Split a card into head, brief and history (see above). The three
    concatenate back to `text` exactly, so a writer that changes one of
    them changes nothing else by construction."""
    lines = text.splitlines(keepends=True)
    n, i = len(lines), 0
    while i < n and not lines[i].strip():
        i += 1
    if i < n and TITLE_LINE_RE.match(lines[i]):
        i += 1
    while i < n and (not lines[i].strip() or HEADER_LINE_RE.match(lines[i])):
        i += 1
    start = i
    end, fenced = n, False
    for k in range(start, n):
        line = lines[k]
        if _is_fence(line):
            fenced = not fenced
            continue
        if fenced or not _starts_history(line):
            continue
        end = k
        above = k - 1
        while above >= start and not lines[above].strip():
            above -= 1
        if above >= start and lines[above].strip() == "---":
            end = above
        break
    return {"head": "".join(lines[:start]), "brief": "".join(lines[start:end]),
            "history": "".join(lines[end:])}


def brief_sha(brief: str) -> str:
    """The brief's revision: what a proposal is bound to. Surrounding blank
    lines are not content, so they do not make a proposal stale."""
    return hashlib.sha256(brief.strip().encode("utf-8")).hexdigest()


def _one_line(value, cap: int = 80) -> str:
    text = re.sub(r"[\x00-\x1f<>]", " ", str(value or ""))
    return " ".join(text.split())[:cap]


def _marker(meta: dict) -> str:
    """One entry's machine-readable line. JSON keeps it to one line; the
    two replacements keep a value from closing the HTML comment early."""
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    return raw.replace("-->", "--\\u003e").replace("<!--", "\\u003c!--")


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">"
                     for line in text.replace("\r\n", "\n").split("\n"))


def unquote(text: str) -> str:
    out = []
    for line in text.split("\n"):
        if line.startswith("> "):
            out.append(line[2:])
        elif line.startswith(">"):
            out.append(line[1:])
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


def comment_problem(body) -> str | None:
    if not isinstance(body, str) or not body.strip():
        return "a comment needs some text"
    if "\x00" in body:
        return "a comment may not contain NUL characters"
    if len(body) > MAX_COMMENT_CHARS:
        return (f"a comment is at most {MAX_COMMENT_CHARS} characters "
                f"(this one is {len(body)})")
    return None


def discussion(text: str) -> list[dict]:
    """Every entry in a card's discussion, oldest first. An entry whose
    marker does not parse is skipped rather than guessed at — the text is
    still in the file for a person to read."""
    section = DISCUSSION_SECTION_RE.search(card_parts(text)["history"])
    if not section:
        return []
    body = section.group(0)
    marks = list(ENTRY_MARK_RE.finditer(body))
    entries = []
    for index, mark in enumerate(marks):
        try:
            meta = json.loads(mark.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(meta, dict) or not meta.get("id"):
            continue
        stop = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        chunk = body[mark.end():stop]
        heading = re.search(r"^###\s", chunk, re.MULTILINE)
        if heading:
            chunk = chunk[:heading.start()]
        chunk = chunk.rstrip()
        while chunk.endswith("\n---") or chunk == "---":
            chunk = chunk[:-3].rstrip()
        entry = dict(meta)
        entry["body"] = unquote(chunk.strip("\n"))
        entries.append(entry)
    return entries


def _entry_block(meta: dict, title: str, body: str) -> str:
    return (f"### {_one_line(title, 160)}\n<!-- bench:entry {_marker(meta)} -->\n\n"
            f"{quote(body.strip())}\n")


def _insert_entry(text: str, block: str) -> str:
    """Append one entry to the end of the discussion, creating the section
    at the end of the card the first time. Whatever follows the section —
    a report appended later, with its `---` rule — is left exactly as it
    was, and so is everything before it."""
    parts = card_parts(text)
    found = DISCUSSION_SECTION_RE.search(parts["history"])
    if found is None:
        return (text.rstrip("\n") + f"\n\n## {DISCUSSION_HEADING}\n\n"
                f"{DISCUSSION_NOTE}\n\n{block.rstrip()}\n")
    offset = len(parts["head"]) + len(parts["brief"])
    start, end = offset + found.start(), offset + found.end()
    body = text[start:end]
    core = body.rstrip()
    while core.endswith("\n---"):
        core = core[:-4].rstrip()
    tail = body[len(core):]
    if tail.strip():
        tail = "\n\n" + tail.lstrip()
    else:
        tail = "\n\n" if end < len(text) else "\n"
    return text[:start] + core + "\n\n" + block.rstrip() + tail + text[end:]


def _atomic_write(path: Path, text: str) -> None:
    """Replace a card in one step: a reader sees the old file or the new
    one, never half of either. The temporary name does not end in `.md`,
    so the stage watcher never mistakes it for a card."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.bench-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stamp(iso: str) -> str:
    return iso.replace("T", " ")[:16] + " UTC"


def add_discussion_entry(filename: str, stage: str, meta: dict, title: str,
                         body: str, what: str) -> dict:
    """Write one entry where the card stands, and commit it like every other
    board-made write. Returns what happened, truthfully: the text reached
    the file, and whether it was committed — which is only ever true under
    BOARD_COMMIT_MOVES, and with BOARD_SYNC is what publishes it."""
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("bad filename")
    if stage not in config.STAGE_DIRS:
        raise ValueError("unknown stage")
    path = config.TASKS / stage / filename
    if not path.is_file():
        raise ValueError(f"{filename} is no longer in {stage}/ — refresh the board")
    _refuse_escape(path)
    block = _entry_block(meta, title, body)
    with WRITE_LOCK:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, _insert_entry(text, block))
    committed = commit_edit(filename, stage, what)
    return {"written": True, "committed": committed}


def add_comment(filename: str, stage: str, body, author: str) -> dict:
    """A person's comment, saved and nothing else: no agent is asked
    anything because a comment exists. Returns the entry as written."""
    problem = comment_problem(body)
    if problem:
        raise ValueError(problem)
    at = now_iso()
    meta = {"v": DISCUSSION_VERSION, "id": f"c-{secrets.token_hex(5)}",
            "kind": "comment", "author": _one_line(author) or "unattributed",
            "at": at}
    text = body.replace("\r\n", "\n").strip()
    title = f"Comment {meta['id']} — {meta['author']} · {stamp(at)}"
    result = add_discussion_entry(filename, stage, meta, title, text,
                                  f"comment {meta['id']}")
    return {**meta, "body": text, **result}


def find_entry(text: str, entry_id: str) -> dict | None:
    return next((e for e in discussion(text) if e.get("id") == entry_id), None)


def decision_for(text: str, proposal: str) -> dict | None:
    """The recorded decision about one proposal, if any board made one —
    read off the card, so a restart or a sync carries it."""
    return next((e for e in discussion(text) if e.get("kind") == "decision"
                 and e.get("proposal") == proposal), None)


# — acceptance criteria, and the records card 81 keeps about them —

def criteria(brief: str) -> list[dict]:
    """The brief's checklist items, each with its stable id when it has
    one. Identity is the id, never the position: reordering an unchanged
    criterion changes nothing about it."""
    out: list[dict] = []
    current = None
    for line in brief.splitlines():
        match = CRITERION_RE.match(line)
        if match:
            current = {"raw": match.group(1)}
            out.append(current)
        elif current is not None and line.strip() and line[:1].isspace():
            current["raw"] += " " + line.strip()
        else:
            current = None
    for item in out:
        found = CRITERION_ID_RE.search(item["raw"])
        item["id"] = found.group(1) if found else None
        item["text"] = " ".join(CRITERION_ID_RE.sub("", item.pop("raw")).split())
    return out


def affected_criteria(before: str, after: str) -> list[str]:
    """Which identified criteria a brief edit touches: reworded or removed.
    A criterion without an id has no record to mark, so it is not listed —
    legacy cards keep their Markdown and gain no invented decisions."""
    now = {c["id"]: c["text"] for c in criteria(after) if c["id"]}
    return sorted(c["id"] for c in criteria(before)
                  if c["id"] and now.get(c["id"]) != c["text"])


def acceptance_records(text: str) -> list[dict]:
    out = []
    for match in ACCEPTANCE_RECORD_RE.finditer(card_parts(text)["history"]):
        try:
            meta = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(meta, dict):
            out.append(meta)
    return out


def mark_for_recheck(text: str, ids: list[str], reason: str, at: str) -> str:
    """Every acceptance record about an affected criterion is marked for
    recheck — in the same text the brief is replaced in, so the two land
    in one write. Nothing else about a record changes, and a card with no
    records is returned untouched. Only the history is read: a record is
    something the board wrote there, and the same characters documented in
    the brief are an example, never a record to update."""
    wanted = set(ids)
    if not wanted:
        return text

    def _mark(match: re.Match) -> str:
        try:
            meta = json.loads(match.group(1))
        except json.JSONDecodeError:
            return match.group(0)
        if not isinstance(meta, dict) or meta.get("criterion") not in wanted:
            return match.group(0)
        meta.update({"recheck": True, "recheckReason": reason, "recheckAt": at})
        return f"<!-- bench:acceptance {_marker(meta)} -->"

    parts = card_parts(text)
    return (parts["head"] + parts["brief"]
            + ACCEPTANCE_RECORD_RE.sub(_mark, parts["history"]))


# — what a proposed brief may and may not contain —

def _cards_section(brief: str) -> str | None:
    found = CARDS_SECTION_RE.search(brief)
    return " ".join(found.group(1).split()) if found else None


def brief_problems(before: str, after) -> list[str]:
    """Why a proposed brief may not replace the active one — each a
    sentence naming the protected thing it reached for. Empty means it may.

    The proposal only ever replaces the brief region, so the head and the
    history are protected by the write itself; this refuses the ways a
    brief's *text* could still reach them — a line every header reader
    would take for metadata, a heading that would start history early, a
    marker a reaper parses, a phase's membership list — and the ways it
    could forge identity, like inventing an acceptance criterion id.

    Code is literal only where every reader agrees it is. `<!-- bench:`
    records are read from the history alone, so an example of one inside
    inline code or a closed ``` block is documentation and is allowed
    (`prose_of`); anywhere else it is a record-shaped HTML comment and is
    refused. Header fields, verdict markers, titles and history headings
    are read by line anywhere in the file, code or not, so those stay
    refused inside code too — the message says so rather than implying a
    code block would make them safe.
    """
    if not isinstance(after, str) or not after.strip():
        return ["the proposed brief is empty — a refinement edits the brief, "
                "it does not delete it"]
    problems = []
    if "\x00" in after:
        problems.append("the proposed brief contains NUL characters")
    if len(after.encode("utf-8")) > MAX_BRIEF_BYTES:
        problems.append(f"the proposed brief is larger than "
                        f"{MAX_BRIEF_BYTES // 1024} KiB")
    prose, unclosed = prose_of(after)
    lines = after.splitlines()
    first = next((line for line in lines if line.strip()), "")
    if unclosed:
        problems.append("the proposed brief opens a ``` fenced block and never "
                        "closes it — the board would read everything after it, "
                        "the card's history included, as part of the brief; "
                        "close the fence")
    if any(TITLE_LINE_RE.match(line) for line in lines):
        problems.append("the proposed brief contains a `# ` title line — the "
                        "card's title is its identity and is not refined (the "
                        "board reads such a line even inside a code block)")
    if PROTECTED_FIELD_RE.search(after) or HEADER_LINE_RE.match(first):
        problems.append("the proposed brief contains a `**Field:**` header line "
                        "— status, assignee, base branch, PR and the other "
                        "header fields are lifecycle metadata and are not "
                        "refined (the board reads such a line anywhere in the "
                        "card, code blocks included)")
    if any(_starts_history(line) for line in lines):
        problems.append("the proposed brief contains a history heading (a "
                        "dated report, Discussion, Phase log or Acceptance "
                        "record) — history is preserved, never rewritten")
    mark = BENCH_MARK_RE.search(prose)
    if mark:
        line_no = prose.count("\n", 0, mark.start()) + 1
        problems.append(f"the proposed brief contains a `<!-- bench:` marker "
                        f"outside code (line {line_no} of the brief) — "
                        f"discussion entries and acceptance records are "
                        f"written by the board only. An example of the "
                        f"syntax is kept as documentation only inside inline "
                        f"code or a closed ``` fenced block")
    if MACHINE_MARKER_RE.search(after):
        problems.append("the proposed brief contains a line the board parses "
                        "as a marker (PR REVIEW, NOT READY, RELEVANCE REVIEW, "
                        "REFINEMENT …), which it reads even inside a code "
                        "block")
    if _cards_section(before) != _cards_section(after):
        problems.append("the proposed brief changes the `## Cards` section — a "
                        "phase's membership is changed by editing the phase "
                        "card, not by refining it")
    known = {c["id"] for c in criteria(before) if c["id"]}
    seen: set[str] = set()
    for item in criteria(after):
        if not item["id"]:
            continue
        if item["id"] in seen:
            problems.append(f"the acceptance criterion id `{item['id']}` appears "
                            f"twice — an id is one criterion's identity")
        elif item["id"] not in known:
            problems.append(f"the acceptance criterion id `{item['id']}` is new "
                            f"— ids are assigned when criteria are tracked, "
                            f"never invented by a refinement")
        seen.add(item["id"])
    return problems


def brief_diff(before: str, after: str, context: int = 2) -> list[dict]:
    """A focused line diff, as data: `op` is ` `, `-`, `+`, or `…` for a
    run of unchanged lines folded away. The page renders the text it is
    given; it never interprets either side as markup."""
    a = before.strip("\n").splitlines()
    b = after.strip("\n").splitlines()
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out += [{"op": "-", "text": line} for line in a[i1:i2]]
            out += [{"op": "+", "text": line} for line in b[j1:j2]]
            continue
        run = a[i1:i2]
        head = run[:context] if i1 > 0 else []
        tail = run[-context:] if i2 < len(a) else []
        if len(run) <= len(head) + len(tail) + 1:
            out += [{"op": " ", "text": line} for line in run]
            continue
        out += [{"op": " ", "text": line} for line in head]
        out.append({"op": "…", "text":
                    f"{len(run) - len(head) - len(tail)} unchanged lines"})
        out += [{"op": " ", "text": line} for line in tail]
    return out


def changed_sections(before: str, after: str) -> list[str]:
    """The `##`/`###` headings whose content changed, or `(intro)` for the
    text above the first heading — the short answer to "what did it touch"."""
    def split(text: str) -> dict:
        out: dict = {}
        name = "(intro)"
        for line in text.splitlines():
            if re.match(r"^#{2,3}\s", line):
                name = line.lstrip("#").strip()
                out.setdefault(name, [])
                continue
            out.setdefault(name, []).append(line.rstrip())
        return {k: "\n".join(v).strip() for k, v in out.items()}
    a, b = split(before), split(after)
    return [name for name in dict.fromkeys([*a, *b]) if a.get(name) != b.get(name)]


def replace_brief(filename: str, stage: str, *, expected_sha: str,
                  new_brief: str, decision: dict, title: str, body: str,
                  affected: list[str]) -> dict:
    """Apply a refinement: the brief replaced, affected acceptance records
    marked for recheck and the decision recorded — one file write and one
    commit, or nothing at all.

    Refused, with nothing written, when the card has moved stage or its
    brief is no longer the one the proposal was made against. Everything
    that is not the brief — the head, every report, every comment that
    arrived meanwhile — is carried over byte for byte, so an intervening
    write is kept rather than overwritten. The file is compared once more
    immediately before it is replaced, so a pull landing mid-write is a
    refusal rather than a lost change.
    """
    path = config.TASKS / stage / filename
    with WRITE_LOCK:
        if not path.is_file():
            raise StaleCard(f"{filename} is no longer in {stage}/ — the proposal "
                            f"was made there; regenerate it where the card is now")
        _refuse_escape(path)
        text = path.read_text(encoding="utf-8")
        parts = card_parts(text)
        if brief_sha(parts["brief"]) != expected_sha:
            raise StaleCard(f"the brief of {filename} has changed since the "
                            f"proposal was generated — nothing was applied; "
                            f"regenerate it against the current brief")
        problems = brief_problems(parts["brief"], new_brief)
        if problems:
            raise ValueError(problems[0])
        head = parts["head"]
        if head.strip() and not head.endswith("\n\n"):
            head = head.rstrip("\n") + "\n\n"
        tail = "\n\n" if parts["history"].strip() else "\n"
        updated = head + new_brief.strip("\n") + tail + parts["history"]
        updated = mark_for_recheck(updated, affected,
                                   f"brief revised by {decision.get('proposal')}",
                                   decision.get("at") or now_iso())
        updated = _insert_entry(updated, _entry_block(decision, title, body))
        if path.read_text(encoding="utf-8") != text:
            raise StaleCard(f"{filename} changed while the proposal was being "
                            f"applied — nothing was applied; try again")
        _atomic_write(path, updated)
    committed = commit_edit(filename, stage,
                            f"brief refined ({decision.get('proposal')})")
    return {"written": True, "committed": committed}
