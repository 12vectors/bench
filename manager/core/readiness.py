"""Verification readiness: from discovered project tests to runnable,
evidenced checks and a deliberately chosen merge rule (task 90).

The setup guide (`setupguide.py`) says whether a check is registered and
what its newest run found; the setup cards (`setuptasks.py`) ask for a
verification program when there is none. Between "a test command exists
somewhere in this repository" and "a merge waits for it" there are five
separate acts, and the failure this module exists to prevent is any two
of them collapsing into one:

    discover    read the project's scripts, CI and docs for commands that
                look like checks. Read-only, runs nothing, and says how
                confident the inference is — an inference is not an
                execution and not proven coverage
    register    turn one candidate (or an explicit command a setup card
                delivered) into a draft of the profile document, validated
                and previewed by Settings' own `validate`/`preview`. The
                write is Settings' armed **Apply**; this module never
                writes the policy
    run         a named check is run through `verify.request`, from a
                card's ◫ evidence sheet, by a person — the existing runner
                and its retained evidence, nothing new
    review      a person records that they read the program and its
                results: a current, bound, clean pass of the check as it
                is now defined, and a controlled failure of the same
                definition (or the reason one is not appropriate). That
                record — bound to the check's identity — is what makes a
                check *ready*; a run's exit status alone never does
    activate    a consequence preview of making ready checks (and a review
                policy) mandatory for one guarded merge. Again a draft
                through Settings; again Settings' Apply writes it

Until at least one check is ready the board carries a **bootstrap
condition**: automated verification is incomplete, so a setup card's
delivery needs a person's review. That condition changes no gate. A merge
rule already in force stays in force — including over the setup card that
is delivering the check it requires, which is the circularity this module
names rather than resolves: the way through is the policy's own recorded
override, or a person changing the merge rule in Settings, never a silent
bypass, a self-approval or a wider grant for the setup card's run.

What it stores is a person's review, portable, in the setup guide's own
`setup/decisions.json` under `verification` — the check's identity, the
program identity and the commits the reviewed runs were bound to, who
reviewed and why. No path, no evidence log, no environment value.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
import time
from pathlib import Path

import config
import profiles
import settings
import setupguide
import setuptasks
import taskfiles

Refused = setupguide.Refused

KIND = "bench-verification-readiness"
VERSION = 1

# ── the vocabulary ─────────────────────────────────────────────────────

# A check's readiness, as against its newest run. Readiness is about the
# check (is this program a usable verification of this project?) and the
# run is about a commit (did it pass here?). They are shown side by side
# and never folded into one word.
READINESS = {
    "candidate": ("inferred from a file in the project. Not registered, never "
                  "run by bench, and its coverage is unproven"),
    "registered": ("declared as a named check; bench has no run of it as it "
                   "is now defined. Configured is not tested"),
    "exercised": ("bench ran it and it passed; nobody has recorded a review of "
                  "the program and its results yet, so it is not ready"),
    "attention": ("bench ran it and the newest run did not pass — read the "
                  "record before anything else"),
    "review-stale": ("a review was recorded, and the check's definition or "
                     "program has changed since. Review it again"),
    "ready": ("a person reviewed the program, a current pass and a controlled "
              "failure (or why one is not appropriate), bound to the check as "
              "it is defined now"),
}

# What a run's outcome may be read as, for a reviewer. A preparation
# failure, a denial, a cancellation and an error reached no verdict: none
# of them is the controlled failure a review needs.
NO_VERDICT = ("prepare-failed", "denied", "cancelled", "error")

RESPONSIBILITIES = [
    {"who": "author",
     "role": "your team, or the agent working a verification setup card",
     "does": ("writes the verification program, says what it covers and what "
              "it does not, and supplies a controlled failing case")},
    {"who": "operator",
     "role": "the person configuring this Bench installation",
     "does": ("approves the command, its preparation, any network use and the "
              "data it may touch; registers it; and decides — separately — "
              "whether a merge rule requires it")},
    {"who": "reviewer",
     "role": "a person, not the run that wrote the program",
     "does": ("reads the program and both results and records the review. "
              "That review is not a card's acceptance, and not a formal "
              "GitHub approval")},
    {"who": "bench",
     "role": "this board",
     "does": ("discovers candidates without running them, validates drafts, "
              "runs approved checks, records evidence bound to source, policy "
              "and program, and enforces only the merge rules you activated")},
]

BOOTSTRAP_LIMITS = [
    "The bootstrap condition is a statement, not an exemption: no merge rule "
    "is relaxed, skipped or overridden because of it.",
    "A setup card's run uses the ordinary work profile. It is granted nothing "
    "extra because it is delivering verification.",
    "A card reaching done/ or an agent saying \"tests pass\" proves no "
    "coverage. Only bench's own evidence, reviewed by a person, makes a check "
    "ready.",
    "Readiness does not activate anything. Making a check required is its "
    "own previewed change in Settings → Merge rules.",
]

SEPARATE_ACTIONS = [
    {"id": "discover", "label": "Discover candidates",
     "writes": "nothing", "runs": "nothing"},
    {"id": "register", "label": "Preview registration, then Apply in Settings",
     "writes": "the profile document, on Apply only", "runs": "nothing"},
    {"id": "prepare", "label": "Preparation runs with the check it prepares",
     "writes": "an evidence record", "runs": "the approved preparation step"},
    {"id": "run", "label": "Run the check from a card's ◫ evidence sheet",
     "writes": "an evidence record", "runs": "the approved check"},
    {"id": "review", "label": "Record a review of the program and results",
     "writes": "setup/decisions.json", "runs": "nothing"},
    {"id": "activate", "label": "Preview a merge rule, then Apply in Settings",
     "writes": "the profile document, on Apply only", "runs": "nothing"},
]

# Core names no project's stack (tests/test_checks_definition.py holds it
# to that), so a CI step or a documented command is offered by its shape —
# one bare program, words that read as checking — not by a list of tools.
BARE = re.compile(r"^(\./)?[A-Za-z][\w.+-]*$")
CHECK_WORDS = re.compile(r"test|check|lint|verify|spec|vet|clippy", re.I)
COMPOUND = re.compile(r"&&|\|\||[|;<>`]|\$\(|\$\{")
SCRIPT_KINDS = {"test": "high", "check": "medium", "lint": "medium",
                "typecheck": "medium", "type-check": "medium",
                "verify": "medium", "build": "low"}
MAKE_TARGETS = {"test": "medium", "check": "medium", "lint": "low",
                "verify": "medium"}
DOC_FILES = ("README.md", "CONTRIBUTING.md", "TESTING.md", "docs/testing.md",
             "docs/TESTING.md", "docs/development.md")
ENTRYPOINT_DIRS = ("verify", "scripts", "bin")
ENTRYPOINT_NAME = re.compile(r"^(test|tests|check|verify|ci)([-_.][\w.-]*)?$|"
                             r"^(backend|frontend|unit|e2e|lint)$", re.I)
SECRET_FILES = (".env.example", ".env.sample", ".env.template", ".env.test")
NOTE_CAP = 1000
RANK = {"high": 3, "medium": 2, "low": 1}


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ── discovery: read, run nothing ───────────────────────────────────────

def _read(app: Path, rel: str) -> str | None:
    try:
        path = app / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _looks_like_check(command: str) -> bool:
    try:
        first = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    return bool(BARE.match(first)) and bool(CHECK_WORDS.search(command))


def _ci_commands(text: str) -> list[tuple[int, str]]:
    """`run:` steps of a GitHub workflow, single-line and block form. A
    reading of YAML by line, on purpose: it is a hint for a person, and a
    line it cannot place is simply not offered."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:-\s*)?run:\s*(.*)$", line)
        if not m:
            continue
        value = m.group(2).strip()
        if value and value[0] not in "|>":
            out.append((i + 1, value.strip("'\"")))
            continue
        indent = len(m.group(1))
        for j in range(i + 1, len(lines)):
            body = lines[j]
            if body.strip() and len(body) - len(body.lstrip()) <= indent:
                break
            if body.strip() and not body.strip().startswith("#"):
                out.append((j + 1, body.strip()))
    return out


