"""Seeding the Brain, step one (task 192): which sources a person approves,
and what bench found when it read exactly those.

Seeding a Brain from an existing repository is three separate acts:
inspecting approved sources (this module), generating a private draft
from them (#193), and a person reviewing and applying it (#194). This
module is the first, and it is useful on its own — a static inventory of
what the project's application and work repositories hold — before any
generation exists.

- **Identities first.** `reading()` names the application checkout and the
  work root the board actually resolved, each with its repository, branch,
  commit and whether it has uncommitted work, the layout (vendored,
  source-hosted, external), whether the two are one repository or two, and
  the Brain destination (`brain/` and the handbook record) with their
  revisions. A mapping `config.problems()` refuses, a work root in no
  repository, an external layout whose work state resolves into the
  application's repository, or a `brain/` that is a link out is a
  **refusal**: nothing is listed or inspected, no repository is created and
  nothing falls back to application git.
- **Candidates are names, not contents.** The listing is `git ls-files` of
  the application (tracked, and untracked files `.gitignore` does not
  hide) and the work root's `tasks/`, `plans/`, `reference/` and `brain/`,
  each with its size from `lstat`. Nothing is opened. Credential-shaped
  names, `.env*`, private transcripts and raw logs, dependency trees,
  binaries, bench's own installation, links and oversized files are listed
  as excluded with why, and cannot be selected.
- **Inspect reads only what was chosen.** `inspect(selection)` reads the
  chosen files — or line slices of them — through no link, inside their
  root, and records each one's bytes, sha256, git blob and whether it is
  the committed version, modified or untracked. A file that is not there
  is **absent**; one that is there and cannot be read is **inaccessible**;
  neither is ever guessed at. A file whose content looks like it holds a
  credential is excluded and its digest withheld.
- **The manifest is a contract, not a permission.** `inspect` returns a
  versioned, bounded manifest (`v: 1`, `kind: bench-brain-seed-manifest`)
  with the repository identities, every item's hashes and sizes, the
  exclusions, the chosen handbook sections and treatments, the destination
  revisions and a digest over all of it. #193 and #194 hold their work to
  it (`drift`). It authorizes nothing: generating a draft is a separate,
  explicit approval.

Reading and inspecting run no project code, script, package manager,
check or agent, fetch nothing, create no card and write nothing — not to
either repository, not to bench's own state. The only processes are local
git reads through `gitrepo` (`config.git_run`'s hardened reads). Source
text is untrusted data: it is hashed and sized here, never interpreted.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

import brain
import config
import gitrepo
import handbook
import redact

CONTRACT = 1
KIND = "bench-brain-seed-manifest"
ROOTS = ("app", "work")

# bounds: every one is said in the reading, so a truncation is never silent
MAX_LISTED = 4000              # candidates listed per root
MAX_SELECTED = 200             # items one inspection may read
MAX_FILE_BYTES = 512 * 1024    # one file read whole
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_EXCLUDES = 40
MAX_PATTERN = 200
MAX_PATH = 300
MAX_DEFAULTS = 40              # candidates selected by default
GIT_BATCH = 100

TREATMENTS = {
    "cite": "cite its facts in place — no wholesale copying, no replacement of the file",
    "policy": "quote it as existing human policy — never invent or extend an agreement",
    "evidence": "static evidence of how the project is built — not proof that it runs or "
                "is deployed",
    "background": "context only — it may inform the draft, and is never cited as a fact",
}
CATEGORIES = {
    "instructions": ("Project instructions", "policy"),
    "docs": ("Documentation", "cite"),
    "config": ("Non-secret configuration", "evidence"),
    "code": ("Source code", "evidence"),
    "other": ("Other files", "background"),
    "card": ("Cards", "cite"),
    "archive": ("Archived cards", "background"),
    "plan": ("Plans", "cite"),
    "reference": ("Reference material", "cite"),
    "brain": ("Existing Brain entries", "cite"),
}

# What is never read, whatever is selected. Pattern matching: it lowers the
# risk and does not prove that a file it lets through holds no secret.
SECRET_NAME = re.compile(
    r"(^|/)\.env($|[._-])|(^|/)env\.(local|prod|production)$"
    r"|(^|/)(\.netrc|\.npmrc|\.pypirc|\.htpasswd|\.pgpass|\.git-credentials)$"
    r"|(^|/)id_(rsa|dsa|ecdsa|ed25519)"
    r"|\.(pem|key|p12|pfx|jks|keystore|asc|gpg|kdbx|ovpn)$"
    r"|(^|[/_.-])(secret|secrets|credential|credentials|password|passwords|passwd"
    r"|token|tokens|apikey|api-key|private-key)([/_.-]|$)",
    re.IGNORECASE)
DEPENDENCY_DIRS = {"node_modules", "bower_components", "jspm_packages", "vendor", ".venv",
                   "venv", "virtualenv", "site-packages", "__pycache__", ".tox", ".nox",
                   ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", "Pods",
                   ".bundle", ".cargo", "target", "dist", "build", ".next", ".nuxt",
                   ".svelte-kit", ".parcel-cache", ".cache", "coverage", ".terraform"}
PRIVATE_DIRS = {".git", ".claude", ".codex", ".opencode", ".cursor", ".aider", ".idea",
                ".vscode", "transcripts", "logs", "log", "sessions", ".task-manager"}
LOG_NAME = re.compile(r"\.(log|log\.\d+|jsonl|ndjson|trace|har|dump|sqlite3?|db)$|(^|/)"
                      r"(npm-debug|yarn-error|pnpm-debug)", re.IGNORECASE)
BINARY_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".pdf",
                 ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".jar", ".war",
                 ".so", ".dll", ".dylib", ".exe", ".bin", ".class", ".pyc", ".pyo", ".o",
                 ".a", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4",
                 ".mov", ".avi", ".webm", ".wav", ".ogg", ".flac", ".psd", ".sketch", ".fig",
                 ".lock"}

INSTRUCTION_NAMES = {"agents.md", "claude.md", "contributing.md", "contributing",
                     "contributing.rst", "code_of_conduct.md", "governance.md", "security.md"}
DOC_SUFFIX = {".md", ".markdown", ".rst", ".txt", ".adoc", ".org"}
CONFIG_NAMES = {"package.json", "pyproject.toml", "setup.cfg", "setup.py", "makefile",
                "dockerfile", "procfile", "cargo.toml", "go.mod", "gemfile", "composer.json",
                "tsconfig.json", "requirements.txt", "tox.ini", "justfile", "taskfile.yml"}
CONFIG_SUFFIX = {".toml", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".json", ".tf", ".nix"}
CODE_SUFFIX = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb",
               ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php",
               ".scala", ".clj", ".ex", ".exs", ".erl", ".hs", ".lua", ".sh", ".bash",
               ".sql", ".vue", ".svelte", ".css", ".scss", ".html", ".dart", ".r", ".jl"}

TITLES = {sid: title for sid, title, _ in handbook.SECTIONS}
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩‎‏]")
SECTION_HINTS = {
    "overview": ("docs", "instructions"),
    "how-it-works": ("code", "config", "docs"),
    "ways-of-working": ("instructions",),
    "direction": ("card", "plan", "brain", "reference"),
    "operations": (),
}
OPERATIONS_HINT = re.compile(
    r"(deploy|operat|runbook|install|release|docker|compose|k8s|kubernetes|helm|terraform|"
    r"ansible|ci|workflow|procfile|infra|ops|monitor|backup|recover)", re.IGNORECASE)


class Refused(ValueError):
    """An inspection bench will not make, with the status the API answers
    and a `code` the page and #193 branch on."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid", **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra


