"""Agent-assisted recovery and classified transient failure (task 144, D5/D9).

The half of a pass that looks at work which is not moving. It owns no
policy and has no launch path of its own: what may happen is
`orchestrator.authorize`'s answer, asked again immediately before every
side effect; a diagnosis is a `reasoning` job (the `recovery` need,
authorized and counted as the `recover` action); a restart is
`agents.start_agent`, the very path **▸ start work** takes; and a stop is
`agents.stop_agent`, the path **‖ hold** takes.

**Silence is not a stall.** A run's activity is read as five separate
signals, never folded into one: its last public progress update, its
last tool activity, whether a tool call is in flight right now (a long
test through the agent's shell is quiet and busy), whether bench is
running a check for the card, and its last commit. The run's age is
measured against the scope's `runAgeMinutes` ceiling, and crossing it is
a *warning*, shown and never acted on alone. A run is `stalled` only when
it is past that age, its process is alive, and none of the activity
signals has moved for `QUIET_SECONDS`. Nothing here ever stops a run
because it was quiet: a stop is a remediation a diagnosis proposed,
which the agreement delegates (`recovery`), and only for a run this
orchestrator launched.

**An incident is one distinct problem, and its attempts are bounded.**
A stalled run, or an implementation run that ended in a failure that is
not transient, opens one incident, keyed by the run, persisted under the
tool's state *and* on the card (a blocker, every attempt, every action).
Each diagnosis is one attempt: its intent is written before the launch
and its job is found again after a restart, so a repeated tick, a second
pass or a board that died between the two never resets or multiplies
the count. At most the scope's `recoveryAttempts` ceiling (two by
default, lowered by a phase) launch per incident, each an actual launch
against slots, daily starts and the card's launches. When they are spent
without an observed remedy the incident is **exhausted** and held on the
card with what was tried and the owner's choices; nothing tries again.

**A diagnosis proposes; bench observes.** The recovery run has the
`reason` intent's stance: no edit tool, no shell, no worktree — so it
cannot make competing edits to an active worker's source. The one
remediation this module carries out on a proposal is a coordinated
**restart** (`recover` proposed for the card): stop the stalled worker
through **‖ hold** and wait until its process is *observed* gone, then
start implementation work again through start work's own preflights, in
the same worktree, keeping every commit and every uncommitted file —
nothing is reset, rebased or discarded, no grant is widened and no check
is weakened. Each step is re-authorized (`recover` and `implement`), an
unknown outcome on the card or another run in its worktree refuses it,
and the incident is resolved only by what bench observes afterwards —
activity from the new run, or the card moving on — never by what the
diagnosis says. Every other proposal is held for a person.

**Failures are classified (D9).** A failed run's own words are read for
the markers of a transport or setup failure (`transient`: retried by the
scheduler, bounded and backed off), a refusal (`denied`: never retried,
held), or a quality failure (`deterministic`); anything else is
`unknown` and never presumed transient. The durable circuit breaker
opens after `BREAKER_THRESHOLD` failures across the board within
`BREAKER_WINDOW` seconds with no run finishing between them, and stays
open through a restart until it times out or a person resets it.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

import agents
import agreements
import config
import decisions
import operations
import orchestrator
import progress
import reasoning
import records
import state
import taskfiles
import verify

VERSION = 1
KIND = "bench-recovery-incident"

# How recently one activity signal must have moved for a run past its
# age threshold to read as active rather than stalled.
QUIET_SECONDS = 15 * 60
# A restart waits this long for the stopped worker's process to be
# observed gone before the incident is held for a person.
STOP_GRACE = 120
BACKOFF_SECONDS = 60
BREAKER_THRESHOLD = 3
BREAKER_WINDOW = 30 * 60
BREAKER_SECONDS = 600

IMPLEMENTING = ("work", "act-pr")

# open         a justified incident, waiting for its first diagnosis
# diagnosing   a diagnosis attempt is reserved or running
# stopping     a coordinated stop was asked; waiting to observe it
# remediating  a restart was launched; waiting to observe the remedy
# held         a person decides: the diagnosis proposed nothing bench
#              executes, or something refused the next step
# exhausted    every attempt the ceiling allows was spent
# resolved     the remedy, or the run's own recovery, was observed — or
#              a person said it is handled
STATUSES = ("open", "diagnosing", "stopping", "remediating", "held",
            "exhausted", "resolved")
CLOSED = frozenset({"resolved"})
# Refusals that say "not now" rather than "no": no room, or a scope a
# person paused. The incident waits through them instead of being held —
# and since `authorize` asks capacity last, a capacity refusal also
# means every authority, eligibility and pause check before it passed.
WAITING = ("slots-full", "daily-starts-exhausted", "paused")
CAPACITY = ("slots-full", "daily-starts-exhausted")

# Markers in a failed run's own last words. Read as text, case-folded;
# the first class whose marker appears wins, in this order. Nothing
# unmatched is presumed transient.
DENIED = ("not authorized", "not authorised", "permission denied",
          "permission to use", "forbidden", "not permitted", "access denied",
          "authentication failed", "invalid api key")
TRANSIENT = ("timed out", "timeout", "connection reset", "connection refused",
             "connection error", "temporarily unavailable", "overloaded",
             "rate limit", "rate_limit", "too many requests",
             "internal server error", "could not launch adapter",
             "network is unreachable", "econnreset", "try again")
# A status code counts only where it reads as one — never a bare number,
# which a card's own number could be.
_STATUS = re.compile(r"(?:error|status|http)[\s:/]*(\d{3})\b")
DENIED_CODES = {"401", "403"}
TRANSIENT_CODES = {"408", "429", "500", "502", "503", "504", "529"}
DETERMINISTIC = ("assert", "traceback", "error:", "failed", "failure",
                 "exception", "syntaxerror", "not ready", "cannot", "refused")

# What a person can do with an incident bench will not settle by itself.
CHOICES = (
    "Read the diagnosis and attempts on the card, then fix the card or its "
    "inputs and move it yourself",
    "Hand the card back to manual work (Orchestrator → hand back); its "
    "branch and worktree are kept",
    "Stop the run with ‖ hold",
    "Raise recoveryAttempts in the operating agreement for later incidents",
    "Mark the incident resolved once it is handled",
)

_LOCK = records.InterprocessLock(lambda: config.STATE / "orchestration" /
                                 "recovery.lock")


# ── failure classification (D9) ───────────────────────────────────────

def classify(text: str | None, rc=None) -> str:
    """`transient`, `denied`, `deterministic` or `unknown`, from what a
    failed run or launch said. A heuristic over markers, said as one:
    anything it does not recognise is `unknown`, never transient."""
    words = (text or "").lower()
    codes = set(_STATUS.findall(words))
    if any(m in words for m in DENIED) or codes & DENIED_CODES:
        return "denied"
    if any(m in words for m in TRANSIENT) or codes & TRANSIENT_CODES:
        return "transient"
    if any(m in words for m in DETERMINISTIC):
        return "deterministic"
    return "unknown"


def transient(op: dict) -> bool:
    """Did this implementation operation end in a transient failure — a
    launch that could not start (`retry`), or a run whose ending was
    classified transient? Either may be tried again, bounded."""
    return op.get("status") == "retry" or (
        op.get("status") == "ended" and op.get("outcome") == "failed"
        and (op.get("failure") or {}).get("class") == "transient")


def transient_tries(card_file: str, basis: str | None,
                    ops: list[dict] | None = None) -> int:
    """Attempts on this brief that ended without anything the next one
    should not repeat: transient failures, and intents that provably ran
    nothing. What the scheduler's retry ceiling is counted against."""
    return len([o for o in operations.for_card(card_file, "implement", ops)
                if o.get("basis") == basis
                and (transient(o) or o.get("status") == "abandoned")])


