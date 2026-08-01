"""Reading, moving and writing task files — the only module that touches tasks/.

The directory a task file sits in *is* its status (see ../AGENTS.md). Nothing
here knows about agents or HTTP; it is the same folder kanban you could drive
by hand with mv.

Every write goes out through one of two doors — `_relocate` for anything
that changes which directory a card sits in, `append_to_task`/`commit_edit`
for anything written into it where it stands — and both commit under the
`COMMIT_MOVES` gate. That is deliberate: committing is a property of
writing to a task file, not something each caller has to remember.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import config
import state

TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
PRIORITY_RE = re.compile(r"^\*\*Priority:\*\*\s*(.+?)\s*$", re.MULTILINE)
TYPE_RE = re.compile(r"^\*\*Type:\*\*\s*(.+?)\s*$", re.MULTILINE)
ASSIGNEE_RE = re.compile(r"^\*\*Assignee:\*\*\s*(.+?)\s*$", re.MULTILINE)
ASSIGNEE_LINE_RE = re.compile(r"^\*\*Assignee:\*\*[^\n]*\n?", re.MULTILINE)
PR_RE = re.compile(r"^\*\*PR:\*\*\s*(\S+)\s*$", re.MULTILINE)
PR_VERDICT_RE = re.compile(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", re.MULTILINE)
DEPENDS_RE = re.compile(r"^\*\*Depends on:\*\*\s*(.+?)\s*$", re.MULTILINE)
NUMBER_RE = re.compile(r"^(\d+)[-_]")

# A phase is a card that lists its cards: `**Type:** Phase` plus a `## Cards`
# section naming its members, one per line, in the order they run.
PHASE_TYPE = "phase"
CARDS_SECTION_RE = re.compile(r"^##\s+Cards\s*$(.*?)(?=^##\s|\Z)",
                              re.MULTILINE | re.DOTALL)
CARD_ITEM_RE = re.compile(r"^(?:[-*+]\s+)?#?0*(\d+)\b")
DEPENDS_ITEM_RE = re.compile(r"#?\s*0*(\d+)")

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
        directory = config.TM_ROOT / slug
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
    return subprocess.run(["git", "-C", str(config.REPO), *args],
                          capture_output=True, text=True, timeout=timeout)


def actor_name() -> str:
    """Who this checkout is: `git config user.name`, the identity git history
    already shows. Empty when git has no name — then nothing is claimed."""
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
    """
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


def _commit_move(filename: str, target: str, src: Path, dst: Path, who: str,
                 number: str | None) -> None:
    """The move and the claim in one commit.

    Both paths are named, so git records a rename rather than a delete and
    an add — and a card git has never seen (a brand-new backlog file) names
    only its destination, since a pathspec matching nothing in HEAD would
    fail the commit outright.
    """
    spec = [str(dst)]
    try:
        tracked = _git("ls-files", "--", str(src))
        if tracked.returncode == 0 and tracked.stdout.strip():
            spec.insert(0, str(src))   # git knew the old path: record its removal
    except (subprocess.SubprocessError, OSError):
        pass
    _commit(filename, f"board: {number or filename[:-3]} → {target} ({who or 'board'})",
            spec, f"{filename} moved, but committing it failed")


def _relocate(filename: str, src: Path, dst: Path, text: str, target: str,
              actor: str, who: str | None = None) -> None:
    """The one door out of a directory under tasks/: register who is doing
    it, write the file, move it, commit it.

    Every mover in this module goes through here, so committing is a
    property of *writing to a task file* rather than something each caller
    remembers — the way archiving forgot it. `target` is what the ticker
    and the commit message call the destination (a stage slug, or
    `archived`), and `who` is this checkout's git name when the caller has
    already paid for it.
    """
    state.expect_move(filename, target, actor)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
    if config.COMMIT_MOVES:
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
    path = config.TASKS / stage / filename
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        return False
    commit_edit(filename, stage, what)
    return True


def move_task(filename: str, source: str, target: str, actor: str = "you") -> dict:
    """Move a task file between stage directories and fix its Status line.

    With `BOARD_COMMIT_MOVES` on, the move also claims the card (an
    **Assignee:** line, this checkout's git name) or releases it when the
    card is walked back to backlog, and commits the whole change.
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

    _relocate(filename, src, dst, text, target, actor, who=name)
    return read_task(dst, target)