def _doc_commands(text: str) -> list[tuple[int, str]]:
    out, fenced = [], False
    for i, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced and line.strip():
            out.append((i + 1, line.strip().removeprefix("$ ").strip()))
    return out


def _prerequisites(command: str, found: dict, app: Path) -> list[dict]:
    """What a candidate needs before it can run, each as the consent
    boundary it is. A suggested preparation step that downloads is marked
    `network`, and registering it is refused without that consent."""
    try:
        first = shlex.split(command)[0]
    except (ValueError, IndexError):
        first = ""
    out: list[dict] = []
    if first in ("npm", "npx", "pnpm", "yarn"):
        lock = (app / "package-lock.json").is_file()
        out.append({"kind": "prepare", "name": "node-deps",
                    "run": "npm ci" if lock else "npm install",
                    "network": True,
                    "why": "installs the project's JavaScript dependencies, "
                           "downloading them from a registry"})
    elif first.startswith("python") or first in ("tox", "nox"):
        if (app / "requirements.txt").is_file():
            out.append({"kind": "prepare", "name": "python-deps",
                        "run": "python3 -m pip install -r requirements.txt",
                        "network": True,
                        "why": "installs the listed Python packages from an "
                               "index"})
        elif (app / "pyproject.toml").is_file() and \
                "dependencies" in (_read(app, "pyproject.toml") or ""):
            out.append({"kind": "prepare", "name": "python-deps",
                        "run": "python3 -m pip install -e .", "network": True,
                        "why": "installs the project and its declared "
                               "dependencies from an index"})
    elif first in ("cargo", "go"):
        out.append({"kind": "network", "name": f"{first}-fetch",
                    "network": True,
                    "why": f"{first} downloads modules on a first run; bench "
                           f"does not bound the network a check uses"})
    if found.get("services"):
        out.append({"kind": "data", "name": "disposable-data",
                    "refs": [r for r, _ in found["services"]],
                    "why": "the project configures services or data. Tests "
                           "must use disposable fixtures, never a live "
                           "database (setup gap: fixtures)"})
    secrets_ = [rel for rel in SECRET_FILES if (app / rel).is_file()]
    if secrets_:
        out.append({"kind": "secrets", "name": "no-live-credentials",
                    "refs": secrets_,
                    "why": "the project expects environment values. Bench "
                           "removes publication credentials from a check's "
                           "environment and supplies no secrets; give tests "
                           "disposable values, never production ones"})
    return out


