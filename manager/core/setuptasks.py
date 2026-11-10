"""Setup cards from discovered gaps: one previewed, selectable set of
ordinary Bench cards, grounded in what was actually inspected (task 89).

The setup guide (`setupguide.py`, task 88) reads six areas and proposes
at most one generic card per area. This is the next step: inspect the
application itself — read-only, file by file, running nothing — and turn
each real gap into a card whose brief cites the files and settings that
support it. Five gaps are known, each with a **stable identity** that is
written into the card's head (`**Setup gap:** <id>`) and into the
portable setup record, so a retry, a replica or a second request adopts
the card that exists rather than writing another:

    instructions   no agent workflow instructions in the application
    fixtures       services or data the tests need, and no disposable
                   fixture or preparation step for them
    preview        something to run, and no driver to start it
    ui-smoke       a user interface, and no browser test of it
    verification   no named check bench can run

Three things this deliberately does not do:

- **It does not invent work.** A gap is proposed only when inspection
  found the capability *absent*; a capability present (a Playwright
  config, a registered check that passed, a runnable driver) proposes
  nothing, and says what it found. A fact bench could not read is
  `unknown`, which is not absent: the gap is shown with the unread file as
  a blocker and cannot be created until somebody can read it, because a
  card built on an unread file asks an agent to guess.
- **It does not order work by prose.** A prerequisite is written as a
  `**Depends on:**` line naming card numbers — the one lifecycle field a
  phase and a reader both honour — and only for real prerequisites:
  disposable data before the startup that uses it, startup before the
  browser tests that drive it. Registering a check and requiring it in a
  merge rule are the operator's, in Settings, and are shown as steps with
  their owner rather than as cards an agent could not finish.
- **It does not treat delivery as proof.** A card reaching `done/` is
  delivery metadata. The gap's own reading is recomputed every time from
  the application and bench's own records, so a merged card whose gap is
  still found says so, and an area reads `tested` only when bench's own
  check run says it.

Reading writes nothing. Preview writes nothing. Create writes the cards
and the links in one commit (under `BOARD_COMMIT_MOVES`), refuses a stale
preview, and is idempotent per gap. Start is a separate action that rides
the normal **▸ start work** path and refuses — before anything moves —
when a prerequisite or the launch preflight says no.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import config
import profiles
import settings
import setupguide
import taskfiles

Refused = setupguide.Refused

GAPS = ("instructions", "fixtures", "preview", "ui-smoke", "verification")
# Which setup area a gap belongs to: a decision about the area (deferred,
# not applicable) holds its gaps back with it.
AREA_OF = {"instructions": "instructions", "fixtures": "verification",
           "preview": "preview", "ui-smoke": "verification",
           "verification": "verification"}
# Real prerequisites only. Each names gaps that must be delivered first
# *when they are themselves open*; a prerequisite already in place is no
# reason to wait.
AFTER = {"instructions": (), "fixtures": (), "preview": ("fixtures",),
         "ui-smoke": ("fixtures", "preview"), "verification": ("fixtures",)}
TITLES = {
    "instructions": "Write project instructions for coding agents",
    "fixtures": "Provide disposable test data and a preparation step",
    "preview": "Make the application start reliably from a worktree",
    "ui-smoke": "Add a browser smoke test of the user interface",
    "verification": "Provide a reproducible verification program",
}
PRIORITIES = ("High", "Medium", "Low")
NOTES_CAP = 2000
TITLE_CAP = 120
RELATED_WORDS = {
    "instructions": ("agents.md", "claude.md", "instructions", "conventions"),
    "fixtures": ("fixture", "seed", "test data", "disposable"),
    "preview": ("driver", "preview", "dev server", "startup"),
    "ui-smoke": ("playwright", "cypress", "browser test", "e2e", "smoke"),
    "verification": ("test suite", "verification", "named check", "unit test"),
}
STARTABLE = ("backlog", "to-do", "in-progress")

UI_DEPS = ("react", "vue", "svelte", "@angular/core", "next", "nuxt",
           "vite", "solid-js", "preact", "@sveltejs/kit", "astro")
BROWSER_DEPS = ("@playwright/test", "playwright", "cypress", "puppeteer",
                "webdriverio", "selenium-webdriver")
BROWSER_FILES = tuple(f"{tool}.config.{ext}" for tool in ("playwright", "cypress")
                      for ext in ("ts", "js", "mjs", "cjs"))
SERVICE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml",
                 "compose.yaml", ".env.example", ".env.sample",
                 ".env.template", "alembic.ini", "prisma/schema.prisma")
SERVICE_DIRS = ("migrations", "db/migrations")
FIXTURE_DIRS = ("tests/fixtures", "test/fixtures", "fixtures", "seeds",
                "db/seeds", "spec/fixtures", "testdata")
START_SCRIPTS = ("dev", "start", "serve", "preview")
START_TARGETS = ("run", "serve", "dev", "start")
NPM_NO_TEST = "no test specified"


# ── inspection: read the application, run nothing ─────────────────────

def _probe(app: Path, rel: str) -> dict:
    """One file, as present / absent / unknown — unknown being a file that
    exists and could not be read, which is a different fact from absent."""
    path = app / rel
    try:
        if not path.exists():
            return {"ref": rel, "finding": "absent"}
        if path.is_dir():
            return {"ref": rel, "finding": "present", "dir": True}
        text = path.read_text(encoding="utf-8")
        return {"ref": rel, "finding": "present", "text": text}
    except (OSError, UnicodeDecodeError) as exc:
        return {"ref": rel, "finding": "unknown",
                "detail": f"could not be read ({type(exc).__name__})"}


def inspect() -> dict:
    """What the application holds that bears on the five gaps. Paths are
    relative to the application, so every reference is portable."""
    app = config.app_repo()
    found: dict = {"readable": app.is_dir(), "refs": {}}
    if not found["readable"]:
        return found

    def probe(rel: str) -> dict:
        found["refs"][rel] = hit = _probe(app, rel)
        return hit

    pkg = probe("package.json")
    scripts, deps = {}, set()
    if pkg["finding"] == "present" and not pkg.get("dir"):
        try:
            doc = json.loads(pkg["text"])
            scripts = doc.get("scripts") or {}
            for key in ("dependencies", "devDependencies"):
                deps |= set((doc.get(key) or {}).keys())
            if not isinstance(scripts, dict):
                scripts = {}
        except (json.JSONDecodeError, AttributeError):
            pkg.update(finding="unknown", detail="does not parse as JSON")
    make = probe("Makefile")
    targets = set()
    if make["finding"] == "present" and not make.get("dir"):
        targets = set(re.findall(r"^([A-Za-z][\w-]*)\s*:", make["text"], re.M))
    for rel in ("pyproject.toml", "setup.py", "manage.py", "Cargo.toml",
                "go.mod", "Procfile", "tests", "test", "index.html",
                "public/index.html", "src/index.html", "templates",
                *setupguide.INSTRUCTION_FILES, *BROWSER_FILES, "cypress",
                "e2e", "tests/e2e", *SERVICE_FILES, *SERVICE_DIRS,
                *FIXTURE_DIRS):
        probe(rel)
    has = lambda rel: found["refs"].get(rel, {}).get("finding") == "present"

    test_cmds = []
    test = scripts.get("test")
    if isinstance(test, str) and NPM_NO_TEST not in test:
        test_cmds.append(("package.json", "the `test` script"))
    if "test" in targets or "check" in targets:
        test_cmds.append(("Makefile", "a `test` or `check` target"))
    if has("Cargo.toml"):
        test_cmds.append(("Cargo.toml", "`cargo test`"))
    if has("go.mod"):
        test_cmds.append(("go.mod", "`go test ./...`"))
    if (has("pyproject.toml") or has("setup.py")) and (has("tests") or has("test")):
        test_cmds.append(("tests" if has("tests") else "test",
                          "a Python test directory"))
    start = [("package.json", f"the `{s}` script") for s in START_SCRIPTS
             if isinstance(scripts.get(s), str)]
    start += [("Makefile", f"a `{t}` target") for t in START_TARGETS
              if t in targets]
    start += [(rel, why) for rel, why in (("manage.py", "a Django project"),
                                          ("Procfile", "a Procfile"))
              if has(rel)]
    ui = [("package.json", f"a `{d}` dependency") for d in UI_DEPS if d in deps]
    ui += [(rel, "a page or template") for rel in
           ("index.html", "public/index.html", "src/index.html", "templates")
           if has(rel)]
    browser = [("package.json", f"a `{d}` dependency")
               for d in BROWSER_DEPS if d in deps]
    browser += [(rel, "browser test configuration") for rel in
                (*BROWSER_FILES, "cypress", "e2e", "tests/e2e") if has(rel)]
    services = [(rel, "service or data configuration")
                for rel in (*SERVICE_FILES, *SERVICE_DIRS) if has(rel)]
    fixtures = [(rel, "a fixture directory") for rel in FIXTURE_DIRS if has(rel)]
    found.update(testCommands=test_cmds, start=start, ui=ui, browser=browser,
                 services=services, fixtureDirs=fixtures,
                 instructions=[n for n in setupguide.INSTRUCTION_FILES if has(n)])
    found["unknown"] = sorted(rel for rel, hit in found["refs"].items()
                              if hit["finding"] == "unknown")
    return found


def _ev(ref: str, finding: str, detail: str, kind: str = "file") -> dict:
    return {"ref": ref, "kind": kind, "finding": finding,
            "detail": settings.scrub(detail)}


def _from(pairs, finding="present") -> list[dict]:
    return [_ev(rel, finding, why) for rel, why in pairs]


def _unread(found: dict, refs) -> list[dict]:
    return [_ev(rel, "unknown", found["refs"][rel].get("detail") or
                "could not be read")
            for rel in refs if rel in found["unknown"]]


# ── the gaps ───────────────────────────────────────────────────────────

def _authority(gap: str, runs: bool) -> dict:
    """What a work run on this card would need, against what this board
    grants — stated, never broadened. A missing grant is the operator's to
    add; the card does not ask for it and bench does not add it."""
    needs = ["read the application checkout", "edit files in the card's "
             "own worktree and commit there"]
    missing, notes = [], []
    if runs:
        needs.append("run the project's own commands (tests, the app)")
        if not config.agent_commands():
            missing.append("BOARD_AGENT_COMMANDS is empty, so a work run may "
                           "edit files but not run the project's commands; "
                           "the operator adds the prefixes in "
                           "manager/local/.env")
    if gap in ("fixtures", "ui-smoke"):
        notes.append("installing dependencies may need the network, which "
                     "bench does not bound")
    if gap in ("verification", "ui-smoke"):
        notes.append("registering the program as a named check, and requiring "
                     "it in a merge rule, are the operator's in Settings — not "
                     "this card's")
    return {"needs": needs, "missing": missing, "notes": notes}


def gaps(found: dict | None = None) -> dict:
    """Every gap inspection supports, and every capability it found in
    place — the second list is why a working project gets no card."""
    found = inspect() if found is None else found
    out, covered = {}, []
    if not found["readable"]:
        return {"gaps": {}, "covered": [], "unreadable": (
            "the application root could not be read, so no gap is proposed: "
            "an unread project is unknown, not empty")}
    doc = profiles.document()
    verif = settings.verification(doc)
    names = {c["name"] for c in verif["checks"]}
    steps = [s["name"] for s in verif.get("prepare") or []]
    outcomes = setupguide._newest_outcomes(names) if names else {}
    driver = config.driver_path()
    runnable = bool(driver and os.access(driver, os.X_OK))

    def gap(gid, why, evidence, deliver, accept, runs, unread=()):
        blockers = [f"bench could not read `{e['ref']}` ({e['detail']}); "
                    f"fix it or check it by hand first — a card built on an "
                    f"unread file asks the agent to guess"
                    for e in evidence if e["finding"] == "unknown"]
        out[gid] = {"id": gid, "title": TITLES[gid], "area": AREA_OF[gid],
                    "why": why, "evidence": evidence,
                    "certainty": "unknown" if blockers else "absent",
                    "deliverables": deliver, "acceptance": accept,
                    "after": list(AFTER[gid]), "authority": _authority(gid, runs),
                    "blockers": blockers}

    # instructions
    if found["instructions"]:
        covered.append({"gap": "instructions", "found": _from(
            (n, "agent instructions") for n in found["instructions"])})
    else:
        cmds = found["testCommands"] + found["start"]
        gap("instructions",
            "No agent workflow instructions were found at the application's "
            "root, so a run launched from this board works from its card alone.",
            [_ev(n, "absent", "not at the application root")
             for n in setupguide.INSTRUCTION_FILES] + _from(cmds),
            ["An instructions file at the application root (AGENTS.md is read "
             "by every agent bench drives) describing this project's real "
             "conventions: layout, how to build, test and run it, and what a "
             "change must include to be done."]
            + ([f"Name the commands inspection found: "
                f"{', '.join(f'{w} in `{r}`' for r, w in cmds)}."] if cmds else
               ["No build, test or start command was found; say how the "
                "project is built and tested, or that it is not."]),
            ["The file exists at the application root and the team has "
             "reviewed it", "Every command it names runs as written in a "
             "fresh worktree"], runs=False)

    # fixtures
    if steps or found["fixtureDirs"]:
        covered.append({"gap": "fixtures", "found": _from(found["fixtureDirs"])
                        + [_ev("profiles.json: prepare", "present",
                               f"preparation step {s}", "policy") for s in steps]})
    elif found["services"]:
        gap("fixtures",
            "The application configures services or data its tests may need, "
            "and no disposable fixture directory or preparation step was found.",
            _from(found["services"])
            + [_ev(rel, "absent", "no fixture directory") for rel in FIXTURE_DIRS[:3]]
            + [_ev("profiles.json: prepare", "absent",
                   "no preparation step is declared", "policy")],
            ["Disposable test data (fixtures or seeds) the tests and the "
             "preview can use, never a live database.",
             "One non-interactive preparation command that brings a fresh "
             "worktree to a testable state and exits non-zero on failure."],
            ["A fresh worktree is prepared by one command with no prompts",
             "Running it twice leaves the same state; nothing live is touched"],
            runs=True)

    # preview
    if runnable:
        covered.append({"gap": "preview", "found": [_ev(
            "manager/local/driver/start", "present", "a runnable driver",
            "setting")]})
    elif driver:
        pass        # present but not executable: a chmod for a person, not a card
    elif found["start"]:
        gap("preview",
            "The application has a way to run, and there is no driver, so a "
            "review card cannot start it from its own worktree.",
            _from(found["start"]) + [_ev("manager/local/driver/start", "absent",
                                         "no driver", "setting")],
            ["An executable driver following core/driver.example: refuse fast "
             "with a printed reason, print `DRIVE URL: <url>` when the app "
             "answers, run until SIGTERM.",
             "It starts the app from the task's worktree on a free loopback "
             "port, using the disposable data, never live services."],
            ["Driving a review card prints a URL that serves the app",
             "Parking it stops the app and leaves no process behind",
             "A missing dependency is refused with a reason, not a hang"],
            runs=True)

    # ui-smoke
    if found["browser"]:
        covered.append({"gap": "ui-smoke", "found": _from(found["browser"])})
    elif found["ui"]:
        gap("ui-smoke",
            "The application has a user interface and no browser test of it "
            "was found.",
            _from(found["ui"]) + [_ev(rel, "absent", "no browser test "
                                      "configuration") for rel in BROWSER_FILES[:2]]
            + _unread(found, ["package.json"]),
            ["A small browser smoke test that starts the app the way the "
             "driver does, loads its main page and fails on an error.",
             "Runnable non-interactively, exiting non-zero on failure, so it "
             "can be registered as a named check."],
            ["The test fails against a deliberately broken page",
             "It passes from a fresh worktree with the disposable data"],
            runs=True)

    # verification
    passed = any(o == "passed" for o in outcomes.values())
    if names:
        covered.append({"gap": "verification", "found": [_ev(
            "profiles.json: checks", "present",
            f"named check {n}: " + outcomes.get(n, "not run"), "policy")
            for n in sorted(names)], "tested": passed})
    else:
        tests = found["testCommands"]
        gap("verification",
            "No named check is registered, so bench cannot verify the work "
            "itself and no merge rule can require evidence."
            + (" A test command exists and needs a reproducible, "
               "non-interactive entry point." if tests else
               " No test command was found in the application either."),
            [_ev("profiles.json: checks", "absent", "no named check", "policy")]
            + (_from(tests) if tests else
               [_ev(r, "absent", "no test command")
                for r in ("package.json", "Makefile", "tests")
                if found["refs"].get(r, {}).get("finding") != "unknown"])
            + _unread(found, ["package.json", "Makefile"]),
            (["A non-interactive program that runs "
              + ", ".join(f"{w} (`{r}`)" for r, w in tests)
              + " from a fresh worktree and exits non-zero on failure."]
             if tests else
             ["Tests for the project's core behaviour, and one "
              "non-interactive program that runs them and exits non-zero on "
              "failure."])
            + ["A short note of what the program covers and what it does not."],
            ["The program fails on a deliberately broken fixture",
             "It passes on a clean checkout with no prompts and no live services"],
            runs=True)
    return {"gaps": out, "covered": covered, "unreadable": None}


# ── existing cards ─────────────────────────────────────────────────────

GAP_HEADER_RE = re.compile(r"^\*\*Setup gap:\*\*\s*([a-z-]+)\s*$", re.M)


def _cards() -> list[dict]:
    """Every card on the board and in the archive, with the setup gap its
    head names, read once."""
    found = []
    for stage in (*config.STAGE_DIRS, "archive"):
        directory = config.TASKS / stage
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".md") or not taskfiles.NUMBER_RE.match(name):
                continue
            try:
                text = (directory / name).read_text(encoding="utf-8",
                                                    errors="replace")
            except OSError:
                continue
            title = taskfiles._first(taskfiles.TITLE_RE, text) or name[:-3]
            mark = GAP_HEADER_RE.search(text)
            found.append({"filename": name, "stage": stage,
                          "number": taskfiles.NUMBER_RE.match(name).group(1),
                          "title": title, "gap": mark.group(1) if mark else None,
                          "dependsOn": taskfiles._depends_on(text)})
    return found


def _failed_run(filename: str) -> bool:
    import state
    with state.LOCK:
        runs = [r for r in state.AGENTS.values() if r.get("task") == filename]
    runs.sort(key=lambda r: r.get("started") or 0)
    return bool(runs and runs[-1].get("failure"))


def _outcome(card: dict | None, still_found: bool, link: dict) -> dict:
    """What became of a setup card, and what that does and does not mean."""
    if card is None:
        return {"word": "missing", "stage": None,
                "meaning": "the linked card is not on the board or in the "
                           "archive; the gap can be proposed again"}
    stage = card["stage"]
    word = {"backlog": "open", "to-do": "open", "in-progress": "in progress",
            "review": "in review", "done": "delivered",
            "archive": "archived"}[stage]
    if stage == "in-progress" and _failed_run(card["filename"]):
        word = "run failed"
    meaning = {
        "open": "proposed and not started",
        "in progress": "being worked on",
        "run failed": "its latest run failed; the card says why",
        "in review": "built and waiting on review",
        "archived": "taken out of the flow without delivery; the gap can be "
                    "proposed again",
    }.get(word, "")
    if word == "delivered":
        meaning = ("delivered — and re-inspection still finds the gap, so the "
                   "merge did not make the capability" if still_found else
                   "delivered, and re-inspection no longer finds the gap. That "
                   "is not a test: the area reads tested only when bench's own "
                   "check run passes")
    elif not still_found and word not in ("archived",):
        word, meaning = "superseded", ("re-inspection no longer finds this gap; "
                                       "the card may no longer be needed")
    elif link.get("fingerprint") and still_found and \
            link["fingerprint"] != link.get("now"):
        meaning += "; the inspected facts changed since it was written"
    return {"word": word, "stage": stage, "meaning": meaning}


def _related(gap: str, cards: list[dict], linked: str | None) -> list[dict]:
    words = RELATED_WORDS[gap]
    return [{"filename": c["filename"], "number": c["number"],
             "title": c["title"], "stage": c["stage"],
             "exact": c["gap"] == gap}
            for c in cards
            if c["filename"] != linked and c["stage"] != "archive" and
            (c["gap"] == gap or (c["gap"] is None and
                                 any(w in c["title"].lower() for w in words)))]


# ── the reading ────────────────────────────────────────────────────────

def _fingerprint(entry: dict) -> str:
    return setupguide._digest({"id": entry["id"], "certainty": entry["certainty"],
                               "evidence": [(e["ref"], e["finding"])
                                            for e in entry["evidence"]]})


def proposals(doc: dict | None = None, areas: dict | None = None) -> dict:
    """The proposed set, read now: each gap with its evidence, its link to
    a card if one exists and what became of it, related existing cards,
    and whether it is selected by default. Writes nothing."""
    if doc is None:
        doc = setupguide.read_decisions()["doc"]
    decisions = doc.get("decisions") or {}
    links = doc.get("tasks") or {}
    held = doc.get("gaps") or {}
    found = gaps()
    cards = _cards()
    by_file = {c["filename"]: c for c in cards}
    exact = {c["gap"]: c for c in cards if c["gap"] and c["stage"] != "archive"}
    rows = []
    for gid in GAPS:
        entry = found["gaps"].get(gid)
        link = links.get(gid) if isinstance(links.get(gid), dict) else None
        if entry is None and link is None:
            continue
        row = dict(entry) if entry else {
            "id": gid, "title": TITLES[gid], "area": AREA_OF[gid],
            "certainty": "resolved", "evidence": [], "blockers": [],
            "after": list(AFTER[gid]), "why": "re-inspection no longer finds "
            "this gap", "deliverables": [], "acceptance": [],
            "authority": _authority(gid, False)}
        row["fingerprint"] = _fingerprint(row) if entry else None
        card = by_file.get(link["filename"]) if link else None
        if card is None and gid in exact and not link:
            card = exact[gid]          # a card with this identity already exists
        row["card"] = None
        if card is not None or link:
            shown = dict(link or {}, now=row["fingerprint"])
            row["card"] = {"filename": (card or link)["filename"],
                           "number": (card or link).get("number"),
                           "title": (card or link).get("title"),
                           "linked": bool(link and card and
                                          card["filename"] == link["filename"]),
                           "dependsOn": (card or {}).get("dependsOn", []),
                           **_outcome(card, entry is not None, shown)}
        decision = held.get(gid) if isinstance(held.get(gid), dict) else None
        area = decisions.get(AREA_OF[gid]) if isinstance(
            decisions.get(AREA_OF[gid]), dict) else None
        row["deferred"] = None
        if decision and decision.get("status") == "deferred":
            row["deferred"] = {k: decision.get(k) for k in
                               ("reason", "actor", "at")} | {"by": "gap"}
        elif area and area.get("status") in ("deferred", "not-applicable"):
            row["deferred"] = {"reason": area.get("reason"),
                               "actor": area.get("actor"), "at": area.get("at"),
                               "by": f"area {area['status']}"}
        # A delivered card whose gap is still found may be followed up by a
        # new card, but never by default: a person reads why first.
        word = (row["card"] or {}).get("word")
        live = word is not None and word not in ("archived", "missing",
                                                 "delivered")
        row["related"] = _related(gid, cards, (row["card"] or {}).get("filename"))
        row["creatable"] = bool(entry and not live and not row["blockers"])
        row["selected"] = bool(row["creatable"] and not row["deferred"]
                               and word != "delivered")
        rows.append(row)
    return {"gaps": rows, "covered": found["covered"],
            "unreadable": found["unreadable"], "steps": _steps(rows, found),
            "order": list(GAPS)}


def _steps(rows: list[dict], found: dict) -> list[dict]:
    """The steps after the cards that are not cards: a person's, in
    Settings. Shown in sequence so "check registration, then merge rules"
    is visible without inventing a card no agent could finish."""
    tested = any(c.get("tested") for c in found["covered"]
                 if c["gap"] == "verification")
    registered = any(c["gap"] == "verification" for c in found["covered"])
    waits = [r["id"] for r in rows if r["id"] in ("verification", "ui-smoke")]
    return [
        {"id": "register-checks", "owner": "you", "section": "verification",
         "label": "Register the delivered programs as named checks",
         "after": waits, "state": "done" if registered else "waiting",
         "detail": "Settings → Checks. Registering does not require anything."},
        {"id": "merge-rules", "owner": "you", "section": "acceptance",
         "label": "Decide which checks a merge requires",
         "after": ["register-checks"],
         "state": "available" if tested else "waiting",
         "detail": "Settings → Merge rules — offered once a registered check "
                   "has passed under bench's own runner, never enabled here."},
    ]


# ── preview: the exact cards, writing nothing ─────────────────────────

FORBIDDEN_LINE = re.compile(r"^\s*(#|\*\*[A-Za-z][A-Za-z ]{0,40}:\*\*|<!--\s*bench:"
                            r"|PR REVIEW:|NOT READY:|RELEVANCE REVIEW:|ADDRESSED:)",
                            re.I)


def _clean_edit(item: dict) -> dict:
    gid = item.get("gap")
    if gid not in GAPS:
        raise Refused(f"unknown setup gap {gid!r}; one of {', '.join(GAPS)}")
    title = item.get("title")
    if title is not None:
        if not isinstance(title, str) or "\n" in title:
            raise Refused(f"{gid}: a title is one line of text")
        title = " ".join(title.split())
        if not 3 <= len(title) <= TITLE_CAP:
            raise Refused(f"{gid}: a title is 3–{TITLE_CAP} characters")
        if settings.MASK in title:
            raise Refused(f"{gid}: a masked value cannot be submitted")
    priority = item.get("priority") or "Medium"
    if priority not in PRIORITIES:
        raise Refused(f"{gid}: priority is one of {', '.join(PRIORITIES)}")
    notes = item.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > NOTES_CAP:
        raise Refused(f"{gid}: notes are text of at most {NOTES_CAP} characters")
    for line in notes.splitlines():
        if FORBIDDEN_LINE.match(line):
            raise Refused(f"{gid}: a note line may not be a heading, a header "
                          f"field or a marker the board parses: {line[:60]!r}")
    if settings.MASK in notes:
        raise Refused(f"{gid}: a masked value cannot be submitted")
    reuse = item.get("reuse")
    if reuse is not None and (not isinstance(reuse, str) or
                              Path(reuse).name != reuse or
                              not reuse.endswith(".md")):
        raise Refused(f"{gid}: reuse names a card file")
    return {"gap": gid, "title": title, "priority": priority,
            "notes": settings.scrub(notes.strip()), "reuse": reuse}


def _bullets(items) -> str:
    return "\n".join(f"- {i}" for i in items)


def card_text(row: dict, number: str, title: str, priority: str, notes: str,
              depends: list[str]) -> str:
    """One setup card, whole. Portable by construction: references are
    relative to the application or named settings, never a path from this
    disk, and everything is scrubbed of anything credential-shaped."""
    evidence = "\n".join(
        f"- `{e['ref']}` — {e['finding']}: {e['detail']}" for e in row["evidence"])
    auth = row["authority"]
    lines = [f"# {number} — {title}", "",
             "**Status:** Backlog",
             f"**Priority:** {priority} — proposed by the setup guide",
             "**Type:** Chore",
             f"**Setup gap:** {row['id']}"]
    if depends:
        lines.append(f"**Depends on:** {', '.join(depends)}")
    lines += ["", f"{row['why']} Proposed by the setup guide from what it "
              f"inspected; edit, refine or archive it freely.", "",
              "## What was inspected", "", evidence, "",
              "## What to build", "", _bullets(row["deliverables"]), "",
              "## Acceptance", "",
              "\n".join(f"- [ ] {a}" for a in row["acceptance"]), "",
              "## Authority this needs", "", _bullets(auth["needs"])]
    if auth["missing"]:
        lines += ["", "Not granted on the board that proposed this (bench does "
                  "not broaden it):", "", _bullets(auth["missing"])]
    if auth["notes"]:
        lines += ["", _bullets(auth["notes"])]
    lines += ["", "## Done is not proof", "",
              "Moving this card to done/ records delivery. The setup guide "
              "re-inspects the project, and the capability counts as tested "
              "only when bench's own check run passes against the delivered "
              "code."]
    if notes:
        lines += ["", "## Notes", "", notes]
    return settings.scrub("\n".join(lines) + "\n")


def _plan(payload: dict) -> dict:
    """The exact set a create would write, from a selection — the one
    function preview and create share, so a preview is the creation."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    selection = payload.get("selection")
    if not isinstance(selection, list) or not selection:
        raise Refused("select at least one setup gap")
    edits = [_clean_edit(i if isinstance(i, dict) else {}) for i in selection]
    if len({e["gap"] for e in edits}) != len(edits):
        raise Refused("each setup gap is selected once")
    read = proposals()
    rows = {r["id"]: r for r in read["gaps"]}
    cards = {c["filename"]: c for c in _cards()}
    chosen = {e["gap"]: e for e in edits}
    number = setupguide._next_number()
    planned, reused, warnings = {}, {}, []
    for gid in GAPS:                         # sequence order, so numbers ascend
        e = chosen.get(gid)
        if e is None:
            continue
        row = rows.get(gid)
        if row is None:
            raise Refused(f"{TITLES[gid]}: inspection finds no such gap now; "
                          f"re-read the guide", 409, stale=True)
        if e["reuse"]:
            card = cards.get(e["reuse"])
            if card is None or card["stage"] == "archive":
                raise Refused(f"{e['reuse']} is not on the board to reuse", 409)
            reused[gid] = card
            continue
        if not row["creatable"]:
            why = (row["blockers"][0] if row["blockers"] else
                   f"#{row['card']['number']} already holds it "
                   f"({row['card']['word']})" if row["card"] else
                   "it cannot be created now")
            raise Refused(f"{TITLES[gid]}: {why}", 409)
        filename = f"{number:02d}-{setupguide._slug(e['title'] or TITLES[gid])}.md"
        planned[gid] = {"filename": filename, "number": f"{number:02d}",
                        "edit": e, "row": row}
        number += 1
    out = []
    for gid, p in planned.items():
        depends = []
        for need in AFTER[gid]:
            if need in planned:
                depends.append(planned[need]["number"])
            elif need in reused:
                depends.append(reused[need]["number"])
            elif need in rows and rows[need]["card"] and \
                    rows[need]["card"]["word"] not in ("archived", "missing",
                                                       "superseded"):
                if rows[need]["card"]["word"] != "delivered":
                    depends.append(rows[need]["card"]["number"])
            elif need in rows and rows[need]["certainty"] != "resolved":
                warnings.append(
                    f"{TITLES[gid]} would come after {TITLES[need]}, which is "
                    f"neither selected nor on the board; no Depends on line is "
                    f"written for it — select it too, or add the number by hand "
                    f"when it exists")
        e = p["edit"]
        text = card_text(p["row"], p["number"], e["title"] or TITLES[gid],
                         e["priority"], e["notes"], depends)
        out.append({"gap": gid, "filename": p["filename"], "number": p["number"],
                    "title": e["title"] or TITLES[gid], "dependsOn": depends,
                    "text": text, "stage": "backlog",
                    "fingerprint": p["row"]["fingerprint"]})
    digest = hashlib.sha256(json.dumps(
        [[c["filename"], c["text"]] for c in out] +
        [[g, c["filename"]] for g, c in sorted(reused.items())],
        sort_keys=True).encode()).hexdigest()[:16]
    return {"cards": out, "reuse": [{"gap": g, "filename": c["filename"],
                                     "number": c["number"], "title": c["title"]}
                                    for g, c in reused.items()],
            "warnings": warnings, "digest": digest,
            "writes": "nothing — this is a preview",
            "starts": "nothing: no agent, worktree, phase or pull request"}


