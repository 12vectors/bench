#!/usr/bin/env python3
"""Prepare — never apply — the focused-verification policy change for an
operator whose application is a bench checkout (card 177, WP2).

It takes the policy draft Settings hands out (`settings.draft()`, or
`GET /api/settings` → `policy.draft`: the whole document and the
`revision` of the bytes on disk) and returns one batched draft adding,
in a single change:

- focused **scopes** on `bench-backend` and `bench-browser`, the ones in
  `verify/examples/focused-checks/profiles.focused.example.json`;
- the verifier report contract, `"report": "v1"`, on `bench-browser`;
- `--probe` on the preparation step(s) `bench-browser` runs that call
  `verify/preflight browser`.

Everything else is carried over untouched: every other check, every
command grant and `verify` list, the gates, `accepts`, context and every
key this script does not know. Nothing is overwritten silently: a scope
already declared under the same name with other arguments, a different
`report` value, or a check that is missing is a *warning*, and that part
is left as it is. No check is created, no grant is added, no gate is
touched, and no operating-agreement setting (`setupRetries`) is read or
changed — that is a separate approval in Orchestrator → Operating
agreement.

The output is a proposal. Activating it is the operator's act, through
the board's own revision-guarded steps, which re-validate everything:

    POST /api/settings/validate {"document": draft}
    POST /api/settings/preview  {"document": draft}
    POST /api/settings/apply    {"document": draft, "revision": baseRevision}

`apply` refuses (409) when the document moved after the draft was read,
so a plan made from an older reading cannot overwrite a newer save. A run
already in flight keeps the policy frozen in its manifest; the change
reaches the next launch only.

    python3 develop/operator-local/focused_policy.py < draft.json > plan.json

reads a Settings draft (`{"document": …, "revision": …}`) on stdin and
prints the plan; it reads no file, asks no board and writes nothing.
"""

from __future__ import annotations

import copy
import json
import re
import sys

BACKEND = "bench-backend"
BROWSER = "bench-browser"

# The example's scopes, by check. Explicit names only: nothing is inferred
# from changed files, and a focused scope never stands for the full run.
SCOPES = {
    BACKEND: {
        "contracts": ["--select", "tests.test_gitrepo",
                      "--select", "tests.test_records_adoption"],
        "architecture": ["--select", "tests.test_architecture",
                         "--select", "tests.test_script_policy"],
        "verify-harness": ["--select", "tests.test_verify_entrypoints",
                           "--select", "tests.test_focused_verification"],
    },
    BROWSER: {
        "board": ["--case", "case_board"],
        "settings": ["--case", "case_settings"],
        "card-detail": ["--case", "case_card_detail",
                        "--case", "case_lazy_detail"],
    },
}
REPORT = "v1"
PREFLIGHT_BROWSER = re.compile(r"(^|\s|/)verify/preflight\s+browser(\s|$)")


def _by_name(items) -> dict:
    return {i.get("name"): i for i in items or [] if isinstance(i, dict)}


def plan(draft: dict) -> dict:
    """One batched draft and what it changes, from a Settings draft."""
    if not isinstance(draft, dict) or not isinstance(draft.get("document"), dict):
        raise ValueError("expected a Settings draft: {\"document\": {...}, "
                         "\"revision\": \"…\"}")
    before = draft["document"]
    after = copy.deepcopy(before)
    checks = _by_name(after.get("checks"))
    steps = _by_name(after.get("prepare"))
    changes: list[dict] = []
    warnings: list[str] = []
    scoped: set[str] = set()          # only its declared scopes moved
    changed_checks: set[str] = set()  # its substance moved (the report)
    changed_steps: set[str] = set()

    for name, wanted in SCOPES.items():
        check = checks.get(name)
        if check is None:
            warnings.append(f"'{name}' is not declared; nothing was added for "
                            f"it (this plan creates no check)")
            continue
        current = check.get("scopes")
        if current is not None and not isinstance(current, dict):
            warnings.append(f"'{name}' has scopes that are not an object; "
                            f"left as they are")
            continue
        current = dict(current or {})
        for scope, args in wanted.items():
            if scope in current:
                if current[scope] != args:
                    warnings.append(
                        f"'{name}' already declares the scope '{scope}' with "
                        f"other arguments; kept as written, not overwritten")
                continue
            current[scope] = list(args)
            changes.append({"kind": "scope-added", "check": name,
                            "scope": scope, "args": list(args)})
            scoped.add(name)
        if current:
            check["scopes"] = current

    browser = checks.get(BROWSER)
    if browser is not None:
        report = browser.get("report")
        if report is None:
            browser["report"] = REPORT
            changes.append({"kind": "report-declared", "check": BROWSER,
                            "report": REPORT})
            changed_checks.add(BROWSER)
        elif report != REPORT:
            warnings.append(f"'{BROWSER}' declares report {report!r}; kept "
                            f"as written, not changed to {REPORT!r}")
        for step_name in browser.get("prepare") or []:
            step = steps.get(step_name)
            run = (step or {}).get("run")
            if not isinstance(run, str) or not PREFLIGHT_BROWSER.search(run):
                continue
            if "--probe" in run.split():
                continue
            step["run"] = run.rstrip() + " --probe"
            changes.append({"kind": "probe-added", "prepare": step_name,
                            "run": step["run"]})
            changed_steps.add(step_name)

    # Every check whose result identity moves: the report contract is
    # part of its substance, and a preparation step it runs is a dimension
    # of its own. Each one's recorded passes read `stale` once
    # (check-changed, prepare-changed) and cost one fresh full run. Adding
    # a scope moves neither: a full-run pass keeps standing.
    invalidated = set(changed_checks)
    for check in checks.values():
        if set(check.get("prepare") or []) & changed_steps:
            invalidated.add(check.get("name"))

    return {
        "kind": "bench-focused-policy-plan", "v": 1,
        "baseRevision": draft.get("revision") or "",
        "document": after,
        "changes": changes,
        "warnings": warnings,
        "invalidates": sorted(n for n in invalidated if n),
        "scopesOnly": sorted(n for n in scoped - invalidated if n),
        "unchanged": not changes,
        "untouched": ["gates", "profiles", "accepts", "context",
                      "every other check and preparation step",
                      "unknown top-level keys",
                      "the operating agreement (setupRetries stays as it is)"],
        "activation": (
            "not applied: POST the document to /api/settings/validate and "
            "/api/settings/preview, then /api/settings/apply with "
            "baseRevision as \"revision\". Apply refuses if the document "
            "moved since this draft was read. A run already in flight keeps "
            "the policy frozen at its launch; the change reaches the next "
            "launch."),
    }


def main() -> int:
    try:
        found = plan(json.load(sys.stdin))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"no plan: {exc}", file=sys.stderr)
        return 2
    json.dump(found, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
