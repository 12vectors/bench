"""Reading and moving task files — the only module that touches tasks/.

The directory a task file sits in *is* its status (see ../AGENTS.md). Nothing
here knows about agents or HTTP; it is the same folder kanban you could drive
by hand with mv.
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
NUMBER_RE = re.compile(r"^(\d+)[-_]")

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


def read_task(path: Path, stage: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    priority, priority_note = _split_reason(_first(PRIORITY_RE, text))
    number_match = NUMBER_RE.match(path.name)
    declared = _first(STATUS_RE, text)
    # the latest appended `PR REVIEW:` marker wins — reviews accumulate
    verdicts = PR_VERDICT_RE.findall(text)
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
        "type": _split_reason(_first(TYPE_RE, text))[0],
        # Flagged in the UI when the file's own Status line contradicts the
        # directory it is in — the board should never quietly paper over that.
        "declaredStatus": declared,
        "statusMismatch": bool(declared)
        and declared.lower() != config.STAGE_LABELS[stage].lower(),
        "mtime": path.stat().st_mtime,
        "words": len(text.split()),
        "body": text,
    }


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


def archive_task(filename: str, source: str) -> dict:
    """Archive: out of the flow but never deleted. tasks/archive/ is not a
    stage — archived cards simply leave the board."""
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
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
    return {"file": filename, "from": source}


def unarchive_task(filename: str, target: str) -> dict:
    """⌘Z: bring the last archived card back where it came from."""
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
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
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


def _commit_move(filename: str, target: str, src: Path, dst: Path, who: str,
                 number: str | None) -> None:
    """The move and the claim in one commit."""
    spec = [str(dst)]
    try:
        tracked = _git("ls-files", "--", str(src))
        if tracked.returncode == 0 and tracked.stdout.strip():
            spec.insert(0, str(src))   # git knew the old path: record its removal
    except (subprocess.SubprocessError, OSError):
        pass
    _commit(filename, f"board: {number or filename[:-3]} → {target} ({who or 'board'})",
            spec, f"{filename} moved, but committing it failed")


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
    number = NUMBER_RE.match(filename)
    return _commit(filename,
                   f"board: {number.group(1) if number else filename[:-3]} "
                   f"{what} ({actor_name() or 'board'})",
                   [str(path)],
                   f"{filename}: {what} recorded, but committing it failed")


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

    state.expect_move(filename, target, actor)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
    task = read_task(dst, target)
    if config.COMMIT_MOVES:
        _commit_move(filename, target, src, dst, name, task["number"])
    return task