def preview(payload: dict) -> dict:
    with setupguide.LOCK:
        found = _plan(payload)
    return dict(found, revision=setupguide.revision())


# ── create ─────────────────────────────────────────────────────────────

def create(payload: dict) -> dict:
    """Write the previewed cards to backlog/ and link them, as one commit.

    Refused when the decisions moved (`expected`) or the preview the
    person confirmed is no longer what would be written (`digest`). A
    replayed request answers with its first result; an interrupted one
    adopts the cards it already wrote."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    request = payload.get("request")
    if not isinstance(request, str) or not setupguide.REQUEST_RE.match(request):
        raise Refused("a mutation names a request identity (8–80 letters, "
                      "digits or ._:-) so a repeat answers rather than acts twice")
    with setupguide.LOCK, taskfiles.WRITE_LOCK:
        again = setupguide._replayed(request, "cards", "proposals")
        if again and again.get("duplicate"):
            return again
        read = setupguide.read_decisions()
        doc = read["doc"]
        written = []
        if again:                                   # resume what was planned
            plan = {"cards": again.get("cards") or [],
                    "reuse": again.get("reuse") or []}
        else:
            setupguide._guard_revision(payload, read)
            plan = _plan(payload)
            if payload.get("digest") != plan["digest"]:
                raise Refused("what would be written differs from the preview "
                              "you confirmed (another card or decision landed "
                              "meanwhile); preview again", 409, stale=True,
                              digest=plan["digest"])
            setupguide._journal_put(request, {
                "kind": "cards", "area": "proposals", "status": "started",
                "at": taskfiles.now_iso(), "cards": plan["cards"],
                "reuse": plan["reuse"]})
        backlog = config.TASKS / "backlog"
        backlog.mkdir(parents=True, exist_ok=True)
        for card in plan["cards"]:
            path = backlog / card["filename"]
            taskfiles._refuse_escape(path)
            located = taskfiles.find_stage_of(card["filename"])
            if located is not None:
                text = (config.TASKS / located / card["filename"]).read_text(
                    encoding="utf-8", errors="replace")
                mark = GAP_HEADER_RE.search(text)
                if not mark or mark.group(1) != card["gap"]:
                    raise Refused(f"{card['filename']} exists and is not the "
                                  f"setup card for {card['gap']}; nothing more "
                                  f"is written — preview again", 409)
                continue                             # adopted, not rewritten
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(card["text"])
            written.append(path)
        who = setupguide._actor()
        now = taskfiles.now_iso()
        for card in plan["cards"]:
            previous = doc["tasks"].get(card["gap"])
            link = {"filename": card["filename"], "number": card["number"],
                    "title": card["title"], "at": now, "actor": who["name"],
                    "request": request, "fingerprint": card.get("fingerprint"),
                    "source": "created"}
            if isinstance(previous, dict) and \
                    previous.get("filename") != card["filename"]:
                link["replaces"] = {k: previous.get(k) for k in
                                    ("filename", "number", "title")}
            doc["tasks"][card["gap"]] = link
            (doc.get("gaps") or {}).pop(card["gap"], None)
        for card in plan["reuse"]:
            doc["tasks"][card["gap"]] = {
                "filename": card["filename"], "number": card["number"],
                "title": card["title"], "at": now, "actor": who["name"],
                "request": request, "source": "reused"}
            (doc.get("gaps") or {}).pop(card["gap"], None)
        committed = _write_and_commit(doc, [p for p in written], plan)
        result = {"ok": True, "created": [{k: c[k] for k in
                                           ("gap", "filename", "number", "title",
                                            "dependsOn")} for c in plan["cards"]],
                  "reused": plan["reuse"], "stage": "backlog",
                  "revision": setupguide.revision(), "committed": committed,
                  "started": "nothing"}
        setupguide._journal_put(request, {"kind": "cards", "area": "proposals",
                                          "status": "done", "at": now,
                                          "result": result})
        return result


def _write_and_commit(doc: dict, cards: list[Path], plan: dict) -> bool:
    """The record and the cards in one commit, through the board's own
    scoped commit — so team mode publishes them like any move."""
    path = setupguide.decisions_path()
    real = config.escapes_work(path)
    if real is not None:
        raise Refused(f"setup/decisions.json is a link to {real}, outside the "
                      f"work root; bench does not write through it", 409)
    doc["kind"], doc["v"] = setupguide.DECISIONS_KIND, setupguide.DECISIONS_VERSION
    setupguide._write_json(path, doc)
    if not config.COMMIT_MOVES:
        return False
    numbers = ", ".join(c["number"] for c in plan["cards"]) or \
        ", ".join(c["number"] for c in plan["reuse"])
    verb = "proposed" if plan["cards"] else "linked"
    paths = [str(p) for p in cards] + [str(path)]
    return taskfiles._commit(
        "setup", f"board: setup cards {numbers} {verb} by the setup guide "
                 f"({taskfiles.actor_name() or 'board'})",
        paths, "setup cards written, but committing them failed")


# ── defer one gap ──────────────────────────────────────────────────────

def decide(payload: dict) -> dict:
    """Defer one gap with a reason, or reopen it. Held to the same
    revision, request identity and reason rules as an area decision."""
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    gid = payload.get("gap")
    if gid not in GAPS:
        raise Refused(f"unknown setup gap {gid!r}; one of {', '.join(GAPS)}")
    request = payload.get("request")
    if not isinstance(request, str) or not setupguide.REQUEST_RE.match(request):
        raise Refused("a mutation names a request identity (8–80 letters, "
                      "digits or ._:-) so a repeat answers rather than acts twice")
    choice = payload.get("decision")
    if choice not in ("defer", "reopen"):
        raise Refused("a setup gap decision is defer or reopen")
    with setupguide.LOCK, taskfiles.WRITE_LOCK:
        again = setupguide._replayed(request, "gap", gid)
        if again and again.get("duplicate"):
            return again
        read = setupguide.read_decisions()
        if not again:
            setupguide._guard_revision(payload, read)
        doc = read["doc"]
        held = doc.setdefault("gaps", {})
        if not isinstance(held, dict):
            raise Refused("setup/decisions.json has a malformed gaps map", 409)
        if choice == "reopen":
            if gid not in held:
                raise Refused(f"{TITLES[gid]} is not deferred", 409)
            held.pop(gid)
        else:
            reason = setupguide._clean_reason(payload.get("reason"))
            who = setupguide._actor()
            record = dict(held.get(gid) or {})
            record.update({"status": "deferred", "reason": reason,
                           "actor": who["name"], "actorSource": who["source"],
                           "at": taskfiles.now_iso(), "request": request})
            held[gid] = record
        setupguide._journal_put(request, {"kind": "gap", "area": gid,
                                          "status": "started",
                                          "at": taskfiles.now_iso()})
        committed = setupguide._write_decisions(doc, f"gap {gid} {choice}")
        result = {"ok": True, "gap": gid, "decision": choice,
                  "revision": setupguide.revision(), "committed": committed}
        setupguide._journal_put(request, {"kind": "gap", "area": gid,
                                          "status": "done",
                                          "at": taskfiles.now_iso(),
                                          "result": result})
        return result


# ── start: a separate, deliberate action ──────────────────────────────

def start(payload: dict) -> dict:
    """Start the work on one linked setup card, through the normal
    **▸ start work** path — or refuse, with the card exactly where it was.

    Everything that can refuse is asked before anything moves: the card's
    `**Depends on:**` cards must be in done/, the agent CLI must be there,
    and the launch's own plan, profile and template preflight must pass.
    Only then is the card moved to in-progress/ (the normal commitment)
    and `agents.start_agent` called; if that still refuses, the move is
    walked back, so a refused start leaves no partial launch behind."""
    import agents
    import plans
    import templates
    if not isinstance(payload, dict):
        raise Refused("the request body must be a JSON object")
    gid = payload.get("gap")
    if gid not in GAPS:
        raise Refused(f"unknown setup gap {gid!r}; one of {', '.join(GAPS)}")
    with setupguide.LOCK:
        doc = setupguide.read_decisions()["doc"]
        link = (doc.get("tasks") or {}).get(gid)
        if not isinstance(link, dict) or not link.get("filename"):
            raise Refused(f"{TITLES[gid]} has no setup card to start — create "
                          f"it first", 409)
        filename = link["filename"]
        cards = {c["filename"]: c for c in _cards()}
        card = cards.get(filename)
        if card is None or card["stage"] not in STARTABLE:
            raise Refused(f"{filename} is "
                          + (f"in {card['stage']}/" if card else "not on the board")
                          + "; only a card in backlog/, to-do/ or in-progress/ "
                            "starts", 409)
        done = {c["number"].lstrip("0") for c in cards.values()
                if c["stage"] == "done"}
        shown = {c["number"].lstrip("0"): c["number"] for c in cards.values()}
        waiting = [shown.get(n.lstrip("0"), n) for n in card["dependsOn"]
                   if n.lstrip("0") not in done]
        if waiting:
            raise Refused(f"#{card['number']} depends on "
                          f"{', '.join('#' + n for n in waiting)}, not done yet; "
                          f"nothing was moved or launched", 409,
                          waiting=waiting)
        agent = setupguide._agent()
        if agent["status"] == "blocked":
            raise Refused(f"the agent cannot be launched here: "
                          f"{agent['next'].get('detail') or agent['next']['label']}"
                          f"; nothing was moved or launched", 409)
        source = card["stage"]
        try:
            agents._validate(filename, source, set(STARTABLE),
                             phase=agents.PHASE_RUNS_WITH)
            plans.require(plans.preflight(filename, source))
            agents._preflight_profile("work", filename, source)
            templates.require("work.md")
        except ValueError as exc:
            raise Refused(f"{exc}; nothing was moved or launched", 409)
        moved = source != "in-progress"
        if moved:
            taskfiles.move_task(filename, source, "in-progress",
                                actor="you")
        try:
            run = agents.start_agent(filename, "in-progress",
                                     bool(payload.get("takeover")))
        except ValueError as exc:
            if moved:
                taskfiles.move_task(filename, "in-progress", source,
                                    actor="board")
            raise Refused(f"{exc}; nothing was launched"
                          + (f" and the card is back in {source}/" if moved
                             else ""), 409)
        return {"ok": True, "gap": gid, "filename": filename, "moved": moved,
                "agent": run}
