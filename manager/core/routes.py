"""Every HTTP route this board answers, its request class, and its handler.

`httpd.py` owns the socket: it finds a request's route in `ROUTES`, applies
the protection its *class* names, reads and checks the body, and only then
calls the handler. A handler never decides whether its own request is
trusted — that is what the class is for, and it is checked in one place for
every route, so a route cannot be added that skips it by forgetting a call.

A handler takes one argument, the request (`httpd.Handler`), and uses only
its public surface: `req.url`, `req.path`, `req.query`, `req.headers`,
`req.body` (a POST's JSON object, already checked), `req.reply(code,
payload)`, `req.send(code, bytes, content_type)`, `req.stream()` and
`req.extra_file(path)`.

The table at the bottom is the inventory. `tests/test_architecture.py`
reads it with `ast`, so every entry is a literal `Route(...)` call.
"""

from __future__ import annotations

import re
import subprocess
import time
from html import escape
from typing import Callable, NamedTuple

import acceptance
import agreements
import archiveall
import ask
import agents
import artifacts
import assets
import agentsettings
import adoption
import brain
import braindraft
import brainreconcile
import brainreviews
import brainwrite
import handbook
import commands
import completion
import config
import coordinate
import cycletime
import decisions
import decisiontrace
import delivery
import doctor
import drive
import events
import execowner
import evidence
import gatepolicy
import gates
import changes
import inputs
import operations
import orchestrator
import phases
import phasescope
import plans
import profiles
import progress
import readiness
import previews
import reasoning
import recovery
import refine
import reportfiling
import required
import results
import reviews
import scheduler
import seedsources
import settings
import setupguide
import setuptasks
import stackactions
import stacks
import state
import sync
import taskfiles
import verify


# ── request classes ────────────────────────────────────────────────────

# What a request of each class must show before its handler runs. The
# words are the same ones ARCHITECTURE.md and the route snapshot use.
CLASSES = {
    "read": "a GET: reads, and changes nothing",
    "browser-mutation": (
        "a write a person makes from the board's page: JSON; Sec-Fetch-Site "
        "same-origin or none when sent; an Origin, when sent, that is this "
        "board on the port it serves or exactly one of "
        "BOARD_ALLOWED_ORIGINS; Origin: null refused; and no run named by a "
        "request that says it is a browser's"),
    "policy-mutation": (
        "changes or exercises the execution policy, or runs the project's "
        "programs: browser-mutation plus the X-Bench-Settings header"),
    "run-authenticated": (
        "a run speaking through its shim: the browser-mutation door, then "
        "the run's own token, checked by the handler"),
    "event-ingest": (
        "a hook bridge's event: the browser-mutation door; a run it names "
        "is proved by that run's token (callers.claim), and linked only "
        "when proved where BOARD_RUN_IDENTITY enforces"),
}

# Every POST body is a JSON object of at most this many bytes, unless its
# route says otherwise. Large enough for a whole policy document and four
# prompt templates; a request that needs more is not one this board makes.
MAX_BODY = 1024 * 1024


class Route(NamedTuple):
    method: str
    path: str                       # exact, or a prefix ending in "*"
    klass: str
    handler: Callable
    max_body: int = MAX_BODY


def _q(req, name: str, default: str = "") -> str:
    return (req.query.get(name) or [default])[0]


# ── payloads more than one route reads ─────────────────────────────────


def policy_payload() -> dict:
    """Where this board's execution policy comes from, and the revision of
    it that applies to the *next* launch.

    Deliberately not the whole resolution: that is per intent and costs a
    hash of every approved input, so it lives behind `/api/profile`. What
    the page needs on every state load is which document is in force and
    whether it has moved — a run wears its own frozen revision, and the
    two being comparable is what keeps "the settings say this" from being
    read as "that run rode this".
    """
    doc = profiles.document()
    adapter = profiles.adapter_identity()
    return {
        "kind": doc["kind"],
        "identity": doc["identity"],
        "describes": doc["describes"],
        "revision": doc["revision"],
        "problem": doc["problem"],
        "intents": list(profiles.INTENTS),
        "adapter": {"name": adapter["name"], "identity": adapter["identity"],
                    "version": adapter["version"],
                    "declared": adapter["declared"],
                    "capabilities": adapter["capabilities"],
                    "unsupported": adapter["unsupported"],
                    "note": adapter.get("note")},
    }


def doctor_payload(modes: list[str], *, approve: bool = False,
                   asked: bool = False) -> dict:
    """The readiness report, at whatever distance was asked for.

    One function behind both routes, because the difference between them
    is not what they compute — it is what they are *allowed* to. A GET
    passes no modes and is therefore inspection: resolving, hashing and
    reading approved files, with no process, no pin and no checkout. A
    POST may name the modes that do something, and `asked` is how this
    tells "the page opened the inspector" from "a person clicked a button
    with its side effects written beside it".

    The real-agent mode is the one that is refused rather than deferred
    when it is named without approval: a request that said "run the real
    CLI" and got a `deferred` row back would read as a mode that quietly
    did nothing, so the request itself fails and nothing is diagnosed.
    """
    for mode in modes:
        if mode not in doctor.MODES:
            raise ValueError(f"unknown doctor mode '{mode}' — one of "
                             f"{', '.join(doctor.MODES)}")
        if not asked and doctor.MODES[mode]["sideEffects"]:
            raise ValueError(
                f"the '{mode}' mode {doctor.MODES[mode]['does']}, which is "
                f"not something reading a report may do — ask for it "
                f"explicitly")
    if "real-agent" in modes and not approve:
        raise ValueError(
            "the real-agent mode launches the installed CLI with a model and "
            "spends tokens on this board's configured account, so it needs "
            "its own approval — send \"approve\": true with it. Nothing was "
            "diagnosed.")
    return doctor.report(modes, approve_real_agent=approve)


def run_diagnostic(payload: dict, evidence_id, *, count: bool = True) -> dict:
    """A run's own check diagnostics (design #94 §9): the run proved by
    the capability its launch was handed (BOARD_RUN_TOKEN) — never by a
    run id alone — then `verify.diagnostic`'s access contract: its card
    and its checks, read off its frozen manifest.

    `count` is the explicit request (`/api/verify/details`): a refusal is
    raised, and counted on the run's channel health as a refused progress
    report is. Without it — the details riding along with a verification
    reply — a refusal leaves them out and says why (`{"refused": …}`,
    not the diagnostic's own `unavailable`, which describes a log), so the
    run is not left guessing."""
    agent = str(payload.get("agent") or "")
    try:
        progress.authenticate(agent, payload.get("token"))
        # `artifact` (task 135): one artifact the verification's report
        # kept, by id, under the same access contract — never a path
        return verify.diagnostic_reply(verify.diagnostic(
            evidence_id, agent=agent,
            task=(payload.get("file") or None) if count else None,
            artifact=(payload.get("artifact") if count
                      and payload.get("artifact") is not None else None)))
    except progress.Refused as exc:
        if count:
            raise
        return {"refused": exc.why, "code": exc.code}
    except verify.DiagnosticRefused as exc:
        if count:
            raise progress.refused(agent, exc.code, exc.status, exc.why)
        return {"refused": exc.why, "code": exc.code}


def gates_payload(name: str, stage: str) -> dict:
    """One card's gates, every scope that could guard it, read.

    Both scopes a card can be guarded by are returned rather than only
    the one that applies right now: a phase member is gated at its merge
    into the integration branch *and* carried through its phase's own
    acceptance, and a reader deciding what to do next needs to see which
    of the two is in the way.

    The Bench verdicts, the formal GitHub review state and the overrides
    come with it, because the whole point of the sheet is that those are
    three different records and none of them stands in for another.
    """
    task = None
    for group in taskfiles.collect()["stages"]:
        for found in group["tasks"]:
            if found["file"] == name:
                task = found
    scopes = ["member", gates.scope_for(task or {})]
    if (task or {}).get("isPhase"):
        scopes = ["integration"]
    elif not (task or {}).get("phase"):
        scopes = ["acceptance"]
    policy = gatepolicy.gate_policy()
    actor = taskfiles.actor_name() or ""
    shown = []
    for scope in scopes:
        decision = gates.evaluate(name, scope, task=task, policy=policy)
        # Whether *this board's identity* could record an override here,
        # from the function the override route refuses with — the sheet
        # draws this answer and never works one out for itself.
        decision["overrides"].update(gates.eligibility(decision, actor=actor))
        shown.append(gates.public(decision))
    return {
        "task": name, "stage": stage,
        "scopes": shown,
        "policy": gatepolicy.gate_policy_public(policy),
        "verdicts": gates.verdicts_for(name),
        "github": gates.github_reviews(name),
        "overrides": gates.overrides_for(name),
        "decisions": gates.DECISIONS,
        "reasons": gates.REASONS,
        # Who this board would record an override against. Sent so the
        # confirmation can name them rather than asking a person to trust
        # that something sensible will be written down.
        "actor": actor or None,
        "location": "local",
    }


def state_payload() -> dict:
    """The whole board, read once. Every section below asks the policy the
    same questions — the document, each intent's resolution, every
    approved input and its provenance — once per evidence record; inside
    one read scope each is asked once for this request and no longer
    (`profiles.read_scope`, task 127). The scope is this thread's and ends
    with the payload: no memo outlives the request, and no mutation path,
    which never opens one, reads through it."""
    with profiles.read_scope():
        return _state_payload()


def _state_payload() -> dict:
    with state.LOCK:
        sessions = sorted(
            (dict(m) for m in state.SESSIONS.values()),
            key=lambda m: m.get("last") or m.get("started") or 0, reverse=True)
        board_events = list(state.BOARD_EVENTS[-80:])
        # read before the disk is: every change announced at or below this
        # revision was already on disk when it was announced, so this
        # reading holds it (task 128 — the page's order guard)
        board_rev = state.BOARD_REV
    collected = taskfiles.collect()
    # which card each card's work is stacked on, what is stacked on it and
    # the state of each edge (task 117) — local reads only, read once and
    # handed to the delivery reading rather than derived twice
    stacked = stacks.board(collected)
    return {
        # every card as a list entry: its fields and none of its text
        # (task 129). What the server reads out of the text travels as the
        # fields and the sections below, all computed from `collected`,
        # which keeps the text; the text itself is `/api/card`, on opening
        "board": taskfiles.list_board(collected),
        # per phase card, its `## Phase log` entries with their times —
        # what the Phases view draws under a lane, without the card's text
        "phaseLogs": phases.board_logs(collected),
        "project": config.PROJECT,
        # the resolved roots: which application this board manages, where
        # its work state lives, and which repository the bookkeeping goes
        # to — so a board can say what it is driving rather than implying it
        "layout": config.layout(),
        # where each card's work starts and what its PR targets, for the
        # cards where that is not simply the application default — read
        # from the card's own headers, its phase, and the plan recorded
        # when its run began. Empty on a board of ordinary cards.
        "plans": plans.board(collected),
        # per stacked card: its parent card, the cards stacked on it, its
        # place on its path, its selected base, review target and landing
        # target, and its edge's state in the server's words. Empty on a
        # board where no card is stacked on another
        "stacks": stacked,
        "appBase": config.APP_BASE,
        "sessions": sessions,
        "agents": agents.list_public(),
        # the build this board serves its page's assets from, and whether
        # the one on disk has moved on since it booted (task 123): an
        # update waiting for a restart, which the header says
        "assets": assets.public(),
        "prs": changes.public_state(),
        # per card with a change request, its combined review reading: the
        # verdict filed in the card folded with the host's, and whether a
        # review has come in at all (task 125) — drawn, never recomputed
        "verdicts": changes.board_verdicts(collected),
        # per phase card, the members its branch already holds, from the
        # runner's last pass and the phase log (task 125)
        "phaseMerged": phases.board_merged(collected),
        # what the last pass of the phase runner saw: per phase card in
        # in-progress/, its branch, whether a run is in force or halted (and
        # why, and where), and each member's state — the header chip and the
        # phase card's own list are both read from here
        "phases": phases.public_state(),
        # per phase card, who schedules it and where its lifecycle stands,
        # with the one next step and who holds it (task 160): the face,
        # the lane and the Orchestrator draw this one reading, so an
        # adopted phase is never read as a runner nobody started
        "phaseLifecycle": phasescope.board_lifecycle(collected),
        "drive": drive.public(),
        "hasDriver": config.driver_path() is not None,
        # whether this project gave its headless agents anything to run. The
        # one setting an agent cannot work around, so the board says it is
        # missing rather than letting a run discover it — see the header chip
        "hasAgentCommands": bool(config.agent_commands()),
        # where the execution policy comes from and which revision of it
        # applies *now*. Cheap (one file read), and it is what lets a run's
        # frozen policy be told apart from the settings as they stand —
        # the whole per-intent reading is `/api/profile`, on demand.
        "policy": policy_payload(),
        # the cheap half of the Settings reading: whether a policy document
        # is in force, whether it refuses anything, how many checks it
        # declares and which scopes it gates. One file read and one gate
        # resolution — the whole page is `/api/settings`, on demand.
        "settings": settings.board_summary(),
        # what bench's own verification runs found, per card: the worst
        # thing any of them says and how many there are. Cheap, and empty
        # on a board nobody declared a check on — the whole reading is
        # `/api/evidence`, on demand. A card is never shown as verified
        # here from anything but a recorded run's own exit status.
        "verify": verify.board(collected),
        # where a PR review of each card actually ran: the revision it was
        # pinned to, the checkout bench owned for it, and whether that is
        # still the head the pull request proposes. Empty on a board that
        # has reviewed nothing, so the chip follows the same law as the
        # plan's and the profile's — the whole reading is `/api/review`.
        "reviews": reviews.board(collected),
        # what this board's gates require before a merge, and what each
        # card's gate currently says. Empty on a board that declared
        # none — the same law every other chip follows — and never a
        # pass: `ungated` is its own word, and a card with an unmet gate
        # carries the reason rather than a colour.
        "gates": gates.board(collected),
        "gatePolicy": gatepolicy.gate_policy_public(gatepolicy.gate_policy()),
        # per card, the one delivery status every surface draws: the word,
        # its sentence, the primary action and who holds it, the
        # candidate commit's state and the required-only counts. Memoised
        # by a fingerprint of every input it reads — never authority; the
        # whole reading is `/api/card/delivery`, always recomputed.
        "delivery": delivery.board(collected, phases.public_state(),
                                   stacked),
        # per card, a batch of required checks in flight on this board:
        # how many it set out to request, how many have an answer, the one
        # running now. The whole record is `/api/verify/required`.
        "requiredRuns": required.running(),
        # card refinements this board holds that are running, waiting for a
        # decision, or failed: the newest per card. Runtime state, local to
        # this board — the decisions themselves are in the cards
        "refinements": refine.board(),
        # what each card's most recent run has *said* about its work —
        # agent-reported, never a result — with its channel's health and
        # whether the run has ended. The whole timeline is `/api/progress`.
        "progress": progress.board_public(),
        # per card, how many tracked criteria a named person has accepted
        # and how many review findings are still open — computed from the
        # card's own records, never from its column. Empty on a board that
        # tracks nothing; the whole reading is `/api/card/acceptance`.
        "acceptance": acceptance.board(collected),
        # per card with orchestration history, how many of its questions
        # are open, need re-evaluation or were answered, its open blockers
        # and its responses (task 140) — read from the card's own records,
        # never from a local index. The whole reading is
        # /api/card/decisions. Empty on a board with no such history
        "decisions": decisions.board(collected),
        # whether orchestration is on, each scope's pause and which scope
        # holds each managed card (task 145) — what the board strip and a
        # card's Overview link from. One file read; the whole reading is
        # GET /api/orchestration
        "orchestration": orchestrator.board_summary(collected),
        # per card that names supporting information, whether a run could
        # use all of it now (task 132): Ready, Needs information, Checking
        # or Unchecked, with counts. The same resolver every launch and the
        # move into in-progress ask; the whole reading is /api/card/inputs
        "information": inputs.board(collected),
        # per card that asks to be shown, or has kept output, its Show the
        # result mode and how many outputs this board keeps (task 133);
        # the whole reading is /api/card/result
        "results": results.board(collected),
        "branches": changes.task_branches(),
        "commands": config.commands(),
        "commandRuns": commands.public(),
        # cards this board is midway through merging and cleaning up: the
        # busy state renders from here, not from what a tab happened to click
        "completing": state.completing_public(),
        # completions that merged and did not publish: what was merged,
        # into what, and why the remote does not hold it. A directory
        # listing, empty on a board where nothing failed, and it survives
        # a restart — the card wears it until a retry finishes the work
        "unpublished": completion.recoveries(),
        "checks": config.checks(),
        # who this board is, so a card can tell "yours" from "someone
        # else's". Empty outside team mode: nothing claims anything there.
        "me": taskfiles.actor_name() if config.COMMIT_MOVES else "",
        "archivedCount": taskfiles.archived_count(),
        # which stages an archive may come from — the same set `archive_task`
        # refuses anything outside, sent so the card's ⌸ chip and the drag
        # gesture offer exactly what the server will do, from one authority
        "archiveFrom": sorted(taskfiles.ARCHIVE_FROM,
                              key=lambda slug: taskfiles.STAGE_ORDER.get(slug, 99)),
        "sync": sync.status(),
        "boardEvents": board_events,
        "boardRev": board_rev,
        "boardBoot": state.BOOT,
        "now": time.time(),
    }


