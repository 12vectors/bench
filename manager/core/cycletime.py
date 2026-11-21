"""How long a card has taken, and where the time went (task 165).

A card's cycle time is read off records bench already keeps — nothing
here is a second collector, and nothing here writes:

- **stage moves** — `movements.history`: the committed `board:` moves and
  the machine-local journal of uncommitted ones, each with its time;
- **runs** — `progress.RUNS`: every work, act-on-PR and review run's
  opening and the ending the board observed (task 73);
- **checks** — `evidence`: each verification's request, start and end,
  its preparation steps, and the reuse records that ran nothing;
- **integration** — a phase member's `<n> merged into` line in its phase
  card's log, and the delivered record merge & clean up leaves;
- **liveness** — the run registry and its session's last tool event, kept
  apart from what a run *reported* (its progress update).

Two rules make the numbers honest, and both are deliberate:

1. **Categories overlap and are never added together.** A check runs
   inside an agent run, which runs while the card sits in In progress.
   Each category is its own measurement over the same clock — the
   verification figure is the *union* of the check windows, so two
   checks running side by side count their shared minutes once — and the
   only whole is the card's lead time, measured from its own first and
   last moves. Agent time outside checks is the run's span less the
   minutes a check of that run was in flight: it is *not* measured coding
   time, and is never called that.
2. **An unknown is said, never filled.** A run whose ending the board did
   not observe (a restart), a check closed by a restart, a move made by
   hand without a record, a clock that runs backwards between two records
   — each is an `unknown` or an `anomaly` with its reason, and its time is
   in no total. Absent is not zero.

`build()` is pure: deterministic from its inputs and the `now` it is
given, which is what the fixtures in `tests/test_cycletime.py` hold to
exact totals. `for_card()` gathers the inputs with reads only — files,
the in-memory registries and the memoised git history movements already
keeps — and launches, fetches and writes nothing.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

import completion
import evidence
import movements
import profiles
import progress
import state
import taskfiles

VERSION = 1

# A record dated this far after `now` is a clock anomaly, not the future.
CLOCK_SKEW = 5.0

STAGES = ("backlog", "to-do", "in-progress", "review", "done", "archive")
QUEUE_STAGES = ("backlog", "to-do")

RUN_KINDS = {
    "work": ("implementation", "Implementation run"),
    "act-pr": ("repair", "Repair run (act on PR)"),
    "review": ("review", "Review run"),
}

# What each category is, said once so the API and the page use one sentence.
CATEGORIES = (
    ("lead", "Lead time",
     "From the card's first recorded move to its arrival in Done — the whole "
     "ticket, the only total. Every other row overlaps it."),
    ("queued", "Queued",
     "Time in Backlog and To do, summed over every visit."),
    ("in-progress", "In progress (stage)",
     "Time the card sat in In progress, summed over every visit, whether "
     "or not a run was working."),
    ("implementation", "Implementation runs",
     "The union of work runs' spans, opening to observed ending."),
    ("verification", "Verification (checks in flight)",
     "The union of every check's window, request to ending, so overlapping "
     "checks count their shared time once."),
    ("outside-checks", "Agent time outside checks",
     "Implementation and repair runs' union, less the time a check was in "
     "flight. Not measured coding time: bench sees the run, not what it did."),
    ("review-runs", "Review runs",
     "The union of review runs' spans."),
    ("review-stage", "In review (stage)",
     "Time the card sat in Review, summed over every visit, waiting or worked."),
    ("repair", "Repair runs",
     "The union of act-on-PR runs' spans."),
    ("integration-wait", "Waiting for integration",
     "A phase member: from its last arrival in Review to its merge into the "
     "phase branch. In the phase branch is not delivered."),
    ("delivery-wait", "Waiting for delivery to main",
     "A phase member: from its merge into the phase branch to its phase's "
     "delivery to the application base."),
)


# ── intervals ──────────────────────────────────────────────────────────


def union(intervals) -> list[tuple[float, float]]:
    """Overlapping or touching intervals merged, in order. An interval
    whose end is before its start is not an interval and is dropped — the
    caller reports it as an anomaly before it gets here."""
    spans = sorted((float(a), float(b)) for a, b in intervals
                   if a is not None and b is not None and b >= a)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def length(merged) -> float:
    return sum(b - a for a, b in merged)


def overlap(a, b) -> float:
    """How much two merged interval lists share."""
    total, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def ts(value) -> float | None:
    """A time as seconds since the epoch: a number as it is, an ISO text
    (a git committer date, a journal line) parsed with its zone. Anything
    else is no time, never zero."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def log_time(stamp: str | None) -> float | None:
    """A phase log line's minute stamp (`YYYY-MM-DD HH:MM`), written in this
    board's local time with no zone."""
    if not isinstance(stamp, str):
        return None
    try:
        return time.mktime(time.strptime(stamp.strip(), "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return None


# The phase runner's (and a phase scope's) record of a member landing in
# the phase branch: `- <YYYY-MM-DD HH:MM> · <n> merged into <branch>`. A
# `partly merged` line is not a merge. Read here rather than asked of
# `phases`, which sits above this module.
_LOG_LINE_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+·\s+(.+?)\s*$")
MERGED_RE = re.compile(r"^(\d+) merged into ")


def log_lines(text: str) -> list[tuple[str, str]]:
    section = re.search(rf"^##\s+{taskfiles.PHASE_LOG_HEADING}\s*$(.*?)(?=^##\s|\Z)",
                        text or "", re.MULTILINE | re.DOTALL)
    if not section:
        return []
    return [(m.group(1), m.group(2)) for m in
            (_LOG_LINE_RE.match(line.strip()) for line in section.group(1).splitlines())
            if m]


def _secs(value: float | None) -> int | None:
    return None if value is None else int(round(value))


# ── the reading ────────────────────────────────────────────────────────


class _Notes:
    def __init__(self, now: float):
        self.now = now
        self.anomalies: list[dict] = []
        self.unknown: list[dict] = []

    def anomaly(self, code: str, detail: str) -> None:
        self.anomalies.append({"code": code, "detail": detail})

    def missing(self, what: str, why: str) -> None:
        self.unknown.append({"what": what, "why": why})

    def when(self, value, what: str) -> float | None:
        """A time, checked against the clock that reads it."""
        at = ts(value)
        if at is not None and at > self.now + CLOCK_SKEW:
            self.anomaly("future-time",
                         f"{what} is dated after this reading was taken — the "
                         f"clocks that wrote it disagree, so it is left out")
            return None
        return at


def _stages(card: dict, moves: dict, notes: _Notes) -> tuple[list, dict, dict]:
    """The card's stage timeline, oldest first, its per-stage totals, and
    where the lead time starts and ends."""
    if moves.get("problem"):
        notes.missing("stage history",
                      {"no-repository": "the work state is in no git repository, so "
                                        "only uncommitted moves are on record",
                       "no-commits": "the work state's repository has no commits yet",
                       "history-unreadable": "git could not read the work state's "
                                             "history"}.get(moves["problem"],
                                                            moves["problem"]))
    if not moves.get("complete", True):
        notes.missing("early stage history",
                      "older moves are beyond the history bench reads, so the "
                      "first moves may be missing")
    raw = [e for e in (moves.get("events") or []) if e.get("to")]
    events = []
    for e in reversed(raw):     # history is newest first
        at = notes.when(e.get("at"), f"the move into {e.get('to')}")
        if at is None and e.get("at") is not None and ts(e.get("at")) is None:
            notes.anomaly("unreadable-time", f"a move into {e['to']} carries no readable time")
        events.append({"at": at, "to": e["to"], "from": e.get("from"),
                       "kind": e.get("kind"), "source": e.get("source"),
                       "commit": e.get("commit")})
    segments = []
    for i, e in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        start = e["at"]
        if nxt is not None:
            end, open_ = nxt["at"], False
        elif e["to"] == card.get("stage"):
            end, open_ = notes.now, True
        else:
            end, open_ = None, False
            notes.anomaly("unrecorded-move",
                          f"the last recorded move put the card in {e['to']}, but it "
                          f"is in {card.get('stage')} — it was moved without a "
                          f"record, so when it left {e['to']} is unknown")
        seconds = None
        if start is not None and end is not None:
            if end < start:
                notes.anomaly("move-order",
                              f"the move out of {e['to']} is dated before the move "
                              f"into it — commit dates out of order (a replayed or "
                              f"rebased board commit), so that visit has no duration")
            else:
                seconds = end - start
        segments.append({"stage": e["to"], "from": start, "to": end if not open_ else None,
                         "open": open_, "seconds": _secs(seconds),
                         "how": e["kind"], "source": e["source"], "commit": e["commit"],
                         "_span": (start, end) if seconds is not None else None})
    totals: dict = {}
    for s in segments:
        if s["_span"] is not None and s["stage"] != "done":
            totals.setdefault(s["stage"], []).append(s["_span"])
    if not events and card.get("stage") and not moves.get("problem"):
        notes.missing("stage history", "no move of this card is on record")
    # the lead time: first record → the newest arrival in done
    start = next((e for e in events if e["at"] is not None), None)
    lead = {"start": None, "end": None, "open": True, "seconds": None,
            "reopened": sum(1 for e in events if e.get("from") == "done"
                            and e["to"] != "done")}
    if start is not None:
        lead["start"] = {"at": start["at"],
                         "how": "created" if start["kind"] == "created" else "first-move"}
        if start["kind"] != "created":
            notes.missing("creation time",
                          "the card's creation is not on record; lead time starts at "
                          "its first recorded move")
    if card.get("stage") == "done":
        arrived = next((e for e in reversed(events) if e["to"] == "done"), None)
        lead["open"] = False
        if arrived is not None and arrived["at"] is not None:
            lead["end"] = {"at": arrived["at"], "how": "moved to done"}
        else:
            notes.missing("delivery time", "the card is in Done and its move there is "
                                           "not on record")
    if lead["start"] is not None:
        end = notes.now if lead["open"] else (lead["end"] or {}).get("at")
        if end is not None:
            if end < lead["start"]["at"]:
                notes.anomaly("lead-order", "the card reached Done before its first "
                                            "recorded move — clocks out of order")
            else:
                lead["seconds"] = _secs(end - lead["start"]["at"])
    if card.get("stage") == "archive":
        lead["open"] = False
        lead["seconds"] = None
        notes.missing("lead time", "an archived card has left the flow; it has no "
                                   "delivery to measure to")
    for s in segments:
        s.pop("_span", None)
    return segments, {k: union(v) for k, v in totals.items()}, lead


def _runs(runs: list, records: list, notes: _Notes, live: dict) -> tuple[list, dict]:
    """Every run's span, and its kind's union."""
    out, spans = [], {"implementation": [], "repair": [], "review": []}
    by_agent: dict = {}
    for r in records:
        if r.get("agent"):
            by_agent.setdefault(r["agent"], []).append(r)
    for r in sorted(runs, key=lambda x: ts(x.get("opened")) or 0):
        kind, label = RUN_KINDS.get(r.get("intent"), ("other", "Run"))
        if r.get("prReview"):
            label = "PR review run"
        opened = notes.when(r.get("opened"), f"run {r.get('agent')}'s opening")
        closed = notes.when(r.get("closed"), f"run {r.get('agent')}'s ending")
        outcome = r.get("outcome")
        alive = live.get(r.get("agent"))
        ended = "observed"
        if not r.get("closed"):
            ended, closed = "running", notes.now
        elif outcome == "unobserved":
            ended, closed = "unobserved", None
            notes.missing(f"run {r.get('agent')}",
                          "the board restarted while it ran, so its ending was not "
                          "observed and its time is in no total")
        span = None
        if opened is None:
            notes.missing(f"run {r.get('agent')}", "its opening time is not on record")
        elif closed is not None:
            if closed < opened:
                notes.anomaly("run-order", f"run {r.get('agent')} is recorded ending "
                                           f"before it opened")
            else:
                span = (opened, closed)
                spans.setdefault(kind, []).append(span)
        mine = union((_window(e, notes, quiet=True) or (None, None))
                     for e in by_agent.get(r.get("agent"), []))
        current = r.get("current") or {}
        report = None
        if current:
            report = {"at": ts(current.get("at")), "stage": current.get("stage"),
                      "doing": current.get("doing"), "blocked": current.get("blocked"),
                      "source": "agent-reported"}
        out.append({
            "agent": r.get("agent"), "intent": r.get("intent"), "kind": kind,
            "label": label, "opened": opened,
            "closed": None if ended == "running" else closed,
            "ended": ended, "outcome": outcome if ended != "running" else None,
            "seconds": _secs(span[1] - span[0]) if span else None,
            "open": ended == "running",
            "checks": {"count": len(by_agent.get(r.get("agent"), [])),
                       "seconds": _secs(length(mine))},
            "outsideChecks": (_secs((span[1] - span[0]) - overlap([span], mine))
                              if span and kind != "review" else None),
            "lastReport": report,
            "lastActivity": (alive or {}).get("lastTool"),
            "process": (alive or {}).get("process") if ended == "running" else None,
        })
    return out, {k: union(v) for k, v in spans.items()}


def _window(e: dict, notes: _Notes, quiet: bool = False) -> tuple[float, float] | None:
    """A check's window, request to ending — or to now while it is live.
    None when it ran nothing or its end is unknown."""
    if e.get("outcome") == "denied" or e.get("reused"):
        return None
    start = ts(e.get("requested"))
    if e.get("outcome") in evidence.LIVE:
        end = notes.now
    elif e.get("reason") == "board-restart":
        return None
    else:
        end = ts(e.get("ended"))
    if start is None or end is None:
        return None
    if start > notes.now + CLOCK_SKEW or end < start:
        return None
    return (start, end)


def _verification(records: list, reuses: list, notes: _Notes) -> dict:
    windows, rows, attempts = [], [], {}
    execution = preparation = 0.0
    summed = 0.0
    for e in sorted(records, key=lambda r: ts(r.get("requested")) or 0):
        win = _window(e, notes)
        live = e.get("outcome") in evidence.LIVE
        name = e.get("check") or "?"
        if e.get("reason") == "board-restart":
            notes.missing(f"check {name} ({e.get('id')})",
                          "the board stopped while it ran; its record was closed at the "
                          "next start, so how long it ran is unknown and in no total")
        elif e.get("outcome") not in ("denied",) and not e.get("reused") and win is None:
            start, end = ts(e.get("requested")), ts(e.get("ended"))
            if start is not None and end is not None and end < start:
                notes.anomaly("check-order", f"check {name} ({e.get('id')}) is recorded "
                                             f"ending before it was requested")
            elif start is not None and start > notes.now + CLOCK_SKEW:
                notes.anomaly("future-time", f"check {name} ({e.get('id')}) is dated after "
                                             f"this reading")
            else:
                notes.missing(f"check {name} ({e.get('id')})",
                              "its request or ending time is not on record")
        run_s = None
        st, en = ts(e.get("started")), ts(e.get("ended"))
        if live and st is not None:
            en = notes.now
        if st is not None and en is not None and en >= st:
            run_s = en - st
        prep = 0.0
        for step in e.get("prepare") or []:
            a, b = ts(step.get("started")), ts(step.get("ended"))
            if a is not None and b is not None and b >= a:
                prep += b - a
        if win is not None:
            windows.append(win)
            summed += win[1] - win[0]
            execution += run_s or 0.0
            preparation += prep
        attempt = len(attempts.setdefault(name, [])) + 1
        attempts[name].append(e.get("id"))
        rows.append({
            "id": e.get("id"), "check": name, "scope": e.get("scope"),
            "attempt": attempt, "outcome": e.get("outcome"),
            "reason": e.get("reason"), "live": live,
            "requested": ts(e.get("requested")), "started": st if not live else ts(e.get("started")),
            "ended": None if live else ts(e.get("ended")),
            "seconds": _secs(win[1] - win[0]) if win else None,
            "execution": _secs(run_s), "preparation": _secs(prep) if prep else None,
            "ranNothing": e.get("outcome") == "denied",
            "commit": ((e.get("source") or {}).get("commit") or "")[:12] or None,
            "agent": e.get("agent"), "actor": e.get("actor"), "intent": e.get("intent"),
            "phase": ("preparing" if e.get("outcome") == "preparing" else
                      "running the check" if live else None),
        })
    merged = union(windows)
    some = bool(windows)
    return {
        "seconds": _secs(length(merged)) if some else None,
        "summed": _secs(summed) if some else None,
        "execution": _secs(execution) if some else None,
        "preparation": _secs(preparation) if some else None,
        "overlapped": _secs(summed - length(merged)) if some else None,
        "count": len(rows),
        "reused": len(reuses),
        "attempts": {k: len(v) for k, v in attempts.items()},
        "records": list(reversed(rows)),     # newest first, as the sheet lists them
        "_merged": merged,
    }


def build(inputs: dict, now: float) -> dict:
    """The whole reading from its inputs — pure, so a fixture can hold it to
    exact totals. `inputs` is what `gather()` reads: the card, its moves,
    runs, verification and reuse records, integration and delivery facts,
    and the live registry's view of running runs."""
    notes = _Notes(now)
    card = inputs.get("card") or {}
    segments, stage_spans, lead = _stages(card, inputs.get("moves") or {}, notes)
    records = inputs.get("evidence") or []
    verification = _verification(records, inputs.get("reuses") or [], notes)
    runs, run_spans = _runs(inputs.get("runs") or [], records, notes,
                            inputs.get("live") or {})
    merged = verification.pop("_merged")
    worked = union([*run_spans.get("implementation", []), *run_spans.get("repair", [])])

    integration = _integration(card, inputs, segments, notes)

    def row(key, spans=None, seconds=None, open_=False, extra=None):
        label, note = next((c[1], c[2]) for c in CATEGORIES if c[0] == key)
        if spans is not None:
            # nothing recorded is absent, never zero: a card never in
            # review has no review time, not 0s of it
            seconds = _secs(length(spans)) if spans else None
        return {"key": key, "label": label, "note": note, "seconds": seconds,
                "recorded": seconds is not None, "open": open_, **(extra or {})}

    def staged(names):
        spans = union([s for n in names for s in stage_spans.get(n, [])])
        return spans, any(s["open"] for s in segments if s["stage"] in names)

    q, q_open = staged(QUEUE_STAGES)
    ip, ip_open = staged(("in-progress",))
    rv, rv_open = staged(("review",))
    running = lambda kind: any(r["open"] and r["kind"] == kind for r in runs)
    categories = [
        row("lead", seconds=lead["seconds"], open_=lead["open"]),
        row("queued", q, open_=q_open),
        row("in-progress", ip, open_=ip_open),
        row("implementation", run_spans.get("implementation", []),
            open_=running("implementation")),
        row("verification", merged, open_=any(r["live"] for r in verification["records"]),
            extra={"summed": verification["summed"]}),
        row("outside-checks",
            seconds=_secs(length(worked) - overlap(worked, merged)) if worked else None,
            open_=running("implementation") or running("repair")),
        row("review-stage", rv, open_=rv_open),
        row("review-runs", run_spans.get("review", []), open_=running("review")),
        row("repair", run_spans.get("repair", []), open_=running("repair")),
    ]
    if integration is not None:
        categories.append(row("integration-wait", seconds=integration["waitIntegration"],
                              open_=integration["integratedAt"] is None
                              and card.get("stage") == "review"))
        categories.append(row("delivery-wait", seconds=integration["waitDelivery"],
                              open_=integration["integratedAt"] is not None
                              and integration["deliveredAt"] is None))

    active = _active(runs, verification, now)
    done = inputs.get("delivered") or None
    delivered = ({"at": ts(done.get("at")), "target": done.get("target"),
                  "how": done.get("how"),
                  "commit": (str(done.get("commit") or ""))[:12] or None}
                 if isinstance(done, dict) else None)
    return {
        "delivered": delivered,
        "v": VERSION, "card": card.get("file"), "stage": card.get("stage"),
        "now": now, "lead": lead, "stages": segments, "categories": categories,
        "runs": runs, "verification": verification, "integration": integration,
        "active": active, "anomalies": notes.anomalies, "unknown": notes.unknown,
        "read": {"at": now, "local": True, "writes": False},
    }


def _integration(card: dict, inputs: dict, segments: list, notes: _Notes) -> dict | None:
    """A phase member's two waits, kept apart: into the phase branch, and
    from there to the application base. None for a card in no phase."""
    phase = card.get("phase")
    if not phase:
        return None
    facts = inputs.get("integration") or {}
    integrated = facts.get("integratedAt")
    delivered = facts.get("deliveredAt")
    entered = None
    for s in segments:
        if s["stage"] == "review" and s["from"] is not None and \
                (integrated is None or s["from"] <= integrated):
            entered = s["from"]
    wait_i = wait_d = None
    if entered is not None:
        end = integrated if integrated is not None else (
            notes.now if card.get("stage") == "review" else None)
        if end is not None and end >= entered:
            wait_i = _secs(end - entered)
        elif end is not None:
            notes.anomaly("integration-order",
                          "the phase log dates the merge into the phase branch before "
                          "the card's arrival in Review (minute-precision local time)")
    if integrated is not None:
        end = delivered if delivered is not None else notes.now
        if end >= integrated:
            wait_d = _secs(end - integrated)
    elif facts.get("logRead") is False:
        notes.missing("integration", "the phase card's log could not be read")
    return {"phase": phase.get("number") or phase.get("file"),
            "phaseFile": phase.get("file"),
            "integratedAt": integrated, "integratedHow": facts.get("integratedHow"),
            "deliveredAt": delivered, "deliveredHow": facts.get("deliveredHow"),
            "precision": "minute" if integrated is not None else None,
            "waitIntegration": wait_i, "waitDelivery": wait_d,
            "note": "In the phase branch is not delivered: the card reaches the "
                    "application base only when its phase does."}


def _active(runs: list, verification: dict, now: float) -> list[dict]:
    """What is moving now, each with its elapsed time, its last report (the
    run's words) beside its last activity (what bench saw), and the action
    that is a person's to take."""
    out = []
    for e in verification["records"]:
        if not e["live"]:
            continue
        since = e["requested"]
        out.append({"kind": "check", "name": e["check"], "scope": e["scope"],
                    "phase": e["phase"], "since": since,
                    "elapsed": _secs(now - since) if since else None,
                    "evidence": e["id"], "attempt": e["attempt"],
                    "next": {"label": "Open the evidence sheet (◫)", "kind": "evidence",
                             "why": "the check's live record, and ↻ or Stop where a "
                                    "person may use them"}})
    for r in runs:
        if not r["open"]:
            continue
        rep = r["lastReport"]
        out.append({"kind": "run", "name": r["label"], "agent": r["agent"],
                    "since": r["opened"],
                    "elapsed": _secs(now - r["opened"]) if r["opened"] else None,
                    "lastReport": rep,
                    "reportAge": _secs(now - rep["at"]) if rep and rep.get("at") else None,
                    "lastActivity": r["lastActivity"],
                    "activityAge": (_secs(now - ts(r["lastActivity"]))
                                    if ts(r["lastActivity"]) else None),
                    "process": r["process"],
                    "next": {"label": "‖ hold", "kind": "hold",
                             "why": "stops this run and the checks it asked for; "
                                    "its branch and worktree are kept"}})
    return out


# ── gathering, read-only ───────────────────────────────────────────────


def _live() -> dict:
    """The running runs' liveness as the registry and their sessions see
    it: what bench observed, never what a run said."""
    out = {}
    with state.LOCK:
        for agent_id, rec in state.AGENTS.items():
            if rec.get("status") != "running":
                continue
            session = state.SESSIONS.get(rec.get("session") or "") or {}
            proc = rec.get("proc")
            alive = None
            if proc is not None and hasattr(proc, "poll"):
                try:
                    alive = "alive" if proc.poll() is None else "exited"
                except OSError:
                    alive = None
            out[agent_id] = {"lastTool": ts(session.get("last")),
                             "process": alive or "registered as running"}
    return out


def _pr_review(agent_id: str) -> bool:
    try:
        data = json.loads(profiles.manifest_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("review"))


def _phase_facts(task: dict, board: dict, snap: dict) -> dict:
    phase = task.get("phase") or {}
    phase_task = None
    for group in board["stages"]:
        for t in group["tasks"]:
            if t["file"] == phase.get("file"):
                phase_task = t
    facts: dict = {"logRead": phase_task is not None}
    if phase_task is None:
        return facts
    number = taskfiles.canonical_number(str(task.get("number") or ""))
    for stamp, text in log_lines(phase_task.get("body") or ""):
        match = MERGED_RE.match(text)
        if match and taskfiles.canonical_number(match.group(1)) == number:
            facts["integratedAt"] = log_time(stamp)
            facts["integratedHow"] = "phase log"
    done = completion.delivered_record(phase_task["file"])
    if done and ts(done.get("at")):
        facts["deliveredAt"], facts["deliveredHow"] = ts(done["at"]), "delivered record"
    elif phase_task["stage"] == "done":
        moved = next((e for e in movements.history(phase_task["file"], snap)["events"]
                      if e.get("to") == "done"), None)
        if moved and ts(moved.get("at")):
            facts["deliveredAt"], facts["deliveredHow"] = ts(moved["at"]), "phase moved to done"
    return facts


def gather(filename: str, stage: str | None = None) -> dict:
    """Every input `build` reads, for one card on this board."""
    if not isinstance(filename, str) or not filename.endswith(".md") or "/" in filename:
        raise ValueError(f"no card {filename!r} on this board")
    board = taskfiles.collect()
    task = next((t for g in board["stages"] for t in g["tasks"] if t["file"] == filename), None)
    if task is None:
        raise ValueError(f"no card {filename!r} on this board")
    snap = movements.snapshot()
    runs = []
    for agent_id, rec in list(progress.RUNS.items()):
        if rec.get("task") != filename:
            continue
        found = progress.public(agent_id) or {}
        found["prReview"] = rec.get("intent") == "review" and _pr_review(agent_id)
        runs.append(found)
    card = {"file": filename, "stage": task["stage"], "number": task.get("number"),
            "phase": task.get("phase")}
    return {
        "card": card,
        "moves": movements.history(filename, snap),
        "runs": runs,
        "evidence": evidence.for_task(filename),
        "reuses": evidence.reuses_for_task(filename),
        "integration": _phase_facts(task, board, snap) if task.get("phase") else None,
        "live": _live(),
        "delivered": completion.delivered_record(filename),
    }


def for_card(filename: str, stage: str | None = None, now: float | None = None) -> dict:
    """One card's cycle-time reading. Reads files, the registries and the
    memoised move history; launches, fetches and writes nothing."""
    return build(gather(filename, stage), time.time() if now is None else now)