def _entrypoints(app: Path) -> list[tuple[str, str]]:
    import os
    out = []
    for folder in ENTRYPOINT_DIRS:
        base = app / folder
        try:
            names = sorted(p for p in base.iterdir() if p.is_file())
        except OSError:
            continue
        for path in names:
            if not (ENTRYPOINT_NAME.match(path.name) and os.access(path, os.X_OK)):
                continue
            rel = f"{folder}/{path.name}"
            # A check names a bare interpreter or an absolute path, never a
            # path relative to a working directory — so the program is
            # offered through the interpreter its own #! line names.
            try:
                with path.open("rb") as handle:
                    first = handle.readline(200).decode("utf-8", "replace")
            except OSError:
                continue
            words = first[2:].split() if first.startswith("#!") else []
            if words and Path(words[0]).name == "env" and len(words) > 1:
                words = words[1:]
            interpreter = Path(words[0]).name if words else ""
            if re.match(r"^[A-Za-z][\w.+-]*$", interpreter):
                out.append((rel, f"{interpreter} {rel}"))
    return out


def discover() -> dict:
    """Candidate checks, from what the project already maintains. Reads
    files; runs nothing, installs nothing and writes nothing."""
    app = config.app_repo()
    found = setuptasks.inspect()
    if not found.get("readable"):
        return {"candidates": [], "unreadable": (
            "the application root could not be read, so nothing is offered: "
            "an unread project is unknown, not empty"), "unknown": []}
    seen: dict[str, dict] = {}

    def add(command: str, ref: str, how: str, confidence: str,
            line: int | None = None) -> None:
        command = command.strip()
        if not command:
            return
        entry = seen.setdefault(command, {
            "command": command, "sources": [], "confidence": confidence})
        entry["sources"].append({"ref": ref, "line": line, "how": how})
        if RANK[confidence] > RANK[entry["confidence"]]:
            entry["confidence"] = confidence

    pkg = found["refs"].get("package.json", {})
    if pkg.get("finding") == "present" and not pkg.get("dir"):
        try:
            scripts = json.loads(pkg["text"]).get("scripts") or {}
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        for name, body in sorted(scripts.items() if isinstance(scripts, dict)
                                 else []):
            kind = SCRIPT_KINDS.get(name) or (
                "medium" if name.startswith(("test:", "test-")) else None)
            if not kind or not isinstance(body, str) or \
                    setuptasks.NPM_NO_TEST in body:
                continue
            add("npm test" if name == "test" else f"npm run {name}",
                "package.json", f"the `{name}` script (`{body[:80]}`)", kind)
    make = found["refs"].get("Makefile", {})
    if make.get("finding") == "present" and not make.get("dir"):
        for target in sorted(set(re.findall(r"^([A-Za-z][\w-]*)\s*:",
                                            make["text"], re.M))):
            if target in MAKE_TARGETS:
                add(f"make {target}", "Makefile", f"the `{target}` target "
                    "(what it runs was not read)", MAKE_TARGETS[target])
    has = lambda rel: found["refs"].get(rel, {}).get("finding") == "present"
    if has("Cargo.toml"):
        add("cargo test", "Cargo.toml", "a Cargo package", "high")
    if has("go.mod"):
        add("go test ./...", "go.mod", "a Go module", "high")
    if (has("pyproject.toml") or has("setup.py")) and has("tests"):
        add("python3 -m unittest discover -s tests", "tests",
            "a Python test directory (the runner is a guess)", "medium")
    for rel, command in _entrypoints(app):
        add(command, rel, "a maintained verification entrypoint", "high")
    workflows = app / ".github" / "workflows"
    try:
        flows = sorted(p for p in workflows.iterdir()
                       if p.suffix in (".yml", ".yaml"))
    except OSError:
        flows = []
    for flow in flows:
        rel = f".github/workflows/{flow.name}"
        for line, command in _ci_commands(_read(app, rel) or ""):
            if _looks_like_check(command):
                add(command, rel, "a CI step", "medium", line)
    for rel in DOC_FILES:
        for line, command in _doc_commands(_read(app, rel) or ""):
            if _looks_like_check(command):
                add(command, rel, "a command in the documentation", "low",
                    line)

    registered = {c["run"]: c["name"] for c in
                  settings.verification(profiles.document())["checks"]}
    out = []
    for command, entry in seen.items():
        if len(entry["sources"]) > 1 and entry["confidence"] != "high":
            entry["confidence"] = {"low": "medium", "medium": "high"}[
                entry["confidence"]]
        compound = bool(COMPOUND.search(command))
        why = None
        if compound:
            why = ("a compound shell line (&&, |, ;, redirection or "
                   "substitution) cannot be a named check: bench runs one "
                   "program with fixed arguments and no shell. Deliver a "
                   "wrapper program through a verification setup card")
        elif _relative_executable(command):
            why = ("the program is named by a path relative to a working "
                   "directory; a check names a bare interpreter or an absolute "
                   "path — register it as `<interpreter> <path>`")
        out.append({
            "id": "cand-" + _digest(command),
            "command": command,
            "name": _suggest_name(command),
            "sources": entry["sources"],
            "confidence": entry["confidence"],
            "confidenceWhy": _confidence_why(entry),
            "prerequisites": _prerequisites(command, found, app),
            "expressible": why is None,
            "expressibleWhy": why,
            "registered": registered.get(command),
            "readiness": "registered" if command in registered else "candidate",
            "inferred": True, "executed": False, "coverage": "unproven",
        })
    out.sort(key=lambda c: (-RANK[c["confidence"]], c["command"]))
    return {"candidates": out, "unreadable": None,
            "unknown": list(found.get("unknown") or []),
            "inert": ("Discovery read files and ran nothing. A candidate is an "
                      "inference: it has not been executed and its coverage "
                      "is unproven.")}


