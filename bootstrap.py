#!/usr/bin/env python3
"""Set up a workspace for bench, then hand off to the board (task 87).

    python3 bench/bootstrap.py inspect --app /ws/app            # read only
    python3 bench/bootstrap.py plan    --app /ws/app [choices]  # preview
    python3 bench/bootstrap.py apply   --app /ws/app [choices] --yes

The terminal half of first-time setup, usable by a person or by their
coding agent. It is not a second installer: everything that installs is
`install.py`, run as a child with the layout flags it already documents.
What this adds is the part before and after that run:

- **inspect** discovers, and writes nothing. The application's canonical
  root, its remotes, branch and whether it has uncommitted work; any bench
  installation already there; whether the proposed work root is a git
  repository of its own; which agent CLIs and git tooling are on PATH;
  and what the project *declares* — instructions, CI workflows, test and
  start scripts — read as text and never run. Git is asked with its
  filesystem monitor and hooks turned off, so not even a repository's own
  configuration executes anything.
- **plan** resolves the choices, refuses an unsafe or ambiguous layout, and
  previews every filesystem, git, settings and network operation that
  `apply` would make. It also writes nothing.
- **apply** makes exactly the previewed operations, after approval:
  `--yes`, or an answered prompt on a terminal. Without a terminal and
  without `--yes` it asks nothing and exits 2 naming what is missing.

Every run ends with a versioned, sanitized **handoff** — printed, and with
`apply` also written to `manager/local/state/bootstrap/handoff.json` — that
names the roles, the choices, what each operation did, the guide items
left for the browser and the board's real URL. A missing optional
capability (no `gh`, no agent CLI, no remote) is a guide item, never a
silent default and never a failed install.

Defaults, all of them conservative: the external layout; the board on
loopback; nothing pushed or merged in the application; no remote created,
no first push and no sync unless each is asked for separately; a created
remote is always private. Credentials are never asked for, accepted in a
URL or printed: a provider's own login flow (`gh auth login`, the agent
CLI's sign-in) runs in the person's terminal, outside this script.

Exit status: 0 done (or nothing to do), 1 refused or a required step
failed, 2 a choice or approval is missing, 130 interrupted. The full
option list is `--help` on each subcommand; the guide "Set up Bench with
your coding agent" (README.md) uses no other flags.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HANDOFF_KIND = "bench-bootstrap-handoff"
HANDOFF_VERSION = 1

TM = Path(__file__).resolve().parent
LOCAL = TM / "manager" / "local"
CORE = TM / "manager" / "core"
ENV_FILE = LOCAL / ".env"
STATE = LOCAL / "state" / "bootstrap"
HANDOFF_FILE = STATE / "handoff.json"
PROGRESS_FILE = STATE / "progress.json"

DOCS_URL = ("https://bench.12vectors.com/guides/"
            "set-up-with-your-coding-agent/")
DEFAULT_PORT = "26071"
STAGES = ("backlog", "to-do", "in-progress", "review", "done")

EXIT_OK, EXIT_FAILED, EXIT_NEEDS, EXIT_INTERRUPTED = 0, 1, 2, 130

# The CLI each shipped adapter drives, and the setting that renames it.
# Only ever looked up on PATH — discovery runs no binary.
AGENT_CLIS = {"claude": ("BOARD_CLAUDE_BIN", "claude"),
              "opencode": ("BOARD_OPENCODE_BIN", "opencode")}


# ── redaction ─────────────────────────────────────────────────────────
#
# Nothing this script prints or writes may carry a credential. It never
# asks for one, but a remote URL can embed one and a child's output can
# echo one, so every line leaving here passes through `redact`.

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})"),
    re.compile(r"(?i)\b(github_pat_[A-Za-z0-9_]{16,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"\b(glpat-[A-Za-z0-9_-]{16,})"),
    re.compile(r"(?i)(?<=bearer )([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)(?<=token[=:] )(\S{8,})"),
    re.compile(r"(?i)(?<=token=)([^\s&]{8,})"),
]
# User information in an http(s) URL is where a token rides
# (https://<token>@github.com/…). An ssh user name (ssh://git@host) is not
# a secret and is kept, so the URL stays recognisable.
USERINFO = re.compile(r"(?i)\b(https?://)[^/@\s]+@")


def redact(text: str) -> str:
    text = USERINFO.sub(r"\1***@", text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def redact_tree(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_tree(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_tree(v) for k, v in value.items()}
    return value


def has_credentials(url: str) -> bool:
    """A URL that carries a password or a token: anything `redact` would
    change, or a user:password pair under any scheme."""
    match = re.match(r"(?i)^[a-z][a-z0-9+.-]*://([^/@\s]+)@", url)
    return bool(match and ":" in match.group(1)) or redact(url) != url


def say(*parts: str) -> None:
    print(redact(" ".join(parts)))


# ── read-only probes ──────────────────────────────────────────────────

# A repository's own config can name programs git runs while answering
# even a read: a filesystem monitor, hooks. Discovery turns both off, and
# never takes an optional lock, so asking about a checkout changes nothing
# in it and runs nothing from it.
GIT_READ = ["git", "-c", "core.fsmonitor=false", "-c",
            f"core.hooksPath={os.devnull}", "--no-optional-locks"]


def _git_env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C"})
    return env


def git_read(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run([*GIT_READ, "-C", str(path), *args],
                             capture_output=True, text=True, timeout=15,
                             env=_git_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def real(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()


def toplevel(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    top = git_read(path, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def repo_facts(path: Path) -> dict:
    """What a directory is, as git sees it — never what to do about it."""
    path = real(path)
    facts = {"path": str(path), "exists": path.exists(),
             "isDir": path.is_dir(), "repo": None}
    top = toplevel(path)
    if top is None:
        return facts
    remotes = []
    listing = git_read(top, "remote") or ""
    for name in listing.splitlines():
        url = git_read(top, "remote", "get-url", name) or ""
        remotes.append({"name": name, "url": redact(url)})
    porcelain = git_read(top, "status", "--porcelain=v1",
                         "--untracked-files=normal")
    facts["repo"] = {
        "toplevel": str(top),
        "isToplevel": top == path,
        "branch": git_read(top, "symbolic-ref", "--quiet", "--short", "HEAD"),
        "hasCommits": git_read(top, "rev-parse", "--verify", "--quiet",
                               "HEAD") is not None,
        "remotes": remotes,
        "dirty": (len(porcelain.splitlines())
                  if porcelain is not None else None),
    }
    return facts


def env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def setting(key: str, default: str = "") -> str:
    """The process environment, then this install's local .env — the
    precedence config itself uses."""
    if os.environ.get(key):
        return os.environ[key].strip()
    return env_file_values(ENV_FILE).get(key, "") or default


def bench_version(tool: Path = TM) -> str:
    try:
        return (tool / "manager" / "core" / "VERSION").read_text(
            encoding="utf-8").strip()
    except OSError:
        return "unknown"


def is_install(path: Path) -> bool:
    return ((path / "install.py").is_file()
            and (path / "manager" / "core" / "VERSION").is_file())


def installations(app: Path | None, workspace: Path) -> list[dict]:
    """Bench installations already here: this one, a vendored one inside
    the application, and siblings in the workspace — each with the
    application its settings name. Read, never run."""
    found, seen = [], set()

    def add(path: Path, role: str) -> None:
        path = real(path)
        if path in seen or not is_install(path):
            return
        seen.add(path)
        values = env_file_values(path / "manager" / "local" / ".env")
        found.append({
            "path": str(path), "role": role, "version": bench_version(path),
            "configured": (path / "manager" / "local" / ".env").is_file(),
            "app": values.get("BOARD_APP_ROOT") or None,
            "work": values.get("BOARD_WORK_ROOT") or None,
        })

    add(TM, "this install")
    if app is not None:
        add(app / ".task-manager", "vendored in the application")
    if workspace.is_dir():
        try:
            children = sorted(workspace.iterdir())[:200]
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and not child.is_symlink():
                add(child, "beside it in the workspace")
    return found


def tooling() -> dict:
    """Which binaries are on PATH. Looked up, not executed: whether a CLI
    is signed in, or works, is a later and separately authorized check."""
    tools = {}
    for name, (key, default) in {"git": ("", "git"), "gh": ("BOARD_GH_BIN",
                                                              "gh"),
                                 **AGENT_CLIS}.items():
        binary = setting(key, default) if key else default
        found = shutil.which(binary)
        tools[name] = {"binary": binary, "onPath": bool(found),
                       "path": found}
    return tools


def adapters() -> list[str]:
    names = []
    for base in (CORE / "adapters", LOCAL / "adapters"):
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if (child / "run").is_file() and child.name not in names:
                    names.append(child.name)
    return names


MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")


def declarations(app: Path) -> dict:
    """What the project says about itself, as text. None of it is run,
    and none of it becomes a command an agent may run: a script named
    `test` is a declaration someone must still review and approve."""
    found: dict = {"instructions": [], "ci": [], "scripts": [],
                   "note": "declared by the project; read as text, never "
                           "run, and not approved as agent commands"}
    for name in ("AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md",
                 ".github/copilot-instructions.md"):
        if (app / name).is_file():
            found["instructions"].append(name)
    workflows = app / ".github" / "workflows"
    if workflows.is_dir():
        found["ci"] += sorted(f".github/workflows/{p.name}"
                              for p in workflows.iterdir()
                              if p.suffix in (".yml", ".yaml"))[:50]
    for name in (".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile"):
        if (app / name).is_file():
            found["ci"].append(name)
    package = app / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")
                                 ).get("scripts") or {}
            for key in sorted(scripts)[:50]:
                found["scripts"].append({"file": "package.json",
                                         "name": str(key)})
        except (OSError, ValueError, AttributeError):
            found["scripts"].append({"file": "package.json",
                                     "name": "(unreadable)"})
    makefile = app / "Makefile"
    if makefile.is_file():
        try:
            lines = makefile.read_text(encoding="utf-8",
                                       errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines[:2000]:
            match = MAKE_TARGET.match(line)
            if match and not match.group(1).startswith("."):
                found["scripts"].append({"file": "Makefile",
                                         "name": match.group(1)})
    for name in ("pyproject.toml", "Cargo.toml", "go.mod", "Procfile",
                 "docker-compose.yml", "compose.yaml"):
        if (app / name).is_file():
            found["scripts"].append({"file": name, "name": "(declared)"})
    return found


def discover(app_arg: str | None, work_arg: str | None) -> dict:
    app = real(Path(app_arg)) if app_arg else None
    app_facts = repo_facts(app) if app else None
    canonical = None
    if app_facts and app_facts["repo"]:
        canonical = Path(app_facts["repo"]["toplevel"])
    workspace = TM.parent
    work = None
    if work_arg:
        work = real(Path(work_arg))
    elif setting("BOARD_WORK_ROOT"):
        work = real(Path(setting("BOARD_WORK_ROOT")))
    return {
        "tool": {"path": str(TM), "version": bench_version(),
                 "configured": ENV_FILE.is_file(),
                 "insideRepo": str(toplevel(TM)) if toplevel(TM) else None,
                 "settings": {k: setting(k) for k in (
                     "BOARD_APP_ROOT", "BOARD_WORK_ROOT", "BOARD_WORKTREES",
                     "BOARD_AGENT_ADAPTER", "BOARD_COMMIT_MOVES",
                     "BOARD_SYNC", "BOARD_PORT") if setting(k)}},
        "app": app_facts,
        "appRoot": str(canonical) if canonical else None,
        "work": repo_facts(work) if work else None,
        "installations": installations(canonical or app, workspace),
        "tooling": tooling(),
        "adapters": adapters(),
        "declarations": declarations(canonical) if canonical else None,
    }


# ── choices ───────────────────────────────────────────────────────────

def inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def overlaps(roles: dict[str, Path]) -> list[str]:
    """Every pair of roles that is one directory or nests inside the
    other. Each role must own its own tree: a work root inside the
    application would put tickets into its history, worktrees inside
    the work root would commit checkouts, and so on."""
    found = []
    names = list(roles)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pa, pb = real(roles[a]), real(roles[b])
            if pa == pb:
                found.append(f"the {a} and the {b} are the same directory "
                             f"({pa}) — each role needs its own")
            elif inside(pa, pb):
                found.append(f"the {a} ({pa}) is inside the {b} ({pb}) — "
                             f"choose locations that do not nest")
            elif inside(pb, pa):
                found.append(f"the {b} ({pb}) is inside the {a} ({pa}) — "
                             f"choose locations that do not nest")
    return found


def credentials_in(url: str) -> bool:
    return has_credentials(url)


def initialized_here(work: Path) -> bool:
    """Whether an earlier apply of this install created the work
    repository at `work` — read from its own progress record."""
    try:
        record = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (record.get("work") == str(real(work))
            and bool(record.get("initialized")))


REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")


def resolve(found: dict, args) -> dict:
    """The choices this run would act on, which of them are still
    missing, and every reason the layout is refused. Asks nothing."""
    required: list[dict] = []
    refusals: list[str] = []
    notes: list[str] = []
    held = env_file_values(ENV_FILE)
    choices: dict = {"mode": args.mode, "access": args.access or "loopback"}

    def need(choice: str, flag: str, question: str, options: list[str]):
        required.append({"choice": choice, "flag": flag,
                         "question": question, "options": options})

    # — the application —
    app = None
    if args.mode == "vendored":
        top = found["tool"]["insideRepo"]
        if not top or real(Path(top)) == TM:
            refusals.append(
                "vendored setup means bench unpacked inside the application "
                "(as <app>/.task-manager); this install is not inside "
                "another repository. Unpack it there, or use the default "
                "external mode")
        else:
            app = Path(top)
            if args.app and real(Path(args.app)) != app:
                refusals.append(f"--app names {real(Path(args.app))}, but "
                                f"this vendored install sits in {app}")
    elif not args.app:
        need("app", "--app PATH",
             "Which application should bench manage? (an absolute path to "
             "its git checkout)", [])
    elif found["app"] is None or not found["app"]["exists"]:
        refusals.append(f"the application {args.app} does not exist")
    elif not found["app"]["repo"]:
        refusals.append(
            f"the application {found['app']['path']} is not a git "
            f"repository. Bench manages a git checkout and does not create "
            f"one for you — `git init` it yourself first if that is right")
    else:
        app = Path(found["appRoot"])
        if not found["app"]["repo"]["isToplevel"]:
            notes.append(f"{found['app']['path']} is inside the repository "
                         f"{app}; that toplevel is the application root")
    if args.app and not Path(args.app).expanduser().is_absolute():
        refusals.append(f"--app {args.app} is not an absolute path — give "
                        f"the whole path")
    choices["app"] = str(app) if app else None

    if app and args.mode == "external":
        if inside(TM, app):
            refusals.append(
                f"this bench ({TM}) sits inside the application {app}. That "
                f"is a vendored install: pass --mode vendored, or unpack "
                f"bench outside the application for the external layout")
        configured = held.get("BOARD_APP_ROOT")
        if configured and real(Path(configured)) != app:
            refusals.append(
                f"this install already manages {configured}. One install "
                f"serves one application — unpack another bench beside it "
                f"for {app}")
        for other in found["installations"]:
            if other["role"] == "vendored in the application":
                notes.append(f"the application already carries a vendored "
                             f"bench at {other['path']}; it is left alone, "
                             f"and the two boards are separate")
            elif (other["role"] == "beside it in the workspace"
                  and other["app"] and real(Path(other["app"])) == app):
                notes.append(f"another bench at {other['path']} also names "
                             f"{app}; it is left alone")

    # — the work state and worktrees —
    workspace = TM.parent
    if args.mode == "external":
        work = (real(Path(args.work)) if args.work
                else real(Path(held["BOARD_WORK_ROOT"]))
                if held.get("BOARD_WORK_ROOT") else workspace / "work")
        if args.work and not Path(args.work).expanduser().is_absolute():
            refusals.append(f"--work {args.work} is not an absolute path")
        trees = (real(Path(args.worktrees)) if args.worktrees
                 else real(Path(held["BOARD_WORKTREES"]))
                 if held.get("BOARD_WORKTREES")
                 else workspace / "worktrees")
        if args.worktrees and not Path(args.worktrees).expanduser(
                ).is_absolute():
            refusals.append(f"--worktrees {args.worktrees} is not an "
                            f"absolute path")
        if held.get("BOARD_WORK_ROOT") and args.work and \
                real(Path(held["BOARD_WORK_ROOT"])) != work:
            notes.append(f"the work root changes from "
                         f"{held['BOARD_WORK_ROOT']} to {work}; the previous "
                         f"one is left exactly as it is")
        choices["work"], choices["worktrees"] = str(work), str(trees)
        if app:
            roles = {"application": app, "tool": TM, "work root": work,
                     "worktrees": trees}
            if inside(TM, app):
                del roles["tool"]   # refused above, in its own words
            refusals += overlaps(roles)
        facts = repo_facts(work)
        repo = facts["repo"]
        wanted = args.work_repo
        if not work.parent.is_dir():
            refusals.append(f"the work root's parent {work.parent} does not "
                            f"exist — bench creates one directory, not a "
                            f"path nobody has thought through")
        if repo and not repo["isToplevel"]:
            refusals.append(
                f"the work root {work} is inside the repository "
                f"{repo['toplevel']}, which it does not own. Tickets would "
                f"be committed into that history: choose a work root "
                f"outside it, or make the work root that repository's top")
        elif repo:
            ours = initialized_here(work)
            if wanted == "new" and ours:
                # The same command again: the repository is the one an
                # earlier run made, so this is a resumption, not a request
                # to start over.
                notes.append(f"{work} was initialized by an earlier setup; "
                             f"it is used as it is")
                choices["initializedHere"] = True
            elif wanted == "new":
                refusals.append(f"{work} is already a git repository; bench "
                                f"uses it as it is and never reinitializes "
                                f"it — pass --work-repo existing")
            elif wanted == "none":
                refusals.append(f"{work} is a git repository; --work-repo "
                                f"none cannot make it not one — pass "
                                f"--work-repo existing")
            choices["workRepo"] = "existing"
            if any((work / "tasks" / s).is_dir() for s in STAGES):
                notes.append(f"{work} already holds a board; its cards and "
                             f"template are kept as they are")
        else:
            if wanted == "existing":
                refusals.append(f"--work-repo existing, but {work} is not a "
                                f"git repository")
            elif wanted in ("new", "none"):
                choices["workRepo"] = wanted
            else:
                need("workRepo", "--work-repo new|none",
                     f"The tickets go in {work}. Make it a new local git "
                     f"repository (versioned tickets), or leave it a plain "
                     f"folder?", ["new", "none"])
            if facts["isDir"] and any(work.iterdir()):
                notes.append(f"{work} exists and is not empty; nothing in "
                             f"it is changed, the board's skeleton is added")
        choices["workRemotes"] = repo["remotes"] if repo else []
    else:
        choices["workRepo"] = "application"
        choices["workRemotes"] = []

    # — the agent —
    names = found["adapters"]
    if args.agent:
        if args.agent not in names:
            refusals.append(f"no agent adapter named '{args.agent}' is "
                            f"installed here — one of: {', '.join(names)}")
        choices["agent"] = args.agent
    elif ENV_FILE.is_file() and held.get("BOARD_AGENT_ADAPTER"):
        choices["agent"] = held["BOARD_AGENT_ADAPTER"]
    elif len(names) == 1:
        choices["agent"] = names[0]
    else:
        choices["agent"] = None
        need("agent", "--agent " + "|".join(names),
             "Which coding agent runs the board's headless jobs?", names)

    # — solo or team, and sync —
    if args.behaviour:
        choices["behaviour"] = args.behaviour
    elif ENV_FILE.is_file() and "BOARD_COMMIT_MOVES" in held:
        on = held.get("BOARD_COMMIT_MOVES", "").lower() not in (
            "", "0", "false", "no", "off")
        choices["behaviour"] = "team" if on else "solo"
    else:
        choices["behaviour"] = None
        need("behaviour", "--behaviour solo|team",
             "Solo (moves only rename files) or team (moves claim the card "
             "and commit themselves to the work state)?", ["solo", "team"])
    choices["sync"] = bool(args.sync)
    if args.sync and choices["behaviour"] == "solo":
        refusals.append("--sync shares the board through a remote, which "
                        "needs moves that commit: pass --behaviour team")
    if choices["behaviour"] == "team" and choices.get("workRepo") == "none":
        refusals.append("team behaviour commits every move, and --work-repo "
                        "none leaves nothing to commit to: choose "
                        "--work-repo new, or solo")

    # — the remote, separately and only when asked —
    remote = {"kind": "none"}
    existing = choices["workRemotes"]
    if args.create_remote and args.add_remote:
        refusals.append("--create-remote and --add-remote are two answers "
                        "to one question; pass one")
    elif args.create_remote or args.add_remote:
        if choices.get("workRepo") not in ("new", "existing"):
            refusals.append("a remote needs the work root to be a git "
                            "repository: pass --work-repo new")
        same_remote = (args.add_remote and len(existing) == 1
                       and existing[0]["name"] == "origin"
                       and git_read(Path(choices["work"]), "remote", "get-url", "origin")
                       == args.add_remote)
        if existing and not same_remote:
            refusals.append(
                f"the work repository already has the remote "
                f"'{existing[0]['name']}'; bench uses it and never adds or "
                f"replaces one beside it")
        if args.create_remote:
            if not REPO_NAME.match(args.create_remote):
                refusals.append(f"--create-remote {args.create_remote} is "
                                f"not OWNER/NAME or NAME")
            remote = {"kind": "create", "name": args.create_remote,
                      "visibility": "private", "as": "origin"}
        else:
            if credentials_in(args.add_remote):
                refusals.append(
                    "--add-remote was given a URL carrying credentials. It "
                    "is refused and not recorded: sign in with your "
                    "provider's own flow and pass the plain URL")
            remote = {"kind": "add", "url": redact(args.add_remote),
                      "as": "origin"}
    elif existing:
        remote = {"kind": "existing", "name": existing[0]["name"],
                  "url": existing[0]["url"]}
    choices["remote"] = remote
    choices["push"] = bool(args.push)
    if args.push and remote["kind"] == "none":
        refusals.append("--push needs a remote: an existing one, "
                        "--create-remote or --add-remote")
    if args.mode == "vendored" and (args.push or args.create_remote
                                    or args.add_remote or args.work
                                    or args.work_repo):
        refusals.append("vendored setup keeps the tickets in the "
                        "application's own repository; the work-root, "
                        "remote and push options belong to the external "
                        "layout")
    return {"choices": choices, "required": required,
            "refusals": refusals, "notes": notes,
            "_add_remote": args.add_remote}


# ── the plan ──────────────────────────────────────────────────────────

def port() -> str:
    return setting("BOARD_PORT", DEFAULT_PORT)


def settings_answers(choices: dict, fresh: bool) -> dict[str, str]:
    answers = {"BOARD_AGENT_ADAPTER": choices.get("agent") or "",
               "BOARD_COMMIT_MOVES": "1" if choices.get(
                   "behaviour") == "team" else "",
               "BOARD_SYNC": "1" if choices.get("sync") else ""}
    if choices.get("remote", {}).get("kind") in ("create", "add"):
        answers["BOARD_STATE_GIT_REMOTE"] = "origin"
    elif choices.get("remote", {}).get("kind") == "existing":
        answers["BOARD_STATE_GIT_REMOTE"] = choices["remote"]["name"]
    if fresh:
        # The example's default is a guess about somebody else's project.
        # Bench does not infer which commands are safe from a filename, so
        # a fresh board starts with none and the guide says where to add
        # them.
        answers["BOARD_AGENT_COMMANDS"] = ""
    return answers


def settings_changes(answers: dict[str, str]) -> list[dict]:
    current = env_file_values(ENV_FILE)
    base = current if ENV_FILE.is_file() else env_file_values(
        CORE / ".env.example")
    return [{"key": k, "from": base.get(k, ""), "to": v}
            for k, v in answers.items() if base.get(k, "") != v]


def install_command(choices: dict) -> list[str]:
    command = [sys.executable, str(TM / "install.py")]
    if choices["mode"] == "external":
        command += ["--app", choices["app"], "--work", choices["work"],
                    "--worktrees", choices["worktrees"]]
    return command


def plan(found: dict, resolved: dict) -> list[dict]:
    c = resolved["choices"]
    ops: list[dict] = []

    def op(id_: str, kind: str, summary: str, command=None,
           required=True, network=False):
        ops.append({"id": id_, "kind": kind, "summary": summary,
                    "command": command, "required": required,
                    "network": network})

    if c["mode"] == "external":
        work = Path(c["work"])
        if not work.exists():
            op("work-dir", "filesystem", f"create the directory {work}")
        if c["workRepo"] == "new":
            op("work-git-init", "git",
               f"initialize a new local git repository in {work} (branch "
               f"main) — the tickets' own history, not the application's",
               ["git", "-C", str(work), "init", "-b", "main"])
        remote = c["remote"]
        if remote["kind"] == "create":
            gh = setting("BOARD_GH_BIN", "gh")
            op("work-remote-create", "network",
               f"create the PRIVATE GitHub repository {remote['name']} and "
               f"add it to {work} as 'origin' (nothing is pushed by this "
               f"step)",
               [gh, "repo", "create", remote["name"], "--private",
                "--source", str(work), "--remote", "origin"],
               required=False, network=True)
        elif remote["kind"] == "add":
            op("work-remote-add", "git",
               f"add the remote 'origin' ({remote['url']}) to {work}",
               ["git", "-C", str(work), "remote", "add", "origin",
                remote["url"]], required=False)
    command = install_command(c)
    op("install", "install",
       "run install.py — " + ("record the layout in manager/local/.env, "
                              "create the work state's skeleton at the work "
                              "root, and generate the agent hooks under the "
                              "tool's own state; nothing is written into the "
                              "application"
                              if c["mode"] == "external" else
                              "wire the project and create the work state "
                              "inside this install"),
       command)
    fresh = not ENV_FILE.is_file()
    changes = settings_changes(settings_answers(c, fresh))
    if changes:
        op("settings", "settings",
           "write the chosen settings into manager/local/.env: "
           + ", ".join(f"{ch['key']}={ch['to'] or '(empty)'}"
                       for ch in changes))
    if c["mode"] == "external" and (c["workRepo"] == "new"
                                    or c.get("initializedHere")):
        op("work-first-commit", "git",
           "commit the new board's skeleton (tasks/, plans/, reference/) "
           "as the work repository's first commit",
           ["git", "-C", c["work"], "commit", "-m",
            "bench: initialize work state"], required=False)
    if c.get("push"):
        name = c["remote"].get("name") if c["remote"]["kind"] == \
            "existing" else "origin"
        base = setting("BOARD_STATE_BASE", "main")
        op("work-first-push", "network",
           f"push the work repository's {base} to '{name}' — tickets only; "
           f"the application is never pushed by setup",
           ["git", "-C", c["work"], "push", "-u", name, f"HEAD:{base}"],
           required=False, network=True)
    return ops


# ── guide items: what is left for the browser ─────────────────────────

def guide_items(found: dict, resolved: dict, results: list[dict]) -> list:
    c = resolved["choices"]
    items = []

    def item(id_: str, title: str, how: str) -> None:
        items.append({"id": id_, "title": title, "how": how})

    outcome = {r["id"]: r for r in results}
    agent = c.get("agent")
    if agent in AGENT_CLIS and not found["tooling"][agent]["onPath"]:
        item("agent-cli-missing",
             f"Install the {agent} CLI",
             f"The {agent} adapter is chosen, but '"
             f"{found['tooling'][agent]['binary']}' is not on PATH. Installing "
             f"a CLI is separate from choosing an adapter: install it, then "
             f"sign in through its own login flow in your terminal. Bench "
             f"never asks for a token.")
    if c["mode"] == "external" and agent == "opencode":
        item("adapter-external-wiring",
             "Live sessions for opencode",
             "opencode discovers its plugin inside the project it runs in, "
             "so bench cannot wire your own sessions without writing into "
             "the application. Headless jobs still run; install.py names "
             "both ways forward.")
    item("agent-smoke", "Prove the agent CLI runs under bench's policy",
         f"Optional, and it spends tokens: python3 {CORE}/board.py "
         f"--doctor --real-agent --approve-real-agent. Setup checked only "
         f"that the binary is on PATH.")
    commands = setting("BOARD_AGENT_COMMANDS")
    if not commands.strip(" ,"):
        item("agent-commands", "Say which commands agents may run",
             "BOARD_AGENT_COMMANDS is empty, so agents run no project "
             "commands. Bench does not infer safe commands from file names: "
             "set it in manager/local/.env, or register named checks in "
             "Settings → Checks.")
    declared = (found.get("declarations") or {}).get("scripts") or []
    if declared:
        item("declared-scripts", "Review what the project declares",
             "Discovery found scripts and CI declarations ("
             + ", ".join(sorted({d['file'] for d in declared})) +
             "). None was run or approved; open Settings → Checks to "
             "register the ones you trust.")
    if c.get("workRepo") == "none":
        item("work-unversioned", "Version the tickets when you want to",
             f"The work root is a plain folder. `git init` it, then re-run "
             f"bootstrap with --work-repo existing.")
    remote = c.get("remote", {})
    created = outcome.get("work-remote-create")
    if remote.get("kind") == "create" and created and \
            created["outcome"] != "done":
        gh = found["tooling"]["gh"]
        item("remote-not-created", "The tickets' remote was not created",
             ("gh is not on PATH. Install it and run `gh auth login` in "
              "your own terminal, " if not gh["onPath"] else
              "GitHub refused or could not be reached (see the operation "
              "result). ")
             + "The local board works without it; re-run the same apply "
               "to try again.")
    if remote.get("kind") == "none" and c.get("workRepo") in ("new",
                                                               "existing"):
        item("remote-none", "Share the tickets later, if you want to",
             "The work repository has no remote, which is fine for one "
             "person. --create-remote OWNER/NAME (private) or --add-remote "
             "URL adds one; --push and --sync are separate choices again.")
    first = outcome.get("work-first-commit")
    if first and first["outcome"] == "failed":
        item("first-commit-failed", "The board's first commit was not made",
             "Usually git has no user.name/user.email for the work "
             "repository. Set them there, then re-run the same apply; the "
             "board itself works either way.")
    pushed = outcome.get("work-first-push")
    if pushed and pushed["outcome"] != "done":
        item("push-failed", "The first push did not land",
             "Nothing was forced. Check access to the remote, then re-run "
             "the same apply.")
    if c.get("sync") and remote.get("kind") == "none":
        item("sync-without-remote", "Sync has nothing to sync through",
             "BOARD_SYNC is on with no remote; the board shows a 'sync "
             "stalled' chip until one is added.")
    if c.get("access") == "remote":
        item("remote-access", "Reach the board from another machine",
             f"The board listens on 127.0.0.1 only, and setup does not "
             f"change that. From the other machine: ssh -L "
             f"{port()}:127.0.0.1:{port()} <this host>, then open "
             f"http://127.0.0.1:{port()}/ there.")
    app = found.get("app") or {}
    repo = (app.get("repo") or {})
    if repo and not repo.get("remotes"):
        item("app-no-remote", "The application has no remote",
             "Cards still run and branch; pull requests need a remote and "
             "gh. Setup pushes nothing in the application either way.")
    elif repo and not found["tooling"]["gh"]["onPath"]:
        item("gh-missing", "Pull requests need gh",
             "Without gh the board works local-only and says so on each "
             "card's plan.")
    if repo and repo.get("dirty"):
        item("app-dirty", "The application has uncommitted changes",
             f"{repo['dirty']} paths. Bench never touches them; task "
             f"branches start from commits.")
    item("configuration-check", "Open the Configuration check",
         "The ⚕ configuration chip in the board's header lists what is "
         "configured, what is not and what to do next.")
    return items


# ── apply ─────────────────────────────────────────────────────────────

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command: list[str], **kw) -> subprocess.CompletedProcess:
    env = _git_env()
    env.update(kw.pop("env", {}))
    return subprocess.run(command, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=env,
                          timeout=kw.pop("timeout", 300), **kw)


def tail(proc: subprocess.CompletedProcess, lines: int = 12) -> str:
    text = (proc.stdout or "") + (proc.stderr or "")
    return redact("\n".join(text.strip().splitlines()[-lines:]))


def load_install():
    spec = importlib.util.spec_from_file_location("bench_install",
                                                  TM / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def perform(op: dict, resolved: dict, add_remote: str | None) -> dict:
    c = resolved["choices"]
    result = {"id": op["id"], "outcome": "done", "detail": ""}
    try:
        if op["id"] == "work-dir":
            work = Path(c["work"])
            if work.is_dir():
                result.update(outcome="already", detail="already there")
            else:
                work.mkdir()
        elif op["id"] == "work-git-init":
            work = Path(c["work"])
            if toplevel(work) == real(work):
                result.update(outcome="already",
                              detail="already a repository")
            else:
                proc = run(op["command"])
                if proc.returncode:
                    result.update(outcome="failed", detail=tail(proc))
        elif op["id"] == "work-remote-create":
            work = Path(c["work"])
            if git_read(work, "remote", "get-url", "origin"):
                result.update(outcome="already",
                              detail="the remote 'origin' is already there")
            elif not shutil.which(op["command"][0]):
                result.update(outcome="unavailable",
                              detail=f"{op['command'][0]} is not on PATH")
            else:
                proc = run(op["command"], timeout=120)
                if proc.returncode:
                    result.update(outcome="failed", detail=tail(proc))
        elif op["id"] == "work-remote-add":
            work = Path(c["work"])
            have = git_read(work, "remote", "get-url", "origin")
            if have is not None:
                same = have == (add_remote or "")
                result.update(outcome="already" if same else "refused",
                              detail=("already there" if same else
                                      "a different 'origin' is already "
                                      "there; bench does not replace it"))
            else:
                command = op["command"][:-1] + [add_remote]
                proc = run(command)
                if proc.returncode:
                    result.update(outcome="failed", detail=tail(proc))
        elif op["id"] == "install":
            fresh = not ENV_FILE.is_file()
            answers = settings_answers(c, fresh)
            proc = run(op["command"], env=answers, timeout=600)
            result["detail"] = tail(proc, 30)
            if proc.returncode:
                result["outcome"] = "failed"
            resolved["_fresh"] = fresh
        elif op["id"] == "settings":
            install = load_install()
            answers = settings_answers(c, resolved.get("_fresh", False))
            base = ENV_FILE.read_text(encoding="utf-8") \
                if ENV_FILE.is_file() else \
                (CORE / ".env.example").read_text(encoding="utf-8")
            updated = install.substitute(base, answers)
            if updated == base:
                result.update(outcome="already", detail="already set")
            else:
                ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
                ENV_FILE.write_text(updated, encoding="utf-8")
        elif op["id"] == "work-first-commit":
            work = Path(c["work"])
            if git_read(work, "rev-parse", "--verify", "--quiet", "HEAD"):
                result.update(outcome="already",
                              detail="the repository already has commits")
            else:
                paths = [p for p in ("tasks", "plans", "reference")
                         if (work / p).exists()]
                proc = run(["git", "-C", str(work), "add", "--", *paths])
                if proc.returncode == 0:
                    proc = run(op["command"] + ["--", *paths])
                if proc.returncode:
                    result.update(outcome="failed", detail=tail(proc))
        elif op["id"] == "work-first-push":
            proc = run(op["command"], timeout=180)
            if proc.returncode:
                result.update(outcome="failed", detail=tail(proc))
    except (OSError, subprocess.SubprocessError) as failure:
        result.update(outcome="failed", detail=redact(str(failure)))
    return result


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(redact_tree(document), indent=2,
                                    sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


# ── the handoff ───────────────────────────────────────────────────────

def handoff(status: str, found: dict, resolved: dict | None,
            ops: list[dict], results: list[dict]) -> dict:
    resolved = resolved or {"choices": {}, "required": [], "refusals": [],
                            "notes": []}
    c = resolved["choices"]
    url = f"http://127.0.0.1:{port()}/"
    done = {r["id"]: r for r in results}
    doc = {
        "kind": HANDOFF_KIND, "v": HANDOFF_VERSION, "at": now(),
        "bench": bench_version(), "status": status,
        "mode": c.get("mode"),
        "roles": {"tool": str(TM), "app": c.get("app"),
                  "work": c.get("work") if c.get("mode") == "external"
                  else (str(TM) if c.get("mode") else None),
                  "worktrees": c.get("worktrees")},
        "choices": {k: v for k, v in c.items()
                    if k not in ("workRemotes",)},
        "required": resolved["required"],
        "refusals": resolved["refusals"],
        "notes": resolved["notes"],
        "operations": [{**{k: o[k] for k in ("id", "kind", "summary",
                                            "required", "network")},
                        "outcome": done.get(o["id"], {}).get(
                            "outcome", "not-run"),
                        "detail": done.get(o["id"], {}).get("detail", "")}
                       for o in ops],
        "guide": (guide_items(found, resolved, results)
                  if c.get("app") else []),
        "board": {"url": url, "bind": "127.0.0.1",
                  "start": str(TM / "start.sh"),
                  "listening": False},
        "continuation": {
            "board": url,
            "then": "Open the board, then the ⚕ configuration chip in its "
                    "header; Settings holds the rest of the guide.",
            # task 88: the installed guide that continues this handoff —
            # the same reading `board.py --setup-guide` prints
            "setup": url + "#setup",
            "docs": DOCS_URL,
        },
        "redaction": "No credential, token or environment value is "
                     "recorded. Remote URLs lose any user information. "
                     "Paths are this machine's and the file stays in "
                     "manager/local/state/, which is never committed.",
    }
    return redact_tree(doc)


def print_human(doc: dict, found: dict | None, command: str) -> None:
    if found and command == "inspect":
        app = found.get("app")
        say(f"bench {found['tool']['version']} at {found['tool']['path']}")
        if app:
            repo = app.get("repo")
            if repo:
                say(f"application: {repo['toplevel']}  branch "
                    f"{repo['branch'] or '(detached)'}, "
                    f"{repo['dirty']} uncommitted path(s), remotes: "
                    + (", ".join(f"{r['name']} {r['url']}"
                                 for r in repo["remotes"]) or "none"))
            else:
                say(f"application: {app['path']} — not a git repository")
        for inst in found["installations"]:
            say(f"installation: {inst['path']} ({inst['role']}, "
                f"{inst['version']}"
                + (f", manages {inst['app']}" if inst["app"] else "") + ")")
        tools = found["tooling"]
        say("tooling on PATH: " + ", ".join(
            f"{k} {'yes' if v['onPath'] else 'no'}" for k, v in
            tools.items()) + " (looked up, not run)")
        say("agent adapters installed: " + ", ".join(found["adapters"]))
        dec = found.get("declarations")
        if dec:
            say("project declares: instructions "
                + (", ".join(dec["instructions"]) or "none") + "; CI "
                + (", ".join(dec["ci"]) or "none") + "; scripts "
                + (", ".join(f"{d['file']}:{d['name']}"
                             for d in dec["scripts"]) or "none")
                + " — read as text, never run")
        print()
    roles = doc["roles"]
    if doc.get("mode"):
        say(f"mode: {doc['mode']}")
        for role in ("app", "tool", "work", "worktrees"):
            if roles.get(role):
                say(f"  {role:<10} {roles[role]}")
    for note in doc["notes"]:
        say(f"note: {note}")
    if doc["refusals"]:
        say("\nRefused — nothing will be written:")
        for refusal in doc["refusals"]:
            say(f"  - {refusal}")
    if doc["required"]:
        say("\nStill to choose:")
        for need in doc["required"]:
            say(f"  - {need['question']}  ({need['flag']})")
    if doc["operations"]:
        say("\nOperations" + (" (preview — nothing written):"
                              if command != "apply" else ":"))
        for o in doc["operations"]:
            mark = "" if command != "apply" else f"[{o['outcome']}] "
            extra = "" if o["required"] else " (optional)"
            say(f"  {mark}{o['summary']}{extra}")
            if command == "apply" and o["outcome"] not in (
                    "done", "already", "not-run") and o["detail"]:
                for line in o["detail"].splitlines()[-6:]:
                    say(f"      {line}")
    if doc["guide"] and command != "inspect":
        say("\nLeft for the guide:")
        for g in doc["guide"]:
            say(f"  - {g['title']}: {g['how']}")
    say(f"\nstatus: {doc['status']}")
    if command == "apply" and doc["status"] in ("complete", "incomplete"):
        say(f"board: {doc['board']['url']}  (start it with "
            f"{doc['board']['start']})")
        say(f"handoff: {HANDOFF_FILE}")
        say(f"setup guide: {doc['continuation']['setup']}  (or "
            f"{CORE / 'board.py'} --setup-guide)")
    say(f"guide: {doc['continuation']['docs']}")


# ── the command line ──────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="Set up a workspace for bench and hand off to the "
                    "board. inspect and plan write nothing.")
    subs = top.add_subparsers(dest="command", required=True)
    for name, text in (("inspect", "discover, read-only"),
                       ("plan", "resolve choices and preview, read-only"),
                       ("apply", "make the previewed operations")):
        sub = subs.add_parser(name, help=text, description=text)
        sub.add_argument("--app", help="the application's git checkout "
                                       "(absolute path)")
        sub.add_argument("--mode", choices=("external", "vendored"),
                         default="external",
                         help="external (recommended, the default) or "
                              "vendored (bench inside the application)")
        sub.add_argument("--work", help="the work root for tickets "
                                        "(absolute; default <workspace>/work)")
        sub.add_argument("--worktrees",
                         help="where task worktrees go (absolute; default "
                              "<workspace>/worktrees)")
        sub.add_argument("--json", action="store_true",
                         help="print the handoff document as JSON")
        if name == "inspect":
            continue
        sub.add_argument("--work-repo", choices=("new", "existing", "none"),
                         help="new: git init the work root; existing: it "
                              "already is one; none: a plain folder")
        sub.add_argument("--agent", help="the agent adapter for headless "
                                         "jobs (e.g. claude)")
        sub.add_argument("--behaviour", choices=("solo", "team"),
                         help="team: moves claim and commit themselves")
        sub.add_argument("--sync", action="store_true",
                         help="also share the board through the work "
                              "repository's remote (BOARD_SYNC)")
        sub.add_argument("--access", choices=("loopback", "remote"),
                         help="loopback (default) or remote: guidance for "
                              "an SSH tunnel; the board never binds beyond "
                              "127.0.0.1")
        sub.add_argument("--create-remote", metavar="OWNER/NAME",
                         help="create a PRIVATE GitHub repository for the "
                              "tickets with gh")
        sub.add_argument("--add-remote", metavar="URL",
                         help="add an existing remote URL to a work "
                              "repository that has none")
        sub.add_argument("--push", action="store_true",
                         help="push the work repository's main to its "
                              "remote once")
        if name == "apply":
            sub.add_argument("--yes", action="store_true",
                             help="approve the previewed operations "
                                  "without a prompt")
            sub.add_argument("--start", action="store_true",
                             help="start the board (loopback) when setup "
                                  "is complete")
    return top


def ask_missing(resolved: dict, args) -> bool:
    """On a terminal, ask each missing choice once. Returns whether all
    were answered; Ctrl-D stops asking."""
    for need in resolved["required"]:
        options = need["options"]
        hint = f" [{'/'.join(options)}]" if options else ""
        try:
            answer = input(f"{need['question']}{hint}: ").strip()
        except EOFError:
            print()
            return False
        if options and answer not in options:
            say(f"  '{answer}' is not one of {', '.join(options)}")
            return False
        if not answer:
            return False
        setattr(args, {"app": "app", "workRepo": "work_repo",
                       "agent": "agent",
                       "behaviour": "behaviour"}[need["choice"]], answer)
    return True


def emit(doc: dict, found: dict, args) -> None:
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        print_human(doc, found, args.command)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    found = discover(args.app, args.work)
    if args.command == "inspect":
        doc = handoff("inspected", found, None, [], [])
        doc["discovery"] = redact_tree(found)
        emit(doc, found, args)
        return EXIT_OK

    resolved = resolve(found, args)
    interactive = args.command == "apply" and sys.stdin.isatty()
    if resolved["required"] and not resolved["refusals"] and interactive:
        if ask_missing(resolved, args):
            found = discover(args.app, args.work)
            resolved = resolve(found, args)
    if resolved["refusals"]:
        emit(handoff("refused", found, resolved, [], []), found, args)
        return EXIT_FAILED
    if resolved["required"]:
        emit(handoff("needs-choices", found, resolved, [], []), found, args)
        return EXIT_NEEDS
    ops = plan(found, resolved)
    if args.command == "plan":
        emit(handoff("planned", found, resolved, ops, []), found, args)
        return EXIT_OK

    if not args.yes:
        if not interactive:
            emit(handoff("needs-approval", found, resolved, ops, []), found,
                 args)
            if not args.json:
                say("Re-run with --yes to approve these operations.")
            return EXIT_NEEDS
        print_human(handoff("planned", found, resolved, ops, []), found,
                    "plan")
        try:
            reply = input("\nApply these operations? [y/N]: ").strip()
        except EOFError:
            reply = ""
        if reply.lower() not in ("y", "yes"):
            say("Nothing was written.")
            return EXIT_NEEDS

    previous = None
    if PROGRESS_FILE.is_file():
        try:
            previous = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    if previous and previous.get("status") in ("running", "interrupted"):
        say(f"resuming a setup interrupted at {previous.get('at')} — every "
            f"step checks what is already there first")
    results: list[dict] = []
    c = resolved["choices"]
    progress = {"v": 1, "status": "running", "at": now(), "results": results,
                "work": c.get("work"),
                "initialized": bool(c.get("initializedHere"))}
    status = "complete"
    try:
        for o in ops:
            write_json(PROGRESS_FILE, {**progress, "current": o["id"]})
            result = perform(o, resolved, resolved.get("_add_remote"))
            results.append(result)
            if o["id"] == "work-git-init" and result["outcome"] in (
                    "done", "already"):
                progress["initialized"] = True
            if o["required"] and result["outcome"] in ("failed", "refused"):
                status = "incomplete"
                break
    except KeyboardInterrupt:
        status = "interrupted"
    progress.update(status=status, at=now())
    write_json(PROGRESS_FILE, progress)
    doc = handoff(status, found, resolved, ops, results)
    write_json(HANDOFF_FILE, doc)
    emit(doc, found, args)
    if status == "interrupted":
        say("Interrupted. Re-run the same command to continue; nothing "
            "done so far is undone or repeated.")
        return EXIT_INTERRUPTED
    if status != "complete":
        return EXIT_FAILED
    if args.start:
        sys.stdout.flush()
        os.execv("/usr/bin/env", ["env", "bash", str(TM / "start.sh"),
                                  "--no-open"])
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted — nothing further was done.")
        sys.exit(EXIT_INTERRUPTED)