_TITLE = re.compile(rb"<title>.*?</title>", re.DOTALL)


def page_bytes() -> bytes:
    """board.html with the project's name rendered into its <title>, so the
    tab reads right on first paint rather than after the first state load,
    and the build it was booted with written where it names its assets —
    the snapshot's copy of the page once booted (task 123), so a page and
    its assets always come from one build."""
    html = assets.page_html() or b""
    title = escape(f"{config.PROJECT} · bench").encode("utf-8")
    html = _TITLE.sub(lambda _: b"<title>" + title + b"</title>", html, count=1)
    return html.replace(assets.PLACEHOLDER,
                        assets.current_build().encode("ascii"))


# ── GET: reads ─────────────────────────────────────────────────────────


def get_page(req) -> None:
    if assets.page_html() is None:
        req.send(500, b"board.html is missing", "text/plain")
        return
    # no-store, as every reply here: the page names the build, so a cached
    # page is how a browser would keep asking for a build nobody serves
    # the script policy (task 124): the build's own modules and nothing else
    req.send(200, page_bytes(), "text/html; charset=utf-8",
             {"Content-Security-Policy": assets.PAGE_POLICY,
              "X-Content-Type-Options": "nosniff"})


# The page's own addresses (task 134): every destination the page can show
# has a path, and opening that path — directly, or by refreshing — is the
# page itself. Only these shapes are; anything else is a 404 as it always
# was, so `/api/…`, `/web/…` and `/files/…` keep their own answers and an
# address nobody documented is not a page.
UI_PATHS = {"board", "brain", "orchestrator", "phases", "sessions", "focus",
            "settings"}
UI_ITEM_PREFIXES = {"cards", "orchestrator", "phases", "sessions", "focus",
                    "settings"}
# segments that name a section, not a card or a session
UI_SECTION_PREFIXES = {"settings", "orchestrator"}
UI_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")
UI_SECTION_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


def ui_path(path: str) -> bool:
    """Whether `path` is one of the page's addresses: `/board`,
    `/orchestrator`, `/phases`, `/sessions`, `/focus`, `/settings`, or one
    of `/cards/<card>`, `/orchestrator/<section>`, `/phases/<card>`,
    `/sessions/<session>`, `/focus/<session>` and `/settings/<section>`
    with a single segment of the narrow alphabet an identifier is written
    in. A trailing slash, an empty or second segment, and anything
    percent-encoded are not. A Brain entry is `/brain/e/<id>` (task 157),
    its id in the narrower alphabet `brain.ID_RE` reads."""
    parts = path.split("/")
    if len(parts) == 4 and parts[1] == "brain" and parts[2] == "e":
        return bool(brain.ID_RE.match(parts[3]))
    # seeding the Brain (task 192): `/brain/seed`, its origin in the query
    if len(parts) == 3 and parts[1] == "brain" and parts[2] == "seed":
        return True
    # a filed review in the Brain (task 168): `/brain/r/<card>/<review>`,
    # the card by number or stem, the review by its rv-/rl- identity
    if len(parts) == 5 and parts[1] == "brain" and parts[2] == "r":
        return bool(UI_ID_RE.match(parts[3]) and brainreviews.REVIEW_ID_RE.match(parts[4]))
    if len(parts) == 2:
        return parts[1] in UI_PATHS
    if len(parts) != 3 or parts[1] not in UI_ITEM_PREFIXES:
        return False
    pattern = UI_SECTION_RE if parts[1] in UI_SECTION_PREFIXES else UI_ID_RE
    return bool(pattern.match(parts[2]))


def get_ui_page(req) -> None:
    # The same page `/` serves, and nothing more: what it shows is read by
    # the page, through the same GETs every view already makes. Opening an
    # address launches, moves, applies and replays nothing.
    if not ui_path(req.path):
        req.send(404, b"not found", "text/plain")
        return
    get_page(req)


def get_card_locate(req) -> None:
    # Where the card an address names is now — a stage, or the archive —
    # by number or file stem (task 134). A read of the task files only.
    try:
        found = taskfiles.locate_card(_q(req, "id"))
    except ValueError as exc:
        req.reply(400, {"error": str(exc), "code": "invalid"})
        return
    except taskfiles.CardNotHere as exc:
        answer = {"error": str(exc), "code": exc.code}
        if exc.code == "ambiguous":
            answer["files"] = getattr(exc, "files", [])
        req.reply(409 if exc.code == "ambiguous" else 404, answer)
        return
    except OSError as exc:
        req.reply(500, {"error": f"the card could not be read: {exc}",
                        "code": "unreadable"})
        return
    req.reply(200, found)


def get_web(req) -> None:
    """`/web/<build>/<path>`: one asset of the build in force, from the
    boot snapshot (immutable) or, under BOARD_ASSETS=disk, from disk
    (no-store). Any other build, an unlisted path or a malformed one is a
    404 — the route serves the manifest's files and nothing beside them."""
    rest = req.path[len("/web/"):]
    build, _, name = rest.partition("/")
    served = assets.lookup(build, name)
    if served is None:
        req.send(404, b"not found", "text/plain")
        return
    req.send(200, served.body, served.type,
             {"Cache-Control": served.cache,
              "X-Content-Type-Options": "nosniff"})


def get_tasks(req) -> None:
    req.reply(200, taskfiles.collect())


def get_state(req) -> None:
    req.reply(200, state_payload())


def get_session(req) -> None:
    sid = _q(req, "id")
    with state.LOCK:
        meta = dict(state.SESSIONS.get(sid) or {})
    req.reply(200, {"meta": meta, "events": events.session_events(sid)})


def get_progress(req) -> None:
    # One run's progress whole — the current update, the milestone
    # checklist and the timeline — by agent id, or the most recent
    # run on a card by file. Read-only; agent-reported throughout.
    agent_id = _q(req, "agent")
    if not agent_id:
        latest = progress.board_public({_q(req, "file")}).values()
        agent_id = next((p["agent"] for p in latest), "")
    found = progress.public(agent_id, full=True) if agent_id else None
    req.reply(200 if found else 404,
              {"progress": found} if found else
              {"error": "no progress recorded for that run"})


def get_plan(req) -> None:
    # The preflight itself, on demand: the whole resolved plan or
    # the reason it does not resolve, before anything is started.
    # Read-only — it fetches (bounded) and answers, and creates no
    # branch, no worktree and no run.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    try:
        req.reply(200, {"plan": plans.preflight(name, stage)})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_task_completion(req) -> None:
    # What merge & clean up would do for this card — merge mode,
    # target, publication and any refusal — from the authorities
    # the completion itself reads, so the sheet describes the
    # completion that would run. Read-only: no fetch, no gh.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    try:
        if not name.endswith(".md") or "/" in name or not stage:
            raise ValueError(f"no card {name!r} on this board")
        req.reply(200, completion.completion_facts(name, stage))
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_profile(req) -> None:
    # The profile preflight, on demand, for one card and one
    # launch intent: what that run would be allowed to do, which
    # files it would be given, and what stops it. Read-only — it
    # resolves and hashes, and pins nothing, so opening it before
    # a launch costs nothing and is the point.
    #
    # This is the *same* resolver the launch rides. The UI must
    # never grow a second settings parser that could disagree
    # with what a run was actually granted.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    intent = _q(req, "intent", "work")
    try:
        # For `review`, the root a PR review actually works in —
        # the checkout it holds, or the one the head bench last
        # saw would put it at. Read free, from a record and the
        # PR poll: the page must show the directory the run would
        # start in, and must not pin, fetch or ask GitHub to.
        held = reviews.live_for_task(name) if intent == "review" else None
        pinned = (reviews.pinned_path_for(name)
                  if intent == "review" else None)
        found = profiles.manifest(intent, name, stage,
                                  review=pinned, review_record=held)
        req.reply(200, {"profile": profiles.public(found),
                        "policy": policy_payload()})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_manifest(req) -> None:
    # What one run actually rode, read back off disk rather than
    # re-resolved: the frozen policy and the inputs as they were
    # pinned, plus what has moved since. A profile edited mid-run
    # changes the next launch and never this record.
    agent_id = _q(req, "agent")
    frozen = profiles.recorded(agent_id)
    if frozen is None:
        req.reply(404, {"error": f"no recorded manifest for {agent_id}"})
    else:
        req.reply(200, {"manifest": profiles.public(frozen),
                        "freshness": profiles.freshness(frozen),
                        "policy": policy_payload()})


def get_evidence(req) -> None:
    # What bench's own verifications found for one card, read back
    # off disk: one row per check, including the checks nothing
    # asked for. Reading is reading — opening a detail panel
    # re-runs nothing, which is why this is a GET of stored
    # records and not a resolution.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    intent = _q(req, "intent", "work")
    manifest = None
    try:
        # which checks this card's run *could* ask for, so a check
        # that never ran is a row rather than an absence — with
        # the review checkout this card holds, if it holds one,
        # for `/api/profile`'s reason above
        held = reviews.live_for_task(name) if intent == "review" else None
        manifest = profiles.manifest(
            intent, name, stage,
            review=(held or {}).get("path") or None,
            review_record=held)
    except (ValueError, OSError):
        manifest = None
    req.reply(200, {
        "rollup": verify.rollup(name, manifest),
        "evidence": [verify.public(r) for r in evidence.for_task(name)],
        # requests answered by an existing record, each naming the
        # evidence it returned and who asked — not evidence itself
        "reuses": [verify.public_reuse(r)
                   for r in evidence.reuses_for_task(name)[:20]],
        "outcomes": evidence.OUTCOMES,
    })


def get_review(req) -> None:
    # Where this card's PR reviews ran, and what they were of: the
    # repository, the base and head identities, the checkout bench
    # owned, and — per review — whether the pull request still
    # proposes that head.
    #
    # A GET of records and one reading of what the poll last saw.
    # It provisions nothing, fetches nothing and starts no
    # validation: opening a historical review must not be a way to
    # set anything running, least of all against an integration
    # branch the review never judged.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    records = reviews.for_task(name)
    pinned = [verify.public(r) for r in evidence.for_task(name)
              if (r.get("root") or {}).get("policy") == "review"]
    req.reply(200, {
        "reviews": [reviews.public(r) for r in records],
        # what the PR currently proposes, as the board last read
        # it — the fact every `stale` above is measured against
        "head": reviews.observed(name),
        "evidence": pinned,
        "outcomes": evidence.OUTCOMES,
        "authorities": reviews.AUTHORITIES,
        "targets": reviews.TARGETS,
        "stage": stage,
    })