def failure_of(record: dict) -> dict:
    """A run's failure as bench recorded it: the exit status and the
    class of its log's last words, with the headline that decided it."""
    found = record.get("failure") or {}
    excerpt = found.get("excerpt") or ""
    return {"class": classify(excerpt, record.get("rc")),
            "rc": record.get("rc", found.get("rc")),
            "headline": agents.died_on(record)[:200] if found else None}


# ── the circuit breaker, durable ──────────────────────────────────────

def _breaker_path() -> Path:
    return config.STATE / "orchestration" / "breaker.json"


def _breaker_read() -> dict:
    found = records.read_json(_breaker_path(), expect=dict) or {}
    return {"v": 1, "failures": [f for f in found.get("failures") or []
                                 if isinstance(f, dict)],
            "openUntil": float(found.get("openUntil") or 0),
            "openedAt": found.get("openedAt"), "cards": found.get("cards") or [],
            "resetBy": found.get("resetBy")}


def breaker_failure(card: str | None, kind: str, now: float | None = None) -> dict:
    """Count one failure; open the breaker at the threshold. Failures older
    than the window drop out, so a slow trickle never trips it."""
    now = time.time() if now is None else now
    with _LOCK:
        found = _breaker_read()
        kept = [f for f in found["failures"] if now - float(f.get("at") or 0)
                < BREAKER_WINDOW][-19:]
        kept.append({"at": now, "card": card, "kind": kind})
        found["failures"] = kept
        if len(kept) >= BREAKER_THRESHOLD and now >= found["openUntil"]:
            found.update(openUntil=now + BREAKER_SECONDS,
                         openedAt=operations.now_iso(),
                         cards=sorted({str(f.get("card")) for f in kept
                                       if f.get("card")}))
        records.write_json(_breaker_path(), found, durable=True)
    return breaker()


def breaker_success() -> None:
    """A run observed finishing: the failures before it were not a
    pattern. Nothing else closes an open breaker early but a person."""
    with _LOCK:
        found = _breaker_read()
        if found["failures"]:
            found["failures"] = []
            records.write_json(_breaker_path(), found, durable=True)


def breaker_reset(actor: str | None = None) -> dict:
    with _LOCK:
        records.write_json(_breaker_path(), {
            "v": 1, "failures": [], "openUntil": 0, "openedAt": None,
            "cards": [], "resetBy": actor or "unknown"}, durable=True)
    return breaker()


