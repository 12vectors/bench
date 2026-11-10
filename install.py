#!/usr/bin/env python3
"""Wire the task manager into the project it manages.

    python3 .task-manager/install.py             # apply (idempotent)
    python3 .task-manager/install.py --dry-run   # report only, change nothing
    python3 .task-manager/install.py --setup     # ask the settings questions again

    python3 bench/install.py --app /ws/app --work /ws/work
                                                 # an external layout: the
                                                 # tool here, the application
                                                 # there, the work state in a
                                                 # third place (task 56)

`--app` and `--work` record the external layout in manager/local/.env,
build the work-state skeleton (tasks/ with its stages, plans/,
reference/) and wire the adapter so that **nothing of bench is written
into the application**: the hook settings are generated under the tool's
own local state and handed to launches from there. `--worktrees` names
where application worktrees go; it defaults to `worktrees` beside the
application, because an external install must not leave directories in a
checkout nobody asked it to touch. `--app-hooks` is the opt-in extra that
also writes the application's *local* settings file, and refuses if git
tracks it.

A layout that does not hold together is refused before anything is
written — `config.problems()` is the one authority, the same list the
board prints when it declines to serve (fail closed: every fallback
available here leads to the wrong repository).

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

Then the work state is initialized where the layout puts it, and only
there (task 64): tasks/ with its stages and archive/, plans/ and
reference/ at the resolved work root — the install itself when vendored,
BOARD_WORK_ROOT when that is set, read from the persisted settings as
well as from this run's flags — with the task template seeded from the
tool-owned manager/core/seed/ when the work root has none. Nothing that
exists is overwritten. Externally, a tool-local tasks/, plans/ or
reference/ that is exactly the untouched skeleton an older release
unpacked is removed; anything else there is kept whole and named, and a
changed work root is reported with the previous one left as it was.

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
LAYOUT_FLAGS = {"--app": "BOARD_APP_ROOT", "--work": "BOARD_WORK_ROOT",
                "--worktrees": "BOARD_WORKTREES"}


def _setting(key: str) -> str:
    """A BOARD_* setting as config reads it: the process environment, then
    manager/local/.env. A standalone copy on purpose — install.py has to
    answer this before manager/core is on the path at all."""
    value = os.environ.get(key)
    if value:
        return value.strip()
    env_file = LOCAL / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, raw = line.strip().partition("=")
            if name.strip() == key and raw.strip():
                value = raw.strip().strip("'\"")
    return (value or "").strip()


def _derived_project_root() -> Path:
    """Where the application is when nobody said: the git toplevel seen
    from the manager's directory (same resolution as config's). Vendored
    installs drop .task-manager/.git at clone time, so this finds the host
    repo; when the manager IS the repo (self-hosted), it finds that repo
    itself. No git → fall back to the vendored-layout assumption."""
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


def _project_root() -> Path:
    """The application this install manages — named explicitly when it is
    external, derived as above when it is not."""
    explicit = _setting("BOARD_APP_ROOT")
    return Path(explicit).expanduser() if explicit else _derived_project_root()


PROJECT = _project_root()


def _work_root() -> Path:
    """Where the work state lives: tasks/, plans/, reference/."""
    explicit = _setting("BOARD_WORK_ROOT")
    return Path(explicit).expanduser() if explicit else TM


def _external() -> bool:
    return bool(_setting("BOARD_APP_ROOT") or _setting("BOARD_WORK_ROOT"))


def load_core_config():
    """manager/core/config.py, for the one answer that must not be given
    twice: what the layout resolves to and whether it holds together.

    None when core is not on disk beside this script — a stripped install
    tree, which is exactly what the wiring tests build. Then install.py
    does what it always did and validates nothing, because there is
    nothing here that could."""
    if not (CORE / "config.py").is_file():
        return None
    sys.path.insert(0, str(CORE))
    try:
        import config
        return config
    except Exception:          # noqa: BLE001 — a broken core must not stop wiring
        return None


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


# ── the external layout ───────────────────────────────────────────────


def flag_value(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else None


def layout_answers(args: list[str]) -> dict[str, str]:
    """The layout keys named on the command line, with the one default
    that is not a plain pass-through: `--worktrees` left unsaid in an
    external layout means `worktrees` beside the application, never
    `.worktrees` inside it."""
    answers: dict[str, str] = {}
    for option, key in LAYOUT_FLAGS.items():
        value = flag_value(args, option)
        if value:
            answers[key] = str(Path(value).expanduser()) \
                if Path(value).expanduser().is_absolute() else value
    if ("BOARD_APP_ROOT" in answers or "BOARD_WORK_ROOT" in answers) \
            and "BOARD_WORKTREES" not in answers \
            and not _setting("BOARD_WORKTREES"):
        app = Path(answers.get("BOARD_APP_ROOT") or PROJECT).expanduser()
        answers["BOARD_WORKTREES"] = str(app.parent / "worktrees")
    return answers


def apply_to_environment(answers: dict[str, str]) -> None:
    """Put the answers where config will read them, so the layout is
    validated as it will actually resolve — before a line is written."""
    for key, value in answers.items():
        os.environ[key] = value


def write_layout_env(answers: dict[str, str], dry_run: bool) -> bool:
    """Record the layout in manager/local/.env, from the documented
    example when there is no file yet — so every other setting arrives
    commented beside it, exactly as `setup()` leaves one. Returns whether
    anything changed; a second identical run writes nothing."""
    if not answers:
        return False
    base = ""
    if ENV_FILE.is_file():
        base = ENV_FILE.read_text(encoding="utf-8")
    elif ENV_EXAMPLE.is_file():
        base = ENV_EXAMPLE.read_text(encoding="utf-8")
    updated = substitute(base, answers)
    if base == updated:
        return False
    if not dry_run:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.write_text(updated, encoding="utf-8")
    return True


# ── the work state's skeleton (task 64) ───────────────────────────────
#
# A release used to unpack tasks/, plans/ and reference/ beside the tool,
# before anything knew which layout it was going into — so an external
# install was left with a second, empty, apparently active board under
# the tool. The artifact now ships none of it. The installer creates the
# skeleton at the one work root the layout resolves to, and the task
# template comes from a tool-owned seed under manager/core/seed/.

WORK_SKELETON = ([Path("tasks") / slug for slug in STAGE_DIRS]
                 + [Path("tasks") / "archive", Path("plans"),
                    Path("reference")])
TEMPLATE = Path("tasks") / "task-template.md"
TEMPLATE_SEED = CORE / "seed" / "task-template.md"

# Every task template a release unpacked at tasks/task-template.md before
# the seed moved into manager/core/seed/, by sha256 — the whole history of
# that file. A tool-local copy matching one of these (or today's seed) is
# bench's own and untouched; anything else is somebody's edit, and kept.
SHIPPED_TEMPLATES = frozenset({
    "e3cccb3976638e4380bf5c4193c04ad7bedcd7e49b695f15a9885b1f15e8b8a0",
    "2932be06257889f703147bb90bd1218c5e5c50fb44f8703fa4759056540d16e5",
    "676ecf3aae8bd4e69514a96671e23bcacd1dfdd6d8f515ca475387bc613a7cca",
    "b2f5c4a0c2143401b54a978e31364a4370a62e8fa72a102d178de8c4ec443735",
    "768d5a29447bb42a444087907184e0482a307c2d509a688c490030d270ee7a38",
})

WORK_ROOT_RECORD = LOCAL / "state" / "work-root"


def _present(path: Path) -> bool:
    """There is something at `path` — a dangling link included, because a
    write to it would land wherever the link points."""
    return path.exists() or path.is_symlink()


def _escapes(path: Path, root: Path) -> Path | None:
    """Where `path` really lands, when that is outside `root` — config's
    `escapes`, repeated here because a vendored install runs without
    loading core at all. Non-strict: a directory about to be created is
    judged by the links in the parents it would be created under."""
    def real(p: Path) -> Path:
        try:
            return p.resolve()
        except OSError:
            return p
    target, base = real(path), real(root)
    try:
        target.relative_to(base)
        return None
    except ValueError:
        return target


def skeleton_refusals(work: Path) -> list[str]:
    """Every path the skeleton would create that a link would carry out
    of the work root. Asked before anything is written, so a refusal
    leaves nothing half made."""
    found = []
    for relative in [*WORK_SKELETON, TEMPLATE]:
        path = work / relative
        if _present(path):
            continue
        real = _escapes(path, work)
        if real is not None:
            found.append(f"{relative.as_posix()} under the work root {work} "
                         f"would be created at {real}, outside it — a link "
                         f"on the way there leads out")
    return found


def make_work_skeleton(work: Path, dry_run: bool) -> list[str]:
    """tasks/ with its stage directories and archive/, plans/ and
    reference/ — each new directory holding a .gitkeep, so the skeleton
    survives a commit — and the task template, seeded from the tool so a
    new board starts from something. Anything already there is left
    exactly as it is: a work root is very often a repository that already
    holds a board, and a template that exists is somebody's."""
    created: list[str] = []
    for relative in WORK_SKELETON:
        path = work / relative
        if _present(path):
            continue
        created.append(relative.as_posix() + "/")
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
            (path / ".gitkeep").touch()
    destination = work / TEMPLATE
    if TEMPLATE_SEED.is_file() and not _present(destination):
        created.append(TEMPLATE.as_posix())
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATE_SEED, destination)
    return created


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tracked(tm: Path) -> set[str]:
    """The tool-local work paths git tracks, relative to the install.
    Removing one would be a change in somebody's history, so a tracked
    file is never pristine — whatever it holds."""
    try:
        out = subprocess.run(
            ["git", "-C", str(tm), "ls-files", "-z", "--",
             "tasks", "plans", "reference"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {name for name in out.stdout.split("\0") if name}


def _pristine(tm: Path, top: str, tracked: set[str]) -> tuple[bool, str,
                                                              list[str]]:
    """Whether the tool-local `top` (tasks, plans or reference) is exactly
    what an old release unpacked and nothing more: skeleton directories,
    empty .gitkeep files and an unedited shipped template, none of them
    tracked and none of them links. Returns the verdict, the reason it is
    not pristine, and what in it bench cannot account for (the generated
    pieces are left out of that list: they are not what a person needs
    to go and look at)."""
    root = tm / top
    if root.is_symlink():
        return False, "it is a link", []
    if not root.is_dir():
        return False, "it is not a directory", []
    skeleton = {(tm / r) for r in WORK_SKELETON if r.parts[0] == top}
    skeleton.add(root)
    notable: list[str] = []
    reasons: list[str] = []

    def unaccounted(path: Path, reason: str, suffix: str = "") -> None:
        notable.append(path.relative_to(tm).as_posix() + suffix)
        if reason not in reasons:
            reasons.append(reason)

    for here, dirs, names in os.walk(root):
        base = Path(here)
        dirs.sort()
        for name in dirs:
            path = base / name
            if path.is_symlink():
                unaccounted(path, "it holds links")
            elif path not in skeleton:
                unaccounted(path, "it holds directories bench never made",
                            "/")
        for name in sorted(names):
            path = base / name
            if path.relative_to(tm).as_posix() in tracked:
                unaccounted(path, "git tracks it")
            elif path.is_symlink() or not path.is_file():
                unaccounted(path, "it holds links")
            elif name == ".gitkeep" and base in skeleton:
                if path.stat().st_size:
                    unaccounted(path, "a .gitkeep in it has content")
            elif path == tm / TEMPLATE:
                digest = _sha256(path)
                seeded = (TEMPLATE_SEED.is_file()
                          and digest == _sha256(TEMPLATE_SEED))
                if digest not in SHIPPED_TEMPLATES and not seeded:
                    unaccounted(path, "its task template has been edited")
            else:
                unaccounted(path, "it holds files bench did not ship")
        dirs[:] = [d for d in dirs
                   if not (base / d).is_symlink() and base / d in skeleton]
    return not reasons, "; ".join(reasons), notable


def _remove_pristine(tm: Path, top: str) -> None:
    """Delete one pristine tool-local tree: its .gitkeep files and its
    shipped template by name, then each directory with rmdir — which
    refuses a directory that is not empty, so anything that appeared
    since it was judged survives."""
    root = tm / top
    for here, _, names in os.walk(root, topdown=False):
        base = Path(here)
        for name in names:
            if name == ".gitkeep" or base / name == tm / TEMPLATE:
                (base / name).unlink()
        try:
            base.rmdir()
        except OSError:
            pass


def converge_tool_local(tm: Path, work: Path, dry_run: bool) -> None:
    """With the work state somewhere else, what to do about tasks/,
    plans/ and reference/ still sitting beside the tool — left there by
    an older release, or by this install's vendored past.

    Only what is demonstrably the untouched skeleton an old release
    unpacked is removed, one top-level directory at a time. Everything
    else — a card, a plan, an edited template, a tracked file, a link,
    anything bench cannot account for — is kept whole and named, beside
    the root that is authoritative. Nothing is moved, merged or read from
    it: a board that used to live there is its owner's to carry over."""
    tops = [top for top in ("tasks", "plans", "reference")
            if _present(tm / top)
            # Under the work root after all (a link into it, or a work
            # root holding the install): part of the live state, not ours.
            and _escapes(tm / top, work) is not None]
    if not tops:
        return
    tracked = _tracked(tm)
    removable, kept = [], []
    for top in tops:
        pristine, reason, files = _pristine(tm, top, tracked)
        (removable if pristine else kept).append((top, reason, files))
    if removable:
        verb = "would remove" if dry_run else "removed"
        print(f"the work state is at {work} — {verb} the unused, untouched "
              f"skeleton an older release left beside the tool:")
        for top, _, _ in removable:
            print(f"  {top}/")
            if not dry_run:
                _remove_pristine(tm, top)
        print()
    if kept:
        print(f"the work state is at {work}; this board reads and writes "
              f"nothing else. Kept beside the tool, untouched and not "
              f"migrated:")
        for top, reason, files in kept:
            count = len(files)
            print(f"  {tm / top}/  — {reason}")
            for name in files[:5]:
                print(f"      {name}")
            if count > 5:
                print(f"      … and {count - 5} more")
        print("  Bench does not move, merge or delete these. Carry over "
              "anything you still need by hand, then remove them.\n")


def record_work_root(work: Path, dry_run: bool) -> None:
    """Say so when the work root is not the one this install last
    initialized — and say that the previous one was left alone, because
    that is the thing a person changing layout needs to hear."""
    try:
        current = str(work.resolve())
    except OSError:
        current = str(work)
    previous = ""
    if WORK_ROOT_RECORD.is_file():
        previous = WORK_ROOT_RECORD.read_text(encoding="utf-8").strip()
    if previous == current:
        return
    if previous:
        print(f"the work root changed: {current} is authoritative now. The "
              f"previous one, {previous}, was left exactly as it was — "
              f"nothing in it was moved, merged or deleted.\n")
    if not dry_run:
        WORK_ROOT_RECORD.parent.mkdir(parents=True, exist_ok=True)
        WORK_ROOT_RECORD.write_text(current + "\n", encoding="utf-8")


def initialize_work_state(work: Path, external: bool, dry_run: bool) -> int:
    """The skeleton at the resolved work root, the tool-local leftovers
    beside it, and the record of which root is authoritative. 1 when a
    link would carry a write out of the work root — refused before
    anything is written."""
    if not work.is_dir() and not dry_run:
        # An external root that is not there: the layout check that
        # follows names it. Creating it whole is not this step's call.
        return 0
    refusals = skeleton_refusals(work)
    if refusals:
        print("The work state's skeleton cannot be created:\n")
        for refusal in refusals:
            print(f"  - {refusal}")
        print("\nNothing was written.")
        return 1
    created = make_work_skeleton(work, dry_run)
    if created:
        print(f"{'would create' if dry_run else 'created'} the work "
              f"state under {work}:")
        for entry in created:
            print(f"  {entry}")
        print()
    if external and work.resolve() != TM.resolve():
        converge_tool_local(TM, work, dry_run)
    # Kept only once a layout has been external: a vendored install that
    # never was one stays without a state stamp it did not ask for, which
    # first_boot() reads as "this project has been here before".
    if work.is_dir() and (external or WORK_ROOT_RECORD.is_file()):
        record_work_root(work, dry_run)
    return 0


def report_problems(problems: list[str]) -> None:
    print("This layout cannot be served:\n")
    for problem in problems:
        print(f"  - {problem}")
    print(f"\nNothing was written. Settings live in {ENV_FILE} "
          f"(defaults in {ENV_EXAMPLE}).")


def print_layout(config) -> None:
    layout = config.layout()
    print(f"tool:     {layout['tool']}")
    print(f"app:      {layout['app']}")
    print(f"work:     {layout['work']}"
          + ("" if layout["stateRepo"]
             else "  (no git here — the board commits nothing)"))
    print(f"trees:    {layout['worktrees']}\n")


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
    global PROJECT
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    app_hooks = "--app-hooks" in args

    answers = layout_answers(args)
    apply_to_environment(answers)
    PROJECT = _project_root()
    external = _external()

    # Validation belongs to a *configured* layout. Vendored, every root is
    # derived from where this script sits, so there is nothing a person
    # could have got wrong — and refusing to wire over it would be a new
    # way to fail at something that always worked. The board refuses a
    # broken layout either way.
    config = load_core_config() if external else None
    if config is not None:
        # Before anything is written, and without requiring the work root
        # or the skeleton this run may be about to create: neither may be
        # built under a layout these checks would refuse.
        problems = config.problems(require_tasks=False, require_work=False)
        if problems:
            report_problems(problems)
            return 1

    # Order is load-bearing: setup writes local/.env, which is one of the
    # two things first_boot() reads as "this project has been here before".
    # It never runs externally — it exists to clear the *distribution's*
    # shipped cards out of a vendored install, and an external work root
    # is a board of its own whose cards are nobody's residue.
    if not external:
        first_boot_clean(dry_run=dry_run)

    # The work root as the layout resolves it — persisted settings and
    # this run's flags alike, so a re-run without the flags initializes
    # the same place the first one did.
    work = _work_root()
    if answers:
        verb = "would record" if dry_run else "recorded"
        if not dry_run and answers.get("BOARD_WORK_ROOT") \
                and not work.exists() and work.parent.is_dir():
            # `--work` names where the work state goes, so creating that
            # directory is part of answering it. Only after the checks
            # above have passed, and only one level: a parent that is not
            # there is a path the person has not thought through.
            work.mkdir()
        if write_layout_env(answers, dry_run):
            print(f"{verb} the layout in {ENV_FILE}:")
            for key in sorted(answers):
                print(f"  {key}={answers[key]}")
            print()

    # Self-hosted — bench's own checkout, where the tool IS the project —
    # tasks/ is that repository's history and is left to it.
    if external or PROJECT.resolve() != TM.resolve():
        status = initialize_work_state(work, external, dry_run)
        if status:
            return status

    setup(dry_run=dry_run, forced="--setup" in args)

    if config is not None:
        problems = config.problems(require_tasks=not dry_run,
                                   require_work=not dry_run)
        if problems:
            report_problems(problems)
            return 1
        print_layout(config)

    name = adapter_name()
    passthrough = ["--dry-run"] if dry_run else []
    if external and config is not None:
        # Generated, not installed: the application gets nothing, and the
        # same document the headless launches build is written where a
        # person's own session can be pointed at it.
        passthrough += ["--generated",
                        str(LOCAL / "state" / "hooks" / name / "settings.json"),
                        "--plans", str(_work_root() / "plans")]
    if app_hooks:
        passthrough.append("--app-hooks")
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