def get_gates(req) -> None:
    # What this card's gates require and what the records say —
    # the same reading the merge path makes, from the same
    # function, so a disabled action and a backend refusal can
    # never give two different reasons for one state.
    #
    # A GET of stored records and one `for-each-ref`. It runs no
    # check, relaunches nothing and reaches no remote, which is
    # why opening the sheet is not an action.
    name = _q(req, "file")
    stage = _q(req, "stage") or (taskfiles.find_stage_of(name) or "")
    try:
        req.reply(200, gates_payload(name, stage))
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_evidence_log(req) -> None:
    # One verification's own output, scrubbed of anything shaped
    # like a credential on the way out. The record itself never
    # held content; this is the one place bytes bench did not
    # write reach a reader.
    try:
        req.reply(200, evidence.log_text(_q(req, "id")))
    except (ValueError, OSError) as exc:
        req.reply(404, {"error": str(exc)})


def get_verify_required(req) -> None:
    # What Run required checks would do for one card, now, and the
    # last batch it ran. A reading: the gate, the candidate, the
    # checkout's cleanliness and the work reading's checks. It
    # starts nothing, pins nothing and creates no checkout, which
    # is why the confirmation may be opened freely.
    try:
        req.reply(200, {"plan": required.plan(
            _q(req, "file"), _q(req, "stage") or None)})
    except required.Refused as exc:
        req.reply(exc.status, {"error": str(exc), "code": exc.code})


def get_verify_checks(req) -> None:
    # What one run may ask for, read off its frozen manifest — the
    # same answer `bench-verify` with no arguments prints.
    agent_id = _q(req, "agent")
    frozen = profiles.recorded(agent_id)
    if frozen is None:
        req.reply(404, {"error": f"no recorded profile for {agent_id}"})
    else:
        req.reply(200, {"checks": [
            {"name": c["name"], "run": c["run"], "root": c["root"],
             "scopes": sorted(c["scopes"]), "help": c["help"],
             "required": c["required"]}
            for c in verify.available(frozen)]})


def get_doctor(req) -> None:
    # Readiness, read. The whole per-intent resolution, every
    # diagnostic with its remedy, bench's own fixture documents
    # through the same resolver, and the coverage table saying
    # which modes were *not* asked for.
    #
    # A GET names no mode, so this starts no process, pins
    # nothing, provisions no checkout and spends nothing —
    # which is why the inspector may be opened without a
    # confirmation. Everything that does something is a POST
    # with the mode named.
    try:
        req.reply(200, {"doctor": doctor_payload([])})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_settings(req) -> None:
    # The whole Settings reading: effective configuration, what it
    # licenses a reader to believe, what to configure next, and the
    # draft to edit.
    #
    # A GET, and it stays one. Assembling it resolves the policy,
    # reads records already on disk and asks git for the tool's own
    # revision. It runs no project program, invokes no adapter
    # hook, launches no model, pins nothing and writes nothing —
    # which is why opening Settings is not an action.
    try:
        req.reply(200, {"settings": settings.page()})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_setup(req) -> None:
    # The setup guide (task 88): six areas read from the same
    # authorities Settings, the doctor and a launch ask. A GET that
    # stays one — it runs no project program, launches no agent,
    # repairs nothing and writes nothing.
    try:
        req.reply(200, {"setup": setupguide.state()})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_setup_verification(req) -> None:
    # Verification readiness (task 90): discovered candidates, each
    # check's readiness and the bootstrap condition. A GET that
    # stays one — discovery reads files and runs nothing.
    try:
        req.reply(200, {"verification": readiness.state()})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_preview(req) -> None:
    # The application preview (task 91): discovery, the configuration
    # and its refusals, the lifecycle bound to its source. A GET that
    # stays one — it reads files and asks git; it starts, prepares
    # and runs nothing.
    try:
        req.reply(200, {"preview": previews.state(_q(req, "file") or None)})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_preview_log(req) -> None:
    req.reply(200, previews.log())


def get_card_discussion(req) -> None:
    # A card's discussion, the jobs this board holds for it and
    # whether refining is available — read, never started: a GET
    # here launches nothing and writes nothing.
    name = _q(req, "file")
    stage = _q(req, "stage") or taskfiles.find_stage_of(name) or ""
    try:
        req.reply(200, refine.for_card(name, stage))
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_card(req) -> None:
    # One card's text for its detail view (task 129), asked by name
    # and stage: a card that moved, was archived or deleted since the
    # page listed it is answered as such, never read from elsewhere.
    # A read: it writes nothing and launches nothing.
    try:
        found = taskfiles.card_detail(_q(req, "file"), _q(req, "stage"))
    except ValueError as exc:
        req.reply(400, {"error": str(exc), "code": "invalid"})
        return
    except taskfiles.CardNotHere as exc:
        answer = {"error": str(exc), "code": exc.code}
        if exc.stage:
            answer["stage"] = exc.stage
        req.reply({"moved": 409, "archived": 410}.get(exc.code, 404), answer)
        return
    except OSError as exc:
        req.reply(500, {"error": f"the card could not be read: {exc}",
                        "code": "unreadable"})
        return
    req.reply(200, {"card": found})


def get_card_acceptance(req) -> None:
    # A card's acceptance criteria and their states, its review
    # rounds and findings, and its dated reports. A GET: it assigns
    # no id and writes nothing — an untracked card reads as one.
    name = _q(req, "file")
    stage = _q(req, "stage") or taskfiles.find_stage_of(name) or ""
    try:
        found = acceptance.for_card(name, stage)
        # Whether a merge rule asks this card for a sign-off — so
        # the section can say so, or say that none does. Read, as
        # the rest of this answer is.
        try:
            found["signoff"] = gates.signoff_reading(name, stage)
        except (ValueError, OSError) as exc:
            found["signoff"] = {"required": None, "error": str(exc)}
        req.reply(200, found)
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_card_decisions(req) -> None:
    # A card's orchestration history (task 140): its questions, open and
    # resolved with their options kept, the answers and who gave them,
    # the reasoning responses, blockers, recovery attempts and actions —
    # read from the card's own records, and for a phase card its members'
    # questions linked to the member that owns each. A GET: it writes,
    # launches and sends nothing.
    name = _q(req, "file")
    stage = _q(req, "stage") or taskfiles.find_stage_of(name) or ""
    try:
        req.reply(200, decisions.for_card(name, stage))
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_ask(req) -> None:
    # Asking the orchestrator (task 181): every conversation this board can
    # read — on cards and in the board's own conversation files — with what
    # asking would launch and count. A read: it launches no run, however
    # often it is asked; only POST /api/ask does.
    board = taskfiles.collect()
    card = _q(req, "card") or None
    scope = None
    if _q(req, "scope"):
        scope = {"kind": _q(req, "scope"), "card": card}
    req.reply(200, {"conversations": ask.conversations(card, board),
                    "preflight": ask.preflight(scope, board),
                    "scopes": list(decisions.ASK_SCOPES),
                    "controls": list(decisions.CONTROLS),
                    "actor": decisions.actor()})


def get_ask_thread(req) -> None:
    # One conversation, each reply beside the live state of its run. A read.
    try:
        req.reply(200, ask.thread(_q(req, "conversation")))
    except ask.Refused as exc:
        req.reply(404, {"error": str(exc), "code": exc.code})
    except ValueError as exc:
        req.reply(400, {"error": str(exc)})


def get_ask_facts(req) -> None:
    # The facts one question's run was given — the evidence its answer
    # cites, as bench wrote it. A read.
    try:
        req.reply(200, {"facts": ask.facts_for(_q(req, "operation"))})
    except ask.Refused as exc:
        req.reply(404, {"error": str(exc), "code": exc.code})


def post_ask(req) -> None:
    # Ask, retry or cancel: a person's explicit request, and the only way a
    # question launches a run. Asking explains — nothing here moves a card,
    # starts work, runs a check or changes the agreement.
    payload, action = req.body, req.path.rsplit("/", 1)[1]
    try:
        if action == "retry":
            result = ask.retry(str(payload.get("conversation") or ""),
                               str(payload.get("ask") or ""))
        elif action == "cancel":
            result = {"job": ask.cancel(str(payload.get("operation") or ""))}
        else:
            result = ask.submit(payload.get("scope"), payload.get("text"),
                                request=payload.get("request"),
                                conversation=payload.get("conversation") or None)
    except ask.Refused as exc:
        req.reply(409, {"error": str(exc), "code": exc.code, "remedy": exc.remedy})
        return
    except taskfiles.StaleCard as exc:
        req.reply(409, {"error": str(exc), "stale": True})
        return
    except ValueError as exc:
        req.reply(400, {"error": str(exc)})
        return
    if action != "cancel":
        state.record_board_event({
            "kind": "agent", "actor": "you",
            "file": (result.get("where") or {}).get("file") or "",
            "summary": f"asked the orchestrator ({result.get('conversation')}) — "
                       f"one read-only reasoning run; nothing is acted on"})
    state.broadcast({"type": "board"})
    req.reply(200, result)


def post_card_decisions(req) -> None:
    # A person's answer to an open question, or withdrawing one — bound to
    # the question's newest record, the brief and the identity the page
    # showed. The actor is this board's identity, never a field in the
    # body; nothing is launched or sent because a question was answered.
    payload = req.body
    name, stage = str(payload["file"]), str(payload["stage"])
    question = str(payload.get("question") or "")
    try:
        if req.path.endswith("/answer"):
            choice = payload.get("choice")
            result = decisions.answer(
                name, stage, question=question, basis=payload.get("basis") or None,
                brief_sha=str(payload.get("briefSha") or ""),
                actor_seen=str(payload.get("actor") or ""),
                choice=choice if isinstance(choice, int) else None,
                note=payload.get("note"), request=payload.get("request"))
            said = f"{name}: question {question} answered"
        else:
            result = decisions.withdraw(
                name, stage, question=question, basis=payload.get("basis") or None,
                note=payload.get("note"), request=payload.get("request"))
            said = f"{name}: question {question} withdrawn"
    except taskfiles.StaleCard as exc:
        req.reply(409, {"error": str(exc), "stale": True})
        return
    except decisions.Refused as exc:
        req.reply(403, {"error": str(exc), "refused": True})
        return
    if result.get("written"):
        who = decisions.actor()["name"] or "you"
        state.record_board_event({
            "kind": "agent", "actor": "you", "file": name,
            "summary": f"{said} by {who}" + (
                "" if result.get("committed") else
                " (written to the card, not committed)")})
        state.broadcast({"type": "board"})
    req.reply(200, result)


def get_card_inputs(req) -> None:
    # A card's Inputs and required tools, read now: the contract in its
    # brief, every input and tool with its state, and what the brief
    # names that the contract does not. Passive — local files are read,
    # nothing is retrieved, nothing written, nothing launched.
    name = _q(req, "file")
    stage = _q(req, "stage") or taskfiles.find_stage_of(name) or ""
    try:
        with profiles.read_scope():
            req.reply(200, inputs.public(inputs.readiness(name, stage)))
    except FileNotFoundError:
        req.reply(404, {"error": f"{name} is not in {stage}/"})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def post_card_inputs(req) -> None:
    # Recheck information (a bounded check, joined when one is already
    # running for the same brief and profile), or a person's
    # classification written into the brief's own section.
    payload = req.body
    name, stage = str(payload["file"]), str(payload["stage"])
    try:
        if req.path.endswith("/check"):
            result = inputs.schedule(name, stage, why="recheck")
            said = f"{name}: information check started"
        else:
            result = inputs.classify(
                name, stage, brief_sha=str(payload.get("briefSha") or ""),
                source=str(payload.get("source") or ""),
                identity=str(payload.get("identity") or ""),
                classification=str(payload.get("classification") or ""),
                kind=str(payload.get("kind") or "input"),
                note=str(payload.get("note") or ""))
            # the line as written, which classify built from a source it
            # refused a credential in; the request's own text is not used
            said = (f"{name}: {taskfiles.one_line(result.get('line') or '', 160)} "
                    f"(classified)")
    except taskfiles.StaleCard as exc:
        req.reply(409, {"error": str(exc), "stale": True})
        return
    except FileNotFoundError:
        req.reply(404, {"error": f"{name} is not in {stage}/"})
        return
    if not result.get("joined"):
        state.record_board_event({"kind": "card", "actor": "you", "file": name,
                                  "summary": said})
    state.broadcast({"type": "board"})
    req.reply(200, result)


def get_card_delivery(req) -> None:
    # One card's delivery status, always recomputed: what is
    # happening, the next action and who holds it, and what the
    # merge rules still need at the current commit. Read-only — no
    # fetch, no GitHub call, no check, no record written.
    name = _q(req, "file")
    stage = _q(req, "stage") or None
    try:
        req.reply(200, {"delivery": delivery.for_card(
            name, stage, phases.public_state())})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_card_timing(req) -> None:
    # One card's cycle time (task 165): lead time, its stage timeline,
    # each run, the checks' union and the waits, from records bench
    # already keeps. Read-only — no fetch, no launch, no record written.
    name = _q(req, "file")
    stage = _q(req, "stage") or None
    try:
        req.reply(200, {"timing": cycletime.for_card(name, stage)})
    except cycletime.Moved as exc:
        req.reply(409, {"error": str(exc), "moved": True, "stage": exc.stage})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_settings_history(req) -> None:
    req.reply(200, {"history": settings.history()})