# ── identities and the mapping ─────────────────────────────────────────


def _real(path: Path) -> Path:
    try:
        return Path(os.path.realpath(path))
    except OSError:
        return path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _git_out(repo: Path, *args: str) -> str | None:
    found = gitrepo.git(repo, *args, timeout=20)
    return found.stdout if found.returncode == 0 else None


def _repo_identity(repo: Path | None, prefix: str = "") -> dict:
    """One repository as a reading names it: its top level, branch, HEAD and
    how many paths under `prefix` differ from it. Local git reads only."""
    if repo is None:
        return {"repository": None, "branch": None, "commit": None, "dirty": None}
    commit = (_git_out(repo, "rev-parse", "--verify", "--quiet", "HEAD") or "").strip() or None
    branch = (_git_out(repo, "symbolic-ref", "--short", "-q", "HEAD") or "").strip() or None
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=normal"]
    if prefix:
        args += ["--", prefix]
    status = _git_out(repo, *args)
    dirty = None if status is None else len([s for s in status.split("\0") if s.strip()])
    return {"repository": str(repo), "name": repo.name, "branch": branch,
            "commit": commit, "dirty": dirty}


def _layout() -> str:
    if config.EXTERNAL:
        return "external"
    if _real(config.TM_ROOT) == _real(gitrepo.app()):
        return "source-hosted"
    return "vendored"


def _work_prefix(state: Path | None) -> str:
    if state is None:
        return ""
    try:
        rel = _real(config.work_root()).relative_to(_real(state)).as_posix()
    except ValueError:
        return ""
    return "" if rel == "." else rel + "/"


