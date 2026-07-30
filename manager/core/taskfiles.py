"""Reading and moving task files — the only module that touches tasks/.

The directory a task file sits in *is* its status (see ../AGENTS.md). Nothing
here knows about agents or HTTP; it is the same folder kanban you could drive
by hand with mv.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import config
import state

TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
PRIORITY_RE = re.compile(r"^\*\*Priority:\*\*\s*(.+?)\s*$", re.MULTILINE)
TYPE_RE = re.compile(r"^\*\*Type:\*\*\s*(.+?)\s*$", re.MULTILINE)
PR_RE = re.compile(r"^\*\*PR:\*\*\s*(\S+)\s*$", re.MULTILINE)
PR_VERDICT_RE = re.compile(r"^PR REVIEW:\s*(APPROVE|REQUEST CHANGES)", re.MULTILINE)
NUMBER_RE = re.compile(r"^(\d+)[-_]")


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


def move_task(filename: str, source: str, target: str, actor: str = "you") -> dict:
    """Move a task file between stage directories and fix its Status line."""
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

    state.expect_move(filename, target, actor)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    shutil.move(str(src), str(dst))
    return read_task(dst, target)
