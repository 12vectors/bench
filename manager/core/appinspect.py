"""What the application's files hold that bears on setup — read file by
file, running nothing (task 122, WP13).

The setup cards (`setuptasks`, task 89), verification readiness
(`readiness`, task 90) and application previews (`previews`, task 91) all
read the application the same way, and until WP13 they did so through
`setuptasks.inspect` — which put `previews` on `setuptasks` while
`setuptasks` reached *up* into `previews` for the preview configuration.
The reading itself was moved down here verbatim: it imports `config` and
nothing else, so every surface that interprets it imports it at the top
level and none of them reaches across another.

Paths are relative to the application, so every reference is portable. A
file that exists and could not be read is `unknown`, which is a different
fact from `absent`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import config

# The instruction files an application may already have. They are the
# project's own authority: bench reads them and never edits them.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md",
                     ".github/copilot-instructions.md")

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
                *INSTRUCTION_FILES, *BROWSER_FILES, "cypress",
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
                 instructions=[n for n in INSTRUCTION_FILES if has(n)])
    found["unknown"] = sorted(rel for rel, hit in found["refs"].items()
                              if hit["finding"] == "unknown")
    return found