def get_diff(req) -> None:
    agent_id = _q(req, "agent")
    try:
        req.reply(200, agents.agent_diff(agent_id))
    except (ValueError, subprocess.SubprocessError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def get_stream(req) -> None:
    req.stream()


def get_files(req) -> None:
    req.extra_file(req.path)


def get_brain(req) -> None:
    # The Brain's index (task 157): a read of brain/ under the work root
    # and nothing else — no directory made, no git, no fetch, no launch.
    # `referenceFiles` (task 172) says whether there is legacy material to
    # offer for review: one directory listing, no file opened
    req.reply(200, {"brain": {**brain.index(), "referenceFiles": adoption.count()}})


def get_brain_entry(req) -> None:
    # One entry by its stable id, metadata and body from one reading of its
    # file. An id nothing carries is a 404 that says so; two files claiming
    # one id are a 409 naming both, never one of them picked.
    try:
        found = brain.entry(_q(req, "id"))
    except brain.NotAnId as exc:
        req.reply(400, {"error": str(exc), "code": "not-an-id"})
        return
    status = {"unknown": 404, "duplicate": 409}.get(found["state"], 200)
    if found["state"] in ("entry", "tombstone"):
        # what the entry's own record says about its sources (task 171),
        # and the handbook sections that link it — brain/ and local git
        # metadata only
        found["authority"] = handbook.authority(found)
        found["handbook"] = handbook.membership(found["id"])
        found["adoption"] = adoption.about(found)
    req.reply(status, {"entry": found})


def get_brain_handbook(req) -> None:
    # The curated handbook (task 171): its membership record, each linked
    # entry said as what it is, each section's coverage and every link's
    # source authority. A read: no file content, fetch, check or launch.
    req.reply(200, {"handbook": handbook.reading()})


def get_brain_adoption(req) -> None:
    # What is in reference/ and what adopting it would mean (task 172): a
    # read of the files, the cards and local git metadata. Nothing is
    # written, fetched, launched or run.
    req.reply(200, {"adoption": adoption.inventory()})


def get_brain_adoption_preview(req) -> None:
    # One file's choices, with the exact bytes and destination an Apply
    # will be held to. A read, like the inventory.
    try:
        req.reply(200, {"preview": adoption.preview(_q(req, "name"))})
    except adoption.NotAName as exc:
        req.reply(400, {"error": str(exc), "code": "not-a-name"})


def post_brain_adoption_apply(req) -> None:
    # One file adopted as one entry, through the Brain's one writer: bound
    # to the previewed source and destination and to a request identity.
    try:
        result = brainwrite.write("adopt", req.body)
    except brainwrite.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, {"operation": result})


def get_brain_seed(req) -> None:
    # Seeding a Brain, step one (task 192): the application and work
    # identities a seed would use (or why the mapping is refused), the
    # Brain destination and every candidate source by name and size.
    # Local git reads and lstat only: nothing is opened, run, fetched,
    # launched or written.
    req.reply(200, {"seed": seedsources.reading()})


def post_brain_seed_inspect(req) -> None:
    # Reads exactly the sources a person approved and answers the version 1
    # seed manifest. A read with a body, like a stack sheet's preview: it
    # writes nothing, launches nothing, and authorizes no generation.
    try:
        req.reply(200, {"manifest": seedsources.inspect(req.body)})
    except seedsources.Refused as exc:
        req.reply(exc.status, {"error": str(exc), "code": exc.code, **exc.extra})


def get_brain_revisions(req) -> None:
    # An entry's revisions (task 158): the work-state commits that touched
    # its file, read from local git, and this board's own saves of it.
    try:
        req.reply(200, {"revisions": brainwrite.revisions(_q(req, "id"))})
    except brain.NotAnId as exc:
        req.reply(400, {"error": str(exc), "code": "not-an-id"})


def get_brain_operation(req) -> None:
    # One Brain write's state now: saved, committed, shared — each read
    # again, so a lost reply is answered from the record, never guessed.
    try:
        found = brainwrite.operation(_q(req, "request"))
    except brainwrite.Refused as exc:
        req.reply(exc.status, {"error": str(exc)})
        return
    if found is None:
        req.reply(404, {"error": "no Brain write has this request identity on this board"})
        return
    req.reply(200, {"operation": found})


def post_brain_write(req) -> None:
    # The person's Brain writes (task 158): create, edit, classify, the
    # manual override and retirement — each one file under brain/, bound to
    # the revision the page read and to a request identity, committed
    # through the work state's scoped commit. The actor is this board's
    # identity, never a field in the body.
    kind = req.path.rsplit("/", 1)[-1]
    try:
        if kind == "retry":
            result = brainwrite.retry(req.body)
        else:
            result = brainwrite.write(kind, req.body)
    except brainwrite.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, {"operation": result})


def get_brain_reviews(req) -> None:
    # The reviews filed on this board's cards, as the Brain lists them
    # (task 168): read from the cards (stages and archive) with
    # acceptance's own readers, each with its classification record or
    # Unclassified. A read — nothing is written to say Unclassified. Since
    # task 169 it also says which reviews this board's index does not list,
    # which indexed reviews lost their source, and the reports this board
    # has not filed; it still writes no index, operation or card.
    req.reply(200, {"reviews": reportfiling.reading()})


def get_report_filing(req) -> None:
    # This board's report filing operations (task 169): one by its
    # identity, or the newest — optionally one card's. Machine-local: a
    # replica that never ran the report has no record of it, and says so.
    op = _q(req, "operation")
    try:
        if op:
            found = reportfiling.operation(op)
            if found is None:
                req.reply(404, {"error": "this board has no record of that filing "
                                "operation; a report is retained only on the board "
                                "whose run wrote it", "code": "not-on-this-board"})
                return
            req.reply(200, {"operation": found})
            return
        req.reply(200, {"operations": reportfiling.operations(_q(req, "card") or None)})
    except reportfiling.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_report_filing_retry(req) -> None:
    # A person's Retry filing, Retry commit, Retry push or Close of one
    # retained report (task 169): filed on its original card on this board
    # or refused, never appended twice, never written anywhere else.
    try:
        result = reportfiling.retry(req.body)
    except reportfiling.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, {"operation": result})


def post_brain_reviews_rebuild(req) -> None:
    # The explicit rebuild of the Brain's review index from the cards (task
    # 169), bound to the index revision the page read. It writes the index
    # and filing records only: no card, no classification record.
    try:
        result = reportfiling.rebuild(req.body)
    except reportfiling.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, {"rebuild": result})


def get_brain_review(req) -> None:
    # One filed review at its Brain address, `/brain/r/<card>/<review>`:
    # the card's stored report and what bench recorded about it. A review
    # the card does not hold, or a card not here, is a 404 that says which;
    # one held by two cards is a 409 naming both, never one picked.
    try:
        found = brainreviews.review(_q(req, "card"), _q(req, "id"))
    except brainreviews.NotAnAddress as exc:
        req.reply(400, {"error": str(exc), "code": "not-an-address"})
        return
    status = {"no-card": 404, "no-review": 404, "ambiguous": 409}.get(found["state"], 200)
    req.reply(status, {"review": found})


def post_brain_review_write(req) -> None:
    # A person's classification of a filed review, or its manual override
    # (task 168): one record under brain/reviews/, bound to the record the
    # page read and to a request identity, through the Brain's one writer.
    # The report and the card are never written here.
    kind = "review-" + req.path.rsplit("/", 1)[-1]
    try:
        result = brainwrite.write(kind, req.body)
    except brainwrite.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    state.broadcast({"type": "brain"})
    req.reply(200, {"operation": result})


def post_brain_seed_preflight(req) -> None:
    # What Generate draft would launch, read and spend for an inspected
    # seed manifest (task 193): harness, model, profile, exact reads,
    # policy, template, destinations, enforced capabilities, budget and the
    # approval Generate must name. A read with a body, like Inspect: it
    # re-inspects and resolves; it pins, launches and writes nothing.
    try:
        req.reply(200, {"preflight": braindraft.seed_preflight(req.body)})
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_brain_seed_generate(req) -> None:
    # Generate draft (task 193): a seed request bound to the manifest and
    # the approval its preflight showed, and its first read-only round
    # launched through the Brain drafting machinery. Continue, Cancel and
    # Discard are the drafting routes; nothing here writes the Brain.
    try:
        result = braindraft.start_seed(req.body)
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    state.broadcast({"type": "brain"})
    req.reply(200, result)


def post_brain_seed_review(req) -> None:
    # The review of a seed draft (task 194) for the person's choices: per
    # section the target, the text before and after, citations, strength,
    # coverage and risks, the handbook before and after, and the preview
    # digest Apply must name. A read with a body: nothing is written.
    try:
        req.reply(200, {"review": braindraft.seed_review(req.body)})
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_brain_seed_apply(req) -> None:
    # Apply a reviewed seed draft (task 194): the selected sections and the
    # handbook's links as one all-or-none Brain batch, under one request
    # identity, held to the review the page showed.
    try:
        result = braindraft.seed_apply(req.body)
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, result)


def post_brain_evaluate_preflight(req) -> None:
    # What Evaluate classifications would read and ride for the selected
    # entries (task 196): each entry's classification, revision and
    # override, related cards, the harness, model, profile, sources, budget
    # and the approval Evaluate must name. A read with a body.
    try:
        req.reply(200, {"preflight": braindraft.evaluate_preflight(req.body)})
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_brain_evaluate_start(req) -> None:
    # Evaluate (task 196): one read-only round through the Brain drafting
    # machinery, bound to the approval its preflight showed. Continue,
    # Cancel and Discard are the drafting routes; nothing here writes.
    try:
        result = braindraft.start_evaluate(req.body)
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    state.broadcast({"type": "brain"})
    req.reply(200, result)


def post_brain_evaluate_review(req) -> None:
    # The review of an evaluation for the person's choices and corrections,
    # with the preview digest Apply must name. A read with a body.
    try:
        req.reply(200, {"review": braindraft.evaluate_review(req.body)})
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_brain_evaluate_apply(req) -> None:
    # Apply the reviewed classifications (task 196): one all-or-none Brain
    # batch of classification-only items, held to the review the page showed.
    try:
        result = braindraft.evaluate_apply(req.body)
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    req.reply(200, result)


def get_brain_reconcile(req) -> None:
    # The Brain reconciliation queue (task 197): the policy and what it
    # grants, the execution owner, the budget, and every entry's state with
    # its bound inputs and receipts. A read: nothing is queued or launched.
    req.reply(200, {"reconcile": brainreconcile.reading()})


def post_brain_reconcile_policy(req) -> None:
    # A person's explicit change of the reconciliation policy — the only
    # way it is turned on or given the evaluate or apply authority. Bound to
    # the revision the page read; launches nothing.
    try:
        req.reply(200, brainreconcile.set_policy(req.body))
    except brainreconcile.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def post_brain_reconcile_action(req) -> None:
    # Rebuild, Refresh sources, Retry or Cancel on the queue: one request
    # identity each; none launches anything itself.
    try:
        req.reply(200, brainreconcile.act(req.body))
    except brainreconcile.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


def get_brain_seed_compare(req) -> None:
    # A seed batch's remote conflict, side by side (task 194): this board's
    # text and the remote's, from the last fetch. Nothing is fetched,
    # merged or overwritten.
    try:
        req.reply(200, {"compare": brainwrite.compare(_q(req, "request"))})
    except brainwrite.Refused as exc:
        req.reply(exc.status, {"error": str(exc)})


def get_brain_draft_preflight(req) -> None:
    # What a Brain drafting Generate would launch and read (task 175): the
    # roots, the harness, model and profile, the source selection and its
    # exclusions, the budget. A read: it hashes and resolves; it pins,
    # launches and writes nothing.
    target = _q(req, "target") or None
    text = _q(req, "text")[:braindraft.MAX_REQUEST] or None
    try:
        req.reply(200, {"preflight": braindraft.preflight(target, text)})
    except brain.NotAnId as exc:
        req.reply(400, {"error": str(exc), "code": "not-an-id"})


def get_brain_drafts(req) -> None:
    # The Brain drafting requests this board holds, or one by id — public
    # state only: no log, no pinned path, no prompt. Reading launches nothing.
    draft_id = _q(req, "id")
    try:
        if draft_id:
            req.reply(200, {"draft": braindraft.get(draft_id)})
        else:
            req.reply(200, braindraft.listing())
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})


BRAIN_DRAFT_STEPS = {"start": braindraft.start, "continue": braindraft.continue_,
                     "cancel": braindraft.cancel, "discard": braindraft.discard,
                     "retarget": braindraft.retarget, "apply": braindraft.apply}


def post_brain_draft(req) -> None:
    # A person's Brain drafting step (task 175): Generate and Continue each
    # launch one bounded `brain` round; Cancel, Discard and Retarget launch
    # nothing; Apply writes one entry through brainwrite, held to the
    # destination revision and the cited sources. Each is bound to what the
    # page read and to a request identity.
    step = req.path.rsplit("/", 1)[-1]
    try:
        result = BRAIN_DRAFT_STEPS[step](req.body)
    except braindraft.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    state.broadcast({"type": "brain"})
    req.reply(200, result)


# ── POST: the board's own writes ───────────────────────────────────────


def post_move(req) -> None:
    payload = req.body
    # a phase card stands for cards this view no longer draws,
    # so it does not move while one of them has an agent in it
    # — the refusal names ‖ hold, which stops both
    phases.assert_not_working(payload["file"])
    # A scoped phase's member goes back to To Do or Backlog only while
    # nothing may be working it (task 160): a run, or an operation with no
    # settled receipt. The refusal says what to do instead; it cancels,
    # settles and rewinds nothing.
    held = phasescope.return_refusal(payload["file"], payload["from"],
                                     payload["to"])
    if held:
        req.reply(409, {"error": held, "code": "member-working",
                        "file": payload["file"]})
        return
    # A person's move into in-progress is the commitment work starts
    # from, so it is held to the card's required information (task 132):
    # refused, with the card where it was, while anything required is
    # blocked, unchecked or stale. Planning moves are never held.
    held = inputs.move_refusal(payload["file"], payload["from"], payload["to"])
    if held:
        req.reply(409, {"error": held, "code": "information",
                        "file": payload["file"], "view": "inputs"})
        return
    # The review handoff of a card whose agreed "Show the result" is
    # Required for handoff waits for that output (task 133). Requested and
    # Off never hold a move.
    held = results.handoff_refusal(payload["file"], payload["from"], payload["to"])
    if held:
        req.reply(409, {"error": held, "code": "demonstration",
                        "file": payload["file"], "view": "overview"})
        return
    task = taskfiles.move_task(payload["file"], payload["from"], payload["to"])
    if payload["to"] == "to-do" and payload["from"] != "to-do":
        # Entering to-do schedules one bounded check of the card's
        # information, on this board only: a replica that sees the move
        # through a sync retrieves nothing of its own.
        try:
            inputs.schedule(payload["file"], "to-do", why="entered to-do")
        except (OSError, ValueError):
            pass
    req.reply(200, {"task": task})