def _relative_executable(command: str) -> bool:
    try:
        first = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    return "/" in first and not first.startswith("/")


def _suggest_name(command: str) -> str:
    words = re.findall(r"[a-z0-9]+", command.lower())
    skip = {"npm", "run", "python3", "python", "m", "make", "s", "discover",
            "go", "cargo", "npx", "pnpm", "yarn", "verify", "scripts", "bin"}
    picked = [w for w in words if w not in skip][:3] or words[:2] or ["check"]
    name = "-".join(picked)[:40].strip("-") or "check"
    return name if profiles.NAME_RE.match(name) else "check"


def _confidence_why(entry: dict) -> str:
    level, n = entry["confidence"], len(entry["sources"])
    base = {"high": "a project manifest or a maintained entrypoint names it",
            "medium": "a build file, a CI step or a directory suggests it",
            "low": "only documentation mentions it"}[level]
    return base + (f"; {n} sources agree" if n > 1 else "") + (
        ". Confidence is about where the command came from, never about "
        "what it covers")


# ── the reviews, and each check's readiness ────────────────────────────

def reviews() -> dict:
    doc = setupguide.read_decisions()["doc"]
    found = (doc.get("verification") or {}).get("checks") or {}
    return found if isinstance(found, dict) else {}


def _card_records(name: str) -> list[dict]:
    """Every card-bound evidence record of one check, newest first. The
    doctor's fixture runs are about bench, not about this project."""
    import verify
    return [r for r in verify.all_evidence()
            if r.get("check") == name and str(r.get("task") or "").endswith(".md")]


def _assignee_of_gap() -> str | None:
    """The assignee of the verification setup card, if one is linked — so a
    reviewer who is also the card's assignee is said not to be independent."""
    doc = setupguide.read_decisions()["doc"]
    link = (doc.get("tasks") or {}).get("verification") or {}
    filename = link.get("filename") if isinstance(link, dict) else None
    if not filename:
        return None
    stage = taskfiles.find_stage_of(filename)
    if stage is None:
        return None
    try:
        text = (config.TASKS / stage / filename).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^\*\*Assignee:\*\*\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else None


def check_states(doc: dict | None = None) -> list[dict]:
    import verify
    doc = profiles.document() if doc is None else doc
    defined = settings._checks_of(doc)
    recorded = reviews()
    policy = profiles.gate_policy(doc)
    required = {}
    for scope, entry in policy["scopes"].items():
        for c in entry.get("checks") or []:
            required.setdefault(c["name"], []).append(scope)
    out = []
    for name in sorted(defined):
        check = defined[name]
        records = [r for r in _card_records(name)
                   if r.get("checkId") == check.get("checkId")]
        latest = verify.outcome_now(records[0]) if records else None
        run = latest["outcome"] if latest else "not-run"
        review = recorded.get(name) if isinstance(recorded.get(name), dict) else None
        review_stale = None
        if review:
            if review.get("checkId") != check.get("checkId"):
                review_stale = "the check's definition changed since the review"
            elif records and review.get("programId") and \
                    (records[0].get("program") or {}).get("programId") \
                    not in (None, review.get("programId")):
                review_stale = ("the program bench resolved for the newest run "
                                "differs from the one reviewed")
        if review and not review_stale:
            word = "ready"
        elif review_stale:
            word = "review-stale"
        elif not records:
            word = "registered"
        elif run == "passed":
            word = "exercised"
        else:
            word = "attention"
        out.append({
            "name": name, "command": check.get("run"), "root": check.get("root"),
            "checkId": check.get("checkId"), "readiness": word,
            "readinessWhy": READINESS[word] if not review_stale else
            f"{READINESS[word]}: {review_stale}",
            "run": run, "runWhy": (latest or {}).get("why"),
            "evidence": [{"id": r.get("id"), "outcome": verify.outcome_now(r)["outcome"],
                          "task": r.get("task"),
                          "commit": ((r.get("source") or {}).get("commit") or "")[:12],
                          "ended": r.get("ended")} for r in records[:6]],
            "required": sorted(required.get(name, [])),
            "review": ({k: review.get(k) for k in
                        ("actor", "actorSource", "at", "note", "passCommit",
                         "failCommit", "failingWaived", "independent",
                         "independentWhy")}
                       if review else None),
        })
    return out