def breaker(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    found = _breaker_read()
    open_ = now < found["openUntil"]
    return {"open": open_, "failures": len(found["failures"]),
            "threshold": BREAKER_THRESHOLD, "windowSeconds": BREAKER_WINDOW,
            "until": found["openUntil"] if open_ else None,
            "openedAt": found["openedAt"] if open_ else None,
            "cards": found["cards"] if open_ else [],
            "choices": (["Wait for it to close at the time shown",
                         "Reset it once the cause (an outage, a credential, "
                         "the harness) is fixed"] if open_ else [])}


# ── observing a run ───────────────────────────────────────────────────

def _process_state(record: dict) -> str:
    """The process, not the registry's word for it: a run marked stopped
    by **‖ hold** is alive until its process is observed gone."""
    proc = record.get("proc")
    if proc is not None and hasattr(proc, "poll"):
        try:
            return "alive" if proc.poll() is None else "exited"
        except OSError:
            return "unknown"
    pid = record.get("pid")
    if isinstance(pid, int) and pid > 0:
        return "alive" if Path(f"/proc/{pid}").exists() else "exited"
    # no process handle: what the registry says, and nothing stronger
    return "alive" if record.get("status") == "running" else "exited"


def _last_commit(record: dict) -> float | None:
    worktree = record.get("worktree")
    if not worktree or not Path(worktree).is_dir():
        return None
    try:
        found = config.git_run(worktree, "log", "-1", "--format=%ct", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (getattr(found, "stdout", "") or "").strip()
    return float(text) if text.isdigit() else None


def signals(agent_id: str, record: dict, now: float | None = None) -> dict:
    """Five activity signals, the process and the age, each its own fact.
    Reads the registries and the worktree's last commit; writes nothing."""
    now = time.time() if now is None else now
    with state.LOCK:
        session = dict(state.SESSIONS.get(record.get("session") or "", {}))
    reported = progress.RUNS.get(agent_id) or {}
    checks = sorted({str(r.get("check")) for r in list(verify.RUNNING.values())
                     if r.get("task") == record.get("task")
                     and r.get("outcome") in ("running", "preparing")})
    started = record.get("started") or now
    in_flight = session.get("inFlight") or None
    return {
        "agent": agent_id, "mode": record.get("mode") or "work",
        "started": started, "ageSeconds": int(max(0, now - started)),
        "process": _process_state(record),
        "lastProgress": reported.get("lastReceived"),
        "lastTool": session.get("last"),
        "toolInFlight": (in_flight or {}).get("since"),
        "toolInFlightSummary": (in_flight or {}).get("summary"),
        "checksRunning": checks,
        "lastCommit": _last_commit(record),
    }


def assess(found: dict, threshold_minutes: int, now: float | None = None) -> dict:
    """What the signals amount to. `stalled` is the only state that can
    justify an incident; every other one is left alone, with the age
    threshold a warning rather than a verdict."""
    now = time.time() if now is None else now
    over = found["ageSeconds"] >= threshold_minutes * 60
    if found["process"] != "alive":
        return {"state": "ended", "warning": False,
                "reason": "the run's process has exited; its ending is observed "
                          "elsewhere"}
    if not over:
        return {"state": "young", "warning": False,
                "reason": f"under the {threshold_minutes}-minute age threshold"}
    if found.get("toolInFlight"):
        return {"state": "quiet-active", "warning": True,
                "reason": "past the age threshold with a tool call in flight "
                          f"({found.get('toolInFlightSummary') or 'a tool'}); "
                          "quiet is not stalled"}
    if found.get("checksRunning"):
        return {"state": "quiet-active", "warning": True,
                "reason": "past the age threshold while bench runs "
                          + ", ".join(found["checksRunning"]) + " for it"}
    moved = [t for t in (found.get("lastTool"), found.get("lastProgress"),
                         found.get("lastCommit")) if t]
    if moved and now - max(moved) < QUIET_SECONDS:
        return {"state": "active", "warning": True,
                "reason": f"past the age threshold and still active: its last "
                          f"activity was {int((now - max(moved)) // 60)} min ago"}
    return {"state": "stalled", "warning": True,
            "reason": f"past the {threshold_minutes}-minute age threshold, its "
                      f"process alive, no tool call in flight, no check running "
                      f"and no tool activity, progress update or commit for "
                      f"{QUIET_SECONDS // 60} min"}


# ── incidents, on disk and on the card ────────────────────────────────

def _dir() -> Path:
    return config.STATE / "orchestration" / "incidents"


def incident_id(card_file: str, kind: str, subject: str) -> str:
    raw = "\x00".join(("incident", card_file, kind, subject))
    return "in-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _path(iid: str) -> Path:
    if not iid.startswith("in-") or len(iid) != 19 or \
            any(c not in "0123456789abcdef" for c in iid[3:]):
        raise ValueError(f"{iid!r} is not an incident")
    return _dir() / f"{iid}.json"


def read(iid: str) -> dict | None:
    return records.read_json(_path(iid), expect=dict)


def all_incidents() -> list[dict]:
    found = []
    try:
        paths = sorted(_dir().glob("in-*.json"))
    except OSError:
        return []
    for path in paths:
        item = records.read_json(path, expect=dict)
        if item and item.get("v") == VERSION and item.get("status") in STATUSES:
            found.append(item)
    found.sort(key=lambda i: (i.get("openedEpoch") or 0, i["id"]))
    return found


def _save(item: dict) -> dict:
    item["updatedAt"] = operations.now_iso()
    item["updatedEpoch"] = time.time()
    records.write_json(_path(item["id"]), item, durable=True)
    return item


def _note(item: dict, kind: str, fields: dict, key: str) -> None:
    """The public record (D11). A card that cannot be written never
    changes what bench does: the incident on disk holds it either way."""
    stage = taskfiles.find_stage_of(item["file"])
    if not stage:
        return
    try:
        decisions.note(item["file"], stage, kind, fields, key=key[:80])
    except (ValueError, OSError):
        pass


def _blocker(item: dict) -> str:
    raw = "\x00".join(("recovery", item["file"], item["id"]))
    return "b-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _hold(item: dict, status: str, code: str, reason: str) -> dict:
    """Held, visibly: the blocker on the card says why and what a person
    can do. Written once per incident and code."""
    item.update(status=status, code=code, reason=reason[:400])
    _save(item)
    tried = len(_launched(item))
    _note(item, "blocker", {
        "blocker": _blocker(item), "state": "open", "operation": item["id"],
        "text": (f"{code}: {reason} — {tried} of {item['cap']} recovery "
                 f"attempts launched. Choices: " + "; ".join(CHOICES))[:1900]},
        f"{item['id']}-{code}-held")
    return item


def _clear(item: dict, why: str) -> None:
    _note(item, "blocker", {
        "blocker": _blocker(item), "state": "cleared", "operation": item["id"],
        "text": why[:600]}, f"{item['id']}-cleared")


def _launched(item: dict) -> list[dict]:
    return [a for a in item.get("attempts") or []
            if a.get("status") in ("launched", "settled")]


def _open(task: dict, scope: str, kind: str, subject: str, *, run: str | None,
          reason: str, found: dict | None = None, failure: dict | None = None,
          cap: int, op: str | None = None) -> tuple[dict, bool]:
    """The incident for this card, kind and subject: created once, read
    back after that — the key is the identity, so a second tick or a
    restart finds the one already open."""
    iid = incident_id(task["file"], kind, subject)
    with _LOCK:
        existing = read(iid)
        if existing is not None:
            return existing, False
        item = {"v": VERSION, "kind": KIND, "id": iid, "file": task["file"],
                "card": _number(task), "scope": scope, "type": kind,
                "run": run, "operation": op, "subject": subject,
                "status": "open", "code": "opened", "reason": reason[:400],
                "signals": found, "failure": failure, "cap": cap,
                "attempts": [], "remediation": None,
                "openedAt": operations.now_iso(), "openedEpoch": time.time()}
        _save(item)
    _note(item, "blocker", {
        "blocker": _blocker(item), "state": "open", "operation": iid,
        "text": f"{kind}: {reason}"[:1900]}, f"{iid}-opened")
    return item, True


def _number(task: dict) -> str | None:
    return taskfiles.canonical_number(task["number"]) if task.get("number") else None


def _tasks(board: dict) -> list[dict]:
    return [t for stage in board["stages"] for t in stage["tasks"]]


def _ceiling(reading: dict, sid: str, name: str) -> int:
    here = agreements.scope(reading, sid) or {}
    value = ((here.get("ceilings") or {}).get(name) or {}).get("value")
    return int(value if value is not None else agreements.DEFAULTS[name])


def _orchestrated(agent_id: str, ops: list[dict]) -> dict | None:
    """The operation that launched this run, when an orchestrator did —
    recovery acts only on its own runs; a person's run is observed."""
    return next((op for op in ops if op.get("agent") == agent_id), None)


# ── detection ─────────────────────────────────────────────────────────

def observe_runs(board: dict, reading: dict, now: float | None = None) -> list[dict]:
    """Every running implementation run on a managed card, with its
    signals and what they amount to. A read: it writes nothing."""
    now = time.time() if now is None else now
    by_file = {t["file"]: t for t in _tasks(board)}
    ops = operations.all_ops()
    with state.LOCK:
        running = [(k, dict(r)) for k, r in state.AGENTS.items()
                   if r.get("status") == "running"
                   and (r.get("mode") or "work") in IMPLEMENTING]
    out = []
    for agent_id, record in running:
        task = by_file.get(record.get("task"))
        if task is None:
            continue
        sid, _why = orchestrator.scope_of(task, reading)
        if sid is None:
            continue
        threshold = _ceiling(reading, sid, "runAgeMinutes")
        found = signals(agent_id, record, now)
        verdict = assess(found, threshold, now)
        op = _orchestrated(agent_id, ops)
        out.append({"card": task["file"], "number": _number(task), "scope": sid,
                    "agent": agent_id, "name": record.get("name"),
                    "orchestrated": op is not None,
                    "operation": (op or {}).get("key"),
                    "thresholdMinutes": threshold, "signals": found, **verdict})
    return out


def detect(board: dict, reading: dict, now: float | None = None) -> list[dict]:
    """Open an incident for each justified case: a stalled run this
    orchestrator launched, and an implementation run that ended failed
    without a transient cause. Everything else is observed and left."""
    opened = []
    by_file = {t["file"]: t for t in _tasks(board)}
    for seen in observe_runs(board, reading, now):
        if seen["state"] != "stalled" or not seen["orchestrated"]:
            continue
        task = by_file[seen["card"]]
        item, new = _open(task, seen["scope"], "stalled", seen["agent"],
                          run=seen["agent"], reason=seen["reason"],
                          found=seen["signals"], op=seen["operation"],
                          cap=_ceiling(reading, seen["scope"], "recoveryAttempts"))
        if new:
            opened.append(item)
    # only the newest implementation operation on a card speaks for it: an
    # earlier failure was superseded by the attempt that followed it
    newest: dict = {}
    for op in operations.all_ops():
        if op.get("action") == "implement":
            newest[op.get("file")] = op
    for op in newest.values():
        if op.get("status") != "ended" or op.get("outcome") != "failed":
            continue
        task = by_file.get(op.get("file"))
        if task is None or task["stage"] not in orchestrator.MANAGED_STAGES:
            continue
        sid, _why = orchestrator.scope_of(task, reading)
        if sid is None:
            continue
        failure = op.get("failure") or {"class": "unknown"}
        if failure.get("class") == "transient" and \
                transient_tries(op["file"], op.get("basis")) <= \
                _ceiling(reading, sid, "transientRetries"):
            continue      # the scheduler retries it, bounded and backed off
        item, new = _open(task, sid, "failed", op["key"], run=op.get("agent"),
                          reason=(f"the implementation run ended failed "
                                  f"({failure.get('class')}): "
                                  f"{failure.get('headline') or 'no output'}"),
                          failure=failure, op=op["key"],
                          cap=_ceiling(reading, sid, "recoveryAttempts"))
        if new:
            if failure.get("class") == "denied":
                # a refusal is authority, not a fault to diagnose around
                _hold(item, "held", "authority-denied",
                      "the run was refused something it needed; recovery "
                      "never widens a grant, so a person decides")
            opened.append(item)
    return opened


# ── diagnosis: one attempt at a time, at most `cap` ────────────────────

def _subject(item: dict, attempt: int) -> dict:
    return {"incident": item["id"], "attempt": attempt, "of": item["cap"],
            "type": item["type"], "reason": item.get("reason"),
            "signals": {k: v for k, v in (item.get("signals") or {}).items()
                        if k not in ("toolInFlightSummary",)},
            "failure": item.get("failure"),
            "earlier": [{"attempt": a["n"], "outcome": a.get("outcome")}
                        for a in item.get("attempts") or []],
            "remediations": ["recover: stop this run (if it is still alive) "
                             "and start implementation work again in the same "
                             "worktree, keeping every commit and file",
                             "hold / ask-owner: leave it for a person"]}


def _job_for(item: dict, n: int) -> dict | None:
    return reasoning.for_subject(item["id"], n)


def _reconcile_attempts(item: dict) -> dict:
    """An attempt whose intent has no job: a reasoning request records
    its job before any process starts, so no job is proof nothing ran."""
    changed = False
    for attempt in item.get("attempts") or []:
        if attempt.get("status") != "intent":
            continue
        job = _job_for(item, attempt["n"])
        if job is not None:
            attempt.update(status="launched", job=job["id"],
                           observed="reconciled: its job was found")
        else:
            attempt.update(status="not-launched",
                           observed="reconciled: no job was recorded, so "
                                    "nothing ran")
        changed = True
    if changed:
        _save(item)
    return item


def _settle_attempts(item: dict) -> dict | None:
    """The newest launched attempt's job, once it has ended; None while
    it runs."""
    for attempt in item.get("attempts") or []:
        if attempt.get("status") != "launched":
            continue
        job = _job_for(item, attempt["n"])
        if job is None or job.get("status") in reasoning.ACTIVE:
            return None
        attempt.update(status="settled", outcome=job["status"],
                       settledEpoch=time.time())
        if job["status"] == "valid":
            attempt["proposed"] = [a.get("action") for a in
                                   (job.get("response") or {}).get("actions") or []
                                   if str(a.get("card")) == str(item.get("card"))]
        _save(item)
        _note(item, "attempt", {
            "incident": item["id"], "attempt": attempt["n"], "of": item["cap"],
            "outcome": job["status"], "operation": job["id"],
            "summary": ((job.get("response") or {}).get("summary")
                        or job.get("error") or job["status"])[:600]},
            f"{item['id']}-a{attempt['n']}-{job['status']}")
        return attempt
    return None


def _diagnose(item: dict, board: dict, request=None) -> dict:
    """Launch the next attempt, or hold, or exhaust. The intent is written
    before the request, so a crash between the two is reconciled rather
    than repeated."""
    request = reasoning.request if request is None else request
    spent = len(_launched(item))
    if spent >= item["cap"]:
        return _exhaust(item)
    held = _unsafe(item, board)
    if held:
        return _hold(item, "held", *held)
    answer = orchestrator.authorize("recover", item["file"], board=board)
    if not answer["allowed"]:
        if answer["code"] in WAITING:
            item.update(code=answer["code"], reason=answer["reason"])
            _save(item)
            return item
        return _hold(item, "held", answer["code"], answer["reason"])
    last = next((a for a in reversed(item.get("attempts") or [])
                 if a.get("status") == "settled"), None)
    if last is not None:
        wait = BACKOFF_SECONDS * (2 ** (spent - 1))
        if time.time() < (last.get("settledEpoch") or last.get("epoch") or 0) + wait:
            item.update(code="backing-off", reason=f"attempt {spent + 1} waits "
                        f"{wait}s after attempt {spent}")
            _save(item)
            return item
    n = len(item.get("attempts") or []) + 1
    attempt = {"n": n, "status": "intent", "at": operations.now_iso(),
               "epoch": time.time()}
    item.setdefault("attempts", []).append(attempt)
    item.update(status="diagnosing", code="diagnosing",
                reason=f"diagnosis attempt {spent + 1} of {item['cap']}")
    _save(item)
    try:
        result = request(item["file"], "recovery", via="orchestrator",
                         action="recover", board=board,
                         subject=_subject(item, n))
    except reasoning.Conflict as exc:
        attempt.update(status="not-launched", reason=str(exc)[:300])
        return _hold(item, "held", "diagnosis-refused", str(exc))
    except (ValueError, OSError) as exc:
        attempt.update(status="not-launched", reason=str(exc)[:300])
        return _hold(item, "held", "diagnosis-refused", str(exc))
    job = result["job"]
    attempt.update(status="launched", job=job["id"])
    _save(item)
    _note(item, "attempt", {
        "incident": item["id"], "attempt": spent + 1, "of": item["cap"],
        "outcome": "launched", "operation": job["id"],
        "summary": f"a read-only diagnosis was launched ({job.get('model') or 'harness default'}); "
                   f"it proposes, and bench observes"}, f"{item['id']}-a{n}-launched")
    return item


def _exhaust(item: dict) -> dict:
    if item["status"] == "exhausted":
        return item
    tried = len(_launched(item))
    _note(item, "attempt", {
        "incident": item["id"], "attempt": tried, "of": item["cap"],
        "outcome": "exhausted", "operation": item["id"],
        "summary": f"{tried} of {item['cap']} recovery attempts were spent "
                   f"without an observed remedy; held for a person"},
        f"{item['id']}-exhausted")
    breaker_failure(item.get("card"), "exhausted")
    return _hold(item, "exhausted", "recovery-exhausted",
                 f"{tried} recovery attempts ended without an observed remedy")


def _unsafe(item: dict, board: dict) -> tuple[str, str] | None:
    """What refuses any recovery step on this card, read now."""
    for op in operations.for_card(item["file"]):
        if op.get("status") == "unknown":
            return ("outcome-unknown", f"operation {op['key']} has an unknown "
                    f"outcome; it is reconciled by a person, never retried "
                    f"or worked around")
    return None


# ── remediation: the one coordinated restart ──────────────────────────

def _brief_basis(task: dict) -> str:
    body = task.get("body")
    if body is None:
        try:
            body = (config.TASKS / task["stage"] / task["file"]).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            body = ""
    return taskfiles.brief_sha(taskfiles.card_parts(body)["brief"])[:16]


def _others(item: dict) -> list[str]:
    with state.LOCK:
        return [k for k, r in state.AGENTS.items()
                if r.get("task") == item["file"] and r.get("status") == "running"
                and k != item.get("run")]


def _remediate(item: dict, board: dict, start=None, stop=None) -> dict:
    """Carry out the restart a valid diagnosis proposed, one observed step
    per pass: stop (when the worker lives), then start. Each step asks
    authority again; each refusal holds the incident visibly."""
    start = agents.start_agent if start is None else start
    stop = agents.stop_agent if stop is None else stop
    tasks = {t["file"]: t for t in _tasks(board)}
    task = tasks.get(item["file"])
    if task is None:
        return _hold(item, "held", "card-gone", "the card is not on the board")
    held = _unsafe(item, board)
    if held:
        return _hold(item, "held", *held)
    if task["stage"] != "in-progress":
        return _hold(item, "held", "remediation-unavailable",
                     f"a restart is implementation work, and the card is in "
                     f"{task['stage']}/; a review card's repair is a repair round")
    others = _others(item)
    if others:
        return _hold(item, "held", "worktree-active",
                     f"another run ({others[0]}) is working on this card; "
                     f"recovery never competes with an active worker")
    # the authority to recover, asked again; a stop is not a launch, so a
    # full pool does not refuse it — the stop is what frees the slot
    answer = orchestrator.authorize("recover", item["file"], board=board)
    if not answer["allowed"] and answer["code"] not in CAPACITY:
        if answer["code"] in WAITING:
            item.update(code=answer["code"], reason=answer["reason"])
            _save(item)
            return item
        return _hold(item, "held", answer["code"], answer["reason"])
    remedy = item.get("remediation") or {}
    with state.LOCK:
        worker = dict(state.AGENTS.get(item.get("run") or "") or {})
    alive = bool(worker) and _process_state(worker) == "alive"
    if alive:
        if not remedy.get("stopAsked"):
            remedy = {"stopAsked": time.time(), "stopAt": operations.now_iso()}
            item.update(remediation=remedy, status="stopping", code="stopping",
                        reason="a coordinated stop was asked; the restart waits "
                               "until the process is observed gone")
            _save(item)
            _note(item, "action", {"action": "recover", "operation": item["id"],
                                   "phase": "intent", "outcome": "stop",
                                   "summary": f"stopping {item.get('run')} through "
                                              f"‖ hold before a restart"},
                  f"{item['id']}-stop-intent")
            try:
                stop(item["run"])
            except (ValueError, OSError) as exc:
                return _hold(item, "held", "stop-refused", str(exc))
            return item
        if time.time() - remedy["stopAsked"] > STOP_GRACE:
            return _hold(item, "held", "stop-unconfirmed",
                         f"{item.get('run')} was asked to stop and its process "
                         f"is still alive; nothing is restarted over it")
        return item
    if remedy.get("stopAsked") and not remedy.get("stopObserved"):
        remedy["stopObserved"] = operations.now_iso()
        _note(item, "action", {"action": "recover", "operation": item["id"],
                               "phase": "receipt", "outcome": "stopped",
                               "summary": f"{item.get('run')}'s process was "
                                          f"observed gone"},
              f"{item['id']}-stop-receipt")
    item["remediation"] = remedy
    if breaker()["open"]:
        item.update(code="breaker-open", reason="the circuit breaker is open; "
                    "the restart waits for it")
        _save(item)
        return item
    again = orchestrator.authorize("implement", item["file"], board=board)
    if not again["allowed"]:
        if again["code"] in WAITING:
            item.update(code=again["code"], reason=again["reason"])
            _save(item)
            return item
        return _hold(item, "held", again["code"], again["reason"])
    basis = _brief_basis(task)
    key = operations.key_for("implement", item["file"], f"recovery:{item['id']}", 1)
    try:
        op = operations.begin(key, action="implement", card=item["file"],
                              number=item.get("card"), scope=again["scope"],
                              pool=orchestrator.LAUNCHES["implement"],
                              basis=basis, attempt=1,
                              note=f"recovery restart for {item['id']}")
    except operations.Exists as exists:
        item.update(remediation={**remedy, "operation": exists.op["key"]},
                    status="remediating", code="remediating",
                    reason="the restart is already recorded")
        _save(item)
        return item
    remedy.update(operation=key, startAt=operations.now_iso())
    item.update(remediation=remedy, status="remediating", code="remediating",
                reason="restarting implementation work in the same worktree")
    _save(item)
    _note(item, "action", {"action": "implement", "operation": key,
                           "phase": "intent", "outcome": "intended",
                           "summary": f"recovery restart for {item['id']}: "
                                      f"start work again, keeping every commit "
                                      f"and file"}, f"{key}-intent-intended")
    final = orchestrator.authorize("implement", item["file"], operation=key)
    if not final["allowed"]:
        operations.update(key, "refused", code=final["code"], reason=final["reason"])
        return _hold(item, "held", final["code"], final["reason"])
    try:
        record = start(item["file"], "in-progress")
    except ValueError as exc:
        operations.update(key, "refused", code="launch-refused", reason=str(exc))
        return _hold(item, "held", "restart-refused", str(exc))
    except Exception as exc:  # noqa: BLE001 — anything unexplained is unknown
        operations.update(key, "unknown", code="launch-error",
                          reason=f"{type(exc).__name__}: {exc}",
                          observed="the restart raised; whether a run started "
                                   "is not known")
        return _hold(item, "held", "outcome-unknown",
                     "the restart's outcome is unknown; held for a person")
    agent_id = (record or {}).get("id")
    with state.LOCK:
        proc = (state.AGENTS.get(agent_id or "") or {}).get("proc")
    operations.update(key, "launched", agent=agent_id,
                      pid=getattr(proc, "pid", None),
                      observed="the recovery restart returned its run")
    # counted as a launch by the ledger itself: a launched operation spent
    # a start and one of the card's launches (`orchestrator.usage`)
    remedy.update(agent=agent_id)
    item["remediation"] = remedy
    _save(item)
    _note(item, "action", {"action": "implement", "operation": key,
                           "phase": "receipt", "outcome": "launched",
                           "summary": f"the restart started {agent_id}; the "
                                      f"incident resolves only when bench "
                                      f"observes it working"},
          f"{key}-receipt-launched")
    return item


def _observe_remedy(item: dict, board: dict, now: float) -> dict:
    """Resolved only by what bench observes: the restarted run active, or
    the card moved on. A remediation that failed closes the incident; its
    new failure is an incident of its own."""
    remedy = item.get("remediation") or {}
    key = remedy.get("operation")
    op = operations.read(key) if key else None
    task = next((t for t in _tasks(board) if t["file"] == item["file"]), None)
    if task is not None and task["stage"] in ("review", "done"):
        return _resolve(item, f"observed: the card moved to {task['stage']}/")
    if op is None:
        return item
    if op["status"] == "ended":
        if op.get("outcome") == "failed":
            item.update(status="resolved", code="remediation-failed",
                        reason="the restarted run failed; its failure is a "
                               "new incident with its own attempts")
            _save(item)
            _clear(item, "the restart ran and failed; see the next incident")
            return item
        return _resolve(item, f"observed: the restarted run ended "
                              f"({op.get('outcome')})")
    agent_id = op.get("agent")
    with state.LOCK:
        record = dict(state.AGENTS.get(agent_id or "") or {})
    if record:
        found = signals(agent_id, record, now)
        moved = [t for t in (found.get("lastTool"), found.get("lastProgress"))
                 if t and t >= (record.get("started") or 0)]
        if moved or found.get("toolInFlight") or found.get("checksRunning"):
            return _resolve(item, f"observed: the restarted run {agent_id} is "
                                  f"working")
    return item


def _resolve(item: dict, why: str, by: str | None = None) -> dict:
    item.update(status="resolved", code="resolved-by-person" if by else "resolved",
                reason=why[:400], resolvedBy=by)
    _save(item)
    _clear(item, why)
    _note(item, "attempt", {
        "incident": item["id"], "attempt": len(_launched(item)),
        "of": item["cap"], "outcome": "resolved", "operation": item["id"],
        "summary": why[:600]}, f"{item['id']}-resolved")
    return item


# ── the pass ──────────────────────────────────────────────────────────

def run(board: dict, request=None, start=None, stop=None,
        now: float | None = None) -> list[dict]:
    """One recovery step per incident, after the scheduler's and the
    delivery steps. Off without an enabled agreement; nothing here runs
    on a read."""
    now = time.time() if now is None else now
    reading = agreements.effective()
    if not (reading["configured"] and reading["valid"]):
        return []
    results = []
    detect(board, reading, now)
    by_file = {t["file"]: t for t in _tasks(board)}
    breaker_open = breaker(now)["open"]
    for item in all_incidents():
        if item["status"] in CLOSED or item["status"] in ("held", "exhausted"):
            continue
        task = by_file.get(item["file"])
        if task is None or task["stage"] not in orchestrator.MANAGED_STAGES:
            results.append({"incident": item["id"], **_summary(_resolve(
                item, "observed: the card left the orchestrated stages"))})
            continue
        _reconcile_attempts(item)
        # a failure a person moved on from, or a newer run superseded
        if item["type"] == "failed" and item["status"] in ("open", "diagnosing"):
            newest = [o for o in operations.for_card(item["file"], "implement")]
            if task["stage"] != "in-progress" or (
                    newest and newest[-1]["key"] != item.get("operation")):
                _settle_attempts(item)
                results.append({"incident": item["id"], **_summary(_resolve(
                    item, "observed: the card moved on from this failure"))})
                continue
        # the run recovered on its own, or ended: nothing more to do —
        # an ending is the scheduler's to observe (and a failure is an
        # incident of its own)
        if item["type"] == "stalled" and item["status"] in ("open", "diagnosing"):
            with state.LOCK:
                worker = dict(state.AGENTS.get(item.get("run") or "") or {})
            verdict = (assess(signals(item["run"], worker, now),
                              _ceiling(reading, item["scope"], "runAgeMinutes"),
                              now) if worker else {"state": "ended"})
            if verdict["state"] in ("active", "quiet-active", "ended"):
                _settle_attempts(item)
                why = ("observed: the run ended by itself"
                       if verdict["state"] == "ended" else
                       f"observed: the run is active again — {verdict['reason']}")
                results.append({"incident": item["id"],
                                **_summary(_resolve(item, why))})
                continue
        if item["status"] in ("stopping", "remediating"):
            if item["status"] == "remediating":
                item = _observe_remedy(item, board, now)
            if item["status"] == "stopping" or (
                    item["status"] == "remediating"
                    and not (item.get("remediation") or {}).get("operation")):
                item = _remediate(item, board, start, stop)
            results.append({"incident": item["id"], **_summary(item)})
            continue
        settled = _settle_attempts(item)
        if any(a.get("status") == "launched" for a in item.get("attempts") or []):
            continue    # a diagnosis is running
        if settled is not None and settled.get("outcome") == "valid":
            proposed = settled.get("proposed") or []
            if "recover" in proposed:
                item = _remediate(item, board, start, stop)
            else:
                words = ", ".join(proposed) or "no action"
                item = _hold(item, "held", "diagnosed",
                             f"the diagnosis proposed {words}; bench executes "
                             f"only a coordinated restart, so a person decides")
            results.append({"incident": item["id"], **_summary(item)})
            continue
        if breaker_open:
            continue
        item = _diagnose(item, board, request)
        results.append({"incident": item["id"], **_summary(item)})
    return results


def resolve(iid: str, actor: str | None = None) -> dict:
    """A person says an incident is handled. It launches nothing, stops
    nothing and resets no attempt count of any other incident."""
    item = read(iid)
    if item is None:
        raise LookupError(f"no incident {iid} on record")
    if item["status"] in CLOSED:
        raise ValueError(f"incident {iid} is already {item['status']}")
    return public(_resolve(item, f"{actor or 'a person'} marked it handled",
                           by=actor or "unknown"))


# ── reading ───────────────────────────────────────────────────────────

PUBLIC = ("id", "file", "card", "scope", "type", "run", "status", "code",
          "reason", "cap", "openedAt", "updatedAt", "failure", "resolvedBy")


def _summary(item: dict) -> dict:
    return {"status": item["status"], "code": item.get("code")}


def public(item: dict) -> dict:
    """What a reader is given: no path, no process id."""
    out = {k: item.get(k) for k in PUBLIC if item.get(k) is not None}
    out["attempts"] = [{k: a.get(k) for k in ("n", "status", "outcome", "job",
                                              "at", "proposed") if a.get(k)}
                       for a in item.get("attempts") or []]
    out["launched"] = len(_launched(item))
    sig = item.get("signals") or {}
    out["signals"] = {k: sig.get(k) for k in (
        "ageSeconds", "process", "lastProgress", "lastTool", "toolInFlight",
        "checksRunning", "lastCommit") if k in sig}
    if item["status"] in ("held", "exhausted"):
        out["choices"] = list(CHOICES)
    return out


def projection(board: dict | None = None) -> dict:
    """What `GET /api/orchestration` shows of recovery: the runs and their
    signals, every incident with its attempts and choices, and the
    breaker. A read — it launches, stops and writes nothing."""
    board = taskfiles.collect() if board is None else board
    reading = agreements.effective()
    runs = observe_runs(board, reading) if reading["configured"] and \
        reading["valid"] else []
    incidents = [public(i) for i in all_incidents()]
    return {"runs": [{k: v for k, v in r.items() if k != "signals"}
                     | {"signals": {k: r["signals"].get(k) for k in (
                         "ageSeconds", "process", "lastProgress", "lastTool",
                         "toolInFlight", "checksRunning", "lastCommit")}}
                     for r in runs],
            "incidents": incidents,
            "held": [i for i in incidents if i["status"] in ("held", "exhausted")],
            "breaker": breaker(),
            "quietSeconds": QUIET_SECONDS,
            "notes": ["The age threshold is a warning: nothing is stopped for "
                      "being quiet.",
                      "A diagnosis proposes; an incident is resolved only by "
                      "what bench observes afterwards."]}