def post_events(req) -> None:
    # The reply may carry a progress reminder for the run that
    # sent the event; a bridge that knows the field hands it
    # back at the tool boundary, and one that does not ignores
    # it — which is the whole of the fallback.
    req.reply(200, {"ok": True, **events.ingest_event(req.body)})


def post_progress(req) -> None:
    # A run's own report of its work. Capped before it is parsed
    # (the route's `max_body`), then validated whole by
    # `progress.report`, which is the only thing that decides what
    # is kept.
    try:
        req.reply(200, progress.report(req.body))
    except progress.Refused as refused:
        req.reply(refused.status, {"error": refused.why,
                                   "code": refused.code})


def post_agent_start(req) -> None:
    payload = req.body
    # takeover: the second, deliberate click on a card someone
    # else holds — never the default a stale card face sends
    try:
        agent = agents.start_agent(payload["file"], payload["stage"],
                                   bool(payload.get("takeover")))
    except inputs.NotReady as exc:
        # named, so the page can open the card's Inputs tab (task 132)
        req.reply(409, {"error": str(exc), "code": "information",
                        "file": payload["file"], "view": "inputs"})
        return
    req.reply(200, {"agent": agent})


def post_agent_review(req) -> None:
    payload = req.body
    agent = agents.start_review(payload["file"], payload["stage"])
    req.reply(200, {"agent": agent})


def post_agent_review_pr(req) -> None:
    payload = req.body
    agent = agents.start_pr_review(payload["file"], payload["stage"])
    req.reply(200, {"agent": agent})


def post_agent_act_pr(req) -> None:
    payload = req.body
    try:
        agent = agents.start_pr_fix(payload["file"], payload["stage"])
    except inputs.NotReady as exc:
        req.reply(409, {"error": str(exc), "code": "information",
                        "file": payload["file"], "view": "inputs"})
        return
    req.reply(200, {"agent": agent})


def _phase_runner_retired(req) -> None:
    # ▸ run phase and its ‖ hold (task 188): the phase runner is retired.
    # Kept as routes so a page or a script from before the upgrade is told
    # the way forward rather than a bare 404; nothing is read or written.
    filename = str(req.body.get("file") or "the phase")
    req.reply(410, {"error": f"{filename}: {phases.RETIRED}",
                    "code": phases.Retired.code})


def post_phase_run(req) -> None:
    _phase_runner_retired(req)


def post_phase_stop(req) -> None:
    _phase_runner_retired(req)


def post_phase_add(req) -> None:
    payload = req.body
    # one line into the phase card, nothing into the card added:
    # membership lives in one place and joining one is not a move
    result = taskfiles.add_to_phase(payload["file"], payload["stage"],
                                    payload["phase"])
    state.record_board_event({
        "kind": "phase", "actor": "you", "file": result["phase"],
        "summary": f"{result['phase']} gained {result['entry']}"})
    state.broadcast({"type": "board"})
    req.reply(200, result)


def post_plan_replan(req) -> None:
    payload = req.body
    # ⌖ replan: the person's decision that retargets this
    # card's work. It records a new plan or refuses; it never
    # resets, rebases or removes anything.
    name = payload["file"]
    stage = payload.get("stage") or taskfiles.find_stage_of(name)
    result = plans.replan(name, stage or "")
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": name,
        "summary": f"{name} replanned — {plans.line(result['plan'])}"})
    state.broadcast({"type": "board"})
    req.reply(200, result)


def post_pr_open(req) -> None:
    payload = req.body
    req.reply(200, {"url": changes.open_pr_now(payload["file"])})


def post_pr_copilot(req) -> None:
    payload = req.body
    url = changes.request_copilot(payload["file"])
    req.reply(200, {"url": url})


def post_task_complete(req) -> None:
    payload = req.body
    # merge & clean up ends with a move to done/, so the same
    # guard: a phase whose member is still working would take
    # its branch to main without that member's work in it
    phases.assert_not_working(payload["file"], "merge it")
    req.reply(200, completion.complete_task(payload["file"], payload["from"]))


def post_task_complete_forget(req) -> None:
    # The explicit decision about an unfinished completion: a
    # person settled it by hand (or decided against it), so the
    # record goes. It touches no git and moves no card.
    payload = req.body
    name = str(payload["file"])
    if (not name.endswith(".md") or "/" in name or "\\" in name
            or not completion.forget_recovery(name)):
        raise ValueError(f"{name} has no unfinished completion")
    state.record_board_event({
        "kind": "agent", "actor": "you", "file": name,
        "summary": f"{name}: the unfinished-completion record was "
                   f"discarded — nothing in git was changed"})
    state.broadcast({"type": "board"})
    req.reply(200, {"forgotten": name})


def post_archive(req) -> None:
    payload = req.body
    # archiving is a move, so it takes the same guard — and it
    # is the likeliest one: a phase you have given up on is
    # exactly the one you would tidy away mid-run
    phases.assert_not_working(payload["file"], "archive it")
    result = taskfiles.archive_task(payload["file"], payload["from"])
    with state.LOCK:
        state.LAST_ARCHIVED = result
    state.record_board_event({
        "kind": "move", "file": result["file"], "from": result["from"],
        "to": "archive", "actor": "you",
        "summary": f"{result['file']} archived (from {result['from']}/) — ⌘Z brings it back"})
    # The reply is the authoritative confirmation the page paints from
    # without waiting for a whole-board read (task 128): the card that
    # left, the count it left behind, whether git recorded it, and the
    # board revision whose reading will contain it — the page's guard
    # against an older reading putting the card back.
    rev = state.broadcast({"type": "board"})
    req.reply(200, {**result, "rev": rev, "boot": state.BOOT,
                    "archivedCount": taskfiles.archived_count()})


def get_archive_all(req) -> None:
    # What Archive all would take from done/ now, and every card it would
    # leave with why (task 187) — or, with `request`, one batch's state.
    request = _q(req, "request")
    if request:
        batch = archiveall.get(request)
        if batch is None:
            req.reply(404, {"error": f"no Archive all {request} on this board"})
            return
        req.reply(200, {"batch": batch})
        return
    req.reply(200, archiveall.reading())


def post_archive_all(req) -> None:
    # The confirmed set, frozen by the page: every card is read again and
    # archived through `taskfiles.archive_task` on its own (task 187).
    payload = req.body
    batch, again = archiveall.start(payload.get("request"), payload.get("files"),
                                    payload.get("retryOf"))
    req.reply(200, {"batch": batch, "duplicate": again})


def post_unarchive(req) -> None:
    with state.LOCK:
        last = state.LAST_ARCHIVED
        state.LAST_ARCHIVED = None
    if not last:
        raise ValueError("nothing to unarchive — the undo covers the last archive this board made")
    if "batch" in last:
        # ⌘Z after Archive all brings that whole set back (task 187)
        result = archiveall.unarchive_batch(last["batch"])
        names = ", ".join(r["file"] for r in result["batch"]) or "nothing"
        refused = (f"; {len(result['refused'])} could not be"
                   if result["refused"] else "")
        state.record_board_event({
            "kind": "move", "from": "archive", "to": "done", "actor": "you",
            "summary": f"Archive all undone: {len(result['batch'])} brought "
                       f"back to done/ ({names}){refused}"})
        rev = state.broadcast({"type": "board"})
        req.reply(200, {**result, "rev": rev, "boot": state.BOOT,
                        "archivedCount": taskfiles.archived_count()})
        return
    result = taskfiles.unarchive_task(last["file"], last["from"])
    state.record_board_event({
        "kind": "move", "file": result["file"], "from": "archive",
        "to": result["to"], "actor": "you",
        "summary": f"{result['file']} brought back to {result['to']}/"})
    rev = state.broadcast({"type": "board"})
    req.reply(200, {**result, "rev": rev, "boot": state.BOOT,
                    "archivedCount": taskfiles.archived_count()})


def post_command_run(req) -> None:
    payload = req.body
    req.reply(200, commands.run(payload["name"], payload["file"]))


def post_drive_start(req) -> None:
    payload = req.body
    req.reply(200, {"drive": drive.start(payload["file"])})


def post_drive_stop(req) -> None:
    req.reply(200, {"drive": drive.stop()})


def post_verify(req) -> None:
    payload = req.body
    # The one door a verification comes through — the shim a
    # run invokes and the board's own ↻ verify alike. It runs
    # the check and returns the evidence, so the caller is
    # told what was recorded rather than what it hoped.
    #
    # `agent` is a run asking, and then the frozen manifest is
    # the policy: what that run was granted, not what the
    # document says now. Without one it is a person's, and the
    # profile resolves fresh. Neither can name a check the
    # profile does not hold.
    #
    # An equal, current pass already on record is returned
    # rather than run again (`reused` names it); `again` is
    # the explicit request for a new run.
    found = verify.request(
        check=payload["check"],
        task=payload.get("file"),
        agent=payload.get("agent") or None,
        scope=payload.get("scope") or None,
        intent=payload.get("intent") or None,
        stage=payload.get("stage") or None,
        actor="agent" if payload.get("agent") else "you",
        again=payload.get("again") is True)
    reply = {"evidence": verify.public(found)}
    # A run that asked and did not get a pass is handed the
    # failure's bounded, scrubbed details in the same reply —
    # under the same access contract as `/api/verify/details`,
    # so a log path it may not read is never the only answer.
    details = run_diagnostic(payload, (found or {}).get("id"),
                             count=False) \
        if payload.get("agent") and (found or {}).get("outcome") \
        not in ("passed", None) else None
    if details is not None and "refused" in details:
        reply["diagnosticUnavailable"] = details["refused"]
    elif details is not None:
        reply["diagnostic"] = details
    req.reply(200, reply)


def post_verify_details(req) -> None:
    # A run's own failure details, again, later in the run.
    # The argument is an evidence id, the run is proved by its
    # token, and its card and checks come from its frozen
    # manifest — no path, no task a caller may widen.
    payload = req.body
    try:
        req.reply(200, {"diagnostic": run_diagnostic(
            payload, payload.get("evidence"))})
    except (verify.DiagnosticRefused, progress.Refused) as refused:
        req.reply(refused.status, {"error": refused.why,
                                   "code": refused.code})


def post_doctor_run(req) -> None:
    payload = req.body
    # The active half, and a POST because each named mode does
    # something: the adapter's own executables, real processes
    # at a fixture bench makes and removes, or — with its own
    # approval and never without — the installed CLI with a
    # model. Nothing here moves a card, touches a branch,
    # publishes anything or changes a setting.
    req.reply(200, {"doctor": doctor_payload(
        [str(m) for m in (payload.get("modes") or [])],
        approve=bool(payload.get("approve")), asked=True)})


def post_gates_override(req) -> None:
    payload = req.body
    # Proceeding over an unmet gate, on the record. The actor
    # is this checkout's git identity rather than anything a
    # request may claim — an override names a person, and a
    # person is not a field a browser fills in. The head the
    # confirmation was shown is sent back with it, so a sheet
    # that has gone stale is refused rather than authorising
    # a commit nobody looked at. A request naming no head is
    # refused outright: without one, a direct call could
    # authorise whatever the branch happens to hold by the time
    # it lands, which is the stale-page case with the check
    # removed.
    seen = str(payload.get("head") or "").strip()
    if not seen:
        req.reply(400, {"error": (
            "an override names the commit it was confirmed for, "
            "and this request names none — there is no "
            "implementation commit to override at, so nothing "
            "was recorded. Read the gate again.")})
        return
    result = gates.override(
        payload["file"], payload["scope"],
        actor=taskfiles.actor_name() or "",
        reason=str(payload.get("reason") or ""),
        head=seen)
    req.reply(200, {
        "override": result["override"],
        "duplicate": result["duplicate"],
        "gate": gates.public(result["decision"])})


def post_setup(req) -> None:
    # The setup guide's two mutations, behind the same door as
    # a policy write: they write the work state's shared setup
    # record (and, for a task, a card). Neither configures,
    # runs a check or enables a gate — those stay in Settings.
    payload = req.body
    try:
        result = (setupguide.decide(payload)
                  if req.path == "/api/setup/decide"
                  else setupguide.create_task(payload))
    except setupguide.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    if not result.get("duplicate"):
        state.record_board_event({
            "kind": "other", "actor": "you",
            "summary": (f"setup: {result['area']} — "
                        + (result.get("decision") or
                           f"setup card {result.get('filename')}"
                           " proposed in backlog/"))})
        state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def get_orchestration(req) -> None:
    # The orchestrator's public effective state (task 137): the
    # agreement in force against the draft, every scope's authority
    # and capacity with provenance, who executes. A read that stays
    # one — it launches no agent, runs no check and writes nothing.
    # The schedule (task 141) is the scheduler's plan as it stands, read
    # the same way: no pass runs, nothing is reserved or acquired.
    board = taskfiles.collect()
    req.reply(200, {"orchestration": orchestrator.reading(board),
                    "schedule": scheduler.projection(board)})


def get_orchestration_reasoning(req) -> None:
    # Orchestrator reasoning jobs (task 139): status, usage, the validated
    # public response or its diagnostics, and provenance — for one card, or
    # the newest per card. A read: it launches no reasoning run, however
    # often it is asked; only a concrete need does (`reasoning.request`).
    name = _q(req, "file")
    if name:
        req.reply(200, {"reasoning": reasoning.for_card(name),
                        "readiness": reasoning.readiness()})
    else:
        req.reply(200, {"reasoning": reasoning.board(),
                        "readiness": reasoning.readiness()})