def bootstrap(states: list[dict] | None = None, doc: dict | None = None) -> dict:
    """The visible condition while no check is ready, and what it does not
    do. It changes no gate: an existing rule is listed as preserved, and a
    rule requiring a check that is not ready is named as the circularity it
    is, with the two ways through that are a person's."""
    doc = profiles.document() if doc is None else doc
    states = check_states(doc) if states is None else states
    ready = [s["name"] for s in states if s["readiness"] == "ready"]
    policy = profiles.gate_policy(doc)
    preserved, circular = [], []
    for scope in sorted(policy["scopes"]):
        entry = policy["scopes"][scope]
        if not entry["gated"]:
            continue
        names = [c["requirement"] for c in entry["checks"]]
        preserved.append({"scope": scope, "checks": names,
                          "review": entry["review"]})
        for c in entry["checks"]:
            if c["name"] not in ready:
                circular.append({
                    "scope": scope, "check": c["name"],
                    "why": (f"{scope} requires {c['requirement']}, which is not "
                            f"ready. A setup card delivering or repairing it is "
                            f"held by the same rule until bench's own evidence "
                            f"passes against that card's commit."),
                    "waysThrough": [
                        "run the check against the setup card's own worktree — "
                        "the rule is satisfied by evidence, not by readiness",
                        "the override the policy enables, which records who "
                        "proceeded over what and is never a pass"
                        if policy["overrides"]["allowed"] else
                        "this policy enables no override; only a person "
                        "changing the merge rule in Settings → Merge rules "
                        "can change what it requires",
                    ]})
    incomplete = not ready
    return {
        "condition": "incomplete" if incomplete else "ready",
        "headline": ("Automated verification is incomplete. Setup delivery "
                     "needs human review." if incomplete else
                     f"{len(ready)} check{'s' if len(ready) != 1 else ''} "
                     f"ready: {', '.join(ready)}."),
        "detail": ("No check on this board has a recorded review of a current "
                   "pass and a controlled failure, so nothing here can stand "
                   "in for a person reading a setup card's work." if incomplete
                   else "A ready check is usable; whether a merge requires it "
                        "is still a separate decision."),
        "ready": ready,
        "preserved": preserved,
        "circular": circular,
        "limits": list(BOOTSTRAP_LIMITS),
        "changesGates": False,
    }


def state() -> dict:
    """The whole reading. Writes nothing and runs nothing."""
    doc = profiles.document()
    states = check_states(doc)
    return {
        "kind": KIND, "v": VERSION,
        "revision": setupguide.revision(),
        "policyRevision": settings.revision_of(settings.document_target()),
        "discovery": discover(),
        "checks": states,
        "bootstrap": bootstrap(states, doc),
        "readiness": dict(READINESS),
        "responsibilities": list(RESPONSIBILITIES),
        "actions": list(SEPARATE_ACTIONS),
        "reviewLimits": review_limits(),
        "endpoints": {"read": "GET /api/setup/verification",
                      "register": "POST /api/setup/verification/register",
                      "review": "POST /api/setup/verification/review",
                      "activate": "POST /api/setup/verification/activate",
                      "apply": "POST /api/settings/apply"},
        "wroteNothing": True,
    }


def review_limits() -> dict:
    return {
        "bench": ("a Bench verdict is the review agent's recorded verdict on "
                  "this board, bound to the head it reviewed. It is not a "
                  "person's independent approval."),
        "github": ("a formal GitHub approval at the merging commit. GitHub "
                   "refuses an approval from the account that opened the pull "
                   "request, so a board whose agents run as that account can "
                   "only ever read this as unmet."),
        "separate-reviewer": ("a formal approval from an account other than "
                              "the one that proposed the head. Bench never "
                              "switches accounts or asks for a second "
                              "credential to satisfy it."),
        "none": "no review is required by this rule.",
        "independence": ("Bench cannot claim an independent approval where "
                         "none exists: a review recorded by the card's own "
                         "assignee is labelled as not independent."),
    }


# ── register: a draft through Settings, never a write ──────────────────

def _base_document() -> dict:
    found = settings.draft()
    if found.get("parseError"):
        raise Refused(f"the policy document cannot be edited: "
                      f"{found['parseError']}. Fix it in Settings → Advanced "
                      f"policy first", 409)
    return {"document": copy.deepcopy(found["document"]),
            "revision": found["revision"]}