def _destination() -> dict:
    """The Brain a later Apply would write: `brain/` under the work root and
    the handbook record, each with the revision a write would be held to."""
    read = brain.reading()
    scan = read["scan"]
    record = handbook.read_record()
    return {"brain": {"path": f"{brain.DIRECTORY}/", "state": scan["state"],
                      "why": scan["why"], "revision": read["revision"],
                      "entries": len(read["entries"])},
            "handbook": {"path": record["file"], "state": record["state"],
                         "revision": record.get("revision") or "none",
                         "why": record.get("why"),
                         "sections": {sid: len(ids) for sid, ids in
                                      (record.get("sections") or {}).items()}}}


def identities() -> dict:
    """The mapping a seed would use, read back from what the board resolved
    — never what was requested — and every reason it is refused. Reads local
    git metadata and directory state; creates and writes nothing."""
    problems: list[dict] = []
    for text in config.problems():
        problems.append({"code": "layout-invalid", "why": text,
                         "remedy": "fix the layout in manager/local/.env and restart the board"})
    app = gitrepo.app()
    work = config.work_root()
    app_real, work_real = _real(app), _real(work)
    app_top = config.git_root(app) if app.is_dir() else None
    if not app.is_dir():
        problems.append({"code": "app-missing", "why": f"the application root {app} is not a "
                         "directory", "remedy": "set BOARD_APP_ROOT to the application's "
                         "checkout"})
    elif app_top is None:
        problems.append({"code": "app-not-repository", "why": f"{app} is not in a git "
                         "repository", "remedy": "name the application's git checkout"})
    if not work.is_dir():
        problems.append({"code": "work-missing", "why": f"the work root {work} is not a "
                         "directory", "remedy": "run install.py to lay down the work state"})
    state = gitrepo.state()
    shared = gitrepo.shares_state()
    layout = _layout()
    if work.is_dir() and state is None:
        problems.append({"code": "work-not-repository",
                         "why": f"the work root {work} is in no git repository, so a seeded "
                                "Brain could not be committed — and bench never commits it "
                                "to the application instead",
                         "remedy": "run `git init` in the work root (bench does not create "
                                   "repositories), then reload"})
    if layout == "external" and (shared or (state is not None and app_top is not None
                                            and _real(state) == _real(app_top))):
        problems.append({"code": "work-in-app-repository",
                         "why": "the work state resolves into the application's repository; "
                                "an external layout keeps them apart",
                         "remedy": "give BOARD_WORK_ROOT a repository of its own"})
    if layout == "external" and (_inside(work_real, app_real) or _inside(app_real, work_real)):
        if not any(p["code"] == "layout-invalid" for p in problems):
            problems.append({"code": "roots-overlap",
                             "why": "the application and the work root contain one another",
                             "remedy": "keep the two roots apart"})
    dest = _destination()
    if dest["brain"]["state"] == "refused":
        problems.append({"code": "destination-refused", "why": dest["brain"]["why"],
                         "remedy": "make brain/ a plain directory inside the work root"})
    if dest["handbook"]["state"] == "refused":
        problems.append({"code": "destination-refused", "why": dest["handbook"]["why"],
                         "remedy": "make brain/handbook/handbook.md a plain file"})
    prefix = _work_prefix(state)
    out = {
        "layout": layout,
        "separation": "shared" if shared else ("independent" if state is not None else "none"),
        "separationWhy": (
            "one repository holds the application and the work state (a vendored or "
            "source-hosted install): the Brain is committed there, as every board edit is"
            if shared else
            "the application and the work state are separate repositories: the Brain is "
            "committed to the work state's, never to the application's"
            if state is not None else "the work root is in no repository"),
        "app": {"root": str(app_real), "role": "read sources only", **_repo_identity(
            Path(app_top) if app_top else None)},
        "work": {"root": str(work_real), "role": "read sources; the Brain's destination",
                 "prefix": prefix, **_repo_identity(state, prefix)},
        "tool": {"root": str(_real(config.TM_ROOT)),
                 "role": "bench's own installation and runtime state — never a source or a "
                         "destination"},
        "destination": dest,
        "problems": problems,
        "refused": bool(problems),
    }
    return out


# ── classifying a path ─────────────────────────────────────────────────


def _excluded_why(rel: str) -> str | None:
    """Why a path is never read, whatever is selected — or None."""
    parts = rel.split("/")
    lower = [p.lower() for p in parts]
    if SECRET_NAME.search(rel):
        return "its name looks like a credential or secret, so it is never opened"
    if any(p in DEPENDENCY_DIRS for p in parts[:-1]):
        return "it is inside a dependency or build tree"
    if any(p in PRIVATE_DIRS for p in lower[:-1]) or lower[-1] in PRIVATE_DIRS:
        return "it is private runtime, a transcript or a log directory"
    if LOG_NAME.search(rel):
        return "it is a raw log, transcript or data dump"
    if PurePosixPath(rel).suffix.lower() in BINARY_SUFFIX:
        return "it is a binary or generated file"
    return None