def _trace_links(entry: dict) -> dict:
    """What each decision in a trace entry links to *now*: the operation
    its result names (its current status in the ledger) and the recovery
    incident with its reasoning attempts. Read beside the record, never
    folded into it — the record says what the pass saw."""
    ops, incidents = {}, {}
    by_id = {i.get("id"): i for i in recovery.all_incidents()}
    for d in entry.get("decisions") or []:
        result = d.get("result") or {}
        key = result.get("key") or result.get("operation")
        if key and key not in ops and operations.KEY_RE.match(str(key)):
            op = operations.read(key)
            ops[key] = operations.public(op) if op else None
        inc = result.get("incident")
        if inc and inc not in incidents:
            found = by_id.get(inc)
            incidents[inc] = recovery.public(found) if found else None
    return {"operations": ops, "incidents": incidents}


def get_orchestration_trace(req) -> None:
    # The decision trace (task 184): every scheduling pass, newest first,
    # filtered and paged by a seq cursor. A read of this installation's own
    # files: it launches, reserves, writes and fetches nothing.
    before = _q(req, "before")
    try:
        found = decisiontrace.page(
            card=_q(req, "card") or None, scope=_q(req, "scope") or None,
            outcome=_q(req, "outcome") or None, action=_q(req, "action") or None,
            since=_q(req, "since") or None, until=_q(req, "until") or None,
            before=int(before) if before else None,
            limit=int(_q(req, "limit") or 25))
    except ValueError as exc:
        req.reply(400, {"error": str(exc)})
        return
    req.reply(200, {"trace": found})


def get_orchestration_trace_entry(req) -> None:
    try:
        found = decisiontrace.entry(_q(req, "id"))
    except ValueError as exc:
        req.reply(400, {"error": str(exc)})
        return
    if found is None:
        req.reply(404, {"error": "no trace entry with that id on this "
                                 "installation — it may have been pruned by "
                                 "retention, or recorded by another board"})
        return
    req.reply(200, {"entry": found, "links": _trace_links(found),
                    "meta": decisiontrace.meta()})


def _orchestration_refused(req, exc) -> None:
    extra = dict(exc.extra)
    if isinstance(exc, execowner.Refused):
        extra["code"] = exc.code
    req.reply(exc.status, {"error": str(exc), **extra})


def post_orchestration_agreement(req) -> None:
    # Draft, validate, preview, apply and discard an operating
    # agreement. Only apply changes what is in force, bound to the
    # draft and effective revisions the page read. No card, branch,
    # worktree or run is touched by any of them.
    body, action = req.body, req.path.rsplit("/", 1)[1]
    actor = taskfiles.actor_name() or None
    try:
        if action == "validate":
            result = agreements.validate(body.get("document"))
        elif action == "draft":
            document = body.get("document")
            if document == "new":
                document = agreements.new_draft()
            result = agreements.save_draft(document, body.get("expected"))
        elif action == "preview":
            result = agreements.preview(body.get("document"))
        elif action == "discard":
            result = agreements.discard_draft(body.get("expected"))
        elif action == "migrate":
            # task 182: legacy phase reservations to shared capacity, an
            # explicit person's act bound to the revision the page read.
            # `"apply": false` previews and writes nothing.
            keep = bool(body.get("keepLimits"))
            defaults = body.get("scopeDefaults")
            if not body.get("apply"):
                result = agreements.migration_preview(keep, defaults)
                result["queue"] = orchestrator.migration_queue(result)
            else:
                result = agreements.migrate(body.get("expected"), keep,
                                            defaults, actor)
                state.record_board_event({
                    "kind": "other", "actor": "you",
                    "summary": f"orchestration: agreement migrated to shared "
                               f"capacity ({result['revision']})"})
                state.broadcast({"type": "board"})
        else:
            result = agreements.apply(body.get("expected"),
                                      body.get("draft"), actor)
            state.record_board_event({
                "kind": "other", "actor": "you",
                "summary": f"orchestration: agreement {result['revision']} "
                           f"applied"})
            state.broadcast({"type": "board"})
    except agreements.Refused as exc:
        _orchestration_refused(req, exc)
        return
    req.reply(200, {"result": result})


def post_orchestration_scope(req) -> None:
    # A person's pause, resume, exclusion or hand-back on one scope.
    # A pause stops new scheduling; work in flight is observed, never
    # cancelled. Hand-back leaves the card's branch and worktree as
    # they are.
    body = req.body
    try:
        result = agreements.set_scope(
            str(body.get("scope") or ""), str(body.get("action") or ""),
            body.get("expected"), body.get("card"),
            taskfiles.actor_name() or None)
    except agreements.Refused as exc:
        _orchestration_refused(req, exc)
        return
    state.record_board_event({
        "kind": "other", "actor": "you",
        "summary": f"orchestration: {result['action']} {result['scope']}"
                   + (f" card {result['card']}" if result.get("card") else "")})
    state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_orchestration_installation(req) -> None:
    # Designate this installation, hand execution over after
    # establishing quiescence, or adopt a working card after an
    # adoption. No failover: nothing here reads a heartbeat.
    body, action = req.body, str(req.body.get("action") or "")
    actor = taskfiles.actor_name() or None
    try:
        if action == "designate":
            result = execowner.designate(body.get("expected"), actor)
        elif action == "handover":
            result = orchestrator.handover(body.get("expected"), actor)
        elif action == "adopt-card":
            result = execowner.adopt_card(body.get("expected"),
                                          str(body.get("card") or ""), actor)
        else:
            req.reply(400, {"error": "action is designate, handover or adopt-card"})
            return
    except execowner.Refused as exc:
        _orchestration_refused(req, exc)
        return
    state.record_board_event({"kind": "other", "actor": "you",
                              "summary": f"orchestration: {action}"})
    state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_orchestration_phase(req) -> None:
    # Task 188: adoption and release are retired (every phase is its
    # scope's), and both answer 410 with the way forward. What remains is
    # a person's `resume` of a phase the one-time upgrade held because it
    # was halted or held under the retired runner: the decision the old
    # ▸ run phase made, made once, here.
    body, action = req.body, str(req.body.get("action") or "")
    card = str(body.get("card") or "")
    if action not in ("adopt", "release", "resume"):
        req.reply(400, {"error": "action is resume"})
        return
    try:
        result = (phasescope.resume if action == "resume" else
                  phasescope.adopt if action == "adopt" else
                  phasescope.release)(card, taskfiles.actor_name() or None)
    except phasescope.Refused as exc:
        req.reply(exc.status, {"error": str(exc), "code": exc.code, **exc.extra})
        return
    req.reply(200, {"result": result})


def post_orchestration_operation(req) -> None:
    # A person settles an operation the scheduler held (task 141): an
    # unknown outcome was in fact launched or not, or a refused launch is
    # released to be tried again. Resolving launches nothing.
    body = req.body
    try:
        result = scheduler.resolve(str(body.get("operation") or ""),
                                   str(body.get("decision") or ""),
                                   taskfiles.actor_name() or None)
    except LookupError as exc:
        req.reply(404, {"error": str(exc)})
        return
    except ValueError as exc:
        req.reply(409, {"error": str(exc)})
        return
    state.record_board_event({
        "kind": "other", "actor": "you",
        "summary": f"orchestration: operation {result['key']} settled "
                   f"({body.get('decision')})"})
    state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_orchestration_recovery(req) -> None:
    # A person's two answers to what recovery held (task 144): an incident
    # is handled (`resolve`), or the circuit breaker may close
    # (`reset-breaker`). Neither launches, stops or retries anything.
    body = req.body
    action = str(body.get("action") or "")
    who = taskfiles.actor_name() or None
    try:
        if action == "resolve":
            result = recovery.resolve(str(body.get("incident") or ""), who)
        elif action == "reset-breaker":
            result = recovery.breaker_reset(who)
        else:
            raise ValueError("the action is resolve or reset-breaker")
    except LookupError as exc:
        req.reply(404, {"error": str(exc)})
        return
    except ValueError as exc:
        req.reply(409, {"error": str(exc)})
        return
    state.record_board_event({
        "kind": "other", "actor": "you",
        "summary": f"orchestration: recovery {action}"
                   + (f" {body.get('incident')}" if action == "resolve" else "")})
    state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_setup_cards(req) -> None:
    # Gap-specific setup cards (task 89), behind the same door.
    # Preview writes nothing; create writes cards and links and
    # starts nothing; start is its own deliberate request that
    # rides ▸ start work and refuses before anything moves.
    payload = req.body
    action = req.path.rsplit("/", 1)[1]
    try:
        result = {"preview": setuptasks.preview,
                  "create": setuptasks.create,
                  "decide": setuptasks.decide,
                  "start": setuptasks.start}[action](payload)
    except setupguide.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    if action != "preview" and not result.get("duplicate"):
        summary = {
            "create": lambda r: "setup cards "
            + ", ".join(c["filename"] for c in
                        r["created"] + r["reused"])
            + " in backlog/ — nothing started",
            "decide": lambda r: f"setup gap {r['gap']} — {r['decision']}",
            "start": lambda r: f"setup card {r['filename']} started",
        }[action](result)
        state.record_board_event({"kind": "other", "actor": "you",
                                  "summary": f"setup: {summary}"})
        state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_preview(req) -> None:
    # The application preview's five acts (task 91), behind the
    # policy door: two of them run this project's own code, and a
    # page on another origin must not be able to ask for that.
    # Validate writes nothing; configure writes one machine-local
    # file and runs nothing; prepare and start each carry their
    # own "approve" and refuse without it.
    payload = req.body
    action = req.path.rsplit("/", 1)[1]
    try:
        result = {"validate": previews.check_draft,
                  "configure": previews.configure,
                  "prepare": previews.prepare,
                  "cancel": previews.cancel,
                  "start": previews.start,
                  "check": previews.check,
                  "stop": previews.stop}[action](payload)
    except setupguide.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    except ValueError as exc:
        req.reply(409, {"error": str(exc)})
        return
    if action == "configure":
        state.record_board_event({
            "kind": "other", "actor": "you",
            "summary": "setup: preview configuration saved — "
                       "nothing was run"})
        state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_setup_verification(req) -> None:
    # Verification readiness (task 90), behind the same door as
    # a policy write. Register and activate return a draft
    # through Settings' own validate and preview and write
    # nothing — Settings' armed Apply is the only writer of the
    # policy. Review writes one portable record; it runs
    # nothing and changes no gate.
    payload = req.body
    action = req.path.rsplit("/", 1)[1]
    try:
        result = {"register": readiness.register,
                  "review": readiness.review,
                  "activate": readiness.activate}[action](payload)
    except setupguide.Refused as exc:
        req.reply(exc.status, {"error": str(exc), **exc.extra})
        return
    except ValueError as exc:
        req.reply(400, {"error": str(exc)})
        return
    if action == "review" and not result.get("duplicate"):
        state.record_board_event({
            "kind": "other", "actor": "you",
            "summary": (f"setup: verification check "
                        f"{result['check']} reviewed — ready; no "
                        f"merge rule changed")})
        state.broadcast({"type": "board"})
    req.reply(200, {"result": result})


def post_settings(req) -> None:
    # Three steps, three requests, on purpose. Validate and
    # preview write nothing at all; only apply writes, and it
    # writes one file over the exact revision the editor was
    # shown. All three go through the policy door, the
    # read-only two included — a request this server will not
    # act on should not be parsed differently depending on
    # which of them it is.
    payload = req.body
    if req.path == "/api/settings/validate":
        req.reply(200, {"validation": settings.validate(payload)})
    elif req.path == "/api/settings/preview":
        req.reply(200, {"preview": settings.preview(payload)})
    else:
        try:
            result = settings.apply(
                payload, actor=taskfiles.actor_name() or None)
        except settings.Conflict as exc:
            # Its own status, because the answer is different
            # from a refused document: nothing is wrong with
            # what was sent, a newer save landed first, and the
            # editor has to re-read before it can apply.
            req.reply(409, {"error": str(exc),
                            "conflict": True,
                            "current": exc.current})
            return
        # A render, not an automation. The page reloads the
        # settings; nothing here moves a card, starts a run or
        # advances anything that was waiting.
        # `other`, deliberately: a configuration change is not
        # an agent, a move or a phase, and filing it under one
        # of those would put the wrong glyph on the ticker.
        state.record_board_event({
            "kind": "other", "actor": "you",
            "summary": (f"settings: {result['identity']} written "
                        f"({', '.join(result['history']['changedKeys']) or 'no key changed'})"
                        f" — applies at the next launch")})
        state.broadcast({"type": "board"})
        req.reply(200, {"applied": result})


def post_settings_agents(req) -> None:
    # Settings → Agents (task 77): the same door as the policy
    # editor, the read-only steps included. The payload names
    # agent keys and template names from fixed lists — never a
    # path — and `agentsettings` refuses anything else.
    payload = req.body
    if req.path.endswith("/validate"):
        req.reply(200, {"validation": agentsettings.validate(payload)})
    elif req.path.endswith("/preview"):
        req.reply(200, {"preview": agentsettings.preview(payload)})
    elif req.path.endswith("/render"):
        req.reply(200, {"render": agentsettings.render(payload)})
    else:
        try:
            result = agentsettings.apply(
                payload, actor=taskfiles.actor_name() or None)
        except agentsettings.Conflict as exc:
            req.reply(409, {"error": str(exc), "conflict": True,
                            "current": exc.current})
            return
        if not result.get("unchanged"):
            what = result["changedKeys"] + [
                t["template"] for t in result["templates"]]
            state.record_board_event({
                "kind": "other", "actor": "you",
                "summary": (f"settings: agents — {', '.join(what)} "
                            f"changed; applies at the next launch")})
            state.broadcast({"type": "board"})
        req.reply(200, {"applied": result})