def register(payload: dict) -> dict:
    """A registration draft for one check, validated and previewed by
    Settings — and nothing written. The page sends the returned document
    to `POST /api/settings/apply`, armed, with the revision it came from.

    `candidate` names a discovered candidate; `command` is an explicit
    command instead (a program a setup card delivered, which discovery on
    the base branch cannot see yet). Preparation is a separate consent:
    a suggested step is included only when `prepare` is true, and one that
    downloads is refused without `network: true`. Gates are never touched.
    """
    payload = payload if isinstance(payload, dict) else {}
    cand_id = str(payload.get("candidate") or "")
    command = str(payload.get("command") or "").strip()
    candidate = None
    if cand_id:
        candidate = next((c for c in discover()["candidates"]
                          if c["id"] == cand_id), None)
        if candidate is None:
            raise Refused(f"no discovered candidate {cand_id!r} — discovery may "
                          f"have changed since the page read it; read it again",
                          404, stale=True)
        command = candidate["command"]
    elif not command:
        raise Refused("name a discovered candidate, or give an explicit command")
    if COMPOUND.search(command):
        raise Refused("a compound shell line cannot be a named check: bench "
                      "runs one program with fixed arguments and no shell. "
                      "Deliver a wrapper program through a verification setup "
                      "card and register that", 409, code="compound")
    if _relative_executable(command):
        raise Refused("a check names a bare interpreter or an absolute path, "
                      "never a path relative to a working directory: register "
                      "it as `<interpreter> <path>`", 409, code="relative")
    name = str(payload.get("name") or
               (candidate or {}).get("name") or _suggest_name(command)).strip()
    if not profiles.NAME_RE.match(name):
        raise Refused(f"the check name {name!r} is not a name: use "
                      f"[a-z0-9][a-z0-9._-]*")
    root = str(payload.get("root") or "worktree")
    if root not in ("worktree", "source"):
        raise Refused("a registered check runs in the card's own worktree or "
                      "in a detached source checkout bench makes; never in "
                      "the application checkout")
    intents = payload.get("intents") or ["work", "act-pr"]
    if not isinstance(intents, list) or \
            not set(intents) <= {"work", "act-pr", "review"}:
        raise Refused("intents are some of work, act-pr and review")
    base = _base_document()
    doc = base["document"]
    gates_before = copy.deepcopy(doc.get("gates"))
    checks = doc.setdefault("checks", [])
    if not isinstance(checks, list):
        raise Refused("the document's checks are not a list", 409)
    if any(isinstance(c, dict) and c.get("name") == name for c in checks):
        raise Refused(f"a check named '{name}' is already registered; edit it "
                      f"in Settings → Checks", 409, code="duplicate")
    entry = {"name": name, "run": command, "root": root,
             "help": str(payload.get("help") or
                         "registered from verification readiness; coverage "
                         "not yet reviewed")[:200]}
    consents = {"network": bool(payload.get("network")),
                "prepare": bool(payload.get("prepare"))}
    included = []
    if consents["prepare"] and candidate:
        for need in candidate["prerequisites"]:
            if need["kind"] != "prepare":
                continue
            if need["network"] and not consents["network"]:
                raise Refused(
                    f"the preparation step '{need['name']}' ({need['run']}) "
                    f"downloads dependencies. Network use is its own consent: "
                    f"send network: true to include it, or prepare the "
                    f"checkout another way", 409, code="network-consent")
            steps = doc.setdefault("prepare", [])
            if not any(isinstance(s, dict) and s.get("name") == need["name"]
                       for s in steps):
                steps.append({"name": need["name"], "run": need["run"],
                              "network": bool(need["network"])})
            entry.setdefault("prepare", []).append(need["name"])
            included.append(need["name"])
    checks.append(entry)
    profs = doc.setdefault("profiles", {})
    for intent in intents:
        block = profs.setdefault(intent, {})
        verify_list = block.setdefault("verify", [])
        if name not in verify_list:
            verify_list.append(name)
    if doc.get("gates") != gates_before:       # by construction; said aloud
        raise Refused("a registration draft must not change a merge rule", 500)
    checked = settings.validate({"document": doc})
    preview = settings.preview({"document": doc})
    notes = [n for n in (candidate or {}).get("prerequisites", [])
             if n["kind"] in ("data", "secrets", "network")]
    return {
        "document": doc, "revision": base["revision"],
        "baseRevision": base["revision"],
        "check": entry, "prepare": included, "consents": consents,
        "valid": checked["ok"], "errors": checked["errors"],
        "changes": preview["changes"], "sideEffects": preview["sideEffects"],
        "notes": notes,
        "gatesUnchanged": True,
        "afterApply": ("Registering makes the check runnable and optional. It "
                       "does not run it and requires it nowhere: the merge "
                       "rules read exactly as they do now."),
        "apply": "POST /api/settings/apply",
        "wroteNothing": True,
    }


# ── review: a person's record, bound to the check's identity ──────────

