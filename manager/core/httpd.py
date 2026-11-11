"""HTTP surface: the page, the JSON API, and the SSE stream."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import time
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

import acceptance
import agents
import agentsettings
import commands
import config
import delivery
import doctor
import drive
import events
import gates
import github
import phases
import plans
import profiles
import progress
import readiness
import previews
import refine
import reviews
import settings
import setupguide
import setuptasks
import state
import sync
import taskfiles
import verify


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
    policy = profiles.gate_policy()
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
        "policy": profiles.gate_policy_public(policy),
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


# ── writing a policy is an authority boundary ──────────────────────────
#
# Everything else this server does is bookkeeping about work: a move, a
# launch, a verification request. Changing the execution policy is
# different in kind — it is what *decides* what a launch may do and what a
# merge must have — so the one mutation on this board that edits policy is
# held to a stricter door than the rest, and the door is checked in the
# server rather than in the page.
#
# The board binds to loopback and has no authentication, which is the
# whole threat model here: a page on another origin, opened in the same
# browser, can post to `http://127.0.0.1:26071` without ever reading a
# reply. Three things together refuse that, and none of them trusts a
# field in the body:
#
# - a JSON content type, so a cross-origin HTML form (which may only send
#   form encodings) is not a request this will parse;
# - a custom header, which a form cannot set at all and a cross-origin
#   `fetch` can only set by asking for a CORS preflight — and nothing
#   here answers `OPTIONS`, so the preflight fails and the real request
#   is never sent;
# - an `Origin`/`Sec-Fetch-Site` check, for the browsers that send them:
#   a stated origin that is not this board's is refused by name.
#
# A loopback client that is not a browser — `curl`, a script, a test —
# sends the header and is allowed, which is correct: it already has the
# operator's own filesystem access and could edit the file directly.

SETTINGS_HEADER = "X-Bench-Settings"
SETTINGS_TOKEN = "1"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def origin_problem(headers, port: int) -> str | None:
    """Why this request may not change policy, or None.

    Read from headers only. Nothing in the body may make a request
    trusted: that is what a forged post would fill in.
    """
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return (f"a policy change must be sent as application/json "
                f"(this was {ctype or 'nothing'}). Nothing was changed.")
    if (headers.get(SETTINGS_HEADER) or "").strip() != SETTINGS_TOKEN:
        return (f"a policy change must carry the {SETTINGS_HEADER}: "
                f"{SETTINGS_TOKEN} header. A cross-origin form cannot set it "
                f"and a cross-origin fetch cannot send it without a "
                f"preflight this board does not answer. Nothing was changed.")
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return (f"this request states Sec-Fetch-Site: {site}, so it did not "
                f"come from this board's own page. Nothing was changed.")
    origin = (headers.get("Origin") or "").strip()
    if origin and origin.lower() != "null":
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return (f"this request states Origin: {origin}, which is not this "
                    f"board. Nothing was changed.")
        if parsed.port and parsed.port != port:
            return (f"this request states Origin: {origin}, which is a "
                    f"different board on this machine. Nothing was changed.")
    return None


def state_payload() -> dict:
    with state.LOCK:
        sessions = sorted(
            (dict(m) for m in state.SESSIONS.values()),
            key=lambda m: m.get("last") or m.get("started") or 0, reverse=True)
        board_events = list(state.BOARD_EVENTS[-80:])
    collected = taskfiles.collect()
    return {
        "board": collected,
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
        "appBase": config.APP_BASE,
        "sessions": sessions,
        "agents": agents.list_public(),
        "prs": github.public_state(),
        # what the last pass of the phase runner saw: per phase card in
        # in-progress/, its branch, whether a run is in force or halted (and
        # why, and where), and each member's state — the header chip and the
        # phase card's own list are both read from here
        "phases": phases.public_state(),
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
        "gatePolicy": profiles.gate_policy_public(profiles.gate_policy()),
        # per card, the one delivery status every surface draws: the word,
        # its sentence, the primary action and who holds it, the
        # candidate commit's state and the required-only counts. Memoised
        # by a fingerprint of every input it reads — never authority; the
        # whole reading is `/api/card/delivery`, always recomputed.
        "delivery": delivery.board(collected, phases.public_state()),
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
        "branches": github.task_branches(),
        "commands": config.commands(),
        "commandRuns": commands.public(),
        # cards this board is midway through merging and cleaning up: the
        # busy state renders from here, not from what a tab happened to click
        "completing": state.completing_public(),
        # completions that merged and did not publish: what was merged,
        # into what, and why the remote does not hold it. A directory
        # listing, empty on a board where nothing failed, and it survives
        # a restart — the card wears it until a retry finishes the work
        "unpublished": github.recoveries(),
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
        "now": time.time(),
    }


_TITLE = re.compile(rb"<title>.*?</title>", re.DOTALL)


def page_bytes() -> bytes:
    """board.html with the project's name rendered into its <title>, so the
    tab reads right on first paint rather than after the first state load."""
    html = (config.CORE / "board.html").read_bytes()
    title = escape(f"{config.PROJECT} · bench").encode("utf-8")
    return _TITLE.sub(lambda _: b"<title>" + title + b"</title>", html, count=1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html", "/board.html"):
            if not (config.CORE / "board.html").is_file():
                self._send(500, b"board.html is missing", "text/plain")
                return
            self._send(200, page_bytes(), "text/html; charset=utf-8")
        elif path == "/api/tasks":
            self._json(200, taskfiles.collect())
        elif path == "/api/state":
            self._json(200, state_payload())
        elif path == "/api/session":
            sid = (parse_qs(url.query).get("id") or [""])[0]
            with state.LOCK:
                meta = dict(state.SESSIONS.get(sid) or {})
            self._json(200, {"meta": meta, "events": events.session_events(sid)})
        elif path == "/api/progress":
            # One run's progress whole — the current update, the milestone
            # checklist and the timeline — by agent id, or the most recent
            # run on a card by file. Read-only; agent-reported throughout.
            query = parse_qs(url.query)
            agent_id = (query.get("agent") or [""])[0]
            if not agent_id:
                latest = progress.board_public(
                    {(query.get("file") or [""])[0]}).values()
                agent_id = next((p["agent"] for p in latest), "")
            found = progress.public(agent_id, full=True) if agent_id else None
            self._json(200 if found else 404,
                       {"progress": found} if found else
                       {"error": "no progress recorded for that run"})
        elif path == "/api/plan":
            # The preflight itself, on demand: the whole resolved plan or
            # the reason it does not resolve, before anything is started.
            # Read-only — it fetches (bounded) and answers, and creates no
            # branch, no worktree and no run.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            try:
                self._json(200, {"plan": plans.preflight(name, stage)})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/task/completion":
            # What merge & clean up would do for this card — merge mode,
            # target, publication and any refusal — from the authorities
            # the completion itself reads, so the sheet describes the
            # completion that would run. Read-only: no fetch, no gh.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            try:
                if not name.endswith(".md") or "/" in name or not stage:
                    raise ValueError(f"no card {name!r} on this board")
                self._json(200, github.completion_facts(name, stage))
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/profile":
            # The profile preflight, on demand, for one card and one
            # launch intent: what that run would be allowed to do, which
            # files it would be given, and what stops it. Read-only — it
            # resolves and hashes, and pins nothing, so opening it before
            # a launch costs nothing and is the point.
            #
            # This is the *same* resolver the launch rides. The UI must
            # never grow a second settings parser that could disagree
            # with what a run was actually granted.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            intent = (query.get("intent") or ["work"])[0]
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
                self._json(200, {"profile": profiles.public(found),
                                 "policy": policy_payload()})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/manifest":
            # What one run actually rode, read back off disk rather than
            # re-resolved: the frozen policy and the inputs as they were
            # pinned, plus what has moved since. A profile edited mid-run
            # changes the next launch and never this record.
            agent_id = (parse_qs(url.query).get("agent") or [""])[0]
            frozen = profiles.recorded(agent_id)
            if frozen is None:
                self._json(404, {"error": f"no recorded manifest for {agent_id}"})
            else:
                self._json(200, {"manifest": profiles.public(frozen),
                                 "freshness": profiles.freshness(frozen),
                                 "policy": policy_payload()})
        elif path == "/api/evidence":
            # What bench's own verifications found for one card, read back
            # off disk: one row per check, including the checks nothing
            # asked for. Reading is reading — opening a detail panel
            # re-runs nothing, which is why this is a GET of stored
            # records and not a resolution.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            intent = (query.get("intent") or ["work"])[0]
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
            self._json(200, {
                "rollup": verify.rollup(name, manifest),
                "evidence": [verify.public(r) for r in verify.for_task(name)],
                # requests answered by an existing record, each naming the
                # evidence it returned and who asked — not evidence itself
                "reuses": verify.reuses_for_task(name)[:20],
                "outcomes": verify.OUTCOMES,
            })
        elif path == "/api/review":
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
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            records = reviews.for_task(name)
            evidence = [verify.public(r) for r in verify.for_task(name)
                        if (r.get("root") or {}).get("policy") == "review"]
            self._json(200, {
                "reviews": [reviews.public(r) for r in records],
                # what the PR currently proposes, as the board last read
                # it — the fact every `stale` above is measured against
                "head": reviews.observed(name),
                "evidence": evidence,
                "outcomes": verify.OUTCOMES,
                "authorities": reviews.AUTHORITIES,
                "targets": reviews.TARGETS,
                "stage": stage,
            })
        elif path == "/api/gates":
            # What this card's gates require and what the records say —
            # the same reading the merge path makes, from the same
            # function, so a disabled action and a backend refusal can
            # never give two different reasons for one state.
            #
            # A GET of stored records and one `for-each-ref`. It runs no
            # check, relaunches nothing and reaches no remote, which is
            # why opening the sheet is not an action.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] \
                or (taskfiles.find_stage_of(name) or "")
            try:
                self._json(200, gates_payload(name, stage))
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/evidence/log":
            # One verification's own output, scrubbed of anything shaped
            # like a credential on the way out. The record itself never
            # held content; this is the one place bytes bench did not
            # write reach a reader.
            try:
                self._json(200, verify.log_text(
                    (parse_qs(url.query).get("id") or [""])[0]))
            except (ValueError, OSError) as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/verify/checks":
            # What one run may ask for, read off its frozen manifest — the
            # same answer `bench-verify` with no arguments prints.
            agent_id = (parse_qs(url.query).get("agent") or [""])[0]
            frozen = profiles.recorded(agent_id)
            if frozen is None:
                self._json(404, {"error": f"no recorded profile for {agent_id}"})
            else:
                self._json(200, {"checks": [
                    {"name": c["name"], "run": c["run"], "root": c["root"],
                     "scopes": sorted(c["scopes"]), "help": c["help"],
                     "required": c["required"]}
                    for c in verify.available(frozen)]})
        elif path == "/api/doctor":
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
                self._json(200, {"doctor": doctor_payload([])})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/settings":
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
                self._json(200, {"settings": settings.page()})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/setup":
            # The setup guide (task 88): six areas read from the same
            # authorities Settings, the doctor and a launch ask. A GET that
            # stays one — it runs no project program, launches no agent,
            # repairs nothing and writes nothing.
            try:
                self._json(200, {"setup": setupguide.state()})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/setup/verification":
            # Verification readiness (task 90): discovered candidates, each
            # check's readiness and the bootstrap condition. A GET that
            # stays one — discovery reads files and runs nothing.
            try:
                self._json(200, {"verification": readiness.state()})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/preview":
            # The application preview (task 91): discovery, the configuration
            # and its refusals, the lifecycle bound to its source. A GET that
            # stays one — it reads files and asks git; it starts, prepares
            # and runs nothing.
            query = parse_qs(url.query)
            try:
                self._json(200, {"preview": previews.state(
                    (query.get("file") or [""])[0] or None)})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/preview/log":
            self._json(200, previews.log())
        elif path == "/api/card/discussion":
            # A card's discussion, the jobs this board holds for it and
            # whether refining is available — read, never started: a GET
            # here launches nothing and writes nothing.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] or taskfiles.find_stage_of(name) or ""
            try:
                self._json(200, refine.for_card(name, stage))
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/card/acceptance":
            # A card's acceptance criteria and their states, its review
            # rounds and findings, and its dated reports. A GET: it assigns
            # no id and writes nothing — an untracked card reads as one.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] or taskfiles.find_stage_of(name) or ""
            try:
                self._json(200, acceptance.for_card(name, stage))
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/card/delivery":
            # One card's delivery status, always recomputed: what is
            # happening, the next action and who holds it, and what the
            # merge rules still need at the current commit. Read-only — no
            # fetch, no GitHub call, no check, no record written.
            query = parse_qs(url.query)
            name = (query.get("file") or [""])[0]
            stage = (query.get("stage") or [""])[0] or None
            try:
                self._json(200, {"delivery": delivery.for_card(
                    name, stage, phases.public_state())})
            except (ValueError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/settings/history":
            self._json(200, {"history": settings.history()})
        elif path == "/api/diff":
            agent_id = (parse_qs(url.query).get("agent") or [""])[0]
            try:
                self._json(200, agents.agent_diff(agent_id))
            except (ValueError, subprocess.SubprocessError, OSError) as exc:
                self._json(409, {"error": str(exc)})
        elif path == "/api/stream":
            self._stream()
        elif path.startswith("/files/"):
            self._extra_file(path)
        else:
            self._send(404, b"not found", "text/plain")

    _MIME = {".html": "text/html; charset=utf-8",
             ".md": "text/markdown; charset=utf-8",
             ".txt": "text/plain; charset=utf-8",
             ".json": "application/json",
             ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".svg": "image/svg+xml", ".pdf": "application/pdf"}

    def _extra_file(self, path: str) -> None:
        """Serve plans/ and reference/ files so the UI can open them."""
        parts = path.split("/", 3)
        if len(parts) != 4 or parts[2] not in ("plans", "reference"):
            self._send(404, b"not found", "text/plain")
            return
        name = unquote(parts[3])
        if "/" in name or ".." in name or name.startswith("."):
            self._send(404, b"not found", "text/plain")
            return
        file_path = config.work_root() / parts[2] / name
        if not file_path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = self._MIME.get(file_path.suffix.lower(), "application/octet-stream")
        self._send(200, file_path.read_bytes(), ctype)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=500)
        with state.LOCK:
            state.CLIENTS.add(q)
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with state.LOCK:
                state.CLIENTS.discard(q)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path == "/api/move":
                payload = self._read_body()
                # a phase card stands for cards this view no longer draws,
                # so it does not move while one of them has an agent in it
                # — the refusal names ‖ hold, which stops both
                phases.assert_not_working(payload["file"])
                task = taskfiles.move_task(payload["file"], payload["from"], payload["to"])
                self._json(200, {"task": task})
            elif path == "/api/events":
                # The reply may carry a progress reminder for the run that
                # sent the event; a bridge that knows the field hands it
                # back at the tool boundary, and one that does not ignores
                # it — which is the whole of the fallback.
                self._json(200, {"ok": True, **events.ingest_event(self._read_body())})
            elif path == "/api/progress":
                # A run's own report of its work. Capped before it is
                # parsed, then validated whole by `progress.report`, which
                # is the only thing that decides what is kept.
                length = int(self.headers.get("Content-Length") or 0)
                if length > progress.MAX_BODY:
                    self.rfile.read(length)
                    self._json(413, {"error": f"a progress report is at most "
                                              f"{progress.MAX_BODY} bytes",
                                     "code": "too-long"})
                    return
                try:
                    try:
                        body = self._read_body()
                    except (ValueError, UnicodeDecodeError):
                        raise progress.Refused(400, "schema", "the body is not JSON")
                    self._json(200, progress.report(body))
                except progress.Refused as refused:
                    self._json(refused.status, {"error": refused.why,
                                                "code": refused.code})
            elif path == "/api/agent/start":
                payload = self._read_body()
                # takeover: the second, deliberate click on a card someone
                # else holds — never the default a stale card face sends
                agent = agents.start_agent(payload["file"], payload["stage"],
                                           bool(payload.get("takeover")))
                self._json(200, {"agent": agent})
            elif path == "/api/agent/review":
                payload = self._read_body()
                agent = agents.start_review(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/agent/review-pr":
                payload = self._read_body()
                agent = agents.start_pr_review(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/agent/act-pr":
                payload = self._read_body()
                agent = agents.start_pr_fix(payload["file"], payload["stage"])
                self._json(200, {"agent": agent})
            elif path == "/api/phase/run":
                payload = self._read_body()
                # takeover carries the same meaning it does for a launch:
                # the deliberate second click on someone else's card
                self._json(200, {"phase": phases.start_phase(
                    payload["file"], payload["stage"],
                    bool(payload.get("takeover")))})
            elif path == "/api/phase/stop":
                payload = self._read_body()
                # ‖ hold on a phase card: the run stops, nothing is unwound
                self._json(200, {"phase": phases.stop_phase(payload["file"],
                                                            payload["stage"])})
            elif path == "/api/phase/add":
                payload = self._read_body()
                # one line into the phase card, nothing into the card added:
                # membership lives in one place and joining one is not a move
                result = taskfiles.add_to_phase(payload["file"], payload["stage"],
                                                payload["phase"])
                state.record_board_event({
                    "kind": "phase", "actor": "you", "file": result["phase"],
                    "summary": f"{result['phase']} gained {result['entry']}"})
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/plan/replan":
                payload = self._read_body()
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
                self._json(200, result)
            elif path == "/api/pr/open":
                payload = self._read_body()
                self._json(200, {"url": github.open_pr_now(payload["file"])})
            elif path == "/api/pr/copilot":
                payload = self._read_body()
                url = github.request_copilot(payload["file"])
                self._json(200, {"url": url})
            elif path == "/api/task/complete":
                payload = self._read_body()
                # merge & clean up ends with a move to done/, so the same
                # guard: a phase whose member is still working would take
                # its branch to main without that member's work in it
                phases.assert_not_working(payload["file"], "merge it")
                self._json(200, github.complete_task(payload["file"], payload["from"]))
            elif path == "/api/task/complete/forget":
                # The explicit decision about an unfinished completion: a
                # person settled it by hand (or decided against it), so the
                # record goes. It touches no git and moves no card.
                payload = self._read_body()
                name = str(payload["file"])
                if (not name.endswith(".md") or "/" in name or "\\" in name
                        or not github.forget_recovery(name)):
                    raise ValueError(f"{name} has no unfinished completion")
                state.record_board_event({
                    "kind": "agent", "actor": "you", "file": name,
                    "summary": f"{name}: the unfinished-completion record was "
                               f"discarded — nothing in git was changed"})
                state.broadcast({"type": "board"})
                self._json(200, {"forgotten": name})
            elif path == "/api/archive":
                payload = self._read_body()
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
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/unarchive":
                with state.LOCK:
                    last = state.LAST_ARCHIVED
                    state.LAST_ARCHIVED = None
                if not last:
                    raise ValueError("nothing to unarchive — the undo covers the last archive this board made")
                result = taskfiles.unarchive_task(last["file"], last["from"])
                state.record_board_event({
                    "kind": "move", "file": result["file"], "from": "archive",
                    "to": result["to"], "actor": "you",
                    "summary": f"{result['file']} brought back to {result['to']}/"})
                state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/command/run":
                payload = self._read_body()
                self._json(200, commands.run(payload["name"], payload["file"]))
            elif path == "/api/drive/start":
                payload = self._read_body()
                self._json(200, {"drive": drive.start(payload["file"])})
            elif path == "/api/drive/stop":
                self._json(200, {"drive": drive.stop()})
            elif path == "/api/verify":
                payload = self._read_body()
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
                self._json(200, {"evidence": verify.public(verify.request(
                    check=payload["check"],
                    task=payload.get("file"),
                    agent=payload.get("agent") or None,
                    scope=payload.get("scope") or None,
                    intent=payload.get("intent") or None,
                    stage=payload.get("stage") or None,
                    actor="agent" if payload.get("agent") else "you",
                    again=payload.get("again") is True))})
            elif path == "/api/doctor/run":
                payload = self._read_body()
                # The active half, and a POST because each named mode does
                # something: the adapter's own executables, real processes
                # at a fixture bench makes and removes, or — with its own
                # approval and never without — the installed CLI with a
                # model. Nothing here moves a card, touches a branch,
                # publishes anything or changes a setting.
                self._json(200, {"doctor": doctor_payload(
                    [str(m) for m in (payload.get("modes") or [])],
                    approve=bool(payload.get("approve")), asked=True)})
            elif path == "/api/gates/override":
                payload = self._read_body()
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
                    self._json(400, {"error": (
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
                self._json(200, {
                    "override": result["override"],
                    "duplicate": result["duplicate"],
                    "gate": gates.public(result["decision"])})
            elif path in ("/api/setup/decide", "/api/setup/task"):
                # The setup guide's two mutations, behind the same door as
                # a policy write: they write the work state's shared setup
                # record (and, for a task, a card). Neither configures,
                # runs a check or enables a gate — those stay in Settings.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                try:
                    result = (setupguide.decide(payload)
                              if path == "/api/setup/decide"
                              else setupguide.create_task(payload))
                except setupguide.Refused as exc:
                    self._json(exc.status, {"error": str(exc), **exc.extra})
                    return
                if not result.get("duplicate"):
                    state.record_board_event({
                        "kind": "other", "actor": "you",
                        "summary": (f"setup: {result['area']} — "
                                    + (result.get("decision") or
                                       f"setup card {result.get('filename')}"
                                       " proposed in backlog/"))})
                    state.broadcast({"type": "board"})
                self._json(200, {"result": result})
            elif path in ("/api/setup/cards/preview", "/api/setup/cards/create",
                          "/api/setup/cards/decide", "/api/setup/cards/start"):
                # Gap-specific setup cards (task 89), behind the same door.
                # Preview writes nothing; create writes cards and links and
                # starts nothing; start is its own deliberate request that
                # rides ▸ start work and refuses before anything moves.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                action = path.rsplit("/", 1)[1]
                try:
                    result = {"preview": setuptasks.preview,
                              "create": setuptasks.create,
                              "decide": setuptasks.decide,
                              "start": setuptasks.start}[action](payload)
                except setupguide.Refused as exc:
                    self._json(exc.status, {"error": str(exc), **exc.extra})
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
                self._json(200, {"result": result})
            elif path.startswith("/api/preview/") and path.rsplit("/", 1)[1] in (
                    "validate", "configure", "prepare", "cancel", "start",
                    "check", "stop"):
                # The application preview's five acts (task 91), behind the
                # policy door: two of them run this project's own code, and a
                # page on another origin must not be able to ask for that.
                # Validate writes nothing; configure writes one machine-local
                # file and runs nothing; prepare and start each carry their
                # own "approve" and refuse without it.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                action = path.rsplit("/", 1)[1]
                try:
                    result = {"validate": previews.check_draft,
                              "configure": previews.configure,
                              "prepare": previews.prepare,
                              "cancel": previews.cancel,
                              "start": previews.start,
                              "check": previews.check,
                              "stop": previews.stop}[action](payload)
                except setupguide.Refused as exc:
                    self._json(exc.status, {"error": str(exc), **exc.extra})
                    return
                except ValueError as exc:
                    self._json(409, {"error": str(exc)})
                    return
                if action == "configure":
                    state.record_board_event({
                        "kind": "other", "actor": "you",
                        "summary": "setup: preview configuration saved — "
                                   "nothing was run"})
                    state.broadcast({"type": "board"})
                self._json(200, {"result": result})
            elif path in ("/api/setup/verification/register",
                          "/api/setup/verification/review",
                          "/api/setup/verification/activate"):
                # Verification readiness (task 90), behind the same door as
                # a policy write. Register and activate return a draft
                # through Settings' own validate and preview and write
                # nothing — Settings' armed Apply is the only writer of the
                # policy. Review writes one portable record; it runs
                # nothing and changes no gate.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                action = path.rsplit("/", 1)[1]
                try:
                    result = {"register": readiness.register,
                              "review": readiness.review,
                              "activate": readiness.activate}[action](payload)
                except setupguide.Refused as exc:
                    self._json(exc.status, {"error": str(exc), **exc.extra})
                    return
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                if action == "review" and not result.get("duplicate"):
                    state.record_board_event({
                        "kind": "other", "actor": "you",
                        "summary": (f"setup: verification check "
                                    f"{result['check']} reviewed — ready; no "
                                    f"merge rule changed")})
                    state.broadcast({"type": "board"})
                self._json(200, {"result": result})
            elif path in ("/api/settings/validate", "/api/settings/preview",
                          "/api/settings/apply"):
                # Three steps, three requests, on purpose. Validate and
                # preview write nothing at all; only apply writes, and it
                # writes one file over the exact revision the editor was
                # shown. All three go through the policy door above, the
                # read-only two included — a request this server will not
                # act on should not be parsed differently depending on
                # which of them it is.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                if path == "/api/settings/validate":
                    self._json(200, {"validation": settings.validate(payload)})
                elif path == "/api/settings/preview":
                    self._json(200, {"preview": settings.preview(payload)})
                else:
                    try:
                        result = settings.apply(
                            payload, actor=taskfiles.actor_name() or None)
                    except settings.Conflict as exc:
                        # Its own status, because the answer is different
                        # from a refused document: nothing is wrong with
                        # what was sent, a newer save landed first, and the
                        # editor has to re-read before it can apply.
                        self._json(409, {"error": str(exc),
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
                    self._json(200, {"applied": result})
            elif path in ("/api/settings/agents/validate",
                          "/api/settings/agents/preview",
                          "/api/settings/agents/render",
                          "/api/settings/agents/apply"):
                # Settings → Agents (task 77): the same door as the policy
                # editor, the read-only steps included. The payload names
                # agent keys and template names from fixed lists — never a
                # path — and `agentsettings` refuses anything else.
                problem = origin_problem(self.headers, config.PORT)
                if problem:
                    self._json(403, {"error": problem})
                    return
                payload = self._read_body()
                if path.endswith("/validate"):
                    self._json(200, {"validation": agentsettings.validate(payload)})
                elif path.endswith("/preview"):
                    self._json(200, {"preview": agentsettings.preview(payload)})
                elif path.endswith("/render"):
                    self._json(200, {"render": agentsettings.render(payload)})
                else:
                    try:
                        result = agentsettings.apply(
                            payload, actor=taskfiles.actor_name() or None)
                    except agentsettings.Conflict as exc:
                        self._json(409, {"error": str(exc), "conflict": True,
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
                    self._json(200, {"applied": result})
            elif path == "/api/card/comment":
                # Add comment: written to the card, and nothing else — no
                # agent is asked anything because a comment exists.
                payload = self._read_body()
                self._json(200, {"comment": refine.comment(
                    str(payload["file"]), str(payload["stage"]), payload.get("body"))})
            elif path == "/api/card/refine":
                # Update card with agent: one refinement of one comment —
                # a new one (`text`) or one already on the card
                # (`comment`). `request` is the page's identity for the
                # click, so a repeat answers with the job it already made.
                payload = self._read_body()
                comment_id = payload.get("comment")
                text = payload.get("text")
                self._json(200, refine.request(
                    str(payload["file"]), str(payload["stage"]),
                    comment_id=str(comment_id) if comment_id else None,
                    text=text if text is not None else None,
                    request_key=str(payload.get("request") or "") or None,
                    retry=bool(payload.get("retry"))))
            elif path in ("/api/card/refine/apply", "/api/card/refine/discard",
                          "/api/card/refine/cancel"):
                payload = self._read_body()
                job = str(payload["id"])
                try:
                    if path.endswith("/apply"):
                        self._json(200, refine.apply(job))
                    elif path.endswith("/discard"):
                        self._json(200, refine.discard(job))
                    else:
                        self._json(200, {"job": refine.cancel(job)})
                except taskfiles.StaleCard as exc:
                    # its own flag, because the remedy is different: the
                    # proposal is fine, the card moved on, and the answer
                    # is to regenerate rather than to fix anything
                    self._json(409, {"error": str(exc), "stale": True})
            elif path in ("/api/card/acceptance/track", "/api/card/acceptance/decide",
                          "/api/card/finding"):
                # The three writes acceptance has, each a person's explicit
                # act, each bound to what that person was shown. The actor
                # is this board's identity, never a field in the body.
                payload = self._read_body()
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
                    self._json(409, {"error": str(exc), "stale": True})
                    return
                if result.get("written"):
                    who = acceptance.actor()["name"] or "you"
                    state.record_board_event({
                        "kind": "agent", "actor": "you", "file": name,
                        "summary": f"{said} by {who}" + (
                            "" if result.get("committed") else
                            " (written to the card, not committed)")})
                    state.broadcast({"type": "board"})
                self._json(200, result)
            elif path == "/api/verify/cancel":
                payload = self._read_body()
                self._json(200, verify.cancel(payload["id"]))
            elif path == "/api/card/delivery/check-remote":
                # One bounded fetch of the card's own two branch names, on
                # a person's click. Viewing never does this.
                payload = self._read_body()
                found = delivery.check_remote(payload["file"])
                self._json(200, {"remote": found,
                                 "delivery": delivery.for_card(
                                     payload["file"], None,
                                     phases.public_state())})
            elif path == "/api/card/delivery/launch-check":
                # The launch's own preflight, asked explicitly and fresh.
                # It claims nothing and starts nothing; the launch asks
                # again when it is asked for.
                payload = self._read_body()
                self._json(200, {"launch": delivery.launch_check(
                    payload["file"], payload.get("stage"),
                    payload.get("intent") or "work",
                    refusals=agents.launch_refusals,
                    takeover=bool(payload.get("takeover")))})
            elif path == "/api/agent/stop":
                payload = self._read_body()
                agent = agents.stop_agent(payload["id"])
                self._json(200, {"agent": agent})
            else:
                self._send(404, b"not found", "text/plain")
        except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
            self._json(409, {"error": str(exc)})
