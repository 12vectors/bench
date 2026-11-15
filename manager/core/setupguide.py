"""The project setup guide: one proposed configuration, read from the
authorities that already answer for each part of it (task 88).

The terminal half of first-time setup is `bootstrap.py` (task 87): it lays
the layout down and writes a handoff. This is the browser half — and the
CLI half again, because `board.py --setup-guide` reads exactly this — and
it is deliberately **not a second configuration store**. Every fact below
is asked of the module that a launch, a merge or the Settings page already
asks:

- the layout and its refusals of `config` (`layout`, `problems`,
  `state_repo`, `driver_path`, the application remote, `gh`);
- the harness and model each purpose rides of `config.agent_choice`, the
  installed adapters of `config.adapters_installed`;
- the checks and merge rules of `settings.verification` and
  `gatepolicy.gate_policy`, and what they found of `verify`'s own records,
  read through `staleness.outcome_now`, which only ever disagrees in the
  safe direction.

What this module owns is the part nobody else did: six **areas**, each
with what was discovered, what bench recommends and why, what follows from
it, and one explicit next action naming who holds it — and the person's
own **decisions** about them (deferred, not applicable, a setup task
created), which are the only thing it writes.

Eight status words, and no percentage. A green bar over six rows would say
"mostly set up" about a board whose one missing piece is the one that
matters, so the summary is a count per word and nothing else.

Two stores, split by who may read them:

- **portable** — `<work root>/setup/decisions.json`: per area, the
  decision, its reason, who made it and when, the setup task it links to,
  and a fingerprint of the *portable* facts it was made against (words and
  names, never paths), so a teammate's board reads the same decision and
  the same staleness. Committed through the board's own write path under
  `BOARD_COMMIT_MOVES`, like every other work-state write.
- **machine-local** — `manager/local/state/setup/operations.json`: the
  operation journal that makes a repeated request answer with the result
  it already had and an interrupted one resumable. Never shared.

Reading writes nothing, runs no project program, launches no agent and
repairs nothing. Each mutation is one of two kinds (a decision, a setup
task), carries the revision it was made against and a request identity,
and is refused as a conflict when the revision moved. Configuring a check,
running one and enabling a gate stay where they already are — Settings →
Checks, a card's ◫ evidence sheet, Settings → Merge rules — with their own
protections; the guide links there rather than growing a second editor.

Where it sits (task 122, WP13): the guide *composes* the surfaces built on
the setup record — verification readiness (`readiness`), application
previews (`previews`) and the proposed setup cards (`setuptasks`) — so it
sits above all three and imports them at the top level. What those
surfaces share with it — the decisions record, the journal, the request
and revision guards and the agent area — is `setupbase`, below them; the
read-only inspection of the application is `appinspect`. Nothing here is
reached into by a lower module, and nothing here reaches up.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from pathlib import Path

import appinspect
import config
import gatepolicy
import githost
import previews
import profiles
import readiness
import settings
import setupbase
import setuptasks
import taskfiles

KIND = "bench-setup-state"
VERSION = 1

# The one refusal every setup surface raises, owned by `setupbase`.
Refused = setupbase.Refused

# ── the vocabulary ─────────────────────────────────────────────────────

STATUSES = {
    "not-inspected": ("nothing has looked at this yet, or the reading could "
                      "not be taken. It is not a pass and not a fault"),
    "decision-needed": ("bench found what is here and cannot choose for you: "
                        "the next action names the decision"),
    "configured": ("set up, and nothing on this board has exercised it yet. "
                   "Configured is not tested"),
    "tested": ("bench itself ran it and recorded a pass that still holds for "
               "what it was about"),
    "failed": "it ran and said no — the record says what it found",
    "blocked": ("it cannot go further until something outside bench changes: "
                "a missing tool, a login, or a layout bench refuses"),
    "deferred": ("someone chose to come back to this later, with a reason. "
                 "It stays revisitable"),
    "not-applicable": ("someone recorded that this project does not need it, "
                       "with a reason. It stays revisitable"),
}

# Who holds the next action. A login or an approval is a person's; writing
# instructions, a driver or a test program is engineering an agent can do,
# through a card; a value bench already edits is Settings'.
OWNERS = {
    "you": "a person: a login, an approval, an install or a decision only "
           "the project's owner can make",
    "agent": "engineering work a coding agent can do through an ordinary "
             "card on this board",
    "bench": "a setting Bench already edits safely, in Settings",
}

AREAS = setupbase.AREAS
TITLES = {
    "workspace": "Workspace and task storage",
    "agent": "Coding agent",
    "instructions": "Project instructions",
    "verification": "Verification",
    "preview": "Application preview",
    "delivery": "Review and delivery",
}
# Which areas a person may mark not applicable. A board always has a
# workspace and an agent; everything else is a project's own call.
NOT_APPLICABLE_OK = {"instructions", "verification", "preview", "delivery"}
# Which areas a setup card can be proposed for, and what it asks for.
TASKABLE = {"instructions", "verification", "preview"}


# ── where things live ──────────────────────────────────────────────────
#
# The decisions record, the journal and their paths are `setupbase`'s.

def handoff_path() -> Path:
    return config.STATE / "bootstrap" / "handoff.json"


# ── the facts, from the authorities that own them ─────────────────────

_fact = setupbase.fact
_next = setupbase.next_action


def _workspace() -> dict:
    lay = config.layout()
    try:
        trouble = config.problems()
    except Exception as exc:  # a reading that failed is not a pass
        trouble = [f"the layout could not be validated: {exc}"]
    versioned = bool(lay.get("stateRepo"))
    mode = "external" if lay["external"] else "vendored"
    facts = [
        _fact("Layout", mode),
        _fact("Application", lay["app"], local=True),
        _fact("Work state (tickets)", lay["work"], local=True),
        _fact("Tickets versioned", "yes — in a git repository" if versioned
              else "no — a plain folder", None if versioned
              else "decision-needed"),
        _fact("Moves commit themselves", "on" if config.COMMIT_MOVES
              else "off"),
        _fact("Boards sync through a remote", "on" if config.SYNC else "off"),
    ]
    facts += [_fact("Layout refusal", p, "blocked") for p in trouble]
    basis = {"mode": mode, "versioned": versioned,
             "commit": config.COMMIT_MOVES, "sync": config.SYNC,
             "problems": len(trouble)}
    if trouble:
        status = "blocked"
        nxt = _next("you", "Fix the layout bench refuses, then re-run the "
                    "installer", "terminal",
                    command=f"python3 {config.TM_ROOT / 'install.py'}")
        rec = ("Fix the refusal first: every fallback from a broken layout "
               "leads to the wrong repository, so bench serves none.")
        consequence = "The board refuses to serve this layout."
    elif not versioned:
        status = "decision-needed"
        nxt = _next("you", "Version the tickets with git init, or defer it",
                    "terminal", command=f"git init {lay['work']}")
        rec = ("Version the tickets when more than one person or machine "
               "will use this board. A plain folder is a working local "
               "board.")
        consequence = ("Cards move and agents run, but nothing the board "
                       "writes to a ticket is committed or shareable.")
    else:
        status = "configured"
        nxt = _next("bench", "Review where each setting comes from",
                    "settings", section="installation")
        rec = ("Keep the layout as installed. Team mode (commit and sync) "
               "is a separate choice made in manager/local/.env.")
        consequence = ("Tickets are versioned in their own repository"
                       if lay["external"] else
                       "Tickets live inside the application's repository")
    return {"status": status, "facts": facts, "basis": basis,
            "recommendation": rec, "consequence": consequence, "next": nxt}


def _instructions() -> dict:
    app = config.app_repo()
    found, unreadable = [], []
    for name in appinspect.INSTRUCTION_FILES:
        try:
            (app / name).read_text(encoding="utf-8")
            found.append(name)
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError):
            unreadable.append(name)
    if unreadable:
        names = ", ".join(unreadable)
        return {"status": "blocked",
                "facts": [_fact("Unreadable project instructions", names, "blocked")],
                "basis": {"found": found, "unreadable": unreadable},
                "recommendation": (f"Inspect and repair {names}: check permissions "
                                   "and UTF-8 encoding. Bench preserves the files."),
                "consequence": "Unreadable instructions are unknown, not absent.",
                "next": _next("you", "Inspect and repair project instructions",
                              "manual")}
    outside = [str(p) for p in config.guidance_outside_app()]
    facts = [_fact("Instructions in the application",
                   ", ".join(found) if found else "none found",
                   None if found else "decision-needed")]
    if outside:
        facts.append(_fact("Workflow briefs outside the checkout",
                           ", ".join(outside), local=True))
    basis = {"found": found}
    if found:
        return {"status": "configured", "facts": facts, "basis": basis,
                "recommendation": (f"Keep {found[0]} as the authority. Bench "
                                   f"points agents at it and never edits it."),
                "consequence": "Every agent run is told to read it first.",
                "next": _next("bench", "See what agents are given to read",
                              "settings", section="access")}
    return {"status": "decision-needed", "facts": facts, "basis": basis,
            "recommendation": ("Propose a setup card for writing instructions "
                               "that describe this project's real "
                               "conventions. Bench adds no boilerplate to the "
                               "application."),
            "consequence": ("Agents work from the card alone and guess at the "
                            "project's conventions."),
            "next": _next("agent", "Create a setup card for project "
                          "instructions", "task")}


def _verification() -> dict:
    doc = profiles.document()
    verif = settings.verification(doc)
    policy = gatepolicy.gate_policy(doc)
    names = {c["name"] for c in verif["checks"]}
    outcomes = setupbase.newest_outcomes(names) if names else {}
    gated = sorted(s for s, e in policy["scopes"].items() if e["gated"])
    facts = [
        _fact("Registered checks", ", ".join(sorted(names)) or "none"),
        _fact("Merge rules", ", ".join(gated) + " gated" if gated else
              "no merge requirements"),
        _fact("Agent commands (legacy prefixes)",
              ", ".join(config.agent_commands()) or "none"),
    ]
    for name in sorted(names):
        word = outcomes.get(name, "not run")
        facts.append(_fact(f"Latest run of {name}", word,
                           "tested" if word == "passed" else
                           "failed" if word in ("failed", "prepare-failed")
                           else None))
    # Readiness (task 90): a check is *ready* only once a person reviewed
    # its program, a current pass and a controlled failure. Shown beside
    # the run's outcome and never folded into it.
    try:
        boot = readiness.bootstrap(doc=doc)
    except Exception as exc:      # a reading that failed is not a pass
        boot = {"condition": "incomplete", "ready": [],
                "headline": f"readiness could not be read: {exc}"}
    facts.append(_fact("Verification readiness", boot["headline"],
                       None if boot["ready"] else "decision-needed"))
    basis = {"state": verif["state"], "checks": sorted(names),
             "gated": gated, "outcomes": outcomes, "ready": boot["ready"]}
    refused = [p["why"] for p in verif.get("problems") or []]
    facts += [_fact("Policy refusal", why, "blocked") for why in refused]
    basis["refused"] = len(refused)
    if verif["state"] == "invalid" or refused or doc.get("problem"):
        status = "blocked"
        nxt = _next("bench", "Fix the refused policy", "settings",
                    section="verification")
        rec = "Fix the policy refusal; until then no check can be asked for."
    elif not names:
        status = "decision-needed"
        nxt = _next("agent", "Create a setup card for verification programs",
                    "task", section="verification")
        rec = ("Register the commands your team already uses to test and "
               "build as named checks. If there are none, a setup card asks "
               "for them; bench does not invent tests.")
    elif any(o in ("failed", "prepare-failed") for o in outcomes.values()):
        status = "failed"
        nxt = _next("you", "Read the failing run in the card's ◫ evidence "
                    "sheet", "settings", section="verification")
        rec = "Fix what the check found, or the check, then run it again."
    elif any(o == "passed" for o in outcomes.values()):
        status = "tested"
        if boot["ready"]:
            nxt = _next("bench", "Decide which checks a merge requires",
                        "settings", section="acceptance")
            rec = ("Require the checks that matter in a merge rule — a "
                   "separate decision from registering and reviewing them.")
        else:
            nxt = _next("you", "Review a check's program and results",
                        "settings", section="setup")
            rec = ("A pass is not coverage. Review the program, a current pass "
                   "and a controlled failure before any merge rule requires "
                   "it.")
    else:
        status = "configured"
        nxt = _next("you", "Run a check from a card's ◫ evidence sheet",
                    "settings", section="verification")
        rec = ("Exercise each check once, including a deliberate failure, "
               "before any merge rule requires it.")
    return {"status": status, "facts": facts, "basis": basis,
            "recommendation": rec,
            "consequence": ("Nothing is required before a merge."
                            if not gated else
                            "Gated merges wait for passing evidence."),
            "next": nxt}


def _preview() -> dict:
    driver = config.driver_path()
    runnable = bool(driver and os.access(driver, os.X_OK))
    p = previews.summary()
    facts = [_fact("Driver (manager/local/driver/start)",
                   "present and executable" if runnable else
                   "present but not executable" if driver else "none",
                   None if runnable or p["present"] else
                   "blocked" if driver else "decision-needed"),
             _fact("Preview configuration (manager/local/preview.json)",
                   "valid" if p["valid"] else "refused" if p["present"]
                   else "none",
                   None if p["valid"] else "blocked" if p["present"] else None),
             _fact("Discovered start commands",
                   str(p["starts"]) if p["starts"] else
                   "none — nothing here starts a server"),
             _fact("Last preview", p["lastStatus"] or "never started",
                   "tested" if p["tested"] else
                   "failed" if p["failed"] else None)]
    if p["needsData"] and not p["fixture"]:
        facts.append(_fact("Disposable data", "the project has service or "
                           "data configuration and the preview names no "
                           "fixture", "decision-needed"))
    basis = {"driver": "runnable" if runnable else
             "not-executable" if driver else "none",
             "preview": "valid" if p["valid"] else
             "invalid" if p["present"] else "none",
             "kind": p["kind"], "tested": p["tested"], "failed": p["failed"],
             "fixture": bool(p["fixture"]), "needsData": p["needsData"]}
    if p["present"] and not p["valid"]:
        return {"status": "blocked", "facts": facts, "basis": basis,
                "recommendation": ("Fix the refused preview configuration: "
                                   + ", ".join(p["problems"][:4]) + "."),
                "consequence": "Nothing can be prepared or started until then.",
                "next": _next("you", "Fix the preview configuration",
                              "settings", section="setup")}
    if p["present"]:
        if p["failed"]:
            return {"status": "failed", "facts": facts, "basis": basis,
                    "recommendation": ("Read the preview's log and its reason "
                                       f"({p['lastReason'] or p['lastStatus']}),"
                                       " fix the app or the configuration, "
                                       "and start it again."),
                    "consequence": "The last preview did not stay ready.",
                    "next": _next("you", "Read the preview below",
                                  "settings", section="setup")}
        if p["tested"]:
            return {"status": "tested", "facts": facts, "basis": basis,
                    "recommendation": ("A preview reached ready and stopped "
                                       "leaving no process of its own. A UI "
                                       "smoke test is a separate check."),
                    "consequence": ("A card's worktree can be previewed, "
                                    "bound to its source."),
                    "next": _next("you", "Preview a card from the guide",
                                  "settings", section="setup")}
        return {"status": "configured", "facts": facts, "basis": basis,
                "recommendation": ("Prepare, start, reach and stop it once for "
                                   "a card before relying on it."),
                "consequence": "Configured is not tested.",
                "next": _next("you", "Start a preview of a card below",
                              "settings", section="setup")}
    if runnable:
        return {"status": "configured", "facts": facts, "basis": basis,
                "recommendation": "Use the ⛭ drive chip on a review card.",
                "consequence": ("A review card can launch the app from its "
                                "own worktree."),
                "next": _next("you", "Drive a review card to try it",
                              "board")}
    if driver:
        return {"status": "blocked", "facts": facts, "basis": basis,
                "recommendation": "Make the driver executable (chmod +x).",
                "consequence": "The drive chip refuses until it is.",
                "next": _next("you", "chmod +x the driver", "terminal",
                              command=f"chmod +x {driver}")}
    if p["kind"] == "non-web":
        return {"status": "decision-needed", "facts": facts, "basis": basis,
                "recommendation": p["alternative"],
                "consequence": "There is nothing to preview in a browser.",
                "next": _next("you", "Mark not applicable and link a smoke "
                              "check or card", "settings", section="setup")}
    return {"status": "decision-needed", "facts": facts, "basis": basis,
            "recommendation": ("Configure a preview from the proposal below "
                               "— a detected start script is not a working "
                               "preview until it has been prepared, started, "
                               "reached and stopped. If startup or data is "
                               "not reproducible, propose a setup card."),
            "consequence": "Review cards cannot launch the app to try it.",
            "next": _next("agent", "Create a setup card for a reproducible "
                          "preview", "task")}


def _delivery() -> dict:
    remote = config.app_remote()
    gh = githost.available()
    doc = profiles.document()
    policy = gatepolicy.gate_policy(doc)
    reviews = sorted({e["review"] for e in policy["scopes"].values()})
    facts = [_fact("Application remote", remote or "none",
                   None if remote else "decision-needed"),
             _fact("GitHub CLI (gh)", "on PATH" if gh else "not on PATH",
                   None if gh else "blocked"),
             _fact("Merges are made", "on origin" if
                   config.app_merge_on_origin() else "locally"),
             _fact("Review required by merge rules",
                   ", ".join(r for r in reviews if r != "none") or "none")]
    basis = {"remote": bool(remote), "gh": gh, "reviews": reviews,
             "origin": config.app_merge_on_origin()}
    if not remote:
        return {"status": "decision-needed", "facts": facts, "basis": basis,
                "recommendation": ("Add a remote to the application when "
                                   "work should become pull requests; a "
                                   "local-only board is honest about it."),
                "consequence": "Cards complete locally; no PR is opened.",
                "next": _next("you", "Add an application remote, or mark "
                              "delivery not applicable", "terminal",
                              command=f"git -C {config.app_repo()} remote "
                              f"add origin <url>")}
    if not gh:
        return {"status": "blocked", "facts": facts, "basis": basis,
                "recommendation": "Install gh and sign in with gh auth login.",
                "consequence": ("Plans read local-only: no PR is opened or "
                                "reviewed."),
                "next": _next("you", "Install gh and run gh auth login in "
                              "your terminal", "external")}
    return {"status": "configured", "facts": facts, "basis": basis,
            "recommendation": ("Choose a review policy for merges in Merge "
                               "rules if a review should be required."),
            "consequence": "A card entering review/ opens a pull request.",
            "next": _next("bench", "Review the merge rules", "settings",
                          section="acceptance")}


READERS = {"workspace": _workspace, "agent": setupbase.agent,
           "instructions": _instructions, "verification": _verification,
           "preview": _preview, "delivery": _delivery}

# Which handoff guide items belong to which area, so the terminal's
# unresolved items are shown where they will be settled.
HANDOFF_AREA = {
    "agent-cli-missing": "agent", "adapter-external-wiring": "agent",
    "agent-smoke": "agent", "agent-commands": "verification",
    "declared-scripts": "verification", "work-unversioned": "workspace",
    "remote-not-created": "workspace", "remote-none": "workspace",
    "first-commit-failed": "workspace", "push-failed": "workspace",
    "sync-without-remote": "workspace", "remote-access": "workspace",
    "app-no-remote": "delivery", "gh-missing": "delivery",
    "app-dirty": "workspace",
}


def handoff() -> dict:
    """What the terminal setup left for the browser, read as the history it
    is: the areas below are read now, and the handoff says what was true
    when it was written."""
    raw = setupbase.raw_text(handoff_path())
    if raw is None:
        return {"present": False}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return {"present": True, "problem": "the bootstrap handoff does not "
                                            "parse; it is ignored"}
    if not isinstance(doc, dict) or doc.get("kind") != "bench-bootstrap-handoff":
        return {"present": True, "problem": "not a bootstrap handoff"}
    ops = [o for o in doc.get("operations") or [] if isinstance(o, dict)]
    return {"present": True, "v": doc.get("v"), "status": doc.get("status"),
            "at": doc.get("at"), "bench": doc.get("bench"),
            "mode": doc.get("mode"),
            "guide": [{"id": g.get("id"), "title": g.get("title"),
                       "how": settings.scrub(str(g.get("how") or "")),
                       "area": HANDOFF_AREA.get(g.get("id"))}
                      for g in doc.get("guide") or [] if isinstance(g, dict)],
            "failed": [{"id": o.get("id"), "summary": o.get("summary"),
                        "outcome": o.get("outcome")}
                       for o in ops if o.get("outcome") == "failed"],
            "historical": ("Written by bootstrap.py when it ran. The areas "
                           "are read now and win where the two differ.")}


# ── the journal (machine-local, `setupbase`'s) ─────────────────────────

def interrupted() -> list[dict]:
    return [{"request": key, "kind": e.get("kind"), "area": e.get("area"),
             "at": e.get("at"),
             "recovery": ("Send the same request again (Resume): it finishes "
                          "what was started and creates nothing twice.")}
            for key, e in sorted(setupbase.journal().items())
            if e.get("status") == "started"]


# ── the reading ────────────────────────────────────────────────────────

def _area(area: str, decisions: dict, tasks: dict) -> dict:
    try:
        found = READERS[area]()
    except Exception as exc:
        found = {"status": "not-inspected", "facts": [],
                 "basis": {"error": type(exc).__name__},
                 "recommendation": "", "consequence": "",
                 "problem": f"this area could not be read: {exc}",
                 "next": _next("you", "Re-read after fixing the problem",
                               "board")}
    fingerprint = setupbase.digest(found.pop("basis"))
    decision = decisions.get(area)
    if decision is not None and not isinstance(decision, dict):
        decision = None
    task = tasks.get(area) if isinstance(tasks.get(area), dict) else None
    if task:
        number = str(task.get("number") or "")
        stage = None
        filename = task.get("filename") or ""
        if filename:
            stage = taskfiles.find_stage_of(filename)
            if stage is None and (config.TASKS / "archive" / filename).is_file():
                stage = "archive"
        task = {"number": number, "filename": filename, "stage": stage,
                "title": task.get("title"), "at": task.get("at")}
    status = found["status"]
    shown = status
    if decision and decision.get("status") in ("deferred", "not-applicable"):
        shown = decision["status"]
    allowed = []
    if shown in ("decision-needed", "blocked", "failed", "not-inspected"):
        allowed.append("defer")
        if area in NOT_APPLICABLE_OK:
            allowed.append("not-applicable")
    if decision:
        allowed.append("reopen")
    if area in TASKABLE and status == "decision-needed" and not task:
        allowed.append("create-task")
    out = {**found, "id": area, "title": TITLES[area], "status": shown,
           "computed": status, "fingerprint": fingerprint,
           "decision": (dict({k: decision.get(k) for k in
                              ("status", "reason", "actor", "actorSource",
                               "at", "fingerprint", "alternative")},
                             stale=decision.get("fingerprint") != fingerprint)
                        if decision else None),
           "task": task, "allowed": allowed,
           "canBeNotApplicable": area in NOT_APPLICABLE_OK}
    return out


def state() -> dict:
    """The whole guide, in one reading. Writes nothing and runs nothing:
    it resolves the policy, reads records on disk, asks `git` and `PATH`
    the questions the authorities already ask, and reads two JSON files."""
    read = setupbase.read_decisions()
    doc = read["doc"]
    areas = [_area(a, doc.get("decisions") or {}, doc.get("tasks") or {})
             for a in AREAS]
    counts = {word: 0 for word in STATUSES}
    for a in areas:
        counts[a["status"]] += 1
    open_ = [a["id"] for a in areas
             if a["status"] in ("decision-needed", "blocked", "failed")]
    stale = [a["id"] for a in areas if a["decision"] and a["decision"]["stale"]]
    return {
        "kind": KIND, "v": VERSION, "generated": time.time(),
        "revision": setupbase.revision(),
        "readOnly": read["problem"],
        "statuses": dict(STATUSES), "owners": dict(OWNERS),
        "areas": areas,
        "summary": {"counts": counts, "open": open_, "stale": stale,
                    "invite": bool(open_),
                    "noPercentage": ("Counted per status word, never as a "
                                     "percentage: configured is not tested, "
                                     "and a deferred item is not done.")},
        "handoff": handoff(),
        "interrupted": interrupted(),
        "proposals": _proposals(doc),
        "verificationReadiness": _readiness(),
        "applicationPreview": _application_preview(),
        "storage": {
            "portable": ("setup/decisions.json in the work state — "
                         "decisions, reasons, who and when, and setup-card "
                         "links; committed like any board edit when "
                         "BOARD_COMMIT_MOVES is on"),
            "local": ("manager/local/state/setup/ — the operation journal; "
                      "never committed"),
            "committed": config.COMMIT_MOVES,
        },
        "endpoints": {"read": "GET /api/setup",
                      "decide": "POST /api/setup/decide",
                      "task": "POST /api/setup/task",
                      "preview": "POST /api/setup/cards/preview",
                      "create": "POST /api/setup/cards/create",
                      "gap": "POST /api/setup/cards/decide",
                      "start": "POST /api/setup/cards/start"},
    }


def _readiness() -> dict:
    """Verification readiness (task 90), read in the same pass. Its own
    module owns it; a reading that fails says so rather than vanishing."""
    try:
        return readiness.state()
    except Exception as exc:
        return {"kind": readiness.KIND, "v": readiness.VERSION,
                "problem": f"verification readiness could not be read: {exc}"}


def _application_preview() -> dict:
    """The preview lifecycle (task 91), read in the same pass. Its own
    module owns it; a reading that fails says so rather than vanishing."""
    try:
        return previews.state()
    except Exception as exc:
        return {"kind": previews.KIND, "v": previews.VERSION,
                "problem": f"the application preview could not be read: {exc}"}


def _proposals(doc: dict) -> dict:
    """The gap-specific setup cards (task 89), read in the same pass and
    under the same rule: nothing is written, nothing is run."""
    try:
        return setuptasks.proposals(doc)
    except Exception as exc:  # a reading that failed is not "no gaps"
        return {"gaps": [], "covered": [], "steps": [], "order": [],
                "unreadable": f"the setup cards could not be proposed: {exc}"}


# ── the two mutations ──────────────────────────────────────────────────
#
# Both are held to `setupbase`'s request identity, journal, revision and
# integrity guards, as every setup mutation is.

def _alternative(area: str, choice: str, value) -> dict | None:
    """What verifies a project that has no preview instead (task 91): a
    registered named check, or a card on this board. Only for marking the
    preview not applicable; validated, never invented."""
    if value in (None, "", {}):
        return None
    if area != "preview" or choice != "not-applicable":
        raise Refused("an alternative is linked only when the application "
                      "preview is marked not applicable")
    if not isinstance(value, dict) or len(value) != 1:
        raise Refused('an alternative is {"check": <name>} or '
                      '{"task": <number>}')
    if "check" in value:
        name = value["check"]
        names = {c["name"] for c in
                 settings.verification(profiles.document())["checks"]}
        if name not in names:
            raise Refused(f"no named check {name!r} is registered; register "
                          f"the smoke check in Settings → Checks first, or "
                          f"link a card", 409)
        return {"check": name}
    if "task" in value:
        number = str(value["task"]).lstrip("#").strip()
        if not number.isdigit():
            raise Refused("a card is named by its number")
        for stage in [*config.STAGE_DIRS, "archive"]:
            try:
                names = os.listdir(config.TASKS / stage)
            except OSError:
                continue
            for name in names:
                m = taskfiles.NUMBER_RE.match(name)
                if m and int(m.group(1)) == int(number):
                    return {"task": f"{int(number):02d}", "filename": name}
        raise Refused(f"no card #{number} is on this board", 409)
    raise Refused('an alternative is {"check": <name>} or {"task": <number>}')


def decide(payload: dict) -> dict:
    """Record, or withdraw, a person's decision about one area: `defer` or
    `not-applicable` with a reason, or `reopen` to make it revisitable."""
    area, request = setupbase.check_request(payload)
    choice = payload.get("decision")
    if choice not in ("defer", "not-applicable", "reopen"):
        raise Refused("decision is one of defer, not-applicable, reopen")
    with setupbase.LOCK, taskfiles.WRITE_LOCK:
        again = setupbase.replayed(request, "decide", area)
        if again and again.get("duplicate"):
            return again
        read = setupbase.read_decisions()
        setupbase.guard_integrity(read)
        if not again:          # a resumed request was checked when it began
            setupbase.guard_revision(payload, read)
        doc = read["doc"]
        current = _area(area, doc["decisions"], doc["tasks"])
        if choice == "not-applicable" and area not in NOT_APPLICABLE_OK:
            raise Refused(f"{TITLES[area]} cannot be marked not applicable: "
                          f"every board has one")
        if choice == "reopen" and not current["decision"]:
            raise Refused(f"{TITLES[area]} has no decision to reopen", 409)
        if choice != "reopen" and choice not in current["allowed"] and \
                not current["decision"]:
            raise Refused(f"{TITLES[area]} reads {current['status']}; there "
                          f"is nothing to {choice}", 409)
        reason = setupbase.clean_reason(payload.get("reason")) \
            if choice != "reopen" else None
        alternative = _alternative(area, choice, payload.get("alternative"))
        setupbase.journal_put(request,{"kind": "decide", "area": area,
                               "status": "started", "at": taskfiles.now_iso()})
        if choice == "reopen":
            doc["decisions"].pop(area, None)
        else:
            who = setupbase.actor()
            # unknown fields of an earlier record are carried over
            record = dict(doc["decisions"].get(area) or {})
            record.update({"status": ("deferred" if choice == "defer"
                                      else "not-applicable"),
                           "reason": reason, "actor": who["name"],
                           "actorSource": who["source"],
                           "at": taskfiles.now_iso(),
                           "fingerprint": current["fingerprint"],
                           "request": request})
            if alternative:
                record["alternative"] = alternative
            else:
                record.pop("alternative", None)
            doc["decisions"][area] = record
        committed = setupbase.write_decisions(doc, f"{area} {choice}")
        result = {"ok": True, "area": area, "decision": choice,
                  "revision": setupbase.revision(), "committed": committed}
        setupbase.journal_put(request,{"kind": "decide", "area": area,
                               "status": "done", "at": taskfiles.now_iso(),
                               "result": result})
        return result


TASK_BODIES = {
    "instructions": (
        "Write project instructions for coding agents",
        "No AGENTS.md or CLAUDE.md was found at the application's root, so "
        "agents launched from this board work from each card alone.",
        "- An instructions file at the application root that describes this "
        "project's real conventions: how to build, test and run it, its "
        "layout, and what a change must include to be done.\n"
        "- Written from the codebase and the team's practice, not from a "
        "generic template.",
        "- [ ] The file exists at the application root and the team has "
        "reviewed it\n"
        "- [ ] It names the commands that build and test the project"),
    "verification": (
        "Provide verification programs bench can run as named checks",
        "No named check is registered, so bench cannot verify the work "
        "itself and no merge rule can require evidence.",
        "- Non-interactive programs that test and build the project and "
        "exit non-zero on failure.\n"
        "- Register them in Settings → Checks once they exist.",
        "- [ ] Each program fails on a deliberately broken fixture\n"
        "- [ ] Each is registered as a named check"),
    "preview": (
        "Make the application start reproducibly for a preview",
        "No preview is configured and no driver exists, so a review card "
        "cannot be previewed from its own worktree.",
        "- One command that starts the app from a worktree on the port and "
        "host it is given ({port}, {host} — always 127.0.0.1), and a page "
        "or endpoint that answers only when it is really up.\n"
        "- Preparation steps for dependencies and disposable data, never a "
        "shared or production service.",
        "- [ ] The guide's preview prepares, starts, reaches ready and "
        "stops with no process left behind\n"
        "- [ ] Startup needs no secret value written into a card or the "
        "configuration"),
}


def create_task(payload: dict) -> dict:
    """Propose a setup card in backlog/ for an area an agent can do, and
    link it from the area. The card is an ordinary card: nothing starts."""
    area, request = setupbase.check_request(payload)
    if area not in TASKABLE:
        raise Refused(f"{TITLES[area]} has no setup card to propose: its next "
                      f"action is yours or Settings'")
    with setupbase.LOCK, taskfiles.WRITE_LOCK:
        again = setupbase.replayed(request, "task", area)
        if again and again.get("duplicate"):
            return again
        read = setupbase.read_decisions()
        setupbase.guard_integrity(read)
        doc = read["doc"]
        planned = again.get("filename") if again else None
        if not again:
            setupbase.guard_revision(payload, read)
            current = _area(area, doc["decisions"], doc["tasks"])
            if "create-task" not in current["allowed"]:
                raise Refused(
                    f"{TITLES[area]} reads {current['computed']}"
                    + (" and already links a setup card"
                       if current["task"] else "")
                    + "; no setup card is proposed for it", 409)
        title, context, build, accept = TASK_BODIES[area]
        backlog = config.TASKS / "backlog"
        if planned and (backlog / planned).is_file():
            filename = planned            # an interrupted run got this far
        else:
            number = setupbase.next_number()
            filename = f"{number:02d}-{setupbase.slug(title)}.md"
            setupbase.journal_put(request,{"kind": "task", "area": area,
                                   "status": "started", "filename": filename,
                                   "at": taskfiles.now_iso()})
            number_text = filename.split("-", 1)[0]
            text = (f"# {number_text} — {title}\n\n**Status:** Backlog\n"
                    f"**Priority:** Medium — proposed by the setup guide\n"
                    f"**Type:** Chore\n**Setup gap:** {area}\n\n"
                    f"{context} Proposed from the setup "
                    f"guide's {TITLES[area]} area; edit or archive it freely.\n"
                    f"\n## What to build\n\n{build}\n\n## Acceptance\n\n"
                    f"{accept}\n")
            path = backlog / filename
            taskfiles.refuse_escape(path)
            backlog.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            taskfiles.commit_edit(filename, "backlog",
                                  "proposed by the setup guide")
        who = setupbase.actor()
        link = dict(doc["tasks"].get(area) or {})
        link.update({"filename": filename,
                     "number": filename.split("-", 1)[0],
                     "title": title, "at": taskfiles.now_iso(),
                     "actor": who["name"], "request": request})
        doc["tasks"][area] = link
        committed = setupbase.write_decisions(doc, f"{area} task {link['number']}")
        result = {"ok": True, "area": area, "filename": filename,
                  "stage": "backlog", "revision": setupbase.revision(),
                  "committed": committed}
        setupbase.journal_put(request,{"kind": "task", "area": area, "status": "done",
                               "filename": filename,
                               "at": taskfiles.now_iso(), "result": result})
        return result


# ── the terminal ───────────────────────────────────────────────────────

def cli(argv: list[str]) -> int:
    """`board.py --setup-guide` — the CLI client of the same contract.

    With no action it reads (and writes nothing). `--decide` and
    `--create-task` call exactly the functions the HTTP routes call, so a
    terminal and a browser are held to the same revision, request identity
    and refusals."""
    p =argparse.ArgumentParser(prog="board.py --setup-guide")
    p.add_argument("--json", action="store_true", help="print the state as JSON")
    p.add_argument("--decide", nargs=2, metavar=("AREA", "DECISION"),
                   help="defer, not-applicable or reopen an area")
    p.add_argument("--reason", help="why (required for defer/not-applicable)")
    p.add_argument("--create-task", metavar="AREA",
                   help="propose a setup card in backlog/ for an area")
    p.add_argument("--expected", help="the revision you read (default: the "
                   "current one — read first when another client may write)")
    p.add_argument("--request", help="request identity; repeat it to resume")
    p.add_argument("--verification", action="store_true",
                   help="read verification readiness: candidate checks "
                        "(inferred, never run), each check's readiness and "
                        "the bootstrap condition. Writes nothing")
    args = p.parse_args(argv)
    if args.verification:
        found = readiness.state()
        print(json.dumps(found, indent=2) if args.json
              else readiness.text(found))
        return 0
    if args.decide or args.create_task:
        payload = {"expected": args.expected or setupbase.revision(),
                   "request": args.request or f"cli-{secrets.token_hex(8)}"}
        try:
            if args.decide:
                payload.update(area=args.decide[0], decision=args.decide[1],
                               reason=args.reason)
                result = decide(payload)
            else:
                payload.update(area=args.create_task)
                result = create_task(payload)
        except Refused as exc:
            print(json.dumps({"error": str(exc), **exc.extra}) if args.json
                  else f"refused: {exc}")
            return 1
        print(json.dumps(result, indent=2) if args.json else
              f"done: {json.dumps(result)}")
        return 0
    found = state()
    print(json.dumps(found, indent=2) if args.json else text(found))
    return 0

def text(found: dict) -> str:
    lines = ["Bench setup guide — read now; nothing was run or written.", ""]
    for a in found["areas"]:
        lines.append(f"{a['title']}: {a['status']}"
                     + (" (decision stale: the facts changed)"
                        if a["decision"] and a["decision"]["stale"] else ""))
        for f in a["facts"]:
            lines.append(f"  - {f['label']}: {f['value']}")
        if a.get("recommendation"):
            lines.append(f"  recommended: {a['recommendation']}")
        n = a["next"]
        lines.append(f"  next ({n['owner']}): {n['label']}"
                     + (f" — {n['command']}" if n.get("command") else ""))
        if a["task"]:
            lines.append(f"  setup card: #{a['task']['number']} "
                         f"({a['task']['stage'] or 'missing'})")
        lines.append("")
    counts = ", ".join(f"{n} {w}" for w, n in found["summary"]["counts"].items()
                       if n)
    lines.append(f"Summary: {counts}. Revision {found['revision']}.")
    for op in found["interrupted"]:
        lines.append(f"Interrupted: {op['kind']} on {op['area']} "
                     f"(request {op['request']}). {op['recovery']}")
    return "\n".join(lines)