def post_card_comment(req) -> None:
    # Add comment: written to the card, and nothing else — no
    # agent is asked anything because a comment exists.
    # `review` links it to one relevance review on this card
    # (task 84); `request` makes a repeated click one comment.
    payload = req.body
    review = payload.get("review")
    req.reply(200, {"comment": refine.comment(
        str(payload["file"]), str(payload["stage"]), payload.get("body"),
        review=str(review) if review else None,
        request_key=str(payload.get("request") or "") or None)})


def post_card_refine(req) -> None:
    # Update card with agent: one refinement of one comment —
    # a new one (`text`) or one already on the card
    # (`comment`). `request` is the page's identity for the
    # click, so a repeat answers with the job it already made.
    # `review` asks from one relevance review on this card.
    payload = req.body
    comment_id = payload.get("comment")
    text = payload.get("text")
    review = payload.get("review")
    req.reply(200, refine.request(
        str(payload["file"]), str(payload["stage"]),
        comment_id=str(comment_id) if comment_id else None,
        text=text if text is not None else None,
        request_key=str(payload.get("request") or "") or None,
        retry=bool(payload.get("retry")),
        review_id=str(review) if review else None))


def post_card_refine_decision(req) -> None:
    payload = req.body
    job = str(payload["id"])
    try:
        if req.path.endswith("/apply"):
            req.reply(200, refine.apply(job))
        elif req.path.endswith("/discard"):
            req.reply(200, refine.discard(job))
        else:
            req.reply(200, {"job": refine.cancel(job)})
    except taskfiles.StaleCard as exc:
        # its own flag, because the remedy is different: the
        # proposal is fine, the card moved on, and the answer
        # is to regenerate rather than to fix anything
        req.reply(409, {"error": str(exc), "stale": True})


def post_card_acceptance(req) -> None:
    # The three writes acceptance has, each a person's explicit
    # act, each bound to what that person was shown. The actor
    # is this board's identity, never a field in the body.
    payload = req.body
    path = req.path
    name, stage = str(payload["file"]), str(payload["stage"])
    try:
        if path.endswith("/track"):
            result = acceptance.track(
                name, stage, brief_sha=str(payload.get("briefSha") or ""),
                request=payload.get("request"))
            said = (f"{name}: acceptance tracked — "
                    f"{len(result['tracked'])} criteria given ids")
        elif path.endswith("/decide"):
            result = acceptance.decide(
                name, stage, criterion=str(payload.get("criterion") or ""),
                state=str(payload.get("state") or ""),
                rev=str(payload.get("rev") or ""),
                basis=payload.get("basis") or None,
                note=payload.get("note"), ref=payload.get("ref"),
                evidence=payload.get("evidence") or None,
                request=payload.get("request"))
            said = (f"{name}: criterion {payload.get('criterion')} "
                    f"recorded {payload.get('state')}")
        else:
            result = acceptance.finding(
                name, stage, event=str(payload.get("event") or ""),
                finding_id=payload.get("finding") or None,
                text=payload.get("text"),
                round_id=payload.get("round") or None,
                criterion=payload.get("criterion") or None,
                note=payload.get("note"),
                evidence=payload.get("evidence") or None,
                basis=payload.get("basis") or None,
                request=payload.get("request"))
            said = (f"{name}: review finding "
                    f"{payload.get('finding') or 'new'} {payload.get('event')}")
    except taskfiles.StaleCard as exc:
        req.reply(409, {"error": str(exc), "stale": True})
        return
    if result.get("written"):
        who = acceptance.actor()["name"] or "you"
        state.record_board_event({
            "kind": "agent", "actor": "you", "file": name,
            "summary": f"{said} by {who}" + (
                "" if result.get("committed") else
                " (written to the card, not committed)")})
        state.broadcast({"type": "board"})
    req.reply(200, result)


def get_card_result(req) -> None:
    # One card's Show the result, read now: the request in its brief, the
    # outputs kept for it with what each is and how far it can be trusted,
    # the checks bench ran, acceptance counts and what is known about
    # merged, installed and deployed. Passive — no capture, no check, no
    # checkout and no agent.
    name = _q(req, "file")
    stage = _q(req, "stage") or taskfiles.find_stage_of(name) or ""
    try:
        req.reply(200, results.reading(name, stage))
    except FileNotFoundError:
        req.reply(404, {"error": f"{name} is not in {stage}/"})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


# How an artifact's bytes go out. Never as a page of this board: every
# answer is nosniff and sandboxed, an image is drawn inline, text and JSON
# are sent as plain text, and anything else — HTML, SVG, scripts, unknown
# bytes — is an attachment typed application/octet-stream.
ARTIFACT_POLICY = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                   "sandbox")


def get_artifact(req) -> None:
    card, artifact = _q(req, "card"), _q(req, "id")
    # a verifier report's artifact (task 135) is kept apart from the
    # card's demonstrations, and served by the same rules
    purpose = artifacts.DIAGNOSTIC if _q(req, "purpose") == "diagnostic" else None
    try:
        record, data = artifacts.content(card, artifact, purpose)
    except artifacts.Refused as exc:
        req.send(404, str(exc).encode("utf-8"), "text/plain; charset=utf-8",
                 {"X-Content-Type-Options": "nosniff"})
        return
    headers = {"X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": ARTIFACT_POLICY,
               "Cross-Origin-Resource-Policy": "same-origin",
               "Referrer-Policy": "no-referrer"}
    download = _q(req, "download") == "1"
    kind = record.get("kind")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(record.get("name") or "artifact"))
    if kind == "image" and not download:
        mime = record.get("mime") or "application/octet-stream"
        headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
    elif kind in ("text", "json") and not download:
        mime = "text/plain; charset=utf-8"
        headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
    else:
        mime = "application/octet-stream"
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    req.send(200, data, mime, headers)


def post_card_result(req) -> None:
    # A person's choices about Show the result: the setting written into
    # the brief (bound to the brief they saw), "Not now" on the suggestion
    # (this board only, nothing written to the card), the preview of what
    # asking after the fact would mean, and withholding one output.
    payload = req.body
    path = req.path
    name = str(payload.get("file") or "")
    stage = str(payload.get("stage") or "")
    try:
        if path.endswith("/setting"):
            result = results.write_setting(
                name, stage, brief_sha=str(payload.get("briefSha") or ""),
                mode=str(payload.get("mode") or ""),
                want=str(payload.get("want") or ""),
                expects=str(payload.get("expects") or "any"),
                agree=payload.get("agree") is True)
            run = results.active_run(name)
            if run:
                result["activeRun"] = run
                result["note"] = ("the run in flight keeps the instructions and "
                                  "authority it launched with; this reaches the "
                                  "next run")
            said = f"{name}: Show the result set to {result['mode']}"
        elif path.endswith("/dismiss"):
            result = results.dismiss(name)
            said = None
        elif path.endswith("/preview"):
            req.reply(200, results.request_preview(name, stage))
            return
        else:
            who = taskfiles.actor_name() or "you"
            # a verifier report's kept files are withheld the same way as
            # a demonstration's, named by the same purpose the read takes
            diagnostic = payload.get("purpose") == "diagnostic"
            record = artifacts.withhold(
                name, str(payload.get("id") or ""), actor=who,
                reason=str(payload.get("reason") or ""),
                purpose=artifacts.DIAGNOSTIC if diagnostic else None)
            result = {"artifact": artifacts.public(record)}
            said = (f"{name}: a verifier report's artifact was withheld by {who}"
                    if diagnostic else f"{name}: an output was withheld by {who}")
    except taskfiles.StaleCard as exc:
        req.reply(409, {"error": str(exc), "stale": True})
        return
    except FileNotFoundError:
        req.reply(404, {"error": f"{name} is not in {stage}/"})
        return
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})
        return
    if said:
        state.record_board_event({"kind": "card", "actor": "you", "file": name,
                                  "summary": said})
        state.broadcast({"type": "board"})
    req.reply(200, result)


def post_verify_cancel(req) -> None:
    payload = req.body
    req.reply(200, verify.cancel(payload["id"]))


def post_verify_required(req) -> None:
    # Run required checks, and stopping it: a person's click,
    # through the same door as a policy write, because it runs
    # the project's programs. Nothing in the body makes the
    # request a run's — a body naming one is refused, since a
    # run asks for its own checks through bench-verify under
    # the profile it was launched with. The head the
    # confirmation was shown is sent back, so a stale sheet
    # is refused rather than requesting checks about a commit
    # nobody looked at.
    payload = req.body
    try:
        if req.path.endswith("/cancel"):
            found = required.cancel(payload["file"])
        else:
            found = required.start(
                payload["file"], payload.get("stage") or None,
                head=str(payload.get("head") or "") or None,
                agent=payload.get("agent") or None)
    except required.Refused as exc:
        req.reply(exc.status, {"error": str(exc),
                               "code": exc.code})
        return
    req.reply(200, {"batch": found})


def post_card_delivery_check_remote(req) -> None:
    # One bounded fetch of the card's own two branch names, on
    # a person's click. Viewing never does this.
    payload = req.body
    found = delivery.check_remote(payload["file"])
    req.reply(200, {"remote": found,
                    "delivery": delivery.for_card(
                        payload["file"], None,
                        phases.public_state())})


def post_card_stack_preview(req) -> None:
    # The confirmation sheet for one stack action (task 119): the
    # identities it is bound to, what it would do, what stops it and,
    # for a retarget bench may not make, the exact manual step. Writes
    # nothing; a retarget's sheet reads the CLI's own help text.
    payload = req.body
    req.reply(200, {"stack": stackactions.preview(
        payload["file"], payload.get("action") or "")})


def post_card_stack_action(req) -> None:
    # A person's confirmed stack action, bound to the identities the
    # sheet showed (`expected`) and refused, naming what moved, when
    # any differs. Nothing calls this but the click.
    payload = req.body
    found = stackactions.run(payload["file"], payload.get("action") or "",
                             payload.get("expected") or {})
    req.reply(200, {"result": found})


def get_card_integration(req) -> None:
    # What Update against target and verify would do for one review card
    # now (task 189): its head, its target, the accepted commits it would
    # bring in, and why it may not. A read: nothing fetched or merged.
    try:
        req.reply(200, {"integration": coordinate.update_preview(
            _q(req, "file"))})
    except (ValueError, OSError) as exc:
        req.reply(409, {"error": str(exc)})


def post_card_integration_update(req) -> None:
    # A person's confirmed Update against target and verify (task 189),
    # bound to the head the sheet showed. A clean update merges the
    # accepted target into the card's own branch and pushes it; a
    # conflict is aborted and handed to one ↻ act on PR run. Nothing is
    # merged into the target.
    payload = req.body
    head = payload.get("head")
    if not isinstance(head, str) or not head.strip():
        # a person's update is about the head the sheet showed; without one
        # it would update whatever the branch holds by the time it lands
        req.reply(400, {"error": "name the head the card showed (`head`) — "
                                 "nothing was updated",
                        "code": "head-required"})
        return
    try:
        found = coordinate.update_against_target(
            payload["file"], {"head": head.strip()}, actor="person")
    except agents.RepairRefused as exc:
        req.reply(409, {"error": str(exc), "code": exc.code})
        return
    except coordinate.UpdateRefused as exc:
        req.reply(409, {"error": str(exc), "code": exc.code})
        return
    req.reply(200, {"result": found})


def post_card_delivery_launch_check(req) -> None:
    # The launch's own preflight, asked explicitly and fresh.
    # It claims nothing and starts nothing; the launch asks
    # again when it is asked for.
    payload = req.body
    req.reply(200, {"launch": delivery.launch_check(
        payload["file"], payload.get("stage"),
        payload.get("intent") or "work",
        refusals=agents.launch_refusals,
        takeover=bool(payload.get("takeover")))})


def post_agent_stop(req) -> None:
    payload = req.body
    agent = agents.stop_agent(payload["id"])
    req.reply(200, {"agent": agent})


# ── the table ──────────────────────────────────────────────────────────
#
# One row per method and path. `tests/test_architecture.py` holds this to
# its snapshot, so a new route, or a route changing class, is a reviewed
# edit of both.