def _category(root: str, rel: str) -> str:
    if root == "work":
        head = rel.split("/", 1)[0]
        if head == "tasks":
            return "archive" if rel.startswith("tasks/archive/") else "card"
        return {"plans": "plan", "reference": "reference", "brain": "brain"}.get(head, "other")
    name = rel.rsplit("/", 1)[-1].lower()
    suffix = PurePosixPath(name).suffix
    # an agent or contributor instruction file, whichever tool it is for
    if name in INSTRUCTION_NAMES or name.endswith("-instructions.md"):
        return "instructions"
    # a CI workflow, wherever the hosting service keeps them
    if name in CONFIG_NAMES or name.startswith(("dockerfile", "docker-compose", "compose.")) \
            or ("/workflows/" in rel and suffix in (".yml", ".yaml")):
        return "config"
    if suffix in DOC_SUFFIX:
        return "docs"
    if suffix in CODE_SUFFIX:
        return "code"
    if suffix in CONFIG_SUFFIX and "/" not in rel:
        return "config"
    return "other"


def _default(category: str, rel: str) -> bool:
    if category == "instructions":
        return True
    if category == "docs":
        name = rel.rsplit("/", 1)[-1].lower()
        return "/" not in rel and name.startswith(("readme", "architecture", "overview")) \
            or rel.lower().startswith(("docs/", "doc/")) and rel.count("/") == 1
    return False


def _clean(text: str, cap: int = MAX_PATH) -> str:
    return brain.clean(text, cap)


def valid_path(rel) -> str | None:
    """Why `rel` cannot name a file relative to a root — or None. No
    absolute path, no `..`, no backslash, no control character, never
    starting with `-` (it is passed to git after `--` all the same)."""
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH:
        return "a path is 1 to 300 characters"
    if _UNSAFE.search(rel) or "\\" in rel:
        return "a path may not hold control characters or backslashes"
    if rel.startswith(("/", "-", "~")):
        return "a path is relative to its root"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "a path may not climb out of its root or hold empty segments"
    return None


# ── the listing: names and sizes, nothing opened ───────────────────────


def _app_paths(app: Path) -> tuple[list[tuple[str, bool]], bool]:
    """The application's files by name: tracked, then untracked files that
    .gitignore does not hide. Bounded."""
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    truncated = False
    for args, tracked in ((("ls-files", "-z", "--cached"), True),
                          (("ls-files", "-z", "--others", "--exclude-standard"), False)):
        listed = _git_out(app, *args)
        if listed is None:
            continue
        for rel in listed.split("\0"):
            if not rel or rel in seen:
                continue
            if len(out) >= MAX_LISTED * 2:
                truncated = True
                break
            seen.add(rel)
            out.append((rel, tracked))
    return out, truncated


def _work_paths(work: Path) -> list[str]:
    out: list[str] = []

    def listing(rel_dir: str, suffixes: tuple[str, ...] | None) -> None:
        directory = work / rel_dir
        try:
            if stat.S_ISLNK(os.lstat(directory).st_mode) or not directory.is_dir():
                return
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            if name.startswith("."):
                continue
            if suffixes and not name.endswith(suffixes):
                continue
            out.append(f"{rel_dir}/{name}")

    for slug, _label in config.STAGES:
        listing(f"tasks/{slug}", (".md",))
    listing("tasks/archive", (".md",))
    listing("plans", None)
    listing("reference", None)
    listing(brain.DIRECTORY, (".md",))
    listing(f"{brain.DIRECTORY}/{brain.HANDBOOK_DIRECTORY}", (".md",))
    return out


def _excluded_by_location(root_path: Path, rel: str, tool: Path, work: Path,
                          root: str) -> str | None:
    """Bench's own installation and private configuration are never
    application sources, and the work state's directories are listed as
    the work root's, not twice. Paths as written: a link is refused when
    it is read."""
    if root != "app":
        return None
    path = Path(os.path.abspath(root_path / rel))
    if _inside(path, tool / "manager" / "local"):
        return "it is bench's private configuration and runtime state"
    if tool != root_path and _inside(path, tool):
        return "it is part of bench's own installation, which is never a source"
    for name in ("tasks", "plans", "reference", brain.DIRECTORY):
        if _inside(path, work / name):
            return "it is in the work root, listed under Work instead"
    return None


