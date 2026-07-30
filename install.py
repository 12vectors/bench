#!/usr/bin/env python3
"""Wire the task manager into the project it sits in.

    python3 .task-manager/install.py             # apply (idempotent)
    python3 .task-manager/install.py --dry-run   # report only, change nothing

Vendor-specific wiring belongs to the configured agent adapter: this script
resolves the adapter (BOARD_AGENT_ADAPTER in manager/local/.env, default
"claude"; local/adapters/ overrides core/adapters/) and runs its `wire`
executable against the project root. Safe to run any time — after dropping
.task-manager/ into a new repo, and after every update.sh.

The distribution repo tracks its own development on its own board, so a
fresh clone arrives carrying those cards. The very first run in a host
project — vendored, before manager/local/ has ever been populated —
clears the stage directories, tasks/archive/, plans/ and reference/
(keeping task-template.md and .gitkeep files, printing every removal) and
then stamps manager/local/state/ so the guard is false on every later run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

TM = Path(__file__).resolve().parent
LOCAL = TM / "manager" / "local"
CORE = TM / "manager" / "core"

STAGE_DIRS = ["backlog", "to-do", "in-progress", "review", "done"]
KEEP = {".gitkeep", "task-template.md"}


def _project_root() -> Path:
    """The host project's root: the git toplevel seen from the manager's
    directory (same resolution as config._repo_root). Vendored installs
    drop .task-manager/.git at clone time, so this finds the host repo;
    when the manager IS the repo (self-hosted), it finds that repo itself.
    No git → fall back to the vendored-layout assumption, the parent."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(TM), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return Path(out)
    except (subprocess.CalledProcessError, OSError):
        pass
    return TM.parent


PROJECT = _project_root()


def _content_dirs(tm: Path) -> list[Path]:
    tasks = tm / "tasks"
    return ([tasks / stage for stage in STAGE_DIRS]
            + [tasks / "archive", tm / "plans", tm / "reference"])


def first_boot(tm: Path, project: Path) -> bool:
    """True only on a vendored install's very first run — the one moment
    anything in the stage directories can only be the distribution's own.
    False in every other situation:

    - self-hosted (the manager IS the repo): tasks/ is that repo's own
      history, never distribution residue — including a fresh dev clone;
    - already wired (local/.env or local/state/ exists): anything in the
      stage directories can only be the host project's own work."""
    if project.resolve() == tm.resolve():
        return False
    local = tm / "manager" / "local"
    return not (local / ".env").exists() and not (local / "state").exists()


def first_boot_leftovers(tm: Path) -> list[Path]:
    """The distribution's shipped cards, plans and reference documents —
    the paths a first boot must clear."""
    return [child
            for d in _content_dirs(tm) if d.is_dir()
            for child in sorted(d.iterdir()) if child.name not in KEEP]


def first_boot_clean(dry_run: bool) -> None:
    """First boot only: remove the distribution's shipped content and stamp
    local/state/ so this never runs again — even if the adapter wire fails
    (a host without .claude/ still gets the board via start.sh) or the host
    creates cards before the next run. Off first boot nothing is touched,
    not even the stamp."""
    if not first_boot(TM, PROJECT):
        return
    leftovers = first_boot_leftovers(TM)
    if leftovers:
        print("first boot — clearing the distribution's own cards:")
        verb = "would remove" if dry_run else "removed"
        for path in leftovers:
            print(f"  {verb}  {path.relative_to(TM)}")
            if not dry_run:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        print()
    if not dry_run:
        (LOCAL / "state").mkdir(parents=True, exist_ok=True)


def adapter_name() -> str:
    if os.environ.get("BOARD_AGENT_ADAPTER"):
        return os.environ["BOARD_AGENT_ADAPTER"]
    env_file = LOCAL / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "BOARD_AGENT_ADAPTER" and value.strip():
                return value.strip().strip("'\"")
    return "claude"


def main() -> int:
    first_boot_clean(dry_run="--dry-run" in sys.argv[1:])
    name = adapter_name()
    for base in (LOCAL / "adapters", CORE / "adapters"):
        wire = base / name / "wire"
        if wire.is_file():
            return subprocess.call(
                [sys.executable, str(wire), str(PROJECT), *sys.argv[1:]])
    print(f"agent adapter '{name}' has no wire script — looked in "
          f"{LOCAL / 'adapters' / name} and {CORE / 'adapters' / name}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