ROUTES = (
    Route("GET", "/", "read", get_page),
    Route("GET", "/index.html", "read", get_page),
    Route("GET", "/board.html", "read", get_page),
    # the page's own addresses (task 134): the shell, for exactly these
    Route("GET", "/board", "read", get_ui_page),
    Route("GET", "/brain", "read", get_ui_page),
    Route("GET", "/brain/*", "read", get_ui_page),
    Route("GET", "/cards/*", "read", get_ui_page),
    Route("GET", "/orchestrator", "read", get_ui_page),
    Route("GET", "/orchestrator/*", "read", get_ui_page),
    Route("GET", "/phases", "read", get_ui_page),
    Route("GET", "/phases/*", "read", get_ui_page),
    Route("GET", "/sessions", "read", get_ui_page),
    Route("GET", "/sessions/*", "read", get_ui_page),
    Route("GET", "/focus", "read", get_ui_page),
    Route("GET", "/focus/*", "read", get_ui_page),
    Route("GET", "/settings", "read", get_ui_page),
    Route("GET", "/settings/*", "read", get_ui_page),
    Route("GET", "/web/*", "read", get_web),
    Route("GET", "/api/tasks", "read", get_tasks),
    Route("GET", "/api/state", "read", get_state),
    Route("GET", "/api/session", "read", get_session),
    Route("GET", "/api/progress", "read", get_progress),
    Route("GET", "/api/plan", "read", get_plan),
    Route("GET", "/api/task/completion", "read", get_task_completion),
    Route("GET", "/api/profile", "read", get_profile),
    Route("GET", "/api/manifest", "read", get_manifest),
    Route("GET", "/api/evidence", "read", get_evidence),
    Route("GET", "/api/review", "read", get_review),
    Route("GET", "/api/gates", "read", get_gates),
    Route("GET", "/api/evidence/log", "read", get_evidence_log),
    Route("GET", "/api/verify/required", "read", get_verify_required),
    Route("GET", "/api/verify/checks", "read", get_verify_checks),
    Route("GET", "/api/doctor", "read", get_doctor),
    Route("GET", "/api/settings", "read", get_settings),
    Route("GET", "/api/orchestration", "read", get_orchestration),
    Route("GET", "/api/orchestration/reasoning", "read", get_orchestration_reasoning),
    Route("GET", "/api/orchestration/trace", "read", get_orchestration_trace),
    Route("GET", "/api/orchestration/trace/entry", "read",
          get_orchestration_trace_entry),
    Route("GET", "/api/setup", "read", get_setup),
    Route("GET", "/api/setup/verification", "read", get_setup_verification),
    Route("GET", "/api/preview", "read", get_preview),
    Route("GET", "/api/preview/log", "read", get_preview_log),
    Route("GET", "/api/card", "read", get_card),
    Route("GET", "/api/card/locate", "read", get_card_locate),
    Route("GET", "/api/archive/all", "read", get_archive_all),
    Route("GET", "/api/card/discussion", "read", get_card_discussion),
    Route("GET", "/api/card/acceptance", "read", get_card_acceptance),
    Route("GET", "/api/card/decisions", "read", get_card_decisions),
    Route("GET", "/api/ask", "read", get_ask),
    Route("GET", "/api/ask/thread", "read", get_ask_thread),
    Route("GET", "/api/ask/facts", "read", get_ask_facts),
    Route("GET", "/api/card/inputs", "read", get_card_inputs),
    Route("GET", "/api/card/result", "read", get_card_result),
    Route("GET", "/api/artifact", "read", get_artifact),
    Route("GET", "/api/card/delivery", "read", get_card_delivery),
    Route("GET", "/api/card/integration", "read", get_card_integration),
    Route("GET", "/api/card/timing", "read", get_card_timing),
    Route("GET", "/api/settings/history", "read", get_settings_history),
    Route("GET", "/api/diff", "read", get_diff),
    Route("GET", "/api/stream", "read", get_stream),
    Route("GET", "/files/*", "read", get_files),
    Route("GET", "/api/brain", "read", get_brain),
    Route("GET", "/api/brain/entry", "read", get_brain_entry),
    Route("GET", "/api/brain/revisions", "read", get_brain_revisions),
    Route("GET", "/api/brain/operation", "read", get_brain_operation),
    Route("GET", "/api/brain/reviews", "read", get_brain_reviews),
    Route("GET", "/api/brain/review", "read", get_brain_review),
    Route("GET", "/api/brain/handbook", "read", get_brain_handbook),
    Route("GET", "/api/brain/seed", "read", get_brain_seed),
    Route("GET", "/api/brain/seed/compare", "read", get_brain_seed_compare),
    Route("POST", "/api/brain/evaluate/preflight", "browser-mutation",
          post_brain_evaluate_preflight),
    Route("POST", "/api/brain/evaluate/start", "browser-mutation",
          post_brain_evaluate_start),
    Route("POST", "/api/brain/evaluate/review", "browser-mutation",
          post_brain_evaluate_review),
    Route("POST", "/api/brain/evaluate/apply", "browser-mutation",
          post_brain_evaluate_apply),
    Route("GET", "/api/brain/reconcile", "read", get_brain_reconcile),
    Route("POST", "/api/brain/reconcile/policy", "policy-mutation",
          post_brain_reconcile_policy),
    Route("POST", "/api/brain/reconcile/action", "browser-mutation",
          post_brain_reconcile_action),
    Route("GET", "/api/brain/adoption", "read", get_brain_adoption),
    Route("GET", "/api/brain/adoption/preview", "read", get_brain_adoption_preview),
    Route("GET", "/api/reports/filing", "read", get_report_filing),
    Route("GET", "/api/brain/draft/preflight", "read", get_brain_draft_preflight),
    Route("GET", "/api/brain/drafts", "read", get_brain_drafts),

    Route("POST", "/api/move", "browser-mutation", post_move),
    Route("POST", "/api/events", "event-ingest", post_events),
    Route("POST", "/api/progress", "run-authenticated", post_progress,
          max_body=progress.MAX_BODY),
    Route("POST", "/api/agent/start", "browser-mutation", post_agent_start),
    Route("POST", "/api/agent/review", "browser-mutation", post_agent_review),
    Route("POST", "/api/agent/review-pr", "browser-mutation", post_agent_review_pr),
    Route("POST", "/api/agent/act-pr", "browser-mutation", post_agent_act_pr),
    Route("POST", "/api/phase/run", "browser-mutation", post_phase_run),
    Route("POST", "/api/phase/stop", "browser-mutation", post_phase_stop),
    Route("POST", "/api/phase/add", "browser-mutation", post_phase_add),
    Route("POST", "/api/plan/replan", "browser-mutation", post_plan_replan),
    Route("POST", "/api/pr/open", "browser-mutation", post_pr_open),
    Route("POST", "/api/pr/copilot", "browser-mutation", post_pr_copilot),
    Route("POST", "/api/task/complete", "browser-mutation", post_task_complete),
    Route("POST", "/api/task/complete/forget", "browser-mutation", post_task_complete_forget),
    Route("POST", "/api/archive", "browser-mutation", post_archive),
    Route("POST", "/api/unarchive", "browser-mutation", post_unarchive),
    Route("POST", "/api/archive/all", "browser-mutation", post_archive_all),
    Route("POST", "/api/command/run", "browser-mutation", post_command_run),
    Route("POST", "/api/drive/start", "browser-mutation", post_drive_start),
    Route("POST", "/api/drive/stop", "browser-mutation", post_drive_stop),
    # A person's ↻ and a run's shim share this route, so its class is the
    # person's: the door refuses a stated foreign origin whatever the body
    # says, and refuses any browser request that names a run. A shim's
    # `agent` is not proved by the class — that is the run token (WP05).
    Route("POST", "/api/verify", "browser-mutation", post_verify),
    Route("POST", "/api/verify/details", "run-authenticated", post_verify_details),
    Route("POST", "/api/doctor/run", "browser-mutation", post_doctor_run),
    Route("POST", "/api/gates/override", "browser-mutation", post_gates_override),
    Route("POST", "/api/orchestration/agreement/validate", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/agreement/draft", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/agreement/preview", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/agreement/discard", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/agreement/apply", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/agreement/migrate", "policy-mutation",
          post_orchestration_agreement),
    Route("POST", "/api/orchestration/scope", "policy-mutation",
          post_orchestration_scope),
    Route("POST", "/api/orchestration/installation", "policy-mutation",
          post_orchestration_installation),
    Route("POST", "/api/orchestration/operation", "policy-mutation",
          post_orchestration_operation),
    Route("POST", "/api/orchestration/phase", "policy-mutation",
          post_orchestration_phase),
    Route("POST", "/api/orchestration/recovery", "policy-mutation",
          post_orchestration_recovery),
    Route("POST", "/api/setup/decide", "policy-mutation", post_setup),
    Route("POST", "/api/setup/task", "policy-mutation", post_setup),
    Route("POST", "/api/setup/cards/preview", "policy-mutation", post_setup_cards),
    Route("POST", "/api/setup/cards/create", "policy-mutation", post_setup_cards),
    Route("POST", "/api/setup/cards/decide", "policy-mutation", post_setup_cards),
    Route("POST", "/api/setup/cards/start", "policy-mutation", post_setup_cards),
    Route("POST", "/api/preview/validate", "policy-mutation", post_preview),
    Route("POST", "/api/preview/configure", "policy-mutation", post_preview),
    Route("POST", "/api/preview/prepare", "policy-mutation", post_preview),
    Route("POST", "/api/preview/cancel", "policy-mutation", post_preview),
    Route("POST", "/api/preview/start", "policy-mutation", post_preview),
    Route("POST", "/api/preview/check", "policy-mutation", post_preview),
    Route("POST", "/api/preview/stop", "policy-mutation", post_preview),
    Route("POST", "/api/setup/verification/register", "policy-mutation",
          post_setup_verification),
    Route("POST", "/api/setup/verification/review", "policy-mutation",
          post_setup_verification),
    Route("POST", "/api/setup/verification/activate", "policy-mutation",
          post_setup_verification),
    Route("POST", "/api/settings/validate", "policy-mutation", post_settings),
    Route("POST", "/api/settings/preview", "policy-mutation", post_settings),
    Route("POST", "/api/settings/apply", "policy-mutation", post_settings),
    Route("POST", "/api/settings/agents/validate", "policy-mutation",
          post_settings_agents),
    Route("POST", "/api/settings/agents/preview", "policy-mutation",
          post_settings_agents),
    Route("POST", "/api/settings/agents/render", "policy-mutation",
          post_settings_agents),
    Route("POST", "/api/settings/agents/apply", "policy-mutation",
          post_settings_agents),
    Route("POST", "/api/brain/create", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/edit", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/classify", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/override", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/retire", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/retry", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/handbook", "browser-mutation", post_brain_write),
    Route("POST", "/api/brain/adoption/apply", "browser-mutation", post_brain_adoption_apply),
    Route("POST", "/api/brain/seed/inspect", "browser-mutation", post_brain_seed_inspect),
    Route("POST", "/api/brain/seed/preflight", "browser-mutation", post_brain_seed_preflight),
    Route("POST", "/api/brain/seed/generate", "browser-mutation", post_brain_seed_generate),
    Route("POST", "/api/brain/seed/review", "browser-mutation", post_brain_seed_review),
    Route("POST", "/api/brain/seed/apply", "browser-mutation", post_brain_seed_apply),
    Route("POST", "/api/brain/review/classify", "browser-mutation", post_brain_review_write),
    Route("POST", "/api/brain/review/override", "browser-mutation", post_brain_review_write),
    Route("POST", "/api/brain/reviews/rebuild", "browser-mutation", post_brain_reviews_rebuild),
    Route("POST", "/api/reports/filing/retry", "browser-mutation", post_report_filing_retry),
    Route("POST", "/api/brain/draft/start", "browser-mutation", post_brain_draft),
    Route("POST", "/api/brain/draft/continue", "browser-mutation", post_brain_draft),
    Route("POST", "/api/brain/draft/cancel", "browser-mutation", post_brain_draft),
    Route("POST", "/api/brain/draft/discard", "browser-mutation", post_brain_draft),
    Route("POST", "/api/brain/draft/retarget", "browser-mutation", post_brain_draft),
    Route("POST", "/api/brain/draft/apply", "browser-mutation", post_brain_draft),
    Route("POST", "/api/card/comment", "browser-mutation", post_card_comment),
    Route("POST", "/api/card/refine", "browser-mutation", post_card_refine),
    Route("POST", "/api/card/refine/apply", "browser-mutation", post_card_refine_decision),
    Route("POST", "/api/card/refine/discard", "browser-mutation", post_card_refine_decision),
    Route("POST", "/api/card/refine/cancel", "browser-mutation", post_card_refine_decision),
    Route("POST", "/api/card/acceptance/track", "browser-mutation", post_card_acceptance),
    Route("POST", "/api/card/acceptance/decide", "browser-mutation", post_card_acceptance),
    Route("POST", "/api/card/finding", "browser-mutation", post_card_acceptance),
    Route("POST", "/api/card/decisions/answer", "browser-mutation", post_card_decisions),
    Route("POST", "/api/card/decisions/withdraw", "browser-mutation", post_card_decisions),
    Route("POST", "/api/ask", "browser-mutation", post_ask),
    Route("POST", "/api/ask/retry", "browser-mutation", post_ask),
    Route("POST", "/api/ask/cancel", "browser-mutation", post_ask),
    Route("POST", "/api/card/inputs/check", "browser-mutation", post_card_inputs),
    Route("POST", "/api/card/inputs/classify", "browser-mutation", post_card_inputs),
    Route("POST", "/api/card/result/setting", "browser-mutation", post_card_result),
    Route("POST", "/api/card/result/dismiss", "browser-mutation", post_card_result),
    Route("POST", "/api/card/result/preview", "browser-mutation", post_card_result),
    Route("POST", "/api/artifact/withhold", "browser-mutation", post_card_result),
    Route("POST", "/api/verify/cancel", "browser-mutation", post_verify_cancel),
    Route("POST", "/api/verify/required", "policy-mutation", post_verify_required),
    Route("POST", "/api/verify/required/cancel", "policy-mutation",
          post_verify_required),
    Route("POST", "/api/card/delivery/check-remote", "browser-mutation",
          post_card_delivery_check_remote),
    Route("POST", "/api/card/delivery/launch-check", "browser-mutation",
          post_card_delivery_launch_check),
    Route("POST", "/api/card/stack/preview", "browser-mutation",
          post_card_stack_preview),
    Route("POST", "/api/card/stack/action", "browser-mutation",
          post_card_stack_action),
    Route("POST", "/api/card/integration/update", "browser-mutation",
          post_card_integration_update),
    Route("POST", "/api/agent/stop", "browser-mutation", post_agent_stop),
)


def _index(routes) -> tuple[dict, list]:
    exact: dict[tuple[str, str], Route] = {}
    prefixes: list[Route] = []
    for route in routes:
        if route.klass not in CLASSES:
            raise ValueError(f"{route.method} {route.path}: unknown request "
                             f"class {route.klass!r}")
        if route.method == "GET" and route.klass != "read":
            raise ValueError(f"GET {route.path}: a GET is a read")
        if route.method == "POST" and route.klass == "read":
            raise ValueError(f"POST {route.path}: a POST is not a read")
        key = (route.method, route.path)
        if key in exact or any((p.method, p.path) == key for p in prefixes):
            raise ValueError(f"{route.method} {route.path} is declared twice")
        if route.path.endswith("*"):
            prefixes.append(route)
        else:
            exact[key] = route
    return exact, prefixes


_EXACT, _PREFIXES = _index(ROUTES)


def find(method: str, path: str) -> Route | None:
    """The route a request is for, or None."""
    found = _EXACT.get((method, path))
    if found is not None:
        return found
    for route in _PREFIXES:
        if route.method == method and path.startswith(route.path[:-1]):
            return route
    return None
