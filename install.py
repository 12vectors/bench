#!/usr/bin/env python3
"""Wire the task manager into the project it sits in.

    python3 .task-manager/install.py             # apply (idempotent)
    python3 .task-manager/install.py --dry-run   # report only, change nothing
    python3 .task-manager/install.py --setup     # ask the settings questions again

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

A project with no manager/local/.env then gets one written, because the
settings that change what bench *is* — claim-on-move and syncing through
origin/main — are otherwise invisible to anyone who has not read
manager/core/.env.example. Setup asks the few questions it cannot answer
for the project and writes that example file with the answers substituted
in, so the rest of the settings are discoverable by opening the result.
It runs after first_boot_clean (writing .env early would flip the
first-boot guard and leave the distribution's cards in a host project), it
never asks without a terminal on stdin — install.py sits on the path of
start.sh, update.sh and anything automated — and an existing .env is never
touched except by an explicit --setup.
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


# ── First-run settings ────────────────────────────────────────────────
#
# Everything not asked about is written at its documented default, so the
# answers are only the ones no default can be right about and nothing
# can be read off the project: how this project works (solo or team)
# and which agent runs its headless jobs. The test command used to be a
# third question; it is detected instead — see TEST_COMMANDS.

ENV_EXAMPLE = CORE / ".env.example"
ENV_FILE = LOCAL / ".env"

TEAM_NOTE = """\
  Team mode: moves claim and commit themselves, and boards converge
  through origin/main. It wants a shared origin, merge rights for whoever
  merges, and a local main that only ever advances through the board.
  Solo — today's default — does none of it."""

# What runs this project's tests, read off the project rather than asked.
# It was a question once, and it was the wrong one to put to someone
# thirty seconds into their first run: it wants an answer about a project
# they may have just cloned, before anything has explained why the board
# needs it. The file that names a project's ecosystem usually names its
# test runner too, so the first match wins and no match writes nothing.
#
# A wrong guess costs nothing it did not already cost: the prefix simply
# never matches, and the agent is denied exactly as it would be with the
# key empty. What it must never do is guess something *broader* than the
# truth — every entry here is one runner, not a shell.
TEST_COMMANDS = [
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
    ("pyproject.toml", "python3 -m unittest"),
    ("setup.py", "python3 -m unittest"),
    ("tests", "python3 -m unittest"),
]


def detect_test_command(root: Path) -> str:
    """The project's test runner, or "" when nothing here names one."""
    for marker, command in TEST_COMMANDS:
        if (root / marker).exists():
            return command
    return ""


class _Skipped(Exception):
    """Ctrl-D: stop asking. Answers already given stand, the rest of the
    file stays at its documented defaults."""


def _rel(path: Path) -> str:
    """A path as a reader would type it, relative to the project root."""
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def env_values(text: str) -> dict[str, str]:
    """KEY=VALUE lines, # comments, optional quotes — config._load_env's
    parser, minus the process environment (this is about the file)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def env_on(value: str) -> bool:
    """config.flag's rule: anything but empty/0/false/no/off is on."""
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def substitute(base: str, answers: dict[str, str]) -> str:
    """`base` with the answered keys rewritten in place — every comment and
    every other key intact, which is the point: the written file is where
    the project reads what else it can change. A key the base does not
    mention is appended rather than lost."""
    out, placed = [], set()
    for line in base.splitlines():
        stripped = line.strip()
        key = ("" if stripped.startswith("#") or "=" not in stripped
               else stripped.partition("=")[0].strip())
        if key in answers:
            out.append(f"{key}={answers[key]}")
            placed.add(key)
        else:
            out.append(line)
    for key in [k for k in answers if k not in placed]:
        out.append(f"{key}={answers[key]}")
    return "\n".join(out).rstrip("\n") + "\n"


def adapter_choices() -> list[str]:
    """The adapters actually present, core's plus this project's own —
    enumerated, never hardcoded, so a local/adapters/ entry shows up."""
    names: list[str] = []
    for base in (CORE / "adapters", LOCAL / "adapters"):
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if (child / "run").is_file() and child.name not in names:
                    names.append(child.name)
    return names


def _ask(question: str, default: str, note: str = "") -> str:
    if note:
        print(f"\n{note}")
    try:
        answer = input(f"  {question} [{default}]: ").strip()
    except EOFError:
        print()
        raise _Skipped from None
    return answer or default