def _evidence(evidence_id: str, name: str, check_id: str, label: str) -> dict:
    import verify
    if not verify.is_evidence_id(evidence_id):
        raise Refused(f"the {label} evidence id {evidence_id!r} is not one")
    record = verify.read(evidence_id)
    if record is None:
        raise Refused(f"no evidence record {evidence_id} on this board", 404)
    if record.get("check") != name:
        raise Refused(f"evidence {evidence_id} is a run of "
                      f"'{record.get('check')}', not of '{name}'", 409)
    if not str(record.get("task") or "").endswith(".md"):
        raise Refused(f"evidence {evidence_id} is not about a card", 409)
    if record.get("checkId") != check_id:
        raise Refused(f"evidence {evidence_id} ran an earlier definition of "
                      f"'{name}'; a review is of the check as it is defined "
                      f"now — run it again", 409, code="definition-changed")
    return record


def review(payload: dict) -> dict:
    """Record that a person reviewed one check: its program, a current pass
    and a controlled failure of the same definition — or the reason a
    failing case is not appropriate. Refused, with nothing written, when
    the pass is not a current, bound, clean `passed`, when the failure is
    not a verdict of `failed` (a preparation failure, a denial, a
    cancellation and an error reached none), or when the page is stale.

    It changes no gate and no acceptance. It is what *readiness* rests on.
    """
    import verify
    payload = payload if isinstance(payload, dict) else {}
    request, _ = setupguide._check_request(dict(payload, area="verification"))
    with setupguide.LOCK:
        replay = setupguide._replayed(request, "verification-review",
                                      "verification")
        if replay is not None and replay.get("duplicate"):
            return replay       # done: answer with the first result
        # started and never finished: the review is redone from the top,
        # which writes the same entry — nothing is written twice
        read = setupguide.read_decisions()
        setupguide._guard_revision(payload, read)
        name = str(payload.get("check") or "")
        defined = settings._checks_of(profiles.document())
        if name not in defined:
            raise Refused(f"no registered check '{name}' — register it first",
                          404)
        check = defined[name]
        note = settings.scrub(str(payload.get("note") or "").strip())
        if len(note) < 10 or len(note) > NOTE_CAP:
            raise Refused("say what you read and what you checked (10–1000 "
                          "characters): a review with no note is not a review")
        passed = _evidence(str(payload.get("passEvidence") or ""), name,
                           check["checkId"], "passing")
        reading = verify.outcome_now(passed)
        if reading["outcome"] != "passed":
            raise Refused(f"evidence {passed['id']} reads "
                          f"{reading['outcome']}, not passed"
                          + (f" — {reading['why']}" if reading.get("why") else "")
                          + ". Only a current pass can support a review", 409,
                          code="not-passed", outcome=reading["outcome"])
        source = passed.get("source") or {}
        if not source.get("bound") or source.get("dirty"):
            raise Refused(f"evidence {passed['id']} ran against "
                          + ("uncommitted work" if source.get("dirty") else
                             "a source bench could not bind")
                          + "; commit, then run it again", 409,
                          code="source-unbound")
        failing_id = str(payload.get("failEvidence") or "")
        waived = settings.scrub(str(payload.get("failingWaived") or "").strip())
        fail_commit = None
        if failing_id:
            failed = _evidence(failing_id, name, check["checkId"], "failing")
            if failed.get("outcome") != "failed":
                raise Refused(
                    f"evidence {failed['id']} reads {failed.get('outcome')}, "
                    f"which is not the check saying no"
                    + (": a preparation failure, a denial, a cancellation or "
                       "an error reached no verdict"
                       if failed.get("outcome") in NO_VERDICT else "")
                    + ". A controlled failure is a run that failed on a "
                      "deliberately broken fixture", 409, code="not-failed",
                    outcome=failed.get("outcome"))
            fail_commit = ((failed.get("source") or {}).get("commit") or "")[:12]
        elif len(waived) < 10:
            raise Refused("name the controlled failing run of this check, or "
                          "say why a failing case is not appropriate "
                          "(failingWaived, 10+ characters)", code="no-failure")
        who = setupguide._actor()
        if not who["name"]:
            raise Refused("a review names who made it, and this board has no "
                          "identity — set git config user.name in the work "
                          "state", 409)
        assignee = _assignee_of_gap()
        independent = not (assignee and assignee == who["name"])
        entry = {
            "checkId": check["checkId"],
            "programId": (passed.get("program") or {}).get("programId"),
            "passCommit": (source.get("commit") or "")[:12],
            "failCommit": fail_commit,
            "failingWaived": waived or None,
            "note": note, "actor": who["name"], "actorSource": who["source"],
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "independent": independent,
            "independentWhy": None if independent else (
                f"{who['name']} is also the assignee of the verification setup "
                f"card, so this review is not independent of the work"),
        }
        doc = read["doc"]
        block = doc.setdefault("verification", {})
        if not isinstance(block, dict):
            raise Refused("setup/decisions.json has a malformed verification "
                          "block; bench refuses to write over it", 409)
        block.setdefault("checks", {})[name] = entry
        setupguide._journal_put(request, {"kind": "verification-review",
                                          "area": "verification",
                                          "status": "started",
                                          "at": entry["at"]})
        committed = setupguide._write_decisions(doc,
                                                f"verification review of {name}")
        result = {"area": "verification", "check": name, "review": entry,
                  "readiness": "ready", "committed": committed,
                  "revision": setupguide.revision(),
                  "changesGates": False,
                  "note": ("Ready means usable. No merge rule requires it "
                           "until you activate one, separately.")}
        setupguide._journal_put(request, {"kind": "verification-review",
                                          "area": "verification",
                                          "status": "done", "at": entry["at"],
                                          "result": result})
        return result