def candidates(ident: dict | None = None) -> dict:
    """Every file a person could approve, by root, with its category, size
    (`lstat`) and default treatment — or why it is excluded. Nothing is
    opened and nothing is written."""
    ident = ident or identities()
    out: dict = {"app": [], "work": [], "truncated": {"app": False, "work": False},
                 "counts": {}}
    if ident["refused"]:
        return out
    tool = _real(config.TM_ROOT)
    work_real = _real(config.work_root())
    app_root = _real(Path(ident["app"]["repository"])) if ident["app"]["repository"] else None
    sources: list[tuple[str, Path, list[tuple[str, bool | None]]]] = []
    if app_root is not None:
        paths, trunc = _app_paths(app_root)
        out["truncated"]["app"] = trunc
        sources.append(("app", app_root, paths))
    sources.append(("work", work_real, [(p, None) for p in _work_paths(work_real)]))
    defaults = 0
    for root, root_path, paths in sources:
        listed = out[root]
        for rel, tracked in paths:
            if len(listed) >= MAX_LISTED:
                out["truncated"][root] = True
                break
            if valid_path(rel):
                continue
            category = _category(root, rel)
            item = {"root": root, "path": _clean(rel), "category": category,
                    "treatment": CATEGORIES[category][1], "bytes": None,
                    "state": "candidate", "why": None, "default": False}
            if tracked is not None:
                item["tracked"] = tracked
            why = _excluded_by_location(root_path, rel, tool, work_real, root)
            if why is not None:
                continue      # listed under its own root, or never a source
            why = _excluded_why(rel)
            if why is None:
                try:
                    info = os.lstat(root_path / rel)
                    if stat.S_ISLNK(info.st_mode):
                        why = "it is a link, and a link is never followed"
                    elif not stat.S_ISREG(info.st_mode):
                        why = "it is not a regular file"
                    else:
                        item["bytes"] = info.st_size
                        if info.st_size > MAX_FILE_BYTES:
                            why = (f"it is {info.st_size} bytes, over the "
                                   f"{MAX_FILE_BYTES // 1024} KiB a file may be read whole; "
                                   "choose a slice of it instead")
                            item["sliceable"] = True
                except FileNotFoundError:
                    item["state"] = "absent"
                    item["why"] = "git lists it, and it is not on disk"
                except OSError as exc:
                    item["state"] = "inaccessible"
                    item["why"] = f"it could not be examined ({exc.strerror or exc})"
            if why is not None:
                item.update(state="excluded", why=why)
            elif item["state"] == "candidate" and defaults < MAX_DEFAULTS \
                    and _default(category, rel):
                item["default"] = True
                defaults += 1
            listed.append(item)
            out["counts"][category] = out["counts"].get(category, 0) + 1
    return out


def reading() -> dict:
    """What the seed page opens on: the identities, the destination, every
    candidate and exclusion, the treatments, sections and bounds. A read."""
    ident = identities()
    found = candidates(ident)
    return {
        "v": CONTRACT, "kind": "bench-brain-seed-inventory",
        **ident,
        "candidates": {"app": found["app"], "work": found["work"]},
        "truncated": found["truncated"], "counts": found["counts"],
        "categories": {k: {"label": v[0], "treatment": v[1]} for k, v in CATEGORIES.items()},
        "treatments": TREATMENTS,
        "sections": [{"id": sid, "title": title, "about": about}
                     for sid, title, about in handbook.SECTIONS],
        "fixedExclusions": FIXED_EXCLUSIONS,
        "bounds": bounds(),
        "authorizes": AUTHORIZES,
    }


FIXED_EXCLUSIONS = [
    "credential-shaped names: .env and .env.*, keys and certificates, *secret*, "
    "*credential*, *password*, *token* — never opened",
    "private transcripts, raw logs and data dumps (.git/, .claude/, logs/, sessions/, "
    "*.log, *.jsonl)",
    "dependency and build trees (node_modules/, vendor/, .venv/, dist/, build/, target/ …)",
    "binaries and generated files (images, archives, fonts, compiled objects, lock files)",
    "links, and any path that resolves outside its root",
    "bench's own installation and runtime state",
    "files whose content looks like it holds a credential (pattern matching: it lowers the "
    "risk and does not prove a file clean)",
]
AUTHORIZES = ("nothing — this inventory is not permission to generate. Generating a draft "
              "is a separate, explicit approval, and only a person's Apply writes the Brain")


def bounds() -> dict:
    return {"listedPerRoot": MAX_LISTED, "selected": MAX_SELECTED,
            "fileBytes": MAX_FILE_BYTES, "totalBytes": MAX_TOTAL_BYTES,
            "excludePatterns": MAX_EXCLUDES}


# ── inspecting the approved selection ──────────────────────────────────