def ask_questions(current: dict[str, str], answers: dict[str, str],
                  held: dict[str, str] | None = None) -> None:
    """Fill `answers` in place — in place because a Ctrl-D part-way through
    keeps what was already answered.

    `current` is the example's values under the existing file's, which is
    what a question should offer as its default. `held` is only what this
    project itself has said — empty on a first run — because "keep what is
    already there" must not mean "keep the example's default".
    """
    held = held or {}
    # Detected, not asked — and settled before the first question, so a
    # Ctrl-D part-way through still leaves the project's own runner rather
    # than the example's Python one. A value this project has already set
    # wins: --setup must not undo a hand-edit. The example's default is
    # not such a value, which is why this reads `held` and not `current`.
    answers["BOARD_AGENT_COMMANDS"] = (
        held.get("BOARD_AGENT_COMMANDS") or detect_test_command(PROJECT))

    team = env_on(current.get("BOARD_SYNC", "")) or env_on(
        current.get("BOARD_COMMIT_MOVES", ""))
    while True:
        reply = _ask("solo or team?", "team" if team else "solo",
                     note=TEAM_NOTE).lower()
        if reply in ("solo", "s", "team", "t"):
            break
        print("  answer solo or team.")
    team = reply.startswith("t")
    answers["BOARD_COMMIT_MOVES"] = "1" if team else ""
    answers["BOARD_SYNC"] = "1" if team else ""

    choices = adapter_choices()
    default_adapter = current.get("BOARD_AGENT_ADAPTER") or "claude"
    if choices:
        allowed = choices + ([default_adapter]
                             if default_adapter not in choices else [])
        while True:
            reply = _ask("which agent adapter?", default_adapter,
                         note="  Which coding agent runs headless jobs — "
                              f"here: {', '.join(choices)}.")
            if reply in allowed:
                break
            print(f"  no such adapter here — one of: {', '.join(allowed)}.")
        answers["BOARD_AGENT_ADAPTER"] = reply



def setup(dry_run: bool, forced: bool) -> None:
    """Write manager/local/.env when there is none — or rewrite it, from
    its own current values, when asked to with --setup. Silent and
    side-effect-free in every other case."""
    exists = ENV_FILE.is_file()
    if (exists and not forced) or not ENV_EXAMPLE.is_file():
        return
    verb = "rewrite" if exists else "write"
    if dry_run:
        found = detect_test_command(PROJECT)
        print(f"would ask: solo or team, which agent adapter — and "
              f"{verb} {_rel(ENV_FILE)} from {_rel(ENV_EXAMPLE)}, with "
              f"BOARD_AGENT_COMMANDS="
              f"{found or '(nothing detected)'}.\n"
              f"Dry run — nothing written.\n")
        return
    if not sys.stdin.isatty():
        if exists:
            print(f"--setup asks questions and there is no terminal to ask "
                  f"on — {_rel(ENV_FILE)} left as it is.\n")
        else:
            print(f"no {_rel(ENV_FILE)} — the defaults in {_rel(ENV_EXAMPLE)} "
                  f"apply; `python3 {_rel(Path(__file__).resolve())} --setup` "
                  f"asks the questions that write one.\n")
        return

    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    base = ENV_FILE.read_text(encoding="utf-8") if exists else example
    current = env_values(example)
    current.update(env_values(base))

    if exists:
        print(f"Rewriting {_rel(ENV_FILE)} — its current values are the "
              f"defaults below.")
    else:
        print(f"No {_rel(ENV_FILE)} yet — a few questions and bench writes "
              f"one.")
    print("Enter takes the default in [brackets]; Ctrl-D skips the rest.")

    answers: dict[str, str] = {}
    try:
        ask_questions(current, answers,
                      held=env_values(base) if exists else {})
    except _Skipped:
        print("  skipped — the rest stay at their documented defaults.")
    except KeyboardInterrupt:
        print(f"\n\nCancelled — {_rel(ENV_FILE)} not written.\n")
        return

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(substitute(base, answers), encoding="utf-8")
    print(f"\nWrote {_rel(ENV_FILE)} — every other setting is in there, "
          f"commented; edit it any time.\n")


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
    args = sys.argv[1:]
    # Order is load-bearing: setup writes local/.env, which is one of the
    # two things first_boot() reads as "this project has been here before".
    first_boot_clean(dry_run="--dry-run" in args)
    setup(dry_run="--dry-run" in args, forced="--setup" in args)
    name = adapter_name()
    passthrough = [a for a in args if a != "--setup"]
    sys.stdout.flush()   # the wire's output is a child's: keep the order
    for base in (LOCAL / "adapters", CORE / "adapters"):
        wire = base / name / "wire"
        if wire.is_file():
            return subprocess.call(
                [sys.executable, str(wire), str(PROJECT), *passthrough])
    print(f"agent adapter '{name}' has no wire script — looked in "
          f"{LOCAL / 'adapters' / name} and {CORE / 'adapters' / name}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
