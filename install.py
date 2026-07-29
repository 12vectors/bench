#!/usr/bin/env python3
"""Wire the task manager into the project it sits in.

    python3 .task-manager/install.py             # apply (idempotent)
    python3 .task-manager/install.py --dry-run   # report only, change nothing

Vendor-specific wiring belongs to the configured agent adapter: this script
resolves the adapter (BOARD_AGENT_ADAPTER in manager/local/.env, default
"claude"; local/adapters/ overrides core/adapters/) and runs its `wire`
executable against the project root. Safe to run any time — after dropping
.task-manager/ into a new repo, and after every update.sh.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TM = Path(__file__).resolve().parent
PROJECT = TM.parent
LOCAL = TM / "manager" / "local"
CORE = TM / "manager" / "core"


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
