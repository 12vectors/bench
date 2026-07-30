"""Paths, stages and launch configuration for the task manager.

Core knows about tasks, worktrees, PRs and events. It knows nothing about
any particular app (drivers do), agent vendor (adapters do), or project
(local/ does). Everything the other modules need to know about *where
things are* lives here. No state, no behaviour.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

CORE = Path(__file__).resolve().parent             # manager/core — replaceable
MANAGER = CORE.parent                              # manager
LOCAL = MANAGER / "local"                          # project-owned, never replaced
TM_ROOT = MANAGER.parent                           # .task-manager
TASKS = TM_ROOT / "tasks"                          # stage directories only

STATE = LOCAL / "state"                            # runtime data (gitignored)
SESSIONS_DIR = STATE / "sessions"                  # per-session event logs, JSONL
AGENT_DIR = STATE / "agent"                        # headless-agent stdout logs
DRIVES_DIR = STATE / "drives"                      # driver stdout logs

# Ordered — this is the column order on the board.
STAGES = [
    ("backlog", "Backlog"),
    ("to-do", "To Do"),
    ("in-progress", "In Progress"),
    ("review", "Review"),
    ("done", "Done"),
]
STAGE_DIRS = {slug for slug, _ in STAGES}
STAGE_LABELS = dict(STAGES)


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(MANAGER), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return Path(out)
    except (subprocess.CalledProcessError, OSError):
        return TM_ROOT.parent


REPO = _repo_root()


def _load_env() -> dict[str, str]:
    """local/.env, overridden by the process environment. Stdlib-only
    parser: KEY=VALUE lines, # comments, optional quotes around the value."""
    values: dict[str, str] = {}
    path = LOCAL / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    values.update(os.environ)
    return values


_ENV = _load_env()


def setting(key: str, default: str) -> str:
    return _ENV.get(key, default)


def child_env() -> dict[str, str]:
    """Environment for adapter/driver child processes: the real environment
    with local/.env settings folded in (process env still wins), so
    BOARD_* settings reach the scripts that read them directly."""
    return dict(_ENV)


# Pinned so the board is always at the same bookmarkable URL. Sits in the
# ephemeral-safe 10000–30000 range, clear of the other local dev servers.
PORT = int(setting("BOARD_PORT", "26071"))
# One isolated checkout per running work agent, relative to the repo root.
WORKTREES = REPO / setting("BOARD_WORKTREES", ".worktrees")

# Which agent adapter runs headless jobs. Resolution ladder: local wins.
ADAPTER = setting("BOARD_AGENT_ADAPTER", "claude")

# Command prefixes headless agents may run in a worktree (the project's
# test/check commands) — neutral, comma-separated; each adapter renders
# them in its own permission-rule syntax. The universal git/gh grants are
# the adapter's own knowledge; this list is the project's half.
AGENT_COMMANDS = setting("BOARD_AGENT_COMMANDS", "python3 -m unittest")

# GitHub plumbing: the gh CLI (stub-able for tests) and the git remote PRs
# go to. Empty remote = auto-detect the first remote; no remote = no PRs.
GH_BIN = setting("BOARD_GH_BIN", "gh")
GIT_REMOTE = setting("BOARD_GIT_REMOTE", "")
PR_POLL_INTERVAL = float(setting("BOARD_PR_POLL_INTERVAL", "60"))

WATCH_INTERVAL = float(setting("BOARD_WATCH_INTERVAL", "2"))
EVENTS_CAP = int(setting("BOARD_EVENTS_CAP", "800"))
BOARD_EVENTS_CAP = int(setting("BOARD_HISTORY_CAP", "300"))


def prompt(name: str) -> str:
    """Prompt templates: core ships defaults, local/prompts/ overrides win.
    Read fresh on every launch so edits apply without a restart."""
    override = LOCAL / "prompts" / name
    if override.is_file():
        return override.read_text(encoding="utf-8")
    return (CORE / "prompts" / name).read_text(encoding="utf-8")


def checks() -> list[dict]:
    """Definition-of-done checks for the Focus panel: core ships a default
    (core/checks); a local/checks replaces it wholesale, like prompts. Each
    line is `<label>: <command regex>`; invalid regexes are skipped. The
    agent adapter reads the same file to classify commands (the claude
    adapter's emit.py is standalone, so the parser is mirrored there), and
    the browser matches with the served patterns — keep them in the regex
    dialect Python and JavaScript share. Read fresh on every request."""
    for base in (LOCAL, CORE):
        path = base / "checks"
        if not path.is_file():
            continue
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            label, sep, pattern = line.partition(":")
            label, pattern = label.strip(), pattern.strip()
            if not sep or not label or not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                continue
            entries.append({"label": label, "pattern": pattern})
        return entries
    return []


def adapter_dir() -> Path | None:
    """The configured agent adapter's directory — local overrides core."""
    for base in (LOCAL / "adapters", CORE / "adapters"):
        candidate = base / ADAPTER
        if (candidate / "run").is_file():
            return candidate
    return None


def driver_path() -> Path | None:
    """The project's app driver, if it has one."""
    candidate = LOCAL / "driver" / "start"
    return candidate if candidate.is_file() else None


def commands() -> list[dict]:
    """Project-owned commands: executables in local/commands/, surfaced as
    chips on cards and run against the task's worktree. A `# help:` line
    near the top becomes the tooltip."""
    directory = LOCAL / "commands"
    found = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith(".") or not os.access(path, os.X_OK):
                continue
            help_text = ""
            try:
                for line in path.read_text(encoding="utf-8").splitlines()[:8]:
                    if line.startswith("# help:"):
                        help_text = line[len("# help:"):].strip()
                        break
            except OSError:
                pass
            found.append({"name": path.name, "help": help_text})
    return found