def _selection(payload) -> dict:
    """The person's choice, checked whole before anything is read."""
    if not isinstance(payload, dict):
        raise Refused("the selection is a JSON object")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise Refused("choose at least one source to inspect", code="nothing-selected")
    if len(sources) > MAX_SELECTED:
        raise Refused(f"at most {MAX_SELECTED} sources are inspected at once; narrow the "
                      "selection", code="too-many")
    chosen, keys = [], set()
    for n, raw in enumerate(sources, 1):
        if not isinstance(raw, dict):
            raise Refused(f"source {n} is not an object")
        root, rel = raw.get("root"), raw.get("path")
        if root not in ROOTS:
            raise Refused(f"source {n}: the root is app or work", code="bad-root")
        why = valid_path(rel)
        if why:
            raise Refused(f"source {n}: {why}", code="bad-path")
        treatment = raw.get("treatment") or CATEGORIES[_category(root, rel)][1]
        if treatment not in TREATMENTS:
            raise Refused(f"source {n}: the treatment is one of " + ", ".join(TREATMENTS),
                          code="bad-treatment")
        lines = raw.get("lines")
        if lines is not None:
            if (not isinstance(lines, list) or len(lines) != 2
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in lines)
                    or lines[0] < 1 or lines[1] < lines[0] or lines[1] > 1_000_000):
                raise Refused(f"source {n}: a slice is [first line, last line], 1-based",
                              code="bad-slice")
        key = (root, rel, tuple(lines) if lines else None)
        if key in keys:
            raise Refused(f"source {n} is chosen twice", code="duplicate")
        keys.add(key)
        chosen.append({"root": root, "path": rel, "treatment": treatment,
                       **({"lines": list(lines)} if lines else {})})
    excludes = payload.get("exclude") or []
    if not isinstance(excludes, list) or len(excludes) > MAX_EXCLUDES:
        raise Refused(f"at most {MAX_EXCLUDES} exclusion patterns", code="bad-exclude")
    for pat in excludes:
        if not isinstance(pat, str) or not pat.strip() or len(pat) > MAX_PATTERN \
                or _UNSAFE.search(pat):
            raise Refused("an exclusion is a glob of 1 to 200 printable characters",
                          code="bad-exclude")
    sections = payload.get("sections")
    if sections is None:
        sections = list(handbook.SECTION_IDS)
    if not isinstance(sections, list) or not sections \
            or any(s not in handbook.SECTION_IDS for s in sections) \
            or len(set(sections)) != len(sections):
        raise Refused("sections are some of " + ", ".join(handbook.SECTION_IDS),
                      code="bad-sections")
    ordered = [s for s in handbook.SECTION_IDS if s in sections]
    return {"sources": chosen, "exclude": [p.strip() for p in excludes], "sections": ordered}


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _committed(repo: Path | None, prefix: str, rels: list[str]) -> dict[str, str]:
    """Each path's blob at HEAD, for the paths that have one. Local git."""
    if repo is None or not rels:
        return {}
    out: dict[str, str] = {}
    for start in range(0, len(rels), GIT_BATCH):
        batch = [prefix + r for r in rels[start:start + GIT_BATCH]]
        listed = _git_out(repo, "ls-tree", "-z", "--full-tree", "HEAD", "--", *batch)
        if listed is None:
            continue
        for row in listed.split("\0"):
            if "\t" not in row:
                continue
            meta, path = row.split("\t", 1)
            fields = meta.split()
            if len(fields) == 3 and fields[1] == "blob" and path.startswith(prefix):
                out[path[len(prefix):]] = fields[2]
    return out


def _read_contained(root: Path, rel: str) -> bytes:
    """The file's bytes, reached through no link at any depth and inside
    `root`. Raises FileNotFoundError (absent), PermissionError or OSError
    (inaccessible) and _Excluded (a link, an escape, not a regular file,
    too large)."""
    at = root
    for part in rel.split("/")[:-1]:
        at = at / part
        info = os.lstat(at)
        if stat.S_ISLNK(info.st_mode):
            raise _Excluded("a directory on its path is a link, and a link is never followed")
        if not stat.S_ISDIR(info.st_mode):
            raise FileNotFoundError(rel)
    path = root / rel
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise _Excluded("it is a link, and a link is never followed")
    if not stat.S_ISREG(info.st_mode):
        raise _Excluded("it is not a regular file")
    if not _inside(_real(path), _real(root)):
        raise _Excluded("it resolves outside its root")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _Excluded("it is not a regular file")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise _Excluded(f"it is over the {MAX_FILE_BYTES // 1024} KiB a file may be "
                                "read")
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


class _Excluded(Exception):
    pass


def _slice(data: bytes, lines: list[int]) -> bytes | None:
    rows = data.splitlines(keepends=True)
    if lines[0] > len(rows):
        return None
    return b"".join(rows[lines[0] - 1:lines[1]])