# ── activate: a consequence preview, never a write ─────────────────────

def activate(payload: dict) -> dict:
    """What making ready checks (and a review policy) mandatory for one
    guarded merge would change — Settings' own preview of a draft that
    adds to the rule and never weakens it. Refused for a check that is not
    ready: registration or a passing run is not a reason to require one.
    The page sends the returned document to `POST /api/settings/apply`."""
    payload = payload if isinstance(payload, dict) else {}
    scope = str(payload.get("scope") or "")
    if scope not in profiles.GATE_SCOPES:
        raise Refused(f"the scope is one of "
                      f"{', '.join(sorted(profiles.GATE_SCOPES))}")
    wanted = payload.get("checks") or []
    review_policy = str(payload.get("review") or "none")
    if review_policy not in ("none", "bench", "github", "separate-reviewer"):
        raise Refused("review is one of none, bench, github, separate-reviewer")
    if not isinstance(wanted, list) or not all(isinstance(w, str) for w in wanted):
        raise Refused("checks is a list of check names")
    if not wanted and review_policy == "none":
        raise Refused("choose at least one check or a review policy")
    states = {s["name"]: s for s in check_states()}
    not_ready = [w for w in wanted if (states.get(w) or {}).get("readiness") != "ready"]
    if not_ready:
        raise Refused(
            "not ready: " + ", ".join(
                f"{w} ({(states.get(w) or {}).get('readiness', 'not registered')})"
                for w in not_ready)
            + ". A check is required only after a person has reviewed its "
              "program and results — registering or passing is not enough",
            409, code="not-ready", notReady=not_ready)
    base = _base_document()
    doc = base["document"]
    block = doc.setdefault("gates", {})
    if not isinstance(block, dict):
        raise Refused("the document's gates block is not an object", 409)
    gate = block.setdefault(scope, {})
    if not isinstance(gate, dict):
        raise Refused(f"the {scope} gate is not an object", 409)
    existing = list(gate.get("checks") or [])
    have = {(c if isinstance(c, str) else c.get("check")) for c in existing}
    gate["checks"] = existing + [w for w in wanted if w not in have]
    strength = ("none", "bench", "github", "separate-reviewer")
    kept_review = gate.get("review") or "none"
    if review_policy != "none" and kept_review not in strength[1:]:
        gate["review"] = review_policy
    elif review_policy != "none" and kept_review != review_policy:
        # an existing review requirement is preserved; a different one is
        # a change for Settings → Merge rules, not an addition here
        raise Refused(f"the {scope} rule already requires a {kept_review} "
                      f"review; change it in Settings → Merge rules if that "
                      f"is what you mean", 409, code="review-exists")
    if not gate["checks"]:
        gate.pop("checks")
    preview = settings.preview({"document": doc})
    limits = review_limits()
    chosen = gate.get("review") or "none"
    return {
        "document": doc, "revision": base["revision"],
        "baseRevision": base["revision"], "scope": scope,
        "requires": {"checks": list(gate.get("checks") or []),
                     "review": chosen},
        "preserved": {"checks": existing, "review": kept_review},
        "valid": preview["ok"], "errors": preview["errors"],
        "changes": preview["changes"], "pending": preview["pending"],
        "sideEffects": preview["sideEffects"],
        "reviewLimit": limits.get(chosen),
        "independence": limits["independence"],
        "effect": (f"After Apply, the {scope} merge is refused until bench's own "
                   f"evidence passes for every required check against the exact "
                   f"commit about to merge"
                   + (f", and a {chosen} review is satisfied" if chosen != "none"
                      else "")
                   + ". Nothing runs because of the write."),
        "apply": "POST /api/settings/apply",
        "wroteNothing": True,
    }


def text(found: dict) -> str:
    """A terminal reading, for `board.py --setup-guide --verification`."""
    lines = [found["bootstrap"]["headline"], ""]
    for c in found["checks"]:
        lines.append(f"  {c['name']:<20} {c['readiness']:<13} last run: {c['run']}")
    if not found["checks"]:
        lines.append("  no registered checks")
    lines.append("")
    lines.append("Candidates (inferred, never run, coverage unproven):")
    for c in found["discovery"]["candidates"]:
        refs = ", ".join(s["ref"] for s in c["sources"])
        lines.append(f"  [{c['confidence']}] {c['command']}  ← {refs}"
                     + ("" if c["expressible"] else "  (needs a wrapper)"))
    if not found["discovery"]["candidates"]:
        lines.append("  none found")
    return "\n".join(lines) + "\n"