def _item_id(src: dict) -> str:
    tail = f"#L{src['lines'][0]}-L{src['lines'][1]}" if src.get("lines") else ""
    return f"{src['root']}:{src['path']}{tail}"


def _inspect_one(src: dict, root_path: Path, excludes: list[str]) -> dict:
    rel = src["path"]
    item = {"id": _item_id(src), "root": src["root"], "path": rel,
            "treatment": src["treatment"], "category": _category(src["root"], rel),
            "state": "read", "why": None, "bytes": None, "sha256": None}
    if src.get("lines"):
        item["lines"] = src["lines"]
    why = _excluded_why(rel)
    if why:
        return {**item, "state": "excluded", "why": why}
    matched = next((p for p in excludes if fnmatch.fnmatchcase(rel, p)
                    or fnmatch.fnmatchcase(rel, p.rstrip("/") + "/*")), None)
    if matched:
        return {**item, "state": "excluded", "why": f"your exclusion {matched!r} matches it",
                "byYou": True}
    try:
        data = _read_contained(root_path, rel)
    except FileNotFoundError:
        return {**item, "state": "absent", "why": "there is no such file — a gap, never "
                "inferred contents"}
    except _Excluded as exc:
        return {**item, "state": "excluded", "why": str(exc)}
    except PermissionError as exc:
        return {**item, "state": "inaccessible",
                "why": f"it is there and could not be read ({exc.strerror or exc})"}
    except OSError as exc:
        return {**item, "state": "inaccessible",
                "why": f"it could not be read ({exc.strerror or exc})"}
    if b"\0" in data[:8192]:
        return {**item, "state": "excluded", "why": "its bytes are binary, not text"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {**item, "state": "excluded", "why": "it is not UTF-8 text"}
    shapes = redact.found(text)
    if shapes:
        # the digest of a credential-bearing file is not published either
        return {**item, "state": "excluded", "why": "its content looks like it holds a "
                "credential (" + ", ".join(shapes[:3]) + "), so it is left out"}
    item["blob"] = _blob(data)
    if src.get("lines"):
        part = _slice(data, src["lines"])
        if part is None:
            return {**item, "state": "absent", "why": f"the file has "
                    f"{len(data.splitlines())} lines; the slice starts after its end",
                    "fileBytes": len(data), "fileSha256": hashlib.sha256(data).hexdigest()}
        item.update(fileBytes=len(data), fileSha256=hashlib.sha256(data).hexdigest(),
                    bytes=len(part), sha256=hashlib.sha256(part).hexdigest(),
                    linesRead=[src["lines"][0],
                               min(src["lines"][1], len(data.splitlines()))])
    else:
        item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return item


def _gaps(items: list[dict], sections: list[str]) -> list[dict]:
    """Which chosen sections no source that was read can speak to: discovery
    coverage, never proof that the project has nothing there."""
    read = [i for i in items if i["state"] == "read"]
    cats = {i["category"] for i in read}
    out = []
    for sid in sections:
        if sid == "operations":
            if not any(OPERATIONS_HINT.search(i["path"]) for i in read):
                out.append({"section": sid, "why": "no source in the selection is about "
                            "installing, deploying, running or recovering the project"})
            continue
        want = SECTION_HINTS.get(sid, ())
        if want and not cats & set(want):
            words = ", ".join(CATEGORIES[c][0].lower() for c in want)
            out.append({"section": sid, "why": f"nothing read is {words}"})
    return out


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest_of(manifest: dict) -> str:
    """The manifest's digest: everything but the digest itself and the time
    it was made, so the same reading of the same bytes digests the same."""
    body = {k: v for k, v in manifest.items() if k not in ("digest", "made")}
    return hashlib.sha256(_canonical(body)).hexdigest()


def inspect(payload) -> dict:
    """Read exactly the approved selection and return manifest version 1.

    Refused, with nothing read: a mapping `identities` refuses (409,
    `mapping-refused`), and a selection that is not well formed (400).
    Everything else is an item with a state: `read`, `absent`,
    `inaccessible` or `excluded`, each said with why. Writes nothing."""
    sel = _selection(payload)
    ident = identities()
    if ident["refused"]:
        raise Refused("the application and work roots cannot be used for seeding: "
                      + "; ".join(p["why"] for p in ident["problems"]), status=409,
                      code="mapping-refused", problems=ident["problems"])
    roots = {"app": _real(Path(ident["app"]["repository"])),
             "work": Path(ident["work"]["root"])}
    tool = _real(config.TM_ROOT)
    work_real = roots["work"]
    items, total = [], 0
    for src in sel["sources"]:
        root_path = roots[src["root"]]
        located = _excluded_by_location(root_path, src["path"], tool, work_real, src["root"])
        if located:
            items.append({"id": _item_id(src), "root": src["root"], "path": src["path"],
                          "treatment": src["treatment"],
                          "category": _category(src["root"], src["path"]),
                          "state": "excluded", "why": located, "bytes": None, "sha256": None,
                          **({"lines": src["lines"]} if src.get("lines") else {})})
            continue
        item = _inspect_one(src, root_path, sel["exclude"])
        if item["state"] == "read":
            if total + item["bytes"] > MAX_TOTAL_BYTES:
                item = {**{k: item[k] for k in ("id", "root", "path", "treatment", "category")},
                        **({"lines": item["lines"]} if item.get("lines") else {}),
                        "state": "excluded", "bytes": None, "sha256": None, "bound": True,
                        "why": f"the selection's {MAX_TOTAL_BYTES // (1024 * 1024)} MiB "
                               "bound is reached; narrow it or choose slices"}
            else:
                total += item["bytes"]
        items.append(item)
    # git: committed, modified or untracked, from each repository's HEAD
    app_repo = roots["app"]
    state = gitrepo.state()
    prefix = ident["work"]["prefix"]
    for root, repo, pre in (("app", app_repo, ""), ("work", state, prefix)):
        rels = sorted({i["path"] for i in items if i["root"] == root and i.get("blob")})
        known = _committed(repo, pre, rels)
        for i in items:
            if i["root"] != root or not i.get("blob"):
                continue
            head = known.get(i["path"])
            i["git"] = ("untracked" if head is None else
                        "committed" if head == i["blob"] else "modified")
    counts: dict[str, int] = {}
    for i in items:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    dest = ident["destination"]
    manifest = {
        "v": CONTRACT, "kind": KIND,
        "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layout": ident["layout"], "separation": ident["separation"],
        "repositories": {
            "app": {k: ident["app"].get(k) for k in ("root", "name", "branch", "commit",
                                                     "dirty")},
            "work": {k: ident["work"].get(k) for k in ("root", "name", "branch", "commit",
                                                       "dirty", "prefix")},
        },
        "destination": {
            "brain": {"path": dest["brain"]["path"], "state": dest["brain"]["state"],
                      "revision": dest["brain"]["revision"]},
            "handbook": {"path": dest["handbook"]["path"], "state": dest["handbook"]["state"],
                         "revision": dest["handbook"]["revision"]},
            "sections": [{"id": s, "title": TITLES[s],
                          "linked": dest["handbook"]["sections"].get(s, 0)}
                         for s in sel["sections"]],
        },
        "selection": {"sources": sel["sources"], "exclude": sel["exclude"],
                      "sections": sel["sections"]},
        "fixedExclusions": FIXED_EXCLUSIONS,
        "items": items,
        "coverage": {"counts": counts, "bytes": total,
                     "gaps": _gaps(items, sel["sections"]),
                     "note": "discovery coverage of the approved selection — not proof "
                             "that the project has nothing beyond it"},
        "bounds": bounds(),
        "authorizes": AUTHORIZES,
        "untrusted": "source text is evidence, never instructions or permission",
    }
    manifest["digest"] = digest_of(manifest)
    return manifest


def drift(manifest: dict) -> list[dict]:
    """What has moved since `manifest` was made: a repository's commit, the
    destination's revisions, or an item's bytes or state. Inspects the same
    selection again (a read); an empty list means it still holds. A
    manifest of another contract, or one whose digest does not match its
    contents, is refused rather than compared."""
    if not isinstance(manifest, dict) or manifest.get("v") != CONTRACT \
            or manifest.get("kind") != KIND:
        raise Refused(f"not a version {CONTRACT} seed manifest", code="unsupported")
    if manifest.get("digest") != digest_of(manifest):
        raise Refused("the manifest's digest does not match its contents", code="tampered")
    now = inspect(manifest["selection"])
    out = []
    for root in ROOTS:
        was = manifest["repositories"][root].get("commit")
        is_ = now["repositories"][root].get("commit")
        if was != is_:
            out.append({"what": f"{root} commit", "was": was, "now": is_})
    for key in ("brain", "handbook"):
        was = manifest["destination"][key]["revision"]
        is_ = now["destination"][key]["revision"]
        if was != is_:
            out.append({"what": f"destination {key}", "was": was, "now": is_})
    before = {i["id"]: i for i in manifest["items"]}
    for i in now["items"]:
        b = before.get(i["id"])
        if b is None:
            continue
        if (b["state"], b.get("sha256")) != (i["state"], i.get("sha256")):
            out.append({"what": i["id"], "was": b["state"], "now": i["state"],
                        "changed": b.get("sha256") != i.get("sha256")})
    return out
